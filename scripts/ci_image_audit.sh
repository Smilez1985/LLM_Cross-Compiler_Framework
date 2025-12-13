#!/bin/bash
# ci_image_audit.sh
#
# DIREKTIVE: Goldstandard, Container-Native.
# ZWECK: Automatisiertes Audit von Docker-Images auf Größe und Layer-Effizienz.
#        Nutzt 'dive' via Docker, um Host-Abhängigkeiten zu vermeiden.
#
# VERWENDUNG: ./ci_image_audit.sh <image_tag> [min_efficiency_percent]

set -e

# --- CONFIGURATION ---
IMAGE_NAME="$1"
MIN_EFFICIENCY="${2:-90}" # Standard: 90% Effizienz gefordert
MAX_WASTED_MB=100         # Warnung ab 100MB Verschwendung

# Farben für Output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- LOGGING ---
log_info() { echo -e "${BLUE}ℹ️  [AUDIT] $1${NC}"; }
log_success() { echo -e "${GREEN}✅ [AUDIT] $1${NC}"; }
log_warn() { echo -e "${YELLOW}⚠️  [AUDIT] $1${NC}"; }
log_error() { echo -e "${RED}❌ [AUDIT] $1${NC}" >&2; }

# --- PREREQUISITES ---
if [ -z "$IMAGE_NAME" ]; then
    log_error "Kein Image-Name angegeben."
    echo "Usage: $0 <image_name>"
    exit 1
fi

# Prüfe ob Docker läuft
if ! docker info > /dev/null 2>&1; then
    log_error "Docker Daemon läuft nicht."
    exit 1
fi

# --- MAIN EXECUTION ---
main() {
    log_info "Starte Audit für Image: $IMAGE_NAME"
    log_info "Ziel-Effizienz: >${MIN_EFFICIENCY}%"

    # 1. Dive Analyse via Docker Container ausführen
    # Wir mounten den Docker Socket, damit Dive das Image analysieren kann
    # Wir nutzen --ci --json für maschinenlesbaren Output
    
    log_info "Führe 'dive' Analyse aus (dies kann einen Moment dauern)..."
    
    # Temporäre Datei für JSON Output
    local json_output
    
    # Der Befehl führt dive in einem Container aus und analysiert das Image auf dem Host
    if ! json_output=$(docker run --rm -v /var/run/docker.sock:/var/run/docker.sock wagoodman/dive:latest "$IMAGE_NAME" --ci --json); then
        log_error "Dive Analyse fehlgeschlagen. Existiert das Image '$IMAGE_NAME'?"
        exit 1
    fi

    # 2. Metriken Parsen (via Python, um jq-Abhängigkeit zu vermeiden)
    # Wir extrahieren: efficiency, wastedBytes, imageSize
    
    log_info "Werte Ergebnisse aus..."
    
    read -r efficiency wasted_bytes image_size <<< $(echo "$json_output" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    eff = data.get('image', {}).get('efficiency', 0)
    wasted = data.get('image', {}).get('wastedBytes', 0)
    size = data.get('image', {}).get('inefficientBytes', 0) + data.get('image', {}).get('efficiency', 0) # Approximation if strict size missing
    # Dive JSON structure varies, let's try robust mapping
    stats = data.get('image', {})
    print(f\"{stats.get('efficiency', 0)} {stats.get('wastedBytes', 0)} {stats.get('totalImageSize', 0)}\")
except Exception as e:
    print(\"0 0 0\")
")

    # Konvertierung in lesbare Einheiten
    local image_size_mb=$(echo "$image_size / 1024 / 1024" | bc)
    local wasted_mb=$(echo "$wasted_bytes / 1024 / 1024" | bc)
    # Effizienz ist meist 0.9x, wir wollen Prozent
    local efficiency_percent=$(echo "$efficiency * 100" | bc | awk '{printf "%.2f", $0}')
    
    # 3. Report Ausgabe
    echo ""
    echo "--------------------------------------------------------"
    echo "📊 AUDIT REPORT: $IMAGE_NAME"
    echo "--------------------------------------------------------"
    echo -e "Gesamtgröße:      ${BLUE}${image_size_mb} MB${NC}"
    echo -e "Verschwendet:     ${YELLOW}${wasted_mb} MB${NC}"
    echo -e "Effizienz-Score:  ${BLUE}${efficiency_percent}%${NC}"
    echo "--------------------------------------------------------"

    # 4. Validierung gegen Grenzwerte
    local failed=0

    # Check Effizienz
    if (( $(echo "$efficiency_percent < $MIN_EFFICIENCY" | bc -l) )); then
        log_error "Effizienz unter Grenzwert! ($efficiency_percent% < $MIN_EFFICIENCY%)"
        failed=1
    else
        log_success "Effizienz OK."
    fi

    # Check Verschwendung
    if (( $(echo "$wasted_mb > $MAX_WASTED_MB" | bc -l) )); then
        log_warn "Verschwendeter Speicher hoch ($wasted_mb MB > $MAX_WASTED_MB MB). Bitte Layer optimieren."
        # Wir lassen den Build hier nicht fehlschlagen, nur Warnung
    else
        log_success "Verschwendung im Rahmen."
    fi

    if [ $failed -eq 1 ]; then
        echo ""
        log_error "AUDIT FEHLGESCHLAGEN. Das Image entspricht nicht dem Goldstandard."
        log_info "Tipp: Nutzen Sie Multi-Stage Builds und 'apt-get clean && rm -rf /var/lib/apt/lists/*'"
        exit 1
    fi

    log_success "Audit erfolgreich bestanden."
    exit 0
}

main
