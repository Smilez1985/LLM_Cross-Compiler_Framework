#!/bin/bash
# scripts/ci_image_audit.sh
#
# DIREKTIVE: Goldstandard, Container-Native, Dependency-Free (Host).
# ZWECK: Automatisiertes Audit von Docker-Images auf Größe und Layer-Effizienz.
#        Nutzt 'dive' im Container, vermeidet Abhängigkeiten wie 'jq' oder 'bc' auf dem Host.
#
# VERWENDUNG: ./ci_image_audit.sh <image_tag> [min_efficiency_percent] [max_wasted_mb]

set -euo pipefail

# --- CONFIGURATION ---
IMAGE_NAME="${1:-}"
MIN_EFFICIENCY="${2:-90}" # Standard: 90% Effizienz gefordert
MAX_WASTED_MB="${3:-100}" # Warnung ab 100MB Verschwendung

# Temp File Setup (Auto-Cleanup)
TMP_JSON=$(mktemp)
cleanup() {
    rm -f "$TMP_JSON"
}
trap cleanup EXIT

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- LOGGING ---
log_info()    { echo -e "${BLUE}ℹ️  [AUDIT] $1${NC}"; }
log_success() { echo -e "${GREEN}✅ [AUDIT] $1${NC}"; }
log_warn()    { echo -e "${YELLOW}⚠️  [AUDIT] $1${NC}"; }
log_error()   { echo -e "${RED}❌ [AUDIT] $1${NC}" >&2; }

# --- PREREQUISITES ---
if [ -z "$IMAGE_NAME" ]; then
    log_error "Kein Image-Name angegeben."
    echo "Usage: $0 <image_name> [min_efficiency] [max_wasted_mb]"
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    log_error "Docker CLI nicht gefunden."
    exit 1
fi

if ! docker info > /dev/null 2>&1; then
    log_error "Docker Daemon läuft nicht oder Zugriff verweigert."
    exit 1
fi

# --- MAIN EXECUTION ---
main() {
    log_info "Starte Audit für Image: $IMAGE_NAME"
    log_info "Ziele: Effizienz > ${MIN_EFFICIENCY}% | Verschwendung < ${MAX_WASTED_MB} MB"

    # 1. Dive Analyse via Docker Container ausführen
    # Wir mounten den Docker Socket (ro), damit Dive das Image analysieren kann.
    log_info "Führe 'dive' Analyse aus..."
    
    set +e # Fehler temporär erlauben, um Exit-Code zu fangen
    docker run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        wagoodman/dive:latest \
        "$IMAGE_NAME" --ci --json > "$TMP_JSON" 2>/dev/null
    
    DIVE_EXIT_CODE=$?
    set -e

    # Prüfen, ob die JSON-Datei Inhalt hat (Dive Exit Code ist oft unzuverlässig bei CI Flags)
    if [ ! -s "$TMP_JSON" ] || [ $DIVE_EXIT_CODE -ne 0 ]; then
        log_error "Dive Analyse fehlgeschlagen oder Image '$IMAGE_NAME' nicht gefunden."
        log_info "Debug: Prüfen Sie, ob das Image lokal vorhanden ist ('docker images')."
        exit 1
    fi

    # 2. Metriken Parsen & Berechnen (via Python)
    # Wir nutzen Python für Parsing UND Mathematik, um 'jq' und 'bc' zu vermeiden.
    log_info "Werte Ergebnisse aus..."

    # Python liest JSON File und gibt formatierte Strings zurück: "SIZE_MB WASTED_MB EFFICIENCY_PCT"
    read -r image_size_mb wasted_mb efficiency_percent <<< $(python3 -c "
import sys, json

try:
    with open('$TMP_JSON', 'r') as f:
        data = json.load(f)
        
    stats = data.get('image', {})
    
    # Bytes to MB
    size_mb = stats.get('totalImageSize', 0) / 1024 / 1024
    wasted_mb = stats.get('wastedBytes', 0) / 1024 / 1024
    
    # Efficiency (0.95 -> 95.0)
    eff_raw = stats.get('efficiency', 0)
    eff_pct = eff_raw * 100
    
    # Output simple space-separated values for bash
    print(f'{size_mb:.2f} {wasted_mb:.2f} {eff_pct:.2f}')
    
except Exception as e:
    # Fail-safe output
    print('0 0 0')
    sys.exit(1)
")

    # 3. Report Ausgabe
    echo ""
    echo "--------------------------------------------------------"
    echo "📊 AUDIT REPORT: $IMAGE_NAME"
    echo "--------------------------------------------------------"
    echo -e "Gesamtgröße:      ${BLUE}${image_size_mb} MB${NC}"
    echo -e "Verschwendet:     ${YELLOW}${wasted_mb} MB${NC}"
    echo -e "Effizienz-Score:  ${BLUE}${efficiency_percent}%${NC}"
    echo "--------------------------------------------------------"

    # 4. Validierung gegen Grenzwerte (Bash Float Comparison via Python logic replacement or simple Awk)
    # Da wir Strings wie "95.50" haben, nutzen wir awk für den Vergleich, das ist Standard auf fast jedem Linux.
    local failed=0

    # Check Effizienz
    if awk "BEGIN {exit !($efficiency_percent < $MIN_EFFICIENCY)}"; then
        log_error "Effizienz unter Grenzwert! ($efficiency_percent% < $MIN_EFFICIENCY%)"
        failed=1
    else
        log_success "Effizienz OK."
    fi

    # Check Verschwendung
    if awk "BEGIN {exit !($wasted_mb > $MAX_WASTED_MB)}"; then
        log_warn "Verschwendeter Speicher hoch ($wasted_mb MB > $MAX_WASTED_MB MB)."
        log_info "Tipp: Layer bereinigen (apt-get clean, temporäre Dateien löschen)."
        # Warnung lässt Build meist nicht fehlschlagen, außer strict mode gewünscht.
    else
        log_success "Verschwendung im Rahmen."
    fi

    if [ $failed -eq 1 ]; then
        echo ""
        log_error "AUDIT FEHLGESCHLAGEN. Image entspricht nicht den Qualitätsstandards."
        exit 1
    fi

    echo ""
    log_success "Audit erfolgreich bestanden."
    exit 0
}

main
