#!/usr/bin/env python3
"""
Secret scan for this repository: provider API keys, tokens, private keys, certificates and files that
hold credentials must never be committed.

    python scripts/secret_scan.py              # tracked files of the working tree
    python scripts/secret_scan.py --staged     # what the next commit would contain (pre-commit hook)
    python scripts/secret_scan.py --push       # commits a push would publish (pre-push hook, refs on stdin;
                                               # under the pre-commit tool from PRE_COMMIT_TO_REF)
    python scripts/secret_scan.py --history    # every blob of every commit (CI)
    python scripts/secret_scan.py FILE...      # the given files

The key formats live in orchestrator/security/secret_patterns.py, the table runtime redaction uses too; add new
formats there. This script loads it by path, so it needs nothing but the checkout.

Personal strings (your e-mail address, user name, host names) belong in a pattern file that is never
committed: .git/info/secret-patterns (one literal per line, or "re:<regex>"; "#" starts a comment), or the
file named by SECRET_SCAN_PATTERNS_FILE. Findings are printed redacted; local patterns are never echoed.

A line containing "secret-scan: allow" is skipped (use it for documented fake values only), and so are obvious
placeholders - except values in the exact format of a token that GitHub push protection blocks (hf_ and 34
letters, ghp_ and 36 characters, ...; EXACT_FORMATS in the table): GitHub refuses the push however fake such a value
looks and ignores the marker, so the scan reports them in every mode, also in text files over MAX_BYTES (which the
other rules skip). Build such a test value at runtime ("hf_" "abc..." or "hf_" + "abc...") or give it a shape the
format does not allow.

Old commits cannot be fixed that way. ALLOWLIST (.secret-scan-allow at the repository root) names documented test
mocks that are already in the history by the sha256 of the exact value; --history skips an exact-format finding
whose digest is listed, and nothing else: every other mode still reports the value (GitHub may block a new commit
with it), and the other rules (provider keys, local patterns, file names) never look at the list. Never list a real
credential: rotate it and rewrite the history. A malformed line in the list is a usage error in every mode.
Exit code: 0 clean, 1 findings, 2 usage or git error. Standard library only.
"""

import argparse
import hashlib
import importlib.util
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

MAX_BYTES = 5_000_000
ALLOW_PRAGMA = "secret-scan: allow"
ALLOWLIST = ".secret-scan-allow"  # at the repository root; for --history only
# "<sha256 of the exact value, 64 lowercase hex digits><two spaces><note>": nothing looser, so a typo is an error.
ALLOWLIST_ENTRY = re.compile(r"(?P<digest>[0-9a-f]{64})  (?P<note>\S.*)")
PATTERN_TABLE = Path(__file__).resolve().parent.parent / "orchestrator" / "security" / "secret_patterns.py"


