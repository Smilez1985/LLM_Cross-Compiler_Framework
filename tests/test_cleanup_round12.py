"""Round 12 cleanup (recon first, one implementation): what the removals and rewirings of the unwired audit keep
working. Each block names the audit item; the characterization tests were written before the change and pass on
both sides of it."""

import os
import sys
import types

import pytest


# --- item 10: Mojo's Python check runs on the original version check of utils/validation.py -------------------------

def _python_check(monkeypatch, info):
    from orchestrator.Core.mojo.checks import python as python_check
    monkeypatch.setattr(python_check, "sys", types.SimpleNamespace(version_info=info, executable=sys.executable))
    return python_check.check_python(None)


@pytest.mark.parametrize("info", [(3, 11, 0, "final", 0), (3, 13, 1, "final", 0), (3, 14, 12, "final", 0)])
def test_mojo_python_check_accepts_3_11_to_3_14(monkeypatch, info):
    (finding,) = _python_check(monkeypatch, info)
    assert (finding.subsystem, finding.code, finding.severity.value) == ("python", "python.version", "ok")
    assert finding.message == f"Python {info[0]}.{info[1]}"


@pytest.mark.parametrize("info", [(3, 10, 14, "final", 0), (3, 15, 0, "alpha", 1), (4, 0, 0, "final", 0)])
def test_mojo_python_check_refuses_other_versions_with_the_same_texts(monkeypatch, info):
    (finding,) = _python_check(monkeypatch, info)
    assert (finding.code, finding.severity.value, finding.cannot) == ("python.version", "error", "admin")
    assert finding.message == f"Python {info[0]}.{info[1]} is not supported."
    assert finding.fix_hint == "Use Python 3.11 to 3.14 (e.g. 'uv python install 3.13')."
    assert finding.where == sys.executable
    assert finding.why == "The framework is made and tested for Python 3.11 to 3.14; other versions can fail anywhere."


def test_the_original_python_check_judges_the_supported_range():
    from orchestrator.utils.validation import ValidationSeverity, validate_python_version
    ok = validate_python_version("3.11", below="3.15", current="3.13.2")
    assert ok and ok.severity == ValidationSeverity.SUCCESS and ok.message == "Python version OK: 3.13.2 >= 3.11 and < 3.15"
    old = validate_python_version("3.11", below="3.15", current="3.10.14")
    assert not old and old.message == "Python version too old: 3.10.14 < 3.11"
    for newer in ("3.15.0", "3.15.0a1", "4.0"):
        result = validate_python_version("3.11", below="3.15", current=newer)
        assert not result and result.message == f"Python version too new: {newer} >= 3.15"
    # Without an upper bound it is the original minimum check, on this interpreter by default.
    here = validate_python_version("3.8.0")
    assert here and here.message.startswith("Python version OK: ") and here.message.endswith(" >= 3.8.0")
    assert "below" not in here.details


def test_validation_keeps_the_ssrf_guard_and_drops_only_duplicates():
    """The functions callers and tests use stay at their path (tests monkeypatch validate_public_url there); the ones
    newer code does better are gone (round 12 item 10, see the changelog for each replacement)."""
    from orchestrator.utils import validation
    for kept in ("validate_public_url", "is_public_ip", "resolve_public_addresses", "_idna_host", "validate_ip_address",
                 "ValidationResult", "ValidationSeverity", "ValidationType", "validate_python_version",
                 "validate_path", "validate_email", "ValidationError"):
        assert hasattr(validation, kept), kept
    for gone in ("validate_system_requirements", "validate_docker_environment", "create_comprehensive_validation_report",
                 "validate_config", "validate_directory_writable", "validate_url", "ValidationReport",
                 "SystemRequirements", "get_validation_info", "_validate_disk_space", "_validate_docker_daemon",
                 "_validate_command_availability", "_validate_docker_image_name", "_validate_target_name"):
        assert not hasattr(validation, gone), gone


