#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Disk rule and hardware estimate for builds (MCP design 6.5, DECISIONS round 2 item 8)

There is no fixed size cap for a model download: a 72B model must be possible when the disk and the hardware can take
it. Instead, before a build is asked about, the framework computes what lands on each file system (the download in
the build cache, the conversion margin, the output three times: in the build cache, as the staged copy next to the
output folder and as the "golden" ZIP) and refuses only when a file system cannot hold its part plus a reserve
(2 GiB or 2 % of the disk, whichever is larger) plus what other accepted calls have reserved there and not written
yet. A call under a standing approval must leave a larger reserve (20 GiB or 10 %).

Reservations, not snapshots: three builds of a 145 GB model prepared together would each see the same free space, so
an accepted build takes a reservation in a ledger in the per-user folder; it is released when the request ends, and a
reservation of a process that has ended is dropped. The check runs again when a build leaves the queue.

The hardware estimate (can this computer convert it, can the board run it) is shown in the question and in
check_build_request; it is never a refusal by itself. Every number here is an estimate and labelled as one.

What lies in the trash (orchestrator/Core/trash.py) is never counted as free: it still uses its disk until the person
empties the trash. A check says how much of a disk the trash holds, and a refusal names the trash when emptying it
would make the call fit.
"""

import json
import os
import secrets
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from orchestrator.utils.file_lock import file_lock

GIB = 1024 ** 3
GB = 1000 ** 3
RESERVE_MIN = 2 * GIB          # always left free on a file system (the per-user folder keeps room for the audit log)
RESERVE_SHARE = 0.02
STANDING_RESERVE_MIN = 20 * GIB  # a call under a standing approval leaves more, or it asks instead
STANDING_RESERVE_SHARE = 0.10
OUTPUT_COPIES = 3              # build cache, staged copy next to the output folder, golden ZIP
# Conversion margins as a share of the download (estimates until measured on the Rockchip pipeline): GGUF keeps an
# F16 intermediate until quantizing ends (the convert stage, targets/Rockchip/modules/convert_module.sh, writes it into
# the work folder; the target stage, target_module.sh, removes it after quantizing; modules/build.sh is the stage
# runner over them); RKLLM and RKNN an export of about the same size.
CONVERSION_MARGIN = {"gguf": 1.0, "rkllm": 1.0, "rknn": 1.0}
DEFAULT_MARGIN = 1.0
LEDGER_FILE = "disk_reservations.json"
LEDGER_MAX_AGE = timedelta(days=7)  # a reservation older than this is dropped (a crash left it behind)
TIGHT_SHARE = 0.85  # hardware: within 15 % of the usable memory is "tight"

DiskUsage = Callable[[str], "shutil._ntuple_diskusage"]


class InsufficientSpace(ValueError):
    """A file system cannot hold what would land on it (reason code insufficient_resources)."""
    code = "insufficient_resources"

    def __init__(self, message: str, checks: Optional[List["DiskCheck"]] = None):
        super().__init__(message)
        self.checks = checks or []
        self.next = ("Tell the person how much space is missing and where; they can free space (old builds, the "
                     "model cache) or pick a smaller model or quantization. Do not retry unchanged.")


def gb(value: float) -> str:
    """Bytes in decimal gigabytes, as people and Hugging Face show sizes."""
    if value < GB:
        return f"{value / 1000 ** 2:.0f} MB"
    return f"{value / GB:.1f} GB"


def reserve_for(disk_total: int, standing: bool = False) -> int:
    if standing:
        return max(STANDING_RESERVE_MIN, int(disk_total * STANDING_RESERVE_SHARE))
    return max(RESERVE_MIN, int(disk_total * RESERVE_SHARE))


def bits_of(quantization: Optional[str]) -> float:
    """Bits per weight a quantization type keeps, roughly (16 for F16/BF16, 32 for F32, 8 and below otherwise)."""
    q = (quantization or "").upper()
    if "F32" in q or "FP32" in q:
        return 32.0
    if "F16" in q or "BF16" in q or "FP16" in q or q in ("W16A16",):
        return 16.0
    return 8.0


def output_share(quantization: Optional[str]) -> float:
    """The output as a share of a 16-bit download: at most 0.6 for types at or below 8 bits, 1.0 for F16/BF16."""
    bits = bits_of(quantization)
    return 2.0 if bits >= 32 else 1.0 if bits >= 16 else 0.6


@dataclass(frozen=True)
class DiskNeed:
    """Bytes that land in one folder: `label` is the folder's speaking label (cache, output), never its path."""
    label: str
    path: Path
    bytes: int
    what: str


