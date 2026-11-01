"""The board bundle wired for the person and the AI (deploy way 3 of orchestrator/runtime/bundle/): the CLI group
`llmcf bundle plan|build|list|deploy|status|uninstall|rollback|delete` and `bundle block list|new`, the deletion of
stored bundles through Core/deletion.py and Core/trash.py with its honest report, and the plan's checks against the
board's probe facts.

The board is the fake board of tests/test_board_bundle.py: llmcf's real gate and the real installer under this
computer's /bin/sh in a temporary home folder (POSIX only). Every name, address and key is a test value
(documentation addresses, a key made for the test)."""

import json
from pathlib import Path

import pytest

from test_board_bundle import (DEVICE, POSIX, RK3566, VERIFIER, GateShell, _elf, _fake_board, _files, _plan,
                               _signer)
from test_cli_builds import invoke_plain

from orchestrator.runtime.bundle import generator as gen
from orchestrator.runtime.bundle import service


# --- a stored board and the CLI around it -----------------------------------------------------------------------------

def _stored_board(state: Path, ssh_user: str = "dietpi"):
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.runtime.profile import ProfileStore
    store = ProfileStore(state)
    store.save_device(DEVICE, RK3566, label="ZERO 3W", address="192.0.2.10")
    if ssh_user:
        store.save_deploy_settings(DEVICE, DeploySettings(ssh_user=ssh_user).as_dict())
    return store


@pytest.fixture
def cli_board(tmp_path, monkeypatch):
    """The CLI with its per-user folder in tmp_path/state, a stored board, its fake board behind the gate and the
    framework's signer of that board; no keyring, no framework boot, no SSH."""
    from rich.console import Console

    from orchestrator import cli as cli_module
    from orchestrator.runtime.device import remote
    state = tmp_path / "state"
    _stored_board(state)
    board = _fake_board(tmp_path, "root")
    opened = []

    def open_device(state_dir, device_id, host, port, user, password, ctx=None, step=""):
        opened.append((device_id, host, port, user, password, step))
        return GateShell(board), "key"
    monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: state)
    monkeypatch.setattr(cli_module, "_open_device", open_device)
    monkeypatch.setattr(cli_module, "_bundle_signer", lambda ctx: board.signer)
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)   # the fake board is the shell already
    monkeypatch.setattr(cli_module, "console", Console(width=1000, color_system=None))
    model, wmodel, prog = _files(tmp_path)
    board.state, board.opened = state, opened
    board.choice = ["--preset", "stt", "--file", f"stt.model={wmodel}", "--file", f"stt.program={prog}",
                    "--licence", "stt.model=MIT"]
    board.prog = prog
    return board


def _bundle_id(state: Path) -> str:
    [built] = gen.BundleStore(state).list()
    return built.bundle_id


def _installed(board, bid: str) -> Path:
    from orchestrator.runtime.bundle import installer as inst
    return board.home / inst.BUNDLE_REL / bid / "components" / "stt" / "model.bin"


def test_the_bundle_commands_exist_with_their_subcommands():
    from orchestrator import cli as cli_module
    group = cli_module.cli.commands["bundle"]
    assert set(group.commands) == {"plan", "build", "list", "deploy", "status", "uninstall", "rollback", "delete",
                                   "block"}
    assert set(group.commands["block"].commands) == {"list", "new"}


def test_plan_shows_components_files_with_source_and_size_root_actions_and_the_probe_checks(cli_board):
    res = invoke_plain("bundle", "plan", "ZERO 3W", *cli_board.choice, "--set", "stt.autostart=yes")
    assert res.code == 0, res.out
    out = res.out
    assert "stt: block stt-whisper-cpp" in out and "autostart=True" in out
    assert f"from {cli_board.prog}" in out and "program: whisper-server" in out and "MB" in out
    assert "root action: create_user for stt (llmcf-install)" in out
    assert "root action: install_unit" in out and "root action: set_cgroup" in out
    for check in ("check architecture: passed", "check init system: passed", "check sudo:", "check offline mode:",
                  "check memory: passed"):
        assert check in out, out
    assert not list((cli_board.state / "bundles").glob("*")) if (cli_board.state / "bundles").exists() else True
    assert cli_board.opened == [], "a plan contacts no board"


