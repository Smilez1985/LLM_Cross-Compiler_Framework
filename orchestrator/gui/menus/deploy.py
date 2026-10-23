#!/usr/bin/env python3
"""
The deploy area's mouse paths (W1' of 2026-10-16: every action of `llmcf deploy package`, `llmcf deploy run`,
`llmcf deploy list-artifacts` and `llmcf scan` also by mouse; GUI parity wave 5a, chain "deploy").

These paths are contextual and live in two tabs of the main window, which `_setup_central_widget` builds after the
menu bar, so this module adds no menu entry of its own (GUI_PARITY.md's menu layout has none for this area):

- Deployment tab (main_window.deployment_window, orchestrator/gui/deployment_window.py): the build list, Save...,
  Other build or model file... (any path, as `deploy package ARTIFACT` takes it), the Port, Name and "Wait for the
  board" fields, Deploy to Target, Create package only..., Deploy existing package... and Cancel.
- Builder tab (main_window.builder_tab, orchestrator/gui/builder_tab.py) > Scan / Import...: Import from File
  (with the board's name), Remote Scan via SSH... (with Port and Name), Scan Local Host (the Python probe), Scan
  Local Host with the Fallback Script, Scan Local Host and Save the Profile As... and Scan Local Host with the
  Fallback Script and Save the Profile As....

CLI_PATHS names, for each `llmcf` command path of this area, the object that does it by mouse (the main window finds
every one of them with findChild: the tabs are its children). TAB_OBJECTS names the tab each object lives in.
MACHINE_ONLY lists the switches of this area that are for scripts only, with the reason (the integrator lists them
once). The Deployment tab's "Benchmark on the board" group belongs to the boards and Mojo areas.
"""

from typing import Dict

DEPLOYMENT_TAB = "deployment_window"   # attribute of MainOrchestrator
BUILDER_TAB = "builder_tab"

CLI_PATHS: Dict[str, str] = {
    "deploy": "btn_deploy_run",
    "deploy list-artifacts": "list_deploy_artifacts",      # the build list with "not saved yet" and tested-on boards
    "deploy package": "btn_deploy_package",                # Create package only... (no board; air-gapped boards)
    "deploy package --profile": "btn_deploy_package",      # the Profile combo of the tab
    "deploy package --docker": "table_deploy_containers",  # the containers table of the "Use Docker
                                                           # Containerization?" group: the containers --docker packs
                                                           # (package_containers), each ticked one goes in
    "deploy package --no-docker": "grp_deploy_docker",     # the group's own tick, off
    "deploy run": "btn_deploy_existing",                   # Deploy existing package... (PACKAGE_PATH); Deploy to
                                                           # Target (btn_deploy_run) packages and deploys in one step
    "deploy run --host": "btn_deploy_existing",            # the "Address or host name" field
    "deploy run --user": "btn_deploy_existing",            # the User field
    "deploy run --port": "spin_deploy_port",
    "deploy run --password": "btn_deploy_existing",        # asked with hidden input, used once, never stored
    "deploy run --label": "txt_deploy_label",
    "deploy run --wait": "spin_deploy_wait",
    "scan": "act_deploy_scan_local",                       # Scan Local Host: the Python probe, stored as probe local
    "scan --script": "act_deploy_scan_script",
    "scan --output": "act_deploy_scan_save",               # the save dialog
    "scan --json": "act_deploy_scan_save",                 # a .json file in the save dialog gets the JSON view
    "scan --device": "act_deploy_scan_remote",             # Remote Scan via SSH...
    "scan --user": "act_deploy_scan_remote",
    "scan --port": "act_deploy_scan_remote",               # the dialog's Port field (spin_scan_port)
    "scan --password": "act_deploy_scan_remote",           # the dialog's Password field, used once, never stored
    "scan --label": "act_deploy_scan_remote",              # the dialog's Name field (txt_scan_label); for --import
                                                           # the name is asked after the file dialog
    "scan --import": "act_deploy_scan_import",             # Import from File (USB/Disk)...
}
TAB_OBJECTS: Dict[str, str] = {name: (BUILDER_TAB if name.startswith("act_deploy_scan") else DEPLOYMENT_TAB)
                               for name in set(CLI_PATHS.values())}
TAB_OBJECTS["btn_deploy_scan"] = BUILDER_TAB               # the Scan / Import... button that opens the scan menu
TAB_OBJECTS["btn_deploy_cancel"] = DEPLOYMENT_TAB          # Cancel of a running deploy or package (Ctrl+C in the CLI)
# `deploy package ARTIFACT` takes any path: Other build or model file... adds a build folder (act_deploy_add_folder)
# or a model file (act_deploy_add_model) to the build list for Create package only... and Deploy to Target.
TAB_OBJECTS["btn_deploy_add_source"] = DEPLOYMENT_TAB
TAB_OBJECTS["act_deploy_add_folder"] = DEPLOYMENT_TAB
TAB_OBJECTS["act_deploy_add_model"] = DEPLOYMENT_TAB
TAB_OBJECTS["act_deploy_scan_script_save"] = BUILDER_TAB   # `scan --script --output FILE` together
MACHINE_ONLY: Dict[str, str] = {
    "deploy run --ip": "compatibility alias of --host (the same field)",
    "deploy run --password-stdin": "script switch: the GUI asks for the password with hidden input",
    "deploy run --trust-fingerprint": "script switch: the GUI shows the fingerprint and asks (No is the default)",
    "scan --password-stdin": "script switch: the GUI asks for the password with hidden input",
    "scan --trust-fingerprint": "script switch: the GUI shows the fingerprint and asks (No is the default)",
}


def install(window, menubar) -> None:
    """Nothing to add to the menu bar: this area's paths are buttons and scan-menu entries of the Deployment and
    Builder tabs (see the module text). Kept so every area module has the same shape."""
    return None


__all__ = ["BUILDER_TAB", "CLI_PATHS", "DEPLOYMENT_TAB", "MACHINE_ONLY", "TAB_OBJECTS", "install"]
