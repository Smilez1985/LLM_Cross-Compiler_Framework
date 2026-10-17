#!/usr/bin/env python3
"""
What the main window remembers between starts (delegation design 5.1): where the chat panel sits and whether it is
shown, the window geometry, the newest chat entry the person has seen ("New since you last looked") and the panel's
filter.

The file `gui_state.json` lives in the per-user folder (mode 0600), never in QSettings, which writes outside it (the
registry on Windows, ~/.config elsewhere). Reading never fails: a missing, unreadable or damaged file gives the
defaults, and a field with a wrong value gives that field's default. It holds no secret and nothing an AI wrote.
"""

import base64
import binascii
import json
import os
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, Optional

from orchestrator.utils.logging import get_logger

logger = get_logger("GuiState")

FILE_NAME = "gui_state.json"
VERSION = 1
FILTERS = ("all", "needs_you", "decisions", "ditto")
MAX_BLOB = 64 * 1024     # characters of a saved Qt state (base64); a real one is a few hundred
MAX_FILE = 512 * 1024    # bytes; a bigger file is not read


def _blob(value: Any) -> str:
    """A base64 text of a Qt state blob, or "" for anything else."""
    if not isinstance(value, str) or len(value) > MAX_BLOB:
        return ""
    try:
        base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return ""
    return value


@dataclass
class GuiState:
    """The remembered state; `path` is where it is saved (None: nowhere)."""
    dock_state: str = ""              # QMainWindow.saveState(), base64
    geometry: str = ""                # QMainWindow.saveGeometry(), base64
    chat_panel_visible: bool = True
    last_seen_entry: int = 0          # the newest feed row id the person has seen
    filter: str = "all"
    path: Optional[Path] = None

    # --- reading ---------------------------------------------------------------------------------------------------

    @classmethod
    def load(cls, state_dir: Any) -> "GuiState":
        """The state saved in `state_dir`; the defaults when there is none or it cannot be used."""
        state = cls(path=Path(state_dir) / FILE_NAME if state_dir else None)
        if state.path is None:
            return state
        try:
            if state.path.is_symlink() or state.path.stat().st_size > MAX_FILE:
                logger.warning(f"{FILE_NAME} is ignored (a link, or too big); the defaults apply.")
                return state
            raw = json.loads(state.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return state
        except (OSError, ValueError, UnicodeDecodeError) as e:
            logger.warning(f"{FILE_NAME} cannot be read ({type(e).__name__}); the defaults apply.")
            return state
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            return state
        state.apply(raw)
        return state

    def apply(self, raw: Dict[str, Any]) -> None:
        """Takes the fields of `raw` that have a valid value; every other field keeps its value."""
        self.dock_state = _blob(raw.get("dock_state")) or self.dock_state
        self.geometry = _blob(raw.get("geometry")) or self.geometry
        visible = raw.get("chat_panel_visible")
        if isinstance(visible, bool):
            self.chat_panel_visible = visible
        seen = raw.get("last_seen_entry")
        if isinstance(seen, int) and not isinstance(seen, bool) and 0 <= seen < 2 ** 62:
            self.last_seen_entry = seen
        if raw.get("filter") in FILTERS:
            self.filter = raw["filter"]

    # --- Qt blobs --------------------------------------------------------------------------------------------------

    @staticmethod
    def encode(blob: Any) -> str:
        """A QByteArray (or bytes) as the base64 text kept in the file."""
        data = bytes(blob.data()) if hasattr(blob, "data") else bytes(blob or b"")
        text = base64.b64encode(data).decode("ascii")
        return text if len(text) <= MAX_BLOB else ""

    @staticmethod
    def decode(text: str) -> bytes:
        try:
            return base64.b64decode((text or "").encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError):
            return b""

    # --- writing ---------------------------------------------------------------------------------------------------

    def data(self) -> Dict[str, Any]:
        values = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "path"}
        return {"version": VERSION, **values}

    def save(self) -> bool:
        """Writes the file atomically (0600); False when it cannot be written (the next start uses the defaults)."""
        if self.path is None:
            return False
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{FILE_NAME}.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self.data(), handle)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            logger.warning(f"{FILE_NAME} was not saved ({type(e).__name__}: {e.strerror or 'error'}).")
            return False
        return True


__all__ = ["FILE_NAME", "FILTERS", "GuiState"]
