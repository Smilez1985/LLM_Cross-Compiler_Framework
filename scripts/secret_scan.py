#!/usr/bin/env python3
"""
Secret scan for this repository: provider API keys, tokens, private keys, certificates and files that
hold credentials must never be committed.

    python scripts/secret_scan.py              # tracked files of the working tree
    python scripts/secret_scan.py --staged     # what the next commit would contain (pre-commit hook)
    python scripts/secret_scan.py --push       # commits a push would publish (pre-push hook, refs on stdin)
    python scripts/secret_scan.py --history    # every blob of every commit (CI)
    python scripts/secret_scan.py FILE...      # the given files

The key formats live in orchestrator/security/secret_patterns.py, the table runtime redaction uses too; add new
formats there. This script loads it by path, so it needs nothing but the checkout.

Personal strings (your e-mail address, user name, host names) belong in a pattern file that is never
committed: .git/info/secret-patterns (one literal per line, or "re:<regex>"; "#" starts a comment), or the
file named by SECRET_SCAN_PATTERNS_FILE. Findings are printed redacted; local patterns are never echoed.

A line containing "secret-scan: allow" is skipped (use it for documented fake values only).
Exit code: 0 clean, 1 findings, 2 usage or git error. Standard library only.
"""

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple

MAX_BYTES = 5_000_000
ALLOW_PRAGMA = "secret-scan: allow"
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
except OSError as e:  # never scan without the table: the commit would pass unchecked
    print(f"secret-scan: cannot load the pattern table {PATTERN_TABLE}: {e}", file=sys.stderr)
    sys.exit(2)
PATTERNS = _patterns.PATTERNS        # the same objects runtime redaction uses
PLACEHOLDER = _patterns.PLACEHOLDER

# Files that hold credentials or per-user state, whatever their content.
FORBIDDEN_NAMES = {
    ".env", ".netrc", ".pypirc", ".npmrc", "credentials.json", "secrets.store", "audit.db",
    "audit.db.anchor", "ca-bundle.crt", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "known_hosts",
    "chat_history.json", "tool_policy.json",
}
FORBIDDEN_SUFFIXES = (".pem", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk")
FORBIDDEN_PATH_ALLOW = ("tests/fixtures/",)


@dataclass
class Finding:
    where: str
    line: int
    kind: str
    sample: str

    def __str__(self) -> str:
        loc = f"{self.where}:{self.line}" if self.line else self.where
        return f"{loc}: {self.kind}" + (f" ({self.sample})" if self.sample else "")


def redact(value: str) -> str:
    return f"{value[:6]}... {len(value)} chars" if len(value) > 10 else f"{len(value)} chars"


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


def scan_text(where: str, text: str, local: Sequence["re.Pattern[str]"] = ()) -> List[Finding]:
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_PRAGMA in line:
            continue
        for kind, match in _patterns.find_secrets(line):
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


def scan_blob(where: str, data: bytes, local: Sequence["re.Pattern[str]"]) -> List[Finding]:
    if len(data) > MAX_BYTES or b"\0" in data[:8192]:
        return []
    return scan_text(where, data.decode("utf-8", "replace"), local)


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
    paths = [p for p in _git(repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").split("\0") if p]
    findings = [f for f in map(check_name, paths) if f]
    for spec, data in _read_blobs(repo, [":" + p for p in paths]):
        findings += scan_blob(spec[1:] + " (staged)", data, local)
    return findings


def scan_revs(repo: Path, rev_args: Sequence[str], local) -> List[Finding]:
    pairs = _objects(repo, rev_args)
    findings = [f for f in (check_name(p) for _, p in pairs) if f]
    paths = dict(pairs)
    for sha, data in _read_blobs(repo, [sha for sha, _ in pairs]):
        findings += scan_blob(f"{paths[sha]} (blob {sha[:10]})", data, local)
    return findings


def push_revs(lines: Iterable[str]) -> List[str]:
    """Revisions a push would publish, from git's pre-push stdin: new commits not on any remote yet."""
    revs = []
    for line in lines:
        parts = line.split()
        if len(parts) == 4 and set(parts[1]) != {"0"}:
            revs.append(parts[1])
    return revs + ["--not", "--remotes"] if revs else []


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
            revs = push_revs(sys.stdin)
            findings = scan_revs(repo, revs, local) if revs else []
        elif args.history:
            findings = scan_revs(repo, ["--all"], local)
        else:
            findings = scan_tracked(repo, local)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as e:
        print(f"secret-scan: {e}", file=sys.stderr)
        return 2

    if not findings:
        return 0
    print("secret-scan: possible credentials found - remove them (and rotate the key) before committing:",
          file=sys.stderr)
    for f in findings:
        print(f"  {f}", file=sys.stderr)
    print(f"Documented fake values may carry the marker '{ALLOW_PRAGMA}' on the same line.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
