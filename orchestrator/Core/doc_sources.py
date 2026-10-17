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


__all__ = ["CATALOGUE", "DocLink", "MAX_LINKS", "catalogue_text", "links_for", "load_catalogue", "vendors_for_probe",
           "vendors_for_sdk"]
