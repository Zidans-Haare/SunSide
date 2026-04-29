from sunside.models import RoutePoint, Recommendation
from sunside.sun_analysis.calculator import analyze_segment
from sunside.sun_analysis.sampler import resample, auto_interval_m


def analyze(
    points: list[RoutePoint],
    interval_m: int | None = None,
    weather_provider=None,
    use_intensity: bool = False,
    terrain_provider=None,
) -> Recommendation:
    """
    Full analysis pipeline:
      1. Auto-detect sampling interval if not provided
      2. Resample route to that interval
      3. Analyse each segment
      4. Aggregate into a Recommendation

    If ``use_intensity`` is True, segment weights are scaled by sin(elevation),
    so a high midday sun contributes more than a low evening sun. If a weather
    provider is also given, both factors multiply. ``terrain_provider`` (a
    :class:`sunside.terrain.TerrainProvider`) zeroes out the sun_factor for
    segments where the sun is blocked by nearby terrain.
    """
    if len(points) < 2:
        raise ValueError("Need at least 2 route points")

    suggested = auto_interval_m(points)
    used_interval = interval_m if interval_m is not None else suggested

    resampled = resample(points, used_interval)

    segments = []
    for i in range(len(resampled) - 1):
        seg = analyze_segment(resampled[i], resampled[i + 1])
        if weather_provider is not None and seg.sun_side not in ("night", "tunnel"):
            weather = weather_provider.get_weather(seg.point)
            seg.cloud_cover_pct = weather.cloud_cover_pct
            seg.sun_factor = weather.sun_factor
        elif seg.sun_side in ("night", "tunnel"):
            seg.sun_factor = 0.0
        if (
            terrain_provider is not None
            and seg.sun_side in ("links", "rechts")
            and terrain_provider.is_shaded(
                seg.point.lat, seg.point.lon, seg.sun_azimuth, seg.sun_elevation,
            )
        ):
            seg.terrain_shaded = True
            seg.sun_factor = 0.0
        segments.append(seg)

    tunnel_count = sum(1 for s in segments if s.sun_side == "tunnel")
    tunnel_pct = (tunnel_count / len(segments) * 100) if segments else 0.0
    terrain_count = sum(1 for s in segments if s.terrain_shaded)
    terrain_pct = (terrain_count / len(segments) * 100) if segments else 0.0
    day_segments = [s for s in segments if s.sun_side in ("links", "rechts") and not s.terrain_shaded]

    if not day_segments:
        return Recommendation(
            shade_side="links",
            sun_side="rechts",
            shade_pct=0.0,
            sun_pct=0.0,
            segments=segments,
            auto_interval_m=used_interval,
            is_night=True,
            weather_adjusted=weather_provider is not None,
            intensity_adjusted=use_intensity,
            mean_cloud_cover_pct=_mean_cloud_cover(segments),
            mean_sun_elevation=_mean_sun_elevation(segments),
            tunnel_pct=tunnel_pct,
            terrain_pct=terrain_pct,
            terrain_adjusted=terrain_provider is not None,
        )

    left_score = sum(
        _segment_weight(s, weather_provider, use_intensity)
        for s in day_segments if s.sun_side == "links"
    )
    right_score = sum(
        _segment_weight(s, weather_provider, use_intensity)
        for s in day_segments if s.sun_side == "rechts"
    )
    total_score = left_score + right_score

    if total_score <= 0.05:
        left_count = sum(1 for s in day_segments if s.sun_side == "links")
        right_count = len(day_segments) - left_count
        left_pct = left_count / len(day_segments) * 100
        right_pct = right_count / len(day_segments) * 100
        low_direct_sun = weather_provider is not None or use_intensity
    else:
        left_pct = left_score / total_score * 100
        right_pct = right_score / total_score * 100
        low_direct_sun = (
            (weather_provider is not None or use_intensity)
            and total_score / len(day_segments) < 0.2
        )

    # sun_side = side the sun is on more; shade_side = opposite
    if left_pct >= right_pct:
        sun_side = "links"
        shade_side = "rechts"
        sun_pct = left_pct
        shade_pct = left_pct
    else:
        sun_side = "rechts"
        shade_side = "links"
        sun_pct = right_pct
        shade_pct = right_pct

    return Recommendation(
        shade_side=shade_side,
        sun_side=sun_side,
        shade_pct=shade_pct,
        sun_pct=sun_pct,
        segments=segments,
        auto_interval_m=used_interval,
        is_night=False,
        weather_adjusted=weather_provider is not None,
        intensity_adjusted=use_intensity,
        mean_cloud_cover_pct=_mean_cloud_cover(segments),
        mean_sun_elevation=_mean_sun_elevation(segments),
        tunnel_pct=tunnel_pct,
        terrain_pct=terrain_pct,
        terrain_adjusted=terrain_provider is not None,
        low_direct_sun=low_direct_sun,
    )


