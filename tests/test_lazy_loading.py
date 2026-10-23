"""Lazy loading (maintainer decision RG1): the CLI, the MCP server and the GUI start without the heavy libraries and
framework parts they do not use yet. Each check runs in a fresh interpreter and reads sys.modules there, so it does
not depend on timing or on what earlier tests imported."""

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

# Third-party libraries that are expensive to import and only needed by some commands.
HEAVY_LIBRARIES = ("fastembed", "onnxruntime", "qdrant_client", "qdrant_edge", "tokenizers", "huggingface_hub",
                   "docker", "paramiko", "anthropic", "openai", "PySide6", "bs4", "pypdf", "langdetect", "PIL", "numpy")
# Framework parts that pull in those libraries or much code of their own. The knowledge base's storage (Qdrant Edge,
# qdrant-client for a server and the old storage, its conversion) loads when it is first used, also when the
# knowledge base is on.
HEAVY_PARTS = ("orchestrator.Core.framework", "orchestrator.Core.builder", "orchestrator.Core.crawler_manager",
               "orchestrator.Core.rag_manager", "orchestrator.Core.ditto_manager", "orchestrator.llm.client",
               "orchestrator.Core.mojo", "orchestrator.rag.store", "orchestrator.rag.edge_store",
               "orchestrator.rag.client_store", "orchestrator.rag.migration")
# Wave 4a's parts load when they are first used too: the GUI bridge (the MCP server's `gui` tool reaches the open
# window through it at its first call) and Mojo's board check (with Mojo, only when Mojo runs; a board is contacted
# only on request, W1). The licence data of the knowledge packs is guarded by the files read (LICENCE_DATA below).
FIRST_USE_PARTS = ("orchestrator.tools.gui_bridge", "orchestrator.Core.mojo.checks.devices")
# The GUI starts the bridge's listener with its window: the GUI tools of Ditto and AI apps work while it is open (W4).
GUI_USES = ("orchestrator.tools.gui_bridge",)
# Loaded when the framework boots: the managers and the build engine, whose Docker reachability check uses the Docker
# SDK (which imports paramiko), and Ditto with its provider layer (not the provider SDKs), which self-healing needs.
BOOT_USES = ("docker", "paramiko", "orchestrator.Core.framework", "orchestrator.Core.builder",
             "orchestrator.Core.ditto_manager", "orchestrator.llm.client")

# The canonical licence texts and licenses.json (rag/licence_texts.py): read at the first export, install or check of
# a pack, never at start. The child records every file it opens below this folder (an audit hook sees each open).
LICENCE_DATA = os.path.join("orchestrator", "data", "licenses")

REPORT = r"""
import atexit, json, os, sys
_OUT = sys.argv[1]
_LICENCE_DATA = os.path.join("orchestrator", "data", "licenses")
_READ = []
def _audit(event, args):
    if event == "open" and args and not isinstance(args[0], int):
        try:
            path = os.path.normpath(os.fsdecode(args[0]))
        except (TypeError, ValueError):
            return
        if _LICENCE_DATA in path:
            _READ.append(path)
sys.addaudithook(_audit)
def _report():
    with open(_OUT + ".licence_data", "w", encoding="utf-8") as f:
        json.dump(_READ, f)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(sorted(sys.modules), f)
atexit.register(_report)
sys.argv = [sys.argv[0]] + sys.argv[2:]
"""


def environment(tmp_path, **extra):
    """The test's isolated environment (conftest) for a child interpreter that imports the framework."""
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), QT_QPA_PLATFORM="offscreen", LLM_SECRET_STORAGE="ephemeral",
               LLM_FRAMEWORK_HOME=str(tmp_path / "state"), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")
    env.update(extra)
    return env


