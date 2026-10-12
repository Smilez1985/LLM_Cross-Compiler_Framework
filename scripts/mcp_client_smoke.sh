#!/bin/sh
# Real-client smoke test of the framework's MCP server with Claude Code (a manual or optional CI check).
#
# Builds a throw-away environment (home, Hugging Face home, XDG folders, the framework's per-user folder, secrets in
# memory only, no OS keyring, no window system), starts the framework's stdio server inside it through the claude
# command with a few scripted prompts, and then checks the outcomes from the isolated audit database (and, for what
# the client was shown, from the tool results in the client's JSON output), never from the model's own words.
#
# The claude command signs in with its own sign-in: this script never reads, copies or passes Claude credentials or
# tokens, and the framework's server gets none of the caller's environment (`env -i` with a short list). The client's
# cache (its log of the server's stderr) and its temporary files (TMPDIR) are in the temporary folder too; its own
# settings file is updated as on every run of the claude command. No Docker.
#
#   scripts/mcp_client_smoke.sh            exit 0: every check passed, 1: a check failed, 2: setup problem,
#                                          77: skipped (the claude command is missing or not signed in)
#
# Settings (optional): LCF_SMOKE_PYTHON (the framework's Python; default: python3), LCF_SMOKE_CLAUDE (default:
# claude), LCF_SMOKE_TIMEOUT (seconds per prompt; default 300), LCF_SMOKE_KEEP=1 (keep the temporary folder).

set -u

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd) || exit 2
repo=$(CDPATH='' cd -- "$here/.." && pwd) || exit 2
helper="$here/mcp_smoke_check.py"
claude=${LCF_SMOKE_CLAUDE:-claude}
python=${LCF_SMOKE_PYTHON:-python3}
limit=${LCF_SMOKE_TIMEOUT:-300}

skip() {
    echo "SKIP: $*" >&2
    exit 77
}

# --- is there a Claude Code? ------------------------------------------------------------------------------------
command -v "$claude" >/dev/null 2>&1 ||
    skip "the claude command is not installed, so there is no real client to test with (install Claude Code, or set LCF_SMOKE_CLAUDE)."

# --- the temporary folder, removed at the end ---------------------------------------------------------------------
tmp=$(mktemp -d "${TMPDIR:-/tmp}/lcf-mcp-smoke.XXXXXX") || exit 2
cleanup() {
    if [ "${LCF_SMOKE_KEEP:-0}" = 1 ]; then
        echo "kept: $tmp" >&2
    else
        rm -rf "$tmp"
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# The client keeps its sign-in and settings; only its cache (where it logs the server's stderr) and its temporary
# files (TMPDIR) go into the temporary folder, on every run of it, so nothing of the run stays behind.
mkdir -p "$tmp/client-cache" "$tmp/client-tmp" || exit 2
client_env() {
    export XDG_CACHE_HOME="$tmp/client-cache" TMPDIR="$tmp/client-tmp"
}

# --- is it signed in? (asked through the command itself; no file of it is read) ---------------------------------
status=$(client_env && "$claude" auth status --json </dev/null 2>/dev/null) || status=""
case $status in
    *'"loggedIn": true'* | *'"loggedIn":true'*) ;;
    *) skip "the claude command is not signed in ('claude auth login'); this script never signs in for it." ;;
esac

# --- the framework's Python ---------------------------------------------------------------------------------------
case $python in
    */*) ;;
    *) python=$(command -v "$python" 2>/dev/null) || python="" ;;
esac
if [ -z "$python" ] || ! (cd "$repo" && PYTHONPATH="$repo" "$python" -c 'import mcp, orchestrator.mcp_server') \
        >/dev/null 2>&1; then
    echo "ERROR: the framework's Python with its dependencies is needed: set LCF_SMOKE_PYTHON (for example to the" \
         "virtual environment's bin/python)." >&2
    exit 2
fi

# --- the isolated environment of the server -----------------------------------------------------------------------
"$python" -I "$helper" config --root "$tmp" --repo "$repo" --python "$python" --path "$PATH" \
    --out "$tmp/mcp.json" || exit 2

wait_for=""
command -v timeout >/dev/null 2>&1 && wait_for="timeout $limit"

run() {  # name: one prompt, one session of its own; the server starts and ends with it
    prompt=$("$python" -I "$helper" prompt "$1") || return 1
    # shellcheck disable=SC2086 # $wait_for is empty or two words
    (cd "$repo" && client_env &&
        $wait_for "$claude" -p "$prompt" --mcp-config "$tmp/mcp.json" --strict-mcp-config \
        --allowedTools "mcp__lcf" --tools "" --output-format json --verbose --no-session-persistence \
        </dev/null >"$tmp/$1.json" 2>"$tmp/$1.err")
    code=$?
    echo "run '$1': claude exited with $code"
    if [ "$code" -ne 0 ]; then
        tail -n 5 "$tmp/$1.err" | sed 's/^/    /'
    fi
    return 0
}

echo "LLM Cross-Compiler Framework: MCP smoke test with $(client_env && "$claude" --version </dev/null 2>/dev/null |
    head -n 1)"
for name in status unknown alias confirm; do
    run "$name"
done

"$python" -I "$helper" check --root "$tmp" \
    --transcript "status=$tmp/status.json" --transcript "unknown=$tmp/unknown.json" \
    --transcript "alias=$tmp/alias.json" --transcript "confirm=$tmp/confirm.json"
exit $?
