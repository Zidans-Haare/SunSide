# SunSide PWA Redesign - JTBD

## Annahmen zu den Nutzern

- Reisende in Zug, Fernbus oder Flugzeug, die vor oder waehrend der Reise schnell eine Sitzseiten-Entscheidung treffen wollen.
- Skill-Level: technisch gemischt; die App muss ohne Fachwissen zu Azimut, Overpass, GTFS oder Sampling bedienbar sein.
- Primaeres Geraet: Smartphone unterwegs, zusaetzlich Desktop beim Planen oder Testen.
- Kontext: kurz vor Buchung, am Bahnsteig, im Busbahnhof oder beim Einsteigen; oft mit wenig Zeit und wechselnder Netzqualitaet.
- Risiko bei Fehlern: meist Komfortverlust, bei langen Reisen aber spuerbar aergerlich.

## Job Statement

Wenn ich eine konkrete Reise plane oder gleich einsteige, will ich aus Start, Ziel, Zeit und Reisemodus eine klare Links/Rechts-Empfehlung bekommen, damit ich ohne Nachdenken die angenehmere Sitzseite waehlen kann.

## Current Solution & Pain Points

- Current: Sonne grob aus Himmelsrichtung schaetzen, Karten-App oeffnen, Route mental drehen, Wetter separat pruefen.
- Pain: zu viele Einzelinformationen, keine Kurvenlogik, keine Fahrplan-/GPX-Route, keine klare Entscheidung.
- Consequence: Nutzer ignorieren die App, wenn sie wie ein Technikformular wirkt oder nicht alle Analyseoptionen erreichbar sind.

## Product Job

SunSide wird fuer eine schnelle, belastbare Sitzseitenentscheidung beauftragt. Die UI muss daher zuerst Entscheidungssicherheit herstellen und technische Details erst danach als pruefbare Evidenz zeigen.

## Success Criteria

- Nutzer erkennt innerhalb weniger Sekunden, welchen Reisemodus und welche Eingaben er braucht.
- Alle Kernfunktionen sind erreichbar: OSM, OSRM, GTFS, Flug/Grosskreis, GPX, Wetter, Intensitaet, Gelaende, Intervall, Konvergenz.
- Ergebnis zeigt Empfehlung, Prozentwert, Route und relevante Gewichtungen ohne Scroll-Suche.
- Segmentdetails und Konvergenz bleiben sichtbar fuer Validierung, dominieren aber nicht den ersten Blick.
