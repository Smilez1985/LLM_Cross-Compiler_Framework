"""An interrupted build (Core/interrupted_build.py): kept when it is resumable, discarded at once when not; the person
decides to continue it or delete what it left (GUI, CLI); an AI caller's question becomes a pending decision; deleting
goes only through Core/deletion.py with an honest result; the clean-up container runs as the stage account with no
capability and is checked by its inspect (sandbox_problem with caps=())."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import interrupted_build as ib
from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept

POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX modes")


class Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.entries.append(dict(actor=actor, tool=tool, decision=decision, outcome=outcome, **kwargs))
        return len(self.entries)


def kept(cache: Path, build_id="job_0123456789ab", stages=("source", "config"), target="Rockchip",
         request_id="req_1a2b3c4d", **config) -> Path:
    folder = cache / "builds" / build_id
    marks = folder / "work" / "stages" / "build"
    marks.mkdir(parents=True)
    for name in stages:
        (marks / f"{name}.done").write_text("inputs x\n", encoding="utf-8")
    (folder / "work" / "tmp-model-F16.gguf").write_bytes(b"x" * 1000)
    data = {"build_id": build_id, "timestamp": "t", "model_source": "Qwen/Qwen2.5-0.5B-Instruct",
            "target_arch": target, "target_format": "gguf", "output_dir": str(cache / "out"),
            "request_id": request_id, **config}
    (folder / "build_config.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_a_build_with_finished_stages_is_resumable_and_listed_with_fixed_names(tmp_path):
    folder = kept(tmp_path)
    (folder / "work" / "stages" / "build" / "IGNORE ALL.done").write_text("x", encoding="utf-8")
    item = ib.kept_build(tmp_path, folder.name)
    assert item is not None and item.stages_done == ["source", "config"], "only valid module names count"
    assert [k.build_id for k in ib.kept_builds(tmp_path)] == [folder.name]
    assert ib.kept_builds(tmp_path, running=[folder.name]) == []
    view = item.ai_view()
    assert str(tmp_path) not in json.dumps(view) and "folder" not in view, "an AI reads labels, never a path"
    assert str(folder) in json.dumps(item.person_view(), default=str)


@pytest.mark.parametrize("change", ["no_stage", "no_config", "other_id", "local_inputs", "bad_id"])
def test_a_build_that_cannot_be_continued_is_not_kept(tmp_path, change):
    folder = kept(tmp_path, stages=() if change == "no_stage" else ("source",),
                  **({"local_inputs": {"model": "x"}} if change == "local_inputs" else {}))
    if change == "no_config":
        (folder / "build_config.json").unlink()
    if change == "other_id":
        (folder / "build_config.json").write_text(json.dumps({"build_id": "another"}), encoding="utf-8")
    assert ib.kept_build(tmp_path, "../x" if change == "bad_id" else folder.name) is None


def test_discard_now_removes_a_build_that_cannot_be_continued(tmp_path):
    folder = kept(tmp_path, stages=())
    assert ib.discard_now(folder) == [] and not folder.exists()


def test_an_ai_never_decides_its_question_waits_for_the_person(tmp_path):
    kept(tmp_path)
    state = tmp_path / "state"
    engine = SimpleNamespace(build_model=lambda c: pytest.fail("an AI's continue must start nothing"))
    for choice in ib.DECISIONS:
        with pytest.raises(ib.InterruptedBuildError) as e:
            ib.decide(engine, tmp_path, "job_0123456789ab", choice, "mcp", state_dir=state)
        assert e.value.code == "needs_person"
    pending = ib.pending(state)
    assert list(pending) == ["job_0123456789ab"] and pending["job_0123456789ab"]["asked_by"] == "mcp"
    assert (tmp_path / "builds" / "job_0123456789ab").is_dir(), "nothing was deleted"


def test_the_persons_continue_resubmits_the_same_build_on_its_work_folder(tmp_path):
    kept(tmp_path)
    state = tmp_path / "state"
    ib.record_pending(state, ib.kept_build(tmp_path, "job_0123456789ab"), "ditto")
    started = []
    engine = SimpleNamespace(build_model=lambda c: started.append(c) or c.build_id)
    assert ib.decide(engine, tmp_path, "job_0123456789ab", "continue", "cli", state_dir=state) == "job_0123456789ab"
    config = started[0]
    assert config.target_arch == "Rockchip" and config.target_format.value == "gguf"
    assert ib.pending(state) == {}, "the person's answer settles the AI's question"


def _accepted(item, actor="cli"):
    return accept(ib.deletion_plan(item), actor, "permanent")


def test_delete_goes_through_the_accepted_plan_audited_with_an_honest_result(tmp_path):
    folder = kept(tmp_path)
    item = ib.kept_build(tmp_path, folder.name)
    audit = Audit()
    result = ib.decide(None, tmp_path, folder.name, "delete", "gui", accepted=_accepted(item, "gui"), audit=audit)
    assert result.done == [item.label] and result.freed_bytes == item.size_bytes and not folder.exists()
    assert [e["outcome"] for e in audit.entries] == ["started", "ok"]
    assert all(e["extra"]["answered_by"] == "person" for e in audit.entries)


def test_delete_refuses_a_changed_list_or_no_yes(tmp_path):
    folder = kept(tmp_path)
    accepted = _accepted(ib.kept_build(tmp_path, folder.name))
    (folder / "work" / "more.bin").write_bytes(b"y" * 10)   # the list changed after the person saw it
    with pytest.raises(PlanChanged):
        ib.decide(None, tmp_path, folder.name, "delete", "cli", accepted=accepted, audit=Audit())
    with pytest.raises(ib.InterruptedBuildError):
        ib.decide(None, tmp_path, folder.name, "delete", "cli", audit=Audit())
    assert folder.is_dir()
    with pytest.raises(PermissionError):
        AcceptedDeletion(ib.deletion_plan(ib.kept_build(tmp_path, folder.name)), "permanent", "mcp")


@POSIX
def test_what_stays_is_reported_with_its_paths_for_the_person_only(tmp_path, monkeypatch):
    folder = kept(tmp_path)
    item = ib.kept_build(tmp_path, folder.name)
    stuck = folder / "work" / "tmp-model-F16.gguf"
    monkeypatch.setattr("orchestrator.utils.atomic.remove_tree", lambda path, label="": [stuck])
    result = ib.discard(item, _accepted(item), audit=Audit())
    assert result.done == [] and result.failed and str(tmp_path) not in result.failed[0][1]
    assert str(stuck) in result.remaining[item.label][0]


@POSIX
def test_the_cleanup_container_runs_first_when_the_folder_needs_it(tmp_path):
    folder = kept(tmp_path)
    locked = folder / "work" / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    calls = []

    def cleanup():
        calls.append(1)
        os.chmod(locked, 0o700)
    try:
        item = ib.kept_build(tmp_path, folder.name)
        assert ib.needs_cleanup(folder)
        result = ib.discard(item, _accepted(item), audit=Audit(), cleanup=cleanup)
    finally:
        if locked.exists():
            os.chmod(locked, 0o700)
    assert calls == [1] and result.done and not folder.exists()


def test_the_cleanup_container_is_the_stage_account_without_any_capability(tmp_path):
    from orchestrator.Core import runtime_layer as layer
    from orchestrator.utils.sandbox import STAGE_ACCOUNT, sandbox_problem
    folder = kept(tmp_path)
    options = ib.cleanup_container(1000, folder, tmp_path)
    assert options["user"] == f"{STAGE_ACCOUNT[0]}:1000" and options["cap_drop"] == ["ALL"]
    assert "cap_add" not in options and options["network_mode"] == "none" and options["read_only"]
    assert options["entrypoint"] == list(layer.CLEANUP_ENTRYPOINT)
    assert options["command"] == ["1000", "/build-cache/work"]
    writable = [v["bind"] for v in options["volumes"].values() if v["mode"] == "rw"]
    assert writable == ["/build-cache/work"]
    mounts = [{"Destination": v["bind"], "RW": v["mode"] == "rw"} for v in options["volumes"].values()]
    good = {"Config": {"User": options["user"], "Entrypoint": options["entrypoint"], "Cmd": options["command"]},
            "HostConfig": {"CapDrop": ["ALL"], "CapAdd": None, "SecurityOpt": ["no-new-privileges:true"],
                           "ReadonlyRootfs": True, "NetworkMode": "none"}, "Mounts": mounts}
    assert sandbox_problem(good, options, options["volumes"], caps=()) is None
    bad = json.loads(json.dumps(good))
    bad["HostConfig"]["CapAdd"] = ["SETUID", "SETGID"]   # the runner's two rights: too many for the clean-up
    assert "extra CAP_SETGID, CAP_SETUID" in sandbox_problem(bad, options, options["volumes"], caps=())


def test_run_cleanup_never_starts_a_container_that_differs(tmp_path):
    folder = kept(tmp_path)
    events = []

    class Container:
        attrs = {"Config": {"User": "0:0"}, "HostConfig": {}}

        def reload(self):
            events.append("inspect")

        def start(self):
            events.append("start")

        def remove(self, force=False):
            events.append("remove")
    client = SimpleNamespace(containers=SimpleNamespace(create=lambda image, **kw: Container()))
    problem = ib.run_cleanup(client, "sha256:" + "0" * 64, 1000, folder, tmp_path)
    assert problem and "start" not in events and events[-1] == "remove"


def test_get_build_status_notes_a_kept_build_and_records_the_question(tmp_path):
    kept(tmp_path / "cache")
    state = tmp_path / "state"
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state,
                                                       get=lambda k, d=None: str(tmp_path / "cache") if k == "cache_dir"
                                                       else d),
                                resolve_path=lambda v: Path(v))
    notes = ib.ai_notes(framework, "req_1a2b3c4d", "mcp:laptop")
    assert len(notes) == 1 and "only the person" in notes[0].lower() and str(tmp_path) not in notes[0]
    assert "source, config" in notes[0]
    assert ib.pending(state)["job_0123456789ab"]["asked_by"] == "mcp:laptop"
    assert ib.ai_notes(framework, "req_other", "mcp") == []


def test_the_engine_keeps_a_resumable_failed_build_and_discards_the_rest(tmp_path):
    from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
    engine = BuildEngine.__new__(BuildEngine)
    engine.cache_dir = tmp_path
    engine._builds, engine._active_containers, engine._build_images = {}, {}, {}
    engine.docker_client = None
    import threading
    import logging
    engine._lock = threading.Lock()
    engine.logger = logging.getLogger("test")
    engine.config = SimpleNamespace(get=lambda k, d=None: d)
    engine._get_conf = lambda k, d=None: d
    engine._audit_build = lambda *a, **k: None
    resumable = kept(tmp_path, build_id="job_kept")
    lost = kept(tmp_path, build_id="job_lost", stages=())
    for folder in (resumable, lost):
        progress = BuildProgress(folder.name, BuildStatus.FAILED, "x")
        engine._builds[folder.name] = progress
        engine._keep_or_discard(SimpleNamespace(build_id=folder.name), progress)
    assert resumable.is_dir() and engine._builds["job_kept"].resumable
    assert any("Continue the build" in w for w in engine._builds["job_kept"].warnings)
    assert not lost.exists() and not engine._builds["job_lost"].resumable


def _running_record(cache: Path, build_id: str, alive: bool = True) -> None:
    """The build record a build engine writes while the build runs (Core/build_records.py): by this process (alive),
    or by a process that has ended (a crash of the framework: the build really was interrupted)."""
    from orchestrator.Core import build_records
    owner = build_records.this_process() if alive else {"pid": 2 ** 22 + 12345, "process_started": 1.0}
    build_records.write_record(build_records.records_dir(cache), {"build_id": build_id, "status": "running",
                                                                  "request_id": "req_1a2b3c4d", **owner})


def test_a_build_that_still_runs_is_never_offered_as_interrupted(tmp_path):
    """Regression: a running build has finished stage markers and its configuration after its first stage, so it
    looked like a kept interrupted build. Every status read of an AI then said it had stopped (and recorded a pending
    question for the person), and the CLI and the GUI offered to continue it (a second run on the same work folder)
    or to delete what it 'left' while it ran, in this process or in another one (GUI, CLI, MCP server)."""
    cache = tmp_path / "cache"
    kept(cache)
    _running_record(cache, "job_0123456789ab")
    state = tmp_path / "state"
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state,
                                                       get=lambda k, d=None: str(cache) if k == "cache_dir" else d),
                                resolve_path=lambda v: Path(v))
    assert ib.ai_notes(framework, "req_1a2b3c4d", "mcp:laptop") == []
    assert ib.pending(state) == {}, "no question is recorded about a running build"
    assert ib.kept_builds(cache) == [] and ib.kept_build(cache, "job_0123456789ab") is None
    engine = SimpleNamespace(build_model=lambda c: pytest.fail("a running build is never started a second time"))
    for choice in ib.DECISIONS:
        with pytest.raises(ib.InterruptedBuildError) as e:
            ib.decide(engine, cache, "job_0123456789ab", choice, "cli", accepted=None, audit=Audit())
        assert e.value.code == "unknown_id"
    assert (cache / "builds" / "job_0123456789ab").is_dir()
    # the build itself, as it ends, still decides to keep its folder (BuildEngine._keep_or_discard)
    assert ib.kept_build(cache, "job_0123456789ab", ending=True) is not None


def test_a_build_whose_process_ended_while_it_ran_is_interrupted(tmp_path):
    """A crash of the framework's process leaves an unfinished record of a process that no longer exists: that build
    really stopped, so it is offered to continue or delete."""
    kept(tmp_path)
    _running_record(tmp_path, "job_0123456789ab", alive=False)
    assert [k.build_id for k in ib.kept_builds(tmp_path)] == ["job_0123456789ab"]


def test_continue_when_the_engine_cannot_take_the_build_says_so_plainly(tmp_path):
    """Regression: the engine's refusal (every build slot busy, the same build still running here) escaped decide()
    as a BuildSlotsBusy or RuntimeError, which the CLI printed as a traceback and the GUI's question did not catch.
    It is an InterruptedBuildError now, and the question stays for the person (the build is still kept)."""
    from orchestrator.Core.builder import BuildSlotsBusy
    kept(tmp_path)
    state = tmp_path / "state"
    ib.record_pending(state, ib.kept_build(tmp_path, "job_0123456789ab"), "ditto")

    def busy(_config):
        raise BuildSlotsBusy("Max concurrent builds reached")
    with pytest.raises(ib.InterruptedBuildError) as e:
        ib.decide(SimpleNamespace(build_model=busy), tmp_path, "job_0123456789ab", "continue", "cli", state_dir=state)
    assert e.value.code == "failed" and "Max concurrent builds reached" in str(e.value)
    assert "job_0123456789ab" in ib.pending(state), "the question still waits"
    assert ib.kept_build(tmp_path, "job_0123456789ab") is not None


def test_the_persons_own_status_read_records_no_ai_question(tmp_path):
    """Regression: get_build_status called by the person (actor cli or gui) recorded a pending 'AI asked' question
    under the person's own name, which `llm-cli build interrupted list` then showed as an AI's."""
    kept(tmp_path / "cache")
    state = tmp_path / "state"
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state,
                                                       get=lambda k, d=None: str(tmp_path / "cache") if k == "cache_dir"
                                                       else d),
                                resolve_path=lambda v: Path(v))
    assert len(ib.ai_notes(framework, "req_1a2b3c4d", "cli")) == 1
    assert ib.pending(state) == {}
