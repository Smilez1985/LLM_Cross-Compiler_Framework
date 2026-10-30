"""The disk guard of tests/conftest.py: what a test leaves and what the run keeps, in apparent sizes.

A Qdrant Edge store sets up about 160 MB of files that are sparse on Linux (no blocks, `du` shows nothing) and
allocated in full on NTFS: the Windows runner ran out of disk while this sandbox showed nothing. The guard counts
apparent sizes (st_size). The inner runs below load the real conftest as a plugin on a tiny test file, with lowered
budgets (the ini options that can only lower them) and their own system temporary folder.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent


def _sparse(path: Path, size: int) -> Path:
    """A file with `size` apparent bytes and (on Linux) no blocks: what a Qdrant Edge write-ahead log looks like."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size)
    return path


def test_apparent_sizes_count_sparse_files_and_never_follow_links(tmp_path):
    _sparse(tmp_path / "store" / "wal" / "open-1", 3 * conftest.MIB)
    (tmp_path / "store" / "meta.json").write_text("{}")
    outside = _sparse(tmp_path.parent / f"{tmp_path.name}-outside" / "huge", 50 * conftest.MIB)
    try:
        (tmp_path / "store" / "link").symlink_to(outside.parent, target_is_directory=True)
        linked = True
    except (OSError, NotImplementedError):
        linked = False
    total, files = conftest.apparent_usage([tmp_path / "store"])
    sizes = dict((rel, size) for size, rel in files)
    assert sizes["store/wal/open-1"] == 3 * conftest.MIB and sizes["store/meta.json"] == 2
    assert total < 4 * conftest.MIB, "a link was followed"
    if linked:
        assert "store/link" in sizes
    outside.unlink()
    outside.parent.rmdir()


def test_the_report_names_the_largest_files_by_relative_path():
    files = [(i * conftest.MIB, f"t0/f{i}") for i in range(1, 8)]
    text = conftest.disk_report(sum(s for s, _ in files), files, 10 * conftest.MIB, "This test's temporary folders")
    assert "28.0 MB" in text and "10.0 MB" in text and "NTFS" in text and "temp_budget" in text
    shown = [line.split()[-1] for line in text.splitlines()[1:]]
    assert shown == ["t0/f7", "t0/f6", "t0/f5", "t0/f4", "t0/f3"]


def test_the_budgets_are_the_documented_ones():
    """512 MiB per test (the largest legitimate tests, Qdrant Edge stores, end at about 375 MB apparent), 1 GiB for
    what the run keeps (docs/DESIGN.md, Tests, CI and the Windows hazards)."""
    assert conftest.TEST_TEMP_BUDGET == 512 * conftest.MIB
    assert conftest.RUN_TEMP_BUDGET == 1024 * conftest.MIB


def test_leftovers_are_this_process_entries_outside_the_base_folder(tmp_path):
    base = tmp_path / "pytest-7"
    base.mkdir()
    mine = tmp_path / "tmpabc"
    mine.mkdir()
    gone = tmp_path / "tmpgone"
    roots = conftest.run_leftovers(base, created={str(mine), str(gone), str(base / "x"), str(tmp_path)})
    assert roots == [base, mine]   # gone ones, ones inside the base folder and ones that hold it do not count


def _inner(tmp_path: Path, source: str, ini: str = "") -> subprocess.CompletedProcess:
    """Runs `source` as a test file with the real conftest as a plugin, its own system temporary folder and the
    lowered budgets of `ini`."""
    project = tmp_path / "inner"
    project.mkdir()
    (project / "pytest.ini").write_text(
        "[pytest]\ntmp_path_retention_policy = failed\n"
        "markers =\n    temp_budget(megabytes, reason): guard test\n" + ini, encoding="utf-8")
    (project / "test_inner.py").write_text(source, encoding="utf-8")
    systemp = tmp_path / "systemp"
    systemp.mkdir()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(TESTS), str(REPO), env.get("PYTHONPATH", "")) if p)
    for name in ("TMPDIR", "TEMP", "TMP"):
        env[name] = str(systemp)
    env.pop("PYTEST_DEBUG_TEMPROOT", None)
    env.pop("LCF_TEST_DISK_LOG", None)
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
                           "-c", str(project / "pytest.ini"), "--rootdir", str(project),
                           "--basetemp", str(tmp_path / "bt"), str(project / "test_inner.py")],
                          cwd=project, env=env, capture_output=True, text=True, timeout=300,
                          stdin=subprocess.DEVNULL)


INNER_TESTS = '''
import pytest

def sparse(path, size):
    with open(path, "wb") as f:
        f.truncate(size)

@pytest.mark.temp_budget(1, reason="guard test")
def test_too_big(tmp_path):
    sparse(tmp_path / "big.bin", 3 * 1024 * 1024)

@pytest.mark.temp_budget(1, reason="guard test")
def test_removed_in_time(tmp_path):
    sparse(tmp_path / "big.bin", 3 * 1024 * 1024)
    (tmp_path / "big.bin").unlink()

@pytest.mark.temp_budget(1, reason="guard test")
def test_factory_folders_count(tmp_path_factory):
    sparse(tmp_path_factory.mktemp("extra") / "wal.bin", 2 * 1024 * 1024)

@pytest.mark.temp_budget(1)
def test_a_marker_needs_a_reason(tmp_path):
    pass

def test_lowered_default(tmp_path):
    sparse(tmp_path / "mid.bin", 3 * 1024 * 1024)
'''


def test_a_test_over_its_budget_fails_at_teardown(tmp_path):
    result = _inner(tmp_path, INNER_TESTS, "temp_test_budget_mb = 2\n")
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "ERROR at teardown of test_too_big" in out
    assert "big.bin" in out and "3.0 MB" in out and "1.0 MB" in out
    assert "ERROR at teardown of test_removed_in_time" not in out
    assert "ERROR at teardown of test_factory_folders_count" in out and "wal.bin" in out
    assert "ERROR at teardown of test_a_marker_needs_a_reason" in out and "needs the megabytes and a reason" in out
    assert "ERROR at teardown of test_lowered_default" in out and "mid.bin" in out
    assert "5 passed, 4 errors" in out, out


SESSION_TESTS = '''
import os, tempfile

def test_leaves_a_temp_folder():
    folder = tempfile.mkdtemp(prefix="leftover-")
    with open(os.path.join(folder, "wal.bin"), "wb") as f:
        f.truncate(3 * 1024 * 1024)
'''


def test_what_the_run_keeps_over_its_budget_fails_the_session(tmp_path):
    result = _inner(tmp_path, SESSION_TESTS, "temp_run_budget_mb = 1\n")
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "temporary folders over budget" in out and "1 passed" in out
    assert "wal.bin" in out and "leftover-" in out
    assert str(tmp_path) not in out.split("temporary folders over budget", 1)[1], "a host path in the report"


def test_a_run_that_cleans_up_passes(tmp_path):
    source = SESSION_TESTS + "    import shutil\n    shutil.rmtree(folder)\n"
    result = _inner(tmp_path, source, "temp_run_budget_mb = 1\n")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("value, expected", [("", conftest.RUN_TEMP_BUDGET), ("1", conftest.MIB),
                                             ("100000", conftest.RUN_TEMP_BUDGET)])
def test_the_ini_can_only_lower_a_budget(value, expected):
    class Config:
        @staticmethod
        def getini(name):
            return value
    assert conftest._ini_budget(Config, "temp_run_budget_mb", conftest.RUN_TEMP_BUDGET) == expected
