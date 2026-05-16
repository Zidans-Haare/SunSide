"""
SunSide B2B REST API — v1

Single endpoint: POST /api/v1/recommend
Batch endpoint:  POST /api/v1/recommend/batch

Supports all transport modes (rail, bus, flight, car, custom) and all
route providers (GTFS, OSM Overpass, OSRM, great-circle, polyline, GPX).

Authentication:
  Set SUNSIDE_API_KEYS env var to a comma-separated list of valid keys.
  Pass the key as the X-Api-Key request header.
  If SUNSIDE_API_KEYS is empty/unset, auth is disabled (dev mode).

Rate limiting:
  SUNSIDE_RATE_LIMIT requests per minute per key (default: 60).
  SUNSIDE_BATCH_RATE_LIMIT for batch endpoint (default: 10).
"""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Header, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, model_validator
from typing import Annotated

from sunside.flight import make_great_circle_route
from sunside.models import RoutePoint
from sunside.route_providers.gtfs_db import GtfsDatabase
from sunside.route_providers.nominatim import geocode_place
from sunside.route_providers.osm import OsmRailProvider
from sunside.route_providers.osrm import OsrmRoadProvider
from sunside.sun_analysis.analyzer import analyze, analyze_converged
from sunside.sun_analysis.calculator import haversine_m
from sunside.terrain import TerrainConfig, TerrainProvider
from sunside.weather import OpenMeteoWeatherProvider


router = APIRouter(prefix="/api/v1", tags=["SunSide API v1"])

_API_VERSION = "1.0"
_GTFS_DIR = Path("data/gtfs")
_MAX_BATCH = int(os.getenv("SUNSIDE_MAX_BATCH", "50"))
_RATE_LIMIT = int(os.getenv("SUNSIDE_RATE_LIMIT", "60"))
_BATCH_RATE_LIMIT = int(os.getenv("SUNSIDE_BATCH_RATE_LIMIT", "10"))

_API_KEYS: set[str] = {
    k.strip() for k in os.getenv("SUNSIDE_API_KEYS", "").split(",") if k.strip()
}

_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
_gtfs_cache: dict[str, GtfsDatabase] = {}


# ---------------------------------------------------------------------------
# Auth + rate-limiting
# ---------------------------------------------------------------------------

_api_key_scheme = APIKeyHeader(name="X-Api-Key", auto_error=False,
                               description="API key — contact sun.olomek.com to get one.")

def _auth(x_api_key: str | None = Depends(_api_key_scheme)) -> str:
    if not _API_KEYS:
        return "anonymous"
    if not x_api_key or x_api_key not in _API_KEYS:
        raise HTTPException(401, "Invalid or missing X-Api-Key header.")
    return x_api_key


def _rate_limit(limit: int, api_key: str) -> None:
    now = time.monotonic()
    bucket = _rate_buckets[api_key]
    cutoff = now - 60.0
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(429, f"Rate limit exceeded: {limit} requests/minute per key.")
    bucket.append(now)


def _auth_single(api_key: str = Depends(_auth)) -> str:
    _rate_limit(_RATE_LIMIT, api_key)
    return api_key


def _auth_batch(api_key: str = Depends(_auth)) -> str:
    _rate_limit(_BATCH_RATE_LIMIT, api_key)
    return api_key


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TransportMode(str, Enum):
    rail = "rail"
    bus = "bus"
    flight = "flight"
    car = "car"
    custom = "custom"


class ProviderHint(str, Enum):
    auto = "auto"
    gtfs = "gtfs"
    osm = "osm"
    osrm = "osrm"
    great_circle = "great_circle"
    polyline = "polyline"


class AnalysisMode(str, Enum):
    auto = "auto"
    fast = "fast"
    precise = "precise"
    converged = "converged"


class Language(str, Enum):
    de = "de"
    en = "en"


class SideLabel(str, Enum):
    left = "left"
    right = "right"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    night = "night"
    unclear = "unclear"


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------

