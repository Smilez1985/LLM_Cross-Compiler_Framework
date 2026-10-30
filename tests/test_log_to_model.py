"""A build log as a model reads it (consolidation chain ctx-slim; decision: cleaner logs to the model).

Everywhere a log goes to a model the order is clean_log -> scrub -> cut: the healing prompt (llm/task_prompts.
healing_parts, used by Ditto's analysis and the MCP prompt diagnose_build_failure) and the tool read_build_log
(BuildService.log_tail). Cleaning first matters: a colour code inside a path would otherwise hide the path from the
scrubber, and repeated progress lines would push the error out of the part that is kept. The person's own log view
(the Builder's live log, for_person=True) stays raw. The conversation summary keeps ids, numbers, versions and errors
exactly (SUMMARY_SYSTEM, one source in task_prompts).
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from orchestrator.llm import task_prompts
from orchestrator.llm.task_prompts import LOG_CHARS, healing_parts

FAKE_HOME = "/home/example-user"


@pytest.fixture
def fake_home(monkeypatch):
    monkeypatch.setenv("HOME", FAKE_HOME)
    monkeypatch.setenv("USERPROFILE", FAKE_HOME)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: __import__("pathlib").Path(FAKE_HOME)))


def log_part(parts):
    return next(part for part in parts if part.startswith("Build log (last part):"))


class TestHealingPrompt:
    def test_escapes_and_progress_are_cleaned(self, fake_home):
        log = "\x1b[32mstep 1\x1b[0m\n" + "Downloading 1%\rDownloading 50%\rDownloading 100%\n" + "error: boom"
        text = log_part(healing_parts(log))
        assert "\x1b" not in text and "\r" not in text
        assert "Downloading 100%" in text and "Downloading 1%" not in text and "error: boom" in text

    def test_repeats_do_not_push_the_error_out(self, fake_home):
        log = "make[2]: Entering directory 'x'\n" * 5000 + "error: undefined reference to 'ggml_init'"
        text = log_part(healing_parts(log))
        assert "error: undefined reference to 'ggml_init'" in text
        assert "make[2]: Entering directory 'x' (repeated 5000 times)" in text

    def test_a_home_path_hidden_by_colour_codes_is_scrubbed(self, fake_home):
        log = f"error: cannot open \x1b[1m{FAKE_HOME}\x1b[0m/models/model.gguf"
        text = log_part(healing_parts(log))
        assert FAKE_HOME not in text and "example-user" not in text
        assert "~/models/model.gguf" in text

    def test_secrets_are_redacted(self, fake_home):
        secret = "sk-abcdefghijklmnopqrstuvwx1234"
        assert secret not in log_part(healing_parts(f"error: key {secret} rejected"))

    def test_the_cut_comes_last_and_keeps_the_end(self, fake_home):
        lines = [f"line {i} " + "x" * (i % 7) for i in range(4000)]   # differ by more than numbers: kept
        lines[3000] = "warning: something earlier"
        log = "\n".join(lines) + "\nerror: the end"
        text = log_part(healing_parts(log))
        assert text.rstrip().endswith("</untrusted_data id=" + text.rsplit("</untrusted_data id=", 1)[1])
        assert "error: the end" in text
        body = text.split(">\n", 1)[1].rsplit("\n</untrusted_data", 1)[0]
        assert len(body) <= LOG_CHARS

    def test_the_knowledge_query_reads_the_cleaned_end(self):
        assert task_prompts.healing_query("a\n\x1b[31merror: x\x1b[0m\nb\nb\nb") == "a\nerror: x\nb (repeated 3 times)"


class TestReadBuildLog:
    """BuildService.log_tail, the tool read_build_log's handler."""

    @staticmethod
    def service_for(logs):
        from orchestrator.Core.build_service import BuildService
        from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
        state = WorkflowState("r1", OrchestrationStatus.COMPLETED, datetime.now())
        state.build_ids = ["r1_001"]
        engine = SimpleNamespace(get_build_status=lambda bid: SimpleNamespace(logs=logs))
        return BuildService(SimpleNamespace(orchestrator=SimpleNamespace(_workflows={"r1": state},
                                                                         build_engine=engine)))

    LOGS = (["\x1b[33mconfigure\x1b[0m"] + [f"[{i}/40] Building CXX object ggml{i}.o" for i in range(1, 41)]
            + ["same"] * 10 + ["error: ld returned 1", "error: ld returned 1"])

    def test_an_ai_reads_the_cleaned_log(self):
        tail = self.service_for(self.LOGS).log_tail("r1")
        assert tail.lines == ["configure", "[1/40] Building CXX object ggml1.o",
                              "(40 lines like these, only their numbers differ: the first and the last are shown)",
                              "[40/40] Building CXX object ggml40.o", "same (repeated 10 times)",
                              "error: ld returned 1", "error: ld returned 1"]
        assert (tail.first_line, tail.total_lines, tail.truncated) == (1, 53, False)

    def test_the_person_reads_the_raw_log(self):
        assert self.service_for(self.LOGS).log_tail("r1", for_person=True).lines == self.LOGS

    def test_paging_keeps_the_raw_line_numbers(self):
        svc = self.service_for(self.LOGS)
        tail = svc.log_tail("r1", lines=12)       # the last 12 raw lines: 10 x "same" and the two errors
        assert tail.lines == ["same (repeated 10 times)", "error: ld returned 1", "error: ld returned 1"]
        assert (tail.first_line, tail.truncated) == (42, True)
        older = svc.log_tail("r1", lines=41, before_line=tail.first_line)
        assert older.first_line == 1 and older.lines[0] == "configure"
        assert older.lines[-1] == "[40/40] Building CXX object ggml40.o"


def test_the_summary_prompt_has_one_source_and_keeps_facts_exact():
    from orchestrator.Core import ditto_manager
    assert ditto_manager.SUMMARY_SYSTEM is task_prompts.SUMMARY_SYSTEM
    assert ("Copy ids, numbers, version strings, labels and error messages exactly. Do not invent paths."
            in task_prompts.SUMMARY_SYSTEM)
    assert task_prompts.UNTRUSTED_RULE in task_prompts.SUMMARY_SYSTEM
