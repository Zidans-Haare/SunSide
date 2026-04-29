from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RoutePoint:
    lat: float
    lon: float
    timestamp: datetime
    in_tunnel: bool = False


@dataclass
class SegmentAnalysis:
    point: RoutePoint
    bearing: float          # degrees, 0=North, clockwise
    sun_azimuth: float      # degrees, 0=North, clockwise
    sun_elevation: float    # degrees above horizon (negative = below = night)
    sun_side: str           # "links" | "rechts" | "night" | "tunnel"
    intensity_factor: float = 1.0  # 0..1, sin(elevation) clamped, 0 at night/tunnel
    cloud_cover_pct: float | None = None
    sun_factor: float = 1.0  # 1=clear direct sun, 0=clouded out / night / tunnel
    terrain_shaded: bool = False


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
    intensity_adjusted: bool = False
    mean_cloud_cover_pct: float | None = None
    mean_sun_elevation: float | None = None
    tunnel_pct: float = 0.0
    terrain_pct: float = 0.0
    terrain_adjusted: bool = False
    low_direct_sun: bool = False
