"""Every CPU feature ggml's CPU backend can use at the pinned llama.cpp, read by the hardware probe (probe 3.2.0; the
one-planner rule for the CPU: a board's acceleration must never be lost silently).

- The three emitters know the same keys (the schema's), the same AT_HWCAP / AT_HWCAP2 bits and the same POWER
  generations.
- Fixture machines of every architecture ggml has CPU options for (an ARMv9 board with SVE2, I8MM, BF16 and SME,
  RISC-V with the V extension, LoongArch, POWER9, s390x z15) give the expected keys in the Python probe and in the
  shell probe, and the two agree.
- AT_HWCAP / AT_HWCAP2 (/proc/self/auxv) fill in what the Features line does not list.
- A profile of probe 3.1.0 or older, which lacks the new keys, stays readable: UNKNOWN in its own architecture with a
  probe_again note, OFF elsewhere.
"""

import struct
import sys

import pytest

from orchestrator.runtime.profile import ProfileError, parse_profile
from test_hardware_probe import (FIXTURES, POSIX_ONLY, PROBE_PS1, PROBE_PY, PROBE_SH, RAW_SCHEMA, available_shells,
                                 comparable, meminfo, probe, run_emitter, tool_dir, write)

NEW_KEYS = ("SUPPORTS_RVV", "SUPPORTS_RV_ZFH", "SUPPORTS_RV_ZVFH", "SUPPORTS_RV_ZICBOP", "SUPPORTS_RV_ZIHINTPAUSE",
            "SUPPORTS_RV_ZVFBFWMA", "SUPPORTS_RV_ZBA", "SUPPORTS_XTHEADVECTOR", "SUPPORTS_LSX", "SUPPORTS_LASX",
            "SUPPORTS_VSX", "SUPPORTS_VXE", "SUPPORTS_VXE2", "SUPPORTS_NNPA")

# kind -> (cpuinfo fixture, uname -m)
MACHINES = {
    "armv9": ("cpuinfo_armv9_sme.txt", "aarch64"),
    "riscv64": ("cpuinfo_riscv64_rvv.txt", "riscv64"),
    "loongarch64": ("cpuinfo_loongarch64.txt", "loongarch64"),
    "ppc64le": ("cpuinfo_ppc64le_power9.txt", "ppc64le"),
    "s390x": ("cpuinfo_s390x_z15.txt", "s390x"),
    "rk3566": ("cpuinfo_rk3566.txt", "aarch64"),
}


def auxv(hwcap, hwcap2=0):
    """/proc/self/auxv bytes with AT_HWCAP (16) and AT_HWCAP2 (26), ended by AT_NULL."""
    return struct.pack("=QQQQQQ", 16, hwcap, 26, hwcap2, 0, 0)


def machine(tmp_path, kind, cpuinfo=None, auxv_bytes=None):
    name, uname = MACHINES[kind]
    root = tmp_path / kind
    write(root, "/proc/cpuinfo", cpuinfo if cpuinfo is not None else (FIXTURES / name).read_text())
    write(root, "/proc/sys/kernel/osrelease", "6.6.0-example\n")
    write(root, "/proc/sys/kernel/arch", uname + "\n")
    write(root, "/proc/meminfo", meminfo(8000000, 6000000, 0))
    if auxv_bytes is not None:
        write(root, "/proc/self/auxv", auxv_bytes)
    return root


def py_profile(root):
    values = probe.collect(root=str(root), run=lambda cmd, timeout: (-1, "", "not found"),
                           sections=("cpu", "memory"))
    return parse_profile(probe.render_text(values))


def on(profile):
    return {k for k in profile.values if k.startswith("SUPPORTS_") and profile.flag(k) == "ON"}


# --- the tables of the three emitters --------------------------------------------------------------------------

class TestTables:

    def test_hwcap_bits_are_the_schema(self):
        table = RAW_SCHEMA["tables"]["hwcap_bits"]
        assert {arch: {t: f"{w}:{b}" for t, (w, b) in bits.items()} for arch, bits in probe.HWCAP_BITS.items()} == table
        text = PROBE_SH.read_text(encoding="utf-8")
        for arch, bits in table.items():
            assert f'{arch}) echo "{" ".join(f"{t}:{v}" for t, v in bits.items())}" ;;' in text, arch

    def test_power_generations_are_the_schema(self):
        values = tuple(RAW_SCHEMA["keys"]["POWER_CPU_TYPE"]["values"])
        assert probe.POWER_TYPES == values == probe.KEYS["POWER_CPU_TYPE"][1]
        assert "power7|power8|power9|power10|power11)" in PROBE_SH.read_text(encoding="utf-8")

    def test_every_new_key_is_a_later_boolean_of_its_architecture(self):
        """Each key of probe 3.2.0 is an ON/OFF key with `since` and one architecture: an older file is readable."""
        for key in NEW_KEYS:
            spec = RAW_SCHEMA["keys"][key]
            assert spec["type"] == "bool" and spec["since"] == "3.2.0" and len(spec["only_arch"]) == 1, key
            assert spec["perf"] == "none", key  # the benchmark key of every existing profile stays the same
        assert RAW_SCHEMA["keys"]["POWER_CPU_TYPE"]["required"] is False

    def test_the_ps1_writes_the_new_keys_off(self):
        text = PROBE_PS1.read_text(encoding="utf-8")
        for key in NEW_KEYS:
            assert f"'{key}' = @($null, $null, '" in text, key


