#!/usr/bin/env python3
"""
The credential check behind "Check" and `auth status --check`: a GET that costs nothing (R7), taken from the
method's `validate` entry in the catalog (default: the model list), sent with the credential the resolver returned.

It tells apart a valid key (with the account or workspace it belongs to and the number of models), a key that works
but may not list models, a rejected key, a missing workspace ID, no credit, a server that is not running and a wrong
address. Redirects are not followed, so the key never goes to another host, and a loopback server is reached
without the environment's proxy. An `ant` profile and other SDK-owned sessions are checked through the SDK's own
model list (the caller passes it), never by reading the vendor's files.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from orchestrator.llm.auth.errors import AuthError, auth_error, classify, engine_name, vendor_of
from orchestrator.llm.auth.spec import AuthMethodSpec, ValidateSpec
from orchestrator.llm.auth.store import display_origin
from orchestrator.llm.errors import LLMError, NotConfigured, parse_retry_after, rejected_key_error, status_error
from orchestrator.security.redaction import reveal
from orchestrator.utils.helpers import redact_secrets

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 15.0


@dataclass
class ValidationResult:
    ok: bool
    provider: str
    method: str
    status: Optional[int] = None
    account_label: Optional[str] = None
    models: Optional[int] = None
    key_checked: bool = True               # False: the endpoint answers without a key, or no key was sent
    error: Optional[LLMError] = None       # with ok=True a warning (the key works but may not list models)
    checked: str = ""                      # "GET https://api.anthropic.com/v1/models" (never a key)

    def __repr__(self) -> str:
        return (f"ValidationResult(ok={self.ok}, provider={self.provider!r}, method={self.method!r}, "
                f"status={self.status}, code={getattr(self.error, 'code', None)!r})")

    @property
    def message(self) -> str:
        if not self.ok:
            return str(self.error) if self.error is not None else "The credential could not be checked."
        parts = ["Valid"]
        if self.account_label:
            parts.append(self.account_label)
        if self.models is not None:
            parts.append(f"{self.models} models")
        text = ", ".join(parts)
        if not self.key_checked:
            text += " (the server answers without a key, so no key was checked)"
        if self.error is not None:
            text += f". {self.error}"
        return text

    def to_dict(self) -> Dict[str, Any]:
        """For `auth status --json` and the GUI: no secrets."""
        error = self.error.to_dict() if isinstance(self.error, AuthError) else (
            {"code": getattr(self.error, "code", None), "message": str(self.error)} if self.error else None)
        return {"ok": self.ok, "provider": self.provider, "method": self.method, "status": self.status,
                "account_label": self.account_label, "models": self.models, "key_checked": self.key_checked,
                "checked": self.checked, "message": self.message, "error": error}


def _dig(data: Any, path: Optional[str]) -> Any:
    if not path:
        return None
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _detail(response: Any, body: Any) -> str:
    message = None
    if isinstance(body, list) and body and isinstance(body[0], dict):
        body = body[0]
    if isinstance(body, dict):
        inner = body.get("error", body)
        message = inner.get("message") if isinstance(inner, dict) else inner
    if not isinstance(message, str) or not message:
        message = (response.text or "")[:300]
    return redact_secrets(message).strip()[:500]


def default_validation(preset: Any) -> ValidateSpec:
    """The model list: /v1/models on the Claude API, /models below the base URL of every other family."""
    return ValidateSpec(path="/v1/models" if preset.adapter == "anthropic" else "/models")


def _label(spec: ValidateSpec, response: Any, body: Any) -> Optional[str]:
    value = response.headers.get(spec.account_header) if spec.account_header else None
    if value and spec.account_header.lower().endswith("workspace-id"):
        value = f"workspace {value}"
    if not value:
        found = _dig(body, spec.account_field)
        value = found if isinstance(found, str) else None
    return (redact_secrets(value).strip()[:120] or None) if value else None


def validate_credential(preset: Any, spec: AuthMethodSpec, credential: Any, base_url: Optional[str], *,
                        http_client: Any = None, timeout: float = DEFAULT_TIMEOUT,
                        list_models: Optional[Callable[[], List[Any]]] = None) -> ValidationResult:
    """Checks `credential` (a ResolvedCredential for `spec`). Never raises for an answer of the provider: the
    result says what was found. `list_models` checks credentials that only the SDK can use (an `ant` profile)."""
    result = ValidationResult(ok=False, provider=preset.id, method=spec.id)
    key = reveal(credential.api_key)
    if key and spec.rejected(key):
        result.error = rejected_key_error(preset)  # before any request
        return result
    if credential.kind not in ("none", "api_key"):
        if list_models is None:
            result.error = auth_error("auth.not_built", provider=preset.id, provider_label=preset.label,
                                      method=spec.id)
            return result
        result.checked = "model list through the SDK"
        try:
            result.models = len(list_models())
        except LLMError as e:
            result.error, result.status = e, e.status
            return result
        result.ok = True
        return result

    check = spec.validation or default_validation(preset)
    if check.url:
        url = check.url
    elif base_url:
        url = base_url.rstrip("/") + check.path
    else:
        result.error = NotConfigured(f"{preset.label}: no server URL is set.", provider=preset.id)
        return result
    result.checked = f"GET {display_origin(url)}{urlsplit(url).path}"
    headers = {"Accept": "application/json", **preset.headers}
    if preset.adapter == "anthropic":
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if key:
            headers["x-api-key"] = key
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    headers.update(credential.headers)
    result.key_checked = bool(key) and not check.public

    import httpx2
    from orchestrator.llm.client import is_loopback_url
    own_client = http_client is None
    client = http_client or httpx2.Client(timeout=timeout, follow_redirects=False, trust_env=not is_loopback_url(url))
    values = {"provider_label": preset.label, "engine": engine_name(preset), "url": display_origin(url),
              "where": f" ({spec.where})" if spec.where else "", "key_page": spec.key_page}
    try:
        response = client.request("GET", url, headers=headers, follow_redirects=False, timeout=timeout)
    except httpx2.ConnectError:
        local = vendor_of(preset, base_url) == "local"
        result.error = auth_error("local.not_running" if local else "auth.network", provider=preset.id,
                                  hint=" or check the address" if local else "", **values)
        return result
    except httpx2.HTTPError as e:
        result.error = auth_error("auth.network", provider=preset.id, detail=type(e).__name__, **values)
        return result
    finally:
        if own_client:
            client.close()

    result.status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    if 200 <= response.status_code < 300:
        result.ok = True
        result.account_label = _label(check, response, body)
        found = _dig(body, check.models_field)
        result.models = len(found) if isinstance(found, list) else None
        return result
    if 300 <= response.status_code < 400:
        result.error = auth_error("auth.unexpected", provider=preset.id, status=response.status_code,
                                  message=f"{display_origin(url)} redirected the check to another address; the key "
                                          "was not sent there. Check the address.")
        return result
    detail = _detail(response, body)
    error_body = body if isinstance(body, (dict, list)) else None
    inner = error_body.get("error") if isinstance(error_body, dict) else None
    found = classify(preset, response.status_code, detail, error_body, headers=response.headers, base_url=base_url,
                     error_type=inner.get("type") if isinstance(inner, dict) else None,
                     error_code=inner.get("code") if isinstance(inner, dict) else None, during="models")
    if found is not None:
        code, extra = found
        result.error = auth_error(code, provider=preset.id, status=response.status_code, detail=detail,
                                  **{**values, **extra})
        result.ok = code == "openai.no_model_list"  # the key works; only the list is not allowed
        return result
    result.error = status_error(response.status_code, detail, preset.label,
                                retry_after=parse_retry_after(response.headers), code=None,
                                kw={"provider": preset.id, "status": response.status_code})
    return result
