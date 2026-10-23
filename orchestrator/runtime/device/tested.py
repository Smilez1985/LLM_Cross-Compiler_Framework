#!/usr/bin/env python3
"""
"Tested on board X" (eighth round N3, seventh round 2'): a benchmark of a model on the board it was built for is the
proof that the golden artifact works on that hardware. Its result is recorded with the build of that model; publishing
it with a recipe in the hub comes later.

A record lands in <build folder>/device_tests.json (schema llm-framework.device-test/1, newest last, at most
MAX_TESTS): the board (the label the person gave it, its board and SoC model from the probe, the device id), the
board's runtime build and llama.cpp version, the model's sha256, the measured prompt and generation speed, the KV-cache
type the measurement used as llama-bench reported it (`kv_cache`, None when it did not say) and the one the board's
server is set to (`server_kv_cache`, from the deploy settings; the short run does not use it), whether the checks
passed, and the time. The build is found by the model's sha256 in the build manifests of the
output folder (build_info.json, artifacts[].sha256), never by a file name or a path: a renamed copy on the board still
finds its build, and a file that no build of this framework made is recorded with none (it stays with the board's own
record).

The board and SoC model come from the board's probe: display text only, shown as plain text and never handed to an AI.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

DEVICE_TESTS = "device_tests.json"
SCHEMA = "llm-framework.device-test/1"
MAX_TESTS = 50
MAX_BYTES = 256 * 1024
_RE_SHA = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


def _text(value: Any, limit: int = 64) -> Optional[str]:
    if value is None:
        return None
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value)).split())[:limit]
    return text or None


def _measured_kv_cache(bench: List[Dict[str, Any]]) -> Optional[str]:
    """The KV-cache type llama-bench reports for its runs (type_k and type_v), when all of them name the same one."""
    kinds = {(r.get("type_k"), r.get("type_v")) for r in bench}
    if len(kinds) != 1:
        return None
    k, v = kinds.pop()
    return _text(k, 16) if isinstance(k, str) and k == v else None


def make_entry(*, device: Any, profile: Any, report: Any, model_sha256: str, runtime_build_id: str,
               llama_cpp: str, kv_cache: Optional[str] = None) -> Dict[str, Any]:
    """The record of one benchmark on a board: `report` is the VerifyReport of the checks with the short llama-bench
    run (its "smoke" check); `kv_cache` is the type the board's deploy settings give its server, kept apart from the
    one the measurement used."""
    smoke = report.check("smoke") if report is not None else None
    bench = (smoke.data.get("bench") if smoke is not None and smoke.data else None) or []
    speeds = {("prompt_ts" if r.get("n_prompt") else "gen_ts"): r.get("avg_ts") for r in bench
              if isinstance(r.get("avg_ts"), (int, float))}
    get = getattr(profile, "get", lambda _key: None)
    return {
        "schema": SCHEMA,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "device_id": str(device.device_id),
        "label": _text(device.label),
        "board": _text(get("BOARD_MODEL")),
        "soc": _text(get("SOC_MODEL"), 32),
        "runtime_build_id": str(runtime_build_id)[:64],
        "llama_cpp": _text(llama_cpp, 32),
        "model_sha256": model_sha256,
        "kv_cache": _measured_kv_cache(bench),
        "server_kv_cache": kv_cache,
        "prompt_ts": speeds.get("prompt_ts"),
        "gen_ts": speeds.get("gen_ts"),
        "smoke_passed": bool(smoke is not None and smoke.ok),
        "checks_passed": bool(report is not None and report.ok),
    }


def builds_with_model(output_dirs: Iterable[Path], model_sha256: str) -> List[Path]:
    """The build folders below `output_dirs` whose manifest lists a model file with this sha256."""
    from orchestrator.Core.deployment_manager import list_builds
    if not _RE_SHA.fullmatch(model_sha256 or ""):
        return []
    found: List[Path] = []
    for folder in output_dirs:
        try:
            builds = list_builds(Path(folder)) if Path(folder).is_dir() else []
        except OSError:
            continue
        for info in builds:
            artifacts = info.get("artifacts") if isinstance(info.get("artifacts"), list) else []
            if any(isinstance(a, dict) and a.get("sha256") == model_sha256 for a in artifacts):
                path = Path(info["path"])
                if path not in found:
                    found.append(path)
    return found


def builds_with_board(output_dirs: Iterable[Path], device_id: str) -> List[Path]:
    """The build folders below `output_dirs` (each once) whose device_tests.json holds a record of the board
    `device_id` (the person's change of the board's host key moves them, runtime/device/board_move.py)."""
    from orchestrator.Core.deployment_manager import list_builds
    found: List[Path] = []
    seen = set()
    for folder in output_dirs:
        try:
            builds = list_builds(Path(folder)) if Path(folder).is_dir() else []
        except OSError:
            continue
        for info in builds:
            path = Path(info["path"])
            key = os.path.realpath(path)
            if key not in seen:
                seen.add(key)
                if any(row.get("device_id") == device_id for row in device_tests(path)):
                    found.append(path)
    return found


def device_tests(build_dir: Path) -> List[Dict[str, Any]]:
    """The recorded benchmarks of a build (oldest first); a damaged file reads as none."""
    try:
        with open(Path(build_dir) / DEVICE_TESTS, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
        data = json.loads(raw) if len(raw) <= MAX_BYTES else None
    except (OSError, ValueError, RecursionError):
        return []
    rows = data.get("tests") if isinstance(data, dict) and data.get("schema") == SCHEMA else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def tested_on(build_dir: Path) -> List[str]:
    """The boards a build passed its benchmark on, by name (board model from the probe, else the person's label)."""
    names: List[str] = []
    for row in device_tests(build_dir):
        if row.get("smoke_passed") and row.get("checks_passed"):
            name = _text(row.get("board")) or _text(row.get("label")) or "?"
            if name not in names:
                names.append(name)
    return names


def record_device_test(output_dirs: Iterable[Path], entry: Mapping[str, Any]) -> List[Path]:
    """Adds `entry` (make_entry) to every build of its model below `output_dirs`; returns their folders. A folder
    that cannot be written is skipped: the benchmark happened, only this copy of its result is missing."""
    from orchestrator.utils.file_lock import file_lock
    written: List[Path] = []
    for build in builds_with_model(output_dirs, str(entry.get("model_sha256") or "")):
        try:
            with file_lock(build / f".{DEVICE_TESTS}.lock"):
                rows = (device_tests(build) + [dict(entry)])[-MAX_TESTS:]
                _write(build / DEVICE_TESTS, {"schema": SCHEMA, "tests": rows})
            written.append(build)
        except (OSError, TimeoutError, ValueError):
            continue
    return written


def copy_board_rows(build_dir: Path, old_id: str, new_id: str) -> int:
    """P6, the person's change of a board's host key (runtime/device/board_move.py): next to each record of the board
    `old_id` in this build's device_tests.json the same record for `new_id` (unless it is there already), in one
    atomic write, then read back. Returns how many records of `old_id` the build holds (0: nothing to copy).
    OSError, TimeoutError or ValueError when the file cannot be written or the copies do not read back."""
    from orchestrator.utils.file_lock import file_lock
    build = Path(build_dir)
    with file_lock(build / f".{DEVICE_TESTS}.lock"):
        rows = device_tests(build)
        olds = [r for r in rows if r.get("device_id") == old_id]
        if not olds:
            return 0
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(row)
            copy = dict(row, device_id=new_id)
            if row.get("device_id") == old_id and copy not in rows:
                out.append(copy)
        if out != rows:
            _write(build / DEVICE_TESTS, {"schema": SCHEMA, "tests": out})
        back = device_tests(build)
        if any(dict(row, device_id=new_id) not in back for row in olds):
            raise ValueError("the copied device test records do not read back")
    return len(olds)


def drop_board_rows(build_dir: Path, device_id: str, *, copies_of: Optional[str] = None) -> int:
    """Removes the records of the board `device_id` from this build's device_tests.json; with `copies_of`, only those
    that are copies of a record of the board `copies_of` (the undo of copy_board_rows). Returns how many went."""
    from orchestrator.utils.file_lock import file_lock
    build = Path(build_dir)
    with file_lock(build / f".{DEVICE_TESTS}.lock"):
        rows = device_tests(build)
        originals = [r for r in rows if r.get("device_id") == copies_of] if copies_of else []

        def goes(row: Dict[str, Any]) -> bool:
            if row.get("device_id") != device_id:
                return False
            return copies_of is None or dict(row, device_id=copies_of) in originals
        kept = [r for r in rows if not goes(r)]
        if len(kept) != len(rows):
            _write(build / DEVICE_TESTS, {"schema": SCHEMA, "tests": kept})
    return len(rows) - len(kept)


def _write(path: Path, data: Dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if len(text) > MAX_BYTES:
        raise ValueError("the device test records are too large")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


__all__ = ["DEVICE_TESTS", "SCHEMA", "builds_with_board", "builds_with_model", "copy_board_rows", "device_tests",
           "drop_board_rows", "make_entry", "record_device_test", "tested_on"]
