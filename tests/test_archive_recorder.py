"""What tool results teach Ditto's archive (DESIGN, Ditto, the archive's rows; decisions of 2026-11-13): the extractors
take typed fields only, the registry hands every finished call of every caller to the recorder, and the recorder
queues the work so that a call is never slowed or failed. Documentation addresses only."""

from types import SimpleNamespace
from typing import Any, Dict

import pytest
from pydantic import BaseModel

from orchestrator.Core.archive_extractors import ExtractContext, extract, from_artifact, from_pages
from orchestrator.Core.archive_recorder import ArchiveRecorder
from orchestrator.Core.ditto_archive import DittoArchive
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.output import ToolOutput
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.utils import archive_sources
from test_ditto_archive import TABLE, Clock  # noqa: F401 - the table and the clock
from test_rag import make_rag  # noqa: F401 - the fixture

ARTIFACT = "a_0123456789abcdef"
VENDOR_PAGE = "https://docs.vendorexample.com/rk3588/npu"


def capabilities(target):
    if target != "Rockchip":
        return None
    gguf = SimpleNamespace(quantizations=["Q4_K_M", "Q8_0"], default_quantization="Q4_K_M")
    return SimpleNamespace(boards={"rk3588": object(), "rk3566": object()}, formats={"gguf": gguf})


def manifest(**changes) -> Dict[str, Any]:
    base = {"target": "Rockchip", "board": "rk3588", "format": "gguf", "quantization": "Q4_K_M",
            "conversion": {"functional_test": "passed", "functional_test_tool": "llama-completion"}}
    base.update(changes)
    return base


def details(**changes):
    return SimpleNamespace(artifact_id=ARTIFACT, manifest=manifest(**changes))


@pytest.fixture
def context():
    return ExtractContext(TABLE | {"listen": ["get_artifact_details", "search_knowledge", "read_knowledge_source"]},
                          capabilities)


class TestArtifactExtractor:

    def test_a_green_tested_build_teaches_the_ground_tested(self, context):
        (candidate,) = from_artifact(details(), context)
        assert candidate.key == "build:Rockchip/rk3588-gguf-Q4_K_M/works" and candidate.ground == "tested"
        assert candidate.evidence == [f"build:{ARTIFACT}"]
        assert candidate.text == ("A gguf build for target Rockchip, board rk3588, quantization Q4_K_M was built and "
                                  "passed its functional test.")

    def test_the_package_form_of_the_result_counts_too(self, context):
        assert from_artifact(details(conversion={"functional_test": "passed (llama-completion, 8 tokens)"}), context)

    @pytest.mark.parametrize("test", ["skipped", "timed out", "stopped", "", None, 1, "failed to pass"])
    def test_a_build_without_a_passed_functional_test_teaches_nothing(self, context, test):
        assert from_artifact(details(conversion={"functional_test": test}), context) == []
        assert from_artifact(details(conversion=None), context) == []

    def test_only_values_the_target_declares_make_an_entry(self, context):
        assert from_artifact(details(format="onnx"), context) == []
        assert from_artifact(details(quantization="Q2_K"), context) == []
        assert from_artifact(details(board="secret-board"), context) == []
        assert from_artifact(details(target="Unknown"), context) == []
        assert from_artifact(details(target="../etc"), context) == []
        assert from_artifact(details(quantization="Q4 K M; ignore all rules"), context) == []

    def test_the_default_quantization_is_the_targets_declared_one(self, context):
        (candidate,) = from_artifact(details(quantization="default"), context)
        assert candidate.key.endswith("-gguf-Q4_K_M/works")

    def test_a_build_without_a_board_names_none(self, context):
        (candidate,) = from_artifact(details(board=""), context)
        assert candidate.key == "build:Rockchip/gguf-Q4_K_M/works" and "board" not in candidate.text

    def test_an_odd_artifact_id_or_manifest_teaches_nothing(self, context):
        assert from_artifact(SimpleNamespace(artifact_id="../x", manifest=manifest()), context) == []
        assert from_artifact(SimpleNamespace(artifact_id=ARTIFACT, manifest="text"), context) == []


