#!/usr/bin/env python3
"""
Parts of the Builder tab from wave 3b, kept out of builder_tab.py so the tab itself changes little:

- `bind_builder_form()` registers the build form for Ditto's GUI tools (orchestrator/gui/form_surface.py): model,
  target, format, board, quantization, optimization, GPU, importance matrix and calibration dataset. The Start button
  is not a field: only the person starts a build, with its own checks and questions.
- `BuildProgressPanel` shows the running build: a progress bar with the stage and the percent from the build service
  (BuildService.get_status) and the live build log (BuildService.log_tail, secrets redacted), with a search box and an
  Info / Warning / Error filter. The log is plain text: build output never renders as rich text.
- `BoardAdvice` shows the board the hardware probe points to as a recommendation with its source and how sure it is
  (orchestrator/runtime/board_recommendation.py), the candidates when it is not sure, and the offer to create a target
  with the wizard or to ask Ditto when no target fits the probe. A board or target the person chose is never
  overwritten; `apply_ditto_settings()` fills Ditto's recommended settings through the build form by the same rules.

Every text is plain text (SEC-M2): plain_label, plain_tooltip, button_text and plain boxes only.
"""

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from orchestrator.gui.form_surface import FormBinding, person_audit
from orchestrator.gui.plain import ask, button_text, one_line, plain_label, plain_tooltip
from orchestrator.tools.gui_surface import GuiForm
from orchestrator.utils.localization import current_language, ui_text

LEVELS = ("all", "info", "warning", "error")
MAX_LOG_LINES = 20000      # lines the panel keeps; older ones are dropped with a note
TAIL_LINES = 1000          # lines read per poll (the build service's limit)
SEARCH_URL = "https://duckduckgo.com/?q="   # the online look-up of a board name, only after the person agreed
_ERROR = re.compile(r"(?<![\w-])(error|fatal|failed|traceback)\b", re.IGNORECASE)
_WARNING = re.compile(r"(?<![\w-])(warning|warn|deprecated)\b", re.IGNORECASE)


# --- the build form for Ditto's GUI tools ---------------------------------------------------------------------------

def _show_tab(tab: QWidget) -> None:
    """Brings the Builder tab to the front of the main window's tabs (so the person sees what Ditto fills)."""
    parent = tab.parentWidget()
    while parent is not None and not isinstance(parent, QTabWidget):
        parent = parent.parentWidget()
    if parent is not None:
        parent.setCurrentWidget(tab)


def bind_builder_form(tab: Any) -> FormBinding:
    """Registers the build form's fields (the tab keeps the binding as tab.gui_binding and the form as tab.gui_form)
    and returns the binding; the caller places its banner."""
    binding = FormBinding("builder", "gui.form.builder")
    binding.combo("model", "gui.field.model", tab.model_input, kind="model", by_data=False,
                  hint="a Hugging Face repo id (owner/name) or a local model by its file id (lf_...)")
    binding.combo("target", "gui.field.target", tab.target_combo, hint="a target id from the list")
    binding.combo("format", "gui.field.format", tab.format_combo, hint="a format the target offers")

    def board_choices() -> List[str]:
        combo = tab.board_combo
        if combo.isEditable():
            return []   # a target without capabilities: the board is free text
        return [str(combo.itemData(i)) for i in range(combo.count()) if combo.itemData(i) is not None]

    def set_board(value: Any) -> None:
        combo, text = tab.board_combo, str(value)
        index = combo.findData(text)
        if index >= 0:
            combo.setCurrentIndex(index)
        elif combo.isEditable():
            combo.setEditText(text)
        else:
            raise ValueError("not offered")

    binding.combo("board", "gui.field.board", tab.board_combo, get=lambda: tab._selected_board() or "",
                  set=set_board, choices=board_choices, closed=True,
                  hint="a board of the target; leave the person's choice alone")

    def quant_check(_current: Any, new: Any) -> str:
        caps = tab._capabilities()
        cap = caps.formats.get(tab.format_combo.currentData()) if caps else None
        if cap is None or not str(new).strip():
            return ""
        offered = cap.quantizations_for(tab._selected_board())
        return "" if str(new) in offered else f"not offered for this board and format: {', '.join(offered[:30])}"

    def set_quant(value: Any) -> None:
        tab.quant_combo.setCurrentText(str(value))

    binding.combo("quantization", "gui.field.quantization", tab.quant_combo, by_data=False, set=set_quant,
                  check=quant_check, hint="a quantization type the format offers (empty: the board's default)")
    binding.combo("optimization", "gui.field.optimization", tab.opt_combo, by_data=False)
    binding.check("use_gpu", "gui.field.use_gpu", tab.chk_gpu)
    binding.check("imatrix", "gui.field.imatrix", tab.chk_imatrix)

    def dataset_read_only() -> str:
        return "" if tab.dataset_combo.isEnabled() else "this format uses no calibration dataset"

    binding.combo("dataset", "gui.field.dataset", tab.dataset_combo, kind="path", by_data=False,
                  read_only=dataset_read_only, hint="a dataset file by its file id (lf_...) or <label>:<path>")
    registry = getattr(getattr(tab, "framework", None), "tool_registry", None)
    tab.gui_binding = binding
    tab.gui_form = binding.finish(tab, show=lambda: _show_tab(tab), is_open=lambda: True)
    tab.fill_banner = binding.banner(tab, audit=person_audit(registry))
    tab.gui_form.listeners.append(lambda event, data: _remember_person_board(tab, event, data))
    return binding


