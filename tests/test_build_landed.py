"""A running build's disk reservation counts what has landed (A4, open item of consolidation C1).

An AI caller's accepted build takes a reservation in the per-user ledger (orchestrator/Core/disk_plan.py,
ReservationLedger). The bytes the build writes show in the free space as they land; without ReservationLedger.landed()
every other check would count them twice (in the free space and in the reservation). The BuildEngine now records the
growth of the folders the build writes (the staged download, its build folder in the cache, the staging folder next
to the output) through disk_plan.LandedMeter: after each stage and in every round of its disk watchdogs."""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.builder import BuildConfiguration, BuildEngine, BuildProgress, BuildStatus, ModelFormat
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.disk_plan import DiskNeed, LandedMeter, ReservationLedger, device_of

GB = 10 ** 9
KB = 1000


def roomy(path):
    return shutil._ntuple_diskusage(1000 * GB, 100 * GB, 900 * GB)


def reservation(state: Path, folder: Path, size: int = 10 ** 6):
    """A reservation of `size` bytes on the file system of `folder`, as the build service takes it."""
    ledger = ReservationLedger(state)
    rid, _ = ledger.reserve([DiskNeed("cache", folder, size, "test")], "mcp#test", "test build", disk_usage=roomy)
    return ledger, rid


def landed(ledger: ReservationLedger, rid: str, folder: Path) -> int:
    return int(ledger.get(rid)["landed"].get(device_of(folder), 0))


