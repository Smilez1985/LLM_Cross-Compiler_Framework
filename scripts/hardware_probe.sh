#!/bin/sh
# hardware_probe.sh - hardware probe of the LLM Cross-Compiler Framework (manual fallback for Linux, schema 2).
#
# Writes target_hardware_config.txt: KEY=VALUE lines starting with PROBE_SCHEMA=2, exactly the keys of
# configs/hardware_probe_schema.yml in the framework and the same values scripts/hardware_probe.py writes. Use it
# where Python 3.8 or newer is missing (minimal board images, air-gapped machines): run it on the machine, carry the
# file to the framework and import it there.
#
# Usage: hardware_probe.sh [--output FILE | --output -] [--no-toolchain] [--install-tools]
#   --output FILE    file to write (default: target_hardware_config.txt in the current folder); "-": standard output
#   --no-toolchain   skip the compiler and build tool versions
#   --install-tools  opt-in: offer to install vulkan-tools (vulkaninfo) with the package manager first; asks on the
#                    terminal and uses sudo. Without it the probe never prompts, installs or asks for a password.
#
# POSIX sh (dash, busybox ash, bash), LC_ALL=C. No network, no stdin (tools get /dev/null), no sudo. No host name,
# user name, path, serial number or MAC address is read into the result; free text is printable ASCII.
# HW_PROBE_SYSROOT (for tests): a folder that stands in for / when files are read; the kernel release and machine
# then come from <sysroot>/proc/sys/kernel/osrelease and .../arch, and the host's getconf/ldconfig are not asked.

set -u
LC_ALL=C
export LC_ALL

PROBE_VERSION=3.0.0
OUTPUT_FILE=target_hardware_config.txt
INSTALL_TOOLS=0
TOOLCHAIN=1

usage() { echo "Usage: $0 [--output FILE|-] [--no-toolchain] [--install-tools]"; }

while [ $# -gt 0 ]; do
    case "$1" in
        -o|--output)
            if [ $# -lt 2 ] || [ -z "$2" ]; then usage >&2; exit 2; fi
            OUTPUT_FILE=$2; shift 2 ;;
        --output=*)
            OUTPUT_FILE=${1#--output=}
            if [ -z "$OUTPUT_FILE" ]; then usage >&2; exit 2; fi
            shift ;;
        --no-toolchain) TOOLCHAIN=0; shift ;;
        --install-tools) INSTALL_TOOLS=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1 ($(usage))" >&2; exit 2 ;;
    esac
done

# --- helpers -----------------------------------------------------------------------------------------------------

NL='
'
SEP=$(printf '\037')   # field separator of GPU records (not whitespace, so empty fields survive word splitting)
OUT=""
WRITTEN=" "
WARNINGS=""

log() { printf '[PROBE] %s\n' "$*" >&2; }

R=${HW_PROBE_SYSROOT:-}
[ "$R" = "/" ] && R=""
R=${R%/}
SYSROOT=0
[ -n "$R" ] && SYSROOT=1

f() { printf '%s%s' "$R" "$1"; }                       # a machine path inside the sysroot
exists() { [ -e "$(f "$1")" ] || [ -L "$(f "$1")" ]; }
rd() { [ -f "$(f "$1")" ] && cat "$(f "$1")" 2>/dev/null; }
rdp() { [ -f "$1" ] && cat "$1" 2>/dev/null; }          # a file by its real path (already inside the sysroot)
have() { command -v "$1" >/dev/null 2>&1; }
trim() { printf '%s' "$1" | sed 's/^[ 	]*//; s/[ 	]*$//'; }
lower() { printf '%s' "$1" | tr 'A-Z' 'a-z'; }
link_name() { l=$(readlink "$1" 2>/dev/null) || return 1; l=${l%/}; printf '%s' "${l##*/}"; }

# A tool with an empty stdin and a time limit where `timeout N` works (old busybox wants -t); stdout only, tool_all
# with stderr.
TIMEOUT=""
if have timeout && timeout 5 true </dev/null >/dev/null 2>&1; then TIMEOUT="timeout 15"; fi
tool() { $TIMEOUT "$@" </dev/null 2>/dev/null; }
tool_all() { $TIMEOUT "$@" </dev/null 2>&1; }

valid() {  # ERE VALUE: the whole value (one line) matches
    case "$2" in *"$NL"*) return 1 ;; esac
    printf '%s\n' "$2" | grep -Eq -- "$1"
}

clean_text() {  # VALUE [LIMIT]: printable ASCII, whitespace collapsed, at most LIMIT (128) characters
    printf '%s' "$1" | tr -c ' -~' ' ' | tr -s ' ' | sed 's/^ //; s/ $//' | cut -c1-"${2:-128}" | sed 's/ $//'
}

first_number() {  # the first dotted number (at most 4 parts) of a text
    printf '%s\n' "$1" | grep -oE '[0-9]{1,5}(\.[0-9]{1,6}){0,3}' | head -n1
}

hexid() {  # VALUE DIGITS: 0x41 / 41 / 0X041 -> 0x41 with at least DIGITS lower-case hex digits
    v=$(trim "$1")
    case $v in 0x*|0X*) v=${v#0[xX]} ;; esac
    case $v in ''|*[!0-9a-fA-F]*) return 1 ;; esac
    [ ${#v} -le 8 ] || return 1
    printf "0x%0${2}x" "0x$v"
}

warn() {
    case ",$WARNINGS," in *",$1,"*) return 0 ;; esac
    WARNINGS="${WARNINGS:+$WARNINGS,}$1"
}

written() { case "$WRITTEN" in *" $1 "*) return 0 ;; esac; return 1; }
emit() { OUT="$OUT$1=$2$NL"; WRITTEN="$WRITTEN$1 "; }
getv() { printf '%s' "$OUT" | sed -n "s/^$1=//p" | head -n1; }

put() {  # KEY VALUE [ERE] [quiet]: writes a value once; one that does not fit is left out (with a warning)
    [ -n "$2" ] || return 1
    written "$1" && return 1
    if [ -n "${3:-}" ] && ! valid "$3" "$2"; then
        [ -n "${4:-}" ] || warn "not_determined:$1"
        return 1
    fi
    emit "$1" "$2"
}

put_text() {  # KEY VALUE [quiet]
    [ -n "$2" ] || return 1
    t=$(clean_text "$2")
    if [ -z "$t" ]; then [ -n "${3:-}" ] || warn "not_determined:$1"; return 1; fi
    put "$1" "$t"
}

# Validation of the schema (configs/hardware_probe_schema.yml) for the values this script reads from files and tools.
P_IDENT='^[a-z0-9][a-z0-9._-]{0,31}$'
P_VERSION='^[0-9]{1,5}(\.[0-9]{1,6}){0,3}$'
P_OS_VERSION='^[A-Za-z0-9][A-Za-z0-9._~-]{0,31}$'
P_KERNEL='^[A-Za-z0-9][A-Za-z0-9._+~-]{0,63}$'
P_LIBC_VERSION='^[0-9]{1,3}(\.[0-9]{1,5}){1,3}$'
P_DOTTED='^[0-9]{1,3}(\.[0-9]{1,4}){1,3}$'
P_SOC_VENDOR='^[a-z0-9][a-z0-9-]{0,31}$'
P_CUDA='^[0-9]{1,2}\.[0-9]{1,3}$'
P_TOOLCHAIN='^(gcc|clang|appleclang|msvc|icx)-[0-9]{1,4}(\.[0-9]{1,6}){0,3}$'
P_INT='^(0|[1-9][0-9]{0,11})$'
P_NPU_DEVICE='^/dev/(rknpu|dri/(renderD|card)[0-9]{1,3}|accel/accel[0-9]{1,3}|hailo[0-9]{1,3}|apex_[0-9]{1,3})$'
# PCI device ids of AMD APU graphics (amdgpu_drv.c): their "VRAM" is a carve-out of the system RAM.
AMD_APU_IDS="0x15dd 0x15d8 0x1636 0x1638 0x164c 0x15e7 0x163f 0x1435 0x1681 0x1506 0x164e 0x13c0 0x15bf 0x15c8"
AMD_APU_IDS="$AMD_APU_IDS 0x150e 0x1114 0x1586"

