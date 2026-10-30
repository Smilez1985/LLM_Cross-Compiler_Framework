"""What the person's Mojo command and the GUI's Mojo window share (W1' of 2026-10-16: every CLI action also by mouse,
through the same core functions).

- Core/mojo/boards.py: the boards a recorded fact points to, the board a name means, and the check of one board
  (one connection to its SSH port, no login), moved out of the CLI;
- Core/mojo/views.py: the person's view of the findings and of what Mojo Miracle did (the CLI's words, and the kinds
  the GUI fills from its own texts);
- Core/mojo/docker_objects.offer_image_list: the person's list of the framework's build images with the person's own
  channel (the terminal's question or the GUI's deletion dialog).
"""

from types import SimpleNamespace

import pytest

from orchestrator.Core.mojo import Finding, MojoReport, Severity
from orchestrator.Core.mojo.runner import MiracleEntry, MiracleReport
from test_mojo_boards import board, counted_port_checks, framework_at, record


# --- boards ------------------------------------------------------------------------------------------------------------

def test_boards_to_offer_lists_each_recorded_board_once_by_the_persons_label(tmp_path):
    from orchestrator.Core.mojo.boards import BoardOffer, boards_to_offer
    from orchestrator.runtime.device import reasons
    state = tmp_path / "state"
    state.mkdir()
    garage = board(state, "192.0.2.11", label="Garage board")
    framework = framework_at(state)
    record(state, garage, "deploy", reasons.HOST_DOWN)
    report = MojoReport([
        Finding("devices", f"devices.{garage.device_id}", Severity.INFO, "recorded", board_check=garage.device_id,
                board_name="Garage board"),
        Finding("devices", f"devices.{garage.device_id}.other", Severity.WARN, "again",
                board_check=garage.device_id, board_name="Garage board"),
        Finding("devices", "devices.gone", Severity.WARN, "a board no longer trusted", board_check="f" * 16,
                board_name="f" * 16),
        Finding("python", "python.version", Severity.OK, "fine")])
    offered = boards_to_offer(framework, report)
    assert offered == [BoardOffer(garage.device_id, "Garage board", "Garage board"),
                       BoardOffer("f" * 16, "f" * 16, "f" * 16)], "once each; the name stands in for a lost label"
    device_id, label = offered[0][:2]
    assert (device_id, label) == (garage.device_id, "Garage board")
    assert boards_to_offer(framework, MojoReport([])) == []


def test_find_board_takes_a_label_a_device_id_or_an_address_and_says_why_not(tmp_path):
    from orchestrator.Core.mojo.boards import find_board
    state = tmp_path / "state"
    state.mkdir()
    garage = board(state, "192.0.2.11", label="Garage board")
    twin_a = board(state, "192.0.2.12", label="Twin")
    twin_b = board(state, "192.0.2.13", label="Twin")
    framework = framework_at(state)
    for name in ("Garage board", "garage BOARD", garage.device_id, "192.0.2.11"):
        found = find_board(framework, name)
        assert found.device_id == garage.device_id and found.problem == "", name
    found = find_board(framework, "Twin")
    assert found.device_id == "" and found.problem == "2 trusted boards match 'Twin'"
    assert {d.device_id for d in found.boards} == {twin_a.device_id, twin_b.device_id}
    found = find_board(framework, "nothing")
    assert found.device_id == "" and found.problem == "No trusted board is named 'nothing'"
    assert len(found.boards) == 3, "every trusted board to choose from"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_board(framework_at(empty), "x").boards == ()


def test_check_board_contacts_only_that_board_once_and_returns_its_finding(tmp_path, monkeypatch):
    from orchestrator.Core.mojo.boards import check_board, trusted_boards
    from orchestrator.runtime.device import reasons
    state = tmp_path / "state"
    state.mkdir()
    garage = board(state, "192.0.2.11", label="Garage board")
    board(state, "192.0.2.12", port=2222, label="Desk")
    framework = framework_at(state)
    assert [d.label for d in trusted_boards(framework)] == ["Desk", "Garage board"]
    asked = counted_port_checks(monkeypatch)
    finding = check_board(framework, garage.device_id)
    assert asked == [("192.0.2.11", 22)], "one connection to that board's SSH port, no other board"
    assert finding.code == f"devices.{garage.device_id}" and finding.severity == Severity.OK
    assert "checked now" in finding.message and "radxa" not in finding.message + finding.fix_hint
    asked.clear()
    counted_port_checks(monkeypatch, answer=reasons.PORT_CLOSED)
    closed = check_board(framework, garage.device_id)
    assert closed.severity == Severity.WARN and reasons.PORT_CLOSED in closed.message and closed.fix_hint
    asked = counted_port_checks(monkeypatch)
    assert check_board(framework, "0" * 16) is None and asked == [], "no trusted board: nothing is contacted"