def loaded_modules(tmp_path, code, *args, timeout=180):
    """The modules a fresh interpreter has loaded when `code` ends (the list is written at exit)."""
    out = tmp_path / "modules.json"
    run = subprocess.run([sys.executable, "-c", REPORT + code, str(out), *args], cwd=tmp_path,
                         env=environment(tmp_path), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                         timeout=timeout)
    assert out.exists(), run.stderr[-3000:]
    return set(json.loads(out.read_text(encoding="utf-8"))), run


def licence_data_read(tmp_path):
    """The files of the licence data the child opened (LICENCE_DATA), as it recorded them at exit."""
    return json.loads((tmp_path / "modules.json.licence_data").read_text(encoding="utf-8"))


def present(modules, names):
    """The names (a package or a module) that are loaded."""
    return sorted(name for name in names if name in modules or any(m.startswith(name + ".") for m in modules))


def test_importing_the_cli_loads_no_heavy_module(tmp_path):
    modules, _ = loaded_modules(tmp_path, "import orchestrator.cli")
    assert present(modules, HEAVY_LIBRARIES + HEAVY_PARTS + FIRST_USE_PARTS + ("rich", "mcp", "pydantic")) == []
    assert licence_data_read(tmp_path) == []


def test_cli_help_loads_no_heavy_module(tmp_path):
    modules, run = loaded_modules(tmp_path, "from orchestrator.cli import cli\ncli()", "--help")
    assert run.returncode == 0 and "Usage:" in run.stdout and "mcp" in run.stdout
    assert present(modules, HEAVY_LIBRARIES + HEAVY_PARTS + FIRST_USE_PARTS + ("rich", "mcp")) == []
    assert licence_data_read(tmp_path) == []


def test_importing_the_mcp_server_loads_no_heavy_module(tmp_path):
    modules, _ = loaded_modules(tmp_path, "import orchestrator.mcp_server")
    assert present(modules, HEAVY_LIBRARIES + HEAVY_PARTS + FIRST_USE_PARTS + ("mcp", "rich")) == []
    assert licence_data_read(tmp_path) == []


