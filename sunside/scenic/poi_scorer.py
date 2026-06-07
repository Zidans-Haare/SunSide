from __future__ import annotations

from math import log10

from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import bearing, haversine_m
from sunside.scenic.models import ScenicPOI


BASE_WEIGHTS: dict[str, float] = {
    "peak":      0.8,
    "glacier":   0.9,
    "water":     0.7,
    "coastline": 0.8,
    "river":     0.3,
    "castle":    0.5,
    "ruins":     0.4,
    "heritage":  0.9,
    "city":      0.6,
    "viewpoint": 0.4,
}


def assign_and_score(
    raw_pois: list[dict],
    points: list[RoutePoint],
    bearings: list[float],
    max_distance_m: float,
) -> list[ScenicPOI]:
    if len(points) < 2 or not raw_pois:
        return []

    scored: list[ScenicPOI] = []
    for rp in raw_pois:
        poi_point = RoutePoint(lat=rp["lat"], lon=rp["lon"], timestamp=points[0].timestamp)

        best_idx = 0
        best_dist = haversine_m(points[0], poi_point)
        for i in range(1, len(points)):
            d = haversine_m(points[i], poi_point)
            if d < best_dist:
                best_dist = d
                best_idx = i

        if best_dist > max_distance_m:
            continue

        seg_idx = min(best_idx, len(bearings) - 1)
        seg_bearing = bearings[seg_idx]

        brng_to_poi = bearing(points[best_idx], poi_point)
        delta = (brng_to_poi - seg_bearing) % 360
        side = "rechts" if delta < 180 else "links"

        weight = _compute_weight(rp, best_dist, max_distance_m)
        if weight <= 0:
            continue

        scored.append(ScenicPOI(
            lat=rp["lat"],
            lon=rp["lon"],
            osm_id=rp.get("osm_id", 0),
            category=rp["category"],
            name=rp.get("name"),
            weight=round(weight, 4),
            distance_m=round(best_dist, 1),
            side=side,
            segment_index=seg_idx,
            tags=rp.get("tags", {}),
        ))

    return scored


def _compute_weight(raw: dict, distance_m: float, max_distance_m: float) -> float:
    category = raw["category"]
    base = BASE_WEIGHTS.get(category, 0.3)
    tags = raw.get("tags", {})

    decay = max(0.0, 1.0 - distance_m / max_distance_m)

    prominence = 1.0
    if category == "peak":
        try:
            ele = float(str(tags.get("ele", "0")).replace(",", ".").split(";")[0])
            prominence = min(1.0, ele / 3000)
        except (ValueError, TypeError):
            prominence = 0.3
    elif category == "city":
        try:
            pop = int(str(tags.get("population", "0")).replace(",", "").replace(".", ""))
            prominence = min(1.0, log10(max(pop, 1)) / 6)
        except (ValueError, TypeError):
            prominence = 0.3

    if raw.get("name"):
        prominence *= 1.2

    return base * decay * prominence
