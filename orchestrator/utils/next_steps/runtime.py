#!/usr/bin/env python3
"""
Next steps of Ditto's local model program and its models (Ditto > Advanced > Local Runtime..., Local Models...;
chain ditto-runtime refines them). Seeded by chain a0 from the wave-6 audit (items 9, 16-20, 47, 51, 65).
"""

from orchestrator.utils.next_steps import Param, Step


def _tiers():
    """The model tiers of the pinned catalogue (configs/llama_runtime.yml)."""
    from orchestrator.runtime.catalog import load_catalog
    return [m.tier for m in load_catalog().models]


_RT = "act_ditto_runtime_open"
_MODE = Param("choice", ("bare-metal", "docker"), display="steps.value.mode")
_FILE = {"file": Param("path")}

MOUSE_LABELS = {
    "btn_ditto_runtime_build": (_RT, "ditto_runtime.build.button"),
    "btn_ditto_runtime_prebuilt": (_RT, "ditto_runtime.prebuilt.button"),
    "btn_ditto_runtime_start": (_RT, "ditto_runtime.server.start"),
    "btn_ditto_runtime_stop": (_RT, "ditto_runtime.server.stop"),
    "btn_ditto_runtime_import": (_RT, "ditto_runtime.import.button"),
    "btn_ditto_runtime_export": (_RT, "ditto_runtime.export.button"),
    "btn_ditto_runtime_import_source": (_RT, "ditto_runtime.import_source.button"),
    "btn_ditto_runtime_switch": (_RT, ("runtime.mode.switch", "runtime.mode.install")),
    "cb_ditto_runtime_mode": (_RT, "runtime.mode.label"),
    "cb_ditto_runtime_placement": (_RT, "ditto_runtime.options.placement"),
    "cb_ditto_runtime_backend": (_RT, "ditto_runtime.options.backend"),
    "cb_ditto_runtime_tier": (_RT, "ditto_runtime.server.tier"),
    "btn_ditto_models_download": ("act_ditto_models_open", "ditto_models.download"),
}

STEPS = (
    Step("runtime.install", cli="ditto runtime install", cli_args=("--yes",), tool="build_local_runtime",
         window="ditto_runtime", target="btn_ditto_runtime_build", runnable=True),
    # not runnable: a licence is read in its window, never accepted with --yes from a one-line question
    Step("runtime.prebuilt", cli="ditto runtime install --prebuilt", cli_args=("--prebuilt", "--yes"),
         person_only="licence", window="ditto_runtime", target="btn_ditto_runtime_prebuilt"),
    Step("runtime.start", cli="ditto runtime start", tool="start_local_runtime", window="ditto_runtime",
         target="btn_ditto_runtime_start", runnable=True),
    Step("runtime.stop", cli="ditto runtime stop", tool="stop_local_runtime", window="ditto_runtime",
         target="btn_ditto_runtime_stop", runnable=True),
    # not runnable: `ensure --force` needs a terminal and deletes the old kind after its own list (the window's switch
    # shows that list)
    Step("runtime.switch", cli="ditto runtime ensure --force", cli_args=("--mode", "{mode}", "--force", "--yes"),
         params={"mode": _MODE}, person_only="permission", window="ditto_runtime", target="btn_ditto_runtime_switch"),
    # --no-model: the step installs the program only; `--yes` alone would also download the recommended model
    Step("runtime.ensure", cli="ditto runtime ensure", cli_args=("--mode", "{mode}", "--no-model", "--yes"),
         params={"mode": _MODE}, person_only="permission", window="ditto_runtime", target="btn_ditto_runtime_switch",
         runnable=True),
    Step("runtime.install_native", cli="ditto runtime install --placement",
         cli_args=("--placement", "native", "--yes"), person_only="permission", window="ditto_runtime",
         target="cb_ditto_runtime_placement", runnable=True),
    Step("runtime.install_container", cli="ditto runtime install --placement",
         cli_args=("--placement", "container", "--yes"), person_only="permission", window="ditto_runtime",
         target="cb_ditto_runtime_placement", runnable=True),
    Step("runtime.placement", cli="ditto runtime install --placement", cli_args=("--placement", "{placement}"),
         params={"placement": Param("choice", ("auto", "native", "container"))}, person_only="permission",
         window="ditto_runtime", target="cb_ditto_runtime_placement"),
    Step("runtime.backend", cli="ditto runtime install --backend", cli_args=("--backend", "{backend}"),
         params={"backend": Param("choice", ("auto", "cpu", "vulkan", "cuda", "rocm", "metal"))},
         person_only="permission", window="ditto_runtime", target="cb_ditto_runtime_backend"),
    Step("runtime.model", cli="ditto runtime start --model", cli_args=("--model", "{tier}"),
         params={"tier": Param("choice", _tiers)}, tool="start_local_runtime", window="ditto_runtime",
         target="cb_ditto_runtime_tier"),
    Step("runtime.import_source", cli="ditto runtime import-source", cli_args=("{file}",), params=_FILE,
         person_only="permission",
         window="ditto_runtime", target="btn_ditto_runtime_import_source"),
    Step("runtime.export", cli="ditto runtime export", cli_args=("{file}",), params=_FILE, person_only="permission",
         window="ditto_runtime",
         target="btn_ditto_runtime_export"),
    Step("runtime.import", cli="ditto runtime import", cli_args=("{file}",), params=_FILE, person_only="permission",
         window="ditto_runtime",
         target="btn_ditto_runtime_import"),
    Step("runtime.show", cli="ditto runtime status", tool="get_status", window="ditto_runtime",
         target="act_ditto_runtime_open", runnable=True),
    # the build tools llama.cpp needs (cmake, a C++ compiler): the package manager is an administrator's work
    Step("runtime.install_tools", person_only="admin"),
    Step("models.download", cli="ditto models download", cli_args=("{tier}", "--yes"),
         params={"tier": Param("choice", _tiers)}, tool="build_local_runtime", window="ditto_models",
         target="btn_ditto_models_download", runnable=True),
    Step("models.show", cli="ditto models list", tool="get_status", window="ditto_models",
         target="act_ditto_models_open", runnable=True),
)

