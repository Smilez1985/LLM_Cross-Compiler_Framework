#!/usr/bin/env python3
"""
System packages on a board, from its own network or a board preload (DESIGN, Deployment to boards: "A board installs
the system packages its components need...").

llmcf-install's typed action `install_package` (rust/llmcf-install/src/package.rs) installs packages of the board's
distribution that a signed list names: a bundle's install_package actions (the block field `system_packages`), or,
without a bundle, only its own clock keeper fake-hwclock (SELF_PACKAGES). This module is the framework's side:

- package-facts (a read-only gate operation, no root): the board's distribution, release and architecture, its
  package manager, the hosts of its package sources, its installed packages and the board preloads taken in; kept in
  the board's package record (<state>/hardware/devices/<id>.packages.json) for a preload made while the board is away;
- package-root: `install_package plan|apply SCOPE NAME...` as root through the sudo rule of the board's root readiness
  only (sudo -n). The plan (a dry run) comes first, the person says yes in the CLI or the Boards window, then apply;
  every apply, also a refused or failed one, goes into the audit log and the package record. No AI tool reaches it:
  an AI reads the record's facts and may propose the step (tools_deploy list_devices);
- in enterprise mode the board's own sources must be the company's internal mirror: online_problem() refuses the
  online way when a source host is not in the own network by the one locality rule (utils/locality.py);
- the board preload: write_preload() writes preload.sh and preload.ps1 for exactly the packages asked for, on the
  board's distribution, release and architecture (scripts/deb_preload.py, carried inside them); take_preload() checks
  the archive on the PC (deb_preload.check_archive: SHA256SUMS, the signatures with the pinned keys, every hash) and
  copies it over the SSH session into the board account's packages/<scope>/ (package-take unpacks it as the account),
  or takes one the person brought to the board by USB (a file in the account's home);
- the clock keeper: keeper_proposal() names fake-hwclock for a board without a working RTC and without a time keeper.
"""

import logging
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from orchestrator.remote import gate
from orchestrator.remote.gate import define
from orchestrator.runtime.bundle import installer as inst
from orchestrator.runtime.bundle.gate_ops import _FIXED_FNS, places_sh

logger = logging.getLogger(__name__)

SELF_PACKAGES = ("fake-hwclock",)
BUNDLE_STORE = "bundles"   # generator.STORE_DIR
KEEPER = "fake-hwclock"
BOARD_SCOPE = "board"
PACKAGES_REL = gate.PLACE_HOME_REL + "/packages"
MAX_PACKAGES = 16
RE_PACKAGE = re.compile(r"[a-z0-9][a-z0-9+.-]{1,63}(?:=[0-9][A-Za-z0-9.+~:-]{0,63})?", re.ASCII)
RE_SCOPE = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}", re.ASCII)
FACTS_TIMEOUT = 60.0
# apt reading its lists and installing on a small board with a slow card or mirror takes minutes
ROOT_TIMEOUT = 1800.0
TAKE_TIMEOUT = 600.0
AUDIT_TOOL = "board_package_install"
TAKE_AUDIT_TOOL = "board_package_preload"
DISTRIBUTIONS = {"debian": "debian", "ubuntu": "ubuntu", "raspbian": "raspbian"}
INSTALL_QUESTION = ("Install {packages} on board {board} as root through llmcf-install's install_package ({source})? "
                    "The plan above shows what comes with them; nothing is removed.")


class PackageError(ValueError):
    """A package step that cannot run (the message says why and what to do)."""


def package_name(word: str) -> str:
    return word.split("=", 1)[0]


def check_words(words: Sequence[str]) -> Tuple[str, ...]:
    """The package words (NAME or NAME=VERSION by the distribution's rules), 1 to MAX_PACKAGES, each name once."""
    words = tuple(str(w) for w in words)
    if not 1 <= len(words) <= MAX_PACKAGES:
        raise PackageError(f"1 to {MAX_PACKAGES} packages")
    bad = [w for w in words if not RE_PACKAGE.fullmatch(w)]
    if bad:
        raise PackageError(f"not a package of the distribution (NAME or NAME=VERSION): {bad[0]!r}")
    names = [package_name(w) for w in words]
    if len(set(names)) != len(names):
        raise PackageError("a package twice")
    return words


def check_scope(scope: str) -> str:
    """A bundle id, or "board" (the packages outside a bundle: the clock keeper)."""
    if not RE_SCOPE.fullmatch(scope or ""):
        raise PackageError(f"not a bundle id: {scope!r}")
    return scope


# --- the board's package facts (no root, changes nothing) --------------------------------------------------------------

