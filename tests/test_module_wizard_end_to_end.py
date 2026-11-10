"""The module wizard checked from start to end (ROADMAP row "The module wizard checked from start to end"): the wizard
(standard import and Ditto's analysis), `llm-cli module create` and the tool create_target_module each make a target
from a hardware probe; the target passes the check, its board is the probe's SoC so the probe finds it, it is marked
untested, list_targets names its trust, and a build request for it is planned."""
from pathlib import Path

import yaml

from conftest import REPO_ROOT
from test_targets_modules import asking, call, fw, registry_fake, state  # noqa: F401
from orchestrator.Core.module_generator import GNU_TRIPLETS, probe_board, validate_target_folder

PROBE = REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "probe_rpi5_raspios.txt"


def check_made(fm, tid, *, trust, provenance):
    folder = Path(fm.target_manager.targets_dir) / tid
    assert validate_target_folder(folder) == []
    meta = yaml.safe_load((folder / "target.yml").read_text())
    assert meta["metadata"]["untested"] is True
    assert meta["metadata"].get("provenance") == provenance
    assert list(meta["capabilities"]["boards"]) == ["bcm2712"]   # the probe's SoC, so the probe finds the target
    dockerfile = (folder / "Dockerfile").read_text()
    assert "[GNU_TRIPLET]" not in dockerfile and "ARG GNU_TRIPLET=aarch64-linux-gnu" in dockerfile
    fm.target_manager.initialize()
    listed = {t.id: t for t in call(fm, "list_targets", {}).targets}
    assert (listed[tid].trust, listed[tid].untested) == (trust, True)
    assert call(fm, "get_target_capabilities", {"target": tid, "check_files": True}).problems == []
    planned = call(fm, "check_build_request", {"model": "HuggingFaceTB/SmolLM2-135M-Instruct", "target": tid,
                                               "format": "gguf", "quantization": "Q4_K_M",
                                               "include_download_size": False})
    assert planned.normalized.board == "bcm2712"


def message_boxes(monkeypatch, wizards):
    boxes = []

    class Box:
        Critical = Information = Ok = 0

        def __init__(self, *a, **k):
            boxes.append(("box",) + a[1:])

        def setTextFormat(self, *a):
            pass

        def exec(self):
            return 0

        @staticmethod
        def information(*a):
            boxes.append(("info",) + a[1:])

        @staticmethod
        def critical(*a):
            boxes.append(("crit",) + a[1:])

        warning = information
    monkeypatch.setattr(wizards, "QMessageBox", Box)
    return boxes


def created_target(boxes):
    made = [b for b in boxes if b[0] == "info" and "\n" in str(b[-1]) and "targets" in str(b[-1])]
    assert made, boxes
    return Path(str(made[-1][-1]).split("\n")[1]).name


def test_the_cli_makes_a_target_from_a_probe(fw):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from test_cli_builds import write_user_config
    write_user_config(Path(fw.config.config_dir).parent, targets_dir=str(fw.target_manager.targets_dir))
    result = CliRunner().invoke(cli_module.cli, ["-q", "module", "create", "--name", "Pi5 CLI", "--arch", "aarch64",
                                                 "--sdk", "None", "--probe", str(PROBE)], input="\n\n\n\n")
    assert result.exit_code == 0, result.output
    check_made(fw, "pi5_cli", trust="own", provenance=None)


def test_the_wizard_makes_a_target_from_a_probe(fw, qapp, monkeypatch):
    from orchestrator.gui import wizards
    from orchestrator.utils.localization import ui_text
    boxes = message_boxes(monkeypatch, wizards)
    wizard = wizards.ModuleCreationWizard(Path(fw.target_manager.targets_dir))
    try:
        wizard.framework_manager = fw
        wizard.show()
        wizard.import_probe_file(str(PROBE))
        wizard.quant_logic.setPlainText("<b>Q4_K_M</b>) echo q4 ;;")
        while wizard.nextId() != -1:
            wizard.next()
        summary = wizard.summary_text.toPlainText()
        assert summary.startswith(ui_text("bld.wizard.summary", name="", arch="", sdk="", base="", packages="",
                                          cpu="", cmake="", quant="", setup="").split("\n")[0])
        assert "<b>Q4_K_M</b>" in summary   # shell code shown as written, never as rich text
        wizard.quant_logic.setPlainText("")
        wizard.accept()
    finally:
        wizard.deleteLater()
    assert not [b for b in boxes if b[0] == "crit"], boxes
    check_made(fw, created_target(boxes), trust="own", provenance=None)


