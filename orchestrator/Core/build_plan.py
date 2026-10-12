#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - The plan of one build, before anything runs (MCP design 2.2, 2.3, 6.5, 6.6)

`plan_build` answers what a build would do: the normalized request, the Hugging Face commit it would fetch (or the
local file it would mount), the download size, weights type, licence and gating with the stored token's access, the
disk rule with the conversion margin and the reservations of other calls, the hardware estimate for this computer and
the board, the mounts (by label), the target's trust, whether a Docker image is built first (with network) and the
build sandbox. check_build_request returns it as a dry run; start_build shows it in its question and builds exactly
the commit it shows. Anything that stops the build is a `problem` with a reason code and a next step; the hardware
estimate never is one by itself.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.Core import disk_plan
from orchestrator.Core.disk_plan import DiskCheck, DiskNeed, Fit, gb
from orchestrator.Core.hf_lookup import HubLookupError, ModelFacts
from orchestrator.tools.spec import CallFacts, Fact

REPO_TEXT = "repository text from huggingface.co"
TARGET_TEXT = "from the target's files"
_HARDWARE: Dict[str, Any] = {}  # this computer's probe, kept for a minute (the probe runs nvidia-smi and friends)
HARDWARE_TTL = 60.0


@dataclass
class Problem:
    code: str
    message: str
    next: str = ""


@dataclass
class BuildPlan:
    request: Any                 # the validated request (target id, canonical format, board, quantization)
    model: str                   # the model as the caller named it: a repo id or <label>:<relative path>
    source: str                  # hf or local
    optimization: str = "balanced"
    facts: Optional[ModelFacts] = None
    commit: Optional[str] = None
    download: int = 0
    download_known: bool = True
    cached: int = 0
    local_bytes: int = 0
    needs: List[DiskNeed] = field(default_factory=list)
    disk: List[DiskCheck] = field(default_factory=list)
    host: Fit = field(default_factory=lambda: Fit("unknown", source="not estimated"))
    target: Fit = field(default_factory=lambda: Fit("unknown", source="not estimated"))
    mounts: List[str] = field(default_factory=list)
    target_name: str = ""
    target_trust: str = "shipped"
    trust_note: str = ""
    support_level: str = ""
    image_build: str = ""
    base_image: str = ""
    base_image_class: str = ""
    sandbox: bool = True
    hub_problem: str = ""        # Hugging Face could not be asked (network): sizes and commit unknown
    grant: Optional[Dict[str, Any]] = None  # facts for grants.BUILD_RULE; None: outside every standing approval
    # what a build the person confirmed records (confirmed_builds): the target and recipe as installed, base images
    confirm_record: Optional[Dict[str, Any]] = None
    problems: List[Problem] = field(default_factory=list)

    # --- the question --------------------------------------------------------------------------------------------

    def commit_text(self) -> str:
        if self.source != "hf":
            return ""
        if self.commit and self.request.revision:
            return f"commit {self.commit[:12]}, as requested"
        return f"commit {self.commit[:12]}, the default branch now" if self.commit else "default branch, commit unknown"

    def consequence(self) -> str:
        r = self.request
        what = f"{self.target_name or r.target}" + (f" {r.board}" if r.board else "")
        build = (f"runs a Docker build for {what} ({r.format}" + (f" {r.quantization}" if r.quantization else "")
                 + f", optimization {self.optimization}) that may take a long time")
        if self.source == "hf":
            size = f"about {gb(self.download)}" if self.download_known else "an unknown amount"
            first = f"Downloads {size} from huggingface.co ({self.commit_text()}) and {build}."
        else:
            first = f"Uses the local model {self.model} and {build}."
        disk = "; ".join(c.sentence() for c in self.disk)
        parts = [first]
        if disk:
            parts.append(f"Disk: {disk}.")
        parts.append(self.host.sentence("Conversion on this computer") + ".")
        parts.append(self.target.sentence(f"On {r.board or 'the board'}") + ".")
        return " ".join(parts)

    def question_facts(self) -> Tuple[Fact, ...]:
        r = self.request
        facts = [Fact("Build", f"{self.model} for {self.target_name or r.target}"
                      + (f" ({r.board})" if r.board else "") + f", {r.format}"
                      + (f" {r.quantization}" if r.quantization else "") + f", optimization {self.optimization}")]
        if self.source == "hf" and self.facts is not None:
            f = self.facts
            facts.append(Fact("Commit", self.commit_text()))
            facts.append(Fact("Download", f"{len(f.staged)} files, about {gb(self.download)}"
                              + (f" ({gb(self.cached)} already staged for this commit)" if self.cached else "")
                              + ("" if self.download_known else "; some sizes unknown")))
            weights = {"safetensors": "safetensors", "gguf": "GGUF",
                       "pickle": "pickle files only (.bin/.pt): loading them can run code inside the build "
                                 "container; the build sandbox contains it", "none": "no weight files found"}
            facts.append(Fact("Weights", weights.get(f.weights, f.weights)))
            facts.append(Fact("Licence", f"{f.license} ({f.license_class})", REPO_TEXT))
            if f.base_model:
                facts.append(Fact("Base model", f.base_model, REPO_TEXT))
            gating = "not gated" if f.gated == "false" else f"gated ({f.gated} approval on huggingface.co)"
            if f.token_access == "yes":
                gating += "; your Hugging Face token stored in the framework has access"
            facts.append(Fact("Gating", gating))
            if f.parameters:
                facts.append(Fact("Parameters", f"about {f.parameters / 1e9:.2f} billion (from the Hub)"))
        elif self.source == "hf":
            facts.append(Fact("Commit", self.commit_text()))
            facts.append(Fact("Download", "size unknown: Hugging Face could not be asked; the disk is not checked "
                              "for the download"))
            if self.hub_problem:
                facts.append(Fact("Hugging Face", self.hub_problem, "a reply from huggingface.co or the network"))
        elif self.source == "local":
            facts.append(Fact("Local model", f"{self.model}, {gb(self.local_bytes)}"))
        for check in self.disk:
            facts.append(Fact("Disk", check.sentence()))
        facts.append(Fact("Hardware", self.host.sentence("conversion on this computer")))
        facts.append(Fact("Hardware", self.target.sentence(f"running on {r.board or 'the board'}")))
        if self.mounts:
            facts.append(Fact("Mounted into the build", ", ".join(self.mounts) + " (read-only)"))
        facts.append(Fact("Target", f"{self.target_trust}" + (f", support level {self.support_level}"
                                                              if self.support_level else "")
                          + (f"; {self.trust_note}" if self.trust_note else "")))
        facts.append(Fact("Image build", self.image_build))
        if self.base_image:
            facts.append(Fact("Base image", f"{self.base_image} ({self.base_image_class})", TARGET_TEXT))
        facts.append(Fact("Sandbox", "the conversion runs without network, capabilities or root (build_sandbox)"
                          if self.sandbox else "build_sandbox is off"))
        return tuple(facts)

    def bound(self) -> Dict[str, Any]:
        """What the handler must use: the commit shown, and the sizes the disk reservation is taken for."""
        return {"model": self.model, "revision": self.commit, "download": self.download, "cached": self.cached,
                "local_bytes": self.local_bytes, "format": self.request.format,
                "quantization": self.request.quantization, "optimization": self.optimization,
                "target": self.request.target, "board": self.request.board, "grant": self.grant,
                "confirm_record": self.confirm_record}

    def call_facts(self) -> CallFacts:
        return CallFacts(consequence=self.consequence(), facts=self.question_facts(), bound=self.bound())


