#!/usr/bin/env python3
"""
Status, settings and honest limits (MCP design 2.9, work package T2; DECISIONS round 1 item 4, round 3).

- get_status: the framework's state in one place, in sections (framework, host, local runtime, knowledge, AI access,
  permissions, audit). No section returns a secret, a key fragment, an account, an endpoint key, URL user info or a
  host path with the user name: folders are labels, URLs scheme and host.
- get_settings: the settings an AI may read (an explicit read list), with whether each may be changed over MCP.
- update_settings: ordinary settings, asked by default; settings that widen what an AI can do may only be tightened
  (and only those may be allowed without a question); permissions, secrets, the vault and the audit log are never
  settings an AI can reach. A value is never read as "default".
- report_cannot_do: the honest "cannot" path. The model states its reason; the framework checks it against its own
  state (the caller's permissions, this session's audit entries, the disk, the "no tool" table) and answers with
  facts it wrote itself. There is no "untested" reason: not being able to test on real hardware is no reason to
  refuse.
"""

import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.llm.settings_change import (MIN_MEMORY_LIMIT, TIGHTEN_ONLY, direction_problem,  # noqa: F401
                                              memory_bytes)
from orchestrator.tools.output import ToolOutput, untrusted_text
from orchestrator.utils.any_path import last_part
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.untrusted import sanitize_untrusted

SECTIONS = ("framework", "host", "local_runtime", "knowledge", "ai_access", "permissions", "audit")
AUDIT_ROWS = 30

# --- the settings an AI may read and change (design 2.9; threat model 6.G) -----------------------------------------

ORDINARY = ("language", "gui_theme", "calibration_language", "crawler_languages", "rag_rerank_default",
            "rag_model_idle_minutes", "build_jobs", "chat_context_limit", "default_enable_imatrix")
# key -> the direction an AI may move it: True/False for a boolean (only to that value), "lower"/"higher" for a number.
# crawler_download_pdfs: Deep Ingest reads more third-party text with it on, so an AI may only turn it off (SEC-M3).
# The table (TIGHTEN_ONLY), memory_bytes and direction_problem live with the AI-settings change service
# (llm/settings_change.py), so the AI settings dialog's GUI-tool fields apply the same rule without importing a tool
# module; they are imported above and stay importable from here.
WRITABLE = ORDINARY + tuple(TIGHTEN_ONLY)
# Settings that keep the person informed of what AIs do (delegation design 3.7, SEC-M3): an AI never changes them, in
# either direction. Some belong to parts that are still being built (the chat panel, the relay, the trash, the benchmark
# sandbox); they are named here already, so the day they exist they are read-only for an AI.
OVERSIGHT = ("audio_announcements", "chat_panel_auto_show", "relay_questions_to_clients", "trash_retention_days",
             "bench_untrusted_sandbox_ok")
READ_ONLY = ("max_concurrent_builds", "build_timeout", "auto_cleanup", "log_level", "rag_embedding_model",
             "rag_reranker", "rag_rerank_min_score", "healing_approval_timeout", "llama_cpp_version", "image_trivy",
             "image_base_debian", "image_inference_runtime", "gui_auto_refresh",
             "gui_refresh_interval", "api_enabled") + OVERSIGHT
URL_KEYS = ("qdrant_url", "community_index_url", "community_repo", "docker_registry", "source_repositories")
FOLDER_KEYS = {"targets_dir": "targets", "models_dir": "models", "datasets_dir": "datasets", "output_dir": "output",
               "build_dir": "builds", "cache_dir": "cache", "logs_dir": "logs", "default_calibration_dataset": "datasets"}
READABLE = WRITABLE + READ_ONLY + URL_KEYS + tuple(FOLDER_KEYS)
NOT_A_VALUE = ("", "default", "null", "none", "reset", "unset")
SETTINGS_PER_HOUR = 10

ReadableKey = Literal[READABLE]  # type: ignore[valid-type]
WritableKey = Literal[WRITABLE]  # type: ignore[valid-type]

# --- report_cannot_do --------------------------------------------------------------------------------------------

REASONS = ("missing_information", "not_supported", "no_tool", "permission_denied", "declined", "needs_person",
           "hardware_unavailable", "insufficient_resources", "limit_reached", "failed")
# Tools that use a physical device or a host accelerator: only for them can "the hardware is not available" be true.
HARDWARE_TOOLS = ("deploy_to_device", "probe_remote_device", "run_device_benchmark", "build_local_runtime",
                  "deploy_device_runtime", "start_local_runtime", "run_benchmark")
UNTESTED_RULE = ("No tool you need here uses the physical board; not being able to test on real hardware is not a "
                 "reason to refuse: do the work, list your assumptions; the result is marked untested.")
