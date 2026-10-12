#!/usr/bin/env python3
"""
The hardware probes the framework knows, by id (MCP design T5): this computer's probe ("local") and the probes of
boards, imported from a file (`llm-cli scan --import FILE`, the GUI's "Import from File") or taken over SSH
(`llm-cli scan --device`). All of them live in the schema-2 profile store (orchestrator/runtime/profile.py,
<state dir>/hardware/): local.txt, and devices/<id>.txt with a small .json holding the person's label.

A tool or a prompt names a probe only by its id; nothing here reads a probe from a path an AI gave. The store keeps
only values the probe schema validates and never a host name (schema 2 has no such key; a schema-1 file loses it
when converted), and volatile values are never stored.

The local probe runs without a terminal: the Python probe (orchestrator/runtime/hardware_probe.py, every tool it
starts gets stdin=DEVNULL and its own timeout) under an overall time limit, or the fallback script with stdin from
/dev/null, NON_INTERACTIVE=true, a private working folder (never the caller's current folder, which for an MCP server
is the client's project) and the same time limit.
"""

import os
import platform
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from orchestrator.runtime.profile import (DEVICE_ID, HardwareProfile, ProfileError, ProfileStore, default_state_dir,
                                          new_device_id, parse_profile, read_profile)

PROBE_TIMEOUT = 120.0            # seconds for a whole local probe
MAX_PROBE_FILE = 256 * 1024      # an imported probe file (a real one is a few KiB)
LOCAL_ID = "local"
PROBE_ID_RE = re.compile(r"^(?:local|[0-9a-f]{16})$", re.ASCII)
LIST_LIMIT = 20
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
IMPORT_NEXT = ("Import the board's probe with 'llm-cli scan --import FILE' or in the GUI (Import from File), or run "
               "probe_local_hardware when the board is this computer.")


class ProbeError(ValueError):
    """A refusal or failure about a probe, with a reason code and the next step (the tool registry's shape)."""

    def __init__(self, message: str, code: str = "failed", next: str = "", detail: str = ""):
        super().__init__(message)
        self.code, self.next = code, next
        self.detail = detail  # the underlying error's text, for the person's terminal only (it can name paths)


@dataclass(frozen=True)
class TargetMatch:
    """A target that fits a probe: its id, the board within it (if one matched) and why, in plain words."""
    target: str
    board: Optional[str]
    reason: str


@dataclass(frozen=True)
class ProbeInfo:
    """One probe in the list: its id, the person's label, where it comes from and a few validated facts."""
    probe_id: str
    label: str
    source: str            # local | imported | device (probed over SSH)
    updated: str
    os: Optional[str]
    arch: Optional[str]
    soc: Optional[str]
    board: Optional[str]
    needs_reprobe: bool


