"""A block proposed from a model, by command and by mouse: `llm-cli bundle block propose --hf|--from-folder|--task`
prints the draft with its reasons and writes it only with --write, at a terminal, after the person's yes ("No" by
default); Boards > Board bundle... > Propose from model... reads the model off the GUI thread, shows the draft with its
reasons and the AI clients' texts in a review dialog, and only the person's Save writes it, checked again, as an own
unsigned block. The Hub is a stand-in: no network. Every name is a test value."""

import json
from types import SimpleNamespace

import pytest

from test_cli_builds import invoke_plain

from orchestrator.runtime.bundle import propose as prop
from orchestrator.runtime.bundle import service

COMMIT = "c" * 40


class Api:
    def __init__(self, info):
        self.info = info

    def model_info(self, repo, **kwargs):
        if repo not in self.info:
            from huggingface_hub.errors import RepositoryNotFoundError
            raise RepositoryNotFoundError("no", response=SimpleNamespace(headers={}, status_code=404, request=None))
        return self.info[repo]


def _hub_info(task="object-detection"):
    return SimpleNamespace(sha=COMMIT, gated=False, card_data={"license": "apache-2.0"}, tags=[], safetensors=None,
                           pipeline_tag=task, library_name="transformers",
                           siblings=[SimpleNamespace(rfilename="model.onnx", size=12_000_000, lfs=None),
                                     SimpleNamespace(rfilename="config.json", size=10, lfs=None)])


@pytest.fixture
def hub_cli(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from orchestrator.Core.hf_hub import HubClient
    settings = {"offline_mode": False}
    fw = SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: settings.get(k, d)),
                         secrets_manager=SimpleNamespace(get_secret=lambda key: None))
    infos = {"example/det-tiny": _hub_info()}
    monkeypatch.setattr(cli_module, "_hub_client",
                        lambda ctx: HubClient(fw, api_factory=lambda token: Api(infos)))
    monkeypatch.setattr(cli_module, "_state_dir", lambda ctx: tmp_path / "state")
    app = tmp_path / "app"
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (app, None))
    return SimpleNamespace(settings=settings, app=app, tmp=tmp_path, infos=infos)


def test_propose_prints_the_draft_with_its_reasons_and_writes_nothing(hub_cli):
    res = invoke_plain("bundle", "block", "propose", "--hf", "example/det-tiny")
    assert res.code == 0, res.out
    for words in ("kind: detect", "protocol: http-multipart", "protocol: http-sse", "Why:",
                  "the model's pipeline task is object-detection", "What the engine needs to know:",
                  "Nothing was written"):
        assert words in res.out, words
    assert not (hub_cli.app / "community").exists()


def test_propose_json_is_labels_with_the_ai_notes(hub_cli):
    prop.ProposalNotes(hub_cli.tmp / "state").add("example/det-tiny", "Raise the timeout to 120 s", "ditto")
    res = invoke_plain("bundle", "block", "propose", "--hf", "example/det-tiny", "--json")
    out = json.loads(res.stdout)
    assert out["block"]["kind"] == "detect" and out["facts"]["commit"] == COMMIT
    assert out["ai_notes"][0]["note"] == "Raise the timeout to 120 s" and str(hub_cli.tmp) not in res.stdout


def test_write_needs_a_terminal_and_the_persons_yes(hub_cli, monkeypatch):
    from orchestrator import cli as cli_module
    res = invoke_plain("bundle", "block", "propose", "--task", "text-to-speech", "--write")
    assert res.code == 2 and "needs a terminal" in res.out
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    res = invoke_plain("bundle", "block", "propose", "--task", "text-to-speech", "--write", input="\n")
    assert res.code == 0 and "Nothing was written." in res.out and not (hub_cli.app / "community").exists()
    res = invoke_plain("bundle", "block", "propose", "--task", "text-to-speech", "--id", "tts-mine", "--write",
                       input="y\n")
    assert res.code == 0 and "Wrote block tts-mine" in res.out, res.out
    folder = hub_cli.app / "community" / "blocks" / "tts-mine"
    from orchestrator.runtime.bundle.blocks import check_block_folder, load_catalog
    assert check_block_folder(folder) == []
    assert load_catalog(hub_cli.app).get("tts-mine").trust == "own"
    out = hub_cli.tmp / "elsewhere"
    res = invoke_plain("bundle", "block", "propose", "--from-folder", str(hub_cli.tmp), "--write", str(out),
                       input="y\n")
    assert res.code == 0 and any(out.iterdir())


def test_the_choices_are_checked_and_offline_mode_refuses(hub_cli):
    assert invoke_plain("bundle", "block", "propose").code == 2
    assert invoke_plain("bundle", "block", "propose", "--task", "x", "--hf", "a/b").code == 2
    assert invoke_plain("bundle", "block", "propose", "--task", "x", "--revision", COMMIT).code == 2
    assert invoke_plain("bundle", "block", "propose", "--task", "x", "--json", "--write").code == 2
    res = invoke_plain("bundle", "block", "propose", "--hf", "example/none")
    assert res.code == 1 and "no such repository" in res.out
    hub_cli.settings["offline_mode"] = True
    res = invoke_plain("bundle", "block", "propose", "--hf", "example/det-tiny")
    assert res.code == 1 and "offline" in res.out.lower()


