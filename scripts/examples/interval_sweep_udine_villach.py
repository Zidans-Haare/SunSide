"""Sweep different sampling intervals to see how the percentages stabilise."""
import pytz
from datetime import date, time

from sunside.route_providers.gtfs_db import GtfsDatabase
from sunside.sun_analysis.analyzer import analyze


def main():
    db = GtfsDatabase('data/gtfs/flixbus.sqlite')
    u = db.search_stops('Udine')[0]
    v = db.search_stops('Villach')[0]
    trips = db.find_trips(board_stop_id=u.stop_id, alight_stop_id=v.stop_id,
                          date_=date(2026, 4, 26), limit=20)
    rome = pytz.timezone('Europe/Rome')
    target = time(18, 5)
    chosen = min(
        trips,
        key=lambda t: abs(
            (t.board_departure.astimezone(rome).hour * 60
             + t.board_departure.astimezone(rome).minute)
            - (target.hour * 60 + target.minute)
        ),
    )
    points = db.build_route(trip_id=chosen.trip_id,
                            board_stop_id=chosen.board_stop_id,
                            alight_stop_id=chosen.alight_stop_id,
                            service_date=chosen.service_date)

    print(f"Trip: {chosen.label}")
    print(f"Roh-Punkte aus GTFS: {len(points)}\n")

    print(f"{'Intervall':>10} | {'Segmente':>9} | {'links':>6} | {'rechts':>6} "
          f"| {'links %':>8} | {'rechts %':>9}")
    print('-' * 70)
    for iv in [20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50, 20, 10, 5]:
        rec = analyze(points, interval_m=iv)
        left = sum(1 for s in rec.segments if s.sun_side == 'links')
        right = sum(1 for s in rec.segments if s.sun_side == 'rechts')
        day = max(left + right, 1)
        print(f'{iv:>9} m | {len(rec.segments):>9} | {left:>6} | {right:>6} '
              f'| {left/day*100:>7.2f} % | {right/day*100:>8.2f} %')


if __name__ == "__main__":
    main()
