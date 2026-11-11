#!/usr/bin/env python3
"""
Texts of the large models (DESIGN, the row "Large models"): the Builder's fields for a given importance matrix and
per-tensor types, its line and question about this computer's disk and memory, Build > F16 Intermediates..., and the
Auto-Tuning tab's per-tensor candidates; English and German. LocalizationManager merges them
(localization.TEXT_MODULES); a key that exists already is refused there. Every key has the same {placeholders} in both
languages. The texts address nobody, so the German du and Sie forms read the same. This module imports nothing from
the framework.
"""

TEXTS = {
    "en": {
        "bld.large.imatrix": "Importance matrix file",
        "bld.large.imatrix.placeholder": "empty: computed from the calibration text; a file or hf:owner/name/path",
        "bld.large.imatrix.tip": "A given importance matrix (GGUF), used instead of computing one: a file in the GGUF "
                                 "or .dat format of llama-imatrix, or a Hugging Face file written hf:owner/name/path, "
                                 "downloaded and checked before the build. Its tensor names are compared with the "
                                 "model's; a matrix of another model stops the build with the reason.",
        "bld.large.imatrix.browse": "Choose an importance matrix file",
        "bld.large.tensor": "Per-tensor types",
        "bld.large.tensor.placeholder": "e.g. ffn_down=q6_k, output=q8_0 (empty: the type's own mix)",
        "bld.large.tensor.tip": "Per-tensor types for llama-quantize, separated by commas: a regular expression on the "
                                "tensor's name and a ggml type. output=TYPE and token_embd=TYPE set the output and "
                                "embedding tensors. Measure such a recipe in the Auto-Tuning tab before you build it.",
        "bld.large.resources.title": "Disk and memory of this computer",
        "bld.large.resources.question": "This computer's disk or memory does not hold this build by the check:\n\n"
                                        "{lines}\n\nThe build may fail late or swap. Build anyway?",
        "bld.large.resources.cancelled": "Nothing was started: this computer's disk or memory does not hold the build.",
        "bld.large.resources.not_made": "Disk and memory check: not made ({reason}).",
        "bld.menu.f16": "F16 Intermediates...",
        "bld.f16.title": "F16 Intermediates",
        "bld.f16.intro": "The F16 GGUF a build converted from a Hugging Face model, kept per model, commit and "
                         "llama.cpp pin: the next build of the same model quantizes from it without converting again. "
                         "Kept only with room on the disk and while the setting keep_f16_intermediates is on.",
        "bld.f16.col.model": "Model",
        "bld.f16.col.commit": "Commit",
        "bld.f16.col.pin": "llama.cpp",
        "bld.f16.col.size": "Size",
        "bld.f16.col.kept": "Kept",
        "bld.f16.col.used": "Last used",
        "bld.f16.delete": "Delete...",
        "bld.f16.close": "Close",
        "bld.f16.none": "No F16 intermediate is kept.",
        "bld.f16.total": "{count} kept, {size} in all.",
        "bld.f16.off": "The setting keep_f16_intermediates is off: builds use these, but keep no new ones.",
        "bld.f16.deleted": "{count} deleted.",
        "tuning.recipes": "Per-tensor candidates:",
        "tuning.recipes.tip": "Per-tensor recipes to measure, separated by semicolons, each TYPE+PATTERN=TYPE,..., "
                              "e.g. Q4_K_M+ffn_down=q6_k,attn_v=q8_0; measured against the same reference as every "
                              "type, and Build the Recommended builds one with its per-tensor types",
    },
    "de": {
        "bld.large.imatrix": "Importance-Matrix-Datei",
        "bld.large.imatrix.placeholder": "leer: aus dem Kalibriertext berechnet; eine Datei oder hf:besitzer/name/pfad",
        "bld.large.imatrix.tip": "Eine vorgegebene Importance Matrix (GGUF), statt eine zu berechnen: eine Datei im "
                                 "GGUF- oder .dat-Format von llama-imatrix oder eine Hugging-Face-Datei als "
                                 "hf:besitzer/name/pfad, vor dem Build heruntergeladen und geprüft. Ihre Tensornamen "
                                 "werden mit denen des Modells verglichen; die Matrix eines anderen Modells hält den "
                                 "Build mit dem Grund an.",
        "bld.large.imatrix.browse": "Eine Importance-Matrix-Datei wählen",
        "bld.large.tensor": "Typen je Tensor",
        "bld.large.tensor.placeholder": "z. B. ffn_down=q6_k, output=q8_0 (leer: die Mischung des Typs)",
        "bld.large.tensor.tip": "Typen je Tensor für llama-quantize, durch Kommas getrennt: ein regulärer Ausdruck auf "
                                "dem Namen des Tensors und ein ggml-Typ. output=TYP und token_embd=TYP legen die "
                                "Ausgabe- und Embedding-Tensoren fest. Miss ein solches Rezept im Auto-Tuning-Tab, "
                                "bevor du es baust.",
        "bld.large.resources.title": "Platte und Speicher dieses Rechners",
        "bld.large.resources.question": "Platte oder Speicher dieses Rechners reichen nach der Prüfung nicht für "
                                        "diesen Build:\n\n{lines}\n\nDer Build kann spät scheitern oder auslagern. "
                                        "Trotzdem bauen?",
        "bld.large.resources.cancelled": "Nichts wurde gestartet: Platte oder Speicher dieses Rechners reichen nicht "
                                         "für den Build.",
        "bld.large.resources.not_made": "Prüfung von Platte und Speicher: nicht gemacht ({reason}).",
        "bld.menu.f16": "F16-Zwischenstufen…",
        "bld.f16.title": "F16-Zwischenstufen",
        "bld.f16.intro": "Die F16-GGUF, die ein Build aus einem Hugging-Face-Modell umgewandelt hat, aufbewahrt je "
                         "Modell, Commit und llama.cpp-Festlegung: Der nächste Build desselben Modells quantisiert "
                         "von ihr aus, ohne erneut umzuwandeln. Aufbewahrt nur mit Platz auf der Platte und solange "
                         "die Einstellung keep_f16_intermediates an ist.",
        "bld.f16.col.model": "Modell",
        "bld.f16.col.commit": "Commit",
        "bld.f16.col.pin": "llama.cpp",
        "bld.f16.col.size": "Größe",
        "bld.f16.col.kept": "Aufbewahrt",
        "bld.f16.col.used": "Zuletzt genutzt",
        "bld.f16.delete": "Löschen…",
        "bld.f16.close": "Schließen",
        "bld.f16.none": "Keine F16-Zwischenstufe ist aufbewahrt.",
        "bld.f16.total": "{count} aufbewahrt, {size} insgesamt.",
        "bld.f16.off": "Die Einstellung keep_f16_intermediates ist aus: Builds nutzen diese, bewahren aber keine "
                       "neuen auf.",
        "bld.f16.deleted": "{count} gelöscht.",
        "tuning.recipes": "Kandidaten je Tensor:",
        "tuning.recipes.tip": "Rezepte je Tensor zum Messen, durch Semikolons getrennt, jedes TYP+MUSTER=TYP,..., "
                              "z. B. Q4_K_M+ffn_down=q6_k,attn_v=q8_0; gemessen gegen dieselbe Referenz wie jeder "
                              "Typ, und Das Empfohlene bauen baut eines mit seinen Typen je Tensor",
    },
}
