from __future__ import annotations

from sunside.models import RoutePoint
from sunside.sun_analysis.calculator import bearing as calc_bearing
from sunside.scenic.models import ScenicPOI, ScenicSegment, ScenicRecommendation
from sunside.scenic.poi_scorer import assign_and_score

TRAIN_BUFFER_M = 5_000
FLIGHT_BUFFER_M = 80_000


def analyze_scenic(
    points: list[RoutePoint],
    raw_pois: list[dict],
    is_flight: bool = False,
) -> ScenicRecommendation:
    if len(points) < 2:
        return _empty(is_flight)

    max_dist = FLIGHT_BUFFER_M if is_flight else TRAIN_BUFFER_M

    bearings = [
        calc_bearing(points[i], points[i + 1])
        for i in range(len(points) - 1)
    ]

    scored_pois = assign_and_score(raw_pois, points, bearings, max_dist)

    if not scored_pois:
        return _empty(is_flight)

    seg_map: dict[int, ScenicSegment] = {}
    for poi in scored_pois:
        seg = seg_map.setdefault(
            poi.segment_index,
            ScenicSegment(segment_index=poi.segment_index, left_score=0.0, right_score=0.0),
        )
        if poi.side == "links":
            seg.left_score += poi.weight
        else:
            seg.right_score += poi.weight
        seg.pois.append(poi)

    segments = sorted(seg_map.values(), key=lambda s: s.segment_index)

    total_left = sum(s.left_score for s in segments)
    total_right = sum(s.right_score for s in segments)
    total = total_left + total_right

    if total <= 0:
        return _empty(is_flight)

    left_pct = total_left / total * 100
    right_pct = total_right / total * 100

    if total_left >= total_right:
        scenic_side = "links"
        other_side = "rechts"
        scenic_pct = left_pct
        other_pct = right_pct
    else:
        scenic_side = "rechts"
        other_side = "links"
        scenic_pct = right_pct
        other_pct = left_pct

    top_pois = sorted(scored_pois, key=lambda p: p.weight, reverse=True)[:10]

    return ScenicRecommendation(
        scenic_side=scenic_side,
        other_side=other_side,
        scenic_pct=round(scenic_pct, 1),
        other_pct=round(other_pct, 1),
        total_left_score=round(total_left, 4),
        total_right_score=round(total_right, 4),
        top_pois=top_pois,
        poi_count=len(scored_pois),
        segments=segments,
        is_flight=is_flight,
    )


def _empty(is_flight: bool) -> ScenicRecommendation:
    return ScenicRecommendation(
        scenic_side="links",
        other_side="rechts",
        scenic_pct=50.0,
        other_pct=50.0,
        total_left_score=0.0,
        total_right_score=0.0,
        poi_count=0,
        is_flight=is_flight,
    )
