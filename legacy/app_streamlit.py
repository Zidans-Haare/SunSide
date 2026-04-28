"""
SunSide — Streamlit App
Welche Seite im Zug/Bus ist im Schatten?
"""
import pytz
import streamlit as st
from datetime import datetime

from sunside.route_providers.nominatim import NominatimProvider, search_places
from sunside.route_providers.osm import OsmRailProvider
from sunside.route_providers.osrm import OsrmRoadProvider
from sunside.route_providers.gpx import GpxProvider
from sunside.sun_analysis.analyzer import analyze
from sunside.sun_analysis.sampler import auto_interval_m
from sunside.weather import OpenMeteoWeatherProvider

st.set_page_config(page_title="SunSide", page_icon="🌞")


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

st.title("🌞 SunSide")
st.caption("Auf welcher Seite sitze ich im Schatten?")

# --- Modus-Auswahl ---
travel_mode = st.selectbox(
    "Reisemodus",
    ["Zug", "Auto/Bus", "Flugzeug", "GPX-Datei"],
)
st.caption(
    "Zug nutzt OSM-Gleise, Auto/Bus nutzt OSRM-Strassenrouting, Flugzeug nutzt Luftlinie. "
    "Flixbus ist damit fuer direkte Strassenrouten abbildbar; echte Fahrplaene kommen spaeter ueber GTFS."
)

# --- Eingabe ---
col1, col2 = st.columns(2)
if travel_mode == "Zug":
    with col1:
        origin, origin_place = station_picker(
            "Startbahnhof", "Berlin Hauptbahnhof", "origin_station"
        )
    with col2:
        destination, destination_place = station_picker(
            "Zielbahnhof", "Berlin Ostbahnhof", "destination_station"
        )
    render_endpoint_preview(origin_place, destination_place, mode_label="Bahnhofsvorschau")
elif travel_mode == "Auto/Bus":
    with col1:
        origin, origin_place = place_picker("Startort", "Berlin ZOB", "origin_road")
    with col2:
        destination, destination_place = place_picker("Zielort", "München ZOB", "destination_road")
    render_endpoint_preview(origin_place, destination_place, mode_label="Strassenroute-Vorschau")
elif travel_mode == "Flugzeug":
    with col1:
        origin, origin_place = place_picker("Startort oder Flughafen", "Berlin BER", "origin_flight")
    with col2:
        destination, destination_place = place_picker("Zielort oder Flughafen", "München Flughafen", "destination_flight")
    render_endpoint_preview(origin_place, destination_place, mode_label="Luftlinie", dashed=False)
else:
    origin_place = None
    destination_place = None
    with col1:
        origin = st.text_input("Von", "Villach")
    with col2:
        destination = st.text_input("Nach", "Wien")

tz = pytz.timezone("Europe/Berlin")
_now = datetime.now(tz).replace(second=0, microsecond=0)
col_d, col_t = st.columns(2)
with col_d:
    dep_date = st.date_input("Abfahrtsdatum", value=_now.date())
with col_t:
    dep_time = st.time_input("Abfahrtszeit", value=_now.time())
departure = tz.localize(datetime.combine(dep_date, dep_time))

travel_hours = None
if travel_mode in {"Zug", "Auto/Bus", "Flugzeug"}:
    manual_duration = st.toggle("Reisedauer manuell setzen", value=False)
    if manual_duration:
        travel_hours = st.slider("Reisedauer (Stunden)", 0.5, 12.0, 2.0, 0.5)

pref = st.radio("Präferenz", ["Schatten", "Sonne"], horizontal=True)
use_weather = st.toggle("Bewölkung berücksichtigen", value=False)

interval_options = {
    "Automatisch": None,
    "300 m (sehr kurvenreich)": 300,
    "1 km": 1_000,
    "5 km": 5_000,
    "20 km (sehr gerade)": 20_000,
}
interval_label = st.selectbox("Messintervall", list(interval_options.keys()))
interval_m = interval_options[interval_label]

gpx_file = None
if travel_mode == "GPX-Datei":
    gpx_file = st.file_uploader("GPX-Datei hochladen", type=["gpx"])

# --- Berechnung ---
if st.button("Berechnen", type="primary"):
    with st.spinner("Berechne..."):
        try:
            if travel_mode == "Zug":
                if origin_place is None or destination_place is None:
                    st.warning("Bitte Start- und Zielbahnhof aus der Liste auswählen.")
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
                    st.warning("Bitte Start- und Zielort aus der Liste auswählen.")
                    st.stop()
                provider = OsrmRoadProvider()
                points = provider.get_route_between_coordinates(
                    (origin_place.lat, origin_place.lon),
                    (destination_place.lat, destination_place.lon),
                    departure,
                    travel_hours=travel_hours,
                )
            elif travel_mode == "Flugzeug":
                if origin_place is None or destination_place is None:
                    st.warning("Bitte Start- und Zielort aus der Liste auswählen.")
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
                st.warning("Bitte GPX-Datei hochladen oder einen anderen Reisemodus wählen.")
                st.stop()

            # Auto-Intervall-Vorschlag anzeigen
            if interval_m is None:
                suggested = auto_interval_m(points)
                label = f"{suggested:,} m".replace(",", ".")
                st.info(f"Auto-Intervall: **{label}** (basierend auf Streckenkurvatur)")

            weather_provider = OpenMeteoWeatherProvider() if use_weather else None
            result = analyze(points, interval_m=interval_m, weather_provider=weather_provider)

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

        # Detailtabelle
        with st.expander("Details pro Segment"):
            import pandas as pd
            rows = [
                {
                    "Zeit": s.point.timestamp.strftime("%H:%M"),
                    "Kurs °": s.bearing,
                    "Sonne °": s.sun_azimuth,
                    "Höhe °": s.sun_elevation,
                    "Bewölkung %": None if s.cloud_cover_pct is None else round(s.cloud_cover_pct),
                    "Sonnenfaktor": round(s.sun_factor, 2),
                    "Sonnenseite": s.sun_side,
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
            folium.Marker([points[0].lat, points[0].lon], tooltip=origin, icon=folium.Icon(color="green")).add_to(m)
            folium.Marker([points[-1].lat, points[-1].lon], tooltip=destination, icon=folium.Icon(color="red")).add_to(m)
            folium.PolyLine(
                [(point.lat, point.lon) for point in points],
                color="#2563eb",
                weight=4,
                opacity=0.75,
            ).add_to(m)

            # Color segments by sun side
            for seg in result.segments:
                color = "orange" if seg.sun_side == "rechts" else ("blue" if seg.sun_side == "links" else "gray")
                folium.CircleMarker(
                    [seg.point.lat, seg.point.lon],
                    radius=3,
                    color=color,
                    fill=True,
                    tooltip=f"{seg.sun_side} | {seg.point.timestamp.strftime('%H:%M')}",
                ).add_to(m)

            st_folium(m, width=700, height=450, key="analysis_result_map")
        except Exception:
            pass
