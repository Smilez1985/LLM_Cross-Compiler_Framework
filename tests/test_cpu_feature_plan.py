"""The one CPU planner (orchestrator/runtime/build/recipe.py) on fixture probes of every architecture ggml has CPU
options for: the plans are pinned, every feature the board has is either used or named as unusable with the reason,
and no plan's build ID changes for options that stay the same.

Boards: RK3566 (Cortex-A55: dotprod and fp16), RK3588 (Cortex-A76 + A55: the same), an ARMv9 board with SVE2, I8MM,
BF16 and SME; PCs: AVX2 only, AVX-512 (Skylake-X / Ice Lake), AMX (Sapphire Rapids), Xeon Phi (AVX-512 F without
BW); RISC-V with V, ZFH and ZVFH, LoongArch, POWER9 and s390x, which the recipe refuses with the features found.
"""

import json

import pytest

from orchestrator.runtime.build.recipe import (ARM_ARCH_TAGS, BuildPlan, PlanError, ToolchainInfo, arm_arch, arm_plan,
                                               build_id_of, plan_build, sme_problem)
from orchestrator.runtime.profile import load_schema
from test_build_recipe import CATALOG, GOLDEN, LINUX_BUILD_HOST, RK3566, Z1_LINUX, changed, probe

SCHEMA = load_schema()
RK3588 = probe("schema2_rk3588_devuan_sysvinit.txt")


def with_flags(profile, arch, flags):
    """The profile with CPU_FLAGS_ALL and every CPU feature key derived from `flags`, as the probe derives them."""
    values = SCHEMA.features_from_flags(arch, flags)
    return changed(profile, CPU_FLAGS_ALL=",".join(flags), **values)


ARMV9_FLAGS = ("fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop "
               "sha3 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 svei8mm svebf16 "
               "i8mm bf16 bti sme").split()
ARMV9 = with_flags(RK3566, "arm64", ARMV9_FLAGS)
ARMV9_GLIBC_241 = changed(ARMV9, LIBC_VERSION="2.41")    # the Debian 13 builder base, GCC 14.2

BASE_X86 = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ht "
            "syscall nx lm sse3 ssse3 sse4_1 sse4_2 popcnt").split()
AVX2_ONLY = with_flags(Z1_LINUX, "x86_64", BASE_X86 + ["avx", "f16c", "fma", "avx2", "bmi2"])
SKYLAKE_X = with_flags(Z1_LINUX, "x86_64", BASE_X86 + ["avx", "f16c", "fma", "avx2", "bmi2", "avx512f", "avx512cd",
                                                       "avx512vl", "avx512dq", "avx512bw"])
ICE_LAKE = with_flags(Z1_LINUX, "x86_64", BASE_X86 + ["avx", "f16c", "fma", "avx2", "bmi2", "avx512f", "avx512cd",
                                                      "avx512vl", "avx512dq", "avx512bw", "avx512vbmi",
                                                      "avx512_vnni"])
SAPPHIRE_RAPIDS = with_flags(Z1_LINUX, "x86_64", BASE_X86 + [
    "avx", "f16c", "fma", "avx2", "bmi2", "avx512f", "avx512cd", "avx512vl", "avx512dq", "avx512bw", "avx512vbmi",
    "avx512_vnni", "avx512_bf16", "avx_vnni", "amx_tile", "amx_int8", "amx_bf16"])
XEON_PHI = with_flags(Z1_LINUX, "x86_64", BASE_X86 + ["avx", "f16c", "fma", "avx2", "bmi2", "avx512f", "avx512cd",
                                                      "avx512er", "avx512pf"])


def device_plan(profile):
    return plan_build(profile, purpose="device-runtime", placement="cross", backend="cpu", catalog=CATALOG,
                      toolchain=LINUX_BUILD_HOST)


# --- pinned plans per board ------------------------------------------------------------------------------------

