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
# POSIX sh (dash, busybox ash, bash), LC_ALL=C. No network, no stdin (tools get /dev/null), no sudo prompt: sudo is
# only asked `sudo -k -n true` (the access facts; -n never prompts or reads a password, -k ignores cached
# credentials). No host name,
# user name, path, serial number or MAC address is read into the result; free text is printable ASCII.
# HW_PROBE_SYSROOT (for tests): a folder that stands in for / when files are read; the kernel release and machine
# then come from <sysroot>/proc/sys/kernel/osrelease and .../arch, and the host's getconf/ldconfig are not asked.

set -u
LC_ALL=C
export LC_ALL

PROBE_VERSION=3.3.0
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
P_NPU_DEVICE='^/dev/(rknpu|dri/(renderD|card)[0-9]{1,3}|accel/accel[0-9]{1,3}|hailo[0-9]{1,3}|h1x(-[0-9]{1,3})?|apex_[0-9]{1,3})$'
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
case "$ARCH" in
    x86_64|x86) ARCH_CLASS=x86 ;;
    arm64|armv7|armv6) ARCH_CLASS=arm ;;
    riscv64) ARCH_CLASS=riscv ;;
    loongarch64) ARCH_CLASS=loongarch ;;
    ppc64le) ARCH_CLASS=power ;;
    s390x) ARCH_CLASS=s390x ;;
    *) ARCH_CLASS=other ;;
esac

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
    feature SUPPORTS_RVV riscv all v
    feature SUPPORTS_RV_ZFH riscv all zfh
    feature SUPPORTS_RV_ZVFH riscv all zvfh
    feature SUPPORTS_RV_ZICBOP riscv all zicbop
    feature SUPPORTS_RV_ZIHINTPAUSE riscv all zihintpause
    feature SUPPORTS_RV_ZVFBFWMA riscv all zvfbfwma
    feature SUPPORTS_RV_ZBA riscv all zba
    feature SUPPORTS_XTHEADVECTOR riscv all xtheadvector
    feature SUPPORTS_LSX loongarch all lsx
    feature SUPPORTS_LASX loongarch all lasx
    feature SUPPORTS_VSX power all vsx
    feature SUPPORTS_VXE s390x all vxe
    feature SUPPORTS_VXE2 s390x all vxe2
    feature SUPPORTS_NNPA s390x all nnpa
}

riscv_tokens() {  # ISA string -> its extensions, space-separated (g = imafd with zicsr and zifencei), as the .py
    printf '%s\n' "$(lower "$(trim "$1")")" | grep -E '^rv(32|64)' |
        sed -n 's/^rv[0-9][0-9]\([a-z]*\)\(\(_[a-z0-9][a-z0-9]*\)*\).*/\1|\2/p' | awk -F'|' '{
            base = $1; rest = $2; n = 0
            for (i = 1; i <= length(base); i++) {
                c = substr(base, i, 1)
                if (c == "g") { split("i m a f d zicsr zifencei", g, " "); for (j = 1; j <= 7; j++) out[++n] = g[j] }
                else out[++n] = c
            }
            m = split(rest, parts, "_")
            for (j = 1; j <= m; j++) if (parts[j] != "") out[++n] = parts[j]
            s = ""
            for (j = 1; j <= n; j++) if (!(out[j] in seen)) { seen[out[j]] = 1; s = s (s == "" ? "" : " ") out[j] }
            printf "%s", s
        }'
}

# Linux AT_HWCAP (1) / AT_HWCAP2 (2) bits -> the /proc/cpuinfo token they stand for (the schema's tables.hwcap_bits).
hwcap_bits() {
    case "$1" in
        arm64) echo "asimd:1:1 fphp:1:9 asimdhp:1:10 asimddp:1:20 sve:1:22 sve2:2:1 i8mm:2:13 bf16:2:14 sme:2:23" ;;
        ppc64le) echo "vsx:1:7 altivec:1:28" ;;
    esac
}

hwcaps() {  # "HWCAP HWCAP2" of /proc/self/auxv (the sysroot's in tests; od's own, the same machine, otherwise)
    have od || return 1
    words=$(od -An -v -t u8 "$(f /proc/self/auxv)" 2>/dev/null) || return 1
    # shellcheck disable=SC2086
    printf '%s\n' $words | awk '
        NR % 2 == 1 { k = $1; next }
        { if (k == 0) exit; if (!(k in v)) v[k] = $1 }
        END { if (!(16 in v)) exit 1; printf "%s %s", v[16], ((26 in v) ? v[26] : 0) }'
}

