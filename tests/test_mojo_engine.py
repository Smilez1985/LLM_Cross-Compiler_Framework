"""Mojo Miracle's engine and verdicts (round 6 design 7.4-7.6 and 7.11; round 7 items 11', M' and T).

Every warning and error carries why it matters and what Mojo Miracle can do about it; a repair's class comes from its
steps; only harmless plans are applied, as a compare-and-swap, audited first, checked again afterwards; nothing is
deleted by a repair.
"""

import ast
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.mojo import Finding, Mojo, Severity, offers
from orchestrator.Core.mojo.checks import check_user_state
from orchestrator.Core.mojo.offers import REASONS, VERDICTS, offer_for
from orchestrator.Core.mojo.repairs import REPAIRS, StepChanged
from orchestrator.Core.mojo.repairs import state as state_repair
from orchestrator.Core.mojo.repairs.engine import apply_repair, lock_path
from orchestrator.Core.mojo.repairs.model import RepairPlan, RepairStep, classify

REPO = Path(__file__).resolve().parent.parent
POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX modes")


class Audit:
    def __init__(self, fail=False):
        self.entries, self.fail = [], fail

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fail:
            raise RuntimeError("disk full")
        from orchestrator.tools.audit import AuditLog
        AuditLog._extra(outcome, kwargs.get("extra"))   # the real log's checks of the extra fields
        self.entries.append(dict(actor=actor, tool=tool, decision=decision, outcome=outcome, **kwargs))
        return len(self.entries)


def framework_with(state: Path, audit=None):
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=state),
                           tool_registry=SimpleNamespace(audit=audit if audit is not None else Audit()))


