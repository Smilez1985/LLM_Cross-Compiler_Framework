#!/usr/bin/env python3
"""
The distribution packages of a build container image (docs/DESIGN.md, Installer, platforms and dependencies: the
build's preload also carries them), read from the target's Dockerfile, the one place the image build runs; never from
a second list that could drift from it.

- stages(): per build stage its base image (a stage built on another stage has that one's base) and the packages its
  RUN lines install with `apt-get install` or `apt install`: the words after the options (`-y`, `-q`,
  `--no-install-recommends`, `-o` with its value), NAME or NAME=VERSION as the Dockerfile pins one. A shell variable
  set in the same RUN line is followed, also in an `if [ A = B ]` branch whose sides are known (an ARG's or ENV's
  default, "$(dpkg --print-architecture)" as the build computer's architecture), which is evaluated; so is an ARG's
  or ENV's default in a package word. Whatever cannot be read for
  certain (a `case`, a loop, an `elif`, a command substitution, a glob, an option such as `-t`, a variable without a
  known value, `apt-get upgrade`, a package source the Dockerfile adds itself) is a problem, never a guess: the
  preload then names the apt packages as not covered.
- need(): the stages that install packages, for the build computer's architecture, and their key (the folder the
  taken-in packages are kept in).
- base_facts(): what a base image on this computer is (os-release) and has (its dpkg status), read from the image the
  engine saves into a temporary file, without running it (scripts/image_files.read_saved).
"""

import hashlib
import json
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# dpkg's names of the build computers' architectures (the image is built for the computer that builds it)
DPKG_ARCHES = {"linux/amd64": "amd64", "linux/arm64": "arm64"}
DOCKER_ARCHES = {"amd64": "amd64", "arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}
PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,63}(?:=[0-9][A-Za-z0-9.+~:-]{0,63})?$", re.ASCII)
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_ARCH_TEST = "$(dpkg --print-architecture)"
# options of apt-get install that change nothing about which packages come (-o takes a value)
_QUIET_OPTIONS = {"-y", "--yes", "--assume-yes", "-q", "-qq", "--quiet", "-yq", "-qy", "-yqq", "-qqy",
                  "--no-install-recommends", "--no-install-suggests"}
_SEPARATORS = {";", "&&", "||", "|", ";;", "&", "(", ")", "\n"}
_UNKNOWN = object()


@dataclass
class Stage:
    """One build stage: its name (AS), its base image (resolved through earlier stages), the packages it installs
    with apt in order, and what could not be read for certain."""
    name: str
    image: str
    packages: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)


def _lines(text: str) -> List[str]:
    from orchestrator.Core.community_pack import _logical_lines
    return _logical_lines(text)


def _expand(word: str, values: Dict[str, Any]) -> Any:
    """`word` with $NAME and ${NAME} filled in from `values`; _UNKNOWN when one is not known or a command
    substitution is in it."""
    if "$(" in word or "`" in word or re.search(r"\$\{[^}]*[:#%/]", word):
        return _UNKNOWN
    unknown = []

    def fill(match):
        name = match.group(1) or match.group(2)
        value = values.get(name, _UNKNOWN)
        if value is _UNKNOWN:
            unknown.append(name)
            return ""
        return str(value)
    out = _VAR.sub(fill, word)
    return _UNKNOWN if unknown or "$" in out else out


