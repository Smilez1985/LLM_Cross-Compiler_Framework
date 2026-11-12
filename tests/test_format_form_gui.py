"""The form per value in the GUI (orchestrator/gui/format_form.py through the real module wizard and the edit step) and
the guided fetch of vendor files (orchestrator/gui/vendor_files_window.py): the formats are a form generated from the
schema, each reason under its field, the YAML kept as the advanced view with one value both ways; Ditto's proposal
fills the form with each value's source and confidence beside its field and its gaps empty; the licence is accepted
only by the person's own tick and click, and the file is kept only when its sha256 matches."""

import hashlib
import threading
import time
from pathlib import Path

import pytest
import yaml

from orchestrator.Core.format_proposal import FormatValuesAnswer, build_proposal
from test_general_template import generate, npu_format
from test_gui_build import MODULE, wizard  # noqa: F401
from test_vendor_files import LICENCE, SDK, SDK_SHA, fetch_text, vendor_module

DOC = "https://vendor.example.com/docs/compiler"
PROBE = "ARCH=arm64\nSUPPORTS_HAILO=ON\nHAILO_RUNTIME_VERSION=4.20.0\n"


def answer_data():
    def v(path, value, source):
        return {"path": path, "value": value, "reason": "read in the documentation", "source": source,
                "confidence": 0.7}
    return {"formats": [{"id": "hef", "values": [
        v("label", "HEF (Hailo NPU)", "template"), v("converter.command", "/opt/hailo/bin/hailo", "S1"),
        v("converter.args", ["compile", "{input}", "--output", "{output}"], "S1"), v("input", ["onnx"], "S1"),
        v("output_extension", "hef", "template"), v("quantizations", ["int8"], "S1"),
        v("default_quantization", "int8", "S1"), v("notes", "guessed", "")]}], "summary": "HEF"}


def proposal():
    return build_proposal(FormatValuesAnswer.model_validate(answer_data()), [DOC], PROBE)


class StandInDitto:
    """Ditto's provider layer replaced by a stand-in model: the real format_proposal.propose runs against it (the
    knowledge base gives one excerpt of DOC, the model answers answer_data())."""
    logger = None

    def __init__(self):
        self.asked = []

    def _rag_manager(self):
        from types import SimpleNamespace
        return SimpleNamespace(search=lambda query, limit=5, rerank=None: [
            SimpleNamespace(source=DOC, content="hailo compile model.onnx --output model.hef")])

    def _folder_labels(self):
        return None

    def _structured(self, task, system, prompt, output_model):
        self.asked.append((threading.current_thread() is threading.main_thread(), prompt))
        return output_model.model_validate(answer_data())


def tab_of(form, index=0):
    return form.tabs.widget(index)


# --- the wizard's form ---------------------------------------------------------------------------------------------

def test_the_wizard_shows_a_form_per_value_generated_from_the_schema(wizard):  # noqa: F811
    form = wizard.formats_form
    assert form.tabs.count() == 0 and "builds GGUF" in form.summary.text()
    form.add_format("")
    tab = tab_of(form)
    assert {"converter.command", "converter.args", "input", "runtime_libraries", "vendor_files",
            "sdk.toolkit"} <= set(tab.rows)
    assert tab.rows["converter.command"].toolTip() and not tab.rows["label"].error.isHidden()
    tab.rows["id"].input.setText("hef")
    tab.rows["label"].input.setText("HEF")
    tab.rows["converter.command"].input.setText("/opt/hailo/bin/hailo")
    tab.rows["converter.args"].input.setText("{input} --output {output} $(id)")
    tab.rows["input"].ticks["onnx"].setChecked(True)
    tab.rows["output_extension"].input.setText("hef")
    tab.rows["quantizations"].input.setText("int8 int16")
    tab.rows["default_quantization"].input.setCurrentText("int8")
    assert "one plain word" in tab.rows["converter.args"].error.text(), "the reason stands under its field"
    assert tab.rows["label"].error.isHidden()
    tab.rows["converter.args"].input.setText("{input} --output {output}")
    assert form.problems() == {} and form.tabs.tabText(0) == "hef"
    written = yaml.safe_load(wizard.formats_edit.toPlainText())
    assert written[0]["converter"]["args"] == ["{input}", "--output", "{output}"]
    # the YAML view is the same value both ways
    form.chk_yaml.setChecked(True)
    assert form.stack.currentIndex() == 1
    wizard.formats_edit.setPlainText(wizard.formats_edit.toPlainText().replace("HEF", "HEF changed"))
    form.chk_yaml.setChecked(False)
    assert tab_of(form).rows["label"].value() == "HEF changed"
    form.chk_yaml.setChecked(True)
    wizard.formats_edit.setPlainText("- id: [")
    form.chk_yaml.setChecked(False)
    assert form.chk_yaml.isChecked() and not form.yaml_error.isHidden(), "unreadable YAML stays in its view"
    wizard.formats_edit.setPlainText("- rknn\n")
    form.chk_yaml.setChecked(False)
    assert form.tabs.count() == 1 and tab_of(form).rows["converter.command"].value() == "modules/rknn_module.sh"
    wizard.accept()
    assert "rknn" in wizard.generated[-1][0]["formats"]