def test_the_wizard_saves_dittos_proposal(fw, qapp, monkeypatch):
    from orchestrator.Core.ditto_manager import ModuleDefinition
    from orchestrator.gui import wizards
    from orchestrator.gui.worker_relay import worker_relay
    boxes = message_boxes(monkeypatch, wizards)

    class Ditto:
        config = None

        def generate_module(self, path):
            return ModuleDefinition(module_name="Pi5 Ditto", architecture="aarch64", sdk="none",
                                    base_os="debian:bookworm-slim@sha256:" + "1" * 64, packages=["cmake", "git"],
                                    cpu_flags="-mcpu=cortex-a76", cmake_flags="-DGGML_NATIVE=OFF",
                                    setup_commands="export FOO=1",
                                    quantization_logic="    Q4_K_M) echo q4 ;;\n    *) echo other ;;",
                                    assumptions=["8 GB [guessed]"])
    wizard = wizards.ModuleCreationWizard(Path(fw.target_manager.targets_dir))
    try:
        wizard.framework_manager = fw
        wizard.show()
        wizard.probe_path = str(PROBE)
        wizard._store_probe(str(PROBE))
        token = wizard._ai_request = object()
        wizards.ModuleCreationWizard._ai_worker(Ditto(), PROBE, token, worker_relay(), True)
        for _ in range(20):
            qapp.processEvents()
        assert wizard.ai_proposal
        while wizard.nextId() != -1:
            wizard.next()
        wizard.accept()
    finally:
        wizard.deleteLater()
    check_made(fw, created_target(boxes), trust="ai-generated", provenance="ai-generated")


def test_the_tool_makes_a_target_from_a_probe(fw, registry_fake):
    from orchestrator.runtime.probe_service import HardwareProbes
    registry_fake.add_index("library/debian", "bookworm-slim", ["linux/amd64", "linux/arm64"])
    probe_id = HardwareProbes(state(fw)).import_file(PROBE, "Pi 5")
    out = call(fw, "create_target_module", {"module_name": "Pi5 MCP", "architecture": "aarch64",
                                            "base_os": "debian:bookworm-slim", "packages": ["cmake"],
                                            "probe_id": probe_id, "assumptions": ["8 GB [guessed]"]},
               confirm=asking(True))
    check_made(fw, out.target, trust="ai-generated", provenance="ai-generated")


def test_the_probe_board_is_the_soc_and_refuses_what_is_no_board_id():
    found = probe_board({"SOC_MODEL": "BCM2712", "BOARD_MODEL": "Raspberry Pi 5 [Model B]"})
    assert found == {"board": "bcm2712", "info": {"label": "Raspberry Pi 5 Model B (bcm2712)",
                                                  "examples": ["Raspberry Pi 5 Model B"]}}
    assert probe_board({"SOC_MODEL": "bcm2712"})["info"] == {"label": "bcm2712"}
    assert probe_board({"SOC_MODEL": "a soc with spaces"}) is None
    assert probe_board({}) is None and probe_board(None) is None
    assert GNU_TRIPLETS["armv7l"] == "arm-linux-gnueabihf"


def test_a_folder_without_a_pack_is_shipped_only_in_the_frameworks_own_targets(tmp_path):
    from orchestrator.Core.community_manager import SHIPPED_TARGETS_DIR, own_target_trust
    assert own_target_trust(SHIPPED_TARGETS_DIR / "ARM64") == "shipped"
    copy = tmp_path / "ARM64"
    copy.mkdir()
    assert own_target_trust(copy) == "own"
    (copy / "target.yml").write_text("metadata:\n  provenance: ai-generated\n")
    assert own_target_trust(copy) == "ai-generated"
