"""The disk watchdog and the disk-full texts (consolidation c1-disk, finding 5).

The first part pins BuildEngine's texts and checks as they were before they moved into Core/disk_plan.py: the exact
words (with 'No space left on device', which self_healing_manager.DISK_FULL_PATTERN matches), the detection of a full
disk, that only labels (cache, output, builds) are named and never paths, and the trivy skip warning. These tests ran
green before the move and stay green after it; tests/test_build_tools.py pins the two watcher threads."""

import errno
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

GB = 10 ** 9


def usage(free, total=1000 * GB):
    return shutil._ntuple_diskusage(total, total - free, free)


@pytest.fixture
def engine(tmp_path):
    from orchestrator.Core.builder import BuildEngine
    e = BuildEngine.__new__(BuildEngine)
    e.cache_dir, e.base_dir = tmp_path / "cache", tmp_path
    e.cache_dir.mkdir()
    e.secrets_manager, e.framework, e.config = None, None, {}
    e.logger = SimpleNamespace(info=print, warning=print, debug=print, error=print)
    e._get_conf = lambda key, default=None: default
    return e


def disk(monkeypatch, free, total=1000 * GB, seen=None):
    def du(path):
        if seen is not None:
            seen.append(Path(path))
        return usage(free, total)
    monkeypatch.setattr("orchestrator.Core.builder.shutil.disk_usage", du)


class TestLowDiskText:
    TEXT = ("Staging stopped: the cache disk has only 500 MB free, less than half of the 20.0 GB that must stay free. "
            "No space left on device for the rest of the build.")

    def test_below_half_the_reserve_it_says_so_in_these_words(self, engine, monkeypatch, tmp_path):
        disk(monkeypatch, 5 * 10 ** 8)
        assert engine._low_disk_text(tmp_path, "Staging") == self.TEXT
        from orchestrator.Core.builder import BuildEngine
        assert BuildEngine._low_disk_text(tmp_path, "Build") == self.TEXT.replace("Staging", "Build", 1)

    def test_at_half_the_reserve_or_more_there_is_room(self, engine, monkeypatch, tmp_path):
        disk(monkeypatch, 10 * GB)  # reserve 20 GB (2 % of 1000 GB), half of it is 10 GB
        assert engine._low_disk_text(tmp_path, "Staging") is None
        disk(monkeypatch, 10 * GB - 1)
        assert engine._low_disk_text(tmp_path, "Staging") is not None

    def test_a_small_disk_keeps_two_gib(self, engine, monkeypatch, tmp_path):
        disk(monkeypatch, 1024 ** 3 - 1, total=50 * GB)
        assert "half of the 2.1 GB that must stay free" in engine._low_disk_text(tmp_path, "Staging")
        disk(monkeypatch, 1024 ** 3, total=50 * GB)
        assert engine._low_disk_text(tmp_path, "Staging") is None

    def test_a_folder_not_made_yet_is_measured_on_its_nearest_existing_parent(self, engine, monkeypatch, tmp_path):
        seen = []
        disk(monkeypatch, 100 * GB, seen=seen)
        engine._low_disk_text(tmp_path / "a" / "b" / "c", "Staging")
        assert seen == [tmp_path]

    def test_a_disk_that_cannot_be_measured_stops_nothing(self, engine, monkeypatch, tmp_path):
        def broken(path):
            raise OSError("gone")
        monkeypatch.setattr("orchestrator.Core.builder.shutil.disk_usage", broken)
        assert engine._low_disk_text(tmp_path, "Staging") is None

    def test_the_staging_check_raises_insufficient_space_with_the_text(self, engine, monkeypatch, tmp_path):
        from orchestrator.Core import disk_plan
        disk(monkeypatch, 5 * 10 ** 8)
        with pytest.raises(disk_plan.InsufficientSpace) as e:
            engine._check_staging_space(tmp_path, SimpleNamespace())
        assert str(e.value) == self.TEXT and e.value.code == "insufficient_resources"
        disk(monkeypatch, 500 * GB)
        assert engine._check_staging_space(tmp_path, SimpleNamespace()) is None


