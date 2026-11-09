#!/usr/bin/env python3
"""
Secret scan for this repository: provider API keys, tokens, private keys, certificates and files that
hold credentials must never be committed, and no commit may add personal data.

    python scripts/secret_scan.py              # tracked files of the working tree
    python scripts/secret_scan.py --staged     # what the next commit would contain (pre-commit hook)
    python scripts/secret_scan.py --push       # commits a push would publish (pre-push hook, refs on stdin;
                                               # under the pre-commit tool from PRE_COMMIT_TO_REF)
    python scripts/secret_scan.py --history    # every blob of every commit (CI)
    python scripts/secret_scan.py FILE...      # the given files

The key formats live in orchestrator/security/secret_patterns.py, the table runtime redaction uses too; add new
formats there. This script loads it by path, so it needs nothing but the checkout.

Personal data: in --staged and --push mode every line a commit adds (not the whole file, so the examples already in
the repository are not reported again) is also checked with the "code" level of
orchestrator/security/personal_data.py, loaded by path too: host addresses outside the documentation, private and
other non-routable ranges, e-mail addresses outside the domains reserved for examples, home folders of an account
that is no example name, and always the paths of an AI session's sandbox. The tracked-files, --history and FILE
modes do not run it (old commits cannot be fixed); the blueprint's scrub test checks the blueprint at its own level.

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
with it), a longer value of another format around it is still reported, and the other rules (provider keys, local
patterns, file names) never drop a finding because of the list; they also read the line without the listed value,
so a value next to a mock cannot pass as a placeholder. Never list a real credential: rotate it and rewrite the
history. A malformed line in the list (also one with a control or invisible character) is a usage error in every
mode.
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
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import AbstractSet, Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple

MAX_BYTES = 5_000_000
ALLOW_PRAGMA = "secret-scan: allow"
ALLOWLIST = ".secret-scan-allow"  # at the repository root; for --history only
# "<sha256 of the exact value, 64 lowercase hex digits><two spaces><note>": nothing looser, so a typo is an error.
ALLOWLIST_ENTRY = re.compile(r"(?P<digest>[0-9a-f]{64})  (?P<note>\S.*)")
PATTERN_TABLE = Path(__file__).resolve().parent.parent / "orchestrator" / "security" / "secret_patterns.py"
PERSONAL_DATA_RULES = PATTERN_TABLE.with_name("personal_data.py")


def _load_module(path: Path, name: str):
    """A rule module of orchestrator/security, loaded by path: the scan runs from a bare checkout and in git hooks,
    without the framework's dependencies. A process that imported it already uses that module."""
    module = sys.modules.get(f"orchestrator.security.{name}")
    if module is None:
        spec = importlib.util.spec_from_file_location(f"_{name}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _load_patterns():
    """The pattern table shared with runtime redaction."""
    return _load_module(PATTERN_TABLE, "secret_patterns")


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

try:
    _personal = _load_module(PERSONAL_DATA_RULES, "personal_data")
    find_personal_data = _personal.find_personal_data
    PERSONAL_DATA_LEVEL = _personal.CODE
    find_personal_data("", PERSONAL_DATA_LEVEL)  # fails now, not after the first added line, if the module is broken
except Exception as e:  # the same: never let a commit pass with half the checks
    print(f"secret-scan: cannot load the personal-data rules {PERSONAL_DATA_RULES}: {type(e).__name__}: {e}",
          file=sys.stderr)
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


def _hidden_character(line: str) -> bool:
    """A control, format or line separator character other than a tab: a viewer may show it as nothing, and other
    tools take some of them as line breaks, so a line holding one could hide an entry inside a comment."""
    return any(c != "\t" and unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in line)


def load_allowlist(repo: Path) -> FrozenSet[str]:
    """The digests listed in ALLOWLIST at the repository root (none without the file). Lines end at a line feed
    (CRLF too); a malformed line raises RuntimeError (exit 2) without echoing it, as it may hold a value pasted by
    mistake."""
    path = repo / ALLOWLIST
    try:
        text = path.read_bytes().decode("utf-8")  # not read_text(): it turns a lone carriage return into a line end
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeDecodeError) as e:
        raise RuntimeError(f"cannot read {path}: {type(e).__name__}: {e}") from None
    digests = set()
    # Not splitlines() either: it also breaks at \r, \v, \f, \x1c-\x1e, \x85, U+2028 and U+2029, which a reviewer's
    # diff shows inside one line.
    for number, line in enumerate(text.split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        if _hidden_character(line):
            raise RuntimeError(f"{path}:{number}: malformed line; it holds a control, format or line separator "
                               "character (a lone carriage return, a byte order mark, a zero-width space, ...)")
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


def exact_matches(line: str, allowed: AbstractSet[str]) -> List[Tuple[str, str, Tuple[int, int], bool]]:
    """(rule name, value, span, listed) for each exact-format value in line; listed: its digest is in `allowed`.
    A listed value hides nothing: it claims no part of the line, and each format is also searched inside it, so a
    longer value of another format around a mock (a PyPI- or Anthropic-shaped value that holds it) is still found."""
    found = find_exact_formats(line)
    if not allowed or not any(digest(value) in allowed for _, value in found):
        return [(kind, value, (-1, -1), False) for kind, value in found]  # as without a list
    result: List[Tuple[str, str, Tuple[int, int], bool]] = []
    spans: List[Tuple[int, int]] = []
    for fmt in EXACT_FORMATS:
        pos = 0
        while pos <= len(line):
            m = fmt.regex.search(line, pos)
            if not m:
                break
            start, end = m.span()
            if digest(m.group(0)) in allowed:
                result.append((fmt.name, m.group(0), (start, end), True))
                pos = start + 1  # a match of this format may start inside it
                continue
            if not any(start < e and s < end for s, e in spans):
                spans.append((start, end))
                result.append((fmt.name, m.group(0), (start, end), False))
            pos = max(end, start + 1)
    return result


def _without(line: str, spans: Sequence[Tuple[int, int]]) -> str:
    """line with the text of the given spans cut out."""
    kept, pos = [], 0
    for start, end in sorted(spans):
        kept.append(line[pos:max(pos, start)])
        pos = max(pos, end)
    return "".join(kept) + line[pos:]


def scan_text(where: str, text: str, local: Sequence["re.Pattern[str]"] = (), exact_only: bool = False,
              allowed: AbstractSet[str] = frozenset()) -> List[Finding]:
    """Findings of every rule in text. An exact-format value whose digest is in `allowed` comes back marked as
    allowed and hides nothing: the other rules still report it if it looks real, and they also read the line
    without it, so a value next to it is reported even where one match would take in the mock and look like a
    placeholder."""
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        # Before the marker: GitHub blocks these values whatever the line says.
        exact, listed = [], []  # the values reported, the spans of the listed ones
        for kind, value, span, is_listed in exact_matches(line, allowed):
            findings.append(Finding(where, number, kind, redact(value), exact=True, allowed=is_listed))
            if is_listed:
                listed.append(span)
            else:
                exact.append(value)
        if exact_only or ALLOW_PRAGMA in line:
            continue
        variants = [line, _without(line, listed)] if listed else [line]
        whole = find_secrets(line)
        hits = whole + ([hit for hit in find_secrets(variants[1]) if hit not in whole] if listed else [])
        for kind, match in hits:
            if not any(value in match or match in value for value in exact):  # each value reported once
                findings.append(Finding(where, number, kind, redact(match)))
        for i, rx in enumerate(local, 1):
            if any(rx.search(variant) for variant in variants):
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
    pairs = [(meta.split()[2], meta.split()[3], path) for meta, path in entries]
    findings += scan_added(repo, [(old, new, path, path + " (staged)") for old, new, path in pairs])
    return findings


# --- Personal data on the lines a commit adds ------------------------------------------------------------------------

_HUNK = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _null(sha: str) -> bool:
    return set(sha) == {"0"}


def added_lines(repo: Path, old: str, new: str, data: bytes) -> Dict[int, str]:
    """{line number in the new blob: text} of the lines the change from blob `old` to blob `new` adds (every line
    of a new file). data: the new blob's content."""
    lines = data.decode("utf-8", "replace").splitlines()
    if _null(old):
        return dict(enumerate(lines, 1))
    patch = _git(repo, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-a", "-U0", old, new)
    added: Dict[int, str] = {}
    number = None
    for row in patch.splitlines():
        hunk = _HUNK.match(row)
        if hunk:
            number = int(hunk.group(1))
        elif number is not None and row.startswith("+"):
            if 0 < number <= len(lines):
                added[number] = lines[number - 1]  # the blob's own text, whatever the patch encoding did
            number += 1
    return added


def personal_findings(where: str, path: str, lines: Dict[int, str]) -> List[Finding]:
    """Personal data on the given lines of one file, reported redacted. The marker spares a documented example,
    never the path of an AI session's sandbox."""
    findings = []
    for number, line in sorted(lines.items()):
        for kind, value in find_personal_data(line, PERSONAL_DATA_LEVEL, path):
            if ALLOW_PRAGMA in line and kind != "AI sandbox path":
                continue
            findings.append(Finding(where, number, f"personal data: {kind}", redact(value)))
    return findings


def scan_added(repo: Path, changes: Sequence[Tuple[str, str, str, str]]) -> List[Finding]:
    """Personal data on the lines each change adds; changes: (old blob, new blob, path, where to report)."""
    findings = []
    contents = dict(_read_blobs(repo, sorted({new for _, new, _, _ in changes})))
    for old, new, path, where in changes:
        data = contents.get(new)
        if data is None or b"\0" in data[:8192] or len(data) > MAX_BYTES:
            continue
        findings += personal_findings(where, path, added_lines(repo, old, new, data))
    return findings


def _changes(repo: Path, *args: str) -> List[Tuple[str, str, str]]:
    """(old blob, new blob, path) of every added or changed file between two trees (git diff-tree args)."""
    fields = _git(repo, "diff-tree", "-r", "-z", "--raw", "--no-abbrev", "--no-renames", "--diff-filter=ACMT",
                  *args).split("\0")
    if fields and fields[0] and not fields[0].startswith(":"):
        fields = fields[1:]  # the commit id diff-tree prints for a single commit
    return [(meta.split()[2], meta.split()[3], path) for meta, path in zip(fields[0::2], fields[1::2])
            if meta.startswith(":") and meta.split()[1] != "160000"]


def push_added(repo: Path, rev_args: Sequence[str]) -> List[Finding]:
    """Personal data on the lines each commit of rev_args adds. A merge adds a line only where it differs from
    every parent (its own resolution), as git's combined diff shows it."""
    changes: List[Tuple[str, str, str, str]] = []
    merges: List[Tuple[str, List[str]]] = []
    for row in _git(repo, "rev-list", "--parents", *rev_args).splitlines():
        commit, *parents = row.split()
        if len(parents) > 1:
            merges.append((commit, parents))
            continue
        tag = f" (commit {commit[:10]})"
        diff = _changes(repo, "--root", commit) if not parents else _changes(repo, parents[0], commit)
        changes += [(old, new, path, path + tag) for old, new, path in diff]
    findings = scan_added(repo, changes)
    for commit, parents in merges:
        per_parent = [{path: (old, new) for old, new, path in _changes(repo, parent, commit)} for parent in parents]
        paths = set.intersection(*(set(p) for p in per_parent))
        contents = dict(_read_blobs(repo, sorted({per_parent[0][p][1] for p in paths})))
        for path in sorted(paths):
            new = per_parent[0][path][1]
            data = contents.get(new)
            if data is None or b"\0" in data[:8192] or len(data) > MAX_BYTES:
                continue
            numbers = set.intersection(*(set(added_lines(repo, p[path][0], new, data)) for p in per_parent))
            lines = data.decode("utf-8", "replace").splitlines()
            findings += personal_findings(f"{path} (commit {commit[:10]})", path,
                                          {n: lines[n - 1] for n in numbers})
    seen, unique = set(), []
    for f in findings:  # the same line added by two commits of one push (a cherry-pick) is reported once
        key = (f.where.rsplit(" (commit", 1)[0], f.kind, f.sample, f.line)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


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
            findings = scan_revs(repo, revs, local) + push_added(repo, revs) if revs else []
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
    print("secret-scan: possible credentials or personal data found - remove them (and rotate a key) before "
          "committing:", file=sys.stderr)
    for f in findings:
        print(f"  {f}", file=sys.stderr)
    if not all(f.exact for f in findings):
        print(f"Documented fake values may carry the marker '{ALLOW_PRAGMA}' on the same line.", file=sys.stderr)
    if any(f.exact for f in findings):
        print(textwrap.fill(EXACT_FORMAT_HELP, 110), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