def _load_patterns():
    """The pattern table shared with runtime redaction, loaded by path: the scan runs from a bare checkout and in
    git hooks, without the framework's dependencies. A process that imported it already uses that module."""
    module = sys.modules.get("orchestrator.security.secret_patterns")
    if module is None:
        spec = importlib.util.spec_from_file_location("_secret_patterns", PATTERN_TABLE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


try:
    _patterns = _load_patterns()
    PATTERNS = _patterns.PATTERNS        # the same objects runtime redaction uses
    PLACEHOLDER = _patterns.PLACEHOLDER
    EXACT_FORMATS = _patterns.EXACT_FORMATS
    find_secrets = _patterns.find_secrets
    find_exact_formats = _patterns.find_exact_formats
except Exception as e:  # never scan without the table (missing, mid-merge, broken): the commit would pass unchecked
    print(f"secret-scan: cannot load the pattern table {PATTERN_TABLE}: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)

# Files that hold credentials or per-user state, whatever their content.
FORBIDDEN_NAMES = {
    ".env", ".netrc", ".pypirc", ".npmrc", "credentials.json", "secrets.store", "audit.db",
    "audit.db.anchor", "ca-bundle.crt", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts",
    "chat_history.json", "tool_policy.json",
}
FORBIDDEN_SUFFIXES = (".pem", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk")
FORBIDDEN_PATH_ALLOW = ("tests/fixtures/",)


EXACT_FORMAT_HELP = (
    "GitHub push protection will block a push that contains a value in a provider's exact token format, even a "
    "fake one, and it ignores the marker '{pragma}': that marker does not silence these findings either. Build "
    "such a test value at runtime instead, from adjacent string literals (\"hf_\" \"abc...\") or by concatenation "
    "(\"hf_\" + \"abc...\"), or use a value that does not match the exact format (another length, or a character "
    "the format does not allow).").format(pragma=ALLOW_PRAGMA)


@dataclass
class Finding:
    where: str
    line: int
    kind: str
    sample: str
    exact: bool = False  # in the exact format of a token that GitHub push protection blocks
    allowed: bool = False  # an exact-format value listed in ALLOWLIST; dropped in --history, never reported

    def __str__(self) -> str:
        loc = f"{self.where}:{self.line}" if self.line else self.where
        what = f"{self.kind} in its exact format: GitHub will block the push" if self.exact else self.kind
        return f"{loc}: {what}" + (f" ({self.sample})" if self.sample else "")


def redact(value: str) -> str:
    return f"{value[:6]}... {len(value)} chars" if len(value) > 10 else f"{len(value)} chars"


def digest(value: str) -> str:
    """How ALLOWLIST names a value: the sha256 hex digest of exactly that value, never the value itself."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_allowlist(repo: Path) -> FrozenSet[str]:
    """The digests listed in ALLOWLIST at the repository root (none without the file). A malformed line raises
    RuntimeError (exit 2) without echoing it, as it may hold a value pasted by mistake."""
    path = repo / ALLOWLIST
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"cannot read {path}: {type(e).__name__}: {e}") from None
    digests = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        entry = ALLOWLIST_ENTRY.fullmatch(line)
        if not entry:
            raise RuntimeError(f"{path}:{number}: malformed line; expected the sha256 of the exact value (64 "
                               "lower-case hex digits), two spaces and a note, or a comment starting with '#'")
        digests.add(entry.group("digest"))
    return frozenset(digests)


def load_local_patterns(repo: Path) -> List["re.Pattern[str]"]:
    """Personal patterns from .git/info/secret-patterns or SECRET_SCAN_PATTERNS_FILE (never committed)."""
    files = []
    env = os.environ.get("SECRET_SCAN_PATTERNS_FILE")
    if env:
        files.append(Path(env).expanduser())
    git_dir = _git(repo, "rev-parse", "--git-common-dir", check=False)
    if git_dir:
        files.append((repo / git_dir.strip()).resolve() / "info" / "secret-patterns")
    patterns = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                patterns.append(re.compile(line[3:]) if line.startswith("re:") else re.compile(re.escape(line), re.I))
            except re.error as e:
                print(f"secret-scan: ignoring bad pattern in {f}: {e}", file=sys.stderr)
    return patterns


def scan_text(where: str, text: str, local: Sequence["re.Pattern[str]"] = (), exact_only: bool = False,
              allowed: AbstractSet[str] = frozenset()) -> List[Finding]:
    """Findings of every rule in text. An exact-format value whose digest is in `allowed` comes back marked as
    allowed and leaves the other rules alone: they still report it if it looks real."""
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        # Before the marker: GitHub blocks these values whatever the line says.
        exact = []
        for kind, match in find_exact_formats(line):
            listed = bool(allowed) and digest(match) in allowed
            findings.append(Finding(where, number, kind, redact(match), exact=True, allowed=listed))
            if not listed:
                exact.append((kind, match))
        if exact_only or ALLOW_PRAGMA in line:
            continue
        for kind, match in find_secrets(line):
            if not any(value in match or match in value for _, value in exact):  # each value reported once
                findings.append(Finding(where, number, kind, redact(match)))
        for i, rx in enumerate(local, 1):
            if rx.search(line):
                findings.append(Finding(where, number, f"local pattern #{i}", ""))
    return findings


def check_name(path: str) -> Optional[Finding]:
    norm = path.replace("\\", "/")
    if norm.startswith(FORBIDDEN_PATH_ALLOW):
        return None
    name = norm.rsplit("/", 1)[-1].lower()
    if name in FORBIDDEN_NAMES or name.startswith(".env.") or name.endswith(FORBIDDEN_SUFFIXES):
        if name in (".env.example", ".env.sample", ".env.template"):
            return None
        return Finding(path, 0, "file that holds credentials or per-user state", "")
    return None


def scan_blob(where: str, data: bytes, local: Sequence["re.Pattern[str]"], allowed: AbstractSet[str] = frozenset()
              ) -> List[Finding]:
    if b"\0" in data[:8192]:
        return []
    # A text file too big for every rule still gets the exact formats: GitHub reads files of any size (it skips only
    # pushes over 50 MB), and one search rules out nearly every line.
    return scan_text(where, data.decode("utf-8", "replace"), local, exact_only=len(data) > MAX_BYTES,
                     allowed=allowed)


def _git(repo: Path, *args: str, check: bool = True, data: Optional[bytes] = None) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], input=data, capture_output=True)
    if proc.returncode != 0:
        if check:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
        return ""
    return proc.stdout.decode("utf-8", "surrogateescape")


def _read_blobs(repo: Path, specs: Sequence[str]) -> Iterator[Tuple[str, bytes]]:
    """(spec, content) for each object spec, via one `git cat-file --batch`; non-blobs are skipped."""
    if not specs:
        return
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                          input="".join(s + "\n" for s in specs).encode("utf-8", "surrogateescape"),
                          capture_output=True, check=True)
    out, pos = proc.stdout, 0
    for spec in specs:
        end = out.index(b"\n", pos)
        header = out[pos:end].decode("utf-8", "replace").split()
        pos = end + 1
        if len(header) < 3 or header[1] == "missing":
            continue
        size = int(header[2])
        content = out[pos:pos + size]
        pos += size + 1
        if header[1] == "blob":
            yield spec, content


def _objects(repo: Path, rev_args: Sequence[str]) -> List[Tuple[str, str]]:
    """(sha, path) of every blob reachable from rev_args, each blob once."""
    listing = _git(repo, "rev-list", "--objects", *rev_args)
    seen, pairs = set(), []
    for line in listing.splitlines():
        sha, _, path = line.partition(" ")
        if path and sha not in seen:
            seen.add(sha)
            pairs.append((sha, path))
    return pairs


def scan_tracked(repo: Path, local) -> List[Finding]:
    findings = []
    for path in filter(None, _git(repo, "ls-files", "-z").split("\0")):
        name_hit = check_name(path)
        if name_hit:
            findings.append(name_hit)
        try:
            data = (repo / path).read_bytes()
        except OSError:
            continue  # deleted in the working tree
        findings += scan_blob(path, data, local)
    return findings


def scan_staged(repo: Path, local) -> List[Finding]:
    # The index's object ids, not ":<path>": a name such as "0:x.py" would read as stage 0 of "x.py", and one with a
    # line break would split the batch. T: a file that replaced a symlink is a type change, not a modification.
    fields = _git(repo, "diff", "--cached", "--raw", "-z", "--no-abbrev", "--no-renames",
                  "--diff-filter=ACMT").split("\0")
    entries = list(zip(fields[0::2], fields[1::2]))  # (":<old mode> <new mode> <old id> <new id> <status>", path)
    staged: Dict[str, List[str]] = {}  # object id -> the paths with that content
    for meta, path in entries:
        staged.setdefault(meta.split()[3], []).append(path)
    findings = [f for f in (check_name(path) for _, path in entries) if f]
    for sha, data in _read_blobs(repo, list(staged)):
        for path in staged[sha]:
            findings += scan_blob(path + " (staged)", data, local)
    return findings


def scan_revs(repo: Path, rev_args: Sequence[str], local, allowed: AbstractSet[str] = frozenset()) -> List[Finding]:
    pairs = _objects(repo, rev_args)
    findings = [f for f in (check_name(p) for _, p in pairs) if f]
    paths = dict(pairs)
    for sha, data in _read_blobs(repo, [sha for sha, _ in pairs]):
        findings += scan_blob(f"{paths[sha]} (blob {sha[:10]})", data, local, allowed)
    return findings


def push_revs(lines: Iterable[str]) -> List[str]:
    """Revisions a push would publish, from git's pre-push stdin: new commits not on any remote yet."""
    revs = []
    for line in lines:
        parts = line.split()
        if len(parts) == 4 and set(parts[1]) != {"0"}:
            revs.append(parts[1])
    return revs + ["--not", "--remotes"] if revs else []


def pre_commit_push_revs(environ) -> List[str]:
    """The same for the pre-commit tool's pre-push stage: pre-commit reads git's stdin itself and hands a hook the
    pushed commit as PRE_COMMIT_TO_REF (only PRE_COMMIT_LOCAL_BRANCH when the push contains a root commit) and an
    empty stdin. pre-commit passes the first pushed branch with new commits only; the git hook checks all."""
    ref = environ.get("PRE_COMMIT_TO_REF") or environ.get("PRE_COMMIT_LOCAL_BRANCH")
    if not ref or ref.startswith("-"):
        return []
    return [ref, "--not", "--remotes"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Scan for committed credentials.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan the index (pre-commit)")
    mode.add_argument("--push", action="store_true", help="scan commits a push would publish (pre-push)")
    mode.add_argument("--history", action="store_true", help="scan every commit")
    ap.add_argument("--repo", default=None, help="repository root (default: the current one)")
    ap.add_argument("files", nargs="*", help="scan these files instead")
    args = ap.parse_args(argv)

    try:
        repo = Path(args.repo or _git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        local = load_local_patterns(repo)
        allowed = load_allowlist(repo)  # read in every mode, so the pre-commit hook already stops a malformed list
        if args.files:
            findings = []
            for f in args.files:
                hit = check_name(f)
                if hit:
                    findings.append(hit)
                try:
                    findings += scan_blob(f, Path(f).read_bytes(), local)
                except OSError as e:
                    print(f"secret-scan: cannot read {f}: {e}", file=sys.stderr)
        elif args.staged:
            findings = scan_staged(repo, local)
        elif args.push:
            revs = push_revs(sys.stdin) or pre_commit_push_revs(os.environ)
            findings = scan_revs(repo, revs, local) if revs else []
        elif args.history:
            # Only here: old commits keep the documented test mocks; anything new must not contain them.
            findings = scan_revs(repo, ["--all"], local, allowed)
        else:
            findings = scan_tracked(repo, local)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as e:
        print(f"secret-scan: {e}", file=sys.stderr)
        return 2

    skipped = [f for f in findings if f.allowed]
    findings = [f for f in findings if not f.allowed]
    if skipped:
        print(f"secret-scan: {len(skipped)} exact-format match(es) in old commits skipped: test mocks listed in "
              f"{ALLOWLIST}", file=sys.stderr)
    if not findings:
        return 0
    print("secret-scan: possible credentials found - remove them (and rotate the key) before committing:",
          file=sys.stderr)
    for f in findings:
        print(f"  {f}", file=sys.stderr)
    if not all(f.exact for f in findings):
        print(f"Documented fake values may carry the marker '{ALLOW_PRAGMA}' on the same line.", file=sys.stderr)
    if any(f.exact for f in findings):
        print(textwrap.fill(EXACT_FORMAT_HELP, 110), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
