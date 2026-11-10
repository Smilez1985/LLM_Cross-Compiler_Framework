"""The pre-flight check before a build (Core/consistency_manager.py; DESIGN, "Build pipeline and target modules").

- What it compares, on the probes' recorded data (tests/fixtures/hardware_probe): this computer's NVIDIA driver with the
  CUDA version of the target's GPU image for a GPU build, the board's NPU driver and runtime with the format's sdk
  declaration. Only a certain failure stops; what may work is a warning; a missing value is "not checked".
- Every build start runs it (BuildService.start_build for the CLI, the Builder, recipes and the AI tools;
  continue_interrupted; the build plan of check_build_request and the start question), and a stop starts nothing.
- Only the person overrides a stop, for one build (--ignore-preflight or the question; Build anyway in the Builder);
  an AI caller only with exactly the stops the person accepted in the start question.
- The result shows in the CLI (build preflight, build start) and the GUI (the Builder's button and its log).
"""

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core import consistency_manager as preflight
from orchestrator.Core.build_service import BuildService, PreflightStopped
from orchestrator.runtime.profile import ProfileStore, parse_profile

PROBES = REPO_ROOT / "tests" / "fixtures" / "hardware_probe"
TEMPLATE = REPO_ROOT / "targets" / "_template"
ROCKCHIP = REPO_ROOT / "targets" / "Rockchip"


def recorded(name):
    return parse_profile((PROBES / name).read_text(encoding="utf-8"))


def changed(profile, **values):
    """The recorded probe with some values set (None removes a key)."""
    data = {**profile.as_dict(), **values}
    return parse_profile("\n".join(f"{k}={v}" for k, v in data.items() if v is not None) + "\n")


PC = recorded("schema2_pc_two_nvidia.txt")                      # two NVIDIA GPUs, driver CUDA 13.0
NO_NVIDIA = recorded("schema2_linux_ivybridge.txt")              # an Intel iGPU only
RK3588 = recorded("schema2_rk3588_devuan_sysvinit.txt")          # driver version needs root (not in the probe)


def board(driver=None, rknn=None, rkllm=None):
    """The recorded RK3588 with its NPU driver and runtime versions as a probe run with sudo finds them."""
    return changed(RK3588, PROBE_WARNINGS=None, RKNPU_DRIVER_VERSION=driver, RKNN_RUNTIME_VERSION=rknn,
                   RKLLM_RUNTIME_VERSION=rkllm)


def rockchip_sdk(fmt):
    from orchestrator.Core.target_manager import read_target_folders
    from orchestrator.schemas.targets import TargetCapabilities
    caps = TargetCapabilities.model_validate(read_target_folders(REPO_ROOT / "targets")["Rockchip"][1]["capabilities"])
    return caps.formats[fmt].sdk


CUDA_12_9 = preflight.GpuImage(True, "12.9.1", "Dockerfile.gpu")


def codes(result):
    return [f.code for f in result.findings]


# --- what it compares ----------------------------------------------------------------------------------------------

class TestTheTargetsGpuImage:

    def test_the_template_names_its_cuda_version(self):
        assert preflight.gpu_image(TEMPLATE) == CUDA_12_9

    def test_the_targets_build_args_win(self):
        image = preflight.gpu_image(TEMPLATE, {"CUDA_VERSION": "13.0.2"})
        assert (image.cuda, image.source) == ("13.0.2", "the target's build_args")

    def test_an_nvidia_cuda_base_image_names_it(self, tmp_path):
        (tmp_path / "Dockerfile.gpu").write_text("FROM nvidia/cuda:12.4.1-devel-ubuntu22.04@sha256:" + "0" * 64
                                                 + " AS builder\n", encoding="utf-8")
        assert preflight.gpu_image(tmp_path).cuda == "12.4.1"

    def test_a_target_without_a_gpu_image(self, tmp_path):
        assert preflight.gpu_image(ROCKCHIP) == preflight.GpuImage(False)
        (tmp_path / "Dockerfile.gpu").write_text("FROM ubuntu:22.04\n", encoding="utf-8")
        assert preflight.gpu_image(tmp_path) == preflight.GpuImage(True)


