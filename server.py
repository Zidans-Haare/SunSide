requirements = ["flask", "geopy", "astral", "pytz"]

from flask import Flask, request, jsonify, send_from_directory
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from astral import LocationInfo
from astral.sun import azimuth, elevation
from datetime import datetime
import pytz
import math

app = Flask(__name__)

def calc_bearing(p1, p2):
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    brng = math.degrees(math.atan2(x, y))
    return (brng + 360) % 360

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    data = request.get_json(force=True)
    start = data.get('start')
    dest = data.get('dest')
    dt_str = data.get('datetime')
    pref = data.get('pref', 'Schatten bevorzugen')
    try:
        tz = pytz.timezone('Europe/Berlin')
        dt = tz.localize(datetime.fromisoformat(dt_str)) if dt_str else datetime.now(tz)
    except Exception:
        return jsonify({'error': 'Ungültiges Datum'}), 400
    geolocator = Nominatim(user_agent='sunside-web')
    try:
        start_geo = geolocator.geocode(start)
        dest_geo = geolocator.geocode(dest)
        if not start_geo or not dest_geo:
            raise ValueError('Ort nicht gefunden')
    except Exception as e:
        return jsonify({'error': f'Geocoding fehlgeschlagen: {e}'})
    start_coords = (start_geo.latitude, start_geo.longitude)
    dest_coords = (dest_geo.latitude, dest_geo.longitude)
    bearing = calc_bearing(start_coords, dest_coords)
    distance_km = geodesic(start_coords, dest_coords).km
    midpoint = geodesic(distance_km / 2).destination(start_coords, bearing)
    obs = LocationInfo(latitude=midpoint.latitude, longitude=midpoint.longitude)
    sun_az = azimuth(obs.observer, dt)
    sun_el = elevation(obs.observer, dt)
    if sun_el < 0:
        return jsonify({'result': 'Nachtfahrt – keine Empfehlung möglich.'})
    delta = (sun_az - bearing) % 360
    if 0 < delta < 180:
        sun_side, shade_side = 'rechts', 'links'
    else:
        sun_side, shade_side = 'links', 'rechts'
    if pref.startswith('Sch'):
        rec_side = shade_side
    else:
        rec_side = sun_side
    return jsonify({'result': f'Empfehlung: {rec_side} sitzen.'})

if __name__ == '__main__':
    app.run(debug=True)
