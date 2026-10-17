"""File hashing (consolidation c1-hashing, finding 4; maintainer rule R0).

The first part pins what every copy of the file-hash code did before they became delegates of the one implementation
(utils/helpers.py, where the maintainer's calculate_file_checksum was first): the digest, the error mode (raise, None
or ''), the link rule, and that the bytes that are hashed are the bytes that are used (TOCTOU) with a cancel that stops
mid-file. These tests ran green against the copies before the change and stay green after it."""

import functools
import hashlib
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

POSIX = pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW and symbolic links without privileges are POSIX")
SIZES = [0, 1, 4095, 4096, 4097, 8192, (1 << 20) - 1, 1 << 20, (1 << 20) + 1, (3 << 20) + 5]


@functools.lru_cache(maxsize=None)
def data_of(size: int) -> bytes:
    return (bytes(range(251)) * (size // 251 + 1))[size % 7:size % 7 + size]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _builder_hash(path):
    from orchestrator.Core.builder import BuildEngine
    return BuildEngine._sha256_file(path)


def _named():
    """The 15 named file-hash helpers: (name, function, error mode for a missing file)."""
    from orchestrator.Core import artifact_output, artifact_store, build_identity, dataset_manager, deployment_manager
    from orchestrator.Core import knowledge_snapshots, trash
    from orchestrator.runtime import bench
    from orchestrator.runtime.build import executor, source, verify
    from orchestrator.runtime.device import package
    from orchestrator.utils import download, helpers
    return [
        ("artifact_output._sha256", artifact_output._sha256, "raise"),
        ("artifact_store._sha256", artifact_store._sha256, "none"),
        ("build_identity.file_sha256", build_identity.file_sha256, "none"),
        ("BuildEngine._sha256_file", _builder_hash, "raise"),
        ("dataset_manager._sha256", dataset_manager._sha256, "raise"),
        ("deployment_manager.sha256_file", deployment_manager.sha256_file, "raise"),
        ("knowledge_snapshots._sha256", knowledge_snapshots._sha256, "raise"),
        ("trash.file_sha256", trash.file_sha256, "none"),
        ("bench.sha256_file", bench.sha256_file, "raise"),
        ("executor._sha256", executor._sha256, "raise"),
        ("source._sha256", source._sha256, "raise"),
        ("verify.sha256_file", verify.sha256_file, "raise"),
        ("package._sha256_file", package._sha256_file, "raise"),
        ("download.sha256_file", download.sha256_file, "raise"),
        ("helpers.calculate_file_checksum", helpers.calculate_file_checksum, "empty"),
    ]


NAMES = [n for n, _f, _m in _named()]


def named(name):
    return next((f, m) for n, f, m in _named() if n == name)


class TestEveryNamedCopy:
    @pytest.mark.parametrize("name", NAMES)
    def test_the_digest_is_sha256_of_the_bytes_whatever_the_chunk_size(self, name, tmp_path):
        fn, _mode = named(name)
        for size in SIZES:
            path = tmp_path / f"f{size}.bin"
            path.write_bytes(data_of(size))
            assert fn(path) == sha(data_of(size)), (name, size)

    @pytest.mark.parametrize("name", NAMES)
    def test_a_missing_file_keeps_its_error_mode(self, name, tmp_path):
        fn, mode = named(name)
        missing = tmp_path / "missing.bin"
        if mode == "raise":
            with pytest.raises(OSError):
                fn(missing)
        elif mode == "none":
            assert fn(missing) is None
        else:
            assert fn(missing) == ""

    @pytest.mark.parametrize("name", NAMES)
    def test_a_folder_keeps_its_error_mode(self, name, tmp_path):
        fn, mode = named(name)
        folder = tmp_path / "folder"
        folder.mkdir()
        if mode == "none":
            assert fn(folder) is None
        else:  # helpers checks only that the path exists, so a folder raises like the others
            with pytest.raises(OSError):
                fn(folder)

    @POSIX
    @pytest.mark.parametrize("name", NAMES)
    def test_the_link_rule(self, name, tmp_path):
        """artifact_store and trash refuse links (None); every other copy follows a link to its file."""
        fn, _mode = named(name)
        target = tmp_path / "real.bin"
        target.write_bytes(b"linked bytes")
        link = tmp_path / "link.bin"
        link.symlink_to(target)
        if name in ("artifact_store._sha256", "trash.file_sha256"):
            assert fn(link) is None
        else:
            assert fn(link) == sha(b"linked bytes")

    @POSIX
    @pytest.mark.parametrize("name", ["artifact_store._sha256", "trash.file_sha256", "build_identity.file_sha256"])
    def test_none_also_for_a_file_that_cannot_be_read(self, name, tmp_path):
        if os.geteuid() == 0:
            pytest.skip("root reads every file")
        fn, _mode = named(name)
        path = tmp_path / "secret.bin"
        path.write_bytes(b"x")
        path.chmod(0)
        try:
            assert fn(path) is None
        finally:
            path.chmod(0o600)

    def test_calculate_file_checksum_keeps_its_algorithm_argument(self, tmp_path):
        from orchestrator.utils.helpers import calculate_file_checksum
        path = tmp_path / "a.bin"
        path.write_bytes(data_of(70000))
        for algorithm in ("sha256", "sha1", "md5", "sha512"):
            assert calculate_file_checksum(path, algorithm) == hashlib.new(algorithm, data_of(70000)).hexdigest()

    def test_the_device_package_hashes_bytes_too(self):
        from orchestrator.runtime.device import package
        assert package._sha256_bytes(b"abc") == sha(b"abc")


class TestCancelMidFile:
    def test_a_cancelled_build_stops_hashing_its_model(self, tmp_path):
        from orchestrator.Core.builder import BuildCancelled, BuildEngine, BuildStatus
        path = tmp_path / "model.gguf"
        path.write_bytes(data_of(3 << 20))
        progress = SimpleNamespace(status=BuildStatus.CANCELLED)
        with pytest.raises(BuildCancelled):
            BuildEngine._sha256_file(path, progress)
        progress.status = BuildStatus.EXTRACTING_ARTIFACTS if hasattr(BuildStatus, "EXTRACTING_ARTIFACTS") \
            else next(s for s in BuildStatus if s != BuildStatus.CANCELLED)
        assert BuildEngine._sha256_file(path, progress) == sha(data_of(3 << 20))

    def test_the_cancel_is_checked_per_chunk(self, tmp_path):
        """A cancel that arrives while a large model is hashed stops it before the end."""
        from orchestrator.Core.builder import BuildCancelled, BuildEngine, BuildStatus
        path = tmp_path / "model.gguf"
        path.write_bytes(data_of(4 << 20))
        running = next(s for s in BuildStatus if s != BuildStatus.CANCELLED)

        class Progress:
            reads = 0

            @property
            def status(self):
                Progress.reads += 1
                return BuildStatus.CANCELLED if Progress.reads > 2 else running
        with pytest.raises(BuildCancelled):
            BuildEngine._sha256_file(path, Progress())
        assert Progress.reads == 3


class TestHashedBytesAreTheUsedBytes:
    """bench (open_pinned, _pinned_copy), the build's recipe dataset and artifact_output._copy_file read the file
    once: what was hashed is what is used, even when the path is replaced meanwhile."""

    def test_open_pinned_returns_the_descriptor_it_hashed(self, tmp_path):
        from orchestrator.runtime import bench
        model = tmp_path / "m.gguf"
        model.write_bytes(b"GGUF" + data_of(2 << 20))
        fd = bench.open_pinned(model, sha(model.read_bytes()), "m")
        try:
            model.unlink()
            model.write_bytes(b"swapped")  # a new file under the old name
            assert os.fstat(fd).st_size == (2 << 20) + 4
        finally:
            os.close(fd)

    def test_open_pinned_refuses_another_sha256_and_closes(self, tmp_path):
        from orchestrator.runtime import bench
        model = tmp_path / "m.gguf"
        model.write_bytes(b"GGUF data")
        for wrong in ("0" * 64, "", None):
            with pytest.raises(bench.BenchError, match="changed after it was checked"):
                bench.open_pinned(model, wrong, "m")

    def test_open_pinned_refuses_a_folder(self, tmp_path):
        from orchestrator.runtime import bench
        if os.name == "nt":
            pytest.skip("a folder cannot be opened as a file on Windows")
        with pytest.raises(bench.BenchError) as e:
            bench.open_pinned(tmp_path, "0" * 64, "m")
        assert e.value.code == "not_supported"

    @POSIX
    def test_open_pinned_never_follows_a_link(self, tmp_path):
        from orchestrator.runtime import bench
        model = tmp_path / "m.gguf"
        model.write_bytes(b"GGUF")
        link = tmp_path / "l.gguf"
        link.symlink_to(model)
        with pytest.raises(bench.BenchError, match="cannot be opened"):
            bench.open_pinned(link, sha(b"GGUF"), "m")

    def _copy(self, monkeypatch, tmp_path, data=b"GGUF" + data_of(3 << 20)):
        from orchestrator.runtime import bench
        monkeypatch.setattr(bench.BenchRunner, "_pinned_link", staticmethod(lambda fd, folder, model=None: None))
        model = tmp_path / "m.gguf"
        model.write_bytes(data)
        folder = tmp_path / "pin"
        folder.mkdir()
        fd = os.open(str(model), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        return bench, model, folder, fd, data

    def test_the_pinned_copy_is_the_open_file(self, monkeypatch, tmp_path):
        bench, model, folder, fd, data = self._copy(monkeypatch, tmp_path)
        try:
            os.replace(tmp_path / "m.gguf", tmp_path / "old.gguf")
            model.write_bytes(b"swapped")
            copy = bench.BenchRunner._pinned_copy(model, fd, folder, sha256=sha(data))
            assert copy.read_bytes() == data and copy.parent == folder
        finally:
            os.close(fd)

    def test_the_pinned_copy_refuses_another_sha256_and_leaves_nothing(self, monkeypatch, tmp_path):
        bench, model, folder, fd, _data = self._copy(monkeypatch, tmp_path)
        try:
            with pytest.raises(bench.BenchError, match="another sha256"):
                bench.BenchRunner._pinned_copy(model, fd, folder, sha256="0" * 64)
            assert list(folder.iterdir()) == []
        finally:
            os.close(fd)

    def test_a_set_stop_ends_the_pinned_copy_and_leaves_nothing(self, monkeypatch, tmp_path):
        bench, model, folder, fd, _data = self._copy(monkeypatch, tmp_path)
        stop = threading.Event()
        stop.set()
        try:
            with pytest.raises(bench.BenchStopped):
                bench.BenchRunner._pinned_copy(model, fd, folder, sha256=None, stop=stop)
            assert list(folder.iterdir()) == []
        finally:
            os.close(fd)

    def _stage(self, tmp_path, source, pinned):
        from orchestrator.Core.builder import BuildEngine
        engine = SimpleNamespace(cache_dir=tmp_path / "cache")
        config = SimpleNamespace(dataset_sha256=pinned, dataset_path=str(source), build_id="b1", dataset_source=None)
        logs = []
        BuildEngine._stage_dataset(engine, config, SimpleNamespace(add_log=logs.append))
        return config, logs

    def test_the_recipe_dataset_copy_is_what_was_hashed(self, tmp_path):
        source = tmp_path / "calib.txt"
        source.write_bytes(data_of(1 << 21))
        config, logs = self._stage(tmp_path, source, sha(data_of(1 << 21)))
        target = Path(config.dataset_path)
        assert target.read_bytes() == data_of(1 << 21) and config.dataset_source == str(source)
        assert target.parent == tmp_path / "cache" / "builds" / "b1" / "dataset"
        assert logs == ["Calibration dataset calib.txt copied into the build (sha256 checked)."]

    def test_a_recipe_dataset_with_another_sha256_is_removed(self, tmp_path):
        source = tmp_path / "calib.txt"
        source.write_bytes(b"other text")
        with pytest.raises(ValueError, match="is not the file that was checked"):
            self._stage(tmp_path, source, "0" * 64)
        assert list((tmp_path / "cache" / "builds" / "b1" / "dataset").iterdir()) == []

    @POSIX
    def test_a_recipe_dataset_behind_a_link_or_a_folder_is_refused(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_bytes(b"text")
        link = tmp_path / "calib.txt"
        link.symlink_to(real)
        with pytest.raises(ValueError, match="cannot be opened"):
            self._stage(tmp_path, link, sha(b"text"))
        folder = tmp_path / "set"
        folder.mkdir()
        with pytest.raises(ValueError, match="is not a regular file"):
            self._stage(tmp_path, folder, sha(b"text"))
        assert list((tmp_path / "cache" / "builds" / "b1" / "dataset").iterdir()) == []

    def test_artifact_output_copies_and_hashes_in_one_read(self, tmp_path):
        from orchestrator.Core import artifact_output
        source = tmp_path / "m.gguf"
        source.write_bytes(data_of(3 << 20))
        os.utime(source, (1_600_000_000, 1_600_000_000))
        target = tmp_path / "copy.gguf"
        assert artifact_output._copy_file(source, target) == sha(data_of(3 << 20))
        assert target.read_bytes() == data_of(3 << 20)
        assert int(target.stat().st_mtime) == 1_600_000_000, "copystat keeps the time"
        with pytest.raises(FileExistsError):
            artifact_output._copy_file(source, target)

    @POSIX
    def test_artifact_output_never_copies_through_a_link(self, tmp_path):
        from orchestrator.Core import artifact_output
        real = tmp_path / "real.gguf"
        real.write_bytes(b"x")
        link = tmp_path / "m.gguf"
        link.symlink_to(real)
        with pytest.raises(OSError):
            artifact_output._copy_file(link, tmp_path / "copy.gguf")
        assert not (tmp_path / "copy.gguf").exists()


class TestComparisons:
    def _recipe(self, digest):
        return SimpleNamespace(id="demo", options=SimpleNamespace(dataset=SimpleNamespace(sha256=digest, name="c.txt")))

    def test_the_recipe_dataset_check(self, tmp_path):
        from orchestrator.Core.build_service import BuildService, BuildServiceError
        path = tmp_path / "c.txt"
        path.write_bytes(data_of(5000))
        BuildService.check_recipe_dataset(self._recipe(sha(data_of(5000))), str(path))
        BuildService.check_recipe_dataset(SimpleNamespace(options=SimpleNamespace(dataset=None)), str(path))
        BuildService.check_recipe_dataset(self._recipe("0" * 64), None)
        with pytest.raises(BuildServiceError, match="is not the file the recipe demo pins") as e:
            BuildService.check_recipe_dataset(self._recipe("0" * 64), str(path))
        assert e.value.code == "invalid_value"
        with pytest.raises(BuildServiceError, match="cannot be read"):
            BuildService.check_recipe_dataset(self._recipe("0" * 64), str(tmp_path / "missing.txt"))


class TestSha256Sums:
    def test_the_package_checksum_file(self, tmp_path):
        """LF line endings, two spaces, POSIX separators, nested folders, the checksum file itself left out."""
        from orchestrator.Core.deployment_manager import DeploymentManager
        folder = tmp_path / "pkg"
        (folder / "models" / "sub").mkdir(parents=True)
        (folder / "deploy.sh").write_bytes(b"#!/bin/sh\n")
        (folder / "models" / "sub" / "m.gguf").write_bytes(data_of(9000))
        out = folder / "checksums.sha256"
        out.write_text("old", encoding="utf-8")
        DeploymentManager._generate_checksums(None, folder, out)
        raw = out.read_bytes()
        assert b"\r" not in raw
        lines = sorted(raw.decode("utf-8").splitlines())
        assert lines == sorted([f"{sha(b'#!/bin/sh' + bytes([10]))}  deploy.sh",
                                f"{sha(data_of(9000))}  models/sub/m.gguf"])


class _Response:
    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=8192):
        return iter(self.chunks)


class TestNetworkGuardDownload:
    """utils/network.NetworkGuard.download_file hashes while it downloads (no product caller; item 11 of round 12)."""

    def _guard(self, monkeypatch, chunks):
        from orchestrator.utils import network

        class Session:
            def mount(self, *a):
                pass

            def get(self, url, stream, timeout):
                return _Response(chunks)
        monkeypatch.setattr(network.requests, "Session", Session)
        guard = network.NetworkGuard()
        monkeypatch.setattr(guard, "wait_for_network", lambda verbose=True: None)
        return guard

    def test_a_matching_hash_in_any_case_keeps_the_file(self, monkeypatch, tmp_path):
        chunks = [b"abc", b"", b"def" * 5000]
        guard = self._guard(monkeypatch, chunks)
        target = tmp_path / "f.bin"
        assert guard.download_file("https://example.invalid/f", target, sha(b"".join(chunks)).upper())
        assert target.read_bytes() == b"".join(chunks) and not (tmp_path / "f.bin.tmp").exists()

    def test_a_wrong_hash_removes_the_download(self, monkeypatch, tmp_path):
        guard = self._guard(monkeypatch, [b"abc"])
        folder = tmp_path / "dl"
        folder.mkdir()
        assert guard.download_file("https://example.invalid/f", folder / "f.bin", "0" * 64) is False
        assert list(folder.iterdir()) == []

    def test_without_a_hash_the_download_is_kept(self, monkeypatch, tmp_path):
        guard = self._guard(monkeypatch, [b"abc"])
        assert guard.download_file("https://example.invalid/f", tmp_path / "f.bin")
        assert (tmp_path / "f.bin").read_bytes() == b"abc"


class TestPinnedDownloadCompare:
    def test_a_finished_download_matches_an_upper_case_pin(self, tmp_path):
        """download.fetch lower-cases the pin before the constant-time compare: a finished file is not fetched again."""
        from orchestrator.utils.download import PinnedDownloader
        dest = tmp_path / "m.gguf"
        dest.write_bytes(b"model bytes")

        class NoNetwork:
            def get(self, *a, **k):
                raise AssertionError("a finished, matching file is never downloaded again")
        got = PinnedDownloader(session=NoNetwork()).fetch("https://example.invalid/m", dest, len(b"model bytes"),
                                                         sha(b"model bytes").upper())
        assert got == dest
