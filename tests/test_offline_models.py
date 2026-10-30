"""Offline builds pick a model already on this computer (DECISIONS round 25 Q1).

A build the person starts in offline mode never tries to download its model: a model named by its Hugging Face id is
used from a copy on this computer (Core/local_models.local_copy: an earlier build's copy, the Hugging Face browser's
download, the old huggingface_hub cache), without any connection. When there is none, nothing starts: the CLI lists
the models already here and takes a listed label or a local path (`build start --model`), the Builder tab opens a
choice with an open dialog for a file or folder of the person's own. An AI picks only among the models already here,
by label; no host path reaches it. `ditto models download` in offline mode refuses in plain words and names the
models already here.

Every test is local: Hugging Face is never asked, and the offline tests refuse every socket."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_build_tools import BUILD, MODEL, env  # noqa: F401 - the AI caller's registry and service
from test_hf_online import guard_sockets

REPO = MODEL                      # the model the AI test's build names
COMMIT = "d" * 40
GGUF = "models:hf/acme--tiny-GGUF/abcdef123456/tiny-q4_k_m.gguf"


def model_folder(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "config.json").write_text("{}", encoding="utf-8")
    (folder / "model.safetensors").write_bytes(b"w" * 10)
    return folder


def staged(cache: Path, repo: str = REPO, commit: str = COMMIT, complete: bool = True) -> Path:
    """The Builder's copy of `repo` at `commit`, as its staging leaves it (with its record when complete)."""
    from orchestrator.Core.local_models import write_complete
    folder = model_folder(cache / "models" / "hf" / repo.replace("/", "--") / commit)
    if complete:
        write_complete(folder, repo, commit, {"config.json": 2, "model.safetensors": 10})
    return folder


def a_gguf(models: Path) -> Path:
    path = models / "hf" / "acme--tiny-GGUF" / "abcdef123456" / "tiny-q4_k_m.gguf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\0" * 64)
    return path


# --- what counts as a copy ---------------------------------------------------------------------------------------

class TestALocalCopy:

    def test_the_builders_copy_counts_only_while_its_record_holds(self, tmp_path):
        from orchestrator.Core.local_models import model_folder_complete
        folder = staged(tmp_path / "cache")
        assert model_folder_complete(folder)
        (folder / "model.safetensors").write_bytes(b"short")
        assert not model_folder_complete(folder), "a file of another size: not the copy the record names"
        incomplete = staged(tmp_path / "cache", commit="e" * 40, complete=False)
        (incomplete / "model.safetensors.part").write_bytes(b"x")
        assert not model_folder_complete(incomplete), "an unfinished download never counts"

    def test_a_folder_without_the_record_needs_config_weights_and_every_shard(self, tmp_path):
        from orchestrator.Core.local_models import model_folder_complete
        folder = model_folder(tmp_path / "m")
        assert model_folder_complete(folder)
        (folder / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {
            "a": "model-00001-of-00002.safetensors", "b": "model-00002-of-00002.safetensors"}}))
        (folder / "model-00001-of-00002.safetensors").write_bytes(b"1")
        assert not model_folder_complete(folder), "a shard the index names is missing"
        (folder / "model-00002-of-00002.safetensors").write_bytes(b"2")
        assert model_folder_complete(folder)
        (folder / "config.json").unlink()
        assert not model_folder_complete(folder)

    def test_it_is_found_in_the_builders_cache_the_browsers_downloads_and_the_old_cache(self, tmp_path):
        from orchestrator.Core.local_models import local_copy
        cache, models = tmp_path / "cache", tmp_path / "models"
        assert local_copy(cache, models, REPO) is None
        browser = model_folder(models / "hf" / REPO.replace("/", "--") / COMMIT[:12])
        assert local_copy(cache, models, REPO).path == browser
        old = model_folder(tmp_path / "other-models" / f"models--{REPO.replace('/', '--')}" / "snapshots" / COMMIT)
        assert local_copy(cache, tmp_path / "other-models", REPO).path == old
        mine = staged(cache)
        found = local_copy(cache, models, REPO)
        assert (found.path, found.commit, found.where) == (mine, COMMIT, "build_cache"), "the Builder's own first"
        assert local_copy(cache, models, REPO, COMMIT).path == mine, "the commit asked for"
        assert local_copy(cache, models, REPO, "f" * 40) is None, "another commit: not one of these copies"
        assert local_copy(cache, models, "../etc") is None


# --- the Builder never downloads in offline mode ----------------------------------------------------------------

