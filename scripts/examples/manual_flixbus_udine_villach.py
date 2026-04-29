"""Retro check: Flixbus Udine -> Villach am 26.04.2026 ab 18:05."""
from datetime import date, time

import pytz

from sunside.route_providers.gtfs_db import GtfsDatabase
from sunside.sun_analysis.analyzer import analyze


def main():
    db = GtfsDatabase("data/gtfs/flixbus.sqlite")

    udine = db.search_stops("Udine")
    villach = db.search_stops("Villach")
    print("Udine-Stops:")
    for s in udine:
        print(f"  {s.stop_id}  {s.name}  ({s.lat:.4f}, {s.lon:.4f})")
    print("Villach-Stops:")
    for s in villach:
        print(f"  {s.stop_id}  {s.name}  ({s.lat:.4f}, {s.lon:.4f})")

    travel_date = date(2026, 4, 26)
    found = []
    for u in udine:
        for v in villach:
            for t in db.find_trips(
                board_stop_id=u.stop_id,
                alight_stop_id=v.stop_id,
                date_=travel_date,
                limit=10,
            ):
                found.append(t)

    print(f"\nGefundene Trips am {travel_date}: {len(found)}")
    for t in sorted(found, key=lambda x: x.board_departure):
        print(f"  {t.label}  | trip_id={t.trip_id}")
    if not found:
        return

    # Pick closest to 18:05 local Udine time
    rome = pytz.timezone("Europe/Rome")
    target = time(18, 5)

    def diff_minutes(t):
        local = t.board_departure.astimezone(rome)
        return abs((local.hour * 60 + local.minute) - (target.hour * 60 + target.minute))

    chosen = min(found, key=diff_minutes)
    print(f"\n=> {chosen.label}")
    print(f"   {chosen.board_stop_name} -> {chosen.alight_stop_name}")
    print(f"   Abfahrt:  {chosen.board_departure}")
    print(f"   Ankunft:  {chosen.alight_arrival}")

    points = db.build_route(
        trip_id=chosen.trip_id,
        board_stop_id=chosen.board_stop_id,
        alight_stop_id=chosen.alight_stop_id,
        service_date=chosen.service_date,
    )
    print(f"   Punkte:   {len(points)}")

    rec = analyze(points)
    print()
    print(f"Auto-Intervall: {rec.auto_interval_m} m")
    print(f"Segmente: {len(rec.segments)} (links/rechts/nacht: "
          f"{sum(1 for s in rec.segments if s.sun_side=='links')}/"
          f"{sum(1 for s in rec.segments if s.sun_side=='rechts')}/"
          f"{sum(1 for s in rec.segments if s.sun_side=='night')})")
    print(f"Nacht? {rec.is_night}")
    print(f"Sonne ist auf: {rec.sun_side} ({rec.sun_pct:.1f} %)")
    print(f"Schattenseite: {rec.shade_side} ({rec.shade_pct:.1f} %)")

    print()
    if rec.is_night:
        print("=> Du sasst links - aber die ganze Fahrt war im Dunkeln.")
    elif rec.sun_side == "links":
        print(f"=> Du sasst links == SONNENSEITE ({rec.sun_pct:.0f} %).")
    else:
        print(f"=> Du sasst links == SCHATTENSEITE ({rec.shade_pct:.0f} %).")


if __name__ == "__main__":
    main()
