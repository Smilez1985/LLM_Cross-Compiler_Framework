#!/usr/bin/env python3
"""
Texts of Ditto > Local Models..., Ditto > Evaluation... and the chat panel's "Options for this question" (W1': every
`llm-cli ditto models ...`, `ditto ask --no-tools/--provider/--model` and `ditto eval` action by mouse), English and
German. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there.

Where the CLI asks a question, the English text uses the CLI's words. Every key has the same {placeholders} in both
languages (tests/test_gui_ditto_models.py). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the menu (shared with Ditto > Local Runtime...: found by its object name or its title)
        "ditto_models.menu.models": "Local Models...",
        "ditto_models.menu.eval": "Evaluation...",

        # Ditto > Local Models... (`ditto models list` / `ditto models download`)
        "ditto_models.title": "Ditto's Local Models",
        "ditto_models.intro": "The models Ditto can run on this computer. Each one is pinned to a fixed revision; a "
                              "download is checked by its size and sha256 before it is used. They are saved in: "
                              "{folder}",
        "ditto_models.loading": "Reading the catalogue and this computer's hardware…",
        "ditto_models.load_failed": "The models could not be listed: {reason}",
        "ditto_models.recommended": "Recommended for this computer: {tier} ({why})",
        "ditto_models.chosen": "Chosen earlier for the local server: {tier}",
        "ditto_models.col.tier": "Tier",
        "ditto_models.col.file": "File",
        "ditto_models.col.size": "Size",
        "ditto_models.col.license": "Licence",
        "ditto_models.col.status": "Status",
        "ditto_models.col.note": "Note",
        "ditto_models.state.ready": "downloaded",
        "ditto_models.state.unverified": "not verified yet (checked before use)",
        "ditto_models.state.missing": "not downloaded",
        "ditto_models.note.recommended": "recommended",
        "ditto_models.note.chosen": "chosen",
        "ditto_models.note.on request": "on request",
        "ditto_models.note.not pinned": "not pinned (cannot be downloaded)",
        "ditto_models.download_recommended": "Download the recommended model...",
        "ditto_models.download": "Download selected...",
        "ditto_models.refresh": "Check again",
        "ditto_models.cancel": "Cancel",
        "ditto_models.close": "Close",
        "ditto_models.select_first": "Select a model in the list first.",
        "ditto_models.busy": "A download is running. Wait until it is done, or cancel it.",
        "ditto_models.ready": "{file} is already downloaded.",
        "ditto_models.question": "{repo} / {file} ({license})\n\nDownload {size} into {folder}?",
        "ditto_models.question_recommended": "Recommended: {why}\n\n{repo} / {file} ({license})\n\nDownload {size} "
                                             "into {folder}?",
        "ditto_models.downloading": "Downloading {file}…",
        "ditto_models.progress": "{name}: {done} of {total}",
        "ditto_models.cancelling": "Cancelling… the download stops after the current piece.",
        "ditto_models.cancelled": "The download was cancelled. What was downloaded so far is kept as a .part file next "
                                  "to the model; the next download of this model continues from there.",
        "ditto_models.saved": "Verified and saved: {path}",
        "ditto_models.failed": "It did not work: {reason}",

        # the chat panel: options for one question (`ditto ask --no-tools / --provider / --model`)
        "ditto_models.chat.options": "Options for this question",
        "ditto_models.chat.options_tip": "Answer without tools, or with another provider or model, for the next "
                                         "question only. The AI settings stay unchanged.",
        "ditto_models.chat.no_tools": "Answer without tools",
        "ditto_models.chat.no_tools_tip": "Ditto answers without using the framework's tools (nothing is looked up, "
                                          "started or changed through them).",
        "ditto_models.chat.provider": "Provider:",
        "ditto_models.chat.model": "Model:",
        "ditto_models.chat.settings": "As in the AI settings",
        "ditto_models.chat.model_placeholder": "empty: as in the AI settings",
        "ditto_models.chat.once": "For the next question only; the AI settings stay unchanged.",
        "ditto_models.chat.notice": "This question: {what}. The AI settings stay unchanged.",
        "ditto_models.chat.what.no_tools": "without tools",
        "ditto_models.chat.what.provider": "provider {provider}",
        "ditto_models.chat.what.model": "model {model}",
        "ditto_models.chat.refused": "The question was not sent: {reason}",

        # Ditto > Evaluation... (`ditto eval`)
        "ditto_eval.title": "Evaluate Ditto",
        "ditto_eval.intro": "Grades Ditto on fixed cases, for example to see whether a small local model is good "
                            "enough, or grades the knowledge-base search (retrieval). Nothing is built or changed.",
        "ditto_eval.cost": "Each case calls the chosen model. A cloud provider bills every request (about 20 per run "
                           "and repeat); local tiers and retrieval cost nothing.",
        "ditto_eval.group.cases": "Cases",
        "ditto_eval.group.model": "Model",
        "ditto_eval.group.results": "Results",
        "ditto_eval.kinds": "Kinds (none ticked: every kind except retrieval):",
        "ditto_eval.kind.healing": "Build-log analysis (healing)",
        "ditto_eval.kind.optimizer": "Build recommendations (optimizer)",
        "ditto_eval.kind.module": "Module proposals (module)",
        "ditto_eval.kind.chat": "Chat with tools (chat)",
        "ditto_eval.kind.retrieval": "Knowledge-base search (retrieval; no AI model)",
        "ditto_eval.variants": "Search variants for retrieval (none ticked: fused):",
        "ditto_eval.variant.fused": "Hybrid search (fused)",
        "ditto_eval.variant.rerank": "Hybrid search plus the reranker (rerank)",
        "ditto_eval.cases_label": "Only these cases (none ticked: every case of the chosen kinds):",
        "ditto_eval.case_item": "{id} ({kind}) {description}",
        "ditto_eval.cases_file": "Cases file:",
        "ditto_eval.cases_file.default": "the installation's cases",
        "ditto_eval.cases_file.choose": "Choose...",
        "ditto_eval.cases_file.reset": "Use the installation's cases",
        "ditto_eval.cases_file.dialog": "Choose a cases file",
        "ditto_eval.cases_file.filter": "Cases files (*.yml *.yaml)",
        "ditto_eval.cases_file.failed": "The cases cannot be read: {reason}",
        "ditto_eval.repeat": "Runs per case:",
        "ditto_eval.tiers": "Local model tiers (each one runs on this computer in turn; none ticked: the provider "
                            "below):",
        "ditto_eval.tier_item": "{tier} ({file}, {size}, {state})",
        "ditto_eval.tiers_failed": "The local models could not be listed: {reason}",
        "ditto_eval.tiers_missing": "These local model tiers are not downloaded yet: {tiers}. Download them first in "
                                    "{path:act_ditto_models_open}, or untick them.",
        "ditto_eval.provider": "Provider:",
        "ditto_eval.model": "Model:",
        "ditto_eval.effort": "Reasoning effort:",
        "ditto_eval.settings": "As in the AI settings",
        "ditto_eval.model_placeholder": "empty: as in the AI settings",
        "ditto_eval.effort.low": "low (local models: no thinking)",
        "ditto_eval.effort.medium": "medium",
        "ditto_eval.effort.high": "high",
        "ditto_eval.tier_conflict": "Local tiers run the managed local model; they cannot be combined with a provider "
                                    "or a model. Untick the tiers, or set provider and model back to the AI settings.",
        "ditto_eval.run": "Run evaluation...",
        "ditto_eval.cancel": "Cancel",
        "ditto_eval.save": "Save report...",
        "ditto_eval.close": "Close",
        "ditto_eval.question": "This evaluation runs {cases} cases, each {repeat} times, against: {target}.\n\n{cost}"
                               "\n\nStart it?",
        "ditto_eval.target.settings": "the provider and model in the AI settings",
        "ditto_eval.target.provider": "provider {provider}, model {model}",
        "ditto_eval.target.default": "as in the AI settings",
        "ditto_eval.target.tiers": "the local model tiers {tiers}, one after the other (the local server is started "
                                   "with each one and stopped afterwards; a server you started keeps running, or "
                                   "you are told when it was replaced)",
        "ditto_eval.target.retrieval": "the knowledge-base search with the local embedding model (no AI model; the "
                                       "embedding model, and for rerank the reranker, are downloaded on first use)",
        "ditto_eval.target.and": "{first}; and {second}",
        "ditto_eval.cost.cloud": "Each case calls the model. This provider runs in the cloud and bills every request "
                                 "(about 20 per run and repeat).",
        "ditto_eval.cost.local": "Each case calls a model on this computer; nothing is billed.",
        "ditto_eval.cost.local_fallback": "Each case calls a model on this computer. If it declines or cannot be "
                                          "reached, the calls move on along the fallback chain of the AI settings "
                                          "({providers}), and a cloud provider there bills them.",
        "ditto_eval.cost.unknown": "Each case calls the model. If it is a cloud provider, every request is billed "
                                   "(about 20 per run and repeat).",
        "ditto_eval.cost.none": "No AI model is called; nothing is billed.",
        "ditto_eval.no_ditto": "Ditto is not available, so only retrieval cases can run.",
        "ditto_eval.step.start_tier": "Starting the local server with {tier}…",
        "ditto_eval.step.index": "Indexing {documents} corpus documents into a temporary knowledge base (the "
                                 "embedding model is downloaded on first use)…",
        "ditto_eval.step.reranker": "Loading the reranker {model} (downloaded on first use)…",
        "ditto_eval.log.run": "{label}: {what}, {cases} cases x {repeat}",
        "ditto_eval.log.pass": "pass {id} ({seconds}s)",
        "ditto_eval.log.fail": "fail {id} ({seconds}s): {problem}",
        "ditto_eval.log.partial": "{rate} {id} ({seconds}s): {problem}",
        "ditto_eval.log.replaced": "Your local server ({model}) was replaced for the evaluation and is not running "
                                   "now; start it again in {path:act_ditto_runtime_open}",
        "ditto_eval.log.unused": "The tiers, provider, model and effort apply to AI cases; retrieval needs none.",
        "ditto_eval.working": "The evaluation runs…",
        "ditto_eval.done": "Done: {runs} runs.",
        "ditto_eval.cancelling": "Cancelling… the case that runs now finishes first.",
        "ditto_eval.cancelled": "Cancelled. The runs finished before are shown and can be saved.",
        "ditto_eval.failed": "The evaluation stopped: {reason}",
        "ditto_eval.busy": "An evaluation is running. Wait until it is done, or cancel it.",
        "ditto_eval.no_runs": "There is no report yet: run an evaluation first.",
        "ditto_eval.save_dialog": "Save the evaluation report",
        "ditto_eval.save_filter": "JSON files (*.json)",
        "ditto_eval.saved": "Report written to {path}",
        "ditto_eval.save_failed": "The report could not be written: {reason}",
        "ditto_eval.retrieval": "Retrieval",
        "ditto_eval.col.run": "Run",
        "ditto_eval.col.model": "Model",
        "ditto_eval.col.passed": "Passed",
        "ditto_eval.col.avg": "Avg s/case",
        "ditto_eval.col.unrelated": "Unrelated: no excerpt",
        "ditto_eval.col.p50": "p50 ms",
    },
    "de": {
        "ditto_models.menu.models": "Lokale Modelle...",
        "ditto_models.menu.eval": "Bewertung...",

        "ditto_models.title": "Dittos lokale Modelle",
        "ditto_models.intro": "Die Modelle, die Ditto auf diesem Computer ausführen kann. Jedes ist auf eine feste "
                              "Revision festgelegt; ein Download wird vor der Verwendung an Größe und sha256 geprüft. "
                              "Gespeichert werden sie in: {folder}",
        "ditto_models.loading": "Katalog und Hardware dieses Computers werden gelesen …",
        "ditto_models.load_failed": "Die Modelle konnten nicht aufgelistet werden: {reason}",
        "ditto_models.recommended": "Empfohlen für diesen Computer: {tier} ({why})",
        "ditto_models.chosen": "Früher für den lokalen Server gewählt: {tier}",
        "ditto_models.col.tier": "Stufe",
        "ditto_models.col.file": "Datei",
        "ditto_models.col.size": "Größe",
        "ditto_models.col.license": "Lizenz",
        "ditto_models.col.status": "Zustand",
        "ditto_models.col.note": "Hinweis",
        "ditto_models.state.ready": "heruntergeladen",
        "ditto_models.state.unverified": "noch nicht geprüft (wird vor der Verwendung geprüft)",
        "ditto_models.state.missing": "nicht heruntergeladen",
        "ditto_models.note.recommended": "empfohlen",
        "ditto_models.note.chosen": "gewählt",
        "ditto_models.note.on request": "auf Wunsch",
        "ditto_models.note.not pinned": "nicht festgelegt (kann nicht heruntergeladen werden)",
        "ditto_models.download_recommended": "Empfohlenes Modell herunterladen...",
        "ditto_models.download": "Ausgewähltes herunterladen...",
        "ditto_models.refresh": "Erneut prüfen",
        "ditto_models.cancel": "Abbrechen",
        "ditto_models.close": "Schließen",
        "ditto_models.select_first": "Wähle zuerst ein Modell in der Liste aus.",
        "ditto_models.busy": "Ein Download läuft. Warte, bis er fertig ist, oder brich ihn ab.",
        "ditto_models.ready": "{file} ist schon heruntergeladen.",
        "ditto_models.question": "{repo} / {file} ({license})\n\n{size} nach {folder} herunterladen?",
        "ditto_models.question_recommended": "Empfohlen: {why}\n\n{repo} / {file} ({license})\n\n{size} nach "
                                             "{folder} herunterladen?",
        "ditto_models.downloading": "{file} wird heruntergeladen …",
        "ditto_models.progress": "{name}: {done} von {total}",
        "ditto_models.cancelling": "Wird abgebrochen … der Download hält nach dem aktuellen Stück an.",
        "ditto_models.cancelled": "Der Download wurde abgebrochen. Was schon heruntergeladen ist, bleibt als "
                                  ".part-Datei neben dem Modell; der nächste Download dieses Modells macht dort "
                                  "weiter.",
        "ditto_models.saved": "Geprüft und gespeichert: {path}",
        "ditto_models.failed": "Das hat nicht geklappt: {reason}",

        "ditto_models.chat.options": "Optionen für diese Frage",
        "ditto_models.chat.options_tip": "Ohne Werkzeuge antworten, oder mit einem anderen Anbieter oder Modell, nur "
                                         "für die nächste Frage. Die KI-Einstellungen bleiben unverändert.",
        "ditto_models.chat.no_tools": "Ohne Werkzeuge antworten",
        "ditto_models.chat.no_tools_tip": "Ditto antwortet, ohne die Werkzeuge des Frameworks zu verwenden (damit wird "
                                          "nichts nachgeschlagen, gestartet oder geändert).",
        "ditto_models.chat.provider": "Anbieter:",
        "ditto_models.chat.model": "Modell:",
        "ditto_models.chat.settings": "Wie in den KI-Einstellungen",
        "ditto_models.chat.model_placeholder": "leer: wie in den KI-Einstellungen",
        "ditto_models.chat.once": "Nur für die nächste Frage; die KI-Einstellungen bleiben unverändert.",
        "ditto_models.chat.notice": "Diese Frage: {what}. Die KI-Einstellungen bleiben unverändert.",
        "ditto_models.chat.what.no_tools": "ohne Werkzeuge",
        "ditto_models.chat.what.provider": "Anbieter {provider}",
        "ditto_models.chat.what.model": "Modell {model}",
        "ditto_models.chat.refused": "Die Frage wurde nicht gesendet: {reason}",

        "ditto_eval.title": "Ditto bewerten",
        "ditto_eval.intro": "Bewertet Ditto an festen Fällen, zum Beispiel um zu sehen, ob ein kleines lokales Modell "
                            "gut genug ist, oder bewertet die Suche in der Wissensbasis (Retrieval). Nichts wird "
                            "gebaut oder geändert.",
        "ditto_eval.cost": "Jeder Fall ruft das gewählte Modell auf. Ein Cloud-Anbieter berechnet jede Anfrage (etwa "
                           "20 pro Lauf und Wiederholung); lokale Stufen und Retrieval kosten nichts.",
        "ditto_eval.group.cases": "Fälle",
        "ditto_eval.group.model": "Modell",
        "ditto_eval.group.results": "Ergebnisse",
        "ditto_eval.kinds": "Arten (nichts angehakt: jede Art außer Retrieval):",
        "ditto_eval.kind.healing": "Build-Log-Analyse (healing)",
        "ditto_eval.kind.optimizer": "Build-Empfehlungen (optimizer)",
        "ditto_eval.kind.module": "Modulvorschläge (module)",
        "ditto_eval.kind.chat": "Chat mit Werkzeugen (chat)",
        "ditto_eval.kind.retrieval": "Suche in der Wissensbasis (retrieval; kein KI-Modell)",
        "ditto_eval.variants": "Suchvarianten für Retrieval (nichts angehakt: fused):",
        "ditto_eval.variant.fused": "Hybride Suche (fused)",
        "ditto_eval.variant.rerank": "Hybride Suche plus Reranker (rerank)",
        "ditto_eval.cases_label": "Nur diese Fälle (nichts angehakt: jeder Fall der gewählten Arten):",
        "ditto_eval.case_item": "{id} ({kind}) {description}",
        "ditto_eval.cases_file": "Falldatei:",
        "ditto_eval.cases_file.default": "die Fälle der Installation",
        "ditto_eval.cases_file.choose": "Auswählen...",
        "ditto_eval.cases_file.reset": "Fälle der Installation verwenden",
        "ditto_eval.cases_file.dialog": "Eine Falldatei auswählen",
        "ditto_eval.cases_file.filter": "Falldateien (*.yml *.yaml)",
        "ditto_eval.cases_file.failed": "Die Fälle können nicht gelesen werden: {reason}",
        "ditto_eval.repeat": "Läufe pro Fall:",
        "ditto_eval.tiers": "Lokale Modellstufen (jede läuft nacheinander auf diesem Computer; nichts angehakt: der "
                            "Anbieter unten):",
        "ditto_eval.tier_item": "{tier} ({file}, {size}, {state})",
        "ditto_eval.tiers_failed": "Die lokalen Modelle konnten nicht aufgelistet werden: {reason}",
        "ditto_eval.tiers_missing": "Diese lokalen Modellstufen sind noch nicht heruntergeladen: {tiers}. Lade sie "
                                    "zuerst unter {path:act_ditto_models_open} herunter, oder nimm die Haken weg.",
        "ditto_eval.provider": "Anbieter:",
        "ditto_eval.model": "Modell:",
        "ditto_eval.effort": "Denkaufwand:",
        "ditto_eval.settings": "Wie in den KI-Einstellungen",
        "ditto_eval.model_placeholder": "leer: wie in den KI-Einstellungen",
        "ditto_eval.effort.low": "niedrig (lokale Modelle: ohne Nachdenken)",
        "ditto_eval.effort.medium": "mittel",
        "ditto_eval.effort.high": "hoch",
        "ditto_eval.tier_conflict": "Lokale Stufen verwenden das verwaltete lokale Modell; sie lassen sich nicht mit "
                                    "einem Anbieter oder Modell kombinieren. Nimm die Haken bei den Stufen weg, oder "
                                    "stelle Anbieter und Modell zurück auf die KI-Einstellungen.",
        "ditto_eval.run": "Bewertung starten...",
        "ditto_eval.cancel": "Abbrechen",
        "ditto_eval.save": "Bericht speichern...",
        "ditto_eval.close": "Schließen",
        "ditto_eval.question": "Diese Bewertung führt {cases} Fälle je {repeat}-mal aus, mit: {target}.\n\n{cost}\n\n"
                               "Starten?",
        "ditto_eval.target.settings": "dem Anbieter und Modell aus den KI-Einstellungen",
        "ditto_eval.target.provider": "Anbieter {provider}, Modell {model}",
        "ditto_eval.target.default": "wie in den KI-Einstellungen",
        "ditto_eval.target.tiers": "den lokalen Modellstufen {tiers}, eine nach der anderen (der lokale Server wird "
                                   "mit jeder gestartet und danach gestoppt; ein Server, den du gestartet hast, läuft "
                                   "weiter, oder du erfährst, dass er ersetzt wurde)",
        "ditto_eval.target.retrieval": "der Suche in der Wissensbasis mit dem lokalen Embedding-Modell (kein "
                                       "KI-Modell; das Embedding-Modell und für rerank der Reranker werden bei der "
                                       "ersten Verwendung heruntergeladen)",
        "ditto_eval.target.and": "{first}; und {second}",
        "ditto_eval.cost.cloud": "Jeder Fall ruft das Modell auf. Dieser Anbieter läuft in der Cloud und berechnet "
                                 "jede Anfrage (etwa 20 pro Lauf und Wiederholung).",
        "ditto_eval.cost.local": "Jeder Fall ruft ein Modell auf diesem Computer auf; nichts wird berechnet.",
        "ditto_eval.cost.local_fallback": "Jeder Fall ruft ein Modell auf diesem Computer auf. Lehnt es ab oder ist "
                                          "es nicht erreichbar, gehen die Aufrufe entlang der Ersatzkette der "
                                          "KI-Einstellungen weiter ({providers}), und ein Cloud-Anbieter dort "
                                          "berechnet sie.",
        "ditto_eval.cost.unknown": "Jeder Fall ruft das Modell auf. Ist es ein Cloud-Anbieter, wird jede Anfrage "
                                   "berechnet (etwa 20 pro Lauf und Wiederholung).",
        "ditto_eval.cost.none": "Kein KI-Modell wird aufgerufen; nichts wird berechnet.",
        "ditto_eval.no_ditto": "Ditto ist nicht verfügbar, daher können nur Retrieval-Fälle laufen.",
        "ditto_eval.step.start_tier": "Der lokale Server wird mit {tier} gestartet …",
        "ditto_eval.step.index": "{documents} Korpus-Dokumente werden in eine vorübergehende Wissensbasis "
                                 "aufgenommen (das Embedding-Modell wird bei der ersten Verwendung heruntergeladen) …",
        "ditto_eval.step.reranker": "Der Reranker {model} wird geladen (bei der ersten Verwendung heruntergeladen) …",
        "ditto_eval.log.run": "{label}: {what}, {cases} Fälle x {repeat}",
        "ditto_eval.log.pass": "bestanden {id} ({seconds} s)",
        "ditto_eval.log.fail": "nicht bestanden {id} ({seconds} s): {problem}",
        "ditto_eval.log.partial": "{rate} {id} ({seconds} s): {problem}",
        "ditto_eval.log.replaced": "Dein lokaler Server ({model}) wurde für die Bewertung ersetzt und läuft jetzt "
                                   "nicht; starte ihn wieder unter {path:act_ditto_runtime_open}",
        "ditto_eval.log.unused": "Stufen, Anbieter, Modell und Denkaufwand gelten für KI-Fälle; Retrieval braucht "
                                 "keine davon.",
        "ditto_eval.working": "Die Bewertung läuft …",
        "ditto_eval.done": "Fertig: {runs} Läufe.",
        "ditto_eval.cancelling": "Wird abgebrochen … der Fall, der gerade läuft, wird noch fertig.",
        "ditto_eval.cancelled": "Abgebrochen. Die vorher fertigen Läufe werden gezeigt und können gespeichert werden.",
        "ditto_eval.failed": "Die Bewertung wurde angehalten: {reason}",
        "ditto_eval.busy": "Eine Bewertung läuft. Warte, bis sie fertig ist, oder brich sie ab.",
        "ditto_eval.no_runs": "Es gibt noch keinen Bericht: starte zuerst eine Bewertung.",
        "ditto_eval.save_dialog": "Den Bewertungsbericht speichern",
        "ditto_eval.save_filter": "JSON-Dateien (*.json)",
        "ditto_eval.saved": "Bericht geschrieben nach {path}",
        "ditto_eval.save_failed": "Der Bericht konnte nicht geschrieben werden: {reason}",
        "ditto_eval.retrieval": "Retrieval",
        "ditto_eval.col.run": "Lauf",
        "ditto_eval.col.model": "Modell",
        "ditto_eval.col.passed": "Bestanden",
        "ditto_eval.col.avg": "Mittel s/Fall",
        "ditto_eval.col.unrelated": "Unpassend: kein Auszug",
        "ditto_eval.col.p50": "p50 ms",
    },
}
