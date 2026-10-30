#!/usr/bin/env python3
"""
The build container image: the Docker image with a target's toolchain that the build stages run in (built by
Core/builder.py from the target's Dockerfile, labelled IMAGE_LABEL and INPUTS_LABEL). It is not the target artifact,
package or bundle, which is what goes to the board.

Offline builds of the build container image (DECISIONS round 36): offline mode means no internet; the own network may
be there. Offline mode may build the image when every source its Dockerfile needs is local by the one locality rule
(utils/locality.py check_reach, the person's opt-outs included):

- base images (FROM, COPY --from, RUN --mount from; community_pack.dockerfile_bases): on this computer, from a
  registry in the own network, or (Docker Hub images) through a registry mirror of the Docker daemon in the own network
  (client.info() RegistryConfig.Mirrors; every mirror is judged);
- llama.cpp (a Dockerfile with ARG LLAMA_CPP_REPO): the mirror the person set as LLAMA_CPP_REPO_OVERRIDE, pinned to
  its checked local addresses (runtime/build/source.offline_reach), or the verified local copy of the pinned commit
  (runtime/build/source.cached_source, the local runtime's source store), staged into the build context;
- apt and pip: only through a mirror or cache in the own network that the person set (person-only settings
  build_apt_mirror, build_apt_proxy, build_pip_index: locality.BUILD_SOURCE_SETTINGS), passed to the Dockerfile as the
  build arguments APT_MIRROR, APT_PROXY, PIP_INDEX_URL (PIP_TRUSTED_HOST for an http index), judged on every build;
- every other address the Dockerfile fetches from (an http(s) URL in a RUN or ADD line, an ARG default), judged by its
  host; an ARG whose name ends in _PIP_URL is a pip location the Dockerfile replaces by the pip index.

offline_plan() is the one plan: the builder asks it before an offline image build (and stops before anything slow when
a source is not local, naming each one and how to provide it), and the CLI (`build image plan`), the GUI (Build >
Build Container Images...) and the MCP status (get_target_capabilities with check_image) show the same plan. An AI
sees labels only (ImagePlan.ai()): never a mirror's address, a path or a URL with credentials.

Air-gapped transfer: export_image() saves a build container image into a file with a sha256 sidecar (the docker save
of utils/docker_engine.save_image, which the deployment manager's package export uses too); import_image() loads a file
the person brought after checking it against a sha256 the person gives or the sidecar, and only when its labels are
those a build here looks for (IMAGE_LABEL of the target, INPUTS_LABEL of this computer's settings). Both follow the
one disk rule (disk_plan.room) and are person-only: an AI caller gets needs_person.
"""

import json
import re
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

from orchestrator.utils.locality import BUILD_SOURCE_SETTINGS
from orchestrator.utils.next_steps import WithSteps

# The build arguments that say where the image's sources come from, never what they are: they stay out of the image's
# INPUTS_LABEL, so an image built online is found offline and the other way round (the pins are in the other args).
SOURCE_ARGS = ("APT_MIRROR", "APT_PROXY", "PIP_INDEX_URL", "PIP_TRUSTED_HOST", "LLAMA_CPP_SOURCE")
APT_MIRROR, APT_PROXY, PIP_INDEX = BUILD_SOURCE_SETTINGS
SETTING_ARGS = {APT_MIRROR: "APT_MIRROR", APT_PROXY: "APT_PROXY", PIP_INDEX: "PIP_INDEX_URL"}
# Where the local copy of llama.cpp lands in the build context (the Dockerfile COPYs sources/ and builds from it with
# LLAMA_CPP_SOURCE=context); utils/build_context.py creates the empty folder for every build.
CONTEXT_SOURCES = "sources"
LLAMA_COPY = "llama.cpp"
# A mirror address the person sets: http or https, a host (name, IPv4 or [IPv6]), an optional port and a plain path;
# no user, password, token, query or fragment (a build argument is visible in the image's history).
MIRROR_URL = re.compile(r"https?://(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%+/-]*)?",
                        re.ASCII)
# Every address a RUN or ADD line or an ARG default may fetch from: a URL of any scheme (http, https, git, ssh, ftp,
# rsync, ...) and the scp-like form user@host:path.
_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'`<>|;&)]+|\b[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}:"
                  r"[^\s\"'`<>|;&)]*", re.ASCII)
# Package managers other than apt and pip: an offline build has no mirror setting for them, so a Dockerfile that uses
# one fetches from the internet (DECISIONS round 36: apt and pip through the person's mirrors; anything else is named).
_OTHER_PACKAGES = re.compile(
    r"\b(?:apk\s+(?:-\S+\s+)*(?:add|update|upgrade|fetch)|(?:dnf|yum|microdnf|tdnf)\s+(?:-\S+\s+)*(?:install|update|"
    r"upgrade|makecache|groupinstall)|zypper\s+(?:-\S+\s+)*(?:in|install|ref|refresh|up|update)|pacman\s+-S\w*|"
    r"conda\s+(?:install|create|update)|mamba\s+(?:install|create|update)|micromamba\s+(?:install|create)|"
    r"npm\s+(?:install|i|ci)|yarn\s+(?:install|add)|pnpm\s+(?:install|add)|cargo\s+(?:install|fetch|build)|"
    r"go\s+(?:install|get|mod\s+download)|gem\s+install|pipx\s+install|uv\s+(?:pip\s+install|sync|add|tool\s+install)|"
    r"poetry\s+install|snap\s+install|flatpak\s+install)\b", re.ASCII)
