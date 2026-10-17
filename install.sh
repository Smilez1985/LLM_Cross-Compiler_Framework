#!/bin/bash
# install.sh - LLM Framework Enterprise Installer
# DIREKTIVE: Goldstandard. Vereint System-Checks, Docker-Setup und App-Deployment.
# Code und Python-Umgebung landen in /opt/llm-cross-compiler-framework (dem aufrufenden Benutzer übergeben). Das
# Framework legt Builds, Cache und Modelle dort an, wenn sie gebraucht werden (Einstellungen output_dir,
# cache_dir, models_dir); persönlicher Zustand liegt in ~/.llm-framework des Benutzers.
# Ein erneuter Aufruf aktualisiert die Dateien, die das Repository ausliefert, und entfernt nur Dateien, die ein
# früheres install.sh kopiert hat und die nicht mehr ausgeliefert werden (Liste in .lcf-installed-files). Alles
# andere bleibt: eigene Targets, installierte Community-Packs, Einstellungen, Builds, Cache und Modelle.
# Eine Installation unter dem früheren Namen (/opt/llm-conversion-framework) wird nie verändert oder gelöscht: Der
# Installer sagt, dass es sie gibt, kopiert nur ihre Einstellungen, wenn die neue Installation noch keine hat, und
# erklärt, wie man sie selbst entfernt.

# Das Skript braucht bash; unter sh (dash) mit einer klaren Meldung abbrechen statt mit "Bad substitution".
if [ -z "${BASH_VERSION:-}" ]; then
    echo "install.sh braucht bash: sudo ./install.sh (oder sudo bash install.sh)" >&2
    exit 1
fi

set -u

# --- KONFIGURATION ---
APP_NAME="llm-cross-compiler-framework"
INSTALL_DIR="/opt/$APP_NAME"
# Der Ordner der Installation vor der Umbenennung des Repositorys (LLM Conversion Framework). Nur gelesen.
OLD_INSTALL_DIR="/opt/llm-conversion-framework"
# Die Dateien, die das letzte install.sh nach $INSTALL_DIR kopiert hat (NUL-getrennt).
MANIFEST_NAME=".lcf-installed-files"
REPO_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
# Paketindex, den pip erreichen muss (PIP_INDEX_URL, falls ein eigener Spiegel gesetzt ist)
PACKAGE_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple/}"
# LCF_CONTAINER_BUILD=1 setzt nur "Docker Setup/Dockerfile" (scripts/build_container_image.sh): Dieselbe Installation
# läuft dann in einem Image, das auf diesem Rechner gebaut wird. Ohne Docker-Engine im Image (sie installiert dort
# niemand) und ohne docker-Gruppe; alles andere ist wie auf dem Rechner selbst.
CONTAINER_BUILD="${LCF_CONTAINER_BUILD:-0}"
# Dittos lokale Laufzeit (selbst gebauter llama-server und sein Modell): --ditto=auto|bare-metal|docker|skip, für
# unbeaufsichtigte Läufe auch LCF_DITTO=... (ohne Rückfragen; Größe und Lizenz des Modells werden trotzdem gezeigt).
DITTO_CHOICE="${LCF_DITTO:-auto}"

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
    # Ohne Nachfrage (siebte Runde, Punkt 6), aber mit klaren Worten, was die Gruppe bedeutet.
    if ! groups "$REAL_USER" | grep &>/dev/null "\bdocker\b"; then
        log_info "Füge User '$REAL_USER' zur Gruppe docker hinzu: Das Framework baut damit die Werkzeuge zum Cross-Kompilieren in Containern."
        log_warn "Was das heißt: Wer in der Gruppe docker ist, steuert den Docker-Dienst, und der läuft als root. Jedes Programm, das als '$REAL_USER' läuft, kann damit Root-Rechte auf diesem Rechner bekommen. Rückgängig machen: sudo gpasswd -d $REAL_USER docker (danach kann das Framework ohne sudo nicht mehr bauen)."
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

# --- INSTALLATION UNTER DEM FRÜHEREN NAMEN ---
# Gibt es OLD_INSTALL_DIR (nicht derselbe Ordner wie INSTALL_DIR)?
old_installation_exists() {
    [ -d "$OLD_INSTALL_DIR" ] && [ "$(realpath -m -- "$OLD_INSTALL_DIR")" != "$(realpath -m -- "$INSTALL_DIR")" ]
}

