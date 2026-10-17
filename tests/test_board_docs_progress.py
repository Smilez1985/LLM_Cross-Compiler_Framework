"""The Builder's board recommendation, the documentation links for Deep Ingest and the build progress (DECISIONS round
8: N9, N9', N9'', N10, N8).

- The board comes from the hardware probe and is only a recommendation with its source and how sure it is; a weak
  match preselects nothing (the candidates are shown and the person picks); a board the person chose is never
  overwritten by a later detection; the online look-up needs the person's consent. No target fits: the wizard and
  Ditto are offered.
- The source catalogue's docs_* links for the probe's vendor and SDK fill the Deep Ingest window and are readable as a
  read-only resource; when Ditto lacks information the wizard offers Deep Ingest; the person starts every crawl.
- The progress bar shows the stage and percent from the build service, the live log has a search and a level filter.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.runtime.board_recommendation import SURE, UNSURE, recommend_board

REPO_ROOT = Path(__file__).resolve().parent.parent


class Profile:
    def __init__(self, **values):
        self.values = values

    def get(self, key):
        return self.values.get(key)


@pytest.fixture
def tm(booted_framework):
    return booted_framework.target_manager


# --- N9 / N9' / N9'': the recommendation ----------------------------------------------------------------------------

def test_the_probes_soc_is_a_sure_recommendation(tm):
    rec = recommend_board(Profile(SOC_MODEL="rk3588", BOARD_MODEL="Radxa ROCK 5B"), tm, "Rockchip")
    assert rec.sure and rec.board == "rk3588" and rec.source == "probe" and rec.confidence == SURE


def test_a_board_name_in_the_known_list_is_sure(tm):
    rec = recommend_board(Profile(BOARD_MODEL="Radxa ZERO 3W"), tm, "Rockchip")
    assert rec.sure and rec.board == "rk3566" and rec.source == "list"


@pytest.mark.parametrize("name", ["Radxa ROCK 5B Plus", "ROCK"])
def test_a_weak_match_is_unsure_and_names_candidates(tm, name):
    rec = recommend_board(Profile(BOARD_MODEL=name), tm, "Rockchip")
    assert not rec.sure and rec.board is None and rec.confidence == UNSURE and rec.candidates
    assert all(c.why for c in rec.candidates)


def test_no_soc_and_no_known_name_lists_every_board(tm):
    rec = recommend_board(Profile(ARCH="arm64"), tm, "Rockchip")
    assert rec.confidence == UNSURE and {c.board for c in rec.candidates} == {"rk3566", "rk3568", "rk3576", "rk3588"}


@pytest.fixture
def builder(booted_framework, qapp):
    from orchestrator.gui.builder_tab import BuilderTab
    tab = BuilderTab(booted_framework)
    tab.target_combo.setCurrentIndex(tab.target_combo.findData("Rockchip"))
    yield tab
    tab.deleteLater()


def test_a_sure_recommendation_is_applied_and_says_where_it_came_from(builder):
    from orchestrator.gui.builder_parts import after_probe
    builder.board_combo.setCurrentIndex(builder.board_combo.findData("rk3566"))
    assert after_probe(builder, Profile(SOC_MODEL="rk3588"), "Rockchip") == "applied"
    assert builder.board_combo.currentData() == "rk3588"
    text = builder.board_advice.label.text()
    assert "rk3588" in text.lower() and "from the probe's SoC" in text and "sure" in text
    assert "only a recommendation" in text, "the field stays editable: the person may change it"
    assert builder.board_combo.isEnabled()


def test_a_persons_board_is_never_overwritten_by_a_later_detection(builder):
    from orchestrator.gui.builder_parts import after_probe
    builder.board_combo.setCurrentIndex(builder.board_combo.findData("rk3566"))
    builder.gui_form.person_edited("board")
    assert after_probe(builder, Profile(SOC_MODEL="rk3588"), "Rockchip") == "kept"
    assert builder.board_combo.currentData() == "rk3566"
    assert "Your choice" in builder.board_advice.label.text()
    builder.board_advice.buttons[0].click()   # the person may still take the recommendation with one click
    assert builder.board_combo.currentData() == "rk3588"


def test_a_recipes_board_counts_as_the_persons_choice(builder):
    from orchestrator.gui.builder_parts import after_probe
    recipe = SimpleNamespace(id="r1", target=SimpleNamespace(id="Rockchip"), format="gguf", board="rk3568",
                             quantization="Q4_0", model=SimpleNamespace(repo="org/m", revision="c" * 40),
                             options=SimpleNamespace(use_imatrix=False, dataset=None))
    builder.apply_recipe(recipe)
    assert builder.board_combo.currentData() == "rk3568"
    assert after_probe(builder, Profile(SOC_MODEL="rk3588"), "Rockchip") == "kept"
    assert builder.board_combo.currentData() == "rk3568"


def test_a_weak_match_preselects_nothing_and_asks(builder):
    from orchestrator.gui.builder_parts import after_probe
    before = builder.board_combo.currentData()
    assert after_probe(builder, Profile(BOARD_MODEL="Radxa ROCK 5B Plus"), "Rockchip") == "ask"
    assert builder.board_combo.currentData() == before, "no silent preselection on a weak match"
    texts = [b.text() for b in builder.board_advice.buttons]
    assert any("RK3588" in t for t in texts) and "Please pick your board" in builder.board_advice.label.text()
    pick = next(b for b in builder.board_advice.buttons if "RK3588" in b.text())
    pick.click()
    assert builder.board_combo.currentData() == "rk3588" and builder.gui_form.field("board").person_set


def test_the_online_look_up_needs_consent(builder, monkeypatch):
    from orchestrator.gui import builder_parts
    from orchestrator.gui.builder_parts import after_probe
    opened, answers = [], [False]
    monkeypatch.setattr(builder_parts, "ask", lambda parent, title, text: answers[0])
    monkeypatch.setattr(builder_parts.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    after_probe(builder, Profile(BOARD_MODEL="Mystery Board 7"), "Rockchip")
    assert builder.board_advice.look_up_online() is False and opened == [], "nothing leaves without consent"
    answers[0] = True
    assert builder.board_advice.look_up_online() is True
    assert opened and "Mystery" in opened[0] and opened[0].startswith(builder_parts.SEARCH_URL)


def test_no_target_offers_the_wizard_and_ditto(builder):
    from orchestrator.gui.builder_parts import after_probe
    seen = []
    builder.create_target_requested.connect(lambda path: seen.append(("wizard", path)))
    builder.ask_ditto_requested.connect(lambda text: seen.append(("ditto", text)))
    builder._last_probe_path = Path("/probes/x.txt")
    assert after_probe(builder, Profile(SOC_MODEL="xyz1", ARCH="riscv64"), None) == "no_target"
    for button in builder.board_advice.buttons:
        button.click()
    assert seen[0] == ("wizard", str(Path("/probes/x.txt")))
    assert seen[1][0] == "ditto" and "xyz1" in seen[1][1] and "I click Finish myself" in seen[1][1]


# --- N10: documentation links ----------------------------------------------------------------------------------------

def test_links_follow_the_probes_vendor_and_the_sdk():
    from orchestrator.Core.doc_sources import links_for, vendors_for_sdk
    links = links_for(Profile(NPU_VENDOR="rockchip", RKLLM_RUNTIME_VERSION="1.2.1"))
    assert links and links[0].project == "rkllm_toolkit" and links[0].key == "docs_workflow"
    assert all(link.url.startswith("https://") for link in links)
    assert vendors_for_sdk("RKLLM / RKNN") == ["rockchip"] and "amd" not in vendors_for_sdk("rockchip")
    assert {link.vendor for link in links_for(sdk="OpenVINO")} == {"intel"}
    assert links_for(Profile(SUPPORTS_CUDA="ON"))[0].vendor == "nvidia"
    assert links_for(Profile(SOC_VENDOR="unknown-vendor")) == []


def test_the_links_are_a_read_only_resource(booted_framework):
    from orchestrator.runtime.probe_service import HardwareProbes
    from orchestrator.tools.registry import ResourceUnknown, ToolContext
    registry = booted_framework.tool_registry
    text, _external = registry.read_resource("lcf://knowledge/doc-sources", {}, ToolContext("mcp"))
    assert "rknn-llm" in text and "crawl_docs_site" in text
    sample = REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt"
    pid = HardwareProbes.for_framework(booted_framework).import_file(sample)
    text, _ = registry.read_resource("lcf://knowledge/doc-sources/{probe_id}", {"probe_id": pid}, ToolContext("mcp"))
    assert "rockchip" in text and "rknn-llm" in text and "nvidia" not in text
    with pytest.raises(ResourceUnknown):
        registry.read_resource("lcf://knowledge/doc-sources/{probe_id}", {"probe_id": "0" * 16}, ToolContext("mcp"))


def test_the_deep_ingest_window_is_filled_and_nothing_is_crawled(qapp):
    from orchestrator.gui.dialogs import URLInputDialog
    dialog = URLInputDialog(None, suggested=["https://example.org/a", "https://example.org/a",
                                             "https://example.org/b"])
    assert dialog.get_urls() == ["https://example.org/a", "https://example.org/b"]
    assert dialog.result() == 0, "the person presses OK; until then nothing is crawled"
    dialog.deleteLater()


@pytest.fixture
def wizard(booted_framework, qapp, tmp_path, monkeypatch):
    from orchestrator.gui import wizards
    w = wizards.ModuleCreationWizard(tmp_path / "targets")
    w.framework_manager = booted_framework
    monkeypatch.setattr(wizards.QMessageBox, "exec", lambda self: 0)
    yield w
    w.deleteLater()


def test_missing_information_offers_deep_ingest_with_the_links(wizard, monkeypatch):
    from orchestrator.gui import plain
    asked, ingested = [], []
    monkeypatch.setattr(plain, "ask", lambda parent, title, text: asked.append(text) or True)
    monkeypatch.setattr(type(wizard), "run_deep_ingest", lambda self: ingested.append(1))
    wizard.sdk_edit.setText("RKNN")
    wizard.on_ai_error("Ditto cannot do this (information is missing)", reason="missing_information")
    assert ingested == [1] and asked and "documentation links" in asked[0]
    wizard.on_ai_error("some other problem", reason="not_supported")
    assert ingested == [1] and len(asked) == 1, "offered only when information is missing"
    assert wizard.documentation_links()[0].startswith("https://github.com/airockchip/")


def test_the_cannot_reason_travels_to_the_gui_thread(wizard, qapp):
    import threading
    import time
    from orchestrator.Core.ditto_manager import CannotAnswer, DittoCannot
    from orchestrator.gui import wizards
    from orchestrator.gui.worker_relay import worker_relay
    seen = []
    wizard.on_ai_error = lambda err, reason="": seen.append(reason)

    class Ditto:
        def generate_module(self, path):
            raise DittoCannot(CannotAnswer(reason="missing_information", missing="docs", next_step="add docs"), True)
    token = wizard._ai_request = object()
    thread = threading.Thread(target=wizards.ModuleCreationWizard._ai_worker,
                              args=(Ditto(), Path("p.txt"), token, worker_relay()))
    thread.start()
    thread.join(5)
    deadline = time.monotonic() + 5
    while not seen and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert seen == ["missing_information"]


# --- N8: progress bar and live log ---------------------------------------------------------------------------------

@pytest.mark.parametrize("line, level", [
    ("[2026-10-15 10:00:00] [ERROR] ERROR: build failed", "error"),
    ("[2026-10-15 10:00:00] [WARNING] WARNING: disk tight", "warning"),
    ("[2026-10-15 10:00:00] [INFO] CONT: main.c:3: error: x undeclared", "error"),
    ("[2026-10-15 10:00:00] [INFO] CONT: cc -Werror -O2 main.c", "info"),
    ("[2026-10-15 10:00:00] [INFO] CONT: warning: unused variable", "warning"),
    ("[2026-10-15 10:00:00] [INFO] Starting Main Build Container...", "info"),
])
def test_log_levels(line, level):
    from orchestrator.gui.builder_parts import line_level
    assert line_level(line) == level


class FakeService:
    def __init__(self):
        self.lines = []
        self.status = SimpleNamespace(status="building", stage="Building Image", progress_percent=40)

    def get_status(self, rid):
        return self.status

    def log_tail(self, rid, lines=200, before_line=None, for_person=False):
        self.for_person = for_person
        chosen = self.lines[-lines:]
        return SimpleNamespace(lines=chosen, first_line=len(self.lines) - len(chosen) + 1,
                               total_lines=len(self.lines))


def test_progress_bar_stage_and_live_log(qapp):
    from orchestrator.gui.builder_parts import BuildProgressPanel
    panel = BuildProgressPanel()
    service = FakeService()
    panel.start("req_1")
    service.lines = ["[t] [INFO] one", "[t] [ERROR] ERROR: two <b>bold</b>"]
    assert panel.poll(service, "req_1") == "building"
    assert service.for_person is True, "the person's window reads the log with its full paths, not an AI's labels"
    assert panel.bar.value() == 40 and "Building Image" in panel.lbl_stage.text() and "40%" in panel.lbl_stage.text()
    service.lines.append("[t] [WARNING] WARNING: three")
    panel.poll(service, "req_1")
    assert panel.plain_text().splitlines() == service.lines, "each line once, in order, as plain text"
    assert "<b>bold</b>" in panel.plain_text()
    panel.level.setCurrentIndex(panel.level.findData("error"))
    assert panel.visible_lines() == ["[t] [ERROR] ERROR: two <b>bold</b>"]
    panel.level.setCurrentIndex(panel.level.findData("all"))
    panel.search.setText("THREE")
    assert panel.visible_lines() == ["[t] [WARNING] WARNING: three"] and "1 of 3" in panel.lbl_count.text()
    service.status = SimpleNamespace(status="completed", stage="Archiving", progress_percent=100)
    assert panel.poll(service, "req_1") == "completed" and "completed" in panel.lbl_stage.text()
    panel.deleteLater()


def test_lines_the_service_no_longer_returns_are_noted(qapp):
    from orchestrator.gui.builder_parts import BuildProgressPanel
    panel = BuildProgressPanel()
    panel.start("req_2")
    panel.add_lines(["a", "b"], 1, 2)
    panel.add_lines(["e", "f"], 5, 6)
    lines = panel.plain_text().splitlines()
    assert lines[:2] == ["a", "b"] and "2 earlier lines" in lines[2] and lines[3:] == ["e", "f"]
    panel.deleteLater()


def test_the_builder_follows_its_build_with_the_panel(builder):
    service = FakeService()
    builder.framework.build_service = service
    builder._track_build("req_3")
    service.lines = ["[t] [INFO] working"]
    builder._poll_build()
    assert builder.progress_panel.bar.value() == 40 and "working" in builder.progress_panel.plain_text()
    service.get_status = lambda rid: (_ for _ in ()).throw(KeyError(rid))
    builder._poll_build()
    assert builder._active_request is None and not builder.btn_cancel.isEnabled()


# --- review of the package: what the first tests let through ---------------------------------------------------------

def _service_with(state, builds):
    """The real BuildService over a request state and the engine's builds (build id -> BuildProgress)."""
    from orchestrator.Core.build_service import BuildService
    engine = SimpleNamespace(get_build_status=lambda bid: builds.get(bid))
    return BuildService(SimpleNamespace(orchestrator=SimpleNamespace(_workflows={state.request_id: state},
                                                                     build_engine=engine)))


