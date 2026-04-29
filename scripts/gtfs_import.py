"""Generic GTFS -> SQLite importer.

Streams CSV rows directly from the zip, batches inserts, builds indexes at the end.
Tested with the Flixbus EU feed (~33 MB zip, ~200 MB extracted).

Usage:
    python scripts/gtfs_import.py --zip data/gtfs/flixbus.zip --db data/gtfs/flixbus.sqlite

Re-running is safe: the DB is rebuilt from scratch.
"""
from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Iterable


SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
PRAGMA temp_store = MEMORY;

DROP TABLE IF EXISTS feed_info;
DROP TABLE IF EXISTS agency;
DROP TABLE IF EXISTS routes;
DROP TABLE IF EXISTS trips;
DROP TABLE IF EXISTS stops;
DROP TABLE IF EXISTS stop_times;
DROP TABLE IF EXISTS shapes;
DROP TABLE IF EXISTS calendar;
DROP TABLE IF EXISTS calendar_dates;
DROP TABLE IF EXISTS import_meta;

CREATE TABLE feed_info (
    publisher_name TEXT,
    publisher_url  TEXT,
    feed_lang      TEXT,
    start_date     TEXT,
    end_date       TEXT
);

CREATE TABLE agency (
    agency_id  TEXT PRIMARY KEY,
    name       TEXT,
    timezone   TEXT
);

CREATE TABLE routes (
    route_id     TEXT PRIMARY KEY,
    agency_id    TEXT,
    short_name   TEXT,
    long_name    TEXT,
    type         INTEGER
);

CREATE TABLE trips (
    trip_id      TEXT PRIMARY KEY,
    route_id     TEXT,
    service_id   TEXT,
    headsign     TEXT,
    shape_id     TEXT
);

CREATE TABLE stops (
    stop_id   TEXT PRIMARY KEY,
    name      TEXT,
    lat       REAL,
    lon       REAL,
    timezone  TEXT
);

CREATE TABLE stop_times (
    trip_id        TEXT NOT NULL,
    stop_sequence  INTEGER NOT NULL,
    stop_id        TEXT NOT NULL,
    arrival_time   TEXT,
    departure_time TEXT,
    PRIMARY KEY (trip_id, stop_sequence)
);

CREATE TABLE shapes (
    shape_id  TEXT NOT NULL,
    sequence  INTEGER NOT NULL,
    lat       REAL,
    lon       REAL,
    PRIMARY KEY (shape_id, sequence)
);

CREATE TABLE calendar (
    service_id TEXT PRIMARY KEY,
    monday     INTEGER, tuesday INTEGER, wednesday INTEGER,
    thursday   INTEGER, friday INTEGER, saturday INTEGER, sunday INTEGER,
    start_date TEXT, end_date TEXT
);

CREATE TABLE calendar_dates (
    service_id     TEXT NOT NULL,
    date           TEXT NOT NULL,
    exception_type INTEGER NOT NULL,
    PRIMARY KEY (service_id, date)
);

CREATE TABLE import_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

INDEXES = [
    "CREATE INDEX idx_routes_short_name ON routes(short_name)",
    "CREATE INDEX idx_trips_route_id    ON trips(route_id)",
    "CREATE INDEX idx_trips_service_id  ON trips(service_id)",
    "CREATE INDEX idx_trips_shape_id    ON trips(shape_id)",
    "CREATE INDEX idx_stop_times_stop   ON stop_times(stop_id)",
    "CREATE INDEX idx_stop_times_trip   ON stop_times(trip_id, stop_sequence)",
    "CREATE INDEX idx_calendar_dates    ON calendar_dates(service_id, date)",
]


def open_csv(zf: zipfile.ZipFile, name: str):
    raw = zf.open(name, "r")
    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
    return csv.DictReader(text)


def batched(iterable: Iterable, size: int):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def insert(conn: sqlite3.Connection, sql: str, rows_iter, batch_size: int = 50_000):
    total = 0
    cur = conn.cursor()
    for batch in batched(rows_iter, batch_size):
        cur.executemany(sql, batch)
        total += len(batch)
    conn.commit()
    return total