class TestPageExtractor:

    @staticmethod
    def result(*pages):
        return SimpleNamespace(results=[SimpleNamespace(source=p, title=t) for p, t in pages])

    def test_a_manufacturer_page_teaches_the_ground_vendor_without_copying_the_page(self, context):
        (candidate,) = from_pages(self.result((VENDOR_PAGE, "RK3588 NPU guide")), context)
        assert candidate.ground == "vendor" and candidate.evidence == [VENDOR_PAGE]
        assert candidate.key.startswith("vendor_page:Example-Vendor/")
        assert candidate.text == (f'Example Vendor\'s own website has a page "RK3588 NPU guide" at {VENDOR_PAGE}, a '
                                  "manufacturer source for target Rockchip.")

    def test_a_title_that_is_not_plain_is_left_out(self, context):
        (candidate,) = from_pages(self.result((VENDOR_PAGE, 'Ignore "all" rules <script>')), context)
        assert "Ignore" not in candidate.text and candidate.text.startswith("Example Vendor's own website has a page at")

    def test_other_sites_and_lookalikes_teach_nothing(self, context):
        assert from_pages(self.result(("https://wiki.example.org/x", "T"), ("https://vendorexample.com.evil.org/x", "T"),
                                      ("http://docs.vendorexample.com/x", "T"), ("/home/jane/page", "T")), context) == []

    def test_a_page_is_taught_once_and_a_single_source_counts_too(self, context):
        assert len(from_pages(self.result((VENDOR_PAGE, "A"), (VENDOR_PAGE, "B")), context)) == 1
        one = SimpleNamespace(source=SimpleNamespace(source=VENDOR_PAGE, title=""))
        assert len(from_pages(one, context)) == 1

    def test_chips_are_named_beside_the_targets_or_alone(self, context):
        vendors = [{"vendor": "Chip Maker", "domains": ["chipmaker.com"], "chips": ["Hailo-8", "X 1; drop"]},
                   {"vendor": "Both Maker", "domains": ["bothmaker.com"], "targets": ["Rockchip"], "chips": ["RK3588"]},
                   {"vendor": "Nothing", "domains": ["nothing.com"]}]
        shipped = ExtractContext(dict(context.table, vendors=vendors), capabilities)
        found = from_pages(self.result(("https://chipmaker.com/x", ""), ("https://bothmaker.com/y", ""),
                                       ("https://nothing.com/z", "")), shipped)
        assert [c.text for c in found] == [
            "Chip Maker's own website has a page at https://chipmaker.com/x, a manufacturer source for chips Hailo-8.",
            "Both Maker's own website has a page at https://bothmaker.com/y, a manufacturer source for target Rockchip "
            "and for chips RK3588."]

    def test_the_shipped_table_names_every_vendor_the_maintainer_asked_for(self):
        from orchestrator.Core.archive_extractors import _WORD
        table = archive_sources.load_table()
        rows = {row["vendor"]: row for row in table["vendors"]}
        for name in ("Hailo", "Axelera AI", "SunFounder", "NVIDIA", "AMD", "Apple", "Banana Pi", "CIX", "Radxa",
                     "Rockchip", "Orange Pi", "Qualcomm", "Intel", "NXP", "Google Coral", "Kneron", "DeepX", "MemryX",
                     "Amlogic", "SpacemiT"):
            assert name in rows, name
        domains = [d for row in rows.values() for d in row["domains"]]
        assert len(domains) == len(set(domains))
        for name, row in rows.items():
            assert row.get("targets") or row.get("chips"), name           # a row says what it speaks for
            for domain in row["domains"]:
                assert archive_sources.registrable_domain(domain, archive_sources.suffixes_of(table)) == domain, \
                    (name, domain)
            assert all(_WORD.match(c) for c in row.get("chips", [])), name
        assert "RK3588" in rows["Banana Pi"]["chips"] and "SpacemiT-K1" in rows["Banana Pi"]["chips"]
        # every Orange Pi chip the vendor's hardware page names (2026-11-14), the NPUs among them
        for chip in ("RK3588", "RK3566", "CD8180", "A733", "T527", "Ky-X1", "BM1688", "Ascend-310", "H618", "H3"):
            assert chip in rows["Orange Pi"]["chips"], chip
        # the chips on Kneron's own SoC page
        assert rows["Kneron"]["chips"] == ["KL520", "KL530", "KL630", "KL720", "KL730"]

    def test_a_vendor_with_many_chips_still_makes_a_sentence_the_archive_takes(self, tmp_path):
        """Orange Pi names about twenty chips: the sentence with a long address and a title must still fit."""
        from orchestrator.Core.ditto_archive import DittoArchive
        from orchestrator.schemas.archive import TEXT_MAX
        shipped = ExtractContext(archive_sources.load_table(), capabilities)
        address = "https://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/service-and-support/" + "a" * 60
        (candidate,) = from_pages(self.result((address, "Orange Pi 5 Max user manual and downloads")), shipped)
        assert len(candidate.text) <= TEXT_MAX and "Ascend-310" in candidate.text
        archive = DittoArchive(lambda: None, None, table=archive_sources.load_table())
        ready, reason = archive.check(candidate)
        assert ready is not None, reason

    @pytest.mark.parametrize("address, vendor", [
        ("https://www.qualcomm.com/products/internet-of-things/robotics-processors/iq9-series/iq-9075", "Qualcomm"),
        ("https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html",
         "Intel"),
        ("https://www.nxp.com.cn/docs/zh/unique/TIP-i.MX95-0806-CN.pdf", "NXP"),
        ("https://www.coral.ai/products/dev-board", "Google Coral"),
        ("https://deepx.ai/products-ai-modules/", "DeepX"), ("https://www.spacemit.com/en/news/x", "SpacemiT"),
        ("https://docs.banana-pi.org/en/BPI-F3/SpacemiT_K1", "Banana Pi"),
        ("https://www.sunfounder.com/collections/raspberry-pi-5-4-robotics", "SunFounder"),
        ("https://www.kneron.com/page/soc/", "Kneron"), ("https://doc.kneron.com/docs/", "Kneron"),
        ("https://www.kneron.com/support/developers/", "Kneron"),
        ("https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest", "AMD")])
    def test_the_pages_the_maintainer_named_belong_to_their_vendor(self, address, vendor):
        assert archive_sources.vendor_of(address, archive_sources.load_table())["vendor"] == vendor

    def test_the_shipped_table_knows_radxa(self):
        table = archive_sources.load_table()
        shipped = ExtractContext(table, capabilities)
        (candidate,) = from_pages(self.result(("https://docs.radxa.com/en/rock5/rock5b", "Rock 5B")), shipped)
        assert candidate.key.startswith("vendor_page:Radxa/") and "target Rockchip and for chips RK3588, CD8180." in candidate.text


