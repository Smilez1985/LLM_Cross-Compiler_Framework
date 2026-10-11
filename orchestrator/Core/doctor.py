#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Doctor

Deterministic health checks, like `flutter doctor` (the idea comes from "Mojo" in the rag4ll project):
every check returns findings with a stable code and a fix hint; nothing asks a model what is wrong.
Fixes are tiered - 1: harmless (create a folder, tighten permissions), 2: removes data (caches) - and
run only when the user asks for that tier. After fixing, everything is checked again; a finding that is
still there is reported as not fixable automatically.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class Finding:
    subsystem: str
    code: str
    severity: Severity
    message: str
    fix_hint: str = ""
    fix_tier: int = 0  # 0: no automatic fix
    fix: Optional[Callable[[], None]] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict:
        data = asdict(self)
        data.pop("fix")
        data["severity"] = self.severity.value
        data["fixable"] = self.fix is not None
        return data


Check = Callable[[object], List[Finding]]

GB = 1024 ** 3


def _ok(subsystem: str, code: str, message: str) -> Finding:
    return Finding(subsystem, code, Severity.OK, message)


def check_python(_framework) -> List[Finding]:
    version = sys.version_info[:2]
    if (3, 11) <= version < (3, 15):
        return [_ok("python", "python.version", f"Python {version[0]}.{version[1]}")]
    return [Finding("python", "python.version", Severity.ERROR, f"Python {version[0]}.{version[1]} is not supported.",
                    "Use Python 3.11 to 3.14 (e.g. 'uv python install 3.13').")]


def check_docker(framework) -> List[Finding]:
    client = framework.get_component("docker_client") if hasattr(framework, "get_component") else None
    findings = []
    try:
        if client is None:
            raise RuntimeError("no client")
        client.ping()
        findings.append(_ok("docker", "docker.daemon", "Docker daemon reachable"))
    except Exception:
        return [Finding("docker", "docker.daemon", Severity.ERROR, "The Docker daemon is not reachable; builds cannot run.",
                        "Start Docker (Docker Desktop on Windows/macOS, 'sudo systemctl start docker' on Linux) and make "
                        "sure your user may use it (docker group or rootless Docker).")]
    if shutil.which("docker"):
        try:
            buildx = subprocess.run(["docker", "buildx", "version"], capture_output=True, text=True, timeout=15)
            if buildx.returncode == 0:
                findings.append(_ok("docker", "docker.buildx", "BuildKit (buildx) available"))
            else:
                findings.append(Finding("docker", "docker.buildx", Severity.WARN,
                                        "docker buildx is missing; image builds fall back to the slower legacy builder.",
                                        "Install the Docker buildx plugin."))
        except (OSError, subprocess.SubprocessError):
            pass
    return findings


def check_disk(framework) -> List[Finding]:
    findings = []
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    seen = set()
    for label, path in (("cache", getattr(engine, "cache_dir", None)), ("output", getattr(engine, "output_dir", None))):
        if path is None:
            continue
        path = Path(path)
        probe = path if path.exists() else path.parent
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            continue
        key = (os.stat(probe).st_dev if probe.exists() else str(probe))
        if key in seen:
            continue
        seen.add(key)
        free = usage.free / GB
        if free < 5:
            findings.append(Finding("disk", f"disk.{label}", Severity.ERROR,
                                    f"Only {free:.1f} GB free for the {label} folder ({path}).",
                                    "Free disk space: 'llm-cli doctor --fix --tier 2' removes dangling framework images "
                                    "and the Docker build cache; large models need 2-3x their size during conversion.",
                                    fix_tier=2, fix=_prune_docker(framework)))
        elif free < 20:
            findings.append(Finding("disk", f"disk.{label}", Severity.WARN,
                                    f"{free:.1f} GB free for the {label} folder; large models need more.",
                                    "Free disk space before converting models larger than a few GB.",
                                    fix_tier=2, fix=_prune_docker(framework)))
        else:
            findings.append(_ok("disk", f"disk.{label}", f"{free:.0f} GB free for {label}"))
    return findings


