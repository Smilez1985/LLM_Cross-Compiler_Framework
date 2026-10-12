"""Path confinement for AI callers (design S6, G8): labelled folders the AI may use, denied folders by file identity,
pinned local files, mounts that never expose more than the file or its own folder, and the "folders the AI may use"
list in the CLI and the GUI."""

import os
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core.builder import BuildConfiguration, BuildEngine, BuildProgress, BuildStatus, ModelFormat
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.schemas.build import BuildRequestIn
from orchestrator.tools import local_files
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.local_files import (FolderStore, LocalFileError, LocalFiles, LocalInputs, denied_folders,
                                            denied_reason, mount_plan)
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry



def isolated_home(tmp_path) -> Path:
    """The test's own home folder (conftest points HOME at tmp_path/home for each test), never the real one: the
    tests below create SSH and Hugging Face files there. Read per test, never at import time."""
    path = Path(os.path.expanduser("~"))
    assert tmp_path in path.parents, f"HOME is not isolated for this test: {path}"
    return path


@pytest.fixture
def area(tmp_path):
    """A per-user folder, a models and a datasets folder, and a resolver over them."""
    state = tmp_path / "state"
    models = tmp_path / "data" / "models"
    datasets = tmp_path / "data" / "datasets"
    for folder in (state, models, datasets):
        folder.mkdir(parents=True)
    files = LocalFiles(state, {"models": models, "datasets": datasets})
    return SimpleNamespace(state=state, models=models, datasets=datasets, files=files, tmp=tmp_path)


def write(path: Path, data: bytes = b"weights") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def refused(files: LocalFiles, reference: str) -> LocalFileError:
    with pytest.raises(LocalFileError) as info:
        files.resolve(reference)
    return info.value


class TestReferences:

    @pytest.mark.parametrize("reference", [
        "/etc/passwd", "~/.ssh/id_ed25519", "~", "C:\\Users\\me\\model.gguf", "C:/models/x.gguf",
        "models:../outside.gguf", "models:a/../../outside.gguf", "models:./m.gguf", "models:", "models:/abs.gguf",
        "models:sub\\m.gguf", "models:m\x00.gguf", "nolabel:m.gguf", "Models:m.gguf", "m.gguf", "../m.gguf",
    ])
    def test_paths_and_bad_references_are_refused_with_the_next_step(self, area, reference):
        write(area.models / "m.gguf")
        error = refused(area.files, reference)
        assert error.code == "invalid_value"
        assert "Next:" in str(error) and "models, datasets" in str(error), "the error names the usable labels"

    def test_a_label_reference_resolves_inside_its_folder(self, area):
        model = write(area.models / "qwen" / "model.gguf")
        pin = area.files.resolve("models:qwen/model.gguf")
        assert pin.path == str(model.resolve()) and not pin.is_dir and pin.file_id.startswith("lf_")
        assert area.files.resolve(pin.file_id) == pin, "the id resolves to the same pinned file"

    def test_a_link_out_of_the_folder_is_refused(self, area):
        outside = write(area.tmp / "elsewhere" / "secret.gguf")
        (area.models / "escape.gguf").symlink_to(outside)
        (area.models / "dir").symlink_to(outside.parent, target_is_directory=True)
        for reference in ("models:escape.gguf", "models:dir/secret.gguf"):
            assert "leads outside its folder" in str(refused(area.files, reference))

    def test_an_unknown_id_says_not_to_guess(self, area):
        error = refused(area.files, "lf_0123456789abcdef")
        assert error.code == "unknown_id" and "do not guess ids" in str(error)

    def test_a_moved_file_makes_its_id_stale(self, area):
        model = write(area.models / "m.gguf")
        file_id = area.files.resolve("models:m.gguf").file_id
        model.rename(area.models / "moved.gguf")
        assert refused(area.files, file_id).code == "unknown_id"

    def test_a_replaced_file_is_refused_under_its_old_id_and_pin(self, area):
        model = write(area.models / "m.gguf", b"first")
        pin = area.files.resolve("models:m.gguf")
        model.unlink()
        write(model, b"second, longer")
        assert refused(area.files, pin.file_id).code == "unknown_id"
        with pytest.raises(LocalFileError, match="replaced or changed"):
            pin.verify()

    def test_a_hard_linked_file_is_refused(self, area):
        model = write(area.models / "m.gguf")
        os.link(model, area.models / "twin.gguf")
        assert "more than one hard link" in str(refused(area.files, "models:m.gguf"))

    def test_the_label_root_itself_is_not_a_model_folder(self, area):
        write(area.models / "m.gguf")
        (area.models / "self").symlink_to(area.models, target_is_directory=True)
        assert "strictly below" in str(refused(area.files, "models:self"))


