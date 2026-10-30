"""Coverage of the pinned llama.cpp's CPU options: every option and tag of ggml's CPU backend at the pin is in the
inventory (orchestrator/runtime/build/ggml_cpu_options.py), every probe key the inventory names is in the probe
schema, and every row the inventory marks as planned is planned by the one planner (recipe.py).

The pin's CPU option lines are committed as excerpts (tests/fixtures/llama_cpp/<tag>/, with the commit in a header). A
pin bump without new excerpts fails (the commit no longer matches configs/llama_runtime.yml), and new excerpts with an
option the inventory does not list fail as well, so the probe and the planner are extended before a pin bump lands.
"""

import re
from pathlib import Path

import pytest
import yaml

from orchestrator.runtime.build import ggml_cpu_options as inv
from orchestrator.runtime.build import recipe
from orchestrator.runtime.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[1]
PIN = load_catalog().llama_cpp.source
EXCERPTS = ROOT / "tests" / "fixtures" / "llama_cpp" / PIN.tag
SCHEMA_KEYS = yaml.safe_load((ROOT / "configs" / "hardware_probe_schema.yml").read_text(encoding="utf-8"))["keys"]

FILES = {"options": "ggml_CMakeLists.txt", "cpu": "ggml_src_ggml-cpu_CMakeLists.txt",
         "variants": "ggml_src_CMakeLists.txt"}

# Names in ggml-cpu's conditions that are no CPU option: internal variables of the variant builds and the probes,
# the system and compiler checks, and options of other parts (each with why it is no CPU feature).
NOT_CPU_OPTIONS = {
    "GGML_SYSTEM_ARCH": "the architecture CMake detected, not an option",
    "GGML_ARM_MSVC": "set by ggml-cpu itself for MSVC on ARM",
    "GGML_BACKEND_DL": "the dynamic backend loading the plan sets for the CPU variants",
    "GGML_OPENMP_ENABLED": "the result of the OpenMP check (GGML_OPENMP is planned by the recipe)",
    "GGML_LLAMAFILE": "the llamafile SGEMM kernels, no CPU feature (upstream default)",
    "GGML_CPU_KLEIDIAI_AARCH64": "set by ggml-cpu itself when GGML_CPU_KLEIDIAI may be used",
}
INTERNAL = ("GGML_INTERNAL_", "GGML_MACHINE_SUPPORTS_", "GGML_COMPILER_SUPPORTS_")


def excerpt(kind: str) -> str:
    path = EXCERPTS / FILES[kind]
    assert path.is_file(), (f"{path.relative_to(ROOT)} is missing: commit the excerpt of the pinned llama.cpp "
                            f"({PIN.tag}) before the pin moves")
    return path.read_text(encoding="utf-8")


def cmake_options(text: str):
    """The options and cache strings of ggml/CMakeLists.txt's "instruction set specific" block."""
    return set(re.findall(r"^\s*option\((GGML_[A-Z0-9_]+)\s", text, re.M)) | set(
        re.findall(r'^\s*set\((GGML_[A-Z0-9_]+)\s+""\s+CACHE STRING', text, re.M))


def cpu_conditions(text: str):
    """GGML_* names ggml-cpu/CMakeLists.txt tests in its if/elseif conditions, without its internal ones."""
    names = set()
    for condition in re.findall(r"^\s*(?:else)?if\s*\((.*)\)", text, re.M):
        names |= set(re.findall(r"\bGGML_[A-Z0-9_]+", condition))
    return {n for n in names if not n.startswith(INTERNAL) and n not in NOT_CPU_OPTIONS}


def arm_tags(text: str):
    """The -march tags of ggml-cpu: ARCH_TAGS of the CPU variants and the tags of check_arm_feature."""
    return set(re.findall(r'ARCH_TAGS "\$\{ARCH_TAGS\}\+([a-z0-9]+)"', text)) | set(
        re.findall(r"check_arm_feature\((\w+)\s", text))


def variant_features(text: str):
    """The feature words of ggml/src/CMakeLists.txt's ggml_add_cpu_backend_variant(name WORD...) lines."""
    words = set()
    for args in re.findall(r"ggml_add_cpu_backend_variant\(\s*[^\s)]+([^)]*)\)", text):
        words |= set(args.split())
    return words


def uncovered(options_text: str, cpu_text: str, variants_text: str):
    """What the pin's CPU option lines have that the inventory does not list, as sorted text."""
    names = {r.name for r in inv.OPTIONS}
    tags = {r.name.lstrip("+") for r in inv.OPTIONS if r.kind == inv.ARM_ARCH_TAG}
    variants = {r.variant for r in inv.OPTIONS if r.variant}
    missing = [f"option {n}" for n in cmake_options(options_text) | cpu_conditions(cpu_text) if n not in names]
    missing += [f"ARM tag +{t}" for t in arm_tags(cpu_text) if t not in tags]
    missing += [f"variant feature {w}" for w in variant_features(variants_text) if w not in variants]
    return sorted(missing)