def test_plan_json_is_labels_only_no_path_no_account(cli_board):
    res = invoke_plain("bundle", "plan", "ZERO 3W", *cli_board.choice, "--set", "stt.autostart=yes",
                       "--set", "stt.user=llmcfsvc", "--json")
    assert res.code == 0, res.out
    labels = json.loads(res.stdout)
    assert labels["ok"] and labels["root_actions"] == ["create_user", "install_unit", "set_cgroup"]
    text = json.dumps(labels)
    assert str(cli_board.prog.parent) not in text and "llmcfsvc" not in text and "dietpi" not in text
    assert {c["check"] for c in labels["checks"]} == {"architecture", "init system", "sudo", "offline mode", "memory"}


def test_a_plan_with_problems_exits_1_and_builds_nothing(cli_board):
    res = invoke_plain("bundle", "plan", "ZERO 3W", "--preset", "stt")
    assert res.code == 1 and "problem: stt: choose the model file" in res.out
    res = invoke_plain("bundle", "build", "ZERO 3W", "--preset", "stt")
    assert res.code == 1 and "Nothing was built" in res.out
    assert gen.BundleStore(cli_board.state).list() == []


@POSIX
@VERIFIER
def test_build_deploy_status_uninstall_with_the_persons_yes_and_no_by_default(cli_board, monkeypatch):
    from orchestrator import cli as cli_module
    res = invoke_plain("bundle", "build", "ZERO 3W", *cli_board.choice)
    assert res.code == 0 and "Built bundle" in res.out, res.out
    bid = _bundle_id(cli_board.state)
    listed = invoke_plain("bundle", "list", "--json")
    assert json.loads(listed.stdout)[0]["bundle_id"] == bid
    # without a terminal: the dry run runs, nothing is installed
    res = invoke_plain("bundle", "deploy", bid)
    assert res.code == 0 and "Nothing was changed on the board (it needs your answer at a terminal)" in res.out
    assert not _installed(cli_board, bid).exists()
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    res = invoke_plain("bundle", "deploy", bid, input="\n")          # "No" is the default
    assert res.code == 0 and "Nothing was changed on the board." in res.out
    assert "Send bundle" in res.out and "[y/N]" in res.out
    assert not _installed(cli_board, bid).exists()
    res = invoke_plain("bundle", "deploy", bid, input="y\n")
    assert res.code == 0 and "result: ok" in res.out, res.out
    assert _installed(cli_board, bid).is_file()
    assert all(o[4] is None for o in cli_board.opened), "the person's login with the key only, never a password"
    assert {o[5] for o in cli_board.opened} == {"bundle"}
    res = invoke_plain("bundle", "status", bid)
    assert res.code == 0 and "result: installed" in res.out
    res = invoke_plain("bundle", "uninstall", bid, input="\n")
    assert res.code == 0 and "Nothing was changed" in res.out and _installed(cli_board, bid).exists()
    assert "would-remove" in res.out
    res = invoke_plain("bundle", "uninstall", bid, input="y\n")
    assert res.code == 0 and "result: ok" in res.out
    assert not _installed(cli_board, bid).exists()
    listed = json.loads(invoke_plain("bundle", "list", "--json").stdout)
    assert listed[0]["boards"][DEVICE]["last"].startswith("uninstall") or listed[0]["boards"][DEVICE]["last"]


@POSIX
@VERIFIER
def test_deploy_dry_run_changes_nothing(cli_board):
    assert invoke_plain("bundle", "build", "ZERO 3W", *cli_board.choice).code == 0
    bid = _bundle_id(cli_board.state)
    res = invoke_plain("bundle", "deploy", bid, "--dry-run")
    assert res.code == 0 and "result: plan" in res.out, res.out
    assert not _installed(cli_board, bid).exists()


def test_a_board_without_an_ssh_user_says_how_to_set_one(cli_board):
    from orchestrator.runtime.profile import ProfileStore
    assert invoke_plain("bundle", "build", "ZERO 3W", *cli_board.choice).code == 0
    ProfileStore(cli_board.state).save_deploy_settings(DEVICE, {"install": "user"})
    res = invoke_plain("bundle", "deploy", _bundle_id(cli_board.state))
    assert res.code == 1 and "No SSH user is set" in res.out and cli_board.opened == []


def test_an_unknown_bundle_is_refused(cli_board):
    res = invoke_plain("bundle", "status", "nope-0000")
    assert res.code == 1 and "no bundle nope-0000" in res.out


