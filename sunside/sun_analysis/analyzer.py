from sunside.models import RoutePoint, Recommendation
from sunside.sun_analysis.calculator import analyze_segment
from sunside.sun_analysis.sampler import resample, auto_interval_m


def analyze(
    points: list[RoutePoint],
    interval_m: int | None = None,
    weather_provider=None,
) -> Recommendation:
    """
    Full analysis pipeline:
      1. Auto-detect sampling interval if not provided
      2. Resample route to that interval
      3. Analyse each segment
      4. Aggregate into a Recommendation
    """
    if len(points) < 2:
        raise ValueError("Need at least 2 route points")

    suggested = auto_interval_m(points)
    used_interval = interval_m if interval_m is not None else suggested

    resampled = resample(points, used_interval)

    segments = []
    for i in range(len(resampled) - 1):
        seg = analyze_segment(resampled[i], resampled[i + 1])
        if weather_provider is not None:
            weather = weather_provider.get_weather(seg.point)
            seg.cloud_cover_pct = weather.cloud_cover_pct
            seg.sun_factor = 0.0 if seg.sun_side == "night" else weather.sun_factor
        segments.append(seg)

    day_segments = [s for s in segments if s.sun_side != "night"]

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
            mean_cloud_cover_pct=_mean_cloud_cover(segments),
        )

    left_score = sum(_segment_weight(s, weather_provider) for s in day_segments if s.sun_side == "links")
    right_score = sum(_segment_weight(s, weather_provider) for s in day_segments if s.sun_side == "rechts")
    total_score = left_score + right_score

    if total_score <= 0.05:
        left_count = sum(1 for s in day_segments if s.sun_side == "links")
        right_count = len(day_segments) - left_count
        left_pct = left_count / len(day_segments) * 100
        right_pct = right_count / len(day_segments) * 100
        low_direct_sun = weather_provider is not None
    else:
        left_pct = left_score / total_score * 100
        right_pct = right_score / total_score * 100
        low_direct_sun = weather_provider is not None and total_score / len(day_segments) < 0.2

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
        mean_cloud_cover_pct=_mean_cloud_cover(segments),
        low_direct_sun=low_direct_sun,
    )


def _segment_weight(segment, weather_provider) -> float:
    if weather_provider is None:
        return 1.0
    return segment.sun_factor


def _mean_cloud_cover(segments) -> float | None:
    values = [s.cloud_cover_pct for s in segments if s.cloud_cover_pct is not None]
    if not values:
        return None
    return sum(values) / len(values)
