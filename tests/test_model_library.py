"""The Models tab as one list and the Benchmark window (DESIGN "GUI and the person's path", the row "The Models tab is
one list"): the model library's rows from the framework's own listers, the filter, each row's deletion through its
owner with the person's accepted plan, the kept benchmark runs, the command line, the tab and the window in the real
main window offscreen."""

import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_files import gguf_bytes

COMMIT = "c" * 40


class Config(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


def kv_u32(key: str, value: int):
    return key, 4, struct.pack("<I", value)


def make_build(output: Path, name: str = "rk3588/smol-q4", model: bytes = b"GGUF build model") -> Path:
    folder = output / name
    folder.mkdir(parents=True)
    (folder / "model-q4_0.gguf").write_bytes(model)
    (folder / "build_info.json").write_text(json.dumps({
        "model": "owner/smol", "format": "gguf", "quantization": "Q4_0", "target": "Rockchip", "board": "rk3588",
        "created": "2026-11-11T08:00:00", "primary": "model-q4_0.gguf",
        "artifacts": [{"file": "model-q4_0.gguf", "sha256": hashlib.sha256(model).hexdigest()}],
        "tokenizer_check": {"status": "pass"}}), encoding="utf-8")
    (output / "rk3588" / "smol-q4.zip").write_bytes(b"zip")
    return folder


@pytest.fixture
def fw(tmp_path):
    """A stand-in framework: the models, output, cache and state folders, and a build service on the real listers
    and the real ArtifactStore."""
    from orchestrator.Core.artifact_output import list_artifacts
    from orchestrator.Core.artifact_store import ArtifactStore
    from orchestrator.Core.build_service import artifact_id
    for name in ("models", "output", "cache", "state"):
        (tmp_path / name).mkdir()
    config = Config(models_dir=str(tmp_path / "models"), output_dir=str(tmp_path / "output"),
                    build_dir=str(tmp_path / "output"), cache_dir=str(tmp_path / "cache"))
    config.user_state_dir = str(tmp_path / "state")
    framework = SimpleNamespace(config=config, resolve_path=lambda value: Path(value).expanduser())

    def entries():
        return [(artifact_id(item.key), item.label, item.info) for item in list_artifacts(framework)]
    out = tmp_path / "output"
    framework.build_service = SimpleNamespace(
        artifact_entries=entries,
        artifact_store=lambda: ArtifactStore(out, entries, lambda: [], [("output", out)]))
    framework.audit = SimpleNamespace(rows=[], record=lambda *a, **k: framework.audit.rows.append((a, k)) or 1)
    framework.tmp = tmp_path
    return framework


def fill(fw):
    """A download of the Hugging Face browser (a Q8_0 GGUF), Ditto's model, a build and a kept F16 intermediate."""
    from orchestrator.Core import f16_cache
    models = fw.tmp / "models"
    download = models / "hf" / "owner--smol-GGUF" / COMMIT[:12] / "smol-q8_0.gguf"
    download.parent.mkdir(parents=True)
    download.write_bytes(gguf_bytes(kv=(kv_u32("general.file_type", 7),)))
    (models / "ditto").mkdir()
    (models / "ditto" / "tier.gguf").write_bytes(gguf_bytes(kv=(kv_u32("general.file_type", 15),)))
    build = make_build(fw.tmp / "output")
    made = fw.tmp / "f.gguf"
    made.write_bytes(b"GGUF" + b"\0" * 12)
    f16_cache.F16Cache(fw.tmp / "cache").keep(f16_cache.key("owner/smol", COMMIT, "b1"), made, repo="owner/smol",
                                              commit=COMMIT, pin="b1")
    return download, build


def by_kind(rows):
    return {row.kind: row for row in rows}


def test_one_list_holds_downloads_dittos_model_builds_and_f16(fw):
    from orchestrator.Core.model_library import library, matching
    download, build = fill(fw)
    rows = by_kind(library(fw))
    assert set(rows) == {"download", "ditto", "build", "f16"}
    d = rows["download"]
    assert d.name == "owner/smol-GGUF / smol-q8_0.gguf" and d.quant == "Q8_0" and d.gguf == download
    assert d.deletable and d.where == "models_folder" and d.date
    assert rows["ditto"].quant == "Q4_K_M" and not rows["ditto"].deletable
    b = rows["build"]
    assert (b.name, b.format, b.quant, b.target, b.board) == ("owner/smol", "gguf", "Q4_0", "Rockchip", "rk3588")
    assert b.check == "pass" and b.artifact_id.startswith("a_") and b.deletable and b.path == build
    assert b.date == "2026-11-11 08:00" and b.label == "output:rk3588/smol-q4"
    assert rows["f16"].quant == "F16" and rows["f16"].deletable
    assert "tokenizer check passed" in b.state()
    assert [r.kind for r in matching(rows.values(), "build")] == ["build"]
    assert [r.kind for r in matching(rows.values(), "", "rk3588")] == ["build"]
    assert matching(rows.values(), "", "nothing like it") == []


def test_a_download_is_deleted_only_with_the_persons_accepted_plan(fw):
    from orchestrator.Core import model_library as lib
    from orchestrator.Core.deletion import AcceptedDeletion, PlanChanged, accept
    download, _ = fill(fw)
    row = by_kind(lib.library(fw))["download"]
    plan = lib.download_plan(fw, [row])
    (item,) = plan.items
    assert item.full_path == str(download) and item.size_bytes == download.stat().st_size
    assert "Q8_0" in item.what and plan.mode_offered == ("trash", "permanent")
    with pytest.raises(PermissionError):
        AcceptedDeletion(plan, "permanent", "mcp")
    with pytest.raises(PermissionError):    # the registry's window is no person's GUI or terminal for this list
        lib.delete_downloads(fw, AcceptedDeletion(plan, "permanent", "framework"), audit=fw.audit)
    with pytest.raises(RuntimeError, match="audit"):
        lib.delete_downloads(fw, accept(plan, "gui", "permanent"), audit=None)
    assert download.exists()
    download.write_bytes(download.read_bytes() + b"more")   # the list changed while the person read it
    with pytest.raises(PlanChanged):
        lib.delete_downloads(fw, accept(plan, "gui", "permanent"), audit=fw.audit)
    assert download.exists() and not fw.audit.rows
    plan = lib.download_plan(fw, [by_kind(lib.library(fw))["download"]])
    result = lib.delete_downloads(fw, accept(plan, "cli", "permanent"), audit=fw.audit)
    assert result.done == [row.name] and not download.exists() and len(fw.audit.rows) == 1
    assert lib.download_plan(fw) is None


def test_dittos_model_and_lent_folders_are_never_in_a_plan(fw):
    from orchestrator.Core import model_library as lib
    fill(fw)
    rows = lib.library(fw)
    ditto = by_kind(rows)["ditto"]
    assert lib.download_plan(fw, [ditto]) is None
    ditto.deletable = True   # even when a caller says so: only downloads of the models folder
    assert lib.download_plan(fw, [ditto]) is None


def test_a_build_is_deleted_through_its_owner_the_artifact_store(fw):
    from orchestrator.Core import model_library as lib
    from orchestrator.Core.deletion import accept
    _, build = fill(fw)
    row = by_kind(lib.library(fw))["build"]
    target, plan = lib.build_deletion(fw, row)
    assert [i.full_path for i in plan.items] == [str(build), str(build.parent / "smol-q4.zip")]
    assert plan.mode_offered == ("trash", "permanent")
    with pytest.raises(PermissionError):
        lib.delete_build(fw, row, target, accept(plan, "framework", "permanent"), audit=fw.audit)
    done = lib.delete_build(fw, row, target, accept(plan, "gui", "permanent"), audit=fw.audit)
    assert not build.exists() and not (build.parent / "smol-q4.zip").exists() and done.freed_bytes > 0
    assert fw.audit.rows and fw.audit.rows[0][0][1] == "delete_artifact"
    assert "build" not in by_kind(lib.library(fw))


def test_an_f16_row_is_narrowed_to_its_own_entry(fw):
    from orchestrator.Core import model_library as lib
    fill(fw)
    row = by_kind(lib.library(fw))["f16"]
    plan = lib.f16_deletion(fw, row)
    assert [i.full_path for i in plan.items] == [str(row.path)]


def test_the_kept_runs_carry_the_model_and_find_their_row(fw):
    from orchestrator.Core import model_library as lib
    from orchestrator.runtime.bench import BenchRun, BenchRunner
    fill(fw)
    rows = by_kind(lib.library(fw))
    runner = BenchRunner(Path(fw.config.user_state_dir))
    records = [{"n_prompt": 512, "n_gen": 0, "avg_ts": 2175.5, "stddev_ts": 352.4, "n_threads": 4, "backends": "CPU"},
               {"n_prompt": 0, "n_gen": 128, "avg_ts": 225.4, "stddev_ts": 1.9, "n_threads": 4, "backends": "CPU"}]
    for name, sha in ((rows["download"].name, "d" * 64), ("someone else's", rows["build"].sha256)):
        runner._keep(BenchRun(run_id=f"bench_{'0' * 11}{len(name) % 10}", kind="artifact_speed", model=name,
                              sha256=sha, trusted=False, where="sandbox", build_id="b", backend="cpu",
                              prompt_ts=2175.5, gen_ts=225.4, at="2026-11-11T09:00:00Z", records=records))
    rows = by_kind(lib.library(fw))
    assert rows["download"].bench["model"] == rows["download"].name    # a download: by its name
    assert rows["build"].bench["sha256"] == rows["build"].sha256        # a build: by its manifest's sha256
    assert "225.4 t/s here (Docker sandbox)" in rows["download"].state()
    assert lib.record_rows(records) == [
        {"test": "pp512", "speed": "2175.50 ± 352.40", "threads": "4", "backend": "CPU"},
        {"test": "tg128", "speed": "225.40 ± 1.90", "threads": "4", "backend": "CPU"}]
    run_id = lib.bench_runs(fw)[0]["run_id"]
    assert runner.run_record(run_id)["records"] == records and runner.run_record("../x") is None


def test_threads_reach_llama_bench_and_are_checked(tmp_path):
    from orchestrator.runtime.bench import BenchError, BenchRunner
    runner = BenchRunner(tmp_path)
    build = SimpleNamespace(backend="cpu")
    assert "-t" not in runner._argv("llama-bench", "m.gguf", build)
    for wrong in (-1, 513, True, "4"):
        with pytest.raises(BenchError):
            runner.run(tmp_path / "m.gguf", kind="artifact_speed", label="m", trusted=False, sha256="0" * 64,
                       sandbox=True, threads=wrong)
    runner._threads = 3
    argv = runner._argv("llama-bench", "m.gguf", build)
    assert argv[argv.index("-t") + 1] == "3"


# --- the command line ----------------------------------------------------------------------------------------------

@pytest.fixture
def cli_fw(fw, monkeypatch):
    from orchestrator import cli

    def require(self):
        self.framework_manager, self._initialized = fw, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    return fw


def invoke(*args):
    from click.testing import CliRunner
    from orchestrator import cli
    return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, list(args))


