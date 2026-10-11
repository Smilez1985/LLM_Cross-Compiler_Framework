"""Sign-in core (orchestrator/llm/auth): catalog schema v2 and v1, the credential store (records, metadata, origin
binding, environment sources), the resolver, the zero-cost credential check, the error table of the design's
section 4.6 and the token provider (refresh under the cross-process lock)."""

import json
import os
import pickle
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

httpx2 = pytest.importorskip("httpx2")
pytest.importorskip("anthropic")
pytest.importorskip("openai")

from orchestrator.Core.secrets_manager import SecretsManager, credential_name  # noqa: E402
from orchestrator.llm import LLMClient, load_catalog  # noqa: E402
from orchestrator.llm.auth import errors as auth_errors  # noqa: E402
from orchestrator.llm.auth.errors import AuthError, auth_error, classify  # noqa: E402
from orchestrator.llm.auth.resolver import CredentialResolver, ResolvedCredential, register_type  # noqa: E402
from orchestrator.llm.auth.spec import AuthMethodSpec, ValidateSpec  # noqa: E402
from orchestrator.llm.auth.store import METADATA_FILE, CredentialStore  # noqa: E402
from orchestrator.llm.auth.tokens import RefreshRejected, TokenProvider, needs_refresh  # noqa: E402
from orchestrator.llm.auth.validate import validate_credential  # noqa: E402
from orchestrator.llm.catalog import ProviderPreset  # noqa: E402
from orchestrator.llm.errors import (AuthFailed, BadRequest, BillingError, NotConfigured,  # noqa: E402
                                     PermissionDenied, ProviderUnavailable)
from test_llm import (FakeConfig, FakeHTTP, FakeSecrets, anthropic_error, ask, completion,  # noqa: E402
                      responses_body)

