#!/usr/bin/env python3
"""Inputs and outputs of the build service (also the schemas of the matching MCP / Ditto tools)."""

from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.schemas.targets import (OPTIMIZATION_LEVELS, BenchmarkEntry, OptimizationProfile,  # noqa: F401
                                          SupportLevel)  # OPTIMIZATION_LEVELS: its old place
from orchestrator.tools.output import Untrusted, UntrustedText, untrusted_text

# Fields with external text are marked (UntrustedText): build logs and stage names come from the build container,
# healing proposals from pattern matching on logs or from an AI, and build manifests are written by the container
# first. The tool registry cleans and caps them, and the output schema marks them as reference data.


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
    probe: Optional[str] = Field(default=None, pattern=r"^(local|[0-9a-f]{16})$",
                                 description="The board's hardware probe by its id (get_hardware_probe): the build "
                                             "is tuned to it, and NPU formats need its NPU. Omitted: the newest "
                                             "stored probe of a board of this SoC, if there is one")


class StartBuildIn(BuildRequestIn):
    """start_build and check_build_request: a build request plus the optimization level (the GUI and the CLI offer
    the same five; without one the board's optimization profile decides, else balanced)."""
    optimization: Optional[Literal["fast", "balanced", "size", "speed", "aggressive"]] = Field(
        default=None, description="Optimization level of the conversion: fast, balanced, size, speed or aggressive; "
                                  "omitted: the level of the board's optimization profile "
                                  "(get_target_capabilities), else balanced")


class CheckBuildIn(StartBuildIn):
    include_download_size: bool = Field(default=True, description="Look up the download size and commit on Hugging "
                                                                  "Face (default true)")


class BuildHandle(BaseModel):
    request_id: str
    status: str


class PendingHealing(BaseModel):
    summary: UntrustedText
    root_cause: UntrustedText
    # A step for the person to do by hand (shown to them); never a command for a client to run.
    manual_step_text: UntrustedText
    automatic: bool = Field(description="True if approving makes the framework apply the fix and retry")
    ai_generated: bool


class RequestSummary(BaseModel):
    """What a build request builds (the model as the caller named it: a repo id or a label, never a host path)."""
    model: Optional[untrusted_text(512)] = None
    target: Optional[untrusted_text(64)] = None
    format: Optional[untrusted_text(32)] = None
    board: Optional[untrusted_text(64)] = None
    quantization: Optional[untrusted_text(32)] = None
    optimization: Optional[str] = None
    revision: Optional[str] = None


class ClientProposalOut(BaseModel):
    """A fix proposal an AI client made for this build (propose_healing_fix): a manual step for the person, never run
    by the framework and never applied by resolve_healing."""
    proposal_id: str
    summary: UntrustedText
    root_cause: UntrustedText
    manual_step_text: UntrustedText
    confidence: float
    where: str
    proposed_by: str = Field(description="The caller that proposed it: mcp, mcp:<token name> or ditto")
    client: untrusted_text(200) = Field(default="", description="The client's own name, as it reports it")
    ai_generated: bool = True
    created: str = ""


class BuildStatusOut(BaseModel):
    request_id: str
    status: str
    stage: UntrustedText
    progress_percent: int
    total_builds: int
    completed_builds: int
    failed_builds: int
    artifacts: List[untrusted_text(1000)] = Field(default_factory=list)
    errors: List[UntrustedText] = Field(default_factory=list)
    warnings: List[UntrustedText] = Field(default_factory=list)
    pending_healing: Optional[PendingHealing] = None
    client_proposals: List[ClientProposalOut] = Field(default_factory=list)
    owner: Optional[str] = Field(default=None, description="Caller that started it through a tool (mcp, "
                                                           "mcp:<token name>, ditto); empty for the GUI and the CLI")
    request: Optional[RequestSummary] = None
    process: Literal["this", "other"] = Field(default="this", description="'other': started by another framework "
                                                                          "process (GUI, CLI, another MCP server); "
                                                                          "shown from the shared build state, "
                                                                          "read-only")
    started_in: Optional[Literal["GUI", "CLI", "MCP server", "API server", "framework process"]] = Field(
        default=None, description="For process 'other': the program that runs it, where the person can stop it")


class BuildList(BaseModel):
    builds: List[BuildStatusOut]
    page: int = 1
    more: bool = False


class ArtifactOut(BaseModel):
    """One finished build, from its manifest (written by the build container first). Named by its id and its label
    under the output folder, never by a host path."""
    artifact_id: str = Field(description="Id of the built model (a_ and 16 hex characters)")
    label: untrusted_text(1000) = Field(description="Where it lies: output:<relative path> in the output folder, "
                                                    "builds:<relative path> in the framework's build folder (not "
                                                    "saved yet), saved:<relative path> in a folder the person chose")
    size_bytes: Optional[int] = None
    model: Optional[UntrustedText] = None
    format: Optional[untrusted_text(200)] = None
    quantization: Optional[untrusted_text(200)] = None
    board: Optional[untrusted_text(200)] = None
    target: Optional[untrusted_text(200)] = None
    created: Optional[untrusted_text(200)] = None
    primary: Optional[untrusted_text(1000)] = None
    sha256: Optional[untrusted_text(200)] = None


class ArtifactList(BaseModel):
    artifacts: List[ArtifactOut]
    page: int = 1
    more: bool = False


class ArtifactFile(BaseModel):
    name: untrusted_text(300)
    size: int
    sha256: Optional[untrusted_text(64)] = None


class ArtifactDetails(BaseModel):
    """One built model: its manifest (written by the build container first, so every string is external text), its
    model card and its files."""
    artifact_id: str
    label: untrusted_text(1000)
    manifest: Annotated[Dict[str, Any], Untrusted(2000)] = Field(
        default_factory=dict, json_schema_extra={"untrusted": True},
        description="build_info.json; every string value is external text")
    model_card: untrusted_text(20000) = ""
    files: List[ArtifactFile] = Field(default_factory=list)


class FormatSummary(BaseModel):
    id: str
    label: untrusted_text(200)  # from target.yml (a community target pack can install one)
    boards: List[str]
    default_quantization: str
    quantizations: List[str]


class BenchmarkRowOut(BenchmarkEntry):
    source_label: untrusted_text(600) = Field(default="", description="Where the numbers come from: measured on a board "
                                                                       "by the framework, published, or unmeasured (an "
                                                                       "estimate, never a measurement)")


class TargetOut(BaseModel):
    id: str
    name: untrusted_text(200)  # from target.yml (a community target pack can install one)
    architecture: Optional[untrusted_text(200)] = None
    support_level: Optional[SupportLevel] = None
    boards: List[str] = Field(default_factory=list)
    formats: List[FormatSummary] = Field(default_factory=list)
    board_profiles: Dict[str, OptimizationProfile] = Field(
        default_factory=dict, description="Per board: the llama.cpp options the build recipe plans for it, and the "
                                          "recommended quantizations, memory profile and optimization level")
    benchmarks: List[BenchmarkRowOut] = Field(default_factory=list,
                                              description="Benchmark rows of target.yml, each with its source")


class TargetList(BaseModel):
    targets: List[TargetOut]


class LogTail(BaseModel):
    request_id: str
    lines: List[untrusted_text(2000)]
    first_line: int = Field(default=1, description="Line number (from 1) of the first line returned")
    total_lines: int = 0
    truncated: bool = Field(default=False, description="Older lines exist (page back with before_line=first_line) "
                                                       "or lines were cut to fit")
