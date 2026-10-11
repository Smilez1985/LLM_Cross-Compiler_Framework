#!/usr/bin/env python3
"""
The framework's MCP prompts (design 4.3): Ditto's tasks for an external client in Ditto's role, with the same task
text (orchestrator/llm/task_prompts.py) and the same knowledge context Ditto gets (DECISIONS round 1 item 5).

A prompt grants nothing: every step still goes through the tools and their permissions. Each prompt is two user
messages: first its instructions, a constant written by the framework (no argument value, no external text), then
the context the server put together, where every value from outside the framework (the arguments, target texts,
build logs, probes, knowledge excerpts) is inside a wrap_untrusted block. Every prompt carries the honest-"cannot"
rule. A prompt is listed only when every tool it leads to is registered, so it never sends a model to a tool that
does not exist (add_board waits for create_target_module, deploy_to_board for the deploy tools).
"""

import json
from typing import Any, Dict, List, Optional

from orchestrator.llm.task_prompts import (CANNOT_RULE, FRAMEWORK, HEALING_TASK, MODULE_TASK, OPTIMIZER_TASK,
                                           format_listing, healing_parts, healing_query, knowledge_excerpts,
                                           module_parts, module_query, optimizer_parts)
from orchestrator.tools.framework_resources import existing_docs
from orchestrator.tools.registry import PromptArg, PromptSpec, ResourceUnknown, ToolContext, ToolRegistry
from orchestrator.utils.untrusted import UNTRUSTED_RULE, wrap_untrusted

CLIENT_ROLE = ("You are connected to the LLM Cross-Compiler Framework over MCP and do this task for the user, as the "
               f"framework's own assistant Ditto does it. {FRAMEWORK} ")
CLOSING = "\n\n" + CANNOT_RULE + "\n\n" + UNTRUSTED_RULE

ASK_INSTRUCTIONS = (
    CLIENT_ROLE + "Answer the user's question about the framework. For facts about targets, boards, builds, built "
    "models and this installation, call the framework's read tools (list_targets, get_target_capabilities, "
    "list_build_requests, get_build_status, read_build_log, list_artifacts, run_doctor, search_knowledge, get_status) "
    "instead of answering from memory, and use the documentation resources listed below. You may add your own "
    "knowledge; say which part comes from the framework. Answer in the language of the question, concisely."
    + CLOSING)
CONVERT_INSTRUCTIONS = (
    CLIENT_ROLE + "Convert a model for a board. 1. Use only the formats, boards and quantization types the target's "
    "capabilities below list (get_target_capabilities shows them again; list_targets shows the other targets). "
    "2. The model is a Hugging Face repo id (owner/name), or a local model the person names as <label>:<relative "
    "path> inside a folder the AI may use (for example models:qwen2.5-0.5b-instruct); never a path. Prefer a model "
    "that needs no licence acceptance unless the user wants another one. 3. Tell the user what you will build "
    "(model, target, format, board, quantization), then call start_build; the person confirms it if their "
    "permissions ask. 4. Follow the build with get_build_status and wait_seconds=50 until it ends; if it waits for a "
    "self-healing decision, show the proposal to the user and call resolve_healing only with their decision. "
    "5. Show the result with list_artifacts. Deploying to a device is done by the person in the framework's GUI or "
    "CLI; ask before suggesting it. A board nobody has measured is no reason to refuse: say that its fit is unknown."
    + CLOSING)
RECOMMEND_INSTRUCTIONS = (
    CLIENT_ROLE + OPTIMIZER_TASK + " Give the format, the quantization type, an optimization level (BALANCED, "
    "SPEED, SIZE or AGGRESSIVE) and your reason. Start the build with start_build only when the user agrees; check "
    "the target with get_target_capabilities if anything is unclear." + CLOSING)
DIAGNOSE_INSTRUCTIONS = (
    CLIENT_ROLE + HEALING_TASK + " Give the user what went wrong, the cause, one fix as a manual step for them and "
    "how sure you are. Never run the fix and never present it as something the framework runs. More of the log comes "
    "from read_build_log, the state from get_build_status. If the build waits for a self-healing decision "
    "(pending_healing in get_build_status), show that proposal to the user and call resolve_healing only with their "
    "explicit decision." + CLOSING)
ADD_BOARD_INSTRUCTIONS = (
    CLIENT_ROLE + MODULE_TASK + " Show the user the whole definition with base_image_reason and assumptions, then "
    "create it with create_target_module; the person confirms every module." + CLOSING)
