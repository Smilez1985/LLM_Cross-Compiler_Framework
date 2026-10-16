#!/usr/bin/env python3
"""
The Qt side of the GUI surface (orchestrator/tools/gui_surface.py): the forms of the window bind their widgets here,
so the GUI tool (orchestrator/tools/tools_gui.py) can read and fill them visibly, for Ditto and for AI apps alike.

- `GuiInvoker` runs a function on the GUI thread for a tool call that runs in Ditto's worker thread or in a bridge
  thread, with a time limit; a call that timed out before it started never runs late.
- `FormBinding` registers a form's fields (id, label in English and German, kind, secret, getter, setter, read-only)
  and tracks the person's edits: a value the person typed, picked or clicked is theirs and never overwritten by an AI.
- Every field an AI filled is marked (a coloured frame and the accessible description) and listed by name in the
  form's banner with who filled it (never colour alone), with one undo button, until the person edits the field.
  Ditto's fields get Ditto's purple; an AI app's fields get another colour and its name in words: its MCP token name,
  or the name it gives itself in quotes, marked as its own claim. An app is never shown in Ditto's look, whatever it
  calls itself.
- `open_gui(window)` attaches the surface when the main window opens, registers the windows show_window may open and
  starts the per-user bridge for AI apps (orchestrator/tools/gui_bridge.py); `close_gui()` stops the bridge and
  detaches the surface again.

Every text here is plain text (design 5.2 R1, SEC-M2): labels through plain_label, button labels through button_text.
"""

import threading
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QAbstractButton, QComboBox, QFrame, QHBoxLayout, QLineEdit, QPushButton, QWidget

from orchestrator.gui.plain import button_text, plain_label
from orchestrator.tools import gui_surface
from orchestrator.tools.gui_surface import Filler, GuiField, GuiForm, GuiSurface, WizardPages
from orchestrator.utils.localization import current_language, ui_text

MARK_COLOUR = "#8E24AA"   # Ditto's purple; the accessible description and the banner say it in words
APP_MARK_COLOUR = "#00838F"   # an AI app's fields: never Ditto's colour, whatever the app calls itself
_STYLE_KEY = "_ditto_style"
_DESC_KEY = "_ditto_desc"
_WHO_KEY = "_ai_mark_by"


class GuiInvoker(QObject):
    """Runs functions on the thread it lives in (the GUI thread). `call(fn, timeout)` from another thread waits for
    the result; when the time runs out before the function started, it never starts (nothing changes late)."""
    _queued = Signal(object)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._queued.connect(self._execute, Qt.QueuedConnection)

    @staticmethod
    def _execute(job: Callable[[], None]) -> None:
        job()

    def call(self, fn: Callable[[], Any], timeout: float = 15.0) -> Any:
        if QThread.currentThread() is self.thread():
            return fn()
        lock, done = threading.Lock(), threading.Event()
        state: Dict[str, Any] = {"started": False, "abandoned": False}

        def job() -> None:
            with lock:
                if state["abandoned"]:
                    return
                state["started"] = True
            try:
                state["value"] = fn()
            except BaseException as e:  # noqa: BLE001 - handed to the waiting thread
                state["error"] = e
            finally:
                done.set()

        self._queued.emit(job)
        if not done.wait(timeout):
            with lock:
                if not state["started"]:
                    state["abandoned"] = True
                    raise TimeoutError("the window did not answer in time")
            done.wait()   # it runs now: its result belongs to this call
        if "error" in state:
            raise state["error"]
        return state.get("value")


