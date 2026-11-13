#!/usr/bin/env python3
"""
The build engine's steps for large models (DESIGN "Build pipeline and target modules", the row "Large models"), kept
out of builder.py so the engine only calls them:

- f16_plan: where a GGUF build's F16 intermediate is (the work folder, shared by the importance-matrix job and the
  build job) or comes from (the folder kept from an earlier build of this model, mounted read-only): the volumes and
  F16_FILE / F16_KEPT for every stage container of the build.
- stage_imatrix: a given importance matrix (a local file, or hf:owner/name/path downloaded by the framework's verified
  fetcher), checked and copied into the build's own folder while it is hashed, used instead of the importance-matrix
  job.
- stage_env: the per-tensor types (TENSOR_TYPES) and IMATRIX_GIVEN for the stages.
- keep_f16: after a finished build, the F16 the build converted is kept for the next build of the same model (a Hugging
  Face model at a known commit, the setting on, room on the disk).
- record: what build_info.json says about all of it (never a path).

`config.large_model` (a dict, made by the build service) carries the request's part: tensor_types, imatrix_file,
gguf_input, tensor_types_measured; the steps add f16 and imatrix_source.
"""

import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from orchestrator.Core import f16_cache, imatrix_source, tensor_types

HF_REPO = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")
# The GGUF types the converter writes itself: no F16 intermediate (the convert stage's GGUF_CONVERT_TYPES)
CONVERT_TYPES = ("F16", "BF16", "F32", "Q8_0")


def options(config: Any) -> Dict[str, Any]:
    value = getattr(config, "large_model", None)
    if not isinstance(value, dict):
        value = {}
        try:
            config.large_model = value
        except AttributeError:
            pass
    return value


def _pin() -> str:
    from orchestrator.utils.llama_pin import pinned_source
    source = pinned_source() or {}
    return str(source.get("commit") or source.get("tag") or "")


def cache_key(config: Any) -> Optional[str]:
    """The F16 cache key of this build: a Hugging Face model (never an AI's pinned local file, never a path) at the
    commit the build fetched, converted by the pinned llama.cpp."""
    source = str(getattr(config, "model_source", "") or "")
    if getattr(getattr(config, "local_inputs", None), "model", None) is not None or not HF_REPO.match(source) \
            or Path(source).expanduser().exists():
        return None
    return f16_cache.key(source, str(getattr(config, "model_revision", "") or ""), _pin())


def needs_f16(config: Any) -> bool:
    """A GGUF build of a model (not a GGUF input) that writes an F16 intermediate: a type the quantizer makes, an
    importance matrix, or a tuning run."""
    from orchestrator.Core.build_formats import ModelFormat
    if getattr(config, "target_format", None) != ModelFormat.GGUF:
        return False
    if str(getattr(config, "model_source", "") or "").lower().endswith(".gguf") or options(config).get("gguf_input"):
        return False
    quant = str(getattr(config, "quantization", "") or "").upper()
    return bool(getattr(config, "tuning", None) or getattr(config, "use_imatrix", False)
                or (quant and quant not in CONVERT_TYPES))


def f16_plan(config: Any, cache_dir: Path, build_temp: Path) -> Tuple[Dict[str, Dict[str, str]], Dict[str, str]]:
    """(volumes, environment) of the F16 intermediate for one stage container of the build; nothing when the build
    writes none. A kept file of this model is mounted read-only (F16_KEPT=1); else the build's own place in its work
    folder (/build-cache/work, mounted by the sandbox)."""
    if not needs_f16(config):
        return {}, {}
    entry = f16_cache.F16Cache(cache_dir).entry(cache_key(config))
    if entry is not None:
        options(config)["f16"] = {"state": "reused", "bytes": entry.size_bytes}
        return ({str(entry.folder): {"bind": f16_cache.MOUNT, "mode": "ro"}},
                {"F16_FILE": f"{f16_cache.MOUNT}/{f16_cache.FILE_NAME}", "F16_KEPT": "1"})
    work = Path(build_temp) / "work"
    (work / "f16").mkdir(parents=True, exist_ok=True)
    _owner_of_work(work / "f16")
    return {}, {"F16_FILE": f16_cache.WORK_FILE}


def _owner_of_work(folder: Path) -> None:
    """A root host makes the folder after the sandbox handed the work folder to the stage runner (utils/sandbox.
    hand_over): it gets the work folder's owner, so the runner makes it writable for the stage account like the work
    folder's others (runtime_layer/stage_user.sh stage_folders). Before, it stayed root's and the convert stage could
    not write the F16 into it."""
    if os.name != "posix" or os.getuid() != 0:
        return
    owner = os.stat(folder.parent)
    os.chown(folder, owner.st_uid, owner.st_gid)


def stage_env(config: Any) -> Dict[str, str]:
    """TENSOR_TYPES and IMATRIX_GIVEN for the stages."""
    opts = options(config)
    env: Dict[str, str] = {}
    entries = opts.get("tensor_types") or []
    if entries:
        env["TENSOR_TYPES"] = tensor_types.env_value(entries)
    if opts.get("imatrix_source"):
        env["IMATRIX_GIVEN"] = "1"
    return env


