#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo's runner

Runs the checks in a fixed order; a check that crashes is itself a finding (`<area>.check_crashed`), never a crash of
Mojo.
"""

from typing import List, Optional

from orchestrator.Core.mojo.checks import (check_calibration, check_chat_feed, check_community, check_disk,
                                           check_docker, check_knowledge, check_python, check_runtime, check_secrets,
                                           check_targets, check_tool_policy, check_user_state)
from orchestrator.Core.mojo.findings import Check, Finding, MojoReport, Severity

DEFAULT_CHECKS: List[Check] = [check_python, check_docker, check_disk, check_targets, check_user_state,
                               check_secrets, check_tool_policy, check_chat_feed, check_calibration, check_community,
                               check_knowledge, check_runtime]


class Mojo:
    def __init__(self, framework, checks: Optional[List[Check]] = None):
        self.framework = framework
        self.checks = checks if checks is not None else list(DEFAULT_CHECKS)

    def run(self) -> MojoReport:
        findings = []
        for check in self.checks:
            try:
                findings.extend(check(self.framework))
            except Exception as e:  # a broken check is itself a finding, never a crash of Mojo
                name = getattr(check, "__name__", "check").replace("check_", "")
                findings.append(Finding(name, f"{name}.check_crashed", Severity.ERROR, f"The check failed: {e}"))
        return MojoReport(findings)