def engine(tmp_path, offline=True):
    from orchestrator.Core.builder import BuildEngine
    e = BuildEngine.__new__(BuildEngine)
    e.cache_dir, e.models_dir, e.base_dir = tmp_path / "cache", tmp_path / "models", tmp_path
    e.secrets_manager = SimpleNamespace(get_secret=lambda key: None)
    e.framework, e.config = None, {"offline_mode": offline}
    e.logger = SimpleNamespace(info=print, warning=print, debug=print, error=print)
    e._get_conf = lambda key, default=None: default
    return e


def stage(e, model=REPO, branch="main"):
    from orchestrator.Core.builder import BuildConfiguration, BuildProgress, BuildStatus, ModelFormat
    config = BuildConfiguration(build_id="b1", timestamp="t", model_source=model, target_arch="Rockchip",
                                target_format=ModelFormat.GGUF, output_dir="out", model_branch=branch)
    progress = BuildProgress("b1", BuildStatus.QUEUED, "")
    return e._stage_model(config, progress), config, progress


class TestTheBuilderOffline:

    def test_a_copy_here_is_used_without_any_connection(self, tmp_path, monkeypatch):
        """Regression (hf-online's open item): BuildEngine._stage_model did not read offline mode, so a build the
        person started in offline mode still downloaded its model from Hugging Face."""
        folder = staged(tmp_path / "cache")
        tried = guard_sockets(monkeypatch)
        (inner, volumes), config, progress = stage(engine(tmp_path))
        assert volumes == {str(folder): {"bind": "/build-cache/input-model", "mode": "ro"}}
        assert inner == "/build-cache/input-model" and config.model_revision == COMMIT
        assert any("Offline mode: using the copy" in line for line in progress.logs)
        assert tried == []

    def test_without_a_copy_nothing_is_downloaded(self, tmp_path, monkeypatch):
        tried = guard_sockets(monkeypatch)
        with pytest.raises(ValueError, match="Offline mode is on, so .* not downloaded"):
            stage(engine(tmp_path))
        partial = staged(tmp_path / "cache", complete=False)   # an earlier staging that stopped mid-way
        (partial / "model-00002-of-00002.safetensors.part").write_bytes(b"x")
        with pytest.raises(ValueError, match="no copy of it is on this computer"):
            stage(engine(tmp_path))
        assert tried == []

    def test_a_staging_that_finished_writes_the_record_of_its_copy(self, tmp_path, monkeypatch):
        import huggingface_hub
        from orchestrator.Core.builder import BuildEngine
        from orchestrator.Core.local_models import local_copy

        class Api:
            def __init__(self, endpoint=None, token=None):
                pass

            def model_info(self, repo, revision=None, token=None, files_metadata=False):
                return SimpleNamespace(sha=COMMIT, gated=False, siblings=[
                    SimpleNamespace(rfilename="config.json", size=2, lfs=None),
                    SimpleNamespace(rfilename="model.safetensors", size=10, lfs=None)])

        class Fetcher:
            def fetch_file(self, repo, commit, file, dest, progress=None):
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                Path(dest).write_bytes(b"x" * file.size)
                return Path(dest), "0" * 64
        monkeypatch.setattr(huggingface_hub, "HfApi", Api)
        monkeypatch.setattr(BuildEngine, "_hub_fetcher", staticmethod(lambda token, stop: Fetcher()))
        e = engine(tmp_path, offline=False)
        stage(e)
        copy = local_copy(e.cache_dir, e.models_dir, REPO)
        assert copy is not None and copy.commit == COMMIT, "the next offline build can use it"
        e.config["offline_mode"] = True
        tried = guard_sockets(monkeypatch)
        (_, volumes), _, _ = stage(e)
        assert list(volumes) == [str(copy.path)] and tried == []


# --- the models already here --------------------------------------------------------------------------------------

def framework(tmp_path, offline=True):
    from test_build_tools import Cfg
    config = Cfg(tmp_path, offline_mode=offline)
    return SimpleNamespace(config=config, resolve_path=lambda p: Path(p))


def earlier_build(tmp_path, model: Path):
    from orchestrator.Core import build_records
    build_records.write_record(build_records.records_dir(tmp_path / "cache"),
                               {"build_id": "old", "model": str(model), "status": "completed", "started": "t"})


