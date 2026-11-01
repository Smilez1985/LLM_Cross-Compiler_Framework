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
# Installer sagt, dass es sie gibt, kopiert nur ihre persönlichen Dateien aus configs/ (Einstellungen, einen Tresor
# von vor 3.0.0), die die neue Installation noch nicht hat, und erklärt, wie man sie selbst entfernt.

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
# Die grafische Oberfläche (PySide6, das optionale Extra 'gui') wird mitinstalliert (requirements-gui.txt). --headless
# (für unbeaufsichtigte Läufe auch LCF_HEADLESS=1; das Framework-Image ohne --gui setzt es) installiert nur
# requirements.txt: Kommandozeile und MCP-Server ohne Qt, für einen Server ohne Bildschirm.
HEADLESS="${LCF_HEADLESS:-0}"
# Dittos lokale Laufzeit (selbst gebauter llama-server und sein Modell): --ditto=auto|bare-metal|docker|skip, für
# unbeaufsichtigte Läufe auch LCF_DITTO=... (ohne Rückfragen; Größe und Lizenz des Modells werden trotzdem gezeigt).
DITTO_CHOICE="${LCF_DITTO:-auto}"
# Der Framework-Modus: private (Standard) oder enterprise (nimmt auf Boards nur OpenSSH mit sudo, die Schlüsselzeile in
# Root-Besitz). --mode=private|enterprise, für unbeaufsichtigte Läufe auch LCF_MODE=...; ohne Angabe fragt der
# Installer am Terminal (Enter: privat). Ohne Angabe und ohne Terminal bleibt der Modus, wie er ist (neu: privat).
MODE_CHOICE="${LCF_MODE:-}"

# Die Container-Engine läuft standardmäßig ohne Root-Rechte (rootless Podman; --engine=docker: rootless Docker);
# --rootful: Docker als root mit der Gruppe docker, nur auf den ausdrücklichen Wunsch der Person und nach einer Warnung
# (setup_engine, weiter unten).

# --dry-run: ein Probelauf. Er erkennt das System und zeigt, was die Installation hier täte (Paketverwaltung, Pakete,
# Quellen und Schlüssel), ohne etwas zu verändern oder zu laden; er braucht keine Root-Rechte. Exit-Code 0: die
# Installation liefe durch; 1: sie hielte an (der Grund steht dabei).
DRY_RUN=0

# Alles, was der Installer installiert (Python, rsync, Docker), kommt über die Paketverwaltung der Linux-Familie
# (apt, dnf, zypper, pacman, apk; Familie aus ID und ID_LIKE in /etc/os-release), nie mit einem heruntergeladenen
# Skript. Jede Paketverwaltung prüft die Signaturen auf ihre Weise; einem fremden Repository (Dockers) wird erst
# vertraut, wenn der Fingerabdruck seines Schlüssels der hier festgelegte ist.
# Docker, falls es fehlt, auf Debian, Ubuntu, Raspberry Pi OS und darauf aufbauenden Systemen: nur aus Dockers
# offiziellem apt-Repository. Dem Signaturschlüssel des Repositorys wird erst vertraut, wenn sein Fingerabdruck dieser
# ist; danach prüft apt jedes Paket gegen die signierte Paketliste (SHA256).
# Quelle des Fingerabdrucks: Dockers Installationsanleitung für Debian und Ubuntu ("Verify that you now have the key
# with the fingerprint 9DC8 5822 9FC7 DD38 854A  E2D8 8D81 803C 0EBF CD88", docs.docker.com/engine/install/; im
# Repository docker/docs, Tag archive/v18.09, install/linux/docker-ce/debian.md und ubuntu.md). Am 2026-10-23
# nachgeprüft: download.docker.com/linux/{debian,ubuntu,raspbian}/gpg ist genau dieser eine Schlüssel
# (Docker Release (CE deb), rsa4096, 2017-02-22).
DOCKER_KEY_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
DOCKER_DOWNLOAD_URL="https://download.docker.com/linux"
DOCKER_MANUAL_URL="https://docs.docker.com/engine/install/"
# Dieselben Dateien wie in Dockers Anleitung, damit eine dort angelegte Quelle nicht mit dieser streitet (Signed-By).
DOCKER_KEYRING="/etc/apt/keyrings/docker.asc"
DOCKER_SOURCES="/etc/apt/sources.list.d/docker.sources"
OS_RELEASE_FILE="/etc/os-release"
DEBIAN_VERSION_FILE="/etc/debian_version"
# Docker auf Fedora, RHEL und CentOS Stream (Docker veröffentlicht für diese drei ein rpm-Repository): dem Schlüssel
# wird erst vertraut, wenn sein Fingerabdruck dieser ist; danach prüft dnf die Signatur jedes Pakets (gpgcheck=1).
# Quelle: Dockers Installationsanleitungen für Fedora, RHEL und CentOS ("verify that the fingerprint matches 060A 61C5
# 1B55 8A7F 742B 77AA C52F EB6B 621E 9F35", docs.docker.com/engine/install/fedora/, .../rhel/, .../centos/). Am
# 2026-10-23 nachgeprüft: download.docker.com/linux/{fedora,rhel,centos}/gpg ist genau dieser eine Schlüssel
# (Docker Release (CE rpm), rsa4096, 2017-02-22). Rocky Linux, AlmaLinux, Oracle Linux und die übrigen
# RHEL-Abkömmlinge bekommen Docker CE aus Dockers RHEL-Repository mit demselben Schlüssel (das Framework braucht
# BuildKit, etwa für Hailo). In der ganzen RHEL-Familie ist Podman nur der Ersatzweg (rhel_family). Amazon Linux bringt
# ein eigenes Docker-Paket.
DOCKER_RPM_KEY_FINGERPRINT="060A61C51B558A7F742B77AAC52FEB6B621E9F35"
# Die Architekturen, für die Docker in seinem RHEL-Repository Pakete veröffentlicht (download.docker.com/linux/rhel/8,
# 9 und 10, am 2026-10-23 nachgesehen: je x86_64, aarch64 und s390x). Auf einer anderen gibt es dort nichts zu laden.
DOCKER_RHEL_ARCHES="x86_64 aarch64 s390x"
DOCKER_RPM_KEYRING="/etc/pki/rpm-gpg/docker-ce.asc"
# Derselbe Dateiname wie mit Dockers Anleitung (dnf config-manager), damit eine dort angelegte Quelle bleibt.
DOCKER_RPM_REPO="/etc/yum.repos.d/docker-ce.repo"
# Alpines Paketquellen (Docker liegt im Repository community).
APK_REPOSITORIES="/etc/apk/repositories"
# Die Anleitung zu dem, was das Framework braucht, für eine Installation von Hand.
MANUAL_URL="https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/blob/main/README_DE.md#voraussetzungen"

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
log_error() {
    if [ "$DRY_RUN" = "1" ]; then echo -e "${RED}❌ Probelauf: Hier hielte die Installation an: $1${NC}"; exit 1; fi
    echo -e "${RED}❌ $1${NC}"; exit 1
}

# Eine Ja/Nein-Frage an die Person, die installiert. Die Antwort kommt vom Terminal (ASK_TTY), nie aus stdin (install.sh
# kann über eine Pipe laufen) und nie aus einer Umgebungsvariable: Was entfernt oder eingeschaltet wird, entscheidet
# die Person selbst. Ohne Terminal (ein unbeaufsichtigter Lauf) gilt Nein. 0 nur bei j, ja, y oder yes.
ASK_TTY="/dev/tty"
ask_person() {
    local answer=""
    printf '%s [j/N] ' "$1"
    if ! { IFS= read -r answer < "$ASK_TTY"; } 2> /dev/null; then
        echo ""
        log_info "Kein Terminal für die Antwort (ein unbeaufsichtigter Lauf): Das gilt als Nein."
        return 1
    fi
    case "${answer,,}" in j|ja|y|yes) return 0 ;; esac
    return 1
}

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

# --- LINUX-FAMILIE UND PAKETVERWALTUNG ---
# Die Werte aus $OS_RELEASE_FILE, die der Installer braucht, in einer Unter-Shell gelesen (wie Dockers früheres
# Installationsskript es tat). Von ID bleiben nur Buchstaben, Ziffern und . _ -, vom Namen nur druckbare Zeichen.
OS_ID="" OS_LIKE="" OS_CODENAME="" OS_UBUNTU_CODENAME="" OS_DEBIAN_CODENAME="" OS_VERSION_ID="" OS_NAME=""
OS_PLATFORM_ID=""
read_os_release() {
    local id="" like="" codename="" ubuntu_codename="" debian_codename="" version_id="" name="" platform_id=""
    if [ -r "$OS_RELEASE_FILE" ]; then
        { IFS= read -r id; IFS= read -r like; IFS= read -r codename; IFS= read -r ubuntu_codename
          IFS= read -r debian_codename; IFS= read -r version_id; IFS= read -r name; IFS= read -r platform_id; } < <(
            # shellcheck disable=SC1090
            . "$OS_RELEASE_FILE" > /dev/null 2>&1
            printf '%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n' "${ID:-}" "${ID_LIKE:-}" "${VERSION_CODENAME:-}" \
                "${UBUNTU_CODENAME:-}" "${DEBIAN_CODENAME:-}" "${VERSION_ID:-}" "${PRETTY_NAME:-}" "${PLATFORM_ID:-}")
    fi
    OS_ID="${id//[^A-Za-z0-9._-]/}"
    OS_LIKE="$like" OS_CODENAME="$codename" OS_UBUNTU_CODENAME="$ubuntu_codename"
    OS_DEBIAN_CODENAME="$debian_codename" OS_VERSION_ID="${version_id//[^A-Za-z0-9._-]/}"
    OS_NAME="${name//[^[:print:]]/}" OS_PLATFORM_ID="${platform_id//[^A-Za-z0-9.:_-]/}"
}

# Die Familie zu einer ID aus /etc/os-release: dieselbe Tabelle wie in scripts/hardware_probe.sh
# (configs/hardware_probe_schema.yml, tables.os_families; tests/test_install_distros.py prüft, dass sie gleich sind).
os_family_of() {
    case "$1" in
        debian) echo debian ;;
        ubuntu) echo debian ;;
        raspbian) echo debian ;;
        linuxmint) echo debian ;;
        pop) echo debian ;;
        elementary) echo debian ;;
        kali) echo debian ;;
        devuan) echo debian ;;
        armbian) echo debian ;;
        dietpi) echo debian ;;
        zorin) echo debian ;;
        neon) echo debian ;;
        pureos) echo debian ;;
        deepin) echo debian ;;
        mx) echo debian ;;
        rhel) echo rhel ;;
        centos) echo rhel ;;
        rocky) echo rhel ;;
        almalinux) echo rhel ;;
        ol) echo rhel ;;
        amzn) echo rhel ;;
        scientific) echo rhel ;;
        eurolinux) echo rhel ;;
        cloudlinux) echo rhel ;;
        virtuozzo) echo rhel ;;
        fedora) echo fedora ;;
        suse) echo suse ;;
        opensuse) echo suse ;;
        opensuse-leap) echo suse ;;
        opensuse-tumbleweed) echo suse ;;
        sles) echo suse ;;
        sled) echo suse ;;
        sle-micro) echo suse ;;
        arch) echo arch ;;
        archarm) echo arch ;;
        manjaro) echo arch ;;
        manjaro-arm) echo arch ;;
        endeavouros) echo arch ;;
        garuda) echo arch ;;
        artix) echo arch ;;
        alpine) echo alpine ;;
        postmarketos) echo alpine ;;
        gentoo) echo gentoo ;;
        funtoo) echo gentoo ;;
        openwrt) echo openwrt ;;
        void) echo void ;;
        nixos) echo nixos ;;
        slackware) echo slackware ;;
        *) return 1 ;;
    esac
}

# Setzt OS_FAMILY (erst ID, dann jeder Eintrag von ID_LIKE der Reihe nach; der erste Treffer der Tabelle zählt, sonst
# "other", wie bei der Hardware-Untersuchung) und PKG, die Paketverwaltung der Familie: apt (debian), dnf (rhel,
# fedora), zypper (suse), pacman (arch), apk (alpine), sonst leer.
OS_FAMILY="" PKG="" SYSTEM_DETECTED=0
detect_system() {
    local words=() word family
    read_os_release
    OS_FAMILY=other
    read -r -a words <<< "$OS_ID $OS_LIKE"
    for word in ${words[@]+"${words[@]}"}; do
        word="${word//[^A-Za-z0-9._-]/}"
        if [ -n "$word" ] && family="$(os_family_of "${word,,}")"; then OS_FAMILY="$family"; break; fi
    done
    case "$OS_FAMILY" in
        debian) PKG=apt ;;
        rhel|fedora) PKG=dnf ;;
        suse) PKG=zypper ;;
        arch) PKG=pacman ;;
        alpine) PKG=apk ;;
        *) PKG="" ;;
    esac
    SYSTEM_DETECTED=1
}

# Dieses System in Meldungen: sein Name aus /etc/os-release, seine ID und seine Familie.
system_label() {
    echo "${OS_NAME:-${OS_ID:-unbekannt}} (ID ${OS_ID:-unbekannt}, Familie $OS_FAMILY)"
}

# Der Befehl der Paketverwaltung PKG.
pkg_tool() {
    case "$PKG" in
        apt) echo apt-get ;;
        dnf|zypper|pacman|apk) echo "$PKG" ;;
    esac
}

# Setzt PKG_ARGV auf den Befehl, mit dem PKG Pakete installiert. Keiner schaltet eine Signaturprüfung ab oder übernimmt
# einen Schlüssel ungefragt: apt prüft die signierten Paketlisten, dnf und zypper die Signaturen der Pakete und
# Repository-Daten mit den Schlüsseln der Distribution, pacman jede Paketsignatur gegen archlinux-keyring, apk die
# signierten Indizes gegen /etc/apk/keys.
PKG_ARGV=()
pkg_argv() {
    case "$PKG" in
        apt) PKG_ARGV=(apt-get install -y) ;;
        dnf) PKG_ARGV=(dnf install -y) ;;
        zypper) PKG_ARGV=(zypper --non-interactive install) ;;
        pacman) PKG_ARGV=(pacman -S --needed --noconfirm) ;;
        apk) PKG_ARGV=(apk add) ;;
        *) PKG_ARGV=() ;;
    esac
}

