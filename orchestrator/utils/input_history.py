#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Recent inputs per GUI field

The GUI offers recently used models, targets, boards, crawl URLs, devices and calibration datasets in its
fields. The history is per user (per-user state directory, JSON, mode 0600, written atomically), bounded
(MAX_PER_FIELD per field), de-duplicated and newest first.

It never holds secrets: a value that redact_secrets would change (API keys, tokens), a URL with a user name or
password, a URL whose query or fragment has a credential-like parameter, and text with control characters are
refused, also when they come from a file edited by hand. A damaged file starts an empty history (a .bad copy
is kept). Saving errors are logged and never break the GUI.
"""

import logging
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Optional, get_args
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from orchestrator.utils.helpers import redact_secrets

Field = Literal["model", "target", "board", "url", "device", "dataset"]
FIELDS = get_args(Field)
MAX_PER_FIELD = 20
MAX_LENGTH = 1000

# Query and fragment parameters that carry credentials (?token=, ?api_key=, X-Amz-Signature, #access_token=)
_CREDENTIAL_PARAM = re.compile(r"token|key|secret|pass(word|wd)?|pwd|auth|sig(nature)?|credential|session", re.I)

logger = logging.getLogger(__name__)


def refusal(value: str) -> Optional[str]:
    """Why value must not be remembered, or None when it may be."""
    if not value:
        return "empty"
    if len(value) > MAX_LENGTH:
        return "too long"
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        return "control characters"
    if redact_secrets(value) != value:
        return "looks like a secret"
    if "://" in value:
        try:
            parts = urlsplit(value)
            has_userinfo = parts.username is not None or parts.password is not None
        except ValueError:
            return "not a valid URL"
        if has_userinfo:
            return "URL with a user name or password"
        for part in (parts.query, parts.fragment):
            if any(_CREDENTIAL_PARAM.search(name) for name, _ in parse_qsl(part, keep_blank_values=True)):
                return "URL with a credential parameter"
    elif "@" in value and ":" in value.rsplit("@", 1)[0]:
        return "user:password@host"
    return None


class HistoryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    fields: Dict[Field, List[str]] = {}

    @field_validator("fields")
    @classmethod
    def _clean(cls, fields: Dict[str, List[str]]) -> Dict[str, List[str]]:
        # A file edited by hand is held to the same rules as new values.
        return {name: _bounded(values) for name, values in fields.items()}


def _bounded(values: Iterable[str]) -> List[str]:
    kept: List[str] = []
    for value in values:
        value = value.strip() if isinstance(value, str) else ""
        if value not in kept and refusal(value) is None:
            kept.append(value)
    return kept[:MAX_PER_FIELD]


class InputHistory:
    """Recent values per field for one user. Every call reads the file, so several windows stay in step."""

    FILE_NAME = "input_history.json"

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / self.FILE_NAME

    @staticmethod
    def _field(field: str) -> str:
        if field not in FIELDS:
            raise ValueError(f"Unknown input history field: {field!r}")
        return field

    def _load(self) -> HistoryFile:
        try:
            return HistoryFile.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return HistoryFile()
        except OSError as e:
            logger.warning(f"Could not read the input history {self.path}: {e}")
            return HistoryFile()
        except ValueError as e:
            logger.warning(f"The input history {self.path} is damaged ({str(e).splitlines()[0][:200]}); "
                           f"starting a new one (a copy is kept as {self.path.name}.bad).")
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
            except OSError:
                pass
            return HistoryFile()

    def _save(self, data: HistoryFile) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(data.model_dump_json(indent=2))
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except OSError as e:
            logger.warning(f"Could not save the input history: {e}")
            return False

    def get(self, field: str) -> List[str]:
        """Recent values of field, newest first."""
        return list(self._load().fields.get(self._field(field), []))

    def add(self, field: str, *values: Optional[str]) -> bool:
        """Remembers values (the first one ends up newest). Refused or empty values are left out; returns
        False when nothing was remembered."""
        field = self._field(field)
        new = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        accepted = [v for v in new if refusal(v) is None]
        if len(accepted) < len(new):
            logger.info(f"Not remembering {len(new) - len(accepted)} {field} value(s) in the input history: "
                        "they could contain a secret.")
        if not accepted:
            return False
        data = self._load()
        data.fields[field] = _bounded(accepted + data.fields.get(field, []))
        return self._save(data)

    def clear(self, field: Optional[str] = None) -> bool:
        """Forgets one field, or everything (the file is removed)."""
        if field is None:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"Could not clear the input history: {e}")
                return False
            return True
        data = self._load()
        data.fields.pop(self._field(field), None)
        return self._save(data)


def for_config(config) -> Optional[InputHistory]:
    """The history of the config's user, or None when the config has no usable per-user state directory."""
    try:
        state_dir = getattr(config, "user_state_dir", None)
    except OSError as e:
        logger.warning(f"No input history: the per-user state directory is not usable ({e}).")
        return None
    return InputHistory(state_dir) if isinstance(state_dir, (str, Path)) and str(state_dir) else None