FACTS_SCRIPT = "\n".join([
    'for llmcf_f in /etc/os-release /usr/lib/os-release; do [ -r "$llmcf_f" ] && break; done',
    'llmcf_v() { sed -n "s/^$1=//p" "$llmcf_f" 2>/dev/null | head -n 1 | tr -d "\\"\'" | tr -cd "A-Za-z0-9._ -"; }',
    'echo "id=$(llmcf_v ID)"; echo "like=$(llmcf_v ID_LIKE)"; echo "codename=$(llmcf_v VERSION_CODENAME)"',
    'if command -v dpkg >/dev/null 2>&1; then echo "arch=$(dpkg --print-architecture 2>/dev/null)"; fi',
    "if command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1; then echo manager=apt; "
    "else for llmcf_m in dnf yum zypper pacman apk opkg; do if command -v $llmcf_m >/dev/null 2>&1; then "
    "echo manager=$llmcf_m; break; fi; done; fi",
    "[ -f /etc/armbian-release ] && echo armbian=yes",
    "for llmcf_s in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do "
    '[ -f "$llmcf_s" ] || continue; grep -v "^[[:space:]]*#" "$llmcf_s" 2>/dev/null | '
    "grep -oE '(https?|ftp|file|cdrom|mirror|tor)(\\+[a-z]+)?:/+[A-Za-z0-9._:@-]*' | sed 's/^/source=/'; done "
    "| sort -u | head -n 64",
    'for llmcf_p in "$HOME"/' + PACKAGES_REL + '/*/manifest.txt; do [ -f "$llmcf_p" ] || continue; '
    'llmcf_n=${llmcf_p%/manifest.txt}; echo "taken=${llmcf_n##*/}"; done',
    "if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='pkg ${db:Status-Abbrev} ${Package} ${Version}\\n'"
    " 2>/dev/null | awk '$2 == \"ii\" { print \"pkg \" $3 \" \" $4 }' | head -n 20000; fi",
])
define("package-facts", (), lambda rest="": FACTS_SCRIPT,
       doc="the board's distribution, release, architecture, package manager, source hosts, the board preloads taken "
           "in and its installed packages (reads only)")


@dataclass(frozen=True)
class PackageFacts:
    """What package-facts found: the os-release ID (and ID_LIKE), the release codename, dpkg's architecture, the
    package manager (apt, dnf, ...; "" none), Armbian, the source addresses (scheme://host), the board preloads
    taken in, and the installed packages (NAME -> VERSION)."""
    id: str = ""
    like: str = ""
    codename: str = ""
    arch: str = ""
    manager: str = ""
    armbian: bool = False
    sources: Tuple[str, ...] = ()
    taken: Tuple[str, ...] = ()
    installed: Dict[str, str] = field(default_factory=dict)

    @property
    def distribution(self) -> str:
        """debian, ubuntu or raspbian (by ID, else by ID_LIKE: DietPi and the like are Debian); "" otherwise."""
        if self.id in DISTRIBUTIONS:
            return DISTRIBUTIONS[self.id]
        like = self.like.split()
        return "ubuntu" if "ubuntu" in like else "debian" if "debian" in like else ""

    @property
    def archives(self) -> Tuple[str, ...]:
        """The extra archives the board uses: raspberrypi (Raspberry Pi OS), armbian."""
        out = []
        if any(re.search(r"(^|[./])raspberrypi\.(com|org)$", _host(s)) for s in self.sources) or self.id == "raspbian":
            out.append("raspberrypi")
        if self.armbian or any("armbian" in _host(s) for s in self.sources):
            out.append("armbian")
        return tuple(out)

    def as_record(self) -> Dict[str, Any]:
        return {"id": self.id, "like": self.like, "codename": self.codename, "arch": self.arch,
                "manager": self.manager, "armbian": self.armbian, "sources": list(self.sources),
                "taken": list(self.taken), "installed": dict(self.installed)}

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "PackageFacts":
        installed = record.get("installed") if isinstance(record.get("installed"), dict) else {}
        return cls(str(record.get("id") or ""), str(record.get("like") or ""), str(record.get("codename") or ""),
                   str(record.get("arch") or ""), str(record.get("manager") or ""), bool(record.get("armbian")),
                   tuple(str(s) for s in record.get("sources") or ()), tuple(str(s) for s in record.get("taken") or ()),
                   {str(k): str(v) for k, v in installed.items()})


def _host(url: str) -> str:
    rest = url.split("://", 1)[-1].split("/", 1)[0]
    return rest.rsplit("@", 1)[-1].split(":", 1)[0].lower()


def parse_facts(text: str) -> PackageFacts:
    values: Dict[str, str] = {}
    sources, taken, installed = [], [], {}
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("pkg "):
            parts = line.split()
            if len(parts) == 3 and re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,63}(:[a-z0-9-]+)?", parts[1]) \
                    and re.fullmatch(r"[0-9][A-Za-z0-9.+~:-]{0,63}", parts[2]):
                installed[parts[1].split(":", 1)[0]] = parts[2]
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key == "source" and len(sources) < 64 and re.fullmatch(r"[a-z+]{2,16}:/+[A-Za-z0-9._:@-]*", value):
            sources.append(value)
        elif key == "taken" and RE_SCOPE.fullmatch(value):
            taken.append(value)
        elif key in ("id", "like", "codename", "arch", "manager", "armbian") and key not in values:
            values[key] = value.strip()[:64]
    word = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
    pick = lambda key: values.get(key, "") if word.fullmatch(values.get(key, "")) else ""  # noqa: E731
    return PackageFacts(pick("id"), " ".join(w for w in values.get("like", "").split() if word.fullmatch(w)),
                        pick("codename"), pick("arch"), pick("manager"), values.get("armbian") == "yes",
                        tuple(sources), tuple(taken), installed)