# --- item 10: one Docker check for the build engine and Mojo (the build engine's original of 2025) -------------------

class _Client:
    def __init__(self, error=None):
        self.error = error

    def ping(self):
        if self.error:
            raise self.error
        return True


class _Framework:
    def __init__(self, client):
        self.client = client

    def get_component(self, name):
        return self.client if name == "docker_client" else None


def _docker_host(monkeypatch, docker_on_path=True, buildx=0):
    import shutil
    import subprocess
    runs = []

    def run(args, *a, **k):
        runs.append(list(args))
        if isinstance(buildx, BaseException):
            raise buildx
        if k.get("check") and buildx:
            raise subprocess.CalledProcessError(buildx, args)
        return subprocess.CompletedProcess(args, buildx, "", "")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/docker" if docker_on_path else None)
    monkeypatch.setattr(subprocess, "run", run)
    return runs


def _mojo_docker(monkeypatch, client):
    from orchestrator.Core.mojo.checks import docker as docker_check
    monkeypatch.setattr(docker_check, "_storage_findings", lambda framework: [])
    monkeypatch.setattr(docker_check, "_stage_user_findings", lambda framework, client: [])   # own tests
    # rootless and the group with root effect: own tests (tests/test_rootless_default.py)
    monkeypatch.setattr(docker_check, "_privilege_findings", lambda client, platform=None: [])
    return [(f.code, f.severity.value) for f in docker_check.check_docker(_Framework(client))]


def test_mojo_docker_check_reports_daemon_and_buildx(monkeypatch):
    import subprocess
    runs = _docker_host(monkeypatch)
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok"), ("docker.buildx", "ok")]
    assert runs == [["docker", "buildx", "version"]]
    _docker_host(monkeypatch, buildx=1)
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok"), ("docker.buildx", "warn")]
    _docker_host(monkeypatch, buildx=subprocess.TimeoutExpired(["docker"], 15))
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok")]
    runs = _docker_host(monkeypatch, docker_on_path=False)
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok")] and runs == []


def test_mojo_docker_check_stops_at_an_unreachable_daemon(monkeypatch):
    runs = _docker_host(monkeypatch)
    for client in (None, _Client(ConnectionError("refused"))):
        assert _mojo_docker(monkeypatch, client) == [("docker.daemon", "error")]
    assert runs == []


def _engine_warnings(client):
    from orchestrator.Core.builder import BuildEngine
    warnings = []
    engine = types.SimpleNamespace(docker_client=client,
                                   logger=types.SimpleNamespace(warning=warnings.append))
    BuildEngine._validate_docker_environment(engine)
    return warnings


def test_build_engine_start_warns_about_docker_as_before(monkeypatch):
    _docker_host(monkeypatch)
    assert _engine_warnings(_Client()) == []
    _docker_host(monkeypatch, buildx=1)
    assert _engine_warnings(_Client()) == ["Docker BuildX not available, standard build will be used."]
    _docker_host(monkeypatch, buildx=OSError("no such file"))
    assert _engine_warnings(_Client()) == ["Docker BuildX not available, standard build will be used."]
    _docker_host(monkeypatch, docker_on_path=False)
    assert _engine_warnings(_Client()) == ["Docker executable not found in PATH."]
    _docker_host(monkeypatch)
    assert _engine_warnings(_Client(ConnectionError("refused"))) == ["Docker environment check failed: refused"]


