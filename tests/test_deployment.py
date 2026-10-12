"""DeploymentManager tests: package contents, Slim-RAG, Air-Gap images, deploy script, SSH safety."""

import glob
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import zipfile

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.deployment_manager import DeploymentManager


INFERENCE_PINNED = "ghcr.io/example/llm-runtime@sha256:" + "a" * 64
QDRANT_PINNED = "qdrant/qdrant@sha256:" + "b" * 64


class FakeImage:
    def __init__(self, name, repo_digests=None):
        self.name = name
        self.attrs = {"RepoDigests": list(repo_digests) if repo_digests is not None else [f"{name}@sha256:" + "c" * 64]}

    def save(self, named=True):
        yield f"tarball:{self.name}".encode()


class FakeDockerClient:
    """Images that are here: `local` (name -> RepoDigests; [] means built on this computer); pulled ones too."""

    def __init__(self, local=None, fail_pull=False):
        self.pulled = []
        self.images = self
        self.local = dict(local or {})
        self.fail_pull = fail_pull

    def pull(self, name):
        if self.fail_pull:
            raise RuntimeError("registry unreachable")
        self.pulled.append(name)
        self.local[name] = [name]

    def get(self, name):
        if name not in self.local:
            raise LookupError(f"no such image {name}")
        return FakeImage(name, self.local[name])


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
    cfg = ConfigManager(tmp_path / "configs")
    # Packages take only digest-pinned images or images built on this computer (MCP design G17).
    cfg.set("image_inference_runtime", INFERENCE_PINNED)
    cfg.set("image_qdrant", QDRANT_PINNED)
    return cfg


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
        assert "./model.gguf" not in script, "a model file must not be executed"
        assert 'cp model.gguf "$MODEL_DIR/"' in script
        assert "llama-server -m" in script
        assert "docker" not in script

    def test_checksums_cover_every_file(self, tmp_path, config, artifact):
        config.set("enable_rag_knowledge", True)
        pkg = build_package(tmp_path, config, artifact, use_docker=True, docker_client=FakeDockerClient())

        raw = (pkg / "checksums.sha256").read_bytes()
        assert b"\r" not in raw and b"\\" not in raw
        entries = dict(reversed(line.split("  ", 1)) for line in raw.decode().splitlines())
        files = {p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file() and p.name != "checksums.sha256"}
        assert set(entries) == files
        assert "data/qdrant/.keep" in entries
        for name, digest in entries.items():
            assert hashlib.sha256((pkg / name).read_bytes()).hexdigest() == digest

    @pytest.mark.skipif(not shutil.which("sha256sum"), reason="sha256sum not available")
    def test_checksums_verify_with_sha256sum(self, tmp_path, config, artifact):
        config.set("enable_rag_knowledge", True)
        pkg = build_package(tmp_path, config, artifact, use_docker=True, docker_client=FakeDockerClient())
        subprocess.run(["sha256sum", "-c", "--quiet", "checksums.sha256"], cwd=pkg, check=True)


