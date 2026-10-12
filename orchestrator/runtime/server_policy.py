#!/usr/bin/env python3
"""
The one policy for starting llama.cpp binaries (decision D22, design P13).

Every launcher the framework has (Ditto's server, the build verification, and later the device start script, the
systemd unit and the Docker command) takes the forbidden flags and the environment rules from here:

- FORBIDDEN_FLAGS: llama-server options that give the model its own tools, file or shell access, MCP clients, a web
  UI, remote compute (RPC), state endpoints or a key on the command line. None of them may reach a command line.
  Self-built servers are also compiled without subprocess support (LLAMA_SUBPROCESS=OFF), so even a forbidden
  --tools would fail; the list is still needed for the in-process file tools and the other options.
- Environment variables are a second way to set every llama-server option (LLAMA_ARG_*, LLAMA_API_KEY), and
  llama.cpp reads more of them: GGML_BACKEND_PATH loads a library into the server, AIP_MODE/AIP_HTTP_PORT move its
  port. So every launcher starts from a cleared environment (cleared_env: HOME, a fixed PATH, the C locale);
  server_env() for Ditto's server adds only the variables of SERVER_ENV_PASS from the caller's environment (which GPUs
  the server sees and where the GPU drivers are), nothing else: no LLAMA_*, no proxy, no token.
- Every other llama.cpp program the framework runs (llama-bench and llama-perplexity of the build verification and of
  the dataset manager's perplexity check) goes through check_command() and cleared_env() as well.
- A llama-server listens on the loopback address only and reads its API key from a file only the user can read
  (server_command, check_server_command, write_key_file at the end of this module): the key never appears on a
  command line or in a process listing, and the server never answers on another interface.

SERVER_TOOLS_CHECK is the one deliberate exception: the build verification starts a freshly built llama-server with
--tools once, with a model path that does not exist and a cleared environment, and requires it to refuse.
"""

import os
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

FORBIDDEN_FLAGS = frozenset({
    "--tools", "--tools-runtime", "-ag", "--agent", "--mcp-servers-config", "--mcp-servers-json",
    "--ui-mcp-proxy", "--webui-mcp-proxy", "--ui", "--webui", "--api-key", "--props", "--media-path",
    "--slot-save-path", "--models-dir", "--models-preset", "--path", "-hf", "--hf-repo", "-mu", "--model-url",
    "--rpc", "--slots", "--metrics",
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
CLEARED_PATH = "/usr/bin:/bin" if os.name != "nt" else ""
# What a Windows process needs to start at all.
_WINDOWS_PASS = ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP")

# The options of the --tools check of the build verification (see the module docstring).
SERVER_TOOLS_CHECK = ("--tools", "all")
SERVER_TOOLS_REFUSAL = "subprocess is not enabled on this build"


class PolicyError(ValueError):
    """A command line that breaks the launch policy; the message names the options."""


def forbidden_in(cmd: Sequence[str]) -> List[str]:
    """The forbidden options in an argument list (also in the --name=value spelling), sorted."""
    bad = set()
    for arg in list(cmd)[1:]:
        name = str(arg).split("=", 1)[0]
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


def cleared_env(home: Path, *, path: Optional[str] = None) -> Dict[str, str]:
    """An environment built from nothing: HOME, a fixed PATH and the C locale (plus what Windows needs to start a
    process). Nothing of the caller's environment reaches the binary: no LLAMA_ARG_*, no proxy, no token."""
    env = {"HOME": str(home), "PATH": CLEARED_PATH if path is None else path, "LANG": "C", "LC_ALL": "C"}
    if os.name == "nt":  # pragma: no cover - Windows
        env.update({k: os.environ[k] for k in _WINDOWS_PASS if k in os.environ})
        env["USERPROFILE"] = str(home)
    return env


def server_tools_check_command(server: Path, missing_model: Path, port: int, key_file: Path) -> List[str]:
    """The command of the build verification's --tools check: loopback, a key file, no UI, offline, and a model path
    that does not exist, so even a server that accepted the tools would stop before it serves anything."""
    if Path(missing_model).exists():
        raise PolicyError("the --tools check needs a model path that does not exist")
    return [str(server), *SERVER_TOOLS_CHECK, "-m", str(missing_model), "--host", "127.0.0.1", "--port", str(port),
            "--api-key-file", str(key_file), "--no-ui", "--offline"]


# --- a llama-server on this machine: loopback, key file, fixed options ------------------------------------------------
LOOPBACK = "127.0.0.1"
# Options every server gets after -m, --host, --port and --api-key-file: no web UI, no /slots (it shows the prompts),
# the chat template engine (tool calls need it), and never a download (the model is a local file).
SERVER_FIXED = ("--no-ui", "--no-slots", "--jinja", "--offline")
KEY_FILE_PREFIX = "server-"


def _values(cmd: Sequence[str], option: str) -> List[str]:
    """Every value of `option` in an argument list, in both spellings (`--host X` and `--host=X`)."""
    args = [str(a) for a in list(cmd)[1:]]
    found = []
    for i, arg in enumerate(args):
        if arg == option:
            found.append(args[i + 1] if i + 1 < len(args) else "")
        elif arg.startswith(option + "="):
            found.append(arg[len(option) + 1:])
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
    keys = _values(cmd, "--api-key-file")
    if len(keys) != 1 or not keys[0]:
        raise PolicyError("llama-server needs exactly one --api-key-file (the key is never given on the command line)")
    missing = [opt for opt in SERVER_FIXED if opt not in cmd]
    if missing:
        raise PolicyError(f"llama-server must be started with {', '.join(missing)}")


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
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(key + "\n")
    return path, key