REPO_ROOT = Path(__file__).resolve().parents[1]
# Synthetic keys, built at run time so that the repository holds no literal that looks like a key.
CLAUDE_KEY = "sk-ant-" + "api03-" + "T" * 40
ADMIN_KEY = "sk-ant-" + "admin01-" + "Z" * 32
OPENAI_KEY = "sk-" + "proj-" + "O" * 40
LOCAL_KEY = "local-" + "k" * 20
WORKSPACE = "wrkspc_" + "01Store"
MODELS = {"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}


@pytest.fixture(autouse=True)
def fresh_keyring_probe():
    from orchestrator.Core import vault_key
    vault_key.reset_probe()
    yield
    vault_key.reset_probe()


@pytest.fixture
def catalog():
    return load_catalog()


@pytest.fixture
def vault(tmp_path, memory_keyring):
    sm = SecretsManager(tmp_path / "state")
    assert sm.initialize(), sm.storage_error
    return sm


@pytest.fixture
def store(vault):
    return CredentialStore(vault, environ={})


def lmstudio():
    """A local engine whose key is optional (schema v2, mapping form)."""
    return ProviderPreset(id="lmstudio", label="LM Studio (local)", adapter="openai_compat", location="local",
                          base_url="http://127.0.0.1:1234/v1", base_url_configurable=True, auth=[
                              {"id": "none", "type": "none"},
                              {"id": "token", "type": "bearer_optional",
                               "where": "Developer > Server Settings > Manage Tokens"}])


def client_for(tmp_path, http, secrets, **settings):
    return LLMClient(FakeConfig(**settings), secrets, state_dir=tmp_path / "client-state",
                     http_client=http.client() if http else None, sleep=lambda s: None)


# ======================================================================================================
# Catalog schema v2 (and v1)
# ======================================================================================================

class TestCatalogSchema:

    def test_bundled_catalog_is_v2_with_methods_in_preferred_order(self, catalog):
        raw = (REPO_ROOT / "configs" / "llm_providers.yml").read_text(encoding="utf-8")
        assert "\nversion: 2\n" in raw
        anthropic = catalog.get("anthropic")
        assert [(m.id, m.type) for m in anthropic.methods] == [("api_key", "api_key"), ("ant_profile", "cli_session")]
        key = anthropic.method("api_key")
        assert key.key_page == "https://platform.claude.com/settings/keys" and key.env == ["ANTHROPIC_API_KEY"]
        assert key.reject_prefixes == ["sk-ant-admin01-"] and key.validation.path == "/v1/models"
        workspace = key.field("workspace_id")
        assert workspace.header == "anthropic-workspace-id" and workspace.setting == "ai_anthropic_workspace_id"
        assert anthropic.method("ant_profile").opt_in and anthropic.method("oauth") is anthropic.method("ant_profile")
        assert catalog.get("openai").method("api_key").field("project").header == "OpenAI-Project"
        assert catalog.get("openrouter").method("api_key").validation.account_field == "data.label"
        assert [m.type for m in catalog.get("ollama").methods] == ["none"]
        assert [m.type for m in catalog.get("local").methods] == ["bearer_optional"]
        assert [m.id for m in catalog.get("custom").methods] == ["api_key", "none"]
        for preset in catalog.providers.values():
            for method in preset.methods:
                assert method.label, (preset.id, method.id)
                if method.type == "api_key" and not preset.base_url_configurable:
                    assert method.key_page and method.env and method.validation, (preset.id, method.id)

    def test_v1_name_lists_still_load(self, tmp_path):
        (tmp_path / "llm_providers.yml").write_text(
            "version: 1\nproviders:\n"
            "  anthropic:\n    label: Claude\n    adapter: anthropic\n    base_url: https://api.anthropic.com\n"
            "    auth: [api_key, oauth]\n    opt_in_auth: [oauth]\n    reject_key_prefixes: [sk-ant-admin01-]\n"
            "    secret: anthropic_api_key\n"
            "  box:\n    label: Box\n    adapter: openai_compat\n    base_url: http://box.local/v1\n"
            "    auth: [api_key, none]\n    secret: box_key\n", encoding="utf-8")
        cat = load_catalog(user_dir=tmp_path)
        anthropic = cat.get("anthropic")
        assert anthropic.auth == ["api_key", "ant_profile"] and anthropic.opt_in_auth == ["ant_profile"]
        assert anthropic.rejected_key(ADMIN_KEY) and anthropic.method("api_key").field("workspace_id") is not None
        box = cat.get("box")
        assert [(m.id, m.type) for m in box.methods] == [("api_key", "api_key"), ("none", "none")]
        assert box.method("api_key").fields == []  # the workspace field belongs to the Claude API only

    def test_a_preset_without_auth_takes_an_api_key(self):
        preset = ProviderPreset(id="x", label="X", adapter="openai_compat")
        assert [(m.id, m.type) for m in preset.methods] == [("api_key", "api_key")]

    @pytest.mark.parametrize("method, problem", [
        ({"id": "Key", "type": "api_key"}, "pattern"),
        ({"id": "k", "type": "magic"}, "type"),
        ({"id": "k", "type": "api_key", "env": ["lower_case"]}, "environment variable"),
        ({"id": "k", "type": "api_key", "key_page": "http://example.com/keys"}, "https"),
        ({"id": "k", "type": "api_key", "key_page": "https://" + "user" + chr(64) + "x.example/keys"}, "https"),
        ({"id": "k", "type": "api_key", "validate": {"path": "models"}}, "path"),
        ({"id": "k", "type": "api_key", "validate": {"path": "//evil.example/x"}}, "path"),
        ({"id": "k", "type": "api_key", "validate": {"method": "POST", "path": "/v1/messages"}}, "GET"),
        ({"id": "k", "type": "api_key", "validate": {"path": "/a", "url": "https://x.example/b"}}, "exactly one"),
        ({"id": "k", "type": "api_key", "fields": [{"id": "origin", "label": "O"}]}, "reserved"),
        ({"id": "k", "type": "api_key", "fields": [{"id": "w", "label": "W", "header": "bad header"}]}, "header"),
        ({"id": "k", "type": "oauth_pkce_loopback"}, "oauth block"),
        ({"id": "k", "type": "none", "env": ["SOME_KEY"]}, "key methods"),
        ({"id": "k", "type": "api_key", "unknown": 1}, "extra"),
    ])
    def test_malformed_methods_are_refused(self, method, problem):
        with pytest.raises(ValueError) as info:
            ProviderPreset(id="x", label="X", adapter="openai_compat", auth=[method])
        assert problem.lower() in str(info.value).lower()

    def test_schema_2_presets_get_a_key_name_and_the_claude_api_refuses_admin_keys(self):
        preset = ProviderPreset(id="together", label="Together", adapter="openai_compat",
                                base_url="https://api.together.ai/v1",
                                auth=[{"id": "api_key", "type": "api_key", "env": ["TOGETHER_API_KEY"]}])
        assert preset.secret == "cred/together/api_key"
        assert ProviderPreset(id="local2", label="L", adapter="openai_compat", runtime_endpoint=True,
                              auth=[{"id": "runtime_key", "type": "bearer_optional"}]).secret is None
        claude = ProviderPreset(id="claude", label="Claude", adapter="anthropic", base_url="https://api.anthropic.com")
        assert claude.rejected_key(ADMIN_KEY) and claude.method("api_key").field("workspace_id") is not None

    def test_duplicate_methods_and_unknown_schema_versions_are_refused(self, tmp_path):
        with pytest.raises(ValueError, match="twice"):
            ProviderPreset(id="x", label="X", adapter="openai_compat", auth=["api_key", {"id": "api_key",
                                                                                       "type": "api_key"}])
        path = tmp_path / "catalog.yml"
        path.write_text("version: 3\nproviders: {}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="schema version"):
            load_catalog(path)

    def test_every_catalog_key_variable_is_kept_out_of_the_tests(self, catalog):
        names = {name for preset in catalog.providers.values() for name in preset.env_names}
        assert names >= {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"}
        assert not names & set(os.environ), "tests/conftest.py must remove the catalog's key variables"


# ======================================================================================================
# Credential store
# ======================================================================================================

class TestCredentialStore:

    def test_api_key_record_and_metadata_are_kept_apart(self, store, vault, catalog):
        anthropic = catalog.get("anthropic")
        meta = store.save_api_key(anthropic, anthropic.method("api_key"), f"  {CLAUDE_KEY}\n",
                                  fields={"workspace_id": f" {WORKSPACE} "})
        assert vault.get_record("cred/anthropic/api_key") == {"v": 1, "kind": "api_key", "secret": CLAUDE_KEY}
        assert meta["last4"] == CLAUDE_KEY[-4:] and meta["workspace_id"] == WORKSPACE and meta["source"] == "manual"
        path = vault.secrets_file.with_name(METADATA_FILE)
        text = path.read_text(encoding="utf-8")
        assert json.loads(text)["auth"]["anthropic"]["api_key"]["workspace_id"] == WORKSPACE
        assert CLAUDE_KEY not in text and CLAUDE_KEY[:-4] not in text
        if os.name == "posix":
            assert path.stat().st_mode & 0o777 == 0o600
        assert vault.get_secret("anthropic_api_key") == CLAUDE_KEY  # the name of earlier versions still works

    def test_admin_keys_and_malformed_fields_are_refused_before_anything_is_written(self, store, vault, catalog):
        anthropic = catalog.get("anthropic")
        with pytest.raises(AuthError) as info:
            store.save_api_key(anthropic, anthropic.method("api_key"), ADMIN_KEY)
        assert info.value.code == "anthropic.admin_key" and isinstance(info.value, NotConfigured)
        with pytest.raises(AuthError) as info:
            store.save_api_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY, fields={"workspace_id": "ws 1"})
        assert info.value.code == "anthropic.workspace_invalid"
        with pytest.raises(ValueError):
            store.save_api_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY, fields={"colour": "red"})
        assert vault.list_secrets() == [] and store.all_metadata() == {}

    def test_metadata_never_holds_a_secret(self, store):
        store.update_metadata("openrouter", "api_key", {"account_label": "label " + OPENAI_KEY})
        assert OPENAI_KEY not in json.dumps(store.all_metadata())

    def test_keys_of_a_changeable_address_are_bound_to_its_origin(self, store, vault, catalog):
        custom, = [catalog.get("custom")]
        spec = custom.method("api_key")
        store.save_api_key(custom, spec, LOCAL_KEY, base_url="http://10.0.0.5:8000/v1")
        bound = credential_name("custom", "api_key", "http://10.0.0.5:8000")
        assert vault.list_secrets() == [bound]
        assert store.find_secret(custom, spec, "http://10.0.0.5:8000/other").value.get_secret_value() == LOCAL_KEY
        for other in ("http://10.0.0.6:8000/v1", "https://10.0.0.5:8000/v1", "http://10.0.0.5:8001/v1",
                      "http://evil.example/v1", None):
            assert store.find_secret(custom, spec, other) is None, other
        assert store.metadata("custom", "api_key", "http://10.0.0.5:8000")["origin"] == "http://10.0.0.5:8000"

    def test_a_key_stored_without_an_origin_is_bound_on_first_use(self, store, vault, catalog):
        custom = catalog.get("custom")
        spec = custom.method("api_key")
        assert vault.set_secret("custom_api_key", LOCAL_KEY)  # earlier versions, `llm-cli secrets set`
        found = store.find_secret(custom, spec, "http://127.0.0.1:8080/v1")
        assert found.value.get_secret_value() == LOCAL_KEY and found.source == "vault"
        assert vault.list_secrets() == [credential_name("custom", "api_key", "http://127.0.0.1:8080")]
        assert store.find_secret(custom, spec, "http://127.0.0.1:9090/v1") is None  # never carried over
        assert store.metadata("custom", "api_key", "http://127.0.0.1:8080")["source"] == "migrated"

    def test_a_newly_saved_key_is_not_replaced_by_a_leftover(self, store, vault, catalog):
        custom = catalog.get("custom")
        spec = custom.method("api_key")
        assert vault.set_secret("custom_api_key", "old-" + "k" * 20)
        store.save_api_key(custom, spec, LOCAL_KEY, base_url="http://127.0.0.1:8080/v1")
        assert store.find_secret(custom, spec, "http://127.0.0.1:8080/v1").value.get_secret_value() == LOCAL_KEY

    def test_a_key_that_cannot_be_bound_is_not_sent(self, store, vault, catalog, monkeypatch):
        custom = catalog.get("custom")
        assert vault.set_secret("custom_api_key", LOCAL_KEY)
        monkeypatch.setattr(vault, "set_record", lambda name, record: False)
        assert store.find_secret(custom, custom.method("api_key"), "http://127.0.0.1:8080/v1") is None

    def test_environment_variables_only_as_listed_and_only_when_nothing_is_stored(self, vault, catalog):
        anthropic, custom = catalog.get("anthropic"), catalog.get("custom")
        env = {"ANTHROPIC_API_KEY": f" {CLAUDE_KEY} ", "OPENAI_API_KEY": OPENAI_KEY, "CUSTOM_API_KEY": LOCAL_KEY}
        store = CredentialStore(vault, environ=env)
        found = store.find_secret(anthropic, anthropic.method("api_key"))
        assert found.source == "env:ANTHROPIC_API_KEY" and found.value.get_secret_value() == CLAUDE_KEY
        assert store.find_secret(custom, custom.method("api_key"), "http://127.0.0.1:1/v1") is None
        assert vault.list_secrets() == []  # never written to the vault
        assert vault.set_secret("anthropic_api_key", "sk-ant-" + "api03-" + "S" * 40)
        assert store.find_secret(anthropic, anthropic.method("api_key")).source == "vault"
        assert CLAUDE_KEY not in repr(found)

    def test_metadata_stays_in_memory_for_an_ephemeral_vault_and_inside_a_git_tree(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_SECRET_STORAGE", "ephemeral")
        sm = SecretsManager(tmp_path / "state")
        assert sm.initialize()
        store = CredentialStore(sm)
        store.update_metadata("openai", "api_key", {"account_label": "team"})
        assert store.metadata("openai", "api_key") == {"account_label": "team"}
        assert not list((tmp_path / "state").glob(METADATA_FILE))
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        in_repo = CredentialStore(FakeSecrets(), state_dir=repo / "state")
        in_repo.update_metadata("openai", "api_key", {"account_label": "team"})
        assert in_repo.metadata_path() is None and not (repo / "state").exists()

    def test_delete_signs_out_locally(self, store, vault, catalog):
        openai = catalog.get("openai")
        store.save_api_key(openai, openai.method("api_key"), OPENAI_KEY, fields={"project": "proj_" + "abc"})
        assert store.delete_key(openai, openai.method("api_key")) is True
        assert vault.list_secrets() == [] and store.metadata("openai", "api_key") == {}
        assert store.delete_key(openai, openai.method("api_key")) is False

    def test_an_odd_provider_id_leaves_only_the_environment(self, vault):
        preset = ProviderPreset(id="My Server", label="Mine", adapter="openai_compat", base_url="https://x.example/v1",
                                auth=[{"id": "api_key", "type": "api_key", "env": ["MINE_API_KEY"]}])
        store = CredentialStore(vault, environ={"MINE_API_KEY": LOCAL_KEY})
        assert store.find_secret(preset, preset.method("api_key")).source == "env:MINE_API_KEY"
        with pytest.raises(AuthError, match="lower-case"):
            store.save_api_key(preset, preset.method("api_key"), LOCAL_KEY)

    def test_the_earlier_key_name_belongs_to_the_first_key_method(self, catalog):
        preset = ProviderPreset(id="box", label="Box", adapter="openai_compat", base_url="https://box.example/v1",
                                secret="box_key", auth=[{"id": "api_key", "type": "api_key"},
                                                        {"id": "second", "type": "api_key"}])
        store = CredentialStore(FakeSecrets({"box_key": LOCAL_KEY}))
        assert store.find_secret(preset, preset.method("api_key")).value.get_secret_value() == LOCAL_KEY
        assert store.find_secret(preset, preset.method("second")) is None

    def test_read_only_stand_ins_still_answer(self, catalog):
        # The AI dialog's overlay of an unsaved key and the test fakes have only get_secret.
        custom = catalog.get("custom")
        store = CredentialStore(FakeSecrets({"custom_api_key": LOCAL_KEY}))
        found = store.find_secret(custom, custom.method("api_key"), "http://127.0.0.1:8080/v1")
        assert found.value.get_secret_value() == LOCAL_KEY and not store.records_supported


# ======================================================================================================
# Resolver
# ======================================================================================================

class TestResolver:

    def resolver(self, store, **settings):
        return CredentialResolver(store, setting=lambda key: settings.get(key))

    def test_kind_per_method(self, store, vault, catalog, monkeypatch):
        resolver = self.resolver(store)
        anthropic, ollama, studio = catalog.get("anthropic"), catalog.get("ollama"), lmstudio()
        assert resolver.resolve(ollama, "none").kind == "none"
        assert resolver.resolve(studio, "token", studio.base_url).kind == "none"  # the key is optional
        store.save_api_key(studio, studio.method("token"), LOCAL_KEY, base_url=studio.base_url)
        token = resolver.resolve(studio, "token", studio.base_url)
        assert token.kind == "api_key" and token.origin == "http://127.0.0.1:1234" and token.source == "vault"
        assert resolver.resolve(studio, "token", "http://127.0.0.1:4321/v1").kind == "none"
        with pytest.raises(NotConfigured) as info:
            resolver.resolve(anthropic, "api_key")
        assert info.value.code == "auth.not_signed_in" and "ANTHROPIC_API_KEY" in str(info.value)
        assert any(a.url == "https://platform.claude.com/settings/keys" for a in info.value.actions)
        store.save_api_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY)
        key = resolver.resolve(anthropic, "api_key")
        assert key.kind == "api_key" and key.api_key.get_secret_value() == CLAUDE_KEY
        profile = self.resolver(store, ai_oauth_profile="work").resolve(anthropic, "ant_profile")
        assert profile.kind == "anthropic_profile" and profile.profile == "work" and profile.api_key is None
        runtime = CredentialResolver(store, runtime_key=lambda preset: LOCAL_KEY).resolve(
            catalog.get("local"), "runtime_key", "http://127.0.0.1:8099/v1")
        assert runtime.kind == "api_key" and runtime.source == "runtime"

    def test_oauth_records_and_types_without_a_flow_yet(self, store, vault):
        preset = ProviderPreset(id="demo", label="Demo", adapter="openai_compat", base_url="https://demo.example/v1",
                                auth=[{"id": "oauth", "type": "oauth_pkce_loopback",
                                       "oauth": {"authorize_url": "https://demo.example/auth", "result": "api_key"}},
                                      {"id": "chain", "type": "cloud_credential_chain"}])
        resolver = self.resolver(store)
        with pytest.raises(NotConfigured) as info:
            resolver.resolve(preset, "oauth")
        assert info.value.code == "auth.not_signed_in"
        store.save_record("demo", "oauth", {"kind": "api_key", "secret": "demo-" + "r" * 20})
        assert resolver.resolve(preset, "oauth").kind == "api_key"  # an OAuth result that is an API key
        store.save_record("demo", "oauth", {"kind": "oauth", "access_token": "a1", "refresh_token": "r1"})
        with pytest.raises(AuthError) as info:
            resolver.resolve(preset, "oauth")
        assert info.value.code == "auth.not_built" and info.value.exit_code == 6
        resolver.refreshers[("demo", "oauth")] = lambda record: {"access_token": "a2"}
        tokens = resolver.resolve(preset, "oauth")
        assert tokens.kind == "token_callable" and tokens.token_provider() == "a1"
        with pytest.raises(AuthError) as info:
            resolver.resolve(preset, "chain")
        assert info.value.code == "auth.not_built"
        assert resolver.select(preset).id == "oauth" and not resolver.available(preset, preset.method("chain"))

    def test_registered_types_are_used(self, store, monkeypatch):
        monkeypatch.setattr("orchestrator.llm.auth.resolver.TYPE_HANDLERS", {})
        register_type("cloud_credential_chain",
                      lambda resolver, preset, spec, url: ResolvedCredential(kind="aws", method=spec.id,
                                                                             type=spec.type, region="eu-central-1"),
                      lambda *args: True)
        preset = ProviderPreset(id="cloud", label="Cloud", adapter="openai_compat",
                                auth=[{"id": "chain", "type": "cloud_credential_chain"}])
        resolver = self.resolver(store)
        assert resolver.select(preset).id == "chain"
        assert resolver.resolve(preset, "chain").region == "eu-central-1"

    def test_selection_follows_the_order_and_never_picks_opt_in(self, store, catalog):
        anthropic, custom = catalog.get("anthropic"), catalog.get("custom")
        resolver = self.resolver(store)
        with pytest.raises(NotConfigured) as info:
            resolver.select(anthropic)
        assert "ai_auth_anthropic" in str(info.value)
        assert resolver.select(anthropic, chosen="ant_profile", explicit=True).id == "ant_profile"
        with pytest.raises(NotConfigured, match="ai_auth_anthropic"):
            resolver.select(anthropic, chosen="ant_profile")
        assert resolver.select(custom, base_url="http://127.0.0.1:1/v1").id == "none"
        store.save_api_key(custom, custom.method("api_key"), LOCAL_KEY, base_url="http://127.0.0.1:1/v1")
        assert resolver.select(custom, base_url="http://127.0.0.1:1/v1").id == "api_key"
        assert resolver.select(custom, base_url="http://127.0.0.1:2/v1").id == "none"

    def test_fields_come_from_the_setting_or_the_metadata(self, store, catalog):
        anthropic = catalog.get("anthropic")
        store.save_api_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY, fields={"workspace_id": WORKSPACE})
        assert self.resolver(store).resolve(anthropic, "api_key").headers == {"anthropic-workspace-id": WORKSPACE}
        other = "wrkspc_" + "02Setting"
        cred = self.resolver(store, ai_anthropic_workspace_id=other).resolve(anthropic, "api_key")
        assert cred.workspace_id == other
        with pytest.raises(NotConfigured) as info:
            self.resolver(store, ai_anthropic_workspace_id="wrkspc_\nX").resolve(anthropic, "api_key")
        assert info.value.code == "anthropic.workspace_invalid"

    def test_the_resolved_credential_shows_no_value(self, store, catalog):
        anthropic = catalog.get("anthropic")
        store.save_api_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY, fields={"workspace_id": WORKSPACE})
        cred = self.resolver(store).resolve(anthropic, "api_key")
        for shown in (repr(cred), str(cred), json.dumps(cred.describe()), f"{cred}", cred.fingerprint):
            assert CLAUDE_KEY not in shown and CLAUDE_KEY[-12:] not in shown
        assert cred.describe()["source"] == "vault" and cred.describe()["workspace_id"] == WORKSPACE
        with pytest.raises(TypeError):
            pickle.dumps(cred)
        with pytest.raises(Exception):
            cred.kind = "none"
        with pytest.raises(TypeError):
            cred.headers["x"] = "y"


