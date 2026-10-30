"""Log text as a language model reads it: the one log cleaner (consolidation chain ctx-slim).

A build log is written for a terminal: colour codes, progress bars that redraw one line with carriage returns, and the
same line hundreds of times. A model pays for every one of those characters and learns nothing from them.
`clean_log` applies five rules, in this order:

1. strip terminal escape sequences (ANSI_RE: CSI such as colours and cursor moves, OSC such as window titles and
   links, the short ESC forms); a lone ESC left over (an unterminated sequence) is removed by itself, so no text is
   hidden behind it and none disappears with it;
2. keep only the text after the last carriage return of a line (what a terminal shows of a redrawn progress line); a
   line break of a Windows log ("\\r\\n") is not a redraw;
3. collapse a run of identical lines to the line plus "(repeated N times)" (a run of empty lines to one empty line);
4. collapse a run of three or more lines that differ only in their numbers (a progress counter, a percentage) to the
   first line, one line with the count, and the last line;
5. never touch a line that reports a problem (KEYWORDS, case-insensitive): error, warning, fatal, failed, Traceback,
   and the other words tools say it with (fail, exception, panic, abort, denied, cannot, unable, undefined, not found,
   no such, segmentation fault, killed, npm's "ERR!", apt's "E:"; a linker names each missing symbol on a line of
   its own and only its last line says "error"): it is never collapsed and ends any run; a redrawn line keeps each
   of its parts that says one of these words (as a line of its own).

`clean_log(clean_log(x)) == clean_log(x)`. What this module does not do is left to the one place that does it:
invisible and format characters and length caps (utils/untrusted.sanitize_untrusted, wrap_untrusted), secrets and host
paths (security/paths.scrub_for_ai), so everywhere a log goes to a model the order is clean_log -> scrub_for_ai -> cut
(llm/task_prompts.healing_parts, the tool read_build_log through BuildService.log_tail). The person's own log views
(the Builder's live log, the log files) stay raw. Standard library only.
"""

import re
from typing import List, Sequence, Tuple

# Escape sequences: OSC (ESC ] ... BEL or ESC \), CSI (ESC [ params intermediates final), the 8-bit CSI and OSC
# introducers, character set choices (ESC ( B) and the other two-character ESC forms (ESC M, ESC 7, ...). No sequence
# reaches over a line break: an OSC left open never takes the lines after it along.
ANSI_RE = re.compile(r"(?:\x1b\]|\x9d)[^\x07\x1b\x9c\n]*(?:\x07|\x1b\\|\x9c)"
                     r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
                     r"|\x1b[()*+][0-9A-Za-z]"
                     r"|\x1b[0-9=>@-Z\\^_`a-z{|}~]")
_LEFTOVER = re.compile(r"[\x1b\x9b\x9d\x9c]")
KEYWORDS = re.compile(r"error|warn|fatal|fail|traceback|exception|panic|abort|denied|cannot|unable|undefined"
                      r"|not found|no such|segmentation fault|killed|\berr!|^\s*e: ", re.IGNORECASE)
_NUMBER = re.compile(r"\d+")
REPEATED = "{line} (repeated {count} times)"
SIMILAR = "({count} lines like these, only their numbers differ: the first and the last are shown)"
MIN_SIMILAR = 3


def strip_escapes(text: str) -> str:
    """Rule 1 alone: the text without terminal escape sequences."""
    return _LEFTOVER.sub("", ANSI_RE.sub("", text))


def keeps(line: str) -> bool:
    """Rule 5: a line that reports a problem (KEYWORDS: error, warning, fatal, failed, Traceback and the other words
    tools say it with) is never collapsed."""
    return bool(KEYWORDS.search(line))


def _redraws(line: str) -> List[str]:
    """Rule 2: what is left of one line after its carriage returns."""
    line = line.rstrip("\r")
    if "\r" not in line:
        return [line]
    parts = line.split("\r")
    kept = [part for part in parts[:-1] if keeps(part)]
    return kept + [parts[-1]]


def _shape(line: str) -> str:
    return _NUMBER.sub("0", line)


def clean_lines(lines: Sequence[str]) -> List[Tuple[int, str]]:
    """`clean_log` over a list of log lines (an entry may itself hold line breaks): each cleaned line with the index of
    the first entry it comes from, so a caller that pages by line numbers keeps them right."""
    flat: List[Tuple[int, str]] = []
    for index, entry in enumerate(lines):
        for raw in strip_escapes(str(entry)).split("\n"):
            flat.extend((index, part) for part in _redraws(raw))
    out: List[Tuple[int, str]] = []
    position = 0
    while position < len(flat):
        index, line = flat[position]
        end = position + 1
        if not keeps(line):
            shape = _shape(line)
            while end < len(flat) and not keeps(flat[end][1]) and _shape(flat[end][1]) == shape:
                end += 1
        run = flat[position:end]
        if len(run) == 1:
            out.append((index, line))
        elif all(text == line for _, text in run):
            out.append((index, REPEATED.format(line=line, count=len(run)) if line.strip() else line))
        elif len(run) >= MIN_SIMILAR:
            out.extend([(index, line), (index, SIMILAR.format(count=len(run))), run[-1]])
        else:
            out.extend(run)
        position = end
    return out


def clean_log(text: str) -> str:
    """A log text with the five rules applied (module docstring)."""
    return "\n".join(line for _, line in clean_lines([text or ""]))