def read_facts(run: Callable[..., Any]) -> PackageFacts:
    """package-facts over `run` (a board session's run)."""
    result = run(gate.script("package-facts"), timeout=FACTS_TIMEOUT)
    if int(getattr(result, "status", 1)) != 0:
        raise PackageError("the board did not answer its package facts (an older gate: pair it again)")
    return parse_facts(getattr(result, "out", ""))


def _store(state_dir: Path):
    from orchestrator.runtime.profile import ProfileStore
    return ProfileStore(Path(state_dir))


def load_record(state_dir: Path, device_id: str) -> Dict[str, Any]:
    try:
        return _store(state_dir).load_packages(device_id)
    except ValueError:
        return {}


def save_record(state_dir: Path, device_id: str, changes: Dict[str, Any]) -> None:
    try:
        store = _store(state_dir)
        record = store.load_packages(device_id)
        record.update({k: v for k, v in changes.items() if v is not None})
        store.save_packages(device_id, record)
    except (OSError, ValueError) as e:
        logger.debug("The package record of board %s was not written: %s", device_id, e)


def record_facts(state_dir: Path, device_id: str, facts: PackageFacts, now: Callable[[], float] = time.time) -> None:
    save_record(state_dir, device_id, {"facts": facts.as_record(),
                                       "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now()))})


def stored_facts(state_dir: Path, device_id: str) -> Optional[PackageFacts]:
    record = load_record(state_dir, device_id).get("facts")
    return PackageFacts.from_record(record) if isinstance(record, dict) else None


# --- what a board needs ------------------------------------------------------------------------------------------------

def bundle_packages(state_dir: Path, bundle_id: str) -> Tuple[str, ...]:
    """The packages of the built bundle `bundle_id` (its manifest's install_package actions; read from the bundle
    store's folder, <state>/bundles/<id>/manifest.json, with the manifest's own checks: the generator's store is not
    imported here, the board operations import this module)."""
    from orchestrator.runtime.bundle.manifest import ManifestError, load_manifest
    path = Path(state_dir) / BUNDLE_STORE / check_scope(bundle_id) / "manifest.json"
    try:
        manifest = load_manifest(path.read_bytes())
    except (OSError, ManifestError, ValueError) as e:
        raise PackageError(f"no bundle {bundle_id} in this framework's store, or its manifest does not read: {e}")
    words: List[str] = []
    for action in manifest.root_actions:
        if action.action == inst.PACKAGE_ACTION:
            words += [w for w in action.packages or () if w not in words]
    return tuple(words)


def keeper_proposal(state_dir: Path, device_id: str) -> Tuple[str, ...]:
    """("fake-hwclock",) when the board's last clock reading shows no working RTC and no time keeper (and its last
    package reading does not show fake-hwclock installed); () otherwise."""
    from orchestrator.runtime.device import board_clock
    clock = board_clock.clock_state(Path(state_dir), device_id)
    if clock["rtc"] in ("", "yes") or clock["keeper"] not in ("none",):
        return ()
    facts = stored_facts(state_dir, device_id)
    if facts is not None and KEEPER in facts.installed:
        return ()
    return (KEEPER,)


def scope_words(state_dir: Path, scope: str, words: Sequence[str]) -> Tuple[str, Tuple[str, ...]]:
    """(scope, words) of a request: for a bundle the words default to all of its packages and must be among them;
    for "board" only the clock keeper."""
    scope = check_scope(scope)
    if scope == BOARD_SCOPE:
        words = check_words(words or SELF_PACKAGES)
        if any(w not in SELF_PACKAGES for w in words):
            raise PackageError("without a bundle only the clock keeper fake-hwclock is installed (a bundle names its "
                               "own packages: its blocks' system_packages)")
        return scope, words
    listed = bundle_packages(state_dir, scope)
    if not listed:
        raise PackageError(f"bundle {scope} names no system packages")
    words = check_words(words or listed)
    if any(w not in listed for w in words):
        raise PackageError(f"bundle {scope} names only {', '.join(listed)}")
    return scope, words


# --- enterprise mode: the board's own sources must be the internal mirror ----------------------------------------------

def online_problem(facts: PackageFacts, config: Any, check: Optional[Callable[[str, Any], Any]] = None) -> str:
    """Why the online way is refused ("" when it is not): in enterprise mode every source of the board must be in the
    own network by the one locality rule (the company's internal mirror); a board with no source it can name is
    refused too. Private mode takes the board's own sources as they are."""
    from orchestrator.Core.config_manager import framework_mode
    if framework_mode(config) != "enterprise":
        return ""
    if not facts.sources:
        return ("enterprise mode installs only from the company's internal mirror, and no package source of the board "
                "could be read; use a board preload")
    if check is None:
        from orchestrator.utils import locality
        check = locality.check_reach
    for source in facts.sources:
        if source.startswith(("file:", "cdrom:")):
            continue
        try:
            local = bool(getattr(check(source, config), "local", False))
        except Exception:  # noqa: BLE001 - a source that cannot be judged is not the internal mirror
            local = False
        if not local:
            return (f"enterprise mode installs only from the company's internal mirror, and the board's source "
                    f"{source} is not in the own network; set up the board's sources to the internal mirror, or use a "
                    "board preload")
    return ""


# --- llmcf-install's install_package (as root, through its sudo rule only) -------------------------------------------

def _root_body(action: str, scope: str, rest: str) -> str:
    """package-root with its values in place: llmcf-install's install_package action from its fixed root-owned place,
    through its sudoers drop-in only (sudo -n; never a password). Any llmcf-install the board's root readiness put there
    will do; one too old for the action, or a sudo rule written before it, answers not-ready."""
    refuse = 'echo "llmcf-bundle: result={0} changed=0 {1}"; exit 3'
    return "\n".join([
        gate.signed_sh(),
        places_sh(),
        _FIXED_FNS,
        f'case {action} in plan|apply) ;; *) ' + refuse.format("refused", "not plan or apply") + " ;; esac",
        f'case {scope} in ""|-*|.*|*[!a-z0-9._-]*) ' + refuse.format("refused", "not a bundle id") + " ;; esac",
        f'llmcf_sc={scope}; [ "$llmcf_sc" = {BOARD_SCOPE} ] && llmcf_sc=-',
        f'[ -n "{rest}" ] || {{ ' + refuse.format("refused", "no package") + "; }",
        "command -v sudo >/dev/null 2>&1 || { " + refuse.format(
            "not-ready", "the board has no sudo (set up the board's root readiness; nothing was changed)") + "; }",
        "llmcf_fixed_ok || { " + refuse.format(
            "not-ready", f"{inst.ROOT_HELPER_FILE} is not at its root-owned place on the board (set up the board's "
                         "root readiness; nothing was changed)") + "; }",
        f"llmcf_dropin_ok {inst.PACKAGE_ACTION} {action} || {{ " + refuse.format(
            "not-ready", "the board's sudo rule for llmcf-install does not take the package action (set up the "
                         "board's root readiness again; nothing was changed)") + "; }",
        f'sudo -n "$llmcf_fx" {inst.PACKAGE_ACTION} {action} "$llmcf_sc" {rest} </dev/null 2>&1',
        "llmcf_rc=$?",
        # an llmcf-install older than the action answers its usage (exit 2): it needs the readiness's update
        '[ $llmcf_rc -ne 2 ] || { ' + refuse.format(
            "not-ready", f"{inst.ROOT_HELPER_FILE} at its root-owned place is older than the package action (update "
                         "it through the board's root readiness; nothing was changed)") + "; }",
        "exit $llmcf_rc",
    ])


def _root_refusal(args) -> str:
    action, scope, words = args[0], args[1], args[2:]
    if action not in ("plan", "apply") or not RE_SCOPE.fullmatch(scope):
        return "not plan or apply, or not a bundle id"
    try:
        check_words(words)
    except PackageError as e:
        return str(e)
    return ""


# The board's system packages: llmcf-install's install_package as root, through the sudoers drop-in of the board's
# root readiness only. Sent only from the person's channels after their yes (the CLI's device package install and the
# Boards window's Board packages...); no tool of an AI names it.
define("package-root", ("word", "word"), lambda action, scope, rest="": _root_body(action, scope, rest),
       rest="package", rest_max=MAX_PACKAGES, refuse=_root_refusal,
       doc="llmcf-install's install_package (plan or apply packages a signed list names) at its fixed root-owned "
           "place, through its sudoers drop-in only (sudo -n)")


def _take_body(source: str, scope: str) -> str:
    """package-take: a board preload archive in the account's home (an upload's staging folder, or a file the person
    brought by USB) unpacked as the account into a new folder of its own, its entries checked (only llmcf-preload/,
    no link, kind packages, every file against SHA256SUMS), then moved to packages/<scope>/ in place of the one taken
    in before. llmcf-install checks the signatures and every package again as root before it installs."""
    no = 'echo "taken=no reason={0}"; exit 3'
    return "\n".join([
        "set -u",
        "umask 022",
        f"llmcf_a={source}",
        'case $llmcf_a in "$HOME"/*) ;; *) ' + no.format("not-in-home") + " ;; esac",
        '[ -f "$llmcf_a" ] && [ ! -L "$llmcf_a" ] || { ' + no.format("no-file") + "; }",
        f'case {scope} in ""|-*|.*|*[!a-z0-9._-]*) ' + no.format("scope") + " ;; esac",
        f'llmcf_b="$HOME"/{PACKAGES_REL}',
        'mkdir -p "$llmcf_b" || { ' + no.format("folder") + "; }",
        'llmcf_t=$(mktemp -d "$llmcf_b/.take-XXXXXXXX") || { ' + no.format("folder") + "; }",
        "trap 'cd /; rm -rf \"$llmcf_t\"' EXIT",
        # only plain entries below llmcf-preload/ (tar -tv: the type is the first letter of each line)
        'tar -tvzf "$llmcf_a" > "$llmcf_t/list" 2>/dev/null || { ' + no.format("not-tar") + "; }",
        "grep -qv '^[-d]' \"$llmcf_t/list\" && { " + no.format("entry") + "; }",
        'tar -tzf "$llmcf_a" 2>/dev/null | grep -qvE "^llmcf-preload(/|$)" && { ' + no.format("entry") + "; }",
        'tar -tzf "$llmcf_a" 2>/dev/null | grep -qE "(^|/)\\.\\.(/|$)" && { ' + no.format("entry") + "; }",
        'mkdir "$llmcf_t/x" && (cd "$llmcf_t/x" && tar -xzf "$llmcf_a" --no-same-owner 2>/dev/null) || { '
        + no.format("unpack") + "; }",
        'llmcf_x="$llmcf_t/x/llmcf-preload"',
        'grep -qx "kind=packages" "$llmcf_x/manifest.txt" 2>/dev/null || { ' + no.format("kind") + "; }",
        '(cd "$llmcf_x" && sha256sum -c --quiet SHA256SUMS >/dev/null 2>&1) || { ' + no.format("damaged") + "; }",
        'chmod -R go-w "$llmcf_x"',
        f'rm -rf "$llmcf_b/{scope}.old"; [ ! -e "$llmcf_b/{scope}" ] || mv "$llmcf_b/{scope}" "$llmcf_b/{scope}.old"',
        f'mv "$llmcf_x" "$llmcf_b/{scope}" || {{ ' + no.format("move") + "; }",
        f'rm -rf "$llmcf_b/{scope}.old"',
        'echo "taken=yes"',
    ])


define("package-take", ("path", "word"), lambda source, scope, rest="": _take_body(source, scope),
       refuse=lambda args: "" if RE_SCOPE.fullmatch(args[1]) else "not a bundle id",
       doc="a board preload archive in the account's home unpacked as the account into its packages folder (entries "
           "and SHA256SUMS checked)")


@dataclass(frozen=True)
class PackageRun:
    """What llmcf-install's install_package answered: `result` planned, ok, refused, failed, not-ready ("" nothing
    came back), `detail` the rest of its last line, `values` its key=value words (source, would, installed,
    already, pending), and its package lines (`lines`: "package NAME installed=..." and "would-install NAME V")."""
    result: str
    detail: str = ""
    values: Dict[str, str] = field(default_factory=dict)
    lines: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.result in ("ok", "planned")

    def listed(self, key: str) -> Tuple[str, ...]:
        value = self.values.get(key, "")
        return () if value in ("", "-") else tuple(value.split(","))


def parse_run(text: str) -> PackageRun:
    last, lines = "", []
    for line in (text or "").splitlines():
        if line.startswith("llmcf-bundle: result="):
            last = line[len("llmcf-bundle: "):]
        elif line.startswith(("llmcf-bundle: package ", "llmcf-bundle: would-install ", "llmcf-bundle: installed ",
                              "llmcf-bundle: preload ")):
            lines.append(line[len("llmcf-bundle: "):][:300])
    if not last:
        rest = [x.strip() for x in (text or "").splitlines() if x.strip()]
        return PackageRun("", (rest[-1] if rest else "llmcf-install gave no answer")[:200], {}, tuple(lines))
    words = last.split(" ")
    result = words[0].partition("=")[2]
    values, rest = {}, []
    for w in words[1:]:
        key, sep, value = w.partition("=")
        if sep and re.fullmatch(r"[a-z_]{1,20}", key) and key not in values:
            values[key] = value
        else:
            rest.append(w)
    return PackageRun(result, " ".join(rest)[:400], values, tuple(lines))


def run_root(run: Callable[..., Any], action: str, scope: str, words: Sequence[str]) -> PackageRun:
    """llmcf-install `install_package ACTION SCOPE WORD...` (package-root: sudo -n through its sudo rule only)."""
    result = run(gate.script("package-root", action, check_scope(scope), *check_words(words)), timeout=ROOT_TIMEOUT)
    return parse_run(getattr(result, "out", "") + "\n" + getattr(result, "err", ""))


def default_audit(state_dir: Path):
    from orchestrator.runtime.device import board_clock
    return board_clock.default_audit(Path(state_dir))


def _audit(audit: Any, tool: str, device_id: str, *, via: str, outcome: str, arguments: Dict[str, Any],
           detail: str) -> None:
    if audit is None or not callable(getattr(audit, "record", None)):
        return
    try:
        audit.record("user", tool, "external", "ask", outcome, arguments=dict(arguments, device_id=device_id),
                     approved=True, detail=detail, extra={"confirm_channel": f"framework-{via}", "answer": "accepted"})
    except Exception:  # noqa: BLE001 - logged by the audit log itself
        pass


def record_apply(state_dir: Path, device_id: str, scope: str, words: Sequence[str], done: PackageRun, *, via: str,
                 audit: Any, now: Callable[[], float] = time.time) -> None:
    """Writes an apply (also a refused or failed one) into the audit log and the board's package record."""
    _audit(audit, AUDIT_TOOL, device_id, via=via, outcome="ok" if done.result == "ok" else "error",
           arguments={"scope": scope, "packages": list(words), "source": done.values.get("source", "")},
           detail=f"llmcf-install install_package: result={done.result or 'none'} {done.detail}".strip())
    save_record(state_dir, device_id, {"last_run": {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())), "via": via, "scope": scope,
        "packages": list(words), "result": done.result or "none", "source": done.values.get("source", ""),
        "installed": list(done.listed("installed")), "detail": done.detail[:300]}})


