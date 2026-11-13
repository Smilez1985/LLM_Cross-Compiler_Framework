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
        "cloud_cache.title": "Cloud cache",
        "cloud_cache.intro": "How the provider of Ditto's chat model caches the repeated start of every request, how "
                             "long Claude's cache lives, and a soft cap on what writing to it may cost in one "
                             "conversation. Crossing the cap brings a notice; caching goes on.",
        "cloud_cache.ttl": "Lifetime (Claude):",
        "cloud_cache.ttl.5m": "5 minutes (default; every hit renews it at no cost)",
        "cloud_cache.ttl.1h": "1 hour (a write costs twice the input price)",
        "cloud_cache.limit": "Soft cap per conversation (0 = no notice):",
        "cloud_cache.apply": "Apply",
        "cloud_cache.failed": "The cloud cache could not be read: {reason}",
        "cloud_cache.not_saved": "Not saved: {reason}",
        "cloud_cache.way.breakpoints": "Anthropic's prompt caching: cache points on the system text and on the "
                                       "conversation's newest block.",
        "cloud_cache.way.openrouter_breakpoints": "OpenRouter passes Anthropic's cache points on: the system text and "
                                                  "the conversation's newest block.",
        "cloud_cache.way.automatic": "The provider caches a stable start of the request by itself; nothing is sent "
                                     "for it, and the lifetime is the provider's.",
        "cloud_cache.way.none": "No cloud cache: a local engine keeps its own prompt cache.",
        "cloud_cache.minimum": "This model caches a prompt of at least {tokens} tokens.",
        "cloud_cache.ttl_not_here": "The lifetime above applies to Claude only; this provider sets its own.",
        "cloud_cache.totals": "This conversation: {calls} call(s), {read} tokens read from the cache, {written} "
                              "written, {uncached} not cached; cache writes about {cost} USD.",
        "cloud_cache.unpriced": "{tokens} written tokens have no price in the table and are not counted in USD.",
        "cloud_cache.cap_off": "The soft cap is off.",
        "cloud_cache.cap_reached": "The soft cap of {limit} USD is reached; caching goes on.",
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
        "cloud_cache.title": "Cloud-Cache",
        "cloud_cache.intro": "Wie der Anbieter von Dittos Chat-Modell den wiederkehrenden Anfang jeder Anfrage cacht, "
                             "wie lange Claudes Cache lebt, und eine weiche Obergrenze dafür, was das Schreiben in "
                             "ihn in einem Gespräch kosten darf. Wird sie überschritten, kommt ein Hinweis; das "
                             "Caching geht weiter.",
        "cloud_cache.ttl": "Lebensdauer (Claude):",
        "cloud_cache.ttl.5m": "5 Minuten (Vorgabe; jeder Treffer erneuert sie kostenlos)",
        "cloud_cache.ttl.1h": "1 Stunde (ein Schreibvorgang kostet das Doppelte des Eingabepreises)",
        "cloud_cache.limit": "Weiche Obergrenze je Gespräch (0 = kein Hinweis):",
        "cloud_cache.apply": "Übernehmen",
        "cloud_cache.failed": "Der Cloud-Cache konnte nicht gelesen werden: {reason}",
        "cloud_cache.not_saved": "Nicht gespeichert: {reason}",
        "cloud_cache.way.breakpoints": "Anthropics Prompt-Caching: Cache-Punkte auf dem Systemtext und auf dem "
                                       "neuesten Block des Gesprächs.",
        "cloud_cache.way.openrouter_breakpoints": "OpenRouter gibt Anthropics Cache-Punkte weiter: den Systemtext und "
                                                  "den neuesten Block des Gesprächs.",
        "cloud_cache.way.automatic": "Der Anbieter cacht einen gleichbleibenden Anfang der Anfrage von selbst; dafür "
                                     "geht nichts hinaus, und die Lebensdauer bestimmt der Anbieter.",
        "cloud_cache.way.none": "Kein Cloud-Cache: eine lokale Engine hält ihren eigenen Prompt-Cache.",
        "cloud_cache.minimum": "Dieses Modell cacht einen Prompt ab {tokens} Token.",
        "cloud_cache.ttl_not_here": "Die Lebensdauer oben gilt nur für Claude; dieser Anbieter setzt seine eigene.",
        "cloud_cache.totals": "Dieses Gespräch: {calls} Aufruf/Aufrufe, {read} Token aus dem Cache gelesen, "
                              "{written} geschrieben, {uncached} nicht gecacht; Cache-Schreibvorgänge etwa {cost} USD.",
        "cloud_cache.unpriced": "{tokens} geschriebene Token haben keinen Preis in der Tabelle und sind nicht in USD "
                                "gezählt.",
        "cloud_cache.cap_off": "Die weiche Obergrenze ist aus.",
        "cloud_cache.cap_reached": "Die weiche Obergrenze von {limit} USD ist erreicht; das Caching geht weiter.",
    },
}

FORMAL = {
    "de": {
        "context.source.setting": "Ihrer Einstellung ai_context_window",
    },
}