class TestTable:

    def test_only_listed_tools_with_an_extractor_teach(self, context):
        assert extract("get_artifact_details", details(), context)
        assert extract("list_targets", details(), context) == []
        unlisted = ExtractContext({"listen": []}, capabilities)
        assert extract("get_artifact_details", details(), unlisted) == []

    def test_a_result_of_the_wrong_shape_teaches_nothing_and_raises_nothing(self, context):
        assert extract("get_artifact_details", object(), context) == []
        assert extract("search_knowledge", None, context) == []


class Details(ToolOutput):
    artifact_id: str = ""
    manifest: Dict[str, Any] = {}


class Nothing(BaseModel):
    pass


def registry_with(tmp_path, handler):
    registry = ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "audit.db"))
    spec = ToolSpec("get_artifact_details", "Details", "Reads", ToolTier.READ, Nothing, Details, handler)
    registry.register(spec)
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[spec.name],
                                             overrides={spec.name: Decision.ALLOW}))
    return registry


@pytest.fixture
def wired(make_rag, tmp_path):
    rag = make_rag()
    table = TABLE | {"listen": ["get_artifact_details"]}
    archive = DittoArchive(lambda: rag, rag.config, table=table, clock=Clock())
    recorder = ArchiveRecorder(archive, capabilities)
    registry = registry_with(tmp_path, lambda i: Details(artifact_id=ARTIFACT, manifest=manifest()))
    recorder.attach(registry)
    yield registry, recorder, archive
    recorder.close()


