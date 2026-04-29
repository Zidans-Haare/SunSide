"""Smoke test: Flixbus Villach -> Padova on 2026-05-01 using GTFS data."""
from datetime import date, time

from sunside.route_providers.gtfs_db import GtfsDatabase
from sunside.sun_analysis.analyzer import analyze


def main():
    db = GtfsDatabase("data/gtfs/flixbus.sqlite")
    print("[meta]", db.feed_info())

    villach = db.search_stops("Villach")
    padova = db.search_stops("Padua")
    print(f"Villach-Stops: {len(villach)}")
    for s in villach:
        print(f"  {s.stop_id}  {s.name}  ({s.lat:.4f}, {s.lon:.4f}) tz={s.timezone}")
    print(f"Padova-Stops:  {len(padova)}")
    for s in padova:
        print(f"  {s.stop_id}  {s.name}  ({s.lat:.4f}, {s.lon:.4f}) tz={s.timezone}")

    if not villach or not padova:
        print("Suche unklar -- abbrechen.")
        return

    # Try all combinations
    travel_date = date(2026, 5, 1)
    found = []
    for v in villach:
        for p in padova:
            trips = db.find_trips(
                board_stop_id=v.stop_id,
                alight_stop_id=p.stop_id,
                date_=travel_date,
                limit=10,
            )
            for t in trips:
                found.append(t)

    print(f"\nGefundene Trips am {travel_date}: {len(found)}")
    for t in sorted(found, key=lambda x: x.board_departure):
        print(f"  {t.label}")

    if not found:
        print("Keine Trips gefunden.")
        return

    # Pick the trip closest to 08:55 local
    target_local = time(8, 55)
    chosen = min(
        found,
        key=lambda t: abs(
            (t.board_departure.astimezone().hour * 60 + t.board_departure.astimezone().minute)
            - (target_local.hour * 60 + target_local.minute)
        ),
    )
    print(f"\n=> Gewaehlt: {chosen.label}")
    print(f"   Trip-ID:  {chosen.trip_id}")
    print(f"   Strecke:  {chosen.board_stop_name} -> {chosen.alight_stop_name}")
    print(f"   Abfahrt:  {chosen.board_departure}")
    print(f"   Ankunft:  {chosen.alight_arrival}")
    print(f"   Dauer:    {chosen.duration_minutes} min")

    points = db.build_route(
        trip_id=chosen.trip_id,
        board_stop_id=chosen.board_stop_id,
        alight_stop_id=chosen.alight_stop_id,
        service_date=chosen.service_date,
    )
    print(f"   Route-Punkte: {len(points)}")
    print(f"   Erster Punkt: {points[0].lat:.5f},{points[0].lon:.5f} @ {points[0].timestamp}")
    print(f"   Letzter:      {points[-1].lat:.5f},{points[-1].lon:.5f} @ {points[-1].timestamp}")

    rec = analyze(points)
    print()
    print(f"Auto-Intervall: {rec.auto_interval_m} m")
    print(f"Segmente: {len(rec.segments)}")
    print(f"Sonne ist auf: {rec.sun_side} ({rec.sun_pct:.1f} %)")
    print(f"Empfehlung Schattenseite: {rec.shade_side}")


if __name__ == "__main__":
    main()