class TestTheModelsHere:

    def test_the_list_names_each_place_and_an_ai_gets_labels_only(self, tmp_path):
        from orchestrator.Core.build_service import BuildService, ModelNotHere
        a_gguf(tmp_path / "models")
        staged(tmp_path / "cache", repo="acme/other-model")
        own = model_folder(tmp_path / "Documents" / "my-model")
        earlier_build(tmp_path, own)
        service = BuildService(framework(tmp_path))
        person = {m.label: m for m in service.models_here()}
        assert set(person) == {GGUF, "acme/other-model", str(own.resolve())}
        assert person[GGUF].where == "models_folder" and person["acme/other-model"].where == "build_cache"
        assert person[str(own.resolve())].where == "earlier_build" and not person[str(own.resolve())].for_ai
        ai = [m.label for m in service.models_here(for_ai=True)]
        assert ai == [GGUF, "acme/other-model"], "the input of an earlier build outside the shared folders: not for an AI"
        with pytest.raises(ModelNotHere) as refused:
            service.check_model_here(REPO, for_ai=True)
        text = str(refused.value)
        assert GGUF in text and "acme/other-model" in text and str(tmp_path) not in text, "no host path to an AI"
        assert refused.value.code == "needs_person" and refused.value.blocker == "offline_mode"
        with pytest.raises(ModelNotHere) as person_refused:
            service.check_model_here(REPO, for_ai=False)
        assert str(own.resolve()) in person_refused.value.text("en")
        assert person_refused.value.text("de").startswith("Der Offline-Modus ist an")

    def test_online_and_with_a_copy_nothing_is_refused(self, tmp_path):
        from orchestrator.Core.build_service import BuildService
        BuildService(framework(tmp_path, offline=False)).check_model_here(REPO)
        staged(tmp_path / "cache")
        BuildService(framework(tmp_path)).check_model_here(REPO)

    def test_a_label_or_a_path_is_the_persons_choice(self, tmp_path):
        from orchestrator.Core.local_models import choice_for
        path = a_gguf(tmp_path / "models")
        staged(tmp_path / "cache", repo="acme/other-model")
        fw = framework(tmp_path)
        assert choice_for(fw, GGUF) == str(path.resolve())
        assert choice_for(fw, "acme/other-model") == "acme/other-model", "the copy is used by its id"
        assert choice_for(fw, str(path)) == str(path.resolve())
        assert choice_for(fw, "nothing/here") is None and choice_for(fw, "") is None


# --- an AI caller: labels only --------------------------------------------------------------------------------------

class TestAnAICaller:

    def test_the_build_check_names_the_labels_and_no_path(self, env):  # noqa: F811
        from orchestrator.tools.registry import ToolContext
        env.framework.config.values["offline_mode"] = True
        a_gguf(env.tmp / "models")
        out = env.registry.call("check_build_request", BUILD, ToolContext("ditto"))
        problem = next(p for p in out.problems if "Offline mode is on" in p.message)
        assert GGUF in problem.message and str(env.tmp) not in problem.message
        assert env.service.lookups == [], "Hugging Face is not asked"

    def test_a_copy_here_is_built_without_any_question_about_a_download(self, env):  # noqa: F811
        from orchestrator.tools.registry import ToolContext
        env.framework.config.values["offline_mode"] = True
        staged(env.tmp / "cache")
        out = env.registry.call("check_build_request", BUILD, ToolContext("ditto"))
        assert not [p for p in out.problems if "Offline mode" in p.message], out.problems
        assert env.service.lookups == []


    def test_start_build_with_a_copy_here_queues_the_build_at_its_commit(self, env):  # noqa: F811
        from test_build_tools import ask
        env.framework.config.values["offline_mode"] = True
        staged(env.tmp / "cache")
        out, shown = ask(env, BUILD)
        assert out.request_id and shown, "the person was asked as for any AI build"
        assert env.orch.requests[0].model_revision == COMMIT and env.service.lookups == []

    def test_a_copy_the_browser_downloaded_is_used_without_pinning_a_short_commit(self, env):  # noqa: F811
        from test_build_tools import ask
        env.framework.config.values["offline_mode"] = True
        model_folder(env.tmp / "models" / "hf" / REPO.replace("/", "--") / COMMIT[:12])
        out, _ = ask(env, BUILD)
        assert out.request_id and env.orch.requests[0].model_revision is None


# --- the command line ------------------------------------------------------------------------------------------------

