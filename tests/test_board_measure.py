"""The board measurement (runtime/device/measure.py): the board's limits come from a measurement on that board, never
from a guess; the start script takes context, KV cache, prompt cache and the reserve from it and refuses without it.

Stub boards only: parsed answers of the board operations, and the fake board of test_device_runtime (a real POSIX
shell in a temporary home, a stand-in llama-server that only says it serves)."""

import os
import subprocess

import pytest

import test_device_runtime as tdr
from test_device_runtime import device_base, source  # noqa: F401 - fixtures
from orchestrator.remote import gate
from orchestrator.runtime.device import board_ops  # noqa: F401 - defines every board operation
from orchestrator.runtime.device import launcher, measure, steps
from orchestrator.runtime.device.remote import RemoteShell, verify_on_device

POSIX = pytest.mark.skipif(os.name != "posix", reason="the start script and the fake board need a POSIX shell")

IDLE = """mem MemTotal 4005888
mem MemAvailable 3072000
mem SwapTotal 1048576
mem SwapFree 1048576
zram 536870912
cpus 4
freqmax 1800000
freq 1800000
model 734003200
"""
IDLE_AFTER = IDLE.replace("MemAvailable 3072000", "MemAvailable 3020800")
LOAD = """step ctx=512 up=1 rss_kib=921600 hwm_kib=921600 avail_kib=2150400 freq_khz=1800000 train=32768
step ctx=1536 up=1 rss_kib=1036288 hwm_kib=1036288 avail_kib=2030000 freq_khz=1416000 train=32768
"""


def record(load=LOAD, kv="f16", after=IDLE_AFTER):
    return measure.Measurement(measured_at="2026-11-01T10:00:00Z", build_id="b" * 64, model_sha256="a" * 64,
                               model_bytes=734003200, kv_measured=kv, idle_before=measure.parse_idle(IDLE),
                               idle_after=measure.parse_idle(after), steps=measure.parse_load(load))


# --- parsing and deriving -------------------------------------------------------------------------------------------

def test_the_answers_are_parsed_strictly():
    idle = measure.parse_idle(IDLE + "mem MemTotal x\nrm -rf /\nmodel 12 13\n")
    assert (idle.total_mib, idle.available_mib, idle.swap_total_mib, idle.zram_mib, idle.cpus) == (3912, 3000, 1024,
                                                                                                 512, 4)
    assert idle.model_bytes == 734003200 and idle.freq_max_khz == 1800000
    steps_ = measure.parse_load(LOAD + "step ctx=9 up=1 rss_kib=x\nhello\n")
    assert [s.ctx for s in steps_] == [512, 1536] and steps_[0].up and steps_[0].rss_mib == 900.0
    assert steps_[1].train_ctx == 32768 and steps_[1].available_mib == 1982
    assert not measure.parse_load("step ctx=512 up=1 rss_kib=0 hwm_kib=0 avail_kib=0 freq_khz=0 train=0")[0].up


def test_the_limits_come_from_the_measured_numbers():
    limits = measure.derive(record())
    # reserve: 5 % of 3912 MiB plus the measured swing of the free memory at rest (3000 -> 2950)
    assert limits.reserve_mib == 196 + 50 and limits.usable_mib == 2950 - 246
    # the KV cache grew by 112 MiB from 512 to 1536 tokens: 112 KiB per token (f16); the model is the rest
    assert limits.f16_bytes_per_token == 112 * 1024 and limits.base_mib == 900 - 56
    # less than 2 GiB left next to the model and the reserve: the boards' rule takes q8_0
    assert limits.kv_cache == "q8_0" and limits.kv_why.startswith("automatic")
    assert limits.kv_bytes_per_token == int(112 * 1024 * 1.0625 / 2)
    assert limits.fits and limits.ctx % 256 == 0 and 512 <= limits.ctx <= 32768
    assert limits.cache_ram_mib == 3912 // 8, "an eighth of the RAM, room enough"
    assert limits.need_mib + limits.reserve_mib <= limits.available_mib
    assert limits.throttled, "the clock fell below 85 % of its top under load"
    assert limits.train_ctx == 32768
    text = limits.board_file()
    assert f"ctx={limits.ctx}\n" in text and "kv=q8_0\n" in text and "model_size=734003200\n" in text
    assert set(limits.ai_facts()) >= {"context_tokens", "kv_cache", "reserve_mib", "server_needs_mib"}


