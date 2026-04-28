"""
GPX file provider: load a saved route from a .gpx file.
Use this for manually entered routes (Flixbus lines, exotic routes, etc.).
GPX files go into data/routes/ and can be committed to the repo.
"""
from datetime import datetime, timedelta
from pathlib import Path

import gpxpy

from sunside.models import RoutePoint
from sunside.route_providers.base import RouteProvider


class GpxProvider(RouteProvider):
    """Load route geometry from a local GPX file."""

    def __init__(self, gpx_path: str | Path):
        self.gpx_path = Path(gpx_path)

    @property
    def name(self) -> str:
        return f"GPX: {self.gpx_path.stem}"

    def get_route(
        self,
        origin: str,
        destination: str,
        departure: datetime,
    ) -> list[RoutePoint]:
        with open(self.gpx_path) as f:
            gpx = gpxpy.parse(f)

        raw_points: list[tuple[float, float]] = []
        for track in gpx.tracks:
            for segment in track.segments:
                for pt in segment.points:
                    raw_points.append((pt.latitude, pt.longitude))
        for route in gpx.routes:
            for pt in route.points:
                raw_points.append((pt.latitude, pt.longitude))

        if not raw_points:
            raise ValueError(f"Keine Trackpunkte in {self.gpx_path}")

        # Estimate total distance to interpolate timestamps
        # (GPX files usually don't have timestamps for bus/train routes)
        from sunside.sun_analysis.calculator import haversine_m

        total_m = sum(
            haversine_m(
                RoutePoint(lat=raw_points[i][0], lon=raw_points[i][1], timestamp=departure),
                RoutePoint(lat=raw_points[i+1][0], lon=raw_points[i+1][1], timestamp=departure),
            )
            for i in range(len(raw_points) - 1)
        )

        # Assume average speed of 80 km/h if no duration known
        travel_seconds = (total_m / 1000) / 80 * 3600
        accumulated_m = 0.0
        result = []

        for i, (lat, lon) in enumerate(raw_points):
            if i == 0:
                ts = departure
            else:
                prev = RoutePoint(lat=raw_points[i-1][0], lon=raw_points[i-1][1], timestamp=departure)
                curr = RoutePoint(lat=lat, lon=lon, timestamp=departure)
                accumulated_m += haversine_m(prev, curr)
                frac = accumulated_m / total_m if total_m > 0 else 0
                ts = departure + timedelta(seconds=frac * travel_seconds)
            result.append(RoutePoint(lat=lat, lon=lon, timestamp=ts))

        return result
