#!/usr/bin/env python3
"""
Ditto's local model server in a container: the runtime mode "docker" (DECISIONS round 7 D).

The image is built on this computer from the same pinned llama.cpp recipe as the bare-metal server. It is never pulled
as a finished image and never pushed: there is no push code at all. build/ditto-runtime.Dockerfile has two stages on a
Docker Official base image of configs/llama_runtime.yml (build_bases), pinned by digest: "runtime-base" installs only
the runtime libraries from the base's own package archive, "ditto" adds the programs of one verified build of the
build store. The programs come from

- a bare-metal build that is already there ("repackaged"): a build of the builder container goes on the base it was
  compiled on; a native build goes on the base with the same C library that is at least as new as this machine's; or
- a new build in the builder container ("rebuilt"; orchestrator/runtime/build, no network during the build), verified
  in a container of the runtime-base stage, where it will run (ContainerRunner).

Every image is verified in a container of itself before it is used (verify_image): llama-server and llama-bench report
the pinned build number and commit, llama-server refuses --tools, and a GPU build sees its device when the GPU is
passed through. Images and containers carry the label org.llm-framework.ditto-runtime ("base", "image", "server",
"check"); that label is how the framework finds them, and it never touches anything without it.

The server container (start_server) keeps the launch policy of every llama-server (server_policy): the command line of
container_server_command (no forbidden option, the key file, no web UI, no /slots, offline), the port published on
127.0.0.1 only (check_publish), the models folder mounted read-only (the models are not copied), the per-start key
file mounted read-only, the host user instead of root, no capabilities, no privilege escalation, a read-only root file
system, and the GPU passed through where it can be (gpu_passthrough: the NVIDIA Container Toolkit for NVIDIA GPUs,
/dev/dri for AMD and Intel GPUs; an honest note where it cannot). A container is created and started by the image's ID,
so Docker never pulls anything for it, and Docker removes it when it stops (auto-remove): stopping the server leaves
nothing behind. The short check containers are removed right after they ran, as the builder's are.
"""

import hashlib
import os
import re
import secrets
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from orchestrator.runtime.build.executor import DockerEngine, Mount
from orchestrator.runtime.build.verify import Check, VerifyReport, parse_devices, parse_version, sha256_file
from orchestrator.runtime.server_policy import (CONTAINER_PORT, LOOPBACK, SERVER_TOOLS_REFUSAL, PolicyError,
                                                check_command, check_container_server_command, check_publish,
                                                tools_check_args)
from orchestrator.utils.sandbox import container_sandbox, hand_over, sandbox_user

RUNTIME_DOCKERFILE = Path(__file__).resolve().parent / "build" / "ditto-runtime.Dockerfile"
LABEL = "org.llm-framework.ditto-runtime"
LABEL_BUILD = "org.llm-framework.ditto-build-id"
LABEL_COMMIT = "org.llm-framework.llama-commit"
LABEL_BASE = "org.llm-framework.ditto-base"
LABEL_BACKEND = "org.llm-framework.ditto-backend"
LABEL_MADE_BY = "org.llm-framework.ditto-made-by"
LABEL_USER = "org.llm-framework.ditto-user"
IMAGE_REPO = "llm-framework/ditto-runtime"
BASE_REPO = "llm-framework/ditto-runtime-base"
SERVER_IN_IMAGE = "/opt/ditto/bin/llama-server"
BENCH_IN_IMAGE = "/opt/ditto/bin/llama-bench"
MODELS_MOUNT = "/models"
KEY_MOUNT = "/run/ditto/server.key"
CHECK_MOUNT = "/run/ditto-check"
BACKENDS = ("cpu", "vulkan")          # what the builder builds today (CUDA and ROCm follow)
MADE_BY = ("repackaged", "rebuilt")
CHECK_TIMEOUT = 120
DEVICES_TIMEOUT = 180
SERVER_PIDS = 512
DRI = Path("/dev/dri")                # the render nodes of AMD and Intel GPUs on Linux
# The tmpfs inside every container of Ditto's (utils/sandbox.py TMPFS), never a folder of this computer.
CONTAINER_TMP = "/tmp"  # nosec B108