def test_the_build_services_step_and_percent_come_from_the_running_build():
    """A request with one build said 0 % and no step of its own until the build ended, so the bar sat still for the
    whole build (the first test used a fake service). The running build's step and percent (builder.py
    BuildProgress) count now; an ended build adds nothing, and a running request never says 100 %."""
    from datetime import datetime
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
    state = WorkflowState("req_1", OrchestrationStatus.BUILDING, datetime.now(),
                          current_stage="Building org/m for aarch64", total_builds=1, build_ids=["req_1_001"])
    first = BuildProgress("req_1_001", BuildStatus.BUILDING, "Building Image", 40)
    status = _service_with(state, {"req_1_001": first}).get_status("req_1")
    assert status.progress_percent == 40 and status.stage == "Building org/m for aarch64: Building Image"
    first.progress_percent = 100
    assert _service_with(state, {"req_1_001": first}).get_status("req_1").progress_percent == 99
    first.status = BuildStatus.FAILED   # ended (a fix may be waiting for approval): the request's own values
    status = _service_with(state, {"req_1_001": first}).get_status("req_1")
    assert status.progress_percent == 0 and status.stage == "Building org/m for aarch64"
    state.total_builds, state.completed_builds = 2, 1
    state.build_ids.append("req_1_002")
    second = BuildProgress("req_1_002", BuildStatus.BUILDING, "Running modules", 60)
    assert _service_with(state, {"req_1_001": first, "req_1_002": second}).get_status("req_1").progress_percent == 80
    state.status = OrchestrationStatus.AWAITING_APPROVAL
    assert _service_with(state, {"req_1_002": second}).get_status("req_1").progress_percent == 50