def test_cli_models_list_shows_the_same_rows(cli_fw):
    result = invoke("models", "list")
    assert result.exit_code == 0 and "No downloaded model and no build" in result.output
    fill(cli_fw)
    result = invoke("models", "list", "--json")
    rows = json.loads(result.stdout)
    assert sorted(r["kind"] for r in rows) == ["build", "ditto", "download", "f16"]
    assert next(r for r in rows if r["kind"] == "download")["quant"] == "Q8_0"
    result = invoke("models", "list", "--kind", "build")
    assert result.exit_code == 0 and "owner/smol" in result.output and "Q8_0" not in result.output
    assert json.loads(invoke("models", "list", "--search", "rk3588", "--json").stdout)[0]["kind"] == "build"


def test_cli_models_delete_asks_at_a_terminal_and_refuses_dittos_model(cli_fw, monkeypatch):
    from orchestrator import cli
    download, _ = fill(cli_fw)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr(cli, "_cli_audit", lambda ctx: cli_fw.audit)
    result = invoke("models", "delete", str(download))
    assert result.exit_code == 0 and "Nothing was deleted" in result.output and download.exists()
    result = invoke("models", "delete", str(cli_fw.tmp / "models" / "ditto" / "tier.gguf"))
    assert "not deleted here" in result.output
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    result = CliRunnerInput("1\npermanent\ny\n")("models", "delete", str(download))
    assert result.exit_code == 0 and not download.exists(), result.output