class Coords(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Latitude in decimal degrees")
    lon: float = Field(..., ge=-180, le=180, description="Longitude in decimal degrees")


class GtfsQuery(BaseModel):
    feed: str = Field(..., description="GTFS feed name, e.g. 'oebb', 'db', 'flixbus'")
    trip_id: str | None = Field(None, description="Exact GTFS trip_id — skip trip search if provided")
    board_stop_id: str | None = Field(None, description="Boarding stop_id from the feed")
    alight_stop_id: str | None = Field(None, description="Alighting stop_id from the feed")
    service_date: date_cls | None = Field(None, description="GTFS service-anchor date (YYYY-MM-DD)")


class AnalysisOptions(BaseModel):
    mode: AnalysisMode = Field(
        AnalysisMode.auto,
        description=(
            "auto=smart interval via bearing-variance, "
            "fast=5 km intervals, "
            "precise=200 m, "
            "converged=iterate until stable (most accurate, slowest)"
        ),
    )
    interval_m: int | None = Field(
        None,
        gt=5,
        le=100_000,
        description="Explicit sampling interval in metres — overrides mode",
    )
    use_intensity: bool = Field(
        False,
        description="Weight segments by sin(sun elevation); midday sun counts more than low-angle evening sun",
    )
    converge_tolerance_pct: float = Field(
        0.5,
        gt=0,
        le=5.0,
        description="Convergence threshold for mode=converged",
    )
    max_segments: int = Field(
        50_000,
        gt=0,
        description="Safety cap on total segments for mode=converged",
    )


class EnvironmentOptions(BaseModel):
    weather: bool = Field(False, description="Fetch hourly cloud cover from Open-Meteo and weight sun exposure")
    terrain: bool = Field(False, description="Fetch elevation via Open-Meteo and detect terrain shading")
    terrain_max_distance_m: float = Field(8_000.0, gt=0, description="How far along the sun ray to look for terrain")
    terrain_sample_step_m: float = Field(500.0, gt=0, description="Step size along the terrain ray")
    terrain_elevation_margin_deg: float = Field(0.5, ge=0, description="Extra degrees before marking as shaded")


class ResponseOptions(BaseModel):
    include_segments: bool = Field(False, description="Include per-segment sun analysis in response")
    include_polyline: bool = Field(False, description="Include route geometry as [[lat, lon], ...]")
    include_convergence_trace: bool = Field(False, description="Include convergence trace (mode=converged only)")
    language: Language = Field(Language.de, description="Language for the human-readable recommendation label")


class RecommendRequest(BaseModel):
    mode: TransportMode = Field(..., description="Transport mode")

    # Route input — exactly one strategy must be resolvable
    origin: str | None = Field(None, description="Place name, geocoded via Nominatim (e.g. 'Wien Hbf')")
    destination: str | None = Field(None, description="Place name, geocoded via Nominatim")
    origin_coords: Coords | None = Field(None, description="Exact coordinates for origin")
    destination_coords: Coords | None = Field(None, description="Exact coordinates for destination")
    gtfs: GtfsQuery | None = Field(None, description="GTFS-based route (most accurate for scheduled services)")
    polyline: list[list[float]] | None = Field(
        None,
        description="Custom route as [[lat, lon], ...] — at least 2 points required",
    )

    # Timing
    departure: datetime | None = Field(
        None,
        description="ISO 8601 departure time with timezone offset (e.g. 2026-05-15T09:14:00+02:00)",
    )
    travel_minutes: float | None = Field(
        None,
        gt=0,
        description="Override total travel duration in minutes",
    )

    # Options
    provider: ProviderHint = Field(
        ProviderHint.auto,
        description=(
            "auto=smart selection based on mode, "
            "gtfs=GTFS feed, "
            "osm=OSM Overpass rail geometry, "
            "osrm=OSRM road routing, "
            "great_circle=geodesic approximation, "
            "polyline=use the provided polyline"
        ),
    )
    analysis: AnalysisOptions = Field(default_factory=AnalysisOptions)
    environment: EnvironmentOptions = Field(default_factory=EnvironmentOptions)
    response: ResponseOptions = Field(default_factory=ResponseOptions)

    @model_validator(mode="after")
    def _check_route_input(self) -> "RecommendRequest":
        has_names = bool(self.origin and self.destination)
        has_coords = bool(self.origin_coords and self.destination_coords)
        has_polyline = self.polyline is not None
        has_gtfs = self.gtfs is not None

        if not any([has_names, has_coords, has_polyline, has_gtfs]):
            raise ValueError(
                "Provide one of: origin+destination (place names), "
                "origin_coords+destination_coords, gtfs, or polyline."
            )
        if has_polyline and len(self.polyline) < 2:
            raise ValueError("polyline requires at least 2 points.")
        if not has_gtfs and not has_polyline and self.departure is None:
            raise ValueError("departure is required unless using gtfs with a fully-specified trip.")
        return self


class BatchRequest(BaseModel):
    requests: list[RecommendRequest] = Field(..., min_length=1, max_length=_MAX_BATCH)
    fail_fast: bool = Field(False, description="Abort batch on first error")


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

class RecommendationOut(BaseModel):
    shade_side: SideLabel = Field(..., description="Side with more shade")
    sun_side: SideLabel = Field(..., description="Side with more direct sun")
    shade_pct: float = Field(..., description="Percentage of journey time with shade on shade_side")
    sun_pct: float = Field(..., description="Percentage of journey time with sun on sun_side")
    confidence: Confidence
    label_de: str = Field(..., description="Human-readable German recommendation")
    label_en: str = Field(..., description="Human-readable English recommendation")
    is_night: bool = Field(..., description="True if the entire journey is at night")
    low_direct_sun: bool = Field(..., description="True if sun intensity is low (clouds, low elevation)")


class JourneyOut(BaseModel):
    origin: str | None
    destination: str | None
    departure: datetime | None
    arrival_estimated: datetime | None
    duration_minutes: float | None
    distance_km: float | None


class RouteOut(BaseModel):
    provider_used: str = Field(..., description="Which route provider was ultimately used")
    points_count: int = Field(..., description="Number of raw route points before resampling")


class AnalysisOut(BaseModel):
    interval_m: int = Field(..., description="Sampling interval that was used")
    segments_count: int = Field(..., description="Number of analysed segments")
    tunnel_pct: float = Field(..., description="Percentage of route inside tunnels")
    terrain_pct: float = Field(..., description="Percentage of route shaded by terrain")
    mean_sun_elevation_deg: float | None = Field(None, description="Average sun elevation above horizon during journey")
    mean_cloud_cover_pct: float | None = Field(None, description="Average cloud cover; None if weather not requested")
    weather_adjusted: bool
    terrain_adjusted: bool
    intensity_adjusted: bool


class SegmentOut(BaseModel):
    lat: float
    lon: float
    time: str
    bearing: float = Field(..., description="Travel direction in degrees (0=North)")
    sun_azimuth: float = Field(..., description="Sun azimuth in degrees (0=North)")
    sun_elevation: float = Field(..., description="Sun elevation above horizon in degrees")
    sun_side: str = Field(..., description="'links'|'rechts'|'night'|'tunnel'")
    sun_factor: float = Field(..., description="Effective sun intensity 0..1 (weather+terrain adjusted)")
    terrain_shaded: bool


class ConvergenceStep(BaseModel):
    interval_m: int
    segments: int
    sun_side: str
    sun_pct: float
    shade_side: str
    shade_pct: float
    is_night: bool


class MetaOut(BaseModel):
    api_version: str
    computed_at: datetime
    processing_ms: int


class RecommendResponse(BaseModel):
    recommendation: RecommendationOut
    journey: JourneyOut
    route: RouteOut
    analysis: AnalysisOut
    segments: list[SegmentOut] | None = Field(None, description="Per-segment detail; null unless include_segments=true")
    polyline: list[list[float]] | None = Field(None, description="Route geometry [[lat,lon],...]; null unless include_polyline=true")
    convergence_trace: list[ConvergenceStep] | None = Field(None, description="Convergence iterations; null unless include_convergence_trace=true")
    meta: MetaOut


class BatchItemResult(BaseModel):
    index: int = Field(..., description="Zero-based index in the input requests array")
    success: bool
    result: RecommendResponse | None = None
    error: str | None = None


class BatchResponse(BaseModel):
    results: list[BatchItemResult]
    processed: int
    failed: int
    meta: MetaOut


# ---------------------------------------------------------------------------
# Route orchestration
# ---------------------------------------------------------------------------

def _resolve_coords(req: RecommendRequest) -> tuple[tuple[float, float], tuple[float, float]]:
    if req.origin_coords and req.destination_coords:
        return (
            (req.origin_coords.lat, req.origin_coords.lon),
            (req.destination_coords.lat, req.destination_coords.lon),
        )
    if req.origin and req.destination:
        start = geocode_place(req.origin)
        end = geocode_place(req.destination)
        return start, end
    raise HTTPException(400, "Cannot resolve coordinates: provide origin+destination or origin_coords+destination_coords.")


def _travel_hours(req: RecommendRequest) -> float | None:
    return req.travel_minutes / 60.0 if req.travel_minutes is not None else None


def _rescale_duration(points: list[RoutePoint], travel_hours: float) -> list[RoutePoint]:
    if len(points) < 2:
        return points
    orig_start = points[0].timestamp
    orig_secs = (points[-1].timestamp - orig_start).total_seconds()
    if orig_secs <= 0:
        return points
    scale = (travel_hours * 3600) / orig_secs
    result = []
    for p in points:
        elapsed = (p.timestamp - orig_start).total_seconds()
        result.append(RoutePoint(
            lat=p.lat,
            lon=p.lon,
            timestamp=orig_start + timedelta(seconds=elapsed * scale),
            in_tunnel=p.in_tunnel,
        ))
    return result


def _polyline_to_route(polyline: list[list[float]], departure: datetime, travel_hours: float | None) -> list[RoutePoint]:
    coords = [(float(row[0]), float(row[1])) for row in polyline]
    if len(coords) < 2:
        raise ValueError("polyline needs at least 2 points.")
    seg_lens = [
        haversine_m(
            RoutePoint(lat=coords[i][0], lon=coords[i][1], timestamp=departure),
            RoutePoint(lat=coords[i + 1][0], lon=coords[i + 1][1], timestamp=departure),
        )
        for i in range(len(coords) - 1)
    ]
    total_m = sum(seg_lens) or 1.0
    travel_s = (travel_hours * 3600) if travel_hours else (total_m / 1000) / 80 * 3600
    accumulated = 0.0
    points: list[RoutePoint] = []
    for i, (lat, lon) in enumerate(coords):
        if i > 0:
            accumulated += seg_lens[i - 1]
        ts = departure + timedelta(seconds=accumulated / total_m * travel_s)
        points.append(RoutePoint(lat=lat, lon=lon, timestamp=ts))
    return points


def _resolve_route(req: RecommendRequest, gtfs_dbs: dict[str, GtfsDatabase]) -> tuple[list[RoutePoint], str]:
    travel_h = _travel_hours(req)
    departure = req.departure or datetime.now(timezone.utc)

    # --- Explicit polyline ---
    if req.polyline or req.provider == ProviderHint.polyline:
        if not req.polyline:
            raise HTTPException(400, "provider=polyline requires a polyline.")
        points = _polyline_to_route(req.polyline, departure, travel_h)
        return points, "polyline"

    # --- GTFS ---
    if req.gtfs is not None or req.provider == ProviderHint.gtfs:
        g = req.gtfs
        feed = g.feed if g else None
        if not feed:
            raise HTTPException(400, "gtfs.feed is required for provider=gtfs.")
        if feed not in gtfs_dbs:
            raise HTTPException(400, f"GTFS feed '{feed}' not loaded. Available: {sorted(gtfs_dbs)}")

        db = gtfs_dbs[feed]

        # Case A: fully-specified trip
        if g and g.trip_id and g.board_stop_id and g.alight_stop_id:
            svc_date = g.service_date or departure.date()
            points = db.build_route(
                trip_id=g.trip_id,
                board_stop_id=g.board_stop_id,
                alight_stop_id=g.alight_stop_id,
                service_date=svc_date,
            )
            if travel_h is not None:
                points = _rescale_duration(points, travel_h)
            return points, f"gtfs:{feed}"

        # Case B: search by stop IDs
        if g and g.board_stop_id and g.alight_stop_id:
            trips = db.find_trips(
                board_stop_id=g.board_stop_id,
                alight_stop_id=g.alight_stop_id,
                date_=g.service_date or departure.date(),
            )
            if not trips:
                raise HTTPException(400, f"No trips found between given stops in feed '{feed}'.")
            trip = min(trips, key=lambda t: abs((t.board_departure - departure).total_seconds()))
            points = db.build_route(
                trip_id=trip.trip_id,
                board_stop_id=trip.board_stop_id,
                alight_stop_id=trip.alight_stop_id,
                service_date=trip.service_date,
            )
            if travel_h is not None:
                points = _rescale_duration(points, travel_h)
            return points, f"gtfs:{feed}"

        # Case C: search by place names
        if req.origin and req.destination:
            origin_stops = db.search_stops(req.origin, limit=5)
            dest_stops = db.search_stops(req.destination, limit=5)
            if not origin_stops:
                raise HTTPException(400, f"No stops found for '{req.origin}' in feed '{feed}'.")
            if not dest_stops:
                raise HTTPException(400, f"No stops found for '{req.destination}' in feed '{feed}'.")
            trips = db.find_trips(
                board_stop_id=origin_stops[0].stop_id,
                alight_stop_id=dest_stops[0].stop_id,
                date_=departure.date(),
            )
            if not trips:
                raise HTTPException(400, f"No trips found for this route/date in feed '{feed}'.")
            trip = min(trips, key=lambda t: abs((t.board_departure - departure).total_seconds()))
            points = db.build_route(
                trip_id=trip.trip_id,
                board_stop_id=trip.board_stop_id,
                alight_stop_id=trip.alight_stop_id,
                service_date=trip.service_date,
            )
            if travel_h is not None:
                points = _rescale_duration(points, travel_h)
            return points, f"gtfs:{feed}"

        raise HTTPException(400, "gtfs query needs trip_id+stop_ids, stop_ids, or origin+destination place names.")

    # --- Flight / great-circle ---
    if req.mode == TransportMode.flight or req.provider == ProviderHint.great_circle:
        start, end = _resolve_coords(req)
        points = make_great_circle_route(start, end, departure, travel_hours=travel_h)
        return points, "great_circle"

    # --- Rail: try OSM, fall back to great-circle ---
    if req.mode == TransportMode.rail:
        if req.provider in (ProviderHint.auto, ProviderHint.osm):
            try:
                start, end = _resolve_coords(req)
                prov = OsmRailProvider()
                points = prov.get_route_between_coordinates(start, end, departure, travel_hours=travel_h)
                return points, "osm"
            except HTTPException:
                raise
            except Exception as exc:
                if req.provider == ProviderHint.osm:
                    raise HTTPException(503, f"OSM provider failed: {exc}") from exc
                # auto mode: fall through to great-circle

        start, end = _resolve_coords(req)
        points = make_great_circle_route(start, end, departure, travel_hours=travel_h)
        return points, "great_circle"

    # --- Bus / car: try OSRM, fall back to great-circle ---
    if req.mode in (TransportMode.bus, TransportMode.car):
        if req.provider in (ProviderHint.auto, ProviderHint.osrm):
            try:
                start, end = _resolve_coords(req)
                prov = OsrmRoadProvider()
                points = prov.get_route_between_coordinates(start, end, departure, travel_hours=travel_h)
                return points, "osrm"
            except HTTPException:
                raise
            except Exception as exc:
                if req.provider == ProviderHint.osrm:
                    raise HTTPException(503, f"OSRM provider failed: {exc}") from exc

        start, end = _resolve_coords(req)
        points = make_great_circle_route(start, end, departure, travel_hours=travel_h)
        return points, "great_circle"

    # --- Custom without polyline: great-circle ---
    start, end = _resolve_coords(req)
    points = make_great_circle_route(start, end, departure, travel_hours=travel_h)
    return points, "great_circle"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _run_analysis(req: RecommendRequest, points: list[RoutePoint]) -> tuple[Any, list[dict]]:
    weather_provider = OpenMeteoWeatherProvider() if req.environment.weather else None

    terrain_provider = None
    if req.environment.terrain:
        cfg = TerrainConfig(
            enabled=True,
            max_distance_m=req.environment.terrain_max_distance_m,
            sample_step_m=req.environment.terrain_sample_step_m,
            elevation_margin_deg=req.environment.terrain_elevation_margin_deg,
        )
        terrain_provider = TerrainProvider(config=cfg)

    opts = req.analysis
    trace: list[dict] = []

    if opts.mode == AnalysisMode.converged:
        rec, trace = analyze_converged(
            points,
            tolerance_pct=opts.converge_tolerance_pct,
            max_segments=opts.max_segments,
            weather_provider=weather_provider,
            use_intensity=opts.use_intensity,
            terrain_provider=terrain_provider,
        )
        return rec, trace

    interval_m: int | None = None
    if opts.interval_m is not None:
        interval_m = opts.interval_m
    elif opts.mode == AnalysisMode.fast:
        interval_m = 5_000
    elif opts.mode == AnalysisMode.precise:
        interval_m = 200
    # auto: interval_m = None → auto_interval_m() inside analyze()

    rec = analyze(
        points,
        interval_m=interval_m,
        weather_provider=weather_provider,
        use_intensity=opts.use_intensity,
        terrain_provider=terrain_provider,
    )
    return rec, trace


# ---------------------------------------------------------------------------
# Response building
# ---------------------------------------------------------------------------

def _calc_confidence(shade_pct: float, is_night: bool, low_direct_sun: bool) -> Confidence:
    if is_night:
        return Confidence.night
    if low_direct_sun:
        return Confidence.unclear
    if shade_pct >= 65:
        return Confidence.high
    if shade_pct >= 55:
        return Confidence.medium
    return Confidence.low


def _labels(shade_side: str, shade_pct: float, is_night: bool, low_direct_sun: bool) -> tuple[str, str]:
    side_de = "links" if shade_side == "links" else "rechts"
    side_en = "left" if shade_side == "links" else "right"
    pct = round(shade_pct)
    if is_night:
        return (
            "Nachtfahrt — kein direktes Sonnenlicht",
            "Night journey — no direct sunlight",
        )
    if low_direct_sun:
        return (
            f"Sitz {side_de} — wenig direkte Sonne ({pct} % Schatten)",
            f"Sit {side_en} — little direct sun ({pct} % shade)",
        )
    return (
        f"Sitz {side_de} — {pct} % im Schatten",
        f"Sit {side_en} — {pct} % in shade",
    )


def _route_distance_km(points: list[RoutePoint]) -> float:
    if len(points) < 2:
        return 0.0
    total = sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))
    return round(total / 1000, 1)


