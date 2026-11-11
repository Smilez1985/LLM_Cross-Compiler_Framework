#!/usr/bin/env python3
"""
File > Settings... > Update (`llm-cli update [--check] [--offline]` and `llm-cli launcher status|install|remove` by
mouse; Tools > Maintenance > Update Framework... opens this page).

The page names the checkout the update comes from (UpdateManager.source_root), checks it for new commits in a worker
thread (UpdateManager.check_for_updates, `update --check`: a git fetch, nothing changes), offers the tested dependency
set (`--offline`) and starts the update with menus/update.run_update: the question with "No" as its default, then the
installer where the person sees its questions. An AI never starts an update; nothing here is a field an AI may fill.
"""

from typing import Any, List, Optional

from PySide6.QtWidgets import QCheckBox, QGroupBox, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from orchestrator.gui.plain import button_text, one_line, plain_label
from orchestrator.utils.localization import current_language, ui_text


class UpdatePage(QWidget):
    """The update check and Update Framework...; `window` is the main window (it closes for the update on Windows)."""

    def __init__(self, window: Any = None, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        from orchestrator.gui.menus.update import app_root_of
        from orchestrator.gui.sign_in import Worker
        from orchestrator.utils.updater import UpdateManager
        self.setObjectName("page_settings_update")
        self.window_ = window
        self.lang = lang or current_language()
        self.manager = UpdateManager(app_root_of(window))
        self.worker = Worker(self._set_busy, self)
        layout = QVBoxLayout(self)
        intro = plain_label(self._t("update.page.intro"), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.lbl_source = plain_label("", self)
        self.lbl_source.setObjectName("lbl_update_source")
        self.lbl_source.setWordWrap(True)
        layout.addWidget(self.lbl_source)
        row = QHBoxLayout()
        self.btn_check = QPushButton(button_text(self._t("update.page.check")), self)
        self.btn_check.setObjectName("btn_update_check")
        self.btn_check.setAutoDefault(False)
        self.btn_check.clicked.connect(self.check)
        row.addWidget(self.btn_check)
        self.lbl_check = plain_label("", self)
        self.lbl_check.setObjectName("lbl_update_check")
        self.lbl_check.setWordWrap(True)
        row.addWidget(self.lbl_check, 1)
        layout.addLayout(row)
        self.chk_offline = QCheckBox(self._t("update.page.offline"), self)
        self.chk_offline.setObjectName("chk_update_offline")
        layout.addWidget(self.chk_offline)
        self.btn_run = QPushButton(button_text(self._t("update.page.run")), self)
        self.btn_run.setObjectName("btn_update_run")
        self.btn_run.setAutoDefault(False)
        self.btn_run.clicked.connect(self.run)
        run_row = QHBoxLayout()
        run_row.addWidget(self.btn_run)
        run_row.addStretch(1)
        layout.addLayout(run_row)
        self.lbl_result = plain_label("", self)
        self.lbl_result.setObjectName("lbl_update_result")
        self.lbl_result.setWordWrap(True)
        layout.addWidget(self.lbl_result)
        self._start_entry_part(layout)
        layout.addStretch(1)
        self.show_source()
        self.show_start_entry()

    def _start_entry_part(self, layout: QVBoxLayout) -> None:
        """The start entry (`llm-cli launcher status|install|remove` by mouse): its state, Create or Repair, Remove."""
        box = QGroupBox(self._t("launcher.title"), self)
        box.setObjectName("grp_launcher")
        part = QVBoxLayout(box)
        intro = plain_label(self._t("launcher.intro"), box)
        intro.setWordWrap(True)
        part.addWidget(intro)
        self.lbl_launcher_status = plain_label("", box)
        self.lbl_launcher_status.setObjectName("lbl_launcher_status")
        self.lbl_launcher_status.setWordWrap(True)
        part.addWidget(self.lbl_launcher_status)
        row = QHBoxLayout()
        self.btn_launcher_install = QPushButton(button_text(self._t("launcher.install")), box)
        self.btn_launcher_install.setObjectName("btn_launcher_install")
        self.btn_launcher_install.setAutoDefault(False)
        self.btn_launcher_install.clicked.connect(lambda _=False: self.change_start_entry("install"))
        row.addWidget(self.btn_launcher_install)
        self.btn_launcher_remove = QPushButton(button_text(self._t("launcher.remove")), box)
        self.btn_launcher_remove.setObjectName("btn_launcher_remove")
        self.btn_launcher_remove.setAutoDefault(False)
        self.btn_launcher_remove.clicked.connect(lambda _=False: self.change_start_entry("remove"))
        row.addWidget(self.btn_launcher_remove)
        row.addStretch(1)
        part.addLayout(row)
        self.lbl_launcher_result = plain_label("", box)
        self.lbl_launcher_result.setObjectName("lbl_launcher_result")
        self.lbl_launcher_result.setWordWrap(True)
        part.addWidget(self.lbl_launcher_result)
        layout.addWidget(box)

    def show_start_entry(self) -> None:
        """`llm-cli launcher status`: each entry with its state and whose it is."""
        from orchestrator.utils import launcher
        supported = launcher.supported()
        self.btn_launcher_install.setEnabled(supported)
        self.btn_launcher_remove.setEnabled(supported)
        if not supported:
            self.lbl_launcher_status.setText(self._t("launcher.unsupported"))
            return
        try:
            entries = launcher.status()
        except (OSError, ValueError) as e:
            self.lbl_launcher_status.setText(self._t("launcher.result.error", reason=one_line(str(e))))
            return
        lines = []
        for entry in entries:
            state = self._t(f"launcher.state.{entry.state}", path=one_line(entry.path))
            lines.append(f"{self._t(f'launcher.scope.{entry.scope}')}: {state}")
        self.lbl_launcher_status.setText("\n".join(lines))

    def change_start_entry(self, action: str) -> str:
        """`llm-cli launcher install` or `remove`: the result in a line under the buttons, then the state again."""
        from orchestrator.utils import launcher
        try:
            result = launcher.install() if action == "install" else launcher.remove()
            text = self._t(f"launcher.result.{result}")
        except (OSError, ValueError) as e:
            result, text = "error", self._t("launcher.result.error", reason=one_line(str(e)))
        self.lbl_launcher_result.setText(text)
        self.show_start_entry()
        return result

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _set_busy(self, busy: bool) -> None:
        self.btn_check.setEnabled(not busy)
        self.btn_run.setEnabled(not busy and self.manager.source_root() is not None)

    def show_source(self) -> None:
        source = self.manager.source_root()
        if source is None:
            self.lbl_source.setText(self._t("update.no_source", root=one_line(str(self.manager.app_root))))
        else:
            self.lbl_source.setText(self._t("update.page.source", source=one_line(str(source))))
        self.btn_check.setEnabled(source is not None)
        self.btn_run.setEnabled(source is not None)

    def flags(self) -> List[str]:
        """The installer's switches the page chose (`llm-cli update --offline`)."""
        return ["--offline"] if self.chk_offline.isChecked() else []

    def check(self) -> None:
        """`llm-cli update --check`: how many commits the checkout lacks (a git fetch in a worker thread)."""
        if self.worker.busy:
            return
        manager = self.manager
        self.lbl_check.setText(self._t("update.page.checking"))
        self.worker.run("update-check", manager.check_for_updates, self._checked)

    def _checked(self, new, error) -> None:
        if error is not None:
            self.lbl_check.setText(self._t("update.failed", reason=one_line(str(error))))
            return
        self.lbl_check.setText(self._t("update.page.new", new=int(new or 0)) if new else self._t("update.page.none"))

    def run(self) -> bool:
        """Update Framework...: asks first ("No" is the default), then starts the installer (menus/update.py)."""
        from orchestrator.gui.menus.update import run_update
        started = run_update(self.window_, self.flags(), parent=self)
        self.lbl_result.setText(self._t("update.started") if started else "")
        return started


__all__ = ["UpdatePage"]