class TestLandedMeter:

    def test_what_lands_below_a_watched_folder_counts_against_the_reservation(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        ledger, rid = reservation(tmp_path / "state", cache)
        meter = LandedMeter(ledger, rid)
        meter.watch(cache)
        (cache / "model.safetensors").write_bytes(b"x" * 3 * KB)
        assert meter.report() == {device_of(cache): 3 * KB}
        assert landed(ledger, rid, cache) == 3 * KB
        assert ledger.outstanding(device_of(cache)) == 10 ** 6 - 3 * KB

    def test_only_growth_counts_files_that_were_there_before_do_not(self, tmp_path):
        """The staged download of the same commit: what was already there is not part of this build."""
        staged = tmp_path / "cache" / "models" / "hf" / "o--m" / ("c" * 40)
        staged.mkdir(parents=True)
        (staged / "config.json").write_bytes(b"x" * 5 * KB)
        ledger, rid = reservation(tmp_path / "state", staged)
        meter = LandedMeter(ledger, rid)
        meter.watch(staged)
        (staged / "model.safetensors.part").write_bytes(b"x" * 2 * KB)
        meter.report()
        assert landed(ledger, rid, staged) == 2 * KB

    def test_a_folder_made_later_counts_all_of_it_and_removed_files_count_no_longer(self, tmp_path):
        ledger, rid = reservation(tmp_path / "state", tmp_path)
        meter = LandedMeter(ledger, rid)
        work = tmp_path / "cache" / "builds" / "b1"
        meter.watch(work)  # not made yet
        work.mkdir(parents=True)
        f16 = work / "m-F16.gguf"
        f16.write_bytes(b"x" * 8 * KB)
        (work / "m-Q4_K_M.gguf").write_bytes(b"x" * 2 * KB)
        meter.report()
        assert landed(ledger, rid, work) == 10 * KB
        f16.unlink()  # the target stage removes the F16 intermediate after quantizing
        meter.report()
        assert landed(ledger, rid, work) == 2 * KB

    def test_folders_on_one_file_system_add_up_and_never_exceed_the_reservation(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir()
        b.mkdir()
        ledger, rid = reservation(tmp_path / "state", tmp_path, size=5 * KB)
        meter = LandedMeter(ledger, rid)
        meter.watch(a, b)
        (a / "one").write_bytes(b"x" * 2 * KB)
        (b / "two").write_bytes(b"x" * 1 * KB)
        assert meter.report() == {device_of(a): 3 * KB}
        (b / "three").write_bytes(b"x" * 4 * KB)
        meter.report()
        assert landed(ledger, rid, a) == 5 * KB, "the ledger keeps landed within the reserved bytes"
        assert ledger.outstanding(device_of(a)) == 0

    def test_the_ledger_is_written_only_when_a_number_changed(self, tmp_path, monkeypatch):
        ledger, rid = reservation(tmp_path / "state", tmp_path)
        calls = []
        original = ReservationLedger.landed
        monkeypatch.setattr(ReservationLedger, "landed",
                            lambda self, r, d, w: (calls.append(w), original(self, r, d, w)))
        meter = LandedMeter(ledger, rid)
        folder = tmp_path / "out"
        folder.mkdir()
        meter.watch(folder)
        meter.report()
        meter.report()
        (folder / "f").write_bytes(b"x" * KB)
        meter.report()
        meter.report()
        assert calls == [0, KB]

    def test_watching_a_folder_again_keeps_its_first_baseline(self, tmp_path):
        ledger, rid = reservation(tmp_path / "state", tmp_path)
        meter = LandedMeter(ledger, rid)
        folder = tmp_path / "out"
        folder.mkdir()
        meter.watch(folder)
        (folder / "f").write_bytes(b"x" * KB)
        meter.watch(folder)  # every stage names its folders; the first baseline stays
        meter.report()
        assert landed(ledger, rid, folder) == KB


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    state = tmp_path / "state"
    monkeypatch.setattr(ConfigManager, "user_state_dir", property(lambda self: state))
    e = BuildEngine(ConfigManager(tmp_path / "configs"))
    e.cache_dir.mkdir(parents=True, exist_ok=True)
    return e


def make_config(**overrides):
    values = dict(build_id="b1", timestamp="t", model_source="HuggingFaceTB/SmolLM2-135M-Instruct",
                  target_arch="Rockchip", target_format=ModelFormat.GGUF, output_dir="out/b1",
                  quantization="Q4_K_M", cleanup_after_build=False)
    values.update(overrides)
    return BuildConfiguration(**values)


class TestBuildEngine:

    def test_every_stage_of_a_build_records_what_it_wrote(self, engine, tmp_path, monkeypatch):
        ledger, rid = reservation(tmp_path / "state", engine.cache_dir)
        written = []
        original = ReservationLedger.landed
        monkeypatch.setattr(ReservationLedger, "landed",
                            lambda self, r, d, w: (written.append(w), original(self, r, d, w)))
        (engine.targets_dir / "Rockchip").mkdir(parents=True)
        build_temp = engine.cache_dir / "builds" / "b1"

        def modules(config, progress, *a):  # the conversion writes its output into the build folder
            (build_temp / "output" / "m-Q4_K_M.gguf").write_bytes(b"x" * 5 * KB)

        def golden(config, progress):
            (engine._staging_root(config) / "b1.zip").write_bytes(b"x" * 2 * KB)

        for name in ("_generate_dockerfile", "_scan_image_security"):
            monkeypatch.setattr(engine, name, lambda *a, **k: None)
        monkeypatch.setattr(engine, "_build_docker_image", lambda *a: SimpleNamespace(tags=["img"], id=None))
        monkeypatch.setattr(engine, "_stage_model", lambda *a: ("/x", {}))
        monkeypatch.setattr(engine, "_execute_build_modules", modules)
        monkeypatch.setattr(engine, "_create_golden_artifact", golden)
        engine._builds["b1"] = BuildProgress("b1", BuildStatus.QUEUED, "")
        engine._execute_build(make_config(disk_reservation=rid))

        assert engine._builds["b1"].status == BuildStatus.COMPLETED, engine._builds["b1"].errors
        # nothing yet; the output in the build folder and its staged copy; then the golden ZIP
        assert written == [0, 10 * KB, 12 * KB]
        assert landed(ledger, rid, engine.cache_dir) == 12 * KB
        assert "b1" not in engine.__dict__.get("_meters", {}), "the meter ends with the build"

    def test_the_staged_download_counts_as_it_arrives(self, engine, tmp_path, monkeypatch):
        import huggingface_hub
        ledger, rid = reservation(tmp_path / "state", engine.cache_dir)
        folder = engine.cache_dir / "models" / "hf" / "HuggingFaceTB--SmolLM2-135M-Instruct" / ("c" * 40)
        folder.mkdir(parents=True)
        (folder / "config.json").write_bytes(b"x" * KB)  # staged by an earlier build of the same commit

        class FakeApi:
            def __init__(self, token=None):
                pass

            def model_info(self, repo, revision=None, token=None, files_metadata=False):
                return SimpleNamespace(sha="c" * 40, siblings=[
                    SimpleNamespace(rfilename=n, size=1, lfs=None) for n in ("config.json", "model.safetensors")])

        class FakeFetcher:
            def fetch_file(self, repo, commit, file, dest, progress=None):
                if file.name == "model.safetensors":
                    Path(dest).write_bytes(b"x" * 4 * KB)
                return dest, "0" * 64

        monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
        monkeypatch.setattr(type(engine), "_hub_fetcher", staticmethod(lambda token, stop: FakeFetcher()))
        config = make_config(disk_reservation=rid)
        engine._stage_model(config, BuildProgress("b1", BuildStatus.QUEUED, ""))
        engine._report_landed(config)  # what the build runs right after staging
        assert landed(ledger, rid, folder) == 4 * KB, "only what this build downloaded"

    def test_the_disk_watchdogs_record_on_every_round(self, engine, tmp_path, monkeypatch):
        ledger, rid = reservation(tmp_path / "state", engine.cache_dir)
        config = make_config(disk_reservation=rid)
        work = engine.cache_dir / "builds" / "b1"
        work.mkdir(parents=True)
        engine._report_landed(config, work)
        (work / "part").write_bytes(b"x" * 3 * KB)
        monkeypatch.setattr(engine, "_low_disk_text", lambda folder, what: f"{what} stopped")
        assert engine._disk_check(config)(engine.cache_dir, "Build") == "Build stopped"
        assert landed(ledger, rid, work) == 3 * KB

    def test_a_build_without_a_reservation_writes_no_ledger(self, engine, tmp_path):
        config = make_config()
        work = engine.cache_dir / "builds" / "b1"
        work.mkdir(parents=True)
        engine._report_landed(config, work)
        (work / "part").write_bytes(b"x" * KB)
        engine._report_landed(config)
        assert not (tmp_path / "state").exists()
        assert not engine.__dict__.get("_meters")

    def test_without_a_per_user_folder_nothing_is_recorded(self, tmp_path):
        e = BuildEngine.__new__(BuildEngine)  # a stand-in configuration without user_state_dir
        e.config, e.logger = {}, SimpleNamespace(debug=print)
        e._report_landed(make_config(disk_reservation="r_0123456789abcdef"), tmp_path)
        assert not e.__dict__.get("_meters")

    def test_a_ledger_that_cannot_be_written_never_stops_the_build(self, engine, tmp_path, monkeypatch):
        ledger, rid = reservation(tmp_path / "state", engine.cache_dir)

        def broken(self, *a):
            raise TimeoutError("ledger locked")
        monkeypatch.setattr(ReservationLedger, "landed", broken)
        engine._report_landed(make_config(disk_reservation=rid), engine.cache_dir)  # no exception


def test_the_orchestrator_hands_the_reservation_to_the_build():
    from orchestrator.Core.builder import OptimizationLevel
    from orchestrator.Core.orchestrator import BuildJob, BuildRequest, LLMOrchestrator, PriorityLevel, WorkflowType
    req = BuildRequest(request_id="req_1", workflow_type=WorkflowType.SIMPLE_CONVERSION,
                       priority=PriorityLevel.NORMAL, models=["m"], targets=["Rockchip"],
                       target_formats=[ModelFormat.GGUF], optimization_level=OptimizationLevel.BALANCED,
                       quantization_options=["Q4_K_M"], parallel_builds=False, output_base_dir="out",
                       disk_reservation="r_0123456789abcdef")
    job = BuildJob("j1", "m", "Rockchip", ModelFormat.GGUF, OptimizationLevel.BALANCED, "Q4_K_M", "out",
                   BuildStatus.QUEUED)
    orchestrator = SimpleNamespace(config=SimpleNamespace())
    assert LLMOrchestrator._map_job_to_config(orchestrator, job, req).disk_reservation == "r_0123456789abcdef"
    req.disk_reservation = ""
    assert LLMOrchestrator._map_job_to_config(orchestrator, job, req).disk_reservation == ""
