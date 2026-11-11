#!/usr/bin/env python3
"""
The model library: what the Models tab and `llm-cli models list` show (DESIGN "GUI and the person's path", the row
"The Models tab is one list").

One list of the models on this computer and the builds made from them, read only from what the framework already
lists, so the tab, the command line and the AI tools cannot drift apart:

- local_models.models_here: the downloads in the models folder and the folders the AI may use, the Builder's own
  copies of Hugging Face models, the local inputs of earlier builds; a file in the models folder's `ditto` folder is
  Ditto's catalogue model;
- BuildService.artifact_entries: the finished builds with their artifact id (tuning reports are no models);
- F16Cache.entries: the kept F16 intermediates.

Each row carries what a person needs: name, kind, format, quantization type (a GGUF file's own general.file_type, a
build's from its manifest), size, where it lies, date, target and board of a build, the boards it was tested on, its
tokenizer check and the newest llama-bench run of this computer for the same file.

Deletion stays with each thing's owner: a build through ArtifactStore, an F16 intermediate through F16Cache, a
download in the models folder through delete_downloads() below (the person's accepted plan, built again first, audited
first, into the models folder's trash or for good). Ditto's catalogue models, files in a folder the person lent the AI,
the Builder's copies and the inputs of earlier builds are not deleted here.

It reads only; nothing here asks the network.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

KINDS = ("download", "ditto", "copy", "input", "f16", "build")
WHERE_CODES = ("models_folder", "folder", "build_cache", "earlier_build", "cache", "output", "builds", "saved")
AUDIT_TOOL = "delete_model_downloads"


@dataclass
class LibraryRow:
    """One model or build of the list. `path` (the folder or file) is for the person only; `label` is what an AI may
    name ("" when none). `gguf`: the model file llama-bench can run, when there is one."""
    key: str
    kind: str
    name: str
    format: str = ""
    quant: str = ""
    size_bytes: Optional[int] = None
    where: str = ""
    path: Optional[Path] = None
    date: str = ""
    target: str = ""
    board: str = ""
    tested_on: List[str] = field(default_factory=list)
    check: str = ""                       # the build's tokenizer and chat-template check: pass | warn | ""
    bench: Optional[Dict[str, Any]] = None   # the newest kept llama-bench run of this computer for this file
    artifact_id: str = ""
    sha256: str = ""
    label: str = ""
    gguf: Optional[Path] = None
    deletable: bool = False

    def state(self, lang: str = "en") -> str:
        """What is known about it, in plain words: tested on boards, the tokenizer check, the newest local run."""
        from orchestrator.utils.localization import ui_text
        parts = []
        if self.tested_on:
            parts.append(ui_text("models.state.tested", lang, boards=", ".join(self.tested_on)))
        if self.check in ("pass", "warn"):
            parts.append(ui_text(f"models.state.check_{self.check}", lang))
        if self.bench:
            where = self.bench.get("where")
            parts.append(ui_text("models.state.bench", lang, gen=speed(self.bench.get("gen_ts")),
                                 where=ui_text(f"bench.where.{where}", lang) if where in ("native", "sandbox")
                                 else str(where or "")))
        return "; ".join(parts)

    def where_text(self, lang: str = "en") -> str:
        from orchestrator.utils.localization import ui_text
        return ui_text(f"models.where.{self.where}", lang) if self.where in WHERE_CODES else self.where

    def kind_text(self, lang: str = "en") -> str:
        from orchestrator.utils.localization import ui_text
        return ui_text(f"models.kind.{self.kind}", lang)

    def to_dict(self) -> Dict[str, Any]:
        """The row for `llm-cli models list --json` (the person's own output: full paths included)."""
        data = asdict(self)
        data["path"] = str(self.path) if self.path else None
        data["gguf"] = str(self.gguf) if self.gguf else None
        return data


def speed(value: Any) -> str:
    return f"{float(value):.1f} t/s" if isinstance(value, (int, float)) and not isinstance(value, bool) else "-"


def _date(path: Optional[Path]) -> str:
    try:
        stamp = os.stat(path).st_mtime if path is not None else None
    except OSError:
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M") if stamp else ""


def _tree_size(path: Path) -> Optional[int]:
    try:
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return None
    total = 0
    for current, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(current, name)).st_size
            except OSError:
                continue
    return total


def _gguf_type(path: Optional[Path]) -> str:
    if path is None or path.suffix.lower() != ".gguf":
        return ""
    from orchestrator.Core.build_resources import gguf_file_type
    try:
        return gguf_file_type(path) or ""
    except Exception:  # noqa: BLE001 - an unreadable header: no type shown
        return ""