def test_build_engine_start_never_fails_on_an_odd_buildx_answer(monkeypatch):
    """The engine's original check swallowed every failure of the buildx question (a warning, never an exception:
    the engine is built when the framework starts, for the CLI, the GUI and the MCP server). Output the locale cannot
    decode raises UnicodeDecodeError, a ValueError, not an OSError."""
    _docker_host(monkeypatch, buildx=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"))
    assert _engine_warnings(_Client()) == ["Docker BuildX not available, standard build will be used."]
    # Mojo treats it like a buildx that does not answer: no buildx line (before: a crashed check)
    assert _mojo_docker(monkeypatch, _Client()) == [("docker.daemon", "ok")]


def test_the_buildx_question_runs_through_the_one_command_runner(monkeypatch):
    """helpers.execute_command (round 12: the one runner of a short command): stdin closed, so the MCP server's stdio
    stream is never handed to docker (rule G25), output decoded with replacement characters, at most 15 seconds."""
    import subprocess
    from orchestrator.utils import validation
    seen = []
    runs = _docker_host(monkeypatch)
    real_fake = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: seen.append(k) or real_fake(args, *a, **k))
    assert validation.check_docker_environment(_Client()) == validation.DockerEnvironment(True, "", True, True)
    assert runs == [["docker", "buildx", "version"]]
    (options,) = seen
    assert options["stdin"] == subprocess.DEVNULL and options["timeout"] == 15 and options["errors"] == "replace"
    assert options["capture_output"] is True and options["text"] is True


# --- item 11: what covers each job of the former utils/network.NetworkGuard ----------------------------------------
# NetworkGuard (2025) pinged 8.8.8.8:53, waited without limit until that answered, and downloaded with an optional
# hash. PinnedDownloader (utils/download.py) does each job through the session the download itself uses.

class _Response:
    def __init__(self, status, body=b"", headers=None, drop_after=None, url="https://files.example/model.bin"):
        self.status_code, self.body, self.headers, self.url = status, body, headers or {}, url
        self.drop_after = drop_after

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        import requests
        sent = 0
        for i in range(0, len(self.body), 10):
            if self.drop_after is not None and sent >= self.drop_after:
                raise requests.exceptions.ChunkedEncodingError("connection dropped")
            yield self.body[i:i + 10]
            sent += 10


class _Session:
    def __init__(self, script):
        self.script, self.ranges = list(script), []

    def get(self, url, headers=None, **kwargs):
        self.ranges.append((headers or {}).get("Range"))
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def test_the_pinned_downloader_rides_out_a_lost_connection_and_resumes(tmp_path):
    import hashlib
    import requests
    from orchestrator.utils.download import PinnedDownloader
    data = bytes(range(100))
    session = _Session([_Response(200, data, {"Content-Length": "100"}, drop_after=40),
                        requests.ConnectionError("network unreachable"),
                        _Response(206, data[40:], {"Content-Range": "bytes 40-99/100"})])
    sleeps = []
    dest = PinnedDownloader(session, sleep=sleeps.append).fetch("https://files.example/model.bin",
                                                                tmp_path / "model.bin", 100,
                                                                hashlib.sha256(data).hexdigest())
    assert dest.read_bytes() == data and session.ranges == [None, "bytes=40-", "bytes=40-"]
    assert sleeps == [2, 4], "a bounded wait between attempts, never an endless loop"


def test_the_pinned_downloader_gives_up_with_a_plain_reason_and_keeps_the_part(tmp_path):
    import hashlib
    import requests
    from orchestrator.utils.download import DownloadError, PinnedDownloader
    data = bytes(range(100))
    session = _Session([_Response(200, data, {"Content-Length": "100"}, drop_after=40)]
                       + [requests.ConnectionError("offline")] * 4)
    with pytest.raises(DownloadError, match="did not complete after 5 attempts; run the command again to resume"):
        PinnedDownloader(session, sleep=lambda s: None).fetch("https://files.example/model.bin",
                                                              tmp_path / "model.bin", 100,
                                                              hashlib.sha256(data).hexdigest())
    assert (tmp_path / "model.bin.part").stat().st_size == 40 and not (tmp_path / "model.bin").exists()