def _segment_weight(segment, weather_provider, use_intensity: bool = False) -> float:
    weight = 1.0
    if use_intensity:
        weight *= segment.intensity_factor
    if weather_provider is not None:
        weight *= segment.sun_factor
    return weight


def _mean_cloud_cover(segments) -> float | None:
    values = [s.cloud_cover_pct for s in segments if s.cloud_cover_pct is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _mean_sun_elevation(segments) -> float | None:
    values = [s.sun_elevation for s in segments if s.sun_side in ("links", "rechts")]
    if not values:
        return None
    return sum(values) / len(values)


def analyze_converged(
    points: list[RoutePoint],
    *,
    tolerance_pct: float = 0.5,
    min_interval_m: int = 20,
    max_segments: int = 50_000,
    weather_provider=None,
    use_intensity: bool = False,
    terrain_provider=None,
) -> tuple[Recommendation, list[dict]]:
    """Steroid mode: keep halving the sampling interval until the result converges.

    Starts at 2 km (coarser intervals are unreliable on curvy routes - they can
    appear "converged" simply because they miss every bend). Stops when the
    dominant-side percentage moves by less than ``tolerance_pct`` between two
    *successive* halvings, OR when ``min_interval_m`` is reached, OR when the
    next iteration would exceed ``max_segments``.

    Returns the final Recommendation and a trace of every iteration
    (interval, segment count, dominant side, percentages).
    """
    if len(points) < 2:
        raise ValueError("Need at least 2 route points")

    intervals = [2_000, 1_000, 500, 200, 100, 50, 20, 10, 5]

    trace: list[dict] = []
    last_pct: float | None = None
    last_side: str | None = None
    stable_count = 0
    final: Recommendation | None = None
    route_length_m = _route_length_m(points)

    for interval_m in intervals:
        if interval_m < min_interval_m:
            break

        approx_segments = route_length_m / interval_m
        if approx_segments > max_segments:
            break

        rec = analyze(points, interval_m=interval_m,
                      weather_provider=weather_provider,
                      use_intensity=use_intensity,
                      terrain_provider=terrain_provider)
        trace.append({
            "interval_m": interval_m,
            "segments": len(rec.segments),
            "sun_side": rec.sun_side,
            "sun_pct": rec.sun_pct,
            "shade_side": rec.shade_side,
            "shade_pct": rec.shade_pct,
            "is_night": rec.is_night,
        })
        final = rec

        if rec.is_night:
            break

        if last_side == rec.sun_side and last_pct is not None \
                and abs(rec.sun_pct - last_pct) <= tolerance_pct:
            stable_count += 1
            # Two consecutive halvings within tolerance → converged.
            if stable_count >= 2:
                break
        else:
            stable_count = 0

        last_pct = rec.sun_pct
        last_side = rec.sun_side

    if final is None:
        final = analyze(points, weather_provider=weather_provider,
                        use_intensity=use_intensity, terrain_provider=terrain_provider)
        trace.append({
            "interval_m": final.auto_interval_m,
            "segments": len(final.segments),
            "sun_side": final.sun_side,
            "sun_pct": final.sun_pct,
            "shade_side": final.shade_side,
            "shade_pct": final.shade_pct,
            "is_night": final.is_night,
        })
    return final, trace


def _route_length_m(points: list[RoutePoint]) -> float:
    from sunside.sun_analysis.calculator import haversine_m
    total = 0.0
    for i in range(len(points) - 1):
        total += haversine_m(points[i], points[i + 1])
    return total
