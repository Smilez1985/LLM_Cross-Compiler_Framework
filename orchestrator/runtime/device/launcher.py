#!/usr/bin/env python3
"""
The launchers of the device runtime: the start script that ships in every device tarball, and the systemd unit a
deployment installs when the deploy settings choose it (decision D7). Both are generated from the shared server
policy (orchestrator/runtime/server_policy.py, decision D22):

- The server listens on 127.0.0.1 only and needs the key in ~/.config/llm-framework/llama-server.key. The key is
  generated on the device by the script (`keygen`, which the deployment runs) under umask 077, so it is never
  readable by anyone else, not even for a moment; it never leaves the device.
- The server starts with a cleared environment (`env -i` with HOME, a fixed PATH and the C locale), so no
  LLAMA_ARG_* variable, GGML_BACKEND_PATH library or proxy setting reaches it.
- The arguments are fixed (-m MODEL, --host 127.0.0.1, --port, --api-key-file, --no-ui, --no-slots, --jinja,
  --offline). The few tuning options a user may add (context size, threads, CPU mask, ...) are passed on only after
  their value is checked; every option of the forbidden list is refused by name, and so is any other option, so the
  script can neither switch on the model's tools nor move the server off loopback. llama.cpp reads `--a_b` as
  `--a-b`, so the script normalises the same way before it checks.
- The programs are taken from the script's own folder by absolute path, never from PATH.

The script is plain POSIX sh (tested with dash and bash) and needs only the usual tools (dirname, od, dd, tr, readlink
for `stop`/`status`).
"""

import re
import shlex
from typing import Dict, List, Mapping, Optional, Tuple

from orchestrator.runtime.server_policy import (DEVICE_DEFAULT_PORT, FORBIDDEN_FLAGS, KV_CACHE_TYPES, LOOPBACK,
                                                POSIX_CLEARED_PATH, SERVER_FIXED, START_SCRIPT_OPTIONS, check_command,
                                                check_server_command, kv_cache_args, posix_cleared_env)

START_SCRIPT = "llama-server.sh"
UNIT_NAME = "llm-llama-server.service"
UNIT_PATH = f"/etc/systemd/system/{UNIT_NAME}"
# Where things live in the device user's home (relative to it).
INSTALL_REL = ".local/share/llm-framework/llama.cpp"
KEY_REL = ".config/llm-framework/llama-server.key"
STATE_REL = ".local/state/llm-framework"

# Absolute paths on the device carry plain characters only (no shell meaning). Their length is only a sanity bound,
# far below PATH_MAX (4096 on Linux), so a deep home folder is never refused for its length: a folder (the home, the
# install folder, a staging folder) up to DEVICE_FOLDER_MAX characters, a file in it (a model) up to DEVICE_PATH_MAX.
DEVICE_FOLDER_MAX = 1024
DEVICE_PATH_MAX = DEVICE_FOLDER_MAX + 512
_RE_OPTION = re.compile(r"^-{1,2}[a-z][a-z0-9-]{0,40}$", re.ASCII | re.IGNORECASE)
_RE_PATH = re.compile(r"^/[A-Za-z0-9._+/-]{1,%d}$" % DEVICE_PATH_MAX, re.ASCII)
_RE_USER = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.ASCII | re.IGNORECASE)
_KINDS = ("uint", "sint", "hex", "bit", "layers", "switch", "loadmode", "name", "cachetype")
_RE_CACHE_TYPE = re.compile(r"^[a-z0-9_]{1,16}$", re.ASCII)


class LauncherError(ValueError):
    """A launcher cannot be generated from these values."""


def _pattern(names) -> str:
    names = sorted(names)
    for name in names:
        if not _RE_OPTION.fullmatch(name):  # pragma: no cover - the policy's own constants
            raise LauncherError(f"{name!r} is not an option name")
    return "|".join(names)


def _options_by_kind(options: Mapping[Tuple[str, ...], str]) -> Dict[str, List[str]]:
    by_kind: Dict[str, List[str]] = {}
    for names, kind in options.items():
        if kind not in _KINDS:  # pragma: no cover - the policy's own constants
            raise LauncherError(f"unknown value kind {kind!r}")
        by_kind.setdefault(kind, []).extend(names)
    return by_kind


