#!/usr/bin/env python3
"""
Build > Targets... (GUI parity W1': `llm-cli targets list` and `llm-cli targets validate` by mouse).

Every hardware target with its ID (the value for --target), name, architecture, boards, formats and support level,
or "no capabilities declared", from BuildService.list_targets, the same query as the CLI. [Check target] runs
TargetManager.validate, the CLI's check, on the selected target (its target.yml read again, so an edited file is
checked as it is now) and shows each schema error by field, or that the target declares no capabilities. The same
check runs after the module wizard created a target and after a Community Hub target install (target_check_text).
Every text is plain text.
"""

from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("bld.targets.col.id", "bld.targets.col.name", "bld.targets.col.arch", "bld.targets.col.boards",
           "bld.targets.col.formats", "bld.targets.col.support")


def _target_manager(framework: Any) -> Any:
    get = getattr(framework, "get_component", None)
    tm = get("target_manager") if callable(get) else None
    return tm if tm is not None else getattr(framework, "target_manager", None)


def declares_capabilities(framework: Any, target_id: str) -> bool:
    """Whether the target's target.yml has a capabilities block, valid or not (an invalid one counts as none for a
    build, but it is not "none declared")."""
    tm = _target_manager(framework)
    data = tm.get_target(target_id) if tm is not None and hasattr(tm, "get_target") else None
    return isinstance(data, dict) and "capabilities" in data


def build_service(framework: Any) -> Any:
    """The framework's build service (the CLI's `targets list` uses the same), or one made for it."""
    service = getattr(framework, "build_service", None)
    if service is None:
        from orchestrator.Core.build_service import BuildService
        service = BuildService(framework)
    return service


def target_check_text(check: Any, lang: Optional[str] = None) -> str:
    """The result of TargetManager.validate as the person reads it (the words of `llm-cli targets validate`)."""
    if not check.found:
        return ui_text("bld.check.not_found", lang, name=one_line(check.name))
    tid = one_line(check.target_id)
    if check.no_capabilities:
        return ui_text("bld.check.no_caps", lang, id=tid)
    if check.errors:
        lines = [ui_text("bld.check.invalid", lang, id=tid)]
        lines += ["  " + ui_text("bld.check.error_line", lang, field=one_line(f), message=one_line(m))
                  for f, m in check.errors]
        return "\n".join(lines)
    return ui_text("bld.check.valid", lang, id=tid, boards=check.boards, formats=one_line(", ".join(check.formats)))


def check_target(framework: Any, name: str) -> Optional[Any]:
    """TargetManager.validate(name) with the target.yml read again; None without a target manager."""
    tm = _target_manager(framework)
    if tm is None or not hasattr(tm, "validate"):
        return None
    return tm.validate(name, reread=True)


def show_target_check(parent: Optional[QWidget], framework: Any, name: str, lang: Optional[str] = None,
                      intro: str = "") -> Optional[Any]:
    """Checks the target `name` and shows the result (warning for an invalid or missing target, information
    otherwise). Returns the TargetCheck (None without a target manager)."""
    lang = lang or current_language()
    check = check_target(framework, name)
    if check is None:
        plain_box(parent, "warning", ui_text("bld.check.title", lang), ui_text("bld.check.unavailable", lang))
        return None
    text = target_check_text(check, lang)
    plain_box(parent, "information" if check.valid else "warning", ui_text("bld.check.title", lang),
              f"{intro}\n\n{text}" if intro else text)
    return check


