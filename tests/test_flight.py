from datetime import datetime, timezone

import pytest

from sunside.flight import great_circle_point, make_great_circle_route


def test_great_circle_point_preserves_endpoints():
    start = (52.0, 13.0)
    end = (40.7, -74.0)

    assert great_circle_point(start, end, 0.0) == pytest.approx(start)
    assert great_circle_point(start, end, 1.0) == pytest.approx(end)


def test_great_circle_midpoint_differs_from_linear_for_long_route():
    # Berlin -> New York arcs north over the Atlantic. A linear lat/lon midpoint
    # would sit around 46.35N; the great-circle midpoint is much farther north.
    midpoint = great_circle_point((52.0, 13.0), (40.7, -74.0), 0.5)
    assert midpoint[0] > 50.0


def test_make_great_circle_route_timestamps_are_evenly_distributed():
    departure = datetime(2026, 4, 29, 8, 0, tzinfo=timezone.utc)
    route = make_great_circle_route(
        (52.0, 13.0),
        (40.7, -74.0),
        departure,
        travel_hours=8.0,
        n_waypoints=4,
    )

    assert len(route) == 5
    assert route[0].timestamp == departure
    assert route[-1].timestamp == datetime(2026, 4, 29, 16, 0, tzinfo=timezone.utc)
    assert route[2].timestamp == datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)


def test_make_great_circle_route_auto_waypoints_has_minimum_resolution():
    departure = datetime(2026, 4, 29, 8, 0, tzinfo=timezone.utc)
    route = make_great_circle_route((52.0, 13.0), (40.7, -74.0), departure, travel_hours=8.0)
    assert len(route) >= 31
