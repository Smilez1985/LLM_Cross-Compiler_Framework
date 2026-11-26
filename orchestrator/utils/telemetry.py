#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the person's opt-in error report

With the setting enable_telemetry (off by default; only the person sets it), an unexpected error can be reported as a
GitHub issue: the crash dialog's "Report on GitHub…" opens GitHub's form for a new issue filled with the details the
dialog shows (utils/crash_report.crash_details and issue_link: the traceback, llmcf's version, Python and the system,
secrets redacted and the home folder written as "~"). Nothing is collected in the background, and nothing is sent until
the person reads the form in the browser and submits it.
"""

import platform
import sys
from typing import Dict

from orchestrator.utils.crash_report import ISSUE_URL, crash_details, hide_home, issue_link, telemetry_enabled
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

GITHUB_REPO_URL = ISSUE_URL   # kept under its old name


class TelemetryManager:
    """
    The person's opt-in error report (setting enable_telemetry, off by default): a link to GitHub's form for a new
    issue, filled with the crash dialog's details (utils/crash_report.issue_link). The crash dialog offers it as
    "Report on GitHub…"; nothing is collected in the background and nothing is sent until the person submits the form.
    """

    def __init__(self, framework_manager):
        self.logger = get_logger(__name__)
        self.framework = framework_manager
        self.enabled = telemetry_enabled(getattr(framework_manager, 'config', None))   # Opt-In

    def generate_issue_link(self, error: Exception, context: str = "", logs: str = "") -> str:
        """The form's address for `error` (crash_details, `where` = `context`) with the end of `logs`, scrubbed; ""
        when the person did not opt in or it cannot be made."""
        if not self.enabled:
            self.logger.debug("Telemetry disabled. Skipping report generation.")
            return ""
        try:
            details = crash_details(error, str(context or ""))
            if logs:
                details += "\nLog (end):\n" + self._sanitize(logs[-2000:])
            return issue_link(details)
        except Exception as e:
            self.logger.error(f"Failed to generate telemetry link: {e}")
            return ""

    def _get_system_info(self) -> Dict[str, str]:
        """Sammelt harmlose Systemdaten."""
        # Get dynamic version if available
        version = "Unknown"
        if hasattr(self.framework, 'info') and hasattr(self.framework.info, 'version'):
            version = self.framework.info.version
            
        return {
            "os": f"{platform.system()} {platform.release()}",
            "python": sys.version.split()[0],
            "arch": platform.machine(),
            "version": version
        }

    def _sanitize(self, text: str) -> str:
        """Secrets redacted (helpers.redact_secrets), this computer's home folder as "~USER" in every spelling and the
        account's name after a separator as USER (crash_report.hide_home, which the crash dialog's report uses too);
        an empty text stays empty."""
        if not text:
            return ""
        return hide_home(redact_secrets(text))
