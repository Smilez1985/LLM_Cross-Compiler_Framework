#!/bin/bash
# install.sh - LLM Framework Enterprise Installer
# DIREKTIVE: Goldstandard. Vereint System-Checks, Docker-Setup und App-Deployment.
# Führt eine saubere Trennung von Code (/opt/llm-framework) und Daten ($HOME/llm-data) durch.

set -u

# --- KONFIGURATION ---
APP_NAME="llm-conversion-framework"
INSTALL_DIR="/opt/$APP_NAME"
DATA_DIR="$HOME/$APP_NAME-data"
REPO_ROOT="$(dirname "$(realpath "$0")")"

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
    log_info "Prüfe Internetverbindung..."
    if ! ping -c 1 -W 2 8.8.8.8 &> /dev/null; then
        log_error "Keine Internetverbindung. Abbruch."
    fi
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
    
    rsync -av --delete \
        --exclude '.git' --exclude '.venv' --exclude '__pycache__' --exclude 'build' --exclude 'dist' \
        ./ "$INSTALL_DIR/" > /dev/null
    
    # Rechte anpassen (Der normale User soll den Code besitzen, um ohne sudo auszuführen)
    REAL_USER="${SUDO_USER:-$USER}"
    REAL_GROUP=$(id -gn "$REAL_USER")
    chown -R "$REAL_USER:$REAL_GROUP" "$INSTALL_DIR"

    # Datenverzeichnis (Templates) kopieren
    # Wir machen das als der reale User, damit die Permissions im Home stimmen
    log_info "Ziel Daten: $DATA_DIR"
    sudo -u "$REAL_USER" mkdir -p "$DATA_DIR"
    
    # Templates kopieren (targets, models, configs)
    for dir in targets models configs; do
        if [ -d "$REPO_ROOT/$dir" ]; then
            sudo -u "$REAL_USER" rsync -av "$REPO_ROOT/$dir/" "$DATA_DIR/$dir/" > /dev/null
        fi
    done
    
    # Output Ordner erstellen
    for dir in output logs cache; do
        sudo -u "$REAL_USER" mkdir -p "$DATA_DIR/$dir"
    done
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
    log_info "[4/5] Konfiguration & Launcher..."
    REAL_USER="${SUDO_USER:-$USER}"

    # 1. Config anpassen
    CONFIG_FILE="$DATA_DIR/configs/user_config.yml"
    if [ -f "$CONFIG_FILE" ]; then
        # Einfaches Ersetzen der Pfade via sed
        sed -i "s|output_dir:.*|output_dir: $DATA_DIR/output|g" "$CONFIG_FILE"
        sed -i "s|logs_dir:.*|logs_dir: $DATA_DIR/logs|g" "$CONFIG_FILE"
        sed -i "s|cache_dir:.*|cache_dir: $DATA_DIR/cache|g" "$CONFIG_FILE"
    fi
    
    # 2. Launcher Skript erstellen (Global verfügbar machen)
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
    echo "📂 Daten:  $DATA_DIR"
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
check_root
setup_system
deploy_files
setup_venv
configure
finish
