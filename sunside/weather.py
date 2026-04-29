"""Weather data helpers for weighting direct sunshine along a route."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sunside.models import RoutePoint


@dataclass(frozen=True)
class WeatherSample:
    cloud_cover_pct: float | None = None

    @property
    def sun_factor(self) -> float:
        if self.cloud_cover_pct is None:
            return 1.0
        return max(0.0, min(1.0, 1.0 - self.cloud_cover_pct / 100.0))


class OpenMeteoWeatherProvider:
    """Fetch hourly cloud cover from Open-Meteo and cache by rough place/hour."""

    endpoint = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, timeout_s: int = 10):
        self.timeout_s = timeout_s
        self._cache: dict[tuple[float, float, str], WeatherSample] = {}
        self.warning: str | None = None

    def get_weather(self, point: RoutePoint) -> WeatherSample:
        timestamp_utc = point.timestamp.astimezone(timezone.utc)
        hour_key = timestamp_utc.replace(minute=0, second=0, microsecond=0)
        key = (round(point.lat, 1), round(point.lon, 1), hour_key.isoformat())
        if key not in self._cache:
            self._cache[key] = self._fetch_hour(point, hour_key)
        return self._cache[key]

    def _fetch_hour(self, point: RoutePoint, hour_key) -> WeatherSample:
        import requests  # lazy: not available in Pyodide unless explicitly loaded
        from sunside.http_cache import cached_request
        try:
            response = cached_request(
                "GET",
                self.endpoint,
                params={
                    "latitude": point.lat,
                    "longitude": point.lon,
                    "hourly": "cloud_cover",
                    "timezone": "UTC",
                    "start_date": hour_key.date().isoformat(),
                    "end_date": hour_key.date().isoformat(),
                },
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            hourly = response.json().get("hourly", {})
            times = hourly.get("time", [])
            cloud_cover = hourly.get("cloud_cover", [])
            target = hour_key.strftime("%Y-%m-%dT%H:00")
            if target in times:
                index = times.index(target)
            else:
                index = _nearest_hour_index(times, target)
            value = cloud_cover[index]
            return WeatherSample(cloud_cover_pct=float(value))
        except Exception as exc:
            self.warning = (
                "Wetterdaten konnten nicht geladen werden; die Empfehlung nutzt nur den Sonnenstand. "
                f"Details: {exc}"
            )
            return WeatherSample()


def _nearest_hour_index(times: list[str], target: str) -> int:
    if not times:
        raise ValueError("keine Wetterstunden im Open-Meteo-Ergebnis")
    target_dt = datetime.fromisoformat(target)
    return min(
        range(len(times)),
        key=lambda index: abs((datetime.fromisoformat(times[index]) - target_dt).total_seconds()),
    )