hwcap_tokens() {  # ARCH HWCAP HWCAP2: the tokens of the set bits, in table order
    out=""
    for row in $(hwcap_bits "$1"); do
        token=${row%%:*}; rest=${row#*:}; word=${rest%%:*}; bit=${rest#*:}
        if [ "$word" = 1 ]; then cap=$2; else cap=$3; fi
        [ $(( (cap >> bit) & 1 )) = 1 ] && out="$out $token"
    done
    printf '%s' "$out"
}

power_cpu() {  # POWER_CPU_TYPE, altivec and vsx from the cpu line (POWER7 and later have VSX), as the .py
    line=$(cpu_field PCPU)
    n=$(printf '%s\n' "$line" | grep -oiE '(^|[^A-Za-z0-9_])POWER[[:space:]]*[0-9]{1,2}([^A-Za-z0-9_]|$)' | head -n1 |
        tr -cd '0-9')
    power=""
    case "power$n" in
        power7|power8|power9|power10|power11) power="power$n"; put POWER_CPU_TYPE "$power" ;;
        *) warn "not_determined:POWER_CPU_TYPE" ;;
    esac
    case "$(lower "$line")" in *"altivec supported"*) TOKENS="$TOKENS altivec" ;; esac
    if [ -n "$power" ]; then
        case " $TOKENS " in *" vsx "*) ;; *) TOKENS="$TOKENS vsx" ;; esac
    fi
    HAVE_FLAGS=1
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
            # x86 "model name", LoongArch "Model Name", RISC-V "uarch", POWER "cpu" (the first one that is set)
            m = ""; split("model name|Model Name|uarch|cpu", mk, "|")
            for (j = 1; j <= 4 && m == ""; j++) if ((1 SUBSEP mk[j]) in f) m = f[1 SUBSEP mk[j]]
            if (m != "") print "MODEL=" m
            if ((1 SUBSEP "cpu") in f) print "PCPU=" f[1 SUBSEP "cpu"]
            if (((1 SUBSEP "isa") in f) && f[1 SUBSEP "isa"] != "") print "ISA=" f[1 SUBSEP "isa"]
            else if ((1 SUBSEP "hart isa") in f) print "ISA=" f[1 SUBSEP "hart isa"]
            else if ((1 SUBSEP "isa") in f) print "ISA="
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
        if [ "$ARCH" = riscv64 ] && [ -z "$(trim "$TOKENS")" ] && cpu_has ISA; then
            # the extensions every hart has ("isa"); a kernel without that line names each hart's ("hart isa")
            TOKENS=$(riscv_tokens "$(cpu_field ISA)") HAVE_FLAGS=1
        fi
        set -f
        # shellcheck disable=SC2086
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
        case "$ARCH" in
            arm64|ppc64le)
                if CAPS=$(hwcaps); then
                    # shellcheck disable=SC2086
                    TOKENS="$TOKENS $(hwcap_tokens "$ARCH" $CAPS)" HAVE_FLAGS=1
                fi ;;
        esac
        if [ "$ARCH" = ppc64le ] && cpu_has PCPU; then power_cpu; fi
        features
    elif [ "$ARCH" = s390x ]; then
        # s390x has no "processor : N" blocks: "# processors" and the "features" line of the whole file (the
        # "processor N:" lines with their identification numbers are never read).
        S390=$(awk '
            function trim(s) { gsub(/^[ \t\r]+|[ \t\r]+$/, "", s); return s }
            {
                i = index($0, ":"); if (i == 0) next
                key = trim(substr($0, 1, i - 1))
                if ((key == "# processors" || key == "features") && !(key in f)) f[key] = trim(substr($0, i + 1))
            }
            END {
                if ("# processors" in f) print "COUNT=" f["# processors"]
                if ("features" in f) print "FEATURES=" f["features"]
            }' "$CPUINFO" 2>/dev/null)
        s390_field() { printf '%s\n' "$S390" | sed -n "s/^$1=//p" | head -n1; }
        put_int CPU_CORES_LOGICAL "$(s390_field COUNT)" 1 4096
        HAVE_FLAGS=0 TOKENS=""
        if printf '%s\n' "$S390" | grep -q '^FEATURES='; then
            HAVE_FLAGS=1 TOKENS=$(lower "$(s390_field FEATURES)" | tr -s ' \t' '  ')
        fi
        set -f
        # shellcheck disable=SC2086
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

# The largest level-2 unified or data cache of /sys/devices/system/cpu/cpu<N>/cache/index<M> (the kernel writes KiB,
# "2048K"); a level-2 cache whose size cannot be read is a warning, a CPU without one is not.
L2_KB=0 L2_LISTED=0
for idx in "$(f /sys/devices/system/cpu)"/cpu[0-9]*/cache/index[0-9]*; do
    [ -d "$idx" ] || continue
    [ "$(trim "$(rdp "$idx/level")")" = 2 ] || continue
    case "$(trim "$(rdp "$idx/type")")" in Unified|Data) ;; *) continue ;; esac
    L2_LISTED=1
    size=$(trim "$(rdp "$idx/size")")
    case "$size" in *K) n=${size%K} mult=1 ;; *M) n=${size%M} mult=1024 ;; *) continue ;; esac
    case "$n" in ''|*[!0-9]*) continue ;; esac
    [ ${#n} -le 9 ] || continue
    n=$(printf '%s' "$n" | sed 's/^0*//')
    n=$(( ${n:-0} * mult ))
    [ "$n" -gt "$L2_KB" ] && L2_KB=$n
done
if [ "$L2_KB" -gt 0 ]; then put_int CPU_L2_CACHE_KB "$L2_KB" 1 1048576
elif [ "$L2_LISTED" = 1 ]; then warn "not_determined:CPU_L2_CACHE_KB"
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

# NVIDIA Jetson (decision O-PREP; read only when NVIDIA's files are there): the L4T release of /etc/nv_tegra_release
# ("# R35 (release), REVISION: 4.1, ..." -> 35.4.1) and the JetPack version of the installed package nvidia-jetpack.
if [ "$OS" = linux ] && [ "$ARCH" = arm64 ]; then
    if [ -f "$(f /etc/nv_tegra_release)" ]; then
        v=$(sed '/^[[:space:]]*$/d' "$(f /etc/nv_tegra_release)" 2>/dev/null | head -n1 |
            sed -n 's/^#[[:space:]]*R\([0-9][0-9]*\)[[:space:]]*(release),[[:space:]]*REVISION:[[:space:]]*\([0-9][0-9]*\.[0-9][0-9]*\).*/\1.\2/p')
        if [ -n "$v" ]; then put JETSON_L4T_VERSION "$v" '^[0-9]{2}\.[0-9]{1,2}\.[0-9]{1,2}$'
        else warn "not_determined:JETSON_L4T_VERSION"
        fi
    fi
    if [ -f "$(f /var/lib/dpkg/status)" ]; then
        v=$(awk '
            function check() {
                if (pkg == "nvidia-jetpack" && st == "install ok installed" && !done) { print ver; done = 1 }
                pkg = ""; st = ""; ver = ""; hp = 0; hs = 0; hv = 0
            }
            { sub(/\r$/, "") }   # the CR of a CRLF line end, as the Python probe reads lines
            /^$/ { check(); next }
            /^Package: / && !hp { pkg = substr($0, 10); hp = 1 }
            /^Status: / && !hs { st = substr($0, 9); hs = 1 }
            /^Version: / && !hv { ver = substr($0, 10); hv = 1 }
            END { check() }' "$(f /var/lib/dpkg/status)" 2>/dev/null | sed -n 's/^\([0-9][0-9.]*\).*/\1/p' | head -n1)
        put JETSON_JETPACK_VERSION "$v" '^[0-9]{1,2}\.[0-9]{1,2}(\.[0-9]{1,2})?$' quiet
    fi
fi

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
    for pattern in renderD card; do
        for node in "$(f /sys/class/drm)/$pattern"*; do
            [ -e "$node" ] || [ -L "$node" ] || continue
            name=${node##*/}
            case "$name" in *-*) continue ;; esac   # a connector (card0-eDP-1), not a node
            # The vendor driver registers as "RKNPU" (upper case): compared without case.
            if [ "$(lower "$(link_name "$node/device/driver")")" = rknpu ]; then
                RKNODE=/dev/dri/$name
                break 2
            fi
        done
    done
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
APEX_NODE="" INTEL_NODE=""
for n in "$(f /dev)"/apex_[0-9]*; do [ -e "$n" ] && { APEX_NODE=/dev/${n##*/}; break; }; done
for n in "$(f /sys/class/accel)"/accel[0-9]*; do
    if [ "$(link_name "$n/device/driver")" = intel_vpu ]; then INTEL_NODE=/dev/accel/${n##*/}; break; fi
done
[ "$OS" = linux ] && put INTEL_NPU_DEVICE "$INTEL_NODE" '^/dev/accel/accel[0-9]{1,3}$' quiet
# --- Hailo (HAILO_PLAN.md section 2; the same read order and rules as hardware_probe.py) -------------------------
# Files first (no tool): /sys/module/<driver> (never lsmod), the sysfs classes, /dev nodes, PCI and USB ids. Then
# hailortcli with fixed arguments and an empty stdin: -v, scan, fw-control identify -s <id> for up to 16 devices (never
# --extended); of identify only the architecture and firmware lines are read. Device ids stay in this script.

hailo_arch_name() {  # identify's "Device Architecture" word -> HAILO_ARCH (tables.hailo_arch_names)
    case "$1" in
        HAILO8) echo hailo8 ;;
        HAILO8_B0) echo hailo8 ;;
        HAILO8L) echo hailo8l ;;
        HAILO10H) echo hailo10h ;;
        HAILO15H) echo hailo15h ;;
        HAILO15M) echo hailo15m ;;
        HAILO15L) echo hailo15l ;;
        HAILO12L) echo hailo12l ;;
        MARS) echo hailo12l ;;
        *) return 1 ;;
    esac
}
hailo_arch_family() {  # HAILO_ARCH -> HAILO_FAMILY (tables.hailo_arch_family)
    case "$1" in
        hailo8) echo hailo8 ;;
        hailo8l) echo hailo8 ;;
        hailo10h) echo hailo1x ;;
        hailo15h) echo hailo1x ;;
        hailo15m) echo hailo1x ;;
        hailo15l) echo hailo1x ;;
        hailo12l) echo hailo1x ;;
        *) return 1 ;;
    esac
}
hailo_driver_family() {  # kernel module -> HAILO_FAMILY (tables.hailo_drivers)
    case "$1" in
        hailo_pci) echo hailo8 ;;
        hailo1x_pci) echo hailo1x ;;
        hailo_integrated_nnc) echo hailo1x ;;
        *) return 1 ;;
    esac
}
hailo_class_family() {  # sysfs class -> HAILO_FAMILY (tables.hailo_sysfs_classes)
    case "$1" in
        hailo_chardev) echo hailo8 ;;
        hailo1x) echo hailo1x ;;
        hailo1x_integrated) echo hailo1x ;;
        *) return 1 ;;
    esac
}
hailo_pci_family() {  # PCI vendor:device -> HAILO_FAMILY (tables.hailo_pci_devices)
    case "$1" in
        0x1e60:0x2864) echo hailo8 ;;
        0x1e60:0x45c4) echo hailo1x ;;
        0x1e60:0x43a2) echo hailo1x ;;
        0x1e60:0x26a2) echo hailo1x ;;
        *) return 1 ;;
    esac
}
hailo_usb_arch() {  # USB vendor:product -> HAILO_ARCH (tables.hailo_usb_devices)
    case "$1" in
        0x0b05:0x1d6f) echo hailo10h ;;
        *) return 1 ;;
    esac
}
hailo_interface() {  # the form of a hailortcli device id -> pcie, usb or integrated
    case "$1" in
        '[integrated]') echo integrated ;;
        usb/*) echo usb ;;
        *) echo pcie ;;
    esac
}
count_distinct() { printf '%s\n' "$@" | sed '/^$/d' | sort -u | grep -c . ; }
first_word() { printf '%s\n' "$@" | sed '/^$/d' | head -n1; }

H_MODULE="" H_DRV_VER="" H_FAMS="" H_ENTRIES=0 H_NODE="" H_NODES=0 H_PCI=0 H_USB=0 H_USB_ARCH="" H_INTEGRATED=0
for m in hailo_pci hailo1x_pci hailo_integrated_nnc; do
    if [ "$OS" = linux ] && exists "/sys/module/$m"; then
        H_MODULE=$m
        H_FAMS="$H_FAMS $(hailo_driver_family "$m")"
        v=$(first_number "$(rd "/sys/module/$m/version")")
        if [ -n "$v" ] && valid "$P_DOTTED" "$v"; then H_DRV_VER=$v; fi
        break
    fi
done
for cls in hailo_chardev hailo1x hailo1x_integrated; do
    for e in "$(f /sys/class)/$cls"/*; do
        [ -e "$e" ] || [ -L "$e" ] || continue
        H_ENTRIES=$((H_ENTRIES + 1))
        [ -n "$H_NODE" ] || H_NODE=/dev/${e##*/}
        H_FAMS="$H_FAMS $(hailo_class_family "$cls")"
        [ "$cls" = hailo1x_integrated ] && H_INTEGRATED=1
        if [ "$cls" = hailo_chardev ]; then   # the hailo8 driver's device_id reads 1e60:2864
            did=$(trim "$(rdp "$e/device_id")")
            case "$did" in
                *:*) if v=$(hexid "${did%%:*}" 4) && d=$(hexid "${did#*:}" 4) && fam=$(hailo_pci_family "$v:$d"); then
                         H_FAMS="$H_FAMS $fam"
                     fi ;;
            esac
        fi
    done
