#!/usr/bin/env bash
# Sync lokale Dateien nach /var/www/sunside und API neu starten.
# Kein git pull — nutzen wenn deploy.sh wegen SSH-Key scheitert.
set -euo pipefail

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
  /opt/sunside/ /var/www/sunside/

systemctl restart sunside-api
echo "done — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