class TestThisComputerForAGpuBuild:

    @pytest.mark.parametrize("driver_cuda, image, found, stopped", [
        ("13.0", "12.9.1", [], False),                  # a newer driver runs an older CUDA
        ("12.9", "12.9.1", [], False),
        ("12.4", "12.9.1", ["cuda_minor"], False),      # minor version compatibility: a warning
        ("11.8", "12.9.1", ["cuda_driver"], True),      # an older major: the CUDA runtime refuses the driver
        ("12.9", "13.0.2", ["cuda_driver"], True),
    ])
    def test_the_drivers_cuda_against_the_images(self, driver_cuda, image, found, stopped):
        result = preflight.check(fmt="gguf", use_gpu=True, host_profile=changed(PC, NVIDIA_DRIVER_CUDA_MAX=driver_cuda),
                                 image=preflight.GpuImage(True, image, "Dockerfile.gpu"))
        assert codes(result) == found and result.stopped is stopped
        assert result.checked == [f"the NVIDIA driver of this computer (CUDA {driver_cuda}) with the image's CUDA "
                                  f"{image}"]
        if stopped:
            assert result.verdict() == "stopped" and result.stop_codes() == ["cuda_driver"]
            (stop,) = result.stops()
            assert stop.where == "this computer" and "Update the NVIDIA driver" in stop.next
            assert result.lines()[0].endswith("nothing was started.")

    def test_no_nvidia_gpu_runs_on_the_cpu_with_a_warning(self):
        result = preflight.check(fmt="gguf", use_gpu=True, host_profile=NO_NVIDIA, image=CUDA_12_9)
        assert codes(result) == ["no_nvidia_gpu"] and not result.stopped

    def test_a_target_without_a_gpu_image_is_a_warning(self):
        result = preflight.check(fmt="gguf", use_gpu=True, host_profile=PC, image=preflight.GpuImage(False))
        assert codes(result) == ["no_gpu_image"] and not result.stopped

    def test_what_the_probe_does_not_have_is_not_checked_never_guessed(self):
        result = preflight.check(fmt="gguf", use_gpu=True, host_profile=None, image=CUDA_12_9)
        assert result.findings == [] and "could not be probed" in result.unchecked[0]
        result = preflight.check(fmt="gguf", use_gpu=True, host_profile=changed(PC, NVIDIA_DRIVER_CUDA_MAX=None),
                                 image=CUDA_12_9)
        assert result.findings == [] and "driver's CUDA version is not in this computer's probe" in result.unchecked[0]
        assert result.verdict() == "passed" and "Not checked:" in "\n".join(result.lines())

    def test_a_cpu_build_compares_nothing_of_this_computer(self):
        result = preflight.check(fmt="gguf", host_profile=changed(PC, NVIDIA_DRIVER_CUDA_MAX="11.0"), image=CUDA_12_9)
        assert result.findings == [] and result.checked == [] and result.unchecked == []
        assert result.summary() == "Pre-flight check: nothing to compare for this build."


