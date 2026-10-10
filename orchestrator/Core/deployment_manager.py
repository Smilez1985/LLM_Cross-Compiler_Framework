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

import os
import re
import shlex
import time
import socket
import logging
import subprocess
import hashlib
import shutil
import tempfile
import json
import zipfile
from pathlib import Path
from typing import Any, Optional, List, Dict

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

import docker
from orchestrator.utils.logging import get_logger

# NEU: Import der zentralen Validierungslogik
from orchestrator.utils.validation import validate_ip_address
from orchestrator.Core.builder import BUILD_MANIFEST, MODEL_FILE_SUFFIXES
from orchestrator.utils import ssh_trust

DEFAULT_INFERENCE_IMAGE = "ghcr.io/smilez1985/llm-runtime:latest"
DEFAULT_QDRANT_IMAGE = "qdrant/qdrant:v1.16.0"
SSH_USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.IGNORECASE)
MODEL_SUFFIXES = MODEL_FILE_SUFFIXES


def _primary_name(info: Dict[str, Any]) -> Optional[str]:
    primary = info.get("primary")
    return primary if isinstance(primary, str) and primary and primary == Path(primary).name else None


def list_builds(output_dir: Path) -> List[Dict[str, Any]]:
    """Finished builds below output_dir, found through their build manifest (newest first).
    Symlinked subfolders are followed (output trees moved to another disk), loops are skipped.
    Hidden folders are skipped: the build engine stages a build there until it has completed."""
    builds, seen = [], set()
    for root, dirs, files in os.walk(output_dir, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
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
        """
        Erstellt ein ZIP-Paket für das Zielsystem.
        Inkludiert Binaries, Configs und Docker-Images (für Air-Gap).
        """
        build_dir = Path(artifact_path) if Path(artifact_path).is_dir() else None
        try:
            artifact_path = resolve_model_artifact(Path(artifact_path))
        except (OSError, ValueError) as e:
            self.logger.error(str(e))
            return None

        # Hardware Flags vom TargetManager holen
        target_mgr = self.framework.get_component("target_manager")
        docker_flags = []
        if target_mgr and profile_name:
            docker_flags = target_mgr.get_docker_flags_for_profile(profile_name)
        flags_str = " ".join(docker_flags)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # A. Model file, plus the build's provenance (manifest, model card) when deploying a build
            shutil.copy2(artifact_path, temp_path / artifact_path.name)
            if build_dir:
                for extra in (BUILD_MANIFEST, "Model_Card.md"):
                    if (build_dir / extra).is_file():
                        shutil.copy2(build_dir / extra, temp_path / extra)
            
            # B. Configs (User Profiles, Prompts)
            self._bundle_user_configs(temp_path / "data" / "configs")

            # C. Docker Images & RAG Setup (image names come from the central config)
            use_docker = docker_config.get("use_docker", False)
            cfg = self.framework.config
            inference_image = cfg.get("image_inference_runtime", DEFAULT_INFERENCE_IMAGE)
            qdrant_image = cfg.get("image_qdrant", DEFAULT_QDRANT_IMAGE) if cfg.get("enable_rag_knowledge", False) else None
            if use_docker:
                images = [inference_image]

                if qdrant_image:
                    images.append(qdrant_image)
                    # WICHTIG: Slim RAG Strategy
                    # Wir erstellen nur die Struktur, kopieren aber KEINE Host-Daten.
                    self._setup_slim_vector_db(temp_path)

                self._export_docker_images(images, temp_path / "images")

            # D. Script Generation (Deploy + Compose Logic)
            try:
                script_content = self._generate_deploy_script(
                    artifact_path.name, flags_str, use_docker, inference_image, qdrant_image)
            except ValueError as e:
                self.logger.error(str(e))
                return None
            
            deploy_script_path = temp_path / "deploy.sh"
            with open(deploy_script_path, "w", newline='\n') as f:
                f.write(script_content)
            os.chmod(deploy_script_path, 0o755)

            # E. Checksums & Zip
            self._generate_checksums(temp_path, temp_path / "checksums.sha256")
            
            timestamp = int(time.time())
            pkg_name = f"deploy_{artifact_path.stem}_{timestamp}.zip"
            pkg_path = output_dir / pkg_name
            
            # Output Directory sicherstellen
            output_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(pkg_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(temp_path):
                    for file in files:
                        p = Path(root) / file
                        zipf.write(p, p.relative_to(temp_path))
            
            self.logger.info(f"Deployment Package created: {pkg_path}")
            return pkg_path

    def _bundle_user_configs(self, dest_dir: Path):
        """Kopiert statische Konfigurationen, aber KEINE Datenbanken."""
        # Wir nehmen an, dass FrameworkConfig.configs_dir auf 'config/' zeigt
        src_dir = self.framework.config.config_dir if hasattr(self.framework.config, 'config_dir') else Path("config")
        
        # Falls configs_dir im config object anders heißt (check config_manager):
        if not src_dir.exists():
            src_dir = Path("config") # Fallback relative

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

    def _export_docker_images(self, images: List[str], dest_dir: Path):
        dest_dir.mkdir(parents=True, exist_ok=True)
        client = self.framework.get_component("docker_client")
        if not client:
            self.logger.warning("No Docker Client available. Skipping Image Export.")
            return

        for img_name in images:
            sanitized = img_name.replace("/", "_").replace(":", "_")
            tar_path = dest_dir / f"{sanitized}.tar"
            
            if tar_path.exists():
                continue # Cache check (optional)

            self.logger.info(f"Exporting Docker Image: {img_name}...")
            try:
                # Versuch Pull, falls nicht lokal
                try: client.images.pull(img_name)
                except: pass
                
                img = client.images.get(img_name)
                with open(tar_path, "wb") as f:
                    for chunk in img.save(named=True): f.write(chunk)
            except Exception as e:
                self.logger.error(f"Export failed for {img_name}: {e}")

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
                                qdrant_image: Optional[str] = None) -> str:
        script = [
            "#!/bin/bash",
            "set -e",
            "echo '[1/3] Checking Integrity...'",
            "sha256sum -c checksums.sha256",
            ""
        ]

        if use_docker:
            script.extend([
                "echo '[2/3] Loading Images (Air-Gap Support)...'",
                "for img in images/*.tar; do if [ -e \"$img\" ]; then docker load -i \"$img\"; fi; done",
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
                    f"  {shlex.quote(qdrant_image)}",
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
                f"  {shlex.quote(inference_image)}",
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
                script.extend([
                    "if command -v llama-server >/dev/null 2>&1; then",
                    f'  echo "Start it with: llama-server -m $MODEL_DIR/"{q}" --host 127.0.0.1 --port 8080"',
                    "  echo 'For access from the LAN add --host 0.0.0.0 --api-key-file <file with a secret key>; never expose it without a key.'",
                    "else",
                    "  echo 'Install llama.cpp (llama-server) on this board to run the model.'",
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
        Uploads the package and runs its deploy.sh on the device. The device's host key must be trusted
        already (host_key_status/trust_host); a password is used for this connection only and, with
        pair=True, to install the framework's SSH key so later deployments need none.
        """
        if not validate_ip_address(target_ip):
            self.logger.error(f"Security/Format Error: Invalid IP address provided: {target_ip}")
            return False

        # A leading '-' would be parsed by ssh/scp as an option (e.g. -oProxyCommand=...).
        if not SSH_USER_PATTERN.match(user or ""):
            self.logger.error(f"Security/Format Error: Invalid SSH user name: {user!r}")
            return False

        if not self._check_conn(target_ip, port):
            self.logger.error(f"Target {target_ip} not reachable (port {port}).")
            return False

        self.logger.info(f"Deploying to {target_ip}...")
        if PARAMIKO_AVAILABLE:
            return self._deploy_paramiko(Path(artifact_path), target_ip, user, password, port, pair)
        return self._deploy_system(Path(artifact_path), target_ip, user, port)

    def _check_conn(self, ip, port=22):
        try:
            with socket.create_connection((ip, port), timeout=2):
                return True
        except OSError:
            return False

    @staticmethod
    def _remote_install_cmd(target_dir: str, package_name: str) -> str:
        d, pkg = shlex.quote(target_dir), shlex.quote(package_name)
        return f"cd {d} && unzip -o {pkg} && chmod +x deploy.sh && ./deploy.sh"

    # A private, unpredictable folder on the device (a fixed /tmp path could be pre-created by another user).
    REMOTE_MKTEMP = "mktemp -d /tmp/llm_deploy.XXXXXXXX"

    def _deploy_paramiko(self, artifact: Path, ip: str, user: str, pw: Optional[str], port: int, pair: bool) -> bool:
        state_dir = self._state_dir()
        try:
            status = ssh_trust.check_host_key(state_dir, ip, port, ssh_trust.fetch_host_key(ip, port))
        except (OSError, ssh_trust.HostKeyError, Exception) as e:
            self.logger.error(f"Could not read the host key of {ip}: {e}")
            return False
        if status == "unknown":
            self.logger.error(f"The device {ip} is not trusted yet: confirm its host key fingerprint first.")
            return False
        if status == "changed":
            self.logger.error(str(ssh_trust.HostKeyChanged(ip, port)))
            return False

        try:
            ssh = ssh_trust.connect(ip, user, state_dir, port=port, password=pw)
        except Exception as e:
            self.logger.error(f"SSH login to {user}@{ip} failed: {e}")
            return False
        try:
            if pw and pair:
                ssh_trust.install_public_key(ssh, ssh_trust.client_key(state_dir)[1])
                self.logger.info("Framework SSH key installed; later deployments need no password.")
            _, out, err = ssh.exec_command(self.REMOTE_MKTEMP)
            target_dir = out.read().decode().strip()
            if out.channel.recv_exit_status() != 0 or not target_dir.startswith("/tmp/llm_deploy."):
                self.logger.error(f"Could not create a folder on the device: {err.read().decode(errors='replace').strip()}")
                return False

            sftp = ssh.open_sftp()
            self.logger.info("Uploading package...")
            sftp.put(str(artifact), f"{target_dir}/{artifact.name}")
            
            self.logger.info("Installing on target...")
            stdin, stdout, stderr = ssh.exec_command(self._remote_install_cmd(target_dir, artifact.name))
            for line in stdout: self.logger.info(f"[REMOTE] {line.strip()}")
            
            exit_status = stdout.channel.recv_exit_status()
            ssh.exec_command(f"rm -rf {shlex.quote(target_dir)}")
            if exit_status == 0:
                self.logger.info("Remote deployment successful.")
                return True
            self.logger.error("Remote deployment failed.")
            return False
        except Exception as e:
            self.logger.error(f"SSH Deployment Error: {e}")
            return False
        finally:
            ssh.close()

    def _deploy_system(self, artifact: Path, ip: str, user: str, port: int = 22) -> bool:
        """Fallback to OpenSSH: framework key and known_hosts only, never prompts, never auto-accepts keys."""
        try:
            opts = ssh_trust.system_ssh_options(self._state_dir())
            target = f"{user}@{ip}"
            made = subprocess.run(["ssh", *opts, "-p", str(port), target, self.REMOTE_MKTEMP],
                                  capture_output=True, text=True, check=True)
            target_dir = made.stdout.strip()
            if not target_dir.startswith("/tmp/llm_deploy."):
                raise RuntimeError("could not create a folder on the device")
            subprocess.run(["scp", *opts, "-P", str(port), str(artifact), f"{target}:{target_dir}/"], check=True)
            cmd = self._remote_install_cmd(target_dir, artifact.name) + f"; status=$?; rm -rf {shlex.quote(target_dir)}; exit $status"
            res = subprocess.run(["ssh", *opts, "-p", str(port), target, cmd], capture_output=True, text=True)
            self.logger.info(res.stdout)
            if res.stderr: self.logger.warning(res.stderr)
            return res.returncode == 0
        except Exception as e:
            self.logger.error(f"System Deployment Error: {e}")
            return False
