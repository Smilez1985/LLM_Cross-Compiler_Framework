#!/usr/bin/env python3
"""
Runs a llama.cpp build plan (orchestrator/runtime/build/recipe.py). The executor decides nothing: what to build and
with which options is the plan's; the executor only checks that the plan fits the machine it runs on, runs CMake and
collects the binaries. Verification (verify.py) and storing (store.py) follow.

Two placements on Linux:

- native: CMake with the user's compiler, in a cleared environment (no proxy variables, no LLAMA_*/GGML_*, CC/CXX
  set to the detected compiler whose version is in the build ID). Native builds have no network sandbox; the guard is
  the plan itself (every download-capable option OFF, FETCHCONTENT_FULLY_DISCONNECTED=ON) plus the configure-log check
  below, which fails the build when upstream's CMake printed that it fetched or downloaded anything.
- container: the framework's builder image (builder.Dockerfile next to this file, built locally from the plan's base,
  which the catalog pins by digest; the image build itself has network, as every image build of the framework). The
  configure and build steps then run in containers of that image with --network none and the sandbox of the
  conversion stages (orchestrator/utils/sandbox.py: no capabilities, no privilege escalation, read-only root, host
  user, process and memory limits). The verified source and the toolchain file are mounted read-only; only the
  build, home and tmp folders are writable, and what the host reads back afterwards never follows a link out of
  them (cross builds are never run on the host, so this sandbox is all that stands between their build and it).

No command carries a licence-accept flag, and nothing is installed on the host. Windows, macOS, CUDA and ROCm builds
follow in later work packages; the executor refuses them with a message instead of guessing.
"""

import hashlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from orchestrator.runtime.build.recipe import BuildPlan, PlanError, check_download_options
from orchestrator.runtime.build.toolchain import DetectedToolchain, host_resources, parallel_jobs
from orchestrator.utils.sandbox import container_sandbox, hand_over, sandbox_user

BUILDER_DOCKERFILE = Path(__file__).resolve().with_name("builder.Dockerfile")
BUILDER_LABEL = "org.llm-framework.builder"
CONFIGURE_TIMEOUT = 1800
BUILD_TIMEOUT = 6 * 3600

# Lines upstream's CMake prints when it fetches or downloads (design 3.2). Any of them in a configure log fails the
# build, even when the download failed (it was attempted).
DOWNLOAD_MARKERS = re.compile(
    r"\bFetching\b|\bDownloading\b|\bdownloading from\b|\bHF download\b|\bHF fetch\b|\brunning npm\b|"
    r"\bPopulating\b|\bFetchContent\b|\bfile\(DOWNLOAD|\bgit clone\b|\bcloning into\b",
    re.IGNORECASE)
_SAFE_PATH_DIRS = ("/usr/local/bin", "/usr/bin", "/bin")
_RE_BUILD_ID = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RE_SHARED = re.compile(r"\.so(\.\d+)*$", re.ASCII)
_RE_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]{1,128})?@sha256:[0-9a-f]{64}$", re.ASCII)

# (argv, cwd, env, log file, timeout) -> exit code; -1 when it could not start or was stopped at the time limit.
ProcessRunner = Callable[[Sequence[str], Path, Mapping[str, str], Path, float], int]


class BuildError(RuntimeError):
    """A build that did not complete; the message says why, `log` names the log with the details."""

    def __init__(self, message: str, log: Optional[Path] = None):
        super().__init__(message)
        self.log = log


@dataclass
class BuildResult:
    build_id: str
    work: Path
    bin_dir: Path
    files: Dict[str, str]          # file name in bin/ -> sha256
    links: Dict[str, str]          # link name in bin/ -> target name (shared library versions)
    duration_s: float
    jobs: int
    placement: str
    builder_image: Optional[str] = None
    logs: Dict[str, str] = field(default_factory=dict)  # name -> path


def check_configure_log(text: str) -> List[str]:
    """Lines of a configure log that say something was fetched or downloaded (design 3.2)."""
    return [line.strip()[:200] for line in (text or "").splitlines() if DOWNLOAD_MARKERS.search(line)]