def _segments(run: str) -> List[List[str]]:
    """The simple commands of a RUN line as token lists (quotes removed, separators dropped)."""
    lexer = shlex.shlex(run, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    out, current = [], []
    for token in lexer:
        if token in _SEPARATORS:
            if current:
                out.append(current)
            current = []
        else:
            current.append(token)
    if current:
        out.append(current)
    return out


def _condition(tokens: List[str], arch: str, values: Dict[str, Any]) -> Optional[bool]:
    """An `if` test this reader can evaluate: [ A =|!= B ], each side a word whose variables are known, where
    "$(dpkg --print-architecture)" is the build computer's architecture (also inside a word, as in
    "$(dpkg --print-architecture):${GNU_TRIPLET}"); None otherwise."""
    if len(tokens) == 5 and tokens[0] == "[" and tokens[-1] == "]" and tokens[2] in ("=", "==", "!="):
        left, right = (_expand(word.replace(_ARCH_TEST, arch), values) for word in (tokens[1], tokens[3]))
        if left is _UNKNOWN or right is _UNKNOWN:
            return None
        same = left == right
        return same if tokens[2] != "!=" else not same
    return None


def _adds_source(tokens: List[str]) -> bool:
    if any(t in ("add-apt-repository", "apt-key", "extrepo") for t in tokens):
        return True
    if any("signed-by=" in t.lower() for t in tokens):
        return True
    for n, token in enumerate(tokens):
        nxt = tokens[n + 1] if n + 1 < len(tokens) else ""
        if token in (">", ">>") or token.startswith(">"):
            target = nxt if token in (">", ">>") else token.lstrip(">")
            if target.startswith(("/etc/apt/sources.list", "/etc/apt/keyrings", "/etc/apt/trusted.gpg")):
                return True
        if token == "tee" and nxt.startswith(("/etc/apt/sources.list", "/etc/apt/keyrings")):
            return True
    return False


def state_of(stack: List[Optional[bool]]) -> Optional[bool]:
    """Whether the commands under the open tests run: False when one is false, None when one cannot be told."""
    return False if False in stack else (None if None in stack else True)


def _run_packages(run: str, arch: str, values: Dict[str, Any], stage: Stage) -> None:
    """The packages one RUN line installs with apt for `arch`, into `stage` (its problems too)."""
    local = dict(values)
    stack: List[Optional[bool]] = []   # one entry per open if/case/loop: True, False or None (cannot tell)
    for tokens in _segments(run):
        while tokens and tokens[0] in ("then", "else", "do", "{", "}", "!"):
            if tokens[0] == "else" and stack:
                stack[-1] = None if stack[-1] is None else not stack[-1]
            tokens = tokens[1:]
        if not tokens:
            continue
        head = tokens[0]
        if head == "if":
            known = state_of(stack)
            stack.append(_condition(tokens[1:], arch, local) if known is not None else None)
            continue
        if head == "elif":
            if stack:
                stack[-1] = None
            continue
        if head in ("case", "for", "while", "until", "select"):
            stack.append(None)   # what runs inside is not known: an install there, or a variable set there, is a problem
            continue
        if head in ("fi", "done", "esac"):
            if stack:
                stack.pop()
            continue
        state = state_of(stack)
        if _adds_source(tokens):
            stage.problems.append("the Dockerfile adds a package source of its own")
            continue
        assignments = [t for t in tokens if _ASSIGN.match(t)]
        if len(assignments) == len(tokens):   # NAME=VALUE ...: shell variables of this RUN line
            for token in tokens:
                name, value = _ASSIGN.match(token).groups()
                if state is False:
                    continue
                local[name] = _expand(value, local) if state else _UNKNOWN
            continue
        while tokens and _ASSIGN.match(tokens[0]):   # NAME=VALUE command: only the command's environment
            tokens = tokens[1:]
        if not tokens or tokens[0] not in ("apt-get", "apt"):
            continue
        rest = tokens[1:]
        options, words, sub = [], [], ""
        n = 0
        while n < len(rest):
            token = rest[n]
            if token in ("-o", "--option"):
                n += 2
                continue
            if token.startswith("-"):
                if not token.startswith("-o"):
                    options.append(token)
            elif not sub:
                sub = token
            else:
                words.append(token)
            n += 1
        if sub in ("update", "clean", "autoclean", "autoremove", "purge", "remove", ""):
            continue
        if sub != "install":
            if state is not False:
                stage.problems.append(f"`{tokens[0]} {sub}` changes packages a preload cannot bring")
            continue
        if state is False:
            continue
        if state is None:
            stage.problems.append("an install runs under a condition this reader cannot evaluate")
            continue
        odd = [o for o in options if o not in _QUIET_OPTIONS]
        if odd:
            stage.problems.append(f"the install takes the option {odd[0]}")
            continue
        for word in words:
            value = _expand(word, local)
            if value is _UNKNOWN:
                stage.problems.append(f"the install names {word}, whose value is not known before the build")
                continue
            for package in str(value).split():
                if not PACKAGE.fullmatch(package):
                    stage.problems.append(f"{package} is not a package name by Debian's rules")
                elif package not in stage.packages:
                    if any(p.split("=", 1)[0] == package.split("=", 1)[0] for p in stage.packages):
                        stage.problems.append(f"{package.split('=', 1)[0]} twice with other versions")
                    else:
                        stage.packages.append(package)


def stages(text: str, arch: str) -> List[Stage]:
    """Every build stage of the Dockerfile `text` with the apt packages it installs on a build computer of the dpkg
    architecture `arch`."""
    out: List[Stage] = []
    by_name: Dict[str, Stage] = {}
    global_args: Dict[str, Any] = {}
    values: Dict[str, Any] = {}
    stage: Optional[Stage] = None
    for line in _lines(text):
        word, _, rest = line.partition(" ")
        word, rest = word.upper(), rest.strip()
        if word == "ARG":
            for item in shlex.split(rest) if rest else []:
                name, eq, default = item.partition("=")
                if stage is None:
                    global_args[name] = default if eq else _UNKNOWN
                else:
                    values[name] = default if eq else global_args.get(name, _UNKNOWN)
            continue
        if word == "FROM":
            tokens = [t for t in rest.split() if not t.startswith("--")]
            image = tokens[0] if tokens else ""
            image = _expand(image, global_args)
            name = tokens[2].lower() if len(tokens) >= 3 and tokens[1].upper() == "AS" else str(len(out))
            parent = by_name.get(str(image).lower()) if image is not _UNKNOWN else None
            base = parent.image if parent else ("" if image is _UNKNOWN else str(image))
            stage = Stage(name, base)
            if image is _UNKNOWN:
                stage.problems.append("the stage's base image is named through a build argument without a default")
            out.append(stage)
            by_name[name] = stage
            values = {}
            continue
        if stage is None:
            continue
        if word == "ENV":
            try:
                items = shlex.split(rest)
            except ValueError:
                continue
            if items and "=" not in items[0] and len(items) >= 2:   # ENV NAME VALUE
                values[items[0]] = " ".join(items[1:])
            else:
                for item in items:
                    name, eq, value = item.partition("=")
                    if eq:
                        values[name] = _expand(value, values)
            continue
        if word == "RUN":
            body = re.sub(r"^(?:--\S+\s+)*", "", rest)
            try:
                _run_packages(body, arch, values, stage)
            except ValueError as error:   # unbalanced quotes
                stage.problems.append(f"a RUN line could not be read ({error})")
    return out


def need(text: str, arch: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """([{"name", "image", "packages"}] of the stages that install packages with apt, problems): the problems name
    what could not be read for certain; with any, a preload does not carry the packages."""
    found, problems = [], []
    for stage in stages(text, arch):
        problems += [f"stage {stage.name}: {p}" for p in stage.problems]
        if stage.packages:
            found.append({"name": stage.name, "image": stage.image, "packages": list(stage.packages)})
    return found, problems


def need_key(found: List[Dict[str, Any]], arch: str) -> str:
    """The key of a package need (the folder its taken-in packages are kept in): the stages' images and packages and
    the architecture."""
    body = json.dumps({"arch": arch, "stages": [{"image": s["image"], "packages": s["packages"]} for s in found]},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def dpkg_arch(platform: str) -> str:
    return DPKG_ARCHES.get(platform, "")


def base_facts(client: Any, ref: str, arch: str) -> Optional[Dict[str, Any]]:
    """{"distribution", "release", "installed"} of the base image `ref` on this computer, read from its saved layers
    without running it; None when it is not here, is for another architecture or cannot be read. "installed" is None
    when its dpkg status cannot be read."""
    from orchestrator.Core.build_preload import deb_preload, reader
    if client is None or not ref:
        return None
    try:
        image = client.images.get(ref)
    except Exception:  # noqa: BLE001 - not here, or no answer
        return None
    found = DOCKER_ARCHES.get(str((getattr(image, "attrs", None) or {}).get("Architecture") or ""), "")
    if found and found != arch:
        return None
    images, deb = reader(), deb_preload()
    try:
        with tempfile.TemporaryDirectory(prefix="llmcf-base-") as work:
            path = Path(work) / "image.tar"
            with open(path, "wb") as sink:
                for chunk in image.save(named=False):
                    sink.write(chunk)
            read = images.read_saved(path, ["usr/lib/os-release", "etc/os-release", "var/lib/dpkg/status"])
    except Exception:  # noqa: BLE001 - the image reader's or the engine's errors: not readable
        return None
    facts = deb.parse_os_release((read["usr/lib/os-release"] or read["etc/os-release"] or b"").decode("utf-8",
                                                                                                    "replace"))
    if not facts.get("ID") or not facts.get("VERSION_CODENAME"):
        return None
    status = read["var/lib/dpkg/status"]
    return {"distribution": facts["ID"], "release": facts["VERSION_CODENAME"],
            "installed": deb.installed_from_status(status.decode("utf-8", "replace")) if status is not None else None}


__all__ = ["Stage", "base_facts", "dpkg_arch", "need", "need_key", "stages"]