# --- the Python probe on fixture machines ------------------------------------------------------------------------

@POSIX_ONLY
class TestFixtureMachines:

    def test_an_armv9_board_with_sve2_i8mm_bf16_and_sme(self, tmp_path):
        p = py_profile(machine(tmp_path, "armv9"))
        assert p.arch == "arm64" and p.get("CPU_MODEL") == "Cortex-A725+Cortex-X925"
        assert on(p) == {"SUPPORTS_NEON", "SUPPORTS_DOTPROD", "SUPPORTS_FP16_ARITH", "SUPPORTS_I8MM", "SUPPORTS_SVE",
                         "SUPPORTS_SVE2", "SUPPORTS_SME", "SUPPORTS_BF16_ARM"}

    def test_risc_v_with_the_v_extension(self, tmp_path):
        p = py_profile(machine(tmp_path, "riscv64"))
        assert p.arch == "riscv64" and p.get("CPU_MODEL") == "example,rvv-core"
        # the "isa" line (every hart): g expands, zba of the one "hart isa" line is not every hart's
        assert p.items_of("CPU_FLAGS_ALL")[:9] == ["i", "m", "a", "f", "d", "zicsr", "zifencei", "c", "v"]
        assert on(p) == {"SUPPORTS_RVV", "SUPPORTS_RV_ZFH", "SUPPORTS_RV_ZVFH", "SUPPORTS_RV_ZICBOP",
                         "SUPPORTS_RV_ZIHINTPAUSE"}

    def test_risc_v_without_an_isa_line_uses_the_hart_s(self, tmp_path):
        text = "".join(line for line in (FIXTURES / "cpuinfo_riscv64_rvv.txt").read_text().splitlines(True)
                       if not line.startswith("isa\t"))
        p = py_profile(machine(tmp_path, "riscv64", cpuinfo=text))
        assert p.is_on("SUPPORTS_RV_ZBA") and p.is_on("SUPPORTS_RVV")

    def test_a_t_head_core_with_xtheadvector(self, tmp_path):
        text = "processor\t: 0\nhart\t\t: 0\nisa\t\t: rv64imafdc_xtheadvector\nuarch\t\t: thead,c906\n"
        p = py_profile(machine(tmp_path, "riscv64", cpuinfo=text))
        assert on(p) == {"SUPPORTS_XTHEADVECTOR"}

    def test_loongarch_with_lsx_and_lasx(self, tmp_path):
        p = py_profile(machine(tmp_path, "loongarch64"))
        assert p.arch == "loongarch64" and p.get("CPU_MODEL") == "Loongson-3A5000"
        assert on(p) == {"SUPPORTS_LSX", "SUPPORTS_LASX"}

    def test_power9(self, tmp_path):
        p = py_profile(machine(tmp_path, "ppc64le"))
        assert p.arch == "ppc64le" and p.get("POWER_CPU_TYPE") == "power9"
        assert on(p) == {"SUPPORTS_VSX"}  # POWER7 and later have VSX (ISA 2.06)

    def test_a_power_cpu_line_without_a_generation_takes_vsx_from_at_hwcap(self, tmp_path):
        text = "processor\t: 0\ncpu\t\t: example PowerPC core, altivec supported\n"
        without = py_profile(machine(tmp_path / "a", "ppc64le", cpuinfo=text))
        assert not without.is_on("SUPPORTS_VSX") and "not_determined:POWER_CPU_TYPE" in without.warnings
        with_hwcap = py_profile(machine(tmp_path / "b", "ppc64le", cpuinfo=text, auxv_bytes=auxv(1 << 7 | 1 << 28)))
        assert with_hwcap.is_on("SUPPORTS_VSX") and with_hwcap.get("POWER_CPU_TYPE") is None

    def test_s390x_z15(self, tmp_path):
        p = py_profile(machine(tmp_path, "s390x"))
        assert p.arch == "s390x" and p.get("CPU_CORES_LOGICAL") == "2"
        assert on(p) == {"SUPPORTS_VXE", "SUPPORTS_VXE2"}
        assert "identification" not in p.to_text()

    def test_at_hwcap_fills_in_what_the_features_line_does_not_list(self, tmp_path):
        """A Features line without asimddp/fphp/asimdhp (an old kernel's, a restricted /proc): the bits of
        AT_HWCAP still say that the board has them."""
        text = "".join(line.replace(" asimddp", "").replace(" fphp asimdhp", "")
                       for line in (FIXTURES / "cpuinfo_rk3566.txt").read_text().splitlines(True))
        plain = py_profile(machine(tmp_path / "a", "rk3566", cpuinfo=text))
        assert not plain.is_on("SUPPORTS_DOTPROD") and not plain.is_on("SUPPORTS_FP16_ARITH")
        hwcap = (1 << 1) | (1 << 9) | (1 << 10) | (1 << 20)
        filled = py_profile(machine(tmp_path / "b", "rk3566", cpuinfo=text, auxv_bytes=auxv(hwcap)))
        assert on(filled) == {"SUPPORTS_NEON", "SUPPORTS_DOTPROD", "SUPPORTS_FP16_ARITH"}

    def test_at_hwcap2_bits(self):
        caps = (0, (1 << 1) | (1 << 13) | (1 << 14) | (1 << 23))
        assert probe.hwcap_tokens("arm64", caps) == ["sve2", "i8mm", "bf16", "sme"]
        assert probe.auxv_hwcaps(auxv(5, 7)) == (5, 7) and probe.auxv_hwcaps(b"") is None

    def test_riscv_isa_strings(self):
        assert probe.riscv_isa_tokens("rv64imafdcv_zicbom_zfh") == ["i", "m", "a", "f", "d", "c", "v", "zicbom", "zfh"]
        assert probe.riscv_isa_tokens("RV64GC") == ["i", "m", "a", "f", "d", "zicsr", "zifencei", "c"]
        assert probe.riscv_isa_tokens("x86_64") == [] and probe.riscv_isa_tokens(None) == []