@dataclass
class DiskCheck:
    """One file system: what lands there, what is free, the reserve and what others reserved."""
    labels: List[str]
    needed: int
    free: int
    total: int
    reserve: int
    reserved_by_others: int
    device: str
    what: List[str] = field(default_factory=list)
    trash_bytes: int = 0      # in the trash on this disk: used, never counted as free

    @property
    def after(self) -> int:
        return self.free - self.reserved_by_others - self.needed

    @property
    def fits(self) -> bool:
        return self.after >= self.reserve

    @property
    def verdict(self) -> str:
        if not self.fits:
            return "does_not_fit"
        return "tight" if self.after < 2 * self.reserve else "enough"

    def sentence(self) -> str:
        where = " and ".join(self.labels)
        others = f", {gb(self.reserved_by_others)} reserved by other calls" if self.reserved_by_others else ""
        verdict = {"enough": "enough", "tight": "enough, but tight",
                   "does_not_fit": "not enough"}[self.verdict]
        trash = f" ({gb(self.trash_bytes)} more after emptying the trash)" if self.trash_bytes else ""
        return (f"needs about {gb(self.needed)} on the {where} disk (estimate); {gb(self.free)} free{trash}{others}, "
                f"{gb(self.reserve)} kept free: {verdict}")

    @property
    def fits_after_emptying_the_trash(self) -> bool:
        return not self.fits and self.trash_bytes > 0 and self.after + self.trash_bytes >= self.reserve


def build_needs(download: int, fmt: str, quantization: Optional[str], cache_dir: Path, output_dir: Path,
                cached: int = 0) -> List[DiskNeed]:
    """What a build lands where: the download (minus what is already staged for the same commit) and the conversion
    margin in the build cache, the output there once, and twice next to the output folder (staged copy, golden ZIP)."""
    margin = CONVERSION_MARGIN.get((fmt or "").lower(), DEFAULT_MARGIN)
    output = int(download * output_share(quantization))
    to_fetch = max(0, int(download) - int(cached))
    needs = []
    if to_fetch:
        needs.append(DiskNeed("cache", Path(cache_dir), to_fetch, f"download {gb(to_fetch)}"))
    needs.append(DiskNeed("cache", Path(cache_dir), int(download * margin) + output,
                          f"conversion margin {gb(int(download * margin))} and output {gb(output)}"))
    needs.append(DiskNeed("output", Path(output_dir), output * (OUTPUT_COPIES - 1),
                          f"staged output and golden ZIP {gb(output * (OUTPUT_COPIES - 1))}"))
    return needs


def nearest_existing(path: Path) -> Path:
    """`path`, or its nearest ancestor that exists (a folder not made yet is measured on the disk it will be on)."""
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


_existing = nearest_existing  # the name this module used before


def device_of(path: Path) -> str:
    """The file system a folder is on (its nearest existing ancestor's device)."""
    try:
        return str(os.stat(_existing(path)).st_dev)
    except OSError:
        return f"?{path}"


def free_space(path: Path, disk_usage: Optional[DiskUsage] = None):
    """Total, used and free bytes of the file system `path` is on (its nearest existing folder); OSError when it cannot
    be measured. The one test seam: `disk_usage`, else shutil.disk_usage looked up at each call."""
    return (disk_usage or shutil.disk_usage)(str(nearest_existing(path)))


# --- the disk watchdog and the disk-full texts (moved from BuildEngine; design 6.5, G39, G40) ----------------------

