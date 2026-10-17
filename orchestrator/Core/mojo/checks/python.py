#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the Python version

The version itself is judged by the framework's original check, utils/validation.validate_python_version (round 12:
the newer code uses the original where both do the same job); this module turns its verdict into Mojo's finding.
"""

import sys
from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

# The supported range: 3.11 up to, not including, 3.15 (pyproject's requires-python and the CI matrix).
SUPPORTED_FROM, SUPPORTED_BELOW = "3.11", "3.15"


def check_python(_framework) -> List[Finding]:
    from orchestrator.utils.validation import validate_python_version
    version = sys.version_info[:2]
    current = ".".join(str(part) for part in sys.version_info[:3])
    if validate_python_version(SUPPORTED_FROM, below=SUPPORTED_BELOW, current=current):
        return [_ok("python", "python.version", f"Python {version[0]}.{version[1]}")]
    return [Finding("python", "python.version", Severity.ERROR, f"Python {version[0]}.{version[1]} is not supported.",
                    "Use Python 3.11 to 3.14 (e.g. 'uv python install 3.13').", where=sys.executable,
                    why="The framework is made and tested for Python 3.11 to 3.14; other versions can fail anywhere.",
                    cannot="admin")]
