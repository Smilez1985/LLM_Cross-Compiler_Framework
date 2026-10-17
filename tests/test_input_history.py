"""Input history: recent models, targets, boards, URLs, devices and datasets per user, bounded, never secrets."""

import json
import os
import shutil
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.utils import input_history
from orchestrator.utils.input_history import MAX_PER_FIELD, InputHistory, refusal

# Built at runtime so that no token-shaped literal sits in the repository
HF_TOKEN = "hf_" + "Q7" * 16
OPENAI_KEY = "sk-" + "proj-" + "Zq4" * 10


@pytest.fixture
def history(tmp_path):
    return InputHistory(tmp_path / "state")


class TestStore:

    def test_newest_first_de_duplicated_and_per_field(self, history):
        assert history.get("model") == []
        history.add("model", "org/a")
        history.add("model", " org/b ")
        history.add("model", "org/a")
        history.add("url", "https://docs.radxa.com/")
        assert history.get("model") == ["org/a", "org/b"]
        assert history.get("url") == ["https://docs.radxa.com/"]
        assert history.get("device") == []

    def test_several_values_keep_their_order(self, history):
        history.add("url", "https://old.example/")
        history.add("url", "https://a.example/", "https://b.example/", "https://a.example/")
        assert history.get("url") == ["https://a.example/", "https://b.example/", "https://old.example/"]

    def test_bounded_per_field(self, history):
        for i in range(MAX_PER_FIELD + 7):
            history.add("device", f"10.0.0.{i}")
        recent = history.get("device")
        assert MAX_PER_FIELD == 20 and len(recent) == MAX_PER_FIELD
        assert recent[0] == f"10.0.0.{MAX_PER_FIELD + 6}" and "10.0.0.0" not in recent

    def test_empty_and_none_values_are_ignored(self, history):
        assert history.add("board", None, "", "   ") is False
        assert not history.path.exists()

    def test_unknown_field_is_an_error(self, history):
        with pytest.raises(ValueError):
            history.add("password", "x")
        with pytest.raises(ValueError):
            history.get("api_key")

    @pytest.mark.skipif(os.name != "posix", reason="file modes")
    def test_private_file_written_atomically(self, history):
        history.add("model", "org/a")
        assert history.path.stat().st_mode & 0o777 == 0o600
        assert [p.name for p in history.path.parent.iterdir()] == ["input_history.json"]
        assert json.loads(history.path.read_text()) == {"version": 1, "fields": {"model": ["org/a"]}}

    def test_clear_forgets_everything(self, history):
        history.add("model", "org/a")
        history.add("url", "https://docs.radxa.com/")
        assert history.clear() and not history.path.exists()
        assert history.clear()   # nothing to clear is fine

    def test_clear_also_removes_the_copy_of_a_damaged_file(self, history):
        history.path.parent.mkdir(parents=True)
        history.path.write_text('{"version": 1, "fields": {"url": ["https://old.example/"]}, "extra": 1}')
        assert history.get("url") == [] and history.path.with_name("input_history.json.bad").exists()
        assert history.clear()
        assert list(history.path.parent.iterdir()) == []   # nothing of the old history is left

    def test_damaged_file_starts_empty_and_keeps_a_copy(self, history):
        history.path.parent.mkdir(parents=True)
        history.path.write_text("{broken")
        assert history.get("model") == []
        assert history.path.with_name("input_history.json.bad").read_text() == "{broken"
        assert history.add("model", "org/a") and history.get("model") == ["org/a"]

    @pytest.mark.parametrize("content", ['{"version": 2, "fields": {}}', '{"version": 1, "fields": {"x": []}}',
                                         '{"version": 1, "fields": {"model": [3]}}', '[]'])
    def test_wrong_shape_counts_as_damaged(self, history, content):
        history.path.parent.mkdir(parents=True)
        history.path.write_text(content)
        assert history.get("model") == []

    def test_save_errors_are_not_fatal(self, history, monkeypatch, caplog):
        def fail(*args):
            raise OSError("read-only file system")

        monkeypatch.setattr(input_history.os, "replace", fail)
        assert history.add("model", "org/a") is False
        assert "Could not save the input history" in caplog.text
        assert [p for p in history.path.parent.iterdir()] == []   # no temp file left behind

    def test_for_config(self, tmp_path):
        assert input_history.for_config(None) is None
        assert input_history.for_config(SimpleNamespace()) is None
        cfg = ConfigManager(tmp_path / "configs")
        assert input_history.for_config(cfg).path == cfg.user_state_dir / "input_history.json"

    def test_the_old_shared_config_key_is_gone(self, tmp_path):
        # The history is personal: it lives in the per-user state directory, not in the installation's config.
        cfg = ConfigManager(tmp_path / "configs")
        assert "input_history" not in cfg.config_schemas
        cfg.set("language", "de")
        cfg.save_user_config()
        assert "input_history" not in (tmp_path / "configs" / "config.yml").read_text()


