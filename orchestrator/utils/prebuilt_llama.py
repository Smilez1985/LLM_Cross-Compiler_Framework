#!/usr/bin/env python3
"""
Prebuilt llama.cpp in a target folder: the one pattern list.

The framework shares recipes, never compiled images or binaries: a target module (shipped, generated, from the
template or from a community pack) builds llama.cpp from source with the shared recipe (configs/llama_runtime.yml
pins the commit, orchestrator/runtime/build/recipe.py derives the options). It must not pull a prebuilt llama.cpp
image, copy llama.cpp binaries out of another image, download release archives or install a distribution's llama.cpp
package. validate_target_folder (orchestrator/Core/module_generator.py) reports every finding of scan_target_folder
for generated targets, community exports and target packs in quarantine.

Plain text only: no I/O beyond reading the files given, no network, no shell.
"""

import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Images whose repository names llama.cpp or its server (any registry, any tag or digest), for example
# ghcr.io/ggml-org/llama.cpp:server, ggml/llama.cpp, someone/llama-cpp-server.
_LLAMA_IMAGE = re.compile(r"llama[._-]?cpp|llama-server", re.IGNORECASE)
# The framework's own former prebuilt runtime images (contents not built by the recipe).
KNOWN_PREBUILT_IMAGES = ("ghcr.io/smilez1985/llm-runtime", "ghcr.io/llm-framework/builder",
                         "ghcr.io/llm-framework/inference-node")

# Text that downloads prebuilt llama.cpp, wherever it appears in a Dockerfile instruction or a script line.
_DOWNLOADS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"github\.com/[^/\s\"']+/llama\.cpp/releases/(?:download|latest)", re.IGNORECASE),
     "downloads a llama.cpp release archive"),
    (re.compile(r"api\.github\.com/repos/[^/\s\"']+/llama\.cpp/releases", re.IGNORECASE),
     "looks up llama.cpp release archives"),
    (re.compile(r"\b(?:cudart-)?llama-b\d+-bin-", re.IGNORECASE), "names a llama.cpp release archive"),
    (re.compile(r"abetlen\.github\.io/llama-cpp-python/whl", re.IGNORECASE),
     "installs prebuilt llama-cpp-python wheels"),
)
# The binaries and libraries a llama.cpp build makes.
_BINARY = re.compile(r"\b(?:llama-(?:server|cli|quantize|imatrix|perplexity|bench|completion|run|mtmd-cli)|"
                     r"libllama\S*|libggml\S*)", re.IGNORECASE)
_FETCH_TOOL = re.compile(r"\b(?:curl|wget|aria2c|Invoke-WebRequest|iwr|urllib\.request|requests\.get|"
                         r"hf_hub_download|huggingface-cli\s+download|hf\s+download)\b|"
                         r"^\s*ADD\s+(?:--\S+\s+)*https?://", re.IGNORECASE)
_PACKAGE_INSTALL = re.compile(
    r"\b(?:apt-get|apt|apk|dnf|yum|microdnf|zypper|pacman|brew|port|nix-env|conda|mamba|micromamba|snap|winget|"
    r"choco|scoop)\b.*?(?:\binstall\b|\badd\b|\s-S\b|\s-i\b).*?"
    r"(?<![\w.-])(?:llama\.cpp[\w.-]*|llama-cpp[\w.-]*|llamacpp|libllama[\w.-]*|libggml[\w.-]*)(?![\w.-])",
    re.IGNORECASE)
# Shell command separators: a download and a binary name count together only within one command. A pipeline is one
# command here: `curl ... | tar -x llama-server` unpacks the binary it downloads.
_COMMANDS = re.compile(r"&&|\|\||[;\n]")
# A RUN step of the shared recipe compiles with CMake: llama.cpp binaries of such a stage are built from source.
_RUN = re.compile(r"^\s*RUN\b", re.IGNORECASE)
_COMPILES = re.compile(r"\bcmake\b", re.IGNORECASE)

_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
_FROM_REF = re.compile(r"(?:--|,)from=([^\s,]+)", re.IGNORECASE)
_COPY = re.compile(r"^\s*(?:COPY|ADD)\b", re.IGNORECASE)
SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".ps1", ".bat", ".cmd", ".yml", ".yaml")
_ADVICE = "the framework shares recipes, not binaries: build llama.cpp from source with the shared recipe"


def is_prebuilt_llama_image(image: str) -> bool:
    """True for an image reference that is a prebuilt llama.cpp (by its repository name or a known image)."""
    ref = (image or "").strip().lower()
    repo = ref.split("@", 1)[0]
    last = repo.rsplit("/", 1)
    if ":" in last[-1]:  # the tag, not a registry port
        repo = repo[:len(repo) - len(last[-1])] + last[-1].split(":", 1)[0]
    return bool(_LLAMA_IMAGE.search(repo)) or any(repo == known or repo.startswith(known + "/")
                                                    for known in KNOWN_PREBUILT_IMAGES)


def _logical_lines(text: str, comment: str = "#") -> List[Tuple[int, str]]:
    """(first line number, text) of each instruction: lines ending in a backslash are joined with the next one, and
    comment lines are left out (Docker drops them inside a continued instruction too)."""
    out: List[Tuple[int, str]] = []
    current: List[str] = []
    start = 0
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(comment):
            continue
        if not current:
            start = number
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            current.append(stripped[:-1])
            continue
        current.append(stripped)
        out.append((start, " ".join(current)))
        current = []
    if current:
        out.append((start, " ".join(current)))
    return out


