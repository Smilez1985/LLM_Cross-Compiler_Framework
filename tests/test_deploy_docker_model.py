"""The Docker branch of a package's deploy.sh hands the model to the inference container (stage C wave 3c-2, chain
boards; the cs-c4 integrator's open item).

The native branch copies the model to $MODEL_DIR (default ~/models) and the start script is given that path. The
Docker branch started the container without the model. Now it installs the model the same way and mounts that folder
read-only at /models (as Ditto's container server mounts its models), with MODEL_FILE naming the model there. The
generated script is run for real with a stand-in `docker` that records its arguments.
"""

import hashlib
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sha256sum") is None,
                           reason="deploy.sh runs on a POSIX board with sha256sum")


def script_of(model_name, flags="--device /dev/dri"):
    from orchestrator.Core.deployment_manager import DeploymentManager
    return DeploymentManager(SimpleNamespace())._generate_deploy_script(
        model_name, flags, True, "llm-framework/inference:local", {"llm-framework/inference:local":
                                                                   "llm-framework/inference:local"})


def test_the_docker_branch_installs_the_model_and_mounts_it_read_only():
    from orchestrator.runtime.container_server import MODELS_MOUNT
    script = script_of("qwen 0.5b.gguf")
    q = shlex.quote("qwen 0.5b.gguf")
    assert f'cp {q} "$MODEL_DIR/"' in script and 'MODEL_DIR="${MODEL_DIR:-$HOME/models}"' in script
    assert f'-v "$MODEL_DIR:{MODELS_MOUNT}:ro"' in script and f"-e MODEL_FILE={MODELS_MOUNT}/{q}" in script
    assert script.index("cp ") < script.index("docker run"), "installed before the container starts"


@POSIX
def test_the_generated_script_hands_the_model_to_the_container(tmp_path):
    """deploy.sh as the board runs it: in the unpacked package, with `sh`, a stand-in docker on PATH."""
    package = tmp_path / "package"
    package.mkdir()
    model = package / "tiny model.gguf"
    model.write_bytes(b"GGUF" + bytes(64))
    (package / "deploy.sh").write_text(script_of(model.name))
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    (package / "checksums.sha256").write_text(f"{digest}  {model.name}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "docker_calls"
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done >> "$DOCKER_CALLS"\n'
                      'echo "--" >> "$DOCKER_CALLS"\n')
    docker.chmod(0o755)
    home = tmp_path / "board-home"   # the board user's home
    home.mkdir()
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home), "DOCKER_CALLS": str(calls), "LANG": "C"}
    done = subprocess.run(["sh", "deploy.sh"], cwd=package, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
    installed = home / "models" / model.name
    assert installed.read_bytes() == model.read_bytes(), "the model is where the native branch puts it"
    run = calls.read_text().split("--\n")
    (node,) = [c.splitlines() for c in run if c.startswith("run\n")]
    assert f"{home / 'models'}:/models:ro" in node and f"MODEL_FILE=/models/{model.name}" in node
    assert node[node.index(f"{home / 'models'}:/models:ro") - 1] == "-v"
    assert "--pull" in node and node[-1] == "llm-framework/inference:local"
    assert Path(installed).stat().st_size == model.stat().st_size
