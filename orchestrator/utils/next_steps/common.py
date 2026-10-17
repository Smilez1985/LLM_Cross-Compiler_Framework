#!/usr/bin/env python3
"""
Next steps of the framework as a whole (Mojo, the trash, Docker, the settings, the main window) and the one table of
the main window's menu structure, MENU_LAYOUT (wave 6, chain a0; DECISIONS round 10 Q2 and V2).

MENU_LAYOUT names every menu by its objectName and title key, and every entry by its objectName and label key, in
the order the person sees them: (objectName, title key, entries) for a menu or submenu, (objectName, label key) for
an entry, "-" for a separator. The main window arranges its menus after it (orchestrator/gui/menus/place.py
arrange()) and
the mouse paths of the next steps follow from it, so a path is always the one on screen, in the person's language.
An entry an area module adds that is not listed stays where that module put it (its MOUSE_LABELS name its place).
"""

from orchestrator.utils.next_steps import Param, Step

MENU_LAYOUT = (
    ("menu_file", "main.menu.file", (
        ("act_file_trash", "main.file.trash"),
        ("act_file_output_folder", "main.file.output_folder"),
        ("act_file_settings", "bld.menu.settings"),
        "-",
        ("act_file_exit", "main.file.exit"),
    )),
    ("menu_build", "main.menu.build", (
        ("act_build_targets", "bld.menu.targets"),
        ("act_build_history", "bld.menu.history"),
    )),
    ("menu_boards", "main.menu.boards", (
        ("act_boards_devices", "boards.menu.devices"),
    )),
    ("menu_ditto", "main.menu.ditto", (
        ("act_ditto_setup", "main.ditto.setup"),
        "-",
        ("act_ditto_settings", "main.ditto.settings"),
        ("act_tools_permissions", "main.ditto.permissions"),
        ("act_tools_folders", "main.ditto.folders"),
        ("act_tools_approvals", "main.ditto.approvals"),
        ("act_tools_proposals", "main.ditto.proposals"),
        "-",
        ("menu_ditto_advanced", "main.menu.ditto_advanced", (
            ("act_ditto_runtime_open", "ditto_runtime.menu.open"),
            ("act_ditto_models_open", "ditto_models.menu.models"),
            ("act_ditto_eval_open", "ditto_models.menu.eval"),
        )),
    )),
    ("menu_security", "main.menu.security", (
        ("act_signin_openai_key", "main.security.openai"),
        "-",
        ("act_signin_secrets", "signin.menu.secrets"),
        ("act_signin_overview", "signin.menu.overview"),
        ("act_signin_key_storage", "signin.menu.key_storage"),
        "-",
        ("act_tools_mcp_audit", "tmcp.menu.audit"),
        ("act_tools_mcp_connections", "tmcp.menu.mcp"),
    )),
    ("menu_view", "main.menu.view", (
        ("act_view_chat", "chat.view.toggle"),
        ("act_view_focus_chat", "chat.view.focus"),
    )),
    ("menu_tools", "main.menu.tools", (
        ("act_tools_module_wizard", "main.tools.module_wizard"),
        ("act_tools_hf_browser", "main.tools.hf_browser"),
        "-",
        ("menu_tools_knowledge", "main.menu.tools_knowledge", (
            ("act_knowledge_hub", "main.tools.hub"),
            ("act_knowledge_snapshots", "main.tools.snapshots"),
            ("act_knowledge_sources", "knowledge.menu.sources"),
            ("act_knowledge_index_docs", "main.tools.index_docs"),
            ("act_tools_mcp_crawl_notice", "tmcp.menu.crawl"),
        )),
        ("menu_tools_maintenance", "main.menu.tools_maintenance", (
            ("act_framework_images", "main.tools.images"),
            ("act_mojo_health", "mojo.menu"),
        )),
        "-",
        ("act_tools_audio", "main.tools.audio"),
        ("act_tools_clear_history", "main.tools.clear_history"),
    )),
    ("menu_help", "main.menu.help", (
        ("act_help_about", "main.help.about"),
    )),
)

# The main window's tabs (their attribute names, as TAB_OBJECTS of orchestrator/gui/menus/deploy.py) and the objects
# of windows that belong to no area below.
MOUSE_LABELS = {
    "builder_tab": (None, "main.tab.builder"),
    "deployment_window": (None, "main.tab.deployment"),
    "btn_trash_restore": ("act_file_trash", "trash.restore"),
    "btn_trash_delete": ("act_file_trash", "trash.delete"),
    "btn_mojo_miracle": ("act_mojo_health", "mojo.miracle.button"),
    "btn_mojo_check_board": ("act_mojo_health", "mojo.boards.pick"),
}

