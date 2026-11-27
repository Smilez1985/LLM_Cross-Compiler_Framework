#!/usr/bin/env python3
"""
Help > Framework Log...: `llm-cli logs show` and `llm-cli logs export` by mouse (step 2 of "One log the person and
Ditto read").

The window reads framework.log and its old files (utils/framework_log.read_entries, the CLI's reader) in a plain
worker thread (a long log must not hold the window; the result comes back through gui/worker_relay.py) and shows the
entries as plain text, one line each, oldest first. The level, the last minutes, the part of the framework and the
number of entries are the CLI's --level, --since, --part and --lines. Export... writes the entries of that choice to a
file the person picks (utils/framework_log.export: private, the home folder written as ~USER; a .json file is JSON,
any other text), Open Log Folder shows the folder. The row "Level of a part" is `llm-cli logs level PART LEVEL` and
`--reset` (utils/logging.set_part_level: saved, applied in this program at once) with the parts that have their own
level beside it (`llm-cli logs levels`); it is shown when the window has the person's settings. Every text is plain
text.
"""

import threading
from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout)

from orchestrator.gui.plain import button_text, plain_box, plain_label, plain_tooltip
from orchestrator.utils.localization import current_language, ui_text

LEVELS = ("info", "warning", "error", "debug")   # the CLI's --level choices, the default first
PART_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")   # `llm-cli logs level PART LEVEL`


def choose_export_file(parent: Any, title: str, start: str, filters: str) -> str:
    """The file dialog of Export... (tests replace this)."""
    return QFileDialog.getSaveFileName(parent, title, start, filters)[0]