def _origin_label(req: RecommendRequest) -> str | None:
    if req.origin:
        return req.origin
    if req.origin_coords:
        return f"{req.origin_coords.lat:.4f},{req.origin_coords.lon:.4f}"
    return None


def _destination_label(req: RecommendRequest) -> str | None:
    if req.destination:
        return req.destination
    if req.destination_coords:
        return f"{req.destination_coords.lat:.4f},{req.destination_coords.lon:.4f}"
    return None


def _build_response(
    req: RecommendRequest,
    points: list[RoutePoint],
    provider_name: str,
    rec: Any,
    trace: list[dict],
    t0: float,
) -> RecommendResponse:
    shade_side = SideLabel.left if rec.shade_side == "links" else SideLabel.right
    sun_side = SideLabel.left if rec.sun_side == "links" else SideLabel.right
    label_de, label_en = _labels(rec.shade_side, rec.shade_pct, rec.is_night, rec.low_direct_sun)
    confidence = _calc_confidence(rec.shade_pct, rec.is_night, rec.low_direct_sun)

    duration_min: float | None = None
    if len(points) >= 2:
        duration_min = round((points[-1].timestamp - points[0].timestamp).total_seconds() / 60, 1)

    segments_out: list[SegmentOut] | None = None
    if req.response.include_segments:
        segments_out = [
            SegmentOut(
                lat=round(s.point.lat, 5),
                lon=round(s.point.lon, 5),
                time=s.point.timestamp.isoformat(),
                bearing=round(s.bearing, 1),
                sun_azimuth=round(s.sun_azimuth, 1),
                sun_elevation=round(s.sun_elevation, 1),
                sun_side=s.sun_side,
                sun_factor=round(s.sun_factor, 3),
                terrain_shaded=s.terrain_shaded,
            )
            for s in rec.segments
        ]

    polyline_out: list[list[float]] | None = None
    if req.response.include_polyline:
        polyline_out = [[round(p.lat, 5), round(p.lon, 5)] for p in points]

    trace_out: list[ConvergenceStep] | None = None
    if req.response.include_convergence_trace and trace:
        trace_out = [
            ConvergenceStep(
                interval_m=step["interval_m"],
                segments=step["segments"],
                sun_side=step["sun_side"],
                sun_pct=round(step["sun_pct"], 1),
                shade_side=step["shade_side"],
                shade_pct=round(step["shade_pct"], 1),
                is_night=step["is_night"],
            )
            for step in trace
        ]

    return RecommendResponse(
        recommendation=RecommendationOut(
            shade_side=shade_side,
            sun_side=sun_side,
            shade_pct=round(rec.shade_pct, 1),
            sun_pct=round(rec.sun_pct, 1),
            confidence=confidence,
            label_de=label_de,
            label_en=label_en,
            is_night=rec.is_night,
            low_direct_sun=rec.low_direct_sun,
        ),
        journey=JourneyOut(
            origin=_origin_label(req),
            destination=_destination_label(req),
            departure=points[0].timestamp if points else req.departure,
            arrival_estimated=points[-1].timestamp if points else None,
            duration_minutes=duration_min,
            distance_km=_route_distance_km(points),
        ),
        route=RouteOut(
            provider_used=provider_name,
            points_count=len(points),
        ),
        analysis=AnalysisOut(
            interval_m=rec.auto_interval_m,
            segments_count=len(rec.segments),
            tunnel_pct=round(rec.tunnel_pct, 1),
            terrain_pct=round(rec.terrain_pct, 1),
            mean_sun_elevation_deg=(
                round(rec.mean_sun_elevation, 1) if rec.mean_sun_elevation is not None else None
            ),
            mean_cloud_cover_pct=(
                round(rec.mean_cloud_cover_pct, 1) if rec.mean_cloud_cover_pct is not None else None
            ),
            weather_adjusted=rec.weather_adjusted,
            terrain_adjusted=rec.terrain_adjusted,
            intensity_adjusted=rec.intensity_adjusted,
        ),
        segments=segments_out,
        polyline=polyline_out,
        convergence_trace=trace_out,
        meta=MetaOut(
            api_version=_API_VERSION,
            computed_at=datetime.now(timezone.utc),
            processing_ms=int((time.monotonic() - t0) * 1000),
        ),
    )


