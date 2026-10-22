"""
Boards > Boards and Devices... (W1' of 2026-10-16: every action of `llmcf deploy devices` and `llmcf device` also by
mouse). Offscreen: the entry is reachable from the menu bar, each button calls the same core function as its CLI
command with the right arguments, every question defaults to No, deletions go through ask_deletion with the full list,
every board contact and build runs off the GUI thread, a password is never kept, and every text exists in English and
German.

No network, no Docker, no board: the core functions behind the buttons are stand-ins where they would reach one.
"""

import re
import string
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")

from test_device_runtime import DEVUAN, POSIX, RK3566, FakeHostKey  # noqa: E402


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.rows.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))


class Boxes:
    """Answers plain message boxes (plain._exec) and keeps what each one asked, with its default button."""

    def __init__(self, monkeypatch, answer="no"):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui import plain
        self.asked, self.answer = [], answer
        buttons = {"yes": QMessageBox.Yes, "no": QMessageBox.No}

        def fake(box):
            default = box.defaultButton()
            self.asked.append(SimpleNamespace(text=box.text(), title=box.windowTitle(),
                                              default_is_no=default is not None and default is box.button(
                                                  QMessageBox.No),
                                              question=box.button(QMessageBox.Yes) is not None))
            if box.button(QMessageBox.Yes) is None:
                return int(QMessageBox.Ok)
            return int(buttons[self.answer])

        monkeypatch.setattr(plain, "_exec", fake)

    def questions(self):
        return [a for a in self.asked if a.question]


def framework(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path / "state")),
                           tool_registry=SimpleNamespace(audit=Audit()))


def wait(qapp, dialog, timeout=10.0):
    end = time.monotonic() + timeout
    while dialog.running and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert not dialog.running, "the step did not end"


def stored(state, device_id="0123456789abcdef", profile=RK3566, label="ZERO 3W", **settings):
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.runtime.profile import ProfileStore
    store = ProfileStore(state)
    store.save_device(device_id, profile, label=label, address="192.0.2.10")
    if settings:
        store.save_deploy_settings(device_id, DeploySettings(**settings).as_dict())
    return store


def trusted_board(state, label="Living room"):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    key = paramiko.ECDSAKey.generate()
    ssh_trust.trust_host_key(state, "192.168.1.50", 22, key)
    return TrustedDevices(state).record("192.168.1.50", 22, "dietpi", key, via="cli", paired=True, label=label)


@pytest.fixture
def window(qapp, tmp_path):
    from orchestrator.gui.boards_window import BoardsWindow
    fw = framework(tmp_path)
    Path(fw.config.user_state_dir).mkdir(parents=True)
    dialog = BoardsWindow(fw, lang="en")
    yield dialog
    if dialog.running:
        dialog._on_cancel()
        wait(qapp, dialog)
    dialog.deleteLater()
    qapp.processEvents()


def state_of(dialog):
    return Path(dialog.framework.config.user_state_dir)


def threads(log):
    """A recorder of the thread each call ran on."""
    def note(*_a, **_k):
        log.append(threading.current_thread() is threading.main_thread())
    return note


# --- the menu bar ------------------------------------------------------------------------------------------------

def test_the_boards_menu_opens_the_window_from_the_menu_bar(qapp, tmp_path, monkeypatch, memory_keyring):
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window
    from orchestrator.gui.boards_window import BoardsWindow
    from orchestrator.gui.menus import boards as menu_module

    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    win = main_window.MainOrchestrator(REPO_ROOT)
    qapp.processEvents()
    try:
        titles = [a.text().replace("&", "") for a in win.menuBar().actions()]   # wave 6: titles with mnemonics
        # the design's order: File, Build, Boards (with the Build menu of wave 5b, Boards follows Build)
        assert titles.index("Build") == titles.index(win.i18n.t("menu.file", "File")) + 1, titles
        assert titles.index("Boards") == titles.index("Build") + 1, titles
        action = win.findChild(type(win.act_boards_devices), menu_module.ACTION_NAME)
        assert action is win.act_boards_devices and action.text() == "Boards and Devices..."
        assert action in win.menu_boards.actions()
        stored(Path(win.framework.config.user_state_dir))
        action.trigger()
        dialog = win.boards_window
        assert isinstance(dialog, BoardsWindow) and dialog.isVisible() and dialog.table.rowCount() == 1
        for name in set(menu_module.WINDOW_OBJECTS):
            assert dialog.findChild(type(dialog.btn_cancel), name) is not None, name
        action.trigger()
        assert len(win.findChildren(BoardsWindow)) == 1, "a second click shows the same window"
        dialog.close()
    finally:
        win.close()


def test_cli_paths_name_every_command_of_the_area_and_existing_objects():
    from click.testing import CliRunner  # noqa: F401 - the command tree is walked below
    from orchestrator import cli as cli_module
    from orchestrator.gui.menus import boards as menu_module

    def walk(group, path):
        for name, command in group.commands.items():
            full = f"{path} {name}".strip()
            yield full, command
            if hasattr(command, "commands"):
                yield from walk(command, full)

    area = {p: c for p, c in walk(cli_module.cli, "") if p.startswith(("deploy devices", "device"))}
    for path, command in area.items():
        assert path in menu_module.CLI_PATHS, path
        for param in command.params:
            flag = next((o for o in param.opts if o.startswith("--")), None)
            if flag is None or flag == "--help":
                continue
            key = f"{path} {flag}"
            assert key in menu_module.CLI_PATHS or key in menu_module.MACHINE_ONLY, key
    for name in menu_module.CLI_PATHS.values():
        assert name == menu_module.ACTION_NAME or menu_module.WINDOW_OBJECTS[name] == menu_module.ACTION_NAME


# --- texts ------------------------------------------------------------------------------------------------------------

def test_every_text_exists_in_english_and_german_with_the_same_placeholders():
    from orchestrator.utils.l10n_boards import TEXTS
    fields = string.Formatter()
    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key, text in TEXTS["en"].items():
        en = {f for _, f, _, _ in fields.parse(text) if f}
        de = {f for _, f, _, _ in fields.parse(TEXTS["de"][key]) if f}
        assert en == de, key
    from orchestrator.runtime.device.person_steps import SUDO_QUESTION
    assert TEXTS["en"]["boards.settings.sudo_question"] == SUDO_QUESTION, "the GUI asks the CLI's words"


def test_the_window_speaks_german(qapp, tmp_path):
    from orchestrator.gui.boards_window import BoardsWindow
    dialog = BoardsWindow(framework(tmp_path), lang="de")
    try:
        assert dialog.windowTitle() == "Boards und Geräte"
        assert dialog.buttons["btn_boards_remove_trust"].text() == "Vertrauen entfernen..."
        assert dialog.table.horizontalHeaderItem(1).text() == "Geräte-ID"
    finally:
        dialog.deleteLater()


# --- the table ----------------------------------------------------------------------------------------------------

