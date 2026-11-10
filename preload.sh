#!/bin/bash
# preload.sh - Das Preload des Installers: auf einem Rechner MIT Netz ausgeführt, legt es alles, was install.sh für
# einen Rechner OHNE Netz aus dem Netz holen würde, in ein Archiv (.tar.gz), das die Person hinüberträgt (ein
# USB-Stick). Dort: sudo ./install.sh --offline --preload DATEI (aus demselben Checkout). Ohne --preload schreibt
# install.sh --offline selbst ein preload.sh und preload.ps1 für genau diesen Rechner (die auch ohne Checkout laufen)
# und wartet auf den Pfad des Archivs; dieses Skript macht dasselbe Archiv vorab, aus dem Checkout.
#
#   ./preload.sh [--target linux-x86_64|linux-aarch64|windows-amd64|windows-arm64] [--python X.Y[.Z]]
#                [--glibc 2.NN] [--headless] [--os-release DATEI] [--out ORDNER]
#
# Ohne Angaben ist das Ziel dieser Rechner (Plattform, Python, glibc). Für einen anderen Rechner: --target, --python
# (das Python, mit dem install.sh dort die .venv baut) und unter Linux --glibc (getconf GNU_LIBC_VERSION dort);
# --os-release mit einer Kopie von /etc/os-release des Zielrechners nennt die Systempakete seiner Paketverwaltung.
# Für install.bat dasselbe mit preload.ps1 (oder hier mit --target windows-amd64). Packen und Laden übernimmt
# scripts/preload_lock.py (spec, pack) mit dem pip dieses Rechners.
#
# Das Archiv llmcf-preload-VERSION-ZIEL-pyX.Y.tar.gz enthält einen Ordner llmcf-preload/ mit:
#   manifest.txt         Version, Ziel, Python, Plattform-Tags, ABI und das sha256 der Lock-Datei
#   wheels/              jedes Paket aus requirements-gui.txt (mit --headless: requirements.txt) als Wheel für das
#                        Ziel, mit pip download --only-binary :all: --no-deps --require-hashes gegen die Hashes des
#                        Locks geholt; die Marker wertet scripts/preload_lock.py für das Ziel aus
#   tools/               uv, festgelegt in requirements-resolve.txt, mit seinem Hash
#   system-packages.txt  was der Installer mit der Paketverwaltung des Ziels (oder winget) installieren würde
#   SHA256SUMS           das sha256 jeder Datei
# Keine Signatur: Der Rechner ohne Netz prüft jedes Wheel gegen die Hashes des Locks in seinem Checkout (pip
# --require-hashes), derselbe Anker wie mit Netz. Das sha256 des Archivs steht am Ende; install.sh gibt es ebenfalls
# aus und prüft es mit --preload-sha256 HEX.
# Exit-Codes: 0 geschrieben, 1 gescheitert (der Grund steht dabei), 2 falscher Aufruf.

if [ -z "${BASH_VERSION:-}" ]; then
    echo "preload.sh braucht bash: ./preload.sh (oder bash preload.sh)" >&2
    exit 2
fi
set -u

PRELOAD_ROOT="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
# Die Funktionen von install.sh (Erkennung des Systems, die Paketnamen je Paketverwaltung); 'source' installiert nichts.
# shellcheck source=install.sh
source "$PRELOAD_ROOT/install.sh"

PRELOAD_SPEC="" TARGET="" TARGET_PYTHON="" TARGET_GLIBC="" PRELOAD_HEADLESS=0 OUT_DIR="$PWD" TARGET_OS_RELEASE=""
# Das Python, mit dem pip die Wheels holt (jedes Python 3 mit pip; nicht das des Ziels).
HOST_PYTHON="${PRELOAD_PYTHON:-python3}"

