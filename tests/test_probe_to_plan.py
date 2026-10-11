"""
Stage 1 of the self-built runtime end to end: the hardware probe's real output becomes a llama.cpp build plan.

The probe runs on the machine that will run llama.cpp (decision D14): scripts/hardware_probe.py, or the manual
fallback scripts/hardware_probe.sh. orchestrator/runtime/profile.py parses its file, and
orchestrator/runtime/build/recipe.py turns the parsed profile into a plan whose architecture, C library and CPU options
follow that machine (decision 8). These tests run both emitters on the fixture machines of tests/test_hardware_probe.py
and the Python probe on this machine, and check that what they write gives a valid plan.

A probe of a fixture sysroot cannot ask the machine's C library for its version (os.confstr, getconf and the musl
loader belong to the computer that runs the test), so it leaves LIBC_VERSION out. The tests add the version the
board's own run reports; without it a cross build is refused. No network: external tools are fakes or absent.
"""

import json
import os
from pathlib import Path

import pytest

from orchestrator.runtime.build.recipe import (ARM_FEATURES, DOWNLOAD_OPTIONS_OFF, NEVER_SET, X86_FEATURES,
                                               BuildPlan, PlanError, ToolchainInfo, check_download_options,
                                               choose_build_base, plan_build, plan_variants)
from orchestrator.runtime.catalog import load_catalog, version_tuple
from orchestrator.runtime.profile import (HardwareProfile, ProfileStore, new_device_id, parse_profile,
                                          parse_profile_json, read_profile)

import test_hardware_probe as fixtures  # the fixture machines and the emitter runners

CATALOG = load_catalog()
PIN = CATALOG.llama_cpp.source
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "build_plans"
PROBES = Path(__file__).resolve().parent / "fixtures" / "hardware_probe"
LINUX_BUILD_HOST = ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1")

# What each fixture machine's own run reports for its C library (Ubuntu 24.04, Alpine 3.22, DietPi on Debian 12).
LIBC_VERSION = {"intel": "2.39", "amd": "1.2.5", "rk3566": "2.36", "rk3588": "2.36"}
MACHINES = {
    "intel": dict(arch="x86_64", libc="glibc"),
    "amd": dict(arch="x86_64", libc="musl"),
    "rk3566": dict(arch="arm64", libc="glibc", arm_arch="armv8.2-a+dotprod+fp16"),
    "rk3588": dict(arch="arm64", libc="glibc", arm_arch="armv8.2-a+dotprod+fp16"),
}

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="the shell probe and sysroots are POSIX")


def probe_text(tmp_path: Path, kind: str, emitter: str) -> str:
    """The file the emitter writes on the fixture machine `kind`."""
    root = fixtures.make_sysroot(tmp_path, kind)
    if emitter == "py":
        return fixtures.probe.render_text(fixtures.py_probe(root))
    bindir = fixtures.tool_dir(tmp_path)
    return fixtures.run_emitter(emitter.split() + [str(fixtures.PROBE_SH)], root, bindir, tmp_path)


def with_libc_version(profile: HardwareProfile, version: str) -> HardwareProfile:
    """The profile plus the LIBC_VERSION the machine's own run reports, validated by the parser again."""
    assert profile.libc_version is None, "a sysroot probe was expected to leave LIBC_VERSION out"
    data = dict(profile.as_dict(), LIBC_VERSION=version)
    return parse_profile("".join(f"{k}={v}\n" for k, v in data.items()))


def check_plan(plan: BuildPlan, profile: HardwareProfile) -> None:
    """What every plan for a probed machine must hold: the pin, no download, no subprocess, the machine's target."""
    assert plan.source["commit"] == PIN.commit
    assert plan.target["os"] == profile.os and plan.target["arch"] == profile.arch
    assert plan.target["libc"] == profile.libc
    assert plan.build_id and len(plan.build_id) == 64
    for step in plan.steps:
        options = dict(step.options)
        check_download_options(options)
        assert not set(NEVER_SET) & set(options)
        assert all(options[name] == "OFF" for name in DOWNLOAD_OPTIONS_OFF)
        assert options["FETCHCONTENT_FULLY_DISCONNECTED"] == "ON"
        assert options["LLAMA_SUBPROCESS"] == "OFF"
        assert options["LLAMA_BUILD_COMMIT"] == PIN.commit[:8]


