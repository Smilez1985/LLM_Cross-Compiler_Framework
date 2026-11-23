"""Ditto's archive, phase 2, part 2 (DESIGN, the row "Ditto's research for the archive starts from the hardware probe";
decision round 117): a claim Ditto proposes about a page is checked by fixed rules against the page the knowledge base
stores; a claim of a web page never replaces an entry, another value is kept as a competing claim and stops Ditto's
confirmation; a mirror on another domain counts as one source. Real embedded storage with the fake embedders;
documentation addresses only."""

import pytest

from orchestrator.Core.archive_claims import (ClaimOffer, QUOTE_MAX, check_claim, passage_fingerprint,
                                              value_in_quote)
from orchestrator.Core.ditto_archive import Candidate, DittoArchive
from orchestrator.schemas.archive import normalize_value
from orchestrator.utils import archive_sources
from test_ditto_archive import Clock
from test_knowledge_tools import make_rag  # noqa: F401 - the fixture

TABLE = {"suffixes": ["co.uk"], "listen": [],
         "vendors": [{"vendor": "Example Vendor", "domains": ["vendorexample.com"], "targets": ["Rockchip"]}]}
KEY = "chip:rk3588/npu_tops"
SPEC = ("The RK3588 has a built-in NPU with up to 6 TOPS of INT8 compute for AI workloads, and four Cortex-A76 cores "
        "at up to 2.4 GHz. Its SDK is rknn-toolkit2 v2.3.0.")
QUOTE = "a built-in NPU with up to 6 TOPS of INT8 compute"


def page(i: int) -> str:
    return f"https://site{i}.org/rk3588"


@pytest.fixture
def world(make_rag):
    rag = make_rag()
    archive = DittoArchive(lambda: rag, rag.config, table=TABLE, clock=Clock())
    return rag, archive


def ingest(rag, address, text=SPEC, **meta):
    assert rag.ingest_document(address, f"# RK3588\n\n{text}", {"content_type": "html", "title": "RK3588", **meta})


def offer(address, value="6 TOPS", quote=QUOTE, value_type="number", key=KEY,
          sentence="The NPU of the RK3588 has 6 TOPS."):
    return ClaimOffer(key, value_type, value, sentence, address, quote)


class TestTheRules:

    def test_a_claim_with_its_quote_on_the_stored_page_passes(self, world):
        rag, _ = world
        ingest(rag, page(1))
        candidate, reason = check_claim(rag, TABLE, offer(page(1)))
        assert reason == "" and candidate.origin == "web" and candidate.ground == "sources"
        assert candidate.evidence == [page(1)] and len(candidate.passages[page(1)]) == 64

    @pytest.mark.parametrize("change, reason", [
        (dict(address="http://site1.org/rk3588"), "public https address"),
        (dict(address="https://192.168.1.5/x"), "public https address"),
        (dict(value_type="colour"), "type is not"),
        (dict(value_type="flag", value="yes"), "flag cannot be read off a quote"),
        (dict(value="six"), "is not a number"),
        (dict(quote="6 TOPS"), "the quote must be"),
        (dict(quote="x" * (QUOTE_MAX + 1)), "the quote must be"),
        (dict(quote="a built-in NPU with up to 8 TOPS of INT8 compute"), "not on the stored page"),
        (dict(value="8 TOPS"), "the value is not in the quote"),
        (dict(value="6 GOPS"), "the value is not in the quote"),
    ])
    def test_a_claim_that_breaks_a_rule_is_refused(self, world, change, reason):
        rag, _ = world
        ingest(rag, page(1))
        args = {"address": page(1), **change}
        candidate, why = check_claim(rag, TABLE, offer(**args))
        assert candidate is None and reason in why

    def test_a_page_outside_the_knowledge_base_or_not_a_web_page_is_refused(self, world):
        rag, _ = world
        assert "not in the knowledge base" in check_claim(rag, TABLE, offer(page(9)))[1]
        ingest(rag, page(2), origin_pack="kp-rknn")
        assert "only a public web page" in check_claim(rag, TABLE, offer(page(2)))[1]

    def test_the_ground_is_the_framework_s(self, world):
        rag, _ = world
        ingest(rag, "https://www.vendorexample.com/rk3588")
        candidate, _ = check_claim(rag, TABLE, offer("https://www.vendorexample.com/rk3588"))
        assert candidate.ground == "vendor"

    def test_the_quote_is_compared_as_the_archive_compares(self, world):
        rag, _ = world
        ingest(rag, page(1))
        candidate, reason = check_claim(rag, TABLE, offer(page(1), quote="A  BUILT-IN npu with up to 6 tops of int8 COMPUTE"))
        assert reason == "" and candidate is not None

    @pytest.mark.parametrize("value_type, value, quote, found", [
        ("number", "6 TOPS", "up to 6.0 tops of INT8", True),
        ("number", "2.4 GHz", "cores at up to 2.4GHz.", True),
        ("number", "6 TOPS", "up to 16 TOPS", False),
        ("version", "2.3.0", "rknn-toolkit2 v2.3.0.", True),
        ("version", "2.3", "rknn-toolkit2 v2.3.0", False),
        ("text", "Cortex-A76", "four Cortex-A76 cores", True),
    ])
    def test_the_value_must_be_written_in_the_quote(self, value_type, value, quote, found):
        assert value_in_quote(value_type, normalize_value(value_type, value), quote) is found