# A Docker Official Image: no registry and no namespace, pinned by digest (debian, ubuntu, alpine in the catalog).
_RE_OFFICIAL = re.compile(r"^[a-z0-9][a-z0-9._-]*(:[A-Za-z0-9._-]{1,128})?@sha256:[0-9a-f]{64}$", re.ASCII)
_RE_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_RE_CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$", re.ASCII)


class ContainerError(RuntimeError):
    """Ditto's container cannot be built, checked or run; the message is safe to show. log_tail is the server's own
    output (untrusted text)."""

    def __init__(self, message: str, log_tail: str = ""):
        super().__init__(message)
        self.log_tail = log_tail


def is_official_image(image: str) -> bool:
    """A Docker Official Image pinned by digest (name[:tag]@sha256:...), never one of another registry or namespace."""
    return bool(_RE_OFFICIAL.fullmatch(image or ""))


@dataclass(frozen=True)
class ImageInfo:
    image_id: str
    tags: Tuple[str, ...]
    labels: Mapping[str, str]
    size: int
    created: str = ""
    repo_digests: Tuple[str, ...] = ()   # RepoDigests of docker inspect: set only for an image of a registry

    @property
    def role(self) -> str:
        """base, image or "" (not one of Ditto's images)."""
        return str(self.labels.get(LABEL, ""))

    @property
    def name(self) -> str:
        return self.tags[0] if self.tags else f"{self.image_id[7:19]} (no name)"

    @property
    def pulled(self) -> bool:
        """The image came from a registry (or was pushed to one): anyone can put Ditto's labels on such an image, and
        Ditto's image is only ever built on this computer, so it is never used."""
        return bool(self.repo_digests)


@dataclass(frozen=True)
class ContainerInfo:
    container_id: str
    name: str
    status: str
    labels: Mapping[str, str]
    image_id: str = ""
    ports: Mapping[str, Any] = field(default_factory=dict)   # NetworkSettings.Ports of docker inspect
    size: int = 0

    @property
    def role(self) -> str:
        return str(self.labels.get(LABEL, ""))


def _image_info(image: Any) -> ImageInfo:
    attrs = getattr(image, "attrs", None) or {}
    try:
        size = max(0, int(attrs.get("Size") or 0))
    except (TypeError, ValueError):
        size = 0
    return ImageInfo(image_id=str(getattr(image, "id", "") or ""), tags=tuple(getattr(image, "tags", None) or ()),
                     labels=dict(getattr(image, "labels", None) or {}), size=size,
                     created=str(attrs.get("Created") or "")[:19],
                     repo_digests=tuple(str(d) for d in (attrs.get("RepoDigests") or ())))


def _container_info(container: Any) -> ContainerInfo:
    attrs = getattr(container, "attrs", None) or {}
    try:
        size = max(0, int(attrs.get("SizeRw") or 0))
    except (TypeError, ValueError):
        size = 0
    return ContainerInfo(container_id=str(getattr(container, "id", "") or ""),
                         name=str(getattr(container, "name", "") or "").lstrip("/"),
                         status=str(getattr(container, "status", "") or ""),
                         labels=dict(getattr(container, "labels", None) or {}), image_id=str(attrs.get("Image") or ""),
                         ports=dict((attrs.get("NetworkSettings") or {}).get("Ports") or {}), size=size)


