"""Ditto's archive, phase 1 (DESIGN, Ditto, the archive's rows): the rules of the entries on a real knowledge-base
storage (Qdrant Edge in tmp_path) with the fake embedders of test_rag; documentation addresses only."""

import pytest

from orchestrator.Core.ditto_archive import ArchiveError, Candidate, DittoArchive, entry_id, private_reason
from orchestrator.schemas.archive import (ARCHIVE_ALIAS, DEFAULT_LEVEL, LEVEL_SOURCES, confirm_needed, normalize_level,
                                          valid_evidence, valid_key)
from orchestrator.utils import archive_sources
from test_rag import FakeEmbedder, make_rag  # noqa: F401 - the fixture and the fake

TABLE = {"suffixes": ["co.uk"], "listen": ["get_artifact_details"],
         "vendors": [{"vendor": "Example Vendor", "domains": ["vendorexample.com"], "targets": ["Rockchip"]}]}
KEY = "target:rk3588/npu"
PAGES = [f"https://example{i}.org/npu" for i in range(1, 9)]


class Clock:
    """A clock that moves one second per call, so the order of writes is the order of the time stamps."""

    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return f"2026-11-13T10:{self.n // 60:02d}:{self.n % 60:02d}+00:00"


@pytest.fixture
def archive(make_rag):
    rag = make_rag()
    return DittoArchive(lambda: rag, rag.config, table=TABLE, clock=Clock())


def offer(archive, text="The NPU of the RK3588 runs quantized models.", key=KEY, ground="sources", evidence=None):
    return archive.record(Candidate(key, text, ground, evidence or PAGES[:2]))


class TestRules:

    def test_the_levels_need_three_five_and_seven_sources(self):
        assert LEVEL_SOURCES == {"low": 3, "mid": 5, "high": 7}
        assert [confirm_needed(x) for x in ("low", "mid", "high")] == [3, 5, 7]
        assert DEFAULT_LEVEL == "mid" and confirm_needed("nonsense") == 5 and normalize_level(None) == "mid"

    def test_independent_sources_are_different_registrable_domains(self):
        suffixes = archive_sources.suffixes_of(TABLE)
        evidence = ["https://docs.vendorexample.com/a", "https://www.vendorexample.com/b", "https://mirror.vendorexample.com/c",
                    "https://a.example.co.uk/x", "https://b.example.co.uk/y", "https://otherexample.org/z", "build:b1"]
        assert archive_sources.domains_of(evidence, suffixes) == ["example.co.uk", "otherexample.org", "vendorexample.com"]
        assert archive_sources.independent_sources(evidence, suffixes) == 3

    def test_two_part_suffixes_keep_neighbours_apart(self):
        suffixes = archive_sources.suffixes_of(TABLE)
        assert archive_sources.registrable_domain("a.example.co.uk", suffixes) == "example.co.uk"
        assert archive_sources.registrable_domain("a.example.co.uk") == "co.uk"      # without the table: one domain
        assert archive_sources.registrable_domain("www.a.example.org", suffixes) == "example.org"

    def test_addresses_of_computers_and_local_names_are_no_sources(self):
        for address in ("https://192.0.2.7/x", "https://[2001:db8::1]/x", "https://board.local/x", "http://a.example.org/x",
                        "https://user:pw@a.example.org/x", "https://localhost/x"):
            assert archive_sources.host_of(address) == "", address
        assert archive_sources.host_of("https://Docs.VendorExample.com./a") == "docs.vendorexample.com"

    def test_a_vendor_is_found_by_its_registrable_domain(self):
        assert archive_sources.vendor_of("https://docs.vendorexample.com/x", TABLE)["vendor"] == "Example Vendor"
        assert archive_sources.vendor_of("https://vendorexample.com.evil.org/x", TABLE) is None

    def test_keys_and_evidence_have_one_shape(self):
        assert valid_key("target:rk3588/npu_tops") and valid_key("build:Rockchip/rk3588-gguf-Q4_K_M/works")
        assert not valid_key("rk3588") and not valid_key("Target:rk3588/x") and not valid_key("a:b/../c d")
        assert valid_evidence("https://docs.example.com/page") and valid_evidence("build:a_1234")
        assert not valid_evidence("/home/jane/build.log") and not valid_evidence("http://a.example/x")

    def test_private_text_is_found(self):
        assert private_reason("Reach it at 8.8.4.4 today")
        assert private_reason("Mail jane@example.org")
        assert private_reason("The log is in /var/lib/builds/x.log")
        assert private_reason("The key is " + "sk-ant-api03-" + "aB3dE5fG7h" * 9)
        assert not private_reason("See https://docs.example.com/a/b for the NPU, version 1.2.3.")

    def test_the_shipped_table_is_data_the_rules_can_read(self):
        table = archive_sources.load_table()
        assert "get_artifact_details" in archive_sources.listened_tools(table)
        assert archive_sources.vendor_of("https://docs.radxa.com/en/rock5/", table)["vendor"] == "Radxa"
        assert "co.uk" in archive_sources.suffixes_of(table)


