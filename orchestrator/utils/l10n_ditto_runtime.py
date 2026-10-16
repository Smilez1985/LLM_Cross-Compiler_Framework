#!/usr/bin/env python3
"""
Texts of Ditto > Local Runtime... and of the build options, the "check and repair" and the model offer of Ditto's
local server box (W1': every `llm-cli ditto runtime ...` command by mouse), English and German. LocalizationManager
merges them (localization.TEXT_MODULES); a key that exists already is refused there.

Where the CLI asks a question, the English text uses the CLI's words. Every key has the same {placeholders} in both
languages (tests/test_gui_ditto_runtime.py). German terms as in l10n_runtime: "direkt" (bare metal), "Papierkorb"
(trash). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the menu and the window
        "ditto_runtime.menu": "Ditto",
        "ditto_runtime.menu.open": "Local Runtime...",
        "ditto_runtime.title": "Ditto's Local Runtime",
        "ditto_runtime.close": "Close",
        "ditto_runtime.cancel": "Cancel",
        "ditto_runtime.cancelling": "Cancelling… the step that runs now finishes first.",
        "ditto_runtime.working": "Working…",
        "ditto_runtime.busy": "Another task of Ditto's local runtime is running. Wait until it is done (or cancel "
                              "it), then try again.",
        "ditto_runtime.failed": "It did not work: {reason}",
        "ditto_runtime.automatic": "automatic",
        "ditto_runtime.progress.download": "{name}: {done} of {total}",

        # status (`ditto runtime status`)
        "ditto_runtime.status.title": "Status",
        "ditto_runtime.status.refresh": "Check again",
        "ditto_runtime.status.checking": "Checking the hardware and the builds …",
        "ditto_runtime.status.failed": "The status could not be read: {reason}",
        "ditto_runtime.row.llama": "llama.cpp",
        "ditto_runtime.row.hardware": "Hardware",
        "ditto_runtime.row.mode": "Mode",
        "ditto_runtime.row.in_use": "In use",
        "ditto_runtime.row.bare_metal": "Bare metal",
        "ditto_runtime.row.built": "Built",
        "ditto_runtime.row.verified": "Verified",
        "ditto_runtime.row.note": "Note",
        "ditto_runtime.row.problem": "Problem",
        "ditto_runtime.row.rebuild": "Rebuild",
        "ditto_runtime.row.plan": "Self-built plan",
        "ditto_runtime.row.prebuilt": "Prebuilt option",
        "ditto_runtime.row.server": "Server",
        "ditto_runtime.row.recommended": "Recommended model",
        "ditto_runtime.row.chosen": "Chosen model",
        "ditto_runtime.row.log": "Log",
        "ditto_runtime.status.mode.bare-metal": "bare metal (switch below: Ditto's local server)",
        "ditto_runtime.status.mode.docker": "Docker (switch below: Ditto's local server)",
        "ditto_runtime.status.image": "image {tag} ({id}), build {build} ({backend}, {made_by}, on {base}), verified "
                                      "in a container at {at}",
        "ditto_runtime.status.nothing": "nothing yet (Install… or Build llama-server… below)",
        "ditto_runtime.status.build": "{origin}: build {build} ({backend}), llama.cpp {llama} commit {commit}",
        "ditto_runtime.status.prebuilt": "{origin}: {asset} ({backend})",
        "ditto_runtime.status.fallback": ", CPU fallback",
        "ditto_runtime.origin.self-built": "self-built",
        "ditto_runtime.origin.imported": "self-built on another machine ({on}), imported",
        "ditto_runtime.origin.prebuilt": "prebuilt, not self-built",
        "ditto_runtime.status.placement": "{placement} build",
        "ditto_runtime.status.seconds": "{n} s",
        "ditto_runtime.status.on": "on {on}",
        "ditto_runtime.status.verified": "{at}; devices: {devices}",
        "ditto_runtime.status.cpu_only": "CPU only",
        "ditto_runtime.status.plan.gpu": "CPU build, then {backend}: {reason}",
        "ditto_runtime.status.plan.only": "{backend}: {reason}",
        "ditto_runtime.status.prebuilt_option": "{asset} (Install the official prebuilt archive…, not self-built): "
                                                "{reason}",
        "ditto_runtime.status.server.ready": "ready {url} ({where}, model {model}, ctx {ctx})",
        "ditto_runtime.status.server.starting": "starting / not answering {url} ({where}, model {model}, ctx {ctx})",
        "ditto_runtime.status.server.stopped": "stopped",
        "ditto_runtime.status.container": "container {name}",
        "ditto_runtime.status.pid": "pid {pid}",
        "ditto_runtime.status.recommended": "{file} - {why}",
        "ditto_runtime.status.chosen": "{tier} (Start with 'Recommended for this computer' forgets it)",

        # Ditto's local server box: ensure's options
        "ditto_runtime.mode.choice.auto": "Automatic (bare metal when possible)",
        "ditto_runtime.mode.name.auto": "bare metal when it can be built here, otherwise in Docker",
        "ditto_runtime.mode.repair": "Check and repair…",
        "ditto_runtime.mode.repair_question": "Ditto's server is checked {mode}. When it cannot be used (for example "
                                              "an old llama.cpp build after an update, or files that no longer match "
                                              "their manifest), it is installed again {mode}: built from the pinned "
                                              "llama.cpp source with the build options below (a few minutes; when "
                                              "the source, about 215 MB, is not there yet, you are asked before it is "
                                              "fetched). When it works, nothing is changed.\n\nContinue?",
        "ditto_runtime.mode.repair_auto_question": "Ditto's server is checked automatically: what is installed now "
                                                   "({mode}) stays when it can be used. When nothing usable is "
                                                   "installed any more (for example an old llama.cpp build after an "
                                                   "update, or a Docker image that is gone), Ditto's server is "
                                                   "installed again: bare metal when it can be built here, otherwise "
                                                   "in Docker, with the build options below (a few minutes; when the "
                                                   "source, about 215 MB, is not there yet, you are asked before it is "
                                                   "fetched). If that is not the mode installed now, every object of "
                                                   "the old mode is listed afterwards and deleted only after your one "
                                                   "yes.\n\nContinue?",
        "ditto_runtime.mode.failed": "It did not work: {reason}",
        "ditto_runtime.mode.unusable": "It cannot be used now: {problems}",
        "ditto_runtime.options.backend": "GPU backend:",
        "ditto_runtime.options.backend.tip": "The GPU backend built after the CPU build (automatic: the build recipe "
                                             "chooses; CPU only: no GPU build).",
        "ditto_runtime.options.placement": "Build:",
        "ditto_runtime.options.placement.tip": "Where the build runs: with this computer's compiler, or in the "
                                               "framework's builder container (Docker, no network during the build).",
        "ditto_runtime.options.jobs": "Compile jobs:",
        "ditto_runtime.options.jobs.tip": "Parallel compile jobs (automatic: from the CPUs and the free memory).",
        "ditto_runtime.backend.auto": "automatic",
        "ditto_runtime.backend.cpu": "CPU only",
        "ditto_runtime.backend.vulkan": "Vulkan",
        "ditto_runtime.backend.cuda": "CUDA (NVIDIA)",
        "ditto_runtime.backend.rocm": "ROCm (AMD)",
        "ditto_runtime.backend.metal": "Metal (Apple)",
        "ditto_runtime.placement.auto": "automatic",
        "ditto_runtime.placement.native": "on this computer",
        "ditto_runtime.placement.container": "in the builder container",
        "ditto_runtime.offer_model": "Offer Ditto's model afterwards (its size and licence are shown first)",
        "ditto_runtime.offer_model.tip": "After the install, the model of the chosen or recommended tier is offered "
                                         "for download when it is not there yet. Untick it to skip the offer.",
        "ditto_runtime.details": "Status and more…",
        "ditto_runtime.details.tip": "Opens Ditto > Local Runtime...: whether the server runs, the build in use and "
                                     "its problems, Start and Stop, builds and the source for offline computers.",

        # the model offer after ensure
        "ditto_runtime.model.offer": "Model for Ditto: {repo} / {file}, {size}, licence {licence} ({why}).",
        "ditto_runtime.model.question": "Download {size} into {folder}? The licence ({licence}) is yours to accept.",
        "ditto_runtime.model.unpinned": "It has no pinned size and sha256, so it is not downloaded.",
        "ditto_runtime.model.there": "Model: {file} is there; nothing to download (the models folder is shared by "
                                     "both modes).",
        "ditto_runtime.model.unknown": "The model to offer could not be found out: {reason}",
        "ditto_runtime.model.downloading": "Downloading the model … (checked by size and sha256)",
        "ditto_runtime.model.saved": "Verified and saved: {path}",
        "ditto_runtime.model.failed": "The model was not downloaded: {reason}",

        # build (`ditto runtime install`)
        "ditto_runtime.build.title": "Build llama-server",
        "ditto_runtime.build.intro": "Build llama-server again on this computer with the build options above (the "
                                     "CPU build first, then the GPU build), for example after an update or to add a "
                                     "GPU build. Or use the official prebuilt archive instead (not self-built).",
        "ditto_runtime.build.button": "Build llama-server…",
        "ditto_runtime.build.tip": "Plans the build first and shows the plan; a build that is there already is reused.",
        "ditto_runtime.build.question": "Build now? The CPU build takes a few minutes, a GPU build longer.",
        "ditto_runtime.build.working": "Building … this can take minutes; the window can stay open.",
        "ditto_runtime.build.nothing": "Nothing was built.",
        "ditto_runtime.build.ready": "Self-built llama-server ready: {backend} build {build} (verified on this "
                                     "machine)",
        "ditto_runtime.build.fallback": "CPU fallback: build {build}",
        "ditto_runtime.build.older": "{n} older build(s) kept; the framework deletes none by itself. 'Remove older "
                                     "builds…' below lists them for you to delete.",
        "ditto_runtime.build.running": "The running server keeps running; the next start uses the new build (Start "
                                       "below).",
        "ditto_runtime.build.docker": "Ditto's server runs in Docker mode; this bare-metal build is used after a "
                                      "switch to bare metal (above).",
        "ditto_runtime.source.fetch_question": "The pinned llama.cpp source ({tag}, commit {commit}, about 215 MB) "
                                               "is not cached yet. Fetch it now?",
        "ditto_runtime.prebuilt.button": "Install the official prebuilt archive…",
        "ditto_runtime.prebuilt.tip": "Downloads the official llama.cpp archive for this computer (size and sha256 "
                                      "checked) instead of building it; it is always shown as prebuilt, not "
                                      "self-built.",
        "ditto_runtime.prebuilt.force": "Download it again even if it is installed",
        "ditto_runtime.prebuilt.what": "llama.cpp {build} - {asset} (prebuilt, not self-built): {reason}",
        "ditto_runtime.prebuilt.use_question": "This official archive is already installed in {root}. Use it for "
                                               "Ditto's server (prebuilt, not self-built)?",
        "ditto_runtime.prebuilt.download_question": "Download the official archive ({size}) into {root}?",
        "ditto_runtime.prebuilt.done": "Installed {asset} in {path} (prebuilt, not self-built)",

        # server (`ditto runtime start` / `stop`)
        "ditto_runtime.server.title": "Server",
        "ditto_runtime.server.tier": "Model:",
        "ditto_runtime.server.ctx": "Context length (tokens):",
        "ditto_runtime.server.ctx.tip": "Automatic: from the free memory, at most 32768. Otherwise at least 256.",
        "ditto_runtime.server.ctx.too_small": "The context length is at least {n} tokens (or automatic).",
        "ditto_runtime.server.idle": "Unload the model when idle after:",
        "ditto_runtime.server.idle.default": "the default",
        "ditto_runtime.server.idle.tip": "Seconds without a request before the model is unloaded; 0 = never.",
        "ditto_runtime.server.start": "Start",
        "ditto_runtime.server.stop": "Stop",
        "ditto_runtime.server.starting": "Starting llama-server …",
        "ditto_runtime.server.started": "Running: {url} model {model} ({where}, ctx {ctx})\n\nDitto uses it as the "
                                        "'local' AI provider; the API key changes with every start and is readable "
                                        "only by you.",
        "ditto_runtime.server.stopped": "Stopped ({n} process(es)).",
        "ditto_runtime.server.not_running": "Not running.",
        "ditto_runtime.tier.keep": "As before (the model chosen earlier, else the recommendation)",
        "ditto_runtime.tier.auto": "Recommended for this computer (forgets the earlier choice)",
        "ditto_runtime.tier.model": "{tier}: {file} ({size}, licence {licence}); remembered for later starts",
        "ditto_runtime.tier.chosen": "Chosen earlier: {tier}. 'Recommended for this computer' at the next start "
                                     "forgets it.",
        "ditto_runtime.tier.none": "No model chosen earlier: the recommendation for this computer is used.",

        # builds and source (`import`, `export`, `prune`, `export-source`, `import-source`)
        "ditto_runtime.files.title": "Builds and source",
        "ditto_runtime.archive.filter": "Build archives (*.tar.gz *.tgz)",
        "ditto_runtime.bundle.filter": "llama.cpp source bundles (*.bundle);;All files (*)",
        "ditto_runtime.import.button": "Import a build…",
        "ditto_runtime.import.tip": "A llama-server build made on another computer of the same kind (Export the "
                                    "build… there).",
        "ditto_runtime.import.choose": "Import a llama-server build",
        "ditto_runtime.import.working": "Checking the build …",
        "ditto_runtime.import.question": "Build {build} of llama.cpp {tag} (commit {commit}), built on {os}/{arch} at "
                                         "{at}; programs: {files}\n\nIts files match its manifest and the pin. Run its "
                                         "programs here to verify it? Import only builds you made yourself.",
        "ditto_runtime.import.done": "Imported and verified: {what}",
        "ditto_runtime.export.button": "Export the build…",
        "ditto_runtime.export.tip": "Writes the self-built build in use to a .tar.gz for another computer of the same "
                                    "kind.",
        "ditto_runtime.export.choose": "Export the llama-server build",
        "ditto_runtime.export.done": "Wrote {path}. On the other computer: Ditto > Local Runtime... > Import a "
                                     "build...",
        "ditto_runtime.prune.button": "Remove older builds…",
        "ditto_runtime.prune.tip": "Lists the older builds (never the active build, the CPU fallback or the running "
                                   "one); nothing is ticked.",
        "ditto_runtime.prune.title": "Older builds of Ditto's local server",
        "ditto_runtime.prune.intro": "Tick the older builds you want to delete. Nothing is ticked; the active build, "
                                     "the CPU fallback and the running one are never listed.",
        "ditto_runtime.prune.none": "No old runtime builds: the active build, the CPU fallback and the running one are "
                                    "never listed.",
        "ditto_runtime.prune.nothing": "Nothing was deleted.",
        "ditto_runtime.prune.listing": "Measuring the older builds …",
        "ditto_runtime.prune.deleting": "Deleting the chosen builds …",
        "ditto_runtime.source.intro": "For a computer without network: export the pinned llama.cpp source here (from "
                                      "the pinned repository or a mirror; the commit is checked the same way) and "
                                      "import it there.",
        "ditto_runtime.source.mirror": "Mirror (optional):",
        "ditto_runtime.source.mirror.placeholder": "empty: the pinned repository",
        "ditto_runtime.source.working": "Preparing the llama.cpp source …",
        "ditto_runtime.export_source.button": "Export the source…",
        "ditto_runtime.export_source.choose": "Export the llama.cpp source",
        "ditto_runtime.export_source.done": "Wrote {path} ({size}, llama.cpp {tag}, commit {commit}) and "
                                            "{name}.json.\n\nOn the computer without network: Ditto > Local "
                                            "Runtime... > Import the source...",
        "ditto_runtime.import_source.button": "Import the source…",
        "ditto_runtime.import_source.choose": "Import the llama.cpp source",
        "ditto_runtime.import_source.done": "Verified llama.cpp {tag} (commit {commit}) in {path}",
    },
    "de": {
        # das Menü und das Fenster
        "ditto_runtime.menu": "Ditto",
        "ditto_runtime.menu.open": "Lokale Laufzeit...",
        "ditto_runtime.title": "Dittos lokale Laufzeit",
        "ditto_runtime.close": "Schließen",
        "ditto_runtime.cancel": "Abbrechen",
        "ditto_runtime.cancelling": "Wird abgebrochen … der Schritt, der gerade läuft, wird noch fertig.",
        "ditto_runtime.working": "Läuft …",
        "ditto_runtime.busy": "Eine andere Aufgabe von Dittos lokaler Laufzeit läuft gerade. Warte, bis sie fertig "
                              "ist (oder brich sie ab), und versuche es dann noch einmal.",
        "ditto_runtime.failed": "Das hat nicht geklappt: {reason}",
        "ditto_runtime.automatic": "automatisch",
        "ditto_runtime.progress.download": "{name}: {done} von {total}",

        # Status (`ditto runtime status`)
        "ditto_runtime.status.title": "Status",
        "ditto_runtime.status.refresh": "Noch einmal prüfen",
        "ditto_runtime.status.checking": "Hardware und Builds werden geprüft …",
        "ditto_runtime.status.failed": "Der Status ließ sich nicht lesen: {reason}",
        "ditto_runtime.row.llama": "llama.cpp",
        "ditto_runtime.row.hardware": "Hardware",
        "ditto_runtime.row.mode": "Betrieb",
        "ditto_runtime.row.in_use": "In Gebrauch",
        "ditto_runtime.row.bare_metal": "Direkt",
        "ditto_runtime.row.built": "Gebaut",
        "ditto_runtime.row.verified": "Geprüft",
        "ditto_runtime.row.note": "Hinweis",
        "ditto_runtime.row.problem": "Problem",
        "ditto_runtime.row.rebuild": "Neu bauen",
        "ditto_runtime.row.plan": "Plan für den eigenen Build",
        "ditto_runtime.row.prebuilt": "Fertiges Archiv",
        "ditto_runtime.row.server": "Server",
        "ditto_runtime.row.recommended": "Empfohlenes Modell",
        "ditto_runtime.row.chosen": "Gewähltes Modell",
        "ditto_runtime.row.log": "Protokoll",
        "ditto_runtime.status.mode.bare-metal": "direkt (wechseln unten: Dittos lokaler Server)",
        "ditto_runtime.status.mode.docker": "Docker (wechseln unten: Dittos lokaler Server)",
        "ditto_runtime.status.image": "Image {tag} ({id}), Build {build} ({backend}, {made_by}, auf {base}), in einem "
                                      "Container geprüft am {at}",
        "ditto_runtime.status.nothing": "noch nichts (Installieren… oder llama-server bauen… unten)",
        "ditto_runtime.status.build": "{origin}: Build {build} ({backend}), llama.cpp {llama} Commit {commit}",
        "ditto_runtime.status.prebuilt": "{origin}: {asset} ({backend})",
        "ditto_runtime.status.fallback": ", CPU-Ersatz",
        "ditto_runtime.origin.self-built": "selbst gebaut",
        "ditto_runtime.origin.imported": "selbst gebaut auf einem anderen Rechner ({on}), importiert",
        "ditto_runtime.origin.prebuilt": "fertig heruntergeladen, nicht selbst gebaut",
        "ditto_runtime.status.placement": "Build {placement}",
        "ditto_runtime.status.seconds": "{n} s",
        "ditto_runtime.status.on": "auf {on}",
        "ditto_runtime.status.verified": "{at}; Geräte: {devices}",
        "ditto_runtime.status.cpu_only": "nur CPU",
        "ditto_runtime.status.plan.gpu": "CPU-Build, dann {backend}: {reason}",
        "ditto_runtime.status.plan.only": "{backend}: {reason}",
        "ditto_runtime.status.prebuilt_option": "{asset} (Offizielles fertiges Archiv installieren…, nicht selbst "
                                                "gebaut): {reason}",
        "ditto_runtime.status.server.ready": "bereit {url} ({where}, Modell {model}, Kontext {ctx})",
        "ditto_runtime.status.server.starting": "startet / antwortet nicht {url} ({where}, Modell {model}, Kontext "
                                                "{ctx})",
        "ditto_runtime.status.server.stopped": "gestoppt",
        "ditto_runtime.status.container": "Container {name}",
        "ditto_runtime.status.pid": "PID {pid}",
        "ditto_runtime.status.recommended": "{file} - {why}",
        "ditto_runtime.status.chosen": "{tier} (Start mit „Empfohlen für diesen Computer“ vergisst es)",

        # Dittos lokaler Server: die Optionen von ensure
        "ditto_runtime.mode.choice.auto": "Automatisch (direkt, wenn möglich)",
        "ditto_runtime.mode.name.auto": "direkt, wenn er hier gebaut werden kann, sonst in Docker",
        "ditto_runtime.mode.repair": "Prüfen und reparieren…",
        "ditto_runtime.mode.repair_question": "Dittos Server wird {mode} geprüft. Lässt er sich nicht nutzen (zum "
                                              "Beispiel ein alter llama.cpp-Build nach einem Update oder Dateien, die "
                                              "nicht mehr zu ihrem Manifest passen), wird er {mode} neu installiert: "
                                              "aus dem festgelegten llama.cpp-Quellcode mit den Build-Optionen unten "
                                              "gebaut (einige Minuten; fehlt der Quellcode, etwa 215 MB, wirst du "
                                              "gefragt, bevor er geholt wird). Funktioniert er, wird nichts "
                                              "geändert.\n\nWeiter?",
        "ditto_runtime.mode.repair_auto_question": "Dittos Server wird automatisch geprüft: Was jetzt installiert ist "
                                                   "({mode}), bleibt, wenn es sich nutzen lässt. Ist nichts Nutzbares "
                                                   "mehr installiert (zum Beispiel ein alter llama.cpp-Build nach "
                                                   "einem Update oder ein Docker-Image, das fehlt), wird Dittos Server "
                                                   "neu installiert: direkt, wenn er hier gebaut werden kann, sonst in "
                                                   "Docker, mit den Build-Optionen unten (einige Minuten; fehlt der "
                                                   "Quellcode, etwa 215 MB, wirst du gefragt, bevor er geholt wird). "
                                                   "Ist das nicht die Art, die jetzt installiert ist, wird danach "
                                                   "jedes Objekt der alten Art aufgelistet und erst nach deinem einen "
                                                   "Ja gelöscht.\n\nWeiter?",
        "ditto_runtime.mode.failed": "Das hat nicht geklappt: {reason}",
        "ditto_runtime.mode.unusable": "Lässt sich gerade nicht nutzen: {problems}",
        "ditto_runtime.options.backend": "GPU-Backend:",
        "ditto_runtime.options.backend.tip": "Das GPU-Backend, das nach dem CPU-Build gebaut wird (automatisch: das "
                                             "Build-Rezept wählt; nur CPU: kein GPU-Build).",
        "ditto_runtime.options.placement": "Bauen:",
        "ditto_runtime.options.placement.tip": "Wo gebaut wird: mit dem Compiler dieses Computers oder im "
                                               "Build-Container des Frameworks (Docker, ohne Netz während des Builds).",
        "ditto_runtime.options.jobs": "Compile-Jobs:",
        "ditto_runtime.options.jobs.tip": "Parallele Compile-Jobs (automatisch: aus den CPUs und dem freien "
                                          "Speicher).",
        "ditto_runtime.backend.auto": "automatisch",
        "ditto_runtime.backend.cpu": "nur CPU",
        "ditto_runtime.backend.vulkan": "Vulkan",
        "ditto_runtime.backend.cuda": "CUDA (NVIDIA)",
        "ditto_runtime.backend.rocm": "ROCm (AMD)",
        "ditto_runtime.backend.metal": "Metal (Apple)",
        "ditto_runtime.placement.auto": "automatisch",
        "ditto_runtime.placement.native": "auf diesem Computer",
        "ditto_runtime.placement.container": "im Build-Container",
        "ditto_runtime.offer_model": "Danach Dittos Modell anbieten (Größe und Lizenz werden vorher gezeigt)",
        "ditto_runtime.offer_model.tip": "Nach der Installation wird das Modell der gewählten oder empfohlenen Stufe "
                                         "zum Herunterladen angeboten, wenn es noch nicht da ist. Ohne Haken entfällt "
                                         "das Angebot.",
        "ditto_runtime.details": "Status und mehr…",
        "ditto_runtime.details.tip": "Öffnet Ditto > Lokale Laufzeit...: ob der Server läuft, der Build in Gebrauch "
                                     "und seine Probleme, Starten und Stoppen, Builds und der Quellcode für Computer "
                                     "ohne Netz.",

        # das Modell nach ensure
        "ditto_runtime.model.offer": "Modell für Ditto: {repo} / {file}, {size}, Lizenz {licence} ({why}).",
        "ditto_runtime.model.question": "{size} nach {folder} herunterladen? Die Lizenz ({licence}) nimmst du selbst "
                                        "an.",
        "ditto_runtime.model.unpinned": "Für dieses Modell sind Größe und sha256 nicht festgelegt, deshalb wird es "
                                        "nicht heruntergeladen.",
        "ditto_runtime.model.there": "Modell: {file} ist da; nichts herunterzuladen (der Modellordner gehört beiden "
                                     "Betriebsarten).",
        "ditto_runtime.model.unknown": "Welches Modell angeboten wird, ließ sich nicht feststellen: {reason}",
        "ditto_runtime.model.downloading": "Das Modell wird heruntergeladen … (geprüft über Größe und sha256)",
        "ditto_runtime.model.saved": "Geprüft und gespeichert: {path}",
        "ditto_runtime.model.failed": "Das Modell wurde nicht heruntergeladen: {reason}",

        # bauen (`ditto runtime install`)
        "ditto_runtime.build.title": "llama-server bauen",
        "ditto_runtime.build.intro": "llama-server auf diesem Computer mit den Build-Optionen oben neu bauen (zuerst "
                                     "der CPU-Build, dann der GPU-Build), zum Beispiel nach einem Update oder um einen "
                                     "GPU-Build hinzuzufügen. Oder stattdessen das offizielle fertige Archiv nutzen "
                                     "(nicht selbst gebaut).",
        "ditto_runtime.build.button": "llama-server bauen…",
        "ditto_runtime.build.tip": "Plant den Build zuerst und zeigt den Plan; ein Build, der schon da ist, wird "
                                   "wiederverwendet.",
        "ditto_runtime.build.question": "Jetzt bauen? Der CPU-Build dauert einige Minuten, ein GPU-Build länger.",
        "ditto_runtime.build.working": "Wird gebaut … das kann Minuten dauern; das Fenster kann offen bleiben.",
        "ditto_runtime.build.nothing": "Nichts wurde gebaut.",
        "ditto_runtime.build.ready": "Selbst gebauter llama-server bereit: {backend}-Build {build} (auf diesem "
                                     "Rechner geprüft)",
        "ditto_runtime.build.fallback": "CPU-Ersatz: Build {build}",
        "ditto_runtime.build.older": "{n} ältere(r) Build(s) behalten; das Framework löscht keinen von selbst. "
                                     "„Ältere Builds entfernen…“ unten listet sie zum Löschen auf.",
        "ditto_runtime.build.running": "Der laufende Server läuft weiter; der nächste Start nutzt den neuen Build "
                                       "(Starten unten).",
        "ditto_runtime.build.docker": "Dittos Server läuft im Docker-Betrieb; dieser direkte Build wird nach einem "
                                      "Wechsel auf direkt (oben) genutzt.",
        "ditto_runtime.source.fetch_question": "Der festgelegte llama.cpp-Quellcode ({tag}, Commit {commit}, etwa 215 "
                                               "MB) ist noch nicht zwischengespeichert. Jetzt holen?",
        "ditto_runtime.prebuilt.button": "Offizielles fertiges Archiv installieren…",
        "ditto_runtime.prebuilt.tip": "Lädt das offizielle llama.cpp-Archiv für diesen Computer herunter (Größe und "
                                      "sha256 geprüft), statt selbst zu bauen; es wird immer als fertig "
                                      "heruntergeladen, nicht selbst gebaut gezeigt.",
        "ditto_runtime.prebuilt.force": "Noch einmal herunterladen, auch wenn es installiert ist",
        "ditto_runtime.prebuilt.what": "llama.cpp {build} - {asset} (fertig heruntergeladen, nicht selbst gebaut): "
                                       "{reason}",
        "ditto_runtime.prebuilt.use_question": "Dieses offizielle Archiv ist schon in {root} installiert. Für Dittos "
                                               "Server nutzen (fertig heruntergeladen, nicht selbst gebaut)?",
        "ditto_runtime.prebuilt.download_question": "Das offizielle Archiv ({size}) nach {root} herunterladen?",
        "ditto_runtime.prebuilt.done": "{asset} in {path} installiert (fertig heruntergeladen, nicht selbst gebaut)",

        # Server (`ditto runtime start` / `stop`)
        "ditto_runtime.server.title": "Server",
        "ditto_runtime.server.tier": "Modell:",
        "ditto_runtime.server.ctx": "Kontextlänge (Tokens):",
        "ditto_runtime.server.ctx.tip": "Automatisch: aus dem freien Speicher, höchstens 32768. Sonst mindestens 256.",
        "ditto_runtime.server.ctx.too_small": "Die Kontextlänge ist mindestens {n} Tokens (oder automatisch).",
        "ditto_runtime.server.idle": "Modell entladen nach Leerlauf von:",
        "ditto_runtime.server.idle.default": "Voreinstellung",
        "ditto_runtime.server.idle.tip": "Sekunden ohne Anfrage, bevor das Modell entladen wird; 0 = nie.",
        "ditto_runtime.server.start": "Starten",
        "ditto_runtime.server.stop": "Stoppen",
        "ditto_runtime.server.starting": "llama-server wird gestartet …",
        "ditto_runtime.server.started": "Läuft: {url} Modell {model} ({where}, Kontext {ctx})\n\nDitto nutzt ihn als "
                                        "lokalen KI-Anbieter („local“); der API-Schlüssel wechselt bei jedem Start "
                                        "und ist nur für dich lesbar.",
        "ditto_runtime.server.stopped": "Gestoppt ({n} Prozess(e)).",
        "ditto_runtime.server.not_running": "Läuft nicht.",
        "ditto_runtime.tier.keep": "Wie bisher (das früher gewählte Modell, sonst die Empfehlung)",
        "ditto_runtime.tier.auto": "Empfohlen für diesen Computer (vergisst die frühere Wahl)",
        "ditto_runtime.tier.model": "{tier}: {file} ({size}, Lizenz {licence}); für spätere Starts gemerkt",
        "ditto_runtime.tier.chosen": "Früher gewählt: {tier}. „Empfohlen für diesen Computer“ beim nächsten Start "
                                     "vergisst es.",
        "ditto_runtime.tier.none": "Kein Modell früher gewählt: Die Empfehlung für diesen Computer gilt.",

        # Builds und Quellcode (`import`, `export`, `prune`, `export-source`, `import-source`)
        "ditto_runtime.files.title": "Builds und Quellcode",
        "ditto_runtime.archive.filter": "Build-Archive (*.tar.gz *.tgz)",
        "ditto_runtime.bundle.filter": "llama.cpp-Quellcode-Bundles (*.bundle);;Alle Dateien (*)",
        "ditto_runtime.import.button": "Build importieren…",
        "ditto_runtime.import.tip": "Ein llama-server-Build, der auf einem gleichartigen Computer gebaut wurde (dort "
                                    "mit Build exportieren…).",
        "ditto_runtime.import.choose": "Einen llama-server-Build importieren",
        "ditto_runtime.import.working": "Der Build wird geprüft …",
        "ditto_runtime.import.question": "Build {build} von llama.cpp {tag} (Commit {commit}), gebaut auf "
                                         "{os}/{arch} am {at}; Programme: {files}\n\nSeine Dateien passen zu seinem "
                                         "Manifest und zur Festlegung. Seine Programme hier zur Prüfung ausführen? "
                                         "Importiere nur Builds, die du selbst gemacht hast.",
        "ditto_runtime.import.done": "Importiert und geprüft: {what}",
        "ditto_runtime.export.button": "Build exportieren…",
        "ditto_runtime.export.tip": "Schreibt den selbst gebauten Build, der in Gebrauch ist, als .tar.gz für einen "
                                    "gleichartigen Computer.",
        "ditto_runtime.export.choose": "Den llama-server-Build exportieren",
        "ditto_runtime.export.done": "{path} geschrieben. Auf dem anderen Computer: Ditto > Lokale Laufzeit... > Build "
                                     "importieren...",
        "ditto_runtime.prune.button": "Ältere Builds entfernen…",
        "ditto_runtime.prune.tip": "Listet die älteren Builds auf (nie den aktiven Build, den CPU-Ersatz oder den "
                                   "laufenden); nichts ist angehakt.",
        "ditto_runtime.prune.title": "Ältere Builds von Dittos lokalem Server",
        "ditto_runtime.prune.intro": "Hake die älteren Builds an, die du löschen willst. Nichts ist angehakt; der "
                                     "aktive Build, der CPU-Ersatz und der laufende werden nie aufgelistet.",
        "ditto_runtime.prune.none": "Keine alten Laufzeit-Builds: Der aktive Build, der CPU-Ersatz und der laufende "
                                    "werden nie aufgelistet.",
        "ditto_runtime.prune.nothing": "Nichts wurde gelöscht.",
        "ditto_runtime.prune.listing": "Die älteren Builds werden gemessen …",
        "ditto_runtime.prune.deleting": "Die gewählten Builds werden gelöscht …",
        "ditto_runtime.source.intro": "Für einen Computer ohne Netz: den festgelegten llama.cpp-Quellcode hier "
                                      "exportieren (aus dem festgelegten Repository oder einem Spiegel; der Commit "
                                      "wird genauso geprüft) und dort importieren.",
        "ditto_runtime.source.mirror": "Spiegel (optional):",
        "ditto_runtime.source.mirror.placeholder": "leer: das festgelegte Repository",
        "ditto_runtime.source.working": "Der llama.cpp-Quellcode wird vorbereitet …",
        "ditto_runtime.export_source.button": "Quellcode exportieren…",
        "ditto_runtime.export_source.choose": "Den llama.cpp-Quellcode exportieren",
        "ditto_runtime.export_source.done": "{path} ({size}, llama.cpp {tag}, Commit {commit}) und {name}.json "
                                            "geschrieben.\n\nAuf dem Computer ohne Netz: Ditto > Lokale Laufzeit... > "
                                            "Quellcode importieren...",
        "ditto_runtime.import_source.button": "Quellcode importieren…",
        "ditto_runtime.import_source.choose": "Den llama.cpp-Quellcode importieren",
        "ditto_runtime.import_source.done": "llama.cpp {tag} (Commit {commit}) geprüft in {path}",
    },
}