class DittoDocker(DockerEngine):
    """The Docker side of Ditto's container through docker-py (no docker CLI): images and containers with the label
    LABEL only, built and started by this module. Tests pass a fake client."""

    def __init__(self, client: Any = None, timeout: int = 120):
        super().__init__(client)
        self._timeout = timeout

    def client(self) -> Any:
        if self._client is None:
            import docker
            from orchestrator.utils.docker_engine import ensure_docker_host
            ensure_docker_host()   # no Docker daemon: the person's Podman socket (DECISIONS round 26 Q1)
            self._client = docker.from_env(timeout=self._timeout)
        return self._client

    def info(self) -> Dict[str, Any]:
        return dict(self.client().info() or {})

    def gpu_request(self) -> Any:
        """A request for every NVIDIA GPU (docker run --gpus all)."""
        import docker
        return docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])

    def build_stage(self, context: Path, tag: str, buildargs: Mapping[str, str], labels: Mapping[str, str],
                    target: str, log: Path) -> None:
        """Builds one stage of the Dockerfile in `context` with a local tag (never pushed; the base is pulled only
        when it is not there yet, by its digest)."""
        with open(log, "a", encoding="utf-8") as out:
            for chunk in self.client().api.build(path=str(context), dockerfile="Dockerfile", tag=tag, target=target,
                                                 buildargs=dict(buildargs), labels=dict(labels), decode=True,
                                                 rm=True, forcerm=True, pull=False):
                if "stream" in chunk:
                    out.write(str(chunk["stream"]))
                if "error" in chunk:
                    out.write(str(chunk["error"]) + "\n")
                    raise ContainerError(f"the image {tag} could not be built: {str(chunk['error'])[:300]} "
                                         f"(log: {log})")

    def image(self, ref: str) -> Optional[ImageInfo]:
        try:
            return _image_info(self.client().images.get(ref))
        except Exception:  # noqa: BLE001 - not there (or the daemon did not answer)
            return None

    def images(self) -> List[ImageInfo]:
        """Every image with Ditto's label (base and image stages)."""
        return [i for i in (_image_info(x) for x in self.client().images.list(filters={"label": LABEL}))
                if i.role in ("base", "image")]

    def containers(self) -> List[ContainerInfo]:
        """Every container with Ditto's label, running or not."""
        return [_container_info(c) for c in self.client().containers.list(all=True, filters={"label": LABEL})]

    def container(self, container_id: str) -> Optional[ContainerInfo]:
        if not _RE_CONTAINER_ID.fullmatch(str(container_id or "")):
            return None
        try:
            return _container_info(self.client().containers.get(container_id))
        except Exception:  # noqa: BLE001 - gone (auto-removed) or the daemon did not answer
            return None

    def run_check(self, image: str, command: Sequence[str], *, mounts: Sequence[Mount], env: Mapping[str, str],
                  options: Mapping[str, Any], timeout: float) -> Tuple[int, str]:
        """Runs a short check in a new container of `image` (created by its ID: nothing is pulled) and returns its exit
        status and its output; the container is removed right after (-1: it did not end in time)."""
        from orchestrator.utils.docker_engine import create_container   # rootless Podman: keep-id
        volumes = {str(m.source): {"bind": m.target, "mode": "ro" if m.read_only else "rw"} for m in mounts}
        container = create_container(self.client(), image=image, command=list(command), volumes=volumes,
                                     environment=dict(env), **dict(options))
        try:
            container.start()
            try:
                result = container.wait(timeout=timeout)
                status = int((result or {}).get("StatusCode", -1))
            except Exception:  # noqa: BLE001 - did not end in time
                _quiet(container.kill)
                status = -1
            output = container.logs(stdout=True, stderr=True) or b""
            return status, output[:1024 * 1024].decode("utf-8", "replace")
        finally:
            _quiet(lambda: container.remove(force=True))

    def start_container(self, image: str, command: Sequence[str], *, volumes: Mapping[str, Mapping[str, str]],
                        env: Mapping[str, str], options: Mapping[str, Any]) -> str:
        from orchestrator.utils.docker_engine import create_container   # rootless Podman: keep-id
        container = create_container(self.client(), image=image, command=list(command), volumes=dict(volumes),
                                     environment=dict(env), **dict(options))
        container.start()
        return str(container.id)

    def follow_logs(self, container_id: str, write: Callable[[bytes], None]) -> threading.Thread:
        """Hands the container's output to `write` until the container ends (a daemon thread)."""
        def follow() -> None:
            try:
                for chunk in self.client().containers.get(container_id).logs(stream=True, follow=True):
                    write(chunk)
            except Exception:  # noqa: BLE001 - the container ended (and was removed)
                pass
        thread = threading.Thread(target=follow, name="ditto-container-log", daemon=True)
        thread.start()
        return thread

    def stop_container(self, container_id: str, timeout: int = 15) -> bool:
        """Stops one of Ditto's containers (never one without the label); Docker removes a server container itself."""
        try:
            container = self.client().containers.get(container_id)
        except Exception:  # noqa: BLE001 - gone
            return False
        if LABEL not in (getattr(container, "labels", None) or {}):
            return False
        try:
            container.stop(timeout=timeout)
        except Exception:  # noqa: BLE001 - it ended meanwhile
            return False
        return True

    def remove_image(self, image_id: str) -> None:
        """Removes exactly this image by its ID (never its untagged parents); only remove_leftovers calls it, after
        the person confirmed the list."""
        self.client().images.remove(image=image_id, noprune=True)

    def remove_container(self, container_id: str) -> None:
        self.client().containers.get(container_id).remove(force=False)


