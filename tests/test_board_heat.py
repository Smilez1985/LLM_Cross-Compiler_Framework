"""The board's temperature during the checks on it (DECISIONS round 23 item 16; stage C wave 3c-2, chain boards).

The framework reads the board's thermal zones (temp, type, trip points) and, on a Raspberry Pi, vcgencmd over the board
session it already has: one read-only command, nothing installed. It reads them during the runtime deploy's checks,
`device runtime verify` and the benchmark, before and after the short llama-bench run. A board at or past the limit (its
zone's own trip point, else a careful default per SoC family) gets a warning with what helps and a Mojo line; an AI gets
the same facts without a path or an account. No probe key: a temperature is a moment, not a property of the board.

The board is tests/test_device_runtime.py's FakeDevice (its thermal zones are in its fake root, none by default).
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_device_runtime as tdr
from test_remote_callers import device_base, source  # noqa: F401 - fixtures
from test_remote_callers_session import board_session, deploy_over  # noqa: F401 - fixtures

from orchestrator.runtime.build.verify import Check, VerifyReport
from orchestrator.runtime.device import thermal
from orchestrator.runtime.device.remote import RemoteShell, verify_on_device

POSIX = tdr.POSIX

PI_ZONES = ("zone thermal_zone0 52350 cpu-thermal\n"
            "compatible raspberrypi,4-model-b brcm,bcm2711\n")
RK_ZONES = ("zone thermal_zone0 86100 soc-thermal\n"
            "trip thermal_zone0 85000 passive\n"
            "trip thermal_zone0 95000 critical\n"
            "zone thermal_zone1 61000 gpu-thermal\n"
            "trip thermal_zone1 85000 passive\n"
            "compatible radxa,zero3 rockchip,rk3566\n")


# --- reading and judging --------------------------------------------------------------------------------------------

def test_a_raspberry_pi_zone_without_trip_points_is_judged_by_the_family_default():
    zones, vc, throttled, family = thermal.parse(PI_ZONES)
    assert family == "raspberry-pi" and vc is None and throttled is None
    assert zones == [thermal.Zone("thermal_zone0", "cpu-thermal", 52.35)]
    heat = thermal.judge(zones, vc, throttled, family)
    assert (heat.celsius, heat.limit, heat.limit_from, heat.zone, heat.hot) == (52.35, 80.0, "default",
                                                                                  "cpu-thermal", False)


def test_the_zone_closest_to_its_own_passive_trip_point_is_the_reading():
    heat = thermal.judge(*thermal.parse(RK_ZONES))
    assert (heat.zone, heat.limit, heat.limit_from, heat.family) == ("soc-thermal", 85.0, "trip", "rockchip")
    assert heat.hot and not heat.throttled


def test_a_default_stays_below_the_zone_s_critical_trip_point():
    zones, *_ = thermal.parse("zone thermal_zone0 66000 cpu\ntrip thermal_zone0 75000 critical\n")
    assert thermal.zone_limit(zones[0], "generic") == (65.0, "default")
    assert thermal.judge(zones, None, None, "generic").hot
    assert thermal.zone_limit(thermal.Zone("z", "", 50.0), "rockchip") == (85.0, "default")


def test_vcgencmd_alone_gives_the_reading_and_its_soft_limit_bit_means_hot():
    out = "vc temp=71.0'C throttled=0x80008\ncompatible raspberrypi,5-model-b brcm,bcm2712\n"
    heat = thermal.judge(*thermal.parse(out))
    assert heat.celsius == 71.0 and heat.limit == 80.0 and heat.throttled and heat.hot, \
        "the firmware's soft temperature limit is active: hot below the default"
    assert not thermal.judge(*thermal.parse("vc temp=55.5'C throttled=0x50000\n")).hot, \
        "only bit 0x8 (active now) counts, not the sticky bits of earlier events"


def test_the_board_s_answer_is_never_taken_as_text():
    out = ("zone thermal_zone0 2147483647 cpu\n"              # a sensor without a value
           "zone thermal_zone1 -273000 cpu\n"
           "zone thermal_zone2 48000 /home/someone/x\n"        # not a zone type
           "zone ../../etc 48000 cpu\n"
           "trip thermal_zone3 85000 passive; rm -rf\n"
           "zone thermal_zone4 47000 cpu-thermal\n"
           "random text 99000\n")
    zones, *_ = thermal.parse(out)
    assert [z.name for z in zones] == ["thermal_zone4"]
    assert thermal.judge([], None, None, "generic") is None


def test_hotter_keeps_the_reading_closer_to_its_limit():
    cool = thermal.BoardHeat(50.0, 80.0, "cpu", "default", "generic")
    warm = thermal.BoardHeat(70.0, 80.0, "cpu", "default", "generic")
    hot = thermal.BoardHeat(81.0, 85.0, "soc", "trip", "rockchip")   # 4 °C below its limit, but past nothing
    past = thermal.BoardHeat(86.0, 85.0, "soc", "trip", "rockchip")
    assert thermal.hotter(cool, warm) is warm and thermal.hotter(None, cool) is cool and thermal.hotter(None, None) is None
    assert thermal.hotter(hot, warm) is hot and thermal.hotter(warm, past) is past


def test_a_reading_round_trips_and_anything_else_reads_back_as_none():
    heat = thermal.judge(*thermal.parse(RK_ZONES))
    data = heat.as_dict()
    assert data == {"celsius": 86.1, "limit_celsius": 85.0, "zone": "soc-thermal", "limit_from": "trip",
                    "family": "rockchip", "hot": True, "throttled": False}
    back = thermal.from_dict(json.loads(json.dumps(data)))
    assert back.hot and back.zone == "soc-thermal" and back.limit_from == "trip"
    for bad in (None, "hot", {}, {**data, "zone": "/home/someone"}, {**data, "celsius": 900},
                {**data, "limit_from": "guess"}, {**data, "family": "somewhere"}):
        assert thermal.from_dict(bad) is None


def test_the_command_only_reads():
    command = thermal.THERMAL_COMMAND
    targets = re.findall(r"(?<![<2])>\s*(\S+)", command.replace("2>/dev/null", "").replace(">/dev/null 2>&1", ""))
    assert targets == [], f"the command writes nowhere: {targets}"
    for word in ("rm ", "echo 1 >", "tee", "mkdir", "apt", "pip", "curl", "wget", "sudo"):
        assert word not in command
    assert "/sys/class/thermal/thermal_zone" in command and "vcgencmd measure_temp" in command


def test_a_failed_read_is_no_reading_but_a_cancel_stays_a_cancel():
    from orchestrator.runtime.device.remote import RemoteError

    class Shell:
        def __init__(self, outcome):
            self.outcome, self.calls = outcome, []

        def run(self, command, **kw):
            self.calls.append(kw)
            if isinstance(self.outcome, BaseException):
                raise self.outcome
            return self.outcome

    assert thermal.read_heat(Shell(RemoteError("no answer", kind="failed"))) is None
    assert thermal.read_heat(Shell(SimpleNamespace(status=1, out=RK_ZONES))) is None
    shell = Shell(SimpleNamespace(status=0, out=RK_ZONES))
    assert thermal.read_heat(shell).hot and shell.calls == [{"again": True, "timeout": thermal.THERMAL_TIMEOUT}]
    with pytest.raises(RemoteError):
        thermal.read_heat(Shell(RemoteError("cancelled", kind="stopped")))


# --- the person's lines and the AI's text -----------------------------------------------------------------------------

def test_the_person_reads_the_temperature_and_for_a_hot_board_what_helps():
    fine = thermal.judge(*thermal.parse(PI_ZONES))
    assert thermal.person_lines(fine, "en") == ["Board temperature: 52.4 °C (cpu-thermal); it slows itself down from "
                                                "80 °C."]
    hot = thermal.judge(*thermal.parse(RK_ZONES))
    en = thermal.person_lines(hot, "en")
    assert en[0].startswith("The board is hot: 86.1 °C (soc-thermal). From 85 °C (the limit the board itself sets)")
    assert "heatsink or a fan" in en[1] and "fewer threads" in en[1] and "smaller quantization" in en[1] \
        and "pause between runs" in en[1]
    assert "Mojo Oracle" in en[2] and len(en) == 3
    de = thermal.person_lines(hot, "de")
    assert de[0].startswith("Das Board ist heiß: 86.1 °C") and "Kühlkörper" in de[1] and "Mojo Oracle" in de[2]
    generic = thermal.BoardHeat(81.0, 80.0, "cpu", "default", "generic", throttled=True)
    lines = thermal.person_lines(generic, "de")
    assert "ein vorsichtiger Standardwert, weil das Board keine eigene Grenze nennt" in lines[0]
    assert "this board" not in " ".join(lines) and "Firmware" in lines[1] and len(lines) == 4
    assert "a careful default for Raspberry Pi" in thermal.person_lines(
        thermal.BoardHeat(81.0, 80.0, "cpu", "default", "raspberry-pi"), "en")[0]
    assert thermal.person_lines(None, "en") == []


def test_the_ai_gets_the_same_facts_without_a_path_or_an_account():
    hot = thermal.judge(*thermal.parse(RK_ZONES))
    text = thermal.ai_text(hot)
    assert "86.1 °C (soc-thermal)" in text and "85 °C (the zone's own trip point)" in text
    assert thermal.ADVICE in text and "Tell the person." in text
    assert "/" not in text.replace("Q4_K_M instead of Q8_0", ""), "no path"
    assert thermal.ai_text(thermal.judge(*thermal.parse(PI_ZONES))) == "", "nothing to say about a cool board"


# --- on the board: the checks read it, the report keeps it ------------------------------------------------------------

def give_zones(board, celsius, trip=85.0, compatible="radxa,zero3\0rockchip,rk3566\0"):
    """The board's thermal zone (soc-thermal) and device tree, in its fake root."""
    zone = board.fakeroot / "sys" / "class" / "thermal" / "thermal_zone0"
    zone.mkdir(parents=True, exist_ok=True)
    (zone / "temp").write_text(f"{int(celsius * 1000)}\n")
    (zone / "type").write_text("soc-thermal\n")
    (zone / "trip_point_0_temp").write_text(f"{int(trip * 1000)}\n")
    (zone / "trip_point_0_type").write_text("passive\n")
    tree = board.fakeroot / "proc" / "device-tree"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "compatible").write_bytes(compatible.encode())


