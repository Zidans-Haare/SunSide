import streamlit as st
from pyhafas import HafasClient
from pyhafas.profile import DBProfile
from datetime import datetime, timedelta
import pandas as pd
import pytz
from astral import Observer
from astral.sun import azimuth, elevation
import folium
from streamlit_folium import st_folium
import matplotlib.pyplot as plt
from math import radians, degrees, atan2, sin, cos


def search_station(query: str, client: HafasClient):
    """Suche nach einem Bahnhofsnamen und liefere Liste von (Name, ID, lat, lon)."""
    try:
        results = client.locations(query)
        return [
            (loc.name, loc.id, loc.latitude, loc.longitude)
            for loc in results[:5]
            if loc.name and loc.id
        ]
    except Exception:
        return []


def fetch_journey(client: HafasClient, origin_id: str, dest_id: str, dt: datetime):
    """Hole die beste Verbindung zwischen zwei Bahnhöfen."""
    try:
        journeys = client.journeys(origin=origin_id, destination=dest_id, date=dt)
        return journeys[0]
    except Exception:
        return None


def calc_bearing(p1, p2):
    """Berechne Kurswinkel zwischen zwei Koordinaten."""
    lat1, lon1 = map(radians, p1)
    lat2, lon2 = map(radians, p2)
    dlon = lon2 - lon1
    x = sin(dlon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    return (degrees(atan2(x, y)) + 360) % 360


def analyze_points(points, pref: str):
    """Analysiere Sonnenposition entlang einer Strecke."""
    left, right = 0, 0
    records = []
    for i in range(len(points) - 1):
        lat, lon, ts = points[i]
        next_lat, next_lon, _ = points[i + 1]
        bearing = calc_bearing((lat, lon), (next_lat, next_lon))

        # Erstelle Observer für astral
        observer = Observer(latitude=lat, longitude=lon)
        sun_az = azimuth(observer, ts)
        sun_el = elevation(observer, ts)

        if sun_el < 0:
            continue
        delta = (sun_az - bearing) % 360
        side = "rechts" if 0 <= delta < 180 else "links"
        if side == "rechts":
            right += 1
        else:
            left += 1
        records.append(
            {
                "Uhrzeit": ts.strftime("%Y-%m-%d %H:%M"),
                "Kurs": round(bearing, 1),
                "Sonnen-Azimut": round(sun_az, 1),
                "Sonnenseite": side,
            }
        )
    total = left + right
    if total == 0:
        return records, None, 0, 0, 0
    left_pct = left / total * 100
    right_pct = right / total * 100
    if pref.startswith("Sch"):
        if left_pct <= right_pct:
            rec_side, pct = "links", 100 - left_pct
        else:
            rec_side, pct = "rechts", 100 - right_pct
    else:
        if left_pct >= right_pct:
            rec_side, pct = "links", left_pct
        else:
            rec_side, pct = "rechts", right_pct
    return records, rec_side, pct, left_pct, right_pct


def create_map(points):
    """Erzeuge Folium-Karte mit Streckenverlauf."""
    if not points:
        return None
    m = folium.Map(location=(points[0][0], points[0][1]), zoom_start=6)
    folium.Marker((points[0][0], points[0][1]), tooltip="Start").add_to(m)
    folium.Marker((points[-1][0], points[-1][1]), tooltip="Ziel").add_to(m)
    line = [(p[0], p[1]) for p in points]
    folium.PolyLine(line, color="blue").add_to(m)
    return m


st.set_page_config(page_title="Sonnenseite im Zug")

st.title("Sonnenseite im Zug")

client = HafasClient(DBProfile())

with st.form("input_form"):
    start_query = st.text_input("Abfahrtsbahnhof", "Berlin", key="start")
    dest_query = st.text_input("Zielbahnhof", "Muenchen", key="dest")
    tz = pytz.timezone("Europe/Berlin")
    dep_time = st.datetime_input(
        "Abfahrtsdatum & Uhrzeit", value=datetime.now(tz), key="dt"
    )
    pref = st.radio(
        "Präferenz", ["Schatten bevorzugen", "Sonne bevorzugen"], index=0
    )
    submit = st.form_submit_button("Berechnen")

if submit:
    if len(start_query) < 3 or len(dest_query) < 3:
        st.error("Bitte mindestens 3 Zeichen pro Bahnhof eingeben.")
        st.stop()
    start_opts = search_station(start_query, client)
    dest_opts = search_station(dest_query, client)
    if not start_opts or not dest_opts:
        st.error("Bahnhof nicht gefunden oder API-Fehler.")
        st.stop()
    start_name, start_id, start_lat, start_lon = start_opts[0]
    dest_name, dest_id, dest_lat, dest_lon = dest_opts[0]
    journey = fetch_journey(client, start_id, dest_id, dep_time)
    if not journey:
        st.error("Keine Verbindung gefunden oder API-Fehler.")
        st.stop()

    # Extrahiere Punkte aus allen Legs und deren Stopovers
    points = []
    for leg in journey.legs:
        if leg.stopovers:
            for stopover in leg.stopovers:
                stop = stopover.stop
                ts = stopover.departure or stopover.arrival
                if stop.latitude and stop.longitude and ts:
                    points.append((stop.latitude, stop.longitude, ts))
        else:
            # Fallback: Nutze nur Origin/Destination des Legs
            if leg.origin.latitude and leg.origin.longitude:
                points.append((leg.origin.latitude, leg.origin.longitude, leg.departure))
            if leg.destination.latitude and leg.destination.longitude:
                points.append((leg.destination.latitude, leg.destination.longitude, leg.arrival))

    if len(points) < 2:
        st.error("Nicht genügend Streckendaten verfügbar.")
        st.stop()

    # Zeige Verbindungsinfo
    first_leg = journey.legs[0]
    st.info(f"**{start_name}** → **{dest_name}**  \n"
            f"Abfahrt: {first_leg.departure.strftime('%H:%M')} · "
            f"Ankunft: {journey.legs[-1].arrival.strftime('%H:%M')} · "
            f"{len(points)} Haltepunkte")

    records, rec_side, pct, left_pct, right_pct = analyze_points(points, pref)
    if rec_side:
        if pref.startswith("Sch"):
            st.success(
                f"Empfehlung: {rec_side} sitzen – dort ca. {pct:.0f} % der Zeit im Schatten."
            )
        else:
            st.success(
                f"Empfehlung: {rec_side} sitzen – dort ca. {pct:.0f} % der Zeit in der Sonne."
            )
    else:
        st.info("Nachtfahrt – keine Empfehlung möglich.")

    df = pd.DataFrame(records)
    st.dataframe(df)

    fig, ax = plt.subplots()
    ax.bar(["links", "rechts"], [left_pct, right_pct], color=["orange", "blue"])
    ax.set_ylabel("Sonnen-Anteil [%]")
    st.pyplot(fig)

    m = create_map(points)
    if m:
        st_folium(m, width=700, height=500)

"""
Deployment-Hinweis:
    ssh user@server
    git clone <REPO_URL> ~/www/sun.olomek.com
    cd ~/www/sun.olomek.com
    python3.10 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    streamlit run sun_side_app.py
"""