# --- helpers -------------------------------------------------------------------------------------------------------

def _config(framework, key: str, default=None):
    config = getattr(framework, "config", None)
    get = getattr(config, "get", None)
    try:
        return get(key, default) if callable(get) else getattr(config, key, default)
    except Exception:
        return default


def folder(framework, key: str, default: str) -> Path:
    resolve = getattr(framework, "resolve_path", None)
    value = _config(framework, key, default) or default
    return Path(resolve(value)) if callable(resolve) else Path(str(value)).expanduser()


def state_dir(framework) -> Optional[Path]:
    """The per-user folder (None for a stand-in framework without one)."""
    try:
        value = getattr(getattr(framework, "config", None), "user_state_dir", None)
    except Exception:  # noqa: BLE001 - ConfigManager raises AttributeError for unknown names
        value = None
    return Path(value) if isinstance(value, (str, Path)) else None


def host_hardware(framework) -> Optional[Any]:
    """This computer's RAM and GPUs from the hardware probe (cached for a minute); None when it cannot be probed."""
    cached = _HARDWARE.get("value")
    if cached is not None and time.monotonic() - _HARDWARE.get("at", 0) < HARDWARE_TTL:
        return cached
    try:
        from orchestrator.runtime.hardware import detect_hardware
        value = detect_hardware()
    except Exception:  # noqa: BLE001 - an estimate is never a reason to fail
        return None
    _HARDWARE.update(value=value, at=time.monotonic())
    return value


def _best_vram(hw) -> Optional[int]:
    gpus = [g for g in getattr(hw, "gpus", []) or [] if g.vram_mib and not g.unified]
    return max(g.vram_mib for g in gpus) * 1024 ** 2 if gpus else None