@pytest.fixture
def open_state(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    os.chmod(state, 0o755)
    return state


def plan_of(*kinds, undoable=True):
    return RepairPlan(repair_id="test.repair", finding_code="test.code", summary="a test",
                      steps=tuple(RepairStep(kind=k, target_label="x", undoable=undoable) for k in kinds))


# --- the class comes from the steps --------------------------------------------------------------------------------

def test_class_is_derived_never_declared():
    assert classify(plan_of("chmod", "switch_ref")) == "delegable"
    assert classify(plan_of("chmod", undoable=False)) == "person", "what cannot be undone is the person's"
    assert classify(plan_of("chmod", "widen")) == "person"
    assert classify(plan_of("chmod", "delete")) == "person_deletion"
    assert classify(plan_of("overwrite")) == "person_deletion"
    assert classify(plan_of("chmod", "audit")) == "person_here"
    assert classify(plan_of("vault", "delete")) == "person_here"
    assert classify(plan_of("chmod", "system", "delete")) == "manual", "the strongest step decides"
    with pytest.raises(ValueError):
        RepairPlan(repair_id="test.repair", finding_code="test.code", summary="x", steps=(), repair_class="delegable")


def test_every_repair_of_the_table_is_harmless():
    """No repair Mojo Miracle makes by itself deletes, widens or needs the person (a deletion is WP7's list)."""
    sources = [p for p in (REPO / "orchestrator" / "Core" / "mojo" / "repairs").glob("*.py")]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for kind in ("delete", "overwrite", "widen", "vault", "policy", "audit", "oversight", "system"):
            assert f'kind="{kind}"' not in text, (path.name, kind)
    assert set(REPAIRS) == {"state.tighten", "runtime.switch_intact"}


# --- every warning and error says why and what Miracle can do --------------------------------------------------------

def _finding_calls():
    for path in sorted((REPO / "orchestrator" / "Core" / "mojo").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Finding":
                severity = node.args[2] if len(node.args) > 2 else None
                if isinstance(severity, ast.Attribute) and severity.attr in ("WARN", "ERROR"):
                    yield path.relative_to(REPO).as_posix(), node.lineno, {k.arg for k in node.keywords}


def test_every_warning_and_error_says_where_why_and_what_miracle_can_do():
    """Transparency (round 7 item T): no problem is reported without where it is (a path for the person, a label for an
    AI), why it matters and a repair or the reason Mojo Miracle cannot repair it."""
    calls = list(_finding_calls())
    assert len(calls) > 25, "the scan finds the checks' findings"
    for source, line, keywords in calls:
        assert "where" in keywords, f"{source}:{line} says not where"
        assert "why" in keywords, f"{source}:{line} says no why"
        assert keywords & {"repair_id", "cannot"}, f"{source}:{line} says neither its repair nor why there is none"


def test_a_revoked_pack_is_the_persons_step_not_a_list_that_does_not_exist(tmp_path):
    """No list of the framework deletes a community pack's folder yet: Mojo says honestly that the person does it,
    instead of pointing at a list for it."""
    from orchestrator.Core.mojo.checks import check_community
    folder = tmp_path / "targets" / "Board"
    prov = SimpleNamespace(kind="target", id="board", version="1.0.0")
    revoked = SimpleNamespace(value="revoked")
    framework = SimpleNamespace(community_manager=SimpleNamespace(
        verify_installed=lambda: [(folder, prov, revoked, ["withdrawn by its publisher"])]))
    (finding,) = check_community(framework)
    assert finding.code == "community.board.revoked" and finding.where == str(folder)
    offer = offer_for(framework, finding)
    assert offer.verdict == "person" and offer.reason == REASONS["person"]


def test_each_problem_gets_exactly_one_verdict(booted_framework):
    report = Mojo(booted_framework).run()
    offered = offers(booted_framework, report)
    problems = [f for f in report.findings if f.severity in (Severity.WARN, Severity.ERROR)]
    assert [o.finding for o in offered] == problems, "every warning and error, nothing else"
    for offer in offered:
        assert offer.verdict in VERDICTS and offer.summary


def test_a_finding_without_its_reason_counts_as_not_automatic():
    finding = Finding("x", "x.y", Severity.WARN, "m")
    offer = offer_for(None, finding)
    assert offer.verdict == "not_automatic" and offer.reason == REASONS["not_automatic"]


# --- Mojo Oracle looks only ------------------------------------------------------------------------------------------

@POSIX
def test_the_state_folder_is_private_when_created_and_an_open_one_is_reported_not_closed_silently(tmp_path,
                                                                                                   monkeypatch):
    """next-fixes 118: the settings set 0700 on every read of the per-user folder, so Mojo's state.permissions rarely
    fired. Now a new folder is private from the start, an existing one keeps its mode, and Mojo reports an open one
    (Mojo Miracle repairs it after the person's yes)."""
    from orchestrator.Core.config_manager import ConfigManager
    home = tmp_path / "people" / ".llm-framework"
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(home))
    config = ConfigManager(tmp_path / "configs")
    assert config.user_state_dir == home and stat.S_IMODE(home.stat().st_mode) == 0o700
    home.chmod(0o755)
    assert config.user_state_dir == home and stat.S_IMODE(home.stat().st_mode) == 0o755, "an existing folder keeps it"
    (finding,) = check_user_state(SimpleNamespace(config=config))
    assert finding.code == "state.permissions" and finding.severity == Severity.WARN
    assert stat.S_IMODE(home.stat().st_mode) == 0o755, "looking changes nothing"


def test_a_file_where_the_state_folder_belongs_is_refused_not_used(tmp_path, monkeypatch):
    """An existing folder keeps its mode, but a file of that name is no per-user folder: refused at once, as before,
    instead of being handed to every caller as a folder."""
    from orchestrator.Core.config_manager import ConfigManager
    home = tmp_path / ".llm-framework"
    home.write_text("not a folder")
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(home))
    with pytest.raises(OSError):
        ConfigManager(tmp_path / "configs").user_state_dir


@POSIX
def test_oracle_changes_nothing(open_state):
    framework = framework_with(open_state)
    report = Mojo(framework, [check_user_state]).run()
    (finding,) = report.findings
    assert finding.code == "state.permissions" and finding.repair_id == "state.tighten"
    assert finding.where == str(open_state) and "other users" in finding.why.lower()
    offer = offer_for(framework, finding)
    assert offer.verdict == "miracle" and offer.plan.steps[0].before == "0755" and offer.plan.steps[0].after == "0700"
    assert stat.S_IMODE(open_state.stat().st_mode) == 0o755, "looking changes nothing"
    assert not (open_state / "mojo").exists() and framework.tool_registry.audit.entries == []


# --- Mojo Miracle: harmless repairs, audited, checked again ---------------------------------------------------------

