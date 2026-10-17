"""The GUI never tells a person to type a command, and no text hand-writes a menu path (wave 6, design A.1 and A.9;
DECISIONS round 9 U2, round 10 V2).

Two guards:

- The texts of the person (every l10n module of TEXT_MODULES and the legacy table) carry no command ('llm-cli ...',
  'llmcf <command>', 'in a terminal', 'im Terminal', 'in einem Terminal') outside a short allowlist with its reason
  (another program's own command), and no hand-written menu path at all: a text names a place with
  {path:<objectName>}, which ui_text fills from the live labels in the person's language. Every such placeholder
  names an object the next-step catalogue knows.
- Every other string literal under orchestrator/ (docstrings excluded; orchestrator/cli.py, whose output is for the
  command line, and the catalogue, whose `cli` and `literal` fields are commands by design, excluded) is counted per
  file for the same patterns. tests/data/command_text_baseline.json holds today's counts: a file may only go down,
  never up, and a new file starts at zero. The chains that move their texts to next steps lower the counts; the
  integration chain tightens the baseline (chains never raise it).
"""

import ast
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List

import pytest

from conftest import REPO_ROOT

ORCHESTRATOR = REPO_ROOT / "orchestrator"
BASELINE = REPO_ROOT / "tests" / "data" / "command_text_baseline.json"
# Commands for the command line, case-sensitive ("LLMCF" as the product's name is no command).
COMMAND = re.compile(r"llm-cli |\bllmcf [a-z]|in a terminal|im Terminal|in einem Terminal")
# A hand-written menu path: a top-level menu title or a tab of the main window, then " > ".
MENU_PATH = re.compile(r"(?<![\w-])(?:File|Build|Boards|Ditto|Security|View|Tools|Help|Datei|Sicherheit|Ansicht|"
                       r"Extras|Hilfe|Werkzeuge|Builder|Deployment)\s+>\s+[A-Z\w]")
PATTERNS = {"command": COMMAND, "menu_path": MENU_PATH}
# A place written out in words instead of a path ("Save... in the Deployment tab"): the person's texts only (review
# w6-a0-v found one the " > " pattern missed).
WORDED_PLACE = re.compile(r"\bin the (?:Builder|Deployment) tab\b|\bim Tab „?(?:Builder|Deployment)\b|"
                          r"\bin the (?:File|Build|Boards|Ditto|Security|View|Tools|Help) menu\b|"
                          r"\bim Menü „?(?:Datei|Build|Boards|Ditto|Sicherheit|Ansicht|Extras|Hilfe)\b")
# Files whose literals are commands by design: the command line's own output, and the catalogue's command templates.
EXCLUDED = ("orchestrator/cli.py", "orchestrator/utils/next_steps/")
# l10n texts that keep another program's own command, with the reason.
ALLOWED_TEXTS = {
    "tmcp.mcp.claude_code": "another program: Claude Code registers the server with its own command (with Copy)",
}


def person_texts() -> Dict[str, Dict[str, str]]:
    from orchestrator.utils.localization import TRANSLATIONS
    return TRANSLATIONS


def formal_texts() -> Dict[str, Dict[str, str]]:
    """The "Sie" forms of the l10n modules (their FORMAL tables, Q1): what a person who chose Sie reads instead of
    the du text, so the same guard reads them."""
    from orchestrator.utils.localization import FORMAL_TEXTS
    return FORMAL_TEXTS


def person_problems() -> List[str]:
    """text_problems of every text a person reads in the GUI: the du tables and their formal overlay."""
    return text_problems(person_texts()) + [f"formal {p}" for p in text_problems(formal_texts())]


def text_problems(texts: Dict[str, Dict[str, str]]) -> List[str]:
    """Commands and hand-written menu paths in the person's texts; placeholders that name no known object."""
    from orchestrator.utils.next_steps import PATH_RE, mouse_labels
    known = mouse_labels()
    problems = []
    for lang, table in texts.items():
        for key, text in table.items():
            if not isinstance(text, str):
                continue
            if COMMAND.search(text) and key not in ALLOWED_TEXTS:
                problems.append(f"{lang} {key}: names a command ({COMMAND.search(text).group(0)!r})")
            if MENU_PATH.search(text):
                problems.append(f"{lang} {key}: a hand-written menu path ({MENU_PATH.search(text).group(0)!r}); "
                                "write {path:<objectName>}")
            elif WORDED_PLACE.search(text):
                problems.append(f"{lang} {key}: a place written out ({WORDED_PLACE.search(text).group(0)!r}); "
                                "write {path:<objectName>}")
            for name in PATH_RE.findall(text):
                if name not in known:
                    problems.append(f"{lang} {key}: {{path:{name}}} names no object of the next-step catalogue")
    return problems


