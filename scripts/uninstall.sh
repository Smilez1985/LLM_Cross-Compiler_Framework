#!/usr/bin/env bash
# uninstall.sh - LLM Cross-Compiler Framework: entfernt, was der Installer selbst installiert hat (Linux).
#
# Das Gegenstück zu scripts/Uninstall-LLM-Cross-Compiler-Framework.bat für die Teile, die der Installer aufgeschrieben hat.
# install.sh schreibt in $INSTALL_DIR/install-manifest.json auf, welche Pakete es installiert hat, weil sie vorher nicht da
# waren (apt, dnf, zypper, pacman, apk); das Framework schreibt in ~/.llm-framework/install-manifest.json auf, was es für die
# Person eingerichtet hat (die Suchmaschine für Dittos Websuche). Dieses Skript zeigt diese Einträge und fragt:
#   - was nur das Framework nutzt (die Suchmaschine im eigenen Ordner): einmal für alles,
#   - jedes Paket einzeln, mit einer Warnung und Nein als Vorgabe: Andere Programme können es benutzen.
# Was nicht aufgeschrieben ist, war vorher da und wird nie entfernt (ein Python, das schon da war). Ein Paket entfernt das
# Skript nur, wenn kein anderes Paket es braucht (dpkg -r, rpm -e, pacman -R lehnen sonst ab; es entfernt nie mit, was davon
# abhängt). Pakete, die nur als Abhängigkeit mitkamen, bleiben; 'sudo apt autoremove' o. ä. entscheidet die Person selbst und
# zeigt vorher, was es entfernen würde.
#
# Nicht Teil dieses Skripts (steht in der ROADMAP): der Installationsordner, der Starter /usr/local/bin/llm-framework, die
# Gruppe llmcf, der Eintrag im Anwendungsmenü und der persönliche Ordner ~/.llm-framework. Das Skript nennt sie am Ende.
#
# Aufruf: sudo ./scripts/uninstall.sh [--dry-run]     (--dry-run zeigt nur, wonach gefragt würde; ohne Root-Rechte möglich)

set -u

INSTALL_DIR="/opt/llm-cross-compiler-framework"
REPO_ROOT="$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")"
MODULE="orchestrator.Core.uninstall_components"
DRY_RUN=0

is_root() { [ "$(id -u)" = "0" ]; }

# Das Python und der Ordner mit dem Code, mit dem die Fragen laufen: das Python der Installation und ihr Code, sonst das
# System-Python und der Quellordner (der Code dafür braucht nur die Standardbibliothek).
find_tools() {
    PYTHON_BIN=""
    CODE_DIR=""
    if [ -f "$INSTALL_DIR/orchestrator/Core/uninstall_components.py" ]; then
        CODE_DIR="$INSTALL_DIR"
    elif [ -f "$REPO_ROOT/orchestrator/Core/uninstall_components.py" ]; then
        CODE_DIR="$REPO_ROOT"
    fi
    if [ -x "$INSTALL_DIR/.venv/bin/python" ]; then
        PYTHON_BIN="$INSTALL_DIR/.venv/bin/python"
    else
        PYTHON_BIN="$(command -v python3 2> /dev/null || true)"
    fi
    [ -n "$PYTHON_BIN" ] && [ -n "$CODE_DIR" ]
}

# Die Person, für die gefragt wird: wer sudo aufgerufen hat. Ihr Ordner (~/.llm-framework) kommt aus der Benutzerdatenbank,
# nie aus $HOME (das ist unter sudo oft das von root).
person_manifest() {
    local user home
    user="${SUDO_USER:-}"
    [ -n "$user" ] && [ "$user" != "root" ] || return 1
    home="$(getent passwd "$user" 2> /dev/null | cut -d: -f6)"
    [ -n "$home" ] || return 1
    echo "$home/.llm-framework/install-manifest.json"
}

closing_note() {
    echo
    echo "Nicht entfernt, weil das nicht Teil dieses Skripts ist: der Installationsordner $INSTALL_DIR, der Starter"
    echo "/usr/local/bin/llm-framework, die Gruppe llmcf, der Eintrag im Anwendungsmenü und dein Ordner ~/.llm-framework"
    echo "(Einstellungen, Zugangsdaten, Builds)."
    echo "Pakete, die nur als Abhängigkeit mitkamen, bleiben. Was die Paketverwaltung davon nicht mehr braucht, zeigt sie"
    echo "selbst, bevor sie es entfernt (zum Beispiel: sudo apt autoremove)."
    echo "Hat eine andere Person dieser Installation sich eine Suchmaschine eingerichtet, entfernt sie die selbst"
    echo "(llm-cli ditto web setup --remove)."
}

main() {
    local arg machine person args=()
    for arg in "$@"; do
        case "$arg" in
            --dry-run) DRY_RUN=1 ;;
            -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
            *) echo "Unbekannte Option: $arg (erlaubt: --dry-run)"; return 2 ;;
        esac
    done
    if [ "$DRY_RUN" != "1" ] && ! is_root; then
        echo "Dieses Skript entfernt Pakete und liest das Manifest des Installers: Starte es mit sudo (sudo ./scripts/uninstall.sh)."
        echo "Ohne Root-Rechte zeigt '--dry-run', wonach gefragt würde."
        return 1
    fi
    if ! find_tools; then
        echo "Es gibt hier weder eine Installation unter $INSTALL_DIR noch den Quellordner mit dem Uninstaller-Code und ein"
        echo "Python (python3); es scheint nichts installiert zu sein."
        return 1
    fi
    machine="$INSTALL_DIR/install-manifest.json"
    args=(uninstall --machine "$machine")
    if person="$(person_manifest)"; then
        args+=(--person "$person")
    else
        echo "Hinweis: Ohne sudo als eine Person (oder als root angemeldet) wird nur das Manifest der Installation gelesen, nicht das persönliche."
    fi
    if [ "$DRY_RUN" = "1" ]; then args+=(--dry-run); fi
    PYTHONPATH="$CODE_DIR" "$PYTHON_BIN" -m "$MODULE" "${args[@]}"
    local rc=$?
    # Der Schlusshinweis nur nach einem Lauf, der durchging: Nach einem Fehler steht dessen Meldung zuletzt da.
    if [ "$rc" -eq 0 ]; then closing_note; fi
    return "$rc"
}

# Tests laden das Skript mit 'source', um einzelne Funktionen zu prüfen; nur ein direkter Aufruf entfernt.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