DISK_FULL_PHRASE = "No space left on device"  # in every disk-full text: self_healing_manager.DISK_FULL_PATTERN
DISK_QUOTA_PHRASE = "Disk quota exceeded"


def low_disk_floor(disk_total: int) -> int:
    """Half the reserve: below this a running build, staging or scan stops, so the per-user folder (audit log,
    permissions) always keeps room to record what happened."""
    return reserve_for(disk_total) // 2


def low_disk_text(folder: Path, what: str, disk_usage: Optional[DiskUsage] = None) -> Optional[str]:
    """Why `what` must stop when the file system of `folder` has fallen below half the reserve, or None while it has
    room (or cannot be measured). Names the disk by its label, never a path."""
    try:
        usage = free_space(folder, disk_usage)
    except OSError:
        return None
    floor = low_disk_floor(usage.total)
    if usage.free < floor:
        return (f"{what} stopped: the cache disk has only {gb(usage.free)} free, less than half of the "
                f"{gb(floor * 2)} that must stay free. {DISK_FULL_PHRASE} for the rest of the build.")
    return None


def is_disk_full(error) -> bool:
    """A full disk: ENOSPC or EDQUOT, the message a tool printed for it, or reason code insufficient_resources. Takes an
    exception or a text."""
    import errno
    if isinstance(error, str):
        return DISK_FULL_PHRASE in error or DISK_QUOTA_PHRASE in error
    return (isinstance(error, OSError) and error.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1))) or \
        is_disk_full(str(error)) or getattr(error, "code", "") == "insufficient_resources"


def disk_full_text(error: BaseException, folders: Dict[str, Path],
                   disk_usage: Optional[DiskUsage] = None) -> Optional[str]:
    """A full disk as reason insufficient_resources with the free space of each folder by its label (cache, output,
    builds; never a path); None for any other error. A folder not made yet is measured on its parent, and left out
    when that is missing too. The text keeps DISK_FULL_PHRASE, which the self-healing heuristic matches."""
    if not is_disk_full(error):
        return None
    text = str(error)
    usage = disk_usage or shutil.disk_usage
    free = []
    for label, folder in folders.items():
        folder = Path(folder)
        try:
            free.append(f"{label} disk {gb(usage(folder if folder.exists() else folder.parent).free)} free")
        except (OSError, ValueError):
            continue
    where = f" ({', '.join(free)})" if free else ""
    detail = text if DISK_FULL_PHRASE in text else f"{text} ({DISK_FULL_PHRASE})"
    return f"insufficient_resources: the disk ran full during the build{where}. {detail}"


class DiskWatchdog:
    """A daemon thread that checks the disk of `folder` every `every` seconds while `stop` (a threading.Event) is not
    set: below half the reserve it calls on_low(text) once and ends. `also_stop_when` is asked first in each round; a
    true answer ends the watch (the caller's own reason, e.g. a cancel). `check(folder, what)` gives the text
    (default low_disk_text). Qt-free; used by the build's container watchdog and its staging watcher."""

    def __init__(self, folder: Path, what: str, every: float, on_low: Callable[[str], None],
                 stop: Optional[threading.Event] = None, *, also_stop_when: Optional[Callable[[], bool]] = None,
                 check: Optional[Callable[[Path, str], Optional[str]]] = None, name: str = "disk-watch"):
        self.folder, self.what, self.every, self.on_low = Path(folder), what, every, on_low
        self.stop = stop if stop is not None else threading.Event()
        self.also_stop_when, self.check, self.text = also_stop_when, check or low_disk_text, None
        self.thread = threading.Thread(target=self.run, name=name, daemon=True)

    def run(self) -> None:
        while not self.stop.wait(self.every):
            if self.also_stop_when is not None and self.also_stop_when():
                return
            problem = self.check(self.folder, self.what)
            if problem:
                self.text = problem
                self.on_low(problem)
                return

    def start(self) -> "DiskWatchdog":
        self.thread.start()
        return self


