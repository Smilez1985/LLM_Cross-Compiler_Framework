#!/usr/bin/env python3
"""
What the prompt filter did to one request, for the person (docs/DESIGN.md, "The person is told, quietly and exactly,
what was filtered out ...").

A FilterReport is filled by the privacy filter while it replaces values (llm/privacy.py) and by the client when the
answer comes back (llm/client.py). It answers three questions without ever holding a value in what it says:

- what was replaced, by place and kind with a count (`lines`, `counts`): "your input: 1 password flag (SECRET_1)";
- what was put back in the answer (`restored_lines`);
- where the person's own text holds a replaced value (`stretches`), so a window can mark exactly those stretches.

Places are the parts of a request: the person's input, the history, the system prompt, tool results, knowledge
excerpts and tool arguments. Only the report's *text* is safe to show, log or pass on; the values it keeps to find
the stretches live in memory on this computer, never in `repr`, `as_data` or `lines`, and are dropped with the
report. A secret gets a short label (SECRET_1, in the order the report met it), never the placeholder that was sent.
Standard library only.
"""

from typing import Dict, List, Optional, Tuple

PLACES = ("input", "history", "system", "tool_results", "knowledge", "tool_arguments")
PLACE_WORDS = {"input": "your input", "history": "history", "system": "system prompt", "tool_results": "tool results",
               "knowledge": "knowledge excerpts", "tool_arguments": "tool arguments"}
# The places whose text the person sees in a window (they get marks); the others are only listed with their count.
SEEN_PLACES = ("input",)
KIND_WORDS = {"IP": ("IP address", "IP addresses"), "MAC": ("MAC address", "MAC addresses"),
              "EMAIL": ("e-mail address", "e-mail addresses"), "USER": ("user name", "user names"),
              "HOST": ("host name", "host names"), "SERIAL": ("serial number", "serial numbers"),
              "UUID": ("UUID", "UUIDs"), "PATH": ("path", "paths")}
HIDDEN_FOR_GOOD = "credential hidden for good"   # a header or query parameter: no placeholder, nothing comes back
SECRET_PREFIX = "SECRET"


def kind_word(kind: str, count: int = 1) -> str:
    """The words for a kind: personal data by KIND_WORDS, a secret by the name of its rule ("password flag")."""
    one, many = KIND_WORDS.get(kind, (kind, kind if kind.endswith("s") else kind + "s"))
    return one if count == 1 else many


class FilterReport:
    """The filter's work on one request. Not thread-safe: one request, one report."""

    def __init__(self) -> None:
        self._counts: Dict[Tuple[str, str], int] = {}
        self._labels: Dict[str, str] = {}        # token -> SECRET_n
        self._label_of: Dict[Tuple[str, str], List[str]] = {}   # (place, kind) -> the labels met there, in order
        self._values: Dict[str, str] = {}        # label or token -> the value (in memory only, never shown)
        self._kinds: Dict[str, str] = {}         # token -> kind
        self._restored: Dict[str, int] = {}      # kind -> how often a placeholder of it was put back
        self._restored_labels: List[str] = []

    def __repr__(self) -> str:   # never a value
        return f"FilterReport({self.total} replaced, {sum(self._restored.values())} put back)"

    # --- filling ----------------------------------------------------------------------------------------------
    def add(self, place: str, kind: str, token: Optional[str] = None, value: Optional[str] = None) -> None:
        """One replaced value. `token`: its placeholder (None for a value hidden for good); `value` stays in memory."""
        if place not in PLACES:
            raise ValueError(f"unknown place {place!r}")
        key = (place, kind)
        self._counts[key] = self._counts.get(key, 0) + 1
        if token is None:
            return
        self._kinds[token] = kind
        if token.startswith(f"<{SECRET_PREFIX}_") and token not in self._labels:
            self._labels[token] = f"{SECRET_PREFIX}_{len(self._labels) + 1}"
        if token.startswith(f"<{SECRET_PREFIX}_"):
            labels = self._label_of.setdefault(key, [])
            if self._labels[token] not in labels:
                labels.append(self._labels[token])
        if value:
            self._values[token] = value

    def note_restored(self, text: str, mapping: Dict[str, str]) -> None:
        """Counts the placeholders of `mapping` that stand in the answer `text` and so are put back in it."""
        for token in mapping:
            n = text.count(token) if text else 0
            if n and token in self._kinds:
                kind = self._kinds[token]
                self._restored[kind] = self._restored.get(kind, 0) + n
                label = self._labels.get(token)
                if label and label not in self._restored_labels:
                    self._restored_labels.append(label)

    # --- reading ----------------------------------------------------------------------------------------------
    @property
    def total(self) -> int:
        return sum(self._counts.values())

    @property
    def empty(self) -> bool:
        return not self._counts

    def counts(self) -> List[Tuple[str, str, int, Tuple[str, ...]]]:
        """(place, kind, count, labels) in the order of PLACES, then by kind: the whole list, no total."""
        rows = []
        for place in PLACES:
            for (p, kind), n in sorted(self._counts.items()):
                if p == place:
                    rows.append((p, kind, n, tuple(self._label_of.get((p, kind), ()))))
        return rows

    def lines(self) -> List[str]:
        """One line per place with its kinds and counts, in English: 'your input: 1 password flag (SECRET_1)'."""
        out = []
        for place in PLACES:
            parts = []
            for p, kind, n, labels in self.counts():
                if p != place:
                    continue
                parts.append(f"{n} {kind_word(kind, n)}" + (f" ({', '.join(labels)})" if labels else ""))
            if parts:
                out.append(f"{PLACE_WORDS[place]}: {'; '.join(parts)}")
        return out

    def restored_lines(self) -> List[str]:
        """What was put back in the answer, in English; empty when nothing was."""
        if not self._restored:
            return []
        parts = [f"{n} {kind_word(kind, n)}" for kind, n in sorted(self._restored.items())]
        labels = f" ({', '.join(self._restored_labels)})" if self._restored_labels else ""
        return [f"put back in this answer: {'; '.join(parts)}{labels}"]

    def summary(self) -> str:
        """The one dim line of the command line, in English: what was replaced before the request left this computer
        and what was put back; '' when nothing was replaced."""
        if self.empty:
            return ""
        text = "Filtered before this left your computer: " + "; ".join(self.lines()) + "."
        return text + "".join(" " + line[0].upper() + line[1:] + "." for line in self.restored_lines())

    def as_data(self) -> Dict[str, object]:
        """The report as plain data (no value): for a log, the audit or a test."""
        return {"replaced": [{"place": p, "kind": k, "count": n, "labels": list(labels)}
                             for p, k, n, labels in self.counts()],
                "restored": dict(sorted(self._restored.items()))}

    def stretches(self, text: str) -> List[Tuple[int, int, str]]:
        """(start, end, kind) of each stretch of `text` that holds a replaced value of this request, in order and
        without overlaps: the marks for the person's own text (the input field, the sent message, the answer with
        its values put back). Found by the values kept in memory; nothing about a value leaves this method."""
        found: List[Tuple[int, int, str]] = []
        for token, value in self._values.items():
            if not value:
                continue
            at = text.find(value)
            while at != -1:
                found.append((at, at + len(value), self._kinds.get(token, "")))
                at = text.find(value, at + len(value))
        found.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        out: List[Tuple[int, int, str]] = []
        for item in found:
            if out and item[0] < out[-1][1]:
                continue
            out.append(item)
        return out


__all__ = ["FilterReport", "HIDDEN_FOR_GOOD", "KIND_WORDS", "PLACES", "PLACE_WORDS", "SEEN_PLACES", "kind_word"]
