"""What a Windows host does differently, checked on every host (the first Windows CI run, 3.0.0 had these too).

Each test fails without its fix on Linux as well: Windows paths are given as text or PureWindowsPath, and Windows'
file semantics (a file another reader holds open cannot be replaced; an open file cannot be deleted, and
shutil.rmtree(ignore_errors=True) then leaves it silently) are simulated.
"""

import asyncio
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from orchestrator.tools.tools_runtime import label_paths
from test_rag import ingest_all, make_rag  # noqa: F401  (make_rag is a fixture)

KEY = b"k" * 32


# --- AI-facing text: the home folder and known folders, in either system's spelling ---------------------------------

class TestLabelPathsOnAnyHost:

    def test_the_home_folder_from_userprofile_is_recognised(self, monkeypatch, tmp_path):
        """Path.home() reads USERPROFILE on Windows and HOME elsewhere: both are the home folder on every host."""
        monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
        monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane Doe")
        out = label_paths(r"see C:\Users\Jane Doe and C:\Users\Jane Doe\Desktop\m.gguf", {})
        assert out == "see the home folder and m.gguf"

    def test_a_posix_home_variable_counts_on_a_windows_host_too(self, monkeypatch):
        """A Windows host's HOME may be a POSIX path (Git Bash, MSYS): Path.home() ignores it there, the labeller
        does not, so '/home/Jane Doe' never leaves 'Jane Doe' behind."""
        monkeypatch.setenv("HOME", "/home/Jane Doe")
        monkeypatch.setenv("USERPROFILE", r"C:\Users\runner")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(r"C:\Users\runner")))
        assert label_paths("home is /home/Jane Doe here", {}) == "home is the home folder here"

    def test_the_home_folder_with_the_other_separator_and_in_another_case(self, monkeypatch):
        monkeypatch.setenv("HOME", r"C:\Users\Jane Doe")
        for text, expected in (("in C:/Users/Jane Doe now", "in the home folder now"),
                               (r"open c:\users\jane doe\x.gguf", "open x.gguf"),
                               ("cmake: C:/Users/Jane Doe/src/My Models/b.bin", "cmake: b.bin")):
            out = label_paths(text, {})
            assert out == expected and "Jane" not in out and "jane" not in out, (text, out)

    def test_a_known_folder_as_a_windows_host_spells_it(self):
        """On Windows str(Path('/srv/my out')) is '\\\\srv\\\\my out', while the text names '/srv/my out'; and a
        Windows folder is named with either separator and in any case."""
        roots = {r"\srv\my out": "output", r"C:\Data\My Models": "models"}
        assert label_paths("ok (/srv/my out/Rk/m.gguf)", roots) == "ok (output:Rk/m.gguf)"
        assert label_paths("load c:/data/my models/qwen 2/model.gguf failed", roots) == \
            "load models:qwen 2/model.gguf failed"
        assert label_paths(r"nothing in C:\Data\My Models.", roots) == "nothing in the models folder."

    def test_a_posix_known_folder_keeps_its_case(self):
        """On POSIX two names that differ in case are two folders: only a Windows path is compared without case."""
        if os.name == "nt":
            pytest.skip("every path is compared without case on Windows")
        assert label_paths("in /srv/Out/x.gguf", {"/srv/out": "output"}) == "in x.gguf"

    def test_a_windows_error_text_with_doubled_backslashes(self, monkeypatch):
        """Python quotes a Windows path in an error text by its repr, every '\\' doubled: the home folder in it is
        still the home folder, a known folder still gets its label."""
        monkeypatch.setenv("HOME", r"C:\Users\Jane Doe")
        error = str(PermissionError(13, "Access is denied", r"C:\Users\Jane Doe"))
        assert "\\\\" in error  # the repr form
        assert label_paths(error, {}) == "[Errno 13] Access is denied: 'the home folder'"
        missing = str(FileNotFoundError(2, "No such file", r"C:\Data\My Models\qwen\m.gguf"))
        assert label_paths(missing, {r"C:\Data\My Models": "models"}) == \
            "[Errno 2] No such file: 'models:qwen/m.gguf'"
        assert label_paths(r"cannot open C:\\Users\\Jane Doe here", {}) == "cannot open the home folder here"
        assert label_paths(r"share '\\\\srv\\home$\\jdoe\\m.gguf'", {}) == "share 'm.gguf'"

    def test_a_windows_share_is_a_path(self):
        assert label_paths(r"cannot read \\fileserver\home$\jdoe\models\x.gguf now", {}) == "cannot read x.gguf now"
        assert label_paths(r"No such file: '\\fileserver\home$\jdoe\My Models\x.gguf'", {}) == \
            "No such file: 'x.gguf'"

    def test_a_root_or_relative_home_variable_is_no_home_folder(self, monkeypatch):
        from orchestrator.tools.tools_runtime import home_folders
        monkeypatch.setenv("HOME", "/")
        monkeypatch.setenv("USERPROFILE", "C:\\")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: PurePosixPath("relative/home")))
        assert home_folders() == []


