#!/usr/bin/env python3
"""
Claude on the user's Pro/Max subscription, through the user's own Claude Code installation (`claude -p`).

Subscription OAuth is meant for Claude Code and Anthropic's own apps; third-party programs may not offer a
Claude.ai login, route requests through plan credentials, or collect, store or intermediate Claude.ai tokens
(https://code.claude.com/docs/en/legal-and-compliance). Signing in to the unmodified Claude Code binary with
one's own subscription is allowed. So this adapter never touches a token: the user signs in inside Claude Code
(`claude auth login`, Anthropic's own flow) and this adapter only starts `claude` in print mode, whose usage
counts against the plan's usage limits (support.claude.com, "Use the Claude Agent SDK with your Claude plan").

Each call is one stateless `claude -p` run:
- built-in tools off (--tools ""), no MCP servers (--strict-mcp-config), no user customizations (--safe-mode),
  no slash commands, no session saved, run in an empty temporary directory (no project CLAUDE.md or .mcp.json);
- the framework's system prompt replaces Claude Code's (--system-prompt-file), the conversation goes in on
  stdin as JSON (cannot be broken out of), and the answer always comes back as structured output
  (--json-schema): {"text", "tool_calls"} so the framework's own tool loop, permissions and audit stay in charge;
- ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are removed from the child environment so the subscription login is
  used, not an API key that happens to be exported.
"""

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Any, Callable, Dict, List, Optional

from orchestrator.llm.errors import (AuthFailed, BadRequest, LLMError, NotConfigured, PermissionDenied,
                                     ProviderTimeout, ProviderUnavailable, RateLimited, Refused, Truncated)
from orchestrator.llm.providers.base import ProviderAdapter, json_dumps
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ModelInfo, ToolCall, Usage
from orchestrator.utils.helpers import redact_secrets

STRIPPED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
Runner = Callable[..., subprocess.CompletedProcess]

TOOL_INSTRUCTIONS = (
    "You can use the tools listed below. To call tools, put one entry per call into `tool_calls` (name and "
    "arguments); the results come back as the next messages of the conversation. When you need no tool, leave "
    "`tool_calls` empty and answer in `text`.\n\nTools:\n{tools}")
TRANSCRIPT_INTRO = ("The conversation so far, oldest first, as JSON. Messages with role \"tool\" are results of "
                    "your earlier tool calls. Reply to the last message.\n")


def _subprocess_run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **kw)  # noqa: S603 - fixed argv, no shell


def answer_schema(request: ChatRequest) -> Dict[str, Any]:
    """The structured answer of one step: text, tool calls (one anyOf entry per tool) and the JSON result."""
    defs: Dict[str, Any] = {}

    def embed(schema: Dict[str, Any]) -> Dict[str, Any]:
        # "#/$defs/..." references point at the document root, so nested definitions move up there.
        schema = dict(schema)
        defs.update(schema.pop("$defs", None) or {})
        return schema

    properties: Dict[str, Any] = {"text": {"type": "string"}}
    required = ["text"]
    if request.tools:
        variants = [{"type": "object",
                     "properties": {"name": {"const": t.name}, "arguments": embed(t.input_schema)},
                     "required": ["name", "arguments"], "additionalProperties": False}
                    for t in request.tools]
        properties["tool_calls"] = {"type": "array", "items": {"anyOf": variants}}
        required.append("tool_calls")
    if request.output_model is not None:
        properties["result"] = {"anyOf": [embed(request.output_model.model_json_schema()), {"type": "null"}]}
        required.append("result")
    schema: Dict[str, Any] = {"type": "object", "properties": properties, "required": required,
                              "additionalProperties": False}
    if defs:
        schema["$defs"] = defs
    return schema


