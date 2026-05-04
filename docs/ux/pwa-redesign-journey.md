# User Journey: Sitzseite waehlen

## Persona

- Who: Reisender mit reservierbarem oder frei waehlbarem Sitzplatz.
- Goal: links oder rechts waehlen, passend zu Schatten oder Sonne.
- Context: unterwegs auf dem Smartphone oder vorab am Desktop.
- Success Metric: Nutzer kann die Empfehlung samt Begruendung in unter einer Minute nachvollziehen.

## Stage 1: Reise erfassen

What user is doing: Reisemodus waehlen, Start/Ziel oder GTFS/GPX angeben.

What user is thinking: "Ich will schnell die richtige Route treffen, nicht erst Provider verstehen."

What user is feeling: Unsicher, wenn zu viele technische Begriffe gleichzeitig auftauchen.

Pain points:

- Alte UI mischte Modi, Provider-Hinweise und Eingaben in einem langen Stapel.
- GTFS und GPX wirkten wie Sonderfaelle statt gleichwertige Modi.

Opportunity:

- Reise als eigene Konfigurationsgruppe, mit klaren Mode-spezifischen Feldern.

## Stage 2: Analyse einstellen

What user is doing: Praeferenz, Wetter, Intensitaet, Gelaende, Intervall und Konvergenz setzen.

What user is thinking: "Wie genau soll die Berechnung sein, und was kostet Zeit?"

What user is feeling: Kontrolliert, wenn Optionen kompakt und konsistent sind.

Pain points:

- Einige Kernfunktionen waren in der PWA nicht erreichbar.
- Manuelles Intervall und Konvergenz waren nicht als Genauigkeitsentscheid sichtbar.

Opportunity:

- Analyseoptionen als eigene Sektion mit Schaltern und Intervallauswahl.

## Stage 3: Berechnen

What user is doing: Berechnung starten und Statusmeldungen verfolgen.

What user is thinking: "Laedt es noch, oder ist etwas kaputt?"

What user is feeling: Geduldig, solange Status klar und konkret bleibt.

Pain points:

- Pyodide, Provider und Open-Meteo brauchen spuerbare Zeit.
- Terrain kann viele Hoehenpunkte erzeugen.

Opportunity:

- Statusmeldungen fuer Runtime, Route, Wetter, Hoehendaten und Analyse.

## Stage 4: Entscheidung treffen

What user is doing: Empfehlung, Prozentwert, Karte und Metriken ansehen.

What user is thinking: "Kann ich dieser Empfehlung trauen?"

What user is feeling: Sicher, wenn Karte und Metriken die Empfehlung stuetzen.

Pain points:

- Nur eine grosse Empfehlung ohne Kontext ist schwer zu validieren.
- Segmentdaten waren vorhanden, aber nicht kuratiert.

Opportunity:

- Ergebnis als Workbench: Empfehlung zuerst, Metriken direkt darunter, Karte mit farbigen Segmenten, Detailtabellen optional.
