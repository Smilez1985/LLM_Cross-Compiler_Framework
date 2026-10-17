#!/usr/bin/env python3
"""
The sign-in entries of the Security menu (W1' of 2026-10-16: every action of `llmcf secrets` and `llmcf ditto login|
logout|auth` also by mouse).

Security > Secrets... (act_signin_secrets), Security > Sign-in Overview... (act_signin_overview) and Security > Key
Storage... (act_signin_key_storage) open the windows of orchestrator/gui/signin_windows.py; they load when an entry is
first used (lazy loading, RG1). The existing Security > Set OpenAI API Key gets its object name here
(act_signin_openai_key). The Security menu is found by its object name or its title; it is created only when the main
window has none.

CLI_PATHS names, for each `llmcf` command path of this area, the object that does it by mouse; WINDOW_OBJECTS names,
for an object that lives in a window, the menu action that opens that window. MACHINE_ONLY lists the switches of this
area that are for scripts only, with the reason (the integrator lists them once).

Sign-in methods per provider (`ditto login`, `ditto logout`) are also reachable from Tools > Configure AI Agent
(Ditto)... > Sign in... (the same sign-in panel, for the address typed in the AI settings).
"""

from typing import Dict

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

MENU_NAME = place.MENU_SECURITY
SECURITY_TITLES = ("Security", "Sicherheit")
ACT_SECRETS = "act_signin_secrets"
ACT_OVERVIEW = "act_signin_overview"
ACT_KEY_STORAGE = "act_signin_key_storage"
ACT_OPENAI_KEY = "act_signin_openai_key"   # the existing Security > Set OpenAI API Key
OPENAI_KEY_TEXT = "Set OpenAI API Key"

CLI_PATHS: Dict[str, str] = {
    "secrets": ACT_SECRETS,
    "secrets list": ACT_SECRETS,                       # the table: every name with stored / not stored
    "secrets set": "btn_secrets_store",                # name (a known one or any other), the value twice, masked
    "ditto login": "btn_signin_save",                  # Sign-in Overview > a provider > its key card > Save
    "ditto login --method": "rb_signin_method",        # the method cards
    "ditto login --from-env": "btn_signin_from_env",   # Store the key from <VARIABLE> in the vault
    "ditto login --base-url": "txt_signin_base_url",   # the panel's own server address field, remembered per provider
    "ditto login --workspace-id": "btn_signin_save",   # the key card's extra fields, saved with Save
    "ditto login --organization": "btn_signin_save",
    "ditto login --project": "btn_signin_save",
    "ditto login --profile": "btn_signin_use_profile",  # the ant profile card: Profile + Use this profile
    "ditto login --insecure-storage": "btn_storage_use",  # Key Storage... > Key file next to the vault (asks first)
    "ditto login --no-check": "btn_signin_save",       # Save stores without the check; Check is its own button
    "ditto logout": "btn_signin_sign_out",             # key and profile cards; Forget for a server without sign-in
    "ditto logout --method": "btn_signin_sign_out",    # the card of that method
    "ditto logout --base-url": "txt_signin_base_url",
    "ditto auth": ACT_OVERVIEW,
    "ditto auth status": ACT_OVERVIEW,                 # the table of every provider and the vault line
    "ditto auth status --check": "btn_signin_check_all",
    "ditto auth methods": "tbl_signin_methods",        # the tab All sign-in methods
    "ditto auth storage": ACT_KEY_STORAGE,             # protection, whether it works, the vault file
    "ditto auth storage --use": "btn_storage_use",     # keyring, helper, passphrase, insecure
    "ditto auth storage --helper": "txt_storage_helper",
    "ditto auth rotate-key": "btn_keystore_rotate",
}
# Objects that live in the Security windows, and the menu action that opens each window. The sign-in panel's Forget
# (btn_signin_forget) is `ditto logout` for a server without sign-in (Ollama, a custom server without a key).
WINDOW_OBJECTS: Dict[str, str] = {
    "tbl_secrets": ACT_SECRETS, "btn_secrets_store": ACT_SECRETS, "cb_secrets_name": ACT_SECRETS,
    "txt_secrets_value": ACT_SECRETS, "txt_secrets_repeat": ACT_SECRETS,
    "tbl_signin_status": ACT_OVERVIEW, "btn_signin_check_all": ACT_OVERVIEW, "tbl_signin_methods": ACT_OVERVIEW,
    "btn_signin_save": ACT_OVERVIEW, "rb_signin_method": ACT_OVERVIEW, "btn_signin_from_env": ACT_OVERVIEW,
    "txt_signin_base_url": ACT_OVERVIEW, "btn_signin_use_profile": ACT_OVERVIEW, "btn_signin_sign_out": ACT_OVERVIEW,
    "btn_signin_forget": ACT_OVERVIEW,
    "btn_storage_use": ACT_KEY_STORAGE, "txt_storage_helper": ACT_KEY_STORAGE, "btn_keystore_rotate": ACT_KEY_STORAGE,
}
# The sign-in panel of the Sign-in Overview shows the selected provider's forms only: the provider to select so that
# each panel object exists (a key card for openai, the ant profile card for anthropic, the address field and Forget
# for ollama).
PANEL_OBJECTS: Dict[str, str] = {
    "btn_signin_save": "openai", "rb_signin_method": "openai", "btn_signin_from_env": "openai",
    "btn_signin_sign_out": "openai", "btn_signin_use_profile": "anthropic", "txt_signin_base_url": "ollama",
    "btn_signin_forget": "ollama",
}
MACHINE_ONLY: Dict[str, str] = {
    "secrets set --with-token": "script switch: the value from stdin; the window has the two masked fields",
    "secrets set --password": "deprecated (the value ends up in the shell history); the window has masked fields",
    "ditto login --with-token": "script switch: the key from stdin; the key card has a masked field",
    "ditto login --yes": "script switch: the window asks the plain-http and key-file questions instead",
    "ditto auth status --json": "machine-readable output; the Sign-in Overview shows the same rows",
    "ditto auth methods --json": "machine-readable output; the tab All sign-in methods shows the same rows",
    "ditto auth storage --yes": "script switch: the window asks the key-file question instead",
}


