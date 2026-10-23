"""The Deployment tab's container table decides what a package with Docker images carries (DECISIONS round 23, item
6): the table lists the containers of deployment_manager.package_containers, the list `llmcf deploy package --docker`
packs (the image of the setting image_inference_runtime, the hardware access of the target profile), and the person's
ticks reach the one package step (make_package, person_steps.deploy_build) through package_docker_config. Every
container ticked is exactly what the CLI's --docker packs; none ticked packs nothing and says why."""

import zipfile
from pathlib import Path

import pytest

from test_deployment import INFERENCE_PINNED, FakeDockerClient, FakeFramework


# --- the core: one list of containers, one Docker part of a package ------------------------------------------------

def test_package_containers_name_the_image_and_the_profiles_hardware_access(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.deployment_manager import INFERENCE, package_containers
    config = ConfigManager(tmp_path / "configs")
    config.set("image_inference_runtime", INFERENCE_PINNED)
    (container,) = package_containers(FakeFramework(config), "rk3588")
    assert (container.key, container.image, container.flags) == (
        INFERENCE, INFERENCE_PINNED, ("--device /dev/dri", "--device /dev/mali0"))
    assert package_containers(FakeFramework(config), "")[0].flags == (), "no profile: no hardware access"


def test_package_containers_without_a_framework_name_the_default_image():
    from types import SimpleNamespace

    from orchestrator.Core.deployment_manager import DEFAULT_INFERENCE_IMAGE, package_containers
    (container,) = package_containers(SimpleNamespace(), "Rockchip")
    assert (container.image, container.flags) == (DEFAULT_INFERENCE_IMAGE, ())


@pytest.mark.parametrize("docker, containers, expected", [
    (False, None, {"use_docker": False}),
    (True, None, "all"),                      # the CLI's --docker
    (True, ["inference"], "all"),             # every container ticked: the same as --docker
    (True, ["inference", "nonsense"], "all"),  # a key no container has is ignored
    (True, [], {"use_docker": False}),        # none ticked: no Docker images
    (False, ["inference"], {"use_docker": False}),
])
def test_package_docker_config_follows_the_choice(docker, containers, expected):
    from orchestrator.Core.deployment_manager import DEFAULT_INFERENCE_IMAGE, package_docker_config
    if expected == "all":
        expected = {"use_docker": True, "image_name": DEFAULT_INFERENCE_IMAGE}
    assert package_docker_config(docker, containers) == expected
    assert package_docker_config(docker) == ({"use_docker": True, "image_name": DEFAULT_INFERENCE_IMAGE}
                                             if docker else {"use_docker": False}), "the CLI's call stays as it was"


def _package(tmp_path, docker_config):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.deployment_manager import DeploymentManager
    config = ConfigManager(tmp_path / "configs")
    config.set("image_inference_runtime", INFERENCE_PINNED)
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF fake weights")
    client = FakeDockerClient()
    package = DeploymentManager(FakeFramework(config, client)).create_package(model, "rk3588", docker_config,
                                                                            tmp_path / "out")
    with zipfile.ZipFile(package) as zf:
        names = zf.namelist()
        script = zf.read("deploy.sh").decode()
    return names, script, client


def test_create_package_packs_exactly_the_chosen_containers(tmp_path):
    names, script, client = _package(tmp_path / "a", {"use_docker": True, "containers": ["inference"]})
    assert client.pulled == [INFERENCE_PINNED] and any(n.startswith("images/") for n in names)
    assert "docker run -d --name llm-node" in script and "--device /dev/dri --device /dev/mali0" in script
    names, script, client = _package(tmp_path / "b", {"use_docker": True, "containers": []})
    assert client.pulled == [] and not any(n.startswith("images/") for n in names)
    assert "docker" not in script, "no container chosen: a package without Docker images"


def test_make_package_and_the_deploy_step_pass_the_choice(tmp_path):
    from types import SimpleNamespace

    from orchestrator.Core.deployment_manager import DeployResult, make_package
    from orchestrator.remote.address import Endpoint
    from orchestrator.runtime.device.person_steps import deploy_build
    seen = []

    class Manager:
        def create_package(self, artifact_path, profile_name, docker_config, output_dir, **extra):
            seen.append(dict(docker_config))
            return Path(output_dir) / "deploy_x.zip"

        def deploy_package(self, package, host, user, **options):
            return DeployResult(False, reason="not reached")

    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    framework = SimpleNamespace()
    make_package(Manager(), framework, str(model), "Rockchip", True, tmp_path, containers=[])
    deploy_build(framework, Manager(), Endpoint("192.0.2.5", 22), "radxa", source=str(model), profile="Rockchip",
                 docker=True, containers=[], output_dir=tmp_path, via="gui", actor="gui")
    deploy_build(framework, Manager(), Endpoint("192.0.2.5", 22), "radxa", source=str(model), profile="Rockchip",
                 docker=True, output_dir=tmp_path, via="cli", actor="cli")
    assert seen[0] == seen[1] == {"use_docker": False}
    assert seen[2]["use_docker"] is True and "containers" not in seen[2]


# --- the Deployment tab (each test takes qapp first: skipped without PySide6) -------------------------------------

@pytest.fixture
def window_framework(make_framework, tmp_path):
    from test_input_history import framework as make_window_framework
    return make_window_framework.__wrapped__(make_framework, tmp_path)


@pytest.fixture
def told(monkeypatch):
    from orchestrator.gui import plain
    shown = []
    monkeypatch.setattr(plain, "_exec", lambda box: shown.append(box.text()) or plain.QMessageBox.No)
    return shown


def a_build(folder: Path) -> Path:
    from test_deploy_parity import a_build as make
    return make(folder)


@pytest.fixture
def tab(qapp, window_framework, monkeypatch):
    from test_deploy_parity import TestDeploymentTab
    return TestDeploymentTab()


def rows(win):
    from PySide6.QtCore import Qt
    table = win.table_docker
    return [(table.item(r, 0).text(), table.item(r, 0).checkState() == Qt.Checked, table.item(r, 1).text(),
             table.item(r, 2).text()) for r in range(table.rowCount())]


def test_the_table_lists_the_containers_the_cli_packs(tab, window_framework, monkeypatch):
    from orchestrator.Core.deployment_manager import package_containers
    window_framework.config.set("image_inference_runtime", INFERENCE_PINNED)
    win = tab.window(window_framework, monkeypatch)
    win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
    (container,) = package_containers(window_framework, "Rockchip")
    assert rows(win) == [("Inference engine", True, INFERENCE_PINNED,
                          " ".join(container.flags) or "none (it runs on the CPU)")]
    assert win.table_docker.objectName() == "table_deploy_containers"
    assert "Llama.cpp (CPU)" not in str(rows(win)) and "TensorRT" not in str(rows(win)), "no mock-up options"


def test_the_table_follows_the_profile_and_keeps_an_untick(tab, window_framework, monkeypatch):
    from PySide6.QtCore import Qt
    win = tab.window(window_framework, monkeypatch)
    flags = {"Rockchip": ["--device /dev/rknpu", "--device /dev/rga"]}
    monkeypatch.setattr(window_framework.target_manager, "get_docker_flags_for_profile",
                        lambda profile: flags.get(profile, []))
    win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
    win._fill_containers()
    assert rows(win)[0][3] == "--device /dev/rknpu --device /dev/rga"
    win.table_docker.item(0, 0).setCheckState(Qt.Unchecked)
    win.cb_profiles.addItem("Other")
    win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Other"))
    assert rows(win)[0][1:] == (False, rows(win)[0][2], "none (it runs on the CPU)"), "the untick stays"


def package_window(tab, window_framework, monkeypatch, tmp_path):
    from orchestrator.gui import artifact_save, deployment_window
    win = tab.window(window_framework, monkeypatch)
    calls = []

    class Manager:
        def create_package(self, artifact_path, profile_name, docker_config, output_dir, **extra):
            calls.append(dict(docker_config))
            path = Path(output_dir) / "deploy_smol_1.zip"
            path.write_bytes(b"PK")
            return path

    real = window_framework.get_component
    monkeypatch.setattr(window_framework, "get_component",
                        lambda name: Manager() if name == "deployment_manager" else real(name))
    monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: start)
    monkeypatch.setattr(deployment_window.PackageWorker, "start", lambda self: self.run())
    folder = tmp_path / "builds"
    tab.select_build(win, a_build(folder / str(len(list(folder.iterdir())) if folder.is_dir() else 0)))
    win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
    return win, calls