def _process_one(req: RecommendRequest, gtfs_dbs: dict[str, GtfsDatabase]) -> RecommendResponse:
    t0 = time.monotonic()
    points, provider_name = _resolve_route(req, gtfs_dbs)
    rec, trace = _run_analysis(req, points)
    return _build_response(req, points, provider_name, rec, trace, t0)


# ---------------------------------------------------------------------------
# GTFS DB cache
# ---------------------------------------------------------------------------

def _open_gtfs_dbs() -> dict[str, GtfsDatabase]:
    global _gtfs_cache
    if _gtfs_cache:
        return _gtfs_cache
    if not _GTFS_DIR.exists():
        return {}
    for p in _GTFS_DIR.glob("*.sqlite"):
        if p.stem not in _gtfs_cache:
            try:
                _gtfs_cache[p.stem] = GtfsDatabase(p)
            except Exception:
                pass
    return _gtfs_cache


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/health",
    summary="Health check",
    description="Returns API status, loaded GTFS feeds, and available capabilities.",
)
def health(api_key: str = Depends(_auth)) -> dict:
    dbs = _open_gtfs_dbs()
    feeds = []
    for name, db in sorted(dbs.items()):
        try:
            info = db.feed_info()
            feeds.append({
                "name": name,
                "publisher": info.get("publisher_name"),
                "start_date": info.get("start_date"),
                "end_date": info.get("end_date"),
                "imported_at": info.get("imported_at_utc"),
            })
        except Exception as exc:
            feeds.append({"name": name, "error": str(exc)})

    return {
        "status": "ok",
        "api_version": _API_VERSION,
        "auth_required": bool(_API_KEYS),
        "rate_limit_per_minute": _RATE_LIMIT,
        "batch_rate_limit_per_minute": _BATCH_RATE_LIMIT,
        "max_batch_size": _MAX_BATCH,
        "gtfs_feeds": feeds,
    }