# What to try before reporting that something is missing (design 2.9: "premature").
TRY_FIRST = {
    "missing_information": ("list_targets", "get_target_capabilities", "get_status", "search_knowledge",
                            "check_build_request"),
    "not_supported": ("get_target_capabilities", "check_build_request"),
    "insufficient_resources": ("check_build_request", "get_status"),
    "failed": ("get_build_status", "read_build_log"),
    "limit_reached": ("get_status",),
    "declined": ("get_status",),
}


# --- models --------------------------------------------------------------------------------------------------------

class FrameworkSection(BaseModel):
    version: str
    docker_reachable: Optional[bool] = None
    targets: int
    offline_mode: bool
    knowledge_base: bool
    active_builds: int = Field(description="Unfinished build requests of this framework process")
    policy_configured: bool
    policy_problem: bool = Field(description="The permission file is damaged: every tool is denied")


class GpuOut(BaseModel):
    vendor: str
    name: str = Field(description="The GPU's name as its driver reports it (invisible characters removed)")
    vram_bytes: Optional[int] = None
    shares_ram: bool = False


class HostSection(BaseModel):
    os: str
    arch: str
    ram_total_bytes: int
    gpus: List[GpuOut] = Field(default_factory=list)
    recommended_backend: Optional[str] = None
    recommended_tier: Optional[str] = None
    reason: str = ""


class LocalRuntimeSection(BaseModel):
    installed: bool
    mode: str = Field(default="bare-metal", description="Where Ditto's server runs: bare-metal or docker. Only the "
                      "person switches it (llm-cli ditto runtime ensure --mode ... --force, or the AI settings)")
    docker_image: Optional[str] = Field(default=None, description="Docker mode: the name of Ditto's image, built and "
                                        "verified on this computer")
    docker_made_by: Optional[str] = Field(default=None, description="Docker mode: repackaged (the bare-metal build) "
                                          "or rebuilt (in the builder container)")
    origin: Optional[str] = Field(default=None, description="self-built, imported, or prebuilt (not self-built)")
    build_id: Optional[str] = None
    commit: Optional[str] = None
    backend: Optional[str] = None
    verified_at: Optional[str] = None
    running: bool = False
    healthy: bool = False
    model: Optional[str] = None
    tier: Optional[str] = None
    rebuild: Optional[str] = None
    problems: List[str] = Field(default_factory=list)
    benchmarks: List["BenchmarkSummary"] = Field(default_factory=list,
                                                 description="The newest llama-bench runs on this computer")


class BenchmarkSummary(BaseModel):
    run_id: str
    kind: str
    where: str
    build_id: str = ""
    prompt_ts: Optional[float] = None
    gen_ts: Optional[float] = None
    at: str = ""


LocalRuntimeSection.model_rebuild()


class KnowledgeSection(BaseModel):
    enabled: bool
    connected: bool = False
    vector_count: int = 0
    mode: Optional[str] = None
    storage: str = Field(default="", description="embedded in the per-user folder, or scheme://host:port")
    store_format: Optional[str] = Field(default=None, description="qdrant-edge (the local knowledge base), old-local "
                                        "(the storage of earlier versions, while its conversion failed) or server")
    embedding_model: Optional[str] = None
    reranker: Optional[str] = None
    snapshots: List[str] = Field(default_factory=list)
    problem: Optional[str] = None


class ProviderAccess(BaseModel):
    provider: str
    label: str
    state: str
    primary: bool = False
    method: Optional[str] = None
    source: str = Field(default="none", description="vault, environment, profile, runtime or none")


class AiAccessSection(BaseModel):
    providers: List[ProviderAccess] = Field(default_factory=list)
    hf_token_stored: bool
    how_to_sign_in: str = ("The person signs in in the GUI's sign-in panel or with 'llm-cli ditto login'; a Hugging "
                           "Face token with 'llm-cli secrets set hf_token'. Keys never pass through an AI.")


class AuditRow(BaseModel):
    time: str
    tool: str
    decision: str
    answer: Optional[str] = None
    outcome: str
    reason_code: Optional[str] = None


class AuditSection(BaseModel):
    caller: str
    intact: bool
    problem: str = ""
    entries: List[AuditRow] = Field(default_factory=list)


SettingValue = Union[bool, int, float, str, List[str], None]


class SettingOut(BaseModel):
    key: str
    value: SettingValue = None
    writable: Literal["ordinary", "tighten_only", "read_only"]
    allowed: str = Field(default="", description="What a change over MCP may do")
    persisted: bool = Field(default=False, description="A change is saved in the user's config.yml")