def import_feed(zip_path: Path, db_path: Path, *, feed_name: str = "flixbus") -> None:
    print(f"[import] zip={zip_path} db={db_path}")
    if not zip_path.exists():
        raise FileNotFoundError(zip_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    started = time.time()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    with zipfile.ZipFile(zip_path) as zf:
        names = {n.lower(): n for n in zf.namelist()}

        # feed_info (optional)
        if "feed_info.txt" in names:
            for row in open_csv(zf, names["feed_info.txt"]):
                conn.execute(
                    "INSERT INTO feed_info VALUES (?, ?, ?, ?, ?)",
                    (
                        row.get("feed_publisher_name"),
                        row.get("feed_publisher_url"),
                        row.get("feed_lang"),
                        row.get("feed_start_date"),
                        row.get("feed_end_date"),
                    ),
                )
            conn.commit()

        n = insert(conn, "INSERT INTO agency VALUES (?, ?, ?)",
                   ((r.get("agency_id") or "_default",
                     r.get("agency_name"),
                     r.get("agency_timezone") or "UTC")
                    for r in open_csv(zf, names["agency.txt"])))
        print(f"[import] agency: {n}")

        n = insert(conn, "INSERT INTO routes VALUES (?, ?, ?, ?, ?)",
                   ((r["route_id"], r.get("agency_id") or "_default",
                     r.get("route_short_name"),
                     r.get("route_long_name"),
                     int(r["route_type"]) if (r.get("route_type") or "").strip() else None)
                    for r in open_csv(zf, names["routes.txt"])))
        print(f"[import] routes: {n}")

        n = insert(conn, "INSERT INTO trips VALUES (?, ?, ?, ?, ?)",
                   ((r["trip_id"], r.get("route_id"), r.get("service_id"),
                     r.get("trip_headsign"), r.get("shape_id"))
                    for r in open_csv(zf, names["trips.txt"])))
        print(f"[import] trips: {n}")

        n = insert(conn, "INSERT INTO stops VALUES (?, ?, ?, ?, ?)",
                   ((r["stop_id"], r.get("stop_name"),
                     float(r["stop_lat"]) if (r.get("stop_lat") or "").strip() else None,
                     float(r["stop_lon"]) if (r.get("stop_lon") or "").strip() else None,
                     r.get("stop_timezone") or None)
                    for r in open_csv(zf, names["stops.txt"])))
        print(f"[import] stops: {n}")

        n = insert(conn, "INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)",
                   ((r["trip_id"], int(r["stop_sequence"]), r["stop_id"],
                     r.get("arrival_time") or None, r.get("departure_time") or None)
                    for r in open_csv(zf, names["stop_times.txt"])))
        print(f"[import] stop_times: {n}")

        if "shapes.txt" in names:
            n = insert(conn, "INSERT INTO shapes VALUES (?, ?, ?, ?)",
                       ((r["shape_id"], int(r["shape_pt_sequence"]),
                         float(r["shape_pt_lat"]), float(r["shape_pt_lon"]))
                        for r in open_csv(zf, names["shapes.txt"])))
            print(f"[import] shapes: {n}")

        if "calendar.txt" in names:
            n = insert(conn, "INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       ((r["service_id"], int(r["monday"]), int(r["tuesday"]),
                         int(r["wednesday"]), int(r["thursday"]), int(r["friday"]),
                         int(r["saturday"]), int(r["sunday"]),
                         r["start_date"], r["end_date"])
                        for r in open_csv(zf, names["calendar.txt"])))
            print(f"[import] calendar: {n}")

        if "calendar_dates.txt" in names:
            n = insert(conn, "INSERT INTO calendar_dates VALUES (?, ?, ?)",
                       ((r["service_id"], r["date"], int(r["exception_type"]))
                        for r in open_csv(zf, names["calendar_dates.txt"])))
            print(f"[import] calendar_dates: {n}")

    print("[import] building indexes...")
    for stmt in INDEXES:
        conn.execute(stmt)

    conn.execute("INSERT OR REPLACE INTO import_meta VALUES (?, ?)",
                 ("imported_at_utc", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    conn.execute("INSERT OR REPLACE INTO import_meta VALUES (?, ?)",
                 ("source_zip", str(zip_path)))
    conn.execute("INSERT OR REPLACE INTO import_meta VALUES (?, ?)",
                 ("feed_name", feed_name))
    conn.commit()

    conn.execute("VACUUM")
    conn.close()

    elapsed = time.time() - started
    size_mb = db_path.stat().st_size / 1024 / 1024
    print(f"[import] done in {elapsed:.1f}s, db size {size_mb:.1f} MB")


def main():
    parser = argparse.ArgumentParser(description="Import GTFS feed into SQLite.")
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--name", default=None,
                        help="Friendly feed name shown in the UI (default: derived from --db)")
    args = parser.parse_args()
    feed_name = args.name or args.db.stem
    import_feed(args.zip, args.db, feed_name=feed_name)


if __name__ == "__main__":
    main()