def install(window, menubar) -> None:
    """Adds Secrets..., Sign-in Overview... and Key Storage... to the Security menu (window.act_signin_*) and names the
    existing Set OpenAI API Key entry."""
    menu = place.menu_or_new(menubar, MENU_NAME, "Security", SECURITY_TITLES)
    place.name_entry(place.entry(menu, OPENAI_KEY_TEXT, ACT_OPENAI_KEY, menubar), ACT_OPENAI_KEY)
    menu.addSeparator()
    entries = ((ACT_SECRETS, "signin.menu.secrets", open_secrets_window),
               (ACT_OVERVIEW, "signin.menu.overview", open_overview_window),
               (ACT_KEY_STORAGE, "signin.menu.key_storage", open_key_storage_window))
    for name, text_key, opener in entries:
        action = place.new_action(window, name, ui_text(text_key), lambda open_it=opener: open_it(window))
        menu.addAction(action)
        setattr(window, name, action)
    window.menu_security = menu


def open_secrets_window(window):
    from orchestrator.gui.signin_windows import open_secrets
    return open_secrets(window)


def open_overview_window(window):
    from orchestrator.gui.signin_windows import open_overview
    return open_overview(window)


def open_key_storage_window(window):
    from orchestrator.gui.signin_windows import open_key_storage
    return open_key_storage(window)


__all__ = ["ACT_KEY_STORAGE", "ACT_OPENAI_KEY", "ACT_OVERVIEW", "ACT_SECRETS", "CLI_PATHS", "MACHINE_ONLY",
           "MENU_NAME", "PANEL_OBJECTS", "WINDOW_OBJECTS", "install", "open_key_storage_window",
           "open_overview_window", "open_secrets_window"]
