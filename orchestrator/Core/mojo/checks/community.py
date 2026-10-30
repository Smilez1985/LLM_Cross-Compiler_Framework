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
    findings = []
    for folder, prov, trust, problems in cm.verify_installed():
        name = f"{prov.kind} {prov.id} {prov.version}"
        if trust is not None and trust.value == "revoked":
            findings.append(Finding("community", f"community.{prov.id}.revoked", Severity.ERROR,
                                    f"Community {name} was revoked: {'; '.join(problems)}",
                                    f"Delete the folder {folder}; it is not built any more.", where=str(folder),
                                    why="Its publisher or the signed index withdrew it, for example because of a "
                                        "security problem.",
                                    # no list of the framework deletes a pack folder yet (WP7 adds its kind later):
                                    # the person deletes it, never Mojo Miracle
                                    cannot="person"))
        elif problems:
            findings.append(Finding("community", f"community.{prov.id}.changed", Severity.WARN,
                                    f"Community {name}: {'; '.join(problems)}",
                                    f"Check the changes, or delete {folder} and install it again (llm-cli zoo "
                                    "install).", where=str(folder),
                                    why="Its files are not the ones that were signed; someone or something changed "
                                        "them.", cannot="person"))
        elif trust is not None and trust.value == "local":
            findings.append(Finding("community", f"community.{prov.id}.local", Severity.INFO,
                                    f"Community {name} was imported from a file without a signed index entry."))
        else:
            findings.append(_ok("community", f"community.{prov.id}", f"Community {name} ({trust.value if trust else '?'})"))
    return findings + check_loose_knowledge(cm)


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
