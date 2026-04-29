"""OSRM road provider for car and direct bus-style routes."""
from __future__ import annotations

from datetime import datetime, timedelta

import requests

from sunside.http_cache import cached_request
from sunside.models import RoutePoint
from sunside.route_providers.base import RouteProvider
from sunside.route_providers.nominatim import geocode_place
from sunside.sun_analysis.calculator import haversine_m


OSRM_ROUTE_URL = "https://router.project-osrm.org/route/v1/driving/{start};{end}"


def _distance_m(a: tuple[float, float], b: tuple[float, float], timestamp: datetime) -> float:
    return haversine_m(
        RoutePoint(lat=a[0], lon=a[1], timestamp=timestamp),
        RoutePoint(lat=b[0], lon=b[1], timestamp=timestamp),
    )


class OsrmRoadProvider(RouteProvider):
    """Road geometry from the public OSRM demo server."""

    @property
    def name(self) -> str:
        return "OSRM Strassenroute"

    def get_route(
        self,
        origin: str,
        destination: str,
        departure: datetime,
        travel_hours: float | None = None,
    ) -> list[RoutePoint]:
        start = geocode_place(origin)
        end = geocode_place(destination)
        return self.get_route_between_coordinates(start, end, departure, travel_hours=travel_hours)

    def get_route_between_coordinates(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        departure: datetime,
        travel_hours: float | None = None,
    ) -> list[RoutePoint]:
        url = OSRM_ROUTE_URL.format(
            start=f"{start[1]},{start[0]}",
            end=f"{end[1]},{end[0]}",
        )
        response = cached_request(
            "GET",
            url,
            params={"overview": "full", "geometries": "geojson"},
            headers={"User-Agent": "SunSide/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        routes = payload.get("routes") or []
        if not routes:
            raise ValueError("Keine Strassenroute gefunden")

        route = routes[0]
        coordinates = [
            (float(lat), float(lon))
            for lon, lat in route.get("geometry", {}).get("coordinates", [])
        ]
        if len(coordinates) < 2:
            raise ValueError("OSRM lieferte keine nutzbare Routengeometrie")

        if travel_hours is None:
            travel_seconds = float(route.get("duration") or 0.0)
        else:
            travel_seconds = travel_hours * 3600
        if travel_seconds <= 0:
            travel_seconds = _estimated_duration_seconds(coordinates, departure, speed_kmh=80.0)

        return _coordinates_to_route_points(coordinates, departure, travel_seconds)


def _coordinates_to_route_points(
    coordinates: list[tuple[float, float]],
    departure: datetime,
    travel_seconds: float,
) -> list[RoutePoint]:
    segment_lengths = [
        _distance_m(coordinates[index], coordinates[index + 1], departure)
        for index in range(len(coordinates) - 1)
    ]
    total_m = sum(segment_lengths)
    if total_m <= 0:
        raise ValueError("Route hat keine Laenge")

    points: list[RoutePoint] = []
    accumulated_m = 0.0
    for index, (lat, lon) in enumerate(coordinates):
        if index > 0:
            accumulated_m += segment_lengths[index - 1]
        fraction = accumulated_m / total_m
        points.append(
            RoutePoint(
                lat=lat,
                lon=lon,
                timestamp=departure + timedelta(seconds=fraction * travel_seconds),
            )
        )
    return points


def _estimated_duration_seconds(
    coordinates: list[tuple[float, float]],
    departure: datetime,
    speed_kmh: float,
) -> float:
    total_m = sum(
        _distance_m(coordinates[index], coordinates[index + 1], departure)
        for index in range(len(coordinates) - 1)
    )
    return (total_m / 1000) / speed_kmh * 3600
