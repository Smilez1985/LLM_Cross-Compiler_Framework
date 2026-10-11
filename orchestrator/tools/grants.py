#!/usr/bin/env python3
"""
Standing approvals: "yes, and do not ask again" (MCP design 6.10, maintainer decisions rounds 2-4).

A standing approval is a scoped "allow" in one caller's policy. It turns "ask" into "allow" only for calls whose
facts fall inside its scope; outside it the tool asks as before. It never lifts a "deny", never covers another caller,
never applies to a tool without a GrantRule or to a tool the person wants confirmed in the framework, and it is created
only by a person in the framework's own window (the pending-approval window, `llm-cli tools approvals approve ID
--always`, Ditto's GUI dialog or CLI prompt), never by an MCP elicitation: an elicitation can only ask the framework
for one, which the person then confirms in its window.

The scope is computed by the tool's GrantRule from facts the framework looked up for the call (never from what the AI
described). Standing approvals are stored in the signed policy file, under the caller, and written only on a person's
action (create, revoke, reset). Uses and the last use are read from the audit log (`confirm_channel: grant:<id>`), so
a use never rewrites the policy file and a revocation can never be undone by a concurrent use. A standing approval
lapses after 90 days without use, at its own expiry, when the tool's GrantRule version changes, and when its token is
revoked.

This module holds the stored record and the rules of the design's table: packs (knowledge and recipe packs of both
trust levels, target packs only `verified`; exact kind, trust level and index signing key), builds and single-file
downloads (never pickle weights, ungated, permissive licence, a size class), and tools whose scope is the tool itself.
A rule is attached to a tool only together with the framework's lookup of its facts (GrantRule.facts); without facts
every scope is "outside" and the tool asks.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

GRANT_ID_RE = r"^g_[0-9a-f]{16}$"
LAPSE_AFTER = timedelta(days=90)          # without use
DURATIONS: Dict[str, Optional[timedelta]] = {"revoke": None, "30d": timedelta(days=30), "24h": timedelta(hours=24)}
DURATION_LABELS = {"revoke": "until I revoke it (it lapses after 90 days without use)", "30d": "30 days",
                   "24h": "24 hours"}
# Every local stdio client shares the caller "mcp": no open-ended standing approval for it (round 4 item 5).
SHARED_CALLER = "mcp"
SHARED_CALLER_DURATIONS = ("30d", "24h")
WIDTH_LABELS = {"this": "this one only", "any": "any"}
REQUEST_TTL = timedelta(minutes=15)       # a request from an elicitation waits this long for the person
DECLINED_COOLDOWN = timedelta(hours=24)   # a declined scope cannot be requested again before this
CREATED_VIA = ("gui", "cli", "ditto-gui", "ditto-cli")

# The tools that never get a standing approval (design 6.10): they run code or commands on another device, change the
# framework for every AI, read third-party sites, add AI-written code, sign under the person's identity, act on
# pattern-matched log text, destroy something, or stop another client's work.
NEVER_GRANT = frozenset({
    "deploy_to_device", "probe_remote_device", "update_settings", "crawl_docs_site", "create_target_module",
    "export_pack", "resolve_healing", "cancel_build", "cancel_job", "delete_artifact", "restore_knowledge_snapshot",
})

# Licences a standing approval may cover (design 2.4); everything else asks.
PERMISSIVE_LICENSES = frozenset({"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0", "cc0-1.0",
                                 "unlicense"})
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")
SIZE_CLASSES = (5, 20, 50, 200)           # GB; above the largest: "any"
GB = 1000 ** 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class Grant(BaseModel):
    """One standing approval as stored in the policy file (no use counts: those come from the audit log)."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=GRANT_ID_RE)
    tool: str = Field(max_length=64)
    scope: Dict[str, Any]                       # from the tool's GrantRule, over facts the framework looked up
    summary: str = Field(max_length=500)        # the scope in plain words (framework text)
    width: Literal["this", "any"] = "this"
    rule_version: int = Field(ge=1)
    created: str
    created_via: Literal["gui", "cli", "ditto-gui", "ditto-cli"]
    expires: Optional[str] = None               # None: until revoked (it still lapses after 90 days without use)
    token_created: Optional[str] = None         # for a named token: when that token was created (a new token of the
    #                                             same name never inherits it)
    announced: bool = True                      # False for one created from a CLI until the GUI has shown it

    def expired(self, now: Optional[datetime] = None) -> bool:
        end = _parse(self.expires)
        return end is not None and (now or _now()) >= end

    def lapsed(self, last_use: Optional[str], now: Optional[datetime] = None) -> bool:
        since = _parse(last_use) or _parse(self.created)
        return since is not None and (now or _now()) - since > LAPSE_AFTER