class TestTheBoardForAnNpuFormat:

    @pytest.mark.parametrize("driver, runtime, found, stopped", [
        ("0.9.8", "2.3.2", [], False),
        ("0.9.8", "2.5.0", [], False),
        ("0.9.6", "2.3.2", ["npu_driver_old"], False),
        ("0.8.8", "2.3.2", ["npu_driver"], True),       # below Rockchip's minimum 0.9.2: refused, as the engine does
        ("0.9.8", "1.6.0", ["npu_runtime"], False),     # the bundle can bring its pinned runtime
        ("0.9.8", "2.2.0", ["npu_runtime"], False),
    ])
    def test_rknn(self, driver, runtime, found, stopped):
        result = preflight.check(fmt="rknn", board_name="rk3588", sdk=rockchip_sdk("rknn"),
                                 board_profile=board(driver=driver, rknn=runtime))
        assert codes(result) == found and result.stopped is stopped
        assert len(result.checked) == 2 and result.unchecked == []
        if stopped:
            (stop,) = result.stops()
            assert stop.where == "the board" and "at least 0.9.2" in stop.message and "build GGUF" in stop.next

    def test_rkllm_compares_the_runtime_and_declares_no_driver_minimum(self):
        sdk = rockchip_sdk("rkllm")
        assert (sdk.toolkit, sdk.version, sdk.driver_key) == ("RKLLM-Toolkit", "1.3.1", None)
        old = preflight.check(fmt="rkllm", board_name="rk3588", sdk=sdk, board_profile=board(driver="0.8.0",
                                                                                              rkllm="1.2.1"))
        assert codes(old) == ["npu_runtime"] and not old.stopped
        assert preflight.check(fmt="rkllm", board_name="rk3588", sdk=sdk,
                               board_profile=board(rkllm="1.3.1")).findings == []

    def test_a_driver_version_that_needs_root_is_not_checked_with_how_to_get_it(self):
        result = preflight.check(fmt="rknn", board_name="rk3588", sdk=rockchip_sdk("rknn"), board_profile=RK3588)
        assert result.findings == []
        assert any("run the probe on the board with sudo" in u for u in result.unchecked)

    def test_without_a_probe_of_the_board_nothing_of_it_is_compared(self):
        result = preflight.check(fmt="rknn", board_name="rk3588", sdk=rockchip_sdk("rknn"), board_profile=None)
        assert result.findings == [] and "no hardware probe of rk3588" in result.unchecked[0]

    def test_gguf_declares_no_sdk_of_the_board(self):
        assert rockchip_sdk("gguf") is None

    def test_the_sdk_declaration_is_checked_by_the_schema(self):
        from pydantic import ValidationError

        from orchestrator.schemas.targets import SdkNeeds
        with pytest.raises(ValidationError):
            SdkNeeds(toolkit="T", version="1.0", driver_min="0.9.2")            # a minimum needs its probe key
        with pytest.raises(ValidationError):
            SdkNeeds(toolkit="<b>T</b>", version="1.0")                         # a closed name only
        with pytest.raises(ValidationError):
            SdkNeeds(toolkit="T", version="1.0; rm -rf", runtime_key="RKNN_RUNTIME_VERSION")

    def test_a_probe_value_that_is_no_version_is_never_shown(self):
        result = preflight.check(fmt="rknn", board_name="rk3588", sdk=rockchip_sdk("rknn"),
                                 board_profile=SimpleNamespace(get=lambda k: "0.8.8 $(reboot)", warnings=[]))
        assert result.findings == [] and "$(" not in " ".join(result.lines())


# --- every build start runs it ---------------------------------------------------------------------------------------

def store(state: Path, name: str, profile) -> str:
    device_id = f"{abs(hash(name)) % 16 ** 16:016x}"
    ProfileStore(state).save_device(device_id, profile, label=name)
    return device_id


@pytest.fixture
def service(tmp_path):
    from orchestrator.Core.target_manager import TargetManager
    from test_build_tools import Cfg, Orchestrator, Service
    framework = SimpleNamespace(config=Cfg(tmp_path), resolve_path=lambda p: Path(p), orchestrator=Orchestrator())
    framework.run_coroutine = lambda coro, timeout=None: asyncio.run(coro)
    framework.target_manager = TargetManager(framework)
    svc = Service(framework)
    state = Path(framework.config.user_state_dir)
    svc.bad = store(state, "old-driver", board(driver="0.8.8", rknn="2.3.2"))
    svc.old = store(state, "older-runtime", board(driver="0.9.8", rknn="1.6.0"))
    svc.good = store(state, "good", board(driver="0.9.8", rknn="2.3.2"))
    return svc


def rknn(probe, **values):
    return BuildService.request_in(**{"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "format": "rknn",
                                      "board": "rk3588", "probe": probe, **values})


