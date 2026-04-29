"""
OSM Overpass provider: fetch railway track geometry between two places.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from heapq import heappop, heappush
from math import inf

import requests

from sunside.http_cache import cached_request
from sunside.models import RoutePoint
from sunside.route_providers.base import RouteProvider
from sunside.route_providers.nominatim import geocode_place
from sunside.sun_analysis.calculator import haversine_m


OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
RAILWAY_TYPES = "rail|light_rail|subway|tram"


def _route_point(lat: float, lon: float, timestamp: datetime) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, timestamp=timestamp)


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    now = datetime.now()
    return haversine_m(
        _route_point(a[0], a[1], now),
        _route_point(b[0], b[1], now),
    )


class OsmRailProvider(RouteProvider):
    """Real railway track geometry from OpenStreetMap Overpass API."""

    def __init__(
        self,
        *,
        buffer_deg: float = 0.25,
        timeout_s: int = 40,
        max_bbox_area_deg2: float = 3.0,
        default_speed_kmh: float = 90.0,
    ):
        self.buffer_deg = buffer_deg
        self.timeout_s = timeout_s
        self.max_bbox_area_deg2 = max_bbox_area_deg2
        self.default_speed_kmh = default_speed_kmh

    @property
    def name(self) -> str:
        return "OSM Overpass (Gleisgeometrie)"

    def get_route(
        self,
        origin: str,
        destination: str,
        departure: datetime,
        travel_hours: float | None = None,
    ) -> list[RoutePoint]:
        start = geocode_place(origin)
        end = geocode_place(destination)
        return self.get_route_between_coordinates(start, end, departure, travel_hours=travel_hours)

    def get_route_between_coordinates(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        departure: datetime,
        travel_hours: float | None = None,
    ) -> list[RoutePoint]:
        nodes, graph, tunnel_nodes = self._fetch_rail_graph(start, end)

        start_node = self._nearest_node(nodes, start)
        end_node = self._nearest_node(nodes, end)
        path = self._shortest_path(graph, start_node, end_node)

        if len(path) < 2:
            raise ValueError("Keine nutzbare OSM-Gleisroute gefunden")

        return self._path_to_route_points(nodes, path, departure, travel_hours, tunnel_nodes)

    def _fetch_rail_graph(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> tuple[dict[int, tuple[float, float]], dict[int, list[tuple[int, float]]], set[int]]:
        south, west, north, east = self._bbox(start, end)
        bbox_area = (north - south) * (east - west)
        if bbox_area > self.max_bbox_area_deg2:
            raise ValueError(
                "OSM-Gleisrouting ist aktuell nur fuer kurze oder regionale Strecken geeignet. "
                "Bitte Start/Ziel genauer als Bahnhof eingeben oder fuer lange Strecken GPX nutzen."
            )

        query = f"""
        [out:json][timeout:{self.timeout_s}];
        (
          way["railway"~"^({RAILWAY_TYPES})$"]({south},{west},{north},{east});
        );
        (._;>;);
        out body;
        """
        payload = self._run_overpass_query(query)

        nodes: dict[int, tuple[float, float]] = {}
        ways: list[tuple[list[int], bool]] = []

        for element in payload.get("elements", []):
            if element.get("type") == "node":
                nodes[element["id"]] = (float(element["lat"]), float(element["lon"]))
            elif element.get("type") == "way":
                refs = [ref for ref in element.get("nodes", []) if isinstance(ref, int)]
                if len(refs) >= 2:
                    tags = element.get("tags", {}) or {}
                    is_tunnel = (
                        tags.get("tunnel") in {"yes", "building_passage", "culvert", "avalanche_protector"}
                        or tags.get("covered") == "yes"
                        or tags.get("location") == "underground"
                    )
                    ways.append((refs, is_tunnel))

        graph: dict[int, list[tuple[int, float]]] = {}
        tunnel_nodes: set[int] = set()
        for way, is_tunnel in ways:
            for current, next_node in zip(way, way[1:]):
                if current not in nodes or next_node not in nodes:
                    continue
                distance = _distance_m(nodes[current], nodes[next_node])
                graph.setdefault(current, []).append((next_node, distance))
                graph.setdefault(next_node, []).append((current, distance))
                if is_tunnel:
                    tunnel_nodes.add(current)
                    tunnel_nodes.add(next_node)

        if not graph:
            raise ValueError("Keine OSM-Gleisdaten im Suchbereich gefunden")

        connected_nodes = {node_id: nodes[node_id] for node_id in graph}
        return connected_nodes, graph, tunnel_nodes

    def _run_overpass_query(self, query: str) -> dict:
        last_error = None
        for url in OVERPASS_URLS:
            try:
                response = cached_request(
                    "POST",
                    url,
                    data={"data": query},
                    headers={"User-Agent": "SunSide/1.0"},
                    timeout=self.timeout_s + 10,
                )
                response.raise_for_status()
                return response.json()
            except requests.HTTPError as exc:
                last_error = exc
                if exc.response is not None and exc.response.status_code not in {429, 502, 503, 504}:
                    break
            except requests.RequestException as exc:
                last_error = exc

        raise ValueError(
            "OSM/Overpass ist gerade ueberlastet oder die Abfrage ist zu gross. "
            "Bitte spaeter erneut versuchen, Start/Ziel genauer als Bahnhof eingeben "
            "oder GPX/Luftlinie nutzen."
        ) from last_error

    def _bbox(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> tuple[float, float, float, float]:
        south = min(start[0], end[0]) - self.buffer_deg
        north = max(start[0], end[0]) + self.buffer_deg
        west = min(start[1], end[1]) - self.buffer_deg
        east = max(start[1], end[1]) + self.buffer_deg
        return south, west, north, east

    def _nearest_node(
        self,
        nodes: dict[int, tuple[float, float]],
        target: tuple[float, float],
    ) -> int:
        return min(nodes, key=lambda node_id: _distance_m(nodes[node_id], target))

    def _shortest_path(
        self,
        graph: dict[int, list[tuple[int, float]]],
        start_node: int,
        end_node: int,
    ) -> list[int]:
        queue: list[tuple[float, int]] = [(0.0, start_node)]
        distances = {start_node: 0.0}
        previous: dict[int, int] = {}
        visited: set[int] = set()

        while queue:
            current_distance, current = heappop(queue)
            if current in visited:
                continue
            visited.add(current)

            if current == end_node:
                break

            for neighbor, edge_distance in graph.get(current, []):
                candidate = current_distance + edge_distance
                if candidate < distances.get(neighbor, inf):
                    distances[neighbor] = candidate
                    previous[neighbor] = current
                    heappush(queue, (candidate, neighbor))

        if end_node not in distances:
            raise ValueError("Start und Ziel liegen in getrennten OSM-Gleisnetzen")

        path = [end_node]
        while path[-1] != start_node:
            path.append(previous[path[-1]])
        path.reverse()
        return path

    def _path_to_route_points(
        self,
        nodes: dict[int, tuple[float, float]],
        path: list[int],
        departure: datetime,
        travel_hours: float | None,
        tunnel_nodes: set[int] | None = None,
    ) -> list[RoutePoint]:
        tunnel_nodes = tunnel_nodes or set()
        coordinates = [nodes[node_id] for node_id in path]
        segment_lengths = [
            _distance_m(coordinates[i], coordinates[i + 1])
            for i in range(len(coordinates) - 1)
        ]
        total_m = sum(segment_lengths)
        if total_m <= 0:
            raise ValueError("OSM-Gleisroute hat keine Laenge")

        if travel_hours is None:
            travel_seconds = (total_m / 1000) / self.default_speed_kmh * 3600
        else:
            travel_seconds = travel_hours * 3600

        accumulated_m = 0.0
        points = []
        for index, node_id in enumerate(path):
            lat, lon = nodes[node_id]
            if index > 0:
                accumulated_m += segment_lengths[index - 1]
            fraction = accumulated_m / total_m
            timestamp = departure + timedelta(seconds=fraction * travel_seconds)
            points.append(RoutePoint(
                lat=lat,
                lon=lon,
                timestamp=timestamp,
                in_tunnel=node_id in tunnel_nodes,
            ))

        return points
