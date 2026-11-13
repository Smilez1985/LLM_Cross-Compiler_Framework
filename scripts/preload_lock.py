#!/usr/bin/env python3
"""
The installer's preload (and a build's, `pack-build`): what install.sh and install.bat would fetch from the network, packed on a computer with a
network into one archive for a computer without one (docs/DESIGN.md, Installer, platforms and dependencies). Used by
preload.sh and preload.ps1 in the checkout and, offline, by the installers, which write the two scripts for exactly
this computer and wait for the archive's path. Needs only Python 3 (the standard library) and, to fetch, its pip.

    preload_lock.py spec --requirements LOCK --resolve RESOLVE --target T --python X.Y[.Z] [--glibc 2.NN]
                         --version V --system-packages FILE --out SPEC
        the preload's recipe in the folder SPEC: manifest.base (target, Python, platform tags, the lock's sha256, ...),
        lock.txt (the entries of the hash-pinned export LOCK whose markers hold on the target, without their markers
        and with every hash), uv.txt (uv from RESOLVE), pip-args.txt and system-packages.txt. The markers are evaluated
        for the target, not for the computer that downloads (pip would leave out colorama for Windows on Linux); with
        only X.Y an entry counts when its marker holds for any patch level of X.Y.
    preload_lock.py pack --spec SPEC [--out FOLDER]
        fetches the wheels with this Python's pip (pip download --only-binary :all: --no-deps --require-hashes for the
        target's platform) and writes llmcf-preload-V-T-pyX.Y.tar.gz into FOLDER: llmcf-preload/ with manifest.txt,
        wheels/, tools/ (uv), system-packages.txt and SHA256SUMS; prints the archive's sha256.
    preload_lock.py write-scripts --spec SPEC --out FOLDER
        writes FOLDER/preload.sh and FOLDER/preload.ps1, each standing alone (the recipe and this program inside): run
        on any computer with a network and Python 3 with pip, they do `pack` into the current folder (or --out / -Out).
    preload_lock.py open --archive FILE --out FOLDER [--sha256 HEX] [--kind installer|build]
        checks a preload archive (only folders and files under llmcf-preload/, SHA256SUMS complete and right, the kind
        asked for) and unpacks it into FOLDER; prints its manifest and the archive's sha256 (install.bat reads it).
    preload_lock.py pack-build --spec SPEC [--out FOLDER] [--image-file FILE ...]
        a build's preload (kind=build): the recipe SPEC (manifest.base, sources.json) that llmcf wrote for an offline
        build's missing sources; fetches its base images, llama.cpp, Python packages, downloads, the files taken
        from source images (image_files.py, next to this file; a source image published only as a file is read from
        the --image-file FILE whose sha256 it names) and the sources of the runtime layer's tools, and writes
        llmcf-preload-build-TARGET-ARCH-RECIPE.tar.gz into FOLDER. The scripts llmcf writes for a build run it.
    preload_lock.py pip-args --target T --python X.Y [--glibc 2.NN]     the pip download options, one per line
    preload_lock.py this-computer                                       target, Python version, glibc of this computer

T is linux-x86_64, linux-aarch64, windows-amd64 or windows-arm64. Exit codes: 0 done, 1 failed (the reason is said),
2 wrong use.
"""

import argparse
import hashlib
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

FORMAT = "1"
TOP = "llmcf-preload"
TARGETS = {
    "linux-x86_64": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
                     "platform_machine": "x86_64"},
    "linux-aarch64": {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
                      "platform_machine": "aarch64"},
    "windows-amd64": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
                      "platform_machine": "AMD64"},
    "windows-arm64": {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt",
                      "platform_machine": "ARM64"},
}
WINDOWS_PLATFORMS = {"windows-amd64": "win_amd64", "windows-arm64": "win_arm64"}
# manylinux2014 is manylinux_2_17; nothing older is looked for.
OLDEST_GLIBC_MINOR = 17
PYTHON = re.compile(r"^3\.(\d{1,2})(?:\.(\d{1,3}))?$")
GLIBC = re.compile(r"^2\.(\d{1,3})$")
SPEC_FILES = ("manifest.base", "lock.txt", "uv.txt", "pip-args.txt", "system-packages.txt")


class PreloadError(Exception):
    pass


def say(message: str) -> None:
    print(f"preload: {message}", flush=True)


# --- markers (PEP 508), evaluated for the target ---------------------------------------------------------------
# Own evaluator, so that the installers need no package beyond Python itself (a system Python may have no pip).
MARKER_TOKEN = re.compile(r"\s*(?:(?P<str>'[^']*'|\"[^\"]*\")|(?P<op>===|==|!=|<=|>=|~=|<|>|\(|\))"
                          r"|(?P<word>not\s+in\b|[A-Za-z_][A-Za-z0-9_.]*))")
VERSION_VARIABLES = {"python_version", "python_full_version", "implementation_version"}


def _tokens(text: str):
    position, out = 0, []
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = MARKER_TOKEN.match(text, position)
        if not match:
            raise PreloadError(f"marker not understood: {text!r}")
        position = match.end()
        kind = match.lastgroup
        value = match.group(kind)
        out.append((kind, re.sub(r"\s+", " ", value) if kind == "word" else value))
    return out


