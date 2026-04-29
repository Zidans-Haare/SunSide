from math import sqrt
from datetime import datetime, timedelta

from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import bearing, haversine_m


def _interpolate(p1: RoutePoint, p2: RoutePoint, fraction: float) -> RoutePoint:
    """Linear interpolation between two RoutePoints (lat/lon + time).

    A point is considered ``in_tunnel`` if either neighbour is in a tunnel
    (conservative: a sample falling between a tunnel and an open-air node is
    treated as still inside the tunnel).
    """
    lat = p1.lat + fraction * (p2.lat - p1.lat)
    lon = p1.lon + fraction * (p2.lon - p1.lon)
    dt1 = p1.timestamp.timestamp()
    dt2 = p2.timestamp.timestamp()
    ts = datetime.fromtimestamp(dt1 + fraction * (dt2 - dt1), tz=p1.timestamp.tzinfo)
    return RoutePoint(
        lat=lat,
        lon=lon,
        timestamp=ts,
        in_tunnel=p1.in_tunnel or p2.in_tunnel,
    )


def resample(points: list[RoutePoint], interval_m: float) -> list[RoutePoint]:
    """
    Resample a route to equally-spaced points every interval_m metres.
    Timestamps are linearly interpolated between original points.
    """
    if len(points) < 2:
        return points

    result = [points[0]]
    carry = 0.0  # metres carried over from previous segment

    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        seg_len = haversine_m(p1, p2)
        if seg_len == 0:
            continue

        offset = interval_m - carry
        while offset <= seg_len:
            frac = offset / seg_len
            result.append(_interpolate(p1, p2, frac))
            offset += interval_m

        carry = seg_len - (offset - interval_m)

    if haversine_m(result[-1], points[-1]) > 0.01:
        result.append(points[-1])
    return result


def _bearing_variance(points: list[RoutePoint]) -> float:
    """
    Compute the standard deviation of bearing changes between consecutive segments.
    High value = curvy route; low value = straight route.
    """
    if len(points) < 3:
        return 0.0

    deltas = []
    for i in range(len(points) - 2):
        b1 = bearing(points[i], points[i + 1])
        b2 = bearing(points[i + 1], points[i + 2])
        delta = abs((b2 - b1 + 180) % 360 - 180)  # shortest angular difference
        deltas.append(delta)

    mean = sum(deltas) / len(deltas)
    variance = sum((d - mean) ** 2 for d in deltas) / len(deltas)
    return sqrt(variance)


def auto_interval_m(points: list[RoutePoint]) -> int:
    """
    Suggest a sampling interval based on route curvature.

    Bearing stddev thresholds (empirically chosen):
      < 3°  → very straight (e.g. Norddeutsche Tiefebene, NRW Autobahn)  → 20 km
      < 8°  → mostly straight (most Fernstrecken)                         →  5 km
      < 18° → moderately curvy (Mittelgebirge, Rheintal)                  →  1 km
      >= 18° → curvy (Alpen, Tirol, Schwarzwald)                          → 300 m
    """
    stddev = _bearing_variance(points)

    if stddev < 3:
        return 20_000
    elif stddev < 8:
        return 5_000
    elif stddev < 18:
        return 1_000
    else:
        return 300
