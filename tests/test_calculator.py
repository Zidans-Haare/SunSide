from datetime import datetime, timezone, timedelta
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
    assert seg.intensity_factor == 0.0


# ---------------------------------------------------------------------------
# Regression: timezone-naive timestamps were interpreted as UTC by astral,
# causing "Nacht" to be reported 2 hours too early in CEST (UTC+2) — the
# exact bug reported by the user for the trip Spital → Villach at 18:30 CEST.
# ---------------------------------------------------------------------------

def test_cest_1830_is_daytime_not_night():
    """
    18:30 local time (CEST = UTC+2) in Kärnten on 22 May is full afternoon sun
    (~20° elevation).  If the timestamp is naive and astral treats it as UTC,
    the computed time would be 18:30 UTC = 20:30 CEST — near sunset → "night".
    With the correct tz-aware timestamp the result must be "links" or "rechts".
    """
    # 18:30 CEST = 16:30 UTC → sun at ~20° in Kärnten
    cest = timezone(timedelta(hours=2))
    ts = datetime(2026, 5, 22, 18, 30, 0, tzinfo=cest)

    p1 = RoutePoint(lat=46.8, lon=13.5, timestamp=ts)           # Spital a.d. Drau area
    p2 = RoutePoint(lat=46.6, lon=13.85, timestamp=ts + timedelta(minutes=30))  # Villach

    seg = analyze_segment(p1, p2)
    assert seg.sun_elevation > 10, (
        f"Expected sun well above horizon at 18:30 CEST in Kärnten, "
        f"got elevation={seg.sun_elevation:.1f}°. "
        "Timezone-naive datetime was probably passed to astral (UTC confusion)."
    )
    assert seg.sun_side in ("links", "rechts"), (
        f"Expected daytime side, got '{seg.sun_side}' — app wrongly reported night."
    )


def test_naive_datetime_bug_demo():
    """
    Documents the *wrong* behaviour that caused the original bug report:
    a naive 18:30 is treated by astral as UTC → sun barely above/below horizon.
    This test shows the difference is > 15° — large enough to flip day→night.
    """
    naive_ts = datetime(2026, 5, 22, 18, 30, 0)          # no tzinfo → astral reads as UTC
    cest = timezone(timedelta(hours=2))
    aware_ts = datetime(2026, 5, 22, 18, 30, 0, tzinfo=cest)  # correct local time

    p1_naive = RoutePoint(lat=46.8, lon=13.5, timestamp=naive_ts)
    p2_naive = RoutePoint(lat=46.6, lon=13.85, timestamp=naive_ts)
    p1_aware = RoutePoint(lat=46.8, lon=13.5, timestamp=aware_ts)
    p2_aware = RoutePoint(lat=46.6, lon=13.85, timestamp=aware_ts)

    seg_naive = analyze_segment(p1_naive, p2_naive)
    seg_aware = analyze_segment(p1_aware, p2_aware)

    # Naive reads UTC → sun near/below horizon; aware reads CEST → sun high
    assert seg_aware.sun_elevation - seg_naive.sun_elevation > 15, (
        "Expected at least 15° difference between naive-UTC and CEST-aware datetimes "
        f"at 18:30 in Kärnten. Got naive={seg_naive.sun_elevation:.1f}°, "
        f"aware={seg_aware.sun_elevation:.1f}°"
    )
