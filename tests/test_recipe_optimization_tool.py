"""The optimization level of a recipe in the tool start_recipe_build (Core/recipe_optimization.py): a format-2 recipe
installed through the community pack checks builds with its level, the question names the level and whether it is the
recipe's, and the caller's `optimization` wins and is a changed variable."""

import pytest

pytest.importorskip("mcp")

from test_builds_grants import ask, install_recipe, recipes, zoo  # noqa: E402,F401 - the fixtures
from test_hf_tools import env  # noqa: E402,F401 - the Hugging Face tools' environment

LEVEL = {"use_imatrix": False, "max_context_length": 4096, "optimization": "speed"}


def test_a_format_2_recipe_is_installed_and_built_with_its_level(recipes):
    folder = install_recipe(recipes, schema_version=2, options=LEVEL)
    assert '"schema_version": 2' in (folder / "recipe.yml").read_text(), "the pack checks accepted it"
    _, shown = ask(recipes, "start_recipe_build", {"recipe_id": "demo-recipe"})
    assert "Optimization level: speed (the recipe's)" in shown[0]
    assert "Changed from the recipe: nothing" in shown[0]
    request = recipes.orch.requests[-1]
    assert request.optimization_level.value == "speed" and request.recipe["changed"] == []


def test_the_callers_level_wins_and_is_a_changed_variable(recipes):
    install_recipe(recipes, schema_version=2, options=LEVEL)
    _, shown = ask(recipes, "start_recipe_build", {"recipe_id": "demo-recipe", "optimization": "size"})
    assert "Optimization level: size (yours; the recipe's: speed)" in shown[0]
    assert "Changed from the recipe: optimization" in shown[0]
    request = recipes.orch.requests[-1]
    assert request.optimization_level.value == "size" and request.recipe["changed"] == ["optimization"]


def test_a_recipe_without_a_level_takes_the_board_profiles(recipes):
    install_recipe(recipes)
    _, shown = ask(recipes, "start_recipe_build", {"recipe_id": "demo-recipe"})
    assert "(the board profile's; the recipe names none)" in shown[0]
    assert recipes.orch.requests[-1].recipe["changed"] == []
