#!/usr/bin/env python3
"""
Errors of the LLM layer. str(error) is a user-facing message: it never contains an API key, and provider
text is passed through redact_secrets before it is shown. Errors with a known cause carry `code` and `actions`
and are AuthErrors (orchestrator/llm/auth/errors.py), which also recognizes the causes.
"""

import email.utils
import time
from typing import Any, Optional, Sequence

from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.next_steps import WithSteps


class LLMError(WithSteps, Exception):
    retryable = False

    def __init__(self, message: str, *, provider: str = "", status: Optional[int] = None,
                 request_id: Optional[str] = None, code: Optional[str] = None, actions: Sequence[Any] = ()):
        super().__init__(redact_secrets(message))
        self.provider = provider
        self.status = status
        self.request_id = request_id
        self.code = code  # a known cause, e.g. "anthropic.workspace_required"; None for the generic mapping
        self.actions = tuple(actions)  # AuthAction: what the user can do next (GUI buttons, CLI "Next:" lines)


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


# Known Claude API causes in plain language (the texts live in the auth error table).
ANTHROPIC_WORKSPACE_REQUIRED = (
    "This Claude API key belongs to your account, not to one workspace. Enter the workspace ID (wrkspc_..., "
    "Console > Settings > Workspaces) in the AI settings, or create a key limited to one workspace.")
ANTHROPIC_WORKSPACE_INVALID = "The workspace ID is not valid. It looks like wrkspc_..."
ANTHROPIC_ADMIN_KEY = "This is an Admin API key. Ditto needs a normal API key (Console > Settings > API keys)."
ANTHROPIC_AUTH = "Claude rejected the key: it is wrong, was revoked, or has expired."


def rejected_key_error(preset: Any) -> NotConfigured:
    """The key in use is of a kind the provider refuses for chat (an admin key, the method's reject_prefixes)."""
    from orchestrator.llm.auth.errors import auth_error
    if preset.adapter == "anthropic":
        return auth_error("anthropic.admin_key", provider=preset.id)
    if preset.id == "openai":
        return auth_error("openai.admin_key", provider=preset.id)
    return auth_error(f"{preset.id}.admin_key", provider=preset.id,
                      message=f"{preset.label}: this is an admin or management key, which cannot be used for chat. "
                              "Create a normal API key.")


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


def without_secret(text: str, secret: Optional[str]) -> str:
    """`text` without the credential itself: a server may repeat a key in its error text whose format no pattern
    knows (a custom server's "invalid key <key>")."""
    if text and secret and len(secret) >= 8:
        return text.replace(secret, "REDACTED")
    return text


def _detail(err: Any, secret: Optional[str] = None) -> str:
    """The provider's own error message (redacted, shortened)."""
    body = getattr(err, "body", None)
    message = None
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict):
            message = inner.get("message")
    message = message or getattr(err, "message", None) or str(err)
    message = redact_secrets(without_secret(str(message), secret)).strip()
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


status_error = _status_error  # the generic class and message for an HTTP status (also used by the credential check)


def _known_cause(err: Any, detail: str, label: str, kw: dict, generic: LLMError, *, preset: Any, base_url: Any,
                 profile: Optional[str] = None, error_type: Optional[str] = None, error_code: Optional[str] = None,
                 retry_after: Optional[float] = None, during: str = "request") -> LLMError:
    """The generic error, or an AuthError with code and actions when the answer has a known cause. `during` is
    "models" for a model list (a 403 there means the key works but may not list models)."""
    if preset is None:
        return generic
    from orchestrator.llm.auth.errors import auth_error, classify, engine_name
    try:
        body = err.response.json()  # the whole answer: the SDKs keep only its "error" part as err.body
    except Exception:
        body = getattr(err, "body", None)
    found = classify(preset, err.status_code, detail, body, headers=err.response.headers, base_url=base_url,
                     profile=profile is not None, error_type=error_type, error_code=error_code, during=during)
    if found is None:
        return generic
    code, values = found
    kind = None  # the table row decides the class: a spend cap is not a rate limit to retry
    if code == "anthropic.profile":
        values = {**values, "profile": f" '{profile}'" if profile else ""}
        kind = "auth_failed"  # the profile's token was rejected (401); a profile that cannot be read is not
    from orchestrator.llm.auth.store import display_origin
    return auth_error(code, detail=detail, kind=kind, retry_after=retry_after, provider_label=label,
                      engine=engine_name(preset), url=display_origin(base_url), **values, **kw)