def _remember_person_board(tab: Any, event: str, data: Any) -> None:
    """The board value the person chose (their edit, or several values at once such as a recipe they picked)."""
    field = tab.gui_form.field("board") if getattr(tab, "gui_form", None) is not None else None
    if event == "person" and data in ("board", "") and field is not None and field.person_set:
        tab._person_board = tab._selected_board()
        advice = getattr(tab, "board_advice", None)
        if advice is not None:   # the person picked a board: nothing left to confirm
            advice.unsure = False


def person_board(tab: Any) -> Optional[str]:
    """The board the person chose, while it is still the board in the form (a target change may have reset it)."""
    form = getattr(tab, "gui_form", None)
    field = form.field("board") if form is not None else None
    if field is None or not field.person_set:
        return None
    chosen = getattr(tab, "_person_board", None)
    return chosen if chosen and chosen == tab._selected_board() else None


def person_target(tab: Any) -> Optional[str]:
    """The target the person chose (their pick, or a recipe they picked), also through a board they chose (a board
    belongs to its target); a probe never replaces it (N9'')."""
    form = getattr(tab, "gui_form", None)
    field = form.field("target") if form is not None else None
    chosen = field is not None and field.person_set
    return tab.target_combo.currentData() if chosen or person_board(tab) else None


def apply_ditto_settings(tab: Any, config: Dict[str, Any]) -> Any:
    """Ditto's recommended build settings ('Ask Ditto for Config') through the build form, like the GUI tool's fill: they
    are marked and undoable, and a value the person chose or is editing stays (the log names Ditto's suggestion)."""
    form = getattr(tab, "gui_form", None)
    values = {key: (str(config[key]).lower() if key == "format" else str(config[key]))
              for key in ("format", "quantization", "optimization") if config.get(key)}
    if form is None or not values:
        return None
    outcome = form.fill(values)
    for field_id, why in outcome.refused:   # every value not filled is named with its reason
        field = form.field(field_id)
        if field is None:
            continue
        name, value = field.label(current_language()), one_line(values[field_id])
        if field.person_set or GuiForm._busy(field):
            tab._log(ui_text("gui.ditto.kept", value=value, field=name))
        else:
            tab._log(ui_text("gui.ditto.not_filled", value=value, field=name, why=one_line(why)))
    return outcome


