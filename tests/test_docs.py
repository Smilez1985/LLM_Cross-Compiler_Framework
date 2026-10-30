"""Documentation rules of the repository, checked on every test run.

Documents are all Markdown files of the repository (README*, CONTRIBUTING*, CHANGELOG*, docs/, configs/,
targets/, data/ and wherever else one is added), except the commented EXCEPTIONS below.

- Every document exists twice: X.md in English (primary) and X_DE.md in German, and each links to its twin.
- Every relative link points to an existing file and, with an #anchor, to an existing heading
  (GitHub anchor rules).
- The blueprint is three files (DECISIONS round 34): docs/DESIGN.md, docs/ROADMAP.md and CHANGELOG.md, each with
  its German twin; docs/BLUEPRINT.md is their short index.
- CHANGELOG.md and CHANGELOG_DE.md (Keep a Changelog 1.1.0, without an [Unreleased] section) say what was done: the
  same versions in the same order with the same dates and the same sections, every entry stamped with its time and
  version (the German twin with the same stamp), a link per version. The roadmap (docs/ROADMAP.md and its German
  twin) says what comes: the changelogs link to it, it names no version that is already released, both languages
  give every item the same status, no item is "done", and no item is also a changelog entry.
- The newest version in both changelogs is orchestrator.__version__, and the version line at the top of every other
  document names that version.
"""

import fnmatch
import os
import re
import subprocess
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import unquote

import pytest

from conftest import REPO_ROOT
from orchestrator import __version__

# --- What the repository must contain --------------------------------------------------------------------

# Every document of the framework as an (English, German) pair. A new document is added here and gets both
# files; the general twin check below also catches documents that are missing from this list.
EXPECTED_PAIRS: List[Tuple[str, str]] = [
    ("README.md", "README_DE.md"),
    ("CONTRIBUTING.md", "CONTRIBUTING_DE.md"),
    ("CHANGELOG.md", "CHANGELOG_DE.md"),
    # The blueprint is three files (DECISIONS round 34): DESIGN (what, how, why), ROADMAP (what comes) and the
    # CHANGELOG above (what was); BLUEPRINT is their short index, kept so that old links lead somewhere.
    ("docs/DESIGN.md", "docs/DESIGN_DE.md"),
    ("docs/ROADMAP.md", "docs/ROADMAP_DE.md"),
    ("docs/BLUEPRINT.md", "docs/BLUEPRINT_DE.md"),
    ("docs/CLI_Usage.md", "docs/CLI_Usage_DE.md"),
    ("docs/Features.md", "docs/Features_DE.md"),
    ("docs/Module_Developer_Guide.md", "docs/Module_Developer_Guide_DE.md"),
    ("docs/Container_Setup.md", "docs/Container_Setup_DE.md"),  # the framework in a container you build locally
    ("docs/Mojo.md", "docs/Mojo_DE.md"),  # the health check (Mojo Oracle and Mojo Miracle)
    ("configs/SSOT_ARCHITECTURE.md", "configs/SSOT_ARCHITECTURE_DE.md"),
    ("data/calibration/README.md", "data/calibration/README_DE.md"),
]

# Documents that were replaced by the pairs above. They must not come back next to their replacements.
RETIRED_DOCUMENTS: Dict[str, str] = {
    "docs/CLI_Usage_EN.md": "docs/CLI_Usage.md",
    "docs/CLI_Anleitung.md": "docs/CLI_Usage_DE.md",
    "docs/features_deep_dive.md": "docs/Features.md",
    "docs/Module_Developer_Guide_EN.md": "docs/Module_Developer_Guide.md",
    "docs/Leitfaden für Modulentwickler_DE.md": "docs/Module_Developer_Guide_DE.md",
}

# Files that are not documents in the sense of the two-language rule (patterns relative to the repository
# root, "*" also matches "/"). Every entry needs its reason; nothing else is exempt.
EXCEPTIONS: Dict[str, str] = {
    # The license is legally binding in its original wording; a translation would be a second license text.
    "LICENSE*": "license text, English original only",
    # Notices for adapted third-party code carry those projects' license texts, which bind in their original
    # wording for the same reason.
    "THIRD_PARTY_NOTICES*": "third-party license notices, English original only",
    # The retrieval evaluation searches a fixed corpus of documentation excerpts (llm-cli ditto eval --kind
    # retrieval); each excerpt is test data in the language it was written in, and its cases depend on that.
    "evals/retrieval/corpus/*": "fixture corpus of the retrieval evaluation (test data)",
    # Dated working notes for the next session (German only by design, see the note at their top); they record
    # a state of work and are not product documentation.
    "handover/*": "internal handover notes, not product documentation",
    # Knowledge packs shared by the community are data that users import, written in whatever language the
    # sources had.
    "community/*": "community knowledge packs (data)",
    # The module generator writes a README.md next to the knowledge snapshot of a generated target
    # (orchestrator/Core/module_generator.py); it describes data and is regenerated, not maintained.
    "targets/*/knowledge/*": "generated knowledge snapshot of a target (data)",
    # The target template is copied into every new target and filled in there; its files are templates.
    "targets/_template/*": "target template, copied and filled in per target",
    # Issue and pull request templates are shown by GitHub in one language.
    ".github/*": "GitHub templates",
    # Runtime folders of a local installation (relative data paths resolve against the installation): downloaded
    # models bring their own model cards, every build writes a Model_Card.md. Not part of the repository:
    # .gitignore ignores these folders (/cache/, /models/, /output/, /logs/), and they stay listed here so that a
    # checkout whose .gitignore differs, or a run without git, does not check them either.
    "cache/*": "runtime data (cache folder)",
    "models/*": "runtime data (downloaded models and their model cards)",
    "output/*": "runtime data (build results with Model_Card.md)",
    "logs/*": "runtime data (logs)",
}

GERMAN_SUFFIX = "_DE.md"

# Keep a Changelog section types and their names in CHANGELOG_DE.md, in the order the spec lists them. The German
# page of Keep a Changelog keeps the English type names; this repository translates them, because the German
# changelog is written for German readers (CHANGELOG.md keeps the English names for tools).
CHANGELOG_SECTIONS_DE: Dict[str, str] = {
    "Added": "Hinzugefügt",
    "Changed": "Geändert",
    "Deprecated": "Veraltet",
    "Removed": "Entfernt",
    "Fixed": "Behoben",
    "Security": "Sicherheit",
}

# The roadmap lists what comes; the changelogs link to it from their header. The roadmap is a document of its own:
# everything in it is roadmap.
ROADMAP_LINKS: Dict[str, str] = {
    "CHANGELOG.md": "docs/ROADMAP.md",
    "CHANGELOG_DE.md": "docs/ROADMAP_DE.md",
}