def _tail(path: Path, limit: int = 1200) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - 16384))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    lines = [ln for ln in text.splitlines() if ln.strip()][-12:]
    clean = "\n".join("".join(ch if ch.isprintable() or ch == "\t" else " " for ch in ln) for ln in lines)
    return clean[-limit:]


def default_process_runner(argv: Sequence[str], cwd: Path, env: Mapping[str, str], log: Path,
                           timeout: float) -> int:
    """Runs a build step; its output (stdout and stderr) goes to `log`. At the time limit the whole process group is
    stopped."""
    with open(log, "ab") as out:
        try:
            proc = subprocess.Popen(list(argv), cwd=str(cwd), env=dict(env), stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=(os.name == "posix"))
        except OSError as e:
            out.write(f"\n[executor] could not start {argv[0]}: {e}\n".encode())
            return -1
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            out.write(f"\n[executor] stopped after {timeout:.0f} s\n".encode())
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:  # pragma: no cover - Windows
                    proc.kill()
            except OSError:
                pass
            proc.wait()
            return -1


# --- containers ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    read_only: bool


class ContainerEngine:
    """What the executor needs from a container engine. DockerEngine is the real one; tests use a fake."""

    def unavailable(self) -> Optional[str]:  # pragma: no cover - interface
        raise NotImplementedError

    def image_exists(self, tag: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def build_image(self, context: Path, tag: str, buildargs: Mapping[str, str], labels: Mapping[str, str],
                    log: Path) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def run(self, image: str, command: Sequence[str], *, mounts: Sequence[Mount], env: Mapping[str, str],
            options: Mapping[str, Any], log: Path, timeout: float) -> int:  # pragma: no cover - interface
        raise NotImplementedError


class DockerEngine(ContainerEngine):
    """docker-py, as the build engine uses it (no docker CLI or BuildKit needed)."""

    def __init__(self, client: Any = None):
        self._client = client

    def client(self) -> Any:
        if self._client is None:
            import docker
            self._client = docker.from_env(timeout=120)
        return self._client

    def unavailable(self) -> Optional[str]:
        try:
            self.client().ping()
        except Exception as e:
            return f"the Docker daemon is not reachable ({type(e).__name__})"
        return None

    def image_exists(self, tag: str) -> bool:
        try:
            self.client().images.get(tag)
            return True
        except Exception:
            return False

    def build_image(self, context: Path, tag: str, buildargs: Mapping[str, str], labels: Mapping[str, str],
                    log: Path) -> None:
        with open(log, "a", encoding="utf-8") as out:
            for chunk in self.client().api.build(path=str(context), dockerfile="Dockerfile", tag=tag,
                                                 buildargs=dict(buildargs), labels=dict(labels), decode=True,
                                                 rm=True, forcerm=True):
                if "stream" in chunk:
                    out.write(str(chunk["stream"]))
                if "error" in chunk:
                    out.write(str(chunk["error"]) + "\n")
                    raise BuildError(f"the builder image could not be built: {str(chunk['error'])[:300]}", log)

    def run(self, image: str, command: Sequence[str], *, mounts: Sequence[Mount], env: Mapping[str, str],
            options: Mapping[str, Any], log: Path, timeout: float) -> int:
        volumes = {str(m.source): {"bind": m.target, "mode": "ro" if m.read_only else "rw"} for m in mounts}
        container = self.client().containers.create(image=image, command=list(command), volumes=volumes,
                                                    environment=dict(env), **dict(options))
        timer = threading.Timer(timeout, lambda: _quiet(container.kill))
        try:
            container.start()
            timer.start()
            with open(log, "ab") as out:
                for chunk in container.logs(stream=True, follow=True):
                    out.write(chunk)
            result = container.wait(timeout=60)
            return int(result.get("StatusCode", -1))
        finally:
            timer.cancel()
            _quiet(lambda: container.remove(force=True))


def _catalog_per_job_mib(backend: str) -> int:
    """The estimated peak memory of one compile job of `backend` (configs/llama_runtime.yml, build_jobs)."""
    from orchestrator.runtime.catalog import load_catalog
    return load_catalog().build_jobs.per_job_mib(backend)


def _quiet(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception:
        pass


def builder_image_tag(plan: BuildPlan, dockerfile: Path = BUILDER_DOCKERFILE) -> str:
    """llm-framework/llama-builder:<base>-<hash of the Dockerfile, the base image and the target>; never latest."""
    h = hashlib.sha256()
    h.update(dockerfile.read_bytes())
    for value in (plan.builder.get("image"), plan.target.get("triple") or "", plan.target.get("arch"),
                  plan.builder.get("libc")):
        h.update(b"\0" + str(value).encode("utf-8"))
    return f"llm-framework/llama-builder:{plan.builder.get('base')}-{h.hexdigest()[:12]}"


# --- the executor -------------------------------------------------------------------------------------------------

class BuildExecutor:
    """Runs a BuildPlan natively or in the builder container and collects the binaries into <work>/bin."""

    def __init__(self, runner: Optional[ProcessRunner] = None, engine: Optional[ContainerEngine] = None, *,
                 configure_timeout: float = CONFIGURE_TIMEOUT, build_timeout: float = BUILD_TIMEOUT,
                 pids_limit: int = 4096, mem_limit: Optional[str] = None,
                 log: Optional[Callable[[str], None]] = None, dockerfile: Path = BUILDER_DOCKERFILE,
                 per_job_mib: Optional[Callable[[str], int]] = None):
        self.per_job_mib = per_job_mib or _catalog_per_job_mib
        self.runner = runner or default_process_runner
        self.engine = engine
        self.configure_timeout = configure_timeout
        self.build_timeout = build_timeout
        self.pids_limit = pids_limit
        self.mem_limit = mem_limit
        self.say = log or (lambda _msg: None)
        self.dockerfile = dockerfile

    # --- shared ---
    @staticmethod
    def refusal(plan: BuildPlan) -> Optional[str]:
        """Why this executor will not run the plan (a later work package, a plan that breaks the build rules), or None.
        Asked before a build is started, so the user hears it before anything is fetched or built."""
        try:
            BuildExecutor._check_plan(plan)
        except BuildError as e:
            return str(e)
        return None

    @staticmethod
    def _check_plan(plan: BuildPlan) -> None:
        if not _RE_BUILD_ID.fullmatch(plan.build_id or ""):
            raise BuildError("the plan has no build ID")
        if plan.target.get("os") != "linux":
            raise BuildError(f"{plan.target.get('os')} builds come with a later work package (Windows, macOS); this "
                             "executor builds Linux binaries")
        if len(plan.steps) != 1:
            raise BuildError("plans with more than one build step (Windows CUDA) are not run here")
        if plan.backend not in ("cpu", "vulkan"):
            raise BuildError(f"{plan.backend} builds need the toolkit provisioning of a later work package; build the "
                             "CPU variant now")
        if plan.builder.get("base") == "target-image":
            raise BuildError("conversion tools are built inside the target's toolchain image, not by this executor")
        if plan.builder.get("prebuilt"):
            raise BuildError("builds in a prebuilt NVIDIA image come with the CUDA work package")
        try:
            check_download_options(plan.options())
        except PlanError as e:
            raise BuildError(f"refusing the plan: {e}") from None

    @staticmethod
    def _prepare(work: Path) -> Dict[str, Path]:
        work = Path(work)
        dirs = {name: work / name for name in ("build", "bin", "home", "tmp", "logs")}
        for path in dirs.values():
            path.mkdir(parents=True, exist_ok=True)
        if any(dirs["bin"].iterdir()):
            raise BuildError(f"{dirs['bin']} is not empty; every build starts in a new folder")
        return dirs

    def _finish_configure(self, log: Path, plan: Optional[BuildPlan] = None,
                          build_dir: Optional[Path] = None) -> None:
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        fetched = check_configure_log(text)
        if fetched:
            raise BuildError("configure tried to fetch or download something, which a build must never do: "
                             + " | ".join(fetched[:3]), log)
        if plan is not None and build_dir is not None and plan.steps[0].option("GGML_OPENMP") == "ON":
            # ggml only warns "OpenMP not found" and builds without it (ggml/src/CMakeLists.txt:339-348): the
            # binaries would then not be the ones the build ID names.
            try:
                cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
            except OSError:
                cache = ""
            if re.search(r"^GGML_OPENMP_ENABLED:INTERNAL=OFF\s*$", cache, re.M):
                raise BuildError("the plan builds with OpenMP, but CMake found no OpenMP runtime (Clang needs libomp, "
                                 "GCC libgomp); install it, or detect the toolchain again so the plan switches "
                                 "OpenMP off", log)

    def build(self, plan: BuildPlan, *, source: Path, work: Path, jobs: Optional[int] = None,
              toolchain: Optional[DetectedToolchain] = None, ccache_dir: Optional[Path] = None) -> BuildResult:
        """Builds `plan` from the verified source tree `source` in the new folder `work`. Without `jobs` (the user's
        choice), -j follows the memory (design 3.5): min(CPUs, available RAM / the catalog's estimate per job)."""
        self._check_plan(plan)
        source = Path(source).resolve()
        if not (source / "CMakeLists.txt").is_file():
            raise BuildError(f"{source} is not a llama.cpp source tree")
        if jobs is None:
            cpus, ram = (toolchain.cpus, toolchain.ram_available_mib) if toolchain is not None else host_resources()
            jobs = parallel_jobs(cpus, ram, self.per_job_mib(plan.backend))
        jobs = max(1, int(jobs))
        dirs = self._prepare(work)
        started = time.monotonic()
        kind = plan.builder.get("kind")
        if kind == "native":
            if plan.placement not in ("native", "on-device"):
                raise BuildError(f"a {plan.placement} plan does not run natively here")
            self._native(plan, source, dirs, jobs, toolchain, ccache_dir)
            image = None
        elif kind == "container":
            image = self._container(plan, source, dirs, jobs)
        else:
            raise BuildError(f"unknown builder {kind!r}")
        files, links = self._collect(plan, dirs["build"] / "bin", dirs["bin"], dirs["logs"] / "build.log")
        return BuildResult(build_id=plan.build_id, work=Path(work), bin_dir=dirs["bin"], files=files, links=links,
                           duration_s=round(time.monotonic() - started, 1), jobs=jobs, placement=plan.placement,
                           builder_image=image,
                           logs={p.stem: str(p) for p in sorted(dirs["logs"].glob("*.log"))})

    # --- native ---
    def _native(self, plan: BuildPlan, source: Path, dirs: Dict[str, Path], jobs: int,
                toolchain: Optional[DetectedToolchain], ccache_dir: Optional[Path]) -> None:
        if toolchain is None:
            raise BuildError("a native build needs the detected toolchain")
        if not toolchain.native_ready:
            raise BuildError("no native build on this machine: " + "; ".join(toolchain.problems))
        info = toolchain.info
        if (info.host_os, info.host_arch) != (plan.target.get("os"), plan.target.get("arch")):
            raise BuildError(f"the plan is for {plan.target.get('os')}/{plan.target.get('arch')}, this machine is "
                             f"{info.host_os}/{info.host_arch}")
        planned = plan.toolchain.get("compiler")
        if planned != info.compiler:
            raise BuildError(f"the plan was made for {planned or 'an unknown compiler'}, but this machine has "
                             f"{info.compiler}: plan the build again (the build ID names the compiler)")
        native = plan.toolchain.get("native") or {}
        if native.get("expansion") and native.get("expansion") != info.native_expansion:
            raise BuildError("-march/-mcpu=native expands differently than when the build was planned: plan it again")
        step = plan.steps[0]
        if step.toolchain_file:
            raise BuildError("a native build has no toolchain file")
        tool_dirs: List[str] = []
        for name in ("cmake", "ninja", "cc", "cxx", "ccache"):
            if name in toolchain.tools:
                folder = str(Path(toolchain.tools[name]).parent)
                if folder not in tool_dirs:
                    tool_dirs.append(folder)
        env = {"PATH": os.pathsep.join(tool_dirs + [d for d in _SAFE_PATH_DIRS if d not in tool_dirs]),
               "HOME": str(dirs["home"]), "TMPDIR": str(dirs["tmp"]), "LANG": "C", "LC_ALL": "C",
               "CC": toolchain.tool("cc"), "CXX": toolchain.tool("cxx")}
        if step.option("GGML_CCACHE") == "ON":
            if not ccache_dir:
                raise BuildError("the plan uses ccache, but no ccache folder was given")
            Path(ccache_dir).mkdir(parents=True, exist_ok=True)
            env["CCACHE_DIR"] = str(ccache_dir)
        cmake = toolchain.tool("cmake")
        configure = [cmake, "-S", str(source), "-B", str(dirs["build"]), "-G", step.generator, *step.cmake_args()]
        self._step("configure", configure, dirs, env, self.configure_timeout)
        self._finish_configure(dirs["logs"] / "configure.log", plan, dirs["build"])
        build = [cmake, "--build", str(dirs["build"]), "--parallel", str(jobs), "--target", *step.targets]
        self._step("build", build, dirs, env, self.build_timeout)

    def _step(self, name: str, argv: Sequence[str], dirs: Dict[str, Path], env: Mapping[str, str],
              timeout: float) -> None:
        log = dirs["logs"] / f"{name}.log"
        self.say(f"{name}: {' '.join(Path(argv[0]).name if i == 0 else a for i, a in enumerate(argv[:6]))} ...")
        rc = self.runner(list(argv), dirs["build"], env, log, timeout)
        if rc != 0:
            raise BuildError(f"{name} failed (exit {rc}):\n{_tail(log)}", log)

    # --- container ---
    def _container(self, plan: BuildPlan, source: Path, dirs: Dict[str, Path], jobs: int) -> str:
        engine = self.engine
        if engine is None:
            raise BuildError("a container build needs Docker")
        reason = engine.unavailable()
        if reason:
            raise BuildError(f"no container build: {reason}. Start Docker, or build natively")
        base_image = str(plan.builder.get("image") or "")
        if not _RE_IMAGE.fullmatch(base_image):
            raise BuildError("the builder base must be pinned by digest")
        tag = builder_image_tag(plan, self.dockerfile)
        if not engine.image_exists(tag):
            self.say(f"building the builder image {tag} (from {base_image.split('@')[0]}, pinned by digest) ...")
            with tempfile.TemporaryDirectory(prefix="llm-builder-ctx-") as ctx:
                shutil.copyfile(self.dockerfile, Path(ctx) / "Dockerfile")
                args = {"BASE": base_image, "LIBC": str(plan.builder.get("libc") or "glibc"),
                        "CROSS_TRIPLE": str(plan.target.get("triple") or "") if plan.steps[0].toolchain_file else ""}
                engine.build_image(Path(ctx), tag, args, {BUILDER_LABEL: "llama.cpp"}, dirs["logs"] / "image.log")
        step = plan.steps[0]
        config = dirs["build"].parent / "config"
        config.mkdir(exist_ok=True)
        if step.toolchain_file:
            (config / "toolchain.cmake").write_text(step.toolchain_file, encoding="utf-8")
        user = sandbox_user()
        hand_over(dirs["build"].parent, user)
        options = container_sandbox(user, working_dir="/work", pids_limit=self.pids_limit, mem_limit=self.mem_limit)
        if options.get("network_mode") != "none":  # pragma: no cover - guarded by the sandbox module's tests
            raise BuildError("the build container must have no network")
        # Only the folders the build writes are writable (each one a mount point the container cannot replace with a
        # link); bin/ and logs/ of the work folder, which the host fills, are not in the container at all.
        mounts = [Mount(source, "/src", True), Mount(config, "/work/config", True),
                  Mount(dirs["build"], "/work/build", False), Mount(dirs["home"], "/work/home", False),
                  Mount(dirs["tmp"], "/work/tmp", False)]
        env = {"HOME": "/work/home", "TMPDIR": "/work/tmp", "LANG": "C", "LC_ALL": "C"}
        self._check_container_compiler(plan, engine, tag, mounts, env, options, dirs)
        configure = ["cmake", "-S", "/src", "-B", "/work/build", "-G", step.generator, *step.cmake_args()]
        if step.toolchain_file:
            configure.append("-DCMAKE_TOOLCHAIN_FILE=/work/config/toolchain.cmake")
        self._container_step("configure", engine, tag, configure, mounts, env, options, dirs, self.configure_timeout)
        self._finish_configure(dirs["logs"] / "configure.log", plan, dirs["build"])
        build = ["cmake", "--build", "/work/build", "--parallel", str(jobs), "--target", *step.targets]
        self._container_step("build", engine, tag, build, mounts, env, options, dirs, self.build_timeout)
        return tag

    def _container_step(self, name: str, engine: ContainerEngine, image: str, command: Sequence[str],
                        mounts: Sequence[Mount], env: Mapping[str, str], options: Mapping[str, Any],
                        dirs: Dict[str, Path], timeout: float) -> None:
        log = dirs["logs"] / f"{name}.log"
        self.say(f"{name} in the builder container (no network) ...")
        rc = engine.run(image, command, mounts=mounts, env=env, options=options, log=log, timeout=timeout)
        if rc != 0:
            raise BuildError(f"{name} failed in the builder container (exit {rc}):\n{_tail(log)}", log)

    def _check_container_compiler(self, plan: BuildPlan, engine: ContainerEngine, image: str,
                                  mounts: Sequence[Mount], env: Mapping[str, str], options: Mapping[str, Any],
                                  dirs: Dict[str, Path]) -> None:
        """The builder image's GCC must be the version the catalog names for the base: it is part of the build ID."""
        want = plan.builder.get("gcc")
        compiler = "gcc"
        if plan.steps[0].toolchain_file:
            if plan.steps[0].compiler != "gcc":
                return  # clang with a sysroot: its version is not part of the catalog yet
            compiler = f"{plan.target.get('triple')}-gcc"
        if not want:
            return
        log = dirs["logs"] / "compiler.log"
        rc = engine.run(image, [compiler, "-dumpfullversion"], mounts=mounts, env=env, options=options, log=log,
                        timeout=120)
        try:
            found = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1].strip()
        except (OSError, IndexError):
            found = ""
        if rc != 0 or not re.fullmatch(r"\d+(\.\d+){0,3}", found):
            raise BuildError(f"the builder image has no working {compiler} (exit {rc})", log)
        if found.split(".")[:len(str(want).split("."))] != str(want).split("."):
            raise BuildError(f"the builder image has GCC {found}, but the catalog names GCC {want} for "
                             f"{plan.builder.get('base')}; the build ID would not describe the build", log)

    # --- outputs ---
    @staticmethod
    def _collect(plan: BuildPlan, built: Path, out: Path, log: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Copies the planned programs and the shared libraries next to them into <work>/bin."""
        files: Dict[str, str] = {}
        links: Dict[str, str] = {}
        # The build ran upstream's code (in a container: with write access to its build folder): a bin folder that is
        # a link would hand the host files from outside the build, for example programs from a folder on PATH.
        root = built.parent
        if root.is_symlink() or built.is_symlink() or (built.exists() and built.resolve().parent != root.resolve()):
            raise BuildError("the build's bin folder is a link that leads outside the build folder; nothing was "
                             "collected", log)
        if out.is_symlink() or not out.is_dir():
            raise BuildError(f"{out} is not a plain folder", log)
        if not built.is_dir():
            raise BuildError("the build produced no bin folder", log)
        for name in plan.targets:
            src = built / name
            if src.is_symlink() or not src.is_file():
                raise BuildError(f"the build did not produce {name}", log)
        for src in sorted(built.iterdir()):
            wanted = src.name in plan.targets or _RE_SHARED.search(src.name)
            if not wanted:
                continue
            dest = out / src.name
            if src.is_symlink():
                target = os.readlink(src)
                if "/" in target or "\\" in target or target.startswith("."):
                    raise BuildError(f"{src.name} links out of the build folder", log)
                os.symlink(target, dest)
                links[src.name] = target
            elif src.is_file():
                shutil.copy2(src, dest)
                files[src.name] = _sha256(dest)
        for name, target in links.items():
            if target not in files and target not in links:
                raise BuildError(f"{name} links to {target}, which is not part of the build", log)
        return files, links


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