def _quiet(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


# --- GPU passthrough --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GpuPassthrough:
    kind: str                                   # none | nvidia | dri
    options: Mapping[str, Any]                  # docker-py create() keywords (device_requests, devices, group_add)
    environment: Mapping[str, str]
    note: str

    @property
    def used(self) -> bool:
        return self.kind != "none"


def gpu_passthrough(docker: Any, hardware: Any, backend: str, dri: Optional[Path] = None) -> GpuPassthrough:
    """How the GPU reaches the container for a `backend` build: NVIDIA GPUs through the NVIDIA Container Toolkit (Docker
    lists its "nvidia" runtime), AMD and Intel GPUs as /dev/dri with the groups of its render nodes. A CPU build needs
    none; where a GPU cannot be passed through, the note says why (the container then computes on the CPU)."""
    dri = Path(dri) if dri is not None else DRI
    if backend == "cpu":
        return GpuPassthrough("none", {}, {}, "CPU build: the container needs no GPU")
    vendors = {getattr(g, "vendor", "") for g in getattr(hardware, "gpus", []) or []}
    notes = []
    engine = docker.engine() if "nvidia" in vendors and callable(getattr(docker, "engine", None)) else ""
    if "nvidia" in vendors and engine == "podman":
        # Podman's Docker-compatible interface drops Docker's GPU request without a word (DECISIONS round 26 Q1).
        notes.append("the NVIDIA GPU is not passed through: under Podman, Docker's GPU request does not reach the "
                     "container (with Docker CE it does)")
    elif "nvidia" in vendors:
        try:
            runtimes = docker.info().get("Runtimes") or {}
        except Exception:  # noqa: BLE001
            runtimes = {}
        if "nvidia" in runtimes:
            return GpuPassthrough("nvidia", {"device_requests": [docker.gpu_request()]},
                                  {"NVIDIA_DRIVER_CAPABILITIES": "compute,utility,graphics"},
                                  "the NVIDIA GPU is passed through by the NVIDIA Container Toolkit")
        notes.append("the NVIDIA GPU is not passed through: Docker lists no 'nvidia' runtime (install the NVIDIA "
                     "Container Toolkit and restart Docker)")
    try:
        nodes = sorted(dri.glob("renderD*")) if dri.is_dir() else []
    except OSError:
        nodes = []
    if nodes and vendors & {"amd", "intel"}:
        groups = sorted({str(node.stat().st_gid) for node in nodes})
        return GpuPassthrough("dri", {"devices": [f"{dri}:{dri}:rwm"], "group_add": groups}, {},
                              f"the GPU is passed through as {dri} (render nodes: {len(nodes)})")
    if vendors & {"amd", "intel"}:
        notes.append(f"the GPU is not passed through: {dri} has no render node on this computer")
    if not notes:
        notes.append("no GPU the container could use was found")
    return GpuPassthrough("none", {}, {}, "; ".join(notes) + "; the container computes on the CPU")


def check_options(gpu: GpuPassthrough, working_dir: str) -> Dict[str, Any]:
    """create() keywords of a check container: the sandbox of every framework container (no network, no capabilities,
    no privilege escalation, read-only root, host user), plus the GPU when it is passed through."""
    options = container_sandbox(sandbox_user(), working_dir=working_dir, pids_limit=SERVER_PIDS)
    options.update(dict(gpu.options))
    options["labels"] = {LABEL: "check"}
    return options


# --- images -----------------------------------------------------------------------------------------------------------

def base_tag(base: Any, backend: str, dockerfile: Path = RUNTIME_DOCKERFILE) -> str:
    """llm-framework/ditto-runtime-base:<base>-<backend>-<hash of the Dockerfile and the pinned base>; never latest."""
    h = hashlib.sha256(dockerfile.read_bytes())
    for value in (base.image, backend):
        h.update(b"\0" + str(value).encode("utf-8"))
    return f"{BASE_REPO}:{base.id}-{backend}-{h.hexdigest()[:12]}"


def image_tag(build_id: str, base: Any) -> str:
    """llm-framework/ditto-runtime:<build ID>-<base>: the build and the base say what is in the image."""
    return f"{IMAGE_REPO}:{build_id[:16]}-{base.id}"


def _check_base(base: Any, backend: str) -> None:
    if not is_official_image(str(getattr(base, "image", ""))):
        raise ContainerError(f"the base {getattr(base, 'id', '?')} is not a Docker Official Image pinned by digest; "
                             "Ditto's image is built only on one")
    if backend not in BACKENDS:
        raise ContainerError(f"{backend} builds do not run in Ditto's container yet (CPU and Vulkan do)")


def offline_base_refusal(base: Any, tag: str) -> str:
    """The plain stop of offline mode when the runtime-base stage is not on this computer: building it pulls the base
    image and installs the runtime libraries from the distribution's package mirrors, which offline mode does not
    allow (the offline image rule of the target images, Core/builder._offline_image)."""
    return (f"Offline mode is on, and the runtime base of Ditto's image ({tag}) is not on this computer. Building it "
            f"pulls {getattr(base, 'image', 'its base image')} and installs its runtime libraries from the "
            "distribution's package mirrors, which offline mode does not allow. Set up Docker mode once with offline "
            "mode off; later images on the same base are made offline from that runtime base.")


def build_runtime_base(docker: DittoDocker, base: Any, backend: str, log: Path,
                       dockerfile: Path = RUNTIME_DOCKERFILE, *, offline: bool = False) -> ImageInfo:
    """The runtime-base stage for `base` and `backend` (reused when it is there): the base and the runtime libraries.
    In offline mode only a runtime base that is there is used; without one ContainerError stops before anything is
    built (offline_base_refusal)."""
    _check_base(base, backend)
    tag = base_tag(base, backend, dockerfile)
    existing = docker.image(tag)
    if existing is not None and existing.role == "base" and not existing.pulled:
        return existing
    if offline:
        raise ContainerError(offline_base_refusal(base, tag))
    with tempfile.TemporaryDirectory(prefix="ditto-runtime-ctx-") as ctx:
        shutil.copyfile(dockerfile, Path(ctx) / "Dockerfile")
        (Path(ctx) / "bin").mkdir()
        docker.build_stage(Path(ctx), tag, {"BASE": base.image, "LIBC": base.libc, "BACKEND": backend},
                           {LABEL: "base", LABEL_BASE: base.id, LABEL_BACKEND: backend}, "runtime-base", log)
    built = docker.image(tag)
    if built is None or built.role != "base":
        raise ContainerError(f"the image {tag} was not found after its build (log: {log})")
    return built


def _readable(folder: Path) -> None:
    """Gives the folder and every file in it (never through a link) mode 0755: readable and runnable by everyone."""
    for root, dirs, files in os.walk(folder, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(root) / name
            if not path.is_symlink():
                path.chmod(0o755)  # copies in a build context: everyone may run them, nobody else may change them
    folder.chmod(0o755)


def build_runtime_image(docker: DittoDocker, build: Any, base: Any, made_by: str, log: Path,
                        dockerfile: Path = RUNTIME_DOCKERFILE, *, offline: bool = False) -> ImageInfo:
    """Ditto's image with the programs of the stored `build` on `base`. The programs are copied from the build store
    into the build context and checked against the build's manifest before the image is built. In offline mode it is
    made only on a runtime base that is there (this stage copies files only, nothing is fetched); without one the
    early stop of build_runtime_base."""
    if made_by not in MADE_BY:
        raise ContainerError("made_by must be repackaged or rebuilt")
    backend = build.backend
    build_runtime_base(docker, base, backend, log, dockerfile, offline=offline)
    tag = image_tag(build.build_id, base)
    files: Mapping[str, str] = build.manifest.get("files") or {}
    with tempfile.TemporaryDirectory(prefix="ditto-runtime-ctx-") as ctx:
        context = Path(ctx)
        shutil.copyfile(dockerfile, context / "Dockerfile")
        shutil.copytree(build.bin_dir, context / "bin", symlinks=True)
        for name, digest in files.items():
            copied = context / "bin" / name
            if copied.is_symlink() or not copied.is_file() or sha256_file(copied) != digest:
                raise ContainerError(f"{name} of build {build.build_id[:16]} does not match its manifest; no image "
                                     "was built")
        # COPY keeps each file's mode and gives it to root; the server runs as the host user, so a build made under a
        # strict umask (0700) would not run in the container. The copies get 0755, the build store stays as it is.
        _readable(context / "bin")
        labels = {LABEL: "image", LABEL_BUILD: build.build_id, LABEL_COMMIT: build.commit, LABEL_BASE: base.id,
                  LABEL_BACKEND: backend, LABEL_MADE_BY: made_by}
        docker.build_stage(context, tag, {"BASE": base.image, "LIBC": base.libc, "BACKEND": backend}, labels,
                           "ditto", log)
    image = docker.image(tag)
    if image is None or image.role != "image" or image.labels.get(LABEL_BUILD) != build.build_id:
        raise ContainerError(f"the image {tag} was not found after its build (log: {log})")
    return image


# --- checks -----------------------------------------------------------------------------------------------------------

def _tail(text: str, limit: int = 300) -> str:
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in (text or "")).split())
    return clean[-limit:] if clean else "no output"


