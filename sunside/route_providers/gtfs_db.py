"""GTFS-backed route provider with trip search and shape slicing.

Use it like this:
    db = GtfsDatabase("data/gtfs/flixbus.sqlite")
    stops_villach = db.search_stops("Villach")
    stops_padova = db.search_stops("Padova")
    trips = db.find_trips(
        board_stop_id=stops_villach[0].stop_id,
        alight_stop_id=stops_padova[0].stop_id,
        date=date(2026, 5, 1),
    )
    points = db.build_route(trips[0].trip_id, board_stop_id, alight_stop_id, date(2026, 5, 1))
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytz

from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import haversine_m


@dataclass(frozen=True)
class GtfsStop:
    stop_id: str
    name: str
    lat: float
    lon: float
    timezone: str | None


@dataclass(frozen=True)
class GtfsTripCandidate:
    trip_id: str
    route_id: str
    route_short_name: str | None
    route_long_name: str | None
    headsign: str | None
    board_stop_id: str
    board_stop_name: str
    alight_stop_id: str
    alight_stop_name: str
    board_departure: datetime  # tz-aware
    alight_arrival: datetime   # tz-aware
    duration_minutes: int
    service_date: date         # GTFS service anchor date
    board_timezone: str
    agency_timezone: str

    @property
    def label(self) -> str:
        line = self.route_short_name or self.route_id
        local = self.board_departure.astimezone(pytz.timezone(self.board_timezone))
        return (
            f"{line} | ab {local:%H:%M} {self.board_stop_name} -> "
            f"{self.alight_stop_name} ({self.duration_minutes} min)"
        )


class GtfsDatabase:
    """Read-only access to a GTFS SQLite DB built by scripts/gtfs_import.py."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(self.db_path)
        self._conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._agency_tz_cache: dict[str, str] = {}

    def close(self) -> None:
        self._conn.close()

    # --- metadata --------------------------------------------------------

    def feed_info(self) -> dict:
        row = self._conn.execute("SELECT * FROM feed_info LIMIT 1").fetchone()
        meta = {r["key"]: r["value"] for r in self._conn.execute("SELECT * FROM import_meta")}
        return {**(dict(row) if row else {}), **meta}

    # --- stops -----------------------------------------------------------

    def search_stops(self, query: str, limit: int = 20) -> list[GtfsStop]:
        like = f"%{query.strip()}%"
        rows = self._conn.execute(
            "SELECT stop_id, name, lat, lon, timezone FROM stops "
            "WHERE name LIKE ? ORDER BY name LIMIT ?",
            (like, limit),
        ).fetchall()
        return [GtfsStop(**dict(r)) for r in rows]

    def get_stop(self, stop_id: str) -> GtfsStop | None:
        row = self._conn.execute(
            "SELECT stop_id, name, lat, lon, timezone FROM stops WHERE stop_id = ?",
            (stop_id,),
        ).fetchone()
        return GtfsStop(**dict(row)) if row else None

    # --- trip search -----------------------------------------------------

    def find_trips(
        self,
        *,
        board_stop_id: str,
        alight_stop_id: str,
        date_: date,
        earliest_local: time | None = None,
        latest_local: time | None = None,
        limit: int = 20,
    ) -> list[GtfsTripCandidate]:
        """Trips that visit board_stop before alight_stop on date_ (local at board stop).

        Handles GTFS overflow times (>=24:00:00) by also checking the previous service day.
        """
        board_stop = self.get_stop(board_stop_id)
        if board_stop is None:
            return []

        # Cover service days [date-1 .. date+1] to catch overnight overflows in either direction.
        candidates: list[GtfsTripCandidate] = []
        for offset_days in (-1, 0, 1):
            service_date = date_ + timedelta(days=offset_days)
            active_services = self._active_service_ids(service_date)
            if not active_services:
                continue

            placeholders = ",".join("?" for _ in active_services)
            sql = f"""
            SELECT
                t.trip_id, t.route_id, t.headsign,
                r.short_name AS route_short_name, r.long_name AS route_long_name,
                st1.departure_time AS board_dep,
                st2.arrival_time   AS alight_arr,
                sb.name AS board_name, sa.name AS alight_name,
                a.timezone AS agency_tz
            FROM trips t
            JOIN routes r       ON r.route_id = t.route_id
            JOIN agency a       ON a.agency_id = r.agency_id
            JOIN stop_times st1 ON st1.trip_id = t.trip_id AND st1.stop_id = ?
            JOIN stop_times st2 ON st2.trip_id = t.trip_id AND st2.stop_id = ?
            JOIN stops sb       ON sb.stop_id = st1.stop_id
            JOIN stops sa       ON sa.stop_id = st2.stop_id
            WHERE t.service_id IN ({placeholders})
              AND st2.stop_sequence > st1.stop_sequence
            """
            params: list = [board_stop_id, alight_stop_id, *active_services]
            rows = self._conn.execute(sql, params).fetchall()

            for row in rows:
                agency_timezone = row["agency_tz"] or "UTC"
                board_timezone = board_stop.timezone or agency_timezone
                board_tz = pytz.timezone(board_timezone)
                board_dt = self._gtfs_time_to_datetime(row["board_dep"], service_date, agency_timezone)
                alight_dt = self._gtfs_time_to_datetime(row["alight_arr"], service_date, agency_timezone)
                if board_dt is None or alight_dt is None:
                    continue
                # Filter: local date at the board stop must equal the requested date
                if board_dt.astimezone(board_tz).date() != date_:
                    continue
                local_t = board_dt.astimezone(board_tz).time()
                if earliest_local and local_t < earliest_local:
                    continue
                if latest_local and local_t > latest_local:
                    continue
                duration_min = max(1, int((alight_dt - board_dt).total_seconds() // 60))
                candidates.append(GtfsTripCandidate(
                    trip_id=row["trip_id"],
                    route_id=row["route_id"],
                    route_short_name=row["route_short_name"],
                    route_long_name=row["route_long_name"],
                    headsign=row["headsign"],
                    board_stop_id=board_stop_id,
                    board_stop_name=row["board_name"],
                    alight_stop_id=alight_stop_id,
                    alight_stop_name=row["alight_name"],
                    board_departure=board_dt,
                    alight_arrival=alight_dt,
                    duration_minutes=duration_min,
                    service_date=service_date,
                    board_timezone=board_timezone,
                    agency_timezone=agency_timezone,
                ))

        # De-duplicate (a trip may be picked up via multiple service-day offsets)
        unique = {c.trip_id: c for c in candidates}
        result = sorted(unique.values(), key=lambda c: c.board_departure)
        return result[:limit]

    # --- route building --------------------------------------------------

    def build_route(
        self,
        *,
        trip_id: str,
        board_stop_id: str,
        alight_stop_id: str,
        service_date: date,
    ) -> list[RoutePoint]:
        """Return RoutePoints for the trip between board and alight stops, with real timestamps.

        ``service_date`` is the GTFS service-anchor date (taken from a GtfsTripCandidate).
        """
        trip_row = self._conn.execute(
            "SELECT t.shape_id, r.agency_id, a.timezone "
            "FROM trips t JOIN routes r ON r.route_id = t.route_id "
            "JOIN agency a ON a.agency_id = r.agency_id WHERE t.trip_id = ?",
            (trip_id,),
        ).fetchone()
        if trip_row is None:
            raise ValueError(f"Trip {trip_id} not found")

        agency_tz = trip_row["timezone"]
        shape_id = trip_row["shape_id"]
        date_ = service_date

        # Stop times for the segment we need
        stop_rows = self._conn.execute(
            "SELECT st.stop_sequence, st.stop_id, st.arrival_time, st.departure_time, "
            "       s.lat, s.lon, s.name "
            "FROM stop_times st JOIN stops s ON s.stop_id = st.stop_id "
            "WHERE st.trip_id = ? ORDER BY st.stop_sequence",
            (trip_id,),
        ).fetchall()

        board_idx = next((i for i, r in enumerate(stop_rows) if r["stop_id"] == board_stop_id), None)
        alight_idx = next((i for i, r in enumerate(stop_rows) if r["stop_id"] == alight_stop_id), None)
        if board_idx is None or alight_idx is None or alight_idx <= board_idx:
            raise ValueError("Stops not found on this trip in the right order")

        sliced_stops = stop_rows[board_idx:alight_idx + 1]

        # Build coordinate sequence: prefer shape if available
        if shape_id:
            shape_pts = self._conn.execute(
                "SELECT lat, lon FROM shapes WHERE shape_id = ? ORDER BY sequence",
                (shape_id,),
            ).fetchall()
            if shape_pts:
                coords = self._slice_shape_between_stops(
                    [(p["lat"], p["lon"]) for p in shape_pts],
                    (sliced_stops[0]["lat"], sliced_stops[0]["lon"]),
                    (sliced_stops[-1]["lat"], sliced_stops[-1]["lon"]),
                )
            else:
                coords = [(r["lat"], r["lon"]) for r in sliced_stops]
        else:
            coords = [(r["lat"], r["lon"]) for r in sliced_stops]

        # Timestamps: use real stop times anchored to the sliced stops, interpolate by distance between them
        anchor_times: list[datetime | None] = []
        anchor_indices: list[int] = []
        for i, stop in enumerate(sliced_stops):
            time_str = stop["departure_time"] if i == 0 else stop["arrival_time"]
            dt = self._gtfs_time_to_datetime(time_str, date_, agency_tz)
            anchor_times.append(dt)
            # snap each anchor stop to the closest point in coords
            target = (stop["lat"], stop["lon"])
            anchor_indices.append(_nearest_index(coords, target))

        # Force first/last anchor to the endpoints
        anchor_indices[0] = 0
        anchor_indices[-1] = len(coords) - 1

        timestamps = _interpolate_timestamps(coords, anchor_indices, anchor_times)

        return [
            RoutePoint(lat=lat, lon=lon, timestamp=ts)
            for (lat, lon), ts in zip(coords, timestamps)
        ]

    # --- internal helpers ------------------------------------------------

    def _active_service_ids(self, date_: date) -> list[str]:
        date_str = date_.strftime("%Y%m%d")
        weekday = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][date_.weekday()]
        rows = self._conn.execute(
            f"SELECT service_id FROM calendar "
            f"WHERE {weekday} = 1 AND start_date <= ? AND end_date >= ?",
            (date_str, date_str),
        ).fetchall()
        active = {r["service_id"] for r in rows}
        # Apply exceptions: 1 = added, 2 = removed
        for r in self._conn.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?",
            (date_str,),
        ):
            if r["exception_type"] == 1:
                active.add(r["service_id"])
            elif r["exception_type"] == 2:
                active.discard(r["service_id"])
        return list(active)

    def _gtfs_time_to_datetime(self, time_str: str | None, date_: date, agency_tz: str | None) -> datetime | None:
        if not time_str:
            return None
        try:
            h, m, s = (int(p) for p in time_str.split(":"))
        except ValueError:
            return None
        tz = pytz.timezone(agency_tz or "UTC")
        # GTFS allows >24h for overnight; spill into next day
        base = tz.localize(datetime.combine(date_, time(0, 0)))
        return base + timedelta(hours=h, minutes=m, seconds=s)

    @staticmethod
    def _slice_shape_between_stops(
        shape: list[tuple[float, float]],
        board: tuple[float, float],
        alight: tuple[float, float],
    ) -> list[tuple[float, float]]:
        if len(shape) < 2:
            return shape
        i_board = _nearest_index(shape, board)
        i_alight = _nearest_index(shape, alight)
        if i_alight <= i_board:
            i_board, i_alight = min(i_board, i_alight), max(i_board, i_alight)
        return shape[i_board:i_alight + 1]


