"""GUI parity for the deploy area (W1' of 2026-10-16, wave 5a, chain "deploy"): every `llmcf deploy package`,
`llmcf deploy run`, `llmcf deploy list-artifacts` and `llmcf scan` path by mouse.

- Deployment tab: Create package only... (deploy package, no board), Deploy existing package... (deploy run
  PACKAGE_PATH), the Port, Name and "Wait for the board" fields (--port, --label, --wait).
- Builder tab > Scan / Import...: Scan Local Host (the Python probe, stored as probe local), the fallback script
  (--script), Save the Profile As... (--output, --json), Remote Scan via SSH... with Port and Name and the trusted
  board record, Import from File with the board's name (--label).

Each handler calls the core step of the CLI (monkeypatched here), confirmations default to No, the host key, packaging,
the deploy and every probe run off the GUI thread, and every text exists in English and German.
"""

import string
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

RK3566 = REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt"


class FakeHostKey:
    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return b"made-up host key of a deploy-parity fixture board"


def board():
    from orchestrator.runtime.profile import parse_profile
    return parse_profile(RK3566.read_text(encoding="utf-8"))


@pytest.fixture
def window_framework(make_framework, tmp_path):
    from test_input_history import framework as make_window_framework
    return make_window_framework.__wrapped__(make_framework, tmp_path)


@pytest.fixture
def told(monkeypatch):
    """Every plain-text box: (text, default button); the answer is No unless a test sets told.answer."""
    from orchestrator.gui import plain

    class Shown(list):
        pass

    shown = Shown()
    state = SimpleNamespace(answer=plain.QMessageBox.No)
    monkeypatch.setattr(plain, "_exec", lambda box: shown.append(
        (box.text(), box.standardButton(box.defaultButton()) if box.defaultButton() else None)) or state.answer)
    shown.state = state
    return shown


def wait_for(qapp, condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def a_build(folder: Path, name="build-p") -> Path:
    import json
    build = folder / name
    build.mkdir(parents=True)
    (build / "smol-q4_0.gguf").write_bytes(b"GGUF fixture model")
    (build / "build_info.json").write_text(json.dumps({"model": "smol", "format": "gguf", "primary": "smol-q4_0.gguf",
                                                      "quantization": "Q4_0", "target": "Rockchip"}))
    return build


# --- texts and the menu module ------------------------------------------------------------------------------------

def test_every_text_exists_in_english_and_german_with_the_same_placeholders():
    from orchestrator.utils.l10n_deploy import TEXTS
    from orchestrator.utils.localization import ui_text

    def fields(text):
        return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name)

    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key, text in TEXTS["en"].items():
        assert fields(text) == fields(TEXTS["de"][key]), key
        assert ui_text(key, "en") == text and ui_text(key, "de") == TEXTS["de"][key], "merged into the table"


def test_the_deployment_tab_follows_the_language(qapp, window_framework):
    from orchestrator.gui.deployment_window import DeploymentWindow
    from orchestrator.utils.localization import get_instance
    i18n = get_instance()
    before = i18n.current_lang
    try:
        i18n.set_language("de")
        win = DeploymentWindow(window_framework)
        assert win.btn_package.text() == "Nur Paket erstellen…"
        assert win.btn_existing.text() == "Vorhandenes Paket deployen…"
    finally:
        i18n.set_language(before)
    assert DeploymentWindow(window_framework).btn_package.text() == "Create package only…"


def test_cli_paths_name_objects_of_the_two_tabs(qapp, window_framework):
    from PySide6.QtCore import QObject
    from orchestrator.gui.builder_tab import BuilderTab
    from orchestrator.gui.deployment_window import DeploymentWindow
    from orchestrator.gui.menus import deploy
    tabs = {deploy.DEPLOYMENT_TAB: DeploymentWindow(window_framework), deploy.BUILDER_TAB: BuilderTab(window_framework)}
    try:
        assert set(deploy.CLI_PATHS.values()) <= set(deploy.TAB_OBJECTS)
        for name, tab in deploy.TAB_OBJECTS.items():
            assert tabs[tab].findChild(QObject, name) is not None, name
        for command in ("deploy package", "deploy run", "deploy run --port", "deploy run --label", "deploy run --wait",
                        "scan", "scan --script", "scan --output", "scan --device", "scan --port", "scan --label",
                        "scan --import", "deploy list-artifacts"):
            assert command in deploy.CLI_PATHS, command
        assert all(reason for reason in deploy.MACHINE_ONLY.values())
        assert deploy.install(None, None) is None
    finally:
        tabs[deploy.BUILDER_TAB]._build_timer.stop()


