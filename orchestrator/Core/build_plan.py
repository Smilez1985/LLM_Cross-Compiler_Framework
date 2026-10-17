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

The facts model of the delegation design (4.1-4.6, work package WP4): every problem names its blocker of the facts
catalogue; the plan's warnings (situation: tight disk, large download, tight or too small memory; security and legal:
pickle weights, a base image outside the Docker Official Images or not pinned, an unreviewed target, the first build
of build code an AI made or installed, facts that could not be checked, gated models and licences) go with the
question. Free space, exact byte counts and the hardware sentences are volatile, so they alone never make a question
new: the decision digest covers the request, the size class, the warnings and what a confirmation binds.
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.Core import disk_plan
from orchestrator.Core.disk_plan import DiskCheck, DiskNeed, Fit, gb
from orchestrator.Core.hf_lookup import HubLookupError, ModelFacts
from orchestrator.tools import facts_catalog as catalog
from orchestrator.tools.spec import CallFacts, CallWarning, Fact

REPO_TEXT = "repository text from huggingface.co"
TARGET_TEXT = "from the target's files"
_HARDWARE: Dict[str, Any] = {}  # this computer's probe, kept for a minute (the probe runs nvidia-smi and friends)
HARDWARE_TTL = 60.0


@dataclass
class Problem:
    code: str
    message: str
    next: str = ""
    blocker: str = ""            # the facts catalogue's blocker code (disk_full, offline_mode, ...)


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
    # (file, image) of every image the target's Dockerfiles build from, shown in the question one by one; only these
    # (and Docker Official Images) can be remembered by a confirmed build
    base_images: List[Tuple[str, str]] = field(default_factory=list)
    sandbox: bool = True
    hub_problem: str = ""        # Hugging Face could not be asked (network): sizes and commit unknown
    grant: Optional[Dict[str, Any]] = None  # facts for grants.BUILD_RULE; None: outside every standing approval
    # what a build the person confirmed records (confirmed_builds): the target and recipe as installed, base images
    confirm_record: Optional[Dict[str, Any]] = None
    # the grant facts without the disk check of a standing approval: the decision digest covers them, never the free
    # space that decides whether `grant` is set
    grant_scope: Optional[Dict[str, Any]] = None
    weights: str = ""            # safetensors, gguf, pickle, none or other (a local model's from its file names)
    # build code an AI made or installed that the person has not confirmed a build of yet ([Q1]): (kind, id)
    first_build: List[Tuple[str, str]] = field(default_factory=list)
    # base images outside the Docker Official Images the person never confirmed in a build of this target (what a
    # standing approval may not cover)
    unconfirmed_images: List[str] = field(default_factory=list)
    # every base image outside the Docker Official Images: a security warning on every build, confirmed before or not
    # (only an acknowledgement bound to the digest, design question Q3, could lift it; none exists yet)
    outside_images: List[str] = field(default_factory=list)
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
                              + ("" if self.download_known else "; some sizes unknown"), volatile=True))
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
        # Free space and the estimates' numbers change while a question waits: shown, never part of the digest (their
        # verdicts are, as warnings and as the fit numbers).
        for check in self.disk:
            facts.append(Fact("Disk", check.sentence(), volatile=True))
        facts.append(Fact("Hardware", self.host.sentence("conversion on this computer"), volatile=True))
        facts.append(Fact("Hardware", self.target.sentence(f"running on {r.board or 'the board'}"), volatile=True))
        if self.mounts:
            facts.append(Fact("Mounted into the build", ", ".join(self.mounts) + " (read-only)"))
        facts.append(Fact("Target", f"{self.target_trust}" + (f", support level {self.support_level}"
                                                              if self.support_level else "")
                          + (f"; {self.trust_note}" if self.trust_note else "")))
        facts.append(Fact("Image build", self.image_build))
        if self.base_images:
            from orchestrator.tools.tools_community import base_image_line
            for file, image in self.base_images:
                facts.append(Fact(f"Base image ({file})", base_image_line(image), TARGET_TEXT))
        elif self.base_image:
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
                "confirm_record": self.confirm_record, "size_class": catalog.size_class(self.size()),
                "grant_scope": self.grant_scope, "weights": self.weights,
                # False when the Hub could not be asked: under a standing approval or a delegation that is the blocker
                # commit_required (tools_builds.require_commit), never a build of an unseen commit
                "commit_resolved": self.source != "hf" or bool(self.facts is not None and self.facts.commit)}

    # What a decision about the build is bound to (design 4.4): the request, the size class, the grant facts without
    # the disk-derived flag, what a confirmed build records (target, recipe, module, base image digests) and the
    # weights class; never the exact byte counts, the free space or the standing approval's disk check.
    DIGEST_KEYS = ("model", "revision", "format", "quantization", "optimization", "target", "board", "size_class",
                   "grant_scope", "confirm_record", "weights")

    def size(self) -> int:
        """The model's size: the download of the commit, or the local file or folder."""
        return int(self.download if self.source == "hf" else self.local_bytes)

    def consequence_template(self) -> str:
        """The consequence with placeholders where volatile values stand (free space, sizes, the estimates)."""
        r = self.request
        what = f"{self.target_name or r.target}" + (f" {r.board}" if r.board else "")
        build = (f"runs a Docker build for {what} ({r.format}" + (f" {r.quantization}" if r.quantization else "")
                 + f", optimization {self.optimization}) that may take a long time")
        if self.source == "hf":
            first = f"Downloads {{download}} from huggingface.co ({self.commit_text()}) and {build}."
        else:
            first = f"Uses the local model {self.model} and {build}."
        parts = [first] + (["Disk: {disk}."] if self.disk else [])
        parts += ["Conversion on this computer: {host_fit}.", f"On {r.board or 'the board'}: {{target_fit}}."]
        return " ".join(parts)

    def warnings(self) -> Tuple[CallWarning, ...]:
        """The warnings the person (or, for situation warnings, an AI with a delegation) weighs before this build."""
        found: List[CallWarning] = []
        tight = [c for c in self.disk if c.verdict == "tight"]
        if tight:
            worst = min(tight, key=lambda c: c.after - c.reserve)
            found.append(catalog.warn("disk_tight", free_after_gb=catalog.gb_number(worst.after),
                                      reserve_gb=catalog.gb_number(worst.reserve)))
        if self.source == "hf" and self.download_known and self.download >= catalog.LARGE_DOWNLOAD_BYTES:
            found.append(catalog.warn("download_large", download_gb=catalog.gb_number(self.download)))
        for fit, prefix in ((self.host, "host_fit"), (self.target, "target_fit")):
            if fit.state in ("tight", "does_not_fit"):
                found.append(catalog.warn(f"{prefix}_{fit.state}", needed_gb=catalog.gb_number(fit.needed),
                                          usable_gb=catalog.gb_number(fit.usable)))
        unknown: List[str] = []
        if self.source == "hf":
            f = self.facts
            if f is None:
                unknown.append("huggingface")  # the Hub did not answer: licence, gating and format are unknown
            else:
                if f.weights == "pickle":
                    found.append(catalog.warn("pickle_weights"))
                if f.gated != "false":
                    found.append(catalog.warn("gated_model"))
                if f.base_gated is None:
                    unknown.append("base_model")
                elif f.base_gated:
                    found.append(catalog.warn("base_model_gated"))
                if f.license_class == "restrictive":
                    found.append(catalog.warn("license_restrictive"))
                elif f.license_class != "permissive":
                    if (f.license or "unknown").strip().lower() == "unknown":
                        unknown.append("licence")  # no licence named at all: unknown, never "no problem"
                    else:
                        found.append(catalog.warn("license_unknown"))
        elif self.weights == "pickle":
            found.append(catalog.warn("pickle_weights"))
        elif self.weights not in ("safetensors", "gguf"):
            unknown.append("weights_format")
        if unknown:
            found.append(catalog.facts_unknown(*unknown))
        if self.outside_images:
            found.append(catalog.warn("base_image_unofficial", registry=_registry_word(self.outside_images[0])))
            if any(not _pinned_image(i) for i in self.outside_images):
                found.append(catalog.warn("base_image_unpinned"))
        if self.target_trust == "local":
            found.append(catalog.warn("target_unreviewed"))
        if self.first_build:
            found.append(catalog.warn("first_build_unconfirmed"))
        return tuple(found)

    def numbers(self) -> Dict[str, Any]:
        """Closed values for texts and rules (the stable and volatile keys of facts_catalog.NUMBER_KEYS)."""
        numbers: Dict[str, Any] = {"size_class": catalog.size_class(self.size()), "host_fit": self.host.state,
                                   "target_fit": self.target.state}
        params = self.facts.parameters if self.facts is not None else None
        if isinstance(params, int) and not isinstance(params, bool):
            numbers["parameters"] = params
        if self.source == "hf":
            numbers.update(download_bytes=int(self.download), cached_bytes=int(self.cached))
        if self.disk:
            numbers["needed_bytes"] = sum(c.needed for c in self.disk)
            worst = min(self.disk, key=lambda c: c.after - c.reserve)
            numbers.update(free_after_bytes=int(worst.after), reserve_bytes=int(worst.reserve))
        for fit, prefix in ((self.host, "host"), (self.target, "target")):
            if fit.needed is not None:
                numbers[f"{prefix}_needed_bytes"] = int(fit.needed)
            if fit.usable is not None:
                numbers[f"{prefix}_usable_bytes"] = int(fit.usable)
        return numbers

    def names(self) -> Dict[str, str]:
        """Speaking names for the person's short summary."""
        names = {"model": self.model, "target": self.target_name or self.request.target}
        if self.request.board:
            names["board"] = self.request.board
        return names

    def call_facts(self) -> CallFacts:
        return CallFacts(consequence=self.consequence(), facts=self.question_facts(), bound=self.bound(),
                         warnings=self.warnings(), numbers=self.numbers(), scope={"size_bytes": self.size()},
                         digest_keys=self.DIGEST_KEYS, consequence_template=self.consequence_template(),
                         names=self.names())


