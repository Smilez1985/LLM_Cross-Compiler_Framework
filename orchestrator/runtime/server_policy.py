#!/usr/bin/env python3
"""
The one policy for starting llama.cpp binaries (decision D22, design P13).

Every launcher the framework has takes the forbidden flags, the environment rules and the server options from here:
Ditto's server (llama_server.py: server_command, server_env), the build verification (build/verify.py: check_command,
cleared_env, server_tools_check_command), the dataset manager's perplexity check (check_command, cleared_env), the
device runtime's start script and systemd unit (device/launcher.py: SERVER_FIXED, START_SCRIPT_OPTIONS,
check_server_command, posix_cleared_env), the checks on a board over SSH (device/remote.py: check_command,
posix_cleared_env) and Ditto's server in a container (container_server.py: container_server_command,
check_container_server_command, check_publish).

- FORBIDDEN_FLAGS: llama-server options that give the model its own tools, file or shell access, MCP clients, a web
  UI, remote compute (RPC), state endpoints, downloads (Hugging Face, URLs, Docker Hub), a prompt log on disk,
  programs run from a folder, a port another process may bind as well (--reuse-port, SO_REUSEPORT), or a key or token
  on the command line. None of them may reach a command line, in any
  spelling llama.cpp accepts: every alias, and `--a_b` for `--a-b` (common/arg.cpp at the pin replaces the
  underscores of long options), so the check reads them the same way (option_name).
  Self-built servers are also compiled without subprocess support (LLAMA_SUBPROCESS=OFF), so even a forbidden
  --tools would fail; the list is still needed for the in-process file tools and the other options.
- Environment variables are a second way to set every llama-server option (LLAMA_ARG_*, LLAMA_API_KEY), and
  llama.cpp reads more of them: GGML_BACKEND_PATH loads a library into the server, AIP_MODE/AIP_HTTP_PORT move its
  port. So every launcher starts from a cleared environment (cleared_env on this machine, posix_cleared_env written
  out for a board: HOME, a fixed PATH, the C locale); server_env() for Ditto's server adds only the variables of
  SERVER_ENV_PASS from the caller's environment (which GPUs the server sees and where the GPU drivers are), nothing
  else: no LLAMA_*, no proxy, no token.
- Every other llama.cpp program the framework runs (llama-bench and llama-perplexity of the build verification, of
  the checks on a board and of the dataset manager's perplexity check) goes through check_command() and the cleared
  environment as well.
- A llama-server listens on the loopback address only and reads its API key from a file only its user can read: the
  key never appears on a command line or in a process listing, and the server never answers on another interface.
  check_server_command() holds every server command line to that (exactly one --host LOOPBACK, one --port, one
  --api-key-file, and the options of SERVER_FIXED: no web UI, no /slots, the chat template engine, offline);
  server_command() builds Ditto's, write_key_file() writes its key, and the device start script is generated from
  the same constants and checked with the same function.

- Ditto's server in a container (runtime mode "docker") keeps every one of these rules but one: inside the container's
  own network namespace it listens on CONTAINER_HOST, because Docker forwards a published port to the container's
  address, never to its loopback. The port is published on the host's loopback address only (check_publish), so the
  server is as unreachable from other computers as a server on the host's loopback; the per-start key still guards
  it against other containers. check_container_server_command() holds that command line to exactly one --host
  CONTAINER_HOST, the fixed container port, one --api-key-file and SERVER_FIXED.

SERVER_TOOLS_CHECK is the one deliberate exception: the build verification (and the checks on a board) start a
freshly built llama-server with --tools once, with a model path that does not exist and a cleared environment, and
require it to refuse.

The device runtime's start script passes on only the few tuning options of START_SCRIPT_OPTIONS (with checked values);
it refuses every option of FORBIDDEN_FLAGS by name and every other option that is not in START_SCRIPT_OPTIONS. The
KV-cache type (-ctk/-ctv, KV_CACHE_TYPES) is one of them: the deploy settings choose it for a board (eighth round N7),
and kv_cache_args() writes it out for the systemd unit and the start command the person is shown.
"""

import os
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