def _check_dir(user: Tuple[int, int]) -> tempfile.TemporaryDirectory:
    """A host folder mounted into check containers (a key file, a model path that does not exist)."""
    folder = tempfile.TemporaryDirectory(prefix="ditto-check-")
    hand_over(Path(folder.name), user)
    return folder


def verify_image(docker: DittoDocker, image: ImageInfo, pin: Any, gpu: GpuPassthrough) -> VerifyReport:
    """Checks Ditto's image in containers of itself, where the server will run: its labels name the pinned commit,
    llama-server and llama-bench report the pinned build number and commit, llama-server refuses --tools, and a GPU
    build sees its device when the GPU is passed through. Nothing is loaded: no model reaches a check container."""
    report = VerifyReport()
    commit = str(image.labels.get(LABEL_COMMIT, ""))
    if image.role != "image" or pin is None or commit != pin.commit:
        report.checks.append(Check("image", False, f"the image is not Ditto's image of the pinned commit (labels: "
                                                   f"{image.role or 'none'}, commit {commit[:12] or '?'})"))
        return report
    if image.pulled:
        report.checks.append(Check("image", False, "the image came from a registry (it was pulled); Ditto's image is "
                                                   "only ever built on this computer"))
        return report
    report.checks.append(Check("image", True, f"{image.name}, commit {commit[:12]}"))
    report.ran_binaries = True
    user = sandbox_user()
    with _check_dir(user) as tmp:
        key = Path(tmp) / "check.key"
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secrets.token_hex(32))
        hand_over(Path(tmp), user)
        mounts = [Mount(Path(tmp), CHECK_MOUNT, True)]
        options = check_options(gpu, working_dir=CONTAINER_TMP)
        env = {"HOME": CONTAINER_TMP, "LANG": "C", "LC_ALL": "C", **dict(gpu.environment)}

        def run(argv: List[str], timeout: float = CHECK_TIMEOUT, policy: bool = True) -> Tuple[int, str]:
            if policy:
                check_command(argv)  # the shared forbidden-flag list (D22); the --tools check is the one exception
            return docker.run_check(image.image_id, argv, mounts=mounts, env=env, options=options, timeout=timeout)

        found, problems = {}, []
        for program in (BENCH_IN_IMAGE, SERVER_IN_IMAGE):
            rc, out = run([program, "--version"])
            parsed = parse_version(out)
            name = program.rsplit("/", 1)[-1]
            if rc != 0 or parsed is None:
                problems.append(f"{name} --version failed in the container (exit {rc}): {_tail(out)}")
                continue
            build, short = parsed
            found[name] = f"b{build} ({short})"
            if build != pin.build_number or not pin.commit.startswith(short[:8]) or len(short) < 8:
                problems.append(f"{name} reports build {build}, commit {short}; the pin is build {pin.build_number}, "
                                f"commit {pin.short_commit}")
        report.checks.append(Check("version", not problems, "; ".join(problems) or
                                   ", ".join(f"{k} {v}" for k, v in found.items()), {"found": found}))

        rc, out = run([BENCH_IN_IMAGE, "--list-devices"], DEVICES_TIMEOUT)
        devices = parse_devices(out)
        backend = str(image.labels.get(LABEL_BACKEND, "cpu"))
        if rc != 0 or devices is None:
            report.checks.append(Check("devices", False, f"llama-bench --list-devices failed in the container "
                                                         f"(exit {rc}): {_tail(out)}"))
        else:
            names = [d.name for d in devices]
            data = {"devices": [{"name": d.name, "description": d.description[:80], "total_mib": d.total_mib}
                                for d in devices]}
            if backend == "vulkan" and gpu.used and "Vulkan0" not in names:
                report.checks.append(Check("devices", False, f"the GPU is passed through, but the container sees "
                                                             f"{', '.join(names) or 'only the CPU'}", data))
            else:
                note = "" if backend == "cpu" or gpu.used else f" ({gpu.note})"
                report.checks.append(Check("devices", True, (", ".join(names) or "CPU only") + note, data))

        missing = f"{CHECK_MOUNT}/no-model-{secrets.token_hex(4)}.gguf"
        argv = tools_check_args(SERVER_IN_IMAGE, missing, CONTAINER_PORT, f"{CHECK_MOUNT}/check.key")
        rc, out = run(argv, policy=False)  # deliberately not through check_command
        if rc != 0 and SERVER_TOOLS_REFUSAL in out:
            report.checks.append(Check("server-tools", True, "llama-server refuses --tools (built without subprocess "
                                                             "support)"))
        else:
            report.checks.append(Check("server-tools", False, f"llama-server did not refuse --tools in the container "
                                                              f"(exit {rc}): {_tail(out)}"))
    return report