class FormBinding:
    """Collects the fields of one form and binds them to their widgets (person edits, markers, banner)."""

    def __init__(self, form_id: str, title_key: str):
        self.form_id = form_id
        self.title_key = title_key
        self.fields: List[GuiField] = []
        self.widgets: Dict[str, List[QWidget]] = {}
        self.form: Optional[GuiForm] = None
        self.banners: List["FillBanner"] = []
        self._pending_edits: List[Callable[[GuiForm], None]] = []

    # --- fields ------------------------------------------------------------------------------------------------

    def add(self, field_id: str, label_key: str, kind: str, get: Callable[[], Any],
            set: Optional[Callable[[Any], None]], widgets: Sequence[QWidget] = (), **options: Any) -> GuiField:
        """A field with its own getter and setter; `widgets` are marked when Ditto fills it."""
        busy = options.pop("busy", None) or (lambda: any(_has_focus(w) for w in widgets))
        f = GuiField(field_id, ui_text(label_key, "en"), ui_text(label_key, "de"), kind, get, set, busy=busy,
                     **options)
        self.fields.append(f)
        self.widgets[field_id] = list(widgets)
        return f

    def combo(self, field_id: str, label_key: str, combo: QComboBox, *, kind: str = "choice", by_data: bool = True,
              **options: Any) -> GuiField:
        """A combo box: its item data (or text) as the value; an editable combo also takes free text."""
        def get() -> str:
            data = combo.currentData() if by_data else None
            if data is not None:
                return str(data)
            if combo.isEditable():
                return combo.currentText().strip()
            return "" if by_data else combo.currentText().strip()

        def set_(value: Any) -> None:
            text = str(value)
            index = combo.findData(text) if by_data else -1
            if index < 0:
                index = combo.findText(text)
            if index >= 0:
                combo.setCurrentIndex(index)
            elif combo.isEditable():
                combo.setEditText(text)
            else:
                raise ValueError("not offered")

        def choices() -> List[str]:
            found = []
            for i in range(combo.count()):
                data = combo.itemData(i) if by_data else None
                if data is not None:
                    found.append(str(data))
                elif not by_data or combo.isEditable():
                    text = combo.itemText(i)
                    if text and text != "(none)":
                        found.append(text)
            return found

        options.setdefault("choices", choices)
        if "closed" not in options:
            options["closed"] = not combo.isEditable()
        f = self.add(field_id, label_key, kind, options.pop("get", get), options.pop("set", set_), [combo],
                     busy=lambda: _has_focus(combo) or combo.view().isVisible(), **options)
        self._pending_edits.append(lambda form: _watch_combo(form, field_id, combo))
        return f

    def line(self, field_id: str, label_key: str, edit: QLineEdit, *, kind: str = "text", **options: Any) -> GuiField:
        f = self.add(field_id, label_key, kind, options.pop("get", lambda: edit.text()),
                     options.pop("set", lambda value: edit.setText(str(value))), [edit], **options)
        self._pending_edits.append(lambda form: edit.textEdited.connect(lambda _t: form.person_edited(field_id)))
        return f

    def check(self, field_id: str, label_key: str, box: QAbstractButton, **options: Any) -> GuiField:
        f = self.add(field_id, label_key, "bool", options.pop("get", lambda: bool(box.isChecked())),
                     options.pop("set", lambda value: box.setChecked(bool(value))), [box],
                     read_only=options.pop("read_only", lambda: "" if box.isEnabled() else "switched off here"),
                     **options)
        self._pending_edits.append(lambda form: box.clicked.connect(lambda *_a: form.person_edited(field_id)))
        return f

    def text(self, field_id: str, label_key: str, edit: Any, *, kind: str = "code", **options: Any) -> GuiField:
        """A multi-line text (QTextEdit or QPlainTextEdit), always as plain text."""
        f = self.add(field_id, label_key, kind, options.pop("get", lambda: edit.toPlainText()),
                     options.pop("set", lambda value: edit.setPlainText(str(value))), [edit], **options)

        def watch(form: GuiForm) -> None:
            # textChanged also fires for the program's own changes: the person types only into a focused editor
            edit.textChanged.connect(lambda: form.person_edited(field_id) if _has_focus(edit) else None)
        self._pending_edits.append(watch)
        return f

    # --- the form ----------------------------------------------------------------------------------------------

    def finish(self, owner: QWidget, *, show: Optional[Callable[[], None]] = None,
               pages: Optional[WizardPages] = None, is_open: Optional[Callable[[], bool]] = None) -> GuiForm:
        """Builds the form, connects the person's edits and the markers, and registers it with the open GUI. It is
        unregistered when `owner` is destroyed (a dialog also when it finishes: see `close_with`)."""
        form = GuiForm(self.form_id, ui_text(self.title_key, "en"), ui_text(self.title_key, "de"), self.fields,
                       show=show, pages=pages, is_open=is_open)
        self.form = form
        for connect in self._pending_edits:
            connect(form)
        self._pending_edits = []
        form.listeners.append(lambda event, data: self.refresh())
        gui_surface.register_form(form)
        owner.destroyed.connect(lambda *_a, f=form: gui_surface.unregister_form(f))
        return form

    def close_with(self, dialog: Any) -> None:
        """A dialog's form leaves the surface when the dialog finishes."""
        if self.form is not None:
            dialog.finished.connect(lambda *_a, f=self.form: gui_surface.unregister_form(f))

    def banner(self, parent: Optional[QWidget] = None, audit: Optional[Callable[[str, dict], Any]] = None) \
            -> "FillBanner":
        banner = FillBanner(self, parent, audit=audit)
        self.banners.append(banner)
        return banner

    def refresh(self) -> None:
        """Markers and banners after a fill, an undo or the person's edit (GUI thread)."""
        if self.form is None:
            return
        marked = {f.id: f.filler() for f in self.form.marked()}
        for field_id, widgets in self.widgets.items():
            for widget in widgets:
                _mark(widget, marked.get(field_id))
        for banner in list(self.banners):
            banner.update_text()