FORBIDDEN_FLAGS = frozenset({
    "--tools", "--tools-runtime", "-ag", "--agent", "--mcp-servers-config", "--mcp-servers-json",
    "--ui-mcp-proxy", "--webui-mcp-proxy", "--ui", "--webui", "--api-key", "--props", "--media-path",
    "--slot-save-path", "--models-dir", "--models-preset", "--path", "-hf", "--hf-repo", "-mu", "--model-url",
    "--rpc", "--slots", "--metrics",
    # The other names of the downloads at the pin (common/arg.cpp: -hf/-hfr/--hf-repo, the model file and the token
    # of Hugging Face, a model from Docker Hub, the draft model's Hugging Face repository, the multimodal projector's
    # URL), the prompt log on disk and the folder llama.cpp runs ffmpeg and ffprobe from. Every launcher also passes
    # --offline.
    "-hfr", "-hff", "--hf-file", "-hft", "--hf-token", "-dr", "--docker-repo", "-mmu", "--mmproj-url",
    "--spec-draft-hf", "-hfd", "-hfrd", "--hf-repo-draft", "--log-prompts-dir", "--video-ffmpeg-dir",
    # SO_REUSEPORT: another process of this user could bind the server's port beside it and take its connections
    # (the key and the prompts).
    "--reuse-port",
})
# What Ditto's server may still take from the caller's environment (decision D22: a cleared environment for every
# launcher): GPU selection of the CUDA, ROCm and Vulkan backends and where the GPU drivers and the Vulkan loader's
# driver manifests are. LD_LIBRARY_PATH stays because some systems find the NVIDIA driver library only through it;
# it already applies to the framework's own process.
SERVER_ENV_PASS = frozenset({
    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "GPU_DEVICE_ORDINAL",
    "HSA_OVERRIDE_GFX_VERSION", "GGML_VK_VISIBLE_DEVICES", "VK_ICD_FILENAMES", "VK_DRIVER_FILES",
    "VK_ADD_DRIVER_FILES", "LD_LIBRARY_PATH",
})
# The PATH a cleared environment gets: the binaries are always started by their absolute path, so this only serves
# whatever a C library itself might look up.
POSIX_CLEARED_PATH = "/usr/bin:/bin"
CLEARED_PATH = POSIX_CLEARED_PATH if os.name != "nt" else ""
# What a Windows process needs to start at all.
_WINDOWS_PASS = ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP")

# The options of the --tools check of the build verification (see the module docstring).
SERVER_TOOLS_CHECK = ("--tools", "all")
SERVER_TOOLS_REFUSAL = "subprocess is not enabled on this build"

# --- every llama-server: loopback, key file, fixed options ---
LOOPBACK = "127.0.0.1"
# Options every server gets after -m, --host, --port and --api-key-file (Ditto's server and the device start script
# alike): no web UI, no /slots (it shows the prompts), the chat template engine (tool calls need it), and never a
# download (the model is a local file).
SERVER_FIXED = ("--no-ui", "--no-slots", "--jinja", "--offline")
KEY_FILE_PREFIX = "server-"

# --- the device runtime (start script and systemd unit) ---
DEVICE_DEFAULT_PORT = 8080
# The KV-cache types a board's server may get (-ctk/--cache-type-k and -ctv/--cache-type-v at the pin, one type for
# both): f16 is llama.cpp's own default, q8_0 needs about half its memory and q4_0 about a quarter (with some loss of
# quality). A quantized V cache needs flash attention, which llama.cpp switches on by itself (-fa auto) where the
# backend supports it; otherwise the server does not start and says so.
KV_CACHE_TYPES = ("f16", "q8_0", "q4_0")
# The tuning options the start script passes on, each with the kind of value it accepts (checked in the script:
# uint digits, sint optional minus and digits, hex a CPU mask, bit 0 or 1, layers digits/auto/all, switch
# on/off/auto, loadmode one of llama.cpp's load modes, name letters, digits and ._:-, cachetype one of
# KV_CACHE_TYPES). Names as at the pin
# (common/arg.cpp); everything else is refused, so neither a forbidden option nor a second --host, --api-key-file or
# -m can be added.
START_SCRIPT_OPTIONS: Mapping[Tuple[str, ...], str] = {
    ("-c", "--ctx-size"): "uint",
    ("-t", "--threads"): "sint",
    ("-tb", "--threads-batch"): "sint",
    ("-C", "--cpu-mask"): "hex",
    ("--cpu-strict",): "bit",
    ("-b", "--batch-size"): "uint",
    ("-ub", "--ubatch-size"): "uint",
    ("-np", "--parallel"): "uint",
    ("-ngl", "--gpu-layers", "--n-gpu-layers"): "layers",
    ("-fa", "--flash-attn"): "switch",
    ("-cram", "--cache-ram"): "sint",
    ("-lm", "--load-mode"): "loadmode",
    ("-a", "--alias"): "name",
    ("--sleep-idle-seconds",): "uint",
    ("-ctk", "--cache-type-k"): "cachetype",
    ("-ctv", "--cache-type-v"): "cachetype",
}


class PolicyError(ValueError):
    """A command line that breaks the launch policy; the message names the options."""


def kv_cache_args(kind: Optional[str]) -> List[str]:
    """The server options of a KV-cache type: none for f16 (llama.cpp's default) or no type, else -ctk and -ctv with
    the same type (KV_CACHE_TYPES)."""
    if kind in (None, "f16"):
        return []
    if kind not in KV_CACHE_TYPES:
        raise PolicyError(f"the KV-cache type must be one of {', '.join(KV_CACHE_TYPES)}")
    return ["-ctk", kind, "-ctv", kind]


