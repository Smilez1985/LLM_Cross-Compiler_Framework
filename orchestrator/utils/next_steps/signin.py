#!/usr/bin/env python3
"""
Next steps of signing in, the secrets and the key safe (Security > ...; chain signin refines them). Seeded by chain
a0 from the wave-6 audit (items 10, 34, 39, 40, 43). Keys and passwords never pass through an AI: every step here is
the person's; `secrets.set` takes its value in a hidden field and hands it to the command through stdin.
"""

from orchestrator.utils.next_steps import Param, Step

MOUSE_LABELS = {
    "btn_signin_save": ("act_signin_overview", "signin.button.save"),
    "btn_secrets_store": ("act_signin_secrets", "secrets.store"),
    "btn_storage_use": ("act_signin_key_storage", "signin.button.storage_use"),
}

STEPS = (
    Step("signin.provider", cli="ditto login", cli_args=("{provider}",), params={"provider": Param("id")},
         person_only="secret", window="ai_settings", target="btn_signin_save"),
    Step("signin.workspace", cli="ditto login --workspace-id", cli_args=("anthropic", "--workspace-id", "{workspace}"),
         params={"workspace": Param("id")}, person_only="secret", window="ai_settings",
         target="btn_signin_save"),
    Step("signin.method", cli="ditto login --method", cli_args=("{provider}", "--method", "{method}"),
         params={"provider": Param("id"), "method": Param("id")}, person_only="secret", window="ai_settings",
         target="rb_signin_method", planned=True),
    Step("signin.overview", cli="ditto auth status", person_only="secret", target="act_signin_overview",
         runnable=True),
    Step("signin.openai_key", person_only="secret", target="act_signin_openai_key"),
    Step("secrets.set", cli="secrets set", cli_args=("{name}", "--with-token", "{value}"),
         params={"name": Param("id"), "value": Param("secret")},
         person_only="secret", target="btn_secrets_store", runnable=True),
    Step("secrets.list", cli="secrets list", person_only="secret", target="act_signin_secrets", runnable=True),
    Step("keystore.use", cli="ditto auth storage --use", cli_args=("--use", "{protection}"),
         params={"protection": Param("choice", ("keyring", "helper", "passphrase", "insecure"))},
         person_only="secret", target="btn_storage_use"),
    Step("signin.anthropic_cli", person_only="third_party", program="the Anthropic command-line tool",
         literal="ant auth login"),
    Step("signin.ollama", person_only="third_party", program="Ollama", literal="ollama signin"),
)

TEXTS = {
    "en": {
        "step.signin.provider": "Sign in to the AI service",
        "step.signin.workspace": "Enter the workspace ID when you sign in",
        "step.signin.method": "Choose another sign-in method",
        "step.signin.overview": "Look at the sign-in overview",
        "step.signin.overview.what": "Lists every AI service with how you are signed in and whether the key safe "
                                     "works.",
        "step.signin.openai_key": "Store the OpenAI API key",
        "step.secrets.set": "Store the secret",
        "step.secrets.set.what": "Stores the secret {name} in the encrypted key safe; you type the value into a "
                                 "hidden field.",
        "step.secrets.set.why": "The framework needs it and it is not stored yet.",
        "step.secrets.list": "Look at the stored secrets",
        "step.secrets.list.what": "Lists the names of the stored secrets with 'stored' or 'not stored'; values are "
                                  "never shown.",
        "step.keystore.use": "Choose how the key safe is protected",
        "step.signin.anthropic_cli": "Sign in with the Anthropic command-line tool first",
        "step.signin.ollama": "Sign in to Ollama",
    },
    "de": {
        "step.signin.provider": "Beim KI-Dienst anmelden",
        "step.signin.workspace": "Beim Anmelden die Workspace-ID eingeben",
        "step.signin.method": "Eine andere Anmeldeart wählen",
        "step.signin.overview": "Die Anmeldeübersicht ansehen",
        "step.signin.overview.what": "Listet jeden KI-Dienst mit deiner Anmeldung und ob der Schlüsseltresor "
                                     "funktioniert.",
        "step.signin.openai_key": "Den OpenAI-API-Key speichern",
        "step.secrets.set": "Das Geheimnis speichern",
        "step.secrets.set.what": "Speichert das Geheimnis {name} im verschlüsselten Schlüsseltresor; den Wert gibst "
                                 "du in ein verdecktes Feld ein.",
        "step.secrets.set.why": "Das Framework braucht es, und es ist noch nicht gespeichert.",
        "step.secrets.list": "Die gespeicherten Geheimnisse ansehen",
        "step.secrets.list.what": "Listet die Namen der gespeicherten Geheimnisse mit 'gespeichert' oder 'nicht "
                                  "gespeichert'; Werte werden nie angezeigt.",
        "step.keystore.use": "Wählen, wie der Schlüsseltresor geschützt ist",
        "step.signin.anthropic_cli": "Dich zuerst mit dem Kommandozeilenwerkzeug von Anthropic anmelden",
        "step.signin.ollama": "Dich bei Ollama anmelden",
    },
}

# The "Sie" form of the German texts above that address the person (Q1); only the texts that differ.
FORMAL = {
    "de": {
        "step.secrets.set.what": "Speichert das Geheimnis {name} im verschlüsselten Schlüsseltresor; den Wert geben "
                                 "Sie in ein verdecktes Feld ein.",
        "step.signin.anthropic_cli": "Sich zuerst mit dem Kommandozeilenwerkzeug von Anthropic anmelden",
        "step.signin.ollama": "Sich bei Ollama anmelden",
        "step.signin.overview.what": "Listet jeden KI-Dienst mit Ihrer Anmeldung und ob der Schlüsseltresor "
                                     "funktioniert.",
    },
}