def _has_focus(widget: QWidget) -> bool:
    try:
        if widget.hasFocus():
            return True
        line = widget.lineEdit() if isinstance(widget, QComboBox) else None
        return bool(line is not None and line.hasFocus())
    except RuntimeError:  # the widget is gone
        return False


def _watch_combo(form: GuiForm, field_id: str, combo: QComboBox) -> None:
    """The person picked an item (activated, never emitted by the program) or typed into an editable combo (the edit
    text changes while the combo has the focus; its line edit may be replaced when the combo becomes editable)."""
    combo.activated.connect(lambda *_a: form.person_edited(field_id))
    combo.editTextChanged.connect(lambda _t: form.person_edited(field_id) if _has_focus(combo) else None)


def mark_colour(who: Optional[Filler]) -> str:
    """Ditto's purple for Ditto's fields, another colour for every AI app's."""
    return MARK_COLOUR if who is None or who.is_ditto else APP_MARK_COLOUR


def field_tip(who: Optional[Filler], lang: Optional[str] = None) -> str:
    """The accessible description of a marked field: who filled it, in words."""
    lang = lang or current_language()
    if who is None or who.is_ditto:
        return ui_text("gui.field.tip", lang)
    return ui_text("gui.field.tip_app", lang, who=who.describe(lang))


def _mark(widget: QWidget, who: Optional[Filler]) -> None:
    """Draws or removes an AI's mark on a widget: a frame in the filler's colour (Ditto's purple, or the AI apps'
    colour) and an accessible description that says who filled it in words (the form's banner names every marked field
    too). `who` None removes the mark. The widget's own style comes back unchanged."""
    try:
        marked = widget.property(_STYLE_KEY) is not None
        if marked and (who is None or widget.property(_WHO_KEY) != who.actor + "\n" + who.app):
            widget.setStyleSheet(str(widget.property(_STYLE_KEY) or ""))
            widget.setAccessibleDescription(str(widget.property(_DESC_KEY) or ""))
            for key in (_STYLE_KEY, _DESC_KEY, _WHO_KEY):
                widget.setProperty(key, None)
            marked = False
        if who is not None and not marked:
            widget.setProperty(_STYLE_KEY, widget.styleSheet())
            widget.setProperty(_DESC_KEY, widget.accessibleDescription())
            widget.setProperty(_WHO_KEY, who.actor + "\n" + who.app)
            name = type(widget).__name__
            widget.setStyleSheet(f"{widget.styleSheet()}\n{name} {{ border: 2px solid {mark_colour(who)}; }}")
            widget.setAccessibleDescription(field_tip(who))
    except RuntimeError:  # the widget is gone
        pass


def is_marked(widget: QWidget) -> bool:
    return widget.property(_STYLE_KEY) is not None


def marked_by(widget: QWidget) -> str:
    """The caller whose mark the widget carries ("" when none): ditto, mcp or mcp:<token name>."""
    value = widget.property(_WHO_KEY)
    return str(value).split("\n", 1)[0] if value else ""


