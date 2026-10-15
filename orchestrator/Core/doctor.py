#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Doctor (compatibility module)

The Doctor is Mojo now (sixth round, decision 9): its checks, its report, what an AI reads of it and the deletion path
for the framework's Docker images live in the package `orchestrator.Core.mojo`, unchanged. This module re-exports every
name it had, so code written for the Doctor keeps working. `Doctor` is Mojo with the Doctor's tiered `fix(tier_max)`:
tier 1 makes the personal folder private and runs by itself; after fixing, everything is checked again, and a finding
that is still there is reported as not fixable automatically. Tier 2 deletes nothing by itself: the framework's build
images are listed for the person, who deletes exactly the ones ticked and confirmed in the framework's own window.
"""

from typing import List, Optional

from orchestrator.Core.mojo.checks import (FREE_SPACE_HINT, _storage_findings, check_calibration,  # noqa: F401
                                           check_chat_feed, check_community, check_disk, check_docker,
                                           check_knowledge, check_python, check_runtime, check_secrets,
                                           check_targets, check_tool_policy, check_user_state)
from orchestrator.Core.mojo.docker_objects import (_FRAMEWORK_TAG, _IMAGE_ID, _TARGET_WORD,  # noqa: F401
                                                   DockerUnavailable, ImageDeletion, ImagePlanChanged,
                                                   _docker_client, _framework_labels, _image_item,
                                                   delete_framework_images, docker_storage, framework_image_plan,
                                                   framework_images)
from orchestrator.Core.mojo.findings import GB, Check, DoctorReport, Finding, Severity, _ok  # noqa: F401
from orchestrator.Core.mojo.runner import DEFAULT_CHECKS, Mojo
from orchestrator.Core.mojo.views import ai_findings, ai_folder_labels  # noqa: F401


class Doctor(Mojo):
    """Mojo with the Doctor's tiered fixes. Its default checks are this module's DEFAULT_CHECKS, read when a Doctor is
    made, so code that replaced them here (tests do) keeps working."""

    def __init__(self, framework, checks: Optional[List[Check]] = None):
        super().__init__(framework, checks if checks is not None else list(DEFAULT_CHECKS))

    def fix(self, tier_max: int = 1) -> DoctorReport:
        before = self.run()
        fixed = []
        for finding in before.findings:
            if finding.fix is None or finding.fix_tier < 1 or finding.fix_tier > tier_max:
                continue
            if finding.severity not in (Severity.WARN, Severity.ERROR):
                continue
            try:
                finding.fix()
                fixed.append(finding.code)
            except Exception:
                pass
        after = self.run()
        problems = {f.code for f in after.findings if f.severity in (Severity.WARN, Severity.ERROR)}
        after.fixed = [code for code in fixed if code not in problems]
        after.still_present = [code for code in fixed if code in problems]
        return after


__all__ = ["Check", "DEFAULT_CHECKS", "DockerUnavailable", "Doctor", "DoctorReport", "FREE_SPACE_HINT", "Finding",
           "GB", "ImageDeletion", "ImagePlanChanged", "Severity", "ai_findings", "ai_folder_labels",
           "check_calibration", "check_chat_feed", "check_community", "check_disk", "check_docker",
           "check_knowledge", "check_python", "check_runtime", "check_secrets", "check_targets", "check_tool_policy",
           "check_user_state", "delete_framework_images", "docker_storage", "framework_image_plan",
           "framework_images"]