def test_the_persons_kv_cache_choice_counts():
    chosen = measure.derive(record(), "f16")
    auto = measure.derive(record())
    assert chosen.kv_cache == "f16" and chosen.kv_why == "the person's choice"
    assert chosen.ctx < auto.ctx, "the f16 cache needs about twice the memory per token"
    # a measurement made with q8_0 is converted by ggml's block sizes
    q8 = measure.derive(record(kv="q8_0"))
    assert q8.f16_bytes_per_token == int(112 * 1024 / (1.0625 / 2))


def test_a_model_that_does_not_fit_is_said_plainly():
    one = measure.derive(record(load=LOAD.splitlines()[0] + "\n"))
    assert not one.fits and "one context step" in one.reason
    none = measure.derive(record(load="step ctx=512 up=0 rss_kib=0 hwm_kib=0 avail_kib=0 freq_khz=0 train=0\n"))
    assert not none.fits and "did not start" in none.reason and "smaller model" in none.reason
    huge = LOAD.replace("rss_kib=921600 hwm_kib=921600", "rss_kib=2900000 hwm_kib=2900000") \
               .replace("rss_kib=1036288 hwm_kib=1036288", "rss_kib=3014688 hwm_kib=3014688")
    tight = measure.derive(record(load=huge))
    assert not tight.fits and tight.ctx < measure.CTX_MIN and "board_file" and "ctx=0\n" in tight.board_file()
    assert measure.person_lines(tight, "de")[1].startswith("Das Modell passt nicht")


def test_without_a_measurement_nothing_is_guessed(tmp_path):
    assert measure.board_limits(tmp_path, "0123456789abcdef") is None
    assert measure.ai_facts(None) == {"measured": False, "reason": measure.NOT_MEASURED}
    assert "not been measured" in measure.person_lines(None, "en")[0]
    assert "noch nicht vermessen" in measure.person_lines(None, "de")[0]


@pytest.mark.parametrize("lang, words", [("en", "from the board's measurement"), ("de", "aus der Vermessung")])
def test_the_measured_kv_cache_is_said_in_words(lang, words):
    """`device runtime deploy` and the windows name the measured KV-cache type with its reason in words, never with
    a text key (review of ctx-measure: device.kv.why_measured was missing)."""
    from orchestrator.cli import _kv_text
    from orchestrator.runtime.device.settings import KvChoice
    from orchestrator.utils.localization import ui_text
    text = ui_text("device.kv.auto", lang, kind="q8_0",
                   why=ui_text("device.kv.why_measured", lang, free=1.5))
    assert "device.kv" not in text and words in text and "1.5 GiB" in text
    assert "device.kv" not in _kv_text(None, KvChoice("q8_0", "measured", 1.5))


def test_the_measurement_is_a_kind_of_the_boards_data_with_its_words():
    """A host-key change moves the measurement with the board's data (board_move): it is one of the KINDS the move
    reports, with its words for the CLI and the window in English and German (review of ctx-measure: it moved but
    was no kind, so the window would have shown a text key)."""
    from orchestrator.runtime.device import board_move
    from orchestrator.utils.localization import ui_text
    assert "limits" in board_move.KINDS and board_move.kind_words(["limits"]) == "measured limits"
    for kind in board_move.KINDS:
        for lang in ("en", "de"):
            assert not ui_text(f"boards.edit.data.{kind}", lang).startswith("boards."), (kind, lang)


def test_the_record_is_kept_per_board_with_its_version(tmp_path):
    device = "0123456789abcdef"
    measure.save(tmp_path, device, record())
    again = measure.load(tmp_path, device)
    assert again is not None and again.steps == record().steps and again.version == measure.MEASURE_VERSION
    assert measure.board_limits(tmp_path, device).ctx == measure.derive(record()).ctx
    path = tmp_path / "hardware" / "devices" / f"{device}.limits.json"
    data = path.read_text().replace(f'"version": {measure.MEASURE_VERSION}', '"version": 99')
    path.write_text(data)
    assert measure.load(tmp_path, device) is None, "a record of another version is measured again, never read"
    path.write_text("{not json")
    assert measure.load(tmp_path, device) is None