def plan_install(session: Any, state_dir: Path, device_id: str, scope: str, words: Sequence[str], *,
                 config: Any = None) -> Tuple[PackageFacts, PackageRun]:
    """The dry run of an install: reads the board's package facts (and records them), refuses the online way in
    enterprise mode when a source is not the internal mirror (a preload taken in for the scope is the way then),
    and runs install_package plan. (facts, the plan's PackageRun)."""
    facts = read_facts(session.run)
    record_facts(state_dir, device_id, facts)
    if facts.manager not in ("apt", ""):
        raise PackageError(f"this board's package manager is {facts.manager}; llmcf-install installs packages only "
                           "with apt and dpkg (Debian, Ubuntu, Raspberry Pi OS, Armbian, DietPi)")
    if scope not in facts.taken:
        problem = online_problem(facts, config)
        if problem:
            raise PackageError(problem)
    return facts, run_root(session.run, "plan", scope, words)


def apply_install(session: Any, state_dir: Path, device_id: str, scope: str, words: Sequence[str], *, via: str,
                  audit: Any = None) -> PackageRun:
    """The install after the person's yes to the plan: install_package apply, recorded (audit log and the package
    record), the board's facts read again."""
    try:
        done = run_root(session.run, "apply", scope, words)
    except Exception as e:  # noqa: BLE001 - the record says it failed
        done = PackageRun("failed", str(e)[:200])
    record_apply(state_dir, device_id, scope, words, done, via=via,
                 audit=audit if audit is not None else default_audit(state_dir))
    try:
        record_facts(state_dir, device_id, read_facts(session.run))
    except Exception:  # noqa: BLE001 - the run itself stands
        pass
    return done