def stage_imatrix(config: Any, progress: Any, build_temp: Path, *, download: Optional[Callable] = None) -> bool:
    """A given importance matrix copied into build_temp/imatrix/imatrix.dat while it is hashed (the bytes checked are
    the bytes used); True when one was given. A Hugging Face file is downloaded first by `download(repo, filename)`
    (the framework's verified fetcher, ModelManager.download_file), which returns its local path."""
    from orchestrator.utils.helpers import copy_hashed
    opts = options(config)
    value = str(opts.get("imatrix_file") or "").strip()
    if not value:
        return False
    commit = ""
    if imatrix_source.is_hub_reference(value):
        source = imatrix_source.parse_hub(value)
        if download is None:
            raise RuntimeError("A Hugging Face importance matrix needs the framework's downloader; nothing was built.")
        progress.add_log(f"Downloading the importance matrix {source.label()}...")
        path = Path(download(source.repo, source.filename))
        commit = next((p.name for p in path.parents if re.fullmatch(r"[0-9a-f]{12,40}", p.name)), "")
    else:
        source = imatrix_source.ImatrixSource(imatrix_source.FILE, path=value)
        pin = getattr(getattr(config, "local_inputs", None), "imatrix", None)
        # an AI caller's file: still the one it was shown (same identity, inside a folder the AI may use)
        path = Path(pin.verify()) if pin is not None else Path(value)
    info = imatrix_source.check_file(path)
    folder = Path(build_temp) / "imatrix"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "imatrix.dat"
    if target.exists():
        target.unlink()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags)
    try:
        digest = copy_hashed(fd, target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)
    os.chmod(target, 0o644)
    opts["imatrix_source"] = imatrix_source.record(source, digest, commit=commit, fmt=str(info.get("format") or ""))
    progress.add_log(f"Importance matrix: {source.label()} ({info.get('format')} format, sha256 {digest[:16]}...), "
                     "used instead of computing one.")
    return True


def keep_f16(config: Any, progress: Any, cache_dir: Path, build_temp: Path, framework_config: Any,
             disk_usage: Optional[Callable] = None) -> None:
    """After a finished build: the F16 it converted (build_temp/work/f16) kept for the next build of this model, when
    it may be (a Hugging Face model at a known commit, the setting keep_f16_intermediates on, more than twice the
    disk's reserve free with it); else it goes with the build's folder. A reused file is marked as used."""
    opts = options(config)
    key = cache_key(config)
    cache = f16_cache.F16Cache(cache_dir)
    if (opts.get("f16") or {}).get("state") == "reused":
        if key:
            cache.touch(key)
        return
    made = Path(build_temp) / "work" / "f16" / f16_cache.FILE_NAME
    if not made.is_file() or made.is_symlink():
        return
    size = made.stat().st_size
    why = ""
    if key is None:
        why = "a local model or a model without a known commit is never kept"
    elif not f16_cache.setting_on(framework_config):
        why = f"the setting {f16_cache.SETTING} is off"
    elif not f16_cache.keep_allowed(cache.root.parent, disk_usage):
        why = "the disk would keep less than twice its reserve free"
    if why:
        opts["f16"] = {"state": "not_kept", "bytes": size, "reason": why}
        progress.add_log(f"F16 intermediate not kept: {why}.")
        return
    repo = str(getattr(config, "model_source", "") or "")
    try:
        entry = cache.keep(key, made, repo=repo, commit=str(config.model_revision), pin=_pin())
    except OSError as e:
        opts["f16"] = {"state": "not_kept", "bytes": size, "reason": f"it could not be kept ({type(e).__name__})"}
        progress.add_warning(f"The F16 intermediate could not be kept ({type(e).__name__}); the next build of this "
                             "model converts it again.")
        return
    opts["f16"] = {"state": "kept", "bytes": entry.size_bytes}
    from orchestrator.utils.next_steps import command_line, get
    progress.add_log(f"F16 intermediate kept for the next build of {repo} ({entry.size_bytes / 1e9:.1f} GB in the "
                     f"build cache; {command_line(get('build.f16_list'))}).")


def record(config: Any) -> Dict[str, Any]:
    """What build_info.json says about the large-model steps of this build: numbers and words, never a path."""
    opts = options(config)
    out: Dict[str, Any] = {}
    if opts.get("tensor_types"):
        out["tensor_types"] = list(opts["tensor_types"])
        out["tensor_types_measured"] = bool(opts.get("tensor_types_measured"))
    if opts.get("imatrix_source"):
        out["imatrix_source"] = dict(opts["imatrix_source"])
    elif getattr(config, "use_imatrix", False):
        out["imatrix_source"] = imatrix_source.record(None, None)
    if opts.get("gguf_input"):
        out["gguf_input"] = dict(opts["gguf_input"])
    if opts.get("f16"):
        out["f16_intermediate"] = dict(opts["f16"])
    return out


def tokenizer_lines(info: Dict[str, Any]) -> Tuple[list, list]:
    """(log lines, warnings) of the tokenizer and chat-template check the target stage wrote into build_info.json."""
    from orchestrator.Core.tokenizer_check import summary
    return summary(info.get("tokenizer_check"))


__all__ = ["CONVERT_TYPES", "cache_key", "f16_plan", "keep_f16", "needs_f16", "options", "record", "stage_env",
           "stage_imatrix", "tokenizer_lines"]
