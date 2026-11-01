#!/usr/bin/env python3
"""
Boards > Board bundle... (act_boards_bundle): the board bundle, deploy way 3, by mouse; the same steps as `llmcf
bundle` (orchestrator/runtime/bundle/service.py), with the same checks, questions and audit records.

    Board, preset, building blocks          bundle plan/build DEVICE --preset / --block
    Variables (checked against their type)  --set COMPONENT.NAME=VALUE
    Files (local file or pinned https item) --file / --online --sha256 --size / --licence
    Unprivileged only                       --unprivileged-only
    Plan                                    bundle plan (the full plan in the output pane, nothing written)
    Build...                                bundle build (an own unsigned block after the person's yes)
    Stored bundles                          bundle list
    Dry run on the board / Deploy...        bundle deploy --dry-run / bundle deploy (dry run, then the question)
    Status on the board                     bundle status
    Roll back... / Uninstall...             bundle rollback / bundle uninstall (the installer's list, then the question)
    Delete stored bundles...                bundle delete (ask_deletion with the full list, nothing ticked)
    New block...                            bundle block new

Every plan (it hashes the chosen files), build and board run is one step in a worker thread with a busy line and
Cancel (BoardSteps of gui/boards_window.py, the one step runner); every question has "No" as the default. The board is
reached as its deploy user with the framework's key only (person_steps.connect_stored, never a password). Every text
is plain text in the person's language (utils/l10n_boards.py, keys bundle.*).
"""

from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.boards_window import BoardSteps, _exec, _form_dialog
from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text

VAR_COLUMNS = ("bundle.col.component", "bundle.col.variable", "bundle.col.type", "bundle.col.value")
FILE_COLUMNS = ("bundle.col.component", "bundle.col.role", "bundle.col.formats", "bundle.col.source",
                "bundle.col.sha256", "bundle.col.size", "bundle.col.licence")
STORE_COLUMNS = ("bundle.col.bundle", "bundle.col.board", "bundle.col.components", "bundle.col.mode",
                 "bundle.col.bytes", "bundle.col.created", "bundle.col.last")
# object name -> (text key, slot, what it needs: "" nothing, "board" a chosen board, "bundle" a stored bundle)
BUTTONS = (
    ("btn_bundle_file", "bundle.btn.file", "_on_file", "board"),
    ("btn_bundle_plan", "bundle.btn.plan", "_on_plan", "board"),
    ("btn_bundle_build", "bundle.btn.build", "_on_build", "board"),
    ("btn_bundle_block_new", "bundle.btn.block_new", "_on_block_new", ""),
    ("btn_bundle_refresh", "bundle.btn.refresh", "reload", ""),
    ("btn_bundle_dry_run", "bundle.btn.dry_run", "_on_dry_run", "bundle"),
    ("btn_bundle_deploy", "bundle.btn.deploy", "_on_deploy", "bundle"),
    ("btn_bundle_status", "bundle.btn.status", "_on_status", "bundle"),
    ("btn_bundle_rollback", "bundle.btn.rollback", "_on_rollback", "bundle"),
    ("btn_bundle_root_rollback", "bundle.btn.root_rollback", "_on_root_rollback", "bundle"),
    ("btn_bundle_uninstall", "bundle.btn.uninstall", "_on_uninstall", "bundle"),
    ("btn_bundle_delete", "bundle.btn.delete", "_on_delete", ""),
)
PLAN_ROW = ("btn_bundle_file", "btn_bundle_plan", "btn_bundle_build", "btn_bundle_block_new", "btn_bundle_refresh")
STORE_ROW = ("btn_bundle_dry_run", "btn_bundle_deploy", "btn_bundle_status", "btn_bundle_rollback",
             "btn_bundle_root_rollback", "btn_bundle_uninstall", "btn_bundle_delete")
# the person's question before an action changes the board (service.question's words, translated)
ASK_KEYS = {"apply": "bundle.ask.apply", "uninstall": "bundle.ask.uninstall", "rollback": "bundle.ask.rollback"}
# the person's question before llmcf-install changes the board as root (service.root_question's words, translated)
ROOT_ASK_KEYS = {"apply": "bundle.ask.root_apply", "uninstall": "bundle.ask.root_uninstall",
                 "rollback": "bundle.ask.root_rollback"}


def _choose_file(parent: QWidget, title: str) -> str:
    """The person's choice of a local file (tests replace this)."""
    path, _filter = QFileDialog.getOpenFileName(parent, title)
    return path or ""