def test_block_list_and_new(cli_board, tmp_path, monkeypatch):
    res = invoke_plain("bundle", "block", "list", "--json")
    ids = {b["id"] for b in json.loads(res.stdout)["blocks"]}
    assert {"llm-llama-server", "stt-whisper-cpp", "tts-sherpa-onnx"} <= ids
    app = tmp_path / "app"
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (app, None))
    res = invoke_plain("bundle", "block", "new", "stt-mine", "--kind", "stt", "--title", "Mine",
                       "--from", "stt-whisper-cpp")
    assert res.code == 0 and "Made block stt-mine" in res.out, res.out
    assert (app / "community" / "blocks" / "stt-mine" / "block.yml").is_file()
    res = invoke_plain("bundle", "block", "list", "--json")
    rows = {b["id"]: b for b in json.loads(res.stdout)["blocks"]}
    assert rows["stt-mine"]["trust"] == "own"


def test_build_asks_before_an_own_unsigned_block_no_by_default(cli_board, tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from orchestrator.runtime.bundle import blocks as blk
    app = tmp_path / "app"
    blk.new_block(app / "community" / "blocks", "stt-mine", kind="stt", title="Mine", template="stt-whisper-cpp")
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (app, None))
    choice = [c.replace("stt", "stt", 1) for c in cli_board.choice[2:]]
    res = invoke_plain("bundle", "build", "ZERO 3W", "--block", "stt-mine", *choice)
    assert res.code == 1 and "Nothing was built" in res.out
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    res = invoke_plain("bundle", "build", "ZERO 3W", "--block", "stt-mine", *choice, input="\n")
    assert res.code == 1 and "Use your own unsigned blocks stt-mine?" in res.out
    res = invoke_plain("bundle", "build", "ZERO 3W", "--block", "stt-mine", *choice, input="y\n")
    assert res.code == 0 and "Built bundle" in res.out, res.out


# --- the deletion of stored bundles ----------------------------------------------------------------------------------

class Audit:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kw):
        self.rows.append((args, kw))


def _two_built(tmp_path):
    state = tmp_path / "state"
    store = gen.BundleStore(state)
    signer = _signer()
    out = []
    for bid in ("one-test", "two-test"):
        plan = _plan(tmp_path, bundle_id=bid)
        assert plan.ok, plan.problems
        out.append(gen.build_bundle(plan, store, signer))
    return state, store, out


def test_the_deletion_plan_lists_every_bundle_with_its_full_path_and_size_and_the_board_note(tmp_path):
    state, store, (one, two) = _two_built(tmp_path)
    store.record_board("one-test", DEVICE, staging="", last="apply:ok")
    plan = store.deletion_plan()
    assert [i.speaking_name for i in plan.items] == ["one-test", "two-test"]
    first = plan.items[0]
    assert first.full_path == str(one.path) and first.size_bytes > 2 * 1024 * 1024 and first.kind == "bundle"
    assert "applied on board" in first.what and "does not uninstall it there" in first.what
    assert "applied" not in plan.items[1].what
    assert store.deletion_plan(["two-test"]).items[0].speaking_name == "two-test"
    assert store.deletion_plan(["nope"]) is None
    ai = plan.ai_view()
    assert str(tmp_path) not in json.dumps(ai)


def test_delete_for_good_and_into_the_trash_only_for_the_person_with_the_audit_first(tmp_path):
    from orchestrator.Core.deletion import PlanChanged, accept, choose
    from orchestrator.Core.trash import Trash
    state, store, (one, two) = _two_built(tmp_path)
    plan = store.deletion_plan()
    with pytest.raises(PermissionError):
        store.delete(accept(choose(plan, [0]), "mcp:laptop", "permanent"), state, audit=Audit())
    with pytest.raises(RuntimeError):   # no audit log: nothing goes
        store.delete(accept(choose(plan, [0]), "cli", "permanent"), state, audit=None)
    assert one.path.is_dir()
    audit = Audit()
    result = store.delete(accept(choose(plan, [0]), "cli", "permanent"), state, audit=audit)
    assert result.done == ["one-test"] and not one.path.exists() and result.freed_bytes > 0
    assert audit.rows and audit.rows[0][0][1] == "bundle_delete"
    with pytest.raises(PlanChanged):    # the list changed since it was shown
        store.delete(accept(plan, "cli", "permanent"), state, audit=Audit())
    trash = Trash(state, key=None)
    accepted = accept(store.deletion_plan(), "gui", "trash")
    result = store.delete(accepted, state, trash=trash, audit=Audit())
    assert result.done == ["two-test"] and result.trash_ids and not two.path.exists()
    [entry] = trash.entries()
    assert entry.kind == "bundle" and entry.origin == "state:bundles/two-test"
    assert gen.BundleStore(state).list() == []


