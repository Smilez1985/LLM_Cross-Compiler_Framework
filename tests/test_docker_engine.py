"""The container engine the framework talks to (DECISIONS round 26 Q1, orchestrator/utils/docker_engine.py).

Docker CE is the standard; on Rocky Linux, AlmaLinux and Oracle Linux install.sh falls back to Podman (podman-docker and
the person's rootless podman.socket). Without a Docker daemon the framework sets DOCKER_HOST to that socket itself, with
one helper that every place connecting to Docker calls, and it refuses early, in plain words with the way to Docker CE,
what Podman does not provide (Docker's GPU request, BuildKit). The doctor names the engine.

The sockets here are real Unix sockets in a short temporary folder (the 108-byte limit of socket paths); the /version
answers are the real ones of Docker 29.8.2 and Podman 5.8.2 (Rocky Linux 9.8, 2026-10-23).
"""

import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import types
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from orchestrator.utils import docker_engine
from orchestrator.utils.docker_engine import EngineChoice, ensure_docker_host

POSIX_SOCKETS = pytest.mark.skipif(not sys.platform.startswith("linux") or not hasattr(socket, "AF_UNIX"),
                                   reason="the Podman fallback and its Unix sockets are Linux")

PODMAN_VERSION = {"Platform": {"Name": "linux/amd64/rocky-9.8"}, "Version": "5.8.2", "ApiVersion": "1.41",
                  "Components": [{"Name": "Podman Engine", "Version": "5.8.2"}, {"Name": "Conmon"},
                                 {"Name": "OCI Runtime (crun)"}]}
DOCKER_VERSION = {"Platform": {"Name": "Docker Engine - Community"}, "Version": "29.8.2", "ApiVersion": "1.56",
                  "Components": [{"Name": "Engine", "Version": "29.8.2"}, {"Name": "containerd"}, {"Name": "runc"},
                                 {"Name": "docker-init"}]}