@router.get(
    "/capabilities",
    summary="API capabilities",
    description="Describes all supported transport modes, providers, and analysis options.",
)
def capabilities() -> dict:
    return {
        "api_version": _API_VERSION,
        "transport_modes": {
            "rail": "Train — uses OSM real track geometry via Overpass API",
            "bus": "Bus — uses OSRM road routing",
            "flight": "Aircraft — uses geodesic great-circle approximation",
            "car": "Car — uses OSRM road routing",
            "custom": "Custom — requires polyline or gtfs input",
        },
        "route_providers": {
            "auto": "Smart selection based on transport mode (recommended)",
            "gtfs": "GTFS scheduled service — most accurate timing and geometry",
            "osm": "OSM Overpass real rail track geometry (rail only)",
            "osrm": "OSRM road routing (bus/car)",
            "great_circle": "Geodesic great-circle fallback (all modes)",
            "polyline": "Custom [[lat,lon],...] polyline",
        },
        "analysis_modes": {
            "auto": "Bearing-variance-based adaptive interval — recommended",
            "fast": "5 km intervals — fast, good for straight routes",
            "precise": "200 m intervals — good for moderately curvy routes",
            "converged": "Iterative halving until result stabilises — most accurate, slowest",
        },
        "environment_options": {
            "weather": "Fetch cloud cover from Open-Meteo, weight sun exposure",
            "terrain": "Fetch elevation via Open-Meteo, detect terrain/mountain shading",
        },
        "gtfs_feeds_loaded": sorted(_open_gtfs_dbs().keys()),
        "batch_max_requests": _MAX_BATCH,
    }