# board -> (GGML_CPU_ARM_ARCH or the x86 options that are ON, used, unusable features)
EXPECTED = {
    "rk3566": (RK3566, "armv8.2-a+dotprod+fp16", ["DOTPROD", "FP16_ARITH"], []),
    "rk3588": (RK3588, "armv8.2-a+dotprod+fp16", ["DOTPROD", "FP16_ARITH"], []),
    # Debian 12's GCC 12.2 does not know +sme: everything else is built, SME and BF16 are named
    "armv9-gcc12": (ARMV9, "armv8.2-a+dotprod+fp16+sve+i8mm+sve2",
                    ["DOTPROD", "FP16_ARITH", "SVE", "I8MM", "SVE2"], ["SME", "BF16_ARM"]),
    "armv9-gcc14": (ARMV9_GLIBC_241, "armv8.2-a+dotprod+fp16+sve+i8mm+sve2+sme",
                    ["DOTPROD", "FP16_ARITH", "SVE", "I8MM", "SVE2", "SME"], ["BF16_ARM"]),
    "avx2-only": (AVX2_ONLY, ["GGML_SSE42", "GGML_AVX", "GGML_AVX2", "GGML_BMI2", "GGML_F16C", "GGML_FMA"],
                  ["SSE42", "AVX", "AVX2", "BMI2", "F16C", "FMA"], []),
    "skylake-x": (SKYLAKE_X, ["GGML_SSE42", "GGML_AVX", "GGML_AVX2", "GGML_BMI2", "GGML_F16C", "GGML_FMA",
                              "GGML_AVX512"], ["SSE42", "AVX", "AVX2", "BMI2", "F16C", "FMA", "AVX512"], []),
    "ice-lake": (ICE_LAKE, ["GGML_SSE42", "GGML_AVX", "GGML_AVX2", "GGML_BMI2", "GGML_F16C", "GGML_FMA",
                            "GGML_AVX512", "GGML_AVX512_VBMI", "GGML_AVX512_VNNI"],
                 ["SSE42", "AVX", "AVX2", "BMI2", "F16C", "FMA", "AVX512", "AVX512_VBMI", "AVX512_VNNI"], []),
    "sapphire-rapids": (SAPPHIRE_RAPIDS, ["GGML_SSE42", "GGML_AVX", "GGML_AVX2", "GGML_BMI2", "GGML_F16C", "GGML_FMA",
                                          "GGML_AVX512", "GGML_AVX512_VBMI", "GGML_AVX512_VNNI", "GGML_AVX512_BF16",
                                          "GGML_AVX_VNNI", "GGML_AMX_TILE", "GGML_AMX_INT8", "GGML_AMX_BF16"],
                        ["SSE42", "AVX", "AVX2", "BMI2", "F16C", "FMA", "AVX512", "AVX512_VBMI", "AVX512_VNNI",
                         "AVX512_BF16", "AVX_VNNI", "AMX_TILE", "AMX_INT8", "AMX_BF16"], []),
    # AVX-512 F without BW, VL, DQ: the AVX2 plan, AVX-512 named as unusable with the parts it lacks
    "xeon-phi": (XEON_PHI, ["GGML_SSE42", "GGML_AVX", "GGML_AVX2", "GGML_BMI2", "GGML_F16C", "GGML_FMA"],
                 ["SSE42", "AVX", "AVX2", "BMI2", "F16C", "FMA"], ["AVX512"]),
}


@pytest.mark.parametrize("board", sorted(EXPECTED))
def test_the_plan_of_each_board_is_pinned(board):
    profile, cpu, used, unusable = EXPECTED[board]
    plan = device_plan(profile)
    options = plan.options()
    if isinstance(cpu, str):
        assert options["GGML_CPU_ARM_ARCH"] == cpu
        assert plan.verify["compile_check_flags"] == [f"-march={cpu}"]
    else:
        assert [o for o, v in sorted(options.items()) if o.startswith(("GGML_SSE", "GGML_AVX", "GGML_BMI", "GGML_F16",
                                                                        "GGML_FMA", "GGML_AMX")) and v == "ON"] \
            == sorted(cpu)
    assert plan.cpu_features["used"] == used
    assert [u["feature"] for u in plan.cpu_features["unusable"]] == unusable
    assert all(u["reason"] for u in plan.cpu_features["unusable"])


