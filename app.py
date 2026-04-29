"""
SunSide — Streamlit App
Welche Seite im Zug/Bus ist im Schatten?
"""
import math
import pytz
import streamlit as st
from datetime import datetime
from pathlib import Path

from sunside.route_providers.nominatim import NominatimProvider, search_places
from sunside.route_providers.osm import OsmRailProvider
from sunside.route_providers.osrm import OsrmRoadProvider
from sunside.route_providers.gpx import GpxProvider
from sunside.route_providers.gtfs_db import GtfsDatabase
from sunside.sun_analysis.analyzer import analyze, analyze_converged
from sunside.sun_analysis.sampler import auto_interval_m
from sunside.terrain import TerrainConfig, TerrainProvider
from sunside.weather import OpenMeteoWeatherProvider

st.set_page_config(page_title="SunSide")


@st.cache_data(ttl=3600)
def cached_station_search(query: str):
    return search_places(query, station_only=True, limit=8)


@st.cache_data(ttl=3600)
def cached_place_search(query: str):
    return search_places(query, station_only=False, limit=8)


def place_picker(label: str, default_query: str, key_prefix: str, *, station_only: bool = False):
    query = st.text_input(f"{label} suchen", default_query, key=f"{key_prefix}_query")
    options = cached_station_search(query) if station_only else cached_place_search(query)
    if not options:
        kind = "Station" if station_only else "Ort"
        st.warning(f"Kein passender {kind} gefunden.")
        return query, None

    selected = st.selectbox(
        f"{label} auswählen",
        options,
        format_func=lambda option: option.label,
        key=f"{key_prefix}_select",
    )
    return selected.name, selected


def station_picker(label: str, default_query: str, key_prefix: str):
    return place_picker(label, default_query, key_prefix, station_only=True)


def render_endpoint_preview(origin_place, destination_place, *, mode_label: str, dashed: bool = True):
    if origin_place is None or destination_place is None:
        return

    try:
        import folium
        from streamlit_folium import st_folium
    except Exception:
        return

    origin_coords = (origin_place.lat, origin_place.lon)
    destination_coords = (destination_place.lat, destination_place.lon)
    center = [
        (origin_coords[0] + destination_coords[0]) / 2,
        (origin_coords[1] + destination_coords[1]) / 2,
    ]
    preview = folium.Map(location=center, zoom_start=10, control_scale=True)
    folium.Marker(
        origin_coords,
        tooltip=origin_place.name,
        icon=folium.Icon(color="green", icon="play"),
    ).add_to(preview)
    folium.Marker(
        destination_coords,
        tooltip=destination_place.name,
        icon=folium.Icon(color="red", icon="flag"),
    ).add_to(preview)
    folium.PolyLine(
        [origin_coords, destination_coords],
        color="#2563eb",
        weight=4,
        opacity=0.75,
        dash_array="8,8" if dashed else None,
        tooltip=mode_label,
    ).add_to(preview)
    preview.fit_bounds([origin_coords, destination_coords], padding=(18, 18))

    st_folium(preview, width=700, height=280, key="endpoint_preview_map")

# --- PWA: Manifest + Meta-Tags + Service Worker (Scope: /app/static/) ---
st.markdown(
    """
    <link rel="manifest" href="./app/static/manifest.webmanifest">
    <meta name="theme-color" content="#f5b301">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="SunSide">
    <link rel="apple-touch-icon" href="./app/static/icon.svg">
    <script>
      if ("serviceWorker" in navigator) {
        window.addEventListener("load", function () {
          navigator.serviceWorker
            .register("./app/static/sw.js", { scope: "./app/static/" })
            .catch(function (err) { console.warn("SW registration failed:", err); });
        });
      }
    </script>
    """,
    unsafe_allow_html=True,
)

st.title("SunSide")
st.caption("Auf welcher Seite sitze ich im Schatten?")

# --- Modus-Auswahl (Sidebar) ---
GTFS_DIR = Path("data/gtfs")