class BundleWindow(BoardSteps, QDialog):
    """The board bundle of the person's boards; one step at a time, each off the GUI thread."""

    TITLE_KEY = "bundle.title"

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("dlg_bundle")
        self.framework = framework
        self.lang = lang or current_language()
        self.setWindowTitle(self._t("bundle.title"))
        self.resize(1200, 820)
        self.catalog = None
        self.boards: List[Dict[str, Any]] = []
        self.rows: List[Dict[str, Any]] = []
        self._values: Dict[Tuple[str, str], str] = {}     # what the person typed, kept while the blocks change
        self._files: Dict[Tuple[str, str], Dict[str, str]] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("bundle.intro")))
        top = QHBoxLayout()
        top.addWidget(plain_label(self._t("bundle.board")))
        self.cmb_board = QComboBox(self)
        self.cmb_board.setObjectName("cmb_bundle_board")
        top.addWidget(self.cmb_board, stretch=1)
        top.addWidget(plain_label(self._t("bundle.preset")))
        self.cmb_preset = QComboBox(self)
        self.cmb_preset.setObjectName("cmb_bundle_preset")
        top.addWidget(self.cmb_preset)
        self.chk_unprivileged = QCheckBox(self._t("bundle.unprivileged"), self)
        self.chk_unprivileged.setObjectName("chk_bundle_unprivileged")
        top.addWidget(self.chk_unprivileged)
        # the person's yes to plan a language model on a board that was not measured yet (never an AI's)
        self.chk_unmeasured = QCheckBox(self._t("bundle.unmeasured"), self)
        self.chk_unmeasured.setObjectName("chk_bundle_unmeasured")
        top.addWidget(self.chk_unmeasured)
        layout.addLayout(top)
        self.lbl_boards = plain_label("")
        self.lbl_boards.setObjectName("lbl_bundle_boards")
        layout.addWidget(self.lbl_boards)
        layout.addWidget(plain_label(self._t("bundle.blocks")))
        self.lst_blocks = QListWidget(self)
        self.lst_blocks.setObjectName("lst_bundle_blocks")
        self.lst_blocks.setMaximumHeight(110)
        layout.addWidget(self.lst_blocks)
        layout.addWidget(plain_label(self._t("bundle.vars")))
        self.tbl_vars = self._table("tbl_bundle_vars", VAR_COLUMNS)
        layout.addWidget(self.tbl_vars, stretch=1)
        self.lbl_var_check = plain_label("")
        self.lbl_var_check.setObjectName("lbl_bundle_var_check")
        layout.addWidget(self.lbl_var_check)
        layout.addWidget(plain_label(self._t("bundle.files")))
        self.tbl_files = self._table("tbl_bundle_files", FILE_COLUMNS)
        layout.addWidget(self.tbl_files, stretch=1)
        self.buttons: Dict[str, QPushButton] = {}
        for name, key, slot, _need in BUTTONS:
            button = QPushButton(button_text(self._t(key)), self)
            button.setObjectName(name)
            button.setAutoDefault(False)
            button.clicked.connect(getattr(self, slot))
            self.buttons[name] = button
        layout.addLayout(self._row(PLAN_ROW))
        layout.addWidget(plain_label(self._t("bundle.store")))
        self.tbl_store = self._table("tbl_bundle_store", STORE_COLUMNS, editable=False)
        self.tbl_store.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.tbl_store, stretch=1)
        layout.addLayout(self._row(STORE_ROW))
        busy = QHBoxLayout()
        self.lbl_busy = plain_label("")
        self.lbl_busy.setObjectName("lbl_bundle_busy")
        self.btn_cancel = QPushButton(button_text(self._t("bundle.btn.cancel")), self)
        self.btn_cancel.setObjectName("btn_bundle_cancel")
        self.btn_cancel.setAutoDefault(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setVisible(False)
        busy.addWidget(self.lbl_busy, stretch=1)
        busy.addWidget(self.btn_cancel)
        layout.addLayout(busy)
        self.output = QPlainTextEdit(self)   # the plan and the board's report: plain text by nature
        self.output.setObjectName("txt_bundle_output")
        self.output.setReadOnly(True)
        layout.addWidget(self.output, stretch=2)
        close = QDialogButtonBox(QDialogButtonBox.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._init_steps()
        self.cmb_board.currentIndexChanged.connect(self._update_buttons)
        self.cmb_preset.currentIndexChanged.connect(self._fill_components)
        self.lst_blocks.itemChanged.connect(self._fill_components)
        self.tbl_vars.itemChanged.connect(self._var_changed)
        self.tbl_files.itemChanged.connect(self._file_changed)
        self._update_buttons()

    # --- layout helpers -------------------------------------------------------------------------------------------

    def _table(self, name: str, columns: Tuple[str, ...], editable: bool = True) -> QTableWidget:
        table = QTableWidget(0, len(columns), self)
        table.setObjectName(name)
        table.setHorizontalHeaderLabels([self._t(c) for c in columns])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        if not editable:
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _row(self, names: Tuple[str, ...]) -> QHBoxLayout:
        line = QHBoxLayout()
        for name in names:
            line.addWidget(self.buttons[name])
        line.addStretch(1)
        return line

    @staticmethod
    def _cell(text: Any, editable: bool = False, data: Any = None) -> QTableWidgetItem:
        item = QTableWidgetItem(one_line(str(text)))   # plain text, never rich text
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        if data is not None:
            item.setData(Qt.UserRole, data)
        return item

    # --- what the window reads (files only; no board is contacted) ---------------------------------------------------

    def _sources(self):
        from orchestrator.runtime.bundle import service
        return service.catalog_sources(self.framework)

    def reload(self) -> None:
        """Reads the boards, the building blocks and the stored bundles again."""
        from orchestrator.runtime.bundle import blocks as blk
        from orchestrator.runtime.bundle import generator as gen
        from orchestrator.runtime.bundle import service
        from orchestrator.runtime.device.person_steps import boards_overview
        app_root, index = self._sources()
        self.catalog = blk.load_catalog(app_root, index=index)
        try:
            self.boards = [e for e in boards_overview(self.state_dir) if e.get("stored") is not None]
            self.rows = service.bundle_rows(gen.BundleStore(self.state_dir))
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder: shown as plain text
            self.boards, self.rows = [], []
            self._say(self._t("boards.err.failed", reason=one_line(str(e))))
        chosen = self.cmb_board.currentData()
        self.cmb_board.blockSignals(True)
        self.cmb_board.clear()
        for entry in self.boards:
            self.cmb_board.addItem(f"{entry['label']} ({entry['device_id']})", entry["device_id"])
        if chosen is not None and self.cmb_board.findData(chosen) >= 0:
            self.cmb_board.setCurrentIndex(self.cmb_board.findData(chosen))
        self.cmb_board.blockSignals(False)
        self.lbl_boards.setText("" if self.boards else self._t("bundle.board.none"))
        self._fill_presets()
        self._fill_blocks()
        self._fill_store()
        for note in self.catalog.notes:
            self._say(note)
        self._fill_components()

    def _fill_presets(self) -> None:
        from orchestrator.runtime.bundle.generator import PRESETS
        chosen = self.cmb_preset.currentData()
        self.cmb_preset.blockSignals(True)
        self.cmb_preset.clear()
        self.cmb_preset.addItem(self._t("bundle.preset.none"), "")
        for name, blocks in PRESETS.items():
            self.cmb_preset.addItem(f"{name} ({', '.join(blocks)})", name)
        if chosen and self.cmb_preset.findData(chosen) >= 0:
            self.cmb_preset.setCurrentIndex(self.cmb_preset.findData(chosen))
        self.cmb_preset.blockSignals(False)

    def _fill_blocks(self) -> None:
        checked = set(self._checked_blocks())
        self.lst_blocks.blockSignals(True)
        self.lst_blocks.clear()
        for block in self.catalog.blocks.values():
            key = "bundle.block.row" if block.ready else "bundle.block.template"
            item = QListWidgetItem(one_line(self._t(key, title=block.title, id=block.id, kind=block.kind,
                                                    trust=block.trust)))
            item.setData(Qt.UserRole, block.id)
            if block.ready:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if block.id in checked else Qt.Unchecked)
            else:   # a documented template is listed, never chosen
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.lst_blocks.addItem(item)
        self.lst_blocks.blockSignals(False)

    def _fill_store(self) -> None:
        from orchestrator.Core.deletion import size_text
        chosen = self.selected_bundle()
        self.tbl_store.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            last = "; ".join(f"{d}: {b['last']}" for d, b in row["boards"].items()) or self._t("boards.dash")
            cells = (row["bundle_id"], row["device_id"],
                     ", ".join(f"{c['id']} ({c['block']})" for c in row["components"]), row["mode"],
                     size_text(row["bytes"], self.lang), row["created"], last)
            for c, value in enumerate(cells):
                self.tbl_store.setItem(r, c, self._cell(value, data=row["bundle_id"] if c == 0 else None))
            if row["bundle_id"] == chosen:
                self.tbl_store.selectRow(r)
        self._update_buttons()

    # --- the person's choice ----------------------------------------------------------------------------------------

    def _checked_blocks(self) -> List[str]:
        out = []
        for i in range(self.lst_blocks.count()):
            item = self.lst_blocks.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.data(Qt.UserRole))
        return out

    def chosen_blocks(self) -> List[str]:
        """The blocks in order: the preset's, then the ticked ones (as selection_from takes them)."""
        from orchestrator.runtime.bundle.generator import PRESETS
        preset = self.cmb_preset.currentData() or ""
        out = list(PRESETS.get(preset, ()))
        out += [b for b in self._checked_blocks() if b not in out]
        return out

    def _components(self) -> List[Tuple[str, Any]]:
        from orchestrator.runtime.bundle.generator import component_ids
        blocks = [self.catalog.get(b) for b in self.chosen_blocks() if self.catalog and self.catalog.get(b)]
        return list(zip(component_ids(blocks), blocks))

    def _fill_components(self, *_args) -> None:
        """The variables and files of the chosen blocks, with what the person typed kept."""
        if self.catalog is None:
            return
        comps = self._components()
        self.tbl_vars.blockSignals(True)
        self.tbl_files.blockSignals(True)
        var_rows = [(cid, block, var) for cid, block in comps for var in block.variables]
        self.tbl_vars.setRowCount(len(var_rows))
        for r, (cid, _block, var) in enumerate(var_rows):
            value = self._values.get((cid, var.name), "" if var.default is None else str(var.default))
            for c, text in enumerate((cid, var.name, var.type + (f" ({', '.join(var.choices)})" if var.choices
                                                                 else ""))):
                self.tbl_vars.setItem(r, c, self._cell(text, data=(cid, var.name) if c == 0 else None))
            self.tbl_vars.setItem(r, 3, self._cell(value, editable=True))
        file_rows = [(cid, need) for cid, block in comps for need in block.files if need.source != "runtime"]
        self.tbl_files.setRowCount(len(file_rows))
        for r, (cid, need) in enumerate(file_rows):
            given = self._files.get((cid, need.role), {})
            fixed = (cid, need.role, ", ".join(need.formats))
            for c, text in enumerate(fixed):
                self.tbl_files.setItem(r, c, self._cell(text, data=(cid, need.role) if c == 0 else None))
            for c, key in enumerate(("source", "sha256", "size", "licence"), start=3):
                self.tbl_files.setItem(r, c, self._cell(given.get(key, ""), editable=True))
        self.tbl_vars.blockSignals(False)
        self.tbl_files.blockSignals(False)
        self._update_buttons()

    def _var_changed(self, item: QTableWidgetItem) -> None:
        """A value the person typed, checked against its variable's type at once (the plan checks it again)."""
        if item.column() != 3:
            return
        cid, name = self.tbl_vars.item(item.row(), 0).data(Qt.UserRole)
        text = item.text().strip()
        self._values[(cid, name)] = text
        block = dict(self._components()).get(cid)
        var = block.variable(name) if block is not None else None
        if var is None or not text:
            self.lbl_var_check.setText("")
            return
        from orchestrator.runtime.bundle.manifest import ManifestError
        try:
            value = var.check(text)
        except ManifestError as e:
            self.lbl_var_check.setText(self._t("bundle.var.bad", component=cid, name=name, reason=one_line(str(e))))
            return
        self.lbl_var_check.setText(self._t("bundle.var.ok", component=cid, name=name, value=value))

    def _file_changed(self, item: QTableWidgetItem) -> None:
        if item.column() < 3:
            return
        cid, role = self.tbl_files.item(item.row(), 0).data(Qt.UserRole)
        key = ("source", "sha256", "size", "licence")[item.column() - 3]
        self._files.setdefault((cid, role), {})[key] = item.text().strip()

    def _on_file(self) -> None:
        """Choose file...: a local file for the selected file row."""
        rows = {i.row() for i in self.tbl_files.selectionModel().selectedRows()}
        if len(rows) != 1:
            self._info(self._t("bundle.need_file_row"))
            return
        path = _choose_file(self, self._t("bundle.btn.file"))
        if path:
            self.tbl_files.item(rows.pop(), 3).setText(path)   # itemChanged keeps it

    def selection(self):
        """The person's choice as the generator takes it (service.selection_from, the same reader as the CLI's
        options): gen.BundleError with the reason when a value is not of its form."""
        from orchestrator.runtime.bundle import service
        sets = [f"{c}.{n}={v}" for (c, n), v in self._values.items() if v != ""]
        files, online, sha, sizes, licences = [], [], [], [], []
        for (cid, role), given in self._files.items():
            source = given.get("source", "")
            if source.startswith(("https://", "http://")):
                online.append(f"{cid}.{role}={source}")
            elif source:
                files.append(f"{cid}.{role}={source}")
            if given.get("sha256"):
                sha.append(f"{cid}.{role}={given['sha256']}")
            if given.get("size"):
                sizes.append(f"{cid}.{role}={given['size']}")
            if given.get("licence"):
                licences.append(f"{cid}.{role}={given['licence']}")
        known = {cid for cid, _b in self._components()}
        keep = (lambda items: [i for i in items if i.split(".", 1)[0] in known])
        preset = self.cmb_preset.currentData() or None
        return service.selection_from(preset=preset, blocks=self._checked_blocks(), sets=keep(sets),
                                      files=keep(files), online=keep(online), sha256=keep(sha), sizes=keep(sizes),
                                      licences=keep(licences), unprivileged_only=self.chk_unprivileged.isChecked())

    def selected_board(self) -> Optional[str]:
        return self.cmb_board.currentData()

    def selected_bundle(self) -> Optional[str]:
        rows = {i.row() for i in self.tbl_store.selectionModel().selectedRows()}
        if len(rows) != 1:
            return None
        item = self.tbl_store.item(rows.pop(), 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _update_buttons(self) -> None:
        running = self._token is not None
        have = {"": True, "board": self.selected_board() is not None, "bundle": self.selected_bundle() is not None}
        for name, _key, _slot, need in BUTTONS:
            self.buttons[name].setEnabled(have[need] and (not running or name == "btn_bundle_refresh"))

    # --- plan and build -----------------------------------------------------------------------------------------------

    def _plan_step(self, allow_own: bool = False):
        """(work, device_id) of a plan in the worker thread (it hashes the chosen files), or None after telling why."""
        from orchestrator.Core.config_manager import framework_mode
        from orchestrator.runtime.bundle import service
        from orchestrator.runtime.bundle.generator import BundleError
        device_id = self.selected_board()
        if device_id is None:
            self._info(self._t("bundle.need_board"))
            return None
        try:
            selection = self.selection()
        except BundleError as e:
            self._info(self._t("boards.err.failed", reason=one_line(str(e))), "warning")
            return None
        state, config = self.state_dir, getattr(self.framework, "config", None)
        app_root, index = self._sources()
        unmeasured_ok = self.chk_unmeasured.isChecked()

        def work():
            plan = service.plan_for_device(state, device_id, selection, config=config, app_root=app_root,
                                           mode=framework_mode(config), allow_own=allow_own, index=index,
                                           unmeasured_ok=unmeasured_ok)
            self._check_cancel()
            return plan
        return work

    def _show_plan(self, plan: Any) -> None:
        self.output.clear()
        self._say("\n".join(plan.lines()))

    def _on_plan(self) -> None:
        work = self._plan_step()
        if work is not None:
            self._start(self._t("bundle.planning"), work, self._show_plan)

    def _on_build(self, allow_own: bool = False) -> None:
        work = self._plan_step(allow_own)
        if work is not None:
            self._start(self._t("bundle.planning"), work, lambda plan: self._planned_for_build(plan, allow_own))

    def _planned_for_build(self, plan: Any, allow_own: bool) -> None:
        self._show_plan(plan)
        only_own = plan.own_blocks and not allow_own and all("is not signed: it is used only after your yes" in p
                                                             for p in plan.problems)
        if only_own:
            if ask(self, self._t("bundle.title"), self._t("bundle.own.ask", blocks=", ".join(plan.own_blocks))):
                self._on_build(allow_own=True)
            else:
                self._say(self._t("bundle.not_built"))
            return
        if not plan.ok:
            self._info(self._t("bundle.not_built"), "warning")
            return
        from orchestrator.runtime.bundle import service
        from orchestrator.runtime.bundle.generator import BundleStore
        from orchestrator.runtime.device import board_ops
        store, framework = BundleStore(self.state_dir), self.framework

        def build():
            # a bundle with root actions carries llmcf-install for its board (service.build_for, as the CLI)
            return service.build_for(plan, store, board_ops.framework_signer(framework), framework=framework,
                                     say=self._progress)

        self._start(self._t("bundle.building"), build, self._built)

    def _built(self, built: Any) -> None:
        text = self._t("bundle.built", bundle=built.bundle_id, device_id=built.manifest.board.device_id)
        self._say(text)
        self.reload()
        for r, row in enumerate(self.rows):
            if row["bundle_id"] == built.bundle_id:
                self.tbl_store.selectRow(r)
        self._info(text)

    # --- the bundle on its board -----------------------------------------------------------------------------------

    def _bundle(self):
        from orchestrator.runtime.bundle.generator import BundleError, BundleStore
        bundle_id = self.selected_bundle()
        if bundle_id is None:
            self._info(self._t("bundle.need_bundle"))
            return None
        store = BundleStore(self.state_dir)
        try:
            return store, store.get(bundle_id)
        except BundleError as e:
            self._info(self._t("boards.err.failed", reason=one_line(str(e))), "warning")
            return None

    def _opener(self, built: Any):
        """(open_session, host, port, user, label) of the bundle's board, the person's login with the framework's
        key only (person_steps.connect_stored, no password for the login); None after telling the person why not."""
        from orchestrator.runtime.bundle import service
        from orchestrator.runtime.bundle.generator import BundleError
        from orchestrator.runtime.device import person_steps
        state, device_id = self.state_dir, built.manifest.board.device_id
        try:
            host, port, user, label = service.board_contact(state, device_id)
        except BundleError as e:
            self._info(self._t("boards.err.failed", reason=one_line(str(e))), "warning")
            return None

        def open_session():
            client, _key = person_steps.connect_stored(state, device_id, host, port, user, None, step="bundle",
                                                       on_client=self._keep_client, cancel=self._cancel,
                                                       on_event=self._board_lines(host), signer=self._signer(),
                                                       mode=self._mode())
            self._check_cancel()
            return client
        return open_session, host, port, user, label

    def _run(self, action: str, then=None) -> None:
        """Runs the installer `action` of the selected bundle on its board in the worker thread, through the person's
        login with the framework's key only (person_steps.connect_stored, no password); `then(run)` afterwards. An
        apply that stops before the root actions goes on with llmcf-install (_root: its plan, a second question);
        a status adds llmcf-install's status."""
        from orchestrator.runtime.bundle import service
        found = self._bundle()
        if found is None:
            return
        store, built = found
        if action == "apply" or (then is not None and action == service.CHANGING_ACTIONS["apply"]):
            revoked = service.revoked_blocks(built, *self._sources())   # nothing is sent (service.revoked_blocks)
            if revoked:
                self._info(self._t("bundle.revoked", reason=one_line("; ".join(revoked))), "warning")
                return
        opener = self._opener(built)
        if opener is None:
            return
        open_session, host, port, user, label = opener
        device_id = built.manifest.board.device_id

        def work():
            return service.run_on_board(open_session, built, action, store=store, device_id=device_id,
                                        say=self._progress)

        def done(run: Any) -> None:
            self._say("\n".join(run.lines()))
            if run.staging and action == "apply":
                self._say(self._t("bundle.staging"))
            self.reload()
            if run.report.result == "stopped-root":
                if built.needs_root:
                    self._say(self._t("bundle.root"))
                    self._root("apply", built)
                else:
                    self._say(self._t("bundle.root.none"))
            elif action == "status" and built.needs_root and run.report.result in ("installed", "partial"):
                self._root("status", built, ask_first=False)
            if then is not None:
                then(run, built, label)

        self._start(self._t("bundle.running", action=action), work, done,
                    failed=lambda e: self._show_error(e, address=host, port=port, user=user,
                                                      device_id=device_id) or True)

    def _ask_password(self) -> str:
        """The board's sudo password for this one run of llmcf-install (private mode only; never shown, never kept)."""
        dialog, form = _form_dialog(self, self._t("bundle.title"), self._t("bundle.root.password_intro"))
        txt = QLineEdit(dialog)
        txt.setObjectName("txt_bundle_sudo_password")
        txt.setEchoMode(QLineEdit.Password)
        form.addRow(self._t("bundle.root.password"), txt)
        return txt.text() if _exec(dialog) else ""

    def _root(self, action: str, built: Any, *, ask_first: bool = True, then=None, password: str = "") -> None:
        """llmcf-install `action` of the bundle on its board (service.run_root_on_board, the gate operation
        bundle-root through sudo -n) in the worker thread: with `ask_first` first its plan of that action, then the
        person's question ("No" is the default), then the action; `then()` afterwards. When the board's sudo asks for
        a password, private mode asks the person for it once (sent on stdin for sudo -S, never kept); enterprise mode
        says it runs root actions with sudo -n only. Only the person starts this, never an AI."""
        from orchestrator.runtime.bundle import service
        opener = self._opener(built)
        if opener is None:
            return
        open_session, host, port, user, label = opener
        device_id = built.manifest.board.device_id
        first_action = service.ROOT_CHANGING_ACTIONS[action] if ask_first else action

        def run_step(what: str, pw: str, after) -> None:
            def work():
                return service.run_root_on_board(open_session, built, what, password=pw, say=self._progress)

            def done(run: Any) -> None:
                if run.report.needs_password and not pw:
                    if not service.password_allowed(self._mode()):
                        self._info(service.ENTERPRISE_NO_PASSWORD, "warning")
                        return
                    typed = self._ask_password()
                    if not typed:
                        self._say(self._t("bundle.unchanged"))
                        return
                    run_step(what, typed, after)
                    return
                self._say("\n".join(run.lines()))
                after(run, pw)

            self._start(self._t("bundle.root.running", action=what), work, done,
                        failed=lambda e: self._show_error(e, address=host, port=port, user=user,
                                                          device_id=device_id) or True)

        def planned(first: Any, pw: str) -> None:
            if not ask_first:
                return
            if not first.ok:
                self._info(self._t("bundle.root.plan_failed"), "warning")
                return
            if action != "apply" and first.report.result == "absent":
                if then is not None:
                    then()
                return
            if not ask(self, self._t("bundle.title"), self._t(ROOT_ASK_KEYS[action], bundle=built.bundle_id,
                                                              board=label, count=service.root_count(first))):
                self._say(self._t("bundle.root.unchanged"))
                return

            def finished(run: Any, _pw: str) -> None:
                if not run.ok and run.report.result == "failed":
                    self._info(self._t("bundle.root.failed"), "warning")
                    return
                if then is not None and run.ok:
                    then()
            run_step(action, pw, finished)

        run_step(first_action, password, planned)

    def _changing(self, action: str) -> None:
        """Deploy... / Roll back... / Uninstall...: first what the installer shows without changing anything, then
        the person's question ("No" is the default), then the action."""
        from orchestrator.runtime.bundle import service

        def asked(first: Any, built: Any, label: str) -> None:
            if not first.ok:
                self._info(self._t("bundle.dry_failed"), "warning")
                return
            size = sum(entry.size for _n, _p, entry in built.payload())
            from orchestrator.Core.deletion import size_text
            if not ask(self, self._t("bundle.title"), self._t(ASK_KEYS[action], bundle=built.bundle_id, board=label,
                                                              size=size_text(size, self.lang))):
                self._say(self._t("bundle.unchanged"))
                return
            self._run(action)

        self._run(service.CHANGING_ACTIONS[action], asked)

    def _on_dry_run(self) -> None:
        self._run("plan")

    def _on_status(self) -> None:
        self._run("status")

    def _on_deploy(self) -> None:
        self._changing("apply")

    def _on_rollback(self) -> None:
        self._changing("rollback")

    def _on_root_rollback(self) -> None:
        """Roll back root actions...: llmcf-install's last run (its journal), after its status and the question."""
        found = self._bundle()
        if found is None:
            return
        _store, built = found
        if not built.needs_root:
            self._info(self._t("bundle.root.none_here"))
            return
        self._root("rollback", built)

    def _on_uninstall(self) -> None:
        """Uninstall...: first what llmcf-install made as root (its journal's list, its own question), then the
        installer's own steps; the root part staying (a No) keeps the installer the board kept too."""
        found = self._bundle()
        if found is None:
            return
        _store, built = found
        if built.needs_root:
            self._root("uninstall", built, then=lambda: self._changing("uninstall"))
            return
        self._changing("uninstall")

    # --- the stored bundles ------------------------------------------------------------------------------------------

    def _on_delete(self) -> None:
        """Delete stored bundles...: the full list through ask_deletion (nothing ticked, "No"), into the trash or for
        good, through Core/deletion.py (BundleStore.delete); the honest report afterwards."""
        from orchestrator.runtime.bundle.generator import BundleStore
        store = BundleStore(self.state_dir)
        self._start(self._t("bundle.delete.reading"), store.deletion_plan, lambda plan: self._ask_delete(store, plan))

    def _ask_delete(self, store: Any, plan: Any) -> None:
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        if plan is None:
            self._info(self._t("bundle.delete.none"))
            return
        title = self._t("bundle.delete.title")
        accepted = ask_deletion(self, plan, multiple=True, title=title, intro=self._t("bundle.delete.intro"),
                                lang=self.lang)
        if accepted is None:
            return
        state, framework, audit = self.state_dir, self.framework, self.audit

        def delete():
            return store.delete(accepted, state, trash=Trash.for_framework(framework), audit=audit)

        def failed(error: BaseException) -> bool:
            if isinstance(error, PlanChanged):
                self._info(self._t("deletion.changed"), "warning")
                return True
            if isinstance(error, (PermissionError, RuntimeError)):
                self._info(self._t("deletion.failed", reason=one_line(str(error))), "warning")
                return True
            return False

        self._start(self._t("bundle.delete.deleting"), delete,
                    lambda result: self._deletion_result(result, accepted.mode, title), failed=failed)

    # --- an own building block ---------------------------------------------------------------------------------------

    def _on_block_new(self) -> None:
        """New block...: the person's own block (block.yml and its guide) in community/blocks/, as bundle block new."""
        from orchestrator.runtime.bundle import blocks as blk
        from orchestrator.runtime.bundle.manifest import KINDS
        app_root, _index = self._sources()
        if app_root is None:
            self._info(self._t("bundle.new.no_folder"), "warning")
            return
        dialog, form = _form_dialog(self, self._t("bundle.new.title"), self._t("bundle.new.intro"))
        txt_id = QLineEdit(dialog)
        txt_id.setObjectName("txt_bundle_new_id")
        cmb_kind = QComboBox(dialog)
        cmb_kind.setObjectName("cmb_bundle_new_kind")
        cmb_kind.addItems(list(KINDS))
        txt_title = QLineEdit(dialog)
        txt_title.setObjectName("txt_bundle_new_title")
        txt_summary = QLineEdit(dialog)
        txt_summary.setObjectName("txt_bundle_new_summary")
        cmb_from = QComboBox(dialog)
        cmb_from.setObjectName("cmb_bundle_new_from")
        cmb_from.addItem(self._t("bundle.new.from.none"), "")
        for block in (self.catalog.blocks.values() if self.catalog else ()):
            if block.trust == "framework":
                cmb_from.addItem(f"{block.title} ({block.id})", block.id)
        for key, widget in (("bundle.new.id", txt_id), ("bundle.new.kind", cmb_kind),
                            ("bundle.new.title_field", txt_title), ("bundle.new.summary", txt_summary),
                            ("bundle.new.from", cmb_from)):
            form.addRow(self._t(key), widget)
        if not _exec(dialog):
            return
        try:
            folder = blk.new_block(blk.community_dir(app_root), txt_id.text().strip(), kind=cmb_kind.currentText(),
                                   title=txt_title.text().strip(), summary=txt_summary.text().strip(),
                                   template=cmb_from.currentData() or None)
        except (blk.BlockError, OSError) as e:
            self._info(self._t("boards.err.failed", reason=one_line(str(e))), "warning")
            return
        text = self._t("bundle.new.done", id=txt_id.text().strip(), path=str(folder))
        self._say(text)
        self.reload()
        self._info(text)


def open_bundle(window: Any) -> Optional[BundleWindow]:
    """Boards > Board bundle...: shows the window of `window` (the main window), with fresh data."""
    dialog = getattr(window, "bundle_window", None)
    if dialog is None:
        dialog = BundleWindow(window.framework, window)
        window.bundle_window = dialog
    try:
        dialog.reload()
    except Exception as e:  # noqa: BLE001 - e.g. no per-user folder; plain text
        plain_box(window, "critical", ui_text("bundle.title"), str(e))
        return None
    dialog.show()
    dialog.raise_()
    return dialog


__all__ = ["BUTTONS", "BundleWindow", "open_bundle"]