int_in() {  # VALUE MIN MAX
    valid "$P_INT" "$1" || return 1
    [ ${#1} -le 12 ] && [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

put_int() {  # KEY VALUE MIN MAX [quiet]
    [ -n "$2" ] || return 1
    if ! int_in "$2" "$3" "$4"; then [ -n "${5:-}" ] || warn "not_determined:$1"; return 1; fi
    put "$1" "$2"
}

# --- tables (copies of configs/hardware_probe_schema.yml tables; tests compare them) -----------------------------

arch_alias() {  # uname -m (lower case) -> ARCH
    case "$1" in
        x86_64) echo x86_64 ;;
        amd64) echo x86_64 ;;
        x64) echo x86_64 ;;
        x86-64) echo x86_64 ;;
        x86) echo x86 ;;
        i386) echo x86 ;;
        i486) echo x86 ;;
        i586) echo x86 ;;
        i686) echo x86 ;;
        arm64) echo arm64 ;;
        aarch64) echo arm64 ;;
        arm64e) echo arm64 ;;
        armv8l) echo armv7 ;;
        armv7) echo armv7 ;;
        armv7l) echo armv7 ;;
        armv7hl) echo armv7 ;;
        armhf) echo armv7 ;;
        armv6) echo armv6 ;;
        armv6l) echo armv6 ;;
        riscv64) echo riscv64 ;;
        ppc64le) echo ppc64le ;;
        s390x) echo s390x ;;
        loongarch64) echo loongarch64 ;;
        *) echo other ;;
    esac
}
os_family_of() {  # an OS ID -> OS_FAMILY (tables.os_families)
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
x86_vendor() {  # vendor_id -> CPU_VENDOR
    case "$1" in
        GenuineIntel) echo intel ;;
        AuthenticAMD) echo amd ;;
        HygonGenuine) echo hygon ;;
        CentaurHauls) echo centaur ;;
        Shanghai) echo zhaoxin ;;
        *) echo other ;;
    esac
}
arm_implementer() {  # CPU implementer (0x41) -> CPU_VENDOR
    case "$1" in
        0x41) echo arm ;;
        0x42) echo broadcom ;;
        0x43) echo cavium ;;
        0x46) echo fujitsu ;;
        0x48) echo hisilicon ;;
        0x4e) echo nvidia ;;
        0x50) echo apm ;;
        0x51) echo qualcomm ;;
        0x53) echo samsung ;;
        0x56) echo marvell ;;
        0x61) echo apple ;;
        0x66) echo faraday ;;
        0x69) echo intel ;;
        0x6d) echo microsoft ;;
        0x70) echo phytium ;;
        0xc0) echo ampere ;;
        *) echo other ;;
    esac
}
arm_part() {  # <implementer>:<part> -> core name (tables.arm_parts)
    case "$1" in
        0x41:0xb76) echo ARM1176 ;;
        0x41:0xc05) echo Cortex-A5 ;;
        0x41:0xc07) echo Cortex-A7 ;;
        0x41:0xc08) echo Cortex-A8 ;;
        0x41:0xc09) echo Cortex-A9 ;;
        0x41:0xc0d) echo Cortex-A17 ;;
        0x41:0xc0f) echo Cortex-A15 ;;
        0x41:0xd01) echo Cortex-A32 ;;
        0x41:0xd02) echo Cortex-A34 ;;
        0x41:0xd03) echo Cortex-A53 ;;
        0x41:0xd04) echo Cortex-A35 ;;
        0x41:0xd05) echo Cortex-A55 ;;
        0x41:0xd06) echo Cortex-A65 ;;
        0x41:0xd07) echo Cortex-A57 ;;
        0x41:0xd08) echo Cortex-A72 ;;
        0x41:0xd09) echo Cortex-A73 ;;
        0x41:0xd0a) echo Cortex-A75 ;;
        0x41:0xd0b) echo Cortex-A76 ;;
        0x41:0xd0c) echo Neoverse-N1 ;;
        0x41:0xd0d) echo Cortex-A77 ;;
        0x41:0xd0e) echo Cortex-A76AE ;;
        0x41:0xd40) echo Neoverse-V1 ;;
        0x41:0xd41) echo Cortex-A78 ;;
        0x41:0xd42) echo Cortex-A78AE ;;
        0x41:0xd43) echo Cortex-A65AE ;;
        0x41:0xd44) echo Cortex-X1 ;;
        0x41:0xd46) echo Cortex-A510 ;;
        0x41:0xd47) echo Cortex-A710 ;;
        0x41:0xd48) echo Cortex-X2 ;;
        0x41:0xd49) echo Neoverse-N2 ;;
        0x41:0xd4a) echo Neoverse-E1 ;;
        0x41:0xd4b) echo Cortex-A78C ;;
        0x41:0xd4c) echo Cortex-X1C ;;
        0x41:0xd4d) echo Cortex-A715 ;;
        0x41:0xd4e) echo Cortex-X3 ;;
        0x41:0xd4f) echo Neoverse-V2 ;;
        0x41:0xd80) echo Cortex-A520 ;;
        0x41:0xd81) echo Cortex-A720 ;;
        0x41:0xd82) echo Cortex-X4 ;;
        0x41:0xd84) echo Neoverse-V3 ;;
        0x41:0xd85) echo Cortex-X925 ;;
        0x41:0xd87) echo Cortex-A725 ;;
        0x41:0xd8e) echo Neoverse-N3 ;;
        *) return 1 ;;
    esac
}
pci_vendor() {  # PCI vendor id -> GPU vendor
    case "$1" in
        0x10de) echo nvidia ;;
        0x1002) echo amd ;;
        0x1022) echo amd ;;
        0x8086) echo intel ;;
        0x106b) echo apple ;;
        0x13b5) echo arm ;;
        0x5143) echo qualcomm ;;
        0x1010) echo imagination ;;
        0x14e4) echo broadcom ;;
        *) echo other ;;
    esac
}
usb_rockchip_mode() {  # Rockchip USB product id -> USB_ROCKCHIP_MODE
    case "$1" in
        0x350a) echo rockusb ;;
        0x350b) echo rockusb ;;
        0x0006) echo adb-mtp ;;
        0x0019) echo adb ;;
        *) echo other ;;
    esac
}

# --- opt-in: helper tools ------------------------------------------------------------------------------------------

install_tools() {
    have vulkaninfo && return 0
    if [ ! -t 0 ]; then log "--install-tools needs a terminal to ask; skipped."; return 0; fi
    printf '[PROBE] vulkaninfo is missing. Install vulkan-tools? (y/n) ' >&2
    read -r answer || answer=n
    case "$answer" in y|Y|yes|YES) ;; *) return 0 ;; esac
    sudo_cmd=""
    [ "$(id -u 2>/dev/null)" = 0 ] || sudo_cmd=sudo
    if have apt-get; then $sudo_cmd apt-get update && $sudo_cmd apt-get install -y vulkan-tools
    elif have dnf; then $sudo_cmd dnf install -y vulkan-tools
    elif have pacman; then $sudo_cmd pacman -S --noconfirm vulkan-tools
    elif have apk; then $sudo_cmd apk add vulkan-tools
    else log "No supported package manager found."
    fi
}

[ "$INSTALL_TOOLS" = 1 ] && install_tools

log "Hardware probe $PROBE_VERSION (schema 2)..."

# --- meta and system -------------------------------------------------------------------------------------------

emit PROBE_SCHEMA 2
emit PROBE_TOOL sh
emit PROBE_TOOL_VERSION "$PROBE_VERSION"
emit GENERATED_AT "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

case "$(uname -s 2>/dev/null)" in
    Linux) ;;
    *) log "This script probes Linux; use hardware_probe.py (Python 3.8+) or hardware_probe.ps1 (Windows)."; exit 3 ;;
esac
OS=linux
exists /system/build.prop && OS=android
emit OS "$OS"

if [ "$SYSROOT" = 1 ]; then
    REL=$(rd /proc/sys/kernel/osrelease | head -n1)
    MACH=$(rd /proc/sys/kernel/arch | head -n1)
else
    REL=$(uname -r 2>/dev/null)
    MACH=$(uname -m 2>/dev/null)
