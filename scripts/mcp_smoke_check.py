#!/usr/bin/env python3
"""
The checker of the real-client smoke test (scripts/mcp_client_smoke.sh): standard library only.

  config  writes the folders of the isolated environment and the MCP configuration that starts the framework's stdio
          server inside it (`env -i` with a short list of variables, so nothing of the caller's environment reaches
          the framework: no token, no key, no home folder)
  prompt  prints one of the scripted prompts the client is given
  check   reads the isolated audit database (and the client's JSON output for what the client was shown) and says
          whether the server behaved as it must, never trusting the model's own words

Exit codes of `check`: 0 every check passed, 1 a check failed, 2 the input could not be read.
"""

import argparse
import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SERVER = "lcf"  # the server's name in the MCP configuration: its tools are mcp__lcf__<tool>
UNKNOWN_TOOL = "get_status"
ALIAS_TOOL = "check_build_request"
ALIAS, ALIAS_FIELD = "quant", "quantization"
ALIAS_NOTE = f"argument '{ALIAS}' read as '{ALIAS_FIELD}'"  # the framework's words for the repair
CONFIRM_TOOL = "update_settings"

_BUILD = {"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "format": "gguf", ALIAS: "Q4_K_M",
          "include_download_size": False}
PROMPTS = {
    "status": (f"Call the tool mcp__{SERVER}__get_status once, without arguments. Then answer in one short "
               "sentence. Do not call any other tool."),
    "unknown": ("This is a test of how an MCP server handles an argument name it does not know. Call the tool "
                f"mcp__{SERVER}__{UNKNOWN_TOOL} exactly once with exactly these arguments, unchanged, even though the "
                "schema does not list 'section': " + json.dumps({"section": ["permissions"]}) + ". Do not retry, do "
                "not call any other tool, and then reply with the text the tool returned."),
    "alias": ("This is a test of how an MCP server handles a declared argument alias. Make exactly two calls of the "
              f"tool mcp__{SERVER}__{ALIAS_TOOL}, one after the other, each with exactly these arguments, unchanged "
              f"(keep the argument name '{ALIAS}' as written): first " + json.dumps(_BUILD) + ", then "
              + json.dumps({**_BUILD, "board": "rk3567"}) + ". Do not call any other tool and do not retry. Then "
              "reply with the notes the tool returned."),
    "confirm": (f"Call the tool mcp__{SERVER}__{CONFIRM_TOOL} exactly once with exactly these arguments: "
                + json.dumps({"changes": [{"key": "language", "value": "de"}]}) + ". Do not retry, do not wait for "
                "or poll a job, and do not call any other tool. Then reply with what the tool answered."),
}

# Outcomes of an audit entry that end a call without running it.
REFUSALS = ("invalid_input", "denied", "rate_limited", "not_confirmed", "error", "repeat_refused",
            "confirmation_timeout", "cancelled")


# --- the isolated environment ------------------------------------------------------------------------------------

def layout(root: Path) -> Dict[str, Path]:
    """The folders of the isolated environment below `root` (the default layout of a home folder)."""
    home = Path(root) / "home"
    return {"home": home, "config": home / ".config", "cache": home / ".cache", "data": home / ".local" / "share",
            "state": home / ".local" / "state", "hf": home / ".cache" / "huggingface",
            "framework": home / ".llm-framework", "tmp": Path(root) / "tmp", "work": Path(root) / "work"}


def server_environment(root: Path, repo: Path, path: str) -> Dict[str, str]:
    """Every variable the framework's server gets, and nothing else: the homes and caches inside `root`, secrets in
    memory only, no OS keyring (the null backend: the person's real keyring is never touched), no window system,
    the Hugging Face Hub offline, and the installation on PYTHONPATH."""
    folders = layout(root)
    return {
        "PATH": path, "LANG": "C.UTF-8", "HOME": str(folders["home"]), "USERPROFILE": str(folders["home"]),
        "XDG_CONFIG_HOME": str(folders["config"]), "XDG_CACHE_HOME": str(folders["cache"]),
        "XDG_DATA_HOME": str(folders["data"]), "XDG_STATE_HOME": str(folders["state"]),
        "HF_HOME": str(folders["hf"]), "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
        "LLM_FRAMEWORK_HOME": str(folders["framework"]), "LLM_SECRET_STORAGE": "ephemeral",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring", "QT_QPA_PLATFORM": "offscreen",
        "DOCKER_CONFIG": str(folders["home"] / ".docker"), "GNUPGHOME": str(folders["home"] / ".gnupg"),
        "TMPDIR": str(folders["tmp"]), "PYTHONPATH": str(repo), "PYTHONDONTWRITEBYTECODE": "1",
    }


