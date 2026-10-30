"""The build's model download shows a percentage (DECISIONS round 23 item 12): bytes done of the total over all files
of the snapshot, from the fetcher's progress callback, as the build's stage and percent; throttled; resumed bytes
count; an unknown size shows bytes without a percent; nothing changes after a cancel."""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
from orchestrator.utils.download import SnapshotProgress, snapshot_progress_text
from test_build_tools import FakeHub, config, engine  # noqa: F401 - the engine fixture

MB = 1000 ** 2


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# --- the meter --------------------------------------------------------------------------------------------------

def test_the_meter_sums_every_file_and_reports_at_most_once_a_second():
    clock, reports = Clock(), []
    meter = SnapshotProgress({"a": 100, "b": 300}, lambda d, t: reports.append((d, t)), clock=clock)
    meter.start()
    for have in range(1, 101):          # 100 chunks within the same second: one report, not one per chunk
        meter.file_progress("a", have, 100)
    clock.now = 1.5
    meter.file_progress("b", 100, 300)
    meter.finish("a")
    clock.now = 3.0
    meter.finish("b")                    # the last file: always the final report
    assert reports[0] == (0, 400) and reports[-1] == (400, 400)
    assert len(reports) <= 4 and meter.percent() == 100


def test_resumed_bytes_count_from_the_start():
    reports = []
    meter = SnapshotProgress({"a": 1000, "b": 1000}, lambda d, t: reports.append((d, t)), already={"a": 1000, "b": 250})
    meter.start()
    assert reports == [(1250, 2000)] and meter.percent() == 62
    meter.file_progress("b", 300, 1000)   # the fetcher counts the .part bytes in `have`: never counted twice
    assert meter.done == 1300


def test_an_unknown_size_shows_bytes_without_a_percent():
    reports = []
    meter = SnapshotProgress({"a": 10 * MB, "b": None}, lambda d, t: reports.append((d, t)))
    meter.start()
    meter.finish("a")
    assert meter.total is None and meter.percent() is None
    assert snapshot_progress_text(10 * MB, None) == "10.0 MB"
    assert snapshot_progress_text(37 * MB, 100 * MB) == "37 % (37.0 MB of 100.0 MB)"


# --- the build ---------------------------------------------------------------------------------------------------

class SizedHub(FakeHub):
    """FakeHub with real sizes; its fetcher reports progress in chunks like HubFetcher."""
    SIZES = {"config.json": 1 * MB, "model-1.safetensors": 3 * MB, "model-2.safetensors": 4 * MB,
             "pytorch_model.bin": 2 * MB}

    def install(self, monkeypatch, engine=None, unknown=False, resumed=None, on_chunk=None):  # noqa: F811
        import huggingface_hub
        hub = self
        sizes = self.SIZES

        class Api:
            def __init__(self, endpoint=None, token=None):
                pass

            def model_info(self, repo, revision=None, token=None, files_metadata=False):
                siblings = [SimpleNamespace(rfilename=n, size=None if unknown and n == "config.json" else s, lfs=None)
                            for n, s in sizes.items()]
                return SimpleNamespace(sha=hub.commit, siblings=siblings, gated=False)

        class Fetcher:
            def __init__(self, token, stop):
                self.stop = stop

            def fetch_file(self, repo, commit, file, dest, progress=None):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                size = sizes[file.name]
                have = (resumed or {}).get(file.name, 0)
                while have < size:
                    have = min(size, have + MB // 4)
                    if progress:
                        progress(file.name, have, size)
                    if on_chunk:
                        on_chunk(file.name, have)
                Path(dest).write_bytes(b"w")
                return Path(dest), "0" * 64
        monkeypatch.setattr(huggingface_hub, "HfApi", Api)
        monkeypatch.setattr(BuildEngine, "_hub_fetcher", staticmethod(lambda token, stop: Fetcher(token, stop)))
        return self


def staged_stages(engine, monkeypatch, **kw):   # noqa: F811 - the fixture
    progress = BuildProgress("b1", BuildStatus.QUEUED, "")
    progress.progress_percent = 40
    seen = []
    real = SnapshotProgress._maybe_report

    def every(self, force=False):     # no throttle in the test: every report is seen
        self._last_time = None
        return real(self, force=force)
    monkeypatch.setattr(SnapshotProgress, "_maybe_report", every)
    hub = SizedHub()
    hub.install(monkeypatch, on_chunk=lambda name, have: seen.append((progress.current_stage,
                                                                       progress.progress_percent)), **kw)
    engine._stage_model(config(), progress)
    return progress, seen


def test_the_build_stage_shows_the_percent_over_all_files(engine, monkeypatch):  # noqa: F811
    progress, seen = staged_stages(engine, monkeypatch)
    stages = [s for s, _ in seen]
    assert any(s.startswith("Fetching model ") and " % (" in s for s in stages), stages[:5]
    assert "Fetching model 50 % (4.0 MB of 8.0 MB)" in stages        # the pickle copy is not staged: 8 MB in all
    assert progress.current_stage == "Fetching model 100 % (8.0 MB of 8.0 MB)"
    percents = [p for _, p in seen]
    assert percents == sorted(percents) and 40 <= percents[0] and progress.progress_percent == 50


def test_a_resumed_download_starts_at_the_bytes_already_there(engine, monkeypatch):  # noqa: F811
    folder = engine.staging_folder("Qwen/Qwen2.5-7B-Instruct", None, None)
    reports = []
    real = BuildEngine._download_meter

    def spy(self, cfg, progress, dest, sizes):
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "model-2.safetensors.part").write_bytes(b"x" * (3 * MB))   # an earlier attempt's part
        meter = real(self, cfg, progress, dest, sizes)
        first = meter._report
        meter._report = lambda d, t: reports.append((d, t)) or first(d, t)
        return meter
    monkeypatch.setattr(BuildEngine, "_download_meter", spy)
    staged_stages(engine, monkeypatch, resumed={"model-2.safetensors": 3 * MB})
    assert reports[0] == (3 * MB, 8 * MB), reports[:3]
    assert folder is not None