def test_the_main_window_has_every_object(qapp, tmp_path, monkeypatch, memory_keyring):
    """The integrator's parity test finds each object of CLI_PATHS in the offscreen main window."""
    from PySide6.QtCore import QObject
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window
    from orchestrator.gui.menus import deploy
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    win = main_window.MainOrchestrator(REPO_ROOT)
    try:
        qapp.processEvents()   # the first-run permissions question is queued: it must meet the stand-in, not a test after

        for name, tab in deploy.TAB_OBJECTS.items():
            found = win.findChild(QObject, name)
            assert found is not None, name
            parent = found
            while parent is not None and parent is not getattr(win, tab):
                parent = parent.parent()
            assert parent is getattr(win, tab), f"{name} lives in {tab}"
    finally:
        win.update_timer.stop()
        win.builder_tab._build_timer.stop()
        win.close()


# --- the host key: off the GUI thread, "No" by default ------------------------------------------------------------

class TestHostKeyCheck:

    def check(self, window_framework, monkeypatch, status, error=None):
        from PySide6.QtWidgets import QWidget
        from orchestrator.gui import host_key
        seen = {"threads": [], "trusted": []}

        def read(folder, host, port):
            seen["threads"].append(threading.current_thread())
            if error is not None:
                raise error
            return FakeHostKey(), status

        monkeypatch.setattr(host_key, "read_host_key", read)
        from orchestrator.utils import ssh_trust
        monkeypatch.setattr(ssh_trust, "trust_host_key", lambda state, host, port, key: seen["trusted"].append(
            (host, port)))
        parent = QWidget()
        check = host_key.HostKeyCheck(parent, window_framework)
        return check, seen, parent

    def test_an_unknown_key_is_read_off_the_gui_thread_and_no_is_the_default(self, qapp, window_framework,
                                                                              monkeypatch, told):
        check, seen, _parent = self.check(window_framework, monkeypatch, "unknown")
        answers = []
        check.start("myboard.local", 2222, answers.append, step="deploy")
        assert wait_for(qapp, lambda: answers)
        assert answers == [False] and seen["trusted"] == [], "No (the default) trusts nothing"
        assert seen["threads"][0] is not threading.main_thread()
        (text, default), = told
        assert "myboard.local:2222" in text and "ssh-keygen -lf" in text and default == told.state.answer
        from orchestrator.gui.plain import QMessageBox
        told.state.answer = QMessageBox.Yes
        check.start("myboard.local", 2222, answers.append, step="deploy")
        assert wait_for(qapp, lambda: len(answers) == 2)
        assert answers[1] is True and seen["trusted"] == [("myboard.local", 2222)]

    def test_a_changed_key_is_refused_and_recorded_for_mojo(self, qapp, window_framework, monkeypatch, told):
        from orchestrator.runtime.device import reasons
        from orchestrator.runtime.device.trusted import TrustedDevices
        state = Path(window_framework.config.user_state_dir)
        device = TrustedDevices(state).record("192.0.2.70", 22, "radxa", FakeHostKey(), via="cli", paired=True)
        check, seen, _parent = self.check(window_framework, monkeypatch, "changed")
        answers = []
        check.start("192.0.2.70", 22, answers.append, step="deploy")
        assert wait_for(qapp, lambda: answers)
        assert answers == [False] and seen["trusted"] == [] and "CHANGED" in told[0][0]
        reach = TrustedDevices(state).last_deploys()[device.device_id]["reach"]
        assert (reach["step"], reach["code"]) == ("deploy", reasons.HOST_KEY_CHANGED)

    def test_an_unreachable_board_says_why(self, qapp, window_framework, monkeypatch, told):
        check, _seen, _parent = self.check(window_framework, monkeypatch, "", error=ConnectionRefusedError(111, "x"))
        answers = []
        check.start("192.0.2.71", 22, answers.append, step="probe")
        assert wait_for(qapp, lambda: answers)
        assert answers == [False] and "SSH server" in told[0][0] and "192.0.2.71" in told[0][0]

    def test_the_old_one_call_question_still_works(self, qapp, window_framework, monkeypatch, told):
        from orchestrator.gui import dialogs, host_key
        monkeypatch.setattr(host_key, "read_host_key", lambda folder, host, port: (FakeHostKey(), "known"))
        assert dialogs.confirm_host_key(None, window_framework, "192.0.2.72") is True
        monkeypatch.setattr(host_key, "read_host_key", lambda folder, host, port: (FakeHostKey(), "unknown"))
        assert dialogs.confirm_host_key(None, window_framework, "192.0.2.72") is False and told