# ======================================================================================================
# LLMClient uses the resolver
# ======================================================================================================

class TestClientUsesTheResolver:

    def test_openai_project_from_the_metadata_reaches_the_request(self, tmp_path, vault, catalog):
        openai = catalog.get("openai")
        project = "proj_" + "Team42"
        CredentialStore(vault).save_api_key(openai, openai.method("api_key"), OPENAI_KEY, fields={"project": project})
        final = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "ok", "annotations": []}]}
        http = FakeHTTP().add(responses_body([final]))
        client_for(tmp_path, http, vault, ai_provider="openai").chat(ask())
        assert http.last.headers["openai-project"] == project
        assert http.last.headers["authorization"] == f"Bearer {OPENAI_KEY}"
        assert "openai-organization" not in http.last.headers

    def test_a_custom_key_never_reaches_a_new_address(self, tmp_path, vault, catalog):
        custom = catalog.get("custom")
        CredentialStore(vault).save_api_key(custom, custom.method("api_key"), LOCAL_KEY,
                                            base_url="http://127.0.0.1:8080/v1")
        http = FakeHTTP().add(completion("ok")).add(completion("ok"))
        client_for(tmp_path, http, vault, ai_provider="custom", ai_base_url="http://127.0.0.1:8080/v1",
                   ai_model="m").chat(ask())
        assert http.last.headers["authorization"] == f"Bearer {LOCAL_KEY}"
        moved = client_for(tmp_path, http, vault, ai_provider="custom", ai_base_url="http://192.168.1.9:8080/v1",
                           ai_model="m")
        assert moved.describe()["auth"] == "none"
        moved.chat(ask())
        assert http.last.headers["authorization"] == "Bearer not-needed"

    def test_check_credentials_stores_when_and_as_whom(self, tmp_path, vault, catalog):
        anthropic = catalog.get("anthropic")
        CredentialStore(vault).save_api_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY)
        http = FakeHTTP().add(MODELS, headers={"anthropic-workspace-id": WORKSPACE})
        result = client_for(tmp_path, http, vault, ai_provider="anthropic").check_credentials()
        assert result.ok and result.models == 3 and result.account_label == f"workspace {WORKSPACE}"
        assert result.message == f"Valid, workspace {WORKSPACE}, 3 models"
        assert http.last.method == "GET" and http.last.path == "/v1/models"
        assert http.last.headers["x-api-key"] == CLAUDE_KEY and http.last.headers["anthropic-version"]
        meta = CredentialStore(vault).metadata("anthropic", "api_key")
        assert meta["account_label"] == f"workspace {WORKSPACE}" and meta["validated_at"]
        assert CLAUDE_KEY not in json.dumps(result.to_dict())