def _registry_word(image: str) -> str:
    """The registry an image comes from, as a closed word for a warning's numbers (docker.io for Docker Hub)."""
    ref = image.split("@", 1)[0]
    first = ref.split("/", 1)[0]
    host = first if "/" in ref and ("." in first or ":" in first or first == "localhost") else "docker.io"
    word = re.sub(r"[^A-Za-z0-9_.+-]", "-", host).strip("-.+_")[:40]
    return word or "unknown"


def _pinned_image(image: str) -> bool:
    from orchestrator.Core.confirmed_builds import pinned
    return pinned(image)


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


def _target_image_pairs(target_folder: Optional[Path]) -> List[Tuple[str, str]]:
    """(file, image) for every image the target's Dockerfiles build from (FROM, COPY --from, RUN --mount from), as
    the pack question showed them; [] without a Dockerfile."""
    if target_folder is None:
        return []
    try:
        from orchestrator.tools.tools_community import pack_base_images
        return list(pack_base_images(Path(target_folder)))
    except Exception:  # noqa: BLE001 - images that cannot be read are no official images
        return [("Dockerfile", "?")]


def _official(image: str) -> bool:
    return registry_class(image) == "Docker Official Image" and "$" not in image and image != "?"


def _target_images(target_folder: Optional[Path], pairs: Optional[List[Tuple[str, str]]] = None) -> List[str]:
    """Every image the target's Dockerfiles build from, once each."""
    pairs = _target_image_pairs(target_folder) if pairs is None else pairs
    return list(dict.fromkeys(image for _, image in pairs))


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
    # every image outside the Docker Official Images is in plan.base_images, so in the question the person answered
    images = _target_images(target_folder, plan.base_images or None)
    record = {"target": plan.request.target, "installed_via": installed_via, "pack_sha256": pack_sha,
              "base_images": images}
    module = module_record(target_folder)
    if module:
        record["module"] = module
    if recipe:
        record["recipe"] = {"id": recipe.get("id"), "installed_via": recipe.get("installed_via") or "",
                            "pack_sha256": recipe.get("pack_sha256") or ""}
    return record