def option_name(arg: str) -> str:
    """The option an argument names, as llama.cpp reads it: `--a_b` is `--a-b` (common/arg.cpp replaces the
    underscores of long options), and a `--name=value` spelling is taken by its name."""
    name = str(arg).split("=", 1)[0]
    return name.replace("_", "-") if name.startswith("--") else name


def forbidden_in(cmd: Sequence[str]) -> List[str]:
    """The forbidden options in an argument list (also spelled --name=value or with underscores), sorted."""
    bad = set()
    for arg in list(cmd)[1:]:
        name = option_name(arg)
        if name in FORBIDDEN_FLAGS:
            bad.add(name)
    return sorted(bad)


def check_command(cmd: Sequence[str]) -> None:
    """Raises PolicyError when the command line holds a forbidden option."""
    bad = forbidden_in(cmd)
    if bad:
        raise PolicyError(f"Refusing to start llama-server with {bad}")


def server_env(base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """The environment of Ditto's server: a cleared environment (with the user's home folder) plus the GPU selection
    and driver variables of SERVER_ENV_PASS from the caller's environment. Nothing else passes."""
    env = dict(os.environ if base is None else base)
    home = env.get("HOME") or env.get("USERPROFILE") or str(Path.home())
    out = cleared_env(Path(home))
    out.update({k: v for k, v in env.items() if k.upper() in SERVER_ENV_PASS})
    return out


def posix_cleared_env(home: str) -> Dict[str, str]:
    """The cleared environment of a program on a Linux machine (also another one: the device start script and the
    verification over SSH write it out): HOME, the fixed PATH and the C locale, nothing else."""
    return {"HOME": str(home), "PATH": POSIX_CLEARED_PATH, "LANG": "C", "LC_ALL": "C"}


def cleared_env(home: Path, *, path: Optional[str] = None) -> Dict[str, str]:
    """An environment built from nothing: HOME, a fixed PATH and the C locale (plus what Windows needs to start a
    process). Nothing of the caller's environment reaches the binary: no LLAMA_ARG_*, no proxy, no token."""
    env = posix_cleared_env(str(home))
    env["PATH"] = CLEARED_PATH if path is None else path
    if os.name == "nt":  # pragma: no cover - Windows
        env.update({k: os.environ[k] for k in _WINDOWS_PASS if k in os.environ})
        env["USERPROFILE"] = str(home)
    return env


def tools_check_args(server: str, missing_model: str, port: int, key_file: str) -> List[str]:
    """The argument list of the --tools check, on this machine (server_tools_check_command) or written out for a
    board (device/remote.py): `--tools all` and a model path that does not exist, and otherwise exactly the server
    rules of check_server_command (loopback, one port, the key file, SERVER_FIXED), so even a server that accepted the
    tools would stop before it serves anything."""
    cmd = [str(server), *SERVER_TOOLS_CHECK, "-m", str(missing_model), "--host", LOOPBACK, "--port", str(int(port)),
           "--api-key-file", str(key_file), *SERVER_FIXED]
    check_server_command([cmd[0], *cmd[1 + len(SERVER_TOOLS_CHECK):]])  # everything but --tools keeps the rules
    return cmd


def server_tools_check_command(server: Path, missing_model: Path, port: int, key_file: Path) -> List[str]:
    """The command of the build verification's --tools check on this machine (tools_check_args), refused when the
    model path exists."""
    if Path(missing_model).exists():
        raise PolicyError("the --tools check needs a model path that does not exist")
    return tools_check_args(str(server), str(missing_model), port, str(key_file))


# --- a llama-server command line ---------------------------------------------------------------------------------------

def _values(cmd: Sequence[str], option: str) -> List[str]:
    """Every value of `option` in an argument list, in every spelling llama.cpp reads as that option (`--host X`,
    `--api_key_file X`; `--host=X`, which llama.cpp refuses, counts too): llama.cpp takes the last one, so a second
    one in another spelling must count."""
    args = [str(a) for a in list(cmd)[1:]]
    found = []
    for i, arg in enumerate(args):
        if option_name(arg) != option:
            continue
        if "=" in arg:
            found.append(arg.split("=", 1)[1])
        else:
            found.append(args[i + 1] if i + 1 < len(args) else "")
    return found


def check_server_command(cmd: Sequence[str]) -> None:
    """Raises PolicyError unless a llama-server command line keeps the policy: no forbidden option, exactly one --host
    and it is the loopback address, exactly one --port, exactly one --api-key-file (so the key is read from a file and
    never given on the command line), and every option of SERVER_FIXED."""
    check_command(cmd)
    hosts = _values(cmd, "--host")
    if hosts != [LOOPBACK]:
        raise PolicyError(f"llama-server must listen on {LOOPBACK} only (found --host {hosts or 'missing'})")
    if len(_values(cmd, "--port")) != 1:
        raise PolicyError("llama-server needs exactly one --port")
    _check_key_file_and_fixed(cmd)


def _check_key_file_and_fixed(cmd: Sequence[str]) -> None:
    """The rules every server command line shares: exactly one --api-key-file and every option of SERVER_FIXED."""
    keys = _values(cmd, "--api-key-file")
    if len(keys) != 1 or not keys[0]:
        raise PolicyError("llama-server needs exactly one --api-key-file (the key is never given on the command line)")
    names = {option_name(a) for a in list(cmd)[1:]}
    missing = [opt for opt in SERVER_FIXED if opt not in names]
    if missing:
        raise PolicyError(f"llama-server must be started with {', '.join(missing)}")


# --- Ditto's server in a container -------------------------------------------------------------------------------------

# Inside the container's own network namespace only; the port is published on LOOPBACK (check_publish), so it is
# reachable from this computer only.
CONTAINER_HOST = "0.0.0.0"  # nosec B104
CONTAINER_PORT = 8080


def check_container_server_command(cmd: Sequence[str]) -> None:
    """Raises PolicyError unless the command line of Ditto's server in a container keeps the policy: the rules of
    check_server_command, except that the one --host is CONTAINER_HOST (inside the container) and the one --port is
    CONTAINER_PORT, which check_publish allows to be published on the host's loopback address only."""
    check_command(cmd)
    hosts = _values(cmd, "--host")
    if hosts != [CONTAINER_HOST]:
        raise PolicyError(f"llama-server in Ditto's container listens on {CONTAINER_HOST} inside the container only "
                          f"(found --host {hosts or 'missing'})")
    if _values(cmd, "--port") != [str(CONTAINER_PORT)]:
        raise PolicyError(f"llama-server in Ditto's container needs exactly one --port {CONTAINER_PORT}")
    _check_key_file_and_fixed(cmd)


def container_server_command(server: str, model: str, *, key_file: str, alias: str,
                             extra: Sequence[str] = ()) -> List[str]:
    """The llama-server command line of Ditto's server in a container (paths inside the container): the model and its
    alias, CONTAINER_HOST and CONTAINER_PORT, the key file and the fixed options, then `extra`. Checked before it is
    returned."""
    cmd = [str(server), "-m", str(model), "--alias", str(alias), "--host", CONTAINER_HOST, "--port",
           str(CONTAINER_PORT), "--api-key-file", str(key_file), *SERVER_FIXED, *[str(a) for a in extra]]
    check_container_server_command(cmd)
    return cmd


def check_publish(ports: Mapping[str, object]) -> None:
    """Raises PolicyError unless the published ports of Ditto's server container (docker-py `ports`) are exactly
    CONTAINER_PORT/tcp on the host's loopback address with a fixed port: nothing else is published, and never on every
    interface of the host."""
    want = f"{CONTAINER_PORT}/tcp"
    if set(ports) != {want}:
        found = ", ".join(sorted(map(str, ports))) or "none"
        raise PolicyError(f"Ditto's container publishes only {want} (found {found})")
    binding = ports[want]
    if not (isinstance(binding, tuple) and len(binding) == 2 and binding[0] == LOOPBACK
            and isinstance(binding[1], int) and 0 < binding[1] < 65536):
        raise PolicyError(f"Ditto's container port is published on {LOOPBACK} with a fixed port only")


def server_command(server: Path, model: Path, *, port: int, key_file: Path, alias: str,
                   extra: Sequence[str] = ()) -> List[str]:
    """The llama-server command line of a server on this machine: the model and its alias, loopback, the port, the key
    file and the fixed options, then `extra` (context, cache, GPU layers, idle time). Checked before it is returned."""
    cmd = [str(server), "-m", str(model), "--alias", str(alias), "--host", LOOPBACK, "--port", str(int(port)),
           "--api-key-file", str(key_file), *SERVER_FIXED, *[str(a) for a in extra]]
    check_server_command(cmd)
    return cmd


def write_key_file(folder: Path) -> Tuple[Path, str]:
    """A new random API key in <folder>/server-<random>.key, created with mode 0600 (never readable by others, not even
    briefly) and never replacing an existing file. Returns (path, key)."""
    import secrets

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(32)
    path = folder / f"{KEY_FILE_PREFIX}{secrets.token_hex(8)}.key"
    # O_BINARY: on Windows a descriptor is in text mode otherwise and the key line ends in \r\n despite newline="\n";
    # llama-server in a Linux container (the file is mounted into it) would read the CR as part of the key.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(key + "\n")
    return path, key