def test_the_ticked_containers_are_what_the_cli_packs(tab, window_framework, monkeypatch, tmp_path, told):
    from orchestrator.Core.deployment_manager import package_docker_config
    win, calls = package_window(tab, window_framework, monkeypatch, tmp_path)
    win.grp_docker.setChecked(True)
    win._on_package()
    assert calls == [package_docker_config(True)], "every container ticked: `deploy package --docker`"
    win.grp_docker.setChecked(False)
    win._on_package()
    assert calls[-1] == package_docker_config(False)


def test_no_container_ticked_packs_nothing_and_says_why(tab, window_framework, monkeypatch, tmp_path, told):
    from PySide6.QtCore import Qt

    from orchestrator.gui import deployment_window
    win, calls = package_window(tab, window_framework, monkeypatch, tmp_path)
    win.grp_docker.setChecked(True)
    win.table_docker.item(0, 0).setCheckState(Qt.Unchecked)
    win._on_package()
    assert calls == [] and "No container is ticked" in told[-1]
    started = []
    monkeypatch.setattr(deployment_window.DeploymentWorker, "start", lambda self: started.append(self))
    win.cb_device.setEditText("myboard.local")
    win.txt_user.setText("radxa")
    win._on_deploy()
    assert started == [] and win.reads == [], "Deploy to Target asks nothing and contacts no board"