# ======================================================================================================
# Zero-cost check
# ======================================================================================================

class TestValidate:

    def check(self, preset, method, credential, base_url, http):
        return validate_credential(preset, preset.method(method), credential, base_url, http_client=http.client())

    def key(self, method="api_key", value=OPENAI_KEY, **kw):
        return ResolvedCredential(kind="api_key", method=method, type="api_key", api_key=value, source="vault", **kw)

    def test_openrouter_names_the_key(self, catalog):
        http = FakeHTTP().add({"data": {"label": "laptop", "limit": None}})
        result = self.check(catalog.get("openrouter"), "api_key", self.key(), "https://openrouter.ai/api/v1", http)
        assert result.ok and result.account_label == "laptop" and result.models is None
        assert http.last.url == "https://openrouter.ai/api/v1/key" and http.last.method == "GET"
        assert http.last.headers["authorization"] == f"Bearer {OPENAI_KEY}"
        assert http.last.headers["x-openrouter-title"] == "LLM Cross-Compiler Framework"

    def test_a_key_that_may_not_list_models_is_still_valid(self, catalog):
        http = FakeHTTP().add({"error": {"message": "Missing scopes: api.model.read"}}, status=403)
        result = self.check(catalog.get("openai"), "api_key", self.key(), "https://api.openai.com/v1", http)
        assert result.ok and result.error.code == "openai.no_model_list" and "Type a model id" in result.message

    def test_admin_keys_never_reach_the_network(self, catalog):
        http = FakeHTTP()
        result = self.check(catalog.get("anthropic"), "api_key", self.key(value=f" {ADMIN_KEY}"),
                            "https://api.anthropic.com", http)
        assert not result.ok and result.error.code == "anthropic.admin_key" and http.requests == []

    def test_redirects_are_not_followed(self, catalog):
        http = FakeHTTP().add({}, status=302, headers={"location": "https://collector.example/steal"})
        result = self.check(catalog.get("openai"), "api_key", self.key(), "https://api.openai.com/v1", http)
        assert not result.ok and len(http.requests) == 1 and "redirected" in result.message

    def test_a_server_that_is_not_running(self, catalog):
        def refuse(request):
            raise httpx2.ConnectError("connection refused", request=request)
        client = httpx2.Client(transport=httpx2.MockTransport(refuse))
        ollama = catalog.get("ollama")
        result = validate_credential(ollama, ollama.method("none"), ResolvedCredential(kind="none", method="none",
                                                                                    type="none"),
                                     "http://localhost:11434/v1", http_client=client)
        assert result.error.code == "local.not_running" and result.error.exit_code == 4
        assert "http://localhost:11434" in result.message and "Start Ollama" in result.message

    def test_a_wrong_path_and_a_public_endpoint(self):
        preset = ProviderPreset(id="box", label="Box", adapter="openai_compat", location="local",
                                base_url="http://127.0.0.1:5000", auth=[
                                    {"id": "none", "type": "none", "validate": {"path": "/models", "public": True}}])
        http = FakeHTTP().add({"detail": "Not Found"}, status=404).add(MODELS)
        cred = ResolvedCredential(kind="none", method="none", type="none")
        assert self.check(preset, "none", cred, preset.base_url, http).error.code == "local.wrong_path"
        result = self.check(preset, "none", cred, preset.base_url, http)
        assert result.ok and not result.key_checked and result.models == 3

    def test_profile_sessions_are_checked_through_the_sdk(self, catalog):
        anthropic = catalog.get("anthropic")
        cred = ResolvedCredential(kind="anthropic_profile", method="ant_profile", type="cli_session")
        result = validate_credential(anthropic, anthropic.method("ant_profile"), cred, anthropic.base_url,
                                     list_models=lambda: ["a", "b"])
        assert result.ok and result.models == 2 and result.checked == "model list through the SDK"

    def test_default_check_for_presets_without_one(self):
        preset = ProviderPreset(id="gw", label="Gateway", adapter="anthropic", base_url="https://gw.example")
        assert preset.method("api_key").validation is None
        http = FakeHTTP().add(MODELS)
        result = self.check(preset, "api_key", self.key(), preset.base_url, http)
        assert result.ok and http.last.url == "https://gw.example/v1/models"