class TestDiskFullText:
    @pytest.mark.parametrize("error, detail", [
        (OSError(errno.ENOSPC, "No space left on device"), "[Errno 28] No space left on device"),
        (OSError(errno.EDQUOT, "Disk quota exceeded"), "[Errno 122] Disk quota exceeded (No space left on device)"),
        (RuntimeError("tar: write error: No space left on device"), "tar: write error: No space left on device"),
        (RuntimeError("Disk quota exceeded"), "Disk quota exceeded (No space left on device)"),
        (SimpleNamespace(code="insufficient_resources", __str__=None), None),
    ])
    def test_a_full_disk_is_reason_insufficient_resources_with_the_free_space_by_label(self, engine, monkeypatch,
                                                                                        tmp_path, error, detail):
        if detail is None:
            class Coded(Exception):
                code = "insufficient_resources"
            error, detail = Coded("the build stopped"), "the build stopped (No space left on device)"
        disk(monkeypatch, 10 ** 6)
        (tmp_path / "out").mkdir()
        text = engine._disk_full_text(error)
        assert text == ("insufficient_resources: the disk ran full during the build (cache disk 1 MB free, output "
                        f"disk 1 MB free). {detail}")
        assert str(tmp_path) not in text, "labels only, never paths"

    def test_other_errors_are_not_a_full_disk(self, engine, monkeypatch):
        disk(monkeypatch, 10 ** 6)
        assert engine._disk_full_text(ValueError("other")) is None
        assert engine._disk_full_text(OSError(errno.EACCES, "Permission denied")) is None

    def test_the_builds_folder_is_named_when_it_is_elsewhere(self, engine, monkeypatch, tmp_path):
        disk(monkeypatch, 3 * GB)
        engine.build_dir = tmp_path / "builds"
        text = engine._disk_full_text(OSError(errno.ENOSPC, "No space left on device"))
        assert "(cache disk 3.0 GB free, output disk 3.0 GB free, builds disk 3.0 GB free)" in text

    def test_a_folder_without_its_parent_is_left_out(self, engine, monkeypatch, tmp_path):
        seen = []

        def du(path):
            seen.append(Path(path))
            if not Path(path).exists():
                raise FileNotFoundError(path)
            return usage(GB)
        monkeypatch.setattr("orchestrator.Core.builder.shutil.disk_usage", du)
        engine._get_conf = lambda key, default=None: "gone/deeper" if key == "output_dir" else default
        text = engine._disk_full_text(OSError(errno.ENOSPC, "No space left on device"))
        assert "(cache disk 1.0 GB free)" in text and "output" not in text
        assert tmp_path / "gone" in seen, "a missing folder is measured on its parent only"

    def test_no_measurable_folder_leaves_out_the_brackets(self, engine, monkeypatch):
        def broken(path):
            raise OSError("gone")
        monkeypatch.setattr("orchestrator.Core.builder.shutil.disk_usage", broken)
        assert engine._disk_full_text(OSError(errno.ENOSPC, "No space left on device")) == \
            "insufficient_resources: the disk ran full during the build. [Errno 28] No space left on device"

    def test_self_healing_recognizes_both_texts(self, engine, monkeypatch):
        import re

        from orchestrator.Core.self_healing_manager import DISK_FULL_PATTERN
        assert DISK_FULL_PATTERN == r"No space left on device|Disk quota exceeded"
        disk(monkeypatch, 5 * 10 ** 8)
        assert re.search(DISK_FULL_PATTERN, engine._low_disk_text(Path("."), "Staging"))
        assert re.search(DISK_FULL_PATTERN, engine._disk_full_text(RuntimeError("Disk quota exceeded")))


class TestTrivySkip:
    def test_the_scan_is_skipped_with_a_warning_when_the_saved_image_would_not_fit(self, engine, monkeypatch):
        image = SimpleNamespace(attrs={"Size": 3 * GB}, save=lambda named=True: iter([b"x"]))
        engine.docker_client = SimpleNamespace(images=SimpleNamespace(get=lambda tag: image))
        warnings = []
        progress = SimpleNamespace(add_warning=warnings.append, add_log=lambda m: None, status=None)
        disk(monkeypatch, 12 * GB)  # 12 - 3 = 9 GB left, half the 20 GB reserve is 10 GB
        assert engine._save_image_for_scan("img:1", progress, "b1") == (None, None)
        assert warnings == [("Security scan skipped: the scanner reads a saved copy of the image (about 3.0 GB), and "
                             "the cache disk has 12.0 GB free, of which 10.0 GB must stay free.")]
        assert not list((engine.cache_dir / "builds" / "b1").iterdir())

    def test_with_room_the_image_is_saved_for_the_scan(self, engine, monkeypatch):
        from orchestrator.Core.builder import BuildStatus
        image = SimpleNamespace(attrs={"Size": 3 * GB}, save=lambda named=True: iter([b"layer"]))
        engine.docker_client = SimpleNamespace(images=SimpleNamespace(get=lambda tag: image))
        warnings = []
        progress = SimpleNamespace(add_warning=warnings.append, add_log=lambda m: None, status=BuildStatus.BUILDING)
        disk(monkeypatch, 13 * GB)
        folder, archive = engine._save_image_for_scan("img:1", progress, "b1")
        assert warnings == [] and archive.read_bytes() == b"layer"
        shutil.rmtree(folder)