done
for n in "$(f /dev)"/hailo[0-9]*; do
    [ -e "$n" ] || [ -L "$n" ] || continue
    H_NODES=$((H_NODES + 1))
    [ -n "$H_NODE" ] || [ "$H_ENTRIES" -gt 0 ] || H_NODE=/dev/${n##*/}
done
for id in $PCI_IDS; do
    case "$id" in 0x1e60:*)
        H_PCI=$((H_PCI + 1))
        if fam=$(hailo_pci_family "$id"); then H_FAMS="$H_FAMS $fam"; fi ;;
    esac
done
for id in $USB_IDS; do
    if arch=$(hailo_usb_arch "$id"); then
        H_USB=$((H_USB + 1)) H_USB_ARCH=$arch
        H_FAMS="$H_FAMS $(hailo_arch_family "$arch")"
    fi
done

# Step B: hailortcli. A call that does not answer (exit 124 of timeout, 126/127: cannot run) ends the Hailo tools,
# as a missing tool does in hardware_probe.py; no device is asked once the Hailo part has run for 60 s
# (HAILO_TOOL_SECONDS there). The clock: date +%s where it gives a number (%s is not POSIX: some busybox builds and
# other date tools lack it), else the whole seconds since boot from /proc/uptime (every Linux, busybox too); with
# neither, the 16-device cap alone applies. One clock for the start and every check.
H_INSTALLED=0 H_WARN="" H_VERSION="" H_SCAN=0 H_IDS="" H_RECORDS=""
H_CLOCK=""
hailo_seconds() {
    case "$H_CLOCK" in
        date) s=$(date +%s 2>/dev/null) ;;
        uptime) s=$(rd /proc/uptime | sed -n '1s/^\([0-9][0-9]*\).*/\1/p') ;;
        *) s="" ;;
    esac
    case "$s" in ''|*[!0-9]*) echo "" ;; *) echo "$s" ;; esac
}
H_CLOCK='date'
H_START=$(hailo_seconds)
if [ -z "$H_START" ]; then H_CLOCK=uptime; H_START=$(hailo_seconds); fi
if have hailortcli; then
    out=$($TIMEOUT hailortcli -v </dev/null 2>&1)
    rc=$?
    case "$rc" in 124|125|126|127) ;; *) H_INSTALLED=1 ;; esac
