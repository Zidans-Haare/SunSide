#!/usr/bin/env bash
# SunSide deploy script — laeuft auf deinem lokalen Rechner.
# Synchronisiert die statischen App-Dateien per rsync auf den Hetzner-Server.
#
# Voraussetzungen:
#   - SSH-Zugang zum Server (Key, kein Passwort)
#   - rsync lokal installiert (auf Windows via WSL oder Git-Bash)
#   - Server-Setup einmalig: siehe deploy/server-setup.md
#
# Usage:
#   SSH_TARGET=root@1.2.3.4 ./deploy/deploy.sh
#   oder ENV in dieser Datei setzen.

set -euo pipefail

SSH_TARGET="${SSH_TARGET:-root@your-hetzner-ip}"
REMOTE_DIR="${REMOTE_DIR:-/var/www/sunside}"

# Lokales Repo-Wurzelverzeichnis (Skript liegt in deploy/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"

echo "==> Deploye nach $SSH_TARGET:$REMOTE_DIR"

rsync -avz --delete \
	--exclude '.git/' \
	--exclude '.github/' \
	--exclude '__pycache__/' \
	--exclude '*.pyc' \
	--exclude 'legacy/' \
	--exclude 'deploy/' \
	--exclude 'data/' \
	--exclude 'CLAUDE.md' \
	--exclude '.gitignore' \
	--exclude 'requirements.txt' \
	./ "$SSH_TARGET:$REMOTE_DIR/"

echo "==> Cache invalidieren (Service Worker bumpt sich beim naechsten Reload selbst)"
ssh "$SSH_TARGET" "chown -R caddy:caddy $REMOTE_DIR && systemctl reload caddy || true"

echo "==> Fertig. Test:  curl -I https://<deine-domain>/"
