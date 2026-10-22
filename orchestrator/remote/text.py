#!/usr/bin/env python3
"""
A board's words and the state of a running deploy as text.

- one_line(text, limit): what a board printed as one plain line, its last `limit` characters ("no output" when it
  printed nothing); control characters become spaces (moved from runtime/device/remote.py `_tail`).
- keep_tail(lines, line): keeps a board's output line, plain and cut, in a list that holds only the last lines
  (moved from DeploymentManager._keep). The board's words are untrusted: they go into output lines, never into a
  reason.
- event_text(event, place, lang, audience): a running deploy's event (session.RemoteEvent, which the deploy calls
  DeployEvent: uploading, waiting, back, installing) as one line; a wait names what goes on when the board answers
  again by the event's step (the upload, "install", "bench" for the benchmark on the board, "command" for any other
  step of the runtime deploy, the probe or the checks). For the person ("person") in their language with
  the board's address, the words of the GUI's Deployment tab and `llmcf deploy run`; for an AI ("ai") in English
  with the board's label only, never its address or account (D0). The CLI adds " (Ctrl+C)" to a wait and escapes
  for its console itself.
- progress_events(progress, label) and wait_lines(say, place, lang): a board session's on_event for a tool job an AI
  follows (its progress, the board by its label) and for the person's output pane or console (the first line of a wait
  and the line when the board answers again).
- ai_size(n): a size in an AI's English text ("1.23 GB", "45.6 MB", "size unknown").
"""

from typing import Any, Callable, List, Optional

OUTPUT_LINES = 100     # lines of a board's output a deploy keeps for its result (the rest is in the log)
LINE_CHARS = 500


def one_line(text: Optional[str], limit: int = 300) -> str:
    """`text` on one plain line, its last `limit` characters; "no output" when there is nothing."""
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in (text or "")).split())
    return clean[-limit:] if clean else "no output"


def keep_tail(lines: List[str], line: Any, *, max_lines: int = OUTPUT_LINES, max_chars: int = LINE_CHARS) -> None:
    """Appends `line` (plain, without its line break, at most `max_chars`) and drops the oldest beyond `max_lines`."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(line).rstrip("\r\n"))[:max_chars]
    lines.append(text)
    if len(lines) > max_lines:
        del lines[0]


def ai_size(n: Optional[int]) -> str:
    """A size as an AI reads it in English: GB with two decimals from 0.1 GB, else MB with one."""
    if n is None:
        return "size unknown"
    return f"{n / 1000 ** 3:.2f} GB" if n >= 10 ** 8 else f"{n / 1000 ** 2:.1f} MB"


def _mb(n: int) -> str:
    return f"{n / 1000 ** 2:.1f} MB"


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# The person's text of a wait and of the board's answer by the step that waits (session.RemoteEvent.step): the
# upload (no step), the deploy's installation, the benchmark on the board and any other step on the board.
_WAITING = {"install": "device.wait.status_install", "bench": "device.wait.status_bench",
            "command": "device.wait.status_step"}
_BACK = {"install": "device.wait.install", "bench": "device.wait.bench", "command": "device.wait.step"}


def event_text(event: Any, place: str, lang: Optional[str] = None, *, audience: str = "person") -> str:
    """A running deploy's event as one line: for the person (`place` the board's address, `lang` their language, None
    for the interface language) or for an AI (`audience` "ai": English, `place` the board's label)."""
    if audience == "ai":
        return _ai_event_text(event, place)
    if audience != "person":
        raise ValueError(f"audience is 'person' or 'ai', not {audience!r}")
    from orchestrator.utils.localization import ui_text
    if event.kind == "waiting":
        key = _WAITING.get(event.step, "device.wait.status")
        return ui_text(key, lang, address=place, elapsed=_clock(event.elapsed), limit=_clock(event.limit))
    if event.kind == "back":
        if event.step in _BACK:
            return ui_text(_BACK[event.step], lang)
        return ui_text("device.wait.back", lang, done=_mb(event.done), total=_mb(event.total))
    if event.kind == "uploading":
        return ui_text("device.deploy.uploading", lang, done=_mb(event.done), total=_mb(event.total))
    return ui_text("device.deploy.installing", lang)


def progress_events(progress: Callable[[Optional[int], Optional[int], str], Any],
                    label: str) -> Callable[[Any], None]:
    """A board session's on_event for a tool job an AI follows: `progress(done, total, text)` gets the event's AI
    text (the board by its label only), an upload once per whole percent with its bytes, every other event once per
    text (the deploy_to_device job's progress, now shared by the board tools)."""
    shown: dict = {}

    def on_event(event: Any) -> None:
        text = event_text(event, label, audience="ai")
        key = text if event.kind != "uploading" else f"up {event.done * 100 // max(event.total, 1)}"
        if shown.get("last") != key:
            shown["last"] = key
            uploading = event.kind == "uploading"
            progress(event.done if uploading else None, event.total if uploading else None, text)
    return on_event


def wait_lines(say: Callable[[str], Any], place: str, lang: Optional[str] = None) -> Callable[[Any], None]:
    """A board session's on_event for the person's output pane or console: `say(line)` gets the first line of each
    wait for the board (in the person's language, `place` the board's address) and the line when it answers again;
    the upload's progress and the wait's later seconds are left out (the CLI's spinner and the Deployment tab show
    those themselves)."""
    seen = {"waiting": False}

    def on_event(event: Any) -> None:
        if event.kind == "waiting":
            if not seen["waiting"]:
                seen["waiting"] = True
                say(event_text(event, place, lang))
        elif event.kind == "back":
            seen["waiting"] = False
            say(event_text(event, place, lang))
    return on_event


def _ai_event_text(event: Any, label: str) -> str:
    if event.kind == "uploading":
        return f"uploading to {label}: {ai_size(event.done)} of {ai_size(event.total)}"
    if event.kind == "waiting":
        what = {"install": "the installation is followed again", "bench": "the benchmark is followed again",
                "command": "the step continues"}.get(event.step, "the upload continues")
        return (f"waiting for the board {label}: {int(event.elapsed)} s of at most {int(event.limit)} s (the network "
                f"dropped; {what} when it answers)")
    if event.kind == "back" and event.step == "install":
        return f"{label} answers again: the installation on the board is followed again (it is never started twice)"
    if event.kind == "back" and event.step == "bench":
        return f"{label} answers again: the benchmark on the board is followed again (it is never started twice)"
    if event.kind == "back" and event.step == "command":
        return f"{label} answers again: the step continues"
    if event.kind == "back":
        return f"{label} answers again: the upload continues at {ai_size(event.done)} of {ai_size(event.total)}"
    return f"installing on {label}"


__all__ = ["LINE_CHARS", "OUTPUT_LINES", "ai_size", "event_text", "keep_tail", "one_line", "progress_events",
           "wait_lines"]
