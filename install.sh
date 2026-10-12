#!/bin/bash
# install.sh - LLM Framework Enterprise Installer
# DIREKTIVE: Goldstandard. Vereint System-Checks, Docker-Setup und App-Deployment.
# Code und Python-Umgebung landen in /opt/llm-conversion-framework (dem aufrufenden Benutzer übergeben). Das
# Framework legt Builds, Cache und Modelle dort an, wenn sie gebraucht werden (Einstellungen output_dir,
# cache_dir, models_dir); persönlicher Zustand liegt in ~/.llm-framework des Benutzers.
# Ein erneuter Aufruf aktualisiert die Dateien, die das Repository ausliefert, und entfernt nur Dateien, die ein
# früheres install.sh kopiert hat und die nicht mehr ausgeliefert werden (Liste in .lcf-installed-files). Alles
# andere bleibt: eigene Targets, installierte Community-Packs, Einstellungen, Builds, Cache und Modelle.

# Das Skript braucht bash; unter sh (dash) mit einer klaren Meldung abbrechen statt mit "Bad substitution".
if [ -z "${BASH_VERSION:-}" ]; then
    echo "install.sh braucht bash: sudo ./install.sh (oder sudo bash install.sh)" >&2
    exit 1
fi

set -u

# --- KONFIGURATION ---
APP_NAME="llm-conversion-framework"
INSTALL_DIR="/opt/$APP_NAME"
# Die Dateien, die das letzte install.sh nach $INSTALL_DIR kopiert hat (NUL-getrennt).
MANIFEST_NAME=".lcf-installed-files"
REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
# Paketindex, den pip erreichen muss (PIP_INDEX_URL, falls ein eigener Spiegel gesetzt ist)
PACKAGE_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple/}"
# LCF_CONTAINER_BUILD=1 setzt nur "Docker Setup/Dockerfile" (scripts/build_container_image.sh): Dieselbe Installation
# läuft dann in einem Image, das auf diesem Rechner gebaut wird. Ohne Docker-Engine im Image (sie installiert dort
# niemand) und ohne docker-Gruppe; alles andere ist wie auf dem Rechner selbst.
CONTAINER_BUILD="${LCF_CONTAINER_BUILD:-0}"

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

# Die URL ohne benutzer:passwort@ (PIP_INDEX_URL eines Spiegels kann Zugangsdaten enthalten).
redact_url() { echo "$1" | sed -E 's#://[^/@]*@#://***@#'; }