fi
put KERNEL "$(trim "$REL")" "$P_KERNEL"
KERNEL_ARCH=$(arch_alias "$(lower "$(trim "$MACH")")")
put KERNEL_ARCH "$KERNEL_ARCH"
ARCH=$KERNEL_ARCH
# Binaries must match the userland: a 32-bit userland on a 64-bit kernel gets the 32-bit ARCH.
if [ "$SYSROOT" = 0 ] && [ "$(getconf LONG_BIT 2>/dev/null)" = 32 ]; then
    case "$ARCH" in x86_64) ARCH=x86 ;; arm64) ARCH=armv7 ;; esac
fi
put ARCH "$ARCH"
case "$ARCH" in x86_64|x86) ARCH_CLASS=x86 ;; arm64|armv7|armv6) ARCH_CLASS=arm ;; *) ARCH_CLASS=other ;; esac

# /etc/os-release is parsed, never sourced.
OSR=$(f /etc/os-release)
[ -f "$OSR" ] || OSR=$(f /usr/lib/os-release)
osr() {
    [ -f "$OSR" ] || return 0
    # CR of a CRLF file removed, as the Python probe's line split does.
    v=$(tr -d '\r' < "$OSR" | grep -E "^[[:space:]]*$1=" | head -n1 | sed "s/^[[:space:]]*$1=//")
    v=$(trim "$v")
    case $v in
        \"*\") v=${v#\"}; v=${v%\"}; v=$(printf '%s' "$v" | sed 's/\\\(.\)/\1/g') ;;
        \'*\') v=${v#\'}; v=${v%\'} ;;
    esac
    printf '%s' "$v"
}
if [ -f "$OSR" ]; then
    OS_ID=$(lower "$(osr ID)")
    LIKE_RAW=$(lower "$(osr ID_LIKE)")
    put OS_ID "$OS_ID" "$P_IDENT"
    if [ -n "$(trim "$LIKE_RAW")" ]; then
        set -f
        LIKE=$(printf '%s\n' $LIKE_RAW | grep -E "$P_IDENT" | awk '!seen[$0]++' | head -n16 | tr '\n' ',' |
               sed 's/,$//')
        set +f
        if [ -n "$LIKE" ]; then put OS_ID_LIKE "$LIKE"; else warn "not_determined:OS_ID_LIKE"; fi
    fi
    if [ "$OS" = linux ]; then
        FAMILY=other
        # Only well-formed IDs count (a crafted ID such as "x debian" must not pick a family).
        CANDIDATES=""
        valid "$P_IDENT" "$OS_ID" && CANDIDATES=$OS_ID
        CANDIDATES="$CANDIDATES $LIKE_RAW"
        set -f
        for candidate in $CANDIDATES; do
            valid "$P_IDENT" "$candidate" || continue
            if fam=$(os_family_of "$candidate"); then FAMILY=$fam; break; fi
        done
        set +f
        put OS_FAMILY "$FAMILY"
        VARIANT=$(lower "$(osr VARIANT_ID)")
        if [ -z "$VARIANT" ]; then
            if exists /boot/dietpi/.version; then VARIANT=dietpi
            elif exists /etc/armbian-release; then VARIANT=armbian
            fi
        fi
        put OS_VARIANT_ID "$VARIANT" "$P_IDENT"
    fi
    put OS_VERSION_ID "$(osr VERSION_ID)" "$P_OS_VERSION"
    put OS_VERSION_CODENAME "$(lower "$(osr VERSION_CODENAME)")" "$P_IDENT"
    put_text OS_PRETTY_NAME "$(osr PRETTY_NAME)"
elif [ "$OS" = linux ]; then
    warn "not_determined:OS_ID"
fi

if [ "$OS" = android ]; then
    put OS_ID android
    put OS_FAMILY android
    put LIBC bionic
    if [ "$SYSROOT" = 0 ] && have getprop; then
        put OS_VERSION_ID "$(trim "$(tool getprop ro.build.version.release)")" "$P_OS_VERSION" quiet
    fi
