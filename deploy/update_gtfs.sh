#!/usr/bin/env bash
# SunSide GTFS auto-update.
#
# Downloads a GTFS feed, imports it into a fresh SQLite DB, then atomically
# swaps it in. Safe to run while the Streamlit app is live -
# the swap is a single rename(2), and the app re-opens the DB on its next
# cache miss (TTL 1h).
#
# Cron (every Wednesday at 03:17 UTC):
#   17 3 * * 3 /opt/sunside/deploy/update_gtfs.sh >> /var/log/sunside_gtfs.log 2>&1
#
# Configurable via env:
#   SUNSIDE_HOME   default: /opt/sunside
#   GTFS_NAME      default: flixbus (used for <name>.zip / <name>.sqlite)
#   GTFS_URL       default: Flixbus generic EU feed
#   PYTHON_BIN     default: /opt/sunside/.venv/bin/python

set -euo pipefail

SUNSIDE_HOME="${SUNSIDE_HOME:-/opt/sunside}"
GTFS_NAME="${GTFS_NAME:-flixbus}"
GTFS_URL="${GTFS_URL:-https://gtfs.gis.flix.tech/gtfs_generic_eu.zip}"
PYTHON_BIN="${PYTHON_BIN:-${SUNSIDE_HOME}/.venv/bin/python}"

case "${GTFS_NAME}" in
    *[!A-Za-z0-9._-]*|'')
        echo "ERROR: GTFS_NAME may only contain letters, numbers, dot, underscore and dash" >&2
        exit 1
        ;;
esac

DATA_DIR="${SUNSIDE_HOME}/data/gtfs"
LIVE_DB="${DATA_DIR}/${GTFS_NAME}.sqlite"
ZIP_TMP="${DATA_DIR}/${GTFS_NAME}.zip.tmp"
ZIP_LIVE="${DATA_DIR}/${GTFS_NAME}.zip"
DB_TMP="${DATA_DIR}/${GTFS_NAME}.sqlite.tmp"
DB_BAK="${DATA_DIR}/${GTFS_NAME}.sqlite.bak"

mkdir -p "${DATA_DIR}"

log() { echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"; }

log "downloading ${GTFS_URL}"
curl --fail --silent --show-error --location \
     --max-time 600 --retry 3 --retry-delay 30 \
     -o "${ZIP_TMP}" "${GTFS_URL}"

# Cheap sanity check: must be a non-trivial zip
if [ "$(stat -c%s "${ZIP_TMP}")" -lt 1000000 ]; then
    log "ERROR: downloaded zip is suspiciously small, aborting"
    rm -f "${ZIP_TMP}"
    exit 1
fi
unzip -t "${ZIP_TMP}" >/dev/null

mv -f "${ZIP_TMP}" "${ZIP_LIVE}"
log "import into ${DB_TMP}"
"${PYTHON_BIN}" "${SUNSIDE_HOME}/scripts/gtfs_import.py" \
    --zip "${ZIP_LIVE}" --db "${DB_TMP}" --name "${GTFS_NAME}"

# Atomic swap with backup
if [ -f "${LIVE_DB}" ]; then
    mv -f "${LIVE_DB}" "${DB_BAK}"
fi
mv -f "${DB_TMP}" "${LIVE_DB}"

log "swap done. live=$(stat -c%s "${LIVE_DB}") bytes."
log "ok."
