#!/usr/bin/env python3
"""
Next steps of the Deployment tab (packages, deploying, saving a finished model; chain deploy-hf refines them).
Seeded by chain a0 from the wave-6 audit (items 29, 59).
"""

from orchestrator.utils.next_steps import Param, Step

_TAB = "deployment_window"

MOUSE_LABELS = {
    "btn_deploy_package": (_TAB, "deploy.package.button"),
    "btn_deploy_existing": (_TAB, "deploy.existing.button"),
    "btn_deploy_save": (_TAB, "save.button"),
}

STEPS = (
    Step("deploy.package", cli="deploy package", cli_args=("{artifact}",), params={"artifact": Param("id")},
         tool="create_deploy_package", window="deployment",
         target="btn_deploy_package"),
    Step("deploy.run", cli="deploy run", cli_args=("{package}",), params={"package": Param("path")},
         tool="deploy_to_device", window="deployment", target="btn_deploy_existing"),
    Step("deploy.save", cli="build save", cli_args=("{artifact}", "--to", "{folder}"),
         params={"artifact": Param("id"), "folder": Param("path")}, person_only="permission", window="deployment",
         target="btn_deploy_save"),
)

TEXTS = {
    "en": {
        "step.deploy.package": "Create a package only (no board needed)",
        "step.deploy.run": "Deploy an existing package to the board",
        "step.deploy.save": "Save the finished model to a folder",
    },
    "de": {
        "step.deploy.package": "Nur ein Paket erstellen (kein Board nötig)",
        "step.deploy.run": "Ein vorhandenes Paket auf das Board bringen",
        "step.deploy.save": "Das fertige Modell in einem Ordner speichern",
    },
}