def docstring_nodes(tree: ast.AST) -> set:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def literal_counts(path: Path) -> Dict[str, int]:
    """How many non-docstring string literals of `path` match each pattern."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = docstring_nodes(tree)
    counts = {name: 0 for name in PATTERNS}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            for name, pattern in PATTERNS.items():
                if pattern.search(node.value):
                    counts[name] += 1
    return counts


def code_files() -> Iterable[Path]:
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(EXCLUDED) or path.name.startswith("l10n_") or "__pycache__" in rel:
            continue
        yield path


def current_counts() -> Dict[str, Dict[str, int]]:
    found = {}
    for path in code_files():
        counts = literal_counts(path)
        if any(counts.values()):
            found[path.relative_to(REPO_ROOT).as_posix()] = counts
    return found


def baseline_problems(current: Dict[str, Dict[str, int]], baseline: Dict[str, Dict[str, int]]) -> List[str]:
    problems = []
    for rel, counts in sorted(current.items()):
        allowed = baseline.get(rel, {})
        for name, count in counts.items():
            if count > allowed.get(name, 0):
                problems.append(f"{rel}: {count} literals with a {name.replace('_', ' ')} (the baseline allows "
                                f"{allowed.get(name, 0)}); give the message a next step "
                                "(orchestrator/utils/next_steps) "
                                "instead of naming the command or the menu path")
    return problems


# --- the person's texts -----------------------------------------------------------------------------------------------

def test_the_persons_texts_name_no_command_and_no_hand_written_path():
    assert text_problems(person_texts()) == []
    assert formal_texts()["de"], "the l10n modules' Sie forms are read too"
    assert person_problems() == []


def test_the_text_guard_reads_the_formal_overlay(monkeypatch):
    """Merge seam (review w6-base-v): f-words' formal overlay (FORMAL of an l10n module) is not in the du tables, so
    a Sie text that named a command or hand-wrote a menu path passed both guards; the formal chain writes one for
    every German text that addresses the person."""
    from orchestrator.utils import localization
    planted = {"words.help.tip": "Was ist {term}? Öffnen Sie Extras > Wartung > Mojo... (F1)",
               "words.error.details_hint": "Führen Sie 'llmcf mojo' aus."}
    monkeypatch.setitem(localization.FORMAL_TEXTS, "de", {**localization.FORMAL_TEXTS["de"], **planted})
    flagged = {p.split(":")[0] for p in person_problems()}
    assert flagged == {"formal de words.help.tip", "formal de words.error.details_hint"}, person_problems()


def test_the_catalogues_own_texts_name_no_command_and_no_hand_written_path():
    """The step texts of the next-step area modules are the person's texts too (every chain adds its own there), so
    the same guard reads them (review w6-a0-v: they were outside both guards)."""
    from orchestrator.utils.next_steps import catalogue
    assert text_problems(catalogue().texts) == []
    # their "Sie" forms (FORMAL, wave-6 base) are what a person who chose Sie reads instead
    assert catalogue().formal["de"] and text_problems(catalogue().formal) == []


def test_every_path_placeholder_is_filled_in_both_languages(monkeypatch):
    from orchestrator.utils import localization
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.next_steps import PATH_RE
    texts = person_texts()
    keys = {key for lang in ("en", "de") for key, text in texts[lang].items() if PATH_RE.search(text)}
    assert len(keys) >= 30, "the hand-written paths became placeholders"
    keys |= {key for key, text in formal_texts()["de"].items() if PATH_RE.search(text)}
    for form in ("du", "sie"):   # German in either form of address (a Sie text has the same places, Q1)
        monkeypatch.setattr(localization, "_form_of_address", form)
        for key in keys:
            for lang in ("en", "de"):
                assert "{path:" not in ui_text(key, lang), (key, lang, form)


def test_the_text_guard_bites():
    texts = {"en": {"good": "Open {path:act_mojo_health}.", "path": "Open Tools > Mojo Health Check...",
                    "cmd": "Run 'llmcf mojo' first.", "terminal": "Do it in a terminal.",
                    "ghost": "See {path:act_not_there}.",
                    "tmcp.mcp.claude_code": "Claude Code (run once in a terminal)",
                    "product": "The creator of LLMCF is not liable.",
                    "worded": "Save it later with Save... in the Deployment tab.",
                    "tab_name": "Deployment tab"},
             "de": {"path": "Öffne Extras > Wartung > Mojo...", "terminal": "Mach das im Terminal.",
                    "worded": "Speichere es mit „Speichern…“ im Tab „Deployment“."}}
    problems = text_problems(texts)
    flagged = {p.split(":")[0] for p in problems}
    assert flagged == {"en path", "en cmd", "en terminal", "en ghost", "en worded", "de path", "de terminal",
                       "de worded"}, problems


# --- every other literal: a baseline that only goes down --------------------------------------------------------------

def test_no_file_names_more_commands_or_menu_paths_than_its_baseline():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline_problems(current_counts(), baseline) == []


def test_the_baseline_guard_bites(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text('"""A docstring that says llm-cli mojo is fine."""\n'
                      'A = "Run \'llm-cli mojo\' in a terminal."\n'
                      'B = f"Open Tools > Mojo Health Check... for {A}"\n', encoding="utf-8")
    counts = literal_counts(source)
    assert counts == {"command": 1, "menu_path": 1}
    assert baseline_problems({"x.py": counts}, {"x.py": {"command": 1, "menu_path": 1}}) == []
    assert len(baseline_problems({"x.py": counts}, {})) == 2


@pytest.mark.parametrize("rel", ["orchestrator/utils/l10n_chat.py", "orchestrator/utils/l10n_knowledge.py"])
def test_the_l10n_modules_are_checked_as_texts_not_as_code(rel):
    assert REPO_ROOT / rel not in set(code_files())