def test_an_ai_fill_of_the_yaml_fills_the_form_and_the_persons_edit_is_theirs(wizard):  # noqa: F811
    outcome = wizard.gui_form.fill({"formats": yaml.safe_dump([npu_format()])})
    assert outcome.filled == ["formats"]
    tab = tab_of(wizard.formats_form)
    assert tab.rows["converter.command"].value() == "/opt/vendor/fakenpu/bin/fakec"
    assert tab.rows["runtime_libraries"].table.rowCount() == 2
    assert wizard.gui_form.ai_touched()
    tab.rows["label"].input.setText("Mine")
    assert wizard.gui_form.field("formats").person_set, "a change in the form is the person's"


def test_dittos_ai_analysis_fills_the_form_with_sources_and_gaps(wizard, tmp_path):  # noqa: F811
    from orchestrator.gui.wizards import FORMAT_PROPOSAL_KEY
    wizard.on_ai_finished({**MODULE, FORMAT_PROPOSAL_KEY: proposal()})
    tab = tab_of(wizard.formats_form)
    note = tab.rows["converter.command"].note.text()
    assert "/opt/hailo/bin/hailo" in note and DOC in note and "0.7" in note
    assert "never guessed" in tab.rows["notes"].note.text() and tab.rows["notes"].value() is None
    assert "the template" in tab.rows["label"].note.text()
    from test_gui_build import WizardBox
    assert any("Format hef:" in text for _kind, _title, text in WizardBox.shown)
    wizard.accept()
    data = wizard.generated[-1][0]
    assert data["provenance"] == "ai-generated" and "/opt/hailo/bin/hailo" in data["formats"]


def test_the_ai_import_worker_proposes_the_formats_when_the_probe_names_an_accelerator(tmp_path):
    from orchestrator.Core.ditto_manager import ModuleDefinition
    from orchestrator.gui.wizards import FORMAT_PROPOSAL_KEY, ModuleCreationWizard
    probe = tmp_path / "probe.txt"
    probe.write_text(PROBE)
    plain = tmp_path / "plain.txt"
    plain.write_text("ARCH=arm64\n")

    class Ditto(StandInDitto):
        offline_mode = True

        def generate_module(self, path):
            return ModuleDefinition(**{k: v for k, v in MODULE.items() if k != "base_os"},
                                    base_os="debian:bookworm-slim")
    ditto = Ditto()

    class Relay:
        def __init__(self):
            self.results = []
            self.done = self

        def emit(self, token, result):
            self.results.append(result)
    relay = Relay()
    import orchestrator.Core.ditto_manager as dm
    real = dm.pin_base_image
    dm.pin_base_image = lambda definition, **kw: definition
    try:
        ModuleCreationWizard._ai_worker(ditto, probe, "t", relay, True)
        ModuleCreationWizard._ai_worker(ditto, plain, "t", relay, True)
    finally:
        dm.pin_base_image = real
    (kind, value), (kind2, value2) = relay.results
    assert kind == "done" and value[FORMAT_PROPOSAL_KEY].drafts[0].id == "hef"
    assert kind2 == "done" and FORMAT_PROPOSAL_KEY not in value2 and len(ditto.asked) == 1
    assert "SUPPORTS_HAILO=ON" in ditto.asked[0][1] and DOC in ditto.asked[0][1]


def test_the_propose_button_asks_ditto_off_the_gui_thread(wizard, qapp, tmp_path):  # noqa: F811
    probe = tmp_path / "probe.txt"
    probe.write_text(PROBE)

    class Ditto(StandInDitto):
        def is_configured(self, task):
            return True
    ditto = Ditto()
    wizard._ditto = lambda: ditto
    assert wizard.propose_formats() is False, "no probe imported yet"
    wizard.probe_path = str(probe)
    assert wizard.propose_formats() is True
    deadline = time.monotonic() + 10
    while wizard.formats_form.tabs.count() == 0 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert [main for main, _prompt in ditto.asked] == [False] and wizard.format_proposal is not None
    assert DOC in tab_of(wizard.formats_form).rows["converter.command"].note.text()
    assert wizard.btn_formats_propose.isEnabled()


# --- the edit step's form ------------------------------------------------------------------------------------------

