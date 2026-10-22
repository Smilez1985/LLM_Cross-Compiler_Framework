#!/usr/bin/env python3
"""
Paths in text an AI reads (design 6.4, DECISIONS round 4 item 1: ids and labels toward an AI, readable names for the
person; D0: a board's account never reaches an AI). Qt-free and below every other layer, so the Core, the tools and
the utils use one implementation (consolidation chain c1-paths; moved here from orchestrator/tools/tools_runtime.py,
which keeps the old names).

- home_folders(): this computer's home folder in every form a text can name it.
- replace_folder(text, folder, new) and shorten_home(text): a folder (the home folder as '~') replaced wherever it
  stands, with either separator, a Windows path in any case, and as Python quotes a Windows path (undouble).
- label_paths(text, roots): every path by its label (a known folder) or its file name, also the path inside a file
  URL; a home folder never by its account name, also when the name holds spaces.
- device_text(text, users, roots): text from or about a board, with its account and its home folder hidden (D0).
- scrub_for_ai(text, roots=None): the status scrub of every AI-facing text: the paths labelled (with `roots`) or the
  home folder shortened to '~', secrets redacted, invisible characters removed, capped.
- HOME_ROOT and ACCOUNT_NAME: the home-folder patterns the other scrubbers share (the GUI tool's form values, the pack
  export's anonymisation, the reversible privacy masks); each keeps its own policy.
"""

import os
import re
import string
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

# The folder above a home folder on Linux, macOS and Windows (either separator), never inside a word or a path part.
HOME_ROOT = r"(?:(?<![\w.])/home|(?<![\w.])/Users|\b[A-Za-z]:[\\/]Users)"
# One path part that names an account (the folder in HOME_ROOT): no separator, space, quote, pipe or colon.
ACCOUNT_NAME = r"[^/\\\s\"'<>|:]+"
# What a scrubbed status text may hold at most (tools_status' runtime texts, the deploy and runtime tool errors).
SCRUB_LIMIT = 500

# Where a path in an error text begins: absolute (POSIX, a Windows drive or a Windows share "\\\\server\\...") or starting
# with ~/; never inside a word or a URL's "//host/..." (a single slash after a colon is a path: "file:/home/...",
# "user@board:/home/...").
_PATH_START = re.compile(r"(?<![\w/.~\\-])(?:~/|/(?!/)|[A-Za-z]:[\\/]|\\\\(?=[^\\\s]))")
# Characters that end a path's run of characters; a single space may still continue it (_path_end).
_PATH_STOP = frozenset("'\"`,;()[]<>")
_PATH_TRAILING = ".:!?"
# Characters after a known folder's name that end it ("/home/Jane Doe|grep", "HOME=/home/Jane Doe=x"): never part of a
# folder name in practice; '-', '_', '+', '@' and '~' are, so "/home/Jane Doe-old" names another folder.
_KNOWN_END = frozenset("/\\") | _PATH_STOP | frozenset(_PATH_TRAILING) | frozenset("|=&*^%$#{}")
_PATH_SEP = re.compile(r"[/\\]")
# A quoted path ("No such file or directory: '/home/u/My Models/x.gguf'"): the quotes say where it ends.
_QUOTED_PATH = re.compile("|".join(f"{q}((?:~/|/|[A-Za-z]:[\\\\/]|\\\\\\\\)[^{q}\\n]*){q}" for q in ("'", '"', "`")))
# Two plain lower-case words around one slash ("and/or", "os/arch") are prose, not the rest of a path.
_WORD_PAIR = re.compile(r"[a-z]{1,16}/[a-z]{1,16}")
_PATH_MAX_SPACES = 8
# A known folder and a home folder are found in text whatever the computer runs: a Windows host's paths come with '\'
# or '/' ("C:/Users/Jane Doe" from git or CMake), a board's POSIX paths with '/'. So paths are compared with '/' for
# '\', and a Windows path (every path on a Windows host) also without regard to ASCII case ("c:\users\..."). Both
# keep the length, so positions found in the compared form are positions in the text.
_FOLD_SEPARATORS = str.maketrans("\\", "/")
_FOLD_CASE = str.maketrans("\\" + string.ascii_uppercase, "/" + string.ascii_lowercase)
_WINDOWS_STYLE = re.compile(r"^(?:[A-Za-z]:|[\\/]{2})")
_ABSOLUTE_ANYWHERE = re.compile(r"^(?:[/\\]|[A-Za-z]:[/\\])")
_ONLY_A_ROOT = re.compile(r"(?:[A-Za-z]:)?[/\\]*")
# A Windows path as Python's error texts quote it (repr): every '\' doubled ("[WinError 5] Access is denied:
# 'C:\\Users\\Jane Doe'"). A real Windows path never holds '\\' after its start, so within such a path the pairs are
# single separators again before anything is compared.
_DOUBLED_WINDOWS_PATH = re.compile(r"(?<![\w\\])(?:[A-Za-z]:\\\\(?!\\)|\\{4}(?=[^\\\s]))[^'\"`\n]*")
# A bare home folder of any account, of either system: its last part is the account name ('/home/bob' on a board or
# another person's here, 'C:\Users\Bob', '/c/Users/bob' from MSYS, '/mnt/c/Users/bob' from WSL, a share's
# '\\server\Users\bob' or '\\server\home$\bob'), so it is never shown by that part.
_OTHER_HOME = re.compile(r"(?:/usr|/var|/export)?/home/[^/\\]+"
                         r"|(?i:(?:[a-z]:|/[a-z]|/mnt/[a-z]|[\\/]{2}[^\\/]+)?[\\/]"
                         r"(?:users|home\$?|documents and settings)[\\/][^\\/]+)")