def test_the_pinned_downloader_checks_what_network_guard_left_optional(tmp_path):
    from orchestrator.utils.download import IntegrityError, PinnedDownloader
    data = bytes(range(100))
    with pytest.raises(IntegrityError, match="No sha256 and size pinned"):
        PinnedDownloader(_Session([])).fetch("https://files.example/model.bin", tmp_path / "a.bin", None, None)
    with pytest.raises(IntegrityError, match="does not match its pin"):
        PinnedDownloader(_Session([_Response(200, data, {"Content-Length": "100"})]), sleep=lambda s: None).fetch(
            "https://files.example/model.bin", tmp_path / "b.bin", 100, "0" * 64)
    assert not (tmp_path / "b.bin").exists() and not (tmp_path / "b.bin.part").exists()
    redirected = _Response(200, data, {"Content-Length": "100"}, url="http://files.example/model.bin")
    with pytest.raises(Exception, match="redirected away from HTTPS"):
        PinnedDownloader(_Session([redirected]), sleep=lambda s: None).fetch(
            "https://files.example/model.bin", tmp_path / "c.bin", 100, "0" * 64)


def test_network_guard_is_gone():
    import importlib.util
    assert importlib.util.find_spec("orchestrator.utils.network") is None


# --- item 13: the small helpers that newer code now uses again (characterization: same results before and after) ----

CHILD = [sys.executable, "-c"]


@pytest.mark.parametrize("runner_name", ["hardware", "toolchain"])
def test_the_command_runners_keep_their_results(runner_name, tmp_path):
    from orchestrator.runtime import hardware
    from orchestrator.runtime.build import toolchain
    if runner_name == "hardware":
        def run(cmd, timeout):
            return hardware._default_runner(cmd, timeout)
    else:
        def run(cmd, timeout):
            return toolchain.default_tool_runner(cmd, timeout, str(tmp_path))
    code, out, err = run(CHILD + ["import sys; print('out'); sys.stderr.write('err'); sys.exit(3)"], 30)
    assert (code, out.strip(), err) == (3, "out", "err")
    # stdin is closed: a command that reads it ends at once instead of waiting
    assert run(CHILD + ["import sys; print(repr(sys.stdin.read()))"], 30)[1].strip() == "''"
    # undecodable output is replaced, never an exception
    code, out, _ = run(CHILD + ["import sys; sys.stdout.buffer.write(b'\\xff\\xfeok')"], 30)
    assert code == 0 and out.endswith("ok")
    missing = run([str(tmp_path / "no-such-program")], 30)
    assert missing[0] == -1 and missing[1] == "" and missing[2]
    slow = run(CHILD + ["import time; time.sleep(30)"], 0.5)
    assert slow[0] == -1 and slow[1] == "" and "timed out" in slow[2]


def test_the_tool_runner_still_clears_the_environment(tmp_path, monkeypatch):
    from orchestrator.runtime.build import toolchain
    monkeypatch.setenv("LCF_CLEANUP_SECRET", "x")
    code, out, _ = toolchain.default_tool_runner(
        CHILD + ["import os; print(sorted(k for k in os.environ if k in ('LCF_CLEANUP_SECRET', 'LANG', 'LC_ALL')));"
                 "print(os.getcwd())"], 30, str(tmp_path))
    lines = out.splitlines()
    assert code == 0 and lines[0] == "['LANG', 'LC_ALL']"
    assert os.path.samefile(lines[1], tmp_path)


@pytest.mark.parametrize("module, empty", [("orchestrator.runtime.llama_server", None),
                                           ("orchestrator.rag.edge_store", {})])
def test_the_json_readers_keep_their_defaults(module, empty, tmp_path):
    import importlib
    read = importlib.import_module(module)._read_json
    assert read(tmp_path / "missing.json") == empty
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert read(tmp_path / "bad.json") == empty
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    assert read(tmp_path / "list.json") == empty
    (tmp_path / "ok.json").write_text('{"a": 1}', encoding="utf-8")
    assert read(tmp_path / "ok.json") == {"a": 1}
    assert read(tmp_path) == empty   # a folder, not a file


