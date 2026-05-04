# SunSide — Deployment auf sun.olomek.com (nginx)

Dieses Dokument ist für Claude Code auf dem Server geschrieben.
Alle Befehle werden als `root` ausgeführt (oder mit `sudo`).
nginx ist bereits vorhanden und läuft.

---

## 1. Repo klonen

```bash
git clone https://github.com/Zidans-Haare/SunSide.git /opt/sunside
cd /opt/sunside
```

---

## 2. Python-Umgebung einrichten

```bash
python3 -m venv /opt/sunside/.venv
/opt/sunside/.venv/bin/pip install --upgrade pip
/opt/sunside/.venv/bin/pip install -r /opt/sunside/requirements.txt
```

---

## 3. GTFS-Daten herunterladen und importieren

Alle drei Feeds nacheinander. Flixbus (~30 MB), DB (~240 MB), ÖBB (~170 MB).
Dauert zusammen ca. 2–5 Minuten je nach Leitung.

```bash
cd /opt/sunside
mkdir -p data/gtfs

# Flixbus
.venv/bin/python scripts/fetch_gtfs.py flixbus

# Deutsche Bahn (Deutschland gesamt)
.venv/bin/python scripts/fetch_gtfs.py db

# ÖBB (Österreich)
.venv/bin/python scripts/fetch_gtfs.py oebb
```

Ergebnis prüfen:

```bash
ls -lh /opt/sunside/data/gtfs/
# erwartet: flixbus.sqlite (~360 MB), db.sqlite (~1.2 GB), oebb.sqlite (~770 MB)
```

---

## 4. FastAPI-Server als systemd-Dienst einrichten

```bash
cat > /etc/systemd/system/sunside-api.service << 'EOF'
[Unit]
Description=SunSide GTFS API
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/sunside
Environment=PYTHONPATH=/opt/sunside
ExecStart=/opt/sunside/.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8001 --workers 2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Besitzer setzen
chown -R www-data:www-data /opt/sunside/data

systemctl daemon-reload
systemctl enable --now sunside-api
systemctl status sunside-api
```

API-Test:

```bash
curl http://127.0.0.1:8001/api/gtfs/feeds
# erwartet: {"feeds":[{"name":"db",...},{"name":"flixbus",...},{"name":"oebb",...}]}
```

---

## 5. Statische Dateien bereitstellen

```bash
mkdir -p /var/www/sunside

rsync -a --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'legacy/' \
  --exclude 'deploy/' \
  --exclude 'data/' \
  --exclude '.venv/' \
  --exclude 'tests/' \
  --exclude 'scripts/' \
  --exclude 'docs/' \
  --exclude 'CLAUDE.md' \
  --exclude '.gitignore' \
  /opt/sunside/ /var/www/sunside/

chown -R www-data:www-data /var/www/sunside
```

---

## 6. nginx konfigurieren

```bash
cat > /etc/nginx/sites-available/sunside << 'EOF'
server {
    listen 80;
    server_name sun.olomek.com;
    # Weiterleitung auf HTTPS (nach Zertifikat-Einrichtung aktiv)
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name sun.olomek.com;

    # Zertifikat — von certbot automatisch eingetragen:
    ssl_certificate     /etc/letsencrypt/live/sun.olomek.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sun.olomek.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    root /var/www/sunside;
    index index.html;

    # --- GTFS-API: /api/* → FastAPI auf Port 8001 ---
    location /api/ {
        proxy_pass         http://127.0.0.1:8001;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }

    # --- Statische Dateien ---
    location / {
        try_files $uri $uri/ /index.html;
    }

    # Service Worker nie cachen
    location = /sw.js {
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Pragma no-cache;
        expires 0;
    }

    # index.html und App-Shell kurz cachen
    location ~* ^/(index\.html|app\.js|manifest\.webmanifest|sunside/)$ {
        add_header Cache-Control "no-cache";
    }

    # Icons und Assets lang cachen
    location /assets/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # MIME-Types für Pyodide-Module
    types {
        text/x-python py;
        application/manifest+json webmanifest;
    }

    # Security-Header
    add_header X-Content-Type-Options "nosniff";
    add_header Referrer-Policy "strict-origin-when-cross-origin";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains";

    access_log /var/log/nginx/sunside.access.log;
    error_log  /var/log/nginx/sunside.error.log;
}
EOF

ln -sf /etc/nginx/sites-available/sunside /etc/nginx/sites-enabled/sunside
nginx -t
```

---

## 7. TLS-Zertifikat holen (certbot)

Falls certbot noch nicht installiert:

```bash
apt install -y certbot python3-certbot-nginx
```

Zertifikat ausstellen (nginx muss laufen und Port 80 erreichbar sein):

```bash
certbot --nginx -d sun.olomek.com --non-interactive --agree-tos -m nickolomek@icloud.com
```

Danach nginx neu laden:

```bash
nginx -t && systemctl reload nginx
```

Test:

```bash
curl -I https://sun.olomek.com/
# erwartet: HTTP/2 200
curl https://sun.olomek.com/api/gtfs/feeds | python3 -m json.tool
# erwartet: 3 Feeds
```

---

## 8. GTFS automatisch aktualisieren (Cron)

Feeds ändern sich alle paar Monate. Jeden Mittwoch um 03:00 UTC neu laden:

```bash
cat > /etc/cron.d/sunside-gtfs << 'EOF'
# SunSide GTFS-Feeds wöchentlich aktualisieren
0 3 * * 3 www-data cd /opt/sunside && .venv/bin/python scripts/fetch_gtfs.py flixbus >> /var/log/sunside_gtfs.log 2>&1
30 3 * * 3 www-data cd /opt/sunside && .venv/bin/python scripts/fetch_gtfs.py db >> /var/log/sunside_gtfs.log 2>&1
0 4 * * 3 www-data cd /opt/sunside && .venv/bin/python scripts/fetch_gtfs.py oebb >> /var/log/sunside_gtfs.log 2>&1
EOF
```

---

## 9. Updates einspielen

Wenn neuer Code auf GitHub ist:

```bash
cd /opt/sunside
git pull

# Python-Pakete ggf. aktualisieren
.venv/bin/pip install -r requirements.txt

# Statische Dateien neu synchronisieren
rsync -a --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'legacy/' \
  --exclude 'deploy/' \
  --exclude 'data/' \
  --exclude '.venv/' \
  --exclude 'tests/' \
  --exclude 'scripts/' \
  --exclude 'docs/' \
  --exclude 'CLAUDE.md' \
  --exclude '.gitignore' \
  /opt/sunside/ /var/www/sunside/

chown -R www-data:www-data /var/www/sunside

# API-Server neu starten (falls server.py geändert)
systemctl restart sunside-api
systemctl reload nginx
```

---

## Troubleshooting

| Problem | Ursache | Fix |
|---|---|---|
| `502 Bad Gateway` auf `/api/` | FastAPI läuft nicht | `systemctl status sunside-api` → `journalctl -u sunside-api -n 50` |
| Feeds leer im Browser | falscher API-Endpunkt | `curl https://sun.olomek.com/api/gtfs/feeds` testen |
| SW cached alte Version | Cache-Version zu alt | In `sw.js` `CACHE`-Konstante hochzählen, dann deploy |
| 404 auf `.py`-Dateien | nginx MIME-Type fehlt | `types`-Block in nginx-Config prüfen |
| GTFS-Import schlägt fehl | ZIP-Struktur geändert | `python scripts/fetch_gtfs.py --force flixbus` |
| Zertifikat läuft ab | certbot-Timer inaktiv | `systemctl status certbot.timer` → ggf. `certbot renew` manuell |
