"""Each task in one place (maintainer, 2026-11-26, recon: "6. ja"): a second implementation beside the first is folded
into it. These checks keep it so: what is retried is the errors' own flag, sizes come from utils/sizes.format_size,
the per-user folder from disk_plan.state_dir, the Podman line from docker_engine.podman_note, one one_line per layer,
and the download options per project from recipe.download_options_off.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent


def test_the_retry_follows_the_errors_own_flag(monkeypatch):
    from orchestrator.llm import client as client_module
    from orchestrator.llm.errors import AuthFailed, LLMError, ProviderUnavailable, RateLimited
    assert RateLimited.retryable and ProviderUnavailable.retryable and not AuthFailed.retryable
    assert not LLMError.retryable

    class Flaky(LLMError):
        retryable = True   # a new kind of error that can pass: retried because it says so, no list to extend

    calls, sleeps = [], []

    class Adapter:
        def chat(self, request, model):
            calls.append(model)
            if len(calls) == 1:
                raise Flaky("later")
            return SimpleNamespace(text="ok")
    client = client_module.LLMClient.__new__(client_module.LLMClient)
    client.adapter = lambda target: Adapter()
    client._setting = lambda key, default=None: default
    client._calibrate = lambda *a: None
    client._restore = lambda result, mapping, report: result
    client._backoff = lambda attempt: 0.5
    client.sleep = sleeps.append
    client.logger = SimpleNamespace(info=lambda *a: None)
    target = SimpleNamespace(local=True, model="m", provider="p")
    assert client._call(target, object()).text == "ok" and calls == ["m", "m"] and sleeps == [0.5]
    source = (REPO / "orchestrator/llm/client.py").read_text(encoding="utf-8")
    assert "except (RateLimited, ProviderUnavailable)" not in source


def _defined(path: str, name: str) -> bool:
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    return any(isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name for n in ast.walk(tree))


def test_sizes_have_one_formatter():
    """Core/deletion.size_text is the one entry into utils/sizes.format_size; the knowledge snapshots and the chat
    panel had a formatter of their own (other units)."""
    assert not _defined("orchestrator/Core/knowledge_snapshots.py", "size_text")
    assert not _defined("orchestrator/gui/chat_panel/panel.py", "size_text")
    from orchestrator.Core.deletion import size_text
    from orchestrator.utils.sizes import format_size
    assert size_text(1_500_000_000) == format_size(1_500_000_000, "en") == "1.5 GB"


def test_the_per_user_folder_has_one_reader(tmp_path):
    from orchestrator.Core.disk_plan import state_dir
    from orchestrator.Core.mojo.repairs import state as repair_state
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path)))
    assert repair_state.state_dir(framework) == state_dir(framework) == tmp_path
    assert repair_state.state_dir(SimpleNamespace()) == Path.home() / ".llm-framework"
    for path in ("orchestrator/Core/mojo/repairs/state.py", "orchestrator/gui/host_key.py"):
        assert "disk_plan import state_dir" in (REPO / path).read_text(encoding="utf-8"), path


def test_the_podman_line_is_one_sentence():
    from orchestrator.utils.docker_engine import podman_note
    assert "podman_note()" in (REPO / "orchestrator/Core/mojo/checks/docker.py").read_text(encoding="utf-8")
    assert podman_note().startswith("The container engine is Podman: ")


def test_one_one_line_in_the_core():
    assert not _defined("orchestrator/Core/community_maintainer.py", "one_line")
    from orchestrator.Core import community_maintainer, feed_words
    assert community_maintainer.one_line is feed_words.one_line
    assert "\n" not in feed_words.one_line("a\nb") and feed_words.one_line("a\nb").startswith("a")


def test_the_download_options_per_project_come_from_one_choice():
    from orchestrator.runtime.build import projects, recipe
    assert recipe.download_options_off(projects.WHISPER_CPP) is recipe.WHISPER_DOWNLOAD_OPTIONS_OFF
    assert recipe.download_options_off(None) is recipe.DOWNLOAD_OPTIONS_OFF
    source = (REPO / "orchestrator/runtime/build/recipe.py").read_text(encoding="utf-8")
    assert source.count("download_options_off(project)") == 2, "the build's options and the check take it"