@pytest.fixture
def short_dir():
    folder = Path(tempfile.mkdtemp(prefix="de-", dir="/tmp" if os.path.isdir("/tmp") else None))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def listening(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(4)
    return server


def dead(path: Path) -> None:
    """A socket file nobody listens on (a stopped daemon leaves one)."""
    listening(path).close()


class FakeClient:
    def __init__(self, version=None, error=None):
        self._version, self._error, self.asked = version, error, 0

    def version(self):
        self.asked += 1
        if self._error:
            raise self._error
        return self._version

    def ping(self):
        return True


# --- the choice ------------------------------------------------------------------------------------------------------

@POSIX_SOCKETS
class TestTheChoice:

    def test_a_docker_host_the_person_set_always_wins(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        env = {"DOCKER_HOST": "tcp://192.0.2.10:2375", "XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        assert choice == EngineChoice("tcp://192.0.2.10:2375", False, "DOCKER_HOST is set")
        assert env["DOCKER_HOST"] == "tcp://192.0.2.10:2375"
        podman.close()

    def test_without_a_docker_daemon_the_persons_podman_socket_is_used(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        dead(short_dir / "docker.sock")   # a stopped Docker leaves its socket file behind
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        assert choice.set_here and env["DOCKER_HOST"] == f"unix://{short_dir}/run/podman/podman.sock"
        assert choice.docker_host == env["DOCKER_HOST"] and "Podman" in choice.reason
        assert str(short_dir) not in choice.reason, "the reason names no path"
        podman.close()

    def test_a_running_docker_daemon_keeps_docker(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        docker = listening(short_dir / "docker.sock")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        assert not choice.set_here and "DOCKER_HOST" not in env
        podman.close(), docker.close()

    def test_a_docker_daemon_that_refuses_this_user_still_counts(self, short_dir, monkeypatch):
        """Not in the group docker yet (a new login fixes it): Docker is there, and Podman is not chosen."""
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        docker = listening(short_dir / "docker.sock")
        real_connect = socket.socket.connect

        def connect(self, address):
            if str(address).endswith("docker.sock"):
                raise PermissionError(13, "Permission denied")
            return real_connect(self, address)
        monkeypatch.setattr(socket.socket, "connect", connect)
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        assert not ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")]).set_here
        podman.close(), docker.close()

    def test_a_rootless_docker_keeps_docker(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        rootless = listening(short_dir / "run" / "docker.sock")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        assert not ensure_docker_host(env, docker_sockets=[str(short_dir / "none.sock")]).set_here
        podman.close(), rootless.close()

    def test_podman_dockers_socket_links_are_podman_not_docker(self, short_dir):
        """podman-docker's systemd-tmpfiles entries link /run/docker.sock to the root Podman socket and
        $XDG_RUNTIME_DIR/docker.sock to the person's (Rocky Linux 9.8, podman-docker 5.8.2): neither is a Docker
        daemon, so the person's Podman socket is chosen."""
        rootful = listening(short_dir / "rootful" / "podman" / "podman.sock")
        rootless = listening(short_dir / "run" / "podman" / "podman.sock")
        (short_dir / "docker.sock").symlink_to(short_dir / "rootful" / "podman" / "podman.sock")
        (short_dir / "run" / "docker.sock").symlink_to(short_dir / "run" / "podman" / "podman.sock")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        assert choice.set_here and env["DOCKER_HOST"] == f"unix://{short_dir}/run/podman/podman.sock"
        rootful.close(), rootless.close()

    def test_a_docker_socket_where_podman_answers_is_no_docker(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        impostor = listening(short_dir / "docker.sock")

        def answer():
            conn, _ = impostor.accept()   # the connection check
            conn.close()
            conn, _ = impostor.accept()   # GET /version
            with conn:
                conn.recv(65536)
                conn.sendall(("HTTP/1.0 200 OK\r\n\r\n" + json.dumps(PODMAN_VERSION)).encode())
        worker = threading.Thread(target=answer, daemon=True)
        worker.start()
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        assert ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")]).set_here
        worker.join(5)
        podman.close(), impostor.close()

    def test_neither_engine_changes_nothing(self, short_dir):
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        choice = ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        assert not choice.set_here and env == {"XDG_RUNTIME_DIR": str(short_dir / "run")}

    def test_a_regular_file_is_no_socket(self, short_dir):
        (short_dir / "run" / "podman").mkdir(parents=True)
        (short_dir / "run" / "podman" / "podman.sock").write_text("not a socket")
        env = {"XDG_RUNTIME_DIR": str(short_dir / "run")}
        assert not ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")]).set_here

    def test_the_process_decides_once_and_child_processes_inherit(self, short_dir, monkeypatch):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        monkeypatch.setattr(docker_engine, "_CHOICE", None)
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
        first = ensure_docker_host(docker_sockets=[str(short_dir / "docker.sock")])
        assert first.set_here and os.environ["DOCKER_HOST"] == first.docker_host
        out = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('DOCKER_HOST', ''))"],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == first.docker_host, "a process the framework starts talks to the same engine"
        podman.close()
        assert ensure_docker_host() is first, "decided once per process"


# --- which engine answers ------------------------------------------------------------------------------------------

class TestTheEngineName:

    def test_podmans_version_names_podman_and_dockers_names_docker(self):
        assert docker_engine.engine_of_version(PODMAN_VERSION) == "podman"
        assert docker_engine.engine_of_version(DOCKER_VERSION) == "docker"
        assert docker_engine.engine_of_version({}) == ""

    def test_a_client_is_asked_once(self):
        client = FakeClient(PODMAN_VERSION)
        assert docker_engine.engine_name(client) == "podman" == docker_engine.engine_name(client)
        assert client.asked == 1

    def test_no_client_or_no_answer_is_unknown(self):
        assert docker_engine.engine_name(None) == ""
        client = FakeClient(error=ConnectionError("refused"))
        assert docker_engine.engine_name(client) == "" and docker_engine.engine_name(client) == ""
        assert client.asked == 2, "an engine that did not answer is asked again"

    @POSIX_SOCKETS
    @pytest.mark.parametrize("status, body, expected", [
        ("200 OK", json.dumps(PODMAN_VERSION), "podman"), ("200 OK", json.dumps(DOCKER_VERSION), "docker"),
        ("500 Internal Server Error", json.dumps(PODMAN_VERSION), ""), ("200 OK", "not json", "")])
    def test_the_script_asks_over_the_socket_without_docker_py(self, short_dir, status, body, expected):
        server = listening(short_dir / "api.sock")

        def answer():
            conn, _ = server.accept()
            with conn:
                conn.recv(65536)
                conn.sendall(f"HTTP/1.0 {status}\r\nContent-Type: application/json\r\n\r\n{body}".encode())
        worker = threading.Thread(target=answer, daemon=True)
        worker.start()
        assert docker_engine.engine_at(f"unix://{short_dir}/api.sock") == expected
        worker.join(5)
        server.close()

    def test_no_socket_answers_nothing(self, short_dir):
        assert docker_engine.engine_at(f"unix://{short_dir}/none.sock") == ""
        assert docker_engine.engine_at("tcp://192.0.2.10:2375") == ""

    @POSIX_SOCKETS
    def test_the_command_line_prints_the_host_and_nothing_else(self, short_dir, monkeypatch, capsys):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        monkeypatch.delenv("DOCKER_HOST", raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir / "run"))
        monkeypatch.setattr(docker_engine, "DOCKER_SOCKETS", (str(short_dir / "docker.sock"),))
        assert docker_engine.main(["--docker-host"]) == 0
        assert capsys.readouterr().out == f"unix://{short_dir}/run/podman/podman.sock\n"
        assert "DOCKER_HOST" not in os.environ, "the command line only prints"
        podman.close()

    def test_the_helper_runs_alone_under_an_isolated_python(self):
        """scripts/build_container_image.sh runs it with the system's python3 -I, without the framework's packages."""
        tree = ast.parse((REPO_ROOT / "orchestrator" / "utils" / "docker_engine.py").read_text(encoding="utf-8"),
                         feature_version=(3, 9))
        imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module and node.col_offset == 0}
        assert imported <= set(sys.stdlib_module_names), imported
        run = subprocess.run([sys.executable, "-I", str(REPO_ROOT / "orchestrator" / "utils" / "docker_engine.py"),
                              "--docker-host"], capture_output=True, text=True, timeout=30,
                             env={"PATH": os.environ.get("PATH", ""), "DOCKER_HOST": "unix:///run/example.sock"})
        assert run.returncode == 0 and run.stdout == "\n", run.stderr


# --- what Podman cannot do, refused early ------------------------------------------------------------------------------

class TestRefusals:

    def test_docker_refuses_nothing(self):
        assert docker_engine.podman_refusal("gpu", "docker") is None
        assert docker_engine.podman_refusal("buildkit", "") is None

    @pytest.mark.parametrize("feature, words", [("gpu", "GPU"), ("buildkit", "BuildKit")])
    def test_podman_refuses_with_the_reason_and_the_way_to_docker_ce(self, feature, words):
        text = docker_engine.podman_refusal(feature, "podman")
        assert text.startswith("Under Podman this does not work: ") and words in text
        assert "Docker CE is the standard" in text and "sudo dnf remove podman-docker" in text
        assert "https://docs.docker.com/engine/install/" in text

    def _service(self, client, monkeypatch, nvidia=True):
        from orchestrator.Core.build_service import BuildService
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/nvidia-smi" if nvidia else None)
        service = types.SimpleNamespace(framework=types.SimpleNamespace(get_component=lambda name: client))
        return lambda request: BuildService._refuse_what_the_engine_cannot(service, request)

    def test_an_nvidia_gpu_build_under_podman_is_refused_before_it_is_queued(self, monkeypatch):
        from orchestrator.Core.build_service import BuildServiceError
        refuse = self._service(FakeClient(PODMAN_VERSION), monkeypatch)
        with pytest.raises(BuildServiceError) as caught:
            refuse(types.SimpleNamespace(use_gpu=True))
        assert caught.value.code == "not_supported" and caught.value.field == "use_gpu"
        assert "without a word" in str(caught.value) and "Docker CE" in str(caught.value)

    @pytest.mark.parametrize("client, use_gpu, nvidia", [
        (FakeClient(DOCKER_VERSION), True, True), (FakeClient(PODMAN_VERSION), False, True),
        (FakeClient(PODMAN_VERSION), True, False), (None, True, True)],
        ids=["docker", "no-gpu", "no-nvidia", "no-client"])
    def test_everything_else_goes_on(self, monkeypatch, client, use_gpu, nvidia):
        self._service(client, monkeypatch, nvidia)(types.SimpleNamespace(use_gpu=use_gpu))

    def test_the_validated_request_carries_the_check(self):
        """BuildService._validated (validate, start_build, the dry run) runs the engine check, so the refusal comes
        before a build is queued."""
        source = (REPO_ROOT / "orchestrator" / "Core" / "build_service.py").read_text(encoding="utf-8")
        body = source[source.index("    def _validated("):source.index("    def _refuse_what_the_engine_cannot(")]
        assert "self._refuse_what_the_engine_cannot(request)" in body

    def test_the_builder_never_runs_a_gpu_build_on_the_cpu_under_podman(self):
        from orchestrator.Core.builder import BuildEngine
        engine = types.SimpleNamespace(docker_client=FakeClient(PODMAN_VERSION))
        with pytest.raises(RuntimeError, match="Under Podman this does not work"):
            BuildEngine._refuse_gpu_under_podman(engine)
        BuildEngine._refuse_gpu_under_podman(types.SimpleNamespace(docker_client=FakeClient(DOCKER_VERSION)))

    def test_under_podman_images_are_built_through_its_api(self, monkeypatch):
        """Podman's 'docker buildx' is buildah, or Docker's CLI asking Podman for BuildKit: the builder uses
        docker-py's api.build, which Podman's builder answers (build arguments, labels and target stages work)."""
        from orchestrator.Core.builder import BuildEngine
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker")
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
        assert not BuildEngine._buildkit_available(types.SimpleNamespace(docker_client=FakeClient(PODMAN_VERSION)))
        assert BuildEngine._buildkit_available(types.SimpleNamespace(docker_client=FakeClient(DOCKER_VERSION)))

    def test_dittos_container_gets_no_nvidia_gpu_under_podman_and_says_why(self):
        from orchestrator.runtime import container_server as cs
        docker = types.SimpleNamespace(engine=lambda: "podman", info=lambda: {"Runtimes": {"nvidia": {}}},
                                       gpu_request=lambda: object())
        hardware = types.SimpleNamespace(gpus=[types.SimpleNamespace(vendor="nvidia")])
        gpu = cs.gpu_passthrough(docker, hardware, "cuda", dri=Path("/nonexistent-dri"))
        assert gpu.kind == "none" and "under Podman" in gpu.note and "Docker CE" in gpu.note
        docker.engine = lambda: "docker"
        assert cs.gpu_passthrough(docker, hardware, "cuda", dri=Path("/nonexistent-dri")).kind == "nvidia"


# --- the one Docker check and the doctor ---------------------------------------------------------------------------

class TestTheDockerCheck:

    def test_the_check_names_podman_and_does_not_ask_for_buildx(self, monkeypatch):
        from orchestrator.utils import validation
        asked = []
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker")
        monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: asked.append(args) or
                            subprocess.CompletedProcess(args, 0, "", ""))
        found = validation.check_docker_environment(FakeClient(PODMAN_VERSION))
        assert found == validation.DockerEnvironment(True, "", True, None, "podman") and asked == []

    def test_docker_is_named_too(self, monkeypatch):
        from orchestrator.utils import validation
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker")
        monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: subprocess.CompletedProcess(args, 0, "", ""))
        assert validation.check_docker_environment(FakeClient(DOCKER_VERSION)).engine == "docker"

    def _mojo(self, client, monkeypatch):
        from orchestrator.Core.mojo.checks import docker as docker_check
        monkeypatch.setattr(docker_check, "_storage_findings", lambda framework: [])
        monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker")
        monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: subprocess.CompletedProcess(args, 0, "", ""))
        return docker_check.check_docker(types.SimpleNamespace(get_component=lambda name: client))

    def test_the_doctor_names_podman_and_its_limits(self, monkeypatch):
        daemon, engine = self._mojo(FakeClient(PODMAN_VERSION), monkeypatch)
        assert (daemon.code, daemon.severity.value) == ("docker.daemon", "ok") and "Podman" in daemon.message
        assert (engine.code, engine.severity.value) == ("docker.engine", "warn")
        assert "GPU" in engine.message and "BuildKit" in engine.message
        assert "sudo dnf remove podman-docker" in engine.fix_hint and engine.where and engine.why
        assert engine.cannot == "admin"

    def test_the_doctor_names_docker(self, monkeypatch):
        findings = self._mojo(FakeClient(DOCKER_VERSION), monkeypatch)
        assert [f.code for f in findings] == ["docker.daemon", "docker.buildx"]
        assert "container engine: Docker" in findings[0].message


# --- rootless Podman: a container that runs as the person writes the person's folders --------------------------------
# Checked with Podman 5.8.9 rootless through its Docker-compatible API (2026-10-23): a container created with
# "User": "1000:1000" and a bind mount of a folder of that person sees the folder as owned by root and cannot write it
# ("Permission denied"); with HostConfig.UsernsMode "keep-id" it writes, and the file belongs to the person on the host.
# A label or annotation does not do it. docker-py's create() refuses any userns_mode but "host".

PODMAN_ROOTLESS_INFO = {"SecurityOptions": ["name=seccomp,profile=default", "name=rootless"]}


class RealApiClient:
    """A real docker-py client whose low-level create request is recorded instead of sent."""

    def __init__(self, monkeypatch, version=PODMAN_VERSION, info=PODMAN_ROOTLESS_INFO):
        import docker
        from docker.models.containers import ContainerCollection
        self.client = docker.DockerClient(base_url="unix:///nonexistent/docker.sock", version="1.41")
        self.created = []
        self.client.api.create_container = lambda **kw: self.created.append(kw) or {"Id": "c0ffee"}
        self.client.version = lambda: version
        self.client.info = lambda: info
        self.container = types.SimpleNamespace(
            id="c0ffee", start=lambda: None, kill=lambda: None, remove=lambda force=False: None,
            logs=lambda **kw: iter([b"done\n"]) if kw.get("stream") else b"done\n",
            wait=lambda timeout=None: {"StatusCode": 0})
        monkeypatch.setattr(ContainerCollection, "get", lambda collection, cid: self.container)

    @property
    def host_config(self):
        assert len(self.created) == 1, self.created
        return self.created[0]["host_config"]


class TestRootlessPodmanKeepsThePersonsUid:

    def _sandboxed_run(self, client, tmp_path):
        from orchestrator.runtime.build.executor import DockerEngine, Mount
        from orchestrator.utils.sandbox import container_sandbox
        engine = DockerEngine()
        engine._client = client
        (tmp_path / "out").mkdir()
        status = engine.run("sha256:" + "0" * 64, ["true"], mounts=[Mount(tmp_path / "out", "/work/build", False)],
                            env={"LANG": "C"}, options=container_sandbox((1000, 1000), working_dir="/work"),
                            log=tmp_path / "build.log", timeout=30)
        assert status == 0

    def test_the_builder_container_under_rootless_podman_keeps_the_persons_uid(self, monkeypatch, tmp_path):
        """The llama.cpp builder container (runtime/build/executor.DockerEngine.run), like every sandboxed container
        of the framework, runs as the person's uid with the person's folders mounted: under rootless Podman it gets
        keep-id, or it could not write them."""
        api = RealApiClient(monkeypatch)
        self._sandboxed_run(api.client, tmp_path)
        assert api.host_config.get("UsernsMode") == "keep-id", api.host_config
        assert api.created[0]["user"] == "1000:1000" and api.host_config["ReadonlyRootfs"] is True
        assert api.host_config["Binds"] == [f"{tmp_path / 'out'}:/work/build:rw"]
        assert api.host_config["CapDrop"] == ["ALL"] and api.host_config["NetworkMode"] == "none"

    @pytest.mark.parametrize("version, info", [(DOCKER_VERSION, {"SecurityOptions": ["name=rootless"]}),
                                               (PODMAN_VERSION, {"SecurityOptions": ["name=seccomp"]})],
                             ids=["docker-rootless", "podman-rootful"])
    def test_docker_and_rootful_podman_get_no_user_namespace_mode(self, monkeypatch, tmp_path, version, info):
        """Docker (rootless or not) has no keep-id and refuses it; rootful Podman needs none."""
        api = RealApiClient(monkeypatch, version=version, info=info)
        self._sandboxed_run(api.client, tmp_path)
        assert "UsernsMode" not in api.host_config, api.host_config

    def test_a_container_that_runs_as_root_gets_no_keep_id(self, monkeypatch):
        """Root in a rootless Podman container is the person already (the legacy build mode, Trivy)."""
        api = RealApiClient(monkeypatch)
        docker_engine.create_container(api.client, image="example", command=["true"], user="0:0")
        assert "UsernsMode" not in api.host_config

    def test_dockers_own_create_is_used_unchanged_elsewhere(self):
        calls = []
        client = types.SimpleNamespace(version=lambda: DOCKER_VERSION,
                                       containers=types.SimpleNamespace(create=lambda **kw: calls.append(kw) or "c"))
        assert docker_engine.create_container(client, image="example", command=["x"], user="1000:1000",
                                              volumes={"/a": {"bind": "/b", "mode": "rw"}}) == "c"
        assert calls == [{"image": "example", "command": ["x"], "user": "1000:1000",
                          "volumes": {"/a": {"bind": "/b", "mode": "rw"}}}]

    def test_rootless_is_asked_once_per_client(self, monkeypatch):
        api = RealApiClient(monkeypatch)
        asked = []
        api.client.info = lambda: asked.append(1) or PODMAN_ROOTLESS_INFO
        assert docker_engine.rootless_podman(api.client) and docker_engine.rootless_podman(api.client)
        assert asked == [1]

    def test_a_podman_that_does_not_answer_info_is_not_taken_for_rootless(self):
        client = FakeClient(PODMAN_VERSION)
        client.info = lambda: (_ for _ in ()).throw(ConnectionError("gone"))
        assert docker_engine.rootless_podman(client) is False


def test_every_framework_container_is_created_through_the_one_helper():
    """docker_engine.create_container is the one place that creates a container of the framework through docker-py's
    containers.create (the build stages, the llama.cpp builder, Ditto's check and server containers), so rootless
    Podman's keep-id cannot be missed by a new site."""
    sites = []
    for path in sorted((REPO_ROOT / "orchestrator").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create"
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "containers"):
                sites.append(path.relative_to(REPO_ROOT).as_posix())
    assert sites == ["orchestrator/utils/docker_engine.py"], sites


# --- every place that connects to Docker ---------------------------------------------------------------------------

def test_every_docker_client_is_made_after_the_one_helper():
    """docker.from_env() reads DOCKER_HOST: each place that makes a client calls ensure_docker_host() first (the build
    engine, the builder image's container engine, Ditto's container engine), never a copy of the decision."""
    sites = []
    for path in sorted((REPO_ROOT / "orchestrator").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = [node for node in ast.walk(func) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == "from_env"
                     and getattr(node.func.value, "id", "") == "docker"]
            if calls:
                helper = [node for node in ast.walk(func) if isinstance(node, ast.Call)
                          and getattr(node.func, "id", getattr(node.func, "attr", "")) == "ensure_docker_host"]
                sites.append((path.relative_to(REPO_ROOT).as_posix(), func.name,
                              bool(helper) and min(h.lineno for h in helper) < min(c.lineno for c in calls)))
    assert sorted(sites) == [("orchestrator/Core/builder.py", "__init__", True),
                             ("orchestrator/runtime/build/executor.py", "client", True),
                             ("orchestrator/runtime/container_server.py", "client", True)]


def test_the_build_engine_calls_the_helper_when_it_starts(monkeypatch):
    from orchestrator.Core import builder
    seen = []
    monkeypatch.setattr(builder, "ensure_docker_host", lambda: seen.append(1) or EngineChoice("", False, "tests"))
    monkeypatch.setattr(builder.docker, "from_env", lambda: (_ for _ in ()).throw(ConnectionError("no daemon")))
    config = types.SimpleNamespace(get=lambda key, default=None: default)
    builder.BuildEngine(config, base_dir=str(REPO_ROOT))
    assert seen == [1]


def test_the_container_engines_call_the_helper_before_their_client(monkeypatch):
    from orchestrator.runtime import container_server
    from orchestrator.runtime.build import executor
    seen = []
    monkeypatch.setattr(docker_engine, "ensure_docker_host", lambda: seen.append("helper"))
    import docker
    monkeypatch.setattr(docker, "from_env", lambda **kw: seen.append("client") or FakeClient(PODMAN_VERSION))
    assert executor.DockerEngine().engine() == "podman"
    assert container_server.DittoDocker().client() is not None
    assert seen == ["helper", "client", "helper", "client"]


# --- scripts/build_container_image.sh ------------------------------------------------------------------------------

needs_bash = pytest.mark.skipif(os.name == "nt" or not Path("/bin/bash").exists() or not shutil.which("git"),
                                reason="the builder script needs bash and git")

FAKE_DOCKER = r"""#!/bin/bash
echo "ARGV: $* DOCKER_HOST=${DOCKER_HOST:-} DOCKER_BUILDKIT=${DOCKER_BUILDKIT:-}" >> "$LOG"
case "$1" in
  --version) [ -n "$DOCKER_SAYS" ] && echo "$DOCKER_SAYS"; exit 0 ;;
  info) exit 0 ;;
  buildx) exit "${DOCKER_BUILDX_EXIT:-0}" ;;
esac
exit 0
"""
# The system's python3 asked for the framework's choice: prints what the real helper would print here.
FAKE_PYTHON = r"""#!/bin/bash
case "$3" in
  --docker-host) echo "${FAKE_HOST:-}" ;;
  --engine) echo "${FAKE_ENGINE:-}" ;;
esac
"""


@needs_bash
class TestTheImageScript:

    def run(self, tmp_path, python=True, **env):
        from test_container_setup import _fake_repo
        repo = _fake_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name, text in (("docker", FAKE_DOCKER),) + ((("python3", FAKE_PYTHON),) if python else ()):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        for tool in ("git", "tar", "find", "sort", "mktemp", "rm", "id", "tr", "wc", "sed", "dirname", "cat", "bash",
                     "grep"):
            found = shutil.which(tool)
            if found and not (bin_dir / tool).exists():
                (bin_dir / tool).symlink_to(found)
        log = tmp_path / "docker.log"
        run = subprocess.run(["/bin/bash", str(repo / "scripts" / "build_container_image.sh")],
                             env={"PATH": str(bin_dir), "HOME": str(tmp_path), "LOG": str(log), "TMPDIR": str(tmp_path),
                                  **env}, capture_output=True, text=True, timeout=120)
        return run, log.read_text() if log.exists() else ""

    def test_without_a_docker_daemon_it_builds_through_podmans_socket(self, tmp_path):
        host = "unix:///run/user/1000/podman/podman.sock"
        run, calls = self.run(tmp_path, FAKE_HOST=host, FAKE_ENGINE="podman", DOCKER_BUILDX_EXIT="1")
        assert run.returncode == 0, run.stdout + run.stderr
        build = next(line for line in calls.splitlines() if line.startswith("ARGV: build "))
        assert f"DOCKER_HOST={host}" in build and "DOCKER_BUILDKIT=0" in build
        assert "Podman" in run.stdout and "ARGV: buildx" not in calls, "Podman is never asked for BuildKit"

    def test_podman_docker_is_recognised_by_its_command(self, tmp_path):
        run, calls = self.run(tmp_path, python=False, DOCKER_SAYS="podman version 5.8.2", DOCKER_BUILDX_EXIT="1")
        assert run.returncode == 0, run.stdout + run.stderr
        assert "DOCKER_BUILDKIT=0" in next(line for line in calls.splitlines() if line.startswith("ARGV: build "))

    def test_docker_keeps_buildkit_and_a_docker_host_you_set_wins(self, tmp_path):
        run, calls = self.run(tmp_path, FAKE_HOST="unix:///run/user/1000/podman/podman.sock", FAKE_ENGINE="docker",
                              DOCKER_HOST="unix:///run/example/docker.sock")
        assert run.returncode == 0, run.stdout + run.stderr
        build = next(line for line in calls.splitlines() if line.startswith("ARGV: build "))
        assert "DOCKER_HOST=unix:///run/example/docker.sock" in build and "DOCKER_BUILDKIT=1" in build
        assert "ARGV: buildx version" in calls


# --- follow-ups: a DOCKER_HOST left from Podman, /dev/dri under rootless Podman, rootless Docker ----------------------

@POSIX_SOCKETS
class TestADockerHostLeftFromPodman:
    """DOCKER_HOST still points at a Podman socket while a Docker daemon runs (an earlier Podman session): the person's
    DOCKER_HOST still wins (_decide), and Mojo names it with the way out."""

    def test_it_is_seen_when_docker_runs(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        docker = listening(short_dir / "docker.sock")
        env = {"DOCKER_HOST": f"unix://{short_dir}/run/podman/podman.sock", "XDG_RUNTIME_DIR": str(short_dir / "run")}
        assert docker_engine.stale_podman_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        choice = ensure_docker_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        assert choice == EngineChoice(env["DOCKER_HOST"], False, "DOCKER_HOST is set"), "the person's setting wins"
        podman.close(), docker.close()

    def test_not_without_a_docker_daemon_or_with_a_docker_host_of_docker(self, short_dir):
        podman = listening(short_dir / "run" / "podman" / "podman.sock")
        dead(short_dir / "docker.sock")
        env = {"DOCKER_HOST": f"unix://{short_dir}/run/podman/podman.sock"}
        assert not docker_engine.stale_podman_host(env, docker_sockets=[str(short_dir / "docker.sock")])
        docker = listening(short_dir / "real.sock")
        for host in (f"unix://{short_dir}/real.sock", "tcp://192.0.2.10:2375", ""):
            assert not docker_engine.stale_podman_host({"DOCKER_HOST": host},
                                                       docker_sockets=[str(short_dir / "real.sock")])
        podman.close(), docker.close()


class TestMojoNamesADockerHostLeftFromPodman:

    @pytest.mark.parametrize("version", [DOCKER_VERSION, PODMAN_VERSION], ids=["docker", "podman"])
    def test_with_the_way_out(self, monkeypatch, version):
        monkeypatch.setattr(docker_engine, "stale_podman_host", lambda *a, **k: True)
        findings = TestTheDockerCheck()._mojo(FakeClient(version), monkeypatch)
        stale = [f for f in findings if f.code == "docker.host_stale"]
        assert len(stale) == 1 and stale[0].severity.value == "warn", findings
        assert "Podman socket" in stale[0].message and "~/.bashrc" in stale[0].fix_hint
        assert stale[0].where and stale[0].why and stale[0].cannot == "person"

    def test_nothing_without_it(self, monkeypatch):
        monkeypatch.setattr(docker_engine, "stale_podman_host", lambda *a, **k: False)
        findings = TestTheDockerCheck()._mojo(FakeClient(DOCKER_VERSION), monkeypatch)
        assert "docker.host_stale" not in [f.code for f in findings]


class TestRenderNodeUnderRootlessPodman:
    """Podman's --group-add keep-groups passes the person's own groups on (its documentation of --group-add); the
    host's numeric render gid is not mapped into a rootless user namespace. keep-id stays."""

    def test_dev_dri_gets_keep_groups_and_keep_id(self, monkeypatch):
        api = RealApiClient(monkeypatch)
        docker_engine.create_container(api.client, image="example", command=["true"], user="1000:1000",
                                       devices=["/dev/dri:/dev/dri"], group_add=["44", "105"])
        assert api.host_config["GroupAdd"] == ["keep-groups"] and api.host_config["UsernsMode"] == "keep-id"

    def test_as_root_too(self):
        calls = []
        client = types.SimpleNamespace(version=lambda: PODMAN_VERSION, info=lambda: PODMAN_ROOTLESS_INFO,
                                       containers=types.SimpleNamespace(create=lambda **kw: calls.append(kw) or "c"))
        docker_engine.create_container(client, image="example", devices=["/dev/dri/renderD128:/dev/dri/renderD128"])
        assert calls[0]["group_add"] == ["keep-groups"]

    @pytest.mark.parametrize("version, info", [(DOCKER_VERSION, {"SecurityOptions": []}),
                                               (PODMAN_VERSION, {"SecurityOptions": ["name=seccomp"]})],
                             ids=["docker", "podman-rootful"])
    def test_elsewhere_the_groups_stay(self, version, info):
        calls = []
        client = types.SimpleNamespace(version=lambda: version, info=lambda: info,
                                       containers=types.SimpleNamespace(create=lambda **kw: calls.append(kw) or "c"))
        docker_engine.create_container(client, image="example", user="0:0", devices=["/dev/dri:/dev/dri"],
                                       group_add=["44"])
        assert calls[0]["group_add"] == ["44"]


class TestRootlessDocker:
    """Under rootless Docker uid 0 in a container is the person outside; any other uid is a sub-uid that cannot write
    the person's folders. The person's containers run as 0:0 there (Docker has no keep-id)."""

    def _client(self, info):
        calls = []
        client = types.SimpleNamespace(version=lambda: DOCKER_VERSION, info=lambda: info,
                                       containers=types.SimpleNamespace(create=lambda **kw: calls.append(kw) or "c"))
        return client, calls

    def test_the_persons_container_runs_as_root_there(self):
        client, calls = self._client({"SecurityOptions": ["name=seccomp,profile=builtin", "name=rootless"]})
        docker_engine.create_container(client, image="example", user="1000:1000", read_only=True)
        assert calls == [{"image": "example", "user": "0:0", "read_only": True}]

    def test_rootful_docker_keeps_the_persons_uid(self):
        client, calls = self._client({"SecurityOptions": ["name=seccomp,profile=builtin"]})
        docker_engine.create_container(client, image="example", user="1000:1000")
        assert calls[0]["user"] == "1000:1000"

    def test_rootless_docker_is_seen_and_podman_is_not_docker(self):
        client, _ = self._client({"SecurityOptions": ["name=rootless"]})
        assert docker_engine.rootless_docker(client) and not docker_engine.rootless_podman(client)
        podman = types.SimpleNamespace(version=lambda: PODMAN_VERSION, info=lambda: PODMAN_ROOTLESS_INFO)
        assert not docker_engine.rootless_docker(podman) and docker_engine.rootless_podman(podman)
