#!/usr/bin/env python3
"""Inputs and outputs of the job tools (get_job_status, cancel_job)."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

JobKind = Literal["crawl", "deploy", "package", "probe", "pack_import", "pack_export", "snapshot_restore",
                  "hf_download", "runtime_build", "runtime_start", "benchmark", "approval"]
JobState = Literal["queued", "running", "waiting_for_approval", "done", "failed", "cancelled", "expired"]


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    job_id: str = Field(pattern=r"^job_[a-z0-9]{16}$",
                        description="Job id (job_ and 16 characters) returned by the tool that started the job")


class JobWaitIn(JobIn):
    wait_seconds: int = Field(default=0, ge=0,
                              description="Wait up to this many seconds (at most 50) for the job to change or end; "
                                          "0 returns at once. Example: 50")


class JobProgress(BaseModel):
    done: Optional[float] = None
    total: Optional[float] = None
    message: str = ""


class JobErrorOut(BaseModel):
    code: str
    message: str
    next: str = ""


class JobStatusOut(BaseModel):
    job_id: str
    kind: JobKind
    state: JobState
    progress: JobProgress
    result: Optional[Dict[str, Any]] = None
    error: Optional[JobErrorOut] = None
    owner: str = Field(description="Caller class that started the job (mcp, ditto, ...)")
    yours: bool = Field(description="True if this client started the job (it may cancel it without a question)")
    created: str
    updated: str
    note: str = ""
    notes: List[str] = Field(default_factory=list)


class CancelJobOut(BaseModel):
    job_id: str
    state: JobState
    note: str = ""
