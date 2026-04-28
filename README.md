# SunSide

SunSide ist eine **statische Progressive Web App**, die je nach Route und Sonnenstand empfiehlt, auf welcher Wagenseite man besser sitzt. Die App läuft komplett im Browser — die Python-Logik (Sonnenstand, Routing, Wetter-Gewichtung) wird via [Pyodide](https://pyodide.org/) clientseitig ausgeführt.

## Funktionen

- Sitzempfehlung **links / rechts** für Schatten oder Sonne
- Routenquellen: **OSM-Gleisroute**, **Luftlinie**, eigene **GPX-Datei**
- Optional: **Bewölkung** (Open-Meteo) gewichtet die Empfehlung
- Karte mit Polylinie + farbigen Segmentpunkten (Leaflet)
- Detailtabelle (Kurs, Sonnenazimut, Sonnenhöhe, Wolken)
- **PWA**: installierbar, App-Shell-Caching, Offline-Start (Berechnungen brauchen Netz für OSM/Wetter)

## Lokal starten

Pyodide-Module dürfen nicht über `file://` geladen werden — du brauchst einen **statischen HTTP-Server**.

```bash
# Python (Bordmittel)
python -m http.server 8000
# oder Node:
npx http-server -p 8000
```

Dann im Browser: <http://localhost:8000/>

Das erste Laden dauert ~5–10 s (Pyodide ~6 MB) und wird vom Service Worker gecacht.

## Deployment

Statisches Hosting reicht. Anforderungen:

- **HTTPS** (außer `localhost`) — Pflicht für Service Worker / PWA
- Korrekte MIME-Types für `.webmanifest` und `.py` (die meisten Hoster liefern das von Haus aus richtig)
- Service Worker liegt im **Wurzelverzeichnis** (`/sw.js`), damit er die ganze App scopt

Funktioniert direkt mit GitHub Pages, Netlify, Cloudflare Pages, Vercel (statisches Preset), etc.

## Projektstruktur

```
SunSide/
├── index.html              # SPA Entry
├── app.js                  # Pyodide-Bootstrapping + UI-Glue
├── sw.js                   # Service Worker
├── manifest.webmanifest    # PWA-Manifest
├── assets/                 # Icons
├── sunside/                # Python-Logik (wird von Pyodide geladen)
│   ├── browser_api.py      # Browser-Entry (kein requests, JS reicht Daten rein)
│   ├── models.py
│   ├── weather.py
│   └── sun_analysis/
└── legacy/                 # alte Streamlit-/Trainings-Skripte (werden nicht mehr genutzt)
```

## Datenquellen

- [OpenStreetMap](https://www.openstreetmap.org/) via [Nominatim](https://nominatim.org/) (Geocoding) und [Overpass API](https://overpass-api.de/) (Gleisgeometrie)
- [Open-Meteo](https://open-meteo.com/) für stündliche Bewölkung
- [astral](https://astral.readthedocs.io/) für Sonnenstand

Bitte fair nutzen — Nominatim und Overpass sind freie Services mit Rate Limits.

## Hinweise

- `requirements.txt` listet die Python-Pakete für CLI/Tests; im Browser werden `astral` + `gpxpy` zur Laufzeit per `micropip` geladen.
- Wirklich offline rechnen geht nicht: Geocoding, OSM und Wetter brauchen Netz. Die App-Shell selbst (HTML, JS, Python-Module, Pyodide-Runtime) liegt aber komplett im Cache — das **Öffnen** ist offline möglich.
