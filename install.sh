#!/bin/bash
# install.sh - LLM Framework Enterprise Installer
# DIREKTIVE: Goldstandard. Vereint System-Checks, Docker-Setup und App-Deployment.
# Code und Python-Umgebung landen in /opt/llm-conversion-framework (dem aufrufenden Benutzer übergeben). Das
# Framework legt Builds, Cache und Modelle dort an, wenn sie gebraucht werden (Einstellungen output_dir,
# cache_dir, models_dir); persönlicher Zustand liegt in ~/.llm-framework des Benutzers.

set -u

# --- KONFIGURATION ---
APP_NAME="llm-conversion-framework"
INSTALL_DIR="/opt/$APP_NAME"
REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
# Paketindex, den pip erreichen muss (PIP_INDEX_URL, falls ein eigener Spiegel gesetzt ist)
PACKAGE_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple/}"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# --- HELFER ---
log() { echo -e "[$(date +'%H:%M:%S')] $1"; }
log_info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
log_success() { echo -e "${GREEN}✅ $1${NC}"; }
log_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
log_error() { echo -e "${RED}❌ $1${NC}"; exit 1; }

check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_warn "Installation in Systemverzeichnisse erfordert Root-Rechte."
        log_info "Bitte starte das Skript mit sudo neu: sudo ./install.sh"
        exit 1
    fi
}

wait_for_internet() {
    # HTTPS zum Paketindex statt ping: ICMP ist in vielen Netzen gesperrt, und pip braucht genau diesen Weg.
    log_info "Prüfe Internetverbindung (HTTPS zu $PACKAGE_INDEX_URL)..."
    if command -v curl &> /dev/null; then
        curl -fsS --max-time 10 -o /dev/null "$PACKAGE_INDEX_URL" && return 0
    elif command -v wget &> /dev/null; then
        wget -q --timeout=10 --tries=1 -O /dev/null "$PACKAGE_INDEX_URL" && return 0
    else
        log_warn "Weder curl noch wget gefunden; die Internetprüfung wird übersprungen."
        return 0
    fi
    log_error "$PACKAGE_INDEX_URL ist per HTTPS nicht erreichbar. Proxy? Dann mit 'sudo -E ./install.sh' starten, damit https_proxy erhalten bleibt (oder PIP_INDEX_URL auf einen erreichbaren Spiegel setzen)."
}

# --- SCHRITT 1: PREREQUISITES (Docker & Python) ---
setup_system() {
    log_info "[1/5] Systemprüfung & Vorbereitung..."
    
    wait_for_internet

    # Python Check
    if ! command -v python3 &> /dev/null; then
        log_warn "Python3 fehlt. Installiere..."
        apt-get update && apt-get install -y python3 python3-venv python3-pip || log_error "Konnte Python nicht installieren."
    fi
    # requirements.txt only matches Python 3.11-3.14; outside that range pip would silently install nothing.
    if ! python3 -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)'; then
        log_error "Python 3.11 bis 3.14 wird benötigt, gefunden: $(python3 --version 2>&1). Bitte eine passende Version installieren (z. B. Debian 12, Ubuntu 24.04 oder 'uv python install 3.13')."
    fi

    # Docker Check (Übernommen aus setup_linux.sh)
    if ! command -v docker &> /dev/null; then
        log_warn "Docker fehlt. Starte Auto-Installer..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh || log_error "Docker Installation fehlgeschlagen."
        rm get-docker.sh
    fi

    # Docker Permissions
    # Wir nehmen den User, der sudo aufgerufen hat (SUDO_USER), nicht root!
    REAL_USER="${SUDO_USER:-$USER}"
    if ! groups "$REAL_USER" | grep &>/dev/null "\bdocker\b"; then
        log_info "Füge User '$REAL_USER' zur Docker-Gruppe hinzu..."
        usermod -aG docker "$REAL_USER"
    fi
}

# --- SCHRITT 2: DATEIEN KOPIEREN ---
deploy_files() {
    log_info "[2/5] Kopiere Programmdateien..."

    # Code nach /opt
    mkdir -p "$INSTALL_DIR"
    
    # Sicherstellen, dass wir im Repo Root sind
    cd "$REPO_ROOT" || log_error "Konnte Quellverzeichnis nicht finden."

    log_info "Ziel Code: $INSTALL_DIR"
    
    # Wir nutzen rsync für sauberes Kopieren (exklusive Müll)
    if ! command -v rsync &> /dev/null; then apt-get install -y rsync; fi

    # Ausgeschlossene Pfade löscht --delete auch im Ziel nicht: Builds, Cache, Modelle und die Einstellungen des
    # Benutzers in /opt überstehen eine erneute Installation.
    rsync -av --delete \
        --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude 'build' --exclude 'dist' \
        --exclude '/output' --exclude '/cache' --exclude '/models' --exclude '/logs' \
        --exclude '/configs/config.yml' \
        ./ "$INSTALL_DIR/" > /dev/null

    # Rechte anpassen (Der normale User soll den Code besitzen, um ohne sudo auszuführen)
    REAL_USER="${SUDO_USER:-$USER}"
    REAL_GROUP=$(id -gn "$REAL_USER")
    chown -R "$REAL_USER:$REAL_GROUP" "$INSTALL_DIR"
}

# --- SCHRITT 3: PYTHON VENV ---
setup_venv() {
    log_info "[3/5] Erstelle Python Environment (VENV)..."
    
    # Wir führen dies als REAL_USER aus, damit das Venv nicht root gehört
    REAL_USER="${SUDO_USER:-$USER}"
    
    sudo -u "$REAL_USER" bash <<EOF
    cd "$INSTALL_DIR"
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip > /dev/null
    
    echo "Installiere Abhängigkeiten..."
    if [ -f requirements.txt ]; then
        ./.venv/bin/pip install -r requirements.txt > /dev/null
    else
        echo "⚠️ requirements.txt nicht gefunden. Installiere Basis-Pakete."
        ./.venv/bin/pip install pyyaml requests psutil > /dev/null
    fi
EOF
}

# --- SCHRITT 4: KONFIGURATION & LAUNCHER ---
configure() {
    log_info "[4/5] Launcher..."

    # Launcher Skript erstellen (Global verfügbar machen)
    cat <<EOF > /usr/local/bin/llm-framework
#!/bin/bash
cd "$INSTALL_DIR"
source .venv/bin/activate
exec python3 orchestrator/main.py "\$@"
EOF
    chmod +x /usr/local/bin/llm-framework
}

# --- SCHRITT 5: ABSCHLUSS ---
finish() {
    log_success "[5/5] Installation abgeschlossen!"
    echo ""
    echo "-----------------------------------------------------"
    echo "📂 Code:   $INSTALL_DIR"
    echo "📂 Daten:  $INSTALL_DIR/output, cache, models (beim ersten Gebrauch angelegt)"
    echo "🚀 Start:  Tippe einfach 'llm-framework'"
    echo "-----------------------------------------------------"
    
    # Hinweis zum Docker Group Refresh
    REAL_USER="${SUDO_USER:-$USER}"
    if ! groups "$REAL_USER" | grep &>/dev/null "\bdocker\b"; then
        log_warn "Du wurdest zur Docker-Gruppe hinzugefügt."
        log_warn "Bitte einmal aus- und einloggen (oder 'newgrp docker' nutzen)."
    fi
}

# --- MAIN ---
main() {
    check_root
    setup_system
    deploy_files
    setup_venv
    configure
    finish
}

# Tests laden das Skript mit 'source', um einzelne Funktionen zu prüfen; nur ein direkter Aufruf installiert.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
