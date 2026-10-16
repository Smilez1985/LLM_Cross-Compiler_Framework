"""The KV cache of a board's server and the proof "tested on board X" (eighth round N7 and N3, seventh round 2').

- N7: the KV-cache types f16, q8_0 and q4_0 (-ctk/-ctv) are checked words of the shared launch policy. The deploy
  settings choose one for a board; unset means automatic: q8_0 where little RAM is left next to the model, f16
  otherwise. The person can always choose a type, also f16 to switch the quantized cache off. The start script, the
  systemd unit, the start command and the package's deploy.sh all take it from there and nothing else.
- N3: a benchmark on the board is recorded with every build of that model (found by the model's sha256 in the build
  manifests) as "tested on" the board; only a run whose checks passed counts.
"""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.runtime import server_policy
from orchestrator.runtime.build.verify import Check, VerifyReport
from orchestrator.runtime.device import launcher, tested
from orchestrator.runtime.device.settings import DeploySettings, SettingsError
from orchestrator.runtime.hardware import GIB, kv_cache_default
from orchestrator.runtime.server_policy import KV_CACHE_TYPES, PolicyError, kv_cache_args

import test_device_runtime as tdr  # noqa: E402

POSIX = tdr.POSIX


class Profile:
    """A stored probe with only the values the KV-cache choice reads."""

    def __init__(self, ram_mb=None, **values):
        self.values = {"RAM_TOTAL_MB": ram_mb, **values}

    def int_value(self, key):
        return self.values.get(key)

    def get(self, key):
        return self.values.get(key)


# --- N7: the policy and the deploy settings -------------------------------------------------------------------------