class TestWatchers:
    def test_the_container_watchdog_writes_the_reason_into_the_log_and_kills_the_container(self, engine,
                                                                                          monkeypatch):
        from orchestrator.Core import builder, disk_plan
        from orchestrator.Core.builder import BuildProgress, BuildStatus
        monkeypatch.setattr(builder, "CONTAINER_WATCH_EVERY", 0.01)
        disk(monkeypatch, 5 * 10 ** 8)
        engine._lock, engine._active_containers, engine.default_timeout = threading.Lock(), {}, 60
        killed = threading.Event()

        class Container:
            def start(self):
                pass

            def logs(self, stream=True, follow=True):
                return iter([])

            def wait(self, timeout=None):
                killed.wait(5)
                return {"StatusCode": 137}

            def kill(self):
                killed.set()

            def remove(self, force=True):
                pass
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        config = SimpleNamespace(build_id="b1", build_timeout=60)
        with pytest.raises(disk_plan.InsufficientSpace) as e:
            engine._run_container(config, progress, Container(), "Convert: ")
        assert str(e.value).startswith("Convert stopped: the cache disk has only 500 MB free")
        assert any("] Convert stopped: the cache disk" in line for line in progress.logs)

    def test_a_container_with_room_runs_to_its_end(self, engine, monkeypatch):
        from orchestrator.Core import builder
        from orchestrator.Core.builder import BuildProgress, BuildStatus
        monkeypatch.setattr(builder, "CONTAINER_WATCH_EVERY", 0.01)
        disk(monkeypatch, 500 * GB)
        engine._lock, engine._active_containers, engine.default_timeout = threading.Lock(), {}, 60

        class Container:
            killed = False

            def start(self):
                pass

            def logs(self, stream=True, follow=True):
                return iter([b"done\n"])

            def wait(self, timeout=None):
                time.sleep(0.05)
                return {"StatusCode": 0}

            def kill(self):
                Container.killed = True

            def remove(self, force=True):
                pass
        progress = BuildProgress("b1", BuildStatus.BUILDING, "")
        assert engine._run_container(SimpleNamespace(build_id="b1", build_timeout=60), progress, Container(), "") == 0
        assert not Container.killed


# --- The one place: Core/disk_plan.py ---------------------------------------------------------------------------------

