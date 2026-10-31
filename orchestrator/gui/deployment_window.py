#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deployment Window (v2.3.0 Functional)
DIREKTIVE: Goldstandard GUI & Threading.

Zweck:
Verbindet die GUI mit dem DeploymentManager.
Führt Paketierung und Deployment asynchron im Hintergrund aus (QThread).

Updates v2.3.0:
- Safe config access via ConfigManager.get().
- Improved file browsing for artifacts.
- Robust SecretsManager integration.

Board deployment (eighth round N4-N7):
- The board is named by its address or its host name (192.168.1.50 or myboard.local); the host key is trusted per
  host string, after the person compared the fingerprint (dialogs.confirm_host_key).
- When the network drops during the deploy (a Wi-Fi board), the window shows that it waits for the board, how long
  it has waited and the limit, with a Cancel button; the upload then continues where it stopped
  (DeploymentManager.deploy_package).
- A board that cannot be reached or logged in to says why and what to do (orchestrator/runtime/device/reasons.py).
- The KV-cache type of the board's server: automatic (q8_0 for a board with little RAM next to the model, from its
  probe) or the person's choice; it is named in the start command of the package and kept in the board's deploy
  settings.
GUI parity (W1' of 2026-10-16, wave 5a): "Create package only..." makes the package without a board (the CLI's
`deploy package`, also for an air-gapped board, saved through the folder dialog at the output folder), "Deploy existing
package..." uploads a ZIP made earlier (`deploy run PACKAGE_PATH`), and the Port, Name and "Wait for the board" fields
are `--port`, `--label` and `--wait`. The host key is read off the GUI thread (gui/host_key.HostKeyCheck) and the
question has "No" as its default; packaging and the deploy run in worker threads.
Docker containers (DECISIONS round 23, item 6): the "Use Docker Containerization?" group lists the containers a package
with Docker images carries for the chosen profile, from the list the CLI's `deploy package --docker` packs
(deployment_manager.package_containers: the image of the settings, the hardware access of the target profile). The
person's ticks decide which go into the package (package_docker_config); the CLI packs every one.
Every text that can hold a value from outside the framework is plain text (gui/plain.py).
"""

import threading
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QHeaderView, QMessageBox, QListWidget,
    QProgressBar, QInputDialog, QLineEdit, QListWidgetItem, QSpinBox, QFileDialog
)
from PySide6.QtCore import Qt, QThread, Signal
from orchestrator.utils.localization import get_instance as get_i18n, ui_text
from orchestrator.gui.dialogs import fill_recent, select_recent
from orchestrator.gui.plain import ask, plain_box, plain_label, plain_tooltip
from orchestrator.utils import input_history

KV_CHOICES = ("auto", "f16", "q8_0", "q4_0")
# What a deploy keeps in the board's deploy settings when the person did not choose the KV cache in this window: the
# saved choice stays (the window may not have shown it, e.g. under another name of the board).
KV_KEEP = "keep"


def event_text(event, host: str) -> str:
    """The visible state of a running deploy (N5) in the interface language (remote/text.py event_text)."""
    from orchestrator.remote.text import event_text as text_of
    return text_of(event, host)


def choose_file(parent, title: str, start: str, filter_text: str) -> str:
    """The operating system's own file dialog; "" when the person cancels (tests replace it)."""
    return QFileDialog.getOpenFileName(parent, title, start, filter_text)[0]


def choose_directory(parent, title: str, start: str) -> str:
    """The operating system's own folder dialog; "" when the person cancels (tests replace it)."""
    return QFileDialog.getExistingDirectory(parent, title, start, QFileDialog.ShowDirsOnly)


def tested_suffix(build_dir: Path) -> str:
    """" · tested on <boards>" for a build that passed its benchmark on a board (N3; the names come from the boards'
    probes, shown as plain text), else ""."""
    from orchestrator.runtime.device.tested import tested_on
    boards = tested_on(build_dir)
    return " · " + ui_text("device.tested_on", boards=", ".join(boards)) if boards else ""