fi
if [ "$H_INSTALLED" = 1 ]; then
    ver=$(printf '%s\n' "$out" | sed -n 's/.*HailoRT-CLI version \([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' |
          head -n1)
    if printf '%s\n' "$out" | grep -q 'libhailort version ([0-9.][0-9.]*) does not match HailoRT-CLI version ([0-9.][0-9.]*)'; then
        H_WARN=version_mismatch
    elif [ "$rc" -ne 0 ]; then
        if [ -n "$ver" ]; then H_WARN=not_determined; else H_WARN=version_mismatch; fi
    elif [ -n "$ver" ] && valid "$P_DOTTED" "$ver"; then
        H_VERSION=$ver
    else
        H_WARN=not_determined
    fi
    if [ "$H_WARN" != version_mismatch ] && out=$(tool hailortcli scan); then
        raw=$(printf '%s\n' "$out" | sed -n 's/^[[:space:]]*\[-\][[:space:]]*Device:[[:space:]]*\([^[:space:]][^[:space:]]*\)[[:space:]]*$/\1/p')
        case "$out" in *"Hailo devices not found"*) H_SCAN=1 ;; esac
        [ -n "$raw" ] && H_SCAN=1
        H_IDS=$(printf '%s\n' "$raw" |
                grep -E '^(\[integrated\]|usb/[0-9]{3}:[0-9]{3}|(pci/)?([0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])$' |
                awk '!seen[$0]++')
    fi
    OLDIFS=$IFS; IFS=$NL
    set -f
    asked=0
    for id in $H_IDS; do
        IFS=$OLDIFS   # the ids are split on line breaks; the commands below need the usual IFS ($TIMEOUT)
        [ "$asked" -lt 16 ] || break
        now=$(hailo_seconds)
        if [ -n "$H_START" ] && [ -n "$now" ] && [ $((now - H_START)) -ge 60 ]; then break; fi
        asked=$((asked + 1))
        # NUL bytes removed (as hailo-apps does); the exit code travels behind the output.
        out=$( { $TIMEOUT hailortcli fw-control identify -s "$id" </dev/null 2>/dev/null; echo "rc=$?"; } | tr -d '\000')
        rc=${out##*rc=}
        out=${out%rc=*}
        case "$rc" in 124|125|126|127) break ;; 0) ;; *) continue ;; esac
        word=$(printf '%s\n' "$out" |
               sed -n 's/^[[:space:]]*Device Architecture[[:space:]]*:[[:space:]]*\([A-Za-z0-9_][A-Za-z0-9_]*\)[[:space:]]*$/\1/p' |
               head -n1 | tr 'a-z' 'A-Z')
        [ -n "$word" ] || continue
        fw=$(printf '%s\n' "$out" | sed -n 's/^[[:space:]]*Firmware Version[[:space:]]*:[[:space:]]*\([0-9][0-9.]*\).*/\1/p' |
             head -n1)
        valid "$P_DOTTED" "$fw" || fw=""
        H_RECORDS="$H_RECORDS$id$SEP$word$SEP$fw$NL"
    done
    set +f
    IFS=$OLDIFS
fi

H_PRESENT=0
if [ "$H_PCI" -gt 0 ] || [ "$H_USB" -gt 0 ] || [ "$H_ENTRIES" -gt 0 ] || [ "$H_NODES" -gt 0 ] || [ -n "$H_IDS" ]; then
    H_PRESENT=1
