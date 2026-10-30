#!/usr/bin/env python3
"""
Texts of the knowledge windows in the GUI, English and German: the knowledge base's storage and whether the next
snapshot fits (Tools > Knowledge Snapshots...: Qdrant Edge, the conversion of the storage of earlier versions and its
old folder, which only the person deletes; DECISIONS RG5, round 5 f), Tools > Knowledge Sources..., and the Community
Hub's details, checks, import, export and public keys (DECISIONS W1': every `llmcf knowledge` and `llmcf zoo` action
also by mouse). LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused
there, so these never replace another text. Every key has the same {placeholders} in both languages
(tests/test_edge_store.py). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        "storage.used.edge": "The knowledge base is stored in Qdrant Edge in {folder}.",
        "storage.used.old": "The knowledge base uses the storage of earlier versions (qdrant-client's local mode) in "
                            "{folder}.",
        "storage.used.server": "The knowledge base uses a Qdrant server (setting qdrant_url); the local storage is "
                               "not used.",
        "storage.used.none": "The knowledge base cannot be opened: {reason}",
        "storage.reason.engine_missing": "Qdrant Edge is not available on this computer (the package qdrant-edge-py "
                                         "has no build for it, for example Windows on ARM), so the knowledge base "
                                         "stays in the older local storage; nothing can be converted.",
        "storage.reason.not_converted": "It has not been converted to Qdrant Edge yet; that happens the next time the "
                                        "knowledge base is opened.",
        "storage.reason.edge_unavailable": "it is stored in Qdrant Edge, but Qdrant Edge cannot be loaded on this "
                                           "computer (the package qdrant-edge-py is missing or broken). Nothing was "
                                           "changed, and the storage of earlier versions is not used in its place: "
                                           "it lacks what was added since the conversion. Install the framework's "
                                           "requirements again (the installer, or pip install --require-hashes --no-deps -r requirements.txt).",
        "storage.convert.offer": "[Convert now] converts it now: the texts and vectors are copied and checked, nothing "
                                 "is embedded again, and the old folder is kept.",
        "storage.converted": "It was converted from the storage of earlier versions on {when} ({points} excerpts; "
                             "counts and sample searches were checked).",
        "storage.old.folder.kept": "The old folder {folder} ({size}) is kept until you delete it.",
        "storage.old.folder": "Old storage folder: {folder} ({size}).",
        "storage.convert.now": "Convert now",
        "snapshots.next.no_room": "The next snapshot (about {size}) does not fit: Deep Ingest, restoring a snapshot "
                                  "and importing a knowledge pack are refused until there is room.",
        "snapshots.next.tight": "The next snapshot (about {size}) still fits, but leaves little free space.",
        "snapshots.next.step": "Free space first: delete or export the knowledge snapshots you no longer need "
                               "({path:act_knowledge_snapshots}), or free space on that disk otherwise. Nothing is "
                               "deleted by itself.",
        "storage.old.failed": "The knowledge base still uses the storage of earlier versions: its conversion to "
                              "Qdrant Edge failed ({reason}). It keeps working. It is tried again {retry}.",
        "storage.old.delete": "Delete the old storage...",
        "storage.old.title": "Old knowledge base storage",
        "storage.old.none": "There is no old storage folder to delete.",
        "storage.old.done.trash": "Moved to the trash: the old knowledge base storage. You can restore it from the "
                                  "trash; its space stays used until you empty the trash.",
        "storage.old.done.permanent": "Deleted: the old knowledge base storage.",
        "storage.old.kept": "Kept: the old knowledge base storage ({reason}).",
        "storage.retry.start": "on the next start, or now with [Try the conversion again]",
        "storage.retry.person": "only when you ask, because it would fail the same way: here with [Try the conversion "
                                "again]",
        "storage.convert": "Try the conversion again",
        "storage.converting": "Converting the knowledge base to Qdrant Edge: the texts and vectors are copied and "
                              "checked. With a large knowledge base this takes minutes. The old storage stays as it "
                              "is.",
        "storage.convert.done": "The knowledge base was converted to Qdrant Edge ({points} excerpts; counts and sample "
                                "searches were checked). The old folder is kept until you delete it.",
        "storage.convert.failed": "The knowledge base was not converted. It keeps working in the storage of earlier "
                                  "versions. Reason: {reason}",
        "storage.convert.off": "The knowledge base is off. Switch it on in {path:act_ditto_settings} first.",
        # Tools > Knowledge Sources...
        "knowledge.menu.sources": "Knowledge Sources...",
        "sources.title": "Knowledge Sources",
        "sources.intro": "Every source of the knowledge base with the licence and copyright notice it shows, and what "
                         "a knowledge pack would carry of it: its full text (its licence allows passing it on), only "
                         "its address and short facts, or nothing (a page of an installed pack, the framework's own "
                         "documents), and why. Nothing is changed here.",
        "sources.kind": "Kind:",
        "sources.filter.any": "Any kind",
        "sources.filter.web": "Web pages",
        "sources.filter.pack": "Knowledge pack pages",
        "sources.filter.docs": "Framework documents",
        "sources.filter.other": "Other",
        "sources.refresh": "Refresh",
        "sources.col.source": "Source",
        "sources.col.kind": "Kind",
        "sources.col.removed": "Hidden text left out",
        "sources.col.licence": "Licence",
        "sources.col.copyright": "Copyright notice",
        "sources.col.carried": "In a pack",
        "sources.col.why": "Why",
        "sources.loading": "Reading the knowledge base...",
        "sources.off": "The knowledge base is off. Switch it on in {path:act_ditto_settings} first.",
        "sources.unreadable": "The knowledge base cannot be read: {reason}",
        "sources.carried.full_text": "full text",
        "sources.carried.link_and_facts": "link and short facts",
        "sources.carried.not_exported": "not exported",
        # What was left out of outside text before it was stored (the words of knowledge_sources.REMOVED)
        "sources.removed.none": "none",
        "sources.removed.template": "template",
        "sources.removed.noscript": "noscript",
        "sources.removed.hidden_attribute": "hidden attribute",
        "sources.removed.aria_hidden": "aria-hidden",
        "sources.removed.display_none": "display:none",
        "sources.removed.visibility_hidden": "visibility:hidden",
        "sources.removed.font_size_zero": "font size 0",
        "sources.removed.opacity_zero": "opacity 0",
        "sources.removed.off_screen": "off-screen",
        "sources.removed.clipped": "clipped away",
        "sources.removed.same_colour": "text in its background colour",
        "sources.removed.invisible_characters": "invisible characters",
        "sources.removed.html_comments": "HTML comments",
        "sources.empty": "The knowledge base holds no source.",
        "sources.empty.kind": "The knowledge base holds no source of the kind {kind}.",
        "sources.totals": "{n} source(s): a knowledge pack would carry {full} with their full text and {facts} only as "
                          "their address and short facts.",
        # Tools > Community Hub: the new buttons
        "zoo.details": "Details...",
        "zoo.details.tip": "Licence, trust, installed folder, download address, size and sha256 of the selected pack, "
                           "and an installed recipe's recipe.yml",
        "zoo.verify_installed": "Check installed packs",
        "zoo.verify_installed.tip": "Checks every installed pack: revoked, changed since it was installed, licence "
                                    "files that do not match its pages",
        "zoo.verify_file": "Check a pack file...",
        "zoo.verify_file.tip": "Checks a pack file before you install or share it: format, hashes, validators and the "
                               "privacy scrub. Nothing is installed.",
        "zoo.export": "Export...",
        "zoo.refresh": "Refresh",
        "zoo.export.tip": "Export a target, a finished build (recipe) or your knowledge base as a scrubbed pack signed "
                          "with your key, plain for the community or encrypted for private sharing",
        "zoo.keys": "My public keys...",
        "zoo.keys.tip": "Your public keys, to give to people who send you encrypted packs or check your signed packs",
        "zoo.choose_first": "Choose a pack in the list first.",
        "zoo.uninstall": "Uninstall...",
        "zoo.uninstall.tip": "Uninstalls the selected installed pack: the exact list first (its folder, its import "
                             "notes, the excerpts only it holds), to the trash or for good; excerpts another pack "
                             "still holds stay",
        "zoo.uninstall.title": "Uninstall {kind} pack {id}",
        "zoo.uninstall.not_installed": "{id} is not installed; there is nothing to uninstall.",
        "zoo.details.loading": "Reading the details of {kind} {id}...",
        "zoo.details.title": "Pack details",
        "zoo.details.none": "{id} is neither in the cached index nor installed; Refresh downloads the index again.",
        "zoo.field.pack": "Pack",
        "zoo.field.licence": "Licence",
        "zoo.field.trust": "Trust",
        "zoo.field.installed": "Installed",
        "zoo.field.folder": "Installed folder",
        "zoo.field.download": "Download address",
        "zoo.field.size": "Size",
        "zoo.field.sha256": "sha256",
        "zoo.field.description": "Description",
        "zoo.field.recipe": "recipe.yml",
        "zoo.value.pack": "{kind} {id} {version} by {author}",
        "zoo.value.unknown_licence": "licence unknown",
        "zoo.value.not_installed": "no",
        "zoo.value.revoked": "{trust} - revoked: {reason}",
        "zoo.value.bytes": "{size} bytes",
        "zoo.verify.checking": "Checking the installed packs...",
        "zoo.verify.title": "Installed community packs",
        "zoo.verify.col.kind": "Kind",
        "zoo.verify.col.id": "ID",
        "zoo.verify.col.version": "Version",
        "zoo.verify.col.folder": "Folder",
        "zoo.verify.col.result": "Result",
        "zoo.verify.ok": "OK ({trust})",
        "zoo.verify.ok_plain": "OK",
        "zoo.verify.summary": "{n} installed pack(s) checked: {bad} with problems. Nothing was changed.",
        "zoo.verify.none": "No community packs installed.",
        "zoo.verify_file.choose": "Check a pack file",
        "zoo.verify_file.checking": "Checking {name}...",
        "zoo.verify_file.ok": "{kind} {id} {version}: acceptable (format, hashes, validators and privacy scrub). "
                              "Nothing was installed.",
        "zoo.verify_file.bad": "{kind} {id} {version} would be rejected:\n{problems}\nNothing was installed.",
        "zoo.import.title": "Import a pack file",
        "zoo.import.intro": "A pack file someone gave you, or an encrypted pack (.lcfpack) sent to your key or "
                            "protected with a passphrase. Its summary is shown before anything is installed, and you "
                            "decide (\"No\" is the default).",
        "zoo.import.file": "Pack file:",
        "zoo.import.browse": "Choose file...",
        "zoo.import.passphrase": "Passphrase:",
        "zoo.import.passphrase.hint": "only for a pack protected with a passphrase; it is never stored or shown",
        "zoo.import.senders": "Only accept these senders:",
        "zoo.import.senders.choose": "Choose key files...",
        "zoo.import.senders.clear": "Clear",
        "zoo.import.senders.none": "any sender (the summary names the sender's key)",
        "zoo.import.ok": "Check and continue",
        "zoo.import.no_file": "Choose a pack file first.",
        "zoo.import.needs_passphrase": "This pack is protected with a passphrase: enter it.",
        "zoo.import.not_encrypted": "Only accept these senders applies only to encrypted packs (.lcfpack); this pack "
                                    "is not encrypted (its author signature is shown in the summary).",
        "zoo.import.checking": "Checking {name}...",
        "zoo.keyfile.not_text": "{file} is not a text file with public keys (My public keys... shows what one looks "
                                "like).",
        "zoo.keyfile.no_keys": "The key files contain no usable public key (My public keys... shows what one looks "
                               "like).",
        "zoo.keyfile.choose": "Choose key files",
        "zoo.keys.title": "My public keys",
        "zoo.keys.intro": "These are not secret: give them to people who send you encrypted packs (they choose this "
                          "file as a key file when they export) or who check your signed packs. Your private keys "
                          "stay in the encrypted secrets store and are never shown.",
        "zoo.keys.loading": "Reading your public keys...",
        "zoo.keys.copy": "Copy",
        "zoo.keys.save": "Save...",
        "zoo.keys.copied": "Copied to the clipboard.",
        "zoo.keys.saved": "Saved: {path}",
        "zoo.keys.not_saved": "Not saved: {reason}",
        "zoo.export.title": "Export a pack",
        "zoo.export.what": "What:",
        "zoo.export.kind.target": "A target (hardware module)",
        "zoo.export.kind.recipe": "A finished build (recipe)",
        "zoo.export.kind.knowledge": "My knowledge base",
        "zoo.export.target": "Target:",
        "zoo.export.no_targets": "There is no target folder to export.",
        "zoo.export.build": "Build:",
        "zoo.export.build.choose": "Choose build_info.json...",
        "zoo.export.build.none": "no build chosen",
        "zoo.export.build.title": "Choose the build_info.json of a finished build",
        "zoo.export.knowledge.note": "Your own pages of the knowledge base (not those of installed knowledge packs, "
                                     "not the framework's documents): each keeps its source address and licence, and "
                                     "a page goes in with its full text only where its licence allows it; the pack "
                                     "ships the licence texts and NOTICE they ask for. {path:act_knowledge_sources} "
                                     "shows what each page would carry.",
        "zoo.export.next": "Continue...",
        "zoo.export.choose_target": "Choose a target first.",
        "zoo.export.choose_build": "Choose the build_info.json of a finished build first.",
        "zoo.export.id": "Pack id:",
        "zoo.export.id.hint": "empty: knowledge-<today's date>",
        "zoo.export.encrypt": "Private sharing:",
        "zoo.export.encrypt.none": "No: a plain pack for the community",
        "zoo.export.encrypt.keys": "Encrypt to the keys in key files",
        "zoo.export.encrypt.passphrase": "Encrypt with a passphrase",
        "zoo.export.recipients": "Key files:",
        "zoo.export.recipients.choose": "Choose key files...",
        "zoo.export.recipients.none": "none chosen",
        "zoo.export.passphrase": "Passphrase:",
        "zoo.export.passphrase2": "Repeat:",
        "zoo.export.folder": "Folder:",
        "zoo.export.folder.choose": "Choose folder...",
        "zoo.export.folder.title": "Folder for the pack",
        "zoo.export.no_recipients": "Choose the key files of the people you share the pack with (they get them from "
                                    "My public keys... in their Community Hub).",
        "zoo.export.passphrase_empty": "Enter the passphrase twice.",
        "zoo.export.passphrase_differs": "The two passphrases differ.",
        "zoo.export.creating": "Creating the pack on this computer: scrubbing, signing and, for private sharing, "
                               "encrypting it...",
        "zoo.export.encrypted_no_pr": "This pack is encrypted for private sharing: give the file to the people it is "
                                      "meant for. It is not submitted to the community.",
        "zoo.export.submit.title": "Open pull request",
        "zoo.export.submit.ask": "Submit this pack to the public community repository?",
        "zoo.export.submit.no": "Not submitted; the pack is ready at {path}.",
        "zoo.install.installing": "Installing {kind} {id}...",
        "zoo.install.snapshot": "The knowledge base was saved first as the snapshot {name} "
                                "({path:act_knowledge_snapshots} lists it).",
        "zoo.install.waiting": "Not imported into the knowledge base yet: {why}",
        "zoo.sort": "Sort loose knowledge...",
        "zoo.sort.tip": "Knowledge always belongs to a topic or a target: a loose knowledge file is never imported. "
                        "Sort it into a topic or target folder; it is checked like a knowledge pack first.",
        "zoo.sort.title": "Sort loose knowledge",
        "zoo.sort.intro": "These files lie loose in the knowledge folder and are not in the knowledge base. Choose one "
                          "and the topic or target it belongs to: it is checked like a knowledge pack (quarantine, its "
                          "pages, the licence files), you see the summary, and after your Yes it is moved into that "
                          "folder and imported (a snapshot of the knowledge base first).",
        "zoo.sort.none": "No loose knowledge file: every knowledge file belongs to a topic or a target.",
        "zoo.sort.check": "Check and sort...",
        "zoo.sort.choose_file": "Choose a file first.",
        "zoo.sort.checking": "Checking {name}...",
        "zoo.sort.done": "Sorted {name} into {folder}.",
        "knowledge.scope": "Belongs to:",
        "knowledge.scope.topic": "A topic",
        "knowledge.scope.target": "A target",
        "knowledge.scope.name": "Topic or target:",
        "knowledge.scope.topic.hint": "e.g. llama.cpp (a new name makes a new topic folder)",
        "knowledge.scope.missing": "Knowledge always belongs to a topic or a target: choose a target or name a topic.",
        "sources.col.topic": "Topic or target",
        "sources.topic": "Topic or target:",
        "sources.topic.hint": "all",
        "sources.topic.topic": "topic {name}",
        "sources.topic.target": "target {name}",
        "storage.reason.server": "The knowledge base uses the Qdrant server (setting qdrant_url), so the local storage "
                                 "is not converted while it is set.",
    },
    "de": {
        "storage.used.edge": "Die Wissensdatenbank liegt in Qdrant Edge in {folder}.",
        "storage.used.old": "Die Wissensdatenbank nutzt den Speicher früherer Versionen (den lokalen Modus von "
                            "qdrant-client) in {folder}.",
        "storage.used.server": "Die Wissensdatenbank nutzt einen Qdrant-Server (Einstellung qdrant_url); der lokale "
                               "Speicher wird nicht genutzt.",
        "storage.used.none": "Die Wissensdatenbank kann nicht geöffnet werden: {reason}",
        "storage.reason.engine_missing": "Qdrant Edge ist auf diesem Computer nicht verfügbar (das Paket "
                                         "qdrant-edge-py hat keinen Build für ihn, zum Beispiel Windows auf ARM), "
                                         "daher bleibt die Wissensdatenbank im älteren lokalen Speicher; es kann "
                                         "nichts umgewandelt werden.",
        "storage.reason.not_converted": "Sie wurde noch nicht in Qdrant Edge umgewandelt; das geschieht, wenn die "
                                        "Wissensdatenbank das nächste Mal geöffnet wird.",
        "storage.reason.edge_unavailable": "sie liegt in Qdrant Edge, aber Qdrant Edge kann auf diesem Computer nicht "
                                           "geladen werden (das Paket qdrant-edge-py fehlt oder ist beschädigt). Es "
                                           "wurde nichts geändert, und der Speicher früherer Versionen springt nicht "
                                           "ein: ihm fehlt, was seit der Umwandlung dazukam. Installiere die "
                                           "Abhängigkeiten des Frameworks erneut (der Installer, oder pip install "
                                           "--require-hashes --no-deps -r requirements.txt).",
        "storage.convert.offer": "[Jetzt umwandeln] wandelt sie jetzt um: Texte und Vektoren werden kopiert und "
                                 "geprüft, nichts wird neu eingebettet, und der alte Ordner bleibt.",
        "storage.converted": "Sie wurde am {when} aus dem Speicher früherer Versionen umgewandelt ({points} Auszüge; "
                             "Anzahl und Stichproben-Suchen wurden geprüft).",
        "storage.old.folder.kept": "Der alte Ordner {folder} ({size}) bleibt, bis du ihn löschst.",
        "storage.old.folder": "Alter Speicherordner: {folder} ({size}).",
        "storage.convert.now": "Jetzt umwandeln",
        "snapshots.next.no_room": "Der nächste Snapshot (etwa {size}) passt nicht: Deep Ingest, das Wiederherstellen "
                                  "eines Snapshots und der Import eines Wissenspakets werden abgelehnt, bis Platz "
                                  "ist.",
        "snapshots.next.tight": "Der nächste Snapshot (etwa {size}) passt noch, lässt aber wenig freien Platz.",
        "snapshots.next.step": "Schaffe zuerst Platz: lösche oder exportiere die Wissens-Snapshots, die du nicht mehr "
                               "brauchst ({path:act_knowledge_snapshots}), oder schaffe auf andere Weise Platz auf "
                               "dieser Festplatte. Es wird nichts von selbst gelöscht.",
        "storage.old.failed": "Die Wissensdatenbank nutzt noch den Speicher früherer Versionen: die Umwandlung in "
                              "Qdrant Edge ist fehlgeschlagen ({reason}). Sie funktioniert weiter. Ein neuer Versuch "
                              "läuft {retry}.",
        "storage.old.delete": "Alten Speicher löschen...",
        "storage.old.title": "Alter Speicher der Wissensdatenbank",
        "storage.old.none": "Es gibt keinen alten Speicherordner zum Löschen.",
        "storage.old.done.trash": "In den Papierkorb verschoben: der alte Speicher der Wissensdatenbank. Du kannst "
                                  "ihn aus dem Papierkorb wiederherstellen; sein Platz bleibt belegt, bis du den "
                                  "Papierkorb leerst.",
        "storage.old.done.permanent": "Gelöscht: der alte Speicher der Wissensdatenbank.",
        "storage.old.kept": "Behalten: der alte Speicher der Wissensdatenbank ({reason}).",
        "storage.retry.start": "beim nächsten Start, oder jetzt mit [Umwandlung erneut versuchen]",
        "storage.retry.person": "nur, wenn du es willst, weil er genauso scheitern würde: hier mit [Umwandlung erneut "
                                "versuchen]",
        "storage.convert": "Umwandlung erneut versuchen",
        "storage.converting": "Die Wissensdatenbank wird in Qdrant Edge umgewandelt: Texte und Vektoren werden "
                              "kopiert und geprüft. Bei einer großen Wissensdatenbank dauert das Minuten. Der alte "
                              "Speicher bleibt, wie er ist.",
        "storage.convert.done": "Die Wissensdatenbank wurde in Qdrant Edge umgewandelt ({points} Auszüge; Anzahl und "
                                "Stichproben-Suchen wurden geprüft). Der alte Ordner bleibt, bis du ihn löschst.",
        "storage.convert.failed": "Die Wissensdatenbank wurde nicht umgewandelt. Sie funktioniert weiter im Speicher "
                                  "früherer Versionen. Grund: {reason}",
        "storage.convert.off": "Die Wissensdatenbank ist aus. Schalte sie zuerst unter {path:act_ditto_settings} ein.",
        # Tools > Knowledge Sources...
        "knowledge.menu.sources": "Wissensquellen...",
        "sources.title": "Wissensquellen",
        "sources.intro": "Jede Quelle der Wissensdatenbank mit der Lizenz und dem Urheberrechtsvermerk, die sie "
                         "zeigt, und was ein Wissenspaket von ihr mitnehmen würde: ihren vollen Text (ihre Lizenz "
                         "erlaubt die Weitergabe), nur ihre Adresse und kurze Fakten, oder nichts (eine Seite eines "
                         "installierten Pakets, die eigenen Dokumente des Frameworks), und warum. Hier wird nichts "
                         "geändert.",
        "sources.kind": "Art:",
        "sources.filter.any": "Jede Art",
        "sources.filter.web": "Webseiten",
        "sources.filter.pack": "Seiten aus Wissenspaketen",
        "sources.filter.docs": "Dokumente des Frameworks",
        "sources.filter.other": "Sonstige",
        "sources.refresh": "Aktualisieren",
        "sources.col.source": "Quelle",
        "sources.col.kind": "Art",
        "sources.col.removed": "Ausgelassener versteckter Text",
        "sources.col.licence": "Lizenz",
        "sources.col.copyright": "Urheberrechtsvermerk",
        "sources.col.carried": "Im Paket",
        "sources.col.why": "Warum",
        "sources.loading": "Die Wissensdatenbank wird gelesen...",
        "sources.off": "Die Wissensdatenbank ist aus. Schalte sie zuerst unter {path:act_ditto_settings} ein.",
        "sources.unreadable": "Die Wissensdatenbank kann nicht gelesen werden: {reason}",
        "sources.carried.full_text": "voller Text",
        "sources.carried.link_and_facts": "Link und kurze Fakten",
        "sources.carried.not_exported": "nicht exportiert",
        # Was vor dem Speichern aus Text von außen ausgelassen wurde (die Wörter von knowledge_sources.REMOVED)
        "sources.removed.none": "nichts",
        "sources.removed.template": "template",
        "sources.removed.noscript": "noscript",
        "sources.removed.hidden_attribute": "Attribut hidden",
        "sources.removed.aria_hidden": "aria-hidden",
        "sources.removed.display_none": "display:none",
        "sources.removed.visibility_hidden": "visibility:hidden",
        "sources.removed.font_size_zero": "Schriftgröße 0",
        "sources.removed.opacity_zero": "Deckkraft 0",
        "sources.removed.off_screen": "außerhalb des Bildschirms",
        "sources.removed.clipped": "weggeschnitten",
        "sources.removed.same_colour": "Text in seiner Hintergrundfarbe",
        "sources.removed.invisible_characters": "unsichtbare Zeichen",
        "sources.removed.html_comments": "HTML-Kommentare",
        "sources.empty": "Die Wissensdatenbank enthält keine Quelle.",
        "sources.empty.kind": "Die Wissensdatenbank enthält keine Quelle der Art {kind}.",
        "sources.totals": "{n} Quelle(n): ein Wissenspaket würde {full} mit ihrem vollen Text und {facts} nur als ihre "
                          "Adresse und kurze Fakten mitnehmen.",
        # Tools > Community Hub: die neuen Schaltflächen
        "zoo.details": "Details...",
        "zoo.details.tip": "Lizenz, Vertrauen, installierter Ordner, Download-Adresse, Größe und sha256 des gewählten "
                           "Pakets, und die recipe.yml eines installierten Rezepts",
        "zoo.verify_installed": "Installierte Pakete prüfen",
        "zoo.verify_installed.tip": "Prüft jedes installierte Paket: zurückgezogen, seit der Installation verändert, "
                                    "Lizenzdateien, die nicht zu seinen Seiten passen",
        "zoo.verify_file": "Paketdatei prüfen...",
        "zoo.verify_file.tip": "Prüft eine Paketdatei, bevor du sie installierst oder weitergibst: Format, Prüfsummen, "
                               "Validatoren und die Datenschutz-Bereinigung. Es wird nichts installiert.",
        "zoo.export": "Exportieren...",
        "zoo.refresh": "Aktualisieren",
        "zoo.export.tip": "Ein Target, einen fertigen Build (Rezept) oder deine Wissensdatenbank als bereinigtes, mit "
                          "deinem Schlüssel signiertes Paket exportieren, offen für die Community oder verschlüsselt "
                          "zum privaten Teilen",
        "zoo.keys": "Meine öffentlichen Schlüssel...",
        "zoo.keys.tip": "Deine öffentlichen Schlüssel, für Leute, die dir verschlüsselte Pakete schicken oder deine "
                        "signierten Pakete prüfen",
        "zoo.choose_first": "Wähle zuerst ein Paket in der Liste.",
        "zoo.uninstall": "Deinstallieren...",
        "zoo.uninstall.tip": "Deinstalliert das gewählte installierte Paket: zuerst die genaue Liste (sein Ordner, "
                             "seine Import-Notizen, die Auszüge, die nur es hält), in den Papierkorb oder endgültig; "
                             "Auszüge, die ein anderes Paket noch hält, bleiben",
        "zoo.uninstall.title": "{kind}-Paket {id} deinstallieren",
        "zoo.uninstall.not_installed": "{id} ist nicht installiert; es gibt nichts zu deinstallieren.",
        "zoo.details.loading": "Die Details von {kind} {id} werden gelesen...",
        "zoo.details.title": "Paket-Details",
        "zoo.details.none": "{id} ist weder im zwischengespeicherten Index noch installiert; Refresh lädt den Index "
                            "neu.",
        "zoo.field.pack": "Paket",
        "zoo.field.licence": "Lizenz",
        "zoo.field.trust": "Vertrauen",
        "zoo.field.installed": "Installiert",
        "zoo.field.folder": "Installierter Ordner",
        "zoo.field.download": "Download-Adresse",
        "zoo.field.size": "Größe",
        "zoo.field.sha256": "sha256",
        "zoo.field.description": "Beschreibung",
        "zoo.field.recipe": "recipe.yml",
        "zoo.value.pack": "{kind} {id} {version} von {author}",
        "zoo.value.unknown_licence": "Lizenz unbekannt",
        "zoo.value.not_installed": "nein",
        "zoo.value.revoked": "{trust} - zurückgezogen: {reason}",
        "zoo.value.bytes": "{size} Bytes",
        "zoo.verify.checking": "Die installierten Pakete werden geprüft...",
        "zoo.verify.title": "Installierte Community-Pakete",
        "zoo.verify.col.kind": "Art",
        "zoo.verify.col.id": "ID",
        "zoo.verify.col.version": "Version",
        "zoo.verify.col.folder": "Ordner",
        "zoo.verify.col.result": "Ergebnis",
        "zoo.verify.ok": "OK ({trust})",
        "zoo.verify.ok_plain": "OK",
        "zoo.verify.summary": "{n} installierte(s) Paket(e) geprüft: {bad} mit Problemen. Es wurde nichts geändert.",
        "zoo.verify.none": "Keine Community-Pakete installiert.",
        "zoo.verify_file.choose": "Paketdatei prüfen",
        "zoo.verify_file.checking": "{name} wird geprüft...",
        "zoo.verify_file.ok": "{kind} {id} {version}: annehmbar (Format, Prüfsummen, Validatoren und "
                              "Datenschutz-Bereinigung). Es wurde nichts installiert.",
        "zoo.verify_file.bad": "{kind} {id} {version} würde abgelehnt:\n{problems}\nEs wurde nichts installiert.",
        "zoo.import.title": "Paketdatei importieren",
        "zoo.import.intro": "Eine Paketdatei, die dir jemand gegeben hat, oder ein verschlüsseltes Paket (.lcfpack), "
                            "das an deinen Schlüssel geschickt oder mit einer Passphrase geschützt wurde. Seine "
                            "Zusammenfassung wird gezeigt, bevor etwas installiert wird, und du entscheidest („Nein“ "
                            "ist die Vorgabe).",
        "zoo.import.file": "Paketdatei:",
        "zoo.import.browse": "Datei wählen...",
        "zoo.import.passphrase": "Passphrase:",
        "zoo.import.passphrase.hint": "nur für ein mit Passphrase geschütztes Paket; sie wird nie gespeichert oder "
                                      "angezeigt",
        "zoo.import.senders": "Nur diese Absender annehmen:",
        "zoo.import.senders.choose": "Schlüsseldateien wählen...",
        "zoo.import.senders.clear": "Leeren",
        "zoo.import.senders.none": "jeder Absender (die Zusammenfassung nennt den Schlüssel des Absenders)",
        "zoo.import.ok": "Prüfen und weiter",
        "zoo.import.no_file": "Wähle zuerst eine Paketdatei.",
        "zoo.import.needs_passphrase": "Dieses Paket ist mit einer Passphrase geschützt: gib sie ein.",
        "zoo.import.not_encrypted": "„Nur diese Absender annehmen“ gilt nur für verschlüsselte Pakete (.lcfpack); "
                                    "dieses Paket ist nicht verschlüsselt (seine Autoren-Signatur steht in der "
                                    "Zusammenfassung).",
        "zoo.import.checking": "{name} wird geprüft...",
        "zoo.keyfile.not_text": "{file} ist keine Textdatei mit öffentlichen Schlüsseln (Meine öffentlichen "
                                "Schlüssel... zeigt, wie eine aussieht).",
        "zoo.keyfile.no_keys": "Die Schlüsseldateien enthalten keinen brauchbaren öffentlichen Schlüssel (Meine "
                               "öffentlichen Schlüssel... zeigt, wie einer aussieht).",
        "zoo.keyfile.choose": "Schlüsseldateien wählen",
        "zoo.keys.title": "Meine öffentlichen Schlüssel",
        "zoo.keys.intro": "Diese sind nicht geheim: gib sie Leuten, die dir verschlüsselte Pakete schicken (sie wählen "
                          "diese Datei beim Export als Schlüsseldatei) oder deine signierten Pakete prüfen. Deine "
                          "privaten Schlüssel bleiben im verschlüsselten Geheimnisspeicher und werden nie angezeigt.",
        "zoo.keys.loading": "Deine öffentlichen Schlüssel werden gelesen...",
        "zoo.keys.copy": "Kopieren",
        "zoo.keys.save": "Speichern...",
        "zoo.keys.copied": "In die Zwischenablage kopiert.",
        "zoo.keys.saved": "Gespeichert: {path}",
        "zoo.keys.not_saved": "Nicht gespeichert: {reason}",
        "zoo.export.title": "Ein Paket exportieren",
        "zoo.export.what": "Was:",
        "zoo.export.kind.target": "Ein Target (Hardware-Modul)",
        "zoo.export.kind.recipe": "Ein fertiger Build (Rezept)",
        "zoo.export.kind.knowledge": "Meine Wissensdatenbank",
        "zoo.export.target": "Target:",
        "zoo.export.no_targets": "Es gibt keinen Target-Ordner zum Exportieren.",
        "zoo.export.build": "Build:",
        "zoo.export.build.choose": "build_info.json wählen...",
        "zoo.export.build.none": "kein Build gewählt",
        "zoo.export.build.title": "Die build_info.json eines fertigen Builds wählen",
        "zoo.export.knowledge.note": "Deine eigenen Seiten der Wissensdatenbank (nicht die installierter "
                                     "Wissenspakete, nicht die Dokumente des Frameworks): jede behält ihre "
                                     "Quelladresse und Lizenz, und eine Seite kommt nur dort mit vollem Text hinein, "
                                     "wo ihre Lizenz es erlaubt; das Paket liefert die Lizenztexte und die NOTICE "
                                     "mit, die sie verlangen. {path:act_knowledge_sources} zeigt, was jede Seite "
                                     "mitnehmen würde.",
        "zoo.export.next": "Weiter...",
        "zoo.export.choose_target": "Wähle zuerst ein Target.",
        "zoo.export.choose_build": "Wähle zuerst die build_info.json eines fertigen Builds.",
        "zoo.export.id": "Paket-ID:",
        "zoo.export.id.hint": "leer: knowledge-<heutiges Datum>",
        "zoo.export.encrypt": "Privat teilen:",
        "zoo.export.encrypt.none": "Nein: ein offenes Paket für die Community",
        "zoo.export.encrypt.keys": "An die Schlüssel in Schlüsseldateien verschlüsseln",
        "zoo.export.encrypt.passphrase": "Mit einer Passphrase verschlüsseln",
        "zoo.export.recipients": "Schlüsseldateien:",
        "zoo.export.recipients.choose": "Schlüsseldateien wählen...",
        "zoo.export.recipients.none": "keine gewählt",
        "zoo.export.passphrase": "Passphrase:",
        "zoo.export.passphrase2": "Wiederholen:",
        "zoo.export.folder": "Ordner:",
        "zoo.export.folder.choose": "Ordner wählen...",
        "zoo.export.folder.title": "Ordner für das Paket",
        "zoo.export.no_recipients": "Wähle die Schlüsseldateien der Leute, mit denen du das Paket teilst (sie "
                                    "bekommen sie über Meine öffentlichen Schlüssel... in ihrem Community Hub).",
        "zoo.export.passphrase_empty": "Gib die Passphrase zweimal ein.",
        "zoo.export.passphrase_differs": "Die beiden Passphrasen sind verschieden.",
        "zoo.export.creating": "Das Paket wird auf diesem Computer erstellt: bereinigt, signiert und, zum privaten "
                               "Teilen, verschlüsselt...",
        "zoo.export.encrypted_no_pr": "Dieses Paket ist zum privaten Teilen verschlüsselt: gib die Datei den Leuten, "
                                      "für die sie bestimmt ist. Es wird nicht bei der Community eingereicht.",
        "zoo.export.submit.title": "Pull Request öffnen",
        "zoo.export.submit.ask": "Dieses Paket beim öffentlichen Community-Repository einreichen?",
        "zoo.export.submit.no": "Nicht eingereicht; das Paket liegt bereit unter {path}.",
        "zoo.install.installing": "{kind} {id} wird installiert...",
        "zoo.install.snapshot": "Die Wissensdatenbank wurde zuerst als Snapshot {name} gesichert "
                                "({path:act_knowledge_snapshots} listet ihn).",
        "zoo.install.waiting": "Noch nicht in die Wissensdatenbank importiert: {why}",
        "zoo.sort": "Lose Wissensdateien einsortieren...",
        "zoo.sort.tip": "Wissen gehört immer zu einem Thema oder einem Target: eine lose Wissensdatei wird nie "
                        "importiert. Sortiere sie in einen Themen- oder Target-Ordner; vorher wird sie wie ein "
                        "Wissenspaket geprüft.",
        "zoo.sort.title": "Lose Wissensdateien einsortieren",
        "zoo.sort.intro": "Diese Dateien liegen lose im Wissensordner und sind nicht in der Wissensdatenbank. Wähle "
                          "eine und das Thema oder Target, zu dem sie gehört: sie wird wie ein Wissenspaket geprüft "
                          "(Quarantäne, ihre Seiten, die Lizenzdateien), du siehst die Zusammenfassung, und nach "
                          "deinem Ja wird sie in diesen Ordner verschoben und importiert (vorher ein Snapshot der "
                          "Wissensdatenbank).",
        "zoo.sort.none": "Keine lose Wissensdatei: jede Wissensdatei gehört zu einem Thema oder einem Target.",
        "zoo.sort.check": "Prüfen und einsortieren...",
        "zoo.sort.choose_file": "Wähle zuerst eine Datei.",
        "zoo.sort.checking": "{name} wird geprüft...",
        "zoo.sort.done": "{name} wurde in {folder} einsortiert.",
        "knowledge.scope": "Gehört zu:",
        "knowledge.scope.topic": "Einem Thema",
        "knowledge.scope.target": "Einem Target",
        "knowledge.scope.name": "Thema oder Target:",
        "knowledge.scope.topic.hint": "z. B. llama.cpp (ein neuer Name legt einen neuen Themenordner an)",
        "knowledge.scope.missing": "Wissen gehört immer zu einem Thema oder einem Target: wähle ein Target oder "
                                   "nenne ein Thema.",
        "sources.col.topic": "Thema oder Target",
        "sources.topic": "Thema oder Target:",
        "sources.topic.hint": "alle",
        "sources.topic.topic": "Thema {name}",
        "sources.topic.target": "Target {name}",
        "storage.reason.server": "Die Wissensdatenbank nutzt den Qdrant-Server (Einstellung qdrant_url), daher wird "
                                 "der lokale Speicher nicht umgewandelt, solange sie gesetzt ist.",
    },
}