class TestEveryBuildStartRunsIt:

    def test_a_stop_starts_nothing(self, service):
        with pytest.raises(PreflightStopped) as exc:
            service.start_build(rknn(service.bad), local_paths=True)
        assert exc.value.code == "needs_person" and exc.value.preflight.stop_codes() == ["npu_driver"]
        assert "build anyway" in exc.value.next and exc.value.problems[0].startswith("Pre-flight check:")
        assert service.framework.orchestrator.requests == []

    def test_the_person_builds_anyway_and_the_build_keeps_it_in_its_warnings(self, service):
        service.start_build(rknn(service.bad), local_paths=True, ignore_preflight=True)
        (sent,) = service.framework.orchestrator.requests
        assert sent.preflight_notes and sent.preflight_notes[0].startswith(
            "Pre-flight check overridden by the person: the NPU driver of rk3588 is 0.8.8")

    def test_warnings_go_into_the_started_build(self, service):
        service.start_build(rknn(service.old), local_paths=True)
        (sent,) = service.framework.orchestrator.requests
        assert len(sent.preflight_notes) == 1 and "librknnrt on rk3588 is 1.6.0" in sent.preflight_notes[0]
        service.start_build(rknn(service.good), local_paths=True)
        assert service.framework.orchestrator.requests[-1].preflight_notes == []

    def test_an_ai_caller_never_overrides_a_stop_on_its_own(self, service):
        with pytest.raises(PreflightStopped) as exc:
            service.start_build(rknn(service.bad), owner="ditto", ignore_preflight=True)
        assert "only they can start this build anyway" in exc.value.next
        with pytest.raises(PreflightStopped):   # a stop the person was not shown
            service.start_build(rknn(service.bad), owner="ditto", preflight_accepted=["cuda_driver"])
        assert service.framework.orchestrator.requests == []
        service.start_build(rknn(service.bad), owner="ditto", preflight_accepted=["npu_driver"])
        assert service.framework.orchestrator.requests[-1].preflight_notes[0].startswith("Pre-flight check overridden")

    def test_a_recipe_build_runs_it_too(self, service, monkeypatch):
        recipe = SimpleNamespace(id="r", options=SimpleNamespace(dataset=None))
        monkeypatch.setattr(service, "recipe_request",
                            lambda rid, dataset_path=None, use_gpu=False: (rknn(None), recipe, None))
        with pytest.raises(PreflightStopped):
            service.start_recipe("r", local_paths=True, probe=service.bad)
        service.start_recipe("r", local_paths=True, probe=service.bad, ignore_preflight=True)
        assert len(service.framework.orchestrator.requests) == 1

    def test_a_continued_build_is_checked_again(self, service):
        from orchestrator.Core.builder import ModelFormat, OptimizationLevel
        kept = SimpleNamespace(target_arch="Rockchip", target_format=ModelFormat.RKNN, target_board="rk3588",
                               hardware_probe=service.bad, use_gpu=False, model_source="Qwen/Qwen2.5-0.5B-Instruct",
                               model_branch="main", output_dir="out", optimization_level=OptimizationLevel.BALANCED,
                               quantization="fp16", max_context_length=None, build_id="b1")
        with pytest.raises(PreflightStopped):
            service.continue_interrupted(kept)
        assert service.framework.orchestrator.requests == []
        service.continue_interrupted(kept, ignore_preflight=True)
        assert service.framework.orchestrator.requests[-1].resume is kept

    def test_the_continue_of_the_person_carries_the_result_for_the_question(self, service, monkeypatch):
        from orchestrator.Core import interrupted_build
        item = SimpleNamespace(label="b1", build_id="b1")
        monkeypatch.setattr(interrupted_build, "configuration", lambda i: SimpleNamespace(
            target_arch="Rockchip", target_format="rknn", target_board="rk3588", hardware_probe=service.bad,
            use_gpu=False, model_source="m/m", model_branch="main", output_dir="out", optimization_level=None,
            quantization="fp16", max_context_length=None, build_id="b1"))
        monkeypatch.setattr(interrupted_build, "_settled", lambda *a: None)
        with pytest.raises(interrupted_build.InterruptedBuildError) as exc:
            interrupted_build.continue_build(None, item, "cli", service=service)
        assert exc.value.preflight is not None and exc.value.preflight.stopped

    def test_a_gpu_build_with_a_driver_too_old_for_the_image_stops(self, service, monkeypatch):
        monkeypatch.setattr(preflight, "gpu_image", lambda folder, args=None: CUDA_12_9)
        service.host_profile = lambda: changed(PC, NVIDIA_DRIVER_CUDA_MAX="11.8")
        gguf = BuildService.request_in(model="Qwen/Qwen2.5-0.5B-Instruct", target="Rockchip", format="gguf",
                                       board="rk3576", use_gpu=True)
        with pytest.raises(PreflightStopped) as exc:
            service.start_build(gguf, local_paths=True)
        assert exc.value.preflight.stop_codes() == ["cuda_driver"]
        service.host_profile = lambda: PC
        service.start_build(gguf, local_paths=True)
        assert service.framework.orchestrator.requests[-1].preflight_notes == []

    def test_the_orchestrator_shows_the_notes_as_the_requests_warnings(self, booted_framework):
        from orchestrator.Core.builder import ModelFormat, OptimizationLevel
        from orchestrator.Core.orchestrator import (BuildRequest, LLMOrchestrator, PriorityLevel, WorkflowType)
        orch = LLMOrchestrator(booted_framework.config, build_engine=SimpleNamespace())
        orch._ensure_worker_running = lambda: None   # queued only: nothing runs
        request = BuildRequest("", WorkflowType.SIMPLE_CONVERSION, PriorityLevel.NORMAL, ["m/m"], ["Rockchip"],
                               [ModelFormat.RKNN], OptimizationLevel.BALANCED, [], False, "out",
                               preflight_notes=["Pre-flight check: a warning"])
        rid = asyncio.run(orch.submit_build_request(request))
        assert orch._workflows[rid].warnings == ["Pre-flight check: a warning"]


