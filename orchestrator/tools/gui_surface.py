#!/usr/bin/env python3
"""
The GUI surface: the forms of the open GUI that Ditto's hands reach (DECISIONS round 8, "HYBRID CLARIFIED" (2)).

The framework's tools are Ditto's hands, also in the GUI. The open forms register their fields here (the build form,
the target module wizard page by page, the AI settings dialog), and the GUI tools (orchestrator/tools/tools_gui.py)
read and fill them through this module. Nothing here imports Qt: the GUI side (orchestrator/gui/form_surface.py)
gives each field its getter and setter and runs every read and write on the GUI thread through the surface's
invoker.

The rules live here, so every form keeps them the same way:

- A secret field is never read (only "set" or "not set") and never filled by an AI.
- A field is filled only when the person is not editing it (it has no keyboard focus), when the person has not chosen
  its value (a value the person typed or picked is never overwritten; an AI may suggest it in the chat instead), when
  it is not read-only, and when its own check allows the new value (for example a setting that widens what an AI may
  do).
- A fill that would change another field the person chose or is editing (a target change resets the board) is
  rolled back as a whole and refused.
- Every filled field is marked "filled by Ditto" until the person edits it; one click undoes Ditto's changes (the
  fields the person changed since keep the person's value).
- Nothing is submitted: no field starts a build, saves a module or changes a setting. The form's own buttons and
  their questions stay with the person.
"""

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

FIELD_KINDS = ("text", "choice", "bool", "path", "model", "secret", "number", "code")
MAX_TEXT = 4000        # characters an AI may put into one text field
MAX_CODE = 20000       # characters of a code field (shell code, reviewed by the person on the summary page)
MAX_UNDO = 20          # fills a form remembers for its undo
_UNSET = object()


def same(a: Any, b: Any) -> bool:
    """Whether two field values are the same for the rules (text compared without surrounding blanks)."""
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return a == b


@dataclass
class GuiField:
    """One field of a form. `get` and `set` run on the GUI thread only. `read_only` returns why an AI may not fill
    the field now ("" when it may); `busy` whether the person is editing it now; `check(current, new)` why a value is
    refused ("" when it is fine); `choices` the values a closed list offers (`closed` False: only suggestions);
    `page` the wizard page the field is on; `hint` an English line for the AI about the values it takes."""
    id: str
    label_en: str
    label_de: str
    kind: str
    get: Callable[[], Any]
    set: Optional[Callable[[Any], None]] = None
    choices: Optional[Callable[[], Sequence[str]]] = None
    closed: bool = True
    busy: Callable[[], bool] = lambda: False
    read_only: Callable[[], str] = lambda: ""
    check: Optional[Callable[[Any, Any], str]] = None
    page: Optional[int] = None
    hint: str = ""
    # state, kept by the form
    person_set: bool = False
    ai_value: Any = _UNSET
    ai_ever: bool = False

    def __post_init__(self):
        if self.kind not in FIELD_KINDS:
            raise ValueError(f"unknown field kind {self.kind!r}")

    def label(self, lang: str = "en") -> str:
        return self.label_de if lang == "de" and self.label_de else self.label_en

    @property
    def secret(self) -> bool:
        return self.kind == "secret"

    def filled_by_ai(self) -> bool:
        """The field holds the value Ditto put there (the person has not changed it since)."""
        if self.ai_value is _UNSET:
            return False
        try:
            return same(self.get(), self.ai_value)
        except Exception:  # noqa: BLE001 - a widget that is gone holds nothing of Ditto's
            return False


@dataclass
class FillRecord:
    """One fill of a form, for its undo: every field's value before and after it (secrets never)."""
    number: int
    filled: Tuple[str, ...]
    before: Dict[str, Any]
    after: Dict[str, Any]


@dataclass
class FillOutcome:
    filled: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    refused: List[Tuple[str, str]] = field(default_factory=list)   # (field id, why)
    record: Optional[FillRecord] = None


