"""Identical builds (DECISIONS, eighth round, N11).

"Identical" means 100 % the same chosen options: the model and its exact commit (a local model: every weights file by
name, size and time), the target and the sha256 of its definition, board, format, quantization, importance matrix and
calibration data, optimization, context length, GPU use and task. Other weights or another quantization is never
identical. check_build_request and the GUI's builder form report an earlier build; the person (or the AI) uses it or
builds again; nothing is skipped by itself.
"""

import json
import os
from pathlib import Path

import pytest

from orchestrator.Core import build_identity as bi
from orchestrator.tools.registry import ToolContext
from test_build_tools import BUILD, COMMIT_A, env  # noqa: F401 - the fixture
from test_review_capabilities_paths import gui_messages  # noqa: F401 - the fixture

COMMIT_B = "b" * 40


def options(**changes):
    base = dict(model="Qwen/Qwen2.5-7B-Instruct", revision=COMMIT_A, weights=None, target="Rockchip",
                target_sha256="t" * 64, board="rk3588", fmt="gguf", quantization="Q4_K_M", imatrix=False,
                dataset_sha256=None, optimization="balanced", max_context_length=None, use_gpu=False)
    base.update(changes)
    return bi.build_options(**base)


# --- what counts as identical ---------------------------------------------------------------------------------------

def test_the_same_options_and_commit_are_identical():
    assert bi.compare(options(), options()) == ("identical", [])
    assert bi.compare(options(quantization="q4_k_m", board="RK3588"), options())[0] == "identical", \
        "letter case of a type or a board is the same choice"


@pytest.mark.parametrize("change", [dict(quantization="Q5_K_M"), dict(revision=COMMIT_B), dict(board="rk3566"),
                                    dict(fmt="rkllm"), dict(imatrix=True), dict(dataset_sha256="d" * 64),
                                    dict(optimization="size"), dict(max_context_length=4096), dict(use_gpu=True),
                                    dict(target_sha256="u" * 64), dict(model="Qwen/Qwen2.5-3B-Instruct")])
def test_any_other_option_is_not_identical(change):
    match, differ = bi.compare(options(**change), options())
    assert match == "different" and differ


def test_an_unknown_commit_is_never_called_identical():
    assert bi.compare(options(revision=None), options())[0] == "commit_unchecked"
    assert bi.compare(options(), options(revision=None))[0] == "different"


def test_other_weights_of_a_local_model_are_not_identical(tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"weights")
    first = options(model=None, revision=None, weights=bi.local_weights(model))
    assert bi.compare(first, first)[0] == "identical"
    model.write_bytes(b"other weights")
    assert bi.compare(options(model=None, revision=None, weights=bi.local_weights(model)), first)[0] == "different"
    os.utime(model, (1_000_000, 1_000_000))
    assert bi.compare(options(model=None, revision=None, weights=bi.local_weights(model)), first)[0] == "different"


def test_a_target_that_could_not_be_hashed_and_old_manifests_are_never_identical():
    assert bi.compare(options(target_sha256=None), options(target_sha256=None))[0] == "different"
    assert bi.compare(options(), {"model": "Qwen/Qwen2.5-7B-Instruct"})[0] == "different", "no recorded options"


def test_find_earlier_lists_identical_builds_first_newest_first():
    def entry(aid, created, **change):
        return aid, f"output:{aid}", {"primary": "m.gguf", "created": created,
                                      bi.MANIFEST_KEY: options(**change)}
    entries = [entry("old", "2026-10-01"), entry("new", "2026-10-10"), entry("q5", "2026-10-12", quantization="Q5_K_M"),
               entry("nocommit", "2026-10-13", revision=None)]
    assert [b.artifact_id for b in bi.find_earlier(entries, options())] == ["new", "old"]
    assert [(b.artifact_id, b.match) for b in bi.find_earlier(entries, options(revision=None))] == [
        ("new", "commit_unchecked"), ("old", "commit_unchecked")]


def test_the_build_engine_records_every_option(tmp_path):
    from orchestrator.Core.builder import BuildConfiguration, BuildEngine, ModelFormat, OptimizationLevel
    engine = BuildEngine.__new__(BuildEngine)
    config = BuildConfiguration(build_id="b1", timestamp="t", model_source="Qwen/Qwen2.5-7B-Instruct",
                                target_arch="Rockchip", target_format=ModelFormat.GGUF, output_dir=str(tmp_path),
                                quantization="Q4_K_M", target_board="rk3588", model_revision=COMMIT_A,
                                target_pack_sha256="t" * 64, optimization_level=OptimizationLevel.SIZE)
    (tmp_path / "m.gguf").write_bytes(b"w")
    info = engine._write_build_manifest(config, tmp_path)
    recorded = info[bi.MANIFEST_KEY]
    assert recorded == options(optimization="size") and info[bi.DIGEST_KEY] == bi.options_sha256(recorded)
    assert json.loads((tmp_path / "build_info.json").read_text())[bi.MANIFEST_KEY] == recorded