def test_the_board_operations_are_the_frameworks_own():
    program = "/home/u/.local/share/llm-framework/llama.cpp/current/bin/llama-server"
    job = gate.job("measure-load", "/home/u", program, "/home/u/m.gguf", "65431", "/home/u/.config/k", "q8_0", "512")
    assert "--host 127.0.0.1" in job and "--api-key-file /home/u/.config/k" in job and "-ctk q8_0" in job
    assert "env -i" in job and "--tools" not in job and "--no-ui" in job
    for bad in (("/home/u/.local/share/llm-framework/llama.cpp/current/bin/llama-bench", "f16", "512"),
                (program, "q2_k", "512"), (program, "f16")):
        with pytest.raises(gate.GateRefused):
            gate.job("measure-load", "/home/u", bad[0], "/home/u/m.gguf", "65431", "/home/u/k", bad[1], *bad[2:])
    assert "board-limits" in gate.script("limits-write", "/home/u")


# --- the start script -----------------------------------------------------------------------------------------------

@POSIX
class TestTheStartScriptTakesTheMeasurement:

    @pytest.fixture
    def home(self, tmp_path):
        home = tmp_path / "board-home"
        home.mkdir()
        (home / "model.gguf").write_bytes(b"GGUF" * 10)
        tdr.install_folder(home)
        return home

    def run(self, home, *args):
        script = home / launcher.INSTALL_REL / "current" / launcher.START_SCRIPT
        return subprocess.run([tdr.SHELL, str(script), *args], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, timeout=60, cwd=str(home))

    def limits(self, home, **values):
        data = {"version": 1, "ctx": 6144, "kv": "q8_0", "cache_ram": 256, "need_mib": 1, "reserve_mib": 1,
                "model_size": 40}
        data.update(values)
        path = home / launcher.LIMITS_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes("".join(f"{k}={v}\n" for k, v in data.items()).encode())

    def test_without_a_measurement_and_without_a_context_it_refuses(self, home):
        # since the board's unit stops on it: the measurement refusals end with their own exit code (MEASURE_EXIT),
        # which the unit's RestartPreventExitStatus names (before: 2, the code of every refusal)
        res = self.run(home, "serve", str(home / "model.gguf"))
        assert res.returncode == launcher.MEASURE_EXIT and "not been measured" in res.stderr
        assert "ARG=" not in res.stdout
        res = self.run(home, "start", str(home / "model.gguf"))
        assert res.returncode == launcher.MEASURE_EXIT and "not been measured" in res.stderr

    def test_the_measured_limits_are_passed_on(self, home):
        self.limits(home)
        res = self.run(home, "serve", str(home / "model.gguf"))
        assert res.returncode == 0, res.stderr
        args, _env = tdr.TestStartScript.args_env(res.stdout)
        tail = args[12:]
        assert tail == ["-c", "6144", "-ctk", "q8_0", "-ctv", "q8_0", "--cache-ram", "256"]

    def test_what_the_person_gives_wins(self, home):
        self.limits(home)
        res = self.run(home, "serve", str(home / "model.gguf"), "-c", "1024", "-ctk", "f16", "-ctv", "f16")
        args, _env = tdr.TestStartScript.args_env(res.stdout)
        assert res.returncode == 0 and args[12:] == ["-c", "1024", "-ctk", "f16", "-ctv", "f16", "--cache-ram", "256"]

    @pytest.mark.parametrize("values, words, code", [
        ({"model_size": 41}, "measured with another model", "measure"),
        ({"ctx": 0}, "does not fit", "measure"),
        # too little free memory right now may pass: the ordinary refusal (2), which the unit tries again
        ({"need_mib": 900000000}, "MiB are free now", 2),
    ])
    def test_a_measurement_that_does_not_hold_refuses(self, home, values, words, code):
        self.limits(home, **values)
        res = self.run(home, "serve", str(home / "model.gguf"))
        want = launcher.MEASURE_EXIT if code == "measure" else code
        assert res.returncode == want and words in res.stderr and "ARG=" not in res.stdout

    @pytest.mark.parametrize("given", [("-ctk", "f16"), ("--cache-type-v", "f16"), ("-ctk", "q8_0", "-ctv", "f16")])
    def test_a_larger_kv_cache_than_measured_needs_its_own_context(self, home, given):
        """The measured context holds for the measured KV-cache type: a larger type without -c would overfill the
        board (review of ctx-measure); a smaller one fits, and with -c the person decides."""
        self.limits(home, kv="q8_0")
        res = self.run(home, "serve", str(home / "model.gguf"), *given)
        assert res.returncode == launcher.MEASURE_EXIT and "measured with the KV cache q8_0" in res.stderr
        assert "ARG=" not in res.stdout
        res = self.run(home, "serve", str(home / "model.gguf"), "-ctk", "q4_0", "-ctv", "q4_0")
        assert res.returncode == 0, res.stderr
        res = self.run(home, "serve", str(home / "model.gguf"), "-c", "1024", *given)
        assert res.returncode == 0, res.stderr

    def test_a_damaged_limits_file_counts_as_none(self, home):
        self.limits(home, kv="q2_k; rm -rf ~")
        res = self.run(home, "serve", str(home / "model.gguf"))
        assert res.returncode == launcher.MEASURE_EXIT and "not been measured" in res.stderr

    def test_an_option_the_script_refuses_keeps_the_ordinary_refusal(self, home):
        res = self.run(home, "serve", str(home / "model.gguf"), "--tools", "all")
        assert res.returncode == 2