# --- the Deployment tab -------------------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
class TestDeploymentTab:

    def window(self, framework, monkeypatch, trusted=True):
        from orchestrator.gui.deployment_window import DeploymentWindow
        win = DeploymentWindow(framework)
        reads = []
        monkeypatch.setattr(win.host_keys, "start", lambda host, port, then, step="": reads.append(
            (host, port, step)) or then(trusted))
        win.reads = reads
        return win

    def select_build(self, win, build):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem
        item = QListWidgetItem("build")
        item.setData(Qt.UserRole, str(build))
        win.list_artifacts.addItem(item)
        win.list_artifacts.setCurrentItem(item)

    def test_the_fields_and_their_checks(self, window_framework, monkeypatch, told):
        win = self.window(window_framework, monkeypatch)
        assert (win.spin_port.value(), win.spin_wait.value(), win.txt_label.text()) == (22, 300, "")
        assert win.spin_port.maximum() == 65535 and win.spin_wait.maximum() == 86400
        win.cb_device.setEditText("[fe80::1]")
        assert win._connection() is None and "without brackets" in told[-1][0]
        win.cb_device.setEditText("myboard.local")
        win.txt_user.setText("-oProxyCommand")
        assert win._connection() is None and "SSH user" in told[-1][0]
        win.txt_user.setText("radxa")
        win.txt_label.setMaxLength(100)
        win.txt_label.setText("x" * 65)
        assert win._connection() is None and "1 to 64" in told[-1][0]
        win.txt_label.setText("  Shed   board ")
        win.spin_port.setValue(2222)
        win.spin_wait.setValue(0)
        conn = win._connection()
        assert (conn.host, conn.user, conn.port, conn.label, conn.wait) == ("myboard.local", "radxa", 2222,
                                                                            "Shed board", 0)

    def test_deploy_to_target_takes_port_name_and_wait(self, window_framework, monkeypatch, tmp_path):
        from orchestrator.gui import deployment_window
        win = self.window(window_framework, monkeypatch)
        self.select_build(win, a_build(tmp_path / "b"))
        win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
        win.cb_device.setEditText("myboard.local")
        win.txt_user.setText("radxa")
        win.spin_port.setValue(2222)
        win.txt_label.setText("Shed board")
        win.spin_wait.setValue(60)
        started = []
        monkeypatch.setattr(deployment_window.DeploymentWorker, "start", lambda self: started.append(self))
        monkeypatch.setattr(deployment_window.QInputDialog, "getText", lambda *a: ("", True))
        win._on_deploy()
        assert win.reads == [("myboard.local", 2222, "deploy")]
        (worker,) = started
        assert (worker.port, worker.label, worker.wait_seconds, worker.package) == (2222, "Shed board", 60, None)
        assert worker.profile_name == "Rockchip" and worker.kv_cache == "f16"
        assert not win.btn_package.isEnabled() and not win.btn_deploy.isEnabled(), "one step at a time"
        assert win.btn_cancel.isVisibleTo(win)

    def test_the_worker_passes_port_and_wait_and_records_the_name(self, window_framework, monkeypatch, tmp_path):
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.gui.deployment_window import DeploymentWorker
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.utils import ssh_trust
        build = a_build(tmp_path / "b")
        package = tmp_path / "deploy_smol.zip"
        package.write_bytes(b"PK")
        calls = []
        manager = SimpleNamespace(create_deployment_package=lambda **kw: package,
                                  deploy_package=lambda *a, **kw: calls.append((a, kw)) or DeployResult(
                                      True, exit_status=0, device_state="ok", resumed=2, waited_s=41.0))
        monkeypatch.setattr(window_framework, "get_component",
                            lambda name: manager if name == "deployment_manager" else None)
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: FakeHostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
        worker = DeploymentWorker(window_framework, build, "Rockchip", {}, "myboard.local", "radxa", None, port=2222,
                                  label="Shed board", wait_seconds=60)
        done = []
        worker.finished.connect(lambda ok, msg: done.append((ok, msg)))
        worker.run()
        ((args, kwargs),) = calls
        assert args == (package, "myboard.local", "radxa") and kwargs["port"] == 2222 and kwargs["wait_seconds"] == 60
        (ok, msg), = done
        assert ok and str(package) in msg, "the package with its full path"
        assert "dropped 2 time(s)" in msg and "41 s" in msg
        device = TrustedDevices(Path(window_framework.config.user_state_dir)).get(worker.device_id)
        assert (device.label, device.port, device.trusted_via) == ("Shed board", 2222, "gui")
        assert f"Trusted board {device.device_id} (Shed board)" in msg
        assert worker.model_name == "smol-q4_0.gguf"

    def test_a_failed_package_names_its_reason_and_the_next_step(self, window_framework, monkeypatch, tmp_path):
        from orchestrator.Core.deployment_manager import DeployError
        from orchestrator.gui.deployment_window import DeploymentWorker
        manager = SimpleNamespace(create_deployment_package=lambda **kw: None,
                                  last_package_error=DeployError("The image is not pinned.",
                                                                 next="Package without Docker images."))
        monkeypatch.setattr(window_framework, "get_component", lambda name: manager)
        worker = DeploymentWorker(window_framework, tmp_path, "Rockchip", {}, "myboard.local", "radxa", None)
        done = []
        worker.finished.connect(lambda ok, msg: done.append((ok, msg)))
        worker.run()
        assert done == [(False, "Package generation failed: The image is not pinned.\n\n"
                                "Package without Docker images.")]

    def test_deploy_existing_package(self, window_framework, monkeypatch, tmp_path, told):
        from orchestrator.Core.deployment_manager import DeployResult
        from orchestrator.gui import deployment_window
        package = tmp_path / "deploy_smol_1.zip"
        with zipfile.ZipFile(package, "w") as zf:
            zf.writestr("smol-q4_0.gguf", b"GGUF")
            zf.writestr("deploy.sh", b"#!/bin/sh\n")
        win = self.window(window_framework, monkeypatch)
        win.cb_device.setEditText("192.0.2.80")
        win.txt_user.setText("radxa")
        chosen = []
        monkeypatch.setattr(deployment_window, "choose_file", lambda parent, title, start, flt: chosen.append(flt) or
                            str(package))
        monkeypatch.setattr(deployment_window.QInputDialog, "getText", lambda *a: ("pw-made-up", True))
        started = []
        monkeypatch.setattr(deployment_window.DeploymentWorker, "start", lambda self: started.append(self))
        win._on_existing()
        assert chosen == ["Deployment packages (*.zip)"] and win.reads == [("192.0.2.80", 22, "deploy")]
        (worker,) = started
        assert worker.package == package and worker.artifact_path is None and worker.password == "pw-made-up"
        calls = []
        manager = SimpleNamespace(create_deployment_package=lambda **kw: pytest.fail("nothing is packaged"),
                                  deploy_package=lambda *a, **kw: calls.append(a) or DeployResult(True, exit_status=0))
        monkeypatch.setattr(window_framework, "get_component", lambda name: manager)
        monkeypatch.setattr(worker, "_remember_device", lambda *a, **k: None)
        worker.run()
        assert calls == [(package, "192.0.2.80", "radxa")] and worker.model_name == "smol-q4_0.gguf"

    def test_no_password_question_without_trust_and_cancel_keeps_everything(self, window_framework, monkeypatch,
                                                                            tmp_path):
        from orchestrator.gui import deployment_window
        package = tmp_path / "deploy_x.zip"
        package.write_bytes(b"PK")
        monkeypatch.setattr(deployment_window, "choose_file", lambda *a: str(package))
        asked = []
        monkeypatch.setattr(deployment_window.QInputDialog, "getText", lambda *a: asked.append(a) or ("", False))
        win = self.window(window_framework, monkeypatch, trusted=False)
        win.cb_device.setEditText("192.0.2.81")
        win.txt_user.setText("radxa")
        win._on_existing()
        assert asked == [] and win.worker is None, "an untrusted board gets no password question"
        win = self.window(window_framework, monkeypatch, trusted=True)
        win.cb_device.setEditText("192.0.2.81")
        win.txt_user.setText("radxa")
        win._on_existing()
        assert len(asked) == 1 and win.worker is None, "Cancel at the password: nothing runs"
        monkeypatch.setattr(deployment_window, "choose_file", lambda *a: "")
        win._on_existing()
        assert len(asked) == 1 and len(win.reads) == 1, "Cancel in the file dialog: nothing is read"

    def test_the_deploy_runs_off_the_gui_thread(self, window_framework, monkeypatch, tmp_path):
        from orchestrator.gui import deployment_window
        package = tmp_path / "deploy_x.zip"
        package.write_bytes(b"PK")
        monkeypatch.setattr(deployment_window, "choose_file", lambda *a: str(package))
        monkeypatch.setattr(deployment_window.QInputDialog, "getText", lambda *a: ("", True))
        threads = []
        monkeypatch.setattr(deployment_window.DeploymentWorker, "run",
                            lambda self: threads.append(threading.current_thread()))
        win = self.window(window_framework, monkeypatch)
        win.cb_device.setEditText("192.0.2.82")
        win.txt_user.setText("radxa")
        win._on_existing()
        assert win.worker.wait(5000) and threads and threads[0] is not threading.main_thread()

    # --- Create package only... ---

    def package_window(self, window_framework, monkeypatch, folder):
        from orchestrator.gui import artifact_save, deployment_window
        win = self.window(window_framework, monkeypatch)
        calls = []

        class Manager:
            def create_package(self, artifact_path, profile_name, docker_config, output_dir, **extra):
                calls.append((artifact_path, profile_name, docker_config, output_dir, extra))
                path = Path(output_dir) / "deploy_smol_1.zip"
                path.write_bytes(b"PK")
                return path

        monkeypatch.setattr(window_framework, "get_component",
                            lambda name: Manager() if name == "deployment_manager" else None)
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: folder(start))
        monkeypatch.setattr(deployment_window.PackageWorker, "start", lambda self: self.run())
        return win, calls

    def test_create_package_only_needs_no_board(self, window_framework, monkeypatch, tmp_path, told):
        from orchestrator.Core.artifact_output import output_folder
        win, calls = self.package_window(window_framework, monkeypatch, lambda start: start)
        build = a_build(tmp_path / "b")
        self.select_build(win, build)
        win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
        win.grp_docker.setChecked(False)
        win._on_package()
        out = output_folder(window_framework)
        ((source, profile, docker, folder, extra),) = calls
        assert (source, profile, docker, folder) == (build, "Rockchip", {"use_docker": False}, out)
        assert extra["kv_cache"] == "f16" and not extra["cancelled"](), "the window's KV choice; not cancelled"
        assert win.reads == [], "no board is contacted"
        text, _ = told[-1]
        assert str(out / "deploy_smol_1.zip") in text and "USB stick" in text
        assert win.btn_package.isEnabled() and win.btn_deploy.isEnabled()

    def test_docker_and_a_model_file_when_nothing_is_selected(self, window_framework, monkeypatch, tmp_path, told):
        from orchestrator.Core.deployment_manager import DEFAULT_INFERENCE_IMAGE
        from orchestrator.gui import deployment_window
        target = tmp_path / "elsewhere"
        target.mkdir()
        win, calls = self.package_window(window_framework, monkeypatch, lambda start: str(target))
        model = tmp_path / "loose" / "model.gguf"
        model.parent.mkdir()
        model.write_bytes(b"GGUF")
        filters = []
        monkeypatch.setattr(deployment_window, "choose_file", lambda parent, title, start, flt: filters.append(flt) or
                            str(model))
        win.cb_profiles.setCurrentIndex(win.cb_profiles.findText("Rockchip"))
        win.grp_docker.setChecked(True)
        win._on_package()
        ((source, _profile, docker, folder, _extra),) = calls
        assert source == model.resolve() and folder == target.resolve()
        assert docker == {"use_docker": True, "image_name": DEFAULT_INFERENCE_IMAGE}
        assert ".gguf" in filters[0]

    def test_cancel_or_a_refused_folder_makes_no_package(self, window_framework, monkeypatch, tmp_path, told):
        from orchestrator.Core.artifact_output import build_folder
        win, calls = self.package_window(window_framework, monkeypatch, lambda start: "")
        self.select_build(win, a_build(tmp_path / "b"))
        win._on_package()
        assert calls == [] and "no package was made" in told[-1][0]
        refused = build_folder(window_framework) / "inside"
        refused.mkdir(parents=True)
        win, calls = self.package_window(window_framework, monkeypatch, lambda start: str(refused))
        self.select_build(win, a_build(tmp_path / "c"))
        win._on_package()
        assert calls == [] and "Not saved there" in told[-1][0]

    def test_a_refused_package_names_the_reason_and_the_next_step(self, window_framework, monkeypatch, tmp_path,
                                                                 told):
        from orchestrator.Core.deployment_manager import DeployError
        from orchestrator.gui.deployment_window import PackageWorker

        def refuse(*a, **k):
            raise DeployError("The image x:latest is not pinned.", next="Package without Docker images.")

        monkeypatch.setattr(window_framework, "get_component", lambda name: SimpleNamespace(create_package=refuse))
        worker = PackageWorker(window_framework, tmp_path, "Rockchip", True, tmp_path)
        done = []
        worker.finished.connect(lambda ok, text: done.append((ok, text)))
        worker.run()
        assert done == [(False, "No package was made: The image x:latest is not pinned.\n\n"
                                "Package without Docker images.")]

    def test_cancel_stops_the_packaging(self, window_framework, monkeypatch, tmp_path):
        from orchestrator.gui.deployment_window import PackageWorker
        seen = []
        monkeypatch.setattr(window_framework, "get_component", lambda name: SimpleNamespace(
            create_package=lambda *a, cancelled=None, **k: seen.append(cancelled()) or tmp_path / "p.zip"))
        worker = PackageWorker(window_framework, tmp_path, "Rockchip", False, tmp_path)
        worker.cancel()
        worker.run()
        assert seen == [True]

    def test_packaging_runs_off_the_gui_thread(self, window_framework, monkeypatch, tmp_path, told):
        from orchestrator.gui import artifact_save, deployment_window
        threads = []
        monkeypatch.setattr(deployment_window.PackageWorker, "run",
                            lambda self: threads.append(threading.current_thread()))
        monkeypatch.setattr(artifact_save, "choose_folder", lambda parent, title, start: start)
        win = self.window(window_framework, monkeypatch)
        self.select_build(win, a_build(tmp_path / "b"))
        win._on_package()
        assert win.package_worker.wait(5000) and threads[0] is not threading.main_thread()

    # --- review (w5-deploy-v): `deploy package ARTIFACT` takes any path, also after a listed build was clicked ---

    def test_another_build_folder_or_model_file_can_be_chosen_by_mouse(self, window_framework, monkeypatch, tmp_path,
                                                                       told):
        """Once a listed build was clicked, no click clears the list's current item, so "with no build selected, a
        model file" was out of reach; and a build folder the list does not name could not be packaged at all (the CLI
        takes any path). Other build or model file... adds either to the list for both deploy buttons."""
        from orchestrator.gui import deployment_window
        win, calls = self.package_window(window_framework, monkeypatch, lambda start: start)
        self.select_build(win, a_build(tmp_path / "listed"))
        assert win.btn_add_source.objectName() == "btn_deploy_add_source"
        assert [a.objectName() for a in win.btn_add_source.menu().actions()] == ["act_deploy_add_folder",
                                                                                 "act_deploy_add_model"]
        other = a_build(tmp_path / "copied from another computer", "build-x")
        titles = []
        monkeypatch.setattr(deployment_window, "choose_directory", lambda parent, title, start: titles.append(title)
                            or str(other))
        win.act_add_folder.trigger()
        assert win.list_artifacts.currentItem().data(deployment_window.Qt.UserRole) == str(other)
        assert str(other) in win.list_artifacts.currentItem().toolTip(), "the full path"
        win._on_package()
        assert calls[-1][0] == other.resolve(), "the chosen folder, with its manifest and model card"
        win.refresh_data()
        assert str(other) in [win.list_artifacts.item(i).data(deployment_window.Qt.UserRole)
                              for i in range(win.list_artifacts.count())], "kept when the list is read again"

        model = tmp_path / "loose" / "model.gguf"
        model.parent.mkdir()
        model.write_bytes(b"GGUF")
        filters = []
        monkeypatch.setattr(deployment_window, "choose_file", lambda parent, title, start, flt: filters.append(flt) or
                            str(model))
        win.act_add_model.trigger()
        win._on_package()
        assert calls[-1][0] == model.resolve() and ".gguf" in filters[0]
        win.act_add_model.trigger()
        assert win.list_artifacts.count() == 2, "a path chosen twice is listed once"

        before = len(calls)
        monkeypatch.setattr(deployment_window, "choose_directory", lambda *a: "")
        win.act_add_folder.trigger()
        assert win.list_artifacts.currentItem().data(deployment_window.Qt.UserRole) == str(model), "Cancel adds nothing"
        win._on_save()
        assert len(calls) == before and "build folder" in told[-1][0], "Save... stays for builds in the build folder"


