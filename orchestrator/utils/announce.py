#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Spoken announcements

Says a short text through the speech engine built into the operating system, in the UI language. Standard
library only: no audio files, no Qt multimedia, no new dependency. Best effort: it runs in a background thread
with a timeout and never blocks or fails the caller. It never changes the mixer or the system volume.

Engines, the first that works:
* Windows: Windows PowerShell 5.1 with System.Speech (built in; PowerShell 7 lacks it), started from the system
  folder without a shell. The script is a constant; the text and the language travel in the environment
  variables LCF_TTS_TEXT and LCF_TTS_LANG, so no text ever becomes code.
* macOS: say, text on stdin, with a voice of the UI language when one is installed.
* Linux: spd-say (speech-dispatcher), else espeak-ng or espeak (text on stdin).

Silent when LLM_FRAMEWORK_NO_AUDIO is set, under CI, in an SSH session without a display, on Linux without a
display, under pytest, when the setting audio_announcements is off, or when no engine works. Every announcement
is logged at INFO as well, so it is never only audible.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

SETTING = "audio_announcements"
TIMEOUT = 30.0
MAX_CHARS = 300
_OFF_VALUES = ("", "0", "false", "no", "off")
# Novelty voices of macOS that should not read a status message.
_MAC_NOVELTY = frozenset({"Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Good News",
                          "Jester", "Organ", "Superstar", "Trinoids", "Whisper", "Wobble", "Zarvox"})
_WINDOWS_SCRIPT = "; ".join([
    "$ErrorActionPreference = 'Stop'",
    "Add-Type -AssemblyName System.Speech",
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
    "$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and "
    "$_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq $env:LCF_TTS_LANG } | Select-Object -First 1",
    "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }",
    "$s.Speak($env:LCF_TTS_TEXT)",
])

logger = logging.getLogger(__name__)
_lock = threading.Lock()   # one announcement at a time


def _platform() -> str:
    return sys.platform


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in _OFF_VALUES


def silent_reason(config: Any = None) -> Optional[str]:
    """Why announcements stay silent right now; None when they may be spoken."""
    env = os.environ
    display = bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))
    try:
        if config is not None and not config.get(SETTING, True):
            return f"the setting {SETTING} is off"
    except Exception:
        return "the setting cannot be read"
    if _env_on("LLM_FRAMEWORK_NO_AUDIO"):
        return "LLM_FRAMEWORK_NO_AUDIO is set"
    if _env_on("CI"):
        return "running under CI"
    if "PYTEST_CURRENT_TEST" in env:
        return "running under tests"
    if any(env.get(v) for v in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")) and not display:
        return "SSH session without a display"
    if _platform().startswith("linux") and not display:
        return "no display (headless)"
    return None


def _language(lang: Optional[str], config: Any) -> str:
    """Two- or three-letter language code (UI language by default); anything else becomes 'en'."""
    if not lang and config is not None:
        try:
            lang = config.get("language", "en")
        except Exception:
            lang = None
    code = str(lang or "en").strip().lower().replace("_", "-").split("-")[0]
    return code if re.fullmatch(r"[a-z]{2,3}", code) else "en"


def _clean(text: Any) -> str:
    """One line of printable text, capped; the engines get nothing else."""
    printable = "".join(ch if ch.isprintable() else " " for ch in str(text))
    return " ".join(printable.split())[:MAX_CHARS]


def _windows_powershell() -> Optional[str]:
    """Windows PowerShell 5.1 from the system folder, never from the working directory or PATH."""
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    path = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return path if os.path.isfile(path) else None


@lru_cache(maxsize=8)
def _mac_voice(say: str, lang: str) -> Optional[str]:
    """An installed voice for the language (from `say -v ?`); None keeps the system voice."""
    try:
        listing = subprocess.run([say, "-v", "?"], capture_output=True, text=True, timeout=5,
                                 check=False).stdout
    except Exception:
        return None
    for line in listing.splitlines():
        match = re.match(r"(.+?)\s+([A-Za-z]{2,3})[_-]\w+\s+#", line)
        if match and match.group(2).lower() == lang and match.group(1).strip() not in _MAC_NOVELTY:
            return match.group(1).strip()
    return None


def speech_commands(text: str, lang: str) -> List[Tuple[List[str], Dict[str, Any]]]:
    """(argv, extra subprocess.run arguments) of the installed speech engines, in the order they are tried."""
    platform = _platform()
    if platform == "win32":
        exe = _windows_powershell()
        if not exe:
            return []
        extra = {"env": dict(os.environ, LCF_TTS_TEXT=text, LCF_TTS_LANG=lang),
                 "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
        return [([exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT], extra)]
    data = {"input": text.encode("utf-8")}
    if platform == "darwin":
        say = shutil.which("say")
        if not say:
            return []
        voice = _mac_voice(say, lang)
        return [([say] + (["-v", voice] if voice else []), data)]
    commands = []
    spd = shutil.which("spd-say")
    if spd:
        commands.append(([spd, "-w", "-l", lang, "--", text], {}))
    for name in ("espeak-ng", "espeak"):
        exe = shutil.which(name)
        if exe:
            commands.append(([exe, "-v", lang, "--stdin"], data))
    return commands


def announce(text: str, *, lang: Optional[str] = None, config: Any = None) -> Optional[threading.Thread]:
    """Says text in the background (best effort) and returns the thread, or None when it stays silent.
    config supplies the setting audio_announcements and the UI language (unless lang is given)."""
    text = _clean(text)
    if not text:
        return None
    logger.info(f"Announcement: {text}")
    reason = silent_reason(config)
    if reason:
        logger.debug(f"Announcement not spoken: {reason}")
        return None
    try:
        thread = threading.Thread(target=_speak, args=(text, _language(lang, config)), name="announce", daemon=True)
        thread.start()
    except Exception as e:   # e.g. no thread can be started: the caller goes on
        logger.debug(f"Announcement not spoken: {e}")
        return None
    return thread


def _speak(text: str, lang: str) -> None:
    """Runs the engines until one succeeds. Never raises: an announcement must not break anything."""
    if not _lock.acquire(timeout=TIMEOUT):
        logger.debug("Announcement dropped: the previous one is still playing")
        return
    try:
        for argv, extra in speech_commands(text, lang):
            if "input" not in extra:
                extra = dict(extra, stdin=subprocess.DEVNULL)
            # Adapted from rag4ll scripts/rag-usb-watcher (MIT, (c) 2026 Smilez1985, https://github.com/Smilez1985/rag4ll).
            try:
                done = subprocess.run(argv, check=False, timeout=TIMEOUT, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, **extra)
            except Exception as e:
                logger.debug(f"Speech engine {os.path.basename(argv[0])} failed: {e}")
                continue
            if done.returncode == 0:
                return
            logger.debug(f"Speech engine {os.path.basename(argv[0])} exited with {done.returncode}")
        logger.debug("No speech engine spoke the announcement")
    except Exception as e:
        logger.debug(f"Announcement failed: {e}")
    finally:
        _lock.release()
