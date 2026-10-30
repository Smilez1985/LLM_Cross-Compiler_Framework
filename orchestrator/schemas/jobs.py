#!/usr/bin/env python3
"""Inputs and outputs of the job tools (get_job_status, cancel_job)."""

from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.output import UNTRUSTED_DESCRIPTION, Untrusted, UntrustedText

# A job's progress message and error can quote text from outside the framework (a server reply, a page, a device),
# so they are marked as external text (cleaned, capped and wrapped for the client's model).

JobKind = Literal["crawl", "deploy", "package", "probe", "pack_import", "pack_export", "snapshot_restore",
                  "hf_download", "runtime_build", "runtime_start", "benchmark", "approval", "docs_index"]
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
    message: UntrustedText = ""


class JobErrorOut(BaseModel):
    code: str
    message: UntrustedText
    next: str = ""
    # The framework's notes about the call a pending approval ran (the repairs it made, "argument 'quant' read as
    # 'quantization'"), as its result's notes would name them.
    notes: List[str] = Field(default_factory=list, description="The framework's notes about the call: the repairs "
                                                               "it made, as a successful result names them")


# A job's result can hold external text too (addresses a crawl skipped and the sites' reasons, pack titles): every
# string in it is cleaned, capped and marked like other external text.
JobResult = Annotated[Dict[str, Any], Untrusted(2000),
                      Field(json_schema_extra={"untrusted": True}, description=UNTRUSTED_DESCRIPTION)]


class JobStatusOut(BaseModel):
    job_id: str
    kind: JobKind
    state: JobState
    progress: JobProgress
    result: Optional[JobResult] = None
    # Text from outside the framework that a job's result carries (the output a device printed during a deploy):
    # kept out of `result` and marked, so it is cleaned, capped and wrapped like every other external text.
    external_text: List[UntrustedText] = Field(default_factory=list,
                                               description="Text from outside the framework in the job's result "
                                                           "(for example a device's output)")
    error: Optional[JobErrorOut] = None
    owner: str = Field(description="Caller class that started the job (mcp, ditto, ...)")
    yours: bool = Field(description="True if this client started the job (it may cancel it without a question)")
    created: str
    updated: str
    note: str = ""
    end_reason: Optional[Literal["done", "failed", "cancelled", "soft_cap", "idle"]] = Field(
        default=None, description="For a build job (runtime_build): how it ended, None while it runs (the same "
                                  "vocabulary as a model build's end_reason)")


class CancelJobOut(BaseModel):
    job_id: str
    state: JobState
    note: str = ""
