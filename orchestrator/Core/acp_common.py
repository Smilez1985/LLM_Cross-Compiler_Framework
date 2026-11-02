#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the helpers both directions of the Agent Client Protocol share

The framework speaks ACP in two directions: Ditto as an ACP agent that an editor starts (orchestrator/acp_agent.py,
with the editor's MCP servers in Core/acp_editor_servers.py) and the ACP client that runs agents the person picks
(Core/acp_hosts.py, Core/acp_client.py). Both take text from outside (an editor's or agent's self-reported name, a
registry entry, a command's arguments, a form an agent asks) and both must recognise what names a secret. One set of
helpers does that for both, so the two sides cannot drift apart:

- one_line(value, limit): text from outside as one printable line (whitespace runs collapsed, control characters
  removed), secrets in known formats redacted, cut to `limit`;
- label(value, limit): a self-reported name as a short, harmless label (one_line, then only letters, digits and
  ` .,:;()+/@-`);
- names_a_secret(*texts): whether a name, title, description or message asks for or names a secret (a password, a
  token, a key, a one-time code ...): the long words count inside a word too ("accessToken"), the short ones (otp,
  2fa, pin, pass, pwd, auth, session) only as words of their own, so "mapping" or "author" is no secret;
- redact_secret_arguments(args): command-line arguments as they may be shown and kept: the value after a flag whose
  name names a secret, and the value of `--flag=VALUE` or `NAME=VALUE` with such a name, becomes REDACTED. For an
  argument name the short words pass, pwd, auth and session count inside a word too (`--dbpass`, `MYSQLPWD`,
  `--basicauth`), as the editor-server record always did: a value too many redacted only hides it, one too few
  would keep a password in the record.

Qt-free and lazy: standard library and orchestrator.utils.helpers.redact_secrets only.
"""

import re
from typing import Any, Iterable, List

REDACTED = "REDACTED"

# Words that name a secret wherever they stand ("accessToken", "db_password", "x-api-key" -> "x api key").
SECRET_WORDS = ("password", "passwort", "passwd", "passphrase", "secret", "token", "api key", "apikey", "authkey",
                "access key", "accesskey", "credential", "private key", "privatekey", "bearer", "cookie",
                "authorization", "authorisation")
# Short words that name a secret only as a word of their own ("PIN", "--pass", "one-time OTP"; not "mapping").
SHORT_SECRET_WORDS = ("otp", "2fa", "pin", "pass", "pwd", "auth", "session")

_SHORT = re.compile(r"(?<![a-z0-9])(?:" + "|".join(map(re.escape, SHORT_SECRET_WORDS)) + r")(?![a-z0-9])")
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")
_LABEL = re.compile(r"[^\w .,:;()+/@-]")
_FLAG = re.compile(r"^(-{1,2}[A-Za-z][\w.-]*)$")
_ASSIGNED = re.compile(r"^(-{0,2}[A-Za-z_][\w.-]*)=(.*)$", re.S)
# In an argument name these short words count inside a word as well ("--dbpass", "SESSIONKEY", "--basicauth").
_INSIDE_AN_ARGUMENT_NAME = re.compile(r"pass|pwd|auth|session", re.I)


def one_line(value: Any, limit: int = 300) -> str:
    """Text from outside as one printable line, secrets in known formats redacted, cut to `limit` characters."""
    from orchestrator.utils.helpers import redact_secrets
    text = redact_secrets(str(value if value is not None else ""))   # first: a key block spans lines
    text = " ".join(text.split())
    return "".join(ch for ch in text if ch.isprintable())[:limit]


def label(value: Any, limit: int = 64) -> str:
    """A self-reported name (an editor's, an agent's, a request id) as one short, harmless label."""
    return _LABEL.sub("?", one_line(value, limit * 4))[:limit]


def names_a_secret(*texts: Any) -> bool:
    """Whether any of the texts (a field's name, title, description or format, a form's message, a flag or variable
    name) asks for or names a secret."""
    parts = [_CAMEL.sub(" ", str(t or "")) for t in texts]
    joined = " ".join(parts).lower().replace("-", " ").replace("_", " ").replace(".", " ")
    squeezed = joined.replace(" ", "")
    if any(word in joined or word.replace(" ", "") in squeezed for word in SECRET_WORDS):
        return True
    return bool(_SHORT.search(joined))


def _secret_argument_name(name: str) -> bool:
    """Whether a flag or variable name names a secret: names_a_secret, or one of the short words inside the name."""
    return names_a_secret(name) or bool(_INSIDE_AN_ARGUMENT_NAME.search(name))


def redact_secret_arguments(args: Iterable[Any]) -> List[str]:
    """The arguments with every secret value replaced by REDACTED: the one after a flag whose name names a secret
    (`--api-key VALUE`, `--token VALUE`, `--dbpass VALUE`), and the value of `--flag=VALUE` or `NAME=VALUE` with such a
    name. Every other argument stays as it is (the caller cleans it for display)."""
    out: List[str] = []
    secret_next = False
    for raw in args:
        text = str(raw if raw is not None else "")
        if secret_next and not _FLAG.match(text):
            out.append(REDACTED)
            secret_next = False
            continue
        secret_next = False
        assigned = _ASSIGNED.match(text)
        if assigned and _secret_argument_name(assigned.group(1)):
            out.append(f"{assigned.group(1)}={REDACTED}")
            continue
        if _FLAG.match(text) and _secret_argument_name(text):
            secret_next = True
        out.append(text)
    return out


__all__ = ["REDACTED", "SECRET_WORDS", "SHORT_SECRET_WORDS", "label", "names_a_secret", "one_line",
           "redact_secret_arguments"]
