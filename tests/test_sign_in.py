"""Sign-in in the CLI and the GUI (orchestrator/llm/auth/signin.py, `llm-cli ditto login|logout|auth`,
orchestrator/gui/sign_in.py): methods and their order, the read-only status, a key checked before it is stored,
exit codes, the non-terminal rules, no secret in argv or in the output, the storage choice, and the panel's
worker threads. No network: providers answer through a mock transport."""

import gc
import json
import threading
import time
from types import SimpleNamespace

import pytest
import yaml

httpx2 = pytest.importorskip("httpx2")
pytest.importorskip("anthropic")
pytest.importorskip("openai")

from click.testing import CliRunner  # noqa: E402

from orchestrator import cli  # noqa: E402
from orchestrator.Core.config_manager import ConfigManager  # noqa: E402
from orchestrator.Core.secrets_manager import SecretsManager, credential_name  # noqa: E402
from orchestrator.llm import load_catalog  # noqa: E402
from orchestrator.llm.auth.errors import AuthError  # noqa: E402
from orchestrator.llm.auth.signin import (NO_SIGN_IN, NOT_RUNNING, NOT_SIGNED_IN, SIGNED_IN, STORAGE,  # noqa: E402
                                          AuthStatus, SignIn, access_text, exit_code, method_infos, status_line)
from orchestrator.llm.auth.validate import ValidationResult  # noqa: E402
from orchestrator.llm.errors import ProviderUnavailable  # noqa: E402
from test_llm import FakeHTTP  # noqa: E402

# Synthetic keys, built at run time so that the repository holds no literal that looks like a key.
OPENAI_KEY = "sk-" + "proj-" + "O" * 40
CLAUDE_KEY = "sk-ant-" + "api03-" + "C" * 40
ADMIN_KEY = "sk-ant-" + "admin01-" + "Z" * 32
CUSTOM_KEY = "custom-" + "k" * 24
WORKSPACE = "wrkspc_" + "01SignIn"
MODELS = {"data": [{"id": "m1"}, {"id": "m2"}]}


@pytest.fixture(autouse=True)
def fresh_keyring_probe():
    from orchestrator.Core import vault_key
    vault_key.reset_probe()
    yield
    vault_key.reset_probe()


@pytest.fixture
def home(tmp_path, monkeypatch, memory_keyring):
    """The per-user folder of the CLI's vault and auth.json (the settings go to tmp_path/configs, see conftest)."""
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "300")  # rich tables keep a cell on one line
    return tmp_path / "state"


def open_vault(home):
    sm = SecretsManager(home)
    assert sm.initialize(), sm.storage_error
    return sm


def settings(tmp_path):
    path = tmp_path / "configs" / "config.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def lock_keyring(monkeypatch, memory_keyring):
    def locked(*args):
        raise RuntimeError("locked")

    monkeypatch.setattr(memory_keyring, "get_password", locked)
    monkeypatch.setattr(memory_keyring, "set_password", locked)
    from orchestrator.Core import vault_key
    vault_key.reset_probe()


def run(*args, http=None, input=None):
    ctx = cli.FrameworkContext()
    ctx.http_client = http.client() if http is not None else None
    return CliRunner().invoke(cli.cli, ["-q", *args], input=input, obj=ctx)


def flat(text):
    return " ".join(text.split())


def service_for(tmp_path, vault, http=None, **values):
    config = ConfigManager(tmp_path / "cfg")
    for key, value in values.items():
        config.set(key, value)
    return SignIn(config, vault, http_client=http.client() if http is not None else None)


# ======================================================================================================
# The sign-in service
# ======================================================================================================