class TestDeniedFolders:

    def test_the_per_user_folder_inside_an_allowed_folder_is_refused(self, tmp_path):
        models = tmp_path / "models"
        state = models / "state"
        write(state / "tool_policy.json", b"{}")
        files = LocalFiles(state, {"models": models})
        error = refused(files, "models:state/tool_policy.json")
        # Review: a models folder holding the per-user folder is not usable at all now (like an added one), so the
        # refusal comes before the denied-folder check (it said "framework's own folder" before).
        assert error.code == "invalid_value" and "contains the framework's own per-user folder" in str(error)

    def test_data_folders_inside_the_per_user_folder_stay_usable(self, tmp_path):
        """A read-only installation keeps models_dir in the per-user folder; only that subfolder is shared."""
        state = tmp_path / "state"
        model = write(state / "models" / "m.gguf")
        write(state / "secrets.store", b"vault")
        files = LocalFiles(state, {"models": state / "models"})
        assert files.resolve("models:m.gguf").path == str(model.resolve())
        (state / "models" / "vault").symlink_to(state / "secrets.store")
        assert refused(files, "models:vault").code in ("invalid_value", "permission_denied")

    def test_a_folder_holding_a_denied_folder_is_not_mounted(self, area, monkeypatch):
        hf_home = area.models / "project" / "hf"
        write(hf_home / "token", b"hf_" + b"x" * 20)
        write(area.models / "project" / "model.safetensors")
        monkeypatch.setenv("HF_HOME", str(hf_home))
        error = refused(area.files, "models:project")
        assert error.code == "permission_denied" and "Hugging Face token" in str(error)
        assert refused(area.files, "models:project/hf/token").code == "permission_denied"

    def test_denial_is_by_identity_on_a_case_insensitive_file_system(self, tmp_path, monkeypatch):
        """macOS and Windows: ~/.SSH/id_ed25519 is ~/.ssh/id_ed25519, but no string starts with ~/.ssh."""
        home = isolated_home(tmp_path)
        key = write(home / ".ssh" / "id_ed25519", b"private")
        real_stat = os.stat
        monkeypatch.setattr(local_files, "_stat", lambda p: real_stat(str(p).replace(".SSH", ".ssh")))
        spelled = home / ".SSH" / "id_ed25519"
        assert not str(spelled).startswith(str(home / ".ssh")), "a string comparison would let it through"
        entry = denied_reason(spelled, denied_folders(tmp_path / "state"))
        assert entry is not None and "SSH keys" in entry.reason
        assert key.exists()

    def test_system_and_credential_folders_are_listed(self, tmp_path, monkeypatch):
        home = isolated_home(tmp_path)
        monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
        paths = {str(d.path) for d in denied_folders(tmp_path / "state")}
        for expected in ("/etc", "/proc", "/sys", "/dev", str(home / ".ssh"), str(home / ".gnupg"),
                         str(home / ".config"), str(home / ".netrc"), str(home / ".docker"), str(home / ".aws"),
                         str(home / ".kube"), str(home / ".password-store"), str(home / ".local/share/keyrings"),
                         str(home / ".cache" / "huggingface"), str(home / ".cache" / "huggingface" / "token"),
                         str(tmp_path / "state"),
                         str(tmp_path / "AppData" / "Roaming" / "Microsoft" / "Credentials"),
                         str(tmp_path / "AppData" / "Roaming" / "Microsoft" / "Protect")):
            assert expected in paths, expected