@POSIX
def test_miracle_narrows_the_state_folder_audits_it_and_checks_again(open_state):
    framework = framework_with(open_state)
    result = Mojo(framework, [check_user_state]).miracle(actor="cli", at_terminal=False)
    (entry,) = result.entries
    assert entry.outcome == "repaired" and "only you can open the state folder" in entry.summary
    assert stat.S_IMODE(open_state.stat().st_mode) == 0o700 and result.exit_code == 0
    started, ended = framework.tool_registry.audit.entries
    assert (started["outcome"], ended["outcome"]) == ("started", "ok") and started["tool"] == "mojo"
    extra = started["extra"]
    assert extra["finding_code"] == "state.permissions" and extra["repair_class"] == "delegable"
    assert extra["repair_id"] == "state.tighten" and len(extra["plan_sha256"]) == 64
    assert str(open_state) not in str(extra), "the audit holds the plan's hash, never its path"


@POSIX
def test_chmod_is_compare_and_swap_on_the_folder(open_state):
    framework = framework_with(open_state)
    finding = check_user_state(framework)[0]
    plan = state_repair.plan(framework, finding)
    os.chmod(open_state, 0o750)          # someone changed it after the plan was shown
    result = apply_repair(framework, "state.permissions", actor="cli", expect_sha256=plan.sha256())
    assert result.outcome == "plan_changed" and "nothing was changed" in result.reason
    assert stat.S_IMODE(open_state.stat().st_mode) == 0o750
    with pytest.raises(StepChanged):     # the step itself refuses a mode that is not the plan's
        state_repair.apply_step(framework, plan.steps[0])
    assert stat.S_IMODE(open_state.stat().st_mode) == 0o750


@POSIX
def test_miracle_binds_each_repair_to_the_plan_that_was_shown(open_state):
    """Mojo Miracle gets the offers the person saw and applies exactly their plans: when the problem changed meanwhile,
    nothing is changed (plan_changed), it never makes a new plan and applies it unseen."""
    framework = framework_with(open_state)
    engine = Mojo(framework, [check_user_state])
    before = engine.run()
    shown = offers(framework, before)        # the plan 0755 -> 0700, as Mojo Oracle printed it
    os.chmod(open_state, 0o777)
    result = engine.miracle(actor="cli", at_terminal=True, before=before, offered=shown)
    (entry,) = result.entries
    assert entry.outcome == "plan_changed" and "nothing was changed" in entry.reason
    assert stat.S_IMODE(open_state.stat().st_mode) == 0o777
    assert framework.tool_registry.audit.entries == [], "nothing was started"
    again = engine.miracle(actor="cli", at_terminal=True, before=before, offered=offers(framework, engine.run()))
    assert again.entries[0].outcome == "repaired" and stat.S_IMODE(open_state.stat().st_mode) == 0o700


