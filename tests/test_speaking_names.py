"""Speaking names in what the person sees (DECISIONS round 4 item 1; delegation design 5.2 rule R7, WP3).

Exact framework ids in a text (outside code) and masked values are shown as their speaking names; near matches,
unknown ids and code stay as written; the original words can always be shown again, and code gets a legend.
"""

from types import SimpleNamespace

import pytest

from orchestrator.Core import speaking_names
from orchestrator.Core.speaking_names import FRAMEWORK_ID_RE, ID_KINDS, SpeakingNames, id_kind, original_text

JOB = "job_k3j2h1g0f9e8d7c6"
REQ = "req_1a2b3c4d"
APPROVAL = "ap_0123456789abcdef"
DELEGATION = "d_0123456789abcdef"


def names(**extra):
    table = {JOB: "Hugging Face download", REQ: "Qwen2.5 0.5B for Rockchip RK3588 (GGUF Q4_K_M)",
             APPROVAL: "question 'Start a build'", DELEGATION: "Everyday work", **extra}
    return SpeakingNames({kind: table.get for kind in ID_KINDS})


def shown(segments):
    return "".join(f"«{s.text}»" if s.kind == "name" else s.text for s in segments)


class TestRender:

    def test_ai_text_ids_are_shown_as_speaking_names(self):
        text = f"I started {JOB} for {REQ}; it waits for {APPROVAL} under {DELEGATION}."
        segments = names().render(text)
        assert shown(segments) == ("I started «Hugging Face download» for «Qwen2.5 0.5B for Rockchip RK3588 (GGUF "
                                   "Q4_K_M)»; it waits for «question 'Start a build'» under «Everyday work».")
        assert [s.original for s in segments if s.kind == "name"] == [JOB, REQ, APPROVAL, DELEGATION]

    def test_only_exact_framework_ids_are_replaced(self):
        near = [JOB + "a", JOB[:-1], JOB.upper(), "x" + JOB, JOB + "_old", "req_1a2b3c4", "req_1a2b3c4dd",
                "data_0123456789abcdef", "build_0123456789abcdef", "ap_0123456789ABCDEF", "lf_" + "0" * 15,
                "0123456789abcdef"]   # a device or probe id without a prefix is never guessed at
        renderer = names(**{n: "WRONG" for n in near})
        for value in near:
            assert renderer.render(f"see {value} here") == [speaking_names.Segment("text", f"see {value} here")], value
        # Punctuation around an exact id is fine; an id nobody can name stays as written.
        assert shown(names().render(f"({JOB}), {JOB}.")) == "(«Hugging Face download»), «Hugging Face download»."
        assert names().render("job_0000000000000000 is gone")[0].text == "job_0000000000000000 is gone"

    def test_original_words_are_kept_and_code_keeps_its_ids(self):
        texts = [f"Done: {JOB}.", f"`{JOB}` and {JOB}", f"```\nget_job_status {JOB}\n```\nthen {REQ}",
                 f"```unterminated {JOB}", "", "plain words only", f"{JOB}{REQ}", f"«{JOB}»"]
        renderer = names()
        for text in texts:
            assert original_text(renderer.render(text)) == text, text
        segments = renderer.render(f"Run `get_job_status {JOB}` for {JOB}.")
        assert [s.kind for s in segments] == ["text", "code", "text", "name", "text"]
        assert segments[1].text == f"`get_job_status {JOB}`"
        assert renderer.legend(f"Run `get_job_status {JOB}` and ```\n{REQ} {JOB}\n``` for {APPROVAL}.") == [
            f"{JOB} = Hugging Face download", f"{REQ} = Qwen2.5 0.5B for Rockchip RK3588 (GGUF Q4_K_M)"]

    def test_masked_values_are_shown_by_name(self):
        renderer = names()
        renderer.add_mask("host_7f3a", "build box")
        renderer.add_mask("user_19c2", "your user name")
        segments = renderer.render(f"Copied to host_7f3a as user_19c2, then {JOB}; host_7f3ab stays.")
        assert shown(segments) == ("Copied to «build box» as «your user name», then «Hugging Face download»; "
                                   "host_7f3ab stays.")
        with pytest.raises(ValueError):
            renderer.add_mask("  ", "x")

    def test_names_are_one_short_line_and_a_failing_lookup_names_nothing(self):
        def broken(_value):
            raise RuntimeError("lookup failed")
        renderer = SpeakingNames({"job": lambda v: "line one\nline two  " + "x" * 300, "request": broken})
        name = renderer.resolve(JOB)
        assert "\n" not in name and len(name) == speaking_names.MAX_NAME and name.endswith("…")
        assert renderer.resolve(REQ) is None and renderer.render(REQ)[0].text == REQ
        assert SpeakingNames({"job": lambda v: v}).resolve(JOB) is None, "a name that is the id itself is no name"
        with pytest.raises(ValueError):
            SpeakingNames({"device": lambda v: "x"})
        with pytest.raises(ValueError):
            renderer.register("device", lambda v: "x")

    def test_a_missing_name_is_looked_up_again_later(self, monkeypatch):
        known = {}
        renderer = SpeakingNames({"job": known.get})
        clock = [1000.0]
        monkeypatch.setattr(speaking_names.time, "monotonic", lambda: clock[0])
        assert renderer.resolve(JOB) is None
        known[JOB] = "benchmark"
        assert renderer.resolve(JOB) is None, "a miss is kept for a short while"
        clock[0] += speaking_names.MISS_TTL + 1
        assert renderer.resolve(JOB) == "benchmark"
        renderer.register("job", lambda v: "deploy to a board")
        assert renderer.resolve(JOB) == "deploy to a board", "a new resolver replaces cached names of its kind"

    def test_id_kinds_match_the_framework_ids(self):
        from orchestrator.Core.chat_feed import DELEGATION_ID_RE, ENTRY_ID_RE, MESSAGE_ID_RE
        from orchestrator.Core.job_service import JOB_ID_RE
        from orchestrator.tools.approvals import APPROVAL_ID_RE
        from orchestrator.tools.grants import GRANT_ID_RE
        from orchestrator.tools.local_files import FILE_ID_RE
        assert ID_KINDS["job"] == JOB_ID_RE.pattern.strip("^$")
        assert ID_KINDS["approval"] == APPROVAL_ID_RE.strip("^$")
        assert ID_KINDS["grant"] == GRANT_ID_RE.strip("^$")
        assert ID_KINDS["file"] == FILE_ID_RE.pattern.strip("^$")
        assert ID_KINDS["delegation"] == DELEGATION_ID_RE.strip("^$")
        assert ID_KINDS["entry"] == ENTRY_ID_RE.strip("^$") and ID_KINDS["message"] == MESSAGE_ID_RE.strip("^$")
        assert id_kind(JOB) == "job" and id_kind(REQ) == "request" and id_kind("job_1") is None
        assert FRAMEWORK_ID_RE.search(f"x {DELEGATION} y").lastgroup == "delegation"