class TestHuggingFaceCache:

    @pytest.fixture
    def hf(self, area, monkeypatch):
        for var in ("HF_HOME", "HF_TOKEN_PATH", "XDG_CACHE_HOME"):
            monkeypatch.delenv(var, raising=False)
        home = isolated_home(area.tmp) / ".cache" / "huggingface"
        snapshot = home / "hub" / "models--org--m" / "snapshots" / "abc123"
        blobs = home / "hub" / "models--org--m" / "blobs"
        write(blobs / "1", b"{}")
        write(blobs / "2", b"weights")
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").symlink_to("../../blobs/1")
        (snapshot / "model.safetensors").symlink_to("../../blobs/2")
        write(home / "token", b"hf_" + b"y" * 20)
        return SimpleNamespace(home=home, hub=home / "hub", snapshot=snapshot, blobs=blobs, area=area)

    def test_the_hub_folder_may_be_added_but_not_the_home_with_the_token(self, hf):
        store = FolderStore(hf.area.state)
        with pytest.raises(LocalFileError, match="Hugging Face token"):
            store.check_new(hf.home)
        store.add(hf.hub, "hub", "model")
        pin = hf.area.files.resolve("hub:models--org--m/snapshots/abc123")
        inner, volumes = mount_plan(pin, "/build-cache/input-model")
        assert inner == "/build-cache/input-model"
        assert volumes == {str((hf.blobs / "1").resolve()): {"bind": "/build-cache/input-model/config.json",
                                                             "mode": "ro"},
                           str((hf.blobs / "2").resolve()): {"bind": "/build-cache/input-model/model.safetensors",
                                                             "mode": "ro"}}

    def test_the_token_stays_denied_through_the_hub_folder(self, hf):
        FolderStore(hf.area.state).add(hf.hub, "hub", "model")
        (hf.hub / "token-link").symlink_to(hf.home / "token")
        assert "leads outside its folder" in str(refused(hf.area.files, "hub:token-link"))
        assert refused(hf.area.files, "hub:../token").code == "invalid_value"

    def test_without_the_hub_folder_nothing_reaches_the_cache(self, hf):
        assert "no folder labelled 'hub'" in str(refused(hf.area.files, "hub:models--org--m"))


class TestFoldersTheAIMayUse:

    def test_root_home_and_the_per_user_folder_cannot_be_added(self, tmp_path):
        state = tmp_path / "outer" / "state"
        state.mkdir(parents=True)
        store = FolderStore(state)
        for folder, message in ((Path("/"), "root folder"), (isolated_home(tmp_path), "home folder"),
                                (tmp_path / "outer", "per-user folder")):
            with pytest.raises(LocalFileError, match=message):
                store.check_new(folder)
        assert store.load() == []

    def test_a_folder_inside_a_denied_folder_cannot_be_added(self, tmp_path):
        keys = isolated_home(tmp_path) / ".ssh" / "keys"
        keys.mkdir(parents=True)
        with pytest.raises(LocalFileError, match="SSH keys"):
            FolderStore(tmp_path / "state").add(keys, "keys")

    def test_labels_are_checked_and_builtin_labels_are_reserved(self, area):
        folder = area.tmp / "mine"
        folder.mkdir()
        store = FolderStore(area.state)
        for label in ("models", "datasets", "Bad", "1x", "a b", ""):
            with pytest.raises(LocalFileError):
                store.add(folder, label)
        store.add(folder, "mine")
        if os.name == "posix":
            assert (area.state / FolderStore.FILE_NAME).stat().st_mode & 0o077 == 0

    def test_a_retargeted_folder_link_is_refused_until_confirmed_again(self, area):
        first, second = area.tmp / "first", area.tmp / "second"
        write(first / "m.gguf")
        write(second / "m.gguf")
        link = area.tmp / "current"
        link.symlink_to(first, target_is_directory=True)
        store = FolderStore(area.state)
        store.add(link, "mine")
        assert area.files.resolve("mine:m.gguf").path == str((first / "m.gguf").resolve())
        link.unlink()
        link.symlink_to(second, target_is_directory=True)
        error = refused(area.files, "mine:m.gguf")
        assert "changed since it was added" in str(error) and "llm-cli tools folders add" in str(error)
        store.add(link, "mine")  # the person confirms it again
        assert area.files.resolve("mine:m.gguf").path == str((second / "m.gguf").resolve())

    def test_a_damaged_list_means_no_added_folder(self, area):
        (area.state / FolderStore.FILE_NAME).write_text("{broken")
        store = FolderStore(area.state)
        assert store.load() == [] and "cannot be used" in store.problem
        assert [f.label for f in area.files.folders()] == ["models", "datasets"]