# The three files of the blueprint and their index (English, German), DECISIONS round 34.
BLUEPRINT_FILES: List[Tuple[str, str]] = [
    ("docs/DESIGN.md", "docs/DESIGN_DE.md"),
    ("docs/ROADMAP.md", "docs/ROADMAP_DE.md"),
    ("CHANGELOG.md", "CHANGELOG_DE.md"),
]
BLUEPRINT_INDEX: Tuple[str, str] = ("docs/BLUEPRINT.md", "docs/BLUEPRINT_DE.md")
# The index stays short: what each file holds and where the old sections went.
BLUEPRINT_INDEX_MAX_LINES = 60

# Every second- and third-level anchor of docs/BLUEPRINT(_DE).md before the split (at cs-c5-v), in order. Nothing is
# lost: each one stays an anchor of the index, a design anchor survives in DESIGN (without its "(unreleased)" mark),
# a roadmap anchor (from "roadmap" up to "history") leads into ROADMAP.
OLD_BLUEPRINT_ANCHORS: Dict[str, List[str]] = {
    "docs/BLUEPRINT.md": [
    "contents", "in-short", "purpose-and-principles", "architecture-overview", "layers-unreleased", "components",
    "where-things-are-stored", "data-flows", "a-build-from-request-to-device", "a-tool-call-from-an-mcp-client",
    "an-ai-app-in-the-open-window-unreleased", "a-question-to-ditto-unreleased", "self-healing-approval",
    "the-local-model-for-offline-mode-unreleased", "offline-mode-and-the-local-network-unreleased",
    "deep-ingest-from-a-web-page-to-the-knowledge-base-unreleased", "a-knowledge-base-search-unreleased",
    "the-knowledge-bases-storage-qdrant-edge-unreleased", "community-packs-format-trust-and-exchange-unreleased",
    "recent-inputs-unreleased", "security-model", "known-gaps", "key-decisions",
    "versioning-and-documentation-policy", "roadmap", "310---ditto-rebuild-sign-in-and-local-ai-in-progress",
    "next---build-service-queue-and-modes-planned-starts-right-after-the-architecture-consolidation",
    "320---device-runtime-and-the-maintainers-hardware-planned", "330---lora-and-large-models-planned",
    "340---supply-chain-and-community-planned", "rust-parts-planned", "later-planned", "history", "glossary",
    ],
    "docs/BLUEPRINT_DE.md": [
    "inhalt", "kurz-gesagt", "zweck-und-grundsätze", "architektur-im-überblick", "schichten-unveröffentlicht",
    "bausteine", "wo-was-liegt", "datenflüsse", "ein-build-vom-auftrag-bis-zum-gerät",
    "ein-tool-aufruf-eines-mcp-clients", "eine-ki-app-im-offenen-fenster-unveröffentlicht",
    "eine-frage-an-ditto-unveröffentlicht", "freigabe-beim-self-healing",
    "das-lokale-modell-für-den-offline-modus-unveröffentlicht", "offline-modus-und-das-eigene-netz-unveröffentlicht",
    "deep-ingest-von-der-webseite-in-die-wissensbasis-unveröffentlicht",
    "eine-suche-in-der-wissensbasis-unveröffentlicht", "der-speicher-der-wissensbasis-qdrant-edge-unveröffentlicht",
    "community-pakete-format-vertrauen-und-austausch-unveröffentlicht",
    "zuletzt-verwendete-eingaben-unveröffentlicht", "sicherheitsmodell", "bekannte-lücken",
    "wichtige-entscheidungen", "versionierung-und-dokumentationsregeln", "roadmap",
    "310---ditto-neubau-anmeldung-und-lokale-ki-in-arbeit",
    "als-nächstes---build-dienst-warteschlange-und-modi-geplant-beginnt-direkt-nach-der-architektur-konsolidierung",
    "320---laufzeit-auf-dem-gerät-und-die-hardware-des-maintainers-geplant", "330---lora-und-große-modelle-geplant",
    "340---lieferkette-und-community-geplant", "rust-teile-geplant", "später-geplant", "geschichte", "glossar",
    ],
}

# The status in the last column of every roadmap table row, and its German name. There is no "done": a finished item
# leaves the roadmap in the commit that writes its changelog entry (DECISIONS round 34).
ROADMAP_STATUS_DE: Dict[str, str] = {
    "in progress": "in Arbeit",
    "planned": "geplant",
}
DONE_STATUSES = {"done", "fertig"}

# Every changelog entry starts with its stamp: `YYYY-MM-DD HH:MM UTC` · `vX.Y.Z` (DECISIONS round 34).
STAMP = re.compile(r"^[-*] `(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}) UTC` · `v(\d+\.\d+\.\d+)` \S")
STAMP_PREFIX = re.compile(r"^`\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC` · `v[^`]+` ")

# --- Finding the documents ------------------------------------------------------------------------------

# Folders the directory walk (used only without git) does not enter: runtime data and virtual environments.
_SKIP_DIRS = {"cache", "output", "models", "logs", "node_modules", "venv", "env", "__pycache__"}


def _git_markdown_files() -> Optional[List[str]]:
    """Markdown files git would commit (tracked plus untracked but not ignored), or None without git.

    Untracked files count, so a new document is checked before it is added."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
             "--", "*.md"],
            capture_output=True, check=True, env=env, timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = {name for name in out.decode("utf-8").split("\0") if name}
    return sorted(name for name in names if (REPO_ROOT / name).is_file())


def _walked_markdown_files() -> List[str]:
    found = []
    for folder, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in files:
            if name.endswith(".md"):
                found.append((Path(folder) / name).relative_to(REPO_ROOT).as_posix())
    return sorted(found)


def _excepted(rel: str) -> Optional[str]:
    for pattern, reason in EXCEPTIONS.items():
        if fnmatch.fnmatchcase(rel, pattern):
            return reason
    return None


ALL_MARKDOWN = _git_markdown_files() or _walked_markdown_files()
DOCUMENTS = [rel for rel in ALL_MARKDOWN if _excepted(rel) is None]


def _twin(rel: str) -> str:
    if rel.endswith(GERMAN_SUFFIX):
        return rel[: -len(GERMAN_SUFFIX)] + ".md"
    return rel[: -len(".md")] + GERMAN_SUFFIX


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# --- Markdown parsing (the subset GitHub needs for links and anchors) ----------------------------------

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_NOT_PARAGRAPH = re.compile(r"^ {0,3}([-*+>|#]|\d+[.)]\s)")
_INLINE_CODE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
_INLINE_LINK = re.compile(r"\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*\)")
_REFERENCE_DEF = re.compile(r"^ {0,3}\[[^\]]+\]:\s*(<[^>]*>|\S+)")
_HTML_LINK = re.compile(r"<(?:a|img|source)\b[^>]*?\b(?:href|src)\s*=\s*\"([^\"]+)\"", re.I)
_HTML_ID = re.compile(r"<[a-z][^>]*?\b(?:id|name)\s*=\s*\"([^\"]+)\"", re.I)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:|^//")


def _prose_lines(text: str) -> List[str]:
    """The lines outside fenced code blocks (fence lines themselves are dropped)."""
    lines, fence = [], None
    for line in text.splitlines():
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
            else:
                lines.append(line)
            continue
        # A closing fence uses the same character, is at least as long and has nothing after it.
        if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
            fence = None
    return lines


def github_slug(heading: str) -> str:
    """The anchor GitHub gives a heading: rendered text, lower case, punctuation removed, spaces to hyphens."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # images keep their alt text
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links keep their text
    text = re.sub(r"<[^>]+>", "", text)  # inline HTML
    text = text.replace("`", "")
    text = unicodedata.normalize("NFC", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(text: str) -> Set[str]:
    """Every anchor a document offers: its headings (with GitHub's -1, -2 suffixes) and HTML ids."""
    found: Set[str] = set()
    seen: Dict[str, int] = {}

    def add(heading: str) -> None:
        slug = github_slug(heading)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        found.add(slug if n == 0 else f"{slug}-{n}")

    previous = ""
    for line in _prose_lines(text):
        atx = _ATX.match(line)
        if atx:
            add(atx.group(2) or "")
        elif _SETEXT.match(line) and previous.strip() and not _NOT_PARAGRAPH.match(previous):
            add(previous)
        found.update(_HTML_ID.findall(line))
        previous = "" if atx else line
    return found


def links(text: str) -> List[str]:
    """Link and image targets outside code: inline, reference definitions and HTML href/src."""
    targets = []
    for line in _prose_lines(text):
        line = _INLINE_CODE.sub("", line)
        targets += _INLINE_LINK.findall(line)
        targets += _REFERENCE_DEF.findall(line)
        targets += _HTML_LINK.findall(line)
    return [t[1:-1] if t.startswith("<") and t.endswith(">") else t for t in targets]


def header(text: str) -> str:
    """The part of a document before its first second-level heading."""
    out = []
    for line in _prose_lines(text):
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out)