# The POSIX form of another account's home ('/home/bob'): an account name there has no space (useradd refuses one).
_POSIX_HOME = re.compile(r"(?:/usr|/var|/export)?/home/")
# A word after another account's Windows home folder that still belongs to its name: a Windows profile folder keeps
# the spaces of the person's name ('C:\Users\Jane Doe'), whose words start with a capital letter or a digit.
_NAME_WORD = re.compile(r" ([A-Z0-9][^\s/\\'\"`,;:()\[\]<>|=]*)")
# A file URL and where its path begins: 'file:///home/u/x', 'file:///C:/Users/u/x', 'file://localhost/home/u/x' (after
# the host) and 'file://server/share/x' (the share, like '\\server\share\x').
_FILE_URL = re.compile(r"(?i)(?<![\w.+/-])file:(?://localhost(?=[/\\])|//(?=[/\\])|(?=//[^/\\\s'\"`]))")
_URL_DRIVE = re.compile(r"[/\\](?=[A-Za-z]:[/\\])")


def _undouble(text: str) -> str:
    return _DOUBLED_WINDOWS_PATH.sub(lambda m: m.group(0).replace("\\\\", "\\"), text)


def _fold(text: str, base: str) -> str:
    """`text` as it is compared with the folder `base`: separators as '/', for a Windows path also lower case."""
    return text.translate(_FOLD_CASE if os.name == "nt" or _WINDOWS_STYLE.match(base) else _FOLD_SEPARATORS)


def _find(text: str, base: str, start: int, stop: Optional[int] = None) -> int:
    """Where the folder `base` occurs in `text` from `start` (before `stop`), compared as _fold does; -1 if nowhere."""
    return _fold(text, base).find(_fold(base, base), start, len(text) if stop is None else stop)


def home_folders() -> List[str]:
    """This computer's home folder in every form text can name it: Path.home(), HOME and USERPROFILE (a Windows host
    may have a POSIX HOME from Git Bash or MSYS, and Path.home() ignores HOME there), each also as its real path, the
    longest first. A root folder or a relative value is no home folder."""
    found = set()
    for home in (str(Path.home()), os.environ.get("HOME", ""), os.environ.get("USERPROFILE", "")):
        if not home or not _ABSOLUTE_ANYWHERE.match(home) or _ONLY_A_ROOT.fullmatch(home):
            continue
        found.add(home.rstrip("/\\"))
        if os.path.isabs(home):
            real = os.path.realpath(home)
            if real and not _ONLY_A_ROOT.fullmatch(real):
                found.add(real.rstrip("/\\"))
    return sorted(found, key=lambda h: (-len(h), h))


