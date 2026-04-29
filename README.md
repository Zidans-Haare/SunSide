# SunSide

**Auf welcher Seite sitze ich im Schatten?**

Gibt eine Links/Rechts-Empfehlung für Zug, Bus und Flugzeug — basierend auf Sonnenstand, Streckenführung und optionaler Bewölkung. Läuft als **Progressive Web App direkt im Browser** (keine Installation, kein Account).

[![CI](https://github.com/Zidans-Haare/SunSide/actions/workflows/ci.yml/badge.svg)](https://github.com/Zidans-Haare/SunSide/actions/workflows/ci.yml)

---

## Schnellstart

### Browser-App (empfohlen — keine Installation nötig)

```bash
# Repo klonen und lokalen Server starten — das war's
git clone https://github.com/Zidans-Haare/SunSide.git && cd SunSide
python3 -m http.server 8000
# → http://localhost:8000
```

### Setup-Wizard (Streamlit-UI, .env, Deployment konfigurieren)

```bash
./setup.sh
```

Der Wizard fragt nach Modus (Browser / Streamlit), legt `.env` an und startet den Server.

### Auf einem nackten Server (Debian/Ubuntu)

```bash
curl -fsSL https://raw.githubusercontent.com/Zidans-Haare/SunSide/main/setup.sh | bash
```

---

## Wie es funktioniert

```
Eingabe: Modus, Start, Ziel, Datum/Zeit (oder konkreter Trip aus dem Fahrplan)
    │
    ▼
Schicht 1 — Route-Provider
    Echte Gleisgeometrie (OSM Overpass) · Strassenrouting (OSRM)
    Flixbus-Fahrplan + Polyline (lokale GTFS-DB) · GPX-Upload · Grosskreis
    │
    ▼
Schicht 2 — Sonnenanalyse
    Bearing je Segment → Sonnenazimut + Sonnenhoehe (astral)
    → links / rechts / Nacht / Tunnel / Gelaendeschatten
    Optional gewichtet nach Bewoelkung (Open-Meteo) und/oder
    Sonnenintensitaet (sin(Hoehe))
    Optional Hoehenmodell (open-meteo) fuer Berg-/Talschatten
    Steroid-Modus: Intervall iterativ verkleinern bis Ergebnis konvergiert
    │
    ▼
Ausgabe: "Sitz links - ca. 88 % im Schatten"
```

Die Python-Logik laeuft entweder als Streamlit-App (Desktop, mit Flixbus-GTFS und Karte) oder via [Pyodide](https://pyodide.org/) im Browser (PWA, ohne Server).

---

## Projektstruktur

```
SunSide/
├── index.html                  # App-Einstiegspunkt (Browser/PWA)
├── app.js                      # Pyodide-Bootstrap + UI
├── sw.js                       # Service Worker (PWA-Caching)
├── manifest.webmanifest        # PWA-Manifest
├── assets/                     # Icons
│
├── app.py                      # Streamlit-App (Desktop, mit Flixbus-GTFS + Karte)
│
├── sunside/                    # Python-Kernlogik
│   ├── models.py               # RoutePoint (inkl. in_tunnel), SegmentAnalysis, Recommendation
│   ├── flight.py               # Grosskreis-Routen fuer Flugzeugmodus
│   ├── weather.py              # Open-Meteo-Integration (gecacht)
│   ├── http_cache.py           # SQLite-HTTP-Cache fuer alle Provider
│   ├── browser_api.py          # Browser-Entry (kein requests, Daten kommen per JS)
│   ├── sun_analysis/
│   │   ├── calculator.py       # bearing, haversine, Sonnenposition + Intensitaet
│   │   ├── sampler.py          # Resampling, Auto-Intervall (propagiert Tunnel-Flag)
│   │   └── analyzer.py         # Pipeline + Steroid-Modus (analyze_converged)
│   └── route_providers/
│       ├── osm.py              # Gleisgeometrie via Overpass API (mit Tunnel-Tags)
│       ├── osrm.py             # Strassenrouting
│       ├── gpx.py              # GPX-Datei-Parser
│       ├── nominatim.py        # Geocoding + Grosskreis-Fallback
│       ├── gtfs.py             # GTFS-Feed (Online-Variante, Stub)
│       └── gtfs_db.py          # Lokale GTFS-SQLite (Bus + Rail, mit Shapes/Polylines)
│
├── scripts/
│   ├── gtfs_import.py          # GTFS-ZIP -> SQLite Importer
│   └── examples/               # Manuelle Smoke-Tests, Interval-Sweeps
│
├── deploy/
│   ├── Caddyfile, deploy.sh, server-setup.md
│   └── update_gtfs.sh          # Cron-Skript fuer wöchentlichen GTFS-Refresh
│
├── data/
│   ├── routes/                 # Lokale GPX-Datenbank (gitignored)
│   ├── gtfs/*.sqlite           # Importierte GTFS-Feeds (Bus + Rail, gitignored)
│   └── http_cache.sqlite       # HTTP-Antwort-Cache (gitignored)
│
├── tests/                      # 53 pytest-Tests
└── legacy/                     # Alte Streamlit-/Trainings-Skripte (inaktiv)
```

---

## Reisemodi (Streamlit-App)

Die Sidebar laesst dich zwischen fuenf Modi waehlen — jeder hat seine eigene Provider-Kette:

| Modus | Provider | Quelle |
|-------|----------|--------|
| **Zug** | OSM Overpass | Echte Gleisgeometrie inkl. Kurven + Tunnel-Tags |
| **Auto/Bus** | OSRM | Strassenrouting mit Geometrie |
| **Fahrplan (GTFS)** | Lokale GTFS-SQLite (Flixbus, DB/Rail, OeBB, ...) | Konkreter Zug-/Bus-Trip mit Halten + Shape/Polyline |
| **Flugzeug** | Grosskreis (Luftlinie) | Geocoding via Nominatim |
| **GPX-Datei** | Upload | Eigene Tracks |

Der GTFS-Modus erkennt automatisch alle `*.sqlite`-Datenbanken unter `data/gtfs/`
und bietet sie zur Auswahl an. Stop-Suche, Trip-Auswahl und Shape-/Polyline-
Slicing laufen identisch fuer alle Feeds. Rail-GTFS ist dabei wichtig fuer
direkte Zuege wie Railjet/ICE: Es liefert echte Fahrzeiten pro Trip; OSM liefert
danach die feinere Gleisgeometrie bzw. bleibt Fallback, wenn der Feed keine
Shapes enthaelt.

Der Flugzeugmodus nutzt keine echte ATC-/Airway-Route, sondern eine lokale
Grosskreis-Approximation zwischen Start und Ziel. Das ist deutlich besser als
lat/lon-lineare Interpolation und fuer die Sitzseitenfrage oft ausreichend;
echte Flugtracks koennen spaeter als GPX/KML/API-Quelle ergaenzt werden.

---

## Steroid-Modus

Optionaler Schalter „Bis zur Konvergenz rechnen". Statt einem festen Intervall
halbiert die Pipeline schrittweise das Sampling-Intervall (2 km → 1 km → 500 m
→ … → 20 m), bis sich die Empfehlung zwei Iterationen lang um weniger als
0,5 % aendert. Verhindert Pseudo-Konvergenz auf groben Intervallen, die alle
Kurven uebersehen. Ein Trace-Expander zeigt jede Iteration mit Intervall,
Segmentanzahl und dominantem Anteil.

---

## Sonnenintensitaet

Schalter „Sonnenintensitaet gewichten". Statt jeden Sonnen-Segment gleich zu
zaehlen, multipliziert die Pipeline das Gewicht mit `max(0, sin(Hoehe))`:

- Mittagssonne (Hoehe ~60°, sin ≈ 0,87) zaehlt voll
- Abendsonne (Hoehe ~10°, sin ≈ 0,17) zaehlt nur ein Sechstel
- Sonne unterhalb des Horizonts: 0

Sinnvoll z.B. wenn die Strecke morgens und abends durch verschiedene
Himmelsrichtungen laeuft — die Mittagsphase dominiert dann das Ergebnis. Mit
Bewoelkungsgewichtung kombinierbar (beide Faktoren multiplizieren).

---

## Tunnel-Erkennung

Der OSM-Provider liest die `tunnel`-, `covered`- und `location=underground`-
Tags aus den Way-Geometrien. Knoten innerhalb solcher Wege werden auf den
RoutePoints als `in_tunnel=True` markiert; der Sampler propagiert das Flag
beim Resampling konservativ (ein Sample zwischen Tunnel- und Tagesknoten gilt
als Tunnel). Die Sonnenanalyse kategorisiert solche Segmente als `"tunnel"`,
sodass sie weder die Sonnenseite noch das Mittel beeinflussen. Der Anteil
wird im UI als `tunnel_pct` angezeigt.

Funktioniert aktuell nur fuer den OSM-Bahnmodus. Flixbus-Polylines enthalten
keine Tunnel-Information — dort werden alle Segmente weiterhin als oberirdisch
behandelt.

---

## Gelaendeschatten

Schalter „Gelaendeschatten beruecksichtigen". Pro Segment werden in
Sonnenrichtung Hoehensamples bis 8 km Entfernung abgefragt (Open-Meteo
Elevation API, gratis, kein Key, gecacht). Liegt der Horizontwinkel hoeher als
die Sonne, gilt das Segment als verschattet (`terrain_shaded=True`,
`sun_factor=0`). Effekt vor allem in Alpentaelern und tief eingeschnittenen
Flusslaeufen merkbar; der Anteil wird als `terrain_pct` ausgewiesen.

Der erste Lauf dauert spuerbar laenger (n Segmente x 1–2 API-Calls), Folgelaeufe
greifen auf den HTTP-Cache zu.

---

## HTTP-Caching

Alle Aufrufe an OSM Overpass, Nominatim, OSRM und Open-Meteo laufen ueber
`sunside.http_cache.cached_request`. Eine SQLite-Datei unter
`data/http_cache.sqlite` haelt erfolgreiche Antworten standardmaessig eine
Woche vor. Das verhindert Rate-Limit-Probleme beim Iterieren auf derselben
Strecke (z.B. bei Konvergenz-Sweeps oder Parametertests). Fehlerantworten
werden nicht gecacht.

Leeren via Python:

```python
from sunside.http_cache import clear_cache, cache_stats
print(cache_stats())
clear_cache()
```

---

## GTFS-Feeds einrichten (Bus + Rail)

```bash
# Einmalig: Feed importieren (~35 s, erzeugt ~360 MB SQLite)
mkdir -p data/gtfs
curl -L https://gtfs.gis.flix.tech/gtfs_generic_eu.zip -o /tmp/flixbus.zip
PYTHONPATH=. python scripts/gtfs_import.py --zip /tmp/flixbus.zip --db data/gtfs/flixbus.sqlite
```

Weitere Feeds (Beispiele, alle frei verfuegbar):

```bash
# Rail-GTFS / Deutschland (gtfs.de aggregiert Nah- und Fernverkehr,
# je nach Feed-Stand inkl. ICE/IC/RJ-Abschnitte und Shapes)
curl -L https://download.gtfs.de/germany/free/latest.zip -o /tmp/db.zip
PYTHONPATH=. python scripts/gtfs_import.py --zip /tmp/db.zip --db data/gtfs/db.sqlite --name db_rail

# OeBB / Railjet (Open Data)
# Den aktuellen GTFS-Link beim Anbieter holen, dann analog importieren:
# PYTHONPATH=. python scripts/gtfs_import.py --zip /tmp/oebb.zip --db data/gtfs/oebb.sqlite --name oebb
```

Alle `*.sqlite` unter `data/gtfs/` werden automatisch erkannt. Die Streamlit-
App zeigt einen Feed-Wechsler, sobald mehrere DBs vorhanden sind.

Ziel: Rail-GTFS soll fuer Zuege immer bevorzugt werden, wenn ein konkreter Trip
verfuegbar ist. Dann kommen Abfahrt, Ankunft und Zwischenhalte aus dem Fahrplan;
OSM-Gleisgeometrie bleibt fuer Kurven/Tunnel und als Fallback erhalten.

Auf dem Server laesst sich der Feed automatisch frisch halten (Cron, atomarer
Swap) — siehe `deploy/server-setup.md` Abschnitt 8 und `deploy/update_gtfs.sh`.

---

## PWA-Fahrplan-Modus (optional, Server noetig)

Die Browser-PWA kann den Fahrplan-Modus nutzen, wenn ein FastAPI-Endpoint
laeuft, der die GTFS-Datenbanken serviert (`server.py`). Lokal:

```bash
PYTHONPATH=. uvicorn server:app --host 0.0.0.0 --port 8001
```

Die PWA fragt standardmaessig `/api` am gleichen Host ab. Anderen Endpoint
setzen via:

```
https://sunside.example.com/?gtfs_api=https://api.example.com/api
```

(Der Wert landet in `localStorage`.) Server-Deployment + Caddy-Reverse-Proxy:
`deploy/server-setup.md` Abschnitt 9.

Wenn kein Endpoint erreichbar ist, bleibt der Fahrplan-Modus deaktiviert; alle
anderen Modi (Zug, Auto, Flugzeug, GPX) funktionieren ohne Server.

---

## Entwicklung

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=. pytest tests/ -q
PYTHONPATH=. streamlit run app.py # Desktop-UI mit Flixbus-GTFS
```

Die `scripts/examples/`-Skripte sind handgepruefte Smoke-Tests fuer reale
Strecken (Villach↔Padova, Udine↔Villach, Konvergenz-Sweeps) — gut als
Ausgangspunkt fuer eigene Experimente.

---

## Deployment

### GitHub Pages (automatisch bei Push auf `main`)

Der CI/CD-Workflow in `.github/workflows/deploy.yml` deployed die statische App automatisch auf GitHub Pages, sobald alle Tests grün sind.

Aktivierung: Repository → Settings → Pages → Source: **GitHub Actions**

### Hetzner (manuell)

```bash
# Einmalig: Server einrichten (Caddy, User, Verzeichnis)
# Anleitung: deploy/server-setup.md

# .env mit Server-IP anlegen
cp .env.example .env  # SSH_TARGET setzen

# Deployen
SSH_TARGET=root@deine-ip ./deploy/deploy.sh
```

---

## Datenquellen

| Quelle | Verwendung | API-Key |
|--------|------------|---------|
| [OSM Overpass](https://overpass-api.de/) | Gleisgeometrie | — |
| [Nominatim](https://nominatim.org/) | Geocoding | — |
| [OSRM](http://project-osrm.org/) | Strassenrouting | — |
| [Open-Meteo](https://open-meteo.com/) | Bewoelkung (optional) | — |
| [Flixbus GTFS](https://gtfs.gis.flix.tech/gtfs_generic_eu.zip) | Fahrplan + Polylines | — |
| [astral](https://astral.readthedocs.io/) | Sonnenstand + Hoehe (lokal) | — |

Alle Services kostenlos und ohne Registrierung. Bitte fair nutzen — Nominatim und Overpass haben Rate Limits.

---

## Bekannte Grenzen

- **Wagenrichtung bei Wendezuegen** kann das Ergebnis spiegeln — nicht
  detektierbar ohne externe Info
- **Tunnel** werden im OSM-Bahnmodus erkannt; in GTFS-/OSRM-/GPX-Routen
  noch nicht
- **Gelaendeschatten** nur 1. Ordnung (nahe Berge in Sonnenrichtung).
  Wolkenschatten, Daecher, etc. nicht modelliert
- **GTFS-Datenbanken** muessen lokal importiert sein; PWA braucht zusaetzlich
  einen API-Endpoint
- **Offline-Berechnung** nur eingeschraenkt: Geocoding, OSM, OSRM, Wetter und
  Hoehenmodell brauchen Netz. Die App-Shell selbst (HTML, JS, Python-Module,
  Pyodide) liegt im Service-Worker-Cache und startet offline.