@pytest.fixture
def deployed(tmp_path, source, board_session):  # noqa: F811 - fixtures
    board, session, _events, _clock = board_session
    build, packaged = tdr.packaged_build(tmp_path, source)
    assert deploy_over(session, build, packaged).activated
    model = board.home / "models" / "fast.gguf"
    model.parent.mkdir(exist_ok=True)
    model.write_text("GGUF")
    return board, session, build, model


def checks(session, board, build, model=None):
    return verify_on_device(RemoteShell(session), home=str(board.home), plan_verify=build.plan["verify"],
                            pin_build=tdr.PIN.build_number, pin_commit=tdr.PIN.commit, port=8080,
                            bench_model=str(model) if model else None, bench_allowed=model is not None)


@POSIX
def test_the_checks_read_the_temperature_before_and_after_the_short_benchmark(deployed):
    board, session, build, model = deployed
    give_zones(board, 86.1)
    start = len(board.commands)   # the deploy's own checks (without a benchmark model) read it once already
    report = checks(session, board, build, model)
    assert report.ok, "a hot board fails no check"
    assert report.heat.hot and report.heat.zone == "soc-thermal" and report.heat.limit_from == "trip"
    reads = [c for c in board.commands[start:] if "thermal_zone" in c]
    assert len(reads) == 2, "before and after the benchmark"
    assert report.as_dict()["temperature"] == report.heat.as_dict()
    ran = board.commands[start:]
    at = [i for i, c in enumerate(ran) if "thermal_zone" in c]
    bench_at = next(i for i, c in enumerate(ran) if "-o json" in c)
    assert at[0] < bench_at < at[1]


