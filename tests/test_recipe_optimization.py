"""The optimization level in recipes (DESIGN "Community, recipes and packs"), whose whole rule is
Core/recipe_optimization.py: an optional options.optimization, written as recipe format 2 only when it is used (a
format-1 recipe stays byte for byte what it was), read in both formats, a later format refused with the llmcf version it
needs; the level is a variable of a recipe build in the Builder, the CLI and the tool (the person's, else the recipe's,
else the board profile's), taken from build_info.json for a recipe made from a build, and a changed level is a changed
variable (no comparison with the recipe's reference)."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from test_recipe_templates import make_recipe, recipe_cli, service, tab  # noqa: F401 - the fixtures
from test_recipes import build_info, recipe_data
from test_review_capabilities_paths import cli, gui_messages  # noqa: F401 - the fixtures

# recipe.yml and the digest of format-1 recipes as the version before the optimization level wrote them
V1_YAML = (
    "schema_version: 1\nid: qwen3.5-2b-rk3588-rkllm-w8a8\nmodel:\n  repo: Qwen/Qwen3.5-2B\n  revision: "
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n  license: apache-2.0\ntarget:\n  id: Rockchip\n  pack_version: 1.2.0\n"
    "  pack_sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\nboard: rk3588\nformat: rkllm\n"
    "quantization: w8a8\noptions:\n  use_imatrix: false\n  max_context_length: 4096\nresults:\n- artifact:\n"
    "    file: Qwen3.5-2B-w8a8.rkllm\n    size: 2340000000\n"
    "    sha256: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n  lcf_version: 3.1.0\n"
    "  toolchain_image: ghcr.io/example/rkllm@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc\n"
    "  device_test:\n    board_model: ROCK 5B\n    runtime: rkllm 1.2.1\n    tokens_per_s: 12.3\n"
    "    smoke_passed: true\nauthor: alice\ncreated: '2026-10-11T12:00:00Z'\n")
V1_DIGEST = "2eef8f1afe4bb0f3d82318dc31546c82fbfcf1afa29d029bf0887f0431a81858"
V1_FROM_BUILD_DIGEST = "c30e79aca334ce5364560ab8786742f9cb3a7119160e0d202e5c1edb424e3cdd"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def v2_data(level="speed", **overrides):
    data = recipe_data(schema_version=2, options={"use_imatrix": False, "max_context_length": 4096,
                                                  "optimization": level})
    data.update(overrides)
    return data


# --- the format ------------------------------------------------------------------------------------------------------

def test_a_recipe_without_a_level_stays_format_1_byte_for_byte():
    import yaml
    from orchestrator.Core.build_service import BuildService
    from orchestrator.schemas.recipes import Recipe
    recipe = Recipe.model_validate(recipe_data())
    assert recipe.to_yaml() == V1_YAML and BuildService.recipe_digest(recipe) == V1_DIGEST
    again = Recipe.model_validate(yaml.safe_load(recipe.to_yaml()))
    assert again.to_yaml() == V1_YAML, "round trip"
    made = Recipe.from_build_info(build_info(), author="alice", now=NOW)
    assert made.schema_version == 1 and made.options.optimization is None
    assert BuildService.recipe_digest(made) == V1_FROM_BUILD_DIGEST and "optimization" not in made.to_yaml()


def test_a_recipe_with_a_level_is_written_as_format_2_and_read_back():
    import yaml
    from orchestrator.Core.build_service import BuildService
    from orchestrator.schemas.recipes import Recipe
    recipe = Recipe.model_validate(v2_data())
    text = recipe.to_yaml()
    assert text.startswith("schema_version: 2\n") and "  optimization: speed\n" in text
    assert text.replace("schema_version: 2", "schema_version: 1").replace("  optimization: speed\n", "") == V1_YAML, \
        "only the version and the field differ from format 1"
    again = Recipe.model_validate(yaml.safe_load(text))
    assert again.options.optimization == "speed" and again.to_yaml() == text
    assert BuildService.recipe_digest(again) != V1_DIGEST, "the level is part of what the person is asked about"


def test_the_level_and_the_format_must_fit():
    from pydantic import ValidationError
    from orchestrator.schemas.recipes import Recipe
    with pytest.raises(ValidationError) as e:
        Recipe.model_validate(v2_data(schema_version=1))
    assert "format 2" in str(e.value), "an older version would refuse the field: no format-1 recipe names a level"
    with pytest.raises(ValidationError):
        Recipe.model_validate(v2_data(level="turbo"))
    assert Recipe.model_validate(recipe_data(schema_version=2)).options.optimization is None, "format 2 is read"


@pytest.mark.parametrize("version", [3, 17])
def test_a_later_format_is_refused_naming_the_version_it_needs(tmp_path, version):
    from orchestrator.Core.community_manager import read_recipe
    from orchestrator.Core.recipe_optimization import FORMAT_NEEDS, RecipeFormatError
    (tmp_path / "recipe.yml").write_text(json.dumps(v2_data(schema_version=version, new_field=1)), encoding="utf-8")
    with pytest.raises(RecipeFormatError) as e:
        read_recipe(tmp_path)
    assert f"format {version}" in str(e.value) and "an llmcf version newer than" in str(e.value)
    assert "llm-cli update" in str(e.value) and "formats 1, 2" in str(e.value)
    assert FORMAT_NEEDS[2] == "llmcf 3.4.0 or later"
    (tmp_path / "recipe.yml").write_text(json.dumps(recipe_data(schema_version="x")), encoding="utf-8")
    with pytest.raises(RecipeFormatError) as e:
        read_recipe(tmp_path)
    assert "no recipe format" in str(e.value)


def test_a_recipe_from_a_build_takes_the_level_from_build_info():
    from orchestrator.schemas.recipes import Recipe
    info = build_info(build_options={"optimization": "size", "format": "rkllm"})
    recipe = Recipe.from_build_info(info, author="alice", now=NOW)
    assert recipe.schema_version == 2 and recipe.options.optimization == "size"
    assert recipe.to_yaml().startswith("schema_version: 2\n")
    unknown = Recipe.from_build_info(build_info(build_options={"optimization": "warp"}), author="alice", now=NOW)
    assert unknown.schema_version == 1 and unknown.options.optimization is None


def test_the_precedence_rule():
    from orchestrator.Core import recipe_optimization as ro
    from orchestrator.schemas.recipes import Recipe
    with_level, without = Recipe.model_validate(v2_data("speed")), Recipe.model_validate(recipe_data())
    assert ro.resolve(with_level, "size", "balanced") == ("size", ro.YOURS)
    assert ro.resolve(with_level, None, "balanced") == ("speed", ro.RECIPES)
    assert ro.resolve(without, None, "balanced") == ("balanced", ro.BOARDS)
    assert ro.changed(with_level, "size", "balanced") and not ro.changed(with_level, "speed", "balanced")
    assert not ro.changed(without, "balanced", "balanced") and ro.changed(without, "fast", "balanced")
    assert "the recipe's" in ro.describe(with_level, "speed", ro.RECIPES, "balanced")
    assert "yours; the recipe's: speed" in ro.describe(with_level, "size", ro.YOURS, "balanced")


# --- the community pack checks -----------------------------------------------------------------------------------------

def test_the_pack_check_accepts_a_format_2_recipe_and_names_a_later_format(tmp_path):
    from orchestrator.Core.community_manager import CommunityManager, RECIPE_FILE
    from orchestrator.Core.community_pack import build_manifest
    cm = CommunityManager.__new__(CommunityManager)
    cm.targets_dir = tmp_path / "no-targets"
    root = tmp_path / "pack"
    root.mkdir()
    data = v2_data()
    (root / RECIPE_FILE).write_text(json.dumps(data), encoding="utf-8")
    manifest = build_manifest(root, kind="recipe", id=data["id"], version="1.0.0", author="alice",
                              license="Apache-2.0")
    notes = []
    assert cm._check_recipe(root, manifest, notes) == [] and "not installed" in notes[0]
    (root / RECIPE_FILE).write_text(json.dumps({**data, "schema_version": 3}), encoding="utf-8")
    (problem,) = cm._check_recipe(root, manifest, [])
    assert problem.startswith(f"{RECIPE_FILE}: The recipe is written in format 3, which needs an llmcf version newer")


# --- one path: the build service --------------------------------------------------------------------------------------

def test_the_recipes_level_builds_and_counts_as_unchanged(service):
    service.installed = make_recipe(schema_version=2, options={"use_imatrix": True, "max_context_length": 4096,
                                                               "optimization": "speed"})
    service.start_recipe("r", local_paths=True)
    (req,) = service.framework.orchestrator.submitted
    assert req.optimization_level.value == "speed" and req.recipe["changed"] == []


def test_your_level_wins_and_is_a_changed_variable(service):
    service.installed = make_recipe(schema_version=2, options={"use_imatrix": True, "max_context_length": 4096,
                                                               "optimization": "speed"})
    from orchestrator.schemas.recipes import RecipeChoices
    service.start_recipe("r", local_paths=True, choices=RecipeChoices(optimization="size"))
    service.start_recipe("r", local_paths=True, choices=RecipeChoices(optimization="speed"))
    changed, same = service.framework.orchestrator.submitted
    assert changed.optimization_level.value == "size" and changed.recipe["changed"] == ["optimization"]
    assert same.optimization_level.value == "speed" and same.recipe["changed"] == []
    with pytest.raises(TypeError):
        service.start_recipe("r", local_paths=True, optimization="size")


def test_a_recipe_without_a_level_takes_the_board_profiles(service):
    from orchestrator.schemas.recipes import RecipeChoices
    request, recipe, _ = service.recipe_request("r")
    board_level = service.optimization_for(service.validate(request, local_paths=True))
    other = next(level for level in ("fast", "aggressive") if level != board_level)
    service.start_recipe("r", local_paths=True)
    service.start_recipe("r", local_paths=True, choices=RecipeChoices(optimization=other))
    plain, mine = service.framework.orchestrator.submitted
    assert plain.optimization_level.value == board_level and plain.recipe["changed"] == []
    assert mine.optimization_level.value == other and mine.recipe["changed"] == ["optimization"]


def test_a_changed_level_skips_the_reference_comparison(service):
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.builder import BuildEngine
    record = {**BuildService.recipe_record(service.installed, ["optimization"]), "target": "same"}
    config = SimpleNamespace(recipe=record, target_arch="Rockchip", toolchain_image=None)
    progress = SimpleNamespace(warnings=[], add_warning=lambda w: progress.warnings.append(w))
    out = BuildEngine._recipe_outcome(config, {"file": "m.gguf", "sha256": "f" * 64}, progress)
    assert out["reproduced"] is None and out["changed"] == ["optimization"] and progress.warnings == []


def test_a_level_changed_after_the_question_is_not_built(service):
    from orchestrator.Core.build_service import BuildServiceError
    service.installed = make_recipe(schema_version=2, options={"use_imatrix": True, "max_context_length": 4096,
                                                               "optimization": "speed"})
    request, recipe, _ = service.recipe_request("r")
    validated = service.validate(request, local_paths=True)
    expect = {"model": request.model, "revision": request.revision, "target": validated.target,
              "format": validated.format, "board": validated.board, "quantization": validated.quantization,
              "optimization": "balanced", "recipe_sha256": service.recipe_digest(recipe)}
    with pytest.raises(BuildServiceError) as e:
        service.start_recipe("r", local_paths=True, expect=expect)
    assert "optimization" in str(e.value) and service.framework.orchestrator.submitted == []
    service.start_recipe("r", local_paths=True, expect={**expect, "optimization": "speed"})
    assert len(service.framework.orchestrator.submitted) == 1


def test_a_later_format_installed_is_refused_with_the_version(service, tmp_path, monkeypatch):
    from orchestrator.Core.build_service import BuildService, BuildServiceError
    from orchestrator.Core.recipe_optimization import RecipeFormatError
    folder = tmp_path / "r"
    folder.mkdir()
    prov = SimpleNamespace(kind="recipe", id="r")
    service.framework.community_manager = SimpleNamespace(installed=lambda: [(folder, prov)],
                                                          cached_index=lambda revocations: None)
    import orchestrator.Core.community_manager as cmod
    monkeypatch.setattr(cmod, "trust_of_folder", lambda folder, index: (None, ""))
    monkeypatch.setattr(cmod, "read_recipe", lambda folder: (_ for _ in ()).throw(RecipeFormatError("needs X")))
    with pytest.raises(BuildServiceError) as e:
        BuildService.installed_recipe(service, "r")
    assert "needs X" in str(e.value) and "llm-cli update" in e.value.next


# --- the CLI ---------------------------------------------------------------------------------------------------------

def test_build_start_recipe_names_the_recipes_level(recipe_cli):
    recipe_cli.recipe = make_recipe(schema_version=2, options={"use_imatrix": True, "max_context_length": 4096,
                                                               "optimization": "speed"})
    result = recipe_cli.invoke("build", "start", "--recipe", "r", "-o", "-", quiet=False)
    assert result.code == 0, result.out
    assert "Optimization level: speed (the recipe's)." in result.out and "Built as the recipe pins it" in result.out
    result = recipe_cli.invoke("build", "start", "--recipe", "r", "--optimization", "size", "-o", "-", quiet=False)
    assert result.code == 0, result.out
    assert "Optimization level: size (yours; the recipe's: speed)." in result.out
    assert "Changed from the recipe: optimization." in result.out
    first, second = recipe_cli.submitted
    assert first.optimization_level.value == "speed" and first.recipe["changed"] == []
    assert second.optimization_level.value == "size" and second.recipe["changed"] == ["optimization"]


def test_build_start_recipe_without_a_level_says_the_board_profiles(recipe_cli):
    result = recipe_cli.invoke("build", "start", "--recipe", "r", "-o", "-", quiet=False)
    assert result.code == 0, result.out
    assert "(the board profile's; the recipe names none)" in result.out
    assert recipe_cli.submitted[0].recipe["changed"] == []


# --- the Builder -----------------------------------------------------------------------------------------------------

def test_the_builder_starts_with_the_recipes_level(tab):
    from test_review_capabilities_paths import submit
    tab.recipe = make_recipe(schema_version=2, options={"use_imatrix": True, "max_context_length": 4096,
                                                        "optimization": "speed"})
    tab.apply_recipe(tab.recipe)
    assert tab.opt_combo.currentText() == "SPEED" and "optimization level speed (the recipe's)" in \
        tab.log_area.toPlainText()
    req = submit(tab)
    assert req.optimization_level.value == "speed" and req.recipe["changed"] == []
    tab.opt_combo.setCurrentIndex(tab.opt_combo.findText("SIZE"))
    req = submit(tab)
    assert req.optimization_level.value == "size" and req.recipe["changed"] == ["optimization"]
    assert "optimization level size (yours; the recipe's: speed)" in tab.log_area.toPlainText()


def test_the_builder_keeps_the_board_profiles_level_for_a_recipe_without_one(tab):
    from test_review_capabilities_paths import submit
    tab.apply_recipe(tab.recipe)
    shown = tab.opt_combo.currentText().lower()
    req = submit(tab)
    assert req.optimization_level.value == shown and req.recipe["changed"] == []
    assert "optimization level not named (the board profile's)" in tab.log_area.toPlainText()

