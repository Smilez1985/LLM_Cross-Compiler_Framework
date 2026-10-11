#!/usr/bin/env python3
"""
The pinned llama.cpp source: fetched by commit, verified, cached read-only, and carried to air-gapped machines as a
git bundle.

- fetch_source() fetches exactly the pinned commit (`git fetch --depth 1 origin <commit>`, a tag only as the fallback
  for mirrors that refuse a fetch by commit), checks out FETCH_HEAD and requires `git rev-parse HEAD` to give the pinned
  commit back; anything else is refused. The verified tree goes to <state dir>/runtime/llama.cpp/src/<commit>/ and is
  made read-only. A later call verifies the cached tree (commit and a clean working tree) and needs no network.
- export_source() writes a git bundle of the pinned commit plus FILE.json (commit, tag, size and sha256 of the bundle,
  framework version); import_source() fetches the commit from such a bundle and verifies it the same way. Git's object
  hashing checks the content whoever made the bundle and with whichever git version, which a sha256 of an archive
  cannot.
- upstream_tag_commit() runs the pin-bump check: `git ls-remote <repo> refs/tags/<tag>` must name the pinned commit,
  because GitHub serves commits pushed to any fork through the upstream URL, so a commit alone fixes the content but
  not its origin.

Git runs with an argument list (never a shell), a cleared environment, no user or system git configuration, no hooks,
no credential helpers or prompts, and only the https transport (plus local files for bundles and local mirrors).
Bundle files and remote output are untrusted: sizes are capped and every value is checked before it is used.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Union

from orchestrator import __version__
from orchestrator.runtime.catalog import LlamaCppSource

GIT_TIMEOUT = 3600            # seconds for a fetch (about 215 MB at the pin)
LOCAL_TIMEOUT = 300           # seconds for local git commands
MAX_BUNDLE_BYTES = 2 * 1024 ** 3
MAX_MANIFEST_BYTES = 16384
MANIFEST_FORMAT = 1

_SHA1 = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_TAG = re.compile(r"^b\d{1,7}$", re.ASCII)
_REF = re.compile(r"^(HEAD|refs/[A-Za-z0-9._/-]{1,200})$", re.ASCII)
# Variables a git subprocess may inherit: the executable search path, proxies and CA bundles for https, and what
# Windows needs to start a process. Nothing else (no GIT_*, no credentials, no user configuration).
_PASS_ENV = ("PATH", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
             "ALL_PROXY", "all_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "GIT_SSL_CAINFO", "CURL_CA_BUNDLE",
             "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR", "PATHEXT", "COMSPEC")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class SourceError(RuntimeError):
    """The pinned source could not be fetched, verified, exported or imported; the message says why and what to do."""


def runtime_source_root(state_dir: Union[str, Path]) -> Path:
    """<state dir>/runtime/llama.cpp/src (next to the runtime installs of llama_server.runtime_root)."""
    return Path(state_dir) / "runtime" / "llama.cpp" / "src"


def source_dir(state_dir: Union[str, Path], commit: str) -> Path:
    """The cache folder of a commit; 12 hex characters on Windows, to stay clear of the 260-character path limit."""
    if not _SHA1.fullmatch(commit):
        raise SourceError("a commit is 40 lower-case hex characters")
    return runtime_source_root(state_dir) / (commit[:12] if os.name == "nt" else commit)


# --- git ---------------------------------------------------------------------------------------------------------

def _is_local(url: str) -> bool:
    return url.startswith("file://") or "://" not in url


def _git_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _PASS_ENV}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "LC_ALL": "C",
        "LANG": "C",
    })
    return env


def _git_config(allow_file: bool) -> List[str]:
    config = {
        "core.hooksPath": os.devnull,
        "core.fsmonitor": "false",
        "core.autocrlf": "false",
        "credential.helper": "",
        "protocol.allow": "never",
        "protocol.https.allow": "always",
        "protocol.file.allow": "always" if allow_file else "never",
        "gc.auto": "0",
        "maintenance.auto": "false",
        "advice.detachedHead": "false",
        "init.defaultBranch": "main",
        "transfer.fsckObjects": "true",
    }
    args: List[str] = []
    for key, value in config.items():
        args += ["-c", f"{key}={value}"]
    return args


class Git:
    """Runs git with the restrictions above. `runner` is subprocess.run, replaceable in tests."""

    def __init__(self, runner: Optional[Runner] = None, git: Optional[str] = None):
        self.runner = runner or subprocess.run
        self.git = git or shutil.which("git")

    def __call__(self, args: Sequence[str], *, cwd: Optional[Path] = None, allow_file: bool = False,
                 timeout: int = LOCAL_TIMEOUT, check: bool = True) -> "subprocess.CompletedProcess[str]":
        if not self.git:
            raise SourceError("git was not found; install git (the pinned source is fetched and verified with it)")
        argv = [self.git, *_git_config(allow_file), *args]
        try:
            result = self.runner(argv, cwd=str(cwd) if cwd else None, env=_git_env(), capture_output=True,
                                 text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise SourceError(f"git {args[0]} did not finish within {timeout} s") from None
        except OSError as e:
            raise SourceError(f"git could not be started: {e.strerror or e}") from None
        if check and result.returncode != 0:
            raise SourceError(f"git {args[0]} failed (exit {result.returncode}): {_tail(result.stderr)}")
        return result


def _tail(text: Optional[str], limit: int = 400) -> str:
    """The end of git's error output for a message: one line, control characters removed, shortened."""
    clean = "".join(ch if ch.isprintable() else " " for ch in (text or "").strip())
    clean = " ".join(clean.split())
    return clean[-limit:] if clean else "no error output"