_RECOMMEND_EXAMPLES = {
    "rail_osm_auto": {
        "summary": "Rail — OSM geometry, auto interval, weather + terrain",
        "description": (
            "Rail route Wien→Salzburg using real OSM track geometry. "
            "Analysis interval auto-selected by bearing variance. "
            "Cloud cover and terrain shading included. Route polyline returned."
        ),
        "value": {
            "mode": "rail",
            "origin": "Wien Hbf",
            "destination": "Salzburg Hbf",
            "departure": "2026-05-15T09:14:00+02:00",
            "provider": "osm",
            "analysis": {
                "mode": "auto",
                "use_intensity": True,
            },
            "environment": {
                "weather": True,
                "terrain": True,
                "terrain_max_distance_m": 8000,
                "terrain_sample_step_m": 500,
            },
            "response": {
                "include_polyline": True,
                "include_segments": False,
                "language": "de",
            },
        },
    },
    "rail_gtfs_converged": {
        "summary": "Rail — GTFS (stop IDs), converged mode + full trace",
        "description": (
            "ÖBB Railjet using GTFS feed — provide stop IDs and the API finds the "
            "closest trip. Convergence mode iterates sampling intervals until the "
            "result stabilises (most accurate for Alpine routes). "
            "Full convergence trace and per-segment detail returned."
        ),
        "value": {
            "mode": "rail",
            "gtfs": {
                "feed": "oebb",
                "board_stop_id": "8100003",
                "alight_stop_id": "8100002",
                "service_date": "2026-05-15",
            },
            "departure": "2026-05-15T09:14:00+02:00",
            "provider": "gtfs",
            "analysis": {
                "mode": "converged",
                "converge_tolerance_pct": 0.5,
                "use_intensity": True,
            },
            "environment": {
                "weather": True,
                "terrain": True,
            },
            "response": {
                "include_segments": True,
                "include_convergence_trace": True,
                "language": "de",
            },
        },
    },
    "rail_gtfs_trip_id": {
        "summary": "Rail — GTFS fully specified (trip_id + stop IDs)",
        "description": (
            "Most precise input: exact trip_id, board/alight stop IDs, and service date "
            "as returned by GET /api/gtfs/{feed}/trips. Skips trip search entirely."
        ),
        "value": {
            "mode": "rail",
            "gtfs": {
                "feed": "db",
                "trip_id": "ICE_1234_20260515",
                "board_stop_id": "8000105",
                "alight_stop_id": "8000261",
                "service_date": "2026-05-15",
            },
            "provider": "gtfs",
            "analysis": {
                "mode": "precise",
            },
            "response": {
                "include_polyline": True,
                "language": "de",
            },
        },
    },
    "rail_gtfs_names": {
        "summary": "Rail — GTFS by place names (auto stop search)",
        "description": (
            "Simplest GTFS call: just origin/destination names and departure time. "
            "The API searches for matching stops in the feed and picks the closest trip."
        ),
        "value": {
            "mode": "rail",
            "origin": "München Hbf",
            "destination": "Berlin Hbf",
            "departure": "2026-05-15T07:00:00+02:00",
            "gtfs": {"feed": "db"},
            "analysis": {"mode": "auto"},
        },
    },
    "bus_gtfs_flixbus": {
        "summary": "Bus — Flixbus GTFS by stop IDs",
        "description": (
            "Flixbus route using GTFS feed. Stop IDs from GET /api/gtfs/flixbus/stops. "
            "Precise mode recommended for bus routes (road routing = more curves than rail)."
        ),
        "value": {
            "mode": "bus",
            "gtfs": {
                "feed": "flixbus",
                "board_stop_id": "40",
                "alight_stop_id": "1",
                "service_date": "2026-05-15",
            },
            "departure": "2026-05-15T08:00:00+02:00",
            "provider": "gtfs",
            "analysis": {"mode": "precise"},
            "response": {"language": "de"},
        },
    },
    "bus_osrm_coords": {
        "summary": "Bus — OSRM road routing via coordinates",
        "description": (
            "Bus route without GTFS: exact coordinates as input, OSRM real road geometry. "
            "Good for routes where no GTFS feed is available."
        ),
        "value": {
            "mode": "bus",
            "origin_coords": {"lat": 46.6228, "lon": 14.3052},
            "destination_coords": {"lat": 45.4654, "lon": 9.1859},
            "departure": "2026-05-15T06:30:00+02:00",
            "travel_minutes": 240,
            "provider": "osrm",
            "analysis": {"mode": "precise"},
        },
    },
    "flight_great_circle": {
        "summary": "Flight — great-circle, intensity weighted",
        "description": (
            "Flight route using geodesic great-circle approximation. "
            "use_intensity=true weights high-elevation (midday) sun more than "
            "low-angle (morning/evening) sun — relevant for long east-west flights."
        ),
        "value": {
            "mode": "flight",
            "origin": "Wien Flughafen",
            "destination": "New York JFK",
            "departure": "2026-05-15T11:30:00+02:00",
            "travel_minutes": 555,
            "provider": "great_circle",
            "analysis": {
                "mode": "precise",
                "use_intensity": True,
            },
            "response": {
                "include_polyline": True,
                "language": "en",
            },
        },
    },
    "car_osrm": {
        "summary": "Car — OSRM road routing",
        "description": "Car journey using real road geometry from OSRM.",
        "value": {
            "mode": "car",
            "origin": "Innsbruck",
            "destination": "Zürich",
            "departure": "2026-05-15T08:00:00+02:00",
            "provider": "osrm",
            "analysis": {
                "mode": "auto",
                "use_intensity": False,
            },
            "environment": {"weather": True},
        },
    },
    "custom_polyline": {
        "summary": "Custom polyline (GPX-style input)",
        "description": (
            "Arbitrary route as [[lat, lon], ...] — use this for exotic routes, "
            "historic railways, or any route not in GTFS/OSM."
        ),
        "value": {
            "mode": "custom",
            "polyline": [
                [48.1951, 16.3766],
                [47.9500, 15.9800],
                [47.4968, 14.8553],
                [47.2267, 14.1870],
                [47.0408, 13.4870],
                [47.8126, 13.0451],
            ],
            "departure": "2026-05-15T09:14:00+02:00",
            "travel_minutes": 147,
            "analysis": {
                "mode": "converged",
                "use_intensity": True,
            },
            "response": {
                "include_polyline": True,
                "include_convergence_trace": True,
                "include_segments": True,
            },
        },
    },
    "rail_fast_minimal": {
        "summary": "Rail — minimal request (fast mode, no extras)",
        "description": (
            "Simplest possible request: just mode, origin, destination, departure. "
            "Fast 5 km sampling — good for straight routes (Hamburg–Berlin etc.)."
        ),
        "value": {
            "mode": "rail",
            "origin": "Hamburg Hbf",
            "destination": "Berlin Hbf",
            "departure": "2026-05-15T10:00:00+02:00",
            "analysis": {"mode": "fast"},
        },
    },
}


