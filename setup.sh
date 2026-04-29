#!/usr/bin/env bash
# SunSide — Setup & Dev-Wizard
# Einmaliger Aufruf: ./setup.sh
# Auf einem nackten Server: curl -fsSL https://raw.githubusercontent.com/Zidans-Haare/SunSide/main/setup.sh | bash
set -euo pipefail

CYAN='\033[0;36m' YELLOW='\033[1;33m' GREEN='\033[0;32m' RED='\033[0;31m' NC='\033[0m'
info()    { echo -e "${CYAN}▸ $*${NC}"; }
success() { echo -e "${GREEN}✓ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠ $*${NC}"; }
error()   { echo -e "${RED}✗ $*${NC}"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  ☀  SunSide Setup"
echo "  ════════════════"
echo ""

# ── 1. Python-Version prüfen ──────────────────────────────
if ! command -v python3 &>/dev/null; then
  warn "Python 3 nicht gefunden."
  if command -v apt-get &>/dev/null; then
    info "Installiere Python 3 via apt..."
    sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip python3-venv
  else
    error "Bitte Python 3.11+ manuell installieren: https://python.org"
  fi
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 11 ]]; then
  error "Python 3.11+ benötigt (gefunden: $PY_VER)"
fi
success "Python $PY_VER"

# ── 2. .env einrichten ────────────────────────────────────
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    info ".env aus .env.example erstellt."
    echo ""
    echo "  Konfiguration (Enter zum Überspringen):"
    echo ""

    read -rp "  Hetzner SSH-Target (z.B. root@1.2.3.4) [leer lassen wenn kein Server]: " SSH_TARGET_INPUT
    if [[ -n "$SSH_TARGET_INPUT" ]]; then
      sed -i.bak "s|SSH_TARGET=root@your-server-ip|SSH_TARGET=$SSH_TARGET_INPUT|" .env
      rm -f .env.bak
      success "SSH_TARGET gesetzt"
    fi
    echo ""
  fi
else
  success ".env bereits vorhanden"
fi

# ── 3. Modus wählen ───────────────────────────────────────
echo ""
echo "  Modus:"
echo "    1) Browser-App  — statischer HTTP-Server, kein pip install nötig"
echo "    2) Streamlit    — Python-Desktop-UI (installiert Abhängigkeiten)"
echo "    3) Nur Setup    — kein Server starten"
echo ""
read -rp "  Wahl [1/2/3, Standard 1]: " MODE
MODE="${MODE:-1}"

# ── 4. Abhängigkeiten installieren (nur Streamlit-Modus) ──
if [[ "$MODE" == "2" ]]; then
  info "Erstelle virtuelle Umgebung..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  info "Installiere Abhängigkeiten..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt streamlit folium streamlit-folium pytz pandas
  success "Pakete installiert"
fi

# ── 5. Starten ────────────────────────────────────────────
echo ""
if [[ "$MODE" == "1" ]]; then
  PORT="${PORT:-8000}"
  success "Starte HTTP-Server auf http://localhost:$PORT"
  echo ""
  python3 -m http.server "$PORT"
elif [[ "$MODE" == "2" ]]; then
  success "Starte Streamlit..."
  echo ""
  streamlit run app.py
else
  success "Setup abgeschlossen. Starten mit:"
  echo ""
  echo "    Browser-App:  python3 -m http.server 8000"
  echo "    Streamlit:    source .venv/bin/activate && streamlit run app.py"
  echo ""
fi