class TestFrameworkNames:

    def test_names_come_from_the_frameworks_records(self):
        job = SimpleNamespace(kind="hf_download")
        record = SimpleNamespace(title="Start a build")
        grant = SimpleNamespace(tool="start_build")
        policy = SimpleNamespace(grant_by_id=lambda gid: ("mcp:laptop", grant) if gid == "g_0123456789abcdef" else None)
        registry = SimpleNamespace(
            approvals=SimpleNamespace(store=SimpleNamespace(get=lambda aid: record if aid == APPROVAL else None)),
            policy_store=SimpleNamespace(load=lambda: policy), get=lambda name: SimpleNamespace(title="Start a build"))
        framework = SimpleNamespace(
            job_service=SimpleNamespace(get=lambda jid: job),
            build_service=SimpleNamespace(request_label=lambda rid: "Qwen for Rockchip" if rid == REQ else None,
                                          artifact_entry=lambda aid: (aid, "output:Rockchip/qwen", {})),
            tool_registry=registry)
        english = SpeakingNames.for_framework(framework)
        assert english.resolve(JOB) == "Hugging Face download"
        assert english.resolve(REQ) == "Qwen for Rockchip"
        assert english.resolve("a_0123456789abcdef") == "output:Rockchip/qwen"
        assert english.resolve(APPROVAL) == "question 'Start a build'"
        assert english.resolve("ap_ffffffffffffffff") is None
        assert english.resolve("g_0123456789abcdef") == "standing approval for 'Start a build'"
        german = SpeakingNames.for_framework(framework, lang="de")
        assert german.resolve(JOB) == "Hugging-Face-Download"
        assert german.resolve("g_0123456789abcdef") == "Dauerfreigabe für „Start a build“"
        # Without the services nothing is named, and nothing fails.
        bare = SpeakingNames.for_framework(SimpleNamespace())
        assert all(bare.resolve(v) is None for v in (JOB, REQ, APPROVAL, "g_0123456789abcdef"))

    def test_every_job_kind_has_words_in_both_languages(self):
        from orchestrator.Core.job_service import JOB_KINDS
        for lang in ("en", "de"):
            assert set(speaking_names.JOB_WORDS[lang]) == set(JOB_KINDS), lang
