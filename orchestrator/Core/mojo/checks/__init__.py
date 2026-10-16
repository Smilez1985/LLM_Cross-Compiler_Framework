#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo's checks, one module per area

Each check takes the framework and returns its findings; it only reads (the chat feed's probe write is rolled back).
A check that crashes becomes the finding `<area>.check_crashed` (runner.Mojo).
"""

from orchestrator.Core.mojo.checks.calibration import check_calibration
from orchestrator.Core.mojo.checks.chat_feed import check_chat_feed
from orchestrator.Core.mojo.checks.community import check_community
from orchestrator.Core.mojo.checks.disk import FREE_SPACE_HINT, check_disk
from orchestrator.Core.mojo.checks.docker import _storage_findings, check_docker
from orchestrator.Core.mojo.checks.knowledge import check_knowledge
from orchestrator.Core.mojo.checks.python import check_python
from orchestrator.Core.mojo.checks.runtime import check_runtime
from orchestrator.Core.mojo.checks.secrets import check_secrets
from orchestrator.Core.mojo.checks.state import check_user_state
from orchestrator.Core.mojo.checks.targets import check_targets
from orchestrator.Core.mojo.checks.tools import check_tool_policy

__all__ = ["FREE_SPACE_HINT", "_storage_findings", "check_calibration", "check_chat_feed", "check_community",
           "check_disk", "check_docker", "check_knowledge", "check_python", "check_runtime", "check_secrets",
           "check_targets", "check_tool_policy", "check_user_state"]