def test_the_unit_does_not_restart_an_unmeasured_board_every_ten_seconds():
    """Regression (looped before: the unit's Restart=on-failure started the start script again every 10 s on a board
    that was never measured, and it refused each time with the code of every refusal): the measurement refusals have
    their own exit code, and the unit names it in RestartPreventExitStatus, so systemd leaves the unit stopped; every
    other failure still restarts."""
    code = getattr(launcher, "MEASURE_EXIT", None)
    assert code is not None and code not in (0, 1, 2, 3)
    unit = launcher.render_systemd_unit(user="dietpi", home="/home/dietpi", model="/home/dietpi/m.gguf", port=8080)
    lines = unit.splitlines()
    assert "Restart=on-failure" in lines and f"RestartPreventExitStatus={code}" in lines
    assert f"exit {code}" in launcher.render_start_script()


# --- on the fake board ----------------------------------------------------------------------------------------------

MEASURE_SERVER = """#!/bin/sh
case "$1" in
  --version) echo "version: 0.6.0 (build @BUILD@, commit @COMMIT@)" >&2; exit 0 ;;
  --tools) echo "error: subprocess is not enabled on this build" >&2; exit 1 ;;
esac
echo "print_info: n_ctx_train      = 8192" >&2
echo "main: server is listening on http://127.0.0.1:65431 - starting the main loop" >&2
exec sleep 30
"""


