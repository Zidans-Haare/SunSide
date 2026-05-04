from sunside import browser_api


def _day_route():
    return browser_api.make_great_circle_route(
        48.0,
        11.0,
        49.0,
        11.2,
        "2024-06-21T10:00:00+00:00",
        travel_hours=1.5,
        n_waypoints=12,
    )


def test_run_analysis_exposes_intensity_fields():
    result = browser_api.run_analysis(
        _day_route(),
        interval_m=20_000,
        use_intensity=True,
    )

    assert result["intensity_adjusted"] is True
    assert result["mean_sun_elevation"] is not None
    assert result["convergence_trace"] == []
    assert result["segments"]
    assert "intensity_factor" in result["segments"][0]


def test_run_analysis_converged_returns_trace():
    result = browser_api.run_analysis(_day_route(), converged=True)

    assert result["convergence_trace"]
    assert result["auto_interval_m"] == result["convergence_trace"][-1]["interval_m"]


def test_elevation_points_for_route_are_unique():
    coords = browser_api.elevation_points_for_route(_day_route(), interval_m=20_000)

    assert coords
    assert len({(coord["lat"], coord["lon"]) for coord in coords}) == len(coords)