class DeploymentWorker(QThread):
    """
    Führt den Deployment-Prozess im Hintergrund aus, um GUI-Freeze zu verhindern.
    """
    progress = Signal(str)
    finished = Signal(bool, str)
    event = Signal(object)   # DeployEvent: the upload's progress, the wait for the board

    def __init__(self, framework, artifact_path, profile_name, docker_config, target_ip, user, password, *,
                 port: int = 22, label=None, wait_seconds=None, package=None, key=None):
        # docker_config: {"use_docker": the group's tick, "containers": the containers ticked in its table (None or
        # left out: every one, as `deploy package --docker`)}
        super().__init__()
        self.framework = framework
        self.artifact_path = artifact_path
        self.profile_name = profile_name
        self.docker_config = docker_config
        self.target_ip = target_ip
        self.user = user
        self.password = password
        self.port = int(port)             # the board's SSH port (deploy run --port)
        self.label = label                # the person's name for the board (deploy run --label); None keeps it
        self.wait_seconds = wait_seconds  # how long a dropped network is waited for (deploy run --wait); None: 300 s
        self.package = Path(package) if package else None  # an existing package (deploy run PACKAGE_PATH)
        # the host key the person trusted (or that was known) in this window's question: the deploy must reach exactly
        # that key, and the board's record is written with it without reading the key again
        self.key = key
        self.trusted = None               # the board's trusted record after a successful deploy
        self.kv_cache = None            # the KV-cache type named in the package's start command
        self.kv_setting = KV_KEEP       # what the board's deploy settings get: None (automatic), a type, or KV_KEEP
        self.device_id = None           # the board's record after a successful deploy
        self.model_name = None          # the GGUF model the package installed (for the benchmark on the board)
        self.package_error = None       # why _make_package made no package (a DeployError with its next step)
        # the one cancel of a board step (remote/session.py CancelToken): it holds the deploy's SSH session
        from orchestrator.remote.session import CancelToken
        self._stop = CancelToken()

    def cancel(self) -> None:
        """Stops the deploy: the wait for the board ends at once, a running upload stops and its session closes."""
        self._stop.cancel()

    def _on_client(self, client) -> None:
        """The deploy's SSH session, so Cancel can close it (at once when Cancel came first)."""
        self._stop.hold(client)

    def run(self):
        password, self.password = self.password, None   # the one-time password: used once, never kept
        dep_mgr = self.framework.get_component("deployment_manager")
        if not dep_mgr:
            self.finished.emit(False, "DeploymentManager not loaded.")
            return

        from orchestrator.remote.address import Endpoint
        from orchestrator.remote.session import KeyOnly, OneTimePassword
        from orchestrator.runtime.device.person_steps import deploy_build
        try:
            # the one deploy of a build (person_steps.deploy_build, also `llmcf deploy run`): the package of the
            # selected build in the output folder (make_package: Cancel stops it too), or the existing package; the
            # deploy, which waits for the board when its network drops and says why when it cannot reach it, bound to
            # the key of this window's question; then the board's trusted record
            done = deploy_build(self.framework, dep_mgr, Endpoint(self.target_ip, self.port), self.user,
                                source=None if self.package is not None else self.artifact_path, package=self.package,
                                profile=self.profile_name, docker=bool((self.docker_config or {}).get("use_docker")),
                                containers=(self.docker_config or {}).get("containers"),
                                credentials=OneTimePassword(password) if password else KeyOnly(), label=self.label,
                                kv_cache=self.kv_cache, wait=self.wait_seconds, on_event=self.event.emit,
                                cancel=self._stop, key=self.key, via="gui", actor="gui",
                                remember=lambda key: self._remember_device(key=key), on_step=self._on_step)
            if done.package_error is not None:
                error = done.package_error
                self.finished.emit(False, "\n\n".join(
                    p for p in (ui_text("deploy.package_failed", reason=str(error)), getattr(error, "next", "")) if p))
                return
            pkg_path, result = done.package, done.result

            if result.success:
                self.model_name = self._model_name()
                # an old container of an earlier package still on the board: told, never removed (only the person)
                state = f"device.deploy.state.{result.old_sidecar}"
                old = ([ui_text("device.deploy.old_sidecar", state=ui_text(state))]
                       if result.old_sidecar in ("running", "stopped") else [])
                # the package with its full path (transparency)
                lines = [ui_text("device.deploy.done", name=str(pkg_path))]
                if getattr(result, "resumed", 0):
                    lines.append(ui_text("deploy.resumed", count=result.resumed, seconds=int(result.waited_s)))
                if self.trusted is not None:
                    lines.append(ui_text("deploy.trusted", device_id=self.trusted.device_id, label=self.trusted.label))
                self.finished.emit(True, "\n\n".join([*lines, *old]))
            else:
                self.finished.emit(False, self._failure_text(result))

        except Exception as e:
            self.finished.emit(False, str(e))

    def _on_step(self, kind: str) -> None:
        """The deploy's step for the button text: packaging the build, then the deploy."""
        if kind == "package":
            self.progress.emit("📦 Generating Deployment Package...")
        elif kind == "deploy":
            self.progress.emit(f"🚀 Deploying to {self.target_ip}...")

    def _make_package(self, dep_mgr):
        """Step 1 of a deploy on its own, kept for older callers (run() packages through person_steps.deploy_build):
        the package of the selected build in the configured output folder, made as run() makes it (make_package: the
        image name of a Docker package; Cancel stops it). None when it cannot be made; the reason is then in
        `package_error`, never in the manager's shared state."""
        from orchestrator.Core.deployment_manager import DeployError, make_package, package_folder
        self.package_error = None
        self._on_step("package")
        try:
            extra = {"containers": self.docker_config["containers"]} \
                if (self.docker_config or {}).get("containers") is not None else {}
            return make_package(dep_mgr, self.framework, self.artifact_path, self.profile_name,
                                bool((self.docker_config or {}).get("use_docker")), package_folder(self.framework),
                                kv_cache=self.kv_cache, cancelled=self._stop.is_set, **extra)
        except DeployError as e:
            self.package_error = e
            return None

    def _model_name(self):
        """The GGUF model deploy.sh copied to models/ in the home folder (for the benchmark on the board); with Docker
        it goes into the container instead, so there is no path on the board to measure."""
        from orchestrator.Core.deployment_manager import package_model_name, resolve_model_artifact
        if self.package is not None:
            return package_model_name(self.package)
        try:
            name = resolve_model_artifact(Path(self.artifact_path)).name
        except (OSError, ValueError, TypeError):
            return None
        docker = bool((self.docker_config or {}).get("use_docker"))
        return name if name.lower().endswith(".gguf") and not docker else None

    def _failure_text(self, result) -> str:
        """Why the deploy failed, for the person: the reason code's title and next step first, then the details."""
        from orchestrator.runtime.device import reasons
        parts = []
        if result.code in reasons.CODES:
            parts.append(reasons.person_text(result.code, address=self.target_ip, port=self.port))
        parts.append(ui_text("device.deploy.failed", reason=result.reason or "Deployment failed during "
                                                                            "transfer/execution."))
        return "\n\n".join(parts)

    def _remember_device(self, port=None, key=None):
        """After a deployment to a device whose host key the person confirmed in this window (confirm_host_key) and
        that the framework logged in to: its record, so AI tools may name it for deploy_to_device (each deploy is
        still confirmed). Written only here and in the CLI, never by a tool; it never fails the deployment. The record
        and its audit row are the CLI's (person_steps.remember_deployed: the registry's audit log, else the per-user
        one). The KV-cache choice of this window goes into the board's deploy settings. The key is the one of this
        window's question (the deploy reached exactly that one: expect_fingerprint); only a worker started without it
        reads the key again. Returns the record (None: not written)."""
        from orchestrator.runtime.device.person_steps import remember_deployed
        port = self.port if port is None else int(port)
        state = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        if not state:
            return None
        device = remember_deployed(Path(state), self.target_ip, port, self.user, self.key if key is None else key,
                                   via="gui", actor="gui", label=self.label, framework=self.framework)
        if device is not None:
            self.device_id, self.trusted = device.device_id, device
            if self.kv_setting != KV_KEEP:
                _save_kv_setting(Path(state), device.device_id, self.kv_setting)
        return device


def _save_kv_setting(state_dir: Path, device_id: str, kind) -> None:
    """Keeps the person's KV-cache choice in the board's deploy settings (None: automatic); never fails a deploy."""
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    from orchestrator.runtime.profile import ProfileStore
    store = ProfileStore(state_dir)
    try:
        current = DeploySettings.from_dict(store.load_deploy_settings(device_id))
        if current.kv_cache != kind:
            store.save_deploy_settings(device_id, current.changed(kv_cache=kind).as_dict())
    except (SettingsError, ValueError, OSError):
        pass  # damaged settings stay as they are; `llm-cli device settings` repairs them


class PackageWorker(QThread):
    """"Create package only...": the deployment ZIP without any board (the CLI's `deploy package`, the same core step
    make_package), off the GUI thread; Cancel stops it between the steps and no partial package stays."""
    finished = Signal(bool, str)

    def __init__(self, framework, source: Path, profile_name: str, docker: bool, folder: Path, kv_cache=None,
                 containers=None):
        super().__init__()
        self.framework, self.source, self.profile_name = framework, Path(source), profile_name
        self.docker, self.folder, self.kv_cache = bool(docker), Path(folder), kv_cache
        self.containers = list(containers) if containers is not None else None   # None: every container
        self.package = None
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def run(self):
        from orchestrator.Core.deployment_manager import DeployError, make_package
        manager = self.framework.get_component("deployment_manager") if hasattr(self.framework,
                                                                               "get_component") else None
        if not manager:
            self.finished.emit(False, ui_text("deploy.package.failed", reason="DeploymentManager not loaded."))
            return
        try:
            extra = {"containers": self.containers} if self.containers is not None else {}
            self.package = make_package(manager, self.framework, str(self.source), self.profile_name, self.docker,
                                        self.folder, kv_cache=self.kv_cache, cancelled=self._stop.is_set, **extra)
        except DeployError as e:
            self.finished.emit(False, "\n\n".join(p for p in (ui_text("deploy.package.failed", reason=str(e)),
                                                               e.next) if p))
            return
        except Exception as e:  # noqa: BLE001 - shown to the person; no package stays (create_package)
            self.finished.emit(False, ui_text("deploy.package.failed", reason=str(e)))
            return
        self.finished.emit(True, ui_text("deploy.package.done", path=str(self.package)))