class AiSummary(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    privacy_level: Optional[str] = None
    offline_mode: bool = False


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keys: List[ReadableKey] = Field(default_factory=list, max_length=50,
                                    description="Settings to show (empty: all the readable ones)")


class SettingsOut(ToolOutput):
    settings: List[SettingOut]
    ai: AiSummary = Field(description="Read-only: the AI settings are changed by the person only")


class SettingChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: WritableKey
    value: str = Field(min_length=1, max_length=200, description="The new value as text, e.g. 'de', 'true', '20'")


class UpdateSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: List[SettingChange] = Field(min_length=1, max_length=10,
                                         description='1-10 changes, e.g. [{"key": "language", "value": "de"}]')


class AppliedOut(BaseModel):
    key: str
    old: str
    new: str


class RejectedOut(BaseModel):
    key: str
    reason: str


class PersistedOut(BaseModel):
    key: str
    persisted: bool


class UpdateSettingsOut(ToolOutput):
    applied: List[AppliedOut] = Field(default_factory=list)
    rejected: List[RejectedOut] = Field(default_factory=list)
    persisted: List[PersistedOut] = Field(default_factory=list)


class CannotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=300, description="What the user asked for")
    reason: Literal[REASONS] = Field(description="Why it cannot be done")  # type: ignore[valid-type]
    missing: str = Field(min_length=1, max_length=300, description="What exactly is missing")
    tool: Optional[str] = Field(default=None, max_length=64, description="The tool that would do it or that failed")
    action: Optional[Literal["sign_in", "change_permissions", "audit_log", "trust_device",
                             "deep_ingest_notice", "publish_pull_request",
                             "install_pack_from_file", "run_command"]] = Field(
        default=None, description="One of the framework's known actions without a tool")
    next_step: Optional[str] = Field(default=None, max_length=300)


class CannotOut(ToolOutput):
    checked: Literal["confirmed", "contradicted", "premature", "unchecked"]
    facts: List[str] = Field(default_factory=list, description="Written by the framework from its own state")
    say_to_user: str = Field(description="One sentence built only from the framework's facts")
    try_first: List[str] = Field(default_factory=list, description="Tools to call before reporting a cannot")
    task: untrusted_text(300) = ""
    missing: untrusted_text(300) = ""
    next_step: untrusted_text(300) = ""


# --- helpers -------------------------------------------------------------------------------------------------------

def _cfg(framework, key: str, default=None):
    config = getattr(framework, "config", None)
    get = getattr(config, "get", None)
    try:
        return get(key, default) if callable(get) else default
    except Exception:  # noqa: BLE001
        return default


def scheme_and_host(value: Any) -> Optional[str]:
    """A URL as scheme://host[:port] (no user info, path, query or token); a GitHub owner/name stays as it is."""
    if not isinstance(value, str) or not value:
        return None
    if "://" not in value:
        return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,140}", value) and "@" not in value else "(set)"
    parsed = urlparse(value)
    host = parsed.hostname or "?"
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        port = ""
    return f"{parsed.scheme}://{host}{port}"


def setting_value(framework, key: str) -> SettingValue:
    """A setting as an AI may read it: URLs reduced to scheme and host, folders as labels, never a path."""
    value = _cfg(framework, key)
    if key in FOLDER_KEYS:
        if not value:
            return None
        # the file's name, split as a path of either system (a settings file written on the other one)
        name = last_part(str(value)) if key == "default_calibration_dataset" else ""
        return f"{FOLDER_KEYS[key]}:{name}"
    if key == "source_repositories":
        if not isinstance(value, dict):
            return []
        return [f"{name}: {scheme_and_host(e.get('url') if isinstance(e, dict) else e)}" for name, e in value.items()]
    if key in URL_KEYS:
        return scheme_and_host(value)
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [str(v)[:64] for v in value][:50]
    return str(value)[:200]


def oversight_problems(writable: Any = None) -> List[str]:
    """Settings that keep the person informed but would be writable by an AI (the registration refuses that)."""
    keys = set(WRITABLE if writable is None else writable)
    return sorted(k for k in OVERSIGHT if k in keys)


def allowed_text(key: str) -> str:
    if key in OVERSIGHT:
        return "read-only over MCP: it keeps you informed of what AIs do; only you change it, in the GUI or CLI"
    if key in ORDINARY:
        return "may be changed over MCP; the person is asked"
    if key in TIGHTEN_ONLY:
        direction = TIGHTEN_ONLY[key]
        if isinstance(direction, bool):
            return f"may only be set to {str(direction).lower()} over MCP (the safer value); the person is asked"
        return f"may only be made {direction} over MCP (the safer direction); the person is asked"
    return "read-only over MCP; the person changes it in the GUI or CLI"


def parse_value(schema, key: str, text: str) -> Any:
    """The text of a change as the setting's type; ValueError with the reason. Never "default" or empty."""
    raw = text.strip()
    if raw.lower() in NOT_A_VALUE:
        raise ValueError(f"'{text}' is not a value: there is no reset to a default over MCP (a default may be looser "
                         "than the person's own setting). Give the new value itself.")
    kind = getattr(schema, "field_type", str)
    if kind is bool:
        low = raw.lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
        raise ValueError(f"'{text}' is not true or false")
    if kind is int:
        if not re.fullmatch(r"-?\d+", raw):
            raise ValueError(f"'{text}' is not a whole number")
        return int(raw)
    if kind is float:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"'{text}' is not a finite number")
        return value
    if kind is list:
        if raw.startswith("["):
            value = json.loads(raw)
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"'{text}' is not a list of words")
            return [v.strip() for v in value if v.strip()]
        return [v.strip() for v in raw.split(",") if v.strip()]
    if key == "build_memory_limit":
        memory_bytes(raw)
    return raw