def _prune_docker(framework) -> Optional[Callable[[], None]]:
    client = framework.get_component("docker_client") if hasattr(framework, "get_component") else None
    if client is None:
        return None

    def prune():
        from orchestrator.Core.builder import IMAGE_LABEL
        client.images.prune(filters={"dangling": True, "label": IMAGE_LABEL})
        client.api.prune_builds()
    return prune


def check_targets(framework) -> List[Finding]:
    tm = getattr(framework, "target_manager", None)
    if tm is None:
        return [Finding("targets", "targets.registry", Severity.ERROR, "Target registry not loaded.")]
    findings = []
    targets = tm.list_targets()
    if not targets:
        return [Finding("targets", "targets.none", Severity.ERROR, f"No targets found in {tm.targets_dir}.",
                        "Check the setting targets_dir.")]
    for meta in targets:
        tid = meta.get("id")
        if tm.get_capabilities(tid) is None:
            findings.append(Finding("targets", f"targets.{tid}.capabilities", Severity.WARN,
                                    f"Target {tid} declares no valid capabilities; GUI, CLI and AI clients cannot "
                                    "check its build settings.", "Add a 'capabilities:' block to its target.yml "
                                    "(see targets/Rockchip/target.yml)."))
        else:
            findings.append(_ok("targets", f"targets.{tid}", f"Target {tid}"))
    return findings


def check_user_state(framework) -> List[Finding]:
    config = getattr(framework, "config", None)
    state_dir = Path(getattr(config, "user_state_dir", Path.home() / ".llm-framework"))
    if os.name != "posix":
        return [_ok("security", "state.dir", f"User state in {state_dir}")]
    mode = state_dir.stat().st_mode & 0o777
    if mode & 0o077:
        def tighten():
            state_dir.chmod(0o700)
        return [Finding("security", "state.permissions", Severity.WARN,
                        f"{state_dir} is readable by other users (mode {oct(mode)}); it holds tool permissions, "
                        "the audit log and chat history.", "Restrict it to your user.", fix_tier=1, fix=tighten)]
    return [_ok("security", "state.permissions", f"User state private ({state_dir})")]


def check_secrets(framework) -> List[Finding]:
    sm = getattr(framework, "secrets_manager", None)
    if sm is None:
        return [Finding("security", "secrets.store", Severity.ERROR, "The secrets store is not available.")]
    refresh = getattr(sm, "refresh", None)
    if callable(refresh):
        refresh()  # a long-running GUI or MCP server: another process may have changed the store
    if getattr(sm, "_load_failed", False):
        failed = getattr(sm, "failed_file", None) or sm.secrets_file
        reason = getattr(sm, "load_error", None)
        reason = f" ({reason})" if isinstance(reason, str) and reason else ""
        if failed != sm.secrets_file:
            return [Finding("security", "secrets.store", Severity.ERROR,
                            f"The old secrets store {failed} cannot be read, so its secrets were not taken over "
                            f"into {sm.secrets_file}; secrets cannot be changed.{reason}",
                            "Repair the file (its secrets are then taken over) or move it aside.")]
        return [Finding("security", "secrets.store", Severity.ERROR,
                        f"The secrets store {sm.secrets_file} cannot be read; secrets cannot be changed.{reason}",
                        "Repair the file or move it aside and set the secrets again (llm-cli secrets set).")]
    findings = [_ok("security", "secrets.store", "Secrets store ready")]
    mode = getattr(sm, "storage_mode", None)
    if mode == "insecure":
        findings.append(Finding("security", "secrets.storage", Severity.WARN,
                                "The vault key is stored in a file next to the vault (secret_storage: insecure): "
                                "credentials are not encrypted at rest.",
                                "Use the OS keyring, a credential helper or a passphrase (setting secret_storage)."))
    elif mode == "ephemeral":
        findings.append(Finding("security", "secrets.storage", Severity.INFO,
                                "Credentials are kept in memory only (secret_storage: ephemeral); nothing is stored."))
    if mode not in (None, "keyring"):
        return findings
    try:
        import keyring
        backend = type(keyring.get_keyring()).__module__ + "." + type(keyring.get_keyring()).__name__
        if any(name in backend.lower() for name in ("null", "fail", "plaintext")):
            findings.append(Finding("security", "secrets.keyring", Severity.WARN,
                                    f"The keyring backend ({backend}) does not protect secrets.",
                                    "Install a system keyring (e.g. gnome-keyring or KWallet), or choose a "
                                    "credential helper or a passphrase (setting secret_storage)."))
    except Exception:
        pass
    return findings


