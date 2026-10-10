#!/bin/bash
# hardware_probe.sh - LLM Framework Hardware Analyzer (Linux Enterprise v2.4)
# DIREKTIVE: Goldstandard Deep Scan.
#
# Ziel: Liefert ALLE verfügbaren Hardware-Metriken (Cache, RAM-Timings/Speed, Flags),
# um präzise Entscheidungen für Quantisierung und Layer-Offloading zu ermöglichen.
#
# HISTORY:
# v2.4.0: Added Deep RAM Analysis (Speed/Type via dmidecode), Cache Topology, Swap info, and FULL CPU Flags.
# v2.3.0: Strict VID/PID checks.

set -u

OUTPUT_FILE="target_hardware_config.txt"

# --- HELPER FUNCTIONS ---
log() { echo -e "\033[1;34m[PROBE]\033[0m $1"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $1"; }
write_conf() { echo "$1=$2" >> "$OUTPUT_FILE"; }

# Tool check & install logic (Preserved)
check_and_install_tool() {
    TOOL_BIN=$1; PKG_NAME=$2
    if ! command -v "$TOOL_BIN" &> /dev/null; then
        if [ "${NON_INTERACTIVE:-false}" = "true" ]; then return 1; fi
        read -p "[WARN] Missing '$TOOL_BIN'. Install '$PKG_NAME'? (y/n) " -n 1 -r; echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if command -v apt-get &>/dev/null; then sudo apt-get update && sudo apt-get install -y "$PKG_NAME"
            elif command -v dnf &>/dev/null; then sudo dnf install -y "$PKG_NAME"
            elif command -v pacman &>/dev/null; then sudo pacman -S --noconfirm "$PKG_NAME"
            else return 1; fi
        else return 1; fi
    fi
    return 0
}

# Helper for ID extraction
get_pci_ids() { command -v lspci &>/dev/null && lspci -n -d $1: | head -n1 | awk '{print $3}'; }
get_usb_ids() { command -v lsusb &>/dev/null && lsusb -d $1: | head -n1 | awk '{print $6}'; }

# --- START ---
log "Starting Deep Hardware Scan v2.4..."
echo "# LLM Framework Hardware Profile (Linux Deep Scan)" > "$OUTPUT_FILE"
echo "GENERATED_AT=$(date)" >> "$OUTPUT_FILE"

# Tools check
check_and_install_tool "lspci" "pciutils"
check_and_install_tool "lsusb" "usbutils"
# dmidecode is optional but recommended for RAM details
check_and_install_tool "dmidecode" "dmidecode"

# --- 1. SYSTEM CORE ---
write_conf "PLATFORM" "Linux"
write_conf "ARCH" "$(uname -m)"
write_conf "KERNEL" "$(uname -r)"
write_conf "HOSTNAME" "$(hostname)"
[ -f /etc/os-release ] && . /etc/os-release && write_conf "OS_PRETTY_NAME" "$PRETTY_NAME"

# --- 2. CPU DEEP DIVE ---
log "Analyzing CPU Topology & Flags..."
if [ -f /proc/cpuinfo ]; then
    # Identification
    MODEL=$(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | xargs)
    [ -z "$MODEL" ] && MODEL=$(grep -m1 "Hardware" /proc/cpuinfo | cut -d: -f2 | xargs)
    write_conf "CPU_MODEL" "$MODEL"
    write_conf "CPU_CORES_LOGICAL" "$(grep -c processor /proc/cpuinfo)"
    
    # PID construction
    VID=$(grep -m1 "vendor_id" /proc/cpuinfo | cut -d: -f2 | xargs)
    FAM=$(grep -m1 "cpu family" /proc/cpuinfo | cut -d: -f2 | xargs)
    MOD=$(grep -m1 "model" /proc/cpuinfo | awk '$1=="model" {print $3}')
    STEP=$(grep -m1 "stepping" /proc/cpuinfo | cut -d: -f2 | xargs)
    write_conf "CPU_VENDOR_ID" "$VID"
    write_conf "CPU_DEVICE_ID" "${FAM}:${MOD}:${STEP}"

    # FULL FLAGS (Crucial for Optimizations like AVX512, AMX, NEON)
    # We clean it up to be a single line space-separated string
    FLAGS=$(grep -m1 "^flags" /proc/cpuinfo | cut -d: -f2 | xargs)
    [ -z "$FLAGS" ] && FLAGS=$(grep -m1 "^Features" /proc/cpuinfo | cut -d: -f2 | xargs) # ARM
    write_conf "CPU_FLAGS_ALL" "$FLAGS"
    
    # Specific Checks
    if echo "$FLAGS" | grep -iq "avx2"; then write_conf "SUPPORTS_AVX2" "ON"; else write_conf "SUPPORTS_AVX2" "OFF"; fi
    if echo "$FLAGS" | grep -iq "avx512"; then write_conf "SUPPORTS_AVX512" "ON"; else write_conf "SUPPORTS_AVX512" "OFF"; fi
    if echo "$FLAGS" | grep -iq "f16c"; then write_conf "SUPPORTS_FP16" "ON"; else write_conf "SUPPORTS_FP16" "OFF"; fi
    if echo "$FLAGS" | grep -iqE "neon|asimd"; then write_conf "SUPPORTS_NEON" "ON"; else write_conf "SUPPORTS_NEON" "OFF"; fi
fi

# Caches (via lscpu if avail)
if command -v lscpu &>/dev/null; then
    L1D=$(lscpu | grep "L1d" | awk '{print $3}' | head -n1)
    L2=$(lscpu | grep "L2" | awk '{print $3}' | head -n1)
    L3=$(lscpu | grep "L3" | awk '{print $3}' | head -n1)
    write_conf "CPU_L1D_CACHE" "$L1D"
    write_conf "CPU_L2_CACHE" "$L2"
    write_conf "CPU_L3_CACHE" "$L3"
fi

# --- 3. MEMORY DEEP DIVE ---
log "Analyzing Memory Subsystem..."
# Capacity
if [ -f /proc/meminfo ]; then
    RAM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    RAM_MB=$((RAM_KB / 1024))
    write_conf "RAM_TOTAL_MB" "$RAM_MB"
    
    # Swap / ZRAM check (Critical for offloading overflow)
    SWAP_KB=$(grep SwapTotal /proc/meminfo | awk '{print $2}')
    SWAP_MB=$((SWAP_KB / 1024))
    write_conf "RAM_SWAP_MB" "$SWAP_MB"
fi

# Speed & Type (Requires Root/Sudo & dmidecode)
# Try execution
DMI_OUT=""
if [ "$EUID" -eq 0 ] && command -v dmidecode &>/dev/null; then
    DMI_OUT=$(dmidecode -t memory)
elif command -v sudo &>/dev/null && command -v dmidecode &>/dev/null; then
    # Try non-interactive sudo if possible, or warn
    if sudo -n true 2>/dev/null; then
        DMI_OUT=$(sudo dmidecode -t memory)
    else
        log "Skipping deep RAM scan (dmidecode needs root privileges)."
    fi
fi

if [ ! -z "$DMI_OUT" ]; then
    # Extract Max Speed (MT/s)
    SPEED=$(echo "$DMI_OUT" | grep "Speed:" | grep -v "Unknown" | sort -rn | head -n1 | awk '{print $2}')
    TYPE=$(echo "$DMI_OUT" | grep "Type:" | grep -v "Unknown" | sort | uniq | head -n1 | awk '{print $2}')
    SLOTS=$(echo "$DMI_OUT" | grep "Form Factor" | wc -l)
    
    write_conf "RAM_SPEED_MTS" "${SPEED:-Unknown}"
    write_conf "RAM_TYPE" "${TYPE:-Unknown}"
    write_conf "RAM_SLOTS_DETECTED" "$SLOTS"
else
    write_conf "RAM_SPEED_MTS" "Unknown (No Root)"
    write_conf "RAM_TYPE" "Unknown (No Root)"
fi

# --- 4. ACCELERATORS (STRICT ID) ---
log "Analyzing Accelerators..."
NPU_FOUND="FALSE"

# [Logic identical to V2.3 but logging more verbose details if possible]

# A. Rockchip
if [ -e /dev/rknpu ]; then
    log "-> Found Rockchip NPU"
    write_conf "NPU_VENDOR" "Rockchip"
    write_conf "NPU_MODE" "SoC"
    write_conf "SUPPORTS_RKNN" "ON"
    if grep -q "rk3588" /proc/device-tree/compatible 2>/dev/null; then
        write_conf "NPU_DEVICE_ID" "SoC_RK3588"
        write_conf "NPU_MODEL" "RK3588"
    elif grep -q "rk3566" /proc/device-tree/compatible 2>/dev/null; then
        write_conf "NPU_DEVICE_ID" "SoC_RK3566"
        write_conf "NPU_MODEL" "RK3566"
    fi
    NPU_FOUND="TRUE"
fi
# Rockchip USB
RK_IDS=$(get_usb_ids "2207")
if [ ! -z "$RK_IDS" ]; then
    write_conf "NPU_VENDOR" "Rockchip"
    VID=${RK_IDS%:*}
    PID=${RK_IDS#*:}
    write_conf "NPU_VENDOR_ID" "0x$VID"
    write_conf "NPU_DEVICE_ID" "0x$PID"
    case "$PID" in
        "350a") write_conf "NPU_MODE" "Maskrom (RK3588)" ;;
        "350b") write_conf "NPU_MODE" "Loader (RK3588)" ;;
        "0006") write_conf "NPU_MODE" "ADB/MTP" ;;
        "0019") write_conf "NPU_MODE" "ADB" ;;
        *)      write_conf "NPU_MODE" "Unknown_USB" ;;
    esac
    NPU_FOUND="TRUE"
