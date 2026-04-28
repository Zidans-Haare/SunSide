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

Dann:

```bash
curl -I https://sunside.deinedomain.de/
# erwartet: HTTP/2 200, Server-Header von Caddy entfernt
```

## 6. Cloudflare Proxy einschalten (optional)

Sobald die Seite über DNS-only läuft:

- In Cloudflare: SSL/TLS-Mode auf **„Full (strict)"** (NICHT „Flexible" — sonst Loop).
- Proxy-Wolke auf orange.

Caddy erneuert automatisch — Cloudflare proxyed danach.

## 7. Updates deployen

Nach jeder Code-Änderung lokal:

```bash
./deploy/deploy.sh
```

Der Service Worker ist auf `no-cache` gesetzt, damit User beim nächsten Reload die neue Version bekommen. Bei größeren Änderungen kannst du im `sw.js` die `CACHE`-Konstante hochzählen (`sunside-shell-v2` → `v3`), das forciert ein Neu-Caching der App-Shell.

## Troubleshooting

- **Zertifikat klappt nicht**: Cloudflare-Proxy ausschalten (graue Wolke), Caddy-Logs prüfen: `journalctl -u caddy -e`
- **403/404 auf Dateien**: `chown -R caddy:caddy /var/www/sunside`
- **`text/x-python` nicht akzeptiert**: Pyodide ist es egal, der MIME-Type ist nur Hygiene
- **CSP blockiert was**: Browser-DevTools → Console zeigt's; im Caddyfile `Content-Security-Policy` anpassen
