"""
Ditto's runtime mode, bare metal or Docker (DECISIONS round 7 D): the image built on this computer from the same pinned
recipe (never pulled, never pushed), the server container under the one launch policy (port on 127.0.0.1 only, models
read-only, no root, no capabilities), detection of both modes, `ditto runtime ensure` (idempotent, bare metal by
default, Docker when chosen or when bare metal is not possible), switching that reuses the models and repackages
where it can, the old mode's leftovers listed for the person and removed only after the person's yes, the installer
step, the AI settings and what an AI may see.

No real Docker, build or download: a fake docker-py client, the fake executor and verifier of the self-built runtime
tests, and a fake llama.cpp in the check containers.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.runtime import container_server as cs
from orchestrator.runtime import runtime_mode, server_policy
from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError
from orchestrator.runtime.runtime_mode import LeftoversChanged, RuntimeModeError, ensure, leftovers, remove_leftovers

from test_runtime_selfbuilt import (APU, CATALOG, PIN, Z1, Engine, FakeExecutor, FakeVerifier,  # noqa: F401
                                    machine, no_source, toolchain)

VERSION = f"version: 0.6.0 (build {PIN.build_number}, commit {PIN.commit[:8]})\nbuilt with GNU 13.3.0\n"
BASES = {b.id: b for b in CATALOG.build_bases}


# --- a fake docker-py client ----------------------------------------------------------------------------------------

class NotFound(Exception):
    pass


def llama(image, command, options):
    """A fake llama.cpp in a check container: the pinned version, the devices it sees, and the --tools refusal."""
    if "--tools" in command:
        return 1, "error: subprocess is not enabled on this build\n"
    if command[-1] == "--version":
        return 0, VERSION
    if command[-1] == "--list-devices":
        gpu = "  Vulkan0: AMD Radeon 780M (512 MiB, 400 MiB free)\n" if options.get("devices") else ""
        return 0, "Available devices:\n" + gpu
    return 0, ""


class FakeImage:
    def __init__(self, image_id, tags, labels, size=300_000_000, created="2026-10-15T10:00:00"):
        self.id, self.tags, self.labels = image_id, list(tags), dict(labels)
        self.attrs = {"Size": size, "Created": created}


class FakeContainer:
    def __init__(self, client, cid, image, command, volumes, environment, options):
        self.client, self.id, self.image_ref = client, cid, image
        self.command, self.volumes, self.environment, self.options = list(command), volumes, environment, options
        self.labels = dict(options.get("labels") or {})
        self.name = options.get("name") or f"check-{cid[:8]}"
        self.status = "created"
        self.attrs = {"Image": image, "NetworkSettings": {"Ports": {}}, "SizeRw": 4096}
        self.exit, self.output = 0, ""

    def start(self):
        self.status = "running"
        ports = self.options.get("ports") or {}
        self.attrs["NetworkSettings"]["Ports"] = {k: [{"HostIp": v[0], "HostPort": str(v[1])}]
                                                  for k, v in ports.items()}
        if self.labels.get(cs.LABEL) == "check":
            self.exit, self.output = self.client.handler(self.image_ref, self.command, self.options)

    def wait(self, timeout=None):
        self.status = "exited"
        return {"StatusCode": self.exit}

    def logs(self, stdout=True, stderr=True, stream=False, follow=False):
        data = self.output.encode()
        return iter([data]) if stream else data

    def kill(self):
        self.status = "exited"

    def stop(self, timeout=None):
        self.client.stopped.append(self.id)
        self.status = "exited"
        if self.options.get("auto_remove"):
            self.client.containers.items.pop(self.id, None)

    def remove(self, force=False):
        self.client.removed_containers.append(self.id)
        self.client.containers.items.pop(self.id, None)


class FakeImages:
    def __init__(self, client):
        self.client, self.items = client, {}

    def get(self, ref):
        for image in self.items.values():
            if image.id == ref or ref in image.tags:
                return image
        raise NotFound(ref)

    def list(self, filters=None):
        label = (filters or {}).get("label")
        return [i for i in self.items.values() if label is None or label in i.labels]

    def remove(self, image, noprune=False):
        assert noprune is True, "only the listed image, never its untagged parents"
        self.client.removed_images.append(image)
        self.items.pop(image, None)


class FakeContainers:
    def __init__(self, client):
        self.client, self.items = client, {}

    def create(self, image, command, volumes=None, environment=None, **options):
        cid = hashlib.sha256(f"{len(self.client.created)}{image}".encode()).hexdigest()
        container = FakeContainer(self.client, cid, image, command, volumes or {}, environment or {}, options)
        self.client.created.append(container)
        self.items[cid] = container
        return container

    def get(self, ref):
        for c in self.items.values():
            if c.id == ref or c.name == ref:
                return c
        raise NotFound(ref)

    def list(self, all=False, filters=None):
        label = (filters or {}).get("label")
        return [c for c in self.items.values() if label is None or label in c.labels]

    def run(self, *a, **k):  # docker-py would pull a missing image here: never used
        raise AssertionError("containers.run pulls missing images; Ditto's containers are created by image ID")


class FakeAPI:
    def __init__(self, client):
        self.client = client

    def build(self, path, dockerfile, tag, target, buildargs, labels, decode, rm, forcerm, pull):
        assert pull is False and rm and forcerm and decode
        context = Path(path)
        assert (context / dockerfile).read_text() == cs.RUNTIME_DOCKERFILE.read_text()
        files = sorted(p.name for p in (context / "bin").iterdir())
        self.client.builds.append({"tag": tag, "target": target, "buildargs": dict(buildargs),
                                   "labels": dict(labels), "files": files})
        if self.client.build_error:
            yield {"error": self.client.build_error}
            return
        image_id = "sha256:" + hashlib.sha256(f"{tag}{target}{files}".encode()).hexdigest()
        self.client.images.items[image_id] = FakeImage(image_id, [tag], labels)
        yield {"stream": f"Successfully built {image_id[7:19]}\n"}


class FakeClient:
    def __init__(self, runtimes=(), handler=llama, reachable=True):
        self.images, self.containers, self.api = FakeImages(self), FakeContainers(self), FakeAPI(self)
        self.runtimes, self.handler, self.reachable = runtimes, handler, reachable
        self.builds, self.created, self.stopped = [], [], []
        self.removed_images, self.removed_containers = [], []
        self.build_error = ""

    def ping(self):
        if not self.reachable:
            raise ConnectionError("no daemon")
        return True

    def info(self):
        return {"Runtimes": {name: {} for name in self.runtimes}}

    def push(self, *a, **k):  # pragma: no cover - the guard: nothing ever pushes
        raise AssertionError("Ditto's image is never pushed")


@pytest.fixture
def dri(tmp_path, monkeypatch):
    """A /dev/dri of the test (empty: no render node), so the machine running the tests does not matter."""
    folder = tmp_path / "dri"
    folder.mkdir()
    monkeypatch.setattr(cs, "DRI", folder)
    return folder


@pytest.fixture
def verify_in_container(monkeypatch):
    """Records the runner of the container verification and answers like the fake verifier (the fake programs are no
    ELF files, so the real format check cannot run on them)."""
    runners = []

    def factory(runner):
        runners.append(runner)
        return FakeVerifier()
    monkeypatch.setattr(runtime_mode, "container_verifier", factory)
    return runners


GB = 1000 ** 3


def disks(free):
    """A fake shutil.disk_usage: `free` bytes free on every disk of 1000 GB (2 % of it is kept free)."""
    return lambda path: SimpleNamespace(total=1000 * GB, used=1000 * GB - free, free=free)


@pytest.fixture(autouse=True)
def plenty_of_space(monkeypatch):
    """The space check of a switch (W2) sees plenty of free space, whatever the test machine's disk holds; the tests of
    the check itself set less."""
    monkeypatch.setattr(runtime_mode, "_disk_usage", disks(500 * GB))


def runtime(tmp_path, client=None, profile=Z1, native=True, engine=None, executor=None, verifier=None, **kw):
    return LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models" / "ditto", catalog=CATALOG,
                           hardware=machine([APU]), profile=profile, toolchain=toolchain(native),
                           engine=engine or Engine(reason=None), executor=executor or FakeExecutor(),
                           verifier=verifier or FakeVerifier(), platform=("linux", "x86_64"),
                           docker=cs.DittoDocker(client=client or FakeClient()), **kw)


def with_libc(profile, version):
    return runtime_mode.profile_for_base(profile, SimpleNamespace(libc="glibc", libc_version=version))


class Refuses(FakeExecutor):
    def build(self, plan, **kw):
        raise AssertionError("nothing is built")


class Audit:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record(self, actor, tool, tier, decision, outcome, **kw):
        if self.fail:
            raise OSError("disk full")
        self.rows.append({"actor": actor, "tool": tool, "outcome": outcome, **kw})
        return len(self.rows)


# --- the launch policy of the container -----------------------------------------------------------------------------

class TestContainerPolicy:

    def test_the_command_listens_inside_the_container_only_with_the_key_file(self):
        cmd = server_policy.container_server_command(cs.SERVER_IN_IMAGE, "/models/x.gguf", key_file=cs.KEY_MOUNT,
                                                     alias="x", extra=["-c", "4096"])
        assert cmd[cmd.index("--host") + 1] == "0.0.0.0" and cmd[cmd.index("--port") + 1] == "8080"
        assert cmd[cmd.index("--api-key-file") + 1] == cs.KEY_MOUNT
        assert {"--no-ui", "--no-slots", "--jinja", "--offline"} <= set(cmd)
        for bad in (["--tools", "all"], ["--api-key", "k"], ["--host", "127.0.0.1"], ["--port", "9000"],
                    ["--mcp-servers-json", "{}"], ["-hf", "x/y"]):
            with pytest.raises(server_policy.PolicyError):
                server_policy.check_container_server_command(cmd + bad)
        with pytest.raises(server_policy.PolicyError, match="--no-ui"):
            server_policy.check_container_server_command([c for c in cmd if c != "--no-ui"])

    def test_bare_metal_still_never_listens_on_every_interface(self, tmp_path):
        cmd = server_policy.container_server_command(cs.SERVER_IN_IMAGE, "/models/x.gguf", key_file=cs.KEY_MOUNT,
                                                     alias="x")
        with pytest.raises(server_policy.PolicyError, match="127.0.0.1 only"):
            server_policy.check_server_command(cmd)

    @pytest.mark.parametrize("ports", [{"8080/tcp": ("0.0.0.0", 4000)}, {"8080/tcp": ("", 4000)},
                                       {"8080/tcp": 4000}, {"8080/tcp": ("127.0.0.1", 4000), "22/tcp": 22},
                                       {"9000/tcp": ("127.0.0.1", 4000)}, {}, {"8080/tcp": ("127.0.0.1", None)}])
    def test_the_port_is_published_on_loopback_only(self, ports):
        with pytest.raises(server_policy.PolicyError):
            server_policy.check_publish(ports)

    def test_the_server_options_keep_the_sandbox(self, tmp_path):
        gpu = cs.GpuPassthrough("dri", {"devices": ["/dev/dri:/dev/dri:rwm"], "group_add": ["44"]}, {}, "dri")
        options, volumes = cs.server_options(models_dir=tmp_path / "models", key_file=tmp_path / "server-a.key",
                                             port=4123, gpu=gpu, name="lcf-ditto-1000")
        assert options["ports"] == {"8080/tcp": ("127.0.0.1", 4123)} and "network_mode" not in options
        assert options["cap_drop"] == ["ALL"] and options["read_only"] is True and options["auto_remove"] is True
        assert "no-new-privileges:true" in options["security_opt"] and not options["user"].startswith("0:")
        assert options["labels"][cs.LABEL] == "server" and options["devices"] == ["/dev/dri:/dev/dri:rwm"]
        assert all(v["mode"] == "ro" for v in volumes.values())
        assert volumes[str(tmp_path / "models")]["bind"] == cs.MODELS_MOUNT, "the models are mounted, not copied"
        for bad in ({"network_mode": "host"}, {"privileged": True}, {"cap_add": ["SYS_ADMIN"]}, {"read_only": False},
                    {"user": "0:0"}, {"security_opt": []}):
            with pytest.raises(server_policy.PolicyError):
                cs.check_server_options({**options, **bad}, volumes)
        with pytest.raises(server_policy.PolicyError, match="read-only"):
            cs.check_server_options(options, {**volumes, str(tmp_path): {"bind": "/x", "mode": "rw"}})

    def test_the_server_is_started_by_the_image_id_never_by_a_name_that_could_be_pulled(self, tmp_path):
        docker = cs.DittoDocker(client=FakeClient())
        options, volumes = cs.server_options(models_dir=tmp_path, key_file=tmp_path / "k", port=4000,
                                             gpu=cs.gpu_passthrough(docker, machine(), "cpu"), name="n")
        cmd = server_policy.container_server_command(cs.SERVER_IN_IMAGE, "/models/x.gguf", key_file=cs.KEY_MOUNT,
                                                     alias="x")
        with pytest.raises(cs.ContainerError, match="image's ID"):
            cs.start_server(docker, "llm-framework/ditto-runtime:abc", cmd, options=options, volumes=volumes, env={})
        cid = cs.start_server(docker, "sha256:" + "a" * 64, cmd, options=options, volumes=volumes, env={})
        assert docker.client().containers.items[cid].image_ref == "sha256:" + "a" * 64

    def test_only_docker_official_images_are_bases(self):
        assert all(cs.is_official_image(b.image) for b in CATALOG.build_bases)
        digest = "@sha256:" + "0" * 64
        for bad in ("ghcr.io/owner/image" + digest, "someone/debian" + digest, "debian:12-slim", "registry:5000/x"
                    + digest, ""):
            assert not cs.is_official_image(bad), bad
        with pytest.raises(cs.ContainerError, match="Docker Official Image"):
            cs.build_runtime_base(cs.DittoDocker(client=FakeClient()),
                                  SimpleNamespace(id="x", image="ghcr.io/x/y" + digest, libc="glibc"), "cpu",
                                  Path(os.devnull))


class TestGpuPassthrough:

    def test_cpu_needs_none(self, dri):
        assert not cs.gpu_passthrough(cs.DittoDocker(client=FakeClient()), machine([APU]), "cpu").used

    def test_nvidia_through_the_container_toolkit_or_an_honest_note(self, dri):
        from orchestrator.runtime.hardware import GpuInfo
        nvidia = machine([GpuInfo(vendor="nvidia", name="RTX", vram_mib=8192)])
        with_toolkit = cs.gpu_passthrough(cs.DittoDocker(client=FakeClient(runtimes=("runc", "nvidia"))), nvidia,
                                          "vulkan")
        assert with_toolkit.kind == "nvidia" and with_toolkit.options["device_requests"]
        assert "graphics" in with_toolkit.environment["NVIDIA_DRIVER_CAPABILITIES"]
        without = cs.gpu_passthrough(cs.DittoDocker(client=FakeClient(runtimes=("runc",))), nvidia, "vulkan")
        assert not without.used and "NVIDIA Container Toolkit" in without.note and "CPU" in without.note

    def test_amd_and_intel_through_dev_dri_with_the_render_group(self, dri):
        assert "no render node" in cs.gpu_passthrough(cs.DittoDocker(client=FakeClient()), machine([APU]),
                                                      "vulkan").note
        (dri / "renderD128").write_text("")
        gpu = cs.gpu_passthrough(cs.DittoDocker(client=FakeClient()), machine([APU]), "vulkan")
        assert gpu.kind == "dri" and gpu.options["devices"] == [f"{dri}:{dri}:rwm"]
        assert gpu.options["group_add"] == [str((dri / "renderD128").stat().st_gid)]


# --- images and their checks ----------------------------------------------------------------------------------------

def _stored_native(rt):
    from orchestrator.runtime.build.recipe import plan_build
    from test_runtime_selfbuilt import GCC
    plan = plan_build(rt.profile(), purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                      toolchain=GCC)
    return rt.store.build(plan, source=rt.root, executor=FakeExecutor(), verifier=FakeVerifier(),
                          host=("linux", "x86_64"))


class TestImages:

    def test_the_image_is_the_verified_build_on_its_base_in_two_stages(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        build = _stored_native(rt)
        image = cs.build_runtime_image(rt.docker(), build, BASES["debian-13"], "repackaged", tmp_path / "log")
        base_build, image_build = client.builds
        assert base_build["target"] == "runtime-base" and image_build["target"] == "ditto"
        assert base_build["buildargs"] == {"BASE": BASES["debian-13"].image, "LIBC": "glibc", "BACKEND": "cpu"}
        assert image_build["files"] == sorted(build.manifest["files"])
        assert image.name == f"llm-framework/ditto-runtime:{build.build_id[:16]}-debian-13"
        assert image.labels[cs.LABEL_BUILD] == build.build_id and image.labels[cs.LABEL_COMMIT] == PIN.commit
        assert image.labels[cs.LABEL_MADE_BY] == "repackaged" and image.role == "image"
        cs.build_runtime_image(rt.docker(), build, BASES["debian-13"], "repackaged", tmp_path / "log")
        assert [b["target"] for b in client.builds[2:]] == ["ditto"], "the runtime base is reused"

    def test_the_programs_in_the_image_are_runnable_by_the_containers_user(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        build = _stored_native(rt)
        for program in build.bin_dir.iterdir():
            program.chmod(0o700)  # built under a strict umask: only its owner may run it
        stored = {p.name: p.stat().st_mode & 0o777 for p in build.bin_dir.iterdir()}
        modes = {}
        build_stage = client.api.build

        def recording(path, **kw):
            # The modes the daemon gets: those of the build context archive the Docker SDK sends (from the file modes
            # on POSIX; on Windows, which keeps no execute bit, the SDK marks every file 0755).
            from docker.api.build import process_dockerfile
            from docker.utils import tar as context_archive
            sent = context_archive(path, dockerfile=process_dockerfile(kw.get("dockerfile"), path))  # as api.build
            with tarfile.open(fileobj=sent) as archive:
                modes.update({m.name[len("bin/"):]: m.mode & 0o777 for m in archive.getmembers()
                              if m.isfile() and m.name.startswith("bin/")})
            return build_stage(path, **kw)
        client.api.build = recording
        cs.build_runtime_image(rt.docker(), build, BASES["debian-13"], "repackaged", tmp_path / "log")
        assert modes and set(modes.values()) == {0o755}, "COPY keeps the mode; the container runs as another owner"
        assert {p.name: p.stat().st_mode & 0o777 for p in build.bin_dir.iterdir()} == stored, \
            "the build store is unchanged"

    def test_a_changed_program_is_never_put_into_an_image(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        build = _stored_native(rt)
        (build.bin_dir / "llama-server").write_text("#!/bin/sh\necho changed\n")
        with pytest.raises(cs.ContainerError, match="does not match its manifest"):
            cs.build_runtime_image(rt.docker(), build, BASES["debian-13"], "rebuilt", tmp_path / "log")
        assert [b["target"] for b in client.builds] == ["runtime-base"]

    def test_the_image_check_runs_in_containers_of_the_image_without_network(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        image = cs.build_runtime_image(rt.docker(), _stored_native(rt), BASES["debian-13"], "repackaged",
                                       tmp_path / "log")
        report = cs.verify_image(rt.docker(), image, PIN, cs.gpu_passthrough(rt.docker(), machine(), "cpu"))
        assert report.ok and report.ran_binaries, report.summary()
        checks = [c for c in client.created if c.labels.get(cs.LABEL) == "check"]
        assert len(checks) == 4 and all(c.image_ref == image.image_id for c in checks)
        assert all(c.options["network_mode"] == "none" and c.options["read_only"] for c in checks)
        assert sorted(client.removed_containers) == sorted(c.id for c in checks), "check containers are removed"
        assert not any("/models" in json.dumps(c.volumes) for c in checks), "no model reaches a check container"

    @pytest.mark.parametrize("broken", ["version", "tools"])
    def test_a_wrong_version_or_a_server_that_takes_tools_fails_the_check(self, tmp_path, broken):
        def handler(image, command, options):
            if broken == "version" and command[-1] == "--version":
                return 0, "version: 0.5.0 (build 11000, commit 0badc0de)\n"
            if broken == "tools" and "--tools" in command:
                return 0, "listening"
            return llama(image, command, options)
        client = FakeClient(handler=handler)
        rt = runtime(tmp_path, client)
        image = cs.build_runtime_image(rt.docker(), _stored_native(rt), BASES["debian-13"], "rebuilt",
                                       tmp_path / "log")
        report = cs.verify_image(rt.docker(), image, PIN, cs.gpu_passthrough(rt.docker(), machine(), "cpu"))
        assert not report.ok and ("11000" in report.summary() or "--tools" in report.summary())

    def test_an_image_of_another_commit_is_refused_without_running_it(self, tmp_path):
        client = FakeClient()
        image = cs.ImageInfo("sha256:" + "b" * 64, ("x:1",), {cs.LABEL: "image", cs.LABEL_COMMIT: "f" * 40}, 1)
        report = cs.verify_image(cs.DittoDocker(client=client), image, PIN,
                                 cs.GpuPassthrough("none", {}, {}, ""))
        assert not report.ok and client.created == []

    def test_the_container_runner_mounts_the_build_at_its_own_path(self, tmp_path):
        client = FakeClient()
        bin_dir, cwd = tmp_path / "build" / "bin", tmp_path / "work"
        bin_dir.mkdir(parents=True)
        cwd.mkdir()
        runner = cs.ContainerRunner(cs.DittoDocker(client=client), "sha256:" + "c" * 64,
                                    cs.GpuPassthrough("none", {}, {}, ""))
        rc, out, _ = runner([str(bin_dir / "llama-bench"), "--version"], 60, str(cwd), {"HOME": str(cwd)})
        assert rc == 0 and "11429" in out
        container = client.created[-1]
        assert container.volumes[str(bin_dir)] == {"bind": str(bin_dir), "mode": "ro"}
        assert container.volumes[str(cwd)] == {"bind": str(cwd), "mode": "rw"}
        assert container.options["network_mode"] == "none" and container.options["working_dir"] == str(cwd)

    def test_the_container_is_alive_only_with_its_label_and_loopback_port(self):
        client = FakeClient()
        docker = cs.DittoDocker(client=client)
        container = client.containers.create("sha256:" + "d" * 64, ["x"], labels={cs.LABEL: "server"},
                                              ports={"8080/tcp": ("127.0.0.1", 4555)})
        container.start()
        assert cs.container_alive({"container_id": container.id, "port": 4555}, docker)
        assert not cs.container_alive({"container_id": container.id, "port": 4556}, docker)
        container.attrs["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostIp"] = "0.0.0.0"
        assert not cs.container_alive({"container_id": container.id, "port": 4555}, docker)
        container.attrs["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostIp"] = "127.0.0.1"
        container.labels = {}
        assert not cs.container_alive({"container_id": container.id, "port": 4555}, docker)
        assert not cs.container_alive({"container_id": "not an id", "port": 4555}, docker)


# --- the server in Docker mode ---------------------------------------------------------------------------------------

def _docker_mode(rt, client):
    """Ditto's verified image recorded as the Docker mode (what ensure() leaves behind)."""
    build = _stored_native(rt)
    image = cs.build_runtime_image(rt.docker(), build, BASES["debian-13"], "repackaged", rt.root / "image.log")
    rt.record_mode("docker", {"image_id": image.image_id, "tag": image.name, "build_id": build.build_id,
                              "base": "debian-13", "backend": "cpu", "made_by": "repackaged", "verified_at": "now"})
    return image


