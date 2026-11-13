"""The module wizard checked from start to end (DESIGN, targets; built in the build image 2026-11-12): the wizard
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
        # the edit step could not make the files from that shell code: it says why, and nothing was saved
        assert wizard.currentId() == wizard.edit_id and wizard.edit_panel.edit is None
        assert "syntax error" in wizard.edit_panel.problems_view.toPlainText()
        wizard.back()
        wizard.quant_logic.setPlainText("")
        wizard.next()
        assert wizard.edit_panel.edit is not None and not wizard.edit_panel.edit.changed()
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


# --- the probe proposes the name and the SDK, never flags; the CLI matches the wizard --------------------------------

def test_the_wizard_names_the_module_after_the_probes_board_and_keeps_it_unique(fw, qapp, monkeypatch):
    """Before: every probe without an accelerator gave "Generic_Target". Now the probe's board name, made unique."""
    from orchestrator.gui import wizards
    message_boxes(monkeypatch, wizards)
    taken = Path(fw.target_manager.targets_dir) / "raspberry_pi_5_model_b_rev_1_0"
    taken.mkdir()
    wizard = wizards.ModuleCreationWizard(Path(fw.target_manager.targets_dir))
    try:
        wizard.framework_manager = fw
        wizard.import_probe_file(str(PROBE))
        assert wizard.name_edit.text() == "Raspberry_Pi_5_Model_B_Rev_1.0_2"
        assert wizard.sdk_edit.text() == "None"
    finally:
        wizard.deleteLater()


def test_the_wizard_proposes_no_flags_from_the_probe(fw, qapp, monkeypatch):
    """Before: -DGGML_NEON=ON (no llama.cpp option) from SUPPORTS_NEON and flags from the schema-1 key SUPPORTS_FP16.
    Now none: the build recipe plans the flags from the board's probe in the config stage."""
    from orchestrator.gui import wizards
    message_boxes(monkeypatch, wizards)
    wizard = wizards.ModuleCreationWizard(Path(fw.target_manager.targets_dir))
    try:
        wizard.framework_manager = fw
        wizard.cpu_flags.setText("-mcpu=old")
        wizard.import_probe_file(str(PROBE))
        assert wizard.cpu_flags.text() == "" and wizard.cmake_flags.text() == ""
    finally:
        wizard.deleteLater()


def test_the_sdk_comes_from_the_accelerator_keys_of_the_current_probe_schema():
    from orchestrator.Core.module_generator import probe_module_name, probe_sdk
    assert probe_sdk({"SUPPORTS_CUDA": "ON"}) == "CUDA"
    assert probe_sdk({"SUPPORTS_RKNN": "ON"}) == "RKNN"
    assert probe_sdk({"SUPPORTS_RKNN": "ON", "RKLLM_RUNTIME_VERSION": "1.2.1"}) == "RKLLM / RKNN"
    assert probe_sdk({"SUPPORTS_HAILO": "ON"}) == "Hailo"
    assert probe_sdk({"SUPPORTS_INTEL_NPU": "ON"}) == "OpenVINO"
    assert probe_sdk({"SUPPORTS_NEON": "ON"}) == "None" and probe_sdk(None) == "None"
    assert probe_module_name({"SOC_MODEL": "rk3588"}) == "rk3588"
    assert probe_module_name({"CPU_MODEL": "Cortex-A76"}) == "Cortex-A76"
    assert probe_module_name({}) == "Custom_Target"


def test_the_wizard_adds_no_cross_compiler_packages(fw, qapp, monkeypatch):
    """The board package's compilers come from the template's runtime stage, not from packages the wizard adds."""
    from orchestrator.gui import wizards
    boxes = message_boxes(monkeypatch, wizards)
    wizard = wizards.ModuleCreationWizard(Path(fw.target_manager.targets_dir))
    try:
        wizard.framework_manager = fw
        wizard.import_probe_file(str(PROBE))
        while wizard.nextId() != -1:
            wizard.next()
        assert "Auto-Added" not in wizard.summary_text.toPlainText()
        wizard.accept()
    finally:
        wizard.deleteLater()
    assert not any("gcc-aarch64-linux-gnu" in p for p in wizard.module_data["packages"])
    check_made(fw, created_target(boxes), trust="own", provenance=None)


