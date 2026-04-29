"""Smoke test: Villach Hbf -> Klagenfurt Hbf via OSM rail provider."""
from datetime import datetime
import pytz

from sunside.route_providers.osm import OsmRailProvider
from sunside.sun_analysis.analyzer import analyze


def main():
    tz = pytz.timezone("Europe/Vienna")
    departure = tz.localize(datetime(2026, 4, 29, 14, 0))

    provider = OsmRailProvider()
    print(f"Provider: {provider.name}")
    print(f"Abfahrt: {departure.isoformat()}")

    points = provider.get_route("Villach Hauptbahnhof", "Klagenfurt Hauptbahnhof", departure)
    print(f"Route-Punkte: {len(points)}")
    print(f"Start: {points[0].lat:.5f},{points[0].lon:.5f} @ {points[0].timestamp}")
    print(f"Ziel : {points[-1].lat:.5f},{points[-1].lon:.5f} @ {points[-1].timestamp}")

    rec = analyze(points)
    print()
    print(f"Auto-Intervall: {rec.auto_interval_m} m")
    print(f"Segmente: {len(rec.segments)}")
    print(f"Nacht? {rec.is_night}")
    print(f"Sonne ist auf: {rec.sun_side} ({rec.sun_pct:.1f} %)")
    print(f"Empfehlung Schattenseite: {rec.shade_side}")


if __name__ == "__main__":
    main()
