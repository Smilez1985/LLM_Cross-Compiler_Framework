#!/bin/bash
# setup_linux.sh - Headless Setup for LLM Framework
# Enterprise Grade: Robust networking, auto-install, permission fix.

set -u # Treat unset variables as an error

# --- HELPER FUNCTIONS ---

log() {
    echo "[$(date +'%H:%M:%S')] $1"
}

wait_for_internet() {
    local host="8.8.8.8"
    local timeout_secs=300
    local start_time=$(date +%s)
    
    log "Checking internet connection..."
    
    while ! ping -c 1 -W 1 "$host" &> /dev/null; do
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        
        if [ "$elapsed" -ge "$timeout_secs" ]; then
            log "❌ Error: No internet connection for $timeout_secs seconds. Aborting."
            exit 1
        fi
        
        echo -ne "\r⚠️  No connection. Waiting... (${elapsed}s)"
        sleep 2
    done
    echo "" # Newline after wait loop
    log "✅ Internet connected."
}

download_file() {
    local url="$1"
    local output="$2"
    
    wait_for_internet
    
    log "Downloading $url..."
    # curl mit retry optionen für Robustheit
    if ! curl -fsSL --retry 5 --retry-delay 2 --retry-max-time 60 "$url" -o "$output"; then
        log "❌ Download failed even after retries."
        exit 1
    fi
}

# --- MAIN LOGIC ---

log ">> [Setup] Starting Enterprise Setup..."

# 1. Check Docker Existence
if ! command -v docker &> /dev/null; then
    log ">> [Setup] Docker not found. Starting auto-installation..."
    
    # Secure Download with Loop
    download_file "https://get.docker.com" "get-docker.sh"
    
    log ">> [Setup] Running Docker Installer (requires sudo)..."
    # Wir nutzen sh, das Skript fordert sudo bei Bedarf an
    if ! sh get-docker.sh; then
        log "❌ Docker installation script returned error."
        exit 1
    fi
    
    rm get-docker.sh
    log ">> [Setup] Docker installation completed."
    
    # 2. Group Permissions (User Logic)
    # Prüfen, ob Gruppe existiert (sollte der Installer gemacht haben)
    if getent group docker > /dev/null; then
        log ">> [Setup] Adding user '$USER' to 'docker' group..."
        sudo usermod -aG docker "$USER"
        
        echo "------------------------------------------------------------------------"
        echo "✅ Setup finished."
        echo "⚠️  CRITICAL: You must log out and log back in for group changes to apply."
        echo "   Alternative for current shell: 'newgrp docker'"
        echo "------------------------------------------------------------------------"
    else
        log "⚠️  Warning: 'docker' group not found. Skipping user modification."
    fi

else
    log "✅ [Setup] Docker is already installed."
fi

# 3. Verify Access
if ! docker info &> /dev/null; then
    log "⚠️  [Setup] Docker is installed, but you don't have permissions."
    log "   Trying to fix group membership..."
    
    if getent group docker > /dev/null; then
        sudo usermod -aG docker "$USER"
        log "   User added to group. Please run 'newgrp docker' or re-login."
    else
        log "   Error: 'docker' group missing."
    fi
    # Wir brechen hier nicht hart ab, da der User evtl. sudo nutzen will
fi

# 4. Check Docker Compose (Plugin)
if ! docker compose version &> /dev/null; then
    log "⚠️  [Setup] 'docker compose' plugin not found."
    log "   Attempting to install plugin via apt (if Debian/Ubuntu)..."
    
    if command -v apt-get &> /dev/null; then
        wait_for_internet
        sudo apt-get update && sudo apt-get install -y docker-compose-plugin
    else
        log "   Manual installation required for your distro."
    fi
fi

log "✅ Setup check complete."