# --- by mouse --------------------------------------------------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui.bundle_window import BundleWindow
    from test_boards_window import framework
    fw = framework(tmp_path)
    fw.config.get = lambda key, default=None: default
    fw.secrets_manager = SimpleNamespace(get_secret=lambda key: None)
    fw.hf_api_factory = lambda token: Api({"example/det-tiny": _hub_info()})
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (tmp_path / "app", None))
    dialog = BundleWindow(fw, lang="en")
    dialog.reload()
    yield dialog
    dialog.deleteLater()
    qapp.processEvents()


def _answers(monkeypatch, source, value, review):
    """The person's answers: the source dialog (source and its value), then the review dialog (`review` changes it
    and returns whether Save was pressed). Keeps every dialog it was shown."""
    from orchestrator.gui import bundle_window
    shown = []

    def fake(dialog):
        from PySide6.QtWidgets import QComboBox, QLineEdit
        shown.append(dialog)
        if dialog.objectName() == "dlg_bundle_propose_review":
            return review(dialog)
        src = dialog.findChild(QComboBox, "cmb_bundle_propose_source")
        src.setCurrentIndex(src.findData(source))
        if source == "task":
            dialog.findChild(QComboBox, "cmb_bundle_propose_task").setCurrentText(value)
        else:
            dialog.findChild(QLineEdit, {"hf": "txt_bundle_propose_hf",
                                         "folder": "txt_bundle_propose_folder"}[source]).setText(value)
        return True
    monkeypatch.setattr(bundle_window, "_exec", fake)
    return shown


def test_propose_from_a_hub_model_shows_the_reasons_and_saves_only_on_the_persons_save(qapp, window, tmp_path,
                                                                                        monkeypatch):
    from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QPushButton
    from test_boards_window import Boxes, wait
    Boxes(monkeypatch)
    state = tmp_path / "state"
    prop.ProposalNotes(state).add("example/det-tiny", "Use 640 px input", "ditto")
    seen = {}

    def review(dialog):
        seen["why"] = dialog.findChild(QPlainTextEdit, "txt_bundle_propose_reasons").toPlainText()
        seen["yaml"] = dialog.findChild(QPlainTextEdit, "txt_bundle_propose_yaml")
        seen["ai"] = dialog.findChild(QPlainTextEdit, "txt_bundle_propose_ai").toPlainText()
        seen["ro"] = dialog.findChild(QPlainTextEdit, "txt_bundle_propose_reasons").isReadOnly()
        seen["save"] = dialog.findChild(QPushButton, "btn_bundle_propose_save") is not None
        dialog.findChild(QLineEdit, "txt_bundle_propose_id").setText("detect-mine")
        yaml = seen["yaml"]
        yaml.setPlainText(yaml.toPlainText().replace("timeout_s: 60", "timeout_s: 90"))
        return True
    _answers(monkeypatch, "hf", "example/det-tiny", review)
    window.buttons["btn_bundle_block_propose"].click()
    wait(qapp, window)
    assert "the model's pipeline task is object-detection" in seen["why"] and seen["ro"] and seen["save"]
    assert "Use 640 px input" in seen["ai"] and "ditto" in seen["ai"]
    block = window.catalog.get("detect-mine")
    assert block is not None and block.trust == "own" and block.reach.routes[0].timeout_s == 90


def test_cancel_writes_nothing_and_an_edited_block_that_does_not_pass_is_refused(qapp, window, tmp_path,
                                                                              monkeypatch):
    from PySide6.QtWidgets import QPlainTextEdit
    from test_boards_window import Boxes, wait
    boxes = Boxes(monkeypatch)
    _answers(monkeypatch, "task", "image-classification", lambda dialog: False)
    window.buttons["btn_bundle_block_propose"].click()
    wait(qapp, window)
    assert not (tmp_path / "app" / "community").exists()

    def broken(dialog):
        edit = dialog.findChild(QPlainTextEdit, "txt_bundle_propose_yaml")
        edit.setPlainText(edit.toPlainText().replace("protocol: http-multipart", "protocol: ftp"))
        return True
    _answers(monkeypatch, "task", "image-classification", broken)
    window.buttons["btn_bundle_block_propose"].click()
    wait(qapp, window)
    assert not (tmp_path / "app" / "community").exists()
    assert any("was not saved" in a.text for a in boxes.asked)


def test_propose_from_a_folder_and_a_missing_model_name(qapp, window, tmp_path, monkeypatch):
    from test_boards_window import Boxes, wait
    boxes = Boxes(monkeypatch)
    model = tmp_path / "whisper-small"
    model.mkdir()
    (model / "README.md").write_text("---\npipeline_tag: automatic-speech-recognition\n---\n", encoding="utf-8")
    (model / "ggml-small.bin").write_bytes(b"x" * 100)
    _answers(monkeypatch, "folder", str(model), lambda dialog: True)
    window.buttons["btn_bundle_block_propose"].click()
    wait(qapp, window)
    block = window.catalog.get("stt-whisper-small")
    assert block is not None and block.reach.routes[0].protocol == "openai-audio-transcription"
    _answers(monkeypatch, "hf", "", lambda dialog: True)
    window.buttons["btn_bundle_block_propose"].click()
    assert any("Name the model" in a.text for a in boxes.asked)
