#!/usr/bin/env python3
"""
The SearXNG that Ditto's web search uses, set up so that the person has nothing to configure (DESIGN, Knowledge base and
Deep Ingest, round 102: "wir legen sie ins repo, der installer kopiert die dann einfach rüber").

The repository holds the finished settings file (`configs/searxng/settings.yml`: `use_default_settings: true` and
`search: formats` with html and json, made from SearXNG's own documentation). `setup` does what the installer does after
it has installed the software, and what the person can do again later (`llm-cli ditto web setup`, the button in the
settings window):

1. copies that file, as it is, to `<folder>/config/settings.yml`; a file that is there already is never overwritten;
2. makes the secret SearXNG needs, once per computer, in `<folder>/secret` (readable by the person only): the file in the
   repository has no secret, because one in a public repository would be the same on every computer, and SearXNG takes
   its `server.secret_key` from the environment variable `SEARXNG_SECRET`;
3. starts the container `docker.io/searxng/searxng` through the framework's container engine, named `llmcf-searxng`,
   with the settings folder as `/etc/searxng`, a volume of the engine for `/var/cache/searxng` and the port published on
   this computer only (127.0.0.1:8888 -> 8080, the mapping of SearXNG's own Docker example), restarted with the engine.

The engine is a parameter (`client_factory`, docker-py's client), so the tests need none. Nothing here reaches the web
except the engine pulling the image.
"""

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

IMAGE = "docker.io/searxng/searxng:latest"
CONTAINER = "llmcf-searxng"
VOLUME = "llmcf-searxng-cache"
HOST_ADDRESS = "127.0.0.1"
HOST_PORT = 8888
CONTAINER_PORT = 8080
TEMPLATE = Path(__file__).resolve().parents[2] / "configs" / "searxng" / "settings.yml"
URL = f"http://{HOST_ADDRESS}:{HOST_PORT}"

NO_ENGINE = ("No container engine is running on this computer, so the search engine could not be started. Install "
             "Docker or Podman (the installer does it) and run this again; the settings file is ready.")


class SetupError(RuntimeError):
    """The search engine could not be set up; the message says why in plain words."""


@dataclass
class SetupResult:
    folder: Path
    settings: Path
    copied: bool = False            # the settings file was copied (False: one was there already and is kept)
    secret_made: bool = False
    started: bool = False           # the container was started now
    running: bool = False           # the container is running after this
    url: str = URL
    notes: List[str] = field(default_factory=list)


def folder_of(state_dir: Any) -> Path:
    """The folder of the search engine inside the person's state folder."""
    return Path(state_dir) / "searxng"


def prepare(folder: Path, template: Path = TEMPLATE) -> "tuple[Path, bool]":
    """Copies the finished settings file to `<folder>/config/settings.yml`; (that path, whether it was copied). A file
    that is there is never overwritten (the person may have changed it)."""
    target = Path(folder) / "config" / "settings.yml"
    if target.is_file():
        return target, False
    try:
        data = Path(template).read_bytes()
    except OSError:
        raise SetupError("The framework's settings file for the search engine is missing "
                         "(configs/searxng/settings.yml); install the framework again.") from None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)       # the container's own user reads it
    except OSError as e:
        raise SetupError(f"The settings file could not be written ({type(e).__name__}).") from None
    return target, True


def ensure_secret(folder: Path) -> "tuple[str, bool]":
    """The secret of this computer's SearXNG (made once, kept in `<folder>/secret` for the person alone); (secret, made)."""
    path = Path(folder) / "secret"
    try:
        existing = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        existing = ""
    if len(existing) >= 32:
        return existing, False
    secret = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secret + "\n")
        path.chmod(0o600)
    except OSError as e:
        raise SetupError(f"The secret of the search engine could not be written ({type(e).__name__}).") from None
    return secret, True


def _engine_error(e: Exception) -> SetupError:
    return SetupError(f"The container engine refused it: {str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__}")