def test_module_create_stores_the_probe_as_the_wizard_does(fw, qapp, monkeypatch):
    """Before: `module create --probe` read the probe but never stored it; the wizard stored it. Now both store it
    through the same import (runtime/scan_steps.import_probe), the CLI prints its probe id, and it proposes the same
    name and SDK as the wizard."""
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from orchestrator.runtime.probe_service import HardwareProbes
    from test_cli_builds import write_user_config
    write_user_config(Path(fw.config.config_dir).parent, targets_dir=str(fw.target_manager.targets_dir))
    probes = HardwareProbes(state(fw))
    before = {p.probe_id for p in probes.list()}
    result = CliRunner().invoke(cli_module.cli, ["-q", "module", "create", "--probe", str(PROBE)],
                                input="\n\n\n\n")
    assert result.exit_code == 0, result.output
    stored = {p.probe_id for p in probes.list()} - before
    assert len(stored) == 1
    (cli_id,) = stored
    assert f"Imported as probe {cli_id}" in " ".join(result.output.split())
    from orchestrator.gui import wizards
    message_boxes(monkeypatch, wizards)
    wizard = wizards.ModuleCreationWizard(Path(fw.target_manager.targets_dir))
    try:
        wizard.framework_manager = fw
        gui_id = wizard._store_probe(str(PROBE))
    finally:
        wizard.deleteLater()
    # an import gets a new id each time; the two imports store the same profile through the same import path
    assert gui_id and gui_id != cli_id
    assert probes.get(gui_id).values == probes.get(cli_id).values
    tid = "raspberry_pi_5_model_b_rev_1_0"
    assert (Path(fw.target_manager.targets_dir) / tid).is_dir()
    check_made(fw, tid, trust="own", provenance=None)


def test_module_create_has_an_option_for_every_value_of_the_wizard(fw, qapp, monkeypatch, tmp_path):
    """Parity: the same values typed into the wizard and given as options of `llm-cli module create` reach the module
    generator as the same module definition."""
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from orchestrator.gui import wizards
    seen = []

    class Recorder:
        def __init__(self, targets_dir, *a, **k):
            self.targets_dir = Path(targets_dir)

        def generate_module(self, data, framework_manager=None, knowledge_roots=None, profile=None):
            seen.append((dict(data), sorted(knowledge_roots or [])))
            folder = self.targets_dir / "recorded"
            folder.mkdir(exist_ok=True)
            return folder

    values = dict(name="Parity Board", arch="aarch64", sdk="RKNN", description="My board",
                  base="debian:bookworm-slim", packages="cmake git", cpu="-mcpu=cortex-a55",
                  cmake="-DGGML_CUDA=OFF", setup="export A=1\n", quant="    Q4_0) echo q ;;\n",
                  root="https://docs.example.org/board", formats="- gguf\n- rknn\n")
    setup, quant, formats = tmp_path / "setup.sh", tmp_path / "quant.sh", tmp_path / "formats.yml"
    setup.write_text(values["setup"])
    quant.write_text(values["quant"])
    formats.write_text(values["formats"])

    monkeypatch.setattr(wizards, "ModuleGenerator", Recorder)
    message_boxes(monkeypatch, wizards)
    wizard = wizards.ModuleCreationWizard(Path(fw.target_manager.targets_dir))
    try:
        wizard.framework_manager = fw
        monkeypatch.setattr(wizard, "_check_created", lambda folder: "")
        wizard.name_edit.setText(values["name"])
        wizard.arch_combo.setCurrentText(values["arch"])
        wizard.sdk_edit.setText(values["sdk"])
        wizard.desc_edit.setText(values["description"])
        wizard.rad_debian.setChecked(True)
        wizard.packages_edit.setText(values["packages"])
        wizard.cpu_flags.setText(values["cpu"])
        wizard.cmake_flags.setText(values["cmake"])
        wizard.setup_commands.setPlainText(values["setup"])
        wizard.quant_logic.setPlainText(values["quant"])
        wizard.formats_edit.setPlainText(values["formats"])   # the general template's variables
        wizard.crawled_roots.add(values["root"])
        wizard.chk_knowledge.setEnabled(True)
        wizard.chk_knowledge.setChecked(True)
        wizard.accept()
    finally:
        wizard.deleteLater()

    monkeypatch.setattr(cli_module, "ModuleGenerator", Recorder)
    from test_cli_builds import write_user_config
    write_user_config(Path(fw.config.config_dir).parent, targets_dir=str(fw.target_manager.targets_dir))
    result = CliRunner().invoke(cli_module.cli, [
        "-q", "module", "create", "--name", values["name"], "--arch", values["arch"], "--sdk", values["sdk"],
        "--description", values["description"], "--base-image", values["base"], "--packages", values["packages"],
        "--cpu-flags", values["cpu"], "--cmake-flags", values["cmake"], "--setup-commands", str(setup),
        "--quantization-logic", str(quant), "--knowledge-root", values["root"], "--formats", str(formats)], input="")
    assert result.exit_code == 0, result.output
    assert len(seen) == 2
    (gui, gui_roots), (cli, cli_roots) = seen
    assert gui_roots == cli_roots == [values["root"]]
    assert gui == cli and gui["formats"] == values["formats"]
