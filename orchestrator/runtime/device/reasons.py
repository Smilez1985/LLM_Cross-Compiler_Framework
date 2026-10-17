#!/usr/bin/env python3
"""
The old import path of the SSH reason codes (N6): they live in orchestrator/remote/reasons.py now. This name is that
module itself (not a copy of its names), so `from orchestrator.runtime.device import reasons`, the patches tests make
on it (reasons.check_port) and every caller of orchestrator.remote.reasons reach the same functions.
"""

import sys

from orchestrator.remote import reasons as _reasons

sys.modules[__name__] = _reasons