def _client(client_factory: Optional[Callable[[], Any]]) -> Any:
    try:
        if client_factory is not None:
            return client_factory()
        from orchestrator.utils.docker_engine import ensure_docker_host
        ensure_docker_host()
        import docker
        client = docker.from_env(timeout=30)
        client.ping()
        return client
    except SetupError:
        raise
    except Exception:  # noqa: BLE001 - no engine, a stopped one or a missing docker package: one plain sentence
        raise SetupError(NO_ENGINE) from None


def container_state(client: Any) -> str:
    """"running", "stopped" or "missing" for the search engine's container."""
    from docker.errors import NotFound
    try:
        container = client.containers.get(CONTAINER)
    except NotFound:
        return "missing"
    except Exception as e:  # noqa: BLE001
        raise _engine_error(e) from None
    return "running" if getattr(container, "status", "") == "running" else "stopped"


def start(client: Any, settings: Path, secret: str) -> str:
    """Starts the container (pulling the image when the engine has none); "running" when it already ran, "started"
    when it was started now. SetupError with the reason when the engine refuses."""
    from docker.errors import ImageNotFound, NotFound
    state = container_state(client)
    if state == "running":
        return "running"
    try:
        if state == "stopped":
            client.containers.get(CONTAINER).start()
            return "started"
        try:
            client.images.get(IMAGE)
        except ImageNotFound:
            client.images.pull(IMAGE)
        from orchestrator.utils.docker_engine import create_container
        container = create_container(
            client, image=IMAGE, name=CONTAINER,
            environment={"SEARXNG_SECRET": secret},
            ports={f"{CONTAINER_PORT}/tcp": (HOST_ADDRESS, HOST_PORT)},
            volumes={str(Path(settings).parent): {"bind": "/etc/searxng", "mode": "rw"},
                     VOLUME: {"bind": "/var/cache/searxng", "mode": "rw"}},
            restart_policy={"Name": "unless-stopped"})
        container.start()
        return "started"
    except NotFound:
        raise SetupError("The search engine's image could not be found; check the network and try again.") from None
    except SetupError:
        raise
    except Exception as e:  # noqa: BLE001 - a port in use, no network for the pull, a refusal of the engine
        text = str(e)
        if "port is already allocated" in text or "address already in use" in text:
            raise SetupError(f"Port {HOST_PORT} on this computer is used by another program, so the search engine "
                             "could not start.") from None
        raise _engine_error(e) from None


def stop(client_factory: Optional[Callable[[], Any]] = None) -> str:
    """Stops and removes the container (the settings, the secret and the cache stay); "removed" or "missing"."""
    client = _client(client_factory)
    if container_state(client) == "missing":
        return "missing"
    try:
        client.containers.get(CONTAINER).remove(force=True)
    except Exception as e:  # noqa: BLE001
        raise _engine_error(e) from None
    return "removed"


def setup(state_dir: Any, client_factory: Optional[Callable[[], Any]] = None, *, run: bool = True,
          template: Path = TEMPLATE) -> SetupResult:
    """Copies the settings file, makes the secret and (with `run`) starts the container. The file and the secret are
    ready even when the engine is missing: SetupError is raised only after they are made, and its text says so."""
    folder = folder_of(state_dir)
    settings, copied = prepare(folder, template)
    secret, made = ensure_secret(folder)
    result = SetupResult(folder, settings, copied=copied, secret_made=made)
    result.notes.append("The settings file was copied." if copied else "A settings file is there already and is kept.")
    if not run:
        return result
    client = _client(client_factory)
    outcome = start(client, settings, secret)
    result.started, result.running = outcome == "started", True
    result.notes.append("The search engine was started." if result.started else "The search engine was running.")
    return result


__all__ = ["CONTAINER", "IMAGE", "NO_ENGINE", "SetupError", "SetupResult", "TEMPLATE", "URL", "VOLUME",
           "container_state", "ensure_secret", "folder_of", "prepare", "setup", "start", "stop"]