class ContainerRunner:
    """A verify.BinaryRunner that runs the programs of a new build in a container of Ditto's runtime-base image, where
    they will run (so a machine whose own C library is older, or that lacks the GPU libraries, still verifies them).
    The build's bin folder (read-only), the verification's working folder and the folder of any other file the command
    names (read-only) are mounted at the same paths, so the verifier's command lines stay exactly as they are. The
    container has no network and the sandbox of every framework container."""

    def __init__(self, docker: DittoDocker, image: str, gpu: GpuPassthrough):
        self.docker = docker
        self.image = image
        self.gpu = gpu

    def __call__(self, argv: Sequence[str], timeout: float, cwd: str, env: Mapping[str, str]) -> Tuple[int, str, str]:
        program = Path(argv[0])
        folders: Dict[Path, bool] = {program.parent: True, Path(cwd): False}
        for arg in list(argv)[1:]:
            path = Path(str(arg))
            if path.is_absolute() and path.is_file() and not any(path.is_relative_to(f) for f in folders):
                folders[path.parent] = True
        user = sandbox_user()
        hand_over(Path(cwd), user)
        mounts = [Mount(folder, str(folder), read_only) for folder, read_only in folders.items()]
        try:
            rc, out = self.docker.run_check(self.image, list(argv), mounts=mounts,
                                            env={**dict(env), **dict(self.gpu.environment)},
                                            options=check_options(self.gpu, working_dir=str(cwd)), timeout=timeout)
        except Exception as e:  # noqa: BLE001 - the daemon refused the container: the check fails with the reason
            return -1, "", f"the check container could not run ({type(e).__name__})"
        return rc, out, ""