# --- model names never show a host folder, whatever system wrote the path -------------------------------------------

class TestModelNames:

    def test_model_label_of_a_windows_path_on_any_host(self):
        from orchestrator.Core.orchestrator import model_label
        assert model_label(r"C:\Users\someone\models\qwen\model.gguf") == "model.gguf"
        assert model_label(r"\\server\share\someone\model.gguf") == "model.gguf"
        assert model_label("/home/someone/models/qwen/model.gguf") == "model.gguf"
        assert model_label("HuggingFaceTB/SmolLM2-135M-Instruct") == "HuggingFaceTB/SmolLM2-135M-Instruct"

    def test_a_listed_artifacts_model_of_either_system_is_its_file_name(self):
        from orchestrator.Core.build_service import BuildService
        service = BuildService(SimpleNamespace())  # no folders the AI may use: no label
        assert service._shown_model(r"C:\Users\someone\private\model.gguf") == "model.gguf"
        assert service._shown_model("/elsewhere/private/model.gguf") == "model.gguf"
        assert service._shown_model("org/model") == "org/model"

    def test_a_local_model_given_as_a_windows_path_builds_into_the_output_folder(self, tmp_path):
        """The output folder took the model's part after the last '/': for 'C:\\\\...\\\\SmolLM2' that is the whole
        path, and pathlib then put the build into the model's own folder instead of the output folder."""
        from orchestrator.Core.builder import ModelFormat, OptimizationLevel
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.orchestrator import (BuildRequest, LLMOrchestrator, OrchestrationStatus, PriorityLevel,
                                                    WorkflowState, WorkflowType)
        from test_pipeline import FakeBuildEngine
        cfg = ConfigManager(tmp_path / "configs")
        (tmp_path / "targets" / "Rockchip").mkdir(parents=True)
        cfg.set("targets_dir", str(tmp_path / "targets"))
        orch = LLMOrchestrator(cfg)
        orch.build_engine = FakeBuildEngine()
        req = BuildRequest(request_id="r1", workflow_type=WorkflowType.SIMPLE_CONVERSION, priority=PriorityLevel.NORMAL,
                           models=[r"C:\Users\someone\models\SmolLM2"], targets=["Rockchip"],
                           target_formats=[ModelFormat.RKLLM], optimization_level=OptimizationLevel.BALANCED,
                           quantization_options=["w4a16"], parallel_builds=False,
                           output_base_dir=str(tmp_path / "out"), target_board="rk3588")
        orch._workflows["r1"] = WorkflowState("r1", OrchestrationStatus.PREPARING, datetime.now())
        asyncio.run(orch._run_build_pipeline(req))
        config = orch.build_engine.configs[0]
        assert config.output_dir == str(tmp_path / "out" / "Rockchip" / "SmolLM2" / "w4a16")


# --- the client snippet for a POSIX client, made on any host --------------------------------------------------------

def test_the_posix_snippet_follows_posix_rules_on_a_windows_host(monkeypatch):
    """On Windows Path('/opt/LLM Framework') is '\\\\opt\\\\LLM Framework', which POSIX does not take as absolute."""
    pytest.importorskip("mcp")
    import shlex
    import sys

    from orchestrator import mcp_server
    monkeypatch.setattr(sys, "executable", "/opt/py/bin/python3")
    launch = mcp_server.launch_command(PureWindowsPath("/opt/LLM Framework"), windows=False)
    assert launch["cwd"] == "/opt/LLM Framework" and launch["env"] == {"PYTHONPATH": "/opt/LLM Framework"}
    command = mcp_server.client_snippets(PureWindowsPath("/opt/LLM Framework"), windows=False)["claude-code"]
    assert "PYTHONPATH=/opt/LLM Framework" in shlex.split(command)
    with pytest.raises(ValueError, match="cannot be put on PYTHONPATH"):
        mcp_server.launch_command(PureWindowsPath(r"C:\LLM"), windows=False)  # a drive is no POSIX path