class TestGrounds:

    def test_an_entry_needs_one_of_the_three_grounds(self, archive):
        assert offer(archive, ground="sources").outcome == "created"
        assert offer(archive, key="target:rk3588/vendor", ground="vendor",
                     evidence=["https://docs.vendorexample.com/rk3588"]).outcome == "created"
        assert offer(archive, key="build:Rockchip/x/works", ground="tested", evidence=["build:a_0123"]).outcome == "created"
        for ground in ("", "guess", None):
            assert offer(archive, key="target:rk3588/none", ground=ground).outcome == "dropped"

    def test_every_ground_has_its_proof(self, archive):
        one = offer(archive, evidence=[PAGES[0], PAGES[0] + "?x=1", "https://www.example1.org/z"])
        assert one.outcome == "dropped" and "two independent" in one.reason       # one domain is no 'sources'
        other = offer(archive, ground="vendor", evidence=[PAGES[0], PAGES[1]])
        assert other.outcome == "dropped" and "manufacturer" in other.reason
        third = offer(archive, ground="tested", evidence=[PAGES[0]])
        assert third.outcome == "dropped" and "build or test" in third.reason
        assert archive.entries() == []

    def test_private_or_changed_text_is_dropped_never_stored_cleaned(self, archive):
        for text in ("It listens on 8.8.4.4 here.", "Mail jane@example.org", "It lives in /var/lib/x/y.log",
                     "Zero​width text of the NPU"):
            assert offer(archive, text=text).outcome == "dropped", text
        assert archive.dropped == 4 and archive.entries() == []

    def test_a_bad_key_or_evidence_is_dropped(self, archive):
        assert offer(archive, key="rk3588").outcome == "dropped"
        assert offer(archive, evidence=["/home/jane/log", "https://a.example.org/x"]).outcome == "dropped"
        assert offer(archive, text="x " * 300).outcome == "dropped"


class TestConfirmation:

    def test_ditto_confirms_only_with_enough_independent_sources(self, archive):
        assert archive.level() == "mid"
        result = offer(archive, evidence=PAGES[:4])
        assert result.entry.status == "unconfirmed" and result.entry.independent_sources == 4
        result = offer(archive, evidence=[PAGES[4]])
        assert result.outcome == "merged" and result.entry.status == "confirmed"
        assert result.entry.confirmed_by == "ditto" and result.entry.independent_sources == 5

    def test_the_level_sets_the_number(self, make_rag):
        rag = make_rag(ditto_archive_security="low")
        archive = DittoArchive(lambda: rag, rag.config, table=TABLE, clock=Clock())
        assert archive.needed() == 3
        assert offer(archive, evidence=PAGES[:3]).entry.confirmed_by == "ditto"
        rag.config.set("ditto_archive_security", "high")
        assert archive.needed() == 7
        assert offer(archive, key="target:rk3588/two", evidence=PAGES[:6]).entry.status == "unconfirmed"

    def test_a_vendor_page_or_a_test_never_confirms_alone(self, archive):
        vendor = offer(archive, key="vendor_page:Example/aaaa", ground="vendor",
                       evidence=["https://docs.vendorexample.com/rk3588"])
        tested = offer(archive, key="build:Rockchip/x/works", ground="tested", evidence=["build:a_1"])
        assert vendor.entry.status == tested.entry.status == "unconfirmed"

    def test_the_person_confirms_at_will_and_takes_it_back(self, archive):
        entry = offer(archive).entry
        assert archive.confirm(entry.id).confirmed_by == "person"
        assert archive.get(entry.id).status == "confirmed"
        assert archive.unconfirm(entry.id).status == "unconfirmed"
        with pytest.raises(ArchiveError):
            archive.confirm("no-such-id")

    def test_a_domain_already_known_counts_once(self, archive):
        offer(archive, evidence=PAGES[:2])
        result = offer(archive, evidence=[PAGES[0] + "?again=1", "https://www.example2.org/other"])
        assert result.entry.independent_sources == 2


