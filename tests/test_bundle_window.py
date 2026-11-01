"""Boards > Board bundle... (orchestrator/gui/bundle_window.py): `llmcf bundle` by mouse. Offscreen: presets, the block
picker and the variable editor with its type check, the plan in the output pane, the build, the deploy, uninstall and
rollback with the installer's list first and a question whose default is "No", the deletion of stored bundles through
ask_deletion, every step off the GUI thread, and every text in English and German.

The board is the fake board of tests/test_board_bundle.py behind llmcf's real gate (POSIX only); no network, no
keyring, no SSH. Every name and address is a test value."""

import threading
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from test_board_bundle import DEVICE, POSIX, VERIFIER, GateShell, _fake_board, _files  # noqa: E402
from test_boards_window import Boxes, framework, wait  # noqa: E402
from test_bundle_ui import _installed, _stored_board  # noqa: E402


@pytest.fixture
def bundle(qapp, tmp_path, monkeypatch):
    """The window over a stored board, its fake board behind the gate and the framework's signer of that board."""
    from orchestrator.gui.bundle_window import BundleWindow
    from orchestrator.runtime.bundle import service
    from orchestrator.runtime.device import board_ops, person_steps, remote
    fw = framework(tmp_path)
    state = Path(fw.config.user_state_dir)
    _stored_board(state)
    board = _fake_board(tmp_path, "root")
    logins = []

    def connect_stored(state_dir, device_id, host, port, user, password=None, **kw):
        logins.append((device_id, host, port, user, password, kw.get("step"),
                       threading.current_thread() is threading.main_thread()))
        return GateShell(board), "key"
    monkeypatch.setattr(person_steps, "connect_stored", connect_stored)
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    monkeypatch.setattr(board_ops, "framework_signer", lambda framework=None, state_dir=None: board.signer)
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (tmp_path / "app", None))
    dialog = BundleWindow(fw, lang="en")
    dialog.reload()
    board.logins, board.state = logins, state
    board.model, board.wmodel, board.prog = _files(tmp_path)
    yield dialog, board
    if dialog.running:
        dialog._on_cancel()
        wait(qapp, dialog)
    dialog.deleteLater()
    qapp.processEvents()


def _pick_stt(dialog, board):
    dialog.cmb_preset.setCurrentIndex(dialog.cmb_preset.findData("stt"))
    files = {}
    for r in range(dialog.tbl_files.rowCount()):
        files[dialog.tbl_files.item(r, 0).data(0x0100)[1]] = r   # Qt.UserRole
    dialog.tbl_files.item(files["model"], 3).setText(str(board.wmodel))
    dialog.tbl_files.item(files["model"], 6).setText("MIT")
    dialog.tbl_files.item(files["program"], 3).setText(str(board.prog))


def test_the_window_lists_the_board_presets_and_blocks_templates_not_choosable(bundle):
    from PySide6.QtCore import Qt
    dialog, _board = bundle
    assert dialog.cmb_board.count() == 1 and dialog.selected_board() == DEVICE
    assert dialog.cmb_preset.findData("voice") >= 0
    states = {dialog.lst_blocks.item(i).data(Qt.UserRole): bool(dialog.lst_blocks.item(i).flags() & Qt.ItemIsEnabled)
              for i in range(dialog.lst_blocks.count())}
    assert states["stt-whisper-cpp"] and not states["tts-sherpa-onnx"]


def test_the_variable_editor_checks_each_value_against_its_type(bundle):
    dialog, board = bundle
    _pick_stt(dialog, board)
    rows = {dialog.tbl_vars.item(r, 1).text(): r for r in range(dialog.tbl_vars.rowCount())}
    assert "port" in rows and "autostart" in rows
    dialog.tbl_vars.item(rows["port"], 3).setText("8080; rm -rf ~")
    assert "stt.port:" in dialog.lbl_var_check.text() and "=" not in dialog.lbl_var_check.text().split(":", 1)[0]
    dialog.tbl_vars.item(rows["port"], 3).setText("8091")
    assert dialog.lbl_var_check.text() == "stt.port = 8091"
    assert dialog.selection().values["stt"]["port"] == "8091"


def test_plan_shows_the_full_plan_off_the_gui_thread_and_builds_nothing(qapp, bundle):
    dialog, board = bundle
    _pick_stt(dialog, board)
    dialog.buttons["btn_bundle_plan"].click()
    wait(qapp, dialog)
    text = dialog.output.toPlainText()
    assert "stt: block stt-whisper-cpp" in text and f"from {board.prog}" in text and "check memory:" in text
    assert dialog.rows == [] and board.logins == []