def shown(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "(empty)"
    return "(empty)" if value in (None, "") else str(value)


# --- status sections -----------------------------------------------------------------------------------------------

def framework_section(framework, registry) -> FrameworkSection:
    service = getattr(framework, "build_service", None)
    try:
        targets = len(service.list_targets().targets) if service is not None else 0
    except Exception:  # noqa: BLE001
        targets = 0
    try:
        active = sum(1 for s in framework.orchestrator._workflows.values()
                     if s.status.name not in ("COMPLETED", "ERROR", "CANCELLED"))
    except Exception:  # noqa: BLE001
        active = 0
    docker = None
    manager = getattr(framework, "docker_manager", None)
    client = getattr(getattr(manager, "builder", None), "docker_client", None) if manager is not None else None
    if manager is not None:
        docker = client is not None
    policy = registry.policy_store.load()
    return FrameworkSection(version=str(getattr(getattr(framework, "info", None), "version", "") or "unknown"),
                            docker_reachable=docker, targets=targets,
                            offline_mode=bool(_cfg(framework, "offline_mode", False)),
                            knowledge_base=bool(_cfg(framework, "enable_rag_knowledge", False)), active_builds=active,
                            policy_configured=bool(policy.configured),
                            policy_problem=bool(getattr(registry.policy_store, "problem", None)))


def host_section(framework, hardware: Callable[[], Any]) -> Optional[HostSection]:
    hw = hardware()
    if hw is None:
        return None
    section = HostSection(os=str(hw.os), arch=str(hw.arch), ram_total_bytes=int(hw.ram_total),
                          gpus=[GpuOut(vendor=g.vendor, name=sanitize_untrusted(str(g.name), 200), vram_bytes=(g.vram_mib or 0) * 1024 ** 2 or None,
                                       shares_ram=bool(g.unified)) for g in hw.gpus])
    try:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        runtime = LocalLLMRuntime.from_framework(framework)
        choice = runtime.select_backend()
        tier, reason = runtime.choose_tier()
        section.recommended_backend, section.recommended_tier = choice.backend, tier.tier
        section.reason = redact_secrets(str(reason))[:500]
    except Exception:  # noqa: BLE001 - the recommendation is a help; the hardware alone is shown
        pass
    return section


def _scrub_paths(text: str) -> str:
    """Framework text about the local runtime with the home folder shortened to ~ (no user name; in every spelling a
    Windows host's texts have), secrets redacted, cleaned and capped: security.paths.scrub_for_ai."""
    from orchestrator.security.paths import scrub_for_ai
    return scrub_for_ai(text)


def local_runtime_section(framework) -> LocalRuntimeSection:
    from orchestrator.runtime.llama_server import LocalLLMRuntime
    status = LocalLLMRuntime.from_framework(framework).status()
    inst = status.installed
    mode = getattr(status, "mode", None) or "bare-metal"
    image = getattr(status, "docker", None) if mode == "docker" else None
    section = LocalRuntimeSection(installed=inst is not None or bool(image), running=status.running,
                                  healthy=status.healthy, model=status.model, tier=status.tier, mode=mode,
                                  rebuild=_scrub_paths(status.rebuild) if status.rebuild else None,
                                  problems=[_scrub_paths(p) for p in status.problems][:20])
    if image:  # the image's name and how it was made: no path and no user name
        section.docker_image = sanitize_untrusted(str(image.get("tag") or ""), 120) or None
        section.docker_made_by = image.get("made_by") if image.get("made_by") in ("repackaged", "rebuilt") else None
    if inst is not None:
        section.origin = inst.origin if inst.origin != "prebuilt" else "prebuilt (not self-built)"
        section.build_id, section.commit, section.backend = inst.build_id, inst.commit, inst.backend
        section.verified_at = inst.verified_at
    try:
        from orchestrator.runtime.bench import BenchRunner
        rows = BenchRunner(Path(framework.config.user_state_dir)).latest(5)
        section.benchmarks = [BenchmarkSummary(**{k: r.get(k) for k in BenchmarkSummary.model_fields
                                                  if r.get(k) is not None}) for r in rows
                              if r.get("run_id") and r.get("kind") and r.get("where")]
    except Exception:  # noqa: BLE001 - no runs or an unreadable index: none shown
        section.benchmarks = []
    return section


def knowledge_section(framework) -> KnowledgeSection:
    enabled = bool(_cfg(framework, "enable_rag_knowledge", False))
    section = KnowledgeSection(enabled=enabled, reranker=str(_cfg(framework, "rag_reranker", "") or "") or None)
    if not enabled:
        return section
    ensure = getattr(framework, "ensure_rag_manager", None)
    rag = ensure() if callable(ensure) else getattr(framework, "rag_manager", None)
    if rag is None:
        return section
    status = rag.get_status()
    storage = str(status.get("storage") or "")
    section.connected = bool(status.get("connected"))
    section.vector_count = int(status.get("vector_count") or 0)
    section.mode = status.get("mode")
    section.storage = scheme_and_host(storage) if "://" in storage else "embedded in the per-user folder"
    section.embedding_model = status.get("embedding_model")
    section.store_format = status.get("store_format")
    if status.get("error"):  # its text can quote a server's reply: only that there is a problem is shown
        section.problem = ("The knowledge base could not be opened; run_mojo_oracle (or 'llmcf mojo' in a terminal) "
                           "shows why.")
    try:
        section.snapshots = [Path(str(s)).name for s in rag.list_snapshots()][:50]
    except Exception:  # noqa: BLE001
        pass
    return section


def ai_access_section(framework, sign_in_factory: Callable[[], Any]) -> AiAccessSection:
    sm = getattr(framework, "secrets_manager", None)
    try:
        hf = bool(sm.get_secret("hf_token")) if sm is not None else False
    except Exception:  # noqa: BLE001 - a locked vault: unknown, shown as not stored
        hf = False
    section = AiAccessSection(hf_token_stored=hf)
    try:
        statuses = sign_in_factory().status_all(check=False)
    except Exception:  # noqa: BLE001
        return section
    for st in statuses:
        source = st.source or "none"
        kind = ("environment" if source.startswith("env:") else "profile" if source.startswith("profile:")
                else source if source in ("vault", "runtime", "none") else "other")
        # Never: last4, account, fields, endpoint origin, profile name, error text.
        section.providers.append(ProviderAccess(provider=str(st.provider), label=str(st.label), state=str(st.state),
                                                primary=bool(st.primary), method=st.method, source=kind))
    return section


def audit_section(registry, caller: str) -> AuditSection:
    audit = registry.audit
    if audit is None:
        return AuditSection(caller=caller, intact=False, problem="no audit log")
    check = audit.check()
    rows = [AuditRow(time=str(e.get("ts", "")), tool=str(e.get("tool", "")), decision=str(e.get("decision", "")),
                     answer=e.get("answer"), outcome=str(e.get("outcome", "")), reason_code=e.get("reason_code"))
            for e in audit.entries(500) if e.get("actor") == caller][:AUDIT_ROWS]
    return AuditSection(caller=caller, intact=bool(check.intact), problem=str(check.problem or ""), entries=rows)


# --- the honest "cannot" check -------------------------------------------------------------------------------------

def session_entries(registry, ctx) -> List[Dict[str, Any]]:
    """This caller's audit entries of this session (newest first), without report_cannot_do itself. A call without a
    session has no "this session": nothing of the caller's earlier sessions counts for it."""
    if registry.audit is None or not ctx.session:
        return []
    out = []
    for entry in registry.audit.entries(1000):
        session = str(entry.get("session") or "")
        if entry.get("actor") != ctx.actor or entry.get("tool") == "report_cannot_do":
            continue
        if session != ctx.session and not session.startswith(ctx.session + ":"):
            continue
        out.append(entry)
    return out


def check_cannot(inp: CannotIn, ctx, registry, framework, no_tool: Dict[str, str]) -> Tuple[str, List[str], List[str]]:
    """(checked, facts, tools to try first) from framework facts only, never from the free text of the report."""
    reason, tool = inp.reason, (inp.tool or "").strip() or None
    action = inp.action
    entries = session_entries(registry, ctx)
    spec = None
    if tool:
        try:
            spec = registry.get(tool)
        except Exception:  # noqa: BLE001 - an unknown tool name
            spec = None
    # Facts name a tool only by its registered name: the model's own spelling of `tool` (free text) never reaches
    # facts or say_to_user, and neither does a name from an audit entry that is not a registered tool.
    tool = spec.name if spec is not None else tool
    said = tool if spec is not None else "the tool you named"

    def name_of(entry: Dict[str, Any]) -> str:
        value = str(entry.get("tool") or "")
        return value if _known(registry, value) else "a call"

    of_tool = [e for e in entries if tool and e.get("tool") == tool]
    try_first = [t for t in TRY_FIRST.get(reason, ()) if _known(registry, t)]

    def premature() -> Tuple[str, List[str], List[str]]:
        return ("premature", ["This session made no call yet that could have found this out."
                              if not entries else f"This session made no call to {said if tool else 'a tool that could help'}."],
                try_first)

    if reason == "hardware_unavailable":
        if tool in HARDWARE_TOOLS and spec is not None:
            return "unchecked", [f"{tool} uses a device or this computer's accelerator; the framework cannot check "
                                 "its availability from here."], []
        return "contradicted", [UNTESTED_RULE], []
    if reason in ("no_tool", "needs_person"):
        if action and action in no_tool:
            return "confirmed", [f"No tool does this: {no_tool[action]}"], []
        if spec is not None and registry.decide(spec, None, ctx).value != "deny":
            return "contradicted", [f"{tool} exists and is available to you ({registry.decide(spec, None, ctx).value})."], []
        if reason == "needs_person":
            asked = [e for e in entries if e.get("reason_code") == "needs_person"]
            if asked:
                return "confirmed", [f"{name_of(asked[0])} was refused with reason needs_person in this session."], []
        return "unchecked", ["The framework has no record that names this action."], []
    if reason == "permission_denied":
        if spec is None:
            return ("unchecked", ["No tool was named, so no permission could be checked."] if not tool else
                    ["The tool you named is not one of the framework's tools."], [])
        decision = registry.decide(spec, None, ctx).value
        if decision == "deny":
            return "confirmed", [f"Your tool permissions deny {tool}; only the person can change that (Tools > Tool "
                                 "Permissions in the GUI, or 'llm-cli tools permissions')."], []
        return "contradicted", [f"{tool} is not denied: your permissions say '{decision}'"
                                + (" (the person is asked)." if decision == "ask" else ".")], []
    if not entries:
        return premature()
    if reason == "declined":
        declined = [e for e in of_tool or entries if e.get("answer") == "declined"
                    or e.get("reason_code") == "declined"]
        if declined:
            return "confirmed", [f"The person declined {name_of(declined[0])} in this session."], []
        return "contradicted", ["No question was declined in this session."], []
    if reason == "limit_reached":
        hits = [e for e in of_tool or entries if e.get("outcome") == "rate_limited"
                or e.get("reason_code") == "limit_reached"]
        if hits:
            detail = str(hits[0].get("detail") or "").split("\n[extra")[0][:300]
            return "confirmed", [f"A limit refused {name_of(hits[0])}: {detail}"], []
        return "contradicted", ["No call of this session was refused by a limit."], []
    if reason == "failed":
        failed = [e for e in of_tool or entries if e.get("outcome") == "error"]
        if failed:
            codes = sorted({str(e.get("reason_code") or "failed") for e in failed})
            return "confirmed", [f"{name_of(failed[0])} failed in this session (reason {', '.join(codes)})."], []
        return ("premature" if tool and not of_tool else "contradicted"), [
            "No call of this session failed." if not (tool and not of_tool) else f"This session made no call to "
                                                                                  f"{said}."], try_first
    if reason == "insufficient_resources":
        full = [e for e in entries if e.get("reason_code") == "insufficient_resources"]
        if full:
            return "confirmed", [f"{name_of(full[0])} was refused for lack of disk space in this session."], []
        return disk_fact(framework)
    if reason == "missing_information":
        seen = [e for e in entries if re.search(r'"(probe_id|board)":\s*"[^"]', str(e.get("args_preview") or ""))]
        if seen:
            return "contradicted", ["This session already named a board or a probe; use it, and list what you had "
                                    "to assume. " + UNTESTED_RULE], []
        return ("premature" if not of_tool and tool else "unchecked"), [
            "The framework cannot tell whether the information exists."], try_first
    if reason == "not_supported":
        refused = [e for e in entries if e.get("reason_code") == "not_supported"]
        if refused:
            return "confirmed", [f"{name_of(refused[0])} was refused with reason not_supported in this session."], []
        return "unchecked", ["No call of this session was refused as not supported."], try_first
    return "unchecked", [], try_first


def _known(registry, name: str) -> bool:
    try:
        registry.get(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def _disk_of(path: Path) -> int:
    """Which disk (file system) a folder lies on."""
    return os.stat(path).st_dev


def disk_fact(framework) -> Tuple[str, List[str], List[str]]:
    """The free space of the disks builds use: the cache, the output folder and the framework's build folder, where a
    build is written first (eighth round, answer 3). Folders on one disk are named together, by label only."""
    from orchestrator.Core.disk_plan import gb, reserve_for
    disks: Dict[Any, Tuple[List[str], Any]] = {}
    for label, key in (("cache", "cache_dir"), ("output", "output_dir"), ("build", "build_dir")):
        value = _cfg(framework, key)
        if not value:
            continue
        resolve = getattr(framework, "resolve_path", None)
        path = Path(resolve(value)) if callable(resolve) else Path(str(value))
        while not path.exists() and path.parent != path:
            path = path.parent
        try:
            usage = shutil.disk_usage(path)
            device = _disk_of(path)
        except OSError:
            continue
        disks.setdefault(device, ([], usage))[0].append(label)
    facts, short = [], False
    for labels, usage in disks.values():
        if usage.free < reserve_for(usage.total):
            short = True
        facts.append(f"The {' and '.join(labels)} disk has {gb(usage.free)} free.")
    if not facts:
        return "unchecked", ["The framework could not read the free disk space."], []
    return ("confirmed" if short else "contradicted"), facts, (["check_build_request"] if not short else [])


def say_to_user(checked: str, reason: str, facts: List[str]) -> str:
    """One sentence from framework facts only (the model's own text never enters it)."""
    first = facts[0] if facts else ""
    words = reason.replace("_", " ")
    if checked == "confirmed":
        return f"I could not complete this ({words}): {first}"
    if checked == "contradicted":
        return f"The framework's state does not support '{words}': {first} Tell the user the actual problem, or try the call."
    if checked == "premature":
        return f"Nothing was tried yet: {first} Try the listed tools first; report a cannot only when they do not help."
    return f"The framework could not check the reason '{words}'. {first}".strip()


# --- the specs -----------------------------------------------------------------------------------------------------

def settings_schema(framework, key: str):
    schemas = getattr(getattr(framework, "config", None), "config_schemas", None)
    return schemas.get(key) if isinstance(schemas, dict) else None


def settings_facts(framework, inp: UpdateSettingsIn):
    """The question's facts (old and new value of every change) and what the handler applies; refuses before the
    question a value that is not one, a schema violation and every loosening of a tighten-only setting."""
    from orchestrator.tools.facts_catalog import blocked
    from orchestrator.tools.spec import CallFacts, Fact
    seen, changes, facts, problems = set(), [], [], []
    for change in inp.changes:
        key = change.key
        if key in seen:
            problems.append(f"{key} is named twice")
            continue
        seen.add(key)
        schema = settings_schema(framework, key)
        old = _cfg(framework, key)
        try:
            new = parse_value(schema, key, change.value)
            if schema is not None and not schema.validate_value(new)[0]:
                raise ValueError(f"'{change.value}' is not allowed for {key} ({getattr(schema, 'description', '')})")
        except (ValueError, TypeError) as e:
            problems.append(f"{key}: {e}")
            continue
        loosens = direction_problem(key, old, new)
        if loosens:
            raise blocked("setting_loosens", f"Refused: {loosens}.",
                          "Tell the person; only they can change it in that direction, in the GUI or CLI.",
                          field="changes")
        changes.append({"key": key, "old": shown(old), "new": new})
        facts.append(Fact(key, f"{shown(old)} -> {shown(new)}"
                          + (" (tightens what an AI may do)" if key in TIGHTEN_ONLY else "")))
    if problems:
        raise blocked("setting_invalid", "Invalid settings: " + "; ".join(problems) + ".",
                      "Call get_settings for the current values and what each setting takes, then call "
                      "update_settings again.", field="changes")
    keys = ", ".join(c["key"] for c in changes)
    consequence = (f"Changes {len(changes)} framework setting{'s' if len(changes) != 1 else ''} ({keys}) for every "
                   "AI and for your own builds, and saves them in your config.yml.")
    return CallFacts(consequence=consequence, facts=tuple(facts), bound={"changes": changes},
                     numbers={"changes": len(changes)}, consequence_template=consequence)


def tighten_only(inp, _who=None) -> Any:
    """The cap of update_settings: "allow" may be set only for calls whose every change is tighten-only."""
    from orchestrator.tools.policy import Decision
    if inp is None:
        return Decision.ASK
    return Decision.ALLOW if all(c.key in TIGHTEN_ONLY for c in inp.changes) else Decision.ASK


def status_tool_specs(registry, framework, no_tool_actions: List[Tuple[str, str]]) -> List[Any]:
    """get_settings, update_settings and report_cannot_do (get_status stays in framework_tools)."""
    from orchestrator.tools.output import fact
    from orchestrator.tools.policy import Decision, ToolTier
    from orchestrator.tools.registry import ToolError, ToolSpec
    from orchestrator.tools.spec import FieldMeta, Rate, ToolLimits
    no_tool = dict(no_tool_actions)
    loose = oversight_problems()
    if loose:  # never registered: a setting that keeps the person informed is not an AI's to change
        raise ValueError(f"update_settings would let an AI change settings that keep the person informed: {loose}")

    def get_settings(inp: SettingsIn) -> SettingsOut:
        missing = object()
        # Without a choice: every readable setting this framework has (oversight settings of parts not built yet are
        # left out instead of being shown as empty).
        keys = list(dict.fromkeys(inp.keys)) or [k for k in READABLE if k not in OVERSIGHT
                                                  or _cfg(framework, k, missing) is not missing]
        rows = []
        for key in keys:
            writable = "ordinary" if key in ORDINARY else "tighten_only" if key in TIGHTEN_ONLY else "read_only"
            rows.append(SettingOut(key=key, value=setting_value(framework, key), writable=writable,
                                   allowed=allowed_text(key), persisted=key in WRITABLE))
        ai = AiSummary(provider=str(_cfg(framework, "ai_provider", "") or "") or None,
                       model=str(_cfg(framework, "ai_model", "") or "") or None,
                       effort=str(_cfg(framework, "ai_effort", "") or "") or None,
                       privacy_level=str(_cfg(framework, "ai_security_level", "") or "") or None,
                       offline_mode=bool(_cfg(framework, "offline_mode", False)))
        return SettingsOut(settings=rows, ai=ai)

    def update_settings(inp: UpdateSettingsIn, ctx) -> UpdateSettingsOut:
        bound = ctx.facts.bound.get("changes") if ctx.facts is not None else None
        if not isinstance(bound, list):
            raise ToolError("The change is not bound to the values the person was shown; nothing was changed.",
                            code="failed", next="Call update_settings again through the framework's tool registry.")
        config = framework.config
        out = UpdateSettingsOut()
        for change in bound:
            key, new = change["key"], change["new"]
            # Checked again right before it is set: the person may have changed it while the question was open.
            loosens = direction_problem(key, _cfg(framework, key), new)
            if loosens:
                out.rejected.append(RejectedOut(key=key, reason=loosens))
                continue
            if config.set(key, new):
                out.applied.append(AppliedOut(key=key, old=change["old"], new=shown(new)))
            else:
                out.rejected.append(RejectedOut(key=key, reason="the framework refused the value"))
        saved = False
        if out.applied:
            save = getattr(config, "save_user_config", None)
            try:
                saved = bool(save()) if callable(save) else False
            except Exception:  # noqa: BLE001 - applied for this process; reported as not saved
                saved = False
        out.persisted = [PersistedOut(key=a.key, persisted=saved) for a in out.applied]
        return out

    def report_cannot_do(inp: CannotIn, ctx) -> CannotOut:
        checked, facts, try_first = check_cannot(inp, ctx, registry, framework, no_tool)
        if registry.audit is not None:
            try:
                registry.audit.record(ctx.actor, "report_cannot_do", ToolTier.READ.value, Decision.ALLOW.value,
                                      "cannot_reported", arguments={"reason": inp.reason, "tool": inp.tool,
                                                                    "action": inp.action, "checked": checked},
                                      detail=f"{inp.reason}: {checked}",
                                      extra={"client": ctx.client, "session": ctx.session,
                                             "reason_code": inp.reason})
            except Exception:  # noqa: BLE001 - a read never fails on the audit log
                pass
        return CannotOut(checked=checked, facts=facts, say_to_user=say_to_user(checked, inp.reason, facts),
                         try_first=try_first, task=inp.task, missing=inp.missing, next_step=inp.next_step or "")

    return [
        ToolSpec("get_settings", "Read settings",
                 "Current values of the framework settings an AI may read, and whether each may be changed over MCP "
                 "(ordinary, tighten_only, read_only). Addresses are shown as scheme and host, folders as labels; "
                 "secrets and the AI provider's settings beyond a summary are never shown.",
                 ToolTier.READ, SettingsIn, SettingsOut, get_settings, idempotent=True,
                 fields={"keys": FieldMeta(kind="value", split=True)}, examples=({"keys": ["language"]},),
                 summary=lambda o: f"{len(o.settings)} settings."),
        ToolSpec("update_settings", "Change settings",
                 "Changes ordinary framework settings (language, theme, build_jobs, ...); the person is asked. "
                 "Settings that limit what an AI or a build may do can only be made stricter over MCP (offline_mode "
                 "true, build_sandbox true, lower page and depth limits, ...). Permissions, secrets, the vault, AI provider "
                 "settings, addresses and folders are never changed here. There is no reset to a default. "
                 'Example: {"changes": [{"key": "language", "value": "de"}]}',
                 ToolTier.LOCAL_WRITE, UpdateSettingsIn, UpdateSettingsOut, update_settings, takes_context=True,
                 default_decision=Decision.ASK, max_decision=tighten_only,
                 prepare=lambda i, ctx: settings_facts(framework, i),
                 limits=ToolLimits(rate=Rate(SETTINGS_PER_HOUR, 3600.0), what="settings changes"),
                 fields={"changes": FieldMeta(kind="trust")},
                 examples=({"changes": [{"key": "language", "value": "de"}]},),
                 summary=lambda o: f"{len(o.applied)} settings changed, {len(o.rejected)} refused."),
        ToolSpec("report_cannot_do", "Report that a task cannot be done",
                 "Use when you really cannot do what the user asked: give the reason code and what exactly is "
                 "missing. The framework checks the reason against its own state and returns facts and one sentence "
                 "for the user. Not being able to test on real hardware is not a reason: do the work and mark it "
                 "untested.",
                 ToolTier.READ, CannotIn, CannotOut, report_cannot_do, idempotent=True, takes_context=True,
                 fields={"tool": FieldMeta(kind="id")},
                 examples=({"task": "trust my board's host key", "reason": "no_tool",
                            "missing": "a tool that trusts a host key", "action": "trust_device"},),
                 summary=lambda o: f"Cannot-report checked: {fact(o.checked)}."),
    ]