def _version(text: str):
    if not re.fullmatch(r"\d+(\.\d+)*", text):
        return None
    parts = [int(p) for p in text.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _compare(left: str, op: str, right: str, versions: bool) -> bool:
    if op == "in":
        return left in right
    if op == "not in":
        return left not in right
    if op == "===":
        return left == right
    a, b = (_version(left), _version(right)) if versions else (None, None)
    if a is None or b is None:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        raise PreloadError(f"cannot compare {left!r} {op} {right!r}")
    if op == "~=":
        prefix = _version(".".join(right.split(".")[:-1])) or ()
        return a >= b and a[:len(prefix)] == prefix
    return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def marker_holds(marker: str, env: dict) -> bool:
    tokens = _tokens(marker)
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else (None, None)

    def take():
        nonlocal position
        position += 1
        return tokens[position - 1]

    def value():
        kind, text = take()
        if kind == "str":
            return text[1:-1], None
        if kind == "word" and text in env:
            return env[text], text
        raise PreloadError(f"unknown marker value {text!r} in {marker!r}")

    def atom():
        if peek() == ("op", "("):
            take()
            result = either()
            if take() != ("op", ")"):
                raise PreloadError(f"missing ')' in {marker!r}")
            return result
        left, left_name = value()
        kind, op = take()
        if not (kind == "op" or op in ("in", "not in")):
            raise PreloadError(f"no comparison in {marker!r}")
        right, right_name = value()
        return _compare(left, op, right, bool({left_name, right_name} & VERSION_VARIABLES))

    def both():
        result = atom()
        while peek() == ("word", "and"):
            take()
            result = atom() and result
        return result

    def either():
        result = both()
        while peek() == ("word", "or"):
            take()
            result = both() or result
        return result

    result = either()
    if position != len(tokens):
        raise PreloadError(f"marker not understood: {marker!r}")
    return result


# --- the lock ------------------------------------------------------------------------------------------------------
def python_parts(text: str):
    match = PYTHON.match(text or "")
    if not match:
        return None
    return int(match.group(1)), (int(match.group(2)) if match.group(2) is not None else None)


def environments(target: str, python: str):
    """The marker environments of the target: one per patch level that matters when the patch is not given."""
    minor, patch = python_parts(python)
    base = dict(TARGETS[target], implementation_name="cpython", platform_python_implementation="CPython",
                platform_release="", platform_version="", python_version=f"3.{minor}", extra="")
    for level in ([patch] if patch is not None else [0, 99]):
        yield dict(base, python_full_version=f"3.{minor}.{level}", implementation_version=f"3.{minor}.{level}")


def entries(text: str):
    """(requirement, marker or None, [hashes]) for each entry of a uv/pip export; comments and options skipped."""
    logical, current = [], ""
    for raw in text.splitlines():
        line = "" if raw.lstrip().startswith("#") else raw.split(" #", 1)[0].rstrip()
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        if current.strip():
            logical.append(current.strip())
        current = ""
    if current.strip():
        logical.append(current.strip())
    for line in logical:
        if line.startswith("-"):
            continue
        hashes = re.findall(r"--hash=(\S+)", line)
        head = re.split(r"\s--hash=", " " + line, maxsplit=1)[0].strip()
        requirement, _, marker = head.partition(";")
        yield requirement.strip(), (marker.strip() or None), hashes


def name_of(requirement: str) -> str:
    return re.split(r"[\s=<>!~\[;]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def filter_lock(text: str, target: str, python: str, only=None):
    """The export's entries for the target ('name==version \\' and its hashes), markers already evaluated."""
    envs = list(environments(target, python))
    kept = []
    for requirement, marker, hashes in entries(text):
        if only and name_of(requirement) not in only:
            continue
        if marker and not any(marker_holds(marker, env) for env in envs):
            continue
        if not hashes:
            raise PreloadError(f"{requirement} has no hash; the lock exports are hash-pinned")
        kept.append(requirement + " \\\n" + " \\\n".join(f"    --hash={h}" for h in hashes))
    return kept


def pip_args(target: str, python: str, glibc: str):
    minor, _ = python_parts(python)
    args = []
    if target in WINDOWS_PLATFORMS:
        args.append(f"--platform={WINDOWS_PLATFORMS[target]}")
    else:
        arch = TARGETS[target]["platform_machine"]
        newest = int(GLIBC.match(glibc).group(1))
        args += [f"--platform=manylinux_2_{level}_{arch}" for level in range(newest, OLDEST_GLIBC_MINOR - 1, -1)]
        args.append(f"--platform=manylinux2014_{arch}")
    return args + [f"--python-version=3.{minor}", "--implementation=cp", f"--abi=cp3{minor}", "--abi=abi3",
                   "--abi=none"]


def this_computer():
    system, machine = platform.system().lower(), platform.machine().lower()
    if system == "windows":
        target = "windows-arm64" if machine in ("arm64", "aarch64") else "windows-amd64"
    else:
        target = f"{system}-" + {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    libc, version = platform.libc_ver()
    glibc = version if libc == "glibc" and GLIBC.match(version or "") else ""
    return target, "%d.%d.%d" % sys.version_info[:3], glibc


def check_target(target: str, python: str, glibc: str) -> None:
    if target not in TARGETS:
        raise PreloadError(f"unknown target {target!r} (one of: {', '.join(TARGETS)})")
    parts = python_parts(python)
    if not parts or not 11 <= parts[0] <= 14:
        raise PreloadError(f"Python {python!r}: 3.11 to 3.14, as X.Y or X.Y.Z")
    if target not in WINDOWS_PLATFORMS and not GLIBC.match(glibc or ""):
        raise PreloadError(f"glibc {glibc!r}: as 2.NN")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- spec, pack, write-scripts -----------------------------------------------------------------------------------
def make_spec(requirements: Path, resolve: Path, target: str, python: str, glibc: str, version: str,
              system_packages: Path, out: Path) -> int:
    glibc = "" if target in WINDOWS_PLATFORMS else glibc
    check_target(target, python, glibc)
    lock = filter_lock(requirements.read_text(encoding="utf-8"), target, python)
    uv = filter_lock(resolve.read_text(encoding="utf-8"), target, python, {"uv"})
    if not lock:
        raise PreloadError(f"no entry of {requirements.name} for {target}, Python {python}")
    if len(uv) != 1:
        raise PreloadError(f"uv is not pinned once in {resolve.name}")
    args = pip_args(target, python, glibc or "2.17")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"format": FORMAT, "version": version, "target": target, "python": python, "glibc": glibc,
                "platforms": " ".join(a.split("=", 1)[1] for a in args if a.startswith("--platform=")),
                "implementation": "cp", "abi": " ".join(a.split("=", 1)[1] for a in args if a.startswith("--abi=")),
                "lock": requirements.name, "lock_sha256": sha256_of(requirements),
                "uv": uv[0].split(" ", 1)[0]}
    (out / "manifest.base").write_text("".join(f"{k}={v}\n" for k, v in manifest.items()), encoding="utf-8")
    (out / "lock.txt").write_text("\n".join(lock) + "\n", encoding="utf-8")
    (out / "uv.txt").write_text(uv[0] + "\n", encoding="utf-8")
    (out / "pip-args.txt").write_text("\n".join(args) + "\n", encoding="utf-8")
    (out / "system-packages.txt").write_text(system_packages.read_text(encoding="utf-8"), encoding="utf-8")
    return len(lock)


def read_manifest(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def spec_count(spec: Path) -> int:
    return sum(1 for r, _, _ in entries((spec / "lock.txt").read_text(encoding="utf-8")) if r)


# What pip needs to build a package that is published only as a source archive (langdetect), on the computer without
# a network: setuptools and wheel, fetched for the target (they are pure Python) next to the packages.
BUILD_TOOLS = ("setuptools", "wheel")
SOURCE_SUFFIXES = (".tar.gz", ".zip", ".tar.bz2")


def _download(requirements, dest: Path, args, hashes=True) -> None:
    """pip download for the target: a wheel where the package has one for the target's platform, else its source
    archive (--prefer-binary; still checked against the lock's hash). The lock already chose each version for the
    target's Python (its markers), so pip's own Requires-Python check, which knows only X.Y, is not repeated."""
    source = ["-r", str(requirements), "--require-hashes"] if hashes else list(requirements)
    command = [sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--no-input",
               "--prefer-binary", "--no-deps", "--ignore-requires-python", *source, "--dest", str(dest), *args]
    if subprocess.run(command).returncode != 0:
        raise PreloadError("pip could not fetch every package for this target (above); a higher --glibc sometimes "
                           "finds newer wheels")


def _tar_add(archive: tarfile.TarFile, path: Path, name: str, now: int) -> None:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = now
    if path.is_dir():
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        archive.addfile(info)
        for child in sorted(path.iterdir()):
            _tar_add(archive, child, f"{name}/{child.name}", now)
    else:
        info.size, info.mode = path.stat().st_size, 0o644
        with open(path, "rb") as handle:
            archive.addfile(info, handle)


def pack(spec: Path, out: Path) -> Path:
    for name in SPEC_FILES:
        if not (spec / name).is_file():
            raise PreloadError(f"{name} is missing in {spec}")
    manifest = read_manifest((spec / "manifest.base").read_text(encoding="utf-8"))
    args = [a for a in (spec / "pip-args.txt").read_text(encoding="utf-8").splitlines() if a.strip()]
    count = spec_count(spec)
    if subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True).returncode != 0:
        raise PreloadError(f"{sys.executable} has no pip (Debian, Ubuntu: sudo apt install python3-pip)")
    say(f"target {manifest['target']}, Python {manifest['python']}"
        + (f", glibc {manifest['glibc']}" if manifest.get("glibc") else "")
        + f"; {count} packages of {manifest['lock']} (llmcf {manifest['version']}), {manifest['uv']}")
    out.mkdir(parents=True, exist_ok=True)
    python = ".".join(manifest["python"].split(".")[:2])
    archive_path = out.resolve() / f"{TOP}-{manifest['version']}-{manifest['target']}-py{python}.tar.gz"
    with tempfile.TemporaryDirectory() as work:
        top = Path(work) / TOP
        (top / "wheels").mkdir(parents=True)
        (top / "tools").mkdir()
        say(f"fetching {count} wheels (pip download, checked against the lock's hashes)...")
        # One pip call per round, each name once: with only X.Y a package can be in the lock twice, once per patch
        # level (e.g. one pin before and one from Python 3.11.5), and pip refuses two versions of one name in one call.
        rounds, seen = [], {}
        for requirement, _, hashes in entries((spec / "lock.txt").read_text(encoding="utf-8")):
            index = seen.get(name_of(requirement), 0)
            seen[name_of(requirement)] = index + 1
            if index == len(rounds):
                rounds.append([])
            rounds[index].append(requirement + " " + " ".join(f"--hash={h}" for h in hashes))
        for number, lines in enumerate(rounds):
            part = Path(work) / f"lock-{number}.txt"
            part.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _download(part, top / "wheels", args)
        fetched = sorted(p.name for p in (top / "wheels").iterdir() if p.is_file())
        if len(fetched) != count:
            raise PreloadError(f"pip fetched {len(fetched)} files, the lock names {count} packages for the target")
        sources = [name for name in fetched if name.endswith(SOURCE_SUFFIXES)]
        if sources:
            say(f"published only as source (built on the target from the archive): {', '.join(sources)}; fetching "
                f"{' and '.join(BUILD_TOOLS)} to build them there...")
            (top / "build").mkdir()
            _download([*BUILD_TOOLS], top / "build", ["--only-binary=:all:"], hashes=False)
        say("fetching uv (pinned in requirements-resolve.txt)...")
        _download(spec / "uv.txt", top / "tools", args)
        (top / "system-packages.txt").write_bytes((spec / "system-packages.txt").read_bytes())
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (top / "manifest.txt").write_text((spec / "manifest.base").read_text(encoding="utf-8")
                                          + f"sources={' '.join(sources)}\ncreated={created}\n", encoding="utf-8")
        files = sorted(p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file())
        (top / "SHA256SUMS").write_text("".join(f"{sha256_of(top / f)}  {f}\n" for f in files), encoding="utf-8")
        part = archive_path.with_name(archive_path.name + ".part")
        with tarfile.open(part, "w:gz") as archive:
            _tar_add(archive, top, TOP, int(time.time()))
        os.replace(part, archive_path)
    digest = sha256_of(archive_path)
    say(f"done: {archive_path} ({archive_path.stat().st_size // (1 << 20)} MB)")
    say(f"sha256: {digest}")
    print("Carry the archive to the computer without a network (with the same llmcf checkout) and give the installer "
          "its path when it asks, or start it with it; compare the sha256 above with the one the installer prints, or "
          "let it check it:")
    if manifest["target"].startswith("windows-"):
        print(f"  install.bat --offline --preload={archive_path.name} --preload-sha256={digest}")
    else:
        print(f"  sudo ./install.sh --offline --preload {archive_path.name} --preload-sha256 {digest}")
    print(f"What the installer cannot install from the archive (system packages such as the container engine) is "
          f"listed in {TOP}/system-packages.txt.")
    return archive_path


def open_archive(archive: Path, out: Path, expected: str = "", kind: str = "installer") -> dict:
    """Checks a preload archive and unpacks it into out/llmcf-preload (install.bat; install.sh does the same in bash;
    a build's preload with kind="build", Core/build_preload.py): only folders and files under llmcf-preload/, every
    file listed in SHA256SUMS with its sha256 and nothing else, the format this program reads and the kind asked for
    (the installer's has wheels/). Returns the manifest with 'archive_sha256' added."""
    digest = sha256_of(archive)
    if expected and expected.lower() != digest:
        raise PreloadError(f"the preload is not the archive preload wrote: sha256 {digest}, expected {expected.lower()}")
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                parts = member.name.split("/")
                if member.name.startswith(("/", "\\")) or ".." in parts or "\\" in member.name or parts[0] != TOP:
                    raise PreloadError(f"the preload holds a path outside {TOP}/: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise PreloadError(f"the preload holds an entry that is neither file nor folder: {member.name}")
            out.mkdir(parents=True, exist_ok=True)
            for member in members:
                target = out / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, open(target, "wb") as sink:
                        sink.write(source.read())
    except (tarfile.TarError, EOFError, OSError) as error:
        raise PreloadError(f"{archive} is not a readable .tar.gz ({error})")
    top = out / TOP
    sums = top / "SHA256SUMS"
    if not sums.is_file() or not (top / "manifest.txt").is_file():
        raise PreloadError("the preload is incomplete (SHA256SUMS and manifest.txt belong in it)")
    listed = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/ ][^ ]*)", line)
        if not match or ".." in match.group(2).split("/"):
            raise PreloadError(f"SHA256SUMS has a line that is not 'sha256  path' inside the preload: {line!r}")
        listed[match.group(2)] = match.group(1)
    present = {p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    if present != set(listed):
        raise PreloadError("the files in the preload are not the ones SHA256SUMS lists: "
                           + " ".join(sorted(present ^ set(listed))))
    for name, want in listed.items():
        if sha256_of(top / name) != want:
            raise PreloadError(f"{name} in the preload does not have the sha256 SHA256SUMS names: the archive is "
                               "damaged; copy it again or make it anew")
    manifest = read_manifest((top / "manifest.txt").read_text(encoding="utf-8"))
    if manifest.get("format") != FORMAT:
        raise PreloadError(f"the preload has format {manifest.get('format')!r}, this installer reads {FORMAT}")
    found = manifest.get("kind") or "installer"
    if found != kind:
        raise PreloadError("this is a build's preload (its images, sources and packages), not the installer's: give "
                           "it to llmcf when a build asks (llm-cli build preload take FILE)" if found == "build" else
                           f"this is the installer's preload, not a build's: install.sh --offline --preload FILE"
                           if found == "installer" else f"the preload is of the unknown kind {found!r}")
    if kind == "installer" and not (top / "wheels").is_dir():
        raise PreloadError("the preload is incomplete (wheels/ belongs in it)")
    return dict(manifest, archive_sha256=digest, folder=str(top))


def _delimiter(texts) -> str:
    word = "LLMCF_" + "PRELOAD_END"
    while any(word in text for text in texts):
        word += "_"
    return word


def write_scripts(spec: Path, out: Path):
    manifest = read_manifest((spec / "manifest.base").read_text(encoding="utf-8"))
    files = {name: (spec / name).read_text(encoding="utf-8") for name in SPEC_FILES}
    program = Path(__file__).read_text(encoding="utf-8")
    end = _delimiter([*files.values(), program])
    count = spec_count(spec)
    what = (f"{count} Python packages of llmcf {manifest['version']} ({manifest['lock']}) as wheels for "
            f"{manifest['target']}, Python {manifest['python']}"
            + (f", glibc {manifest['glibc']}" if manifest.get("glibc") else "")
            + f"; {manifest['uv']}; the list of system packages")
    out.mkdir(parents=True, exist_ok=True)
    sh = ["#!/bin/sh",
          "# llmcf preload, written by the llmcf installer for the computer without a network.",
          "# Run it on a computer WITH a network (Linux or macOS, Python 3 with pip): sh preload.sh [--out FOLDER]",
          f"# It fetches: {what}.",
          f"# It writes {TOP}-{manifest['version']}-{manifest['target']}-py"
          f"{'.'.join(manifest['python'].split('.')[:2])}.tar.gz into the current folder (or FOLDER);",
          "# carry it back and give the installer its path. The recipe and the program are inside this script.",
          "set -eu",
          'out="$PWD"',
          'if [ "${1:-}" = "--out" ] && [ -n "${2:-}" ]; then out="$2"; fi',
          'py="${PRELOAD_PYTHON:-}"',
          'if [ -z "$py" ]; then for c in python3 python; do if command -v "$c" > /dev/null 2>&1; then py="$c"; break; fi; '
          'done; fi',
          'if [ -z "$py" ]; then echo "preload: Python 3 with pip is needed (PRELOAD_PYTHON names one)" >&2; exit 1; fi',
          'work="$(mktemp -d)"',
          "trap 'rm -rf \"$work\"' EXIT",
          'mkdir "$work/spec"']
    for name, text in files.items():
        sh += [f"cat > \"$work/spec/{name}\" <<'{end}'", text.rstrip("\n"), end]
    sh += [f"cat > \"$work/preload_lock.py\" <<'{end}'", program.rstrip("\n"), end,
           '"$py" -I "$work/preload_lock.py" pack --spec "$work/spec" --out "$out"', ""]
    ps = ["# llmcf preload, written by the llmcf installer for the computer without a network.",
          "# Run it on a computer WITH a network (Windows, Python 3 with pip):",
          "#   powershell -ExecutionPolicy Bypass -File preload.ps1 [-Out FOLDER]",
          f"# It fetches: {what}.",
          "# It writes the archive (.tar.gz) into the current folder (or FOLDER); carry it back and give the installer",
          "# its path. The recipe and the program are inside this script.",
          "param([string]$Out = (Get-Location).Path)",
          '$ErrorActionPreference = "Stop"',
          "$Py = $null; $PyArgs = @()",
          "if ($env:PRELOAD_PYTHON) { $Py = $env:PRELOAD_PYTHON }",
          'elseif (Get-Command "py" -ErrorAction SilentlyContinue) { $Py = "py"; $PyArgs = @("-3") }',
          'elseif (Get-Command "python" -ErrorAction SilentlyContinue) { $Py = "python" }',
          'elseif (Get-Command "python3" -ErrorAction SilentlyContinue) { $Py = "python3" }',
          'if (-not $Py) { Write-Error "preload: Python 3 with pip is needed (PRELOAD_PYTHON names one)." }',
          '$Work = Join-Path ([IO.Path]::GetTempPath()) ("llmcf-preload-" + [Guid]::NewGuid().ToString("N"))',
          '$Spec = Join-Path $Work "spec"',
          "New-Item -ItemType Directory -Path $Spec -Force | Out-Null",
          "$Utf8 = New-Object System.Text.UTF8Encoding $false",
          # a here-string leaves out its last line break: it is written back, so the file is the same bytes as on the PC
          "function Write-Part ($Path, $Text) { [IO.File]::WriteAllText($Path, $Text.Replace(\"`r`n\", \"`n\") + \"`n\", $Utf8) }"]
    for name, text in [*files.items(), ("../preload_lock.py", program)]:
        target = f'(Join-Path $Spec "{name}")' if not name.startswith("..") else '(Join-Path $Work "preload_lock.py")'
        ps += [f"Write-Part {target} @'", text.rstrip("\n"), "'@"]
    ps += ["try {",
           '    & $Py @PyArgs -I (Join-Path $Work "preload_lock.py") pack --spec $Spec --out $Out',
           "    $Code = $LASTEXITCODE",
           "} finally {",
           "    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue",
           "}",
           "exit $Code", ""]
    if any(line.startswith("'@") for text in [*files.values(), program] for line in text.splitlines()):
        raise PreloadError("a recipe line would end a PowerShell here-string")
    (out / "preload.sh").write_text("\n".join(sh), encoding="utf-8", newline="\n")
    (out / "preload.sh").chmod(0o755)
    (out / "preload.ps1").write_text("\n".join(ps).replace("\r\n", "\n").replace("\n", "\r\n"),
                                     encoding="utf-8-sig", newline="")
    return out / "preload.sh", out / "preload.ps1", what


# --- the build's preload (kind=build) ------------------------------------------------------------------------------
# A build's preload carries exactly the sources an offline build of one target's build container image lacks
# (Core/build_preload.py writes the recipe, BUILD_SPEC_FILES): base images, the pinned llama.cpp commit, the image's
# Python packages, pinned downloads, the files taken from source images and the image's distribution packages, and the
# sources of the runtime layer's tools when that computer has none (toolbox/, Core/toolbox_preload.py); same
# folder, manifest, SHA256SUMS and checks as the installer's preload. The image reader is scripts/image_files.py and
# the distribution packages are scripts/deb_preload.py's (the board preload's packages/ folder), both standard library
# only, next to this file in the scripts the build writes.
BUILD_SPEC_FILES = ("manifest.base", "sources.json")
ARCHES = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}


def _beside(name: str) -> Path:
    """A program next to this file (scripts/ of the checkout, and the folder the build's scripts unpack into)."""
    path = Path(__file__).resolve().with_name(name)
    if not path.is_file():
        raise PreloadError(f"{name} is missing next to preload_lock.py")
    return path


def _image_files_path() -> Path:
    return _beside("image_files.py")


def _load(name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"llmcf_{name}", _beside(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _image_files():
    return _load("image_files")


def _deb_preload():
    return _load("deb_preload")


def _gather_packages(apt: dict, images_saved: dict, top: Path) -> dict:
    """The build image's distribution packages into top/packages (the board preload's folder, deb_preload.gather):
    each stage resolved against its base image's dpkg status, from the recipe (an image on the computer that wrote
    it) or read from the base image this preload fetched (`images_saved`: reference -> its docker save archive);
    without one, the whole closure. Returns the manifest's lines for it."""
    deb, images = _deb_preload(), _image_files()
    distribution, release = apt.get("distribution"), apt.get("release")
    stages = []
    for stage in apt.get("stages") or []:
        installed = stage.get("installed")
        saved = images_saved.get(stage.get("image") or "")
        if saved is not None:
            try:
                read = images.read_saved(saved, ["usr/lib/os-release", "etc/os-release", "var/lib/dpkg/status"])
            except images.ImageFilesError as error:
                raise PreloadError(str(error))
            facts = deb.parse_os_release((read["usr/lib/os-release"] or read["etc/os-release"] or b"").decode(
                "utf-8", "replace"))
            found = (facts.get("ID", ""), facts.get("VERSION_CODENAME", ""))
            if (distribution or release) and found != (distribution, release):
                raise PreloadError(f"the base image {stage['image']} is {found[0]} {found[1]}, the recipe names "
                                   f"{distribution} {release}")
            distribution, release = found
            if installed is None and read["var/lib/dpkg/status"] is not None:
                installed = deb.installed_from_status(read["var/lib/dpkg/status"].decode("utf-8", "replace"))
        if installed is None:
            say(f"the dpkg status of {stage.get('image')} is not known: its packages' whole closure is fetched")
        stages.append({"packages": stage["packages"], "installed": installed or {}})
    try:
        recipe = deb.make_build_recipe(distribution, release, apt.get("arch"), stages)
        say(f"fetching the build image's distribution packages for {distribution} {release} on {apt.get('arch')} "
            "(checked against the distribution's signed package lists)...")
        resolved = deb.gather(recipe, top / deb.FOLDER, fetch=deb._fetch, log=say)
    except deb.PreloadError as error:
        raise PreloadError(str(error))
    names = {word.split("=", 1)[0] for word in resolved}
    in_base = sorted({p for s in stages for p in s["packages"] if p.split("=", 1)[0] not in names})
    return {"packages_distribution": distribution, "packages_release": release, "packages_arch": apt.get("arch"),
            "packages_resolved": ",".join(resolved) or "-", "packages_in_base": ",".join(in_base) or "-"}


def build_archive_name(manifest: dict) -> str:
    arch = ARCHES.get(manifest.get("platform", ""), "unknown")
    return f"{TOP}-build-{manifest['target'].lower()}-{arch}-{manifest['recipe_sha256'][:12]}.tar.gz"


def _fetch(url: str, dest: Path, sha256: str = "", size: int = 0) -> None:
    import urllib.request
    check, got = hashlib.sha256(), 0
    part = Path(str(dest) + ".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as answer, open(part, "wb") as sink:  # nosec B310 - https pins
            for block in iter(lambda: answer.read(1 << 20), b""):
                check.update(block)
                sink.write(block)
                got += len(block)
    except OSError as error:
        part.unlink(missing_ok=True)
        raise PreloadError(f"{url}: {getattr(error, 'reason', error)}")
    if (size and got != size) or (sha256 and check.hexdigest() != sha256):
        part.unlink()
        raise PreloadError(f"{url}: not the file the build pins (size {got}, sha256 {check.hexdigest()}; pinned "
                           f"{size or '-'} and {sha256 or '-'})")
    os.replace(part, dest)


def raw_base(repo: str, commit: str) -> str:
    """Where llama.cpp's files of `commit` are read one by one (GitHub's raw address of the repository)."""
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", repo or "")
    if not match or not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise PreloadError(f"llama.cpp's requirement files are read from a GitHub repository at a full commit, not "
                           f"{repo} {commit}")
    return f"https://raw.githubusercontent.com/{match.group(1)}/{match.group(2)}/{commit}/"


def _llama_requirements(base: str, names, work: Path) -> list:
    """Fetches llama.cpp's requirement files `names` (paths at the pinned commit) and every file they include with
    -r, into work/ at the same paths. Returns the local paths of `names`."""
    todo, seen = list(names), set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        parts = name.split("/")
        if not name or ".." in parts or name.startswith("/") or len(seen) > 50:
            raise PreloadError(f"llama.cpp requirement file {name!r} is not a path inside the repository")
        dest = work / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        _fetch(base + name, dest)
        folder = "/".join(parts[:-1])
        for line in dest.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(?:-r|--requirement)\s*=?\s*(\S+)", line)
            if match:
                joined = os.path.normpath(f"{folder}/{match.group(1)}" if folder else match.group(1))
                todo.append(joined.replace("\\", "/"))
    return [work / name for name in names]


def build_pip_args(platform_name: str, python: str, glibc: str):
    """The pip download options for the build image's platform and Python (manylinux and plain linux wheels; the
    PyTorch index serves plain linux ones)."""
    arch = ARCHES.get(platform_name)
    if arch is None:
        raise PreloadError(f"the build image's platform {platform_name!r} is not one the preload knows")
    args = pip_args(f"linux-{arch}", python, glibc)
    return args + [f"--platform=linux_{arch}", "--only-binary=:all:"]


def _gather_pip(pip: dict, platform_name: str, llama: dict, wheels: Path, work: Path) -> int:
    args = build_pip_args(platform_name, pip.get("python", "3.11"), pip.get("glibc", "2.36"))
    for number, group in enumerate(pip.get("groups") or []):
        if group.get("platforms") and platform_name not in group["platforms"]:
            continue   # a group only for another build computer (RKLLM's wheels are x86_64 only)
        lines = list(group.get("requirements") or [])
        if group.get("llama_cpp_requirements"):
            if not llama:
                raise PreloadError("a group names llama.cpp's requirement files, but the recipe has no llama.cpp pin")
            files = _llama_requirements(raw_base(llama["repo"], llama["commit"]), group["llama_cpp_requirements"],
                                        work / f"llama-{number}")
            lines += [f"-r {path}" for path in files]
        part = work / f"group-{number}.txt"
        part.write_text("\n".join(lines) + "\n", encoding="utf-8")
        index = (["--index-url", group["index_url"]] if group.get("index_url") else [])
        for extra in group.get("extra_index_urls") or []:
            index += ["--extra-index-url", extra]
        command = [sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--no-input",
                   "--dest", str(wheels), "-r", str(part), *index, *args]
        say(f"fetching the Python packages of group {number + 1} with their dependencies (pip download)...")
        if subprocess.run(command).returncode != 0:
            raise PreloadError("pip could not fetch every package of the build image (above)")
    # pip, setuptools, wheel and the build backends the image's pip upgrades to or builds with, one call each (two
    # versions of one name, setuptools and setuptools<81, cannot share one call)
    for tool in pip.get("tools") or ["pip", "setuptools", "wheel", "poetry-core"]:
        if subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--no-input",
                           "--no-deps", "--dest", str(wheels), tool, *args]).returncode != 0:
            raise PreloadError(f"pip could not fetch {tool} for the build image (above)")
    return sum(1 for p in wheels.iterdir() if p.is_file())


def _bundle_llama(llama: dict, dest: Path, work: Path) -> None:
    """The pinned llama.cpp commit as a git bundle (llm-cli ditto runtime export-source writes the same)."""
    import shutil
    git = shutil.which("git")
    if not git:
        raise PreloadError("git is needed on this computer to fetch the pinned llama.cpp commit (install git, or "
                           "export the source with llm-cli ditto runtime export-source on a computer with llmcf)")
    repo = work / "llama.cpp"
    run = [git, "-C", str(repo)]
    steps = [[git, "init", "-q", str(repo)], run + ["fetch", "-q", "--depth", "1", llama["repo"], llama["commit"]],
             run + ["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"]]
    for step in steps:
        if subprocess.run(step).returncode != 0:
            raise PreloadError("git could not fetch the pinned llama.cpp commit (above)")
    head = subprocess.run(run + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if head != llama["commit"]:
        raise PreloadError(f"llama.cpp: got commit {head}, the pin is {llama['commit']}")
    if subprocess.run(run + ["bundle", "create", str(dest), "HEAD"], capture_output=True).returncode != 0:
        raise PreloadError("git could not write the llama.cpp bundle")


def _image_file_for(source: dict, given: dict) -> Path:
    """The file given with --image-file whose sha256 is the one the recipe's source image names."""
    archive = source.get("archive") or {}
    pin = str(archive.get("sha256") or "")
    path = given.get(pin)
    if path is None:
        name = f"{archive.get('name')} " if archive.get("name") else ""
        raise PreloadError(f"the recipe takes files from the image file {name}(sha256 {pin}), which its vendor "
                           "publishes only as a file: download it and give it with --image-file FILE (-ImageFile FILE "
                           "in PowerShell)" + (f"; the {len(given)} file(s) given have other sha256 sums" if given
                                               else ""))
    return path


def _gather_toolbox(toolbox: dict, platform_name: str, folder: Path) -> dict:
    """The sources of the runtime layer's tools (the recipe's section `toolbox`) into folder (llmcf-preload/toolbox/):
    the builder's base image pinned by digest as a docker save archive with its documents (image_files.save_pinned,
    every document and blob checked against its digest), the pinned source tarballs (sha256) and the builder's
    distribution packages (as the build image's, checked against the signed lists). Returns the manifest's lines of
    the packages (toolbox_packages_*). The computer that takes the archive in checks all of it against its own pins."""
    images = _image_files()
    base = str(toolbox.get("base") or "")
    say(f"fetching the runtime layer's tools' sources: the builder's base image {base.split('@')[0]} (pinned by "
        "digest; every document and blob checked)...")
    try:
        images.save_pinned(base, folder / "base", str(toolbox.get("base_name") or ""), platform_name)
    except images.ImageFilesError as error:
        raise PreloadError(str(error))
    (folder / "sources").mkdir()
    for pin in toolbox.get("sources") or []:
        name = str(pin.get("file") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) or not str(pin.get("url", "")).startswith(
                "https://") or not re.fullmatch(r"[0-9a-f]{64}", str(pin.get("sha256", ""))):
            raise PreloadError(f"the recipe names a source of the runtime layer's tools without a file name, an https "
                               f"address and a sha256: {name or '?'}")
        say(f"fetching {name} (sha256 pinned)...")
        _fetch(pin["url"], folder / "sources" / name, pin["sha256"])
    if not toolbox.get("apt"):
        return {}
    found = _gather_packages(toolbox["apt"], {base: folder / "base" / "image.tar"}, folder)
    say(f"{found['packages_resolved'].count('=')} distribution packages for the builder of the runtime layer's tools")
    return {f"toolbox_{key}": value for key, value in found.items()}


def pack_build(spec: Path, out: Path, image_files_given=()) -> Path:
    """Fetches what a build's recipe names and writes the build's preload into `out`. `image_files_given`: the files
    of source images published only as a file (each matched by its sha256)."""
    for name in BUILD_SPEC_FILES:
        if not (spec / name).is_file():
            raise PreloadError(f"{name} is missing in {spec}")
    manifest = read_manifest((spec / "manifest.base").read_text(encoding="utf-8"))
    recipe_bytes = (spec / "sources.json").read_bytes()
    if hashlib.sha256(recipe_bytes).hexdigest() != manifest.get("recipe_sha256"):
        raise PreloadError("sources.json is not the recipe the manifest names")
    import json
    recipe = json.loads(recipe_bytes.decode("utf-8"))
    platform_name = manifest["platform"]
    images = _image_files()
    here = images.host_platform()
    say(f"the build's preload of the target {manifest['target']} (llmcf {manifest['version']}), for {platform_name}")
    if here != platform_name and recipe.get("pip"):
        say(f"note: this computer is {here}; pip evaluates the packages' markers for this computer, so make the "
            f"preload on a {platform_name} computer if a package is missing on the other side")
    out.mkdir(parents=True, exist_ok=True)
    archive_path = out.resolve() / build_archive_name(manifest)
    personal = False
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        top = work / TOP
        top.mkdir()
        lines = []
        if recipe.get("images"):
            (top / "images").mkdir()
        for number, ref in enumerate(recipe.get("images") or []):
            say(f"fetching the base image {ref} ({platform_name}; every blob checked against its digest)...")
            try:
                saved = images.save_image(ref, top / "images" / f"{number:02d}.tar", platform_name)
            except images.ImageFilesError as error:
                raise PreloadError(str(error))
            lines.append(f"images/{number:02d}.tar {saved['name']} {saved['digest']} {saved['config']}")
        if lines:
            (top / "images.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        packages = {}
        if recipe.get("apt"):
            saved = {ref: top / "images" / f"{number:02d}.tar" for number, ref in enumerate(recipe.get("images") or [])}
            packages = _gather_packages(recipe["apt"], saved, top)
            say(f"{packages['packages_resolved'].count('=')} distribution packages for the build image")
        if recipe.get("toolbox"):
            (top / "toolbox").mkdir()
            packages.update(_gather_toolbox(recipe["toolbox"], platform_name, top / "toolbox"))
        llama = recipe.get("llama_cpp") or {}
        if recipe.get("llama_cpp_bundle") and llama:
            say(f"fetching llama.cpp at the pinned commit {llama['commit'][:12]} (git)...")
            (top / "llama.cpp").mkdir()
            _bundle_llama(llama, top / "llama.cpp" / "llama.cpp.bundle", work)
        if recipe.get("pip"):
            (top / "wheels").mkdir()
            count = _gather_pip(recipe["pip"], platform_name, llama, top / "wheels", work)
            say(f"{count} Python packages for the build image")
        if recipe.get("downloads"):
            (top / "files").mkdir()
        for item in recipe.get("downloads") or []:
            say(f"fetching {item['name']} (size and sha256 pinned)...")
            _fetch(item["url"], top / "files" / item["name"], item["sha256"], int(item.get("size") or 0))
        given = {}
        for path in image_files_given or ():
            if not Path(path).is_file():
                raise PreloadError(f"--image-file {path}: no such file")
            given[images.file_sha256(path)] = Path(path)
        for source in recipe.get("source_images") or []:
            wanted = {f["path"]: f["sha256"] for f in source.get("files") or []}
            archive = source.get("archive")
            try:
                if archive:
                    path = _image_file_for(source, given)
                    say(f"taking {len(wanted)} file(s) from the image file {path.name} (its sha256 checked, its layers "
                        "read, nothing run or loaded; the rest discarded)...")
                    images.take_archive_files(path, archive["sha256"], wanted, top / "image-files",
                                              source.get("platform") or platform_name, archive.get("config") or "")
                else:
                    say(f"taking {len(wanted)} file(s) from {source['image']} (its layers read, nothing run; the rest "
                        "discarded)...")
                    images.take_files(source["image"], wanted, top / "image-files", source.get("platform") or
                                      platform_name)
            except images.ImageFilesError as error:
                raise PreloadError(str(error))
            personal = personal or any(not f.get("redistributable") for f in source.get("files") or [])
        (top / "sources.json").write_bytes(recipe_bytes)
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (top / "manifest.txt").write_text((spec / "manifest.base").read_text(encoding="utf-8")
                                          + "".join(f"{k}={v}\n" for k, v in packages.items())
                                          + f"personal={'yes' if personal else 'no'}\ncreated={created}\n",
                                          encoding="utf-8")
        files = sorted(p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file())
        (top / "SHA256SUMS").write_text("".join(f"{sha256_of(top / f)}  {f}\n" for f in files), encoding="utf-8")
        part = archive_path.with_name(archive_path.name + ".part")
        with tarfile.open(part, "w:gz") as archive:
            _tar_add(archive, top, TOP, int(time.time()))
        os.replace(part, archive_path)
    digest = sha256_of(archive_path)
    say(f"done: {archive_path} ({archive_path.stat().st_size // (1 << 20)} MB)")
    say(f"sha256: {digest}")
    if personal:
        say("it holds files whose licence does not allow passing them on: it is for your own computers only; do not "
            "share it")
    for item in recipe.get("not_covered") or []:
        say(f"not in the preload: {item}")
    print("Carry the archive to the computer without a network and give llmcf its path when the build asks, or:")
    print(f"  llm-cli build preload take {archive_path.name} --target {manifest['target']} --sha256 {digest}")
    return archive_path


def write_build_scripts(spec: Path, out: Path, what: str):
    """preload.sh and preload.ps1 for a build's recipe: the recipe, this program, the image reader and the board
    preload's program (the distribution packages) inside."""
    files = {name: (spec / name).read_text(encoding="utf-8") for name in BUILD_SPEC_FILES}
    programs = {"preload_lock.py": Path(__file__).read_text(encoding="utf-8"),
                "image_files.py": _image_files_path().read_text(encoding="utf-8"),
                "deb_preload.py": _beside("deb_preload.py").read_text(encoding="utf-8")}
    end = _delimiter([*files.values(), *programs.values()])
    out.mkdir(parents=True, exist_ok=True)
    sh = ["#!/bin/sh",
          "# llmcf preload of a build, written by llmcf for the computer without a network.",
          "# Run it on a computer WITH a network (Linux or macOS, Python 3 with pip; git for llama.cpp):",
          "#   sh preload.sh [--out FOLDER] [--image-file FILE ...]",
          f"# It fetches: {what}.",
          "# It writes the archive (.tar.gz) into the current folder (or FOLDER); carry it back and give llmcf its",
          "# path. The recipe and the programs are inside this script.",
          "set -eu",
          'out="$PWD"',
          # the options; each --image-file FILE is moved to the end of "$@" and handed on as it is
          'n=$#',
          'while [ "$n" -gt 0 ]; do',
          '  if [ "$n" -lt 2 ]; then echo "preload: $1 needs a value" >&2; exit 2; fi',
          '  case "$1" in',
          '    --out) out="$2" ;;',
          '    --image-file) set -- "$@" --image-file "$2" ;;',
          '    *) echo "preload: unknown option $1 (--out FOLDER, --image-file FILE)" >&2; exit 2 ;;',
          '  esac',
          '  shift 2; n=$((n - 2))',
          'done',
          'py="${PRELOAD_PYTHON:-}"',
          'if [ -z "$py" ]; then for c in python3 python; do if command -v "$c" > /dev/null 2>&1; then py="$c"; break; fi; '
          'done; fi',
          'if [ -z "$py" ]; then echo "preload: Python 3 with pip is needed (PRELOAD_PYTHON names one)" >&2; exit 1; fi',
          'work="$(mktemp -d)"',
          "trap 'rm -rf \"$work\"' EXIT",
          'mkdir "$work/spec"']
    for name, text in files.items():
        sh += [f"cat > \"$work/spec/{name}\" <<'{end}'", text.rstrip("\n"), end]
    for name, text in programs.items():
        sh += [f"cat > \"$work/{name}\" <<'{end}'", text.rstrip("\n"), end]
    sh += ['"$py" -I "$work/preload_lock.py" pack-build --spec "$work/spec" --out "$out" "$@"', ""]
    ps = ["# llmcf preload of a build, written by llmcf for the computer without a network.",
          "# Run it on a computer WITH a network (Windows, Python 3 with pip; git for llama.cpp):",
          "#   powershell -ExecutionPolicy Bypass -File preload.ps1 [-Out FOLDER] [-ImageFile FILE[,FILE...]]",
          f"# It fetches: {what}.",
          "# It writes the archive (.tar.gz) into the current folder (or FOLDER); carry it back and give llmcf its",
          "# path. The recipe and the programs are inside this script.",
          "param([string]$Out = (Get-Location).Path, [string[]]$ImageFile = @())",
          '$ErrorActionPreference = "Stop"',
          '$Extra = @(); foreach ($F in $ImageFile) { $Extra += "--image-file"; $Extra += $F }',
          "$Py = $null; $PyArgs = @()",
          "if ($env:PRELOAD_PYTHON) { $Py = $env:PRELOAD_PYTHON }",
          'elseif (Get-Command "py" -ErrorAction SilentlyContinue) { $Py = "py"; $PyArgs = @("-3") }',
          'elseif (Get-Command "python" -ErrorAction SilentlyContinue) { $Py = "python" }',
          'elseif (Get-Command "python3" -ErrorAction SilentlyContinue) { $Py = "python3" }',
          'if (-not $Py) { Write-Error "preload: Python 3 with pip is needed (PRELOAD_PYTHON names one)." }',
          '$Work = Join-Path ([IO.Path]::GetTempPath()) ("llmcf-preload-" + [Guid]::NewGuid().ToString("N"))',
          '$Spec = Join-Path $Work "spec"',
          "New-Item -ItemType Directory -Path $Spec -Force | Out-Null",
          "$Utf8 = New-Object System.Text.UTF8Encoding $false",
          # a here-string leaves out its last line break: it is written back, so the file is the same bytes as on the PC
          "function Write-Part ($Path, $Text) { [IO.File]::WriteAllText($Path, $Text.Replace(\"`r`n\", \"`n\") + \"`n\", $Utf8) }"]
    for name, text in files.items():
        ps += [f'Write-Part (Join-Path $Spec "{name}") @\'', text.rstrip("\n"), "'@"]
    for name, text in programs.items():
        ps += [f'Write-Part (Join-Path $Work "{name}") @\'', text.rstrip("\n"), "'@"]
    ps += ["try {",
           '    & $Py @PyArgs -I (Join-Path $Work "preload_lock.py") pack-build --spec $Spec --out $Out @Extra',
           "    $Code = $LASTEXITCODE",
           "} finally {",
           "    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue",
           "}",
           "exit $Code", ""]
    if any(line.startswith("'@") for text in [*files.values(), *programs.values()] for line in text.splitlines()):
        raise PreloadError("a recipe line would end a PowerShell here-string")
    (out / "preload.sh").write_text("\n".join(sh), encoding="utf-8", newline="\n")
    (out / "preload.sh").chmod(0o755)
    (out / "preload.ps1").write_text("\n".join(ps).replace("\r\n", "\n").replace("\n", "\r\n"),
                                     encoding="utf-8-sig", newline="")
    return out / "preload.sh", out / "preload.ps1"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("spec")
    p.add_argument("--requirements", type=Path, required=True)
    p.add_argument("--resolve", type=Path, required=True)
    p.add_argument("--target", required=True)
    p.add_argument("--python", required=True)
    p.add_argument("--glibc", default="")
    p.add_argument("--version", required=True)
    p.add_argument("--system-packages", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("pack")
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("."))
    p = sub.add_parser("write-scripts")
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("open")
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sha256", default="")
    p.add_argument("--kind", default="installer", choices=("installer", "build"))
    p = sub.add_parser("pack-build")
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("."))
    p.add_argument("--image-file", type=Path, action="append", default=[])
    p = sub.add_parser("pip-args")
    p.add_argument("--target", required=True)
    p.add_argument("--python", required=True)
    p.add_argument("--glibc", default="2.28")
    sub.add_parser("this-computer")
    args = parser.parse_args(argv)
    try:
        if args.command == "this-computer":
            print("\n".join(this_computer()))
        elif args.command == "pip-args":
            glibc = "" if args.target in WINDOWS_PLATFORMS else args.glibc
            check_target(args.target, args.python, glibc)
            print("\n".join(pip_args(args.target, args.python, glibc)))
        elif args.command == "spec":
            print(make_spec(args.requirements, args.resolve, args.target, args.python, args.glibc, args.version,
                            args.system_packages, args.out))
        elif args.command == "pack":
            pack(args.spec, args.out)
        elif args.command == "open":
            print("".join(f"{k}={v}\n" for k, v in open_archive(args.archive, args.out, args.sha256,
                                                                    args.kind).items()), end="")
        elif args.command == "pack-build":
            pack_build(args.spec, args.out, args.image_file)
        else:
            sh, ps, what = write_scripts(args.spec, args.out)
            print(f"{sh}\n{ps}\n{what}")
    except PreloadError as error:
        print(f"preload: {error}", file=sys.stderr)
        return 2 if args.command in ("pip-args", "spec") and "target" in str(error) else 1
    except OSError as error:
        print(f"preload: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