class GrantOffer(BaseModel):
    """What a question may offer for one call: computed by the framework from the call's facts, stored with a pending
    approval or a request, and shown with its warning before "Approve and do not ask again"."""
    model_config = ConfigDict(extra="forbid")

    tool: str
    caller: str
    rule_version: int
    scopes: Dict[str, Dict[str, Any]]           # width -> scope (only the widths this caller may choose)
    summaries: Dict[str, str]                   # width -> scope in plain words
    durations: List[str]                        # keys of DURATIONS this caller may choose
    warning: str = Field(max_length=4000)

    def default_width(self) -> str:
        return "this" if "this" in self.scopes else next(iter(self.scopes))

    def default_duration(self) -> str:
        return self.durations[0]


@dataclass(frozen=True)
class ScopeRule:
    """A GrantRule's scope logic: `scope(facts, width)` gives the scope a standing approval would cover (None: no
    standing approval can cover this call), `covers(scope, facts)` whether a later call's facts lie inside it, and
    `summary(scope)` the scope in plain words."""
    scope: Callable[[Dict[str, Any], str], Optional[Dict[str, Any]]]
    covers: Callable[[Dict[str, Any], Dict[str, Any]], bool]
    summary: Callable[[Dict[str, Any]], str]
    widths: Tuple[str, ...] = ("this",)


@dataclass(frozen=True)
class GrantChoice:
    """The answer "yes, and do not ask again" from the framework's own dialog or prompt, with the width and duration
    the person chose (truthy: it is a "yes" for this call too)."""
    width: str
    duration: str

    def __bool__(self) -> bool:
        return True

    @property
    def grant_choice(self) -> Tuple[str, str]:
        return self.width, self.duration


def expiry_for(duration: str, now: Optional[datetime] = None) -> Optional[str]:
    delta = DURATIONS[duration]
    return None if delta is None else ((now or _now()) + delta).isoformat(timespec="seconds")


def durations_for(caller: str) -> List[str]:
    return list(SHARED_CALLER_DURATIONS) if caller == SHARED_CALLER else list(DURATIONS)


# --- rules ------------------------------------------------------------------------------------------------------

def _pack_scope(facts: Dict[str, Any], width: str) -> Optional[Dict[str, Any]]:
    kind, trust, key = facts.get("kind"), facts.get("trust"), facts.get("signing_key")
    if facts.get("source") != "index" or not isinstance(key, str) or not key or width != "this":
        return None
    if kind in ("knowledge", "recipe") and trust in ("verified", "community"):
        return {"kind": kind, "trust": trust, "signing_key": key}
    if kind == "target" and trust == "verified":  # a community target pack always asks (round 4 item 3)
        return {"kind": kind, "trust": trust, "signing_key": key}
    return None  # revoked, local, community target packs, unknown kinds


def _pack_covers(scope: Dict[str, Any], facts: Dict[str, Any]) -> bool:
    return _pack_scope(facts, "this") == scope


PACK_RULE = ScopeRule(
    scope=_pack_scope, covers=_pack_covers,
    summary=lambda s: (f"{s['kind']} packs from the signed index with trust {s['trust']}, verified with the index key "
                       f"{str(s['signing_key'])[:16]}"))


def size_class(size_bytes: Any) -> Optional[int]:
    """The smallest size class (GB) that holds this download; 0 for "any" above the largest; None when unknown."""
    if not isinstance(size_bytes, int) or size_bytes < 0:
        return None
    for limit in SIZE_CLASSES:
        if size_bytes <= limit * GB:
            return limit
    return 0


def _licence_ok(facts: Dict[str, Any]) -> bool:
    licence = facts.get("license")
    return isinstance(licence, str) and licence.strip().lower() in PERMISSIVE_LICENSES


def _weights_ok(name: Any) -> bool:
    text = str(name or "").lower()
    return not text.endswith(PICKLE_SUFFIXES)


def _build_scope(facts: Dict[str, Any], width: str) -> Optional[Dict[str, Any]]:
    if facts.get("target_trust") not in ("shipped", "verified"):
        return None
    if str(facts.get("installed_via") or "").startswith("grant:") and not facts.get("confirmed_build"):
        return None  # installed under a standing approval: one build of it is confirmed by the person first
    if facts.get("weights_format") not in ("safetensors", "gguf") or facts.get("image_base_official") is not True:
        return None
    source = facts.get("source")
    if source == "hf":
        if facts.get("gated") is not False or facts.get("base_gated") is not False or not _licence_ok(facts):
            return None
        limit = size_class(facts.get("size_bytes"))
        if limit is None:
            return None
    elif source == "local":
        limit = None
    else:
        return None
    return {"target": facts.get("target"), "source": source, "max_gb": limit,
            "model": facts.get("model") if width == "this" else "*"}


def _build_covers(scope: Dict[str, Any], facts: Dict[str, Any]) -> bool:
    current = _build_scope(facts, "this")
    if current is None or current["target"] != scope.get("target") or current["source"] != scope.get("source"):
        return False
    if scope.get("model") != "*" and current["model"] != scope.get("model"):
        return False
    if current["source"] == "hf":
        allowed, needed = scope.get("max_gb"), current["max_gb"]
        if allowed != 0 and (needed == 0 or needed > allowed):  # 0 is "any size"
            return False
    return True


