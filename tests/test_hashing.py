"""File hashing (consolidation c1-hashing, finding 4; maintainer rule R0).

The first part pins what every copy of the file-hash code did before they became delegates of the one implementation
(utils/helpers.py, where the maintainer's calculate_file_checksum was first): the digest, the error mode (raise, None
or ''), the link rule, and that the bytes that are hashed are the bytes that are used (TOCTOU) with a cancel that stops
mid-file. These tests ran green against the copies before the change and stay green after it."""

import ast
import functools
import hashlib
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

POSIX = pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW and symbolic links without privileges are POSIX")
# Windows refuses to remove or rename a file while a descriptor on it is open (os.open shares no delete access), so
# the path is swapped under an open descriptor only on POSIX systems; the rest of such a test runs everywhere.
SWAP_WHILE_OPEN = os.name != "nt"
SIZES = [0, 1, 4095, 4096, 4097, 8192, (1 << 20) - 1, 1 << 20, (1 << 20) + 1, (3 << 20) + 5]


@functools.cache
def data_of(size: int) -> bytes:
    return (bytes(range(251)) * (size // 251 + 1))[size % 7:size % 7 + size]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _builder_hash(path):
    from orchestrator.Core.builder import BuildEngine
    return BuildEngine._sha256_file(path)


def _named():
    """The 15 named file-hash helpers: (name, function, error mode for a missing file)."""
    from orchestrator.Core import (
        artifact_output,
        artifact_store,
        build_identity,
        dataset_manager,
        deployment_manager,
        knowledge_snapshots,
        trash,
    )
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
            if SWAP_WHILE_OPEN:
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

    def _copy(self, monkeypatch, tmp_path):
        from orchestrator.runtime import bench
        data = b"GGUF" + data_of(3 << 20)
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
            if SWAP_WHILE_OPEN:
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


# --- The one implementation: orchestrator/utils/helpers.py -----------------------------------------------------------

class TestOneImplementation:
    def test_sha256_file_is_independent_of_the_chunk_and_reports_progress(self, tmp_path):
        from orchestrator.utils import helpers
        path = tmp_path / "f.bin"
        path.write_bytes(data_of((3 << 20) + 5))
        for chunk in (4096, 1 << 20, 1 << 24):
            done = []
            assert helpers.sha256_file(path, chunk=chunk, progress=done.append) == sha(data_of((3 << 20) + 5))
            assert done[-1] == (3 << 20) + 5 and done == sorted(done)
        small = tmp_path / "s.bin"
        small.write_bytes(b"abc")
        done = []
        assert helpers.sha256_file(small, chunk=1, progress=done.append) == sha(b"abc") and done == [1, 2, 3]

    def test_a_cancel_is_asked_before_every_read(self, tmp_path):
        from orchestrator.utils import helpers
        path = tmp_path / "f.bin"
        path.write_bytes(data_of(3 << 20))
        asked = []

        def cancel():
            asked.append(1)
            return len(asked) > 2
        with pytest.raises(helpers.HashStopped):
            helpers.sha256_file(path, cancel=cancel)
        assert len(asked) == 3, "stopped before the third chunk was read"

        class Mine(Exception):
            pass

        def raising():
            raise Mine()
        with pytest.raises(Mine):
            helpers.sha256_file(path, cancel=raising)
        empty = tmp_path / "e.bin"
        empty.write_bytes(b"")
        assert helpers.sha256_file(empty, cancel=lambda: False) == sha(b"")

    @POSIX
    def test_without_following_links_a_link_is_refused(self, tmp_path):
        from orchestrator.utils import helpers
        real = tmp_path / "r.bin"
        real.write_bytes(b"r")
        link = tmp_path / "l.bin"
        link.symlink_to(real)
        assert helpers.sha256_file(link) == sha(b"r")
        with pytest.raises(OSError):
            helpers.sha256_file(link, follow_links=False)
        assert helpers.sha256_file(real, follow_links=False) == sha(b"r")
        assert helpers.try_sha256_file(link) is None and helpers.try_sha256_file(real) == sha(b"r")

    def test_a_folder_raises_or_gives_none(self, tmp_path):
        from orchestrator.utils import helpers
        with pytest.raises(OSError):
            helpers.sha256_file(tmp_path, follow_links=False)
        assert helpers.try_sha256_file(tmp_path) is None and helpers.try_sha256_file(tmp_path / "missing") is None

    def test_an_open_handle_or_its_first_bytes(self, tmp_path):
        from orchestrator.utils import helpers
        data = data_of(3 << 20)
        path = tmp_path / "f.bin"
        path.write_bytes(data)
        with open(path, "rb") as fh:
            assert helpers.sha256_fileobj(fh) == sha(data) and fh.tell() == len(data)
            for limit in (0, 5, 1 << 20, len(data), len(data) + 10):
                fh.seek(0)
                assert helpers.sha256_fileobj(fh, limit=limit, chunk=4096) == sha(data[:limit])
                assert fh.tell() == min(limit, len(data)), "the handle is left after the bytes read"
            fh.seek(10)
            assert helpers.sha256_fileobj(fh, limit=20) == sha(data[10:30]), "from the current position"

    def test_a_descriptor_is_hashed_from_its_start(self, tmp_path):
        from orchestrator.utils import helpers
        path = tmp_path / "f.bin"
        path.write_bytes(data_of(3 << 20))
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            os.lseek(fd, 7, os.SEEK_SET)
            assert helpers.sha256_fd(fd, chunk=1 << 16) == sha(data_of(3 << 20))
            if hasattr(os, "pread"):
                assert os.lseek(fd, 0, os.SEEK_CUR) == 7, "pread leaves the position"
            with pytest.raises(helpers.HashStopped):
                helpers.sha256_fd(fd, cancel=lambda: True)
        finally:
            os.close(fd)

    def test_copy_hashed_from_a_path_or_a_descriptor(self, tmp_path):
        from orchestrator.utils import helpers
        data = data_of((2 << 20) + 3)
        source = tmp_path / "s.bin"
        source.write_bytes(data)
        assert helpers.copy_hashed(source, tmp_path / "a.bin", fsync=True) == sha(data)
        assert (tmp_path / "a.bin").read_bytes() == data
        fd = os.open(str(source), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            assert helpers.copy_hashed(fd, tmp_path / "b.bin", chunk=4096) == sha(data)
            os.fstat(fd)  # a descriptor given in stays open
        finally:
            os.close(fd)
        assert (tmp_path / "b.bin").read_bytes() == data

    def test_copy_hashed_never_overwrites_and_never_removes(self, tmp_path):
        from orchestrator.utils import helpers
        source = tmp_path / "s.bin"
        source.write_bytes(data_of(3 << 20))
        (tmp_path / "t.bin").write_bytes(b"keep")
        with pytest.raises(FileExistsError):
            helpers.copy_hashed(source, tmp_path / "t.bin")
        assert (tmp_path / "t.bin").read_bytes() == b"keep"
        if os.name != "nt":  # Windows cannot open a folder as a file at all
            with pytest.raises(IsADirectoryError):
                helpers.copy_hashed(tmp_path, tmp_path / "dir.bin")
            assert not (tmp_path / "dir.bin").exists(), "a folder is refused before the target exists"
        calls = []
        with pytest.raises(helpers.HashStopped):
            helpers.copy_hashed(source, tmp_path / "c.bin", cancel=lambda: calls.append(1) or len(calls) > 1)
        assert (tmp_path / "c.bin").stat().st_size == 1 << 20, "the caller removes a cancelled copy"

    @POSIX
    def test_copy_hashed_refuses_a_link_and_sets_the_mode(self, tmp_path):
        from orchestrator.utils import helpers
        real = tmp_path / "r.bin"
        real.write_bytes(b"r")
        link = tmp_path / "l.bin"
        link.symlink_to(real)
        with pytest.raises(OSError):
            helpers.copy_hashed(link, tmp_path / "x.bin")
        assert not (tmp_path / "x.bin").exists()
        assert helpers.copy_hashed(link, tmp_path / "y.bin", follow_links=True) == sha(b"r")
        old = os.umask(0o022)
        try:
            helpers.copy_hashed(real, tmp_path / "m.bin", mode=0o600)
            helpers.copy_hashed(real, tmp_path / "d.bin")
        finally:
            os.umask(old)
        assert (tmp_path / "m.bin").stat().st_mode & 0o777 == 0o600
        assert (tmp_path / "d.bin").stat().st_mode & 0o777 == 0o644, "the default is open(..., 'xb')'s 0o666"

    def test_a_stream_of_chunks(self):
        from orchestrator.utils import helpers
        got = []
        assert helpers.hash_stream([b"ab", b"", b"c"], sink=got.append) == sha(b"abc") and got == [b"ab", b"c"]
        assert helpers.hash_stream([]) == sha(b"") and helpers.sha256_bytes(b"abc") == sha(b"abc")

    @pytest.mark.parametrize("actual, expected, same", [
        ("ab" * 32, "ab" * 32, True), ("ab" * 32, "AB" * 32, True), ("AB" * 32, "ab" * 32, True),
        ("ab" * 32, "ac" * 32, False), ("ab" * 32, "", False), ("ab" * 32, None, False), ("", "", False),
        ("ab" * 32, "\u00e4" * 64, False), ("ab" * 32, "ab" * 31, False)])
    def test_digest_matches(self, actual, expected, same):
        from orchestrator.utils.helpers import digest_matches
        assert digest_matches(actual, expected) is same

    def test_the_hash_cache(self, tmp_path):
        from orchestrator.utils.helpers import HashCache
        calls = []

        def hasher(path):
            calls.append(path)
            return sha(path.read_bytes())
        cache = HashCache(2, hasher=hasher)
        a = tmp_path / "a.bin"
        a.write_bytes(b"one")
        assert cache.sha256(a) == cache.sha256(a) == sha(b"one") and len(calls) == 1
        a.write_bytes(b"other")
        assert cache.sha256(a) == sha(b"other") and len(calls) == 2, "a changed file is hashed again"
        for i in range(5):
            (tmp_path / f"{i}.bin").write_bytes(bytes([i]))
            cache.sha256(tmp_path / f"{i}.bin")
        assert len(cache._items) <= 3, "more than `cap` entries start the cache over"
        with pytest.raises(OSError):
            cache.sha256(tmp_path / "missing.bin")

    def test_the_hash_cache_is_shared_by_threads(self, tmp_path):
        from orchestrator.utils.helpers import HashCache
        path = tmp_path / "m.bin"
        path.write_bytes(data_of(1 << 20))
        cache, out = HashCache(4), []
        threads = [threading.Thread(target=lambda: out.append(cache.sha256(path))) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert out == [sha(data_of(1 << 20))] * 8

    def test_sha256sums_and_the_sidecar(self, tmp_path):
        from orchestrator.utils import helpers
        assert helpers.sha256sums_line("d", Path("models") / "m.gguf") == "d  models/m.gguf\n"
        assert helpers.sha256sums_line("d", "bin" + os.sep + "x") == "d  bin/x\n"
        assert helpers.sha256sums_text([("a", "x"), ("b", "y/z")]) == "a  x\nb  y/z\n"
        out = tmp_path / "SHA256SUMS"
        helpers.write_sha256sums(iter([("a", Path("x")), ("b", Path("y") / "z")]), out)
        assert out.read_bytes() == b"a  x\nb  y/z\n"
        tarball = tmp_path / "rt.tar.gz"
        tarball.write_bytes(b"t")
        sidecar = helpers.write_sha256_sidecar(tarball, sha(b"t"))
        assert sidecar == tmp_path / "rt.tar.gz.sha256"
        assert sidecar.read_bytes() == f"{sha(b't')}  rt.tar.gz\n".encode("ascii")
        assert helpers.read_sha256_sidecar(tarball) == sha(b"t")
        with pytest.raises(OSError):
            helpers.read_sha256_sidecar(tmp_path / "none.bin")

    def test_the_hash_cache_looks_the_hash_up_when_it_hashes(self, tmp_path, monkeypatch):
        """Without its own hasher the cache calls helpers.sha256_file at each hash (one seam for tests). The deploy
        tools' cache is pinned in tests/test_deploy_tools.py; the benchmark's in tests/test_runtime_tools.py."""
        from orchestrator.utils import helpers
        cache = helpers.HashCache(4)
        monkeypatch.setattr(helpers, "sha256_file", lambda path: "f" * 64)
        (tmp_path / "x.bin").write_bytes(b"x")
        assert cache.sha256(tmp_path / "x.bin") == "f" * 64


# --- Ratchet: no new hash loop beside the one implementation ---------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
# Incremental hashlib digests updated in a loop that are not a file read, each with its reason. Every file hash goes
# through orchestrator/utils/helpers.py (sha256_file, sha256_fileobj, sha256_fd, copy_hashed, hash_stream).
OWN_LOOPS = {
    ("orchestrator/runtime/build/executor.py", "builder_image_tag"):
        "the builder image tag: the Dockerfile's text and the plan's values, already in memory",
    ("orchestrator/runtime/container_server.py", "base_tag"):
        "Ditto's runtime base image tag: the Dockerfile's text and two values, already in memory",
    ("orchestrator/evals/ditto_eval.py", "corpus_digest"): "one digest over names and contents of small corpus files",
    ("orchestrator/mcp_server.py", "._files"): "one digest over the digests of two small state files (a method)",
}


def hash_loops(root: Path):
    """(file, qualified function) of every hashlib hasher updated inside a loop, and of every hashlib.file_digest."""
    found = set()
    for path in sorted((root / "orchestrator").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        for name, fn in _functions(ast.parse(path.read_text(encoding="utf-8")), ""):
            if _hash_loop_in(fn):
                found.add((rel, name))
    return found


def _functions(node, qual):
    """(qualified name, node) of every function and method below `node`."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{qual}.{child.name}" if qual else child.name
            if not isinstance(child, ast.ClassDef):
                yield name, child
            yield from _functions(child, name)


def _hash_loop_in(fn) -> bool:
    hashers = set()
    for node in ast.walk(fn):
        call = node.value if isinstance(node, ast.Assign) else node
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and \
                getattr(call.func.value, "id", "") == "hashlib":
            if call.func.attr == "file_digest":
                return True
            if isinstance(node, ast.Assign):
                hashers.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update"
               and getattr(node.func.value, "id", None) in hashers
               for loop in ast.walk(fn) if isinstance(loop, (ast.For, ast.While, ast.AsyncFor))
               for node in ast.walk(loop))


def _allowed(rel: str, name: str) -> bool:
    return any(rel == f and (name == n or (n.startswith(".") and name.endswith(n))) for f, n in OWN_LOOPS)


def test_no_hash_loop_outside_the_one_implementation():
    found = hash_loops(REPO)
    new = {entry for entry in found if not _allowed(*entry)}
    assert new == set(), f"hash loops beside utils/helpers.py (use sha256_file, sha256_fileobj, copy_hashed): {new}"
    stale = {(f, n) for f, n in OWN_LOOPS if not any(_allowed(*entry) and entry[0] == f and
                                                     (entry[1] == n or entry[1].endswith(n)) for entry in found)}
    assert stale == set(), f"allowlist entries without a loop any more: {stale}"


def test_the_ratchet_sees_a_copied_loop(tmp_path):
    """The detector itself: a classic read loop and a hashlib.file_digest are both found; helpers.py has none."""
    pkg = tmp_path / "orchestrator"
    pkg.mkdir()
    (pkg / "copy.py").write_text(
        "import hashlib\n"
        "def h(p):\n"
        "    d = hashlib.sha256()\n"
        "    with open(p, 'rb') as f:\n"
        "        for c in iter(lambda: f.read(1), b''):\n"
        "            d.update(c)\n"
        "    return d.hexdigest()\n"
        "class K:\n"
        "    def g(self, f):\n"
        "        return hashlib.file_digest(f, 'sha256')\n"
        "def fine(data):\n"
        "    return hashlib.sha256(data).hexdigest()\n", encoding="utf-8")
    assert hash_loops(tmp_path) == {("orchestrator/copy.py", "h"), ("orchestrator/copy.py", "K.g")}
