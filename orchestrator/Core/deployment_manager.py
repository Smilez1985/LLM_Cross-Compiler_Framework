#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deployment Manager (v2.3.1)

Features:
- Package Generation (the model, its provenance and deploy.sh; optionally the inference container image).
- Air-Gap Image Export (Docker Tarballs).
- Centralized Security Validation.

The knowledge base stays on this computer, where it serves Ditto: a package carries no vector database and no Qdrant
sidecar (nothing on the device used one).
"""

import os
import re
import shlex
import time
import shutil
import tempfile
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, List, Dict

from orchestrator.remote.transport import PARAMIKO_AVAILABLE   # true while paramiko imports (tested on first use)
from orchestrator.remote import session as remote_session
from orchestrator.remote.transport import ParamikoTransport
from orchestrator.utils.logging import get_logger

from orchestrator.Core.builder import BUILD_MANIFEST, MODEL_FILE_SUFFIXES, is_staging_folder
from orchestrator.remote.address import USER_PATTERN, valid_host, valid_user
from orchestrator.remote.text import LINE_CHARS, OUTPUT_LINES, keep_tail
from orchestrator.utils import ssh_trust
from orchestrator.utils.helpers import sha256_fileobj, write_sha256sums
from orchestrator.utils.helpers import sha256_file  # deployment_manager.sha256_file, a public name
from orchestrator.utils.next_steps import WithSteps

# A local image name: a package takes it only once it was built on this computer (image_problem); no framework image
# is published or pulled.
DEFAULT_INFERENCE_IMAGE = "llm-framework/inference:local"
# The device runtime's start script, relative to the device user's home (orchestrator/runtime/device/launcher.py).
DEVICE_START_SCRIPT = ".local/share/llm-framework/llama.cpp/current/llama-server.sh"
SSH_USER_PATTERN = USER_PATTERN   # the one account check (orchestrator/remote/address.py), kept under its old name
MODEL_SUFFIXES = MODEL_FILE_SUFFIXES
# An image reference pinned by its content digest (name@sha256:<64 hex>): the only form pulled for a package.
DIGEST_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}$", re.ASCII)
# Lines of the device's output kept for the result (the rest is in the log), and their length (remote/text.py).
REMOTE_OUTPUT_LINES = OUTPUT_LINES
REMOTE_LINE_CHARS = LINE_CHARS
# A deploy whose network drops (a Wi-Fi board) waits this long for the board to answer again (N5), trying every
# WAIT_INTERVAL seconds, and continues at most MAX_RESUMES times: the numbers of the one board session
# (orchestrator/remote/session.py), under the names the deploy has always had.
DEPLOY_WAIT_SECONDS = remote_session.WAIT_SECONDS
WAIT_INTERVAL = remote_session.WAIT_INTERVAL
MAX_RESUMES = remote_session.MAX_RESUMES
UPLOAD_CHUNK = remote_session.UPLOAD_CHUNK
INSTALL_TIMEOUT = remote_session.JOB_TIMEOUT   # seconds deploy.sh may run on the board
INSTALL_POLL = remote_session.JOB_POLL
# Files of an installation that runs on the board on its own, in the deploy's private folder there.
INSTALL_STARTED = remote_session.JOB_STARTED
INSTALL_STATUS = remote_session.JOB_STATUS
INSTALL_LOG = remote_session.JOB_LOG
# Packages with Docker images from earlier versions started a Qdrant sidecar on the board (`docker run --name
# qdrant-sidecar --restart unless-stopped`). Nothing uses it any more; after a deploy the framework looks whether it is
# still there and tells the person, and never removes it (round 5 f: the person removes what they can see).
OLD_SIDECAR = "qdrant-sidecar"
OLD_SIDECAR_SECONDS = 20   # the look comes after a finished deploy: a Docker that does not answer never holds it


def old_sidecar_check(seconds: int = OLD_SIDECAR_SECONDS) -> str:
    """The board command that looks for the old sidecar: `docker ps`, bounded with `timeout SECONDS` where the board's
    `timeout` takes that form (coreutils, BusyBox 1.30 and later), as before where it does not (no `timeout`, or an
    older BusyBox one that wants `-t SECONDS`)."""
    look = f"docker ps -a --filter name={OLD_SIDECAR} --format '{{{{.Names}}}}|{{{{.Status}}}}' 2>/dev/null"
    return f"if timeout 5 true >/dev/null 2>&1; then timeout {int(seconds)} {look}; else {look}; fi"


OLD_SIDECAR_CHECK = old_sidecar_check()
# What the OpenSSH fallback (no paramiko) says when it was given a password: it logs in with the framework key only.
NO_PASSWORD_NOTE = ("The password was not used: OpenSSH runs in BatchMode and never asks for one, and logging in "
                    "with a password or pairing (installing the framework's key on the board) needs the 'paramiko' "
                    "package. The deploy logged in with the framework's key only.")
OLD_SIDECAR_NOTE = ("The board still has the container {name} ({state}) from an earlier package with Docker images. "
                    "Nothing uses it any more: the knowledge base stays on this computer. The framework does not "
                    "remove it. To remove it, run on the board: docker rm -f {name} (the data folder data/qdrant of "
                    "that old package stays until you delete it).")


def old_sidecar_state(answer: str) -> str:
    """'running' or 'stopped' when the board's `docker ps` answer (OLD_SIDECAR_CHECK) lists the old Qdrant sidecar by
    its exact name, else "" (no Docker, no access to it, or no such container)."""
    for line in str(answer or "").splitlines()[:50]:
        name, _, status = line.strip().partition("|")
        if name == OLD_SIDECAR:
            return "running" if status.startswith("Up") else "stopped"
    return ""


class DeployError(WithSteps, ValueError):
    """A package or a deployment that cannot be made; the message says why (for the person or the calling AI), `code`
    is a reason code of the tool registry and `next` the step that helps."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