# Hält an, wenn $1 fehlt und es keine Paketverwaltung gibt, die der Installer dafür nutzen kann: die Familie dieses
# Systems und die Anleitung für eine Installation von Hand.
pkg_ready() {
    local tool
    [ "$SYSTEM_DETECTED" = "1" ] || detect_system
    tool="$(pkg_tool)"
    if [ -z "$tool" ]; then
        log_error "$1 fehlt, und für $(system_label) kennt der Installer keine Paketverwaltung (er nutzt apt, dnf, zypper, pacman und apk). Installiere es selbst (was das Framework braucht: $MANUAL_URL; Docker: $DOCKER_MANUAL_URL) und starte danach 'sudo ./install.sh' erneut."
    fi
    if ! command -v "$tool" &> /dev/null; then
        log_error "$1 fehlt, und $tool, die Paketverwaltung von $(system_label), fehlt auch. Installiere es selbst (was das Framework braucht: $MANUAL_URL) und starte danach 'sudo ./install.sh' erneut."
    fi
    if ! pacman_database_ready; then log_error "$1 fehlt. $(pacman_database_text)"; fi
}

# pacman ohne Paketdatenbank (ein frisch eingerichtetes System, ein Container) findet kein Paket. Der Installer liest
# sie nicht selbst (pacman -Sy allein wäre ein Teil-Upgrade, das Arch nicht unterstützt, und -Syu aktualisierte das
# ganze System ungefragt); er sagt, was zu tun ist.
PACMAN_SYNC_DIR="/var/lib/pacman/sync"
pacman_database_ready() {
    local db
    [ "$PKG" = "pacman" ] || return 0
    for db in "$PACMAN_SYNC_DIR"/*.db; do
        if [ -f "$db" ]; then return 0; fi
    done
    return 1
}

pacman_database_text() {
    echo "pacman hat noch keine Paketdatenbank ($PACMAN_SYNC_DIR). Aktualisiere das System zuerst (sudo pacman -Syu) und starte danach 'sudo ./install.sh' erneut; der Installer aktualisiert das System nicht selbst."
}

# Installiert Pakete mit PKG. Vor der ersten Installation lesen apt und apk ihre Paketlisten neu; pacman nie
# (pacman -Sy allein wäre ein Teil-Upgrade, das Arch nicht unterstützt), dnf und zypper tun es selbst.
PKG_LISTS_READ=0
pkg_install() {
    pkg_argv
    [ "${#PKG_ARGV[@]}" -gt 0 ] || return 1
    if [ "$PKG_LISTS_READ" = "0" ]; then
        case "$PKG" in
            apt) apt-get update || return 1 ;;
            apk) apk update || return 1 ;;
        esac
        PKG_LISTS_READ=1
    fi
    "${PKG_ARGV[@]}" "$@"
}

# Ein Hinweis, wenn PKG Pakete nicht installieren konnte und es einen bekannten Grund dafür gibt.
pkg_failure_hint() {
    case "$PKG" in
        pacman) echo " Ist die Paketdatenbank veraltet, aktualisiere das System zuerst (sudo pacman -Syu) und starte 'sudo ./install.sh' erneut; der Installer selbst ruft nie 'pacman -Sy' auf (ein Teil-Upgrade, das Arch nicht unterstützt)." ;;
    esac
}

# --- PYTHON 3.11 BIS 3.14 ---
# Die Befehle, unter denen ein passendes Python gesucht wird: python3, auf dnf- und zypper-Systemen auch eine Version
# mit Nummer im Namen (RHEL 8/9 und ihre Abkömmlinge bringen als python3 3.6 oder 3.9, openSUSE Leap 15 3.6).
python_commands() {
    case "$PKG" in
        dnf|zypper) echo python3 python3.13 python3.12 python3.11 python3.14 ;;
        *) echo python3 ;;
    esac
}

# Die Pakete für python3 mit venv und pip, wie die Distribution sie nennt.
python_packages() {
    case "$PKG" in
        apt) echo python3 python3-venv python3-pip ;;
        dnf|zypper) echo python3 python3-pip ;;
        pacman) echo python ;;
        apk) echo python3 ;;
    esac
}

# Die Pakete einer Python-Version mit Nummer im Namen, je Zeile ein Satz, der Reihe nach versucht (3.13 wie unter
# Windows, dann 3.12, 3.11 und 3.14): dnf (RHEL 9: python3.12 und python3.12-pip) und zypper (Leap 15.6: python312
# und python312-pip). apt, pacman und apk bringen ein passendes python3 oder keins.
python_versioned_packages() {
    local v
    for v in 13 12 11 14; do
        case "$PKG" in
            dnf) echo "python3.$v python3.$v-pip" ;;
            zypper) echo "python3$v python3$v-pip" ;;
        esac
    done
}

# Setzt PYTHON auf den Pfad des ersten Pythons aus python_commands, das zwischen 3.11 und 3.14 liegt (requirements.txt
# passt nur dazu; außerhalb installierte pip stillschweigend nichts).
PYTHON=""
find_python() {
    local name path
    for name in $(python_commands); do
        path="$(command -v "$name" 2> /dev/null)" || continue
        if "$path" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] < (3, 15) else 1)' 2> /dev/null; then
            PYTHON="$path"
            return 0
        fi
    done
    return 1
}

python_missing_error() {
    local hint="z. B. Debian 12, Ubuntu 24.04 oder 'uv python install 3.13'"
    if sles_system; then
        hint="unter SLES aus SUSEs Python 3 Module: sudo SUSEConnect -p $(sles_python_module_product), dann zypper install python311 python311-pip; sonst 'uv python install 3.13'"
    fi
    log_error "Python 3.11 bis 3.14 wird benötigt, gefunden: $(python3 --version 2>&1). Bitte eine passende Version installieren ($hint)."
}

# --- SLES: PYTHON AUS SUSES PYTHON 3 MODULE ---
# SLES 15 bringt als python3 nur 3.6; python311 (15 SP4 bis SP7), python312 (SP6) und python313 (SP7) liegen in SUSEs
# Python 3 Module, das nicht eingeschaltet ist, bis man es mit SUSEConnect einschaltet. Der Name des Moduls für
# SUSEConnect: sle-module-python3/<Version>/<Architektur> (SUSE Customer Center, Paketsuche unter
# scc.suse.com/api/package_search, am 2026-10-23 nachgesehen: python311 und python311-pip liegen für SLES 15.6 x86_64 in
# sle-module-python3/15.6/x86_64). Ein Modul einschalten geht nur auf einem registrierten System.
sles_system() {
    case "$OS_ID" in sles|sled) ;; *) return 1 ;; esac
    case "$OS_VERSION_ID" in 15|15.*) return 0 ;; esac
    return 1
}

sles_python_module_product() {
    echo "sle-module-python3/${OS_VERSION_ID:-15}/$(uname -m)"
}

# Schaltet SUSEs Python 3 Module ein, wenn die Person zustimmt: 0, wenn es eben eingeschaltet wurde (die Pakete mit
# Nummer im Namen lohnen einen zweiten Versuch), 1, wenn es hier nicht passt (kein SLES, kein SUSEConnect, das Modul
# ist schon eingeschaltet). Ein nicht registriertes System, ein Nein oder ein Fehler von SUSEConnect hält mit dem
# Schritt an, den die Person selbst tun kann. Die Ausgabe von 'SUSEConnect --status' (mit dem Registrierungscode)
# wird nur gelesen, nie gezeigt.
sles_python_module() {
    local product status
    sles_system || return 1
    command -v SUSEConnect &> /dev/null || return 1
    product="$(sles_python_module_product)"
    status="$(SUSEConnect --status 2> /dev/null)" || status=""
    status="${status//[[:space:]]/}"
    if grep -q -E '"identifier":"sle-module-python3"[^}]*"status":"Registered"' <<< "$status"; then return 1; fi
    if ! grep -q -E '"identifier":"(SLES|SLED)"[^}]*"status":"Registered"' <<< "$status"; then
        log_error "Python 3.11 bis 3.14 kommt unter SLES aus SUSEs Python 3 Module ($product), und ein Modul lässt sich nur auf einem registrierten System einschalten; dieses SLES ist nicht registriert. Registriere es (sudo SUSEConnect -r <dein Registrierungscode>), schalte das Modul ein (sudo SUSEConnect -p $product) und starte danach 'sudo ./install.sh' erneut."
    fi
    log_warn "Python 3.11 bis 3.14 kommt unter SLES aus SUSEs Python 3 Module, das hier nicht eingeschaltet ist."
    if ! ask_person "SUSEs Python 3 Module einschalten (SUSEConnect -p $product)?"; then
        log_error "Ohne SUSEs Python 3 Module gibt es hier kein Python 3.11 bis 3.14; nichts wurde verändert. Selbst einschalten: sudo SUSEConnect -p $product, danach 'sudo ./install.sh' erneut."
    fi
    log_info "Schalte SUSEs Python 3 Module ein: SUSEConnect -p $product"
    if ! SUSEConnect -p "$product"; then
        log_error "SUSEConnect konnte das Python 3 Module ($product) nicht einschalten (Meldungen oben). Selbst: sudo SUSEConnect -p $product, danach 'sudo ./install.sh' erneut."
    fi
    return 0
}

# Versucht der Reihe nach die Pakete einer Python-Version mit Nummer im Namen (python_versioned_packages); 0, sobald
# ein passendes Python da ist.
python_try_versioned() {
    local sets=() set pkgs=()
    mapfile -t sets < <(python_versioned_packages)
    for set in ${sets[@]+"${sets[@]}"}; do
        [ -n "$set" ] || continue
        pkg_ready "Python 3.11 bis 3.14"
        log_info "$(python3 --version 2>&1) passt nicht (3.11 bis 3.14); versuche die Pakete $set..."
        read -r -a pkgs <<< "$set"
        if pkg_install "${pkgs[@]}" && find_python; then return 0; fi
    done
    return 1
}

# Ein passendes Python, notfalls über die Paketverwaltung: fehlt python3, die übliche Version der Distribution; passt
# sie nicht, der Reihe nach eine Version mit Nummer im Namen, bis eine passt. Unter SLES ohne eine davon schaltet der
# Installer mit der Zustimmung der Person SUSEs Python 3 Module ein und versucht es noch einmal.
ensure_python() {
    local pkgs=()
    find_python && return 0
    [ "$SYSTEM_DETECTED" = "1" ] || detect_system
    if ! command -v python3 &> /dev/null; then
        log_warn "Python3 fehlt. Installiere..."
        pkg_ready "Python 3.11 bis 3.14"
        read -r -a pkgs <<< "$(python_packages)"
        if ! pkg_install "${pkgs[@]}"; then log_error "Konnte Python nicht installieren.$(pkg_failure_hint)"; fi
        find_python && return 0
    fi
    python_try_versioned && return 0
    if sles_python_module && python_try_versioned; then return 0; fi
    python_missing_error
}

# --- DOCKER: NUR MIT GEPRÜFTEM SCHLÜSSEL ---
# Hält an, ohne Docker zu installieren: der Grund in klaren Worten und Dockers Anleitung für eine Installation von Hand.
docker_stop() {
    log_error "Docker wurde nicht installiert: $1 Installiere Docker selbst nach Dockers Anleitung für dein System ($DOCKER_MANUAL_URL) und starte danach 'sudo ./install.sh' erneut."
}

# Der Codename der Debian-Version in $DEBIAN_VERSION_FILE ("12.7" -> bookworm, "trixie/sid" -> trixie), so wie Dockers
# Installationsskript sie las, das der Installer früher ausführte; nichts, wenn dort keine steht (etwa "kali-rolling").
debian_version_codename() {
    local version=""
    if [ -r "$DEBIAN_VERSION_FILE" ]; then IFS= read -r version < "$DEBIAN_VERSION_FILE" || true; fi
    version="${version%%/*}"
    case "$version" in
        10|10.*) echo buster ;;
        11|11.*) echo bullseye ;;
        12|12.*) echo bookworm ;;
        13|13.*) echo trixie ;;
        *) if [[ "$version" =~ ^[a-z]+$ ]]; then echo "$version"; fi ;;
    esac
}

# Setzt DOCKER_REPO und DOCKER_SUITE (etwa "debian" und "bookworm") für dieses System oder hält an. Raspberry Pi OS mit
# 32 Bit (ID raspbian) bekommt die armhf-Pakete von Debian: Dockers Anleitung nennt sie dafür, und ab Docker Engine 29
# gibt es keine eigenen Pakete für raspbian mehr. ARMv6 (Raspberry Pi 1, Zero) hat keine offiziellen Pakete.
# Raspberry Pi OS mit 64 Bit meldet sich als debian. Debian testing ohne VERSION_CODENAME: der Codename aus
# $DEBIAN_VERSION_FILE. Ein System, das auf Ubuntu oder Debian aufbaut (ID_LIKE; etwa Linux Mint, Pop!_OS, LMDE),
# bekommt wie mit Dockers früher ausgeführtem Installationsskript die Pakete der Version, auf der es aufbaut:
# UBUNTU_CODENAME (so nennt es Dockers Anleitung für Linux Mint), bei Debian DEBIAN_CODENAME oder
# $DEBIAN_VERSION_FILE, nie den eigenen Codenamen. Jedes andere System hält an.
docker_apt_repo() {
    read_os_release
    local id="$OS_ID" like="$OS_LIKE" codename="$OS_CODENAME" ubuntu_codename="$OS_UBUNTU_CODENAME"
    local debian_codename="$OS_DEBIAN_CODENAME"
    case "$id" in
        debian) DOCKER_REPO=debian; DOCKER_SUITE="${codename:-$(debian_version_codename)}" ;;
        raspbian)
            if [ "$(uname -m)" = "armv6l" ]; then
                docker_stop "Für Raspberry Pi OS auf ARMv6 (Raspberry Pi 1, Zero) gibt es keine offiziellen Docker-Pakete."
            fi
            DOCKER_REPO=debian; DOCKER_SUITE="${codename:-$(debian_version_codename)}" ;;
        ubuntu) DOCKER_REPO=ubuntu; DOCKER_SUITE="${ubuntu_codename:-$codename}" ;;
        *)
            if [[ " $like " == *" ubuntu "* ]]; then
                DOCKER_REPO=ubuntu; DOCKER_SUITE="$ubuntu_codename"
            elif [[ " $like " == *" debian "* ]]; then
                DOCKER_REPO=debian; DOCKER_SUITE="${debian_codename:-$(debian_version_codename)}"
            else
                docker_stop "Der Installer richtet Docker nur unter Debian, Ubuntu, Raspberry Pi OS und darauf aufbauenden Systemen ein (dieses System laut $OS_RELEASE_FILE: ${id:-unbekannt})."
            fi ;;
    esac
    if ! [[ "$DOCKER_SUITE" =~ ^[a-z][a-z0-9-]*$ ]]; then
        docker_stop "$OS_RELEASE_FILE nennt keine Version dieses Systems (VERSION_CODENAME; bei einem System, das auf Ubuntu oder Debian aufbaut, UBUNTU_CODENAME oder DEBIAN_CODENAME, sonst die Debian-Version in $DEBIAN_VERSION_FILE), also kein passendes Docker-Repository."
    fi
    case "$id" in
        debian|raspbian|ubuntu) ;;
        *) log_info "${id:-Dieses System} baut auf $DOCKER_REPO $DOCKER_SUITE auf: Docker kommt aus Dockers Repository für $DOCKER_REPO $DOCKER_SUITE (wie bisher mit Dockers Installationsskript; offiziell unterstützt Docker nur $DOCKER_REPO selbst)." ;;
    esac
}

# Lädt Dockers Signaturschlüssel für das Repository $1 und legt nach $2 nur einen Schlüssel, dessen Fingerabdruck
# $3 ist (ohne $3: DOCKER_KEY_FINGERPRINT). Wie die Paketquellen eines Moduls (module_generator.vendor_blocks): eingelesen in ein
# eigenes, leeres GNUPGHOME, verglichen, dann exportiert. Strenger als dort: Die Datei muss genau einen Schlüssel
# enthalten, und der festgelegte Fingerabdruck muss der seines Hauptschlüssels sein (nicht der eines Unterschlüssels).
# Vor der Prüfung wird nichts außerhalb eines eigenen temporären Ordners geschrieben. gpg läuft mit --no-autostart: Es
# startet keinen gpg-agent, der als root nach dem Installer weiterliefe. Rückgabe 0 geschrieben, 1 nicht Dockers
# Schlüssel, 2 nicht geladen, 3 nicht geschrieben; außer bei 0 bleibt $2, wie es war.
fetch_docker_key() {
    local repo="$1" dest="$2" want="${3:-$DOCKER_KEY_FINGERPRINT}" home primary rc=0
    home="$(mktemp -d)" || return 3
    if ! curl -fsSL --proto '=https' --tlsv1.2 "$DOCKER_DOWNLOAD_URL/$repo/gpg" -o "$home/download.key"; then
        rc=2
    else
        # Was danach im Schlüsselbund steht, entscheidet, nicht der Rückgabewert des Einlesens: Ohne gpg-agent meldet
        # gpg auch für einen eingelesenen Schlüssel einen Fehler. Ein nicht eingelesener fehlt in der Liste.
        GNUPGHOME="$home" gpg --batch --no-autostart --quiet --import "$home/download.key" > /dev/null 2>&1 || true
        primary="$(GNUPGHOME="$home" gpg --batch --no-autostart --with-colons --list-keys 2> /dev/null | awk -F: '
            $1 == "pub" { keys++; want = 1; next }
            want && $1 == "fpr" { fpr = $10; want = 0 }
            END { if (keys == 1) print fpr }')"
        if [ "$primary" != "$want" ]; then
            rc=1
        elif ! GNUPGHOME="$home" gpg --batch --no-autostart --armor --export "$want" > "$home/docker.asc" \
            || [ ! -s "$home/docker.asc" ]; then
            rc=3
        elif ! install -d -m 0755 "$(dirname "$dest")" || ! install -m 0644 "$home/docker.asc" "$dest"; then
            rc=3
        fi
    fi
    rm -rf "$home"
    return "$rc"
}

# Gibt es schon eine eingeschaltete apt-Quelle für download.docker.com (Dockers Anleitung, ein früherer Lauf, Dockers
# Installationsskript)? Eine auskommentierte Zeile und ein Eintrag mit "Enabled: no" zählen nicht: apt liest sie nicht,
# und so schaltet ein Versions-Upgrade von Ubuntu fremde Quellen ab.
docker_source_exists() {
    local dir file
    dir="$(dirname "$DOCKER_SOURCES")"
    [ -e "$DOCKER_SOURCES" ] && return 0
    for file in "$(dirname "$dir")/sources.list" "$dir"/*.list; do
        if [ -f "$file" ] && grep -q -E '^[[:space:]]*deb(-src)?[[:space:]][^#]*download\.docker\.com' "$file"; then
            return 0
        fi
    done
    for file in "$dir"/*.sources; do
        # deb822: ein Absatz je Eintrag; Zeilen mit # sind Kommentare.
        if [ -f "$file" ] && awk 'BEGIN { RS = "" }
            { docker = 0; off = 0; n = split($0, lines, "\n")
              for (i = 1; i <= n; i++) {
                  line = tolower(lines[i])
                  if (line ~ /^[ \t]*#/) continue
                  if (line ~ /download\.docker\.com/) docker = 1
                  if (line ~ /^enabled:[ \t]*(no|false|off|disable)[ \t]*$/) off = 1
              }
              if (docker && !off) found = 1 }
            END { exit !found }' "$file"; then
            return 0
        fi
    done
    return 1
}

# Der Grund in klaren Worten, warum Dockers Schlüssel für das Repository $1 nicht übernommen wurde: $2 ist die Rückgabe
# von fetch_docker_key, $3 der Fingerabdruck, $4 das Ziel.
docker_key_problem() {
    case "$2" in
        1) echo "Der Schlüssel von $DOCKER_DOWNLOAD_URL/$1/gpg hat nicht den Fingerabdruck aus Dockers Anleitung ($3) oder enthält mehr als diesen einen Schlüssel; ihm wird nicht vertraut, und nichts wurde eingetragen." ;;
        2) echo "Dockers Signaturschlüssel ließ sich nicht von $DOCKER_DOWNLOAD_URL/$1/gpg laden (Proxy? Dann mit 'sudo -E ./install.sh')." ;;
        *) echo "Der geprüfte Schlüssel ließ sich nicht nach $4 schreiben." ;;
    esac
}

# Prüft Dockers Signaturschlüssel für das Repository $1 gegen den Fingerabdruck $3 und legt ihn nach $2, oder hält mit
# dem Grund an (fetch_docker_key).
check_docker_key() {
    local repo="$1" dest="$2" want="$3" rc=0
    fetch_docker_key "$repo" "$dest" "$want" || rc=$?
    if [ "$rc" = 0 ]; then
        log_success "Dockers Signaturschlüssel geprüft (Fingerabdruck $want): $dest"
        return 0
    fi
    docker_stop "$(docker_key_problem "$repo" "$rc" "$want" "$dest")"
}

# Woher Docker auf diesem System kommt (nach detect_system), oder Anhalten mit dem Grund. Setzt DOCKER_ROUTE:
#   apt-repo  Dockers apt-Repository (Debian, Ubuntu, Raspberry Pi OS und darauf aufbauende Systeme; docker_apt_repo)
#   rpm-repo  Docker CE aus Dockers rpm-Repository: Fedora, RHEL und CentOS Stream je aus ihrem eigenen, die übrigen
#             RHEL-Abkömmlinge (Rocky Linux, AlmaLinux, Oracle Linux, ...) aus dem für RHEL (docker_rhel_rebuild_plan)
#   distro    das Docker-Paket der Distribution: Fedora-Abkömmlinge moby-engine, Amazon Linux docker, openSUSE und
#             SLES docker (SLES: aus dem Containers Module), Arch und Abkömmlinge docker, Alpine docker (community)
#   podman    Podman mit dem Befehl docker (podman-docker) aus den Paketquellen der Distribution: nur der Ersatzweg der
#             RHEL-Familie (Fedora, RHEL, CentOS Stream und die RHEL-Abkömmlinge), wenn Docker CE dort nicht geht
#             (DOCKER_PODMAN_WHY sagt, warum)
# dazu DOCKER_REPO (Name in Dockers URL), DOCKER_RPM_RELEASE (die RHEL-Version in Dockers URL; leer: $releasever des
# Systems), DOCKER_PACKAGES, DOCKER_OPTIONAL (darf fehlen; ohne buildx baut das Framework mit dem älteren Builder),
# DOCKER_SERVICE (der Dienst, den der Installer startet) und DOCKER_FALLBACK (podman: geht Docker CE nicht, kommt
# Podman; leer: dann hält der Installer mit dem Grund an).
DOCKER_ROUTE="" DOCKER_REPO="" DOCKER_SUITE="" DOCKER_PACKAGES=() DOCKER_OPTIONAL=() DOCKER_SERVICE=""
DOCKER_RPM_RELEASE="" DOCKER_FALLBACK="" DOCKER_PODMAN_WHY=""
DOCKER_CE_PACKAGES=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
docker_plan() {
    DOCKER_ROUTE="" DOCKER_REPO="" DOCKER_SUITE="" DOCKER_PACKAGES=() DOCKER_OPTIONAL=() DOCKER_SERVICE=""
    DOCKER_RPM_RELEASE="" DOCKER_FALLBACK="" DOCKER_PODMAN_WHY=""
    [ "$SYSTEM_DETECTED" = "1" ] || detect_system
    case "$PKG" in
        apt)
            docker_apt_repo
            DOCKER_ROUTE=apt-repo
            DOCKER_PACKAGES=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin) ;;
        dnf)
            DOCKER_SERVICE=docker
            case "$OS_ID" in
                fedora|rhel|centos)
                    # Dieselbe Regel wie für die RHEL-Abkömmlinge (eine Regel für die ganze RHEL-Familie): Docker CE
                    # aus Dockers Repository für dieses System, die Frage nach dem, was dnf dafür entfernen müsste, und
                    # Podman als Ersatzweg.
                    DOCKER_ROUTE=rpm-repo DOCKER_REPO="$OS_ID" DOCKER_FALLBACK=podman
                    DOCKER_PACKAGES=("${DOCKER_CE_PACKAGES[@]}") ;;
                amzn) DOCKER_ROUTE=distro DOCKER_PACKAGES=(docker) ;;
                *)
                    if rhel_rebuild; then
                        docker_rhel_rebuild_plan
                    else
                        DOCKER_ROUTE=distro DOCKER_PACKAGES=(moby-engine)
                    fi ;;
            esac ;;
        zypper) DOCKER_ROUTE=distro DOCKER_PACKAGES=(docker) DOCKER_OPTIONAL=(docker-buildx) DOCKER_SERVICE=docker ;;
        pacman) DOCKER_ROUTE=distro DOCKER_PACKAGES=(docker docker-buildx) DOCKER_SERVICE=docker ;;
        apk) DOCKER_ROUTE=distro DOCKER_PACKAGES=(docker docker-cli-buildx) DOCKER_SERVICE=docker ;;
        *) docker_stop "Der Installer richtet Docker nur über die Paketverwaltung der Distribution ein: apt (Debian, Ubuntu, Raspberry Pi OS und darauf aufbauende Systeme), dnf (Fedora, RHEL, CentOS Stream, Rocky Linux, AlmaLinux und Verwandte), zypper (openSUSE, SLES), pacman (Arch, Manjaro, EndeavourOS) und apk (Alpine); dieses System laut $OS_RELEASE_FILE: $(system_label)." ;;
    esac
}

# Ein Abkömmling von RHEL (Rocky Linux, AlmaLinux, Oracle Linux, ...): dnf, weder Fedora, RHEL, CentOS Stream noch
# Amazon Linux und kein System, das auf Fedora aufbaut. Sie bekommen Docker CE aus Dockers RHEL-Repository.
rhel_rebuild() {
    [ "$PKG" = "dnf" ] || return 1
    case "$OS_ID" in fedora|rhel|centos|amzn) return 1 ;; esac
    [ "$OS_FAMILY" != "fedora" ]
}

# Die RHEL-Familie mit einer Install-Regel: Fedora, RHEL, CentOS Stream und die RHEL-Abkömmlinge. Docker CE aus Dockers
# Repository mit dem geprüften Schlüssel, die genaue Liste dessen, was dnf dafür entfernen oder ersetzen müsste, vor
# einer Frage, und Podman als Ersatzweg. Amazon Linux und die Systeme, die auf Fedora aufbauen, gehen ihren eigenen Weg.
rhel_family() {
    [ "$PKG" = "dnf" ] || return 1
    case "$OS_ID" in fedora|rhel|centos) return 0 ;; esac
    rhel_rebuild
}

# Die Hauptversion von RHEL, auf der dieses System aufbaut: aus PLATFORM_ID ("platform:el9"), sonst aus VERSION_ID
# ("9.4" -> 9); nichts, wenn keins davon eine Zahl nennt.
el_release() {
    local v="${OS_PLATFORM_ID#platform:el}"
    if [ "$v" != "$OS_PLATFORM_ID" ] && [[ "$v" =~ ^[0-9]+$ ]]; then echo "$v"; return 0; fi
    v="${OS_VERSION_ID%%.*}"
    if [[ "$v" =~ ^[0-9]+$ ]]; then echo "$v"; fi
}

# Podman (podman-docker) aus den Paketquellen der Distribution als Ersatzweg, mit dem Grund $1.
podman_route() {
    DOCKER_ROUTE=podman DOCKER_PACKAGES=(podman-docker) DOCKER_OPTIONAL=() DOCKER_SERVICE="" DOCKER_PODMAN_WHY="$1"
}

# Rocky Linux, AlmaLinux, Oracle Linux und die übrigen RHEL-Abkömmlinge (die Regel der RHEL-Familie): Standard ist Docker CE
# aus Dockers RHEL-Repository (download.docker.com/linux/rhel/<RHEL-Version>), sein Schlüssel geprüft wie unter Fedora,
# RHEL und CentOS, denn das Framework braucht BuildKit (etwa für Hailo). Podman ist der Ersatzweg: gleich hier, wenn
# Docker für diese Architektur nichts veröffentlicht oder die RHEL-Version nicht feststeht; sonst während der
# Installation, wenn das Repository nicht erreichbar ist, der Schlüssel nicht passt, dnf kein Paket für diese Version
# findet oder die Person die Pakete nicht entfernen lassen will, die dnf dafür entfernen müsste (install_docker_rpm).
docker_rhel_rebuild_plan() {
    local release arch
    release="$(el_release)" arch="$(uname -m)"
    DOCKER_FALLBACK=podman
    if [ -z "$release" ]; then
        podman_route "$OS_RELEASE_FILE nennt keine RHEL-Version dieses Systems (PLATFORM_ID oder VERSION_ID), also kein Verzeichnis in Dockers RHEL-Repository."
    elif [[ " $DOCKER_RHEL_ARCHES " != *" $arch "* ]]; then
        podman_route "Docker veröffentlicht in seinem RHEL-Repository keine Pakete für die Architektur $arch (nur für ${DOCKER_RHEL_ARCHES// /, })."
    else
        DOCKER_ROUTE=rpm-repo DOCKER_REPO=rhel DOCKER_RPM_RELEASE="$release"
        DOCKER_PACKAGES=("${DOCKER_CE_PACKAGES[@]}")
    fi
}

# Docker CE geht hier nicht, aus dem Grund $1: Mit dem Ersatzweg (DOCKER_FALLBACK=podman) merkt sich der Installer den
# Grund und gibt 1 zurück (install_docker nimmt dann Podman); sonst hält er mit dem Grund an.
docker_ce_fail() {
    if [ "$DOCKER_FALLBACK" = "podman" ]; then DOCKER_PODMAN_WHY="$1"; return 1; fi
    docker_stop "$1"
}

# Docker Engine aus Dockers offiziellem apt-Repository. Kein heruntergeladenes Skript läuft als root: Der
# Signaturschlüssel wird gegen den festgelegten Fingerabdruck geprüft, bevor apt ihm vertraut, und apt prüft dann
# jedes Paket gegen die signierte Paketliste. Passt etwas nicht, hält der Installer mit dem Grund an.
install_docker_apt() {
    local arch created=0 undone=""
    if ! command -v apt-get &> /dev/null; then docker_stop "apt-get fehlt auf diesem System."; fi
    log_info "Quelle: $DOCKER_DOWNLOAD_URL/$DOCKER_REPO ($DOCKER_SUITE). Ihr Schlüssel muss den Fingerabdruck $DOCKER_KEY_FINGERPRINT haben (Dockers Installationsanleitung)."
    if ! apt-get update || ! apt-get install -y --no-install-recommends ca-certificates curl gnupg; then
        docker_stop "Die Werkzeuge für die Schlüsselprüfung (ca-certificates, curl, gnupg) ließen sich nicht installieren (Meldungen oben)."
    fi
    check_docker_key "$DOCKER_REPO" "$DOCKER_KEYRING" "$DOCKER_KEY_FINGERPRINT"
    arch="$(dpkg --print-architecture 2> /dev/null)"
    if ! [[ "$arch" =~ ^[a-z0-9]+$ ]]; then docker_stop "dpkg nennt keine Architektur dieses Systems."; fi
    if docker_source_exists; then
        log_info "Eine apt-Quelle für download.docker.com gibt es schon in $(dirname "$DOCKER_SOURCES"); sie bleibt, wie sie ist."
    else
        if ! install -d -m 0755 "$(dirname "$DOCKER_SOURCES")" || ! printf '%s\n' "Types: deb" \
            "URIs: $DOCKER_DOWNLOAD_URL/$DOCKER_REPO" "Suites: $DOCKER_SUITE" "Components: stable" \
            "Architectures: $arch" "Signed-By: $DOCKER_KEYRING" > "$DOCKER_SOURCES"; then
            docker_stop "$DOCKER_SOURCES ließ sich nicht schreiben."
        fi
        created=1
    fi
    if ! apt-get update; then
        # Die eben angelegte Quelle wieder entfernen: Streitet sie mit einer anderen, bliebe apt sonst kaputt.
        if [ "$created" = "1" ]; then rm -f -- "$DOCKER_SOURCES"; undone="; $DOCKER_SOURCES ist wieder entfernt"; fi
        docker_stop "apt-get update mit Dockers Repository ist gescheitert (Meldungen oben)$undone."
    fi
    if ! apt-get install -y "${DOCKER_PACKAGES[@]}"; then
        docker_stop "Die Docker-Pakete ließen sich nicht installieren (Meldungen oben)."
    fi
    if ! command -v docker &> /dev/null; then docker_stop "Nach der Installation gibt es den Befehl docker nicht."; fi
    log_success "Docker ist installiert (aus $DOCKER_DOWNLOAD_URL/$DOCKER_REPO, Schlüssel und Pakete geprüft)."
}

# Gibt es schon ein eingeschaltetes dnf-Repository für download.docker.com (Dockers Anleitung, ein früherer Lauf)?
# Ein Abschnitt mit enabled=0 und auskommentierte Zeilen zählen nicht; ohne enabled ist ein Abschnitt eingeschaltet.
docker_rpm_repo_exists() {
    local file
    for file in "$(dirname "$DOCKER_RPM_REPO")"/*.repo; do
        if [ -f "$file" ] && awk '
            function section_end() { if (docker && !off) found = 1; docker = 0; off = 0 }
            /^[ \t]*[#;]/ { next }
            /^[ \t]*\[/ { section_end(); next }
            { line = tolower($0) }
            line ~ /^[ \t]*(baseurl|mirrorlist|metalink)[ \t]*=.*download\.docker\.com/ { docker = 1 }
            line ~ /^[ \t]*enabled[ \t]*=[ \t]*(0|no|false|off)[ \t]*$/ { off = 1 }
            END { section_end(); exit !found }' "$file"; then
            return 0
        fi
    done
    return 1
}

# Hält an, wenn es DOCKER_RPM_REPO schon gibt, aber ohne eingeschaltetes Repository für download.docker.com (etwa mit
# enabled=0 oder auskommentiert) und auch sonst keins eingeschaltet ist: Die Datei gehört der Person (Dockers Anleitung
# legt sie unter diesem Namen an). Der Installer ersetzt sie nicht und entfernt sie nie, auch nicht nach einer
# gescheiterten Installation; er hält an, bevor er etwas verändert.
docker_rpm_repo_check() {
    if ! docker_rpm_repo_exists && { [ -e "$DOCKER_RPM_REPO" ] || [ -L "$DOCKER_RPM_REPO" ]; }; then
        docker_stop "$DOCKER_RPM_REPO gibt es schon, aber Dockers Repository ist darin nicht eingeschaltet (enabled=0 oder auskommentiert); der Installer ändert und entfernt diese Datei nicht. Schalte es dort ein (enabled=1) oder benenne die Datei um."
    fi
}

# Nimmt zurück, was install_docker_rpm eben angelegt hat (DOCKER_RPM_CREATED: das Repository, vor 'rpm --import' auch
# den geprüften Schlüssel), und setzt DOCKER_RPM_UNDONE auf den Satzteil, der das sagt. Was es vorher gab, bleibt.
DOCKER_RPM_CREATED=() DOCKER_RPM_UNDONE=""
docker_rpm_undo() {
    local file names=()
    for file in ${DOCKER_RPM_CREATED[@]+"${DOCKER_RPM_CREATED[@]}"}; do
        if rm -f -- "$file"; then names+=("$file"); fi
    done
    DOCKER_RPM_CREATED=() DOCKER_RPM_UNDONE=""
    case "${#names[@]}" in
        0) ;;
        1) DOCKER_RPM_UNDONE="; ${names[0]} ist wieder entfernt" ;;
        *) DOCKER_RPM_UNDONE="; $(printf '%s und ' "${names[@]}" | sed 's/ und $//') sind wieder entfernt" ;;
    esac
}

# Die Pakete, die eine Transaktion von dnf (seine Ausgabe auf stdin, mit LC_ALL=C) entfernen oder ersetzen würde: die
# Abschnitte "Removing ..." (dnf 4 und dnf 5), "Replacing ..." und "Obsoleting ..." und die Zeilen "replacing <Paket>";
# jeder Name einmal, durch Leerzeichen getrennt.
dnf_removals() {
    awk '
        /^[^ \t].*:[ \t]*$/ { removing = ($0 ~ /^(Removing|Replacing|Obsoleting)/); next }
        /^Transaction Summary/ { removing = 0; next }
        /^[ \t]+replacing[ \t]/ { name = $2; sub(/\.(x86_64|aarch64|s390x|ppc64le|noarch|i686|armv7hl)$/, "", name)
                                print name; next }
        removing && /^ [^ \t]/ { print $1 }' | grep -E '^[A-Za-z0-9][A-Za-z0-9._+-]*$' | sort -u \
        | awk '{ printf "%s%s", (NR > 1 ? " " : ""), $0 }'
}

# Der Grund in klaren Worten, warum dnf Docker CE hier nicht installieren kann (seine Ausgabe in $1, mit LC_ALL=C).
dnf_problem() {
    local where
    where="${OS_NAME:-${OS_ID:-dieses System}}${DOCKER_RPM_RELEASE:+, RHEL $DOCKER_RPM_RELEASE}, $(uname -m)"
    if grep -q -E 'No match for argument|Unable to find a match|No package .* available|[Ss]tatus code: 404|error: 404' <<< "$1"; then
        echo "Docker veröffentlicht in $DOCKER_DOWNLOAD_URL/$DOCKER_REPO keine Pakete für dieses System ($where)."
    elif grep -q -E 'Cannot download|Failed to download metadata|Curl error|Could not resolve|Cannot retrieve' <<< "$1"; then
        echo "Dockers Repository $DOCKER_DOWNLOAD_URL/$DOCKER_REPO ist nicht erreichbar (Proxy? Dann mit 'sudo -E ./install.sh')."
    else
        echo "dnf kann Docker CE hier nicht installieren (Meldungen oben)."
    fi
}

# Was dnf für Docker CE täte, ohne etwas zu verändern: --assumeno beantwortet dnfs Frage nach der Transaktion mit Nein.
# --allowerasing steht nur hier, damit dnf ausrechnet, welche Pakete es dafür entfernen oder ersetzen müsste
# (podman-docker, runc, buildah, ...); installiert wird damit erst nach dem Ja der Person. Setzt DNF_REMOVALS (leer:
# keins) oder gibt 1 zurück, mit dem Grund in DNF_PROBLEM und dnfs letzten Meldungen auf dem Bildschirm.
DNF_REMOVALS="" DNF_PROBLEM=""
docker_rpm_transaction() {
    local out rc=0
    DNF_REMOVALS="" DNF_PROBLEM=""
    out="$(LC_ALL=C dnf install --assumeno --allowerasing "${DOCKER_PACKAGES[@]}" 2>&1)" || rc=$?
    if [ "$rc" != 0 ] && ! grep -q "Operation aborted" <<< "$out"; then
        printf '%s\n' "$out" | awk '{ line[NR] = $0 } END { for (i = (NR > 20 ? NR - 19 : 1); i <= NR; i++) print "   dnf: " line[i] }'
        DNF_PROBLEM="$(dnf_problem "$out")"
        return 1
    fi
    DNF_REMOVALS="$(dnf_removals <<< "$out")"
}

# Was der Installer sagt, wenn dnf die Docker-Pakete nicht installieren konnte (danach, was er zurückgenommen hat).
DOCKER_RPM_INSTALL_FAILED="Die Docker-Pakete ließen sich nicht installieren (Meldungen oben; nennt dnf einen Konflikt mit podman, buildah oder runc, sagt Dockers Anleitung, was vorher entfernt werden muss)"

# Schreibt DOCKER_RPM_REPO: Dockers Repository DOCKER_REPO, das nur den geprüften Schlüssel nennt (gpgkey=file://), mit
# gpgcheck=1; die RHEL-Version ist DOCKER_RPM_RELEASE, ohne sie $releasever des Systems.
docker_rpm_write_repo() {
    install -d -m 0755 "$(dirname "$DOCKER_RPM_REPO")" && printf '%s\n' "[docker-ce-stable]" \
        "name=Docker CE Stable - \$basearch" \
        "baseurl=$DOCKER_DOWNLOAD_URL/$DOCKER_REPO/${DOCKER_RPM_RELEASE:-\$releasever}/\$basearch/stable" "enabled=1" \
        "gpgcheck=1" "gpgkey=file://$DOCKER_RPM_KEYRING" > "$DOCKER_RPM_REPO"
}

# Docker Engine aus Dockers rpm-Repository (Fedora, RHEL, CentOS Stream je aus ihrem eigenen, die übrigen
# RHEL-Abkömmlinge aus dem für RHEL), wie Dockers Anleitung, aber ohne fremden Schlüssel: Der Signaturschlüssel wird
# gegen DOCKER_RPM_KEY_FINGERPRINT geprüft und erst dann mit rpm übernommen; das Repository nennt nur diese geprüfte
# Datei (gpgkey=file://), und dnf prüft die Signatur jedes Pakets (gpgcheck=1). Die ganze RHEL-Familie fragt vorher, was
# dnf dafür entfernen müsste (docker_rpm_asked); geht Docker CE nicht, gibt die Funktion 1 zurück (Ersatzweg Podman).
install_docker_rpm() {
    local tools=()
    if ! command -v dnf &> /dev/null; then docker_stop "dnf fehlt auf diesem System."; fi
    docker_rpm_repo_check
    log_info "Quelle: $DOCKER_DOWNLOAD_URL/$DOCKER_REPO. Ihr Schlüssel muss den Fingerabdruck $DOCKER_RPM_KEY_FINGERPRINT haben (Dockers Installationsanleitung)."
    log_info "Docker CE ist hier der Standard: Das Framework braucht BuildKit (etwa für Hailo). Geht es nicht, kommt Podman als Ersatzweg."
    # Nur, was fehlt: Fedora und RHEL bringen oft curl-minimal mit, das mit dem Paket curl streitet.
    command -v curl &> /dev/null || tools+=(curl)
    command -v gpg &> /dev/null || tools+=(gnupg2)
    if [ "${#tools[@]}" -gt 0 ] && ! dnf install -y "${tools[@]}"; then
        docker_ce_fail "Die Werkzeuge für die Schlüsselprüfung (${tools[*]}) ließen sich nicht installieren (Meldungen oben)."
        return 1
    fi
    docker_rpm_asked || return 1
    docker_service_start
    if ! command -v docker &> /dev/null; then docker_stop "Nach der Installation gibt es den Befehl docker nicht."; fi
    log_success "Docker ist installiert (aus $DOCKER_DOWNLOAD_URL/$DOCKER_REPO, Schlüssel und Pakete geprüft)."
}

# Die RHEL-Familie (Fedora, RHEL, CentOS Stream und die RHEL-Abkömmlinge). Bevor rpm dem Schlüssel vertraut, rechnet
# dnf aus, was es für Docker CE entfernen oder ersetzen müsste (docker_rpm_transaction, der einzige Aufruf mit
# --allowerasing, und nur als Probelauf mit --assumeno): Das nennt der Installer genau und fragt die Person; nie
# --allowerasing ungefragt, nie ein stiller Ersatz über obsoletes. Geht Docker CE nicht (kein Paket, Repository nicht erreichbar, Schlüssel passt nicht, ein Nein, dnf
# scheitert), nimmt sie zurück, was sie angelegt hat, merkt sich den Grund (docker_ce_fail) und gibt 1 zurück.
docker_rpm_asked() {
    local rc=0 allow=() repo_created=0 key_created=0
    DOCKER_RPM_CREATED=() DOCKER_RPM_UNDONE=""
    if [ ! -e "$DOCKER_RPM_KEYRING" ] && [ ! -L "$DOCKER_RPM_KEYRING" ]; then key_created=1; fi
    fetch_docker_key "$DOCKER_REPO" "$DOCKER_RPM_KEYRING" "$DOCKER_RPM_KEY_FINGERPRINT" || rc=$?
    if [ "$rc" != 0 ]; then
        docker_ce_fail "$(docker_key_problem "$DOCKER_REPO" "$rc" "$DOCKER_RPM_KEY_FINGERPRINT" "$DOCKER_RPM_KEYRING")"
        return 1
    fi
    log_success "Dockers Signaturschlüssel geprüft (Fingerabdruck $DOCKER_RPM_KEY_FINGERPRINT): $DOCKER_RPM_KEYRING"
    if [ "$key_created" = "1" ]; then DOCKER_RPM_CREATED+=("$DOCKER_RPM_KEYRING"); fi
    if docker_rpm_repo_exists; then
        log_info "Ein dnf-Repository für download.docker.com gibt es schon in $(dirname "$DOCKER_RPM_REPO"); es bleibt, wie es ist."
    else
        if ! docker_rpm_write_repo; then
            docker_rpm_undo
            docker_ce_fail "$DOCKER_RPM_REPO ließ sich nicht schreiben$DOCKER_RPM_UNDONE."
            return 1
        fi
        repo_created=1
        DOCKER_RPM_CREATED+=("$DOCKER_RPM_REPO")
    fi
    if ! docker_rpm_transaction; then
        docker_rpm_undo
        docker_ce_fail "$DNF_PROBLEM${DOCKER_RPM_UNDONE:+ (${DOCKER_RPM_UNDONE#; })}"
        return 1
    fi
    if [ -n "$DNF_REMOVALS" ]; then
        log_warn "Für Docker CE müsste dnf diese Pakete entfernen oder ersetzen: $DNF_REMOVALS"
        echo "   Nein: Docker CE wird nicht installiert, nichts wird entfernt, und Podman kommt als Ersatzweg."
        if ! ask_person "Diese Pakete entfernen und Docker CE installieren?"; then
            docker_rpm_undo
            docker_ce_fail "Für Docker CE müsste dnf diese Pakete entfernen: $DNF_REMOVALS; du hast Nein gesagt$DOCKER_RPM_UNDONE."
            return 1
        fi
        allow=(--allowerasing)
    fi
    # rpm vertraut dem geprüften Schlüssel, bevor dnf ein Paket lädt: dnf holt dann keinen Schlüssel selbst.
    if ! rpm --import "$DOCKER_RPM_KEYRING"; then
        docker_rpm_undo
        docker_ce_fail "rpm konnte den geprüften Schlüssel $DOCKER_RPM_KEYRING nicht übernehmen (Meldungen oben)$DOCKER_RPM_UNDONE."
        return 1
    fi
    # Ab hier vertraut rpm dem Schlüssel, und seine Datei bleibt. Ein eben angelegtes Repository wird bei einem Fehler
    # wieder entfernt: Ein unerreichbares hielte sonst jeden dnf-Aufruf an.
    DOCKER_RPM_CREATED=()
    if [ "$repo_created" = "1" ]; then DOCKER_RPM_CREATED=("$DOCKER_RPM_REPO"); fi
    if ! dnf install -y ${allow[@]+"${allow[@]}"} "${DOCKER_PACKAGES[@]}"; then
        docker_rpm_undo
        docker_ce_fail "$DOCKER_RPM_INSTALL_FAILED$DOCKER_RPM_UNDONE."
        return 1
    fi
}

# Alpine: Docker liegt im Repository community (Alpines eigenes, mit denselben Schlüsseln in /etc/apk/keys signiert
# wie main). Ist es nicht eingeschaltet, schaltet der Installer genau die community-Zeile zur eingeschalteten main-Zeile
# in APK_REPOSITORIES ein (eine auskommentierte wieder ein, sonst angehängt), wie 'setup-apkrepos -c'; andere Zeilen
# (etwa edge oder testing) bleiben, wie sie sind.
apk_enable_community() {
    local file="$APK_REPOSITORIES" main community tmp
    [ -f "$file" ] || return 1
    main="$(awk '/^[ \t]*(#|$)/ { next } { sub(/^[ \t]+/, ""); sub(/[ \t\/]+$/, "") } /\/main$/ { print; exit }' "$file")"
    [ -n "$main" ] || return 1
    community="${main%/main}/community"
    if awk -v want="$community" '/^[ \t]*#/ { next } { sub(/^[ \t]+/, ""); sub(/[ \t\/]+$/, "") }
        $0 == want { found = 1 } END { exit !found }' "$file"; then
        return 0
    fi
    tmp="$(mktemp)" || return 1
    if awk -v want="$community" '
        !done { line = $0; sub(/^[ \t]*#[ \t]*/, "", line); sub(/[ \t\/]+$/, "", line)
                if ($0 ~ /^[ \t]*#/ && line == want) { print want; done = 1; next } }
        { print }
        END { if (!done) print want }' "$file" > "$tmp" && install -m 0644 "$tmp" "$file"; then
        rm -f "$tmp"
        log_info "Alpines Repository community ist jetzt eingeschaltet: $community ($file)."
        PKG_LISTS_READ=0
        return 0
    fi
    rm -f "$tmp"
    return 1
}

