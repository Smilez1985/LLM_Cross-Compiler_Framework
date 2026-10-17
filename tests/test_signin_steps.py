"""The shared steps behind `llmcf secrets set|list` and `llmcf ditto login --from-env`, which the GUI's Security
menu calls too (orchestrator/Core/secret_entries.py, SignIn.env_variable / SignIn.env_key): what is stored and what
is only kept for this run, the names with "stored" / "not stored" and never a value, and the key taken from the
provider's environment variable. No network."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from orchestrator import cli
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.secret_entries import (KIND_KNOWN, KIND_OTHER, KIND_PROVIDER, KNOWN_SECRETS, SecretNotStored,
                                              SecretsUnavailable, secret_entries, store_secret, stored_names)
from orchestrator.Core.secrets_manager import SecretsManager

OPENAI_KEY = "sk-" + "proj-" + "E" * 40
HF_TOKEN = "hf_" + "S" * 30


@pytest.fixture(autouse=True)
def fresh_keyring_probe():
    from orchestrator.Core import vault_key
    vault_key.reset_probe()
    yield
    vault_key.reset_probe()


@pytest.fixture
def home(tmp_path, monkeypatch, memory_keyring):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    return tmp_path / "state"


def open_vault(home):
    sm = SecretsManager(home)
    assert sm.initialize(), sm.storage_error
    return sm


class TestStoreSecret:

    def test_stores_any_name_and_says_it_was_saved(self, home):
        vault = open_vault(home)
        stored = store_secret(vault, "qdrant_api_key", "q-value-123")
        assert stored.name == "qdrant_api_key" and not stored.memory_only
        assert vault.get_secret("qdrant_api_key") == "q-value-123"
        store_secret(vault, "my_own_name", "v")  # any printable name, as the CLI allows
        assert vault.get_secret("my_own_name") == "v"

    def test_ephemeral_storage_is_reported_as_memory_only(self, tmp_path, home, monkeypatch):
        monkeypatch.setenv("LLM_SECRET_STORAGE", "ephemeral")
        vault = SecretsManager(SimpleNamespace(config=ConfigManager(tmp_path / "cfg")))
        assert vault.initialize() and vault.storage_mode == "ephemeral"
        stored = store_secret(vault, "hf_token", HF_TOKEN)
        assert stored.memory_only

    def test_a_refused_name_raises_without_the_value(self, home):
        vault = open_vault(home)
        with pytest.raises(SecretNotStored) as raised:
            store_secret(vault, "two\nlines", HF_TOKEN)
        assert HF_TOKEN not in str(raised.value) and raised.value.name == "two\nlines"
        with pytest.raises(SecretsUnavailable):
            store_secret(None, "hf_token", HF_TOKEN)

    def test_the_cli_says_the_same_as_before(self, home, monkeypatch):
        vault = open_vault(home)

        def require(self):
            self.framework_manager = SimpleNamespace(secrets_manager=vault)
            return self

        monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
        result = CliRunner().invoke(cli.cli, ["-q", "secrets", "set", "hf_token", "--with-token"], input=HF_TOKEN)
        assert result.exit_code == 0 and "stored in the encrypted credential vault" in result.output
        monkeypatch.setattr(vault, "set_secret", lambda *a: False)
        result = CliRunner().invoke(cli.cli, ["-q", "secrets", "set", "hf_token", "--with-token"], input=HF_TOKEN)
        assert result.exit_code == 1 and "Failed to store secret 'hf_token'" in result.output
        monkeypatch.setattr(type(vault), "storage_mode", property(lambda self: "ephemeral"))
        monkeypatch.setattr(vault, "set_secret", lambda *a: True)
        result = CliRunner().invoke(cli.cli, ["-q", "secrets", "set", "hf_token", "--with-token"], input=HF_TOKEN)
        assert result.exit_code == 0 and "nothing was saved" in " ".join(result.output.split())
        result = CliRunner().invoke(cli.cli, ["-q", "secrets", "list"])
        assert result.exit_code == 0 and "hf_token" in result.output and HF_TOKEN not in result.output


class TestSecretEntries:

    def test_known_names_first_with_stored_or_not_then_every_stored_name(self, home):
        vault = open_vault(home)
        vault.set_secret("hf_token", HF_TOKEN)
        vault.set_secret("openai_api_key", OPENAI_KEY)  # an old provider key name: listed under its vault name
        vault.set_secret("github_token", "g")
        entries = secret_entries(vault)
        assert [e.name for e in entries[:len(KNOWN_SECRETS)]] == list(KNOWN_SECRETS)
        by_name = {e.name: e for e in entries}
        assert by_name["hf_token"].stored and by_name["hf_token"].kind == KIND_KNOWN
        assert not by_name["qdrant_api_key"].stored
        provider = by_name["cred/openai/api_key"]
        assert provider.kind == KIND_PROVIDER and provider.provider == "openai" and provider.stored
        assert by_name["github_token"].kind == KIND_OTHER and by_name["github_token"].stored
        assert stored_names(vault) == vault.list_secrets()
        text = repr(entries) + repr([e.to_dict() for e in entries])
        assert HF_TOKEN not in text and OPENAI_KEY not in text

    def test_the_pack_key_names_match_the_community_packs(self):
        community_pack = pytest.importorskip("orchestrator.Core.community_pack")
        assert community_pack.UserKeys.SIGNING_SECRET in KNOWN_SECRETS
        assert community_pack.UserKeys.AGE_SECRET in KNOWN_SECRETS

    def test_every_known_name_has_its_text_in_both_languages(self):
        from orchestrator.utils.l10n_signin import TEXTS
        for what in KNOWN_SECRETS.values():
            assert TEXTS["en"][what] and TEXTS["de"][what]


class TestEnvKey:

    def service(self, tmp_path, vault):
        from orchestrator.llm.auth.signin import SignIn
        return SignIn(ConfigManager(tmp_path / "cfg"), vault)

    def test_the_first_set_variable_and_its_key(self, tmp_path, home, monkeypatch):
        from orchestrator.llm.auth.signin import EnvKeyError
        service = self.service(tmp_path, open_vault(home))
        preset = service.catalog.get("openai")
        spec = preset.method("api_key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert service.env_variable(spec) is None
        with pytest.raises(EnvKeyError) as raised:
            service.env_key(preset, spec)
        assert raised.value.code == "not_set" and raised.value.variables == ["OPENAI_API_KEY"]
        monkeypatch.setenv("OPENAI_API_KEY", f"  {OPENAI_KEY}\n")
        assert service.env_variable(spec) == "OPENAI_API_KEY"
        assert service.env_key(preset, spec) == (OPENAI_KEY, "OPENAI_API_KEY")

    def test_a_malformed_value_and_a_method_without_a_variable(self, tmp_path, home, monkeypatch):
        from orchestrator.llm.auth.signin import EnvKeyError
        service = self.service(tmp_path, open_vault(home))
        preset = service.catalog.get("openai")
        monkeypatch.setenv("OPENAI_API_KEY", "two words")
        with pytest.raises(EnvKeyError) as raised:
            service.env_key(preset, preset.method("api_key"))
        assert raised.value.code == "malformed" and raised.value.name == "OPENAI_API_KEY"
        assert "two words" not in str(raised.value)
        ollama = service.catalog.get("ollama")
        with pytest.raises(EnvKeyError) as raised:
            service.env_key(ollama, ollama.methods[0])
        assert raised.value.code == "no_variable"

    def test_the_cli_keeps_its_words(self, tmp_path, home, monkeypatch):
        def run(*args):
            return CliRunner().invoke(cli.cli, ["-q", *args], obj=cli.FrameworkContext())

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        result = run("ditto", "login", "openai", "--from-env", "--no-check")
        assert result.exit_code == 2 and "OPENAI_API_KEY is not set" in " ".join(result.output.split())
        monkeypatch.setenv("OPENAI_API_KEY", "two words")
        result = run("ditto", "login", "openai", "--from-env", "--no-check")
        assert result.exit_code == 2 and "OPENAI_API_KEY has a space" in " ".join(result.output.split())
        monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
        result = run("ditto", "login", "openai", "--from-env", "--no-check")
        assert result.exit_code == 0 and OPENAI_KEY not in result.output
        assert open_vault(home).get_record("cred/openai/api_key")["secret"] == OPENAI_KEY