def map_anthropic_error(err: BaseException, label: str = "Claude API", provider: str = "anthropic", *,
                        profile: Optional[str] = None, preset: Any = None, base_url: Optional[str] = None,
                        secret: Optional[str] = None) -> LLMError:
    """Maps anthropic SDK exceptions, most specific class first. `profile` is set (the profile name, or "" when
    unknown) when the request was signed in with an `ant auth login` profile instead of an API key. With `preset`,
    answers with a known cause become AuthErrors (code, plain message, actions). `secret` (the key the request
    was sent with) never appears in the message."""
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
        detail = _detail(err, secret)
        error_type = getattr(err, "type", None)
        generic = _status_error(err.status_code, detail, label, retry_after=retry_after,
                                code="insufficient_quota" if error_type == "billing_error" else None, kw=kw)
        return _known_cause(err, detail, label, kw, generic, preset=preset if preset is not None else _CLAUDE,
                            base_url=base_url, profile=profile, error_type=error_type, retry_after=retry_after)
    credentials_error = getattr(anthropic, "CredentialsError", None)
    if credentials_error is not None and isinstance(err, credentials_error):
        from orchestrator.llm.auth.errors import auth_error
        return auth_error("anthropic.profile", detail=redact_secrets(str(err))[:300],
                          profile=f" '{profile}'" if profile else "", **kw)
    return LLMError(f"{label} request failed: {redact_secrets(without_secret(str(err), secret))[:300]}", **kw)


class _ClaudeAPI:
    """Stands in for the preset when a caller passes none: the Claude API's own causes still apply."""
    adapter = "anthropic"
    id = "anthropic"
    label = "Claude API"
    base_url = "https://api.anthropic.com"

    @staticmethod
    def is_local(url=None) -> bool:
        return False


_CLAUDE = _ClaudeAPI()


def map_openai_error(err: BaseException, label: str = "OpenAI-compatible server", provider: str = "", *,
                     preset: Any = None, base_url: Optional[str] = None, during: str = "request",
                     secret: Optional[str] = None) -> LLMError:
    """Maps openai SDK exceptions, most specific class first. With `preset`, answers with a known cause become
    AuthErrors (code, plain message, actions), and a local server that is not running says so. `during` is
    "models" for the model list. `secret` (the key the request was sent with) never appears in the message."""
    import openai

    if isinstance(err, LLMError):
        return err
    kw = {"provider": provider}
    if isinstance(err, openai.APITimeoutError):
        return ProviderTimeout(f"{label} did not answer in time.", **kw)
    if isinstance(err, openai.APIConnectionError):
        if preset is not None and preset.is_local(base_url):
            from orchestrator.llm.auth.errors import auth_error, engine_name
            from orchestrator.llm.auth.store import display_origin
            return auth_error("local.not_running", engine=engine_name(preset), url=display_origin(base_url),
                              hint=" or check the address", **kw)
        return ProviderUnavailable(f"Could not connect to {label}. Is the server running and the URL correct?", **kw)
    if isinstance(err, openai.APIStatusError):
        kw.update(status=err.status_code, request_id=getattr(err, "request_id", None))
        retry_after = parse_retry_after(err.response.headers)
        code = getattr(err, "code", None)
        detail = _detail(err, secret)
        generic = _status_error(err.status_code, detail, label, retry_after=retry_after, code=code, kw=kw)
        return _known_cause(err, detail, label, kw, generic, preset=preset, base_url=base_url, error_code=code,
                            retry_after=retry_after, during=during)
    return LLMError(f"{label} request failed: {redact_secrets(without_secret(str(err), secret))[:300]}", **kw)
