"""Great-circle route helpers for flight-style routes.

This models the shortest path on a sphere between two coordinates. It is a
better approximation for flights than linearly interpolating latitude and
longitude, especially on long east-west routes.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from math import atan2, cos, degrees, radians, sin, sqrt

from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import haversine_m


def great_circle_point(
    start: tuple[float, float],
    end: tuple[float, float],
    fraction: float,
) -> tuple[float, float]:
    """Return the coordinate at ``fraction`` along the great-circle path."""
    lat1, lon1 = (radians(start[0]), radians(start[1]))
    lat2, lon2 = (radians(end[0]), radians(end[1]))

    x1, y1, z1 = _to_cartesian(lat1, lon1)
    x2, y2, z2 = _to_cartesian(lat2, lon2)

    omega = atan2(
        sqrt((y1 * z2 - z1 * y2) ** 2 + (z1 * x2 - x1 * z2) ** 2 + (x1 * y2 - y1 * x2) ** 2),
        x1 * x2 + y1 * y2 + z1 * z2,
    )
    if abs(omega) < 1e-12:
        return start

    sin_omega = sin(omega)
    a = sin((1.0 - fraction) * omega) / sin_omega
    b = sin(fraction * omega) / sin_omega
    x = a * x1 + b * x2
    y = a * y1 + b * y2
    z = a * z1 + b * z2

    lat = atan2(z, sqrt(x * x + y * y))
    lon = atan2(y, x)
    return degrees(lat), _normalize_lon(degrees(lon))


def make_great_circle_route(
    start: tuple[float, float],
    end: tuple[float, float],
    departure: datetime,
    *,
    travel_hours: float | None = None,
    default_speed_kmh: float = 800.0,
    n_waypoints: int | None = None,
) -> list[RoutePoint]:
    """Build timestamped RoutePoints along a great-circle route."""
    if travel_hours is None:
        distance_m = haversine_m(
            RoutePoint(lat=start[0], lon=start[1], timestamp=departure),
            RoutePoint(lat=end[0], lon=end[1], timestamp=departure),
        )
        travel_seconds = (distance_m / 1000) / default_speed_kmh * 3600
    else:
        travel_seconds = travel_hours * 3600

    if n_waypoints is None:
        distance_m = haversine_m(
            RoutePoint(lat=start[0], lon=start[1], timestamp=departure),
            RoutePoint(lat=end[0], lon=end[1], timestamp=departure),
        )
        n_waypoints = max(30, min(240, int(distance_m // 100_000)))
    n_waypoints = max(1, int(n_waypoints))

    points: list[RoutePoint] = []
    for index in range(n_waypoints + 1):
        fraction = index / n_waypoints
        lat, lon = great_circle_point(start, end, fraction)
        timestamp = departure + timedelta(seconds=fraction * travel_seconds)
        points.append(RoutePoint(lat=lat, lon=lon, timestamp=timestamp))
    return points


def _to_cartesian(lat_rad: float, lon_rad: float) -> tuple[float, float, float]:
    return (
        cos(lat_rad) * cos(lon_rad),
        cos(lat_rad) * sin(lon_rad),
        sin(lat_rad),
    )


def _normalize_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0