def plan_lines(run: PackageRun) -> List[str]:
    """The person's lines of a plan or an install."""
    out = [line for line in run.lines]
    source = run.values.get("source", "")
    if source:
        out.append("Source: " + ("the board preload taken in for it (checked against the distribution's signed "
                                 "lists on the board)" if source == "preload" else "the board's own package sources"))
    if run.result == "planned":
        would = run.listed("would")
        out.append("Would install: " + (", ".join(would) if would else "nothing (all in place)"))
    elif run.result == "ok":
        installed = run.listed("installed")
        out.append("Installed: " + (", ".join(installed) if installed else "nothing (all in place)"))
    elif run.result == "not-ready":
        out.append("Not done: the board's root readiness for llmcf-install is not set up for packages yet (its sudo "
                   "rule or llmcf-install is an older one). " + run.detail)
    else:
        out.append(f"Not done: {run.result or 'no answer'} {run.detail}".strip())
    return out


# --- the board preload ---------------------------------------------------------------------------------------------

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_DEB_PRELOAD: Any = None

def deb_preload():
    """scripts/deb_preload.py of this installation (standard library only; the preload scripts carry it), loaded
    once by its path (as Core/build_preload loads scripts/preload_lock.py)."""
    global _DEB_PRELOAD
    if _DEB_PRELOAD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("llmcf_deb_preload", _SCRIPTS / "deb_preload.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _DEB_PRELOAD = module
    return _DEB_PRELOAD


def recipe_for(state_dir: Path, device_id: str, scope: str, words: Sequence[str], *, distribution: str = "",
               release: str = "", arch: str = "", archives: Sequence[str] = ()) -> dict:
    """The board preload's recipe: the packages for the board's distribution, release and architecture as its last
    package reading found them (with what it had installed), or as the person names them for a board never read."""
    facts = stored_facts(state_dir, device_id)
    mod = deb_preload()
    if facts is not None and not (distribution or release or arch):
        if not (facts.distribution and facts.codename and facts.arch):
            raise PackageError("the board's last package reading names no Debian-family distribution, release and "
                               "architecture; name them (--distribution, --release, --arch)")
        distribution, release, arch = facts.distribution, facts.codename, facts.arch
        archives = archives or facts.archives
        installed: Optional[Dict[str, str]] = facts.installed or None
    else:
        if not (distribution and release and arch):
            raise PackageError("the board's packages were never read: name its distribution, release and architecture "
                               "(--distribution, --release, --arch), or reach the board once")
        installed = None
    try:
        return mod.make_recipe(scope, distribution, release, arch, check_words(words), archives, installed)
    except mod.PreloadError as e:
        raise PackageError(str(e)) from None


def write_preload(recipe: dict, out: Path) -> Tuple[Path, Path, str]:
    """preload.sh and preload.ps1 for `recipe` into `out` (their paths and what they fetch)."""
    mod = deb_preload()
    try:
        return mod.write_scripts(recipe, Path(out))
    except mod.PreloadError as e:
        raise PackageError(str(e)) from None


def check_preload(archive: Path, *, release: str = "", arch: str = "", expected: str = "") -> Dict[str, str]:
    """The archive checked on the PC (deb_preload.check_archive in a temporary folder, removed after): its manifest,
    archive_sha256 and debs. With `release`/`arch` (the board's), a preload for another board is refused."""
    from orchestrator.utils.atomic import remove_tree
    mod = deb_preload()
    work = Path(tempfile.mkdtemp(prefix="llmcf-preload-check-"))
    try:
        manifest = mod.check_archive(Path(archive), work, expected)
    except mod.PreloadError as e:
        raise PackageError(str(e)) from None
    finally:
        remove_tree(work)
    for key, want in (("release", release), ("arch", arch)):
        if want and manifest.get(key) != want:
            raise PackageError(f"this board preload is for {manifest.get('release')} on {manifest.get('arch')}; the "
                               f"board runs {release or '?'} on {arch or '?'}")
    return {k: v for k, v in manifest.items() if k != "folder"}


def take_preload(session: Any, state_dir: Path, device_id: str, archive: Path, scope: str, *, via: str,
                 audit: Any = None, say: Callable[[str], None] = lambda _t: None) -> Dict[str, str]:
    """Checks the board preload `archive` on the PC and copies it to the board over `session` (into an upload folder
    of the install folder, then package-take): the board account's packages/<scope>/ holds it afterwards."""
    from orchestrator.runtime.device.remote import RemoteShell
    from orchestrator.runtime.device.remote import read_facts as device_facts
    scope = check_scope(scope)
    facts = stored_facts(state_dir, device_id)
    manifest = check_preload(archive, release=facts.codename if facts else "", arch=facts.arch if facts else "")
    if manifest.get("scope") not in (scope, None):
        say(f"The preload was made for {manifest.get('scope')}; it is taken in for {scope}.")
    shell = session if hasattr(session, "session") else RemoteShell(session)
    root = device_facts(shell).root
    res = shell.run(gate.script("staging-create", root))
    staging = res.out.strip()
    if res.status != 0 or not staging.startswith(root + "/" + gate.STAGING_PREFIX):
        raise PackageError("no upload folder could be made on the board")
    remote = f"{staging}/packages.tar.gz"
    try:
        say(f"Copying the board preload ({Path(archive).stat().st_size // 1024} KiB) to the board...")
        shell.upload(Path(archive), remote, manifest["archive_sha256"])
        taken = _take(shell.run, remote, scope)
    finally:
        try:
            shell.run(gate.script("dir-remove", staging), again=True)
        except Exception:  # noqa: BLE001 - the upload folder is the account's scratch
            pass
    _record_take(state_dir, device_id, scope, manifest, taken, via=via, audit=audit)
    return manifest


def take_on_board(session: Any, state_dir: Path, device_id: str, path: str, scope: str, *, via: str,
                  audit: Any = None) -> Dict[str, str]:
    """Takes in a board preload the person brought to the board by USB: `path`, a file in the board account's home."""
    scope = check_scope(scope)
    taken = _take(session.run, path, scope)
    manifest = {"archive": path, "scope": scope}
    _record_take(state_dir, device_id, scope, manifest, taken, via=via, audit=audit)
    return manifest


def _take(run: Callable[..., Any], path: str, scope: str) -> str:
    res = run(gate.script("package-take", path, scope), timeout=TAKE_TIMEOUT)
    out = getattr(res, "out", "")
    if "taken=yes" in out:
        return "yes"
    reason = re.search(r"reason=([a-z-]+)", out)
    why = {"not-in-home": "the file is not in the board account's home", "no-file": "there is no such file",
           "not-tar": "it is not a readable .tar.gz", "entry": "it holds an entry a board preload never has",
           "unpack": "it could not be unpacked", "kind": "it is not a board preload of packages",
           "damaged": "a file does not match its SHA256SUMS (copy it again)"}.get(
        reason.group(1) if reason else "", "the board did not take it")
    raise PackageError(f"The board preload was not taken in: {why}.")


def _record_take(state_dir: Path, device_id: str, scope: str, manifest: Dict[str, str], taken: str, *, via: str,
                 audit: Any) -> None:
    _audit(audit if audit is not None else default_audit(state_dir), TAKE_AUDIT_TOOL, device_id, via=via,
           outcome="ok", arguments={"scope": scope, "archive_sha256": manifest.get("archive_sha256", ""),
                                    "packages": manifest.get("packages", "")},
           detail=f"board preload taken in for {scope}: {manifest.get('debs', '')}"[:300])
    record = load_record(state_dir, device_id)
    takes = dict(record.get("taken") or {})
    takes[scope] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "via": via,
                    "archive_sha256": manifest.get("archive_sha256", ""), "packages": manifest.get("debs", "")}
    save_record(state_dir, device_id, {"taken": takes})