# --- Windows: replacing a file another reader holds open fails for a moment -----------------------------------------

def test_an_approval_is_stored_while_a_reader_holds_the_file(tmp_path, monkeypatch):
    """The approval watcher (and every other window) reads tool_approvals.json without the lock; on Windows
    os.replace onto a file that is open for reading fails with 'Access is denied'. The store tries again."""
    from orchestrator.Core import vault_key
    from orchestrator.tools.approvals import ApprovalRecord, ApprovalStore
    now = datetime.now(timezone.utc)
    record = ApprovalRecord(id="ap_0123456789abcdef", caller="mcp", client="", tool="start_build", title="Start",
                            args_sha256="0" * 64, question="Start a build", question_sha256="1" * 64,
                            created=now.isoformat(timespec="seconds"),
                            expires=(now + timedelta(minutes=30)).isoformat(timespec="seconds"),
                            state="pending", pid=os.getpid())
    store = ApprovalStore(tmp_path / "approvals", key=KEY)
    store.add(record)
    monkeypatch.setattr(vault_key, "REPLACE_RETRIES", 10)  # what a Windows host uses
    real_replace, refused = os.replace, []

    def busy_once(src, dst):
        if Path(dst) == store.path and not refused:
            refused.append(dst)
            raise PermissionError(13, "Access is denied", str(dst))  # a reader has it open
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", busy_once)
    assert store.update(record.id, question="Start a build of x") is not None
    assert refused and store.get(record.id).question == "Start a build of x"
    assert not list(store.path.parent.glob(".tool_approvals.*.tmp")), "no temporary file is left behind"


# --- Windows: an open file cannot be deleted, and rmtree(ignore_errors=True) says nothing ---------------------------

@pytest.fixture
def windows_deletes(monkeypatch):
    """Embedded Qdrant's collection deletion with Windows' rule: a folder whose storage file is still open stays."""
    from qdrant_client.local import persistence, qdrant_local
    open_files = set()
    real_init, real_close = persistence.CollectionPersistence.__init__, persistence.CollectionPersistence.close

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        open_files.add(Path(self.location))

    def close(self):
        real_close(self)
        open_files.discard(Path(self.location))

    def rmtree(path, ignore_errors=False, **kwargs):
        if any(Path(path) in f.parents for f in open_files):
            if ignore_errors:
                return
            raise PermissionError(13, "The process cannot access the file", str(path))
        shutil.rmtree(path, ignore_errors=ignore_errors, **kwargs)

    monkeypatch.setattr(persistence.CollectionPersistence, "__init__", init)
    monkeypatch.setattr(persistence.CollectionPersistence, "close", close)
    monkeypatch.setattr(qdrant_local, "shutil", SimpleNamespace(rmtree=rmtree))
    return SimpleNamespace(rmtree=rmtree, set=lambda fn: monkeypatch.setattr(qdrant_local, "shutil",
                                                                              SimpleNamespace(rmtree=fn)))


def test_clearing_the_knowledge_base_deletes_the_vectors_on_windows(make_rag, windows_deletes):  # noqa: F811
    rag = make_rag()
    ingest_all(rag)
    count = rag.get_status()["vector_count"]
    assert count > 0
    name = rag.create_snapshot("before_clear")
    assert rag.clear_knowledge_base() and rag.get_status()["vector_count"] == 0
    assert rag.restore_snapshot(name) and rag.get_status()["vector_count"] == count


def test_vectors_that_cannot_be_deleted_are_reported(make_rag, windows_deletes):  # noqa: F811
    rag = make_rag()
    ingest_all(rag)
    windows_deletes.set(lambda path, ignore_errors=False, **kw: None)  # e.g. another program holds a file
    assert rag.clear_knowledge_base() is False
    assert "could not be deleted" in (rag.last_error or "")


