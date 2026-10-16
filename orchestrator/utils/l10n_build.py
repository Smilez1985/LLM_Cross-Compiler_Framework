#!/usr/bin/env python3
"""
Texts of the build chain of the GUI parity wave (W1': every CLI action by mouse), English and German: the Build menu
(Targets..., Build History...), File > Settings..., the Builder's Targets... / Check target buttons, its priority
and its note for a target without capabilities, and the module wizard's description and "for this analysis only"
provider/model. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused
there, so these never replace another text.

Every key has the same {placeholders} in both languages (tests/test_gui_build.py). German terms: "Target" stays
"Target" (as in the German guides), "Build-Verlauf" (build history), "Einstellungen" (settings). This module imports
nothing from the framework.
"""

TEXTS = {
    "en": {
        # menus
        "bld.menu": "Build",
        "bld.menu.targets": "Targets...",
        "bld.menu.history": "Build History...",
        "bld.menu.settings": "Settings...",

        # Build > Targets... (llm-cli targets list / targets validate)
        "bld.targets.title": "Targets",
        "bld.targets.intro": "Every hardware target of this installation ({n}). The ID is the value for --target. A "
                             "target without capabilities builds every format, but its boards, formats and "
                             "quantizations are not checked before a build. Targets folder: {path}",
        "bld.targets.empty": "No targets found. Targets folder: {path}",
        "bld.targets.col.id": "ID (--target)",
        "bld.targets.col.name": "Name",
        "bld.targets.col.arch": "Architecture",
        "bld.targets.col.boards": "Boards",
        "bld.targets.col.formats": "Formats",
        "bld.targets.col.support": "Support level",
        "bld.targets.none_declared": "- (no capabilities declared)",
        "bld.targets.invalid": "- (capabilities block invalid: Check target names each error)",
        "bld.targets.check": "Check target",
        "bld.targets.reload": "Reload",
        "bld.targets.pick": "Choose a target in the list first.",
        "bld.targets.unavailable": "The target list is not available: {reason}",

        # the check of one target (targets validate), also after the module wizard and a Community Hub install
        "bld.check.title": "Check target",
        "bld.check.valid": "Target '{id}' is valid ({boards} boards, formats: {formats}).",
        "bld.check.invalid": "Target '{id}': its capabilities block is invalid:",
        "bld.check.error_line": "{field}: {message}",
        "bld.check.no_caps": "Target '{id}' loads but declares no capabilities: boards, formats and quantizations are "
                             "not checked before a build. Add a 'capabilities:' block (see "
                             "targets/Rockchip/target.yml).",
        "bld.check.not_found": "Target '{name}' not found (or its target.yml cannot be read; see the log).",
        "bld.check.unavailable": "The target manager is not available.",
        "bld.check.after_create": "Check of the new target:",
        "bld.check.after_install": "Check of the installed target:",

        # the Builder tab
        "bld.builder.targets": "Targets...",
        "bld.builder.targets.tip": "Every target with its ID, boards, formats and support level (Build > Targets...)",
        "bld.builder.check": "Check target",
        "bld.builder.check.tip": "Checks that the selected target loads and that its capabilities block passes the "
                                 "schema",
        "bld.builder.no_target": "Choose a target first.",
        "bld.builder.no_caps": "This target declares no capabilities: every format is offered, and its boards, "
                               "formats and quantizations are not checked before a build.",
        "bld.builder.invalid_caps": "This target's capabilities block is invalid (Check target names each error): "
                                    "every format is offered, and its boards, formats and quantizations are not "
                                    "checked before a build.",
        "bld.builder.priority": "Priority:",
        "bld.builder.priority.tip": "The build queue orders by it; a failed critical build stops the whole pipeline.",
        "bld.builder.priority.low": "Low",
        "bld.builder.priority.normal": "Normal",
        "bld.builder.priority.high": "High",
        "bld.builder.priority.urgent": "Urgent",
        "bld.builder.priority.critical": "Critical (a failed build stops the pipeline)",

        # Build > Build History... (llm-cli build status [--all] [REQUEST_ID])
        "bld.history.title": "Build History",
        "bld.history.intro": "Every build kept on this computer and every request still waiting, wherever it was "
                             "started (GUI, CLI, MCP server), newest first: {n} entries. Build records: {path}",
        "bld.history.empty": "No builds found in {path}.",
        "bld.history.col.request": "Request",
        "bld.history.col.build": "Build",
        "bld.history.col.status": "Status",
        "bld.history.col.progress": "Progress",
        "bld.history.col.target": "Target",
        "bld.history.col.format": "Format",
        "bld.history.col.quantization": "Quantization",
        "bld.history.col.model": "Model",
        "bld.history.col.started": "Started",
        "bld.history.col.where": "Where",
        "bld.history.details": "Show details",
        "bld.history.refresh": "Refresh",
        "bld.history.pick": "Choose a build in the list first.",
        "bld.history.not_found": "Build '{id}' not found in {path}.",
        "bld.history.proposals": "AI clients proposed fixes for {n} failed build(s); the details of {request} show "
                                 "them.",
        "bld.history.details_title": "Build {id}",
        "bld.history.request_header": "Request",
        "bld.history.attempt_header": "Attempt {n}",
        "bld.history.owner_person": "the person",
        "bld.history.error_line": "Error: {error}",
        "bld.history.interrupted_request": "The process that ran this request has ended before it finished.",
        "bld.history.awaiting": "Waits for a decision on a self-healing proposal in the {program} that started it.",
        "bld.history.interrupted_build": "The process that ran this build has ended before the build finished.",
        "bld.history.proposal_header": "Fix proposal {id} (AI-generated), proposed by {who}",
        "bld.history.proposal_summary": "Summary: {text}",
        "bld.history.proposal_cause": "Cause: {text}",
        "bld.history.proposal_step": "Manual step ({where}, not run by the framework): {text}",
        "bld.history.proposal_confidence": "Confidence: {value}",
        "bld.history.status_hint": "Details: Build > Build History...",
        "bld.field.request": "Request",
        "bld.field.build": "Build",
        "bld.field.status": "Status",
        "bld.field.stage": "Stage",
        "bld.field.progress": "Progress",
        "bld.field.model": "Model",
        "bld.field.target": "Target",
        "bld.field.board": "Board",
        "bld.field.format": "Format",
        "bld.field.quantization": "Quantization",
        "bld.field.optimization": "Optimization",
        "bld.field.owner": "Started by",
        "bld.field.where": "Where",
        "bld.field.started": "Started",
        "bld.field.ended": "Ended",
        "bld.field.output": "Output folder",
        "bld.field.process": "Process",
        "bld.field.error": "Error",

        # File > Settings... (llm-cli config show / config repos)
        "bld.settings.title": "Settings",
        "bld.settings.tab.effective": "Effective settings",
        "bld.settings.effective.intro": "Every setting with its value and where the value comes from: the defaults, "
                                        "a settings file, an LLM_FRAMEWORK_* variable, or changed while the program "
                                        "runs. Secrets are shown as REDACTED. Nothing can be changed here.",
        "bld.settings.col.name": "Setting",
        "bld.settings.col.value": "Value",
        "bld.settings.col.source": "Comes from",
        "bld.settings.source.default": "default",
        "bld.settings.source.environment": "environment variable",
        "bld.settings.source.file": "file {path}",
        "bld.settings.source.override": "changed while the program runs",
        "bld.settings.output_now": "Output folder for finished models: {path}",
        "bld.settings.output_folder": "Output folder...",
        "bld.settings.tab.repos": "Source repositories",
        "bld.settings.repos.intro": "Repository overrides: builds receive each one as <NAME>_REPO_OVERRIDE. Saved in "
                                    "{path}.",
        "bld.settings.repos.empty": "No repository overrides defined.",
        "bld.settings.col.repo": "Name",
        "bld.settings.col.url": "URL",
        "bld.settings.col.variable": "Passed to builds as",
        "bld.settings.repos.name": "Name:",
        "bld.settings.repos.name_hint": "e.g. llama.cpp",
        "bld.settings.repos.url": "URL:",
        "bld.settings.repos.url_hint": "https://...",
        "bld.settings.repos.add": "Add",
        "bld.settings.repos.remove": "Remove selected...",
        "bld.settings.repos.need_both": "Enter a name and a URL.",
        "bld.settings.repos.added": "Added repo override: {name} -> {url} (passed to builds as {variable})",
        "bld.settings.repos.removed": "Removed repo override: {name}",
        "bld.settings.repos.not_found": "Repo {name} not found.",
        "bld.settings.repos.pick": "Choose a repository override in the list first.",
        "bld.settings.repos.remove_question": "Remove this repository override?\n\nName: {name}\nURL: {url}\nPassed "
                                              "to builds as: {variable}\nSaved in: {path}\n\nBuilds then use the "
                                              "repository the target's scripts name.",

        # the module wizard (llm-cli module create / module generate-ai --provider --model)
        "bld.wizard.description": "Description:",
        "bld.wizard.description_hint": "Target for {name}",
        "bld.wizard.description_ai": "Written by the framework for a module made with Ditto: it says the module is "
                                     "untested and lists the values Ditto guessed.",
        "bld.field.wizard.description": "Description",
        "bld.wizard.run_label": "For this analysis only:",
        "bld.wizard.run_saved": "(AI settings)",
        "bld.wizard.run_model_hint": "model (empty: the AI settings' one)",
        "bld.wizard.run_tip": "Provider and model for this one analysis; the saved AI settings stay unchanged.",
        "bld.wizard.run_refused": "Not started: {reason}",
        "bld.wizard.run_not_ready": "{provider} is not set up for this analysis. Sign in under Tools > Configure AI "
                                    "Agent (Ditto)... or choose another provider.",
        "bld.wizard.pin_failed": "The base image {image} cannot be pinned: {reason} {next}",
    },
    "de": {
        "bld.menu": "Build",
        "bld.menu.targets": "Targets…",
        "bld.menu.history": "Build-Verlauf…",
        "bld.menu.settings": "Einstellungen…",

        "bld.targets.title": "Targets",
        "bld.targets.intro": "Alle Hardware-Targets dieser Installation ({n}). Die ID ist der Wert für --target. Ein "
                             "Target ohne Fähigkeiten baut jedes Format, aber seine Boards, Formate und "
                             "Quantisierungen werden vor einem Build nicht geprüft. Targets-Ordner: {path}",
        "bld.targets.empty": "Keine Targets gefunden. Targets-Ordner: {path}",
        "bld.targets.col.id": "ID (--target)",
        "bld.targets.col.name": "Name",
        "bld.targets.col.arch": "Architektur",
        "bld.targets.col.boards": "Boards",
        "bld.targets.col.formats": "Formate",
        "bld.targets.col.support": "Unterstützungsgrad",
        "bld.targets.none_declared": "- (keine Fähigkeiten angegeben)",
        "bld.targets.invalid": "- (Fähigkeiten-Block ungültig: „Target prüfen“ nennt jeden Fehler)",
        "bld.targets.check": "Target prüfen",
        "bld.targets.reload": "Neu laden",
        "bld.targets.pick": "Wähle zuerst ein Target in der Liste.",
        "bld.targets.unavailable": "Die Target-Liste ist nicht verfügbar: {reason}",

        "bld.check.title": "Target prüfen",
        "bld.check.valid": "Target „{id}“ ist gültig ({boards} Boards, Formate: {formats}).",
        "bld.check.invalid": "Target „{id}“: sein Fähigkeiten-Block ist ungültig:",
        "bld.check.error_line": "{field}: {message}",
        "bld.check.no_caps": "Target „{id}“ lädt, gibt aber keine Fähigkeiten an: Boards, Formate und "
                             "Quantisierungen werden vor einem Build nicht geprüft. Ergänze einen Block "
                             "„capabilities:“ (siehe targets/Rockchip/target.yml).",
        "bld.check.not_found": "Target „{name}“ nicht gefunden (oder seine target.yml lässt sich nicht lesen; siehe "
                               "das Log).",
        "bld.check.unavailable": "Der Target-Manager ist nicht verfügbar.",
        "bld.check.after_create": "Prüfung des neuen Targets:",
        "bld.check.after_install": "Prüfung des installierten Targets:",

        "bld.builder.targets": "Targets…",
        "bld.builder.targets.tip": "Alle Targets mit ID, Boards, Formaten und Unterstützungsgrad (Build > Targets…)",
        "bld.builder.check": "Target prüfen",
        "bld.builder.check.tip": "Prüft, ob das gewählte Target lädt und sein Fähigkeiten-Block dem Schema entspricht",
        "bld.builder.no_target": "Wähle zuerst ein Target.",
        "bld.builder.no_caps": "Dieses Target gibt keine Fähigkeiten an: jedes Format wird angeboten, und seine "
                               "Boards, Formate und Quantisierungen werden vor einem Build nicht geprüft.",
        "bld.builder.invalid_caps": "Der Fähigkeiten-Block dieses Targets ist ungültig („Target prüfen“ nennt jeden "
                                    "Fehler): jedes Format wird angeboten, und seine Boards, Formate und "
                                    "Quantisierungen werden vor einem Build nicht geprüft.",
        "bld.builder.priority": "Priorität:",
        "bld.builder.priority.tip": "Die Build-Warteschlange ordnet danach; ein fehlgeschlagener kritischer Build "
                                    "beendet die ganze Pipeline.",
        "bld.builder.priority.low": "Niedrig",
        "bld.builder.priority.normal": "Normal",
        "bld.builder.priority.high": "Hoch",
        "bld.builder.priority.urgent": "Dringend",
        "bld.builder.priority.critical": "Kritisch (ein fehlgeschlagener Build beendet die Pipeline)",

        "bld.history.title": "Build-Verlauf",
        "bld.history.intro": "Alle auf diesem Computer aufbewahrten Builds und alle noch wartenden Anfragen, egal wo "
                             "sie gestartet wurden (GUI, CLI, MCP-Server), die neuesten zuerst: {n} Einträge. "
                             "Build-Protokolle: {path}",
        "bld.history.empty": "Keine Builds in {path} gefunden.",
        "bld.history.col.request": "Anfrage",
        "bld.history.col.build": "Build",
        "bld.history.col.status": "Status",
        "bld.history.col.progress": "Fortschritt",
        "bld.history.col.target": "Target",
        "bld.history.col.format": "Format",
        "bld.history.col.quantization": "Quantisierung",
        "bld.history.col.model": "Modell",
        "bld.history.col.started": "Gestartet",
        "bld.history.col.where": "Wo",
        "bld.history.details": "Details anzeigen",
        "bld.history.refresh": "Aktualisieren",
        "bld.history.pick": "Wähle zuerst einen Build in der Liste.",
        "bld.history.not_found": "Build „{id}“ nicht in {path} gefunden.",
        "bld.history.proposals": "KI-Clients haben Korrekturen für {n} fehlgeschlagene(n) Build(s) vorgeschlagen; die "
                                 "Details von {request} zeigen sie.",
        "bld.history.details_title": "Build {id}",
        "bld.history.request_header": "Anfrage",
        "bld.history.attempt_header": "Versuch {n}",
        "bld.history.owner_person": "die Person",
        "bld.history.error_line": "Fehler: {error}",
        "bld.history.interrupted_request": "Der Prozess, der diese Anfrage ausgeführt hat, wurde beendet, bevor sie "
                                           "fertig war.",
        "bld.history.awaiting": "Wartet auf eine Entscheidung über einen Selbstheilungs-Vorschlag im Programm "
                                "{program}, das sie gestartet hat.",
        "bld.history.interrupted_build": "Der Prozess, der diesen Build ausgeführt hat, wurde beendet, bevor der "
                                         "Build fertig war.",
        "bld.history.proposal_header": "Korrekturvorschlag {id} (KI-generiert), vorgeschlagen von {who}",
        "bld.history.proposal_summary": "Zusammenfassung: {text}",
        "bld.history.proposal_cause": "Ursache: {text}",
        "bld.history.proposal_step": "Manueller Schritt ({where}, vom Framework nicht ausgeführt): {text}",
        "bld.history.proposal_confidence": "Sicherheit: {value}",
        "bld.history.status_hint": "Details: Build > Build-Verlauf…",
        "bld.field.request": "Anfrage",
        "bld.field.build": "Build",
        "bld.field.status": "Status",
        "bld.field.stage": "Schritt",
        "bld.field.progress": "Fortschritt",
        "bld.field.model": "Modell",
        "bld.field.target": "Target",
        "bld.field.board": "Board",
        "bld.field.format": "Format",
        "bld.field.quantization": "Quantisierung",
        "bld.field.optimization": "Optimierung",
        "bld.field.owner": "Gestartet von",
        "bld.field.where": "Wo",
        "bld.field.started": "Gestartet",
        "bld.field.ended": "Beendet",
        "bld.field.output": "Ausgabeordner",
        "bld.field.process": "Prozess",
        "bld.field.error": "Fehler",

        "bld.settings.title": "Einstellungen",
        "bld.settings.tab.effective": "Wirksame Einstellungen",
        "bld.settings.effective.intro": "Jede Einstellung mit ihrem Wert und woher der Wert kommt: die Vorgaben, eine "
                                        "Einstellungsdatei, eine Variable LLM_FRAMEWORK_* oder während des Laufs "
                                        "geändert. Geheimnisse erscheinen als REDACTED. Hier lässt sich nichts "
                                        "ändern.",
        "bld.settings.col.name": "Einstellung",
        "bld.settings.col.value": "Wert",
        "bld.settings.col.source": "Kommt aus",
        "bld.settings.source.default": "Vorgabe",
        "bld.settings.source.environment": "Umgebungsvariable",
        "bld.settings.source.file": "Datei {path}",
        "bld.settings.source.override": "während des Laufs geändert",
        "bld.settings.output_now": "Ausgabeordner für fertige Modelle: {path}",
        "bld.settings.output_folder": "Ausgabeordner…",
        "bld.settings.tab.repos": "Quell-Repositorys",
        "bld.settings.repos.intro": "Repository-Überschreibungen: Builds erhalten jede als <NAME>_REPO_OVERRIDE. "
                                    "Gespeichert in {path}.",
        "bld.settings.repos.empty": "Keine Repository-Überschreibungen angelegt.",
        "bld.settings.col.repo": "Name",
        "bld.settings.col.url": "URL",
        "bld.settings.col.variable": "An Builds übergeben als",
        "bld.settings.repos.name": "Name:",
        "bld.settings.repos.name_hint": "z. B. llama.cpp",
        "bld.settings.repos.url": "URL:",
        "bld.settings.repos.url_hint": "https://…",
        "bld.settings.repos.add": "Hinzufügen",
        "bld.settings.repos.remove": "Ausgewählte entfernen…",
        "bld.settings.repos.need_both": "Gib einen Namen und eine URL ein.",
        "bld.settings.repos.added": "Repository-Überschreibung hinzugefügt: {name} -> {url} (an Builds übergeben als "
                                    "{variable})",
        "bld.settings.repos.removed": "Repository-Überschreibung entfernt: {name}",
        "bld.settings.repos.not_found": "Repository {name} nicht gefunden.",
        "bld.settings.repos.pick": "Wähle zuerst eine Repository-Überschreibung in der Liste.",
        "bld.settings.repos.remove_question": "Diese Repository-Überschreibung entfernen?\n\nName: {name}\nURL: {url}"
                                              "\nAn Builds übergeben als: {variable}\nGespeichert in: {path}\n\n"
                                              "Builds verwenden dann das Repository, das die Skripte des Targets "
                                              "nennen.",

        "bld.wizard.description": "Beschreibung:",
        "bld.wizard.description_hint": "Target for {name}",   # the value written into target.yml (not translated)
        "bld.wizard.description_ai": "Vom Framework geschrieben für ein mit Ditto erstelltes Modul: sie sagt, dass das "
                                     "Modul ungetestet ist, und nennt die Werte, die Ditto geraten hat.",
        "bld.field.wizard.description": "Beschreibung",
        "bld.wizard.run_label": "Nur für diese Analyse:",
        "bld.wizard.run_saved": "(KI-Einstellungen)",
        "bld.wizard.run_model_hint": "Modell (leer: das der KI-Einstellungen)",
        "bld.wizard.run_tip": "Anbieter und Modell für diese eine Analyse; die gespeicherten KI-Einstellungen bleiben "
                              "unverändert.",
        "bld.wizard.run_refused": "Nicht gestartet: {reason}",
        "bld.wizard.run_not_ready": "{provider} ist für diese Analyse nicht eingerichtet. Melde dich unter Tools > "
                                    "Configure AI Agent (Ditto)... an oder wähle einen anderen Anbieter.",
        "bld.wizard.pin_failed": "Das Basis-Image {image} lässt sich nicht festlegen: {reason} {next}",
    },
}