class TestBuildHandoff:
    """Build output -> deployment: builds are found through their manifest, only model files are installed."""

    @pytest.fixture
    def build_dir(self, tmp_path):
        d = tmp_path / "output" / "Rockchip" / "SmolLM2" / "Q4_K_M"
        d.mkdir(parents=True)
        (d / "SmolLM2-Q4_K_M.gguf").write_bytes(b"GGUF weights")
        (d / "Model_Card.md").write_text("card")
        (d / "build_info.json").write_text(json.dumps({
            "model": "SmolLM2", "format": "gguf", "quantization": "Q4_K_M", "board": "rk3566",
            "created": "2026-10-10T12:00:00", "primary": "SmolLM2-Q4_K_M.gguf"}))
        shutil.make_archive(str(d.parent / "Q4_K_M"), "zip", d.parent, d.name)  # the golden ZIP next to it
        return d

    def test_builds_are_listed_from_manifests(self, tmp_path, build_dir):
        from orchestrator.Core.deployment_manager import list_builds
        builds = list_builds(tmp_path / "output")
        assert [b["path"] for b in builds] == [str(build_dir)]
        assert builds[0]["primary"] == "SmolLM2-Q4_K_M.gguf"

    def test_build_directory_deploys_its_primary_model(self, tmp_path, config, build_dir):
        pkg = build_package(tmp_path, config, build_dir, use_docker=False)
        assert (pkg / "SmolLM2-Q4_K_M.gguf").read_bytes() == b"GGUF weights"
        assert (pkg / "build_info.json").is_file() and (pkg / "Model_Card.md").is_file()
        script = (pkg / "deploy.sh").read_text()
        assert 'cp SmolLM2-Q4_K_M.gguf "$MODEL_DIR/"' in script
        assert "chmod +x" not in script

    def test_archive_is_never_executed(self, tmp_path, config, build_dir):
        dm = DeploymentManager(FakeFramework(config))
        assert dm.create_deployment_package(build_dir.parent / "Q4_K_M.zip", "", {}, tmp_path / "out") is None
        with pytest.raises(ValueError):
            dm._generate_deploy_script("tool.sh", "", use_docker=False)

    def test_manifest_cannot_point_outside_the_build(self, tmp_path, build_dir):
        from orchestrator.Core.deployment_manager import resolve_model_artifact
        secret = tmp_path / "output" / "secret.gguf"
        secret.write_bytes(b"x" * 100)
        info = json.loads((build_dir / "build_info.json").read_text())
        info["primary"] = "../../../secret.gguf"
        (build_dir / "build_info.json").write_text(json.dumps(info))
        assert resolve_model_artifact(build_dir) == build_dir / "SmolLM2-Q4_K_M.gguf"

    def test_non_string_primary_falls_back_to_the_model_file(self, tmp_path, build_dir):
        from orchestrator.Core.deployment_manager import list_builds, resolve_model_artifact
        info = json.loads((build_dir / "build_info.json").read_text())
        info["primary"] = 5
        (build_dir / "build_info.json").write_text(json.dumps(info))
        assert resolve_model_artifact(build_dir).name == "SmolLM2-Q4_K_M.gguf"
        assert list_builds(tmp_path / "output")[0]["primary"] is None

    def test_symlinked_subfolders_are_listed(self, tmp_path, build_dir):
        import os
        from orchestrator.Core.deployment_manager import list_builds
        if not hasattr(os, "symlink"):
            pytest.skip("no symlinks")
        out2 = tmp_path / "out2"
        out2.mkdir()
        try:
            (out2 / "Rockchip").symlink_to(tmp_path / "output" / "Rockchip", target_is_directory=True)
        except OSError:
            pytest.skip("symlinks not permitted")
        (out2 / "loop").symlink_to(out2, target_is_directory=True)
        assert len(list_builds(out2)) == 1

    def test_multi_file_builds_are_refused_honestly(self, tmp_path, config, build_dir):
        from orchestrator.Core.deployment_manager import resolve_model_artifact
        info = json.loads((build_dir / "build_info.json").read_text())
        info["multi_file"] = True
        (build_dir / "build_info.json").write_text(json.dumps(info))
        with pytest.raises(ValueError, match="multi-file"):
            resolve_model_artifact(build_dir)

    def test_llama_server_hint_stays_on_loopback(self, tmp_path, config, artifact):
        script = (build_package(tmp_path, config, artifact, use_docker=False) / "deploy.sh").read_text()
        assert "--host 127.0.0.1" in script
        assert "--api-key-file" in script