def served_modules(tmp_path, **extra_env):
    """`llm-cli mcp serve` over stdio: the framework boots, the server answers initialize, the client leaves. The
    modules the server had loaded at its end."""
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
    out = tmp_path / "modules.json"
    server = subprocess.Popen([sys.executable, "-c", REPORT + "from orchestrator.cli import cli\ncli()", str(out),
                               "-q", "mcp", "serve"], cwd=tmp_path, env=environment(tmp_path, **extra_env),
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        server.stdin.write(json.dumps(init) + "\n")
        server.stdin.flush()
        first = server.stdout.readline()  # the answer to initialize
        _, errors = server.communicate(timeout=120)  # closes stdin: the client leaves, the server ends
    finally:
        if server.poll() is None:
            server.kill()
    answer = json.loads(first)
    assert answer["id"] == 1 and answer["result"]["serverInfo"]["name"] == "llm-cross-compiler", errors[-3000:]
    modules = set(json.loads(out.read_text(encoding="utf-8")))
    assert present(modules, ("mcp",)) == ["mcp"], "the check would pass without a server"
    return modules


def test_mcp_serve_answers_initialize_without_the_heavy_modules(tmp_path):
    """Only what the boot itself uses is loaded (BOOT_USES); not the knowledge base, the crawler, the provider SDKs,
    Qt, Mojo's checks (its board check too) or the GUI bridge, and no licence text is read."""
    modules = served_modules(tmp_path)
    assert "orchestrator.tools.tools_gui" in modules, "the gui tool was not registered: the check would pass anyway"
    heavy = [name for name in HEAVY_LIBRARIES + HEAVY_PARTS + FIRST_USE_PARTS if name not in BOOT_USES]
    assert present(modules, heavy) == []
    assert licence_data_read(tmp_path) == []


def test_mcp_serve_with_the_knowledge_base_on_loads_neither_the_crawler_nor_the_embedding_libraries(tmp_path):
    """With the knowledge base switched on, the boot creates the RAG manager. The crawler (BeautifulSoup, pypdf,
    langdetect, Pillow) is needed only by a Deep Ingest, numpy, FastEmbed and the vector store only when a text is
    embedded or searched; none of them may load with the boot."""
    modules = served_modules(tmp_path, LLM_FRAMEWORK_ENABLE_RAG_KNOWLEDGE="true")
    assert "orchestrator.Core.rag_manager" in modules, "the knowledge base was not on: the check would pass anyway"
    heavy = [name for name in HEAVY_LIBRARIES + HEAVY_PARTS + FIRST_USE_PARTS
             if name not in BOOT_USES + ("orchestrator.Core.rag_manager",)]
    assert present(modules, heavy) == []
    assert licence_data_read(tmp_path) == [], "the licence texts are read with the first pack, not with the boot"


def test_the_gui_main_window_starts_without_the_heavy_modules(tmp_path):
    pytest.importorskip("PySide6")
    code = ("from PySide6.QtWidgets import QApplication\n"
            "app = QApplication([])\n"
            "from pathlib import Path\n"
            "from orchestrator.gui.main_window import MainOrchestrator\n"
            "MainOrchestrator._open_tool_permissions = lambda self, first_run=False: None\n"
            f"window = MainOrchestrator(Path({str(REPO_ROOT)!r}))\n"
            "app.processEvents()\n"
            "assert window.framework.tool_registry is not None\n"
            "window.close()\n")
    modules, run = loaded_modules(tmp_path, code)
    assert run.returncode == 0, run.stderr[-3000:]
    assert "PySide6" in {m.split(".")[0] for m in modules}
    heavy = [name for name in HEAVY_LIBRARIES + HEAVY_PARTS + FIRST_USE_PARTS
             if name not in BOOT_USES + GUI_USES + ("PySide6",)]
    assert present(modules, heavy + ["mcp"]) == []
    assert licence_data_read(tmp_path) == []


def test_the_licence_data_check_sees_a_read_and_the_gui_tool_loads_its_bridge_on_first_use(tmp_path):
    """The other direction, so the guards above cannot pass by not looking: the first use of the licence texts reads
    them (and the audit hook records it), and the first call of the `gui` tool loads the bridge (here it finds no
    window and says so)."""
    code = ("import sys\n"
            "from types import SimpleNamespace\n"
            "from orchestrator.rag import licence_texts\n"
            "assert licence_texts.list_version()\n"
            "from orchestrator.tools import tools_gui\n"
            "assert 'orchestrator.tools.gui_bridge' not in sys.modules\n"
            "access = tools_gui._BridgeAccess(SimpleNamespace(config=SimpleNamespace(user_state_dir=sys.argv[1])))\n"
            "try:\n"
            "    access.run(tools_gui.GuiIn.model_validate({'action': 'read_form'}), tools_gui.DITTO)\n"
            "    raise SystemExit('a window answered')\n"
            "except tools_gui.ToolError as e:\n"
            "    assert 'framework window' in str(e), e\n")
    modules, run = loaded_modules(tmp_path, code, str(tmp_path / "state"))
    assert run.returncode == 0, run.stderr[-3000:]
    read = licence_data_read(tmp_path)
    assert any(path.endswith("licenses.json") for path in read) and all(LICENCE_DATA in p for p in read), read
    assert "orchestrator.tools.gui_bridge" in modules


def test_the_knowledge_base_is_still_created_once_it_is_enabled(monkeypatch):
    """The other direction: lazy loading must not lose a part. The RAG manager (imported on first use) is created as
    soon as the knowledge base is switched on."""
    from orchestrator.Core import framework as framework_module
    created = []

    class FakeRag:
        def __init__(self, framework):
            created.append(framework)

    monkeypatch.setattr(framework_module, "RAGManager", FakeRag)
    fm = framework_module.FrameworkManager()
    assert fm.ensure_rag_manager() is None and created == []
    assert fm.config.set("enable_rag_knowledge", True)
    assert isinstance(fm.ensure_rag_manager(), FakeRag) and created == [fm]


def test_deep_ingest_without_the_crawler_still_says_so(monkeypatch):
    """The crawler is imported with the first Deep Ingest. When it cannot be imported, the answer is the one of a
    knowledge base that imported it at start: "Crawler module missing", nothing crawled."""
    from types import SimpleNamespace
    from orchestrator.Core import rag_manager as rag_module
    real = rag_module.CrawlerManager  # resolved now, so the end of the test puts the real class back
    assert real is not None and real.__name__ == "CrawlerManager"
    monkeypatch.delattr(rag_module, "CrawlerManager")
    monkeypatch.setitem(sys.modules, "orchestrator.Core.crawler_manager", None)  # its import fails
    rag = rag_module.RAGManager(SimpleNamespace(config=None, info=None))
    rag._connect = lambda: pytest.fail("the knowledge base was opened without a crawler")
    result = rag.ingest_url("https://docs.example/")
    assert result == {"success": False, "outcome": "failure", "message": "Crawler module missing"}
    assert rag_module.CrawlerManager is None


def test_a_framework_part_that_cannot_be_imported_stops_the_boot_with_its_cause(tmp_path):
    """The managers are imported when the framework boots. A broken installation (here: the Docker SDK cannot be
    imported) ends the boot like every other boot error: initialize() is False and init_error names the cause, so the
    CLI prints it and exits with 1 and the GUI shows it, instead of a traceback."""
    code = ("import sys\n"
            "sys.modules['docker'] = None  # its import fails\n"
            "from orchestrator.Core.framework import FrameworkManager\n"
            "fm = FrameworkManager()\n"
            "assert fm.initialize() is False\n"
            "assert isinstance(fm.init_error, ImportError) and 'docker' in str(fm.init_error), fm.init_error\n"
            "from click.testing import CliRunner\n"
            "from orchestrator.cli import cli\n"
            "result = CliRunner().invoke(cli, ['-q', 'tools', 'list'])\n"
            "assert result.exit_code == 1, (result.exit_code, result.output)\n"
            "assert 'Failed to initialize framework' in result.output and 'docker' in result.output, result.output\n"
            "assert 'Traceback' not in result.output\n")
    _, run = loaded_modules(tmp_path, code)
    assert run.returncode == 0, run.stderr[-3000:]


# --- what the lazy names resolve to ---------------------------------------------------------------------------------

def test_lazy_names_are_the_real_objects():
    import orchestrator.cli as cli
    import orchestrator.Core.framework as framework
    from orchestrator.Core import build_formats, builder, module_generator
    from orchestrator.Core.framework import FrameworkManager
    assert cli.FrameworkManager is FrameworkManager and cli.ModuleGenerator is module_generator.ModuleGenerator
    assert builder.ModelFormat is build_formats.ModelFormat and builder.OptimizationLevel is build_formats.OptimizationLevel
    assert framework.build_tool_registry.__module__ == "orchestrator.tools.framework_tools"
    for name in framework._LAZY:
        assert getattr(framework, name).__name__ == name
    import importlib
    for name, source in cli._LAZY.items():  # a wrong module or name would only fail when a command runs
        assert getattr(cli, name) is getattr(importlib.import_module(source), name)
    with pytest.raises(AttributeError):
        cli.no_such_name  # noqa: B018
    with pytest.raises(AttributeError):
        framework.no_such_name  # noqa: B018


def test_build_format_choices_still_come_from_the_engine():
    """Changed on purpose by DECISIONS round 23 item 7: --format offers what the installed targets build (read when
    its help or completion is shown, never at import), no longer every format of the engine. What a target without
    capabilities is offered is still the engine's own list: every ModelFormat but hf (build_formats.ModelFormat is
    builder.ModelFormat)."""
    from orchestrator.cli import _TargetFormat, cli
    from orchestrator.Core.build_formats import unchecked_formats
    from orchestrator.Core.builder import ModelFormat
    option = next(p for p in cli.commands["build"].commands["start"].params if p.name == "format")
    assert isinstance(option.type, _TargetFormat)
    assert sorted(v for _, v in unchecked_formats()) == sorted(f.value for f in ModelFormat
                                                               if f is not ModelFormat.HUGGINGFACE)


def test_package_exports_resolve():
    import importlib
    import orchestrator.llm as llm
    import orchestrator.runtime as runtime
    for package in (llm, runtime):
        assert sorted(package.__all__) == sorted(n for names in package._SOURCES.values() for n in names)
        for module, names in package._SOURCES.items():
            for name in names:
                assert getattr(package, name) is getattr(importlib.import_module(module), name)
    from orchestrator.llm import LLMClient
    from orchestrator.llm.client import LLMClient as Direct
    assert LLMClient is Direct


def test_the_cli_console_is_made_on_first_use_and_live_displays_get_the_real_one(monkeypatch):
    """The commands' console is a stand-in until the first print; a live display (download progress) enters its
    console with `with`, which a stand-in cannot forward, so it gets rich's Console itself."""
    from rich.console import Console
    import orchestrator.cli as cli
    monkeypatch.setattr(cli, "console", cli._SharedConsole())
    assert cli.console._console is None
    bar, _update = cli._download_progress()
    assert isinstance(bar.live.console, Console) and bar.live.console is cli.console._console
    replaced = Console(width=80)
    monkeypatch.setattr(cli, "console", replaced)  # a test's own console is used as it is
    assert cli._download_progress()[0].live.console is replaced


def test_mcp_token_variable_has_one_name():
    from orchestrator.tools import mcp_env, mcp_tokens
    assert mcp_tokens.ENV_VAR == mcp_env.ENV_VAR == "LCF_MCP_TOKEN"


# --- the Docker manager needs no Qt ---------------------------------------------------------------------------------

def test_the_docker_manager_needs_no_qt(tmp_path):
    """Changed on purpose (round 12, item 6): its Qt signals were removed with its old build path, so the manager
    never loads Qt (tests/test_docker_manager.py has what took their place)."""
    code = ("import sys\n"
            "from orchestrator.Core.docker_manager import DockerManager, container_resources\n"
            "manager = DockerManager()\n"
            "container_resources({'memory_stats': {'usage': 1, 'limit': 2}})\n"
            "assert 'PySide6' not in sys.modules, 'Qt loaded with the manager'\n")
    modules, run = loaded_modules(tmp_path, code)
    assert run.returncode == 0, run.stderr[-3000:]
    assert present(modules, ("PySide6",)) == []


def test_the_key_storage_names_load_no_file_lock_library(tmp_path):
    """Review fix (consolidation chain c1-imports): Core/vault_key is imported for its storage names by the CLI's
    secret commands, the sign-in and the GUI start. The audit key that moved there from framework_tools brought a
    module-level import of the file lock (the filelock package, which loads asyncio and sqlite3); only the key's
    get-or-create needs it, so it is imported there, as framework_tools' import was only paid by the tool registry."""
    code = ("import sys\n"
            "from orchestrator.Core.vault_key import STORAGE_CHOICES, StorageError, audit_key\n"
            "assert 'filelock' not in sys.modules, 'the lock library loaded with the storage names'\n")
    modules, run = loaded_modules(tmp_path, code)
    assert run.returncode == 0, run.stderr[-3000:]
    assert present(modules, ("filelock", "orchestrator.utils.file_lock")) == []


def test_the_command_lines_sizes_need_no_qt(tmp_path):
    """Review fix (wave 6, f-words): deletion.size_text, used by the command line, Mojo and the board steps, went
    through the GUI's language tables (localization imports Qt) and so loaded Qt into llmcf and the MCP server."""
    code = ("import sys\n"
            "from orchestrator.Core.deletion import size_text\n"
            "from orchestrator.utils.sizes import format_size\n"
            "assert size_text(1_500_000_000) == '1.5 GB' and size_text(532) == '532 bytes'\n"
            "assert format_size(532, 'de') == '532 Byte'\n"
            "assert format_size(1_500, 'de', exact=True) == '2 kB (1.500 Byte)'\n"
            "assert 'PySide6' not in sys.modules, 'Qt loaded for a size'\n"
            "assert 'orchestrator.utils.localization' not in sys.modules\n")
    modules, run = loaded_modules(tmp_path, code)
    assert run.returncode == 0, run.stderr[-3000:]
    assert present(modules, ("PySide6",)) == []


def test_the_target_tools_load_no_tool_hub(tmp_path):
    """Stage-B integration (open item of the cleanup chain): the target tool module took TargetIn and
    TargetCapabilitiesOut from framework_tools, so importing it (module create in the CLI, the GUI wizard's save,
    target_marks of the deploy tools) loaded the whole tool hub with the HF, knowledge, Mojo and status tools and the
    job service. TargetIn lives in schemas/targets.py, TargetCapabilitiesOut in the target tool module itself;
    framework_tools keeps the names."""
    code = ("import sys\n"
            "from orchestrator.tools.tools_targets import TargetCheckIn, TargetDetailsOut, target_marks\n"
            "from orchestrator.Core.module_generator import record_module_saved\n")
    modules, run = loaded_modules(tmp_path, code)
    assert run.returncode == 0, run.stderr[-3000:]
    assert present(modules, ("orchestrator.tools.framework_tools", "orchestrator.tools.tools_hf",
                             "orchestrator.tools.tools_knowledge", "orchestrator.tools.tools_mojo",
                             "orchestrator.Core.job_service")) == []


# --- the former import-time host check of utils.validation -------------------------------------------------------

def test_validation_import_checks_nothing_about_the_host(tmp_path):
    """Importing the module once checked the host (Python, memory, disk, platform; earlier also a one-second CPU
    sample and two command lookups) and logged what it found. Round 12 removed that check from the import: Mojo
    reports the Python version (on validate_python_version) and the free disk space (decision A4), and every build
    plan judges the RAM it needs (disk_plan.host_fit). The import now takes no sample, starts no process and logs
    nothing, even on a host with almost no free disk space."""
    code = ("import psutil, subprocess\n"
            "calls = []\n"
            "psutil.cpu_percent = lambda *a, **k: calls.append('cpu') or 0.0\n"
            "psutil.virtual_memory = lambda *a, **k: calls.append('memory')\n"
            "real_disk = psutil.disk_usage\n"
            "psutil.disk_usage = lambda path: calls.append('disk') or real_disk(path)._replace(free=1024 ** 3)\n"
            "real_run = subprocess.run\n"
            "subprocess.run = lambda args, *a, **k: calls.append(list(args)[:1]) or real_run(args, *a, **k)\n"
            "import orchestrator.utils.validation as v\n"
            "assert calls == [], calls\n"
            "assert not hasattr(v, '_init_validation')\n")
    _, run = loaded_modules(tmp_path, code)
    assert run.returncode == 0, run.stderr[-3000:]
    assert "Validation Warning on Import" not in run.stderr
    alone = tmp_path / "alone"
    alone.mkdir()
    modules, run = loaded_modules(alone, "import orchestrator.utils.validation")
    assert run.returncode == 0, run.stderr[-3000:]
    assert present(modules, ("psutil",)) == [], "psutil is loaded by the two usage checks that need it, not at import"


def test_llama_pin_reads_the_same_values_with_either_loader(monkeypatch):
    """The pin is read with libyaml's safe loader at every start; PyYAML without libyaml reads the same values."""
    import yaml
    from orchestrator.utils import llama_pin
    with open(llama_pin.CATALOG_PATH, encoding="utf-8") as f:
        source = yaml.safe_load(f)["llama_cpp"]["source"]
    expected = {"tag": source["tag"], "commit": source["commit"]}
    assert llama_pin.pinned_source() == expected
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    assert llama_pin._read.__wrapped__(str(llama_pin.CATALOG_PATH), 0) == expected
