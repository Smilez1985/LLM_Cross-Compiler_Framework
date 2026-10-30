#!/usr/bin/env python3
"""The dependency cache of module CMake options (DECISIONS round 25 Q6 addendum; orchestrator/runtime/build/deps.py):
fetched before the build, pinned as llama.cpp declares it, the sha256 recorded on the first fetch and checked after,
offline only from the cache, mounted read-only into the build with FETCHCONTENT_SOURCE_DIR_<NAME> and
FETCHCONTENT_FULLY_DISCONNECTED=ON, listed and deleted only through Core/deletion.py. No network: a local git
repository and an http server on this computer."""

import hashlib
import http.server
import io
import os
import shutil
import subprocess
import tarfile
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT, posix_shell
from orchestrator.runtime.build import deps, recipe
from orchestrator.runtime.build.deps import (DEPENDENCIES, Dependency, DependencyCache, DependencyError,
                                             DependencyMissing, Fetcher)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
needs_bash = pytest.mark.skipif(os.name == "nt" or posix_shell("bash") is None, reason="needs bash")


def _tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(f"pkg-1.0/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _Server:
    """An http server on 127.0.0.1 that serves `self.body` (changeable between requests)."""

    def __init__(self, body: bytes):
        self.body = body
        self.requests = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.requests += 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/pkg-1.0.tar.gz"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def archive_server():
    server = _Server(_tar({"CMakeLists.txt": b"project(pkg)\n", "src/a.c": b"int a;\n"}))
    yield server
    server.close()


def _archive_dep(url, body, algo="md5"):
    return Dependency("LIBRESSL", "LLAMA_BUILD_LIBRESSL", "archive", url, "4.3.2", "test", "LibreSSL (test copy)",
                      declared_hash=f"{algo}:{hashlib.new(algo, body).hexdigest()}" if algo else "",
                      size_estimate=1024)


def _cache(tmp_path, **kw):
    return DependencyCache(tmp_path / "cache", fetcher=Fetcher(allow_local=True), **kw)


def _git_repo(tmp_path, tag="0.20260929.0", text="first"):
    from orchestrator.runtime.build.source import Git
    repo = tmp_path / "upstream"
    repo.mkdir(exist_ok=True)
    git = Git()
    if not (repo / ".git").exists():
        git(["init", "-q", str(repo)])
    (repo / "CMakeLists.txt").write_text(f"project(boringssl) # {text}\n", encoding="utf-8")
    ident = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
             "-c", "tag.gpgsign=false"]
    git(["-C", str(repo), "add", "-A"])
    git(["-C", str(repo), *ident, "commit", "-q", "-m", text])
    git(["-C", str(repo), *ident, "tag", "-f", tag])
    return repo


def _git_dep(repo):
    return replace(DEPENDENCIES["BORINGSSL"], source=repo.as_uri())


# --- the list -----------------------------------------------------------------------------------------------------

def test_every_dependency_option_of_the_recipe_has_its_pinned_dependency():
    assert {d.option: name for name, d in DEPENDENCIES.items()} == dict(recipe.DEPENDENCY_OF_OPTION)
    for dep in DEPENDENCIES.values():
        assert dep.source.startswith("https://") and dep.ref and dep.declared
        assert dep.mount.startswith(deps.MOUNT_ROOT + "/")
    assert DEPENDENCIES["KLEIDIAI"].declared_hash == "md5:2f02ebe29573d45813e671eb304f2a00"


def test_needed_for_names_what_the_flags_switch_on():
    assert deps.needed_for(["-DGGML_NATIVE=OFF"]) == []
    assert deps.needed_for(["-DLLAMA_BUILD_BORINGSSL=ON", "-D", "GGML_CPU_KLEIDIAI=ON",
                            "-DLLAMA_BUILD_BORINGSSL=ON"]) == [DEPENDENCIES["BORINGSSL"], DEPENDENCIES["KLEIDIAI"]]
    assert deps.needed_for(["-DLLAMA_BUILD_LIBRESSL=OFF"]) == []


# --- archive: declared hash, the recorded sha256, offline ------------------------------------------------------------