# --- the AI's tools: the start question and the dry run ----------------------------------------------------------------

class TestTheAisStartQuestion:

    @pytest.fixture
    def env(self, tmp_path):
        pytest.importorskip("mcp")
        from orchestrator.Core.target_manager import TargetManager
        from orchestrator.tools.audit import AuditLog
        from orchestrator.tools.framework_tools import register_framework_tools
        from orchestrator.tools.policy import PolicyStore, ToolPolicy
        from orchestrator.tools.registry import ToolRegistry
        from test_build_tools import Cfg, Orchestrator, Service
        framework = SimpleNamespace(config=Cfg(tmp_path), resolve_path=lambda p: Path(p), orchestrator=Orchestrator())
        framework.run_coroutine = lambda coro, timeout=None: asyncio.run(coro)
        framework.target_manager = TargetManager(framework)
        framework.build_service = Service(framework)
        registry = ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "audit.db"))
        register_framework_tools(registry, framework)
        registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
        bad = store(Path(framework.config.user_state_dir), "old-driver", board(driver="0.8.8", rknn="2.3.2"))
        return SimpleNamespace(framework=framework, service=framework.build_service, registry=registry,
                               orch=framework.orchestrator, tmp=tmp_path, bad=bad)

    def arguments(self, env):
        return {"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "format": "rknn", "board": "rk3588",
                "probe": env.bad}

    def test_check_build_request_names_the_stop(self, env):
        from orchestrator.tools.registry import ToolContext
        out = env.registry.call("check_build_request", self.arguments(env), ToolContext("ditto"))
        assert out.preflight.verdict == "stopped" and out.preflight.findings[0].code == "npu_driver"
        assert out.problems == [] and env.orch.requests == []

    def test_only_the_persons_yes_starts_it_and_never_a_standing_approval(self, env):
        from orchestrator.schemas.build import StartBuildIn
        from test_build_tools import ask
        plan = env.service.plan(StartBuildIn(**self.arguments(env)))
        assert plan.grant is None and plan.grant_scope is None and plan.bound()["preflight"] == ["npu_driver"]
        assert [w.code for w in plan.warnings() if w.code == "preflight_error"] == ["preflight_error"]
        out, shown = ask(env, self.arguments(env), answer=True)
        assert "Pre-flight check" in shown[0] and "0.8.8" in shown[0] and "Needs you" in shown[0]
        (sent,) = env.orch.requests
        assert out.request_id == sent.request_id
        assert sent.preflight_notes[0].startswith("Pre-flight check overridden by the person")


# --- the CLI ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    made = make_cli(booted_framework, tmp_path, monkeypatch)
    state = Path(booted_framework.config.user_state_dir)
    made.bad = store(state, "old-driver", board(driver="0.8.8", rknn="2.3.2"))
    made.good = store(state, "good", board(driver="0.9.8", rknn="2.3.2"))
    return made


