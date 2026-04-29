"""Smoke test: Flixbus Villach -> Padova, Friday 2026-05-01 08:55."""
from datetime import datetime
import pytz

from sunside.route_providers.osrm import OsrmRoadProvider
from sunside.sun_analysis.analyzer import analyze


def main():
    tz = pytz.timezone("Europe/Vienna")
    departure = tz.localize(datetime(2026, 5, 1, 8, 55))

    provider = OsrmRoadProvider()
    print(f"Provider: {provider.name}")
    print(f"Abfahrt: {departure.isoformat()}")

    points = provider.get_route("Villach Hauptbahnhof", "Padova", departure)
    print(f"Route-Punkte: {len(points)}")
    duration = points[-1].timestamp - points[0].timestamp
    print(f"Fahrzeit (OSRM): {duration}")
    print(f"Start: {points[0].lat:.5f},{points[0].lon:.5f} @ {points[0].timestamp}")
    print(f"Ziel : {points[-1].lat:.5f},{points[-1].lon:.5f} @ {points[-1].timestamp}")

    rec = analyze(points)
    print()
    print(f"Auto-Intervall: {rec.auto_interval_m} m")
    print(f"Segmente: {len(rec.segments)}")
    print(f"Nacht? {rec.is_night}")
    print(f"Sonne ist auf: {rec.sun_side} ({rec.sun_pct:.1f} %)")
    print(f"Empfehlung Schattenseite: {rec.shade_side}")

    left = sum(1 for s in rec.segments if s.sun_side == "links")
    right = sum(1 for s in rec.segments if s.sun_side == "rechts")
    night = sum(1 for s in rec.segments if s.sun_side == "night")
    print(f"Segmente links/rechts/nacht: {left}/{right}/{night}")


if __name__ == "__main__":
    main()