def test_no_feature_the_board_has_is_lost_silently():
    """Every CPU feature the probe confirms is in used or in unusable (with the reason), for every board."""
    for board, (profile, _cpu, _used, _unusable) in EXPECTED.items():
        plan = device_plan(profile)
        named = set(plan.cpu_features["used"]) | {u["feature"] for u in plan.cpu_features["unusable"]}
        arch = "x86" if profile.arch == "x86_64" else "arm"
        confirmed = {k[len("SUPPORTS_"):] for k in SCHEMA.keys if SCHEMA.keys[k].group == arch
                     and k != "SUPPORTS_NEON" and profile.flag(k) == "ON"}  # NEON: the ARMv8-A baseline
        assert confirmed <= named, (board, confirmed - named)


def test_the_tags_follow_ggml_s_arch_tags_order():
    assert [tag for _key, tag in ARM_ARCH_TAGS] == ["dotprod", "fp16", "sve", "i8mm", "sve2", "sme"]
    both = changed(RK3566, SUPPORTS_SVE="ON", SUPPORTS_I8MM="ON")
    assert arm_arch(both) == "armv8.2-a+dotprod+fp16+sve+i8mm"


def test_sme_needs_a_compiler_that_knows_it():
    sme = changed(RK3566, SUPPORTS_SME="ON")
    assert arm_arch(sme) == "armv8.2-a+dotprod+fp16"          # compiler not known: no +sme
    assert arm_arch(sme, ("gcc", "13.3")) == "armv8.2-a+dotprod+fp16"
    assert arm_arch(sme, ("gcc", "14.2")) == "armv8.2-a+dotprod+fp16+sme"
    assert arm_arch(sme, ("clang", "18.1.3")) == "armv8.2-a+dotprod+fp16+sme"
    assert "compiler too old" in sme_problem(("gcc", "12.2")) and sme_problem(("gcc", "14.2")) is None
    _march, _used, unusable = arm_plan(sme, ("gcc", "12.2"))
    assert unusable == [{"feature": "SME", "reason": sme_problem(("gcc", "12.2"))}]


def test_an_arm_board_without_any_feature_key_gets_armv8_a():
    plain = changed(RK3566, SUPPORTS_DOTPROD="OFF", SUPPORTS_FP16_ARITH="OFF")
    assert device_plan(plain).options()["GGML_CPU_ARM_ARCH"] == "armv8-a"


def test_the_plan_views_name_what_is_used_and_what_is_not():
    from orchestrator.runtime.build.recipe import cpu_feature_lines
    lines = cpu_feature_lines(device_plan(ARMV9).cpu_features)
    assert lines[0] == "CPU features used: DOTPROD, FP16_ARITH, SVE, I8MM, SVE2"
    assert lines[1].startswith("The machine has SME, the build cannot use it: compiler too old: +sme needs GCC 14")
    assert lines[2].startswith("The machine has BF16_ARM, the build cannot use it: the pin has no BF16 kernel for ARM")
    assert cpu_feature_lines({"used": [], "unusable": []}) == ["CPU features used: none beyond the base level"]


# --- architectures the recipe does not build ------------------------------------------------------------------

def other_arch(arch, **features):
    text = (f"PROBE_SCHEMA=2\nPROBE_TOOL=python\nPROBE_TOOL_VERSION=3.2.0\nGENERATED_AT=2026-10-30T08:00:00Z\n"
            f"OS=linux\nKERNEL=6.6.0-example\nARCH={arch}\nLIBC=glibc\nLIBC_VERSION=2.39\nCPU_CORES_LOGICAL=4\n"
            "RAM_TOTAL_MB=8000\nGPU_COUNT=0\n")
    bools = {k: "OFF" for k, s in SCHEMA.keys.items() if s.type == "bool"}
    bools.update(features)
    from orchestrator.runtime.profile import parse_profile
    return parse_profile(text + "".join(f"{k}={v}\n" for k, v in bools.items()))


