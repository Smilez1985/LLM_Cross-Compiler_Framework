#!/usr/bin/env python3
"""Inputs and outputs of the build service (also the schemas of the matching MCP / Ditto tools)."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.schemas.targets import BoardInfo, SupportLevel


class BuildRequestIn(BaseModel):
    """A conversion request. Format, board and quantization are checked against the target's capabilities."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model: str = Field(min_length=1, max_length=512,
                       description="Hugging Face repo id (owner/name), or a local model as <label>:<relative path> "
                                   "inside a folder the AI may use (e.g. models:qwen2.5-0.5b-instruct) or its file "
                                   "id (lf_...); never a path")
    revision: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{40}$",
                                    description="Commit of the Hugging Face model (a recipe's pinned revision); "
                                                "the default branch when omitted")
    target: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][\w.-]*$",
                        description="Target id, e.g. Rockchip")
    format: str = Field(min_length=1, max_length=32, description="Output format, e.g. gguf, rkllm, rknn")
    board: Optional[str] = Field(default=None, max_length=64, description="Board within the target, e.g. rk3566")
    quantization: Optional[str] = Field(default=None, max_length=32,
                                        description="Quantization type; the target's default when omitted")
    use_imatrix: bool = Field(default=False, description="Compute an importance matrix (GGUF only)")
    dataset_path: Optional[str] = Field(default=None, max_length=1024,
                                        description="Calibration dataset file as <label>:<relative path> (e.g. "
                                                    "datasets:calib/de.txt) or its file id; never a path")
    use_gpu: bool = False
    max_context_length: Optional[int] = Field(default=None, ge=256, le=1_048_576)


class BuildHandle(BaseModel):
    request_id: str
    status: str


class PendingHealing(BaseModel):
    summary: str
    root_cause: str
    suggested_step: str
    automatic: bool = Field(description="True if approving makes the framework apply the fix and retry")
    ai_generated: bool


class BuildStatusOut(BaseModel):
    request_id: str
    status: str
    stage: str
    progress_percent: int
    total_builds: int
    completed_builds: int
    failed_builds: int
    artifacts: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    pending_healing: Optional[PendingHealing] = None


class BuildList(BaseModel):
    builds: List[BuildStatusOut]


class ArtifactOut(BaseModel):
    path: str
    model: Optional[str] = None
    format: Optional[str] = None
    quantization: Optional[str] = None
    board: Optional[str] = None
    target: Optional[str] = None
    created: Optional[str] = None
    primary: Optional[str] = None
    sha256: Optional[str] = None


class ArtifactList(BaseModel):
    artifacts: List[ArtifactOut]


class FormatSummary(BaseModel):
    id: str
    label: str
    boards: List[str]
    default_quantization: str
    quantizations: List[str]


class TargetOut(BaseModel):
    id: str
    name: str
    architecture: Optional[str] = None
    support_level: Optional[SupportLevel] = None
    boards: List[str] = Field(default_factory=list)
    formats: List[FormatSummary] = Field(default_factory=list)


class TargetList(BaseModel):
    targets: List[TargetOut]


class BoardOut(BoardInfo):
    id: str


class LogTail(BaseModel):
    request_id: str
    lines: List[str]
