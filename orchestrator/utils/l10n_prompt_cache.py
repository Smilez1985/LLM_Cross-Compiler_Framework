#!/usr/bin/env python3
"""
Texts of the "Prompt cache" group (gui/prompt_cache_box.py) in the AI settings and in Ditto > Local Models...: the
memory budget of Ditto's local server (`llm-cli ditto cache` by mouse), English and German. LocalizationManager merges
them (localization.TEXT_MODULES). Every key has the same {placeholders} in both languages. This module imports nothing
from the framework.
"""

TEXTS = {
    "en": {
        "prompt_cache.title": "Prompt cache (Ditto's local server)",
        "prompt_cache.intro": "How much memory Ditto's local server may hold. \"auto\" takes the values measured on "
                              "this computer.",
        "prompt_cache.ram": "Prompt cache in RAM (MiB):",
        "prompt_cache.keep": "Keep the model loaded (minutes, 0 = always):",
        "prompt_cache.kv": "KV cache:",
        "prompt_cache.kv.auto": "auto (as on boards: smaller when little RAM is left)",
        "prompt_cache.over_cap_ok": "Accept values above the soft cap without a warning",
        "prompt_cache.apply": "Apply",
        "prompt_cache.refresh": "Measure again",
        "prompt_cache.loading": "Measuring this computer's memory…",
        "prompt_cache.load_failed": "The memory budget could not be read: {reason}",
        "prompt_cache.row": "{name}: automatic here {auto}, soft cap {soft}, hard limit {hard}. {reason}",
        "prompt_cache.running": "The running server has: prompt cache {ram} MiB, KV cache {kv}, context {ctx} tokens.",
        "prompt_cache.not_loaded": "The memory budget is still being measured; try again in a moment.",
        "prompt_cache.refused": "Not saved: {reason}",
        "prompt_cache.warning": "{warning}\n\nSet it anyway?",
        "prompt_cache.saved": "Saved. A running server takes the new values at its next start.",
        "prompt_cache.unchanged": "Nothing was changed.",
    },
    "de": {
        "prompt_cache.title": "Prompt-Cache (Dittos lokaler Server)",
        "prompt_cache.intro": "Wie viel Speicher Dittos lokaler Server halten darf. \"auto\" nimmt die Werte, die auf "
                              "diesem Computer gemessen wurden.",
        "prompt_cache.ram": "Prompt-Cache im RAM (MiB):",
        "prompt_cache.keep": "Modell geladen halten (Minuten, 0 = immer):",
        "prompt_cache.kv": "KV-Cache:",
        "prompt_cache.kv.auto": "auto (wie auf Boards: kleiner, wenn wenig RAM übrig ist)",
        "prompt_cache.over_cap_ok": "Werte über der weichen Grenze ohne Warnung annehmen",
        "prompt_cache.apply": "Übernehmen",
        "prompt_cache.refresh": "Neu messen",
        "prompt_cache.loading": "Der Speicher dieses Computers wird gemessen …",
        "prompt_cache.load_failed": "Das Speicherbudget konnte nicht gelesen werden: {reason}",
        "prompt_cache.row": "{name}: automatisch hier {auto}, weiche Grenze {soft}, harte Grenze {hard}. {reason}",
        "prompt_cache.running": "Der laufende Server hat: Prompt-Cache {ram} MiB, KV-Cache {kv}, Kontext {ctx} Tokens.",
        "prompt_cache.not_loaded": "Das Speicherbudget wird noch gemessen; versuch es gleich noch einmal.",
        "prompt_cache.refused": "Nicht gespeichert: {reason}",
        "prompt_cache.warning": "{warning}\n\nTrotzdem setzen?",
        "prompt_cache.saved": "Gespeichert. Ein laufender Server nimmt die neuen Werte bei seinem nächsten Start.",
        "prompt_cache.unchanged": "Nichts wurde geändert.",
    },
}
