#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deployment Manager (v2.3.0)
DIREKTIVE: Goldstandard Deployment with Slim-RAG Strategy.

Features:
- Package Generation with Multi-Container Support.
- Air-Gap Image Export (Docker Tarballs).
- Slim-RAG: Target gets empty DB structure, learns locally.
"""

import os
import time
import socket
import logging
import subprocess
import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, List, Dict

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

import docker
from orchestrator.utils.logging import get_logger

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
        artifact_path = Path(artifact_path)
        if not artifact_path.exists():
            self.logger.error(f"Artifact not found: {artifact_path}")
            return None

        # Hardware Flags vom TargetManager holen
        target_mgr = self.framework.get_component("target_manager")
        docker_flags = []
        if target_mgr and profile_name:
            docker_flags = target_mgr.get_docker_flags_for_profile(profile_name)
        flags_str = " ".join(docker_flags)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # A. Copy Binary / Artifact
            shutil.copy2(artifact_path, temp_path / artifact_path.name)
            
            # B. Configs (User Profiles, Prompts)
            self._bundle_user_configs(temp_path / "data" / "configs")

            # C. Docker Images & RAG Setup
            use_docker = docker_config.get("use_docker", False)
            if use_docker:
                images = ["ghcr.io/llm-framework/inference-node:latest"]
                
                # RAG Logic: Check if enabled
                if self.framework.config.enable_rag_knowledge:
                    images.append("qdrant/qdrant:latest")
                    # WICHTIG: Slim RAG Strategy
                    # Wir erstellen nur die Struktur, kopieren aber KEINE Host-Daten.
                    self._setup_slim_vector_db(temp_path)
                
                self._export_docker_images(images, temp_path / "images")

            # D. Script Generation (Deploy + Compose Logic)
            script_content = self._generate_deploy_script(artifact_path.name, flags_str, use_docker)
            
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
        with open(output_file, "w") as f:
            for root, _, files in os.walk(directory):
                for file in files:
                    p = Path(root) / file
                    if p.name == output_file.name: continue
                    sha = hashlib.sha256()
                    with open(p, "rb") as f_in:
                        for chunk in iter(lambda: f_in.read(4096), b""): sha.update(chunk)
                    f.write(f"{sha.hexdigest()}  {p.relative_to(directory)}\n")

    def _generate_deploy_script(self, artifact, flags, use_docker) -> str:
        script = [
            "#!/bin/bash",
            "set -e",
            "echo '[1/5] Checking Integrity...'",
            "sha256sum -c checksums.sha256",
            ""
        ]
        
        if use_docker:
            # Multi-Container Logic
            script.extend([
                "echo '[2/5] Loading Images (Air-Gap Support)...'",
                "for img in images/*.tar; do docker load -i \"$img\"; done",
                "",
                "echo '[3/5] Starting Stack (Inference + Local Memory)...'",
                "# Create Network",
                "docker network create llm-net || true",
                "",
                "# 1. Qdrant Sidecar (Local Long-Term Memory)",
                "# Nutzt ./data/qdrant für Persistenz auf dem Target",
                "docker run -d --name qdrant-sidecar \\",
                "  --network llm-net \\",
                "  -v $(pwd)/data/qdrant:/qdrant/storage \\",
                "  --restart unless-stopped \\",
                "  qdrant/qdrant:latest",
                "",
                "# 2. Inference Node (with Hardware Access)",
                f"docker run -d --name llm-node \\",
                "  --network llm-net \\",
                f"  {flags} \\", # Hardware Flags (NPU/GPU)
                "  -e QDRANT_HOST=qdrant-sidecar \\",
                "  -v $(pwd)/data:/app/data \\",
                "  --restart unless-stopped \\",
                "  ghcr.io/llm-framework/inference-node:latest",
                ""
            ])
        else:
            script.append(f"chmod +x {artifact} && ./{artifact}")

        script.append("echo 'Deployment Complete. System is ready.'")
        return "\n".join(script)

    # --- EXECUTION (SSH / SCP) ---
    def deploy_artifact(self, artifact_path: Path, target_ip: str, user: str, password: Optional[str] = None) -> bool:
        if not self._check_conn(target_ip):
            self.logger.error(f"Target {target_ip} not reachable.")
            return False
        
        target_dir = "/tmp/llm_deploy"
        self.logger.info(f"Deploying to {target_ip}...")
        
        if PARAMIKO_AVAILABLE and password:
            return self._deploy_paramiko(artifact_path, target_ip, user, password, target_dir)
        return self._deploy_system(artifact_path, target_ip, user, target_dir)

    def _check_conn(self, ip, port=22):
        try:
            socket.create_connection((ip, port), timeout=2)
            return True
        except: return False

    def _deploy_paramiko(self, artifact, ip, user, pw, target_dir):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(ip, username=user, password=pw)
            sftp = ssh.open_sftp()
            try: sftp.mkdir(target_dir)
            except: pass
            
            # Upload
            self.logger.info("Uploading package...")
            sftp.put(str(artifact), f"{target_dir}/{artifact.name}")
            
            # Execute
            self.logger.info("Installing on target...")
            cmd = f"cd {target_dir} && unzip -o {artifact.name} && chmod +x deploy.sh && ./deploy.sh"
            stdin, stdout, stderr = ssh.exec_command(cmd)
            
            # Stream Output
            for line in stdout: self.logger.info(f"[REMOTE] {line.strip()}")
            
            exit_status = stdout.channel.recv_exit_status()
            if exit_status == 0:
                self.logger.info("Remote deployment successful.")
                return True
            else:
                self.logger.error("Remote deployment failed.")
                return False
        except Exception as e:
            self.logger.error(f"SSH Deployment Error: {e}")
            return False
        finally:
            ssh.close()

    def _deploy_system(self, artifact, ip, user, target_dir):
        """Fallback auf System SSH/SCP calls."""
        try:
            target = f"{user}@{ip}"
            subprocess.run(["ssh", target, f"mkdir -p {target_dir}"], check=True)
            subprocess.run(["scp", str(artifact), f"{target}:{target_dir}/"], check=True)
            cmd = f"cd {target_dir} && unzip -o {artifact.name} && chmod +x deploy.sh && ./deploy.sh"
            res = subprocess.run(["ssh", target, cmd], capture_output=True, text=True)
            self.logger.info(res.stdout)
            if res.stderr: self.logger.warning(res.stderr)
            return res.returncode == 0
        except Exception as e:
            self.logger.error(f"System Deployment Error: {e}")
            return False