class FrameworkLogWindow(QDialog):
    """The framework's log: the CLI's filters, the entries, Export... and Open Log Folder."""

    def __init__(self, folder: Path, parent: Any = None, lang: Optional[str] = None, config: Any = None):
        super().__init__(parent)
        from orchestrator.gui.worker_relay import worker_relay
        self.folder, self.lang = Path(folder), lang or current_language()
        self.config = config   # the person's settings, for the levels per part (None: the row is not shown)
        self.entries: List[dict] = []
        self._token: Optional[object] = None
        self.setObjectName("framework_log_window")
        self.setWindowTitle(self._t("logs.title"))
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("logs.intro", path=str(self.folder / "framework.log")), self))
        form = QFormLayout()
        self.cmb_log_level = QComboBox(self)
        self.cmb_log_level.setObjectName("cmb_log_level")
        for level in LEVELS:
            self.cmb_log_level.addItem(self._t(f"logs.level.{level}"), level)
        form.addRow(self._t("logs.level"), self.cmb_log_level)
        self.spin_log_since = QSpinBox(self)
        self.spin_log_since.setObjectName("spin_log_since")
        self.spin_log_since.setRange(0, 60 * 24 * 365)
        form.addRow(self._t("logs.since"), self.spin_log_since)
        self.txt_log_part = QLineEdit(self)
        self.txt_log_part.setObjectName("txt_log_part")
        form.addRow(self._t("logs.part"), self.txt_log_part)
        self.spin_log_lines = QSpinBox(self)
        self.spin_log_lines.setObjectName("spin_log_lines")
        self.spin_log_lines.setRange(1, 10000)
        self.spin_log_lines.setValue(200)
        form.addRow(self._t("logs.lines"), self.spin_log_lines)
        layout.addLayout(form)
        self.lbl_log_status = plain_label("", self)
        self.lbl_log_status.setObjectName("lbl_log_status")
        layout.addWidget(self.lbl_log_status)
        self.txt_log_entries = QPlainTextEdit(self)
        self.txt_log_entries.setObjectName("txt_log_entries")
        self.txt_log_entries.setReadOnly(True)
        self.txt_log_entries.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self.txt_log_entries, stretch=1)
        row = QHBoxLayout()
        self.btn_log_refresh = self._button("btn_log_refresh", "logs.refresh", self.refresh)
        self.btn_log_export = self._button("btn_log_export", "logs.export", self.export)
        self.btn_log_export.setToolTip(plain_tooltip(self._t("logs.export.tip")))
        self.btn_log_folder = self._button("btn_log_folder", "logs.folder", self.open_folder)
        for button in (self.btn_log_refresh, self.btn_log_export, self.btn_log_folder):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        if self.config is not None:
            layout.addLayout(self._part_level_row())
        worker_relay().done.connect(self._done, Qt.QueuedConnection)
        self.refresh()

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, name: str, key: str, slot) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _part_level_row(self) -> QHBoxLayout:
        """`llm-cli logs level PART LEVEL` and `--reset` by mouse, with the parts that have their own level."""
        row = QHBoxLayout()
        row.addWidget(plain_label(self._t("logs.part_level"), self))
        self.txt_part_level_part = QLineEdit(self)
        self.txt_part_level_part.setObjectName("txt_part_level_part")
        self.txt_part_level_part.setPlaceholderText(self._t("logs.part_level.part"))
        self.txt_part_level_part.setToolTip(plain_tooltip(self._t("logs.part_level.tip")))
        row.addWidget(self.txt_part_level_part, stretch=1)
        self.cmb_part_level = QComboBox(self)
        self.cmb_part_level.setObjectName("cmb_part_level")
        for level in PART_LEVELS:
            self.cmb_part_level.addItem(level, level)
        row.addWidget(self.cmb_part_level)
        self.btn_part_level_set = self._button("btn_part_level_set", "logs.part_level.set", self.set_part_level)
        self.btn_part_level_reset = self._button("btn_part_level_reset", "logs.part_level.reset",
                                                 self.reset_part_level)
        row.addWidget(self.btn_part_level_set)
        row.addWidget(self.btn_part_level_reset)
        self.lbl_part_levels = plain_label("", self)
        self.lbl_part_levels.setObjectName("lbl_part_levels")
        row.addWidget(self.lbl_part_levels, stretch=1)
        self._show_part_levels()
        return row

    def _show_part_levels(self) -> None:
        from orchestrator.utils.logging import LOG_LEVELS_SETTING, part_levels_of
        levels = part_levels_of(self.config.get(LOG_LEVELS_SETTING, {}))
        self.lbl_part_levels.setText(self._t("logs.part_level.list", levels=", ".join(
            f"{part}: {level}" for part, level in sorted(levels.items()))) if levels else
            self._t("logs.part_level.none"))

    def _change_part_level(self, level: Optional[str]) -> bool:
        from orchestrator.utils.logging import set_part_level
        try:
            set_part_level(self.config, self.txt_part_level_part.text().strip(), level)
        except (OSError, ValueError) as e:
            plain_box(self, "warning", self._t("logs.part_level"), self._t("logs.part_level.failed", reason=str(e)))
            return False
        self._show_part_levels()
        return True

    def set_part_level(self) -> bool:
        """`llm-cli logs level PART LEVEL`: saved and applied in this program at once."""
        return self._change_part_level(self.cmb_part_level.currentData())

    def reset_part_level(self) -> bool:
        """`llm-cli logs level PART --reset`."""
        return self._change_part_level(None)

    def choice(self) -> dict:
        """The CLI's filters as the window holds them (--level, --since, --part, --lines)."""
        return {"level": self.cmb_log_level.currentData() or "info", "since": self.spin_log_since.value(),
                "part": self.txt_log_part.text().strip(), "lines": self.spin_log_lines.value()}

    def _read(self, choice: dict, limit: Optional[int]) -> List[dict]:
        from orchestrator.utils import framework_log
        return framework_log.read_entries(self.folder, level=choice["level"],
                                          since=framework_log.minutes_ago(choice["since"]), part=choice["part"],
                                          limit=limit)

    def refresh(self) -> None:
        """Reads the entries of the current choice off the GUI thread (`llm-cli logs show`)."""
        from orchestrator.gui.worker_relay import worker_relay
        token, relay, choice = object(), worker_relay(), self.choice()
        self._token = token
        self.lbl_log_status.setText(self._t("logs.reading"))

        def run() -> None:
            try:
                result: Any = ("ok", self._read(choice, choice["lines"]))
            except Exception as e:  # noqa: BLE001 - shown in the window, never a crash
                result = ("error", e)
            relay.done.emit(token, result)

        threading.Thread(target=run, name="framework-log", daemon=True).start()

    def _done(self, token: object, result: Any) -> None:
        if token is not self._token:
            return
        self._token = None
        from orchestrator.gui.plain import display_text
        from orchestrator.utils import framework_log
        kind, value = result
        self.entries = value if kind == "ok" else []
        self.txt_log_entries.setPlainText("\n".join(display_text(framework_log.entry_text(e)) for e in self.entries))
        self.lbl_log_status.setText(self._t("logs.count", n=len(self.entries)) if self.entries else
                                    self._t("logs.none"))
        self.txt_log_entries.verticalScrollBar().setValue(self.txt_log_entries.verticalScrollBar().maximum())

    @property
    def reading(self) -> bool:
        return self._token is not None

    def export(self) -> Optional[Path]:
        """`llm-cli logs export FILE`: the entries of the current choice (every one, not only the newest shown) to a
        file the person picks; .json is JSON, any other name text."""
        from orchestrator.utils import framework_log
        start = str(Path.home() / "framework-log.json")
        name = choose_export_file(self, self._t("logs.export.title"), start, self._t("logs.export.filter"))
        if not name:
            return None
        path = Path(name)
        choice = self.choice()
        try:
            entries = self._read(choice, None)
            framework_log.export(entries, path, "json" if path.suffix.lower() == ".json" else "text")
        except (OSError, ValueError) as e:
            plain_box(self, "warning", self._t("logs.export.title"),
                      self._t("logs.export.failed", path=str(path), reason=getattr(e, "strerror", None) or str(e)))
            return None
        self.lbl_log_status.setText(self._t("logs.export.done", n=len(entries), path=str(path)))
        return path

    def open_folder(self) -> bool:
        from orchestrator.gui.crash_dialog import open_folder
        return open_folder(self.folder)


__all__ = ["FrameworkLogWindow", "LEVELS", "PART_LEVELS", "choose_export_file"]
