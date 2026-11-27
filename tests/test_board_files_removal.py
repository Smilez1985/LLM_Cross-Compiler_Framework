"""
A board's stored files go only after the person's yes (DESIGN, No dead code: the second recon's decision B of
2026-11-27; the rule of Deletion and the trash): `deploy devices remove` removes the board's trust, then lists its stored
probe, deploy settings and measurement with full path, size and what each is, "No" by default, to the trash or for
good (ProfileStore.remove_device). Never an AI's, never without the audit log, never a list that changed.
"""

from types import SimpleNamespace

import pytest

from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept
from orchestrator.Core.trash import Trash
from orchestrator.runtime.device.person_steps import BOARD_FILE_WHAT, board_files_plan, remove_board_files
from orchestrator.runtime.profile import ProfileStore

DEVICE = "1" * 16


class Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


@pytest.fixture
def state(tmp_path):
    state = tmp_path / "state"
    files = ProfileStore(state).device_files(DEVICE)
    for key in ("probe", "deploy_settings", "limits"):
        files[key].parent.mkdir(parents=True, exist_ok=True)
        files[key].write_text(f"{key}\n", encoding="utf-8")
    return state


def test_the_list_names_each_stored_file_with_its_path_size_and_what_it_is(state):
    plan = board_files_plan(state, DEVICE)
    files = ProfileStore(state).device_files(DEVICE)
    assert [(i.full_path, i.what) for i in plan.items] == [
        (str(files[k]), BOARD_FILE_WHAT[k]) for k in ("probe", "deploy_settings", "limits")]
    assert all(i.size_bytes > 0 and i.kind == "board_files" for i in plan.items)
    assert plan.mode_offered[0] == "trash"
    assert board_files_plan(state, "2" * 16) is None


def test_for_good_through_remove_device(state):
    audit = Audit()
    result = remove_board_files(state, DEVICE, accept(board_files_plan(state, DEVICE), "cli", "permanent"),
                                audit=audit)
    assert len(result.done) == 3 and result.freed_bytes > 0 and not result.failed
    assert not any(p.exists() for p in ProfileStore(state).device_files(DEVICE).values())
    assert audit.entries[0]["tool"] == "device_files_removed" and audit.entries[0]["arguments"]["mode"] == "permanent"


def test_to_the_trash_and_back(state):
    audit = Audit()
    trash = Trash(state, audit=audit)
    result = remove_board_files(state, DEVICE, accept(board_files_plan(state, DEVICE), "gui"), trash=trash,
                                audit=audit)
    assert len(result.trash_ids) == 3 and result.freed_bytes == 0
    assert board_files_plan(state, DEVICE) is None
    assert {e.kind for e in trash.entries()} == {"board_files"}


def test_a_changed_list_an_ai_and_a_missing_audit_remove_nothing(state):
    plan = board_files_plan(state, DEVICE)
    files = ProfileStore(state).device_files(DEVICE)
    with pytest.raises(PermissionError):
        remove_board_files(state, DEVICE, AcceptedDeletion(plan, "permanent", "mcp"), audit=Audit())
    with pytest.raises(RuntimeError):
        remove_board_files(state, DEVICE, accept(plan, "cli", "permanent"), audit=None)
    files["limits"].write_text("a newer measurement\n", encoding="utf-8")
    with pytest.raises(PlanChanged):
        remove_board_files(state, DEVICE, accept(plan, "cli", "permanent"), audit=Audit())
    assert all(files[k].exists() for k in ("probe", "deploy_settings", "limits"))


def test_the_command_removes_the_trust_and_asks_about_the_files(state, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.runtime.device import person_steps
    monkeypatch.setattr(cli, "_state_dir", lambda ctx: state)
    monkeypatch.setattr(person_steps, "remove_trust", lambda *a, **k: True)
    audit = Audit()
    monkeypatch.setattr(cli, "_person_trash", lambda ctx, s: (None, audit))
    asked = []

    def confirm(plan, **kwargs):
        asked.append((len(plan.items), kwargs.get("verb")))
        return accept(plan, "cli", "permanent")
    monkeypatch.setattr(cli, "confirm_deletion", confirm)
    out = CliRunner().invoke(cli.cli, ["deploy", "devices", "remove", DEVICE])
    assert out.exit_code == 0, out.output
    assert asked == [(3, "Remove")] and "Deleted:" in out.output
    assert board_files_plan(state, DEVICE) is None


def test_no_keeps_the_files(state, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.runtime.device import person_steps
    monkeypatch.setattr(cli, "_state_dir", lambda ctx: state)
    monkeypatch.setattr(person_steps, "remove_trust", lambda *a, **k: True)
    monkeypatch.setattr(cli, "confirm_deletion", lambda plan, **kwargs: None)
    out = CliRunner().invoke(cli.cli, ["deploy", "devices", "remove", DEVICE])
    assert out.exit_code == 0 and "Its stored files stay." in out.output
    assert len(board_files_plan(state, DEVICE).items) == 3


def test_the_boards_window_offers_the_files_after_the_trust(qapp, state, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui import boards_window, deletion_dialog
    shown = []
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: shown.append(plan) or None)
    window = SimpleNamespace(state_dir=state, _t=lambda key, **kw: key, lang="en")
    boards_window.BoardsWindow._ask_board_files(window, DEVICE)
    assert len(shown) == 1 and len(shown[0].items) == 3