def test_the_table_merges_stored_and_trusted_boards_and_shows_the_full_fingerprint(qapp, window):
    state = state_of(window)
    device = trusted_board(state)
    stored(state, device_id=device.device_id, ssh_user="dietpi")
    stored(state, device_id="fedcba9876543210", profile=DEVUAN, label="Attic")
    window.reload()
    assert window.table.rowCount() == 2
    rows = {window.table.item(r, 0).text(): [window.table.item(r, c).text() for c in range(window.table.columnCount())]
            for r in range(window.table.rowCount())}
    living = rows["Living room"]
    assert living[1] == device.device_id and living[3] == "dietpi"
    assert device.fingerprint in living[4] and "(the trusted key)" in living[4]
    assert "glibc" in living[8] and "user as dietpi" in living[9]
    assert rows["Attic"][4] == "not trusted" and "sysvinit" in rows["Attic"][8]
    window.select("fedcba9876543210")
    assert not window.buttons["btn_boards_remove_trust"].isEnabled(), "an untrusted board has no trust to remove"
    assert window.buttons["btn_boards_build"].isEnabled()


# --- trusted boards ------------------------------------------------------------------------------------------------

def test_remove_trust_asks_with_exactly_what_goes_no_by_default_and_audits(qapp, window, monkeypatch):
    from orchestrator.runtime.device.trusted import TrustedDevices
    state = state_of(window)
    device = trusted_board(state)
    window.reload()
    window.select(device.device_id)
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_remove_trust"].click()
    (question,) = boxes.questions()
    assert question.default_is_no
    for part in (device.label, device.device_id, device.address, "dietpi", device.fingerprint, "audit log"):
        assert part in question.text
    assert TrustedDevices(state).get(device.device_id) is not None, "No keeps the board trusted"
    boxes.answer = "yes"
    window.buttons["btn_boards_remove_trust"].click()
    assert TrustedDevices(state).get(device.device_id) is None
    (row,) = window.framework.tool_registry.audit.rows
    assert row["actor"] == "gui" and row["tool"] == "device_removed"
    assert row["extra"]["confirm_channel"] == "framework-gui"


def test_adding_a_board_shows_the_fingerprint_asks_no_by_default_and_runs_off_the_gui_thread(qapp, window,
                                                                                             monkeypatch):
    from orchestrator.gui import boards_window
    from orchestrator.runtime.device import person_steps
    ran, added = [], []
    monkeypatch.setattr(person_steps, "read_host_key", lambda state, host, port: threads(ran)() or (
        FakeHostKey(), "known", "SHA256:" + "A" * 43))

    def add(state, host, port, user, key, *, label, via, audit, actor):
        threads(ran)()
        added.append(dict(host=host, port=port, user=user, label=label, via=via, actor=actor, audit=audit))
        return None, False

    monkeypatch.setattr(person_steps, "add_trusted_board", add)

    def fill(dialog):
        from PySide6.QtWidgets import QLineEdit, QSpinBox
        dialog.findChild(QLineEdit, "txt_boards_add_host").setText("myboard.local")
        dialog.findChild(QLineEdit, "txt_boards_add_user").setText("dietpi")
        dialog.findChild(QSpinBox, "spn_boards_add_port").setValue(2222)
        dialog.findChild(QLineEdit, "txt_boards_add_label").setText("Shelf")
        return True

    monkeypatch.setattr(boards_window, "_exec", fill)
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_add"].click()
    wait(qapp, window)
    (question,) = boxes.questions()
    assert question.default_is_no and "SHA256:" + "A" * 43 in question.text and "myboard.local:2222" in question.text
    assert added == [] and ran == [False], "the key is read off the GUI thread; No adds nothing"
    boxes.answer = "yes"
    window.buttons["btn_boards_add"].click()
    wait(qapp, window)
    wait(qapp, window)
    assert added == [dict(host="myboard.local", port=2222, user="dietpi", label="Shelf", via="gui", actor="gui",
                          audit=window.framework.tool_registry.audit)]
    assert all(r is False for r in ran)


def test_adding_a_board_nobody_trusted_is_refused_as_in_the_cli(qapp, window, monkeypatch):
    from orchestrator.runtime.device import person_steps
    monkeypatch.setattr(person_steps, "read_host_key", lambda state, host, port: (FakeHostKey(), "unknown", "SHA256:x"))
    monkeypatch.setattr(person_steps, "add_trusted_board", lambda *a, **k: pytest.fail("nothing is added"))
    boxes = Boxes(monkeypatch)
    window.add_board("192.0.2.10", "dietpi", 22, None)
    wait(qapp, window)
    assert boxes.questions() == [] and "is not trusted yet (its host key is unknown)" in boxes.asked[-1].text
    window.add_board("-oProxyCommand=x", "dietpi", 22, None)
    assert "Enter an address or a host name" in boxes.asked[-1].text


# --- deploy settings ---------------------------------------------------------------------------------------------

def test_settings_show_everything_and_systemd_needs_the_sudo_yes_written_first(qapp, window, monkeypatch):
    from orchestrator.gui import boards_window
    from orchestrator.runtime.device.person_steps import load_settings
    from orchestrator.runtime.profile import ProfileStore
    state = state_of(window)
    stored(state, ssh_user="dietpi", model="models/q.gguf")
    window.reload()
    window.select("0123456789abcdef")
    shown = []

    def fill(dialog):
        from PySide6.QtWidgets import QComboBox, QLabel, QSpinBox
        shown.append("\n".join(label.text() for label in dialog.findChildren(QLabel)))
        install = dialog.findChild(QComboBox, "cmb_boards_install")
        install.setCurrentIndex(install.findData("systemd"))
        dialog.findChild(QSpinBox, "spn_boards_port").setValue(9090)
        return True

    monkeypatch.setattr(boards_window, "_exec", fill)
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_settings"].click()
    assert "SSH user: dietpi" in shown[0] and "models/q.gguf" in shown[0] and "KV cache:" in shown[0]
    (question,) = boxes.questions()
    assert question.default_is_no and "sudo -n" in question.text
    store = ProfileStore(state)
    assert load_settings(store, "0123456789abcdef").install == "user", "No changes nothing"
    assert window.framework.tool_registry.audit.rows == []
    boxes.answer = "yes"
    window.buttons["btn_boards_settings"].click()
    saved = load_settings(store, "0123456789abcdef")
    assert saved.install == "systemd" and saved.port == 9090 and saved.sudo_confirmed_at
    (row,) = window.framework.tool_registry.audit.rows
    assert row["tool"] == "device_runtime_systemd" and row["extra"]["confirm_channel"] == "framework-gui"


