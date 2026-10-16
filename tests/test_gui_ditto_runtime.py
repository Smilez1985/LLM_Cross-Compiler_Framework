"""
Ditto > Local Runtime... (W1': every `llm-cli ditto runtime ...` command by mouse) and the fixed box "Ditto's local
server" of the AI settings.

Offscreen: the entry is reachable from the menu bar, every handler calls the same core function as the CLI with the
values of the fields, long work runs off the GUI thread, a question the core asks in the middle of a job is asked on
the GUI thread with "No" as the default, deletions go through the deletion dialog with the full list, and every text
exists in English and German. No real build, download, Docker or server: fakes record the core calls.
"""

import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.runtime import runtime_mode
from orchestrator.runtime.build import source as source_module
from orchestrator.runtime.llama_server import LocalRuntimeError

from test_runtime_selfbuilt import CATALOG, PIN, machine, no_source, runtime  # noqa: F401

GUI = REPO_ROOT / "orchestrator" / "gui"
GIB = 1024 ** 3


def wait_until(qapp, condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def on_gui_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


class FakeRuntime:
    """A LocalLLMRuntime stand-in: records each core call with its arguments and whether it ran on the GUI thread."""

    def __init__(self, tmp_path, active=None):
        self.catalog = CATALOG
        self.build = CATALOG.llama_cpp.build
        self.data_dir = tmp_path / "state"
        self.models_dir = tmp_path / "models" / "ditto"
        self.root = self.data_dir / "runtime"
        self.calls = []
        build = SimpleNamespace(build_id=active, backend="cpu") if active else None
        self.store = SimpleNamespace(references=lambda: {"active": active} if active else {},
                                     get=lambda build_id: build)

    def record(self, name, **kw):
        self.calls.append((name, kw, on_gui_thread()))

    def called(self, name):
        return [kw for n, kw, _ in self.calls if n == name]

    def threads(self, name):
        return [gui for n, _, gui in self.calls if n == name]

    def mode(self):
        return "bare-metal"

    def mode_record(self):
        return {}

    def preferred_tier(self):
        return None

    def endpoint(self):
        return None


@pytest.fixture
def view(tmp_path, no_source):
    rt = runtime(tmp_path / "real")
    rt.install()
    from orchestrator.runtime.runtime_overview import overview
    return overview(rt)


@pytest.fixture
def framework(tmp_path):
    class Audit:
        def __init__(self):
            self.rows = []

        def record(self, *a, **k):
            self.rows.append((a, k))
            return len(self.rows)
    config = SimpleNamespace(user_state_dir=tmp_path / "state", get=lambda key, default=None: default)
    (tmp_path / "state").mkdir(exist_ok=True)
    return SimpleNamespace(config=config, tool_registry=SimpleNamespace(audit=Audit(), policy_store=None))


@pytest.fixture
def ui(qapp, tmp_path, monkeypatch, view, framework):
    """The window on a FakeRuntime; questions answered from `answers` (default: No), messages collected."""
    from orchestrator.gui import ditto_runtime_window as win_module
    from orchestrator.gui import runtime_mode_box as box_module
    rt = FakeRuntime(tmp_path)
    asked, shown, answers = [], [], []

    def answer(parent, title, text):
        asked.append((text, on_gui_thread()))
        return answers.pop(0) if answers else False
    monkeypatch.setattr(win_module, "overview", lambda r: view)
    monkeypatch.setattr(win_module, "ask", answer)
    monkeypatch.setattr(box_module, "ask", answer)
    monkeypatch.setattr(win_module, "plain_box", lambda parent, kind, title, text, **k: shown.append(
        (kind, text, k.get("details", ""))))
    monkeypatch.setattr(box_module, "plain_box", lambda parent, kind, title, text, **k: shown.append(
        (kind, text, k.get("details", ""))))
    win = win_module.LocalRuntimeWindow(framework, runtime_factory=lambda: rt)
    wait_until(qapp, lambda: win._status_token is None)
    ns = SimpleNamespace(win=win, rt=rt, asked=asked, shown=shown, answers=answers, module=win_module, qapp=qapp,
                         box_module=box_module)
    yield ns
    if win._job is not None:
        win._cancel.set()
        wait_until(qapp, lambda: win._job is None)
    win.close()
    win.deleteLater()


def done(ui):
    wait_until(ui.qapp, lambda: ui.win._job is None and ui.win.box._token is None)


# --- the menu bar ----------------------------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
class TestMenu:

    def test_ditto_local_runtime_opens_the_window_with_every_object_of_cli_paths(self, qapp, tmp_path, monkeypatch,
                                                                                 memory_keyring, view):
        from PySide6.QtCore import QObject
        from orchestrator.Core.framework import FrameworkManager
        from orchestrator.gui import ditto_runtime_window as win_module
        from orchestrator.gui import main_window
        from orchestrator.gui.menus import ditto_runtime as menu

        (tmp_path / "configs").mkdir()

        class TmpFramework(FrameworkManager):
            def __init__(self, *args, **kwargs):
                super().__init__(str(tmp_path / "configs"))
                self.info.installation_path = tmp_path

        monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
        monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
        monkeypatch.setattr(win_module, "overview", lambda rt: view)
        win = main_window.MainOrchestrator(REPO_ROOT)
        qapp.processEvents()
        try:
            titles = [a.text() for a in win.menuBar().actions()]
            assert "Ditto" in titles and titles.index("Ditto") < titles.index("Security")
            ditto = next(a.menu() for a in win.menuBar().actions() if a.text() == "Ditto")
            assert ditto.objectName() == menu.DITTO_MENU
            action = next(a for a in ditto.actions() if a.objectName() == "act_ditto_runtime_open")
            assert action.text() == "Local Runtime..." and win.act_ditto_runtime is action
            assert menu.ditto_menu(win, win.menuBar()) is ditto, "the shared menu is found, not made twice"
            action.trigger()
            window = win.ditto_runtime_window
            assert isinstance(window, win_module.LocalRuntimeWindow) and window.isVisible()
            wait_until(qapp, lambda: window._status_token is None)
            for command, name in menu.CLI_PATHS.items():
                assert win.findChild(QObject, name) is not None, (command, name)
            action.trigger()
            assert win.ditto_runtime_window is window and len(win.findChildren(win_module.LocalRuntimeWindow)) == 1
            wait_until(qapp, lambda: window._status_token is None)
        finally:
            win.close()

    def test_cli_paths_cover_every_person_facing_command_and_option_of_ditto_runtime(self):
        from orchestrator.cli import ditto_runtime
        from orchestrator.gui.menus import ditto_runtime as menu
        named = set(menu.CLI_PATHS) | set(menu.MACHINE_ONLY)
        assert not set(menu.CLI_PATHS) & set(menu.MACHINE_ONLY)
        for name, command in ditto_runtime.commands.items():
            assert f"ditto runtime {name}" in menu.CLI_PATHS, name
            for param in command.params:
                for option in getattr(param, "opts", []):
                    if option.startswith("--") and param.param_type_name == "option":
                        assert f"ditto runtime {name} {option}" in named or any(
                            f"ditto runtime {name} {o}" in named for o in param.opts), (name, option)
        for path in named:
            words = path.split()
            command = ditto_runtime.commands[words[2]]
            if len(words) > 3:
                assert any(words[3] in p.opts for p in command.params), path


# --- the status (`ditto runtime status`) -----------------------------------------------------------------------------

class TestStatus:

    def test_the_window_shows_the_status_of_the_same_call_off_the_gui_thread(self, qapp, tmp_path, monkeypatch,
                                                                            no_source, framework):
        from orchestrator.gui import ditto_runtime_window as win_module
        from orchestrator.runtime import runtime_overview
        rt = runtime(tmp_path)
        rt.install()
        threads = []
        monkeypatch.setattr(win_module, "overview", lambda r: threads.append(on_gui_thread())
                            or runtime_overview.overview(r))
        win = win_module.LocalRuntimeWindow(framework, runtime_factory=lambda: rt)
        try:
            wait_until(qapp, lambda: win._status_token is None)
            text = win.txt_status.toPlainText()
            assert threads == [False], "the probes and the build plan run off the GUI thread"
            assert f"Hardware: {rt.hardware().summary()}" in text and "In use: self-built: build" in text
            assert "Built: native build, 42 s" in text and "Self-built plan: CPU build, then vulkan" in text
            assert "Server: stopped" in text and f"Log: {rt.log_file}" in text and "Recommended model:" in text
            assert win.lbl_tier.text().startswith("No model chosen earlier")
            win.btn_refresh.click()
            wait_until(qapp, lambda: win._status_token is None)
            assert threads == [False, False]
        finally:
            win.close()
            win.deleteLater()

    def test_problems_rebuild_server_and_the_chosen_tier(self, view):
        from orchestrator.gui.ditto_runtime_window import status_text
        status = view.status.model_copy(update={"problems": ["the active build is old"],
                                                "rebuild": "required: the pin changed", "running": True,
                                                "healthy": True, "base_url": "http://127.0.0.1:5/v1", "pid": 42,
                                                "model": "m", "ctx": 4096})
        view.status = status
        view.preferred = next(m.tier for m in CATALOG.models if m.tier != view.model.tier)
        text = status_text(view, "en")
        assert "Problem: the active build is old" in text and "Rebuild: required: the pin changed" in text
        assert "Server: ready http://127.0.0.1:5/v1 (pid 42, model m, ctx 4096)" in text
        assert f"Chosen model: {view.preferred} (Start with 'Recommended for this computer' forgets it)" in text
        german = status_text(view, "de")
        assert "Betrieb: direkt" in german and "In Gebrauch: selbst gebaut: Build" in german
        assert "Server: bereit http://127.0.0.1:5/v1 (PID 42, Modell m, Kontext 4096)" in german

    def test_nothing_installed_and_docker_mode(self, tmp_path):
        from orchestrator.gui.ditto_runtime_window import status_text
        from orchestrator.runtime.runtime_overview import overview
        rt = runtime(tmp_path, hw=machine([]))
        view = overview(rt)
        assert "In use: nothing yet (Install… or Build llama-server… below)" in status_text(view, "en")
        view.status = view.status.model_copy(update={"mode": "docker", "docker": {
            "tag": "llm-framework/ditto:x", "image_id": "sha256:0123456789abcdef", "build_id": "f" * 64,
            "backend": "cpu", "made_by": "repackaged", "base": "debian", "verified_at": "2026-10-16"}})
        text = status_text(view, "en")
        assert "Mode: Docker" in text and "image llm-framework/ditto:x (0123456789ab)" in text


# --- Ditto's local server box (`ditto runtime ensure`) ---------------------------------------------------------------

class TestEnsure:

    def _ensure(self, monkeypatch, result=None, wait=None):
        calls = []

        def fake(rt, **kw):
            calls.append((kw, on_gui_thread()))
            if wait is not None:
                wait(kw)
            return result or runtime_mode.EnsureResult("installed", kw["mode"], ["Built for bare metal."])
        monkeypatch.setattr(runtime_mode, "ensure", fake)
        return calls

    def test_install_passes_mode_backend_placement_and_jobs_off_the_gui_thread(self, ui, monkeypatch):
        box = ui.win.box
        calls = self._ensure(monkeypatch)
        offers = []
        monkeypatch.setattr(runtime_mode, "model_offer", lambda rt: offers.append(rt))
        assert not box.installed and box.cb_mode.currentData() == "auto" and box.btn_switch.text() == "Install…"
        box.cb_mode.setCurrentIndex(box.cb_mode.findData("docker"))
        box.cb_backend.setCurrentIndex(box.cb_backend.findData("cuda"))
        box.cb_placement.setCurrentIndex(box.cb_placement.findData("container"))
        box.sp_jobs.setValue(4)
        box.chk_model.setChecked(False)   # --no-model
        box.btn_switch.click()
        assert calls == [] and "installed in Docker" in ui.asked[-1][0], "No (the default): nothing runs"
        ui.answers.append(True)
        box.btn_switch.click()
        done(ui)
        (kw, gui), = calls
        assert gui is False and kw["mode"] == "docker" and kw["force"] is False
        assert (kw["backend"], kw["placement"], kw["jobs"]) == ("cuda", "container", 4)
        assert kw["container_build"] is False and kw["cancelled"]() is False
        assert offers == [], "unticked: no model offer (--no-model)"
        assert ui.shown[-1][1] == "Built for bare metal."
        ui.answers.append(True)   # the source is fetched only after the CLI's question (asked here on the GUI thread)
        assert kw["fetch_source"]() is True and ui.asked[-1][0].endswith("is not cached yet. Fetch it now?")
        assert kw["fetch_source"]() is False, "No is the default"

    def test_the_installed_mode_is_checked_and_repaired_never_refused(self, qapp, tmp_path, monkeypatch, ui):
        from orchestrator.gui.runtime_mode_box import RuntimeModeBox
        rt = FakeRuntime(tmp_path, active="a" * 64)
        box = RuntimeModeBox(None, runtime_factory=lambda: rt)
        calls = self._ensure(monkeypatch)
        monkeypatch.setattr(runtime_mode, "model_offer", lambda r: None)
        assert box.installed and box.mode == "bare-metal" and box.cb_mode.currentData() == "bare-metal"
        assert box.btn_switch.text() == "Check and repair…"
        ui.answers.append(True)
        box._switch()
        wait_until(qapp, lambda: box._token is None)
        assert "is checked bare metal" in ui.asked[-1][0] and calls[-1][0]["force"] is False
        assert calls[-1][0]["mode"] == "bare-metal"
        box.cb_mode.setCurrentIndex(box.cb_mode.findData("auto"))
        assert box.btn_switch.text() == "Check and repair…"
        box.cb_mode.setCurrentIndex(box.cb_mode.findData("docker"))
        assert box.btn_switch.text() == "Switch…"
        ui.answers.append(True)
        box._switch()
        wait_until(qapp, lambda: box._token is None)
        assert calls[-1][0]["mode"] == "docker" and calls[-1][0]["force"] is True, "the other mode: the W2 switch"
        assert "fits next to the old mode" in ui.asked[-1][0]
        box.deleteLater()

    def test_the_ai_settings_box_opens_the_whole_window(self, qapp, tmp_path, ui):
        from orchestrator.gui.ditto_runtime_window import LocalRuntimeWindow
        from orchestrator.gui.runtime_mode_box import RuntimeModeBox
        rt = FakeRuntime(tmp_path)
        box = RuntimeModeBox(None, runtime_factory=lambda: rt)
        assert ui.win.box.btn_details is None, "the window's own box has no button to open itself"
        assert box.btn_details.objectName() == "btn_ditto_runtime_details"
        assert box.btn_details.text() == "Status and more…"
        box.btn_details.click()
        window = box._details_window
        try:
            assert isinstance(window, LocalRuntimeWindow) and window._factory() is rt
            wait_until(qapp, lambda: window._status_token is None)
            assert window.txt_status.toPlainText().startswith("llama.cpp:")
        finally:
            window.close()
            window.deleteLater()
            box.deleteLater()

    def test_the_persons_mode_choice_survives_a_status_refresh(self, ui):
        box = ui.win.box
        box.cb_mode.setCurrentIndex(box.cb_mode.findData("docker"))
        box.note_problems(["the active build is old"])
        assert box.cb_mode.currentData() == "docker"
        assert "It cannot be used now: the active build is old" in box.lbl_mode.text()

    def test_cancel_reaches_the_build(self, ui, monkeypatch):
        box = ui.win.box
        seen = []

        def wait(kw):
            end = time.monotonic() + 10
            while not kw["cancelled"]() and time.monotonic() < end:
                time.sleep(0.01)
            seen.append(kw["cancelled"]())
            raise LocalRuntimeError("the install was cancelled")
        self._ensure(monkeypatch, wait=wait)
        ui.answers.append(True)
        box.btn_switch.click()
        wait_until(ui.qapp, lambda: not box.btn_cancel.isHidden())
        box.btn_cancel.click()
        done(ui)
        assert seen == [True] and "It did not work: the install was cancelled" in ui.shown[-1][1]
        assert box.btn_cancel.isHidden() and box.btn_switch.isEnabled()

    def test_the_model_is_offered_with_size_and_licence_and_downloaded_only_after_yes(self, ui, monkeypatch, tmp_path):
        box, rt = ui.win.box, ui.rt
        self._ensure(monkeypatch)
        model = next(m for m in CATALOG.models if m.pinned)
        offer = runtime_mode.ModelOffer(model=model, why="the recommendation", state="missing", folder=tmp_path / "m")
        monkeypatch.setattr(runtime_mode, "model_offer", lambda r: offer)
        downloads = []

        def download(tier, progress=None, stop=None):
            downloads.append((tier, stop, on_gui_thread()))
            progress(model.file, GIB, 2 * GIB)
            return tmp_path / "m" / model.file
        rt.download_model = download
        ui.answers.extend([True, False])
        box.btn_switch.click()
        done(ui)
        question = ui.asked[-1][0]
        assert f"Model for Ditto: {model.repo} / {model.file}" in question and f"licence {model.license}" in question
        assert str(tmp_path / "m") in question and downloads == [], "No: nothing is downloaded"
        ui.answers.extend([True, True])
        box.btn_switch.click()
        done(ui)
        (tier, stop, gui), = downloads
        assert tier == model.tier and isinstance(stop, threading.Event) and gui is False
        assert "Verified and saved:" in ui.shown[-1][1]

    def test_a_model_that_is_there_is_named_and_an_unpinned_one_is_refused(self, ui, monkeypatch, tmp_path):
        box = ui.win.box
        self._ensure(monkeypatch)
        model = CATALOG.models[0]
        offer = runtime_mode.ModelOffer(model=model, why="x", state="ready", folder=tmp_path)
        monkeypatch.setattr(runtime_mode, "model_offer", lambda r: offer)
        ui.answers.append(True)
        box.btn_switch.click()
        done(ui)
        assert f"Model: {model.file} is there; nothing to download" in ui.shown[-1][1]
        offer.state, offer.model = "missing", model.model_copy(update={"sha256": None})
        ui.answers.append(True)
        box.btn_switch.click()
        done(ui)
        assert "no pinned size and sha256" in ui.shown[-1][1] and len(ui.asked) == 2, "asked nothing about it"


# --- build and prebuilt (`ditto runtime install`) --------------------------------------------------------------------

def _install_result():
    return SimpleNamespace(active=SimpleNamespace(backend="vulkan", build_id="a" * 64),
                           fallback=SimpleNamespace(build_id="b" * 64), problems=["a note"], older=["x"],
                           requested_missing=False)


class TestBuild:

    def _fake(self, rt, needs_source=True):
        def plan_install(backend="auto", placement="auto"):
            rt.record("plan_install", backend=backend, placement=placement)
            return SimpleNamespace(describe=lambda: ["CPU build (the fallback, built first)"], notes=["a plan note"],
                                   needs_source=needs_source, placement="native")

        def install(plan, jobs=None, log=None, fetch_source=None, cancelled=None):
            fetched = fetch_source()
            rt.record("install", jobs=jobs, fetched=fetched, cancelled=cancelled())
            if not fetched:
                raise LocalRuntimeError("The pinned llama.cpp source is needed for the build and was not fetched.")
            return _install_result()
        rt.plan_install, rt.install = plan_install, install

    def test_build_plans_asks_and_installs_with_the_options(self, ui):
        rt, win = ui.rt, ui.win
        self._fake(rt)
        win.box.cb_backend.setCurrentIndex(win.box.cb_backend.findData("vulkan"))
        win.box.cb_placement.setCurrentIndex(win.box.cb_placement.findData("native"))
        win.box.sp_jobs.setValue(3)
        win.btn_build.click()
        done(ui)
        assert rt.called("plan_install") == [{"backend": "vulkan", "placement": "native"}]
        assert rt.called("install") == [] and ui.shown[-1][1] == "Nothing was built."
        question, gui = ui.asked[-1]
        assert gui is True and "CPU build (the fallback, built first)" in question and "a plan note" in question
        assert question.endswith("Build now? The CPU build takes a few minutes, a GPU build longer.")
        ui.answers.extend([True, True])
        win.btn_build.click()
        done(ui)
        assert rt.called("install") == [{"jobs": 3, "fetched": True, "cancelled": False}]
        assert rt.threads("install") == [False] and rt.threads("plan_install") == [False, False]
        assert f"({PIN.tag}, commit {PIN.commit[:12]}, about 215 MB) is not cached yet. Fetch it now?" in \
            ui.asked[-1][0]
        text = ui.shown[-1][1]
        assert text.startswith("Self-built llama-server ready: vulkan build aaaaaaaaaaaaaaaa")
        assert "CPU fallback: build bbbbbbbbbbbbbbbb" in text and "a note" in text and "1 older build(s)" in text

    def test_nothing_to_build_asks_nothing(self, ui):
        self._fake(ui.rt, needs_source=False)
        ui.answers.append(True)   # the source question only
        ui.win.btn_build.click()
        done(ui)
        assert len(ui.asked) == 1 and "Fetch it now?" in ui.asked[0][0]

    def test_the_prebuilt_archive_is_named_asked_and_downloaded_with_force_and_cancel(self, ui, tmp_path):
        rt, win = ui.rt, ui.win
        asset = SimpleNamespace(id="linux-x64-cpu", download_size=2 * GIB)
        installed = [False]
        rt.select_backend = lambda prefer=None: rt.record("select_backend", prefer=prefer) or SimpleNamespace(
            asset=asset, reason="glibc 2.39 fits")
        rt.prebuilt_installed = lambda a: installed[0]

        def install_prebuilt(prefer=None, force=False, progress=None, stop=None):
            rt.record("install_prebuilt", prefer=prefer, force=force, stop=stop)
            return SimpleNamespace(asset_id=asset.id, path=tmp_path / "b1-cpu")
        rt.install_prebuilt = install_prebuilt
        win.box.cb_backend.setCurrentIndex(win.box.cb_backend.findData("cpu"))
        win.btn_prebuilt.click()
        done(ui)
        question = ui.asked[-1][0]
        assert f"llama.cpp {rt.build} - linux-x64-cpu (prebuilt, not self-built): glibc 2.39 fits" in question
        assert f"Download the official archive (2.0 GiB) into {rt.root}?" in question
        assert rt.called("install_prebuilt") == [] and rt.called("select_backend") == [{"prefer": "cpu"}]
        installed[0] = True
        ui.answers.append(True)
        win.btn_prebuilt.click()
        done(ui)
        assert "is already installed in" in ui.asked[-1][0]
        (kw,) = rt.called("install_prebuilt")
        assert kw["prefer"] == "cpu" and kw["force"] is False and isinstance(kw["stop"], threading.Event)
        assert rt.threads("install_prebuilt") == [False]
        assert ui.shown[-1][1] == f"Installed linux-x64-cpu in {tmp_path / 'b1-cpu'} (prebuilt, not self-built)"
        win.chk_force.setChecked(True)   # --force
        ui.answers.append(True)
        win.btn_prebuilt.click()
        done(ui)
        assert "Download the official archive" in ui.asked[-1][0] and rt.called("install_prebuilt")[-1]["force"]

    def test_no_archive_for_this_machine_says_why(self, ui):
        ui.rt.select_backend = lambda prefer=None: SimpleNamespace(asset=None, reason="musl has no official archive")
        ui.win.btn_prebuilt.click()
        done(ui)
        assert ui.asked == [] and "musl has no official archive" in ui.shown[-1][1]


# --- server (`ditto runtime start` / `stop`) -------------------------------------------------------------------------

class TestServer:

    def _fake(self, rt, error=None):
        def start(tier=None, ctx=None, sleep_idle_seconds=None):
            rt.record("start", tier=tier, ctx=ctx, sleep_idle_seconds=sleep_idle_seconds)
            if error is not None:
                raise error
            return SimpleNamespace(base_url="http://127.0.0.1:8123/v1", model="qwen", pid=4242)
        rt.start = start
        rt.status = lambda: SimpleNamespace(container=None, ctx=8192)

    def test_start_takes_tier_context_and_idle_unload_off_the_gui_thread(self, ui):
        rt, win = ui.rt, ui.win
        self._fake(rt)
        win.btn_start.click()
        done(ui)
        assert rt.called("start") == [{"tier": None, "ctx": None, "sleep_idle_seconds": None}], "the CLI defaults"
        assert rt.threads("start") == [False]
        assert "Running: http://127.0.0.1:8123/v1 model qwen (pid 4242, ctx 8192)" in ui.shown[-1][1]
        win.cb_tier.setCurrentIndex(win.cb_tier.findData("auto"))
        win.sp_ctx.setValue(4096)
        win.sp_idle.setValue(0)
        win.btn_start.click()
        done(ui)
        assert rt.called("start")[-1] == {"tier": "auto", "ctx": 4096, "sleep_idle_seconds": 0}
        tier = CATALOG.models[-1].tier
        win.cb_tier.setCurrentIndex(win.cb_tier.findData(tier))
        win.sp_idle.setValue(600)
        win.btn_start.click()
        done(ui)
        assert rt.called("start")[-1] == {"tier": tier, "ctx": 4096, "sleep_idle_seconds": 600}

    def test_a_context_below_the_minimum_is_refused_like_the_cli(self, ui):
        self._fake(ui.rt)
        ui.win.sp_ctx.setValue(100)
        ui.win.btn_start.click()
        assert ui.rt.called("start") == [] and "at least 256 tokens" in ui.shown[-1][1]

    def test_a_failed_start_shows_the_log_tail_as_plain_details(self, ui):
        self._fake(ui.rt, LocalRuntimeError("llama-server exited", log_tail="<b>error</b> line"))
        ui.win.btn_start.click()
        done(ui)
        kind, text, details = ui.shown[-1]
        assert kind == "warning" and "llama-server exited" in text and details == "<b>error</b> line"

    def test_the_remembered_tier_is_shown(self, ui):
        ui.rt.preferred_tier = lambda: "small"
        ui.win.fill_tiers()
        assert ui.win.lbl_tier.text().startswith("Chosen earlier: small.")
        assert ui.win.cb_tier.itemData(0) is None and ui.win.cb_tier.itemData(1) == "auto"
        assert [ui.win.cb_tier.itemData(i) for i in range(2, ui.win.cb_tier.count())] == [m.tier for m in
                                                                                        CATALOG.models]

    def test_stop(self, ui):
        stops = iter([2, 0])
        ui.rt.stop = lambda: ui.rt.record("stop") or next(stops)
        ui.win.btn_stop.click()
        done(ui)
        assert ui.shown[-1][1] == "Stopped (2 process(es))." and ui.rt.threads("stop") == [False]
        ui.win.btn_stop.click()
        done(ui)
        assert ui.shown[-1][1] == "Not running."

    def test_one_job_at_a_time(self, ui):
        self._fake(ui.rt)
        ui.win._job = object()
        ui.win.btn_start.click()
        ui.win.box.btn_switch.click()
        assert ui.rt.called("start") == [] and ui.shown[-1][1].startswith("Another task")
        ui.win._job = None


# --- builds (`import`, `export`, `prune`) ----------------------------------------------------------------------------

class TestBuilds:

    def test_import_asks_before_its_programs_run_no_by_default(self, ui, monkeypatch, tmp_path):
        rt = ui.rt
        archive = tmp_path / "llama-build.tar.gz"
        monkeypatch.setattr(ui.module, "_open_file", lambda parent, title, filters: str(archive))
        inst = SimpleNamespace(build_id="c" * 64, backend="cpu", role="active", origin="imported",
                               built_on="linux/x86_64", build="b1", commit="d" * 40, asset_id=None)

        def import_build(path, confirm):
            rt.record("import_build", path=path)
            info = {"build_id": "c" * 64, "tag": PIN.tag, "commit": PIN.commit, "built_at": "2026-10-16",
                    "built_on": {"os": "linux", "arch": "x86_64"}, "files": ["llama-server", "llama-cli"]}
            if not confirm(info):
                raise LocalRuntimeError("The import was not confirmed.")
            return inst
        rt.import_build = import_build
        ui.win.btn_import.click()
        done(ui)
        question, gui = ui.asked[-1]
        assert gui is True and "Run its programs here to verify it? Import only builds you made yourself." in question
        assert "built on linux/x86_64 at 2026-10-16; programs: llama-server, llama-cli" in question
        assert rt.called("import_build") == [{"path": archive}] and rt.threads("import_build") == [False]
        assert "not confirmed" in ui.shown[-1][1]
        ui.answers.append(True)
        ui.win.btn_import.click()
        done(ui)
        assert ui.shown[-1][1].startswith("Imported and verified: self-built on another machine (linux/x86_64)")

    def test_export_adds_the_tar_gz_suffix_and_a_cancelled_dialog_does_nothing(self, ui, monkeypatch, tmp_path):
        rt = ui.rt
        chosen = [""]
        monkeypatch.setattr(ui.module, "_save_file", lambda parent, title, name, filters: chosen[0])
        rt.export_build = lambda path: rt.record("export_build", path=path) or path
        ui.win.btn_export.click()
        assert rt.called("export_build") == []
        chosen[0] = str(tmp_path / "mybuild")
        ui.win.btn_export.click()
        done(ui)
        assert rt.called("export_build") == [{"path": tmp_path / "mybuild.tar.gz"}]
        assert rt.threads("export_build") == [False] and "Import a build..." in ui.shown[-1][1]
        chosen[0] = str(tmp_path / "x.tgz")
        ui.win.btn_export.click()
        done(ui)
        assert rt.called("export_build")[-1] == {"path": tmp_path / "x.tgz"}

    def test_prune_lists_every_build_in_the_deletion_dialog_and_deletes_only_what_is_accepted(self, ui, monkeypatch,
                                                                                             tmp_path, framework):
        from orchestrator.Core.deletion import DeletionResult, accept, plan_of
        from orchestrator.Core.trash import Trash
        from orchestrator.gui import deletion_dialog
        from orchestrator.tools.spec import DeletionItem
        rt = ui.rt
        folder = tmp_path / "state" / "runtime" / "builds" / "old"
        plan = plan_of([DeletionItem(kind="runtime_build", speaking_name="llama-server build 0123456789abcdef",
                                     full_path=str(folder), file_name="old", size_bytes=123456789,
                                     what="older self-built llama.cpp build of Ditto's server; not in use")],
                       ("trash", "permanent"))
        plans = [None, plan, plan]
        rt.cleanup_plan = lambda: rt.record("cleanup_plan") or plans.pop(0)
        dialogs, answers = [], [None, "trash"]

        def ask_deletion(parent, shown_plan, **kw):
            dialogs.append((shown_plan, kw, on_gui_thread()))
            mode = answers.pop(0)
            return None if mode is None else accept(shown_plan, "gui", mode)
        monkeypatch.setattr(deletion_dialog, "ask_deletion", ask_deletion)
        deleted = []

        def delete_old_builds(accepted, trash=None, audit=None):
            deleted.append((accepted, trash, audit, on_gui_thread()))
            return DeletionResult(done=[accepted.plan.items[0].speaking_name])
        rt.delete_old_builds = delete_old_builds
        ui.win.btn_prune.click()
        done(ui)
        assert dialogs == [] and ui.shown[-1][1].startswith("No old runtime builds")
        ui.win.btn_prune.click()
        done(ui)
        (shown_plan, kw, gui), = dialogs
        assert shown_plan is plan and kw["multiple"] is True and deleted == [], "No: nothing is deleted"
        assert gui is True, "the deletion dialog is on the GUI thread"
        ui.win.btn_prune.click()
        done(ui)
        (accepted, trash, audit, gui), = deleted
        assert accepted.plan.items == plan.items and accepted.mode == "trash" and accepted.actor == "gui"
        assert isinstance(trash, Trash) and audit is framework.tool_registry.audit
        assert ui.shown[-1][1] == "Moved to the trash: llama-server build 0123456789abcdef"
        assert gui is False and rt.threads("cleanup_plan") == [False, False, False], \
            "measuring the build folders and deleting them run off the GUI thread"

    def test_the_deletion_dialog_shows_the_full_list_with_nothing_ticked_and_no_as_default(self, qapp, tmp_path):
        from orchestrator.Core.deletion import plan_of
        from orchestrator.gui.deletion_dialog import DeletionConfirmDialog
        from orchestrator.tools.spec import DeletionItem
        plan = plan_of([DeletionItem(kind="runtime_build", speaking_name="llama-server build 0123456789abcdef",
                                     full_path=str(tmp_path / "old"), file_name="old", size_bytes=2_000_000,
                                     what="older self-built llama.cpp build of Ditto's server; not in use")],
                       ("trash", "permanent"))
        dialog = DeletionConfirmDialog(plan, multiple=True)
        try:
            text = dialog.plain_text()
            assert str(tmp_path / "old") in text and "File name: old" in text and "Size: 2.0 MB" in text
            assert "not in use" in text and dialog.ticked() == [] and dialog.btn_no.isDefault()
        finally:
            dialog.deleteLater()


# --- the source (`export-source`, `import-source`) -------------------------------------------------------------------

class TestSource:

    def test_export_asks_before_fetching_and_takes_the_mirror(self, ui, monkeypatch, tmp_path):
        out = tmp_path / "llama.bundle"
        monkeypatch.setattr(ui.module, "_save_file", lambda parent, title, name, filters: str(out))
        monkeypatch.setattr(source_module, "cached_source", lambda pin, state, **kw: None)
        exports = []

        def export_source(pin, state_dir, path, url=None, log=None, git=None):
            exports.append((pin, state_dir, path, url, on_gui_thread()))
            return {"bundle_size": GIB}
        monkeypatch.setattr(source_module, "export_source", export_source)
        ui.win.btn_export_source.click()
        done(ui)
        assert exports == [] and "is not cached yet. Fetch it now?" in ui.asked[-1][0]
        ui.win.txt_mirror.setText("  https://mirror.example/llama.cpp.git ")
        ui.answers.append(True)
        ui.win.btn_export_source.click()
        done(ui)
        assert exports == [(PIN, ui.rt.data_dir, out, "https://mirror.example/llama.cpp.git", False)]
        text = ui.shown[-1][1]
        assert f"Wrote {out} (1.0 GiB, llama.cpp {PIN.tag}, commit {PIN.commit[:12]}) and llama.bundle.json" in text
        ui.win.txt_mirror.setText("")
        monkeypatch.setattr(source_module, "cached_source", lambda pin, state, **kw: tmp_path)
        ui.win.btn_export_source.click()
        done(ui)
        assert exports[-1][3] is None and len(ui.asked) == 2, "cached: no question; no mirror: the pin's repository"

    def test_import_source(self, ui, monkeypatch, tmp_path):
        bundle = tmp_path / "llama.bundle"
        monkeypatch.setattr(ui.module, "_open_file", lambda parent, title, filters: str(bundle))
        imports = []
        monkeypatch.setattr(source_module, "import_source", lambda pin, state, path, log=None, git=None: imports.append(
            (pin, state, path, on_gui_thread())) or tmp_path / "src")
        ui.win.btn_import_source.click()
        done(ui)
        assert imports == [(PIN, ui.rt.data_dir, bundle, False)]
        assert ui.shown[-1][1] == f"Verified llama.cpp {PIN.tag} (commit {PIN.commit[:12]}) in {tmp_path / 'src'}"


# --- questions, texts and languages ----------------------------------------------------------------------------------

class TestTextsAndQuestions:

    def test_questions_default_to_no(self, ui, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui import plain
        boxes = []
        monkeypatch.setattr(plain, "_exec", lambda box: boxes.append(box) or QMessageBox.No)
        monkeypatch.setattr(ui.module, "ask", plain.ask)
        assert ui.win._ask_from_worker("Fetch it now?") is False
        box = boxes[-1]
        assert box.defaultButton() is box.button(QMessageBox.No) and box.escapeButton() is box.button(QMessageBox.No)

    def test_every_text_exists_in_english_and_german_with_the_same_placeholders(self):
        from orchestrator.utils.l10n_ditto_runtime import TEXTS
        from orchestrator.utils.localization import LocalizationManager
        assert set(TEXTS["en"]) == set(TEXTS["de"])
        for key, text in TEXTS["en"].items():
            assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", TEXTS["de"][key])), key
        table = LocalizationManager.TRANSLATIONS
        used = set()
        for name in ("ditto_runtime_window.py", "runtime_mode_box.py", "menus/ditto_runtime.py"):
            source = (GUI / name).read_text(encoding="utf-8")
            used |= set(re.findall(r"[\"']((?:ditto_runtime|runtime\.mode|deletion)\.[a-z_.\-]+[a-z])[\"']", source))
        for prefix, values in (("ditto_runtime.backend.", ("auto", "cpu", "vulkan", "cuda", "rocm", "metal")),
                               ("ditto_runtime.placement.", ("auto", "native", "container")),
                               ("ditto_runtime.status.mode.", ("bare-metal", "docker")),
                               ("ditto_runtime.row.", ("llama", "hardware", "mode", "in_use", "bare_metal", "built",
                                                      "verified", "note", "problem", "rebuild", "plan", "prebuilt",
                                                      "server", "recommended", "chosen", "log"))):
            used |= {prefix + value for value in values}
        assert len(used) > 120
        for key in used:
            assert key in table["en"] and key in table["de"], key

    def test_no_rich_text_and_no_cli_command_in_the_window_texts(self):
        from orchestrator.utils.l10n_ditto_runtime import TEXTS
        for name in ("ditto_runtime_window.py", "runtime_mode_box.py", "menus/ditto_runtime.py"):
            source = (GUI / name).read_text(encoding="utf-8")
            for api in ("setHtml", "RichText", "QMessageBox.information(", "QMessageBox.warning(",
                        "QMessageBox.question("):
                assert api not in source, (name, api)
            assert all("plain_tooltip(" in line for line in source.splitlines() if "setToolTip(" in line), name
        for lang in ("en", "de"):
            for key, text in TEXTS[lang].items():
                assert "llm-cli" not in text and "llmcf" not in text, (lang, key)
        from orchestrator.utils.l10n_runtime import TEXTS as RUNTIME
        assert "llm-cli ditto models download" not in RUNTIME["en"]["runtime.mode.install_question"]

    def test_the_window_speaks_german(self, qapp, tmp_path, monkeypatch, view, framework):
        from orchestrator.gui import ditto_runtime_window as win_module
        from orchestrator.utils import localization
        monkeypatch.setattr(localization, "current_language", lambda: "de")
        monkeypatch.setattr(win_module, "current_language", lambda: "de")
        monkeypatch.setattr(win_module, "overview", lambda rt: view)
        win = win_module.LocalRuntimeWindow(framework, runtime_factory=lambda: FakeRuntime(tmp_path))
        try:
            wait_until(qapp, lambda: win._status_token is None)
            assert win.windowTitle() == "Dittos lokale Laufzeit" and win.btn_start.text() == "Starten"
            assert win.box.btn_switch.text() == "Installieren…" and win.btn_prune.text() == "Ältere Builds entfernen…"
            assert "Betrieb: direkt" in win.txt_status.toPlainText()
            from orchestrator.gui.runtime_mode_box import gib
            assert gib(int(1.5 * GIB), "de") == "1,5 GiB" and gib(None) == "?"
        finally:
            win.close()
            win.deleteLater()

    def test_archive_path(self, tmp_path):
        from orchestrator.gui.ditto_runtime_window import archive_path
        assert archive_path(str(tmp_path / "a")) == tmp_path / "a.tar.gz"
        assert archive_path(str(tmp_path / "a.tar.gz")) == tmp_path / "a.tar.gz"
        assert archive_path(str(tmp_path / "a.tgz")) == tmp_path / "a.tgz"
        assert Path(archive_path("b.zip")).name == "b.zip.tar.gz"


# --- one job at a time in the whole GUI, the source question, the automatic check, the hints ------------------------

class TestOneCodePathDetails:

    def test_a_job_of_another_box_blocks_every_runtime_job(self, qapp, tmp_path, monkeypatch, ui):
        """The AI settings' box and the window (or the window opened by "Status and more...") are separate widgets:
        while one of them builds, none of the others starts a second build, download or server start."""
        from orchestrator.gui.runtime_mode_box import RuntimeModeBox
        release, started = threading.Event(), threading.Event()

        def ensure(rt, **kw):
            started.set()
            release.wait(10)
            return runtime_mode.EnsureResult("installed", kw["mode"], ["Built for bare metal."])
        monkeypatch.setattr(runtime_mode, "ensure", ensure)
        monkeypatch.setattr(runtime_mode, "model_offer", lambda r: None)
        TestServer()._fake(ui.rt)
        settings_box = RuntimeModeBox(None, runtime_factory=lambda: FakeRuntime(tmp_path))
        try:
            ui.answers.append(True)
            settings_box.btn_switch.click()
            assert started.wait(10)
            ui.win.btn_start.click()
            assert ui.rt.called("start") == [] and ui.shown[-1][1].startswith("Another task")
            asked = len(ui.asked)
            ui.win.box.btn_switch.click()
            assert len(ui.asked) == asked and ui.shown[-1][1].startswith("Another task"), "nothing asked, nothing run"
            release.set()
            wait_until(qapp, lambda: settings_box._token is None)
            ui.win.btn_start.click()
            done(ui)
            assert len(ui.rt.called("start")) == 1, "free again once the other job ended"
        finally:
            release.set()
            settings_box.deleteLater()

    def test_the_box_asks_the_clis_question_before_the_source_is_fetched(self, ui, monkeypatch):
        """`ditto runtime ensure` asks before it fetches the pinned source (about 215 MB); so does the box, on the GUI
        thread with "No" as the default, while the build waits."""
        answers_seen = []

        def ensure(rt, **kw):
            answers_seen.append((kw["fetch_source"](), on_gui_thread()))
            return runtime_mode.EnsureResult("installed", kw["mode"], ["Built for bare metal."])
        monkeypatch.setattr(runtime_mode, "ensure", ensure)
        monkeypatch.setattr(runtime_mode, "model_offer", lambda r: None)
        ui.answers.append(True)   # the install question; the source question gets the default No
        ui.win.box.btn_switch.click()
        done(ui)
        question, gui = ui.asked[-1]
        assert answers_seen == [(False, False)] and gui is True
        assert question == (f"The pinned llama.cpp source ({PIN.tag}, commit {PIN.commit[:12]}, about 215 MB) is not "
                            "cached yet. Fetch it now?")
        ui.answers.extend([True, True])
        ui.win.box.btn_switch.click()
        done(ui)
        assert answers_seen[-1] == (True, False)

    def test_automatic_on_an_installed_runtime_says_what_ensure_does(self, qapp, tmp_path, monkeypatch, ui):
        """'Automatic' with a runtime installed is `ensure` (auto): what is installed stays when it works, otherwise
        bare metal is preferred, which may be the other mode. The question says so instead of promising the installed
        mode."""
        from orchestrator.gui.runtime_mode_box import RuntimeModeBox
        rt = FakeRuntime(tmp_path, active="a" * 64)
        box = RuntimeModeBox(None, runtime_factory=lambda: rt)
        calls = []
        monkeypatch.setattr(runtime_mode, "ensure", lambda r, **kw: calls.append(kw) or runtime_mode.EnsureResult(
            "skipped", "bare-metal", ["Nothing to do."]))
        monkeypatch.setattr(runtime_mode, "model_offer", lambda r: None)
        try:
            box.cb_mode.setCurrentIndex(box.cb_mode.findData("auto"))
            assert box.btn_switch.text() == "Check and repair…"
            box._switch()
            question = ui.asked[-1][0]
            assert calls == [] and "installed again bare metal" not in question
            assert "bare metal when it can be built here, otherwise in Docker" in question and "now (bare metal)" in \
                question
            ui.answers.append(True)
            box._switch()
            wait_until(qapp, lambda: box._token is None)
            assert calls[-1]["mode"] == "auto" and calls[-1]["force"] is False
        finally:
            box.deleteLater()

    def test_the_hints_name_the_mouse_path_too(self, tmp_path):
        """A stopped local server and a declined source fetch name the GUI path next to the command."""
        from orchestrator.llm import LLMClient, NotConfigured
        from test_runtime import FakeConfig
        client = LLMClient(FakeConfig(ai_provider="local"), None, state_dir=tmp_path / "state",
                           runtime_resolver=lambda pid: None)
        with pytest.raises(NotConfigured) as info:
            client.resolve()
        assert "llm-cli ditto runtime start" in str(info.value)
        assert "Ditto > Local Runtime... > Start" in str(info.value)
        rt = runtime(tmp_path / "rt")
        with pytest.raises(LocalRuntimeError) as info:
            rt.pinned_source(True, fetch_source=lambda: False)
        assert "Ditto > Local Runtime... > Import the source..." in str(info.value)
        assert "llm-cli ditto runtime import-source FILE" in str(info.value)