# --- the state for the person's channels and an AI's read-only facts ---------------------------------------------------

def state(state_dir: Path, device_id: str, bundle_ids: Sequence[str] = ()) -> Dict[str, Any]:
    """The board's package state: the facts of the last reading (distribution, release, architecture, manager), the
    packages wanted (each bundle's and the clock keeper's proposal) with whether they were installed at that reading,
    the preloads taken in and the last run."""
    record = load_record(state_dir, device_id)
    facts = stored_facts(state_dir, device_id)
    wanted: Dict[str, List[str]] = {}
    for bid in bundle_ids:
        try:
            words = bundle_packages(state_dir, bid)
        except Exception:  # noqa: BLE001 - a bundle that cannot be read names nothing
            words = ()
        if words:
            wanted[bid] = list(words)
    keeper = keeper_proposal(state_dir, device_id)
    if keeper:
        wanted[BOARD_SCOPE] = list(keeper)
    missing = sorted({package_name(w) for words in wanted.values() for w in words
                      if facts is None or package_name(w) not in facts.installed})
    return {"read_at": str(record.get("read_at") or ""),
            "distribution": facts.distribution if facts else "", "release": facts.codename if facts else "",
            "arch": facts.arch if facts else "", "manager": facts.manager if facts else "",
            "archives": list(facts.archives) if facts else [], "sources": list(facts.sources) if facts else [],
            "taken": list(facts.taken) if facts else [], "wanted": wanted, "missing": missing,
            "last_run": record.get("last_run") if isinstance(record.get("last_run"), dict) else {}}


