#!/usr/bin/env python3
"""
The installer's preload: what install.sh and install.bat would fetch from the network, packed on a computer with a
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
    preload_lock.py open --archive FILE --out FOLDER [--sha256 HEX]
        checks a preload archive (only folders and files under llmcf-preload/, SHA256SUMS complete and right) and
        unpacks it into FOLDER; prints its manifest and the archive's sha256 (install.bat reads it).
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
        # level (soupsieve for Python before and from 3.11.5), and pip refuses two versions of one name in one call.
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


def open_archive(archive: Path, out: Path, expected: str = "") -> dict:
    """Checks a preload archive and unpacks it into out/llmcf-preload (install.bat; install.sh does the same in bash):
    only folders and files under llmcf-preload/, every file listed in SHA256SUMS with its sha256 and nothing else,
    the format this program reads. Returns the manifest with 'archive_sha256' added."""
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
    if not sums.is_file() or not (top / "manifest.txt").is_file() or not (top / "wheels").is_dir():
        raise PreloadError("the preload is incomplete (SHA256SUMS, manifest.txt and wheels/ belong in it)")
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
          "function Write-Part ($Path, $Text) { [IO.File]::WriteAllText($Path, $Text.Replace(\"`r`n\", \"`n\"), $Utf8) }"]
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
    (out / "preload.ps1").write_text("\r\n".join(ps), encoding="utf-8-sig", newline="")
    return out / "preload.sh", out / "preload.ps1", what


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
            print("".join(f"{k}={v}\n" for k, v in open_archive(args.archive, args.out, args.sha256).items()), end="")
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