fi

# B. NVIDIA
NV_IDS=$(get_pci_ids "10de")
if [ ! -z "$NV_IDS" ]; then
    log "-> Found NVIDIA GPU"
    VID=${NV_IDS%:*}
    PID=${NV_IDS#*:}
    write_conf "GPU_VENDOR" "NVIDIA"
    write_conf "GPU_VENDOR_ID" "0x$VID"
    write_conf "GPU_DEVICE_ID" "0x$PID"
    write_conf "SUPPORTS_CUDA" "ON"
    if command -v nvidia-smi &>/dev/null; then
        write_conf "GPU_MODEL" "$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)"
        write_conf "GPU_VRAM_MB" "$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1)"
        write_conf "GPU_DRIVER_VERSION" "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)"
    fi
else
    write_conf "SUPPORTS_CUDA" "OFF"
fi

# C. Hailo / Axelera / MemryX / Intel (Consolidated Logic)
# Helper for generic PCI detection
detect_pci_npu() {
    NAME=$1; VID=$2; FLAG=$3
    IDS=$(get_pci_ids "$VID")
    if [ ! -z "$IDS" ]; then
        log "-> Found $NAME"
        write_conf "NPU_VENDOR" "$NAME"
        write_conf "NPU_VENDOR_ID" "0x${IDS%:*}"
        write_conf "NPU_DEVICE_ID" "0x${IDS#*:}"
        write_conf "$FLAG" "ON"
        NPU_FOUND="TRUE"
        return 0
    fi
    return 1
}

detect_pci_npu "Hailo" "1e60" "SUPPORTS_HAILO" && [ -e /dev/hailo0 ] && write_conf "DEVICE_NODE" "/dev/hailo0"
detect_pci_npu "Axelera" "1f4b" "SUPPORTS_METIS"
detect_pci_npu "MemryX" "1d6b" "SUPPORTS_MX3" # Warning: 1d6b is generic, but often used by prototypes. Refine if real VID known.

# Intel GPU
INTEL_IDS=$(get_pci_ids "8086")
if [ ! -z "$INTEL_IDS" ]; then
    if lspci -d 8086: -n | grep -E "^.*: 03" >/dev/null; then
        write_conf "GPU_VENDOR" "Intel"
        write_conf "GPU_DEVICE_ID" "0x${INTEL_IDS#*:}"
        write_conf "SUPPORTS_INTEL_XPU" "ON"
    fi
fi

[ "$NPU_FOUND" = "FALSE" ] && write_conf "NPU_STATUS" "None detected"

# --- 5. SOFTWARE ---
log "[SOFTWARE]"
docker --version &>/dev/null && write_conf "DOCKER_VERSION" "$(docker --version|awk '{print $3}'|tr -d ',')" || write_conf "DOCKER_VERSION" "Missing"
python3 --version &>/dev/null && write_conf "PYTHON_VERSION" "$(python3 --version|awk '{print $2}')" || write_conf "PYTHON_VERSION" "Missing"

log "Scan complete. Data written to $OUTPUT_FILE"