class TestDockerPackages:

    def test_rag_enabled_bundles_qdrant_with_empty_storage(self, tmp_path, config, artifact):
        config.set("enable_rag_knowledge", True)
        client = FakeDockerClient()

        pkg = build_package(tmp_path, config, artifact, use_docker=True, docker_client=client)

        inference, qdrant = config.get("image_inference_runtime"), config.get("image_qdrant")
        assert client.pulled == [inference, qdrant]
        tarballs = sorted(p.name for p in (pkg / "images").iterdir())
        assert tarballs == sorted(f"{n.replace('/', '_').replace(':', '_').replace('@', '_')}.tar"
                                  for n in (inference, qdrant))

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

    def test_without_docker_the_images_are_not_silently_left_out(self, tmp_path, config, artifact):
        """A package for an air-gapped device without its images would fail there: it is refused with the reason."""
        from orchestrator.Core.deployment_manager import DeployError
        dm = DeploymentManager(FakeFramework(config, None))
        with pytest.raises(DeployError) as e:
            dm.create_package(artifact, "", {"use_docker": True}, tmp_path / "out")
        assert e.value.code == "needs_person"
        assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())

    @pytest.mark.parametrize("image", ["ghcr.io/smilez1985/llm-runtime:latest", "qdrant/qdrant:v1.16.0",
                                       "example/runtime", "example/runtime@sha256:short"])
    def test_an_image_named_by_a_tag_is_refused_before_anything_is_pulled(self, tmp_path, config, artifact, image):
        """G17: a tag names whatever the registry serves today; only a digest or an image built here is packaged."""
        from orchestrator.Core.deployment_manager import DeployError
        config.set("image_inference_runtime", image)
        client = FakeDockerClient()
        dm = DeploymentManager(FakeFramework(config, client))
        with pytest.raises(DeployError, match="neither pinned by its digest") as e:
            dm.create_package(artifact, "", {"use_docker": True}, tmp_path / "out")
        assert e.value.code == "not_supported" and client.pulled == []
        assert dm.create_deployment_package(artifact, "", {"use_docker": True}, tmp_path / "out") is None

    def test_an_image_pulled_by_a_tag_earlier_is_still_refused(self, tmp_path, config, artifact):
        from orchestrator.Core.deployment_manager import DeployError
        config.set("image_inference_runtime", "example/runtime:1.0")
        client = FakeDockerClient(local={"example/runtime:1.0": ["example/runtime@sha256:" + "d" * 64]})
        with pytest.raises(DeployError):
            DeploymentManager(FakeFramework(config, client)).create_package(artifact, "", {"use_docker": True},
                                                                            tmp_path / "out")

    def test_an_image_built_on_this_computer_is_packaged_without_a_pull(self, tmp_path, config, artifact):
        config.set("image_inference_runtime", "llm-framework/inference:local")
        client = FakeDockerClient(local={"llm-framework/inference:local": []})
        pkg = build_package(tmp_path, config, artifact, use_docker=True, docker_client=client)
        assert client.pulled == []
        assert [p.name for p in (pkg / "images").iterdir()] == ["llm-framework_inference_local.tar"]

    def test_a_failed_pull_stops_the_package(self, tmp_path, config, artifact):
        from orchestrator.Core.deployment_manager import DeployError
        client = FakeDockerClient(fail_pull=True)
        with pytest.raises(DeployError, match="could not be packaged"):
            DeploymentManager(FakeFramework(config, client)).create_package(artifact, "", {"use_docker": True},
                                                                            tmp_path / "out")
        assert not list((tmp_path / "out").glob("*.zip"))

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

    def test_deploy_script_runs_through_sh_also_where_tmp_is_noexec(self, config, tmp_path):
        """The package is unpacked in /tmp/llm_deploy.*: executing ./deploy.sh there fails on boards that mount /tmp
        noexec (and on boards without bash); sh reads the script instead of executing it."""
        cmd = DeploymentManager._remote_install_cmd("/tmp/llm_deploy.x", "pkg.zip")
        assert "./deploy.sh" not in cmd and "chmod +x" not in cmd and cmd.endswith("&& sh deploy.sh")
        script = DeploymentManager(FakeFramework(config))._generate_deploy_script("model.gguf", "", False)
        assert script.startswith("#!/bin/sh\n")
        path = tmp_path / "deploy.sh"
        path.write_text(script)
        dash = shutil.which("dash") or shutil.which("sh")
        assert subprocess.run([dash, "-n", str(path)]).returncode == 0

    def test_system_ssh_fallback_never_prompts_or_accepts_keys(self, config, tmp_path, monkeypatch):
        dm = DeploymentManager(FakeFramework(config))
        monkeypatch.setattr(dm, "_check_conn", lambda *a, **k: True)
        monkeypatch.setattr("orchestrator.Core.deployment_manager.PARAMIKO_AVAILABLE", False)
        calls = []

        def fake_run(args, **kwargs):
            assert isinstance(args, list) and not kwargs.get("shell")
            # Over MCP stdio, an inherited stdin would forward the protocol stream to the device (G25).
            assert kwargs.get("stdin") is subprocess.DEVNULL
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="/tmp/llm_deploy.Ab12Cd34\n", stderr="")

        monkeypatch.setattr("orchestrator.Core.deployment_manager.subprocess.run", fake_run)
        pkg = tmp_path / "deploy_model_1.zip"
        pkg.write_bytes(b"zip")

        assert dm.deploy_artifact(pkg, "192.168.1.50", "radxa") is True
        assert [c[0] for c in calls] == ["ssh", "scp", "ssh"]
        assert calls[1][-1] == "radxa@192.168.1.50:/tmp/llm_deploy.Ab12Cd34/"
        for call in calls:
            assert "BatchMode=yes" in call and "StrictHostKeyChecking=yes" in call and "IdentitiesOnly=yes" in call

    def test_untrusted_device_gets_nothing(self, config, tmp_path, monkeypatch):
        pytest.importorskip("paramiko")
        import paramiko
        dm = DeploymentManager(FakeFramework(config))
        monkeypatch.setattr(dm, "_check_conn", lambda *a, **k: True)
        monkeypatch.setattr("orchestrator.utils.ssh_trust.fetch_host_key", lambda *a, **k: paramiko.ECDSAKey.generate())
        connected = []
        monkeypatch.setattr("orchestrator.utils.ssh_trust.connect", lambda *a, **k: connected.append(a))
        pkg = tmp_path / "deploy_model_1.zip"
        pkg.write_bytes(b"zip")
        assert dm.deploy_artifact(pkg, "192.168.1.50", "radxa", password="secret") is False
        assert connected == []