def test_a_local_model_is_recorded_by_its_weights_never_its_path(tmp_path):
    from orchestrator.Core.builder import BuildConfiguration, BuildEngine, ModelFormat
    model = tmp_path / "private-folder" / "m.gguf"
    model.parent.mkdir()
    model.write_bytes(b"w")
    out = tmp_path / "out"
    out.mkdir()
    engine = BuildEngine.__new__(BuildEngine)
    config = BuildConfiguration(build_id="b1", timestamp="t", model_source=str(model), target_arch="Rockchip",
                                target_format=ModelFormat.GGUF, output_dir=str(out))
    recorded = engine._write_build_manifest(config, out)[bi.MANIFEST_KEY]
    assert recorded["model"] is None and recorded["weights"][0]["file"] == "m.gguf"
    assert "private-folder" not in json.dumps(recorded)


# --- check_build_request and start_build ----------------------------------------------------------------------------

def earlier_build(env, revision=COMMIT_A, quantization="Q4_K_M"):  # noqa: F811
    """A finished build in the output folder whose options are those of BUILD (with the given changes)."""
    from orchestrator.schemas.build import BuildRequestIn
    service = env.service
    req, inputs = service._validated(BuildRequestIn(**{**BUILD, "quantization": quantization}), False)
    recorded = service.request_options(req, inputs, revision=revision)
    folder = Path(env.framework.config.values["output_dir"]) / "Rockchip" / "Qwen2.5-7B-Instruct" / quantization
    folder.mkdir(parents=True)
    (folder / "m.gguf").write_bytes(b"w")
    (folder / "build_info.json").write_text(json.dumps({
        "primary": "m.gguf", "created": "2026-10-15T09:00:00", "target": "Rockchip", "format": "gguf",
        "artifacts": [{"file": "m.gguf", "sha256": "e" * 64}], bi.MANIFEST_KEY: recorded}))
    return folder


def test_check_build_request_reports_an_identical_earlier_build(env):  # noqa: F811
    earlier_build(env)
    out = env.registry.call("check_build_request", BUILD, ToolContext("ditto"))
    assert [(b.match, b.label) for b in out.earlier_builds] == [
        ("identical", "output:Rockchip/Qwen2.5-7B-Instruct/Q4_K_M")]
    assert out.earlier_builds[0].sha256 == "e" * 64
    assert any("identical build exists already" in n and "nothing is skipped" in n for n in out.notes)


def test_another_quantization_or_commit_is_not_reported(env):  # noqa: F811
    earlier_build(env, quantization="Q8_0")
    assert env.registry.call("check_build_request", BUILD, ToolContext("ditto")).earlier_builds == []
    earlier_build(env, revision=COMMIT_B)
    assert env.registry.call("check_build_request", BUILD, ToolContext("ditto")).earlier_builds == []


def test_without_the_commit_look_up_it_is_only_the_same_settings(env):  # noqa: F811
    earlier_build(env)
    out = env.registry.call("check_build_request", {**BUILD, "include_download_size": False}, ToolContext("ditto"))
    assert [b.match for b in out.earlier_builds] == ["commit_unchecked"]
    assert any("may differ" in n for n in out.notes)


def test_the_build_question_says_it_builds_an_identical_build_again(env):  # noqa: F811
    earlier_build(env)
    spec = env.registry.get("start_build")
    facts = env.registry.look_up(spec, env.registry.validate_input(spec, dict(BUILD)), ToolContext("ditto"))
    shown = [f for f in facts.facts if f.label == "Identical build"]
    assert shown and shown[0].volatile and "builds it again" in shown[0].value
    assert str(env.tmp) not in shown[0].value


def test_start_build_tells_the_ai_where_the_finished_build_goes(env):  # noqa: F811
    """Eighth round, answer 3: no dialog for an AI caller; the output folder, named by label in get_build_status."""
    from orchestrator.tools.tools_builds import SAVED_NOTE
    assert env.registry.get("start_build").notes(None) == [SAVED_NOTE]
    assert "output folder" in SAVED_NOTE and "get_build_status" in SAVED_NOTE and "output:<target>" in SAVED_NOTE