# ======================================================================================================
# The error table (design section 4.6), one case per row
# ======================================================================================================

def chat_error(tmp_path, provider, status, body, *, headers=None, secrets=None, **settings):
    http = FakeHTTP().add(body, status=status, headers=headers or {})
    secrets = FakeSecrets(secrets or {"anthropic_api_key": CLAUDE_KEY, "openai_api_key": OPENAI_KEY,
                                      "gemini_api_key": "AIza" + "g" * 35,
                                      "openrouter_api_key": "sk-or-" + "v1-" + "r" * 40})
    with pytest.raises(Exception) as info:
        client_for(tmp_path, http, secrets, ai_provider=provider, **settings).chat(ask())
    return info.value


ANTHROPIC_ERROR_ROWS = [
    ("anthropic.workspace_required", 400, "invalid_request_error", "anthropic-workspace-id is required for this key",
     NotConfigured),
    ("anthropic.workspace_invalid", 400, "invalid_request_error",
     "anthropic-workspace-id header must be a valid workspace ID.", NotConfigured),
    ("anthropic.workspace_not_found", 404, "not_found_error", "Workspace `wrkspc_gone` not found.", NotConfigured),
    ("anthropic.auth", 401, "authentication_error", "invalid x-api-key", AuthFailed),
    ("anthropic.billing", 402, "billing_error", "Your organization has a billing problem.", BillingError),
    ("anthropic.billing", 400, "invalid_request_error", "Your credit balance is too low.", BillingError),
    ("anthropic.spend_limit", 400, "invalid_request_error", "You have reached your workspace spend limit.",
     BillingError),
    ("anthropic.spend_limit", 429, "rate_limit_error", "You have reached your monthly spend cap.", BillingError),
]