# SLES 15: Docker kommt aus dem Containers Module, SUSEs eigener Quelle (mit SUSEs Schlüssel signiert). Wie beim Python
# 3 Module (sles_python_module) fragt der Installer die Person, bevor er es einschaltet: 0, wenn es eben eingeschaltet
# wurde (ein zweiter Versuch lohnt), 1, wenn es hier nicht passt (kein SLES, kein SUSEConnect, das Modul ist schon
# eingeschaltet). Ein nicht registriertes System, ein Nein oder ein Fehler von SUSEConnect hält mit dem Schritt an, den
# die Person selbst tun kann. Die Ausgabe von 'SUSEConnect --status' (mit dem Registrierungscode) wird nur gelesen.
sles_containers_module_product() {
    echo "sle-module-containers/${OS_VERSION_ID:-15}/$(uname -m)"
}

sles_containers_module() {
    local product status
    sles_system || return 1
    command -v SUSEConnect &> /dev/null || return 1
    product="$(sles_containers_module_product)"
    status="$(SUSEConnect --status 2> /dev/null)" || status=""
    status="${status//[[:space:]]/}"
    if grep -q -E '"identifier":"sle-module-containers"[^}]*"status":"Registered"' <<< "$status"; then return 1; fi
    if ! grep -q -E '"identifier":"(SLES|SLED)"[^}]*"status":"Registered"' <<< "$status"; then
        docker_stop "Docker kommt unter SLES aus SUSEs Containers Module ($product), und ein Modul lässt sich nur auf einem registrierten System einschalten; dieses SLES ist nicht registriert. Registriere es (sudo SUSEConnect -r <dein Registrierungscode>), schalte das Modul ein (sudo SUSEConnect -p $product) und starte danach 'sudo ./install.sh' erneut."
    fi
    log_warn "Docker kommt unter SLES aus SUSEs Containers Module, das hier nicht eingeschaltet ist."
    if ! ask_person "SUSEs Containers Module einschalten (SUSEConnect -p $product)?"; then
        docker_stop "Ohne SUSEs Containers Module gibt es hier kein Docker-Paket; nichts wurde verändert. Selbst einschalten: sudo SUSEConnect -p $product, danach 'sudo ./install.sh' erneut."
    fi
    log_info "Schalte SUSEs Containers Module ein: SUSEConnect -p $product"
    if ! SUSEConnect -p "$product"; then
        docker_stop "SUSEConnect konnte das Containers Module ($product) nicht einschalten (Meldungen oben). Selbst: sudo SUSEConnect -p $product, danach 'sudo ./install.sh' erneut."
    fi
    return 0
}