def test_damaged_settings_are_set_again_after_a_question_with_the_file(qapp, window, monkeypatch):
    """The table says 'damaged; set them again' (review of wave 5a): Settings... asks, with the file's full path and
    size and "No" as the default, whether to start again from the defaults; only a yes opens the form, and its OK
    writes the settings even when no field differs from the defaults."""
    from orchestrator.gui import boards_window
    from orchestrator.runtime.device.person_steps import DAMAGED_QUESTION, load_settings
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.utils.l10n_boards import TEXTS
    assert TEXTS["en"]["boards.settings.damaged_question"] == DAMAGED_QUESTION, "the GUI asks the CLI's words"
    state = state_of(window)
    store = stored(state)
    path = Path(store.deploy_settings_path("0123456789abcdef"))
    path.write_text("{damaged", encoding="utf-8")
    window.reload()
    window.select("0123456789abcdef")
    assert window.table.item(0, 9).text() == "damaged; set them again"
    forms = []
    monkeypatch.setattr(boards_window, "_exec", lambda dialog: forms.append(dialog) or True)
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_settings"].click()
    (question,) = boxes.questions()
    assert question.default_is_no and str(path) in question.text and "is overwritten" in question.text
    assert forms == [] and path.read_text(encoding="utf-8") == "{damaged", "No: no form, nothing changed"
    boxes.answer = "yes"
    window.buttons["btn_boards_settings"].click()
    assert len(forms) == 1
    assert load_settings(store, "0123456789abcdef") == DeploySettings(), "set again: the defaults, now readable"
    assert "user as not set" in window.table.item(0, 9).text()


def test_settings_refusals_are_the_clis(qapp, window, monkeypatch):
    from orchestrator.runtime.device.person_steps import load_settings
    from orchestrator.runtime.profile import ProfileStore
    state = state_of(window)
    store = stored(state, profile=DEVUAN, label="rock5")
    window.reload()
    boxes = Boxes(monkeypatch, answer="yes")
    entry = window.entries[0]
    current = load_settings(store, "0123456789abcdef")
    assert window.change_settings(entry, DEVUAN, current, {"install": "systemd"}) is False
    assert "does not run systemd" in boxes.asked[-1].text and "sysvinit" in boxes.asked[-1].text
    assert window.change_settings(entry, DEVUAN, current, {"ssh_user": "root"}) is False
    assert "never runs as root" in boxes.asked[-1].text
    window.framework.tool_registry.audit = None   # no audit log: no consent, nothing changes
    stored(state, device_id="fedcba9876543210")
    window.reload()
    entry = next(e for e in window.entries if e["device_id"] == "fedcba9876543210")
    assert window.change_settings(entry, RK3566, load_settings(store, "fedcba9876543210"),
                                  {"install": "systemd", "model": "m.gguf", "ssh_user": "dietpi"}) is False
    assert "could not be written to the audit log" in boxes.asked[-1].text
    assert ProfileStore(state).load_deploy_settings("fedcba9876543210") == {}


# --- plan, status, build ----------------------------------------------------------------------------------------------

def test_plan_and_status_run_the_clis_steps_off_the_gui_thread(qapp, window, monkeypatch):
    from orchestrator.runtime.device import steps
    from test_device_runtime import HOST
    monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
    ran = []
    real = steps.plan_runtime
    monkeypatch.setattr(steps, "plan_runtime", lambda profile: threads(ran)() or real(profile))
    stored(state_of(window))
    window.reload()
    window.select("0123456789abcdef")
    window.buttons["btn_boards_plan"].click()
    wait(qapp, window)
    text = window.output.toPlainText()
    assert "Builder: container debian-12" in text and "armv8.2-a+dotprod+fp16" in text and "Built: no" in text
    window.buttons["btn_boards_status"].click()
    wait(qapp, window)
    assert "Deployed: nothing yet" in window.output.toPlainText()
    assert ran == [False, False]


def test_build_asks_before_the_download_no_by_default_and_passes_jobs_and_mirror(qapp, window, monkeypatch):
    import orchestrator.runtime.build.source as source_module
    from orchestrator.gui import boards_window
    from orchestrator.runtime.device import steps
    from test_device_runtime import HOST
    monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
    monkeypatch.setattr(source_module, "cached_source", lambda pin, state: None)
    calls = []

    def build_runtime(state, device_id, profile, plan, source, **kw):
        calls.append(dict(device_id=device_id, jobs=kw["jobs"], mirror=kw["mirror"], gui=threading.current_thread()
                          is threading.main_thread(), fetch=kw["may_fetch"]()))
        kw["on_step"]("build", None)
        packaged = SimpleNamespace(path=Path("/x/llama-runtime-arm64-0123.tar.gz"), sha256="a" * 64)
        return steps.RuntimeBuild(builds=None, build=SimpleNamespace(build_id="b" * 64), packaged=packaged,
                                  source_dir=Path("/x"), built_now=True)

    monkeypatch.setattr(steps, "build_runtime", build_runtime)

    def fill(dialog):
        from PySide6.QtWidgets import QLineEdit, QSpinBox
        dialog.findChild(QSpinBox, "spn_boards_jobs").setValue(4)
        dialog.findChild(QLineEdit, "txt_boards_mirror").setText("https://mirror.example/llama.cpp.git")
        return True

    monkeypatch.setattr(boards_window, "_exec", fill)
    stored(state_of(window))
    window.reload()
    window.select("0123456789abcdef")
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_build"].click()
    wait(qapp, window)
    (question,) = boxes.questions()
    assert question.default_is_no and "about 215 MB" in question.text and calls == []
    boxes.answer = "yes"
    window.buttons["btn_boards_build"].click()
    wait(qapp, window)
    wait(qapp, window)
    assert calls == [dict(device_id="0123456789abcdef", jobs=4, mirror="https://mirror.example/llama.cpp.git",
                          gui=False, fetch=True)]
    text = window.output.toPlainText()
    assert "Building llama.cpp for ZERO 3W" in text and "Board runtime ready" in text


def test_cancel_stops_the_running_step_and_closes_its_session(qapp, window):
    started, closed = threading.Event(), []

    class Client:
        def close(self):
            closed.append(True)

    def work():
        window._keep_client(Client())
        started.set()
        for _ in range(500):
            if window._cancel.is_set():
                break
            time.sleep(0.01)
        window._check_cancel()
        return "never shown"

    done = []
    window._start("A step", work, done.append)
    assert started.wait(5) and not window.btn_cancel.isHidden() and window.lbl_busy.text().startswith("A step")
    window.btn_cancel.click()
    wait(qapp, window)
    assert closed == [True] and done == [] and "Cancelled." in window.output.toPlainText()


# --- deletions --------------------------------------------------------------------------------------------------------

def test_older_packages_go_through_ask_deletion_with_the_full_list(qapp, window, monkeypatch):
    from orchestrator.Core.deletion import accept
    from orchestrator.gui import deletion_dialog
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    state = state_of(window)
    stored(state)
    devices = DeviceRuntimeStore(state, "0123456789abcdef")
    devices.root.mkdir(parents=True)
    old = devices.root / "llama-runtime-arm64-aaaaaaaaaaaaaaaa.tar.gz"
    old.write_bytes(b"x" * 2048)
    (devices.root / (old.name + ".sha256")).write_text("a  x\n")
    window.reload()
    window.select("0123456789abcdef")
    asked = []
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: asked.append((plan, kw)))
    Boxes(monkeypatch)
    window.buttons["btn_boards_prune"].click()
    wait(qapp, window)   # the list is read in the background (review of wave 5a)
    (plan, kw), = asked
    assert kw["multiple"] is True and kw["title"] == "Runtime packages of ZERO 3W"
    item = next(i for i in plan.items if i.file_name == old.name)
    assert item.full_path == str(old) and item.size_bytes == 2048 and "runtime package of build" in item.what
    assert old.exists(), "No (or closing the dialog) deletes nothing"
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: accept(plan, "gui", "permanent"))
    window.buttons["btn_boards_prune"].click()
    wait(qapp, window)
    wait(qapp, window)   # and the ticked files are deleted (or moved to the trash, hashed there) in the background
    assert not old.exists() and "Deleted for good" in window.output.toPlainText()
    rows = window.framework.tool_registry.audit.rows
    assert rows[0]["actor"] == "gui" and rows[0]["tool"] == "device_runtime"