def registry_class(image: str) -> str:
    """Docker Official Image, or plainly the other registry the image comes from (design 6.6 rule 11)."""
    ref = image.split("@", 1)[0]
    first = ref.split("/", 1)[0]
    if "/" not in ref or ref.startswith(("library/", "docker.io/library/")):
        return "Docker Official Image"
    if "." in first or ":" in first or first == "localhost":
        return f"other registry: {first}; prebuilt image made by its publisher, not checked by the framework"
    return "other registry: docker.io (not an official image); prebuilt image made by its publisher, not checked"


def _base_image(target_folder: Optional[Path]) -> str:
    if target_folder is None:
        return ""
    for name in ("dockerfile", "Dockerfile"):
        path = target_folder / name
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    match = re.match(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)", line, re.IGNORECASE)
                    if match:
                        return match.group(1)[:200]
            except OSError:
                return ""
    return ""


def _size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _cached_bytes(cache_dir: Path, repo: str, commit: Optional[str], facts: ModelFacts) -> int:
    """Bytes of this commit that are already staged (its own staging folder; files of the right size only)."""
    if not commit or "/" not in repo:
        return 0
    owner, name = repo.split("/", 1)
    staged = cache_dir / "models" / "hf" / f"{owner}--{name}" / commit
    total = 0
    for f in facts.staged:
        path = staged / f.name
        try:
            if f.size is not None and path.is_file() and path.stat().st_size == f.size:
                total += f.size
        except OSError:
            continue
    return total


MAX_LOCAL_NAMES = 10000  # files looked at in a local model folder; more: "other" (outside every standing approval)


def _local_weights(path: Path) -> str:
    """safetensors, gguf, pickle or other for a local model file or folder (never read beyond its names). A folder is
    mounted whole into the build, so pickle weights anywhere in it make it "pickle", even beside safetensors (design
    6.10: never pickle weights under a standing approval)."""
    from orchestrator.tools.grants import PICKLE_SUFFIXES
    if path.is_file():
        names = [path.name]
    elif path.is_dir():
        names = []
        for p in path.rglob("*"):
            if len(names) >= MAX_LOCAL_NAMES:
                return "other"
            if p.is_file() or p.is_symlink():
                names.append(p.name)
    else:
        names = []
    lowered = [n.lower() for n in names]
    if any(n.endswith(PICKLE_SUFFIXES) for n in lowered):
        return "pickle"
    if any(n.endswith(".safetensors") for n in lowered):
        return "safetensors"
    if any(n.endswith(".gguf") for n in lowered):
        return "gguf"
    return "other"


def _target_images(target_folder: Optional[Path]) -> List[str]:
    """Every image the target's Dockerfiles build from (FROM, COPY --from, RUN --mount from), as the pack question
    showed them; [] without a Dockerfile."""
    if target_folder is None:
        return []
    try:
        from orchestrator.tools.tools_community import pack_base_images
        return list(dict.fromkeys(image for _, image in pack_base_images(Path(target_folder))))
    except Exception:  # noqa: BLE001 - images that cannot be read are no official images
        return ["?"]