# Startet DOCKER_SERVICE und schaltet ihn für jeden Start ein (systemd, unter Alpine OpenRC). Gelingt das nicht (etwa
# ohne laufendes systemd), sagt der Installer, wie es geht, und macht weiter.
docker_service_start() {
    [ -n "$DOCKER_SERVICE" ] || return 0
    if [ "$PKG" = "apk" ]; then
        if rc-update add "$DOCKER_SERVICE" default && rc-service "$DOCKER_SERVICE" start; then return 0; fi
        log_warn "Docker ist installiert, ließ sich aber nicht starten (Meldungen oben). Selbst starten: rc-update add docker default; rc-service docker start"
    else
        if systemctl enable --now "$DOCKER_SERVICE"; then return 0; fi
        log_warn "Docker ist installiert, ließ sich aber nicht starten (Meldungen oben). Selbst starten: sudo systemctl enable --now docker"
    fi
}

# Docker (oder Podman mit dem Befehl docker) aus den Paketquellen der Distribution; die Paketverwaltung prüft die
# Signaturen mit den Schlüsseln, denen das System schon vertraut. Kein fremdes Repository wird eingetragen.
install_docker_distro() {
    local tool package
    tool="$(pkg_tool)"
    if ! command -v "$tool" &> /dev/null; then docker_stop "$tool fehlt auf diesem System."; fi
    if ! pacman_database_ready; then docker_stop "$(pacman_database_text)"; fi
    log_info "Quelle: die Paketquellen von $(system_label): ${DOCKER_PACKAGES[*]} (mit $tool; die Signaturen prüft $tool mit den Schlüsseln der Distribution)."
    if [ "$PKG" = "apk" ] && ! apk_enable_community; then
        docker_stop "Alpines Repository community ließ sich in $APK_REPOSITORIES nicht einschalten (keine eingeschaltete main-Zeile gefunden oder die Datei nicht schreibbar)."
    fi
    if ! pkg_install "${DOCKER_PACKAGES[@]}"; then
        if ! sles_containers_module || ! pkg_install "${DOCKER_PACKAGES[@]}"; then
            docker_stop "Die Pakete ${DOCKER_PACKAGES[*]} ließen sich mit $tool nicht installieren (Meldungen oben).$(pkg_failure_hint)"
        fi
    fi
    for package in ${DOCKER_OPTIONAL[@]+"${DOCKER_OPTIONAL[@]}"}; do
        if ! pkg_install "$package"; then
            log_warn "$package ließ sich nicht installieren; das Framework baut Images dann mit Dockers älterem Builder (langsamer)."
        fi
    done
    docker_service_start
    if ! command -v docker &> /dev/null; then docker_stop "Nach der Installation gibt es den Befehl docker nicht."; fi
    if [ "$DOCKER_ROUTE" = "podman" ]; then
        log_success "Podman ist installiert (${DOCKER_PACKAGES[*]}: der Befehl docker ruft Podman auf; Pakete von $tool geprüft)."
    else
        log_success "Docker ist installiert (${DOCKER_PACKAGES[*]} aus den Paketquellen der Distribution, Pakete von $tool geprüft)."
    fi
}

