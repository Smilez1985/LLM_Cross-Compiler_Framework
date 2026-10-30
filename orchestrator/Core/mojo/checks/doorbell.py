#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the MQTT build doorbell (Core/doorbell.py)
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok
from orchestrator.utils.next_steps import NextStep

WHY = "Without the doorbell nobody is woken when a build ends; the chat and the build status still say it."


def check_doorbell(framework) -> List[Finding]:
    """The person's MQTT broker for the build doorbell: no line without a broker; a setting that breaks the doorbell's
    rules (TLS beyond this computer, offline mode only to a local broker); the last send of any process failed. Reads
    only; it never connects. The broker by scheme, host and port only (never the user name or the password)."""
    from orchestrator.Core.doorbell import DoorbellRefused, broker_of, check_broker, read_status
    config = getattr(framework, "config", None)
    try:
        broker = broker_of(config)
        if broker is None:
            return []
        check_broker(broker, config)
    except DoorbellRefused as e:
        return [Finding("network", "doorbell.settings", Severity.WARN, str(e),
                        "Change the build doorbell's broker in the settings.",
                        where="the setting mqtt_broker_url", why=WHY, cannot="person",
                        steps=(NextStep("settings.doorbell"),))]
    status = read_status(getattr(config, "user_state_dir", None))
    if status and not status.get("ok"):
        return [Finding("network", "doorbell.send", Severity.WARN,
                        f"The last build doorbell to {broker.label()} was not sent ({status.get('at', '?')}): "
                        f"{status.get('problem') or 'the broker did not take it'}",
                        "Check that the broker runs and that its address, user name, password and CA file are "
                        "right, then send a test doorbell.",
                        where=broker.label(), why=WHY, cannot="person", steps=(NextStep("settings.doorbell"),))]
    return [_ok("network", "doorbell.settings", f"Build doorbell set to {broker.label()}")]