def confirm_record(plan: "BuildPlan", framework, target_folder: Optional[Path],
                   recipe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What a confirmed build of this plan records (confirmed_builds.ConfirmedBuilds): the target (and recipe) as
    installed, and the target's base images."""
    installed_via, pack_sha = "", ""
    try:
        from orchestrator.Core.community_manager import read_provenance
        provenance = read_provenance(target_folder) if target_folder is not None else None
        if provenance is not None:
            installed_via = str(getattr(provenance, "installed_via", "") or "")
            pack_sha = str(getattr(provenance, "pack_sha256", "") or "")
    except Exception:  # noqa: BLE001
        pass
    record = {"target": plan.request.target, "installed_via": installed_via, "pack_sha256": pack_sha,
              "base_images": _target_images(target_folder)}
    if recipe:
        record["recipe"] = {"id": recipe.get("id"), "installed_via": recipe.get("installed_via") or "",
                            "pack_sha256": recipe.get("pack_sha256") or ""}
    return record


def grant_facts(plan: "BuildPlan", service, framework, target_folder: Optional[Path], pin, ledger,
                record: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """What a standing approval for start_build is matched against (grants.BUILD_RULE, design 6.10): target trust
    (shipped: no pack provenance, not AI-generated, support level official; verified: a verified pack), how the target
    (and a recipe) was installed and whether the person has since confirmed a build of it, the weights type, the
    base images (Docker Official Images, or a digest-pinned image the person confirmed in a build of this target
    before), the source with gating, base-model gating and licence, and the download size. None (the person is asked)
    when the plan has a problem or the disk does not keep the larger reserve of a standing approval (20 GiB or 10 %)."""
    if plan.problems:
        return None
    trust = "other"
    try:
        from orchestrator.Core.community_manager import ai_generated_target, read_provenance
        provenance = read_provenance(target_folder) if target_folder is not None else None
        if provenance is not None:
            trust = "verified" if plan.target_trust == "verified" else plan.target_trust
        elif target_folder is not None and not ai_generated_target(target_folder) and plan.support_level == "official":
            trust = "shipped"
    except Exception:  # noqa: BLE001 - trust that cannot be read is no trust
        trust = "other"
    if plan.needs:
        standing = disk_plan.check_disk(plan.needs, ledger, standing=True, disk_usage=service.disk_usage)
        if any(not c.fits for c in standing):
            return None
    record = record or confirm_record(plan, framework, target_folder)
    store = None
    state = state_dir(framework)
    if state is not None:
        from orchestrator.Core.confirmed_builds import ConfirmedBuilds
        store = ConfirmedBuilds(state)
    # a pack installed under a standing approval counts only after the person confirmed one build of it
    parts = [("target", record["target"], record["installed_via"], record["pack_sha256"])]
    if record.get("recipe"):
        r = record["recipe"]
        parts.append(("recipe", r["id"], r["installed_via"], r["pack_sha256"]))
    pending = [p for p in parts if str(p[2]).startswith("grant:")
               and not (store is not None and store.confirmed(p[0], p[1], installed_via=p[2], pack_sha256=p[3]))]
    images = record["base_images"]
    official = bool(images) and all(
        registry_class(i) == "Docker Official Image" and "$" not in i or
        (store is not None and store.image_confirmed(record["target"], i, installed_via=record["installed_via"],
                                                     pack_sha256=record["pack_sha256"]))
        for i in images)
    facts = {"target": plan.request.target, "target_trust": trust,
             "installed_via": pending[0][2] if pending else record["installed_via"],
             "confirmed_build": not pending, "image_base_official": official, "source": plan.source,
             "model": plan.model}
    if plan.source == "hf":
        f = plan.facts
        if f is None:
            return None
        facts.update(weights_format=f.weights, gated=f.gated != "false", base_gated=f.base_gated,
                     license=f.license, size_bytes=plan.download if plan.download_known else None)
    else:
        facts["weights_format"] = _local_weights(Path(pin.path)) if pin is not None else "other"
    return facts


# --- the plan ------------------------------------------------------------------------------------------------------

def plan_build(service, request, *, look_up_hub: bool = True, ledger=None, standing: bool = False,
               recipe: Optional[Dict[str, Any]] = None) -> BuildPlan:
    """The plan of an AI caller's build. Input problems (an unknown target, a format the target does not offer, a
    path instead of a local-file reference) raise like start_build does; problems that stop this build otherwise are
    collected in `problems`."""
    framework = service.framework
    optimization = getattr(request, "optimization", "balanced") or "balanced"
    base = request.model_dump(exclude={"optimization", "include_download_size"})
    from orchestrator.schemas.build import BuildRequestIn
    req, inputs = service._validated(BuildRequestIn(**base), False)
    pin = getattr(inputs, "model", None)
    shown = pin.ref if pin is not None else req.model
    plan = BuildPlan(request=req.model_copy(update={"model": shown, "dataset_path": (
        inputs.dataset.ref if getattr(inputs, "dataset", None) is not None else None)}), model=shown,
        source="local" if pin is not None else "hf", optimization=optimization)
    caps = service.capabilities(req.target)

    # target, trust, image build
    try:
        names = {t.id: t.name for t in service.list_targets().targets}
        plan.target_name = str(names.get(req.target) or req.target)
    except Exception:  # noqa: BLE001
        plan.target_name = req.target
    plan.support_level = getattr(caps.support_level, "value", "") if caps.support_level else ""
    target_folder = None
    try:
        tm = getattr(framework, "target_manager", None)
        target_folder = tm.folder_of(req.target) if tm is not None and hasattr(tm, "folder_of") else None
    except Exception:  # noqa: BLE001
        target_folder = None
    if target_folder is not None:
        try:
            from orchestrator.Core.community_manager import CommunityManager, trust_of_folder
            state = state_dir(framework)
            index = CommunityManager.read_cached_index(Path(state) / "community", revocations=True) if state else None
            trust, reason = trust_of_folder(target_folder, index)
            if trust is not None:
                plan.target_trust = trust.value
                if trust.value == "revoked":
                    plan.problems.append(Problem("not_supported", f"The target {req.target} was revoked by the "
                                                 f"community index ({reason}); it is not built.",
                                                 "Tell the person; they remove the target in the Community Hub."))
                elif trust.value == "local":
                    plan.trust_note = "imported from a file that is not in the signed community index; nobody reviewed its scripts"
        except Exception:  # noqa: BLE001 - trust is shown when it can be read
            pass
    plan.image_build = ("a Docker image for the target is built first if none exists yet; the image build runs with "
                        "network access on this computer, outside the build sandbox (size unknown, first build only)")
    if plan.support_level != "official" or plan.target_trust != "shipped":
        plan.base_image = _base_image(target_folder)
        plan.base_image_class = registry_class(plan.base_image) if plan.base_image else ""

    # sandbox
    plan.sandbox = bool(_config(framework, "build_sandbox", True))
    if not plan.sandbox:
        plan.problems.append(Problem("needs_person", "The build sandbox is switched off (setting build_sandbox); a "
                                     "build an AI starts runs only in the sandbox.",
                                     "Ask the person to switch build_sandbox on (update_settings can set it to true "
                                     "with their confirmation, or they do it in the GUI or CLI)."))

    # the model
    if pin is not None:
        plan.local_bytes = _size_of(Path(pin.path))
        plan.mounts.append(pin.ref)
    else:
        plan.mounts.append(f"model cache: {req.model}" + "@commit")
        if bool(_config(framework, "offline_mode", False)):
            plan.problems.append(Problem("needs_person", "Offline mode is on, so the framework does not contact "
                                         "huggingface.co.", "Use a local model (list_local_files), or ask the person "
                                         "to switch offline mode off in the GUI or CLI."))
        elif look_up_hub:
            try:
                plan.facts = service.hub_facts(req.model, req.revision)
            except HubLookupError as e:
                if e.code == "failed":  # Hugging Face not reachable: the question says "commit unknown"
                    plan.hub_problem = str(e)
                    plan.commit, plan.download_known = req.revision, False
                else:  # gated without access, unknown repository: refused before the question
                    plan.problems.append(Problem(e.code, str(e), e.next))
            if plan.facts is not None:
                plan.commit = plan.facts.commit or req.revision
                plan.download = plan.facts.download_bytes
                plan.download_known = plan.facts.sizes_known
                plan.cached = _cached_bytes(folder(framework, "cache_dir", "cache"), req.model, plan.commit,
                                            plan.facts)
                if plan.commit:
                    plan.mounts[-1] = f"model cache: {req.model}@{plan.commit[:12]} (only this commit's folder)"
                if plan.facts.weights == "none":
                    plan.problems.append(Problem("not_supported", f"{req.model} has no weights the build can convert "
                                                 "(no safetensors or PyTorch files; GGUF-only repositories are not "
                                                 "converted by start_build).",
                                                 "Pick a repository with safetensors weights, or download a single "
                                                 "file with download_hf_file when it is offered."))
        else:
            plan.commit = req.revision
            plan.download_known = False
    if getattr(inputs, "dataset", None) is not None:
        plan.mounts.append(inputs.dataset.ref)

    # disk
    size = plan.download if plan.source == "hf" else plan.local_bytes
    if size:
        plan.needs = disk_plan.build_needs(size, req.format, req.quantization, folder(framework, "cache_dir", "cache"),
                                           folder(framework, "output_dir", "output"),
                                           cached=plan.cached if plan.source == "hf" else size)
        plan.disk = disk_plan.check_disk(plan.needs, ledger, standing=standing, disk_usage=service.disk_usage)
        try:
            disk_plan.require(plan.disk)
        except disk_plan.InsufficientSpace as e:
            plan.problems.append(Problem("insufficient_resources", str(e), e.next))

    plan.confirm_record = confirm_record(plan, framework, target_folder, recipe)
    plan.grant = grant_facts(plan, service, framework, target_folder, pin, ledger, plan.confirm_record)

    # hardware estimate (never a refusal)
    params, basis = disk_plan.parameters_from(size, plan.facts.parameters if plan.facts else None)
    hw = service.host_hardware()
    plan.host = disk_plan.host_fit(req.format, req.use_imatrix, params, getattr(hw, "ram_total", None),
                                   _best_vram(hw) if hw is not None else None, req.use_gpu, basis)
    board = caps.boards.get(req.board) if req.board else None
    output = int(size * disk_plan.output_share(req.quantization)) if size else 0
    plan.target = disk_plan.target_fit(output, None, req.board, getattr(board, "ram_max_gb", None))
    return plan