@pytest.mark.parametrize("arch, features, named", [
    ("riscv64", {"SUPPORTS_RVV": "ON", "SUPPORTS_RV_ZFH": "ON", "SUPPORTS_RV_ZVFH": "ON"},
     ["RVV (GGML_RVV: v (-march=rv64gcv))", "RV_ZFH (GGML_RV_ZFH: _zfh)", "RV_ZVFH (GGML_RV_ZVFH: _zvfh)"]),
    ("loongarch64", {"SUPPORTS_LSX": "ON", "SUPPORTS_LASX": "ON"},
     ["LSX (GGML_LSX: -mlsx)", "LASX (GGML_LASX: -mlasx)"]),
    ("ppc64le", {"SUPPORTS_VSX": "ON", "POWER_CPU_TYPE": "power9"},
     ["power9 (GGML_CPU_POWERPC_CPUTYPE=power9)", "VSX (VSX: -mvsx)"]),
    ("s390x", {"SUPPORTS_VXE": "ON", "SUPPORTS_VXE2": "ON"}, ["VXE2 (GGML_VXE: -mvx -mzvector)"]),
])
def test_an_architecture_the_recipe_does_not_build_is_refused_with_the_features_found(arch, features, named):
    with pytest.raises(PlanError) as e:
        device_plan(other_arch(arch, **features))
    message = str(e.value)
    assert f"no plan for architecture '{arch}'" in message and "the probe found these CPU features" in message
    for item in named:
        assert item in message


# --- the plan format and the build ID ---------------------------------------------------------------------------

def test_cpu_features_stay_out_of_the_build_id():
    plan = device_plan(RK3566)
    assert "cpu_features" not in plan.id_inputs()
    golden = json.loads((GOLDEN / "rk3566_dietpi_cross.json").read_text(encoding="utf-8"))
    assert plan.build_id == golden["build_id"] == build_id_of(plan.id_inputs())


def test_a_plan_round_trips_with_its_cpu_features_and_an_older_plan_still_loads():
    plan = device_plan(ARMV9)
    again = BuildPlan.from_dict(json.loads(plan.to_json()))
    assert again.cpu_features == plan.cpu_features and again.build_id == plan.build_id
    older = plan.as_dict()
    del older["cpu_features"]     # a build.json written before 3.3.0
    assert BuildPlan.from_dict(older).cpu_features == {"used": [], "unusable": []}


@pytest.mark.parametrize("bad", [{"used": ["dotprod; rm"]}, {"unusable": [{"feature": "SME"}]}, {"used": "SVE"}])
def test_an_imported_plan_with_odd_cpu_features_is_refused(bad):
    data = device_plan(RK3566).as_dict()
    data["cpu_features"] = bad
    with pytest.raises(PlanError, match="cpu_features"):
        BuildPlan.from_dict(data)


def test_a_native_arm_build_keeps_its_build_id_when_the_board_has_no_later_feature():
    """The native identity records SVE2/SME only when ON: the ID of every earlier native ARM build stays."""
    from orchestrator.runtime.build.recipe import _cpu_identity, _Target
    target = _Target("linux", "arm64", "glibc", "2.36")
    assert set(_cpu_identity(RK3566, target)["features"]) == {"SUPPORTS_DOTPROD", "SUPPORTS_FP16_ARITH",
                                                             "SUPPORTS_I8MM", "SUPPORTS_SVE"}
    assert _cpu_identity(ARMV9, target)["features"]["SUPPORTS_SME"] == "ON"


def test_a_build_on_the_board_itself_names_bf16_as_unusable():
    board = ToolchainInfo(host_os="linux", host_arch="arm64", compiler="gcc-12.2.0", cmake="3.25.1")
    plan = plan_build(ARMV9, purpose="device-runtime", placement="on-device", backend="cpu", catalog=CATALOG,
                      toolchain=board)
    assert plan.options()["GGML_NATIVE"] == "ON"
    assert [u["feature"] for u in plan.cpu_features["unusable"]] == ["BF16_ARM"]
    assert "SVE2" in plan.cpu_features["used"] and "SME" in plan.cpu_features["used"]