def state_lines(state_dir: Path, device_id: str, bundle_ids: Sequence[str] = ()) -> List[str]:
    s = state(state_dir, device_id, bundle_ids)
    out = [f"Last package reading: {s['read_at'] or 'never'}",
           f"Distribution: {s['distribution'] or '-'} {s['release'] or ''} on {s['arch'] or '-'} "
           f"(package manager: {s['manager'] or '-'})".replace("  ", " "),
           f"More archives: {', '.join(s['archives']) or 'none'}",
           f"Package sources: {', '.join(s['sources']) or 'not read'}",
           f"Board preloads taken in: {', '.join(s['taken']) or 'none'}"]
    for scope, words in s["wanted"].items():
        what = "the clock keeper (no working RTC, no time keeper)" if scope == BOARD_SCOPE else f"bundle {scope}"
        out.append(f"Wanted by {what}: {', '.join(words)}")
    out.append(f"Missing at the last reading: {', '.join(s['missing']) or 'nothing'}")
    run = s["last_run"]
    if run:
        out.append(f"Last install: {run.get('at')} {run.get('result')} ({run.get('source') or '-'}) "
                   f"{', '.join(run.get('installed') or ())}".rstrip())
    return out


def ai_facts(state_dir: Path, device_id: str) -> Dict[str, Any]:
    """The read-only package facts an AI may see (no source address, no account): distribution, release,
    architecture, manager, the packages missing at the last reading and the proposal; only the person installs."""
    s = state(state_dir, device_id)
    facts: Dict[str, Any] = {k: s[k] for k in ("read_at", "distribution", "release", "arch", "manager", "missing")}
    if s["missing"]:
        from orchestrator.utils.next_steps import NextStep, render
        facts["proposal"] = (f"install {', '.join(s['missing'])} on this board, from its own sources or a board "
                             "preload. " + render([NextStep("board.packages", {"device": device_id})], "ai", "en"))
    return facts


__all__ = ["AUDIT_TOOL", "BOARD_SCOPE", "INSTALL_QUESTION", "KEEPER", "MAX_PACKAGES", "PACKAGES_REL", "PackageError",
           "PackageFacts", "PackageRun", "SELF_PACKAGES", "TAKE_AUDIT_TOOL", "ai_facts", "apply_install",
           "bundle_packages", "check_preload", "check_scope", "check_words", "deb_preload", "keeper_proposal",
           "load_record", "online_problem", "parse_facts", "parse_run", "plan_install", "plan_lines", "read_facts",
           "recipe_for", "record_apply", "record_facts", "run_root", "save_record", "scope_words", "state",
           "state_lines", "stored_facts", "take_on_board", "take_preload", "write_preload"]