def link_destination(doc: str, target: str) -> Optional[Path]:
    """The file a relative link in document `doc` points to (normalized), or None for an external link."""
    if _SCHEME.match(target):
        return None
    path_part = unquote(target.partition("#")[0].split("?", 1)[0])
    base = REPO_ROOT / doc
    if not path_part:
        dest = base
    elif path_part.startswith("/"):
        dest = REPO_ROOT / path_part.lstrip("/")
    else:
        dest = base.parent / path_part
    return Path(os.path.normpath(dest))


def link_problem(doc: str, target: str) -> Optional[str]:
    """Why a link in document `doc` (relative to the repository) is broken, or None if it works."""
    dest = link_destination(doc, target)
    if dest is None:
        return None  # external (https:, mailto:, ...): not checked offline
    anchor = target.partition("#")[2]
    try:
        rel = dest.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return f"{target}: points outside the repository"
    if not dest.exists():
        return f"{target}: {rel} does not exist"
    if anchor and dest.is_file() and dest.suffix.lower() == ".md":
        if unquote(anchor) not in anchors(dest.read_text(encoding="utf-8")):
            return f"{target}: {rel} has no heading with the anchor #{unquote(anchor)}"
    return None


# --- Changelog parsing ------------------------------------------------------------------------------------

_VERSION_HEADING = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?(?: \[YANKED\])?\s*$")
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def changelog_entries(rel: str) -> List[Tuple[str, Optional[str], List[Tuple[str, int]]]]:
    """(version, date, [(section, number of top-level entries), ...]) for every version heading, in order."""
    entries: List[Tuple[str, Optional[str], List[Tuple[str, int]]]] = []
    for line in _prose_lines(_read(rel)):
        version = _VERSION_HEADING.match(line)
        if version:
            entries.append((version.group(1), version.group(2), []))
        elif line.startswith("## "):
            raise AssertionError(f"{rel}: second-level heading is not a version: {line!r}")
        elif entries and line.startswith("### "):
            entries[-1][2].append((line[4:].strip(), 0))
        elif entries and entries[-1][2] and re.match(r"^[-*] ", line):
            name, count = entries[-1][2][-1]
            entries[-1][2][-1] = (name, count + 1)
    return entries


def _semver(version: str) -> Tuple[int, int, int]:
    m = _SEMVER.match(version)
    assert m, f"not a semantic version: {version}"
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# --- (1) Every document in English and German -------------------------------------------------------------