@router.post(
    "/recommend",
    response_model=RecommendResponse,
    summary="Sun-side recommendation",
    description=(
        "Compute which side of the vehicle to sit on for maximum shade (or sun). "
        "Supports all transport modes and route providers. "
        "**Provider auto-selection:** rail→OSM track geometry, bus/car→OSRM road routing, "
        "flight→great-circle. Override with `provider` field or supply `gtfs` for "
        "schedule-based routing.\n\n"
        "**Analysis modes:** `auto` (adaptive interval), `fast` (5 km), "
        "`precise` (200 m), `converged` (iterates until stable — best for Alpine routes).\n\n"
        "Use `include_convergence_trace=true` with `mode=converged` to inspect "
        "how the result stabilised across sampling intervals."
    ),
)
def recommend(
    req: Annotated[RecommendRequest, Body(openapi_examples=_RECOMMEND_EXAMPLES)],
    api_key: str = Depends(_auth_single),
) -> RecommendResponse:
    try:
        return _process_one(req, _open_gtfs_dbs())
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Unexpected error: {exc}") from exc


# ---------------------------------------------------------------------------
# GTFS discovery endpoints — Step 1 and 2 of the B2B workflow
# ---------------------------------------------------------------------------

class StopOut(BaseModel):
    stop_id: str = Field(..., description="Use this as board_stop_id / alight_stop_id in /recommend")
    name: str
    lat: float
    lon: float
    timezone: str | None


class TripOut(BaseModel):
    trip_id: str = Field(..., description="Use in gtfs.trip_id for the most precise /recommend call")
    route_short_name: str | None
    route_long_name: str | None
    headsign: str | None
    board_stop_id: str
    board_stop_name: str
    board_departure: str = Field(..., description="ISO 8601 local departure time")
    alight_stop_id: str
    alight_stop_name: str
    alight_arrival: str = Field(..., description="ISO 8601 local arrival time")
    duration_minutes: int
    service_date: str
    label: str = Field(..., description="Human-readable summary for display")