class TestTheCli:

    def test_build_preflight_shows_the_stop_and_exits_1(self, cli):
        result = cli.invoke("build", "preflight", "-t", "Rockchip", "-f", "rknn", "-b", "rk3588", "--probe", cli.bad)
        assert result.code == 1, result.out
        assert "the build cannot succeed as asked" in result.out and "Stops the build (the board)" in result.out
        assert cli.submitted == []

    def test_build_preflight_json_and_a_pass(self, cli):
        result = cli.invoke("build", "preflight", "-t", "Rockchip", "-f", "rknn", "-b", "rk3588", "--probe", cli.good,
                            "--json")
        assert result.code == 0, result.out
        data = json.loads(result.out[result.out.index("{"):])
        assert data["verdict"] == "passed" and len(data["checked"]) == 2

    def test_build_preflight_refuses_an_invalid_request_with_exit_2(self, cli):
        result = cli.invoke("build", "preflight", "-t", "Rockchip", "-f", "tensorrt")
        assert result.code == 2, result.out

    def test_build_start_stops_before_anything_starts(self, cli):
        result = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "Rockchip", "-f", "rknn", "-b",
                            "rk3588", "--probe", cli.bad, "-o", "-")
        assert result.code == 2, result.out
        assert "Stops the build" in result.out and "--ignore-preflight" in result.out
        assert cli.submitted == []

    def test_build_start_with_ignore_preflight_builds_and_notes_it(self, cli):
        result = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "Rockchip", "-f", "rknn", "-b",
                            "rk3588", "--probe", cli.bad, "-o", "-", "--ignore-preflight")
        assert result.code == 0, result.out
        (sent,) = cli.submitted
        assert sent.preflight_notes[0].startswith("Pre-flight check overridden by the person")

    def test_build_start_says_a_pass_in_one_line(self, cli):
        result = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "Rockchip", "-f", "rknn", "-b",
                            "rk3588", "--probe", cli.good, "-o", "-")
        assert result.code == 0, result.out
        assert "Pre-flight check: passed." in result.out and len(cli.submitted) == 1

    def test_build_start_asks_at_a_terminal_with_no_as_the_default(self, cli, monkeypatch):
        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        asked = []
        monkeypatch.setattr(cli_module.click, "confirm", lambda text, default=None: asked.append((text, default))
                            or False)
        result = cli.invoke("build", "start", "-m", "Qwen/Qwen2.5-0.5B-Instruct", "-t", "Rockchip", "-f", "rknn", "-b",
                            "rk3588", "--probe", cli.bad, "-o", "-")
        assert asked == [("Build anyway?", False)] and result.code == 2 and cli.submitted == []

    def test_build_interrupted_continue_asks_after_a_stop(self, cli, monkeypatch):
        from orchestrator import cli as cli_module
        from orchestrator.Core import interrupted_build
        stopped = preflight.check(fmt="rknn", board_name="rk3588", sdk=rockchip_sdk("rknn"),
                                  board_profile=board(driver="0.8.8"))
        calls = []

        def decide(*a, ignore_preflight=False, **k):
            calls.append(ignore_preflight)
            if not ignore_preflight:
                raise interrupted_build.InterruptedBuildError("b1 could not be continued now", preflight=stopped)
            return "req_1"

        monkeypatch.setattr(interrupted_build, "decide", decide)
        monkeypatch.setattr(cli_module, "_interrupted_context", lambda ctx: (None, Path("."), None))
        monkeypatch.setattr(cli_module, "_follow_build", lambda *a, **k: None)
        result = cli.invoke("build", "interrupted", "continue", "b1")   # no terminal: nothing continues
        assert result.code == 2 and calls == [False] and "Stops the build" in result.out
        calls.clear()
        result = cli.invoke("build", "interrupted", "continue", "b1", "--ignore-preflight")
        assert result.code == 0 and calls == [True], result.out


# --- the GUI -------------------------------------------------------------------------------------------------------------

@pytest.fixture
def builder_tab(booted_framework, qapp, service, monkeypatch):
    from orchestrator.gui.builder_tab import BuilderTab
    tab = BuilderTab(booted_framework)
    tab.target_combo.setCurrentIndex(tab.target_combo.findData("Rockchip"))
    monkeypatch.setattr(tab, "_build_service", lambda: service)
    for name in ("_track_build", "_remember_build"):
        monkeypatch.setattr(tab, name, lambda *a, **k: None)
    monkeypatch.setattr("orchestrator.gui.builder_tab.QMessageBox.information", lambda *a, **k: None)
    yield tab
    tab.deleteLater()