def mcp_config(root: Path, repo: Path, python: str, path: str, env_command: Optional[str] = None) -> Dict[str, Any]:
    """The MCP configuration for the client: the framework's stdio server (python -P -m orchestrator.cli -q mcp
    serve; the client starts it in its own working directory, the repository) under `env -i`, so it gets exactly
    server_environment() and never a variable of the client's own environment."""
    env_command = env_command or shutil.which("env") or "/usr/bin/env"
    variables = [f"{k}={v}" for k, v in server_environment(root, repo, path).items()]
    return {"mcpServers": {SERVER: {"type": "stdio", "command": env_command,
                                    "args": ["-i", *variables, python, "-P", "-m", "orchestrator.cli", "-q", "mcp",
                                             "serve"]}}}


def write_config(root: Path, repo: Path, python: str, path: str, out: Path) -> Path:
    for folder in layout(root).values():
        folder.mkdir(parents=True, exist_ok=True)
    out = Path(out)
    out.write_text(json.dumps(mcp_config(root, repo, python, path), indent=2), encoding="utf-8")
    out.chmod(0o600)
    return out


# --- what happened: the audit database and the client's output -----------------------------------------------------

@dataclass
class Entry:
    id: int
    actor: str
    tool: str
    outcome: str
    detail: str
    extra: Dict[str, Any]

    @property
    def repairs(self) -> List[Dict[str, Any]]:
        value = self.extra.get("repairs")
        return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []

    def get(self, key: str) -> str:
        value = self.extra.get(key)
        return value if isinstance(value, str) else ""


def read_audit(path: Path) -> List[Entry]:
    """The audit entries, oldest first (read-only; the framework's own format: extra fields as JSON)."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"no audit database at {path}")
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT id, actor, tool, outcome, detail, extra FROM events ORDER BY id").fetchall()
    finally:
        db.close()
    entries = []
    for row_id, actor, tool, outcome, detail, extra in rows:
        try:
            parsed = json.loads(extra) if extra else {}
        except ValueError:
            parsed = {}
        entries.append(Entry(int(row_id), str(actor), str(tool), str(outcome), str(detail or ""),
                             parsed if isinstance(parsed, dict) else {}))
    return entries


@dataclass
class ToolCall:
    name: str             # without the mcp__<server>__ prefix
    arguments: Dict[str, Any]
    text: str = ""        # the tool result as the client gave it to the model
    is_error: bool = False
    answered: bool = False


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def read_transcript(path: Path) -> Tuple[List[ToolCall], Dict[str, Any]]:
    """The tool calls of one `claude -p --output-format json --verbose` run with their results, and the final result
    object (empty when the output is not that list of messages)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    messages = data if isinstance(data, list) else []
    final = data if isinstance(data, dict) else next(
        (m for m in reversed(messages) if isinstance(m, dict) and m.get("type") == "result"), {})
    calls: Dict[str, ToolCall] = {}
    prefix = f"mcp__{SERVER}__"
    for message in messages:
        content = (message.get("message") or {}).get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name", ""))
                calls[str(block.get("id"))] = ToolCall(name[len(prefix):] if name.startswith(prefix) else name,
                                                       block.get("input") if isinstance(block.get("input"), dict)
                                                       else {})
            elif block.get("type") == "tool_result" and str(block.get("tool_use_id")) in calls:
                call = calls[str(block.get("tool_use_id"))]
                call.text, call.is_error, call.answered = _text(block.get("content")), bool(block.get("is_error")), True
    return list(calls.values()), final if isinstance(final, dict) else {}