@POSIX
@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="the stand-in server's memory is read from /proc")
def test_the_benchmark_measures_the_board_and_writes_its_limits(tmp_path, source, device_base):  # noqa: F811
    build, packaged = tdr.packaged_build(tmp_path, source, server=tdr.fake_program(MEASURE_SERVER))
    dev = tdr.FakeDevice(device_base)
    assert tdr.deploy(dev, build, packaged).activated
    meminfo = dev.fakeroot / "proc" / "meminfo"
    meminfo.parent.mkdir(parents=True, exist_ok=True)
    meminfo.write_bytes(b"MemTotal: 4005888 kB\nMemAvailable: 3072000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    model = dev.home / "models" / "qwen.gguf"
    model.parent.mkdir()
    model.write_bytes(b"GGUF" * 1000)
    report = verify_on_device(RemoteShell(dev), home=str(dev.home), plan_verify=build.plan["verify"],
                              pin_build=tdr.PIN.build_number, pin_commit=tdr.PIN.commit, port=8080,
                              bench_model=str(model), bench_allowed=True,
                              measure=measure.MeasureRequest(None, build.build_id, "c" * 64))
    check = report.check("measure")
    assert check is not None and check.ok, check.detail if check else report.checks
    data = check.data["record"]
    assert [s["ctx"] for s in data["steps"]] == list(measure.CTX_STEPS) and all(s["up"] for s in data["steps"])
    assert data["idle_before"]["total_mib"] == 3912 and data["model_bytes"] == 4000
    limits = check.data["limits"]
    assert limits["training_context"] == 8192 and limits["context_tokens"] <= 8192
    text = (dev.home / launcher.LIMITS_REL).read_text()
    assert f"ctx={limits['context_tokens']}\n" in text and "model_size=4000\n" in text
    assert oct((dev.home / launcher.LIMITS_REL).stat().st_mode & 0o777) == "0o600"
    # nothing of the measurement stays on the board but the limits file
    assert not list((dev.home / launcher.INSTALL_REL).glob(".bench.*"))
    # kept with the board for every later plan and the listings
    state = tmp_path / "state-measure"
    assert steps.record_measurement(state, "0123456789abcdef", report)
    assert steps.board_limits_of(state, "0123456789abcdef").ctx == limits["context_tokens"]
    assert report.ok


@POSIX
def test_without_a_benchmark_model_nothing_is_measured(tmp_path, source, device_base):  # noqa: F811
    build, packaged = tdr.packaged_build(tmp_path, source)
    dev = tdr.FakeDevice(device_base)
    assert tdr.deploy(dev, build, packaged).activated
    report = verify_on_device(RemoteShell(dev), home=str(dev.home), plan_verify=build.plan["verify"],
                              pin_build=tdr.PIN.build_number, pin_commit=tdr.PIN.commit, port=8080,
                              measure=measure.MeasureRequest())
    assert report.check("measure") is None and not any("measure-load" in c or "starting the main loop" in c
                                                       for c in dev.commands)
    assert steps.limits_facts(report)["measured"] is False


# --- the board bundle decides with the measured limits --------------------------------------------------------------

def _bundle_plan(tmp_path, **kw):
    import test_board_bundle as tbb
    from orchestrator.runtime.bundle import blocks as blk, generator as gen
    selection = kw.pop("selection", None) or tbb._selection(tmp_path)
    return gen.plan_bundle(selection, catalog=blk.load_catalog(), profile=tbb.RK3566, device_id=tbb.DEVICE,
                           runtime=tbb._runtime_tarball(tmp_path), offline=False, bundle_id="voice-test", **kw)


def test_an_llm_bundle_on_an_unmeasured_board_is_refused_unless_the_person_says_yes(tmp_path):
    plan = _bundle_plan(tmp_path)
    assert not plan.ok and any("not been measured" in p for p in plan.problems)
    yes = _bundle_plan(tmp_path, unmeasured_ok=True)
    assert yes.ok, yes.problems
    assert any("without the board's measurement" in w for w in yes.warnings)


def test_the_measured_limits_set_the_context_kv_cache_and_the_memory(tmp_path):
    limits = measure.derive(record())
    plan = _bundle_plan(tmp_path, limits=limits)
    assert plan.ok, plan.problems
    llm = next(c for c in plan.manifest.components if c.kind == "llm")
    assert llm.variables["ctx_size"] == limits.ctx and llm.variables["kv_cache"] == "q8_0"
    assert "--cache-type-k" in llm.start.args and "q8_0" in llm.start.args
    assert plan.board_memory_mb == limits.usable_mib
    memory = next(c for c in plan.checks if c["check"] == "memory")
    assert memory["result"] == "passed" and memory["detail"].startswith("measured")


def test_the_persons_choice_first_and_a_plain_reason_when_it_does_not_fit(tmp_path):
    import test_board_bundle as tbb
    limits = measure.derive(record())
    too_big = _bundle_plan(tmp_path, limits=limits, selection=tbb._selection(
        tmp_path, values={"llm": {"ctx_size": str(limits.ctx + 256)}}))
    assert not too_big.ok and any("does not fit" in p for p in too_big.problems)
    huge = LOAD.replace("rss_kib=921600 hwm_kib=921600", "rss_kib=2900000 hwm_kib=2900000") \
               .replace("rss_kib=1036288 hwm_kib=1036288", "rss_kib=3014688 hwm_kib=3014688")
    no_fit = _bundle_plan(tmp_path, limits=measure.derive(record(load=huge)))
    assert not no_fit.ok and any("leave the llm block out" in p for p in no_fit.problems)
    selection = tbb._selection(tmp_path, blocks=["stt-whisper-cpp-program"])
    del selection.files["llm"]
    off_board = _bundle_plan(tmp_path, selection=selection)
    assert off_board.ok, off_board.problems
    assert any("no language model on the board (your choice)" in n for n in off_board.notes)