def _disk(device: str, free: int, total: int, ledger: Optional["ReservationLedger"], exclude: str, standing: bool,
          margin: Optional[int]) -> DiskCheck:
    """The DiskCheck of one file system before anything is counted on it: its free space, the reserve (reserve_for,
    or a check's own fixed `margin`), what other calls reserved there (the ledger, without `exclude`) and its trash."""
    others = ledger.outstanding(device, exclude=exclude) if ledger is not None else 0
    reserve = int(margin) if margin is not None else reserve_for(total, standing)
    check = DiskCheck([], 0, int(free), int(total), reserve, others, device)
    if ledger is not None:
        from orchestrator.Core.trash import trash_bytes_by_device
        check.trash_bytes = trash_bytes_by_device(ledger.state_dir).get(device, 0)
    return check


def room(path: Path, needed: int = 0, *, label: str = "", what: str = "",
         ledger: Optional["ReservationLedger"] = None, exclude: str = "", standing: bool = False,
         margin: Optional[int] = None, disk_usage: Optional[DiskUsage] = None) -> DiskCheck:
    """The DiskCheck of the file system `path` is on (its nearest existing folder) for `needed` more bytes: the one
    rule every free-space check uses. The reserve is reserve_for (2 GiB or 2 %; 20 GiB or 10 % with `standing`);
    `margin` is a check's own fixed reserve instead. With `ledger`, what other calls of any process reserved there
    counts as used, and the trash there is known. OSError when the disk cannot be measured (check_disk, which checks
    many folders at once, counts such a disk as full instead). A measurement without the disk's size keeps the
    smallest reserve (2 GiB, or 20 GiB with `standing`)."""
    du = free_space(path, disk_usage)
    total = int(getattr(du, "total", 0) or 0)
    check = _disk(device_of(path), int(du.free), total, ledger, exclude, standing, margin)
    check.needed = max(0, int(needed))
    if label:
        check.labels.append(label)
    if what:
        check.what.append(what)
    return check


def check_disk(needs: Iterable[DiskNeed], ledger: Optional["ReservationLedger"] = None, exclude: str = "",
               standing: bool = False, disk_usage: Optional[DiskUsage] = None,
               margin: Optional[int] = None) -> List[DiskCheck]:
    """Per file system: needed, free, reserve, reserved by other calls. Never raises for a full disk (see require);
    a disk that cannot be measured counts as full. `margin`: a fixed reserve instead of reserve_for (see room)."""
    usage = disk_usage or shutil.disk_usage
    groups: Dict[str, DiskCheck] = {}
    for need in needs:
        if need.bytes <= 0:
            continue
        device = device_of(need.path)
        check = groups.get(device)
        if check is None:
            try:
                du = usage(str(_existing(need.path)))
                free, total = int(du.free), int(du.total)
            except OSError:
                free, total = 0, 0
            check = groups[device] = _disk(device, free, total, ledger, exclude, standing, margin)
        if need.label not in check.labels:
            check.labels.append(need.label)
        check.needed += need.bytes
        check.what.append(need.what)
    return list(groups.values())


RESERVED_NEXT_AI = ("Tell the person that a running build or download has reserved {reserved} on that disk: the call "
                    "fits once it has finished, or after they free space. Do not retry unchanged while it runs.")


def held_by_others(check: DiskCheck) -> bool:
    """The call would fit without what other calls (a running build or download) reserved on that disk."""
    return not check.fits and check.reserved_by_others > 0 and check.after + check.reserved_by_others >= check.reserve


