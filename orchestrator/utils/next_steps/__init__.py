#!/usr/bin/env python3
"""
Next steps: what a person (or an AI) can do about a message, as data instead of a hand-written command
(wave 6, design A; DECISIONS round 9 U2 and round 10 V1, V2, Q7).

A message says what is wrong (the facts). A separate *step* says what to do. The step names everything the three
audiences need, and one renderer turns it into the words each of them reads:

- the command line (audience "cli") gets ``Next: <what to do>: llmcf <command>``, the same commands the CLI shows;
- the GUI (audience "gui") gets the menu path in the person's language (``Ditto > Erweitert > Lokale Laufzeit... >
  llama-server bauen...``) plus the object an [Open] button triggers, never a command;
- an AI (audience "ai") gets the tool to call, or "only the person can do this: <English GUI path>" and the command
  line only where the person sits at one (channel "cli", or None for an external MCP app).

The catalogue is Qt-free and lives in this package: one *area module* per part of the framework (common, runtime,
boards, build, knowledge, deploy, signin, tools_mcp, ditto_setup). Each defines ``STEPS`` (a tuple of Step), their
English and German texts in ``TEXTS`` (``step.<id>``: what to do; ``step.<id>.what``: what its command does;
``step.<id>.why``: why it is needed), and ``MOUSE_LABELS`` (objectName -> (parent objectName or None, ui_text key):
where the object sits and what it reads; the chain up to the menu bar is the mouse path). The area modules are found
with pkgutil, so a new one needs no shared list. ``common`` also holds ``MENU_LAYOUT``, the one table of the main
window's menu structure (menus, submenus, entries by objectName), from which the menu entries' paths follow.

A text in an l10n module that must name a place writes ``{path:<objectName>}``; ui_text fills it with
:func:`mouse_path` in the text's language, so no text hand-writes "Tools > ..." again (tests/test_no_command_texts.py).

Carriers: :class:`NextStep` (a step id plus its values), ``ToolError(steps=...)``, the :class:`WithSteps` mixin of
the core exceptions (``exception.steps``), :class:`Line` (a result line that is a str and carries steps) and
``Finding.steps`` of Mojo. :func:`steps_of` reads the steps of any of them.

Allow-to-run (V1): :func:`proposal` turns a catalogue step and validated values into a :class:`CommandProposal`
(the exact argv, what it does, why it is needed, both localized); :func:`run_proposal` runs exactly that command as a
child process of the framework's own interpreter and streams its lines; deletions open the deletion dialog instead,
board/administrator/other-program steps are only shown, secrets go through stdin. Never free text from an AI.

Error presentation (Q7): :func:`error_report` gives a localized summary, the next steps and the English technical
detail of an error, for the error dialog of orchestrator/gui (chain f-words).

Form of address (Q1): German texts say "du". A module (this one or an area module) may add ``FORMAL = {"de": {key:
text}}``, the "Sie" form of each of its German texts that differs, with the same placeholders. The catalogue reads it
when the window's setting form_of_address is "sie" (orchestrator.utils.localization, only when the process loaded it
already); everywhere else the du text stays.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

# --- closed vocabularies ---------------------------------------------------------------------------------------------

# Why only the person can do a step (no AI tool does it).
PERSON_ONLY: Tuple[str, ...] = ("secret", "permission", "audit", "host_key", "deletion", "admin", "board", "licence",
                                "third_party")
# Steps whose command belongs to another place (the board, an administrator, another program): shown with their
# literal command and a [Copy] button, never run by the framework.
SHOWN_ONLY: Tuple[str, ...] = ("admin", "board", "third_party")
AUDIENCES: Tuple[str, ...] = ("cli", "gui", "ai")
# Who is asking (ToolContext.channel): Ditto with a question typed into this window's chat, Ditto working for the
# GUI without a chat turn (healing), `llmcf ditto ask`, or None for an MCP app.
CHANNELS: Tuple[Optional[str], ...] = ("gui-chat", "gui", "cli", None)
GUI_CHANNELS: Tuple[str, ...] = ("gui-chat", "gui")
PROGRAM = "llmcf"                       # the command's name in what the person reads
STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,47}$")   # = tools/tool_windows.STEP_ID_RE
OBJECT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
PATH_RE = re.compile(r"\{path:([A-Za-z_][A-Za-z0-9_]{0,79})\}")
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_VALUE = 200                          # characters of one argument value
MAX_LINE = 2000                          # characters of one streamed output line
KEEP_LINES = 400                         # output lines a ProposalResult keeps
OUTPUT_GRACE = 2.0                       # seconds a run still reads output after the command ended

# Texts of the renderer itself (the frames around a step); the steps' own texts live in the area modules.
TEXTS: Dict[str, Dict[str, str]] = {
    "en": {
        "steps.next": "Next",
        "steps.where.board": "on the board, in its terminal",
        "steps.where.admin": "for an administrator of this computer",
        "steps.where.third_party": "in {program}",
        "steps.planned": "(not in this version yet)",
        "steps.only_person": "Only the person can do this",
        "steps.allow": "Allow `{command}`? {what} {why}",
        "steps.allow.secret": "It asks for {secret} in a hidden field; the value never appears on a command line.",
        "steps.secret": "the secret value",
        "steps.value.mode.bare-metal": "directly on this computer",
        "steps.value.mode.docker": "in Docker",
        "steps.deletion": "This deletes something: the framework shows the full list first (\"No\" is the default).",
        "steps.shown_only": "The framework does not run this command; copy it and run it {where}.",
        "steps.result.done": "Done: the command finished without errors.",
        "steps.result.failed": "The command failed (exit code {code}). The output above shows why.",
        "steps.result.cancelled": "Cancelled: the command was stopped.",
        "steps.result.timeout": "Stopped: the command took longer than {seconds} seconds.",
        "steps.result.not_started": "The command could not be started: {reason}",
        "steps.declined": "Not run: you said no.",
        # Q7: the plain summary of an error by its code (the English detail stays under "Technical details")
        "error.title": "Something did not work",
        "error.unknown": "Something went wrong.",
        "error.failed": "The action failed.",
        "error.permission_denied": "Your settings do not allow this.",
        "error.declined": "Not done: it was declined.",
        "error.needs_person": "Only you can do this.",
        "error.not_supported": "This is not possible here.",
        "error.invalid_value": "A value is not valid.",
        "error.unknown_id": "This item does not exist (any more).",
        "error.missing_information": "Something is missing before this can run.",
        "error.insufficient_resources": "There is not enough room (disk space or memory).",
        "error.hardware_unavailable": "The device cannot be reached.",
        "error.limit_reached": "A limit was reached; try again later.",
        "error.waiting_for_approval": "This waits for your approval.",
        "error.network": "The network or a service did not answer.",
        "error.storage": "The key safe cannot be used.",
        "error.details": "Technical details",
    },
    "de": {
        "steps.next": "Nächster Schritt",
        "steps.where.board": "auf dem Board, in seinem Terminal",
        "steps.where.admin": "mit Administratorrechten auf diesem Computer",
        "steps.where.third_party": "in {program}",
        "steps.planned": "(in dieser Version noch nicht da)",
        "steps.only_person": "Das kannst nur du tun",
        "steps.allow": "`{command}` zulassen? {what} {why}",
        "steps.allow.secret": "Es fragt in einem verdeckten Feld nach {secret}; der Wert steht nie in einer "
                              "Befehlszeile.",
        "steps.secret": "den geheimen Wert",
        "steps.value.mode.bare-metal": "direkt auf diesem Computer",
        "steps.value.mode.docker": "in Docker",
        "steps.deletion": "Das löscht etwas: Das Framework zeigt zuerst die vollständige Liste (\"Nein\" ist "
                          "vorgewählt).",
        "steps.shown_only": "Das Framework führt diesen Befehl nicht aus; kopiere ihn und führe ihn {where} aus.",
        "steps.result.done": "Fertig: Der Befehl lief ohne Fehler durch.",
        "steps.result.failed": "Der Befehl ist fehlgeschlagen (Exit-Code {code}). Die Ausgabe oben zeigt, warum.",
        "steps.result.cancelled": "Abgebrochen: Der Befehl wurde gestoppt.",
        "steps.result.timeout": "Gestoppt: Der Befehl brauchte länger als {seconds} Sekunden.",
        "steps.result.not_started": "Der Befehl konnte nicht starten: {reason}",
        "steps.declined": "Nicht ausgeführt: Du hast Nein gesagt.",
        "error.title": "Etwas hat nicht geklappt",
        "error.unknown": "Etwas ist schiefgegangen.",
        "error.failed": "Die Aktion ist fehlgeschlagen.",
        "error.permission_denied": "Deine Einstellungen erlauben das nicht.",
        "error.declined": "Nicht gemacht: Es wurde abgelehnt.",
        "error.needs_person": "Das kannst nur du tun.",
        "error.not_supported": "Das ist hier nicht möglich.",
        "error.invalid_value": "Ein Wert ist ungültig.",
        "error.unknown_id": "Diesen Eintrag gibt es nicht (mehr).",
        "error.missing_information": "Es fehlt noch etwas, bevor das laufen kann.",
        "error.insufficient_resources": "Es ist nicht genug Platz da (Speicherplatz oder Arbeitsspeicher).",
        "error.hardware_unavailable": "Das Gerät ist nicht erreichbar.",
        "error.limit_reached": "Eine Grenze ist erreicht; versuche es später noch einmal.",
        "error.waiting_for_approval": "Das wartet auf deine Zustimmung.",
        "error.network": "Das Netzwerk oder ein Dienst hat nicht geantwortet.",
        "error.storage": "Der Schlüsseltresor kann nicht benutzt werden.",
        "error.details": "Technische Details",
    },
}

# The "Sie" form of the frames above that address the person (Q1); only the texts that differ.
FORMAL_LANGUAGES: Tuple[str, ...] = ("de",)   # = orchestrator.utils.localization.FORMAL_LANGUAGES
FORMAL: Dict[str, Dict[str, str]] = {
    "de": {
        "steps.only_person": "Das können nur Sie tun",
        "steps.shown_only": "Das Framework führt diesen Befehl nicht aus; kopieren Sie ihn und führen Sie ihn {where} "
                            "aus.",
        "steps.declined": "Nicht ausgeführt: Sie haben Nein gesagt.",
        "error.permission_denied": "Ihre Einstellungen erlauben das nicht.",
        "error.needs_person": "Das können nur Sie tun.",
        "error.limit_reached": "Eine Grenze ist erreicht; versuchen Sie es später noch einmal.",
        "error.waiting_for_approval": "Das wartet auf Ihre Zustimmung.",
    },
}


# --- the types -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Param:
    """One value of a step's argument template. `kind`:

    - "choice": one of `choices` (a tuple, or a function that returns the allowed values, e.g. the model tiers);
    - "label": a person's name for something (1-64 printable characters, no line break, never starting with "-");
      with `choices` (best practice: a function that returns the existing labels) it must also be one of them;
    - "id": an id the framework issued (letters, digits, ".", "_", "-"; 1-80 characters; never starting with "-");
    - "int": a whole number between `minimum` and `maximum`;
    - "secret": a password or key: never in argv, given to the command through stdin (`via` "stdin") or an
      environment variable (`via` "env:NAME"); the GUI asks for it in a masked field;
    - "path": a file or folder the person chooses in the window's own dialog: shown as a placeholder in the command
      line text, never a value of a proposal (a step with one is not runnable).

    `display`: for a "choice", the prefix of the catalogue texts that name each value in the person's words
    (``<display>.<value>``), used in the step's texts instead of the raw value.

    `private`: a value an AI never reads (a board's address or login account, study 6.2 D0): the AI's rendering shows
    the placeholder's name instead. A "path" or "secret" value never reaches an AI either.
    """
    kind: str
    choices: Union[Tuple[str, ...], Callable[[], Iterable[str]], None] = None
    minimum: int = 0
    maximum: int = 1 << 31
    via: str = "stdin"
    secret_key: str = ""          # the ui_text key that names the secret for the person ("the password")
    display: str = ""
    private: bool = False

    def allowed(self) -> Tuple[str, ...]:
        if callable(self.choices):
            try:
                return tuple(str(c) for c in self.choices())
            except Exception:   # noqa: BLE001 - a list that cannot be read allows nothing
                return ()
        return tuple(self.choices or ())


PARAM_KINDS = ("choice", "label", "id", "int", "secret", "path")
_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,79}$")


@dataclass(frozen=True)
class Step:
    """One entry of the catalogue.

    `cli`: a key of CLI_PATHS (orchestrator/gui/menus/*.py), e.g. "ditto runtime install"; `cli_args`: the argument
    template appended after the command's words, e.g. ("--mode", "{mode}", "--force", "--yes"), whose placeholders are
    `params`. `tool`: a KNOWN_TOOLS name the AI calls, or `person_only`: one of PERSON_ONLY. `window`: a WINDOW_IDS id
    the AI may show the person. `target`: the objectName the GUI's [Open] triggers (it equals CLI_PATHS[cli] when
    `cli` is set). `planned`: the target is still being built (the step renders its text without a path). `runnable`:
    the GUI may run the command after the person's yes (V1); only for commands that run without further questions
    with exactly these arguments. `literal`: the command a board, an administrator or another program runs (SHOWN_ONLY
    steps; shown with [Copy]); `program`: that program's name; `url`: an https page the person may open."""
    id: str
    cli: Optional[str] = None
    cli_args: Tuple[str, ...] = ()
    tool: Optional[str] = None
    person_only: Optional[str] = None
    window: Optional[str] = None
    target: Optional[str] = None
    planned: bool = False
    params: Mapping[str, Param] = field(default_factory=dict)
    runnable: bool = False
    literal: str = ""
    program: str = ""
    url: str = ""

    @property
    def text_key(self) -> str:
        return f"step.{self.id}"

    @property
    def shown_only(self) -> bool:
        return self.person_only in SHOWN_ONLY

    @property
    def deletes(self) -> bool:
        return self.person_only == "deletion"

    def command_words(self) -> Tuple[str, ...]:
        """The words of the command (the CLI_PATHS key up to its first option)."""
        words = []
        for word in (self.cli or "").split():
            if word.startswith("--"):
                break
            words.append(word)
        return tuple(words)

    def placeholders(self) -> Tuple[str, ...]:
        found: List[str] = []
        for token in self.cli_args:
            for name in PLACEHOLDER_RE.findall(token):
                if name not in found:
                    found.append(name)
        return tuple(found)


@dataclass(frozen=True)
class NextStep:
    """A step of the catalogue by its id, with the values its texts and its command template need (closed values:
    labels, ids, choices; never free text from an AI). `reason`: the name of one of the step's alternative reasons
    (``step.<id>.why.<reason>``), else its default reason."""
    id: str
    values: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.id, str) or not STEP_ID_RE.match(self.id):
            raise ValueError(f"not a step id: {self.id!r}")
        object.__setattr__(self, "values", dict(self.values or {}))


StepLike = Union[NextStep, str, Tuple[str, Mapping[str, Any]]]


def as_next_steps(items: Any) -> Tuple[NextStep, ...]:
    """Steps from a NextStep, a step id, an (id, values) pair or a list of them; anything else is ignored."""
    if items is None:
        return ()
    if isinstance(items, (NextStep, str)) or (isinstance(items, tuple) and len(items) == 2
                                               and isinstance(items[0], str) and isinstance(items[1], Mapping)):
        items = [items]
    found: List[NextStep] = []
    for item in items:
        try:
            if isinstance(item, NextStep):
                found.append(item)
            elif isinstance(item, str):
                found.append(NextStep(item))
            elif isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], Mapping):
                found.append(NextStep(item[0], item[1]))
        except ValueError:
            continue
    return tuple(found)


class Line(str):
    """A result line or note that carries steps: it is the text (str(line) == the text, so every caller that joins or
    prints lines keeps working) and `steps` says what to do about it."""
    steps: Tuple[NextStep, ...]

    def __new__(cls, text: str = "", steps: Any = ()) -> "Line":
        line = super().__new__(cls, text)
        line.steps = as_next_steps(steps)
        return line

    def __reduce__(self):
        return (Line, (str(self), self.steps))


class WithSteps:
    """Mixin of the core exceptions that once put a command into their text: `steps` says what to do, the message
    keeps only the facts. ``raise SomeError("...").with_steps("runtime.install")``."""
    steps: Tuple[NextStep, ...] = ()

    def with_steps(self, *steps: StepLike):
        self.steps = tuple(self.steps or ()) + as_next_steps(list(steps))
        return self


def steps_of(obj: Any) -> Tuple[NextStep, ...]:
    """The steps an exception, a Line, a Mojo finding, a ToolError or a list carries (() when none)."""
    if obj is None:
        return ()
    if isinstance(obj, (list, tuple)) and not isinstance(obj, str):
        return as_next_steps(obj)
    return as_next_steps(getattr(obj, "steps", ()) or ())


# --- the catalogue ---------------------------------------------------------------------------------------------------

class Catalogue:
    """The merged area modules: steps by id, texts per language, mouse labels and the menu layout."""

    def __init__(self, modules: Sequence[Any]):
        self.modules = list(modules)
        self.steps: Dict[str, Step] = {}
        self.owner: Dict[str, str] = {}
        self.texts: Dict[str, Dict[str, str]] = {lang: dict(table) for lang, table in TEXTS.items()}
        self.formal: Dict[str, Dict[str, str]] = {lang: {} for lang in FORMAL_LANGUAGES}
        self.labels: Dict[str, Tuple[Optional[str], Tuple[str, ...]]] = {}
        self.layout: Tuple[Any, ...] = ()
        self.problems: List[str] = []
        self._add_formal(FORMAL, TEXTS, "next_steps")
        for module in self.modules:
            self._add(module)
        for menu in self.layout:
            self._add_layout(menu, None)

    def _add(self, module: Any) -> None:
        name = module.__name__.rsplit(".", 1)[-1]
        for step in getattr(module, "STEPS", ()) or ():
            if step.id in self.steps:
                self.problems.append(f"step {step.id} is in {self.owner[step.id]} and {name}")
                continue
            self.steps[step.id] = step
            self.owner[step.id] = name
        for lang, table in (getattr(module, "TEXTS", None) or {}).items():
            target = self.texts.setdefault(lang, {})
            for key, text in table.items():
                if key in target:
                    self.problems.append(f"text {key} ({lang}) of {name} exists already")
                    continue
                target[key] = text
        self._add_formal(getattr(module, "FORMAL", None) or {}, getattr(module, "TEXTS", None) or {}, name)
        for obj, entry in (getattr(module, "MOUSE_LABELS", None) or {}).items():
            self._label(obj, entry, name)
        layout = getattr(module, "MENU_LAYOUT", None)
        if layout:
            if self.layout:
                self.problems.append(f"{name} has a second MENU_LAYOUT")
            else:
                self.layout = tuple(layout)

    def _add_formal(self, overlay: Mapping[str, Mapping[str, str]], texts: Mapping[str, Mapping[str, str]],
                    name: str) -> None:
        """The module's "Sie" texts: German only, each for a du text of the same module with the same placeholders,
        never given twice."""
        for lang, table in overlay.items():
            if lang not in FORMAL_LANGUAGES or not isinstance(table, Mapping):
                self.problems.append(f"FORMAL of {name}: {lang!r} has one form of address")
                continue
            for key, text in table.items():
                du = (texts.get(lang) or {}).get(key)
                if not isinstance(text, str) or du is None:
                    self.problems.append(f"FORMAL text {key} ({lang}) of {name} has no du text in {name}")
                elif _placeholders(text) != _placeholders(du):
                    self.problems.append(f"FORMAL text {key} ({lang}) of {name} has other placeholders than its du "
                                         f"text")
                elif key in self.formal[lang]:
                    self.problems.append(f"FORMAL text {key} ({lang}) of {name} exists already")
                else:
                    self.formal[lang][key] = text

    def _label(self, obj: str, entry: Any, owner: str) -> None:
        parent, keys = entry[0], entry[1]
        keys = (keys,) if isinstance(keys, str) else tuple(keys)
        if obj in self.labels and self.labels[obj] != (parent, keys):
            self.problems.append(f"mouse label of {obj} is given twice ({owner})")
            return
        self.labels[obj] = (parent, keys)

    def _add_layout(self, menu: Any, parent: Optional[str]) -> None:
        obj, key, entries = menu
        self._label(obj, (parent, key), "MENU_LAYOUT")
        for item in entries:
            if isinstance(item, tuple) and len(item) == 3 and isinstance(item[2], tuple):
                self._add_layout(item, obj)
            elif isinstance(item, tuple) and len(item) == 2:
                self._label(item[0], (obj, item[1]), "MENU_LAYOUT")

    def text(self, key: str, lang: str, form: Optional[str] = None) -> Optional[str]:
        """The text of `key` in `lang` (English when missing); German in the form of address `form` (default: the
        window's setting), so "sie" reads the FORMAL text where one exists."""
        if lang in self.formal and (form or form_of_address()) == "sie":
            text = self.formal[lang].get(key)
            if text is not None:
                return text
        return self.texts.get(lang, {}).get(key) or self.texts["en"].get(key)


def _placeholders(text: str) -> Tuple[Any, ...]:
    """The {placeholders} and {path:...} places of a text, to compare a FORMAL text with its du text."""
    return tuple(sorted(set(PLACEHOLDER_RE.findall(text)) | {f"path:{n}" for n in PATH_RE.findall(text)}))


def form_of_address() -> str:
    """The German form of address of the window ("du" or "sie"), read from orchestrator.utils.localization when this
    process loaded it (the catalogue never imports it itself, see _loaded_localization); "du" everywhere else."""
    localization = _loaded_localization()
    if localization is None:
        return "du"
    try:
        form = localization.form_of_address()
    except Exception:   # noqa: BLE001 - a text never fails because of its form
        return "du"
    return form if form in ("du", "sie") else "du"


_CATALOGUE: Optional[Catalogue] = None
_LOCK = threading.Lock()


def area_modules() -> List[Any]:
    """Every area module of this package (found with pkgutil: a new module needs no shared list)."""
    package = sys.modules[__name__]
    modules = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        modules.append(importlib.import_module(f"{__name__}.{info.name}"))
    return modules


def catalogue() -> Catalogue:
    """The catalogue, built once from the area modules."""
    global _CATALOGUE
    if _CATALOGUE is None:
        with _LOCK:
            if _CATALOGUE is None:
                _CATALOGUE = Catalogue(area_modules())
    return _CATALOGUE


def steps() -> Dict[str, Step]:
    return dict(catalogue().steps)


def get(step_id: str) -> Step:
    """The step `step_id` (KeyError for an id the catalogue does not know)."""
    try:
        return catalogue().steps[step_id]
    except KeyError:
        raise KeyError(f"unknown step {step_id!r}: add it to an area module of orchestrator/utils/next_steps") from None


def mouse_labels() -> Dict[str, Tuple[Optional[str], Tuple[str, ...]]]:
    return dict(catalogue().labels)


def menu_layout() -> Tuple[Any, ...]:
    return catalogue().layout


LOCALIZATION = "orchestrator.utils.localization"
_PLAIN_TEXTS: Optional[Dict[str, Dict[str, str]]] = None
_PLAIN_LOCK = threading.Lock()


def _loaded_localization() -> Any:
    """orchestrator.utils.localization when this process imported it already (the window does at its start), else
    None. The catalogue never imports it itself: it imports PySide6, and the first PySide6 import in a worker thread
    (a tool call of the MCP server renders its next steps there) makes Qt take that thread for the main thread, which
    breaks a window made later in the same process."""
    module = sys.modules.get(LOCALIZATION)
    return module if module is not None and hasattr(module, "merge_text_modules") else None


def _plain_texts() -> Dict[str, Dict[str, str]]:
    """The texts of the l10n modules (orchestrator/utils/l10n_*.py, plain tables that import nothing), read without
    Qt, for a process without the window: the same tables ui_text merges (TEXT_MODULES names every one of them)."""
    global _PLAIN_TEXTS
    with _PLAIN_LOCK:
        if _PLAIN_TEXTS is None:
            import orchestrator.utils as utils_package
            table: Dict[str, Dict[str, str]] = {"en": {}, "de": {}}
            for info in sorted(pkgutil.iter_modules(utils_package.__path__), key=lambda i: i.name):
                if not info.name.startswith("l10n_"):
                    continue
                texts = getattr(importlib.import_module(f"orchestrator.utils.{info.name}"), "TEXTS", None) or {}
                for lang, entries in texts.items():
                    if isinstance(entries, dict):
                        for key, text in entries.items():
                            table.setdefault(lang, {}).setdefault(key, text)
            _PLAIN_TEXTS = table
        return _PLAIN_TEXTS


def _ui_text(key: str, lang: str) -> str:
    """ui_text(key, lang) of orchestrator.utils.localization when the process loaded it, else the same text from the
    l10n tables without Qt (see _loaded_localization)."""
    localization = _loaded_localization()
    if localization is not None:
        return localization.ui_text(key, lang)
    table = _plain_texts()
    text = table.get(lang, {}).get(key) or table["en"].get(key, key)
    return fill_paths(text, lang) if "{path:" in text else text


def _lang(lang: Optional[str]) -> str:
    """`lang`, else the window's language (English in a process without the window)."""
    if lang:
        return lang
    localization = _loaded_localization()
    if localization is None:
        return "en"
    try:
        return localization.current_language()
    except Exception:   # noqa: BLE001
        return "en"


def own_text(key: str, lang: Optional[str] = None, **values: Any) -> str:
    """A text of the catalogue (the renderer's frames, the steps' texts) in `lang`, English when missing, with its
    {path:...} places and {placeholders} filled; the key itself for an unknown key."""
    lang = _lang(lang)
    text = catalogue().text(key, lang)
    if text is None:
        return key
    text = fill_paths(text, lang)
    if "{" in text:
        try:
            text = text.format_map(_Values({k: _plain(v) for k, v in values.items()}))
        except (IndexError, ValueError):
            pass
    return text


class _Values(dict):
    """The values of a text; a placeholder without a value shows its name in capitals (as in the command line)."""

    def __missing__(self, key: str) -> str:
        return key.upper()


def step_text(step: Union[Step, NextStep, str], lang: Optional[str] = None, part: str = "", **values: Any) -> str:
    """The text of a step ("" part: what to do; "what": what its command does; "why": why it is needed; or
    "why.<reason>"), with the step's values filled in; "" when the step has no such text."""
    step_id = step if isinstance(step, str) else step.id
    if isinstance(step, NextStep):
        values = {**step.values, **values}
    known = catalogue().steps.get(step_id)
    if known is not None:
        for name, param in known.params.items():
            if param.display and name in values:
                worded = own_text(f"{param.display}.{values[name]}", lang)
                if worded != f"{param.display}.{values[name]}":
                    values[name] = worded
    key = f"step.{step_id}" + (f".{part}" if part else "")
    text = own_text(key, lang, **values)
    return "" if text == key else text


def _plain(value: Any) -> str:
    """A value as one line (a value never adds a line break or a {placeholder} of its own)."""
    text = " ".join(str(value).split())
    return text.replace("{", "(").replace("}", ")")


# --- mouse paths -----------------------------------------------------------------------------------------------------

def label_chain(object_name: str) -> Tuple[str, ...]:
    """The ui_text keys from the menu bar (or the main window's tab) down to `object_name` (KeyError when the
    catalogue does not know where it sits)."""
    labels = catalogue().labels
    chain: List[str] = []
    seen = set()
    current: Optional[str] = object_name
    while current is not None:
        if current in seen or current not in labels:
            raise KeyError(f"no mouse label for {current!r}: add it to MOUSE_LABELS of an area module")
        seen.add(current)
        parent, keys = labels[current]
        chain.append(keys[0])
        current = parent
    return tuple(reversed(chain))


def clean_label(text: str) -> str:
    """A label as a path element: without its mnemonic "&" ("&&" stays one "&"), a leading symbol (an emoji) and a
    trailing colon."""
    text = (text or "").replace("&&", "\0").replace("&", "").replace("\0", "&").strip()
    while text and not (text[0].isalnum() or text[0] in "([\"'"):
        text = text[1:].lstrip()
    if text.endswith(":"):
        text = text[:-1].rstrip()
    return text


def mouse_path(object_name: str, lang: Optional[str] = None) -> str:
    """Where the person clicks to reach `object_name`, in `lang`: "Ditto > Advanced > Local Runtime... > Build
    llama-server..." (KeyError for an object the catalogue does not know)."""
    lang = _lang(lang)
    return " > ".join(clean_label(_ui_text(key, lang)) for key in label_chain(object_name))


def button_label(object_name: str, lang: Optional[str] = None) -> str:
    """Only the last element of the path: what the object itself reads (for a text shown inside its own window)."""
    return clean_label(_ui_text(label_chain(object_name)[-1], _lang(lang)))


def fill_paths(text: str, lang: Optional[str] = None) -> str:
    """`text` with every {path:<objectName>} replaced by that object's mouse path in `lang` (an object the catalogue
    does not know keeps its name: tests/test_no_command_texts.py makes sure there is none)."""
    if "{path:" not in (text or ""):
        return text

    def path(match):
        try:
            found = mouse_path(match.group(1), lang)
        except KeyError:
            found = match.group(1)
        return found.replace("{", "(").replace("}", ")")

    return PATH_RE.sub(path, text)


def protect_paths(text: str, lang: Optional[str] = None) -> str:
    """fill_paths for a text that is formatted afterwards: the filled-in paths cannot carry braces anyway, so this is
    the same; kept as the name ui_text uses."""
    return fill_paths(text, lang)


def window_of(object_name: Optional[str]) -> Optional[str]:
    """The objectName of what opens the window an object lives in (its menu action, the main window's tab, or the
    object itself when it is a menu entry); None for an unknown object."""
    labels = catalogue().labels
    current = object_name
    last = None
    while current is not None and current in labels:
        last = current
        parent = labels[current][0]
        if parent is None or parent.startswith("menu_"):
            return current
        current = parent
    return last


# --- rendering -------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Rendered:
    """One step as an audience reads it. `text`: the line; `target`: the objectName an [Open] button triggers (gui;
    None when there is none or it is planned); `command`: the framework command (cli and the allow-to-run proposal);
    `copy`: the literal command of a SHOWN_ONLY step for a [Copy] button; `url`: a page for an [Open page] button."""
    step_id: str
    text: str
    target: Optional[str] = None
    command: str = ""
    copy: str = ""
    url: str = ""
    planned: bool = False
    runnable: bool = False
    person_only: Optional[str] = None


def command_line(step: Step, values: Optional[Mapping[str, Any]] = None, *, check: bool = False) -> str:
    """The step's framework command as the person reads it ("llmcf ditto runtime install --yes"); placeholders
    without a value stay as their upper-case names. `check`: validate the values first (ProposalRefused)."""
    if not step.cli:
        return ""
    values = dict(values or {})
    if check:
        values = validate(step, values)
    argv = list(step.command_words())
    for token in step.cli_args:
        names = PLACEHOLDER_RE.findall(token)
        if names and any(name in step.params and step.params[name].kind == "secret" for name in names):
            continue
        argv.append(PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(1).upper())), token))
    return " ".join([PROGRAM, *(shlex.quote(a) if a else "''" for a in argv)])


def _here_matches(step: Step, here: Optional[str]) -> bool:
    if not here or not step.target:
        return False
    opener = here
    try:
        from orchestrator.tools.tool_windows import WINDOW_OPENERS
        opener = WINDOW_OPENERS.get(here, here)
    except Exception:   # noqa: BLE001
        pass
    return window_of(step.target) == opener and step.target != opener


def rendered(steps_: Any, audience: str, lang: Optional[str] = None, channel: Optional[str] = None,
             here: Optional[str] = None) -> List[Rendered]:
    """Each step as `audience` reads it (see the module text). `here`: the window the text is shown in (a WINDOW_IDS
    id or the objectName that opens it): a step whose target lives there names only its button. Unknown step ids are
    skipped (a row from an older or newer version)."""
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}")
    lang = "en" if audience == "ai" else (_lang(lang) if audience == "gui" else (lang or "en"))
    found: List[Rendered] = []
    for next_step in steps_of(steps_):
        try:
            step = get(next_step.id)
        except KeyError:
            continue
        found.append(_render_one(step, next_step, audience, lang, channel, here))
    return found


def shown_values(step: Step, values: Optional[Mapping[str, Any]], audience: str) -> Dict[str, str]:
    """The values of a step that `audience` may read, each checked like a proposal's value (one that does not fit its
    parameter is left out, so its placeholder's name shows: never free text): never a secret or a value the step has
    no parameter for; for an AI never a path or a private value (a board's address or login, D0)."""
    shown: Dict[str, str] = {}
    for name, value in (values or {}).items():
        param = step.params.get(name)
        if param is None or value is None or param.kind == "secret":
            continue
        if param.kind == "path" or param.private:
            if audience == "ai":
                continue
            if param.kind == "path":   # a file the person chose, named to the person only
                text = " ".join(str(value).split())
                if text and len(text) <= 4 * MAX_VALUE and "\0" not in text:
                    shown[name] = text
                continue
        try:
            shown[name] = _check_value(step.id, name, param, value)
        except ProposalRefused:
            continue
    return shown


def _render_one(step: Step, next_step: NextStep, audience: str, lang: str, channel: Optional[str],
                here: Optional[str]) -> Rendered:
    values = shown_values(step, next_step.values, audience)
    label = step_text(step.id, lang, **values) or step.id
    try:
        command = command_line(step, values) if step.cli else ""
    except Exception:   # noqa: BLE001 - a value that cannot be shown never breaks a message
        command = ""
    where = ""
    if step.shown_only:
        where = own_text(f"steps.where.{step.person_only}", lang, program=step.program or "")
    path = ""
    target = None
    if step.target and not step.planned:
        try:
            path = button_label(step.target, lang) if _here_matches(step, here) else mouse_path(step.target, lang)
            target = step.target
        except KeyError:
            path = ""
    common = dict(step_id=step.id, url=step.url, planned=step.planned, runnable=step.runnable,
                  person_only=step.person_only)
    if audience == "cli":
        if command:
            return Rendered(text=f"{own_text('steps.next', lang)}: {label}: {command}", command=command, **common)
        if step.shown_only and step.literal:
            return Rendered(text=f"{own_text('steps.next', lang)}: {label} ({where}): {step.literal}",
                            copy=step.literal, **common)
        gui = f" (GUI: {path})" if path else ""
        return Rendered(text=f"{own_text('steps.next', lang)}: {label}{gui}", **common)
    if audience == "gui":
        if step.shown_only and step.literal:
            return Rendered(text=f"{label} ({where}): {step.literal}", copy=step.literal, **common)
        if step.planned:
            return Rendered(text=f"{label} {own_text('steps.planned', lang)}", **common)
        text = f"{label}: {path}" if path else label
        return Rendered(text=text, target=target, command=command, **common)
    # ai: the tool to call; else what only the person does, where, and the command line where the person sits at one
    if step.tool:
        return Rendered(text=f"{label}: call {step.tool}.", command=command, **common)
    parts = [f"{own_text('steps.only_person', 'en')}: {label}"]
    if step.shown_only and step.literal:
        parts.append(f" ({where}: {step.literal})")
    elif path:
        parts.append(f" (in the framework window: {path})")
    if command and channel not in GUI_CHANNELS:
        parts.append(f"; command line: {command}")
    return Rendered(text="".join(parts) + ".", command=command, copy=step.literal if step.shown_only else "",
                    **common)


def render(steps_: Any, audience: str, lang: Optional[str] = None, channel: Optional[str] = None,
           here: Optional[str] = None) -> str:
    """The steps as one text for `audience`: one line per step (see rendered()). For an AI in the GUI's channels a
    person-only step ends with the reminder that the person uses the framework window, never a terminal."""
    lines = [r.text for r in rendered(steps_, audience, lang, channel, here)]
    if audience == "ai" and channel in GUI_CHANNELS and any(not get(s.id).tool for s in steps_of(steps_)
                                                            if s.id in catalogue().steps):
        lines.append("The person uses the framework window: name the place, never a command.")
    return ("\n" if audience != "ai" else " ").join(lines)


# --- allow-to-run proposals (V1) -------------------------------------------------------------------------------------

class ProposalRefused(ValueError):
    """A step or a value that cannot become a command proposal (unknown step, not runnable, a value outside its
    template)."""


@dataclass(frozen=True)
class CommandProposal:
    """A command the person may allow (V1): built only from a catalogue step and validated values, never from free AI
    text. `kind`: "run" (the person's yes runs exactly `argv`), "deletion" (opens the deletion dialog through `target`:
    the full list, "No" by default), "shown" (a board, administrator or other-program command: shown with [Copy],
    never run) or "open" (no command; [Open] shows the place). `argv`: the words after the program name; `command`:
    what the person reads; `what` and `why`: what it does and why it is needed now, in `lang`; `secrets`: the names of
    the values asked in a masked field (passed through stdin or the environment, never on the command line); `reason`:
    the name of the step's alternative reason `why` came from ("" for its default one). run_proposal builds the
    proposal again from the catalogue and runs it only when every field is the same: what runs is what was shown."""
    step_id: str
    kind: str
    argv: Tuple[str, ...]
    command: str
    what: str
    why: str
    lang: str
    values: Mapping[str, str] = field(default_factory=dict)
    secrets: Tuple[str, ...] = ()
    target: Optional[str] = None
    copy: str = ""
    reason: str = ""

    def question(self) -> str:
        """The person's question: "Allow `<command>`? <what it does>. <why>." in the proposal's language (or why it
        is only shown, or that it opens the deletion list)."""
        if self.kind == "run":
            text = own_text("steps.allow", self.lang, command=self.command, what=self.what, why=self.why).strip()
            if self.secrets:
                step = get(self.step_id)
                names = [_secret_name(step.params[s], self.lang) for s in self.secrets]
                text += " " + own_text("steps.allow.secret", self.lang, secret=", ".join(names))
            return text
        if self.kind == "deletion":
            return f"{self.what} {own_text('steps.deletion', self.lang)}".strip()
        if self.kind == "shown":
            where = own_text(f"steps.where.{get(self.step_id).person_only}", self.lang,
                             program=get(self.step_id).program or "")
            return f"{self.what} {own_text('steps.shown_only', self.lang, where=where)}".strip()
        return self.what


def _secret_name(param: Param, lang: str) -> str:
    if param.secret_key:
        text = _ui_text(param.secret_key, lang)
        if text != param.secret_key:
            return text
    return own_text("steps.secret", lang)


def checked_values(step: Union[Step, str], values: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    """Every given value of `step` checked by its parameter's kind (the closed values of a proposal of any kind: a
    choice, a label, an id, a number). ProposalRefused for a value the step has no parameter for, a secret (asked in
    the hidden field only, never a value), a path (chosen by the person in the window's own dialog) or a value that
    does not fit. A None value counts as not given. Returns the values as text."""
    step = get(step) if isinstance(step, str) else step
    checked: Dict[str, str] = {}
    for name, value in dict(values or {}).items():
        if value is None:
            continue
        param = step.params.get(name)
        if param is None:
            raise ProposalRefused(f"{step.id}: it has no value {name}")
        if param.kind == "secret":
            raise ProposalRefused(f"{step.id}: {name} is asked in a hidden field, never given as a value")
        checked[name] = _check_value(step.id, name, param, value)
    return checked


def validate(step: Union[Step, str], values: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    """The values of `step`'s template, checked: exactly its placeholders (secrets excluded), each of its kind and
    allowed range, one line, never starting with "-" (no option can be smuggled in), no NUL; any other value given
    is checked the same way (checked_values). ProposalRefused when one does not fit. Returns the template's values
    as text."""
    step = get(step) if isinstance(step, str) else step
    checked = checked_values(step, values)
    wanted = [name for name in step.placeholders() if not (name in step.params and step.params[name].kind == "secret")]
    for name in wanted:
        if name not in step.params:
            raise ProposalRefused(f"{step.id}: {{{name}}} has no parameter description")
        if name not in checked:
            raise ProposalRefused(f"{step.id}: the value {name} is missing")
    return {name: checked[name] for name in wanted}


def _check_value(step_id: str, name: str, param: Param, value: Any) -> str:
    if isinstance(value, bool):
        raise ProposalRefused(f"{step_id}: {name} is not a {param.kind}")
    text = str(value)
    if not text or len(text) > MAX_VALUE or "\0" in text or "\n" in text or "\r" in text:
        raise ProposalRefused(f"{step_id}: {name} is empty, too long or not one line")
    if text.startswith("-"):
        raise ProposalRefused(f"{step_id}: {name} may not start with '-'")
    if param.kind == "choice":
        if text not in param.allowed():
            raise ProposalRefused(f"{step_id}: {name} must be one of {', '.join(param.allowed()) or '(none)'}")
    elif param.kind == "label":
        collapsed = " ".join(text.split())
        if not (1 <= len(collapsed) <= 64 and collapsed.isprintable()):
            raise ProposalRefused(f"{step_id}: {name} must have 1 to 64 printable characters")
        if param.choices is not None and collapsed not in param.allowed():
            raise ProposalRefused(f"{step_id}: {name} names nothing that exists")
        text = collapsed
    elif param.kind == "id":
        if not _ID_RE.match(text):
            raise ProposalRefused(f"{step_id}: {name} is not an id")
    elif param.kind == "int":
        if not re.fullmatch(r"\d{1,10}", text) or not (param.minimum <= int(text) <= param.maximum):
            raise ProposalRefused(f"{step_id}: {name} must be a number from {param.minimum} to {param.maximum}")
    elif param.kind == "path":
        raise ProposalRefused(f"{step_id}: {name} is chosen by the person in the window's own dialog")
    else:
        raise ProposalRefused(f"{step_id}: {name} cannot be given as a value")
    return text


def _argv(step: Step, values: Mapping[str, str]) -> Tuple[str, ...]:
    argv = list(step.command_words())
    for token in step.cli_args:
        names = PLACEHOLDER_RE.findall(token)
        if any(n in step.params and step.params[n].kind == "secret" for n in names):
            continue
        argv.append(PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], token))
    return tuple(argv)


def proposal(step: Union[Step, NextStep, str], values: Optional[Mapping[str, Any]] = None,
             lang: Optional[str] = None, reason: str = "") -> CommandProposal:
    """The command proposal of a catalogue step (see CommandProposal). A NextStep brings its values and reason.
    Every value is a closed one, for every kind of proposal (checked_values: never free text, a secret or a path in
    what the person reads). ProposalRefused for an unknown step or a value that does not fit."""
    if isinstance(step, NextStep):
        values = {**step.values, **(values or {})}
        reason = reason or step.reason
        step = step.id
    if isinstance(step, str):
        try:
            step = get(step)
        except KeyError as e:
            raise ProposalRefused(str(e)) from None
    lang = _lang(lang)
    shown = checked_values(step, values)
    what = step_text(step.id, lang, "what", **shown) or step_text(step.id, lang, **shown)
    why = ""
    if reason:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", reason):
            raise ProposalRefused(f"{step.id}: not a reason name: {reason!r}")
        why = step_text(step.id, lang, f"why.{reason}", **shown)
        if not why:
            raise ProposalRefused(f"{step.id}: no reason {reason!r}")
    why = why or step_text(step.id, lang, "why", **shown)
    secrets = tuple(name for name, param in step.params.items() if param.kind == "secret")
    if step.shown_only:
        return CommandProposal(step.id, "shown", (), step.literal, what, why, lang, copy=step.literal,
                               target=step.target, reason=reason)
    if step.deletes:
        command = command_line(step, shown) if step.cli else ""
        return CommandProposal(step.id, "deletion", (), command, what, why, lang, values=shown, target=step.target,
                               reason=reason)
    if not (step.runnable and step.cli):
        return CommandProposal(step.id, "open", (), "", what, why, lang, values=shown,
                               target=None if step.planned else step.target, reason=reason)
    checked = validate(step, shown)
    argv = _argv(step, checked)
    return CommandProposal(step.id, "run", argv, command_line(step, checked), what, why, lang, values=shown,
                           secrets=secrets, target=step.target, reason=reason)


@dataclass(frozen=True)
class ProposalResult:
    """What a run of a proposal gave: `state` done, failed, cancelled, timeout or not_started; `exit_code` (None when
    it never ran or was stopped); the last output `lines` (redacted); `summary`: one plain sentence in the proposal's
    language."""
    state: str
    exit_code: Optional[int]
    lines: Tuple[str, ...]
    summary: str


def framework_root() -> Path:
    """The folder that holds the orchestrator package (the child process starts there)."""
    return Path(__file__).resolve().parents[3]


def framework_python() -> str:
    """The framework's own interpreter for a child process: sys.executable, or on Windows the console python.exe next
    to a pythonw.exe (whose child would have no output to stream)."""
    exe = sys.executable or "python"
    if os.name == "nt" and Path(exe).name.lower() == "pythonw.exe":
        console = Path(exe).with_name("python.exe")
        if console.exists():
            return str(console)
    return exe


def child_argv(prop: CommandProposal, config_path: Optional[str] = None, python: Optional[str] = None) -> List[str]:
    """The full argv of the child process: the framework's interpreter, ``-m orchestrator.cli -q``, the config file
    the GUI uses (if any) and the proposal's argv."""
    argv = [python or framework_python(), "-m", "orchestrator.cli", "-q"]
    if config_path:
        argv += ["--config", str(config_path)]
    return argv + list(prop.argv)


def _clean_line(line: str) -> str:
    from orchestrator.utils.helpers import redact_secrets
    line = line.rstrip("\r\n")
    line = "".join(ch for ch in line if ch == "\t" or ch.isprintable())
    return redact_secrets(line)[:MAX_LINE]


def run_proposal(prop: CommandProposal, on_line: Optional[Callable[[str], Any]] = None, *,
                 secrets: Optional[Mapping[str, str]] = None, config_path: Optional[str] = None,
                 cancel: Optional[threading.Event] = None, timeout: Optional[float] = None,
                 python: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> ProposalResult:
    """Runs exactly the proposal's command (after the person's yes) as a child process of the framework's own
    interpreter (the same code path as the CLI) and streams each output line (redacted, control characters removed)
    to `on_line` in the calling thread. Only a "run" proposal runs; it is built again from the catalogue and every
    field must match (argv, the command text, what and why the person read, the secrets): a changed proposal is
    refused, so what runs is what was shown. `secrets`: the masked values by name (stdin or the environment, never
    argv). `cancel`: stops the command; `timeout`: seconds before it is stopped. The run ends when the command ends
    (a process it left behind with the output still open is not waited for longer than OUTPUT_GRACE). Never raises
    for the command's own failure: the result says what happened. The lines are for the person (they may name this
    computer's folders): never hand them to an AI unfiltered."""
    lang = prop.lang or "en"
    if prop.kind != "run":
        raise ProposalRefused(f"{prop.step_id}: a {prop.kind} proposal is not run by the framework")
    try:
        again = proposal(prop.step_id, prop.values, lang, prop.reason)
    except ProposalRefused:
        raise ProposalRefused(f"{prop.step_id}: the proposal does not match the catalogue") from None
    if again != prop:
        raise ProposalRefused(f"{prop.step_id}: the proposal does not match the catalogue")
    step = get(prop.step_id)
    secrets = dict(secrets or {})
    missing = [name for name in prop.secrets if not secrets.get(name)]
    if missing:
        raise ProposalRefused(f"{prop.step_id}: the value {', '.join(missing)} was not given")
    child_env = dict(os.environ)
    child_env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
    child_env.update(dict(env or {}))
    root = str(framework_root())
    child_env["PYTHONPATH"] = root + (os.pathsep + child_env["PYTHONPATH"] if child_env.get("PYTHONPATH") else "")
    stdin_text = ""
    for name in prop.secrets:
        param = step.params[name]
        if param.via.startswith("env:"):
            child_env[param.via[4:]] = secrets[name]
        else:
            stdin_text += secrets[name] + "\n"
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(child_argv(prop, config_path, python), cwd=root, env=child_env,
                                stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", bufsize=1, **kwargs)
    except OSError as e:
        reason = _clean_line(str(e)) or type(e).__name__
        return ProposalResult("not_started", None, (), own_text("steps.result.not_started", lang, reason=reason))
    if stdin_text:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except OSError:
            pass
    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def read():
        try:
            for raw in proc.stdout:
                lines.put(raw)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read, name="proposal-output", daemon=True)
    reader.start()
    kept: List[str] = []
    began = time.monotonic()
    state = ""
    done_reading = False
    ended: Optional[float] = None
    while not done_reading:
        try:
            raw = lines.get(timeout=0.1)
        except queue.Empty:
            raw = ""
        if raw is None:
            done_reading = True
        elif raw:
            line = _clean_line(raw)
            kept.append(line)
            del kept[:-KEEP_LINES]
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:   # noqa: BLE001 - a listener never stops the command's output
                    pass
        if not state and cancel is not None and cancel.is_set():
            state = "cancelled"
        if not state and timeout is not None and time.monotonic() - began > timeout:
            state = "timeout"
        if state and proc.poll() is None:
            _stop(proc)
        if not done_reading and proc.poll() is not None:
            # The command ended; a process it started may still hold the output open (a server it left running):
            # read what is still coming for a short grace, then stop waiting for it.
            ended = ended if ended is not None else time.monotonic()
            if time.monotonic() - ended > OUTPUT_GRACE:
                break
    code = proc.wait()
    if done_reading:
        reader.join(timeout=5)
    if state == "cancelled":
        return ProposalResult("cancelled", None, tuple(kept), own_text("steps.result.cancelled", lang))
    if state == "timeout":
        return ProposalResult("timeout", None, tuple(kept),
                              own_text("steps.result.timeout", lang, seconds=int(timeout or 0)))
    if code == 0:
        return ProposalResult("done", 0, tuple(kept), own_text("steps.result.done", lang))
    return ProposalResult("failed", code, tuple(kept), own_text("steps.result.failed", lang, code=code))


def _stop(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    except OSError:
        pass


def audit_proposal(audit: Any, prop: CommandProposal, approved: bool, result: Optional[ProposalResult] = None,
                   actor: str = "gui") -> Optional[int]:
    """Records the person's decision on a proposal in the activity record (actor gui, decision person): the step, the
    command as shown (secrets are never part of it) and how it ended. None when nothing could be recorded."""
    if audit is None:
        return None
    if not approved:
        outcome = "not_confirmed"
    elif result is None:
        outcome = "approval_decided"
    else:
        outcome = {"done": "ok", "cancelled": "cancelled"}.get(result.state, "error")
    detail = result.summary if result is not None else ""
    extra = {"answer": "accepted" if approved else "declined", "confirm_channel": f"framework-{actor}"
             if actor in ("gui", "cli") else "framework"}
    if result is not None and result.exit_code not in (None, 0):
        extra["reason_code"] = "failed"
    try:
        return audit.record(actor, "command_proposal", "person", "person", outcome,
                            arguments={"step": prop.step_id, "command": prop.command}, approved=bool(approved),
                            detail=detail, extra=extra)
    except Exception:   # noqa: BLE001 - the decision stands; the GUI shows the result anyway
        return None


# --- error presentation (Q7) -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ErrorReport:
    """An error as the person reads it (Q7): `title` and `summary` in their language (what happened, plainly),
    `steps` (what to do: render with audience "gui" or show a StepsRow), and `detail`: the English technical detail
    (redacted) for a collapsed "Technical details" part with Copy. `code`: the error's reason code, "" when it has
    none."""
    title: str
    summary: str
    steps: Tuple[NextStep, ...]
    detail: str
    code: str
    details_label: str

    def dialog_kwargs(self) -> Dict[str, Any]:
        """The keywords of the error box of orchestrator/gui/error_dialog.py (chain f-words):
        ``show_error(parent, **report.dialog_kwargs())`` / ``ErrorDialog(parent, **report.dialog_kwargs())``."""
        return {"summary": self.summary, "steps": self.steps, "details": self.detail, "title": self.title}


def error_code(error: Any) -> str:
    code = getattr(error, "code", "") or ""
    return code if isinstance(code, str) else ""


def error_report(error: Any, lang: Optional[str] = None, summary: str = "") -> ErrorReport:
    """The ErrorReport of an exception (or any object with a text): the summary by its code ("error.<code>", or
    "error.<first part>" of a dotted code such as storage.locked), else the given `summary`, else a plain "Something
    went wrong"; the steps it carries; its English text as the detail."""
    from orchestrator.utils.helpers import redact_secrets
    lang = _lang(lang)
    code = error_code(error)
    text = ""
    for key in (f"error.{code}", f"error.{code.split('.', 1)[0]}") if code else ():
        if catalogue().text(key, lang) is not None:
            text = own_text(key, lang)
            break
    if summary:
        text = summary
    if not text:
        text = own_text("error.unknown", lang)
    detail = str(error) if isinstance(error, BaseException) else str(error or "")
    if isinstance(error, BaseException):
        detail = f"{type(error).__name__}: {detail}".strip()
    return ErrorReport(own_text("error.title", lang), text, steps_of(error), redact_secrets(detail)[:4000], code,
                       own_text("error.details", lang))


# --- checks the tests run --------------------------------------------------------------------------------------------

def catalogue_problems(known_tools: Optional[Iterable[str]] = None, cli_paths: Optional[Mapping[str, str]] = None,
                       window_ids: Optional[Iterable[str]] = None) -> List[str]:
    """What is wrong with the catalogue on its own (and against the tables given): ids, person-only reasons, cli
    keys and targets, tools, windows, templates, texts in en and de with the same placeholders, mouse labels that
    lead to the menu bar. An empty list when everything fits."""
    cat = catalogue()
    problems = list(cat.problems)
    tools = set(known_tools) if known_tools is not None else None
    windows = set(window_ids) if window_ids is not None else None
    for step_id, step in sorted(cat.steps.items()):
        where = f"step {step_id}"
        if not STEP_ID_RE.match(step_id):
            problems.append(f"{where}: not a step id")
        if step.person_only is not None and step.person_only not in PERSON_ONLY:
            problems.append(f"{where}: unknown person_only reason {step.person_only!r}")
        if not step.tool and not step.person_only:
            problems.append(f"{where}: needs a tool or a person_only reason")
        if step.tool and step.person_only:
            problems.append(f"{where}: has both a tool and a person_only reason")
        if tools is not None and step.tool and step.tool not in tools:
            problems.append(f"{where}: {step.tool} is no known tool")
        if windows is not None and step.window and step.window not in windows:
            problems.append(f"{where}: {step.window} is no known window")
        if cli_paths is not None and step.cli:
            if step.cli not in cli_paths:
                problems.append(f"{where}: {step.cli!r} is no CLI_PATHS key")
            elif step.target != cli_paths[step.cli]:
                problems.append(f"{where}: target {step.target} is not CLI_PATHS[{step.cli!r}] = {cli_paths[step.cli]}")
        for option in [w for w in (step.cli or "").split() if w.startswith("--")]:
            if option not in step.cli_args:
                problems.append(f"{where}: its template lacks {option} of {step.cli!r}")
        if step.shown_only and step.runnable:
            problems.append(f"{where}: a {step.person_only} step is never run")
        if step.deletes and step.runnable:
            problems.append(f"{where}: a deletion is never run blind (the deletion dialog lists it)")
        if step.person_only == "licence" and step.runnable:
            problems.append(f"{where}: a licence step is never run from a one-line question (the person reads the "
                            "licence in its window)")
        if step.runnable and not step.cli:
            problems.append(f"{where}: runnable without a command")
        if step.url and not step.url.startswith("https://"):
            problems.append(f"{where}: its page is not https")
        for name in step.placeholders():
            param = step.params.get(name)
            if param is None:
                problems.append(f"{where}: {{{name}}} has no parameter")
            elif param.kind not in PARAM_KINDS:
                problems.append(f"{where}: {name} has the unknown kind {param.kind!r}")
            elif param.kind == "choice" and not param.allowed():
                problems.append(f"{where}: {name} allows no value")
        if step.target and not step.planned:
            try:
                label_chain(step.target)
            except KeyError as e:
                problems.append(f"{where}: {e.args[0]}")
        keys = [step.text_key] + ([step.text_key + ".what"] if step.runnable else [])
        for key in keys:
            if key not in cat.texts["en"] or key not in cat.texts["de"]:
                problems.append(f"{where}: {key} needs an English and a German text")
    for lang in ("en", "de"):
        other = "de" if lang == "en" else "en"
        for key, text in cat.texts[lang].items():
            if key not in cat.texts[other]:
                problems.append(f"text {key} is in {lang} only")
                continue
            if sorted(set(PLACEHOLDER_RE.findall(text))) != sorted(set(PLACEHOLDER_RE.findall(cat.texts[other][key]))):
                if lang == "en":
                    problems.append(f"text {key}: en and de have different placeholders")
        for key in cat.texts[lang]:
            if key.startswith("step.") and not _text_belongs(key, cat.steps):
                problems.append(f"text {key} belongs to no step")
    for obj in sorted(cat.labels):
        try:
            label_chain(obj)
        except KeyError as e:
            problems.append(f"mouse label {obj}: {e.args[0]}")
    return problems


def _text_belongs(key: str, steps_: Mapping[str, Step]) -> bool:
    rest = key[len("step."):]
    for step_id in steps_:
        if rest == step_id or rest.startswith(step_id + "."):
            return True
    return False


__all__ = ["AUDIENCES", "CHANNELS", "CommandProposal", "ErrorReport", "FORMAL_LANGUAGES", "GUI_CHANNELS", "Line",
           "NextStep",
           "PERSON_ONLY", "PROGRAM", "Param", "ProposalRefused", "ProposalResult", "Rendered", "SHOWN_ONLY", "Step",
           "WithSteps", "as_next_steps", "audit_proposal", "button_label", "catalogue", "catalogue_problems",
           "checked_values", "child_argv", "clean_label", "command_line", "error_report", "fill_paths",
           "form_of_address", "framework_python", "get", "label_chain", "menu_layout", "mouse_labels", "mouse_path", "own_text",
           "proposal", "render", "rendered", "run_proposal", "shown_values", "step_text", "steps", "steps_of",
           "validate", "window_of"]