fi
[ -n "$H_VERSION" ] && put HAILORT_VERSION "$H_VERSION" "$P_DOTTED"
[ -n "$H_WARN" ] && warn "$H_WARN:HAILORT_VERSION"
H_ARCHS="" H_UNKNOWN=0 H_FWS="" H_ARCH=""
OLDIFS=$IFS; IFS=$NL
set -f
for rec in $H_RECORDS; do
    id=${rec%%"$SEP"*} rest=${rec#*"$SEP"}
    word=${rest%%"$SEP"*} fw=${rest#*"$SEP"}
    if ! arch=$(hailo_arch_name "$word"); then H_UNKNOWN=1; continue; fi
    if [ "$arch" = hailo15h ] && [ "$(hailo_interface "$id")" != integrated ]; then arch=hailo10h; fi
    H_ARCHS="$H_ARCHS$arch$NL"
    [ -n "$fw" ] && H_FWS="$H_FWS$fw$NL"
done
set +f
IFS=$OLDIFS
if [ "$(count_distinct "$H_ARCHS")" -gt 1 ] || [ "$H_UNKNOWN" = 1 ]; then warn "not_determined:HAILO_ARCH"
elif [ -n "$H_ARCHS" ]; then H_ARCH=$(first_word "$H_ARCHS")
elif [ -n "$H_USB_ARCH" ] && [ "$H_PCI" = 0 ] && [ -z "$H_RECORDS" ]; then H_ARCH=$H_USB_ARCH
fi
[ -n "$H_ARCH" ] && H_FAMS="$(hailo_arch_family "$H_ARCH") $H_FAMS"
set -f
# shellcheck disable=SC2086 # the families are single words
H_NFAM=$(count_distinct $H_FAMS)
# shellcheck disable=SC2086
H_FAMILY=$(first_word $H_FAMS)
set +f
H_CONFLICT=0
if [ "$H_NFAM" -gt 1 ]; then H_CONFLICT=1 H_FAMILY="" H_ARCH=""; warn "not_determined:HAILO_FAMILY"; fi
if [ "$H_PRESENT" = 1 ] && [ -z "$H_ARCH" ] && [ "$H_CONFLICT" = 0 ] && [ "$H_WARN" != version_mismatch ]; then
    if [ "$H_INSTALLED" = 1 ] && [ -n "$H_NODE" ] && { [ -e "$(f "$H_NODE")" ] || [ -L "$(f "$H_NODE")" ]; } &&
       ! { [ -r "$(f "$H_NODE")" ] && [ -w "$(f "$H_NODE")" ]; }; then
        warn "needs_root:HAILO_ARCH"
    else
        warn "not_determined:HAILO_ARCH"
    fi
fi
if [ "$(count_distinct "$H_FWS")" = 1 ]; then put HAILO_FW_VERSION "$(first_word "$H_FWS")" "$P_DOTTED"
elif [ "$(count_distinct "$H_FWS")" -gt 1 ]; then warn "not_determined:HAILO_FW_VERSION"
elif [ "$H_PRESENT" = 1 ] && { [ -n "$H_MODULE" ] || [ "$H_ENTRIES" -gt 0 ]; } && [ "$H_INSTALLED" = 0 ]; then
    warn "not_determined:HAILO_FW_VERSION"
fi
H_IFACES=""
if [ -n "$H_IDS" ]; then
    OLDIFS=$IFS; IFS=$NL
    set -f
    for id in $H_IDS; do H_IFACES="$H_IFACES$(hailo_interface "$id")$NL"; done
    set +f
    IFS=$OLDIFS
else
    [ "$H_PCI" -gt 0 ] && H_IFACES="${H_IFACES}pcie$NL"
    [ "$H_USB" -gt 0 ] && H_IFACES="${H_IFACES}usb$NL"
    [ "$H_INTEGRATED" = 1 ] && H_IFACES="${H_IFACES}integrated$NL"
fi
if [ "$(count_distinct "$H_IFACES")" = 1 ]; then put HAILO_INTERFACE "$(first_word "$H_IFACES")"
elif [ "$(count_distinct "$H_IFACES")" -gt 1 ]; then warn "not_determined:HAILO_INTERFACE"
fi
H_COUNT=""
if [ -n "$H_IDS" ]; then H_COUNT=$(printf '%s\n' "$H_IDS" | grep -c .)
elif [ "$H_PRESENT" = 1 ]; then
    if [ "$H_ENTRIES" -gt 0 ]; then H_COUNT=$H_ENTRIES
    elif [ "$H_NODES" -gt 0 ]; then H_COUNT=$H_NODES
    else H_COUNT=$((H_PCI + H_USB))
    fi
elif [ "$H_SCAN" = 1 ]; then H_COUNT=0
fi
if [ -n "$H_COUNT" ]; then put_int HAILO_DEVICES "$H_COUNT" 0 64
elif [ "$H_INSTALLED" = 1 ] && [ "$H_WARN" != version_mismatch ]; then warn "not_determined:HAILO_DEVICES"
fi
put HAILO_FAMILY "$H_FAMILY"
put HAILO_ARCH "$H_ARCH"
if [ -n "$H_MODULE" ]; then
    put HAILO_DRIVER "$H_MODULE"
    if [ -n "$H_DRV_VER" ]; then put HAILO_DRIVER_VERSION "$H_DRV_VER"; else warn "not_determined:HAILO_DRIVER_VERSION"; fi
fi
if [ "$OS" = linux ]; then
    for m in "$(f /usr/lib/python3/dist-packages)"/hailort-*.dist-info/METADATA \
             "$(f /usr/local/lib)"/python3*/dist-packages/hailort-*.dist-info/METADATA; do
        [ -f "$m" ] || continue
        v=$(trim "$(sed -n 's/^Version:[ 	]*//p' "$m" | head -n1 | tr -d '\r')")
        if valid "$P_DOTTED" "$v"; then put HAILO_PYHAILORT_VERSION "$v"; break; fi
    done
fi
H_MODEL=""
[ -n "$H_ARCH" ] && H_MODEL="hailo-${H_ARCH#hailo}"

describe() {  # VENDOR MODEL DEVICE: the first NPU found is described
    [ -n "$NPU_VENDOR" ] && return 0
    NPU_VENDOR=$1 NPU_MODEL=$2 NPU_DEVICE=$3
}
if [ "$H_PRESENT" = 1 ]; then HAILO=ON; describe hailo "$H_MODEL" "$H_NODE"; fi
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

VK_RAN=0 VK_GPUS="" VK_INSTANCE=""
if have vulkaninfo; then
    if out=$(tool vulkaninfo --summary); then
        case "$out" in *Devices*) VK_RAN=1 ;; esac
        VK_INSTANCE=$(printf '%s\n' "$out" |
                      sed -n 's/^[[:space:]]*Vulkan Instance Version:[[:space:]]*\([0-9][0-9.]*\).*/\1/p' | head -n1)
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

# The instance version: vulkaninfo's line, else the version in the loader's file name (libvulkan.so.1.Y.Z), the first
# file in the order of the loader folders.
if [ -z "$VK_INSTANCE" ]; then
    for m in "$(f /usr/lib)"/*/libvulkan.so.1.* "$(f /usr/lib)"/libvulkan.so.1.* "$(f /usr/lib64)"/libvulkan.so.1.* \
             "$(f /lib)"/*/libvulkan.so.1.* "$(f /lib)"/libvulkan.so.1.* "$(f /lib64)"/libvulkan.so.1.*; do
        [ -e "$m" ] || [ -L "$m" ] || continue   # a link counts by its name, as in hardware_probe.py
        v=${m##*/libvulkan.so.}
        if valid '^1\.[0-9]+\.[0-9]+$' "$v"; then VK_INSTANCE=$v; break; fi
    done
fi
put VULKAN_INSTANCE_VERSION "$VK_INSTANCE" '^[0-9]{1,2}\.[0-9]{1,3}\.[0-9]{1,5}$' quiet

# OpenCL platforms of clinfo: each "Platform Version" line (OpenCL <major>.<minor> ...) with the "Platform Name" line
# before it, as <name>:<version> (the name lower case, (r)/(tm) removed, other characters one "-", at most 48).
opencl_name() {
    printf '%s' "$1" | tr -c ' -~' ' ' | tr 'A-Z' 'a-z' | sed 's/(r)//g; s/(tm)//g' | tr -c 'a-z0-9' '-' |
        tr -s '-' | sed 's/^-//; s/-$//' | cut -c1-48 | sed 's/-$//'
}
OPENCL=""
if have clinfo && out=$(tool clinfo); then
    RAW=$(printf '%s\n' "$out" | awk '
        { line = $0; gsub(/^[ \t\r]+|[ \t\r]+$/, "", line) }
        line == "Platform Name" || line ~ /^Platform Name[ \t]/ {
            name = substr(line, 14); gsub(/^[ \t\r]+|[ \t\r]+$/, "", name); next
        }
        line ~ /^Platform Version[ \t]+OpenCL [0-9]+\.[0-9]+/ && name != "" {
            v = line; sub(/^Platform Version[ \t]+OpenCL /, "", v); match(v, /^[0-9]+\.[0-9]+/)
            printf "%s\037%s\n", name, substr(v, 1, RLENGTH)
        }')
    OLDIFS=$IFS; IFS=$NL
    set -f
    for row in $RAW; do
        name=${row%%"$SEP"*} version=${row#*"$SEP"}
        item="$(opencl_name "$name"):$version"
        valid '^[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?:[0-9]{1,2}\.[0-9]{1,2}$' "$item" || continue
        case ",$OPENCL," in *",$item,"*) continue ;; esac
        [ "$(printf '%s' "$OPENCL" | tr ',' '\n' | grep -c .)" -lt 16 ] || break
        OPENCL="${OPENCL:+$OPENCL,}$item"
    done
    set +f
    IFS=$OLDIFS
fi
put OPENCL_PLATFORMS "$OPENCL" "" quiet

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
for num in $(for c in "$DRM"/card[0-9]*; do n=${c##*/card}; case "$n" in ''|*[!0-9]*) continue ;; esac
                                          echo "$n"; done | sort -n); do
    dev=$DRM/card$num/device
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
    # Without vulkaninfo: a Vulkan loader plus a GPU whose driver ships a Vulkan ICD. A vulkaninfo that ran and lists
    # only software renderers (llvmpipe) means no GPU has a Vulkan driver: VULKAN stays OFF.
    if [ -n "$OTHERS$NV_GPUS" ] && [ "$VK_RAN" != 1 ]; then
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
    # Decision O-PREP, each read only when it is there: the gfx targets of every KFD node (also compute devices that
    # are no display controller), the CUDA toolkit release, the TensorRT and OpenVINO runtime libraries, the Intel GPU
    # render node and its driver.
    ROCM_GFX=""
    for props in "$(f /sys/class/kfd/kfd/topology/nodes)"/*/properties; do
        [ -f "$props" ] || continue
        ver=$(awk '$1 == "gfx_target_version" { print $2; exit }' "$props")
        case "$ver" in ''|*[!0-9]*) continue ;; esac
        [ ${#ver} -le 9 ] || continue
        [ "$ver" -gt 0 ] || continue
        t=$(printf 'gfx%d%x%x' $((ver / 10000)) $((ver / 100 % 100)) $((ver % 100)))
        valid '^gfx[0-9a-f]{3,5}$' "$t" || continue
        case ",$ROCM_GFX," in *",$t,"*) ;; *) ROCM_GFX="${ROCM_GFX:+$ROCM_GFX,}$t" ;; esac
    done
    put ROCM_GFX_TARGETS "$(printf '%s' "$ROCM_GFX" | tr ',' '\n' | head -n16 | tr '\n' ',' | sed 's/,$//')" \
        '^gfx[0-9a-f]{3,5}(,gfx[0-9a-f]{3,5})*$' quiet
    if [ -f "$(f /usr/local/cuda/version.json)" ]; then
        v=$(tr -d '\r\n' < "$(f /usr/local/cuda/version.json)" |
            sed -n 's/.*"cuda"[[:space:]]*:[[:space:]]*{[^}]*"version"[[:space:]]*:[[:space:]]*"\([0-9][0-9.]*\)".*/\1/p')
        if [ -n "$v" ]; then put CUDA_TOOLKIT_VERSION "$v" "$P_DOTTED"; else warn "not_determined:CUDA_TOOLKIT_VERSION"; fi
    elif [ -f "$(f /usr/local/cuda/version.txt)" ]; then
        v=$(sed -n 's/^CUDA Version \([0-9][0-9.]*\).*/\1/p' "$(f /usr/local/cuda/version.txt)" | head -n1)
        if [ -n "$v" ]; then put CUDA_TOOLKIT_VERSION "$v" "$P_DOTTED"; else warn "not_determined:CUDA_TOOLKIT_VERSION"; fi
    fi
    v=""
    for m in "$(f /usr/lib)"/*/libnvinfer.so.* "$(f /usr/lib64)"/libnvinfer.so.* "$(f /usr/lib)"/libnvinfer.so.*; do
        [ -e "$m" ] || [ -L "$m" ] || continue
        if valid '^[0-9]+\.[0-9]+\.[0-9]+$' "${m##*/libnvinfer.so.}"; then v=${m##*/libnvinfer.so.}; break; fi
    done
    put TENSORRT_VERSION "$v" '^[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,2}$' quiet
    v=""
    for m in "$(f /usr/lib)"/libopenvino.so.* "$(f /usr/lib)"/*/libopenvino.so.* "$(f /usr/lib64)"/libopenvino.so.*; do
        [ -e "$m" ] || [ -L "$m" ] || continue
        if valid '^[0-9]+\.[0-9]+\.[0-9]+$' "${m##*/libopenvino.so.}"; then v=${m##*/libopenvino.so.}; break; fi
    done
    put OPENVINO_VERSION "$v" '^[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}$' quiet
    for n in "$(f /sys/class/drm)"/renderD[0-9]*; do
        [ -e "$n" ] || [ -L "$n" ] || continue
        vendor=$(hexid "$(rdp "$n/device/vendor")" 4) || continue
        drv=$(link_name "$n/device/driver") || drv=""
        if [ "$vendor" = 0x8086 ] && { [ "$drv" = i915 ] || [ "$drv" = xe ]; }; then
            put INTEL_GPU_DEVICE "/dev/dri/${n##*/}" '^/dev/dri/renderD[0-9]{1,3}$' quiet
            put INTEL_GPU_DRIVER "$drv"
            break
        fi
    done
else
    emit HIP_RUNTIME OFF
fi
emit METAL OFF

# --- access: SSH server, sudo, ssh-keygen, package manager (probe 3.3.0; the same rules as hardware_probe.py) ----

# Copies of configs/hardware_probe_schema.yml tables.ssh_server_*, sudo_paths, ssh_keygen_paths, ssh_client_paths,
# package_managers, dropbear_package_queries and dropbear_packages (tests compare them). Fixed paths and words only: no path, host name or
# user name reaches the result.
ssh_server_name() {  # /proc/<pid>/comm -> SSH server (tables.ssh_server_names)
    case "$1" in
        sshd) echo openssh ;;
        sshd-session) echo openssh ;;
        dropbear) echo dropbear ;;
        dropbearmulti) echo dropbear ;;
        tinysshd) echo other ;;
        *) return 1 ;;
    esac
}
SSH_SERVER_BINARIES="/usr/sbin/sshd:openssh /usr/bin/sshd:openssh /sbin/sshd:openssh /usr/local/sbin/sshd:openssh"
SSH_SERVER_BINARIES="$SSH_SERVER_BINARIES /usr/sbin/dropbear:dropbear /usr/bin/dropbear:dropbear"
SSH_SERVER_BINARIES="$SSH_SERVER_BINARIES /sbin/dropbear:dropbear /usr/local/sbin/dropbear:dropbear"
SSH_SERVER_SERVICES="/etc/systemd/system/multi-user.target.wants/ssh.service:openssh"
SSH_SERVER_SERVICES="$SSH_SERVER_SERVICES /etc/systemd/system/multi-user.target.wants/sshd.service:openssh"
SSH_SERVER_SERVICES="$SSH_SERVER_SERVICES /etc/systemd/system/sockets.target.wants/ssh.socket:openssh"
SSH_SERVER_SERVICES="$SSH_SERVER_SERVICES /etc/systemd/system/multi-user.target.wants/dropbear.service:dropbear"
SSH_SERVER_SERVICES="$SSH_SERVER_SERVICES /etc/runlevels/default/sshd:openssh /etc/runlevels/default/dropbear:dropbear"
SSH_SERVER_SERVICES="$SSH_SERVER_SERVICES /etc/rc2.d/S[0-9][0-9]ssh:openssh /etc/rc2.d/S[0-9][0-9]dropbear:dropbear"
SUDO_PATHS="/usr/bin/sudo /bin/sudo /usr/local/bin/sudo /usr/sbin/sudo"
SSH_KEYGEN_PATHS="/usr/bin/ssh-keygen /bin/ssh-keygen /usr/local/bin/ssh-keygen"
SSH_CLIENT_PATHS="/usr/bin/ssh /bin/ssh /usr/local/bin/ssh"
PACKAGE_MANAGERS="/usr/bin/apt-get:apt /usr/bin/dnf:dnf /usr/bin/zypper:zypper /usr/bin/pacman:pacman /sbin/apk:apk"
PACKAGE_MANAGERS="$PACKAGE_MANAGERS /usr/bin/apk:apk /usr/bin/yum:other /bin/opkg:other /usr/bin/opkg:other"
PACKAGE_MANAGERS="$PACKAGE_MANAGERS /usr/bin/xbps-install:other /usr/bin/emerge:other /usr/sbin/pkg:other"
PACKAGE_MANAGERS="$PACKAGE_MANAGERS /usr/local/bin/brew:other /opt/homebrew/bin/brew:other"
DROPBEAR_PACKAGE_QUERIES="/usr/bin/dpkg-query:dpkg /usr/bin/pacman:pacman /sbin/apk:apk /usr/bin/apk:apk"
DROPBEAR_PACKAGE_QUERIES="$DROPBEAR_PACKAGE_QUERIES /usr/bin/rpm:rpm /bin/rpm:rpm"
DROPBEAR_PACKAGES_DPKG="dropbear dropbear-bin dropbear-run dropbear-initramfs"
DROPBEAR_PACKAGES_PACMAN="dropbear"
DROPBEAR_PACKAGES_APK="dropbear dropbear-openrc"
DROPBEAR_PACKAGES_RPM="dropbear"