class TestKvPolicy:

    def test_the_types_and_their_options(self):
        assert KV_CACHE_TYPES == ("f16", "q8_0", "q4_0")
        assert kv_cache_args(None) == [] and kv_cache_args("f16") == [], "f16 is llama.cpp's own default"
        assert kv_cache_args("q8_0") == ["-ctk", "q8_0", "-ctv", "q8_0"]
        assert kv_cache_args("q4_0") == ["-ctk", "q4_0", "-ctv", "q4_0"]
        for bad in ("q5_1", "Q8_0", "q8_0 ", "q8_0;reboot", "--tools", ""):
            with pytest.raises(PolicyError):
                kv_cache_args(bad)

    def test_the_start_script_passes_them_on_and_they_are_never_forbidden(self):
        allowed = {n: kind for names, kind in server_policy.START_SCRIPT_OPTIONS.items() for n in names}
        for name in ("-ctk", "--cache-type-k", "-ctv", "--cache-type-v"):
            assert allowed[name] == "cachetype" and name not in server_policy.FORBIDDEN_FLAGS
        assert "cachetype) case $v in f16|q8_0|q4_0) return 0" in launcher.render_start_script()

    def test_the_settings_keep_a_checked_type_or_automatic(self):
        assert DeploySettings().kv_cache is None, "automatic unless the person chooses"
        chosen = DeploySettings(kv_cache="q4_0")
        assert DeploySettings.from_dict(chosen.as_dict()) == chosen
        assert "kv_cache" not in DeploySettings().as_dict()
        assert chosen.changed(kv_cache=None).kv_cache is None
        for bad in ("q5_1", "auto", "", 8):
            with pytest.raises(SettingsError):
                DeploySettings(kv_cache=bad)
        with pytest.raises(SettingsError):
            DeploySettings.from_dict({"kv_cache": "q8_0; reboot"})

    def test_automatic_is_q8_0_where_little_ram_is_left_and_f16_otherwise(self):
        assert kv_cache_default(2 * GIB)[0] == "q8_0"
        assert kv_cache_default(4 * GIB, model_bytes=int(1.5 * GIB))[0] == "q8_0"
        assert kv_cache_default(16 * GIB)[0] == "f16"
        assert kv_cache_default(16 * GIB, model_bytes=13 * GIB)[0] == "q8_0", "a big model leaves little room"
        kind, left = kv_cache_default(8 * GIB, model_bytes=2 * GIB)
        assert kind == "f16" and left == pytest.approx(4.4, abs=0.1)

    def test_the_choice_says_where_it_comes_from(self):
        settings = DeploySettings()
        little = settings.kv_choice(Profile(ram_mb=2048))
        assert (little.kind, little.why, little.automatic) == ("q8_0", "little", True)
        assert little.free_gib is not None and "little memory" in little.describe()
        enough = settings.kv_choice(Profile(ram_mb=16384))
        assert (enough.kind, enough.why) == ("f16", "enough")
        unknown = settings.kv_choice(Profile())
        assert (unknown.kind, unknown.why) == ("f16", "unknown"), "a board that was not probed is not guessed at"
        assert settings.kv_choice(None).why == "unknown"
        # opt-out: f16 chosen on a board with little RAM stays f16; opt-in: q4_0 on a roomy board stays q4_0
        out = DeploySettings(kv_cache="f16").kv_choice(Profile(ram_mb=2048))
        assert (out.kind, out.why, out.automatic) == ("f16", "chosen", False)
        assert DeploySettings(kv_cache="q4_0").kv_choice(Profile(ram_mb=65536)).kind == "q4_0"
        assert "person's choice" in out.describe()

    def test_the_systemd_unit_starts_the_server_with_the_type(self):
        values = dict(user="dietpi", home="/home/dietpi", model="/home/dietpi/models/q.gguf", port=8080)
        unit = launcher.render_systemd_unit(**values, kv_cache="q8_0")
        exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        assert exec_line.endswith("--port 8080 -ctk q8_0 -ctv q8_0")
        assert "-ctk" not in launcher.render_systemd_unit(**values, kv_cache="f16")
        assert launcher.render_systemd_unit(**values) == launcher.render_systemd_unit(**values, kv_cache=None)
        with pytest.raises(PolicyError):
            launcher.render_systemd_unit(**values, kv_cache="q8_0\nExecStartPre=/bin/sh")

    def test_the_start_command_names_the_type(self):
        from orchestrator.runtime.device.remote import start_command
        command = start_command("/home/dietpi", "/home/dietpi/models/q.gguf", 8080, "q4_0")
        assert command.endswith("start /home/dietpi/models/q.gguf -ctk q4_0 -ctv q4_0")
        assert start_command("/home/dietpi", None, 9090, None).endswith("start MODEL.gguf --port 9090")

    def test_the_packages_deploy_script_names_the_type(self):
        from orchestrator.Core.deployment_manager import DeploymentManager
        dm = DeploymentManager()
        script = dm._generate_deploy_script("model.gguf", "", False, kv_cache="q8_0")
        line = next(ln for ln in script.splitlines() if "Start it with" in ln).strip()
        said = subprocess.run(["sh", "-c", line], env={"RUNTIME": "/r.sh", "MODEL_DIR": "/m", "PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, timeout=30).stdout
        assert said == "Start it with: sh /r.sh start /m/model.gguf -ctk q8_0 -ctv q8_0\n"
        assert "-ctk" not in dm._generate_deploy_script("model.gguf", "", False)
        with pytest.raises(ValueError):
            dm._generate_deploy_script("model.gguf", "", False, kv_cache="q8_0; rm -rf ~")


@POSIX
class TestKvOnTheBoard:
    """The start script itself checks the type: the board never takes another word."""

    @pytest.fixture
    def home(self, tmp_path):
        home = tmp_path / "board-home"
        home.mkdir()
        (home / "model.gguf").write_bytes(b"GGUF")
        tdr.install_folder(home)
        return home

    @staticmethod
    def run(home, *args):
        script = home / launcher.INSTALL_REL / "current" / launcher.START_SCRIPT
        return subprocess.run(["sh", str(script), *args], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, timeout=60, cwd=str(home))

    def test_the_types_are_passed_on(self, home):
        res = self.run(home, "serve", str(home / "model.gguf"), "-ctk", "q8_0", "--cache-type-v", "q4_0")
        assert res.returncode == 0, res.stderr
        args = [line[4:] for line in res.stdout.splitlines() if line.startswith("ARG=")]
        assert args[-4:] == ["-ctk", "q8_0", "--cache-type-v", "q4_0"]

    @pytest.mark.parametrize("extra, message", [
        (["-ctk", "q5_1"], "not a valid value"),
        (["-ctv", "q8_0;reboot"], "not a valid value"),
        (["-ctk", ""], "not a valid value"),
        (["--cache-type-k=q8_0"], "separate argument"),
        (["-ctk"], "needs a value"),
    ])
    def test_other_words_are_refused(self, home, extra, message):
        res = self.run(home, "serve", str(home / "model.gguf"), *extra)
        assert res.returncode == 2 and message in res.stderr, res.stderr
        assert "ARG=" not in res.stdout


@POSIX
class TestKvDeployedToTheBoard:

    def test_the_unit_and_the_start_command_carry_the_type(self, tmp_path, source, device_base):
        build, packaged = tdr.packaged_build(tmp_path, source)
        dev = tdr.FakeDevice(device_base)
        (dev.home / "models").mkdir()
        (dev.home / "models" / "qwen.gguf").write_bytes(b"GGUF")
        settings = DeploySettings(install="systemd", ssh_user="dietpi", model="models/qwen.gguf",
                                  sudo_confirmed_at=tdr.CONFIRMED, kv_cache="q4_0")
        tdr.deploy(dev, build, packaged, settings=settings, kv_cache="q4_0")
        unit = (dev.fakeroot / "etc" / "systemd" / "system" / launcher.UNIT_NAME).read_text()
        assert "-ctk q4_0 -ctv q4_0" in unit

        dev2 = tdr.FakeDevice(tmp_path / "second")
        result = tdr.deploy(dev2, build, packaged, settings=tdr.USER, kv_cache="q8_0")
        (note,) = [n for n in result.notes if n.startswith("start the server with:")]
        assert note.endswith("start MODEL.gguf -ctk q8_0 -ctv q8_0 (KV cache q8_0, from the deploy settings)")


source = tdr.source
device_base = tdr.device_base


class TestKvCli:

    @pytest.fixture(autouse=True)
    def keep_logger_levels(self):
        import logging
        saved = {name: logging.getLogger(name).level for name in list(logging.Logger.manager.loggerDict)}
        root = logging.getLogger().level
        yield
        for name in list(logging.Logger.manager.loggerDict):
            logging.getLogger(name).setLevel(saved.get(name, logging.NOTSET))
        logging.getLogger().setLevel(root)

    def test_the_person_chooses_the_type_or_automatic(self):
        from test_cli_builds import invoke_plain
        store, device_id = tdr.stored_device()  # an RK3566 with 3.8 GB of RAM
        shown = invoke_plain("device", "settings", device_id)
        out = " ".join(shown.out.split())
        assert shown.code == 0 and "KV cache: Automatic: q8_0 (little memory is left" in out
        res = invoke_plain("device", "settings", device_id, "--kv-cache", "f16")
        assert res.code == 0 and "KV cache: f16 (your choice)" in " ".join(res.out.split())
        assert DeploySettings.from_dict(store.load_deploy_settings(device_id)).kv_cache == "f16"
        back = invoke_plain("device", "settings", device_id, "--kv-cache", "auto")
        assert back.code == 0 and DeploySettings.from_dict(store.load_deploy_settings(device_id)).kv_cache is None
        assert invoke_plain("device", "settings", device_id, "--kv-cache", "q5_1").code == 2


# --- N3: "tested on board X" -----------------------------------------------------------------------------------------

def report(passed=True, smoke=True):
    bench = [{"n_prompt": 64, "n_gen": 0, "avg_ts": 21.5}, {"n_prompt": 0, "n_gen": 16, "avg_ts": 4.25}]
    checks = [Check("version", passed, "b1"), Check("smoke", smoke, "pp 21.5 t/s", {"bench": bench} if smoke else {})]
    return VerifyReport(checks=checks, ran_binaries=True)


def a_build(output: Path, name: str, model_bytes: bytes, file_name: str = "model-q4_0.gguf") -> Path:
    import hashlib
    folder = output / name
    folder.mkdir(parents=True)
    (folder / file_name).write_bytes(model_bytes)
    manifest = {"model": "SmolLM2", "format": "gguf", "quantization": "Q4_0", "created": "2026-10-16T08:00:00Z",
                "primary": file_name,
                "artifacts": [{"file": file_name, "sha256": hashlib.sha256(model_bytes).hexdigest()}]}
    (folder / "build_info.json").write_text(json.dumps(manifest))
    return folder


DEVICE = SimpleNamespace(device_id="d_0123456789abcdef", label="Living room board")
BOARD = Profile(ram_mb=3912, BOARD_MODEL="Radxa ZERO 3", SOC_MODEL="rk3566")


def entry_for(model_bytes: bytes, **kw):
    import hashlib
    values = dict(device=DEVICE, profile=BOARD, report=report(), runtime_build_id="b" * 64, llama_cpp="b1 abcdef12",
                  kv_cache="q8_0")
    values.update(kw)
    return tested.make_entry(model_sha256=hashlib.sha256(model_bytes).hexdigest(), **values)


class TestTestedOnBoard:

    def test_the_record_lands_with_every_build_of_that_model(self, tmp_path):
        output = tmp_path / "output"
        first = a_build(output, "build-a", b"GGUF one model")
        renamed = a_build(output, "build-b", b"GGUF one model", file_name="renamed.gguf")
        other = a_build(output, "build-c", b"GGUF another model")
        entry = entry_for(b"GGUF one model")
        written = tested.record_device_test([output], entry)
        assert sorted(written) == sorted([first, renamed]), "found by sha256, never by file name or path"
        assert tested.device_tests(other) == []
        (row,) = tested.device_tests(first)
        assert row["schema"] == tested.SCHEMA and row["board"] == "Radxa ZERO 3" and row["soc"] == "rk3566"
        assert row["label"] == "Living room board" and row["kv_cache"] == "q8_0"
        assert (row["prompt_ts"], row["gen_ts"]) == (21.5, 4.25) and row["smoke_passed"] and row["checks_passed"]
        assert tested.tested_on(first) == ["Radxa ZERO 3"] and tested.tested_on(other) == []

    def test_only_a_passed_run_counts_as_tested(self, tmp_path):
        output = tmp_path / "output"
        build = a_build(output, "build-a", b"GGUF model")
        tested.record_device_test([output], entry_for(b"GGUF model", report=report(passed=False)))
        tested.record_device_test([output], entry_for(b"GGUF model", report=report(smoke=False)))
        assert len(tested.device_tests(build)) == 2 and tested.tested_on(build) == []
        tested.record_device_test([output], entry_for(b"GGUF model", profile=Profile(ram_mb=2048)))
        assert tested.tested_on(build) == ["Living room board"], "no board model in the probe: the person's label"

    def test_a_model_no_build_made_is_recorded_nowhere(self, tmp_path):
        output = tmp_path / "output"
        a_build(output, "build-a", b"GGUF model")
        assert tested.record_device_test([output], entry_for(b"GGUF copied by hand")) == []
        assert tested.record_device_test([tmp_path / "missing"], entry_for(b"GGUF model")) == []
        assert tested.builds_with_model([output], "not a sha256") == []

    def test_board_text_is_cleaned_and_a_damaged_record_reads_as_none(self, tmp_path):
        output = tmp_path / "output"
        build = a_build(output, "build-a", b"GGUF model")
        noisy = Profile(ram_mb=2048, BOARD_MODEL="Radxa\x1b[31m ZERO\n3 <b>x</b>" + "y" * 200, SOC_MODEL="rk3566")
        tested.record_device_test([output], entry_for(b"GGUF model", profile=noisy))
        (row,) = tested.device_tests(build)
        assert "\x1b" not in row["board"] and "\n" not in row["board"] and len(row["board"]) <= 64
        (build / tested.DEVICE_TESTS).write_text("{not json")
        assert tested.device_tests(build) == [] and tested.tested_on(build) == []
        (build / tested.DEVICE_TESTS).write_text(json.dumps({"schema": "other", "tests": [{"smoke_passed": True}]}))
        assert tested.device_tests(build) == []

    def test_the_records_are_capped(self, tmp_path):
        output = tmp_path / "output"
        build = a_build(output, "build-a", b"GGUF model")
        for _ in range(tested.MAX_TESTS + 5):
            tested.record_device_test([output], entry_for(b"GGUF model"))
        assert len(tested.device_tests(build)) == tested.MAX_TESTS

    def test_a_benchmark_that_did_not_run_records_nothing(self, tmp_path):
        from orchestrator.runtime.device.service import record_board_test
        output = tmp_path / "output"
        build = a_build(output, "build-a", b"GGUF model")
        import hashlib
        digest = hashlib.sha256(b"GGUF model").hexdigest()
        source = SimpleNamespace(build_number=1, commit="abcdef1234567890")
        skipped = VerifyReport(checks=[Check("version", True), Check("smoke", None, "not allowed")])
        assert record_board_test([output], DEVICE, BOARD, skipped, digest, "b" * 64, source) == []
        assert record_board_test([output], DEVICE, BOARD, report(), None, "b" * 64, source) == []
        assert record_board_test([output], DEVICE, BOARD, report(), digest, "b" * 64, source) == [build]
        assert tested.device_tests(build)[0]["llama_cpp"] == "b1 abcdef12"

    def test_the_deployment_window_shows_tested_on(self, tmp_path):
        pytest.importorskip("PySide6")
        from orchestrator.gui.deployment_window import tested_suffix
        output = tmp_path / "output"
        build = a_build(output, "build-a", b"GGUF model")
        assert tested_suffix(build) == ""
        tested.record_device_test([output], entry_for(b"GGUF model"))
        assert tested_suffix(build) == " · tested on Radxa ZERO 3"

    def test_no_benchmark_of_a_board_model_is_recorded_from_this_computer(self):
        """The record is written only from a run on the board (service.benchmark and the CLI's device runtime
        verify/deploy); the local benchmark tool and runner never write it."""
        root = Path(__file__).resolve().parents[1]
        for rel in ("orchestrator/runtime/bench.py", "orchestrator/Core/dataset_manager.py"):
            text = (root / rel).read_text(encoding="utf-8")
            assert "record_device_test" not in text and "device_tests" not in text, rel
        assert os.path.basename(tested.DEVICE_TESTS) == "device_tests.json"


# --- the Deployment window: KV cache, waiting for the board, the benchmark on a board -------------------------------

@pytest.fixture
def window_framework(make_framework, tmp_path):
    from test_input_history import framework as make_window_framework
    return make_window_framework.__wrapped__(make_framework, tmp_path)


@pytest.fixture
def told(monkeypatch):
    from orchestrator.gui import plain
    shown = []
    monkeypatch.setattr(plain, "_exec", lambda box: shown.append(
        (box.text(), box.standardButton(box.defaultButton()) if box.defaultButton() else None)) or
        plain.QMessageBox.No)
    return shown


@pytest.mark.usefixtures("qapp")
class TestDeploymentWindow:

    def test_the_kv_cache_is_automatic_or_the_persons_choice(self, window_framework, told):
        from orchestrator.gui.deployment_window import DeploymentWindow
        win = DeploymentWindow(window_framework)
        assert win.cb_kv.currentData() == "auto"
        assert "f16" in win.cb_kv.itemText(0) and "not known yet" in win.cb_kv.itemText(0)
        assert win.kv_choice() == ("f16", None), "automatic: nothing is kept in the settings"
        win.cb_kv.setCurrentIndex(win.cb_kv.findData("q4_0"))
        assert win.kv_choice() == ("q4_0", "q4_0")
        win.cb_kv.setCurrentIndex(win.cb_kv.findData("f16"))
        assert win.kv_choice() == ("f16", "f16"), "the opt-out is kept as the person's choice"

    def test_the_wait_for_the_board_is_visible_and_can_be_cancelled(self, window_framework, told):
        from orchestrator.Core.deployment_manager import DeployEvent
        from orchestrator.gui.deployment_window import DeploymentWindow, DeploymentWorker
        win = DeploymentWindow(window_framework)
        worker = DeploymentWorker(window_framework, Path("x"), "Rockchip", {}, "myboard.local", "radxa", None)
        win.worker = worker
        win._on_event(DeployEvent("uploading", done=500, total=1000))
        assert win.progress_bar.maximum() == 1000 and win.progress_bar.value() == 500
        win._on_event(DeployEvent("waiting", elapsed=65, limit=300, step="upload"))
        assert win.lbl_status.text().startswith("Waiting for the board at myboard.local: 1:05 of at most 5:00")
        assert win.progress_bar.maximum() == 0, "busy while it waits"
        closed = []
        worker._on_client(SimpleNamespace(close=lambda: closed.append(True)))
        win.btn_cancel.setEnabled(True)
        win._on_cancel()
        assert worker._stop.is_set() and closed == [True] and not win.btn_cancel.isEnabled()
        worker._on_client(SimpleNamespace(close=lambda: closed.append("late")))
        assert closed[-1] == "late", "a session opened after the cancel is closed at once"

    def test_a_failure_names_the_reason_and_the_next_step(self, window_framework):
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.gui.deployment_window import DeploymentWorker
        worker = DeploymentWorker(window_framework, Path("x"), "Rockchip", {}, "myboard.local", "radxa", None)
        text = worker._failure_text(DeployResult(False, code="device_port_closed", reason="Target not reachable."))
        assert "myboard.local" in text and "port 22" in text and "SSH server" in text
        assert "Target not reachable." in text

    def test_the_benchmark_on_a_board_after_a_deploy(self, window_framework, told, monkeypatch):
        paramiko = pytest.importorskip("paramiko")
        from orchestrator.gui import deployment_window
        from orchestrator.runtime.device.trusted import TrustedDevices
        state = Path(window_framework.config.user_state_dir)
        device = TrustedDevices(state).record("myboard.local", 22, "radxa", paramiko.ECDSAKey.generate(),
                                              via="gui", paired=True, label="Radxa on the shelf")
        win = deployment_window.DeploymentWindow(window_framework)
        assert win.cb_bench_board.currentData() == device.device_id and win.btn_bench.isEnabled()
        assert win.cb_bench_board.currentText() == "Radxa on the shelf", "the board by the person's name"
        win.worker = SimpleNamespace(device_id=device.device_id, model_name="smol-q4_0.gguf")
        win._on_finished(True, "Deployment successful.")
        assert win.txt_bench_model.text() == "models/smol-q4_0.gguf"
        assert "Benchmark on a board" in told[-1][0]

        started = []

        class Worker:
            def __init__(self, framework, device_id, model, allowed):
                started.append((device_id, model, allowed))
                self.finished = SimpleNamespace(connect=lambda slot: None)

            def start(self):
                pass
        monkeypatch.setattr(deployment_window, "BoardBenchWorker", Worker)
        win._on_bench()
        question, default = told[-1]
        assert "outside a sandbox" in question and default == deployment_window.QMessageBox.No
        assert started == [(device.device_id, "models/smol-q4_0.gguf", False)], "No: only a catalog model runs"

    def test_the_bench_worker_measures_on_the_board_and_records_the_consent(self, window_framework, monkeypatch):
        paramiko = pytest.importorskip("paramiko")
        from orchestrator.gui.deployment_window import BoardBenchWorker
        from orchestrator.runtime.device import service as dev
        from orchestrator.runtime.device.trusted import TrustedDevices
        state = Path(window_framework.config.user_state_dir)
        device = TrustedDevices(state).record("myboard.local", 22, "radxa", paramiko.ECDSAKey.generate(),
                                              via="gui", paired=True, label="Radxa")
        seen = {}

        def benchmark(state_dir, dev_, profile, settings, fp, *, allow_untrusted, consent, output_dirs, **kw):
            seen.update(model=settings.model, allowed=allow_untrusted, fp=fp, outputs=output_dirs)
            consent("ab" * 32)
            return {"ok": True, "prompt_ts": 21.5, "gen_ts": 4.25, "recorded_builds": 1,
                    "checks": [{"name": "smoke", "result": "passed"}]}
        monkeypatch.setattr(dev, "device_profile", lambda s, d: BOARD)
        monkeypatch.setattr(dev, "read_key", lambda d, s: "SHA256:trusted")
        monkeypatch.setattr(dev, "benchmark", benchmark)
        results = []
        worker = BoardBenchWorker(window_framework, device.device_id, "models/smol.gguf", True)
        worker.finished.connect(lambda ok, text: results.append((ok, text)))
        worker.run()
        ((ok, text),) = results
        assert ok and "21.5 t/s" in text and "tested on Radxa ZERO 3" in text
        assert seen["model"] == "models/smol.gguf" and seen["allowed"] is True and seen["fp"] == "SHA256:trusted"
        rows = window_framework.tool_registry.audit.entries(limit=20)
        assert any(r.get("tool") == "device_runtime_untrusted_model" for r in rows), "the yes is on record first"

        def unreachable(*a, **k):
            raise dev.DeviceRuntimeError("cannot be used now", code="hardware_unavailable",
                                         reason="device_host_down")
        monkeypatch.setattr(dev, "read_key", unreachable)
        results.clear()
        worker.run()
        ((ok, text),) = results
        assert not ok and "does not answer" in text and "myboard.local" in text