class TestDiskPlanWatchdog:
    def test_nearest_existing_and_the_old_name(self, tmp_path):
        from orchestrator.Core import disk_plan
        assert disk_plan.nearest_existing(tmp_path / "x" / "y") == tmp_path
        assert disk_plan.nearest_existing(tmp_path) == tmp_path
        assert disk_plan._existing is disk_plan.nearest_existing

    def test_free_space_has_one_seam(self, tmp_path, monkeypatch):
        from orchestrator.Core import disk_plan
        seen = []
        assert disk_plan.free_space(tmp_path / "new", lambda p: seen.append(p) or usage(7)).free == 7
        assert seen == [str(tmp_path)]
        monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(9))
        assert disk_plan.free_space(tmp_path).free == 9, "shutil.disk_usage is looked up at each call"

    def test_the_floor_is_half_the_reserve(self):
        from orchestrator.Core import disk_plan
        assert disk_plan.low_disk_floor(1000 * GB) == disk_plan.reserve_for(1000 * GB) // 2 == 10 * GB
        assert disk_plan.low_disk_floor(10 * GB) == disk_plan.RESERVE_MIN // 2

    def test_low_disk_text(self, tmp_path):
        from orchestrator.Core import disk_plan
        assert disk_plan.low_disk_text(tmp_path, "Staging", lambda p: usage(5 * 10 ** 8)) == TestLowDiskText.TEXT
        assert disk_plan.low_disk_text(tmp_path, "Staging", lambda p: usage(10 * GB)) is None

        def broken(path):
            raise OSError("gone")
        assert disk_plan.low_disk_text(tmp_path, "Staging", broken) is None

    @pytest.mark.parametrize("error, full", [
        (OSError(errno.ENOSPC, "x"), True), (OSError(errno.EDQUOT, "x"), True), (OSError(errno.EACCES, "x"), False),
        ("No space left on device", True), ("Disk quota exceeded", True), ("Permission denied", False),
        (RuntimeError("write error: No space left on device"), True), (ValueError("other"), False)])
    def test_is_disk_full(self, error, full):
        from orchestrator.Core import disk_plan
        assert disk_plan.is_disk_full(error) is full

    def test_is_disk_full_by_reason_code(self):
        from orchestrator.Core import disk_plan
        assert disk_plan.is_disk_full(disk_plan.InsufficientSpace("not enough room"))

    def test_disk_full_text_by_label(self, tmp_path):
        from orchestrator.Core import disk_plan
        folders = {"cache": tmp_path, "output": tmp_path / "new"}
        text = disk_plan.disk_full_text(OSError(errno.ENOSPC, "No space left on device"), folders,
                                        lambda p: usage(2 * GB))
        assert text == ("insufficient_resources: the disk ran full during the build (cache disk 2.0 GB free, output "
                        "disk 2.0 GB free). [Errno 28] No space left on device")
        assert disk_plan.disk_full_text(ValueError("other"), folders) is None
        assert disk_plan.DISK_FULL_PHRASE in disk_plan.disk_full_text(RuntimeError("Disk quota exceeded"), {})

    def test_the_watchdog_calls_on_low_once_and_ends(self, tmp_path):
        from orchestrator.Core import disk_plan
        calls, stop = [], threading.Event()
        dog = disk_plan.DiskWatchdog(tmp_path, "Convert", 0.01, calls.append, stop,
                                     check=lambda folder, what: f"{what} low in {folder.name}").start()
        dog.thread.join(5)
        assert not dog.thread.is_alive() and calls == [f"Convert low in {tmp_path.name}"] and dog.text == calls[0]
        assert not stop.is_set(), "on_low decides what stops"

    def test_the_watchdog_ends_when_stopped_or_when_the_caller_says_so(self, tmp_path):
        from orchestrator.Core import disk_plan
        calls, stop = [], threading.Event()
        dog = disk_plan.DiskWatchdog(tmp_path, "Staging", 0.01, calls.append, stop, check=lambda f, w: None).start()
        time.sleep(0.05)
        stop.set()
        dog.thread.join(5)
        assert not dog.thread.is_alive() and calls == []
        asked = []

        def caller_says_stop():
            asked.append(1)
            return True
        dog = disk_plan.DiskWatchdog(tmp_path, "Staging", 0.01, calls.append, also_stop_when=caller_says_stop,
                                     check=lambda f, w: "never asked").start()
        dog.thread.join(5)
        assert not dog.thread.is_alive() and calls == [] and asked == [1]

    def test_the_watchdog_checks_the_real_disk_by_default(self, tmp_path, monkeypatch):
        from orchestrator.Core import disk_plan
        monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(5 * 10 ** 8))
        calls = []
        dog = disk_plan.DiskWatchdog(tmp_path, "Staging", 0.01, calls.append, name="t").start()
        dog.thread.join(5)
        assert calls == [TestLowDiskText.TEXT] and dog.thread.name == "t"

    def test_build_engine_delegates_to_disk_plan(self, engine, monkeypatch, tmp_path):
        from orchestrator.Core import disk_plan
        monkeypatch.setattr(disk_plan, "low_disk_text", lambda folder, what: f"low {what}")
        monkeypatch.setattr(disk_plan, "disk_full_text", lambda error, folders: sorted(folders))
        assert engine._low_disk_text(tmp_path, "Staging") == "low Staging"
        assert engine._disk_full_text(OSError()) == ["cache", "output"]
        engine.build_dir = tmp_path / "builds"
        assert engine._disk_full_text(OSError()) == ["builds", "cache", "output"]
