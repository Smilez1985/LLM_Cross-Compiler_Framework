"""The person's `knowledge clear` for tests that empty a knowledge base (RAGManager.clear_knowledge_base takes only
the plan the person accepted, with the audit log)."""

from orchestrator.Core.deletion import accept


class Audit:
    def __init__(self):
        self.entries = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.entries.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))
        return len(self.entries)


def clear_knowledge(rag, actor: str = "cli"):
    """Empties `rag` as the person would: its plan accepted, the snapshot first, audited. An empty knowledge base has
    nothing to empty (True)."""
    plan = rag.clear_plan()
    return True if plan is None else rag.clear_knowledge_base(accept(plan, actor, "permanent"), audit=Audit())
