#!/usr/bin/env bash
# The system libraries Qt (PySide6) needs on a Linux CI runner, without depending on one package mirror.
#
# 1. Libraries already on the runner image are not installed again: when every one is there, apt is not touched at
#    all (most of them come with GitHub's Ubuntu image; usually only libEGL is missing).
# 2. A missing one comes from the runner's own package sources first, with retries.
# 3. When those cannot be reached (the runners have lost the DNS of *.ubuntu.com before, for hours), the same
#    packages come from official Ubuntu mirrors on other hosts, one after the other. apt checks them exactly as it
#    checks the runner's own sources: against the signed Release file and the Ubuntu archive keyring already on the
#    image, so a mirror changes where the bytes come from, never what is trusted.
#
# Usage: scripts/ci_qt_libraries.sh   (as root or with sudo available; Ubuntu or Debian-based runners)
set -euo pipefail

# soname -> package
LIBRARIES=(
    "libEGL.so.1 libegl1"
    "libGL.so.1 libgl1"
    "libxkbcommon.so.0 libxkbcommon0"
    "libfontconfig.so.1 libfontconfig1"
    "libdbus-1.so.3 libdbus-1-3"
    "libglib-2.0.so.0 libglib2.0-0"
)
FALLBACK_MIRRORS=(
    "https://mirrors.edge.kernel.org/ubuntu"
    "https://mirror.leaseweb.net/ubuntu"
    "https://ftp.halifax.rwth-aachen.de/ubuntu"
)
KEYRING="/usr/share/keyrings/ubuntu-archive-keyring.gpg"
APT_OPTIONS=(-o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

missing=()
for entry in "${LIBRARIES[@]}"; do
    soname="${entry%% *}"
    package="${entry#* }"
    if ldconfig -p | grep -q "[[:space:]]${soname} "; then
        echo "on the image: ${soname}"
    else
        missing+=("$package")
    fi
done

if [ "${#missing[@]}" -eq 0 ]; then
    echo "Every library Qt needs is on the image; nothing to install."
    exit 0
fi
echo "To install: ${missing[*]}"

install_from_runner_sources() {
    # A failed update keeps the image's package lists, which are often recent enough to install from.
    $SUDO apt-get "${APT_OPTIONS[@]}" update || echo "apt-get update failed; trying with the image's package lists"
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get "${APT_OPTIONS[@]}" install -y --no-install-recommends "${missing[@]}"
}

install_from_mirror() {
    local mirror="$1" codename parts
    # shellcheck disable=SC1091
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
    if [ -z "$codename" ] || [ ! -f "$KEYRING" ]; then
        echo "No Ubuntu codename or archive keyring here; the fallback mirrors are for Ubuntu runners only."
        return 1
    fi
    parts="$(mktemp -d)"
    cat > "$parts/fallback.sources" <<EOF
Types: deb
URIs: ${mirror}
Suites: ${codename} ${codename}-updates ${codename}-security
Components: main universe
Signed-By: ${KEYRING}
EOF
    local only_this=(-o "Dir::Etc::SourceList=/dev/null" -o "Dir::Etc::SourceParts=${parts}")
    local rc=0
    $SUDO apt-get "${APT_OPTIONS[@]}" "${only_this[@]}" update || rc=$?
    if [ "$rc" -eq 0 ]; then
        DEBIAN_FRONTEND=noninteractive $SUDO apt-get "${APT_OPTIONS[@]}" "${only_this[@]}" \
            install -y --no-install-recommends "${missing[@]}" || rc=$?
    fi
    rm -rf "$parts"
    return "$rc"
}

if install_from_runner_sources; then
    exit 0
fi
for mirror in "${FALLBACK_MIRRORS[@]}"; do
    echo "The runner's package sources failed; trying the Ubuntu mirror ${mirror}"
    if install_from_mirror "$mirror"; then
        exit 0
    fi
done
echo "The Qt libraries could not be installed from the runner's sources or any fallback mirror: ${missing[*]}" >&2
exit 1