class BoardBenchWorker(QThread):
    """The checks of the board's runtime with a short llama-bench run on a model on the board (seventh round 2',
    N3): the measurement runs on the board, never on this computer, and a passed run is recorded with the builds of
    that model as "tested on" the board."""
    finished = Signal(bool, str)

    def __init__(self, framework, device_id: str, model: str, allow_untrusted: bool):
        super().__init__()
        self.framework = framework
        self.device_id = device_id
        self.model = model
        self.allow_untrusted = allow_untrusted

    def _consent(self, digest: str) -> None:
        """The person's yes to an untrusted model, written to the audit log before the board loads it."""
        from orchestrator.remote.pairing import record_consent
        from orchestrator.runtime.device.person_steps import UNTRUSTED_CONSENT, device_audit
        from orchestrator.runtime.device.service import DeviceRuntimeError
        state = Path(getattr(getattr(self.framework, "config", None), "user_state_dir", "") or ".")
        audit = device_audit(self.framework, state)   # the one sink (round 25 Q3): the registry's, else audit.db
        tool, detail = UNTRUSTED_CONSENT
        try:
            record_consent(audit, tool, {"device": self.device_id, "model_sha256": digest}, detail,
                           channel="framework-gui")
        except Exception as e:  # noqa: BLE001 - without its record there is no consent
            raise DeviceRuntimeError(f"The consent could not be written to the audit log ({e}); nothing was "
                                     "loaded.") from None

    def run(self):
        from orchestrator.runtime.device import reasons
        from orchestrator.runtime.device import service as dev
        from orchestrator.runtime.device.settings import SettingsError, check_model_path
        from orchestrator.runtime.device.trusted import TrustedDevices
        config = getattr(self.framework, "config", None)
        state = Path(getattr(config, "user_state_dir", "") or ".")
        device = TrustedDevices(state).get(self.device_id)
        if device is None:
            self.finished.emit(False, ui_text("device.bench.none"))
            return
        try:
            from orchestrator.Core.artifact_output import artifact_folders
            profile = dev.device_profile(state, device)
            settings = dev.deploy_settings(state, device, profile).changed(model=check_model_path(self.model))
            fp = dev.read_key(device, state, step="benchmark")   # a failure is recorded for Mojo (W1)
            from orchestrator.Core.config_manager import framework_mode
            out = dev.benchmark(state, device, profile, settings, fp, allow_untrusted=self.allow_untrusted,
                                consent=self._consent, output_dirs=artifact_folders(self.framework),
                                mode=framework_mode(config))
        except SettingsError as e:
            self.finished.emit(False, str(e))
            return
        except dev.DeviceRuntimeError as e:
            parts = [reasons.person_text(e.reason, address=device.host, port=device.port, error=e.error)
                     if e.reason in reasons.CODES else str(e)]
            parts += e.external_text[-10:]  # the board's own words, shown as plain text
            self.finished.emit(False, "\n".join(parts))
            return
        except Exception as e:  # noqa: BLE001 - a failure no step expected
            self.finished.emit(False, str(e))
            return
        lines = [f"{ui_text('device.bench.prompt')}: {out.get('prompt_ts') or '-'} t/s",
                 f"{ui_text('device.bench.gen')}: {out.get('gen_ts') or '-'} t/s",
                 f"{ui_text('device.bench.checks')}: " + ", ".join(f"{c['name']} {c['result']}"
                                                                   for c in out.get("checks", []))]
        # the board's temperature during the run, as the CLI and the Boards window show it (round 23 item 16)
        from orchestrator.runtime.device.thermal import from_dict, person_lines
        lines += person_lines(from_dict(out.get("temperature")))
        board = profile.get("BOARD_MODEL") or device.label
        lines.append(ui_text("device.bench.recorded", board=board, count=out["recorded_builds"])
                     if out.get("recorded_builds") else ui_text("device.bench.not_recorded"))
        self.finished.emit(bool(out.get("ok")), "\n".join(lines))


