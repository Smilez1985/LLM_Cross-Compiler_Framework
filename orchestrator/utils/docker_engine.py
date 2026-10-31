#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the container engine the framework talks to: a rootless engine by default (rootless
Podman through its Docker-compatible interface, or rootless Docker), a Docker daemon that runs as root only when the
person chose it

No standing rights that can do more than needed (docs/DESIGN.md): install.sh sets up rootless Podman (podman-docker
and the person's podman.socket, with sub-id ranges) by default, rootless Docker with --engine=docker or where Podman
cannot be installed, and Docker as root with the group docker only with --rootful and a warning. The framework then
finds the engine by itself: `ensure_docker_host()` sets DOCKER_HOST in this process when DOCKER_HOST is not set,
preferring the person's rootless Docker, then the person's Podman socket, over a Docker daemon that runs as root.
Every place that connects to Docker calls it first (the build engine's client, the container engine of the builder
image and of Ditto's container, the one Docker check of the build engine and Mojo), and the processes the framework
starts (docker buildx, child llm-cli runs) inherit the variable. scripts/build_container_image.sh asks the same
function: `python3 -I orchestrator/utils/docker_engine.py --docker-host` (and `--engine`), so this file uses the
standard library only and runs under the system's Python 3.9 as well. `root_effect_group()` names a group of the
person's account that has root effect (Linux 'docker', Windows 'docker-users'); Mojo says it plainly.

What Podman does not provide is refused early, in plain words, with the way to rootless Docker (`podman_refusal`).
Checked on Rocky Linux 9.8 with Podman 5.8.2 and buildah 1.43.1 (2026-10-23) and against Podman's sources:
- works under Podman: docker-py over the socket (ping, version, info, images, containers, df); image builds through
  the API with a target stage, build arguments and labels; Podman's `docker buildx build --load --progress=plain`
  (an alias of `podman build`); RUN --mount=type=bind (read-only by default, as in BuildKit), multi-stage builds and
  heredocs in a Dockerfile (buildah 1.35 and later; Debian and Ubuntu builds of buildah remove heredocs again);
  the framework's sandboxed containers, which run as the person's uid, through `create_container`: under rootless
  Podman they get the user namespace mode keep-id (checked with Podman 5.8.9 rootless: without it a container with
  the person's uid cannot write the person's bind-mounted folders, "Permission denied"; with it the files it writes
  belong to the person);
- does not work: Docker's GPU request (`docker run --gpus`, DeviceRequests): Podman's Docker-compatible interface
  passes on only CDI device requests and drops this one without a word (the container was created with no GPU);
- not there: BuildKit itself. Podman's `docker buildx` is buildah, without BuildKit's frontends (`# syntax=`), its
  cache export and import, or its drivers. llmcf's Hailo images are planned for BuildKit (HAILO_PLAN).
"""

import json
import os
import socket
import stat
import sys
import threading
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

# Docker's sockets on Linux: the system daemon (both names point to the same place on current systems).
DOCKER_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")
DOCKER_CE_URL = "https://docs.docker.com/engine/install/"

# What needs Docker itself rather than Podman's Docker-compatible interface (see the module text).
PODMAN_LIMITS = {
    "gpu": ("Docker's GPU request (--gpus, the NVIDIA GPU in a container) needs Docker: Podman's Docker-compatible "
            "interface drops that request without a word, so the container would run without the GPU"),
    "buildkit": ("this needs BuildKit, Docker's builder behind docker buildx: under Podman 'docker buildx' is "
                 "Podman's own builder (buildah), without BuildKit's frontends, cache export or drivers"),
}
DOCKER_CE_WAY = ("For these, use rootless Docker (BuildKit, and the GPU through the NVIDIA Container Toolkit): remove "
                 "the package podman-docker with your package manager (for example 'sudo dnf remove podman-docker'), "
                 "then run 'sudo ./install.sh --engine=docker' again; it sets up Docker CE from Docker's repository "
                 "for the system, with its key checked, rootless for your account. Manual: " + DOCKER_CE_URL)
ROOTLESS_DOCKER_URL = "https://docs.docker.com/engine/security/rootless/"

# The groups whose members control a container engine that runs with root's rights: on Linux the group docker (the
# Docker daemon runs as root), on Windows Docker Desktop's docker-users (it controls Docker Desktop's privileged
# service and its Linux machine). A member has root-equivalent rights on this computer, permanently.
ROOT_EFFECT_GROUPS = {"linux": "docker", "win32": "docker-users"}
ROOT_GROUP_TEXT = ("Your account is in the group '{group}', and that group has root effect: whoever is in it controls "
                   "a container engine that runs with administrator (root) rights, so every program you run can gain "
                   "those rights on this computer, permanently, also while the framework is not running.")
ROOT_GROUP_WHY = ("No standing rights that can do more than needed: a rootless engine builds the same containers with "
                  "only your own rights.")
ROOT_GROUP_WAY = {
    "linux": ("Switch to a rootless engine: run 'sudo ./install.sh' again (it sets up rootless Podman by default, or "
              "rootless Docker with --engine=docker; the framework prefers a rootless engine as soon as it is there), "
              "then an administrator takes your account out of the group: 'sudo gpasswd -d $USER docker', and you "
              "log in again. Rootless Docker by hand: " + ROOTLESS_DOCKER_URL),
    "win32": ("Switch to a rootless engine: install Podman Desktop (or run install.bat again; it sets up Podman with a "
              "rootless machine by default), then an administrator takes your account out of the group: "
              "'net localgroup docker-users <your account> /delete' in an administrator's terminal, and you sign in "
              "again. Keep Docker Desktop only if you need it, knowing what the group means."),
}


@dataclass(frozen=True)
class EngineChoice:
    """What ensure_docker_host decided. `docker_host`: the DOCKER_HOST in effect afterwards ("" = docker-py's default,
    Docker's socket); `set_here`: the framework set it to the person's Podman socket; `reason`: why, in plain words
    (no path)."""
    docker_host: str
    set_here: bool
    reason: str


def _is_socket(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def _points_to_podman(path: Path) -> bool:
    """podman-docker links Docker's socket names to Podman's own sockets (its systemd-tmpfiles entries:
    /run/docker.sock -> /run/podman/podman.sock, $XDG_RUNTIME_DIR/docker.sock -> $XDG_RUNTIME_DIR/podman/podman.sock):
    such a name is Podman, not Docker."""
    try:
        return "podman" in Path(os.path.realpath(path)).parts
    except (OSError, ValueError):
        return False


def _docker_daemon_at(path: Path, timeout: float = 0.5) -> bool:
    """Whether a Docker daemon listens on the socket `path`: it accepts the connection and does not answer as Podman,
    or it refuses this user (the person is not in the group docker yet, which a new login fixes). A socket file nobody
    listens on (a stopped daemon) and a name that leads to Podman's socket (podman-docker's links) do not count."""
    if not _is_socket(path) or not hasattr(socket, "AF_UNIX") or _points_to_podman(path):
        return False
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        conn.connect(str(path))
    except PermissionError:
        return True
    except OSError:
        return False
    finally:
        conn.close()
    return engine_at("unix://" + str(path), timeout=1.0) != "podman"


# Where systemd keeps each user's runtime folder (/run/user/<uid>, $XDG_RUNTIME_DIR of a login): the person's Podman
# socket (podman/podman.sock) and rootless Docker's socket (docker.sock) live there.
USER_RUNTIME_ROOT = "/run/user"


def podman_socket(environ: Mapping[str, str], uid: Optional[int] = None) -> Optional[Path]:
    """The Podman socket of this user: $XDG_RUNTIME_DIR/podman/podman.sock or /run/user/<uid>/podman/podman.sock
    (systemctl --user enable --now podman.socket, as install.sh sets it up), for root also /run/podman/podman.sock.
    None when there is none."""
    candidates = []
    runtime_dir = str(environ.get("XDG_RUNTIME_DIR") or "")
    if runtime_dir:
        candidates.append(Path(runtime_dir) / "podman" / "podman.sock")
    if uid is not None:
        candidates.append(Path(USER_RUNTIME_ROOT) / str(uid) / "podman" / "podman.sock")
        if uid == 0:
            candidates.append(Path("/run/podman/podman.sock"))
    for path in candidates:
        if _is_socket(path):
            return path
    return None


_LOCK = threading.Lock()
_CHOICE: Optional[EngineChoice] = None
# The engine of each docker-py client already asked (a client talks to one daemon for its life).
_ENGINES: "weakref.WeakKeyDictionary[Any, str]" = weakref.WeakKeyDictionary()


def _decide(environ: MutableMapping[str, str], uid: Optional[int], docker_sockets: Sequence[str]) -> EngineChoice:
    """The engine of this process: a DOCKER_HOST the person set always wins; otherwise a rootless engine before one
    that runs as root (no standing rights that can do more than needed, docs/DESIGN.md): the person's rootless Docker,
    then the person's rootless Podman, then the system's Docker daemon. Root itself keeps the old order (Docker's
    daemon, then root's Podman socket): nothing it runs is rootless."""
    given = str(environ.get("DOCKER_HOST") or "").strip()
    if given:
        return EngineChoice(given, False, "DOCKER_HOST is set")
    if not sys.platform.startswith("linux"):
        return EngineChoice("", False, "Docker's default address on this system")
    if uid not in (None, 0):
        rootless = rootless_docker_socket(environ, uid)
        if rootless is not None:
            host = "unix://" + str(rootless)
            environ["DOCKER_HOST"] = host
            return EngineChoice(host, True, ROOTLESS_DOCKER_CHOSEN)
        found = podman_socket(environ, uid)
        if found is not None:
            host = "unix://" + str(found)
            environ["DOCKER_HOST"] = host
            return EngineChoice(host, True, ROOTLESS_PODMAN_CHOSEN)
        if _docker_daemon_running(environ, docker_sockets):
            return EngineChoice("", False, "a Docker daemon is there")
        return EngineChoice("", False, "neither a Docker daemon nor a Podman socket is there")
    if _docker_daemon_running(environ, docker_sockets):
        return EngineChoice("", False, "a Docker daemon is there")
    found = podman_socket(environ, uid)
    if found is None:
        return EngineChoice("", False, "neither a Docker daemon nor a Podman socket is there")
    host = "unix://" + str(found)
    environ["DOCKER_HOST"] = host
    return EngineChoice(host, True, "no Docker daemon is there, the person's Podman socket is")


# Why ensure_docker_host chose a rootless engine (plain words, no path)
ROOTLESS_DOCKER_CHOSEN = "the person's rootless Docker is there (a rootless engine is preferred)"
ROOTLESS_PODMAN_CHOSEN = ("the person's rootless Podman socket is there (a rootless engine is preferred over a Docker "
                          "daemon that runs as root)")


def rootless_docker_socket(environ: Mapping[str, str], uid: Optional[int] = None) -> Optional[Path]:
    """The socket of the person's rootless Docker ($XDG_RUNTIME_DIR/docker.sock, as dockerd-rootless-setuptool.sh sets
    it up; without XDG_RUNTIME_DIR /run/user/<uid>/docker.sock, as podman_socket looks for Podman's) when a Docker
    daemon answers there (not podman-docker's link to Podman's socket); None otherwise."""
    candidates = []
    runtime_dir = str(environ.get("XDG_RUNTIME_DIR") or "")
    if runtime_dir:
        candidates.append(Path(runtime_dir) / "docker.sock")
    if uid not in (None, 0):
        candidates.append(Path(USER_RUNTIME_ROOT) / str(uid) / "docker.sock")
    for path in candidates:
        if _docker_daemon_at(path):
            return path
    return None


def _docker_daemon_running(environ: Mapping[str, str], docker_sockets: Sequence[str]) -> bool:
    rootless = [str(Path(environ["XDG_RUNTIME_DIR"]) / "docker.sock")] if environ.get("XDG_RUNTIME_DIR") else []
    return any(_docker_daemon_at(Path(p)) for p in list(docker_sockets) + rootless)


# What Mojo says when DOCKER_HOST still points at a Podman socket while the person's rootless Docker runs (no path in
# it). A Docker daemon that runs as root does not count: the framework prefers a rootless engine itself.
STALE_PODMAN_HOST = ("DOCKER_HOST still points at a Podman socket (probably left from an earlier Podman session), "
                     "while your rootless Docker runs on this computer. Your DOCKER_HOST always wins, so the framework "
                     "talks to Podman, with Podman's limits (no NVIDIA GPU in a container, no BuildKit).")
STALE_PODMAN_WAY = ("To use your rootless Docker: remove the DOCKER_HOST line from your shell's start file (for "
                    "example the line 'export DOCKER_HOST=...podman.sock' in ~/.bashrc), open a new terminal and start "
                    "the framework again. To keep Podman on purpose, leave it as it is.")


def stale_podman_host(environ: Optional[Mapping[str, str]] = None, *,
                      docker_sockets: Sequence[str] = DOCKER_SOCKETS) -> bool:
    """Whether DOCKER_HOST points at a Podman socket (a podman path, or Podman answers there) while the person's
    rootless Docker answers ($XDG_RUNTIME_DIR/docker.sock): a DOCKER_HOST left from an earlier Podman session. A
    Docker daemon that runs as root does not make it stale (a rootless engine is preferred, _decide). The person's
    DOCKER_HOST still wins; Mojo names it (STALE_PODMAN_HOST, STALE_PODMAN_WAY). `docker_sockets` stays for the
    callers that pass it. Linux only; never raises."""
    env = os.environ if environ is None else environ
    try:
        given = str(env.get("DOCKER_HOST") or "").strip()
        if not given.startswith("unix://") or not sys.platform.startswith("linux"):
            return False
        path = Path(given[len("unix://"):])
        podman = "podman" in path.parts or _points_to_podman(path) or engine_at(given, timeout=1.0) == "podman"
        return bool(podman) and rootless_docker_socket(env) is not None
    except Exception:  # noqa: BLE001 - a health hint must never stop a check
        return False


def ensure_docker_host(environ: Optional[MutableMapping[str, str]] = None, *, uid: Optional[int] = None,
                       docker_sockets: Sequence[str] = DOCKER_SOCKETS, refresh: bool = False) -> EngineChoice:
    """Sets DOCKER_HOST to the person's rootless engine when DOCKER_HOST is not set (Linux only): the person's rootless
    Docker, else the person's Podman socket, both before a Docker daemon that runs as root (_decide); for root, Podman's
    socket only when no Docker daemon answers. Changes nothing otherwise: a DOCKER_HOST the person set always wins.
    For this process (os.environ, decided once; `refresh` decides again) or
    for the mapping `environ` (tests, child environments). Never raises."""
    own = environ is None
    env = os.environ if own else environ
    if uid is None and hasattr(os, "getuid"):
        uid = os.getuid()
    global _CHOICE
    with _LOCK:
        if own and _CHOICE is not None and not refresh:
            if _CHOICE.set_here and not env.get("DOCKER_HOST"):
                env["DOCKER_HOST"] = _CHOICE.docker_host
            return _CHOICE
        try:
            choice = _decide(env, uid, docker_sockets)
        except Exception:  # noqa: BLE001 - deciding must never stop a start; docker-py's default stays
            choice = EngineChoice(str(env.get("DOCKER_HOST") or ""), False, "the engine could not be determined")
        if own:
            _CHOICE = choice
        return choice


def engine_name(client: Any) -> str:
    """'podman' when the API behind the docker-py `client` is Podman's (its version lists the component
    'Podman Engine'), 'docker' for another answer (Docker's), '' when there is no client or no answer."""
    if client is None:
        return ""
    try:
        known = _ENGINES.get(client)
    except TypeError:   # a client that cannot be weakly referenced: asked every time
        known = None
    if known:
        return known
    try:
        version = client.version() or {}
    except Exception:  # noqa: BLE001 - no answer: the engine is unknown (asked again next time)
        return ""
    name = engine_of_version(version)
    try:
        _ENGINES[client] = name
    except TypeError:
        pass
    return name


def engine_of_version(version: Mapping[str, Any]) -> str:
    """The engine named by the JSON of GET /version ('podman', 'docker'; '' for none)."""
    if not isinstance(version, Mapping) or not version:
        return ""
    names = [str(c.get("Name") or "") for c in version.get("Components") or [] if isinstance(c, Mapping)]
    return "podman" if any("podman" in name.lower() for name in names) else "docker"


def engine_at(docker_host: str = "", timeout: float = 5.0) -> str:
    """The engine behind a unix:// DOCKER_HOST (empty: Docker's default socket), asked with GET /version over the
    socket without docker-py (for scripts/build_container_image.sh). '' when nothing answers or it is no unix
    socket."""
    host = docker_host or "unix://" + DOCKER_SOCKETS[0]
    if not host.startswith("unix://") or not hasattr(socket, "AF_UNIX"):
        return ""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        conn.connect(host[len("unix://"):])
        conn.sendall(b"GET /version HTTP/1.0\r\nHost: docker\r\n\r\n")
        data = b""
        while len(data) < 1 << 20:
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    except OSError:
        return ""
    finally:
        conn.close()
    head, _, body = data.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].split()
    if len(status) < 2 or not status[0].startswith(b"HTTP/") or status[1] != b"200":
        return ""
    try:
        return engine_of_version(json.loads(body.decode("utf-8", "replace")))
    except ValueError:
        return ""


# Podman's user namespace mode that maps the person's uid and gid to the same ids inside the container.
USERNS_KEEP_ID = "keep-id"
# Podman's --group-add value that keeps the person's own supplementary groups in the container (Podman's
# documentation of --group-add: "keep-groups" passes the user's groups on, only with crun, and cannot be combined with
# other groups). Under rootless Podman the host's render/video gids are not mapped into the user namespace, so a
# numeric group_add does not open /dev/dri; keep-groups does. Podman's Docker-compatible API passes HostConfig.GroupAdd
# into the same options as the command line (pkg/api/handlers/compat/containers_create.go: GroupAdd -> cliOpts).
GROUP_KEEP_GROUPS = "keep-groups"
# Whether the engine behind each docker-py client already asked runs rootless (a client talks to one daemon).
_ROOTLESS: "weakref.WeakKeyDictionary[Any, bool]" = weakref.WeakKeyDictionary()


def rootless_podman(client: Any) -> bool:
    """Whether the API behind the docker-py `client` is Podman running rootless (its /info lists the security option
    'name=rootless'; the person's podman.socket always is). There the person is root inside a container's user
    namespace, and a container that runs as the person's uid sees the person's folders as root's. False for Docker,
    rootful Podman and when Podman does not answer (asked again next time)."""
    return engine_name(client) == "podman" and _rootless(client)


def rootless_docker(client: Any) -> bool:
    """Whether the API behind `client` is Docker in rootless mode (docker info's SecurityOptions list 'name=rootless').
    There uid 0 inside a container is the person outside, and any other uid is a sub-uid that cannot write the
    person's folders; Docker has no keep-id."""
    return engine_name(client) == "docker" and _rootless(client)


def _rootless(client: Any) -> bool:
    try:
        known = _ROOTLESS.get(client)
    except TypeError:   # a client that cannot be weakly referenced: asked every time
        known = None
    if known is not None:
        return known
    try:
        options = (client.info() or {}).get("SecurityOptions") or []
    except Exception:  # noqa: BLE001 - no answer: not rootless as far as we know
        return False
    rootless = any(str(option).strip() == "name=rootless" for option in options)
    try:
        _ROOTLESS[client] = rootless
    except TypeError:
        pass
    return rootless


def create_container(client: Any, **kwargs: Any) -> Any:
    """client.containers.create(**kwargs) for every container of the framework (the build stages, the llama.cpp builder,
    Ditto's check and server containers). Under rootless Podman a container that runs as a user other than root (the
    sandbox runs as the person, utils/sandbox.py) also gets Podman's user namespace mode keep-id: the person's uid is
    then the same inside the container as outside, so the container writes the person's bind-mounted folders and
    what it writes belongs to the person, as with Docker. Without it the folders look like root's inside and the build
    stops with "Permission denied". docker-py's create() refuses any userns_mode but "host", so there the request goes
    through docker-py's own conversion of create()'s arguments (docker 7.2.0) and the low-level API, and the container
    comes back exactly as create() returns it."""
    devices = [str(d) for d in kwargs.get("devices") or []]
    if any(d.split(":", 1)[0].startswith("/dev/dri") for d in devices) and rootless_podman(client):
        # The render node under rootless Podman: the person's own groups, not the host's (unmapped) gids.
        kwargs = dict(kwargs, group_add=[GROUP_KEEP_GROUPS])
    user = str(kwargs.get("user") or "").split(":")[0]
    if user not in ("", "0", "root") and rootless_docker(client):
        # Rootless Docker: uid 0 in the container is the person outside, so a container of the person runs as 0:0
        # there (the sandbox's other limits stay: no capabilities, read-only root, no network).
        kwargs = dict(kwargs, user="0:0")
        user = "0"
    if user in ("", "0", "root") or not rootless_podman(client):
        return client.containers.create(**kwargs)
    from docker.models.containers import _create_container_args   # docker-py's create(): its arguments -> the API's
    args = dict(kwargs)
    image = args.pop("image")
    args["image"] = getattr(image, "id", image)
    args.setdefault("command", None)
    args["version"] = client.api._version
    request = _create_container_args(args)
    request["host_config"]["UsernsMode"] = USERNS_KEEP_ID
    return client.containers.get(client.api.create_container(**request)["Id"])


def root_effect_group(platform: Optional[str] = None, *, groups: Optional[Sequence[str]] = None) -> str:
    """The group with root effect (ROOT_EFFECT_GROUPS) the person's account is in: 'docker' on Linux, 'docker-users'
    on Windows; '' when it is in none of them or that cannot be read. `groups`: the account's group names (tests);
    otherwise read here (Linux: the process's groups and the group's member list, so a membership that a new login
    would bring counts too; Windows: 'whoami /groups'). Only the group's name leaves this function, never an account
    name. Never raises."""
    platform = platform or sys.platform
    key = "win32" if platform.startswith("win") else "linux" if platform.startswith("linux") else ""
    group = ROOT_EFFECT_GROUPS.get(key, "")
    if not group:
        return ""
    try:
        names = [str(g) for g in groups] if groups is not None else _own_groups(key, group)
    except Exception:  # noqa: BLE001 - a health hint must never stop a check
        return ""
    wanted = group.lower()
    return group if any(n.strip().lower().rsplit("\\", 1)[-1] == wanted for n in names) else ""


def _own_groups(key: str, group: str) -> list:
    """The account's group names that matter for `group` (read locally, never shown)."""
    if key == "linux":
        import grp
        try:
            entry = grp.getgrnam(group)
        except KeyError:
            return []
        own = set(os.getgroups()) | {os.getgid()}
        try:
            import pwd
            me = pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            me = ""
        return [group] if entry.gr_gid in own or (me and me in entry.gr_mem) else []
    import subprocess
    out = subprocess.run(["whoami", "/groups", "/fo", "csv", "/nh"], capture_output=True, text=True,  # nosec B603 B607
                         timeout=10, check=False).stdout or ""
    return [line.split(",", 1)[0].strip().strip('"') for line in out.splitlines() if line.strip()]


def save_image(image: Any, path: Any, *, named: Any = True, cancelled: Optional[Any] = None) -> str:
    """`docker save` of the docker-py `image` into the file `path`, written chunk by chunk as the engine sends it
    (Docker and Podman's Docker-compatible interface alike), and its sha256 (lower-case hex). `named`: the tags the
    archive carries (True: the image's own, or one name); `cancelled()` is called between two chunks and may raise to
    stop. The one docker save of the framework: the deployment manager's package export, the security scan's archive
    and the export of a build container image (Core/build_image.py) use it."""
    with open(path, "wb") as handle:
        for chunk in image.save(named=named):
            if cancelled is not None:
                cancelled()
            handle.write(chunk)
    from orchestrator.utils.helpers import sha256_file   # the one hash (framework code only; not for the script use)
    return sha256_file(path)


def podman_refusal(feature: str, engine: str) -> Optional[str]:
    """The plain refusal of a `feature` of PODMAN_LIMITS when `engine` is 'podman' (None otherwise): why it does not
    work and the way to Docker CE."""
    if engine != "podman":
        return None
    return f"Under Podman this does not work: {PODMAN_LIMITS[feature]}. {DOCKER_CE_WAY}"


def podman_note() -> str:
    """The doctor's line about Podman: what it cannot do here and the way to Docker CE."""
    return ("The framework works with Podman's Docker-compatible interface, with limits: " + PODMAN_LIMITS["gpu"]
            + "; and " + PODMAN_LIMITS["buildkit"] + ". " + DOCKER_CE_WAY)


def main(argv: Sequence[str]) -> int:
    """`--docker-host`: the DOCKER_HOST the framework would set (one line, empty when it sets none);
    `--engine`: the engine that answers there or at Docker's default socket (podman, docker or empty)."""
    env = dict(os.environ)
    choice = ensure_docker_host(env, docker_sockets=DOCKER_SOCKETS)
    if "--engine" in argv:
        print(engine_at(env.get("DOCKER_HOST", "")))
    else:
        print(choice.docker_host if choice.set_here else "")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