def test_an_archive_is_fetched_checked_recorded_and_read_only(tmp_path, archive_server):
    cache = _cache(tmp_path)
    dep = _archive_dep(archive_server.url, archive_server.body)
    lines = []
    entry = cache.ensure(dep, offline=False, owner="test", progress=lines.append)
    assert (entry.path / "CMakeLists.txt").read_bytes() == b"project(pkg)\n"
    assert entry.sha256 == deps.tree_sha256(entry.path) and entry.size_bytes > 0
    assert lines and dep.source in lines[0]
    if os.name != "nt":   # read-only (the mode bits: root may write anyway)
        assert not os.stat(entry.path).st_mode & 0o222
        assert not os.stat(entry.path / "CMakeLists.txt").st_mode & 0o222
    # a second use comes from the cache, checked, without a download
    assert cache.ensure(dep, offline=True).sha256 == entry.sha256
    assert archive_server.requests == 1
    pins = (cache.root / deps.PINS_FILE).read_text(encoding="utf-8")
    assert str(tmp_path) not in pins     # no host path in what the cache writes


def test_an_archive_without_the_declared_hash_is_not_kept(tmp_path, archive_server):
    cache = _cache(tmp_path)
    dep = _archive_dep(archive_server.url, b"something else")
    with pytest.raises(DependencyError, match="MD5 the pinned llama.cpp declares"):
        cache.ensure(dep, offline=False)
    assert cache.entry(dep) is None
    assert [p.name for p in cache.root.iterdir()] == []      # nothing left behind


def test_a_source_that_changed_under_the_same_pin_is_refused(tmp_path, archive_server):
    cache = _cache(tmp_path)
    dep = _archive_dep(archive_server.url, archive_server.body, algo="")
    first = cache.ensure(dep, offline=False)
    # the person deletes the entry; the pin's recorded sha256 stays
    from orchestrator.runtime.build.source import remove_tree
    remove_tree(first.path.parent)
    archive_server.body = _tar({"CMakeLists.txt": b"project(other)\n"})
    with pytest.raises(DependencyError, match="recorded on the first fetch"):
        cache.ensure(dep, offline=False)
    assert cache.entry(dep) is None


def test_a_changed_cache_entry_is_not_used(tmp_path, archive_server):
    cache = _cache(tmp_path)
    dep = _archive_dep(archive_server.url, archive_server.body)
    entry = cache.ensure(dep, offline=False)
    target = entry.path / "src" / "a.c"
    os.chmod(target, 0o600)
    target.write_text("int b;\n")
    with pytest.raises(DependencyError, match="does not match the sha256"):
        cache.ensure(dep, offline=True)


def test_offline_without_the_cache_stops_in_plain_words(tmp_path, archive_server):
    cache = _cache(tmp_path)
    dep = _archive_dep(archive_server.url, archive_server.body)
    with pytest.raises(DependencyMissing) as e:
        cache.ensure(dep, offline=True)
    assert "offline mode is on" in str(e.value) and dep.ref in str(e.value)
    assert str(tmp_path) not in str(e.value)
    assert archive_server.requests == 0