# Docker über die Paketverwaltung dieses Systems (docker_plan), nie mit einem heruntergeladenen Skript. In der
# RHEL-Familie kommt Podman, wenn Docker CE nicht geht (install_podman_fallback).
install_docker() {
    detect_system
    docker_plan
    case "$DOCKER_ROUTE" in
        apt-repo) install_docker_apt ;;
        rpm-repo) install_docker_rpm || install_podman_fallback ;;
        podman) install_podman_fallback ;;
        *) install_docker_distro ;;
    esac
}

# Der Ersatzweg der RHEL-Familie: Podman aus den Paketquellen der Distribution (podman-docker: der Befehl docker ruft
# Podman auf), mit dem Grund, warum Docker CE nicht installiert wurde, was unter Podman nicht geht und dem Weg zu
# Docker CE.
install_podman_fallback() {
    log_warn "Docker CE wird hier nicht installiert: $DOCKER_PODMAN_WHY"
    log_info "Ersatzweg: Podman aus den Paketquellen von $(system_label). Unter Podman gibt es kein BuildKit, und Dockers Anfrage nach Grafikkarten (--gpus) übergeht Podman; was das braucht, lehnt das Framework mit dem Grund ab (docs/Features.md, Podman). Später zu Docker CE: den Grund oben beheben, 'sudo dnf remove podman-docker', dann 'sudo ./install.sh' erneut."
    podman_route "$DOCKER_PODMAN_WHY"
    install_docker_distro
}

# Podman (podman-docker) läuft ohne Dienst mit Root-Rechten und ohne Gruppe docker: Das Framework erreicht es über
# Podmans Docker-kompatible Schnittstelle der Person. Der Installer schaltet sie für sie ein (systemd des Benutzers)
# und sagt, welche Adresse das Framework dafür braucht.
podman_for_user() {
    local uid
    if [ -z "$REAL_USER" ] || [ "$REAL_USER" = "root" ]; then
        log_warn "Podman: Die Docker-kompatible Schnittstelle wird für deinen Benutzer eingerichtet, nicht für root. Als dein Benutzer: systemctl --user enable --now podman.socket"
        return 0
    fi
    uid="$(id -u "$REAL_USER" 2> /dev/null)"
    if ! systemctl --user --machine="$REAL_USER@" enable --now podman.socket; then
        log_warn "Podmans Docker-kompatible Schnittstelle ließ sich für '$REAL_USER' nicht einschalten (Meldungen oben). Als '$REAL_USER' selbst: systemctl --user enable --now podman.socket"
    fi
    log_info "Podman braucht keine Gruppe docker und keinen Dienst mit Root-Rechten. Das Framework findet Podmans Schnittstelle selbst und nimmt sie auch vor einem Docker-Dienst mit Root-Rechten (unix:///run/user/${uid:-UID}/podman/podman.sock); andere Programme, die Docker ansprechen, brauchen DOCKER_HOST=unix:///run/user/${uid:-UID}/podman/podman.sock (etwa in ~/.bashrc)."
}

