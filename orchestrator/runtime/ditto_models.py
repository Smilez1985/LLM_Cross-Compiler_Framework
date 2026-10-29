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
NOTES = ("recommended", "chosen", "on request", "not pinned")


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

    @property
    def tier(self) -> str:
        return self.model.tier

    def notes(self) -> List[str]:
        """The list's "Note" column, in this order: recommended, chosen, on request (only without the first two), not
        pinned (never downloaded)."""
        notes = []
        if self.recommended:
            notes.append("recommended")
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
    recommended: Any           # catalog.ModelEntry recommended for this computer
    why: str                   # why it is recommended
    preferred: Optional[str]   # the tier chosen with `ditto runtime start --model` (None: the recommendation)
    folder: Path               # where the models are downloaded to

    def row(self, tier: str) -> ModelRow:
        for row in self.rows:
            if row.tier == tier:
                return row
        raise KeyError(tier)


def models_overview(rt: Any) -> ModelsOverview:
    """Everything `ditto models list` shows, from `rt` (a LocalLLMRuntime)."""
    recommended, why = rt.choose_tier()
    preferred = rt.preferred_tier()
    rows = [ModelRow(model=m, state=rt.model_state(m), recommended=m.tier == recommended.tier,
                     chosen=m.tier == preferred) for m in rt.catalog.models]
    return ModelsOverview(rows=rows, recommended=recommended, why=why, preferred=preferred, folder=Path(rt.models_dir))


def model_to_download(rt: Any, tier: Optional[str] = None) -> Tuple[Any, Optional[str]]:
    """The model `ditto models download [TIER]` is about: the named tier (why None; KeyError with the known tiers when
    the catalogue has no such tier); without a tier the model a plain `runtime start` loads (LocalLLMRuntime.
    resolve_model): the tier chosen earlier with `runtime start --model` (why None), else the recommendation for this
    computer with its reason."""
    if tier is None:
        preferred = rt.preferred_tier()
        if preferred:
            return rt.catalog.model(preferred), None
        return rt.choose_tier()
    return rt.catalog.model(tier), None


def offline_refusal(rt: Any, model: Any, lang: Optional[str] = None) -> Optional[str]:
    """`ditto models download` (and every other download of a model for Ditto: Ditto > Local Models..., the runtime
    tools) in offline mode (DECISIONS round 25 Q1): the plain refusal that names the models already on this computer,
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


__all__ = ["NOTES", "STATES", "ModelRow", "ModelsOverview", "gib", "model_to_download", "models_overview",
           "offline_refusal"]