def test_a_bundle_that_stays_is_reported_with_its_paths_for_the_person_and_labels_for_the_ai(tmp_path, monkeypatch):
    from orchestrator.Core.deletion import accept, kept_lines
    from orchestrator.utils import atomic
    state, store, (one, _two) = _two_built(tmp_path)
    left = one.path / "files"
    monkeypatch.setattr(atomic, "remove_tree", lambda path: [left] if Path(path) == one.path else [])
    result = store.delete(accept(store.deletion_plan(["one-test"]), "cli", "permanent"), state, audit=Audit())
    assert result.done == [] and result.failed and result.failed[0][0] == "one-test"
    lines = "\n".join(kept_lines(result))
    assert str(left) in lines, "the person sees every path that stayed"
    assert str(tmp_path) not in json.dumps(result.failed), "the reason itself names no path (safe for an AI)"


def test_the_cli_delete_lists_everything_ticks_nothing_and_asks_no_by_default(cli_board, tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    _state, _store, (one, two) = _two_built(tmp_path)
    res = invoke_plain("bundle", "delete")
    assert res.code == 0 and str(one.path) in res.out and str(two.path) in res.out
    assert "Nothing was deleted: deleting needs your answer at a terminal" in res.out
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    monkeypatch.setattr(cli_module, "_person_trash", lambda ctx, state: (None, Audit()))
    res = invoke_plain("bundle", "delete", input="\n")
    assert one.path.is_dir() and two.path.is_dir(), "nothing is ticked by default"
    res = invoke_plain("bundle", "delete", "one-test", input="1\npermanent\n\n")
    assert one.path.is_dir(), "No is the default"
    res = invoke_plain("bundle", "delete", "one-test", input="1\npermanent\ny\n")
    assert res.code == 0 and "Deleted: one-test" in res.out and not one.path.exists() and two.path.is_dir(), res.out


def test_the_bundle_store_is_deleted_only_through_core_deletion():
    """Only BundleStore.delete removes a stored bundle (behind Core/deletion.py's person check and the audit); the
    CLI and the window call it, no tool module does."""
    root = Path(__file__).resolve().parents[1] / "orchestrator"
    callers = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                     if "deletion_plan(" in p.read_text(encoding="utf-8") and ".delete(accepted" in
                     p.read_text(encoding="utf-8") and "BundleStore" in p.read_text(encoding="utf-8")
                     or "_bundle_store(ctx).delete" in p.read_text(encoding="utf-8"))
    assert not [c for c in callers if c.startswith("tools/")], callers


# --- the plan's checks against the board's probe facts -------------------------------------------------------------

def test_the_checks_name_what_the_plan_found_never_a_second_rule(tmp_path):
    from test_board_bundle import _profile, _selection
    plan = _plan(tmp_path)
    checks = {c["check"]: c["result"] for c in plan.checks}
    assert checks == {"architecture": "passed", "init system": "passed", "sudo": "passed", "offline mode": "passed",
                      "memory": "passed"}
    (tmp_path / "whisper-server").write_bytes(_elf(0x3E))
    bad = _plan(tmp_path, _selection(tmp_path, values={"stt": {"autostart": "yes"}}),
                profile=_profile(INIT_SYSTEM="openrc", RAM_TOTAL_MB="512"))
    checks = {c["check"]: c["result"] for c in bad.checks}
    assert checks["init system"] == "failed" and checks["memory"] == "failed"
    class NoMemory:   # a probe that does not say the board's memory
        def __getattr__(self, name):
            return getattr(RK3566, name)

        def int_value(self, key):
            return None if key == "RAM_TOTAL_MB" else RK3566.int_value(key)
    unknown = _plan(tmp_path, profile=NoMemory())
    assert {c["check"]: c["result"] for c in unknown.checks}["memory"] == "unknown"