@POSIX
@VERIFIER
def test_build_deploy_and_uninstall_ask_first_with_no_as_the_default(qapp, bundle, monkeypatch):
    dialog, board = bundle
    _pick_stt(dialog, board)
    boxes = Boxes(monkeypatch, "no")
    dialog.buttons["btn_bundle_build"].click()
    wait(qapp, dialog)
    assert len(dialog.rows) == 1, dialog.output.toPlainText()
    bid = dialog.rows[0]["bundle_id"]
    dialog.tbl_store.selectRow(0)
    dialog.buttons["btn_bundle_deploy"].click()
    wait(qapp, dialog)                      # the dry run, then the question
    asked = boxes.questions()
    assert asked and asked[-1].default_is_no and "Send bundle" in asked[-1].text and "ZERO 3W" in asked[-1].text
    assert not _installed(board, bid).exists() and "Nothing was changed" in dialog.output.toPlainText()
    boxes.answer = "yes"
    dialog.tbl_store.selectRow(0)
    dialog.buttons["btn_bundle_deploy"].click()
    wait(qapp, dialog)
    wait(qapp, dialog)                      # the apply after the yes
    assert _installed(board, bid).is_file(), dialog.output.toPlainText()
    assert all(login[4] is None and login[5] == "bundle" and not login[6] for login in board.logins), \
        "the person's login with the key only, off the GUI thread"
    boxes.answer = "no"
    dialog.tbl_store.selectRow(0)
    dialog.buttons["btn_bundle_uninstall"].click()
    wait(qapp, dialog)
    assert boxes.questions()[-1].default_is_no and _installed(board, bid).is_file()
    assert "would-remove" in dialog.output.toPlainText()


def test_delete_goes_through_ask_deletion_and_core_deletion(qapp, bundle, monkeypatch, tmp_path):
    from orchestrator.Core import trash as trash_module
    from orchestrator.Core.deletion import accept, choose
    from orchestrator.gui import deletion_dialog
    from orchestrator.runtime.bundle import generator as gen
    from test_board_bundle import _plan, _signer
    dialog, _board = bundle
    built = gen.build_bundle(_plan(tmp_path, bundle_id="one-test"), gen.BundleStore(dialog.state_dir), _signer())
    shown = []

    def ask_deletion(parent, plan, **kw):
        shown.append((plan, kw))
        return None
    monkeypatch.setattr(deletion_dialog, "ask_deletion", ask_deletion)
    dialog.buttons["btn_bundle_delete"].click()
    wait(qapp, dialog)
    assert shown and shown[0][1]["multiple"] and shown[0][0].items[0].full_path == str(built.path)
    assert built.path.is_dir(), "No: nothing goes"
    monkeypatch.setattr(deletion_dialog, "ask_deletion",
                        lambda parent, plan, **kw: accept(choose(plan, [0]), "gui", "permanent"))
    monkeypatch.setattr(trash_module.Trash, "for_framework", classmethod(lambda cls, fw: None))
    Boxes(monkeypatch)
    dialog.buttons["btn_bundle_delete"].click()
    wait(qapp, dialog)
    wait(qapp, dialog)
    assert not built.path.exists() and "one-test" in dialog.output.toPlainText()


def test_new_block_makes_an_own_block_in_community_blocks(bundle, monkeypatch, tmp_path):
    from orchestrator.gui import bundle_window

    def fill(dialog):
        from PySide6.QtWidgets import QComboBox, QLineEdit
        dialog.findChild(QLineEdit, "txt_bundle_new_id").setText("stt-mine")
        dialog.findChild(QLineEdit, "txt_bundle_new_title").setText("Mine")
        kind = dialog.findChild(QComboBox, "cmb_bundle_new_kind")
        kind.setCurrentText("stt")
        src = dialog.findChild(QComboBox, "cmb_bundle_new_from")
        src.setCurrentIndex(src.findData("stt-whisper-cpp"))
        return True
    monkeypatch.setattr(bundle_window, "_exec", fill)
    Boxes(monkeypatch)
    window, _board = bundle
    window.buttons["btn_bundle_block_new"].click()
    assert (tmp_path / "app" / "community" / "blocks" / "stt-mine" / "block.yml").is_file()
    assert window.catalog.get("stt-mine").trust == "own"


