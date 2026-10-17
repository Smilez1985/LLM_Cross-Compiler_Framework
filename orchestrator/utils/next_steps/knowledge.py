#!/usr/bin/env python3
"""
Next steps of the knowledge base, its snapshots and the Community Hub (Tools > Knowledge > ...; chain knowledge
refines them). Seeded by chain a0 from the wave-6 audit (items 6-8, 13, 14, 32-34, 48, 61).
"""

from orchestrator.utils.next_steps import Param, Step

_SNAP = "act_knowledge_snapshots"
_HUB = "act_knowledge_hub"
_EXPORT = {"kind": Param("choice", ("target", "recipe", "knowledge")), "source": Param("id")}

MOUSE_LABELS = {
    "btn_knowledge_snapshots_delete": (_SNAP, "snapshots.delete"),
    "btn_knowledge_snapshots_export": (_SNAP, "snapshots.export"),
    "btn_knowledge_storage_delete_old": (_SNAP, "storage.old.delete"),
    "btn_knowledge_storage_convert": (_SNAP, ("storage.convert", "storage.convert.now")),
    "btn_zoo_import": (_HUB, "main.hub.import"),
    "btn_zoo_export": (_HUB, "zoo.export"),
    "btn_zoo_keys": (_HUB, "zoo.keys"),
    "tbl_zoo_packs": (_HUB, "main.hub.install"),   # the Install button in the pack's row
}

STEPS = (
    Step("snapshots.free", cli="knowledge snapshots prune", person_only="deletion", window="knowledge_snapshots",
         target="btn_knowledge_snapshots_delete"),
    Step("snapshots.export", cli="knowledge snapshots export", cli_args=("{names}", "--to", "{folder}"),
         params={"names": Param("id"), "folder": Param("path")}, person_only="permission",
         window="knowledge_snapshots", target="btn_knowledge_snapshots_export"),
    Step("snapshots.list", cli="knowledge snapshots list", person_only="permission", window="knowledge_snapshots",
         target=_SNAP, runnable=True),
    Step("storage.delete_old", cli="knowledge storage delete-old", person_only="deletion",
         window="knowledge_snapshots", target="btn_knowledge_storage_delete_old"),
    Step("storage.convert", cli="knowledge storage convert", person_only="permission", window="knowledge_snapshots",
         target="btn_knowledge_storage_convert", runnable=True),
    Step("knowledge.index_docs", cli="knowledge index-docs", tool="index_framework_docs", window="knowledge_sources",
         target="act_knowledge_index_docs"),
    Step("knowledge.sources", cli="knowledge sources", tool="list_knowledge_sources", window="knowledge_sources",
         target="act_knowledge_sources", runnable=True),
    Step("hub.open", cli="zoo", tool="list_packs", window="community_hub", target=_HUB),
    Step("pack.passphrase", cli="zoo install --passphrase", cli_args=("--file", "{file}", "--passphrase"),
         params={"file": Param("path")}, person_only="secret", window="community_hub",
         target="btn_zoo_import"),
    Step("pack.reinstall", cli="zoo install", cli_args=("{pack}",), params={"pack": Param("id")},
         tool="add_pack_from_index", window="community_hub",
         target="tbl_zoo_packs"),
    Step("pack.install_file", cli="zoo install --file", cli_args=("--file", "{file}"), params={"file": Param("path")},
         person_only="permission", window="community_hub",
         target="btn_zoo_import"),
    Step("pack.publish", cli="zoo export --pr", cli_args=("{kind}", "{source}", "--pr"), params=_EXPORT,
         person_only="permission", window="community_hub",
         target="btn_zoo_export"),
    Step("pack.export_to", cli="zoo export --to", cli_args=("{kind}", "{source}", "--to", "{key_file}"),
         params={**_EXPORT, "key_file": Param("path")}, tool="export_pack", window="community_hub",
         target="btn_zoo_export"),
    Step("pack.remove", person_only="deletion", window="community_hub", target="btn_zoo_remove", planned=True),
    Step("keys.show", cli="zoo keys", person_only="secret", window="community_hub", target="btn_zoo_keys"),
    Step("keys.new", person_only="secret", window="community_hub", target="btn_zoo_keys_new", planned=True),
)

