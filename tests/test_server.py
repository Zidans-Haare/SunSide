"""Smoke tests for the GTFS API server."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

flixbus_db = Path("data/gtfs/flixbus.sqlite")
pytestmark = pytest.mark.skipif(
    not flixbus_db.exists(),
    reason="No GTFS database available - run scripts/gtfs_import.py first",
)


def _client():
    fastapi = pytest.importorskip("fastapi")  # noqa: F841
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from server import app
    return TestClient(app)


def test_health_lists_feeds():
    c = _client()
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["feeds"], list)


def test_feeds_metadata():
    c = _client()
    r = c.get("/api/gtfs/feeds")
    assert r.status_code == 200
    feeds = r.json()["feeds"]
    assert any(f["name"] == "flixbus" for f in feeds)


def test_stops_search_requires_min_length():
    c = _client()
    assert c.get("/api/gtfs/flixbus/stops", params={"q": "a"}).status_code == 422


def test_stops_search_returns_results():
    c = _client()
    r = c.get("/api/gtfs/flixbus/stops", params={"q": "Villach"})
    assert r.status_code == 200
    stops = r.json()["stops"]
    assert len(stops) >= 1
    assert all({"stop_id", "name", "lat", "lon"} <= s.keys() for s in stops)


def test_unknown_feed_404():
    c = _client()
    r = c.get("/api/gtfs/doesnotexist/stops", params={"q": "Wien"})
    assert r.status_code == 404


def test_invalid_date():
    c = _client()
    r = c.get("/api/gtfs/flixbus/trips",
              params={"board": "x", "alight": "y", "date": "not-a-date"})
    assert r.status_code == 400
