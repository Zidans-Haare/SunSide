from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScenicPOI:
    lat: float
    lon: float
    osm_id: int
    category: str
    name: str | None
    weight: float
    distance_m: float
    side: str              # "links" | "rechts"
    segment_index: int
    tags: dict = field(default_factory=dict)


@dataclass
class ScenicSegment:
    segment_index: int
    left_score: float
    right_score: float
    pois: list[ScenicPOI] = field(default_factory=list)


@dataclass
class ScenicRecommendation:
    scenic_side: str           # "links" | "rechts"
    other_side: str
    scenic_pct: float
    other_pct: float
    total_left_score: float
    total_right_score: float
    top_pois: list[ScenicPOI] = field(default_factory=list)
    poi_count: int = 0
    segments: list[ScenicSegment] = field(default_factory=list)
    is_flight: bool = False
