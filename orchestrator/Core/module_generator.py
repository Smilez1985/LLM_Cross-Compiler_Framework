#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Module Generator (Core Logic)
DIREKTIVE: Goldstandard, Separation of Concerns.

Zweck:
Zentrale Logik zur Generierung neuer Hardware-Target-Module.
Wird sowohl von der GUI (Wizard) als auch der CLI (Command) genutzt.
Enthält Templates für Dockerfile, target.yml und build.sh.

The build of a generated target is the template's four stage modules (source, config, convert, target), which
modules/build.sh, the stage runner, runs in this order (DECISIONS U9); the generator fills in the wizard's setup
commands (source), quantization logic (convert) and packaging commands (target).
The generated target.yml carries a capabilities block (support level community) and no template placeholder.
A knowledge snapshot is attached only on request and holds only the pages crawled for this target, as the licences
allow (full text only where a page's licence allows passing it on, otherwise its address and short facts), with the
licence texts and the NOTICE its pages need next to it (knowledge/LICENSES/, knowledge/NOTICE; rag/pack_licences.py).
validate_target_folder checks a target folder from anywhere (generated, exported or a community pack).
"""

import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Any, Optional
import yaml

from orchestrator.utils.logging import get_logger
from orchestrator.utils.helpers import ensure_directory
from orchestrator.utils.prebuilt_llama import is_prebuilt_llama_image, scan_target_folder

# --- Validation of module data (wizard input, AI output, community input) ---------------------------
# Every value below ends up in Dockerfiles, YAML or shell scripts, so it is checked before any
# template is rendered. Shell code fields stay free-form (they are reviewed by the user) but must
# pass a syntax check and a fixed deny-list that no setting can override.

ARCHITECTURES = ("aarch64", "x86_64", "armv7l", "riscv64")
MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
SDK_RE = re.compile(r"^[\w .+/-]{0,64}$")
DEB_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+(?::[a-z0-9-]+)?(?:=[A-Za-z0-9.+~:-]+)?$")
FLAG_RE = re.compile(r"^-[A-Za-z][\w.=+,:/-]*$")
# A CMake option on a command line or in a module's CMake flags: -DNAME=VALUE or -DNAME:TYPE=VALUE
CMAKE_OPTION_RE = re.compile(r"(?<![\w-])-D([A-Za-z_][A-Za-z0-9_]*)(?::[A-Za-z]+)?=([^\s'\";|&)]*)")
# An image reference: an optional registry with a port (registry.example:5000/...; any registry is allowed, DECISIONS
# round 3), the repository, an optional tag and an optional digest.
IMAGE_RE = re.compile(r"^(?:[a-z0-9][a-z0-9.-]*(?::\d{1,5})?/)?[a-z0-9][a-z0-9._/-]*(?::[\w][\w.-]{0,127})?"
                      r"(?:@sha256:[a-f0-9]{64})?$")
# Images that may be used without a digest pin; anything else must be pinned by digest. NVIDIA's prebuilt CUDA images
# are an explicit opt-in pinned by digest (decision D6): the GPU template provisions CUDA from the pinned redist
# archives instead.
TRUSTED_IMAGE_PREFIXES = ("debian:", "ubuntu:", "rocm/dev-ubuntu-", "rocm/rocm-terminal:", "python:")
# The prebuilt CUDA base the GPU template accepts as opt-in: a development image on the Ubuntu release of its runtime
# stage (the Python environment is copied from one stage to the other), pinned by digest.
CUDA_OPT_IN_RE = re.compile(r"^nvidia/cuda:[\w.-]+-devel-ubuntu22\.04@sha256:[a-f0-9]{64}$")
DETECTION_RE = re.compile(r"^[\w ./=-]{0,200}$")
SHELL_DENY_PATTERNS = [
    # Generated build code must not fetch anything: downloads belong in a reviewed Dockerfile, and build
    # containers will run without network. This also covers every download-and-execute variant.
    (re.compile(r"\b(?:curl|wget|aria2c|fetch)\b"), "downloads at build time"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(?:--no-preserve-root\s+)?/(?:\s|$|\*)"), "deletes the root filesystem"),
    (re.compile(r"\bchmod\s+(?:-R\s+)?0?777\b"), "makes files world-writable"),
    (re.compile(r"\bsudo\b"), "uses sudo"),
    (re.compile(r"\b(?:mkfs(?:\.\w+)?|dd\s+if=)"), "writes raw devices"),
    (re.compile(r"/var/run/docker\.sock|\bdocker\s+run\b"), "controls the Docker host"),
    (re.compile(r"(?:^|[;&|]\s*)\s*(?:eval|source|\.)\s+[\"']?\$\(", re.MULTILINE), "evaluates generated code"),
    # Process substitution runs code, except as plain input ('done < <(grep ...)'), unless a shell reads it.
    (re.compile(r"\b(?:ba|z|da)?sh\s+-c\s+[\"']?\$\(|(?<!<\s)(?<!<)<\(\s*\S|\b(?:ba|z|da)?sh\s*<\s*<\("),
     "runs generated code"),
]
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SHIPPED_TEMPLATE = Path(__file__).resolve().parents[2] / "targets" / "_template"
# Template placeholders that must never survive into a generated module (target.yml is also checked
# for any bracketed placeholder text, see _leftover_placeholders).
TEMPLATE_PLACEHOLDERS = re.compile(  # not after a name: COMPILER_CONFIG[CMAKE_FLAGS] is a bash array entry
    r"(?<![\w$])\[(?:MODULE_NAME|Hardware-Familie|IHRE_ARCHITEKTUR|Hersteller|QUANTIZATION_LOGIC|CPU_FLAGS|CMAKE_FLAGS"
    r"|SDK_SETUP_COMMANDS|PACKAGING_COMMANDS|ihr-target-name|VENDOR_SOURCES|PIP_PACKAGES|PINNED_DOWNLOADS"
    r"|PINNED_DOWNLOADS_COPY)\]|\{packages_str\}|YYYY-MM-DD")
GGUF_DEFAULT_QUANTS = ["Q4_K_M", "Q4_0", "Q5_K_M", "Q6_K", "Q8_0", "F16"]
# The stages of a target's build, in the order modules/build.sh (the stage runner) runs them (DECISIONS U9).
STAGE_MODULES = ("source", "config", "convert", "target")
STAGE_FALLBACK = ('#!/bin/bash\n# {stage}_module.sh - the {stage} stage (no template for it was found when this target '
                  'was generated)\necho "ERROR: the {stage} stage of this target has no script yet: write '
                  'modules/{stage}_module.sh (see targets/_template)." >&2\nexit 1\n')
KNOWLEDGE_SNAPSHOT = "knowledge/initial_knowledge.json"  # crawled pages: data, not template output
# The licence texts and the NOTICE next to the snapshot (rag/pack_licences.py): data from the web and from SPDX too.
SNAPSHOT_LICENCE_FILES = ("knowledge/NOTICE", "knowledge/LICENSES/")


def crawled_data(rel: str) -> bool:
    """A file of a target folder that holds crawled pages or their licence files (data, not template output)."""
    return rel == KNOWLEDGE_SNAPSHOT or rel == SNAPSHOT_LICENCE_FILES[0] or rel.startswith(SNAPSHOT_LICENCE_FILES[1])
KNOWLEDGE_README = """# Knowledge snapshot of {slug}

`initial_knowledge.json` holds the pages that Deep Ingest crawled for this target in the module wizard, after the
privacy scrub (home folders, addresses, host and user names and keys removed). Every entry keeps its source address
(`source`) and its metadata, among them the start URL of its crawl (`root_url`), the licence and copyright notice the
page shows, and how it is shared (`shared_as`, `share_reason`): a page whose licence allows passing the text on keeps
its text in sections, with the attribution its licence requires; every other page (a copyright notice and no such
licence, code without a licence, an unknown licence) keeps only its address and short facts. The pages keep their
owners' terms, and the target's licence does not cover them.

When pages are kept in full under a licence that asks for its text or permission notice (MIT, BSD, ISC, Apache-2.0,
MPL-2.0, CC BY, CC BY-SA), `LICENSES/` holds the canonical text of each such licence, unchanged from the SPDX License
List, and `NOTICE` lists every page with its source address, its copyright line as found and its licence (for
Apache-2.0 also the notices of the work's NOTICE file, when the crawl read it). An export of the target makes both
anew from the snapshot.

The framework does not read this file: nothing imports it into a knowledge base, and there is no command for
that. To share knowledge so that it reaches other users' knowledge bases, export your knowledge base as a
knowledge pack (`llm-cli zoo export knowledge --author <name>`); an installed knowledge pack is added to the
knowledge base.
"""


def download_flag_problems(text: str) -> List[str]:
    """Why the CMake options in `text` (a module's CMake flags, or a line of its scripts) would let llama.cpp download
    something, or build a server with subprocesses: the build recipe's one rule for every option
    (orchestrator/runtime/build/recipe.py refused_option). Builds have no network, and the board package's cross build
    (targets/*/modules/target_module.sh) takes a module's CMake flags as they are; empty when nothing is refused."""
    from orchestrator.runtime.build.recipe import refused_option
    problems = []
    for match in CMAKE_OPTION_RE.finditer(text or ""):
        problem = refused_option(match.group(1), match.group(2))
        if problem:
            problems.append(f"{match.group(0)} would let llama.cpp download while it is built ({problem}); builds have "
                            "no network: leave it out")
    return problems


def module_slug(module_name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]", "_", module_name.strip().lower().replace(" ", "_")).strip("_-")
    return slug


def canonical_target_name(name: str) -> str:
    """A target name compared the way a person reads it: lower case, separators (space, '_', '-', '.') ignored."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def target_name_collision(targets_dir: Path, *names: str) -> Optional[str]:
    """The existing target id or folder name that a new target's names would equal ignoring case and separators
    (None when there is none): target names resolve to the first case-insensitive match (G43), so a new target
    must never capture the name of another one."""
    wanted = {canonical_target_name(n) for n in names if canonical_target_name(n)}
    root = Path(targets_dir)
    if not wanted or not root.is_dir():
        return None
    from orchestrator.Core.target_manager import read_target_folders
    existing = [p.name for p in root.iterdir() if not p.name.startswith(".")]
    existing += list(read_target_folders(root))
    return next((name for name in existing if canonical_target_name(name) in wanted), None)


# --- vendor toolchains of a module (MCP design 2.6, DECISIONS round 3 (d) and round 4 item 4) ------------------------
# A module adds software as apt packages, vendor apt sources with a signing key, pinned pip packages and pinned
# downloads. The framework's Dockerfile template renders them during the image build (which runs with network); the
# AI's shell fields may still not download. Security checks are hard (https, pinned versions, sha256 and size, key
# fingerprints); the counts are soft caps: going above one is shown as a warning in the question and needs the
# person's approval, never a refusal. No count is refused here; over MCP the question's own length (16,000
# characters, every entry shown) is the only bound.
SOFT_CAPS = {"package_sources": 3, "pip_packages": 30, "pinned_downloads": 5}
# Sanity bounds of the input models (MCP tool, Ditto's ModuleDefinition), far above what one question can show, so
# they never decide before the question's length does.
INPUT_LIST_BOUNDS = {"package_sources": 200, "pip_packages": 2000, "pinned_downloads": 200}
HTTPS_URL_RE = re.compile(r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?"
                          r"(?:/[A-Za-z0-9._~%/+=-]*)?$")
APT_SUITE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$")
APT_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
FINGERPRINT_RE = re.compile(r"^[A-F0-9]{40}$")
PIP_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?)==([A-Za-z0-9][A-Za-z0-9.+!_-]{0,63})$")
DOWNLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_DOWNLOAD_BYTES = 1 << 50  # a sanity bound for the declared size, not a cap
_LLAMA_PACKAGE = re.compile(r"llama[._-]?cpp|^ggml", re.IGNORECASE)
VENDOR_DIR = "/opt/vendor"
TEXT_FIELD_LIMITS = {"assumptions": 300, "base_image_reason": 1000}


def _https(url: Any) -> bool:
    return isinstance(url, str) and bool(HTTPS_URL_RE.fullmatch(url)) and ".." not in url


def soft_cap_warnings(data: Dict[str, Any]) -> List[str]:
    """One plain warning per soft cap the module goes above (empty: none)."""
    out = []
    for key, cap in SOFT_CAPS.items():
        n = len(data.get(key) or [])
        if n > cap:
            out.append(f"{key}: {n} entries, more than the usual {cap}. Every entry adds software from outside the "
                       "framework to the image build; approving overrides this soft limit.")
    return out


def vendor_fact_lines(data: Dict[str, Any]) -> List[str]:
    """What a person must see about a module's vendor software before it is created: every address, fingerprint, pin
    and hash (the values are the AI's; callers show them as AI text), then a warning per soft cap gone above."""
    lines = []
    for s in data.get("package_sources") or []:
        s = s.model_dump() if hasattr(s, "model_dump") else dict(s)
        components = s.get("components") or []
        lines.append(f"Vendor apt source: {s.get('url')} {s.get('suite')} "
                     f"{' '.join(components) if isinstance(components, list) else components}; signing key "
                     f"{s.get('signed_by_url')}, fingerprint {s.get('signed_by_fingerprint')}")
    pips = data.get("pip_packages") or []
    if pips:
        lines.append(f"pip packages from {data.get('pip_index_url') or 'PyPI'}: {', '.join(map(str, pips))}")
    for d in data.get("pinned_downloads") or []:
        d = d.model_dump() if hasattr(d, "model_dump") else dict(d)
        lines.append(f"Download into {VENDOR_DIR}/{d.get('name')}: {d.get('url')}, {d.get('size_bytes')} bytes, "
                     f"sha256 {d.get('sha256')}")
    return lines + [f"WARNING: {w}" for w in soft_cap_warnings(data)]


def _validate_vendor_fields(data: Dict[str, Any], out: Dict[str, Any], problems: List[str]) -> None:
    from orchestrator.utils.prebuilt_llama import download_finding
    sources = []
    for i, entry in enumerate(data.get("package_sources") or []):
        entry = dict(entry) if isinstance(entry, dict) else {}
        fingerprint = re.sub(r"\s", "", str(entry.get("signed_by_fingerprint", ""))).upper()
        components = entry.get("components") or []
        components = components.split() if isinstance(components, str) else [str(c).strip() for c in components]
        where = f"package_sources[{i}]"
        if not _https(entry.get("url")):
            problems.append(f"{where}.url: an https address of an apt repository")
        if not APT_SUITE_RE.fullmatch(str(entry.get("suite", ""))):
            problems.append(f"{where}.suite: a plain suite name such as bookworm or jammy")
        if not components or len(components) > 10 or not all(APT_COMPONENT_RE.fullmatch(c) for c in components):
            problems.append(f"{where}.components: 1 to 10 plain component names such as main")
        if not _https(entry.get("signed_by_url")):
            problems.append(f"{where}.signed_by_url: the https address of the repository's signing key")
        if not FINGERPRINT_RE.fullmatch(fingerprint):
            problems.append(f"{where}.signed_by_fingerprint: the key's full fingerprint, 40 hex characters")
        sources.append({"url": entry.get("url"), "suite": entry.get("suite"), "components": components,
                        "signed_by_url": entry.get("signed_by_url"), "signed_by_fingerprint": fingerprint})
    out["package_sources"] = sources

    pips = [str(p).strip() for p in (data.get("pip_packages") or []) if str(p).strip()]
    for p in pips:
        m = PIP_REQUIREMENT_RE.fullmatch(p)
        if not m:
            problems.append(f"pip_packages: '{p[:80]}' must be pinned exactly as name==version")
        elif _LLAMA_PACKAGE.search(m.group(1)):
            problems.append(f"pip_packages: '{m.group(1)}' is llama.cpp; targets build llama.cpp from source with the "
                            "shared recipe")
    out["pip_packages"] = pips
    index = str(data.get("pip_index_url") or "").strip()
    if index and not _https(index):
        problems.append("pip_index_url: one https address of a Python package index")
    elif index and download_finding(index, ""):
        problems.append(f"pip_index_url: {download_finding(index, '')}")
    out["pip_index_url"] = index

    downloads, names = [], set()
    for i, entry in enumerate(data.get("pinned_downloads") or []):
        entry = dict(entry) if isinstance(entry, dict) else {}
        where = f"pinned_downloads[{i}]"
        url, name = entry.get("url"), str(entry.get("name", "")).strip()
        sha = str(entry.get("sha256", "")).strip().lower()
        size = entry.get("size_bytes")
        if not _https(url):
            problems.append(f"{where}.url: an https address")
        if not DOWNLOAD_NAME_RE.fullmatch(name) or name in names:
            problems.append(f"{where}.name: a unique plain file name (letters, digits, '.', '_', '-')")
        if not SHA256_RE.fullmatch(sha):
            problems.append(f"{where}.sha256: the file's sha256, 64 hex characters")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size < MAX_DOWNLOAD_BYTES:
            problems.append(f"{where}.size_bytes: the file's exact size in bytes")
        finding = download_finding(str(url or ""), name)
        if finding:
            problems.append(f"{where}: {finding}")
        names.add(name)
        downloads.append({"url": url, "name": name, "sha256": sha, "size_bytes": size})
    out["pinned_downloads"] = downloads

    assumptions = data.get("assumptions") or []
    assumptions = [assumptions] if isinstance(assumptions, str) else list(assumptions)
    cleaned = [" ".join(str(a).split()) for a in assumptions if str(a).strip()]
    if len(cleaned) > 10:
        problems.append("assumptions: at most 10 entries")
    if any(len(a) > TEXT_FIELD_LIMITS["assumptions"] or _CONTROL_CHARS.search(a) for a in cleaned):
        problems.append("assumptions: each at most 300 printable characters")
    out["assumptions"] = cleaned
    reason = " ".join(str(data.get("base_image_reason") or "").split())
    if len(reason) > TEXT_FIELD_LIMITS["base_image_reason"]:
        problems.append("base_image_reason: at most 1000 characters")
    out["base_image_reason"] = reason
    provenance = data.get("provenance")
    if provenance not in (None, "", "ai-generated"):
        problems.append("provenance: only 'ai-generated' (set by the framework)")
    out["provenance"] = provenance or None


def _q(value: str) -> str:
    """A validated value in single quotes (the regexes above already keep quotes and shell characters out)."""
    if "'" in value:
        raise ValueError("a quoted value contains a quote")
    return f"'{value}'"


def vendor_blocks(data: Dict[str, Any]) -> Dict[str, str]:
    """The Dockerfile blocks for a validated module's vendor sources, pip packages and downloads ("" for none)."""
    blocks = {"# [VENDOR_SOURCES]": "", "# [PIP_PACKAGES]": "", "# [PINNED_DOWNLOADS]": "",
              "# [PINNED_DOWNLOADS_COPY]": ""}
    sources = data.get("package_sources") or []
    if sources:
        lines = ["RUN set -eu; \\",
                 "    apt-get update; \\",
                 "    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \\",
                 "    install -d -m 0755 /etc/apt/keyrings; \\",
                 '    GNUPGHOME="$(mktemp -d)"; export GNUPGHOME; \\']
        for n, s in enumerate(sources):
            fpr, keyring = s["signed_by_fingerprint"], f"/etc/apt/keyrings/lcf-vendor-{n}.gpg"
            lines += [f"    curl -fsSL --proto '=https' --tlsv1.2 {_q(s['signed_by_url'])} -o /tmp/lcf-vendor-{n}.key; \\",
                      f"    gpg --batch --quiet --import /tmp/lcf-vendor-{n}.key; \\",
                      f"    gpg --batch --with-colons --fingerprint {_q(fpr)} | grep -q '^fpr:::::::::{fpr}:' \\",
                      f"        || {{ echo 'package source {n}: the signing key is not {fpr}' >&2; exit 1; }}; \\",
                      f"    gpg --batch --export {_q(fpr)} > {keyring}; \\",
                      f"    test -s {keyring}; \\",
                      f"    echo {_q('deb [signed-by=' + keyring + '] ' + s['url'] + ' ' + s['suite'] + ' ' + ' '.join(s['components']))}"
                      f" > /etc/apt/sources.list.d/lcf-vendor-{n}.list; \\",
                      f"    rm -f /tmp/lcf-vendor-{n}.key; \\"]
        lines += ['    rm -rf "$GNUPGHOME" /var/lib/apt/lists/*']
        blocks["# [VENDOR_SOURCES]"] = "\n".join(lines)
    pips = data.get("pip_packages") or []
    if pips:
        index = data.get("pip_index_url") or ""
        args = (f"--index-url {_q(index)} " if index else "") + " ".join(_q(p) for p in pips)
        blocks["# [PIP_PACKAGES]"] = f"RUN python3 -m pip install --no-cache-dir {args}"
    downloads = data.get("pinned_downloads") or []
    if downloads:
        lines = ["RUN set -eu; \\", f"    mkdir -p {VENDOR_DIR}; \\"]
        for d in downloads:
            dest = f"{VENDOR_DIR}/{d['name']}"
            lines += [f"    curl -fsSL --proto '=https' --tlsv1.2 -o {_q(dest + '.part')} {_q(d['url'])}; \\",
                      f"    test \"$(wc -c < {_q(dest + '.part')})\" -eq {int(d['size_bytes'])} \\",
                      f"        || {{ echo '{d['name']}: not the size the module declares' >&2; exit 1; }}; \\",
                      f"    echo {_q(d['sha256'] + '  ' + dest + '.part')} | sha256sum -c -; \\",
                      f"    mv {_q(dest + '.part')} {_q(dest)}; \\"]
        lines[-1] = lines[-1][:-3]  # the last command ends the RUN step
        blocks["# [PINNED_DOWNLOADS]"] = "\n".join(lines)
        blocks["# [PINNED_DOWNLOADS_COPY]"] = f"COPY --from=builder {VENDOR_DIR} {VENDOR_DIR}"
    return blocks


AI_DEFINITION_FIELDS = ("architecture", "sdk", "base_os", "packages", "cpu_flags", "cmake_flags", "quantization_logic",
                        "setup_commands", "package_sources", "pip_packages", "pip_index_url", "pinned_downloads",
                        "assumptions", "base_image_reason")


def generator_input(definition: Dict[str, Any], module_name: Optional[str] = None,
                    boards: Optional[List[str]] = None, description: str = "") -> Dict[str, Any]:
    """The module generator's input for an AI-proposed module definition, the one mapping for every path (Ditto's
    `llm-cli module generate-ai`, the module wizard's AI proposal and the MCP tool create_target_module): every field
    of the definition is passed on (none is dropped), the module is marked ai-generated and untested, and its
    description says so with the values the AI guessed. `description`: the person's own words for the module (the
    module wizard's Description field), kept in front of that note; never an AI's."""
    from orchestrator.Core.ditto_manager import ai_module_description
    name = module_name or str(definition.get("module_name") or "") or "Custom_Target"
    data = {key: definition[key] for key in AI_DEFINITION_FIELDS if definition.get(key) is not None}
    packages = data.get("packages", [])
    data.update({"module_name": name, "packages": packages.split() if isinstance(packages, str) else packages,
                 "description": ai_module_description(name, definition.get("assumptions") or [], lead=description),
                 "detection_commands": "lscpu", "supported_boards": list(boards or [name]),
                 "provenance": "ai-generated"})
    return data


def validate_module_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a normalized copy of the module data or raises ValueError listing every problem."""
    problems: List[str] = []
    out = dict(data)

    name = str(data.get("module_name", "")).strip()
    if not MODULE_NAME_RE.fullmatch(name):
        problems.append("module_name: letters, digits, space, '_', '.', '-' only (max 64), starting with a letter or digit")
    elif not module_slug(name) or module_slug(name).startswith("_"):
        problems.append("module_name: does not give a usable folder name")
    out["module_name"] = name

    arch = str(data.get("architecture", "aarch64")).strip()
    if arch not in ARCHITECTURES:
        problems.append(f"architecture: one of {', '.join(ARCHITECTURES)}")
    out["architecture"] = arch

    sdk = str(data.get("sdk", "none") or "none").strip()
    if not SDK_RE.fullmatch(sdk):
        problems.append("sdk: short plain name expected")
    out["sdk"] = sdk

    base_os = str(data.get("base_os", "debian:bookworm-slim") or "debian:bookworm-slim").strip()
    if not IMAGE_RE.fullmatch(base_os):
        problems.append("base_os: not a valid image reference")
    elif "@sha256:" not in base_os and not base_os.startswith(TRUSTED_IMAGE_PREFIXES):
        problems.append(f"base_os: '{base_os}' must be pinned by digest (image@sha256:...) unless it is an official base image")
    elif base_os.startswith("nvidia/cuda:") and not CUDA_OPT_IN_RE.fullmatch(base_os):
        problems.append("base_os: a prebuilt NVIDIA image is an opt-in for the GPU template and must be an "
                        "nvidia/cuda:<version>-devel-ubuntu22.04 image pinned by digest; without it CUDA comes from "
                        "NVIDIA's pinned redist archives")
    elif is_prebuilt_llama_image(base_os):
        problems.append(f"base_os: '{base_os}' is a prebuilt llama.cpp image; targets build llama.cpp from source "
                        "with the shared recipe")
    out["base_os"] = base_os

    packages = data.get("packages", [])
    if isinstance(packages, str):
        packages = packages.split()
    packages = [str(p).strip() for p in packages if str(p).strip()]
    bad = [p for p in packages if not DEB_PACKAGE_RE.fullmatch(p)]
    if bad:
        problems.append(f"packages: invalid Debian package names: {', '.join(bad[:5])}")
    out["packages"] = packages

    for key in ("cpu_flags", "cmake_flags"):
        value = data.get(key, "") or ""
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        tokens = str(value).split()
        bad = [t for t in tokens if not FLAG_RE.fullmatch(t)]
        if bad:
            problems.append(f"{key}: only plain compiler/CMake options like -mcpu=cortex-a76 or -DGGML_CUDA=ON: {', '.join(bad[:5])}")
        elif key == "cmake_flags":
            problems += [f"cmake_flags: {problem}" for problem in download_flag_problems(" ".join(tokens))]
        out[key] = " ".join(tokens)

    boards = data.get("supported_boards") or []
    boards = [str(b).strip() for b in ([boards] if isinstance(boards, str) else boards) if str(b).strip()]
    bad = [b for b in boards if not MODULE_NAME_RE.fullmatch(b) or not module_slug(b)]
    if bad:
        problems.append(f"supported_boards: plain names only (letters, digits, space, '_', '.', '-'): {', '.join(bad[:5])}")
    out["supported_boards"] = boards

    detection = str(data.get("detection_commands", "lscpu") or "lscpu")
    if not DETECTION_RE.fullmatch(detection):
        problems.append("detection_commands: a single simple command line expected")
    out["detection_commands"] = detection

    for key in ("setup_commands", "quantization_logic", "packaging_commands"):
        if key not in data or data.get(key) is None:
            continue
        code = str(data.get(key))
        if _CONTROL_CHARS.search(code):
            problems.append(f"{key}: contains control characters")
        for pattern, reason in SHELL_DENY_PATTERNS:
            if pattern.search(code):
                problems.append(f"{key}: not allowed, {reason}")
        out[key] = code

    _validate_vendor_fields(data, out, problems)
    if problems:
        raise ValueError("Invalid module definition:\n- " + "\n- ".join(problems))
    return out


DEFAULT_BASE = "debian:bookworm-slim"


def uses_gpu_template(data: Dict[str, Any]) -> bool:
    """Whether a module is generated from the GPU template (a CUDA SDK, or an NVIDIA image as the base)."""
    return "cuda" in str(data.get("sdk", "")).lower() or "nvidia" in str(data.get("base_os", "")).lower()


def effective_base(data: Dict[str, Any], template_dir: Path = SHIPPED_TEMPLATE) -> str:
    """The image the generated Dockerfile's builder stage starts from: the module's base_os, except that the GPU
    template keeps its own pinned Ubuntu base when the module names only the default base."""
    base = str(data.get("base_os") or DEFAULT_BASE)
    if uses_gpu_template(data) and base == DEFAULT_BASE:
        try:
            text = (Path(template_dir) / "Dockerfile.gpu").read_text(encoding="utf-8")
        except OSError:
            return base
        m = re.search(r"^FROM (\S+) AS builder$", text, re.MULTILINE)
        return m.group(1) if m else base
    return base


def _yaml_text(text: str) -> str:
    """AI text for target.yml: brackets removed (a bracketed value reads as a template placeholder), one line."""
    return " ".join(re.sub(r"[\[\]{}]", "", str(text)).split())


def capabilities_block(board_names: List[str], quantization_logic: str = "") -> Dict[str, Any]:
    """The capabilities of a generated target: its boards and GGUF with the types its convert stage handles (the
    labels of the case branches of the quantization logic in modules/convert_module.sh) or common defaults. Support level community: works for its makers."""
    from orchestrator.schemas.targets import TargetCapabilities
    labels = re.findall(r"^\s*([A-Za-z0-9_|\"' ]+)\)", quantization_logic or "", re.MULTILINE)
    quants = [q.strip(" \"'") for label in labels for q in label.split("|")]
    quants = list(dict.fromkeys(q for q in quants if re.fullmatch(r"[A-Za-z0-9_]{1,32}", q))) or list(GGUF_DEFAULT_QUANTS)
    boards = {module_slug(b): {"label": b} for b in board_names}
    block = {"schema_version": 1, "support_level": "community", "default_board": next(iter(boards)),
             "boards": boards,
             "formats": {"gguf": {"label": "GGUF (llama.cpp)", "input": ["hf"], "quantizations": quants,
                                  "default_quantization": quants[0], "imatrix": True, "dataset": "optional"}}}
    TargetCapabilities.model_validate(block)
    return block


def check_shell_syntax(root: Path) -> List[str]:
    """bash -n of every script below root (skipped where bash is missing)."""
    bash = shutil.which("bash")
    if not bash:
        return []
    errors = []
    for script in sorted(Path(root).rglob("*.sh")):
        result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
        if result.returncode != 0:
            errors.append(f"{script.relative_to(root).as_posix()}: shell syntax error: {result.stderr.strip()}")
    return errors


def _yaml_strings(value) -> Iterable[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _yaml_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _yaml_strings(item)
    elif isinstance(value, str):
        yield value


def _leftover_placeholders(root: Path) -> List[str]:
    found = []
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file() and not p.is_symlink()):
        if crawled_data(path.relative_to(root).as_posix()):
            continue  # a crawled page, or a NOTICE quoting one, may well write "YYYY-MM-DD"
        text = path.read_text(encoding="utf-8", errors="replace")
        match = TEMPLATE_PLACEHOLDERS.search(text)
        if match is None and path.name == "target.yml":
            # Any bracketed text in a value, e.g. "[Board Name 1]" (YAML lists are not strings).
            try:
                strings = list(_yaml_strings(yaml.safe_load(text)))
            except yaml.YAMLError:
                strings = []
            match = next((m for m in (re.search(r"\[[^\]\n]*[A-Za-z][^\]\n]*\]", s) for s in strings) if m), None)
        if match:
            found.append(f"{path.relative_to(root).as_posix()}: {match.group(0)}")
    return found


def validate_target_folder(root: Path, template_dir: Path = SHIPPED_TEMPLATE) -> List[str]:
    """Problems of a target folder that others will build (empty: none): target.yml with valid capabilities (and a
    valid runtime: block if it has one), every Dockerfile FROM pinned by digest unless it is an official base image
    or an earlier stage, no prebuilt llama.cpp (images, copied or downloaded binaries, packages: the framework shares
    recipes, decision D21), the shell deny-list and no llama.cpp option that downloads (download_flag_problems) on
    every script line that is not in the shipped template, bash -n, no template placeholders and no binary files. Used for generated modules, community exports and packs in
    quarantine."""
    from orchestrator.schemas.targets import TargetCapabilities, TargetRuntime
    root = Path(root)
    problems: List[str] = []
    meta = None
    try:
        meta = yaml.safe_load((root / "target.yml").read_text(encoding="utf-8"))
        TargetCapabilities.model_validate((meta or {}).get("capabilities"))
    except FileNotFoundError:
        problems.append("target.yml: missing")
    except Exception as e:  # YAML error, not a mapping, invalid capabilities
        problems.append(f"target.yml: no valid capabilities block ({str(e).splitlines()[0][:200]})")
    if isinstance(meta, dict) and meta.get("runtime") is not None:
        try:
            TargetRuntime.model_validate(meta["runtime"])
        except Exception as e:
            problems.append(f"target.yml: invalid runtime block ({str(e).splitlines()[0][:200]})")
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if b"\0" in data:
            problems.append(f"{rel}: binary file (packs carry text only)")
            continue
        lines = data.decode("utf-8", errors="replace").splitlines()
        if path.name.lower().startswith("dockerfile"):  # the builder also takes a lowercase 'dockerfile'
            stages = set()
            for line in (x for x in lines if not x.lstrip().startswith("#")):
                m = re.match(r"\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
                # COPY --from=<image> and RUN --mount=...,from=<image> pull images as well
                refs = [("FROM ", m.group(1))] if m else []
                refs += [("from=", ref) for ref in re.findall(r"(?:--|,)from=([^\s,]+)", line, re.IGNORECASE)]
                for label, image in refs:
                    if image.lower() not in stages and not image.isdigit() and (not IMAGE_RE.fullmatch(image) or (
                            "@sha256:" not in image and not image.startswith(TRUSTED_IMAGE_PREFIXES))):
                        problems.append(f"{rel}: {label}{image} must be pinned by digest (image@sha256:...)")
                if m and m.group(2):
                    stages.add(m.group(2).lower())
        elif path.suffix == ".sh":
            template = template_dir / rel
            known = set(template.read_text(encoding="utf-8", errors="replace").splitlines()) if template.is_file() else set()
            for number, line in enumerate(lines, 1):
                if line in known:
                    continue
                problems += [f"{rel}:{number}: not allowed, {reason}" for pattern, reason in SHELL_DENY_PATTERNS
                             if pattern.search(line)]
                # the module's CMake flags (CMAKE_OPTIONS of config_module.sh) and any cmake call of its scripts
                problems += [f"{rel}:{number}: not allowed, {problem}" for problem in download_flag_problems(line)]
    problems += scan_target_folder(root, skip=(KNOWLEDGE_SNAPSHOT,))
    problems += [f"template placeholder left: {p}" for p in _leftover_placeholders(root)]
    return problems + check_shell_syntax(root)


class ModuleGenerator:
    """
    Generates file structure and content for new hardware targets.
    Uses a template-based approach to ensure consistency across all modules.
    Now capable of attaching Knowledge Snapshots (RAG Data).
    """
    
    def __init__(self, targets_dir: Path):
        """
        Initialize the ModuleGenerator.
        
        Args:
            targets_dir: Base directory where targets are stored (e.g. /app/targets)
        """
        self.logger = get_logger(__name__)
        self.targets_dir = targets_dir
        
        # Template Verzeichnis liegt parallel zu den Targets
        # Wir suchen es relativ zum targets_dir oder via absolutem Fallback
        self.template_dir = targets_dir / "_template"

        if not self.template_dir.exists():
             # Fallback für Development-Umgebung
             possible_path = Path(__file__).parent.parent.parent / "targets" / "_template"
             if possible_path.exists():
                 self.template_dir = possible_path
             else:
                 self.logger.warning(f"Template directory not found at {self.template_dir} or {possible_path}")

    def generate_module(self, data: Dict[str, Any], framework_manager=None,
                        knowledge_roots: Optional[Iterable[str]] = None, profile=None) -> Path:
        """
        Generate a complete module from configuration data.
        
        Args:
            data: Dictionary containing configuration keys.
            framework_manager: Optional reference to access Community/RAG Managers for snapshots.
            knowledge_roots: Opt-in knowledge snapshot: the root URLs crawled for this target. Only their
                pages are attached (scrubbed); without roots nothing from the knowledge base is attached.
            profile: The hardware probe of the TARGET (a HardwareProfile), if one was given. The runtime: block of
                target.yml is the build recipe's plan for the device runtime from it; cpu_flags and cmake_flags
                are kept there as advisory text and never applied (decision D20).
        
        Returns:
            Path: Path to the created module directory
        """
        data = validate_module_data(data)
        data["runtime_block"] = self._runtime_block(data, profile)
        module_name = data["module_name"]
        slug = module_slug(module_name)
        targets_root = Path(self.targets_dir).resolve()
        target_dir = (targets_root / slug).resolve()
        if target_dir.parent != targets_root:
            raise ValueError(f"Module folder '{slug}' would be outside {targets_root}.")
        # Never overwrite or shadow an existing target: names are compared ignoring case and separators ('rockchip'
        # and 'Rock-Chip' must not capture 'Rockchip'), against folder names and target ids.
        taken = target_name_collision(targets_root, slug, module_name)
        if taken:
            raise ValueError(f"A target named '{taken}' already exists, and '{module_name}' would read the same "
                             "(names are compared ignoring case and separators); choose another module name.")
        
        self.logger.info(f"Generating new target module: '{module_name}' at {target_dir}")
        
        try:
            # 1. Create Directory Structure
            ensure_directory(target_dir)
            ensure_directory(target_dir / "modules")
            ensure_directory(target_dir / "scripts")
            
            # 2. Generate Files via Template Processing
            
            # Core Configuration
            self._process_template("target.yml", target_dir, data)
            
            # Container Definition
            # Check if GPU template is needed
            if uses_gpu_template(data):
                self._process_template("Dockerfile.gpu", target_dir, data, target_filename="Dockerfile")
            else:
                self._process_template("Dockerfile", target_dir, data)
            
            # The four stage modules of the build (DECISIONS U9): source -> config -> convert -> target, each with the
            # template's instructions for its stage; the wizard's setup commands go into the source stage, its
            # quantization logic into the convert stage and its packaging commands into the target stage.
            for stage in STAGE_MODULES:
                self._process_template(f"modules/{stage}_module.sh", target_dir, data)

            # The stage runner that runs them
            self._write_build_script(target_dir, data)
            
            # Specialized Modules (Optional copy if template exists): the convert stage's format helpers, and the
            # host benchmark that no build runs (models for a board are measured on the board)
            self._process_template("modules/rknn_module.sh", target_dir, data)
            self._process_template("modules/rkllm_module.sh", target_dir, data)
            self._process_template("modules/benchmark_module.sh", target_dir, data)

            # 3. Generate Helper Scripts (Dynamic)
            self._write_profile_script(target_dir, data)

            # 4. Copy Python Scripts (Static Helpers)
            self._copy_scripts(target_dir)

            # 5. Fallback stage modules, only when the template folder lacks one
            self._write_standard_modules(target_dir)
            
            # 6. Knowledge snapshot: only on request, and only the pages crawled for this target.
            roots = sorted({str(r) for r in knowledge_roots or [] if r})
            if framework_manager and roots:
                self._create_knowledge_snapshot(target_dir, framework_manager, slug, roots)

            # 7. What others would build: capabilities, pinned images, deny-list, bash -n, no placeholders; and the
            # image the person approved is the one the builder stage starts from.
            problems = validate_target_folder(target_dir, self.template_dir)
            problems += self._base_problems(target_dir, effective_base(data, self.template_dir))
            if problems:
                raise ValueError("The generated module is not usable:\n- " + "\n- ".join(problems[:20]))
            
            self.logger.info(f"Module generation for '{module_name}' completed successfully.")
            return target_dir
            
        except Exception as e:
            self.logger.error(f"Failed to generate module: {e}")
            # The folder did not exist before (checked above): remove the half-written target so it
            # neither shows up as a buildable target nor blocks a corrected retry.
            shutil.rmtree(target_dir, ignore_errors=True)
            raise e

    @staticmethod
    def _base_problems(target_dir: Path, base_os: str) -> List[str]:
        """The generated Dockerfile's builder stage must start from exactly the module's base image (the one the
        person was shown); a template that keeps another base would build something else than was approved."""
        try:
            text = (Path(target_dir) / "Dockerfile").read_text(encoding="utf-8")
        except OSError:
            return ["Dockerfile: missing"]
        for line in text.splitlines():
            m = re.match(r"\s*FROM\s+(?:--platform=\S+\s+)?(\S+)\s+AS\s+builder\s*$", line, re.IGNORECASE)
            if m:
                return [] if m.group(1) == base_os else [
                    f"Dockerfile: the builder stage starts from {m.group(1)}, not from the module's base image {base_os}"]
        return ["Dockerfile: no builder stage"]

    def _runtime_block(self, data: Dict[str, Any], profile) -> Dict[str, Any]:
        """The runtime: block of the new target.yml (orchestrator/runtime/build/target_runtime.py). A recipe that
        cannot plan says so in the block's notes; it never stops the module from being created."""
        from orchestrator.runtime.build.target_runtime import runtime_block
        from orchestrator.runtime.profile import HardwareProfile
        if profile is not None and not isinstance(profile, HardwareProfile):
            raise ValueError("profile: expected a parsed hardware profile of the target")
        advisory = {"cpu_flags": data.get("cpu_flags", ""), "cmake_flags": data.get("cmake_flags", "")}
        try:
            return runtime_block(profile, architecture=data["architecture"], advisory=advisory)
        except Exception as e:  # e.g. an unreadable runtime catalog
            self.logger.warning(f"Device runtime not planned for the new module: {e}")
            block = runtime_block(None, architecture=data["architecture"], advisory=advisory)
            block["notes"] = [f"the device runtime could not be planned: {str(e)[:200]}"]
            return block

    def _create_knowledge_snapshot(self, target_dir: Path, framework_manager, module_slug: str,
                                   roots: List[str]):
        """Exports the pages crawled from the given root URLs (scrubbed) into the module's knowledge folder."""
        try:
            # Lazy import to prevent circular dependencies at module level
            from orchestrator.Core.community_manager import CommunityManager

            # CommunityManager owns the export and its privacy scrub
            cm = CommunityManager(framework_manager)
            self.logger.info("Creating Knowledge Snapshot for module...")
            snapshot_path_str = cm.export_knowledge_base(output_name_prefix=f"knowledge_{module_slug}",
                                                         root_urls=roots)

            if snapshot_path_str and os.path.exists(snapshot_path_str):
                src = Path(snapshot_path_str)
                dst = target_dir / KNOWLEDGE_SNAPSHOT
                ensure_directory(dst.parent)
                shutil.move(src, dst)
                (dst.parent / "README.md").write_text(KNOWLEDGE_README.format(slug=module_slug), encoding="utf-8")
                self._write_licence_files(dst)
                self.logger.info(f"Attached the knowledge snapshot: {dst}")
            else:
                self.logger.warning("No knowledge snapshot attached: no stored page comes from the crawled URLs, "
                                    "or the knowledge base could not be read.")
                
        except Exception as e:
            self.logger.warning(f"Failed to create knowledge snapshot: {e}")
            # Non-fatal error, module creation should still succeed

    def _write_licence_files(self, snapshot: Path) -> None:
        """The licence texts and the NOTICE the snapshot's pages need, next to it (rag/pack_licences.py): pages kept in
        full under a licence that asks for its text get it in knowledge/LICENSES/, and NOTICE lists every page."""
        from orchestrator.rag import pack_licences
        try:
            entries = json.loads(snapshot.read_text(encoding="utf-8"))
            files = pack_licences.write(snapshot.parent, [e for e in entries if isinstance(e, dict)]
                                        if isinstance(entries, list) else [], prefix="knowledge/")
        except (OSError, ValueError) as e:
            self.logger.warning(f"The licence files of the knowledge snapshot were not written: {e}")
            return
        for note in files.notes():
            self.logger.info(f"Knowledge snapshot: {note}")

    def _process_template(self, filename: str, target_dir: Path, data: Dict[str, Any], target_filename: str = None):
        """
        Reads a template file, replaces placeholders with data, and writes to target.
        """
        src = self.template_dir / filename
        dst_name = target_filename if target_filename else filename
        dst = target_dir / dst_name
        
        if not src.exists():
            self.logger.debug(f"Skipping optional template {filename} (not found)")
            return

        try:
            content = src.read_text(encoding="utf-8")
            
            # Prepare Data
            packages = data.get("packages", [])
            if isinstance(packages, str): packages = packages.split()
            packages_str = " \\\n        ".join(packages)
            
            # Replacements Map
            replacements = {
                "[MODULE_NAME]": data.get("module_name", "Unknown"),
                "[Hardware-Familie]": data.get("module_name", "Unknown"),
                "[IHRE_ARCHITEKTUR]": data.get("architecture", "aarch64"),
                "[Hersteller]": "Community",
                "debian:bookworm-slim": data.get("base_os", "debian:bookworm-slim"),
                "{packages_str}": packages_str,
                "# [SDK_SETUP_COMMANDS]": data.get("setup_commands", ""),
                "[QUANTIZATION_LOGIC]": data.get("quantization_logic", ""),
                "# [PACKAGING_COMMANDS]": data.get("packaging_commands", ""),  # no build/ folder by default
                "[CPU_FLAGS]": data.get("cpu_flags", ""),
                "[CMAKE_FLAGS]": data.get("cmake_flags", "")
            }
            
            for key, value in replacements.items():
                if value is not None:
                    content = content.replace(key, str(value))
            if filename.startswith("Dockerfile"):  # the framework-written vendor blocks, after the AI's fields
                for key, value in vendor_blocks(data).items():
                    content = content.replace(key, value)

            if filename == "Dockerfile.gpu":
                content = self._cuda_opt_in(content, str(data.get("base_os", "")))
                content = self._gpu_base(content, str(data.get("base_os", "")))
                if re.search(r"^ARG CUDA_SOURCE=prebuilt-image$", content, re.MULTILINE):
                    self.logger.warning(f"CUDA for '{data.get('module_name')}' comes from the prebuilt image "
                                        f"{data.get('base_os')} (your explicit opt-in, not self-built), not from "
                                        "NVIDIA's pinned redist archives.")

            # YAML Handling
            if dst.suffix == ".yml":
                try:
                    self._update_yaml_config(content, dst, data)
                except:
                    self._write_file(dst, content)
            else:
                self._write_file(dst, content)
                
            # Permissions
            if dst.suffix == ".sh":
                self._make_executable(dst)
                
        except Exception as e:
            self.logger.error(f"Error processing template {filename}: {e}")

    @staticmethod
    def _gpu_base(content: str, base_os: str) -> str:
        """The GPU template on the module's own base image: a base other than the default and the prebuilt CUDA opt-in
        (for example a vendor's toolchain image) replaces the template's Ubuntu base in both stages, so the image that
        is built is the one the person approved (CUDA still comes from the pinned redist archives)."""
        if not base_os or base_os == DEFAULT_BASE or base_os.startswith("nvidia/cuda:"):
            return content
        content, n = re.subn(r"^FROM \S+ AS (builder|runtime)$", lambda m: f"FROM {base_os} AS {m.group(1)}", content,
                             flags=re.MULTILINE)
        if n != 2:
            raise ValueError("the GPU template has no builder and runtime stage to put the base image into")
        return content

    @staticmethod
    def _cuda_opt_in(content: str, base_os: str) -> str:
        """The GPU template with a prebuilt NVIDIA development image as the builder's base: the explicit opt-in of
        decision D6 (validate_module_data accepts only a digest-pinned *-devel-ubuntu22.04 image), labelled through
        CUDA_SOURCE=prebuilt-image. Every other base keeps the template: CUDA from the pinned redist archives."""
        if not base_os.startswith("nvidia/cuda:"):
            return content
        content, stages = re.subn(r"^FROM \S+ AS builder$", f"FROM {base_os} AS builder", content, count=1,
                                  flags=re.MULTILINE)
        content, sources = re.subn(r"^ARG CUDA_SOURCE=redist$", "ARG CUDA_SOURCE=prebuilt-image", content, count=1,
                                   flags=re.MULTILINE)
        if (stages, sources) != (1, 1):
            raise ValueError("the GPU template has no builder stage or CUDA_SOURCE to switch to the prebuilt image")
        return content

    def _update_yaml_config(self, template_content: str, dst: Path, data: Dict[str, Any]):
        """target.yml from the template: metadata, boards, image name and the capabilities block."""
        config = yaml.safe_load(template_content)
        name = data["module_name"]
        boards = data.get("supported_boards") or [name]
        config.setdefault("metadata", {}).update({
            "name": name, "description": data.get("description") or f"Target for {name}",
            "maintainer": data.get("maintainer") or "community", "created": date.today().isoformat(),
            "architecture_family": data["architecture"], "vendor": data.get("vendor") or "community",
            "sdk": data.get("sdk", "none"), "base_image": effective_base(data, self.template_dir)})
        if data.get("provenance") == "ai-generated":
            # Untested is a fact to state, not a reason to refuse (design 1.3); builds of this target warn.
            config["metadata"].update({"provenance": "ai-generated", "untested": True})
            if data.get("assumptions"):
                config["metadata"]["assumptions"] = [_yaml_text(a) for a in data["assumptions"]]
            if data.get("base_image_reason"):
                config["metadata"]["base_image_reason"] = _yaml_text(data["base_image_reason"])
        config["supported_boards"] = [{"name": b} for b in boards]
        config.setdefault("docker", {})["image_name"] = f"llm-framework/{module_slug(name).replace('_', '-')}"
        config["capabilities"] = capabilities_block(boards, data.get("quantization_logic", ""))
        if data.get("runtime_block"):
            config["runtime"] = data["runtime_block"]

        with open(dst, "w", encoding="utf-8", newline="\n") as f:  # LF like every file of the module
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    def _write_build_script(self, target_dir: Path, data: Dict[str, Any]):
        """modules/build.sh: the template's stage runner; without a template folder (an incomplete installation) a
        self-contained dispatcher of the quantization logic."""
        src = self.template_dir / "modules/build.sh"
        if src.exists():
            self._process_template("modules/build.sh", target_dir, data)
            return

        # Fallback Generator
        quant_logic = data.get("quantization_logic", "")
        if not quant_logic:
            quant_logic = '    *) echo ">> Defaulting to FP16/Copy"; cp -r "$MODEL_SOURCE" "$OUTPUT_DIR/" ;;'

        content = f'''#!/bin/bash
# build.sh for {data["module_name"]}
# Generated by LLM Cross-Compiler Framework

set -euo pipefail

WORK_DIR="${{BUILD_CACHE_DIR:-/build-cache}}"
OUTPUT_DIR="${{WORK_DIR}}/output"
mkdir -p "$OUTPUT_DIR"

echo "=== Build Started: {data['module_name']} ==="
echo "Model: $MODEL_SOURCE"
echo "Task:  ${{MODEL_TASK:-LLM}}"
echo "Quantization: ${{QUANTIZATION:-None}}"

QUANT_TYPE="${{QUANTIZATION:-FP16}}"

case "$QUANT_TYPE" in
{quant_logic}
esac

echo "=== Build Completed ==="
'''
        dst = target_dir / "modules" / "build.sh"
        self._write_file(dst, content)
        self._make_executable(dst)

    def _write_profile_script(self, target_dir: Path, data: Dict[str, Any]):
        """Generates the hardware probe script for the target."""
        content = f'''#!/bin/bash
# Hardware Profile Generator
OUTPUT_FILE="target_hardware_config.txt"
echo "# Profile for {data['module_name']}" > "$OUTPUT_FILE"
{data.get("detection_commands", "lscpu >> $OUTPUT_FILE")}
echo "Generated $OUTPUT_FILE"
'''
        dst = target_dir / "generate_profile.sh"
        self._write_file(dst, content)
        self._make_executable(dst)

    def _copy_scripts(self, target_dir: Path):
        """Copies the static helper scripts to the target: the Python helpers and the shell scripts the target stage
        puts into the package for the board (package_test_model.sh, package_deploy.sh), executable like the stages."""
        src_scripts = self.template_dir / "scripts"
        dst_scripts = target_dir / "scripts"
        
        if src_scripts.exists():
            ensure_directory(dst_scripts)
            for item in src_scripts.glob("*.py"):
                shutil.copy2(item, dst_scripts / item.name)
            for item in src_scripts.glob("*.sh"):
                self._write_script(dst_scripts / item.name, item.read_text(encoding="utf-8"))

    def _write_standard_modules(self, target_dir: Path):
        """A stage module the template folder did not have (an incomplete installation): a stage that stops the
        build with the reason, never one that does nothing and lets the build look finished."""
        for stage in STAGE_MODULES:
            path = target_dir / "modules" / f"{stage}_module.sh"
            if not path.exists():
                self._write_script(path, STAGE_FALLBACK.format(stage=stage))

    def _write_script(self, path: Path, content: str):
        self._write_file(path, content)
        self._make_executable(path)

    def _write_file(self, path: Path, content: str):
        # LF on every host: the module's scripts and Dockerfiles run in a Linux container, where a Windows host's
        # CRLF breaks them ("$'\r': command not found").
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def _make_executable(self, path: Path):
        # Generated build scripts must run in the image; packs keep exactly the modes 0644 or 0755.
        try: os.chmod(path, 0o755)  # nosec B103
        except: pass


# --- the audit row of a module the person saved (moved unchanged from tools/tools_targets.py, chain c1-imports) ---

def record_module_saved(framework: Any, actor: str, data: Dict[str, Any], folder: Path, channel: str) -> None:
    """Audits a module the person saved from an AI proposal outside the registry (the GUI wizard, the CLI): actor,
    that it is AI-generated, the definition's sha256 and the base image (design 6.7). Never fails the save."""
    import hashlib

    from orchestrator.tools.policy import Decision, ToolTier
    registry = getattr(framework, "tool_registry", None)
    audit = getattr(registry, "audit", None)
    if audit is None:
        return
    text = json.dumps({k: data.get(k) for k in sorted(data) if k != "description"}, sort_keys=True, default=str)
    try:
        audit.record(actor, "module_saved", ToolTier.LOCAL_WRITE.value, Decision.ASK.value, "ok",
                     arguments={"target": Path(folder).name, "ai_generated": data.get("provenance") == "ai-generated",
                                "definition_sha256": hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
                                "base_image": str(data.get("base_os") or "")},
                     approved=True, extra={"confirm_channel": channel, "answer": "accepted"})
    except Exception:  # noqa: BLE001 - the module exists; the audit failure is logged by the audit log itself
        pass
