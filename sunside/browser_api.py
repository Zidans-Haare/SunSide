"""
Browser/Pyodide-friendly entry points.

The JS layer does all HTTP (Nominatim, Overpass, Open-Meteo). Python only
computes: build route from already-fetched data, then run the analysis.

This keeps the existing route_providers/ and weather.py modules untouched
(they remain usable from CLI / tests with `requests`).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from math import atan2, cos, degrees, inf, radians, sin

import gpxpy

from sunside.flight import make_great_circle_route as _make_great_circle_route
from sunside.models import RoutePoint
from sunside.sun_analysis.analyzer import analyze, analyze_converged
from sunside.sun_analysis.calculator import analyze_segment, haversine_m
from sunside.sun_analysis.sampler import auto_interval_m, resample
from sunside.weather import WeatherSample


EARTH_R_M = 6_371_000.0


# -------- helpers --------

def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_iso(dt: datetime) -> str:
    return dt.isoformat()


def _js_value(item, key: str, default=None):
    try:
        return item[key]
    except Exception:
        return getattr(item, key, default)


def _edge_dist(a: tuple[float, float], b: tuple[float, float], ref_dt: datetime) -> float:
    return haversine_m(
        RoutePoint(lat=a[0], lon=a[1], timestamp=ref_dt),
        RoutePoint(lat=b[0], lon=b[1], timestamp=ref_dt),
    )


# -------- route builders --------

def make_straight_route(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    departure_iso: str,
    travel_hours: float,
    n_waypoints: int = 30,
) -> list[RoutePoint]:
    departure = _parse_iso(departure_iso)
    return _make_great_circle_route(
        (start_lat, start_lon),
        (end_lat, end_lon),
        departure,
        travel_hours=travel_hours,
        n_waypoints=n_waypoints,
    )


def make_great_circle_route(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    departure_iso: str,
    travel_hours: float,
    n_waypoints: int = 120,
) -> list[RoutePoint]:
    departure = _parse_iso(departure_iso)
    return _make_great_circle_route(
        (start_lat, start_lon),
        (end_lat, end_lon),
        departure,
        travel_hours=travel_hours,
        n_waypoints=n_waypoints,
    )


def make_linear_route(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    departure_iso: str,
    travel_hours: float,
    n_waypoints: int = 30,
) -> list[RoutePoint]:
    departure = _parse_iso(departure_iso)
    points: list[RoutePoint] = []
    for i in range(n_waypoints + 1):
        frac = i / n_waypoints
        lat = start_lat + frac * (end_lat - start_lat)
        lon = start_lon + frac * (end_lon - start_lon)
        ts = departure + timedelta(seconds=frac * travel_hours * 3600)
        points.append(RoutePoint(lat=lat, lon=lon, timestamp=ts))
    return points


def make_polyline_route(
    coordinates: list,
    departure_iso: str,
    travel_seconds: float,
    default_speed_kmh: float = 80.0,
) -> list[RoutePoint]:
    departure = _parse_iso(departure_iso)
    coords = [(float(p[0]), float(p[1])) for p in coordinates]
    if len(coords) < 2:
        raise ValueError("Route hat zu wenige Punkte")

    seg_lens = [_edge_dist(coords[i], coords[i + 1], departure) for i in range(len(coords) - 1)]
    total_m = sum(seg_lens)
    if total_m <= 0:
        raise ValueError("Route hat keine Laenge")

    if not travel_seconds or travel_seconds <= 0:
        travel_seconds = (total_m / 1000) / default_speed_kmh * 3600

    accumulated = 0.0
    points: list[RoutePoint] = []
    for i, (lat, lon) in enumerate(coords):
        if i > 0:
            accumulated += seg_lens[i - 1]
        ts = departure + timedelta(seconds=accumulated / total_m * travel_seconds)
        points.append(RoutePoint(lat=lat, lon=lon, timestamp=ts))
    return points


def make_rail_route_from_overpass(
    overpass_json: dict,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    departure_iso: str,
    travel_hours: float | None = None,
    default_speed_kmh: float = 90.0,
) -> list[RoutePoint]:
    departure = _parse_iso(departure_iso)

    nodes: dict[int, tuple[float, float]] = {}
    ways: list[list[int]] = []
    for el in overpass_json.get("elements", []):
        if el.get("type") == "node":
            nodes[el["id"]] = (float(el["lat"]), float(el["lon"]))
        elif el.get("type") == "way":
            refs = [r for r in el.get("nodes", []) if isinstance(r, int)]
            if len(refs) >= 2:
                ways.append(refs)

    graph: dict[int, list[tuple[int, float]]] = {}
    for way in ways:
        for u, v in zip(way, way[1:]):
            if u in nodes and v in nodes:
                d = _edge_dist(nodes[u], nodes[v], departure)
                graph.setdefault(u, []).append((v, d))
                graph.setdefault(v, []).append((u, d))

    if not graph:
        raise ValueError("Keine OSM-Gleisdaten im Suchbereich gefunden")

    nodes = {nid: nodes[nid] for nid in graph}

    def nearest(target: tuple[float, float]) -> int:
        return min(nodes, key=lambda nid: _edge_dist(nodes[nid], target, departure))

    start_node = nearest((start_lat, start_lon))
    end_node = nearest((end_lat, end_lon))

    def dijkstra(source: int) -> tuple[dict[int, float], dict[int, int]]:
        q: list[tuple[float, int]] = [(0.0, source)]
        dist: dict[int, float] = {source: 0.0}
        prev: dict[int, int] = {}
        vis: set[int] = set()
        while q:
            cd, cur = heappop(q)
            if cur in vis:
                continue
            vis.add(cur)
            for nb, ed in graph.get(cur, []):
                cand = cd + ed
                if cand < dist.get(nb, inf):
                    dist[nb] = cand
                    prev[nb] = cur
                    heappush(q, (cand, nb))
        return dist, prev

    distances, previous = dijkstra(start_node)

    if end_node not in distances:
        # Disconnected network: find end's connected component via BFS,
        # enter it from the node closest to the physical start location,
        # then run Dijkstra within that component.
        end_comp: set[int] = {end_node}
        bfs: deque[int] = deque([end_node])
        while bfs:
            n = bfs.popleft()
            for nb, _ in graph.get(n, []):
                if nb not in end_comp:
                    end_comp.add(nb)
                    bfs.append(nb)

        entry_node = min(end_comp, key=lambda nid: _edge_dist(nodes[nid], (start_lat, start_lon), departure))
        dist2, prev2 = dijkstra(entry_node)

        if end_node not in dist2:
            raise ValueError(
                "Start und Ziel liegen in getrennten OSM-Gleisnetzen — "
                "bitte 'Flugzeug' oder 'Auto/Bus' als Modus waehlen."
            )

        rail_path = [end_node]
        while rail_path[-1] != entry_node:
            rail_path.append(prev2[rail_path[-1]])
        rail_path.reverse()

        # Straight-line approach from physical start to entry point, then rail geometry.
        coords = [(start_lat, start_lon)] + [nodes[nid] for nid in rail_path]
    else:
        path = [end_node]
        while path[-1] != start_node:
            path.append(previous[path[-1]])
        path.reverse()
        coords = [nodes[nid] for nid in path]
    seg_lens = [_edge_dist(coords[i], coords[i + 1], departure) for i in range(len(coords) - 1)]
    total_m = sum(seg_lens)
    if total_m <= 0:
        raise ValueError("OSM-Gleisroute hat keine Laenge")

    travel_seconds = travel_hours * 3600 if travel_hours is not None else (total_m / 1000) / default_speed_kmh * 3600

    accumulated = 0.0
    points: list[RoutePoint] = []
    for i, (lat, lon) in enumerate(coords):
        if i > 0:
            accumulated += seg_lens[i - 1]
        ts = departure + timedelta(seconds=accumulated / total_m * travel_seconds)
        points.append(RoutePoint(lat=lat, lon=lon, timestamp=ts))
    return points


def make_gpx_route(gpx_text: str, departure_iso: str, default_speed_kmh: float = 80.0) -> list[RoutePoint]:
    departure = _parse_iso(departure_iso)
    gpx = gpxpy.parse(gpx_text)

    raw: list[tuple[float, float]] = []
    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                raw.append((p.latitude, p.longitude))
    for route in gpx.routes:
        for p in route.points:
            raw.append((p.latitude, p.longitude))

    if not raw:
        raise ValueError("Keine Trackpunkte in GPX gefunden")

    seg_lens = [_edge_dist(raw[i], raw[i + 1], departure) for i in range(len(raw) - 1)]
    total_m = sum(seg_lens) or 1.0
    travel_seconds = (total_m / 1000) / default_speed_kmh * 3600

    accumulated = 0.0
    points: list[RoutePoint] = []
    for i, (lat, lon) in enumerate(raw):
        if i > 0:
            accumulated += seg_lens[i - 1]
        ts = departure + timedelta(seconds=accumulated / total_m * travel_seconds)
        points.append(RoutePoint(lat=lat, lon=lon, timestamp=ts))
    return points


# -------- weather (data injected from JS) --------

class _StaticWeatherProvider:
    """Lookup-only weather provider. Samples come from JS (Open-Meteo)."""

    def __init__(self, samples: list[dict]):
        # samples: list of {"lat": float, "lon": float, "hour_iso": "YYYY-MM-DDTHH:00", "cloud_cover_pct": float}
        self._samples = samples
        self.warning: str | None = None

    def get_weather(self, point: RoutePoint) -> WeatherSample:
        if not self._samples:
            return WeatherSample()
        ts_utc = point.timestamp.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        target = ts_utc.isoformat().replace("+00:00", "")

        def score(s: dict) -> float:
            # Match by hour first, then by spatial closeness
            hour_iso = str(_js_value(s, "hour_iso", ""))
            lat = float(_js_value(s, "lat", 0.0))
            lon = float(_js_value(s, "lon", 0.0))
            hour_match = 0 if hour_iso.startswith(target[:13]) else 1
            d = (lat - point.lat) ** 2 + (lon - point.lon) ** 2
            return hour_match * 1e6 + d

        best = min(self._samples, key=score)
        return WeatherSample(cloud_cover_pct=float(_js_value(best, "cloud_cover_pct", 0.0)))


class _StaticTerrainProvider:
    """Terrain horizon lookup backed by elevation samples fetched in JS."""

    def __init__(
        self,
        samples: list[dict],
        *,
        max_distance_m: float = 8_000.0,
        sample_step_m: float = 500.0,
        elevation_margin_deg: float = 0.5,
    ):
        self.max_distance_m = max_distance_m
        self.sample_step_m = sample_step_m
        self.elevation_margin_deg = elevation_margin_deg
        self.warning: str | None = None
        self._cache: dict[tuple[float, float], float] = {}

        for sample in samples or []:
            lat = _js_value(sample, "lat")
            lon = _js_value(sample, "lon")
            elevation = _js_value(sample, "elevation")
            if lat is None or lon is None or elevation is None:
                continue
            self._cache[(round(float(lat), 4), round(float(lon), 4))] = float(elevation)

        if not self._cache:
            self.warning = "Keine Hoehendaten erhalten - Gelaendeschatten ignoriert."

    def _lookup(self, lat: float, lon: float) -> float | None:
        return self._cache.get((round(lat, 4), round(lon, 4)))

    def is_shaded(self, lat: float, lon: float, sun_azimuth_deg: float, sun_elevation_deg: float) -> bool:
        if sun_elevation_deg <= 0 or not self._cache:
            return False

        anchor = self._lookup(lat, lon)
        if anchor is None:
            return False

        for sample_lat, sample_lon, distance_m in _terrain_ray_samples(
            lat, lon, sun_azimuth_deg, self.max_distance_m, self.sample_step_m,
        ):
            elevation = self._lookup(sample_lat, sample_lon)
            if elevation is None or distance_m <= 0:
                continue
            horizon_angle = degrees(atan2(elevation - anchor, distance_m))
            if horizon_angle > sun_elevation_deg + self.elevation_margin_deg:
                return True
        return False


def _terrain_ray_samples(
    lat: float,
    lon: float,
    azimuth_deg: float,
    max_distance_m: float = 8_000.0,
    sample_step_m: float = 500.0,
) -> list[tuple[float, float, float]]:
    n = int(max_distance_m // sample_step_m)
    if n <= 0:
        return []

    samples = []
    azimuth = radians(azimuth_deg)
    lat1 = radians(lat)
    lon1 = radians(lon)
    for i in range(1, n + 1):
        distance = i * sample_step_m
        d_over_r = distance / EARTH_R_M
        lat2 = lat1 + d_over_r * cos(azimuth)
        lon2 = lon1 + d_over_r * sin(azimuth) / max(cos(lat1), 1e-9)
        samples.append((degrees(lat2), degrees(lon2), distance))
    return samples


def _analysis_intervals(
    points: list[RoutePoint],
    interval_m: int | None = None,
    *,
    converged: bool = False,
    min_interval_m: int = 20,
    max_segments: int = 50_000,
    max_total_segments: int = 80_000,
) -> list[int]:
    if not converged:
        return [int(interval_m) if interval_m else auto_interval_m(points)]

    route_length_m = _route_length_m(points)
    intervals = []
    total = 0
    for candidate in [2_000, 1_000, 500, 200, 100, 50, 20, 10, 5]:
        if candidate < min_interval_m:
            break
        segs = route_length_m / candidate
        if segs > max_segments:
            continue  # skip too-fine intervals, don't abort the whole loop
        if total + segs > max_total_segments:
            break  # total budget exhausted
        intervals.append(candidate)
        total += segs
    return intervals or [auto_interval_m(points)]


def _route_length_m(points: list[RoutePoint]) -> float:
    total = 0.0
    for i in range(len(points) - 1):
        total += haversine_m(points[i], points[i + 1])
    return total


# -------- public entry point --------

def _segments_to_dicts(segments) -> list[dict]:
    out = []
    for s in segments:
        out.append({
            "lat": s.point.lat,
            "lon": s.point.lon,
            "time": _to_iso(s.point.timestamp),
            "bearing": s.bearing,
            "sun_azimuth": s.sun_azimuth,
            "sun_elevation": s.sun_elevation,
            "sun_side": s.sun_side,
            "intensity_factor": s.intensity_factor,
            "cloud_cover_pct": s.cloud_cover_pct,
            "sun_factor": s.sun_factor,
            "terrain_shaded": s.terrain_shaded,
        })
    return out


def elevation_points_for_route(
    points: list[RoutePoint],
    interval_m: int | None = None,
    converged: bool = False,
    max_distance_m: float = 8_000.0,
    sample_step_m: float = 500.0,
) -> list[dict]:
    """Return unique coordinates the JS layer must fetch from Open-Meteo Elevation."""
    seen: set[tuple[float, float]] = set()
    out: list[dict] = []

    def add(lat: float, lon: float):
        key = (round(lat, 4), round(lon, 4))
        if key in seen:
            return
        seen.add(key)
        out.append({"lat": key[0], "lon": key[1]})

    for used_interval in _analysis_intervals(points, interval_m, converged=converged):
        sampled = resample(points, used_interval)
        for i in range(len(sampled) - 1):
            segment = analyze_segment(sampled[i], sampled[i + 1])
            if segment.sun_side not in ("links", "rechts") or segment.sun_elevation <= 0:
                continue
            add(segment.point.lat, segment.point.lon)
            for lat, lon, _distance in _terrain_ray_samples(
                segment.point.lat,
                segment.point.lon,
                segment.sun_azimuth,
                max_distance_m,
                sample_step_m,
            ):
                add(lat, lon)
    return out


def run_analysis(
    points: list[RoutePoint],
    interval_m: int | None = None,
    weather_samples: list[dict] | None = None,
    use_intensity: bool = False,
    terrain_samples: list[dict] | None = None,
    converged: bool = False,
) -> dict:
    weather_provider = _StaticWeatherProvider(weather_samples) if weather_samples is not None else None
    terrain_provider = _StaticTerrainProvider(terrain_samples) if terrain_samples is not None else None
    interval = int(interval_m) if interval_m else None

    if converged:
        rec, trace = analyze_converged(
            points,
            weather_provider=weather_provider,
            use_intensity=use_intensity,
            terrain_provider=terrain_provider,
        )
    else:
        rec = analyze(
            points,
            interval_m=interval,
            weather_provider=weather_provider,
            use_intensity=use_intensity,
            terrain_provider=terrain_provider,
        )
        trace = []

    result = {
        "shade_side": rec.shade_side,
        "sun_side": rec.sun_side,
        "shade_pct": rec.shade_pct,
        "sun_pct": rec.sun_pct,
        "auto_interval_m": rec.auto_interval_m,
        "is_night": rec.is_night,
        "weather_adjusted": rec.weather_adjusted,
        "intensity_adjusted": rec.intensity_adjusted,
        "mean_cloud_cover_pct": rec.mean_cloud_cover_pct,
        "mean_sun_elevation": rec.mean_sun_elevation,
        "tunnel_pct": rec.tunnel_pct,
        "terrain_pct": rec.terrain_pct,
        "terrain_adjusted": rec.terrain_adjusted,
        "low_direct_sun": rec.low_direct_sun,
        "segments": _segments_to_dicts(rec.segments),
        "polyline": [[p.lat, p.lon] for p in points],
        "convergence_trace": trace,
        "terrain_warning": terrain_provider.warning if terrain_provider else None,
    }
    return result


def hour_samples_for_route(points: list[RoutePoint]) -> list[dict]:
    """Return one (lat, lon, hour_iso) per hour along the route. JS uses this to fetch weather."""
    if not points:
        return []
    samples: list[dict] = []
    seen: set[str] = set()
    for p in points:
        ts_utc = p.timestamp.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        key = f"{round(p.lat, 1)},{round(p.lon, 1)},{ts_utc.isoformat()}"
        if key in seen:
            continue
        seen.add(key)
        samples.append({
            "lat": round(p.lat, 1),
            "lon": round(p.lon, 1),
            "hour_iso": ts_utc.isoformat().replace("+00:00", ""),
        })
    return samples
