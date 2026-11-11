"""Boards > Boards and Devices... > Board packages... (runtime/device/board_packages.py) through the real Boards window,
offscreen: the plan before the question ("No" is the default and runs nothing), the install with exactly the planned
names, enterprise mode refusing the online way and offering the board preload, writing a board preload, taking one in
from this computer and from the board (a refused one shown as refused, nothing changed), and the clock window's
fake-hwclock proposal leading to it.

No network, no board: the session behind connect_stored is a stand-in that answers the gate operations."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from test_board_packages import archive, keys, made  # noqa: E402,F401 - the fake archive and its preload
from test_boards_window import Audit, Boxes, stored, wait  # noqa: E402
from test_device_runtime import FakeHostKey  # noqa: E402

DEVICE = "0123456789abcdef"
ROOT = "/home/dietpi/.local/share/llm-framework/llama.cpp"
FACTS = ("id=debian\ncodename=bookworm\narch=arm64\nmanager=apt\nsource=https://deb.debian.org\n"
         "pkg libc6 2.36-9\n")
PLAN = ("llmcf-bundle: package fake-hwclock installed=no\nllmcf-bundle: would-install fake-hwclock 0.13\n"
        "llmcf-bundle: result=planned changed=0 source=online pending=1 would=fake-hwclock=0.13 already=-\n")
APPLY = ("llmcf-bundle: installed fake-hwclock 0.13\n"
         "llmcf-bundle: result=ok changed=1 source=online installed=fake-hwclock=0.13 already=-\n")


class FakeBoard:
    """The board session: answers package-facts, package-root, the upload folder, the upload and package-take; keeps
    every operation it was sent and on which thread."""

    def __init__(self, facts=FACTS, take="taken=yes"):
        self.facts, self.take, self.sent, self.uploads, self.threads = facts, take, [], [], []

    def run(self, script, timeout=None, **_kw):
        self.threads.append(threading.current_thread() is threading.main_thread())
        self.sent.append((script.verb, tuple(script.args)))
        out = {"package-facts": self.facts, "staging-create": ROOT + "/.staging-abcdefgh",
               "package-take": self.take, "dir-remove": ""}.get(script.verb)
        if script.verb == "package-root":
            out = PLAN if script.args[0] == "plan" else APPLY
        if out is None:
            raise AssertionError(f"unexpected operation {script.verb}")
        return SimpleNamespace(status=0, out=out + "\n", err="")

    def upload(self, path, remote, sha256):
        self.uploads.append((Path(path), remote, sha256))

    def close(self):
        pass

    def verbs(self, verb):
        return [args for v, args in self.sent if v == verb]


@pytest.fixture
def window(qapp, tmp_path):
    from orchestrator.gui.boards_window import BoardsWindow
    config = SimpleNamespace(user_state_dir=str(tmp_path / "state"), framework_mode="private")
    config.get = lambda key, default=None: getattr(config, key, default)
    fw = SimpleNamespace(config=config, tool_registry=SimpleNamespace(audit=Audit()))
    Path(config.user_state_dir).mkdir(parents=True)
    dialog = BoardsWindow(fw, lang="en")
    stored(Path(config.user_state_dir), ssh_user="dietpi")
    dialog.reload()
    dialog.select(DEVICE)
    yield dialog
    if dialog.running:
        dialog._on_cancel()
        wait(qapp, dialog)
    dialog.deleteLater()
    qapp.processEvents()


@pytest.fixture
def board(monkeypatch):
    from orchestrator.runtime.device import person_steps, remote
    fake = FakeBoard()
    monkeypatch.setattr(person_steps, "connect_stored", lambda *a, **k: (fake, FakeHostKey()))
    monkeypatch.setattr(remote, "RemoteShell", lambda client: client)
    monkeypatch.setattr(remote, "read_facts", lambda shell: SimpleNamespace(root=ROOT, home="/home/dietpi"))
    return fake


def choose(monkeypatch, what, scope="board", on_board=""):
    """The Board packages... form filled with `what` (install, write, take, take_board) and accepted."""
    from PySide6.QtWidgets import QComboBox, QLineEdit
    from orchestrator.gui import boards_window
    def fill(dialog):
        action = dialog.findChild(QComboBox, "cmb_boards_packages_action")
        action.setCurrentIndex(action.findData(what))
        combo = dialog.findChild(QComboBox, "cmb_boards_packages_scope")
        combo.setCurrentIndex(combo.findData(scope))
        dialog.findChild(QLineEdit, "txt_boards_packages_on_board").setText(on_board)
        return True
    monkeypatch.setattr(boards_window, "_exec", fill)


def state(window):
    return Path(window.framework.config.user_state_dir)


def test_the_plan_comes_first_no_runs_nothing_and_yes_installs_exactly_the_planned_names(qapp, window, board,
                                                                                         monkeypatch):
    from orchestrator.runtime.device import board_packages
    choose(monkeypatch, "install")
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    question, = boxes.questions()
    assert question.default_is_no and "Install fake-hwclock on ZERO 3W" in question.text
    assert "Would install: fake-hwclock=0.13" in question.text and "own package sources" in question.text
    assert board.verbs("package-root") == [("plan", "board", "fake-hwclock")], "No runs nothing"
    assert board_packages.load_record(state(window), DEVICE).get("last_run") is None
    boxes.answer = "yes"
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    wait(qapp, window)
    assert board.verbs("package-root")[-2:] == [("plan", "board", "fake-hwclock"), ("apply", "board", "fake-hwclock")]
    assert not any(board.threads), "every board contact runs off the GUI thread"
    assert "Installed on ZERO 3W: fake-hwclock=0.13." in boxes.asked[-1].text
    record = board_packages.load_record(state(window), DEVICE)
    assert record["last_run"]["result"] == "ok" and record["last_run"]["packages"] == ["fake-hwclock"]
    assert window.framework.tool_registry.audit.rows[-1]["tool"] == board_packages.AUDIT_TOOL


def test_enterprise_mode_refuses_the_online_way_and_offers_the_board_preload(qapp, window, board, monkeypatch,
                                                                            tmp_path):
    from PySide6.QtWidgets import QFileDialog
    window.framework.config.framework_mode = "enterprise"
    choose(monkeypatch, "install")
    folders = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: folders.append(1) or str(tmp_path / "p"))
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    assert board.verbs("package-root") == [], "nothing runs as root"
    offer, = boxes.questions()
    assert offer.default_is_no and "internal mirror" in offer.text and "https://deb.debian.org" in offer.text
    assert "Write a board preload for these packages now?" in offer.text and folders == []
    # yes: the preload for exactly these packages, from the facts just read
    boxes.answer = "yes"
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    assert folders == [1] and (tmp_path / "p" / "preload.sh").is_file()
    assert '"packages": [\n  "fake-hwclock"\n ]' in (tmp_path / "p" / "preload.sh").read_text()
    assert board.verbs("package-root") == []


def test_writing_a_board_preload_writes_both_scripts_where_the_person_chose(qapp, window, board, monkeypatch,
                                                                         tmp_path):
    from PySide6.QtWidgets import QFileDialog
    from orchestrator.runtime.device import board_packages
    board_packages.record_facts(state(window), DEVICE, board_packages.parse_facts(FACTS))
    choose(monkeypatch, "write")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "pre"))
    boxes = Boxes(monkeypatch)
    window.buttons["btn_boards_packages"].click()
    assert (tmp_path / "pre" / "preload.sh").is_file() and (tmp_path / "pre" / "preload.ps1").is_file()
    assert '"release": "bookworm"' in (tmp_path / "pre" / "preload.sh").read_text()
    assert "fake-hwclock with their dependencies for debian bookworm on arm64" in boxes.asked[-1].text
    assert board.sent == [], "writing a preload needs no board contact"
    # cancelled folder dialog: nothing written
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    window.buttons["btn_boards_packages"].click()
    assert not (tmp_path / "pre2").exists()


def test_a_preload_from_this_computer_is_checked_copied_and_taken_in(qapp, window, board, monkeypatch, made):
    from PySide6.QtWidgets import QFileDialog
    from orchestrator.runtime.device import board_packages
    _recipe, path = made
    board_packages.record_facts(state(window), DEVICE, board_packages.parse_facts(FACTS))
    choose(monkeypatch, "take")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    boxes = Boxes(monkeypatch)
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    (sent, remote, sha), = board.uploads
    assert sent == path and remote == ROOT + "/.staging-abcdefgh/packages.tar.gz" and len(sha) == 64
    assert board.verbs("package-take") == [(remote, "board")]
    assert board.verbs("dir-remove") == [(ROOT + "/.staging-abcdefgh",)]
    assert "taken in on ZERO 3W for board: fake-hwclock=0.13,libdep=2.1" in boxes.asked[-1].text
    assert board_packages.load_record(state(window), DEVICE)["taken"]["board"]["packages"] == \
        "fake-hwclock=0.13,libdep=2.1"


def test_a_preload_for_another_release_is_refused_and_nothing_reaches_the_board(qapp, window, board, monkeypatch,
                                                                              made):
    from PySide6.QtWidgets import QFileDialog
    from orchestrator.runtime.device import board_packages
    _recipe, path = made
    board_packages.record_facts(state(window), DEVICE, board_packages.parse_facts(FACTS.replace("bookworm",
                                                                                                "trixie")))
    choose(monkeypatch, "take")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))
    boxes = Boxes(monkeypatch)
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    assert board.sent == [] and board.uploads == []
    assert "not done" in boxes.asked[-1].text and "board runs trixie on arm64" in boxes.asked[-1].text
    assert "taken" not in board_packages.load_record(state(window), DEVICE)


def test_a_preload_brought_to_the_board_by_usb_is_taken_in_there_and_a_damaged_one_refused(qapp, window, board,
                                                                                         monkeypatch):
    from orchestrator.runtime.device import board_packages
    choose(monkeypatch, "take_board", on_board="/home/dietpi/usb.tar.gz")
    boxes = Boxes(monkeypatch)
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    assert board.verbs("package-take") == [("/home/dietpi/usb.tar.gz", "board")] and board.uploads == []
    assert "taken in on ZERO 3W for board" in boxes.asked[-1].text
    assert "board" in board_packages.load_record(state(window), DEVICE)["taken"]
    # a damaged one: refused, shown as refused, the record keeps the earlier take-in only
    before = board_packages.load_record(state(window), DEVICE)["taken"]
    board.take = "taken=no reason=damaged"
    window.buttons["btn_boards_packages"].click()
    wait(qapp, window)
    assert "not done" in boxes.asked[-1].text and "does not match its SHA256SUMS" in boxes.asked[-1].text
    assert board_packages.load_record(state(window), DEVICE)["taken"] == before
    # no path named: said, no contact
    sent = len(board.sent)
    choose(monkeypatch, "take_board", on_board="")
    window.buttons["btn_boards_packages"].click()
    assert len(board.sent) == sent and "Name the path" in boxes.asked[-1].text


def test_the_clock_windows_proposal_leads_to_board_packages_with_the_clock_keeper(qapp, window, board,
                                                                                monkeypatch):
    from PySide6.QtWidgets import QComboBox, QLabel
    from orchestrator.gui import boards_window
    from orchestrator.runtime.device import board_clock
    board_clock.save_record(state(window), DEVICE, {"rtc": "reset", "keeper": "none", "offset_s": 0})
    boxes = Boxes(monkeypatch, answer="no")
    window.buttons["btn_boards_clock"].click()
    question = boxes.questions()[-1]
    assert "install the clock keeper fake-hwclock with Install in" in question.text
    assert "Board packages..." in question.text, "the proposal names the window's own place"
    seen = {}

    def look(dialog):
        combo = dialog.findChild(QComboBox, "cmb_boards_packages_scope")
        seen["scope"] = combo.currentData()
        seen["intro"] = "\n".join(w.text() for w in dialog.findChildren(QLabel))
        return False
    monkeypatch.setattr(boards_window, "_exec", look)
    window.buttons["btn_boards_packages"].click()
    assert seen["scope"] == "board"
    assert "The clock keeper (no working RTC, no time keeper): fake-hwclock" in seen["intro"]
    assert "Missing at the last reading: fake-hwclock" in seen["intro"]
    assert board.sent == []
