from __future__ import annotations

from math import cos, radians

from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import haversine_m


CATEGORY_TAGS = {
    "peak":      'node["natural"="peak"]["ele"]',
    "glacier":   'nwr["natural"="glacier"]',
    "water":     'nwr["natural"="water"]["name"]',
    "coastline": 'way["natural"="coastline"]',
    "river":     'way["waterway"="river"]["name"]',
    "castle":    'nwr["historic"~"castle|fort"]',
    "ruins":     'nwr["historic"="ruins"]',
    "heritage":  'nwr["heritage:operator"="whc"]',
    "city":      'node["place"="city"]',
    "viewpoint": 'node["tourism"="viewpoint"]',
}

FLIGHT_CATEGORIES = {"peak", "glacier", "water", "coastline", "city", "heritage"}


def corridor_bboxes(
    points: list[RoutePoint],
    buffer_km: float = 5.0,
    chunk_km: float = 50.0,
) -> list[tuple[float, float, float, float]]:
    if len(points) < 2:
        return []

    bboxes: list[tuple[float, float, float, float]] = []
    chunk_m = chunk_km * 1000
    chunk_points: list[RoutePoint] = [points[0]]
    chunk_dist = 0.0

    for i in range(1, len(points)):
        d = haversine_m(points[i - 1], points[i])
        chunk_dist += d
        chunk_points.append(points[i])

        if chunk_dist >= chunk_m or i == len(points) - 1:
            bbox = _points_to_bbox(chunk_points, buffer_km)
            bboxes.append(bbox)
            chunk_points = [points[i]]
            chunk_dist = 0.0

    return bboxes


def build_overpass_query(
    bbox: tuple[float, float, float, float],
    is_flight: bool = False,
) -> str:
    s, w, n, e = bbox
    bbox_str = f"{s},{w},{n},{e}"
    cats = FLIGHT_CATEGORIES if is_flight else CATEGORY_TAGS.keys()
    stmts = [f"  {CATEGORY_TAGS[c]}({bbox_str});" for c in cats if c in CATEGORY_TAGS]
    body = "\n".join(stmts)
    return f"[out:json][timeout:25];\n(\n{body}\n);\nout center tags;"


def parse_overpass_response(data: dict) -> list[dict]:
    pois: list[dict] = []
    for el in data.get("elements", []):
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue

        tags = el.get("tags", {})
        category = _classify(tags)
        if category is None:
            continue

        pois.append({
            "lat": lat,
            "lon": lon,
            "osm_id": el.get("id", 0),
            "category": category,
            "name": tags.get("name"),
            "tags": tags,
        })
    return pois


def _classify(tags: dict) -> str | None:
    if tags.get("natural") == "peak" and "ele" in tags:
        return "peak"
    if tags.get("natural") == "glacier":
        return "glacier"
    if tags.get("natural") == "coastline":
        return "coastline"
    if tags.get("natural") == "water" and tags.get("name"):
        return "water"
    if tags.get("waterway") == "river" and tags.get("name"):
        return "river"
    if tags.get("historic") in ("castle", "fort"):
        return "castle"
    if tags.get("historic") == "ruins":
        return "ruins"
    if tags.get("heritage:operator") == "whc":
        return "heritage"
    if tags.get("place") == "city":
        return "city"
    if tags.get("tourism") == "viewpoint":
        return "viewpoint"
    return None


def _points_to_bbox(
    pts: list[RoutePoint], buffer_km: float,
) -> tuple[float, float, float, float]:
    lats = [p.lat for p in pts]
    lons = [p.lon for p in pts]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    dlat = buffer_km / 111.0
    mid_lat = (min_lat + max_lat) / 2
    dlon = buffer_km / (111.0 * max(cos(radians(mid_lat)), 0.01))

    return (
        round(min_lat - dlat, 5),
        round(min_lon - dlon, 5),
        round(max_lat + dlat, 5),
        round(max_lon + dlon, 5),
    )
