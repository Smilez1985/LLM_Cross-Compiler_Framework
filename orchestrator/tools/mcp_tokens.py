#!/usr/bin/env python3
"""
Named MCP tokens: one per client that connects over HTTP, or over stdio with LCF_MCP_TOKEN.

A token is a transport credential. It is shown once when it is created or rotated and stored only as its
SHA-256 (the token has 256 random bits, so a fast hash is enough). Each token has a name, which becomes the
audit actor `mcp:<name>`, and its own tool policy: a new token can only read until the person sets its
permissions (`llm-cli tools permissions --token NAME`), copies another caller's decisions at creation
(`--policy-from`), or links it to Ditto's policy (`--link ditto`, by reference, so a change to Ditto's
permissions applies to it at once). Revoking a token removes its policy with it.

Tokens are created, listed, rotated and revoked only by a person, with `llm-cli mcp token` or in the GUI (both through
the functions at the end of this module): no tool, resource or prompt can reach this store, and no token ever appears
in a tool argument, result, log line or audit entry.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from orchestrator.tools.mcp_env import ENV_VAR  # noqa: F401 - re-exported: the token of a stdio client
from orchestrator.tools.policy import Decision, ToolPolicy, ToolTier
from orchestrator.tools.signing import SIGNATURE_FIELD, RevisionAnchor, SignatureError, sign, verify
from orchestrator.utils.file_lock import file_lock
from orchestrator.utils.next_steps import WithSteps

ACTOR_PREFIX = "mcp:"          # the audit actor of a named token is "mcp:<name>"
TOKEN_PREFIX = "lcf_mcp_"      # makes a token recognisable in a configuration file
LINK_TARGETS = ("ditto",)      # callers whose policy a token may use by reference
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
_SHA256_RE = r"^[0-9a-f]{64}$"
MAX_STORE_BYTES = 4 * 1024 * 1024  # far more than any token store; a larger file is refused unread


def read_only_policy() -> ToolPolicy:
    """What a new token may do: read; everything that changes something is denied. `configured` stays False until
    the person sets the token's permissions: until then the registry decides it as a new token (read only, and never
    more than your shared setting for a single tool or the tool's own default)."""
    return ToolPolicy(configured=False, tiers={ToolTier.READ: Decision.ALLOW, ToolTier.LOCAL_WRITE: Decision.DENY,
                                               ToolTier.EXTERNAL: Decision.DENY})


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TokenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_RE.pattern)
    sha256: str = Field(pattern=_SHA256_RE)
    created: str
    rotated: Optional[str] = None
    link: Optional[Literal["ditto"]] = None  # uses that caller's policy by reference
    policy: Optional[ToolPolicy] = None      # its own policy (None when linked)
    # Tells this token apart from an earlier one of the same name (its standing approvals name it); kept on rotation.
    uid: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{16}$")

    @model_validator(mode="after")
    def _one_policy(self):
        if (self.link is None) == (self.policy is None):
            raise ValueError("a token has either its own policy or a link to another caller's policy")
        return self


class TokenFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    # Counts the changes; an older copy put back (to bring back a revoked token) is refused (RevisionAnchor).
    revision: int = Field(default=0, ge=0)
    tokens: List[TokenRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self):
        names = [t.name for t in self.tokens]
        if len(names) != len(set(names)):
            raise ValueError("token names must be unique")
        return self


class TokenStoreError(WithSteps, Exception):
    """The token store cannot be used or the change is not possible; the message is safe to show."""


