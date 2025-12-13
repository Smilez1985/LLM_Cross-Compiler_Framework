#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Telemetry & Support
DIREKTIVE: Goldstandard, Privacy First (Opt-In), Sanitization.

Zweck:
Erstellt anonymisierte Fehlerberichte als GitHub Issue Links.
Sammelt keine Daten im Hintergrund, sondern bereitet sie für den User vor.
Entfernt sensible Pfade und Secrets (Sanitization).
"""

import sys
import platform
import urllib.parse
import re
import os
from typing import Optional, Dict, Any
from datetime import datetime

from orchestrator.utils.logging import get_logger

GITHUB_REPO_URL = "https://github.com/Smilez1985/llm_conversion_framework/issues/new"

class TelemetryManager:
    """
    Verwaltet Fehlerberichterstattung und Anonymisierung.
    """
    
    def __init__(self, framework_manager):
        self.logger = get_logger(__name__)
        self.config = framework_manager.config
        self.enabled = self.config.get("enable_telemetry", False) # Standard: False (Opt-In)

    def generate_issue_link(self, error: Exception, context: str = "", logs: str = "") -> str:
        """
        Generiert einen mailto-artigen Link für ein neues GitHub Issue.
        """
        if not self.enabled:
            self.logger.debug("Telemetry disabled. Skipping report generation.")
            return ""

        try:
            # 1. System Info sammeln
            sys_info = self._get_system_info()
            
            # 2. Report Body bauen
            body = f"""
**Describe the bug**
A clear description of what happened.

**Context**
{context}

**Error Message**
{str(error)}


**System Information**
- OS: {sys_info['os']}
- Python: {sys_info['python']}
- Framework Version: {sys_info['version']}
- Architecture: {sys_info['arch']}

**Logs (Last 20 lines)**
{self._sanitize(logs[-2000:] if logs else "No logs provided")}

"""
            # 3. URL Encoding
            params = {
                "title": f"[Bug]: {str(error)[:60]}...",
                "body": body,
                "labels": "bug,automated-report"
            }
            query_string = urllib.parse.urlencode(params)
            full_url = f"{GITHUB_REPO_URL}?{query_string}"
            
            self.logger.info("Generated GitHub Issue Link (Sanitized).")
            return full_url
            
        except Exception as e:
            self.logger.error(f"Failed to generate telemetry link: {e}")
            return ""

    def _get_system_info(self) -> Dict[str, str]:
        """Sammelt harmlose Systemdaten."""
        return {
            "os": f"{platform.system()} {platform.release()}",
            "python": sys.version.split()[0],
            "arch": platform.machine(),
            "version": "2.0.0" # Hardcoded or from framework info
        }

    def _sanitize(self, text: str) -> str:
        """
        Entfernt PII (Personally Identifiable Information) und Secrets.
        """
        if not text: return ""
        
        # 1. API Keys (sk-...)
        text = re.sub(r'(sk-[a-zA-Z0-9]{20,})', 'sk-REDACTED', text)
        text = re.sub(r'(hf_[a-zA-Z0-9]{20,})', 'hf_REDACTED', text)
        
        # 2. User Paths (Windows/Linux)
        # Ersetzt C:\Users\Name\... mit C:\Users\USER\...
        # Ersetzt /home/name/... mit /home/USER/...
        
        # Username ermitteln
        username = os.getenv("USERNAME") or os.getenv("USER")
        if username:
            text = text.replace(username, "USER")
            
        return text
