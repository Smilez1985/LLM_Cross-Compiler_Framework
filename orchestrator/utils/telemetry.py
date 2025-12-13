#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Telemetry & Support (v2.3.0)
DIREKTIVE: Goldstandard, Privacy First (Opt-In), Sanitization.

Zweck:
Erstellt anonymisierte Fehlerberichte als GitHub Issue Links.
Sammelt keine Daten im Hintergrund, sondern bereitet sie für den User vor.
Entfernt sensible Pfade und Secrets (Sanitization).

Updates v2.3.0:
- Fixed f-string syntax error by separating log block construction.
- Dynamic version retrieval.
- Robust user path anonymization.
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
        self.framework = framework_manager
        
        # Safe config access (v2.3 ConfigManager)
        config = getattr(framework_manager, 'config', {})
        get_cfg = getattr(config, 'get', lambda k, d=None: d)
        
        self.enabled = get_cfg("enable_telemetry", False) # Standard: False (Opt-In)

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
            # Wir bereiten den Log-Teil separat vor, um f-String Syntaxfehler zu vermeiden
            sanitized_logs = self._sanitize(logs[-2000:] if logs else "No logs provided")
            
            # Konstruktion des Body-Strings (Sicherere Methode)
            body_parts = [
                "**Describe the bug**",
                "A clear description of what happened.",
                "",
                "**Context**",
                str(context),
                "",
                "**Error Message**",
                str(error),
                "",
                "**System Information**",
                f"- OS: {sys_info['os']}",
                f"- Python: {sys_info['python']}",
                f"- Framework Version: {sys_info['version']}",
                f"- Architecture: {sys_info['arch']}",
                "",
                "**Logs (Last 20 lines)**",
                "```text",
                sanitized_logs,
                "```"
            ]
            body = "\n".join(body_parts)

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
        """
        Entfernt PII (Personally Identifiable Information) und Secrets.
        """
        if not text: return ""
        
        # 1. API Keys (Basic Pattern Matching)
        text = re.sub(r'(sk-[a-zA-Z0-9]{20,})', 'sk-REDACTED', text)
        text = re.sub(r'(hf_[a-zA-Z0-9]{20,})', 'hf_REDACTED', text)
        
        # 2. User Paths (Robust)
        # Replaces /home/username with /home/USER
        try:
            home = os.path.expanduser("~")
            if home and len(home) > 1: # Avoid replacing "/" if home is weird
                text = text.replace(home, "~USER")
        except:
            pass # Fallback if env is broken
            
        # Fallback Username check
        username = os.getenv("USERNAME") or os.getenv("USER")
        if username and len(username) > 2: # Avoid replacing short names like "pi" accidentally globally without context
             # Simple heuristic: Only replace if preceded by path separator
             text = text.replace(f"/{username}", "/USER").replace(f"\\{username}", "\\USER")
            
        return text