def test_deploy_to_target_hands_the_choice_to_the_one_deploy_step(tab, window_framework, monkeypatch, tmp_path,
                                                                  told):
    from orchestrator.gui import deployment_window
    from orchestrator.runtime.device import person_steps
    win = tab.window(window_framework, monkeypatch)
    tab.select_build(win, a_build(tmp_path / "b"))
    win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
    win.cb_device.setEditText("myboard.local")
    win.txt_user.setText("radxa")
    win.grp_docker.setChecked(True)
    started = []
    monkeypatch.setattr(deployment_window.DeploymentWorker, "start", lambda self: started.append(self))
    monkeypatch.setattr(deployment_window.QInputDialog, "getText", lambda *a: ("", True))
    win._on_deploy()
    (worker,) = started
    assert worker.docker_config == {"use_docker": True, "containers": None}
    seen = []
    monkeypatch.setattr(person_steps, "deploy_build", lambda *a, **k: seen.append(k) or person_steps.BuildDeploy(
        package_error=RuntimeError("stop here")))
    monkeypatch.setattr(window_framework, "get_component", lambda name: object())
    worker.run()
    assert seen and seen[0]["docker"] is True and seen[0]["containers"] is None


def test_the_cli_and_the_tab_hand_the_same_docker_part_to_the_package(cli_and_tab):
    """Parity: `llmcf deploy package --docker` and the tab with every container ticked make the same package call;
    --no-docker and the group switched off too."""
    cli, gui = cli_and_tab
    assert cli(True) == gui(True) and cli(False) == gui(False)
    assert cli(True) != cli(False)


@pytest.fixture
def cli_and_tab(tab, window_framework, monkeypatch, tmp_path, told):
    from test_review_capabilities_paths import make_cli
    build = a_build(tmp_path / "cli")

    def cli(docker: bool):
        runner = make_cli(window_framework, tmp_path, monkeypatch)
        result = runner.invoke("deploy", "package", str(build), "--profile", "Rockchip",
                               "--docker" if docker else "--no-docker")
        assert result.code == 0, result.out
        return runner.packaged[-1]["docker_config"]

    def gui(docker: bool):
        win, calls = package_window(tab, window_framework, monkeypatch, tmp_path)
        win.grp_docker.setChecked(docker)
        win._on_package()
        return calls[-1]

    return cli, gui


# --- a target.yml whose hardware block is empty (review cs-cli-gui-v) ---------------------------------------------

EMPTY_HARDWARE_TARGET = """\
metadata:
  id: Mine
  name: My board
  architecture: aarch64
hardware:
  # npu:
  #   vendor: rockchip
"""