def check_tool_policy(framework) -> List[Finding]:
    registry = getattr(framework, "tool_registry", None)
    if registry is None:
        return [Finding("ai", "tools.registry", Severity.WARN, "Tool registry not loaded.")]
    findings = []
    policy = registry.policy()
    problem = getattr(registry.policy_store, "problem", None)
    if problem:
        findings.append(Finding("ai", "tools.permissions", Severity.ERROR, problem,
                                "Run 'llm-cli tools permissions' (or Tools > Tool Permissions in the GUI) to set "
                                "them up again."))
    elif not policy.configured:
        findings.append(Finding("ai", "tools.permissions", Severity.INFO,
                                "Tool permissions are not set up yet (safe defaults apply: read allowed, everything "
                                "else asks).", "Run 'llm-cli ditto setup' or use Tools > Tool Permissions in the GUI."))
    else:
        findings.append(_ok("ai", "tools.permissions", "Tool permissions set up"))
    if registry.audit is not None:
        archive = ("'llm-cli tools audit --archive' moves it and its anchor aside (kept with a timestamp) and "
                   "starts a new log.")
        try:
            result = registry.audit.check()
        except Exception as e:
            return findings + [Finding("ai", "tools.audit", Severity.ERROR,
                                       f"The tool audit log {registry.audit.path} cannot be opened: {e}",
                                       f"Keep the damaged file for inspection: {archive}")]
        # A log this key cannot verify may just as well have been rewritten, so it is no lesser finding.
        if result.problem in ("modified", "removed", "key_mismatch"):
            findings.append(Finding("ai", "tools.audit", Severity.ERROR, result.message,
                                    f"Inspect {registry.audit.path}; {archive}"))
        elif result.problem:
            findings.append(Finding("ai", "tools.audit", Severity.WARN, result.message,
                                    f"Inspect {registry.audit.path}; {archive}"))
        elif not result.keyed:
            findings.append(Finding("ai", "tools.audit", Severity.WARN, result.message,
                                    "The next tool call adds a keyed entry, which protects the whole chain."
                                    if result.key_available else
                                    "Install a system keyring (e.g. gnome-keyring or KWallet), so the audit chain "
                                    "is protected by a key."))
        else:
            findings.append(_ok("ai", "tools.audit", result.message))
    return findings


def check_calibration(_framework) -> List[Finding]:
    from orchestrator.utils.calibration import CALIBRATION_SETS, CalibrationDataError, load_set
    try:
        for name in CALIBRATION_SETS:
            load_set(name)
    except CalibrationDataError as e:
        return [Finding("data", "data.calibration", Severity.WARN, str(e),
                        "Restore data/calibration from the repository (git checkout -- data/calibration).")]
    return [_ok("data", "data.calibration", "Calibration data intact")]


DEFAULT_CHECKS: List[Check] = [check_python, check_docker, check_disk, check_targets, check_user_state,
                               check_secrets, check_tool_policy, check_calibration]


@dataclass
class DoctorReport:
    findings: List[Finding]
    fixed: List[str] = field(default_factory=list)
    still_present: List[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        severities = {f.severity for f in self.findings}
        return 2 if Severity.ERROR in severities else 1 if Severity.WARN in severities else 0

    def to_dict(self) -> Dict:
        return {"exit_code": self.exit_code, "findings": [f.to_dict() for f in self.findings],
                "fixed": self.fixed, "still_present": self.still_present}


class Doctor:
    def __init__(self, framework, checks: Optional[List[Check]] = None):
        self.framework = framework
        self.checks = checks if checks is not None else list(DEFAULT_CHECKS)

    def run(self) -> DoctorReport:
        findings = []
        for check in self.checks:
            try:
                findings.extend(check(self.framework))
            except Exception as e:  # a broken check is itself a finding, never a crash of the doctor
                name = getattr(check, "__name__", "check").replace("check_", "")
                findings.append(Finding(name, f"{name}.check_crashed", Severity.ERROR, f"The check failed: {e}"))
        return DoctorReport(findings)

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