# --- read-only trees ---------------------------------------------------------------------------------------------

def _make_read_only(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            mode = os.stat(p).st_mode
            os.chmod(p, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        if os.name != "nt":
            os.chmod(dirpath, stat.S_IMODE(os.stat(dirpath).st_mode) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def remove_tree(path: Path) -> None:
    """Removes a (read-only) tree of the source cache or a build folder. Links are removed, never followed: a link
    that a build left behind must not make the host change or delete what it points to."""
    def retry(func, p, _exc):  # read-only files and folders: make writable, retry once
        try:
            os.chmod(os.path.dirname(p), stat.S_IRWXU)
            if os.path.islink(p):
                os.unlink(p)  # os.chmod would change the link's target
                return
            os.chmod(p, stat.S_IRWXU)
            func(p)
        except OSError:
            pass
    if path.is_symlink():
        path.unlink()
        return
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=retry)


# --- verification ------------------------------------------------------------------------------------------------

def verify_source(path: Path, commit: str, *, git: Optional[Git] = None) -> None:
    """Raises SourceError unless `path` is a checkout of exactly `commit` with no changed or added files. Files git
    ignores count too: llama.cpp ignores tools/ui/dist/, and its CMake embeds a web UI found there into llama-server."""
    git = git or Git()
    if not (path / ".git").is_dir():
        raise SourceError(f"{path} is not a git checkout")
    head = git(["rev-parse", "HEAD"], cwd=path).stdout.strip()
    if head != commit:
        raise SourceError(f"{path} holds commit {head[:12] or '?'}, not the pinned {commit[:12]}")
    status = git(["status", "--porcelain=v1", "--untracked-files=normal", "--ignored=traditional"], cwd=path).stdout
    if status.strip():
        raise SourceError(f"{path} has changed or added files; it is removed and fetched again")


def cached_source(source: LlamaCppSource, state_dir: Union[str, Path], *, git: Optional[Git] = None) -> Optional[Path]:
    """The verified cached tree of the pin, or None (absent or damaged). Needs no network."""
    path = source_dir(state_dir, source.commit)
    if not path.is_dir():
        return None
    try:
        verify_source(path, source.commit, git=git)
    except SourceError:
        return None
    return path


def _staging(state_dir: Union[str, Path]) -> Path:
    root = runtime_source_root(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f".staging-{secrets.token_hex(6)}"
    path.mkdir()
    return path


def _init(staging: Path, git: Git) -> None:
    git(["init", "--quiet", "--template=", str(staging)])
    # Making the tree read-only changes the files' ctime; git must not take that for a change.
    git(["config", "core.trustctime", "false"], cwd=staging)


def _install(staging: Path, source: LlamaCppSource, state_dir: Union[str, Path], git: Git) -> Path:
    """Verifies the staging checkout, makes it read-only and moves it into place."""
    git(["update-index", "-q", "--refresh"], cwd=staging, check=False)
    verify_source(staging, source.commit, git=git)
    _make_read_only(staging)
    target = source_dir(state_dir, source.commit)
    if target.exists():
        remove_tree(target)  # damaged (cached_source said so before we started)
    try:
        os.replace(staging, target)
    except OSError:
        if cached_source(source, state_dir, git=git) is None:  # another process won the race with a good tree?
            raise SourceError(f"could not move the verified source to {target}") from None
        remove_tree(staging)
    return target


def _checkout(staging: Path, git: Git, commit: str) -> None:
    git(["checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=staging)
    head = git(["rev-parse", "HEAD"], cwd=staging).stdout.strip()
    if head != commit:
        raise SourceError(f"the source holds commit {head[:12] or '?'} instead of the pinned {commit[:12]}; refused")


# --- fetch -------------------------------------------------------------------------------------------------------

def fetch_source(source: LlamaCppSource, state_dir: Union[str, Path], *, url: Optional[str] = None,
                 git: Optional[Git] = None, log: Optional[Callable[[str], None]] = None) -> Path:
    """The verified, read-only tree of the pinned commit: from the cache (no network) or fetched once by commit.
    `url` overrides the catalog's repository (a mirror; tests use a local repository)."""
    git = git or Git()
    say = log or (lambda _msg: None)
    url = url or source.repo
    if url.startswith("-"):
        raise SourceError("the repository URL must not start with '-'")
    if "://" not in url:
        url = str(Path(url).absolute())  # a local mirror: git runs in a staging folder, the path is the user's
    cached = cached_source(source, state_dir, git=git)
    if cached is not None:
        return cached
    target = source_dir(state_dir, source.commit)
    if target.exists():
        say(f"The cached source in {target} is damaged; it is fetched again.")
        remove_tree(target)
    local = _is_local(url)
    staging = _staging(state_dir)
    try:
        _init(staging, git)
        say(f"Fetching llama.cpp {source.tag} (commit {source.commit[:12]}) ...")
        fetched = git(["fetch", "--quiet", "--depth", "1", "--no-tags", url, source.commit], cwd=staging,
                      allow_file=local, timeout=GIT_TIMEOUT, check=False)
        if fetched.returncode != 0:
            # Some mirrors refuse a fetch by commit; the tag is fetched instead and must give the same commit.
            say("The server refused a fetch by commit; fetching the tag and checking its commit.")
            git(["fetch", "--quiet", "--depth", "1", "--no-tags", url, f"refs/tags/{source.tag}"], cwd=staging,
                allow_file=local, timeout=GIT_TIMEOUT)
        _checkout(staging, git, source.commit)
        return _install(staging, source, state_dir, git)
    finally:
        if staging.exists():
            remove_tree(staging)


# --- bundles for air-gapped machines -----------------------------------------------------------------------------

def manifest_path(bundle: Path) -> Path:
    return bundle.with_name(bundle.name + ".json")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_source(source: LlamaCppSource, state_dir: Union[str, Path], out: Path, *, url: Optional[str] = None,
                  git: Optional[Git] = None, log: Optional[Callable[[str], None]] = None) -> Dict[str, object]:
    """Writes `out` (a git bundle of the pinned commit) and `out`.json; fetches the source first if it is not
    cached. Returns the manifest."""
    git = git or Git()
    path = fetch_source(source, state_dir, url=url, git=git, log=log)
    out = Path(out).absolute()  # git runs inside the source cache: a relative path would land there
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{secrets.token_hex(4)}.tmp")
    try:
        git(["bundle", "create", str(tmp), "HEAD"], cwd=path)
        manifest = {
            "format": MANIFEST_FORMAT,
            "kind": "llama.cpp source bundle",
            "repo": source.repo,
            "commit": source.commit,
            "tag": source.tag,
            "bundle_size": tmp.stat().st_size,
            "bundle_sha256": _sha256(tmp),
            "framework_version": __version__,
        }
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()
    manifest_path(out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def read_manifest(bundle: Path) -> Optional[Mapping[str, object]]:
    """The validated manifest next to a bundle, None when there is none. Raises SourceError when it is unusable."""
    path = manifest_path(bundle)
    if not path.is_file():
        return None
    with open(path, "rb") as f:
        raw = f.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise SourceError(f"{path.name} is too large to be a source manifest")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise SourceError(f"{path.name} is not valid JSON") from None
    if not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT:
        raise SourceError(f"{path.name} is not a source manifest of this framework (format {MANIFEST_FORMAT})")
    commit, sha, size = data.get("commit"), data.get("bundle_sha256"), data.get("bundle_size")
    if not (isinstance(commit, str) and _SHA1.fullmatch(commit) and isinstance(sha, str)
            and re.fullmatch(r"^[0-9a-f]{64}$", sha) and isinstance(size, int) and size > 0):
        raise SourceError(f"{path.name} has missing or invalid fields")
    return data


def _bundle_ref(bundle: Path, commit: str, git: Git, cwd: Path) -> str:
    """The ref of the bundle that points at the pinned commit (HEAD for bundles of export_source)."""
    heads = git(["bundle", "list-heads", str(bundle)], cwd=cwd, allow_file=True).stdout
    refs = []
    for line in heads.splitlines()[:1000]:
        parts = line.split()
        if len(parts) == 2 and parts[0] == commit and _REF.fullmatch(parts[1]):
            refs.append(parts[1])
    if not refs:
        raise SourceError(f"{bundle.name} does not contain the pinned commit {commit[:12]}")
    return "HEAD" if "HEAD" in refs else refs[0]


def import_source(source: LlamaCppSource, state_dir: Union[str, Path], bundle: Path, *, git: Optional[Git] = None,
                  log: Optional[Callable[[str], None]] = None) -> Path:
    """Installs the pinned commit from a bundle of export_source (also one made with plain `git bundle`), verified by
    commit. Returns the cache folder."""
    git = git or Git()
    say = log or (lambda _msg: None)
    bundle = Path(bundle)
    try:
        size = bundle.stat().st_size
    except OSError as e:
        raise SourceError(f"cannot read {bundle.name}: {e.strerror or e}") from None
    if not bundle.is_file() or size == 0 or size > MAX_BUNDLE_BYTES:
        raise SourceError(f"{bundle.name} is not a source bundle (empty, not a file, or larger than "
                          f"{MAX_BUNDLE_BYTES // 1024 ** 3} GiB)")
    manifest = read_manifest(bundle)
    if manifest is not None:
        if manifest["commit"] != source.commit:
            raise SourceError(f"{bundle.name} holds commit {str(manifest['commit'])[:12]}, but this framework is "
                              f"pinned to {source.commit[:12]}; export the source with the same framework version")
        if manifest["bundle_size"] != size or manifest["bundle_sha256"] != _sha256(bundle):
            raise SourceError(f"{bundle.name} does not match its manifest (size or sha256): damaged in transit")
    cached = cached_source(source, state_dir, git=git)
    if cached is not None:
        say(f"The pinned source is already in {cached}.")
        return cached
    target = source_dir(state_dir, source.commit)
    if target.exists():
        remove_tree(target)
    staging = _staging(state_dir)
    try:
        _init(staging, git)
        ref = _bundle_ref(bundle.resolve(), source.commit, git, staging)
        # A bundle made from a depth-1 clone lacks the parent commits; marking the pinned commit as shallow lets git
        # accept it. The content is still checked by git's object hashes and the commit below.
        (staging / ".git" / "shallow").write_text(source.commit + "\n", encoding="ascii")
        git(["fetch", "--quiet", "--no-tags", str(bundle.resolve()), ref], cwd=staging, allow_file=True,
            timeout=GIT_TIMEOUT)
        _checkout(staging, git, source.commit)
        return _install(staging, source, state_dir, git)
    finally:
        if staging.exists():
            remove_tree(staging)


# --- the pin-bump check ------------------------------------------------------------------------------------------

def upstream_tag_commit(tag: str, *, repo: str, git: Optional[Git] = None, cwd: Optional[Path] = None) -> str:
    """The commit `refs/tags/<tag>` points at upstream (peeled for annotated tags), from `git ls-remote`."""
    if not _TAG.fullmatch(tag):
        raise SourceError("a llama.cpp tag looks like b11429")
    if repo.startswith("-"):
        raise SourceError("the repository URL must not start with '-'")
    git = git or Git()
    with tempfile.TemporaryDirectory(prefix="llama-pin-") as empty:
        # Never inside the caller's checkout: git would apply that repository's config (url.<x>.insteadOf could send
        # the check to another repository).
        out = git(["ls-remote", repo, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], cwd=cwd or Path(empty),
                  allow_file=_is_local(repo), timeout=120).stdout
    found: Dict[str, str] = {}
    for line in out.splitlines()[:16]:
        parts = line.split("\t")
        if len(parts) == 2 and _SHA1.fullmatch(parts[0]) and parts[1] in (f"refs/tags/{tag}",
                                                                            f"refs/tags/{tag}^{{}}"):
            found[parts[1]] = parts[0]
    commit = found.get(f"refs/tags/{tag}^{{}}") or found.get(f"refs/tags/{tag}")
    if commit is None:
        raise SourceError(f"{repo} has no tag {tag}")
    return commit


def check_pin_upstream(source: LlamaCppSource, *, repo: Optional[str] = None, git: Optional[Git] = None,
                       cwd: Optional[Path] = None) -> str:
    """Raises SourceError unless upstream's tag of the pin points at the pinned commit. Returns the commit."""
    commit = upstream_tag_commit(source.tag, repo=repo or source.repo, git=git, cwd=cwd)
    if commit != source.commit:
        raise SourceError(f"upstream's {source.tag} points at {commit}, but the catalog pins {source.commit}")
    return commit
