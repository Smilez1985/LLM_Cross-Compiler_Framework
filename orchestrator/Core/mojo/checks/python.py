#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the Python version
"""

import sys
from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_python(_framework) -> List[Finding]:
    version = sys.version_info[:2]
    if (3, 11) <= version < (3, 15):
        return [_ok("python", "python.version", f"Python {version[0]}.{version[1]}")]
    return [Finding("python", "python.version", Severity.ERROR, f"Python {version[0]}.{version[1]} is not supported.",
                    "Use Python 3.11 to 3.14 (e.g. 'uv python install 3.13').")]
