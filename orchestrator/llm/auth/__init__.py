"""
Sign-in core of the provider layer: which methods a provider offers (spec), where credentials live (store), what
a call uses (resolver), whether it works (validate), how tokens stay fresh (tokens) and what went wrong (errors).

    store = CredentialStore(framework.secrets_manager)
    resolver = CredentialResolver(store, setting=settings_lookup)
    spec = resolver.select(preset, base_url=url)
    credential = resolver.resolve(preset, spec, url)   # ResolvedCredential; repr shows no value

LLMClient builds both; the GUI and the CLI use LLMClient.credentials and LLMClient.check_credentials().
Names are imported on first use: the catalog loads spec.py without the vault behind store.py.
"""

import importlib

_EXPORTS = {
    "AuthAction": "errors", "AuthError": "errors", "auth_error": "errors",
    "CredentialResolver": "resolver", "ResolvedCredential": "resolver", "register_refresher": "resolver",
    "register_type": "resolver",
    "AuthMethodSpec": "spec", "FieldSpec": "spec", "OAuthSpec": "spec", "ValidateSpec": "spec",
    "CredentialStore": "store",
    "RefreshRejected": "tokens", "TokenProvider": "tokens",
    "ValidationResult": "validate", "validate_credential": "validate",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)