class TestMounts:

    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                            lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
        return BuildEngine(ConfigManager(tmp_path / "configs"))

    @staticmethod
    def config(**overrides):
        values = dict(build_id="b1", timestamp="t", model_source="x", target_arch="Rockchip",
                      target_format=ModelFormat.GGUF, output_dir="out", quantization="Q4_K_M")
        values.update(overrides)
        return BuildConfiguration(**values)

    def test_a_model_file_mounts_only_itself(self, area, engine):
        model = write(area.models / "m.gguf")
        write(area.models / "other-private.gguf")
        pin = area.files.resolve("models:m.gguf")
        path, volumes = engine._stage_model(self.config(model_source=pin.path, local_inputs=LocalInputs(model=pin)),
                                            BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert path == "/build-cache/input-model/m.gguf"
        assert volumes == {str(model.resolve()): {"bind": "/build-cache/input-model/m.gguf", "mode": "ro"}}

    def test_a_model_folder_mounts_itself_never_its_parent(self, area, engine):
        folder = area.models / "qwen"
        write(folder / "config.json", b"{}")
        pin = area.files.resolve("models:qwen")
        path, volumes = engine._stage_model(self.config(model_source=pin.path, local_inputs=LocalInputs(model=pin)),
                                            BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert (path, volumes) == ("/build-cache/input-model",
                                   {str(folder.resolve()): {"bind": "/build-cache/input-model", "mode": "ro"}})

    def test_a_hard_link_inside_a_model_folder_is_refused_at_the_mount(self, area, engine):
        folder = area.models / "qwen"
        write(folder / "config.json", b"{}")
        pin = area.files.resolve("models:qwen")
        secret = write(area.tmp / "secret.txt", b"private")
        os.link(secret, folder / "innocent.txt")
        with pytest.raises(LocalFileError, match="hard link"):
            engine._stage_model(self.config(model_source=pin.path, local_inputs=LocalInputs(model=pin)),
                                BuildProgress("b1", BuildStatus.QUEUED, ""))

    def test_an_rknn_dataset_at_a_label_root_is_refused(self, area, engine):
        dataset = write(area.datasets / "list.txt", b"img.jpg\n")
        write(area.datasets / "unrelated-private.txt", b"x")
        pin = area.files.resolve("datasets:list.txt")
        config = self.config(target_format=ModelFormat.RKNN, quantization="i8", dataset_path=str(dataset),
                             local_inputs=LocalInputs(dataset=pin))
        with pytest.raises(LocalFileError, match="own subfolder"):
            engine._dataset_mount(config)

    def test_an_rknn_dataset_in_its_own_subfolder_mounts_that_folder(self, area, engine):
        dataset = write(area.datasets / "calib" / "list.txt", b"img.jpg\n")
        write(area.datasets / "calib" / "img.jpg", b"jpg")
        pin = area.files.resolve("datasets:calib/list.txt")
        config = self.config(target_format=ModelFormat.RKNN, quantization="i8", dataset_path=str(dataset),
                             local_inputs=LocalInputs(dataset=pin))
        assert engine._dataset_mount(config) == (
            {str(dataset.parent.resolve()): {"bind": "/build-cache/dataset", "mode": "ro"}},
            "/build-cache/dataset/list.txt")

    def test_the_pins_are_checked_again_right_before_a_container_starts(self, area, engine):
        model = write(area.models / "m.gguf", b"shown")
        pin = area.files.resolve("models:m.gguf")
        config = self.config(model_source=pin.path, local_inputs=LocalInputs(model=pin))
        engine._verify_local_inputs(config)  # unchanged: fine
        model.unlink()
        write(model, b"swapped after the question")
        with pytest.raises(LocalFileError, match="replaced or changed"):
            engine._verify_local_inputs(config)


# --- the build service, the registry (Ditto) and the MCP server --------------------------------------------------

class FakeOrchestrator:
    def __init__(self):
        self._workflows = {}
        self.submitted = []
        self.build_engine = SimpleNamespace(get_build_status=lambda bid: None)

    async def submit_build_request(self, req):
        from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
        self.submitted.append(req)
        self._workflows[req.request_id] = WorkflowState(req.request_id, OrchestrationStatus.QUEUED, datetime.now())
        return req.request_id


@pytest.fixture
def framework(booted_framework):
    booted_framework.orchestrator = FakeOrchestrator()
    registry = booted_framework.tool_registry
    registry.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.ALLOW}))
    return booted_framework