# Die persönlichen Dateien in configs/ der früheren Installation, die es in der neuen noch nicht gibt: kopiert, nie
# verschoben, und als die Person, die installiert (nicht als root: ein Link dort kann so nichts lesen, was ihr nicht
# ohnehin gehört). Eine vorhandene Datei wird nie überschrieben, ein Link nie verfolgt. Auf Linux liegen sie im
# Installationsordner: config.yml (ohne sie passte etwa die gewählte Art, den Tresor zu schützen, secret_storage,
# nicht mehr), eine selbst geschriebene framework.yml und secrets.store, der Tresor für Zugangsdaten einer Version vor
# 3.0.0. Ihn übernimmt das Framework beim ersten Start einmal nach ~/.llm-framework; einen schon übernommenen erkennt
# es am Inhalt wieder und übernimmt nichts davon noch einmal, ein seither gelöschter Eintrag kommt also nicht zurück.
OLD_CONFIG_FILES=(config.yml framework.yml secrets.store)
OLD_SETTINGS_COPIED=()
adopt_old_settings() {
    old_installation_exists || return 0
    local real_user="${SUDO_USER:-${USER:-}}" name old new
    local as_user=(sudo -u "$real_user")
    if ! command -v sudo &> /dev/null; then as_user=(runuser -u "$real_user" --); fi
    if [ -z "$real_user" ] || [ "$real_user" = "root" ]; then as_user=(); fi
    for name in "${OLD_CONFIG_FILES[@]}"; do
        old="$OLD_INSTALL_DIR/configs/$name"
        new="$INSTALL_DIR/configs/$name"
        if [ ! -f "$old" ] || [ -L "$old" ] || [ -e "$new" ] || [ -L "$new" ]; then continue; fi
        if ${as_user[@]+"${as_user[@]}"} mkdir -p "$INSTALL_DIR/configs" \
            && ${as_user[@]+"${as_user[@]}"} cp -P -n -- "$old" "$new"; then
            # Der Tresor bleibt nur für seine Besitzerin lesbar, auch wenn das Original es nicht war.
            if [ "$name" = "secrets.store" ]; then ${as_user[@]+"${as_user[@]}"} chmod go-rwx -- "$new"; fi
            OLD_SETTINGS_COPIED+=("configs/$name")
        else
            log_warn "$old ließ sich nicht kopieren; die neue Installation beginnt ohne diese Datei."
        fi
    done
}

# Sagt in klaren Worten, dass es die frühere Installation noch gibt, was übernommen wurde und was nicht, und wie man
# sie selbst entfernt. Der Installer löscht sie nie.
old_installation_note() {
    old_installation_exists || return 0
    local kept=() d
    for d in output cache models; do
        if [ -d "$OLD_INSTALL_DIR/$d" ]; then kept+=("$OLD_INSTALL_DIR/$d"); fi
    done
    echo ""
    log_warn "Es gibt noch eine Installation unter dem früheren Namen: $OLD_INSTALL_DIR"
    echo "   Der Installer hat sie nicht verändert und löscht sie nicht. Die neue Installation liegt in $INSTALL_DIR;"
    echo "   'llm-framework' startet ab jetzt die neue. Dein persönlicher Ordner (~/.llm-framework: Zugangsdaten,"
    echo "   Werkzeugrechte, Wissensbasis, Verlauf) gehört keiner der beiden und gilt weiter, wie er ist."
    # Nur mit Einträgen ausgewertet: Ein leeres Feld gilt unter 'set -u' in bash vor 4.4 als ungesetzt.
    if [ "${#OLD_SETTINGS_COPIED[@]}" -gt 0 ]; then
        echo "   Diese Dateien wurden in die neue Installation kopiert; die Originale bleiben: ${OLD_SETTINGS_COPIED[*]}"
        if [[ " ${OLD_SETTINGS_COPIED[*]} " == *" configs/secrets.store "* ]]; then
            echo "   configs/secrets.store ist der Tresor für Zugangsdaten einer Version vor 3.0.0; das Framework"
            echo "   übernimmt ihn beim ersten Start nach ~/.llm-framework."
        fi
    fi
    if [ "${#kept[@]}" -gt 0 ]; then
        echo "   Nicht übernommen, weil groß (bei Bedarf selbst kopieren, z. B. cp -a $OLD_INSTALL_DIR/output $INSTALL_DIR/):"
        for d in "${kept[@]}"; do echo "     $d"; done
    fi
    echo "   Ebenso nicht übernommen: Targets, die du selbst angelegt hast, und installierte Packs unter targets/ und"
    echo "   community/ dort. Kopiere sie bei Bedarf in dieselben Ordner von $INSTALL_DIR."
    echo "   KI-Apps, die du über MCP verbunden hast (Claude Desktop, VS Code, Claude Code), starten noch die alte"
    echo "   Installation. Richte sie vor dem Entfernen aus der neuen ein (llm-cli mcp config, siehe README):"
    echo "   cd $INSTALL_DIR && .venv/bin/python -m orchestrator.cli mcp config"
    echo "   Entfernen, wenn du nichts mehr daraus brauchst: erst ansehen, was darin liegt (ls -la $OLD_INSTALL_DIR),"
    echo "   dann selbst: sudo rm -rf $OLD_INSTALL_DIR"
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

    # Genau die Versionen aus requirements.txt dieses Stands (uv export aus uv.lock); pip prüft jede Datei gegen
    # ihren Hash. Ohne die Datei wird nichts ungeprüft nachinstalliert.
    echo "Installiere Abhängigkeiten..."
    if [ ! -f requirements.txt ]; then
        echo "requirements.txt fehlt in $INSTALL_DIR; ohne die festgelegten Versionen wird nichts installiert." >&2
        exit 1
    fi
    ./.venv/bin/pip install --require-hashes -r requirements.txt > /dev/null
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

# --- DITTO: LOKALE LAUFZEIT ---
# Liest --ditto=WERT aus den Argumenten des Installers (sonst LCF_DITTO, sonst auto). Ein unbekannter Wert bricht ab,
# bevor irgendetwas installiert wird.
ditto_option() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            --ditto=*) DITTO_CHOICE="${arg#--ditto=}" ;;
        esac
    done
    case "$DITTO_CHOICE" in
        auto|bare-metal|docker|skip) ;;
        *) log_error "Unbekannter Wert für --ditto: '$DITTO_CHOICE' (auto, bare-metal, docker oder skip)." ;;
    esac
}

