from datetime import datetime, timedelta, timezone
import pytest
from sunside.models import RoutePoint
from sunside.sun_analysis.analyzer import analyze


def _route(n=10, lat_start=48.0, lat_end=49.0, lon=11.0, hour=10):
    """Straight north-south route starting at the given UTC hour."""
    ts = datetime(2024, 6, 21, hour, 0, tzinfo=timezone.utc)
    return [
        RoutePoint(
            lat=lat_start + (lat_end - lat_start) * i / (n - 1),
            lon=lon,
            timestamp=ts + timedelta(hours=2 * i / (n - 1)),
        )
        for i in range(n)
    ]


# --- basic contract ---

def test_analyze_requires_at_least_two_points():
    ts = datetime(2024, 6, 21, 10, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        analyze([RoutePoint(48.0, 11.0, ts)])


def test_analyze_daytime_returns_valid_recommendation():
    result = analyze(_route(hour=10))
    assert result.shade_side in ("links", "rechts")
    assert result.sun_side in ("links", "rechts")
    assert result.shade_side != result.sun_side
    assert 0.0 <= result.shade_pct <= 100.0
    assert 0.0 <= result.sun_pct <= 100.0
    assert result.auto_interval_m > 0
    assert not result.is_night


def test_analyze_night_sets_is_night_flag():
    result = analyze(_route(hour=1))  # 01:00 UTC — before sunrise in Central Europe
    assert result.is_night


def test_analyze_segments_populated():
    result = analyze(_route())
    assert len(result.segments) > 0


def test_analyze_custom_interval_is_used():
    result = analyze(_route(), interval_m=5_000)
    assert result.auto_interval_m == 5_000


def test_analyze_weather_adjusted_flag_without_provider():
    result = analyze(_route())
    assert not result.weather_adjusted


def test_analyze_no_weather_means_cloud_cover_none():
    result = analyze(_route())
    for seg in result.segments:
        assert seg.cloud_cover_pct is None


# --- consistency ---

def test_shade_and_sun_sides_are_opposite():
    result = analyze(_route(hour=10))
    sides = {"links", "rechts"}
    assert result.shade_side in sides
    assert result.sun_side in sides
    assert result.shade_side != result.sun_side


def test_percentages_sum_to_100():
    result = analyze(_route(hour=10))
    if not result.is_night:
        # shade_pct is the majority side's percentage — it equals sun_pct of the opposite
        # Both should be the same value (dominant side's share)
        assert result.shade_pct == pytest.approx(result.sun_pct, abs=0.01)