BUILD_RULE = ScopeRule(
    scope=_build_scope, covers=_build_covers, widths=("this", "any"),
    summary=lambda s: (f"builds for {s['target']} of {'the model ' + str(s['model']) if s['model'] != '*' else 'any model'}"
                       + (f" from Hugging Face up to {s['max_gb']} GB" if s["source"] == "hf" and s["max_gb"] else
                          " from Hugging Face of any size" if s["source"] == "hf" else " from a local file")
                       + " (safetensors or GGUF, ungated, permissive licence)"))


def _download_scope(facts: Dict[str, Any], width: str) -> Optional[Dict[str, Any]]:
    name = str(facts.get("filename") or "").lower()
    small_text = name.endswith((".json", ".txt", "tokenizer.model")) and isinstance(facts.get("size_bytes"), int) \
        and facts["size_bytes"] <= 50 * 1000 ** 2
    if not (name.endswith((".gguf", ".safetensors")) or small_text) or not _weights_ok(name):
        return None
    if facts.get("gated") is not False or facts.get("base_gated") is not False or not _licence_ok(facts):
        return None
    limit = size_class(facts.get("size_bytes"))
    if limit is None:
        return None
    return {"repo": facts.get("repo") if width == "this" else "*", "max_gb": limit}


def _download_covers(scope: Dict[str, Any], facts: Dict[str, Any]) -> bool:
    current = _download_scope(facts, "this")
    if current is None or (scope.get("repo") != "*" and current["repo"] != scope.get("repo")):
        return False
    allowed, needed = scope.get("max_gb"), current["max_gb"]
    return allowed == 0 or (needed != 0 and needed <= allowed)


DOWNLOAD_RULE = ScopeRule(
    scope=_download_scope, covers=_download_covers, widths=("this", "any"),
    summary=lambda s: (f"single-file downloads from {'the repository ' + str(s['repo']) if s['repo'] != '*' else 'any repository'}"
                       + (f" up to {s['max_gb']} GB" if s["max_gb"] else " of any size")
                       + " (GGUF, safetensors or small text files; ungated, permissive licence)"))


def tool_rule(name: str) -> ScopeRule:
    """The scope is the tool itself (probe_local_hardware, propose_healing_fix, the local runtime tools)."""
    return ScopeRule(scope=lambda facts, width: {"tool": name} if width == "this" else None,
                     covers=lambda scope, facts: scope == {"tool": name},
                     summary=lambda s: f"every call of {name}")


# --- the warning ----------------------------------------------------------------------------------------------

def warning_text(rule_warning: str, caller: str, client: str = "", linked: Optional[List[str]] = None,
                 external: bool = False) -> str:
    """The warning shown before "Approve and do not ask again": who may then act without asking (the caller class,
    with the client's own name as self-reported), what the rule allows, and how to take it back."""
    if caller == SHARED_CALLER:
        who = ("Every local stdio client (they all share one set of permissions"
               + (f"; this request came from {client}, as it reports itself" if client else "") + ")")
    elif caller.startswith("mcp:"):
        who = f"The client with the MCP token '{caller[4:]}'"
    elif caller == "ditto":
        who = "Ditto" + (f" and the MCP tokens linked to it ({', '.join(linked)})" if linked else "")
    else:
        who = caller
    parts = [f"{who} may then {rule_warning} whenever it decides to, without asking you. You will see a notice and an "
             "audit entry for each use. You can revoke this in Tool Permissions at any time."]
    if caller == SHARED_CALLER:
        parts.append("To give one client its own permissions instead, create a token for it with 'llm-cli mcp token "
                     "add NAME' and put it into that client's server entry as LCF_MCP_TOKEN. For all local stdio "
                     "clients together only the narrowest scope and at most 30 days are offered.")
    if external:
        parts.append("Claude Code still shows its own prompt for every call of this tool; this removes only the "
                     "framework's question.")
    return " ".join(parts)


# --- the GUI session lock --------------------------------------------------------------------------------------

GUI_LOCK_NAME = ".gui_session.lock"


def hold_gui_lock(state_dir) -> Optional[Any]:
    """Held by the GUI for as long as it runs (the operating system releases it when the process ends). While it is
    held, "do not ask again" is refused on the command line: the GUI's approval window is the stronger channel."""
    from pathlib import Path

    from filelock import FileLock, Timeout
    try:
        Path(state_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = FileLock(str(Path(state_dir) / GUI_LOCK_NAME), timeout=0, mode=0o600)
        lock.acquire()
        return lock
    except (Timeout, OSError):
        return None


def gui_running(state_dir) -> bool:
    """Whether a GUI session of this user holds its lock (when it cannot be told, it counts as running)."""
    from pathlib import Path

    from filelock import FileLock, Timeout
    path = Path(state_dir) / GUI_LOCK_NAME
    if not path.exists():
        return False
    lock = FileLock(str(path), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return True
    except OSError:
        return True
    lock.release()
    return False