def test_every_bundle_text_exists_in_english_and_german_and_names_no_command():
    import re
    from orchestrator.utils.l10n_boards import TEXTS
    en = {k: v for k, v in TEXTS["en"].items() if k.startswith("bundle.") or k == "boards.menu.bundle"}
    de = {k: v for k, v in TEXTS["de"].items() if k.startswith("bundle.") or k == "boards.menu.bundle"}
    assert set(en) == set(de) and len(en) > 60
    for key, text in {**en, **de}.items():
        assert not re.search(r"\bllm(?:-cli|cf) ", text), key   # the GUI never tells a person to type a command
        assert set(re.findall(r"\{(\w+)\}", en[key])) == set(re.findall(r"\{(\w+)\}", de[key])), key


def test_every_object_the_bundle_paths_name_is_in_the_window(bundle):
    from PySide6.QtWidgets import QWidget
    from orchestrator.gui.menus import boards
    dialog, _board = bundle
    for name in {n for n, opener in boards.WINDOW_OBJECTS.items() if opener == boards.BUNDLE_ACTION}:
        assert dialog.findChild(QWidget, name) is not None, name


def test_the_menu_entry_and_its_paths(qapp):
    from orchestrator.gui.menus import boards
    from orchestrator.utils.next_steps.common import MENU_LAYOUT
    assert boards.BUNDLE_ACTION == "act_boards_bundle"
    assert {k for k in boards.CLI_PATHS if k.startswith("bundle")} >= {"bundle plan", "bundle deploy", "bundle delete",
                                                                        "bundle block new"}
    assert all(boards.WINDOW_OBJECTS[o] == "act_boards_bundle" for k, o in boards.CLI_PATHS.items()
               if k.startswith("bundle"))
    entries = dict(e[:2] for menu in MENU_LAYOUT if menu[0] == "menu_boards" for e in menu[2] if e != "-")
    assert entries["act_boards_bundle"] == "boards.menu.bundle"


def test_deploy_refuses_a_bundle_whose_block_was_revoked_since_the_build(qapp, bundle, tmp_path, monkeypatch):
    """Review: a block the signed index revoked after the build is never sent (service.revoked_blocks), before the
    dry run and the question; the CLI and bundle_deploy refuse it the same way."""
    from test_bundle_ui import _Revoking, _indexed_built
    from orchestrator.runtime.bundle import service
    dialog, board = bundle
    app, built = _indexed_built(tmp_path, board.state, board.signer)
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (app, _Revoking()))
    dialog.reload()
    rows = [r for r, row in enumerate(dialog.rows) if row["bundle_id"] == built.bundle_id]
    dialog.tbl_store.selectRow(rows[0])
    boxes = Boxes(monkeypatch, "yes")
    dialog.buttons["btn_bundle_deploy"].click()
    wait(qapp, dialog)
    assert board.logins == [] and boxes.questions() == []
    assert boxes.asked and "revoked" in boxes.asked[-1].text, [a.text for a in boxes.asked]


@POSIX
@VERIFIER
def test_the_root_actions_follow_the_apply_after_their_own_question_and_the_password_once(qapp, bundle, monkeypatch):
    """Deploy... with autostart: the installer stops before the root actions, llmcf-install's plan is shown, its own
    question ("No" default) comes, and only a yes runs its apply; a sudo that asks for a password gets the person's
    password from a dialog once (private mode). llmcf-install itself runs in tests/test_llmcf_install.py."""
    import hashlib
    from orchestrator.gui import bundle_window
    from orchestrator.runtime.bundle import installer as inst
    from orchestrator.runtime.bundle import service
    from orchestrator.runtime.bundle.board import BoardRun
    from orchestrator.runtime.bundle.root_helper import HelperBuild, target_of
    dialog, board = bundle
    stand_in = board.state.parent / "llmcf-install.stand-in"
    stand_in.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(service, "helper_for", lambda framework, arch, say=None: HelperBuild(
        stand_in, hashlib.sha256(b"#!/bin/sh\n").hexdigest(), "0.1.0", target_of(arch), "test"))
    runs = []
    answers = {"plan": "llmcf-bundle: would create_user stt user=llmcf\n"
                       "llmcf-bundle: result=plan changed=0 pending=1\n",
               "apply": "llmcf-bundle: done create_user stt user=llmcf\nllmcf-bundle: result=ok changed=1\n"}

    def run_root_on_board(open_session, built, action, *, password="", say=None):
        runs.append((action, password, threading.current_thread() is threading.main_thread()))
        if not password:
            return BoardRun("root-" + action, 3, inst.report("llmcf-bundle: result=needs-password changed=0 x\n"))
        return BoardRun("root-" + action, 0, inst.report(answers[action]))
    monkeypatch.setattr(service, "run_root_on_board", run_root_on_board)

    def fill(form):
        from PySide6.QtWidgets import QLineEdit
        form.findChild(QLineEdit, "txt_bundle_sudo_password").setText("correct horse")
        return True
    monkeypatch.setattr(bundle_window, "_exec", fill)
    _pick_stt(dialog, board)
    for r in range(dialog.tbl_vars.rowCount()):
        if dialog.tbl_vars.item(r, 1).text() == "autostart":
            dialog.tbl_vars.item(r, 3).setText("yes")
    boxes = Boxes(monkeypatch, "yes")
    dialog.buttons["btn_bundle_build"].click()
    wait(qapp, dialog)
    assert len(dialog.rows) == 1, dialog.output.toPlainText()
    dialog.tbl_store.selectRow(0)
    dialog.buttons["btn_bundle_deploy"].click()
    for _ in range(6):
        wait(qapp, dialog)
    texts = [q.text for q in boxes.questions()]
    assert any("Send bundle" in t for t in texts) and any("root action" in t for t in texts), texts
    assert all(q.default_is_no for q in boxes.questions())
    assert [r[:2] for r in runs] == [("plan", ""), ("plan", "correct horse"), ("apply", "correct horse")], runs
    assert not any(r[2] for r in runs), "llmcf-install runs off the GUI thread"
    assert "done create_user" in dialog.output.toPlainText()
    assert "correct horse" not in dialog.output.toPlainText()