def replace_folder(text: str, folder: str, new: str) -> str:
    """Every occurrence of the folder `folder` in `text` replaced by `new`, found as _fold compares (with either
    separator, a Windows path in any case)."""
    if not folder:
        return text
    target, folded = _fold(folder, folder), _fold(text, folder)
    out, pos = [], 0
    k = folded.find(target)
    while k >= 0:
        out += [text[pos:k], new]
        pos = k + len(folder)
        k = folded.find(target, pos)
    return "".join(out) + text[pos:]


def shorten_home(text: str) -> str:
    """`text` with this computer's home folder shortened to '~': every name home_folders knows, with either separator,
    a Windows path in any case, and as Python quotes a Windows path (doubled backslashes)."""
    text = _undouble(text or "")
    for home in home_folders():
        text = replace_folder(text, home, "~")
    return text


def _run_end(text: str, i: int) -> int:
    while i < len(text) and not text[i].isspace() and text[i] not in _PATH_STOP:
        i += 1
    return i


def _path_exists(path: str) -> bool:
    try:
        return os.path.lexists(os.path.expanduser(path))
    except (OSError, ValueError):
        return False


def _continues(word: str) -> bool:
    """Whether the word after a space is clearly the rest of a path (a folder name with a space): it holds a separator
    and is no address, URL, new path or pair of plain words."""
    core = word.rstrip(_PATH_TRAILING + ",")
    if not _PATH_SEP.search(core) or ":" in core or core[:1] in ("/", "\\", "~") or _WORD_PAIR.fullmatch(core):
        return False
    return True


def _known_at(text: str, at: int, bases: List[str]) -> Optional[str]:
    """The known folder (`bases`, longest first) whose whole name stands at `at` in `text`, if any."""
    n = len(text)
    for base in bases:
        k = at + len(base)
        if k <= n and _fold(text[at:k], base) == _fold(base, base):
            if k == n or text[k].isspace() or text[k] in _KNOWN_END:
                return base
    return None


def _account_end(text: str, start: int, end: int, homes: Sequence[str]) -> int:
    """Where another account's bare home folder (text[start:end], e.g. 'C:\\Users\\Jane') ends when its account name
    holds spaces: an account name of this computer (the last part of a home folder, 'Jane Doe') is taken whole in any
    root, and after a Windows home folder each following word that starts with a capital letter or a digit ('Bob
    Smith 2'), so no part of the name is left in the text (D0). `end` unchanged otherwise."""
    run = text[start:end]
    if not _OTHER_HOME.fullmatch(run):
        return end
    part = _PATH_SEP.split(run)[-1]
    at = end - len(part)
    for name in sorted({_PATH_SEP.split(h)[-1] for h in homes}, key=len, reverse=True):
        if " " in name and len(name) > len(part) and _fold(text[at:at + len(name)], name) == _fold(name, name):
            k = at + len(name)
            if k == len(text) or text[k].isspace() or text[k] in _KNOWN_END:
                return k
    if _POSIX_HOME.match(run):
        return end
    for _ in range(_PATH_MAX_SPACES):
        m = _NAME_WORD.match(text, end)
        if m is None:
            break
        end = m.end()
    return end


def _path_end(text: str, start: int, bases: List[str], homes: Sequence[str] = ()) -> int:
    """Where the path that begins at `start` ends. A known folder (`bases`, longest first; it may hold spaces, e.g. a
    home folder 'C:\\Users\\Jane Doe') is taken whole. A single space continues the path when the next word is clearly
    the rest of a path ('/home/u/My Models/x.gguf') or, inside a known folder, the longer path exists on this computer
    (only there: text from outside cannot probe other folders). A known folder further on, where a new path can begin
    ('/usr/bin:/home/Jane Doe', 'a|/home/Jane Doe'), and the home folder (`homes`) anywhere further on
    ('/mnt/home/Jane Doe') start a path of their own and are never taken into this one, so the user name is never
    kept as the "file name" of a longer path. Another account's home folder takes the rest of an account name with
    spaces (_account_end)."""
    n = len(text)
    base = _known_at(text, start, bases)
    end = start + len(base) if base is not None else None
    if end is None:
        end = _run_end(text, start)
        named = _account_end(text, start, end, homes)
        if named != end:
            end = _run_end(text, named) if named < n and text[named] in "/\\" else named
    elif end < n and text[end] in "/\\":
        end = _run_end(text, end)
    for _ in range(_PATH_MAX_SPACES):
        if end >= n - 1 or text[end] != " ":
            break
        nxt = _run_end(text, end + 1)
        if nxt == end + 1:
            break
        word = text[end + 1:nxt]
        longer = text[start:end] + " " + _PATH_SEP.split(word, maxsplit=1)[0]
        known = longer.startswith("~/") or any(_fold(longer, b).startswith(_fold(b.rstrip("/\\"), b) + "/")
                                               for b in bases)
        if not (_continues(word) or (known and _path_exists(longer))):
            break
        end = nxt
    after = start + (len(base) if base is not None else 1)  # never inside the known folder taken whole at the start
    for m in _PATH_START.finditer(text, after, end):
        if _known_at(text, m.start(), bases) is not None:
            end = m.start()
            break
    for home in homes:
        stop = end - 1 + len(home)  # only an occurrence that begins inside this path
        k = _find(text, home, after, stop)
        while k >= 0:
            if _known_at(text, k, [home]) is not None:
                end = k
                break
            k = _find(text, home, k + 1, stop)
    return end