@pytest.fixture
def offline_cli(make_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import framework_with_targets, make_cli
    fm = framework_with_targets(make_framework, tmp_path / "targets_mixed")
    fm.config.set("offline_mode", True)
    return make_cli(fm, tmp_path, monkeypatch)


class TestTheCommandLine:

    def test_without_a_terminal_it_lists_the_models_here_and_starts_nothing(self, offline_cli, tmp_path):
        models = Path(offline_cli.framework.resolve_path(offline_cli.framework.config.get("models_dir", "models")))
        a_gguf(models)
        result = offline_cli.invoke("build", "start", "-m", REPO, "-t", "Rockchip", "-f", "gguf")
        assert result.code == 2, result.out
        assert "Offline mode is on" in result.out and GGUF in result.out and "--model" in result.out
        assert offline_cli.submitted == []

    def test_at_a_terminal_a_listed_label_starts_the_build_with_it(self, offline_cli, monkeypatch):
        from orchestrator import cli as cli_module
        models = Path(offline_cli.framework.resolve_path(offline_cli.framework.config.get("models_dir", "models")))
        path = a_gguf(models)
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        monkeypatch.setattr(cli_module.click, "prompt", lambda *a, **k: GGUF)
        monkeypatch.setattr(cli_module, "_build_destination", lambda ctx, out: (str(offline_cli.work / "out"), False))
        result = offline_cli.invoke("build", "start", "-m", REPO, "-t", "Rockchip", "-f", "gguf")
        assert result.code == 0, result.out
        assert [r.models[0] for r in offline_cli.submitted] == [str(path.resolve())]

    def test_a_listed_label_works_as_model_directly(self, offline_cli, monkeypatch):
        """The refusal says to start again with --model <label or path>: the label names its file."""
        from orchestrator import cli as cli_module
        models = Path(offline_cli.framework.resolve_path(offline_cli.framework.config.get("models_dir", "models")))
        path = a_gguf(models)
        monkeypatch.setattr(cli_module, "_build_destination", lambda ctx, out: (str(offline_cli.work / "out"), False))
        result = offline_cli.invoke("build", "start", "-m", GGUF, "-t", "Rockchip", "-f", "gguf")
        assert result.code == 0, result.out
        assert [r.models[0] for r in offline_cli.submitted] == [str(path.resolve())]

    def test_an_empty_answer_cancels(self, offline_cli, monkeypatch):
        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        monkeypatch.setattr(cli_module.click, "prompt", lambda *a, **k: "")
        result = offline_cli.invoke("build", "start", "-m", REPO, "-t", "Rockchip", "-f", "gguf")
        assert result.code == 0 and "Nothing was started." in result.out and offline_cli.submitted == []


# --- the Builder tab ---------------------------------------------------------------------------------------------------

class TestTheBuilderChoice:

    def test_the_window_lists_the_models_and_takes_a_pick_or_a_file(self, qapp, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        from orchestrator.Core.build_service import ModelNotHere
        from orchestrator.Core.local_models import LocalModel
        from orchestrator.gui import offline_model
        choices = [LocalModel(GGUF, "models_folder", "file", tmp_path / "a.gguf", "gguf"),
                   LocalModel("acme/other-model", "build_cache", "folder", tmp_path / "copy", repo="acme/other-model",
                              commit=COMMIT)]
        error = ModelNotHere(REPO, choices, for_ai=False)
        dialog = offline_model.OfflineModelDialog(error)
        assert dialog.table.rowCount() == 2 and dialog.table.item(0, 0).text() == GGUF
        dialog.table.selectRow(1)
        dialog.use_selected()
        assert dialog.chosen == "acme/other-model", "the Builder's copy: used by its id"
        dialog = offline_model.OfflineModelDialog(error)
        monkeypatch.setattr(offline_model.QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, **k: (str(tmp_path / "own.gguf"), "")))
        dialog.choose_file()
        assert dialog.chosen == str(tmp_path / "own.gguf")
        empty = offline_model.OfflineModelDialog(ModelNotHere(REPO, [], for_ai=False))
        assert not empty.btn_use.isEnabled() and "No model is on this computer yet" in empty.intro.text()

    def test_start_build_opens_the_choice_and_builds_with_the_pick(self, qapp, booted_framework, monkeypatch,
                                                                   tmp_path):
        pytest.importorskip("PySide6")
        from orchestrator.gui import builder_tab, offline_model
        booted_framework.config.set("offline_mode", True)
        own = model_folder(tmp_path / "own-model")
        shown = []
        monkeypatch.setattr(offline_model.OfflineModelDialog, "choose",
                            classmethod(lambda cls, parent, error: shown.append(error) or str(own)))
        tab = builder_tab.BuilderTab(booted_framework)
        values = {"model": REPO, "recipe": None}
        assert tab._model_on_this_computer(values) is True
        assert shown and shown[0].repo == REPO
        assert values["model"] == str(own) and tab.model_input.currentText() == str(own)
        monkeypatch.setattr(offline_model.OfflineModelDialog, "choose", classmethod(lambda cls, p, e: None))
        assert tab._model_on_this_computer({"model": REPO, "recipe": None}) is False, "cancelled: nothing starts"
        assert tab._model_on_this_computer({"model": str(own), "recipe": None}) is True, "a path: as it is"

    def test_a_recipe_pinning_another_commit_than_the_copy_asks_instead_of_looping(self, qapp, booted_framework,
                                                                                  monkeypatch):
        """The recipe pins commit X, the copy here is commit Y: choosing the copy must not bring the same choice back
        (the recipe at X is still not here). The person cancels, or builds the copy as their own build without it."""
        pytest.importorskip("PySide6")
        from types import SimpleNamespace
        from orchestrator.Core.build_service import BuildService
        from orchestrator.gui import builder_tab, offline_model
        booted_framework.config.set("offline_mode", True)
        cache, _models = BuildService(booted_framework).model_folders()
        staged(cache, REPO, COMMIT)
        pinned = "c" * 40
        recipe = SimpleNamespace(id="acme-recipe", model=SimpleNamespace(repo=REPO, revision=pinned))
        monkeypatch.setattr(offline_model.OfflineModelDialog, "choose", classmethod(lambda cls, p, e: REPO))
        asked = []
        monkeypatch.setattr(builder_tab, "ask", lambda parent, title, text: asked.append(text) or False)
        tab = builder_tab.BuilderTab(booted_framework)
        values = {"model": REPO, "recipe": recipe}
        assert tab._model_on_this_computer(values) is False, "No: nothing starts"
        assert asked and pinned[:12] in asked[0] and COMMIT[:12] in asked[0]
        assert values["recipe"] is recipe
        monkeypatch.setattr(builder_tab, "ask", lambda parent, title, text: True)
        assert tab._model_on_this_computer(values) is True
        assert values["model"] == REPO and values["recipe"] is None, "the copy, as the person's own build"


# --- ditto models download --------------------------------------------------------------------------------------------

class TestDittoModelsDownload:

    def test_offline_it_refuses_in_plain_words_and_names_what_is_here(self, tmp_path, monkeypatch):
        from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError
        from orchestrator.utils import locality
        locality.clear_cache()
        monkeypatch.setattr(locality, "_system_lookup", lambda host: ["192.0.2.18"])   # a public address
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                             settings={"offline_mode": True})
        models = rt.catalog.models
        wanted, here = models[0], models[-1]
        monkeypatch.setattr(rt, "model_state", lambda m: "ready" if m.tier == here.tier else "missing")
        monkeypatch.setattr(rt, "_fetch", lambda *a, **k: pytest.fail("no download in offline mode"))
        with pytest.raises(LocalRuntimeError) as refused:
            rt.download_model(wanted.tier)
        text = str(refused.value)
        assert text.startswith(f"Offline mode is on, so {wanted.file} is not downloaded")
        assert f"Already on this computer: {here.tier} ({here.file})." in text
        monkeypatch.setattr(rt, "model_state", lambda m: "missing")
        with pytest.raises(LocalRuntimeError, match="No model for Ditto is on this computer yet"):
            rt.download_model(wanted.tier)
        locality.clear_cache()

    def test_a_mirror_in_the_own_network_still_serves_it(self, tmp_path, monkeypatch):
        from orchestrator.runtime.ditto_models import offline_refusal
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        from orchestrator.utils import locality
        locality.clear_cache()
        monkeypatch.setattr(locality, "_system_lookup", lambda host: ["192.168.1.9"])
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                             settings={"offline_mode": True})
        assert offline_refusal(rt, rt.catalog.models[0]) is None
        assert offline_refusal(LocalLLMRuntime(data_dir=tmp_path / "s2", models_dir=tmp_path / "m2", settings={}),
                               rt.catalog.models[0]) is None, "online: as before"
        locality.clear_cache()

    def test_the_refusal_keeps_the_persons_opt_out_as_before(self, tmp_path, monkeypatch):
        # Regression (review of hf-local): before this chain the download refusal was the locality rule's own
        # (verdict.refusal), which says that the person can count a host of the own network as local. The new text
        # that names the models already here dropped that sentence, so a mirror behind a reverse proxy or a VPN could
        # not be found out from the refusal any more.
        from orchestrator.runtime.ditto_models import offline_refusal
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        from orchestrator.utils import locality
        from orchestrator.utils.localization import ui_text
        locality.clear_cache()
        monkeypatch.setattr(locality, "_system_lookup", lambda host: ["192.0.2.18"])   # a public address
        rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                             settings={"offline_mode": True})
        model = rt.catalog.models[0]
        assert ui_text("net.refusal.opt_out", "en").strip() in offline_refusal(rt, model)
        assert ui_text("net.refusal.opt_out", "de").strip() in offline_refusal(rt, model, "de")
        locality.clear_cache()