def test_older_builds_on_the_board_are_listed_off_the_gui_thread_and_removed_only_after_yes(qapp, window,
                                                                                           monkeypatch):
    from orchestrator.Core.deletion import accept
    from orchestrator.gui import deletion_dialog
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.remote import board_cleanup_plan, BoardBuild
    from orchestrator.runtime.device import remote
    stored(state_of(window), ssh_user="dietpi")
    window.reload()
    window.select("0123456789abcdef")
    ran, connected, removed = [], [], []

    class Client:
        def close(self):
            connected.append("closed")

    def connect(state, device_id, host, port, user, password=None, *, step="", on_client=None):
        threads(ran)()
        connected.append((device_id, host, port, user, password, step))
        on_client(Client())
        return Client(), FakeHostKey()

    plan = board_cleanup_plan([BoardBuild("a" * 16, 4096, False)], "/home/dietpi/.local/share/x")
    prune = person_steps.BoardPrune(root="/home/dietpi/.local/share/x", plan=plan, busy=["c" * 16], previous=None)
    monkeypatch.setattr(person_steps, "connect_stored", connect)
    monkeypatch.setattr(remote, "RemoteShell", lambda client: client)
    monkeypatch.setattr(person_steps, "list_board_builds", lambda shell, state, device_id: threads(ran)() or prune)
    monkeypatch.setattr(person_steps, "remove_board_builds", lambda shell, accepted, p, device_id, audit: (
        threads(ran)(), removed.append((accepted, p, device_id, audit)))[1] or SimpleNamespace(
        done=["runtime build " + "a" * 16], failed=[], freed_bytes=4096))
    asked = []
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: asked.append((plan, kw)))
    Boxes(monkeypatch)
    window.buttons["btn_boards_prune_board"].click()
    wait(qapp, window)
    (shown, kw), = asked
    assert shown is plan and kw["multiple"] is True and "no trash" in kw["intro"]
    assert connected[0] == ("0123456789abcdef", "192.0.2.10", 22, "dietpi", None, "")
    assert removed == [] and "Not listed, because a running program uses them" in window.output.toPlainText()
    accepted = accept(plan, "gui", "permanent")
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: accepted)
    window.buttons["btn_boards_prune_board"].click()
    wait(qapp, window)
    wait(qapp, window)
    assert removed == [(accepted, prune, "0123456789abcdef", window.framework.tool_registry.audit)]
    assert ran and all(r is False for r in ran), "every board contact runs off the GUI thread"
    assert f"Deleted for good: runtime build {'a' * 16}" in window.output.toPlainText()
    assert "freed on the board" in window.output.toPlainText()


# --- deploy and verify -------------------------------------------------------------------------------------------

def runtime_fakes(monkeypatch, record):
    """Stand-ins for the core steps behind Deploy and Verify; `record` collects their calls."""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.build.verify import Check, VerifyReport
    from orchestrator.runtime.device import person_steps, remote, steps
    build = SimpleNamespace(build_id="b" * 64, plan={"verify": {}})
    report = VerifyReport([Check("version", True, "b1")])
    monkeypatch.setattr(steps, "plan_runtime", lambda profile: SimpleNamespace(build_id="b" * 64,
                                                                               target={"arch": "arm64"},
                                                                               verify={}))
    monkeypatch.setattr(BuildStore, "find", lambda self, plan: build)
    monkeypatch.setattr(steps, "ensure_package", lambda state, device_id, builds, b, source: (
        SimpleNamespace(cleanup_plan=lambda: None), SimpleNamespace(path=Path("/x.tar.gz"), sha256="a" * 64)))

    class Client:
        def close(self):
            record.append("closed")

    def connect(state, device_id, host, port, user, password=None, *, step="", on_client=None):
        record.append(("connect", user, password, step, threading.current_thread() is threading.main_thread()))
        return Client(), FakeHostKey()

    monkeypatch.setattr(person_steps, "connect_stored", connect)
    monkeypatch.setattr(person_steps, "remember_board", lambda *a, **k: record.append(("remember", k["via"])))
    monkeypatch.setattr(remote, "RemoteShell", lambda client: client)
    monkeypatch.setattr(remote, "read_facts", lambda shell: SimpleNamespace(uid=1000, home="/home/dietpi"))

    def choose(shell, home, model, *, may_run_untrusted, record_consent):
        allowed = may_run_untrusted("/home/dietpi/" + model) if model else False
        record.append(("bench", model, allowed))
        return steps.BenchChoice("/home/dietpi/" + model if model else None, allowed, None)

    monkeypatch.setattr(steps, "choose_bench_model", choose)
    monkeypatch.setattr(steps, "install_runtime", lambda shell, **kw: record.append(("install", kw["kv_cache"])) or
                        steps.InstallOutcome(SimpleNamespace(root="/home/dietpi/.local/share/x", previous=None,
                                                             notes=["start the server with: ..."]), report, True))
    monkeypatch.setattr(steps, "check_runtime", lambda shell, **kw: record.append(("check", kw["port"])) or report)
    return build


def test_deploy_asks_for_the_password_each_time_and_never_keeps_it(qapp, window, monkeypatch):
    from orchestrator.gui import boards_window
    record = []
    runtime_fakes(monkeypatch, record)
    stored(state_of(window), ssh_user="dietpi")
    window.reload()
    window.select("0123456789abcdef")
    fields = []

    def fill(dialog):
        from PySide6.QtWidgets import QLineEdit
        password = dialog.findChild(QLineEdit, "txt_boards_password")
        fields.append(password.echoMode() == QLineEdit.Password)
        password.setText("s3cret-once")
        dialog.findChild(QLineEdit, "txt_boards_bench_model").setText("models/own.gguf")
        return True

    monkeypatch.setattr(boards_window, "_exec", fill)
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_deploy"].click()
    wait(qapp, window)
    wait(qapp, window)
    (question,) = boxes.questions()
    # the CLI's words since the review of wave 5a (asked from the worker once the board named the model untrusted)
    assert question.default_is_no and "(no sandbox)" in question.text and "models/own.gguf" in question.text
    assert fields == [True]
    assert ("connect", "dietpi", "s3cret-once", "runtime_deploy", False) in record
    assert ("remember", "gui") in record and ("bench", "models/own.gguf", False) in record
    assert "closed" in record, "the SSH session is closed"
    assert "Deployed build bbbbbbbbbbbbbbbb" in window.output.toPlainText()
    assert "s3cret-once" not in repr(vars(window)), "the password is never kept by the window"
    record.clear()
    window.buttons["btn_boards_deploy"].click()
    wait(qapp, window)
    wait(qapp, window)
    assert len(boxes.questions()) == 2, "asked again for the next deploy"