def _download_name(path: Path) -> str:
    """A download's name: the Hugging Face repository of the browser's folders (hf/<owner>--<name>/<commit>/...) with
    the file, else the file or folder name."""
    parts = path.parts
    for i, part in enumerate(parts[:-1]):
        if part == "hf" and i + 2 < len(parts) and "--" in parts[i + 1]:
            repo = parts[i + 1].replace("--", "/", 1)
            rest = parts[i + 3:]
            return f"{repo} / {'/'.join(rest)}" if rest else repo
    return path.name


def models_folder(framework: Any) -> Optional[Path]:
    config = getattr(framework, "config", None)
    get = getattr(config, "get", None)
    resolve = getattr(framework, "resolve_path", None)
    value = (get("models_dir", "models") if callable(get) else None) or "models"
    try:
        return Path(resolve(value)) if callable(resolve) else Path(str(value)).expanduser()
    except Exception:  # noqa: BLE001
        return None


def _inside(path: Path, folder: Optional[Path]) -> bool:
    if folder is None:
        return False
    try:
        real, base = path.resolve(), folder.resolve()
    except OSError:
        return False
    return real != base and base in real.parents


def _state_dir(framework: Any) -> Optional[Path]:
    value = getattr(getattr(framework, "config", None), "user_state_dir", None)
    return Path(value) if isinstance(value, (str, Path)) else None


def bench_runs(framework: Any, limit: int = 200) -> List[Dict[str, Any]]:
    """The kept llama-bench runs of this computer, newest first (runtime/bench.BenchRunner.latest)."""
    state = _state_dir(framework)
    if state is None:
        return []
    from orchestrator.runtime.bench import BenchRunner
    try:
        return BenchRunner(state).latest(limit)
    except Exception:  # noqa: BLE001 - an unreadable index: no runs shown
        return []


def _downloads(framework: Any) -> List[LibraryRow]:
    from orchestrator.Core.local_models import models_here
    try:
        found = models_here(framework)
    except Exception:  # noqa: BLE001 - a folder that cannot be read lists nothing
        return []
    base = models_folder(framework)
    ditto = base / "ditto" if base is not None else None
    rows = []
    for model in found:
        path = Path(model.path)
        if model.where == "build_cache":
            kind, name = "copy", model.repo or model.label
        elif model.where == "earlier_build":
            kind, name = "input", path.name
        elif _inside(path, ditto):
            kind, name = "ditto", path.name
        else:
            kind, name = "download", _download_name(path)
        is_gguf = path.is_file() and path.suffix.lower() == ".gguf"
        rows.append(LibraryRow(
            key=f"{kind}:{model.label}", kind=kind, name=name, format=model.format or ("gguf" if is_gguf else ""),
            quant=_gguf_type(path) if is_gguf else "", size_bytes=model.size_bytes if model.size_bytes is not None
            else _tree_size(path), where=model.where, path=path, date=_date(path),
            label=model.label if model.for_ai else "", gguf=path if is_gguf else None,
            deletable=kind == "download" and model.where == "models_folder" and _inside(path, base)))
    return rows


def _builds(framework: Any) -> List[LibraryRow]:
    service = getattr(framework, "build_service", None)
    entries = []
    if service is not None:
        try:
            entries = service.artifact_entries()
        except Exception:  # noqa: BLE001
            entries = []
    else:
        from orchestrator.Core.artifact_output import list_artifacts
        try:
            entries = [("", item.label, item.info) for item in list_artifacts(framework)]
        except Exception:  # noqa: BLE001
            entries = []
    from orchestrator.runtime.device.tested import tested_on
    rows = []
    for aid, label, info in entries:
        if not isinstance(info, dict) or info.get("kind") == "tuning":
            continue
        folder = Path(str(info.get("path") or ""))
        primary = str(info.get("primary") or "")
        artifacts = info.get("artifacts") if isinstance(info.get("artifacts"), list) else []
        sha = next((str(a.get("sha256") or "") for a in artifacts if isinstance(a, dict) and a.get("file") == primary),
                   "")
        model_file = folder / primary if primary else None
        gguf = model_file if model_file is not None and primary.lower().endswith(".gguf") and model_file.is_file() \
            and not model_file.is_symlink() else None
        check = info.get("tokenizer_check")
        try:
            boards = tested_on(folder)
        except Exception:  # noqa: BLE001
            boards = []
        source = info.get("model") or Path(str(info.get("model_source") or "?")).name
        rows.append(LibraryRow(
            key=f"build:{aid or label}", kind="build", name=str(source), format=str(info.get("format") or ""),
            quant=str(info.get("quantization") or ""), size_bytes=_tree_size(folder), where=label.split(":", 1)[0],
            path=folder, date=str(info.get("created") or "")[:16].replace("T", " ") or _date(folder),
            target=str(info.get("target") or ""), board=str(info.get("board") or ""), tested_on=boards,
            check=str(check.get("status") or "") if isinstance(check, dict) else "", artifact_id=aid, sha256=sha,
            label=label, gguf=gguf, deletable=bool(aid)))
    return rows