# --- the GUI's builder form --------------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp", "gui_messages")
class TestBuilderForm:

    @pytest.fixture
    def tab(self, booted_framework, monkeypatch):
        from test_review_capabilities_paths import make_tab, select
        tab = make_tab(booted_framework)
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        tab.quant_combo.setEditText("Q4_K_M")
        tab.model_input.setEditText("HuggingFaceTB/SmolLM2-135M-Instruct")
        found = [bi.EarlierBuild("a_" + "1" * 16, "builds:Rockchip/SmolLM2/Q4_K_M",
                                 {"path": "/srv/builds/Rockchip/SmolLM2/Q4_K_M", "created": "2026-10-15"}, "identical")]
        monkeypatch.setattr(booted_framework.build_service, "earlier_builds", lambda *a, **k: found)
        return tab

    @pytest.mark.parametrize("choice, started", [("use", False), ("again", True), ("cancel", False)])
    def test_the_person_uses_the_earlier_build_or_builds_again(self, tab, monkeypatch, choice, started):
        from orchestrator.gui import artifact_save
        from test_review_capabilities_paths import submit
        asked, folders = [], []
        monkeypatch.setattr(artifact_save, "ask_earlier", lambda parent, found, lang=None: asked.append(found) or
                            choice)
        # Use opens the folder dialog for the copy (round 23 item 9); the person cancels it here: nothing is copied
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: folders.append(start) or "")
        req = submit(tab)
        assert asked and (req is not None) == started
        assert bool(folders) == (choice == "use"), "only Use asks where the earlier build is copied to"
        if choice == "use":
            assert "/srv/builds/Rockchip/SmolLM2/Q4_K_M" in tab.log_area.toPlainText()

    def test_the_commit_is_looked_up_only_when_an_earlier_build_could_be_identical(self, tab, booted_framework,
                                                                                   monkeypatch):
        from orchestrator.gui import artifact_save
        from test_review_capabilities_paths import submit
        service = booted_framework.build_service
        unchecked = [bi.EarlierBuild("a_" + "2" * 16, "builds:x", {"path": "/srv/x", "created": "2026-10-15"},
                                     "commit_unchecked")]
        identical = [bi.EarlierBuild("a_" + "2" * 16, "builds:x", {"path": "/srv/x", "created": "2026-10-15"},
                                     "identical")]
        import threading

        from PySide6.QtWidgets import QApplication
        calls, looked_up, asked, threads = [], [], [], []
        monkeypatch.setattr(service, "earlier_builds", lambda request, revision=None, **k: calls.append(revision) or
                            (identical if revision else unchecked))
        monkeypatch.setattr(service, "model_commit", lambda repo: looked_up.append(repo)
                            or threads.append(threading.current_thread()) or COMMIT_A)
        monkeypatch.setattr(artifact_save, "ask_earlier", lambda parent, found, lang=None: asked.append(found) or
                            "cancel")
        assert submit(tab) is None
        # Hugging Face is asked in a worker (up to 10 s), never on the GUI thread; the Start button waits meanwhile
        assert not tab.btn_start.isEnabled() and asked == []
        tab._commit_worker.wait(10000)
        QApplication.processEvents()
        assert looked_up == ["HuggingFaceTB/SmolLM2-135M-Instruct"] and calls == [None, COMMIT_A]
        assert threads and threads[0] is not threading.main_thread()
        assert asked[0][0].match == "identical" and tab.btn_start.isEnabled()
        monkeypatch.setattr(service, "earlier_builds", lambda *a, **k: [])
        looked_up.clear()
        assert submit(tab) is not None and looked_up == [], "no earlier build: nothing is looked up"

    def test_the_values_checked_are_the_values_submitted_after_the_look_up(self, tab, booted_framework,
                                                                           monkeypatch):
        """The form may change while the commit is looked up: the build started is the one that was compared."""
        from PySide6.QtWidgets import QApplication

        from orchestrator.gui import artifact_save
        service = booted_framework.build_service
        unchecked = [bi.EarlierBuild("a_" + "3" * 16, "builds:x", {"path": "/srv/x", "created": "2026-10-15"},
                                     "commit_unchecked")]
        monkeypatch.setattr(service, "earlier_builds", lambda request, revision=None, **k: [] if revision else
                            unchecked)
        monkeypatch.setattr(service, "model_commit", lambda repo: COMMIT_A)
        monkeypatch.setattr(artifact_save, "ask_earlier", lambda *a, **k: pytest.fail("no identical build"))
        before = len(tab.framework.orchestrator.submitted)
        tab._submit_build()
        tab.quant_combo.setEditText("Q8_0")   # changed while the look-up runs
        tab._commit_worker.wait(10000)
        QApplication.processEvents()
        submitted = tab.framework.orchestrator.submitted
        assert len(submitted) == before + 1 and submitted[-1].quantization_options == ["Q4_K_M"]

    def test_the_question_names_the_full_path_in_plain_text(self, tab, monkeypatch):
        from PySide6.QtCore import Qt

        from orchestrator.gui import artifact_save, plain
        boxes = []
        monkeypatch.setattr(plain, "_exec", lambda box: boxes.append(box) or 0)
        found = [bi.EarlierBuild("a_" + "1" * 16, "builds:x", {"path": "/srv/<b>x</b>", "created": "2026-10-15"},
                                 "identical")]
        assert artifact_save.ask_earlier(None, found) == "cancel"
        assert boxes[0].textFormat() == Qt.PlainText and "/srv/<b>x</b>" in boxes[0].text()
        assert boxes[0].defaultButton().text() == "Use the existing build"


def test_nothing_is_skipped_by_itself():
    """No build path skips a build because an identical one exists: only the person (or the AI after telling them)
    decides. The engine and the orchestrator never read the recorded options."""
    import inspect

    from orchestrator.Core import builder, orchestrator
    assert "find_earlier" not in inspect.getsource(builder) and "earlier_builds" not in inspect.getsource(orchestrator)