@pytest.fixture
def empty_hardware(make_framework, tmp_path):
    """A target whose `hardware:` block has nothing in it (its lines commented out: YAML reads null), next to one
    whose npu and gpu blocks are empty."""
    targets = tmp_path / "targets_empty_hw"
    (targets / "Mine").mkdir(parents=True)
    (targets / "Mine" / "target.yml").write_text(EMPTY_HARDWARE_TARGET, encoding="utf-8")
    (targets / "Other").mkdir()
    (targets / "Other" / "target.yml").write_text("metadata:\n  id: Other\nhardware:\n  npu:\n  gpu:\n    vendor:\n",
                                                  encoding="utf-8")
    fm = make_framework(initialize=False)
    fm.config.set("targets_dir", str(targets))
    assert fm.initialize()
    return fm


@pytest.mark.parametrize("hardware, flags", [
    ({"gpu": {"vendor": "NVIDIA"}}, ["--gpus all"]),
    ({"gpu": {"vendor": "intel"}, "npu": {"vendor": "Rockchip"}},
     ["--device /dev/dri", "--device /dev/rknpu", "--device /dev/rga"]),
    ({"npu": {"vendor": "hailo"}}, ["--device /dev/hailo0"]),
    ({"npu": {"vendor": "axelera"}}, ["--privileged"]),
    ({"cpu": {"architecture": "aarch64"}}, []),
    ({}, []),
])
def test_the_device_access_of_a_hardware_block_stays(hardware, flags):
    """Characterization (the flags a filled hardware block gives, as before the empty-block fix)."""
    from types import SimpleNamespace

    from orchestrator.Core.target_manager import TargetManager
    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir="targets"), resolve_path=Path))
    tm._targets, tm._initialized = {"T": {"metadata": {"id": "T"}, "hardware": hardware}}, True
    assert tm.get_docker_flags_for_profile("T") == flags
    assert tm.get_docker_flags_for_profile("unknown") == []


def test_an_empty_hardware_block_gives_no_device_access(empty_hardware):
    """A target.yml whose hardware block (or its npu/gpu block) is empty gives the container no device access, as no
    block at all does: the container table, `deploy package --docker` and the AI's package facts all read it."""
    from orchestrator.Core.deployment_manager import package_containers
    assert empty_hardware.target_manager.get_docker_flags_for_profile("Mine") == []
    assert empty_hardware.target_manager.get_docker_flags_for_profile("Other") == []
    (container,) = package_containers(empty_hardware, "Mine")
    assert container.flags == ()


def test_the_deployment_tab_opens_with_an_empty_hardware_block(qapp, empty_hardware, monkeypatch):
    """The container table is filled when the tab is built: a person's target.yml with an empty hardware block must
    not stop the Deployment tab from opening."""
    from test_deploy_parity import TestDeploymentTab
    win = TestDeploymentTab().window(empty_hardware, monkeypatch)
    win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Mine"))
    assert rows(win)[0][1:] == (True, rows(win)[0][2], "none (it runs on the CPU)")


# --- the probe import's target suggestion reads the block the same way (stage C wave 3c, fix b) -------------------

def matching(targets: dict, probe: dict):
    from types import SimpleNamespace

    from orchestrator.Core.target_manager import TargetManager
    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir="targets"), resolve_path=Path))
    tm._targets, tm._initialized = targets, True
    return tm.find_matching_target(probe)


FILLED = {"Rk": {"hardware": {"cpu": {"architecture": "AArch64"}, "npu": {"vendor_id": "0x1E60"}}},
          "Nv": {"hardware": {"cpu": {"architecture": "x86_64"}, "gpu": {"vendor_id": "0x10de"}}},
          "Any": {"hardware": {"gpu": {"vendor_id": "0x8086"}}}}


@pytest.mark.parametrize("probe, found", [
    ({"ARCH": "aarch64", "NPU_VENDOR_ID": "0x1e60"}, "Rk"),
    ({"ARCH": "x86_64", "GPU_VENDOR_ID": "0x10DE"}, "Nv"),
    ({"ARCH": "aarch64", "GPU_VENDOR_ID": "0x10de"}, None),        # the architecture does not fit
    ({"ARCH": "riscv64", "GPU_VENDOR_ID": "0x8086"}, "Any"),       # a target without an architecture fits any
    ({"ARCH": "x86_64"}, None),
    ({}, None),
])
def test_the_target_suggestion_of_a_filled_hardware_block_stays(probe, found):
    """Characterization (the probe import's suggestion, as before the empty-block fix): the architecture first, then
    the NPU's and the GPU's vendor id."""
    assert matching(FILLED, probe) == found