def _f16(framework: Any) -> List[LibraryRow]:
    from orchestrator.Core.f16_cache import F16Cache
    try:
        entries = F16Cache.for_framework(framework).entries()
    except Exception:  # noqa: BLE001
        return []
    return [LibraryRow(key=f"f16:{e.key}", kind="f16", name=e.repo, format="gguf", quant="F16",
                       size_bytes=e.size_bytes, where="cache", path=e.folder,
                       date=(e.kept or "")[:16].replace("T", " ") or _date(e.folder), label=f"f16:{e.key}",
                       gguf=e.file if e.file.is_file() else None, deletable=True)
            for e in entries]


def library(framework: Any, *, with_bench: bool = True) -> List[LibraryRow]:
    """Every row of the list: builds first (newest first), then the downloads, Ditto's models, the Builder's copies,
    the inputs of earlier builds and the F16 intermediates. Reads the disk only."""
    rows = _builds(framework) + _downloads(framework) + _f16(framework)
    if with_bench:
        runs = bench_runs(framework)
        for row in rows:
            row.bench = next((r for r in runs if (row.sha256 and r.get("sha256") == row.sha256)
                              or (r.get("model") and r.get("model") == row.name)), None)
    return rows


def matching(rows: Iterable[LibraryRow], kind: str = "", search: str = "") -> List[LibraryRow]:
    """The rows of `kind` ("" for all) whose name, format, type, target, board or place contain `search`."""
    text = (search or "").strip().lower()
    found = []
    for row in rows:
        if kind and row.kind != kind:
            continue
        hay = " ".join((row.name, row.format, row.quant, row.target, row.board, row.label,
                        str(row.path or ""))).lower()
        if text and text not in hay:
            continue
        found.append(row)
    return found


def find(rows: Iterable[LibraryRow], name: str) -> List[LibraryRow]:
    """The rows a person means by `name`: its key, its label, its artifact id, its name or its full path."""
    name = (name or "").strip()
    rows = list(rows)
    exact = [r for r in rows if name in (r.key, r.label, r.artifact_id) or (r.path is not None and name == str(r.path))]
    return exact or [r for r in rows if r.name == name]


# --- deleting downloads of the models folder -------------------------------------------------------------------

def download_plan(framework: Any, rows: Optional[Iterable[LibraryRow]] = None):
    """The deletion plan of the downloads in the models folder (all, or of `rows`): each file or model folder with its
    full path, file name, size and what it is; both modes offered. None when there is none."""
    from orchestrator.Core.deletion import plan_of
    from orchestrator.tools.spec import DeletionItem
    chosen = list(rows) if rows is not None else _downloads(framework)
    items = [DeletionItem(kind="model_file", speaking_name=row.name[:300], full_path=str(row.path),
                          file_name=row.path.name, size_bytes=int(row.size_bytes or 0),
                          what=(f"a model downloaded into the models folder ({row.format or 'model'}"
                                + (f", {row.quant}" if row.quant else "") + ")")[:300])
             for row in chosen if row.kind == "download" and row.deletable and row.path is not None]
    return plan_of(items, ("trash", "permanent"))