def test_an_unknown_file_size_shows_bytes_without_a_percent(engine, monkeypatch):  # noqa: F811
    progress, seen = staged_stages(engine, monkeypatch, unknown=True)
    assert all(" % " not in s for s, _ in seen) and progress.current_stage.startswith("Fetching model ")
    assert progress.current_stage.endswith("MB") and progress.progress_percent == 40


def test_after_a_cancel_the_stage_stays(engine, monkeypatch):  # noqa: F811
    from orchestrator.Core.builder import BuildCancelled
    progress = BuildProgress("b1", BuildStatus.QUEUED, "")
    stages = []

    def chunk(name, have):
        if name == "model-1.safetensors" and have >= 2 * MB:
            if progress.status != BuildStatus.CANCELLED:
                progress.status = BuildStatus.CANCELLED
                stages.append(progress.current_stage)
    SizedHub().install(monkeypatch, on_chunk=chunk)
    with pytest.raises(BuildCancelled):
        engine._stage_model(config(), progress)
    assert progress.current_stage == stages[0]   # no percent was written after the cancel


def test_the_build_status_carries_the_download_stage():
    """The service's status (get_build_status, the CLI's follow line, the Builder) shows the engine's stage."""
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.orchestrator import OrchestrationStatus
    build = SimpleNamespace(status=SimpleNamespace(value="building"),
                            current_stage="Fetching model 37 % (3.7 MB of 10.0 MB)", progress_percent=43)
    engine_ = SimpleNamespace(get_build_status=lambda bid: build)

    class Service(BuildService):
        _orchestrator = SimpleNamespace(build_engine=engine_)
    service = Service.__new__(Service)
    state = SimpleNamespace(status=OrchestrationStatus.BUILDING, total_builds=1, build_ids=["b1"],
                            current_stage="Building", progress_percent=10, completed_builds=0, failed_builds=0)
    stage, percent = service.live_progress(state)
    assert stage == "Building: Fetching model 37 % (3.7 MB of 10.0 MB)" and percent == 43
    assert time is not None


def test_the_cli_follow_line_takes_the_live_stage_and_percent():
    from orchestrator import cli as cli_module
    state = SimpleNamespace(current_stage="Building", progress_percent=10)
    live = SimpleNamespace(live_progress=lambda s: ("Building: Fetching model 37 % (3.7 MB of 10.0 MB)", 43))
    assert cli_module._follow_progress(live, state) == ("Building: Fetching model 37 % (3.7 MB of 10.0 MB)", 43)
    assert cli_module._follow_progress(SimpleNamespace(), state) == ("Building", 10)   # a service without it