class FillBanner(QFrame):
    """'Ditto filled: Model, Quantization. Change a field to keep your own value.' with one Undo button; hidden while
    nothing of an AI's is in the form. With an AI app's fields, each filler is named in words ('Filled by the AI app
    with the MCP token "laptop": Board.'), and the button undoes the AI changes. The undo is recorded in the audit log
    (actor gui) with who had filled."""

    def __init__(self, binding: FormBinding, parent: Optional[QWidget] = None,
                 audit: Optional[Callable[[str, dict], Any]] = None):
        super().__init__(parent)
        self.binding = binding
        self._audit = audit
        self.setObjectName("dittoFillBanner")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(f"QFrame#dittoFillBanner {{ border: 1px solid {MARK_COLOUR}; border-radius: 3px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 3, 6, 3)
        self.label = plain_label("", self)
        row.addWidget(self.label, 1)
        self.btn_undo = QPushButton(button_text(ui_text("gui.banner.undo", current_language())), self)
        self.btn_undo.setAutoDefault(False)
        self.btn_undo.clicked.connect(self.undo)
        row.addWidget(self.btn_undo)
        self.setVisible(False)

    def update_text(self) -> None:
        form = self.binding.form
        if form is None:
            return
        lang = current_language()
        groups = form.marked_by()
        only_ditto = all(who.is_ditto for who in list(groups) + form.undo_fillers())
        if groups and only_ditto:
            names = ", ".join(f.label(lang) for fields in groups.values() for f in fields)
            self.label.setText(ui_text("gui.banner.filled", lang, fields=names))
        elif groups:
            lines = [ui_text("gui.banner.filled_by", lang, who=who.describe(lang),
                             fields=", ".join(f.label(lang) for f in fields)) for who, fields in groups.items()]
            self.label.setText(" ".join(lines + [ui_text("gui.banner.check", lang)]))
        self.btn_undo.setText(button_text(ui_text("gui.banner.undo" if only_ditto else "gui.banner.undo_ai", lang)))
        self.btn_undo.setEnabled(bool(form.undo_records))
        colour = MARK_COLOUR if only_ditto else APP_MARK_COLOUR
        self.setStyleSheet(f"QFrame#dittoFillBanner {{ border: 1px solid {colour}; border-radius: 3px; }}")
        self.setVisible(bool(groups) or bool(form.undo_records))

    def undo(self) -> List[str]:
        form = self.binding.form
        if form is None:
            return []
        fillers = form.undo_fillers()
        restored = form.undo_all()
        if self._audit is not None:
            try:
                self._audit("gui_fill_undone", {"form": form.id, "fields": restored,
                                                "filled_by": [who.actor for who in fillers]})
            except Exception:  # noqa: BLE001 - the undo is done; the log is extra here
                pass
        only_ditto = all(who.is_ditto for who in fillers)
        self.label.setText(ui_text("gui.banner.undone" if only_ditto else "gui.banner.undone_ai",
                                   current_language()))
        self.binding.refresh()
        return restored


class QWizardPages(WizardPages):
    """The pages of a QWizard for the GUI tools: next only when the page is complete, never past the last page (only
    the person clicks Finish)."""

    def __init__(self, wizard: Any):
        self.wizard = wizard

    def _ids(self) -> List[int]:
        return list(self.wizard.pageIds())

    def index(self) -> int:
        ids, current = self._ids(), self.wizard.currentId()
        return ids.index(current) if current in ids else 0

    def titles(self, lang: str = "en") -> List[str]:
        return [self.wizard.page(i).title() for i in self._ids()]

    def next(self) -> str:
        if self.last():
            return "this is the last page: only the person clicks Finish"
        page = self.wizard.currentPage()
        if page is not None and not page.isComplete():
            return "this page is not complete yet (a required field is empty)"
        self.wizard.next()
        return ""

    def back(self) -> str:
        if self.index() == 0:
            return "this is the first page"
        self.wizard.back()
        return ""

    def last(self) -> bool:
        return self.wizard.nextId() == -1


# --- the main window ------------------------------------------------------------------------------------------------

def person_audit(registry: Any) -> Callable[[str, dict], Any]:
    """Records a person's action in the GUI (an undo of the AI changes) in the audit log, actor gui."""
    def record(event: str, arguments: dict) -> Any:
        from orchestrator.gui.dialogs import audit_person
        return audit_person(registry, event, arguments) if registry is not None else None
    return record


def open_gui(window: Any) -> GuiSurface:
    """Attaches the GUI surface of the main window `window`: the GUI tool works on its forms from now on, for Ditto in
    this process and, through the per-user bridge, for AI apps. Registers the windows show_window may open."""
    invoker = GuiInvoker(window if isinstance(window, QObject) else None)
    surface = gui_surface.attach(GuiSurface(invoker.call))
    surface.extra["invoker"] = invoker

    def deferred(name: str) -> Callable[[], None]:
        # A dialog's exec() waits for the person: started from the event loop, so the tool call returns at once.
        return lambda: QTimer.singleShot(0, getattr(window, name))

    def tab(name: str) -> Callable[[], None]:
        def show() -> None:
            window.tabs.setCurrentWidget(getattr(window, name))
            window.raise_()
        return show

    def wizard() -> None:
        form = surface.form("module_wizard")
        if form is not None and form.is_open():
            form.show()
            return
        QTimer.singleShot(0, window._open_module_wizard)

    def ai_settings() -> None:
        form = surface.form("ai_settings")
        if form is not None and form.is_open():
            form.show()
            return
        QTimer.singleShot(0, window._open_ai_config)

    windows = {
        "builder": ("gui.window.builder", tab("builder_tab")),
        "deployment": ("gui.window.deployment", tab("deployment_window")),
        "chat": ("gui.window.chat", getattr(window, "_show_chat", lambda: None)),
        "module_wizard": ("gui.window.module_wizard", wizard),
        "ai_settings": ("gui.window.ai_settings", ai_settings),
        "hf_browser": ("gui.window.hf_browser", getattr(window, "_open_hf_browser", lambda: None)),
        "community_hub": ("gui.window.community_hub", getattr(window, "_open_community_hub", lambda: None)),
        "knowledge_snapshots": ("gui.window.knowledge_snapshots", deferred("_open_knowledge_snapshots")),
    }
    for window_id, (key, opener) in windows.items():
        surface.register_window(window_id, ui_text(key, "en"), ui_text(key, "de"), opener)
    builder = getattr(window, "builder_tab", None)
    form = getattr(builder, "gui_form", None)
    if form is not None:   # built before the surface was attached
        surface.register_form(form)
    if hasattr(builder, "create_target_requested"):   # the Builder's offer when no target fits a probe
        builder.create_target_requested.connect(lambda path: window._open_module_wizard(probe_path=path or None))
        builder.ask_ditto_requested.connect(lambda text: ask_ditto(window, text))
    framework = getattr(window, "framework", None)
    registry = getattr(framework, "tool_registry", None)
    if registry is not None:
        from orchestrator.tools.tools_gui import register_gui_tools
        register_gui_tools(registry, framework)   # the framework's tools include it already: nothing twice
    start_bridge(surface, framework)
    return surface


def start_bridge(surface: GuiSurface, framework: Any) -> Optional[Any]:
    """Starts the per-user bridge, so AI apps (MCP clients) reach this window with the same GUI tool as Ditto. The
    window works without it: when it cannot start (or another window of this person already serves AI apps), AI apps
    are told the window is not open."""
    config = getattr(framework, "config", None)
    if config is None:
        return None
    from orchestrator.tools.gui_bridge import BridgeServer
    from orchestrator.tools.tools_gui import bridge_handler
    try:
        state_dir = config.user_state_dir
    except Exception:  # noqa: BLE001 - no per-user folder: no bridge
        return None
    server = BridgeServer(state_dir, bridge_handler(framework, lambda: surface))
    if server.start():
        surface.extra["bridge"] = server
        return server
    return None


def ask_ditto(window: Any, text: str) -> None:
    """Shows the chat panel with a question written into its input field; the person reads it and sends it (or not)."""
    chat = getattr(window, "chat_window", None)
    if chat is None:
        return
    show = getattr(window, "_show_chat", None)
    if callable(show):
        show()
    read = getattr(chat.txt_input, "text", None)
    draft = (read() if callable(read) else "").rstrip()
    chat.txt_input.setText(f"{draft}\n\n{text}" if draft else text)   # a draft the person typed stays
    chat.focus_composer()


def close_gui(surface: Optional[GuiSurface]) -> None:
    """The main window closes: the bridge for AI apps stops (they are told the window is not open), and the GUI tool
    no longer reaches its forms."""
    bridge = surface.extra.pop("bridge", None) if surface is not None else None
    if bridge is not None:
        bridge.stop()
    gui_surface.detach(surface)


__all__ = ["APP_MARK_COLOUR", "FillBanner", "FormBinding", "GuiInvoker", "MARK_COLOUR", "QWizardPages", "close_gui",
           "field_tip", "is_marked", "mark_colour", "marked_by", "open_gui", "person_audit", "start_bridge"]
