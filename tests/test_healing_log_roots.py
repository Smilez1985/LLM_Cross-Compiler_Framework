"""The healing prompt names the framework's own folders by label (chain followups-3): llm/task_prompts.log_for_ai
takes the folder labels as `roots` and hands them to security/paths.scrub_for_ai, so a path in the framework's
output, build or installation folder reaches the AI as 'output:<relative path>' and any other path by its file name
(label_paths), as every other AI reader of a build log already has it (BuildService._log_paths). Ditto's healing
analysis passes the labels of its framework (Core/mojo/views.ai_folder_labels); llm/ never imports the Core."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.llm.task_prompts import healing_parts, log_for_ai

FAKE_HOME = "/home/example-user"
INSTALL = "/opt/example/llmcf"
OUTPUT = "/srv/example/llmcf-output"


@pytest.fixture
def fake_home(monkeypatch):
    monkeypatch.setenv("HOME", FAKE_HOME)
    monkeypatch.setenv("USERPROFILE", FAKE_HOME)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: Path(FAKE_HOME)))


LOG = (f"cc -c {INSTALL}/modules/rockchip/build.sh\n"
       f"error: cannot write {OUTPUT}/run-1/model.gguf\n"
       "error: see /var/tmp/example-builder/cmake.log")


class TestLogForAi:
    def test_known_folders_by_label_and_other_paths_by_file_name(self, fake_home):
        text = log_for_ai(LOG, roots={INSTALL: "framework", OUTPUT: "output"})
        assert "framework:modules/rockchip/build.sh" in text
        assert "output:run-1/model.gguf" in text
        assert "cmake.log" in text and "/var/tmp" not in text and "example-builder" not in text
        assert INSTALL not in text and OUTPUT not in text

    def test_without_roots_the_text_is_as_before(self, fake_home):
        assert log_for_ai(f"error: {FAKE_HOME}/models/m.gguf") == "error: ~/models/m.gguf"

    def test_healing_parts_hands_the_roots_on(self, fake_home):
        part = next(p for p in healing_parts(LOG, roots={OUTPUT: "output"}) if p.startswith("Build log"))
        assert "output:run-1/model.gguf" in part and OUTPUT not in part


def test_ditto_healing_passes_its_framework_folder_labels(tmp_path, fake_home, monkeypatch):
    from orchestrator.Core.ditto_manager import DittoCoder, HealingAnalysis

    class Config:
        def get(self, key, default=None):
            return {"output_dir": OUTPUT, "ai_provider": "ollama"}.get(key, default)

    prompts = []

    def structured(self, task, system, prompt, model):
        prompts.append(prompt)
        return HealingAnalysis(summary="s", root_cause="r", fix_command="", confidence=0.5, target="HOST")

    monkeypatch.setattr(DittoCoder, "_structured", structured)
    monkeypatch.setattr(DittoCoder, "_knowledge", lambda self, *a, **k: "")
    framework = SimpleNamespace(config=Config(), app_root=INSTALL)
    DittoCoder(framework, config_manager=Config(), client=object()).analyze_error_log(LOG, "")

    assert "framework:modules/rockchip/build.sh" in prompts[0] and "output:run-1/model.gguf" in prompts[0]
    assert INSTALL not in prompts[0] and OUTPUT not in prompts[0]


def test_the_log_path_in_llm_never_imports_the_core():
    """The labels come in as data; the older allowlisted edges of llm/ are guarded by tests/test_layering.py."""
    path = Path(__file__).resolve().parent.parent / "orchestrator" / "llm" / "task_prompts.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("log_for_ai", "healing_parts")]
    assert len(funcs) == 2
    for func in funcs:
        for node in ast.walk(func):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                assert not any(n.startswith("orchestrator.Core") for n in names), func.name