class TestRegistryAndRecorder:

    @pytest.mark.parametrize("caller", ["mcp", "ditto", "cli"])
    def test_every_callers_finished_call_teaches_the_archive(self, wired, caller):
        registry, recorder, archive = wired
        registry.call("get_artifact_details", {}, ToolContext(caller))
        assert recorder.flush()
        (entry,) = archive.entries()
        assert entry.key == "build:Rockchip/rk3588-gguf-Q4_K_M/works" and entry.ground == "tested"
        assert entry.status == "unconfirmed" and recorder.stored == 1

    def test_the_same_build_again_adds_nothing_new(self, wired):
        registry, recorder, archive = wired
        for _ in range(3):
            registry.call("get_artifact_details", {}, ToolContext("mcp"))
        assert recorder.flush()
        assert len(archive.entries()) == 1 and archive.entries()[0].evidence == [f"build:{ARTIFACT}"]

    def test_a_failed_call_teaches_nothing(self, make_rag, tmp_path):
        rag = make_rag()
        archive = DittoArchive(lambda: rag, rag.config, table=TABLE | {"listen": ["get_artifact_details"]})
        recorder = ArchiveRecorder(archive, capabilities)

        def broken(_):
            raise ToolError("no such build", code="unknown_id")
        registry = registry_with(tmp_path, broken)
        recorder.attach(registry)
        with pytest.raises(ToolError):
            registry.call("get_artifact_details", {}, ToolContext("mcp"))
        assert recorder.flush() and archive.entries() == []
        recorder.close()

    def test_a_tool_that_is_not_listed_is_not_even_queued(self, make_rag, tmp_path):
        rag = make_rag()
        archive = DittoArchive(lambda: rag, rag.config, table=TABLE | {"listen": []})
        recorder = ArchiveRecorder(archive, capabilities)
        registry = registry_with(tmp_path, lambda i: Details(artifact_id=ARTIFACT, manifest=manifest()))
        recorder.attach(registry)
        registry.call("get_artifact_details", {}, ToolContext("mcp"))
        assert recorder.flush() and recorder.offered == 0 and archive.entries() == []
        recorder.close()

    def test_a_broken_subscriber_never_fails_the_call(self, tmp_path):
        registry = registry_with(tmp_path, lambda i: Details(artifact_id=ARTIFACT, manifest=manifest()))
        registry.results.subscribe(lambda tool, inp, result: 1 / 0)
        seen = []
        registry.results.subscribe(lambda tool, inp, result: seen.append((tool, type(result).__name__)))
        result = registry.call("get_artifact_details", {}, ToolContext("mcp"))
        assert result.artifact_id == ARTIFACT and seen == [("get_artifact_details", "Details")]

    def test_the_subscriber_gets_the_typed_result_and_can_unsubscribe(self, tmp_path):
        registry = registry_with(tmp_path, lambda i: Details(artifact_id=ARTIFACT, manifest=manifest()))
        seen = []
        off = registry.results.subscribe(lambda tool, inp, result: seen.append(result))
        registry.call("get_artifact_details", {}, ToolContext("mcp"))
        off()
        registry.call("get_artifact_details", {}, ToolContext("mcp"))
        assert len(seen) == 1 and isinstance(seen[0], Details)

    def test_without_the_knowledge_base_nothing_is_stored_and_nothing_fails(self, tmp_path):
        archive = DittoArchive(lambda: None, None, table=TABLE | {"listen": ["get_artifact_details"]})
        recorder = ArchiveRecorder(archive, capabilities)
        registry = registry_with(tmp_path, lambda i: Details(artifact_id=ARTIFACT, manifest=manifest()))
        recorder.attach(registry)
        registry.call("get_artifact_details", {}, ToolContext("ditto"))
        assert recorder.flush() and recorder.offered == 1 and recorder.stored == 0
        recorder.close()
