#!/usr/bin/env python3
"""
The documentation links of the source catalogue (configs/project_sources.yml, section hardware_targets) that fit a
board (DECISIONS round 8, N10).

The catalogue keeps the vendors' SDK documentation as docs_* links. This module picks the links of the vendors a
hardware probe names (its SoC and NPU vendor and its accelerator switches: CUDA, Intel NPU, Hailo, ...) or that an SDK
name points to (RKNN, CUDA, OpenVINO, ...), so the Deep Ingest window can be filled with them, and AI clients can read
them through a read-only resource (lcf://knowledge/doc-sources). Nothing here fetches a link: the person checks the
links and starts the crawl, with the usual confirmation, robots.txt and the other opt-outs, and the limits.

Only https links are used, from the catalogue in this installation; a probe value never becomes a link.

The catalogue's sections models and tiny_models are the model suggestions (DECISIONS round 23 item 10): exact Hugging
Face repository ids with their size, recommended quantization, licence and gating, and the day they were checked.
model_suggestions() gives them in order (small models first, gated ones only on request), for the Builder's model
field (free text stays allowed), `llm-cli models suggestions` and the read-only resource lcf://models/suggestions;
model_problems() is the check scripts/validate_ssot.py runs on the section.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

CATALOGUE = Path(__file__).resolve().parents[2] / "configs" / "project_sources.yml"
MAX_LINKS = 12

# A word of an SDK or toolkit name -> the vendor section of the catalogue.
SDK_VENDORS: Tuple[Tuple[str, str], ...] = (
    ("rkllm", "rockchip"), ("rknn", "rockchip"), ("rockchip", "rockchip"), ("tensorrt", "nvidia"),
    ("jetson", "nvidia"), ("cuda", "nvidia"), ("nvidia", "nvidia"), ("openvino", "intel"), ("ipex", "intel"),
    ("intel", "intel"), ("rocm", "amd"), ("hip", "amd"), ("amd", "amd"), ("hailo", "hailo"), ("memryx", "memryx"),
    ("axelera", "axelera"), ("metis", "axelera"), ("riscv", "riscv"), ("risc-v", "riscv"))
# Projects of a vendor that come first for a probe value (the LLM toolkit before the vision toolkit, ...).
FIRST_PROJECTS = {"rockchip": ("rkllm_toolkit", "rknn_toolkit2"), "nvidia": ("tensorrt_llm",),
                  "intel": ("openvino", "optimum_intel")}


@dataclass(frozen=True)
class DocLink:
    vendor: str
    project: str
    key: str         # docs_workflow, docs_api, ...
    url: str

    def label(self) -> str:
        what = self.key[len("docs_"):].replace("_", " ") if self.key.startswith("docs_") else self.key
        return f"{self.vendor} / {self.project.replace('_', ' ')}: {what}"


def _links_of(vendor: str, project: str, entry: Dict[str, Any]) -> List[DocLink]:
    keys = sorted((k for k in entry if isinstance(k, str) and k.startswith("docs_")),
                  key=lambda k: (k != "docs_workflow", k))
    found = []
    for key in keys:
        url = entry.get(key)
        if isinstance(url, str) and re.fullmatch(r"https://[^\s]{4,500}", url):
            found.append(DocLink(vendor, project, key, url))
    return found


@lru_cache(maxsize=4)
def load_catalogue(path: Path = CATALOGUE) -> Dict[str, Tuple[DocLink, ...]]:
    """vendor -> its documentation links, in the catalogue's order (each project's docs_workflow first). An unreadable
    catalogue gives no links (the Deep Ingest window then stays empty, as before)."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    targets = data.get("hardware_targets") if isinstance(data, dict) else None
    out: Dict[str, Tuple[DocLink, ...]] = {}
    for vendor, section in (targets or {}).items():
        if not isinstance(section, dict):
            continue
        links: List[DocLink] = _links_of(str(vendor), str(vendor), section)   # a flat entry (memryx, axelera)
        for project, entry in section.items():
            if isinstance(entry, dict):
                links += _links_of(str(vendor), str(project), entry)
        if links:
            out[str(vendor)] = tuple(links)
    return out


def vendors_for_sdk(text: str) -> List[str]:
    """The catalogue vendors an SDK or toolkit name points to ('RKLLM / RKNN' -> rockchip)."""
    low = str(text or "").lower()
    # a word at the start of a word only: 'hip' is AMD's, never the end of 'rockchip'
    return list(dict.fromkeys(vendor for word, vendor in SDK_VENDORS
                              if re.search(r"(?<![a-z0-9])" + re.escape(word), low)))


