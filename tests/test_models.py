from datetime import datetime, timezone
from sunside.models import RoutePoint, SegmentAnalysis, Recommendation


def _ts():
    return datetime(2024, 6, 21, 10, 0, tzinfo=timezone.utc)


def test_route_point_fields():
    p = RoutePoint(lat=48.0, lon=11.0, timestamp=_ts())
    assert p.lat == 48.0
    assert p.lon == 11.0
    assert p.timestamp == _ts()


def test_segment_analysis_defaults():
    p = RoutePoint(lat=48.0, lon=11.0, timestamp=_ts())
    seg = SegmentAnalysis(
        point=p, bearing=90.0, sun_azimuth=180.0, sun_elevation=45.0, sun_side="links"
    )
    assert seg.cloud_cover_pct is None
    assert seg.sun_factor == 1.0


def test_recommendation_defaults():
    r = Recommendation(shade_side="links", sun_side="rechts", shade_pct=60.0, sun_pct=60.0)
    assert r.segments == []
    assert r.auto_interval_m == 0
    assert not r.is_night
    assert not r.weather_adjusted
    assert r.mean_cloud_cover_pct is None
    assert not r.low_direct_sun