# --- the person's view -------------------------------------------------------------------------------------------------

def offer(verdict, code="x.code", summary="", reason="", steps=()):
    finding = Finding("area", code, Severity.WARN, "message", "hint")
    plan = SimpleNamespace(summary=summary, steps=list(steps)) if verdict == "miracle" else None
    return SimpleNamespace(finding=finding, verdict=verdict, plan=plan, reason=reason)


def test_miracle_can_names_one_kind_for_every_verdict_in_the_clis_words():
    from orchestrator.Core.mojo.offers import DELETION_WHERE, OTHER_LISTS
    from orchestrator.Core.mojo.views import MIRACLE_LINES, miracle_acts, miracle_can, miracle_line
    repair = offer("miracle", summary="only you can open the state folder then")
    images = offer("deletion_list", code="disk.cache", reason="deleting needs your list")
    snapshots = offer("deletion_list", code="knowledge.snapshots", reason="deleting needs your list")
    person = offer("person", reason="needs you")
    assert miracle_can(repair, True) == ("repair", {"summary": "only you can open the state folder then"})
    assert miracle_line(repair, False) == "Mojo Miracle can repair it: only you can open the state folder then."
    assert miracle_can(images, True) == ("list", {})
    assert miracle_line(images, True).startswith("Mojo Miracle shows you the framework's build images")
    assert miracle_can(images, False) == ("list_elsewhere", {"reason": "deleting needs your list",
                                                             "where": DELETION_WHERE})
    assert miracle_can(snapshots, True) == ("other_list", {"reason": "deleting needs your list",
                                                           "where": OTHER_LISTS["knowledge.snapshots"]})
    assert miracle_line(person, True) == "Mojo Miracle cannot repair it: needs you."
    assert set(MIRACLE_LINES) == {"repair", "list", "list_elsewhere", "other_list", "cannot", "ask", "ask_elsewhere"}
    assert miracle_acts([repair, images, snapshots, person]) == [repair, images], \
        "Mojo Miracle is offered for its repairs and the lists it opens"


def test_person_findings_pair_each_finding_with_its_offer_and_plan_steps():
    from orchestrator.Core.mojo.views import person_findings, plan_steps
    step = SimpleNamespace(target_label="the state folder", full_path="/srv/state", before="0755", after="0700",
                           undoable=True)
    repair = offer("miracle", summary="s", steps=[step])
    fine = Finding("python", "python.version", Severity.OK, "Python 3.13")
    report = MojoReport([fine, repair.finding])
    assert person_findings(report, [repair]) == [(fine, None), (repair.finding, repair)]
    assert plan_steps(repair) == [step] and plan_steps(None) == [] and plan_steps(offer("admin")) == []


def test_what_became_of_each_problem_after_miracle():
    from orchestrator.Core.mojo.views import entry_next, entry_text, miracle_summary, outcome_kind
    hinted = Finding("ai", "tools.audit", Severity.ERROR, "changed", "Archive it.")
    repaired = MiracleEntry(Finding("security", "state.permissions", Severity.WARN, "open", "chmod"), "miracle",
                            "repaired", "only you can open it")
    gone = MiracleEntry(hinted, "miracle", "already_fixed", reason="no longer found")
    changed = MiracleEntry(hinted, "miracle", "plan_changed", reason="the problem changed since the plan was shown")
    refused = MiracleEntry(hinted, "person", "not_done", reason="needs you")
    declined = MiracleEntry(hinted, "deletion_list", "declined", reason="you chose nothing")
    assert [outcome_kind(e) for e in (repaired, gone, changed, refused, declined)] == \
        ["repaired", "already_fixed", "not_repaired", "not_repaired", "declined"]
    assert entry_text(repaired) == "only you can open it. Checked again: the problem is gone."
    assert entry_text(refused) == "needs you"
    assert entry_next(changed) == ("again", "") and entry_next(refused) == ("hint", "Archive it.")
    assert entry_next(repaired) == ("", "") and entry_next(gone) == ("", "")
    after = MojoReport([Finding("disk", "disk.new", Severity.WARN, "new problem")])
    result = MiracleReport([repaired, gone, refused], after)
    assert miracle_summary(result) == ("Mojo Miracle repaired 1 of 3 problems; 1 was already gone; 1 was not "
                                       "repaired (why: above); the check afterwards found 1 new (above).")
    assert miracle_summary(MiracleReport([], MojoReport([]))) == \
        "Mojo Oracle found no problems, so Mojo Miracle had nothing to repair."


