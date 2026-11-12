#!/usr/bin/env python3
"""
Next steps of builds, targets and the hardware probe (the Builder tab and its Scan / Import... menu, Build >
Targets..., Build History..., Tools > Create Hardware Module...; chain build-probe refines them). Seeded by chain a0
from the wave-6 audit (items 21-23, 57).
"""

from orchestrator.utils.next_steps import Param, Step

_SCAN = "btn_deploy_scan"

MOUSE_LABELS = {
    _SCAN: ("builder_tab", "main.builder.scan"),
    "act_deploy_scan_import": (_SCAN, "main.builder.scan.import"),
    "act_deploy_scan_remote": (_SCAN, "main.builder.scan.remote"),
    "act_deploy_scan_local": (_SCAN, "main.builder.scan.local"),
    "act_deploy_scan_script": (_SCAN, "deploy.scan.script"),
    "act_deploy_scan_save": (_SCAN, "deploy.scan.save"),
    "btn_builder_check_target": ("builder_tab", "bld.builder.check"),
    "btn_builder_targets": ("builder_tab", "bld.builder.targets"),
    "btn_builder_start": ("builder_tab", "bld.builder.start"),
    "btn_builder_preflight": ("builder_tab", "bld.preflight.button"),
    "btn_f16_delete": ("act_build_f16", "bld.f16.delete"),
    # the Models tab and Build > Benchmark... (gui/models_tab.py, gui/benchmark_window.py)
    "btn_models_delete": ("models_tab", "models.delete"),
    "btn_models_bench": ("models_tab", "models.bench"),
    # the portable model archives (gui/model_archive_window.py, Core/model_archive.py)
    "btn_models_archives": ("models_tab", "models.archive.button"),
    "btn_archive_write": ("btn_models_archives", "models.archive.write"),
    "btn_archive_take": ("btn_models_archives", "models.archive.take"),
    "btn_archive_delete": ("btn_models_archives", "models.archive.delete"),
    "btn_bench_run": ("act_build_benchmark", "bench.run"),
    # Build > Build Container Images...: a source image published only as a file (Core/build_preload.take_image_file)
    "btn_bimg_image_file": ("act_build_images", "bimg.source.take"),
    "btn_bimg_source_list": ("act_build_images", "bimg.source.list"),
}

STEPS = (
    # not runnable: `scan` writes target_hardware_config.txt into the folder it runs in (for a proposal the program
    # folder); the Builder's scan saves where the person chooses
    Step("probe.local", cli="scan", tool="probe_local_hardware", window="builder", target="act_deploy_scan_local"),
    Step("probe.script", cli="scan --script", cli_args=("--script",), tool="probe_local_hardware", window="builder",
         target="act_deploy_scan_script"),
    Step("probe.import", cli="scan --import", cli_args=("--import", "{file}"), params={"file": Param("path")},
         tool="probe_remote_device", window="builder",
         target="act_deploy_scan_import"),
    Step("module.create", cli="module create", tool="create_target_module", window="module_wizard",
         target="act_tools_module_wizard"),
    Step("targets.list", cli="targets list", tool="list_targets", window="targets", target="act_build_targets",
         runnable=True),
    Step("targets.check", cli="targets validate", cli_args=("{target}",), params={"target": Param("id")},
         tool="get_target_capabilities", window="builder",
         target="btn_builder_check_target"),
    Step("build.history", cli="build status", tool="list_build_requests", window="build_history",
         target="act_build_history", runnable=True),
    # a module's CMake options outside the allowlist (DESIGN round 25 Q6): the person decides when they start the
    # build in the Builder (or with build start --module-flags)
    Step("build.decide_flags", cli="build start --module-flags", cli_args=("--module-flags", "{decision}"),
         params={"decision": Param("choice", choices=("allow", "standard"))}, person_only="permission",
         window="builder", target="btn_builder_start"),
    # the kept F16 intermediates of GGUF builds (Core/f16_cache.py): listed for everyone, deleted by the person only
    Step("build.f16_list", cli="build f16 list", tool="get_status", target="act_build_f16", runnable=True),
    Step("build.f16_delete", cli="build f16 delete", person_only="deletion", target="btn_f16_delete"),
    # a source image published only as a file: the person gives the file (an AI never names it)
    Step("build.source_image_take", cli="build source-image take", cli_args=("{file}", "--target", "{target}"),
         params={"file": Param("path"), "target": Param("id")}, person_only="permission",
         target="btn_bimg_image_file"),
)

