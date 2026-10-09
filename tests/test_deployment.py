#!/usr/bin/env python3
"""DeploymentManager tests: package contents, Slim-RAG, Air-Gap images, deploy script, SSH safety."""

import hashlib
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.deployment_manager import DeploymentManager


class FakeImage:
    def __init__(self, name):
        self.name = name

    def save(self, named=True):
        yield f"tarball:{self.name}".encode()


class FakeDockerClient:
    def __init__(self):
        self.pulled = []
        self.images = self

    def pull(self, name):
        self.pulled.append(name)

    def get(self, name):
        return FakeImage(name)


class FakeTargetManager:
    def get_docker_flags_for_profile(self, profile_name):
        return ["--device /dev/dri", "--device /dev/mali0"] if profile_name == "rk3588" else []


class FakeFramework:
    def __init__(self, config, docker_client=None):
        self.config = config
        self._components = {"target_manager": FakeTargetManager(), "docker_client": docker_client}

    def get_component(self, name):
        return self._components.get(name)


@pytest.fixture
def config(tmp_path):
    return ConfigManager(tmp_path / "configs")


@pytest.fixture
def artifact(tmp_path):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"GGUF fake weights")
    return path


def build_package(tmp_path, config, artifact, use_docker, docker_client=None, profile="rk3588"):
    dm = DeploymentManager(FakeFramework(config, docker_client))
    pkg = dm.create_deployment_package(artifact, profile, {"use_docker": use_docker}, tmp_path / "out")
    assert pkg is not None and pkg.exists()
    extract = tmp_path / "pkg"
    with zipfile.ZipFile(pkg) as zf:
        zf.extractall(extract)
    return extract


class TestPackageCreation:

    def test_missing_artifact_returns_none(self, tmp_path, config):
        dm = DeploymentManager(FakeFramework(config))
        assert dm.create_deployment_package(tmp_path / "nope.gguf", "", {}, tmp_path / "out") is None

    def test_native_package(self, tmp_path, config, artifact):
        pkg = build_package(tmp_path, config, artifact, use_docker=False)

        assert (pkg / "model.gguf").read_bytes() == b"GGUF fake weights"
        script = (pkg / "deploy.sh").read_text()
        assert "chmod +x ./model.gguf && ./model.gguf" in script
        assert "docker" not in script

    def test_checksums_cover_every_file(self, tmp_path, config, artifact):
        pkg = build_package(tmp_path, config, artifact, use_docker=False)

        entries = dict(reversed(line.split("  ", 1)) for line in (pkg / "checksums.sha256").read_text().splitlines())
        files = {str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file() and p.name != "checksums.sha256"}
        assert set(entries) == files
        for name, digest in entries.items():
            assert hashlib.sha256((pkg / name).read_bytes()).hexdigest() == digest

    @pytest.mark.skipif(not shutil.which("sha256sum"), reason="sha256sum not available")
    def test_checksums_verify_with_sha256sum(self, tmp_path, config, artifact):
        pkg = build_package(tmp_path, config, artifact, use_docker=False)
        subprocess.run(["sha256sum", "-c", "--quiet", "checksums.sha256"], cwd=pkg, check=True)


class TestDockerPackages:

    def test_rag_enabled_bundles_qdrant_with_empty_storage(self, tmp_path, config, artifact):
        config.set("enable_rag_knowledge", True)
        client = FakeDockerClient()

        pkg = build_package(tmp_path, config, artifact, use_docker=True, docker_client=client)

        inference, qdrant = config.get("image_inference_runtime"), config.get("image_qdrant")
        assert client.pulled == [inference, qdrant]
        tarballs = sorted(p.name for p in (pkg / "images").iterdir())
        assert tarballs == sorted(f"{n.replace('/', '_').replace(':', '_')}.tar" for n in (inference, qdrant))

        storage = list((pkg / "data" / "qdrant").iterdir())
        assert [p.name for p in storage] == [".keep"] and storage[0].stat().st_size == 0

        script = (pkg / "deploy.sh").read_text()
        assert "qdrant-sidecar" in script and "QDRANT_HOST=qdrant-sidecar" in script
        assert inference in script and qdrant in script
        assert "--device /dev/dri --device /dev/mali0" in script

    def test_rag_disabled_has_no_qdrant(self, tmp_path, config, artifact):
        client = FakeDockerClient()

        pkg = build_package(tmp_path, config, artifact, use_docker=True, docker_client=client)

        assert client.pulled == [config.get("image_inference_runtime")]
        assert not (pkg / "data" / "qdrant").exists()
        script = (pkg / "deploy.sh").read_text()
        assert "qdrant" not in script
        assert config.get("image_inference_runtime") in script

    def test_without_docker_client_still_produces_script(self, tmp_path, config, artifact):
        pkg = build_package(tmp_path, config, artifact, use_docker=True, docker_client=None, profile="")
        script = (pkg / "deploy.sh").read_text()
        assert "docker run -d --name llm-node" in script
        assert "--device" not in script

    @pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
    @pytest.mark.parametrize("use_docker, rag", [(False, False), (True, False), (True, True)])
    def test_deploy_script_is_valid_bash(self, tmp_path, config, artifact, use_docker, rag):
        config.set("enable_rag_knowledge", rag)
        pkg = build_package(tmp_path, config, artifact, use_docker=use_docker, docker_client=FakeDockerClient())
        subprocess.run(["bash", "-n", str(pkg / "deploy.sh")], check=True)


class TestSSHDeployment:

    @pytest.fixture
    def dm(self, config, monkeypatch):
        dm = DeploymentManager(FakeFramework(config))
        monkeypatch.setattr(dm, "_check_conn", lambda *a, **k: pytest.fail("network reached despite invalid input"))
        return dm

    @pytest.mark.parametrize("ip", ["999.999.999.999", "not-an-ip", "", "10.0.0.1; reboot"])
    def test_invalid_ip_is_rejected_before_connecting(self, dm, artifact, ip):
        assert dm.deploy_artifact(artifact, ip, "root") is False

    @pytest.mark.parametrize("user", ["-oProxyCommand=touch /tmp/pwned", "root;reboot", "", "a b"])
    def test_invalid_user_is_rejected_before_connecting(self, dm, artifact, user):
        assert dm.deploy_artifact(artifact, "192.168.1.50", user) is False

    def test_remote_command_quotes_package_name(self):
        cmd = DeploymentManager._remote_install_cmd("/tmp/llm_deploy", "evil; rm -rf ~.zip")
        tokens = shlex.split(cmd)
        assert "evil; rm -rf ~.zip" in tokens

    def test_system_ssh_fallback_uses_argument_lists(self, config, tmp_path, monkeypatch):
        dm = DeploymentManager(FakeFramework(config))
        monkeypatch.setattr(dm, "_check_conn", lambda *a, **k: True)
        calls = []

        def fake_run(args, **kwargs):
            assert isinstance(args, list) and not kwargs.get("shell")
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

        monkeypatch.setattr("orchestrator.Core.deployment_manager.subprocess.run", fake_run)
        pkg = tmp_path / "deploy_model_1.zip"
        pkg.write_bytes(b"zip")

        assert dm.deploy_artifact(pkg, "192.168.1.50", "radxa") is True
        assert [c[0] for c in calls] == ["ssh", "scp", "ssh"]
        assert calls[1][2] == "radxa@192.168.1.50:/tmp/llm_deploy/"


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