@POSIX
def test_without_a_benchmark_it_is_read_once_and_a_board_without_zones_has_no_reading(deployed):
    board, session, build, _model = deployed
    report = checks(session, board, build)
    assert report.heat is None and "temperature" not in report.as_dict(), "the board shows no temperature"
    give_zones(board, 48.0)
    before = len([c for c in board.commands if "thermal_zone" in c])
    report = checks(session, board, build)
    assert len([c for c in board.commands if "thermal_zone" in c]) == before + 1
    assert report.heat.celsius == 48.0 and not report.heat.hot


@POSIX
def test_vcgencmd_on_a_raspberry_pi_counts(deployed):
    board, session, build, _model = deployed
    stub = board.stubs / "vcgencmd"
    stub.write_text("#!/bin/sh\ncase \"$1\" in measure_temp) echo \"temp=79.5'C\" ;; "
                    "get_throttled) echo throttled=0x8 ;; esac\n")
    stub.chmod(0o755)
    report = checks(session, board, build)
    assert report.heat.throttled and report.heat.hot and report.heat.zone == "soc (vcgencmd)"


# --- recorded for Mojo; the surfaces --------------------------------------------------------------------------------

def trusted(state):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    key = paramiko.ECDSAKey.generate()
    ssh_trust.trust_host_key(state, "192.0.2.41", 22, key)
    return TrustedDevices(state).record("192.0.2.41", 22, "radxa", key, via="cli", paired=True, label="Shelf board")


