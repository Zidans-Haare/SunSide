"""
Tiny SQLite-backed HTTP cache for the desktop providers.

We hit OSM Overpass, Nominatim, OSRM and Open-Meteo from the same machine
many times in a row when iterating on a route. Without caching, repeated
runs get rate-limited or silently throttled. This wraps ``requests.get`` /
``requests.post`` with a content-keyed cache that survives across runs.

Pyodide / browser flow does not touch this module.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

import requests

DEFAULT_CACHE_PATH = Path("data/http_cache.sqlite")
DEFAULT_TTL_SECONDS = 7 * 24 * 3600  # one week

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CACHE_PATH: Path | None = None


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS http_cache (
            key TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            status INTEGER NOT NULL,
            body BLOB NOT NULL,
            content_type TEXT,
            stored_at INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _get_conn(path: Path | None = None) -> sqlite3.Connection:
    global _CONN, _CACHE_PATH
    target = path or DEFAULT_CACHE_PATH
    with _LOCK:
        if _CONN is None or _CACHE_PATH != target:
            _CONN = _connect(target)
            _CACHE_PATH = target
        return _CONN


def _key(method: str, url: str, params=None, data=None, json_body=None) -> str:
    payload = json.dumps(
        {
            "method": method.upper(),
            "url": url,
            "params": params,
            "data": data,
            "json": json_body,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_request(
    method: str,
    url: str,
    *,
    params=None,
    data=None,
    json_body=None,
    headers=None,
    timeout: float = 30.0,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    cache_path: Path | None = None,
) -> requests.Response:
    """Make an HTTP request, cached on disk. Only 200 responses are cached."""
    key = _key(method, url, params=params, data=data, json_body=json_body)
    conn = _get_conn(cache_path)

    with _LOCK:
        row = conn.execute(
            "SELECT status, body, content_type, stored_at FROM http_cache WHERE key = ?",
            (key,),
        ).fetchone()

    now = int(time.time())
    if row is not None:
        status, body, content_type, stored_at = row
        if now - stored_at < ttl_seconds and status == 200:
            return _build_response(status, body, content_type, url)

    response = requests.request(
        method.upper(),
        url,
        params=params,
        data=data,
        json=json_body,
        headers=headers,
        timeout=timeout,
    )

    if response.status_code == 200:
        with _LOCK:
            conn.execute(
                "INSERT OR REPLACE INTO http_cache "
                "(key, url, status, body, content_type, stored_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    url,
                    response.status_code,
                    response.content,
                    response.headers.get("Content-Type"),
                    now,
                ),
            )
            conn.commit()

    return response


def _build_response(status: int, body: bytes, content_type: str | None, url: str) -> requests.Response:
    fake = requests.Response()
    fake.status_code = status
    fake._content = body
    fake.url = url
    if content_type:
        fake.headers["Content-Type"] = content_type
    return fake


def cache_stats(cache_path: Path | None = None) -> dict:
    conn = _get_conn(cache_path)
    with _LOCK:
        rows = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM http_cache"
        ).fetchone()
    count, size = rows
    return {"entries": count, "bytes": size}


def clear_cache(cache_path: Path | None = None) -> int:
    conn = _get_conn(cache_path)
    with _LOCK:
        cur = conn.execute("DELETE FROM http_cache")
        conn.commit()
    return cur.rowcount