class WizardPages:
    """What a wizard form tells the GUI tools about its pages. Every method runs on the GUI thread."""

    def index(self) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def titles(self, lang: str = "en") -> List[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def next(self) -> str:  # pragma: no cover - interface
        """Goes to the next page; returns why it did not ("" when it did)."""
        raise NotImplementedError

    def back(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def last(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class GuiForm:
    """The registered fields of one open form, with the fill and undo rules of this module."""

    def __init__(self, form_id: str, title_en: str, title_de: str, fields: Sequence[GuiField], *,
                 show: Optional[Callable[[], None]] = None, pages: Optional[WizardPages] = None,
                 is_open: Optional[Callable[[], bool]] = None):
        ids = [f.id for f in fields]
        if len(ids) != len(set(ids)):
            raise ValueError(f"form {form_id}: a field id is used twice")
        self.id = form_id
        self.title_en, self.title_de = title_en, title_de
        self.fields: List[GuiField] = list(fields)
        self.show = show or (lambda: None)
        self.pages = pages
        self._is_open = is_open or (lambda: True)
        self.undo_records: List[FillRecord] = []
        self._numbers = 0
        self.filling = False        # True while Ditto fills or the undo runs: the widgets' signals are not the person
        self.listeners: List[Callable[[str, Any], None]] = []
        # What the GUI tools may start in this form besides filling (e.g. "deep_ingest": open the Deep Ingest window,
        # where the person starts the crawl). GUI thread; never anything that submits the form.
        self.actions: Dict[str, Callable[[], None]] = {}

    # --- reading ----------------------------------------------------------------------------------------------------

    def title(self, lang: str = "en") -> str:
        return self.title_de if lang == "de" and self.title_de else self.title_en

    def is_open(self) -> bool:
        try:
            return bool(self._is_open())
        except Exception:  # noqa: BLE001 - a window that is gone is not open
            return False

    def field(self, field_id: str) -> Optional[GuiField]:
        return next((f for f in self.fields if f.id == field_id), None)

    def page_fields(self) -> List[GuiField]:
        """The fields of the current page (every field for a form without pages)."""
        if self.pages is None:
            return list(self.fields)
        index = self.pages.index()
        return [f for f in self.fields if f.page is None or f.page == index]

    def snapshot(self, only: Optional[Sequence[GuiField]] = None) -> List[Dict[str, Any]]:
        """Every field as plain data (GUI thread): a secret only as set or not set, never its value."""
        rows = []
        for f in (only if only is not None else self.fields):
            try:
                raw = f.get()
            except Exception:  # noqa: BLE001 - a widget that is gone
                raw = None
            reason = self._read_only_reason(f)
            rows.append({
                "id": f.id, "label_en": f.label_en, "label_de": f.label_de, "kind": f.kind,
                "value": bool(raw) if f.secret else raw,
                "read_only": bool(reason), "why_read_only": reason,
                "filled_by_ditto": f.filled_by_ai(), "chosen_by_person": f.person_set,
                "editing_now": self._busy(f), "page": f.page, "hint": f.hint,
                "choices": list(self._choices(f)) if f.choices is not None else [],
                "closed": f.closed,
            })
        return rows

    @staticmethod
    def _choices(f: GuiField) -> List[str]:
        try:
            return [str(c) for c in (f.choices() or [])]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _busy(f: GuiField) -> bool:
        try:
            return bool(f.busy())
        except Exception:  # noqa: BLE001 - unknown counts as busy: never written then
            return True

    @staticmethod
    def _read_only_reason(f: GuiField) -> str:
        if f.secret:
            return "a secret: only the person enters it; an AI never reads or fills it"
        if f.set is None:
            return "this field is read-only for an AI"
        try:
            return f.read_only() or ""
        except Exception:  # noqa: BLE001
            return "this field cannot be filled now"

    # --- the person's edits -----------------------------------------------------------------------------------------

    def person_edited(self, field_id: str) -> None:
        """The person typed, picked or clicked in the field: their value from now on. Ditto's mark goes away."""
        if self.filling:
            return
        f = self.field(field_id)
        if f is None:
            return
        f.person_set = True
        f.ai_value = _UNSET
        self._emit("person", f.id)

    def person_chose(self, field_ids: Sequence[str]) -> None:
        """Values the form set on the person's request (a recipe from the Community Hub): the person's choice."""
        for field_id in field_ids:
            f = self.field(field_id)
            if f is not None:
                f.person_set = True
                f.ai_value = _UNSET
        self._emit("person", "")

    def forget_person_choices(self) -> None:
        """The person cleared the form: no value is theirs any more."""
        for f in self.fields:
            f.person_set = False
            f.ai_value = _UNSET
        self._emit("person", "")

    # --- Ditto's fill -----------------------------------------------------------------------------------------------

    def fill(self, values: Dict[str, Any], *, current_page_only: bool = False,
             convert: Optional[Callable[[GuiField, Any], Any]] = None) -> FillOutcome:
        """Fills the given fields in the form's own order (GUI thread). Each field is checked against the rules of
        this module; a refused field never stops the others. A fill that changed a field the person chose or is
        editing (through the form's own logic, e.g. a target change that resets the board) is rolled back whole.
        `convert(field, value)` turns the AI's value into the widget's (a local file id into its path); it raises
        ValueError with the reason when it cannot."""
        outcome = FillOutcome()
        allowed_ids = {f.id for f in self.page_fields()} if current_page_only else None
        known = {f.id for f in self.fields}
        for key in values:
            if key not in known:
                outcome.refused.append((key, "no such field in this form"))
        protected = {f.id for f in self.fields if f.person_set or self._busy(f)}
        before = self._values()
        changed: List[str] = []
        self.filling = True
        try:
            for f in self.fields:
                if f.id not in values:
                    continue
                why = self._refusal(f, values[f.id], allowed_ids)
                if why:
                    outcome.refused.append((f.id, why))
                    continue
                value = values[f.id]
                if convert is not None:
                    try:
                        value = convert(f, value)
                    except ValueError as e:
                        outcome.refused.append((f.id, str(e)))
                        continue
                try:
                    current = f.get()
                except Exception:  # noqa: BLE001
                    outcome.refused.append((f.id, "the field cannot be read now"))
                    continue
                if f.check is not None:
                    try:
                        problem = f.check(current, value)
                    except Exception as e:  # noqa: BLE001 - a check that cannot run refuses
                        problem = f"the value cannot be checked ({type(e).__name__})"
                    if problem:
                        outcome.refused.append((f.id, problem))
                        continue
                if same(current, value):
                    outcome.unchanged.append(f.id)
                    continue
                try:
                    f.set(value)
                    taken = f.get()
                except Exception as e:  # noqa: BLE001
                    outcome.refused.append((f.id, f"the form did not take the value ({type(e).__name__})"))
                    continue
                if not same(taken, value):
                    self._restore({f.id: current})
                    outcome.refused.append((f.id, "the form did not take the value (it offers other values now)"))
                    continue
                changed.append(f.id)
            hit = [f for f in self.fields if f.id in protected and f.id not in changed
                   and not same(self._value_of(f), before.get(f.id))]
            if hit:
                self._restore(before)
                names = ", ".join(f.label_en for f in hit)
                outcome.refused = [(fid, why) for fid, why in outcome.refused] + [
                    (fid, f"it would change {names}, which the person chose or is editing; nothing was filled")
                    for fid in changed]
                outcome.unchanged = []
                return outcome
        finally:
            self.filling = False
        if not changed:
            return outcome
        after = self._values()
        for fid in changed:
            f = self.field(fid)
            f.ai_value = after.get(fid)
            f.ai_ever = True
        self._numbers += 1
        record = FillRecord(self._numbers, tuple(changed), before, after)
        self.undo_records.append(record)
        del self.undo_records[:-MAX_UNDO]
        outcome.filled, outcome.record = changed, record
        self._emit("filled", record)
        return outcome

    def _refusal(self, f: GuiField, value: Any, allowed_ids: Optional[set]) -> str:
        if f.secret:
            return "a secret is never filled by an AI; only the person enters it"
        reason = self._read_only_reason(f)
        if reason:
            return reason
        if allowed_ids is not None and f.id not in allowed_ids:
            return "this field is not on the wizard's current page"
        if self._busy(f):
            return "the person is editing this field now"
        if f.person_set:
            return ("the person chose this value; an AI never overwrites it (suggest your value in the chat "
                    "instead)")
        if f.kind == "bool" and not isinstance(value, bool):
            return "give true or false"
        if f.kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            return "give a number"
        if f.kind in ("text", "choice", "path", "model", "code") and not isinstance(value, str):
            return "give text"
        if isinstance(value, str) and len(value) > (MAX_CODE if f.kind == "code" else MAX_TEXT):
            return "the text is too long for this field"
        if f.kind == "choice" and f.closed and f.choices is not None:
            offered = self._choices(f)
            if offered and value not in offered:
                shown = ", ".join(offered[:30]) + (" ..." if len(offered) > 30 else "")
                return f"not one of the values this field offers now: {shown}"
        return ""

    def _value_of(self, f: GuiField) -> Any:
        try:
            return f.get()
        except Exception:  # noqa: BLE001
            return None

    def _values(self) -> Dict[str, Any]:
        return {f.id: self._value_of(f) for f in self.fields if not f.secret}

    def _restore(self, values: Dict[str, Any]) -> List[str]:
        """Puts the given values back in the form's order (dependent fields after the fields they depend on)."""
        restored = []
        for f in self.fields:
            if f.secret or f.set is None or f.id not in values:
                continue
            if same(self._value_of(f), values[f.id]):
                continue
            try:
                f.set(values[f.id])
                restored.append(f.id)
            except Exception:  # noqa: BLE001 - a value the form no longer offers stays as it is
                continue
        return restored

    # --- undo -------------------------------------------------------------------------------------------------------

    def undo_all(self) -> List[str]:
        """Undoes every fill of Ditto that is still recorded, newest first (GUI thread). A field the person changed
        since keeps the person's value. Returns the ids of the fields put back."""
        restored: List[str] = []
        kept = {f.id: self._value_of(f) for f in self.fields if not f.secret and (f.person_set or self._busy(f))}
        self.filling = True
        try:
            while self.undo_records:
                record = self.undo_records.pop()
                back = {}
                for fid, old in record.before.items():
                    f = self.field(fid)
                    if f is None or f.person_set:
                        continue
                    if same(self._value_of(f), record.after.get(fid)):
                        back[fid] = old
                restored += self._restore(back)
                for fid in record.filled:
                    f = self.field(fid)
                    if f is not None and not f.person_set:
                        f.ai_value = _UNSET
            # A value the person chose stays theirs: the form's own logic may have reset it with an earlier field
            self._restore(kept)
        finally:
            self.filling = False
        for f in self.fields:   # nothing of Ditto's is left marked after an undo
            if not f.person_set:
                f.ai_value = _UNSET
        restored = list(dict.fromkeys(restored))
        self._emit("undone", restored)
        return restored

    def marked(self) -> List[GuiField]:
        """The fields that hold Ditto's value now."""
        return [f for f in self.fields if f.filled_by_ai()]

    def ai_touched(self) -> bool:
        """Whether Ditto filled any field of this form while it was open (a module it helped with is AI-made)."""
        return any(f.ai_ever for f in self.fields)

    # --- listeners --------------------------------------------------------------------------------------------------

    def _emit(self, event: str, data: Any) -> None:
        for listener in list(self.listeners):
            try:
                listener(event, data)
            except Exception:  # noqa: BLE001 - a marker that cannot be drawn never stops a fill
                continue


class GuiSurface:
    """The open GUI of this process: its forms, the windows the GUI tools may show, and the invoker that runs a
    function on the GUI thread (`run(fn)`, with a time limit). Ditto runs in the GUI process; an external AI app would
    reach the same surface later through the host service (tools_gui.GUI_TOOL_CALLERS)."""

    def __init__(self, run: Callable[[Callable[[], Any], float], Any]):
        self._run = run
        self._lock = threading.Lock()
        self._forms: Dict[str, GuiForm] = {}
        self._windows: Dict[str, Tuple[str, str, Callable[[], None]]] = {}
        self.extra: Dict[str, Any] = {}   # the GUI's own helpers (its invoker), kept alive with the surface

    def run(self, fn: Callable[[], Any], timeout: float = 15.0) -> Any:
        return self._run(fn, timeout)

    # forms

    def register_form(self, form: GuiForm) -> GuiForm:
        with self._lock:
            self._forms[form.id] = form
        return form

    def unregister_form(self, form: GuiForm) -> None:
        with self._lock:
            if self._forms.get(form.id) is form:
                del self._forms[form.id]

    def forms(self) -> List[GuiForm]:
        with self._lock:
            forms = list(self._forms.values())
        return forms

    def form(self, form_id: str) -> Optional[GuiForm]:
        with self._lock:
            return self._forms.get(form_id)

    # windows

    def register_window(self, window_id: str, title_en: str, title_de: str, opener: Callable[[], None]) -> None:
        with self._lock:
            self._windows[window_id] = (title_en, title_de, opener)

    def windows(self) -> Dict[str, Tuple[str, str, Callable[[], None]]]:
        with self._lock:
            return dict(self._windows)


_current: Optional[GuiSurface] = None
_current_lock = threading.Lock()


def attach(surface: GuiSurface) -> GuiSurface:
    """The GUI of this process is open: the GUI tools work on `surface` from now on."""
    global _current
    with _current_lock:
        _current = surface
    return surface


def detach(surface: Optional[GuiSurface] = None) -> None:
    """The GUI closed (or `surface` is no longer the open one): the GUI tools are hidden again."""
    global _current
    with _current_lock:
        if surface is None or _current is surface:
            _current = None


def current() -> Optional[GuiSurface]:
    with _current_lock:
        return _current


def register_form(form: GuiForm) -> GuiForm:
    """Registers an open form with the open GUI (a form built without a GUI, e.g. in a test, stays unregistered)."""
    surface = current()
    if surface is not None:
        surface.register_form(form)
    return form


def unregister_form(form: GuiForm) -> None:
    surface = current()
    if surface is not None:
        surface.unregister_form(form)


__all__ = ["FIELD_KINDS", "FillOutcome", "FillRecord", "GuiField", "GuiForm", "GuiSurface", "MAX_CODE", "MAX_TEXT",
           "WizardPages", "attach", "current", "detach", "register_form", "same", "unregister_form"]