def vendors_for_probe(profile: Any) -> List[str]:
    """The catalogue vendors a hardware probe names, from validated probe values only."""
    get = getattr(profile, "get", None)
    if not callable(get):
        return []
    found: List[str] = []
    for key in ("NPU_VENDOR", "SOC_VENDOR"):
        value = str(get(key) or "").lower()
        if value:
            found += vendors_for_sdk(value) or ([value] if re.fullmatch(r"[a-z0-9-]{1,32}", value) else [])
    switches = (("SUPPORTS_RKNN", "rockchip"), ("SUPPORTS_CUDA", "nvidia"), ("SUPPORTS_INTEL_NPU", "intel"),
                ("SUPPORTS_INTEL_XPU", "intel"), ("SUPPORTS_HAILO", "hailo"), ("SUPPORTS_METIS", "axelera"))
    for key, vendor in switches:
        if str(get(key) or "").upper() == "ON":
            found.append(vendor)
    if get("ROCM_VERSION") or str(get("HIP_RUNTIME") or "").upper() == "ON":
        found.append("amd")
    if str(get("ARCH") or "") == "riscv64":
        found.append("riscv")
    return list(dict.fromkeys(found))


def _first_projects(vendor: str, profile: Any) -> Sequence[str]:
    first = list(FIRST_PROJECTS.get(vendor, ()))
    get = getattr(profile, "get", None)
    if vendor == "rockchip" and callable(get) and not get("RKLLM_RUNTIME_VERSION") and \
            str(get("SUPPORTS_RKNN") or "").upper() == "ON":
        first.reverse()   # RKNN runtime only: the vision toolkit first
    return first


def links_for(profile: Any = None, sdk: str = "", *, limit: int = MAX_LINKS,
              catalogue: Optional[Dict[str, Tuple[DocLink, ...]]] = None) -> List[DocLink]:
    """The documentation links for a probe and/or an SDK name, the best first, at most `limit`."""
    catalogue = load_catalogue() if catalogue is None else catalogue
    vendors = list(dict.fromkeys(vendors_for_probe(profile) + vendors_for_sdk(sdk)))
    chosen: List[DocLink] = []
    for vendor in vendors:
        links = list(catalogue.get(vendor, ()))
        order = {p: i for i, p in enumerate(_first_projects(vendor, profile))}
        links.sort(key=lambda link: order.get(link.project, len(order)))
        chosen += links
    unique = list({link.url: link for link in chosen}.values())
    return unique[:max(0, int(limit))]


def catalogue_text(links: Iterable[DocLink], heading: str) -> str:
    """The links as plain text for a resource (framework text: vendor, project and the catalogue's addresses)."""
    lines = [heading, ""]
    for link in links:
        lines.append(f"- {link.label()}: {link.url}")
    if len(lines) == 2:
        lines.append("(no documentation links for this hardware in the source catalogue)")
    lines += ["", "To add one to the knowledge base, use crawl_docs_site (the person confirms every crawl; robots.txt, "
              "the other opt-outs and licences are respected)."]
    return "\n".join(lines)


# --- the model suggestions (sections tiny_models and models) ------------------------------------------------------

MODEL_SECTIONS = ("tiny_models", "models")   # in this order: small models first
_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_QUANT = re.compile(r"^[A-Z0-9][A-Z0-9_]{1,15}$")
_CHECKED = re.compile(r"^(\d{4}-\d{2}-\d{2}|unchecked)$")


@dataclass(frozen=True)
class ModelSuggestion:
    key: str
    repo_id: str
    size_mb: int
    recommended_quant: str
    license: str
    gated: bool
    checked: str      # YYYY-MM-DD the Hub API was asked, or "unchecked"
    note: str
    section: str      # tiny_models or models

    def label(self) -> str:
        gated = "; gated: needs a Hugging Face token" if self.gated else ""
        checked = "not checked on the Hub" if self.checked == "unchecked" else f"checked {self.checked}"
        return (f"{self.repo_id} ({self.size_mb} MB, {self.recommended_quant}, {self.license or 'licence unknown'}"
                f"{gated}; {checked})")


