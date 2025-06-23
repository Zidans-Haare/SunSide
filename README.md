# SunSide

SunSide ist eine Streamlit-Anwendung, die je nach Zugstrecke und Sonnenstand empfiehlt, auf welcher Wagenseite man besser sitzt. Daneben enthält das Projekt eine kleine Flask-Webseite, auf der man Start- und Zielbahnhof eingibt und sofort eine Empfehlung erhält.

## Nutzung

Nach dem Klonen des Repositories lassen sich die benötigten Abhängigkeiten installieren und die Anwendungen starten:

```bash
pip install -r requirements.txt
streamlit run sun_side_app.py  # Streamlit App
python server.py                # Flask-Webseite
```

Weitere Informationen sowie eine kurze Vorstellung der Funktionen finden sich auf der [Projektwebseite](./index.html).
