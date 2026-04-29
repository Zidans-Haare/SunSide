"""Terrain shading: query elevations along a sun ray, compute horizon angle.

Uses the Open-Meteo Elevation API (https://api.open-meteo.com/v1/elevation),
which is free, key-less and accepts up to 100 coordinates per call. Results
are cached via :mod:`sunside.http_cache` so a route is only fetched once.

A point is considered shaded by terrain when, looking from the point towards
the sun's azimuth, any sample along the ray within ``max_distance_m`` rises
high enough that the apparent horizon angle exceeds the sun's elevation.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, degrees, radians, sin

import requests

from sunside.http_cache import cached_request

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
EARTH_R_M = 6_371_000.0


@dataclass(frozen=True)
class TerrainConfig:
    enabled: bool = False
    max_distance_m: float = 8_000.0   # how far down the sun ray to look
    sample_step_m: float = 500.0      # spacing between ray samples
    elevation_margin_deg: float = 0.5 # require horizon to clearly exceed sun


class TerrainProvider:
    """Batched, cached elevation lookup with a horizon-shading query."""

    def __init__(self, config: TerrainConfig | None = None, *, batch_size: int = 100):
        self.config = config or TerrainConfig()
        self.batch_size = batch_size
        self._cache: dict[tuple[float, float], float] = {}
        self.warning: str | None = None

    # --- elevation lookup ------------------------------------------------

    def get_elevations(self, coords: list[tuple[float, float]]) -> list[float | None]:
        if not coords:
            return []

        # round to ~10m grid to enable cache reuse across nearby samples
        rounded = [(round(lat, 4), round(lon, 4)) for lat, lon in coords]

        missing = [c for c in set(rounded) if c not in self._cache]
        for chunk in _chunks(missing, self.batch_size):
            try:
                response = cached_request(
                    "GET",
                    ELEVATION_URL,
                    params={
                        "latitude": ",".join(str(c[0]) for c in chunk),
                        "longitude": ",".join(str(c[1]) for c in chunk),
                    },
                    timeout=20,
                )
                response.raise_for_status()
                data = response.json()
                values = data.get("elevation") or []
                for coord, elev in zip(chunk, values):
                    self._cache[coord] = float(elev) if elev is not None else None
            except (requests.RequestException, ValueError) as exc:
                self.warning = (
                    "Hoehenabfrage fehlgeschlagen — Gelaendeschatten ignoriert "
                    f"({exc.__class__.__name__})."
                )
                for coord in chunk:
                    self._cache.setdefault(coord, None)

        return [self._cache.get(c) for c in rounded]

    # --- horizon test ----------------------------------------------------

    def is_shaded(self, lat: float, lon: float, sun_azimuth_deg: float, sun_elevation_deg: float) -> bool:
        """True if any sample along the sun ray rises above the sun's elevation."""
        if not self.config.enabled or sun_elevation_deg <= 0:
            return False

        anchor = self.get_elevations([(lat, lon)])[0]
        if anchor is None:
            return False

        samples = self._ray_samples(lat, lon, sun_azimuth_deg)
        if not samples:
            return False

        sample_elevs = self.get_elevations([(s_lat, s_lon) for s_lat, s_lon, _ in samples])

        for (_, _, distance_m), elev in zip(samples, sample_elevs):
            if elev is None or distance_m <= 0:
                continue
            horizon_angle = degrees(atan2(elev - anchor, distance_m))
            if horizon_angle > sun_elevation_deg + self.config.elevation_margin_deg:
                return True
        return False

    def _ray_samples(self, lat: float, lon: float, azimuth_deg: float) -> list[tuple[float, float, float]]:
        cfg = self.config
        n = int(cfg.max_distance_m // cfg.sample_step_m)
        if n <= 0:
            return []

        samples = []
        az = radians(azimuth_deg)
        for i in range(1, n + 1):
            distance = i * cfg.sample_step_m
            d_over_r = distance / EARTH_R_M
            lat1 = radians(lat)
            lon1 = radians(lon)
            lat2 = (lat1 + d_over_r * cos(az))
            lon2 = (lon1 + d_over_r * sin(az) / max(cos(lat1), 1e-9))
            samples.append((degrees(lat2), degrees(lon2), distance))
        return samples


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]