def test_mojo_names_a_board_that_was_hot_until_a_cooler_reading_replaces_it(tmp_path):
    from orchestrator.Core.mojo import Severity
    from orchestrator.Core.mojo.checks.devices import check_devices
    from orchestrator.runtime.device import steps
    state = tmp_path / "state"
    state.mkdir()
    device = trusted(state)
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=state))
    steps.record_heat(state, device.device_id, None)
    assert not [f for f in check_devices(framework) if f.code.endswith(".heat")], "no reading, nothing recorded"
    steps.record_heat(state, device.device_id, thermal.judge(*thermal.parse(RK_ZONES)))
    (found,) = [f for f in check_devices(framework) if f.code.endswith(".heat")]
    assert found.severity == Severity.WARN and "Shelf board" in found.message and "86.1 °C (soc-thermal)" in found.message
    assert "Mojo did not contact the board." in found.message and found.fix_hint == thermal.ADVICE
    assert found.where == "192.0.2.41" and found.why and found.cannot == "person"
    assert "radxa" not in found.message and "radxa" not in found.fix_hint, "never the device account"
    steps.record_heat(state, device.device_id, thermal.judge(*thermal.parse(PI_ZONES)))
    assert not [f for f in check_devices(framework) if f.code.endswith(".heat")], "a cooler reading replaced it"
    rows = json.loads((state / "device_deploys.json").read_text())
    assert rows[device.device_id]["heat"]["celsius"] == 52.4


def test_a_deploy_record_keeps_the_reading(tmp_path):
    """note_deploy keeps the board facts it does not write itself (BOARD_FACTS), the reading among them."""
    from orchestrator.runtime.device.trusted import BOARD_FACTS, TrustedDevices
    state = tmp_path / "state"
    state.mkdir()
    device = trusted(state)
    store = TrustedDevices(state)
    store.note_heat(device.device_id, thermal.judge(*thermal.parse(RK_ZONES)))
    store.note_deploy(device.device_id, True, "pkg")
    assert "heat" in BOARD_FACTS and store.last_heat(device)["heat"].hot


