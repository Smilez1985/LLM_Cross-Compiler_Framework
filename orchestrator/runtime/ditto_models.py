#!/usr/bin/env python3
"""
Ditto's model catalogue as `llm-cli ditto models list` shows it, and the model `ditto models download` fetches
(W1': the GUI's Ditto > Local Models... shows and downloads the same, from the same calls).

models_overview() reads the pinned catalogue once: every tier with its file, size, licence, whether it is downloaded
(ready, unverified, missing), and which tier is recommended for this computer (and why), chosen with
`ditto runtime start --model`, only on request, or not pinned. model_to_download() picks the model a download is for:
the named tier, else the model a plain `runtime start` loads (the tier chosen earlier with --model, else the
recommendation; chain port-runtime: it used to be the recommendation even when another tier was chosen). The download
itself is LocalLLMRuntime.download_model (pinned revision, size
and sha256 checked; a set stop event ends it and keeps the `.part` file for a resume).

It reads only: nothing is downloaded, started or stopped here. The recommendation probes the hardware (seconds);
callers with a window run it off their GUI thread.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

STATES = ("ready", "unverified", "missing")   # LocalLLMRuntime.model_state
NOTES = ("recommended", "usable here", "candidate to evaluate", "failed here", "passed here", "not evaluated here",
         "chosen", "on request", "not pinned")


def gib(size: Optional[int]) -> str:
    """A model size as the CLI writes it ("4.4 GiB"; "?" when the catalogue has none)."""
    return f"{size / 1024 ** 3:.1f} GiB" if size else "?"


@dataclass
class ModelRow:
    """One tier of the catalogue as the list shows it."""
    model: Any                 # catalog.ModelEntry
    state: str                 # ready | unverified | missing
    recommended: bool
    chosen: bool
    advice: Optional[str] = None   # the rule's pick only: recommended | usable | candidate | failed (tier_advice)
    check: Optional[str] = None    # a 2-bit tier: passed | usable | failed on this computer, None when not measured

    @property
    def tier(self) -> str:
        return self.model.tier

    def notes(self) -> List[str]:
        """The list's "Note" column, in this order: recommended (or usable here, candidate to evaluate, failed here for
        the rule's pick of a 2-bit tier), for any other 2-bit tier passed here, usable here, failed here or not
        evaluated here, chosen, on request (only without a note before it), not pinned (never downloaded)."""
        notes = []
        if self.advice in ("usable", "candidate", "failed"):
            notes.append({"usable": "usable here", "candidate": "candidate to evaluate",
                          "failed": "failed here"}[self.advice])
        elif self.recommended:
            notes.append("recommended")
        elif getattr(self.model, "needs_eval", False):
            notes.append({"passed": "passed here", "usable": "usable here", "failed": "failed here"}.get(
                self.check or "", "not evaluated here"))
        if self.chosen:
            notes.append("chosen")
        if self.model.on_request and not notes:
            notes.append("on request")
        if not self.model.pinned:
            notes.append("not pinned")
        return notes


@dataclass
class ModelsOverview:
    rows: List[ModelRow]
    recommended: Any           # catalog.ModelEntry the tier rule picks for this computer (see `state`)
    why: str                   # the advice in words (why it is recommended, or that it is a candidate or failed)
    preferred: Optional[str]   # the tier chosen with `ditto runtime start --model` (None: the recommendation)
    folder: Path               # where the models are downloaded to
    state: str = "recommended"  # tier_advice: recommended | usable | candidate | failed
    action: Optional[str] = None  # the CLI's action for a candidate or failed tier (ditto eval --measure)

    @property
    def offered(self) -> bool:
        """Whether the rule's pick is offered to Ditto (recommended or usable), not only a candidate or failed."""
        return self.state in ("recommended", "usable")

    def heading(self) -> str:
        """The line under the list: "Recommended for this machine: ..." or the candidate's or failure's advice."""
        if self.state == "recommended":
            return f"Recommended for this machine: {self.why}"
        if self.state == "usable":
            return f"Offered for this machine as usable: {self.why}"
        return f"No local model is recommended yet: {self.why}"

    def row(self, tier: str) -> ModelRow:
        for row in self.rows:
            if row.tier == tier:
                return row
        raise KeyError(tier)


def models_overview(rt: Any) -> ModelsOverview:
    """Everything `ditto models list` shows, from `rt` (a LocalLLMRuntime)."""
    advice = rt.tier_advice()
    recommended = advice.model
    preferred = rt.preferred_tier()
    rows = []
    for m in rt.catalog.models:
        pick = m.tier == recommended.tier
        check = None
        if getattr(m, "needs_eval", False):
            check = advice.record.get("verdict") if pick and advice.record and not advice.stale else \
                (None if pick else rt.tier_record(m).verdict)
        rows.append(ModelRow(model=m, state=rt.model_state(m), recommended=pick and advice.offered,
                             chosen=m.tier == preferred, advice=advice.state if pick else None, check=check))
    return ModelsOverview(rows=rows, recommended=recommended, why=advice.text(), preferred=preferred,
                          folder=Path(rt.models_dir), state=advice.state, action=advice.action)


