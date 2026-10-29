"""The board's hardware probe drives every build (stage C wave 3c-2, DECISIONS round 23 items 5 and 15).

- The build service takes the probe the request names, else the newest stored probe of a board of the request's SoC
  (HardwareProbes.for_board), refuses a named probe of another board, and hands its id to the build engine.
- The build engine writes the probe into the build's folder and mounts it read-only into every build container (the
  importance-matrix job and the build) with HARDWARE_CONFIG_FILE: the config stage plans the build for that board.
- Formats follow the probe (build_formats.format_refusal, the one verdict): RKLLM and RKNN only with the Rockchip NPU
  in the probe, otherwise GGUF; RKLLM only for the RK3588, RK3576, RK3562 and RV1126B and only on an x86_64 build
  computer (rkllm-toolkit 1.3.1 ships linux_x86_64 wheels only); RKNN also on an ARM build computer.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.build_formats import (FormatVerdict, build_host_arch, format_refusal, format_verdicts,
                                             probe_formats, probe_npu)
from orchestrator.runtime.profile import ProfileStore, parse_profile

PROBES = REPO_ROOT / "tests" / "fixtures" / "hardware_probe"
RK3588 = parse_profile((PROBES / "schema2_rk3588_devuan_sysvinit.txt").read_text(encoding="utf-8"))
RK3566 = parse_profile((PROBES / "schema2_rk3566_dietpi.txt").read_text(encoding="utf-8"))


def without_npu(profile):
    """The board with its NPU switched off (no rknpu driver loaded): the probe finds no NPU node."""
    data = {k: v for k, v in profile.as_dict().items()
            if k not in ("NPU_VENDOR", "NPU_MODEL", "NPU_DEVICE", "RKNPU_DRIVER_VERSION")}
    data["SUPPORTS_RKNN"] = "OFF"
    return parse_profile("\n".join(f"{k}={v}" for k, v in data.items()) + "\n")


RK3588_NO_NPU = without_npu(RK3588)


def with_soc(profile, soc):
    """The same board probe with another SoC_MODEL."""
    data = {**profile.as_dict(), "SOC_MODEL": soc}
    return parse_profile("\n".join(f"{k}={v}" for k, v in data.items()) + "\n")


def rockchip_caps():
    from orchestrator.Core.target_manager import read_target_folders
    from orchestrator.schemas.targets import TargetCapabilities
    folders = read_target_folders(REPO_ROOT / "targets")
    return TargetCapabilities.model_validate(folders["Rockchip"][1]["capabilities"])


# --- the one verdict ------------------------------------------------------------------------------------------------

class TestFormatsFollowTheProbe:

    def test_an_rk3588_with_its_npu_is_offered_every_format(self):
        assert probe_npu(RK3588) == "rockchip"
        assert [f for _, f in probe_formats(rockchip_caps(), profile=RK3588, host_arch="x86_64")] == \
            ["gguf", "rkllm", "rknn"]

    def test_an_rk3566_gets_rknn_but_no_rkllm_with_the_reason(self):
        """The RK3566 has an NPU (RKNN for vision and audio models), but RKLLM does not convert for it."""
        caps = rockchip_caps()
        assert [f for _, f in probe_formats(caps, profile=RK3566, host_arch="x86_64")] == ["gguf", "rknn"]
        reason = format_refusal("rkllm", profile=RK3566, host_arch="x86_64")
        assert reason == ("RKLLM-Toolkit 1.3.1 converts for the RK3588, RK3576, RK3562 and RV1126B only, not for the "
                          "RK3566: an LLM runs on its CPU there, so build GGUF.")
        assert format_refusal("rkllm", board="rk3568") is not None

    def test_a_board_without_npu_gets_gguf_only(self):
        assert probe_npu(RK3588_NO_NPU) is None
        verdicts = format_verdicts(rockchip_caps(), profile=RK3588_NO_NPU, host_arch="x86_64")
        assert [(v.format, v.offered) for v in verdicts] == [("gguf", True), ("rkllm", False), ("rknn", False)]
        assert verdicts[1].reason == ("The board's hardware probe shows no Rockchip NPU, and RKLLM runs on that NPU "
                                      "only: build GGUF, which runs on the board's CPU.")
        assert isinstance(verdicts[0], FormatVerdict) and verdicts[0].reason == ""

    def test_on_an_arm_build_computer_rkllm_is_refused_and_rknn_and_gguf_run_natively(self):
        caps = rockchip_caps()
        assert [f for _, f in probe_formats(caps, profile=RK3588, host_arch="arm64")] == ["gguf", "rknn"]
        assert format_refusal("rkllm", profile=RK3588, host_arch="arm64") == (
            "RKLLM-Toolkit 1.3.1 ships Linux x86_64 wheels only, and this build computer is arm64: build GGUF here "
            "(it converts natively), or build RKLLM on an x86_64 computer.")
        assert format_refusal("rknn", profile=RK3588, host_arch="arm64") is None
        assert format_refusal("gguf", profile=RK3588_NO_NPU, host_arch="arm64") is None

    def test_without_a_probe_only_the_build_computer_and_the_soc_rule_apply(self):
        assert format_refusal("rknn") is None and format_refusal("rkllm", board="rk3588", host_arch="x86_64") is None
        assert [f for _, f in probe_formats(rockchip_caps())] == ["gguf", "rkllm", "rknn"]

    @pytest.mark.parametrize("machine, arch", [("x86_64", "x86_64"), ("AMD64", "x86_64"), ("aarch64", "arm64"),
                                               ("arm64", "arm64"), ("riscv64", "riscv64")])
    def test_the_build_computer_s_architecture(self, machine, arch, monkeypatch):
        assert build_host_arch(machine) == arch
        import platform
        monkeypatch.setattr(platform, "machine", lambda: machine)
        assert build_host_arch() == arch


# --- the board's probe -------------------------------------------------------------------------------------------

def store_probes(state: Path):
    """Three stored board probes: an RK3588, an RK3566 and an RK3588 without its NPU (newest last)."""
    store = ProfileStore(state)
    ids = {}
    for n, (name, profile) in enumerate((("rk3566", RK3566), ("rk3588", RK3588), ("rk3588-no-npu", RK3588_NO_NPU))):
        device_id = f"{n + 1:016x}"
        store.save_device(device_id, profile, label=name)
        stamp = 1_700_000_000 + n * 100
        os.utime(store.device_path(device_id), (stamp, stamp))
        ids[name] = device_id
    return ids


def test_the_board_s_probe_is_the_newest_stored_probe_of_its_soc(tmp_path):
    from orchestrator.runtime.probe_service import HardwareProbes
    ids = store_probes(tmp_path / "state")
    probes = HardwareProbes(tmp_path / "state")
    assert probes.for_board("rk3588").probe_id == ids["rk3588-no-npu"]
    assert probes.for_board("RK3566").probe_id == ids["rk3566"]
    assert probes.for_board("rk3576") is None and probes.for_board(None) is None


@pytest.fixture
def service(tmp_path):
    from orchestrator.Core.target_manager import TargetManager
    from test_build_tools import Cfg, Orchestrator, Service
    framework = SimpleNamespace(config=Cfg(tmp_path), resolve_path=lambda p: Path(p), orchestrator=Orchestrator())
    framework.run_coroutine = lambda coro, timeout=None: asyncio.run(coro)
    framework.target_manager = TargetManager(framework)
    svc = Service(framework)
    svc.ids = store_probes(Path(framework.config.user_state_dir))
    return svc


def request(**values):
    from orchestrator.Core.build_service import BuildService
    return BuildService.request_in(**{"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "format": "gguf",
                                      **values})


class TestTheBuildServiceTakesTheBoardsProbe:

    def test_a_named_probe_reaches_the_build_engine(self, service):
        service.start_build(request(board="rk3588", probe=service.ids["rk3588"]))
        sent = service.framework.orchestrator.requests[-1]
        assert sent.hardware_probe == service.ids["rk3588"] and sent.target_board == "rk3588"

    def test_without_a_named_probe_the_board_s_newest_probe_is_used(self, service):
        assert service.validate(request(board="rk3566")).probe == service.ids["rk3566"]
        assert service.validate(request(board="rk3576")).probe is None  # none stored: the target's defaults

    def test_an_npu_format_without_the_npu_in_the_probe_is_refused(self, service):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request(board="rk3588", format="rkllm", probe=service.ids["rk3588-no-npu"]))
        assert exc.value.code == "not_supported" and "shows no Rockchip NPU" in str(exc.value)
        assert "gguf" in exc.value.next
        ok = service.validate(request(board="rk3588", format="rkllm", probe=service.ids["rk3588"]))
        assert ok.probe == service.ids["rk3588"] and ok.quantization == "w8a8"

    def test_rkllm_for_an_rk3566_is_refused_with_the_reason(self, service):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request(board="rk3566", format="rkllm"))
        assert "not for the RK3566" in str(exc.value)
        assert service.validate(request(board="rk3566", format="rknn")).probe == service.ids["rk3566"]

    def test_rkllm_on_an_arm_build_computer_is_refused_and_gguf_offered(self, service, monkeypatch):
        import platform

        from orchestrator.Core.build_service import BuildServiceError
        monkeypatch.setattr(platform, "machine", lambda: "aarch64")
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request(board="rk3588", format="rkllm", probe=service.ids["rk3588"]))
        assert "this build computer is arm64" in str(exc.value)
        assert service.validate(request(board="rk3588", format="rknn", probe=service.ids["rk3588"])).format == "rknn"
        assert service.validate(request(board="rk3588")).format == "gguf"

    def test_a_probe_of_another_board_is_refused(self, service):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request(board="rk3588", probe=service.ids["rk3566"]))
        assert exc.value.code == "invalid_value" and exc.value.field == "probe"
        assert "SoC rk3566, not of rk3588" in str(exc.value)

    def test_a_probe_of_a_board_that_is_not_one_of_the_target_s_is_refused(self, service):
        """Review: an RK3399 is no board of the Rockchip target, so canonical_board gave nothing and the probe was
        taken for an RK3588 build: its CPU and its NPU would have planned the package."""
        from orchestrator.Core.build_service import BuildServiceError
        ProfileStore(Path(service.framework.config.user_state_dir)).save_device(
            "00000000000000aa", with_soc(RK3588, "rk3399"), label="rk3399")
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request(board="rk3588", probe="00000000000000aa"))
        assert exc.value.code == "invalid_value" and exc.value.field == "probe"
        assert "SoC rk3399, not of rk3588" in str(exc.value)

    def test_a_probe_of_a_pc_is_refused_for_an_aarch64_board(self, service):
        """Review: a PC's probe has no SoC, so it was taken for an RK3588 build (the Builder tab's scan of this
        computer sets probe 'local'): the config stage planned an x86_64 package for the board."""
        from orchestrator.Core.build_service import BuildServiceError
        pc = parse_profile((PROBES / "schema2_linux_z1_extreme.txt").read_text(encoding="utf-8"))
        assert pc.get("SOC_MODEL") is None and pc.arch == "x86_64"
        ProfileStore(Path(service.framework.config.user_state_dir)).save_device("00000000000000bb", pc, label="pc")
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request(board="rk3588", probe="00000000000000bb"))
        assert exc.value.field == "probe"
        assert str(exc.value) == ("The hardware probe 00000000000000bb is of an x86_64 system, and the target "
                                  "Rockchip builds for aarch64.")

    def test_a_variant_of_the_board_s_soc_is_the_board(self, service):
        """An RK3588S board (Radxa ROCK 5A: device tree rockchip,rk3588s) is an RK3588 for the build."""
        ProfileStore(Path(service.framework.config.user_state_dir)).save_device(
            "00000000000000cc", with_soc(RK3588, "rk3588s"), label="rock-5a")
        ok = service.validate(request(board="rk3588", format="rkllm", probe="00000000000000cc"))
        assert ok.probe == "00000000000000cc" and ok.board == "rk3588"

    def test_an_unknown_probe_is_refused_with_the_next_step(self, service):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError) as exc:
            service.validate(request(board="rk3588", probe="f" * 16))
        assert exc.value.code == "unknown_id" and "get_hardware_probe" in exc.value.next

    def test_a_probe_id_must_be_one(self):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError):
            request(probe="../../etc/passwd")

    def test_the_probe_is_an_option_of_an_identical_build(self, service):
        with_probe = service.request_options(service.validate(request(board="rk3588",
                                                                      probe=service.ids["rk3588"])))
        without = service.request_options(service.validate(request(board="rk3576")))
        assert with_probe["hardware_sha256"] and without["hardware_sha256"] is None
        from orchestrator.Core import build_identity
        assert build_identity.compare(with_probe, {**with_probe, "hardware_sha256": None})[1] == ["hardware_sha256"]
        old = {k: v for k, v in without.items() if k != "hardware_sha256"}  # a manifest from before the option
        assert "hardware_sha256" not in build_identity.compare(without, old)[1]


# --- the build engine mounts it ----------------------------------------------------------------------------------

from test_review_build_engine import client, config_for, engine, make_engine  # noqa: E402,F401


@pytest.mark.parametrize("stage, use_imatrix", [("_execute_build_modules", False), ("_generate_imatrix", True)])
def test_every_build_container_gets_the_board_s_probe_read_only(engine, client, tmp_path, stage, use_imatrix):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    state = Path(engine.config.user_state_dir)
    ids = store_probes(state)
    dataset = tmp_path / "calib.txt"
    dataset.write_text("text")
    cfg = config_for(tmp_path, target_board="rk3588", hardware_probe=ids["rk3588"], use_imatrix=use_imatrix,
                     dataset_path=str(dataset))
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    client.stage_options["llm-"] = {"outputs": {}}  # the stage writes nothing: only its options matter here
    image = SimpleNamespace(id="sha256:img", tags=["img"])
    try:
        getattr(engine, stage)(cfg, progress, image, engine.targets_dir / "Rockchip", ("/build-cache/m", {}))
    except RuntimeError:
        pass  # the fake image writes no imatrix.dat / model: only the container's options matter here
    created = client.created[-1]
    mounts = {bind["bind"]: (host, bind["mode"]) for host, bind in created["volumes"].items()}
    host, mode = mounts["/build-cache/hardware"]
    assert mode == "ro"
    written = Path(host) / "target_hardware_config.txt"
    assert written.read_text(encoding="utf-8") == RK3588.stable().to_text()
    assert created["environment"]["HARDWARE_CONFIG_FILE"] == "/build-cache/hardware/target_hardware_config.txt"
    import hashlib
    assert cfg.hardware_sha256 == hashlib.sha256(RK3588.stable().to_text().encode("utf-8")).hexdigest()
    assert any("Hardware probe of the board: rk3588" in line for line in progress.logs)


def test_a_build_without_a_probe_mounts_none(engine, client, tmp_path):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    cfg = config_for(tmp_path)
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    client.stage_options["llm-"] = {"outputs": {}}
    try:
        engine._execute_build_modules(cfg, progress, SimpleNamespace(id="sha256:img", tags=["img"]),
                                      engine.targets_dir / "Rockchip", ("/build-cache/m", {}))
    except RuntimeError:
        pass
    created = client.created[-1]
    assert "/build-cache/hardware" not in {b["bind"] for b in created["volumes"].values()}
    assert "HARDWARE_CONFIG_FILE" not in created["environment"]


def test_a_probe_that_is_gone_stops_the_build_with_the_reason(engine, client, tmp_path):
    from orchestrator.Core.builder import BuildProgress, BuildStatus
    cfg = config_for(tmp_path, hardware_probe="e" * 16)
    progress = BuildProgress("b1", BuildStatus.BUILDING, "")
    engine._builds["b1"] = progress
    with pytest.raises(RuntimeError, match=r"The hardware probe of the board \(e{16}\) cannot be used"):
        engine._execute_build_modules(cfg, progress, SimpleNamespace(id="sha256:img", tags=["img"]),
                                      engine.targets_dir / "Rockchip", ("/build-cache/m", {}))
    assert client.created == []


def test_the_orchestrator_hands_the_probe_to_the_build_configuration(tmp_path):
    from test_review_build_engine import make_request

    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.orchestrator import BuildJob, LLMOrchestrator
    orch = LLMOrchestrator(ConfigManager(tmp_path / "configs"), build_engine=SimpleNamespace())
    req = make_request(tmp_path, hardware_probe="a" * 16, target_board="rk3588")
    job = SimpleNamespace(job_id="j1", source_model=req.models[0], target_architecture="Rockchip",
                          target_format=req.target_formats[0], output_path=str(tmp_path / "out"),
                          optimization=req.optimization_level, quantization="Q4_K_M")
    assert BuildJob  # the job type the orchestrator maps
    cfg = orch._map_job_to_config(job, req)
    assert cfg.hardware_probe == "a" * 16 and cfg.target_board == "rk3588"


# --- the Builder tab: the formats of the probe it loaded, and the probe in its request ----------------------------

@pytest.fixture
def builder_tab(booted_framework, qapp):
    from orchestrator.gui.builder_tab import BuilderTab
    tab = BuilderTab(booted_framework)
    tab.target_combo.setCurrentIndex(tab.target_combo.findData("Rockchip"))
    yield tab
    tab.deleteLater()


def _formats(tab):
    return [tab.format_combo.itemData(i) for i in range(tab.format_combo.count())]


def test_the_builder_offers_npu_formats_only_with_the_npu_of_the_loaded_probe(builder_tab, tmp_path):
    board = tmp_path / "no_npu.txt"
    board.write_text(RK3588_NO_NPU.to_text(), encoding="utf-8")
    builder_tab._load_probe_config(board)
    assert builder_tab.target_combo.currentData() == "Rockchip"
    assert _formats(builder_tab) == ["gguf"] and builder_tab.format_combo.currentData() == "gguf"
    builder_tab._load_probe_config(PROBES / "schema2_rk3588_devuan_sysvinit.txt")
    assert _formats(builder_tab) == ["gguf", "rkllm", "rknn"]
    builder_tab._load_probe_config(PROBES / "schema2_rk3566_dietpi.txt")
    assert _formats(builder_tab) == ["gguf", "rknn"], "the RK3566 has an NPU, but RKLLM does not convert for it"


def test_the_builder_s_request_names_the_probe_it_imported(builder_tab):
    from orchestrator.Core.builder import ModelFormat
    values = {"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "target_fmt": ModelFormat.GGUF,
              "board": "rk3588", "quant": "Q4_K_M", "use_imatrix": False, "dataset": None, "use_gpu": False,
              "recipe": None, "probe": "0123456789abcdef"}
    assert builder_tab._service_request(values).probe == "0123456789abcdef"
    assert builder_tab._service_request({**values, "probe": None}).probe is None


@pytest.mark.parametrize("soc, board, fits", [
    ("rk3588", "rk3588", True), ("RK3588", "rk3588", True), ("rk3588s", "rk3588", True), (None, "rk3588", True),
    ("rk3588", None, True), ("rk3399", "rk3588", False), ("rk3566", "rk3588", False), ("rk35881", "rk3588", False),
    ("rk3588", "rk3588s", False), ("rk3588sxyz", "rk3588", False)])
def test_a_probe_s_soc_against_the_board(soc, board, fits):
    from orchestrator.runtime.probe_service import soc_fits
    assert soc_fits(soc, board) is fits


@pytest.mark.parametrize("arch, family, fits", [
    ("arm64", "aarch64", True), ("x86_64", "aarch64", False), ("arm64", "x86_64", False), ("x86_64", "x86_64", True),
    ("x86_64", None, True), (None, "aarch64", True), ("armv7", "aarch64", False)])
def test_a_probe_s_architecture_against_the_target(arch, family, fits):
    from orchestrator.runtime.probe_service import arch_fits
    assert arch_fits(arch, family) is fits


def test_a_probe_that_fits_no_target_drives_no_build_of_the_builder(builder_tab, tmp_path):
    """Review: a scan of this PC (probe 'local') fits no Rockchip board, yet its profile hid the NPU formats of the
    Rockchip target and its id went into the next Rockchip build, which the build service then refused with no way
    in the tab to build without it. A probe drives the formats and the build of the target it matched only."""
    builder_tab._probe_id = "local"  # what the scan of this computer sets before it loads the file
    builder_tab._load_probe_config(PROBES / "schema2_linux_z1_extreme.txt")
    builder_tab.target_combo.setCurrentIndex(builder_tab.target_combo.findData("Rockchip"))
    assert _formats(builder_tab) == ["gguf", "rkllm", "rknn"]
    assert builder_tab._board_probe_id("Rockchip") is None
    # the board's own probe drives the Rockchip build, and only that target's
    builder_tab._probe_id = "0123456789abcdef"
    board = tmp_path / "no_npu.txt"
    board.write_text(RK3588_NO_NPU.to_text(), encoding="utf-8")
    builder_tab._load_probe_config(board)
    assert builder_tab._board_probe_id("Rockchip") == "0123456789abcdef"
    assert builder_tab._board_probe_id("SomeOtherTarget") is None
    assert _formats(builder_tab) == ["gguf"]