def refusal(checks: List[DiskCheck], what: str, estimate: bool = False) -> Optional[InsufficientSpace]:
    """None when every disk holds its part; else the refusal of `what` ("the download"): the numbers (require's), the
    plain next step at the end of the message (wait for the running build or download that holds the space, or free
    space: the person's English text, l10n_facts disk.no_room.reserved and action.free_disk) and as `next` for an
    AI, and the person's next steps (`steps`: the builds list, the trash)."""
    try:
        require(checks, what=what, estimate=estimate)
    except InsufficientSpace as e:
        from orchestrator.utils.localization import ui_text
        short = [c for c in checks if not c.fits]
        others = [c for c in short if held_by_others(c)]
        if others:
            reserved = gb(sum(c.reserved_by_others for c in others))
            e.args = (f"{e} {ui_text('disk.no_room.reserved', 'en', reserved=reserved)}",)
            e.next = RESERVED_NEXT_AI.format(reserved=reserved)
        else:
            e.args = (f"{e} {ui_text('action.free_disk', 'en')}",)
        from orchestrator.utils.next_steps import as_next_steps
        e.steps = as_next_steps((["build.history"] if others else [])
                                + (["trash.show"] if any(c.trash_bytes for c in short) else []))
        return e
    return None


def download_rule(margin: Optional[int] = None, ledger: Optional["ReservationLedger"] = None,
                  disk_usage: Optional[DiskUsage] = None) -> Callable[[Path, int, str], Optional[InsufficientSpace]]:
    """The disk rule of a download (PinnedDownloader's `space`): None when `needed` more bytes fit in the folder, else
    the refusal (InsufficientSpace with its check, see `refusal`). The one rule: the reserve (reserve_for, or a fixed
    `margin`) stays free beside what other calls of any process reserved there (`ledger`); a reservation of a process
    that has ended is never counted (the ledger drops it)."""
    def space(folder: Path, needed: int, what: str) -> Optional[InsufficientSpace]:
        check = room(folder, needed, label="download", what=what, ledger=ledger, margin=margin, disk_usage=disk_usage)
        return refusal([check], what)
    return space


def person_text(checks: List[DiskCheck], lang: Optional[str] = None) -> str:
    """Why a call does not fit, for the person in `lang` (English or German): the numbers of each disk that is short,
    and what to do (wait for the running build or download that holds the space, empty the trash, free space). It
    names no folder (the window says what was asked)."""
    from orchestrator.utils.localization import ui_text
    from orchestrator.utils.sizes import format_size
    lines = []
    for c in (c for c in checks if not c.fits):
        lines.append(ui_text("disk.no_room", lang, needed=format_size(c.needed, lang), free=format_size(c.free, lang),
                             reserve=format_size(c.reserve, lang)))
        if held_by_others(c):
            lines.append(ui_text("disk.no_room.reserved", lang, reserved=format_size(c.reserved_by_others, lang)))
        else:
            lines.append(ui_text("action.free_disk", lang))
        if c.trash_bytes:
            lines.append(ui_text("disk.no_room.trash", lang, trash=format_size(c.trash_bytes, lang)))
    return " ".join(lines)


def person_reason(error: BaseException, lang: Optional[str] = None) -> str:
    """The text a window shows for a failed call: a disk that cannot hold it in the person's language (person_text of
    the checks the error carries), any other error as its own text."""
    checks = [c for c in (getattr(error, "checks", None) or []) if isinstance(c, DiskCheck) and not c.fits]
    return person_text(checks, lang) if checks else str(error)


def require(checks: List[DiskCheck], what: str = "this build", estimate: bool = True) -> None:
    """InsufficientSpace with the numbers when a file system cannot hold its part plus the reserve; `what` names the
    call in the message ("this build", "the download"); `estimate` marks the sizes as estimates (a build's are)."""
    short = [c for c in checks if not c.fits]
    if short:
        detail = "; ".join(f"{' and '.join(c.labels)} disk: {gb(c.needed)} needed"
                           + (" (estimate)" if estimate else "") + f", {gb(c.free)} free"
                           + (f", {gb(c.reserved_by_others)} reserved by other calls" if c.reserved_by_others else "")
                           + f", {gb(c.reserve)} must stay free" for c in short)
        trash = [c for c in short if c.trash_bytes]
        if trash:
            fits = all(c.fits_after_emptying_the_trash for c in short)
            detail += (f". The trash holds {gb(sum(c.trash_bytes for c in trash))} there"
                       + (": emptying it would make room" if fits else "")
                       + " (only the person empties it: File > Trash... or 'llm-cli trash empty')")
        raise InsufficientSpace(f"Not enough disk space for {what}: {detail}.", checks)