# --- the person's list of the framework's build images ----------------------------------------------------------------

def test_offer_image_list_lists_asks_the_persons_channel_and_deletes_exactly_what_was_accepted():
    from test_doctor_fixes import FakeClient, deletions, fake_framework, images
    from orchestrator.Core.deletion import accept, choose
    from orchestrator.Core.mojo.docker_objects import offer_image_list
    asked = []
    outcome = offer_image_list(fake_framework(None), lambda plan, root: asked.append(plan), actor="gui")
    assert not outcome.listed and "Docker is not available" in outcome.why and asked == [], "no list: not asked"
    empty = fake_framework(FakeClient([]))
    outcome = offer_image_list(empty, lambda plan, root: asked.append(plan), actor="gui")
    assert outcome.listed and outcome.plan is None and asked == []
    assert outcome.why == "there are no framework build images to delete" and outcome.count == 0
    client = FakeClient(images())
    framework = fake_framework(client)
    outcome = offer_image_list(framework, lambda plan, root: asked.append((plan, root)) or None, actor="gui")
    assert len(asked) == 1 and asked[0][1] == "" and outcome.why == "" and outcome.count == 0, \
        "the person chose nothing: no reason, nothing deleted"
    assert deletions(client) == []
    shown = asked[0][0]
    outcome = offer_image_list(framework, lambda plan, root: accept(choose(plan, [0]), "gui", "permanent"),
                               actor="gui")
    assert outcome.count == 1 and outcome.why == "" and len(deletions(client)) == 1
    assert outcome.result.deleted == [shown.items[0].speaking_name]
    assert framework.tool_registry.audit.entries[0]["actor"] == "gui"


def test_offer_image_list_says_why_nothing_was_deleted():
    from test_doctor_fixes import FakeClient, deletions, fake_framework, images
    from orchestrator.Core.deletion import accept, choose
    from orchestrator.Core.mojo.docker_objects import offer_image_list
    client = FakeClient(images())
    framework = fake_framework(client)

    def changed_meanwhile(plan, root):
        accepted = accept(choose(plan, [0]), "gui", "permanent")
        client.images.images = client.images.images[1:]   # the chosen image changed while the person read the list
        return accepted
    outcome = offer_image_list(framework, changed_meanwhile, actor="gui")
    assert outcome.listed and outcome.count == 0 and "changed since the list was shown" in outcome.why
    assert deletions(client) == []
    client = FakeClient(images())
    client.images.refuse = "sha256:" + "a" * 64
    framework = fake_framework(client)
    outcome = offer_image_list(framework, lambda plan, root: accept(
        choose(plan, [i for i, item in enumerate(plan.items) if item.file_name == client.images.refuse]), "gui",
        "permanent"), actor="gui")
    assert outcome.count == 0 and outcome.result.failed and outcome.why.startswith("Docker kept every image")
    with pytest.raises(PermissionError):
        offer_image_list(framework, lambda plan, root: accept(plan, "gui", "permanent"), actor="mcp")


def test_the_cli_calls_the_shared_core_functions():
    """The CLI's Mojo board offer, its --board lookup, its image list and its Miracle line call Core/mojo (nothing of
    it stays inline), and its output keeps its words (tests/test_mojo_cli.py, tests/test_mojo_boards.py)."""
    import inspect
    from orchestrator import cli
    assert "boards_to_offer" in inspect.getsource(cli._mojo_offer_boards)
    assert "check_board" in inspect.getsource(cli._mojo_offer_boards)
    assert "find_board" in inspect.getsource(cli._mojo_board)
    assert "offer_image_list" in inspect.getsource(cli._doctor_free_space)
    assert "miracle_line" in inspect.getsource(cli._mojo_miracle_line)
    assert "miracle_acts" in inspect.getsource(cli.mojo.callback)
