# SunSide — Architektur & Entscheidungen

## Ziel

Sag mir, auf welcher Seite (links/rechts) ich im Zug oder Bus sitzen soll, damit ich
möglichst viel im Schatten (oder in der Sonne) sitze.

Input: Start, Ziel, Abfahrtszeit  
Output: "Sitz **links** — ca. 75 % im Schatten"

Anwendungsfälle: ICE, Regionalzug, Flixbus, Railjet, ÖBB — alles was eine definierbare Route hat.

---

## Architektur: Zwei saubere Schichten

```
Schicht 1: Route-Provider          → liefert List[RoutePoint] (lat, lon, timestamp)
Schicht 2: Sonnenanalyse           → berechnet Empfehlung aus den Punkten
```

Schicht 2 ist vollständig unabhängig von Schicht 1. Egal ob die Route aus OSM,
GTFS, GPX oder manuellen Koordinaten kommt — die Analyse läuft identisch.

---

## Schicht 1: Route-Provider

### Priorität der Provider (beste zuerst)

1. **OSM Overpass API** — echte Gleisgeometrie mit allen Kurven, kostenlos, kein Key
   - Query: `way[railway=rail]` entlang des Korridors zwischen zwei Stationen
   - Ideal für Bahn (ICE, Railjet, S-Bahn, U-Bahn)

2. **GTFS-Feed** — Haltestellensequenz + Koordinaten + Fahrzeiten
   - DB und Flixbus publizieren offizielle GTFS-Daten (kostenlos)
   - Gibt Haltestellen, aber keine Gleiskurven → Interpolation zwischen Halten
   - Gut genug für 90 % der Fälle

3. **OSRM / GraphHopper** — Straßenrouting für Busse
   - Gibt echte Straßengeometrie
   - Für Flixbus, Regionalbus etc.

4. **GPX-Upload** — manuell einpflegbare Routen, maximale Flexibilität
   - Fallback für alles Exotische
   - Lokale Datenbank mit einmal eingepflegten Lieblingsrouten

### Nicht mehr verwenden
- `pyhafas` / DB HAFAS API — war fragil, rate-limited, jetzt ignoriert
- Google Maps — kostet Geld, unnötig

---

## Schicht 2: Sonnenanalyse

### Kernprinzip (3 Schritte)

1. **Bearing** zwischen zwei aufeinanderfolgenden Punkten (Himmelsrichtung der Fahrt)
2. **Sonnenazimut** am jeweiligen Punkt zur jeweiligen Zeit (`astral`-Bibliothek, reine Astronomie, kein API)
3. **Differenz** → `(sun_azimuth - bearing) % 360` → `< 180` = Sonne rechts, `>= 180` = Sonne links

Die Sonne steht am Messpunkt, nicht am Start- oder Zielort.

### Sampling-Intervall (Hebel 1)

Das Intervall bestimmt die Genauigkeit bei kurvenreichen Strecken.
Die Sonne bewegt sich nur ~0,25°/Minute → Sonnenbewegung ist fast egal.
Was zählt: Bearing-Wechsel durch Kurven der Strecke.

**Auto-Intervall-Logik** (in `sampler.py`):
- Grob-Route laden (z.B. aus GTFS, niedrige Auflösung)
- Bearing-Varianz berechnen (Standardabweichung der Kurswinkel-Deltas)
- Hohe Varianz (kurvenreich, z.B. Tirol, Rheintal) → 200–500 m vorschlagen
- Niedrige Varianz (gerade, z.B. NRW, Norddeutschland) → 5–50 km vorschlagen
- Schwellwerte: stddev < 5° → 20 km, < 15° → 2 km, > 15° → 300 m

Der Nutzer kann den Vorschlag überschreiben.

---

## Datenstrukturen

Siehe `sunside/models.py`:
- `RoutePoint(lat, lon, timestamp)`
- `SegmentAnalysis(point, bearing, sun_azimuth, sun_elevation, sun_side)`
- `Recommendation(shade_side, sun_side, shade_pct, sun_pct, segments, auto_interval_m)`

---

## Abhängigkeiten (minimal halten)

```
astral          — Sonnenstand (astronomische Berechnung, kein API)
geopy           — Geocoding via Nominatim (kein API-Key nötig)
streamlit       — UI
folium          — Kartenvisualisierung
streamlit-folium
requests        — OSM Overpass API calls
gpxpy           — GPX-Datei parsen
pandas          — Tabellen im UI
```

---

## Nächste Schritte (wenn am Rechner)

1. **OSM Overpass Provider** implementieren — echte Gleisgeometrie zwischen zwei Stationen
2. **GTFS Provider** implementieren — DB + Flixbus Feeds einbinden
3. **GPX Provider** fertig stellen — inkl. lokaler Routen-Datenbank
4. **Streamlit App** ausbauen — Provider-Auswahl, Kartendarstellung, Segment-Coloring
5. **Realtest**: Flixbus Villach → irgendwo, Bahnfahrt zum Vergleich

---

## Bekannte Grenzen

- Gerade Strecken: Luftlinie-Approximation ist fast perfekt
- Kurvenreiche Strecken (Alpen etc.): echte OSM-Gleisgeometrie nötig
- Tunnels: werden nicht berücksichtigt (Sonne egal im Tunnel)
- Wetter/Bewölkung: optional via Open-Meteo gewichtet; bei starker Bewölkung wird die Sitzseite weniger wichtig