class TestNoSecrets:

    @pytest.mark.parametrize("value", [
        HF_TOKEN,
        f"/models/{HF_TOKEN}/weights",
        OPENAI_KEY,
        "https://user:hunter2@docs.example.org/",
        "https://" + HF_TOKEN[:5] + "@git.example.org/repo",    # token as user name
        "https://docs.example.org/?token=abc",
        "https://docs.example.org/?q=x&api_key=abc",
        "https://docs.example.org/page?X-Amz-Signature=abc",
        "https://docs.example.org/#access_token=abc",
        "https://docs.example.org/?Password=abc",
        "https://docs.example.org/guide;jsessionid=0123456789ABCDEF",   # session ids as path parameters,
        "https://forum.example.org/viewtopic.php?t=5&sid=0123456789abcdef",   # in the query
        "https://docs.example.org/?PHPSESSID=0123456789abcdef",
        "https://docs.example.org/?a=1;token=abc",                      # ";" separates parameters too
        "https://docs.example.org/?%74oken=abc",                        # percent-encoded name
        "https://docs.example.org/?jwt=abc.def.ghi",
        "root:hunter2@192.168.77.10",
        "https://[::1/",
        "org/model\n--trust-remote-code",
        "org/model\u202e",
        "x" * 1001,
    ])
    def test_refused(self, history, value):
        assert refusal(value)
        assert history.add("url", value) is False and history.add("device", value) is False
        assert not history.path.exists()

    @pytest.mark.parametrize("value", [
        "Qwen/Qwen3.5-0.8B", "/home/me/models/model.gguf", "C:\\data\\calib.txt", "https://docs.radxa.com/?q=npu",
        "https://wiki.example.org/page#section", "root@192.168.77.10", "fe80::1", "rock-5b.local", "rk3588",
        "https://docs.example.org/side/inside?topic=sidebar",
    ])
    def test_allowed(self, history, value):
        assert refusal(value) is None
        assert history.add("model", value) and history.get("model") == [value]

    def test_a_refused_value_does_not_block_the_others(self, history):
        assert history.add("url", "https://a.example/", f"https://a.example/?token={HF_TOKEN}")
        assert history.get("url") == ["https://a.example/"]
        assert HF_TOKEN not in history.path.read_text()

    def test_secrets_in_a_hand_edited_file_are_dropped(self, history):
        history.path.parent.mkdir(parents=True)
        history.path.write_text(json.dumps({"version": 1, "fields": {"url": [
            "https://u:pw@docs.example.org/", "https://ok.example/", " https://ok.example/ ", HF_TOKEN]}}))
        assert history.get("url") == ["https://ok.example/"]
        history.add("model", "org/a")
        assert HF_TOKEN not in history.path.read_text() and "u:pw" not in history.path.read_text()


# --- GUI (offscreen) ---------------------------------------------------------------------------------------

PLAIN_TARGET = """\
metadata:
  id: Plain
  name: Plain board
  architecture: aarch64
"""


@pytest.fixture
def framework(make_framework, tmp_path):
    """Two targets: Rockchip (declares boards) and Plain (declares none, so the board is typed)."""
    from test_foundation import FakeOrchestrator

    targets = tmp_path / "targets"
    (targets / "Rockchip").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "targets" / "Rockchip" / "target.yml", targets / "Rockchip" / "target.yml")
    (targets / "Plain").mkdir()
    (targets / "Plain" / "target.yml").write_text(PLAIN_TARGET)
    fm = make_framework(initialize=False)
    fm.config.set("targets_dir", str(targets))
    assert fm.initialize()
    fm.orchestrator = FakeOrchestrator()
    return fm


@pytest.fixture
def boxes(monkeypatch):
    from orchestrator.gui import builder_tab, deployment_window
    shown = []

    class RecordingMessageBox:
        @staticmethod
        def warning(parent, title, text, *args):
            shown.append(("warning", text))

        @staticmethod
        def information(parent, title, text, *args):
            shown.append(("information", text))

        @staticmethod
        def critical(parent, title, text, *args):
            shown.append(("critical", text))

    monkeypatch.setattr(builder_tab, "QMessageBox", RecordingMessageBox)
    monkeypatch.setattr(deployment_window, "QMessageBox", RecordingMessageBox)
    return shown


def items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def select(combo, data):
    idx = combo.findData(data)
    assert idx >= 0, (data, [combo.itemData(i) for i in range(combo.count())])
    combo.setCurrentIndex(idx)


def user_history(framework):
    return InputHistory(framework.config.user_state_dir)


