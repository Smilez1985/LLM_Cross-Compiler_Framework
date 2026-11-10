"""Recipes as target templates (DESIGN "Community, recipes and packs"): the build code is the target a recipe names,
and model, format, board, quantization, importance matrix and calibration file are variables picked in the build menu;
a variable left out takes the recipe's value. One path (BuildService.start_recipe) for the Builder, `build start
--recipe` and start_recipe_build; the builder uses everything a recipe holds: its values build, its pins are checked,
its reference results are compared with the new build, its target pin decides whether they can be."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_recipes import COMMIT, recipe_data
from test_review_capabilities_paths import HF_MODEL, cli, gui_messages  # noqa: F401 - the fixtures

OTHER_COMMIT = "b" * 40


def make_recipe(**overrides):
    from orchestrator.schemas.recipes import Recipe
    data = recipe_data(format="gguf", board="rk3588", quantization="Q4_K_M",
                       options={"use_imatrix": True, "max_context_length": 4096})
    data.update(overrides)
    return Recipe.model_validate(data)


def with_dataset(recipe, path: Path):
    from orchestrator.schemas.recipes import DatasetRef
    named = DatasetRef(name=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return recipe.model_copy(update={"options": recipe.options.model_copy(update={"dataset": named})})


@pytest.fixture
def service(booted_framework, monkeypatch):
    from orchestrator.Core.build_service import BuildService
    from test_foundation import FakeOrchestrator
    booted_framework.orchestrator = FakeOrchestrator()
    svc = BuildService(booted_framework)
    svc.installed = make_recipe()
    monkeypatch.setattr(svc, "installed_recipe", lambda rid: (svc.installed, SimpleNamespace(id=svc.installed.id)))
    return svc


def choices(**values):
    from orchestrator.schemas.recipes import RecipeChoices
    return RecipeChoices(**values)


# --- the variables -------------------------------------------------------------------------------------------------

def test_left_out_every_variable_is_the_recipes(service):
    request, recipe, _ = service.recipe_request("r")
    assert (request.model, request.revision, request.target, request.format, request.board, request.quantization) == (
        "Qwen/Qwen3.5-2B", COMMIT, "Rockchip", "gguf", "rk3588", "Q4_K_M")
    assert request.use_imatrix is True and request.max_context_length == 4096
    assert request.dataset_path == "", "a recipe never takes the person's default calibration file"


def test_another_model_is_built_at_its_own_commit_and_the_rest_stays(service):
    request, _, _ = service.recipe_request("r", choices=choices(model="Qwen/Qwen2.5-0.5B-Instruct"))
    assert request.model == "Qwen/Qwen2.5-0.5B-Instruct" and request.revision is None, "the default branch"
    assert (request.quantization, request.use_imatrix, request.max_context_length) == ("Q4_K_M", True, 4096)
    request, _, _ = service.recipe_request("r", choices=choices(model="Qwen/Qwen2.5-0.5B-Instruct",
                                                                revision=OTHER_COMMIT))
    assert request.revision == OTHER_COMMIT
    request, _, _ = service.recipe_request("r", choices=choices(model="qwen/qwen3.5-2b"))
    assert request.revision == COMMIT, "the recipe's own model (any letter case) keeps the recipe's commit"


def test_a_changed_format_takes_the_targets_quantization_and_the_build_defaults(service):
    request, _, _ = service.recipe_request("r", choices=choices(format="rkllm"))
    assert request.format == "rkllm" and request.quantization is None and request.use_imatrix is None
    assert request.board == "rk3588", "the board stays the recipe's"
    request, _, _ = service.recipe_request("r", choices=choices(format="rkllm", quantization="w8a8",
                                                                use_imatrix=False))
    assert (request.quantization, request.use_imatrix) == ("w8a8", False)


def test_the_calibration_file_the_recipes_or_your_own(service, tmp_path):
    from orchestrator.Core.build_service import BuildServiceError
    ds = tmp_path / "calib.txt"
    ds.write_text("calibration\n", encoding="utf-8")
    service.installed = with_dataset(make_recipe(), ds)
    with pytest.raises(BuildServiceError) as e:
        service.recipe_request("r")
    assert e.value.code == "missing_argument" and "own_dataset_path" in e.value.next
    assert service.recipe_request("r", str(ds))[0].dataset_path == str(ds)
    assert service.recipe_request("r", choices=choices(own_dataset_path=""))[0].dataset_path == ""
    with pytest.raises(BuildServiceError) as e:
        service.recipe_request("r", str(ds), choices=choices(own_dataset_path=str(ds)))
    assert e.value.field == "own_dataset_path"


def test_the_changed_variables_are_named(service):
    request, recipe, _ = service.recipe_request("r")
    validated = service.validate(request, local_paths=True)
    assert service.recipe_changes(recipe, validated, None) == []
    chosen = choices(model="Qwen/Qwen2.5-0.5B-Instruct", quantization="Q8_0", use_imatrix=False,
                     own_dataset_path="")
    request, recipe, _ = service.recipe_request("r", choices=chosen)
    validated = service.validate(request, local_paths=True)
    assert service.recipe_changes(recipe, validated, chosen) == ["model", "quantization", "use_imatrix"], \
        "no file for a recipe that names none is no change"


# --- one path, and the record the build engine compares with -----------------------------------------------------

def test_start_recipe_carries_the_recipe_record(service):
    service.start_recipe("r", local_paths=True, choices=choices(quantization="Q8_0"))
    (req,) = service.framework.orchestrator.submitted
    assert req.quantization_options == ["Q8_0"] and req.model_revision == COMMIT and req.max_context_length == 4096
    record = req.recipe
    assert record["id"] == service.installed.id and record["changed"] == ["quantization"]
    assert record["sha256"] == service.recipe_digest(service.installed)
    assert record["target_sha256"] == service.installed.target.pack_sha256
    assert [r["sha256"] for r in record["results"]] == [service.installed.results[0].artifact.sha256]


def test_your_own_calibration_file_carries_no_pin(service, tmp_path):
    ds, own = tmp_path / "calib.txt", tmp_path / "mine.txt"
    ds.write_text("calibration\n", encoding="utf-8")
    own.write_text("my text\n", encoding="utf-8")
    service.installed = with_dataset(make_recipe(), ds)
    service.start_recipe("r", local_paths=True, dataset_path=str(ds))
    service.start_recipe("r", local_paths=True, choices=choices(own_dataset_path=str(own)))
    pinned, mine = service.framework.orchestrator.submitted
    assert pinned.dataset_sha256 == service.installed.options.dataset.sha256 and pinned.recipe["changed"] == []
    assert mine.dataset_sha256 is None and mine.dataset_path == str(own.resolve())
    assert mine.recipe["changed"] == ["dataset"]


def test_a_plain_build_carries_no_recipe(service):
    from orchestrator.schemas.build import BuildRequestIn
    service.start_build(BuildRequestIn(model=HF_MODEL, target="Rockchip", format="gguf"), local_paths=True)
    assert service.framework.orchestrator.submitted[0].recipe is None


def test_the_target_note_and_the_reference_lines(service):
    note = service.recipe_target_note(service.installed)
    assert "target's files differ" in note and "Rockchip version 1.2.0" in note, "the shipped target is not the pin"
    (line,) = service.recipe_reference_lines(service.installed)
    assert "Qwen3.5-2B-w8a8.rkllm" in line and "framework 3.1.0" in line and "12.3 tokens/s" in line


# --- the build engine: the target pin and the reference results ----------------------------------------------------

def engine_config(recipe_record, toolchain="sha256:" + "e" * 64):
    return SimpleNamespace(recipe=recipe_record, target_arch="Rockchip", toolchain_image=toolchain)


def record(service, changed=()):
    from orchestrator.Core.build_service import BuildService
    return BuildService.recipe_record(service.installed, list(changed))


def test_the_target_pin_is_compared(service, tmp_path):
    from orchestrator.Core.builder import BuildEngine
    folder = tmp_path / "Target"
    folder.mkdir()
    (folder / "target.yml").write_text("metadata: {}\n", encoding="utf-8")
    progress = SimpleNamespace(warnings=[], add_warning=lambda w: progress.warnings.append(w))
    config = engine_config(record(service))
    BuildEngine._compare_recipe_target(config, progress, folder)
    assert config.recipe["target"] == "different" and "target's files differ" in progress.warnings[0]
    from orchestrator.Core.community_manager import installed_digest
    config = engine_config({**record(service), "target_sha256": installed_digest(folder)})
    progress.warnings.clear()
    BuildEngine._compare_recipe_target(config, progress, folder)
    assert config.recipe["target"] == "same" and progress.warnings == []


@pytest.mark.parametrize("changed, target, sha, reproduced", [
    ((), "same", "REF", True),
    ((), "same", "f" * 64, False),
    (("quantization",), "same", "f" * 64, None),
    ((), "different", "f" * 64, None),
])
def test_the_result_is_compared_with_the_reference(service, changed, target, sha, reproduced):
    from orchestrator.Core.builder import BuildEngine
    ref = service.installed.results[0].artifact.sha256
    config = engine_config({**record(service, changed), "target": target})
    progress = SimpleNamespace(warnings=[], add_warning=lambda w: progress.warnings.append(w))
    primary = {"file": "model.gguf", "sha256": ref if sha == "REF" else sha}
    out = BuildEngine._recipe_outcome(config, primary, progress)
    assert out["reproduced"] is reproduced and out["changed"] == list(changed) and out["target"] == target
    assert out["id"] == service.installed.id and out["reference"] == [ref]
    if reproduced is False:
        (warning,) = progress.warnings
        assert "did not reproduce" in warning and "framework 3.1.0" in warning and "e" * 16 in warning
    else:
        assert progress.warnings == []


def test_build_info_records_the_recipe(service, tmp_path):
    from orchestrator.Core.builder import BuildConfiguration, BuildEngine, ModelFormat
    out = tmp_path / "out"
    out.mkdir()
    (out / "model.gguf").write_bytes(b"weights")
    config = BuildConfiguration(build_id="b1", timestamp="t", model_source=HF_MODEL, target_arch="Rockchip",
                                target_format=ModelFormat.GGUF, output_dir=str(out),
                                recipe={**record(service), "target": "same"})
    engine = BuildEngine.__new__(BuildEngine)
    info = engine._write_build_manifest(config, out)
    assert info["recipe"]["reproduced"] is False and info["recipe"]["id"] == service.installed.id


# --- the CLI: build start --recipe --------------------------------------------------------------------------------

@pytest.fixture
def recipe_cli(cli, monkeypatch):  # noqa: F811 - the imported fixture
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.consistency_manager import Preflight
    cli.recipe = make_recipe()
    monkeypatch.setattr(BuildService, "installed_recipe",
                        lambda self, rid: (cli.recipe, SimpleNamespace(id=cli.recipe.id)))
    monkeypatch.setattr(BuildService, "preflight_for", lambda self, *a, **kw: Preflight())
    monkeypatch.setattr(BuildService, "earlier_builds", lambda self, request, **kw: [])
    return cli


def test_build_start_recipe_builds_the_recipe(recipe_cli):
    result = recipe_cli.invoke("build", "start", "--recipe", "r", "-o", "-", quiet=False)
    assert result.code == 0, result.out
    (req,) = recipe_cli.submitted
    assert req.models == ["Qwen/Qwen3.5-2B"] and req.model_revision == COMMIT and req.targets == ["Rockchip"]
    assert req.quantization_options == ["Q4_K_M"] and req.max_context_length == 4096
    assert req.recipe["changed"] == [] and "Built as the recipe pins it" in result.out
    assert "Recipe reference: Qwen3.5-2B-w8a8.rkllm" in result.out


def test_build_start_recipe_takes_the_options_as_variables(recipe_cli):
    result = recipe_cli.invoke("build", "start", "--recipe", "r", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-q", "Q8_0",
                               "--no-imatrix", "-o", "-", quiet=False)
    assert result.code == 0, result.out
    (req,) = recipe_cli.submitted
    assert req.models == ["Qwen/Qwen2.5-0.5B-Instruct"] and req.model_revision is None
    assert req.recipe["changed"] == ["model", "quantization", "use_imatrix"]
    assert "Changed from the recipe: model, quantization, use_imatrix" in result.out


def test_build_start_recipe_tells_the_calibration_files_apart(recipe_cli, tmp_path):
    ds, own = tmp_path / "calib.txt", tmp_path / "mine.txt"
    ds.write_text("calibration\n", encoding="utf-8")
    own.write_text("my text\n", encoding="utf-8")
    recipe_cli.recipe = with_dataset(make_recipe(), ds)
    refused = recipe_cli.invoke("build", "start", "--recipe", "r", "-o", "-")
    assert refused.code == 2 and "--no-dataset" in refused.out and recipe_cli.submitted == []
    assert recipe_cli.invoke("build", "start", "--recipe", "r", "--dataset", str(ds), "-o", "-").code == 0
    assert recipe_cli.invoke("build", "start", "--recipe", "r", "--dataset", str(own), "-o", "-").code == 0
    pinned, mine = recipe_cli.submitted
    assert pinned.dataset_sha256 == recipe_cli.recipe.options.dataset.sha256 and pinned.recipe["changed"] == []
    assert mine.dataset_sha256 is None and mine.recipe["changed"] == ["dataset"]


def test_build_start_recipe_refuses_another_target(recipe_cli, tmp_path):
    result = recipe_cli.invoke("build", "start", "--recipe", "r", "-t", "NoSuchTarget", "-o", "-")
    assert result.code == 2 and "template of the target Rockchip" in result.out and recipe_cli.submitted == []


def test_build_start_without_a_recipe_still_needs_model_and_target(recipe_cli):
    result = recipe_cli.invoke("build", "start", "-t", "Rockchip")
    assert result.code == 2 and "Missing option --model" in result.out and recipe_cli.submitted == []


# --- the Builder ----------------------------------------------------------------------------------------------------

@pytest.fixture
def tab(qapp, booted_framework, monkeypatch, gui_messages):  # noqa: F811 - the imported fixture
    from test_review_capabilities_paths import make_tab
    widget = make_tab(booted_framework)
    widget.recipe = make_recipe()
    monkeypatch.setattr(widget.framework.build_service, "installed_recipe",
                        lambda rid: (widget.recipe, SimpleNamespace(id=widget.recipe.id)))
    yield widget
    widget._build_timer.stop()


def test_the_builder_keeps_the_recipe_for_another_model(tab):
    from test_review_capabilities_paths import submit
    tab.apply_recipe(tab.recipe)
    assert "Recipe reference: Qwen3.5-2B-w8a8.rkllm" in tab.log_area.toPlainText()
    tab.model_input.setEditText("Qwen/Qwen2.5-0.5B-Instruct")
    req = submit(tab)
    assert req is not None and req.recipe is not None and req.recipe["changed"] == ["model"]
    assert req.model_revision is None and req.max_context_length == 4096, "the recipe's context length stays"
    assert "model Qwen/Qwen2.5-0.5B-Instruct (recipe: Qwen/Qwen3.5-2B)" in tab.log_area.toPlainText()