def _env_words() -> str:
    """`HOME="$HOME" PATH=/usr/bin:/bin LANG=C LC_ALL=C`: the cleared environment of the shared policy."""
    words = []
    for key, value in posix_cleared_env("$HOME").items():
        words.append(f'{key}="$HOME"' if key == "HOME" else f"{key}={shlex.quote(value)}")
    return " ".join(words)


def _server_words() -> str:
    """The server's part of the script's exec line, `"$SERVER" -m "$model" --host 127.0.0.1 --port "$port"
    --api-key-file "$KEY" --no-ui --no-slots --jinja --offline`: built from the shared policy and held to the same
    check_server_command as Ditto's server (loopback only, one port, the key file, the fixed options, nothing of the
    forbidden list), with the shell variables standing in for the values the script checks at run time."""
    argv = ["$SERVER", "-m", "$model", "--host", LOOPBACK, "--port", "$port", "--api-key-file", "$KEY",
            *SERVER_FIXED]
    check_server_command(argv)
    return " ".join(f'"{word}"' if word.startswith("$") else word for word in argv)


_SCRIPT = r'''#!/bin/sh
# llama-server start script of the LLM Cross-Compiler Framework (generated; a new deployment replaces it).
#
#   llama-server.sh serve MODEL [OPTION VALUE ...]   run the server in the foreground (also what the systemd unit runs)
#   llama-server.sh start MODEL [OPTION VALUE ...]   run it in the background (log and pid in ~/.local/state/llm-framework)
#   llama-server.sh stop | status | version
#   llama-server.sh keygen                           create the API key file if it is missing (only you can read it)
#
# The server listens on 127.0.0.1 only (default port @PORT@, --port to change it) and needs the key from
# ~/.config/llm-framework/llama-server.key in every request but /health ("Authorization: Bearer <key>"). It starts
# with a cleared environment. Options it passes on: @ALLOWED_TEXT@.
# The framework's server policy forbids, among others, --tools, --agent, MCP servers, the web UI, RPC and --api-key.
set -eu
PATH=@PATH@
export PATH
umask 077

me=llama-server.sh
die() { printf '%s: %s\n' "$me" "$*" >&2; exit 2; }

DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
BASE=$(dirname -- "$DIR")
SERVER="$DIR/bin/llama-server"
case "${HOME:-}" in
  /*) ;;
  *) die "HOME is not an absolute path" ;;
esac
KEY="$HOME/@KEY_REL@"
CONF=$(dirname -- "$KEY")
STATE="$HOME/@STATE_REL@"
PIDFILE="$STATE/llama-server.pid"
LOG="$STATE/llama-server.log"

[ -f "$SERVER" ] && [ -x "$SERVER" ] || die "no llama-server in $DIR/bin"

keygen() {
  mkdir -p "$CONF"
  chmod 700 "$CONF"
  if [ ! -s "$KEY" ]; then
    tmp="$KEY.$$.tmp"
    dd if=/dev/urandom bs=32 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n' > "$tmp"
    printf '\n' >> "$tmp"
    size=$(wc -c < "$tmp" | tr -d ' ')
    [ "$size" = 65 ] || { rm -f "$tmp"; die "could not create a key from /dev/urandom"; }
    mv -f "$tmp" "$KEY"
  fi
  chmod 600 "$KEY"
}

value_ok() {
  kind=$1 v=$2
  case $kind in
    uint) case $v in ''|*[!0-9]*) return 1 ;; esac; [ ${#v} -le 9 ] ;;
    sint) w=${v#-}; case $w in ''|*[!0-9]*) return 1 ;; esac; [ ${#w} -le 9 ] ;;
    hex) w=${v#0x}; case $w in ''|*[!0-9a-fA-F]*) return 1 ;; esac; [ ${#w} -le 32 ] ;;
    bit) case $v in 0|1) return 0 ;; esac; return 1 ;;
    layers) case $v in auto|all) return 0 ;; ''|*[!0-9]*) return 1 ;; esac; [ ${#v} -le 4 ] ;;
    switch) case $v in on|off|auto) return 0 ;; esac; return 1 ;;
    loadmode) case $v in auto|none|mmap|mlock|mmap+mlock) return 0 ;; esac; return 1 ;;
    name) case $v in ''|-*|*[!A-Za-z0-9._:-]*) return 1 ;; esac; [ ${#v} -le 64 ] ;;
    cachetype) case $v in @CACHE_TYPES@) return 0 ;; esac; return 1 ;;
    port) case $v in ''|*[!0-9]*) return 1 ;; esac; [ ${#v} -le 5 ] && [ "$v" -ge 1024 ] && [ "$v" -le 65535 ] ;;
    *) return 1 ;;
  esac
}

# run_server MODE MODEL [OPTION VALUE ...]: checks every argument; MODE exec starts the server, check only checks.
run_server() {
  mode=$1 model=$2
  shift 2
  case $model in
    ''|-*) die "the model path must not be empty or start with '-'" ;;
  esac
  [ -f "$model" ] || die "no model file at $model"
  [ -r "$model" ] || die "cannot read $model"
  port=@PORT@
  n=$#
  i=0
  while [ "$i" -lt "$n" ]; do
    opt=$1
    shift
    i=$((i + 1))
    case $opt in
      --*) opt=$(printf '%s' "$opt" | tr '_' '-') ;;
    esac
    case ${opt%%=*} in
      @FORBIDDEN@) die "$opt is forbidden by the framework's server policy" ;;
    esac
    case $opt in
      *=*) die "write the value of ${opt%%=*} as a separate argument" ;;
      --port) kind=port ;;
@ALLOWED_CASES@
      *) die "$opt is not an option this script passes on (allowed: @ALLOWED_TEXT@)" ;;
    esac
    [ "$i" -lt "$n" ] || die "$opt needs a value"
    val=$1
    shift
    i=$((i + 1))
    value_ok "$kind" "$val" || die "$val is not a valid value for $opt"
    if [ "$opt" = "--port" ]; then
      port=$val
    else
      set -- "$@" "$opt" "$val"
    fi
  done
  [ "$mode" = exec ] || return 0
  [ -s "$KEY" ] || keygen
  exec env -i @ENV@ @SERVER_ARGS@ "$@"
}

running() {
  [ -s "$PIDFILE" ] || return 1
  pid=$(cat "$PIDFILE")
  case $pid in ''|*[!0-9]*) return 1 ;; esac
  exe=$(readlink "/proc/$pid/exe" 2>/dev/null) || return 1
  case $exe in
    "$BASE"/*/bin/llama-server|"$BASE"/*/bin/llama-server" (deleted)") return 0 ;;
  esac
  return 1
}

cmd=${1:-help}
[ $# -eq 0 ] || shift
case $cmd in
  serve)
    [ $# -ge 1 ] || die "usage: $me serve MODEL [OPTION VALUE ...]"
    run_server exec "$@" ;;
  start)
    [ $# -ge 1 ] || die "usage: $me start MODEL [OPTION VALUE ...]"
    run_server check "$@"
    mkdir -p "$STATE"
    if running; then die "already running (pid $pid); stop it first"; fi
    keygen
    nohup sh "$DIR/$me" serve "$@" > "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"
    echo "started (pid $!), log: $LOG, key: $KEY" ;;
  stop)
    if running; then
      kill "$pid"
      i=0
      while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 15 ]; do sleep 1; i=$((i + 1)); done
      rm -f "$PIDFILE"
      echo "stopped (pid $pid)"
    else
      rm -f "$PIDFILE"
      echo "not running"
    fi ;;
  status)
    if running; then echo "running (pid $pid): $exe"; else echo "not running"; exit 3; fi ;;
  keygen)
    keygen
    echo "$KEY" ;;
  version)
    exec env -i @ENV@ "$SERVER" --version ;;
  help|-h|--help)
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0" ;;
  *)
    die "unknown command $cmd (serve, start, stop, status, keygen, version)" ;;
esac
'''