@router.get(
    "/gtfs/feeds",
    summary="List available GTFS feeds",
    description=(
        "Returns all loaded GTFS feeds (e.g. `oebb`, `db`, `flixbus`). "
        "Use the feed name as the `{feed}` parameter in the other GTFS endpoints."
    ),
)
def gtfs_feeds(api_key: str = Depends(_auth)) -> dict:
    dbs = _open_gtfs_dbs()
    feeds = []
    for name, db in sorted(dbs.items()):
        try:
            info = db.feed_info()
            feeds.append({
                "feed": name,
                "publisher": info.get("publisher_name"),
                "valid_from": info.get("start_date"),
                "valid_until": info.get("end_date"),
            })
        except Exception:
            feeds.append({"feed": name})
    return {"feeds": feeds}


@router.get(
    "/gtfs/{feed}/stops",
    response_model=list[StopOut],
    summary="Search stops by name",
    description=(
        "**Step 1 of the B2B workflow.** Search for stops by name within a GTFS feed.\n\n"
        "Returns `stop_id` values you need for `board_stop_id` / `alight_stop_id` "
        "in `/recommend` and in `/gtfs/{feed}/trips`.\n\n"
        "Results are deduplicated by name and ranked by trip frequency — "
        "the first result is almost always the right main station."
    ),
)
def gtfs_stops(
    feed: str,
    q: str = Query(min_length=2, description="Station name fragment, e.g. 'Wien', 'München', 'Salzburg'"),
    limit: int = Query(default=10, ge=1, le=50),
    api_key: str = Depends(_auth),
) -> list[StopOut]:
    dbs = _open_gtfs_dbs()
    if feed not in dbs:
        raise HTTPException(404, f"Feed '{feed}' not found. Available: {sorted(dbs)}")
    stops = dbs[feed].search_stops(q, limit=limit)
    return [
        StopOut(
            stop_id=s.stop_id,
            name=s.name,
            lat=s.lat,
            lon=s.lon,
            timezone=s.timezone,
        )
        for s in stops
    ]


@router.get(
    "/gtfs/{feed}/trips",
    response_model=list[TripOut],
    summary="Find trips between two stops",
    description=(
        "**Step 2 of the B2B workflow.** Find scheduled trips between two stops on a given date.\n\n"
        "Use `stop_id` values from `/gtfs/{feed}/stops`. "
        "Returns trips sorted by departure time. "
        "Pick the trip matching your desired departure and use `trip_id` + `board_stop_id` + "
        "`alight_stop_id` + `service_date` in the `gtfs` block of `/recommend` for the most "
        "precise result — this skips all auto-search and uses the exact schedule.\n\n"
        "**Typical workflow:**\n"
        "```\n"
        "GET /api/v1/gtfs/oebb/stops?q=Wien        → stop_id for Wien Hbf\n"
        "GET /api/v1/gtfs/oebb/stops?q=Salzburg    → stop_id for Salzburg Hbf\n"
        "GET /api/v1/gtfs/oebb/trips?board=<id>&alight=<id>&date=2026-05-15\n"
        "POST /api/v1/recommend  { gtfs: { feed, trip_id, board_stop_id, alight_stop_id, service_date } }\n"
        "```"
    ),
)
def gtfs_trips(
    feed: str,
    board: str = Query(description="Boarding stop_id (from /gtfs/{feed}/stops)"),
    alight: str = Query(description="Alighting stop_id (from /gtfs/{feed}/stops)"),
    date: str = Query(description="Service date as YYYY-MM-DD"),
    limit: int = Query(default=20, ge=1, le=100),
    api_key: str = Depends(_auth),
) -> list[TripOut]:
    dbs = _open_gtfs_dbs()
    if feed not in dbs:
        raise HTTPException(404, f"Feed '{feed}' not found. Available: {sorted(dbs)}")
    try:
        service_date = date_cls.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    trips = dbs[feed].find_trips(
        board_stop_id=board,
        alight_stop_id=alight,
        date_=service_date,
        limit=limit,
    )
    return [
        TripOut(
            trip_id=t.trip_id,
            route_short_name=t.route_short_name,
            route_long_name=t.route_long_name,
            headsign=t.headsign,
            board_stop_id=t.board_stop_id,
            board_stop_name=t.board_stop_name,
            board_departure=t.board_departure.isoformat(),
            alight_stop_id=t.alight_stop_id,
            alight_stop_name=t.alight_stop_name,
            alight_arrival=t.alight_arrival.isoformat(),
            duration_minutes=t.duration_minutes,
            service_date=t.service_date.isoformat(),
            label=t.label,
        )
        for t in trips
    ]


@router.post(
    "/recommend/batch",
    response_model=BatchResponse,
    summary="Batch sun-side recommendations",
    description=(
        f"Process up to {_MAX_BATCH} recommendation requests in a single call. "
        "Useful for pre-computing seat recommendations for a full timetable. "
        "Set fail_fast=true to abort on first error."
    ),
)
def recommend_batch(
    body: BatchRequest,
    api_key: str = Depends(_auth_batch),
) -> BatchResponse:
    t0 = time.monotonic()
    gtfs_dbs = _open_gtfs_dbs()
    results: list[BatchItemResult] = []
    failed = 0

    for i, req in enumerate(body.requests):
        try:
            result = _process_one(req, gtfs_dbs)
            results.append(BatchItemResult(index=i, success=True, result=result))
        except HTTPException as exc:
            failed += 1
            results.append(BatchItemResult(index=i, success=False, error=exc.detail))
            if body.fail_fast:
                break
        except Exception as exc:
            failed += 1
            results.append(BatchItemResult(index=i, success=False, error=str(exc)))
            if body.fail_fast:
                break

    return BatchResponse(
        results=results,
        processed=len(results),
        failed=failed,
        meta=MetaOut(
            api_version=_API_VERSION,
            computed_at=datetime.now(timezone.utc),
            processing_ms=int((time.monotonic() - t0) * 1000),
        ),
    )