def models_dir(fw) -> Path:
    return Path(fw.resolve_path(fw.config.get("models_dir")))


ATTACKS = ["/etc/passwd", "~/.ssh/id_ed25519", "models:../../.ssh/id_ed25519", "elsewhere:m.gguf"]


class TestBuildServiceForAICallers:

    def build(self, **overrides):
        values = dict(model="HuggingFaceTB/SmolLM2-135M-Instruct", target="Rockchip", format="gguf", board="rk3566")
        values.update(overrides)
        return BuildRequestIn(**values)

    def test_a_local_model_by_label_is_pinned_into_the_request(self, framework):
        model = write(models_dir(framework) / "smol" / "model.gguf")
        framework.build_service.start_build(self.build(model="models:smol/model.gguf"))
        req = framework.orchestrator.submitted[0]
        assert req.models == [str(model.resolve())] and req.local_inputs.model.ref == "models:smol/model.gguf"

    @pytest.mark.parametrize("model", ATTACKS)
    def test_paths_are_refused(self, framework, model):
        with pytest.raises(LocalFileError):
            framework.build_service.start_build(self.build(model=model))
        assert framework.orchestrator.submitted == []

    def test_a_dataset_path_is_refused(self, framework):
        with pytest.raises(LocalFileError, match="is a path"):
            framework.build_service.start_build(self.build(dataset_path="/home/user/.ssh/id_ed25519"))

    def test_a_repo_id_is_never_looked_up_on_disk(self, framework, tmp_path, monkeypatch):
        """'owner/name' that happens to exist below the working directory stays a Hugging Face repo id."""
        write(tmp_path / "cwd" / "owner" / "name" / "config.json", b"{}")
        monkeypatch.chdir(tmp_path / "cwd")
        framework.build_service.start_build(self.build(model="owner/name"))
        req = framework.orchestrator.submitted[0]
        # Review: the request carries empty local inputs (it was None), which tells the build engine not to look
        # the repo id up on disk either (TestReviewS6.test_the_engine_never_looks_up_an_ai_callers_repo_id_on_disk).
        assert req.models == ["owner/name"] and req.local_inputs == LocalInputs()

    def test_a_persons_own_caller_may_still_name_a_path(self, framework, tmp_path):
        model = write(tmp_path / "anywhere" / "model.gguf")
        req = framework.build_service.validate(self.build(model=str(model)), local_paths=True)
        assert req.model == str(model.resolve())


class TestSameRefusalsForEveryCaller:

    @pytest.mark.parametrize("model", ATTACKS)
    def test_ditto_in_process_and_mcp_get_the_same_refusal(self, framework, model):
        pytest.importorskip("mcp")
        import asyncio
        from mcp import Client
        from orchestrator import mcp_server
        arguments = {"model": model, "target": "Rockchip", "format": "gguf", "board": "rk3566"}
        with pytest.raises(ToolError) as ditto:
            framework.tool_registry.call("start_build", arguments, ToolContext("ditto"))

        async def go():
            async with Client(mcp_server.build_mcp_server(framework)) as client:
                return await client.call_tool("start_build", arguments)
        result = asyncio.run(go())
        text = " ".join(getattr(c, "text", "") for c in result.content)
        # Over MCP a handler's refusal is marked as quoting external text (it echoes the reference the model sent):
        # the same words, inside an untrusted block.
        assert "<untrusted_data" in text
        plain = re.sub(r'<untrusted_data id="(\w+)"[^>]*>\n(.*)\n</untrusted_data id="\1">', r"\2", text,
                       flags=re.DOTALL)
        assert result.is_error and str(ditto.value) in plain and "Next:" in plain
        assert framework.orchestrator.submitted == []
        # Each call has a write-ahead "started" entry and its end entry (S1): one refused call per caller.
        actors = [e["actor"] for e in framework.tool_registry.audit.entries(10) if e["outcome"] == "error"]
        assert sorted(actors) == ["ditto", "mcp"]


# --- the list in the CLI and the GUI --------------------------------------------------------------------------

