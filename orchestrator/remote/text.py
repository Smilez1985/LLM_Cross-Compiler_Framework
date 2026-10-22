#!/usr/bin/env python3
"""
A board's words and the state of a running deploy as text.

- one_line(text, limit): what a board printed as one plain line, its last `limit` characters ("no output" when it
  printed nothing); control characters become spaces (moved from runtime/device/remote.py `_tail`).
- keep_tail(lines, line): keeps a board's output line, plain and cut, in a list that holds only the last lines
  (moved from DeploymentManager._keep). The board's words are untrusted: they go into output lines, never into a
  reason.
- event_text(event, place, lang, audience): a running deploy's event (session.RemoteEvent, which the deploy calls
  DeployEvent: uploading, waiting, back, installing) as one line. For the person ("person") in their language with
  the board's address, the words of the GUI's Deployment tab and `llmcf deploy run`; for an AI ("ai") in English
  with the board's label only, never its address or account (D0). The CLI adds " (Ctrl+C)" to a wait and escapes
  for its console itself.
- ai_size(n): a size in an AI's English text ("1.23 GB", "45.6 MB", "size unknown").
"""

from typing import Any, List, Optional

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


def event_text(event: Any, place: str, lang: Optional[str] = None, *, audience: str = "person") -> str:
    """A running deploy's event as one line: for the person (`place` the board's address, `lang` their language, None
    for the interface language) or for an AI (`audience` "ai": English, `place` the board's label)."""
    if audience == "ai":
        return _ai_event_text(event, place)
    if audience != "person":
        raise ValueError(f"audience is 'person' or 'ai', not {audience!r}")
    from orchestrator.utils.localization import ui_text
    if event.kind == "waiting":
        key = "device.wait.status_install" if event.step == "install" else "device.wait.status"
        return ui_text(key, lang, address=place, elapsed=_clock(event.elapsed), limit=_clock(event.limit))
    if event.kind == "back":
        if event.step == "install":
            return ui_text("device.wait.install", lang)
        return ui_text("device.wait.back", lang, done=_mb(event.done), total=_mb(event.total))
    if event.kind == "uploading":
        return ui_text("device.deploy.uploading", lang, done=_mb(event.done), total=_mb(event.total))
    return ui_text("device.deploy.installing", lang)


def _ai_event_text(event: Any, label: str) -> str:
    if event.kind == "uploading":
        return f"uploading to {label}: {ai_size(event.done)} of {ai_size(event.total)}"
    if event.kind == "waiting":
        what = "the upload continues" if event.step != "install" else "the installation is followed again"
        return (f"waiting for the board {label}: {int(event.elapsed)} s of at most {int(event.limit)} s (the network "
                f"dropped; {what} when it answers)")
    if event.kind == "back" and event.step == "install":
        return f"{label} answers again: the installation on the board is followed again (it is never started twice)"
    if event.kind == "back":
        return f"{label} answers again: the upload continues at {ai_size(event.done)} of {ai_size(event.total)}"
    return f"installing on {label}"


__all__ = ["LINE_CHARS", "OUTPUT_LINES", "ai_size", "event_text", "keep_tail", "one_line"]
