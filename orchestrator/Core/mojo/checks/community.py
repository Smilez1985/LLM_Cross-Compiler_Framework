#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: installed community packs against the cached signed index, and the loose
knowledge files that wait to be sorted into a topic or target folder
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_community(framework) -> List[Finding]:
    """Installed community packs against the cached signed index (no network): revoked or changed packs."""
    cm = getattr(framework, "community_manager", None)
    if cm is None:
        return []
    from orchestrator.Core.community_manager import CHANGED, changed_text   # loaded with the check, not with Mojo
    findings = []
    for folder, prov, trust, problems in cm.verify_installed():
        name = f"{prov.kind} {prov.id} {prov.version}"
        if trust is not None and trust.value == "revoked":
            findings.append(Finding("community", f"community.{prov.id}.revoked", Severity.ERROR,
                                    f"Community {name} was revoked: {'; '.join(problems)}",
                                    f"Uninstall {folder} (it lists what goes, \"No\" by default); it is not "
                                    "built any more.", where=str(folder),
                                    why="Its publisher or the signed index withdrew it, for example because of a "
                                        "security problem.",
                                    # the person uninstalls it (Core/deletion.py), never Mojo Miracle
                                    cannot="person", steps=("pack.remove",)))
        elif problems:
            findings.append(Finding("community", f"community.{prov.id}.changed", Severity.WARN,
                                    f"Community {name}: {'; '.join(problems)}",
                                    (f"{changed_text(prov)[0].upper()}{changed_text(prov)[1:]}." if CHANGED in
                                     problems else "Check the changes; then uninstall it and install it again."),
                                    where=str(folder),
                                    why="Its files are not the ones that were signed; someone or something changed "
                                        "them. The check against the signed digest is the security gate, so an "
                                        "edited pack is not imported or built as the signed one.",
                                    cannot="person", steps=("pack.remove",)))
        elif trust is not None and trust.value == "local":
            findings.append(Finding("community", f"community.{prov.id}.local", Severity.INFO,
                                    f"Community {name} was imported from a file without a signed index entry."))
        else:
            findings.append(_ok("community", f"community.{prov.id}", f"Community {name} ({trust.value if trust else '?'})"))
    return (findings + check_loose_knowledge(cm) + check_knowledge_links(cm)
            + check_untagged_knowledge(cm, getattr(framework, "rag_manager", None)))


UNTAGGED_WHY = ("An earlier version imported these packs before every excerpt carried its pack's digest and topic or "
                "target: search_knowledge(topic=...), the Topic or target column and the knowledge exports do not find "
                "them under their topic or target, and a newer version or an uninstall of the pack cannot always tell "
                "them apart. Importing them again stores both; a snapshot is taken first, so it can be undone.")


def check_untagged_knowledge(cm, rag_manager) -> List[Finding]:
    """The installed packs whose excerpts were imported before they carried their topic or target
    (CommunityManager.untagged_imports), when the knowledge base is open (Mojo never opens it): a warning with each
    pack and its excerpts; Mojo Miracle imports them again after the person's yes (knowledge.reimport)."""
    lister = getattr(cm, "untagged_imports", None)
    found = lister(rag_manager) if callable(lister) and rag_manager is not None else []
    if not found:
        return []
    names = ", ".join(f"{item.label} ({item.excerpts} excerpt(s))" for item in found[:10]) + \
        (f" and {len(found) - 10} more" if len(found) > 10 else "")
    return [Finding("community", "community.knowledge.untagged", Severity.WARN,
                    f"{len(found)} installed pack(s) were imported without their topic or target: {names}",
                    "Import them again: Mojo Miracle shows the packs, their pages and size first and asks you (No is "
                    "the default).", where=str(cm.knowledge_dir), why=UNTAGGED_WHY, repair_id="knowledge.reimport")]


def check_knowledge_links(cm) -> List[Finding]:
    """Links in the knowledge folder (CommunityManager.ignored_links): ignored on purpose, and said with the reason
    instead of silently. Reads the folder only."""
    note = getattr(cm, "links_note", None)
    text = note() if callable(note) else ""
    if not text:
        return []
    return [Finding("community", "community.knowledge.links", Severity.WARN, text,
                    "Move the folder into community/knowledge instead of linking it.", where=str(cm.knowledge_dir),
                    why="A link can point anywhere; what the framework installs and checks stays in its own folders.",
                    cannot="person")]


LOOSE_WHY = ("Knowledge always belongs to a topic or a target. A loose file was never checked "
             "like a knowledge pack (its pages, their sources, the licence files), so it is not imported into the "
             "knowledge base until it is sorted into a topic or target folder.")


def check_loose_knowledge(cm) -> List[Finding]:
    """The loose knowledge files (CommunityManager.loose_knowledge): a warning with each file's name, never imported
    until the person sorts it (`llmcf knowledge loose`, the Community Hub's Sort loose knowledge...). Reads the folder
    only."""
    lister = getattr(cm, "loose_knowledge", None)
    loose = lister() if callable(lister) else []
    if not loose:
        return []
    names = ", ".join(item.label for item in loose[:10]) + (f" and {len(loose) - 10} more" if len(loose) > 10 else "")
    return [Finding("community", "community.knowledge.loose", Severity.WARN,
                    f"{len(loose)} loose knowledge file(s) are not imported into the knowledge base: {names}",
                    "Sort each into a topic or target folder: it is checked like a knowledge pack, then imported.",
                    where=str(cm.knowledge_dir), why=LOOSE_WHY, cannot="person", steps=("knowledge.loose",))]