def discover_gtfs_dbs() -> list[Path]:
    if not GTFS_DIR.exists():
        return []
    return sorted(p for p in GTFS_DIR.glob("*.sqlite") if p.is_file())


@st.cache_resource(ttl=3600)
def get_gtfs_db(path: str):
    try:
        return GtfsDatabase(path)
    except FileNotFoundError:
        return None


@st.cache_data(ttl=600)
def cached_gtfs_stop_search(path: str, query: str):
    db = get_gtfs_db(path)
    if db is None or len(query.strip()) < 2:
        return []
    return db.search_stops(query, limit=20)


def gtfs_stop_picker(label: str, default_query: str, key_prefix: str, db_path: str):
    query = st.text_input(f"{label} suchen", default_query, key=f"{key_prefix}_query")
    options = cached_gtfs_stop_search(db_path, query)
    if not options:
        st.caption(f"Kein passender Halt im Fahrplan fuer '{query}' gefunden.")
        return None
    selected = st.selectbox(
        f"{label} auswaehlen",
        options,
        format_func=lambda stop: f"{stop.name}  ({stop.lat:.3f}, {stop.lon:.3f})",
        key=f"{key_prefix}_select",
    )
    return selected


with st.sidebar:
    st.header("Reisemodus")
    travel_mode = st.radio(
        "Modus",
        ["Zug", "Auto/Bus", "Fahrplan (GTFS)", "Flugzeug", "GPX-Datei"],
        label_visibility="collapsed",
    )
    st.caption({
        "Zug": "OSM-Gleisgeometrie. Beliebige Bahnhoefe.",
        "Auto/Bus": "OSRM-Strassenrouting per Start/Ziel.",
        "Fahrplan (GTFS)": "Konkreter Zug-/Bus-Trip aus einem importierten GTFS-Feed (Rail-GTFS, Flixbus, DB, OeBB, ...) inkl. Shape/Polyline.",
        "Flugzeug": "Grosskreis-Approximation zwischen zwei Orten oder Flughaefen.",
        "GPX-Datei": "Eigene Strecke aus einer GPX-Datei.",
    }[travel_mode])

    st.divider()
    st.header("Praeferenz")
    pref = st.radio("Praeferenz", ["Schatten", "Sonne"], horizontal=True, label_visibility="collapsed")
    use_weather = st.toggle("Bewoelkung beruecksichtigen", value=False)
    use_intensity = st.toggle(
        "Sonnenintensitaet gewichten",
        value=False,
        help="Gewichtet Segmente nach Sonnenhoehe (sin(Hoehe)). "
             "Eine flache Abendsonne zaehlt dann weniger als die Mittagssonne.",
    )
    use_terrain = st.toggle(
        "Gelaendeschatten beruecksichtigen",
        value=False,
        help="Pro Segment werden Hoehensamples in Sonnenrichtung abgefragt "
             "(open-meteo). Wenn der Horizont die Sonne ueberragt, gilt das "
             "Segment als verschattet. Sinnvoll vor allem in Alpen-/Tal-Strecken.",
    )

    interval_options = {
        "Automatisch": None,
        "300 m (sehr kurvenreich)": 300,
        "1 km": 1_000,
        "5 km": 5_000,
        "20 km (sehr gerade)": 20_000,
    }
    interval_label = st.selectbox("Messintervall", list(interval_options.keys()))
    interval_m = interval_options[interval_label]

    steroid_mode = st.toggle(
        "Steroid-Modus (konvergierend)",
        value=False,
        help="Halbiert das Messintervall iterativ bis das Ergebnis stabil ist. "
             "Genauer, aber rechenintensiver. Ueberschreibt die Messintervall-Auswahl.",
    )

# --- Eingabe pro Modus ---
tz = pytz.timezone("Europe/Berlin")
_now = datetime.now(tz).replace(second=0, microsecond=0)

origin = ""
destination = ""
origin_place = None
destination_place = None
travel_hours = None
gpx_file = None
gtfs_board_stop = None
gtfs_alight_stop = None
gtfs_chosen_trip = None
gtfs_travel_date = None
departure = None

