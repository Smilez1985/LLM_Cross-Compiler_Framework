#!/usr/bin/env python3
"""
The search engine of Ditto's web search, both ways in one place (DESIGN, Knowledge base and Deep Ingest, rounds 103 and
104): what the person is told before they say yes, setting it up, and removing it. `llm-cli ditto web setup` and the web
search window call these functions; nothing here asks the question (the command and the window do, with No as the
default).

- `own`, the default: SearXNG in the person's own Python folder (Core/searxng_native.py): no Docker, no administrator
  rights, no WSL; it is installed, started once to check that it answers, and stopped.
- `container`, the opt-in of whoever wants Docker: the container of Core/searxng_setup.py through the engine that is
  installed.

Both leave nothing running: SearXNG starts for a search and stops right after. A successful setup writes the setting
`web_search_engine` (which way) and turns the way "searxng" of the web search on, because the person asked for the search
engine to use it; their other choices are kept.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from orchestrator.Core import searxng_native, searxng_setup, web_search
from orchestrator.utils import install_manifest

WAYS = web_search.ENGINES
DEFAULT_WAY = "own"
SIZE_OWN = "about 200 MB"


class EngineError(RuntimeError):
    """Not set up or not removed; the message says why in plain words."""


@dataclass
class EngineResult:
    way: str
    folder: Path
    notes: List[str] = field(default_factory=list)
    url: str = searxng_setup.URL


def explain(way: str, state_dir: Any) -> List[str]:
    """What setting up the search engine this way does, in plain lines: what it installs and what it runs with. The command
    and the window show it before they ask, and add how to remove it again in their own words."""
    folder = searxng_setup.folder_of(state_dir) if state_dir is not None else Path("<your state folder>/searxng")
    if way == "own":
        return ["This sets up SearXNG, a search engine that Ditto's web search can use, in a folder of your own.",
                f"What it installs: a pinned copy of SearXNG from github.com with its own Python packages, {SIZE_OWN}, in "
                f"{folder / 'native'}.",
                "How it runs: with your own rights, only while a search needs it and on this computer only (127.0.0.1); "
                "nothing runs in between. It needs no Docker, no administrator rights and no WSL."]
    if way == "container":
        return ["This sets up SearXNG, a search engine that Ditto's web search can use, as a container.",
                f"What it installs: the image {searxng_setup.IMAGE} through your container engine (Docker or Podman, which "
                f"must be installed), and its settings and secret in {folder}.",
                f"How it runs: started only while a search needs it and stopped right after, published on this computer "
                f"only (127.0.0.1:{searxng_setup.HOST_PORT}); the engine does not restart it by itself."]
    raise EngineError(f"The way is own or container, not '{way}'.")


def _ways_with_searxng(config: Any) -> List[str]:
    try:
        current = config.get("web_search_ways", []) or []
    except Exception:  # noqa: BLE001
        current = []
    current = [w.strip() for w in current.split(",")] if isinstance(current, str) else list(current)
    return [w for w in web_search.WAYS if w in current or w == "searxng"]


def current(config: Any) -> str:
    """The way that is set up ("own", "container" or "" for none), from the setting web_search_engine."""
    try:
        value = str(config.get("web_search_engine", "") or "")
    except Exception:  # noqa: BLE001
        value = ""
    return value if value in WAYS else ""


def setup(way: str, state_dir: Any, config: Any, *, run: bool = True, progress: Callable[[str], None] = lambda text: None,
          client_factory: Optional[Callable[[], Any]] = None, install_fn: Callable = searxng_native.install,
          running_cls: Callable = searxng_native.Running) -> EngineResult:
    """Sets the search engine up this way (the person said yes). With `run` it is started once to check that it answers
    and stopped again. Writes web_search_engine and turns the way "searxng" on. EngineError, searxng_native.NativeError or
    searxng_setup.SetupError with the reason when it does not work; what was made before stays and a second try goes on from
    there."""
    if way not in WAYS:
        raise EngineError(f"The way is own or container, not '{way}'.")
    if state_dir is None:
        raise EngineError("The state folder is not known; the search engine could not be set up.")
    folder = searxng_setup.folder_of(state_dir)
    result = EngineResult(way, folder)
    if way == "own":
        settings, copied = searxng_setup.prepare(folder)
        secret, _ = searxng_setup.ensure_secret(folder)
        install_fn(folder / "native", progress=progress)
        result.notes.append("SearXNG is installed in your own folder.")
        if run:
            progress("Checking that it answers ...")
            with running_cls(folder / "native", settings, secret,
                             searxng_native.port_of(str(config.get("web_search_url", searxng_setup.URL)))):
                pass
            result.notes.append("It answered and was stopped again: it starts for a search.")
    else:
        sub = searxng_setup.setup(state_dir, client_factory, run=run)
        result.notes += sub.notes
    # Written down so that the uninstaller knows it: only the framework uses it, and it was not there before (the folder is
    # the framework's own). A manifest that cannot be written only means the uninstaller leaves it alone.
    install_manifest.record(install_manifest.person_path(state_dir), install_manifest.Entry(
        "searxng", "own", "searxng", str(folder), "SearXNG, die Suchmaschine fuer Dittos Websuche"))
    saved = web_search.configure(config, engine=way, ways=_ways_with_searxng(config))
    result.notes.append("The web search uses it now (the way \"search engine on this computer\" is on)."
                        + ("" if saved else " The settings file could not be saved; this holds until the program ends."))
    return result


def remove_files(state_dir: Any, *, client_factory: Optional[Callable[[], Any]] = None,
                 container_expected: bool = False) -> List[str]:
    """Removes the software of both ways: the Python folder and the container. The settings file and the secret stay (they
    are a few bytes and the next setup uses them). Takes the component out of the install manifest. The lines say what
    happened. The uninstaller calls this with only the state folder: it needs no settings."""
    if state_dir is None:
        raise EngineError("The state folder is not known; nothing was removed.")
    folder = searxng_setup.folder_of(state_dir)
    notes: List[str] = []
    if searxng_native.remove(folder / "native"):
        notes.append("The Python folder of SearXNG was removed.")
    try:
        if searxng_setup.remove(client_factory) == "removed":
            notes.append("The container was stopped and removed.")
    except searxng_setup.SetupError:
        if container_expected:
            notes.append("The container could not be reached (is the container engine running?); remove it with "
                         "your engine if it is still there.")
    install_manifest.forget(install_manifest.person_path(state_dir), "searxng")
    return notes


def remove(state_dir: Any, config: Any, *, client_factory: Optional[Callable[[], Any]] = None) -> List[str]:
    """Removes the search engine (remove_files) and clears web_search_engine. The lines say what happened."""
    notes = remove_files(state_dir, client_factory=client_factory, container_expected=current(config) == "container")
    web_search.configure(config, engine="none")
    notes.append("The settings file and the secret stay; the web search no longer starts a search engine."
                 if notes else "There was nothing to remove.")
    return notes


__all__ = ["DEFAULT_WAY", "EngineError", "EngineResult", "SIZE_OWN", "WAYS", "current", "explain", "remove",
           "remove_files", "setup"]
