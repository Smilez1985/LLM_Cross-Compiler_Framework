"""The pinned llama.cpp source (orchestrator/runtime/build/source.py) against a local fake upstream repository.

No network: the upstream is a git repository in tmp_path, reached through file://.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from orchestrator.runtime.build import source as src
from orchestrator.runtime.build.source import (Git, SourceError, cached_source, check_pin_upstream, export_source,
                                               fetch_source, import_source, manifest_path, source_dir,
                                               upstream_tag_commit)
from orchestrator.runtime.catalog import LlamaCppSource

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
            "-c", "tag.gpgsign=false", "-c", "init.defaultBranch=main"]


def run_git(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "TEMP", "TMP")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, HOME=str(cwd or "."))
    r = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """A fake llama.cpp upstream: two commits, the lightweight tag b42 and the annotated tag v1.0.0 on the second."""
    up = tmp_path / "upstream"
    up.mkdir()
    run_git("init", "-q", cwd=up)
    (up / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.14)\nproject(fake C)\n")
    run_git("add", "CMakeLists.txt", cwd=up)
    run_git("commit", "-q", "-m", "one", cwd=up)
    first = run_git("rev-parse", "HEAD", cwd=up)
    (up / "ggml").mkdir()
    (up / "ggml" / "ggml.c").write_text("int ggml(void) { return 42; }\n")
    (up / "run.sh").write_text("#!/bin/sh\necho fake\n")
    os.chmod(up / "run.sh", 0o755)
    run_git("add", ".", cwd=up)
    run_git("commit", "-q", "-m", "two", cwd=up)
    second = run_git("rev-parse", "HEAD", cwd=up)
    run_git("tag", "b42", cwd=up)
    run_git("tag", "-a", "-m", "release", "v1.0.0", cwd=up)
    return {"path": up, "url": up.as_uri(), "first": first, "second": second}


def pin(commit: str, tag: str = "b42") -> LlamaCppSource:
    return LlamaCppSource(repo="https://example.invalid/fake/llama.cpp.git", commit=commit, tag=tag,
                          build_number=int(tag[1:]))


class Recorder:
    """subprocess.run that remembers every git call (to prove that a run used no fetch)."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return subprocess.run(argv, **kwargs)

    def commands(self):
        out = []
        for argv, _kw in self.calls:
            rest = argv[1:]
            while rest and rest[0] == "-c":
                rest = rest[2:]
            out.append(rest[0] if rest else "")
        return out


def writable_bits(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode) & 0o222


