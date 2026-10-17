#!/usr/bin/env python3
"""
Next steps of the tool permissions, the approvals, the AI folders, the activity record and the AI app connections
(Ditto > Tool Permissions..., Security > AI App Connections (MCP)...; chain ai-oversight refines them). Seeded by
chain a0 from the wave-6 audit (items 11, 35-38, 41, 44, 49). An AI never changes its own permissions: every step here
is the person's.
"""

from orchestrator.utils.next_steps import Param, Step

_MCP = "act_tools_mcp_connections"

MOUSE_LABELS = {
    "btn_tokens_permissions": (_MCP, "tmcp.tokens.permissions"),
    "btn_tokens_add": (_MCP, "tmcp.tokens.add"),
    "btn_audit_archive": ("act_tools_mcp_audit", "tmcp.audit.archive"),
    "btn_ai_folders_add": ("act_tools_folders", "main.folders.add"),
}

STEPS = (
    Step("permissions.edit", cli="tools permissions", person_only="permission", window="tool_permissions",
         target="act_tools_permissions"),
    Step("permissions.token", cli="tools permissions --token", cli_args=("--token", "{token}"),
         params={"token": Param("id")}, person_only="permission", target="btn_tokens_permissions"),
    Step("tokens.add", cli="mcp token add", cli_args=("{name}",), params={"name": Param("id")}, person_only="secret",
         target="btn_tokens_add"),
    Step("tokens.list", cli="mcp token list", person_only="secret", target=_MCP, runnable=True),
    Step("tokens.reset_store", person_only="deletion", target="btn_tokens_reset_store", planned=True),
    Step("audit.archive", cli="tools audit --archive", cli_args=("--archive",), person_only="audit",
         target="btn_audit_archive"),
    Step("audit.show", cli="tools audit", person_only="audit", target="act_tools_mcp_audit", runnable=True),
    Step("approvals.list", cli="tools approvals list", person_only="permission", window="approvals",
         target="act_tools_approvals", runnable=True),
    Step("folders.add", cli="tools folders add", cli_args=("{folder}", "--label", "{label}"),
         params={"folder": Param("path"), "label": Param("label")}, person_only="permission",
         target="btn_ai_folders_add"),
    Step("mcp.connect", cli="mcp config", person_only="permission", target=_MCP),
    Step("crawl.notice", cli="tools crawl-notice", person_only="licence", window="knowledge_sources",
         target="act_tools_mcp_crawl_notice"),
    Step("proposals.show", person_only="permission", window="proposals", target="act_tools_proposals"),
)

TEXTS = {
    "en": {
        "step.permissions.edit": "Allow it in the tool permissions",
        "step.permissions.token": "Change the permissions of the AI app's access code",
        "step.tokens.add": "Create an access code for the AI app",
        "step.tokens.list": "Look at the AI apps' access codes",
        "step.tokens.list.what": "Lists the names of the access codes with their state; the codes themselves are "
                                 "never shown.",
        "step.tokens.reset_store": "Reset the access codes (it lists what goes)",
        "step.audit.archive": "Archive the activity record and start a new one",
        "step.audit.show": "Look at the activity record",
        "step.audit.show.what": "Lists the latest entries of the activity record: who called which tool, when and "
                                "with which result.",
        "step.approvals.list": "Look at the calls that wait for your approval",
        "step.approvals.list.what": "Lists the AI apps' calls that wait for your approval.",
        "step.folders.add": "Share the folder with the AI (Add folder)",
        "step.mcp.connect": "Connect the AI app to the framework",
        "step.crawl.notice": "Read the Deep Ingest notice",
        "step.proposals.show": "Read the fix proposals of the AI apps",
    },
    "de": {
        "step.permissions.edit": "Es in den Tool-Berechtigungen erlauben",
        "step.permissions.token": "Die Berechtigungen des Zugangscodes der KI-App ändern",
        "step.tokens.add": "Einen Zugangscode für die KI-App erstellen",
        "step.tokens.list": "Die Zugangscodes der KI-Apps ansehen",
        "step.tokens.list.what": "Listet die Namen der Zugangscodes mit ihrem Zustand; die Codes selbst werden nie "
                                 "angezeigt.",
        "step.tokens.reset_store": "Die Zugangscodes zurücksetzen (es listet, was gelöscht wird)",
        "step.audit.archive": "Das Aktivitätsprotokoll archivieren und ein neues beginnen",
        "step.audit.show": "Das Aktivitätsprotokoll ansehen",
        "step.audit.show.what": "Listet die neuesten Einträge des Aktivitätsprotokolls: wer welches Tool wann mit "
                                "welchem Ergebnis aufgerufen hat.",
        "step.approvals.list": "Die Aufrufe ansehen, die auf deine Freigabe warten",
        "step.approvals.list.what": "Listet die Aufrufe der KI-Apps, die auf deine Freigabe warten.",
        "step.folders.add": "Den Ordner für die KI freigeben (Ordner hinzufügen)",
        "step.mcp.connect": "Die KI-App mit dem Framework verbinden",
        "step.crawl.notice": "Den Hinweis zu Deep Ingest lesen",
        "step.proposals.show": "Die Korrekturvorschläge der KI-Apps lesen",
    },
}