class TestReplacement:

    def test_a_newer_sentence_replaces_the_older_one(self, archive):
        first = offer(archive, text="The NPU of the RK3588 has 6 TOPS.").entry
        second = offer(archive, text="The NPU of the RK3588 has 6 TOPS in INT8.", evidence=PAGES[2:4])
        assert second.outcome == "replaced" and second.entry.id == first.id
        assert [e.text for e in archive.entries()] == ["The NPU of the RK3588 has 6 TOPS in INT8."]
        assert archive.entries(kind="copy") == []        # nobody confirmed the old one: nothing is kept

    def test_an_entry_the_person_confirmed_is_kept_as_a_copy(self, archive):
        old = offer(archive, text="The NPU of the RK3588 has 6 TOPS.").entry
        archive.confirm(old.id)
        offer(archive, text="The NPU of the RK3588 has 8 TOPS.", evidence=PAGES[2:4])
        entry, = archive.entries()
        assert entry.text.endswith("8 TOPS.") and entry.status == "unconfirmed"
        copy, = archive.entries(kind="copy")
        assert copy.text.endswith("6 TOPS.") and copy.status == "confirmed" and copy.confirmed_by == "person"
        assert copy.copied_from == old.id and copy.id != old.id
        assert archive.search("rk3588 npu tops")[0].entry.kind == "entry"         # search never returns a copy

    def test_undo_swaps_the_person_s_text_back_in(self, archive):
        old = offer(archive, text="The NPU of the RK3588 has 6 TOPS.").entry
        archive.confirm(old.id)
        offer(archive, text="The NPU of the RK3588 has 8 TOPS.", evidence=PAGES[2:4])
        restored = archive.undo(KEY)
        assert restored.text.endswith("6 TOPS.") and restored.status == "confirmed" and restored.id == old.id
        entry, = archive.entries()
        assert entry.text.endswith("6 TOPS.")
        copy, = archive.entries(kind="copy")
        assert copy.text.endswith("8 TOPS.")                                      # the newer text became the copy
        with pytest.raises(ArchiveError):
            archive.undo("target:none/x")

    def test_a_copy_is_never_overwritten_and_stays_until_deleted(self, archive):
        old = offer(archive, text="The NPU of the RK3588 has 6 TOPS.").entry
        archive.confirm(old.id)
        offer(archive, text="The NPU of the RK3588 has 8 TOPS.", evidence=PAGES[2:4])
        copy, = archive.entries(kind="copy")
        newer, = archive.entries()
        archive.confirm(newer.id)
        offer(archive, text="The NPU of the RK3588 has 9 TOPS.", evidence=PAGES[4:6])
        assert sorted(c.text[-7:] for c in archive.copies(KEY)) == ["6 TOPS.", "8 TOPS."]
        assert archive.delete(copy.id) is True and archive.delete(copy.id) is False
        assert [c.text[-7:] for c in archive.copies(KEY)] == ["8 TOPS."]

    def test_the_same_sentence_again_adds_evidence(self, archive):
        offer(archive, evidence=PAGES[:2])
        merged = offer(archive, evidence=PAGES[2:3])
        assert merged.outcome == "merged" and len(merged.entry.evidence) == 3
        assert offer(archive, evidence=PAGES[2:3]).outcome == "unchanged"
        assert len(archive.entries()) == 1

    def test_the_stronger_ground_stays(self, archive):
        offer(archive, evidence=PAGES[:2])
        result = offer(archive, ground="tested", evidence=["build:a_1"])
        assert result.entry.ground == "tested"