def _model(rt):
    model = rt.catalog.model("small")
    rt.models_dir.mkdir(parents=True, exist_ok=True)
    rt.model_path(model).write_bytes(b"GGUF")
    rt._ensure_model_verified = lambda m: rt.model_path(m)
    return model


class TestServerInDocker:

    def test_start_runs_the_verified_image_with_the_models_read_only_on_loopback(self, tmp_path, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        image = _docker_mode(rt, client)
        model = _model(rt)
        rt._health = lambda port: 200
        rt._check_auth = lambda port, key: None
        started = []
        rt._popen = lambda *a, **k: started.append(a) or pytest.fail("no process on this computer in Docker mode")
        ep = rt.start(tier="small")
        server = [c for c in client.created if c.labels.get(cs.LABEL) == "server"][-1]
        assert server.image_ref == image.image_id and server.options["auto_remove"] is True
        assert server.options["ports"] == {"8080/tcp": ("127.0.0.1", ep.port)} and ep.base_url.startswith(
            "http://127.0.0.1:")
        assert server.volumes[str(rt.models_dir)] == {"bind": "/models", "mode": "ro"}
        assert server.command[server.command.index("-m") + 1] == f"/models/{model.file}"
        server_policy.check_container_server_command(server.command)
        assert not any(k.startswith("LLAMA") or "PROXY" in k.upper() for k in server.environment)
        state = json.loads(rt.state_file.read_text())
        assert state["mode"] == "docker" and state["container_id"] == server.id and "pid" not in state
        assert rt.endpoint() is not None and rt.endpoint().api_key == ep.api_key
        status = rt.status()
        assert status.mode == "docker" and status.running and status.container == server.name
        assert rt.stop() == 1 and server.id in client.stopped
        assert not rt.state_file.exists() and not list(rt.root.glob("server-*.key")) and rt.endpoint() is None

    def test_an_endpoint_is_given_only_while_the_container_runs(self, tmp_path, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        _docker_mode(rt, client)
        _model(rt)
        rt._health = lambda port: 200
        rt._check_auth = lambda port, key: None
        rt.start(tier="small")
        server = [c for c in client.created if c.labels.get(cs.LABEL) == "server"][-1]
        server.status = "exited"
        assert rt.endpoint() is None, "a key is never handed to whatever listens on the port now"

    def test_a_missing_or_other_image_is_not_started(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        image = _docker_mode(rt, client)
        client.images.items.pop(image.image_id)
        with pytest.raises(LocalRuntimeError, match="ensure --mode docker --force"):
            rt.start(tier="small")
        assert not [c for c in client.created if c.labels.get(cs.LABEL) == "server"]

    def test_without_docker_the_way_out_is_named(self, tmp_path):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        _docker_mode(rt, client)
        client.reachable = False
        with pytest.raises(LocalRuntimeError, match="ensure --mode bare-metal --force"):
            rt.start(tier="small")


# --- detection and ensure ----------------------------------------------------------------------------------------------

class TestEnsure:

    def test_nothing_installed_gives_bare_metal_and_a_second_run_does_nothing(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        result = ensure(rt)
        assert result.action == "installed" and result.mode == "bare-metal" and rt.mode() == "bare-metal"
        assert any("bare metal is the default" in line for line in result.lines)
        assert result.leftovers is None and client.builds == [], "no image for a bare-metal installation"
        again = ensure(runtime(tmp_path, client, executor=Refuses()))
        assert again.action == "skipped" and any("Nothing to do" in line for line in again.lines)

    def test_installed_bare_metal_is_kept_when_docker_is_asked_without_force(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        rt.install()
        result = ensure(runtime(tmp_path, client, executor=Refuses()), mode="docker")
        assert result.action == "skipped" and result.mode == "bare-metal" and client.builds == []
        assert any("--mode docker --force" in line and "models are reused" in line for line in result.lines)

    def test_docker_when_bare_metal_is_not_possible_with_the_reason(self, tmp_path, no_source, dri,
                                                                     verify_in_container):
        client = FakeClient()
        old = with_libc(Z1, "2.17")  # older than every builder base, and no compiler here
        rt = runtime(tmp_path, client, profile=old, native=False)
        result = ensure(rt)
        assert result.mode == "docker" and rt.mode() == "docker"
        assert any("Bare metal is not possible here" in line and "older than every builder base" in line
                   for line in result.lines)
        record = rt.mode_record()["docker"]
        build = rt.store.get(record["build_id"])
        assert build.verified_in == "container" and not build.verified, "verified where it runs, not here"
        assert rt.store.references().get("docker") == build.build_id and "active" not in rt.store.references()
        assert record["made_by"] == "rebuilt" and record["base"] == "debian-13"
        assert verify_in_container and verify_in_container[0].image.startswith("sha256:")
        assert [b["target"] for b in client.builds] == ["runtime-base", "ditto"]
        assert rt.resolve_installed()[0] is None, "a build verified only in the container never runs bare metal"

    def test_neither_mode_names_both_reasons(self, tmp_path, dri):
        rt = runtime(tmp_path, FakeClient(reachable=False), profile=with_libc(Z1, "2.17"), native=False)
        with pytest.raises(RuntimeModeError) as e:
            ensure(rt)
        assert "Bare metal:" in str(e.value) and "Docker:" in str(e.value) and "Docker daemon" in str(e.value)

    def test_force_needs_a_mode(self, tmp_path):
        with pytest.raises(RuntimeModeError, match="force needs a mode"):
            ensure(runtime(tmp_path), force=True)

    def test_in_the_frameworks_own_container_build_docker_mode_is_not_available(self, tmp_path):
        with pytest.raises(RuntimeModeError, match="LCF_CONTAINER_BUILD"):
            ensure(runtime(tmp_path), mode="docker", container_build=True)

    def test_docker_mode_on_windows_says_it_follows(self, tmp_path):
        rt = runtime(tmp_path)
        rt._platform = ("windows", "x86_64")
        assert "Linux" in runtime_mode.docker_problem(rt)

    def test_a_pulled_image_with_dittos_labels_is_never_used(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        image = cs.build_runtime_image(rt.docker(), _stored_native(rt), BASES["debian-13"], "repackaged",
                                       tmp_path / "log")
        # the same labels on an image that came from a registry (anyone can put labels on an image)
        client.images.items[image.image_id].attrs["RepoDigests"] = [f"{cs.IMAGE_REPO}@sha256:{'a' * 64}"]
        det = runtime_mode.detect(rt)
        assert det.docker is None and any("pulled from a registry" in note for note in det.notes), det.notes
        pulled = rt.docker().image(image.image_id)
        report = cs.verify_image(rt.docker(), pulled, PIN, cs.gpu_passthrough(rt.docker(), rt.hardware(), "cpu"))
        assert not report.ok and not [c for c in client.created if c.labels.get(cs.LABEL) == "check"], \
            "refused before anything of it runs"

    def test_ensure_removes_no_old_build_by_itself(self, tmp_path, no_source, dri, monkeypatch):
        from orchestrator.runtime.build.store import BuildStore
        removed = []   # BuildStore.remove deletes only what the person chose from the list (WP7)
        monkeypatch.setattr(BuildStore, "remove", lambda self, *a, **k: removed.append(a) or ([], []))
        rt = runtime(tmp_path)
        other = rt.root / "b1000-cpu-x64"  # the official archive of another llama.cpp build
        other.mkdir(parents=True)
        (other / "runtime.json").write_text("{}")
        result = ensure(rt)
        assert result.action == "installed" and rt.installed() is not None
        assert removed == [] and other.is_dir(), "a deletion is the person's, from a list (round 5 f)"

    def test_docker_mode_stays_while_docker_cannot_be_asked(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        rt.install()
        ensure(runtime(tmp_path, client, executor=Refuses()), mode="docker", force=True)
        client.reachable = False  # Docker is not started (yet), the bare-metal build is still there
        again = runtime(tmp_path, client, executor=Refuses())
        result = ensure(again)
        assert result.action == "skipped" and result.mode == "docker" and again.mode() == "docker"
        assert any("Docker cannot be used now" in line and "nothing was changed" in line
                   and "--mode bare-metal --force" in line for line in result.lines)

    def test_a_recorded_mode_that_is_gone_is_named_when_the_mode_changes(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        rt.install()
        ensure(runtime(tmp_path, client, executor=Refuses()), mode="docker", force=True)
        client.images.items.clear()  # the person removed Ditto's image with docker rmi
        again = runtime(tmp_path, client, executor=Refuses())
        result = ensure(again)
        assert result.action == "skipped" and again.mode() == "bare-metal"
        assert any("Docker: the recorded image" in line and "is no longer there" in line for line in result.lines)
        assert any("was Docker" in line and "now bare metal" in line for line in result.lines)


class TestSwitch:

    def test_bare_metal_to_docker_repackages_the_build_and_lists_the_old_files(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        rt.install()
        models = rt.models_dir
        models.mkdir(parents=True)
        (models / "Qwen3.5-2B-Q4_K_M.gguf").write_bytes(b"GGUF")
        switched = runtime(tmp_path, client, executor=Refuses())
        result = ensure(switched, mode="docker", force=True)
        assert result.action == "switched" and switched.mode() == "docker"
        record = switched.mode_record()["docker"]
        assert record["made_by"] == "repackaged" and record["base"] == "debian-13", "glibc 2.41 -> debian-13"
        assert record["backend"] == "cpu", "no render node: the CPU build goes into the image"
        assert any("Repackaged" in line and "Nothing was built" in line for line in result.lines)
        assert any("vulkan build is not used in the container" in line for line in result.lines)
        assert (models / "Qwen3.5-2B-Q4_K_M.gguf").read_bytes() == b"GGUF", "the models stay"
        plan = result.leftovers
        builds = [i for i in plan.items if i.kind == "runtime_build"]
        assert len(builds) == 2 and all(Path(i.full_path).is_dir() for i in builds), "listed, nothing removed"
        for item in builds:
            assert item.file_name == Path(item.full_path).name and item.size_bytes > 0 and "build ID" in item.what
            assert item.full_path not in item.speaking_name
        assert not [i for i in plan.items if i.kind == "docker_image"], "the image in use is never listed"
        assert client.removed_images == [] and client.removed_containers == [
            c.id for c in client.created if c.labels.get(cs.LABEL) == "check"]

    def test_repackaging_that_cannot_fit_a_base_builds_again_and_says_why(self, tmp_path, no_source, dri,
                                                                         verify_in_container):
        client = FakeClient()
        newer = with_libc(Z1, "2.42")  # newer than every base: a native build fits none
        rt = runtime(tmp_path, client, profile=newer)
        rt.install()
        result = ensure(runtime(tmp_path, client, profile=newer), mode="docker", force=True)
        assert any("Repackaging is not possible" in line and "2.42 or newer" in line for line in result.lines)
        assert rt.mode_record()["docker"]["made_by"] == "rebuilt"

    def test_an_image_that_fails_its_check_is_rebuilt_and_stays_for_the_person(self, tmp_path, no_source, dri,
                                                                             verify_in_container):
        state = {"calls": 0}

        def handler(image, command, options):
            state["calls"] += 1
            if state["calls"] <= 4 and command[-1] == "--version":
                return 127, "error while loading shared libraries: libstdc++.so.6: version GLIBCXX_3.4.33 not found"
            return llama(image, command, options)
        client = FakeClient(handler=handler)
        rt = runtime(tmp_path, client)
        rt.install()
        result = ensure(runtime(tmp_path, client), mode="docker", force=True)
        assert any("did not pass its check" in line and "stays until you remove it" in line for line in result.lines)
        assert rt.mode_record()["docker"]["made_by"] == "rebuilt"
        failed = [i for i in result.leftovers.items if i.kind == "docker_image" and "repackaged" in i.what]
        assert failed and client.removed_images == [], "never deleted unless the person does it"

    def test_docker_to_bare_metal_reuses_the_images_build_and_lists_the_images(self, tmp_path, no_source, dri,
                                                                              verify_in_container):
        client = FakeClient()
        rt = runtime(tmp_path, client, native=False)
        ensure(rt, mode="docker")
        back = runtime(tmp_path, client, native=False, executor=Refuses())
        result = ensure(back, mode="bare-metal", force=True)
        assert result.mode == "bare-metal" and back.mode() == "bare-metal"
        assert any("Repackaged" in line and "nothing was built" in line for line in result.lines)
        inst = back.installed()
        assert inst is not None and inst.build_id == rt.mode_record()["docker"]["build_id"]
        assert back.store.get(inst.build_id).verified, "verified on this computer before it runs here"
        images = [i for i in result.leftovers.items if i.kind == "docker_image"]
        assert {i.file_name for i in images} == {i.id for i in client.images.items.values()}
        assert all(i.full_path == "" and i.file_name.startswith("sha256:") and i.size_bytes > 0 for i in images)
        assert client.removed_images == []

    def test_docker_objects_that_cannot_be_listed_are_named(self, tmp_path, no_source, dri, verify_in_container):
        client = FakeClient()
        ensure(runtime(tmp_path, client, native=False), mode="docker")
        client.reachable = False
        back = runtime(tmp_path, client, native=False, executor=Refuses())
        result = ensure(back, mode="bare-metal", force=True)
        assert back.mode() == "bare-metal" and client.removed_images == []
        assert any("Ditto's images and containers could not be listed" in line and "Docker" in line
                   for line in result.lines), result.lines

    def test_force_to_the_mode_that_is_there_only_records_it(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        rt.install()
        result = ensure(runtime(tmp_path, client, executor=Refuses()), mode="bare-metal", force=True)
        assert result.action == "recorded" and any("nothing was built" in line for line in result.lines)




# --- the old mode goes completely (W2) -------------------------------------------------------------------------------

def _switched_to_docker(tmp_path, client):
    rt = runtime(tmp_path, client)
    rt.install()
    switched = runtime(tmp_path, client, executor=Refuses())
    return switched, ensure(switched, mode="docker", force=True).leftovers


def _switched_to_bare_metal(tmp_path, client):
    """Docker mode first (rebuilt in the builder container), then bare metal: the image's build repackaged."""
    ensure(runtime(tmp_path, client, native=False), mode="docker")
    back = runtime(tmp_path, client, native=False, executor=Refuses())
    return back, ensure(back, mode="bare-metal", force=True).leftovers


def _exists(item) -> bool:
    return Path(item.full_path).exists()


class TestLeftovers:

    @pytest.mark.parametrize("actor", ["ditto", "mcp", "ai", "delegation", "", "client:claude"])
    def test_only_the_person_removes(self, tmp_path, no_source, dri, actor):
        rt, plan = _switched_to_docker(tmp_path, FakeClient())
        for before in (False, True):
            with pytest.raises(PermissionError):
                remove_leftovers(rt, plan, actor=actor, audit=Audit(), before_switch=before)
        assert all(_exists(i) for i in plan.items if i.full_path)

    def test_a_changed_list_removes_nothing(self, tmp_path, no_source, dri):
        rt, plan = _switched_to_docker(tmp_path, FakeClient())
        build = next(i for i in plan.items if i.kind == "runtime_build")
        extra = rt.store.builds_dir / ("e" * 16)
        shutil.copytree(Path(build.full_path), extra)
        data = json.loads((extra / "build.json").read_text())
        data["build_id"] = "e" * 64
        (extra / "build.json").write_text(json.dumps(data))
        manifest = json.loads((extra / "manifest.json").read_text())
        manifest["build_id"] = "e" * 64
        (extra / "manifest.json").write_text(json.dumps(manifest))
        with pytest.raises(LeftoversChanged):
            remove_leftovers(rt, plan, actor="cli", audit=Audit())
        assert all(_exists(i) for i in plan.items if i.full_path)

    def test_without_an_audit_nothing_is_removed(self, tmp_path, no_source, dri):
        rt, plan = _switched_to_docker(tmp_path, FakeClient())
        for audit in (None, Audit(fail=True)):
            with pytest.raises(RuntimeModeError, match="audit"):
                remove_leftovers(rt, plan, actor="cli", audit=audit)
        assert all(_exists(i) for i in plan.items if i.full_path)

    def test_bare_metal_goes_completely_and_for_good_once_docker_works(self, tmp_path, no_source, dri):
        """W2, bare metal -> Docker: every bare-metal build folder and bare metal's entries in active.json are listed
        once and deleted for good (no trash); Docker mode keeps working, the models stay."""
        from orchestrator.Core.trash import Trash
        client = FakeClient()
        rt, plan = _switched_to_docker(tmp_path, client)
        models = rt.models_dir
        models.mkdir(parents=True, exist_ok=True)
        (models / "m.gguf").write_bytes(b"GGUF")
        assert plan.mode_offered == ("permanent",), "no trash: the old mode goes completely"
        builds = [i for i in plan.items if i.kind == "runtime_build"]
        records = [i for i in plan.items if i.kind == "runtime_record"]
        assert len(builds) == 2 and [r.file_name for r in records] == ["active.json"]
        assert "build_id" in records[0].what and "the file stays" in records[0].what
        audit = Audit()
        removal = remove_leftovers(rt, plan, actor="gui", audit=audit)
        assert sorted(removal.deleted) == sorted(i.speaking_name for i in plan.items) and not removal.failed
        assert removal.freed_bytes == plan.total_bytes and not removal.stopped
        assert not any(Path(i.full_path).exists() for i in builds), "deleted, not moved"
        assert Trash(tmp_path / "state").entries() == [], "nothing went to the trash"
        active = json.loads((rt.root / "active.json").read_text())
        assert not {"build_id", "fallback_build_id", "use", "variant"} & set(active), active
        assert rt.installed() is None and runtime_mode.detect(rt).bare_metal is None, "nothing of bare metal is left"
        assert runtime_mode.detect(rt).docker.verified and rt.mode() == "docker", "Docker mode keeps working"
        assert (models / "m.gguf").read_bytes() == b"GGUF", "the models stay"
        assert audit.rows[0]["arguments"]["deletion_mode"] == "permanent"
        assert audit.rows[0]["arguments"]["removes"] == "bare-metal"
        assert audit.rows[0]["extra"]["deletion_sha256"] == plan.sha256()
        assert [r["outcome"] for r in audit.rows] == ["started", "ok"]
        assert leftovers(rt, "docker") is None

    def test_docker_goes_completely_and_for_good_once_bare_metal_works(self, tmp_path, no_source, dri,
                                                                      verify_in_container):
        """W2, Docker -> bare metal: every image and container of Ditto, Docker mode's entries in mode.json and
        active.json and the log of its image builds are listed once and deleted for good."""
        client = FakeClient()
        rt, plan = _switched_to_bare_metal(tmp_path, client)
        log = Path(rt.data_dir) / "logs" / runtime_mode.IMAGE_LOG
        assert log.is_file()
        kinds = {i.kind for i in plan.items}
        assert {"docker_image", "runtime_record"} <= kinds and plan.mode_offered == ("permanent",)
        assert {i.file_name for i in plan.items if i.kind == "docker_image"} == set(client.images.items)
        assert {i.file_name for i in plan.items if i.kind == "runtime_record"} == {"mode.json", "active.json",
                                                                                   runtime_mode.IMAGE_LOG}
        removal = remove_leftovers(rt, plan, actor="cli", audit=Audit())
        assert not removal.failed and client.images.items == {}, "every image of Ditto is gone"
        assert "docker" not in json.loads(rt.mode_file.read_text()), "Docker mode's record is gone"
        assert "docker_build_id" not in json.loads((rt.root / "active.json").read_text())
        assert not log.exists() and rt.mode() == "bare-metal"
        assert rt.installed() is not None, "bare metal keeps working (the image's build is its build now)"
        assert leftovers(rt, "bare-metal") is None

    def test_the_official_archive_chosen_goes_with_bare_metals_record(self, tmp_path, no_source, dri):
        """W2: bare metal's record in active.json also names the official archive chosen earlier (asset_id): it is
        listed and deleted with the other entries, so nothing of bare metal stays in the file."""
        rt, _plan = _switched_to_docker(tmp_path, FakeClient())
        active = rt.root / "active.json"
        data = json.loads(active.read_text())
        data.update(asset_id="linux-cpu-x64", variant="cpu-x64")
        active.write_text(json.dumps(data))
        plan = leftovers(rt, "docker")
        (record,) = [i for i in plan.items if i.kind == "runtime_record"]
        assert "asset_id" in record.what, record.what
        removal = remove_leftovers(rt, plan, actor="cli", audit=Audit())
        left = json.loads(active.read_text())
        assert not removal.failed and "asset_id" not in left and "variant" not in left, left

    def test_the_old_mode_is_removed_only_while_the_new_one_works(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt, plan = _switched_to_docker(tmp_path, client)
        client.images.items.clear()   # Ditto's image is gone: Docker mode does not work
        with pytest.raises(RuntimeModeError, match="only after the new one works"):
            remove_leftovers(rt, plan, actor="cli", audit=Audit())
        assert all(_exists(i) for i in plan.items if i.full_path), "bare metal stays while Docker mode is broken"

    def test_images_are_removed_only_through_the_confirmed_list(self):
        sources = {p: p.read_text(encoding="utf-8") for p in (REPO_ROOT / "orchestrator").rglob("*.py")}
        removers = [p.relative_to(REPO_ROOT).as_posix() for p, text in sources.items() if ".remove_image(" in text]
        assert removers == ["orchestrator/runtime/runtime_mode.py"], removers
        text = sources[REPO_ROOT / "orchestrator" / "runtime" / "runtime_mode.py"]
        assert text.count(".remove_image(") == 1 and "def _remove_item" in text
        assert "trash" not in text.split("def remove_leftovers", 1)[1].split("def _forget_keys", 1)[0].replace(
            "not into the trash", "").replace("no trash", ""), "the old mode never goes to the trash"
        for path in ("orchestrator/runtime/container_server.py", "orchestrator/runtime/runtime_mode.py"):
            code = (REPO_ROOT / path).read_text(encoding="utf-8")
            assert ".push(" not in code and ".prune" not in code and "containers.run(" not in code, path


# --- the space a switch needs, and removing the old mode first (W2) ----------------------------------------------

def _docker_disk(client, folder):
    """Docker's image storage as a folder of this computer (DockerRootDir), so the check can measure it."""
    folder.mkdir(parents=True, exist_ok=True)
    client.info = lambda: {"Runtimes": {}, "DockerRootDir": str(folder)}
    return folder


class TestSpace:

    def test_a_switch_that_does_not_fit_builds_nothing_and_names_the_sizes_and_the_old_mode(self, tmp_path, no_source,
                                                                                           dri, monkeypatch):
        client = FakeClient()
        newer = with_libc(Z1, "2.42")   # no base fits the native build: Docker mode needs a new build
        rt = runtime(tmp_path, client, profile=newer)
        rt.install()
        before = sorted(p.name for p in rt.store.builds_dir.iterdir())
        _docker_disk(client, tmp_path / "docker-root")
        monkeypatch.setattr(runtime_mode, "_disk_usage", disks(3 * GB))   # 20 GB are kept free on 1000 GB
        switching = runtime(tmp_path, client, profile=newer, executor=Refuses())
        with pytest.raises(runtime_mode.SpaceShortage) as e:
            ensure(switching, mode="docker", force=True)
        shortage = e.value
        assert client.builds == [] and sorted(p.name for p in rt.store.builds_dir.iterdir()) == before, \
            "nothing was built"
        assert rt.mode() == "bare-metal" and rt.installed() is not None, "bare metal stays as it was"
        estimate = shortage.estimate
        assert estimate.mode == "docker" and not estimate.fits and estimate.total > 0
        labels = {label for check in estimate.checks for label in check.labels}
        assert labels == {runtime_mode.RUNTIME_FOLDER, runtime_mode.DOCKER_STORAGE, runtime_mode.TEMP_FOLDER}
        assert any(n.label == runtime_mode.TEMP_FOLDER for n in estimate.needs), \
            "the image's build context (a copy of the build's programs) is made in the temporary folder"
        text = str(shortage)
        assert "does not fit next to bare metal" in text and "not enough" in text and "free" in text
        assert shortage.removal_helps is False and "would not fit either" in text, \
            "3 GB free on 1000 GB: deleting bare metal first cannot make room"
        assert shortage.old_mode == "bare-metal"
        assert {i.kind for i in shortage.old_plan.items} == {"runtime_build", "runtime_record"}
        assert len([i for i in shortage.old_plan.items if i.kind == "runtime_build"]) == 2

    def test_a_switch_that_fits_says_so_and_builds(self, tmp_path, no_source, dri, verify_in_container):
        client = FakeClient()
        newer = with_libc(Z1, "2.42")
        rt = runtime(tmp_path, client, profile=newer)
        rt.install()
        _docker_disk(client, tmp_path / "docker-root")
        result = ensure(runtime(tmp_path, client, profile=newer), mode="docker", force=True)
        assert result.space is not None and result.space.fits and result.mode == "docker"
        assert any(line.startswith("Space for Docker") for line in result.lines)
        assert any("enough" in line for line in result.lines)

    def test_a_disk_that_cannot_be_measured_is_named_and_blocks_nothing(self, tmp_path, no_source, dri,
                                                                         verify_in_container):
        client = FakeClient()   # Docker does not say where it keeps its images
        newer = with_libc(Z1, "2.42")
        runtime(tmp_path, client, profile=newer).install()
        result = ensure(runtime(tmp_path, client, profile=newer), mode="docker", force=True)
        assert result.mode == "docker"
        assert any("Docker image storage" in line and "could not be read" in line for line in result.lines)

    def test_docker_to_bare_metal_reusing_the_images_build_needs_no_space(self, tmp_path, no_source, dri,
                                                                          verify_in_container, monkeypatch):
        client = FakeClient()
        ensure(runtime(tmp_path, client, native=False), mode="docker")
        monkeypatch.setattr(runtime_mode, "_disk_usage", disks(1 * GB))
        back = runtime(tmp_path, client, native=False, executor=Refuses())
        result = ensure(back, mode="bare-metal", force=True)
        assert result.space.total == 0 and result.space.fits and back.mode() == "bare-metal"

    def test_the_old_mode_removed_first_then_the_new_one_is_made(self, tmp_path, no_source, dri,
                                                                 verify_in_container, monkeypatch):
        """W2: the person chose to remove bare metal first: every object of it (also the build in use) is deleted for
        good after Ditto's server was stopped; then Docker mode is made and nothing of bare metal is left."""
        client = FakeClient()
        newer = with_libc(Z1, "2.42")
        rt = runtime(tmp_path, client, profile=newer)
        rt.install()
        plan = runtime_mode.old_mode_plan(rt, "bare-metal")
        assert {i.kind for i in plan.items} == {"runtime_build", "runtime_record"}
        assert any("the active bare-metal build" in i.what for i in plan.items), "also the build in use"
        stops = []
        rt.stop = lambda timeout=15: stops.append(1) or 1
        audit = Audit()
        removal = remove_leftovers(rt, plan, actor="cli", audit=audit, before_switch=True, removes="bare-metal")
        assert removal.stopped and stops == [1] and not removal.failed
        assert rt.installed() is None and runtime_mode.old_mode_plan(rt, "bare-metal") is None
        assert audit.rows[0]["arguments"]["before_switch"] is True
        result = ensure(runtime(tmp_path, client, profile=newer), mode="docker", force=True)
        assert result.mode == "docker" and result.leftovers is None, "nothing of bare metal is left to list"

    def test_removing_first_is_not_offered_when_the_new_mode_would_still_not_fit(self, tmp_path, no_source, dri,
                                                                                monkeypatch):
        """W2: bare metal -> Docker by repackaging does not fit. Once bare metal is deleted, its build can no longer
        be repackaged, so Docker mode would need a new build in the builder container. The estimate after removing
        the old mode first is made without its build and counts what its objects free on their disk; here it does
        not fit either, so deleting bare metal first is not offered (it would only leave Ditto without a runtime)."""
        client = FakeClient()
        rt = runtime(tmp_path, client)
        rt.install()
        _docker_disk(client, tmp_path / "docker-root")
        monkeypatch.setattr(runtime_mode, "_folder_size", lambda path: GB)
        monkeypatch.setattr(runtime_mode, "_disk_usage", disks(21 * GB))   # 20 GB of 1000 GB are kept free
        with pytest.raises(runtime_mode.SpaceShortage) as e:
            ensure(runtime(tmp_path, client, executor=Refuses()), mode="docker", force=True)
        shortage = e.value
        assert "repackaged" in shortage.estimate.how and client.builds == []
        after = shortage.after_removal
        assert after is not None and "builder container" in after.how and after.total > shortage.estimate.total
        assert after.freed == shortage.old_plan.total_bytes > 2 * GB - 1
        (before_check,), (after_check,) = shortage.estimate.checks, after.checks
        assert after_check.free == before_check.free + after.freed, "what the old mode frees there is counted"
        assert shortage.removal_helps is False and not after.fits
        assert any("deleted first" in line for line in after.lines())
        text = str(shortage)
        assert "would not fit either" in text and "remove bare metal first" not in text

    def test_removing_first_is_refused_when_the_list_changed(self, tmp_path, no_source, dri):
        rt = runtime(tmp_path, FakeClient())
        rt.install()
        plan = runtime_mode.old_mode_plan(rt, "bare-metal")
        (rt.root / "b1000-cpu-x64").mkdir()
        (rt.root / "b1000-cpu-x64" / "runtime.json").write_text("{}")
        with pytest.raises(LeftoversChanged):
            remove_leftovers(rt, plan, actor="gui", audit=Audit(), before_switch=True, removes="bare-metal")
        assert rt.installed() is not None


# --- what an AI may do ------------------------------------------------------------------------------------------------

class TestStoreAndSwitchDetails:

    def test_a_build_verified_in_the_container_runs_here_only_after_it_is_verified_here(self, tmp_path):
        from orchestrator.runtime.build.recipe import plan_build
        from orchestrator.runtime.build.store import StoreError
        from test_runtime_selfbuilt import GCC
        rt = runtime(tmp_path)
        plan = plan_build(Z1, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                          toolchain=GCC)
        build = rt.store.build(plan, source=rt.root, executor=FakeExecutor(), verifier=FakeVerifier(),
                               host=("linux", "x86_64"), verified_in="container")
        assert build.verified_in == "container" and build.verified_anywhere and not build.verified
        with pytest.raises(StoreError, match="verification"):
            rt.store.activate(build.build_id)
        with pytest.raises(Exception, match="verification"):
            rt.store.build(plan, source=rt.root, executor=Refuses(), verifier=FakeVerifier(ok=False),
                           host=("linux", "x86_64"))
        assert not rt.store.get(build.build_id).verified, "a failed check here changes nothing"
        again = rt.store.build(plan, source=rt.root, executor=Refuses(), verifier=FakeVerifier(),
                               host=("linux", "x86_64"))
        assert again.build_id == build.build_id and again.verified and again.verified_in == "native"

    def test_the_list_of_old_builds_keeps_the_build_of_the_image(self, tmp_path):
        """The person's list of old builds (`ditto runtime prune`, WP7) never offers the build Ditto's image was made
        from, and a removal of it keeps it; once forgotten, it is an old build like any other."""
        rt = runtime(tmp_path)
        build = _stored_native(rt)
        rt.store.keep_for_docker(build.build_id)
        assert rt.store.removable() == [] and rt.store.remove([build.build_id])[0] == []
        assert rt.store.get(build.build_id) is not None
        assert rt.store.forget(build.build_id[:16]) == ["docker"] and rt.store.references() == {}
        assert [b.build_id for b in rt.store.removable()] == [build.build_id]

    def test_a_switch_stops_the_old_modes_server_and_says_so(self, tmp_path, no_source, dri):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        rt.install()
        switched = runtime(tmp_path, client, executor=Refuses())
        stops = []
        switched.stop = lambda timeout=15: stops.append(1) or 1
        result = ensure(switched, mode="docker", force=True)
        assert stops == [1] and any("server of the bare metal mode was stopped" in line for line in result.lines)

    def test_the_texts_have_the_same_placeholders_in_both_languages(self):
        from orchestrator.utils.l10n_runtime import TEXTS
        assert set(TEXTS["en"]) == set(TEXTS["de"])
        for key, text in TEXTS["en"].items():
            assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", TEXTS["de"][key])), key

    def test_an_ai_may_start_the_server_of_the_docker_mode(self, make_framework, tmp_path, monkeypatch, dri):
        from test_runtime_tools import allow, asking, call, wait_job
        fw = make_framework()
        client = FakeClient()
        rt = runtime(tmp_path / "rt", client)
        _docker_mode(rt, client)
        _model(rt)
        rt._health = lambda port: 200
        rt._check_auth = lambda port, key: None
        monkeypatch.setattr(LocalLLMRuntime, "from_framework", classmethod(lambda cls, framework, **kw: rt))
        allow(fw, "start_local_runtime")
        confirm = asking(True)
        started = call(fw, "start_local_runtime", {"tier": "small"}, confirm=confirm)
        job = wait_job(fw, started.job_id)
        assert job.state == "done", job.error
        assert str(tmp_path) not in json.dumps(job.result) and job.result["running"]
        assert [c for c in client.created if c.labels.get(cs.LABEL) == "server"]
        # the result names what runs: the image's build, not a bare-metal build (there is none here)
        assert job.result["build_id"] == rt.mode_record()["docker"]["build_id"]
        assert "Docker" in job.result["origin"] and "repackaged" in job.result["origin"]


class TestForAnAI:

    def test_no_tool_switches_the_mode_or_removes_the_old_one(self):
        import ast
        for path in list((REPO_ROOT / "orchestrator" / "tools").glob("*.py")) + [REPO_ROOT / "orchestrator" /
                                                                                   "mcp_server.py"]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = [alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                        for alias in node.names] + [node.module or "" for node in ast.walk(tree)
                                                    if isinstance(node, ast.ImportFrom)]
            called = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)]
            assert not any("runtime_mode" in name for name in imported), path.name
            assert "remove_leftovers" not in called and "record_mode" not in called, path.name

    def test_the_status_shows_the_mode_without_paths(self, tmp_path, monkeypatch):
        from orchestrator.tools import tools_status
        client = FakeClient()
        rt = runtime(tmp_path, client)
        _docker_mode(rt, client)
        monkeypatch.setattr(LocalLLMRuntime, "from_framework", classmethod(lambda cls, fw, **kw: rt))
        section = tools_status.local_runtime_section(SimpleNamespace(config=SimpleNamespace(
            user_state_dir=str(tmp_path / "state"))))
        assert section.mode == "docker" and section.installed and section.docker_made_by == "repackaged"
        assert section.docker_image.startswith("llm-framework/ditto-runtime:")
        assert str(tmp_path) not in section.model_dump_json()


# --- the CLI ---------------------------------------------------------------------------------------------------------

def _cli(monkeypatch, rt, *args, **kw):
    from click.testing import CliRunner
    from rich.console import Console
    from orchestrator import cli as cli_module
    monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
    # One line per table row: on a narrow console a long full path (a Windows temp folder) folds the cells, and a
    # file name split over two lines is no longer one piece in the flattened output.
    monkeypatch.setattr(cli_module, "console", Console(width=1000, color_system=None))
    return CliRunner().invoke(cli_module.cli, ["-q", "ditto", "runtime", *args], **kw)


class TestCli:

    def test_force_is_the_persons_decision_at_a_terminal(self, tmp_path, monkeypatch):
        called = []
        monkeypatch.setattr(runtime_mode, "ensure", lambda *a, **k: called.append(1))
        result = _cli(monkeypatch, runtime(tmp_path), "ensure", "--mode", "docker", "--force")
        assert result.exit_code == 2 and "needs a terminal" in result.output and called == []
        result = _cli(monkeypatch, runtime(tmp_path), "ensure", "--force")
        assert result.exit_code == 2 and "--force needs a mode" in result.output

    def test_a_second_run_says_nothing_to_do_and_downloads_nothing_without_a_terminal(self, tmp_path, monkeypatch,
                                                                                     no_source, dri):
        rt = runtime(tmp_path)
        rt.install()
        downloads = []
        rt.download_model = lambda *a, **k: downloads.append(a)
        result = _cli(monkeypatch, rt, "ensure")
        assert result.exit_code == 0, result.output
        assert "Nothing to do" in result.output and "licence apache-2.0" in result.output
        assert "Not downloaded without a terminal" in result.output and downloads == []

    def test_yes_downloads_a_permissive_model_after_showing_size_and_licence(self, tmp_path, monkeypatch,
                                                                            no_source, dri):
        rt = runtime(tmp_path)
        rt.install()
        downloads = []
        rt.download_model = lambda tier, progress=None: downloads.append(tier) or rt.models_dir / "x.gguf"
        result = _cli(monkeypatch, rt, "ensure", "-y")
        assert result.exit_code == 0, result.output
        assert result.output.index("licence apache-2.0") < result.output.index("Verified and saved")
        assert downloads == [rt.choose_tier()[0].tier]

    def test_yes_never_accepts_another_licence(self, tmp_path, monkeypatch, no_source, dri):
        rt = runtime(tmp_path)
        rt.install()
        rt.catalog = rt.catalog.model_copy(update={"models": [m.model_copy(update={"license": "llama3-community"})
                                                              for m in rt.catalog.models]})
        downloads = []
        rt.download_model = lambda *a, **k: downloads.append(a)
        result = _cli(monkeypatch, rt, "ensure", "-y")
        assert "llama3-community" in result.output and downloads == []

    def test_leftovers_are_listed_and_nothing_is_removed_without_a_terminal(self, tmp_path, monkeypatch, no_source,
                                                                           dri):
        client = FakeClient()
        rt, plan = _switched_to_docker(tmp_path, client)
        monkeypatch.setattr(runtime_mode, "ensure", lambda *a, **k: runtime_mode.EnsureResult(
            "switched", "docker", ["switched"], leftovers=plan))
        removed = []
        monkeypatch.setattr(runtime_mode, "remove_leftovers", lambda *a, **k: removed.append(1))
        result = _cli(monkeypatch, rt, "ensure", "--no-model")
        assert result.exit_code == 0, result.output
        assert "Nothing was removed" in result.output and removed == []
        flat = re.sub(r"[\s│┃|]+", "", result.output)
        for item in plan.items:
            assert re.sub(r"\s+", "", item.file_name) in flat
        assert "deletedforgood(notintothetrash)" in flat and "themodelsstay" in flat, "what happens to them"

    def _shortage(self, rt, old="bare-metal", after_fits=None):
        """A shortage of Docker mode next to `old`; `after_fits`: whether it fits once the old mode is deleted first
        (None: that estimate could not be made)."""
        from orchestrator.Core.disk_plan import DiskCheck
        check = DiskCheck(["Ditto runtime folder"], 2 * GB, GB, 1000 * GB, 20 * GB, 0, "1")
        estimate = runtime_mode.SpaceEstimate("docker", "a new cpu build in the builder container",
                                              keeps=GB, checks=[check])
        after = None
        if after_fits is not None:
            free = 30 * GB if after_fits else 21 * GB
            after = runtime_mode.SpaceEstimate("docker", "a new cpu build in the builder container", keeps=GB,
                                               checks=[DiskCheck(["Ditto runtime folder"], 6 * GB, free, 1000 * GB,
                                                                 20 * GB, 0, "1")], freed=GB)
        return runtime_mode.SpaceShortage(estimate, old, runtime_mode.old_mode_plan(rt, old), after)

    def _terminal_cli(self, monkeypatch, rt, answers, ensure_steps):
        """`ditto runtime ensure --mode docker --force` at a terminal: each call of ensure takes the next step (an
        exception is raised, a result returned); remove_leftovers is recorded."""
        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        calls, removed = [], []

        def fake_ensure(rt_, **kw):
            calls.append((kw["mode"], kw["force"]))
            step = ensure_steps.pop(0)
            if isinstance(step, BaseException):
                raise step
            return step
        monkeypatch.setattr(runtime_mode, "ensure", fake_ensure)
        monkeypatch.setattr(runtime_mode, "remove_leftovers", lambda rt_, plan, **k: removed.append((plan, k))
                            or runtime_mode.Removal(deleted=[i.speaking_name for i in plan.items], stopped=True))
        result = _cli(monkeypatch, rt, "ensure", "--mode", "docker", "--force", "--no-model", input=answers)
        return result, calls, removed

    def test_a_switch_that_does_not_fit_lists_the_old_mode_and_no_removes_and_builds_nothing(self, tmp_path,
                                                                                           monkeypatch, no_source,
                                                                                           dri):
        rt = runtime(tmp_path)
        rt.install()
        shortage = self._shortage(rt)
        result, calls, removed = self._terminal_cli(monkeypatch, rt, "n\n", [shortage])
        assert result.exit_code == 1 and removed == [] and calls == [("docker", True)], result.output
        flat = re.sub(r"[\s│┃|]+", "", result.output)
        assert "doesnotfitnexttobaremetal" in flat and "notenough" in flat
        assert "unavailableuntilDockerworks" in flat and "themodelsstay" in flat
        for item in shortage.old_plan.items:
            assert re.sub(r"\s+", "", item.file_name) in flat
        assert "Nothingwasdeletedandnothingwasbuilt" in flat

    def test_removing_the_old_mode_first_then_switching(self, tmp_path, monkeypatch, no_source, dri):
        rt = runtime(tmp_path)
        rt.install()
        shortage = self._shortage(rt, after_fits=True)
        done = runtime_mode.EnsureResult("switched", "docker", ["Ditto's runtime mode is now Docker."])
        result, calls, removed = self._terminal_cli(monkeypatch, rt, "y\n", [shortage, done])
        assert result.exit_code == 0, result.output
        (plan, kw), = removed
        assert plan is shortage.old_plan and kw["actor"] == "cli" and kw["before_switch"] is True
        assert kw["removes"] == "bare-metal" and calls == [("docker", True), ("docker", True)]
        assert "server was stopped first" in result.output
        assert "With bare metal deleted first" in re.sub(r"\s+", " ", result.output), "the sizes after the removal"

    def test_removing_first_is_not_offered_when_the_new_mode_would_still_not_fit(self, tmp_path, monkeypatch,
                                                                                no_source, dri):
        """W2: when Docker mode would not fit even with bare metal deleted first, deleting it is not offered: the
        person frees space; nothing is deleted or built."""
        rt = runtime(tmp_path)
        rt.install()
        shortage = self._shortage(rt, after_fits=False)
        result, calls, removed = self._terminal_cli(monkeypatch, rt, "y\ny\n", [shortage])
        assert result.exit_code == 1 and removed == [] and calls == [("docker", True)], result.output
        flat = re.sub(r"\s+", " ", result.output)
        assert "Even with bare metal deleted first, Docker would not fit" in flat
        assert "Delete all" not in flat and "llm-cli ditto runtime ensure --mode docker --force" in flat
        assert rt.installed() is not None

    def test_a_shortage_without_a_terminal_deletes_nothing_whatever_stdin_holds(self, tmp_path, monkeypatch,
                                                                               no_source, dri):
        """W2: deleting the old mode first is asked only of a person at a terminal. A run without one (a pipe, the
        installers' step, a script) shows the sizes and the list and deletes nothing, even when its input holds a
        'y'."""
        rt = runtime(tmp_path)
        rt.install()
        shortage = self._shortage(rt, after_fits=True)
        calls, removed = [], []

        def fake_ensure(rt_, **kw):
            calls.append(kw["mode"])
            raise shortage
        monkeypatch.setattr(runtime_mode, "ensure", fake_ensure)
        monkeypatch.setattr(runtime_mode, "remove_leftovers", lambda *a, **k: removed.append(1))
        result = _cli(monkeypatch, rt, "ensure", "--no-model", input="y\ny\n")
        assert result.exit_code == 1 and removed == [] and calls == ["auto"], result.output
        flat = re.sub(r"[\s│┃|]+", "", result.output)
        for item in shortage.old_plan.items:
            assert re.sub(r"\s+", "", item.file_name) in flat
        assert "Nothingwasdeletedandnothingwasbuilt" in flat and "terminal" in result.output
        assert rt.installed() is not None

    def test_when_the_new_mode_fails_after_the_old_one_was_removed_it_is_said_and_the_old_one_offered(
            self, tmp_path, monkeypatch, no_source, dri):
        rt = runtime(tmp_path)
        rt.install()
        shortage = self._shortage(rt)
        back = runtime_mode.EnsureResult("installed", "bare-metal", ["Built for bare metal."])
        result, calls, removed = self._terminal_cli(monkeypatch, rt, "y\ny\n", [
            shortage, RuntimeModeError("the build for Ditto's image failed"), back])
        assert result.exit_code == 1 and len(removed) == 1
        flat = re.sub(r"\s+", " ", result.output)
        assert "Docker could not be made: the build for Ditto's image failed" in flat
        assert "Ditto has no local runtime now: bare metal was deleted before" in flat
        assert calls == [("docker", True), ("docker", True), ("bare-metal", True)], "the old mode made again"
        result, calls, removed = self._terminal_cli(monkeypatch, rt, "y\nn\n", [
            self._shortage(rt), RuntimeModeError("no")])
        assert calls == [("docker", True), ("docker", True)] and "llm-cli ditto runtime ensure --mode bare-metal" in \
            result.output

    def test_status_shows_the_mode(self, tmp_path, monkeypatch):
        client = FakeClient()
        rt = runtime(tmp_path, client)
        _docker_mode(rt, client)
        data = json.loads(_cli(monkeypatch, rt, "status", "--json").output)
        assert data["mode"] == "docker" and data["docker"]["made_by"] == "repackaged"
        flat = re.sub(r"[\s│┃|]+", "", _cli(monkeypatch, rt, "status").output)
        assert "Docker(llm-clidittoruntimeensure--modebare-metal--force" in flat
        assert "verifiedinacontainer" in flat and "repackaged" in flat


# --- the AI settings ---------------------------------------------------------------------------------------------------

class TestAISettings:

    def test_the_box_shows_the_mode_and_switches_through_ensure_after_the_question(self, qapp, tmp_path,
                                                                                  monkeypatch):
        from orchestrator.gui import runtime_mode_box as box_module
        client = FakeClient()
        rt = runtime(tmp_path, client)
        _docker_mode(rt, client)
        box = box_module.RuntimeModeBox(None, runtime_factory=lambda: rt)
        assert "Docker" in box.lbl_mode.text() and "repackaged" in box.lbl_mode.text()
        calls, questions = [], []
        monkeypatch.setattr(box_module.runtime_mode, "ensure", lambda *a, **k: calls.append(k))
        monkeypatch.setattr(box_module, "ask", lambda parent, title, text: questions.append(text) or False)
        box.cb_mode.setCurrentIndex(box.cb_mode.findData("bare-metal"))
        box._switch()
        assert calls == [] and box._token is None, "No: nothing happens"
        assert "not downloaded again" in questions[0] and "fits next to the old mode" in questions[0]
        assert "deleted for good" in questions[0]

    def test_the_leftovers_question_lists_every_object_and_no_removes_nothing(self, qapp, tmp_path, monkeypatch,
                                                                            no_source, dri):
        from orchestrator.gui import runtime_mode_box as box_module
        rt, plan = _switched_to_docker(tmp_path, FakeClient())
        text = box_module.leftovers_text(plan)
        for item in plan.items:
            assert item.file_name in text and item.what[:40] in text
            assert (item.full_path in text) if item.full_path else (f"ID: {item.file_name}" in text)
        questions, removed, shown = [], [], []
        monkeypatch.setattr(box_module, "ask", lambda parent, title, question: questions.append(question) or False)
        monkeypatch.setattr(box_module, "plain_box", lambda *a, **k: shown.append(a))
        monkeypatch.setattr(box_module.runtime_mode, "remove_leftovers", lambda *a, **k: removed.append(k))
        box = box_module.RuntimeModeBox(None, runtime_factory=lambda: rt)
        box._offer_removal(plan)
        assert removed == [] and str(len(plan.items)) in questions[0]
        monkeypatch.setattr(box_module, "ask", lambda *a: True)
        monkeypatch.setattr(box_module.runtime_mode, "remove_leftovers", lambda rt_, plan_, **k: removed.append(k)
                            or runtime_mode.Removal())
        box._offer_removal(plan)
        assert removed[-1] == {"actor": "gui", "audit": None}, "the whole list, for good, no trash"
        assert len([q for q in questions]) == 1, "one question for the whole list (round 7 D)"

    def test_the_question_says_everything_goes_for_good_and_nothing_reaches_the_trash(self, qapp, tmp_path,
                                                                                     monkeypatch, no_source, dri):
        from orchestrator.Core.trash import Trash
        from orchestrator.gui import runtime_mode_box as box_module
        client = FakeClient()
        rt, plan = _switched_to_docker(tmp_path, client)
        assert "Goes to" not in box_module.leftovers_text(plan), "no choice and no trash per item"
        asked, shown = [], []
        monkeypatch.setattr(box_module, "ask", lambda parent, title, question: asked.append(question) or True)
        monkeypatch.setattr(box_module, "plain_box", lambda *a, **k: shown.append(a[-1]))
        audit = Audit()
        framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path / "state", get=lambda k, d=None: d),
                                    tool_registry=SimpleNamespace(audit=audit, policy_store=None))
        box = box_module.RuntimeModeBox(framework, runtime_factory=lambda: rt)
        box._offer_removal(plan)
        question = asked[0].split("\n\n")[0]
        assert len(asked) == 1 and "for good" in question and "Nothing goes to the trash" in question
        assert "The models stay" in question
        assert Trash(tmp_path / "state").entries() == [] and not any(
            Path(i.full_path).exists() for i in plan.items if i.kind == "runtime_build")
        assert "deleted for good" in shown[-1]

    def test_a_switch_that_does_not_fit_asks_once_to_delete_the_old_mode_first(self, qapp, tmp_path, monkeypatch,
                                                                              no_source, dri):
        from orchestrator.Core.disk_plan import DiskCheck
        from orchestrator.gui import runtime_mode_box as box_module
        rt = runtime(tmp_path)
        rt.install()
        check = DiskCheck(["Ditto runtime folder"], 2 * GB, GB, 1000 * GB, 20 * GB, 0, "1")
        estimate = runtime_mode.SpaceEstimate("docker", "a new cpu build", keeps=GB, checks=[check])
        shortage = runtime_mode.SpaceShortage(estimate, "bare-metal", runtime_mode.old_mode_plan(rt, "bare-metal"))
        asked, shown, removed, started = [], [], [], []
        monkeypatch.setattr(box_module, "ask", lambda parent, title, question: asked.append(question) or False)
        monkeypatch.setattr(box_module, "plain_box", lambda *a, **k: shown.append(a[-1]))
        monkeypatch.setattr(box_module.runtime_mode, "remove_leftovers", lambda rt_, plan_, **k: removed.append(k)
                            or runtime_mode.Removal(deleted=["x"]))
        box = box_module.RuntimeModeBox(None, runtime_factory=lambda: rt)
        monkeypatch.setattr(box, "_start", lambda *a, **k: started.append((a, k)))
        box._on_relay(box._token, None)   # nothing pending: ignored
        box._token, box._pending = object(), ("docker", None)
        box._on_relay(box._token, ("space", shortage))
        question = asked[-1]
        assert "Docker mode does not fit next to bare metal" in question and "not enough" in question
        assert "Ditto is unavailable until Docker mode works; the models stay" in question
        for item in shortage.old_plan.items:
            assert item.file_name in question
        assert removed == [] and started == [] and "Nothing was deleted and nothing was built" in shown[-1]
        monkeypatch.setattr(box_module, "ask", lambda parent, title, question: asked.append(question) or True)
        box._space_shortage(shortage)
        assert removed == [{"actor": "gui", "audit": None, "before_switch": True, "removes": "bare-metal"}]
        assert started == [(("docker", True), {"after_removal": "bare-metal"})]

    def test_deleting_first_is_not_offered_when_it_would_not_help(self, qapp, tmp_path, monkeypatch, no_source, dri):
        """W2: Docker mode would not fit even with bare metal deleted first: the GUI says so with both estimates and
        asks nothing; nothing is deleted or built. When it would fit, the question shows the estimate after the
        removal too."""
        from orchestrator.Core.disk_plan import DiskCheck
        from orchestrator.gui import runtime_mode_box as box_module
        rt = runtime(tmp_path)
        rt.install()
        plan = runtime_mode.old_mode_plan(rt, "bare-metal")
        check = DiskCheck(["Ditto runtime folder"], 2 * GB, GB, 1000 * GB, 20 * GB, 0, "1")
        estimate = runtime_mode.SpaceEstimate("docker", "the bare-metal build repackaged", keeps=GB, checks=[check])

        def after(free):
            return runtime_mode.SpaceEstimate("docker", "a new cpu build in the builder container", keeps=GB,
                                              checks=[DiskCheck(["Ditto runtime folder"], 6 * GB, free, 1000 * GB,
                                                                20 * GB, 0, "1")], freed=GB)
        asked, shown, removed, started = [], [], [], []
        monkeypatch.setattr(box_module, "ask", lambda parent, title, question: asked.append(question) or False)
        monkeypatch.setattr(box_module, "plain_box", lambda *a, **k: shown.append(a[-1]))
        monkeypatch.setattr(box_module.runtime_mode, "remove_leftovers", lambda *a, **k: removed.append(k))
        box = box_module.RuntimeModeBox(None, runtime_factory=lambda: rt)
        monkeypatch.setattr(box, "_start", lambda *a, **k: started.append((a, k)))
        box._space_shortage(runtime_mode.SpaceShortage(estimate, "bare-metal", plan, after(21 * GB)))
        assert asked == [] and removed == [] and started == []
        assert "Even with bare metal deleted first, Docker mode would not fit" in shown[-1]
        assert "a new cpu build in the builder container" in shown[-1]
        box._space_shortage(runtime_mode.SpaceShortage(estimate, "bare-metal", plan, after(30 * GB)))
        assert len(asked) == 1 and "With bare metal deleted first" in asked[-1] and removed == []

    def test_a_failure_after_the_old_mode_was_deleted_offers_it_again(self, qapp, tmp_path, monkeypatch):
        from orchestrator.gui import runtime_mode_box as box_module
        rt = runtime(tmp_path)
        asked, started, shown = [], [], []
        monkeypatch.setattr(box_module, "ask", lambda parent, title, question: asked.append(question) or True)
        monkeypatch.setattr(box_module, "plain_box", lambda *a, **k: shown.append(a[-1]))
        box = box_module.RuntimeModeBox(None, runtime_factory=lambda: rt)
        monkeypatch.setattr(box, "_start", lambda *a, **k: started.append((a, k)))
        box._token, box._pending = object(), ("docker", "bare-metal")
        box._on_relay(box._token, ("error", "the build for Ditto's image failed"))
        assert "Docker mode could not be made: the build for Ditto's image failed" in asked[-1]
        assert "Ditto has no local runtime now: bare metal was deleted before" in asked[-1]
        assert started == [(("bare-metal", True), {})]
        monkeypatch.setattr(box_module, "ask", lambda parent, title, question: False)
        box._token, box._pending = object(), ("docker", "bare-metal")
        box._on_relay(box._token, ("error", "no"))
        # Wave 6 (DECISIONS round 9 U2): the window says the mode can be made again here; it named the CLI command
        # (`ditto runtime ensure --mode bare-metal`) before and never tells a command now.
        assert "You can make it again here later." in shown[-1] and "llm-cli" not in shown[-1]
        assert len(started) == 1


# --- the installers ---------------------------------------------------------------------------------------------------

INSTALL_SH = REPO_ROOT / "install.sh"
needs_bash = pytest.mark.skipif(not Path("/bin/bash").exists(), reason="needs bash")


def _recorder(folder: Path, name: str, exit_code: int = 0) -> None:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{folder}/calls.log"\nexit {exit_code}\n')
    path.chmod(0o755)


@needs_bash
class TestInstaller:

    def _run(self, tmp_path, script, env=None, sudo=True, exit_code=0):
        tmp_path = Path(tempfile.mkdtemp(dir=tmp_path))  # every run its own stubs and record
        stubs = tmp_path / "bin"
        _recorder(stubs, "runuser", exit_code)
        if sudo:
            _recorder(stubs, "sudo", exit_code)
        for tool in ("bash", "date", "id"):
            if shutil.which(tool):
                (stubs / tool).symlink_to(shutil.which(tool))
        base = {"PATH": str(stubs), "HOME": str(tmp_path), "SUDO_USER": "anna"}
        run = subprocess.run(["/bin/bash", "-c", f'source "$1"; INSTALL_DIR="$2"; {script}', "test", str(INSTALL_SH),
                              str(tmp_path)], env={**base, **(env or {})}, capture_output=True, text=True,
                             timeout=60)
        calls = (stubs / "calls.log").read_text() if (stubs / "calls.log").exists() else ""
        return run, calls

    def test_the_step_runs_ensure_as_the_person_and_never_as_root(self, tmp_path):
        run, calls = self._run(tmp_path, "setup_ditto")
        assert run.returncode == 0, run.stdout + run.stderr
        assert calls.startswith("sudo -H --preserve-env=https_proxy,") and " -u anna " in calls
        assert calls.rstrip().endswith("-m orchestrator.cli ditto runtime ensure --mode auto"), calls
        run, calls = self._run(tmp_path, "setup_ditto", env={"SUDO_USER": "root"})
        assert run.returncode == 0 and calls == "" and "nicht für root" in run.stdout

    def test_the_option_and_the_unattended_variable(self, tmp_path):
        run, calls = self._run(tmp_path, 'ditto_option --ditto=docker; setup_ditto', env={"LCF_DITTO": "auto"})
        assert run.returncode == 0 and calls.rstrip().endswith("ensure --mode docker --yes"), calls
        run, calls = self._run(tmp_path, 'ditto_option --other --ditto=skip; setup_ditto')
        assert run.returncode == 0 and calls == "" and "Übersprungen" in run.stdout
        run, calls = self._run(tmp_path, 'ditto_option --ditto=everything; echo AFTER')
        assert run.returncode != 0 and "AFTER" not in run.stdout and "Unbekannter Wert" in run.stdout

    def test_a_failing_step_never_fails_the_installation(self, tmp_path):
        run, _ = self._run(tmp_path, "setup_ditto; echo AFTER", exit_code=1)
        assert run.returncode == 0 and "AFTER" in run.stdout and "trotzdem fertig" in run.stdout

    def test_in_the_frameworks_container_build_the_step_says_why_and_does_nothing(self, tmp_path):
        run, calls = self._run(tmp_path, "setup_ditto", env={"LCF_CONTAINER_BUILD": "1"})
        assert run.returncode == 0 and calls == ""
        assert "Docker-Modus ist im Framework-Container nicht verfügbar" in run.stdout
        # docs/Container_Setup: inside the container ensure cannot work either, so it is not suggested there
        assert "docker exec" not in run.stdout and "Cloud-Anbieter" in run.stdout

    def test_without_any_user_variable_the_step_never_fails_the_installation(self, tmp_path):
        run, calls = self._run(tmp_path, "setup_ditto; echo AFTER", env={"SUDO_USER": ""})  # USER is not set either
        assert run.returncode == 0 and "AFTER" in run.stdout and calls == "", run.stdout + run.stderr
        assert "nicht für root" in run.stdout

    def test_without_sudo_runuser_is_used(self, tmp_path):
        run, calls = self._run(tmp_path, "setup_ditto", sudo=False)
        assert run.returncode == 0 and calls.startswith("runuser -u anna -- "), calls

    def test_main_runs_the_step_after_the_launcher(self):
        text = INSTALL_SH.read_text(encoding="utf-8")
        main = text[text.index("main() {"):]
        assert main.index('ditto_option "$@"') < main.index("check_root")
        assert main.index("configure") < main.index("setup_ditto") < main.index("finish")

    def test_install_bat_has_the_same_step(self):
        text = (REPO_ROOT / "install.bat").read_text(encoding="utf-8")
        assert 'set "LCF_INSTALL_ARGS=%*"' in text and '--ditto=*' in text and "$env:LCF_DITTO" in text
        step = text[text.index("[Ditto]"):]
        assert '"ditto", "runtime", "ensure", "--mode", $DittoChoice' in step
        assert "Write-Error" not in step, "a failing Ditto step only warns; the installation goes on"
        assert "Win32_ComputerSystem" in step, "only for the signed-in person, never another administrator account"
