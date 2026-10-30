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
  not its origin. With `settings` in offline mode it follows the mirror's rule below (the own network only, pinned).

Git runs with an argument list (never a shell), a cleared environment, no user or system git configuration, no hooks,
no credential helpers or prompts, and only the https transport (plus local files for bundles and local mirrors).

Offline mode (fetch_source, export_source and upstream_tag_commit with `settings`; DECISIONS round 24 L-NET, round 26
Q4, f, g): a network repository is reached only in the person's own network (a local mirror), judged by the one
network-locality rule (utils/locality.check_reach, with the person's opt-outs), and git connects only to the local
addresses that were checked (http.curloptResolve, git 2.37 or newer; a name with local and public addresses only to its
local ones), so a DNS answer that changes in between cannot reach a public host; it uses no proxy (an empty http.proxy
and no proxy variables: a proxy would resolve the name itself) and follows no redirect (http.followRedirects=false: a
redirect could lead to a host nobody checked). An international host name is refused until it is written in its xn--
form: Python (IDNA 2003) and git's curl (IDNA 2008) convert a few characters differently, so the checked name might not
be the one curl looks up. A fetch that fails drops the lookup (locality.forget), so the next one asks again. A local
file or path needs no network.
Bundle files and remote output are untrusted: sizes are capped and every value is checked before it is used.
"""

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union

from orchestrator import __version__
from orchestrator.runtime.catalog import LlamaCppSource
from orchestrator.utils.helpers import sha256_file as _sha256
from orchestrator.utils.helpers import windowless_flags

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


def catalog_pin(catalog=None) -> LlamaCppSource:
    """The llama.cpp pin of configs/llama_runtime.yml (or of `catalog`): what export-source, import-source and every
    build use (CLI and GUI). Raises SourceError when the catalog has none."""
    if catalog is None:
        from orchestrator.runtime.catalog import load_catalog
        catalog = load_catalog()
    source = catalog.llama_cpp.source
    if source is None:
        raise SourceError("configs/llama_runtime.yml has no llama_cpp.source (the pinned commit).")
    return source


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


def _git_env(offline: bool = False) -> Dict[str, str]:
    """The cleared environment git runs with; in offline mode without the proxy variables (git connects to the
    checked address of the mirror itself, never through a proxy that would resolve the name on its own)."""
    env = {k: v for k, v in os.environ.items() if k in _PASS_ENV and not (offline and k.lower().endswith("_proxy"))}
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


def idn_refusal(host: str, what: str = "the repository") -> Optional[str]:
    """The plain refusal of an international host name in offline mode (round 26 g), with the xn-- form to write
    instead (IDNA 2008 as the idna package and curl's libidn2 give it, else Python's IDNA 2003); None for an ASCII
    name."""
    if not host or host.isascii():
        return None
    try:
        import idna
        ascii_form = idna.encode(host, uts46=True).decode("ascii")
    except Exception:  # noqa: BLE001 - the idna package missing or the name not valid in IDNA 2008
        try:
            ascii_form = host.encode("idna").decode("ascii")
        except UnicodeError:
            ascii_form = "xn--..."
    from orchestrator.utils.localization import ui_text
    return ui_text("net.idn", "en", host=host, what=what, ascii=ascii_form)


def offline_reach(url: str, settings: Any, what: str = "the llama.cpp source repository") -> Optional[str]:
    """In offline mode (the settings `settings`), the git option value that pins the repository at `url` to the
    local addresses the network-locality rule checked ("HOST:PORT:ADDRESS[,ADDRESS...]" for http.curloptResolve,
    every checked local address in the resolver's order, which curl tries in turn, never a public one of a name with
    both; HOST spelled as git and curl look it up, a trailing dot kept); SourceError when the repository is not on
    this computer or in the person's own network, or its host is an international name not written in its xn-- form
    (idn_refusal). None for a local file or path, when offline mode is off, or for an address (nothing to resolve: git
    connects to the address that was checked). fetch_source and upstream_tag_commit pass git the other options of
    offline mode in every case (no proxy, no redirect)."""
    from urllib.parse import urlsplit

    from orchestrator.utils.locality import check_reach, offline_mode, parse_address
    if _is_local(url) or not offline_mode(settings):
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    refused = idn_refusal(host, what)
    if refused:
        raise SourceError(refused)
    verdict = check_reach(url, settings)
    if not verdict.local_addresses:
        raise SourceError(verdict.refusal(what))
    if parse_address(host) is not None:
        return None
    checked = verdict.local_addresses
    # curl takes no zone in the address list: a link-local address with one is left out (unless it is the only one)
    addresses = [a for a in checked if "%" not in a] or [checked[0].split("%", 1)[0]]
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    return f"{host}:{port}:" + ",".join(f"[{a}]" if ":" in a else a for a in addresses)


def offline_git_options(url: str, settings: Any, what: str = "the llama.cpp source repository") -> Dict[str, Any]:
    """The options a git call that reaches `url` gets in offline mode: {"resolve": pin} for a name (offline_reach),
    {"offline": True} for an address (no proxy, no redirect either way); {} for a local file or path or when offline
    mode is off. SourceError as offline_reach."""
    from orchestrator.utils.locality import offline_mode
    pin = offline_reach(url, settings, what)
    if _is_local(url) or not offline_mode(settings):
        return {}
    return {"resolve": pin} if pin else {"offline": True}


def _forget_host(url: str) -> None:
    """A fetch from `url` failed: its lookup is dropped (locality.forget), so the next one asks the resolver again."""
    from urllib.parse import urlsplit

    from orchestrator.utils.locality import forget
    try:
        forget(urlsplit(url).hostname or "")
    except ValueError:
        pass


def _git_config(allow_file: bool, resolve: Optional[str] = None, offline: bool = False) -> List[str]:
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
    if offline or resolve:
        # offline mode: no proxy (an empty http.proxy switches off the one from the environment as well) and no
        # redirect, so git reaches only the checked mirror itself
        config["http.proxy"] = ""
        config["http.followRedirects"] = "false"
    if resolve:   # offline mode: the checked addresses of the repository's host (offline_reach)
        config["http.curloptResolve"] = resolve
    args: List[str] = []
    for key, value in config.items():
        args += ["-c", f"{key}={value}"]
    return args


class Git:
    """Runs git with the restrictions above. `runner` is subprocess.run, replaceable in tests. Started the way the
    repository's one helper starts a console program (utils/helpers.execute_command): stdin closed, output that is not
    UTF-8 decoded with replacement characters (a non-UTF-8 message never ends in a traceback), and on Windows no
    console window (windowless_flags: the Boards window and Ditto's runtime export run it from the GUI)."""

    def __init__(self, runner: Optional[Runner] = None, git: Optional[str] = None):
        self.runner = runner or subprocess.run
        self.git = git or shutil.which("git")

    def __call__(self, args: Sequence[str], *, cwd: Optional[Path] = None, allow_file: bool = False,
                 timeout: int = LOCAL_TIMEOUT, check: bool = True, resolve: Optional[str] = None,
                 offline: bool = False) -> "subprocess.CompletedProcess[str]":
        """`resolve` (offline_reach) pins the mirror's host to its checked addresses; with it, or with `offline`,
        git also uses no proxy and follows no redirect (offline mode)."""
        if not self.git:
            raise SourceError("git was not found; install git (the pinned source is fetched and verified with it)")
        offline = offline or bool(resolve)
        argv = [self.git, *_git_config(allow_file, resolve, offline), *args]
        try:
            result = self.runner(argv, cwd=str(cwd) if cwd else None, env=_git_env(offline), capture_output=True,
                                 text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                                 creationflags=windowless_flags())
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


def remove_tree(path: Path, *, label: str = "") -> List[Path]:
    """Deprecated name of orchestrator.utils.atomic.remove_tree, the one removal of the framework's own folders
    (moved there by chain win-hygiene so the Core and the runtime share it): a (read-only) tree of the source cache or
    a build folder is removed, links are removed and never followed (a link that a build left behind must not make
    the host change or delete what it points to), and what could not be removed is returned and logged."""
    from orchestrator.utils.atomic import remove_tree
    return remove_tree(path, label=label)


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
                 git: Optional[Git] = None, log: Optional[Callable[[str], None]] = None,
                 settings: Any = None) -> Path:
    """The verified, read-only tree of the pinned commit: from the cache (no network) or fetched once by commit.
    `url` overrides the catalog's repository (a mirror; tests use a local repository). With `settings` in offline mode
    the repository must be in the person's own network and git connects to its checked address (offline_reach)."""
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
    # offline mode: only the own network, at the checked local addresses; git also uses no proxy and follows no
    # redirect (also for a mirror given by its address)
    pinned = offline_git_options(url, settings)
    staging = _staging(state_dir)
    try:
        _init(staging, git)
        say(f"Fetching llama.cpp {source.tag} (commit {source.commit[:12]}) ...")
        fetched = git(["fetch", "--quiet", "--depth", "1", "--no-tags", url, source.commit], cwd=staging,
                      allow_file=local, timeout=GIT_TIMEOUT, check=False, **pinned)
        if fetched.returncode != 0:
            # Some mirrors refuse a fetch by commit; the tag is fetched instead and must give the same commit.
            say("The server refused a fetch by commit; fetching the tag and checking its commit.")
            try:
                git(["fetch", "--quiet", "--depth", "1", "--no-tags", url, f"refs/tags/{source.tag}"], cwd=staging,
                    allow_file=local, timeout=GIT_TIMEOUT, **pinned)
            except SourceError:
                if pinned:
                    _forget_host(url)   # the checked addresses did not answer: the next fetch asks again
                raise
        _checkout(staging, git, source.commit)
        return _install(staging, source, state_dir, git)
    finally:
        if staging.exists():
            remove_tree(staging)


# --- bundles for air-gapped machines -----------------------------------------------------------------------------

def manifest_path(bundle: Path) -> Path:
    return bundle.with_name(bundle.name + ".json")


def export_source(source: LlamaCppSource, state_dir: Union[str, Path], out: Path, *, url: Optional[str] = None,
                  git: Optional[Git] = None, log: Optional[Callable[[str], None]] = None,
                  settings: Any = None) -> Dict[str, object]:
    """Writes `out` (a git bundle of the pinned commit) and `out`.json; fetches the source first if it is not
    cached (in offline mode only from the person's own network, as fetch_source with `settings`). Returns the
    manifest."""
    git = git or Git()
    path = fetch_source(source, state_dir, url=url, git=git, log=log, settings=settings)
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

def upstream_tag_commit(tag: str, *, repo: str, git: Optional[Git] = None, cwd: Optional[Path] = None,
                        settings: Any = None) -> str:
    """The commit `refs/tags/<tag>` points at upstream (peeled for annotated tags), from `git ls-remote`. With
    `settings` in offline mode the repository must be in the person's own network, and git reaches it as a mirror
    (offline_git_options: the checked local addresses, no proxy, no redirect); SourceError otherwise."""
    if not _TAG.fullmatch(tag):
        raise SourceError("a llama.cpp tag looks like b11429")
    if repo.startswith("-"):
        raise SourceError("the repository URL must not start with '-'")
    pinned = offline_git_options(repo, settings, "the repository of the pin check")
    git = git or Git()
    with tempfile.TemporaryDirectory(prefix="llama-pin-") as empty:
        # Never inside the caller's checkout: git would apply that repository's config (url.<x>.insteadOf could send
        # the check to another repository).
        try:
            out = git(["ls-remote", repo, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], cwd=cwd or Path(empty),
                      allow_file=_is_local(repo), timeout=120, **pinned).stdout
        except SourceError:
            if pinned:
                _forget_host(repo)
            raise
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
                       cwd: Optional[Path] = None, settings: Any = None) -> str:
    """Raises SourceError unless upstream's tag of the pin points at the pinned commit. Returns the commit. `settings`:
    offline mode and the person's opt-outs (upstream_tag_commit)."""
    commit = upstream_tag_commit(source.tag, repo=repo or source.repo, git=git, cwd=cwd, settings=settings)
    if commit != source.commit:
        raise SourceError(f"upstream's {source.tag} points at {commit}, but the catalog pins {source.commit}")
    return commit
