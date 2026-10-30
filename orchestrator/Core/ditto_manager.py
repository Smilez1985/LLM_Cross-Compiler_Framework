#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Ditto, the AI assistant.

Ditto runs on the provider layer (orchestrator.llm): provider and model per task, the privacy filter for
cloud providers, retries and the fallback chain all live there. Ditto adds prompts, tools and structured
answers. Its profiles map to the layer's task profiles (ChatRequest.task), which pick model and effort:

  profile    task     answer
  chat       chat     text; the framework tools run through the ToolRegistry as actor "ditto"
  healing    healing  HealingAnalysis: advice only, self-healing shows it as a manual step
  module     json     ModuleDefinition: reviewed by the user, checked again by the module generator
  optimizer  json     OptimizerRecommendation: limited to what the selected target can build

Rules:
- untrusted text (build logs, probe files, knowledge-base hits, tool results) goes into user turns wrapped by
  wrap_untrusted, never into a system prompt;
- structured answers are validated pydantic models; anything else is an error and is never applied;
- tool calls go through the user's tool permissions; "ask" needs the confirmation callback's yes;
- the chat history is append-only: a long conversation is summarized into a new conversation, earlier turns
  of the current one are never rebuilt or edited;
- Ditto's hands are the framework's own tools on this computer (DECISIONS round 8): the same registry, permissions
  and audit log every MCP client gets; Ditto never connects to a framework on another computer.
"""

import json
import secrets
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Type, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator, model_validator

from orchestrator.Core.ai_override import SettingsOverlay as RunSettings, provider_problem  # noqa: F401 - shared
from orchestrator.Core.module_generator import ARCHITECTURES, INPUT_LIST_BOUNDS, MODULE_NAME_RE, ModuleGenerator
from orchestrator.schemas.modules import PackageSourceIn, PinnedDownloadIn
from orchestrator.llm import (AuthFailed, BadRequest, BillingError, ChatRequest, InvalidOutput, LLMClient, LLMError,
                              Message, NotConfigured, PermissionDenied, ProviderTimeout, ProviderUnavailable,
                              RateLimited, Refused, Truncated, Usage, make_registry_executor,
                              run_tool_loop, tool_specs_from_registry)
from orchestrator.llm.task_prompts import (CANNOT_REASONS, CANNOT_RETRY, CANNOT_RULE, CHAT_INTRO,  # noqa: F401
                                           CHAT_TOOLS, FRAMEWORK, HEALING_SYSTEM, MODULE_SYSTEM, OPTIMIZER_SYSTEM,
                                           SDK_HINTS, SUMMARY_SYSTEM, base_image_block, format_listing, healing_parts,
                                           healing_query, is_official_image, knowledge_excerpts, module_parts,
                                           module_query, optimizer_parts)
# Tool calls and results pair up (one implementation, in the tool loop); the names are Ditto's too.
from orchestrator.llm.tool_loop import (CALL_NOT_RUN, close_open_calls, conversation_is_consistent,  # noqa: F401
                                        open_tool_calls)
from orchestrator.tools.registry import ToolContext, ToolDenied, ToolError, ToolInputError
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger
from orchestrator.utils.untrusted import UNTRUSTED_RULE, sanitize_untrusted, wrap_untrusted

logger = get_logger("DittoManager")

MAX_TOOL_STEPS = 8             # model turns per question; every tool call is answered before the loop ends
FIX_EXTRA_STEPS = 4            # more turns while the model fixes an input error (design 6.11 D8)
MIN_MESSAGES_TO_SUMMARIZE = 4  # never summarize a conversation of a single exchange
OPTIMIZATION_LEVELS = ("BALANCED", "SPEED", "SIZE", "AGGRESSIVE")

Confirm = Callable[[Any, BaseModel], bool]  # (ToolSpec, validated input) -> the user's yes/no

# Commands Ditto must never show as a fix or a recommendation, whatever a log or model name told the model
# (small local models follow injected instructions more often). The prompts forbid them too.
DANGEROUS_COMMANDS = (
    (re.compile(r"\b(?:curl|wget|iwr|Invoke-WebRequest)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b|\biex\b", re.I),
     "piping a download into a shell"),
    (re.compile(r"\bchmod\s+(?:-\w+\s+)*0?(?:777|666|[0-7]?7[0-7]7)\b"), "world-writable permissions"),
    (re.compile(r"\brm\s+(?:-\w+\s+)*(?:--no-preserve-root\s+)?(?:/|~|\$HOME|\*|/\*|~/\*)(?=\s|$|;|&)"),
     "deleting the root or home folder"),
    (re.compile(r"\bmkfs(?:\.\w+)?\b|\bdd\s+[^\n]*\bof=/dev/(?:sd|nvme|mmcblk|hd|vd)", re.I), "overwriting a disk"),
    (re.compile(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:"), "a fork bomb"),
)


def format_notes(caps) -> Dict[str, str]:
    """One line per format of a target's capabilities: label, accepted inputs and notes (for the optimizer)."""
    return {fmt: f"{cap.label}; input: {', '.join(cap.input)}" + (f"; {cap.notes}" if cap.notes else "")
            for fmt, cap in caps.formats.items()}


def dangerous_command(*texts: str) -> Optional[str]:
    """What is dangerous about the text (None if nothing found). The text is checked as given and as the person reads
    it (cleaned: NFKC, invisible characters removed), so a zero-width character or full-width letters cannot hide a
    command that the GUI and the CLI then show readable and whole."""
    for text in texts:
        shown = sanitize_untrusted(text or "")
        # The cleaning keeps a few joiners for emoji (ZWJ, variation selectors); they render invisibly, too.
        bare = "".join(ch for ch in shown if unicodedata.category(ch) not in ("Cf", "Mn"))
        for variant in (text or "", shown, bare):
            for rx, what in DANGEROUS_COMMANDS:
                if rx.search(variant):
                    return what
    return None


class DittoError(RuntimeError):
    """Ditto could not answer. str() is a message for the user; it is never shown or stored as an answer."""

    def __init__(self, message: str, *, cause: Optional[BaseException] = None):
        super().__init__(message)
        self.cause = cause


# --- error messages ---------------------------------------------------------------------------------------------

SETTINGS_HINT = "the AI settings (GUI: Tools > Configure AI Agent; CLI: llm-cli ditto login <provider>)"
WORKSPACE_SETTING_HINT = ("the AI settings (GUI: Tools > Configure AI Agent > Workspace ID; CLI: llm-cli ditto login "
                          "anthropic --workspace-id wrkspc_...; otherwise the setting ai_anthropic_workspace_id)")