class TestFetch:

    def test_fetches_the_pinned_commit_once_then_uses_the_cache_without_network(self, upstream, tmp_path):
        state = tmp_path / "state"
        rec = Recorder()
        source = pin(upstream["second"])
        path = fetch_source(source, state, url=upstream["url"], git=Git(rec))
        assert path == source_dir(state, upstream["second"])
        assert (path / "ggml" / "ggml.c").read_text() == "int ggml(void) { return 42; }\n"
        assert "fetch" in rec.commands()
        assert not list(path.parent.glob(".staging-*")), "no staging folder is left behind"
        if os.name == "posix":
            assert writable_bits(path / "ggml" / "ggml.c") == 0 and writable_bits(path / "ggml") == 0
            assert stat.S_IMODE((path / "run.sh").stat().st_mode) & 0o111, "executable bits are kept"
        # Second run: the upstream is gone and nothing is fetched. (git's object files are read-only, which Windows
        # refuses to delete: remove_tree clears that, as for the source cache. It ignores what it cannot remove, so
        # the test checks that the upstream is really gone.)
        src.remove_tree(upstream["path"])
        assert not upstream["path"].exists()
        rec2 = Recorder()
        assert fetch_source(source, state, url=upstream["url"], git=Git(rec2)) == path
        assert "fetch" not in rec2.commands() and "ls-remote" not in rec2.commands()
        assert cached_source(source, state) == path

    def test_a_commit_the_tag_does_not_point_at_is_refused(self, upstream, tmp_path):
        state = tmp_path / "state"
        rec = Recorder()
        wrong = pin("1" * 40)  # not upstream: the fetch by commit fails, the tag gives another commit
        with pytest.raises(SourceError, match="instead of the pinned 111111111111; refused"):
            fetch_source(wrong, state, url=upstream["url"], git=Git(rec))
        assert rec.commands().count("fetch") == 2, "fetch by commit, then the tag as the fallback"
        assert not source_dir(state, "1" * 40).exists()
        assert not list(src.runtime_source_root(state).glob(".staging-*"))

    def test_the_first_commit_is_fetched_by_commit_even_though_the_tag_is_newer(self, upstream, tmp_path):
        path = fetch_source(pin(upstream["first"]), tmp_path / "state", url=upstream["url"])
        assert not (path / "ggml").exists() and (path / "CMakeLists.txt").is_file()

    def test_a_damaged_cache_is_fetched_again(self, upstream, tmp_path):
        state = tmp_path / "state"
        source = pin(upstream["second"])
        path = fetch_source(source, state, url=upstream["url"])
        victim = path / "ggml" / "ggml.c"
        os.chmod(victim.parent, 0o755)
        os.chmod(victim, 0o644)
        victim.write_text("int ggml(void) { return 0; }\n")
        assert cached_source(source, state) is None
        messages = []
        again = fetch_source(source, state, url=upstream["url"], log=messages.append)
        assert again == path and victim.read_text() == "int ggml(void) { return 42; }\n"
        assert any("damaged" in m for m in messages)

    def test_an_added_file_counts_as_damage(self, upstream, tmp_path):
        state = tmp_path / "state"
        source = pin(upstream["second"])
        path = fetch_source(source, state, url=upstream["url"])
        os.chmod(path, 0o755)
        (path / "planted.cmake").write_text("message(planted)\n")
        assert cached_source(source, state) is None

    def test_an_added_ignored_file_counts_as_damage(self, upstream, tmp_path):
        # llama.cpp ignores tools/ui/dist/, and its CMake embeds a dist/index.html found there into llama-server
        # (scripts/ui-assets.cmake, priority 1): a file git ignores still changes the build.
        up = upstream["path"]
        (up / ".gitignore").write_text("dist\n")
        run_git("add", ".gitignore", cwd=up)
        run_git("commit", "-q", "-m", "ignore dist", cwd=up)
        source = pin(run_git("rev-parse", "HEAD", cwd=up))
        state = tmp_path / "state"
        path = fetch_source(source, state, url=upstream["url"])
        assert cached_source(source, state) == path
        os.chmod(path, 0o755)
        (path / "dist").mkdir()
        (path / "dist" / "index.html").write_text("<script>planted</script>\n")
        assert cached_source(source, state) is None

    def test_a_relative_mirror_path_is_relative_to_the_user(self, upstream, tmp_path, monkeypatch):
        # git fetches inside a staging folder; `--mirror ../llama.cpp` means the folder next to the user's.
        monkeypatch.chdir(upstream["path"].parent)
        path = fetch_source(pin(upstream["second"]), tmp_path / "state", url="upstream")
        assert (path / "ggml" / "ggml.c").is_file()

    def test_repository_url_cannot_be_an_option(self, tmp_path):
        with pytest.raises(SourceError, match="must not start"):
            fetch_source(pin("2" * 40), tmp_path / "state", url="--upload-pack=touch x")

    def test_git_runs_with_a_cleared_environment_and_locked_down_config(self, upstream, tmp_path, monkeypatch):
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
        monkeypatch.setenv("GIT_SSH_COMMAND", "touch pwned")
        monkeypatch.setenv("SOME_TOKEN", "secret-value")
        rec = Recorder()
        fetch_source(pin(upstream["second"]), tmp_path / "state", url=upstream["url"], git=Git(rec))
        for argv, kwargs in rec.calls:
            env = kwargs["env"]
            assert "GIT_DIR" not in env and "GIT_SSH_COMMAND" not in env and "SOME_TOKEN" not in env
            assert "HOME" not in env and env["GIT_CONFIG_NOSYSTEM"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"
            assert "protocol.allow=never" in argv and f"core.hooksPath={os.devnull}" in argv
            assert "credential.helper=" in argv and kwargs["stdin"] == subprocess.DEVNULL
            assert isinstance(argv, list) and "shell" not in kwargs

    def test_only_https_is_allowed_for_remote_repositories(self, tmp_path):
        rec = Recorder()
        with pytest.raises(SourceError):
            fetch_source(pin("3" * 40), tmp_path / "state", url="ssh://example.invalid/llama.cpp.git", git=Git(rec))
        fetch = [argv for argv, _kw in rec.calls if "fetch" in argv]
        assert fetch and all("protocol.file.allow=never" in argv for argv in fetch)

    def test_missing_git_is_reported(self, tmp_path):
        git = Git(git="")
        git.git = None
        with pytest.raises(SourceError, match="git was not found"):
            fetch_source(pin("4" * 40), tmp_path / "state", url="https://example.invalid/x.git", git=git)


class TestBundle:

    def test_round_trip_to_an_air_gapped_machine(self, upstream, tmp_path):
        source = pin(upstream["second"])
        bundle = tmp_path / "carry" / "llama.cpp-b42.bundle"
        manifest = export_source(source, tmp_path / "online", bundle, url=upstream["url"])
        assert bundle.is_file() and manifest_path(bundle).is_file()
        written = json.loads(manifest_path(bundle).read_text())
        assert written == manifest
        assert manifest["commit"] == upstream["second"] and manifest["tag"] == "b42"
        assert manifest["bundle_size"] == bundle.stat().st_size and len(manifest["bundle_sha256"]) == 64
        assert "framework_version" in manifest
        src.remove_tree(upstream["path"])  # the air-gapped machine has no upstream at all (read-only git objects)
        assert not upstream["path"].exists()
        rec = Recorder()
        path = import_source(source, tmp_path / "offline", bundle, git=Git(rec))
        assert (path / "ggml" / "ggml.c").read_text() == "int ggml(void) { return 42; }\n"
        assert cached_source(source, tmp_path / "offline") == path
        fetches = [argv for argv, _kw in rec.calls if "fetch" in argv]
        assert fetches and all(str(bundle.resolve()) in argv for argv in fetches), "only the bundle is read"
        assert "ls-remote" not in rec.commands()
        rec2 = Recorder()
        assert import_source(source, tmp_path / "offline", bundle, git=Git(rec2)) == path
        assert "fetch" not in rec2.commands(), "an imported source is not imported twice"

    def test_a_relative_bundle_path_is_written_where_the_user_is(self, upstream, tmp_path, monkeypatch):
        # git runs inside the source cache; a relative output path must not end up there (the documented example
        # `export-source llama.cpp-source.bundle` is relative).
        source = pin(upstream["second"])
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        manifest = export_source(source, tmp_path / "online", Path("llama.bundle"), url=upstream["url"])
        assert (work / "llama.bundle").is_file() and (work / "llama.bundle.json").is_file()
        assert manifest["bundle_size"] == (work / "llama.bundle").stat().st_size
        cache = source_dir(tmp_path / "online", upstream["second"])
        assert cached_source(source, tmp_path / "online") == cache, "nothing was written into the source cache"
        assert sorted(p.name for p in cache.iterdir()) == [".git", "CMakeLists.txt", "ggml", "run.sh"]
        path = import_source(source, tmp_path / "offline", Path("llama.bundle"))
        assert (path / "ggml" / "ggml.c").is_file()

    def test_a_bundle_of_another_commit_is_refused(self, upstream, tmp_path):
        bundle = tmp_path / "b.bundle"
        export_source(pin(upstream["second"]), tmp_path / "online", bundle, url=upstream["url"])
        with pytest.raises(SourceError, match="same framework version"):
            import_source(pin(upstream["first"]), tmp_path / "offline", bundle)
        manifest_path(bundle).unlink()  # without the manifest, git and the commit check refuse it
        with pytest.raises(SourceError, match="does not contain the pinned commit"):
            import_source(pin(upstream["first"]), tmp_path / "offline", bundle)
        assert not source_dir(tmp_path / "offline", upstream["first"]).exists()

    def test_a_damaged_bundle_is_refused(self, upstream, tmp_path):
        bundle = tmp_path / "b.bundle"
        export_source(pin(upstream["second"]), tmp_path / "online", bundle, url=upstream["url"])
        data = bytearray(bundle.read_bytes())
        data[-30] ^= 0xFF
        bundle.write_bytes(bytes(data))
        with pytest.raises(SourceError, match="does not match its manifest"):
            import_source(pin(upstream["second"]), tmp_path / "offline", bundle)
        manifest_path(bundle).unlink()
        with pytest.raises(SourceError):
            import_source(pin(upstream["second"]), tmp_path / "offline", bundle)
        assert cached_source(pin(upstream["second"]), tmp_path / "offline") is None

    @pytest.mark.parametrize("content", ["not json", json.dumps({"format": 99}), json.dumps(
        {"format": 1, "commit": "nope", "bundle_sha256": "0" * 64, "bundle_size": 1}), "[1, 2]"])
    def test_an_unusable_manifest_is_refused(self, upstream, tmp_path, content):
        bundle = tmp_path / "b.bundle"
        export_source(pin(upstream["second"]), tmp_path / "online", bundle, url=upstream["url"])
        manifest_path(bundle).write_text(content)
        with pytest.raises(SourceError):
            import_source(pin(upstream["second"]), tmp_path / "offline", bundle)

    def test_a_bundle_of_a_full_clone_works_too(self, upstream, tmp_path):
        bundle = tmp_path / "full.bundle"
        run_git("bundle", "create", str(bundle), "HEAD", cwd=upstream["path"])
        path = import_source(pin(upstream["second"]), tmp_path / "offline", bundle)
        assert (path / "ggml" / "ggml.c").is_file()

    def test_empty_and_missing_files_are_refused(self, tmp_path):
        empty = tmp_path / "empty.bundle"
        empty.write_bytes(b"")
        with pytest.raises(SourceError, match="not a source bundle"):
            import_source(pin("5" * 40), tmp_path / "offline", empty)
        with pytest.raises(SourceError, match="cannot read"):
            import_source(pin("5" * 40), tmp_path / "offline", tmp_path / "missing.bundle")


class TestPinBumpCheck:

    def test_ls_remote_gives_the_commit_of_a_lightweight_and_an_annotated_tag(self, upstream):
        assert upstream_tag_commit("b42", repo=upstream["url"]) == upstream["second"]
        assert check_pin_upstream(pin(upstream["second"]), repo=upstream["url"]) == upstream["second"]

    def test_the_configuration_of_the_current_repository_is_not_used(self, upstream, tmp_path, monkeypatch):
        # Run from inside some checkout, git would read that repository's config: url.<x>.insteadOf could send the
        # check to another repository whose tag says what the pin says.
        decoy = tmp_path / "decoy"
        decoy.mkdir()
        run_git("init", "-q", cwd=decoy)
        (decoy / "x").write_text("x\n")
        run_git("add", "x", cwd=decoy)
        run_git("commit", "-q", "-m", "decoy", cwd=decoy)
        run_git("tag", "b42", cwd=decoy)
        here = tmp_path / "checkout"
        here.mkdir()
        run_git("init", "-q", cwd=here)
        run_git("config", f"url.{decoy.as_uri()}.insteadOf", upstream["url"], cwd=here)
        monkeypatch.chdir(here)
        assert upstream_tag_commit("b42", repo=upstream["url"]) == upstream["second"]

    def test_annotated_tags_are_peeled(self, upstream):
        run_git("tag", "-a", "-m", "release", "b43", upstream["first"], cwd=upstream["path"])
        assert upstream_tag_commit("b43", repo=upstream["url"]) == upstream["first"]

    def test_a_pin_that_is_not_upstream_s_tag_fails_the_check(self, upstream):
        with pytest.raises(SourceError, match="points at"):
            check_pin_upstream(pin(upstream["first"]), repo=upstream["url"])
        with pytest.raises(SourceError, match="has no tag b7"):
            check_pin_upstream(pin(upstream["second"], tag="b7"), repo=upstream["url"])
        with pytest.raises(SourceError, match="looks like"):
            upstream_tag_commit("v1.0.0", repo=upstream["url"])


class TestCli:

    def test_export_and_import_source(self, upstream, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        source = pin(upstream["second"])
        monkeypatch.setattr(cli_module, "_pinned_llama_source", lambda: source)
        runner = CliRunner()
        bundle = tmp_path / "llama.bundle"
        result = runner.invoke(cli_module.cli, ["-q", "ditto", "runtime", "export-source", str(bundle),
                                                "--mirror", upstream["url"], "--yes"])
        assert result.exit_code == 0, result.output
        assert bundle.is_file() and "import-source" in result.output
        # The air-gapped machine: another user folder, no upstream (read-only git objects: remove_tree).
        src.remove_tree(upstream["path"])
        assert not upstream["path"].exists()
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "offline-home"))
        result = runner.invoke(cli_module.cli, ["-q", "ditto", "runtime", "import-source", str(bundle)])
        assert result.exit_code == 0, result.output
        assert "Verified llama.cpp b42" in result.output
        assert source_dir(tmp_path / "offline-home", upstream["second"]).is_dir()

    def test_import_of_a_wrong_bundle_fails_with_exit_1(self, upstream, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        bundle = tmp_path / "b.bundle"
        export_source(pin(upstream["second"]), tmp_path / "online", bundle, url=upstream["url"])
        monkeypatch.setattr(cli_module, "_pinned_llama_source", lambda: pin(upstream["first"]))
        result = CliRunner().invoke(cli_module.cli, ["-q", "ditto", "runtime", "import-source", str(bundle)])
        assert result.exit_code == 1 and "pinned to" in result.output

    def test_export_asks_before_fetching(self, upstream, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli_module, "_pinned_llama_source", lambda: pin(upstream["second"]))
        result = CliRunner().invoke(cli_module.cli, ["-q", "ditto", "runtime", "export-source",
                                                     str(tmp_path / "x.bundle"), "--mirror", upstream["url"]],
                                    input="n\n")
        assert result.exit_code == 0 and "Fetch it now?" in result.output
        assert not (tmp_path / "x.bundle").exists()
