#!/usr/bin/env python3
"""
The one policy for starting llama.cpp binaries (decision D22, design P13).

Every launcher the framework has (Ditto's server, the build verification, the device start script and the systemd
unit of the device runtime, and later the Docker command) takes the forbidden flags and the environment rules from
here:

- FORBIDDEN_FLAGS: llama-server options that give the model its own tools, file or shell access, MCP clients, a web
  UI, remote compute (RPC), state endpoints or a key on the command line. None of them may reach a command line, in
  any spelling llama.cpp accepts: it reads `--a_b` as `--a-b` (common/arg.cpp at the pin), so the check does too.
  Self-built servers are also compiled without subprocess support (LLAMA_SUBPROCESS=OFF), so even a forbidden
  --tools would fail; the list is still needed for the in-process file tools and the other options.
- Environment variables are a second way to set every llama-server option (LLAMA_ARG_*, LLAMA_API_KEY), and
  llama.cpp reads more of them: GGML_BACKEND_PATH loads a library into the server, AIP_MODE/AIP_HTTP_PORT move its
  port. So every launcher starts from a cleared environment (cleared_env: HOME, a fixed PATH, the C locale);
  server_env() for Ditto's server adds only the variables of SERVER_ENV_PASS from the caller's environment (which GPUs
  the server sees and where the GPU drivers are), nothing else: no LLAMA_*, no proxy, no token.

SERVER_TOOLS_CHECK is the one deliberate exception: the build verification starts a freshly built llama-server with
--tools once, with a model path that does not exist and a cleared environment, and requires it to refuse.

The device runtime's start script (orchestrator/runtime/device/launcher.py) is generated from DEVICE_SERVER_FIXED
(loopback, the key file, no web UI, no slots, offline) and START_SCRIPT_OPTIONS, the few tuning options it passes on;
it refuses every option of FORBIDDEN_FLAGS by name and every other option that is not in START_SCRIPT_OPTIONS.
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
POSIX_CLEARED_PATH = "/usr/bin:/bin"
CLEARED_PATH = POSIX_CLEARED_PATH if os.name != "nt" else ""
# What a Windows process needs to start at all.
_WINDOWS_PASS = ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP")

# The options of the --tools check of the build verification (see the module docstring).
SERVER_TOOLS_CHECK = ("--tools", "all")
SERVER_TOOLS_REFUSAL = "subprocess is not enabled on this build"

# --- the device runtime (start script and systemd unit) ---
LOOPBACK = "127.0.0.1"
DEVICE_DEFAULT_PORT = 8080
# Every server the start script starts gets these options after `-m MODEL --host 127.0.0.1 --port PORT
# --api-key-file KEY`: no web UI, no /slots (it shows the prompts), the chat template engine, and never a download.
DEVICE_SERVER_FIXED = ("--no-ui", "--no-slots", "--jinja", "--offline")
# The tuning options the start script passes on, each with the kind of value it accepts (checked in the script:
# uint digits, sint optional minus and digits, hex a CPU mask, bit 0 or 1, layers digits/auto/all, switch
# on/off/auto, loadmode one of llama.cpp's load modes, name letters, digits and ._:-). Names as at the pin
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
}


class PolicyError(ValueError):
    """A command line that breaks the launch policy; the message names the options."""


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


def server_tools_check_command(server: Path, missing_model: Path, port: int, key_file: Path) -> List[str]:
    """The command of the build verification's --tools check: loopback, a key file, no UI, offline, and a model path
    that does not exist, so even a server that accepted the tools would stop before it serves anything."""
    if Path(missing_model).exists():
        raise PolicyError("the --tools check needs a model path that does not exist")
    return [str(server), *SERVER_TOOLS_CHECK, "-m", str(missing_model), "--host", "127.0.0.1", "--port", str(port),
            "--api-key-file", str(key_file), "--no-ui", "--offline"]