class TokenStore:
    """The named tokens in the per-user folder (`mcp_tokens.json`, mode 0600), changed under a cross-process lock.
    The parsed tokens are cached by the SHA-256 of the file's bytes, so the HTTP server can check every request
    cheaply and sees a revoke or rotate at once. The file is signed like the tool permissions
    (orchestrator/tools/signing.py): a file edited by hand, damaged, unsigned or signed with another key accepts no
    token at all, and with the keyring an older signed copy put back is refused too, also when its bytes are those
    of a revision this process has cached."""

    FILE_NAME = "mcp_tokens.json"
    LOCK_NAME = ".mcp_tokens.lock"
    KIND = "mcp_tokens"

    def __init__(self, state_dir: Path, key: Optional[bytes] = None,
                 refuse_unkeyed: Optional[Callable[[], bool]] = None, anchor: Optional[RevisionAnchor] = None):
        """key, refuse_unkeyed and anchor: as for PolicyStore (the audit key from the OS keyring, None without one;
        the newest saved revision, kept in the keyring, so an older copy put back is refused)."""
        self.path = Path(state_dir) / self.FILE_NAME
        self.lock_path = Path(state_dir) / self.LOCK_NAME
        self.key = key
        self.refuse_unkeyed = refuse_unkeyed
        self.anchor = anchor
        # (sha256 of the bytes, tokens, the file's revision)
        self._cache: Optional[Tuple[str, Dict[str, TokenRecord], int]] = None
        self._cache_lock = threading.Lock()

    # --- reading ------------------------------------------------------------------------------------

    def _read(self) -> Dict[str, TokenRecord]:
        """All tokens by name. TokenStoreError when the file exists but cannot be used (nothing is accepted then).
        The file is read on every call and the parsed tokens are cached by the SHA-256 of its bytes: a cache by
        file identity, size and modification time would keep a token that was replaced within the time stamp's
        resolution (or by a same-size write that kept the time)."""
        # The newest revision the framework saved (a stat while the file is unchanged), checked on a cache hit too:
        # the bytes of an older revision this process cached can be put back over a newer file. Read before the file
        # is opened, as the framework saves the file before it moves the anchor: a file saved meanwhile is then
        # newer than the anchor read, never older (which would refuse every token and close every session).
        anchored = self.anchor.current(self.path) if self.anchor is not None else 0
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except FileNotFoundError:
            if os.path.lexists(self.path):
                raise TokenStoreError(f"{self.path} is a link to a missing file; no MCP token is accepted until "
                                      "it is back.") from None
            with self._cache_lock:
                self._cache = None
            return {}  # no token is accepted (a removed file brings nothing back)
        except OSError as e:
            raise TokenStoreError(f"{self.path} cannot be read ({e}); no MCP token is accepted.") from None
        try:
            # On every read, before the cache answers (chmod keeps the bytes): a running server must stop accepting
            # tokens as soon as someone else could have written the file. Checked on the file that is read.
            problem = self._problem_of(os.fstat(fd))
            if problem:
                raise TokenStoreError(problem)
            raw = b""
            while len(raw) <= MAX_STORE_BYTES:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                raw += chunk
        except OSError as e:
            raise TokenStoreError(f"{self.path} cannot be read ({e}); no MCP token is accepted.") from None
        finally:
            os.close(fd)
        if len(raw) > MAX_STORE_BYTES:
            raise TokenStoreError(f"{self.path} is larger than any token store ({MAX_STORE_BYTES // 1024} KiB); no "
                                  "MCP token is accepted. Remove the file and create the tokens again.")
        key = hashlib.sha256(raw).hexdigest()
        with self._cache_lock:
            cached = self._cache if self._cache is not None and self._cache[0] == key else None
        if cached is not None:
            if cached[2] < anchored:
                raise self._older_copy(cached[2], anchored)
            return cached[1]
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("not a JSON object")
            data = TokenFile.model_validate({k: v for k, v in parsed.items() if k != SIGNATURE_FIELD})
        except (ValueError, ValidationError, RecursionError) as e:
            raise TokenStoreError(f"{self.path} is damaged ({str(e).splitlines()[0][:200]}); no MCP token is "
                                  "accepted. Remove the file and create the tokens again.") from None
        try:
            verify(self.KIND, parsed, self.key, self.refuse_unkeyed)
        except SignatureError as e:
            raise TokenStoreError(f"{self.path} cannot be trusted: {e}. No MCP token is accepted. Remove the file and "
                                  "create the tokens again ('llm-cli mcp token add NAME').") from None
        if data.revision < anchored:  # genuinely signed, but put back over a newer one (a revoked token returns)
            raise self._older_copy(data.revision, anchored)
        records = {t.name: t for t in data.tokens}
        with self._cache_lock:
            self._cache = (key, records, data.revision)
        return records

    def _older_copy(self, revision: int, anchored: int) -> TokenStoreError:
        """The refusal of a genuinely signed file older than the revision the framework saved last (parsed now or
        cached by its bytes)."""
        return TokenStoreError(f"{self.path} cannot be trusted: it is an older copy (revision {revision}; the "
                               f"framework saved revision {anchored} last). No MCP token is accepted. Remove the file "
                               "and create the tokens again ('llm-cli mcp token add NAME').")

    def _revision(self) -> int:
        """The revision of the file _read() just read (0 when there is none)."""
        with self._cache_lock:
            return self._cache[2] if self._cache is not None else 0

    def list(self) -> List[TokenRecord]:
        return sorted(self._read().values(), key=lambda t: t.name)

    def get(self, name: str) -> Optional[TokenRecord]:
        return self._read().get(name)

    def find(self, token: str) -> Optional[TokenRecord]:
        """The record of a presented token, or None (unknown, revoked, rotated, or the store is unusable).
        Every stored hash is compared in constant time."""
        if not token:
            return None
        try:
            records = self._read()
        except TokenStoreError:
            return None
        digest = token_digest(token)
        found = None
        for record in records.values():
            if hmac.compare_digest(digest, record.sha256):
                found = record
        return found

    def holds(self, name: str, digest: str) -> bool:
        """Whether the token with this hash still belongs to `name` (not revoked or rotated since)."""
        try:
            record = self._read().get(name)
        except TokenStoreError:
            return False
        return record is not None and hmac.compare_digest(digest, record.sha256)

    def private_problem(self) -> Optional[str]:
        """On POSIX: a message when other users could read or change the store (group or other permission bits, or
        another owner). No token is accepted then (every read checks it), and the MCP server does not start."""
        try:
            return self._problem_of(os.stat(self.path))
        except OSError:
            return None

    def _problem_of(self, st: os.stat_result) -> Optional[str]:
        if os.name != "posix":
            return None
        check = (" No MCP token is accepted and the MCP server does not start until it is fixed; check "
                 "'llm-cli mcp token list' afterwards: another user could have added a token or changed its "
                 "permissions.")
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            return (f"{self.path} is open to other users (mode {stat.S_IMODE(st.st_mode):o}). Run "
                    f"'chmod 600 {self.path}'.{check}")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            return f"{self.path} belongs to another user (uid {st.st_uid}).{check}"
        return None

    # --- changing (a person, through the CLI or the GUI) ---------------------------------------------

    def _write(self, records: Dict[str, TokenRecord], revision: int = 0) -> None:
        data = TokenFile(revision=revision, tokens=sorted(records.values(), key=lambda t: t.name))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".mcp_tokens.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(sign(self.KIND, data.model_dump(mode="json", exclude_none=True), self.key),
                                   indent=2, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _change(self, change: Callable[[Dict[str, TokenRecord]], object]):
        with file_lock(self.lock_path):
            records = dict(self._read())
            anchored = self.anchor.current(self.path) if self.anchor is not None else 0
            revision = max(self._revision(), anchored) + 1
            result = change(records)
            self._write(records, revision)
            if self.anchor is not None:  # after the file, as for the permissions
                self.anchor.saved(self.path, revision)
            return result

    @staticmethod
    def check_name(name: str) -> str:
        if not NAME_RE.match(name or ""):
            raise TokenStoreError(f"'{name}' is not a valid token name: 1-32 lower-case letters, digits and "
                                  "hyphens, starting and ending with a letter or digit (for example ditto-laptop).")
        return name

    def add(self, name: str, policy: Optional[ToolPolicy] = None, link: Optional[str] = None) -> str:
        """Creates a token and returns it; this is the only time it can be read. Without `policy` and `link`
        the token can only read."""
        self.check_name(name)
        if link is not None and link not in LINK_TARGETS:
            raise TokenStoreError(f"A token can only be linked to: {', '.join(LINK_TARGETS)}.")
        if link is not None and policy is not None:
            raise TokenStoreError("A token is either linked to another caller's policy or has its own, not both.")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)

        def change(records):
            if name in records:
                raise TokenStoreError(f"A token named '{name}' exists already; rotate or revoke it.")
            records[name] = TokenRecord(name=name, sha256=token_digest(token), created=_now(), link=link,
                                        policy=None if link else (policy or read_only_policy()),
                                        uid=secrets.token_hex(8))
        self._change(change)
        return token

    def rotate(self, name: str) -> str:
        """A new token for `name`, same policy; the old one stops working at once."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)

        def change(records):
            record = self._existing(records, name)
            records[name] = record.model_copy(update={"sha256": token_digest(token), "rotated": _now()})
        self._change(change)
        return token

    def revoke(self, name: str) -> None:
        """Removes the token and its policy."""
        def change(records):
            self._existing(records, name)
            del records[name]
        self._change(change)

    def set_policy(self, name: str, policy: ToolPolicy) -> None:
        """Gives the token its own policy (a linked token stops using the other caller's policy)."""
        ToolPolicy.model_validate(policy.model_dump())

        def change(records):
            record = self._existing(records, name)
            records[name] = record.model_copy(update={"policy": policy, "link": None})
        self._change(change)

    @staticmethod
    def _existing(records: Dict[str, TokenRecord], name: str) -> TokenRecord:
        if name not in records:
            raise TokenStoreError(f"There is no token named '{name}' ('llm-cli mcp token list' shows them).")
        return records[name]


# --- a person's changes to the tokens: one code path for `llm-cli mcp token` and the GUI ---------------------------
# Every function takes the tool registry (its token store, policy store and audit log) and the person's window as the
# audit actor (cli or gui); none of them is reachable from a tool, a resource or a prompt.

class UnknownCaller(TokenStoreError):
    """--policy-from names neither mcp, ditto nor an existing token."""


def policy_summary(policy: ToolPolicy) -> str:
    """A token's permissions in one line: the decision per tier and how many per-tool settings it has."""
    from orchestrator.tools.policy import TIER_LABELS
    tiers = ", ".join(f"{tier.value}: {policy.tiers[tier].value}" for tier in TIER_LABELS)
    return tiers + (f" (+{len(policy.overrides)} per tool)" if policy.overrides else "")


def person_change(registry, actor: str, event: str, arguments: Dict) -> Optional[str]:
    """Records a person's change to who may call the tools in the audit log (`actor`: cli or gui). Names only, never a
    token. Returns why it could not be recorded (the change is done), or None (also without an audit log)."""
    audit = getattr(registry, "audit", None)
    if audit is None:
        return None
    try:
        audit.record(actor, event, "framework", "person", "ok", arguments=arguments, approved=True)
    except Exception as e:  # noqa: BLE001 - the change is done; the person is told it is not recorded
        from orchestrator.utils.helpers import redact_secrets
        return redact_secrets(str(e)) or type(e).__name__
    return None


def copied_policy(registry, source: str) -> ToolPolicy:
    """`--policy-from SOURCE`: a copy of another caller's permissions for a new token. `mcp` (local stdio clients) and
    `ditto` are those callers (their own section or the shared permissions; the copy counts as set by the person, so
    the token is no longer treated as a new, read-only one); any other name, and `mcp:NAME` always, is a token whose
    own permissions are copied as they are. Later changes of the source do not apply to the copy. UnknownCaller for
    a name that is neither; TokenStoreError when the stored permissions cannot be used."""
    named_token = source.startswith(ACTOR_PREFIX)
    name = source[len(ACTOR_PREFIX):] if named_token else source
    if not named_token and name in ("mcp", "ditto"):
        policy = registry.policy(name)
        problem = getattr(registry.policy_store, "problem", None)
        if problem:
            raise TokenStoreError(problem)
    elif registry.tokens is not None and registry.tokens.get(name) is not None:
        policy = registry.policy(ACTOR_PREFIX + name)
    else:
        raise UnknownCaller(f"Unknown caller '{source}': use mcp, ditto or a token name.")
    return policy.model_copy(deep=True, update={"configured": True} if not named_token else {})


def _via(via: Optional[str]) -> Dict[str, str]:
    return {"via": via} if via else {}


def add_token(registry, name: str, actor: str, policy_from: Optional[str] = None, link: Optional[str] = None,
              via: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Creates the token `name` (read only, a copy of `policy_from`'s permissions, or `link`ed to Ditto's) and audits
    it as mcp_token.add. Returns (the token, which is shown only now; why the audit could not record it, or None).
    TokenStoreError (UnknownCaller for an unknown --policy-from) when nothing was created."""
    if policy_from and link:
        raise TokenStoreError("Use either --policy-from or --link.")
    policy = copied_policy(registry, policy_from) if policy_from else None
    token = registry.tokens.add(name, policy=policy, link=link)
    problem = person_change(registry, actor, "mcp_token.add",
                            {"token": name, "policy_from": policy_from, "link": link, **_via(via)})
    return token, problem


def set_token_policy(registry, name: str, policy: ToolPolicy, actor: str, via: Optional[str] = None) -> Optional[str]:
    """Gives the token `name` its own permissions `policy` (a linked token stops using the other caller's), audited
    as mcp_token.permissions with the permissions before and after. Returns why the audit could not record it, or
    None. TokenStoreError when there is no such token (nothing changed)."""
    record = registry.tokens.get(name)
    if record is None:
        raise TokenStoreError(f"There is no token named '{name}' ('llm-cli mcp token list').")
    before = registry.policy(ACTOR_PREFIX + name)
    registry.tokens.set_policy(name, policy)
    return person_change(registry, actor, "mcp_token.permissions",
                         {"token": name, "before": policy_summary(before), "after": policy_summary(policy),
                          "was_linked": record.link, **_via(via)})


def reset_token_policy(registry, name: str, actor: str, via: Optional[str] = None) -> Optional[str]:
    """`tools permissions --reset --token NAME`: the token is a new token again (read_only_policy, not configured: it
    may read and never more than your shared setting for a tool or the tool's own default), not a token with
    hand-set Allow/Deny. Returns why the audit could not record it, or None."""
    return set_token_policy(registry, name, read_only_policy(), actor, via)


@dataclass
class TokenRevoked:
    """What revoke_token did: the token and its permissions are gone; its standing approvals too, unless
    `grants_problem` says why not (they no longer apply: a new token of the same name never inherits them)."""
    name: str
    grants_removed: int = 0
    grants_problem: Optional[str] = None
    audit_problem: Optional[str] = None


def revoke_token(registry, name: str, actor: str, via: Optional[str] = None) -> TokenRevoked:
    """Revokes the token `name` completely: the token and its own permissions (its client loses access at once,
    also its open connections), audited as mcp_token.revoke, then its standing approvals (grants of mcp:NAME) in the
    shared permissions, audited with that save. TokenStoreError when there is no such token or the token store
    cannot be changed (then nothing changed)."""
    registry.tokens.revoke(name)
    result = TokenRevoked(name, audit_problem=person_change(registry, actor, "mcp_token.revoke",
                                                            {"token": name, **_via(via)}))
    owner = ACTOR_PREFIX + name
    grants = registry.policy().grants.get(owner)
    if grants:
        try:
            registry.policy_store.update(lambda p: p.model_copy(update={"grants": {
                caller: items for caller, items in p.grants.items() if caller != owner}}), actor=actor)
            result.grants_removed = len(grants)
        except Exception as e:  # noqa: BLE001 - reported; they no longer apply
            from orchestrator.utils.helpers import redact_secrets
            result.grants_problem = redact_secrets(str(e)) or type(e).__name__
    return result
