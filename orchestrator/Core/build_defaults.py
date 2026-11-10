#!/usr/bin/env python3
"""The defaults of a build's importance matrix and calibration text (DESIGN: Build pipeline and target modules).

One rule for every caller: the build service applies it to a request that leaves `use_imatrix` or `dataset_path` out
(None), so the Builder tab, `llm-cli build start`, the MCP and Ditto tools, the identical-build check and the build plan
get the same values. An explicit choice always wins.

- Importance matrix (setting `default_enable_imatrix`, default true): on for the GGUF types below 5 bits per weight
  that llama.cpp quantizes with an importance matrix; with the setting false only for the types the target says need
  one (`needs_imatrix`). Every other format: off.
- Calibration text (setting `default_calibration_dataset`, default empty): empty is the shipped, checksummed text of
  data/calibration (orchestrator/utils/calibration.py, in the mix of `calibration_language`), written by the
  orchestrator for a GGUF importance matrix; a value names the person's own text file (a name inside `datasets_dir` or
  an absolute path), used for the GGUF importance matrix and for RKLLM, never for RKNN (a sample list).
"""

from pathlib import Path
from typing import Any, Iterable, Optional

from orchestrator.utils.any_path import last_part

# GGUF types below 5 bits per weight whose llama.cpp quantizer reads an importance matrix. Q5_*, Q6_K (small gain),
# Q8_0 and the float types (not read), TQ1_0, TQ2_0, MXFP4_MOE, Q1_0 and Q2_0 are left out.
IMATRIX_BY_DEFAULT = frozenset({
    "Q2_K", "Q2_K_S", "Q3_K_S", "Q3_K_M", "Q3_K_L", "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M",
    "IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M", "IQ3_XXS", "IQ3_XS", "IQ3_S", "IQ3_M", "IQ4_NL", "IQ4_XS",
})
# Formats that read a calibration text file (GGUF only through its importance matrix); RKNN reads a sample list.
TEXT_DATASET_FORMATS = ("gguf", "rkllm")
SETTING_IMATRIX = "default_enable_imatrix"
SETTING_DATASET = "default_calibration_dataset"
DATASETS_LABEL = "datasets"


class DefaultDatasetMissing(ValueError):
    """The file `default_calibration_dataset` names is not there."""

    def __init__(self, name: str):
        super().__init__(f"The default calibration dataset '{name}' (setting {SETTING_DATASET}) was not found.")
        self.name = name


def _get(config: Any, key: str, default: Any = None) -> Any:
    get = getattr(config, "get", None)
    try:
        return get(key, default) if callable(get) else getattr(config, key, default)
    except Exception:  # noqa: BLE001 - an unreadable setting is its default
        return default


def as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def imatrix_setting(config: Any) -> bool:
    return as_bool(_get(config, SETTING_IMATRIX, True), True)


def default_imatrix(config: Any, fmt: Optional[str], quantization: Optional[str], *, format_uses_imatrix: bool = True,
                    needs: Iterable[str] = ()) -> bool:
    """Whether a build of `fmt` / `quantization` gets an importance matrix when the caller did not say.
    `format_uses_imatrix`: the target's capability (`imatrix` of the format); `needs`: its `needs_imatrix` types."""
    if str(fmt or "").strip().lower() != "gguf" or not format_uses_imatrix:
        return False
    quant = str(quantization or "").strip().upper()
    if quant and quant in {str(q).upper() for q in needs}:
        return True
    return imatrix_setting(config) and quant in IMATRIX_BY_DEFAULT


def dataset_setting(config: Any) -> str:
    return str(_get(config, SETTING_DATASET, "") or "").strip()


def default_dataset_path(config: Any, datasets_dir: Optional[Path]) -> Optional[Path]:
    """The person's default calibration file (absolute; a bare name or relative path is inside `datasets_dir`), or None
    when the setting is empty (the shipped text). It is not checked to exist here."""
    value = dataset_setting(config)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute() and datasets_dir is not None:
        path = Path(datasets_dir).expanduser() / path
    return path


def dataset_label(config: Any) -> Optional[str]:
    """The default file as an AI may see it (datasets:<name>, never the path), None for the shipped text."""
    value = dataset_setting(config)
    return f"{DATASETS_LABEL}:{last_part(value)}" if value else None


def takes_default_dataset(fmt: Optional[str], use_imatrix: bool) -> bool:
    """A build of `fmt` reads the person's default text file: GGUF only with an importance matrix, RKLLM always."""
    fmt = str(fmt or "").strip().lower()
    return fmt in TEXT_DATASET_FORMATS and (fmt != "gguf" or bool(use_imatrix))


def describe(use_imatrix: bool, dataset: Optional[str], fmt: Optional[str], language: str = "mixed") -> str:
    """One line on the calibration of a resolved request (the question before an AI's build, the CLI's summary)."""
    fmt = str(fmt or "").strip().lower()
    matrix = "importance matrix yes" if use_imatrix else "importance matrix no"
    if dataset:
        return f"{matrix}; calibration text {dataset}"
    if use_imatrix:
        return f"{matrix}; calibration text: the shipped text ({language})"
    return matrix if fmt == "gguf" else "no calibration dataset"
