#!/usr/bin/env python3
"""
Next steps of setting up Ditto and asking it (Ditto > Set up Ditto..., the AI settings, the chat panel; chain
ditto-setup refines them, and its guide replaces the AI settings behind "Set up Ditto..."). Seeded by chain a0 from
the wave-6 audit (items 3, 51, 52).
"""

from orchestrator.utils.next_steps import Step

MOUSE_LABELS = {
    "btn_chat_send": ("act_view_chat", "chat.composer.send"),
}

STEPS = (
    Step("ditto.setup", person_only="permission", window="ai_settings", target="act_ditto_setup"),
    Step("ditto.settings", person_only="permission", window="ai_settings", target="act_ditto_settings"),
    Step("ditto.notice", person_only="licence", window="ai_settings", target="act_ditto_setup"),
    Step("ditto.ask", cli="ditto ask", person_only="permission", window="chat", target="btn_chat_send"),
    Step("ditto.chat", person_only="permission", window="chat", target="act_view_chat"),
)

TEXTS = {
    "en": {
        "step.ditto.setup": "Set up Ditto",
        "step.ditto.settings": "Change Ditto's AI settings",
        "step.ditto.notice": "Read the notice about Ditto",
        "step.ditto.ask": "Ask Ditto in the chat",
        "step.ditto.chat": "Show the chat with Ditto",
    },
    "de": {
        "step.ditto.setup": "Ditto einrichten",
        "step.ditto.settings": "Dittos KI-Einstellungen ändern",
        "step.ditto.notice": "Den Hinweis zu Ditto lesen",
        "step.ditto.ask": "Ditto im Chat fragen",
        "step.ditto.chat": "Den Chat mit Ditto zeigen",
    },
}
