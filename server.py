"""SunSide API server.

Exposes two sets of endpoints:
  /api/v1/recommend        — B2B recommendation API (all modes, providers, batch)
  /api/v1/health           — Health + feed status
  /api/v1/capabilities     — Supported modes, providers, options
  /api/gtfs/...            — Raw GTFS data for the browser PWA

Run locally:
    uvicorn server:app --host 0.0.0.0 --port 8001

Deploy:
    See deploy/server-setup.md (Caddy reverse proxy + systemd unit).

Environment variables:
    SUNSIDE_API_KEYS          Comma-separated API keys (empty = auth disabled)
    SUNSIDE_RATE_LIMIT        Requests/min per key for /recommend (default: 60)
    SUNSIDE_BATCH_RATE_LIMIT  Requests/min per key for /recommend/batch (default: 10)
    SUNSIDE_MAX_BATCH         Max items per batch request (default: 50)
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from sunside.api_v1 import router as api_v1_router
from sunside.route_providers.gtfs_db import GtfsDatabase

GTFS_DIR = Path("data/gtfs")
_GTFS_RATE = 60  # requests per minute per IP

_gtfs_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _gtfs_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _gtfs_rate_buckets[ip]
    cutoff = now - 60.0
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= _GTFS_RATE:
        raise HTTPException(429, "Rate limit exceeded. Please slow down.")
    bucket.append(now)

app = FastAPI(
    title="SunSide API",
    version="1.0",
    description=(
        "## Which side of the train, bus, or plane should you sit on?\n\n"
        "SunSide computes it — using **real OSM track geometry**, "
        "**GTFS schedules** (DB, ÖBB, Flixbus), and astronomical sun calculations.\n\n"
        "### Quick start\n"
        "```\nPOST /api/v1/recommend\n"
        '{"mode":"rail","origin":"Wien Hbf","destination":"Salzburg Hbf",'
        '"departure":"2026-05-15T09:14:00+02:00"}\n```\n\n"'
        "### Authentication\n"
        "Pass your API key as `X-Api-Key` header. "
        "Contact [sun.olomek.com](https://sun.olomek.com) for a key.\n\n"
        "### Route providers (auto-selected by default)\n"
        "| Mode | Default provider | Accuracy |\n"
        "|---|---|---|\n"
        "| `rail` | OSM Overpass real track geometry | High — curves included |\n"
        "| `bus` / `car` | OSRM road routing | High |\n"
        "| `flight` | Geodesic great-circle | Good |\n"
        "| Any | GTFS feed (if specified) | Highest — real schedule |\n"
    ),
    contact={"name": "SunSide", "url": "https://sun.olomek.com"},
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.include_router(api_v1_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
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
def feeds(request: Request):
    _gtfs_rate_limit(request)
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
def stops(request: Request, feed: str, q: str = Query(min_length=2), limit: int = 20):
    _gtfs_rate_limit(request)
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
def trips(request: Request, feed: str, board: str, alight: str,
          date_: str = Query(alias="date"), limit: int = 30):
    _gtfs_rate_limit(request)
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
def route(request: Request, feed: str, trip_id: str, board: str, alight: str,
          service_date: str):
    _gtfs_rate_limit(request)
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