DEPLOY_INSTRUCTIONS = (
    CLIENT_ROLE + "Deploy a built model to a device. A device must be trusted and paired by the person in the "
    "framework's GUI or CLI first; you can never trust a device or its host key. Create the package with "
    "create_deploy_package, then deploy it with deploy_to_device; the person confirms each deploy." + CLOSING)


def _block(label: str, text: str, source: str, max_len: int = 4000) -> str:
    return f"{label}:\n" + wrap_untrusted(text, source, max_len=max_len)


def _knowledge_on(framework) -> Any:
    """The knowledge base Ditto would use (None when the person switched it off)."""
    config = getattr(framework, "config", None)
    try:
        enabled = bool(config.get("enable_rag_knowledge", False)) if config is not None else False
    except Exception:
        enabled = False
    if not enabled:
        return None
    ensure = getattr(framework, "ensure_rag_manager", None)
    try:
        return ensure() if callable(ensure) else getattr(framework, "rag_manager", None)
    except Exception:
        return None


def _yes(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("yes", "true", "1", "y", "ja")


def register_framework_prompts(registry: ToolRegistry, framework,
                               probe_text=None) -> ToolRegistry:
    """`probe_text(probe_id)`: the scrubbed text of an imported probe (the probe store, design T5); None until then."""
    service = getattr(framework, "build_service", None)

    def capabilities(target: str):
        try:
            tid = service.resolve_target(target)
            return tid, service.capabilities(tid)
        except Exception as e:  # an unknown target: the prompt says so with the targets there are
            raise ResourceUnknown(str(e), next="Call list_targets for the targets you can use.") from None

    def caps_text(caps) -> str:
        return json.dumps(caps.model_dump(mode="json"), ensure_ascii=False)

    def ask(args: Dict[str, str], ctx: ToolContext) -> str:
        docs = ", ".join(f"lcf://docs/{name}" for name in existing_docs(framework)) or "none in this installation"
        parts = [_block("The user's question", args["question"], "question", 2000),
                 f"Documentation resources of this installation: {docs}"]
        known = knowledge_excerpts(_knowledge_on(framework), args["question"])
        if known:
            parts.append("Knowledge-base excerpts:\n" + known)
        return "\n\n".join(parts)

    def convert(args: Dict[str, str], ctx: ToolContext) -> str:
        tid, caps = capabilities(args["target"])
        parts = [_block("Model", args["model"], "model name", 500),
                 _block("Target and board", f"{tid}" + (f", board {args['board']}" if args.get("board") else ""),
                        "target", 500),
                 _block("The target's capabilities", caps_text(caps), "target capabilities", 12000)]
        return "\n\n".join(parts)

    def recommend(args: Dict[str, str], ctx: ToolContext) -> str:
        tid, caps = capabilities(args["target"])
        board = args.get("board") or None
        options = {fmt: cap.quantizations_for(board) for fmt, cap in caps.formats.items()
                   if not board or board in caps.boards_for(fmt)}
        notes = {fmt: f"{cap.label}; input: {', '.join(cap.input)}" + (f"; {cap.notes}" if cap.notes else "")
                 for fmt, cap in caps.formats.items()}
        known = ""
        if _yes(args.get("use_knowledge")):
            known = knowledge_excerpts(_knowledge_on(framework),
                                       f"{tid} {board or ''} {args['model']} quantization performance", rerank=True)
        listing = format_listing(options, notes) or "(none for this board)"
        return "\n\n".join(optimizer_parts(args["model"], tid, board, listing, known))

    def diagnose(args: Dict[str, str], ctx: ToolContext) -> str:
        status = service.get_status(args["request_id"])
        log = "\n".join(service.log_tail(args["request_id"], 2000).lines)
        context = json.dumps({k: v for k, v in status.model_dump(mode="json").items()
                              if k in ("request_id", "status", "stage", "errors", "warnings", "pending_healing")},
                             ensure_ascii=False)
        known = knowledge_excerpts(_knowledge_on(framework), healing_query(log), limit=3, rerank=False)
        return "\n\n".join(healing_parts(log, context, known))

    def add_board(args: Dict[str, str], ctx: ToolContext) -> str:
        probe = ""
        if args.get("probe_id"):
            probe = probe_text(args["probe_id"]) if probe_text is not None else None
            if probe is None:
                raise ResourceUnknown(f"There is no probe '{args['probe_id']}'.",
                                      next="Import the board's probe with 'llm-cli scan --import FILE' or in the GUI, "
                                           "or name the board and its SoC.")
        known = knowledge_excerpts(_knowledge_on(framework), module_query(probe, args.get("board", "")))
        return "\n\n".join(module_parts(probe, args.get("board", ""), known))

    def deploy(args: Dict[str, str], ctx: ToolContext) -> str:
        parts = [_block("Built model", args["artifact_id"], "artifact id", 200)]
        if args.get("device_id"):
            parts.append(_block("Device", args["device_id"], "device id", 200))
        return "\n\n".join(parts)

    def targets(_args: Dict[str, str], _ctx: ToolContext) -> List[str]:
        try:
            return list(service.target_choices()[0])
        except Exception:
            return []

    def boards(args: Dict[str, str], _ctx: ToolContext) -> List[str]:
        try:
            return list(capabilities(args.get("target", ""))[1].boards)
        except Exception:
            return []

    def own_builds(_args: Dict[str, str], ctx: ToolContext) -> List[str]:
        try:
            return [b.request_id for b in service.list_requests().builds if b.owner == ctx.actor]
        except Exception:
            return []

    model = PromptArg("model", "Hugging Face repo id (owner/name) or a local model as <label>:<relative path>",
                      required=True, max_chars=512)
    target = PromptArg("target", "Target id, e.g. Rockchip", required=True, max_chars=64)
    board = PromptArg("board", "Board within the target, e.g. rk3588 (optional)", max_chars=64)
    specs = [
        PromptSpec("ask_about_framework", "Ask about the framework",
                   "Answers a question about the framework with its knowledge base, documentation and read tools.",
                   ASK_INSTRUCTIONS, (PromptArg("question", "Your question (free text)", required=True,
                                                max_chars=2000),), ask,
                   tools=("list_targets", "get_target_capabilities", "list_build_requests", "get_build_status",
                          "read_build_log", "list_artifacts", "run_doctor", "search_knowledge", "get_status")),
        PromptSpec("convert_model_for_board", "Convert a model for a board",
                   "Checks the target, starts the build, follows it and shows the result.", CONVERT_INSTRUCTIONS,
                   (model, target, board), convert,
                   tools=("list_targets", "get_target_capabilities", "start_build", "get_build_status",
                          "resolve_healing", "list_artifacts"),
                   completions={"target": targets, "board": boards}),
        PromptSpec("recommend_build_settings", "Recommend build settings",
                   "Recommends format, quantization and optimization for a model on a target (Ditto's optimizer task).",
                   RECOMMEND_INSTRUCTIONS,
                   (model, target, board, PromptArg("use_knowledge", "yes to add knowledge-base excerpts (default no)",
                                                    max_chars=8)), recommend,
                   tools=("get_target_capabilities", "start_build"),
                   completions={"target": targets, "board": boards, "use_knowledge": lambda a, c: ["yes", "no"]}),
        PromptSpec("diagnose_build_failure", "Diagnose a failed build",
                   "Analyzes a failed build with its log and knowledge excerpts and proposes one fix as a manual step "
                   "(Ditto's self-healing task).", DIAGNOSE_INSTRUCTIONS,
                   (PromptArg("request_id", "The build request, e.g. req_1a2b3c4d", required=True, max_chars=64),),
                   diagnose, tools=("get_build_status", "read_build_log", "resolve_healing"),
                   completions={"request_id": own_builds}),
        PromptSpec("add_board", "Add a board",
                   "Proposes a target module for a board from its probe or from its name and SoC (Ditto's module "
                   "task).", ADD_BOARD_INSTRUCTIONS,
                   (PromptArg("probe_id", "An imported probe of the board (optional)", max_chars=64),
                    PromptArg("board", "The board and its SoC, when there is no probe (free text, last)",
                              max_chars=1000)), add_board, tools=("create_target_module",)),
        PromptSpec("deploy_to_board", "Deploy to a board",
                   "Packages a built model and deploys it to a device the person trusted and paired.",
                   DEPLOY_INSTRUCTIONS,
                   (PromptArg("artifact_id", "The built model", required=True, max_chars=64),
                    PromptArg("device_id", "A paired device (optional)", max_chars=64)), deploy,
                   tools=("create_deploy_package", "deploy_to_device")),
    ]
    for spec in specs:
        registry.register_prompt(spec)
    return registry