def emitters():
    """The Python probe, and the .sh in every POSIX shell installed here."""
    found = [pytest.param("py", id="py")]
    found += [pytest.param(shell, id="sh-" + Path(shell.split()[0]).name + ("-sh" if " " in shell else ""))
              for shell in fixtures.available_shells()]
    return found


@POSIX_ONLY
class TestFixtureMachines:

    @pytest.mark.parametrize("emitter", emitters())
    @pytest.mark.parametrize("kind", sorted(MACHINES))
    def test_probe_output_parses_and_gives_a_cross_plan(self, tmp_path, kind, emitter):
        expected = MACHINES[kind]
        profile = parse_profile(probe_text(tmp_path, kind, emitter))
        assert profile.source_schema == 2 and not profile.needs_reprobe
        assert (profile.os, profile.arch, profile.libc) == ("linux", expected["arch"], expected["libc"])
        assert not [n for n in profile.notes if n.startswith(("dropped:", "unknown_key:"))], profile.notes

        profile = with_libc_version(profile, LIBC_VERSION[kind])
        plans = plan_variants(profile, purpose="device-runtime", placement="cross", catalog=CATALOG,
                              toolchain=LINUX_BUILD_HOST)
        assert plans[0].backend == "cpu", "the CPU build comes first"
        for plan in plans:
            check_plan(plan, profile)
            options = plan.options()
            assert options["GGML_NATIVE"] == "OFF", "a build for another machine never uses -march=native"
            assert plan.builder["libc"] == expected["libc"]
            if expected["arch"] == "arm64":
                assert options["GGML_CPU_ARM_ARCH"] == expected["arm_arch"]
                assert all(ARM_FEATURES[k] in options["GGML_CPU_ARM_ARCH"] for k in ARM_FEATURES
                           if profile.flag(k) == "ON")
            else:
                assert "GGML_CPU_ARM_ARCH" not in options
                for key, option in X86_FEATURES.items():
                    assert options[option] in ("ON", "OFF")
                    if profile.flag(key) != "ON":
                        assert options[option] == "OFF", f"{option} is ON though the probe says {profile.flag(key)}"

    @pytest.mark.parametrize("emitter", emitters())
    def test_the_rk3566_probe_gives_the_golden_plan(self, tmp_path, emitter):
        """The ZERO 3W's real probe output plans exactly what the golden plan of the hand-written fixture holds."""
        profile = with_libc_version(parse_profile(probe_text(tmp_path, "rk3566", emitter)), "2.36")
        plan = plan_build(profile, purpose="device-runtime", placement="cross", catalog=CATALOG,
                          toolchain=LINUX_BUILD_HOST)
        golden = (GOLDEN / "rk3566_dietpi_cross.json").read_text(encoding="utf-8")
        assert json.loads(plan.to_json()) == json.loads(golden)
        hand_written = plan_build(read_profile(PROBES / "schema2_rk3566_dietpi.txt"), purpose="device-runtime",
                                  placement="cross", catalog=CATALOG, toolchain=LINUX_BUILD_HOST)
        assert plan.build_id == hand_written.build_id

    def test_json_view_plans_the_same(self, tmp_path):
        values = fixtures.py_probe(fixtures.make_sysroot(tmp_path, "rk3588"))
        text = parse_profile(fixtures.probe.render_text(values))
        json_view = parse_profile_json(fixtures.probe.render_json(values))
        assert text == json_view
        plans = [plan_build(with_libc_version(p, "2.36"), purpose="device-runtime", placement="cross",
                            catalog=CATALOG, toolchain=LINUX_BUILD_HOST) for p in (text, json_view)]
        assert plans[0].build_id == plans[1].build_id

    @pytest.mark.parametrize("kind", sorted(MACHINES))
    def test_a_stored_profile_plans_the_same_build(self, tmp_path, kind):
        """The store keeps only stable keys (llm-cli scan --device saves there); the plan needs nothing volatile."""
        profile = with_libc_version(parse_profile(probe_text(tmp_path, kind, "py")), LIBC_VERSION[kind])
        store = ProfileStore(tmp_path / "state")
        device_id = new_device_id()
        store.save_device(device_id, profile, label="fixture board")
        stored = store.load_device(device_id)
        assert stored is not None and "RAM_AVAILABLE_MB" not in stored
        assert stored.perf_key() == profile.perf_key()
        before, after = (plan_variants(p, purpose="device-runtime", placement="cross", catalog=CATALOG,
                                       toolchain=LINUX_BUILD_HOST) for p in (profile, stored))
        assert [p.build_id for p in before] == [p.build_id for p in after]

    @pytest.mark.parametrize("kind", ["rk3566", "amd"])
    def test_without_libc_version_a_cross_build_is_refused(self, tmp_path, kind):
        """Design 2.4/3.10: LIBC_VERSION is required for cross builds, for glibc and for musl alike (a binary
        linked against a newer musl can need symbols an older musl lacks)."""
        profile = parse_profile(probe_text(tmp_path, kind, "py"))
        assert profile.libc_version is None
        with pytest.raises(PlanError, match="LIBC_VERSION"):
            plan_build(profile, purpose="device-runtime", placement="cross", backend="cpu", catalog=CATALOG,
                       toolchain=LINUX_BUILD_HOST)

    def test_choose_build_base_needs_the_musl_version(self):
        with pytest.raises(PlanError, match="LIBC_VERSION"):
            choose_build_base(CATALOG, "musl", None)
        assert choose_build_base(CATALOG, "musl", "1.2.5").libc == "musl"