# --- the server container ---------------------------------------------------------------------------------------------

def container_name(uid: Optional[int] = None) -> str:
    """lcf-ditto-<uid>: one server container per user of this computer."""
    if uid is None:
        uid = os.getuid() if hasattr(os, "getuid") else 0
    return f"lcf-ditto-{int(uid)}"


def server_options(*, models_dir: Path, key_file: Path, port: int, gpu: GpuPassthrough,
                   name: str) -> Tuple[Dict[str, Any], Dict[str, Dict[str, str]]]:
    """(create() keywords, volumes) of the server container: the sandbox of every framework container except the
    network (the default bridge, so the port can be published), the port published on 127.0.0.1 only, the models
    folder and the key file read-only, the GPU when it is passed through, Ditto's label, auto-remove and an init
    process. Checked before they are returned (check_server_options)."""
    user = sandbox_user()
    options = container_sandbox(user, working_dir="/opt/ditto/bin", pids_limit=SERVER_PIDS)
    options.pop("network_mode", None)
    options.update(dict(gpu.options))
    options.update({"ports": {f"{CONTAINER_PORT}/tcp": (LOOPBACK, int(port))}, "name": name,
                    "labels": {LABEL: "server", LABEL_USER: str(user[0])}, "auto_remove": True, "init": True})
    volumes = {str(models_dir): {"bind": MODELS_MOUNT, "mode": "ro"}, str(key_file): {"bind": KEY_MOUNT, "mode": "ro"}}
    check_server_options(options, volumes)
    return options, volumes