# --- CONTAINER-ENGINE OHNE ROOT-RECHTE (STANDARD) ---
# Keine dauerhaften Rechte, die mehr können als nötig (docs/DESIGN_DE.md): Standard ist eine Container-Engine, die mit
# den Rechten der Person läuft. rootless Podman (podman, dazu podman-docker für den Befehl docker, wo es das gibt) mit
# Sub-ID-Bereichen für die Person und ihrem Dienst podman.socket (systemd des Benutzers); mit --engine=docker oder wo
# Podman sich nicht installieren lässt rootless Docker (Docker CE aus Dockers Repository mit geprüftem Schlüssel, dazu
# docker-ce-rootless-extras; dockerd-rootless-setuptool.sh richtet den Dienst des Benutzers ein). Docker als root mit
# der Gruppe docker nur mit --rootful und einer klaren Warnung (rootful_warning). Das Framework nimmt eine Engine ohne
# Root-Rechte, sobald es eine gibt (orchestrator/utils/docker_engine.py).
ROOTFUL=0
ENGINE_CHOICE="podman"
# Die Sub-ID-Bereiche für Benutzer und Gruppen. Ein Bereich von 65536 IDs je Konto ist der übliche (useradd, Podman,
# Docker); er reicht bis zum Stufen-Konto des Build-Containers (uid und gid 64010).
SUBUID_FILE="/etc/subuid"
SUBGID_FILE="/etc/subgid"
SUBID_COUNT=65536
SUBID_FIRST=100000
# Wo systemd die Laufzeitordner der Benutzer anlegt (/run/user/<uid>: rootless Docker legt dort docker.sock an, Podman
# podman/podman.sock).
USER_RUNTIME_ROOT="/run/user"
# Was setup_rootless eingerichtet hat (podman, docker; leer: nichts), für finish.
ROOTLESS_ENGINE=""

# Die Pakete für rootless Podman je Paketverwaltung (PODMAN_PACKAGES) und was dazu darf, aber fehlen kann
# (PODMAN_OPTIONAL: podman-docker gibt den Befehl docker; neben einem echten Docker bleibt es weg, denn beide liefern
# /usr/bin/docker). apt braucht newuidmap und newgidmap (uidmap), slirp4netns und den D-Bus des Benutzers für den
# Dienst; dnf, zypper und pacman bringen das mit podman; Alpine hat newuidmap in shadow-uidmap.
PODMAN_PACKAGES=() PODMAN_OPTIONAL=()
podman_plan() {
    [ "$SYSTEM_DETECTED" = "1" ] || detect_system
    PODMAN_OPTIONAL=(podman-docker)
    case "$PKG" in
        apt) PODMAN_PACKAGES=(podman uidmap slirp4netns dbus-user-session) ;;
        dnf|zypper|pacman) PODMAN_PACKAGES=(podman) ;;
        apk) PODMAN_PACKAGES=(podman shadow-uidmap) ;;
        *) PODMAN_PACKAGES=() PODMAN_OPTIONAL=() ;;
    esac
    if docker_is_real_docker; then PODMAN_OPTIONAL=(); fi
}

# Ist der Befehl docker ein echtes Docker (nicht podman-docker)?
docker_is_real_docker() {
    command -v docker &> /dev/null && ! docker_is_podman
}

# Rootless Docker geht hier: aus Dockers apt- oder rpm-Repository (docker-ce-rootless-extras, unter apt mit uidmap)
# oder unter openSUSE aus dem Paket docker-rootless-extras. Setzt DOCKER_PACKAGES dafür; 1, wenn es hier keins gibt
# (ROOTLESS_DOCKER_WHY sagt, warum).
ROOTLESS_DOCKER_WHY=""
rootless_docker_plan() {
    ROOTLESS_DOCKER_WHY=""
    docker_plan
    case "$DOCKER_ROUTE" in
        apt-repo) DOCKER_PACKAGES+=(docker-ce-rootless-extras uidmap) ;;
        rpm-repo) DOCKER_PACKAGES+=(docker-ce-rootless-extras) ;;
        distro)
            if [ "$PKG" = "zypper" ]; then
                DOCKER_PACKAGES+=(docker-rootless-extras)
            else
                ROOTLESS_DOCKER_WHY="Rootless Docker kommt nur aus Dockers Repository (apt, dnf) oder unter openSUSE aus docker-rootless-extras; $(system_label) hat dafür kein Paket."
                return 1
            fi ;;
        *) ROOTLESS_DOCKER_WHY="Docker CE geht hier nicht: $DOCKER_PODMAN_WHY"; return 1 ;;
    esac
    # Der Dienst mit Root-Rechten wird nicht gestartet: rootless Docker läuft als Dienst des Benutzers.
    DOCKER_SERVICE=""
}

# Der Benutzer, für den die Engine eingerichtet wird (wer sudo aufgerufen hat), seine uid und sein Laufzeitordner.
engine_user() {
    REAL_USER="${SUDO_USER:-${USER:-}}"
    REAL_UID="$(id -u "$REAL_USER" 2> /dev/null)" || REAL_UID=""
}

# Befehl als die Person, mit ihrem systemd des Benutzers (XDG_RUNTIME_DIR, D-Bus), nie mit der Umgebung von root.
as_engine_user() {
    local runtime="${USER_RUNTIME_ROOT}/${REAL_UID:-0}"
    if command -v sudo &> /dev/null; then
        sudo -H -u "$REAL_USER" env XDG_RUNTIME_DIR="$runtime" DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime/bus" "$@"
    else
        runuser -u "$REAL_USER" -- env XDG_RUNTIME_DIR="$runtime" DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime/bus" "$@"
    fi
}

# Hat $1 (subuid- oder subgid-Datei) für die Person (Name oder uid) einen Bereich von mindestens SUBID_COUNT IDs?
subids_present() {
    [ -f "$1" ] || return 1
    awk -F: -v name="$REAL_USER" -v uid="$REAL_UID" -v need="$SUBID_COUNT" \
        '($1 == name || (uid != "" && $1 == uid)) && $3 + 0 >= need { found = 1 } END { exit !found }' "$1"
}

# Der erste Anfang eines freien Bereichs hinter allen Bereichen beider Dateien (mindestens SUBID_FIRST).
subids_next_start() {
    cat "$SUBUID_FILE" "$SUBGID_FILE" 2> /dev/null | awk -F: -v first="$SUBID_FIRST" \
        'NF == 3 && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/ { end = $2 + $3; if (end > max) max = end }
         END { print (max > first ? max : first) }'
}

# Gibt der Person Sub-ID-Bereiche für Benutzer und Gruppen, wo sie noch keinen ausreichenden hat (rootless Podman
# und rootless Docker bilden die IDs eines Containers darauf ab). Ein vorhandener Bereich bleibt, wie er ist.
# Rückgabe 0: alles da (SUBIDS_ADDED nennt, was neu ist), 1: nicht schreibbar.
SUBIDS_ADDED=""
ensure_subids() {
    local start file
    SUBIDS_ADDED=""
    start="$(subids_next_start)"
    for file in "$SUBUID_FILE" "$SUBGID_FILE"; do
        subids_present "$file" && continue
        if ! printf '%s:%s:%s\n' "$REAL_USER" "$start" "$SUBID_COUNT" >> "$file"; then
            log_warn "$file ließ sich nicht schreiben; ohne Sub-IDs läuft die Engine ohne Root-Rechte nicht. Selbst: sudo usermod --add-subuids $start-$((start + SUBID_COUNT - 1)) --add-subgids $start-$((start + SUBID_COUNT - 1)) $REAL_USER"
            return 1
        fi
        SUBIDS_ADDED="${SUBIDS_ADDED:+$SUBIDS_ADDED, }$file"
    done
    if [ -n "$SUBIDS_ADDED" ]; then
        log_info "Sub-IDs für '$REAL_USER': $SUBID_COUNT IDs ab $start ($SUBIDS_ADDED)."
    fi
}

# Ist $1 in der Gruppe docker selbst? 'groups NAME' sagt "NAME : g1 g2"; gezählt wird nur ein Gruppenname, der genau
# docker ist (nicht docker-users, nicht ein Benutzername, der docker enthält).
in_docker_group() {
    groups "$1" 2> /dev/null | awk -F: '{ n = split($NF, g, /[ \t]+/); for (i = 1; i <= n; i++) if (g[i] == "docker") f = 1 } END { exit !f }'
}

# Ist das ein Socket von rootless Docker der Person? podman-docker legt dort einen Link auf Podmans Socket an (Podman,
# kein Docker); nur ein echter Socket zählt.
rootless_docker_there() {
    local sock="${USER_RUNTIME_ROOT}/${REAL_UID:-x}/docker.sock"
    [ -S "$sock" ] && [ ! -L "$sock" ]
}

# Sagt klar, wenn die Person in einer Gruppe mit Root-Wirkung ist (docker); nimmt sie nie selbst heraus.
root_group_note() {
    if in_docker_group "$REAL_USER"; then
        log_warn "'$REAL_USER' ist in der Gruppe docker: Wer darin ist, steuert den Docker-Dienst, der als root läuft, und hat damit dauerhaft Rechte wie root auf diesem Rechner, auch wenn das Framework nicht läuft. Das Framework braucht die Gruppe nicht mehr (es nimmt die Engine ohne Root-Rechte). Herausnehmen, wenn nichts anderes sie braucht: sudo gpasswd -d $REAL_USER docker (danach ab- und wieder anmelden)."
    fi
}

# Rootless Podman: die Pakete, die Sub-IDs, der Dienst des Benutzers. 1, wenn sich Podman nicht installieren ließ
# (ROOTLESS_PODMAN_WHY sagt, warum).
ROOTLESS_PODMAN_WHY=""
setup_rootless_podman() {
    local tool package
    ROOTLESS_PODMAN_WHY=""
    if ! command -v podman &> /dev/null; then
        podman_plan
        tool="$(pkg_tool)"
        if [ "${#PODMAN_PACKAGES[@]}" -eq 0 ] || ! command -v "$tool" &> /dev/null; then
            ROOTLESS_PODMAN_WHY="Für $(system_label) kennt der Installer keine Paketverwaltung für Podman."
            return 1
        fi
        if ! pacman_database_ready; then ROOTLESS_PODMAN_WHY="$(pacman_database_text)"; return 1; fi
        log_info "Quelle: die Paketquellen von $(system_label): ${PODMAN_PACKAGES[*]} (mit $tool; die Signaturen prüft $tool mit den Schlüsseln der Distribution)."
        if [ "$PKG" = "apk" ] && ! apk_enable_community; then
            ROOTLESS_PODMAN_WHY="Alpines Repository community ließ sich in $APK_REPOSITORIES nicht einschalten."
            return 1
        fi
        if ! pkg_install "${PODMAN_PACKAGES[@]}"; then
            ROOTLESS_PODMAN_WHY="Die Pakete ${PODMAN_PACKAGES[*]} ließen sich mit $tool nicht installieren (Meldungen oben).$(pkg_failure_hint)"
            return 1
        fi
        for package in ${PODMAN_OPTIONAL[@]+"${PODMAN_OPTIONAL[@]}"}; do
            if ! pkg_install "$package"; then
                log_warn "$package ließ sich nicht installieren: Ohne es gibt es den Befehl docker nicht; das Framework spricht Podman über seinen Socket an."
            fi
        done
    fi
    ensure_subids || true
    ROOTLESS_ENGINE=podman
    DOCKER_ROUTE=podman
    if command -v systemctl &> /dev/null && [ "$PKG" != "apk" ]; then
        podman_for_user
        # Podman übernimmt neue Sub-IDs erst nach 'podman system migrate'.
        if [ -n "$SUBIDS_ADDED" ]; then as_engine_user podman system migrate &> /dev/null || true; fi
    else
        log_info "Ohne systemd gibt es keinen Dienst podman.socket; der Befehl docker (podman-docker) ruft Podman direkt auf. Für das Framework selbst als '$REAL_USER': podman system service --time=0 &"
    fi
    log_success "Rootless Podman ist eingerichtet: Es läuft mit den Rechten von '$REAL_USER', ohne Dienst mit Root-Rechten und ohne Gruppe docker."
}

# Rootless Docker: Docker CE aus Dockers Repository (geprüfter Schlüssel) mit docker-ce-rootless-extras, der Dienst
# mit Root-Rechten aus, die Sub-IDs, dann dockerd-rootless-setuptool.sh als die Person. 1, wenn es hier nicht geht
# (ROOTLESS_DOCKER_WHY sagt, warum).
setup_rootless_docker() {
    if ! rootless_docker_plan; then return 1; fi
    case "$DOCKER_ROUTE" in
        apt-repo) install_docker_apt ;;
        rpm-repo) if ! install_docker_rpm; then ROOTLESS_DOCKER_WHY="$DOCKER_PODMAN_WHY"; return 1; fi ;;
        *) install_docker_distro ;;
    esac
    # Dockers Anleitung für rootless: der Dienst mit Root-Rechten aus (apt startet ihn bei der Installation).
    if command -v systemctl &> /dev/null; then systemctl disable --now docker.service docker.socket &> /dev/null || true; fi
    ensure_subids || true
    if ! as_engine_user dockerd-rootless-setuptool.sh install; then
        log_warn "dockerd-rootless-setuptool.sh ließ sich für '$REAL_USER' nicht ausführen (Meldungen oben; es braucht eine Anmeldung von '$REAL_USER'). Als '$REAL_USER' selbst: dockerd-rootless-setuptool.sh install"
    fi
    ROOTLESS_ENGINE=docker
    log_success "Rootless Docker ist eingerichtet: Es läuft als Dienst von '$REAL_USER' mit dessen Rechten, ohne Gruppe docker. Das Framework findet seinen Socket selbst (unix://${USER_RUNTIME_ROOT}/${REAL_UID:-UID}/docker.sock)."
}

# Der Standard: eine Engine ohne Root-Rechte für die Person. Gibt es schon eine (podman, oder rootless Docker der
# Person), richtet der Installer nur Sub-IDs und Dienst ein; sonst kommt Podman (mit --engine=docker rootless Docker;
# geht das eine nicht, das andere, mit dem Grund).
setup_rootless() {
    engine_user
    if [ -z "$REAL_USER" ] || [ "$REAL_USER" = "root" ]; then
        log_warn "Die Container-Engine ohne Root-Rechte wird für deinen Benutzer eingerichtet, nicht für root: Starte den Installer mit sudo als dein Benutzer (sudo ./install.sh)."
        return 0
    fi
    log_info "Container-Engine: ohne Root-Rechte (Standard). Sie läuft mit den Rechten von '$REAL_USER'; nichts bekommt dauerhaft Rechte wie root. Docker als root nur mit --rootful."
    if rootless_docker_there; then
        ensure_subids || true
        ROOTLESS_ENGINE=docker
        log_success "Rootless Docker von '$REAL_USER' ist schon da; das Framework nimmt es."
    elif [ "$ENGINE_CHOICE" = "docker" ]; then
        if ! setup_rootless_docker; then
            log_warn "Rootless Docker geht hier nicht: $ROOTLESS_DOCKER_WHY Stattdessen: rootless Podman."
            setup_rootless_podman || engine_stop "Weder rootless Docker noch rootless Podman ließen sich einrichten: $ROOTLESS_PODMAN_WHY"
        fi
    elif ! setup_rootless_podman; then
        log_warn "Podman ließ sich nicht einrichten: $ROOTLESS_PODMAN_WHY Stattdessen: rootless Docker."
        setup_rootless_docker || engine_stop "Weder rootless Podman noch rootless Docker ließen sich einrichten: $ROOTLESS_PODMAN_WHY $ROOTLESS_DOCKER_WHY"
    fi
    root_group_note
}

