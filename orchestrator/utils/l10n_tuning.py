#!/usr/bin/env python3
"""
Texts of the Auto-Tuning tab (orchestrator/gui/tuning_tab.py, Core/tuning.py), English and German. LocalizationManager
merges them (localization.TEXT_MODULES); a key that exists already is refused there. Every key has the same
{placeholders} in both languages. The texts address nobody, so the German du and Sie forms read the same. Technical
terms stay English (KL divergence, importance matrix: "Importance Matrix"). This module imports nothing from the
framework.
"""

TEXTS = {
    "en": {
        "tuning.intro": "Auto-Tuning measures which quantization of a model gives the best quality that still fits "
                        "a board: each candidate type is quantized as a build makes it and compared with the original "
                        "model by the KL divergence and the top-1 agreement on a fixed evaluation text. The result is "
                        "a recommendation; no type is refused.",
        "tuning.model": "Model:",
        "tuning.model.tip": "A Hugging Face repo id (owner/name) or a local model folder or GGUF file",
        "tuning.target": "Target:",
        "tuning.board": "Board:",
        "tuning.types": "More types:",
        "tuning.types.tip": "Types to measure as well, separated by commas (measured whatever the memory estimate "
                            "says)",
        "tuning.imatrix": "Importance matrix:",
        "tuning.imatrix.auto": "As a build (below 5 bits per weight)",
        "tuning.imatrix.both": "With and without (types below 5 bits)",
        "tuning.chunks": "Chunks:",
        "tuning.chunks.tip": "Chunks of 512 tokens of the evaluation text: more give a smaller uncertainty and take "
                             "longer",
        "tuning.plan": "Plan",
        "tuning.start": "Start Tuning",
        "tuning.refresh": "Refresh",
        "tuning.build": "Build the Recommended",
        "tuning.runs": "Finished tuning runs:",
        "tuning.planning": "Planning (the model's size is looked up)...",
        "tuning.started": "Tuning request {request} queued; its progress shows in the Builder's build list, its "
                          "report here when it has finished.",
        "tuning.build_started": "Build request {request} queued with the recommended type.",
        "tuning.no_runs": "No tuning run yet.",
        "tuning.no_model": "Name a model first.",
        "tuning.failed": "Not possible: {reason}",
        "tuning.run_row": "{model} for {board}: {choice} ({created})",
        "tuning.none": "no recommendation",
    },
    "de": {
        "tuning.intro": "Auto-Tuning misst, welche Quantisierung eines Modells die beste Qualität ergibt, die noch "
                        "auf ein Board passt: Jeder Kandidatentyp wird quantisiert, wie ein Build ihn macht, und mit "
                        "dem Originalmodell über die KL-Divergenz und die Top-1-Übereinstimmung auf einem festen "
                        "Bewertungstext verglichen. Das Ergebnis ist eine Empfehlung; kein Typ wird verweigert.",
        "tuning.model": "Modell:",
        "tuning.model.tip": "Eine Hugging-Face-Repo-ID (owner/name) oder ein lokaler Modellordner oder eine "
                            "GGUF-Datei",
        "tuning.target": "Target:",
        "tuning.board": "Board:",
        "tuning.types": "Weitere Typen:",
        "tuning.types.tip": "Typen, die auch gemessen werden, durch Kommas getrennt (gemessen, gleich was die "
                            "Speicherschätzung sagt)",
        "tuning.imatrix": "Importance Matrix:",
        "tuning.imatrix.auto": "Wie ein Build (unter 5 Bit pro Gewicht)",
        "tuning.imatrix.both": "Mit und ohne (Typen unter 5 Bit)",
        "tuning.chunks": "Chunks:",
        "tuning.chunks.tip": "Chunks zu 512 Tokens des Bewertungstexts: mehr ergeben eine kleinere Unsicherheit und "
                             "dauern länger",
        "tuning.plan": "Planen",
        "tuning.start": "Tuning starten",
        "tuning.refresh": "Aktualisieren",
        "tuning.build": "Das Empfohlene bauen",
        "tuning.runs": "Fertige Tuning-Läufe:",
        "tuning.planning": "Wird geplant (die Größe des Modells wird nachgeschlagen)...",
        "tuning.started": "Tuning-Anfrage {request} eingereiht; ihr Fortschritt steht in der Build-Liste des "
                          "Builders, ihr Bericht hier, wenn sie fertig ist.",
        "tuning.build_started": "Build-Anfrage {request} mit dem empfohlenen Typ eingereiht.",
        "tuning.no_runs": "Noch kein Tuning-Lauf.",
        "tuning.no_model": "Zuerst ein Modell nennen.",
        "tuning.failed": "Nicht möglich: {reason}",
        "tuning.run_row": "{model} für {board}: {choice} ({created})",
        "tuning.none": "keine Empfehlung",
    },
}