# --- the reservation ledger ----------------------------------------------------------------------------------------

def _process_alive(pid: int, started: Optional[float]) -> bool:
    try:
        import psutil
        created = psutil.Process(int(pid)).create_time()
    except Exception:  # noqa: BLE001 - gone, or not inspectable: a reservation of a process that ended
        return False
    return started is None or abs(float(started) - created) < 2.0


def _this_process() -> Tuple[int, Optional[float]]:
    try:
        import psutil
        return os.getpid(), psutil.Process(os.getpid()).create_time()
    except Exception:  # noqa: BLE001
        return os.getpid(), None


class ReservationLedger:
    """Disk space accepted calls will still write, per file system, in the per-user folder (shared by the GUI, the CLI
    and every MCP server of this user). Entries of ended processes and old entries are dropped when read."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / LEDGER_FILE

    def _lock(self):
        return file_lock(self.state_dir / f".{LEDGER_FILE}.lock", timeout=10)

    def _load(self) -> List[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("reservations") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        now, kept = datetime.now(), []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("bytes"), dict):
                continue
            try:
                if now - datetime.fromisoformat(str(item.get("created"))) > LEDGER_MAX_AGE:
                    continue
            except ValueError:
                continue
            if not _process_alive(item.get("pid", 0), item.get("process_started")):
                continue
            kept.append(item)
        return kept

    def _save(self, items: List[dict]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "reservations": items}, f, indent=1)
        os.replace(tmp, self.path)

    def entries(self) -> List[dict]:
        return self._load()

    def outstanding(self, device: str, exclude: str = "") -> int:
        total = 0
        for item in self._load():
            if item.get("id") == exclude:
                continue
            total += max(0, int(item["bytes"].get(device, 0)) - int(item.get("landed", {}).get(device, 0)))
        return total

    def reserve(self, needs: List[DiskNeed], owner: str, label: str, standing: bool = False,
                disk_usage: Optional[DiskUsage] = None) -> Tuple[str, List[DiskCheck]]:
        """Checks the disks again under the ledger's lock and takes the reservation; InsufficientSpace when a file
        system no longer holds it (another call reserved the space meanwhile)."""
        with self._lock():
            checks = check_disk(needs, self, standing=standing, disk_usage=disk_usage)
            require(checks)
            items = self._load()
            pid, started = _this_process()
            rid = f"r_{secrets.token_hex(8)}"
            items.append({"id": rid, "owner": owner, "label": label[:200], "pid": pid, "process_started": started,
                          "created": datetime.now().isoformat(timespec="seconds"),
                          "bytes": {c.device: c.needed for c in checks}, "landed": {},
                          # the folders, kept in the per-user folder only (never shown), for recheck()
                          "paths": {device_of(n.path): str(_existing(n.path)) for n in needs}})
            self._save(items)
        return rid, checks

    def get(self, rid: str) -> Optional[dict]:
        return next((i for i in self._load() if i.get("id") == rid), None)

    def landed(self, rid: str, device: str, written: int) -> None:
        """Records bytes of the reservation that are on disk now (they show in the free space already)."""
        with self._lock():
            items = self._load()
            for item in items:
                if item.get("id") == rid:
                    landed = dict(item.get("landed") or {})
                    landed[device] = min(int(item["bytes"].get(device, 0)), int(written))
                    item["landed"] = landed
            self._save(items)

    def release(self, rid: str) -> bool:
        if not rid:
            return False
        try:
            with self._lock():
                items = self._load()
                kept = [i for i in items if i.get("id") != rid]
                if len(kept) != len(items) or self.path.exists():
                    self._save(kept)
                return len(kept) != len(items)
        except (OSError, TimeoutError):
            return False

    def recheck(self, rid: str, disk_usage: Optional[DiskUsage] = None) -> List[DiskCheck]:
        """When a build leaves the queue: its reserved bytes still fit beside everything else reserved since?
        InsufficientSpace otherwise. An unknown reservation (released, or of an ended process) checks nothing."""
        item = self.get(rid)
        if item is None:
            return []
        usage = disk_usage or shutil.disk_usage
        checks = []
        for device, needed in item["bytes"].items():
            path = item.get("paths", {}).get(device) if isinstance(item.get("paths"), dict) else None
            if not path:
                continue
            du = usage(path)
            remaining = max(0, int(needed) - int(item.get("landed", {}).get(device, 0)))
            checks.append(DiskCheck(["build"], remaining, int(du.free), int(du.total), reserve_for(int(du.total)),
                                    self.outstanding(device, exclude=rid), device))
        require(checks)
        return checks


# --- the hardware estimate -----------------------------------------------------------------------------------------

@dataclass
class Fit:
    """One hardware estimate: fits, tight, does_not_fit or unknown, with the numbers and where they come from."""
    state: str
    needed: Optional[int] = None
    usable: Optional[int] = None
    source: str = ""

    def sentence(self, where: str) -> str:
        if self.state == "unknown":
            return f"{where}: unknown ({self.source})"
        words = {"fits": "fits", "tight": "tight", "does_not_fit": "does not fit"}[self.state]
        return (f"{where}: {words} (about {gb(self.needed or 0)} needed, {gb(self.usable or 0)} usable; "
                f"{self.source}; estimate)")


def _verdict(needed: int, usable: int) -> str:
    if needed > usable:
        return "does_not_fit"
    return "tight" if needed > usable * TIGHT_SHARE else "fits"


def parameters_from(download: int, parameters: Optional[int]) -> Tuple[Optional[int], str]:
    """The parameter count: the Hub's safetensors metadata, else the download size read as 16-bit weights."""
    if parameters:
        return int(parameters), "parameter count from the Hub"
    if download:
        return int(download // 2), "parameter count guessed from the download size"
    return None, ""


def host_fit(fmt: str, use_imatrix: bool, parameters: Optional[int], ram_total: Optional[int],
             vram: Optional[int] = None, use_gpu: bool = False, basis: str = "") -> Fit:
    """Can this computer convert it? GGUF conversion and quantization stream tensors (little RAM); an importance
    matrix runs the model at about its Q8_0 size; RKLLM and RKNN export load the model in PyTorch at 2-3 x its BF16
    size (2.5 x used)."""
    if not parameters or not ram_total:
        return Fit("unknown", source="the model size or this computer's memory is unknown")
    fmt = (fmt or "").lower()
    if fmt == "gguf":
        needed = int(parameters * 1.0625) if use_imatrix else 2 * GIB + int(parameters * 2 * 0.05)
        what = "importance matrix" if use_imatrix else "conversion and quantization"
    else:
        needed = int(parameters * 2 * 2.5)
        what = f"{fmt.upper()} export"
    usable = int(ram_total * 0.85) + (int(vram * 0.95) if use_gpu and vram else 0)
    memory = gb(ram_total) + " RAM" + (f" and {gb(vram)} GPU memory" if use_gpu and vram else "")
    return Fit(_verdict(needed, usable), needed, usable, f"{what}; {memory} from the hardware probe; {basis}".strip("; "))


def target_fit(output: int, board_ram: Optional[int], board: Optional[str], board_max_gb: Optional[float] = None) -> Fit:
    """Can the board run it? The output plus a small context against the board's RAM from its imported probe."""
    if not board_ram:
        hint = (f"; boards of this type have at most {board_max_gb:g} GB" if board_max_gb else "")
        return Fit("unknown", source=f"no probe of this board; import one with llm-cli scan --import FILE{hint}")
    needed = int(output * 1.05) + 512 * 1024 ** 2
    usable = max(0, int(board_ram) - 512 * 1024 ** 2)
    return Fit(_verdict(needed, usable), needed, usable, f"{board or 'the board'} RAM from its probe")