def _stamp(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return ""


def _canon(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# The probe's ARCH against a target's architecture_family (target.yml).
_ARCH_FAMILIES = {"arm64": ("aarch64", "arm64"), "armv7": ("armv7l", "armv7", "arm"), "armv6": ("armv6", "arm"),
                  "x86_64": ("x86_64", "amd64", "x64"), "riscv64": ("riscv64",)}


def match_target(profile: HardwareProfile, target_manager: Any) -> Optional[TargetMatch]:
    """The target that fits a probe best, from validated probe values only (SOC_MODEL, SOC_VENDOR, NPU_VENDOR, ARCH)
    against each target's boards, vendor and architecture family: a board named like the probe's SoC is the strongest
    match, then the SoC's or the NPU's vendor. None when nothing fits; an architecture that differs rules a target
    out."""
    if profile is None or target_manager is None:
        return None
    soc, arch = _canon(profile.get("SOC_MODEL")), profile.get("ARCH") or ""
    vendors = {v for v in (_canon(profile.get("SOC_VENDOR")), _canon(profile.get("NPU_VENDOR"))) if v}
    best: Optional[Tuple[int, TargetMatch]] = None
    try:
        metas = target_manager.list_targets()
    except Exception:  # noqa: BLE001 - no targets known: nothing matches
        return None
    for meta in metas:
        tid = str(meta.get("id") or "")
        if not tid:
            continue
        family = _canon(meta.get("architecture_family") or "")
        if family and arch and family not in {_canon(a) for a in _ARCH_FAMILIES.get(arch, (arch,))}:
            continue
        boards: List[str] = []
        try:
            caps = target_manager.get_capabilities(tid)
            boards = list(caps.boards) if caps else []
        except Exception:  # noqa: BLE001
            boards = []
        if not boards:
            data = target_manager.get_target(tid) or {}
            boards = [str(b.get("name")) for b in data.get("supported_boards") or [] if isinstance(b, dict)]
        board = next((b for b in boards if soc and _canon(b) == soc), None)
        if board:
            found = (3, TargetMatch(tid, board, f"the probe's SoC {profile.get('SOC_MODEL')} is a board of this target"))
        elif _canon(meta.get("vendor") or "") in vendors:
            found = (2, TargetMatch(tid, None, f"the probe's SoC or NPU vendor is {meta.get('vendor')}"))
        else:
            continue
        if best is None or found[0] > best[0]:
            best = found
    return best[1] if best else None


class HardwareProbes:
    """The probes of this user, by id, over the profile store."""

    def __init__(self, state_dir: Union[str, Path, None] = None, *, store: Optional[ProfileStore] = None,
                 scripts_dir: Path = SCRIPTS_DIR):
        self.store = store or ProfileStore(state_dir)
        self.scripts_dir = Path(scripts_dir)
        self.notes: List[str] = []  # what the last run_local had to fall back from (for the person)

    @classmethod
    def for_framework(cls, framework: Any) -> "HardwareProbes":
        config = getattr(framework, "config", None)
        try:
            state = getattr(config, "user_state_dir", None)
        except OSError:
            state = None
        return cls(Path(state) if isinstance(state, (str, Path)) else default_state_dir())

    # --- reading ------------------------------------------------------------------------------------------------

    @staticmethod
    def check_id(probe_id: str) -> str:
        probe_id = str(probe_id or "").strip()
        if not PROBE_ID_RE.fullmatch(probe_id):
            raise ProbeError(f"'{probe_id[:40]}' is not a probe id (a probe id is 'local' or 16 hex characters).",
                             code="invalid_value", next="Call get_hardware_probe without probe_id for the probes there are.")
        return probe_id

    def get(self, probe_id: str) -> HardwareProfile:
        """The stored profile of a probe; ProbeError unknown_id when there is none."""
        probe_id = self.check_id(probe_id)
        try:
            profile = self.store.load_local() if probe_id == LOCAL_ID else self.store.load_device(probe_id)
        except (ProfileError, OSError, ValueError):
            raise ProbeError(f"The stored probe {probe_id} is damaged and cannot be read.", code="failed",
                             next="Probe the hardware again (" + IMPORT_NEXT + ")") from None
        if profile is None:
            raise ProbeError(f"There is no probe {probe_id}.", code="unknown_id",
                             next="Call get_hardware_probe without probe_id for the probes there are. " + IMPORT_NEXT)
        return profile

    def text(self, probe_id: str) -> Optional[str]:
        """The stored probe as a schema-2 file (for prompts); None when there is none."""
        try:
            return self.get(probe_id).to_text()
        except ProbeError:
            return None

    def info(self, probe_id: str, profile: Optional[HardwareProfile] = None) -> ProbeInfo:
        probe_id = self.check_id(probe_id)
        profile = profile or self.get(probe_id)
        if probe_id == LOCAL_ID:
            label, source, path = "this computer", "local", self.store.local_path()
        else:
            meta = self.store.device_info(probe_id)
            label = meta.get("label") or profile.get("BOARD_MODEL") or "unnamed board"
            source = "device" if meta.get("address") else "imported"
            path = self.store.device_path(probe_id)
        return ProbeInfo(probe_id=probe_id, label=label, source=source, updated=_stamp(path), os=profile.os,
                         arch=profile.arch, soc=profile.get("SOC_MODEL"), board=profile.get("BOARD_MODEL"),
                         needs_reprobe=profile.needs_reprobe)

    def list(self, limit: int = LIST_LIMIT) -> List[ProbeInfo]:
        """The newest probes first (at most `limit`); a damaged stored file is left out."""
        ids = ([LOCAL_ID] if self.store.local_path().is_file() else []) + self.store.list_devices()
        out = []
        for probe_id in ids:
            try:
                out.append(self.info(probe_id))
            except ProbeError:
                continue
        out.sort(key=lambda p: p.updated, reverse=True)
        return out[:max(0, int(limit))]

    # --- adding -------------------------------------------------------------------------------------------------

    def import_text(self, data: Union[str, bytes], label: Optional[str] = None) -> str:
        """Stores a board's probe file (schema 2, or schema 1 converted: what the schema does not know, the host
        name among it, is dropped) under a new random id and returns that id."""
        if len(data) > MAX_PROBE_FILE:
            raise ProbeError(f"The probe file is larger than {MAX_PROBE_FILE // 1024} KiB; it is not a probe file.",
                             code="invalid_value", next="Import the target_hardware_config.txt the probe wrote.")
        try:
            profile = parse_profile(data)
        except (ProfileError, ValueError) as e:
            raise ProbeError(f"The file is not a hardware probe the framework can read: {e}", code="invalid_value",
                             next="Run the probe on the board (scripts/hardware_probe.sh or llm-cli scan) and import "
                                  "the file it wrote.") from None
        label = " ".join(str(label or "").split()) or profile.get("BOARD_MODEL") or "imported board"
        device_id = new_device_id()
        while self.store.device_path(device_id).exists():  # practically never
            device_id = new_device_id()
        try:
            self.store.save_device(device_id, profile, label=label[:64])
        except ValueError as e:
            raise ProbeError(f"The probe could not be stored: {e}", code="invalid_value",
                             next="Give a label of 1 to 64 printable characters.") from None
        return device_id

    def import_file(self, path: Union[str, Path], label: Optional[str] = None) -> str:
        """Imports a probe file the person chose (CLI or GUI; never a path an AI gave)."""
        path = Path(path)
        try:
            with open(path, "rb") as f:
                data = f.read(MAX_PROBE_FILE + 1)
        except OSError as e:
            raise ProbeError(f"The probe file cannot be read: {e.strerror or e}", code="invalid_value",
                             next="Check the file name.") from None
        return self.import_text(data, label)

    def run_local(self, *, use_script: bool = False, timeout: float = PROBE_TIMEOUT,
                  probe: Optional[Callable[[], HardwareProfile]] = None,
                  output: Optional[Path] = None) -> Tuple[HardwareProfile, str]:
        """Probes this computer, stores the result as the "local" probe and returns (profile, how). The Python probe
        runs under the time limit; when it fails (or `use_script`), the fallback script runs without a terminal in a
        private working folder. `output`: where the script writes its file (default: that folder)."""
        profile, how, problem = None, "Python probe", ""
        self.notes = []
        if not use_script:
            try:
                profile = self._python_probe(probe, timeout)
            except ProbeError as e:
                problem = str(e)
                self.notes.append(f"{e}{' (' + e.detail + ')' if e.detail else ''} Using the fallback script.")
        if profile is None:
            how = "fallback script"
            try:
                profile = self._script_probe(timeout, output)
            except ProbeError as e:
                raise ProbeError(f"{problem} {e}".strip(), code=e.code, next=e.next) from None
        try:
            self.store.save_local(profile)
        except (OSError, ValueError) as e:
            raise ProbeError(f"The probe ran, but storing it as this computer's probe failed: {e}", code="failed",
                             next="Check the per-user folder with 'llm-cli doctor'.") from None
        return profile, how

    def _python_probe(self, probe: Optional[Callable[[], HardwareProfile]], timeout: float) -> HardwareProfile:
        if probe is None:
            from orchestrator.runtime.hardware import local_profile
            state = self.store.state_dir

            def probe() -> HardwareProfile:
                return local_profile(disk_path=str(state) if state.is_dir() else None)
        box: Dict[str, Any] = {}

        def run():
            try:
                box["profile"] = probe()
            except Exception as e:  # noqa: BLE001 - reported below, the fallback script may still work
                box["error"] = e

        worker = threading.Thread(target=run, name="hardware-probe", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            raise ProbeError(f"The Python probe did not finish within {int(timeout)} s.", code="failed",
                             next="Run 'llm-cli scan' in a terminal to see which step hangs.")
        if "error" in box:
            raise ProbeError(f"The Python probe failed ({box['error'].__class__.__name__}).", code="failed",
                             next="Run 'llm-cli scan' in a terminal for details.", detail=str(box["error"])[:300])
        return box["profile"]

    def work_dir(self) -> Path:
        """The probe's own working folder in the per-user folder (mode 0700)."""
        folder = self.store.root / "work"
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return folder

    def _script_probe(self, timeout: float, output: Optional[Path]) -> HardwareProfile:
        windows = platform.system() == "Windows"
        script = self.scripts_dir / ("hardware_probe.ps1" if windows else "hardware_probe.sh")
        if not script.is_file():
            raise ProbeError("The fallback probe script is missing from this installation.", code="failed",
                             next="Reinstall the framework or run the Python probe.")
        work = self.work_dir()
        out = Path(output) if output is not None else work / "target_hardware_config.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)  # a file of an earlier run must not count as this run's result
        cmd = (["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-OutputFile", str(out)] if windows else ["sh", str(script), "--output", str(out)])
        env = dict(os.environ, NON_INTERACTIVE="true")
        try:
            # stdin from /dev/null: over stdio the inherited stdin is the MCP protocol stream; cwd: never the caller's.
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=work,
                           env=env, timeout=timeout, check=True)  # nosec B603
        except subprocess.TimeoutExpired:
            raise ProbeError(f"The probe script did not finish within {int(timeout)} s.", code="failed",
                             next="Run the script in a terminal to see which step hangs.") from None
        except (subprocess.CalledProcessError, OSError) as e:
            raise ProbeError(f"The probe script failed ({e.__class__.__name__}).", code="failed",
                             next="Run 'llm-cli scan --script' in a terminal for details.") from None
        if not out.is_file():
            raise ProbeError("The probe script ran but wrote no file.", code="failed",
                             next="Run 'llm-cli scan --script' in a terminal for details.")
        try:
            return read_profile(out)
        except (ProfileError, ValueError) as e:
            raise ProbeError(f"The probe script wrote a file the framework cannot read: {e}", code="failed",
                             next="Run 'llm-cli scan' again.") from None


def is_probe_id(value: str) -> bool:
    return bool(PROBE_ID_RE.fullmatch(str(value or ""))) and (value == LOCAL_ID or bool(DEVICE_ID.fullmatch(value)))
