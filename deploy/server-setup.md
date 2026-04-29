# Server-Setup (Hetzner Ubuntu + Cloudflare DNS + Caddy)

Einmalig auf dem Server ausführen. Domain unten überall durch deine ersetzen.

## 1. Cloudflare DNS

- A-Record `sunside.deinedomain.de` → Hetzner-IPv4
- (optional) AAAA-Record → Hetzner-IPv6
- **Proxy auf „DNS only" (graue Wolke)** für die initiale Zertifikat-Ausstellung. Nach erfolgreicher Ausstellung kannst du den Proxy einschalten — siehe Hinweis ganz unten.

## 2. Caddy installieren

```bash
sudo apt update
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list

sudo apt update
sudo apt install -y caddy
```

Caddy läuft danach schon als systemd-Dienst (`systemctl status caddy`).

## 3. Webroot anlegen

```bash
sudo mkdir -p /var/www/sunside /var/log/caddy
sudo chown -R caddy:caddy /var/www/sunside /var/log/caddy
```

## 4. Caddyfile installieren

```bash
# Die mitgelieferte Caddyfile aus dem Repo kopieren …
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile

# … dann die Domain anpassen:
sudo nano /etc/caddy/Caddyfile          # `sunside.example.com` ersetzen

# Konfiguration validieren und neu laden
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy holt sich automatisch ein **Let's-Encrypt-Zertifikat** beim ersten HTTPS-Request. Voraussetzung: Ports **80 und 443** offen (UFW / Hetzner-Firewall prüfen).

```bash
sudo ufw allow 80,443/tcp 2>/dev/null || true
```

## 5. Erste Files hochladen

Lokal (auf deinem Rechner, im Repo-Root):

```bash
SSH_TARGET=root@deine-hetzner-ip ./deploy/deploy.sh
```

Das Skript ist fuer die statische PWA unter `/var/www/sunside`. Es schliesst
`deploy/`, `data/` und `requirements.txt` bewusst aus, damit keine lokalen
GTFS- oder Python-Artefakte im Webroot landen.

```bash
curl -I https://sunside.deinedomain.de/
# erwartet: HTTP/2 200, Server-Header von Caddy entfernt
```

## 6. Cloudflare Proxy einschalten (optional)

Sobald die Seite ueber DNS-only laeuft:

- In Cloudflare: SSL/TLS-Mode auf **„Full (strict)"** (NICHT „Flexible" — sonst Loop).
- Proxy-Wolke auf orange.

Caddy erneuert automatisch — Cloudflare proxyed danach.

## 7. Updates deployen

Nach jeder Code-Aenderung lokal:

```bash
./deploy/deploy.sh
```

Der Service Worker ist auf `no-cache` gesetzt, damit User beim naechsten Reload die neue Version bekommen. Bei groesseren Aenderungen kannst du im `sw.js` die `CACHE`-Konstante hochzaehlen (`sunside-shell-v2` → `v3`), das forciert ein Neu-Caching der App-Shell.

## 8. GTFS-Feeds automatisch aktualisieren (Bus + Rail)

Dieser Abschnitt gilt nur fuer den Python-/GTFS-Betrieb unter `/opt/sunside`.
`deploy/deploy.sh` kopiert diesen Ordner nicht; nutze dafuer z.B. ein separates
`git clone` oder einen eigenen Service-Deploy.

Auf dem Server:

```bash
# Einmalig ein Python-venv im SunSide-Ordner anlegen, falls noch nicht vorhanden
cd /opt/sunside
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Erste manuelle Initial-Befuellung (~30-60 s, ~360 MB DB)
mkdir -p data/gtfs
deploy/update_gtfs.sh

# Weiterer Rail-GTFS-Feed, z.B. DB/Deutschland ueber gtfs.de
GTFS_NAME=db_rail \
GTFS_URL=https://download.gtfs.de/germany/free/latest.zip \
deploy/update_gtfs.sh

# Cron einrichten: jeden Mittwoch 03:17 UTC Flixbus neu laden
sudo chmod +x /opt/sunside/deploy/update_gtfs.sh
( crontab -l 2>/dev/null; echo "17 3 * * 3 /opt/sunside/deploy/update_gtfs.sh >> /var/log/sunside_gtfs.log 2>&1" ) | crontab -

# Optional: Rail-GTFS separat aktualisieren
( crontab -l 2>/dev/null; echo "47 3 * * 3 GTFS_NAME=db_rail GTFS_URL=https://download.gtfs.de/germany/free/latest.zip /opt/sunside/deploy/update_gtfs.sh >> /var/log/sunside_gtfs_db_rail.log 2>&1" ) | crontab -
```

Was macht das Skript?

1. Laedt den per `GTFS_URL` gesetzten Feed herunter (Default: Flixbus EU).
2. Validiert das Zip (Mindestgroesse + `unzip -t`).
3. Importiert in eine **neue** Datei `${GTFS_NAME}.sqlite.tmp`.
4. Tauscht atomar mit der bestehenden `${GTFS_NAME}.sqlite` (alt -> `.bak`).
5. Streamlit-App nimmt die neue DB beim naechsten Cache-Refresh (TTL 1 h) auf.

Variablen (im Cron-Eintrag setzbar):

- `SUNSIDE_HOME` (Default `/opt/sunside`)
- `GTFS_NAME` (Default `flixbus`; z.B. `db_rail`, `oebb`)
- `GTFS_URL` (Default Flixbus EU-Feed)
- `PYTHON_BIN` (Default `${SUNSIDE_HOME}/.venv/bin/python`)


## 9. GTFS-API-Server fuer den PWA-Fahrplan-Modus

Damit die Browser-PWA Trips suchen und Polylines abrufen kann, laeuft
`server.py` als FastAPI-Service hinter Caddy. Die SQLite-Datenbanken aus
Schritt 8 werden read-only gelesen.

### systemd-Unit

Datei `/etc/systemd/system/sunside-api.service`:

```ini
[Unit]
Description=SunSide GTFS API
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/sunside
Environment=PYTHONPATH=/opt/sunside
ExecStart=/opt/sunside/.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8001
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sunside-api
sudo systemctl status sunside-api
```

### Caddy-Reverse-Proxy

In `deploy/Caddyfile` einen Block fuer `/api/*` ergaenzen:

```
sunside.deinedomain.de {
    handle /api/* {
        reverse_proxy 127.0.0.1:8001
    }
    handle {
        root * /var/www/sunside
        file_server
    }
}
```

`sudo systemctl reload caddy`.

### PWA-Endpoint setzen

Default ist `/api` (gleicher Host). Wenn der Server unter einer anderen Domain
laeuft, kann man das einmalig per URL-Parameter speichern:

```
https://sunside.example.com/?gtfs_api=https://api.example.com/api
```

Der Wert landet in `localStorage` (`sunside_gtfs_api`). Alternativ in
`index.html` global `window.SUNSIDE_GTFS_API = "..."` setzen.


## Troubleshooting

- **Zertifikat klappt nicht**: Cloudflare-Proxy ausschalten (graue Wolke), Caddy-Logs prüfen: `journalctl -u caddy -e`
- **403/404 auf Dateien**: `chown -R caddy:caddy /var/www/sunside`
- **`text/x-python` nicht akzeptiert**: Pyodide ist es egal, der MIME-Type ist nur Hygiene
- **CSP blockiert was**: Browser-DevTools → Console zeigt's; im Caddyfile `Content-Security-Policy` anpassen