def board_confirmed(tab: Any) -> bool:
    """Start Build after a probe that could not tell the board for sure (N9': never guess): until the person picks a
    board, they are asked whether the board in the form is theirs ("No" is the default); their yes makes it their
    choice. Returns whether the build may start."""
    advice = getattr(tab, "board_advice", None)
    if advice is None or not advice.unsure:
        return True
    names = ", ".join(c.label for c in getattr(advice.recommendation, "candidates", ()) or ()) or "-"
    board = one_line(tab.board_combo.currentText().strip() or "-")
    if not ask(tab, ui_text("gui.board.confirm.title"), ui_text("gui.board.confirm", board=board,
                                                                candidates=one_line(names))):
        tab._log(ui_text("gui.board.confirm.no"))
        return False
    form = getattr(tab, "gui_form", None)
    if form is not None:
        form.person_edited("board")
    advice.unsure = False
    return True


# --- progress bar and live build log --------------------------------------------------------------------------------

def line_level(line: str) -> str:
    """info, warning or error for one log line: the build engine's [ERROR] / [WARNING] tag first, then the words a
    tool's own output uses ('error:', 'fatal', 'warning:'); a flag such as -Werror is no error."""
    tag = re.search(r"\[(ERROR|WARNING|WARN|INFO)\]", line[:60])
    if tag and tag.group(1) == "ERROR":
        return "error"
    if tag and tag.group(1) in ("WARNING", "WARN"):
        return "warning"
    return _content_level(line[tag.end():] if tag else line)


def _content_level(text: str) -> str:
    if _ERROR.search(text):
        return "error"
    if _WARNING.search(text):
        return "warning"
    return "info"


