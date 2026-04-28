"""
Browser/Pyodide-friendly entry points.

The JS layer does all HTTP (Nominatim, Overpass, Open-Meteo). Python only
computes: build route from already-fetched data, then run the analysis.

This keeps the existing route_providers/ and weather.py modules untouched
(they remain usable from CLI / tests with `requests`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from math import inf

import gpxpy

from sunside.models import RoutePoint
from sunside.sun_analysis.analyzer import analyze
from sunside.sun_analysis.calculator import haversine_m
from sunside.weather import WeatherSample


# -------- helpers --------

def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_iso(dt: datetime) -> str:
    return dt.isoformat()


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

    queue: list[tuple[float, int]] = [(0.0, start_node)]
    distances = {start_node: 0.0}
    previous: dict[int, int] = {}
    visited: set[int] = set()
    while queue:
        cd, cur = heappop(queue)
        if cur in visited:
            continue
        visited.add(cur)
        if cur == end_node:
            break
        for nb, ed in graph.get(cur, []):
            cand = cd + ed
            if cand < distances.get(nb, inf):
                distances[nb] = cand
                previous[nb] = cur
                heappush(queue, (cand, nb))

    if end_node not in distances:
        raise ValueError("Start und Ziel liegen in getrennten OSM-Gleisnetzen")

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
            hour_match = 0 if s["hour_iso"].startswith(target[:13]) else 1
            d = (s["lat"] - point.lat) ** 2 + (s["lon"] - point.lon) ** 2
            return hour_match * 1e6 + d

        best = min(self._samples, key=score)
        return WeatherSample(cloud_cover_pct=float(best["cloud_cover_pct"]))


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
            "cloud_cover_pct": s.cloud_cover_pct,
            "sun_factor": s.sun_factor,
        })
    return out


def run_analysis(
    points: list[RoutePoint],
    interval_m: int | None = None,
    weather_samples: list[dict] | None = None,
) -> dict:
    weather_provider = _StaticWeatherProvider(weather_samples) if weather_samples else None
    rec = analyze(points, interval_m=interval_m, weather_provider=weather_provider)
    return {
        "shade_side": rec.shade_side,
        "sun_side": rec.sun_side,
        "shade_pct": rec.shade_pct,
        "sun_pct": rec.sun_pct,
        "auto_interval_m": rec.auto_interval_m,
        "is_night": rec.is_night,
        "weather_adjusted": rec.weather_adjusted,
        "mean_cloud_cover_pct": rec.mean_cloud_cover_pct,
        "low_direct_sun": rec.low_direct_sun,
        "segments": _segments_to_dicts(rec.segments),
        "polyline": [[p.lat, p.lon] for p in points],
    }


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
