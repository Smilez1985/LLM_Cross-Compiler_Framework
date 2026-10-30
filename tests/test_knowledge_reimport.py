"""Excerpts an earlier version imported without their pack's digest and topic or target: Mojo Oracle finds the packs
(when the knowledge base is open), and Mojo Miracle imports them again only after the person's own yes to the plan,
which lists each pack with its pages and size first. An AI cannot start it.

Real knowledge base (Qdrant Edge, the fake embedders of test_rag); documentation addresses only."""

import json
from types import SimpleNamespace

from orchestrator.Core.mojo import Mojo
from orchestrator.Core.mojo.checks.community import check_community
from orchestrator.Core.mojo.offers import offer_for
from orchestrator.Core.mojo.repairs.engine import apply_repair
from orchestrator.Core.mojo.views import miracle_acts, oracle_rows, plan_steps
from test_edge_store import backend  # noqa: F401 - the fixture
from test_rag import make_rag  # noqa: F401 - the fixture
from test_zoo import World, pack_bytes

PAGE = {"source": "https://docs.example.com/npu", "content": "Run the model on the NPU of the board.", "metadata": {}}
CODE = "community.knowledge.untagged"


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def old_import(tmp_path, rag):
    """pack npu-guide (topic npu) installed and imported, then its excerpts as an earlier version stored them: the
    pack named, but neither its digest nor its topic."""
    world = World(tmp_path)
    world.framework.get_component = lambda name: rag if name == "rag_manager" else None
    root = world.tmp / "src" / "npu-guide"
    root.mkdir(parents=True)
    (root / "knowledge.json").write_text(json.dumps([PAGE]), encoding="utf-8")
    world.add(pack_bytes(root, kind="knowledge", pack_id="npu-guide", version="1.0.0", topic="npu"),
              kind="knowledge", pack_id="npu-guide", version="1.0.0")
    world.publish()
    folder = world.cm.finish_install(world.cm.prepare_install("knowledge", "npu-guide"))
    assert rag.ingest_document(PAGE["source"], PAGE["content"], {"origin_pack": "npu-guide"})
    framework = SimpleNamespace(community_manager=world.cm, rag_manager=rag, config=world.config,
                                tool_registry=SimpleNamespace(audit=Audit()))
    return world, folder, framework


def topics(rag):
    return {s.get("topic") for s in rag.list_sources() if s["source"] == PAGE["source"]}


def test_oracle_finds_the_old_import_and_the_plan_shows_pages_and_size(make_rag, tmp_path):  # noqa: F811
    rag = make_rag()
    world, folder, framework = old_import(tmp_path, rag)
    (item,) = world.cm.untagged_imports(rag)
    assert (item.label, item.folder, item.pages, item.belongs) == ("knowledge pack npu-guide", folder, 1, "topic npu")
    assert item.excerpts >= 1 and item.size > 0
    (finding,) = [f for f in check_community(framework) if f.code == CODE]
    assert "knowledge pack npu-guide" in finding.message and "search_knowledge(topic=...)" in finding.why
    offer = offer_for(framework, finding)
    assert offer.verdict == "miracle_yes" and offer.plan.repair_class() == "person_yes"
    assert offer in miracle_acts([offer]), "Mojo Miracle is offered for it"
    (step,) = plan_steps(offer)
    assert step.kind == "reimport" and step.full_path == str(folder) and "1 page(s)" in step.after
    assert "a knowledge snapshot first" in offer.plan.summary
    # an AI reads it as the person's: no tool starts it
    from orchestrator.Core.mojo.findings import MojoReport
    (row,) = [r for r in oracle_rows(framework, MojoReport([finding]), [offer]) if r["code"] == CODE]
    assert row["repair"]["how"] == "person" and "No tool starts it" in row["repair"]["next"]
    assert str(folder) not in json.dumps(row["repair"])


def test_only_the_persons_yes_imports_it_again(make_rag, tmp_path):  # noqa: F811
    rag = make_rag()
    world, folder, framework = old_import(tmp_path, rag)
    # an AI's call (decision ask) and a run without the person are refused: nothing changes
    refused = apply_repair(framework, CODE, actor="mcp", decision="ask", answered_by="person")
    assert refused.outcome == "refused" and world.cm.untagged_imports(rag)
    engine = Mojo(framework, checks=[check_community])
    report = engine.run()
    asked = []
    result = engine.miracle(actor="cli", at_terminal=True, before=report, decision="person", answered_by="person",
                            confirm=lambda offer: asked.append(offer.plan.summary) or False)
    (entry,) = [e for e in result.entries if e.finding.code == CODE]
    assert entry.outcome == "declined" and asked and world.cm.untagged_imports(rag), "No changes nothing"
    no_person = engine.miracle(actor="cli", at_terminal=False, before=engine.run())
    assert [e.outcome for e in no_person.entries if e.finding.code == CODE] == ["not_done"]
    # the person's yes: imported again, with its topic, after a snapshot
    result = engine.miracle(actor="cli", at_terminal=True, before=engine.run(), decision="person",
                            answered_by="person", confirm=lambda offer: True)
    (entry,) = [e for e in result.entries if e.finding.code == CODE]
    assert entry.outcome == "repaired", entry.reason
    assert world.cm.untagged_imports(rag) == [] and topics(rag) == {"npu"}
    assert world.cm.last_sync_snapshot, "a snapshot was taken before the import"
    assert any(kwargs.get("extra", {}).get("repair_class") == "person_yes"
               for _, kwargs in framework.tool_registry.audit.rows)


def test_without_an_open_knowledge_base_mojo_finds_nothing(tmp_path):
    world = World(tmp_path)
    assert world.cm.untagged_imports(None) == []
    framework = SimpleNamespace(community_manager=world.cm, rag_manager=None)
    assert not [f for f in check_community(framework) if f.code == CODE]


def test_the_cli_asks_with_the_plan_and_no_as_the_default(monkeypatch, capsys):
    import orchestrator.cli as cli_module
    step = SimpleNamespace(target_label="knowledge pack npu-guide", full_path="/srv/llm/knowledge/npu/npu-guide",
                           before="3 excerpt(s) without topic or target", after="imported again under topic npu")
    offer = SimpleNamespace(verdict="miracle_yes", plan=SimpleNamespace(summary="import 1 installed pack(s) again",
                                                                        steps=[step]))
    asked = []
    monkeypatch.setattr(cli_module.click, "confirm", lambda text, default: asked.append((text, default)) or True)
    assert cli_module._mojo_confirm(offer) is True
    assert asked == [("Do it now?", False)]
    out = capsys.readouterr().out
    assert "import 1 installed pack(s) again" in out and "/srv/llm/knowledge/npu/npu-guide" in out