_COMPUTE = object()   # grant_facts: no grant_scope given, work it out


def grant_facts(plan: "BuildPlan", service, framework, target_folder: Optional[Path], pin, ledger,
                record: Optional[Dict[str, Any]] = None, scope: Any = _COMPUTE) -> Optional[dict]:
    """What a standing approval for start_build is matched against (grants.BUILD_RULE, design 6.10): target trust
    (shipped: no pack provenance, not AI-generated, support level official; verified: a verified pack), how the target
    (and a recipe) was installed and whether the person has since confirmed a build of it, the weights type, the
    base images (Docker Official Images, or a digest-pinned image the person confirmed in a build of this target
    before), the source with gating, base-model gating and licence, and the download size. None (the person is asked)
    when the plan has a problem or the disk does not keep the larger reserve of a standing approval (20 GiB or 10 %).
    `scope`: the grant_scope the caller has already worked out (computed here when it is not given)."""
    if scope is _COMPUTE:
        scope = grant_scope(plan, framework, target_folder, pin, record)
    return scope if scope is not None and _standing_fits(plan, service, ledger) else None


def _standing_fits(plan: "BuildPlan", service, ledger) -> bool:
    """Whether every disk keeps the larger reserve of a standing approval (20 GiB or 10 %) for this build."""
    if not plan.needs:
        return True
    standing = disk_plan.check_disk(plan.needs, ledger, standing=True, disk_usage=service.disk_usage)
    return all(c.fits for c in standing)