else
    LIBC_DONE=0
    if [ "$SYSROOT" = 0 ]; then
        g=$(getconf GNU_LIBC_VERSION 2>/dev/null)
        case "$(lower "$g")" in
            glibc*) put LIBC glibc; put LIBC_VERSION "$(first_number "$g")" "$P_LIBC_VERSION"; LIBC_DONE=1 ;;
        esac
    fi
    if [ "$LIBC_DONE" = 0 ]; then
        MUSL=""
        for m in "$(f /lib)"/ld-musl-*.so.1; do [ -e "$m" ] && { MUSL=$m; break; }; done
        if [ -n "$MUSL" ]; then
            put LIBC musl
            MV=""
            if [ "$SYSROOT" = 0 ]; then
                # The musl loader prints its version when run without arguments (exit code 1).
                MV=$("$MUSL" </dev/null 2>&1 | grep -oE 'Version[[:space:]]+[0-9]+\.[0-9]+(\.[0-9]+)?' |
                     head -n1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?')
            fi
            if [ -n "$MV" ]; then put LIBC_VERSION "$MV" "$P_LIBC_VERSION"
            elif [ "$SYSROOT" = 0 ]; then warn "not_determined:LIBC_VERSION"
            fi
            LIBC_DONE=1
        fi
    fi
    if [ "$LIBC_DONE" = 0 ]; then
        for m in "$(f /lib)"/ld-uClibc*.so*; do
            if [ -e "$m" ]; then put LIBC uclibc; LIBC_DONE=1; break; fi
        done
    fi
    if [ "$LIBC_DONE" = 0 ]; then
        for m in "$(f /lib)"/ld-linux*.so* "$(f /lib64)"/ld-linux*.so* "$(f /lib)"/*/ld-linux*.so* \
                 "$(f /lib)"/*/libc.so.6 "$(f /lib64)"/libc.so.6 "$(f /lib)"/libc.so.6 "$(f /usr/lib)"/*/libc.so.6 \
                 "$(f /usr/lib64)"/libc.so.6; do
            if [ -e "$m" ]; then put LIBC glibc; LIBC_DONE=1; break; fi
        done
    fi
    [ "$LIBC_DONE" = 1 ] || warn "not_determined:LIBC"

    if [ -d "$(f /run/systemd/system)" ]; then INIT=systemd
    elif exists /run/openrc; then INIT=openrc
    elif exists /run/runit || exists /etc/runit/runsvdir; then INIT=runit
    elif exists /run/s6 || exists /run/s6-rc; then INIT=s6
    elif exists /sbin/procd; then INIT=procd
    else
        INIT=other
        if [ "$(trim "$(rd /proc/1/comm | head -n1)")" = init ]; then
            if [ "$(link_name "$(f /sbin/init)")" = busybox ]; then INIT=busybox
            elif exists /etc/inittab; then INIT=sysvinit
            fi
        fi
    fi
    put INIT_SYSTEM "$INIT"
fi

# --- CPU -------------------------------------------------------------------------------------------------------

feature() {  # KEY CLASS MODE TOKEN...: ON when all (MODE all) or one (MODE any) of the tokens is in the flag list
    key=$1 class=$2 mode=$3
    shift 3
    if [ "$ARCH_CLASS" != "$class" ]; then emit "$key" OFF; return; fi
    if [ "$HAVE_FLAGS" = 0 ]; then emit "$key" UNKNOWN; return; fi
    result=OFF
    if [ "$mode" = any ]; then
        for t in "$@"; do case " $TOKENS " in *" $t "*) result=ON ;; esac; done
    else
        result=ON
        for t in "$@"; do case " $TOKENS " in *" $t "*) ;; *) result=OFF ;; esac; done
    fi
    emit "$key" "$result"
}

features() {
    feature SUPPORTS_SSE42 x86 all sse4_2
    feature SUPPORTS_AVX x86 all avx
    feature SUPPORTS_AVX2 x86 all avx2
    feature SUPPORTS_BMI2 x86 all bmi2
    feature SUPPORTS_F16C x86 all f16c
    feature SUPPORTS_FMA x86 all fma
    feature SUPPORTS_AVX512 x86 all avx512f
    feature SUPPORTS_AVX512_VBMI x86 all avx512vbmi
    feature SUPPORTS_AVX512_VNNI x86 all avx512_vnni
    feature SUPPORTS_AVX512_BF16 x86 all avx512_bf16
    feature SUPPORTS_AVX_VNNI x86 all avx_vnni
    feature SUPPORTS_AMX_TILE x86 all amx_tile
    feature SUPPORTS_AMX_INT8 x86 all amx_int8
    feature SUPPORTS_AMX_BF16 x86 all amx_bf16
    feature SUPPORTS_NEON arm any asimd neon
    feature SUPPORTS_DOTPROD arm all asimddp
    feature SUPPORTS_FP16_ARITH arm all fphp asimdhp
    feature SUPPORTS_I8MM arm all i8mm
    feature SUPPORTS_SVE arm all sve
    feature SUPPORTS_SVE2 arm all sve2
    feature SUPPORTS_SME arm all sme
    feature SUPPORTS_BF16_ARM arm all bf16
}

CPUINFO=$(f /proc/cpuinfo)
if [ -f "$CPUINFO" ]; then
    # Fields of each "processor : N" block (the first occurrence wins); lines outside a block (Hardware, Revision,
    # Serial) are ignored, so a serial number never reaches the result.
    CPU=$(awk '
        function trim(s) { gsub(/^[ \t\r]+|[ \t\r]+$/, "", s); return s }
        {
            i = index($0, ":"); if (i == 0) next
            key = trim(substr($0, 1, i - 1)); val = trim(substr($0, i + 1))
            if (key == "processor" && val ~ /^[0-9]+$/ && length(val) <= 5) { n++; num[n] = val; next }
            if (n == 0 || key == "" || tolower(key) == "serial") next
            k = n SUBSEP key
            if (!(k in f)) f[k] = val
        }
        END {
            print "COUNT=" n + 0
            if (n == 0) exit
            for (j = 1; j <= n; j++) {
                impl = ((j SUBSEP "CPU implementer") in f) ? f[j SUBSEP "CPU implementer"] : "-"
                part = ((j SUBSEP "CPU part") in f) ? f[j SUBSEP "CPU part"] : "-"
                gsub(/ /, "_", impl); gsub(/ /, "_", part)
                print "CPU=" num[j] " " impl " " part
                if (((j SUBSEP "physical id") in f) && ((j SUBSEP "core id") in f)) {
                    p = f[j SUBSEP "physical id"] SUBSEP f[j SUBSEP "core id"]
                    if (!(p in pairs)) { pairs[p] = 1; np++ }
                }
            }
            print "PAIRS=" np + 0
            if ((1 SUBSEP "model name") in f) print "MODEL=" f[1 SUBSEP "model name"]
            if ((1 SUBSEP "vendor_id") in f) print "VENDOR=" f[1 SUBSEP "vendor_id"]
            if ((1 SUBSEP "flags") in f) print "FLAGS=" f[1 SUBSEP "flags"]
            if ((1 SUBSEP "Features") in f) print "FEATURES=" f[1 SUBSEP "Features"]
            if ((1 SUBSEP "CPU implementer") in f) print "IMPL=" f[1 SUBSEP "CPU implementer"]
        }' "$CPUINFO" 2>/dev/null)
    cpu_field() { printf '%s\n' "$CPU" | sed -n "s/^$1=//p" | head -n1; }
    cpu_has() { printf '%s\n' "$CPU" | grep -q "^$1="; }
    COUNT=$(cpu_field COUNT)
    if [ "${COUNT:-0}" -gt 0 ]; then
        put_int CPU_CORES_LOGICAL "$COUNT" 1 4096
        if [ "$ARCH_CLASS" = arm ]; then
            if cpu_has IMPL; then
                put CPU_VENDOR "$(arm_implementer "$(hexid "$(cpu_field IMPL)" 2)")"
            fi
        elif cpu_has VENDOR; then
            put CPU_VENDOR "$(x86_vendor "$(trim "$(cpu_field VENDOR)")")"
        fi
        PAIRS=$(cpu_field PAIRS)
        if [ "${PAIRS:-0}" -gt 0 ]; then put_int CPU_CORES_PHYSICAL "$PAIRS" 1 4096
        elif [ "$ARCH_CLASS" = arm ]; then put_int CPU_CORES_PHYSICAL "$COUNT" 1 4096
        fi
        CLUSTERS="" NAMES=""
        if [ "$ARCH_CLASS" = arm ]; then
            ok=1 runs="" last_key="" lo="" hi=""
            set -f
            for line in $(printf '%s\n' "$CPU" | sed -n 's/^CPU=//p' | tr ' ' ':' | sort -t: -k1,1n); do
                num=${line%%:*}; rest=${line#*:}; impl=${rest%%:*}; part=${rest#*:}
                impl=$(hexid "$impl" 2) || { ok=0; break; }
                part=$(hexid "$part" 3) || { ok=0; break; }
                if [ ${#impl} -ne 4 ] || [ ${#part} -ne 5 ]; then ok=0; break; fi
                khz=$(rd "/sys/devices/system/cpu/cpu$num/cpufreq/cpuinfo_max_freq" | head -n1 | tr -d ' \t\r')
                mhz=""
                case "$khz" in
                    ''|*[!0-9]*) ;;
                    *) khz=$(printf '%s' "$khz" | sed 's/^0*//'); khz=${khz:-0}
                       if [ ${#khz} -le 9 ]; then
                           m=$((khz / 1000))
                           [ "$m" -gt 0 ] && [ "$m" -lt 100000 ] && mhz=$m
                       fi ;;
                esac
                key="$impl:$part:$mhz"
                if [ "$key" = "$last_key" ] && [ "$num" -eq $((hi + 1)) ]; then
                    hi=$num
                else
                    [ -n "$last_key" ] && runs="$runs$lo $hi $last_key$NL"
                    last_key=$key lo=$num hi=$num
                fi
            done
            [ -n "$last_key" ] && runs="$runs$lo $hi $last_key$NL"
            set +f
            if [ "$ok" = 1 ] && [ -n "$runs" ]; then
                set -f
                for run in $(printf '%s' "$runs" | tr ' ' '/'); do
                    lo=${run%%/*}; rest=${run#*/}; hi=${rest%%/*}; key=${rest#*/}
                    impl=${key%%:*}; rest=${key#*:}; part=${rest%%:*}; mhz=${rest#*:}
                    if [ "$lo" = "$hi" ]; then cpus=$lo; else cpus="$lo-$hi"; fi
                    entry="$cpus:$impl:$part"
                    [ -n "$mhz" ] && entry="$entry:$mhz"
                    CLUSTERS="${CLUSTERS:+$CLUSTERS,}$entry"
                    if ! name=$(arm_part "$impl:$part"); then name="$impl:$part"; warn unknown_arm_part; fi
                    case "+$NAMES+" in *"+$name+"*) ;; *) NAMES="${NAMES:+$NAMES+}$name" ;; esac
                done
                set +f
                # As the Python probe's list check: entries of the schema's form, no repeats, at most 64 and 4096
                # characters (a five-digit CPU number or a 65th cluster is left out, not the whole file spoilt).
                CLUSTERS=$(printf '%s\n' "$CLUSTERS" | tr ',' '\n' |
                           grep -E '^[0-9]{1,4}(-[0-9]{1,4})?:0x[0-9a-f]{2}:0x[0-9a-f]{3}(:[0-9]{1,5})?$' | awk '
                    !seen[$0]++ {
                        if (n >= 64 || length(out) + (n ? 1 : 0) + length($0) > 4096) exit
                        out = out (n ? "," : "") $0; n++
                    }
                    END { printf "%s", out }')
                if [ -n "$CLUSTERS" ]; then put CPU_CLUSTERS "$CLUSTERS"; else warn "not_determined:CPU_CLUSTERS"; fi
            fi
        fi
        if [ "$ARCH" = arm64 ]; then
            if [ -n "$NAMES" ]; then put_text CPU_MODEL "$NAMES"; else warn "not_determined:CPU_MODEL"; fi
        else
            MODEL=$(cpu_field MODEL)
            if [ -n "$MODEL" ]; then put_text CPU_MODEL "$MODEL"; else warn "not_determined:CPU_MODEL"; fi
        fi
        HAVE_FLAGS=1
        if [ "$ARCH_CLASS" = arm ] && cpu_has FEATURES; then FLAGS=$(cpu_field FEATURES)
        elif [ "$ARCH_CLASS" != arm ] && cpu_has FLAGS; then FLAGS=$(cpu_field FLAGS)
        elif cpu_has FLAGS; then FLAGS=$(cpu_field FLAGS)
        elif cpu_has FEATURES; then FLAGS=$(cpu_field FEATURES)
        else FLAGS="" HAVE_FLAGS=0
        fi
        TOKENS=$(lower "$FLAGS" | tr -s ' \t' '  ')
        set -f
        LIST=$(printf '%s\n' $TOKENS | awk '
            length($0) <= 48 && $0 ~ /^[a-z0-9_.]+$/ && !seen[$0]++ {
                if (n >= 1024 || length(out) + (n ? 1 : 0) + length($0) > 4096) exit
                out = out (n ? "," : "") $0; n++
            }
            END { printf "%s", out }')
        set +f
        if [ -n "$(trim "$TOKENS")" ]; then
            if [ -n "$LIST" ]; then put CPU_FLAGS_ALL "$LIST"; else warn "not_determined:CPU_FLAGS_ALL"; fi
            put CPU_FLAGS_SOURCE proc-cpuinfo
        fi
        features
    fi
else
    warn "not_determined:CPU_FLAGS_ALL"
fi

# --- memory ----------------------------------------------------------------------------------------------------

meminfo_mb() {
    rd /proc/meminfo | awk -v k="$1:" '$1 == k && $2 ~ /^[0-9]+$/ && $3 == "kB" { print int($2 / 1024); exit }'
}
put_int RAM_TOTAL_MB "$(meminfo_mb MemTotal)" 16 16777216
put_int SWAP_TOTAL_MB "$(meminfo_mb SwapTotal)" 0 16777216
put_int RAM_AVAILABLE_MB "$(meminfo_mb MemAvailable)" 0 16777216
DISK_PATH=""
for candidate in "${LLM_FRAMEWORK_HOME:-}" "${HOME:-}/.llm-framework" "${HOME:-}"; do
    if [ -n "$candidate" ] && [ -d "$candidate" ]; then DISK_PATH=$candidate; break; fi
done
if [ -n "$DISK_PATH" ]; then
    v=$(df -Pk "$DISK_PATH" 2>/dev/null | awk 'NR == 2 && $4 ~ /^[0-9]+$/ { print int($4 / 1024) }')
    put_int DISK_FREE_MB "$v" 0 2147483647 quiet
fi

# --- SoC (device tree) -----------------------------------------------------------------------------------------

DT_BASE=""
for base in /proc/device-tree /sys/firmware/devicetree/base; do
    if [ -d "$(f "$base")" ]; then DT_BASE=$base; break; fi
done
dt_prop() {  # the first base with the property
    for base in /proc/device-tree /sys/firmware/devicetree/base; do
        if [ -f "$(f "$base/$1")" ]; then tr '\000' "$2" < "$(f "$base/$1")"; return 0; fi
    done
    return 1
}
LAST=$(dt_prop compatible '\n' | sed '/^[[:space:]]*$/d' | tail -n1)
LAST=$(lower "$(trim "$LAST")")
case "$LAST" in
    *,*) SOC_VENDOR=${LAST%%,*}; SOC_MODEL=${LAST#*,}
         if [ -n "$SOC_MODEL" ]; then
             put SOC_VENDOR "$SOC_VENDOR" "$P_SOC_VENDOR" quiet
             put SOC_MODEL "$SOC_MODEL" "$P_IDENT" quiet
         fi ;;
esac
BOARD=$(dt_prop model ' ')
[ -n "$BOARD" ] && put_text BOARD_MODEL "$BOARD" quiet

# --- NPU and USB-attached boards -------------------------------------------------------------------------------

PCI_IDS=""
for dev in "$(f /sys/bus/pci/devices)"/*; do
    [ -e "$dev/vendor" ] || continue
    vendor=$(hexid "$(rdp "$dev/vendor")" 4) || continue
    device=$(hexid "$(rdp "$dev/device")" 4) || continue
    PCI_IDS="$PCI_IDS $vendor:$device"
done
USB_IDS=""
for dev in "$(f /sys/bus/usb/devices)"/*; do
    [ -e "$dev/idVendor" ] || continue
    vendor=$(hexid "$(rdp "$dev/idVendor")" 4) || continue
    product=$(hexid "$(rdp "$dev/idProduct")" 4) || continue
    USB_IDS="$USB_IDS $vendor:$product"
done
pci_vendor_seen() { case "$PCI_IDS " in *" $1:"*) return 0 ;; esac; return 1; }
id_seen() { case "$2 " in *" $1 "*) return 0 ;; esac; return 1; }

RKNN=OFF HAILO=OFF METIS=OFF MX3=OFF CORAL=OFF INTEL_NPU=OFF
NPU_VENDOR="" NPU_MODEL="" NPU_DEVICE=""
RKNODE=""
if exists /dev/rknpu; then RKNODE=/dev/rknpu
else
    # A DRM node of the vendor rknpu driver or an accel node of the mainline rocket driver (unverified on a board).
    set -f
    for pattern in renderD card; do
        for name in $(ls "$(f /sys/class/drm)" 2>/dev/null | grep "^$pattern" | grep -v -- -); do
            # The vendor driver registers as "RKNPU" (upper case): compared without case.
            if [ "$(lower "$(link_name "$(f /sys/class/drm)/$name/device/driver")")" = rknpu ]; then
                RKNODE=/dev/dri/$name
                break 2
            fi
        done
    done
    set +f
    if [ -z "$RKNODE" ]; then
        for n in "$(f /sys/class/accel)"/accel[0-9]*; do
            if [ "$(link_name "$n/device/driver")" = rocket ]; then RKNODE=/dev/accel/${n##*/}; break; fi
        done
    fi
fi
if [ -n "$RKNODE" ]; then
    RKNN=ON
    NPU_VENDOR=rockchip NPU_DEVICE=$RKNODE
    [ "$(getv SOC_VENDOR)" = rockchip ] && NPU_MODEL=$(getv SOC_MODEL)
    DRV=""
    # Module version, the world-readable procfs node ("RKNPU driver: v0.9.6"), the debugfs node (root only).
    for source in /sys/module/rknpu/version /proc/rknpu/version /sys/kernel/debug/rknpu/version; do
        DRV=$(first_number "$(rd "$source")")
        if [ -n "$DRV" ] && valid "$P_DOTTED" "$DRV"; then break; fi
        DRV=""
    done
    if [ -n "$DRV" ]; then put RKNPU_DRIVER_VERSION "$DRV"; else warn "needs_root:RKNPU_DRIVER_VERSION"; fi
fi
HAILO_NODE="" APEX_NODE="" INTEL_NODE=""
for n in "$(f /dev)"/hailo[0-9]*; do [ -e "$n" ] && { HAILO_NODE=/dev/${n##*/}; break; }; done
for n in "$(f /dev)"/apex_[0-9]*; do [ -e "$n" ] && { APEX_NODE=/dev/${n##*/}; break; }; done
for n in "$(f /sys/class/accel)"/accel[0-9]*; do
    if [ "$(link_name "$n/device/driver")" = intel_vpu ]; then INTEL_NODE=/dev/accel/${n##*/}; break; fi
done
describe() {  # VENDOR MODEL DEVICE: the first NPU found is described
    [ -n "$NPU_VENDOR" ] && return 0
    NPU_VENDOR=$1 NPU_MODEL=$2 NPU_DEVICE=$3
}
if pci_vendor_seen 0x1e60 || [ -n "$HAILO_NODE" ]; then HAILO=ON; describe hailo "" "$HAILO_NODE"; fi
if pci_vendor_seen 0x1f4b; then METIS=ON; describe axelera "" ""; fi
if pci_vendor_seen 0x1d6b; then MX3=ON; describe memryx "" ""; fi
if id_seen 0x1ac1:0x089a "$PCI_IDS" || id_seen 0x1a6e:0x089a "$USB_IDS" || id_seen 0x18d1:0x9302 "$USB_IDS" \
        || [ -n "$APEX_NODE" ]; then
    CORAL=ON; describe google "" "$APEX_NODE"
fi
if [ -n "$INTEL_NODE" ]; then INTEL_NPU=ON; describe intel intel-npu "$INTEL_NODE"; fi
for id in $USB_IDS; do
    case "$id" in 0x2207:*)
        put USB_ROCKCHIP_PID "${id#*:}"
        put USB_ROCKCHIP_MODE "$(usb_rockchip_mode "${id#*:}")"
        break ;;
    esac
done
emit SUPPORTS_RKNN "$RKNN"
emit SUPPORTS_HAILO "$HAILO"
emit SUPPORTS_METIS "$METIS"
emit SUPPORTS_MX3 "$MX3"
emit SUPPORTS_CORAL "$CORAL"
emit SUPPORTS_INTEL_NPU "$INTEL_NPU"
if [ -n "$NPU_VENDOR" ]; then
    put NPU_VENDOR "$NPU_VENDOR"
    put NPU_MODEL "$NPU_MODEL" "$P_IDENT" quiet
    put NPU_DEVICE "$NPU_DEVICE" "$P_NPU_DEVICE" quiet
fi

# --- GPU -------------------------------------------------------------------------------------------------------
# A GPU is one line of 11 fields separated by SEP: VENDOR NAME PCI_ID DISCRETE UNIFIED VRAM_MB VRAM_FREE_MB
# DRIVER_VERSION COMPUTE_CAP GFX_TARGET SOURCE (empty when unknown).

gpu_line() { printf "%s$SEP%s$SEP%s$SEP%s$SEP%s$SEP%s$SEP%s$SEP%s$SEP%s$SEP%s$SEP%s\n" "$@"; }

NVF=driver_version,compute_cap,memory.total,memory.free,pci.bus_id,pci.device_id,name
NVF_OLD=driver_version,memory.total,memory.free,pci.bus_id,pci.device_id,name
NV_GPUS=""
if have nvidia-smi; then
    out=$(tool nvidia-smi --query-gpu=$NVF --format=csv,noheader,nounits)
    rc=$?
    if [ "$rc" -ne 0 ]; then
        err=$(tool_all nvidia-smi --query-gpu=$NVF --format=csv,noheader,nounits)
        case "$err" in
            *compute_cap*|*pci.*)
                # Drivers before R510 do not know compute_cap: ask without it and leave the column empty.
                out=$(tool nvidia-smi --query-gpu=$NVF_OLD --format=csv,noheader,nounits)
                rc=$?
                out=$(printf '%s\n' "$out" | sed 's/,/,,/') ;;
        esac
    fi
    if [ "$rc" -eq 0 ]; then
        RAW=$(printf '%s\n' "$out" | awk -F, '
            function trim(s) { gsub(/^[ \t\r]+|[ \t\r]+$/, "", s); return s }
            NF >= 5 {
                if (NF >= 7) {
                    name = $7; for (i = 8; i <= NF; i++) name = name "," $i
                    name = trim(name); bus = trim($5); dev = trim($6)
                }
                else { name = trim($5); if (NF == 6) name = name "," trim($6); bus = ""; dev = "" }
                driver = trim($1); cap = trim($2); total = trim($3); free = trim($4)
                if (driver == "" || substr(driver, 1, 1) == "[") next
                capout = (cap ~ /^[0-9]+(\.[0-9]+)?$/) ? sprintf("%.1f", cap) : ""
                totout = (total ~ /^[0-9]+(\.[0-9]+)?$/ && total + 0 > 0) ? int(total) : ""
                freeout = (free ~ /^[0-9]+(\.[0-9]+)?$/) ? int(free) : ""
                pci = ""
                if (length(dev) == 10 && dev ~ /^0[xX][0-9a-fA-F]+$/)
                    pci = "0x" tolower(substr(dev, 7, 4)) ":0x" tolower(substr(dev, 3, 4))
                printf "%s\037%s\037%s\037%s\037%s\037%s\n", driver, capout, totout, freeout, pci, name
            }')
        OLDIFS=$IFS; IFS=$NL
        set -f
        for row in $RAW; do
            IFS=$SEP
            set -- $row
            IFS=$NL
            driver=${1:-} cap=${2:-} total=${3:-} free=${4:-} pci=${5:-} name=${6:-}
            valid "$P_VERSION" "$driver" || driver=""
            valid '^[0-9]{1,2}\.[0-9]$' "$cap" || cap=""
            name=$(clean_text "$name"); [ -n "$name" ] || name="NVIDIA GPU"
            int_in "$total" 0 16777216 || total=""
            int_in "$free" 0 16777216 || free=""
            line=$(gpu_line nvidia "$name" "$pci" ON OFF "$total" "$free" "$driver" "$cap" "" nvidia-smi)
            NV_GPUS="$NV_GPUS$line$NL"
        done
        set +f
        IFS=$OLDIFS
    fi
    if [ -n "$NV_GPUS" ]; then
        cuda=$(tool nvidia-smi -q -d COMPUTE | grep -oE 'CUDA Version[[:space:]]*:[[:space:]]*[0-9]{1,2}\.[0-9]{1,3}' |
               head -n1 | grep -oE '[0-9]+\.[0-9]+$')
        put NVIDIA_DRIVER_CUDA_MAX "$cuda" "$P_CUDA" quiet
    fi
fi

VK_RAN=0 VK_GPUS=""
if have vulkaninfo; then
    out=$(tool vulkaninfo --summary)
    if [ $? -eq 0 ]; then
        case "$out" in *Devices*) VK_RAN=1 ;; esac
    fi
    if [ "$VK_RAN" = 1 ]; then
        RAW=$(printf '%s\n' "$out" | awk '
            function trim(s) { gsub(/^[ \t\r]+|[ \t\r]+$/, "", s); return s }
            function flush() {
                if (n) printf "%s\037%s\037%s\037%s\037%s\n", d["vendorID"], d["deviceID"], d["deviceType"],
                    d["deviceName"], d["driverInfo"]
            }
            { line = trim($0) }
            line ~ /^GPU[0-9]+:$/ { flush(); n = 1; split("", d); next }
            n && index(line, " = ") {
                i = index(line, " = "); d[trim(substr(line, 1, i - 1))] = trim(substr(line, i + 3))
            }
            END { flush() }')
        OLDIFS=$IFS; IFS=$NL
        set -f
        for row in $RAW; do
            IFS=$SEP
            set -- $row
            IFS=$NL
            vid=${1:-} did=${2:-} dtype=${3:-} dname=${4:-} dinfo=${5:-}
            case "$dtype" in *_CPU|*_OTHER) continue ;; esac
            [ -n "$dname" ] || continue
            vid=$(hexid "$vid" 4) || vid=""
            did=$(hexid "$did" 4) || did=""
            vendor=other
            [ -n "$vid" ] && vendor=$(pci_vendor "$vid")
            name=$(clean_text "$dname"); [ -n "$name" ] || name=GPU
            case "$dtype" in *DISCRETE_GPU) disc=ON ;; *) disc=OFF ;; esac
            case "$dtype" in *INTEGRATED_GPU) uni=ON ;; *) uni=OFF ;; esac
            pci=""
            if [ -n "$vid" ] && [ -n "$did" ] && [ "$did" != 0x0000 ] && [ ${#vid} -eq 6 ] && [ ${#did} -eq 6 ]; then
                pci="$vid:$did"
            fi
            drv=$(first_number "$dinfo")
            valid "$P_VERSION" "$drv" || drv=""
            VK_GPUS="$VK_GPUS$(gpu_line "$vendor" "$name" "$pci" "$disc" "$uni" "" "" "$drv" "" "" vulkaninfo)$NL"
        done
        set +f
        IFS=$OLDIFS
    fi
fi

# Display-class PCI GPUs of /sys/class/drm, with the amdgpu VRAM and the KFD gfx target.
FS_GPUS=""
KFD=""
for props in "$(f /sys/class/kfd/kfd/topology/nodes)"/*/properties; do
    [ -f "$props" ] || continue
    ver=$(awk '$1 == "gfx_target_version" { print $2; exit }' "$props")
    minor=$(awk '$1 == "drm_render_minor" { print $2; exit }' "$props")
    case "$ver" in ''|*[!0-9]*) continue ;; esac
    case "$minor" in ''|*[!0-9]*) continue ;; esac
    [ ${#ver} -le 9 ] || continue
    [ "$ver" -gt 0 ] || continue
    KFD="$KFD$minor $(printf 'gfx%d%x%x' $((ver / 10000)) $((ver / 100 % 100)) $((ver % 100)))$NL"
done
DRM=$(f /sys/class/drm)
for num in $(ls "$DRM" 2>/dev/null | grep -E '^card[0-9]+$' | sed 's/^card//' | sort -n); do
    card=$DRM/card$num dev=$DRM/card$num/device
    vendor_id=$(hexid "$(rdp "$dev/vendor")" 4) || continue
    [ ${#vendor_id} -eq 6 ] || continue
    device_id=$(hexid "$(rdp "$dev/device")" 4) || device_id=""
    pci_class=$(lower "$(trim "$(rdp "$dev/class")")")
    if [ -n "$pci_class" ]; then case "$pci_class" in 0x03*) ;; *) continue ;; esac; fi
    vendor=$(pci_vendor "$vendor_id")
    pci=""
    [ -n "$device_id" ] && [ ${#device_id} -eq 6 ] && pci="$vendor_id:$device_id"
    vram_mb="" free_mb="" gfx="" src=sysfs
    if [ "$vendor" = amd ]; then
        vram=$(trim "$(rdp "$dev/mem_info_vram_total")")
        case "$vram" in ''|*[!0-9]*) vram=0 ;; esac
        vram=$(printf '%s' "$vram" | sed 's/^0*//'); vram=${vram:-0}
        [ ${#vram} -le 15 ] || vram=0
        used=$(trim "$(rdp "$dev/mem_info_vram_used")")
        case "$used" in ''|*[!0-9]*) used="" ;; *) used=$(printf '%s' "$used" | sed 's/^0*//'); used=${used:-0}
                                                     [ ${#used} -le 15 ] || used="" ;; esac
        apu=0
        case " $AMD_APU_IDS " in *" $device_id "*) apu=1 ;; esac
        [ "$vram" -lt 2147483648 ] && apu=1
        for render in "$dev"/drm/renderD*; do
            [ -e "$render" ] || continue
            m=${render##*/renderD}
            case "$m" in ''|*[!0-9]*) continue ;; esac
            t=$(printf '%s' "$KFD" | awk -v m="$m" '$1 == m { g = $2 } END { print g }')
            [ -n "$t" ] && gfx=$t
        done
        if [ "$apu" = 1 ]; then kind=integrated disc=OFF uni=ON; else kind=dedicated disc=ON uni=OFF; fi
        name="AMD Radeon ${gfx:-${device_id:-card$num}} ($kind)"
        if [ $((vram / 1048576)) -gt 0 ]; then
            vram_mb=$((vram / 1048576))
            [ -n "$used" ] && [ "$used" -le "$vram" ] && free_mb=$(((vram - used) / 1048576))
        fi
        [ -n "$gfx" ] && src=kfd
    elif [ "$vendor" = intel ]; then
        disc=OFF uni=ON
        if [ -n "$device_id" ]; then
            d=$((device_id))
            if { [ "$d" -ge $((0x5690)) ] && [ "$d" -le $((0x56FF)) ]; } ||
               { [ "$d" -ge $((0xE202)) ] && [ "$d" -le $((0xE2FF)) ]; }; then
                disc=ON uni=OFF
            fi
        fi
        name="Intel GPU ${device_id:-card$num}"
    else
        if [ "$vendor" = other ]; then label=PCI; else label=$(printf '%s' "$vendor" | tr 'a-z' 'A-Z'); fi
        name="$label GPU $pci"
        if [ "$vendor" = nvidia ]; then disc=ON uni=OFF; else disc=UNKNOWN uni=UNKNOWN; fi
    fi
    name=$(clean_text "$name")
    FS_GPUS="$FS_GPUS$(gpu_line "$vendor" "$name" "$pci" "$disc" "$uni" "$vram_mb" "$free_mb" "" "" "$gfx" "$src")$NL"
done
if [ -n "$NV_GPUS" ]; then
    FS_GPUS=$(printf '%s' "$FS_GPUS" | awk -F"$SEP" '$1 != "nvidia"')
    [ -n "$FS_GPUS" ] && FS_GPUS="$FS_GPUS$NL"
fi

# Mali GPUs of the device tree: top-level nodes whose compatible names arm,mali-*, status okay.
if [ -n "$DT_BASE" ]; then
    for node in "$(f "$DT_BASE")"/*/compatible; do
        [ -f "$node" ] || continue
        mali=$(tr '\000' '\n' < "$node" | tr 'A-Z' 'a-z' | sed 's/^[ 	]*//; s/[ 	]*$//' | grep '^arm,mali-' | head -n1)
        [ -n "$mali" ] || continue
        status=okay
        [ -f "${node%/compatible}/status" ] && status=$(tr -d '\000\n ' < "${node%/compatible}/status")
        case "$status" in okay|ok) ;; *) continue ;; esac
        kind=${mali#arm,mali-}
        case "$kind" in
            bifrost|valhall|valhall-csf|midgard|utgard) name="Mali ($kind)" ;;
            *) name="Mali-$(printf '%s' "$kind" | tr 'a-z' 'A-Z')" ;;
        esac
        FS_GPUS="$FS_GPUS$(gpu_line arm "$(clean_text "$name")" "" OFF ON "" "" "" "" "" device-tree)$NL"
    done
fi

VK_OTHERS=$VK_GPUS
[ -n "$NV_GPUS" ] && VK_OTHERS=$(printf '%s' "$VK_GPUS" | awk -F"$SEP" '$1 != "nvidia"')
VULKAN=OFF
if [ -z "$VK_GPUS" ]; then
    OTHERS=$FS_GPUS
    # Without vulkaninfo: a Vulkan loader plus a GPU whose driver ships a Vulkan ICD.
    if [ -n "$OTHERS$NV_GPUS" ]; then
        LOADER=0
        if [ "$SYSROOT" = 0 ]; then
            for ldc in /sbin/ldconfig ldconfig; do
                if "$ldc" -p 2>/dev/null </dev/null | grep -q 'libvulkan\.so'; then LOADER=1; break; fi
            done
        else
            for m in "$(f /usr/lib)"/*/libvulkan.so.1 "$(f /usr/lib)"/libvulkan.so.1 "$(f /usr/lib64)"/libvulkan.so.1 \
                     "$(f /lib)"/*/libvulkan.so.1 "$(f /lib)"/libvulkan.so.1 "$(f /lib64)"/libvulkan.so.1; do
                if [ -e "$m" ]; then LOADER=1; break; fi
            done
        fi
        [ "$LOADER" = 1 ] && VULKAN=ON
    fi
else
    VULKAN=ON
    # vulkaninfo's GPUs, enriched with sysfs and the device tree (PCI id first; AMD GPUs without one paired in order
    # when the counts agree); GPUs of a vendor vulkaninfo does not list are added.
    OTHERS=$( { printf '%s\n' "$VK_OTHERS" | sed '/^$/d' | sed "s/^/V$SEP/"
                printf '%s\n' "$FS_GPUS" | sed '/^$/d' | sed "s/^/F$SEP/"; } | awk -F"$SEP" -v OFS="$SEP" '
        $1 == "V" { nv++; for (i = 2; i <= 12; i++) v[nv, i - 1] = $i; next }
        $1 == "F" { nf++; for (i = 2; i <= 12; i++) g[nf, i - 1] = $i; next }
        function enrich(a, b) {
            if (g[b, 6] != "") v[a, 6] = g[b, 6]
            if (g[b, 7] != "") v[a, 7] = g[b, 7]
            if (g[b, 10] != "") v[a, 10] = g[b, 10]
            if (g[b, 3] != "") v[a, 3] = g[b, 3]
        }
        END {
            for (a = 1; a <= nv; a++) {
                if (v[a, 3] == "") continue
                for (b = 1; b <= nf; b++) if (!used[b] && g[b, 3] == v[a, 3]) { enrich(a, b); used[b] = 1; break }
            }
            na = 0; for (a = 1; a <= nv; a++) if (v[a, 1] == "amd" && v[a, 10] == "" && v[a, 6] == "") amdv[++na] = a
            nb = 0; for (b = 1; b <= nf; b++) if (!used[b] && g[b, 1] == "amd") amdf[++nb] = b
            if (na > 0 && na == nb) for (i = 1; i <= na; i++) { enrich(amdv[i], amdf[i]); used[amdf[i]] = 1 }
            for (a = 1; a <= nv; a++) {
                listed[v[a, 1]] = 1
                print v[a,1], v[a,2], v[a,3], v[a,4], v[a,5], v[a,6], v[a,7], v[a,8], v[a,9], v[a,10], v[a,11]
            }
            for (b = 1; b <= nf; b++) if (!used[b] && !(g[b, 1] in listed))
                print g[b,1], g[b,2], g[b,3], g[b,4], g[b,5], g[b,6], g[b,7], g[b,8], g[b,9], g[b,10], g[b,11]
        }')
    [ -n "$OTHERS" ] && OTHERS="$OTHERS$NL"
fi
if [ "$VK_RAN" = 1 ]; then
    put VULKAN_DEVICES "$(printf '%s' "$VK_GPUS" | sed '/^$/d' | wc -l | tr -d ' ')" "$P_INT" quiet
fi

GPUS=$(printf '%s%s' "$NV_GPUS" "$OTHERS" | sed '/^$/d' | head -n16)
GPU_COUNT=0
CUDA=OFF XPU=OFF
OLDIFS=$IFS; IFS=$NL
set -f
for row in $GPUS; do
    IFS=$SEP
    set -- $row
    IFS=$NL
    n=$GPU_COUNT
    vendor=$1
    [ "$vendor" = nvidia ] && CUDA=ON
    [ "$vendor" = intel ] && XPU=ON
    GPU_LINES="${GPU_LINES:-}GPU${n}_VENDOR=$vendor$NL"
    [ -n "${2:-}" ] && GPU_LINES="${GPU_LINES}GPU${n}_NAME=$2$NL"
    [ -n "${3:-}" ] && GPU_LINES="${GPU_LINES}GPU${n}_PCI_ID=$3$NL"
    GPU_LINES="${GPU_LINES}GPU${n}_DISCRETE=${4:-UNKNOWN}${NL}GPU${n}_UNIFIED=${5:-UNKNOWN}$NL"
    [ -n "${6:-}" ] && GPU_LINES="${GPU_LINES}GPU${n}_VRAM_MB=$6$NL"
    [ -n "${7:-}" ] && GPU_LINES="${GPU_LINES}GPU${n}_VRAM_FREE_MB=$7$NL"
    [ -n "${8:-}" ] && GPU_LINES="${GPU_LINES}GPU${n}_DRIVER_VERSION=$8$NL"
    [ -n "${9:-}" ] && [ "$vendor" = nvidia ] && GPU_LINES="${GPU_LINES}GPU${n}_COMPUTE_CAP=$9$NL"
    [ -n "${10:-}" ] && [ "$vendor" = amd ] && GPU_LINES="${GPU_LINES}GPU${n}_GFX_TARGET=${10}$NL"
    [ -n "${11:-}" ] && GPU_LINES="${GPU_LINES}GPU${n}_SOURCE=${11}$NL"
    GPU_COUNT=$((GPU_COUNT + 1))
done
set +f
IFS=$OLDIFS
emit GPU_COUNT "$GPU_COUNT"
OUT="$OUT${GPU_LINES:-}"
emit SUPPORTS_CUDA "$CUDA"
emit SUPPORTS_INTEL_XPU "$XPU"
emit VULKAN "$VULKAN"
if [ "$OS" = linux ]; then
    HIP=OFF
    if [ "$SYSROOT" = 0 ]; then
        for ldc in /sbin/ldconfig ldconfig; do
            if "$ldc" -p 2>/dev/null </dev/null | grep -q 'libamdhip64'; then HIP=ON; break; fi
        done
    fi
    if [ "$HIP" = OFF ]; then
        for m in "$(f /opt)"/rocm*/lib*/libamdhip64.so*; do [ -e "$m" ] && { HIP=ON; break; }; done
    fi
    emit HIP_RUNTIME "$HIP"
    put ROCM_VERSION "$(first_number "$(rd /opt/rocm/.info/version)")" "$P_VERSION" quiet
else
    emit HIP_RUNTIME OFF
fi
emit METAL OFF

# --- toolchain: versions only, never paths ------------------------------------------------------------------------

first_line() { printf '%s\n' "$1" | sed '/^[[:space:]]*$/d' | head -n1; }
compiler_id() {  # a `cc --version` banner -> gcc-12.2.0, clang-18.1.3, appleclang-16.0.0
    b=$1
    v=$(printf '%s\n' "$b" | sed -n 's/.*Apple clang version \([0-9][0-9.]*\).*/\1/p' | head -n1)
    if [ -n "$v" ]; then echo "appleclang-$(first_number "$v")"; return; fi
    v=$(printf '%s\n' "$b" | sed -n 's/.*clang version \([0-9][0-9.]*\).*/\1/p' | head -n1)
    if [ -n "$v" ]; then echo "clang-$(first_number "$v")"; return; fi
    l=$(first_line "$b")
    case "$b" in
        *"Free Software Foundation"*) ;;
        *) printf '%s\n' "$l" | grep -Eq '(^|[^A-Za-z0-9_])(gcc|GCC|g\+\+)([^A-Za-z0-9_]|$)' || return 0 ;;
    esac
    v=$(first_number "${l##*)}")
    [ -n "$v" ] || v=$(first_number "$l")
    [ -n "$v" ] && echo "gcc-$v"
}
if [ "$TOOLCHAIN" = 1 ]; then
    for spec in TOOL_CMAKE:cmake TOOL_NINJA:ninja TOOL_GIT:git TOOL_CCACHE:ccache TOOL_DOCKER:docker TOOL_MAKE:make; do
        key=${spec%%:*} cmd=${spec#*:}
        have "$cmd" || continue
        put "$key" "$(first_number "$(first_line "$(tool_all "$cmd" --version)")")" "$P_VERSION" quiet
    done
    if [ "$OS" = linux ] && have hipconfig; then
        put TOOL_HIPCONFIG "$(first_number "$(first_line "$(tool_all hipconfig --version)")")" "$P_VERSION" quiet
    fi
    if have nvcc; then
        v=$(tool_all nvcc --version | sed -n 's/.*release [0-9]*\.[0-9]*, V\([0-9][0-9.]*\).*/\1/p' | head -n1)
        put TOOL_NVCC "$v" "$P_VERSION" quiet
    fi
    if have glslc; then
        out=$(tool_all glslc --version)
        line=$(printf '%s\n' "$out" | grep shaderc | head -n1)
        [ -n "$line" ] || line=$(first_line "$out")
        put TOOL_GLSLC "$(first_number "$line")" "$P_VERSION" quiet
    fi
    have cc && put TOOL_CC "$(compiler_id "$(tool_all cc --version)")" "$P_TOOLCHAIN" quiet
    have c++ && put TOOL_CXX "$(compiler_id "$(tool_all c++ --version)")" "$P_TOOLCHAIN" quiet
    if have clang; then
        v=$(tool_all clang --version | sed -n 's/.*clang version \([0-9][0-9.]*\).*/\1/p' | head -n1)
        put TOOL_CLANG "$(first_number "$v")" "$P_VERSION" quiet
    fi
    have python3 && put TOOL_PYTHON "$(first_number "$(tool_all python3 --version)")" "$P_VERSION" quiet
fi

# --- the result ------------------------------------------------------------------------------------------------

# Every bool is written: OFF outside its ARCH, else UNKNOWN when nothing was learned (no /proc/cpuinfo).
if [ -z "${HAVE_FLAGS+x}" ]; then HAVE_FLAGS=0; TOKENS=""; features; fi
[ -n "$WARNINGS" ] && emit PROBE_WARNINGS "$WARNINGS"

MISSING=""
for key in KERNEL ARCH CPU_CORES_LOGICAL RAM_TOTAL_MB; do written "$key" || MISSING="$MISSING $key"; done
CONTENT="# LLM Cross-Compiler Framework hardware profile (schema 2, hardware_probe.sh $PROBE_VERSION)$NL$OUT"
if [ "$OUTPUT_FILE" = "-" ]; then
    printf '%s' "$CONTENT"
else
    if ! printf '%s' "$CONTENT" > "$OUTPUT_FILE"; then log "Cannot write $OUTPUT_FILE."; exit 1; fi
    log "Wrote $OUTPUT_FILE."
fi
if [ -n "$MISSING" ]; then log "Could not determine required keys:$MISSING"; exit 1; fi
exit 0
