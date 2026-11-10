#!/usr/bin/env python3
"""
The optimization level in recipes (DESIGN "Community, recipes and packs"): the whole rule in one place, so the recipe
schema (schemas/recipes.py), BuildService.start_recipe, the Builder, `llm-cli build start --recipe` and the tool
start_recipe_build share it instead of each knowing it.

- The field: a recipe's optional `options.optimization`, one of the levels the build knows (OPTIMIZATION_LEVELS in
  schemas/targets.py). A recipe made from a finished build takes it from its build_info.json (the build's recorded
  options).
- The format version: a recipe that names a level is written as format 2; one without stays format 1, byte for byte as
  before, so older llmcf versions still read it. This version reads formats 1 and 2; a later format is refused with a
  sentence naming the llmcf version it needs (FORMAT_NEEDS).
- In the build: the level is a variable like the others. The person's choice wins, else the recipe's level, else the
  board profile's level as for any build (board_profiles.optimization_for). The recipe's value of the variable is what
  a build that leaves it takes (the recipe's level, else the board profile's), so a level that differs from it is a
  changed variable and the result is not compared with the recipe's reference.

This module imports no other part of the recipe code (schemas/recipes.py imports it).
"""

from typing import Any, Dict, Mapping, Optional, Tuple

from orchestrator.schemas.targets import OPTIMIZATION_LEVELS

# The variable's name among RECIPE_VARIABLES and in the recipe record's "changed" list.
VARIABLE = "optimization"

# Each recipe format and the llmcf version that first reads it, in words. A later format adds its row here when it is
# made, so a recipe in it tells the person exactly which llmcf version to get.
FORMAT_NEEDS: Dict[int, str] = {
    1: "any llmcf version that builds recipes",
    2: "llmcf 3.4.0 or later",   # options.optimization
}
FORMAT_WITHOUT_LEVEL = 1
FORMAT_WITH_LEVEL = 2
READS = tuple(sorted(FORMAT_NEEDS))

# Where the level came from (resolve): the person, the recipe, the board profile.
YOURS, RECIPES, BOARDS = "yours", "recipe", "board"


class RecipeFormatError(ValueError):
    """A recipe.yml in a format this llmcf version does not read; the message names the version it needs."""


def format_sentence(version: Any) -> str:
    """Why a recipe in format `version` is not read here, naming the llmcf version it needs."""
    from orchestrator import __version__
    known = ", ".join(str(v) for v in READS)
    needs = FORMAT_NEEDS.get(version) if isinstance(version, int) else None
    if needs is None and isinstance(version, int) and not isinstance(version, bool) and version > max(READS):
        needs = f"an llmcf version newer than {__version__}"
    if needs is None:
        return (f"The recipe names format {version!r}, which is no recipe format; llmcf {__version__} reads formats "
                f"{known}.")
    return (f"The recipe is written in format {version}, which needs {needs}; this llmcf {__version__} reads recipe "
            f"formats {known}. Update llmcf (llm-cli update) to build it.")


def check_format(version: Any) -> int:
    """The recipe format `version` when this version reads it; RecipeFormatError naming the needed version otherwise."""
    if isinstance(version, bool) or not isinstance(version, int) or version not in READS:
        raise RecipeFormatError(format_sentence(version))
    return version


def check_raw(data: Any) -> None:
    """Checks the format of a recipe.yml as read (before its schema): a later format is refused with the sentence
    naming the llmcf version it needs instead of a schema error about fields this version does not know."""
    if isinstance(data, Mapping) and "schema_version" in data:
        check_format(data.get("schema_version"))


def format_for(level: Optional[str]) -> int:
    """The format a recipe is written in: 2 when it names a level, else 1 (readable by older versions)."""
    return FORMAT_WITH_LEVEL if level else FORMAT_WITHOUT_LEVEL


def check_level_format(version: int, level: Optional[str]) -> None:
    """A format-1 recipe cannot name a level: older versions refuse the field, so such a file is no valid recipe."""
    if level and version < FORMAT_WITH_LEVEL:
        raise ValueError(f"a recipe that names an optimization level is format {FORMAT_WITH_LEVEL} "
                         f"(schema_version: {FORMAT_WITH_LEVEL}), not format {version}")


def normalize(level: Any) -> Optional[str]:
    """A known level in lower case, or None."""
    text = str(level or "").strip().lower()
    return text if text in OPTIMIZATION_LEVELS else None


def level_of(recipe: Any) -> Optional[str]:
    """The level a recipe names, or None (a format-1 recipe)."""
    return normalize(getattr(getattr(recipe, "options", None), "optimization", None))


def level_from_build_info(info: Mapping[str, Any]) -> Optional[str]:
    """The level a finished build was made with: its recorded options (build_identity's build_options), else a
    top-level "optimization"; None for a build that recorded none (an older build: its recipe stays format 1)."""
    from orchestrator.Core.build_identity import MANIFEST_KEY
    options = info.get(MANIFEST_KEY)
    if isinstance(options, Mapping) and normalize(options.get(VARIABLE)):
        return normalize(options.get(VARIABLE))
    return normalize(info.get(VARIABLE))


def recipe_default(recipe: Any, board_level: str) -> Tuple[str, str]:
    """(level, source) a recipe build takes when the person leaves the level: the recipe's, else the board's."""
    named = level_of(recipe)
    return (named, RECIPES) if named else (normalize(board_level) or str(board_level), BOARDS)


def resolve(recipe: Any, chosen: Optional[str], board_level: str) -> Tuple[str, str]:
    """(level, source) of a recipe build: the person's choice, else the recipe's level, else the board profile's."""
    mine = normalize(chosen)
    if mine:
        return mine, YOURS
    return recipe_default(recipe, board_level)


def changed(recipe: Any, level: str, board_level: str) -> bool:
    """True when the build's level is not the one the recipe's build takes (a changed variable: no comparison)."""
    return normalize(level) != recipe_default(recipe, board_level)[0]


def describe(recipe: Any, level: str, source: str, board_level: str) -> str:
    """The level in words for the CLI's summary, the start question and the Builder log: which level and whether it is
    the recipe's."""
    named = level_of(recipe)
    if source == RECIPES:
        return f"{level} (the recipe's)"
    if source == YOURS:
        if named:
            return f"{level} (the recipe's)" if level == named else f"{level} (yours; the recipe's: {named})"
        return (f"{level} (yours; the recipe names none, the board profile's: {board_level})"
                if level != board_level else f"{level} (yours, the board profile's; the recipe names none)")
    return f"{level} (the board profile's; the recipe names none)"


def canonical(data: Dict[str, Any]) -> Dict[str, Any]:
    """A recipe's JSON dump without the format-2 field when it is empty, so the digest of a format-1 recipe stays what
    it was before the field existed (BuildService.recipe_digest)."""
    options = data.get("options")
    if isinstance(options, dict) and options.get(VARIABLE) is None and VARIABLE in options:
        data = {**data, "options": {k: v for k, v in options.items() if k != VARIABLE}}
    return data


__all__ = ["BOARDS", "FORMAT_NEEDS", "FORMAT_WITHOUT_LEVEL", "FORMAT_WITH_LEVEL", "READS", "RECIPES",
           "RecipeFormatError", "VARIABLE", "YOURS", "canonical", "changed", "check_format", "check_level_format",
           "check_raw", "describe", "format_for", "format_sentence", "level_from_build_info", "level_of", "normalize",
           "recipe_default", "resolve"]