def test_the_chat_client_finds_the_managed_server_of_its_state_folder(tmp_path, monkeypatch):
    from orchestrator.llm.client import LLMClient
    from orchestrator.runtime import llama_server
    seen = []
    endpoint = types.SimpleNamespace(base_url="http://127.0.0.1:8099/v1", api_key="k", model="m")
    monkeypatch.setattr(llama_server, "read_endpoint", lambda root, docker=None: seen.append(root) or endpoint)
    client = LLMClient(types.SimpleNamespace(get=lambda key, default=None: default, user_state_dir=None),
                       None, state_dir=tmp_path / "state")
    assert client._managed_runtime_endpoint("local") == ("http://127.0.0.1:8099/v1", "k", "m")
    assert seen == [llama_server.runtime_root(tmp_path / "state")]
    assert client._managed_runtime_endpoint("openai") is None and len(seen) == 1


def test_the_target_tool_models_keep_their_schemas():
    from orchestrator.tools.tools_targets import TargetCheckIn, TargetDetailsOut
    # Round 36 (on purpose): check_image and build_image, the read-only status of the build container image.
    assert list(TargetCheckIn.model_fields) == ["target", "check_files", "check_image"]
    assert list(TargetDetailsOut.model_fields) == [
        "notes", "schema_version", "support_level", "boards", "formats", "default_board", "target", "trust",
        "untested", "description", "assumptions", "base_image", "base_image_reason", "checked_files", "problems",
        "board_profiles", "benchmarks",   # the board profiles and marked benchmark rows (round 23 item 10)
        "build_image"]
    schema = TargetCheckIn.model_json_schema()
    assert schema["additionalProperties"] is False and schema["required"] == ["target"]
    assert schema["properties"]["target"] == {"description": "Target id, e.g. Rockchip", "maxLength": 64,
                                              "minLength": 1, "title": "Target", "type": "string"}


def test_the_target_tool_input_schema_an_ai_reads_is_the_one_of_before():
    """MCP clients and Ditto get the input model's JSON schema (mcp_server.closed_schema, ToolSpecLLM.from_model),
    so a class docstring becomes text an AI reads. Sharing the original TargetIn changes nothing an AI sees."""
    from orchestrator.tools.tools_targets import TargetCheckIn
    assert TargetCheckIn.model_json_schema() == {
        "additionalProperties": False, "required": ["target"], "title": "TargetCheckIn", "type": "object",
        "properties": {
            "target": {"description": "Target id, e.g. Rockchip", "maxLength": 64, "minLength": 1, "title": "Target",
                       "type": "string"},
            "check_files": {"default": False, "description": "Also check the target's files (target.yml, Dockerfile "
                                                             "pins, scripts) the way a module or pack is checked",
                            "title": "Check Files", "type": "boolean"},
            # round 36 (on purpose): the build container image's status on request
            "check_image": {"default": False, "description": "Also show the target's build container image: how many "
                                                             "are on this computer and whether an offline build could "
                                                             "make one (each source by its label)",
                            "title": "Check Image", "type": "boolean"}}}


def test_the_shared_target_models_keep_their_schemas():
    """Characterization (stage-B integration): TargetIn and TargetCapabilitiesOut, the bases of the target tool's
    models, keep the JSON schema an AI reads however they are imported (framework_tools keeps the names)."""
    from orchestrator.schemas.targets import TargetCapabilities
    from orchestrator.tools.framework_tools import TargetCapabilitiesOut, TargetIn
    from orchestrator.tools.output import ToolOutput
    assert TargetIn.model_json_schema() == {
        "additionalProperties": False, "required": ["target"], "title": "TargetIn", "type": "object",
        "properties": {"target": {"description": "Target id, e.g. Rockchip", "maxLength": 64, "minLength": 1,
                                  "title": "Target", "type": "string"}}}
    assert list(TargetCapabilitiesOut.model_fields) == ["notes", "schema_version", "support_level", "boards",
                                                        "formats", "default_board"]
    assert issubclass(TargetCapabilitiesOut, TargetCapabilities) and issubclass(TargetCapabilitiesOut, ToolOutput)
    out, base, notes = (TargetCapabilitiesOut.model_json_schema(), TargetCapabilities.model_json_schema(),
                        ToolOutput.model_json_schema())
    assert out["title"] == "TargetCapabilitiesOut" and out["additionalProperties"] is False
    assert out["properties"] == {**notes["properties"], **base["properties"]}
    assert out["$defs"] == {**notes.get("$defs", {}), **base["$defs"]} and out["required"] == base["required"]


