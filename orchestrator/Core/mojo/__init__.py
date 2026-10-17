#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo (the health check that was called the Doctor)

Deterministic health checks, like `flutter doctor`; the name comes from "Mojo" in the rag4ll project (sixth round,
decision 9). Every check returns findings with a stable code and a fix hint; nothing asks a model what is wrong.
Mojo Oracle (the default) only looks and reports what it found: what, where, why it matters and whether Mojo Miracle
can repair it. Mojo Miracle repairs what is harmless and reports what it repaired, what not and why (seventh round,
items 11', M' and T); it never deletes: deleting is the person's, from the framework's list (WP7).

- findings.py: Severity, Finding and the report (exit code 0 ok, 1 warnings, 2 errors).
- checks/: one module per area (python, docker, disk, targets, state, secrets, tools, chat_feed, calibration,
  community, knowledge, runtime).
- runner.py: Mojo runs the checks (Oracle; a check that crashes becomes a finding) and Mojo Miracle.
- offers.py: what Mojo Miracle can do about each warning and error (one verdict each, nothing skipped).
- repairs/: the harmless repairs (dry-run plans bound to a sha256, a class computed from the steps) and the engine
  that applies one (a lock, a fresh check, the audit first, compare-and-swap steps, a check afterwards).
- views.py: what an AI reads of the findings (folders by label, other paths by file name, secrets redacted).
- docker_objects.py: the framework's Docker images, listed for the person and deleted only as the person ticked and
  confirmed them in the framework's own window (never over MCP, never under a delegation).

`orchestrator/Core/doctor.py` stays as a compatibility module that re-exports all of this.
"""

from orchestrator.Core.mojo.checks import (FREE_SPACE_HINT, check_calibration, check_chat_feed, check_community,
                                           check_disk, check_docker, check_knowledge, check_python, check_runtime,
                                           check_secrets, check_targets, check_tool_policy, check_user_state)
from orchestrator.Core.mojo.docker_objects import (DockerUnavailable, ImageDeletion, ImagePlanChanged,
                                                   delete_framework_images, docker_storage, framework_image_plan,
                                                   framework_images)
from orchestrator.Core.mojo.findings import GB, Check, DoctorReport, Finding, MojoReport, Severity
from orchestrator.Core.mojo.offers import REASONS, VERDICTS, Offer, offer_for, offers
from orchestrator.Core.mojo.runner import DEFAULT_CHECKS, MiracleEntry, MiracleReport, Mojo
from orchestrator.Core.mojo.views import ai_findings, ai_folder_labels

__all__ = ["Check", "DEFAULT_CHECKS", "DockerUnavailable", "DoctorReport", "FREE_SPACE_HINT", "Finding", "GB",
           "ImageDeletion", "ImagePlanChanged", "MiracleEntry", "MiracleReport", "Mojo", "MojoReport", "Offer",
           "REASONS", "Severity", "VERDICTS", "ai_findings", "ai_folder_labels", "offer_for", "offers",
           "check_calibration", "check_chat_feed", "check_community", "check_disk", "check_docker",
           "check_knowledge", "check_python", "check_runtime", "check_secrets", "check_targets", "check_tool_policy",
           "check_user_state", "delete_framework_images", "docker_storage", "framework_image_plan",
           "framework_images"]