class TestSearch:

    def test_entries_are_found_by_meaning_and_by_words(self, archive):
        offer(archive, text="The NPU speeds up model inference on the RK3588.")
        offer(archive, key="target:rk3588/logo", text="Version two ships a new logo.")
        assert archive.search("neural accelerator inference")[0].entry.key == KEY
        assert archive.search("logo")[0].entry.key == "target:rk3588/logo"
        assert archive.search("") == []

    def test_confirmed_only_leaves_the_rest_out(self, archive):
        offer(archive, evidence=PAGES[:5])
        offer(archive, key="target:rk3588/other", text="The model runs fast on the NPU.")
        assert [h.entry.key for h in archive.search("npu model", confirmed_only=True)] == [KEY]

    def test_the_overview_counts(self, archive):
        a = offer(archive).entry
        archive.confirm(a.id)
        overview = archive.overview()
        assert overview["entries"] == 1 and overview["confirmed"] == 1 and overview["needed"] == 5
        assert overview["copies"] == 0 and overview["level"] == "mid"


class TestStorage:

    def test_the_archive_lives_in_its_own_collection_beside_the_knowledge_base(self, archive):
        rag = archive._rag()
        offer(archive)
        from orchestrator.rag.store import KnowledgeStore
        assert KnowledgeStore.alias_target(rag.client, ARCHIVE_ALIAS).startswith(ARCHIVE_ALIAS + "__")
        assert rag.get_status()["vector_count"] == 0                  # the knowledge base itself is untouched
        assert rag.get_status()["other_collections"] == []

    def test_the_id_is_a_hash_of_the_key(self, archive):
        assert offer(archive).entry.id == entry_id(KEY)
        assert entry_id(KEY) != entry_id(KEY, "2026-11-13T10:00:01")

    @pytest.mark.temp_budget(1024, reason="two embedding models: each opens its own shard of the knowledge base and "
                                          "of the archive (32 MiB write-ahead files each)")
    def test_a_new_embedding_model_fills_a_new_collection_from_the_stored_texts(self, make_rag):
        rag_a = make_rag(embedder=FakeEmbedder("fake/model-a"))
        archive_a = DittoArchive(lambda: rag_a, rag_a.config, table=TABLE, clock=Clock())
        offer(archive_a, text="The NPU of the RK3588 has 6 TOPS.")
        rag_a.close()
        rag_b = make_rag(embedder=FakeEmbedder("fake/model-b"))
        archive_b = DittoArchive(lambda: rag_b, rag_b.config, table=TABLE, clock=Clock())
        entry, = archive_b.entries()
        assert entry.text == "The NPU of the RK3588 has 6 TOPS." and entry.key == KEY
        assert archive_b.search("npu tops")[0].entry.id == entry.id

    def test_without_the_knowledge_base_the_archive_is_unavailable(self):
        archive = DittoArchive(lambda: None, None, table=TABLE)
        assert offer(archive).outcome == "unavailable"


class TestIdsAndLevel:

    def test_an_id_can_be_given_by_its_first_characters(self, archive):
        entry = offer(archive).entry
        assert archive.resolve_id(entry.id) == entry.id
        assert archive.resolve_id(entry.id[:8].upper()) == entry.id
        with pytest.raises(ArchiveError, match="at least six"):
            archive.resolve_id(entry.id[:3])
        with pytest.raises(ArchiveError, match="no archive entry"):
            archive.resolve_id("ffffffff")

    def test_two_entries_with_the_same_beginning_are_not_guessed(self, archive, monkeypatch):
        first, second = offer(archive).entry, offer(archive, key="target:rk3588/two").entry
        monkeypatch.setattr(archive, "entries", lambda **kw: [first.model_copy(update={"id": "abcdef01-0000"}),
                                                              second.model_copy(update={"id": "abcdef02-0000"})])
        with pytest.raises(ArchiveError, match="2 archive entries begin with"):
            archive.resolve_id("abcdef")

    def test_set_level_checks_first_and_the_archive_follows_the_setting(self, archive):
        from orchestrator.Core.ditto_archive import set_level
        config = archive.config
        assert set_level(config, "HIGH", save=False) is True and archive.level() == "high" and archive.needed() == 7
        with pytest.raises(ValueError, match="low, mid or high"):
            set_level(config, "extreme", save=False)
        assert archive.level() == "high"                                  # nothing changed