def test_an_archive_with_a_link_is_refused(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("pkg/evil")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(DependencyError, match="link"):
        deps._extract(buf.getvalue(), tmp_path / "out")


def test_only_https_or_a_test_s_local_source(tmp_path):
    with pytest.raises(DependencyError, match="https"):
        Fetcher().git_clone((tmp_path / "x").as_uri(), "v1", tmp_path / "dest")
    with pytest.raises(DependencyError, match="https"):
        Fetcher().git_clone("http://example.com/x.git", "v1", tmp_path / "dest")


def test_the_fetch_takes_a_disk_reservation_and_releases_it(tmp_path, archive_server):
    calls = []

    class Ledger:
        def reserve(self, needs, owner, label, disk_usage=None):
            calls.append(("reserve", needs[0].bytes, owner))
            return "rid-1", []

        def release(self, rid):
            calls.append(("release", rid))
            return True

    cache = _cache(tmp_path, ledger=Ledger())
    cache.ensure(_archive_dep(archive_server.url, archive_server.body), offline=False, owner="build b1")
    assert calls == [("reserve", 1024, "build b1"), ("release", "rid-1")]


# --- git -------------------------------------------------------------------------------------------------------------

@needs_git
def test_a_git_dependency_is_cloned_at_its_tag_and_checked(tmp_path):
    repo = _git_repo(tmp_path)
    cache = _cache(tmp_path)
    dep = _git_dep(repo)
    entry = cache.ensure(dep, offline=False)
    assert "first" in (entry.path / "CMakeLists.txt").read_text()
    assert not (entry.path / ".git").exists()
    assert len(entry.commit) == 40
    # the tag moved to other content: the next fetch of the same pin is refused
    from orchestrator.runtime.build.source import remove_tree
    remove_tree(entry.path.parent)
    _git_repo(tmp_path, text="second")
    with pytest.raises(DependencyError, match="recorded on the first fetch"):
        cache.ensure(dep, offline=False)


# --- for the build -----------------------------------------------------------------------------------------------

def test_mounts_are_read_only_and_named_in_llmcf_deps(tmp_path, archive_server):
    cache = _cache(tmp_path)
    entry = cache.ensure(_archive_dep(archive_server.url, archive_server.body), offline=False)
    assert cache.mounts([entry]) == {str(entry.path): {"bind": f"{deps.MOUNT_ROOT}/libressl", "mode": "ro"}}
    assert cache.environment([entry]) == {"LLMCF_DEPS": f"LIBRESSL={deps.MOUNT_ROOT}/libressl"}
    assert cache.environment([]) == {}
    assert "in the dependency cache" in cache.facts(entry.dependency)
    assert "estimate" in cache.facts(DEPENDENCIES["KLEIDIAI"])


@needs_bash
@pytest.mark.parametrize("target", ["Rockchip", "_template"])
def test_the_cross_build_takes_the_mounted_copy_fully_disconnected(tmp_path, target):
    """cross_compile_target: FETCHCONTENT_SOURCE_DIR_<NAME> for each mounted dependency, FULLY_DISCONNECTED=ON, and
    GGML_CPU_KLEIDIAI no longer forced OFF once KleidiAI is mounted."""
    modules = REPO_ROOT / "targets" / target / "modules"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "args"
    fake = bin_dir / "cmake"
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{record}"\nexit 1\n')
    fake.chmod(0o755)
    mounted = tmp_path / "kleidiai"
    mounted.mkdir()

    def run(llmcf_deps):
        subprocess.run(
            [posix_shell("bash"), "-c", 'source "$1" && cross_compile_target', "_", str(modules / "target_module.sh")],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "MODULES_DIR": str(modules),
                 "SCRATCH_DIR": str(tmp_path / "scratch"), "LLAMA_BASE": str(tmp_path / "src"),
                 "CMAKE_TOOLCHAIN_FILE": str(tmp_path / "tc.cmake"), "CMAKE_FLAGS": "-DGGML_CPU_KLEIDIAI=ON",
                 "LLMCF_DEPS": llmcf_deps})
        return record.read_text().splitlines()

    args = run(f"KLEIDIAI={mounted}")
    assert f"-DFETCHCONTENT_SOURCE_DIR_KLEIDIAI={mounted}" in args
    assert "-DFETCHCONTENT_FULLY_DISCONNECTED=ON" in args
    assert "-DGGML_CPU_KLEIDIAI=OFF" not in args
    args = run(f"KLEIDIAI={tmp_path / 'missing'}")     # not mounted: no source folder, the option forced OFF
    assert not any(a.startswith("-DFETCHCONTENT_SOURCE_DIR_") for a in args)
    assert args.index("-DGGML_CPU_KLEIDIAI=OFF") > args.index("-DGGML_CPU_KLEIDIAI=ON")


def test_the_builder_fetches_before_and_mounts(tmp_path, monkeypatch, archive_server):
    from orchestrator.Core import builder as builder_mod
    dep = _archive_dep(archive_server.url, archive_server.body)
    monkeypatch.setattr(deps, "needed_for", lambda words: [dep] if "-DLLAMA_BUILD_LIBRESSL=ON" in words else [])
    real = DependencyCache.__init__

    def init(self, cache_dir, fetcher=None, ledger=None, disk_usage=None):
        real(self, cache_dir, Fetcher(allow_local=True), ledger, disk_usage)
    monkeypatch.setattr(DependencyCache, "__init__", init)
    folder = tmp_path / "target"
    (folder / "modules").mkdir(parents=True)
    (folder / "modules" / "config_module.sh").write_text("CMAKE_OPTIONS='-DLLAMA_BUILD_LIBRESSL=ON'\n")
    logs = []
    fake = SimpleNamespace(cache_dir=tmp_path / "cache", config=SimpleNamespace(), framework=None,
                           _dependency_ledger=lambda: None)
    vols, env = builder_mod.BuildEngine._build_dependencies(fake, SimpleNamespace(build_id="b1"),
                                                            SimpleNamespace(add_log=logs.append), folder)
    assert list(vols.values()) == [{"bind": f"{deps.MOUNT_ROOT}/libressl", "mode": "ro"}]
    assert env == {"LLMCF_DEPS": f"LIBRESSL={deps.MOUNT_ROOT}/libressl"}
    assert any("read-only" in line for line in logs)
    # without the option nothing is fetched or mounted
    (folder / "modules" / "config_module.sh").write_text("CMAKE_OPTIONS='-DGGML_NATIVE=OFF'\n")
    assert builder_mod.BuildEngine._build_dependencies(fake, SimpleNamespace(build_id="b2"),
                                                       SimpleNamespace(add_log=logs.append), folder) == ({}, {})


