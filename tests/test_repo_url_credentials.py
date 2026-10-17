"""A source repository URL never takes a credential (maintainer, wave 5): `llm-cli config repos --add` and File >
Settings... > Add refuse a URL with a password or token in it through one core function (settings_view.url_credential
inside add_repo_override) and name the secrets store; a URL stored with one before keeps working and is only ever shown
redacted (settings_view.shown_repo_url)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

AT = "@"
PASSWORD = "FAKEpassword123"
GH_TOKEN = "ghp_" + "A1b2C3d4" * 4 + "Ef"          # the GitHub token format (36 characters after the prefix)
LONG_TOKEN = "x9Y8" * 10                            # an opaque token used as the user name
WITH_PASSWORD = "https://builder:" + PASSWORD + AT + "git.example.com/llama.cpp.git"
SHORT_PASSWORD = "https://builder:pw1" + AT + "git.example.com/llama.cpp.git"
TOKEN_AS_USER = "https://" + GH_TOKEN + AT + "git.example.com/o/r.git"
OPAQUE_USER = "https://" + LONG_TOKEN + AT + "git.example.com/o/r.git"
TOKEN_PARAMETER = "https://git.example.com/o/r.git?private_token=" + "secretvalue42"
SCP_PASSWORD = "builder:" + PASSWORD + AT + "git.example.com:o/r.git"
CLEAN = ("https://git.example.com/ggml-org/llama.cpp.git", "ssh://deploy" + AT + "git.example.com/o/r.git",
         "deploy" + AT + "git.example.com:o/r.git", "https://git.example.com/o/r.git?ref=main",
         "file:///srv/mirror/llama.cpp")


@pytest.mark.parametrize("url, kind", [(WITH_PASSWORD, "password"), (SHORT_PASSWORD, "password"),
                                       (TOKEN_AS_USER, "token"), (OPAQUE_USER, "token"),
                                       (TOKEN_PARAMETER, "parameter"), (SCP_PASSWORD, "password")])
def test_a_credential_in_the_url_is_found(url, kind):
    from orchestrator.Core.settings_view import shown_repo_url, url_credential
    assert url_credential(url) == kind
    shown = shown_repo_url(url)
    for secret in (PASSWORD, "pw1", GH_TOKEN, LONG_TOKEN, "secretvalue42"):
        assert secret not in shown, shown
    assert "REDACTED" in shown and "git.example.com" in shown


@pytest.mark.parametrize("url", CLEAN)
def test_a_plain_address_or_account_name_is_no_credential(url):
    from orchestrator.Core.settings_view import shown_repo_url, url_credential
    assert url_credential(url) is None
    assert shown_repo_url(url) == url


def test_the_core_refuses_and_saves_nothing(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.settings_view import (CREDENTIAL_IN_URL, RepoOverrideError, add_repo_override,
                                                 repo_overrides)
    cfg = ConfigManager(tmp_path / "cfg")
    for url, kind in ((WITH_PASSWORD, "password"), (TOKEN_AS_USER, "token"), (TOKEN_PARAMETER, "parameter")):
        with pytest.raises(RepoOverrideError) as refused:
            add_repo_override(cfg, "llama.cpp", url)
        assert refused.value.code == CREDENTIAL_IN_URL and refused.value.kind == kind
        message = str(refused.value)
        assert "secrets store" in message and "llm-cli secrets set" in message
        assert PASSWORD not in message and GH_TOKEN not in message and "secretvalue42" not in message
    assert repo_overrides(cfg) == {} and not (tmp_path / "cfg" / "config.yml").exists(), "nothing saved"
    assert add_repo_override(cfg, "llama.cpp", CLEAN[0]) == "LLAMA_CPP_REPO_OVERRIDE", "a clean URL is added"


def test_a_url_stored_before_keeps_working_and_is_shown_redacted(tmp_path):
    from orchestrator.Core.builder import BuildEngine
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.settings_view import effective_settings, repo_overrides, shown_repo_url
    folder = tmp_path / "cfg"
    folder.mkdir()
    (folder / "config.yml").write_text(yaml.safe_dump({"source_repositories": {
        "llama_cpp": {"url": TOKEN_PARAMETER}, "old": WITH_PASSWORD}}), encoding="utf-8")
    cfg = ConfigManager(folder)
    assert repo_overrides(cfg) == {"llama_cpp": TOKEN_PARAMETER, "old": WITH_PASSWORD}, "it still reads"
    engine = BuildEngine.__new__(BuildEngine)   # only its reading of the setting, no Docker
    engine._get_conf = lambda key, default=None: cfg.get(key, default)
    assert engine._repo_overrides()["LLAMA_CPP_REPO_OVERRIDE"] == TOKEN_PARAMETER, "builds still receive it"
    shown = json.dumps(effective_settings(cfg)["source_repositories"])
    assert "secretvalue42" not in shown and PASSWORD not in shown and "private_token=REDACTED" in shown
    assert shown_repo_url(WITH_PASSWORD) == "https://builder:REDACTED" + AT + "git.example.com/llama.cpp.git"


class _Boxes:
    def __init__(self, monkeypatch):
        from orchestrator.gui import settings_window
        self.texts = []
        monkeypatch.setattr(settings_window, "plain_box",
                            lambda parent, kind, title, text, **k: self.texts.append((kind, text)))


def test_the_cli_refuses_with_the_secrets_store_and_lists_redacted(tmp_path, monkeypatch):
    import io

    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module
    from orchestrator.Core.config_manager import ConfigManager
    cfg = ConfigManager(tmp_path / "cfg")
    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=500, color_system=None))
    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = SimpleNamespace(config=cfg)
            return self

    result = CliRunner().invoke(cli_module.cli, ["-q", "config", "repos", "--add", "llama.cpp", WITH_PASSWORD],
                                obj=Ctx())
    out = printed.getvalue() + result.output
    assert result.exit_code == 2, out
    assert "llm-cli secrets set" in out and "secrets store" in out and PASSWORD not in out
    assert cfg.get("source_repositories") in ({}, None)
    cfg.set("source_repositories", {"old": {"url": TOKEN_PARAMETER}})
    printed.truncate(0)
    printed.seek(0)
    result = CliRunner().invoke(cli_module.cli, ["-q", "config", "repos"], obj=Ctx())
    out = printed.getvalue() + result.output
    assert result.exit_code == 0 and "private_token=REDACTED" in out and "secretvalue42" not in out


def test_the_gui_refuses_with_security_secrets_in_both_languages(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui.settings_window import SettingsDialog
    folder = tmp_path / "cfg"
    folder.mkdir()
    (folder / "config.yml").write_text(yaml.safe_dump({"source_repositories": {"old": {"url": OPAQUE_USER}}}),
                                       encoding="utf-8")
    framework = SimpleNamespace(config=ConfigManager(folder))
    # Wave 6 (Q2, V2): the path is filled from the live, translated menu labels, so the German text names "Sicherheit"
    # (it said "Security > Geheimnisse..." before, with an untranslated menu title).
    for lang, where in (("en", "Security > Secrets..."), ("de", "Sicherheit > Geheimnisse...")):
        boxes = _Boxes(monkeypatch)
        dialog = SettingsDialog(framework, lang=lang)
        try:
            table = dialog.table_repos
            shown = [[table.item(r, c).text() for c in range(3)] for r in range(table.rowCount())]
            assert shown == [["old", "https://REDACTED" + AT + "git.example.com/o/r.git", "OLD_REPO_OVERRIDE"]]
            dialog.edit_repo_name.setText("llama.cpp")
            dialog.edit_repo_url.setText(TOKEN_AS_USER)
            dialog.btn_repo_add.click()
            kind, text = boxes.texts[-1]
            assert kind == "warning" and where in text and GH_TOKEN not in text
            assert "llama.cpp" not in framework.config.get("source_repositories"), "nothing saved"
            saved = yaml.safe_load((Path(folder) / "config.yml").read_text(encoding="utf-8"))
            assert list(saved["source_repositories"]) == ["old"]
        finally:
            dialog.close()