wait_for_internet() {
    # HTTPS zum Paketindex statt ping: ICMP ist in vielen Netzen gesperrt, und pip braucht genau diesen Weg.
    # Geladen wird die kleine Projektseite von pip (<index>/pip/, etwa 100 KB), nicht die Startseite des Index
    # (bei pypi.org über 40 MB, in 10 s nur mit schneller Leitung). Die URL geht über stdin an curl/wget statt auf
    # die Kommandozeile, die jeder Benutzer mit ps sieht.
    local probe shown
    probe="${PACKAGE_INDEX_URL%/}/pip/"
    shown="$(redact_url "$PACKAGE_INDEX_URL")"
    log_info "Prüfe Internetverbindung (HTTPS zu $shown)..."
    if command -v curl &> /dev/null; then
        printf 'url = "%s"\n' "$probe" | curl -fsS --max-time 10 -o /dev/null -K - && return 0
    elif command -v wget &> /dev/null; then
        printf '%s\n' "$probe" | wget -q --timeout=10 --tries=1 -O /dev/null -i - && return 0
    else
        log_warn "Weder curl noch wget gefunden; die Internetprüfung wird übersprungen."
        return 0
    fi
    log_error "$shown ist per HTTPS nicht erreichbar. Proxy? Dann mit 'sudo -E ./install.sh' starten, damit https_proxy erhalten bleibt (oder PIP_INDEX_URL auf einen erreichbaren Spiegel setzen)."
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

    # In einem selbst gebauten Framework-Image gibt es keine Docker-Engine und keine docker-Gruppe.
    if [ "$CONTAINER_BUILD" = "1" ]; then
        log_info "Container-Build: Docker wird im Image nicht installiert."
        return 0
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
# Die Dateien, die das Repository ausliefert, relativ zu REPO_ROOT und NUL-getrennt: in einem git-Klon die
# verfolgten Dateien, sonst (etwa ein entpacktes Archiv) alle Dateien ohne Entwicklungsreste und Daten. Scheitert
# git oder find, scheitert auch die Funktion: Nach einer unvollständigen Liste wird nichts entfernt.
shipped_files() {
    if command -v git &> /dev/null \
        && git -C "$REPO_ROOT" -c safe.directory="$REPO_ROOT" rev-parse --is-inside-work-tree &> /dev/null; then
        (
            set -o pipefail
            git -C "$REPO_ROOT" -c safe.directory="$REPO_ROOT" ls-files -z | while IFS= read -r -d '' f; do
                if [ -f "$REPO_ROOT/$f" ]; then printf '%s\0' "$f"; fi
            done
        )
    else
        # build/ und dist/ nur auf oberster Ebene: orchestrator/runtime/build/ ist ein Paket des Frameworks.
        (cd "$REPO_ROOT" && find . \( -name .git -o -name .venv -o -name __pycache__ -o -path ./build -o -path ./dist \
            -o -path ./output -o -path ./cache -o -path ./models -o -path ./logs \) -prune \
            -o -type f ! -path ./configs/config.yml -printf '%P\0')
    fi
}

# Entfernt Dateien der alten Liste ($1), die in der neuen ($2) fehlen: was das Repository nicht mehr ausliefert.
# Was nie in einer Liste stand (eigene Targets, Community-Packs, Einstellungen, Builds, Cache, Modelle), bleibt.
# Nur einfache relative Pfade, die innerhalb von INSTALL_DIR bleiben (kein .., kein Link nach draußen). Aufgelöst
# wird nur der Ordner: Ein Link wird selbst entfernt, nie die Datei, auf die er zeigt.
remove_stale_files() {
    local f base dir path
    base="$(realpath -m -- "$INSTALL_DIR")"
    while IFS= read -r -d '' f; do
        case "/$f/" in */../*|*/./*|//*) continue ;; esac
        dir="$(realpath -m -- "$(dirname -- "$INSTALL_DIR/$f")")"
        path="$dir/$(basename -- "$f")"
        case "$dir/" in "$base"/*) ;; *) continue ;; esac
        if [ -L "$path" ] || [ ! -d "$path" ]; then rm -f -- "$path"; fi
    done < <(comm -z -23 <(sort -z -u "$1") <(sort -z -u "$2"))
}

deploy_files() {
    log_info "[2/5] Kopiere Programmdateien..."

    # Code nach /opt
    mkdir -p "$INSTALL_DIR"
    
    # Sicherstellen, dass wir im Repo Root sind
    cd "$REPO_ROOT" || log_error "Konnte Quellverzeichnis nicht finden."

    log_info "Ziel Code: $INSTALL_DIR"
    
    # Wir nutzen rsync für sauberes Kopieren (exklusive Müll)
    if ! command -v rsync &> /dev/null; then apt-get install -y rsync; fi

    # Nur die ausgelieferten Dateien, ohne --delete: Was im Ziel sonst liegt, gehört dem Benutzer oder dem Framework
    # (eigene Targets unter targets/, Community-Packs unter targets/ und community/, configs/config.yml, Builds,
    # Cache, Modelle, .venv). Entfernt wird nur, was ein früheres install.sh kopiert hat und nicht mehr ausgeliefert
    # wird; beim ersten Lauf ohne Liste wird nichts entfernt.
    # Die Kopie von install.sh in INSTALL_DIR kopiert und entfernt nichts: Ihre Liste enthielte sonst alles dort,
    # auch eigene Targets und Community-Packs, und das nächste Update aus dem Repository löschte sie als "nicht mehr
    # ausgeliefert".
    if [ "$(realpath -m -- "$REPO_ROOT")" = "$(realpath -m -- "$INSTALL_DIR")" ]; then
        log_warn "install.sh läuft aus $INSTALL_DIR selbst: nichts kopiert oder entfernt (Update: im Repository starten)."
    else
        local list
        list="$(mktemp)"
        if ! shipped_files > "$list" || [ ! -s "$list" ]; then
            rm -f "$list"
            log_error "Die Dateien in $REPO_ROOT ließen sich nicht auflisten; nichts kopiert oder entfernt."
        fi
        if ! rsync -a --from0 --files-from="$list" ./ "$INSTALL_DIR/" > /dev/null; then
            rm -f "$list"
            log_error "Kopieren nach $INSTALL_DIR fehlgeschlagen."
        fi
        if [ -f "$INSTALL_DIR/$MANIFEST_NAME" ]; then
            remove_stale_files "$INSTALL_DIR/$MANIFEST_NAME" "$list"
        fi
        cp "$list" "$INSTALL_DIR/$MANIFEST_NAME"
        rm -f "$list"
    fi

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
    # sudo fehlt auf minimalen Systemen (und im Framework-Image); runuser (util-linux) tut dort dasselbe.
    local as_user=(sudo -u "$REAL_USER")
    if ! command -v sudo &> /dev/null; then as_user=(runuser -u "$REAL_USER" --); fi
    
    # Scheitert ein Schritt (kein venv, ein Paket nicht erreichbar), bricht die Installation ab: Sonst meldete sie
    # "abgeschlossen" mit einer leeren Umgebung, und ein Container-Image würde trotzdem gebaut.
    if ! "${as_user[@]}" bash <<EOF
    set -e
    cd "$INSTALL_DIR"
    python3 -m venv .venv
    ./.venv/bin/pip install --upgrade pip > /dev/null || echo "⚠️ pip ließ sich nicht aktualisieren; weiter mit der vorhandenen Version."

    echo "Installiere Abhängigkeiten..."
    if [ -f requirements.txt ]; then
        ./.venv/bin/pip install -r requirements.txt > /dev/null
    else
        echo "⚠️ requirements.txt nicht gefunden. Installiere Basis-Pakete."
        ./.venv/bin/pip install pyyaml requests psutil > /dev/null
    fi
EOF
    then
        log_error "Die Python-Umgebung in $INSTALL_DIR/.venv ließ sich nicht einrichten (Meldungen oben); die Installation ist nicht fertig. Proxy oder eigener Spiegel: sudo -E ./install.sh bzw. PIP_INDEX_URL."
    fi
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
    if [ "$CONTAINER_BUILD" != "1" ] && ! groups "$REAL_USER" | grep &>/dev/null "\bdocker\b"; then
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