class BuildProgressPanel(QWidget):
    """The running build: stage and percent, and its live log with search and a level filter."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang = current_language()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_stage = plain_label(ui_text("gui.progress.idle", self.lang), self)
        layout.addWidget(self.lbl_stage)
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setAccessibleName(ui_text("gui.progress.bar", self.lang))
        layout.addWidget(self.bar)
        row = QHBoxLayout()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(ui_text("gui.log.search", self.lang))
        self.search.setAccessibleName(ui_text("gui.log.search", self.lang))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        row.addWidget(self.search, 1)
        self.level = QComboBox(self)
        for level in LEVELS:
            self.level.addItem(ui_text(f"gui.log.level.{level}", self.lang), level)
        self.level.setAccessibleName(ui_text("gui.log.level", self.lang))
        self.level.setToolTip(plain_tooltip(ui_text("gui.log.level", self.lang)))
        self.level.currentIndexChanged.connect(self.apply_filter)
        row.addWidget(self.level)
        self.lbl_count = plain_label("", self, wrap=False)
        row.addWidget(self.lbl_count)
        layout.addLayout(row)
        # Plain text: build output and names from packs never render as rich text.
        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(MAX_LOG_LINES)   # the view drops what the list drops
        self.log_view.setAccessibleName(ui_text("gui.log.title", self.lang))
        self.log_view.setStyleSheet("font-family: Consolas, Monospace; background: #222; color: #ddd;")
        layout.addWidget(self.log_view, 1)
        self._lines: List[Tuple[str, str]] = []
        self._seen = 0
        self._dropped = 0
        self._attempts = False   # the service heads each attempt (it does so once a request has two)
        self.request_id: Optional[str] = None

    def start(self, request_id: str) -> None:
        self.request_id = request_id
        self._lines, self._seen, self._dropped, self._attempts = [], 0, 0, False
        self.bar.setValue(0)
        self.lbl_stage.setText(ui_text("gui.progress.started", self.lang, request=request_id))
        self.apply_filter()

    def poll(self, service: Any, request_id: str) -> Optional[str]:
        """Reads the build's status and its new log lines; returns the status (None when the service does not know
        the build any more)."""
        try:
            status = service.get_status(request_id)
        except Exception:  # noqa: BLE001 - unknown to the service: nothing to follow any more
            self.lbl_stage.setText(ui_text("gui.progress.unknown", self.lang, request=request_id))
            return None
        self.show_status(status)
        try:
            tail = service.log_tail(request_id, lines=TAIL_LINES, for_person=True)  # the person's window: full paths
        except Exception:  # noqa: BLE001 - the status is shown; the log comes with the next poll
            tail = None
        if tail is not None:
            self.add_lines(list(tail.lines), int(tail.first_line), int(tail.total_lines))
        return status.status

    def show_status(self, status: Any) -> None:
        percent = max(0, min(100, int(getattr(status, "progress_percent", 0) or 0)))
        self.bar.setValue(percent)
        stage = one_line(getattr(status, "stage", "") or "")
        state = str(getattr(status, "status", "") or "")
        key = "gui.progress.ended" if state in ("completed", "error", "cancelled") else "gui.progress.stage"
        self.lbl_stage.setText(ui_text(key, self.lang, stage=stage or "-", percent=percent,
                                       status=ui_text(f"gui.progress.status.{state}", self.lang)
                                       if state in ("completed", "error", "cancelled") else state))

    def add_lines(self, lines: Sequence[str], first_line: int, total: int) -> None:
        """New lines of the log (numbered from first_line); lines read before are skipped, lines the service no
        longer returns are noted."""
        new = []
        heading = re.compile(r"^===== " + re.escape(self.request_id or "") + r"_\d+(?:_r\d+)? =====$")
        if not self._attempts and any(heading.match(line) for line in lines):
            # A second attempt began: the service now heads the first one too, so every line read before moved down
            # by one (a panel that starts with the headings in place read none of them yet)
            self._attempts = True
            self._seen += 1 if self._seen else 0
        if lines and first_line > self._seen + 1:
            new.append(("info", ui_text("gui.log.skipped", self.lang, n=first_line - self._seen - 1)))
        for number, line in enumerate(lines, start=first_line):
            if number <= self._seen:
                continue
            new.append((line_level(line), line))
        if lines:
            self._seen = max(self._seen, first_line + len(lines) - 1)
        if not new:
            return
        self._lines += new
        if len(self._lines) > MAX_LOG_LINES:
            cut = len(self._lines) - MAX_LOG_LINES
            self._dropped += cut
            del self._lines[:cut]
        if self._filter_active():
            self.apply_filter()
        else:
            for _level, line in new:
                self.log_view.appendPlainText(line)
            self._count()

    def _filter_active(self) -> bool:
        return bool(self.search.text().strip()) or self.level.currentData() not in (None, "all")

    def visible_lines(self) -> List[str]:
        """The lines the search box and the level filter let through."""
        needle = self.search.text().strip().lower()
        level = self.level.currentData() or "all"
        return [line for lv, line in self._lines
                if (level == "all" or lv == level) and (not needle or needle in line.lower())]

    def apply_filter(self, *_args: Any) -> None:
        shown = self.visible_lines()
        self.log_view.setPlainText("\n".join(shown))
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._count(len(shown))

    def _count(self, shown: Optional[int] = None) -> None:
        total = len(self._lines)
        shown = total if shown is None else shown
        text = ui_text("gui.log.count", self.lang, shown=shown, total=total)
        if self._dropped:
            text += " " + ui_text("gui.log.dropped", self.lang, n=self._dropped)
        self.lbl_count.setText(text)

    def plain_text(self) -> str:
        return self.log_view.toPlainText()


# --- the board recommendation and the offer when no target fits -------------------------------------------------------

class BoardAdvice(QWidget):
    """Under the target and board: the board recommendation (source and how sure), the candidates to pick from when it
    is not sure, the online look-up (after the person agreed) and Ask Ditto; or, when no target fits the probe, the
    offer to create a target with the wizard or to ask Ditto."""
    create_target = Signal()
    ask_ditto = Signal(str)

    def __init__(self, tab: Any, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.tab = tab
        self.lang = current_language()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = plain_label("", self)
        layout.addWidget(self.label)
        self.row = QHBoxLayout()
        layout.addLayout(self.row)
        self.buttons: List[QPushButton] = []
        self.recommendation = None
        self.profile = None
        self.unsure = False   # a probe could not tell the board and the person has not picked one since
        self.setVisible(False)

    def _clear_buttons(self) -> None:
        for button in self.buttons:
            self.row.removeWidget(button)
            button.deleteLater()
        self.buttons = []
        self.unsure = False

    def _button(self, text: str, slot: Callable[[], Any], tip: str = "") -> QPushButton:
        button = QPushButton(button_text(text), self)
        button.setAutoDefault(False)
        if tip:
            button.setToolTip(plain_tooltip(tip))
        button.clicked.connect(lambda *_a: slot())
        self.row.addWidget(button)
        self.buttons.append(button)
        return button

    def hide_advice(self) -> None:
        self._clear_buttons()
        self.recommendation = None
        self.setVisible(False)

    def show_recommendation(self, rec: Any, profile: Any = None) -> str:
        """Applies a sure recommendation unless the person chose another board (then it is shown only); shows the
        candidates when it is not sure. Returns applied, kept, ask or none."""
        self._clear_buttons()
        self.recommendation, self.profile = rec, profile
        lang = self.lang
        source = ui_text(f"gui.board.source.{rec.source or 'none'}", lang)
        if rec.sure:
            label = next((c.label for c in rec.candidates if c.board == rec.board), rec.board)
            chosen = person_board(self.tab)
            if chosen and chosen != rec.board:
                self.label.setText(ui_text("gui.board.kept", lang, board=one_line(label), source=source,
                                           chosen=one_line(chosen)))
                self._button(ui_text("gui.board.use", lang, board=label), lambda b=rec.board: self.pick(b))
                outcome = "kept"
            else:
                index = self.tab.board_combo.findData(rec.board)
                if index < 0:
                    self.label.setText(ui_text("gui.board.not_offered", lang, board=one_line(label), source=source))
                    outcome = "none"
                else:
                    self.tab.board_combo.setCurrentIndex(index)
                    self.label.setText(ui_text("gui.board.recommended", lang, board=one_line(label), source=source))
                    outcome = "applied"
        elif rec.candidates:
            names = ", ".join(c.label for c in rec.candidates)
            self.label.setText(ui_text("gui.board.unsure", lang, why=ui_text(_why_key(rec), lang),
                                       name=one_line(rec.board_model or "-"), candidates=one_line(names)))
            for candidate in rec.candidates[:8]:
                self._button(ui_text("gui.board.pick", lang, board=candidate.label),
                             lambda b=candidate.board: self.pick(b), tip=candidate.why)
            if rec.board_model:
                self._button(ui_text("gui.board.online", lang), self.look_up_online)
            self._button(ui_text("gui.board.ask_ditto", lang), self._ask_about_board)
            self.unsure = True   # Start Build asks until the person picks a board
            outcome = "ask"
        else:
            self.hide_advice()
            return "none"
        self.setVisible(True)
        return outcome

    def pick(self, board: str) -> None:
        """The person picked a candidate: their choice from now on."""
        index = self.tab.board_combo.findData(board)
        if index < 0:
            return
        self.tab.board_combo.setCurrentIndex(index)
        form = getattr(self.tab, "gui_form", None)
        if form is not None:
            form.person_edited("board")
        self.label.setText(ui_text("gui.board.picked", self.lang,
                                   board=one_line(self.tab.board_combo.currentText())))
        self._clear_buttons()

    def look_up_online(self) -> bool:
        """Opens the person's web browser with a search for the probe's board name, after asking: the search site
        receives that name."""
        rec = self.recommendation
        name = (getattr(rec, "board_model", "") or "").strip()
        if not name:
            return False
        if not ask(self, ui_text("gui.board.online.title", self.lang),
                   ui_text("gui.board.online.question", self.lang, name=one_line(name))):
            return False
        return bool(QDesktopServices.openUrl(QUrl(SEARCH_URL + quote_plus(f"{name} SoC"))))

    def _ask_about_board(self) -> None:
        rec = self.recommendation
        names = ", ".join(c.label for c in getattr(rec, "candidates", ()) or ())
        self.ask_ditto.emit(ui_text("gui.board.ask_text", self.lang, name=one_line(getattr(rec, "board_model", "")
                                                                                    or "-"),
                                    soc=one_line(getattr(rec, "soc", "") or "-"),
                                    target=one_line(getattr(rec, "target", "") or "-"), candidates=names or "-"))

    def show_no_target(self, profile: Any) -> None:
        """No target fits the probe: offer the wizard (with this probe) and Ditto."""
        self._clear_buttons()
        self.recommendation, self.profile = None, profile
        get = getattr(profile, "get", None)
        facts = _probe_words(get) if callable(get) else "-"
        self.label.setText(ui_text("gui.target.none", self.lang, facts=one_line(facts)))
        self._button(ui_text("gui.target.wizard", self.lang), self.create_target.emit)
        self._button(ui_text("gui.target.ask_ditto", self.lang),
                     lambda: self.ask_ditto.emit(ui_text("gui.target.ask_text", self.lang, facts=one_line(facts))))
        self.setVisible(True)

    def show_target_kept(self, profile: Any, match: str, chosen: str) -> None:
        """The probe points to another target than the one the person chose: their choice stays (N9''); one click
        takes the probe's target and then its board recommendation."""
        self._clear_buttons()
        self.recommendation, self.profile = None, profile
        combo = self.tab.target_combo
        label = combo.itemText(combo.findData(match)) or match
        self.label.setText(ui_text("gui.target.kept", self.lang, target=one_line(label),
                                   chosen=one_line(combo.itemText(combo.findData(chosen)) or chosen)))
        self._button(ui_text("gui.target.use", self.lang, target=label), lambda: self.use_target(match))
        self.setVisible(True)

    def use_target(self, target: str) -> str:
        """The person took the probe's target: their choice from now on, then the board recommendation for it."""
        index = self.tab.target_combo.findData(target)
        if index < 0:
            return "none"
        self.tab.target_combo.setCurrentIndex(index)
        form = getattr(self.tab, "gui_form", None)
        if form is not None:
            form.person_edited("target")
        return after_probe(self.tab, self.profile, target)

    def plain_text(self) -> str:
        return "\n".join([self.label.text()] + [b.text().replace("&&", "&") for b in self.buttons])