def transcript(messages: List[Message]) -> str:
    entries = []
    for m in messages:
        entry: Dict[str, Any] = {"role": m.role, "content": m.text()}
        if m.role == "assistant" and m.tool_calls:
            entry["tool_calls"] = [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in m.tool_calls]
        if m.role == "tool":
            entry["tool_call_id"] = m.tool_call_id
            entry["is_error"] = m.is_error
        if isinstance(m.content, list) and any(b.get("type") == "image" for b in m.content):
            entry["note"] = "an image was attached here; images are not supported on this connection"
        entries.append(entry)
    return TRANSCRIPT_INTRO + json.dumps(entries, ensure_ascii=False, indent=1)


class ClaudeCodeAdapter(ProviderAdapter):
    family = "claude_code"

    def __init__(self, preset, *, binary: Optional[str] = None, runner: Optional[Runner] = None,
                 keep_api_key_env: bool = False, **kw):
        super().__init__(preset, **kw)
        self.binary = binary
        self.runner = runner or _subprocess_run
        self.keep_api_key_env = keep_api_key_env

    def executable(self) -> str:
        path = self.binary or shutil.which("claude")
        if not path:
            raise NotConfigured("Claude Code is not installed. Install it (https://code.claude.com/docs) and sign in "
                                "with `claude auth login` to use your Claude subscription.", provider=self.provider)
        return path

    def child_env(self) -> Dict[str, str]:
        env = dict(os.environ)
        if not self.keep_api_key_env:
            for name in STRIPPED_ENV:
                env.pop(name, None)
        return env

    def login_command(self) -> List[str]:
        """Claude Code's own sign-in (browser, Anthropic's flow); the GUI runs it in a terminal."""
        return [self.executable(), "auth", "login"]

    def auth_status(self) -> Dict[str, Any]:
        """`claude auth status`: {"logged_in": bool, "auth_method": "claude.ai" | "api_key" | ... | None}."""
        try:
            proc = self.runner([self.executable(), "auth", "status"], capture_output=True, text=True, timeout=30,
                               env=self.child_env(), stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            raise ProviderUnavailable(f"Could not run Claude Code: {e.__class__.__name__}", provider=self.provider)
        try:
            data = json.loads(proc.stdout or "{}")
        except ValueError:
            data = {}
        method = data.get("authMethod") if isinstance(data, dict) else None
        return {"logged_in": proc.returncode == 0 and method not in (None, "none"), "auth_method": method}

    def build_command(self, request: ChatRequest, model: str, effort: Optional[str], system_file: str) -> List[str]:
        # --safe-mode skips the user's CLAUDE.md, hooks, plugins, skills and memory but keeps the sign-in
        # (unlike --bare, which ignores the subscription login).
        cmd = [self.executable(), "-p", "--output-format", "json", "--model", model,
               "--system-prompt-file", system_file, "--tools", "", "--strict-mcp-config", "--safe-mode",
               "--disable-slash-commands", "--no-session-persistence", "--max-turns", "4",
               "--json-schema", json_dumps(answer_schema(request))]
        if effort:
            cmd += ["--effort", effort]
        return cmd

    def system_prompt(self, request: ChatRequest) -> str:
        parts = [request.system] if request.system else []
        if request.tools:
            listing = "\n".join(f"- {t.name}: {t.description}" for t in request.tools)
            parts.append(TOOL_INSTRUCTIONS.format(tools=listing))
        if request.output_model is not None:
            parts.append("Put your final answer as JSON into `result` (null while you still call tools).")
        return "\n\n".join(parts)

    def _complete(self, request: ChatRequest, model: str) -> ChatResult:
        effort = self.preset.map_effort(request.effort or self.preset.efforts.get(request.task))
        with tempfile.TemporaryDirectory(prefix="llm-claude-code-") as workdir:
            system_file = os.path.join(workdir, "system.txt")
            with open(system_file, "w", encoding="utf-8") as f:
                f.write(self.system_prompt(request))
            os.chmod(system_file, 0o600)
            cmd = self.build_command(request, model, effort, system_file)
            try:
                proc = self.runner(cmd, input=transcript(request.messages), capture_output=True, text=True,
                                   timeout=self.timeout, cwd=workdir, env=self.child_env())
            except subprocess.TimeoutExpired:
                raise ProviderTimeout("Claude Code did not answer in time.", provider=self.provider) from None
            except OSError as e:
                raise ProviderUnavailable(f"Could not run Claude Code: {e.__class__.__name__}",
                                          provider=self.provider) from None
        return self.to_result(proc, request, model)

    def to_result(self, proc: subprocess.CompletedProcess, request: ChatRequest, model: str) -> ChatResult:
        data = _last_json(proc.stdout)
        if data is None:
            detail = redact_secrets((proc.stderr or proc.stdout or "").strip())[:300]
            if "unknown option" in detail.lower():
                raise NotConfigured(f"This Claude Code version is too old for the framework. Run `claude update`. "
                                    f"({detail})", provider=self.provider)
            raise ProviderUnavailable(f"Claude Code failed (exit {proc.returncode}). {detail}", provider=self.provider)
        if data.get("is_error") or data.get("subtype", "success") != "success":
            raise self._error(data)
        structured = data.get("structured_output")
        if not isinstance(structured, dict):
            structured = {"text": str(data.get("result") or "")}
        calls = []
        for entry in structured.get("tool_calls") or []:
            if isinstance(entry, dict) and entry.get("name"):
                args = entry.get("arguments")
                calls.append(ToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=str(entry["name"]),
                                      arguments=args if isinstance(args, dict) else {},
                                      parse_error=None if isinstance(args, dict) else "arguments must be an object"))
        text = str(structured.get("text") or "")
        if request.output_model is not None and not calls and structured.get("result") is not None:
            text = json.dumps(structured["result"], ensure_ascii=False)
        stop_reason = data.get("stop_reason")
        stop = "tool_use" if calls else ("max_tokens" if stop_reason == "max_tokens" else "end")
        usage = data.get("usage") or {}
        used_model = next(iter(data.get("modelUsage") or {}), None) or model
        result = ChatResult(
            text=text, tool_calls=calls, stop=stop, model=used_model, provider=self.provider,
            usage=Usage(input_tokens=int(usage.get("input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
                        cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0)),
            message=Message(role="assistant", content=text, tool_calls=calls),
        )
        if stop_reason == "refusal":
            result.stop = "refusal"
            raise Refused("Claude declined this request.", result=result, provider=self.provider)
        return result

    def _error(self, data: Dict[str, Any]) -> LLMError:
        status = data.get("api_error_status")
        detail = redact_secrets(str(data.get("result") or "; ".join(map(str, data.get("errors") or []))))[:300]
        lowered = detail.lower()
        kw = {"provider": self.provider, "status": status}
        if data.get("stop_reason") == "refusal":
            return Refused("Claude declined this request.", **kw)
        if status == 401 or any(s in lowered for s in ("not logged in", "/login", "invalid api key", "auth login")):
            return AuthFailed("Claude Code is not signed in. Run `claude auth login` and sign in with your Claude "
                              f"account. ({detail})", **kw)
        if status == 403:
            return PermissionDenied(f"Claude Code: access denied. ({detail})", **kw)
        if status == 429 or "usage limit" in lowered or "hit your limit" in lowered:
            return RateLimited(f"Your Claude plan's usage limit is reached. ({detail})", **kw)
        if status and int(status) >= 500:
            return ProviderUnavailable(f"Claude is temporarily unavailable ({status}). ({detail})", **kw)
        if status and 400 <= int(status) < 500:
            return BadRequest(f"Claude Code rejected the request ({status}). ({detail})", **kw)
        if data.get("subtype") == "error_max_turns":
            return Truncated("Claude Code stopped at its turn limit before answering.", **kw)
        return LLMError(f"Claude Code could not answer: {detail or data.get('subtype')}", **kw)

    def list_models(self) -> List[ModelInfo]:
        return [ModelInfo(id=m, display_name=m) for m in self.preset.suggested_models]


def _last_json(stdout: Optional[str]) -> Optional[Dict[str, Any]]:
    """The result object (json output is one object; tolerate extra lines before it)."""
    text = (stdout or "").strip()
    if not text:
        return None
    for candidate in (text, *reversed(text.splitlines())):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("type", "result") == "result":
            return value
    return None
