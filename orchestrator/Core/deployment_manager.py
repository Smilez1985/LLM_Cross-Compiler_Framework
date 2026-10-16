#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deployment Manager (v2.3.1)
DIREKTIVE: Goldstandard Deployment with Slim-RAG Strategy.

Features:
- Package Generation with Multi-Container Support.
- Air-Gap Image Export (Docker Tarballs).
- Slim-RAG: Target gets empty DB structure, learns locally.
- Centralized Security Validation.
"""

import importlib.util
import os
import re
import shlex
import time
import subprocess
import hashlib
import shutil
import tempfile
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, List, Dict

# paramiko is used through orchestrator.utils.ssh_trust; without it deployments fall back to OpenSSH.
PARAMIKO_AVAILABLE = importlib.util.find_spec("paramiko") is not None

from orchestrator.utils.logging import get_logger

from orchestrator.Core.builder import BUILD_MANIFEST, MODEL_FILE_SUFFIXES, is_staging_folder
from orchestrator.utils import ssh_trust

DEFAULT_INFERENCE_IMAGE = "ghcr.io/smilez1985/llm-runtime:latest"
# The device runtime's start script, relative to the device user's home (orchestrator/runtime/device/launcher.py).
DEVICE_START_SCRIPT = ".local/share/llm-framework/llama.cpp/current/llama-server.sh"
DEFAULT_QDRANT_IMAGE = "qdrant/qdrant:v1.16.0"
SSH_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.IGNORECASE)
MODEL_SUFFIXES = MODEL_FILE_SUFFIXES
# An image reference pinned by its content digest (name@sha256:<64 hex>): the only form pulled for a package.
DIGEST_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}$", re.ASCII)
REMOTE_OUTPUT_LINES = 100     # lines of the device's output kept for the result (the rest is in the log)
REMOTE_LINE_CHARS = 500
# A board's address or host name (192.168.1.50, myboard.local, an IPv6 address); the person enters what they know.
_HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:.]{2,45})$", re.ASCII)
# A deploy whose network drops (a Wi-Fi board) waits this long for the board to answer again (N5), trying every
# WAIT_INTERVAL seconds, and continues at most MAX_RESUMES times.
DEPLOY_WAIT_SECONDS = 300
WAIT_INTERVAL = 5.0
MAX_RESUMES = 20
UPLOAD_CHUNK = 1 << 20
INSTALL_TIMEOUT = 1800        # seconds deploy.sh may run on the board
INSTALL_POLL = 2.0
# Files of an installation that runs on the board on its own, in the deploy's private folder there.
INSTALL_STARTED = ".lcf-install.started"
INSTALL_STATUS = ".lcf-install.status"
INSTALL_LOG = ".lcf-install.log"
_REMOTE_DIR = re.compile(r"^/tmp/llm_deploy\.[A-Za-z0-9]{8}$", re.ASCII)


class DeployError(ValueError):
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


@dataclass(frozen=True)
class DeployEvent:
    """What a running deploy reports (deploy_package's on_event): "uploading" (bytes on the board of the package's
    total), "waiting" (the network dropped: seconds waited of the limit, and whether the upload or the installation
    waits), "back" (the board answers again; the upload continues at `done`), "installing"."""
    kind: str
    done: int = 0
    total: int = 0
    elapsed: float = 0.0
    limit: float = 0.0
    step: str = ""


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _primary_name(info: Dict[str, Any]) -> Optional[str]:
    primary = info.get("primary")
    return primary if isinstance(primary, str) and primary and primary == Path(primary).name else None


def list_builds(output_dir: Path) -> List[Dict[str, Any]]:
    """Finished builds below output_dir, found through their build manifest (newest first).
    Symlinked subfolders are followed (output trees moved to another disk), loops are skipped.
    The build engine's staging folders are skipped: a build is staged there until it has completed."""
    builds, seen = [], set()
    for root, dirs, files in os.walk(output_dir, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if not is_staging_folder(d)]
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