def _why_key(rec: Any) -> str:
    if rec.source == "list":
        return "gui.board.why.partial" if len(rec.candidates) == 1 else "gui.board.why.several"
    return "gui.board.why.no_soc"


def _probe_words(get: Callable[[str], Any]) -> str:
    """The probe's board, SoC, NPU and architecture in words (validated values; the board name is display text)."""
    parts = []
    for key, word in (("BOARD_MODEL", ""), ("SOC_MODEL", "SoC "), ("NPU_VENDOR", "NPU "), ("ARCH", "")):
        value = get(key)
        if value:
            parts.append(f"{word}{value}")
    return ", ".join(parts) or "-"


def read_probe(path: Path) -> Any:
    """The probe file as a hardware profile (a schema-1 file converted leniently); None when it is none."""
    from orchestrator.runtime.profile import ProfileError, read_profile
    try:
        return read_profile(Path(path))
    except (ProfileError, OSError, ValueError):
        return None


def after_probe(tab: Any, profile: Any, match: Optional[str]) -> str:
    """After a probe: the board recommendation for the matched target, or the offer when no target fits."""
    advice = getattr(tab, "board_advice", None)
    if advice is None:
        return "none"
    if not match:
        advice.show_no_target(profile)
        return "no_target"
    chosen = person_target(tab)
    if chosen and chosen != match:   # the person's target stays; the probe's is offered with one click
        advice.show_target_kept(profile, match, chosen)
        return "kept_target"
    if profile is None:
        advice.hide_advice()
        return "none"
    from orchestrator.runtime.board_recommendation import recommend_board
    tm = tab.framework.get_component("target_manager") if hasattr(tab.framework, "get_component") else None
    return advice.show_recommendation(recommend_board(profile, tm, match), profile)


__all__ = ["BoardAdvice", "BuildProgressPanel", "LEVELS", "after_probe", "apply_ditto_settings", "bind_builder_form",
           "board_confirmed", "line_level", "person_board", "person_target", "read_probe"]