class TestErrorTable:

    @pytest.mark.parametrize("code, status, kind, message, cls", ANTHROPIC_ERROR_ROWS)
    def test_claude_rows(self, tmp_path, code, status, kind, message, cls):
        err = chat_error(tmp_path, "anthropic", status, anthropic_error(status, kind, message)[1])
        assert isinstance(err, AuthError) and isinstance(err, cls) and err.code == code, (code, err)
        assert str(err) and auth_errors.ERRORS[code].message.split("{")[0][:20] in str(err)
        assert err.actions and err.exit_code == 3 and CLAUDE_KEY not in str(err)

    def test_a_rate_limit_with_retry_after_is_not_a_spend_cap(self, tmp_path):
        http = FakeHTTP()
        for _ in range(3):
            http.add(anthropic_error(429, "rate_limit_error", "spend cap?")[1], status=429,
                     headers={"retry-after": "1"})
        with pytest.raises(Exception) as info:
            client_for(tmp_path, http, FakeSecrets({"anthropic_api_key": CLAUDE_KEY}),
                       ai_provider="anthropic").chat(ask())
        assert info.value.code is None and len(http.requests) == 3  # retried as a rate limit

    def test_profile_row(self, tmp_path, monkeypatch):
        (tmp_path / "ant").mkdir()
        monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", str(tmp_path / "ant"))
        err = chat_error(tmp_path, "anthropic", 200, {}, ai_auth_anthropic="ant_profile",
                         ai_oauth_profile="missing")
        assert err.code == "anthropic.profile" and "'missing'" in str(err) and "ant auth login" in str(err)
        assert isinstance(err, NotConfigured) and err.actions[0].command == "ant auth login"

    @pytest.mark.parametrize("code, status, body, cls", [
        ("openai.auth", 401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}},
         AuthFailed),
        ("openai.quota", 429, {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota",
                                         "code": "insufficient_quota"}}, BillingError),
        ("openai.admin_key", 401, {"error": {"message": "Admin keys cannot be used for this endpoint."}},
         NotConfigured),
    ])
    def test_openai_rows(self, tmp_path, code, status, body, cls):
        err = chat_error(tmp_path, "openai", status, body)
        assert isinstance(err, AuthError) and isinstance(err, cls) and err.code == code
        assert err.actions and OPENAI_KEY not in str(err)

    def test_openai_model_list_row(self, catalog):
        found = classify(catalog.get("openai"), 403, "Missing scopes", during="models")
        assert found == ("openai.no_model_list", {})
        assert classify(catalog.get("openai"), 403, "Missing scopes") is None  # a refused request stays generic

    @pytest.mark.parametrize("status, body", [
        (400, [{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                          "status": "INVALID_ARGUMENT"}}]),
        (403, {"error": {"message": "Method doesn't allow unregistered callers"}}),
    ])
    def test_gemini_row(self, tmp_path, status, body):
        err = chat_error(tmp_path, "google", status, body)
        assert err.code == "gemini.key_rejected" and isinstance(err, AuthFailed)
        assert err.actions[0].url == "https://aistudio.google.com/apikey"

    def test_openrouter_key_limit_row(self, tmp_path):
        body = {"error": {"code": 402, "message": "Key limit exceeded",
                          "metadata": {"limit_source": "openrouter_key_limit"}}}
        err = chat_error(tmp_path, "openrouter", 402, body, ai_model="m")
        assert err.code == "openrouter.key_limit" and isinstance(err, BillingError)
        other = chat_error(tmp_path, "openrouter", 402, {"error": {"code": 402, "message": "Add credits",
                                                                   "metadata": {"limit_source": "openrouter_credits"}}},
                           ai_model="m")
        assert other.code is None and isinstance(other, BillingError)

    def test_local_rows(self, tmp_path):
        def refuse(request):
            raise httpx2.ConnectError("connection refused", request=request)
        client = LLMClient(FakeConfig(ai_provider="ollama", ai_model="m"), FakeSecrets(), state_dir=tmp_path / "s",
                           http_client=httpx2.Client(transport=httpx2.MockTransport(refuse)), sleep=lambda s: None)
        with pytest.raises(ProviderUnavailable) as info:
            client.chat(ask())
        err = info.value
        assert err.code == "local.not_running" and err.exit_code == 4 and "http://localhost:11434" in str(err)

        err = chat_error(tmp_path, "custom", 401, {"error": "unauthorized"}, ai_base_url="http://127.0.0.1:8080/v1",
                         ai_model="m")
        assert err.code == "local.key_needed" and isinstance(err, AuthFailed)
        signin = "https://ollama.com/connect?name=" + "box"
        err = chat_error(tmp_path, "ollama", 401, {"error": "unauthorized", "signin_url": signin}, ai_model="m")
        assert err.code == "local.ollama_signin" and err.actions[0].url == signin
        assert err.actions[0].command == "ollama signin"
        err = chat_error(tmp_path, "ollama", 401, {"error": "unauthorized", "signin_url": "https://evil.example/x"},
                         ai_model="m")
        assert err.code == "local.ollama_signin" and err.actions[0].url is None  # only ollama.com links

    def test_storage_rows(self, tmp_path, memory_keyring, catalog, monkeypatch):
        anthropic = catalog.get("anthropic")
        damaged = tmp_path / "damaged"
        damaged.mkdir()
        (damaged / "secrets.store").write_text("{not json", encoding="utf-8")
        with pytest.raises(AuthError) as info:
            CredentialStore(SecretsManager(damaged)).save_api_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY)
        assert info.value.code == "storage.vault_unreadable" and info.value.exit_code == 5
        assert (damaged / "secrets.store").read_text(encoding="utf-8") == "{not json"

        def locked(*args):
            raise RuntimeError("locked")

        monkeypatch.setattr(memory_keyring, "get_password", locked)
        monkeypatch.setattr(memory_keyring, "set_password", locked)
        from orchestrator.Core import vault_key
        vault_key.reset_probe()  # the keyring answered for the first vault; it is locked now
        with pytest.raises(AuthError) as info:
            CredentialStore(SecretsManager(tmp_path / "state")).save_api_key(anthropic, anthropic.method("api_key"),
                                                                             CLAUDE_KEY)
        assert info.value.code == "storage.keyring_unavailable" and info.value.exit_code == 5
        assert "Choose how to protect" in str(info.value) and not (tmp_path / "state" / "secrets.store").exists()

    def test_every_row_has_a_message_and_errors_render_without_secrets(self):
        for code, spec in auth_errors.ERRORS.items():
            err = auth_error(code, provider_label="Demo", detail="token " + OPENAI_KEY)
            assert str(err) and OPENAI_KEY not in str(err) and err.code == code
            assert isinstance(err.to_dict()["exit_code"], int) and "{" not in str(err)
            assert all(a.label for a in err.actions)
        assert isinstance(auth_error("anthropic.workspace_invalid"), NotConfigured)
        assert isinstance(auth_error("openai.no_model_list"), PermissionDenied)
        assert isinstance(auth_error("local.wrong_path"), BadRequest)


