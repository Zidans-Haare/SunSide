from datetime import datetime, timedelta, timezone
import pytest
from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import haversine_m
from sunside.sun_analysis.sampler import resample, auto_interval_m


def _ts(offset_min=0):
    return datetime(2024, 6, 21, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_min)


def _straight_route(n=20, lat_start=48.0, lat_end=49.0, lon=11.0):
    """Straight north-south route."""
    return [
        RoutePoint(
            lat=lat_start + (lat_end - lat_start) * i / (n - 1),
            lon=lon,
            timestamp=_ts(i * 5),
        )
        for i in range(n)
    ]


def _irregular_curvy_route():
    """Route with highly irregular turn angles → high bearing-stddev → fine interval."""
    import math
    # Varying turn angles so bearing deltas have high variance
    turns = [0, 90, 0, -90, 45, 135, 0, -45, 90, 0, -90, 45, 135, 0, 90, -90, 45, 0, -45, 90]
    points = []
    lat, lon = 48.0, 11.0
    heading = 0.0
    for i, turn in enumerate(turns):
        heading = (heading + turn) % 360
        lat += 0.05 * math.cos(math.radians(heading))
        lon += 0.05 * math.sin(math.radians(heading))
        points.append(RoutePoint(lat=lat, lon=lon, timestamp=_ts(i * 5)))
    return points


# --- resample ---

def test_resample_single_point_passthrough():
    route = [RoutePoint(48.0, 11.0, _ts())]
    assert resample(route, 1000) == route


def test_resample_two_points_passthrough():
    route = [RoutePoint(48.0, 11.0, _ts()), RoutePoint(48.1, 11.0, _ts(10))]
    result = resample(route, 200_000)  # interval > total length → keep both endpoints
    assert len(result) == 2


def test_resample_produces_more_points_for_small_interval():
    route = _straight_route(5)
    result = resample(route, 5_000)  # 5 km intervals on a ~111 km route → ~22+ points
    assert len(result) > 5


def test_resample_first_and_last_preserved():
    route = _straight_route()
    result = resample(route, 10_000)
    assert result[0].lat == route[0].lat
    assert result[-1].lat == pytest.approx(route[-1].lat, abs=0.001)


def test_resample_spacing_is_approximately_correct():
    route = _straight_route(n=10, lat_start=48.0, lat_end=58.0)  # ~1100 km
    interval_m = 50_000  # 50 km
    result = resample(route, interval_m)
    # All consecutive pairs should be within 10% of the target interval
    for i in range(len(result) - 2):  # skip last segment (may be shorter)
        d = haversine_m(result[i], result[i + 1])
        assert d == pytest.approx(interval_m, rel=0.1), f"segment {i}: {d:.0f} m"


# --- auto_interval_m ---

def test_auto_interval_straight_route_is_large():
    route = _straight_route(n=30)
    interval = auto_interval_m(route)
    assert interval >= 5_000  # straight route → coarse sampling


def test_auto_interval_curvy_route_is_small():
    route = _irregular_curvy_route()
    interval = auto_interval_m(route)
    assert interval <= 1_000  # irregular turns → high bearing-stddev → fine sampling


def test_auto_interval_two_points_returns_default():
    route = [RoutePoint(48.0, 11.0, _ts()), RoutePoint(49.0, 11.0, _ts(60))]
    interval = auto_interval_m(route)
    assert interval > 0


def test_auto_interval_returns_valid_tier():
    route = _straight_route()
    interval = auto_interval_m(route)
    assert interval in (300, 1_000, 5_000, 20_000)