def label_paths(text: str, roots: Dict[str, str]) -> str:
    """Replaces every path in `text`: one inside a known folder becomes `<label>:<relative path>` (roots maps the
    folder to its label), any other one, the home folder's ('~/...') included, its file name only. Paths with spaces
    are replaced whole (a quoted path by its quotes, a known folder and the home folder by their real names, a space
    otherwise when the longer path exists or the next word is clearly part of it), so neither a user name nor a piece
    of a host path is left. Paths of both systems are recognised on any computer, with either separator (and a
    Windows path in any case), and the home folder by every name it has here (home_folders). Ids and labels instead of
    paths toward an AI, readable names for the person (design 6.4, DECISIONS round 4 item 1)."""
    pairs = set()
    for folder, label in roots.items():
        if folder:
            for form in (str(Path(folder)), os.path.realpath(folder)):
                if form and form not in ("/", "\\"):
                    pairs.add((form, label))
    ordered = sorted(pairs, key=lambda kv: (-len(kv[0]), kv[0]))
    # The home folder as it is set and as its real path (the framework resolves paths, so its errors name a linked
    # home folder by the folder it points to)
    homes = home_folders()
    bases = sorted({root for root, _ in ordered} | set(homes), key=lambda b: (-len(b), b))

    def one(token: str) -> str:
        tail = ""
        while token and token[-1] in _PATH_TRAILING:
            token, tail = token[:-1], token[-1] + tail
        for root, label in ordered:
            folded, top = _fold(token, root), _fold(root.rstrip("/\\"), root)
            if folded == top or folded.startswith(top + "/"):
                rel = token[len(top):].lstrip("/\\").replace("\\", "/")
                return f"{label}:{rel}" + tail if rel else f"the {label} folder" + tail
        if any(_fold(token.rstrip("/\\"), h) == _fold(h, h) for h in homes):
            return "the home folder" + tail
        home_at = _known_at(token, 0, homes)
        if home_at is not None and token[len(home_at):len(home_at) + 1] not in ("/", "\\"):
            # the home folder and then text that is no part of a path ("'/home/Jane Doe)'", "/home/Jane Doe's")
            return "the home folder" + token[len(home_at):] + tail
        if _OTHER_HOME.fullmatch(token.rstrip("/\\")):
            return "a home folder" + tail  # its last part would be the account's name
        name = _PATH_SEP.split(token.rstrip("/\\"))[-1]
        return (name or "a folder") + tail

    def replace(text: str, find: Callable[[str, int], Optional[int]], known: List[str]) -> str:
        out, pos = [], 0
        while True:
            start = find(text, pos)
            if start is None:
                break
            end = max(_path_end(text, start, known, homes), start + 1)
            out += [text[pos:start], one(text[start:end])]
            pos = end
        return "".join(out) + text[pos:]

    def next_start(text: str, pos: int) -> Optional[int]:
        m = _PATH_START.search(text, pos)
        return m.start() if m else None

    def quoted(m: "re.Match[str]") -> str:
        inner = next(g for g in m.groups() if g is not None)
        q = m.group(0)[0]
        if _PATH_START.search(inner, 1) is not None or any(_find(inner, h, 1) >= 0 for h in homes):
            # more than one path between the quotes ("'/home/u/x and /home/u'") or the home folder further in
            # ("'/mnt/home/u'"): path by path, so the home folder's own name is never kept as if it were a file name
            return q + replace(inner, next_start, bases) + q
        return q + one(inner) + q

    def file_urls(text: str) -> str:
        """The path of every file URL labelled like any other path; the URL keeps its 'file://' ('file://a.bin')."""
        out, pos = [], 0
        for m in _FILE_URL.finditer(text):
            if m.start() < pos:
                continue
            start = m.end()
            if _URL_DRIVE.match(text, start):
                start += 1   # file:///C:/Users/...: the path is C:/Users/...
            if start >= len(text) or text[start].isspace():
                continue
            end = max(_path_end(text, start, bases, homes), start + 1)
            out += [text[pos:m.start()], "file://", one(text[start:end])]
            pos = end
        return "".join(out) + text[pos:]

    text = _QUOTED_PATH.sub(quoted, _undouble(text or ""))
    text = file_urls(text)
    text = replace(text, next_start, bases)
    # What the start rule cannot see (a "file:///home/..." URL): any path left in the home folder, by its file name.
    if homes:
        def next_home(text: str, pos: int) -> Optional[int]:
            found = [k for k in (_find(text, h, pos) for h in homes) if k >= 0]
            return min(found) if found else None
        text = replace(text, next_home, homes)
    return text