def _nearest_index(points: list[tuple[float, float]], target: tuple[float, float]) -> int:
    best_i = 0
    best_d = float("inf")
    target_pt = RoutePoint(lat=target[0], lon=target[1], timestamp=datetime.utcnow())
    for i, (lat, lon) in enumerate(points):
        d = haversine_m(target_pt, RoutePoint(lat=lat, lon=lon, timestamp=target_pt.timestamp))
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _interpolate_timestamps(
    coords: list[tuple[float, float]],
    anchor_indices: list[int],
    anchor_times: list[datetime | None],
) -> list[datetime]:
    """Linearly interpolate timestamps along coords using anchor stops as fixed points."""
    if not coords:
        return []
    # Cumulative distance along coords
    cum = [0.0]
    base_ts = next((t for t in anchor_times if t is not None), datetime.utcnow())
    prev_pt = RoutePoint(lat=coords[0][0], lon=coords[0][1], timestamp=base_ts)
    for lat, lon in coords[1:]:
        curr = RoutePoint(lat=lat, lon=lon, timestamp=base_ts)
        cum.append(cum[-1] + haversine_m(prev_pt, curr))
        prev_pt = curr

    # Build piecewise segments between consecutive valid anchors
    valid = [(i, t) for i, t in zip(anchor_indices, anchor_times) if t is not None]
    if len(valid) < 2:
        # Fall back to evenly distributed times around the single anchor (or now)
        if not valid:
            return [base_ts] * len(coords)
        return [valid[0][1]] * len(coords)

    timestamps: list[datetime | None] = [None] * len(coords)
    # Set known anchors
    for i, t in valid:
        timestamps[i] = t

    # Interpolate between each pair of consecutive valid anchors
    for (ia, ta), (ib, tb) in zip(valid, valid[1:]):
        if ib <= ia:
            continue
        d_total = max(cum[ib] - cum[ia], 1e-6)
        seconds_total = (tb - ta).total_seconds()
        for k in range(ia + 1, ib):
            frac = (cum[k] - cum[ia]) / d_total
            timestamps[k] = ta + timedelta(seconds=frac * seconds_total)

    # Extrapolate before first / after last anchor using nearest segment speed
    first_idx, first_t = valid[0]
    last_idx, last_t = valid[-1]
    if first_idx > 0 and len(valid) >= 2:
        ia, ta = valid[0]
        ib, tb = valid[1]
        d_total = max(cum[ib] - cum[ia], 1e-6)
        seconds_total = (tb - ta).total_seconds()
        for k in range(0, first_idx):
            frac = (cum[k] - cum[ia]) / d_total
            timestamps[k] = ta + timedelta(seconds=frac * seconds_total)
    if last_idx < len(coords) - 1 and len(valid) >= 2:
        ia, ta = valid[-2]
        ib, tb = valid[-1]
        d_total = max(cum[ib] - cum[ia], 1e-6)
        seconds_total = (tb - ta).total_seconds()
        for k in range(last_idx + 1, len(coords)):
            frac = (cum[k] - cum[ia]) / d_total
            timestamps[k] = ta + timedelta(seconds=frac * seconds_total)

    # Final pass: any None defaults to base_ts
    return [t if t is not None else base_ts for t in timestamps]