@pytest.mark.parametrize("action", ["deploy", "verify"])
def test_the_untrusted_model_question_comes_only_for_a_model_outside_the_catalog(qapp, window, monkeypatch, action):
    """As the CLI (review of wave 5a): the model is hashed on the board first; a catalog model runs without a
    question, any other one only after the person's yes to the CLI's own words ("No" is the default), asked while
    the board is reached, and the consent is in the audit log before the model is loaded."""
    import orchestrator.runtime.device.remote as remote
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui import plain
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.person_steps import UNTRUSTED_QUESTION
    from orchestrator.utils.l10n_boards import TEXTS
    assert TEXTS["en"]["boards.untrusted_question"] == UNTRUSTED_QUESTION, "the GUI asks the CLI's words"
    real_choose = steps.choose_bench_model
    order = []
    runtime_fakes(monkeypatch, order)
    monkeypatch.setattr(steps, "choose_bench_model", real_choose)
    monkeypatch.setattr(remote, "model_sha256", lambda shell, path: {"/home/dietpi/models/cat.gguf": "c" * 64,
                                                                     "/home/dietpi/models/own.gguf": "d" * 64}[path])
    monkeypatch.setattr(steps, "catalog_model_digests", lambda: {"c" * 64})
    real_install, real_check = steps.install_runtime, steps.check_runtime
    monkeypatch.setattr(steps, "install_runtime", lambda shell, **kw: order.append(("bench", kw["bench"].allowed))
                        or real_install(shell, **kw))
    monkeypatch.setattr(steps, "check_runtime", lambda shell, **kw: order.append(("bench", kw["bench"].allowed))
                        or real_check(shell, **kw))
    answer = {"yes": False}

    def box(message_box):
        yes = message_box.button(QMessageBox.Yes)
        order.append(("asked", message_box.text(), message_box.defaultButton() is message_box.button(QMessageBox.No),
                      threading.current_thread() is threading.main_thread()))
        return int(QMessageBox.Yes if answer["yes"] else QMessageBox.No) if yes is not None else int(QMessageBox.Ok)

    monkeypatch.setattr(plain, "_exec", box)
    state = state_of(window)
    stored(state, ssh_user="dietpi")
    DeviceRuntimeStore(state, "0123456789abcdef").save_record({"build_id": "b" * 64, "install": "user"})
    window.reload()
    entry = window.entries[0]

    def run(model):
        order.clear()
        if action == "deploy":
            window.deploy(entry, RK3566, None, None, model)
        else:
            window.verify(entry, RK3566, None, model)
        for _ in range(3):
            wait(qapp, window)
        return [o for o in order if o[0] in ("connect", "asked", "bench")]

    seen = run("models/cat.gguf")
    assert [o[0] for o in seen] == ["connect", "bench"] and seen[-1] == ("bench", True), "a catalog model: no question"
    seen = run("models/own.gguf")
    question = UNTRUSTED_QUESTION.format(path="/home/dietpi/models/own.gguf", user="dietpi")
    assert [o[0] for o in seen] == ["connect", "asked", "bench"], seen
    assert seen[1] == ("asked", question, True, True), "the CLI's words, No by default, in the GUI thread"
    assert seen[2] == ("bench", False), "No: the untrusted model is not loaded"
    rows = window.framework.tool_registry.audit.rows
    assert not [r for r in rows if r["tool"] == "device_runtime_untrusted_model"]
    answer["yes"] = True
    assert run("models/own.gguf")[-1] == ("bench", True)
    (consent,) = [r for r in window.framework.tool_registry.audit.rows if r["tool"] == "device_runtime_untrusted_model"]
    assert consent["arguments"] == {"device": "0123456789abcdef", "model_sha256": "d" * 64}
    assert consent["extra"]["confirm_channel"] == "framework-gui"


def test_deploy_refuses_what_the_cli_refuses_before_any_contact(qapp, window, monkeypatch):
    from orchestrator.runtime.build.store import BuildStore
    record = []
    runtime_fakes(monkeypatch, record)
    state = state_of(window)
    stored(state, profile=DEVUAN, label="rock5", ssh_user="dietpi", install="user")
    window.reload()
    entry = window.entries[0]
    boxes = Boxes(monkeypatch)
    window.deploy(entry, DEVUAN, "root", None, None)
    assert "never runs as root" in boxes.asked[-1].text
    monkeypatch.setattr(BuildStore, "find", lambda self, plan: None)
    window.deploy(entry, DEVUAN, None, None, "models/own.gguf")
    wait(qapp, window)
    assert "not built yet" in boxes.asked[-1].text and not [r for r in record if r[0] == "connect"]
    assert boxes.questions() == [], "a board without a build is refused before the benchmark question"


def test_verify_works_without_a_model_and_with_a_user_override(qapp, window, monkeypatch):
    from orchestrator.gui import boards_window
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    record = []
    runtime_fakes(monkeypatch, record)
    state = state_of(window)
    stored(state, ssh_user="dietpi", port=9090)
    DeviceRuntimeStore(state, "0123456789abcdef").save_record({"build_id": "b" * 64, "install": "user"})
    window.reload()
    window.select("0123456789abcdef")

    def fill(dialog):
        from PySide6.QtWidgets import QLineEdit
        assert dialog.findChild(QLineEdit, "txt_boards_password") is None, "a verify never logs in with a password"
        dialog.findChild(QLineEdit, "txt_boards_run_user").setText("tester")
        return True

    monkeypatch.setattr(boards_window, "_exec", fill)
    boxes = Boxes(monkeypatch)
    window.buttons["btn_boards_verify"].click()
    wait(qapp, window)
    assert boxes.questions() == [], "no benchmark model: no question"
    assert ("connect", "tester", None, "verify", False) in record and ("check", 9090) in record
    assert not [r for r in record if r[0] == "remember"], "a verify never rewrites the board's trust record"
    assert "version: passed" in window.output.toPlainText()


def test_verify_says_when_nothing_was_deployed(qapp, window, monkeypatch):
    stored(state_of(window), ssh_user="dietpi")
    window.reload()
    boxes = Boxes(monkeypatch)
    window.verify(window.entries[0], RK3566, None, None)
    assert "Nothing was deployed to this board yet" in boxes.asked[-1].text and not window.running


# --- Mojo's check of this board ----------------------------------------------------------------------------------

def test_check_this_board_is_a_thin_call_of_mojos_board_check_off_the_gui_thread(qapp, window, monkeypatch):
    from orchestrator.Core.mojo.checks import devices as mojo_devices
    from orchestrator.Core.mojo.findings import Finding, Severity
    state = state_of(window)
    device = trusted_board(state)
    window.reload()
    window.select(device.device_id)
    calls = []

    def check(framework, contact=()):
        calls.append((framework, tuple(contact), threading.current_thread() is threading.main_thread()))
        return [Finding("devices", f"devices.{device.device_id}", Severity.OK, "Board Living room, checked now: it "
                        "answers on its SSH port.", ""),
                Finding("devices", "devices.recorded", Severity.OK, "1 trusted board: no recorded problem.")]

    monkeypatch.setattr(mojo_devices, "check_devices", check)
    window.buttons["btn_boards_check"].click()
    wait(qapp, window)
    assert calls == [(window.framework, (device.device_id,), False)]
    assert "checked now: it answers on its SSH port" in window.output.toPlainText()