# ======================================================================================================
# Token provider
# ======================================================================================================

def seed_oauth(store, *, expires_at, refresh_token="refresh-1", earliest=None):
    record = {"kind": "oauth", "access_token": "access-1", "refresh_token": refresh_token, "expires_at": expires_at}
    if earliest is not None:
        record["earliest_refresh_at"] = earliest
    store.save_record("demo", "oauth", record)


class TestTokenProvider:

    def test_fresh_tokens_are_used_as_they_are(self, store):
        seed_oauth(store, expires_at=int(time.time()) + 3600)
        provider = TokenProvider(store, "demo", "oauth", lambda record: pytest.fail("no refresh expected"))
        assert provider() == "access-1" and provider.get() == "access-1"

    def test_refresh_is_stored_before_the_token_is_used(self, store, vault):
        seed_oauth(store, expires_at=int(time.time()) + 60)
        events = []
        real_save = store.save_record

        def save(*args, **kwargs):
            events.append("save")
            return real_save(*args, **kwargs)

        def refresher(record):
            events.append(f"refresh with {record['refresh_token']}")
            return {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}

        store.save_record = save
        token = TokenProvider(store, "demo", "oauth", refresher).get()
        events.append(f"use {token}")
        assert events == ["refresh with refresh-1", "save", "use access-2"]
        record = vault.get_record("cred/demo/oauth")
        assert record["refresh_token"] == "refresh-2" and record["expires_at"] > time.time() + 3000
        assert store.metadata("demo", "oauth")["expires_at"]

    def test_a_token_that_cannot_be_stored_is_not_used(self, store, vault, monkeypatch):
        seed_oauth(store, expires_at=int(time.time()) - 10)
        monkeypatch.setattr(vault, "set_record", lambda name, record: False)
        provider = TokenProvider(store, "demo", "oauth", lambda record: {"access_token": "access-2",
                                                                         "refresh_token": "refresh-2"})
        with pytest.raises(AuthError) as info:
            provider.get()
        assert info.value.exit_code == 5 and "access-2" not in str(info.value)

    def test_an_unrotated_refresh_token_is_kept(self, store, vault):
        seed_oauth(store, expires_at=int(time.time()) - 10)
        TokenProvider(store, "demo", "oauth", lambda record: {"access_token": "access-2", "expires_at": 1}).get()
        assert vault.get_record("cred/demo/oauth")["refresh_token"] == "refresh-1"

    def test_invalid_grant_marks_the_credential_signed_out(self, store, vault):
        seed_oauth(store, expires_at=int(time.time()) - 10)
        calls = []

        def refresher(record):
            calls.append(1)
            raise RefreshRejected("invalid_grant", detail="Refresh token already used: refresh-1")

        provider = TokenProvider(store, "demo", "oauth", refresher)
        for _ in range(2):
            with pytest.raises(AuthError) as info:
                provider.get()
            assert info.value.code == "auth.signed_out" and isinstance(info.value, NotConfigured)
        assert calls == [1], "no retry loop"
        assert store.is_signed_out("demo", "oauth") and vault.get_record("cred/demo/oauth") is None
        assert store.metadata("demo", "oauth")["signed_out_reason"] == "invalid_grant"
        preset = ProviderPreset(id="demo", label="Demo", adapter="openai_compat", auth=[
            {"id": "oauth", "type": "oauth_device_code", "oauth": {"device_url": "https://demo.example/device"}}])
        resolver = CredentialResolver(store)
        resolver.refreshers[("demo", "oauth")] = refresher
        assert not resolver.available(preset, preset.method("oauth"))
        seed_oauth(store, expires_at=int(time.time()) + 3600)
        store.update_metadata("demo", "oauth", {"state": None})  # a new sign-in clears the state
        assert resolver.available(preset, preset.method("oauth"))

    def test_vendor_codes_and_earliest_refresh(self, store):
        now = time.time()
        assert not needs_refresh({"access_token": "a", "expires_at": now + 100, "earliest_refresh_at": now + 50}, now)
        assert needs_refresh({"access_token": "a", "expires_at": now + 100, "earliest_refresh_at": now - 1}, now)
        assert needs_refresh({"access_token": "a", "expires_at": now - 1, "earliest_refresh_at": now + 50}, now)
        assert not needs_refresh({"access_token": "a"}, now)
        seed_oauth(store, expires_at=int(now) - 10)
        with pytest.raises(AuthError) as info:
            TokenProvider(store, "demo", "oauth", lambda record: (_ for _ in ()).throw(
                RefreshRejected("refresh_token_reused", code="anthropic.auth"))).get()
        assert info.value.code == "anthropic.auth"

    def test_two_threads_refresh_once(self, store):
        seed_oauth(store, expires_at=int(time.time()) - 10)
        calls, results = [], []
        barrier = threading.Barrier(2)

        def refresher(record):
            calls.append(record["refresh_token"])
            time.sleep(0.3)
            return {"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}

        def worker():
            barrier.wait()
            results.append(TokenProvider(store, "demo", "oauth", refresher).get())

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert calls == ["refresh-1"] and results == ["access-2", "access-2"]

    def test_two_processes_refresh_once(self, tmp_path):
        state = tmp_path / "state"
        sm = SecretsManager(state, storage="insecure")
        assert sm.initialize(), sm.storage_error
        seed_oauth(CredentialStore(sm), expires_at=int(time.time()) - 10)
        counter, go = tmp_path / "token-calls.txt", tmp_path / "go"
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from orchestrator.Core.secrets_manager import SecretsManager\n"
            "from orchestrator.llm.auth.store import CredentialStore\n"
            "from orchestrator.llm.auth.tokens import TokenProvider\n"
            "state, counter, go = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])\n"
            "sm = SecretsManager(state, storage='insecure')\n"
            "assert sm.initialize(), sm.storage_error\n"
            "def refresher(record):\n"
            "    with open(counter, 'a', encoding='utf-8') as f:\n"
            "        f.write(record['refresh_token'] + '\\n')\n"
            "    time.sleep(0.5)\n"
            "    return {'access_token': 'access-2', 'refresh_token': 'refresh-2', 'expires_in': 3600}\n"
            "while not go.exists():\n"
            "    time.sleep(0.01)\n"
            "print(TokenProvider(CredentialStore(sm), 'demo', 'oauth', refresher).get())\n"
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_")}
        env["PYTHONPATH"] = str(REPO_ROOT)
        procs = [subprocess.Popen([sys.executable, "-c", script, str(state), str(counter), str(go)], env=env,
                                  cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        time.sleep(0.5)
        go.write_text("go", encoding="utf-8")
        outputs = []
        for proc in procs:
            out, err = proc.communicate(timeout=120)
            assert proc.returncode == 0, err.decode(errors="replace")[-2000:]
            outputs.append(out.decode().strip())
        assert outputs == ["access-2", "access-2"]
        assert counter.read_text(encoding="utf-8").splitlines() == ["refresh-1"]
        assert SecretsManager(state, storage="insecure").get_record("cred/demo/oauth")["refresh_token"] == "refresh-2"

    def test_the_provider_shows_no_token(self, store):
        seed_oauth(store, expires_at=int(time.time()) + 3600)
        provider = TokenProvider(store, "demo", "oauth", lambda record: {})
        assert "access-1" not in repr(provider)
        with pytest.raises(TypeError):
            pickle.dumps(provider)


def test_adapters_refuse_credentials_they_cannot_use_yet(tmp_path, store):
    preset = ProviderPreset(id="demo", label="Demo", adapter="openai_compat", base_url="https://demo.example/v1",
                            auth=[{"id": "oauth", "type": "oauth_pkce_loopback",
                                   "oauth": {"authorize_url": "https://demo.example/auth"}}],
                            models={"default": "m"})
    from orchestrator.llm.providers.openai_compat import OpenAICompatAdapter
    cred = ResolvedCredential(kind="token_callable", method="oauth", type="oauth_pkce_loopback",
                              token_provider=lambda: "t")
    with pytest.raises(NotConfigured) as info:
        OpenAICompatAdapter(preset, base_url=preset.base_url, credential=cred).client()
    assert info.value.code == "auth.not_built"


def test_validate_spec_defaults():
    assert ValidateSpec(path="/models").models_field == "data"
    assert AuthMethodSpec(id="k", type="api_key").label == "API key"
    assert AuthMethodSpec(id="k", type="none", label="Own").label == "Own"