if travel_mode == "Zug":
    col1, col2 = st.columns(2)
    with col1:
        origin, origin_place = station_picker("Startbahnhof", "Berlin Hauptbahnhof", "origin_station")
    with col2:
        destination, destination_place = station_picker("Zielbahnhof", "Muenchen Hauptbahnhof", "destination_station")
    render_endpoint_preview(origin_place, destination_place, mode_label="Bahnhofsvorschau")
    col_d, col_t = st.columns(2)
    with col_d:
        dep_date = st.date_input("Abfahrtsdatum", value=_now.date(), key="train_date")
    with col_t:
        dep_time = st.time_input("Abfahrtszeit", value=_now.time(), key="train_time")
    departure = tz.localize(datetime.combine(dep_date, dep_time))
    if st.toggle("Reisedauer manuell setzen", value=False, key="train_duration_toggle"):
        travel_hours = st.slider("Reisedauer (Stunden)", 0.5, 12.0, 2.0, 0.5, key="train_duration")

elif travel_mode == "Auto/Bus":
    col1, col2 = st.columns(2)
    with col1:
        origin, origin_place = place_picker("Startort", "Berlin ZOB", "origin_road")
    with col2:
        destination, destination_place = place_picker("Zielort", "Muenchen ZOB", "destination_road")
    render_endpoint_preview(origin_place, destination_place, mode_label="Strassenroute-Vorschau")
    col_d, col_t = st.columns(2)
    with col_d:
        dep_date = st.date_input("Abfahrtsdatum", value=_now.date(), key="road_date")
    with col_t:
        dep_time = st.time_input("Abfahrtszeit", value=_now.time(), key="road_time")
    departure = tz.localize(datetime.combine(dep_date, dep_time))
    if st.toggle("Reisedauer manuell setzen", value=False, key="road_duration_toggle"):
        travel_hours = st.slider("Reisedauer (Stunden)", 0.5, 12.0, 2.0, 0.5, key="road_duration")

elif travel_mode == "Fahrplan (GTFS)":
    feeds = discover_gtfs_dbs()
    if not feeds:
        st.error(
            "Keine GTFS-Datenbank gefunden. Bitte zuerst importieren, z.B.: "
            "`python scripts/gtfs_import.py --zip data/gtfs/flixbus.zip --db data/gtfs/flixbus.sqlite`. "
            "Rail-GTFS-Feeds (DB, OeBB/Railjet, ...) findest du ebenfalls in der README."
        )
        st.stop()

    if len(feeds) == 1:
        gtfs_db_path = str(feeds[0])
    else:
        gtfs_db_path = str(st.selectbox(
            "GTFS-Feed",
            feeds,
            format_func=lambda p: p.stem,
            key="gtfs_feed_select",
        ))

    db = get_gtfs_db(gtfs_db_path)
    if db is None:
        st.error(f"Konnte GTFS-Datenbank nicht oeffnen: {gtfs_db_path}")
        st.stop()
    meta = db.feed_info()
    st.caption(
        f"Feed: {meta.get('feed_name') or meta.get('publisher_name', '?')} - gueltig "
        f"{meta.get('start_date', '?')} bis {meta.get('end_date', '?')} "
        f"(importiert {meta.get('imported_at_utc', '?')})"
    )

    col1, col2 = st.columns(2)
    with col1:
        gtfs_board_stop = gtfs_stop_picker("Einstieg", "", "gtfs_board", gtfs_db_path)
    with col2:
        gtfs_alight_stop = gtfs_stop_picker("Ausstieg", "", "gtfs_alight", gtfs_db_path)

    gtfs_travel_date = st.date_input("Reisedatum", value=_now.date(), key="gtfs_date")

    if gtfs_board_stop is not None and gtfs_alight_stop is not None:
        trips = db.find_trips(
            board_stop_id=gtfs_board_stop.stop_id,
            alight_stop_id=gtfs_alight_stop.stop_id,
            date_=gtfs_travel_date,
            limit=30,
        )
        if not trips:
            st.warning("Keine Fahrt fuer diese Kombination am gewaehlten Datum gefunden.")
            st.stop()
        gtfs_chosen_trip = st.selectbox(
            "Fahrt auswaehlen",
            trips,
            format_func=lambda t: t.label,
            key="gtfs_trip_select",
        )
        departure = gtfs_chosen_trip.board_departure
        origin = gtfs_chosen_trip.board_stop_name
        destination = gtfs_chosen_trip.alight_stop_name
    else:
        st.info("Bitte Einstieg und Ausstieg waehlen.")
        st.stop()

