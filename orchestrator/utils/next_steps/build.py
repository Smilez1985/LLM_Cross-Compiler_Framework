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
    # a module's CMake options outside the allowlist (DECISIONS round 25 Q6): the person decides when they start the
    # build in the Builder (or with build start --module-flags)
    Step("build.decide_flags", cli="build start --module-flags", cli_args=("--module-flags", "{decision}"),
         params={"decision": Param("choice", choices=("allow", "standard"))}, person_only="permission",
         window="builder", target="btn_builder_start"),
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
        "step.build.decide_flags.what": "Den Build selbst starten: Sie sehen die Optionen außerhalb der erlaubten "
                                        "Liste, was sie tun, und eine Empfehlung, und erlauben sie oder nehmen die "
                                        "Standard-Flags.",
    },
}
