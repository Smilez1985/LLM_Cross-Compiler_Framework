#!/bin/bash
# test_model.sh - a short test of the model on the board (in the package for the board, written by target_module.sh)
#
#   ./test_model.sh [PROMPT] [TOKENS]      (default: "Hello, world!" and 10 tokens, as in version 1.x)
#
# Checks the package first: its files against SHA256SUMS (when the board has sha256sum) and that this machine is the
# system the package's programs were built for (MANIFEST.json, "arch"). Then llama-cli of the package answers the
# prompt once (single turn: it does not wait for more input) and the script ends with llama-cli's exit code.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT="${1:-Hello, world!}"
TOKENS="${2:-10}"
cd "$DIR"

if [ -f SHA256SUMS ] && command -v sha256sum > /dev/null 2>&1; then
    if ! sha256sum -c --quiet SHA256SUMS; then
        echo "The package is damaged: the files above do not match SHA256SUMS. Copy the package to the board again." >&2
        exit 1
    fi
fi

# This machine runs programs of the package's system: the same system, or a 32-bit Arm package (armv6, armv7) on an
# Arm machine that runs 32-bit programs (armv7l; armv8l, a 32-bit system on a 64-bit CPU; aarch64, a 64-bit kernel
# with a 32-bit system such as Raspberry Pi OS 32-bit on a Pi 4 or 5).
runs_here() {  # PACKAGE_SYSTEM MACHINE
    [ "$1" = "$2" ] && return 0
    case "$1:$2" in
        armv6*:armv6*|armv6*:armv7*|armv6*:armv8l|armv6*:aarch64|armv7*:armv7*|armv7*:armv8l|armv7*:aarch64) return 0 ;;
    esac
    return 1
}

expected="$(sed -n 's/^ *"arch": "\([A-Za-z0-9_]*\)".*/\1/p' MANIFEST.json 2>/dev/null | head -n 1)"
machine="$(uname -m)"
[ "$machine" = arm64 ] && machine=aarch64
if [ -n "$expected" ] && ! runs_here "$expected" "$machine"; then
    echo "This package is for a $expected system; this machine is $machine. Copy it to the board (./deploy.sh USER@HOST) and run ./test_model.sh there." >&2
    exit 1
fi

models=(*.gguf)
if [ ! -f "${models[0]}" ]; then
    echo "The package has no GGUF model." >&2
    exit 1
fi
MODEL="${models[0]}"
CLI="$DIR/llama-cli"
if [ ! -x "$CLI" ]; then chmod +x "$CLI"; fi
case "$TOKENS" in
    ''|*[!0-9]*) echo "TOKENS must be a number, not '$TOKENS'." >&2; exit 2 ;;
esac

echo "Testing model: $MODEL"
threads="$(nproc 2>/dev/null || echo 4)"
exec "$CLI" -m "$MODEL" -p "$PROMPT" -n "$TOKENS" -st --no-warmup -t "$threads" < /dev/null