class TestMirrors:

    def test_the_same_passage_gives_the_same_fingerprint(self):
        assert passage_fingerprint(SPEC, QUOTE) == passage_fingerprint("  " + SPEC.upper().lower(), QUOTE.upper())
        assert passage_fingerprint(SPEC, QUOTE) != passage_fingerprint("Other text. " + SPEC, QUOTE)
        assert passage_fingerprint(SPEC, "not there at all, really") == ""

    def test_two_domains_with_the_same_passage_count_once(self):
        fp, other = "a" * 64, "b" * 64
        evidence = [page(1), page(2), page(3)]
        assert archive_sources.independent_sources(evidence, ()) == 3
        assert archive_sources.independent_sources(evidence, (), {page(1): fp, page(2): fp, page(3): other}) == 2
        assert archive_sources.independent_sources(evidence, (), {page(1): fp, page(2): fp, page(3): fp}) == 1

    def test_a_mirror_does_not_help_ditto_confirm(self, world):
        rag, archive = world
        for i in range(1, 6):
            ingest(rag, page(i))     # the same text on five domains: one source
            result = archive.record(check_claim(rag, TABLE, offer(page(i)))[0])
        assert result.entry.independent_sources == 1 and result.entry.status == "unconfirmed"


class TestClaimsInTheArchive:

    def test_one_page_enters_unconfirmed_and_waits_for_more(self, world):
        rag, archive = world
        ingest(rag, page(1))
        result = archive.record(check_claim(rag, TABLE, offer(page(1)))[0])
        assert result.outcome == "created" and result.entry.status == "unconfirmed"
        assert result.entry.independent_sources == 1

    def test_enough_independent_pages_let_ditto_confirm(self, world):
        rag, archive = world
        for i in range(1, archive.needed() + 1):
            ingest(rag, page(i), text=f"Page {i}. " + SPEC + f" Updated by site {i}.")
            result = archive.record(check_claim(rag, TABLE, offer(page(i)))[0])
        assert result.entry.independent_sources == archive.needed()
        assert result.entry.status == "confirmed" and result.entry.confirmed_by == "ditto"

    def test_another_value_from_a_page_is_a_competing_claim_and_stops_the_confirmation(self, world):
        rag, archive = world
        for i in range(1, archive.needed() + 1):
            ingest(rag, page(i), text=f"Page {i}. " + SPEC)
            archive.record(check_claim(rag, TABLE, offer(page(i)))[0])
        old = "The RK3588 has an NPU with up to 8 TOPS of INT8 compute, an older figure of its first data sheet."
        ingest(rag, page(20), text=old)
        result = archive.record(check_claim(rag, TABLE, offer(page(20), value="8 TOPS",
                                                              quote="an NPU with up to 8 TOPS of INT8 compute"))[0])
        assert result.outcome == "contested"
        entry, = archive.entries()
        assert entry.value == "6 tops" and entry.status == "unconfirmed" and entry.confirmed_by is None
        claim, = entry.competing
        assert claim.value == "8 tops" and claim.evidence == [page(20)] and claim.independent_sources == 1

    def test_the_same_competing_value_again_adds_its_evidence(self, world):
        rag, archive = world
        ingest(rag, page(1))
        archive.record(check_claim(rag, TABLE, offer(page(1)))[0])
        for i in (2, 3):
            ingest(rag, page(i), text=f"Site {i}: an NPU with up to 8 TOPS of INT8 compute in its first revision.")
            archive.record(check_claim(rag, TABLE, offer(page(i), value="8 TOPS",
                                                         quote="an NPU with up to 8 TOPS of INT8 compute"))[0])
        claim, = archive.entries()[0].competing
        assert claim.evidence == [page(2), page(3)] and claim.independent_sources == 2

    def test_a_person_s_confirmation_stays_and_the_conflict_is_shown(self, world):
        rag, archive = world
        ingest(rag, page(1))
        entry = archive.record(check_claim(rag, TABLE, offer(page(1)))[0]).entry
        archive.confirm(entry.id)
        ingest(rag, page(2), text="Site 2 says: an NPU with up to 8 TOPS of INT8 compute.")
        archive.record(check_claim(rag, TABLE, offer(page(2), value="8 TOPS",
                                                     quote="an NPU with up to 8 TOPS of INT8 compute"))[0])
        entry, = archive.entries()
        assert entry.status == "confirmed" and entry.confirmed_by == "person" and len(entry.competing) == 1

    def test_a_tool_s_result_still_replaces_as_in_phase_1(self, world):
        rag, archive = world
        ingest(rag, page(1))
        archive.record(check_claim(rag, TABLE, offer(page(1)))[0])
        result = archive.record(Candidate(KEY, "The NPU of the RK3588 was measured at 5 TOPS.", "tested",
                                          ["build:a_0123456789abcdef"], "number", "5 TOPS"))
        assert result.outcome == "replaced" and result.entry.competing == []

    def test_the_competing_claims_survive_storage(self, world, make_rag):
        rag, archive = world
        ingest(rag, page(1))
        archive.record(check_claim(rag, TABLE, offer(page(1)))[0])
        ingest(rag, page(2), text="Site 2 says: an NPU with up to 8 TOPS of INT8 compute.")
        archive.record(check_claim(rag, TABLE, offer(page(2), value="8 TOPS",
                                                     quote="an NPU with up to 8 TOPS of INT8 compute"))[0])
        again = DittoArchive(lambda: rag, rag.config, table=TABLE, clock=Clock())
        entry, = again.entries()
        assert [c.value for c in entry.competing] == ["8 tops"] and entry.passages

    def test_a_web_claim_without_a_value_or_with_a_stray_fingerprint_is_dropped(self, world):
        _, archive = world
        bare = Candidate(KEY, "The NPU has 6 TOPS.", "sources", [page(1)], origin="web")
        assert archive.record(bare).reason == "a claim of a web page needs a typed value"
        stray = Candidate(KEY, "The NPU has 6 TOPS.", "sources", [page(1)], "number", "6 TOPS", "web",
                          {page(2): "a" * 64})
        assert archive.record(stray).reason == "a passage's fingerprint belongs to no evidence address"