class TargetsDialog(QDialog):
    """The targets of `framework` as a table; [Check target] checks the selected one, [Reload] reads the targets
    folder again. `select`: the target to select first (the Builder's current one)."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, select: Optional[str] = None,
                 lang: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.targets: List[Any] = []
        self.reloaded = False   # the targets folder was read again (the caller refreshes its lists)
        self.setWindowTitle(self._t("bld.targets.title"))
        self.resize(1000, 420)
        layout = QVBoxLayout(self)
        self.intro = plain_label("")
        layout.addWidget(self.intro)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setObjectName("table_targets")
        self.table.setHorizontalHeaderLabels([self._t(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda _index: self._check())
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_check = QPushButton(button_text(self._t("bld.targets.check")), self)
        self.btn_check.setObjectName("btn_targets_check")
        self.btn_reload = QPushButton(button_text(self._t("bld.targets.reload")), self)
        self.btn_reload.setObjectName("btn_targets_reload")
        # the boards with their optimization profile and benchmark rows (`llm-cli targets boards`)
        self.btn_boards = QPushButton(button_text(self._t("bld.targets.boards")), self)
        self.btn_boards.setObjectName("btn_targets_boards")
        for button, slot in ((self.btn_check, self._check), (self.btn_boards, self._boards),
                             (self.btn_reload, self._reload)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh(select)

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _folder(self) -> str:
        tm = _target_manager(self.framework)
        return str(getattr(tm, "targets_dir", "") or "-")

    def refresh(self, select: Optional[str] = None) -> None:
        """Fills the table from BuildService.list_targets (the CLI's `targets list`)."""
        select = select or self.selected()
        try:
            self.targets = list(build_service(self.framework).list_targets().targets)
        except Exception as e:  # noqa: BLE001 - BuildServiceError or no framework: said in the window
            self.targets = []
            self.intro.setText(self._t("bld.targets.unavailable", reason=one_line(str(e))))
        else:
            self.intro.setText(self._t("bld.targets.intro", n=len(self.targets), path=one_line(self._folder()))
                               if self.targets else self._t("bld.targets.empty", path=one_line(self._folder())))
        self.table.setRowCount(len(self.targets))
        none, invalid = self._t("bld.targets.none_declared"), self._t("bld.targets.invalid")
        for row, target in enumerate(self.targets):
            formats = ", ".join(f.id for f in target.formats)
            support = getattr(target.support_level, "value", target.support_level)
            values = (target.id, target.name or "", target.architecture or "-", ", ".join(target.boards) or "-",
                      formats or (invalid if declares_capabilities(self.framework, target.id) else none),
                      support or "-")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(one_line(value))   # plain text, never rich text
                if column == 0:
                    cell.setData(Qt.UserRole, target.id)
                self.table.setItem(row, column, cell)
            if select is not None and str(target.id).casefold() == str(select).casefold():
                self.table.selectRow(row)
        has = bool(self.targets)
        self.btn_check.setEnabled(has)
        self.btn_boards.setEnabled(has)

    def selected(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _check(self) -> None:
        name = self.selected()
        if not name:
            plain_box(self, "information", self._t("bld.targets.title"), self._t("bld.targets.pick"))
            return
        show_target_check(self, self.framework, name, self.lang)

    def boards_text(self, name: str) -> Optional[str]:
        """The boards of target `name` as `llm-cli targets boards` prints them (board_profiles.board_lines), or None
        when its profiles or benchmarks are not valid."""
        from orchestrator.Core.board_profiles import board_lines
        tm = _target_manager(self.framework)
        data = tm.get_board_data(name) if tm is not None and hasattr(tm, "get_board_data") else None
        if data is None:
            return None
        caps = tm.get_capabilities(name) if hasattr(tm, "get_capabilities") else None
        return "\n".join(one_line(line) if not line.startswith("  ") else "  " + one_line(line)
                         for line in board_lines(caps, data))

    def _boards(self) -> None:
        name = self.selected()
        if not name:
            plain_box(self, "information", self._t("bld.targets.title"), self._t("bld.targets.pick"))
            return
        text = self.boards_text(name)
        plain_box(self, "information" if text is not None else "warning",
                  self._t("bld.targets.boards_title", id=one_line(name)),
                  text if text is not None else self._t("bld.targets.boards_invalid", id=one_line(name)))

    def _reload(self) -> None:
        tm = _target_manager(self.framework)
        if tm is not None and hasattr(tm, "initialize"):
            tm.initialize()
            self.reloaded = True
        self.refresh()


def open_targets_dialog(parent: Optional[QWidget], framework: Any, select: Optional[str] = None,
                        on_reload: Optional[Any] = None) -> Optional[TargetsDialog]:
    """Build > Targets... and the Builder's Targets... button: the window, then `on_reload()` when the person read
    the targets folder again (the caller refreshes its target lists)."""
    try:
        dialog = TargetsDialog(framework, parent, select=select)
    except Exception as e:  # noqa: BLE001 - no framework: said in plain text
        plain_box(parent, "critical", ui_text("bld.targets.title"), one_line(str(e)))
        return None
    dialog.exec()
    if dialog.reloaded and on_reload is not None:
        on_reload()
    return dialog


__all__ = ["COLUMNS", "TargetsDialog", "build_service", "check_target", "declares_capabilities", "open_targets_dialog",
           "show_target_check", "target_check_text"]