# --- the Builder tab's hardware scan ------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
class TestScanMenu:

    @pytest.fixture
    def tab(self, window_framework):
        from orchestrator.gui.builder_tab import BuilderTab
        tab = BuilderTab(window_framework)
        yield tab
        tab._build_timer.stop()

    def test_the_menu_entries(self, tab):
        names = [a.objectName() for a in tab.scan_menu.actions() if a.objectName()]
        assert names == ["act_deploy_scan_import", "act_deploy_scan_remote", "act_deploy_scan_local",
                         "act_deploy_scan_script", "act_deploy_scan_save", "act_deploy_scan_script_save"]
        assert tab.act_scan_script.text().endswith("Fallback Script")
        assert tab.scan_btn.objectName() == "btn_deploy_scan" and tab.scan_btn.menu() is tab.scan_menu

    def test_scan_local_host_runs_the_python_probe_and_stores_this_computer(self, tab, window_framework, monkeypatch,
                                                                            tmp_path):
        from orchestrator.gui import builder_tab
        from orchestrator.runtime import hardware
        from orchestrator.runtime.profile import ProfileStore, read_profile
        monkeypatch.setattr(hardware, "local_profile", lambda **options: board())
        gui_copy = builder_tab.probe_result_dir(window_framework) / builder_tab.LOCAL_PROBE_FILE
        worker = builder_tab.LocalScanWorker(window_framework, gui_copy, gui_copy=gui_copy)
        done = []
        worker.finished.connect(lambda ok, msg, path: done.append((ok, msg, path)))
        worker.run()
        (ok, msg, path), = done
        store = ProfileStore(Path(window_framework.config.user_state_dir))
        assert ok and "Python probe" in msg and str(store.local_path()) in msg
        assert store.load_local() == board().stable() and Path(path) == gui_copy and read_profile(gui_copy) == board()

    def test_save_as_writes_the_json_view_and_keeps_a_copy_for_the_tab(self, tab, window_framework, monkeypatch,
                                                                      tmp_path):
        from orchestrator.gui import builder_tab
        from orchestrator.runtime import hardware
        from orchestrator.runtime.profile import parse_profile_json, read_profile
        monkeypatch.setattr(hardware, "local_profile", lambda **options: board())
        out = tmp_path / "saved" / "probe.json"
        gui_copy = tmp_path / "copy.txt"
        worker = builder_tab.LocalScanWorker(window_framework, out, as_json=True, gui_copy=gui_copy)
        done = []
        worker.finished.connect(lambda ok, msg, path: done.append((ok, path)))
        worker.run()
        assert done == [(True, str(gui_copy))]
        assert parse_profile_json(out.read_text(encoding="utf-8")) == board() and read_profile(gui_copy) == board()

    def test_a_failing_probe_names_the_next_step(self, tab, window_framework, monkeypatch, tmp_path):
        from orchestrator.gui import builder_tab
        from orchestrator.runtime import scan_steps
        from orchestrator.runtime.probe_service import ProbeError

        def broken(*a, **k):
            raise ProbeError("The probe script failed (OSError).", next="Run 'llm-cli scan --script' in a terminal.")

        monkeypatch.setattr(scan_steps, "scan_local", broken)
        worker = builder_tab.LocalScanWorker(window_framework, tmp_path / "p.txt")
        done = []
        worker.finished.connect(lambda ok, msg, path: done.append((ok, msg)))
        worker.run()
        assert done == [(False, "The probe failed: The probe script failed (OSError).\nRun 'llm-cli scan --script' "
                                "in a terminal.")]

    def test_the_entries_start_the_cli_step_with_its_options(self, tab, window_framework, monkeypatch, tmp_path):
        from orchestrator.gui import builder_tab
        started = []

        class Worker:
            def __init__(self, framework, output, **options):
                started.append((Path(output), options))
                self.finished = SimpleNamespace(connect=lambda slot: None)

            def start(self):
                pass

        monkeypatch.setattr(builder_tab, "LocalScanWorker", Worker)
        gui_copy = builder_tab.probe_result_dir(window_framework) / builder_tab.LOCAL_PROBE_FILE
        tab.act_scan_local.trigger()
        tab.act_scan_script.trigger()
        assert started[0] == (gui_copy, {"use_script": False, "as_json": False, "gui_copy": gui_copy})
        assert started[1][1]["use_script"] is True
        answers = iter([(str(tmp_path / "probe.json"), "JSON view (*.json)"), (str(tmp_path / "plain"), "Probe file"),
                        ("", "")])
        monkeypatch.setattr(builder_tab, "choose_save_file", lambda *a: next(answers))
        tab.act_scan_save.trigger()
        tab.act_scan_save.trigger()
        tab.act_scan_save.trigger()
        assert started[2] == (tmp_path / "probe.json", {"use_script": False, "as_json": True, "gui_copy": gui_copy})
        assert started[3][0] == tmp_path / "plain.txt" and started[3][1]["as_json"] is False
        assert len(started) == 4, "Cancel in the save dialog scans nothing"

    def test_the_fallback_script_can_save_the_profile_too(self, tab, window_framework, monkeypatch, tmp_path):
        """Review (w5-deploy-v): `llmcf scan --script --output FILE` (the script writes the file itself; a .json file
        gets the JSON view) was not possible by mouse: the script entry wrote only the tab's copy."""
        from orchestrator.gui import builder_tab
        started = []

        class Worker:
            def __init__(self, framework, output, **options):
                started.append((Path(output), options))
                self.finished = SimpleNamespace(connect=lambda slot: None)

            def start(self):
                pass

        monkeypatch.setattr(builder_tab, "LocalScanWorker", Worker)
        answers = iter([(str(tmp_path / "probe.txt"), "Probe file"), (str(tmp_path / "probe.json"), "JSON view"),
                        ("", "")])
        monkeypatch.setattr(builder_tab, "choose_save_file", lambda *a: next(answers))
        gui_copy = builder_tab.probe_result_dir(window_framework) / builder_tab.LOCAL_PROBE_FILE
        for _ in range(3):
            tab.act_scan_script_save.trigger()
        assert started == [(tmp_path / "probe.txt", {"use_script": True, "as_json": False, "gui_copy": gui_copy}),
                           (tmp_path / "probe.json", {"use_script": True, "as_json": True, "gui_copy": gui_copy})]
        assert "Fallback Script" in tab.act_scan_script_save.text()

    def test_a_failed_import_names_the_next_step(self, tab, monkeypatch, tmp_path):
        """Review (w5-deploy-v): `llmcf scan --import` prints the refusal and its next step; the GUI logged only the
        refusal."""
        from PySide6.QtWidgets import QInputDialog
        from orchestrator.gui import builder_tab
        from orchestrator.runtime import probe_service
        broken = tmp_path / "not_a_probe.txt"
        broken.write_text("hello\n", encoding="utf-8")

        def refuse(self, path, label=None):
            raise probe_service.ProbeError("This is not a probe file.", next="Run the probe script on the board.")

        monkeypatch.setattr(probe_service.HardwareProbes, "import_file", refuse)
        monkeypatch.setattr(builder_tab.QFileDialog, "getOpenFileName", lambda *a: (str(broken), ""))
        monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("", True))
        monkeypatch.setattr(tab, "_load_probe_config", lambda path: None)
        tab.act_scan_import.trigger()
        log = tab.log_area.toPlainText()
        assert "This is not a probe file." in log and "Run the probe script on the board." in log

    def test_the_local_scan_runs_off_the_gui_thread(self, tab, monkeypatch):
        from orchestrator.runtime import scan_steps
        threads = []

        def scan(*a, **k):
            threads.append(threading.current_thread())
            raise RuntimeError("stop here")

        monkeypatch.setattr(scan_steps, "scan_local", scan)
        tab.act_scan_local.trigger()
        assert tab.probe_worker.wait(5000) and threads[0] is not threading.main_thread()

    def test_remote_scan_takes_port_and_name(self, tab, monkeypatch):
        from orchestrator.gui import builder_tab
        dialog = builder_tab.SSHConnectDialog()
        assert dialog.get_options() == (22, None)
        dialog.port_input.setValue(2222)
        dialog.label_input.setText("  Shed   board ")
        assert dialog.get_options() == (2222, "Shed board")
        reads, workers = [], []

        class Dialog:
            def __init__(self, parent=None, recent=()):
                pass

            def exec(self):
                return 1

            def get_data(self):
                return "192.0.2.90", "radxa", "pw-made-up"

            def get_options(self):
                return 2222, "Shed board"

        monkeypatch.setattr(builder_tab, "SSHConnectDialog", Dialog)
        monkeypatch.setattr(builder_tab, "SSHProbeWorker", lambda *a, **k: workers.append((a, k)) or SimpleNamespace(
            finished=SimpleNamespace(connect=lambda slot: None), start=lambda: None))
        monkeypatch.setattr(tab.host_keys, "start", lambda host, port, then, step="": reads.append(
            (host, port, step)) or then(True))
        tab._open_ssh_dialog()
        assert reads == [("192.0.2.90", 2222, "probe")]
        ((args, kwargs),) = workers
        assert args[:3] == ("192.0.2.90", "radxa", "pw-made-up") and kwargs["port"] == 2222
        assert kwargs["label"] == "Shed board"

    def test_the_ssh_probe_records_the_trusted_board(self, window_framework, monkeypatch, tmp_path):
        from orchestrator.gui import builder_tab
        from orchestrator.runtime import remote_probe as rp
        from orchestrator.runtime.device.trusted import TrustedDevices
        from orchestrator.runtime.profile import ProfileStore
        from orchestrator.utils import ssh_trust

        class Client:
            def close(self):
                pass

            def get_transport(self):
                return SimpleNamespace(get_remote_server_key=lambda: FakeHostKey())

        monkeypatch.setattr(builder_tab, "PARAMIKO_AVAILABLE", True)
        monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None: Client())
        monkeypatch.setattr(rp, "probe_over_ssh", lambda client: rp.RemoteProbeResult(board(), "python", []))
        audited = []
        audit = SimpleNamespace(record=lambda *a, **k: audited.append(a))
        state = tmp_path / "state"
        worker = builder_tab.SSHProbeWorker("192.0.2.91", "radxa", None, state, tmp_path / "probes", port=2222,
                                            label="Shed board", audit=audit)
        done = []
        worker.finished.connect(lambda ok, msg, path: done.append((ok, msg, path)))
        worker.run()
        (ok, msg, path), = done
        (device,) = TrustedDevices(state).list()
        assert ok and device.paired and (device.label, device.port, device.trusted_via) == ("Shed board", 2222, "gui")
        assert f"Trusted board {device.device_id} (Shed board)" in msg and "Python probe" in msg
        assert audited and audited[0][:2] == ("gui", "device_trusted")
        assert ProfileStore(state).device_info(device.device_id)["label"] == "Shed board"
        assert Path(path).parent == tmp_path / "probes"

    def test_a_failed_login_says_why(self, window_framework, monkeypatch, tmp_path):
        paramiko = pytest.importorskip("paramiko")
        from orchestrator.gui import builder_tab
        from orchestrator.utils import ssh_trust

        def refuse(*a, **k):
            raise paramiko.AuthenticationException("no")

        monkeypatch.setattr(builder_tab, "PARAMIKO_AVAILABLE", True)
        monkeypatch.setattr(ssh_trust, "connect", refuse)
        worker = builder_tab.SSHProbeWorker("192.0.2.92", "radxa", None, tmp_path / "state", tmp_path / "probes")
        done = []
        worker.finished.connect(lambda ok, msg, path: done.append((ok, msg)))
        worker.run()
        (ok, msg), = done
        assert not ok and "192.0.2.92" in msg and "password" in msg.lower()

    def test_import_asks_for_the_board_s_name(self, tab, window_framework, monkeypatch):
        from PySide6.QtWidgets import QInputDialog
        from orchestrator.gui import builder_tab
        from orchestrator.runtime.probe_service import HardwareProbes
        monkeypatch.setattr(builder_tab.QFileDialog, "getOpenFileName", lambda *a: (str(RK3566), ""))
        answers = iter([("", False), ("Zero 3W", True)])
        monkeypatch.setattr(QInputDialog, "getText", lambda *a: next(answers))
        loaded = []
        monkeypatch.setattr(tab, "_load_probe_config", lambda path: loaded.append(path))
        tab.act_scan_import.trigger()
        probes = HardwareProbes.for_framework(window_framework)
        assert probes.list() == [] and loaded == [], "Cancel at the name: nothing is imported"
        tab.act_scan_import.trigger()
        (probe,) = probes.list()
        assert probe.label == "Zero 3W" and loaded == [RK3566]
        assert f"Stored as probe {probe.probe_id} (Zero 3W)" in tab.log_area.toPlainText()
