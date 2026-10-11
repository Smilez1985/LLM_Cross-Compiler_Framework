#!/usr/bin/env python3
"""
Errors of the LLM layer. str(error) is a user-facing message: it never contains an API key, and provider
text is passed through redact_secrets before it is shown.
"""

import email.utils
import re
import time
from typing import Any, Optional, Tuple

from orchestrator.utils.helpers import redact_secrets


class LLMError(Exception):
    retryable = False

    def __init__(self, message: str, *, provider: str = "", status: Optional[int] = None,
                 request_id: Optional[str] = None, code: Optional[str] = None):
        super().__init__(redact_secrets(message))
        self.provider = provider
        self.status = status
        self.request_id = request_id
        self.code = code  # a known cause, e.g. "anthropic.workspace_required"; None for the generic mapping


class NotConfigured(LLMError):
    """Provider, model, endpoint or credentials are missing."""


class AuthFailed(LLMError):
    """The credentials were rejected (401)."""


class PermissionDenied(LLMError):
    """The credentials are valid but not allowed to do this (403)."""


class BillingError(PermissionDenied):
    """No credit or quota left on the account (402, OpenAI insufficient_quota, Anthropic credit balance)."""


class RateLimited(LLMError):
    retryable = True

    def __init__(self, message: str, *, retry_after: Optional[float] = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class ProviderTimeout(LLMError):
    """The request took too long. Not retried automatically: a retry would make the user wait twice."""


class ProviderUnavailable(LLMError):
    """5xx, overload or no connection."""
    retryable = True

    def __init__(self, message: str, *, retry_after: Optional[float] = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class BadRequest(LLMError):
    """The provider rejected the request (400/404/413/422): wrong model id, unsupported parameter, ..."""


class Refused(LLMError):
    """The model or a safety classifier declined. `result` holds the partial ChatResult, if any."""

    def __init__(self, message: str, *, category: Optional[str] = None, explanation: Optional[str] = None,
                 result: Any = None, **kw):
        super().__init__(message, **kw)
        self.category = category
        self.explanation = explanation
        self.result = result


class Truncated(LLMError):
    """The answer hit max_tokens before it was complete (a JSON answer or tool call cannot be used)."""

    def __init__(self, message: str, *, result: Any = None, **kw):
        super().__init__(message, **kw)
        self.result = result


class InvalidOutput(LLMError):
    """The answer did not match the requested JSON schema, also after one repair attempt."""


# Known Claude API causes in plain language.
ANTHROPIC_WORKSPACE_REQUIRED = (
    "This Claude API key belongs to your account, not to one workspace. Enter the workspace ID (wrkspc_..., "
    "Console > Settings > Workspaces) in the AI settings, or create a key limited to one workspace.")
ANTHROPIC_WORKSPACE_INVALID = "The workspace ID is not valid. It looks like wrkspc_..."
ANTHROPIC_WORKSPACE_NOT_FOUND = "This key has no access to workspace {workspace}."
ANTHROPIC_ADMIN_KEY = "This is an Admin API key. Ditto needs a normal API key (Console > Settings > API keys)."
ANTHROPIC_AUTH = "Claude rejected the key: it is wrong, was revoked, or has expired."
_WORKSPACE_NOT_FOUND = re.compile(r"workspace\s+`?([A-Za-z0-9_-]+)`?\s+not found", re.I)


def rejected_key_error(preset: Any) -> NotConfigured:
    """The key in use is of a kind the provider refuses for chat (preset.reject_key_prefixes)."""
    if preset.adapter == "anthropic":
        return NotConfigured(ANTHROPIC_ADMIN_KEY, provider=preset.id, code="anthropic.admin_key")
    return NotConfigured(f"{preset.label}: this is an admin or management key, which cannot be used for chat. "
                         "Create a normal API key.", provider=preset.id, code=f"{preset.id}.admin_key")


def parse_retry_after(headers: Any) -> Optional[float]:
    """Seconds from retry-after-ms / retry-after (seconds or HTTP date), like the SDKs read them."""
    if headers is None:
        return None
    try:
        ms = headers.get("retry-after-ms")
        if ms is not None:
            return max(0.0, float(ms) / 1000)
    except (TypeError, ValueError):
        pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    parsed = email.utils.parsedate_tz(value)
    if parsed is None:
        return None
    return max(0.0, float(email.utils.mktime_tz(parsed) - time.time()))


def _detail(err: Any) -> str:
    """The provider's own error message (redacted, shortened)."""
    body = getattr(err, "body", None)
    message = None
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict):
            message = inner.get("message")
    message = message or getattr(err, "message", None) or str(err)
    message = redact_secrets(str(message)).strip()
    return message[:500]


def _status_error(status: int, detail: str, label: str, *, retry_after: Optional[float], code: Optional[str],
                  kw: dict) -> LLMError:
    lowered = detail.lower()
    if status == 401:
        return AuthFailed(f"{label} rejected the credentials. Check the API key or sign in again. ({detail})", **kw)
    if status == 402 or code == "insufficient_quota" or "credit balance" in lowered:
        return BillingError(f"{label}: no credit or quota left on this account. ({detail})", **kw)
    if status == 403:
        return PermissionDenied(f"{label} does not allow this request for your account. ({detail})", **kw)
    if status == 429:
        wait = f" Try again in {int(retry_after) + 1} s." if retry_after else ""
        return RateLimited(f"{label} rate limit reached.{wait} ({detail})", retry_after=retry_after, **kw)
    if status == 408:
        return ProviderTimeout(f"{label} timed out. ({detail})", **kw)
    if status == 404:
        return BadRequest(f"{label}: model or endpoint not found. Check the model id. ({detail})", **kw)
    if status >= 500 or status == 409:
        return ProviderUnavailable(f"{label} is temporarily unavailable ({status}). ({detail})",
                                   retry_after=retry_after, **kw)
    return BadRequest(f"{label} rejected the request ({status}). ({detail})", **kw)


def _anthropic_cause(status: int, detail: str) -> Optional[Tuple[str, str]]:
    """(code, message) for Claude API answers that have a specific fix."""
    lowered = detail.lower()
    if status == 400 and "anthropic-workspace-id is required" in lowered:
        return "anthropic.workspace_required", ANTHROPIC_WORKSPACE_REQUIRED
    if status == 400 and "anthropic-workspace-id" in lowered and "valid workspace id" in lowered:
        return "anthropic.workspace_invalid", ANTHROPIC_WORKSPACE_INVALID
    if status == 404:
        found = _WORKSPACE_NOT_FOUND.search(detail)
        if found:
            return "anthropic.workspace_not_found", ANTHROPIC_WORKSPACE_NOT_FOUND.format(workspace=found.group(1))
    if status == 401:
        return "anthropic.auth", ANTHROPIC_AUTH
    return None


def map_anthropic_error(err: BaseException, label: str = "Claude API", provider: str = "anthropic") -> LLMError:
    """Maps anthropic SDK exceptions, most specific class first."""
    import anthropic

    if isinstance(err, LLMError):
        return err
    kw = {"provider": provider}
    if isinstance(err, anthropic.APITimeoutError):
        return ProviderTimeout(f"{label} did not answer in time.", **kw)
    if isinstance(err, anthropic.APIConnectionError):
        return ProviderUnavailable(f"Could not connect to {label}. Check the network or proxy.", **kw)
    if isinstance(err, anthropic.APIStatusError):
        kw.update(status=err.status_code, request_id=getattr(err, "request_id", None))
        retry_after = parse_retry_after(err.response.headers)
        detail = _detail(err)
        cause = _anthropic_cause(err.status_code, detail)
        if cause is not None:
            code, message = cause
            cls = AuthFailed if code == "anthropic.auth" else NotConfigured
            return cls(f"{message} ({detail})", code=code, **kw)
        error_type = getattr(err, "type", None)
        code = "insufficient_quota" if error_type == "billing_error" else None
        return _status_error(err.status_code, detail, label, retry_after=retry_after, code=code, kw=kw)
    credentials_error = getattr(anthropic, "CredentialsError", None)
    if credentials_error is not None and isinstance(err, credentials_error):
        return NotConfigured(f"{label}: the sign-in profile could not be read. Run `ant auth login` again. "
                             f"({redact_secrets(str(err))[:300]})", **kw)
    return LLMError(f"{label} request failed: {redact_secrets(str(err))[:300]}", **kw)


def map_openai_error(err: BaseException, label: str = "OpenAI-compatible server", provider: str = "") -> LLMError:
    """Maps openai SDK exceptions, most specific class first."""
    import openai

    if isinstance(err, LLMError):
        return err
    kw = {"provider": provider}
    if isinstance(err, openai.APITimeoutError):
        return ProviderTimeout(f"{label} did not answer in time.", **kw)
    if isinstance(err, openai.APIConnectionError):
        return ProviderUnavailable(f"Could not connect to {label}. Is the server running and the URL correct?", **kw)
    if isinstance(err, openai.APIStatusError):
        kw.update(status=err.status_code, request_id=getattr(err, "request_id", None))
        retry_after = parse_retry_after(err.response.headers)
        code = getattr(err, "code", None)
        return _status_error(err.status_code, _detail(err), label, retry_after=retry_after, code=code, kw=kw)
    return LLMError(f"{label} request failed: {redact_secrets(str(err))[:300]}", **kw)