def test_a_probe_never_replaces_the_target_the_person_chose(builder):
    """The probe's target used to replace the person's target, which reset their board (a recipe's too) and then
    filled in the probe's board. The person's target stays; one click takes the probe's target and its board."""
    builder.target_combo.addItem("Other boards (aarch64)", "Other")
    builder.target_combo.setCurrentIndex(builder.target_combo.findData("Other"))
    builder.gui_form.person_edited("target")
    builder._load_probe_config(REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt")
    assert builder.target_combo.currentData() == "Other"
    text = builder.board_advice.label.text()
    assert "Rockchip" in text and "Your choice" in text
    builder.board_advice.buttons[0].click()
    assert builder.target_combo.currentData() == "Rockchip" and builder.gui_form.field("target").person_set
    assert builder.board_combo.currentData() == "rk3566", "then the probe's board, as a recommendation"


def test_a_target_the_person_did_not_choose_still_follows_the_probe(builder):
    builder.target_combo.addItem("Other boards (aarch64)", "Other")
    builder.target_combo.setCurrentIndex(builder.target_combo.findData("Other"))
    builder._load_probe_config(REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt")
    assert builder.target_combo.currentData() == "Rockchip" and builder.board_combo.currentData() == "rk3566"


def test_dittos_recommended_settings_keep_the_persons_choice_and_are_marked(builder):
    """'Ask Ditto for Config' filled format, quantization and optimization over the person's values and left no
    mark. It fills through the build form now: the person's values stay, Ditto's are marked and undoable."""
    from orchestrator.gui.form_surface import is_marked
    builder.format_combo.setCurrentIndex(builder.format_combo.findData("gguf"))
    builder.quant_combo.setCurrentText("Q5_K_M")
    builder.gui_form.person_edited("quantization")
    builder._on_ai_finished(True, {"format": "gguf", "quantization": "Q4_0", "optimization": "SPEED"}, "ok")
    assert builder.quant_combo.currentText() == "Q5_K_M", "the person's quantization stays"
    assert builder.opt_combo.currentText() == "SPEED" and is_marked(builder.opt_combo)
    assert "Q4_0" in builder.log_area.toPlainText() and "Your choice stays" in builder.log_area.toPlainText()
    builder.fill_banner.btn_undo.click()
    assert builder.opt_combo.currentText() == "BALANCED"


def test_the_log_view_keeps_at_most_its_limit(qapp, monkeypatch):
    """Lines past the limit were dropped from the list but stayed in the view, so the 'removed' note was wrong and
    the view grew without end."""
    from orchestrator.gui import builder_parts
    monkeypatch.setattr(builder_parts, "MAX_LOG_LINES", 5)
    panel = builder_parts.BuildProgressPanel()
    panel.start("req_4")
    panel.add_lines([f"line {i}" for i in range(1, 9)], 1, 8)
    assert panel.plain_text().splitlines() == [f"line {i}" for i in range(4, 9)]
    assert "3 older lines" in panel.lbl_count.text()
    panel.deleteLater()


def test_a_second_attempt_repeats_no_line(qapp):
    """Once a request has a second attempt, the build service puts a heading before each attempt, so the earlier
    lines move down by one: the panel showed the last line again."""
    from orchestrator.gui.builder_parts import BuildProgressPanel
    panel = BuildProgressPanel()
    panel.start("req_5")
    panel.add_lines(["a", "b"], 1, 2)
    panel.add_lines(["===== req_5_001 =====", "a", "b", "===== req_5_001_r1 =====", "c"], 1, 5)
    assert panel.plain_text().splitlines() == ["a", "b", "===== req_5_001_r1 =====", "c"]
    panel.add_lines(["===== req_5_001 =====", "a", "b", "===== req_5_001_r1 =====", "c", "d"], 1, 6)
    assert panel.plain_text().splitlines()[-2:] == ["c", "d"]
    fresh = BuildProgressPanel()   # a panel that starts after the second attempt began shifts nothing
    fresh.start("req_5")
    fresh.add_lines(["===== req_5_001 =====", "a", "===== req_5_001_r1 =====", "c"], 1, 4)
    assert fresh.plain_text().splitlines() == ["===== req_5_001 =====", "a", "===== req_5_001_r1 =====", "c"]
    panel.deleteLater()
    fresh.deleteLater()


def test_a_build_the_service_no_longer_knows_shows_no_old_step(qapp):
    from orchestrator.gui.builder_parts import BuildProgressPanel
    panel = BuildProgressPanel()
    service = FakeService()
    panel.start("req_6")
    panel.poll(service, "req_6")
    service.get_status = lambda rid: (_ for _ in ()).throw(KeyError(rid))
    assert panel.poll(service, "req_6") is None
    assert "40%" not in panel.lbl_stage.text() and "cannot be read" in panel.lbl_stage.text()
    panel.deleteLater()


def test_a_board_the_person_chose_keeps_its_target(builder, monkeypatch):
    """A board belongs to its target: the person's board stayed only while they had picked the target too, so a probe
    of another target replaced the target and with it the board they chose."""
    tm = builder.framework.get_component("target_manager")
    builder.target_combo.addItem("Other boards (aarch64)", "Other")
    builder.board_combo.setCurrentIndex(builder.board_combo.findData("rk3588"))
    builder.gui_form.person_edited("board")
    monkeypatch.setattr(tm, "find_matching_target", lambda data: "Other")
    builder._load_probe_config(REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt")
    assert builder.target_combo.currentData() == "Rockchip" and builder.board_combo.currentData() == "rk3588"
    assert "Your choice" in builder.board_advice.label.text()


def _submit(builder, monkeypatch, answer):
    """Start Build with the build itself stubbed out: (the questions asked, the requests submitted)."""
    from orchestrator.gui import builder_parts, builder_tab
    asked, submitted = [], []
    monkeypatch.setattr(builder_parts, "ask", lambda parent, title, text: asked.append(text) or answer)
    monkeypatch.setattr(builder.framework.orchestrator, "submit_build_request",
                        lambda req: submitted.append(req) or "req_x")
    monkeypatch.setattr(builder.framework, "run_coroutine", lambda value, timeout=30: value)
    monkeypatch.setattr(builder, "_track_build", lambda rid: None)
    monkeypatch.setattr(builder, "_remember_build", lambda *a: None)
    monkeypatch.setattr(builder_tab.QMessageBox, "information", lambda *a, **k: 0)
    monkeypatch.setattr(builder_tab.QMessageBox, "warning", lambda *a, **k: pytest.fail(str(a[2:])))
    builder.model_input.setEditText("org/m")
    builder._submit_build()
    return asked, submitted


def test_start_build_asks_when_the_probe_was_not_sure_about_the_board(builder, monkeypatch):
    """A weak match filled nothing in, but the form still held a board nobody chose (the first of the list), and Start
    Build used it without a word. Until the person picks a board, Start Build asks first ("No" is the default)."""
    from orchestrator.gui.builder_parts import after_probe
    assert after_probe(builder, Profile(BOARD_MODEL="Radxa ROCK 5B Plus"), "Rockchip") == "ask"
    asked, submitted = _submit(builder, monkeypatch, False)
    assert asked and "could not tell your board for sure" in asked[0] and submitted == []
    assert "was not started" in builder.log_area.toPlainText()
    asked, submitted = _submit(builder, monkeypatch, True)
    assert len(submitted) == 1 and builder.gui_form.field("board").person_set, "their yes makes it their choice"
    asked, submitted = _submit(builder, monkeypatch, False)
    assert asked == [] and len(submitted) == 1, "asked once"


def test_a_board_picked_after_a_weak_match_is_not_asked_about(builder, monkeypatch):
    from orchestrator.gui.builder_parts import after_probe
    assert after_probe(builder, Profile(BOARD_MODEL="Radxa ROCK 5B Plus"), "Rockchip") == "ask"
    next(b for b in builder.board_advice.buttons if "RK3588" in b.text()).click()
    asked, submitted = _submit(builder, monkeypatch, False)
    assert asked == [] and [r.target_board for r in submitted] == ["rk3588"]


def test_dittos_settings_that_were_not_filled_are_named_with_the_reason(builder):
    """Ask Ditto for Config skipped a value the form does not offer without a word (only the person's own values were
    named)."""
    builder.format_combo.setCurrentIndex(builder.format_combo.findData("gguf"))
    builder._on_ai_finished(True, {"format": "gguf", "quantization": "Q9_NOPE"}, "ok")
    log = builder.log_area.toPlainText()
    assert "Q9_NOPE" in log and "It was not filled" in log


def test_add_board_offers_the_documentation_links_when_information_is_missing(booted_framework):
    """The wizard offered Deep Ingest with the links on missing information; an AI client running the add_board prompt
    was never told where the framework's documentation links are."""
    from orchestrator.tools.registry import ToolContext
    _description, parts = booted_framework.tool_registry.get_prompt("add_board", {"board": "Radxa ROCK 5B, RK3588"},
                                                                    ToolContext("mcp"))
    assert "lcf://knowledge/doc-sources" in parts[0] and "crawl_docs_site" in parts[0]