def test_the_edit_step_shows_the_variables_as_a_form(qapp, tmp_path):
    from orchestrator.Core.module_edit import ModuleEdit
    from orchestrator.gui.module_edit import ModuleEditPanel
    target = generate(tmp_path, formats=[npu_format()])
    panel = ModuleEditPanel()
    try:
        panel.load(ModuleEdit(target))
        form = panel.variables_form
        assert form.tabs.count() == 1
        tab = tab_of(form)
        assert tab.rows["output_extension"].value() == "fnpu"
        tab.rows["converter.args"].input.setText("--model {input} --output {output} --dtype {quant} --fast")
        assert "--fast" in panel.variables_edit.toPlainText()
        assert panel.form.field("variables").person_set
        assert panel.apply_variables() == ["modules/config_module.sh", "target.yml"]
        assert "'--fast'" in panel.editors["modules/config_module.sh"].toPlainText()
        tab.rows["quantizations"].input.setText("")
        assert tab.rows["quantizations"].error.text() == "Required."
    finally:
        panel.unload()
        panel.deleteLater()


# --- the guided fetch in the GUI -----------------------------------------------------------------------------------

def test_targets_window_has_vendor_files():
    from orchestrator.utils.next_steps import catalogue
    assert catalogue().steps["build.vendor_fetch"].target == "btn_targets_vendor"


def test_the_licence_needs_the_persons_tick_and_the_file_its_sha256(qapp, tmp_path, monkeypatch):
    from orchestrator.Core import vendor_files as vf
    from orchestrator.gui import vendor_files_window as win
    (tmp_path / "m").mkdir()
    module = vendor_module(tmp_path / "m")
    state = tmp_path / "state-gui"
    dialog = win.VendorFilesDialog(module, state, fetch_text=fetch_text, open_url=lambda url: opened.append(url))
    opened = []
    try:
        assert dialog.table.rowCount() == 2 and dialog.selected() == "vendor_sdk-1.0-py3-none-any.whl"
        assert not dialog.btn_add.isEnabled()
        seen = {}

        def declined(self):
            seen["accept_enabled"] = self.btn_accept.isEnabled()
            seen["text"] = self.text.toPlainText()
            return 0
        monkeypatch.setattr(win.LicenceDialog, "exec", declined)
        assert dialog.fetch() is False and vf.acceptances(state) == {}
        assert seen == {"accept_enabled": False, "text": LICENCE}

        def ticked(self):
            self.tick.setChecked(True)
            assert self.btn_accept.isEnabled()
            self.btn_accept.click()
            return self.result()
        monkeypatch.setattr(win.LicenceDialog, "exec", ticked)
        assert dialog.fetch() is True
        record = next(iter(vf.acceptances(state).values()))
        assert record["channel"] == "gui" and record["licence_sha256"] == hashlib.sha256(LICENCE.encode()).hexdigest()
        assert dialog.btn_add.isEnabled() and not dialog.btn_download.isEnabled()
        dialog.open_page()
        assert opened == ["https://vendor.example.com/downloads"]
        boxes = []
        monkeypatch.setattr(win, "plain_box", lambda *a, **k: boxes.append(a[3]))
        bad = tmp_path / "bad.whl"
        bad.write_bytes(b"other")
        assert dialog.add_file(str(bad)) is False and "not the" in boxes[-1]
        good = tmp_path / "good.whl"
        good.write_bytes(SDK)
        assert dialog.add_file(str(good)) is True and SDK_SHA in dialog.result.text()
        assert dialog.table.item(0, 5).text() == "yes"
    finally:
        dialog.deleteLater()


def test_the_targets_window_opens_vendor_files(qapp, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from orchestrator.gui import targets_window
    opened = []
    monkeypatch.setattr("orchestrator.gui.vendor_files_window.open_vendor_files",
                        lambda parent, folder, state: opened.append((folder, state)))

    class TM:
        targets_dir = tmp_path

        def folder_of(self, name):
            return tmp_path / name

    class Service:
        def list_targets(self):
            return SimpleNamespace(targets=[SimpleNamespace(id="npu", name="NPU", architecture="aarch64",
                                                            boards=[], formats=[], support_level="community",
                                                            build_test=None)])
    framework = SimpleNamespace(target_manager=TM(), build_service=Service(),
                                config=SimpleNamespace(user_state_dir=tmp_path / "st"))
    monkeypatch.setattr(targets_window, "build_service", lambda fw: Service())
    dialog = targets_window.TargetsDialog(framework)
    try:
        assert dialog.btn_vendor.objectName() == "btn_targets_vendor" and dialog.btn_vendor.isEnabled()
        dialog.table.selectRow(0)
        dialog._vendor()
        assert opened == [(tmp_path / "npu", tmp_path / "st")]
    finally:
        dialog.deleteLater()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    from orchestrator.Core import vendor_files as vf
    monkeypatch.setattr(vf, "default_fetch_text", lambda url: (_ for _ in ()).throw(OSError("no network in tests")))