# Docker als root mit der Gruppe docker, nur auf den ausdrücklichen Wunsch der Person (--rootful): die Warnung vorab.
rootful_warning() {
    engine_user
    log_warn "--rootful: Docker läuft als root, und '${REAL_USER:-dein Benutzer}' kommt in die Gruppe docker. Damit hat das Konto dauerhaft Rechte wie root auf diesem Rechner, auch wenn das Framework nicht läuft: Jedes Programm, das als dieses Konto läuft, kann sie nutzen. Ohne --rootful richtet der Installer eine Engine ohne Root-Rechte ein (rootless Podman), mit der das Framework genauso baut."
}

# Hält an, ohne eine Container-Engine einzurichten: der Grund und die Anleitungen für eine Einrichtung von Hand.
PODMAN_MANUAL_URL="https://podman.io/docs/installation"
ROOTLESS_DOCKER_MANUAL_URL="https://docs.docker.com/engine/security/rootless/"
engine_stop() {
    log_error "Die Container-Engine wurde nicht eingerichtet: $1 Richte rootless Podman ($PODMAN_MANUAL_URL) oder rootless Docker ($ROOTLESS_DOCKER_MANUAL_URL) selbst ein und starte danach 'sudo ./install.sh' erneut; Docker als root nur, wenn du das willst: 'sudo ./install.sh --rootful'."
}

# Der Probelauf der Engine ohne Root-Rechte (plan_installation): dieselben Entscheidungen wie setup_rootless, ohne
# etwas aufzurufen.
plan_subids() {
    local start
    if subids_present "$SUBUID_FILE" && subids_present "$SUBGID_FILE"; then
        echo "     Sub-IDs für '$REAL_USER': vorhanden ($SUBUID_FILE, $SUBGID_FILE)"
    else
        start="$(subids_next_start)"
        echo "     Sub-IDs für '$REAL_USER', wo noch keine sind: $SUBID_COUNT IDs ab $start in $SUBUID_FILE und $SUBGID_FILE"
    fi
}

plan_rootless_podman() {
    local optional=""
    if command -v podman &> /dev/null; then
        echo "   Container-Engine: rootless Podman (Standard), vorhanden ($(command -v podman))"
    else
        podman_plan
        pkg_argv
        if [ "${#PODMAN_PACKAGES[@]}" -eq 0 ] || ! command -v "$(pkg_tool)" &> /dev/null; then return 1; fi
        if ! pacman_database_ready; then engine_stop "$(pacman_database_text)"; fi
        if [ "${#PODMAN_OPTIONAL[@]}" -gt 0 ]; then optional=" (dazu, wenn es das gibt: ${PODMAN_OPTIONAL[*]})"; fi
        echo "   Container-Engine: rootless Podman (Standard) -> ${PKG_ARGV[*]} ${PODMAN_PACKAGES[*]}$optional"
        if [ "$PKG" = "apk" ]; then echo "     aus Alpines Repository community (wird in $APK_REPOSITORIES eingeschaltet, falls nötig)"; fi
    fi
    plan_subids
    if [ "$PKG" = "apk" ]; then
        echo "     ohne systemd kein Dienst podman.socket: der Befehl docker (podman-docker) ruft Podman direkt auf"
    else
        echo "     Dienst des Benutzers: systemctl --user --machine=$REAL_USER@ enable --now podman.socket (unix://${USER_RUNTIME_ROOT}/${REAL_UID:-UID}/podman/podman.sock)"
    fi
}

plan_rootless_docker() {
    rootless_docker_plan || return 1
    pkg_argv
    case "$DOCKER_ROUTE" in
        apt-repo) echo "   Container-Engine: rootless Docker (--engine=docker) -> Dockers apt-Repository $DOCKER_DOWNLOAD_URL/$DOCKER_REPO ($DOCKER_SUITE), sein Schlüssel nur mit dem Fingerabdruck $DOCKER_KEY_FINGERPRINT; apt-get install -y ${DOCKER_PACKAGES[*]}" ;;
        rpm-repo)
            docker_rpm_repo_check
            echo "   Container-Engine: rootless Docker (--engine=docker) -> Dockers dnf-Repository $DOCKER_DOWNLOAD_URL/$DOCKER_REPO${DOCKER_RPM_RELEASE:+ ($DOCKER_RPM_RELEASE, $(uname -m))}, sein Schlüssel nur mit dem Fingerabdruck $DOCKER_RPM_KEY_FINGERPRINT; dnf install -y ${DOCKER_PACKAGES[*]}"
            echo "     Müsste dnf dafür Pakete entfernen (etwa podman-docker), nennt der Installer sie genau und fragt dich." ;;
        *) echo "   Container-Engine: rootless Docker (--engine=docker) -> aus den Paketquellen der Distribution: ${PKG_ARGV[*]} ${DOCKER_PACKAGES[*]}" ;;
    esac
    echo "     der Docker-Dienst mit Root-Rechten bleibt aus (systemctl disable --now docker.service docker.socket)"
    plan_subids
    echo "     Dienst des Benutzers: dockerd-rootless-setuptool.sh install (als '$REAL_USER'; Socket unix://${USER_RUNTIME_ROOT}/${REAL_UID:-UID}/docker.sock)"
}

plan_engine() {
    if [ "$ROOTFUL" = "1" ]; then
        rootful_warning
        plan_docker
        return 0
    fi
    engine_user
    if [ -z "$REAL_USER" ] || [ "$REAL_USER" = "root" ]; then
        echo "   Container-Engine: ohne Root-Rechte für deinen Benutzer; als root ohne sudo richtet der Installer keine ein (sudo ./install.sh als dein Benutzer)"
        return 0
    fi
    if rootless_docker_there; then
        echo "   Container-Engine: rootless Docker von '$REAL_USER', vorhanden"
        plan_subids
    elif [ "$ENGINE_CHOICE" = "docker" ]; then
        if ! plan_rootless_docker; then
            echo "   Container-Engine: rootless Docker geht hier nicht: $ROOTLESS_DOCKER_WHY Stattdessen rootless Podman:"
            plan_rootless_podman || engine_stop "Für $(system_label) kennt der Installer keine Paketverwaltung für Podman, und rootless Docker geht hier nicht: $ROOTLESS_DOCKER_WHY"
        fi
    elif ! plan_rootless_podman; then
        echo "   Container-Engine: Podman geht hier nicht (für $(system_label) keine Paketverwaltung dafür); stattdessen rootless Docker:"
        plan_rootless_docker || engine_stop "Für $(system_label) kennt der Installer keine Paketverwaltung für Podman, und rootless Docker geht hier nicht: $ROOTLESS_DOCKER_WHY"
    fi
    if in_docker_group "$REAL_USER"; then
        echo "     '$REAL_USER' ist in der Gruppe docker (dauerhaft Rechte wie root): Der Installer sagt es und nimmt dich nicht selbst heraus (sudo gpasswd -d $REAL_USER docker)"
    fi
}

# --- SCHRITT 1: PREREQUISITES (Docker & Python) ---
setup_system() {
    log_info "[1/5] Systemprüfung & Vorbereitung..."

    wait_for_internet
    detect_system

    # Alpine (musl): nur Docker und die Werkzeuge für das Framework-Image, dann der Weg, der hier geht (alpine_stop).
    if [ "$OS_FAMILY" = "alpine" ]; then
        if [ "$CONTAINER_BUILD" != "1" ]; then
            setup_engine
            alpine_image_tools
        fi
        alpine_stop
    fi

    # requirements.txt only matches Python 3.11-3.14; outside that range pip would silently install nothing.
    ensure_python

    # In einem selbst gebauten Framework-Image gibt es keine Docker-Engine und keine docker-Gruppe.
    if [ "$CONTAINER_BUILD" = "1" ]; then
        log_info "Container-Build: Docker wird im Image nicht installiert."
        return 0
    fi

    setup_engine
}

# Die Container-Engine: standardmäßig ohne Root-Rechte (setup_rootless); Docker als root mit der Gruppe docker nur mit
# --rootful, nach der Warnung.
setup_engine() {
    if [ "$ROOTFUL" != "1" ]; then
        setup_rootless
        return 0
    fi
    rootful_warning
    if ! command -v docker &> /dev/null; then
        log_warn "Docker fehlt. Installiere es über die Paketverwaltung dieses Systems (Schlüssel und Signaturen geprüft)..."
        install_docker
    fi
    docker_group
}

# Ist der Befehl docker Podman (podman-docker: 'docker --version' meldet "podman version ...")?
docker_is_podman() {
    local version
    command -v docker &> /dev/null || return 1
    version="$(docker --version 2> /dev/null)" || return 1
    [[ "${version,,}" == podman* ]]
}

# Docker Permissions
# Wir nehmen den User, der sudo aufgerufen hat (SUDO_USER), nicht root!
docker_group() {
    REAL_USER="${SUDO_USER:-$USER}"
    # Gab es docker schon (ein zweiter Lauf nach podman-docker, oder podman-docker, das die Person selbst installiert
    # hat) und ist es Podman: derselbe Weg wie nach der Installation von podman-docker, ohne Gruppe docker.
    if [ -z "$DOCKER_ROUTE" ] && docker_is_podman; then
        DOCKER_ROUTE=podman
        # In der RHEL-Familie, wo Podman nur der Ersatzweg für Docker CE ist, sagt der Installer, wie man wechselt.
        if rhel_family; then
            log_info "Der Befehl docker ist hier Podman. Standard ist Docker CE: Das Framework braucht BuildKit (etwa für Hailo) und lehnt unter Podman ab, was das braucht. Wechseln: 'sudo dnf remove podman-docker', dann 'sudo ./install.sh' erneut (der Installer nennt, was dnf dafür sonst noch entfernen müsste, und fragt dich)."
        fi
    fi
    if [ "$DOCKER_ROUTE" = "podman" ]; then podman_for_user; return 0; fi
    # Ohne Nachfrage (siebte Runde, Punkt 6), aber mit klaren Worten, was die Gruppe bedeutet.
    if ! groups "$REAL_USER" | grep &>/dev/null "\bdocker\b"; then
        log_info "Füge User '$REAL_USER' zur Gruppe docker hinzu: Das Framework baut damit die Werkzeuge zum Cross-Kompilieren in Containern."
        log_warn "Was das heißt: Wer in der Gruppe docker ist, steuert den Docker-Dienst, und der läuft als root. Jedes Programm, das als '$REAL_USER' läuft, kann damit Root-Rechte auf diesem Rechner bekommen. Rückgängig machen: sudo gpasswd -d $REAL_USER docker (danach kann das Framework ohne sudo nicht mehr bauen)."
        # Alpine hat ohne das Paket shadow kein usermod; dort tut busybox' addgroup dasselbe.
        if command -v usermod &> /dev/null; then usermod -aG docker "$REAL_USER"; else addgroup "$REAL_USER" docker; fi
    fi
}

# Alpine und postmarketOS (musl): Die festgelegten Python-Pakete des Frameworks gibt es nicht für musl (onnxruntime,
# das die lokale Wissensbasis über fastembed braucht, veröffentlicht nur Builds für glibc, ohne Quellpaket), also kann
# das Framework hier nicht direkt laufen. Der Installer hat Docker eingerichtet und hält mit dem Weg an, der geht: das
# Framework-Image (Debian), auf diesem Rechner gebaut. Das Skript dafür braucht git und GNU tar (es stellt die Dateien
# mit 'tar --null --files-from' zusammen; busybox' tar kennt --null nicht), die Alpine nicht mitbringt: Der Installer
# richtet beide mit apk ein (alpine_image_tools). ALPINE_TOOLS sagt, wie das ausging (ok, failed; im Probelauf planned).
ALPINE_TOOLS=""
alpine_stop() {
    local group="" engine="Die Container-Engine ohne Root-Rechte ist eingerichtet"
    if [ "$ROOTFUL" = "1" ]; then group=" Für die Gruppe docker einmal ab- und wieder anmelden." engine="Docker ist eingerichtet"; fi
    local image_tools="Das Skript braucht dafür auch git und GNU tar (busybox' tar genügt nicht): sudo apk add git tar."
    case "$ALPINE_TOOLS" in
        ok) image_tools="git und GNU tar, die das Skript braucht (busybox' tar genügt nicht), sind installiert." ;;
        planned) image_tools="git und GNU tar, die das Skript braucht (busybox' tar genügt nicht), richtet der Installer vorher ein (apk add git tar)." ;;
    esac
    log_error "Das Framework selbst wurde auf $(system_label) nicht installiert: Seine festgelegten Python-Pakete gibt es nicht für musl (onnxruntime, das die lokale Wissensbasis braucht, nur für glibc). $engine; baue auf diesem Rechner das Framework-Image (Debian) und nutze das Framework darin: scripts/build_container_image.sh (Anleitung: docs/Container_Setup_DE.md). $image_tools$group"
}

# Fehlen git oder GNU tar (busybox' tar meldet sich nicht als "GNU tar"), die Pakete, die apk dafür hat: 'git tar'.
alpine_missing_tools() {
    local missing=()
    command -v git &> /dev/null || missing+=(git)
    tar --version 2> /dev/null | grep -q "GNU tar" || missing+=(tar)
    echo "${missing[*]}"
}