def undouble(text: str) -> str:
    """`text` with every Windows path as Python quotes it (repr: each '\\' doubled) back to single separators."""
    return _undouble(text)


def scrub_for_ai(text: str, roots: Optional[Dict[str, str]] = None, limit: int = SCRUB_LIMIT) -> str:
    """Framework text as an AI may see it. With `roots` every path is labelled first (label_paths: a known folder by
    its label, any other path by its file name); without, this computer's home folder is shortened to '~'
    (shorten_home, in every spelling a Windows host's texts have). Then secrets are redacted and the text is cleaned
    and capped (sanitize_untrusted). The person's own window shows the real names."""
    from orchestrator.utils.helpers import redact_secrets
    from orchestrator.utils.untrusted import sanitize_untrusted
    if roots is not None:
        text = label_paths(str(text), roots)
    return sanitize_untrusted(redact_secrets(shorten_home(text)), limit)


# --- device accounts never reach an AI (study 6.2, D0) ---------------------------------------------------------------

# What an AI reads instead of the account name the framework logs in with on a device ("dietpi", "orangepi", ...).
DEVICE_ACCOUNT = "device account"
_HOME_ROOTS = ("/home", "/Users", "/usr/home", "/var/home", "/export/home")
_WORD_END = r"(?=$|[\s/'\"`:;,()\[\]{}<>|=@])"


def device_text(text: Any, users: Sequence[str], roots: Optional[Dict[str, str]] = None) -> str:
    """Text from or about a device (its answers, check output, errors, success tails) as an AI may see it: the device
    account's home folder becomes '~' and every path is reduced to its file name (label_paths; this computer's own
    folders by their labels), then the account name itself becomes "device account". Secrets are redacted and host
    paths scrubbed as everywhere. The person's own window shows the real names."""
    # The one account check (orchestrator/remote/address.py, ASCII only), looked up where it runs: the bottom layer
    # imports nothing above it at import time.
    from orchestrator.remote.address import USER_PATTERN
    out = str(text if text is not None else "")
    names = sorted({u for u in users if isinstance(u, str) and USER_PATTERN.fullmatch(u)}, key=len, reverse=True)
    for user in names:
        u = re.escape(user)
        out = re.sub("(?:" + "|".join(re.escape(r) for r in _HOME_ROOTS) + ")/" + u + _WORD_END, "~", out)
        out = re.sub("~" + u + _WORD_END, "~", out)
    out = label_paths(out, roots or {})
    for user in names:
        out = re.sub(r"(?<![\w.-])" + re.escape(user) + r"(?![\w-])", DEVICE_ACCOUNT, out)
    return scrub_for_ai(out)


__all__ = ["ACCOUNT_NAME", "DEVICE_ACCOUNT", "HOME_ROOT", "SCRUB_LIMIT", "device_text", "home_folders", "label_paths",
           "replace_folder", "scrub_for_ai", "shorten_home", "undouble"]
