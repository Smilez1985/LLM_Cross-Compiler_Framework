"""
Texts of Ditto's context window (Settings > Language model > Context window, Ditto > Context Window...; `llm-cli ditto
context`), English and German. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already
is refused there. Every key has the same {placeholders} in both languages. German addresses the person with "du";
FORMAL has the "Sie" form of each such text. Imports nothing.
"""

TEXTS = {
    "en": {
        "context.menu": "Context Window...",
        "context.title": "Context window",
        "context.intro": "How much text Ditto's model reads at once, where that number comes from, how the tokens "
                         "are counted, and what was compacted to fit. The original of every compaction is kept.",
        "context.loading": "Reading the context window ...",
        "context.no_ditto": "Ditto is not available.",
        "context.failed": "The context window could not be read: {reason}",
        "context.refresh": "Check Again",
        "context.original": "Show Original...",
        "context.undo": "Undo Compaction...",
        "context.window": "Context window: {tokens} tokens, from {source}",
        "context.how": "Source: {how}",
        "context.max_output": "Longest answer: {tokens} tokens",
        "context.started.confirmed": "The local server was started with -c {ctx}; /props confirms it.",
        "context.started.differs": "The local server was started with -c {ctx}; /props reports another number, "
                                   "which Ditto uses.",
        "context.started.open": "The local server was started with -c {ctx}; /props has not confirmed it yet.",
        "context.counting": "Counting: {how}",
        "context.budget": "Saved conversation: {messages} message(s), about {history} tokens; room for excerpts and "
                          "history {room} tokens; {answer} tokens kept for the answer.",
        "context.compactions": "Compactions kept: {n}; the newest, {id} at {at}: {summarized} message(s) "
                               "summarized, {kept} kept word for word.",
        "context.no_compactions": "No compaction so far.",
        "context.none": "No compaction is kept.",
        "context.original.title": "Original conversation of the newest compaction",
        "context.undo.title": "Undo compaction",
        "context.undo.question": "Undo compaction {id} of {at}? Its {n} original message(s) come back in front of "
                                 "what was said after it. The next question decides again whether the conversation "
                                 "fits the window.",
        "context.undo.done": "Undone: the original conversation is back.",
        "context.undo.failed": "The conversation changed since or could not be saved; nothing was undone.",
        "context.source.local_server": "the local server's own report",
        "context.source.local_started": "the start of the local server (not confirmed yet)",
        "context.source.provider": "the provider's server",
        "context.source.table": "the pinned table",
        "context.source.default": "a conservative default",
        "context.source.setting": "your setting ai_context_window",
    },
    "de": {
        "context.menu": "Kontextfenster...",
        "context.title": "Kontextfenster",
        "context.intro": "Wie viel Text Dittos Modell auf einmal liest, woher diese Zahl kommt, wie die Token gezählt "
                         "werden und was verdichtet wurde, damit es passt. Das Original jeder Verdichtung bleibt "
                         "erhalten.",
        "context.loading": "Das Kontextfenster wird gelesen ...",
        "context.no_ditto": "Ditto ist nicht verfügbar.",
        "context.failed": "Das Kontextfenster konnte nicht gelesen werden: {reason}",
        "context.refresh": "Erneut prüfen",
        "context.original": "Original zeigen...",
        "context.undo": "Verdichtung rückgängig...",
        "context.window": "Kontextfenster: {tokens} Token, von {source}",
        "context.how": "Quelle: {how}",
        "context.max_output": "Längste Antwort: {tokens} Token",
        "context.started.confirmed": "Der lokale Server wurde mit -c {ctx} gestartet; /props bestätigt das.",
        "context.started.differs": "Der lokale Server wurde mit -c {ctx} gestartet; /props meldet eine andere Zahl, "
                                   "die Ditto nutzt.",
        "context.started.open": "Der lokale Server wurde mit -c {ctx} gestartet; /props hat das noch nicht "
                                "bestätigt.",
        "context.counting": "Zählung: {how}",
        "context.budget": "Gespeichertes Gespräch: {messages} Nachricht(en), etwa {history} Token; Platz für Auszüge "
                          "und Verlauf {room} Token; {answer} Token für die Antwort reserviert.",
        "context.compactions": "Gespeicherte Verdichtungen: {n}; die neueste, {id} am {at}: {summarized} "
                               "Nachricht(en) zusammengefasst, {kept} Wort für Wort behalten.",
        "context.no_compactions": "Bisher keine Verdichtung.",
        "context.none": "Es ist keine Verdichtung gespeichert.",
        "context.original.title": "Ursprüngliches Gespräch der neuesten Verdichtung",
        "context.undo.title": "Verdichtung rückgängig machen",
        "context.undo.question": "Verdichtung {id} vom {at} rückgängig machen? Ihre {n} ursprünglichen "
                                 "Nachricht(en) kommen vor das zurück, was danach gesagt wurde. Die nächste Frage "
                                 "entscheidet neu, ob das Gespräch ins Fenster passt.",
        "context.undo.done": "Rückgängig gemacht: das ursprüngliche Gespräch ist zurück.",
        "context.undo.failed": "Das Gespräch hat sich inzwischen geändert oder konnte nicht gespeichert werden; "
                               "nichts wurde rückgängig gemacht.",
        "context.source.local_server": "dem eigenen Bericht des lokalen Servers",
        "context.source.local_started": "dem Start des lokalen Servers (noch nicht bestätigt)",
        "context.source.provider": "dem Server des Anbieters",
        "context.source.table": "der festgehaltenen Tabelle",
        "context.source.default": "einer vorsichtigen Vorgabe",
        "context.source.setting": "deiner Einstellung ai_context_window",
    },
}

FORMAL = {
    "de": {
        "context.source.setting": "Ihrer Einstellung ai_context_window",
    },
}