TEXTS = {
    "en": {
        "step.snapshots.free": "Free space: delete knowledge snapshots you no longer need (you tick what goes)",
        "step.snapshots.export": "Export knowledge snapshots to another folder",
        "step.snapshots.list": "Look at the knowledge snapshots",
        "step.snapshots.list.what": "Lists the knowledge snapshots with name, size and date, and whether the next "
                                    "one fits on the disk.",
        "step.storage.delete_old": "Delete the old storage of the knowledge base (it lists what goes)",
        "step.storage.convert": "Convert the knowledge base's storage again",
        "step.storage.convert.what": "Converts the knowledge base's storage of an earlier version to the current "
                                     "one; the old storage stays until you delete it.",
        "step.storage.convert.why": "The last conversion did not finish.",
        "step.knowledge.index_docs": "Index the framework's documentation",
        "step.knowledge.sources": "Look at the knowledge sources",
        "step.knowledge.sources.what": "Lists every source of the knowledge base with its kind, licence and "
                                       "copyright notice.",
        "step.hub.open": "Open the Community Hub",
        "step.pack.passphrase": "Install the pack with its passphrase",
        "step.pack.reinstall": "Install the pack again from the Community Hub",
        "step.pack.install_file": "Install a pack file",
        "step.pack.publish": "Publish the pack as a pull request",
        "step.pack.export_to": "Export the pack encrypted to the recipients' keys",
        "step.pack.remove": "Remove the pack (it lists what goes)",
        "step.keys.show": "Show your public keys",
        "step.keys.new": "Create new keys",
    },
    "de": {
        "step.snapshots.free": "Platz schaffen: Wissens-Snapshots löschen, die du nicht mehr brauchst (du hakst an, "
                               "was gelöscht wird)",
        "step.snapshots.export": "Wissens-Snapshots in einen anderen Ordner exportieren",
        "step.snapshots.list": "Die Wissens-Snapshots ansehen",
        "step.snapshots.list.what": "Listet die Wissens-Snapshots mit Name, Größe und Datum, und ob der nächste auf "
                                    "die Festplatte passt.",
        "step.storage.delete_old": "Den alten Speicher der Wissensdatenbank löschen (er listet, was gelöscht wird)",
        "step.storage.convert": "Den Speicher der Wissensdatenbank erneut umwandeln",
        "step.storage.convert.what": "Wandelt den Speicher der Wissensdatenbank einer früheren Version in den "
                                     "jetzigen um; der alte Speicher bleibt, bis du ihn löschst.",
        "step.storage.convert.why": "Die letzte Umwandlung wurde nicht fertig.",
        "step.knowledge.index_docs": "Die Dokumentation des Frameworks indexieren",
        "step.knowledge.sources": "Die Wissensquellen ansehen",
        "step.knowledge.sources.what": "Listet jede Quelle der Wissensdatenbank mit Art, Lizenz und "
                                       "Urheberrechtshinweis.",
        "step.hub.open": "Den Community Hub öffnen",
        "step.pack.passphrase": "Das Paket mit seiner Passphrase installieren",
        "step.pack.reinstall": "Das Paket erneut aus dem Community Hub installieren",
        "step.pack.install_file": "Eine Paketdatei installieren",
        "step.pack.publish": "Das Paket als Pull Request veröffentlichen",
        "step.pack.export_to": "Das Paket verschlüsselt für die Schlüssel der Empfänger exportieren",
        "step.pack.remove": "Das Paket entfernen (es listet, was gelöscht wird)",
        "step.keys.show": "Deine öffentlichen Schlüssel anzeigen",
        "step.keys.new": "Neue Schlüssel erstellen",
    },
}

# The "Sie" form of the German texts above that address the person (Q1); only the texts that differ.
FORMAL = {
    "de": {
        "step.keys.show": "Ihre öffentlichen Schlüssel anzeigen",
        "step.snapshots.free": "Platz schaffen: Wissens-Snapshots löschen, die Sie nicht mehr brauchen (Sie haken an, "
                               "was gelöscht wird)",
        "step.storage.convert.what": "Wandelt den Speicher der Wissensdatenbank einer früheren Version in den jetzigen "
                                     "um; der alte Speicher bleibt, bis Sie ihn löschen.",
    },
}