# --- the tool propose_archive_claim (optional: off until the person enables it, then without a question) -------------

from orchestrator.tools.policy import Decision, ToolPolicy  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError  # noqa: E402
from test_knowledge_tools import tools_for  # noqa: E402

ARGS = {"key": KEY, "value_type": "number", "value": "6 TOPS", "sentence": "The NPU of the RK3588 has 6 TOPS.",
        "address": page(1), "quote": QUOTE}


@pytest.fixture
def tool(world, tmp_path):
    rag, _ = world
    ingest(rag, page(1))
    return tools_for(tmp_path, rag)


def enable(registry, decision=Decision.ALLOW):
    registry.policy_store.replace(ToolPolicy(configured=True, overrides={"propose_archive_claim": decision},
                                             reviewed=[s.name for s in registry.list()]))


class TestTheTool:

    def test_it_is_off_until_the_person_enables_it(self, tool):
        with pytest.raises(ToolError) as refused:
            tool.call("propose_archive_claim", ARGS, ToolContext("ditto"))
        assert refused.value.code in ("permission_denied", "unknown_tool")

    def test_an_enabled_tool_stores_a_checked_claim_without_a_question(self, tool):
        enable(tool)
        out = tool.call("propose_archive_claim", ARGS, ToolContext("ditto"))
        assert out.outcome == "created" and out.status == "unconfirmed" and out.independent_sources == 1
        assert out.needed >= 3 and out.competing_values == []
        found = tool.call("search_knowledge", {"query": "rk3588 npu tops", "scope": "archive"}, ToolContext("ditto"))
        hit, = found.archive
        assert hit.value == "6 tops" and hit.competing_values == []

    def test_a_claim_that_breaks_a_rule_is_refused_with_its_reason(self, tool):
        enable(tool)
        with pytest.raises(ToolError) as refused:
            tool.call("propose_archive_claim", {**ARGS, "quote": "a built-in NPU with up to 9 TOPS of INT8 compute"},
                      ToolContext("ditto"))
        assert "not on the stored page" in str(refused.value) and refused.value.code == "invalid_value"

    def test_a_flag_is_not_offered_to_the_model(self, tool):
        enable(tool)
        with pytest.raises(ToolError):
            tool.call("propose_archive_claim", {**ARGS, "value_type": "flag", "value": "yes"}, ToolContext("ditto"))