def model_to_download(rt: Any, tier: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    """The model `ditto models download [TIER]` is about: the named tier (why None; KeyError with the known tiers when
    the catalogue has no such tier); without a tier the model a plain `runtime start` loads (LocalLLMRuntime.
    resolve_model): the tier chosen earlier with `runtime start --model` (why None), else the recommendation for this
    computer with its reason (for a 2-bit candidate to evaluate the reason says it is one: it is downloaded to be
    evaluated)."""
    if tier is None:
        preferred = rt.preferred_tier()
        if preferred:
            return rt.catalog.model(preferred), None
        return rt.choose_tier()
    return rt.catalog.model(tier), None


def offline_refusal(rt: Any, model: Any, lang: Optional[str] = None) -> Optional[str]:
    """`ditto models download` (and every other download of a model for Ditto: Ditto > Local Models..., the runtime
    tools) in offline mode (DESIGN round 25 Q1): the plain refusal that names the models already on this computer,
    before any request; None when offline mode is off or the download address is in the person's own network (a local
    mirror, by the framework's one locality rule, utils/locality.py)."""
    from orchestrator.utils.locality import check_url, offline_mode
    settings = getattr(rt, "settings", None)
    if not offline_mode(settings):
        return None
    verdict = check_url(model.url(rt.catalog.model_base_url), settings)
    if verdict.local:
        return None
    from orchestrator.utils.locality import OPT_OUT_HELPS
    from orchestrator.utils.localization import ui_text
    here = [m for m in rt.catalog.models if rt.model_state(m) == "ready"]
    text = ui_text("ditto_models.offline", lang, file=model.file, why=verdict.why(lang))
    if verdict.reason in OPT_OUT_HELPS:   # as the locality rule's own refusal: the person may count it as local
        text += ui_text("net.refusal.opt_out", lang)
    listed = ", ".join(f"{m.tier} ({m.file})" for m in here)
    return f"{text} " + (ui_text("ditto_models.offline.here", lang, models=listed) if here else
                         ui_text("ditto_models.offline.none", lang))


@dataclass
class CacheRow:
    """One setting of the server's memory budget as `ditto cache` shows it."""
    key: str
    saved: str                 # auto or the person's value
    auto: Any                  # what auto stands for on this computer
    used: Any                  # what the next start uses
    soft_cap: Optional[int]
    hard_limit: Optional[int]
    reason: str
    over_soft_cap: bool


@dataclass
class CacheView:
    """The memory budget of Ditto's server (hardware.local_budget) for `ditto cache`, the AI settings' Prompt cache
    group and Ditto > Local Models: one row per setting, whether values above the soft cap were accepted, and what the
    running server got (from server.json; empty when none runs)."""
    rows: List[CacheRow]
    over_cap_ok: bool
    budget: Any
    running: dict

    def to_dict(self) -> dict:
        return {"settings": [vars(row) for row in self.rows], "over_cap_ok": self.over_cap_ok,
                "running": self.running}


def cache_overview(rt: Any) -> CacheView:
    """The memory budget of the server `rt` (a LocalLLMRuntime) would start now: the person's settings, their
    automatic values for this computer, the soft caps and hard limits, and the running server's values. Probes the
    hardware (seconds); numbers and words only, no folder."""
    budget = rt.cache_budget()
    saved = rt.cache_settings()
    used = {"local_cache_ram_mib": budget.ram_mib, "local_cache_keep_minutes": budget.keep_minutes,
            "local_kv_cache": budget.kv_cache}
    rows = [CacheRow(key=key, saved=saved.get(key, "auto"), auto=budget.auto.get(key), used=used[key],
                     soft_cap=budget.soft_caps.get(key), hard_limit=budget.hard_limits.get(key),
                     reason=budget.reasons.get(key, ""), over_soft_cap=key in budget.over_soft_cap)
            for key in used]
    get = getattr(getattr(rt, "settings", None), "get", None)
    status = rt.status()
    running = {}
    if status.running:
        running = {"cache_ram_mib": status.cache_ram_mib, "kv_cache": status.kv_cache, "n_ctx_slot": status.n_ctx_slot,
                   "ctx": status.ctx, "kv_fallback": status.kv_fallback}
    return CacheView(rows=rows, over_cap_ok=bool(get("local_cache_over_cap_ok", False)) if callable(get) else False,
                     budget=budget, running=running)


__all__ = ["NOTES", "STATES", "CacheRow", "CacheView", "ModelRow", "ModelsOverview", "cache_overview", "gib",
           "model_to_download", "models_overview", "offline_refusal"]