def model_problems(data: Any) -> List[str]:
    """What is wrong with the sections models and tiny_models of the catalogue's data (empty: valid): every entry needs
    an exact repo_id (owner/name), a positive size_mb, a GGUF type as recommended_quant, gated true or false, checked
    as a date or "unchecked"; a repository id appears once."""
    problems: List[str] = []
    seen: Dict[str, str] = {}
    sections = data if isinstance(data, dict) else {}
    for section in MODEL_SECTIONS:
        entries = sections.get(section)
        if entries is None:
            problems.append(f"{section}: missing")
            continue
        if not isinstance(entries, dict):
            problems.append(f"{section}: not a mapping")
            continue
        for key, entry in entries.items():
            where = f"{section}.{key}"
            if not isinstance(entry, dict):
                problems.append(f"{where}: not a mapping")
                continue
            repo = entry.get("repo_id")
            if not isinstance(repo, str) or not _REPO_ID.fullmatch(repo):
                problems.append(f"{where}: repo_id must be an exact Hugging Face id (owner/name)")
            elif repo.casefold() in seen:
                problems.append(f"{where}: {repo} is listed twice (also {seen[repo.casefold()]})")
            else:
                seen[repo.casefold()] = where
            size = entry.get("size_mb")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                problems.append(f"{where}: size_mb must be a positive number of MB")
            if not isinstance(entry.get("recommended_quant"), str) or not _QUANT.fullmatch(entry["recommended_quant"]):
                problems.append(f"{where}: recommended_quant must be a GGUF type such as Q4_K_M")
            if not isinstance(entry.get("gated"), bool):
                problems.append(f"{where}: gated must be true or false")
            if not isinstance(entry.get("checked"), str) or not _CHECKED.fullmatch(entry["checked"]):
                problems.append(f"{where}: checked must be the date of the Hub check (YYYY-MM-DD) or unchecked")
    return problems


def _models_of(data: Any) -> List[ModelSuggestion]:
    out: List[ModelSuggestion] = []
    for section in MODEL_SECTIONS:
        entries = (data or {}).get(section) if isinstance(data, dict) else None
        for key, entry in (entries or {}).items() if isinstance(entries, dict) else ():
            if not isinstance(entry, dict):
                continue
            repo, size = entry.get("repo_id"), entry.get("size_mb")
            if not isinstance(repo, str) or not _REPO_ID.fullmatch(repo) or isinstance(size, bool) or \
                    not isinstance(size, int) or size <= 0:
                continue   # validate_ssot names it; a broken entry is never suggested
            quant = str(entry.get("recommended_quant") or "Q4_K_M")
            out.append(ModelSuggestion(
                key=str(key), repo_id=repo, size_mb=size,
                recommended_quant=quant if _QUANT.fullmatch(quant) else "Q4_K_M",
                license=" ".join(str(entry.get("license") or "").split())[:40],
                gated=entry.get("gated") is not False, checked=str(entry.get("checked") or "unchecked")[:10],
                note=" ".join(str(entry.get("note") or "").split())[:200], section=section))
    return out


@lru_cache(maxsize=4)
def load_models(path: Path = CATALOGUE) -> Tuple[ModelSuggestion, ...]:
    """Every valid entry of the sections tiny_models and models, in the catalogue's order (small models first). An
    unreadable catalogue gives none (the Builder then offers the person's recent models only)."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    return tuple(_models_of(data))


def model_suggestions(include_gated: bool = False, path: Path = CATALOGUE) -> List[ModelSuggestion]:
    """The model suggestions: ungated ones only unless `include_gated` (an entry without gated: false counts as
    gated, so nothing that needs a token is a suggestion by default)."""
    return [m for m in load_models(path) if include_gated or not m.gated]


def suggestions_text(models: Iterable[ModelSuggestion], heading: str) -> str:
    lines = [heading, ""] + [f"- {m.label()}: {m.note}" if m.note else f"- {m.label()}" for m in models]
    if len(lines) == 2:
        lines.append("(no model suggestions in the source catalogue)")
    lines += ["", "Any other Hugging Face id or a local model works as well; check_build_request shows its size and "
              "licence before a build."]
    return "\n".join(lines)


__all__ = ["CATALOGUE", "DocLink", "MAX_LINKS", "MODEL_SECTIONS", "ModelSuggestion", "catalogue_text", "links_for",
           "load_catalogue", "load_models", "model_problems", "model_suggestions", "suggestions_text",
           "vendors_for_probe", "vendors_for_sdk"]
