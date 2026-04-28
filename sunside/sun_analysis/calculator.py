from math import radians, degrees, sin, cos, atan2, sqrt, asin
from datetime import datetime

from astral import Observer
from astral.sun import azimuth as astral_azimuth, elevation as astral_elevation

from sunside.models import RoutePoint, SegmentAnalysis


def bearing(p1: RoutePoint, p2: RoutePoint) -> float:
    """Compass bearing from p1 to p2, degrees 0–360 (0=North, clockwise)."""
    lat1, lon1 = radians(p1.lat), radians(p1.lon)
    lat2, lon2 = radians(p2.lat), radians(p2.lon)
    dlon = lon2 - lon1
    x = sin(dlon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    return (degrees(atan2(x, y)) + 360) % 360


def haversine_m(p1: RoutePoint, p2: RoutePoint) -> float:
    """Distance between two points in metres."""
    R = 6_371_000
    lat1, lon1 = radians(p1.lat), radians(p1.lon)
    lat2, lon2 = radians(p2.lat), radians(p2.lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def sun_position(point: RoutePoint) -> tuple[float, float]:
    """Returns (azimuth_deg, elevation_deg) for the sun at this point and time."""
    obs = Observer(latitude=point.lat, longitude=point.lon)
    az = astral_azimuth(obs, point.timestamp)
    el = astral_elevation(obs, point.timestamp)
    return az, el


def analyze_segment(p1: RoutePoint, p2: RoutePoint) -> SegmentAnalysis:
    """
    Determine sun side for the segment from p1 to p2.
    Sun position is calculated at p1 (the start of the segment).
    Returns a SegmentAnalysis attached to p1.
    """
    brng = bearing(p1, p2)
    sun_az, sun_el = sun_position(p1)

    if sun_el < 0:
        side = "night"
    else:
        # delta: angle of sun relative to travel direction
        # 0–180 = sun is to the right, 180–360 = sun is to the left
        delta = (sun_az - brng) % 360
        side = "rechts" if delta < 180 else "links"

    return SegmentAnalysis(
        point=p1,
        bearing=round(brng, 1),
        sun_azimuth=round(sun_az, 1),
        sun_elevation=round(sun_el, 1),
        sun_side=side,
    )
