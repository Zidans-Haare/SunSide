"""SunSide GTFS API server.

Small read-only FastAPI service that exposes the locally imported GTFS
SQLite databases (Flixbus, DB, OeBB, ...) as JSON. The browser PWA fetches
this for the "Fahrplan (GTFS)" mode; the Streamlit app uses GtfsDatabase
directly and does NOT need this server.

Run locally:
    uvicorn server:app --host 0.0.0.0 --port 8001

Deploy:
    See deploy/server-setup.md (Caddy reverse proxy + systemd unit).
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from sunside.route_providers.gtfs_db import GtfsDatabase

GTFS_DIR = Path("data/gtfs")

app = FastAPI(title="SunSide GTFS API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_DB_CACHE: dict[str, GtfsDatabase] = {}


def _list_feeds() -> list[str]:
    if not GTFS_DIR.exists():
        return []
    return sorted(p.stem for p in GTFS_DIR.glob("*.sqlite") if p.is_file())


def _open(feed: str) -> GtfsDatabase:
    if feed not in _list_feeds():
        raise HTTPException(404, f"Unknown feed: {feed}")
    if feed not in _DB_CACHE:
        _DB_CACHE[feed] = GtfsDatabase(GTFS_DIR / f"{feed}.sqlite")
    return _DB_CACHE[feed]


@app.get("/api/health")
def health():
    return {"status": "ok", "feeds": _list_feeds()}


@app.get("/api/gtfs/feeds")
def feeds():
    out = []
    for name in _list_feeds():
        info = _open(name).feed_info()
        out.append({
            "name": name,
            "publisher": info.get("publisher_name"),
            "start_date": info.get("start_date"),
            "end_date": info.get("end_date"),
            "imported_at": info.get("imported_at_utc"),
        })
    return {"feeds": out}


@app.get("/api/gtfs/{feed}/stops")
def stops(feed: str, q: str = Query(min_length=2), limit: int = 20):
    db = _open(feed)
    return {
        "stops": [
            {
                "stop_id": s.stop_id,
                "name": s.name,
                "lat": s.lat,
                "lon": s.lon,
                "timezone": s.timezone,
            }
            for s in db.search_stops(q, limit=limit)
        ]
    }


@app.get("/api/gtfs/{feed}/trips")
def trips(feed: str, board: str, alight: str, date_: str = Query(alias="date"),
          limit: int = 30):
    db = _open(feed)
    try:
        target = date.fromisoformat(date_)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    return {
        "trips": [
            {
                "trip_id": t.trip_id,
                "route_id": t.route_id,
                "route_short_name": t.route_short_name,
                "route_long_name": t.route_long_name,
                "headsign": t.headsign,
                "board_stop_id": t.board_stop_id,
                "board_stop_name": t.board_stop_name,
                "alight_stop_id": t.alight_stop_id,
                "alight_stop_name": t.alight_stop_name,
                "board_departure": t.board_departure.isoformat(),
                "alight_arrival": t.alight_arrival.isoformat(),
                "duration_minutes": t.duration_minutes,
                "service_date": t.service_date.isoformat(),
                "agency_timezone": t.agency_timezone,
                "label": t.label,
            }
            for t in db.find_trips(
                board_stop_id=board,
                alight_stop_id=alight,
                date_=target,
                limit=limit,
            )
        ]
    }


@app.get("/api/gtfs/{feed}/route")
def route(feed: str, trip_id: str, board: str, alight: str,
          service_date: str):
    db = _open(feed)
    try:
        sd = date.fromisoformat(service_date)
    except ValueError:
        raise HTTPException(400, "service_date must be YYYY-MM-DD")
    points = db.build_route(
        trip_id=trip_id,
        board_stop_id=board,
        alight_stop_id=alight,
        service_date=sd,
    )
    return {
        "points": [
            {"lat": p.lat, "lon": p.lon,
             "timestamp": p.timestamp.isoformat(),
             "in_tunnel": p.in_tunnel}
            for p in points
        ]
    }