TEXTS = {
    "en": {
        "step.probe.local": "Probe this computer",
        "step.probe.local.what": "Probes this computer's hardware (CPU, memory, GPU, operating system) and stores "
                                 "the result as the profile of this computer.",
        "step.probe.local.why": "No profile of this computer is stored yet.",
        "step.probe.script": "Probe this computer with the fallback script",
        "step.probe.script.what": "Probes this computer with the framework's fallback shell script instead of the "
                                  "Python probe.",
        "step.probe.script.why": "The normal probe did not finish.",
        "step.probe.import": "Import a board's probe file",
        "step.module.create": "Create a hardware module for the board",
        "step.targets.list": "Look at the targets",
        "step.targets.list.what": "Lists every target with its ID, boards, formats and support level.",
        "step.targets.check": "Check the target",
        "step.build.f16_list": "Look at the kept F16 intermediates",
        "step.build.f16_list.what": "Lists the F16 intermediates kept for the next build of a model, with their size.",
        "step.build.f16_list.why": "A kept F16 intermediate saves converting the model again.",
        "step.build.f16_delete": "Delete kept F16 intermediates",
        "step.build.source_image_take": "Give the image file the target's vendor publishes",
        "step.build.source_image_take.what": "Reads the file you choose (a docker save or OCI archive): its sha256 "
                                             "must be the one target {target} names; only the named files are kept, "
                                             "nothing is run.",
        "step.build.source_image_take.why": "A source image of the target is published only as a file, which no build "
                                            "can fetch.",
        "step.build.f16_delete.what": "Lists the kept F16 intermediates and deletes the ones you pick "
                                      "({path:btn_f16_delete}).",
        "step.build.f16_delete.why": "They use space in the build cache until they are deleted.",
        "step.build.history": "Look at the build history",
        "step.build.history.what": "Lists the builds of every framework process with their state.",
        "step.build.decide_flags": "Decide on the module's CMake options",
        "step.build.decide_flags.what": "Start the build yourself: you see the options outside the allowed list, what "
                                        "they do and a recommendation, and allow them or use the standard flags.",
    },
    "de": {
        "step.probe.local": "Diesen Computer prüfen",
        "step.probe.local.what": "Prüft die Hardware dieses Computers (CPU, Arbeitsspeicher, GPU, Betriebssystem) "
                                 "und speichert das Ergebnis als Profil dieses Computers.",
        "step.probe.local.why": "Es ist noch kein Profil dieses Computers gespeichert.",
        "step.probe.script": "Diesen Computer mit dem Ersatz-Skript prüfen",
        "step.probe.script.what": "Prüft diesen Computer mit dem Shell-Ersatz-Skript des Frameworks statt mit der "
                                  "Python-Prüfung.",
        "step.probe.script.why": "Die normale Prüfung wurde nicht fertig.",
        "step.probe.import": "Die Prüfdatei eines Boards importieren",
        "step.module.create": "Ein Hardware-Modul für das Board erstellen",
        "step.targets.list": "Die Targets ansehen",
        "step.targets.list.what": "Listet jedes Target mit ID, Boards, Formaten und Unterstützungsgrad.",
        "step.targets.check": "Das Target prüfen",
        "step.build.f16_list": "Die aufbewahrten F16-Zwischenstufen ansehen",
        "step.build.f16_list.what": "Listet die F16-Zwischenstufen, die für den nächsten Build eines Modells "
                                    "aufbewahrt sind, mit ihrer Größe.",
        "step.build.f16_list.why": "Eine aufbewahrte F16-Zwischenstufe erspart das erneute Umwandeln des Modells.",
        "step.build.f16_delete": "Aufbewahrte F16-Zwischenstufen löschen",
        "step.build.source_image_take": "Die Image-Datei angeben, die der Hersteller des Targets veröffentlicht",
        "step.build.source_image_take.what": "Liest die gewählte Datei (ein Archiv aus docker save oder OCI): Der "
                                             "sha256 der Datei muss der sein, den Target {target} nennt; nur die "
                                             "genannten Dateien bleiben, nichts wird ausgeführt.",
        "step.build.source_image_take.why": "Ein Quell-Image des Targets ist nur als Datei veröffentlicht, die kein "
                                            "Build holen kann.",
        "step.build.f16_delete.what": "Listet die aufbewahrten F16-Zwischenstufen und löscht die, die du wählst "
                                      "({path:btn_f16_delete}).",
        "step.build.f16_delete.why": "Sie belegen Platz im Build-Cache, bis sie gelöscht sind.",
        "step.build.history": "Den Build-Verlauf ansehen",
        "step.build.history.what": "Listet die Builds aller Framework-Prozesse mit ihrem Zustand.",
        "step.build.decide_flags": "Über die CMake-Optionen des Moduls entscheiden",
        "step.build.decide_flags.what": "Den Build selbst starten: Du siehst die Optionen außerhalb der erlaubten "
                                        "Liste, was sie tun, und eine Empfehlung, und erlaubst sie oder nimmst die "
                                        "Standard-Flags.",
    },
}

# The "Sie" form of the German texts above that address the person (Q1); only the texts that differ.
FORMAL = {
    "de": {
        "step.build.f16_delete.what": "Listet die aufbewahrten F16-Zwischenstufen und löscht die, die Sie wählen "
                                      "({path:btn_f16_delete}).",
        "step.build.decide_flags.what": "Den Build selbst starten: Sie sehen die Optionen außerhalb der erlaubten "
                                        "Liste, was sie tun, und eine Empfehlung, und erlauben sie oder nehmen die "
                                        "Standard-Flags.",
    },
}
