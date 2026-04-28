"""
Simple provider: geocode start + end via Nominatim, straight line between them.
Good enough for very straight routes; replaced by OSM/GTFS for curvy ones.
This is the fallback when no better geometry is available.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import unicodedata

import requests

from sunside.models import RoutePoint
from sunside.route_providers.base import RouteProvider
from sunside.sun_analysis.calculator import haversine_m


@dataclass(frozen=True)
class PlaceSearchResult:
    name: str
    display_name: str
    lat: float
    lon: float
    category: str
    place_type: str
    importance: float = 0.0

    @property
    def label(self) -> str:
        if self.name and self.name != self.display_name:
            return f"{self.name} — {self.display_name}"
        return self.display_name


def search_places(query: str, *, station_only: bool = False, limit: int = 8) -> list[PlaceSearchResult]:
    if len(query.strip()) < 3:
        return []

    results: list[PlaceSearchResult] = []
    seen: set[tuple[str, float, float]] = set()

    for search_query in _search_queries(query, station_only):
        for result in _fetch_places(search_query, station_only=station_only, limit=limit):
            if station_only and not _matches_query_area(query, result):
                continue
            key = (result.display_name, round(result.lat, 5), round(result.lon, 5))
            if key in seen:
                continue
            seen.add(key)
            results.append(result)

        if len(results) >= limit and station_only:
            break

    if station_only:
        results.sort(key=lambda result: _station_score(query, result), reverse=True)

    return results[:limit]


def _fetch_places(query: str, *, station_only: bool, limit: int) -> list[PlaceSearchResult]:
    request_limit = max(limit, 30) if station_only else limit
    url = "https://nominatim.openstreetmap.org/search"
    resp = requests.get(
        url,
        params={
            "q": query,
            "format": "jsonv2",
            "limit": request_limit,
            "addressdetails": 1,
            "namedetails": 1,
        },
        headers={"User-Agent": "SunSide/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    raw_results = resp.json()

    results = []
    for item in raw_results:
        category = item.get("category") or item.get("class") or ""
        place_type = item.get("type") or ""
        if station_only and not _looks_like_station(category, place_type, item):
            continue

        namedetails = item.get("namedetails") or {}
        name = namedetails.get("name") or item.get("name") or item.get("display_name") or query
        display_name = item.get("display_name") or name
        results.append(
            PlaceSearchResult(
                name=name,
                display_name=display_name,
                lat=float(item["lat"]),
                lon=float(item["lon"]),
                category=category,
                place_type=place_type,
                importance=float(item.get("importance") or 0.0),
            )
        )

    return results


def geocode_place(place: str) -> tuple[float, float]:
    results = search_places(place, limit=1)
    if not results:
        raise ValueError(f"Ort nicht gefunden: {place}")
    return results[0].lat, results[0].lon


def _looks_like_station(category: str, place_type: str, item: dict) -> bool:
    if category == "railway":
        return place_type in {"station", "halt", "tram_stop", "subway_entrance"}
    if category == "public_transport":
        return place_type in {"station", "stop_position", "platform"}

    display_name = (item.get("display_name") or "").lower()
    return any(token in display_name for token in ["bahnhof", "hauptbahnhof", "station"])


def _search_queries(query: str, station_only: bool) -> list[str]:
    query = query.strip()
    if not station_only:
        return [query]

    normalized = _normalize_text(query)
    has_station_word = any(
        word in normalized for word in ["bahnhof", "hauptbahnhof", "hbf", "station"]
    )
    if has_station_word:
        return [query]

    return [query, f"{query} Hauptbahnhof", f"{query} Bahnhof", f"{query} station"]


def _matches_query_area(query: str, result: PlaceSearchResult) -> bool:
    tokens = _significant_query_tokens(query)
    if not tokens:
        return True
    haystack = _normalize_text(f"{result.name} {result.display_name}")
    return any(token in haystack for token in tokens)


def _station_score(query: str, result: PlaceSearchResult) -> float:
    haystack = _normalize_text(f"{result.name} {result.display_name}")
    tokens = _significant_query_tokens(query)
    score = result.importance * 10

    if all(token in haystack for token in tokens):
        score += 80
    if "hauptbahnhof" in haystack or " hbf" in haystack:
        score += 40
    if "deutschland" in haystack or "osterreich" in haystack or "schweiz" in haystack:
        score += 20
    if result.category == "railway":
        score += 15
    if result.place_type == "station":
        score += 10

    return score


def _significant_query_tokens(query: str) -> list[str]:
    ignored = {"bahnhof", "hauptbahnhof", "hbf", "station", "train", "railway"}
    return [
        token
        for token in _normalize_text(query).split()
        if len(token) >= 3 and token not in ignored
    ]


def _normalize_text(value: str) -> str:
    value = value.lower().replace("ß", "ss")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.replace("—", " ").replace(",", " ").split())


_geocode = geocode_place


class NominatimProvider(RouteProvider):
    """Straight-line route between geocoded start and end. No real track geometry."""

    def __init__(self, *, default_speed_kmh: float = 800.0):
        self.default_speed_kmh = default_speed_kmh

    @property
    def name(self) -> str:
        return "Luftlinie (Nominatim)"

    def get_route(
        self,
        origin: str,
        destination: str,
        departure: datetime,
        travel_hours: float | None = None,
        n_waypoints: int = 20,
    ) -> list[RoutePoint]:
        start = _geocode(origin)
        end = _geocode(destination)
        return self.get_route_between_coordinates(
            start,
            end,
            departure,
            travel_hours=travel_hours,
            n_waypoints=n_waypoints,
        )

    def get_route_between_coordinates(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        departure: datetime,
        travel_hours: float | None = None,
        n_waypoints: int = 20,
    ) -> list[RoutePoint]:
        lat1, lon1 = start
        lat2, lon2 = end
        if travel_hours is None:
            distance_m = haversine_m(
                RoutePoint(lat=lat1, lon=lon1, timestamp=departure),
                RoutePoint(lat=lat2, lon=lon2, timestamp=departure),
            )
            travel_seconds = (distance_m / 1000) / self.default_speed_kmh * 3600
        else:
            travel_seconds = travel_hours * 3600

        points = []
        for i in range(n_waypoints + 1):
            frac = i / n_waypoints
            lat = lat1 + frac * (lat2 - lat1)
            lon = lon1 + frac * (lon2 - lon1)
            ts = departure + timedelta(seconds=frac * travel_seconds)
            points.append(RoutePoint(lat=lat, lon=lon, timestamp=ts))
        return points
