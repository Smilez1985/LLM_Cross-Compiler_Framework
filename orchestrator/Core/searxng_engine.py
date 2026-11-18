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

`update_plan` and `update` move the search engine on (DESIGN, Installer, platforms and dependencies): the own way to the
commit this version of the framework pins, the container way to the image's newest stand. The plan says what changes and
touches nothing, so the command and the window can ask before `update` does it; neither takes a newer SearXNG than the
framework pins, and offline mode refuses it.
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


@dataclass
class UpdatePlan:
    """What an update would do, read without changing anything. `current` and `pinned` are the first 12 letters of the two
    commits of the own way (empty for the container); `link` shows what changed between them; `lines` say it in plain words
    for the command (the window has its own, translated texts and uses the fields)."""
    way: str
    needs_update: bool
    current: str = ""
    pinned: str = ""
    link: str = ""
    lines: List[str] = field(default_factory=list)


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


def update_plan(state_dir: Any, config: Any, *, info_fn: Callable = searxng_native.update_info) -> UpdatePlan:
    """What updating the search engine that is set up would do (nothing is fetched or changed). EngineError when none is set
    up, the state folder is not known, or the setting says own but nothing is installed there."""
    way = current(config)
    if not way:
        raise EngineError("No search engine is set up, so there is nothing to update.")
    if state_dir is None:
        raise EngineError("The state folder is not known; the search engine could not be updated.")
    folder = searxng_setup.folder_of(state_dir)
    if way == "container":
        return UpdatePlan(way, True, lines=[
            f"The image {searxng_setup.IMAGE} is pulled again through your container engine, and the container is made new "
            "from it at the next search.",
            f"Your settings file and secret in {folder} stay."])
    info = info_fn(folder / "native")
    if not info.present:
        raise EngineError("The setting says the search engine is in your own folder, but it is not installed there; set it "
                          "up again.")
    short_current, short_pinned = info.current[:12], info.pinned[:12]
    if not info.needs_update:
        return UpdatePlan(way, False, short_current, short_pinned, info.link, [
            f"SearXNG in your own folder is at the stand that this version of the framework pins ({short_pinned}); there "
            "is nothing to update."])
    return UpdatePlan(way, True, short_current, short_pinned, info.link, [
        f"SearXNG in your own folder is at {short_current}; this version of the framework pins {short_pinned}.",
        f"What changes: a new copy of SearXNG is fetched from github.com into a new folder beside the old one, with its own "
        f"Python packages ({SIZE_OWN}), and started once to check that it answers. The old copy is removed only after that; "
        "when a step fails, the old copy stays and keeps working. Your settings file and secret stay.",
        f"What changed between the two: {info.link}"])


def update(state_dir: Any, config: Any, *, progress: Callable[[str], None] = lambda text: None,
           client_factory: Optional[Callable[[], Any]] = None, update_fn: Callable = searxng_native.update,
           container_update: Callable = searxng_setup.update, info_fn: Callable = searxng_native.update_info,
           offline: Optional[Callable[[Any], bool]] = None) -> EngineResult:
    """Updates the search engine that is set up (the person said yes after the plan). Offline mode refuses it. EngineError,
    searxng_native.NativeError or searxng_setup.SetupError with the reason; a step that fails leaves what was there as it
    was."""
    plan = update_plan(state_dir, config, info_fn=info_fn)
    if offline is None:
        from orchestrator.utils.locality import offline_mode as offline
    if offline(config):
        raise EngineError("Offline mode is on, and updating the search engine needs the network. Turn offline mode off "
                          "first.")
    folder = searxng_setup.folder_of(state_dir)
    result = EngineResult(plan.way, folder)
    if plan.way == "container":
        progress("Pulling the image ...")
        container_update(client_factory)
        result.notes.append("The image was pulled again; the container is made new from it at the next search.")
        return result
    if not plan.needs_update:
        result.notes += plan.lines
        return result
    settings, _ = searxng_setup.prepare(folder)
    secret, _ = searxng_setup.ensure_secret(folder)
    outcome = update_fn(folder / "native", settings, secret, progress=progress)
    result.notes.append("SearXNG was updated to the stand that this version of the framework pins; it starts for a search "
                        "as before." if outcome == "updated" else "SearXNG is at the pinned stand already.")
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


__all__ = ["DEFAULT_WAY", "EngineError", "EngineResult", "SIZE_OWN", "UpdatePlan", "WAYS", "current", "explain", "remove",
           "remove_files", "setup", "update", "update_plan"]