def test_check_this_board_shares_one_core_call_and_one_text_with_the_mojo_window(qapp, window, monkeypatch):
    """Wave 5a integration: the Boards window, the Mojo window and the Deployment tab check a board through the same
    core call (Core/mojo/boards.check_board) and show its result in the same words (mojo_window.board_check_text):
    a problem with its next step, and a check that could not run with its reason."""
    from orchestrator.Core.mojo import boards as mojo_boards
    from orchestrator.Core.mojo.findings import Finding, Severity
    from orchestrator.gui.mojo_window import board_check_text
    boxes = Boxes(monkeypatch)
    device = trusted_board(state_of(window))
    window.reload()
    window.select(device.device_id)
    finding = Finding("devices", f"devices.{device.device_id}", Severity.WARN,
                      "Board Living room did not answer on its SSH port.", "Switch the board on.")
    calls = []

    def check(framework, device_id):
        calls.append((framework, device_id, threading.current_thread() is threading.main_thread()))
        return finding

    monkeypatch.setattr(mojo_boards, "check_board", check)
    window.buttons["btn_boards_check"].click()
    wait(qapp, window)
    assert calls == [(window.framework, device.device_id, False)]
    expected = board_check_text(finding, "Living room", lang=window.lang)
    assert "Switch the board on." in expected and expected in window.output.toPlainText()
    assert boxes.asked and boxes.asked[-1].text == expected, "a problem is also said in a message, as in the tab"

    def broken(framework, device_id):
        raise OSError("the trusted boards cannot be read")

    monkeypatch.setattr(mojo_boards, "check_board", broken)
    window.buttons["btn_boards_check"].click()
    wait(qapp, window)
    assert board_check_text(None, "Living room", "the trusted boards cannot be read",
                            window.lang) in window.output.toPlainText()


def test_no_html_is_built_from_board_data(qapp, window):
    """A label from a board's probe is shown literally (plain text table cells, plain labels)."""
    stored(state_of(window), label="<b>bold</b>")
    window.reload()
    assert window.table.item(0, 0).text() == "<b>bold</b>"
    source = (REPO_ROOT / "orchestrator/gui/boards_window.py").read_text(encoding="utf-8")
    assert not re.search(r"setText\(f?[\"']<", source) and "setHtml" not in source


# --- review of wave 5a: the defects found after the build -------------------------------------------------------------

def test_older_packages_are_listed_and_deleted_off_the_gui_thread(qapp, window, monkeypatch):
    """The list of packages (a folder walk) and the deletion (the trash hashes each file it takes) never run in a
    slot of the window: both run in the background, the question stays in the GUI thread."""
    from orchestrator.Core.deletion import accept
    from orchestrator.gui import deletion_dialog
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    state = state_of(window)
    stored(state)
    devices = DeviceRuntimeStore(state, "0123456789abcdef")
    devices.root.mkdir(parents=True)
    old = devices.root / "llama-runtime-arm64-aaaaaaaaaaaaaaaa.tar.gz"
    old.write_bytes(b"x" * 2048)
    window.reload()
    window.select("0123456789abcdef")
    on_gui = {}
    real_plan, real_delete = DeviceRuntimeStore.cleanup_plan, DeviceRuntimeStore.delete_packages

    def cleanup_plan(self):
        on_gui.setdefault("plan", []).append(threading.current_thread() is threading.main_thread())
        return real_plan(self)

    def delete_packages(self, *a, **k):
        on_gui.setdefault("delete", []).append(threading.current_thread() is threading.main_thread())
        return real_delete(self, *a, **k)

    def ask(parent, plan, **kw):
        on_gui.setdefault("ask", []).append(threading.current_thread() is threading.main_thread())
        return accept(plan, "gui", "permanent")

    monkeypatch.setattr(DeviceRuntimeStore, "cleanup_plan", cleanup_plan)
    monkeypatch.setattr(DeviceRuntimeStore, "delete_packages", delete_packages)
    monkeypatch.setattr(deletion_dialog, "ask_deletion", ask)
    Boxes(monkeypatch)
    window.buttons["btn_boards_prune"].click()
    wait(qapp, window)
    wait(qapp, window)
    assert not old.exists() and "Deleted for good" in window.output.toPlainText()
    assert on_gui["ask"] == [True], "the person is asked in the GUI thread"
    assert on_gui["plan"] and not any(on_gui["plan"]), "the folder is read off the GUI thread"
    assert on_gui["delete"] == [False], "the deletion runs off the GUI thread"


def fakes_with_tested_on(monkeypatch, record, on_gui):
    """runtime_fakes with a short benchmark that ran (a smoke check and the model's sha256), so 'tested on' is
    recorded; `on_gui` collects the thread of each step of that record."""
    from orchestrator.Core import artifact_output
    from orchestrator.runtime.build.verify import Check, VerifyReport
    from orchestrator.runtime.device import service, steps
    runtime_fakes(monkeypatch, record)
    report = VerifyReport([Check("version", True, "b1"), Check("smoke", True, "llama-bench ran")])
    monkeypatch.setattr(steps, "choose_bench_model", lambda shell, home, model, **kw: steps.BenchChoice(
        "/home/dietpi/models/q.gguf", True, "c" * 64))
    monkeypatch.setattr(steps, "install_runtime", lambda shell, **kw: steps.InstallOutcome(SimpleNamespace(
        root="/home/dietpi/.local/share/x", previous=None, notes=[], unit_started=False), report, True))
    monkeypatch.setattr(steps, "check_runtime", lambda shell, **kw: report)

    def folders(framework):
        on_gui.append(("folders", threading.current_thread() is threading.main_thread()))
        return [Path("output")]

    def record_board_test(output_dirs, device, profile, rep, digest, build_id, source, kv):
        on_gui.append(("record", threading.current_thread() is threading.main_thread()))
        assert digest == "c" * 64 and build_id == "b" * 64
        return [Path("output/build-1")]

    monkeypatch.setattr(artifact_output, "artifact_folders", folders)
    monkeypatch.setattr(service, "record_board_test", record_board_test)


def test_tested_on_is_recorded_off_the_gui_thread(qapp, window, monkeypatch):
    """'tested on' walks every golden-artifact folder for the builds of the benchmarked model: after a deploy and
    after a verify it runs in the background, and the window says what was recorded."""
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    record, on_gui = [], []
    fakes_with_tested_on(monkeypatch, record, on_gui)
    state = state_of(window)
    stored(state, ssh_user="dietpi")
    window.reload()
    Boxes(monkeypatch, answer="yes")
    window.deploy(window.entries[0], RK3566, None, None, "models/q.gguf")
    wait(qapp, window)
    wait(qapp, window)
    assert "with 1 build(s) of this model" in window.output.toPlainText()
    assert on_gui == [("folders", False), ("record", False)], on_gui
    on_gui.clear()
    DeviceRuntimeStore(state, "0123456789abcdef").save_record({"build_id": "b" * 64, "install": "user"})
    window.verify(window.entries[0], RK3566, None, "models/q.gguf")
    wait(qapp, window)
    assert on_gui == [("folders", False), ("record", False)], on_gui