def render_start_script(default_port: int = DEVICE_DEFAULT_PORT) -> str:
    """The start script of the device tarball (the same for every device; the port is its default)."""
    if not isinstance(default_port, int) or not 1024 <= default_port <= 65535:
        raise LauncherError("the default port must be from 1024 to 65535")
    by_kind = _options_by_kind(START_SCRIPT_OPTIONS)
    cases = "\n".join(f"      {_pattern(names)}) kind={kind} ;;" for kind, names in by_kind.items())
    allowed = ", ".join(["--port"] + [names[-1] for names in START_SCRIPT_OPTIONS])
    server_args = _server_words()
    allowed_names = [n for names in START_SCRIPT_OPTIONS for n in names]
    if set(allowed_names) & FORBIDDEN_FLAGS:  # pragma: no cover - the policy's own constants
        raise LauncherError("an allowed option is on the forbidden list")
    if not all(_RE_CACHE_TYPE.fullmatch(kind) for kind in KV_CACHE_TYPES):  # pragma: no cover - policy constants
        raise LauncherError("a KV-cache type is not a plain word")
    text = _SCRIPT
    for token, value in (("@PORT@", str(default_port)), ("@PATH@", POSIX_CLEARED_PATH), ("@KEY_REL@", KEY_REL),
                         ("@STATE_REL@", STATE_REL), ("@FORBIDDEN@", _pattern(FORBIDDEN_FLAGS)),
                         ("@ALLOWED_CASES@", cases), ("@ALLOWED_TEXT@", allowed), ("@ENV@", _env_words()),
                         ("@CACHE_TYPES@", "|".join(KV_CACHE_TYPES)),
                         ("@SERVER_ARGS@", server_args)):
        text = text.replace(token, value)
    if re.search(r"@[A-Z_]+@", text):  # pragma: no cover - template check
        raise LauncherError("the start script template has an unfilled placeholder")
    return text