def cli_framework(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    registry = ToolRegistry(PolicyStore(state), AuditLog(state / "audit.db"))
    values = {"models_dir": str(tmp_path / "models"), "datasets_dir": str(tmp_path / "datasets")}
    config = SimpleNamespace(user_state_dir=state, get=lambda key, default=None: values.get(key, default))
    return SimpleNamespace(tool_registry=registry, config=config, resolve_path=Path)


def invoke(fw, *args, monkeypatch=None, terminal=False, answer="y\n"):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = fw
            return self
    if terminal:
        monkeypatch.setattr(cli_module, "_require_terminal", lambda command: None)
    return CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx(), input=answer)


class TestCli:

    def test_add_and_remove_need_a_terminal(self, tmp_path):
        fw = cli_framework(tmp_path)
        (tmp_path / "mine").mkdir()
        for args in (("tools", "folders", "add", str(tmp_path / "mine"), "--label", "mine"),
                     ("tools", "folders", "remove", "mine")):
            result = invoke(fw, *args)
            assert result.exit_code == 2 and "needs a terminal" in result.output
        assert FolderStore(tmp_path / "state").load() == []

    def test_add_warns_asks_and_audits(self, tmp_path, monkeypatch):
        fw = cli_framework(tmp_path)
        write(tmp_path / "mine" / "m.gguf")
        result = invoke(fw, "tools", "folders", "add", str(tmp_path / "mine"), "--label", "mine",
                        monkeypatch=monkeypatch, terminal=True)
        flat = " ".join(result.output.split())
        assert result.exit_code == 0, result.output
        assert "community or AI-written code" in flat and "build logs" in flat
        assert [f["label"] for f in FolderStore(tmp_path / "state").load()] == ["mine"]
        entry = fw.tool_registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"], entry["outcome"]) == ("cli", "ai_folders", "folder_added")
        listed = invoke(fw, "tools", "folders", "list")
        assert "mine" in listed.output and "models" in listed.output and "datasets" in listed.output

    def test_no_at_the_question_adds_nothing(self, tmp_path, monkeypatch):
        fw = cli_framework(tmp_path)
        (tmp_path / "mine").mkdir()
        result = invoke(fw, "tools", "folders", "add", str(tmp_path / "mine"), "--label", "mine",
                        monkeypatch=monkeypatch, terminal=True, answer="n\n")
        assert result.exit_code == 1 and FolderStore(tmp_path / "state").load() == []

    def test_home_is_refused(self, tmp_path, monkeypatch):
        fw = cli_framework(tmp_path)
        result = invoke(fw, "tools", "folders", "add", str(isolated_home(tmp_path)), "--label", "home",
                        monkeypatch=monkeypatch, terminal=True)
        assert result.exit_code == 1 and "home folder" in result.output

    def test_remove_is_audited(self, tmp_path, monkeypatch):
        fw = cli_framework(tmp_path)
        (tmp_path / "mine").mkdir()
        FolderStore(tmp_path / "state").add(tmp_path / "mine", "mine")
        result = invoke(fw, "tools", "folders", "remove", "mine", monkeypatch=monkeypatch, terminal=True)
        assert result.exit_code == 0 and FolderStore(tmp_path / "state").load() == []
        assert fw.tool_registry.audit.entries(1)[0]["outcome"] == "folder_removed"


