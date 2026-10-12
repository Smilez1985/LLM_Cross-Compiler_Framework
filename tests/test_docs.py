"""Documentation rules of the repository, checked on every test run.

Documents are all Markdown files of the repository (README*, CONTRIBUTING*, CHANGELOG*, docs/, configs/,
targets/, data/ and wherever else one is added), except the commented EXCEPTIONS below.

- Every document exists twice: X.md in English (primary) and X_DE.md in German, and each links to its twin.
- Every relative link points to an existing file and, with an #anchor, to an existing heading
  (GitHub anchor rules).
- CHANGELOG.md and CHANGELOG_DE.md (Keep a Changelog 1.1.0) say what was done: the same versions in the same
  order with the same dates and the same sections. The roadmap (docs/BLUEPRINT.md#roadmap and its German twin)
  says what comes: the changelogs link to it, it names no version that is already released, both languages give
  every item the same status, and items marked "done" have entries under [Unreleased] in the changelog.
- The newest released version in both changelogs is orchestrator.__version__, and the version line at the top
  of every other document names that version.
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
    ("docs/BLUEPRINT.md", "docs/BLUEPRINT_DE.md"),  # design and roadmap
    ("docs/CLI_Usage.md", "docs/CLI_Usage_DE.md"),
    ("docs/Features.md", "docs/Features_DE.md"),
    ("docs/Module_Developer_Guide.md", "docs/Module_Developer_Guide_DE.md"),
    ("docs/Container_Setup.md", "docs/Container_Setup_DE.md"),  # the framework in a container you build locally
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

# The roadmap lists what comes; the changelogs link to it from their header.
ROADMAP_LINKS: Dict[str, str] = {
    "CHANGELOG.md": "docs/BLUEPRINT.md#roadmap",
    "CHANGELOG_DE.md": "docs/BLUEPRINT_DE.md#roadmap",
}

# The status in the last column of every roadmap table row, and its German name. "done" means built and listed
# under [Unreleased] in the changelog; at release such items leave the roadmap.
ROADMAP_STATUS_DE: Dict[str, str] = {
    "done": "fertig",
    "in progress": "in Arbeit",
    "planned": "geplant",
}

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


def test_changelog_versions_are_newest_first_with_dates():
    entries = changelog_entries("CHANGELOG.md")
    assert entries and entries[0][0] == "Unreleased" and entries[0][1] is None, \
        "the changelog starts with an undated [Unreleased] section"
    released = entries[1:]
    assert all(date for _, date, _ in released), "every released version has a date"
    versions = [_semver(v) for v, _, _ in released]
    assert versions == sorted(versions, reverse=True) and len(set(versions)) == len(versions), \
        "versions must be unique and newest first"
    dates = [date for _, date, _ in released]
    assert dates == sorted(dates, reverse=True), "release dates must not increase downwards"


def roadmap_versions(rel: str) -> List[str]:
    """Versions the roadmap section of a document names in its headings and table rows, in order."""
    versions, inside = [], False
    for line in _prose_lines(_read(rel)):
        if line.startswith("## "):
            inside = github_slug(line[3:]) == "roadmap"
            continue
        if inside:
            m = re.match(r"^#{3,6} (\d+\.\d+\.\d+)\b", line) or re.match(r"^\|\s*(\d+\.\d+\.\d+)\s*\|", line)
            if m:
                versions.append(m.group(1))
    return versions


def test_roadmap_lists_only_what_comes():
    """Finished versions leave the roadmap at release; it names only versions newer than the newest release."""
    newest = next(v for v, _, _ in changelog_entries("CHANGELOG.md") if v != "Unreleased")
    found = {}
    for target in ROADMAP_LINKS.values():
        rel = target.partition("#")[0]
        assert (REPO_ROOT / rel).is_file(), f"{rel}, the document with the roadmap, is missing"
        assert "roadmap" in anchors(_read(rel)), f"{rel} has no '## Roadmap' section"
        found[rel] = roadmap_versions(rel)
        released = [v for v in found[rel] if _semver(v) <= _semver(newest)]
        assert not released, (f"{rel}: the roadmap still lists released version(s) {sorted(set(released))}; "
                              f"they belong in the changelog (newest release: {newest})")
    english, german = found.values()
    assert english == german, f"the English and German roadmaps name different versions: {found}"


def roadmap_statuses(rel: str, known: Set[str]) -> List[str]:
    """The status (last table cell) of every roadmap table row whose last cell is a known status, in order."""
    statuses, inside = [], False
    for line in _prose_lines(_read(rel)):
        if line.startswith("## "):
            inside = github_slug(line[3:]) == "roadmap"
            continue
        if inside and line.lstrip().startswith("|"):
            last = line.strip().strip("|").split("|")[-1].strip()
            if last in known:
                statuses.append(last)
    return statuses


def test_roadmap_statuses_match_and_done_items_are_unreleased():
    """Both roadmaps give every item the same status; "done" items must be in the changelog under [Unreleased]."""
    english_doc, german_doc = (target.partition("#")[0] for target in ROADMAP_LINKS.values())
    for rel in (english_doc, german_doc):
        assert (REPO_ROOT / rel).is_file(), f"{rel}, the document with the roadmap, is missing"
    english = roadmap_statuses(english_doc, set(ROADMAP_STATUS_DE))
    german = roadmap_statuses(german_doc, set(ROADMAP_STATUS_DE.values()))
    assert [ROADMAP_STATUS_DE[s] for s in english] == german, \
        f"the English and German roadmaps differ in their item statuses: {english} / {german}"
    if "done" in english:
        version, _, sections = changelog_entries("CHANGELOG.md")[0]
        assert version == "Unreleased" and sum(count for _, count in sections) > 0, \
            "the roadmap marks items as done, but the changelog has no [Unreleased] entries for them"


@pytest.mark.parametrize("changelog", sorted(ROADMAP_LINKS))
def test_changelog_header_links_twin_and_roadmap(changelog):
    head = links(header(_read(changelog)))
    assert ROADMAP_LINKS[changelog] in head, f"{changelog} must link to the roadmap ({ROADMAP_LINKS[changelog]})"
    assert _twin(changelog) in head, f"{changelog} must link to {_twin(changelog)}"
    for target in (ROADMAP_LINKS[changelog], _twin(changelog)):
        problem = link_problem(changelog, target)
        assert problem is None, problem


# --- (4) One version everywhere -----------------------------------------------------------------------------

@pytest.mark.parametrize("changelog", ["CHANGELOG.md", "CHANGELOG_DE.md"])
def test_newest_changelog_version_is_the_package_version(changelog):
    released = [v for v, d, _ in changelog_entries(changelog) if v != "Unreleased"]
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
    """The text a reader takes as current: for a changelog only [Unreleased]; released versions record history."""
    text = _read(rel)
    if Path(rel).name.startswith("CHANGELOG"):
        released = re.search(r"^## \[\d+\.\d+\.\d+\]", text, re.M)
        return text[:released.start()] if released else text
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
