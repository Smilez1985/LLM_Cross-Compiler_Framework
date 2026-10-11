#!/usr/bin/env python3
"""
Sign-in problems with a known cause: AuthError(code, message, actions).

`message` is plain language, `actions` are the GUI's buttons and the CLI's "Next:" lines, `exit_code` is the CLI's
exit code (3 authentication, 4 network, 5 storage, 6 method not available). Each error is also an instance of the
matching LLMError class (NotConfigured, AuthFailed, BillingError, ...), so callers that know only those keep
working. Provider text is passed through redact_secrets before it becomes part of a message.

classify() recognizes the causes from an HTTP status, the provider's error text and body; it is shared by the
chat path (orchestrator/llm/errors.py) and the credential check (validate.py).
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

from orchestrator.llm.errors import (ANTHROPIC_ADMIN_KEY, ANTHROPIC_AUTH, ANTHROPIC_WORKSPACE_INVALID,
                                     ANTHROPIC_WORKSPACE_REQUIRED, AuthFailed, BadRequest, BillingError, LLMError,
                                     NotConfigured, PermissionDenied, ProviderUnavailable, RateLimited)
from orchestrator.utils.helpers import redact_secrets

EXIT_AUTH, EXIT_NETWORK, EXIT_STORAGE, EXIT_POLICY = 3, 4, 5, 6


@dataclass(frozen=True)
class AuthAction:
    """What the user can do next: a button in the GUI, a "Next:" line in the CLI."""
    id: str
    label: str
    url: Optional[str] = None
    command: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        return {k: v for k, v in (("id", self.id), ("label", self.label), ("url", self.url),
                                  ("command", self.command)) if v}


class AuthError(LLMError):
    """A sign-in problem with a known cause."""

    def __init__(self, code: str, message: str, actions: Sequence[AuthAction] = (), **kw):
        super().__init__(message, code=code, actions=tuple(actions), **kw)

    @property
    def message(self) -> str:
        return str(self)

    @property
    def exit_code(self) -> int:
        spec = ERRORS.get(self.code or "")
        if spec is not None:
            return spec.exit_code
        return EXIT_STORAGE if (self.code or "").startswith("storage.") else EXIT_AUTH

    def to_dict(self) -> Dict[str, Any]:
        """For `auth status --json` and the GUI (no secrets: the message is redacted)."""
        return {"code": self.code, "message": str(self), "actions": [a.to_dict() for a in self.actions],
                "exit_code": self.exit_code}


class AuthNotConfigured(AuthError, NotConfigured):
    pass


class AuthRejected(AuthError, AuthFailed):
    pass


class AuthPermission(AuthError, PermissionDenied):
    pass


class AuthBilling(AuthError, BillingError):
    pass


class AuthUnavailable(AuthError, ProviderUnavailable):
    pass


class AuthRateLimited(AuthError, RateLimited):
    pass


class AuthBadRequest(AuthError, BadRequest):
    pass


KINDS = {"not_configured": AuthNotConfigured, "auth_failed": AuthRejected, "permission": AuthPermission,
         "billing": AuthBilling, "unavailable": AuthUnavailable, "rate_limited": AuthRateLimited,
         "bad_request": AuthBadRequest}


@dataclass(frozen=True)
class ErrorSpec:
    message: str                       # str.format template; unknown names stay empty
    actions: Tuple[AuthAction, ...] = ()
    kind: str = "not_configured"
    exit_code: int = EXIT_AUTH


CLAUDE_KEYS = "https://platform.claude.com/settings/keys"
CLAUDE_WORKSPACES = "https://platform.claude.com/settings/workspaces"
CLAUDE_BILLING = "https://platform.claude.com/settings/billing"
OPENAI_KEYS = "https://platform.openai.com/settings/organization/api-keys"
OPENAI_BILLING = "https://platform.openai.com/settings/organization/billing/overview"
AI_STUDIO_KEYS = "https://aistudio.google.com/apikey"
OPENROUTER_KEYS = "https://openrouter.ai/keys"

OPEN_KEY_PAGE = AuthAction("open_key_page", "Open key page", url="{key_page}")
EDIT_WORKSPACE = AuthAction("edit_workspace_id", "Edit workspace ID")

# The rows of the design's error table (section 4.6) for the providers in the catalog today, plus the generic
# cases of the auth core itself (auth.*).
ERRORS: Dict[str, ErrorSpec] = {
    "anthropic.workspace_required": ErrorSpec(
        ANTHROPIC_WORKSPACE_REQUIRED,
        (AuthAction("enter_workspace_id", "Enter workspace ID"),
         AuthAction("open_console_workspaces", "Open Console workspaces", url=CLAUDE_WORKSPACES))),
    "anthropic.workspace_invalid": ErrorSpec(ANTHROPIC_WORKSPACE_INVALID, (EDIT_WORKSPACE,)),
    "anthropic.workspace_not_found": ErrorSpec("This key has no access to workspace {workspace}.", (EDIT_WORKSPACE,)),
    "anthropic.admin_key": ErrorSpec(ANTHROPIC_ADMIN_KEY,
                                     (AuthAction("open_key_page", "Open key page", url=CLAUDE_KEYS),)),
    "anthropic.auth": ErrorSpec(ANTHROPIC_AUTH, (AuthAction("open_key_page", "Open key page", url=CLAUDE_KEYS),),
                                "auth_failed"),
    "anthropic.billing": ErrorSpec(
        "Your Claude Console organization has no credits. Add credits, or link your Max/Team plan's monthly API "
        "credits (claude.ai > Settings > Billing > API credits).",
        (AuthAction("open_billing", "Open billing", url=CLAUDE_BILLING),), "billing"),
    "anthropic.spend_limit": ErrorSpec("The spend limit of this organization or workspace is reached.",
                                       (AuthAction("open_console", "Open Console", url="https://platform.claude.com"),),
                                       "billing"),
    "anthropic.profile": ErrorSpec("The Anthropic CLI profile{profile} cannot be used. Run `ant auth login`.",
                                   (AuthAction("copy_command", "Copy command", command="ant auth login"),)),
    "openai.auth": ErrorSpec("OpenAI rejected the key. Check that it belongs to a project you can use.",
                             (AuthAction("open_key_page", "Open key page", url=OPENAI_KEYS),), "auth_failed"),
    "openai.quota": ErrorSpec("No credit left on this OpenAI project.",
                              (AuthAction("open_billing", "Open billing", url=OPENAI_BILLING),), "billing"),
    "openai.no_model_list": ErrorSpec(
        "The key works but may not list models. Type a model id, or give the key the 'List models' permission.",
        (AuthAction("enter_model", "Enter model"),), "permission"),
    "openai.admin_key": ErrorSpec("Admin keys cannot be used for chat.",
                                  (AuthAction("open_key_page", "Open key page", url=OPENAI_KEYS),)),
    "gemini.key_rejected": ErrorSpec(
        "Google rejected this key. Create a new key in AI Studio, or restrict this key to the Gemini API in the "
        "Cloud console.", (AuthAction("open_ai_studio", "Open AI Studio", url=AI_STUDIO_KEYS),), "auth_failed"),
    "openrouter.key_limit": ErrorSpec("This OpenRouter key reached its credit limit.",
                                      (AuthAction("open_key_page", "Open key page", url=OPENROUTER_KEYS),), "billing"),
    "local.not_running": ErrorSpec("Nothing is listening on {url}. Start {engine}{hint}.",
                                   (AuthAction("detect_again", "Detect again"),), "unavailable", EXIT_NETWORK),
    "local.key_needed": ErrorSpec("{engine} requires an API key{where}.", (AuthAction("enter_key", "Enter key"),),
                                  "auth_failed"),
    "local.ollama_signin": ErrorSpec(
        "Ollama is not signed in to ollama.com, so cloud models are unavailable. Run `ollama signin`.",
        (AuthAction("open_signin", "Open sign-in", url="{signin_url}", command="ollama signin"),), "auth_failed"),
    "local.wrong_path": ErrorSpec(
        "{url} answered 404: the address probably lacks the server's path prefix (for example /v1).",
        (AuthAction("edit_url", "Edit address"),), "bad_request"),
    "storage.keyring_unavailable": ErrorSpec(
        "Your system keyring is locked or not available. Choose how to protect your credentials.",
        (AuthAction("choose_storage", "Choose storage"),), exit_code=EXIT_STORAGE),
    "storage.vault_unreadable": ErrorSpec(
        "The credential vault cannot be read and was not changed. Restore it or reset it (you will need to sign "
        "in again).", (AuthAction("reset_vault", "Reset vault"),), exit_code=EXIT_STORAGE),
    "auth.not_signed_in": ErrorSpec("{provider_label}: not signed in.",
                                    (AuthAction("sign_in", "Sign in"), OPEN_KEY_PAGE)),
    "auth.signed_out": ErrorSpec("{provider_label}: the sign-in expired or was revoked. Sign in again.",
                                 (AuthAction("sign_in", "Sign in again"),)),
    "auth.rejected": ErrorSpec("{provider_label} rejected the credentials. Check the key or sign in again.",
                               (OPEN_KEY_PAGE,), "auth_failed"),
    "auth.field_invalid": ErrorSpec("{provider_label}: the {field} is not valid.",
                                    (AuthAction("edit_field", "Edit"),)),
    "auth.unexpected": ErrorSpec("{provider_label} answered the check unexpectedly.", (AuthAction("retry", "Retry"),),
                                 "bad_request"),
    "auth.network": ErrorSpec("Could not reach {provider_label} ({url}). Check the network or proxy.",
                              (AuthAction("retry", "Retry"),), "unavailable", EXIT_NETWORK),
    "auth.refresh_busy": ErrorSpec("{provider_label}: another program is renewing this sign-in. Try again.",
                                   (AuthAction("retry", "Retry"),), "unavailable", EXIT_NETWORK),
    "auth.refresh_failed": ErrorSpec("{provider_label}: the sign-in could not be renewed (the answer had no "
                                     "token). Try again later or sign in again.",
                                     (AuthAction("retry", "Retry"), AuthAction("sign_in", "Sign in again")),
                                     "unavailable", EXIT_NETWORK),
    "auth.not_built": ErrorSpec("{provider_label}: the sign-in method '{method}' is not available in this version.",
                                (AuthAction("switch_method", "Choose another method"),), exit_code=EXIT_POLICY),
}


class _Blank(dict):
    def __missing__(self, key):
        return ""


def _fill(template: Optional[str], values: Mapping[str, Any]) -> Optional[str]:
    if template is None:
        return None
    filled = template.format_map(_Blank({k: "" if v is None else str(v) for k, v in values.items()}))
    return filled or None


def auth_error(code: str, *, message: Optional[str] = None, detail: Optional[str] = None,
               kind: Optional[str] = None, provider: str = "", status: Optional[int] = None,
               request_id: Optional[str] = None, retry_after: Optional[float] = None, **values: Any) -> AuthError:
    """The AuthError for `code`, its message and actions filled from `values` (provider_label, url, key_page, ...).
    `detail` (the provider's own text) is redacted and appended; `kind` overrides the class of the table row."""
    spec = ERRORS.get(code)
    if spec is None:
        spec = ErrorSpec(message or code, exit_code=EXIT_STORAGE if code.startswith("storage.") else EXIT_AUTH)
    text = message or _fill(spec.message, values) or code
    if detail:
        text = f"{text} ({redact_secrets(str(detail)).strip()[:500]})"
    actions = []
    for action in spec.actions:
        url, command = _fill(action.url, values), _fill(action.command, values)
        if action.url and not url and not command:
            continue  # a link the case has no address for
        actions.append(AuthAction(action.id, action.label, url=url, command=command))
    cls = KINDS[kind or spec.kind]
    kw: Dict[str, Any] = {"provider": provider, "status": status, "request_id": request_id}
    if cls in (AuthUnavailable, AuthRateLimited):
        kw["retry_after"] = retry_after
    return cls(code, text, actions, **kw)


def storage_error(error: Any, provider: str = "") -> AuthError:
    """An AuthError for a vault StorageError (its own text when the code has no row of its own)."""
    code = getattr(error, "code", None) or "storage.unavailable"
    own = getattr(error, "message", None) or str(error) or None
    return auth_error(code, message=None if code in ERRORS else own, detail=own if code in ERRORS else None,
                      provider=provider)


# --- recognizing causes -----------------------------------------------------------------------------------------

_WORKSPACE_NOT_FOUND = re.compile(r"workspace\s+`?([A-Za-z0-9_-]+)`?\s+not found", re.I)
OPENAI_HOSTS = ("api.openai.com",)
GEMINI_HOSTS = ("generativelanguage.googleapis.com",)
OPENROUTER_HOSTS = ("openrouter.ai",)
OLLAMA_SIGNIN_HOSTS = ("ollama.com", "www.ollama.com")


def _host(url: Optional[str]) -> str:
    return (urlparse(url or "").hostname or "").lower()


def vendor_of(preset: Any, base_url: Optional[str] = None) -> str:
    """anthropic | openai | gemini | openrouter | local | other: whose error texts apply."""
    url = base_url or getattr(preset, "base_url", None)
    host = _host(url)
    if getattr(preset, "adapter", "") == "anthropic":
        return "anthropic"
    if host in OPENAI_HOSTS:
        return "openai"
    if host in GEMINI_HOSTS:
        return "gemini"
    if host in OPENROUTER_HOSTS:
        return "openrouter"
    is_local = getattr(preset, "is_local", None)
    if callable(is_local) and is_local(url):
        return "local"
    return "other"


def _dig(body: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(body, dict):
            return None
        body = body.get(key)
    return body


def _error_body(body: Any) -> Any:
    """Gemini's OpenAI-compatible endpoint wraps its error in a list."""
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return body[0]
    return body


def signin_url(body: Any) -> Optional[str]:
    """The ollama.com sign-in link of an Ollama 401, only when it really points to ollama.com."""
    url = _dig(_error_body(body), "signin_url")
    if isinstance(url, str) and url.startswith("https://") and _host(url) in OLLAMA_SIGNIN_HOSTS:
        return url
    return None


def classify(preset: Any, status: int, detail: str = "", body: Any = None, *, headers: Any = None,
             base_url: Optional[str] = None, profile: bool = False, error_type: Optional[str] = None,
             error_code: Optional[str] = None, during: str = "request") -> Optional[Tuple[str, Dict[str, Any]]]:
    """(code, values for the message) for an answer with a known cause, None otherwise.
    `during` is "models" for the credential check (a model list), "request" otherwise."""
    lowered = (detail or "").lower()
    body = _error_body(body)
    vendor = vendor_of(preset, base_url)
    has_retry_after = bool(headers is not None and (headers.get("retry-after") or headers.get("retry-after-ms")))
    if vendor == "anthropic":
        if status == 400 and "anthropic-workspace-id is required" in lowered:
            return "anthropic.workspace_required", {}
        if status == 400 and "anthropic-workspace-id" in lowered and "valid workspace id" in lowered:
            return "anthropic.workspace_invalid", {}
        if status == 404:
            found = _WORKSPACE_NOT_FOUND.search(detail or "")
            if found:
                return "anthropic.workspace_not_found", {"workspace": found.group(1)}
        if status == 401:
            return ("anthropic.profile", {}) if profile else ("anthropic.auth", {})
        if status == 402 or error_type == "billing_error" or "credit balance" in lowered:
            return "anthropic.billing", {}
        if ("spend limit" in lowered or "spend cap" in lowered) and (status == 400 or (status == 429
                                                                                        and not has_retry_after)):
            return "anthropic.spend_limit", {}
        return None
    if vendor == "openai":
        if status in (401, 403) and "admin" in lowered and "key" in lowered:
            return "openai.admin_key", {}
        if status == 401:
            return "openai.auth", {}
        if status == 429 and (error_code == "insufficient_quota" or "insufficient_quota" in lowered
                              or _dig(body, "error", "code") == "insufficient_quota"):
            return "openai.quota", {}
        if status == 403 and during == "models":
            return "openai.no_model_list", {}
        return None
    if vendor == "gemini":
        if status in (401, 403) or (status == 400 and ("api key" in lowered or "api_key" in lowered)):
            return "gemini.key_rejected", {}
        return None
    if vendor == "openrouter":
        if status == 402 and _dig(body, "error", "metadata", "limit_source") == "openrouter_key_limit":
            return "openrouter.key_limit", {}
        if status == 401:
            return "auth.rejected", {}
        return None
    if vendor == "local":
        if status == 401:
            if _dig(body, "signin_url"):  # Ollama without its ollama.com sign-in; the link only if it is ollama.com
                return "local.ollama_signin", {"signin_url": signin_url(body)}
            return "local.key_needed", {}
        if status == 404 and during == "models":
            return "local.wrong_path", {}
        return None
    if status == 401 and during == "models":
        return "auth.rejected", {}
    return None


def engine_name(preset: Any) -> str:
    """The preset's label without its explanation in parentheses ("Ollama (local)" -> "Ollama")."""
    label = str(getattr(preset, "label", "") or getattr(preset, "id", "the server"))
    return re.sub(r"\s*\(.*\)\s*$", "", label) or label