class TestGui:

    def test_dialog_adds_after_yes_and_audits_removes_too(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui.dialogs import AIFoldersDialog
        fw = cli_framework(tmp_path)
        (tmp_path / "mine").mkdir()
        asked = []
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
        dialog = AIFoldersDialog(fw, fw.tool_registry)
        assert dialog.add_folder(str(tmp_path / "mine"), "mine")
        assert "community or AI-written code" in asked[0]
        assert dialog.table.rowCount() == 3
        assert fw.tool_registry.audit.entries(1)[0]["actor"] == "gui"
        assert dialog.remove_folder("mine") and dialog.table.rowCount() == 2
        assert [e["outcome"] for e in fw.tool_registry.audit.entries(2)] == ["folder_removed", "folder_added"]

    def test_dialog_refuses_home_without_asking(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui.dialogs import AIFoldersDialog
        fw = cli_framework(tmp_path)
        warned = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("must not ask"))
        assert not AIFoldersDialog(fw, fw.tool_registry).add_folder(str(isolated_home(tmp_path)), "home")
        assert "home folder" in warned[0] and FolderStore(tmp_path / "state").load() == []


def test_datasets_folder_setting_defaults_next_to_models(tmp_path):
    cfg = ConfigManager(tmp_path / "configs")
    assert Path(cfg.get("datasets_dir")).name == "datasets"
    assert Path(cfg.get("datasets_dir")).parent == Path(cfg.get("models_dir")).parent


# --- adversarial review of S6 ----------------------------------------------------------------------------------

class TestReviewS6:
    """Findings of the adversarial review: each test fails without its fix."""

    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                            lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
        return BuildEngine(ConfigManager(tmp_path / "configs"))

    def test_the_engine_never_looks_up_an_ai_callers_repo_id_on_disk(self, framework, engine, tmp_path,
                                                                     monkeypatch):
        """'Documents/private' is a valid repo id. The build service kept it as one, but the build engine mounted
        the folder of that name below its working directory (an MCP server started in the home folder mounted
        ~/Documents/private into the build)."""
        import huggingface_hub

        class ReachedHuggingFace(Exception):
            pass
        private = write(tmp_path / "cwd" / "Documents" / "private" / "config.json", b"{}").parent
        write(private / "tax-return.pdf", b"private")
        monkeypatch.chdir(tmp_path / "cwd")
        monkeypatch.setattr(huggingface_hub, "HfApi", lambda **kw: (_ for _ in ()).throw(ReachedHuggingFace()))
        framework.build_service.start_build(TestBuildServiceForAICallers().build(model="Documents/private"))
        req = framework.orchestrator.submitted[0]
        config = TestMounts.config(model_source=req.models[0], local_inputs=req.local_inputs)
        with pytest.raises(ReachedHuggingFace):  # a repo id, downloaded; never the local folder
            engine._stage_model(config, BuildProgress("b1", BuildStatus.QUEUED, ""))
        # A person's own caller (GUI, CLI) still names a local folder by its path.
        _, volumes = engine._stage_model(TestMounts.config(model_source="Documents/private"),
                                         BuildProgress("b1", BuildStatus.QUEUED, ""))
        assert volumes == {str(private.resolve()): {"bind": "/build-cache/input-model", "mode": "ro"}}

    def test_a_builtin_folder_at_root_home_or_around_the_per_user_folder_is_not_usable(self, tmp_path):
        """The models and datasets settings get the same limits as an added folder: a label for '/', the home
        folder or a folder holding the per-user folder would reach every file the denied list does not name."""
        home = isolated_home(tmp_path)
        write(home / "Documents" / "tax-return.pdf", b"private")
        state = tmp_path / "outer" / "state"
        state.mkdir(parents=True)
        outside = write(tmp_path / "outer" / "m.gguf")
        for root, reference in ((home, "models:Documents/tax-return.pdf"), (tmp_path / "outer", "models:m.gguf"),
                                (Path("/"), "models:" + str(outside.resolve()).lstrip("/"))):
            files = LocalFiles(state, {"models": root})
            error = refused(files, reference)
            assert "cannot be used" in str(error), (root, str(error))
            assert [f.label for f in files.folders() if not f.problem] == []

    def test_a_folder_that_changes_while_the_person_is_asked_is_not_added(self, tmp_path, monkeypatch):
        """The question shows one folder; what is recorded must be that folder, not where a link points after the
        answer."""
        import click
        fw = cli_framework(tmp_path)
        shown, swapped = tmp_path / "shown", tmp_path / "swapped"
        shown.mkdir()
        swapped.mkdir()
        link = tmp_path / "link"
        link.symlink_to(shown, target_is_directory=True)

        def retarget_then_yes(*args, **kwargs):
            link.unlink()
            link.symlink_to(swapped, target_is_directory=True)
            return True
        monkeypatch.setattr(click, "confirm", retarget_then_yes)
        result = invoke(fw, "tools", "folders", "add", str(link), "--label", "mine", monkeypatch=monkeypatch,
                        terminal=True)
        assert result.exit_code == 1 and "changed while you were asked" in " ".join(result.output.split())
        assert FolderStore(tmp_path / "state").load() == []
        with pytest.raises(LocalFileError, match="changed while you were asked"):
            FolderStore(tmp_path / "state").add(link, "mine", expected_real=shown.resolve())

    def test_the_dialog_adds_no_folder_it_cannot_audit(self, qapp, tmp_path, monkeypatch):
        """Without a tool registry (an unusable audit log) the GUI added the folder without any record."""
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui.dialogs import AIFoldersDialog
        fw = cli_framework(tmp_path)
        (tmp_path / "mine").mkdir()
        errors = []
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a[2]))
        assert not AIFoldersDialog(fw, None).add_folder(str(tmp_path / "mine"), "mine")
        assert FolderStore(tmp_path / "state").load() == [] and "not added" in errors[0]
        with pytest.raises(RuntimeError, match="audit"):
            local_files.record_folder_change(None, "cli", "added", {"label": "mine"})

    @pytest.mark.skipif(os.name != "posix", reason="FIFOs and Unix sockets")
    def test_sockets_fifos_and_devices_inside_a_model_folder_are_never_mounted(self, area, engine, monkeypatch):
        """A mounted folder holding a Unix socket (a Docker socket) would hand the build's community or AI-written
        code the service behind it."""
        import socket
        folder = area.models / "qwen"
        write(folder / "config.json", b"{}")
        pin = area.files.resolve("models:qwen")
        os.mkfifo(folder / "pipe")
        with pytest.raises(LocalFileError, match="neither a regular file nor a folder"):
            engine._stage_model(TestMounts.config(model_source=pin.path, local_inputs=LocalInputs(model=pin)),
                                BuildProgress("b1", BuildStatus.QUEUED, ""))
        (folder / "pipe").unlink()
        monkeypatch.chdir(folder)  # a short relative name: Unix socket paths are limited to about 100 bytes
        server = socket.socket(socket.AF_UNIX)
        try:
            server.bind("docker.sock")
            with pytest.raises(LocalFileError, match="neither a regular file nor a folder"):
                engine._stage_model(TestMounts.config(model_source=pin.path, local_inputs=LocalInputs(model=pin)),
                                    BuildProgress("b1", BuildStatus.QUEUED, ""))
        finally:
            server.close()

    def test_refusals_for_ai_callers_name_the_label_never_the_real_path(self, area, framework):
        """Toward the AI: ids and labels, never the person's real paths (DECISIONS round 4 item 1)."""
        files = LocalFiles(area.state, {"models": area.tmp / "missing-models"})
        assert str(area.tmp) not in str(refused(files, "models:m.gguf"))
        first, second = area.tmp / "first", area.tmp / "second"
        first.mkdir()
        second.mkdir()
        link = area.tmp / "current"
        link.symlink_to(first, target_is_directory=True)
        FolderStore(area.state).add(link, "mine")
        link.unlink()
        link.symlink_to(second, target_is_directory=True)
        error = str(refused(area.files, "mine:m.gguf"))
        assert str(area.tmp) not in error and "llm-cli tools folders add" in error
        folder = write(models_dir(framework) / "notes" / "readme.txt", b"not a model").parent
        with pytest.raises(ValueError) as info:
            framework.build_service.start_build(TestBuildServiceForAICallers().build(model="models:notes"))
        assert "models:notes" in str(info.value) and str(folder.parent) not in str(info.value)

    def test_ai_client_credentials_and_moved_config_folders_are_denied(self, tmp_path, monkeypatch):
        """The framework never reads an AI client's credentials (principle 5): on Linux Claude Code keeps them in
        ~/.claude, not in ~/.config; an XDG_CONFIG_HOME or GNUPGHOME elsewhere is denied like ~/.config."""
        home = isolated_home(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setenv("GNUPGHOME", str(tmp_path / "gpg"))
        denied = denied_folders(tmp_path / "state")
        for secret in (write(home / ".claude" / ".credentials.json", b"{}"), write(home / ".claude.json", b"{}"),
                       write(home / ".codex" / "auth.json", b"{}"), write(home / ".gemini" / "oauth.json", b"{}"),
                       write(tmp_path / "cfg" / "gh" / "hosts.yml", b"x"), write(tmp_path / "gpg" / "key", b"x")):
            assert denied_reason(secret, denied) is not None, secret