STEPS = (
    Step("mojo.oracle", cli="mojo", tool="run_mojo_oracle", window="mojo", target="act_mojo_health", runnable=True),
    Step("mojo.miracle", cli="mojo --miracle", cli_args=("--miracle",), tool="run_mojo_miracle", window="mojo",
         target="btn_mojo_miracle"),
    Step("images.free", cli="doctor --tier", cli_args=("--fix", "--tier", "2"), person_only="deletion",
         window="mojo", target="act_framework_images"),
    Step("trash.restore", cli="trash restore", cli_args=("{ids}",), params={"ids": Param("id")},
         tool="restore_from_trash", window="trash", target="btn_trash_restore"),
    Step("trash.empty", cli="trash empty", person_only="deletion", window="trash", target="btn_trash_delete"),
    Step("trash.show", cli="trash list", tool="list_trash", window="trash", target="act_file_trash", runnable=True),
    # settings that loosen a protection: only the person, in the AI settings (offline, knowledge base) or, until
    # File > Settings... can change them (chain settings-models), by editing the settings file
    Step("settings.offline_off", person_only="permission", window="ai_settings", target="act_ditto_settings"),
    Step("settings.knowledge_on", person_only="permission", window="ai_settings", target="act_ditto_settings"),
    Step("settings.sandbox_on", person_only="permission", window="settings", target="act_file_settings"),
    Step("settings.pin_image", person_only="permission", window="settings", target="act_file_settings"),
    Step("settings.show", cli="config show", person_only="permission", window="settings", target="act_file_settings",
         runnable=True),
    Step("output.folder", cli="config output-folder", cli_args=("{folder}",), params={"folder": Param("path")},
         person_only="permission", target="act_file_output_folder"),
    # Docker and the computer itself: an administrator's work, shown with its command, never run by the framework
    Step("docker.start", person_only="admin", literal="sudo systemctl start docker"),
    Step("docker.group", person_only="admin", literal="sudo usermod -aG docker $USER"),
    Step("docker.login", person_only="third_party", program="Docker", literal="docker login"),
    Step("hf.access", person_only="third_party", program="Hugging Face", url="https://huggingface.co/settings/tokens"),
    Step("window.open", person_only="permission"),
    Step("log.folder", person_only="permission"),
)

TEXTS = {
    "en": {
        "step.mojo.oracle": "Check the installation with Mojo",
        "step.mojo.oracle.what": "Mojo Oracle checks the installation and lists what is wrong, with where and why; "
                                 "it changes nothing.",
        "step.mojo.oracle.why": "It finds the cause of the problem.",
        "step.mojo.miracle": "Let Mojo Miracle repair what it can",
        "step.images.free": "Free disk space by deleting framework build images you no longer need",
        "step.images.free.what": "Lists the framework's Docker build images with their sizes; you tick what goes.",
        "step.images.free.why": "The disk is nearly full.",
        "step.trash.restore": "Put it back from the trash",
        "step.trash.empty": "Empty the trash (you tick what goes)",
        "step.trash.show": "Look into the trash",
        "step.trash.show.what": "Lists what is in the trash, with size and when it was moved there.",
        "step.settings.offline_off": "Switch offline mode off in the AI settings",
        "step.settings.knowledge_on": "Switch the knowledge base on in the AI settings",
        "step.settings.sandbox_on": "Switch the build sandbox on in the settings",
        "step.settings.pin_image": "Set the program image to an exact version in the settings",
        "step.settings.show": "Look at the settings",
        "step.settings.show.what": "Shows the effective settings and where each one comes from.",
        "step.output.folder": "Choose the folder for finished models",
        "step.docker.start": "Start Docker (Docker Desktop on Windows and macOS)",
        "step.docker.group": "Allow your user account to use Docker, then log in again",
        "step.docker.login": "Log in to the image registry",
        "step.hf.access": "Sign in to Hugging Face and accept the model's conditions on its page",
        "step.window.open": "Open the framework window",
        "step.log.folder": "Look at the framework's log",
    },
    "de": {
        "step.mojo.oracle": "Die Installation mit Mojo prüfen",
        "step.mojo.oracle.what": "Mojo Oracle prüft die Installation und listet, was nicht stimmt, mit Ort und "
                                 "Grund; es ändert nichts.",
        "step.mojo.oracle.why": "Es findet die Ursache des Problems.",
        "step.mojo.miracle": "Mojo Miracle reparieren lassen, was es kann",
        "step.images.free": "Speicherplatz freigeben, indem du Build-Images des Frameworks löschst, die du nicht mehr "
                             "brauchst",
        "step.images.free.what": "Listet die Docker-Build-Images des Frameworks mit ihrer Größe; du hakst an, was "
                                 "gelöscht wird.",
        "step.images.free.why": "Die Festplatte ist fast voll.",
        "step.trash.restore": "Aus dem Papierkorb zurücklegen",
        "step.trash.empty": "Den Papierkorb leeren (du hakst an, was gelöscht wird)",
        "step.trash.show": "In den Papierkorb schauen",
        "step.trash.show.what": "Listet, was im Papierkorb liegt, mit Größe und wann es dorthin kam.",
        "step.settings.offline_off": "Den Offline-Modus in den KI-Einstellungen ausschalten",
        "step.settings.knowledge_on": "Die Wissensdatenbank in den KI-Einstellungen einschalten",
        "step.settings.sandbox_on": "Die Build-Sandbox in den Einstellungen einschalten",
        "step.settings.pin_image": "Das Programm-Image in den Einstellungen auf eine genaue Version festlegen",
        "step.settings.show": "Die Einstellungen ansehen",
        "step.settings.show.what": "Zeigt die wirksamen Einstellungen und woher jede kommt.",
        "step.output.folder": "Den Ordner für fertige Modelle wählen",
        "step.docker.start": "Docker starten (Docker Desktop unter Windows und macOS)",
        "step.docker.group": "Deinem Benutzerkonto Docker erlauben, dann neu anmelden",
        "step.docker.login": "Bei der Image-Registry anmelden",
        "step.hf.access": "Bei Hugging Face anmelden und auf der Seite des Modells seine Bedingungen akzeptieren",
        "step.window.open": "Das Framework-Fenster öffnen",
        "step.log.folder": "Das Protokoll des Frameworks ansehen",
    },
}