class DeploymentWindow(QWidget):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework = framework_manager
        
        # Access components safely
        self.target_manager = getattr(framework_manager, 'target_manager', None)
        self.secrets_manager = getattr(framework_manager, 'secrets_manager', None)
        self.history = input_history.for_config(getattr(framework_manager, "config", None))
        
        self.i18n = get_i18n()
        self.worker = None
        
        self._init_ui()
        self.refresh_data()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # --- 1. Artifact Selection ---
        grp_artifact = QGroupBox("1. Select Golden Artifact")
        layout_art = QVBoxLayout()
        self.list_artifacts = QListWidget()
        self.list_artifacts.setObjectName("list_deploy_artifacts")   # `llmcf deploy list-artifacts` (GUI parity)
        self.list_artifacts.currentItemChanged.connect(lambda *_: self._update_kv_auto())
        layout_art.addWidget(self.list_artifacts)
        # Save...: a build still in the framework's build folder goes to the output folder or another folder
        # (eighth round, answer 3); the person's save dialog, never by itself
        self.btn_save = QPushButton(ui_text("save.button").replace("&", "&&"))
        self.btn_save.setObjectName("btn_deploy_save")  # `llm-cli build save` by mouse (GUI parity)
        self.btn_save.clicked.connect(self._on_save)
        layout_art.addWidget(self.btn_save)
        self._save_workers = []
        self._init_source_button(layout_art)   # Other build or model file... (deploy package/run take any path)
        grp_artifact.setLayout(layout_art)
        layout.addWidget(grp_artifact)
        
        # --- 2. Target Profile Selection ---
        grp_target = QGroupBox("2. Select Target Hardware Profile")
        layout_group = QVBoxLayout()
        layout_target = QHBoxLayout()
        self.cb_profiles = QComboBox()
        # the containers of a Docker package depend on the profile (its hardware access): the table follows it
        self.cb_profiles.currentTextChanged.connect(lambda *_: self._fill_containers())
        layout_target.addWidget(QLabel("Profile:"))
        layout_target.addWidget(self.cb_profiles, 1)
        
        # Target Connection Info (Quick Edit); devices deployed to before are offered first. The person enters what
        # they know: an IP address or a host name such as myboard.local (N4).
        self.cb_device = QComboBox()
        self.cb_device.setEditable(True)
        self.cb_device.setInsertPolicy(QComboBox.NoInsert)
        self.cb_device.setMinimumWidth(160)
        self.cb_device.setAccessibleName(ui_text("device.address.label").rstrip(":"))
        self.cb_device.lineEdit().setPlaceholderText(ui_text("device.address.hint"))
        recent = self._recent("device")
        fill_recent(self.cb_device, recent, text=None if recent else "192.168.1.100")
        self.cb_device.editTextChanged.connect(lambda *_: self._update_kv_auto())
        layout_target.addWidget(QLabel(ui_text("device.address.label")))
        layout_target.addWidget(self.cb_device)
        
        self.txt_user = QLineEdit("root")
        self.txt_user.setPlaceholderText("User")
        self.txt_user.setFixedWidth(80)
        layout_target.addWidget(QLabel("User:"))
        layout_target.addWidget(self.txt_user)
        
        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh_data)
        layout_target.addWidget(btn_refresh)
        layout_group.addLayout(layout_target)
        self._init_connection_fields(layout_group)

        # The KV cache of the board's server (N7): automatic or the person's choice, and what it changes.
        layout_kv = QHBoxLayout()
        self.cb_kv = QComboBox()
        self.cb_kv.setAccessibleName(ui_text("device.kv.label").rstrip(":"))
        for kind in KV_CHOICES:
            self.cb_kv.addItem(ui_text(f"device.kv.{kind}") if kind != "auto" else "", kind)
        self._kv_presetting, self._kv_touched = False, False
        self.cb_kv.currentIndexChanged.connect(self._on_kv_changed)
        self.cb_kv.setToolTip(plain_tooltip(ui_text("device.kv.explain")))
        layout_kv.addWidget(QLabel(ui_text("device.kv.label")))
        layout_kv.addWidget(self.cb_kv, 1)
        layout_group.addLayout(layout_kv)
        self.lbl_kv = plain_label(ui_text("device.kv.explain"))
        layout_group.addWidget(self.lbl_kv)

        grp_target.setLayout(layout_group)
        layout.addWidget(grp_target)
        
        # --- 3. Docker Configuration ---
        self.grp_docker = QGroupBox("3. Docker Configuration")
        self.grp_docker.setObjectName("grp_deploy_docker")   # `llmcf deploy package --docker / --no-docker`
        self.grp_docker.setCheckable(True)
        self.grp_docker.setChecked(False)
        self.grp_docker.setTitle("Use Docker Containerization?")

        layout_docker = QVBoxLayout()
        # The containers the package carries (deployment_manager.package_containers, the CLI's --docker): each row
        # with its image and the hardware access of the profile; the ticked ones go into the package.
        self.table_docker = QTableWidget()
        self.table_docker.setObjectName("table_deploy_containers")
        self.table_docker.setColumnCount(3)
        self.table_docker.setHorizontalHeaderLabels([ui_text("deploy.containers.component"),
                                                     ui_text("deploy.containers.image"),
                                                     ui_text("deploy.containers.access")])
        self.table_docker.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table_docker.setToolTip(plain_tooltip(ui_text("deploy.containers.tip")))
        self.table_docker.setAccessibleName(ui_text("deploy.containers.component"))

        layout_docker.addWidget(self.table_docker)
        self.grp_docker.setLayout(layout_docker)
        layout.addWidget(self.grp_docker)
        self._init_board_bench(layout)
        
        # --- Progress Bar and the visible state of the deploy (uploading, waiting for the board) ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        self.lbl_status = plain_label("")
        self.lbl_status.setVisible(False)
        layout.addWidget(self.lbl_status)
        
        # --- 4. Action Buttons ---
        btn_layout = QHBoxLayout()
        
        self.btn_deploy = QPushButton("🚀 " + ui_text("main.deploy.run"))
        self.btn_deploy.setObjectName("btn_deploy_run")   # `llmcf deploy package` + `deploy run` in one step
        self.btn_deploy.clicked.connect(self._on_deploy)
        self.btn_deploy.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 10px;")

        btn_layout.addWidget(self.btn_deploy)
        self._init_package_buttons(btn_layout)
        self.btn_cancel = QPushButton(ui_text("device.wait.cancel"))
        self.btn_cancel.setObjectName("btn_deploy_cancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)
        self._update_kv_auto()

    # --- GUI parity (W1'): deploy run --port/--label/--wait, deploy package, deploy run PACKAGE_PATH ---

    def _init_connection_fields(self, layout_group):
        """The board's SSH port, the person's name for it and how long a dropped network is waited for: the CLI's
        `deploy run --port`, `--label` and `--wait` (N5)."""
        row = QHBoxLayout()
        self.spin_port = QSpinBox()
        self.spin_port.setObjectName("spin_deploy_port")
        self.spin_port.setRange(1, 65535)
        self.spin_port.setValue(22)
        self.spin_port.setAccessibleName(ui_text("deploy.port").rstrip(":"))
        self.spin_port.setToolTip(plain_tooltip(ui_text("deploy.port.tip")))
        row.addWidget(QLabel(ui_text("deploy.port")))
        row.addWidget(self.spin_port)
        self.txt_label = QLineEdit()
        self.txt_label.setObjectName("txt_deploy_label")
        self.txt_label.setMaxLength(64)
        self.txt_label.setPlaceholderText(ui_text("deploy.label.hint"))
        self.txt_label.setAccessibleName(ui_text("deploy.label").rstrip(":"))
        self.txt_label.setToolTip(plain_tooltip(ui_text("deploy.label.tip")))
        row.addWidget(QLabel(ui_text("deploy.label")))
        row.addWidget(self.txt_label, 1)
        self.spin_wait = QSpinBox()
        self.spin_wait.setObjectName("spin_deploy_wait")
        self.spin_wait.setRange(0, 86400)
        from orchestrator.Core.deployment_manager import DEPLOY_WAIT_SECONDS
        self.spin_wait.setValue(DEPLOY_WAIT_SECONDS)
        self.spin_wait.setSuffix(ui_text("deploy.wait.suffix"))
        self.spin_wait.setAccessibleName(ui_text("deploy.wait").rstrip(":"))
        self.spin_wait.setToolTip(plain_tooltip(ui_text("deploy.wait.tip")))
        row.addWidget(QLabel(ui_text("deploy.wait")))
        row.addWidget(self.spin_wait)
        layout_group.addLayout(row)

    def _init_package_buttons(self, btn_layout):
        """Create package only... (deploy package, no board) and Deploy existing package... (deploy run
        PACKAGE_PATH)."""
        from orchestrator.gui.host_key import HostKeyCheck
        self.btn_package = QPushButton(ui_text("deploy.package.button").replace("&", "&&"))
        self.btn_package.setObjectName("btn_deploy_package")
        self.btn_package.setToolTip(plain_tooltip(ui_text("deploy.package.tip")))
        self.btn_package.clicked.connect(self._on_package)
        btn_layout.addWidget(self.btn_package)
        self.btn_existing = QPushButton(ui_text("deploy.existing.button").replace("&", "&&"))
        self.btn_existing.setObjectName("btn_deploy_existing")
        self.btn_existing.setToolTip(plain_tooltip(ui_text("deploy.existing.tip")))
        self.btn_existing.clicked.connect(self._on_existing)
        btn_layout.addWidget(self.btn_existing)
        self.package_worker = None
        self.host_keys = HostKeyCheck(self, self.framework)

    def _init_source_button(self, layout_art):
        """Other build or model file...: `llmcf deploy package ARTIFACT` takes any path, not only a build the list
        names. A build folder or a model file the person picks joins the list (kept for this session) and is selected;
        Create package only... and Deploy to Target then take it like a listed build. The button's menu has two
        entries: the folder dialog and the file dialog."""
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu
        self._chosen_sources = []
        self.btn_add_source = QPushButton(ui_text("deploy.source.button").replace("&", "&&"))
        self.btn_add_source.setObjectName("btn_deploy_add_source")
        self.btn_add_source.setToolTip(plain_tooltip(ui_text("deploy.source.tip")))
        menu = QMenu(self.btn_add_source)
        self.act_add_folder = QAction(ui_text("deploy.source.folder").replace("&", "&&"), self)
        self.act_add_folder.setObjectName("act_deploy_add_folder")
        self.act_add_folder.triggered.connect(lambda _=False: self._add_source(folder=True))
        menu.addAction(self.act_add_folder)
        self.act_add_model = QAction(ui_text("deploy.source.model").replace("&", "&&"), self)
        self.act_add_model.setObjectName("act_deploy_add_model")
        self.act_add_model.triggered.connect(lambda _=False: self._add_source(folder=False))
        menu.addAction(self.act_add_model)
        self.btn_add_source.setMenu(menu)
        layout_art.addWidget(self.btn_add_source)

    def _add_source(self, folder: bool) -> None:
        """The folder or the file dialog, opened at the output folder; the chosen path joins the list, selected.
        The model is not checked here: packaging finds it and says why when there is none (the KV-cache choice reads
        its size, as for a listed build)."""
        from orchestrator.Core.artifact_output import output_folder
        from orchestrator.Core.deployment_manager import MODEL_SUFFIXES
        out = output_folder(self.framework)
        start = str(out) if out.is_dir() else ""
        if folder:
            path = choose_directory(self, ui_text("deploy.source.folder_title"), start)
        else:
            patterns = " ".join(f"*{suffix}" for suffix in MODEL_SUFFIXES)
            path = choose_file(self, ui_text("deploy.source.model_title"), start,
                               ui_text("deploy.package.models", patterns=patterns))
        if not path:
            return
        chosen = str(Path(path))
        if chosen not in self._chosen_sources:
            self._chosen_sources.append(chosen)
        self.list_artifacts.setCurrentItem(self._list_source(chosen))

    def _list_source(self, chosen: str):
        """The list entry of a path the person added (one per path): its name and "chosen by you", the full path as
        its tooltip."""
        for row in range(self.list_artifacts.count()):
            item = self.list_artifacts.item(row)
            if item.data(Qt.UserRole + 1) == "chosen" and item.data(Qt.UserRole) == chosen:
                return item
        item = QListWidgetItem(f"📂 {Path(chosen).name} ({ui_text('deploy.source.chosen')})")
        item.setData(Qt.UserRole, chosen)
        item.setData(Qt.UserRole + 1, "chosen")
        item.setToolTip(plain_tooltip(chosen))
        self.list_artifacts.addItem(item)
        return item

    def _set_busy(self, busy: bool, text: str = "") -> None:
        """One step at a time: the three action buttons wait while a host key is read, a package is made or a deploy
        runs; the busy line says what runs."""
        for button in (self.btn_deploy, self.btn_package, self.btn_existing):
            button.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(busy and bool(text))

    def _connection(self):
        """The board's address, user, port, name and wait as the person entered them, checked as `deploy run`
        checks them; None after a plain-text warning."""
        from types import SimpleNamespace
        from orchestrator.remote.address import valid_host, valid_user
        from orchestrator.runtime.scan_steps import label_problem
        host = self.cb_device.currentText().strip()
        user = self.txt_user.text().strip()
        label = " ".join(self.txt_label.text().split()) or None
        problem = ("deploy.bad_address" if not valid_host(host) else
                   "deploy.bad_user" if not valid_user(user, allow_root=True) else
                   "deploy.label.bad" if label_problem(label) else "")
        if problem:
            plain_box(self, "warning", "Warning", ui_text(problem))
            return None
        return SimpleNamespace(host=host, user=user, port=int(self.spin_port.value()), label=label,
                               wait=int(self.spin_wait.value()))

    def _trust_host(self, conn, then) -> None:
        """Reads the board's host key off the GUI thread, asks on first use ("No" is the default) and calls
        then(trusted)."""
        self._set_busy(True, ui_text("deploy.reading_key", address=conn.host, port=conn.port))

        def done(trusted: bool) -> None:
            self._set_busy(False)
            conn.key = self.host_keys.key if trusted else None   # the deploy reaches exactly this key
            then(trusted)

        self.host_keys.start(conn.host, conn.port, done, step="deploy")

    def _ask_password(self, user: str, host: str):
        """The one-time password (never stored): (password or None, the person did not cancel)."""
        pwd, ok = QInputDialog.getText(self, ui_text("deploy.password.title"),
                                       ui_text("deploy.password.text", user=user, address=host), QLineEdit.Password)
        return (pwd or None), bool(ok)

    def _start_deploy(self, conn, password, *, artifact_path=None, profile_name="", docker_config=None,
                      package=None) -> None:
        """Starts the deploy worker: `docker_config` as the worker takes it ({"use_docker", "containers"})."""
        self._set_busy(True)
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.worker = DeploymentWorker(
            self.framework, artifact_path, profile_name, docker_config or {}, conn.host, conn.user, password,
            port=conn.port, label=conn.label, wait_seconds=conn.wait, package=package, key=getattr(conn, "key", None))
        if package is None:
            self.worker.kv_cache, self.worker.kv_setting = self.kv_choice()
        self.worker.progress.connect(self._update_progress)
        self.worker.event.connect(self._on_event)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _on_existing(self):
        """Deploy existing package...: a ZIP made earlier (here or with `llmcf deploy package`) to the board named
        above, with its host-key question, the one-time password, the port, the name and the wait."""
        from orchestrator.Core.artifact_output import output_folder
        out = output_folder(self.framework)
        path = choose_file(self, ui_text("deploy.existing.title"), str(out) if out.is_dir() else "",
                           ui_text("deploy.existing.filter"))
        if not path:
            return
        conn = self._connection()
        if conn is None:
            return
        package = Path(path)

        def trusted(ok: bool) -> None:
            if not ok:
                return
            if self.history:
                self.history.add("device", conn.host)  # a trusted, reachable device
                self.reload_history()
            password, asked = self._ask_password(conn.user, conn.host)
            if asked:
                self._start_deploy(conn, password, package=package)

        self._trust_host(conn, trusted)

    def _package_folder(self, name: str):
        """The folder dialog at the output folder, as for every golden artifact: OK keeps the output folder (where
        `llmcf deploy package` writes), or the person picks another folder (refused where a golden artifact may not
        go). None when the person cancels or the folder is refused."""
        from orchestrator.Core.artifact_output import DestinationRefused, check_destination, output_folder, refusal
        from orchestrator.gui.artifact_save import choose_folder
        out = output_folder(self.framework)
        try:
            if not out.exists() and not refusal(self.framework, out):
                out.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        chosen = choose_folder(self, ui_text("deploy.package.folder_title", name=name), str(out))
        if not chosen:
            plain_box(self, "information", ui_text("deploy.package.title"), ui_text("deploy.package.no_folder"))
            return None
        try:
            if Path(chosen).resolve() == out.resolve():
                return out
        except OSError:
            pass
        try:
            return check_destination(self.framework, chosen)
        except DestinationRefused as e:
            plain_box(self, "warning", ui_text("deploy.package.title"), "\n\n".join(p for p in (str(e), e.next) if p))
            return None

    def _on_package(self):
        """Create package only...: the selected build (or a model file the person picks) for the chosen profile, with
        the Docker choice and the KV-cache type of this window; no board is contacted (air-gapped boards)."""
        from orchestrator.Core.artifact_output import output_folder
        from orchestrator.Core.deployment_manager import MODEL_SUFFIXES
        item = self.list_artifacts.currentItem()
        if item is not None:
            source = Path(item.data(Qt.UserRole))
        else:
            out = output_folder(self.framework)
            patterns = " ".join(f"*{suffix}" for suffix in MODEL_SUFFIXES)
            path = choose_file(self, ui_text("deploy.package.choose_model"), str(out) if out.is_dir() else "",
                               ui_text("deploy.package.models", patterns=patterns))
            if not path:
                return
            source = Path(path)
        profile_name = self.cb_profiles.currentText().strip()
        if not profile_name:
            plain_box(self, "warning", ui_text("deploy.package.title"), ui_text("deploy.package.no_profile"))
            return
        docker = self.docker_choice()
        if docker is None:
            return
        folder = self._package_folder(source.name)
        if folder is None:
            return
        self._set_busy(True, ui_text("deploy.package.running", name=source.name, folder=str(folder)))
        self.btn_cancel.setVisible(True)
        self.btn_cancel.setEnabled(True)
        self.package_worker = PackageWorker(self.framework, source, profile_name, docker["use_docker"],
                                            folder, kv_cache=self.kv_choice()[0], containers=docker.get("containers"))
        self.package_worker.finished.connect(self._on_packaged)
        self.package_worker.start()

    def _on_packaged(self, ok: bool, text: str):
        self._set_busy(False)
        self.btn_cancel.setVisible(False)
        plain_box(self, "information" if ok else "warning", ui_text("deploy.package.title"), text)

    # --- the containers of a package with Docker images (DECISIONS round 23, item 6) ---

    def _fill_containers(self) -> None:
        """The table lists the containers a package for the chosen profile carries (package_containers, as `llmcf
        deploy package --docker` packs them): the component, ticked unless the person unticked it before, its image
        and the hardware access the profile's target gives it on the board. Values from the settings and target.yml
        are shown as plain text."""
        from orchestrator.Core.deployment_manager import package_containers
        if not hasattr(self, "table_docker"):
            return
        unticked = {self.table_docker.item(row, 0).data(Qt.UserRole) for row in range(self.table_docker.rowCount())
                    if self.table_docker.item(row, 0) is not None
                    and self.table_docker.item(row, 0).checkState() != Qt.Checked}
        containers = package_containers(self.framework, self.cb_profiles.currentText().strip())
        self.table_docker.setRowCount(0)
        for container in containers:
            self._add_docker_row(container, ticked=container.key not in unticked)

    def _add_docker_row(self, container, ticked: bool = True):
        """One container of the package: its component (ticked: it goes into the package), image and hardware
        access."""
        row = self.table_docker.rowCount()
        self.table_docker.insertRow(row)
        component = QTableWidgetItem(ui_text(f"deploy.containers.{container.key}"))
        component.setData(Qt.UserRole, container.key)
        component.setFlags((component.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
        component.setCheckState(Qt.Checked if ticked else Qt.Unchecked)
        self.table_docker.setItem(row, 0, component)
        access = " ".join(container.flags) or ui_text("deploy.containers.access_none")
        for column, text in ((1, container.image), (2, access)):
            item = QTableWidgetItem(text)   # QTableWidgetItem shows plain text, never rich text
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setToolTip(plain_tooltip(text))
            self.table_docker.setItem(row, column, item)

    def container_choice(self):
        """The component keys of the ticked containers, or None when every one is ticked (what `llmcf deploy package
        --docker` packs)."""
        keys, ticked = [], []
        for row in range(self.table_docker.rowCount()):
            item = self.table_docker.item(row, 0)
            if item is None:
                continue
            keys.append(item.data(Qt.UserRole))
            if item.checkState() == Qt.Checked:
                ticked.append(item.data(Qt.UserRole))
        return None if ticked == keys else ticked

    def docker_choice(self):
        """The Docker part of this window's package: {"use_docker": the group's tick, "containers": the ticked
        containers (None: every one)}; None after a plain warning when Docker is on but no container is ticked."""
        if not self.grp_docker.isChecked():
            return {"use_docker": False}
        chosen = self.container_choice()
        if chosen is not None and not chosen:
            plain_box(self, "warning", ui_text("deploy.package.title"), ui_text("deploy.containers.none"))
            return None
        return {"use_docker": True, "containers": chosen}

    # --- the KV cache (N7) ---

    def _board(self):
        """(device id, stored profile, deploy settings) of the board the address names, when it was probed before."""
        from orchestrator.runtime.device.devices import DeviceNotFound, resolve_device
        from orchestrator.runtime.device.settings import DeploySettings, SettingsError
        from orchestrator.runtime.profile import ProfileError, ProfileStore
        state = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        address = self.cb_device.currentText().strip()
        if not state or not address:
            return None, None, DeploySettings()
        store = ProfileStore(Path(state))
        try:
            device_id = resolve_device(store, address)
            profile = store.load_device(device_id)
            settings = DeploySettings.from_dict(store.load_deploy_settings(device_id))
        except (DeviceNotFound, ProfileError, SettingsError, ValueError, OSError):
            return None, None, DeploySettings()
        return device_id, profile, settings

    def _model_bytes(self):
        from orchestrator.Core.deployment_manager import resolve_model_artifact
        item = self.list_artifacts.currentItem()
        if item is None:
            return None
        try:
            return resolve_model_artifact(Path(item.data(Qt.UserRole))).stat().st_size
        except (OSError, ValueError, TypeError):
            return None

    def _update_kv_auto(self):
        """The automatic choice for this board and model. When the address names another known board, its saved
        choice is preselected; a choice the person made here is never replaced by a change of the model."""
        if not hasattr(self, "cb_kv"):
            return
        device_id, profile, settings = self._board()
        auto = settings.changed(kv_cache=None).kv_choice(profile, self._model_bytes())
        why = ui_text(f"device.kv.why_{auto.why}", free=auto.free_gib)
        self.cb_kv.setItemText(0, ui_text("device.kv.auto", kind=auto.kind, why=why))
        if device_id != getattr(self, "_kv_device", ""):
            self._kv_device = device_id
            self._kv_presetting = True
            try:
                self.cb_kv.setCurrentIndex(max(0, self.cb_kv.findData(settings.kv_cache or "auto")))
            finally:
                self._kv_presetting = False
            self._kv_touched = False  # what is shown now is the board's saved choice, not one made here
        self._kv_auto = auto.kind

    def _on_kv_changed(self, *_):
        if not self._kv_presetting:
            self._kv_touched = True

    def kv_choice(self):
        """(type for the package's start command, what the board's deploy settings get: None for automatic, or
        KV_KEEP when the person did not choose here, so a saved choice the window could not show is never
        replaced)."""
        kind = self.cb_kv.currentData() or "auto"
        setting = (None if kind == "auto" else kind) if self._kv_touched else KV_KEEP
        return (getattr(self, "_kv_auto", "f16") if kind == "auto" else kind), setting

    def refresh_data(self):
        # Artifacts
        self.list_artifacts.clear()

        # Every finished build writes build_info.json; list builds, not raw folders or archives: in the output
        # folder, in the framework's build folder (not saved yet, earlier builds) and where the person saved them.
        from orchestrator.Core.artifact_output import BUILDS, list_artifacts
        try:
            listed = list_artifacts(self.framework)
        except Exception:  # noqa: BLE001 - a folder that cannot be read lists nothing
            listed = []
        for item in listed:
            build = item.info
            if not build.get("primary"):
                continue
            label = " · ".join(str(x) for x in (
                build.get("model") or Path(str(build.get("model_source", "?"))).name,
                str(build.get("format", "?")).upper(),
                build.get("quantization") or "default",
                build.get("board") or build.get("target") or "?",
            ))
            label += tested_suffix(item.path)
            if item.where == BUILDS:
                label += f" ({ui_text('save.not_saved')})"
            list_item = QListWidgetItem(f"📦 {label}")
            list_item.setData(Qt.UserRole, build["path"])
            list_item.setData(Qt.UserRole + 1, item.where)
            list_item.setToolTip(f"{build['path']}\n{build.get('primary')}\n{build.get('created', '')}")
            self.list_artifacts.addItem(list_item)
        for chosen in getattr(self, "_chosen_sources", []):   # a build folder or model file the person added
            self._list_source(chosen)

        # Profiles (target ids): keep the selection, else preselect the most recently used target
        previous = self.cb_profiles.currentText()
        self.cb_profiles.clear()
        if self.target_manager:
            profiles = self.target_manager.list_hardware_profiles()
            if profiles:
                self.cb_profiles.addItems(profiles)
            else:
                self.cb_profiles.addItem("Generic Profile (Default)")
        else:
            self.cb_profiles.addItem("Error: TargetManager not loaded")
        select_recent(self.cb_profiles, ([previous] if previous else []) + self._recent("target"))
        self._fill_containers()

    def _recent(self, field):
        return self.history.get(field) if self.history else []

    def _on_save(self):
        """Save... for the selected build: only one still in the framework's build folder; the person picks the folder
        (the output folder is preselected) in the operating system's own dialog."""
        from orchestrator.Core.artifact_output import BUILDS
        from orchestrator.gui import artifact_save
        from orchestrator.gui.plain import plain_box
        item = self.list_artifacts.currentItem()
        if item is None or item.data(Qt.UserRole + 1) != BUILDS:
            plain_box(self, "information", ui_text("save.button").rstrip("…"), ui_text("save.only_build_folder"))
            return
        worker = artifact_save.ask_and_save(self, self.framework, Path(item.data(Qt.UserRole)), self._on_saved)
        if worker is not None:
            self._save_workers.append(worker)
            worker.finished.connect(lambda w=worker: self._save_workers.remove(w) if w in self._save_workers else None)

    def _on_saved(self, ok: bool, text: str):
        from orchestrator.gui.plain import plain_box
        from orchestrator.utils.localization import ui_text
        plain_box(self, "information" if ok else "warning", ui_text("save.button").rstrip("…"), text)
        self.refresh_data()

    def reload_history(self):
        """Refills the recent devices (after a deployment or after the history was cleared)."""
        fill_recent(self.cb_device, self._recent("device"), text=self.cb_device.currentText())

    def _on_deploy(self):
        # 1. Validation
        artifact_item = self.list_artifacts.currentItem()
        if not artifact_item:
            QMessageBox.warning(self, "Warning", "Please select an artifact first.")
            return
        
        profile_name = self.cb_profiles.currentText()
        if not profile_name:
            QMessageBox.warning(self, "Warning", "Please select a Hardware Profile.")
            return

        artifact_path = Path(artifact_item.data(Qt.UserRole))
        docker_config = self.docker_choice()   # read before any question: a choice of no container stops here
        if docker_config is None:
            return
        conn = self._connection()   # address, user, port, name and wait (deploy run --port/--label/--wait)
        if conn is None:
            return

        # 2. Trust the device (host key fingerprint, first use only; read off the GUI thread) and log in. The
        #    password is needed only until the framework's SSH key is installed; it is used once and never stored.
        def trusted(ok: bool) -> None:
            if not ok:
                return
            if self.history:
                self.history.add("device", conn.host)  # a trusted, reachable device
                if self.target_manager and profile_name in self.target_manager.list_hardware_profiles():
                    self.history.add("target", profile_name)
                self.reload_history()
            password, asked = self._ask_password(conn.user, conn.host)
            if not asked:
                return
            # 3. Docker: the group's tick and the containers ticked in its table (docker_choice), packed by the
            #    one deploy step as `llmcf deploy package --docker` packs them (package_docker_config)
            # 4. Start Worker
            self._start_deploy(conn, password, artifact_path=artifact_path, profile_name=profile_name,
                               docker_config=docker_config)

        self._trust_host(conn, trusted)

    def _on_cancel(self):
        """Stops the deploy or the packaging that runs (one runs at a time; stopping a finished one changes
        nothing)."""
        for worker in (self.worker, self.package_worker):
            if worker is not None:
                self.btn_cancel.setEnabled(False)
                worker.cancel()

    def _update_progress(self, msg):
        self.btn_deploy.setText(msg)

    def _on_event(self, event):
        """The deploy's visible state: the upload's progress, or the wait for the board with its time (N5)."""
        target = self.worker.target_ip if self.worker is not None else ""
        self.lbl_status.setText(event_text(event, target))
        self.lbl_status.setVisible(True)
        if event.kind == "uploading" and event.total:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(int(event.done * 1000 / event.total))
        else:
            self.progress_bar.setRange(0, 0)

    def _on_finished(self, success, msg):
        self._set_busy(False)
        self.btn_deploy.setText("🚀 " + ui_text("main.deploy.run"))
        self.btn_cancel.setVisible(False)
        
        if success:
            worker = self.worker
            if worker is not None and worker.device_id and worker.model_name:
                # after a deploy, the benchmark on the board is the next step: the board and the model are filled in
                self._refresh_boards(select=worker.device_id)
                self.txt_bench_model.setText(f"models/{worker.model_name}")
                msg = f"{msg}\n\n{ui_text('device.bench.after_deploy')}"
            plain_box(self, "information", "Success", msg)
        else:
            plain_box(self, "critical", "Error", msg)

    # --- the benchmark on a board (seventh round 2', N3) ---

    def _init_board_bench(self, layout):
        """Models built for a board are measured on the board: its runtime's checks and a short llama-bench run."""
        group = QGroupBox(ui_text("device.bench.title"))
        box = QVBoxLayout()
        box.addWidget(plain_label(ui_text("device.bench.intro")))
        row = QHBoxLayout()
        self.cb_bench_board = QComboBox()
        self.cb_bench_board.setAccessibleName(ui_text("device.bench.device").rstrip(":"))
        self.cb_bench_board.currentIndexChanged.connect(lambda *_: self._on_bench_board())
        row.addWidget(QLabel(ui_text("device.bench.device")))
        row.addWidget(self.cb_bench_board, 1)
        self.txt_bench_model = QLineEdit()
        self.txt_bench_model.setPlaceholderText(ui_text("device.bench.model_hint"))
        self.txt_bench_model.setAccessibleName(ui_text("device.bench.model").rstrip(":"))
        row.addWidget(QLabel(ui_text("device.bench.model")))
        row.addWidget(self.txt_bench_model, 2)
        self.btn_bench = QPushButton(ui_text("device.bench.start"))
        self.btn_bench.clicked.connect(self._on_bench)
        row.addWidget(self.btn_bench)
        # W1': Mojo's check of the chosen board, as Tools > Mojo Health Check... and `llmcf mojo --board` check it
        self.btn_check_board = QPushButton(ui_text("mojo.deploy.check").replace("&", "&&"))
        self.btn_check_board.setObjectName("btn_check_board")
        self.btn_check_board.setToolTip(plain_tooltip(ui_text("mojo.deploy.check_tip")))
        self.btn_check_board.clicked.connect(self._on_check_board)
        row.addWidget(self.btn_check_board)
        box.addLayout(row)
        self.lbl_bench = plain_label("")
        box.addWidget(self.lbl_bench)
        group.setLayout(box)
        layout.addWidget(group)
        self.bench_worker = None
        self.board_check = None   # mojo_window.BoardCheck, made at the first check (Mojo loads when first used)
        self._refresh_boards()

    def _refresh_boards(self, select=None):
        """The paired boards by the names the person gave them."""
        from orchestrator.runtime.device.trusted import DeviceStoreError, TrustedDevices
        state = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        try:
            devices = [d for d in TrustedDevices(Path(state)).list() if d.paired] if state else []
        except (DeviceStoreError, OSError, ValueError):
            devices = []
        keep = select or self.cb_bench_board.currentData()
        self.cb_bench_board.blockSignals(True)
        self.cb_bench_board.clear()
        for device in devices:
            self.cb_bench_board.addItem(device.label, device.device_id)
        self.cb_bench_board.blockSignals(False)
        found = self.cb_bench_board.findData(keep) if keep else -1
        self.cb_bench_board.setCurrentIndex(found if found >= 0 else (0 if devices else -1))
        self.btn_bench.setEnabled(bool(devices))
        self.btn_check_board.setEnabled(bool(devices))
        self.lbl_bench.setText("" if devices else ui_text("device.bench.none"))
        self._on_bench_board()

    def _on_bench_board(self):
        """Prefills the model the board's deploy settings name, unless the person typed one."""
        from orchestrator.runtime.device.settings import DeploySettings, SettingsError
        from orchestrator.runtime.profile import ProfileStore
        state = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        device_id = self.cb_bench_board.currentData()
        if not state or not device_id or self.txt_bench_model.text().strip():
            return
        try:
            model = DeploySettings.from_dict(ProfileStore(Path(state)).load_deploy_settings(device_id)).model
        except (SettingsError, ValueError, OSError):
            model = None
        if model:
            self.txt_bench_model.setText(model)

    def _on_bench(self):
        device_id = self.cb_bench_board.currentData()
        model = self.txt_bench_model.text().strip()
        if not device_id or not model:
            plain_box(self, "warning", ui_text("device.bench.title"), ui_text("device.bench.missing"))
            return
        # asked before anything runs, No preselected: a model that is not a catalog model loads outside a sandbox
        allowed = ask(self, ui_text("device.bench.title"),
                      ui_text("device.bench.untrusted", board=self.cb_bench_board.currentText(), model=model))
        self.btn_bench.setEnabled(False)
        self.lbl_bench.setText(ui_text("device.bench.running"))
        self.bench_worker = BoardBenchWorker(self.framework, device_id, model, allowed)
        self.bench_worker.finished.connect(self._on_bench_done)
        self.bench_worker.start()

    def _on_check_board(self):
        """[Check this board]: Mojo's check of the chosen board off the GUI thread (Core/mojo/boards.check_board: one
        connection to its SSH port, no login); the result shows under the row, a problem also in a message."""
        device_id = self.cb_bench_board.currentData()
        if not device_id:
            plain_box(self, "warning", ui_text("mojo.deploy.check"), ui_text("mojo.deploy.choose"))
            return
        if self.board_check is None:
            from orchestrator.gui.mojo_window import BoardCheck
            self.board_check = BoardCheck(self, self.framework, self._on_board_checked)
        label = self.cb_bench_board.currentText()
        if self.board_check.start(device_id, label):
            self.btn_check_board.setEnabled(False)
            self.lbl_bench.setText(ui_text("mojo.boards.checking", label=label))

    def _on_board_checked(self, device_id, label, text, finding):
        self.btn_check_board.setEnabled(self.cb_bench_board.count() > 0)
        self.lbl_bench.setText(text)
        if finding is None or finding.severity.value not in ("ok", "info"):
            plain_box(self, "warning", ui_text("mojo.deploy.check"), text)

    def _on_bench_done(self, ok, text):
        self.btn_bench.setEnabled(True)
        self.lbl_bench.setText(text)
        if ok:
            self.refresh_data()  # the build list shows "tested on" now
        else:
            plain_box(self, "warning", ui_text("device.bench.title"), text)