class TestService:

    def test_methods_in_preferred_order_and_the_recommended_one(self):
        catalog = load_catalog()
        anthropic = method_infos(catalog.get("anthropic"))
        assert [(i.spec.id, i.recommended, i.built) for i in anthropic] == [("api_key", True, True),
                                                                           ("ant_profile", False, True)]
        assert anthropic[1].spec.opt_in and "not confirmed" in anthropic[1].spec.note
        assert [(i.spec.id, i.recommended) for i in method_infos(catalog.get("custom"))] == [("api_key", True),
                                                                                            ("none", False)]

    def test_status_only_looks(self, tmp_path, home):
        vault = open_vault(home)
        assert vault.set_secret("custom_api_key", CUSTOM_KEY)  # an earlier version's key, not bound yet
        service = service_for(tmp_path, vault, ai_provider="custom", ai_base_url="http://127.0.0.1:8000/v1")
        status = service.status(service.catalog.get("custom"))
        assert (status.state, status.source, status.last4) == (SIGNED_IN, "vault", CUSTOM_KEY[-4:])
        assert vault.list_secrets() == ["cred/custom/api_key"], "status must not bind the key to an address"
        assert CUSTOM_KEY not in repr(status) and CUSTOM_KEY not in json.dumps(status.to_dict())

    def test_status_names_the_environment_variable(self, tmp_path, home, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
        service = service_for(tmp_path, open_vault(home))
        status = service.status(service.catalog.get("openai"))
        assert status.env_variable == "OPENAI_API_KEY" and status.last4 == "OOOO"
        assert status_line(status) == "Using key from environment variable OPENAI_API_KEY"
        assert OPENAI_KEY not in json.dumps(status.to_dict())

    def test_a_typed_key_is_checked_before_anything_is_stored(self, tmp_path, home):
        vault = open_vault(home)
        http = FakeHTTP().add({"error": {"message": "bad key"}}, status=401)
        service = service_for(tmp_path, vault, http)
        openai = service.catalog.get("openai")
        result = service.check(openai, openai.method("api_key"), key=OPENAI_KEY)
        assert not result.ok and result.error.code == "openai.auth" and exit_code(result.error) == 3
        assert http.last.headers["authorization"] == f"Bearer {OPENAI_KEY}"
        assert vault.list_secrets() == []

    def test_save_keeps_stored_fields_and_the_setting_in_step(self, tmp_path, home):
        vault = open_vault(home)
        service = service_for(tmp_path, vault, ai_anthropic_workspace_id="wrkspc_" + "Old1")
        anthropic = service.catalog.get("anthropic")
        spec = anthropic.method("api_key")
        saved = service.save_key(anthropic, spec, CLAUDE_KEY, fields={"workspace_id": WORKSPACE})
        assert saved.metadata["workspace_id"] == WORKSPACE and saved.metadata["last4"] == "CCCC"
        assert service.config.get("ai_anthropic_workspace_id") == WORKSPACE, "the setting would win otherwise"
        assert service.config.get("ai_auth_anthropic") == "api_key"
        service.save_key(anthropic, spec, CLAUDE_KEY[:-1] + "D")  # a new key without the option keeps the field
        assert service.store.metadata("anthropic", "api_key")["workspace_id"] == WORKSPACE

    def test_sign_out_is_local_and_honest(self, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        service = service_for(tmp_path, vault)
        openai = service.catalog.get("openai")
        spec = openai.method("api_key")
        service.save_key(openai, spec, OPENAI_KEY)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "env-" + "E" * 30)
        result = service.sign_out(openai, spec)
        assert result.deleted and result.revoke_url == spec.key_page and result.env_variable == "OPENAI_API_KEY"
        assert vault.list_secrets() == [] and not service.config.get("ai_auth_openai")

    def test_the_profile_keeps_only_its_name(self, tmp_path, home):
        vault = open_vault(home)
        service = service_for(tmp_path, vault)
        anthropic = service.catalog.get("anthropic")
        spec = anthropic.method("ant_profile")
        service.use_method(anthropic, spec, profile="work")
        assert service.store.metadata("anthropic", "ant_profile")["profile"] == "work"
        assert vault.list_secrets() == [] and service.config.get("ai_auth_anthropic") == "ant_profile"
        status = service.status(anthropic)
        assert (status.method, status.source) == ("ant_profile", "profile:work")
        assert status_line(status) == "Using the ant CLI profile 'work'"
        with pytest.raises(ValueError):
            service.use_method(anthropic, spec, profile="../other")
        assert service.sign_out(anthropic, spec).note.startswith("`ant auth logout`")
        assert service.status(anthropic).method == "api_key"

    def test_storage_is_chosen_explicitly_when_no_keyring_works(self, tmp_path, home, monkeypatch, memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        vault = SecretsManager(SimpleNamespace(config=ConfigManager(tmp_path / "cfg")))
        assert not vault.initialize()
        service = SignIn(vault._config, vault)
        anthropic = service.catalog.get("anthropic")
        assert service.status(anthropic).state == STORAGE
        with pytest.raises(AuthError) as info:
            service.save_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY)
        assert info.value.exit_code == 5
        with pytest.raises(Exception) as info:
            service.choose_storage("ephemeral")
        assert info.value.code == "storage.invalid_mode"
        service.choose_storage("insecure")
        assert vault.storage_mode == "insecure" and service.config.get("secret_storage") == "insecure"
        service.save_key(anthropic, anthropic.method("api_key"), CLAUDE_KEY)
        assert (home / "secrets.store.insecure-key").exists()

    def test_rotation_keeps_every_credential(self, tmp_path, home):
        vault = open_vault(home)
        service = service_for(tmp_path, vault)
        openai = service.catalog.get("openai")
        service.save_key(openai, openai.method("api_key"), OPENAI_KEY)
        kid = json.loads((home / "secrets.store").read_text())["kid"]
        service.rotate_key()
        assert json.loads((home / "secrets.store").read_text())["kid"] != kid
        assert open_vault(home).get_record("cred/openai/api_key")["secret"] == OPENAI_KEY

    def test_access_text_for_the_chat_header(self):
        assert access_text({"label": "Ollama (local)", "local": True, "auth_type": "none"}) == "Local · Ollama"
        assert access_text({"label": "Claude", "auth_label": "API key", "auth_type": "api_key",
                            "auth_source": "vault", "workspace_id": WORKSPACE}) == f"Claude · API key · {WORKSPACE}"
        assert access_text({"label": "OpenAI", "auth_type": "api_key", "auth_source": "env:OPENAI_API_KEY"}) == \
            "OpenAI · key from OPENAI_API_KEY"
        assert access_text({"label": "Claude", "auth_type": "api_key", "auth_source": "none"}) == \
            "Claude · not signed in"

    def test_exit_codes_and_status_lines(self):
        assert exit_code(ProviderUnavailable("down")) == 4
        st = AuthStatus(provider="x", label="X", state=SIGNED_IN, source="vault", account="workspace w", last4="abcd")
        assert status_line(st) == "Signed in as workspace w"
        assert status_line(AuthStatus(provider="x", label="X", state=NOT_SIGNED_IN)) == "Not signed in"
        assert status_line(AuthStatus(provider="x", label="X", state="signed_out")) == "Expired: sign in again"
        german = {"signin.status.not_signed_in": "Nicht angemeldet"}
        assert status_line(AuthStatus(provider="x", label="X", state=NOT_SIGNED_IN),
                           lambda key, default: german.get(key, default)) == "Nicht angemeldet"


# ======================================================================================================
# llm-cli ditto login / logout / auth
# ======================================================================================================

class TestLogin:

    def test_a_key_from_stdin_is_checked_stored_and_never_echoed(self, tmp_path, home):
        http = FakeHTTP().add(MODELS)
        result = run("ditto", "login", "openai", "--with-token", "--project", "proj_" + "abc", http=http,
                     input=OPENAI_KEY + "\n")
        assert result.exit_code == 0, result.output
        assert http.last.url == "https://api.openai.com/v1/models"
        assert http.last.headers["authorization"] == f"Bearer {OPENAI_KEY}"
        assert http.last.headers["openai-project"] == "proj_abc"
        assert OPENAI_KEY not in result.output and "...OOOO" in result.output and "2 models" in result.output
        vault = open_vault(home)
        assert vault.get_record("cred/openai/api_key")["secret"] == OPENAI_KEY
        assert settings(tmp_path)["ai_auth_openai"] == "api_key"
        assert OPENAI_KEY not in json.dumps(json.loads((home / "auth.json").read_text()))

    def test_without_a_terminal_a_provider_with_several_methods_needs_method(self, home):
        result = run("ditto", "login", "custom", "--with-token", input=CUSTOM_KEY)
        assert result.exit_code == 2 and "--method" in result.output and CUSTOM_KEY not in result.output
        assert not (home / "secrets.store").exists() or open_vault(home).list_secrets() == []

    def test_an_opt_in_method_does_not_count_as_a_choice(self, tmp_path, home):
        http = FakeHTTP().add(MODELS, headers={"anthropic-workspace-id": WORKSPACE})
        result = run("ditto", "login", "anthropic", "--with-token", "--workspace-id", WORKSPACE, http=http,
                     input=CLAUDE_KEY)
        assert result.exit_code == 0, result.output
        assert http.last.headers["x-api-key"] == CLAUDE_KEY and http.last.headers["anthropic-workspace-id"] == WORKSPACE
        assert f"workspace {WORKSPACE}" in result.output
        assert SignIn(ConfigManager(None), open_vault(home)).store.metadata("anthropic", "api_key")["workspace_id"] \
            == WORKSPACE

    def test_without_a_terminal_the_key_comes_only_from_stdin_or_the_environment(self, home):
        result = run("ditto", "login", "openai")
        assert result.exit_code == 2 and "--with-token" in result.output and "--from-env" in result.output

    def test_no_option_takes_a_key_value(self):
        params = {p.name for p in cli.ditto_login.params}
        assert not params & {"key", "api_key", "token", "password", "secret"}
        assert all(not p.is_flag for p in cli.ditto_login.params if p.name in ("workspace_id", "base_url"))

    def test_from_env_imports_the_documented_variable_once(self, tmp_path, home, monkeypatch):
        result = run("ditto", "login", "openai", "--from-env", "--no-check")
        assert result.exit_code == 2 and "OPENAI_API_KEY is not set" in flat(result.output)
        monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
        result = run("ditto", "login", "openai", "--from-env", "--no-check")
        assert result.exit_code == 0, result.output
        assert "not checked" in result.output and "OPENAI_API_KEY is now stored" in flat(result.output)
        assert open_vault(home).get_record("cred/openai/api_key")["secret"] == OPENAI_KEY

    def test_the_hidden_prompt_at_a_terminal(self, tmp_path, home, monkeypatch):
        monkeypatch.setattr(cli, "_interactive", lambda: True)
        http = FakeHTTP().add(MODELS)
        result = run("ditto", "login", "anthropic", http=http, input="1\n" + CLAUDE_KEY + "\n")
        assert result.exit_code == 0, result.output
        assert "API key [api_key] (recommended)" in result.output and CLAUDE_KEY not in result.output
        assert open_vault(home).get_record("cred/anthropic/api_key")["secret"] == CLAUDE_KEY

    def test_an_admin_key_is_refused_before_any_request(self, home):
        http = FakeHTTP()
        result = run("ditto", "login", "anthropic", "--with-token", http=http, input=ADMIN_KEY)
        assert result.exit_code == 3 and "Admin API key" in result.output and http.requests == []
        assert ADMIN_KEY not in result.output

    def test_a_rejected_key_is_not_stored(self, home):
        http = FakeHTTP().add({"type": "error", "error": {"type": "authentication_error", "message": "invalid"}},
                              status=401)
        result = run("ditto", "login", "anthropic", "--with-token", http=http, input=CLAUDE_KEY)
        assert result.exit_code == 3 and "Claude rejected the key" in result.output
        assert "Next: Open key page: https://platform.claude.com/settings/keys" in result.output
        assert open_vault(home).list_secrets() == []

    def test_an_unreachable_provider_exits_with_4(self, home):
        def refuse(request):
            raise httpx2.ConnectError("refused")

        ctx = cli.FrameworkContext()
        ctx.http_client = httpx2.Client(transport=httpx2.MockTransport(refuse))
        result = CliRunner().invoke(cli.cli, ["-q", "ditto", "login", "openai", "--with-token"], input=OPENAI_KEY,
                                    obj=ctx)
        assert result.exit_code == 4 and "Could not reach" in result.output

    def test_no_usable_keyring_exits_with_5_and_names_the_choices(self, tmp_path, home, monkeypatch, memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        result = run("ditto", "login", "openai", "--with-token", input=OPENAI_KEY)
        assert result.exit_code == 5
        text = flat(result.output)
        assert "llm-cli ditto auth storage --use passphrase" in text and "--insecure-storage" in text
        assert not (home / "secrets.store").exists()

    def test_insecure_storage_is_an_explicit_choice(self, tmp_path, home, monkeypatch, memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        result = run("ditto", "login", "openai", "--with-token", "--insecure-storage", "--no-check",
                     input=OPENAI_KEY)
        assert result.exit_code == 0, result.output
        assert "NOT encrypted at rest" in result.output and settings(tmp_path)["secret_storage"] == "insecure"
        assert (home / "secrets.store.insecure-key").exists()
        assert OPENAI_KEY not in (home / "secrets.store").read_text()

    def test_insecure_storage_never_replaces_a_working_keyring(self, tmp_path, home):
        result = run("ditto", "login", "openai", "--with-token", "--insecure-storage", "--no-check",
                     input=OPENAI_KEY)
        assert result.exit_code == 0 and "is ignored" in flat(result.output)
        assert not (home / "secrets.store.insecure-key").exists() and "secret_storage" not in settings(tmp_path)

    def test_a_method_without_a_flow_and_a_removed_provider_exit_with_6(self, tmp_path, home):
        (tmp_path / "configs").mkdir(exist_ok=True)
        (tmp_path / "configs" / "llm_providers.yml").write_text(
            "version: 2\nproviders:\n  router:\n    label: Router\n    adapter: openai_compat\n"
            "    base_url: https://router.example.com/v1\n    auth:\n"
            "      - {id: oauth, type: oauth_pkce_loopback, oauth: {authorize_url: https://router.example.com/auth}}\n",
            encoding="utf-8")
        result = run("ditto", "login", "router")
        assert result.exit_code == 6 and "not available in this version" in result.output
        result = run("ditto", "login", "claude_code")
        assert result.exit_code == 6 and "llm-cli mcp config" in flat(result.output)

    def test_an_unknown_provider_is_a_usage_error_and_a_pasted_key_is_not_echoed(self, home):
        result = run("ditto", "login", OPENAI_KEY)
        assert result.exit_code == 2 and "Unknown AI provider" in result.output and OPENAI_KEY not in result.output

    def test_options_that_do_not_belong_to_the_method(self, home):
        assert run("ditto", "login", "openai", "--workspace-id", WORKSPACE).exit_code == 2
        assert run("ditto", "login", "openai", "--profile", "work").exit_code == 2
        assert run("ditto", "login", "ollama", "--with-token", input="x").exit_code == 2
        assert run("ditto", "login", "openai", "--base-url", "http://127.0.0.1:1/v1").exit_code == 2
        assert run("ditto", "login", "openai", "--with-token", "--from-env").exit_code == 2

    def test_a_key_over_plain_http_to_another_computer_needs_yes(self, tmp_path, home):
        args = ("ditto", "login", "custom", "--method", "api_key", "--base-url", "http://192.0.2.10:8000/v1",
                "--with-token", "--no-check")
        result = run(*args, input=CUSTOM_KEY)
        assert result.exit_code == 2 and "unencrypted" in result.output and "--yes" in result.output
        result = run(*args, "--yes", input=CUSTOM_KEY)
        assert result.exit_code == 0, result.output
        vault = open_vault(home)
        assert vault.list_secrets() == [credential_name("custom", "api_key", "http://192.0.2.10:8000")]
        assert settings(tmp_path)["ai_base_url_custom"] == "http://192.0.2.10:8000/v1"

    def test_a_local_engine_needs_no_sign_in(self, tmp_path, home):
        http = FakeHTTP().add(MODELS)
        result = run("ditto", "login", "ollama", "--base-url", "http://127.0.0.1:11500/v1", http=http)
        assert result.exit_code == 0, result.output
        assert http.last.url == "http://127.0.0.1:11500/v1/models" and "authorization" not in http.last.headers
        assert "no sign-in needed" in result.output
        assert settings(tmp_path)["ai_base_url_ollama"] == "http://127.0.0.1:11500/v1"
        assert settings(tmp_path)["ai_auth_ollama"] == "none"

    def test_an_optional_key_left_empty_means_no_key(self, tmp_path, home, monkeypatch):
        (tmp_path / "configs").mkdir(exist_ok=True)
        (tmp_path / "configs" / "llm_providers.yml").write_text(
            "version: 2\nproviders:\n  studio:\n    label: Studio (local)\n    adapter: openai_compat\n"
            "    location: local\n    base_url: http://127.0.0.1:1234/v1\n    base_url_configurable: true\n"
            "    auth:\n      - {id: token, type: bearer_optional, where: \"Developer > Manage Tokens\"}\n",
            encoding="utf-8")
        monkeypatch.setattr(cli, "_interactive", lambda: True)
        http = FakeHTTP().add(MODELS).add(MODELS)
        result = run("ditto", "login", "studio", http=http, input=CUSTOM_KEY + "\n")
        assert result.exit_code == 0, result.output
        assert "Where to find it: Developer > Manage Tokens" in result.output
        assert http.last.headers["authorization"] == f"Bearer {CUSTOM_KEY}"
        assert open_vault(home).list_secrets() == [credential_name("studio", "token", "http://127.0.0.1:1234")]
        result = run("ditto", "login", "studio", http=http, input="\n")  # Enter for none
        assert result.exit_code == 0 and "used without a key" in result.output
        assert "authorization" not in http.last.headers and open_vault(home).list_secrets() == []

    def test_a_local_engine_that_is_not_running(self, tmp_path, home):
        def refuse(request):
            raise httpx2.ConnectError("refused")

        ctx = cli.FrameworkContext()
        ctx.http_client = httpx2.Client(transport=httpx2.MockTransport(refuse))
        result = CliRunner().invoke(cli.cli, ["-q", "ditto", "login", "ollama"], obj=ctx)
        assert result.exit_code == 4 and "Nothing is listening" in result.output
        assert "ai_auth_ollama" not in settings(tmp_path)
        assert run("ditto", "login", "ollama", "--no-check").exit_code == 0
        result = run("ditto", "logout", "ollama")
        assert result.exit_code == 0 and "forgotten" in result.output and "ai_auth_ollama" not in settings(tmp_path)

    def test_the_managed_server_is_explained(self, home):
        result = run("ditto", "login", "local")
        assert result.exit_code == 4 and "llm-cli ditto runtime start" in flat(result.output)

    def test_the_ant_profile(self, tmp_path, home, monkeypatch):
        seen = []

        def fake_check(self, preset, spec, base_url=None, **kw):
            seen.append(kw)
            return ValidationResult(ok=bool(kw.get("profile") == "work"), provider=preset.id, method=spec.id,
                                    models=3, error=None if kw.get("profile") == "work" else AuthError(
                                        "anthropic.profile", "The Anthropic CLI profile 'old' cannot be used."))

        monkeypatch.setattr(SignIn, "check", fake_check)
        result = run("ditto", "login", "anthropic", "--method", "ant-profile", "--profile", "old")
        assert result.exit_code == 3 and "Next: sign in with the ant CLI: ant auth login --profile old" in result.output
        assert "ai_auth_anthropic" not in settings(tmp_path)
        result = run("ditto", "login", "anthropic", "--method", "ant_profile", "--profile", "work")
        assert result.exit_code == 0, result.output
        assert "not confirmed" in flat(result.output) and "3 models" in result.output
        assert settings(tmp_path)["ai_auth_anthropic"] == "ant_profile"
        result = run("ditto", "logout", "anthropic")
        assert result.exit_code == 0 and "no longer uses the ant CLI profile" in result.output
        assert "ai_auth_anthropic" not in settings(tmp_path)


class TestLogoutAndStatus:

    def test_logout_deletes_and_prints_the_revoke_link(self, tmp_path, home):
        assert run("ditto", "login", "openai", "--with-token", "--no-check", input=OPENAI_KEY).exit_code == 0
        result = run("ditto", "logout", "openai")
        assert result.exit_code == 0, result.output
        assert "was deleted" in result.output
        assert "https://platform.openai.com/settings/organization/api-keys" in result.output
        assert open_vault(home).list_secrets() == [] and "ai_auth_openai" not in settings(tmp_path)
        assert "nothing was deleted" in run("ditto", "logout", "openai").output

    def test_logout_of_a_key_bound_to_an_address_needs_the_address(self, tmp_path, home):
        url = "http://127.0.0.1:8000/v1"
        assert run("ditto", "login", "custom", "--method", "api_key", "--base-url", url, "--with-token",
                   "--no-check", input=CUSTOM_KEY).exit_code == 0
        assert run("ditto", "logout", "custom", "--method", "api_key").exit_code == 0, "the remembered address"
        assert open_vault(home).list_secrets() == []
        (tmp_path / "configs" / "config.yml").write_text("{}\n", encoding="utf-8")  # no address known
        result = run("ditto", "logout", "custom", "--method", "api_key")
        assert result.exit_code == 2 and "server address" in result.output

    def test_status_json_holds_no_secret(self, tmp_path, home, monkeypatch):
        assert run("ditto", "login", "anthropic", "--with-token", "--no-check", "--workspace-id", WORKSPACE,
                   input=CLAUDE_KEY).exit_code == 0
        monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
        result = run("ditto", "auth", "status", "--json")
        assert result.exit_code == 0, result.output
        assert CLAUDE_KEY not in result.output and OPENAI_KEY not in result.output
        data = json.loads(result.stdout)
        rows = {row["provider"]: row for row in data["providers"]}
        assert rows["anthropic"]["state"] == SIGNED_IN and rows["anthropic"]["last4"] == "CCCC"
        assert rows["anthropic"]["fields"] == {"workspace_id": WORKSPACE}
        assert rows["openai"]["source"] == "env:OPENAI_API_KEY" and rows["openai"]["primary"]
        assert rows["ollama"]["state"] == NO_SIGN_IN and rows["local"]["state"] == NOT_RUNNING
        assert data["storage"]["usable"] and data["storage"]["mode"] == "keyring"

    def test_status_table_and_check(self, tmp_path, home):
        assert run("ditto", "login", "openai", "--with-token", "--no-check", input=OPENAI_KEY).exit_code == 0
        result = run("ditto", "auth", "status")
        assert result.exit_code == 0 and "Signed in (key ...OOOO)" in flat(result.output)
        assert OPENAI_KEY not in result.output
        http = FakeHTTP().add({"error": {"message": "revoked"}}, status=401)
        result = run("ditto", "auth", "status", "--check", "--json", http=http)
        assert result.exit_code == 3
        assert json.loads(result.stdout)["providers"][1]["check"]["error"]["code"] == "openai.auth"

    def test_status_without_a_usable_vault_exits_with_5(self, home, monkeypatch, memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        result = run("ditto", "auth", "status")
        assert result.exit_code == 5 and "credential vault unavailable" in flat(result.output)

    def test_methods_need_neither_vault_nor_network(self, home, monkeypatch, memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        result = run("ditto", "auth", "methods", "anthropic")
        assert result.exit_code == 0
        assert "1. API key [api_key] - API key (recommended)" in result.output
        assert "advanced, only when chosen" in result.output and "note:" in result.output
        data = json.loads(run("ditto", "auth", "methods", "--json").stdout)
        assert [m["id"] for m in data["providers"][0]["methods"]] == ["api_key", "ant_profile"]

    def test_storage_show_choose_and_rotate(self, tmp_path, home, monkeypatch, memory_keyring):
        result = run("ditto", "auth", "storage")
        assert result.exit_code == 0 and "'keyring'" in result.output
        assert run("ditto", "auth", "storage", "--use", "ephemeral").exit_code == 2
        assert run("ditto", "login", "openai", "--with-token", "--no-check", input=OPENAI_KEY).exit_code == 0
        result = run("ditto", "auth", "rotate-key")
        assert result.exit_code == 0 and "rotated" in result.output
        assert open_vault(home).get_record("cred/openai/api_key")["secret"] == OPENAI_KEY
        lock_keyring(monkeypatch, memory_keyring)
        assert run("ditto", "auth", "rotate-key").exit_code == 5
        assert run("ditto", "auth", "storage").exit_code == 5

    def test_storage_choice_without_a_keyring(self, tmp_path, home, monkeypatch, memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        result = run("ditto", "auth", "storage", "--use", "insecure")
        assert result.exit_code == 0, result.output
        assert settings(tmp_path)["secret_storage"] == "insecure"
        assert run("ditto", "login", "openai", "--with-token", "--no-check", input=OPENAI_KEY).exit_code == 0
        result = run("ditto", "auth", "storage", "--use", "passphrase")  # no terminal for the passphrase
        assert result.exit_code == 5 and settings(tmp_path)["secret_storage"] == "insecure"


class TestOtherCommands:

    def test_secrets_set_reads_stdin_and_deprecates_password(self, tmp_path, home, monkeypatch):
        vault = open_vault(home)

        def require(self):
            self.framework_manager = SimpleNamespace(secrets_manager=vault)
            return self

        monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
        token = "hf_" + "T" * 30
        result = CliRunner().invoke(cli.cli, ["-q", "secrets", "set", "hf_token", "--with-token"], input=token)
        assert result.exit_code == 0 and token not in result.output and vault.get_secret("hf_token") == token
        result = CliRunner().invoke(cli.cli, ["-q", "secrets", "set", "hf_token", "--password", "placeholder"])
        assert result.exit_code == 0 and "deprecated" in result.output

    def test_generate_ai_uses_the_stored_sign_in_and_asks_for_no_key(self, tmp_path, home, monkeypatch):
        from orchestrator.Core.ditto_manager import DittoCoder
        config = ConfigManager(None)
        config.set("ai_provider", "anthropic")
        ditto = DittoCoder(config_manager=config, secrets_manager=open_vault(home))
        framework = SimpleNamespace(config=config, get_component=lambda name: ditto)

        def require(self):
            self.framework_manager = framework
            return self

        monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
        probe = tmp_path / "probe.txt"
        probe.write_text("cpu: aarch64\n", encoding="utf-8")
        result = CliRunner().invoke(cli.cli, ["-q", "module", "generate-ai", str(probe)], input="")
        assert result.exit_code == 1
        assert "llm-cli ditto login anthropic" in flat(result.output) and "litellm" not in result.output.lower()
        assert "API key:" not in result.output, "no key prompt of its own"

    def test_help_names_the_sign_in(self):
        result = CliRunner().invoke(cli.cli, ["--help"])
        assert "llm-cli ditto login" in flat(result.output)
        result = CliRunner().invoke(cli.cli, ["-q", "ditto", "--help"])
        for command in ("login", "logout", "auth"):
            assert command in result.output


STUDIO = ("version: 2\nproviders:\n  studio:\n    label: Studio (local)\n    adapter: openai_compat\n"
          "    location: local\n    base_url: http://127.0.0.1:1234/v1\n    base_url_configurable: true\n"
          "    auth:\n      - {id: token, type: bearer_optional, where: \"Developer > Manage Tokens\"}\n")


def user_catalog(folder, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "llm_providers.yml").write_text(text, encoding="utf-8")


class TestReviewFindings:
    """Problems found by the review of the sign-in package, each reproduced before it was fixed."""

    def test_a_key_echoed_by_the_server_never_reaches_the_output(self, tmp_path, home):
        url = "http://127.0.0.1:8000/v1"
        assert run("ditto", "login", "custom", "--method", "api_key", "--base-url", url, "--with-token", "--no-check",
                   input=CUSTOM_KEY).exit_code == 0
        echo = {"error": {"message": "invalid api key " + CUSTOM_KEY}}  # no secret pattern knows this key
        result = run("ditto", "auth", "status", "--check", "--json", http=FakeHTTP().add(echo, status=401))
        assert result.exit_code == 3 and CUSTOM_KEY not in result.output
        rows = {row["provider"]: row for row in json.loads(result.stdout)["providers"]}
        assert "invalid api key REDACTED" in rows["custom"]["check"]["message"]
        result = run("ditto", "login", "custom", "--method", "api_key", "--with-token",
                     http=FakeHTTP().add(echo, status=401), input=CUSTOM_KEY)
        assert result.exit_code == 3 and CUSTOM_KEY not in result.output

    def test_a_chat_error_that_echoes_the_key_is_masked(self, tmp_path, home):
        # `module generate-ai`, `ditto ask` and the chat go through LLMClient: the same server, the same echo.
        from orchestrator.llm import ChatRequest, LLMClient, LLMError, Message
        url = "http://127.0.0.1:8000/v1"
        vault = open_vault(home)
        service = service_for(tmp_path, vault, ai_provider="custom", ai_base_url_custom=url, ai_model="m1")
        custom = service.catalog.get("custom")
        service.save_key(custom, custom.method("api_key"), CUSTOM_KEY, url)
        http = FakeHTTP().add({"error": {"message": "invalid api key " + CUSTOM_KEY}}, status=401)
        client = LLMClient(service.config, vault, http_client=http.client())
        with pytest.raises(LLMError) as info:
            client.chat(ChatRequest(system="You are Ditto.", messages=[Message.user("Hello")]))
        assert http.last.headers["authorization"] == f"Bearer {CUSTOM_KEY}"
        assert CUSTOM_KEY not in str(info.value) and "REDACTED" in str(info.value)

    def test_an_account_label_that_echoes_the_key_is_masked(self, tmp_path, home):
        user_catalog(tmp_path / "cfg", "version: 2\nproviders:\n  echo:\n    label: Echo\n    adapter: openai_compat\n"
                                       "    base_url: https://echo.example.com/v1\n    auth:\n      - {id: api_key, "
                                       "type: api_key, validate: {path: /me, account_field: owner}}\n")
        http = FakeHTTP().add({"owner": "user " + CUSTOM_KEY})
        service = service_for(tmp_path, open_vault(home), http)
        echo = service.catalog.get("echo")
        result = service.check(echo, echo.method("api_key"), key=CUSTOM_KEY)
        assert result.ok and result.account_label == "user REDACTED"
        assert CUSTOM_KEY not in json.dumps(result.to_dict())

    def test_a_key_with_a_line_break_or_an_invisible_character_is_refused(self, tmp_path, home):
        http = FakeHTTP()
        result = run("ditto", "login", "openai", "--with-token", http=http, input=OPENAI_KEY + "\nsecond line\n")
        assert result.exit_code == 2 and "line break" in result.output and http.requests == []
        invisible = OPENAI_KEY[:10] + "​" + OPENAI_KEY[10:]  # copied from a web page
        result = run("ditto", "login", "openai", "--with-token", http=http, input=invisible)
        assert result.exit_code == 2 and isinstance(result.exception, SystemExit), "no traceback holding the key"
        assert http.requests == [] and open_vault(home).list_secrets() == []
        vault = open_vault(home)
        service = service_for(tmp_path, vault, http)
        openai = service.catalog.get("openai")
        spec = openai.method("api_key")
        with pytest.raises(AuthError) as info:
            service.save_key(openai, spec, OPENAI_KEY + " " + OPENAI_KEY)
        assert info.value.code == "auth.key_malformed" and vault.list_secrets() == []
        assert service.check(openai, spec, key=invisible).error.code == "auth.key_malformed"
        assert vault.set_secret("openai_api_key", invisible)  # stored by an earlier version
        assert service.check(openai, spec).error.code == "auth.key_malformed" and http.requests == []

    def test_an_empty_stdin_never_deletes_an_optional_key(self, tmp_path, home):
        user_catalog(tmp_path / "configs", STUDIO)
        assert run("ditto", "login", "studio", "--with-token", "--no-check", input=CUSTOM_KEY).exit_code == 0
        result = run("ditto", "login", "studio", "--with-token", "--no-check", input="")  # an unset variable
        assert result.exit_code == 2 and "llm-cli ditto logout studio" in flat(result.output)
        assert open_vault(home).list_secrets() == [credential_name("studio", "token", "http://127.0.0.1:1234")]

    def test_a_field_check_binds_a_key_stored_without_an_address_first(self, tmp_path, home):
        user_catalog(tmp_path / "cfg", "version: 2\nproviders:\n  studio:\n    label: Studio\n    adapter: "
                                       "openai_compat\n    location: local\n    base_url: http://127.0.0.1:1234/v1\n"
                                       "    base_url_configurable: true\n    auth:\n      - {id: api_key, type: "
                                       "api_key, fields: [{id: project, label: Project, pattern: '^p[0-9]+$'}]}\n")
        vault = open_vault(home)
        assert vault.set_record("cred/studio/api_key", {"kind": "api_key", "secret": CUSTOM_KEY})  # no address yet
        http = FakeHTTP().add(MODELS).add(MODELS)
        service = service_for(tmp_path, vault, http)
        studio = service.catalog.get("studio")
        spec = studio.method("api_key")
        assert service.check(studio, spec, "http://127.0.0.1:9001/v1", fields={"project": "p1"}).ok
        assert vault.list_secrets() == [credential_name("studio", "api_key", "http://127.0.0.1:9001")]
        result = service.check(studio, spec, "http://127.0.0.1:9002/v1")
        assert not result.ok and len(http.requests) == 1, "the key went to one address only"

    def test_a_provider_without_an_address_is_a_usage_error(self, home):
        result = run("ditto", "login", "custom", "--method", "api_key", "--with-token", input=CUSTOM_KEY)
        assert result.exit_code == 2 and "--base-url" in result.output
        assert run("ditto", "login", "custom", "--method", "none").exit_code == 2

    def test_insecure_storage_never_replaces_a_chosen_helper(self, tmp_path, home):
        (tmp_path / "configs").mkdir(exist_ok=True)
        (tmp_path / "configs" / "config.yml").write_text("secret_storage: helper\nsecret_helper: /nonexistent/helper\n",
                                                         encoding="utf-8")
        result = run("ditto", "login", "openai", "--with-token", "--no-check", "--insecure-storage", input=OPENAI_KEY)
        assert result.exit_code == 5 and "only replaces a keyring" in flat(result.output)
        assert settings(tmp_path)["secret_storage"] == "helper"
        assert not (home / "secrets.store.insecure-key").exists()

    def test_a_failed_storage_choice_leaves_the_user_config_alone(self, tmp_path, home, monkeypatch,
                                                                   memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        monkeypatch.setenv("LLM_FRAMEWORK_SECRET_HELPER", "/nonexistent/env-helper")
        result = run("ditto", "auth", "storage", "--use", "passphrase")  # no terminal for the passphrase
        assert result.exit_code == 5
        assert not {"secret_storage", "secret_helper"} & set(settings(tmp_path)), "no default or env value saved"

    def test_a_key_typed_as_the_method_is_not_echoed(self, home):
        for command in ("login", "logout"):
            result = run("ditto", command, "openai", "--method", OPENAI_KEY)
            assert result.exit_code == 2 and OPENAI_KEY not in result.output

    def test_a_malformed_option_value_is_a_usage_error(self, home):
        http = FakeHTTP()
        result = run("ditto", "login", "anthropic", "--with-token", "--workspace-id", "workspace-1", http=http,
                     input=CLAUDE_KEY)
        assert result.exit_code == 2 and "wrkspc_" in result.output and http.requests == []
        assert open_vault(home).list_secrets() == []

    def test_the_status_after_a_checked_login_names_the_account(self, tmp_path, home):
        http = FakeHTTP().add(MODELS, headers={"anthropic-workspace-id": WORKSPACE})
        assert run("ditto", "login", "anthropic", "--with-token", http=http, input=CLAUDE_KEY).exit_code == 0
        result = run("ditto", "auth", "status")
        assert result.exit_code == 0 and f"Signed in as workspace {WORKSPACE}" in flat(result.output)
        meta = SignIn(ConfigManager(None), open_vault(home)).store.metadata("anthropic", "api_key")
        assert meta["validated_at"] and meta["account_label"] == f"workspace {WORKSPACE}"


# ======================================================================================================
# GUI: SignInPanel, the storage chooser, the chat header
# ======================================================================================================

@pytest.fixture
def gui_threads():
    """Workers hold no widget; collect on the GUI thread and let them finish before the next test."""
    gc.collect()
    yield
    for thread in threading.enumerate():
        if thread.name.startswith(("sign-in-", "chat-access", "ai-model-list")):
            thread.join(timeout=5)
    gc.collect()


def wait_for(qapp, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


class OffThreadService(SignIn):
    """Records every vault, keyring or network call that ran on the GUI (main) thread."""

    on_gui_thread: list = []

    def _note(self, name):
        if threading.current_thread() is threading.main_thread():
            self.on_gui_thread.append(name)

    def status(self, *a, **kw):
        self._note("status")
        return super().status(*a, **kw)

    def storage_problem(self, *a, **kw):
        self._note("storage_problem")
        return super().storage_problem(*a, **kw)

    def check(self, *a, **kw):
        self._note("check")
        return super().check(*a, **kw)

    def save_key(self, *a, **kw):
        self._note("save_key")
        return super().save_key(*a, **kw)

    def sign_out(self, *a, **kw):
        self._note("sign_out")
        return super().sign_out(*a, **kw)

    def choose_storage(self, *a, **kw):
        self._note("choose_storage")
        return super().choose_storage(*a, **kw)


@pytest.mark.usefixtures("gui_threads")
class TestSignInPanel:

    def panel(self, tmp_path, vault, provider, http=None, **values):
        from orchestrator.gui.sign_in import SignInPanel
        service = OffThreadService(ConfigManager(tmp_path / "cfg"), vault,
                                   http_client=http.client() if http is not None else None)
        for key, value in values.items():
            service.config.set(key, value)
        OffThreadService.on_gui_thread = []
        framework = SimpleNamespace(config=service.config, secrets_manager=vault)
        return SignInPanel(framework, service.catalog.get(provider), service=service)

    def test_the_panel_switches_per_type(self, qapp, tmp_path, home):
        from orchestrator.gui.sign_in import CliSessionForm, KeyForm, LaterForm, ManagedForm, NoneForm
        vault = open_vault(home)
        panel = self.panel(tmp_path, vault, "anthropic")
        assert panel.current_method() == "api_key" and isinstance(panel.stack.currentWidget(), KeyForm)
        assert "Recommended" in panel.cards["api_key"].badges.text()
        assert "Advanced" in panel.cards["ant_profile"].badges.text()
        panel.select_method("ant_profile")
        assert isinstance(panel.stack.currentWidget(), CliSessionForm)
        assert panel.stack.currentWidget().command == "ant auth login"
        assert list(panel.forms["api_key"].fields) == ["workspace_id"]
        assert panel.forms["api_key"].fields["workspace_id"].placeholderText() == "wrkspc_..."
        assert wait_for(qapp, lambda: not panel.busy)
        ollama, local = self.panel(tmp_path, vault, "ollama"), self.panel(tmp_path, vault, "local")
        assert isinstance(ollama.stack.currentWidget(), NoneForm)
        assert isinstance(local.stack.currentWidget(), ManagedForm)
        custom = self.panel(tmp_path, vault, "custom")
        custom.select_method("none")
        assert isinstance(custom.stack.currentWidget(), NoneForm)
        (tmp_path / "cfg").mkdir(exist_ok=True)
        (tmp_path / "cfg" / "llm_providers.yml").write_text(
            "version: 2\nproviders:\n  router:\n    label: Router\n    adapter: openai_compat\n"
            "    base_url: https://router.example.com/v1\n    auth:\n"
            "      - {id: oauth, type: oauth_pkce_loopback, oauth: {authorize_url: https://router.example.com/a}}\n"
            "      - {id: api_key, type: api_key}\n", encoding="utf-8")
        router = self.panel(tmp_path, vault, "router")
        assert isinstance(router.forms["oauth"], LaterForm) and not router.cards["oauth"].radio.isEnabled()
        assert router.current_method() == "api_key" and router.cards["api_key"].recommended
        for each in (panel, ollama, local, custom, router):
            assert wait_for(qapp, lambda: not each.busy)

    def test_status_line(self, qapp, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        panel = self.panel(tmp_path, vault, "openai")
        assert wait_for(qapp, lambda: panel.status is not None)
        assert panel.lbl_status.text() == "Not signed in"
        monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
        panel.refresh()
        assert wait_for(qapp, lambda: "OPENAI_API_KEY" in panel.lbl_status.text())
        assert panel.lbl_status.text() == "Using key from environment variable OPENAI_API_KEY"
        assert OffThreadService.on_gui_thread == []

    def test_check_save_and_sign_out_run_in_workers(self, qapp, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        http = FakeHTTP().add(MODELS, headers={"anthropic-workspace-id": WORKSPACE})
        panel = self.panel(tmp_path, vault, "anthropic", http)
        changes = []
        panel.changed.connect(lambda: changes.append(1))
        assert wait_for(qapp, lambda: not panel.busy)
        form = panel.forms["api_key"]
        form.txt_key.setText(CLAUDE_KEY)
        form.fields["workspace_id"].setText(WORKSPACE)
        panel.check("api_key")
        assert wait_for(qapp, lambda: not panel.busy)
        assert panel.lbl_result.text() == f"Valid, workspace {WORKSPACE}, 2 models"
        assert vault.list_secrets() == [], "Check never stores"
        panel.save("api_key")
        assert wait_for(qapp, lambda: not panel.busy and "Signed in" in panel.lbl_status.text())
        assert vault.get_record("cred/anthropic/api_key")["secret"] == CLAUDE_KEY and changes == [1]
        assert form.txt_key.text() == "" and "...CCCC" in form.txt_key.placeholderText()
        assert CLAUDE_KEY not in panel.lbl_status.text() + panel.lbl_result.text()
        from orchestrator.gui import sign_in
        monkeypatch.setattr(sign_in.QMessageBox, "question", lambda *a, **k: sign_in.QMessageBox.Yes)
        panel.sign_out("api_key")
        assert wait_for(qapp, lambda: not panel.busy and panel.lbl_status.text() == "Not signed in")
        assert "https://platform.claude.com/settings/keys" in panel.lbl_result.text()
        assert vault.list_secrets() == [] and changes == [1, 1]
        assert OffThreadService.on_gui_thread == []

    def test_the_ui_thread_is_never_blocked(self, qapp, tmp_path, home, monkeypatch):
        from PySide6.QtCore import QTimer
        vault = open_vault(home)
        panel = self.panel(tmp_path, vault, "openai")
        assert wait_for(qapp, lambda: not panel.busy)
        release, threads = threading.Event(), []

        def slow_check(self, preset, spec, base_url=None, **kw):
            threads.append(threading.current_thread().name)
            release.wait(5)
            return ValidationResult(ok=True, provider=preset.id, method=spec.id, models=1)

        monkeypatch.setattr(OffThreadService, "check", slow_check)
        panel.forms["api_key"].txt_key.setText(OPENAI_KEY)
        started = time.monotonic()
        panel.check("api_key")
        assert time.monotonic() - started < 0.05
        assert panel.busy and not panel.forms["api_key"].btn_save.isEnabled()
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start(10)
        end = time.monotonic() + 0.3
        while time.monotonic() < end:
            qapp.processEvents()
            time.sleep(0.005)
        timer.stop()
        # The check waits 5 s in its worker; on the GUI thread no tick would come at all. The bound leaves room for
        # a slow CI machine.
        assert len(ticks) > 5 and max(b - a for a, b in zip(ticks, ticks[1:])) < 0.25
        release.set()
        assert wait_for(qapp, lambda: not panel.busy)
        assert threads and threads[0].startswith("sign-in-") and panel.lbl_result.text() == "Valid, 1 models"
        assert panel.forms["api_key"].btn_save.isEnabled()

    def test_the_storage_chooser_appears_without_a_keyring(self, qapp, tmp_path, home, monkeypatch,
                                                         memory_keyring):
        lock_keyring(monkeypatch, memory_keyring)
        config = ConfigManager(tmp_path / "cfg")
        vault = SecretsManager(SimpleNamespace(config=config))
        assert not vault.initialize()
        from orchestrator.gui.sign_in import SignInPanel
        OffThreadService.on_gui_thread = []
        service = OffThreadService(config, vault)
        panel = SignInPanel(SimpleNamespace(config=config, secrets_manager=vault), service.catalog.get("openai"),
                            service=service)
        assert wait_for(qapp, lambda: panel.status is not None)
        assert not panel.storage.isHidden() and "keyring" in panel.storage.lbl_problem.text()
        panel.storage.rb_insecure.setChecked(True)
        panel.storage.btn_use.click()
        assert "Confirm" in panel.storage.lbl_result.text() and not panel.busy  # nothing happens without the tick
        panel.storage.chk_insecure.setChecked(True)
        panel.storage.btn_use.click()
        assert wait_for(qapp, lambda: not panel.busy and panel.storage.isHidden())
        assert vault.storage_mode == "insecure" and config.get("secret_storage") == "insecure"
        assert OffThreadService.on_gui_thread == []

    def test_the_settings_dialog_opens_the_panel_for_the_typed_address(self, qapp, tmp_path, home, monkeypatch):
        from orchestrator.gui.dialogs import AIConfigurationDialog

        def no_network(config, secrets, provider):
            raise RuntimeError("no network in tests")

        monkeypatch.setattr(AIConfigurationDialog, "model_lister", staticmethod(no_network))
        vault = open_vault(home)
        framework = SimpleNamespace(config=ConfigManager(tmp_path / "cfg"), secrets_manager=vault,
                                    ditto_manager=None)
        dlg = AIConfigurationDialog(framework)
        dlg.cb_provider.setCurrentIndex(dlg.cb_provider.findData("custom"))
        dlg.txt_base_url.setText("http://127.0.0.1:9000/v1")
        window = dlg._sign_in()
        assert window.panel.base_url == "http://127.0.0.1:9000/v1"
        assert wait_for(qapp, lambda: not window.panel.busy)
        window.close()

    def test_a_typed_key_over_plain_http_to_another_computer_needs_a_yes(self, qapp, tmp_path, home, monkeypatch):
        from orchestrator.gui import sign_in
        vault = open_vault(home)
        http = FakeHTTP().add(MODELS)
        panel = self.panel(tmp_path, vault, "custom", http, ai_provider="custom",
                           ai_base_url_custom="http://192.0.2.10:8000/v1")
        assert wait_for(qapp, lambda: not panel.busy)
        asked, answers = [], [sign_in.QMessageBox.No]

        def question(parent, title, text, *args):
            asked.append((threading.current_thread() is threading.main_thread(), text))
            return answers[-1]

        monkeypatch.setattr(sign_in.QMessageBox, "question", question)
        panel.forms["api_key"].txt_key.setText(CUSTOM_KEY)
        panel.check("api_key")
        panel.save("api_key")
        assert not panel.busy and "Cancelled" in panel.lbl_result.text()
        assert http.requests == [] and vault.list_secrets() == []
        assert len(asked) == 2 and all(on_gui for on_gui, _ in asked) and "192.0.2.10:8000" in asked[0][1]
        answers.append(sign_in.QMessageBox.Yes)
        panel.check("api_key")
        assert wait_for(qapp, lambda: not panel.busy) and panel.lbl_result.text() == "Valid, 2 models"
        panel.save("api_key")  # the address was confirmed once
        assert wait_for(qapp, lambda: not panel.busy and vault.list_secrets() != [])
        assert len(asked) == 3 and OffThreadService.on_gui_thread == []
        loopback = self.panel(tmp_path, vault, "custom", ai_base_url_custom="http://127.0.0.1:8000/v1")
        assert wait_for(qapp, lambda: not loopback.busy)
        loopback.forms["api_key"].txt_key.setText(CUSTOM_KEY)
        loopback.save("api_key")
        assert wait_for(qapp, lambda: not loopback.busy) and len(asked) == 3, "this computer: no question"

    def test_a_late_status_read_does_not_replace_a_newer_one(self, qapp, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        release, calls = threading.Event(), []
        original = OffThreadService.status

        def status(self, preset, base_url=None):
            calls.append(1)
            if len(calls) == 1:  # the first read hangs on a slow vault and then reports an old state
                release.wait(5)
                return AuthStatus(provider=preset.id, label=preset.label, state=NOT_SIGNED_IN)
            return original(self, preset, base_url)

        monkeypatch.setattr(OffThreadService, "status", status)
        service = service_for(tmp_path, vault)
        openai = service.catalog.get("openai")
        service.save_key(openai, openai.method("api_key"), OPENAI_KEY)
        panel = self.panel(tmp_path, vault, "openai")
        panel.refresh()
        assert wait_for(qapp, lambda: "OOOO" in panel.lbl_status.text())
        release.set()
        assert wait_for(qapp, lambda: not panel.busy)
        assert "OOOO" in panel.lbl_status.text() and panel.status.state == SIGNED_IN


@pytest.mark.usefixtures("gui_threads")
class TestGuiHelpers:

    def test_the_chat_header_shows_the_access(self, qapp, tmp_path):
        from orchestrator.gui import chat_window

        class Ditto:
            def __init__(self, info):
                self.info = info

            def describe(self, task="chat"):
                if isinstance(self.info, Exception):
                    raise self.info
                return self.info

        framework = SimpleNamespace(config=ConfigManager(tmp_path / "cfg"),
                                    ditto_manager=Ditto({"label": "Ollama (local)", "local": True,
                                                         "auth_type": "none", "auth_source": "none"}))
        win = chat_window.ChatWindow(framework)
        assert wait_for(qapp, lambda: win.lbl_access.text() == "Local · Ollama")
        win.ditto = Ditto(RuntimeError("not set up"))
        win.refresh_access()
        assert wait_for(qapp, lambda: win.lbl_access.text() == "AI not set up")

    def test_the_passphrase_is_asked_on_the_gui_thread(self, qapp, monkeypatch):
        from orchestrator.gui.sign_in import PassphrasePrompt
        prompt = PassphrasePrompt()
        asked = []

        def dialog(new):
            asked.append((threading.current_thread() is threading.main_thread(), new))
            return "correct horse"

        monkeypatch.setattr(prompt, "dialog", dialog)
        answers = []
        worker = threading.Thread(target=lambda: answers.append(prompt(True)), name="sign-in-test")
        worker.start()
        assert wait_for(qapp, lambda: answers)
        worker.join(timeout=5)
        assert answers == ["correct horse"] and asked == [(True, True)]
        assert prompt(False) == "correct horse" and asked[-1] == (True, False)

    def test_the_storage_choice_at_start_is_saved(self, qapp, tmp_path, monkeypatch):
        from orchestrator.gui import sign_in

        def choose(self):
            self.chooser.rb_helper.setChecked(True)
            self.chooser.txt_helper.setText("pass show llmcc/vault-key")
            self.chooser.btn_use.click()
            return self.result()

        monkeypatch.setattr(sign_in.StorageChooserDialog, "exec", choose)
        config = ConfigManager(tmp_path / "cfg")
        assert sign_in.choose_storage_at_start(None, config, RuntimeError("no keyring"))
        reloaded = ConfigManager(tmp_path / "cfg")
        assert reloaded.get("secret_storage") == "helper"
        assert reloaded.get("secret_helper") == "pass show llmcc/vault-key"
        monkeypatch.setattr(sign_in.StorageChooserDialog, "exec", lambda self: 0)
        assert not sign_in.choose_storage_at_start(None, config, RuntimeError("no keyring"))

    def test_the_main_window_offers_the_chooser_before_giving_up(self, qapp, tmp_path, monkeypatch):
        from orchestrator.Core import vault_key
        from orchestrator.gui import main_window
        offered, shown = [], []

        class FailingFramework:
            init_error = vault_key.keyring_unavailable("it did not answer within 3 s")

            def __init__(self):
                self.config = ConfigManager(tmp_path / "cfg")

            def initialize(self):
                return False

        monkeypatch.setattr(main_window, "FrameworkManager", FailingFramework)
        monkeypatch.setattr(main_window, "choose_storage_at_start",
                            lambda parent, config, error: offered.append(error.code) or len(offered) < 2)
        monkeypatch.setattr(main_window.QMessageBox, "critical", lambda parent, title, text: shown.append(text))
        with pytest.raises(SystemExit):
            main_window.MainOrchestrator(tmp_path)
        assert offered == ["storage.keyring_unavailable"] * 2 and "secret_storage" in shown[0]