def _built_with_root(qapp, dialog, board, monkeypatch):
    """A stored autostart bundle (root actions) of the fake board, built through the window."""
    import hashlib
    from orchestrator.runtime.bundle import service
    from orchestrator.runtime.bundle.root_helper import HelperBuild, target_of
    stand_in = board.state.parent / "llmcf-install.stand-in"
    stand_in.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(service, "helper_for", lambda framework, arch, say=None: HelperBuild(
        stand_in, hashlib.sha256(b"#!/bin/sh\n").hexdigest(), "0.1.0", target_of(arch), "test"))
    _pick_stt(dialog, board)
    for r in range(dialog.tbl_vars.rowCount()):
        if dialog.tbl_vars.item(r, 1).text() == "autostart":
            dialog.tbl_vars.item(r, 3).setText("yes")
    Boxes(monkeypatch, "yes")
    dialog.buttons["btn_bundle_build"].click()
    wait(qapp, dialog)
    dialog.tbl_store.selectRow(0)
    return dialog._bundle()[1]


def test_a_not_ready_board_sets_the_readiness_up_once_and_the_status_never_asks_for_it(qapp, bundle, monkeypatch):
    """Regression (looped before: when llmcf-install still answered not-ready after the readiness check said it was
    there, the window checked and ran again without end; and the read-only status started the readiness's setup,
    which the CLI's status never does): one setup per run, and the status only says that it was not read."""
    from orchestrator.runtime.bundle import installer as inst
    from orchestrator.runtime.bundle import readiness, service
    from orchestrator.runtime.bundle.board import BoardRun
    dialog, board = bundle
    built = _built_with_root(qapp, dialog, board, monkeypatch)
    runs, checks = [], []

    def run_root_on_board(open_session, built, action, *, password="", say=None):
        runs.append(action)
        if len(runs) > 6:
            raise AssertionError("the window runs llmcf-install again and again")
        return BoardRun("root-" + action, 3, inst.report("llmcf-bundle: result=not-ready changed=0 x\n"))

    def readiness_on_board(open_session, built):
        checks.append(1)
        return readiness.Readiness.parse("sudo=yes\nrootway=nopass\nsigners=root\nhelper=same\ndropin=yes\n"
                                         "account=ok\n")
    monkeypatch.setattr(service, "run_root_on_board", run_root_on_board)
    monkeypatch.setattr(service, "readiness_on_board", readiness_on_board)
    boxes = Boxes(monkeypatch, "yes")
    dialog._root("apply", built)
    for _ in range(10):
        wait(qapp, dialog)
    assert runs == ["plan", "plan"] and len(checks) == 1, (runs, checks)
    assert any("root readiness" in a.text for a in boxes.asked), [a.text for a in boxes.asked]
    runs.clear()
    checks.clear()
    dialog._root("status", built, ask_first=False)
    for _ in range(4):
        wait(qapp, dialog)
    assert runs == ["status"] and checks == [], (runs, checks)
    assert "not read" in dialog.output.toPlainText()