def test_the_last_deploy_column_names_only_a_deploy(qapp, window):
    """A board whose only bookkeeping is a recorded contact (a failed verify, Mojo's note) has no last deploy: the
    column shows the dash, never "None (failed)"."""
    from orchestrator.runtime.device import reasons
    from orchestrator.runtime.device.trusted import TrustedDevices
    state = state_of(window)
    device = trusted_board(state)
    TrustedDevices(state).note_reach(device.device_id, "verify", reasons.HOST_DOWN)
    window.reload()
    assert window.table.item(0, 7).text() == "-"
    TrustedDevices(state).note_deploy(device.device_id, True, "pkg")
    window.reload()
    assert window.table.item(0, 7).text().endswith("(ok)") and "None" not in window.table.item(0, 7).text()


@pytest.mark.parametrize("install, expected", [
    ("user", "The server listens on 127.0.0.1:9090; its key is in ~/.config/llm-framework/llama-server.key"),
    ("systemd", "systemd unit: enabled; the server listens on 127.0.0.1:9090; its key is in "
                "~/.config/llm-framework/llama-server.key")])
def test_a_deploy_says_where_the_server_listens(qapp, window, monkeypatch, install, expected):
    """As the CLI: after a deploy the person reads the server's loopback port and where its key is (and, for the
    systemd unit, whether it was started or only enabled)."""
    record = []
    runtime_fakes(monkeypatch, record)
    extra = {"sudo_confirmed_at": "2026-10-16T10:00:00Z", "model": "models/q.gguf"} if install == "systemd" else {}
    stored(state_of(window), ssh_user="dietpi", port=9090, install=install, **extra)
    window.reload()
    Boxes(monkeypatch)
    window.deploy(window.entries[0], RK3566, None, None, None)
    wait(qapp, window)
    wait(qapp, window)
    assert expected in window.output.toPlainText()


def test_the_source_is_never_fetched_without_the_persons_yes(qapp, window, monkeypatch):
    """The source was cached when the window looked, so nobody was asked; when the cache is gone by the time the
    build runs, the build stops and says so instead of fetching about 215 MB unasked."""
    import orchestrator.runtime.build.source as source_module
    from orchestrator.gui import boards_window
    from orchestrator.runtime.device import steps
    from test_device_runtime import HOST
    monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
    monkeypatch.setattr(source_module, "cached_source", lambda pin, state: Path("cached-source"))
    fetched = []

    def build_runtime(state, device_id, profile, plan, source, **kw):
        if not kw["may_fetch"]():   # the cache vanished after the look
            raise steps.SourceDeclined("not allowed")
        fetched.append(True)
        raise AssertionError("fetched without a yes")

    monkeypatch.setattr(steps, "build_runtime", build_runtime)
    monkeypatch.setattr(boards_window, "_exec", lambda dialog: True)
    stored(state_of(window))
    window.reload()
    window.select("0123456789abcdef")
    boxes = Boxes(monkeypatch, answer="yes")
    window.buttons["btn_boards_build"].click()
    wait(qapp, window)
    wait(qapp, window)
    assert boxes.questions() == [] and fetched == []
    assert "The build needs the pinned llama.cpp source" in window.output.toPlainText()


@POSIX
def test_deploy_and_verify_run_the_real_core_steps_on_a_fake_board(qapp, window, monkeypatch, tmp_path_factory):
    """No stand-in for the core: the window's Deploy runtime... and Verify... run the CLI's own steps (the login to
    the stored board, the trust record, install_runtime, check_runtime) against a board behind fake SSH, so a
    wrong argument between the window and the core fails here."""
    from test_device_runtime import HOST, LICENCE, FakeDevice, FakeDeviceExecutor, FormatOnlyVerifier, plan_for
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device.package import DeviceRuntimeStore, package_runtime
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
    state = state_of(window)
    device_id = device_id_for_host_key(ssh_trust.fingerprint(FakeHostKey()))
    stored(state, device_id=device_id, ssh_user="dietpi", port=9090)
    src = tmp_path_factory.mktemp("src")
    (src / "CMakeLists.txt").write_text("project(llama.cpp)\n")
    (src / "LICENSE").write_bytes(LICENCE.encode("utf-8"))
    builds = BuildStore(state)
    build = builds.build(plan_for(RK3566), source=src, executor=FakeDeviceExecutor(), verifier=FormatOnlyVerifier(),
                         jobs=1, host=HOST, perf_key=RK3566.perf_key())
    package_runtime(builds, build, licence=src / "LICENSE", out_dir=DeviceRuntimeStore(state, device_id).root)
    board = FakeDevice(tmp_path_factory.mktemp("board"))
    connected = []

    class Client:
        def exec_command(self, command, timeout=None):
            return board.exec_command(command, timeout=timeout)

        def close(self):
            connected.append("closed")

    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: FakeHostKey())
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda st, host, port, key: "known")
    monkeypatch.setattr(ssh_trust, "connect", lambda host, user, st, port=22, password=None: (
        connected.append((host, user, port, password)) or Client()))
    window.reload()
    Boxes(monkeypatch)
    window.deploy(window.entries[0], RK3566, None, None, None)
    wait(qapp, window, timeout=60)
    wait(qapp, window, timeout=60)
    text = window.output.toPlainText()
    assert f"Deployed build {build.build_id[:16]}" in text and "127.0.0.1:9090" in text, text
    assert connected[0] == ("192.0.2.10", "dietpi", 22, None) and connected[-1] == "closed"
    record = DeviceRuntimeStore(state, device_id).record()
    assert record["build_id"] == build.build_id and record["verification"]["ok"] is True
    trusted = TrustedDevices(state).get(device_id)
    assert trusted is not None and trusted.trusted_via == "gui" and trusted.paired, "logged in: trusted by the person"
    rows = window.framework.tool_registry.audit.rows
    assert any(r["tool"] == "device_trusted" and r["actor"] == "gui" for r in rows)
    connected.clear()
    window.verify(window.entries[0], RK3566, None, None)
    wait(qapp, window, timeout=60)
    text = window.output.toPlainText()
    assert f"Board {device_id}, build {build.build_id[:16]}:" in text and "version: passed" in text, text
    assert DeviceRuntimeStore(state, device_id).record()["verified_at"] >= record["verified_at"]