@pytest.fixture(scope="module")
def profile():
    """This computer's profile, written by the Python probe in-process (no sysroot, real tools, no network)."""
    return parse_profile(fixtures.probe.render_text(fixtures.probe.collect()))


class TestThisMachine:
    """The Python probe on the computer that runs the tests: its file parses and plans or is refused with a reason,
    never with another error."""

    def test_the_probe_output_parses(self, profile):
        assert profile.source_schema == 2 and not profile.needs_reprobe
        assert profile.os in ("linux", "windows", "macos") and profile.arch is not None

    def test_a_native_plan_or_a_reason(self, profile):
        try:
            toolchain = ToolchainInfo.from_profile(profile)
            plan = plan_build(profile, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                              toolchain=toolchain)
        except PlanError as e:
            assert str(e)
            return
        check_plan(plan, profile)
        assert plan.options()["GGML_NATIVE"] == "ON", "a build on the machine itself uses -march=native"

    def test_a_linux_machine_gets_a_cross_plan(self, profile):
        if profile.os != "linux" or profile.arch not in ("x86_64", "arm64") or profile.libc not in ("glibc", "musl"):
            pytest.skip("cross builds from the builder container are for Linux machines")
        oldest = min(version_tuple(b.libc_version) for b in CATALOG.build_bases if b.libc == profile.libc)
        version = profile.libc_version
        if version is None or version_tuple(version) < oldest:
            with pytest.raises(PlanError):
                plan_build(profile, purpose="device-runtime", placement="cross", backend="cpu", catalog=CATALOG,
                           toolchain=LINUX_BUILD_HOST)
            return
        plan = plan_build(profile, purpose="device-runtime", placement="cross", backend="cpu", catalog=CATALOG,
                          toolchain=LINUX_BUILD_HOST)
        check_plan(plan, profile)
        assert plan.builder["libc"] == profile.libc
        assert version_tuple(plan.builder["libc_version"]) <= version_tuple(version), \
            "the builder base must not have a newer C library than the machine"
