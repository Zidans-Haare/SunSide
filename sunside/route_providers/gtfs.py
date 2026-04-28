"""
GTFS provider: load route geometry from a GTFS feed (DB, Flixbus, ÖBB, etc.).

GTFS feeds to download:
  - Deutsche Bahn:  https://data.deutschebahn.com/dataset/fahrplandaten-open-data (GTFS)
  - Flixbus:        available via Mobility Database / transitfeeds.com
  - ÖBB:            https://www.data.gv.at (GTFS Austria)

A GTFS feed contains:
  stops.txt       → station names + coordinates
  routes.txt      → route IDs
  trips.txt       → which trips belong to which route
  stop_times.txt  → stop sequence + arrival/departure times per trip

TODO: implement
  1. Parse GTFS zip (or cached directory)
  2. Find trips matching origin → destination
  3. Extract stop sequence with coordinates and scheduled times
  4. Interpolate points between stops (straight line or via OSM)
"""
from datetime import datetime

from sunside.models import RoutePoint
from sunside.route_providers.base import RouteProvider


class GtfsProvider(RouteProvider):
    """Route from a local GTFS feed."""

    def __init__(self, gtfs_path: str):
        self.gtfs_path = gtfs_path

    @property
    def name(self) -> str:
        return f"GTFS: {self.gtfs_path}"

    def get_route(
        self,
        origin: str,
        destination: str,
        departure: datetime,
    ) -> list[RoutePoint]:
        raise NotImplementedError("GTFS provider — noch nicht implementiert")
