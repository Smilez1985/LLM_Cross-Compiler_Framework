#!/bin/bash
# deploy.sh - puts this package onto the board (in the package for the board, written by target_module.sh)
#
#   ./deploy.sh USER@HOST [DEST] [--port PORT] [--identity KEYFILE]
#       on your computer: copies the package to DEST on the board over scp (default: llm/<this folder's name> in the
#       user's home there), checks the copy there against SHA256SUMS and makes the programs executable. The board's
#       host key must already be in your known_hosts: unknown or changed keys are refused
#       (StrictHostKeyChecking=yes), nothing is accepted blindly. To add it, compare the fingerprint
#       `ssh-keyscan -t ed25519 HOST | ssh-keygen -lf -` shows with the one the board itself shows
#       (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`, at the board's own console) before you trust it.
#   ./deploy.sh [DEST]
#       on the board itself: copies the package to DEST (default /opt/llm), as deploy.sh of version 1.x did.
#
# The framework deploys a package itself through its own board session (llm-cli deploy, or the Deployment window):
# this script is for using the package by hand.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="$(basename "$DIR")"

die() { echo "deploy.sh: $*" >&2; exit 1; }

usage() {
    echo "Usage: ./deploy.sh USER@HOST [DEST] [--port PORT] [--identity KEYFILE]   (copy to a board over ssh)"
    echo "       ./deploy.sh [DEST]                                               (install on this machine)"
}

TARGET=""
DEST=""
PORT=22
IDENTITY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --port) [ $# -ge 2 ] || die "--port needs a value."; PORT="$2"; shift 2 ;;
        --identity) [ $# -ge 2 ] || die "--identity needs a value."; IDENTITY="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) die "Unknown option '$1' (./deploy.sh --help)." ;;
        *@*) [ -z "$TARGET" ] || die "Only one USER@HOST, please."; TARGET="$1"; shift ;;
        *) [ -z "$DEST" ] || die "Only one DEST, please."; DEST="$1"; shift ;;
    esac
done

# The board's files are made by a fixed command line: the destination may hold only plain path characters.
check_dest() {
    [[ "$1" =~ ^[A-Za-z0-9._/-]+$ ]] && [[ "$1" != -* ]] && [[ "/$1/" != */../* ]] \
        || die "The destination '$1' may hold only letters, digits and . _ / - (no '..'; a relative one is in the user's home)."
}

if [ -z "$TARGET" ]; then
    # On the board itself (version 1.x)
    DEST="${DEST:-/opt/llm}"
    check_dest "$DEST"
    echo "Deploying to $DEST..."
    mkdir -p -- "$DEST"
    cp -r -- "$DIR"/. "$DEST/"
    if [ -f "$DEST/SHA256SUMS" ] && command -v sha256sum > /dev/null 2>&1; then
        (cd -- "$DEST" && sha256sum -c --quiet SHA256SUMS) || die "The copy in $DEST does not match SHA256SUMS."
    fi
    echo "Done. Test it: $DEST/test_model.sh"
    exit 0
fi

user="${TARGET%@*}"
host="${TARGET#*@}"
[[ "$user" =~ ^[A-Za-z_][A-Za-z0-9._-]{0,31}$ ]] || die "'$user' is no user name of a board (letters, digits, . _ -)."
[[ "$host" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ || "$host" =~ ^\[[0-9A-Fa-f:.]+\]$ ]] \
    || die "'$host' is no host name or address (an IPv6 address goes in brackets: [fe80::1])."
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "'$PORT' is no port."
DEST="${DEST:-llm/$NAME}"
check_dest "$DEST"

for tool in ssh scp; do
    command -v "$tool" > /dev/null 2>&1 || die "$tool is not installed on this computer (OpenSSH client)."
done
# Never accept a key that is not known already; ConnectTimeout and keep-alive so a board that does not answer ends
# the copy with a reason instead of hanging.
opts=(-o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
if [ -n "$IDENTITY" ]; then
    [ -f "$IDENTITY" ] || die "The key file '$IDENTITY' does not exist."
    opts+=(-i "$IDENTITY" -o IdentitiesOnly=yes)
fi
remote_host="${host#[}"
remote_host="${remote_host%]}"

files=()
for f in "$DIR"/*; do
    if [ -f "$f" ] && [ ! -L "$f" ]; then
        files+=("$f")
    fi
done

echo "Copying $NAME to $user@$host:$DEST ..."
ssh "${opts[@]}" -p "$PORT" -- "$user@$remote_host" "mkdir -p -- '$DEST'" \
    || die "The board did not accept the login or the folder (above). Is its host key in your known_hosts?"
scp "${opts[@]}" -P "$PORT" -p -- "${files[@]}" "$user@$host:$DEST/" || die "The copy failed (above)."
ssh "${opts[@]}" -p "$PORT" -- "$user@$remote_host" \
    "cd -- '$DEST' && if command -v sha256sum > /dev/null 2>&1; then sha256sum -c --quiet SHA256SUMS; else echo 'The board has no sha256sum: the copy was not checked there.'; fi && chmod 755 llama-cli llama-server test_model.sh deploy.sh" \
    || die "The copy on the board does not match SHA256SUMS, or its programs could not be made executable (above)."
echo "Done. Test it on the board: ssh -p $PORT $user@$remote_host '$DEST/test_model.sh'"
