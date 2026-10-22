#!/usr/bin/env python3
"""
Texts of the GUI tool (the AI's hands in the window: Ditto's and AI apps'), the build progress panel and the board
recommendation, English and German. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists
already is refused there.

Every key has the same {placeholders} in both languages (tests/test_gui_tools.py). The field labels are also what
an AI reads (in English) through the GUI tool's read_form. This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # forms and their fields
        "gui.form.builder": "Build form",
        "gui.form.module_wizard": "Target module wizard",
        "gui.form.ai_settings": "AI settings",
        "gui.field.model": "Model",
        "gui.field.target": "Target",
        "gui.field.format": "Format",
        "gui.field.board": "Board",
        "gui.field.quantization": "Quantization",
        "gui.field.optimization": "Optimization",
        "gui.field.use_gpu": "Use the GPU for the build",
        "gui.field.imatrix": "Importance matrix",
        "gui.field.dataset": "Calibration dataset",
        "gui.field.wizard.name": "Module name",
        "gui.field.wizard.arch": "Architecture",
        "gui.field.wizard.sdk": "SDK",
        "gui.field.wizard.base_image": "Base image",
        "gui.field.wizard.packages": "Packages",
        "gui.field.wizard.cpu_flags": "CPU flags",
        "gui.field.wizard.cmake_flags": "CMake flags",
        "gui.field.wizard.quantization_logic": "Quantization logic (convert stage)",
        "gui.field.wizard.setup_commands": "Build-time setup",
        "gui.field.wizard.attach_knowledge": "Attach the crawled knowledge",
        "gui.field.wizard.export": "Export for the community",
        "gui.field.ai.provider": "AI provider",
        "gui.field.ai.model": "Model",
        "gui.field.ai.api_key": "API key",
        "gui.field.ai.workspace": "Workspace ID",
        "gui.field.ai.base_url": "Base URL",
        "gui.field.ai.effort": "Effort",
        "gui.field.ai.privacy": "Privacy (cloud)",
        "gui.field.ai.fallback": "Fallback chain",
        "gui.field.ai.enable_rag": "Local knowledge base",
        "gui.field.ai.offline": "Offline mode",
        "gui.ai.where_data": "It decides where your data goes, so only the person changes it.",
        "gui.ai.privacy": "A lower level sends more personal data to a cloud provider, so only the person changes it.",

        # an AI's mark on a field and the banner with the undo
        "gui.field.tip": "Filled by Ditto. Change it to keep your own value, or undo Ditto's changes.",
        "gui.field.tip_app": "Filled by {who}. Change it to keep your own value, or undo the AI changes.",
        "gui.banner.filled": "Ditto filled: {fields}. Please check them. Change a field to keep your own value. "
                             "Nothing was started or saved.",
        "gui.banner.filled_by": "Filled by {who}: {fields}.",
        "gui.banner.check": "Please check them. Change a field to keep your own value. Nothing was started or saved.",
        "gui.banner.undo": "Undo Ditto's changes",
        "gui.banner.undo_ai": "Undo the AI changes",
        "gui.banner.undone": "Ditto's changes are undone. Fields you changed keep your value.",
        "gui.banner.undone_ai": "The AI changes are undone. Fields you changed keep your value.",
        # who filled a field (an AI app's own name is its claim: shown in quotes, never trusted)
        "gui.filler.ditto": "Ditto",
        "gui.filler.app": "the AI app \"{app}\" (its own name, not checked)",
        "gui.filler.token": "the AI app with the MCP token \"{token}\"",
        "gui.filler.token_app": "the AI app with the MCP token \"{token}\" (it calls itself \"{app}\")",
        "gui.filler.unnamed": "an AI app that gave no name",

        # what show_window opens
        "gui.window.builder": "Builder tab",
        "gui.window.deployment": "Deployment tab",
        "gui.window.chat": "Chat panel",
        "gui.window.module_wizard": "Target module wizard",
        "gui.window.ai_settings": "AI settings",
        "gui.window.hf_browser": "Hugging Face model browser",
        "gui.window.community_hub": "Community Hub",
        "gui.window.knowledge_snapshots": "Knowledge snapshots",

        # build progress and the live log
        "gui.progress.idle": "No build is running.",
        "gui.progress.bar": "Build progress",
        "gui.progress.started": "Build {request} started. Waiting for the first step.",
        "gui.progress.stage": "Step: {stage} ({percent}%)",
        "gui.progress.ended": "The build ended: {status}. Last step: {stage} ({percent}%).",
        "gui.progress.status.completed": "completed",
        "gui.progress.status.error": "failed",
        "gui.progress.status.cancelled": "cancelled",
        "gui.progress.unknown": "The status of build {request} cannot be read any more. The log below shows its "
                                "last lines.",
        "gui.progress.resources": "Build container: CPU {cpu} % · memory {used} of {limit}",
        "gui.progress.resources.near": "Build container: CPU {cpu} % · memory {used} of {limit}, nearly full",
        "gui.log.title": "Build log (live)",
        "gui.log.search": "Search the build log",
        "gui.log.level": "Show the lines of this level",
        "gui.log.level.all": "All",
        "gui.log.level.info": "Info",
        "gui.log.level.warning": "Warnings",
        "gui.log.level.error": "Errors",
        "gui.log.count": "{shown} of {total} lines",
        "gui.log.dropped": "({n} older lines were removed from this view.)",
        "gui.log.skipped": "({n} earlier lines are not shown here. The build's full log keeps them.)",

        # the board recommendation from the probe
        "gui.board.source.probe": "from the probe's SoC",
        "gui.board.source.list": "from the probe's board name in the list of known boards",
        "gui.board.source.none": "no source",
        "gui.board.recommended": "Recommended board: {board} ({source}, sure). It is only a recommendation. Change it "
                                 "if it is wrong.",
        "gui.board.kept": "The probe recommends {board} ({source}, sure). Your choice {chosen} stays.",
        "gui.board.use": "Use {board}",
        "gui.board.not_offered": "The probe points to {board} ({source}), but this format does not offer it.",
        "gui.board.unsure": "It is not sure which board this is: {why}. Board name in the probe: {name}. Candidates: "
                            "{candidates}. Please pick your board.",
        "gui.board.why.partial": "the board name only partly matches a known board",
        "gui.board.why.several": "the board name fits more than one board",
        "gui.board.why.no_soc": "the probe names no SoC this target knows",
        "gui.board.pick": "It is {board}",
        "gui.board.picked": "Board set to {board} (your choice).",
        "gui.board.online": "Look up the board name online...",
        "gui.board.online.title": "Look up the board online",
        "gui.board.online.question": "This opens your web browser with a search for the board name '{name}'. The "
                                     "search site gets this name. Continue?",
        "gui.board.ask_ditto": "Ask Ditto",
        "gui.board.ask_text": "The probe of my board names the board '{name}' (SoC: {soc}). Which board of the target "
                              "{target} is it: {candidates}? Please tell me why. I pick the board myself.",
        "gui.target.none": "No target fits this probe ({facts}). Create one with the wizard, or ask Ditto.",
        "gui.target.wizard": "Create a target with the wizard",
        "gui.target.ask_ditto": "Ask Ditto",
        "gui.target.ask_text": "No target fits my board ({facts}). Please help me create a target module. You can "
                               "steer the target module wizard. I click Finish myself.",
        "gui.target.kept": "The probe points to the target {target}. Your choice {chosen} stays.",
        "gui.target.use": "Use {target}",
        "gui.ditto.kept": "Ditto suggests {value} for {field}. Your choice stays.",
        "gui.ditto.not_filled": "Ditto suggests {value} for {field}. It was not filled: {why}.",
        "gui.board.confirm.title": "Which board?",
        "gui.board.confirm": "The probe could not tell your board for sure (candidates: {candidates}). The form says "
                             "{board}. Build for {board}?",
        "gui.board.confirm.no": "The build was not started. Pick your board, then start the build.",

        # documentation links for Deep Ingest
        "gui.wizard.ingest_title": "Add documentation",
        "gui.wizard.ingest_offer": "Ditto lacks information about this hardware. Open Deep Ingest with {n} "
                                   "documentation links the framework knows for it? You check the links and start "
                                   "the crawl.",
        "gui.wizard.ingest_offer_none": "Ditto lacks information about this hardware. Open Deep Ingest to add its "
                                        "documentation? You enter the links and start the crawl.",
        "gui.ingest.suggested": "{n} links from the framework's source list for this hardware are filled in. Check "
                                "them and remove any you do not want. Nothing is crawled until you press OK.",
    },
    "de": {
        "gui.form.builder": "Build-Formular",
        "gui.form.module_wizard": "Assistent für Target-Module",
        "gui.form.ai_settings": "KI-Einstellungen",
        "gui.field.model": "Modell",
        "gui.field.target": "Target",
        "gui.field.format": "Format",
        "gui.field.board": "Board",
        "gui.field.quantization": "Quantisierung",
        "gui.field.optimization": "Optimierung",
        "gui.field.use_gpu": "GPU für den Build nutzen",
        "gui.field.imatrix": "Wichtigkeitsmatrix (IMatrix)",
        "gui.field.dataset": "Kalibrierungsdatensatz",
        "gui.field.wizard.name": "Modulname",
        "gui.field.wizard.arch": "Architektur",
        "gui.field.wizard.sdk": "SDK",
        "gui.field.wizard.base_image": "Basis-Image",
        "gui.field.wizard.packages": "Pakete",
        "gui.field.wizard.cpu_flags": "CPU-Flags",
        "gui.field.wizard.cmake_flags": "CMake-Flags",
        "gui.field.wizard.quantization_logic": "Quantisierungslogik (Stufe convert)",
        "gui.field.wizard.setup_commands": "Einrichtung beim Build",
        "gui.field.wizard.attach_knowledge": "Gesammeltes Wissen anhängen",
        "gui.field.wizard.export": "Für die Community exportieren",
        "gui.field.ai.provider": "KI-Anbieter",
        "gui.field.ai.model": "Modell",
        "gui.field.ai.api_key": "API-Schlüssel",
        "gui.field.ai.workspace": "Workspace-ID",
        "gui.field.ai.base_url": "Basis-URL",
        "gui.field.ai.effort": "Aufwand",
        "gui.field.ai.privacy": "Datenschutz (Cloud)",
        "gui.field.ai.fallback": "Ersatzkette",
        "gui.field.ai.enable_rag": "Lokale Wissensbasis",
        "gui.field.ai.offline": "Offline-Modus",
        "gui.ai.where_data": "Es entscheidet, wohin deine Daten gehen. Deshalb ändert es nur die Person.",
        "gui.ai.privacy": "Eine niedrigere Stufe schickt mehr persönliche Daten an einen Cloud-Anbieter. Deshalb ändert "
                          "sie nur die Person.",

        "gui.field.tip": "Von Ditto ausgefüllt. Ändere es, um deinen eigenen Wert zu behalten, oder mache Dittos "
                         "Änderungen rückgängig.",
        "gui.banner.filled": "Ditto hat ausgefüllt: {fields}. Bitte prüfe sie. Ändere ein Feld, um deinen eigenen Wert "
                             "zu behalten. Nichts wurde gestartet oder gespeichert.",
        "gui.banner.undo": "Dittos Änderungen rückgängig machen",
        "gui.banner.undone": "Dittos Änderungen sind rückgängig gemacht. Felder, die du geändert hast, behalten deinen "
                             "Wert.",
        "gui.field.tip_app": "Ausgefüllt von {who}. Ändere es, um deinen eigenen Wert zu behalten, oder mache die "
                             "KI-Änderungen rückgängig.",
        "gui.banner.filled_by": "Ausgefüllt von {who}: {fields}.",
        "gui.banner.check": "Bitte prüfe sie. Ändere ein Feld, um deinen eigenen Wert zu behalten. Nichts wurde "
                            "gestartet oder gespeichert.",
        "gui.banner.undo_ai": "KI-Änderungen rückgängig machen",
        "gui.banner.undone_ai": "Die KI-Änderungen sind rückgängig gemacht. Felder, die du geändert hast, behalten "
                                "deinen Wert.",
        "gui.filler.ditto": "Ditto",
        "gui.filler.app": "der KI-App \u201e{app}\u201c (ihr eigener Name, nicht geprüft)",
        "gui.filler.token": "der KI-App mit dem MCP-Token \u201e{token}\u201c",
        "gui.filler.token_app": "der KI-App mit dem MCP-Token \u201e{token}\u201c (sie nennt sich \u201e{app}\u201c)",
        "gui.filler.unnamed": "einer KI-App, die keinen Namen genannt hat",

        "gui.window.builder": "Reiter Builder",
        "gui.window.deployment": "Reiter Deployment",
        "gui.window.chat": "Chat-Bereich",
        "gui.window.module_wizard": "Assistent für Target-Module",
        "gui.window.ai_settings": "KI-Einstellungen",
        "gui.window.hf_browser": "Hugging-Face-Modellbrowser",
        "gui.window.community_hub": "Community Hub",
        "gui.window.knowledge_snapshots": "Wissens-Snapshots",

        "gui.progress.idle": "Es läuft kein Build.",
        "gui.progress.bar": "Build-Fortschritt",
        "gui.progress.started": "Build {request} gestartet. Warte auf den ersten Schritt.",
        "gui.progress.stage": "Schritt: {stage} ({percent} %)",
        "gui.progress.ended": "Der Build ist beendet: {status}. Letzter Schritt: {stage} ({percent} %).",
        "gui.progress.status.completed": "abgeschlossen",
        "gui.progress.status.error": "fehlgeschlagen",
        "gui.progress.status.cancelled": "abgebrochen",
        "gui.progress.unknown": "Der Status von Build {request} lässt sich nicht mehr lesen. Das Protokoll darunter "
                                "zeigt seine letzten Zeilen.",
        "gui.progress.resources": "Build-Container: CPU {cpu} % · Arbeitsspeicher {used} von {limit}",
        "gui.progress.resources.near": "Build-Container: CPU {cpu} % · Arbeitsspeicher {used} von {limit}, fast voll",
        "gui.log.title": "Build-Protokoll (live)",
        "gui.log.search": "Im Build-Protokoll suchen",
        "gui.log.level": "Die Zeilen dieser Stufe zeigen",
        "gui.log.level.all": "Alle",
        "gui.log.level.info": "Info",
        "gui.log.level.warning": "Warnungen",
        "gui.log.level.error": "Fehler",
        "gui.log.count": "{shown} von {total} Zeilen",
        "gui.log.dropped": "({n} ältere Zeilen wurden aus dieser Ansicht entfernt.)",
        "gui.log.skipped": "({n} frühere Zeilen werden hier nicht gezeigt. Das vollständige Protokoll des Builds "
                           "behält sie.)",

        "gui.board.source.probe": "aus dem SoC der Probe",
        "gui.board.source.list": "aus dem Board-Namen der Probe in der Liste bekannter Boards",
        "gui.board.source.none": "keine Quelle",
        "gui.board.recommended": "Empfohlenes Board: {board} ({source}, sicher). Es ist nur eine Empfehlung. Ändere "
                                 "es, wenn es falsch ist.",
        "gui.board.kept": "Die Probe empfiehlt {board} ({source}, sicher). Deine Wahl {chosen} bleibt.",
        "gui.board.use": "{board} verwenden",
        "gui.board.not_offered": "Die Probe zeigt auf {board} ({source}), aber dieses Format bietet es nicht an.",
        "gui.board.unsure": "Es ist nicht sicher, welches Board das ist: {why}. Board-Name in der Probe: {name}. "
                            "Kandidaten: {candidates}. Bitte wähle dein Board.",
        "gui.board.why.partial": "der Board-Name passt nur teilweise zu einem bekannten Board",
        "gui.board.why.several": "der Board-Name passt zu mehr als einem Board",
        "gui.board.why.no_soc": "die Probe nennt keinen SoC, den dieses Target kennt",
        "gui.board.pick": "Es ist {board}",
        "gui.board.picked": "Board auf {board} gesetzt (deine Wahl).",
        "gui.board.online": "Board-Namen online nachschlagen...",
        "gui.board.online.title": "Board online nachschlagen",
        "gui.board.online.question": "Das öffnet deinen Webbrowser mit einer Suche nach dem Board-Namen '{name}'. Die "
                                     "Suchseite erhält diesen Namen. Fortfahren?",
        "gui.board.ask_ditto": "Ditto fragen",
        "gui.board.ask_text": "Die Probe meines Boards nennt das Board '{name}' (SoC: {soc}). Welches Board des "
                              "Targets {target} ist es: {candidates}? Bitte sag mir, warum. Das Board wähle ich "
                              "selbst.",
        "gui.target.none": "Kein Target passt zu dieser Probe ({facts}). Erstelle eines mit dem Assistenten, oder "
                           "frag Ditto.",
        "gui.target.wizard": "Target mit dem Assistenten erstellen",
        "gui.target.ask_ditto": "Ditto fragen",
        "gui.target.ask_text": "Kein Target passt zu meinem Board ({facts}). Bitte hilf mir, ein Target-Modul zu "
                               "erstellen. Du kannst den Assistenten für Target-Module steuern. Auf Finish klicke "
                               "ich selbst.",
        "gui.target.kept": "Die Probe zeigt auf das Target {target}. Deine Wahl {chosen} bleibt.",
        "gui.target.use": "{target} verwenden",
        "gui.ditto.kept": "Ditto schlägt {value} für {field} vor. Deine Wahl bleibt.",
        "gui.ditto.not_filled": "Ditto schlägt {value} für {field} vor. Es wurde nicht eingetragen: {why}.",
        "gui.board.confirm.title": "Welches Board?",
        "gui.board.confirm": "Die Probe konnte dein Board nicht sicher bestimmen (Kandidaten: {candidates}). Im "
                             "Formular steht {board}. Für {board} bauen?",
        "gui.board.confirm.no": "Der Build wurde nicht gestartet. Wähle dein Board und starte dann den Build.",

        "gui.wizard.ingest_title": "Dokumentation hinzufügen",
        "gui.wizard.ingest_offer": "Ditto fehlen Informationen über diese Hardware. Deep Ingest mit {n} "
                                   "Dokumentations-Links öffnen, die das Framework dafür kennt? Du prüfst die Links "
                                   "und startest das Einlesen.",
        "gui.wizard.ingest_offer_none": "Ditto fehlen Informationen über diese Hardware. Deep Ingest öffnen, um ihre "
                                        "Dokumentation hinzuzufügen? Du gibst die Links ein und startest das Einlesen.",
        "gui.ingest.suggested": "{n} Links aus der Quellenliste des Frameworks für diese Hardware sind eingetragen. "
                                "Prüfe sie und entferne die, die du nicht willst. Nichts wird eingelesen, bevor du "
                                "OK drückst.",
    },
}
