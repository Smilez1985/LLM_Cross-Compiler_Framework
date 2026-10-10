#!/usr/bin/env python3
"""
Machine-readable target capabilities (the `capabilities:` block of a target's target.yml).

One place says which boards a target has, which formats it can produce for which board, which
quantizations each format accepts and what calibration data it needs. GUI, CLI, the build service,
Ditto and the MCP server derive their choices and their request validation from it.
"""

from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SupportLevel(str, Enum):
    OFFICIAL = "official"          # maintained and tested by the project
    COMMUNITY = "community"        # contributed; works for its maintainers, use at your own risk
    EXPERIMENTAL = "experimental"  # incomplete or unverified


class BoardInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    cpu: Optional[str] = None
    ram_max_gb: Optional[float] = Field(default=None, gt=0)
    npu: Optional[str] = None
    gpu: Optional[str] = None
    examples: List[str] = Field(default_factory=list)


class FormatCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    input: List[Literal["hf", "onnx", "gguf"]] = Field(default_factory=lambda: ["hf"])
    quantizations: List[str] = Field(min_length=1)
    default_quantization: str
    # Boards that can run this format (None: every board of the target).
    boards: Optional[List[str]] = None
    # Per-board subsets of `quantizations` where a SoC supports fewer types.
    board_quantizations: Dict[str, List[str]] = Field(default_factory=dict)
    board_default_quantization: Dict[str, str] = Field(default_factory=dict)
    imatrix: bool = False
    needs_imatrix: List[str] = Field(default_factory=list)
    dataset: Literal["none", "optional", "required"] = "none"
    dataset_kind: Literal["text", "sample_list"] = "text"
    dataset_required_for: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _consistent(self):
        known = set(self.quantizations)
        problems = []
        if self.default_quantization not in known:
            problems.append(f"default_quantization {self.default_quantization!r} is not in quantizations")
        for board, quants in self.board_quantizations.items():
            extra = set(quants) - known
            if extra:
                problems.append(f"board_quantizations[{board}] lists unknown types {sorted(extra)}")
        for board, quant in self.board_default_quantization.items():
            if quant not in self.board_quantizations.get(board, self.quantizations):
                problems.append(f"board_default_quantization[{board}] {quant!r} is not allowed on that board")
        for name, values in (("needs_imatrix", self.needs_imatrix), ("dataset_required_for", self.dataset_required_for)):
            extra = set(values) - known
            if extra:
                problems.append(f"{name} lists unknown types {sorted(extra)}")
        if self.needs_imatrix and not self.imatrix:
            problems.append("needs_imatrix is set but imatrix is false")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def canonical(self, quantization: Optional[str]) -> Optional[str]:
        """The listed spelling of a quantization type (matching is case-insensitive), or None."""
        if not quantization:
            return None
        wanted = quantization.strip().casefold()
        return next((q for q in self.quantizations if q.casefold() == wanted), None)

    def quantizations_for(self, board: Optional[str]) -> List[str]:
        if board and board in self.board_quantizations:
            return list(self.board_quantizations[board])
        return list(self.quantizations)

    def default_for(self, board: Optional[str]) -> str:
        if board and board in self.board_default_quantization:
            return self.board_default_quantization[board]
        allowed = self.quantizations_for(board)
        return self.default_quantization if self.default_quantization in allowed else allowed[0]


class TargetCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    support_level: SupportLevel = SupportLevel.OFFICIAL
    boards: Dict[str, BoardInfo] = Field(min_length=1)
    formats: Dict[str, FormatCapability] = Field(min_length=1)

    @model_validator(mode="after")
    def _boards_exist(self):
        problems = []
        for fmt, cap in self.formats.items():
            referenced = set(cap.boards or []) | set(cap.board_quantizations) | set(cap.board_default_quantization)
            unknown = referenced - set(self.boards)
            if unknown:
                problems.append(f"format {fmt} references unknown boards {sorted(unknown)}")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def boards_for(self, fmt: str) -> List[str]:
        cap = self.formats.get(fmt)
        if not cap:
            return []
        return list(cap.boards) if cap.boards is not None else list(self.boards)

    def check_request(self, fmt: str, board: Optional[str], quantization: Optional[str],
                      dataset: bool = False, imatrix: bool = False) -> List[str]:
        """Problems with a build request, as user-facing sentences (empty list: the request is valid)."""
        cap = self.formats.get(fmt)
        if cap is None:
            return [f"Format '{fmt}' is not available for this target (available: {', '.join(self.formats)})."]
        problems = []
        if board is not None and board not in self.boards:
            problems.append(f"Unknown board '{board}' (known: {', '.join(self.boards)}).")
        elif board is not None and board not in self.boards_for(fmt):
            problems.append(f"Format '{fmt}' does not run on {board} (supported: {', '.join(self.boards_for(fmt))}).")
        quant = cap.canonical(quantization) or quantization or cap.default_for(board)
        allowed = cap.quantizations_for(board)
        if quant not in allowed:
            where = f" on {board}" if board else ""
            problems.append(f"Quantization '{quant}' is not supported for {fmt}{where} (allowed: {', '.join(allowed)}).")
        if imatrix and not cap.imatrix:
            problems.append(f"An importance matrix (IMatrix) is not used by the {fmt} format.")
        if quant in cap.needs_imatrix and not imatrix:
            problems.append(f"{quant} needs an importance matrix: enable IMatrix and provide a calibration dataset.")
        if imatrix and not dataset:
            problems.append("IMatrix needs a calibration dataset.")
        if (cap.dataset == "required" or quant in cap.dataset_required_for) and not dataset:
            problems.append(f"{fmt} {quant} needs a calibration dataset ({cap.dataset_kind.replace('_', ' ')}).")
        if dataset and cap.dataset == "none" and not cap.imatrix:
            problems.append(f"The {fmt} format does not use a calibration dataset.")
        return problems
