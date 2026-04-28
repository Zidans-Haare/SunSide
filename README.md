# SunSide ☀

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
Eingabe: Start, Ziel, Abfahrtszeit
    │
    ▼
Schicht 1 — Route-Provider
    Echte Gleisgeometrie (OSM Overpass) · Straßenrouting (OSRM)
    GPX-Upload · Luftlinie
    │
    ▼
Schicht 2 — Sonnenanalyse
    Bearing je Segment → Sonnenazimut (astral) → links/rechts/Nacht
    Optionale Gewichtung per Bewölkung (Open-Meteo)
    │
    ▼
Ausgabe: "Sitz links — ca. 75 % im Schatten"
```

Die Python-Logik läuft via [Pyodide](https://pyodide.org/) vollständig im Browser — kein Server, kein Backend.

---

## Projektstruktur

```
SunSide/
├── index.html                  # App-Einstiegspunkt
├── app.js                      # Pyodide-Bootstrap + UI
├── sw.js                       # Service Worker (PWA-Caching)
├── manifest.webmanifest        # PWA-Manifest
├── assets/                     # Icons
│
├── sunside/                    # Python-Kernlogik (von Pyodide geladen)
│   ├── models.py               # RoutePoint, SegmentAnalysis, Recommendation
│   ├── weather.py              # Open-Meteo-Integration
│   ├── browser_api.py          # Browser-Entry (kein requests, Daten kommen per JS)
│   ├── sun_analysis/
│   │   ├── calculator.py       # bearing, haversine, Sonnenposition, Segment-Analyse
│   │   ├── sampler.py          # Resampling, Auto-Intervall
│   │   └── analyzer.py        # Haupt-Pipeline
│   └── route_providers/
│       ├── osm.py              # Gleisgeometrie via Overpass API
│       ├── osrm.py             # Straßenrouting
│       ├── gpx.py              # GPX-Datei-Parser
│       ├── nominatim.py        # Geocoding + Luftlinie
│       └── gtfs.py             # GTFS-Feed (in Entwicklung)
│
├── app.py                      # Streamlit-App (Desktop-Alternative)
├── tests/                      # Unit Tests (pytest)
├── deploy/                     # Caddy-Config + Hetzner-Deploy-Script
├── data/routes/                # Lokale GPX-Datenbank (gitignored außer .gitkeep)
└── legacy/                     # Alte Streamlit-/Trainings-Skripte (nicht mehr aktiv)
```

---

## Entwicklung

```bash
# Venv + Test-Abhängigkeiten
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Tests
pytest tests/ -v

# Mit Coverage
pytest tests/ --cov=sunside --cov-report=term-missing
```

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
| [OSRM](http://project-osrm.org/) | Straßenrouting | — |
| [Open-Meteo](https://open-meteo.com/) | Bewölkung (optional) | — |
| [astral](https://astral.readthedocs.io/) | Sonnenstand (lokal) | — |

Alle Services kostenlos und ohne Registrierung. Bitte fair nutzen — Nominatim und Overpass haben Rate Limits.

---

## Bekannte Grenzen

- **Tunnels** werden nicht berücksichtigt (Sonne irrelevant)
- **GTFS** (Fahrplan-Routen) ist noch in Entwicklung
- **Offline-Berechnung** nicht möglich: Geocoding, OSM und Wetter brauchen Netz. Die App-Shell selbst (HTML, JS, Python-Module, Pyodide) liegt im Service-Worker-Cache und startet offline.