@dataclass
class DeployResult:
    """What a deployment did: success, the exit status of deploy.sh on the device (None when it did not run to its
    end), the last lines the device printed (device text: untrusted), what state the device is in afterwards and,
    when it failed, the reason."""
    success: bool
    exit_status: Optional[int] = None
    output: List[str] = field(default_factory=list)
    device_state: str = "unknown"   # ok | failed | unknown
    reason: str = ""
    code: str = ""                  # the SSH reason code when the board could not be reached or logged in to (N6)
    resumed: int = 0                # how often the deploy continued after the network dropped (N5)
    waited_s: float = 0.0           # seconds it waited for the board
    notes: List[str] = field(default_factory=list)   # the framework's own sentences for the person (no device text)
    old_sidecar: str = ""           # "running" | "stopped": the old Qdrant sidecar is still on the board


# What a running deploy reports (deploy_package's on_event): "uploading" (bytes on the board of the package's total),
# "waiting" (the network dropped: seconds waited of the limit, and whether the upload or the installation waits),
# "back" (the board answers again: the upload continues at `done`, or with step "install" the installation is followed
# again), "installing". It is the board session's event (orchestrator/remote/session.py RemoteEvent).
DeployEvent = remote_session.RemoteEvent


def image_problem(image: str, client: Any) -> Optional[str]:
    """Why `image` cannot go into a deployment package (None: it can). An image is taken only when it is pinned by its
    digest (name@sha256:...), or when it was built on this computer: it exists locally and no registry digest is
    recorded for it, so it was never pulled. A tag alone names whatever the registry serves today."""
    if DIGEST_REFERENCE.fullmatch(image or ""):
        return None
    if client is not None:
        try:
            local = client.images.get(image)
        except Exception:  # noqa: BLE001 - not here (or no Docker): not built here either
            local = None
        if local is not None:
            digests = (getattr(local, "attrs", None) or {}).get("RepoDigests")
            if isinstance(digests, list) and not digests:
                return None
    return (f"the image {image} is neither pinned by its digest nor built on this computer; packages take only "
            "image@sha256:<digest> references or images built here")


def _primary_name(info: Dict[str, Any]) -> Optional[str]:
    primary = info.get("primary")
    return primary if isinstance(primary, str) and primary and primary == Path(primary).name else None


