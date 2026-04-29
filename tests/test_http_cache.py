"""Tests for the SQLite-backed HTTP cache."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import requests

from sunside import http_cache


def _fake_response(body: bytes, status: int = 200, content_type: str = "application/json"):
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.headers["Content-Type"] = content_type
    return response


def test_cached_request_hits_network_only_once(tmp_path: Path):
    cache_path = tmp_path / "cache.sqlite"

    with patch("sunside.http_cache.requests.request") as mock_req:
        mock_req.return_value = _fake_response(b'{"ok": true}')

        a = http_cache.cached_request(
            "GET", "https://example.com/x", params={"q": 1}, cache_path=cache_path,
        )
        b = http_cache.cached_request(
            "GET", "https://example.com/x", params={"q": 1}, cache_path=cache_path,
        )

        assert a.json() == {"ok": True}
        assert b.json() == {"ok": True}
        assert mock_req.call_count == 1


def test_cached_request_does_not_cache_errors(tmp_path: Path):
    cache_path = tmp_path / "cache.sqlite"

    with patch("sunside.http_cache.requests.request") as mock_req:
        mock_req.return_value = _fake_response(b"oops", status=500)

        http_cache.cached_request("GET", "https://example.com/y", cache_path=cache_path)
        http_cache.cached_request("GET", "https://example.com/y", cache_path=cache_path)

        assert mock_req.call_count == 2


def test_clear_cache(tmp_path: Path):
    cache_path = tmp_path / "cache.sqlite"

    with patch("sunside.http_cache.requests.request") as mock_req:
        mock_req.return_value = _fake_response(b"{}")
        http_cache.cached_request("GET", "https://example.com/z", cache_path=cache_path)

    assert http_cache.cache_stats(cache_path)["entries"] == 1
    http_cache.clear_cache(cache_path)
    assert http_cache.cache_stats(cache_path)["entries"] == 0
