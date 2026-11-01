"""A continued interrupted build shows its progress like a started build (the decision that progress is shown
wherever a build can be watched): `llm-cli build interrupted continue` follows it with the one follow loop of
`build start` (status lines with stage and percent, Ctrl+C cancels it, the end and its exit code), the GUI's
[Continue] hands the build to the Builder's progress panel, and an AI caller's get_build_status reads its stage and
percent. All of it because the continued build is a build request of the one build path (BuildService
continue_interrupted over the orchestrator), on its own build id and kept work folder.

Every engine here is a stand-in (no Docker); documentation names only.
"""

import io
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import interrupted_build as ib
from orchestrator.Core.builder import BuildStatus
from test_interrupted_build import kept

BUILD_ID = "job_0123456789ab"
STAGE = "Stage 3/4: convert"


class ResumeEngine:
    """A build engine stand-in: the build runs at 40 % in its convert stage until `finish` is set or it is
    cancelled ('its container is stopped')."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.configs, self.cancelled = [], []
        self.started, self.finish = threading.Event(), threading.Event()

    def resolve_target_dir(self, name):
        return Path(".")

    def build_model(self, config):
        self.configs.append(config)
        self.started.set()
        return config.build_id

    def _status(self, build_id):
        if build_id in self.cancelled:
            return BuildStatus.CANCELLED
        return BuildStatus.COMPLETED if self.finish.is_set() else BuildStatus.BUILDING

    def get_build_status(self, build_id):
        if not any(c.build_id == build_id for c in self.configs):
            return None
        return SimpleNamespace(status=self._status(build_id), progress_percent=40, current_stage=STAGE, errors=[],
                               logs=[], published=None, end_reason=None, resources=None)

    def cancel_build(self, build_id):
        self.cancelled.append(build_id)
        return True

    def is_active(self, build_id):
        return self._status(build_id) == BuildStatus.BUILDING

    def list_builds(self):
        return []


@pytest.fixture
def resumed(booted_framework, tmp_path):
    """A booted framework whose orchestrator runs on the stand-in engine, with one kept interrupted build."""
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.orchestrator import LLMOrchestrator
    cache = tmp_path / "cache"
    kept(cache, build_id=BUILD_ID, output_dir=str(tmp_path / "out" / "Rockchip" / "model"))
    engine = ResumeEngine(cache)
    orch = LLMOrchestrator(booted_framework.config, build_engine=engine)
    booted_framework.orchestrator = orch
    service = BuildService(booted_framework, orchestrator=orch)
    return SimpleNamespace(framework=booted_framework, engine=engine, orch=orch, service=service, cache=cache,
                           state=tmp_path / "state")


def _until(check, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("not reached in time")


def test_a_continued_build_is_a_build_request_on_its_own_build_id(resumed):
    """Regression (failed before: continue ran the engine directly, outside any build request, so nothing could
    follow, cancel or report it the way a started build is): the kept configuration runs as the one job of a new
    request, with its own build id (its kept work folder) and output folder."""
    ib.record_pending(resumed.state, ib.kept_build(resumed.cache, BUILD_ID), "ditto")
    rid = ib.decide(resumed.engine, resumed.cache, BUILD_ID, "continue", "cli", state_dir=resumed.state,
                    service=resumed.service)
    assert rid.startswith("req_") and rid != BUILD_ID
    assert resumed.engine.started.wait(10)
    (config,) = resumed.engine.configs
    assert (config.build_id, config.request_id) == (BUILD_ID, rid)
    assert config.target_arch == "Rockchip" and config.output_dir.endswith("model")
    assert ib.pending(resumed.state) == {}, "the person's answer settles the AI's question"
    state = resumed.orch._workflows[rid]
    assert state.total_builds == 1 and state.build_ids == [BUILD_ID]
    resumed.engine.finish.set()
    _until(lambda: resumed.service.get_status(rid).status == "completed")


def test_its_status_reports_stage_and_percent_like_a_started_build(resumed):
    """The build service's status of the continued request (what get_build_status, the Builder's panel and the
    CLI's follow line read) counts the running build's own step and percent in."""
    rid = ib.decide(resumed.engine, resumed.cache, BUILD_ID, "continue", "gui", service=resumed.service)
    status = _until(lambda: (lambda s: s if s.status == "building" and s.progress_percent else None)(
        resumed.service.get_status(rid)))
    assert status.stage.endswith(STAGE) and status.progress_percent == 40 and status.total_builds == 1
    stage, percent = resumed.service.live_progress(resumed.service.request_state(rid))
    assert stage.endswith(STAGE) and percent == 40
    resumed.engine.finish.set()
    _until(lambda: resumed.service.get_status(rid).status == "completed")


def test_an_ai_reads_the_continued_builds_progress_with_get_build_status(resumed):
    """MCP: the person continued the build; an AI caller's get_build_status of its request shows the stage and the
    percent as for any started build (labels only)."""
    from orchestrator.tools.policy import ToolPolicy
    from orchestrator.tools.registry import ToolContext
    fm = resumed.framework
    fm.tool_registry.policy_store.replace(ToolPolicy(configured=True,
                                                     reviewed=[s.name for s in fm.tool_registry.list()]))
    rid = ib.decide(resumed.engine, resumed.cache, BUILD_ID, "continue", "cli", service=resumed.service)
    assert resumed.engine.started.wait(10)

    def building():
        out = fm.tool_registry.call("get_build_status", {"request_id": rid}, ToolContext("mcp", session="s"))
        return out if out.status == "building" and out.progress_percent else None
    out = _until(building)
    assert out.stage.endswith(STAGE) and out.progress_percent == 40
    assert str(resumed.cache) not in out.model_dump_json()
    resumed.engine.finish.set()
    _until(lambda: resumed.service.get_status(rid).status == "completed")


def test_an_ai_still_cannot_continue_it(resumed):
    with pytest.raises(ib.InterruptedBuildError) as refused:
        ib.decide(resumed.engine, resumed.cache, BUILD_ID, "continue", "mcp", service=resumed.service)
    assert refused.value.code == "needs_person" and not resumed.engine.configs


def test_without_a_build_service_the_engine_path_stays_as_it_was(tmp_path):
    """Characterization (deprecated path): a caller without a build service still runs the configuration on the
    engine directly and gets the build id back."""
    kept(tmp_path, build_id=BUILD_ID)
    started = []
    engine = SimpleNamespace(build_model=lambda c: started.append(c) or c.build_id)
    assert ib.decide(engine, tmp_path, BUILD_ID, "continue", "cli") == BUILD_ID and started


# --- the CLI: the one follow loop of build start ---------------------------------------------------------------

def _cli(resumed, monkeypatch, pause):
    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module
    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=500, color_system=None))
    monkeypatch.setattr(cli_module, "_pause", pause)

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = resumed.framework
            self.orchestrator = resumed.orch
            return self

    result = CliRunner().invoke(cli_module.cli, ["-q", "build", "interrupted", "continue", BUILD_ID], obj=Ctx())
    return result, printed.getvalue() + result.output


def test_cli_continue_shows_the_progress_of_build_start_and_its_end(resumed, monkeypatch):
    """Regression (failed before: `build interrupted continue` printed one line, 'follow it with: llm-cli build
    status', and ended): it follows the continued build with build start's status lines (stage and percent), says how
    it ended and exits with that end's code."""
    def pause(seconds):
        if resumed.engine.started.is_set() and resumed.orch._workflows:
            (state,) = resumed.orch._workflows.values()
            if state.build_ids:
                resumed.engine.finish.set()
        time.sleep(0.05)
    result, out = _cli(resumed, monkeypatch, pause)
    assert result.exit_code == 0, out
    assert f"Build {BUILD_ID} continues as build request req_" in out
    assert "Following build progress (Ctrl+C cancels the build)" in out
    assert "Status: building - " in out and f"{STAGE} (40 %, 0/1 builds)" in out, out
    assert "Status: completed" in out and "follow it with" not in out


def test_cli_ctrl_c_cancels_the_continued_build_like_a_started_one(resumed, monkeypatch):
    def interrupt(seconds):
        assert resumed.engine.started.wait(10), "the build never reached the engine"
        raise KeyboardInterrupt
    result, out = _cli(resumed, monkeypatch, interrupt)
    assert result.exit_code == 130, out
    assert resumed.engine.cancelled == [BUILD_ID], "the continued build's container was not stopped"
    assert "cancelled: its container was stopped" in out


def test_build_start_and_continue_share_the_one_follow_loop():
    """One helper, no copy: both commands reach _follow_build, and the loop's text lives there only."""
    import inspect

    from orchestrator import cli as cli_module
    start = inspect.getsource(cli_module.start_build.callback)
    cont = inspect.getsource(cli_module.build_interrupted_continue.callback)
    assert "_follow_build(" in start and "_follow_build(" in cont
    whole = inspect.getsource(cli_module)
    assert whole.count("Following build progress (Ctrl+C cancels the build)") == 1


# --- the GUI: [Continue] hands the build to the Builder's progress panel -----------------------------------------

def test_the_gui_continue_follows_the_build_in_the_builder(qapp, tmp_path, monkeypatch):
    """Regression (failed before: [Continue] said the Builder shows the progress, but nothing followed the build):
    the build service queues it and the request goes to the window that shows its progress."""
    from orchestrator.gui import interrupted_build_dialog as dialog_module
    kept(tmp_path / "cache", build_id=BUILD_ID)
    queued, followed, boxes = [], [], []
    service = SimpleNamespace(continue_interrupted=lambda cfg, description="": queued.append(cfg) or SimpleNamespace(
        request_id="req_cont0001"))
    framework = SimpleNamespace(build_service=service, config=SimpleNamespace(user_state_dir=str(tmp_path / "state")),
                                orchestrator=SimpleNamespace(build_engine=SimpleNamespace(cache_dir=tmp_path / "cache")))

    from PySide6.QtWidgets import QMessageBox

    class ContinueBox(QMessageBox):
        def exec(self):
            return 0

        def clickedButton(self):
            return next(b for b in self.buttons() if self.buttonRole(b) == QMessageBox.AcceptRole)
    monkeypatch.setattr(dialog_module, "QMessageBox", ContinueBox)
    monkeypatch.setattr(dialog_module, "plain_box", lambda *a, **k: boxes.append(a[3]))
    outcome = dialog_module.ask_interrupted(None, framework, BUILD_ID, "en", on_continued=followed.append)
    assert outcome == "continued" and followed == ["req_cont0001"]
    assert queued and queued[0].build_id == BUILD_ID
    # Regression (review): the message names the build as Build History lists it (its build id), as before the
    # continued build became a request; the request id goes to the Builder's progress panel only
    assert BUILD_ID in boxes[-1] and "req_cont0001" not in boxes[-1]


def test_the_gui_continue_without_a_build_service_hands_no_build_id_to_the_builder(qapp, tmp_path, monkeypatch):
    """Regression (review): without the framework's build service the engine runs the build directly and returns
    its build id, which is no build request: the Builder must not be asked to follow it."""
    from orchestrator.gui import interrupted_build_dialog as dialog_module
    kept(tmp_path / "cache", build_id=BUILD_ID)
    followed, boxes = [], []
    engine = SimpleNamespace(cache_dir=tmp_path / "cache", build_model=lambda cfg: cfg.build_id)
    framework = SimpleNamespace(build_service=None, config=SimpleNamespace(user_state_dir=str(tmp_path / "state")),
                                orchestrator=SimpleNamespace(build_engine=engine))

    from PySide6.QtWidgets import QMessageBox

    class ContinueBox(QMessageBox):
        def exec(self):
            return 0

        def clickedButton(self):
            return next(b for b in self.buttons() if self.buttonRole(b) == QMessageBox.AcceptRole)
    monkeypatch.setattr(dialog_module, "QMessageBox", ContinueBox)
    monkeypatch.setattr(dialog_module, "plain_box", lambda *a, **k: boxes.append(a[3]))
    outcome = dialog_module.ask_interrupted(None, framework, BUILD_ID, "en", on_continued=followed.append)
    assert outcome == "continued" and followed == [] and BUILD_ID in boxes[-1]


def test_build_history_hands_the_continued_build_to_the_builder_tab(qapp, monkeypatch):
    from orchestrator.gui import build_history_window
    from orchestrator.gui.builder_tab import BuilderTab
    from orchestrator.gui.menus import build as build_menu
    made = {}

    class Dialog:
        def __init__(self, framework, parent, on_continued=None):
            made["on_continued"] = on_continued

        def exec(self):
            return 0
    monkeypatch.setattr(build_history_window, "BuildHistoryDialog", Dialog)
    tracked, shown = [], []
    builder = SimpleNamespace(track_build=tracked.append)
    window = SimpleNamespace(framework=object(), builder_tab=builder,
                             tabs=SimpleNamespace(setCurrentWidget=shown.append))
    build_menu.open_build_history(window)
    made["on_continued"]("req_cont0001")
    assert tracked == ["req_cont0001"] and shown == [builder]
    calls = []
    BuilderTab.track_build(SimpleNamespace(_track_build=calls.append), "req_cont0001")
    assert calls == ["req_cont0001"]
