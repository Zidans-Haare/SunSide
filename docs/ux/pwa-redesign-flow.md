# SunSide PWA Redesign - Flow Specification

## User Flow

Entry Point: Nutzer oeffnet die PWA im Browser oder als installierte App.

Flow Steps:

1. Reise konfigurieren
   - Reisemodus waehlen: Zug, Auto/Bus, Fahrplan, Flugzeug, GPX.
   - Mode-spezifische Felder anzeigen.
   - Start/Ziel per Autocomplete oder GPX/GTFS eingeben.

2. Zeit setzen
   - Datum und Uhrzeit setzen.
   - Bei nicht fahrplanbasierten Modi Reisedauer einstellen.

3. Analyse konfigurieren
   - Praeferenz Sonne/Schatten waehlen.
   - Messintervall automatisch oder manuell setzen.
   - Optionale Gewichtungen aktivieren: Bewoelkung, Sonnenintensitaet, Gelaendeschatten.
   - Konvergenzmodus aktivieren, wenn Genauigkeit wichtiger als Laufzeit ist.

4. Berechnen
   - Route bauen.
   - Wetter- und Hoehendaten bei Bedarf vorladen.
   - Analyse in Pyodide ausfuehren.

5. Ergebnis pruefen
   - Empfehlung und Prozentwert lesen.
   - Kennzahlen pruefen: Intervall, Segmente, Bewoelkung, Sonnenhoehe, Tunnel, Gelaende.
   - Karte mit Segmentfarben ansehen.
   - Konvergenztrace und Segmenttabelle bei Bedarf oeffnen.

Exit Points:

- Success: Nutzer entscheidet sich fuer links oder rechts.
- Partial: Nutzer korrigiert Modus, Zeit oder Analyseoptionen und berechnet neu.
- Blocked: Provider/API nicht erreichbar oder Terrainabfrage zu gross; Statusmeldung fuehrt zur naechsten sinnvollen Anpassung.

## Design Principles

1. Decision first: Empfehlung und Prozentwert stehen visuell vor technischen Details.
2. Operational density: keine Landingpage, keine Marketing-Sektion, sondern direkt nutzbare Arbeitsoberflaeche.
3. Progressive evidence: Karte, Metriken und Tabellen erklaeren die Empfehlung, ohne sie zu verdecken.
4. Equal mode treatment: GTFS und GPX sind regulaere Reisemodi, keine versteckten Sonderfaelle.
5. Mobile readiness: einspaltiger Flow, 48px Controls auf kleinen Screens, Safe-Area-Abstaende, stabile Karten- und Tabellenbereiche.

## Accessibility Requirements

- Alle Eingaben und Schalter haben sichtbare Labels.
- Interaktive Elemente haben mindestens 44px Hoehe oder klaren Fokusbereich.
- Fokusindikator ist sichtbar und nicht nur farbbasiert.
- Ergebnisbereich nutzt `aria-live`, damit neue Ergebnisse angekuendigt werden koennen.
- Tabellen haben echte `thead`/`tbody`-Struktur.
- Farbige Kartensegmente werden zusaetzlich durch Legende und Tooltips benannt.
- Mobile PWA pruefen mit Browserleisten, Standalone-Modus, Rotation und kleinen Viewports ab 360px Breite.

## Figma Handoff

- Primary layout: Desktop zwei Spalten, links Konfiguration, rechts Ergebnis-Workbench.
- Mobile layout: Einspaltig, Konfiguration zuerst, Ergebnis danach.
- Result hierarchy: Empfehlung, Kennzahlen, Karte, Details.
- Visual tone: ruhig, utilitaristisch, deploy-faehig; keine dekorativen Illustrationen.