_PROVIDED = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):-[^}]*\}")
_APT = re.compile(r"\bapt(?:-get)?\s+(?:-\S+\s+)*(?:update|install|upgrade|dist-upgrade)\b")
_PIP = re.compile(r"\bpip3?\s+(?:-\S+\s+)*(?:install|download|wheel)\b|-m\s+pip\s+(?:-\S+\s+)*(?:install|download)\b")
_SYNTAX = re.compile(r"^\s*#\s*syntax\s*=\s*(\S+)", re.IGNORECASE)
PERSON_ACTORS = ("cli", "gui")
SIDECAR_SUFFIX = ".sha256"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_TAR_JSON = 4 * 1024 * 1024   # manifest.json and an image config read from an archive


class ImageTransferError(WithSteps, ValueError):
    """An import or export that cannot be done; `code`: needs_person, invalid_value, mismatch, missing_information,
    insufficient_resources, failed. `next` is the plain next step."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


@dataclass(frozen=True)
class ImageSource:
    """One source of a build container image: `kind` (base_image, registry_mirror, llama_cpp, apt, pip, download),
    `label` (what it is, in words an AI may read: never a mirror's address or a path), whether it is `local`, `how` it
    is provided or can be provided (words, no address) and `detail` (the person's view: the host and the locality
    rule's reason; never given to an AI)."""
    kind: str
    label: str
    local: bool
    how: str = ""
    detail: str = ""

    def ai(self) -> Dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "local": self.local, "how": self.how}


@dataclass
class ImagePlan:
    """What an offline build of the build container image needs and whether each source is local. `build_args` and
    `hosts` (--add-host entries: the checked local addresses of every named mirror) go into the build; `llama_copy` is
    the local copy of llama.cpp to stage into the build context."""
    sources: List[ImageSource] = field(default_factory=list)
    build_args: Dict[str, str] = field(default_factory=dict)
    hosts: List[str] = field(default_factory=list)
    llama_copy: Optional[Path] = None
    bases: List[str] = field(default_factory=list)
    # (base image, registry mirror): Docker Hub images that come through a registry mirror of the Docker daemon in the
    # own network; the builder pulls each from that mirror's own address before the build (pull_from_mirror), because
    # the daemon falls back to Docker Hub when a mirror fails.
    mirror_pulls: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(s.local for s in self.sources)

    @property
    def missing(self) -> List[ImageSource]:
        return [s for s in self.sources if not s.local]

    def ai(self) -> Dict[str, Any]:
        """The plan as an AI may read it: kinds, labels, local or not and how; no address, path or detail."""
        return {"ready": self.ready, "sources": [s.ai() for s in self.sources]}

    def lines(self, person: bool = True) -> List[str]:
        """One line per source; `person` adds the host and the locality rule's reason (the CLI and the GUI)."""
        out = []
        for s in self.sources:
            state = "local" if s.local else "NOT local"
            text = f"{s.label}: {state}"
            if s.how:
                text += f" - {s.how}"
            if person and s.detail:
                text += f" ({s.detail})"
            out.append(text)
        return out


# --- the plan -------------------------------------------------------------------------------------------------------

class _AsOffline:
    """The settings with offline mode on: the plan judges every source as an offline build would, also while offline
    mode is off (the CLI and the GUI show what an offline build would need)."""

    def __init__(self, config: Any):
        self._config = config

    def get(self, key: str, default: Any = None) -> Any:
        if key == "offline_mode":
            return True
        get = getattr(self._config, "get", None)
        try:
            return get(key, default) if callable(get) else getattr(self._config, key, default)
        except Exception:  # noqa: BLE001 - an unreadable setting is not set
            return default


def setting(config: Any, key: str) -> str:
    """A build source setting as text ("" when not set or unreadable)."""
    get = getattr(config, "get", None)
    try:
        value = get(key, "") if callable(get) else getattr(config, key, "")
    except Exception:  # noqa: BLE001
        return ""
    return str(value or "").strip()


def mirror_problem(url: str) -> Optional[str]:
    """Why `url` cannot be a build source address (None when it can): only http or https with a host, an optional
    port and a plain path; no user, password, token, query or fragment."""
    from orchestrator.Core.settings_view import url_credential
    text = str(url or "").strip()
    if not text:
        return "it is empty"
    if url_credential(text) or "@" in text:
        return "it carries a user name, password or token, which a build argument would show in the image's history"
    if not MIRROR_URL.fullmatch(text):
        return "it is not a plain http or https address (host, optional port and path; no query)"
    return None


def add_host_entries(pin: Optional[str]) -> List[str]:
    """The --add-host entries of a pin "HOST:PORT:ADDRESS[,ADDRESS...]" (runtime/build/source.offline_reach): the name
    with and without a trailing dot, each checked address; an IPv6 address in buildx's NAME=[ADDRESS] form (the
    legacy builder's form is made from it in Core/builder.py). [] for no pin (an address needs none)."""
    hosts: List[str] = []
    if not pin:
        return hosts
    host, _port, addresses = pin.split(":", 2)
    for name in dict.fromkeys((host, host.rstrip("."))):
        for address in addresses.split(","):
            address = address.strip("[]")
            hosts.append(f"{name}=[{address}]" if ":" in address else f"{name}:{address}")
    return hosts


_REASON_WORDS = {"public": "it is on the internet", "vpn": "it is reached through a VPN that is not your own",
                 "unresolved": "its name could not be resolved", "no_host": "it names no host",
                 "not_checked": "it was not checked", "metadata": "it is a metadata service"}


def _judge_url(url: str, config: Any, what: str) -> Tuple[bool, str, str, List[str]]:
    """(local, words for an AI, the person's detail, --add-host entries) of `url` by the one locality rule with the
    person's opt-outs (source.offline_reach: also the xn-- rule for an international name)."""
    from orchestrator.runtime.build.source import SourceError, offline_reach
    from orchestrator.utils.locality import check_reach, url_host
    try:
        pin = offline_reach(url, _AsOffline(config), what)
    except SourceError as e:
        verdict = check_reach(url, config) if url_host(url)[0] else None
        words = _REASON_WORDS.get(getattr(verdict, "reason", ""), "it is not in your own network")
        return False, words, str(e), []
    host = url_host(url)[0]
    return True, "", f"{host} is in your own network or on this computer", add_host_entries(pin)


def _logical_lines(text: str) -> List[str]:
    from orchestrator.Core.community_pack import _logical_lines as lines
    return lines(text)


def _declares(text: str, name: str) -> bool:
    return re.search(rf"^\s*ARG\s+{re.escape(name)}\b", text, re.MULTILINE | re.IGNORECASE) is not None


def _image_here(client: Any, reference: str) -> bool:
    try:
        client.images.get(reference)
        return True
    except Exception:  # noqa: BLE001 - docker.errors.ImageNotFound, or no answer
        return False


def _daemon_mirrors(client: Any) -> List[str]:
    try:
        info = client.info() or {}
    except Exception:  # noqa: BLE001 - no answer: no mirror known
        return []
    mirrors = ((info.get("RegistryConfig") or {}).get("Mirrors") or []) if isinstance(info, dict) else []
    return [str(m).strip() for m in mirrors if str(m).strip()]


def _base_sources(text: str, config: Any, client: Any, plan: ImagePlan) -> List[ImageSource]:
    from orchestrator.Core.community_pack import dockerfile_bases
    from orchestrator.utils.image_ref import registry_host, split_image
    from orchestrator.utils.locality import check_reach, url_host
    bases = list(dict.fromkeys(dockerfile_bases(text)))
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    syntax = _SYNTAX.match(first)
    if syntax:   # a BuildKit frontend image is pulled like a base image
        bases.insert(0, syntax.group(1))
    plan.bases = bases
    out: List[ImageSource] = []
    hub_missing = False
    for ref in bases:
        label = f"base image {ref}"
        if "$" in ref:
            out.append(ImageSource("base_image", label, False, "the Dockerfile names it through a build argument "
                                   "without a default; write the image out"))
            continue
        if client is None:
            out.append(ImageSource("base_image", label, False, "Docker is not available, so it cannot be checked"))
            continue
        if _image_here(client, ref):
            out.append(ImageSource("base_image", label, True, "on this computer"))
            continue
        registry = split_image(ref)["registry"]
        if registry == "docker.io":
            hub_missing = True
            mirrors = _daemon_mirrors(client)
            local = [m for m in mirrors if check_reach(m, config).local]
            if local:
                plan.mirror_pulls.append((ref, local[0]))
                out.append(ImageSource("base_image", label, True, "pulled through the Docker daemon's registry "
                                       "mirror in your own network"))
                continue
        else:
            verdict = check_reach(url_host(registry_host(ref))[0], config)
            if verdict.local:
                out.append(ImageSource("base_image", label, True, "pulled from the registry in your own network",
                                       verdict.why()))
                continue
        out.append(ImageSource("base_image", label, False,
                               "bring it onto this computer while online or from a file, use a registry or a "
                               "registry mirror in your own network, or import the whole build container image",
                               f"it would be pulled from {registry_host(ref)}"))
    if hub_missing and client is not None:
        for mirror in _daemon_mirrors(client):
            verdict = check_reach(mirror, config)
            if not verdict.local:   # the daemon would reach it for the pull
                out.append(ImageSource("registry_mirror", "a registry mirror of the Docker daemon", False,
                                       "the Docker daemon would pull through it; remove it from the daemon's "
                                       "settings or use one in your own network", verdict.why()))
    return out


def _llama_source(text: str, config: Any, plan: ImagePlan, state_dir: Any, llama_repo: Optional[str],
                  copy_finder) -> Optional[ImageSource]:
    if not re.search(r"^\s*ARG\s+LLAMA_CPP_REPO\b", text, re.MULTILINE):
        return None
    label = "llama.cpp source (the pinned commit)"
    if llama_repo and not usable_override(llama_repo):
        # The builder fetches from upstream (GitHub) instead of an override it cannot use (Core/builder.py
        # _llama_cpp_repo), so the override is not the source of this build.
        return ImageSource("llama_cpp", label, False, "the llama.cpp repository override you set is not usable for "
                           "the image (only an https address without a user name, password, token or query is), so "
                           "the image would fetch llama.cpp from GitHub; set an https mirror in your own network")
    if llama_repo:
        local, words, detail, hosts = _judge_url(llama_repo, config,
                                                 "the llama.cpp repository override (LLAMA_CPP_REPO_OVERRIDE)")
        if local:
            plan.hosts += hosts
            return ImageSource("llama_cpp", label, True, "from the repository mirror you set in your own network",
                               detail)
        return ImageSource("llama_cpp", label, False, f"the repository mirror you set is not local: {words}; set a "
                           "mirror in your own network as the llama.cpp repository override", detail)
    copy = copy_finder(state_dir) if state_dir else None
    if copy is not None and _declares(text, "LLAMA_CPP_SOURCE"):
        plan.llama_copy = copy
        plan.build_args["LLAMA_CPP_SOURCE"] = "context"
        return ImageSource("llama_cpp", label, True, "the verified local copy of the pinned commit on this computer")
    how = ("set a mirror in your own network as the llama.cpp repository override, or import the pinned llama.cpp "
           "source from a file (the local runtime's source bundle)")
    if copy is not None:
        how = "the target's Dockerfile takes no local copy (ARG LLAMA_CPP_SOURCE); " + how
    return ImageSource("llama_cpp", label, False, how, "it would be fetched from the llama.cpp repository on GitHub")


def usable_override(url: str) -> bool:
    """Whether the image build takes `url` as the llama.cpp repository (LLAMA_CPP_REPO), the builder's own rule
    (Core/builder.py LLAMA_REPO_URL, no credential): otherwise the builder warns and fetches from upstream."""
    from orchestrator.Core.builder import LLAMA_REPO_URL
    from orchestrator.Core.settings_view import url_credential
    text = str(url or "")
    return bool(LLAMA_REPO_URL.fullmatch(text)) and not url_credential(text)


def local_llama_copy(state_dir: Any) -> Optional[Path]:
    """The verified local copy of the pinned llama.cpp commit (the local runtime's source store,
    runtime/build/source.cached_source), or None."""
    from orchestrator.runtime.build.source import cached_source
    from orchestrator.utils.llama_pin import pinned_source
    pin = pinned_source()
    if not pin:
        return None
    try:
        return cached_source(SimpleNamespace(commit=pin["commit"]), state_dir)
    except Exception:  # noqa: BLE001 - git missing or the store unreadable: no copy
        return None


def _mirror_source(kind: str, label: str, keys: Iterable[str], text: str, config: Any,
                   plan: ImagePlan) -> ImageSource:
    """apt or pip: local when the person set a mirror (or cache) the Dockerfile takes and it is in the own network."""
    chosen = [(key, setting(config, key)) for key in keys if setting(config, key)]
    names = " or ".join(keys)
    if not chosen:
        return ImageSource(kind, label, False, f"set a mirror or cache in your own network (the person's setting "
                           f"{names}), or import the whole build container image")
    problems = []
    for key, url in chosen:
        arg = SETTING_ARGS[key]
        if not _declares(text, arg):
            problems.append((f"the target's Dockerfile takes no {arg}; its maintainer adds ARG {arg} (see the module "
                             "developer guide)", ""))
            continue
        bad = mirror_problem(url)
        if bad:
            problems.append((f"the setting {key} is not usable: {bad}", ""))
            continue
        local, words, detail, hosts = _judge_url(url, config, f"the {label} you set ({key})")
        if not local:
            problems.append((f"the address you set ({key}) is not local: {words}", detail))
            continue
        plan.hosts += hosts
        plan.build_args[arg] = url
        if arg == "PIP_INDEX_URL" and url.lower().startswith("http://"):
            from urllib.parse import urlsplit
            plan.build_args["PIP_TRUSTED_HOST"] = urlsplit(url).netloc
        return ImageSource(kind, label, True, f"through the mirror or cache you set ({key})", detail)
    how, detail = problems[0]
    return ImageSource(kind, label, False, how, detail)


def _download_sources(text: str, config: Any) -> Tuple[List[ImageSource], bool]:
    """Every other address the Dockerfile fetches from, one source per host; and whether it has a pip location
    (an ARG ..._PIP_URL) that the pip index replaces."""
    from orchestrator.utils.locality import check_reach
    hosts: Dict[str, List[str]] = {}
    pip_urls = False
    for line in _logical_lines(text):
        word, _, rest = line.partition(" ")
        word = word.upper()
        if word == "ARG":
            name, eq, default = rest.strip().partition("=")
            name = name.strip()
            if not eq or name == "LLAMA_CPP_REPO":
                continue
            if name.endswith("_PIP_URL") and _URL.search(default):
                pip_urls = True
                continue
            found = _URL.findall(default)
        elif word in ("RUN", "ADD"):
            # ${PIP_INDEX_URL:-...} and the like: the fallback belongs to that source (apt, pip), judged there
            found = _URL.findall(_PROVIDED.sub(lambda m: "" if m.group(1) in SOURCE_ARGS else m.group(0), rest))
        else:
            continue
        for url in found:
            host = _fetch_host(url)
            if host and "$" not in host:
                hosts.setdefault(host, []).append(url)
    out = []
    for host in hosts:
        verdict = check_reach(host, config)
        out.append(ImageSource("download", f"downloads from {host}", verdict.local,
                               "in your own network" if verdict.local else
                               "the Dockerfile fetches from this address at build time; put it in your own network "
                               "or import the whole build container image", verdict.why()))
    return out, pip_urls


def _fetch_host(url: str) -> str:
    """The host a fetch of `url` goes to (an scp-like user@host:path too); "" for a local file."""
    from orchestrator.utils.locality import url_host
    if "://" not in url:
        return url.split("@", 1)[1].split(":", 1)[0] if "@" in url else ""
    if url.lower().startswith("file:"):
        return ""
    return url_host(url)[0]


def _other_package_sources(body: str) -> List[ImageSource]:
    """One source per package manager other than apt and pip that the Dockerfile runs: never local (no mirror setting
    exists for it)."""
    found = dict.fromkeys(m.group(0).split()[0] for m in _OTHER_PACKAGES.finditer(body))
    return [ImageSource("download", f"packages through {name}", False,
                        "an offline build fetches packages only through apt and pip mirrors you set; this package "
                        "manager would fetch from the internet: import the whole build container image, or build it "
                        "once with offline mode off", "") for name in found]


def offline_plan(text: str, config: Any, client: Any, *, state_dir: Any = None, llama_repo: Optional[str] = None,
                 copy_finder=None) -> ImagePlan:
    """The plan of an offline build of the build container image from the Dockerfile `text`: every source it needs
    and whether it is local by the one locality rule (the person's opt-outs in `config` count). `client`: the docker-py
    client (None: base images cannot be checked); `state_dir`: the per-user state folder (the local llama.cpp copy);
    `llama_repo`: the LLAMA_CPP_REPO_OVERRIDE the build uses. Names are resolved through the system resolver (never on
    the GUI thread: the GUI asks it in a worker). Nothing is pulled, fetched or built."""
    plan = ImagePlan()
    plan.sources += _base_sources(text, config, client, plan)
    llama = _llama_source(text, config, plan, state_dir, llama_repo, copy_finder or local_llama_copy)
    if llama is not None:
        plan.sources.append(llama)
    body = "\n".join(_logical_lines(text))
    if _APT.search(body):
        plan.sources.append(_mirror_source("apt", "apt packages", (APT_MIRROR, APT_PROXY), text, config, plan))
    pip = None
    if _PIP.search(body):
        pip = _mirror_source("pip", "pip packages", (PIP_INDEX,), text, config, plan)
    downloads, pip_urls = _download_sources(text, config)
    if pip_urls and pip is None:
        pip = _mirror_source("pip", "pip packages", (PIP_INDEX,), text, config, plan)
    if pip is not None:
        plan.sources.append(pip)
    plan.sources += downloads
    plan.sources += _other_package_sources(body)
    plan.hosts = list(dict.fromkeys(plan.hosts))
    return plan


def mirror_reference(ref: str, mirror: str) -> Tuple[str, str]:
    """(repository, tag or digest) of the Docker Hub image `ref` at the registry mirror `mirror` (its own address,
    e.g. 192.0.2.30:5000/library/debian and bookworm-slim)."""
    from urllib.parse import urlsplit

    from orchestrator.utils.image_ref import split_image
    parts = split_image(ref)
    netloc = urlsplit(mirror if "://" in mirror else f"https://{mirror}").netloc
    repository = parts["repository"] if "/" in parts["repository"] else f"library/{parts['repository']}"
    return f"{netloc}/{repository}", parts["digest"] or parts["tag"] or "latest"


def pull_from_mirror(client: Any, ref: str, mirror: str) -> None:
    """Brings the Docker Hub image `ref` onto this computer from the registry mirror `mirror` in the own network
    (its own address, so the Docker daemon cannot fall back to Docker Hub on the internet as it does for a failing
    registry mirror), named `ref` as a pull through the mirror would name it. An image pinned by digest is pulled by
    that digest from the mirror first and then by its own name through the daemon, which the mirror now serves.
    Raises the engine's error when the mirror does not provide it."""
    from orchestrator.utils.image_ref import split_image
    repository, tag = mirror_reference(ref, mirror)
    image = client.images.pull(repository, tag=tag)
    parts = split_image(ref)
    if parts["digest"]:
        client.images.pull(ref)
        return
    name = ref.rsplit(":", 1)[0] if parts["tag"] else ref
    image.tag(name, parts["tag"] or "latest")


def stop_text(target: str, plan: ImagePlan) -> str:
    """The plain stop of an offline build whose build container image is not on this computer and cannot be built
    offline: each source that is not local and how to provide it, in words an AI may read (no address, no path)."""
    missing = plan.missing
    numbered = "; ".join(f"({n}) {s.label}: {s.how}" for n, s in enumerate(missing, 1))
    bases = ", ".join(plan.bases) or "its base image"
    return (f"Offline mode is on, and the image of the target {target} for these settings is not on this computer. "
            f"Building it offline needs every source on this computer or in your own network, and "
            f"{len(missing)} {'is' if len(missing) == 1 else 'are'} not: {numbered}. Its base images: {bases}. "
            "Provide what is missing and start the build again, or import a build container image exported on "
            "another computer. Start this build once with offline mode off; later builds with the same settings use "
            "that image offline. An image built before the framework also recorded the modules and scripts copied "
            "into it is not reused (it may hold older copies of them).")


def stage_llama_copy(copy: Path, context: Path) -> Path:
    """Copies the local llama.cpp copy (with its .git, so the Dockerfile checks the commit) into the build context's
    sources/ folder; the copy is writable (the source store's tree is read-only), so the build folder can be cleaned."""
    import os
    target = Path(context) / CONTEXT_SOURCES / LLAMA_COPY
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(copy, target, symlinks=True, copy_function=shutil.copyfile)
    for root, dirs, files in os.walk(target):
        for name in dirs:
            os.chmod(Path(root) / name, 0o755)
        for name in files:
            path = Path(root) / name
            if not path.is_symlink():
                os.chmod(path, 0o644)
    os.chmod(target, 0o755)
    return target


# --- the framework's view (CLI, GUI, MCP) ----------------------------------------------------------------------------

def _engine(framework: Any):
    orchestrator = getattr(framework, "orchestrator", None)
    engine = getattr(orchestrator, "build_engine", None)
    if engine is None:
        raise ImageTransferError("The framework's build engine is not available.", "failed",
                                 "Start the framework fully (the health check shows why it is not).")
    return engine


def docker_client(framework: Any, engine: Any = None):
    client = getattr(engine, "docker_client", None) if engine is not None else None
    if client is None and hasattr(framework, "get_component"):
        client = framework.get_component("docker_client")
    return client


def target_dockerfile(engine: Any, target: str, gpu: bool = False) -> Tuple[Path, Path]:
    """(target folder, Dockerfile) of `target` as a build uses them."""
    from orchestrator.utils.build_context import find_dockerfile
    folder = engine.resolve_target_dir(target)
    if folder is None:
        raise ImageTransferError(f"There is no target {target}.", "invalid_value", "List the targets and pick one.")
    return folder, find_dockerfile(folder, "Dockerfile.gpu" if gpu else "Dockerfile")


def target_plan(framework: Any, target: str, *, gpu: bool = False) -> ImagePlan:
    """offline_plan() for an installed target as a build here would make it (its Dockerfile, the person's settings,
    the llama.cpp override, the local copy, the Docker client)."""
    engine = _engine(framework)
    _folder, dockerfile = target_dockerfile(engine, target, gpu)
    config = getattr(framework, "config", None) or getattr(engine, "config", None)
    overrides = engine._repo_overrides() if hasattr(engine, "_repo_overrides") else {}
    text = dockerfile.read_text(encoding="utf-8", errors="replace")
    return offline_plan(text, config, docker_client(framework, engine),
                        state_dir=getattr(config, "user_state_dir", None),
                        llama_repo=overrides.get("LLAMA_CPP_REPO_OVERRIDE") or None)


@dataclass(frozen=True)
class ImageInfo:
    """A build container image on this computer: its id, tags, target, inputs label, size and creation time."""
    image_id: str
    tags: Tuple[str, ...]
    target: str
    inputs: str
    size: int
    created: str


def _info(image: Any) -> ImageInfo:
    from orchestrator.Core.builder import IMAGE_LABEL, INPUTS_LABEL
    attrs = getattr(image, "attrs", None) or {}
    labels = getattr(image, "labels", None) or ((attrs.get("Config") or {}).get("Labels") or {})
    return ImageInfo(str(getattr(image, "id", "") or attrs.get("Id") or ""), tuple(getattr(image, "tags", ()) or ()),
                     str(labels.get(IMAGE_LABEL) or ""), str(labels.get(INPUTS_LABEL) or ""),
                     int(attrs.get("Size") or 0), str(attrs.get("Created") or ""))


def target_images(client: Any, target: Optional[str] = None) -> List[ImageInfo]:
    """The build container images on this computer (IMAGE_LABEL), of one target or of all, newest first."""
    from orchestrator.Core.builder import IMAGE_LABEL
    if client is None:
        return []
    label = f"{IMAGE_LABEL}={target}" if target else IMAGE_LABEL
    try:
        found = list(client.images.list(filters={"label": [label]}) or [])
    except Exception:  # noqa: BLE001 - no answer from the engine
        return []
    return sorted((_info(i) for i in found), key=lambda i: i.created, reverse=True)


def status(framework: Any, target: str, *, gpu: bool = False) -> Dict[str, Any]:
    """The read-only status an AI may read (MCP get_target_capabilities with check_image): how many build container
    images of the target are here, whether an offline build of one could run (each source by its label) and that import
    and export are the person's."""
    engine = _engine(framework)
    plan = target_plan(framework, target, gpu=gpu)
    return {"images_here": len(target_images(docker_client(framework, engine), target)), **plan.ai(),
            "transfer": "needs_person: only the person imports or exports a build container image"}


# --- import and export ----------------------------------------------------------------------------------------------

def _person_only(actor: str, what: str) -> None:
    if actor not in PERSON_ACTORS:
        raise ImageTransferError(f"Only the person {what} a build container image, in the framework's window or on "
                                 "its command line.", "needs_person",
                                 "Tell the person; they choose the file and confirm it.")


def sidecar_path(file: Path) -> Path:
    return Path(str(file) + SIDECAR_SUFFIX)


def read_sidecar(file: Path) -> Optional[str]:
    """The sha256 in the sidecar written by export_image ("<sha256>  <name>", sha256sum's format), or None."""
    path = sidecar_path(file)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return None
    word = (text.split() or [""])[0].lower()
    return word if SHA256.fullmatch(word) else None


def _room(folder: Path, needed: int, what: str) -> None:
    from orchestrator.Core.disk_plan import refusal, room
    try:
        check = room(folder, needed, label="target", what=what)
    except OSError:
        return
    refused = refusal([check], what)
    if refused is not None:
        raise ImageTransferError(str(refused), "insufficient_resources", refused.next)


@dataclass(frozen=True)
class ExportResult:
    file: Path
    sidecar: Path
    sha256: str
    bytes: int
    image: ImageInfo


def export_image(framework: Any, target: str, dest: Path, *, actor: str, image: Optional[str] = None,
                 client: Any = None) -> ExportResult:
    """Saves the newest build container image of `target` (or the one named `image`) into the file `dest` (docker
    save, utils/docker_engine.save_image) and writes its sha256 sidecar next to it, for another offline computer.
    Person-only; the one disk rule at the destination; an existing file is never replaced."""
    from orchestrator.utils.atomic import remove_file
    from orchestrator.utils.docker_engine import save_image
    _person_only(actor, "exports")
    client = client or docker_client(framework, getattr(getattr(framework, "orchestrator", None), "build_engine", None))
    if client is None:
        raise ImageTransferError("Docker is not available on this computer.", "failed", "Start Docker.")
    found = target_images(client, target)
    if image:
        found = [i for i in found if image in i.tags or i.image_id.startswith(image) or i.image_id == image]
    if not found:
        raise ImageTransferError(f"There is no build container image of the target {target} on this computer.",
                                 "missing_information", "Build the target once (online, or offline with every "
                                 "source local), or import its image.")
    info = found[0]
    dest = Path(dest).expanduser()
    if dest.exists() or sidecar_path(dest).exists():
        raise ImageTransferError("The file or its sha256 file exists already; nothing is replaced.", "invalid_value",
                                 "Choose another file name.")
    if not dest.parent.is_dir():
        raise ImageTransferError("The folder of the file does not exist.", "invalid_value",
                                 "Choose an existing folder.")
    _room(dest.parent, info.size, "the export of the build container image")
    partial = dest.with_name(dest.name + ".partial")
    try:
        digest = save_image(client.images.get(info.image_id or info.tags[0]), partial)
        partial.replace(dest)
    except BaseException:
        remove_file(partial)
        raise
    sidecar = sidecar_path(dest)
    sidecar.write_text(f"{digest}  {dest.name}\n", encoding="utf-8")
    return ExportResult(dest, sidecar, digest, dest.stat().st_size, info)


def archive_labels(file: Any) -> Dict[str, str]:
    """The labels of the image in a docker save archive (manifest.json -> its config's Labels), read without loading
    it. ImageTransferError when the file is no such archive or holds more than one image. `file`: a path or an open
    binary file (read from its start)."""
    return _archive(file)[0]


def _archive(file: Any) -> Tuple[Dict[str, str], List[str]]:
    """(labels, names) of the one image in a docker save archive: the labels of its config and every name a load
    would give it (manifest.json RepoTags, the OCI index's image names). An OCI index.json (Docker 25 and later, which a
    load with the containerd image store reads) must name the same image config as manifest.json: a load never brings
    another image than the one checked here."""
    try:
        if isinstance(file, (str, Path)):
            tar = tarfile.open(file, "r:*")
        else:
            file.seek(0)
            tar = tarfile.open(fileobj=file, mode="r:*")
        with tar:
            manifest = _tar_json(tar, "manifest.json")
            if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
                raise ImageTransferError("The file must hold exactly one image (a docker save of one build container "
                                         "image).", "invalid_value", "Export the image again with the framework.")
            config_name = str(manifest[0].get("Config") or "")
            config = _tar_json(tar, config_name)
            names = [str(t) for t in (manifest[0].get("RepoTags") or []) if isinstance(t, str)]
            if "index.json" in tar.getnames():
                names += _index_names(tar, config_name)
    except (tarfile.TarError, OSError, ValueError) as e:
        if isinstance(e, ImageTransferError):
            raise
        raise ImageTransferError(f"The file is not an image archive: {e}", "invalid_value",
                                 "Choose the file the export wrote.") from None
    labels = ((config or {}).get("config") or {}).get("Labels") or {} if isinstance(config, dict) else {}
    labels = {str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {}
    return labels, names


def _index_names(tar: tarfile.TarFile, config_name: str) -> List[str]:
    """The image names of an archive's OCI index.json; ValueError unless every image it lists has the config
    `config_name` of manifest.json (blobs/sha256/<digest>)."""
    index = _tar_json(tar, "index.json")
    entries = (index or {}).get("manifests") if isinstance(index, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("index.json lists no image")
    names: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("index.json is not an image index")
        notes = entry.get("annotations") or {}
        if isinstance(notes, dict):
            names += [str(notes[k]) for k in ("io.containerd.image.name", "org.opencontainers.image.ref.name")
                      if isinstance(notes.get(k), str) and ("/" in notes[k] or ":" in notes[k])]
        blob = _tar_json(tar, _blob_name(entry.get("digest")))
        if isinstance(blob, dict) and isinstance(blob.get("manifests"), list):   # a nested index (one platform)
            inner = [m for m in blob["manifests"] if isinstance(m, dict)]
            if len(inner) != 1:
                raise ValueError("index.json lists more than one image")
            blob = _tar_json(tar, _blob_name(inner[0].get("digest")))
        config = (blob or {}).get("config") if isinstance(blob, dict) else None
        if not isinstance(config, dict) or _blob_name(config.get("digest")) != config_name:
            raise ValueError("index.json names another image than manifest.json")
    return names


def _blob_name(digest: Any) -> str:
    algorithm, _, value = str(digest or "").partition(":")
    if algorithm != "sha256" or not SHA256.fullmatch(value):
        raise ValueError("an image index entry has no sha256 digest")
    return f"blobs/sha256/{value}"


def _tar_json(tar: tarfile.TarFile, name: str) -> Any:
    try:
        member = tar.getmember(name)
    except KeyError:
        raise ValueError(f"{name or 'the image config'} is missing") from None
    if not member.isfile() or member.size > MAX_TAR_JSON:
        raise ValueError(f"{name} is not a small file")
    handle = tar.extractfile(member)
    return json.loads(handle.read().decode("utf-8")) if handle else None


def expected_labels(framework: Any, target: str, *, gpu: bool = False) -> Dict[str, str]:
    """The labels a build of `target` here looks for (IMAGE_LABEL and INPUTS_LABEL with the default build options and
    this computer's settings: the same computation as the build, BuildEngine.image_labels, on a build context staged
    as a build stages it (utils/build_context.stage_build_context: the inputs cover the files it copies, the
    framework's entrypoint included) and with the target's digest as a build records it (installed_digest))."""
    import tempfile
    from orchestrator.Core.builder import BuildConfiguration, BuildProgress, BuildStatus, ModelFormat
    from orchestrator.Core.community_manager import installed_digest
    from orchestrator.utils.build_context import stage_build_context
    engine = _engine(framework)
    folder, dockerfile = target_dockerfile(engine, target, gpu)
    config = BuildConfiguration(build_id="image-check", timestamp="", model_source="-", target_arch=target,
                                target_format=ModelFormat.GGUF, output_dir="-", use_gpu=gpu)
    config.target_pack_sha256 = installed_digest(folder)
    with tempfile.TemporaryDirectory(prefix="llmcf-image-check-") as scratch:
        staged = stage_build_context(folder, engine.base_dir, Path(scratch) / "context", dockerfile.name)
        return engine.image_labels(config, BuildProgress("image-check", BuildStatus.QUEUED, ""), staged)[1]


@dataclass(frozen=True)
class ImportResult:
    target: str
    inputs: str
    sha256: str
    images: Tuple[str, ...]


def import_image(framework: Any, file: Path, *, actor: str, sha256: Optional[str] = None, target: Optional[str] = None,
                 client: Any = None, expected: Optional[Dict[str, str]] = None) -> ImportResult:
    """Loads a build container image from a file the person brought (docker load or podman load through the engine's
    API) after checking it: its sha256 against the one the person gives or the sidecar export_image wrote, its labels
    against those a build of its target here looks for (`expected`, else expected_labels). Any mismatch refuses
    before anything is loaded. Person-only; the one disk rule in Docker's image folder when it is visible here."""
    from orchestrator.Core.builder import IMAGE_LABEL, INPUTS_LABEL, TARGET_ID
    from orchestrator.utils.helpers import sha256_fileobj
    _person_only(actor, "imports")
    file = Path(file).expanduser()
    if not file.is_file():
        raise ImageTransferError("The file does not exist.", "invalid_value", "Choose the exported image file.")
    wanted = (sha256 or "").strip().lower() or read_sidecar(file)
    if not wanted:
        raise ImageTransferError("No sha256 to check the file against: give it, or keep the .sha256 file the export "
                                 "wrote next to the image file.", "missing_information",
                                 "Ask the person for the sha256 of the file.")
    if not SHA256.fullmatch(wanted):
        raise ImageTransferError("The sha256 must be 64 hexadecimal characters.", "invalid_value",
                                 "Give the sha256 the export wrote.")
    # One open file for the digest, the labels and the load: the file that is loaded is the file that was checked
    # (a file swapped in under the same name between the check and the load is never loaded).
    with open(file, "rb") as handle:
        actual = sha256_fileobj(handle)
        if actual != wanted:
            raise ImageTransferError(f"The file does not match its sha256 (expected {wanted[:12]}..., the file has "
                                     f"{actual[:12]}...): it is damaged or another file; nothing was loaded.",
                                     "mismatch", "Copy the file again from the computer that exported it.")
        labels, names = _archive(handle)
        found_target, inputs = labels.get(IMAGE_LABEL, ""), labels.get(INPUTS_LABEL, "")
        if not TARGET_ID.fullmatch(found_target) or not SHA256.fullmatch(inputs):
            raise ImageTransferError("The image carries no build container image labels of the framework "
                                     f"({IMAGE_LABEL}, {INPUTS_LABEL}); nothing was loaded.", "mismatch",
                                     "Export a build container image with the framework.")
        if target and found_target.lower() != target.lower():
            raise ImageTransferError(f"The image is for the target {found_target}, not {target}; nothing was loaded.",
                                     "mismatch", "Choose the image of this target.")
        # The names a load gives the image: only the framework's own names of this target (llm-framework/<target>:...),
        # so an import never takes over the name of a base image or of another target's image.
        prefix = f"llm-framework/{found_target.lower()}:"
        foreign = [n for n in names if not n.lower().removeprefix("docker.io/").startswith(prefix)]
        if foreign:
            raise ImageTransferError(f"The image would also be named {', '.join(foreign[:3])}, which is not a name of "
                                     f"the framework's build container images of {found_target}; nothing was loaded.",
                                     "mismatch", "Export the image again with the framework.")
        want = expected if expected is not None else expected_labels(framework, found_target)
        if want.get(INPUTS_LABEL) != inputs or str(want.get(IMAGE_LABEL, found_target)).lower() != found_target.lower():
            raise ImageTransferError(
                f"The image of {found_target} was built from other settings than a build here uses (another target "
                "folder, Dockerfile, llama.cpp pin or build host CPU features), so a build here would not use it; "
                "nothing was loaded.", "mismatch",
                "Export it again from a computer with the same target version and settings, or build it here.")
        client = client or docker_client(framework, getattr(getattr(framework, "orchestrator", None), "build_engine",
                                                            None))
        if client is None:
            raise ImageTransferError("Docker is not available on this computer.", "failed", "Start Docker.")
        _docker_room(client, file.stat().st_size)
        handle.seek(0)
        loaded = client.images.load(handle)
    tags = tuple(t for i in (loaded or []) for t in (getattr(i, "tags", None) or ()))
    return ImportResult(found_target, inputs, actual, tags)


def _docker_room(client: Any, needed: int) -> None:
    import os
    try:
        root = str((client.info() or {}).get("DockerRootDir") or "")
    except Exception:  # noqa: BLE001
        return
    if root and os.path.isabs(root) and Path(root).exists():
        _room(Path(root), needed, "the import of the build container image")


__all__ = ["CONTEXT_SOURCES", "ExportResult", "ImageInfo", "ImagePlan", "ImageSource", "ImageTransferError",
           "ImportResult", "SOURCE_ARGS", "add_host_entries", "archive_labels", "expected_labels", "export_image",
           "import_image", "local_llama_copy", "mirror_problem", "mirror_reference", "offline_plan",
           "pull_from_mirror", "read_sidecar", "sidecar_path", "usable_override",
           "stage_llama_copy", "status", "stop_text", "target_dockerfile", "target_images", "target_plan"]