# --- .sh and .py on the same machines ---------------------------------------------------------------------------

@POSIX_ONLY
@pytest.mark.skipif(not available_shells(), reason="no POSIX shell")
class TestShellEquivalence:

    @pytest.mark.parametrize("shell", available_shells())
    @pytest.mark.parametrize("kind, hwcap", [("armv9", None), ("riscv64", None), ("loongarch64", None),
                                             ("ppc64le", None), ("ppc64le", (1 << 7, 0)), ("s390x", None),
                                             ("rk3566", ((1 << 1) | (1 << 20), 1 << 13))])
    def test_sh_and_py_agree(self, tmp_path, shell, kind, hwcap):
        root = machine(tmp_path, kind, auxv_bytes=auxv(*hwcap) if hwcap else None)
        bindir = tool_dir(tmp_path)
        sh_text = run_emitter(shell.split() + [str(PROBE_SH)], root, bindir, tmp_path)
        py_text = run_emitter([sys.executable, "-I", str(PROBE_PY)], root, bindir, tmp_path)
        assert comparable(sh_text) == comparable(py_text)
        if kind == "rk3566":  # i8mm only from AT_HWCAP2
            assert parse_profile(sh_text).is_on("SUPPORTS_I8MM")


# --- profiles of older probes stay readable ---------------------------------------------------------------------

RISCV_310 = """PROBE_SCHEMA=2
PROBE_TOOL=sh
PROBE_TOOL_VERSION=3.1.0
GENERATED_AT=2026-10-29T08:00:00Z
OS=linux
KERNEL=6.6.0-example
ARCH=riscv64
CPU_CORES_LOGICAL=4
RAM_TOTAL_MB=8000
GPU_COUNT=0
"""


def _with_bools_off(text):
    """The ON/OFF keys of probe 3.1.0 (all of them OFF on RISC-V): the file as that probe wrote it."""
    old = [k for k, v in RAW_SCHEMA["keys"].items() if v["type"] == "bool" and not v.get("since")]
    return text + "".join(f"{k}=OFF\n" for k in old)


class TestOlderProfiles:

    def test_an_older_profile_of_another_architecture_gets_the_new_keys_off(self):
        p = parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text(encoding="utf-8"))
        assert all(p.flag(k) == "OFF" for k in NEW_KEYS)
        assert not [n for n in p.notes if n.startswith("probe_again:")]

    def test_an_older_profile_of_the_architecture_gets_unknown_and_a_probe_again_note(self):
        p = parse_profile(_with_bools_off(RISCV_310))
        assert p.flag("SUPPORTS_RVV") == "UNKNOWN" and p.flag("SUPPORTS_LSX") == "OFF"
        assert "probe_again:SUPPORTS_RVV" in p.notes and "probe_again:SUPPORTS_LSX" not in p.notes

    def test_a_profile_of_the_new_version_must_write_them(self):
        with pytest.raises(ProfileError, match="SUPPORTS_RVV: missing"):
            parse_profile(_with_bools_off(RISCV_310.replace("3.1.0", "3.2.0")))