def CliRunnerInput(text):
    from click.testing import CliRunner
    from orchestrator import cli

    def run(*args):
        return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, list(args), input=text)
    return run


def test_cli_models_bench_runs_bench_runner_and_bench_results_lists_it(cli_fw, monkeypatch):
    from orchestrator.runtime import bench
    download, _ = fill(cli_fw)
    seen = {}

    def fake_run(self, model, **kw):
        seen.update(kw, model=model)
        run = bench.BenchRun(run_id="bench_0123456789ab", kind=kw["kind"], model=kw["label"], sha256=kw["sha256"],
                             trusted=kw["trusted"], where="sandbox", build_id="b" * 64, backend="cpu",
                             prompt_ts=100.0, gen_ts=10.0, at="2026-11-11T09:00:00Z",
                             records=[{"n_prompt": 512, "n_gen": 0, "avg_ts": 100.0, "n_threads": 2}])
        self._keep(run)
        return run
    monkeypatch.setattr(bench.BenchRunner, "run", fake_run)
    result = invoke("models", "bench", str(download), "--threads", "2")
    assert result.exit_code == 0 and "pp512" in result.output and "bench_0123456789ab" in result.output
    assert seen["sandbox"] is True and seen["trusted"] is False and seen["threads"] == 2   # never natively here
    assert seen["sha256"] == hashlib.sha256(download.read_bytes()).hexdigest()
    rows = json.loads(invoke("models", "bench-results", "--json").stdout)
    assert rows[0]["model"] == "owner/smol-GGUF / smol-q8_0.gguf" and rows[0]["gen_ts"] == 10.0
    assert invoke("models", "bench", "nothing-like-it").exit_code == 2