CONSOLE_WORKSPACE_HINT = ("Claude needs the workspace ID for this API key (Claude Console > Settings > Workspaces). "
                          f"Enter it in {WORKSPACE_SETTING_HINT}.")
# Errors with a known cause (LLMError.code): what to change. The plain-language cause follows as the details.
CODE_HINTS = {
    "anthropic.workspace_required": CONSOLE_WORKSPACE_HINT,
    "anthropic.workspace_invalid": f"Correct the Claude workspace ID in {WORKSPACE_SETTING_HINT}.",
    "anthropic.workspace_not_found": f"Correct the Claude workspace ID in {WORKSPACE_SETTING_HINT}, or use a key "
                                     "of that workspace.",
    "anthropic.admin_key": f"Store a normal Claude API key in {SETTINGS_HINT}.",
    "anthropic.profile": "Sign in again with `ant auth login`, or go back to the stored Claude API key: "
                         "`llm-cli ditto logout anthropic --method ant_profile`.",
    "provider.removed": f"Choose another AI provider in {SETTINGS_HINT}.",
}


def describe_llm_error(err: BaseException) -> str:
    """A message for the user: what happened and what to do. The provider's own (redacted) text follows."""
    detail = str(err).strip()
    code = getattr(err, "code", None)
    if code in CODE_HINTS:
        head = CODE_HINTS[code]
    elif isinstance(err, BillingError):  # a PermissionDenied subclass: check it first
        head = ("The AI provider reports no credit or quota left on this account. Add credit or raise the limit "
                "in the provider's console, or choose another provider.")
    elif isinstance(err, AuthFailed):
        head = f"The AI provider rejected the credentials. Check the API key or sign-in in {SETTINGS_HINT}."
    elif isinstance(err, PermissionDenied):
        head = "The AI provider does not allow this request for your account or key."
    elif isinstance(err, RateLimited):
        retry_after = getattr(err, "retry_after", None)
        wait = f" Try again in about {int(retry_after) + 1} s." if retry_after else " Wait a moment and try again."
        head = "The AI provider's rate limit was reached." + wait
    elif isinstance(err, Refused):
        category = getattr(err, "category", None)
        head = (f"The model declined this request{f' ({category})' if category else ''}. Rephrase it, or add a "
                "fallback provider in the AI settings.")
    elif isinstance(err, Truncated):
        head = ("The answer was cut off before it was complete, so it was not used. Try again, ask for less, or "
                "lower the effort in the AI settings.")
    elif isinstance(err, ProviderTimeout):
        head = "The AI provider did not answer in time. Try again; with a local model a smaller one answers faster."
    elif isinstance(err, ProviderUnavailable):
        head = ("The AI provider could not be reached or is temporarily unavailable. Check the network, or that "
                "the local server (Ollama, llama-server) is running.")
    elif isinstance(err, NotConfigured):
        head = f"The AI assistant is not set up yet. Open {SETTINGS_HINT}."
    elif isinstance(err, InvalidOutput):
        head = ("The model's answer did not have the required structure, so nothing was applied. Try again or "
                "choose a more capable model.")
    elif isinstance(err, BadRequest):
        head = "The AI provider rejected the request. Check the model name in the AI settings."
    else:
        head = "The AI request failed."
    return f"{head}\nDetails: {detail}" if detail else head


# --- structured answers -----------------------------------------------------------------------------------------

# NVIDIA's prebuilt development images are an explicit opt-in of the user (decision D6): typed as the base image in the
# module wizard or `llm-cli module create`. A proposal, which a probe file can steer, never carries one; without it the
# GPU template provisions CUDA from the pinned redist archives.
PREBUILT_CUDA_IMAGE = re.compile(r"(?:^|/)nvidia/cuda(?:[:@]|$)", re.IGNORECASE)
PREBUILT_CUDA_REFUSAL = ("a prebuilt nvidia/cuda image is the user's explicit opt-in, never part of a proposal; "
                         "propose ubuntu:22.04 (CUDA comes from the framework's pinned redist archives)")


def _limit(value: str, size: int, name: str) -> str:
    # Lengths are checked here, not in the JSON schema: not every provider's strict mode accepts maxLength.
    value = value.strip()
    if len(value) > size:
        raise ValueError(f"{name} is longer than {size} characters")
    return value


class HealingAnalysis(BaseModel):
    """Ditto's analysis of a failed build. Shown to the user as a manual step; never executed."""
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="One sentence: what went wrong")
    root_cause: str = Field(description="Technical explanation of the cause")
    fix_command: str = Field(description="Suggested manual step: one shell command or a short instruction. The "
                                         "user runs it; the framework never does.")
    confidence: float = Field(ge=0, le=1, description="How sure you are, from 0 to 1")
    target: Literal["HOST", "DEVICE"] = Field(description="HOST: the build machine; DEVICE: the edge device")

    @field_validator("summary")
    @classmethod
    def _summary(cls, v: str) -> str:
        v = _limit(v, 300, "summary")
        if not v:
            raise ValueError("summary is empty")
        return v

    @field_validator("root_cause", "fix_command")
    @classmethod
    def _texts(cls, v: str, info) -> str:
        return _limit(v, 2000, info.field_name)


class CannotAnswer(BaseModel):
    """A model's statement that it cannot do a task, with the specific reason (design 1.3). "Untested" is not a
    reason; the framework accepts it only when its own check of the task input agrees."""
    model_config = ConfigDict(extra="forbid")

    reason: Literal[CANNOT_REASONS] = Field(description="Why: missing_information, not_supported, no_tool, "
                                                        "permission_denied, needs_person, hardware_unavailable, "
                                                        "insufficient_resources or limit_reached")
    missing: str = Field(description="Exactly what is missing or impossible")
    next_step: str = Field(description="What the user can do about it")

    @field_validator("missing", "next_step")
    @classmethod
    def _texts(cls, v: str, info) -> str:
        return _limit(v, 500, info.field_name)


class DittoCannot(DittoError):
    """Ditto did not do the task and says why. `confirmed`: the framework's own check of the task input agrees
    (e.g. neither a probe nor a board was given); otherwise the reason is only the model's statement."""

    # What the person reads for each reason code (speaking names, DECISIONS round 4 item 1); the code stays on .cannot.
    REASON_TEXT = {"missing_information": "information is missing", "not_supported": "not supported",
                   "no_tool": "no tool for it", "permission_denied": "not allowed by your permissions",
                   "needs_person": "needs you", "hardware_unavailable": "hardware not available",
                   "insufficient_resources": "not enough resources", "limit_reached": "a limit was reached"}

    def __init__(self, cannot: CannotAnswer, confirmed: bool):
        self.cannot, self.confirmed = cannot, confirmed
        reason = self.REASON_TEXT.get(cannot.reason, "other reason")
        what = (f"{reason}: {sanitize_untrusted(cannot.missing, 500)}. "
                f"Next: {sanitize_untrusted(cannot.next_step, 500)}")
        super().__init__(f"Ditto cannot do this ({what})" if confirmed else
                         f"Ditto did not complete this; its stated reason, not confirmed by the framework: {what}")