first_path() {  # PATH...: the first one that exists (a link counts)
    for p in "$@"; do exists "$p" && { printf '%s' "$p"; return 0; }; done
    return 1
}
kind_add() {  # LIST KIND: LIST with KIND once (space-separated)
    case " $1 " in *" $2 "*) printf '%s' "$1" ;; *) printf '%s' "${1:+$1 }$2" ;; esac
}
pick_server() {  # KINDS: dropbear before openssh before other (see SSH_SERVER in the schema)
    for k in dropbear openssh other; do
        case " $1 " in *" $k "*) echo "$k"; return 0 ;; esac
    done
    return 1
}
openssh_version() { printf '%s\n' "$1" | sed -n 's/.*OpenSSH_\(for_Windows_\)\{0,1\}\([0-9]\{1,2\}\.[0-9]\{1,2\}\).*/\2/p' | head -n1; }
dropbear_version() { printf '%s\n' "$1" | grep Dropbear | grep -oE 'v[0-9]{4}\.[0-9]{1,3}' | head -n1 | cut -c2-; }

binary_kind() {  # PATH KIND: the server a program of the table is, by the name its link points to (followed inside
    # the machine: a Dropbear behind /usr/sbin/sshd is Dropbear), else KIND
    t=$(f "$1") n=0
    while [ -L "$t" ] && [ "$n" -lt 8 ]; do
        l=$(readlink "$t" 2>/dev/null) || break
        case "$l" in /*) t=$(f "$l") ;; *) t="${t%/*}/$l" ;; esac
        n=$((n + 1))
    done
    ssh_server_name "${t##*/}" || echo "$2"
}
proc_kind() {  # COMM: the server a process name is; a name that is also a program of the table counts as that program
    ssh_server_name "$1" >/dev/null || return 1
    found=""
    for spec in $SSH_SERVER_BINARIES; do
        p=${spec%%:*}
        [ "${p##*/}" = "$1" ] && exists "$p" && found=$(kind_add "$found" "$(binary_kind "$p" "${spec#*:}")")
    done
    pick_server "$found" || ssh_server_name "$1"
}

dropbear_packages() {  # the installed packages that provide Dropbear (no root): DROPBEAR_PACKAGES
    for spec in $DROPBEAR_PACKAGE_QUERIES; do
        exists "${spec%%:*}" || continue
        q=$(f "${spec%%:*}")
        case "${spec#*:}" in
            dpkg) names=$DROPBEAR_PACKAGES_DPKG
                  # shellcheck disable=SC2086,SC2016
                  out=$(tool "$q" -W -f '${Package} ${Status}\n' $names | sed -n 's/^\([^ ]*\) .* installed$/\1/p') ;;
            pacman) names=$DROPBEAR_PACKAGES_PACMAN
                    # shellcheck disable=SC2086
                    out=$(tool "$q" -Qq $names) ;;
            apk) names=$DROPBEAR_PACKAGES_APK
                 # shellcheck disable=SC2086
                 out=$(tool "$q" info -e $names) ;;
            *) names=$DROPBEAR_PACKAGES_RPM
               # shellcheck disable=SC2086
               out=$(tool "$q" -q --qf '%{NAME}\n' $names) ;;
        esac
        list=""
        for n in $names; do
            printf '%s\n' "$out" | grep -qx -- "$n" && list="${list:+$list,}$n"
        done
        [ -n "$list" ] && put DROPBEAR_PACKAGES "$list"
        return 0
    done
    return 0
}

