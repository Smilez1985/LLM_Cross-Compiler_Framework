#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the shipped calibration data
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_calibration(_framework) -> List[Finding]:
    from orchestrator.utils.calibration import CALIBRATION_SETS, DATA_DIR, CalibrationDataError, load_set
    try:
        for name in CALIBRATION_SETS:
            load_set(name)
    except CalibrationDataError as e:
        return [Finding("data", "data.calibration", Severity.WARN, str(e),
                        "Restore data/calibration from the repository (git checkout -- data/calibration).",
                        where=str(DATA_DIR),
                        why="Builds that measure quantization quality with it would use damaged data or fail.",
                        cannot="not_automatic")]
    return [_ok("data", "data.calibration", "Calibration data intact")]
