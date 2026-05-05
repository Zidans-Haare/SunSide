#!/usr/bin/env bash
# Laeuft auf dem Server — wird per SSH von CI/CD oder lokal aufgerufen.
# Voraussetzungen einmalig: siehe deploy/nginx-setup.md (Abschnitt "Updates einspielen")
set -euo pipefail

REPO=/opt/sunside
WEBROOT=/var/www/sunside

echo "[deploy] git pull"
cd "$REPO"
git pull --ff-only

echo "[deploy] pip install"
.venv/bin/pip install -r requirements.txt -q

echo "[deploy] sync static files"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.github/' \
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
  "$REPO/" "$WEBROOT/"

echo "[deploy] reload API"
sudo systemctl restart sunside-api

echo "[deploy] done — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