# --- the excerpts are the pin's ---------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(FILES))
def test_the_excerpts_are_of_the_pinned_commit(kind):
    first = excerpt(kind).splitlines()[0]
    assert f"llama.cpp commit {PIN.commit} (tag {PIN.tag})" in first, \
        f"{FILES[kind]} is of another commit than configs/llama_runtime.yml: replace the excerpts at the pin bump"


def test_the_inventory_is_of_the_pinned_commit():
    assert (inv.PIN_COMMIT, inv.PIN_TAG) == (PIN.commit, PIN.tag), \
        "ggml_cpu_options.py names another commit: re-check every CPU option of the new pin and update it"


def test_the_excerpts_hold_what_the_coverage_reads():
    assert {"GGML_AVX2", "GGML_RVV", "GGML_LSX", "GGML_VXE", "GGML_CPU_ARM_ARCH"} <= cmake_options(excerpt("options"))
    assert {"dotprod", "fp16", "sve", "i8mm", "sve2", "sme", "nosve", "fma"} <= arm_tags(excerpt("cpu"))
    assert {"AVX512_BF16", "SME", "VSX", "VXE2", "NNPA", "RVV"} <= variant_features(excerpt("variants"))
    assert "GGML_RV_ZBA" in cpu_conditions(excerpt("cpu"))   # read by ggml-cpu, declared nowhere


# --- the coverage itself ----------------------------------------------------------------------------------------

def test_every_cpu_option_and_tag_of_the_pin_is_in_the_inventory():
    assert uncovered(excerpt("options"), excerpt("cpu"), excerpt("variants")) == []


def test_a_new_option_tag_or_variant_feature_of_a_later_pin_is_found():
    options = excerpt("options") + 'option(GGML_AVX10          "ggml: enable AVX10"          OFF)\n'
    cpu = excerpt("cpu") + '        set(ARCH_TAGS "${ARCH_TAGS}+sme2")\n        if (GGML_RV_ZVKNED)\n'
    variants = excerpt("variants") + "        ggml_add_cpu_backend_variant(zen6    SSE42 AVX10)\n"
    assert uncovered(options, cpu, variants) == ["ARM tag +sme2", "option GGML_AVX10", "option GGML_RV_ZVKNED",
                                                 "variant feature AVX10"]


def test_every_probe_key_of_the_inventory_is_in_the_schema():
    for row in inv.OPTIONS:
        if row.probe_key:
            assert row.probe_key in SCHEMA_KEYS, (row.name, row.probe_key)
            only = SCHEMA_KEYS[row.probe_key].get("only_arch") or []
            assert row.arch in only, (row.name, row.probe_key, only)
        if row.builds_on:
            assert row.builds_on in SCHEMA_KEYS, (row.name, row.builds_on)


def test_every_row_has_a_state_and_every_unusable_row_a_reason():
    for row in inv.OPTIONS:
        assert row.state in (inv.PLANNED, inv.NOT_USABLE), row.name
        assert row.state == inv.PLANNED or row.reason, row.name
        assert row.kind in (inv.CMAKE_OPTION, inv.ARM_ARCH_TAG, inv.POWER_CPU_TYPE, inv.VARIANT_FEATURE), row.name


# --- planned rows are the planner's ----------------------------------------------------------------------------

def test_the_planned_x86_rows_are_the_recipe_s_tables():
    rows = [r for r in inv.rows("x86_64") if r.state == inv.PLANNED]
    assert {r.probe_key: r.name for r in rows} == dict(recipe.X86_FEATURES)
    assert {r.probe_key: r.builds_on for r in rows if r.builds_on} == dict(recipe._X86_NEEDS)
    assert {r.name: tuple(r.flag.split()) for r in rows} == dict(recipe._X86_FLAGS)


def test_the_arm_tags_are_the_recipe_s():
    planned = [(r.probe_key, r.name.lstrip("+")) for r in inv.rows("arm64", inv.ARM_ARCH_TAG)
               if r.state == inv.PLANNED]
    assert planned == list(recipe.ARM_ARCH_TAGS)
    assert set(recipe.ARM_FEATURES) <= {k for k, _ in planned}
    unusable = {r.probe_key for r in inv.rows("arm64", inv.ARM_ARCH_TAG) if r.state == inv.NOT_USABLE and r.probe_key}
    assert unusable == set(recipe.ARM_UNUSABLE) == {"SUPPORTS_BF16_ARM"}


def test_every_cpu_feature_key_of_the_schema_is_decided_by_the_inventory():
    """No CPU feature the probe reads is left without a ggml option or a reason (NEON is ARMv8-A's baseline)."""
    decided = {r.probe_key for r in inv.OPTIONS if r.probe_key} | {r.builds_on for r in inv.OPTIONS if r.builds_on}
    groups = ("x86", "arm", "riscv", "loongarch", "power", "s390x")
    keys = {k for k, v in SCHEMA_KEYS.items() if v["group"] in groups}
    assert keys - decided == {"SUPPORTS_NEON"}


def test_the_architectures_the_recipe_does_not_build_are_not_planned():
    assert set(inv.PLANNED_ARCHES) == set(recipe._SUPPORTED_ARCH)
    for row in inv.OPTIONS:
        if row.arch not in inv.PLANNED_ARCHES + ("any",):
            assert row.state == inv.NOT_USABLE and "no plan for" in row.reason, row.name