class TestBuildContext:
    """Target Dockerfiles COPY paths relative to the staged context; every source must exist there."""

    @pytest.mark.parametrize("dockerfile", sorted(
        str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "targets").glob("*/*") if p.name.lower().startswith("dockerfile")))
    def test_every_copy_source_exists_in_staged_context(self, tmp_path, dockerfile):
        from orchestrator.utils.build_context import stage_build_context

        source = REPO_ROOT / dockerfile
        staged = stage_build_context(source.parent, REPO_ROOT, tmp_path / "ctx", source.name)

        instructions = re.sub(r"\\\n", " ", staged.read_text()).splitlines()
        checked = 0
        for line in instructions:
            parts = line.split(None, 1)
            if len(parts) < 2 or parts[0].upper() not in ("COPY", "ADD"):
                continue
            args = parts[1].strip()
            flags = re.findall(r"--\S+", args)
            if any(f.startswith("--from") for f in flags):
                continue
            args = re.sub(r"--\S+\s*", "", args).strip()
            paths = json.loads(args) if args.startswith("[") else args.split()
            for src in paths[:-1]:
                assert glob.glob(str(staged.parent / src)), f"{dockerfile}: '{line.strip()}' has no '{src}' in the build context"
                checked += 1
        assert checked > 0

    def test_context_layout(self, tmp_path):
        from orchestrator.utils.build_context import stage_build_context

        staged = stage_build_context(REPO_ROOT / "targets" / "Rockchip", REPO_ROOT, tmp_path / "ctx")

        assert staged == tmp_path / "ctx" / "Dockerfile"
        assert (tmp_path / "ctx" / "docker" / "entrypoint.sh").read_bytes() == (REPO_ROOT / "entrypoint.sh").read_bytes()
        assert (tmp_path / "ctx" / "modules" / "build.sh").exists()
        assert (tmp_path / "ctx" / "scripts" / "rknn_converter.py").exists()

    def test_refuses_to_overwrite_a_non_empty_directory(self, tmp_path):
        from orchestrator.utils.build_context import stage_build_context

        (tmp_path / "ctx").mkdir()
        (tmp_path / "ctx" / "keep.txt").write_text("user data")
        with pytest.raises(FileExistsError):
            stage_build_context(REPO_ROOT / "targets" / "Rockchip", REPO_ROOT, tmp_path / "ctx")
        assert (tmp_path / "ctx" / "keep.txt").read_text() == "user data"

    def test_gpu_dockerfile_is_preferred_when_present(self, tmp_path):
        from orchestrator.utils.build_context import stage_build_context

        staged = stage_build_context(REPO_ROOT / "targets" / "_template", REPO_ROOT, tmp_path / "ctx", "Dockerfile.gpu")
        assert staged.read_bytes() == (REPO_ROOT / "targets" / "_template" / "Dockerfile.gpu").read_bytes()