def values(probe):
    from orchestrator.Core.builder import ModelFormat, OptimizationLevel
    return {"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "target_fmt": ModelFormat.RKNN,
            "board": "rk3588", "quant": "fp16", "use_imatrix": False, "dataset": None, "use_gpu": False,
            "recipe": None, "probe": probe, "output_dir": "out", "opt_lvl": OptimizationLevel.BALANCED}


class TestTheBuilder:

    def test_start_build_asks_and_cancel_starts_nothing(self, builder_tab, service, monkeypatch):
        from orchestrator.gui import preflight_box
        asked = []
        monkeypatch.setattr(preflight_box, "ask_preflight", lambda parent, result, key="": asked.append(result)
                            or False)
        builder_tab._start_or_reuse(values(service.bad), [])
        assert len(asked) == 1 and asked[0].stop_codes() == ["npu_driver"]
        assert service.framework.orchestrator.requests == []
        log = builder_tab.log_area.toPlainText()
        assert "Stops the build (the board)" in log and "the pre-flight check stopped the build" in log

    def test_build_anyway_starts_it_with_the_override(self, builder_tab, service, monkeypatch):
        from orchestrator.gui import preflight_box
        monkeypatch.setattr(preflight_box, "ask_preflight", lambda parent, result, key="": True)
        builder_tab._start_or_reuse(values(service.bad), [])
        (sent,) = service.framework.orchestrator.requests
        assert sent.preflight_notes[0].startswith("Pre-flight check overridden by the person")

    def test_a_pass_is_written_into_the_log_and_nothing_is_asked(self, builder_tab, service, monkeypatch):
        from orchestrator.gui import preflight_box
        monkeypatch.setattr(preflight_box, "ask_preflight", lambda *a, **k: pytest.fail("asked without a stop"))
        builder_tab._start_or_reuse(values(service.good), [])
        assert len(service.framework.orchestrator.requests) == 1
        assert "Pre-flight check: passed." in builder_tab.log_area.toPlainText()

    def test_the_pre_flight_button_shows_the_check_without_starting(self, builder_tab, service, monkeypatch):
        from orchestrator.gui import plain
        shown = []
        monkeypatch.setattr(plain, "plain_box", lambda parent, kind, title, text, **k: shown.append((kind, text)))
        builder_tab.format_combo.setCurrentIndex(builder_tab.format_combo.findData("rknn"))
        monkeypatch.setattr(builder_tab, "_selected_board", lambda: "rk3588")
        monkeypatch.setattr(builder_tab, "_board_probe_id", lambda target: service.bad)
        assert builder_tab.btn_preflight.objectName() == "btn_builder_preflight"
        builder_tab.btn_preflight.click()
        assert shown and shown[0][0] == "warning" and "Stops the build (the board)" in shown[0][1]
        assert service.framework.orchestrator.requests == []

    def test_the_question_has_cancel_as_its_default(self, qapp, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        from orchestrator.gui import preflight_box
        seen = {}

        def exec_(box):
            seen["default"] = box.defaultButton().text()
            seen["text"] = box.text()
            return 0

        monkeypatch.setattr(QMessageBox, "exec", exec_)
        stopped = preflight.check(fmt="rknn", board_name="rk3588", sdk=rockchip_sdk("rknn"),
                                  board_profile=board(driver="0.8.8"))
        assert preflight_box.ask_preflight(None, stopped) is False
        assert "Cancel" in seen["default"] and "0.8.8" in seen["text"]


def test_the_old_consistency_manager_is_gone_and_its_module_is_the_check():
    """The class nothing called (it read keys the schema-2 probe no longer writes and assumed CUDA 11.8) is replaced
    by the check in the same module; nothing else of the framework keeps a second gate."""
    assert not hasattr(preflight, "ConsistencyManager") and callable(preflight.check)
    text = (REPO_ROOT / "orchestrator" / "Core" / "consistency_manager.py").read_text(encoding="utf-8")
    assert "11.8" not in text and "target_hardware_config.txt" not in text
    assert os.path.isfile(REPO_ROOT / "orchestrator" / "gui" / "preflight_box.py")
