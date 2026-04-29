"""Tests for the terrain shading provider (no real network calls)."""
from __future__ import annotations

from unittest.mock import patch

from sunside.terrain import TerrainProvider, TerrainConfig


def _fake_response(elevations: list[float | None]):
    import requests
    import json
    body = json.dumps({"elevation": elevations}).encode("utf-8")
    r = requests.Response()
    r.status_code = 200
    r._content = body
    r.headers["Content-Type"] = "application/json"
    return r


def test_terrain_disabled_never_shades():
    tp = TerrainProvider(TerrainConfig(enabled=False))
    assert tp.is_shaded(47.0, 13.0, 90.0, 30.0) is False


def test_terrain_low_sun_blocked_by_high_horizon():
    # Anchor at 500 m, mountain at 2500 m, 5 km away -> horizon angle ~21°.
    # Sun elevation 10° -> shaded.
    cfg = TerrainConfig(enabled=True, max_distance_m=5_000.0,
                        sample_step_m=5_000.0, elevation_margin_deg=0.5)
    tp = TerrainProvider(cfg)
    with patch("sunside.terrain.cached_request") as mock:
        # First call: anchor elevation
        # Second call: ray sample elevation
        mock.side_effect = [_fake_response([500.0]), _fake_response([2500.0])]
        assert tp.is_shaded(47.0, 13.0, sun_azimuth_deg=180.0, sun_elevation_deg=10.0) is True


def test_terrain_high_sun_clears_horizon():
    cfg = TerrainConfig(enabled=True, max_distance_m=5_000.0, sample_step_m=5_000.0)
    tp = TerrainProvider(cfg)
    with patch("sunside.terrain.cached_request") as mock:
        mock.side_effect = [_fake_response([500.0]), _fake_response([800.0])]
        # Mountain only 300m higher at 5km -> horizon ~3.4°. Sun at 30° passes.
        assert tp.is_shaded(47.0, 13.0, sun_azimuth_deg=180.0, sun_elevation_deg=30.0) is False


def test_terrain_below_horizon_skipped():
    cfg = TerrainConfig(enabled=True)
    tp = TerrainProvider(cfg)
    # Sun below horizon -> always False without any HTTP call
    with patch("sunside.terrain.cached_request") as mock:
        assert tp.is_shaded(47.0, 13.0, sun_azimuth_deg=180.0, sun_elevation_deg=-1.0) is False
        mock.assert_not_called()


def test_terrain_api_failure_warns_and_returns_false():
    import requests
    cfg = TerrainConfig(enabled=True, max_distance_m=2_000.0, sample_step_m=2_000.0)
    tp = TerrainProvider(cfg)
    with patch("sunside.terrain.cached_request",
               side_effect=requests.ConnectionError("offline")):
        assert tp.is_shaded(47.0, 13.0, sun_azimuth_deg=180.0, sun_elevation_deg=10.0) is False
        assert tp.warning is not None