# git und GNU tar für scripts/build_container_image.sh, aus Alpines Paketquellen (apk prüft die signierten Indizes).
# Gelingt das nicht, sagt alpine_stop, was die Person selbst tun kann.
alpine_image_tools() {
    local missing
    missing="$(alpine_missing_tools)"
    if [ -z "$missing" ]; then ALPINE_TOOLS=ok; return 0; fi
    log_info "Für das Framework-Image (scripts/build_container_image.sh) fehlen: $missing. Installiere sie über apk..."
    # shellcheck disable=SC2086 # die Paketnamen sind einzelne Wörter
    if pkg_install $missing; then ALPINE_TOOLS=ok; else ALPINE_TOOLS=failed; fi
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
    
    # Wir nutzen rsync für sauberes Kopieren (exklusive Müll); fehlt es, über die Paketverwaltung dieses Systems.
    if ! command -v rsync &> /dev/null; then
        pkg_ready "rsync"
        if ! pkg_install rsync; then
            log_error "rsync ließ sich mit $(pkg_tool) nicht installieren (Meldungen oben); ohne rsync wird nichts kopiert.$(pkg_failure_hint)"
        fi
    fi

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
    
    # Mit grafischer Oberfläche requirements-gui.txt (Basis plus PySide6), mit --headless nur requirements.txt.
    REQUIREMENTS="requirements-gui.txt"
    if [ "$HEADLESS" = "1" ]; then REQUIREMENTS="requirements.txt"; fi
    # Das Python, das setup_system gefunden hat (auf RHEL 9 etwa python3.12), sonst python3.
    local python="${PYTHON:-python3}"

    # Scheitert ein Schritt (kein venv, ein Paket nicht erreichbar), bricht die Installation ab: Sonst meldete sie
    # "abgeschlossen" mit einer leeren Umgebung, und ein Container-Image würde trotzdem gebaut.
    if ! "${as_user[@]}" bash <<EOF
    set -e
    cd "$INSTALL_DIR"
    "$python" -m venv .venv
    # Das pip der Umgebung ist das, was mit Python kommt (ensurepip). Es wird nicht aktualisiert: Das lüde die neueste
    # Version von pip ohne Prüfsumme, und das mitgelieferte genügt für die Installation mit --require-hashes unten.

    # Genau die Versionen aus $REQUIREMENTS dieses Stands (uv export aus uv.lock); pip prüft jede Datei gegen
    # ihren Hash. Ohne die Datei wird nichts ungeprüft nachinstalliert.
    echo "Installiere Abhängigkeiten ($REQUIREMENTS)..."
    if [ ! -f "$REQUIREMENTS" ]; then
        echo "$REQUIREMENTS fehlt in $INSTALL_DIR; ohne die festgelegten Versionen wird nichts installiert." >&2
        exit 1
    fi
    # --no-deps: $REQUIREMENTS is the complete export of uv.lock (every package, every hash), so pip installs exactly
    # that set and does not resolve again (pyproject.toml [tool.uv] overrides one upstream cap: Pillow).
    ./.venv/bin/pip install --require-hashes --no-deps -r "$REQUIREMENTS" > /dev/null
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
            --headless) HEADLESS=1 ;;
            --dry-run) DRY_RUN=1 ;;
            --rootful) ROOTFUL=1 ;;
            --engine=*) ENGINE_CHOICE="${arg#--engine=}" ;;
            --mode=*) MODE_CHOICE="${arg#--mode=}" ;;
        esac
    done
    case "$MODE_CHOICE" in
        ""|private|enterprise) ;;
        *) log_error "Unbekannter Wert für --mode: '$MODE_CHOICE' (private oder enterprise)." ;;
    esac
    case "$ENGINE_CHOICE" in
        podman|docker) ;;
        *) log_error "Unbekannter Wert für --engine: '$ENGINE_CHOICE' (podman oder docker; beide ohne Root-Rechte, Docker als root nur mit --rootful)." ;;
    esac
    case "$DITTO_CHOICE" in
        auto|bare-metal|docker|skip) ;;
        *) log_error "Unbekannter Wert für --ditto: '$DITTO_CHOICE' (auto, bare-metal, docker oder skip)." ;;
    esac
}

# Fragt den Framework-Modus, wenn weder --mode noch LCF_MODE ihn nennen: am Terminal (ASK_TTY, nie aus stdin), Enter
# heißt privat. Ohne Terminal (ein unbeaufsichtigter Lauf) bleibt der Modus, wie er ist (MODE_CHOICE leer).
ask_mode() {
    local answer=""
    if [ -n "$MODE_CHOICE" ]; then return 0; fi
    printf '%s [P/e] ' "Framework-Modus: privat (Standard) oder enterprise? Enterprise nimmt auf Boards nur OpenSSH mit sudo (die Schlüsselzeile in Root-Besitz) und lehnt die schwächere Dropbear-Stufe und das Home-Tor ab."
    if ! { IFS= read -r answer < "$ASK_TTY"; } 2> /dev/null; then
        echo ""
        log_info "Kein Terminal für die Antwort (ein unbeaufsichtigter Lauf): Der Framework-Modus bleibt, wie er ist (neu: privat; --mode=enterprise wählt ihn)."
        return 0
    fi
    case "${answer,,}" in e|enterprise) MODE_CHOICE="enterprise" ;; *) MODE_CHOICE="private" ;; esac
}

# Setzt den gewählten Framework-Modus mit 'llm-cli config set framework_mode' als die Person, die installiert (ihre
# config.yml; in einem Container-Build die des Images). Ohne Wahl tut der Schritt nichts. Er lässt die Installation nie
# scheitern: Ein Fehler wird gemeldet (später: llm-cli config set framework_mode WERT oder Datei > Einstellungen...).
setup_mode() {
    if [ -z "$MODE_CHOICE" ]; then return 0; fi
    log_info "[Modus] Framework-Modus: $MODE_CHOICE..."
    local run=()
    if [ "$CONTAINER_BUILD" != "1" ]; then
        local real_user="${SUDO_USER:-${USER:-}}"
        if [ "$real_user" = "root" ] || [ -z "$real_user" ]; then
            log_warn "Der Framework-Modus wird für deinen Benutzer gesetzt, nicht für root: Später als dein Benutzer: llm-cli config set framework_mode $MODE_CHOICE"
            return 0
        fi
        run=(sudo -H -u "$real_user")
        if ! command -v sudo &> /dev/null; then run=(runuser -u "$real_user" --); fi
    fi
    if (cd "$INSTALL_DIR" && "${run[@]}" "$INSTALL_DIR/.venv/bin/python" -m orchestrator.cli config set framework_mode "$MODE_CHOICE"); then
        log_success "Framework-Modus: $MODE_CHOICE."
    else
        log_warn "Der Framework-Modus ließ sich nicht setzen (Meldungen oben); die Installation geht weiter. Später: llm-cli config set framework_mode $MODE_CHOICE"
    fi
    return 0
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
    # Ein Argument mit Kommas (die Liste für --preserve-env), keine Feldelemente.
    # shellcheck disable=SC2054
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
    if [ "$HEADLESS" = "1" ]; then
        echo "🚀 Start:  llm-cli bzw. llmcf (ohne grafische Oberfläche installiert, --headless)"
    else
        echo "🚀 Start:  Tippe einfach 'llm-framework'"
    fi
    case "$ROOTLESS_ENGINE" in
        podman) echo "🐳 Engine: rootless Podman (mit deinen Rechten, ohne Gruppe docker)" ;;
        docker) echo "🐳 Engine: rootless Docker (mit deinen Rechten, ohne Gruppe docker)" ;;
    esac
    echo "-----------------------------------------------------"
    old_installation_note
    
    # Hinweis zum Docker Group Refresh
    REAL_USER="${SUDO_USER:-$USER}"
    if [ "$CONTAINER_BUILD" != "1" ] && [ "$ROOTFUL" = "1" ] && [ "$DOCKER_ROUTE" != "podman" ] \
        && ! groups "$REAL_USER" | grep &>/dev/null "\bdocker\b"; then
        log_warn "Du wurdest zur Docker-Gruppe hinzugefügt."
        log_warn "Bitte einmal aus- und einloggen (oder 'newgrp docker' nutzen)."
    fi
}

# --- PROBELAUF (--dry-run) ---
# Zeigt, was setup_system und deploy_files hier täten, mit denselben Entscheidungen (detect_system, find_python,
# python_packages, docker_plan, pkg_argv), ohne etwas zu verändern, zu laden oder eine Paketverwaltung aufzurufen.
plan_python() {
    local sets=() rest=""
    pkg_argv
    if find_python; then
        echo "   Python: $PYTHON ($("$PYTHON" --version 2>&1))"
        return 0
    fi
    mapfile -t sets < <(python_versioned_packages)
    if [ "${#sets[@]}" -gt 1 ]; then rest=" (sonst der Reihe nach: $(printf '%s; ' "${sets[@]:1}" | sed 's/; $//'))"; fi
    if ! command -v python3 &> /dev/null; then
        pkg_ready "Python 3.11 bis 3.14"
        echo "   Python: fehlt -> ${PKG_ARGV[*]} $(python_packages)"
        if [ "${#sets[@]}" -gt 0 ]; then
            echo "     passt es nicht (3.11 bis 3.14): ${PKG_ARGV[*]} ${sets[0]}$rest"
        fi
    elif [ "${#sets[@]}" -gt 0 ]; then
        pkg_ready "Python 3.11 bis 3.14"
        echo "   Python: $(python3 --version 2>&1) passt nicht (3.11 bis 3.14) -> ${PKG_ARGV[*]} ${sets[0]}$rest"
    else
        python_missing_error
    fi
    if sles_system; then
        echo "     unter SLES liegen diese Pakete in SUSEs Python 3 Module: Gibt es keins davon, fragt der Installer, ob er das Modul einschaltet (SUSEConnect -p $(sles_python_module_product); nur auf einem registrierten System)"
    fi
}

plan_docker() {
    local service="" optional=""
    if command -v docker &> /dev/null; then
        echo "   Docker: vorhanden ($(command -v docker))"
        return 0
    fi
    docker_plan
    pkg_argv
    # Wie install_docker_apt, install_docker_rpm und install_docker_distro: ohne die Paketverwaltung hält es hier an.
    if ! command -v "$(pkg_tool)" &> /dev/null; then docker_stop "$(pkg_tool) fehlt auf diesem System."; fi
    if ! pacman_database_ready; then docker_stop "$(pacman_database_text)"; fi
    if [ -n "$DOCKER_SERVICE" ]; then
        if [ "$PKG" = "apk" ]; then service="; rc-update add docker default; rc-service docker start"
        else service="; systemctl enable --now $DOCKER_SERVICE"; fi
    fi
    if [ "${#DOCKER_OPTIONAL[@]}" -gt 0 ]; then optional=" (dazu, wenn es das gibt: ${DOCKER_OPTIONAL[*]})"; fi
    case "$DOCKER_ROUTE" in
        apt-repo)
            echo "   Docker: fehlt -> Dockers apt-Repository $DOCKER_DOWNLOAD_URL/$DOCKER_REPO ($DOCKER_SUITE), sein Schlüssel nur mit dem Fingerabdruck $DOCKER_KEY_FINGERPRINT; apt-get install -y ${DOCKER_PACKAGES[*]}" ;;
        rpm-repo)
            docker_rpm_repo_check
            echo "   Docker: fehlt -> Dockers dnf-Repository $DOCKER_DOWNLOAD_URL/$DOCKER_REPO${DOCKER_RPM_RELEASE:+ ($DOCKER_RPM_RELEASE, $(uname -m))}, sein Schlüssel nur mit dem Fingerabdruck $DOCKER_RPM_KEY_FINGERPRINT; dnf install -y ${DOCKER_PACKAGES[*]}$service"
            if [ "$DOCKER_FALLBACK" = "podman" ]; then
                echo "     Docker CE ist hier der Standard: Das Framework braucht BuildKit (etwa für Hailo)."
                echo "     Müsste dnf dafür Pakete entfernen (etwa podman-docker oder runc), nennt der Installer sie genau und fragt dich. Bei Nein, ohne Paket für dieses System, ohne Verbindung zum Repository oder mit einem falschen Schlüssel kommt der Ersatzweg: ${PKG_ARGV[*]} podman-docker (Podman mit dem Befehl docker); systemctl --user enable --now podman.socket"
            fi ;;
        podman)
            echo "   Docker: fehlt -> Podman (der Ersatzweg), denn Docker CE geht hier nicht: $DOCKER_PODMAN_WHY Stattdessen: ${PKG_ARGV[*]} ${DOCKER_PACKAGES[*]} (Podman mit dem Befehl docker); Podmans Docker-kompatible Schnittstelle für deinen Benutzer (systemctl --user enable --now podman.socket)" ;;
        *)
            echo "   Docker: fehlt -> aus den Paketquellen der Distribution: ${PKG_ARGV[*]} ${DOCKER_PACKAGES[*]}$optional$service"
            if [ "$PKG" = "apk" ]; then echo "     aus Alpines Repository community (wird in $APK_REPOSITORIES eingeschaltet, falls nötig)"; fi
            if sles_system; then
                echo "     unter SLES liegt Docker in SUSEs Containers Module: Gibt es das Paket nicht, fragt der Installer, ob er das Modul einschaltet (SUSEConnect -p $(sles_containers_module_product); nur auf einem registrierten System)"
            fi ;;
    esac
}

plan_installation() {
    local requirements="requirements-gui.txt"
    if [ "$HEADLESS" = "1" ]; then requirements="requirements.txt"; fi
    log_info "Probelauf (--dry-run): Es wird nichts verändert, nichts geladen und keine Paketverwaltung aufgerufen; so liefe die Installation hier ab."
    detect_system
    echo "   System: $(system_label); Paketverwaltung: ${PKG:-keine, die der Installer kennt}"
    if [ "$OS_FAMILY" = "alpine" ]; then
        if [ "$CONTAINER_BUILD" != "1" ]; then
            plan_engine
            if [ -n "$(alpine_missing_tools)" ]; then
                pkg_argv
                echo "   git, GNU tar (für scripts/build_container_image.sh): fehlen -> ${PKG_ARGV[*]} $(alpine_missing_tools)"
                ALPINE_TOOLS=planned
            else
                echo "   git, GNU tar (für scripts/build_container_image.sh): vorhanden"
                ALPINE_TOOLS=ok
            fi
        fi
        alpine_stop
    fi
    plan_python
    if [ "$CONTAINER_BUILD" = "1" ]; then
        echo "   Docker: im Container-Build nicht"
    else
        plan_engine
    fi
    if command -v rsync &> /dev/null; then
        echo "   rsync: vorhanden"
    else
        pkg_ready "rsync"
        pkg_argv
        echo "   rsync: fehlt -> ${PKG_ARGV[*]} rsync"
    fi
    echo "   Danach: Programmdateien nach $INSTALL_DIR, .venv mit $requirements (${PYTHON:-dem eben installierten Python}), Starter /usr/local/bin/llm-framework, Ditto ($DITTO_CHOICE)"
    echo "   Framework-Modus: ${MODE_CHOICE:-wird gefragt (ohne Terminal: bleibt, wie er ist; neu: privat)}"
    log_success "Probelauf fertig: Die Installation liefe hier durch."
}

# --- MAIN ---
main() {
    ditto_option "$@"
    if [ "$DRY_RUN" = "1" ]; then plan_installation; return 0; fi
    check_root
    ask_mode
    setup_system
    deploy_files
    adopt_old_settings
    setup_venv
    configure
    setup_mode
    setup_ditto
    finish
}

# Tests laden das Skript mit 'source', um einzelne Funktionen zu prüfen; nur ein direkter Aufruf installiert.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
