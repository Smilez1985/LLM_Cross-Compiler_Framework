#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the per-user state folder is private
"""

import os
from pathlib import Path
from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_user_state(framework) -> List[Finding]:
    config = getattr(framework, "config", None)
    state_dir = Path(getattr(config, "user_state_dir", Path.home() / ".llm-framework"))
    if os.name != "posix":
        return [_ok("security", "state.dir", f"User state in {state_dir}")]
    mode = state_dir.stat().st_mode & 0o777
    if mode & 0o077:
        def tighten():
            state_dir.chmod(0o700)
        return [Finding("security", "state.permissions", Severity.WARN,
                        f"{state_dir} is readable by other users (mode {oct(mode)}); it holds tool permissions, "
                        "the audit log and chat history.", "Restrict it to your user.", fix_tier=1, fix=tighten,
                        where=str(state_dir), why="Other users of this computer could read your tool permissions, "
                                                  "the audit log and your chat with the AI.",
                        repair_id="state.tighten")]
    return [_ok("security", "state.permissions", f"User state private ({state_dir})")]