def list_builds(output_dir: Path) -> List[Dict[str, Any]]:
    """Finished builds below output_dir, found through their build manifest (newest first).
    Symlinked subfolders are followed (output trees moved to another disk), loops are skipped.
    The build engine's staging folders are skipped: a build is staged there until it has completed. So is the output
    folder's trash: a built model moved there is no build any more until the person restores it."""
    from orchestrator.Core.trash import TRASH_FOLDER
    builds, seen = [], set()
    for root, dirs, files in os.walk(output_dir, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if not is_staging_folder(d) and d != TRASH_FOLDER]
        if BUILD_MANIFEST not in files:
            continue
        try:
            info = json.loads((Path(root) / BUILD_MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(info, dict):
            info["primary"] = _primary_name(info)
            info["path"] = str(root)
            builds.append(info)
    return sorted(builds, key=lambda b: str(b.get("created", "")), reverse=True)


def resolve_model_artifact(artifact_path: Path) -> Path:
    """The model file to deploy: the file itself, or the primary model of a build directory.
    Archives are refused; the build directory next to them holds the same files plus the manifest."""
    path = Path(artifact_path)
    if path.is_file():
        if not path.name.lower().endswith(MODEL_SUFFIXES):
            raise ValueError(f"'{path.name}' is not a model file ({', '.join(MODEL_SUFFIXES)}); select the build instead.")
        return path
    if not path.is_dir():
        raise FileNotFoundError(f"Artifact not found: {path}")
    manifest = path / BUILD_MANIFEST
    if manifest.is_file():
        try:
            info = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        info = info if isinstance(info, dict) else {}
        if info.get("multi_file"):
            raise ValueError("This build produced several weight files (or weights that need their config and "
                             "tokenizer files); deploying multi-file models is not supported yet.")
        primary = _primary_name(info)
        if primary:
            candidate = (path / primary).resolve()
            if candidate.parent == path.resolve() and candidate.is_file():
                return candidate
    models = [f for f in path.iterdir() if f.is_file() and f.name.lower().endswith(MODEL_SUFFIXES)]
    if not models:
        raise ValueError(f"No model file found in {path}.")
    return max(models, key=lambda f: f.stat().st_size)


# --- a package without a board (`llmcf deploy package`, the Deployment tab's "Create package only...") -------------

def package_docker_config(docker: bool) -> Dict[str, Any]:
    """The Docker part of a package: whether the inference container image goes into it (air-gap use) and, then, the
    image the package names (the package itself takes image_inference_runtime from the settings)."""
    config: Dict[str, Any] = {"use_docker": bool(docker)}
    if docker:
        config["image_name"] = DEFAULT_INFERENCE_IMAGE
    return config


def package_folder(framework: Any) -> Path:
    """The folder a package is written to when the caller names none: the configured output folder (setting output_dir,
    relative paths below the installation), as `llmcf deploy package` and the Deployment tab use it."""
    from orchestrator.Core.build_plan import folder
    return folder(framework, "output_dir", "output")


def resolve_package_source(framework: Any, artifact: Any, output_dir: Path) -> Path:
    """The build or model file a package is made from: an absolute path or one relative to the working folder as it
    is, else a folder as `llmcf deploy list-artifacts` names it (relative to the output folder, then to the
    framework's build folder, where builds wait to be saved). A path that exists nowhere is returned as below the
    output folder, so the refusal names it."""
    given = Path(str(artifact)).expanduser()
    path = given.resolve() if given.is_absolute() or given.exists() else Path(output_dir) / str(artifact)
    if not path.exists():
        from orchestrator.Core.artifact_output import build_folder
        waiting = build_folder(framework) / str(artifact)
        path = waiting if waiting.exists() else path
    return path


def make_package(manager: Any, framework: Any, artifact: Any, profile: str, docker: bool, output_dir: Path, *,
                 kv_cache: Optional[str] = None, cancelled: Optional[Callable[[], bool]] = None) -> Path:
    """The deployment ZIP for a board this computer need not reach (`llmcf deploy package` and the GUI's "Create
    package only..."): the model, its provenance, deploy.sh and checksums, with `docker` also the inference image
    (air-gap). Written to `output_dir`; raises DeployError with the reason and the next step."""
    extra: Dict[str, Any] = {}
    if cancelled is not None:
        extra["cancelled"] = cancelled
    if kv_cache:
        extra["kv_cache"] = kv_cache
    return manager.create_package(resolve_package_source(framework, artifact, output_dir), profile,
                                  package_docker_config(docker), Path(output_dir), **extra)


def package_model_name(package: Path) -> Optional[str]:
    """The GGUF model a package's deploy.sh copies to models/ on the board (for the benchmark there): the model file at
    the package's top level, None when the package carries Docker images (the model then goes into the container),
    has no GGUF model or cannot be read. Reads only the ZIP's list of names."""
    try:
        with zipfile.ZipFile(package) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile, ValueError):
        return None
    if any(name.startswith("images/") for name in names):
        return None
    models = [name for name in names if "/" not in name and name.lower().endswith(".gguf")]
    return models[0] if len(models) == 1 else None


class DeploymentManager:
    def __init__(self, framework_manager=None):
        self.logger = get_logger("DeploymentManager")
        self.framework = framework_manager
        self._clock, self._sleep = time.monotonic, time.sleep  # the waiting for a board (tests replace them)
        # why create_deployment_package returned None; kept for its older callers only (it is shared by every caller
        # of this manager): the framework's own callers take the reason with the call (make_package raises it, and
        # person_steps.deploy_build returns it as package_error)
        self.last_package_error: Optional[DeployError] = None

    # --- PACKAGE GENERATION ---

    def create_deployment_package(self, artifact_path: Path, profile_name: str, docker_config: Dict, output_dir: Path,
                                  kv_cache: Optional[str] = None) -> Optional[Path]:
        """The package (see create_package), or None when it cannot be made (the reason is logged and kept in
        `last_package_error`, so a caller can show it with its next step). Kept for older callers: the framework's
        own callers use make_package (it raises the reason) or person_steps.deploy_build (it returns it)."""
        self.last_package_error = None
        try:
            return self.create_package(artifact_path, profile_name, docker_config, output_dir, kv_cache=kv_cache)
        except DeployError as e:
            self.logger.error(str(e))
            self.last_package_error = e
            return None

    def create_package(self, artifact_path: Path, profile_name: str, docker_config: Dict, output_dir: Path, *,
                       cancelled: Optional[Callable[[], bool]] = None, kv_cache: Optional[str] = None) -> Path:
        """
        Creates the ZIP package for the device: the model file (and the build's manifest and model card), the static
        user configs, the generated deploy.sh and checksums; with docker_config["use_docker"] also the Docker images
        (air-gap), each pinned by its digest or built on this computer (image_problem). Raises DeployError with the
        reason. `cancelled()` is checked between the steps; the zip is written under a temporary name and renamed
        when complete, so a cancelled or failed package leaves no file. `kv_cache`: the KV-cache type of the board's
        deploy settings (eighth round N7), named in the start command deploy.sh prints for a GGUF model.
        """
        stop = cancelled or (lambda: False)
        build_dir = Path(artifact_path) if Path(artifact_path).is_dir() else None
        try:
            artifact_path = resolve_model_artifact(Path(artifact_path))
        except (OSError, ValueError) as e:
            raise DeployError(str(e), code="invalid_value") from None

        # Hardware Flags vom TargetManager holen
        target_mgr = self.framework.get_component("target_manager")
        docker_flags = []
        if target_mgr and profile_name:
            docker_flags = target_mgr.get_docker_flags_for_profile(profile_name)
        flags_str = " ".join(docker_flags)

        # Images (names come from the central config), checked before anything is copied
        use_docker = docker_config.get("use_docker", False)
        cfg = self.framework.config
        inference_image = cfg.get("image_inference_runtime", DEFAULT_INFERENCE_IMAGE)
        images = [inference_image] if use_docker else []
        client = self.framework.get_component("docker_client") if images else None
        for image in images:
            problem = image_problem(image, client)
            if problem:
                raise DeployError(problem[0].upper() + problem[1:] + ".", code="not_supported",
                                  next="Set image_inference_runtime to an image@sha256:<digest> "
                                       "reference, build the image on this computer, or package without Docker "
                                       "images.")

        def check_stop():
            if stop():
                raise DeployError("Packaging was cancelled; no package was written.", code="failed")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # A. Model file, plus the build's provenance (manifest, model card) when deploying a build
            shutil.copy2(artifact_path, temp_path / artifact_path.name)
            if build_dir:
                for extra in (BUILD_MANIFEST, "Model_Card.md"):
                    if (build_dir / extra).is_file():
                        shutil.copy2(build_dir / extra, temp_path / extra)
            check_stop()

            # B. Configs (User Profiles, Prompts)
            self._bundle_user_configs(temp_path / "data" / "configs")

            # C. Docker Images
            if use_docker:
                load_names = self._export_docker_images(images, temp_path / "images", client)
                check_stop()
            else:
                load_names = {}

            # D. Script Generation (Deploy + Compose Logic)
            try:
                script_content = self._generate_deploy_script(
                    artifact_path.name, flags_str, use_docker, inference_image, load_names, kv_cache=kv_cache)
            except ValueError as e:
                raise DeployError(str(e), code="not_supported") from None

            deploy_script_path = temp_path / "deploy.sh"
            with open(deploy_script_path, "w", newline='\n') as f:
                f.write(script_content)
            # deploy.sh is run on the board and holds no secret.
            os.chmod(deploy_script_path, 0o755)  # nosec B103

            # E. Checksums & Zip
            self._generate_checksums(temp_path, temp_path / "checksums.sha256")
            check_stop()

            timestamp = int(time.time())
            pkg_name = f"deploy_{artifact_path.stem}_{timestamp}.zip"
            pkg_path = output_dir / pkg_name

            # Output Directory sicherstellen
            output_dir.mkdir(parents=True, exist_ok=True)

            partial = output_dir / f".{pkg_name}.part"
            try:
                with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, _, files in os.walk(temp_path):
                        for file in files:
                            check_stop()
                            p = Path(root) / file
                            zipf.write(p, p.relative_to(temp_path))
                os.replace(partial, pkg_path)
            except BaseException:
                try:
                    partial.unlink()
                except OSError:
                    pass
                raise

            self.logger.info(f"Deployment Package created: {pkg_path}")
            return pkg_path

    def _bundle_user_configs(self, dest_dir: Path):
        """Kopiert statische Konfigurationen, aber KEINE Datenbanken."""
        # The resolved config folder (LLM_FRAMEWORK_CONFIG_DIR, the installation's configs/ or the per-user
        # folder); never a folder relative to the working directory, which may be any project.
        src_dir = getattr(getattr(self.framework, "config", None), "config_dir", None)
        if src_dir is None or not Path(src_dir).is_dir():
            self.logger.info("No config folder to bundle user profiles, voice templates or prompts from.")
            return
        src_dir = Path(src_dir)

        # Nur diese Ordner sind für das Target sicher/relevant
        safe_folders = ["user_profiles", "voice_templates", "prompts"]
        
        for folder in safe_folders:
            src = src_dir / folder
            if src.exists():
                shutil.copytree(src, dest_dir / folder, dirs_exist_ok=True)

    @staticmethod
    def _load_name(image: str) -> str:
        """The name a packed image carries in its tarball, so `docker load` restores it on the device: the image's own
        tag, or for a digest-pinned image (docker keeps no tag for an image pulled by its digest, and `docker load`
        does not restore registry digests) its repository with the tag lcf-<first 12 hex characters of the digest>."""
        if not DIGEST_REFERENCE.fullmatch(image):
            return image
        name, digest = image.split("@sha256:", 1)
        repo = name.rsplit(":", 1)[0] if ":" in name.rsplit("/", 1)[-1] else name
        return f"{repo}:lcf-{digest[:12]}"

    def _export_docker_images(self, images: List[str], dest_dir: Path, client: Any = None) -> Dict[str, str]:
        """Saves each image into the package under the name deploy.sh starts it by (_load_name) and returns
        {image: that name}. A digest-pinned image that is not here is pulled by its digest; an image is never pulled
        by a tag. Any failure stops the package (DeployError): a package without its images would fail on the
        air-gapped device."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        client = client or self.framework.get_component("docker_client")
        if not client:
            raise DeployError("Docker is not available on this computer, so the images cannot be packaged.",
                              code="needs_person", next="Start Docker, or package without Docker images.")

        names: Dict[str, str] = {}
        for img_name in images:
            sanitized = img_name.replace("/", "_").replace(":", "_").replace("@", "_")
            tar_path = dest_dir / f"{sanitized}.tar"

            self.logger.info(f"Exporting Docker Image: {img_name}...")
            try:
                try:
                    img = client.images.get(img_name)
                except Exception:  # noqa: BLE001 - not here: only a digest-pinned image is fetched
                    if not DIGEST_REFERENCE.fullmatch(img_name):
                        raise
                    client.images.pull(img_name)
                    img = client.images.get(img_name)
                name = self._load_name(img_name)
                if name != img_name:  # digest-pinned: the content is the digest's; the local tag only names it
                    repo, tag = name.rsplit(":", 1)
                    img.tag(repo, tag=tag)
                    img.reload()
                with open(tar_path, "wb") as f:
                    for chunk in img.save(named=name):
                        f.write(chunk)
                names[img_name] = name
            except Exception as e:
                raise DeployError(f"The image {img_name} could not be packaged: {e}", code="failed",
                                  next="Check Docker and the image reference, then package again.") from None
        return names

    def _generate_checksums(self, directory: Path, output_file: Path):
        # LF line endings: sha256sum < coreutils 9.0 (Debian 11, Ubuntu 22.04, Git for Windows) treats CR as part of the name.
        # POSIX separators: the file is verified with sha256sum on the Linux target, also when built on Windows. Both
        # come from write_sha256sums; the checksum file itself (created first, in the same folder) is left out.
        def entries():
            for root, _, files in os.walk(directory):
                for file in files:
                    p = Path(root) / file
                    if p.name == output_file.name: continue
                    yield sha256_file(p), p.relative_to(directory)
        write_sha256sums(entries(), output_file)

    def _generate_deploy_script(self, artifact, flags, use_docker,
                                inference_image: str = DEFAULT_INFERENCE_IMAGE,
                                load_names: Optional[Dict[str, str]] = None, *,
                                kv_cache: Optional[str] = None) -> str:
        """deploy.sh of a package. The packed images are started by the names their tarballs carry (`load_names`,
        from _export_docker_images) with `--pull never`: the device runs exactly what the package holds, and a device
        where an image is missing fails instead of pulling a look-alike from a registry."""
        names = load_names or {}
        script = [
            "#!/bin/sh",
            "set -e",
            "echo '[1/3] Checking Integrity...'",
            "sha256sum -c checksums.sha256",
            ""
        ]

        if use_docker:
            script.extend([
                "echo '[2/3] Loading Images (Air-Gap Support)...'",
                "for img in images/*.tar; do if [ -e \"$img\" ]; then docker load -i \"$img\"; fi; done",
                *[f"# {pinned} is packed as {name}" for pinned, name in sorted(names.items()) if pinned != name],
                "",
                "echo '[3/3] Starting Stack...'",
                "docker network create llm-net || true",
                ""
            ])
            node = [
                "# Inference Node (with Hardware Access)",
                "docker run -d --name llm-node \\",
                "  --network llm-net \\",
            ]
            if flags:
                node.append(f"  {flags} \\")  # Hardware Flags (NPU/GPU) from the target profile
            node.extend([
                "  -v \"$(pwd)/data:/app/data\" \\",
                "  --restart unless-stopped \\",
                "  --pull never \\",
                f"  {shlex.quote(names.get(inference_image, inference_image))}",
                ""
            ])
            script.extend(node)
        elif str(artifact).lower().endswith(MODEL_SUFFIXES):
            # A model file is data, not a program: install it and say how to run it.
            q = shlex.quote(str(artifact))
            script.extend([
                "echo '[2/3] Installing model...'",
                'MODEL_DIR="${MODEL_DIR:-$HOME/models}"',
                'mkdir -p "$MODEL_DIR"',
                f'cp {q} "$MODEL_DIR/"',
                f'echo "[3/3] Model installed: $MODEL_DIR/"{q}',
            ])
            if str(artifact).lower().endswith(".gguf"):
                from orchestrator.runtime.server_policy import kv_cache_args
                # The framework's self-built runtime (llm-cli device runtime deploy), never a llama-server found on
                # PATH: its start script keeps the server on 127.0.0.1 with the key file and a cleared environment.
                # The KV-cache options are checked words of the shared policy (f16, q8_0, q4_0).
                kv = " ".join(kv_cache_args(kv_cache))
                script.extend([
                    f'RUNTIME="$HOME/{DEVICE_START_SCRIPT}"',
                    'if [ -f "$RUNTIME" ]; then',
                    f'  echo "Start it with: sh $RUNTIME start $MODEL_DIR/"{q}' + (f'" {kv}"' if kv else ""),
                    "  echo '(llama-server -m <model> --host 127.0.0.1 --port 8080 --api-key-file "
                    "~/.config/llm-framework/llama-server.key: loopback only, every request needs that key)'",
                    "else",
                    "  echo 'No self-built llama-server on this board yet: deploy it from your computer with "
                    "llm-cli device runtime build <device> and llm-cli device runtime deploy <device>.'",
                    "fi",
                ])
            elif str(artifact).lower().endswith(".rkllm"):
                script.append("echo 'Run it with the RKLLM runtime (librkllmrt, rkllm_server_demo or RKLLama) on the RK3588/RK3576.'")
        else:
            # Only model files are deployed without Docker; never make an arbitrary artifact executable.
            raise ValueError(f"Refusing to deploy '{artifact}': not a model file ({', '.join(MODEL_SUFFIXES)}).")

        script.append("echo 'Deployment Complete. System is ready.'")
        return "\n".join(script)

    # --- EXECUTION (SSH / SCP) ---
    def _state_dir(self) -> Path:
        config = getattr(self.framework, "config", None)
        return Path(getattr(config, "user_state_dir", None) or Path.home() / ".llm-framework")

    def trust_store(self):
        """The framework's known_hosts this deploy logs in with (remote/trust.py TrustStore, read-only)."""
        from orchestrator.remote.trust import TrustStore
        return TrustStore(self._state_dir())

    def host_key_status(self, target_ip: str, port: int = 22):
        """('known'|'unknown'|'changed', key) for the device - shown to the user before trusting it."""
        from orchestrator.remote.address import Endpoint
        check = self.trust_store().check(Endpoint(target_ip, port))
        return check.status, check.key

    def trust_host(self, target_ip: str, key, port: int = 22, *, person: Any = None) -> None:
        """Deprecated (DECISIONS round 22, P5): a thin delegate to orchestrator/remote/pairing.py trust_presented_key,
        the one writer of a confirmed host key, which stores it in this deploy's known_hosts under its lock and writes
        the trust's audit row in the same step. Only the person's: `person` is the pairing.PersonChannel of the
        calling channel (the CLI or the GUI, with its audit log). No module an AI can reach imports pairing
        (tests/test_remote_trust.py), so nothing an AI runs holds one, and no code of the framework calls this method.
        Without it nothing is trusted (PermissionError): the old primitive without an audit row is gone."""
        import warnings
        from orchestrator.remote.address import Endpoint, valid_port
        warnings.warn("DeploymentManager.trust_host is deprecated: the person's channels trust a host key with "
                      "orchestrator.remote.pairing.trust_presented_key", DeprecationWarning, stacklevel=2)
        trust = getattr(person, "trust_presented_key", None)
        if not callable(trust):
            raise PermissionError("Only the person trusts a board's host key, in the GUI or the CLI: pass the "
                                  "channel's orchestrator.remote.pairing.PersonChannel; nothing was trusted.")
        if not valid_host(target_ip) or not valid_port(port):
            raise ValueError("the board's address must be a host name or an IP address with a port from 1 to 65535")
        trust(self.trust_store(), Endpoint(target_ip, port), key)

    def deploy_package(self, package: Path, host: str, user: str, *, port: int = 22, **options: Any) -> DeployResult:
        """Uploads the package and runs its deploy.sh on the device (_deploy_package says how), and records for Mojo
        what this contact with the board found (W1): a failure with an SSH reason code is kept with the trusted board
        at this address; a deploy that reached the board and logged in (it succeeded, or deploy.sh ran there and
        failed) ends a recorded problem. Mojo reads that record instead of contacting the board itself."""
        result = self._deploy_package(package, host, user, port=port, **options)
        self._note_reach(host, port, result)
        return result

    def _note_reach(self, host: str, port: int, result: DeployResult) -> None:
        from orchestrator.remote import reasons
        from orchestrator.remote.address import Endpoint
        from orchestrator.runtime.device.trusted import record_reach
        # deploy.sh ran on the board and ended with its own exit status: the board was reached and logged in to (255 is
        # also what the ssh command itself returns when it cannot connect, so it says nothing)
        ran_there = result.exit_status is not None and result.exit_status != 255
        code = result.code if result.code in reasons.REASONS else ("" if result.success or ran_there else None)
        if code is None:
            return  # a failure before the board was reached (the package, a refused address): nothing to record
        record_reach(self._state_dir(), "deploy", code, endpoint=Endpoint(host, port))

    def _deploy_package(self, package: Path, host: str, user: str, *, port: int = 22, password: Optional[str] = None,
                        pair: bool = True, expect_fingerprint: Optional[str] = None,
                        expect_sha256: Optional[str] = None, on_client: Optional[Callable[[Any], None]] = None,
                        cancelled: Optional[Callable[[], bool]] = None, wait_seconds: float = DEPLOY_WAIT_SECONDS,
                        on_event: Optional[Callable[[DeployEvent], None]] = None) -> DeployResult:
        """
        Uploads the package and runs its deploy.sh on the device; returns what happened with the reason instead of a
        bare bool. `host` is the board's address or host name. The device's host key must be trusted already. With
        `expect_fingerprint` the key the device presents now must be exactly that one (the fingerprint a person was
        shown), with `expect_sha256` the package must still have that hash (re-hashed right before the upload, from
        the same open file that is uploaded). `on_client` gets each SSH session (an object with close()) so a cancel
        can close it; `cancelled()` is checked between the steps and while waiting. Child processes never inherit this
        process's stdin (over MCP stdio that is the protocol stream).

        A board that cannot be reached or logged in to at the start fails at once with its SSH reason code (N6:
        result.code). When the network drops later (a Wi-Fi board), the deploy waits up to `wait_seconds` for the board
        to answer on its SSH port again (`on_event` gets a "waiting" event about every WAIT_INTERVAL seconds), logs in
        again with the same host key check and continues: the upload resumes where it stopped once the sha256 of what
        arrived matches, and deploy.sh, which runs on the board on its own, is started once and never again.
        """
        stop = cancelled or (lambda: False)
        # A leading '-' would be parsed by ssh/scp as an option (e.g. -oProxyCommand=...).
        if not valid_host(host):
            return DeployResult(False, reason=f"Security/Format Error: Invalid address or host name: "
                                              f"{str(host)[:64]!r} (an IPv6 address is written without brackets, "
                                              "e.g. fe80::1)")
        if not valid_user(user, allow_root=True):
            return DeployResult(False, reason=f"Security/Format Error: Invalid SSH user name: {user!r}")
        if not Path(package).is_file():
            return DeployResult(False, reason=f"The package {Path(package).name} does not exist.")

        if not self._check_conn(host, port):
            from orchestrator.runtime.device import reasons
            down = reasons.check_port(host, port) or reasons.HOST_DOWN
            return DeployResult(False, code=down, reason=f"Target {host} not reachable on port {port} ({down}).")

        self.logger.info(f"Deploying to {host}...")
        if PARAMIKO_AVAILABLE:
            return self._deploy_paramiko(Path(package), host, user, password, port, pair, expect_fingerprint,
                                         expect_sha256, on_client, stop, wait_seconds, on_event)
        if expect_fingerprint:
            # OpenSSH checks the key against known_hosts only; the fingerprint shown cannot be compared without
            # paramiko, so a deploy bound to one is refused rather than sent unchecked.
            return DeployResult(False, reason="Checking the device's host key against the fingerprint shown needs the "
                                              "'paramiko' package; nothing was deployed.")
        return self._deploy_system(Path(package), host, user, port, expect_sha256, stop, password=password,
                                   on_client=on_client, wait_seconds=wait_seconds, on_event=on_event)

    def _check_conn(self, ip, port=22) -> bool:
        """Whether the board's SSH port opens (reasons.check_port says why not)."""
        from orchestrator.runtime.device import reasons
        return reasons.check_port(ip, port, timeout=2) is None

    @staticmethod
    def _remote_install_cmd(target_dir: str, package_name: str) -> str:
        d, pkg = shlex.quote(target_dir), shlex.quote(package_name)
        # sh reads the script instead of executing it from /tmp, which many boards mount noexec (and deploy.sh is
        # plain POSIX sh, so boards without bash, such as Alpine, run it too).
        return f"cd {d} && unzip -o {pkg} && sh deploy.sh"

    @staticmethod
    def _install_job(package_name: str) -> str:
        """The installation as a job on the board: unzip and deploy.sh (read by sh, as in _remote_install_cmd) write
        into INSTALL_LOG."""
        pkg = shlex.quote(package_name)
        return f"unzip -o {pkg} > {INSTALL_LOG} 2>&1 && sh deploy.sh >> {INSTALL_LOG} 2>&1"

    @staticmethod
    def _remote_start_cmd(target_dir: str, package_name: str) -> str:
        """Starts the installation on the board on its own (nohup, nothing attached to the SSH session), so a network
        drop never stops deploy.sh halfway: its exit status goes into INSTALL_STATUS. The marker INSTALL_STARTED is
        made first, and a folder that has it is never started again, also when this command reaches the board twice
        (RemoteSession.detached_command)."""
        return remote_session.RemoteSession.detached_command(target_dir, DeploymentManager._install_job(package_name))

    # A private, unpredictable folder on the device (a fixed /tmp path could be pre-created by another user).
    REMOTE_MKTEMP = "mktemp -d /tmp/llm_deploy.XXXXXXXX"

    @staticmethod
    def _keep(lines: List[str], line: str) -> None:
        keep_tail(lines, line, max_lines=REMOTE_OUTPUT_LINES, max_chars=REMOTE_LINE_CHARS)

    @staticmethod
    def _run(ssh, command: str) -> tuple:
        """(exit status, stdout, stderr) of a command on the board: one `sh -c '...'` with a time limit and an output
        cap (remote/transport.py ParamikoTransport.run)."""
        result = ParamikoTransport(ssh).run(command)
        return result.status, result.out, result.err

    def _old_sidecar(self, answer: str, fingerprint: str = "") -> Dict[str, Any]:
        """What a successful deploy reports about the old Qdrant sidecar on the board (OLD_SIDECAR_CHECK's answer):
        a note for the person and its state, kept with the board's last deploy for Mojo (by the device ID of the host
        key it presented). The container is never stopped or removed here."""
        state = old_sidecar_state(answer)
        if fingerprint:
            try:
                from orchestrator.runtime.device.trusted import TrustedDevices
                from orchestrator.runtime.profile import device_id_for_host_key
                TrustedDevices(self._state_dir()).note_board(device_id_for_host_key(fingerprint), old_sidecar=state)
            except Exception:  # noqa: BLE001 - bookkeeping never fails a deploy
                pass
        if not state:
            return {}
        return {"old_sidecar": state, "notes": [OLD_SIDECAR_NOTE.format(name=OLD_SIDECAR, state=state)]}

    @staticmethod
    def _connection_lost(ssh) -> bool:
        """Whether the SSH session itself broke (the network dropped), not a command or a file on the board."""
        return not ParamikoTransport(ssh).alive()

    def _deploy_paramiko(self, artifact: Path, ip: str, user: str, pw: Optional[str], port: int, pair: bool,
                         expect_fingerprint: Optional[str] = None, expect_sha256: Optional[str] = None,
                         on_client: Optional[Callable[[Any], None]] = None,
                         stop: Callable[[], bool] = lambda: False, wait_seconds: float = DEPLOY_WAIT_SECONDS,
                         on_event: Optional[Callable[[DeployEvent], None]] = None) -> DeployResult:
        # The package is opened once: the bytes hashed are the bytes uploaded, and a changed package is refused before
        # anything reaches the device.
        with open(artifact, "rb") as fh:
            digest = sha256_fileobj(fh)
            if expect_sha256 and digest != expect_sha256:
                return DeployResult(False, reason=f"The package {artifact.name} changed after it was shown (its "
                                                  "sha256 differs); nothing was deployed.")
            size = fh.tell()
            fh.seek(0)
            session = _BoardSession(self, ip, user, pw, port, expect_fingerprint, on_client, stop, wait_seconds,
                                    on_event)
            return session.deploy(fh, artifact.name, size, digest, pair)

    def _deploy_system(self, artifact: Path, ip: str, user: str, port: int = 22,
                       expect_sha256: Optional[str] = None, stop: Callable[[], bool] = lambda: False, *,
                       password: Optional[str] = None, on_client: Optional[Callable[[Any], None]] = None,
                       wait_seconds: float = DEPLOY_WAIT_SECONDS,
                       on_event: Optional[Callable[[DeployEvent], None]] = None) -> DeployResult:
        """Fallback to OpenSSH where paramiko cannot be imported: the same board session as the paramiko deploy
        (_BoardSession over remote/session.py RemoteSession), over the second transport, this computer's OpenSSH
        client (remote/transport.py SystemSshTransport, decision A5). So it waits for a board whose network dropped
        (`wait_seconds`, `on_event`), resumes the upload, starts deploy.sh once on the board on its own and follows it
        again after a drop, and hands each session to `on_client` (its close() ends the running OpenSSH call). Every
        OpenSSH call takes the framework key and known_hosts only, never asks and never takes a new key, and never
        gets this process's stdin (over MCP stdio it is the protocol stream). The package is opened once: the bytes
        hashed are the bytes uploaded, and the board checks their sha256 before they are installed. OpenSSH in
        BatchMode never asks for a password, so a one-time password (and pairing with it) needs paramiko: the deploy
        logs in with the framework key only and its result says so (NO_PASSWORD_NOTE) instead of dropping it."""
        with open(artifact, "rb") as fh:
            digest = sha256_fileobj(fh)
            if expect_sha256 and digest != expect_sha256:
                return DeployResult(False, reason=f"The package {artifact.name} changed after it was shown (its "
                                                  "sha256 differs); nothing was deployed.")
            size = fh.tell()
            fh.seek(0)
            session = _BoardSession(self, ip, user, None, port, None, on_client, stop, wait_seconds, on_event,
                                    openssh=self._openssh_transport)
            result = session.deploy(fh, artifact.name, size, digest, pair=False)
        if password:
            result.notes.append(NO_PASSWORD_NOTE)
            if not result.success:
                result.reason = f"{result.reason} {NO_PASSWORD_NOTE}".strip()
        return result

    @staticmethod
    def _openssh_transport(endpoint, user: str, state_dir: Path):
        """The OpenSSH transport of one login (a seam: tests replace it)."""
        from orchestrator.remote.transport import SystemSshTransport
        return SystemSshTransport(endpoint, user, state_dir)


class _BoardSession:
    """One deploy (DeploymentManager.deploy_package): the package and deploy.sh's job on a board session
    (orchestrator/remote/session.py RemoteSession), which logs in (every login checks the host key as the first one
    did), makes the private folder, uploads with resume, waits for the board (N5) and follows the installation that
    runs on the board on its own; over paramiko, or with `openssh` (the fallback without paramiko) over this
    computer's OpenSSH client. This adapter keeps the deploy's results as they were: the reasons, the device state
    and the device's output lines (its words, never in a reason)."""

    def __init__(self, manager: DeploymentManager, host: str, user: str, password: Optional[str], port: int,
                 expect_fingerprint: Optional[str], on_client: Optional[Callable[[Any], None]],
                 stop: Callable[[], bool], wait_seconds: float, on_event: Optional[Callable[[DeployEvent], None]],
                 openssh: Optional[Callable[..., Any]] = None):
        self.manager, self.logger = manager, manager.logger
        self.openssh = openssh   # the OpenSSH transport's factory (the fallback without paramiko), else paramiko
        self.host, self.user, self.password, self.port = host, user, password, port
        self.expect_fingerprint, self.on_client, self.wait_seconds = expect_fingerprint, on_client, wait_seconds
        self.lines: List[str] = []
        self.on_event = on_event or (lambda _e: None)
        self.cancel = remote_session.CancelToken(stop)
        self.session: Optional[remote_session.RemoteSession] = None

    def _session(self, pair: bool) -> remote_session.RemoteSession:
        """The board session of this deploy: the person's one-time password when one was given (pairing installs the
        framework key with it), else the framework key only. manager._clock, manager._sleep and manager._check_conn
        are read at each use (tests set and patch them)."""
        from orchestrator.remote.address import Endpoint
        manager = self.manager
        credentials = (remote_session.OneTimePassword(self.password, install_key=pair) if self.password else
                       remote_session.KeyOnly())
        return remote_session.RemoteSession(
            Endpoint(self.host, self.port), self.user, manager._state_dir(), credentials=credentials,
            expect_fingerprint=self.expect_fingerprint, cancel=self.cancel, clock=lambda: manager._clock(),
            sleep=lambda seconds: manager._sleep(seconds), reachable=lambda h, p: manager._check_conn(h, p),
            on_event=self.on_event, on_client=self.on_client, wait_seconds=self.wait_seconds, logger=manager.logger,
            openssh=self.openssh)

    @property
    def state_after_stop(self) -> str:
        """"unknown" once deploy.sh's start command is on its way to the board, else "ok"."""
        return "unknown" if self.session.detached_sent else "ok"

    def result(self, success: bool, **values: Any) -> DeployResult:
        values.setdefault("output", self.lines)
        return DeployResult(success, resumed=self.session.resumed, waited_s=round(self.session.waited, 1), **values)

    def stopped(self) -> DeployResult:
        return self.result(False, device_state=self.state_after_stop,
                           reason=("The deploy was cancelled; deploy.sh runs on the board on its own and is never "
                                   "stopped halfway, so the device state is unknown."
                                   if self.state_after_stop == "unknown" else
                                   "The deploy was cancelled before deploy.sh ran; nothing was installed."))

    def failed(self, failure: remote_session.SessionFailure) -> DeployResult:
        """The deploy's result of a session step that could not be done, in the words the deploy has always used."""
        kind = failure.kind
        for line in failure.output:
            if kind == "private_dir":
                self.logger.error(f"[REMOTE] {line.strip()}")
            DeploymentManager._keep(self.lines, line)
        if kind in ("key_read", "not_trusted", "key_changed", "login", "needs_paramiko"):
            return self.result(False, code=failure.reason, reason=str(failure))
        if kind == "other_key":
            return self.result(False, code=failure.reason, reason=f"{failure}; nothing was deployed.")
        if kind == "wait":
            return self.result(False, code=failure.reason, device_state=failure.state,
                               reason=f"{failure}; " + ("the installation on the board may still have finished."
                                                        if failure.step == "install" else "deploy.sh did not run."))
        texts = {
            "private_dir": ("ok", "Could not create a folder on the device (its answer is in the device output)."),
            "rename": ("ok", "The package arrived, but could not be renamed on the device."),
            "upload_damaged": ("ok", "The package arrived damaged twice (its sha256 on the device differs); "
                                     "deploy.sh did not run."),
            "upload_failed": ("ok", str(failure)),
            "folder_gone": ("ok", "The board answers again, but the upload folder is gone (the board restarted); "
                                  "deploy.sh did not run. Deploy again."),
            "not_started": ("ok", "The installation could not be started on the device (its answer is in the device "
                                  "output)."),
            "cannot_follow": ("unknown", "The board answers again, but the installation cannot be followed (its answer "
                                         "is in the device output). It may have started before the connection "
                                         "dropped, so the device state is unknown."),
            "job_gone": ("unknown", "The board restarted during the installation (its upload folder is gone), so the "
                                    "device state is unknown. Deploy again."),
            "job_timeout": ("unknown", f"deploy.sh did not finish on the device within {INSTALL_TIMEOUT} s."),
            "job_lost": (failure.state, f"The installation on {self.host} could not be followed: {failure.error}"),
        }
        state, reason = texts[kind]
        return self.result(False, device_state=state, reason=reason)

    # --- the steps ---

    def deploy(self, fh, name: str, size: int, sha256: str, pair: bool) -> DeployResult:
        self.session = self._session(pair)
        try:
            return self._deploy(fh, name, size, sha256)
        except remote_session.Stopped:
            return self.stopped()
        except remote_session.SessionFailure as failure:
            return self.failed(failure)
        except Exception as e:  # noqa: BLE001 - a step that failed in a way no step expected
            if self.cancel.cancelled:
                return self.stopped()
            return self.result(False, device_state=self.state_after_stop, reason=f"SSH Deployment Error: {e}")
        finally:
            self.session.close()

    def _deploy(self, fh, name: str, size: int, sha256: str) -> DeployResult:
        session = self.session
        session.open()
        if session.pair():
            self.logger.info("Framework SSH key installed; later deployments need no password.")
        target_dir = session.private_dir()
        if self.cancel.cancelled:
            raise remote_session.Stopped()
        session.upload(fh, f"{target_dir}/{name}", size, sha256)
        if self.cancel.cancelled:
            return self.result(False, reason="The deploy was cancelled after the upload; deploy.sh did not run.",
                               device_state="ok")
        self.logger.info("Installing on target...")
        self.on_event(DeployEvent("installing", done=size, total=size))
        return self._install(name, target_dir)

    def _install(self, name: str, target_dir: str) -> DeployResult:
        """Starts the installation once (DeploymentManager._remote_start_cmd) and waits for its exit status; after a
        network drop the session waits for the board, logs in again and keeps waiting for the same installation. Once
        the start command is on its way, the installation may run on the board even when its answer is lost: from then
        on the device state is unknown until the board says how deploy.sh ended."""
        session = self.session
        d = shlex.quote(target_dir)
        code = session.run_detached(target_dir, DeploymentManager._install_job(name), timeout=INSTALL_TIMEOUT,
                                    poll=INSTALL_POLL)
        log = session.run(f"tail -n {REMOTE_OUTPUT_LINES} {d}/{INSTALL_LOG}").out
        for line in log.splitlines():
            self.logger.info(f"[REMOTE] {line.strip()}")
            DeploymentManager._keep(self.lines, line)
        session.run(f"rm -rf {d}")
        if code == 0:
            self.logger.info("Remote deployment successful.")
            try:
                answer = session.run(OLD_SIDECAR_CHECK).out
            except Exception:  # noqa: BLE001 - only a look; the deploy itself succeeded
                answer = ""
            return self.result(True, exit_status=0, device_state="ok",
                               **self.manager._old_sidecar(answer, session.fingerprint))
        return self.result(False, exit_status=code, device_state="failed",
                           reason=f"deploy.sh failed on the device (exit {code}).")