# --- the GUI, through the real main window -------------------------------------------------------------------------

def test_the_models_tab_and_the_benchmark_window_in_the_main_window(qapp, tmp_path, monkeypatch, memory_keyring):
    from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton
    import orchestrator.Core.mojo as mojo_package
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.Core.mojo import MojoReport
    from orchestrator.gui import main_window, plain
    from orchestrator.gui.models_tab import ModelsTab
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    class FakeMojo:
        def __init__(self, framework, checks=None, contact=()):
            pass

        def run(self):
            return MojoReport([])
    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(mojo_package, "Mojo", FakeMojo)
    monkeypatch.setattr(mojo_package, "offers", lambda framework, report: [])
    monkeypatch.setattr(QDialog, "exec", lambda self: (self.show(), 0)[1])
    monkeypatch.setattr(plain, "_exec", lambda box: QMessageBox.No)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    from conftest import REPO_ROOT
    win = main_window.MainOrchestrator(REPO_ROOT)
    try:
        tab = win.models_tab
        assert isinstance(tab, ModelsTab) and not tab.loaded   # nothing read at the start
        win.show()
        qapp.processEvents()
        assert not tab.loaded                                    # a tab that is not shown reads nothing
        win.tabs.setCurrentWidget(tab)
        qapp.processEvents()
        assert tab.loaded and tab.stack.is_empty()               # an empty state with its one button
        opened = []
        monkeypatch.setattr(win, "_open_hf_browser", lambda: opened.append("hf"))
        tab._open_hf = win._open_hf_browser
        tab.empty.button.click()
        assert opened == ["hf"]
        models = Path(win.framework.resolve_path(win.framework.config.get("models_dir", "models")))
        download = models / "hf" / "owner--smol-GGUF" / COMMIT[:12] / "smol-q8_0.gguf"
        download.parent.mkdir(parents=True)
        download.write_bytes(gguf_bytes(kv=(kv_u32("general.file_type", 7),)))
        tab.btn_refresh.click()
        assert not tab.stack.is_empty() and tab.table.rowCount() == 1
        assert tab.table.item(0, 3).text() == "Q8_0" and not tab.btn_delete.isEnabled()
        tab.table.selectRow(0)
        assert tab.btn_delete.isEnabled() and tab.btn_bench.isEnabled() and not tab.btn_info.isEnabled()
        for button in tab.findChildren(QPushButton):   # labels show no stray "&"
            assert "&" not in button.text().replace("&&", ""), button.text()
        tab.delete()                                   # the deletion dialog answers No: nothing goes
        assert download.exists()
        tab.benchmark()                                # the Benchmark window with this model chosen
        bench = win.benchmark_window
        assert bench.cb_model.currentData() == tab.current().key and bench.lbl_note.text()
        assert win.findChild(type(win.act_build_benchmark), "act_build_benchmark") is win.act_build_benchmark
        assert win.act_build_benchmark in win.menu_build.actions()
        for button in bench.findChildren(QPushButton):
            assert "&" not in button.text().replace("&&", ""), button.text()
        failures = []
        bench._on_failed = lambda text: failures.append(text)
        from orchestrator.gui import benchmark_window
        monkeypatch.setattr(benchmark_window.LocalBenchWorker, "start", lambda self: self.run())
        bench.worker = None
        bench.start()        # no self-built llama-bench here: the reason, never a crash
        assert failures and "llama" in failures[0].lower()
        from orchestrator.runtime import bench as bench_module

        def stopped_run(self, *a, **k):
            raise bench_module.BenchStopped()
        monkeypatch.setattr(bench_module.BenchRunner, "run", stopped_run)
        monkeypatch.setattr(bench_module.BenchRunner, "trusted_sha256", lambda self: {})
        bench.start()        # Cancel: the run's stop event ends it, the window says so
        assert bench.lbl_status.text() == "The benchmark was stopped." and bench.btn_run.isEnabled()
        rows = bench.table.rowCount()
        bench._on_board_measured({"prompt_ts": 12.5, "gen_ts": 3.25}, "models/smol-q8_0.gguf")   # a board's result
        assert bench.table.rowCount() == rows + 2 and bench.table.item(rows + 1, 1).text() == "3.2"
    finally:
        win.update_timer.stop()
        for child in win.findChildren(QDialog):
            child.close()
        win.close()
        qapp.processEvents()