fail() { echo "preload: $*" >&2; exit 1; }
usage_error() { echo "preload: $*" >&2; echo "Aufruf: ./preload.sh [--target ZIEL] [--python X.Y] [--glibc 2.NN] [--headless] [--os-release DATEI] [--out ORDNER]" >&2; exit 2; }

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --target) [ $# -ge 2 ] || usage_error "--target braucht einen Wert"; TARGET="$2"; shift ;;
            --target=*) TARGET="${1#--target=}" ;;
            --python) [ $# -ge 2 ] || usage_error "--python braucht einen Wert"; TARGET_PYTHON="$2"; shift ;;
            --python=*) TARGET_PYTHON="${1#--python=}" ;;
            --glibc) [ $# -ge 2 ] || usage_error "--glibc braucht einen Wert"; TARGET_GLIBC="$2"; shift ;;
            --glibc=*) TARGET_GLIBC="${1#--glibc=}" ;;
            --os-release) [ $# -ge 2 ] || usage_error "--os-release braucht eine Datei"; TARGET_OS_RELEASE="$2"; shift ;;
            --os-release=*) TARGET_OS_RELEASE="${1#--os-release=}" ;;
            --out) [ $# -ge 2 ] || usage_error "--out braucht einen Ordner"; OUT_DIR="$2"; shift ;;
            --out=*) OUT_DIR="${1#--out=}" ;;
            --headless) PRELOAD_HEADLESS=1 ;;
            -h|--help) sed -n '2,27p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
            *) usage_error "unbekannte Angabe: $1" ;;
        esac
        shift
    done
}

lock_tool() { "$HOST_PYTHON" -I "$PRELOAD_ROOT/scripts/preload_lock.py" "$@"; }

# Ziel, Python und glibc: was angegeben ist, sonst dieser Rechner.
choose_target() {
    local here=() same=0
    command -v "$HOST_PYTHON" &> /dev/null || fail "$HOST_PYTHON fehlt; preload.sh braucht ein Python 3 mit pip (PRELOAD_PYTHON nennt ein anderes)."
    "$HOST_PYTHON" -m pip --version &> /dev/null || fail "$HOST_PYTHON hat kein pip (Debian, Ubuntu: sudo apt install python3-pip)."
    mapfile -t here < <(lock_tool this-computer)
    if [ -z "$TARGET" ]; then TARGET="${here[0]:-}"; same=1; fi
    if [ "$TARGET" = "${here[0]:-}" ]; then same=1; fi
    if [ -z "$TARGET_PYTHON" ]; then
        # Dasselbe Python, das install.sh hier nähme (find_python), sonst das von preload.sh selbst.
        if [ "$same" = "1" ] && find_python; then
            TARGET_PYTHON="$("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
        else
            usage_error "Für ein anderes Ziel als diesen Rechner (oder ohne Python 3.11 bis 3.14 hier) nenne das Python des Zielrechners: --python X.Y"
        fi
    fi
    case "$TARGET" in
        linux-*)
            if [ -z "$TARGET_GLIBC" ]; then
                if [ "$same" = "1" ] && [ -n "${here[2]:-}" ]; then TARGET_GLIBC="${here[2]}"; else TARGET_GLIBC="2.28"; fi
            fi ;;
        windows-*) TARGET_GLIBC="" ;;
    esac
}

main() {
    local spec=""
    parse_args "$@"
    choose_target
    if [ -n "$TARGET_OS_RELEASE" ]; then
        [ -r "$TARGET_OS_RELEASE" ] || fail "$TARGET_OS_RELEASE lässt sich nicht lesen."
        # shellcheck disable=SC2034 # read by detect_system (install.sh)
        OS_RELEASE_FILE="$TARGET_OS_RELEASE"
    fi
    # shellcheck disable=SC2034 # read by preload_spec (install.sh)
    HEADLESS="$PRELOAD_HEADLESS"
    spec="$(mktemp -d)" || fail "Kein temporärer Ordner."
    PRELOAD_SPEC="$spec"
    trap 'rm -rf "$PRELOAD_SPEC"' EXIT
    preload_spec "$spec" "$TARGET" "$TARGET_PYTHON" "${TARGET_GLIBC:-}" "$HOST_PYTHON" > /dev/null \
        || usage_error "Ziel $TARGET, Python $TARGET_PYTHON${TARGET_GLIBC:+, glibc $TARGET_GLIBC}: so nicht möglich (oben)."
    "$HOST_PYTHON" -I "$PRELOAD_ROOT/scripts/preload_lock.py" pack --spec "$spec" --out "$OUT_DIR" \
        || fail "Das Preload ließ sich nicht schreiben (oben)."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
