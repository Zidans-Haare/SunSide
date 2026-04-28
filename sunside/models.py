from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RoutePoint:
    lat: float
    lon: float
    timestamp: datetime


@dataclass
class SegmentAnalysis:
    point: RoutePoint
    bearing: float          # degrees, 0=North, clockwise
    sun_azimuth: float      # degrees, 0=North, clockwise
    sun_elevation: float    # degrees above horizon (negative = below = night)
    sun_side: str           # "links" | "rechts" | "night"
    cloud_cover_pct: float | None = None
    sun_factor: float = 1.0  # 1=clear direct sun, 0=clouded out or night


@dataclass
class Recommendation:
    shade_side: str             # "links" | "rechts"
    sun_side: str               # "links" | "rechts"
    shade_pct: float            # % of journey in shade on shade_side
    sun_pct: float              # % of journey in sun on sun_side
    segments: list[SegmentAnalysis] = field(default_factory=list)
    auto_interval_m: int = 0    # suggested interval that was used
    is_night: bool = False      # true if entire journey is at night
    weather_adjusted: bool = False
    mean_cloud_cover_pct: float | None = None
    low_direct_sun: bool = False