TEXTS = {
    "en": {
        "step.runtime.install": "Build Ditto's local model program",
        "step.runtime.install.what": "Builds llama-server, Ditto's local model program, from the pinned llama.cpp "
                                     "source for this computer (a few minutes; the source, about 215 MB, is fetched "
                                     "when it is not there yet).",
        "step.runtime.install.why": "Ditto's local model program is not installed yet.",
        "step.runtime.install.why.rebuild": "The build no longer fits this computer and needs to be made again.",
        "step.runtime.prebuilt": "Install the official prebuilt llama-server archive instead",
        "step.runtime.prebuilt.what": "Downloads and installs the official prebuilt llama-server archive (pinned "
                                      "and checked) instead of building it here.",
        "step.runtime.prebuilt.why": "Building here is not possible or not wanted.",
        "step.runtime.start": "Start Ditto's local model program",
        "step.runtime.start.what": "Starts llama-server with the model chosen for this computer.",
        "step.runtime.start.why": "Ditto's local model program is not running.",
        "step.runtime.stop": "Stop Ditto's local model program",
        "step.runtime.stop.what": "Stops llama-server; Ditto cannot answer with the local model until it starts "
                                  "again.",
        "step.runtime.stop.why": "It must not run while this is changed.",
        "step.runtime.switch": "Switch where Ditto's local model program runs",
        "step.runtime.switch.what": "Installs Ditto's local model program {mode} and removes the other kind once "
                                    "the new one works.",
        "step.runtime.switch.why": "The current kind does not work on this computer.",
        "step.runtime.ensure": "Install Ditto's local model program in another way",
        "step.runtime.ensure.what": "Installs Ditto's local model program {mode} and checks that it works.",
        "step.runtime.ensure.why": "It is not installed in that kind yet.",
        "step.runtime.install_native": "Build Ditto's local model program on this computer itself",
        "step.runtime.install_native.what": "Builds llama-server directly on this computer (no Docker).",
        "step.runtime.install_native.why": "Building in a container is not possible here.",
        "step.runtime.install_container": "Build Ditto's local model program in a container",
        "step.runtime.install_container.what": "Builds llama-server inside a Docker container, so this computer "
                                               "needs no build tools.",
        "step.runtime.install_container.why": "This computer lacks the build tools.",
        "step.runtime.placement": "Choose where Ditto's local model program is built",
        "step.runtime.backend": "Choose the GPU backend of Ditto's local model program",
        "step.runtime.model": "Choose the model of Ditto's local model program",
        "step.runtime.import_source": "Import the llama.cpp source from a file",
        "step.runtime.export": "Export the build for another computer",
        "step.runtime.import": "Import a build made on another computer",
        "step.runtime.show": "Look at Ditto's local model program",
        "step.runtime.show.what": "Shows whether llama-server runs, the build in use and its problems.",
        "step.runtime.install_tools": "Install the build tools for llama.cpp (cmake and a C++ compiler)",
        "step.models.download": "Download Ditto's model",
        "step.models.download.what": "Downloads the pinned model of the tier {tier} (several GB) and checks its "
                                     "checksum; its licence, listed in {path:act_ditto_models_open}, applies to you.",
        "step.models.download.why": "The model is not downloaded yet.",
        "step.models.show": "Look at Ditto's local models",
        "step.models.show.what": "Lists the model tiers with file, size, licence and whether each is downloaded.",
    },
    "de": {
        "step.runtime.install": "Dittos lokales Modellprogramm bauen",
        "step.runtime.install.what": "Baut llama-server, Dittos lokales Modellprogramm, aus dem festgelegten "
                                     "llama.cpp-Quellcode für diesen Computer (einige Minuten; der Quellcode, etwa "
                                     "215 MB, wird geholt, wenn er noch fehlt).",
        "step.runtime.install.why": "Dittos lokales Modellprogramm ist noch nicht installiert.",
        "step.runtime.install.why.rebuild": "Der Build passt nicht mehr zu diesem Computer und muss neu gebaut "
                                            "werden.",
        "step.runtime.prebuilt": "Stattdessen das offizielle fertige llama-server-Archiv installieren",
        "step.runtime.prebuilt.what": "Lädt das offizielle fertige llama-server-Archiv (festgelegt und geprüft) und "
                                      "installiert es, statt hier zu bauen.",
        "step.runtime.prebuilt.why": "Hier zu bauen geht nicht oder ist nicht gewünscht.",
        "step.runtime.start": "Dittos lokales Modellprogramm starten",
        "step.runtime.start.what": "Startet llama-server mit dem Modell, das für diesen Computer gewählt ist.",
        "step.runtime.start.why": "Dittos lokales Modellprogramm läuft nicht.",
        "step.runtime.stop": "Dittos lokales Modellprogramm stoppen",
        "step.runtime.stop.what": "Stoppt llama-server; Ditto kann mit dem lokalen Modell erst wieder antworten, "
                                  "wenn es neu startet.",
        "step.runtime.stop.why": "Es darf nicht laufen, während das geändert wird.",
        "step.runtime.switch": "Wechseln, wo Dittos lokales Modellprogramm läuft",
        "step.runtime.switch.what": "Installiert Dittos lokales Modellprogramm {mode} und entfernt die andere Art, "
                                    "sobald die neue funktioniert.",
        "step.runtime.switch.why": "Die jetzige Art funktioniert auf diesem Computer nicht.",
        "step.runtime.ensure": "Dittos lokales Modellprogramm auf andere Art installieren",
        "step.runtime.ensure.what": "Installiert Dittos lokales Modellprogramm {mode} und prüft, ob es läuft.",
        "step.runtime.ensure.why": "Es ist in dieser Art noch nicht installiert.",
        "step.runtime.install_native": "Dittos lokales Modellprogramm direkt auf diesem Computer bauen",
        "step.runtime.install_native.what": "Baut llama-server direkt auf diesem Computer (ohne Docker).",
        "step.runtime.install_native.why": "In einem Container zu bauen geht hier nicht.",
        "step.runtime.install_container": "Dittos lokales Modellprogramm in einem Container bauen",
        "step.runtime.install_container.what": "Baut llama-server in einem Docker-Container, damit dieser Computer "
                                               "keine Build-Werkzeuge braucht.",
        "step.runtime.install_container.why": "Diesem Computer fehlen die Build-Werkzeuge.",
        "step.runtime.placement": "Wählen, wo Dittos lokales Modellprogramm gebaut wird",
        "step.runtime.backend": "Das GPU-Backend von Dittos lokalem Modellprogramm wählen",
        "step.runtime.model": "Das Modell von Dittos lokalem Modellprogramm wählen",
        "step.runtime.import_source": "Den llama.cpp-Quellcode aus einer Datei importieren",
        "step.runtime.export": "Den Build für einen anderen Computer exportieren",
        "step.runtime.import": "Einen Build von einem anderen Computer importieren",
        "step.runtime.show": "Dittos lokales Modellprogramm ansehen",
        "step.runtime.show.what": "Zeigt, ob llama-server läuft, welcher Build benutzt wird und seine Probleme.",
        "step.runtime.install_tools": "Die Build-Werkzeuge für llama.cpp installieren (cmake und einen C++-Compiler)",
        "step.models.download": "Dittos Modell herunterladen",
        "step.models.download.what": "Lädt das festgelegte Modell der Stufe {tier} herunter (mehrere GB) und prüft "
                                     "seine Prüfsumme; seine Lizenz, aufgeführt unter {path:act_ditto_models_open}, "
                                     "gilt für dich.",
        "step.models.download.why": "Das Modell ist noch nicht heruntergeladen.",
        "step.models.show": "Dittos lokale Modelle ansehen",
        "step.models.show.what": "Listet die Modellstufen mit Datei, Größe, Lizenz und ob jede heruntergeladen ist.",
    },
}

# The "Sie" form of the German texts above that address the person (Q1); only the texts that differ.
FORMAL = {
    "de": {
        "step.models.download.what": "Lädt das festgelegte Modell der Stufe {tier} herunter (mehrere GB) und prüft "
                                     "seine Prüfsumme; seine Lizenz, aufgeführt unter "
                                     "{path:act_ditto_models_open}, gilt für Sie.",
    },
}