def _text_findings(line: str) -> List[str]:
    """At most one download finding and one package finding per instruction or line."""
    found: List[str] = []
    commands = _COMMANDS.split(line)
    download = next((reason for pattern, reason in _DOWNLOADS if pattern.search(line)), None)
    if download is None:
        for command in commands:
            binary = _BINARY.search(command)
            if binary and _FETCH_TOOL.search(command):
                download = f"downloads llama.cpp binaries ({binary.group(0)})"
                break
    if download:
        found.append(download)
    if any(_PACKAGE_INSTALL.search(command) for command in commands):
        found.append("installs a prebuilt llama.cpp package")
    return found


class _Stage:
    """One build stage: its name, its FROM reference, and whether llama.cpp binaries in it are built from source (a
    RUN step that compiles, or a COPY --from= of a stage whose binaries are)."""

    def __init__(self, name: Optional[str], base: str):
        self.name, self.base, self.builds = name, base, False


def _stage_of(stages: List[_Stage], ref: str) -> Optional[int]:
    """The index of the earlier stage `ref` names (by name or number), None for an image."""
    if ref.isdigit():
        return int(ref) if int(ref) < len(stages) else None
    for index in range(len(stages) - 1, -1, -1):
        if stages[index].name == ref.lower():
            return index
    return None


def _origin(stages: List[_Stage], index: int) -> Tuple[bool, str]:
    """(llama.cpp binaries of the stage are built from source, the image at the root of its FROM chain). A stage that
    only names a foreign image (FROM acme/tools@sha256:... AS tools) holds that image's files, not built ones."""
    seen: set = set()
    base = stages[index].base
    current: Optional[int] = index
    while current is not None and current not in seen:
        seen.add(current)
        stage = stages[current]
        if stage.builds:
            return True, stage.base
        base = stage.base
        parent = _stage_of(stages[:current], base)
        current = parent if not base.isdigit() else None
    return False, base


def scan_dockerfile(text: str, name: str = "Dockerfile") -> List[str]:
    """Findings of one Dockerfile: prebuilt llama.cpp images in FROM, COPY --from and RUN --mount=...,from=, llama.cpp
    binaries copied out of another image (also through a stage that only names that image), and downloads or package
    installs of prebuilt llama.cpp."""
    findings: List[str] = []
    stages: List[_Stage] = []
    for number, line in _logical_lines(text):
        where = f"{name}:{number}"
        m = _FROM.match(line)
        if m:
            base = m.group(1)
            if _stage_of(stages, base) is None and not base.isdigit() and is_prebuilt_llama_image(base):
                findings.append(f"{where}: pulls a prebuilt llama.cpp image ({base}); {_ADVICE}")
            stages.append(_Stage(m.group(2).lower() if m.group(2) else None, base))
        # COPY/ADD --from= copies files out of an image or stage; RUN --mount=...,from= mounts one.
        takes = bool(_COPY.match(line) or _RUN.match(line))
        # The binary names of a RUN step that compiles are its build targets.
        names_binary = bool(_BINARY.search(line)) and not (_RUN.match(line) and _COMPILES.search(line))
        for ref in _FROM_REF.findall(line):
            index = _stage_of(stages[:-1], ref) if stages else None
            if index is None:
                if ref.isdigit():
                    continue
                if is_prebuilt_llama_image(ref):
                    findings.append(f"{where}: pulls a prebuilt llama.cpp image ({ref}); {_ADVICE}")
                elif takes and names_binary:
                    findings.append(f"{where}: copies llama.cpp binaries out of the image {ref}; {_ADVICE}")
                continue
            built, image = _origin(stages, index)
            if built:
                if takes and stages:
                    stages[-1].builds = True  # the binaries it takes over are built from source
            elif takes and names_binary:
                findings.append(f"{where}: copies llama.cpp binaries out of the image {image} (stage {ref}), which "
                                f"does not build them; {_ADVICE}")
        if stages and _RUN.match(line) and _COMPILES.search(line):
            stages[-1].builds = True
        findings += [f"{where}: {reason}; {_ADVICE}" for reason in _text_findings(line)]
    return findings


def scan_script(text: str, name: str) -> List[str]:
    """Findings of a script or YAML file: downloads and package installs of prebuilt llama.cpp."""
    findings: List[str] = []
    for number, line in _logical_lines(text):
        findings += [f"{name}:{number}: {reason}; {_ADVICE}" for reason in _text_findings(line)]
    return findings


def is_dockerfile(path: Path) -> bool:
    return path.name.lower().startswith("dockerfile")  # Dockerfile, dockerfile, Dockerfile.gpu


def _is_script(path: Path) -> bool:
    """A file without a known suffix that starts with #! (a script a Dockerfile RUN step can execute)."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def scan_target_folder(root: Path, skip: Iterable[str] = ()) -> List[str]:
    """Findings of every Dockerfile, script (by its suffix, or any file that starts with #!) and YAML file below
    `root` (paths relative to it in the messages). Files in `skip` (relative POSIX paths, e.g. a crawled knowledge
    snapshot) and symbolic links are left out."""
    root = Path(root)
    skipped = set(skip)
    findings: List[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        if rel in skipped:
            continue
        if not (is_dockerfile(path) or path.suffix.lower() in SCRIPT_SUFFIXES or _is_script(path)):
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary files are refused by the validator on their own
        text = data.decode("utf-8", errors="replace")
        findings += scan_dockerfile(text, rel) if is_dockerfile(path) else scan_script(text, rel)
    return findings