# A module answer that says `cannot` keeps the other fields only as placeholders (strict decoding makes the model
# send every field); they are never used.
_CANNOT_PLACEHOLDERS = {"module_name": "cannot", "architecture": "aarch64", "sdk": "none",
                        "base_os": "debian:bookworm-slim", "packages": [], "cpu_flags": "", "cmake_flags": "",
                        "setup_commands": "", "quantization_logic": "", "assumptions": [], "base_image_reason": "",
                        "package_sources": [], "pip_packages": [], "pip_index_url": "", "pinned_downloads": []}


class ModuleDefinition(BaseModel):
    """A hardware target module proposed from a probe file (input of the module generator)."""
    model_config = ConfigDict(extra="forbid")

    module_name: str = Field(description="Short target name: letters, digits, space, '_', '.', '-'; at most 64")
    architecture: Literal[ARCHITECTURES]
    sdk: str = Field(description="SDK or vendor stack, e.g. rknn-toolkit2, cuda, none")
    base_os: str = Field(description="Docker base image as image:tag, e.g. debian:bookworm-slim (a Docker Official "
                                     "Image is preferred; CUDA targets get CUDA from the framework's pinned redist "
                                     "archives, so no nvidia/cuda image); never an invented digest")
    packages: List[str] = Field(description="Debian/Ubuntu package names for the build image")
    cpu_flags: str = Field(description="GCC flags for the target CPU, e.g. -mcpu=cortex-a76 (advisory: shown in the "
                                       "module, never applied)")
    cmake_flags: str = Field(description="CMake flags for llama.cpp, e.g. -DGGML_NATIVE=OFF (advisory: the framework "
                                         "builds llama.cpp with its own recipe from the device's probe)")
    setup_commands: str = Field(description="Bash the source stage runs once before converting, as the unprivileged "
                                            "build user and without network (e.g. exporting SDK variables; what it "
                                            "exports reaches the later stages); may be empty")
    quantization_logic: str = Field(description="Body of the bash `case \"$QUANT_TYPE\" in` block of the convert stage "
                                                "(modules/convert_module.sh)")
    assumptions: List[str] = Field(default_factory=list,
                                   description="Values you guessed rather than read from the probe or the "
                                               "documentation, each with what to check first; at most 10")
    base_image_reason: str = Field(default="", description="Why this base image; required when base_os is not a "
                                                           "Docker Official Image (e.g. a vendor image)")
    package_sources: List[PackageSourceIn] = Field(default_factory=list,
                                                   max_length=INPUT_LIST_BOUNDS["package_sources"],
                                                   description="Vendor apt repositories with their signing key "
                                                               "(usually at most 3)")
    pip_packages: List[str] = Field(default_factory=list, max_length=INPUT_LIST_BOUNDS["pip_packages"],
                                    description="Vendor Python packages, each pinned as name==version (usually at "
                                                "most 30)")
    pip_index_url: str = Field(default="", max_length=300,
                               description="One https Python package index for pip_packages; empty: PyPI")
    pinned_downloads: List[PinnedDownloadIn] = Field(default_factory=list,
                                                     max_length=INPUT_LIST_BOUNDS["pinned_downloads"],
                                                     description="Vendor files with https address, size and sha256 "
                                                                 "(usually at most 5); never llama.cpp")
    cannot: Optional[CannotAnswer] = Field(default=None, description="Only when there is nothing to base a module "
                                                                     "on; untested hardware is no reason")

    @model_validator(mode="before")
    @classmethod
    def _cannot_placeholders(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("cannot"), dict):
            return {**_CANNOT_PLACEHOLDERS, "cannot": data["cannot"]}
        return data

    @model_validator(mode="after")
    def _base_image(self):
        if self.cannot is None and not is_official_image(self.base_os) and not self.base_image_reason:
            raise ValueError("base_image_reason is required when base_os is not a Docker Official Image "
                             f"('{self.base_os}' comes from {self._registry()})")
        return self

    def _registry(self) -> str:
        from orchestrator.utils.image_ref import split_image
        info = split_image(self.base_os)
        return f"{info['registry']}, image {info['repository']}"

    @field_validator("assumptions")
    @classmethod
    def _assumptions(cls, v: List[str]) -> List[str]:
        if len(v) > 10:
            raise ValueError("assumptions has more than 10 entries")
        return [_limit(a, 300, "an assumption") for a in v if a.strip()]

    @field_validator("base_image_reason")
    @classmethod
    def _reason(cls, v: str) -> str:
        return _limit(v, 1000, "base_image_reason")

    @field_validator("module_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not MODULE_NAME_RE.fullmatch(v):
            raise ValueError("module_name must be 1-64 letters, digits, spaces, '_', '.' or '-', starting with a "
                             "letter or digit")
        return v

    @field_validator("sdk", "base_os", "cpu_flags", "cmake_flags")
    @classmethod
    def _short(cls, v: str, info) -> str:
        return _limit(v, 500, info.field_name)

    @field_validator("base_os")
    @classmethod
    def _no_prebuilt_cuda(cls, v: str) -> str:
        if PREBUILT_CUDA_IMAGE.search(v):
            raise ValueError(PREBUILT_CUDA_REFUSAL)
        return v

    @field_validator("setup_commands", "quantization_logic")
    @classmethod
    def _code(cls, v: str, info) -> str:
        return _limit(v, 8000, info.field_name)

    @field_validator("packages")
    @classmethod
    def _packages(cls, v: List[str]) -> List[str]:
        if len(v) > 100:
            raise ValueError("packages has more than 100 entries")
        return [p.strip() for p in v if p.strip()]


def proposal_facts(definition: Union["ModuleDefinition", Dict[str, Any]]) -> str:
    """What the person must see before an AI-proposed module is created, besides its values: the base image block
    (registry, digest, Docker Official Image or plainly another registry, the image build runs with network, the
    AI's reason), the values the AI guessed, and that the module is untested."""
    data = definition.model_dump() if isinstance(definition, BaseModel) else dict(definition)
    lines = [base_image_block(str(data.get("base_os") or ""), str(data.get("base_image_reason") or ""))]
    assumptions = [sanitize_untrusted(str(a), 300) for a in data.get("assumptions") or [] if str(a).strip()]
    if assumptions:
        lines.append("Guessed by the AI (check these first):")
        lines += [f"  - {a}" for a in assumptions]
    from orchestrator.Core.module_generator import vendor_fact_lines
    vendor = [sanitize_untrusted(line, 600) for line in vendor_fact_lines(data)]
    if vendor:
        lines.append("Vendor software the image build adds (written by the AI; installed with network access):")
        lines += [f"  - {line}" for line in vendor]
    lines.append("The module is untested on real hardware: it is created with support level community.")
    return "\n".join(lines)


def ai_module_description(name: str, assumptions: Sequence[Any], lead: str = "") -> str:
    """The description of a target module made from Ditto's proposal (`llm-cli module generate-ai` and the module
    wizard alike): untested is a fact to state, not a reason to refuse, so it says so, with the values Ditto guessed
    (brackets removed: target.yml must not look like a template placeholder). `lead`: the person's own words for the
    module (the module wizard's Description field), kept in front of that note."""
    cleaned = [re.sub(r"[\[\]{}]", "", str(a)).strip() for a in assumptions or [] if str(a).strip()]
    description = f"AI-generated target for {name}, untested on real hardware"
    if cleaned:
        description += ". Assumptions: " + "; ".join(cleaned)[:1500]
    lead = " ".join(str(lead or "").split()).rstrip(".")
    return f"{lead}. {description}" if lead else description


# provider_problem() and the settings overlay for one run (RunSettings) are the shared ones of Core/ai_override.py
# (imported above): `module generate-ai --provider --model`, the module wizard's "for this analysis only", `ditto
# ask/eval --provider --model`, the chat panel's "For this question" and Ditto > Evaluation... check a provider with
# the same words and read the chosen values over the saved settings the same way.


def pin_base_image(definition: "ModuleDefinition", offline: bool = False, client: Any = None) -> "ModuleDefinition":
    """The proposal with its base image pinned to the digest its tag points at now (DECISIONS round 3), before the
    person is asked: the module is written with exactly the digest shown (`llm-cli module generate-ai` and the
    module wizard's AI import alike). A reference with a digest stays as it is. Raises ImageLookupError (with
    .next) when the tag cannot be resolved (offline mode, the registry cannot be reached)."""
    if "@sha256:" in definition.base_os:
        return definition
    return definition.model_copy(update={"base_os": pin_reference(definition.base_os, offline=offline, client=client)})


def pin_reference(reference: str, offline: bool = False, client: Any = None) -> str:
    """The image reference pinned to the digest its tag points at now (one that has a digest stays as it is): what an
    AI-made module is written with, also for a base image an AI filled into the module wizard through the GUI tools.
    Network: call it off the GUI thread. Raises ImageLookupError (with .next) when the tag cannot be resolved."""
    if "@sha256:" in reference:
        return reference
    from orchestrator.utils.image_registry import inspect_image
    return inspect_image(reference, offline=offline, client=client).pinned


class OptimizerRecommendation(BaseModel):
    """Build settings for a model on a target (format and quantization from the target's own options)."""
    model_config = ConfigDict(extra="forbid")

    format: str = Field(description="One of the offered formats")
    quantization: str = Field(description="One of the quantization types offered for that format")
    optimization: Literal[OPTIMIZATION_LEVELS]
    explanation: str = Field(description="Why, in one to three sentences")
    cannot: Optional[CannotAnswer] = Field(default=None, description="Only when no offered format takes this model; "
                                                                     "an unmeasured board is no reason")

    @field_validator("explanation")
    @classmethod
    def _explanation(cls, v: str) -> str:
        return _limit(v, 1500, "explanation")


# --- chat history -----------------------------------------------------------------------------------------------

@dataclass
class ToolUse:
    """One tool call during a chat turn, for the transcript."""
    name: str
    title: str
    ok: bool
    detail: str = ""  # why it failed or was not run (the model saw the same text)


def tool_note(use: ToolUse) -> str:
    """The transcript line of a tool use (plain text)."""
    return f"Used tool: {use.title}" if use.ok else f"Tool not run: {use.title} ({use.detail})"


@dataclass
class ChatReply:
    question: str
    text: str
    # the results that close an earlier turn's open tool calls (CALL_NOT_RUN), the user turn and everything the model
    # and the tools added, in order
    new_messages: List[Message]
    new_conversation: bool = False    # earlier turns were summarized into a new conversation
    tools: List[ToolUse] = field(default_factory=list)
    stop: str = "end"                 # end | max_tokens | max_steps
    provider: str = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    # True only once, for a saved conversation of the removed remote mode (a history whose tool source names a
    # framework on another computer): its tool calls named that computer's objects, so it starts anew.
    source_changed: bool = False
    # Why earlier turns were summarized (new_conversation): "long", or "no_tools" for a conversation with tool turns
    # asked without tools (tool turns need the tool definitions, which a question without tools does not send).
    new_conversation_reason: str = ""


# The transcript notice of a conversation summarized into a new one, by ChatReply.new_conversation_reason (the chat
# panel's chat.notice.new_conversation and chat.notice.new_conversation_no_tools).
NEW_CONVERSATION_NOTICES = {
    "long": "The conversation got long: Ditto summarized it and continues in a new conversation.",
    "no_tools": "Answered without tools: Ditto summarized the conversation, which used tools, and continues in a new "
                "conversation.",
}


class TranscriptEntry(BaseModel):
    role: Literal["user", "assistant", "tool", "notice"]
    text: str


# The notice of a conversation of the removed remote mode that starts anew (the chat panel's chat.notice.source_local).
SOURCE_LOCAL_NOTICE = "Ditto now uses its own tools: a new conversation."


class ChatHistory:
    """
    Ditto's chat, persisted as JSON (mode 0600) in the per-user state directory.

    messages    the current API conversation, append-only, with the providers' own assistant turns
    transcript  what the chat window shows, across conversations (tool uses and notices included)

    The file of earlier versions held a plain [{"role", "content"}] list; it is migrated on load. Versions 2 and 3
    are read as they are; the fields the removed remote mode wrote (a tool source naming another computer, a tool
    use's place) are read and ignored, and such a conversation starts anew once (ChatReply source_changed).
    """

    FILE_NAME = "chat_history.json"
    VERSION = 3
    READS = (2, 3)
    MAX_TRANSCRIPT = 500

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self.messages: List[Message] = []
        self.transcript: List[TranscriptEntry] = []
        self.tool_source = "local"  # kept in the file for earlier readers; "mcp:..." came from the removed remote mode

    @classmethod
    def in_state_dir(cls, state_dir: Path) -> "ChatHistory":
        return cls.load(Path(state_dir) / cls.FILE_NAME)

    @classmethod
    def load(cls, path: Path) -> "ChatHistory":
        history = cls(path)
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return history
        except (OSError, ValueError) as e:
            logger.warning(f"Chat history {path} cannot be read ({e.__class__.__name__}); starting a new one.")
            history._keep_copy()
            return history
        if isinstance(raw, list):
            history._migrate_plain(raw)
            history.save()
        elif isinstance(raw, dict) and raw.get("version") in cls.READS:
            history._read(raw)
        else:
            logger.warning(f"Chat history {path} has an unknown format; starting a new one.")
            history._keep_copy()
        return history

    def _migrate_plain(self, entries: list) -> None:
        for entry in entries:
            if (not isinstance(entry, dict) or entry.get("role") not in ("user", "assistant")
                    or not isinstance(entry.get("content"), str)):
                continue  # damaged entries are skipped; only the two known keys are kept
            self.messages.append(Message(role=entry["role"], content=entry["content"]))
            self.transcript.append(TranscriptEntry(role=entry["role"], text=entry["content"]))

    def _read(self, raw: dict) -> None:
        source = raw.get("tool_source")
        self.tool_source = source if isinstance(source, str) and source else "local"
        transcript = raw.get("transcript")
        for entry in transcript if isinstance(transcript, list) else []:
            try:
                self.transcript.append(TranscriptEntry.model_validate(entry))
            except ValidationError:
                continue
        messages = raw.get("messages")
        try:
            if messages is not None and not isinstance(messages, list):
                raise ValueError("messages is not a list")
            self.messages = [Message.model_validate(m) for m in messages or []]
            if not conversation_is_consistent(self.messages):
                # Every provider rejects a conversation that goes on after an unanswered call (an OpenAI-compatible
                # server may have accepted it): every later question on it would fail.
                raise ValueError("tool calls and tool results do not pair up")
        except (ValidationError, ValueError):
            # A conversation with a missing turn could pair a tool call with the wrong result: start anew.
            self.messages = []
            self.transcript.append(TranscriptEntry(role="notice", text="The saved conversation could not be read; "
                                                                       "Ditto starts a new one."))

    def _keep_copy(self) -> None:
        if self.path is None:
            return
        try:
            bad = self.path.with_name(self.path.name + ".bad")
            os.replace(self.path, bad)
        except OSError:
            pass

    def save(self) -> bool:
        if self.path is None:
            return True
        data = {"version": self.VERSION, "tool_source": self.tool_source,
                "messages": [m.model_dump(mode="json", exclude_defaults=True) for m in self.messages],
                "transcript": [t.model_dump() for t in self.transcript]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except OSError as e:
            logger.warning(f"Could not save the chat history: {e}")
            return False

    def migrating(self) -> bool:
        """Whether this conversation comes from the removed remote mode (its tool source names a framework on another
        computer): its tool calls named that computer's objects, so it starts anew once."""
        source = self.tool_source if isinstance(self.tool_source, str) else ""
        return source.startswith("mcp:")

    def apply(self, reply: ChatReply) -> None:
        """Appends a finished turn (only finished turns: an error is not an answer)."""
        if reply.source_changed:
            self.messages = []
            self.transcript.append(TranscriptEntry(role="notice", text=SOURCE_LOCAL_NOTICE))
        elif reply.new_conversation:
            self.messages = []
            notice = NEW_CONVERSATION_NOTICES.get(reply.new_conversation_reason, NEW_CONVERSATION_NOTICES["long"])
            self.transcript.append(TranscriptEntry(role="notice", text=notice))
        self.tool_source = "local"
        self.messages.extend(reply.new_messages)
        self.transcript.append(TranscriptEntry(role="user", text=reply.question))
        for use in reply.tools:
            self.transcript.append(TranscriptEntry(role="tool", text=tool_note(use)))
        self.transcript.append(TranscriptEntry(role="assistant", text=reply.text))
        del self.transcript[:-self.MAX_TRANSCRIPT]

    def clear(self) -> None:
        self.messages = []
        self.transcript = []
        self.tool_source = "local"


def render_transcript(messages: Sequence[Message], tool_text: int = 800) -> str:
    """A conversation as plain text (for summaries); tool results are shortened."""
    lines = []
    for msg in messages:
        if msg.role == "tool":
            text = msg.text()
            lines.append(f"[tool result{' (error)' if msg.is_error else ''}] "
                         f"{text[:tool_text]}{' ...' if len(text) > tool_text else ''}")
            continue
        if msg.text():
            lines.append(f"{msg.role}: {msg.text()}")
        for call in msg.tool_calls:
            lines.append(f"[tool call] {call.name} {json.dumps(call.arguments, ensure_ascii=False, default=str)}")
    return "\n".join(lines)


# --- prompts ----------------------------------------------------------------------------------------------------
# The task texts live in orchestrator/llm/task_prompts.py, one source for Ditto and the MCP prompts.

# SUMMARY_SYSTEM, the conversation summary's system text, lives there too (imported above under the same name).


def _text_block(text: str) -> Dict[str, str]:
    return {"type": "text", "text": text}


# --- Ditto ------------------------------------------------------------------------------------------------------

class DittoCoder:
    """Ditto: chat with tools, build-log analysis, module generation and build recommendations."""

    def __init__(self, framework_manager: Any = None, *, config_manager: Any = None, secrets_manager: Any = None,
                 client: Optional[LLMClient] = None, registry: Any = None):
        self.logger = logger
        self.framework = framework_manager
        self.config = config_manager if config_manager is not None else getattr(framework_manager, "config", None)
        self._secrets = secrets_manager
        self._client = client
        self._own_client = client is None  # an injected client (tests, tools) is kept by reload()
        self._registry_override = registry

    # --- settings and wiring ------------------------------------------------------------------------------
    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            value = self.config.get(key, default) if self.config is not None else default
        except Exception:
            value = default
        return default if value is None else value

    @property
    def offline_mode(self) -> bool:
        """Only local providers are used (read live from the settings: utils/locality.offline_mode, the one offline
        check)."""
        from orchestrator.utils.locality import offline_mode
        return offline_mode(self.config)

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            secrets = self._secrets if self._secrets is not None else getattr(self.framework, "secrets_manager", None)
            self._client = LLMClient(self.config, secrets)
        return self._client

    def reload(self) -> None:
        """Drops the provider clients so changed settings and keys take effect on the next call (the settings
        themselves are read on every call)."""
        if self._own_client:
            self._client = None

    def _registry(self):
        return self._registry_override if self._registry_override is not None \
            else getattr(self.framework, "tool_registry", None)

    def _rag_manager(self):
        if self.framework is None or not self._cfg("enable_rag_knowledge", False):
            return None
        ensure = getattr(self.framework, "ensure_rag_manager", None)
        return ensure() if ensure else getattr(self.framework, "rag_manager", None)

    def _attach_local_runtime(self, client: LLMClient) -> None:
        """Hands a running managed llama-server to the layer when the "local" provider may be used."""
        if self.framework is None:
            return
        try:
            providers = [client.primary_provider().id]
            providers += [client.catalog.resolve_id(p) or p for p, _ in client.fallback_targets("chat")]
            managed = [pid for pid in providers
                       if getattr(client.catalog.providers.get(pid), "runtime_endpoint", False)]
        except Exception:  # not set up: the call itself reports that
            return
        if not managed:
            return
        try:
            from orchestrator.runtime.llama_server import LocalLLMRuntime
            endpoint = LocalLLMRuntime.from_framework(self.framework).endpoint()
        except Exception as e:  # the runtime is optional; the layer then reports the server as not running
            self.logger.debug(f"Local runtime not available: {e.__class__.__name__}")
            endpoint = None
        for pid in managed:
            if endpoint is None:
                client.set_runtime_endpoint(pid, None)
            else:
                client.set_runtime_endpoint(pid, endpoint.base_url, endpoint.api_key, endpoint.model)

    def _client_for_call(self) -> LLMClient:
        client = self.client
        self._attach_local_runtime(client)
        return client

    def for_run(self, provider: Optional[str] = None, model: Optional[str] = None) -> "DittoCoder":
        """Ditto with the AI provider and/or model chosen for one run only (`--provider/--model`, the module wizard's
        "for this analysis only"): a Ditto of its own on the shared settings overlay (Core/ai_override.ditto_with, the
        same path as the chat panel's "For this question" and Ditto > Evaluation...), so the saved AI settings stay
        unchanged. This Ditto itself when neither is given. DittoError when the provider is unknown or removed."""
        if not provider and not model:
            return self
        problem = provider_problem(self.client.catalog, provider)
        if problem:
            raise DittoError(problem)
        from orchestrator.Core.ai_override import ditto_with, override_values
        return ditto_with(self, override_values(provider, model, ditto=self))

    def pin_base_image(self, definition: "ModuleDefinition") -> "ModuleDefinition":
        """pin_base_image() in this Ditto's offline mode (the settings' offline_mode)."""
        return pin_base_image(definition, offline=self.offline_mode)

    def describe(self, task: str = "chat") -> Dict[str, Any]:
        """Provider and model a call would use (no secrets); raises DittoError when not set up."""
        try:
            return self._client_for_call().describe(task)
        except LLMError as e:
            raise self._error(e) from None

    def is_configured(self, task: str = "chat") -> bool:
        try:
            self._client_for_call().resolve(task)
            return True
        except LLMError:
            return False

    def _error(self, err: LLMError, done: Sequence[str] = ()) -> DittoError:
        """The person's message for a failed request. `done`: titles of the tools that already ran in the failed chat
        turn; the turn is not stored, so the message says so (a build may have started)."""
        self.logger.warning(f"AI request failed: {type(err).__name__}: {err}")
        message = describe_llm_error(err)
        done = list(dict.fromkeys(done))
        if done:
            message += (f"\nAlready done in this turn before the error: {', '.join(done)}. Ditto will not remember "
                        "this turn, so check what these tools did before you ask again.")
        return DittoError(message, cause=err)

    # --- knowledge base -----------------------------------------------------------------------------------
    def _knowledge(self, query: str, limit: int = 5, rerank: Optional[bool] = None) -> str:
        """Matching knowledge-base excerpts as one untrusted block ("" when disabled or nothing matches), within
        a third of the context limit. rerank: see RAGManager.search (None: rag_rerank_default)."""
        if not (query or "").strip():
            return ""
        # The same function builds the knowledge context of the MCP prompts (task_prompts.knowledge_excerpts).
        return knowledge_excerpts(self._rag_manager(), query, limit=limit, rerank=rerank,
                                  max_tokens=self._context_limit() // 3, logger=self.logger)

    # --- structured answers -------------------------------------------------------------------------------
    def _structured(self, task: str, system: str, prompt: str, output_model: Type[BaseModel]) -> BaseModel:
        request = ChatRequest(system=system, messages=[Message.user(prompt)], output_model=output_model, task=task)
        try:
            result = self._client_for_call().chat(request)
        except LLMError as e:
            raise self._error(e) from None
        if not isinstance(result.parsed, output_model):
            raise DittoError("The AI answer did not contain the expected structured result, so nothing was applied. "
                             "Try again or choose a more capable model.")
        return result.parsed

    def analyze_error_log(self, log_content: str, context_info: str = "") -> HealingAnalysis:
        """A fix proposal for a failed build, from the log and context given here and this framework's knowledge
        base. Raises DittoError; the proposal is advice, never executed."""
        log_content = log_content or ""
        # Never reranked: the cross-encoder scores log lines against prose poorly and lost relevant excerpts in the
        # retrieval evaluation (cases tagged log).
        known = self._knowledge(healing_query(log_content), limit=3, rerank=False)
        parts = healing_parts(log_content, context_info, known)
        parts.append("Analyze why the build failed and propose one fix.")
        analysis = self._structured("healing", HEALING_SYSTEM, "\n\n".join(parts), HealingAnalysis)
        danger = dangerous_command(analysis.fix_command, analysis.root_cause, analysis.summary)
        if danger:
            raise DittoError(f"Ditto's proposal contained a dangerous command ({danger}), so it was discarded. The "
                             "build log may contain instructions aimed at the AI; check the log yourself.")
        return analysis

    def _task_answer(self, task: str, system: str, parts: List[str], output_model: Type[BaseModel]) -> BaseModel:
        """A structured answer that may say `cannot`. It is called only when the framework's own check of the task
        input found what the task needs, so a `cannot` is not confirmed: it is asked once more with the rule text
        (untested hardware is no reason), and a second one reaches the person as not confirmed (DittoCannot)."""
        answer = self._structured(task, system, "\n\n".join(parts), output_model)
        if getattr(answer, "cannot", None) is None:
            return answer
        answer = self._structured(task, system, "\n\n".join(parts + [CANNOT_RETRY]), output_model)
        if getattr(answer, "cannot", None) is None:
            return answer
        raise DittoCannot(answer.cannot, confirmed=False)

    def generate_module(self, probe_file: Optional[Path] = None, board: str = "") -> ModuleDefinition:
        """A target module definition from a hardware probe (target_hardware_config.txt), or from the board and its
        SoC (`board`, free text) when there is no probe. With neither there is nothing to base a module on: the
        framework says so itself (DittoCannot, missing_information) and asks no model."""
        probe = ""
        if probe_file is not None:
            probe_file = Path(probe_file)
            if not probe_file.is_file():
                raise FileNotFoundError(f"Probe file not found: {probe_file}")
            probe = probe_file.read_text(encoding="utf-8", errors="ignore")
        board = (board or "").strip()
        if not probe.strip() and not board:
            raise DittoCannot(CannotAnswer(reason="missing_information",
                                           missing="a hardware probe of the board, or the board's name and its SoC",
                                           next_step="Import the board's probe (llm-cli scan, or the GUI), or name "
                                                     "the board and its SoC."), confirmed=True)
        # Never reranked either: for this keyword string the floor dropped the relevant excerpts too.
        docs = self._knowledge(module_query(probe, board), rerank=False)
        parts = module_parts(probe, board, docs)
        parts.append("Propose the module definition for this hardware.")
        return self._task_answer("json", MODULE_SYSTEM, parts, ModuleDefinition)

    def _format_notes(self, target: str) -> Dict[str, str]:
        """Label, accepted inputs and notes of each format of `target` (empty without a target registry)."""
        tm = getattr(self.framework, "target_manager", None)
        try:
            caps = tm.get_capabilities(target) if tm is not None else None
        except Exception:
            caps = None
        return format_notes(caps) if caps is not None else {}

    def recommend_build_settings(self, model: str, target: str, options: Dict[str, Sequence[str]],
                                 board: Optional[str] = None, use_knowledge: bool = False,
                                 notes: Optional[Dict[str, str]] = None) -> OptimizerRecommendation:
        """Format, quantization and optimization level for `model` on `target`. `options` maps each format the
        target can build to its quantization types; the answer is checked against them. `notes` describes each
        format (label, accepted inputs); by default it comes from the target's capabilities."""
        options = {str(fmt): [str(q) for q in qs] for fmt, qs in options.items() if qs}
        if not options:
            raise DittoError("This target offers no format with quantization types to choose from.")
        formats = list(options)
        quants = sorted({q for qs in options.values() for q in qs})
        # The JSON schema itself lists the choices, so constrained decoding can only produce offered values;
        # the quantization is checked below per format (case-insensitively, like the target capabilities).
        schema = create_model(
            "OptimizerRecommendation", __base__=OptimizerRecommendation,
            format=(Literal[tuple(formats)], Field(description="One of the offered formats",
                                                   json_schema_extra={"enum": formats})),
            quantization=(str, Field(description="A quantization type offered for that format",
                                     json_schema_extra={"enum": quants})))
        notes = self._format_notes(target) if notes is None else notes
        known = (self._knowledge(f"{target} {board or ''} {model} quantization performance", rerank=True)
                 if use_knowledge else "")
        parts = optimizer_parts(model, target, board, format_listing(options, notes), known)
        parts.append("Recommend the build settings.")
        # The target offers formats (checked above), so a `cannot` is not confirmed by the framework.
        answer = self._task_answer("json", OPTIMIZER_SYSTEM, parts, schema)
        danger = dangerous_command(answer.explanation)
        if danger:
            raise DittoError(f"Ditto's recommendation contained a dangerous command ({danger}), so it was discarded. "
                             "Check the model name for instructions aimed at the AI.")
        offered = {q.lower(): q for q in options[answer.format]}
        if answer.quantization.lower() not in offered:
            raise DittoError(f"Ditto recommended {answer.quantization} for {answer.format}, which this target does "
                             "not offer for that format. Nothing was changed; try again or choose the settings "
                             "yourself.")
        return OptimizerRecommendation(format=answer.format, quantization=offered[answer.quantization.lower()],
                                       optimization=answer.optimization, explanation=answer.explanation)

    def save_module(self, definition: Union[ModuleDefinition, Dict[str, Any]], targets_dir: Path,
                    module_name: Optional[str] = None, profile=None, knowledge_roots=None,
                    generator: Any = None, description: str = "") -> Path:
        """Creates the module through the module generator (which validates every value again). `profile` is the
        parsed hardware probe of the target, when the probe file was one: the runtime: block of the module is the
        build recipe's plan from it. The proposal's cpu_flags and cmake_flags are kept there as advisory text and
        never applied (decision D20). A proposal never opts in to a prebuilt nvidia/cuda image (decision D6). The one
        save of an AI-made module (`llm-cli module generate-ai` and the module wizard); knowledge_roots: the pages
        crawled for this target that the person chose to attach (the wizard's opt-in); generator: the module
        generator class the caller uses (default ModuleGenerator); description: the person's own words for the
        module (the wizard's Description field), kept in front of the framework's untested note."""
        data = definition.model_dump() if isinstance(definition, BaseModel) else dict(definition)
        if PREBUILT_CUDA_IMAGE.search(str(data.get("base_os") or "")):
            raise ValueError(f"base_os: {PREBUILT_CUDA_REFUSAL}")
        # The same rule as create_target_module (DECISIONS round 3 (d)): an image that is not a Docker Official Image
        # needs the AI's reason, which the person reads before approving.
        from orchestrator.utils.image_ref import is_official_image
        base = str(data.get("base_os") or "debian:bookworm-slim").strip()
        if not is_official_image(base) and not str(data.get("base_image_reason") or "").strip():
            raise ValueError(f"base_os: '{base}' is not a Docker Official Image, so base_image_reason is required "
                             "(why this image); or use an official image with the vendor's package_sources.")
        from orchestrator.Core.module_generator import generator_input
        data = generator_input(data, module_name, description=description)
        return (generator or ModuleGenerator)(Path(targets_dir)).generate_module(data, self.framework,
                                                                                knowledge_roots=knowledge_roots,
                                                                                profile=profile)

    # --- chat ---------------------------------------------------------------------------------------------
    def _context_limit(self) -> int:
        try:
            return max(512, int(self._cfg("chat_context_limit", 4096)))
        except (TypeError, ValueError):
            return 4096

    def _too_long(self, messages: Sequence[Message]) -> bool:
        if len(messages) < MIN_MESSAGES_TO_SUMMARIZE:
            return False
        chars = sum(len(m.text()) + sum(len(json.dumps(c.arguments, default=str)) for c in m.tool_calls)
                    for m in messages)
        return chars / 4 > self._context_limit()  # about four characters per token

    def _summarize(self, client: LLMClient, messages: Sequence[Message]) -> str:
        """A summary of the conversation for the start of a new one (a separate request; the conversation
        itself is not changed)."""
        transcript = render_transcript(messages)[-40000:]
        request = ChatRequest(system=SUMMARY_SYSTEM, task="chat", effort="low", max_tokens=2000, messages=[
            Message.user("Summarize this conversation:\n" + wrap_untrusted(transcript, "conversation", max_len=40000))])
        try:
            summary = client.chat(request).text.strip()
            if summary:
                return summary
        except LLMError as e:
            self.logger.warning(f"Summarizing the chat failed ({type(e).__name__}); keeping its last exchange.")
        return render_transcript(messages[-4:])[-4000:]

    def _tool_specs(self, registry):
        if registry is None:
            return []
        try:
            # The same tools an MCP client with the same permissions is offered (no subset by model size).
            names = [s.name for s in registry.visible("ditto")]
        except Exception as e:
            self.logger.warning(f"Tools unavailable for this question: {e}")
            return []
        return tool_specs_from_registry(registry, names) if names else []

    def _executor(self, registry, confirm: Optional[Confirm], uses: List[ToolUse],
                  on_tool: Optional[Callable[[ToolUse], None]]):
        """execute_tool for a chat turn: this computer's registry (validation, permissions, audit). One session per
        question, for the repeat guard and the example calls after repeated input errors; output and handler errors
        come back as untrusted blocks, the framework's own refusals as they are."""
        run = make_registry_executor(registry, ToolContext(actor="ditto", confirm=confirm,  # output: untrusted block
                                                           session=f"ditto-{secrets.token_hex(6)}"))

        def title_of(name: str) -> str:
            try:
                return registry.get(name).title
            except Exception:  # noqa: BLE001 - an unknown name keeps its own words
                return str(name)

        def note(use: ToolUse) -> None:
            uses.append(use)
            if on_tool is not None:
                try:
                    on_tool(use)
                except Exception:
                    pass

        def execute(name: str, arguments: dict) -> str:
            try:
                output = run(name, arguments)
            except (ToolDenied, ToolInputError) as e:  # the framework's own messages
                note(ToolUse(str(name), title_of(name), False, str(e)))
                raise
            except ToolError as e:
                # Handler errors can carry external text (server replies, log lines): data, not instructions. The
                # reason code and field stay, so the loop tells a fix from a repeat.
                note(ToolUse(str(name), title_of(name), False, redact_secrets(str(e))[:300]))
                raise ToolError(wrap_untrusted(str(e), source=f"tool error:{name}", max_len=4000), code=e.code,
                                field=e.field) from None
            note(ToolUse(str(name), title_of(name), True))
            return output

        return execute

    def chat(self, question: str, history: Optional[ChatHistory] = None, *, confirm: Optional[Confirm] = None,
             use_tools: bool = True, on_tool: Optional[Callable[[ToolUse], None]] = None) -> ChatReply:
        """One user turn. `history` is only read: the caller appends the reply with history.apply(reply).
        `confirm(spec, input)` answers tool calls that need confirmation (None: they are denied). The tools are the
        framework's own on this computer (the same ones an MCP client with the same permissions is offered)."""
        question = (question or "").strip()
        if not question:
            raise DittoError("Please enter a question.")
        client = self._client_for_call()
        earlier = list(history.messages) if history is not None else []
        blocks: List[Dict[str, str]] = []
        new_conversation = False
        # A conversation of the removed remote mode named another computer's objects: anew, once.
        source_changed = bool(history is not None and earlier and getattr(history, "migrating", lambda: False)())
        if source_changed:
            earlier = []
        registry = self._registry() if use_tools else None
        tools = self._tool_specs(registry)
        has_tool_turns = any(m.role == "tool" or m.tool_calls for m in earlier)
        # Tool turns need tool definitions (Claude rejects the request otherwise): asked without tools (no registry),
        # the conversation continues as a new one, like a long one.
        reason = "long" if self._too_long(earlier) else ("no_tools" if has_tool_turns and registry is None else "")
        if reason:
            summary = self._summarize(client, earlier)
            earlier, new_conversation, has_tool_turns = [], True, False
            blocks.append(_text_block("Summary of our earlier conversation, for context:\n"
                                      + wrap_untrusted(summary, "conversation summary")))
        if not tools and has_tool_turns:
            # The registry is there (see above) and still denies every call the permissions forbid.
            tools = tool_specs_from_registry(registry)
        # A turn cut off during a tool call (saved by an earlier version) is closed first, by appending results.
        repairs = close_open_calls(earlier)
        if not tools:
            # Without tools Ditto cannot search the knowledge base itself, so matching excerpts come along.
            excerpts = self._knowledge(question)
            if excerpts:
                blocks.append(_text_block("Possibly relevant knowledge-base excerpts:\n" + excerpts))
        blocks.append(_text_block(question))
        user_turn = Message(role="user", content=blocks if len(blocks) > 1 else question)
        system = CHAT_INTRO + "\n\n" + (CHAT_TOOLS if tools else CANNOT_RULE) + "\n\n" + UNTRUSTED_RULE
        request = ChatRequest(system=system, messages=[*earlier, *repairs, user_turn], tools=tools, task="chat")
        uses: List[ToolUse] = []
        try:
            if tools:
                loop = run_tool_loop(client, request, self._executor(registry, confirm, uses, on_tool),
                                     max_steps=MAX_TOOL_STEPS, extra_steps=FIX_EXTRA_STEPS)
                if loop.stop == "refusal":
                    raise loop.refusal   # the same message as any other failed turn, with the tools that ran
                results, new, text, stop, usage = loop.results, loop.new_messages, loop.text, loop.stop, loop.usage
            else:
                result = client.chat(request)
                results, new, text, stop, usage = [result], result.new_messages, result.text, result.stop, result.usage
                new = [*new, *close_open_calls(new)]  # the tool loop answers a call cut off in it itself
        except LLMError as e:
            raise self._error(e, done=[use.title for use in uses if use.ok]) from None
        text = text.strip()
        if stop == "max_steps":
            steps = getattr(loop, "steps", MAX_TOOL_STEPS) if tools else MAX_TOOL_STEPS
            text = (text + "\n\n" if text else "") + (f"(Ditto stopped after {steps} steps without a final "
                                                       "answer. Ask to continue if needed.)")
        elif stop == "max_tokens":
            text = (text + "\n\n" if text else "") + "(The answer was cut off at the length limit.)"
        elif not text:
            text = "(Ditto gave no answer.)"
        last = results[-1] if results else None
        return ChatReply(question=question, text=text, new_messages=[*repairs, user_turn, *new],
                         new_conversation=new_conversation, tools=uses, stop=stop,
                         provider=getattr(last, "provider", ""), model=getattr(last, "model", ""), usage=usage,
                         source_changed=source_changed, new_conversation_reason=reason)