def module_record(target_folder: Optional[Path]) -> Optional[Dict[str, str]]:
    """What binds a module an AI created without the person answering the question itself (under a delegation): the
    channel it was created under and its definition's sha256 (target.yml metadata `created_under`,
    `definition_sha256`); None for every other target."""
    if target_folder is None:
        return None
    try:
        import yaml
        meta = yaml.safe_load((Path(target_folder) / "target.yml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - no readable target.yml: no module record
        return None
    info = meta.get("metadata") if isinstance(meta, dict) else None
    if not isinstance(info, dict) or not info.get("created_under"):
        return None
    return {"created_under": str(info.get("created_under"))[:80],
            "definition_sha256": str(info.get("definition_sha256") or "")[:64]}


def needs_first_confirmation(installed_via: Optional[str]) -> bool:
    """Whether build code reached this computer without the person seeing it ([Q1]): installed or created under a
    standing approval or a delegation."""
    from orchestrator.Core.confirmed_builds import unattended
    return unattended(installed_via)


def confirmation_state(framework, record: Dict[str, Any]) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """(the target, recipe or module an AI made or installed that the person has not confirmed a build of, bound to its
    pack sha256 or definition sha256, as (kind, id, channel); the base images outside the Docker Official Images the
    person never confirmed in a build of exactly this installed target with exactly that pinned image)."""
    from orchestrator.Core.confirmed_builds import ConfirmedBuilds
    store = None
    state = state_dir(framework)
    if state is not None:
        store = ConfirmedBuilds.for_framework(framework, state)
    pending: List[Tuple[str, str, str]] = []
    parts = [("target", record["target"], record.get("installed_via") or "", record.get("pack_sha256") or "")]
    if record.get("recipe"):
        r = record["recipe"]
        parts.append(("recipe", r["id"], r.get("installed_via") or "", r.get("pack_sha256") or ""))
    for kind, item_id, via, sha in parts:
        if needs_first_confirmation(via) and not (store is not None and store.confirmed(
                kind, item_id, installed_via=via, pack_sha256=sha)):
            pending.append((kind, str(item_id), via))
    module = record.get("module")
    if isinstance(module, dict) and needs_first_confirmation(module.get("created_under")):
        digest = str(module.get("definition_sha256") or "")
        # a module is only ever confirmed for its content: recreated under the same name, it is asked again
        if not (digest and store is not None and store.confirmed("module", record["target"],
                                                                  definition_sha256=digest)):
            pending.append(("module", str(record["target"]), str(module.get("created_under"))))
    images = [i for i in record.get("base_images") or [] if not _official(i) and not (
        store is not None and store.image_confirmed(record["target"], i,
                                                    installed_via=record.get("installed_via") or "",
                                                    pack_sha256=record.get("pack_sha256") or ""))]
    return pending, images


def grant_scope(plan: "BuildPlan", framework, target_folder: Optional[Path], pin,
                record: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """The facts of grant_facts without the disk check of a standing approval (None when the plan has a problem): the
    part of a standing approval's facts a decision digest may cover, since free space never belongs to it."""
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
    record = record or confirm_record(plan, framework, target_folder)
    # build code installed or made unattended (standing approval, delegation) counts only after the person confirmed
    # one build of exactly it ([Q1])
    pending, unconfirmed = confirmation_state(framework, record)
    images = record["base_images"]
    official = bool(images) and not unconfirmed
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
                                                 "Tell the person; they remove the target in the Community Hub.",
                                                 "target_revoked"))
                elif trust.value == "local":
                    plan.trust_note = "imported from a file that is not in the signed community index; nobody reviewed its scripts"
        except Exception:  # noqa: BLE001 - trust is shown when it can be read
            pass
    plan.image_build = ("a Docker image for the target is built first if none exists yet; the image build runs with "
                        "network access on this computer, outside the build sandbox (size unknown, first build only)")
    pairs = _target_image_pairs(target_folder)
    if plan.support_level != "official" or plan.target_trust != "shipped" or not all(_official(i) for _, i in pairs):
        plan.base_image = _base_image(target_folder)
        plan.base_image_class = registry_class(plan.base_image) if plan.base_image else ""
        # every image, not only the first FROM: a later stage can pull a vendor image too (DECISIONS round 3)
        plan.base_images = pairs

    # sandbox
    plan.sandbox = bool(_config(framework, "build_sandbox", True))
    if not plan.sandbox:
        plan.problems.append(Problem("needs_person", "The build sandbox is switched off (setting build_sandbox); a "
                                     "build an AI starts runs only in the sandbox.",
                                     "Ask the person to switch build_sandbox on (update_settings can set it to true "
                                     "with their confirmation, or they do it in the GUI or CLI).", "sandbox_off"))

    # the model
    if pin is not None:
        plan.local_bytes = _size_of(Path(pin.path))
        plan.mounts.append(pin.ref)
    else:
        plan.mounts.append(f"model cache: {req.model}" + "@commit")
        if bool(_config(framework, "offline_mode", False)):
            plan.problems.append(Problem("needs_person", "Offline mode is on, so the framework does not contact "
                                         "huggingface.co.", "Use a local model (list_local_files), or ask the person "
                                         "to switch offline mode off in the GUI or CLI.", "offline_mode"))
        elif look_up_hub:
            try:
                plan.facts = service.hub_facts(req.model, req.revision)
            except HubLookupError as e:
                if e.code == "failed":  # Hugging Face not reachable: the question says "commit unknown"
                    plan.hub_problem = str(e)
                    plan.commit, plan.download_known = req.revision, False
                else:  # gated without access, unknown repository: refused before the question
                    blocker = ("gated_no_access" if e.code in ("needs_person", "permission_denied") else
                               "unknown_repository" if e.code in ("invalid_value", "unknown_id") else "")
                    plan.problems.append(Problem(e.code, str(e), e.next, blocker))
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
                                                 "file with download_hf_file when it is offered.",
                                                 "no_convertible_weights"))
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
            plan.problems.append(Problem("insufficient_resources", str(e), e.next, "disk_full"))

    plan.confirm_record = confirm_record(plan, framework, target_folder, recipe)
    plan.weights = (plan.facts.weights if plan.facts is not None else "") if plan.source == "hf" else \
        _local_weights(Path(pin.path))
    pending, plan.unconfirmed_images = confirmation_state(framework, plan.confirm_record)
    plan.outside_images = [i for i in plan.confirm_record.get("base_images") or [] if not _official(i)]
    plan.first_build = [(kind, item) for kind, item, _via in pending]
    plan.grant_scope = grant_scope(plan, framework, target_folder, pin, plan.confirm_record)
    plan.grant = grant_facts(plan, service, framework, target_folder, pin, ledger, plan.confirm_record,
                             scope=plan.grant_scope)

    # hardware estimate (never a refusal)
    params, basis = disk_plan.parameters_from(size, plan.facts.parameters if plan.facts else None)
    hw = service.host_hardware()
    plan.host = disk_plan.host_fit(req.format, req.use_imatrix, params, getattr(hw, "ram_total", None),
                                   _best_vram(hw) if hw is not None else None, req.use_gpu, basis)
    board = caps.boards.get(req.board) if req.board else None
    output = int(size * disk_plan.output_share(req.quantization)) if size else 0
    plan.target = disk_plan.target_fit(output, None, req.board, getattr(board, "ram_max_gb", None))
    return plan