def delete_downloads(framework: Any, accepted: Any, trash: Any = None, audit: Any = None):
    """Deletes exactly the downloads the person accepted (Core/deletion.py): only a person's accepted plan in the GUI
    or at a terminal, built again from the disk first (PlanChanged when it changed), audited before anything goes,
    into the models folder's trash or for good. Returns a DeletionResult; an item that cannot go is kept and
    reported."""
    from orchestrator.Core.deletion import DeletionResult, note_kept, require_person, require_same, same_items
    from orchestrator.utils.atomic import remove_tree
    accepted = require_person(accepted)
    if accepted.actor not in ("gui", "cli"):
        raise PermissionError("Only the person deletes downloaded models, in the GUI or at a terminal.")
    base = models_folder(framework)
    current = download_plan(framework)
    require_same(accepted, same_items(current, accepted.plan) if current is not None else None,
                 "the list of downloaded models")
    if accepted.mode == "trash" and trash is None:
        raise RuntimeError("The trash is not available; nothing was deleted.")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so nothing was deleted.")
    items = accepted.plan.items
    try:
        audit.record(accepted.actor, AUDIT_TOOL, "framework", "person", "started", approved=True,
                     arguments={"items": [i.speaking_name for i in items], "bytes": accepted.plan.total_bytes,
                                "mode": accepted.mode},
                     detail=f"{len(items)} downloaded models "
                            + ("moved to the trash" if accepted.mode == "trash" else "deleted for good"),
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a host path
        raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
    result = DeletionResult()
    for item in items:
        path = Path(item.full_path)
        try:
            if not _inside(path, base) or path.is_symlink():
                raise OSError("not a model of the models folder")
            if accepted.mode == "trash":
                entry = trash.move("model_file", path, base, "models", item.speaking_name, accepted, accepted.actor)
                result.trash_ids.append(entry.id)
            else:
                if note_kept(result, item.speaking_name, path, remove_tree(path)):
                    continue
                result.freed_bytes += item.size_bytes
            result.done.append(item.speaking_name)
        except Exception as e:  # noqa: BLE001 - a TrashError or an OSError: the item stays and is reported
            result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
    return result


# --- deleting a build or an F16 intermediate: their owners' deletions -------------------------------------------

def build_deletion(framework: Any, row: LibraryRow):
    """(target, plan) of a build's deletion through its owner, ArtifactStore (its folder and the golden ZIP beside it;
    both modes offered to the person). ArtifactRefused when the store refuses it (not inside the output or build
    folder, a build still writing into it, a changed model file)."""
    service = getattr(framework, "build_service", None)
    if service is None or not row.artifact_id:
        from orchestrator.Core.artifact_store import ArtifactRefused
        raise ArtifactRefused("This build has no id the build service knows; it is not deleted here.",
                              code="not_supported")
    store = service.artifact_store()
    target = store.resolve(row.artifact_id, row.sha256 or None)
    return target, store.plan(target, ("trash", "permanent"))


def delete_build(framework: Any, row: LibraryRow, target: Any, accepted: Any, trash: Any = None, audit: Any = None):
    """Deletes the build the person accepted (ArtifactStore.delete, which checks the plan again), audited first."""
    from orchestrator.Core.deletion import require_person
    accepted = require_person(accepted)
    if accepted.actor not in ("gui", "cli"):
        raise PermissionError("Only the person deletes a build here, in the GUI or at a terminal.")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so nothing was deleted.")
    try:
        audit.record(accepted.actor, "delete_artifact", "framework", "person", "started", approved=True,
                     arguments={"artifact_id": row.artifact_id, "bytes": accepted.plan.total_bytes,
                                "mode": accepted.mode},
                     detail="a built model " + ("moved to the trash" if accepted.mode == "trash" else
                                                "deleted for good"),
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a host path
        raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
    store = framework.build_service.artifact_store()
    return store.delete(row.artifact_id, row.sha256 or None, list(target.identity), accepted, trash=trash,
                        deleted_by=accepted.actor)


def f16_deletion(framework: Any, row: LibraryRow):
    """The F16 cache's own plan narrowed to this row's entry (Core/deletion.choose), None when it is gone."""
    from orchestrator.Core.deletion import choose
    from orchestrator.Core.f16_cache import F16Cache
    plan = F16Cache.for_framework(framework).deletion_plan()
    if plan is None or row.path is None:
        return None
    index = [i for i, item in enumerate(plan.items) if item.full_path == str(row.path)]
    return choose(plan, index) if index else None


def record_rows(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """llama-bench's JSON rows as the table shows them: test, tokens/s with deviation, threads, backend."""
    rows = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        n_prompt, n_gen = r.get("n_prompt") or 0, r.get("n_gen") or 0
        test = f"pp{n_prompt}" if n_prompt and not n_gen else f"tg{n_gen}" if n_gen and not n_prompt else \
            f"pp{n_prompt}+tg{n_gen}"
        avg, dev = r.get("avg_ts"), r.get("stddev_ts")
        speed = f"{avg:.2f}" + (f" ± {dev:.2f}" if isinstance(dev, (int, float)) else "") \
            if isinstance(avg, (int, float)) else "-"
        rows.append({"test": test, "speed": speed, "threads": str(r.get("n_threads") or "-"),
                     "backend": str(r.get("backends") or r.get("backend") or "-")})
    return rows


def build_info_text(framework: Any, row: LibraryRow) -> str:
    """A build's manifest and model card as plain text (BuildService.artifact_details; external text, written by the
    build container)."""
    service = getattr(framework, "build_service", None)
    if service is None or not row.artifact_id:
        try:
            data = json.loads((Path(row.path) / "build_info.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ""
        return json.dumps(data, indent=2, ensure_ascii=False)
    details = service.artifact_details(row.artifact_id)
    manifest = getattr(details, "manifest", None) or {}
    card = getattr(details, "model_card", None) or getattr(details, "card", None) or ""
    text = json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
    return f"{text}\n\n{card}".strip() if card else text


__all__ = ["KINDS", "LibraryRow", "bench_runs", "build_deletion", "build_info_text", "delete_build", "delete_downloads",
           "download_plan", "f16_deletion", "find", "library", "matching", "models_folder", "record_rows", "speed"]