@pytest.mark.parametrize("english,german", EXPECTED_PAIRS)
def test_expected_document_pair_exists(english, german):
    missing = [rel for rel in (english, german) if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"missing document(s): {', '.join(missing)}"


def test_every_document_has_its_twin():
    missing = [f"{rel} -> {_twin(rel)}" for rel in DOCUMENTS if not (REPO_ROOT / _twin(rel)).is_file()]
    assert not missing, ("documents without their English/German twin (add the twin or, for files that are "
                         "not documents, an entry with a reason in EXCEPTIONS):\n  " + "\n  ".join(missing))


def test_retired_documents_are_gone():
    back = [f"{old} (replaced by {new})" for old, new in RETIRED_DOCUMENTS.items() if (REPO_ROOT / old).exists()]
    assert not back, "replaced documents still present: " + ", ".join(back)


def test_every_document_links_to_its_twin():
    """The language switch at the top: X.md links to X_DE.md and back."""
    missing = []
    for rel in DOCUMENTS:
        twin = Path(os.path.normpath(REPO_ROOT / _twin(rel)))
        if not any(link_destination(rel, t) == twin for t in links(header(_read(rel)))):
            missing.append(f"{rel} (expected a link to {Path(_twin(rel)).name} before the first '## ' heading)")
    assert not missing, "documents without a link to their twin:\n  " + "\n  ".join(missing)


def test_exceptions_are_explained():
    for pattern, reason in EXCEPTIONS.items():
        assert pattern and reason.strip(), f"exception {pattern!r} needs a reason"
    # The framework's own documents can never be exempt.
    for english, german in EXPECTED_PAIRS:
        assert _excepted(english) is None and _excepted(german) is None, f"{english}/{german} must not be exempt"


# --- (2) Relative links and anchors -------------------------------------------------------------------------

@pytest.mark.parametrize("doc", DOCUMENTS)
def test_relative_links_resolve(doc):
    problems = [p for p in (link_problem(doc, t) for t in links(_read(doc))) if p]
    assert not problems, f"broken links in {doc}:\n  " + "\n  ".join(problems)


def test_github_slug_rules():
    assert github_slug("Roadmap") == "roadmap"
    assert github_slug("[Unreleased]") == "unreleased"
    assert github_slug("[3.0.0] - 2026-10-10") == "300---2026-10-10"
    assert github_slug("Unterstützte Targets und Formate") == "unterstützte-targets-und-formate"
    assert github_slug("`llm-cli ditto eval`") == "llm-cli-ditto-eval"
    assert github_slug("Knowledge base (RAG)") == "knowledge-base-rag"
    assert github_slug("Bekannte Einschränkungen") == "bekannte-einschränkungen"
    assert github_slug("Ditto & MCP: tools") == "ditto--mcp-tools"
    text = "# A\n\n## Same\n\n```\n## Not a heading\n```\n\n## Same\n\nSetext\n------\n<a id=\"custom\"></a>\n"
    assert anchors(text) == {"a", "same", "same-1", "setext", "custom"}


def test_link_extraction_skips_code():
    text = ("See [a](a.md#x) and ![img](assets/i.png \"t\").\n`[not](code.md)`\n\n```\n[no](fence.md)\n```\n"
            "[ref]: <docs/with space.md>\n<a href=\"html.md\">h</a>\n")
    assert links(text) == ["a.md#x", "assets/i.png", "docs/with space.md", "html.md"]


# --- (3) Changelogs in both languages, and the roadmap link -------------------------------------------------

def test_changelogs_have_the_same_versions_dates_and_sections():
    english = changelog_entries("CHANGELOG.md")
    german = changelog_entries("CHANGELOG_DE.md")
    assert [(v, d) for v, d, _ in english] == [(v, d) for v, d, _ in german], \
        "CHANGELOG.md and CHANGELOG_DE.md differ in their version headings or dates"
    for (version, _, sections_en), (_, _, sections_de) in zip(english, german):
        unknown = [name for name, _ in sections_en if name not in CHANGELOG_SECTIONS_DE]
        assert not unknown, f"[{version}]: sections that Keep a Changelog does not define: {unknown}"
        translated = [(CHANGELOG_SECTIONS_DE[name], count) for name, count in sections_en]
        assert sections_de == translated, (f"[{version}]: sections or number of entries differ "
                                           f"(English {sections_en}, German {sections_de})")


@pytest.mark.parametrize("changelog", ["CHANGELOG.md", "CHANGELOG_DE.md"])
def test_changelog_versions_are_newest_first_with_dates(changelog):
    """No [Unreleased] and no other undated section: the changelog lists only what is done (DECISIONS round 34)."""
    entries = changelog_entries(changelog)
    assert entries, f"{changelog} has no version"
    assert all(v != "Unreleased" for v, _, _ in entries), f"{changelog} has an [Unreleased] section"
    released = entries
    assert all(date for _, date, _ in released), "every version has a date (YYYY-MM-DD)"
    versions = [_semver(v) for v, _, _ in released]
    assert versions == sorted(versions, reverse=True) and len(set(versions)) == len(versions), \
        "versions must be unique and newest first"
    dates = [date for _, date, _ in released]
    assert dates == sorted(dates, reverse=True), "release dates must not increase downwards"


def section_lines(rel: str, section: Optional[str] = None) -> List[str]:
    """The prose lines of a document's second-level section (by its anchor), or of the whole document below its
    title when `section` is None (the roadmap is a document of its own)."""
    lines = _prose_lines(_read(rel))
    if section is None:
        return [line for line in lines if not line.startswith("# ")]
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            inside = github_slug(line[3:]) == section
            continue
        if inside:
            out.append(line)
    return out


def roadmap_versions(rel: str) -> List[str]:
    """Versions the roadmap names in its headings and table rows, in order."""
    versions = []
    for line in section_lines(rel):
        m = re.match(r"^#{2,6} (\d+\.\d+\.\d+)\b", line) or re.match(r"^\|\s*(\d+\.\d+\.\d+)\s*\|", line)
        if m:
            versions.append(m.group(1))
    return versions


def roadmap_in_progress(rel: str) -> Set[str]:
    """The versions the roadmap's version table marks as being built ("in progress", "in Arbeit")."""
    building = {"in progress", ROADMAP_STATUS_DE["in progress"]}
    found = set()
    for line in section_lines(rel):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and re.fullmatch(r"\d+\.\d+\.\d+", cells[0]) and cells[-1] in building:
            found.add(cells[0])
    return found


def test_roadmap_lists_only_what_comes():
    """Finished versions leave the roadmap at release; it names only versions newer than the newest release.

    The newest changelog section is the version being built (there is no [Unreleased]: finished work goes under the
    version it lands in, tests/test_versioning.py), so the roadmap may still name that one version for what of it is
    not done yet, marked "in progress" in its version table; every older version is released and must be gone from
    it."""
    newest = changelog_entries("CHANGELOG.md")[0][0]
    found = {}
    for target in ROADMAP_LINKS.values():
        rel = target.partition("#")[0]
        assert (REPO_ROOT / rel).is_file(), f"{rel}, the roadmap, is missing"
        found[rel] = roadmap_versions(rel)
        building = roadmap_in_progress(rel)
        released = [v for v in found[rel] if _semver(v) < _semver(newest) or (v == newest and v not in building)]
        assert not released, (f"{rel}: the roadmap still lists released version(s) {sorted(set(released))}; "
                              f"they belong in the changelog (newest release: {newest})")
    english, german = found.values()
    assert english == german, f"the English and German roadmaps name different versions: {found}"


def roadmap_statuses(rel: str, known: Set[str]) -> List[str]:
    """The status (last table cell) of every roadmap table row whose last cell is a known status, in order."""
    statuses = []
    for line in section_lines(rel):
        if line.lstrip().startswith("|"):
            last = line.strip().strip("|").split("|")[-1].strip()
            if last in known:
                statuses.append(last)
    return statuses


def test_roadmap_statuses_match_and_nothing_is_done():
    """Both roadmaps give every item the same status, and no item is "done": finished work is in the changelog."""
    english_doc, german_doc = (target.partition("#")[0] for target in ROADMAP_LINKS.values())
    for rel in (english_doc, german_doc):
        assert (REPO_ROOT / rel).is_file(), f"{rel}, the roadmap, is missing"
    english = roadmap_statuses(english_doc, set(ROADMAP_STATUS_DE))
    german = roadmap_statuses(german_doc, set(ROADMAP_STATUS_DE.values()))
    assert [ROADMAP_STATUS_DE[s] for s in english] == german, \
        f"the English and German roadmaps differ in their item statuses: {english} / {german}"
    done = roadmap_statuses(english_doc, DONE_STATUSES) + roadmap_statuses(german_doc, DONE_STATUSES)
    assert not done, "the roadmap lists done items; they belong in the changelog under their version"


def _bold_title(text: str) -> str:
    m = re.match(r"^\*\*(.+?)\*\*", text.strip())
    return " ".join(m.group(1).split()).rstrip(".:").lower() if m else ""


def roadmap_titles(rel: str) -> List[str]:
    """The bold title at the start of every roadmap table row and list item."""
    titles = []
    for item in roadmap_items(rel):
        body = item.strip("|").strip() if item.startswith("|") else item[2:] if item.startswith("- ") else ""
        title = _bold_title(body)
        if title:
            titles.append(title)
    return titles


def changelog_entry_texts(rel: str) -> List[str]:
    """Every top-level entry of a changelog with its wrapped lines, in order."""
    entries: List[str] = []
    for line in _prose_lines(_read(rel)):
        if line.startswith("#") or _REFERENCE_DEF.match(line):
            entries.append("")
            continue
        if re.match(r"^[-*] ", line):
            entries.append(line)
        elif entries and entries[-1] and line.strip():
            entries[-1] += " " + line.strip()
    return [e for e in entries if e]


def changelog_titles(rel: str) -> Set[str]:
    titles = set()
    for entry in changelog_entry_texts(rel):
        title = _bold_title(STAMP_PREFIX.sub("", entry[2:]))
        if title:
            titles.add(title)
    return titles


@pytest.mark.parametrize("roadmap,changelog", [("docs/ROADMAP.md", "CHANGELOG.md"),
                                               ("docs/ROADMAP_DE.md", "CHANGELOG_DE.md")])
def test_no_roadmap_item_is_also_a_changelog_entry(roadmap, changelog):
    """What is done is in the changelog and has left the roadmap; a row with the title of an entry is a leftover."""
    both = sorted(set(roadmap_titles(roadmap)) & changelog_titles(changelog))
    assert not both, f"{roadmap} still lists finished items (their entries are in {changelog}): {both}"


# --- (3b) Every changelog entry carries its stamp; the two languages agree (DECISIONS round 34) ----------------

def changelog_stamps(rel: str) -> List[Tuple[str, str, str, Optional[str]]]:
    """(version, type, entry's first line, stamp "YYYY-MM-DD HH:MM vX.Y.Z" or None) for every entry, in order."""
    out = []
    version = section = None
    for line in _prose_lines(_read(rel)):
        heading = _VERSION_HEADING.match(line)
        if heading:
            version, section = heading.group(1), None
        elif line.startswith("### "):
            section = line[4:].strip()
        elif version and section and re.match(r"^[-*] ", line):
            m = STAMP.match(line)
            out.append((version, section, line[:100],
                        f"{m.group(1)} {m.group(2)}:{m.group(3)} v{m.group(4)}" if m else None))
    return out


@pytest.mark.parametrize("changelog", ["CHANGELOG.md", "CHANGELOG_DE.md"])
def test_every_changelog_entry_starts_with_its_stamp(changelog):
    """`YYYY-MM-DD HH:MM UTC` · `vX.Y.Z`: the version is its section's, the time a real one not after the section's
    date."""
    dates = {v: d for v, d, _ in changelog_entries(changelog)}
    problems = []
    for version, section, first, stamp in changelog_stamps(changelog):
        if stamp is None:
            problems.append(f"[{version}] {section}: no stamp: {first}")
            continue
        day, clock, v = stamp.split(" ")
        if v != f"v{version}":
            problems.append(f"[{version}] {section}: stamp names {v}: {first}")
        hours, minutes = clock.split(":")
        if not (0 <= int(hours) < 24 and 0 <= int(minutes) < 60):
            problems.append(f"[{version}] {section}: no such time {clock}: {first}")
        if dates.get(version) and day > dates[version]:
            problems.append(f"[{version}] {section}: stamped {day}, after the section's date {dates[version]}: {first}")
    assert not problems, f"{changelog}:\n  " + "\n  ".join(problems[:40])


def test_the_german_changelog_entries_carry_the_stamps_of_their_english_twins():
    english = [(v, s, stamp) for v, s, _, stamp in changelog_stamps("CHANGELOG.md")]
    german = [(v, s, stamp) for v, s, _, stamp in changelog_stamps("CHANGELOG_DE.md")]
    translated = [(v, CHANGELOG_SECTIONS_DE.get(s, s), stamp) for v, s, stamp in english]
    assert len(translated) == len(german), "both changelogs have the same number of entries"
    differ = [f"{e} / {g}" for e, g in zip(translated, german) if e != g]
    assert not differ, "entries paired by order differ in version, type or stamp:\n  " + "\n  ".join(differ[:20])


@pytest.mark.parametrize("changelog", ["CHANGELOG.md", "CHANGELOG_DE.md"])
def test_every_changelog_version_has_its_link(changelog):
    """Keep a Changelog: linkable versions, one link reference per version heading."""
    text = _read(changelog)
    versions = [v for v, _, _ in changelog_entries(changelog)]
    refs = dict(re.findall(r"^\[([^\]]+)\]: (\S+)$", text, re.M))
    missing = [v for v in versions if not refs.get(v, "").startswith("https://")]
    assert not missing, f"{changelog}: versions without a link reference: {missing}"


# Entries whose first line was rewrapped or edited after the entry was written. Their stamp is the time the entry
# was first written (the first commit that added a line starting with the entry's opening words), never the time of
# the later rewrap: a stamp says when the entry was made, not when it was last touched.
FIRST_WRITTEN = {
    "**Two-phase confirmation and question text v2**": "2026-10-11 22:39",
    "**MCP clients see only the tools they may use**": "2026-10-11 18:55",
    "**`llm-cli doctor --fix --tier 2` no longer deletes": "2026-10-12 18:37",
    "**Encrypted packs for private sharing**": "2026-10-11 01:54",
    "**One file hash and one disk watchdog**": "2026-10-17 15:50",
    "**Refused MCP calls carry their reason code": "2026-10-12 09:20",
    "**Retrieval evaluation**": "2026-10-11 01:38",
    "**Credential check**": "2026-10-11 03:18",
    "Windows: the uninstaller (`scripts/Uninstall-LLM": "2026-10-11 16:55",
    "Dependencies: `anthropic` 1.13.0, `openai` 3.28.0 (was 2.54.0)": "2026-10-10 22:57",
}


def test_a_rewrapped_entry_keeps_the_stamp_of_its_first_writing():
    english = {first: stamp for _, _, first, stamp in changelog_stamps("CHANGELOG.md")}
    problems = []
    for start, when in FIRST_WRITTEN.items():
        hits = [(first, stamp) for first, stamp in english.items() if STAMP_PREFIX.sub("", first[2:]).startswith(start)]
        if len(hits) != 1:
            problems.append(f"{start}: {len(hits)} entries")
        elif not hits[0][1].startswith(when + " "):
            problems.append(f"{start}: stamped {hits[0][1]}, first written {when} UTC")
    assert not problems, "\n  ".join(problems)


def test_the_stamp_check_finds_a_missing_or_wrong_stamp():
    assert STAMP.match("- `2026-10-29 21:26 UTC` · `v3.1.0` **Title** text")
    for line in ["- **Title** text", "- `2026-10-29 21:26` · `v3.1.0` **Title**", "- `2026-10-29 UTC` · `v3.1` x"]:
        assert not STAMP.match(line), line


@pytest.mark.parametrize("changelog", sorted(ROADMAP_LINKS))
def test_changelog_header_links_twin_and_roadmap(changelog):
    head = links(header(_read(changelog)))
    assert ROADMAP_LINKS[changelog] in head, f"{changelog} must link to the roadmap ({ROADMAP_LINKS[changelog]})"
    assert _twin(changelog) in head, f"{changelog} must link to {_twin(changelog)}"
    for target in (ROADMAP_LINKS[changelog], _twin(changelog)):
        problem = link_problem(changelog, target)
        assert problem is None, problem


# --- (3a) The blueprint is three files and a short index (DECISIONS round 34) --------------------------------

@pytest.mark.parametrize("english,german", BLUEPRINT_FILES + [BLUEPRINT_INDEX])
def test_the_blueprint_files_and_their_twins_exist(english, german):
    missing = [rel for rel in (english, german) if not (REPO_ROOT / rel).is_file()]
    assert not missing, f"the blueprint is DESIGN, ROADMAP and CHANGELOG, each with its German twin: missing {missing}"


@pytest.mark.parametrize("index", BLUEPRINT_INDEX)
def test_the_blueprint_is_a_short_index_of_the_three_files(index):
    """docs/BLUEPRINT(_DE).md only says what each file holds and where the old sections went, so old links keep
    leading somewhere; it holds no design or roadmap text of its own."""
    text = _read(index)
    german = index.endswith(GERMAN_SUFFIX)
    targets = {link_destination(index, t) for t in links(text)}
    for english, de in BLUEPRINT_FILES:
        wanted = Path(os.path.normpath(REPO_ROOT / (de if german else english)))
        assert wanted in targets, f"{index} must link to {wanted.relative_to(REPO_ROOT).as_posix()}"
    assert len(text.splitlines()) <= BLUEPRINT_INDEX_MAX_LINES, f"{index} is an index, not a document"
    assert not [line for line in _prose_lines(text) if line.startswith("### ")], f"{index} has no subsections"
    # The old anchors that outside links use most stay valid (each row: old anchor -> new place).
    old = anchors(text)
    for anchor in ("roadmap", "key-decisions" if not german else "wichtige-entscheidungen",
                   "security-model" if not german else "sicherheitsmodell", "glossary" if not german else "glossar"):
        assert anchor in old, f"{index}: the old anchor #{anchor} must still lead somewhere"

@pytest.mark.parametrize("index", BLUEPRINT_INDEX)
def test_nothing_of_the_old_blueprint_is_lost(index):
    """Every heading the blueprint had before the split is still there: in the design (its "(unreleased)" mark
    gone) or in the roadmap, and its old anchor in the index leads to it."""
    german = index.endswith(GERMAN_SUFFIX)
    design = "docs/DESIGN_DE.md" if german else "docs/DESIGN.md"
    roadmap = "docs/ROADMAP_DE.md" if german else "docs/ROADMAP.md"
    old = OLD_BLUEPRINT_ANCHORS[index]
    road_start, road_end = old.index("roadmap"), old.index("geschichte" if german else "history")
    kept = anchors(_read(index))
    new_design = anchors(_read(design))
    targets = {t.partition("#")[0]: t for t in links(_read(index))}
    assert roadmap.rsplit("/", 1)[-1] in targets, f"{index} must link to {roadmap}"
    problems = []
    for n, anchor in enumerate(old):
        if anchor not in kept:
            problems.append(f"#{anchor} is no longer an anchor of {index}")
        if not road_start <= n < road_end:
            plain = re.sub(r"-(unreleased|unveröffentlicht)$", "", anchor)
            if plain not in new_design:
                problems.append(f"#{anchor}: {design} has no heading #{plain}")
    assert not problems, "\n  ".join(problems)


# --- (3c) The blueprint is scrubbed, and every component of the design has its decisions (DECISIONS round 35) ------

BLUEPRINT_TEXTS = [rel for pair in BLUEPRINT_FILES for rel in pair]
# Addresses that may appear: the documentation ranges (RFC 5737, RFC 3849) and the well-known special addresses; a
# range written with its prefix (10.0.0.0/8) names a class of addresses, not a host. Public resolvers are named
# where the text explains that the framework no longer contacts them.
DOC_NETWORKS = ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32"]
WELL_KNOWN_ADDRESSES = {"8.8.8.8", "1.1.1.1"}
# Account names in home paths that are no person's: defaults of CI runners and board images, the framework's own
# account, and placeholders.
DEFAULT_ACCOUNTS = {"runner", "pi", "dietpi", "ubuntu", "llmcf", "...", "<name>", "<user>"}
# E-mail addresses the project lists itself (none today).
PROJECT_EMAILS: Set[str] = set()
_V4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(/\d{1,2})?(?![\w.]*\d)")
_V6 = re.compile(r"(?<![\w:.])((?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4})(/\d{1,3})?(?![\w:])")
_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
_HOME = re.compile(r"/home/([^/\s`)\]]+)|[A-Za-z]:\\\\?Users\\\\?([^\\\s`)\]]+)")


def scrub_findings(text: str) -> List[str]:
    """What in a text looks like private data: a host address, an e-mail address, a person's home folder or a
    secret (the patterns of the repository's secret scan)."""
    import ipaddress

    from orchestrator.security.secret_patterns import find_secrets
    networks = [ipaddress.ip_network(n) for n in DOC_NETWORKS]
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for pattern in (_V4, _V6):
            for m in pattern.finditer(line):
                try:
                    address = ipaddress.ip_address(m.group(1))
                except ValueError:
                    continue
                if m.group(2) or str(address) in WELL_KNOWN_ADDRESSES or any(address in n for n in networks if
                                                                             n.version == address.version):
                    continue
                if address.is_loopback or address.is_unspecified or address.is_link_local or address.is_multicast:
                    continue
                found.append(f"{number}: address {m.group(1)}")
        found += [f"{number}: e-mail {m.group(0)}" for m in _MAIL.finditer(line) if m.group(0) not in PROJECT_EMAILS]
        for m in _HOME.finditer(line):
            account = m.group(1) or m.group(2)
            if account not in DEFAULT_ACCOUNTS and not account.startswith(("<", "$", "%")):
                found.append(f"{number}: home folder {m.group(0)}")
        found += [f"{number}: {kind}" for kind, _ in find_secrets(line)]
    return found


@pytest.mark.parametrize("rel", BLUEPRINT_TEXTS)
def test_the_blueprint_holds_no_private_data(rel):
    """The design, roadmap and changelog are public: no host address outside the documentation ranges, no e-mail
    address, no person's home folder and no token-like string."""
    found = scrub_findings(_read(rel))
    assert not found, f"{rel}:\n  " + "\n  ".join(found[:20])


def test_the_scrub_finds_private_data():
    text = ("board at 192.168.1.20\nmail me: someone@example.org\nsee /home/alice/models\n"
            "C:\\Users\\bob\\llmcf\nkey ghp_" + "a" * 36 + "\n")
    found = " ".join(scrub_findings(text))
    for part in ("192.168.1.20", "someone@example.org", "/home/alice", "bob"):
        assert part in found, part
    kept = "doc address 192.0.2.10, range 100.64.0.0/10, loopback 127.0.0.1, ::1, fe80::1, /home/<name>, /home/llmcf"
    assert scrub_findings(kept) == []


# The maintainer's own devices stay private (DECISIONS round 35): the blueprint names hardware classes and example
# boards, never which hardware the maintainer owns or how much memory or storage their board has.
MAINTAINER_DEVICE_WORDING = [
    re.compile(r"maintainer'?s (?:own )?(?:hardware|boards?|radxa|devices?|gpus?|computers?|machines?)", re.I),
    re.compile(r"(?:Hardware|Boards?|Geräte?|Rechner|Radxa) des Maintainers", re.I),
    re.compile(r"\b\d+\s*(?:-|–|to|or|bis|oder)\s*\d+\s*GB\b"),
    re.compile(r"\b\d+\s*GB eMMC\b", re.I),
]


@pytest.mark.parametrize("rel", ["docs/DESIGN.md", "docs/DESIGN_DE.md", "docs/ROADMAP.md", "docs/ROADMAP_DE.md"])
def test_the_blueprint_names_no_device_of_the_maintainer(rel):
    text = " ".join(_read(rel).split())
    found = [m.group(0) for pattern in MAINTAINER_DEVICE_WORDING for m in pattern.finditer(text)]
    assert not found, f"{rel}: wording that describes the maintainer's own devices: {found}"


COMPONENT_SECTIONS = {"docs/DESIGN.md":("components-and-decisions", "#### Decisions", "| What | How | Why |"),
                      "docs/DESIGN_DE.md": ("bausteine-und-entscheidungen", "#### Entscheidungen",
                                            "| Was | Wie | Warum |")}


def design_components(rel: str) -> List[Tuple[str, int]]:
    """(component, number of decision rows) for every component of the design's chapter of components."""
    section, block, head = COMPONENT_SECTIONS[rel]
    lines = section_lines(rel, section)
    out: List[Tuple[str, int]] = []
    in_block = False
    for line in lines:
        if line.startswith("### "):
            out.append((line[4:].strip(), -1))
            in_block = False
        elif out and line.strip() == block:
            out[-1] = (out[-1][0], 0)
            in_block = True
        elif in_block and line.startswith("| ") and line.strip() != head and not set(line.strip()) <= set("|-"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and all(cells):
                out[-1] = (out[-1][0], out[-1][1] + 1)
        elif in_block and line.strip() and not line.startswith("|"):
            in_block = False
    return out


@pytest.mark.parametrize("rel", sorted(COMPONENT_SECTIONS))
def test_every_design_component_has_its_decisions(rel):
    """Every component of the design has a decisions block whose rows say what, how and why."""
    components = design_components(rel)
    assert len(components) >= 10, f"{rel}: the chapter of components is missing or nearly empty"
    without = [name for name, rows in components if rows < 1]
    assert not without, f"{rel}: components without a decisions table (what, how, why): {without}"


def test_the_design_components_match_in_both_languages():
    english = design_components("docs/DESIGN.md")
    german = design_components("docs/DESIGN_DE.md")
    assert [rows for _, rows in english] == [rows for _, rows in german], \
        f"the two designs differ in their components or decisions: {english} / {german}"


def test_the_design_says_where_the_decisions_log_is():
    for rel, phrase in (("docs/DESIGN.md", "outside the repository"), ("docs/DESIGN_DE.md", "außerhalb des Repositorys")):
        assert phrase in " ".join(_read(rel).split()), f"{rel} must say that the decisions log lives outside the repo"


# --- (4) One version everywhere -----------------------------------------------------------------------------

@pytest.mark.parametrize("changelog", ["CHANGELOG.md", "CHANGELOG_DE.md"])
def test_newest_changelog_version_is_the_package_version(changelog):
    released = [v for v, d, _ in changelog_entries(changelog)]
    assert released and released[0] == __version__, \
        f"{changelog}: newest version {released[:1]} differs from orchestrator.__version__ {__version__}"


def test_document_version_lines_match_the_package_and_their_twin():
    """Each document starts with a line naming the version and the date it was last updated; both twins agree."""
    problems = []
    for rel in DOCUMENTS:
        if rel.endswith(GERMAN_SUFFIX) or Path(rel).name.startswith("CHANGELOG"):
            continue
        stamps = {}
        for name in (rel, _twin(rel)):
            if not (REPO_ROOT / name).is_file():
                continue  # reported by test_every_document_has_its_twin
            first = next((line for line in _read(name).splitlines() if line.strip()), "")
            version = re.search(r"\bVersion (\d+\.\d+\.\d+)\b", first)
            date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", first)
            if not (version and date):
                problems.append(f"{name}: first line must name the version and the date ({first[:60]!r})")
                continue
            stamps[name] = (version.group(1), date.group(1))
            if version.group(1) != __version__:
                problems.append(f"{name}: describes version {version.group(1)}, the package is {__version__}")
        if len(set(stamps.values())) > 1:
            problems.append(f"{rel} and {_twin(rel)} differ in version or date: {stamps}")
    assert not problems, "\n  ".join(["version lines:"] + problems)


# --- (5) No published framework image (DECISIONS round 5 h; design WP2) ---------------------------------------------

# Wording that promises or suggests a framework image to pull. There is none: people who want the framework in a
# container build the image on their own computer (docs/Container_Setup.md), and Ditto reaches a framework on
# another computer without Docker.
PUBLISHED_IMAGE_WORDING = [
    re.compile(r"headless[- ]docker[- ]image", re.I),
    re.compile(r"docker[- ]image ohne oberfläche", re.I),
    re.compile(r"image (for the cli and the mcp server|ohne oberfläche für (die )?cli)", re.I),
    re.compile(r"\b(docker|podman|nerdctl) (image )?pull\s+\S*(llm-framework|lcf-framework|smilez1985|orchestrator)",
               re.I),
    re.compile(r"\bghcr\.io/\S*(llm-framework|lcf-framework|orchestrator)\S*:(latest|v?\d)", re.I),
    re.compile(r"\bframework (running elsewhere|elsewhere)\b|\bframeworks?, das anderswo läuft", re.I),
]


def _current_text(rel: str) -> str:
    """The text a reader takes as current: for a changelog only the newest version; older versions record history."""
    text = _read(rel)
    if Path(rel).name.startswith("CHANGELOG"):
        versions = list(re.finditer(r"^## \[\d+\.\d+\.\d+\]", text, re.M))
        return text[:versions[1].start()] if len(versions) > 1 else text
    return text


def test_docs_never_promise_a_published_framework_image():
    found = []
    for rel in DOCUMENTS:
        for number, line in enumerate(_current_text(rel).splitlines(), 1):
            if any(pattern.search(line) for pattern in PUBLISHED_IMAGE_WORDING):
                found.append(f"{rel}:{number}: {line.strip()[:120]}")
    assert not found, ("documents suggest a published framework image or a framework 'elsewhere' (there is none: "
                       "the image is built locally, docs/Container_Setup.md):\n  " + "\n  ".join(found))


def test_the_published_image_check_finds_the_old_wording():
    old = ["Ditto for a framework running elsewhere (for example the headless Docker image)",
           "Ein gehärtetes Docker-Image ohne Oberfläche für CLI und MCP-Server",
           "docker pull ghcr.io/smilez1985/llm-framework:latest",
           "A hardened headless image for the CLI and the MCP server is being built",
           "MCP-Server eines Frameworks, das anderswo läuft"]
    for line in old:
        assert any(pattern.search(line) for pattern in PUBLISHED_IMAGE_WORDING), line
    for line in ["No framework image is published or pulled.", "Kein Framework-Image wird veröffentlicht oder geladen.",
                 "docker exec -i lcf-framework llm-cli mcp serve", "the Rockchip build image installs Rockchip's toolkits"]:
        assert not any(pattern.search(line) for pattern in PUBLISHED_IMAGE_WORDING), line


# --- (6) No cloud builds on the roadmap (DECISIONS round 8, Q-3) -------------------------------------------------

# Remote compiling was never planned: the roadmap names builds in the cloud (AWS, Azure) or on rented machines only
# in a "Not planned" line. If builds on another computer ever come, the framework builds them itself.
CLOUD_BUILD_WORDING = re.compile(
    r"(cloud|\bAWS\b|azure|rented|gemietet|GPU[- ]machine|GPU-Maschine)[^.|]{0,80}\bbuilds?\b"
    r"|\bbuilds?\b[^.|]{0,80}(cloud|\bAWS\b|azure|rented|gemietet|GPU[- ]machine|GPU-Maschine)", re.I)
NOT_PLANNED = re.compile(r"\bnot planned\b|\bnicht geplant\b", re.I)
# The roadmap's summary in the READMEs: the same rule holds there.
ROADMAP_SUMMARIES = {"README.md": "what-comes-next", "README_DE.md": "wie-es-weitergeht"}


def roadmap_items(rel: str, section: Optional[str] = None) -> List[str]:
    """The items of the roadmap (or of a section of another document, by its anchor): table rows, list items with
    their wrapped lines, and paragraphs."""
    items: List[str] = []
    for line in section_lines(rel, section):
        if line.startswith("## "):
            continue
        stripped = line.strip()
        if not stripped:
            items.append("")
        elif stripped.startswith(("|", "- ", "#")) or not items or not items[-1]:
            items.append(stripped)
        else:
            items[-1] += " " + stripped
    return [item for item in items if item]


def cloud_builds_planned(items: List[str]) -> List[str]:
    """The items with a sentence that names builds in the cloud or on rented machines without saying "not
    planned"."""
    return [item[:120] for item in items
            if any(CLOUD_BUILD_WORDING.search(sentence) and not NOT_PLANNED.search(sentence)
                   for sentence in re.split(r"(?<=[.!?])\s+", item))]


def test_the_roadmap_never_plans_cloud_builds():
    found = []
    for target in ROADMAP_LINKS.values():
        rel = target.partition("#")[0]
        found += [f"{rel}: {item}" for item in cloud_builds_planned(roadmap_items(rel))]
    for rel, section in ROADMAP_SUMMARIES.items():
        items = roadmap_items(rel, section)
        assert items, f"{rel} has no section #{section} with the roadmap's summary"
        found += [f"{rel}: {item}" for item in cloud_builds_planned(items)]
    assert not found, ("the roadmap plans builds in the cloud or on rented machines (struck in the eighth round, "
                       "Q-3):\n  " + "\n  ".join(found))


def test_the_cloud_build_check_finds_the_old_wording():
    old = ["- **Builds on a GPU machine you provide** (rented or your own): the framework offers the connections",
           "- **Builds auf einer GPU-Maschine, die du stellst** (gemietet oder eigene): Das Framework bietet",
           "| **Cloud builds** on AWS or Azure. | planned |"]
    assert cloud_builds_planned(old) == [line[:120] for line in old]
    kept = ["Not planned: builds in the cloud (AWS, Azure) or on rented machines.",
            "Nicht geplant: Builds in der Cloud (AWS, Azure) oder auf gemieteten Maschinen.",
            "- **Cloud platforms** (AWS, Google Cloud, Azure) through the vendors' own credential chains",
            "- **Planned:** recipes as target templates. Builds in the cloud are not planned."]
    assert cloud_builds_planned(kept) == []
    summary = ["- **Planned:** recipes as target templates, and builds in the cloud on AWS."]
    assert cloud_builds_planned(summary) == summary, "a README summary that plans them is found too"


# --- the README's section on the repository rename names the window by its mouse path -----------------------------

@pytest.mark.parametrize("rel, lang", [("README.md", "en"), ("README_DE.md", "de")])
def test_the_rename_section_names_the_mcp_window_by_its_mouse_path(rel, lang):
    """Merge seam (review w6-base-v): the rename (round 10b) was written on development before chain a0 translated
    the menu titles, so the German README sent the person to "Security > Verbindungen zu KI-Apps (MCP)...", a path
    the German GUI does not show (it says "Sicherheit"). The path is the one the GUI builds from its own labels."""
    from orchestrator.utils.next_steps import mouse_path
    text = " ".join((REPO_ROOT / rel).read_text(encoding="utf-8").split())
    start = text.index("AI apps connected over MCP" if lang == "en" else "Über MCP verbundene KI-Apps")
    item = text[start:text.index(" - **", start)]   # the list item, up to the next one
    assert f"**{mouse_path('act_tools_mcp_connections', lang)}**" in item, item


# --- public Markdown never names the working log or its round numbers ---------------------------------------------

# The maintainer's working log is not public: a document names a decision by what it decides, or points to its row in
# docs/DESIGN.md ("Components and decisions"), never by the log or a round number of it.
WORKING_LOG = re.compile(r"DECISIONS|ENTSCHEIDUNGEN|\b[Dd]ecisions?\s+(?:round|Runde)\b|\b[Rr]ounds?\s+[0-9]|"
                         r"\bRunden?\s+[0-9]|\bEntscheidung(?:en)?(?:\s+des\s+Maintainers,?)?\s+Runde\b")


def working_log_mentions(rel: str, text: str) -> List[str]:
    return [f"{rel}:{n}: {line.strip()[:120]}" for n, line in enumerate(text.split("\n"), 1)
            if WORKING_LOG.search(line)]


def test_no_public_document_names_the_working_log():
    found = []
    for rel in ALL_MARKDOWN:
        found += working_log_mentions(rel, _read(rel))
    assert not found, "public Markdown names the working log or a round of it:\n  " + "\n  ".join(found[:40])


def test_the_working_log_check_finds_every_form():
    for line in ("(DECISIONS round 25 Q1)", "(DECISIONS Runde 9 U2)", "decision round 25 Q2", "Round 29 (Q1).",
                 "round 5 (2026-10-12)", "Runde 24 (2026-10-23)", "Runden 18, 19 und 22", "Entscheidung Runde 25 Q2",
                 "### Ditto's context (DECISIONS rounds 29, 32 and 33)"):
        assert working_log_mentions("x.md", line), line
    for line in ("the decision of 2026-10-22 replaced it", "a round trip", "one more round of checks",
                 "Key decisions", "eine Runde später"):
        assert not working_log_mentions("x.md", line), line