@POSIX
def test_older_builds_on_a_fake_board_go_with_the_real_core_steps(qapp, window, monkeypatch, tmp_path_factory):
    """Remove older builds on the board... with the CLI's own steps against a board behind fake SSH: the board's
    list (never the active build) in the deletion dialog, exactly the ticked build removed for good, audited."""
    from orchestrator.Core.deletion import accept, choose
    from orchestrator.gui import deletion_dialog
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    from test_device_runtime import FakeDevice, board_builds
    state = state_of(window)
    device_id = device_id_for_host_key(ssh_trust.fingerprint(FakeHostKey()))
    stored(state, device_id=device_id, ssh_user="dietpi")
    board = FakeDevice(tmp_path_factory.mktemp("board"))
    a, b, c = "a" * 16, "b" * 16, "c" * 16
    board_builds(board, a, b, c, current=c)

    class Client:
        def exec_command(self, command, timeout=None):
            return board.exec_command(command, timeout=timeout)

        def close(self):
            pass

    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: FakeHostKey())
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda st, host, port, key: "known")
    monkeypatch.setattr(ssh_trust, "connect", lambda host, user, st, port=22, password=None: Client())
    shown = []

    def ask(parent, plan, **kw):
        shown.append(sorted(i.file_name for i in plan.items))
        return accept(choose(plan, [[i.file_name for i in plan.items].index(a)]), "gui", "permanent")

    monkeypatch.setattr(deletion_dialog, "ask_deletion", ask)
    window.reload()
    window.select(device_id)
    Boxes(monkeypatch)
    window.buttons["btn_boards_prune_board"].click()
    wait(qapp, window, timeout=60)
    wait(qapp, window, timeout=60)
    assert shown == [[a, b]], "the active build is never listed"
    assert not (board.root / a).exists() and (board.root / b).is_dir() and (board.root / c).is_dir()
    rows = window.framework.tool_registry.audit.rows
    assert [r["outcome"] for r in rows] == ["started", "ok"] and rows[0]["actor"] == "gui"
    assert f"Deleted for good: runtime build {a}" in window.output.toPlainText()


# --- Q5: Edit address or key... (deploy devices edit) -------------------------------------------------------------

def _edit_dialogs(monkeypatch, *, host=None, port=None, key_text=None, read=False, answer=False):
    """Fills the edit form and answers the side-by-side question; keeps what each dialog showed."""
    from PySide6.QtWidgets import QCheckBox, QDialogButtonBox, QLabel, QLineEdit, QPlainTextEdit, QSpinBox
    from orchestrator.gui import boards_window
    seen = SimpleNamespace(forms=[], questions=[])

    def fill(dialog):
        if dialog.objectName() == "dlg_boards_edit_confirm":
            buttons = dialog.findChild(QDialogButtonBox)
            no = buttons.button(QDialogButtonBox.No)
            seen.questions.append(SimpleNamespace(
                default_is_no=no.isDefault() and not buttons.button(QDialogButtonBox.Yes).isDefault(),
                cells={label.objectName(): label.text() for label in dialog.findChildren(QLabel)
                       if label.objectName().startswith("lbl_boards_edit_")},
                text="\n".join(label.text() for label in dialog.findChildren(QLabel)),
                main_thread=threading.current_thread() is threading.main_thread()))
            return answer
        seen.forms.append({"host": dialog.findChild(QLineEdit, "txt_boards_edit_host").text(),
                           "port": dialog.findChild(QSpinBox, "spn_boards_edit_port").value(),
                           "key_placeholder": dialog.findChild(QPlainTextEdit, "txt_boards_edit_key")
                           .placeholderText()})
        if host is not None:
            dialog.findChild(QLineEdit, "txt_boards_edit_host").setText(host)
        if port is not None:
            dialog.findChild(QSpinBox, "spn_boards_edit_port").setValue(port)
        if key_text is not None:
            dialog.findChild(QPlainTextEdit, "txt_boards_edit_key").setPlainText(key_text)   # pasted
        dialog.findChild(QCheckBox, "cb_boards_edit_read").setChecked(read)
        return True

    monkeypatch.setattr(boards_window, "_exec", fill)
    return seen


def test_editing_a_board_s_key_shows_old_and_new_side_by_side_with_no_as_the_default(qapp, window, monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    state = state_of(window)
    device = trusted_board(state)
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: SimpleNamespace(close=lambda: None))
    window.reload()
    assert window.select(device.device_id) and window.buttons["btn_boards_edit"].isEnabled()
    new = paramiko.ECDSAKey.generate()
    pasted = f"{new.get_name()} {new.get_base64()} root@board\n"
    before = (state / "known_hosts").read_bytes()
    seen = _edit_dialogs(monkeypatch, key_text=pasted, answer=False)
    Boxes(monkeypatch)
    window.buttons["btn_boards_edit"].click()
    wait(qapp, window)
    assert seen.forms == [{"host": "192.168.1.50", "port": 22, "key_placeholder": window._t("boards.edit.key_hint")}]
    (question,) = seen.questions
    assert question.default_is_no and question.main_thread
    assert question.cells["lbl_boards_edit_key_now"].endswith(device.fingerprint)
    assert question.cells["lbl_boards_edit_key_after"].endswith(ssh_trust.fingerprint(new))
    assert question.cells["lbl_boards_edit_address_now"] == question.cells["lbl_boards_edit_address_after"]
    assert "192.168.1.50" in question.cells["lbl_boards_edit_address_now"]
    assert (state / "known_hosts").read_bytes() == before, "No changes nothing"
    assert window.framework.tool_registry.audit.rows == []
    seen = _edit_dialogs(monkeypatch, key_text=pasted, host="myboard.local", answer=True)
    boxes = Boxes(monkeypatch)
    window.buttons["btn_boards_edit"].click()
    wait(qapp, window)
    wait(qapp, window)
    (moved,) = TrustedDevices(state).list()
    assert (moved.host, moved.fingerprint, moved.label) == ("myboard.local", ssh_trust.fingerprint(new), "Living room")
    assert [row["tool"] for row in window.framework.tool_registry.audit.rows] == ["host_key_replaced"]
    assert window.selected()["device_id"] == moved.device_id
    (told,) = boxes.asked
    assert ssh_trust.fingerprint(new) in told.text and "myboard.local" in told.text


def test_the_key_read_from_the_board_is_read_off_the_gui_thread(qapp, window, monkeypatch):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.utils import ssh_trust
    state = state_of(window)
    device = trusted_board(state)
    window.reload()
    window.select(device.device_id)
    new, ran = paramiko.ECDSAKey.generate(), []
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: threads(ran)() or new)
    seen = _edit_dialogs(monkeypatch, read=True, key_text="ignored while reading", answer=False)
    Boxes(monkeypatch)
    window.buttons["btn_boards_edit"].click()
    wait(qapp, window)
    assert ran == [False], "read in a worker thread"
    (question,) = seen.questions
    assert question.cells["lbl_boards_edit_key_after"].endswith(ssh_trust.fingerprint(new))


@pytest.mark.parametrize("pasted, key", [("not a key", "boards.edit.err.key_not_a_key"),
                                         ("", "boards.edit.err.unchanged")])
def test_a_refused_change_says_why_in_plain_words(qapp, window, monkeypatch, pasted, key):
    state = state_of(window)
    device = trusted_board(state)
    window.reload()
    window.select(device.device_id)
    seen = _edit_dialogs(monkeypatch, key_text=pasted)
    boxes = Boxes(monkeypatch)
    window.buttons["btn_boards_edit"].click()
    wait(qapp, window)
    assert seen.questions == [] and window._t(key) in [a.text for a in boxes.asked]