# Richtet Dittos lokale Laufzeit ein: 'llm-cli ditto runtime ensure' als die Person, die installiert (nie als root).
# Idempotent: Ist Ditto schon da (direkt oder in Docker), tut der Befehl nichts und sagt das. Der Schritt lässt die
# Installation nie scheitern: Ein Fehler wird gemeldet, und die Installation geht weiter. Ein Wechsel der Art
# (--force) und das Entfernen der alten Art bleiben der Person überlassen.
setup_ditto() {
    log_info "[Ditto] Lokale Laufzeit für Ditto ($DITTO_CHOICE)..."
    if [ "$DITTO_CHOICE" = "skip" ]; then
        log_info "Übersprungen (--ditto=skip). Später: llm-cli ditto runtime ensure"
        return 0
    fi
    if [ "$CONTAINER_BUILD" = "1" ]; then
        log_info "Container-Build: Ditto wird hier nicht eingerichtet. Der Docker-Modus ist im Framework-Container nicht verfügbar (darin gibt es keine Docker-Engine), und eine im Image gebaute Laufzeit passte nicht zur Hardware, auf der der Container später läuft. Nutze im Container einen Cloud-Anbieter oder einen lokalen Server in deinem Netz, oder installiere das Framework direkt auf dem Rechner, der Dittos Modell ausführen soll (docs/Container_Setup_DE.md)."
        return 0
    fi
    # Ohne SUDO_USER und USER (etwa als root in einem Container) bricht 'set -u' nicht ab: Der Schritt meldet nur.
    local real_user="${SUDO_USER:-${USER:-}}"
    if [ "$real_user" = "root" ] || [ -z "$real_user" ]; then
        log_warn "Ditto wird für deinen Benutzer eingerichtet, nicht für root: Starte das Skript mit sudo als dein Benutzer, oder führe später als dein Benutzer aus: llm-cli ditto runtime ensure"
        return 0
    fi
    # sudo übernimmt nur die Proxy-Variablen (für den Quellcode und das Modell), nie die übrige Umgebung von root.
    local as_user=(sudo -H --preserve-env=https_proxy,HTTPS_PROXY,http_proxy,HTTP_PROXY,no_proxy,NO_PROXY -u "$real_user")
    if ! command -v sudo &> /dev/null; then as_user=(runuser -u "$real_user" --); fi
    local args=(ditto runtime ensure --mode "$DITTO_CHOICE")
    if [ -n "${LCF_DITTO:-}" ]; then args+=(--yes); fi
    if (cd "$INSTALL_DIR" && "${as_user[@]}" "$INSTALL_DIR/.venv/bin/python" -m orchestrator.cli "${args[@]}"); then
        log_success "Ditto: fertig (Meldungen oben)."
    else
        log_warn "Ditto ließ sich nicht einrichten (Meldungen oben); die Installation ist trotzdem fertig. Später erneut: llm-cli ditto runtime ensure"
    fi
    return 0
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
    old_installation_note
    
    # Hinweis zum Docker Group Refresh
    REAL_USER="${SUDO_USER:-$USER}"
    if [ "$CONTAINER_BUILD" != "1" ] && ! groups "$REAL_USER" | grep &>/dev/null "\bdocker\b"; then
        log_warn "Du wurdest zur Docker-Gruppe hinzugefügt."
        log_warn "Bitte einmal aus- und einloggen (oder 'newgrp docker' nutzen)."
    fi
}

# --- MAIN ---
main() {
    ditto_option "$@"
    check_root
    setup_system
    deploy_files
    adopt_old_settings
    setup_venv
    configure
    setup_ditto
    finish
}

# Tests laden das Skript mit 'source', um einzelne Funktionen zu prüfen; nur ein direkter Aufruf installiert.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