@POSIX
def test_repair_refuses_a_linked_state_folder(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    os.chmod(real, 0o755)
    link = tmp_path / "state"
    link.symlink_to(real)
    framework = framework_with(link)
    finding = check_user_state(framework)[0]
    assert finding.code == "state.permissions"
    offer = offer_for(framework, finding)
    assert offer.verdict == "not_automatic" and "link" in offer.reason
    result = Mojo(framework, [check_user_state]).miracle(actor="cli", at_terminal=False)
    assert result.entries[0].outcome == "not_done"
    assert stat.S_IMODE(real.stat().st_mode) == 0o755, "a link is never followed"
    step = RepairStep(kind="chmod", target_label="the state folder", full_path=str(link), before="0755",
                      after="0700", undoable=True)
    with pytest.raises(OSError):          # opened without following the link
        state_repair.apply_step(framework, step)
    assert stat.S_IMODE(real.stat().st_mode) == 0o755


@POSIX
def test_without_audit_nothing_is_applied(open_state):
    for framework in (framework_with(open_state, Audit(fail=True)),
                      SimpleNamespace(config=SimpleNamespace(user_state_dir=open_state))):
        result = apply_repair(framework, "state.permissions", actor="cli")
        assert result.outcome == "failed" and "audit log" in result.reason and "nothing was changed" in result.reason
        assert stat.S_IMODE(open_state.stat().st_mode) == 0o755


@POSIX
def test_a_second_miracle_is_refused(open_state):
    from orchestrator.utils.file_lock import file_lock
    framework = framework_with(open_state)
    with file_lock(lock_path(framework), timeout=1):
        result = apply_repair(framework, "state.permissions", actor="cli")
    assert result.outcome == "refused" and "another Mojo Miracle is running" in result.reason
    assert stat.S_IMODE(open_state.stat().st_mode) == 0o755


@POSIX
def test_still_present_is_reported_honestly(open_state, monkeypatch):
    framework = framework_with(open_state)
    monkeypatch.setitem(REPAIRS, "state.tighten", REPAIRS["state.tighten"].__class__(
        **{**REPAIRS["state.tighten"].__dict__, "apply_step": lambda fw, step: None}))
    result = apply_repair(framework, "state.permissions", actor="cli")
    assert result.outcome == "still_present" and "still finds the problem" in result.reason
    assert [e["outcome"] for e in framework.tool_registry.audit.entries] == ["started", "ok"]


@POSIX
def test_already_fixed_changes_nothing(open_state):
    framework = framework_with(open_state)
    os.chmod(open_state, 0o700)
    result = apply_repair(framework, "state.permissions", actor="cli")
    assert result.outcome == "already_fixed" and framework.tool_registry.audit.entries == []


@POSIX
def test_the_engine_applies_only_a_harmless_plan_whoever_asks(open_state, monkeypatch):
    """A repair whose plan deletes or widens is never applied by Mojo Miracle, even when it is called directly (the
    CLI and the tool already refuse it before; the engine refuses it again)."""
    applied = []
    real = REPAIRS["state.tighten"]
    for kind, verdict in (("delete", "deletion_list"), ("widen", "person"), ("system", "admin")):
        def plan(framework, finding, kind=kind):
            base = real.plan(framework, finding)
            return base.model_copy(update={"steps": base.steps + (RepairStep(kind=kind, target_label="x",
                                                                             undoable=True),)})
        monkeypatch.setitem(REPAIRS, "state.tighten", real.__class__(
            **{**real.__dict__, "plan": plan, "apply_step": lambda fw, step: applied.append(step)}))
        framework = framework_with(open_state)
        finding = check_user_state(framework)[0]
        assert offer_for(framework, finding).verdict == verdict
        result = apply_repair(framework, "state.permissions", actor="cli", decision="person", answered_by="person")
        assert result.outcome == "refused" and "not harmless" in result.reason, kind
        assert applied == [] and framework.tool_registry.audit.entries == [], kind
    assert stat.S_IMODE(open_state.stat().st_mode) == 0o755


# --- the runtime: switch to an intact build -------------------------------------------------------------------------

def test_switch_intact_moves_to_an_intact_build_only(tmp_path):
    from test_runtime_build import store_build

    from orchestrator.runtime.build.store import BuildStore
    state = tmp_path / "state"
    store = BuildStore(state)
    build = store_build(store, tmp_path)
    missing = "f" * 64
    store.root.mkdir(parents=True, exist_ok=True)
    store.active_file.write_text('{"build_id": "%s"}' % missing)
    framework = framework_with(state)
    from orchestrator.Core.mojo.checks import check_runtime
    found = [f for f in check_runtime(framework) if f.code == "runtime.build.missing"]
    assert found and found[0].repair_id == "runtime.switch_intact"
    offer = offer_for(framework, found[0])
    assert offer.verdict == "miracle" and offer.plan.steps[0].after == build.build_id[:16]
    result = apply_repair(framework, "runtime.build.missing", actor="cli", expect_sha256=offer.plan.sha256())
    assert result.outcome == "repaired", result.reason
    assert store.references()["active"] == build.build_id
    # a damaged build is never switched to
    store.active_file.write_text('{"build_id": "%s"}' % missing)
    (build.bin_dir / sorted(build.manifest["files"])[0]).write_text("changed")
    offer = offer_for(framework, found[0])
    assert offer.verdict == "not_automatic" and "no intact" in offer.reason


# --- a deletion is never a repair ------------------------------------------------------------------------------------

# What deletes or opens a deletion: the framework's image deletion and the person's lists, the trash, and the calls that
# remove a file, a folder or a Docker object.
DELETING_NAMES = {"delete_framework_images", "_doctor_free_space", "_mojo_free_space", "confirm_deletion",
                  "free_framework_images", "accept", "accepted_through_registry", "Trash", "remove_tree"}
DELETING_CALLS = {"remove", "rmtree", "unlink", "rmdir", "removedirs", "prune", "prune_builds", "prune_images",
                  "prune_containers", "prune_volumes", "remove_tree", "move", "empty", "delete"}


def test_no_mojo_module_an_ai_reaches_deletes():
    """run_mojo_oracle and run_mojo_miracle run Mojo's checks, offers, views, runner and repairs. None of these modules
    deletes or opens a deletion: deleting stays in docker_objects.py, which only the person's terminal and the
    framework's own window call (the wave-2 sweep covers tools/, the MCP server and Ditto, not these modules)."""
    root = REPO / "orchestrator" / "Core" / "mojo"
    modules = [p for p in root.rglob("*.py") if p.name not in ("docker_objects.py", "__init__.py")
               or p.parent != root]
    assert {"runner.py", "offers.py", "views.py", "engine.py", "state.py", "runtime.py"} <= {p.name for p in modules}
    for path in modules:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {a.name.split(".")[-1] for a in node.names} | {getattr(node, "module", "") or ""}
                assert not names & (DELETING_NAMES | {"orchestrator.Core.trash"}), \
                    f"{path.relative_to(REPO)}:{node.lineno} imports a deletion"
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                assert name not in DELETING_NAMES | DELETING_CALLS, \
                    f"{path.relative_to(REPO)}:{node.lineno} calls {name}"


def disk_problem(_fw):
    return [Finding("disk", "disk.cache", Severity.ERROR, "Only 1.0 GB free for the cache folder.", "hint",
                    fix_tier=2, where="/srv/cache", why="a full disk stops a build", cannot="deletion_list")]


def test_miracle_never_deletes_and_says_where_the_person_chooses():
    calls = []
    result = Mojo(None, [disk_problem]).miracle(actor="cli", at_terminal=False,
                                                free_space=lambda: calls.append(1) or (3, ""))
    (entry,) = result.entries
    assert entry.outcome == "not_done" and calls == [], "without a terminal the list is never opened"
    assert entry.reason.startswith(REASONS["deletion_list"]) and "Tools > Framework Build Images" in entry.reason


def test_at_a_terminal_the_persons_list_decides():
    gone = {"now": False}

    def disk(_fw):
        return [] if gone["now"] else disk_problem(_fw)

    declined = Mojo(None, [disk]).miracle(actor="cli", at_terminal=True, free_space=lambda: (0, ""))
    assert declined.entries[0].outcome == "declined" and "nothing was deleted" in declined.entries[0].reason
    unlisted = Mojo(None, [disk]).miracle(actor="cli", at_terminal=True,
                                          free_space=lambda: (0, "Docker is not available"))
    assert unlisted.entries[0].outcome == "not_done"
    assert unlisted.entries[0].reason == "nothing was deleted: Docker is not available", "the real reason, not a no"

    def delete_two():
        gone["now"] = True
        return 2, ""
    done = Mojo(None, [disk]).miracle(actor="cli", at_terminal=True, free_space=delete_two)
    assert done.entries[0].outcome == "repaired" and "2 item" in done.entries[0].summary
    gone["now"] = False
    still = Mojo(None, [disk]).miracle(actor="cli", at_terminal=True, free_space=lambda: (1, ""))
    assert still.entries[0].outcome == "still_present"


def test_every_problem_is_in_the_miracle_report():
    def many(_fw):
        return [Finding("a", "a.person", Severity.ERROR, "m", why="w", cannot="person"),
                Finding("a", "a.admin", Severity.WARN, "m", why="w", cannot="admin"),
                Finding("a", "a.none", Severity.WARN, "m", why="w", cannot="not_automatic"),
                Finding("a", "a.fine", Severity.OK, "m"), Finding("a", "a.note", Severity.INFO, "m")]
    result = Mojo(None, [many]).miracle(actor="cli", at_terminal=True)
    assert [(e.finding.code, e.outcome) for e in result.entries] == [
        ("a.person", "not_done"), ("a.admin", "not_done"), ("a.none", "not_done")]
    assert [e.reason for e in result.entries] == [REASONS["person"], REASONS["admin"], REASONS["not_automatic"]]
    assert result.not_repaired == result.entries and result.repaired == []


def test_a_problem_that_appears_after_the_repairs_is_reported():
    """The check afterwards can find what was not there before: it is listed, never hidden behind "repaired"."""
    def before(_fw):
        return [Finding("a", "a.person", Severity.ERROR, "m", where="x", why="w", cannot="person")]

    def after(_fw):
        return before(_fw) + [Finding("a", "a.new", Severity.WARN, "new", where="y", why="w", cannot="admin")]
    result = Mojo(None, [after]).miracle(actor="cli", at_terminal=False, before=Mojo(None, [before]).run())
    assert [f.code for f in result.new_problems] == ["a.new"]
    assert [e.finding.code for e in result.entries] == ["a.person"], "the old problem is reported as not repaired"
    assert result.exit_code == 2

