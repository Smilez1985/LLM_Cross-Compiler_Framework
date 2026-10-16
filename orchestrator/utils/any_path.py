#!/usr/bin/env python3
"""Paths of either system, whatever this computer runs.

A Windows host reads a board's POSIX paths and the manifests of builds made on Linux; a Linux host reads what a
Windows machine wrote. pathlib follows the host's rules only: on Windows `Path("/home/u/m.gguf").is_absolute()` is
False (no drive) and `Path("C:\\Users\\u\\m.gguf").name` on Linux is the whole text (no separator). Code that decides
whether a value is a path to hide, or what its file name is, uses these instead.
"""

import re

# POSIX absolute ("/..."), home-relative ("~"), rooted or a share on Windows ("\\..."), or a Windows drive ("C:\" "C:/")
_ABSOLUTE = re.compile(r"^(?:[/\\~]|[A-Za-z]:[/\\])")
_SEPARATORS = re.compile(r"[/\\]")


def is_absolute_anywhere(text: str) -> bool:
    """Whether `text` is a full or home-relative path on POSIX or on Windows (a host path, never a repo id)."""
    return bool(_ABSOLUTE.match(str(text or "")))


def last_part(text: str) -> str:
    """The last part of a path of either system (its file or folder name), split at '/' and '\\'."""
    return _SEPARATORS.split(str(text or "").rstrip("/\\"))[-1]
