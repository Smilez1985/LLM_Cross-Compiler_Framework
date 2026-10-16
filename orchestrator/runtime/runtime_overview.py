#!/usr/bin/env python3
"""
Everything `llm-cli ditto runtime status` shows about Ditto's local runtime, gathered in one place (W1': the GUI's
Ditto > Local Runtime... window shows the same, from the same call).

overview() asks the runtime once: the status of the build in use and of the server (with the rebuild advice, which
plans the build and so detects the toolchain), the hardware, the backend a self-built install would make active, the
official prebuilt archive the person may opt into, and the recommended and the chosen model tier. The CLI prints it as
a table (or as JSON with as_json()), the GUI as plain text with its own words. Which rows exist and what they hold is
decided here, so both show the same: build_facts() for the "Built" row, extra_problems() for the problems that are no
note of the build in use, gpu_after_cpu for the self-built plan, chosen_differs for the tier chosen with
`start --model`.

It reads only: nothing is built, downloaded, started or stopped. It needs no keyring and no Docker (the Docker image
of Docker mode is reported from mode.json).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def installed_json(inst: Any) -> Optional[Dict[str, Any]]:
    """The build in use as `status --json` names it (None when there is none)."""
    if inst is None:
        return None
    data = inst.model_dump(mode="json", include={"origin", "build_id", "commit", "backend", "variant", "asset_id",
                                                 "role", "placement", "built_on", "built_at", "verified_at",
                                                 "duration_s", "devices", "notes"})
    data["label"] = inst.label
    data["path"] = str(inst.path)
    return data


def installed_summary(inst: Any) -> str:
    """One line: the origin label, build, backend and commit of the build in use (or the prebuilt archive)."""
    if inst.build_id:
        role = ", CPU fallback" if inst.role == "fallback" else ""
        return (f"{inst.label}: build {inst.build_id[:16]} ({inst.backend}{role}), llama.cpp {inst.build} commit "
                f"{(inst.commit or '')[:8]}")
    return f"{inst.label}: {inst.asset_id} ({inst.backend})"


@dataclass
class RuntimeOverview:
    status: Any                  # llama_server.RuntimeStatus (with the rebuild advice)
    hardware: Any                # hardware.HardwareInfo
    backend: str                 # what the self-built install makes active here
    backend_reason: str
    prebuilt: Any                # hardware.BackendChoice: the official archive the person may opt into
    model: Any                   # catalog.ModelEntry recommended for this machine
    tier_reason: str
    preferred: Optional[str]     # the tier chosen with `start --model` (None: the recommendation is used)

    @property
    def installed(self) -> Any:
        return self.status.installed

    @property
    def gpu_after_cpu(self) -> bool:
        """Whether the self-built plan is "the CPU build, then the GPU build of `backend`" (else only `backend`)."""
        return not (self.backend == "cpu" or not self.backend_reason)

    @property
    def chosen_differs(self) -> bool:
        """Whether a tier chosen earlier replaces the recommendation."""
        return bool(self.preferred) and self.preferred != self.model.tier

    def build_facts(self) -> Optional[Dict[str, Any]]:
        """The "Built" row of a self-built or imported build in use: placement, duration in seconds, the machine it was
        built on (imported builds only) and when; None for the prebuilt archive or when nothing is in use."""
        inst = self.installed
        if inst is None or not inst.build_id:
            return None
        return {"placement": inst.placement, "duration_s": inst.duration_s,
                "built_on": inst.built_on if inst.built_on and inst.origin == "imported" else None,
                "built_at": inst.built_at}

    def extra_problems(self) -> List[str]:
        """Why a stored build cannot be used, without what the build in use already names as its notes."""
        inst = self.installed
        return [p for p in self.status.problems if inst is None or p not in inst.notes]

    def as_json(self) -> Dict[str, Any]:
        """What `llm-cli ditto runtime status --json` prints."""
        status = self.status
        return {
            "build": status.build, "hardware": self.hardware.model_dump(mode="json"), "mode": status.mode,
            "docker": status.docker, "container": status.container,
            "installed": installed_json(status.installed), "problems": status.problems, "rebuild": status.rebuild,
            "recommended_backend": self.backend, "backend_reason": self.backend_reason,
            "prebuilt_option": self.prebuilt.asset.id if self.prebuilt.asset else None,
            "prebuilt_reason": self.prebuilt.reason,
            "running": status.running, "healthy": status.healthy, "base_url": status.base_url, "pid": status.pid,
            "model": status.model, "tier": status.tier, "ctx": status.ctx,
            "recommended_tier": self.model.tier, "tier_reason": self.tier_reason, "preferred_tier": self.preferred,
            "log_file": str(status.log_file)}


def overview(rt: Any) -> RuntimeOverview:
    """Asks `rt` (a LocalLLMRuntime) for everything the status shows. Plans the build for the rebuild advice and probes
    the hardware: seconds, not minutes; callers with a window run it off their GUI thread."""
    status = rt.status(check_rebuild=True)
    hardware = rt.hardware()
    backend, backend_reason = rt.recommend_backend()
    prebuilt = rt.select_backend()
    model, why = rt.choose_tier()
    return RuntimeOverview(status=status, hardware=hardware, backend=backend, backend_reason=backend_reason,
                           prebuilt=prebuilt, model=model, tier_reason=why, preferred=rt.preferred_tier())


__all__ = ["RuntimeOverview", "installed_json", "installed_summary", "overview"]