# --- the checks ----------------------------------------------------------------------------------------------------

def shown(value: Any, limit: int = 160) -> str:
    """Text from the client or the log as one short printable line."""
    text = " ".join(str(value).split())
    text = "".join(ch if ch.isprintable() and ord(ch) < 0x10000 else "?" for ch in text)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _alias_repair(entry: Entry) -> bool:
    return any(r.get("kind") == "alias" and r.get("field") == ALIAS_FIELD and r.get("wrote") == ALIAS
               for r in entry.repairs)


def check_client(entries: List[Entry]) -> Tuple[bool, str]:
    lines = sorted({e.get("client") for e in entries if e.actor.startswith("mcp") and e.get("client")})
    claude = [line for line in lines if "claude" in line.lower()]
    if claude:
        return True, "client: " + "; ".join(shown(line, 120) for line in claude)
    return False, ("no call from a Claude Code client reached the server" +
                   (f" (clients seen: {'; '.join(shown(line, 80) for line in lines)})" if lines else ""))


def check_status(entries: List[Entry]) -> Tuple[bool, str]:
    ok = [e for e in entries if e.tool == "get_status" and e.outcome == "ok" and e.actor.startswith("mcp")]
    if ok:
        return True, f"get_status ran ({len(ok)} call{'s' if len(ok) != 1 else ''})"
    seen = sorted({e.outcome for e in entries if e.tool == "get_status"})
    return False, "get_status did not run" + (f" (outcomes: {', '.join(seen)})" if seen else "")


def check_unknown_argument(entries: List[Entry]) -> Tuple[bool, str]:
    refused = [e for e in entries if e.outcome == "invalid_input" and e.get("reason_code") == "unknown_argument"]
    with_hint = [e for e in refused if "Did you mean" in e.detail]
    if with_hint:
        e = with_hint[-1]
        hint = e.detail[e.detail.index("Did you mean"):].split("?")[0] + "?"
        return True, f"unknown argument refused by {e.tool} with a suggestion: {shown(hint, 80)}"
    if refused:
        return False, f"an unknown argument was refused by {refused[-1].tool}, but without a suggestion"
    return False, ("no call with an unknown argument reached the server (the client may have left the argument out "
                   "or renamed it)")


def check_alias(entries: List[Entry], calls: Iterable[ToolCall]) -> List[Tuple[bool, str]]:
    """The declared alias is recorded in the audit log on a call that ran and on one that was refused, and the client
    was shown the framework's note about it in both results."""
    repaired = [e for e in entries if _alias_repair(e)]
    ran = [e for e in repaired if e.outcome == "ok"]
    refused = [e for e in repaired if e.outcome in REFUSALS]
    results = [
        (bool(ran), f"alias '{ALIAS}' recorded on a call that ran ({ran[-1].tool})" if ran else
         f"no call that ran recorded the alias '{ALIAS}' as a repair"),
        (bool(refused), f"alias '{ALIAS}' recorded on a refused call ({refused[-1].tool}: {refused[-1].outcome}, "
                        f"{refused[-1].get('reason_code') or 'no reason code'})" if refused else
         f"no refused call recorded the alias '{ALIAS}' as a repair"),
    ]
    answered = [c for c in calls if c.answered]
    if not answered:
        results.append((False, "the client output has no tool results (run claude with --output-format json "
                               "--verbose), so what the client was shown cannot be checked"))
        return results
    noted = [c for c in answered if ALIAS_NOTE in c.text]
    good, bad = [c for c in noted if not c.is_error], [c for c in noted if c.is_error]
    results.append((bool(good), f"the client was shown \"{ALIAS_NOTE}\" in a result" if good else
                    f"no successful result the client got named \"{ALIAS_NOTE}\""))
    results.append((bool(bad), f"the client was shown \"{ALIAS_NOTE}\" in a refusal" if bad else
                    f"no refusal the client got named \"{ALIAS_NOTE}\""))
    return results