elif travel_mode == "Flugzeug":
    col1, col2 = st.columns(2)
    with col1:
        origin, origin_place = place_picker("Startort oder Flughafen", "Berlin BER", "origin_flight")
    with col2:
        destination, destination_place = place_picker("Zielort oder Flughafen", "Muenchen Flughafen", "destination_flight")
    render_endpoint_preview(origin_place, destination_place, mode_label="Grosskreis", dashed=False)
    col_d, col_t = st.columns(2)
    with col_d:
        dep_date = st.date_input("Abflugdatum", value=_now.date(), key="flight_date")
    with col_t:
        dep_time = st.time_input("Abflugzeit", value=_now.time(), key="flight_time")
    departure = tz.localize(datetime.combine(dep_date, dep_time))
    if st.toggle("Flugdauer manuell setzen", value=False, key="flight_duration_toggle"):
        travel_hours = st.slider("Flugdauer (Stunden)", 0.5, 12.0, 2.0, 0.5, key="flight_duration")

else:  # GPX-Datei
    col1, col2 = st.columns(2)
    with col1:
        origin = st.text_input("Von", "Villach", key="gpx_origin")
    with col2:
        destination = st.text_input("Nach", "Wien", key="gpx_destination")
    col_d, col_t = st.columns(2)
    with col_d:
        dep_date = st.date_input("Startdatum", value=_now.date(), key="gpx_date")
    with col_t:
        dep_time = st.time_input("Startzeit", value=_now.time(), key="gpx_time")
    departure = tz.localize(datetime.combine(dep_date, dep_time))
    gpx_file = st.file_uploader("GPX-Datei hochladen", type=["gpx"])

