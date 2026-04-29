"""Test the steroid (converged) mode on the Udine -> Villach trip."""
import time as time_mod
import pytz
from datetime import date, time

from sunside.route_providers.gtfs_db import GtfsDatabase
from sunside.sun_analysis.analyzer import analyze_converged


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
    print(f"Roh-Punkte: {len(points)}\n")

    for tol in (1.0, 0.5, 0.1):
        t0 = time_mod.perf_counter()
        rec, trace = analyze_converged(points, tolerance_pct=tol)
        dt = (time_mod.perf_counter() - t0) * 1000
        print(f"--- tolerance = {tol} % --- ({dt:.1f} ms)")
        for entry in trace:
            print(f"  {entry['interval_m']:>6} m  segs={entry['segments']:>5}  "
                  f"Sonne {entry['sun_side']:>6} {entry['sun_pct']:6.2f} %")
        print(f"  -> Final: Sonne {rec.sun_side} ({rec.sun_pct:.2f} %), "
              f"Schatten {rec.shade_side} ({rec.shade_pct:.2f} %)\n")


if __name__ == "__main__":
    main()