def test_the_original_helpers_do_the_job_of_their_copies(tmp_path):
    from orchestrator.utils.helpers import execute_command, safe_json_load
    (tmp_path / "a.json").write_text('[1, 2]', encoding="utf-8")
    assert safe_json_load(tmp_path / "a.json") == [1, 2]
    assert safe_json_load(tmp_path / "a.json", "fallback", dict) == "fallback"
    assert safe_json_load(tmp_path / "missing.json", {"x": 1}) == {"x": 1}
    code, out, err = execute_command(CHILD + ["print('hi')"], cwd=str(tmp_path), timeout=30)
    assert (code, out.strip(), err) == (0, "hi", "")


def test_host_tools_record_only_known_tunings():
    from orchestrator.runtime.build.host_tools import TUNINGS, HostTools
    from orchestrator.runtime.build.recipe import PlanError
    for tuning in TUNINGS:
        assert HostTools("b1", "0" * 40, None, tuning, "why").record()["tuning"] == tuning
    with pytest.raises(PlanError, match="unknown tuning 'fast'"):
        HostTools("b1", "0" * 40, None, "fast", "why")


def test_grant_facts_is_the_one_standing_approval_check(monkeypatch):
    from orchestrator.Core import build_plan
    fits = []
    monkeypatch.setattr(build_plan, "_standing_fits", lambda plan, service, ledger: fits.append(plan) or True)
    monkeypatch.setattr(build_plan, "grant_scope", lambda *a, **k: pytest.fail("the given scope is not worked out again"))
    plan = types.SimpleNamespace()
    assert build_plan.grant_facts(plan, None, None, None, None, None, scope={"k": 1}) == {"k": 1}
    assert build_plan.grant_facts(plan, None, None, None, None, None, scope=None) is None
    assert fits == [plan]   # no scope (a plan problem): no disk check either


def test_list_workflows_is_a_plain_snapshot():
    from orchestrator.Core.orchestrator import LLMOrchestrator
    orchestrator = object.__new__(LLMOrchestrator)
    orchestrator._workflows = {"a": "first", "b": "second"}
    flows = orchestrator.list_workflows()
    assert flows == ["first", "second"]
    orchestrator._workflows["c"] = "third"
    assert flows == ["first", "second"], "a snapshot: the caller's list does not change under it"


def test_the_leftovers_of_moved_code_are_gone():
    import importlib
    gone = {"orchestrator.utils.helpers": ("check_command_exists", "is_admin"),
            "orchestrator.tools.normalize": ("canonical_key",),
            "orchestrator.tools.registry": ("_with_message",),
            "orchestrator.schemas.build": ("BoardOut",),
            "orchestrator.runtime.probe_service": ("is_probe_id",),
            "orchestrator.Core.rag_manager": ("SOURCE_KINDS",),
            "orchestrator.cli": ("_write_profile_file",),
            "orchestrator.tools.grants": ("WIDTH_LABELS", "REQUEST_TTL")}
    for module, names in gone.items():
        loaded = importlib.import_module(module)
        for name in names:
            assert not hasattr(loaded, name), f"{module}.{name}"
    from orchestrator.Core.deployment_manager import DeploymentManager
    from orchestrator.Core.rag_manager import RAGManager
    from orchestrator.tools.mcp_tokens import TokenStore
    assert not hasattr(DeploymentManager, "deploy_artifact") and not hasattr(RAGManager, "snapshot_listing")
    assert not hasattr(TokenStore, "policy_of")
