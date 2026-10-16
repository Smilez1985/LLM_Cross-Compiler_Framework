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

import importlib.util
import os
import re
import shlex
import time
import socket
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

# NEU: Import der zentralen Validierungslogik
from orchestrator.utils.validation import validate_ip_address
from orchestrator.Core.builder import BUILD_MANIFEST, MODEL_FILE_SUFFIXES, is_staging_folder
from orchestrator.utils import ssh_trust

DEFAULT_INFERENCE_IMAGE = "ghcr.io/smilez1985/llm-runtime:latest"
# The device runtime's start script, relative to the device user's home (orchestrator/runtime/device/launcher.py).
DEVICE_START_SCRIPT = ".local/share/llm-framework/llama.cpp/current/llama-server.sh"
SSH_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.IGNORECASE)
MODEL_SUFFIXES = MODEL_FILE_SUFFIXES
# An image reference pinned by its content digest (name@sha256:<64 hex>): the only form pulled for a package.
DIGEST_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}$", re.ASCII)
REMOTE_OUTPUT_LINES = 100     # lines of the device's output kept for the result (the rest is in the log)
REMOTE_LINE_CHARS = 500
_HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:.]{2,45})$", re.ASCII)


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

    # --- PACKAGE GENERATION ---

    def create_deployment_package(self, artifact_path: Path, profile_name: str, docker_config: Dict, output_dir: Path) -> Optional[Path]:
        """The package (see create_package), or None when it cannot be made (the reason is logged)."""
        try:
            return self.create_package(artifact_path, profile_name, docker_config, output_dir)
        except DeployError as e:
            self.logger.error(str(e))
            return None

    def create_package(self, artifact_path: Path, profile_name: str, docker_config: Dict, output_dir: Path, *,
                       cancelled: Optional[Callable[[], bool]] = None) -> Path:
        """
        Creates the ZIP package for the device: the model file (and the build's manifest and model card), the static
        user configs, the generated deploy.sh and checksums; with docker_config["use_docker"] also the Docker images
        (air-gap), each pinned by its digest or built on this computer (image_problem). Raises DeployError with the
        reason. `cancelled()` is checked between the steps; the zip is written under a temporary name and renamed
        when complete, so a cancelled or failed package leaves no file.
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
                    artifact_path.name, flags_str, use_docker, inference_image, load_names)
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
                                load_names: Optional[Dict[str, str]] = None) -> str:
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
                # The framework's self-built runtime (llm-cli device runtime deploy), never a llama-server found on
                # PATH: its start script keeps the server on 127.0.0.1 with the key file and a cleared environment.
                script.extend([
                    f'RUNTIME="$HOME/{DEVICE_START_SCRIPT}"',
                    'if [ -f "$RUNTIME" ]; then',
                    f'  echo "Start it with: sh $RUNTIME start $MODEL_DIR/"{q}',
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

    def deploy_artifact(self, artifact_path: Path, target_ip: str, user: str, password: Optional[str] = None,
                        port: int = 22, pair: bool = True) -> bool:
        """
        Uploads the package and runs its deploy.sh on the device (see deploy_package). The device's host key must be
        trusted already (host_key_status/trust_host); a password is used for this connection only and, with
        pair=True, to install the framework's SSH key so later deployments need none.
        """
        if not validate_ip_address(target_ip):
            self.logger.error(f"Security/Format Error: Invalid IP address provided: {target_ip}")
            return False
        result = self.deploy_package(Path(artifact_path), target_ip, user, port=port, password=password, pair=pair)
        if not result.success and result.reason:
            self.logger.error(result.reason)
        return result.success

    def deploy_package(self, package: Path, host: str, user: str, *, port: int = 22, password: Optional[str] = None,
                       pair: bool = True, expect_fingerprint: Optional[str] = None,
                       expect_sha256: Optional[str] = None, on_client: Optional[Callable[[Any], None]] = None,
                       cancelled: Optional[Callable[[], bool]] = None) -> DeployResult:
        """
        Uploads the package and runs its deploy.sh on the device; returns what happened with the reason instead of a
        bare bool. The device's host key must be trusted already. With `expect_fingerprint` the key the device presents
        now must be exactly that one (the fingerprint a person was shown), with `expect_sha256` the package must still
        have that hash (re-hashed right before the upload, from the same open file that is uploaded). `on_client`
        gets the SSH session (an object with close()) so a cancel can close it; `cancelled()` is checked between the
        steps. Child processes never inherit this process's stdin (over MCP stdio that is the protocol stream).
        """
        stop = cancelled or (lambda: False)
        # A leading '-' would be parsed by ssh/scp as an option (e.g. -oProxyCommand=...).
        if not isinstance(host, str) or not _HOST.fullmatch(host) or host.startswith("-"):
            return DeployResult(False, reason=f"Security/Format Error: Invalid host: {str(host)[:64]!r}")
        if not SSH_USER_PATTERN.match(user or ""):
            return DeployResult(False, reason=f"Security/Format Error: Invalid SSH user name: {user!r}")
        if not Path(package).is_file():
            return DeployResult(False, reason=f"The package {Path(package).name} does not exist.")

        if not self._check_conn(host, port):
            return DeployResult(False, reason=f"Target {host} not reachable (port {port}).")

        self.logger.info(f"Deploying to {host}...")
        if PARAMIKO_AVAILABLE:
            return self._deploy_paramiko(Path(package), host, user, password, port, pair, expect_fingerprint,
                                         expect_sha256, on_client, stop)
        if expect_fingerprint:
            # OpenSSH checks the key against known_hosts only; the fingerprint shown cannot be compared without
            # paramiko, so a deploy bound to one is refused rather than sent unchecked.
            return DeployResult(False, reason="Checking the device's host key against the fingerprint shown needs the "
                                              "'paramiko' package; nothing was deployed.")
        return self._deploy_system(Path(package), host, user, port, expect_sha256, stop)

    def _check_conn(self, ip, port=22):
        try:
            with socket.create_connection((ip, port), timeout=2):
                return True
        except OSError:
            return False

    @staticmethod
    def _remote_install_cmd(target_dir: str, package_name: str) -> str:
        d, pkg = shlex.quote(target_dir), shlex.quote(package_name)
        # sh reads the script instead of executing it from /tmp, which many boards mount noexec (and deploy.sh is
        # plain POSIX sh, so boards without bash, such as Alpine, run it too).
        return f"cd {d} && unzip -o {pkg} && sh deploy.sh"

    # A private, unpredictable folder on the device (a fixed /tmp path could be pre-created by another user).
    REMOTE_MKTEMP = "mktemp -d /tmp/llm_deploy.XXXXXXXX"

    @staticmethod
    def _keep(lines: List[str], line: str) -> None:
        text = "".join(ch if ch.isprintable() else " " for ch in str(line).rstrip("\r\n"))[:REMOTE_LINE_CHARS]
        lines.append(text)
        if len(lines) > REMOTE_OUTPUT_LINES:
            del lines[0]

    def _deploy_paramiko(self, artifact: Path, ip: str, user: str, pw: Optional[str], port: int, pair: bool,
                         expect_fingerprint: Optional[str] = None, expect_sha256: Optional[str] = None,
                         on_client: Optional[Callable[[Any], None]] = None,
                         stop: Callable[[], bool] = lambda: False) -> DeployResult:
        # The package is opened once: the bytes hashed are the bytes uploaded, and a changed package is refused before
        # anything reaches the device.
        with open(artifact, "rb") as fh:
            if expect_sha256:
                h = hashlib.sha256()
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
                if h.hexdigest() != expect_sha256:
                    return DeployResult(False, reason=f"The package {artifact.name} changed after it was shown (its "
                                                      "sha256 differs); nothing was deployed.")
                fh.seek(0)
            return self._deploy_paramiko_open(fh, artifact.name, ip, user, pw, port, pair, expect_fingerprint,
                                              on_client, stop)

    def _deploy_paramiko_open(self, fh, name: str, ip: str, user: str, pw: Optional[str], port: int, pair: bool,
                              expect_fingerprint: Optional[str], on_client: Optional[Callable[[Any], None]],
                              stop: Callable[[], bool]) -> DeployResult:
        state_dir = self._state_dir()
        try:
            key = ssh_trust.fetch_host_key(ip, port)
            status = ssh_trust.check_host_key(state_dir, ip, port, key)
        except (OSError, ssh_trust.HostKeyError, Exception) as e:
            return DeployResult(False, reason=f"Could not read the host key of {ip}: {e}")
        if status == "unknown":
            return DeployResult(False, reason=f"The device {ip} is not trusted yet: confirm its host key fingerprint "
                                              "first.")
        if status == "changed":
            return DeployResult(False, reason=str(ssh_trust.HostKeyChanged(ip, port)))
        if expect_fingerprint and ssh_trust.fingerprint(key) != expect_fingerprint:
            return DeployResult(False, reason=f"The device {ip} now presents another host key than the one shown "
                                              f"({ssh_trust.fingerprint(key)}, not {expect_fingerprint}); nothing was "
                                              "deployed.")
        if stop():
            return DeployResult(False, reason="The deploy was cancelled before connecting; nothing was changed.")

        try:
            ssh = ssh_trust.connect(ip, user, state_dir, port=port, password=pw)
        except Exception as e:
            return DeployResult(False, reason=f"SSH login to {user}@{ip} failed: {e}")
        if on_client is not None:
            on_client(ssh)
        lines: List[str] = []
        started = False  # deploy.sh started on the device: its state is unknown unless it ends
        cancelled = DeployResult(False, device_state="unknown", output=lines,
                                 reason="The deploy was cancelled; the SSH session was closed and the device state is "
                                        "unknown.")
        try:
            if pw and pair:
                ssh_trust.install_public_key(ssh, ssh_trust.client_key(state_dir)[1])
                self.logger.info("Framework SSH key installed; later deployments need no password.")
            _, out, err = ssh.exec_command(self.REMOTE_MKTEMP)
            target_dir = out.read().decode().strip()
            # Checks mktemp's answer on the device; no local temp file is involved.
            if out.channel.recv_exit_status() != 0 or not target_dir.startswith("/tmp/llm_deploy."):  # nosec B108
                # What the device answered is device text: it goes into the output lines, never into the reason.
                for line in err.read().decode(errors="replace").splitlines()[-REMOTE_OUTPUT_LINES:]:
                    self.logger.error(f"[REMOTE] {line.strip()}")
                    self._keep(lines, line)
                return DeployResult(False, reason="Could not create a folder on the device (its answer is in the "
                                                  "device output).", output=lines, device_state="ok")
            if stop():
                return DeployResult(False, reason="The deploy was cancelled before the upload.", device_state="ok")

            sftp = ssh.open_sftp()
            self.logger.info("Uploading package...")
            sftp.putfo(fh, f"{target_dir}/{name}")
            if stop():
                return DeployResult(False, reason="The deploy was cancelled after the upload; deploy.sh did not run.",
                                    device_state="ok")

            self.logger.info("Installing on target...")
            started = True
            stdin, stdout, stderr = ssh.exec_command(self._remote_install_cmd(target_dir, name))
            for line in stdout:
                self.logger.info(f"[REMOTE] {line.strip()}")
                self._keep(lines, line)
            for line in stderr:
                self._keep(lines, line)
            if stop():
                return cancelled

            exit_status = stdout.channel.recv_exit_status()
            ssh.exec_command(f"rm -rf {shlex.quote(target_dir)}")
            if exit_status == 0:
                self.logger.info("Remote deployment successful.")
                return DeployResult(True, exit_status=0, output=lines, device_state="ok")
            return DeployResult(False, exit_status=exit_status, output=lines, device_state="failed",
                                reason=f"deploy.sh failed on the device (exit {exit_status}).")
        except Exception as e:
            if stop():
                return cancelled if started else DeployResult(False, device_state="ok", reason="The deploy was "
                                                              "cancelled before deploy.sh ran.")
            return DeployResult(False, output=lines, device_state="unknown" if started else "ok",
                                reason=f"SSH Deployment Error: {e}")
        finally:
            ssh.close()

    def _deploy_system(self, artifact: Path, ip: str, user: str, port: int = 22,
                       expect_sha256: Optional[str] = None, stop: Callable[[], bool] = lambda: False) -> DeployResult:
        """Fallback to OpenSSH: framework key and known_hosts only, never prompts, never auto-accepts keys, and never
        hands this process's stdin to ssh or scp (over MCP stdio it is the protocol stream)."""
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
            return DeployResult(False, exit_status=res.returncode, output=lines, device_state="failed",
                                reason=f"deploy.sh failed on the device (exit {res.returncode}).")
        except Exception as e:
            return DeployResult(False, output=lines, device_state="unknown" if started else "ok",
                                reason=f"System Deployment Error: {e}")