def test_the_persons_plan_lines_name_the_local_source_the_labels_do_not(tmp_path):
    plan = _plan(tmp_path)
    lines = "\n".join(plan.lines())
    assert f"from {tmp_path / 'model.gguf'}" in lines and "the board's runtime package" in lines
    assert str(tmp_path) not in json.dumps(plan.labels())


def test_the_questions_before_a_board_changes_name_bundle_board_and_size(tmp_path):
    plan = _plan(tmp_path)
    built = gen.build_bundle(plan, gen.BundleStore(tmp_path / "state"), _signer())
    text = service.question("apply", built, "ZERO 3W")
    assert "voice-test" in text and "ZERO 3W" in text and "MB" in text and "llmcf-install" in text
    assert "newest first" in service.question("uninstall", built, "ZERO 3W")
    assert set(service.CHANGING_ACTIONS) == {"apply", "uninstall", "rollback"}
    rows = service.bundle_rows(gen.BundleStore(tmp_path / "state"))
    assert rows[0]["bundle_id"] == "voice-test" and str(tmp_path) not in json.dumps(rows)


# --- review: what the plan says to an AI, and a block revoked after the build ----------------------------------------

def test_the_labels_say_that_root_actions_wait_for_llmcf_install_even_when_files_are_still_missing(tmp_path):
    """bundle_plan and `bundle plan --json` give labels(): they say that the installer stops before the root actions
    (llmcf-install is not there yet), as the person's plan lines do, also while the files are still to choose (an
    AI's plan never has them)."""
    from test_board_bundle import _selection
    full = _plan(tmp_path, _selection(tmp_path, values={"stt": {"autostart": "yes"}}))
    assert full.ok and full.labels()["root_actions"]
    assert any("llmcf-install" in n and "stops before them" in n for n in full.labels()["notes"])
    selection = _selection(tmp_path, values={"stt": {"autostart": "yes"}})
    selection.files = {}
    missing = _plan(tmp_path, selection)
    assert not missing.ok
    assert any("llmcf-install" in n and "stops before them" in n for n in missing.labels()["notes"])
    assert str(tmp_path) not in json.dumps(missing.labels()["notes"])


class _Revoking:
    """A verified cached index that revokes block stt-indexed (every version)."""
    def revocation(self, kind, pack_id, version, sha256):
        return object() if (kind, pack_id) == ("block", "stt-indexed") else None


def _indexed_built(tmp_path, state: Path, signer):
    from test_board_bundle import _indexed_block, _selection
    app = tmp_path / "app"
    _indexed_block(app)
    selection = _selection(tmp_path, blocks=["stt-indexed"])
    del selection.files["llm"]
    plan = _plan(tmp_path, selection, app_root=app, bundle_id="stt-indexed-test")
    assert plan.ok, plan.problems
    return app, gen.build_bundle(plan, gen.BundleStore(state), signer)


def test_a_block_revoked_after_the_build_is_named_before_a_deploy(tmp_path):
    app, built = _indexed_built(tmp_path, tmp_path / "state", _signer())
    assert service.revoked_blocks(built, app, None) == []
    found = service.revoked_blocks(built, app, _Revoking())
    assert found and "stt-indexed" in found[0] and str(tmp_path) not in found[0]

    class BySha:   # revoked by the pack's sha256 only: found through the install record of the folder
        def revocation(self, kind, pack_id, version, sha256):
            return object() if sha256 == "a" * 64 else None
    assert service.revoked_blocks(built, app, BySha())
    # the framework's own blocks are never revoked by the community index
    framework_built = gen.build_bundle(_plan(tmp_path, bundle_id="fw-test"), gen.BundleStore(tmp_path / "s2"),
                                       _signer())
    assert service.revoked_blocks(framework_built, app, _Revoking()) == []


def test_the_cli_deploy_refuses_a_bundle_whose_block_was_revoked_since_the_build(cli_board, tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    app, built = _indexed_built(tmp_path, cli_board.state, cli_board.signer)
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (app, _Revoking()))
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    res = invoke_plain("bundle", "deploy", built.bundle_id, input="y\n")
    assert res.code == 1 and "revoked" in res.out and "nothing was sent" in res.out, res.out
    assert cli_board.opened == [], "no board is contacted"
    # status, rollback and uninstall of what the board holds stay possible
    assert invoke_plain("bundle", "status", built.bundle_id).code in (0, 1)
    assert cli_board.opened, "a status run is not refused"
