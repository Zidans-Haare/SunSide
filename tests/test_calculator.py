from datetime import datetime, timezone
import pytest
from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import bearing, haversine_m, analyze_segment


def _pt(lat, lon, hour=10):
    ts = datetime(2024, 6, 21, hour, 0, 0, tzinfo=timezone.utc)
    return RoutePoint(lat=lat, lon=lon, timestamp=ts)


# --- bearing ---

def test_bearing_north():
    assert abs(bearing(_pt(48.0, 11.0), _pt(49.0, 11.0))) < 0.1


def test_bearing_south():
    assert abs(bearing(_pt(49.0, 11.0), _pt(48.0, 11.0)) - 180.0) < 0.1


def test_bearing_east():
    assert abs(bearing(_pt(48.0, 11.0), _pt(48.0, 12.0)) - 90.0) < 0.5


def test_bearing_west():
    assert abs(bearing(_pt(48.0, 12.0), _pt(48.0, 11.0)) - 270.0) < 0.5


def test_bearing_range():
    b = bearing(_pt(48.0, 11.0), _pt(48.5, 11.5))
    assert 0.0 <= b < 360.0


# --- haversine_m ---

def test_haversine_zero():
    p = _pt(48.0, 11.0)
    assert haversine_m(p, p) == 0.0


def test_haversine_one_degree_latitude():
    # 1° latitude ≈ 111 km
    d = haversine_m(_pt(48.0, 11.0), _pt(49.0, 11.0))
    assert 110_000 < d < 112_000


def test_haversine_symmetry():
    p1 = _pt(48.0, 11.0)
    p2 = _pt(49.0, 12.0)
    assert abs(haversine_m(p1, p2) - haversine_m(p2, p1)) < 0.001


# --- analyze_segment ---

def test_analyze_segment_night():
    # 00:00 UTC — sun is below horizon in central Europe
    p1 = _pt(48.1, 11.6, hour=0)
    p2 = _pt(48.2, 11.6, hour=0)
    seg = analyze_segment(p1, p2)
    assert seg.sun_side == "night"
    assert seg.sun_elevation < 0


def test_analyze_segment_daytime_returns_valid_side():
    # 10:00 UTC = 12:00 CEST, summer solstice, Munich area — sun is high
    p1 = _pt(48.1, 11.6, hour=10)
    p2 = _pt(48.2, 11.6, hour=10)
    seg = analyze_segment(p1, p2)
    assert seg.sun_side in ("links", "rechts")
    assert seg.sun_elevation > 0


def test_analyze_segment_bearing_matches_route():
    # heading north → bearing ≈ 0
    p1 = _pt(48.0, 11.0, hour=10)
    p2 = _pt(48.1, 11.0, hour=10)
    seg = analyze_segment(p1, p2)
    assert abs(seg.bearing) < 1.0 or abs(seg.bearing - 360) < 1.0


def test_analyze_segment_point_is_p1():
    p1 = _pt(48.0, 11.0, hour=10)
    p2 = _pt(48.1, 11.0, hour=10)
    seg = analyze_segment(p1, p2)
    assert seg.point is p1


def test_analyze_segment_tunnel_overrides_sun():
    # Daytime in Munich, but flagged as tunnel: must report "tunnel"
    p1 = RoutePoint(lat=48.0, lon=11.0,
                    timestamp=datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc),
                    in_tunnel=True)
    p2 = RoutePoint(lat=48.1, lon=11.0,
                    timestamp=datetime(2024, 6, 21, 12, 5, tzinfo=timezone.utc),
                    in_tunnel=True)
    seg = analyze_segment(p1, p2)
    assert seg.sun_side == "tunnel"
    assert seg.intensity_factor == 0.0


def test_analyze_segment_tunnel_one_endpoint():
    # If either endpoint is in a tunnel, the segment counts as tunnel
    p1 = RoutePoint(lat=48.0, lon=11.0,
                    timestamp=datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc),
                    in_tunnel=False)
    p2 = RoutePoint(lat=48.1, lon=11.0,
                    timestamp=datetime(2024, 6, 21, 12, 5, tzinfo=timezone.utc),
                    in_tunnel=True)
    seg = analyze_segment(p1, p2)
    assert seg.sun_side == "tunnel"