def install_root(home: str) -> str:
    return f"{home.rstrip('/')}/{INSTALL_REL}"


def render_systemd_unit(*, user: str, home: str, model: str, port: int, kv_cache: Optional[str] = None) -> str:
    """The systemd unit of the deploy setting "systemd": the start script's `serve` as the unprivileged user, the
    same fixed arguments (plus the KV-cache type of the deploy settings, checked again by the script), no
    Environment= or EnvironmentFile= (the script clears the environment anyway), and the server limited to loopback
    by systemd itself. Every value is checked; none comes from the device unchecked."""
    if not isinstance(user, str) or not _RE_USER.fullmatch(user) or user == "root":
        raise LauncherError("the unit runs as an unprivileged account name, never root")
    for what, path in (("home", home), ("model", model)):
        if not isinstance(path, str) or not _RE_PATH.fullmatch(path) or ".." in path.split("/"):
            raise LauncherError(f"the {what} path is not a plain absolute path")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise LauncherError("the port must be from 1024 to 65535")
    script = f"{install_root(home)}/current/{START_SCRIPT}"
    argv = ["/bin/sh", script, "serve", model, "--port", str(port), *kv_cache_args(kv_cache)]
    check_command(argv)
    return "\n".join([
        "# llama-server of the LLM Cross-Compiler Framework (generated; a new deployment replaces it).",
        "# Self-built llama.cpp, started by the framework's start script on 127.0.0.1 with a key file and a cleared",
        "# environment, as the unprivileged user below.",
        "[Unit]",
        "Description=llama-server (self-built llama.cpp, LLM Cross-Compiler Framework)",
        "After=network.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user}",
        f"WorkingDirectory={home}",
        "ExecStart=" + " ".join(argv),
        "Restart=on-failure",
        "RestartSec=10",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "ProtectKernelTunables=yes",
        "ProtectKernelModules=yes",
        "ProtectControlGroups=yes",
        "RestrictSUIDSGID=yes",
        "LockPersonality=yes",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


__all__ = ["INSTALL_REL", "KEY_REL", "LauncherError", "START_SCRIPT", "STATE_REL", "UNIT_NAME", "UNIT_PATH",
           "install_root", "render_start_script", "render_systemd_unit"]