access_facts() {
    INSTALLED=""
    for spec in $SSH_SERVER_BINARIES; do
        exists "${spec%%:*}" && INSTALLED=$(kind_add "$INSTALLED" "$(binary_kind "${spec%%:*}" "${spec#*:}")")
    done
    # The listener of TCP port 22 (state 0A), by its socket inode in /proc/<pid>/fd where readable.
    LISTENERS=""
    INODES=$( { rd /proc/net/tcp; rd /proc/net/tcp6; } | tr -d '\r' |
              awk 'NR > 0 && $4 == "0A" && toupper($2) ~ /:0016$/ && $10 ~ /^[0-9]+$/ && $10 != "0" { print $10 }' |
              sort -u)
    if [ -n "$INODES" ]; then
        WANT="socket:\[($(printf '%s\n' "$INODES" | tr '\n' '|' | sed 's/|$//'))\]\$"
        for d in "$(f /proc)"/[0-9]*/fd; do
            [ -d "$d" ] || continue
            # The link targets are matched (socket:[inode]), not file names; one ls per process, not one readlink per fd.
            # shellcheck disable=SC2010
            ls -l "$d" 2>/dev/null | grep -Eq -- "$WANT" || continue   # another account's process: not readable
            k=$(proc_kind "$(trim "$(head -n1 "${d%/fd}/comm" 2>/dev/null)")") &&
                LISTENERS=$(kind_add "$LISTENERS" "$k")
        done
    fi
    PROCESSES=""
    for c in "$(f /proc)"/[0-9]*/comm; do
        [ -f "$c" ] || continue
        k=$(proc_kind "$(trim "$(head -n1 "$c" 2>/dev/null)")") && PROCESSES=$(kind_add "$PROCESSES" "$k")
    done
    SERVICES=""
    for spec in $SSH_SERVER_SERVICES; do
        for m in $(f "${spec%%:*}"); do
            if [ -e "$m" ] || [ -L "$m" ]; then SERVICES=$(kind_add "$SERVICES" "${spec#*:}"); break; fi
        done
    done
    SERVER=none SOURCE=""
    if s=$(pick_server "$LISTENERS"); then SERVER=$s SOURCE=socket
    elif s=$(pick_server "$PROCESSES"); then SERVER=$s SOURCE=process
    elif s=$(pick_server "$SERVICES"); then SERVER=$s SOURCE=service
    elif s=$(pick_server "$INSTALLED"); then SERVER=$s SOURCE=binary
    fi
    put SSH_SERVER "$SERVER"
    [ -n "$SOURCE" ] && put SSH_SERVER_SOURCE "$SOURCE"
    LIST=""
    for k in openssh dropbear; do
        case " $INSTALLED " in *" $k "*) LIST="${LIST:+$LIST,}$k" ;; esac
    done
    [ -n "$LIST" ] && put SSH_SERVERS_INSTALLED "$LIST"
    case "$SERVER" in
        openssh|dropbear)
            for spec in $SSH_SERVER_BINARIES; do
                exists "${spec%%:*}" && [ "$(binary_kind "${spec%%:*}" "${spec#*:}")" = "$SERVER" ] || continue
                out=$(tool_all "$(f "${spec%%:*}")" -V)
                if [ "$SERVER" = openssh ]; then v=$(openssh_version "$out"); else v=$(dropbear_version "$out"); fi
                put SSH_SERVER_VERSION "$v" "$P_VERSION" quiet
                break
            done ;;
    esac
    # sudo: root needs none; else `sudo -k -n true` only (never prompts, never reads a password).
    UID_NOW=$(tool id -u)
    if [ "$UID_NOW" = 0 ]; then put SUDO root
    elif ! s=$(first_path $SUDO_PATHS); then put SUDO absent
    else
        $TIMEOUT "$(f "$s")" -k -n true </dev/null >/dev/null 2>&1
        rc=$?
        if [ "$rc" = 0 ]; then put SUDO nopasswd
        elif [ "$rc" = 126 ] || [ "$rc" = 127 ] || [ "$rc" = 124 ]; then warn "not_determined:SUDO"
        else put SUDO password
        fi
    fi
    if first_path $SSH_KEYGEN_PATHS >/dev/null; then
        put SSH_KEYGEN openssh
        v=""
        if c=$(first_path $SSH_CLIENT_PATHS); then v=$(openssh_version "$(tool_all "$(f "$c")" -V)"); fi
        put SSH_KEYGEN_VERSION "$v" "$P_VERSION" quiet || warn "not_determined:SSH_KEYGEN_VERSION"
    else
        put SSH_KEYGEN none
    fi
    for spec in $PACKAGE_MANAGERS; do
        if exists "${spec%%:*}"; then put PACKAGE_MANAGER "${spec#*:}"; break; fi
    done
    [ "$OS" = linux ] && dropbear_packages
    return 0
}
access_facts

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