class DeploymentManager:
    def __init__(self, framework_manager=None):
        self.logger = get_logger("DeploymentManager")
        self.framework = framework_manager
        self._clock, self._sleep = time.monotonic, time.sleep  # the waiting for a board (tests replace them)

    # --- PACKAGE GENERATION ---

    def create_deployment_package(self, artifact_path: Path, profile_name: str, docker_config: Dict, output_dir: Path,
                                  kv_cache: Optional[str] = None) -> Optional[Path]:
        """The package (see create_package), or None when it cannot be made (the reason is logged)."""
        try:
            return self.create_package(artifact_path, profile_name, docker_config, output_dir, kv_cache=kv_cache)
        except DeployError as e:
            self.logger.error(str(e))
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
        qdrant_image = cfg.get("image_qdrant", DEFAULT_QDRANT_IMAGE) if cfg.get("enable_rag_knowledge", False) else None
        images = ([inference_image] + ([qdrant_image] if qdrant_image else [])) if use_docker else []
        client = self.framework.get_component("docker_client") if images else None
        for image in images:
            problem = image_problem(image, client)
            if problem:
                raise DeployError(problem[0].upper() + problem[1:] + ".", code="not_supported",
                                  next="Set image_inference_runtime (and image_qdrant) to an image@sha256:<digest> "
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

            # C. Docker Images & RAG Setup
            if use_docker:
                if qdrant_image:
                    # WICHTIG: Slim RAG Strategy
                    # Wir erstellen nur die Struktur, kopieren aber KEINE Host-Daten.
                    self._setup_slim_vector_db(temp_path)

                load_names = self._export_docker_images(images, temp_path / "images", client)
                check_stop()
            else:
                load_names = {}

            # D. Script Generation (Deploy + Compose Logic)
            try:
                script_content = self._generate_deploy_script(
                    artifact_path.name, flags_str, use_docker, inference_image, qdrant_image, load_names,
                    kv_cache=kv_cache)
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

    def _setup_slim_vector_db(self, temp_path: Path):
        """
        Erstellt leere Ordnerstruktur für Qdrant.
        Verhindert "Knowledge Bloat" durch Nicht-Kopieren der Host-DB.
        Das Target startet bei Null und lernt lokal.
        """
        qdrant_storage = temp_path / "data" / "qdrant"
        qdrant_storage.mkdir(parents=True, exist_ok=True)
        # .keep Datei, damit leere Ordner gezippt werden
        (qdrant_storage / ".keep").touch()
        self.logger.info("Initialized Slim-RAG storage structure (Clean Slate).")

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
        with open(output_file, "w", newline="\n", encoding="utf-8") as f:
            for root, _, files in os.walk(directory):
                for file in files:
                    p = Path(root) / file
                    if p.name == output_file.name: continue
                    sha = hashlib.sha256()
                    with open(p, "rb") as f_in:
                        for chunk in iter(lambda: f_in.read(4096), b""): sha.update(chunk)
                    # POSIX separators: the file is verified with sha256sum on the Linux target, also when built on Windows.
                    f.write(f"{sha.hexdigest()}  {p.relative_to(directory).as_posix()}\n")

    def _generate_deploy_script(self, artifact, flags, use_docker,
                                inference_image: str = DEFAULT_INFERENCE_IMAGE,
                                qdrant_image: Optional[str] = None,
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
            if qdrant_image:
                script.extend([
                    "# Qdrant Sidecar (Local Long-Term Memory, persisted in ./data/qdrant)",
                    "docker run -d --name qdrant-sidecar \\",
                    "  --network llm-net \\",
                    "  -v \"$(pwd)/data/qdrant:/qdrant/storage\" \\",
                    "  --restart unless-stopped \\",
                    "  --pull never \\",
                    f"  {shlex.quote(names.get(qdrant_image, qdrant_image))}",
                    ""
                ])
            node = [
                "# Inference Node (with Hardware Access)",
                "docker run -d --name llm-node \\",
                "  --network llm-net \\",
            ]
            if flags:
                node.append(f"  {flags} \\")  # Hardware Flags (NPU/GPU) from the target profile
            if qdrant_image:
                node.append("  -e QDRANT_HOST=qdrant-sidecar \\")
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

    def host_key_status(self, target_ip: str, port: int = 22):
        """('known'|'unknown'|'changed', key) for the device - shown to the user before trusting it."""
        key = ssh_trust.fetch_host_key(target_ip, port)
        return ssh_trust.check_host_key(self._state_dir(), target_ip, port, key), key

    def trust_host(self, target_ip: str, key, port: int = 22) -> None:
        """Stores a host key the user confirmed by comparing its fingerprint."""
        ssh_trust.trust_host_key(self._state_dir(), target_ip, port, key)

    def deploy_artifact(self, artifact_path: Path, host: str, user: str, password: Optional[str] = None,
                        port: int = 22, pair: bool = True) -> bool:
        """
        Uploads the package and runs its deploy.sh on the device (see deploy_package, which also says why it failed).
        `host` is the board's address or host name (e.g. 192.168.1.50 or myboard.local). The device's host key must
        be trusted already (host_key_status/trust_host); a password is used for this connection only and, with
        pair=True, to install the framework's SSH key so later deployments need none.
        """
        result = self.deploy_package(Path(artifact_path), host, user, port=port, password=password, pair=pair)
        if not result.success and result.reason:
            self.logger.error(result.reason)
        return result.success

    def deploy_package(self, package: Path, host: str, user: str, *, port: int = 22, password: Optional[str] = None,
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
        from orchestrator.runtime.remote_probe import valid_host
        stop = cancelled or (lambda: False)
        # A leading '-' would be parsed by ssh/scp as an option (e.g. -oProxyCommand=...).
        if not isinstance(host, str) or not _HOST.fullmatch(host) or not valid_host(host):
            return DeployResult(False, reason=f"Security/Format Error: Invalid address or host name: "
                                              f"{str(host)[:64]!r}")
        if not SSH_USER_PATTERN.match(user or ""):
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
        return self._deploy_system(Path(package), host, user, port, expect_sha256, stop)

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
    def _remote_start_cmd(target_dir: str, package_name: str) -> str:
        """Starts the installation on the board on its own (nohup, nothing attached to the SSH session), so a network
        drop never stops deploy.sh halfway: unzip and deploy.sh (read by sh, as in _remote_install_cmd) write into
        INSTALL_LOG, their exit status goes into INSTALL_STATUS. The marker INSTALL_STARTED is made first, and a folder
        that has it is never started again, also when this command reaches the board twice."""
        d, pkg = shlex.quote(target_dir), shlex.quote(package_name)
        job = f"unzip -o {pkg} > {INSTALL_LOG} 2>&1 && sh deploy.sh >> {INSTALL_LOG} 2>&1; echo $? > {INSTALL_STATUS}"
        return (f"cd {d} && if [ -e {INSTALL_STARTED} ]; then echo started=already; else : > {INSTALL_STARTED} && "
                f"(nohup sh -c {shlex.quote(job)} </dev/null >/dev/null 2>&1 &) && echo started=now; fi")

    # A private, unpredictable folder on the device (a fixed /tmp path could be pre-created by another user).
    REMOTE_MKTEMP = "mktemp -d /tmp/llm_deploy.XXXXXXXX"

    @staticmethod
    def _keep(lines: List[str], line: str) -> None:
        text = "".join(ch if ch.isprintable() else " " for ch in str(line).rstrip("\r\n"))[:REMOTE_LINE_CHARS]
        lines.append(text)
        if len(lines) > REMOTE_OUTPUT_LINES:
            del lines[0]

    @staticmethod
    def _run(ssh, command: str) -> tuple:
        """(exit status, stdout, stderr) of a command on the board."""
        _, out, err = ssh.exec_command(command)
        stdout = out.read().decode(errors="replace")
        stderr = err.read().decode(errors="replace")
        return out.channel.recv_exit_status(), stdout, stderr

    @staticmethod
    def _connection_lost(ssh) -> bool:
        """Whether the SSH session itself broke (the network dropped), not a command or a file on the board."""
        try:
            transport = ssh.get_transport()
        except Exception:  # noqa: BLE001 - a session that cannot say: treated as broken
            return True
        return transport is None or not transport.is_active()

    def _deploy_paramiko(self, artifact: Path, ip: str, user: str, pw: Optional[str], port: int, pair: bool,
                         expect_fingerprint: Optional[str] = None, expect_sha256: Optional[str] = None,
                         on_client: Optional[Callable[[Any], None]] = None,
                         stop: Callable[[], bool] = lambda: False, wait_seconds: float = DEPLOY_WAIT_SECONDS,
                         on_event: Optional[Callable[[DeployEvent], None]] = None) -> DeployResult:
        # The package is opened once: the bytes hashed are the bytes uploaded, and a changed package is refused before
        # anything reaches the device.
        with open(artifact, "rb") as fh:
            h = hashlib.sha256()
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
            if expect_sha256 and h.hexdigest() != expect_sha256:
                return DeployResult(False, reason=f"The package {artifact.name} changed after it was shown (its "
                                                  "sha256 differs); nothing was deployed.")
            size = fh.tell()
            fh.seek(0)
            session = _BoardSession(self, ip, user, pw, port, expect_fingerprint, on_client, stop, wait_seconds,
                                    on_event)
            return session.deploy(fh, artifact.name, size, h.hexdigest(), pair)

    def _deploy_system(self, artifact: Path, ip: str, user: str, port: int = 22,
                       expect_sha256: Optional[str] = None, stop: Callable[[], bool] = lambda: False) -> DeployResult:
        """Fallback to OpenSSH: framework key and known_hosts only, never prompts, never auto-accepts keys, and never
        hands this process's stdin to ssh or scp (over MCP stdio it is the protocol stream). A failure says its SSH
        reason code where OpenSSH's message shows it; waiting for the board and resuming the upload need paramiko
        (a dependency of the framework, so this fallback is only used where it is missing)."""
        from orchestrator.runtime.device import reasons
        lines: List[str] = []
        started = False
        try:
            with tempfile.TemporaryDirectory(prefix="llm-deploy-") as tmp:
                upload = artifact
                if expect_sha256:
                    # The copy that is hashed is the copy that is uploaded.
                    upload = Path(tmp) / artifact.name
                    shutil.copyfile(artifact, upload)
                    if sha256_file(upload) != expect_sha256:
                        return DeployResult(False, reason=f"The package {artifact.name} changed after it was shown "
                                                          "(its sha256 differs); nothing was deployed.")
                opts = ssh_trust.system_ssh_options(self._state_dir())
                target = f"{user}@{ip}"
                made = subprocess.run(["ssh", *opts, "-p", str(port), target, self.REMOTE_MKTEMP],
                                      capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
                target_dir = made.stdout.strip()
                # Checks mktemp's answer on the device; no local temp file is involved.
                if not target_dir.startswith("/tmp/llm_deploy."):  # nosec B108
                    raise RuntimeError("could not create a folder on the device")
                subprocess.run(["scp", *opts, "-P", str(port), str(upload), f"{target}:{target_dir}/"], check=True,
                               stdin=subprocess.DEVNULL, capture_output=True)
                if stop():
                    return DeployResult(False, reason="The deploy was cancelled after the upload; deploy.sh did not "
                                                      "run.", device_state="ok")
                cmd = (self._remote_install_cmd(target_dir, artifact.name)
                       + f"; status=$?; rm -rf {shlex.quote(target_dir)}; exit $status")
                started = True
                res = subprocess.run(["ssh", *opts, "-p", str(port), target, cmd], capture_output=True, text=True,
                                     stdin=subprocess.DEVNULL)
            self.logger.info(res.stdout)
            if res.stderr:
                self.logger.warning(res.stderr)
            for line in (res.stdout or "").splitlines() + (res.stderr or "").splitlines():
                self._keep(lines, line)
            if res.returncode == 0:
                return DeployResult(True, exit_status=0, output=lines, device_state="ok")
            code = reasons.classify_ssh_output(res.stderr or "") if res.returncode == 255 else None
            return DeployResult(False, exit_status=res.returncode, output=lines, code=code or "",
                                device_state="unknown" if code else "failed",
                                reason=(f"The connection to the device failed ({code})." if code else
                                        f"deploy.sh failed on the device (exit {res.returncode})."))
        except subprocess.CalledProcessError as e:
            output = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode(errors="replace")
            code = reasons.classify_ssh_output(output) or ""
            for line in output.splitlines():
                self._keep(lines, line)
            return DeployResult(False, output=lines, device_state="unknown" if started else "ok", code=code,
                                reason=f"System Deployment Error: {e.cmd[0]} failed (exit {e.returncode})"
                                       + (f" ({code})." if code else "."))
        except Exception as e:
            return DeployResult(False, output=lines, device_state="unknown" if started else "ok",
                                reason=f"System Deployment Error: {e}")


class _Stopped(Exception):
    """The person (or the calling AI's cancel) stopped the deploy."""


class _BoardSession:
    """One deploy over paramiko (DeploymentManager.deploy_package): the first login, the private folder on the board,
    the upload that resumes after a network drop, the installation that runs on the board on its own, and the waiting
    for the board in between (N5). Every login checks the host key as the first one did."""

    def __init__(self, manager: DeploymentManager, host: str, user: str, password: Optional[str], port: int,
                 expect_fingerprint: Optional[str], on_client: Optional[Callable[[Any], None]],
                 stop: Callable[[], bool], wait_seconds: float, on_event: Optional[Callable[[DeployEvent], None]]):
        self.manager, self.logger = manager, manager.logger
        self.host, self.user, self.password, self.port = host, user, password, port
        self.expect_fingerprint, self.on_client, self.stop = expect_fingerprint, on_client, stop
        self.wait_seconds = max(0.0, float(wait_seconds))
        self.on_event = on_event or (lambda _e: None)
        self.ssh: Any = None
        self.lines: List[str] = []
        self.resumed = 0
        self.waited = 0.0
        self.state_after_stop = "ok"   # "unknown" once deploy.sh was started on the board

    def result(self, success: bool, **values: Any) -> DeployResult:
        values.setdefault("output", self.lines)
        return DeployResult(success, resumed=self.resumed, waited_s=round(self.waited, 1), **values)

    # --- logging in ---

    def login(self) -> Optional[DeployResult]:
        """Reads and checks the board's host key, then logs in. None when logged in, else why not."""
        from orchestrator.runtime.device import reasons
        state_dir = self.manager._state_dir()
        try:
            key = ssh_trust.fetch_host_key(self.host, self.port)
            status = ssh_trust.check_host_key(state_dir, self.host, self.port, key)
        except Exception as e:  # noqa: BLE001 - unreachable, no SSH server, no paramiko, a damaged known_hosts
            code = reasons.classify(e) or ""
            return self.result(False, code=code, reason=f"Could not read the host key of {self.host}"
                                                        + (f" ({code})." if code else f": {e}"))
        if status == "unknown":
            return self.result(False, reason=f"The device {self.host} is not trusted yet: confirm its host key "
                                             "fingerprint first.")
        if status == "changed":
            return self.result(False, code=reasons.HOST_KEY_CHANGED,
                               reason=str(ssh_trust.HostKeyChanged(self.host, self.port)))
        if self.expect_fingerprint and ssh_trust.fingerprint(key) != self.expect_fingerprint:
            return self.result(False, code=reasons.HOST_KEY_CHANGED,
                               reason=f"The device {self.host} now presents another host key than the one shown "
                                      f"({ssh_trust.fingerprint(key)}, not {self.expect_fingerprint}); nothing was "
                                      "deployed.")
        if self.stop():
            raise _Stopped()
        try:
            self.ssh = ssh_trust.connect(self.host, self.user, state_dir, port=self.port, password=self.password)
        except Exception as e:  # noqa: BLE001 - authentication, network, a host key that changed meanwhile
            code = reasons.classify(e) or ""
            return self.result(False, code=code, reason=f"SSH login to {self.user}@{self.host} failed"
                                                        + (f" ({code})." if code else f": {e}"))
        if self.on_client is not None:
            self.on_client(self.ssh)
        return None

    def close(self) -> None:
        if self.ssh is not None:
            try:
                self.ssh.close()
            except Exception:  # noqa: BLE001
                pass
            self.ssh = None

    def pause(self, seconds: float) -> None:
        """Sleeps, but ends at once on a cancel."""
        end = self.manager._clock() + seconds
        while not self.stop():
            left = end - self.manager._clock()
            if left <= 0:
                return
            self.manager._sleep(min(0.25, left))

    def wait_for_board(self, step: str) -> Optional[DeployResult]:
        """After a network drop: waits until the board answers on its SSH port again and logs in again (None), or
        until wait_seconds have passed (the result with the reason code). A cancel ends the wait at once."""
        from orchestrator.runtime.device import reasons
        self.close()
        manager = self.manager
        started = manager._clock()
        code: Optional[str] = None
        while True:
            if self.stop():
                raise _Stopped()
            elapsed = manager._clock() - started
            self.on_event(DeployEvent("waiting", elapsed=elapsed, limit=self.wait_seconds, step=step))
            if manager._check_conn(self.host, self.port):
                failed = self.login()
                if failed is None:
                    self.waited += manager._clock() - started
                    return None
                if failed.code not in (reasons.HOST_DOWN, reasons.PORT_CLOSED):
                    return failed  # a changed key or a refused login: waiting does not help
                code = failed.code  # the SSH server is not up yet: keep waiting
            elapsed = manager._clock() - started
            if elapsed >= self.wait_seconds:
                self.waited += elapsed
                code = code or reasons.check_port(self.host, self.port) or reasons.HOST_DOWN
                return self.result(False, code=code, device_state="unknown" if step == "install" else "ok",
                                   reason=f"The board did not answer again within {int(self.wait_seconds)} s "
                                          f"({code}); " + ("the installation on the board may still have finished."
                                                           if step == "install" else "deploy.sh did not run."))
            self.pause(min(WAIT_INTERVAL, max(0.1, self.wait_seconds - elapsed)))

    # --- the steps ---

    def deploy(self, fh, name: str, size: int, sha256: str, pair: bool) -> DeployResult:
        try:
            failed = self.login()
            if failed is not None:
                return failed
            return self._deploy(fh, name, size, sha256, pair)
        except _Stopped:
            return self.stopped()
        except Exception as e:  # noqa: BLE001 - a step that failed in a way no step expected
            if self.stop():
                return self.stopped()
            return self.result(False, device_state=self.state_after_stop, reason=f"SSH Deployment Error: {e}")
        finally:
            self.close()

    def stopped(self) -> DeployResult:
        return self.result(False, device_state=self.state_after_stop,
                           reason=("The deploy was cancelled; deploy.sh runs on the board on its own and is never "
                                   "stopped halfway, so the device state is unknown."
                                   if self.state_after_stop == "unknown" else
                                   "The deploy was cancelled before deploy.sh ran; nothing was installed."))

    def _deploy(self, fh, name: str, size: int, sha256: str, pair: bool) -> DeployResult:
        if self.password and pair:
            ssh_trust.install_public_key(self.ssh, ssh_trust.client_key(self.manager._state_dir())[1])
            self.logger.info("Framework SSH key installed; later deployments need no password.")
        status, out, err = DeploymentManager._run(self.ssh, DeploymentManager.REMOTE_MKTEMP)
        target_dir = out.strip()
        # Checks mktemp's answer on the device; no local temp file is involved.
        if status != 0 or not target_dir.startswith("/tmp/llm_deploy.") or not _REMOTE_DIR.fullmatch(target_dir):  # nosec B108
            # What the device answered is device text: it goes into the output lines, never into the reason.
            for line in err.splitlines()[-REMOTE_OUTPUT_LINES:]:
                self.logger.error(f"[REMOTE] {line.strip()}")
                DeploymentManager._keep(self.lines, line)
            return self.result(False, reason="Could not create a folder on the device (its answer is in the device "
                                             "output).", device_state="ok")
        if self.stop():
            raise _Stopped()
        failed = self._upload(fh, name, size, sha256, target_dir)
        if failed is not None:
            return failed
        if self.stop():
            return self.result(False, reason="The deploy was cancelled after the upload; deploy.sh did not run.",
                               device_state="ok")
        self.logger.info("Installing on target...")
        self.on_event(DeployEvent("installing", done=size, total=size))
        return self._install(name, target_dir)

    def _upload(self, fh, name: str, size: int, sha256: str, target_dir: str) -> Optional[DeployResult]:
        """Uploads the package as <name>.part and renames it when its sha256 on the board is the package's. After a
        network drop it waits for the board and continues at the bytes that arrived, once the sha256 of those bytes
        on the board matches the same bytes here (else it starts again from the beginning)."""
        part = f"{target_dir}/{name}.part"
        restarted = False
        while True:
            try:
                offset = self._resume_offset(fh, part, size)
                self.logger.info("Uploading package..." if not offset else f"Resuming the upload at {offset} bytes...")
                self._send(fh, part, offset, size)
                status, out, _err = DeploymentManager._run(self.ssh, f"sha256sum {shlex.quote(part)}")
                if status == 0 and out.split()[:1] == [sha256]:
                    status, _out, _err = DeploymentManager._run(
                        self.ssh, f"mv -f {shlex.quote(part)} {shlex.quote(target_dir + '/' + name)}")
                    if status == 0:
                        return None
                    return self.result(False, reason="The package arrived, but could not be renamed on the device.",
                                       device_state="ok")
                if restarted:
                    return self.result(False, reason="The package arrived damaged twice (its sha256 on the device "
                                                     "differs); deploy.sh did not run.", device_state="ok")
                restarted = True
                DeploymentManager._run(self.ssh, f"rm -f {shlex.quote(part)}")
            except _Stopped:
                raise
            except Exception as e:  # noqa: BLE001 - a network drop, or a failure on the board (disk full, ...)
                if self.stop():
                    raise _Stopped() from None
                if self.ssh is None or not DeploymentManager._connection_lost(self.ssh) or \
                        self.resumed >= MAX_RESUMES:
                    return self.result(False, device_state="ok", reason=f"The upload to {self.host} failed: {e}")
                self.logger.warning(f"The connection to {self.host} dropped during the upload; waiting for the board.")
                failed = self.wait_for_board("upload")
                if failed is not None:
                    return failed
                self.resumed += 1
                status, _out, _err = DeploymentManager._run(self.ssh, f"[ -d {shlex.quote(target_dir)} ]")
                if status != 0:
                    # the board restarted and its /tmp is empty: nothing of the package is left there
                    return self.result(False, device_state="ok",
                                       reason="The board answers again, but the upload folder is gone (the board "
                                              "restarted); deploy.sh did not run. Deploy again.")

    def _resume_offset(self, fh, part: str, size: int) -> int:
        """How many bytes of the package are on the board and match it (0: start from the beginning)."""
        sftp = self.ssh.open_sftp()
        try:
            try:
                offset = int(sftp.stat(part).st_size or 0)
            except FileNotFoundError:  # nothing arrived yet
                return 0
        finally:
            sftp.close()
        if offset <= 0 or offset > size:
            return 0
        status, out, _err = DeploymentManager._run(self.ssh, f"head -c {offset} {shlex.quote(part)} | sha256sum")
        h = hashlib.sha256()
        fh.seek(0)
        left = offset
        while left:
            chunk = fh.read(min(UPLOAD_CHUNK, left))
            if not chunk:
                break
            h.update(chunk)
            left -= len(chunk)
        if status != 0 or out.split()[:1] != [h.hexdigest()]:
            self.logger.warning("What arrived on the board does not match the package; the upload starts again.")
            return 0
        self.on_event(DeployEvent("back", done=offset, total=size))
        return offset

    def _send(self, fh, part: str, offset: int, size: int) -> None:
        sftp = self.ssh.open_sftp()
        try:
            fh.seek(offset)
            with sftp.open(part, "r+b" if offset else "wb") as remote:
                remote.set_pipelined(True)
                if offset:
                    remote.seek(offset)
                done = offset
                self.on_event(DeployEvent("uploading", done=done, total=size))
                for chunk in iter(lambda: fh.read(UPLOAD_CHUNK), b""):
                    if self.stop():
                        raise _Stopped()
                    remote.write(chunk)
                    done += len(chunk)
                    self.on_event(DeployEvent("uploading", done=done, total=size))
        finally:
            sftp.close()

    def _install(self, name: str, target_dir: str) -> DeployResult:
        """Starts the installation once (DeploymentManager._remote_start_cmd) and waits for its exit status; after a
        network drop it waits for the board, logs in again and keeps waiting for the same installation."""
        d = shlex.quote(target_dir)
        start = DeploymentManager._remote_start_cmd(target_dir, name)
        started_at = self.manager._clock()
        sent = False
        while True:
            try:
                if not sent:
                    status, out, err = DeploymentManager._run(self.ssh, start)
                    if status != 0 or "started=" not in out:
                        for line in err.splitlines()[-REMOTE_OUTPUT_LINES:]:
                            DeploymentManager._keep(self.lines, line)
                        return self.result(False, device_state="ok", reason="The installation could not be started "
                                                                            "on the device (its answer is in the "
                                                                            "device output).")
                    sent = True
                    self.state_after_stop = "unknown"
                status, out, _err = DeploymentManager._run(
                    self.ssh, f"cat {d}/{INSTALL_STATUS} 2>/dev/null || {{ [ -d {d} ] && echo running || echo gone; }}")
                exit_status = out.strip()
                if status == 0 and exit_status.isdigit():
                    break
                if exit_status == "gone":
                    return self.result(False, device_state="unknown",
                                       reason="The board restarted during the installation (its upload folder is "
                                              "gone), so the device state is unknown. Deploy again.")
                if self.manager._clock() - started_at > INSTALL_TIMEOUT:
                    return self.result(False, device_state="unknown",
                                       reason=f"deploy.sh did not finish on the device within {INSTALL_TIMEOUT} s.")
                if self.stop():
                    raise _Stopped()
                self.pause(INSTALL_POLL)
            except _Stopped:
                raise
            except Exception as e:  # noqa: BLE001 - a network drop while the board installs on its own
                if self.stop():
                    raise _Stopped() from None
                if self.ssh is None or not DeploymentManager._connection_lost(self.ssh) or \
                        self.resumed >= MAX_RESUMES:
                    return self.result(False, device_state="unknown" if sent else "ok",
                                       reason=f"The installation on {self.host} could not be followed: {e}")
                self.logger.warning(f"The connection to {self.host} dropped during the installation; waiting for "
                                    "the board.")
                failed = self.wait_for_board("install" if sent else "upload")
                if failed is not None:
                    return failed
                self.resumed += 1
                self.on_event(DeployEvent("installing"))
        _status, log, _err = DeploymentManager._run(self.ssh, f"tail -n {REMOTE_OUTPUT_LINES} {d}/{INSTALL_LOG}")
        for line in log.splitlines():
            self.logger.info(f"[REMOTE] {line.strip()}")
            DeploymentManager._keep(self.lines, line)
        DeploymentManager._run(self.ssh, f"rm -rf {d}")
        code = int(exit_status)
        if code == 0:
            self.logger.info("Remote deployment successful.")
            return self.result(True, exit_status=0, device_state="ok")
        return self.result(False, exit_status=code, device_state="failed",
                           reason=f"deploy.sh failed on the device (exit {code}).")
