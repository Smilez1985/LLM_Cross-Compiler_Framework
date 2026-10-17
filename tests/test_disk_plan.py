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