@pytest.mark.usefixtures("qapp", "boxes")
class TestBuilderTab:

    def test_without_history_nothing_changes(self, framework):
        from orchestrator.gui.builder_tab import MODEL_SUGGESTIONS, BuilderTab

        tab = BuilderTab(framework)
        assert items(tab.model_input) == MODEL_SUGGESTIONS and tab.model_input.isEditable()
        assert tab.model_input.currentText() == "" and tab.model_input.lineEdit().placeholderText()
        assert tab.dataset_combo.isEditable() and items(tab.dataset_combo) == [] and tab.dataset_path is None

    @pytest.mark.parametrize("target, board", [("Rockchip", "rk3576"), ("Plain", None)])
    def test_recent_inputs_are_offered_and_preselected(self, framework, target, board):
        from orchestrator.gui.builder_tab import MODEL_SUGGESTIONS, BuilderTab

        recent = user_history(framework)
        recent.add("model", "Qwen/Qwen3.5-2B")
        recent.add("model", "/models/mine.gguf")
        recent.add("target", target)
        recent.add("board", "rk3576")
        recent.add("dataset", "/data/calib.txt")

        tab = BuilderTab(framework)
        assert items(tab.model_input) == ["/models/mine.gguf", "Qwen/Qwen3.5-2B"] + \
            [m for m in MODEL_SUGGESTIONS if m != "Qwen/Qwen3.5-2B"]
        assert tab.model_input.currentText() == "/models/mine.gguf"
        assert tab.target_combo.currentData() == target
        if board:
            assert tab.board_combo.currentData() == board and not tab.board_combo.isEditable()
        # A dataset changes the quantization: it is offered, never preselected.
        assert items(tab.dataset_combo) == ["/data/calib.txt"] and tab.dataset_path is None

    def test_a_target_without_boards_offers_recent_boards_as_suggestions(self, framework):
        from orchestrator.gui.builder_tab import BuilderTab

        user_history(framework).add("board", "rk3588", "my-board")
        tab = BuilderTab(framework)
        select(tab.target_combo, "Plain")
        assert tab.board_combo.isEditable()
        assert items(tab.board_combo) == ["(none)", "rk3588", "my-board"]
        tab.board_combo.setCurrentIndex(2)
        assert tab._selected_board() == "my-board"

    def test_a_submitted_build_is_remembered(self, framework, tmp_path):
        from orchestrator.gui.builder_tab import BuilderTab

        dataset = tmp_path / "calib.txt"
        dataset.write_text("text")
        tab = BuilderTab(framework)
        select(tab.target_combo, "Rockchip")
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        tab.model_input.setEditText(" org/new-model ")
        tab.dataset_path = str(dataset)
        tab._submit_build()

        req = framework.orchestrator.submitted[-1]
        assert (req.models, req.dataset_path, req.target_board) == (["org/new-model"], str(dataset), "rk3588")
        recent = user_history(framework)
        assert recent.get("model") == ["org/new-model"] and recent.get("target") == ["Rockchip"]
        assert recent.get("board") == ["rk3588"] and recent.get("dataset") == [str(dataset)]
        assert items(tab.model_input)[0] == "org/new-model" and items(tab.dataset_combo) == [str(dataset)]
        assert tab.model_input.currentText() == " org/new-model " and tab.dataset_path == str(dataset), \
            "what is typed stays as typed"

        # The next window (or the next start) offers them.
        again = BuilderTab(framework)
        assert again.model_input.currentText() == "org/new-model"
        assert (again.target_combo.currentData(), again.board_combo.currentData()) == ("Rockchip", "rk3588")

    def test_a_refused_build_is_not_remembered(self, framework, boxes):
        from orchestrator.gui.builder_tab import BuilderTab

        tab = BuilderTab(framework)
        select(tab.target_combo, "Rockchip")
        select(tab.format_combo, "rkllm")
        select(tab.board_combo, "rk3588")
        tab.quant_combo.setEditText("int4")
        tab.model_input.setEditText("org/never")
        tab._submit_build()
        assert boxes and boxes[0][0] == "warning" and not framework.orchestrator.submitted
        assert not user_history(framework).path.exists()

    def test_a_secret_is_never_remembered(self, framework, tmp_path):
        from orchestrator.gui.builder_tab import BuilderTab

        tab = BuilderTab(framework)
        select(tab.target_combo, "Rockchip")
        select(tab.format_combo, "gguf")
        # An existing model file: Start Build goes through BuildService.start_build, which refuses a local model that
        # does not exist (c3-build-path)
        model = tmp_path / "models" / f"{HF_TOKEN}.gguf"
        model.parent.mkdir()
        model.write_bytes(b"GGUF")
        tab.model_input.setEditText(str(model))
        tab._submit_build()
        assert framework.orchestrator.submitted, "the build itself still runs"
        recent = user_history(framework)
        assert recent.get("model") == [] and recent.get("target") == ["Rockchip"]
        assert HF_TOKEN not in recent.path.read_text()
        assert HF_TOKEN not in items(tab.model_input)

    def test_ssh_probe_offers_recent_devices_and_remembers_a_trusted_one(self, framework, monkeypatch):
        from orchestrator.gui import builder_tab

        user_history(framework).add("device", "10.0.0.5", "rock-5b.local")
        dlg = builder_tab.SSHConnectDialog(recent=user_history(framework).get("device"))
        assert items(dlg.ip_input) == ["10.0.0.5", "rock-5b.local"] and dlg.ip_input.isEditable()
        dlg.ip_input.setEditText(" 10.0.0.9 ")
        dlg.user_input.setText("root")
        assert dlg.get_data() == ("10.0.0.9", "root", "")

        trusted, started = [], []

        class Dialog:
            def __init__(self, parent=None, recent=()):
                self.recent = recent

            def exec(self):
                return 1

            def get_data(self):
                return "10.0.0.9", "root", ""

            def get_options(self):   # the dialog's Port and Name fields (scan --device --port --label)
                return 22, None

        monkeypatch.setattr(builder_tab, "SSHConnectDialog", Dialog)
        monkeypatch.setattr(builder_tab, "SSHProbeWorker", lambda *a, **k: SimpleNamespace(
            finished=SimpleNamespace(connect=lambda slot: None), start=lambda: started.append(a[0])))
        tab = builder_tab.BuilderTab(framework)

        # The host key is read off the GUI thread (gui/host_key.HostKeyCheck); the person's answer comes back to then.
        monkeypatch.setattr(tab.host_keys, "start", lambda host, port, then, step="": trusted.append(host) or then(False))
        tab._open_ssh_dialog()
        assert trusted == ["10.0.0.9"] and started == []
        assert user_history(framework).get("device") == ["10.0.0.5", "rock-5b.local"], "an untrusted host is not kept"

        monkeypatch.setattr(tab.host_keys, "start", lambda host, port, then, step="": then(True))
        tab._open_ssh_dialog()
        assert started == ["10.0.0.9"]
        assert user_history(framework).get("device") == ["10.0.0.9", "10.0.0.5", "rock-5b.local"]


