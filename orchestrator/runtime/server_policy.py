#!/usr/bin/env python3
"""
The one policy for starting llama.cpp binaries (decision D22, design P13).

Every launcher the framework has (Ditto's server, the build verification, and later the device start script, the
systemd unit and the Docker command) takes the forbidden flags and the environment rules from here:

- FORBIDDEN_FLAGS: llama-server options that give the model its own tools, file or shell access, MCP clients, a web
  UI, remote compute (RPC), state endpoints or a key on the command line. None of them may reach a command line.
  Self-built servers are also compiled without subprocess support (LLAMA_SUBPROCESS=OFF), so even a forbidden
  --tools would fail; the list is still needed for the in-process file tools and the other options.
- Environment variables are a second way to set every llama-server option (LLAMA_ARG_*, LLAMA_API_KEY):
  server_env() drops them and every variable that looks like a secret; cleared_env() starts from nothing.

SERVER_TOOLS_CHECK is the one deliberate exception: the build verification starts a freshly built llama-server with
--tools once, with a model path that does not exist and a cleared environment, and requires it to refuse.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

FORBIDDEN_FLAGS = frozenset({
    "--tools", "--tools-runtime", "-ag", "--agent", "--mcp-servers-config", "--mcp-servers-json",
    "--ui-mcp-proxy", "--webui-mcp-proxy", "--ui", "--webui", "--api-key", "--props", "--media-path",
    "--slot-save-path", "--models-dir", "--models-preset", "--path", "-hf", "--hf-repo", "-mu", "--model-url",
    "--rpc", "--slots", "--metrics",
})
_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH", re.IGNORECASE)
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
    """The environment of Ditto's server: the caller's, without LLAMA_* (option twins) and without secrets."""
    env = dict(os.environ if base is None else base)
    return {k: v for k, v in env.items() if not k.upper().startswith("LLAMA_") and not _SECRET_ENV.search(k)}


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