def test_the_service_benchmark_gives_the_ai_the_reading_and_records_it(tmp_path, monkeypatch):
    import orchestrator.runtime.device.remote as remote
    from orchestrator.runtime.device import service, steps
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.runtime.device.trusted import TrustedDevices
    DeviceRuntimeStore(tmp_path, "0123456789abcdef").save_record({"build_id": "b" * 64, "install": "user"})
    device = SimpleNamespace(device_id="0123456789abcdef", label="ZERO 3W")
    settings = DeploySettings(ssh_user="dietpi", model="models/q.gguf")
    monkeypatch.setattr(service, "connect", lambda *a, **k: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(remote, "RemoteShell", lambda client: client)
    monkeypatch.setattr(remote, "read_facts", lambda shell: SimpleNamespace(uid=1000, home="/home/dietpi"))
    monkeypatch.setattr(remote, "model_sha256", lambda shell, path: "e" * 64)
    monkeypatch.setattr(steps, "catalog_model_digests", lambda: {"e" * 64})
    heat = thermal.judge(*thermal.parse(RK_ZONES))
    monkeypatch.setattr(remote, "verify_on_device", lambda shell, **kw: VerifyReport([Check("version", True, "")],
                                                                                     heat=heat))
    out = service.benchmark(tmp_path, device, tdr.RK3566, settings, "SHA256:x", allow_untrusted=False)
    assert out["temperature"] == heat.as_dict() and out["temperature_note"] == thermal.ai_text(heat)
    text = json.dumps({k: out[k] for k in ("temperature", "temperature_note")})
    assert "dietpi" not in text and "/home" not in text, "no account, no path"
    rows = json.loads((tmp_path / "device_deploys.json").read_text())
    assert rows[device.device_id]["heat"]["hot"] is True
    assert DeviceRuntimeStore(tmp_path, device.device_id).record()["verification"]["temperature"]["hot"] is True
    monkeypatch.setattr(remote, "verify_on_device", lambda shell, **kw: VerifyReport([Check("version", True, "")]))
    out = service.benchmark(tmp_path, device, tdr.RK3566, settings, "SHA256:x", allow_untrusted=False)
    assert "temperature" not in out and "temperature_note" not in out
    assert TrustedDevices(tmp_path).last_deploys()[device.device_id]["heat"]["hot"] is True, \
        "no reading leaves the last one"


def test_the_cli_prints_the_warning_in_the_person_s_language(capsys):
    from orchestrator import cli
    report = VerifyReport([Check("smoke", True, "pp 12.0 t/s")], heat=thermal.judge(*thermal.parse(RK_ZONES)))
    cli._print_report(report, "de")
    out = " ".join(capsys.readouterr().out.split())
    assert "smoke: passed" in out and "Das Board ist heiß: 86.1 °C" in out and "Kühlkörper" in out
    cli._print_report(VerifyReport([Check("smoke", True, "")]), "en")
    assert "temperature" not in capsys.readouterr().out.lower()


def test_the_boards_window_shows_the_cli_s_lines():
    pytest.importorskip("PySide6")
    from orchestrator.gui.boards_window import BoardsWindow
    report = VerifyReport([Check("smoke", True, "")], heat=thermal.judge(*thermal.parse(RK_ZONES)))
    stub = SimpleNamespace(lang="en", _t=lambda key, **kw: key)
    lines = BoardsWindow._check_lines(stub, report)
    assert lines[-3:] == thermal.person_lines(report.heat, "en")


def test_the_deployment_tab_s_benchmark_shows_the_reading(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.gui.deployment_window import BoardBenchWorker
    from orchestrator.runtime.device import service as dev
    from orchestrator.runtime.device.trusted import TrustedDevices
    state = tmp_path / "state"
    state.mkdir()
    device = TrustedDevices(state).record("198.51.100.7", 22, "radxa", paramiko.ECDSAKey.generate(), via="gui",
                                          paired=True, label="Radxa")
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state), output_dir=str(tmp_path / "out")))
    heat = thermal.judge(*thermal.parse(RK_ZONES))
    monkeypatch.setattr(dev, "device_profile", lambda s, d: tdr.RK3566)
    monkeypatch.setattr(dev, "read_key", lambda d, s, step="": "SHA256:trusted")
    monkeypatch.setattr(dev, "benchmark", lambda *a, **k: {"ok": True, "prompt_ts": 21.5, "gen_ts": 4.25,
                                                            "recorded_builds": 0, "checks": [],
                                                            "temperature": heat.as_dict()})
    monkeypatch.setattr("orchestrator.Core.artifact_output.artifact_folders", lambda framework: [])
    results = []
    worker = BoardBenchWorker(framework, device.device_id, "models/smol.gguf", False)
    worker.finished.connect(lambda ok, text: results.append((ok, text)))
    worker.run()
    ((ok, text),) = results
    assert ok and "86.1 °C (soc-thermal)" in text and "heatsink" in text


def test_the_texts_exist_in_both_languages_with_the_same_placeholders():
    from orchestrator.utils.l10n_device import TEXTS
    keys = [k for k in TEXTS["en"] if k.startswith("device.heat.")]
    assert len(keys) == 8 and all(k in TEXTS["de"] for k in keys)
    for key in keys:
        assert set(re.findall(r"{(\w+)}", TEXTS["en"][key])) == set(re.findall(r"{(\w+)}", TEXTS["de"][key]))


def test_no_probe_key_for_the_temperature():
    """The probe's schema stays as it is: a temperature is a moment, not a property of the board."""
    text = (Path(__file__).resolve().parent.parent / "configs" / "hardware_probe_schema.yml").read_text().lower()
    assert "thermal" not in text and "temperature" not in text