@pytest.mark.usefixtures("qapp", "boxes")
class TestDeploymentWindow:

    def make(self, framework, tmp_path):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem
        from orchestrator.gui.deployment_window import DeploymentWindow

        win = DeploymentWindow(framework)
        item = QListWidgetItem("build")
        item.setData(Qt.UserRole, str(tmp_path / "build"))
        win.list_artifacts.addItem(item)
        win.list_artifacts.setCurrentItem(item)
        return win

    def test_default_device_without_history(self, framework, tmp_path):
        win = self.make(framework, tmp_path)
        assert win.cb_device.isEditable() and win.cb_device.currentText() == "192.168.1.100"
        assert items(win.cb_device) == []

    def test_recent_devices_and_target_are_offered(self, framework, tmp_path):
        recent = user_history(framework)
        recent.add("device", "10.0.0.7", "10.0.0.8")
        recent.add("target", "Plain")
        win = self.make(framework, tmp_path)
        assert items(win.cb_device) == ["10.0.0.7", "10.0.0.8"] and win.cb_device.currentText() == "10.0.0.7"
        assert win.cb_profiles.currentText() == "Plain"
        win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
        win.refresh_data()
        assert win.cb_profiles.currentText() == "Rockchip", "a refresh keeps the selection"

    def test_a_trusted_device_and_its_target_are_remembered(self, framework, tmp_path, monkeypatch):
        from orchestrator.gui import deployment_window

        win = self.make(framework, tmp_path)
        win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
        win.cb_device.setEditText("10.0.0.42")
        asked = []
        monkeypatch.setattr(deployment_window.QInputDialog, "getText", lambda *a: asked.append(a[2]) or ("", False))

        # The host key is read off the GUI thread (gui/host_key.HostKeyCheck); the person's answer comes back to then.
        monkeypatch.setattr(win.host_keys, "start", lambda host, port, then, step="": then(False))
        win._on_deploy()
        assert not user_history(framework).path.exists() and asked == []

        monkeypatch.setattr(win.host_keys, "start", lambda host, port, then, step="": then(True))
        win._on_deploy()   # cancelled at the password prompt, after the device was trusted
        assert asked and win.worker is None
        recent = user_history(framework)
        assert recent.get("device") == ["10.0.0.42"] and recent.get("target") == ["Rockchip"]
        assert items(win.cb_device) == ["10.0.0.42"] and win.cb_device.currentText() == "10.0.0.42"
        # The password typed at the prompt never reaches the history.
        assert set(json.loads(recent.path.read_text())["fields"]) == {"device", "target"}