# --- review: another account's home folder, and the status scrubber, on any host ------------------------------------

class TestAnotherAccountsHomeFolder:
    """A path is shown by its last part, and for a bare home folder that part is an account name: another person on
    this computer ('/home/bob', 'C:\\\\Users\\\\Bob'), or a board's account the framework does not log in with (a
    POSIX path in text a Windows host reads). Such a folder is 'a home folder'; a file in it keeps its name."""

    @pytest.mark.parametrize("text, expected", [
        ("Permission denied: /home/bob", "Permission denied: a home folder"),
        ("cannot open '/home/bob'", "cannot open 'a home folder'"),
        (r"access denied: C:\Users\Bob", "access denied: a home folder"),
        ("no space in /Users/alice/.", "no space in a home folder."),
        ("[WinError 5] Access is denied: " + repr(r"C:\Users\Bob Smith"),
         "[WinError 5] Access is denied: 'a home folder'"),
        ("see c:/users/bob and /c/Users/bob", "see a home folder and a home folder"),
        ("ls /var/home/pi", "ls a home folder"),
        (r"share \\fs\Users\bob", "share a home folder"),
    ])
    def test_it_is_never_shown_by_its_account_name(self, text, expected, monkeypatch):
        monkeypatch.setenv("HOME", "/home/jane")  # this computer's own home folder is another one
        monkeypatch.setenv("USERPROFILE", "/home/jane")
        assert label_paths(text, {}) == expected

    def test_a_file_in_it_keeps_its_name_and_other_folders_their_last_part(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/jane")
        assert label_paths("cannot read /home/bob/x.gguf", {}) == "cannot read x.gguf"
        assert label_paths("cannot read /srv/home", {}) == "cannot read home"
        assert label_paths("cannot read /data/users/list", {}) == "cannot read list"

    def test_a_board_account_the_framework_does_not_log_in_with(self):
        from orchestrator.tools.tools_runtime import device_text
        out = device_text("no space in /home/dietpi and /home/other", ["dietpi"])
        assert out == "no space in ~ and a home folder" and "other" not in out

    def test_a_calibration_file_setting_of_either_system_is_shown_by_its_name(self):
        """A settings file written on the other system: on Linux Path(r'C:\\\\Users\\\\...').name is the whole text."""
        from orchestrator.tools.tools_status import setting_value
        for value in (r"C:\Users\Jane Doe\data\calib.txt", "/home/jane/data/calib.txt"):
            framework = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None, v=value: v))
            assert setting_value(framework, "default_calibration_dataset") == "datasets:calib.txt", value


class TestStatusTextsWithoutTheHomeFolder:
    """The runtime status and the deploy tools shorten the home folder to '~' (_scrub_paths) after their own labels:
    on a Windows host a text names it with '/' (CMake, git), in another case, or doubled as Python quotes a path."""

    @pytest.mark.parametrize("text", [
        "build failed: " + repr(r"C:\Users\Jane Doe\llmcf\x.bin"),
        "cmake: C:/Users/Jane Doe/src/llama.cpp",
        r"open c:\users\jane doe\x",
    ])
    def test_the_status_scrubber_finds_every_spelling(self, text, monkeypatch):
        from orchestrator.tools.tools_status import _scrub_paths
        monkeypatch.setenv("HOME", r"C:\Users\Jane Doe")
        monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane Doe")
        out = _scrub_paths(text)
        assert "Jane" not in out and "jane" not in out and "~" in out, out

    def test_a_package_error_quoting_the_build_folder(self, monkeypatch):
        from orchestrator.tools.tools_deploy import hide_paths
        monkeypatch.setenv("HOME", r"C:\Users\Jane Doe")
        monkeypatch.setenv("USERPROFILE", r"C:\Users\Jane Doe")
        folder = r"C:\Users\Jane Doe\llmcf\output\Rockchip\b1"
        error = str(PermissionError(13, "Access is denied", folder + r"\package.zip"))
        out = hide_paths(f"Cannot write the package: {error}", {folder: "the build folder"})
        assert out == r"Cannot write the package: [Errno 13] Access is denied: 'the build folder\package.zip'", out
        assert hide_paths("in c:/users/jane doe/llmcf/output/Rockchip/b1/x", {folder: "the build folder"}) == \
            "in the build folder/x"
