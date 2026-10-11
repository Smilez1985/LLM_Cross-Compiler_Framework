#!/usr/bin/env python3
"""
Startup guard for environment variables that change what the provider SDKs send. Each entry point (CLI, GUI,
MCP server) runs it once, before any thread starts: changing os.environ while other threads read it is unsafe.

- OPENAI_CUSTOM_HEADERS / ANTHROPIC_CUSTOM_HEADERS add headers to every request of the SDK, to every endpoint
  (local servers and routers included), and an Authorization line in them can replace the stored key. They are
  dropped unless the setting ai_allow_sdk_env_headers is true (a real yes: a quoted "false" is no).
- ANTHROPIC_LOG=debug / OPENAI_LOG=debug make the SDKs log request details; lowered to info unless the user
  asked for it with --debug-http.
"""

import logging
import os
import sys
from typing import Any, List, MutableMapping, Optional

SDK_HEADER_VARIABLES = ("OPENAI_CUSTOM_HEADERS", "ANTHROPIC_CUSTOM_HEADERS")
# variable -> (package, loggers its setup_logging() switches to DEBUG on import)
SDK_LOG_VARIABLES = {"ANTHROPIC_LOG": ("anthropic", ("anthropic", "httpx2")), "OPENAI_LOG": ("openai", ("openai",))}


def setting_enabled(value: Any) -> bool:
    """A yes/no setting as a config file or the environment may hold it: only true/1/yes/on count as yes."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def guard_sdk_environment(allow_env_headers: Any = False, debug_http: bool = False,
                          environ: Optional[MutableMapping[str, str]] = None) -> List[str]:
    """Applies the rules above and returns one warning line per change. `allow_env_headers` is the setting's value
    or a callable returning it: the settings are then only read when a header variable is set."""
    env = os.environ if environ is None else environ
    warnings: List[str] = []
    present = [name for name in SDK_HEADER_VARIABLES if name in env]
    if present and not setting_enabled(allow_env_headers() if callable(allow_env_headers) else allow_env_headers):
        for name in present:
            del env[name]
            warnings.append(f"Ignoring {name}: headers from the environment could replace the API key or reach "
                            "other servers (set ai_allow_sdk_env_headers: true to keep them).")
    if not debug_http:
        for name, (package, loggers) in SDK_LOG_VARIABLES.items():
            if env.get(name, "").strip().lower() != "debug":
                continue
            env[name] = "info"
            if package in sys.modules:  # already imported: its logging was set up from the old value
                for logger_name in loggers:
                    logger = logging.getLogger(logger_name)
                    if logger.level == logging.DEBUG:
                        logger.setLevel(logging.INFO)
            warnings.append(f"{name}=debug lowered to info: SDK debug logs show request details "
                            "(use --debug-http to keep it).")
    return warnings