def check_server_options(options: Mapping[str, Any], volumes: Mapping[str, Mapping[str, str]]) -> None:
    """Raises PolicyError unless the server container keeps the launch policy: only the server port, published on
    127.0.0.1 (check_publish); never the host's network, privileged, added capabilities or a writable root; every
    mount read-only."""
    check_publish(options.get("ports") or {})
    if options.get("network_mode") not in (None, "bridge"):
        raise PolicyError("Ditto's container uses the default bridge network (never the host's network)")
    if options.get("privileged") or options.get("cap_add"):
        raise PolicyError("Ditto's container never gets privileges or capabilities")
    if options.get("cap_drop") != ["ALL"] or options.get("read_only") is not True:
        raise PolicyError("Ditto's container drops every capability and has a read-only root file system")
    if "no-new-privileges:true" not in (options.get("security_opt") or []):
        raise PolicyError("Ditto's container runs without privilege escalation")
    if str(options.get("user", "0")).split(":")[0] in ("0", "root"):
        raise PolicyError("Ditto's container never runs as root")
    if any(v.get("mode") != "ro" for v in volumes.values()):
        raise PolicyError("Ditto's container mounts the models and the key file read-only")


def server_environment(env: Mapping[str, str], gpu: GpuPassthrough) -> Dict[str, str]:
    """The server's environment inside the container: what server_policy.server_env lets through from the caller's
    (GPU selection; a host folder like HOME or LD_LIBRARY_PATH is left out), a home in the container's /tmp, the C
    locale and the NVIDIA Toolkit's capabilities."""
    out = {k: v for k, v in env.items() if k not in ("HOME", "PATH", "USERPROFILE", "LD_LIBRARY_PATH", "LANG",
                                                       "LC_ALL")}
    out.update({"HOME": CONTAINER_TMP, "LANG": "C", "LC_ALL": "C"})
    out.update(dict(gpu.environment))
    return out


def start_server(docker: DittoDocker, image_id: str, command: Sequence[str], *, options: Mapping[str, Any],
                 volumes: Mapping[str, Mapping[str, str]], env: Mapping[str, str]) -> str:
    """Starts the server container by the image's ID (nothing is pulled) and returns the container's ID. The command
    line and the options are checked against the launch policy right before the start (decision D22)."""
    if not _RE_IMAGE_ID.fullmatch(str(image_id or "")):
        raise ContainerError("Ditto's container is started only by the image's ID")
    check_container_server_command(command)
    check_server_options(options, volumes)
    return docker.start_container(image_id, command, volumes=volumes, env=env, options=options)


def container_alive(state: Mapping[str, Any], docker: Optional[DittoDocker] = None) -> bool:
    """Whether the server container recorded in server.json still runs with Ditto's label and its port published on
    127.0.0.1 at the recorded port: only then is its key handed to the provider layer."""
    try:
        port = str(int(state["port"]))
        engine = docker or DittoDocker(timeout=5)
        info = engine.container(str(state["container_id"]))
    except (KeyError, TypeError, ValueError):
        return False
    except Exception:  # noqa: BLE001 - no Docker (module or daemon): not running
        return False
    if info is None or info.status != "running" or info.role != "server":
        return False
    bindings = info.ports.get(f"{CONTAINER_PORT}/tcp") or []
    return any(isinstance(b, Mapping) and b.get("HostIp") == LOOPBACK and str(b.get("HostPort")) == port
               for b in bindings)


__all__ = ["BACKENDS", "ContainerError", "ContainerInfo", "ContainerRunner", "DittoDocker", "GpuPassthrough",
           "ImageInfo", "LABEL", "build_runtime_base", "build_runtime_image", "check_server_options",
           "offline_base_refusal",
           "container_alive", "container_name", "gpu_passthrough", "is_official_image", "server_options",
           "start_server", "verify_image"]