@pytest.mark.parametrize("hardware", [None, "", [], {"cpu": None, "npu": None, "gpu": None},
                                      {"cpu": {"architecture": None}, "npu": {"vendor_id": None}},
                                      {"npu": "hailo"}])
def test_an_empty_hardware_block_suggests_nothing_and_stops_nothing(hardware):
    """The Builder tab's probe import asks find_matching_target for a target: an empty `hardware:`, `cpu:`, `npu:` or
    `gpu:` block (YAML null, e.g. its lines commented out) or one that is no mapping is no block, as the container
    flags read it (target_manager._block), and the other targets are still looked at."""
    targets = {"Mine": {"hardware": hardware}, **FILLED}
    assert matching(targets, {"ARCH": "aarch64", "NPU_VENDOR_ID": "0x1e60"}) == "Rk"
    assert matching({"Mine": {"hardware": hardware}}, {"ARCH": "aarch64", "GPU_VENDOR_ID": "0x10de"}) is None


def test_the_probe_import_suggests_a_target_next_to_an_empty_hardware_block(empty_hardware):
    """The real targets of a target folder (one with its hardware lines commented out, one with empty npu and gpu
    blocks): the suggestion runs through and finds nothing to match."""
    assert empty_hardware.target_manager.find_matching_target({"ARCH": "aarch64", "NPU_VENDOR_ID": "0x1e60"}) is None


# --- vendor and device ids: an integer and hex text are one id (stage C wave 3c-2, from the cs-c4-v review) -------

@pytest.mark.parametrize("written", ["0x1e60", "0x1E60", " 0x1e60 ", 0x1E60, 7776])
def test_an_unquoted_vendor_id_is_the_same_id_as_its_hex_text(written):
    """YAML reads an unquoted `vendor_id: 0x1e60` as the integer 7776; before, str(7776) matched no probe and the
    probe import suggested no target. An integer and hex text in any letter case are one id."""
    import yaml

    from orchestrator.Core.target_manager import hardware_id
    unquoted = yaml.safe_load("npu:\n  vendor_id: 0x1e60\n")["npu"]["vendor_id"]
    assert unquoted == 7776
    assert hardware_id(written) == hardware_id(unquoted) == "0x1e60"
    targets = {"Rk": {"hardware": {"cpu": {"architecture": "aarch64"}, "npu": {"vendor_id": written}}}}
    for probe_value in ("0x1e60", "0x1E60"):
        assert matching(targets, {"ARCH": "aarch64", "NPU_VENDOR_ID": probe_value}) == "Rk"


def test_an_unquoted_gpu_vendor_id_of_a_target_yml_suggests_its_target(tmp_path):
    """The real reader: a target.yml with `vendor_id: 0x10de` unquoted (YAML: 4318) is found for an NVIDIA probe."""
    from types import SimpleNamespace

    from orchestrator.Core.target_manager import TargetManager
    folder = tmp_path / "targets" / "Nv"
    folder.mkdir(parents=True)
    (folder / "target.yml").write_text("metadata:\n  name: Nv\nhardware:\n  cpu:\n    architecture: x86_64\n"
                                       "  gpu:\n    vendor_id: 0x10de\n", encoding="utf-8")
    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(tmp_path / "targets")),
                                       resolve_path=Path))
    tm.initialize()
    assert tm.find_matching_target({"ARCH": "x86_64", "GPU_VENDOR_ID": "0x10DE"}) == "Nv"


@pytest.mark.parametrize("value, expected", [(None, ""), (True, ""), (0, ""), ("", ""), ("rockchip", "rockchip"),
                                             ("0xZZ", "0xzz"), (0x2207, "0x2207"), ("0x02207", "0x2207")])
def test_hardware_id_spellings(value, expected):
    from orchestrator.Core.target_manager import hardware_id
    assert hardware_id(value) == expected