# --- Berechnung ---
if st.button("Berechnen", type="primary"):
    with st.spinner("Berechne..."):
        try:
            if travel_mode == "Zug":
                if origin_place is None or destination_place is None:
                    st.warning("Bitte Start- und Zielbahnhof aus der Liste auswaehlen.")
                    st.stop()
                provider = OsmRailProvider()
                points = provider.get_route_between_coordinates(
                    (origin_place.lat, origin_place.lon),
                    (destination_place.lat, destination_place.lon),
                    departure,
                    travel_hours=travel_hours,
                )
            elif travel_mode == "Auto/Bus":
                if origin_place is None or destination_place is None:
                    st.warning("Bitte Start- und Zielort aus der Liste auswaehlen.")
                    st.stop()
                provider = OsrmRoadProvider()
                points = provider.get_route_between_coordinates(
                    (origin_place.lat, origin_place.lon),
                    (destination_place.lat, destination_place.lon),
                    departure,
                    travel_hours=travel_hours,
                )
            elif travel_mode == "Fahrplan (GTFS)":
                if gtfs_chosen_trip is None:
                    st.warning("Bitte erst eine Fahrt auswaehlen.")
                    st.stop()
                db = get_gtfs_db(gtfs_db_path)
                points = db.build_route(
                    trip_id=gtfs_chosen_trip.trip_id,
                    board_stop_id=gtfs_chosen_trip.board_stop_id,
                    alight_stop_id=gtfs_chosen_trip.alight_stop_id,
                    service_date=gtfs_chosen_trip.service_date,
                )
            elif travel_mode == "Flugzeug":
                if origin_place is None or destination_place is None:
                    st.warning("Bitte Start- und Zielort aus der Liste auswaehlen.")
                    st.stop()
                provider = NominatimProvider(default_speed_kmh=800.0)
                points = provider.get_route_between_coordinates(
                    (origin_place.lat, origin_place.lon),
                    (destination_place.lat, destination_place.lon),
                    departure,
                    travel_hours=travel_hours,
                )
            elif gpx_file is not None:
                import tempfile, os
                with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False) as tmp:
                    tmp.write(gpx_file.read())
                    tmp_path = tmp.name
                provider = GpxProvider(tmp_path)
                points = provider.get_route(origin, destination, departure)
                os.unlink(tmp_path)
            else:
                st.warning("Bitte GPX-Datei hochladen oder einen anderen Reisemodus waehlen.")
                st.stop()

            # Auto-Intervall-Vorschlag anzeigen
            if interval_m is None and not steroid_mode:
                suggested = auto_interval_m(points)
                label = f"{suggested:,} m".replace(",", ".")
                st.info(f"Auto-Intervall: **{label}** (basierend auf Streckenkurvatur)")

            weather_provider = OpenMeteoWeatherProvider() if use_weather else None
            terrain_provider = TerrainProvider(TerrainConfig(enabled=True)) if use_terrain else None
            steroid_trace = None
            if steroid_mode:
                result, steroid_trace = analyze_converged(
                    points,
                    weather_provider=weather_provider,
                    use_intensity=use_intensity,
                    terrain_provider=terrain_provider,
                )
                final_interval = steroid_trace[-1]['interval_m']
                final_segments = steroid_trace[-1]['segments']
                seg_label = f"{final_segments:,}".replace(",", ".")
                st.info(
                    f"Steroid-Modus konvergiert bei **{final_interval} m** "
                    f"({seg_label} Segmente, {len(steroid_trace)} Iterationen)."
                )
            else:
                result = analyze(
                    points,
                    interval_m=interval_m,
                    weather_provider=weather_provider,
                    use_intensity=use_intensity,
                    terrain_provider=terrain_provider,
                )

        except Exception as e:
            st.error(f"Fehler: {e}")
            st.stop()

    # --- Ergebnis ---
    if result.is_night:
        st.info("Nachtfahrt — keine Sonnenempfehlung nötig.")
    else:
        if result.weather_adjusted:
            if result.mean_cloud_cover_pct is not None:
                st.caption(f"Wettergewichtung: durchschnittlich {result.mean_cloud_cover_pct:.0f} % Bewölkung")
            if result.low_direct_sun:
                st.info("Bei starker Bewölkung ist die Sitzseite weniger entscheidend.")
            if weather_provider is not None and weather_provider.warning:
                st.warning(weather_provider.warning)
        if result.intensity_adjusted and result.mean_sun_elevation is not None:
            st.caption(
                f"Intensitaetsgewichtung aktiv: durchschnittliche Sonnenhoehe "
                f"{result.mean_sun_elevation:.1f}° (sin = {max(0.0, math.sin(math.radians(result.mean_sun_elevation))):.2f})"
            )

        if pref == "Schatten":
            side = result.shade_side
            pct = result.shade_pct
            st.success(f"## Sitz **{side}** — ca. {pct:.0f} % im Schatten")
        else:
            side = result.sun_side
            pct = result.sun_pct
            st.success(f"## Sitz **{side}** — ca. {pct:.0f} % in der Sonne")

        st.caption(
            f"Messintervall: {result.auto_interval_m:,} m · "
            f"{len(result.segments)} Segmente analysiert"
        )
        if result.tunnel_pct > 0:
            st.caption(f"Tunnelanteil (Sonne irrelevant): {result.tunnel_pct:.0f} %")
        if result.terrain_adjusted and result.terrain_pct > 0:
            st.caption(f"Gelaendeschattenanteil: {result.terrain_pct:.0f} %")
        if result.terrain_adjusted and terrain_provider is not None and terrain_provider.warning:
            st.warning(terrain_provider.warning)

        if steroid_trace:
            with st.expander("Steroid-Konvergenz (Iterationen)"):
                import pandas as pd
                trace_rows = [
                    {
                        "Intervall (m)": entry["interval_m"],
                        "Segmente": entry["segments"],
                        "Sonne Seite": entry["sun_side"],
                        "Sonne %": round(entry["sun_pct"], 2),
                    }
                    for entry in steroid_trace
                ]
                st.dataframe(pd.DataFrame(trace_rows), width="stretch")

        # Detailtabelle
        with st.expander("Details pro Segment"):
            import pandas as pd
            rows = [
                {
                    "Zeit": s.point.timestamp.strftime("%H:%M"),
                    "Kurs °": s.bearing,
                    "Sonne °": s.sun_azimuth,
                    "Höhe °": s.sun_elevation,
                    "Intensität": round(s.intensity_factor, 2),
                    "Bewölkung %": None if s.cloud_cover_pct is None else round(s.cloud_cover_pct),
                    "Sonnenfaktor": round(s.sun_factor, 2),
                    "Sonnenseite": s.sun_side,
                    "Gelände-Schatten": "X" if s.terrain_shaded else "",
                }
                for s in result.segments
            ]
            st.dataframe(pd.DataFrame(rows), width="stretch")

        # Karte
        try:
            import folium
            from streamlit_folium import st_folium

            mid = points[len(points) // 2]
            m = folium.Map(location=[mid.lat, mid.lon], zoom_start=7)
            folium.Marker(
                [points[0].lat, points[0].lon], tooltip=origin,
                icon=folium.Icon(color="green"),
            ).add_to(m)
            folium.Marker(
                [points[-1].lat, points[-1].lon], tooltip=destination,
                icon=folium.Icon(color="red"),
            ).add_to(m)

            side_colors = {
                "links": "#2563eb",   # blue
                "rechts": "#f97316",  # orange
                "night": "#475569",   # slate
                "tunnel": "#1f2937",  # dark gray
                "terrain": "#16a34a", # green
            }
            side_labels = {
                "links": "Sonne links",
                "rechts": "Sonne rechts",
                "night": "Nacht",
                "tunnel": "Tunnel",
                "terrain": "Gelaendeschatten",
            }

            # Split the route into runs of consecutive same-side segments and
            # draw each as its own polyline so the colour reflects the analysis.
            if result.segments:
                def _run_key(seg):
                    return "terrain" if seg.terrain_shaded else seg.sun_side

                run_side = _run_key(result.segments[0])
                run_coords = [(result.segments[0].point.lat, result.segments[0].point.lon)]
                for seg in result.segments[1:]:
                    coord = (seg.point.lat, seg.point.lon)
                    key = _run_key(seg)
                    if key == run_side:
                        run_coords.append(coord)
                    else:
                        run_coords.append(coord)
                        folium.PolyLine(
                            run_coords,
                            color=side_colors.get(run_side, "#2563eb"),
                            weight=5,
                            opacity=0.85,
                            tooltip=side_labels.get(run_side, run_side),
                        ).add_to(m)
                        run_side = key
                        run_coords = [coord]
                # close last run to destination
                run_coords.append((points[-1].lat, points[-1].lon))
                folium.PolyLine(
                    run_coords,
                    color=side_colors.get(run_side, "#2563eb"),
                    weight=5,
                    opacity=0.85,
                    tooltip=side_labels.get(run_side, run_side),
                ).add_to(m)

            legend_html = (
                '<div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999; '
                'background: white; padding: 8px 12px; border: 1px solid #999; '
                'border-radius: 6px; font-size: 12px; line-height: 1.6;">'
                '<b>Sonnenseite</b><br>'
                '<span style="color:#2563eb">&#9632;</span> links&nbsp; '
                '<span style="color:#f97316">&#9632;</span> rechts&nbsp; '
                '<span style="color:#475569">&#9632;</span> Nacht&nbsp; '
                '<span style="color:#1f2937">&#9632;</span> Tunnel&nbsp; '
                '<span style="color:#16a34a">&#9632;</span> Gelände'
                '</div>'
            )
            m.get_root().html.add_child(folium.Element(legend_html))

            st_folium(m, width=700, height=450, key="analysis_result_map")
        except Exception:
            pass