def check_confirmation(entries: List[Entry]) -> Tuple[bool, str]:
    mine = [e for e in entries if e.tool == CONFIRM_TOOL]
    if not mine:
        return False, f"{CONFIRM_TOOL} was never called"
    ran = [e for e in mine if e.outcome in ("started", "ok")]
    if ran:
        return False, f"{CONFIRM_TOOL} ran without the person's answer (outcome {ran[-1].outcome})"
    asked = [e for e in mine if e.outcome == "confirmation_requested"]
    ended = [e for e in mine if e.outcome in REFUSALS]
    if not asked and not ended:
        return False, f"{CONFIRM_TOOL} neither asked nor ended (outcomes: {', '.join(e.outcome for e in mine)})"
    how = ""
    if asked:
        how = f"asked via {shown(asked[-1].get('confirm_channel') or 'unknown channel', 40)}"
    if ended:
        last = ended[-1]
        how += (", " if how else "") + f"ended {last.outcome}" + (
            f" (answer {shown(last.get('answer'), 30)})" if last.get("answer") else "")
    return True, f"{CONFIRM_TOOL} did not run without an answer: {how}"


def run_checks(entries: List[Entry], calls: List[ToolCall]) -> List[Tuple[bool, str]]:
    return [check_client(entries), check_status(entries), check_unknown_argument(entries),
            *check_alias(entries, calls), check_confirmation(entries)]


def report(results: List[Tuple[bool, str]], finals: Dict[str, Dict[str, Any]], out=None) -> bool:
    out = out or sys.stdout
    for name, final in finals.items():
        denials = final.get("permission_denials") if isinstance(final, dict) else None
        if denials:
            print(f"note  run '{name}': the client denied {len(denials)} tool use(s) itself", file=out)
        if isinstance(final, dict) and final.get("is_error"):
            print(f"note  run '{name}': the client reported an error ({shown(final.get('subtype', ''), 40)})",
                  file=out)
    for ok, message in results:
        print(f"{'PASS' if ok else 'FAIL'}  {message}", file=out)
    passed = all(ok for ok, _ in results)
    print(f"{'all' if passed else 'not all'} {len(results)} checks passed", file=out)
    return passed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config", help="write the isolated folders and the MCP configuration")
    config.add_argument("--root", required=True, type=Path)
    config.add_argument("--repo", required=True, type=Path)
    config.add_argument("--python", required=True)
    config.add_argument("--path", required=True, help="PATH for the server")
    config.add_argument("--out", required=True, type=Path)
    prompt = commands.add_parser("prompt", help="print one scripted prompt")
    prompt.add_argument("name", choices=sorted(PROMPTS))
    check = commands.add_parser("check", help="check the audit database and the client's output")
    check.add_argument("--root", type=Path, help="the isolated environment (its audit database is used)")
    check.add_argument("--audit", type=Path, help="an audit database (instead of --root)")
    check.add_argument("--transcript", action="append", default=[], metavar="NAME=FILE",
                       help="a claude -p --output-format json --verbose output (repeatable)")
    args = parser.parse_args(argv)

    if args.command == "config":
        write_config(args.root, args.repo, args.python, args.path, args.out)
        return 0
    if args.command == "prompt":
        print(PROMPTS[args.name])
        return 0
    audit = args.audit or (layout(args.root)["framework"] / "audit.db" if args.root else None)
    if audit is None:
        parser.error("check needs --root or --audit")
    try:
        entries = read_audit(audit)
    except (OSError, sqlite3.Error) as e:
        print(f"FAIL  the audit database could not be read: {shown(e)}")
        return 2
    calls: List[ToolCall] = []
    finals: Dict[str, Dict[str, Any]] = {}
    for item in args.transcript:
        name, _, path = item.partition("=")
        try:
            found, final = read_transcript(Path(path))
        except (OSError, ValueError) as e:
            print(f"note  run '{name}': its output could not be read ({shown(e, 80)})")
            continue
        calls += found
        finals[name] = final
    return 0 if report(run_checks(entries, calls), finals) else 1


if __name__ == "__main__":
    sys.exit(main())