# --- deletion ------------------------------------------------------------------------------------------------------

class _Audit:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def test_entries_are_listed_and_deleted_only_through_the_deletion_list(tmp_path, archive_server, monkeypatch):
    from orchestrator.Core.deletion import accept
    cache = _cache(tmp_path)
    dep = _archive_dep(archive_server.url, archive_server.body)
    monkeypatch.setitem(deps.DEPENDENCIES, "LIBRESSL", dep)
    entry = cache.ensure(dep, offline=False)
    plan = cache.deletion_plan()
    assert [i.kind for i in plan.items] == ["build_dependency"]
    assert plan.items[0].speaking_name == "build dependency LIBRESSL 4.3.2"
    with pytest.raises(PermissionError):
        cache.delete(plan, audit=_Audit())               # a plan nobody accepted
    audit = _Audit()
    result = cache.delete(accept(plan, "cli", "permanent"), audit=audit)
    assert result.done == ["build dependency LIBRESSL 4.3.2"] and not entry.path.exists()
    assert audit.rows and cache.deletion_plan() is None
    # the recorded sha256 stays: a later fetch of the same pin must give it back
    assert "LIBRESSL@4.3.2" in (cache.root / deps.PINS_FILE).read_text()


def test_the_trash_and_the_spec_know_a_build_dependency():
    from orchestrator.Core import trash
    from orchestrator.tools.spec import DELETION_KINDS
    assert "build_dependency" in DELETION_KINDS and "build_dependency" in trash.WHAT


# --- CLI ----------------------------------------------------------------------------------------------------------

def test_cli_build_deps_list_shows_every_dependency(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    monkeypatch.setattr(cli, "_dependency_cache", lambda ctx: DependencyCache(tmp_path / "cache"))
    result = CliRunner().invoke(cli.build_deps_list, [], obj=cli.FrameworkContext(), catch_exceptions=False,
                                env={"COLUMNS": "250"})
    assert result.exit_code == 0, result.output
    for name in DEPENDENCIES:
        assert name in result.output


def test_cli_build_deps_fetch_in_offline_mode_downloads_nothing(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    monkeypatch.setattr(cli, "_dependency_cache", lambda ctx: DependencyCache(tmp_path / "cache"))
    monkeypatch.setattr("orchestrator.utils.locality.offline_mode", lambda config=None: True)
    ctx = cli.FrameworkContext()
    ctx.framework_manager = SimpleNamespace(config=None)
    result = CliRunner().invoke(cli.build_deps_fetch, ["kleidiai"], obj=ctx, catch_exceptions=False,
                                env={"COLUMNS": "250"})
    assert result.exit_code == 1 and "offline mode is on" in result.output
    result = CliRunner().invoke(cli.build_deps_fetch, ["nothing"], obj=ctx, catch_exceptions=False)
    assert result.exit_code == 2 and "Unknown dependency" in result.output


# --- GUI ----------------------------------------------------------------------------------------------------------

def test_the_build_dependencies_window_lists_fetches_offline_never_and_deletes_through_the_list(qapp, tmp_path,
                                                                                                monkeypatch):
    from orchestrator.gui import build_deps_window
    cache = DependencyCache(tmp_path / "cache")
    monkeypatch.setattr(DependencyCache, "for_framework", classmethod(lambda cls, fw: cache))
    shown = []
    monkeypatch.setattr(build_deps_window, "plain_box", lambda parent, kind, title, text: shown.append((kind, text)))
    dialog = build_deps_window.BuildDependenciesDialog(SimpleNamespace(config=None))
    assert dialog.table.rowCount() == len(DEPENDENCIES)
    assert dialog.table.item(0, 4).text() == "no"
    assert dialog.delete_entries() is None and shown[-1][0] == "information"
    # Fetch in offline mode: the plain reason, nothing downloaded
    monkeypatch.setattr("orchestrator.utils.locality.offline_mode", lambda config=None: True)
    dialog.table.selectRow(0)
    assert dialog.fetch_selected()
    dialog.worker.wait(10000)
    qapp.processEvents()
    assert shown[-1][0] == "warning" and "offline mode is on" in shown[-1][1]
    assert dialog.worker is None and cache.entries() == []
    dialog.close()
