requirements = [
    "streamlit",
    "geopy",
    "astral",
    "folium",
    "pandas"
]

import streamlit as st
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from datetime import datetime, timedelta
import pandas as pd
import pytz
from astral import LocationInfo
from astral.sun import azimuth, elevation
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="Train Sun Side Advisor")

st.title("🚆🌞 Train Sun Side Advisor")

# 1. Eingabemaske
with st.form(key="input_form"):
    col1, col2 = st.columns(2)
    with col1:
        start_loc = st.text_input("Abfahrtsort", "Berlin")
    with col2:
        dest_loc = st.text_input("Zielort", "München")

    tz = pytz.timezone("Europe/Berlin")
    dt_input = st.datetime_input(
        "Abfahrtsdatum/-zeit", value=datetime.now(tz), key="dt_input"
    )

    pref = st.radio(
        "Präferenz", ["Schatten bevorzugen", "Sonne bevorzugen"], index=0
    )

    submit = st.form_submit_button("Berechnen")

if submit:
    geolocator = Nominatim(user_agent="train-sun-side")
    try:
        start_geo = geolocator.geocode(start_loc)
        dest_geo = geolocator.geocode(dest_loc)
        if not start_geo or not dest_geo:
            raise ValueError("Ort nicht gefunden")
    except Exception as e:
        st.error(f"Geocoding fehlgeschlagen: {e}")
    else:
        start_coords = (start_geo.latitude, start_geo.longitude)
        dest_coords = (dest_geo.latitude, dest_geo.longitude)

        # b) Bearing und Distanz
        def calc_bearing(p1, p2):
            import math

            lat1, lon1 = map(math.radians, p1)
            lat2, lon2 = map(math.radians, p2)
            dlon = lon2 - lon1
            x = math.sin(dlon) * math.cos(lat2)
            y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
            brng = math.degrees(math.atan2(x, y))
            return (brng + 360) % 360

        bearing = calc_bearing(start_coords, dest_coords)
        distance_km = geodesic(start_coords, dest_coords).km

        # Streckenmittelpunkt
        midpoint = geodesic(distance_km / 2).destination(start_coords, bearing)
        mid_coords = (midpoint.latitude, midpoint.longitude)

        # Sonnenposition
        obs = LocationInfo(latitude=mid_coords[0], longitude=mid_coords[1])
        sun_az = azimuth(obs.observer, dt_input)
        sun_el = elevation(obs.observer, dt_input)

        if sun_el < 0:
            st.info("Nachtfahrt – keine Empfehlung nötig.")
        else:
            delta = (sun_az - bearing) % 360
            if 0 < delta < 180:
                sun_side = "rechts"
                shade_side = "links"
            else:
                sun_side = "links"
                shade_side = "rechts"

            warn = ""
            if abs(delta - 90) < 20 or abs(delta - 270) < 20:
                warn = "⚠️ Sonne fast vorne/hinten"

            # Simulation
            steps = int((distance_km / 120) * 2)  # alle 30min
            times = [dt_input + timedelta(minutes=30 * i) for i in range(steps + 1)]
            records = []
            sun_left = 0
            for t in times:
                sun_az_t = azimuth(obs.observer, t)
                sun_el_t = elevation(obs.observer, t)
                delta_t = (sun_az_t - bearing) % 360
                side = "rechts" if 0 < delta_t < 180 else "links"
                if side == "links":
                    sun_left += 1
                records.append({
                    "Zeit": t.strftime("%Y-%m-%d %H:%M"),
                    "Sonnen-Azimut": round(sun_az_t, 2),
                    "Kurs": round(bearing, 2),
                    "Sonnenseite": side,
                })
            percent_left = sun_left / len(records) * 100
            percent_right = 100 - percent_left

            if pref.startswith("Sch"):
                rec_side = shade_side
                pct = percent_left if shade_side == "links" else percent_right
                st.success(
                    f"Empfehlung: {rec_side} sitzen, dort ca. {pct:.0f} % der Zeit im Schatten." + (" " + warn if warn else "")
                )
            else:
                rec_side = sun_side
                pct = percent_left if sun_side == "links" else percent_right
                st.success(
                    f"Empfehlung: {rec_side} sitzen, dort ca. {pct:.0f} % der Zeit in der Sonne." + (" " + warn if warn else "")
                )

            df = pd.DataFrame(records)
            st.table(df)

            # Karte
            m = folium.Map(location=mid_coords, zoom_start=6)
            folium.Marker(start_coords, tooltip="Start").add_to(m)
            folium.Marker(dest_coords, tooltip="Ziel").add_to(m)
            folium.PolyLine([start_coords, dest_coords], color="blue").add_to(m)
            st_folium(m, width=700)
