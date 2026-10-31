#!/usr/bin/env python3
"""
The GUI's questions for a board that cannot take the root-owned key line (remote/pairing.py TierChooser): asked from
the scan's worker thread in the GUI thread through the one worker relay (gui/worker_relay.py ask_from_worker), each a
plain yes/no question with "No" as the default. The choices come in the order of the offer (key_tiers.offer): the
switch to OpenSSH first, then the weaker tier this board could take (with its warning), then nothing: the board keeps
being reached with its password. The CLI asks the same choices at the terminal (cli.py _tier_chooser).
"""

from typing import Any, Callable, Optional

from orchestrator.gui.worker_relay import ask_from_worker
from orchestrator.remote import key_tiers
from orchestrator.remote.pairing import TierChooser
from orchestrator.utils.localization import ui_text


class GuiTierChooser(TierChooser):
    """The person's answers in the GUI; `token` is the receiver's relay token, `stop` ends a wait (Cancel)."""

    def __init__(self, token: object, stop: Optional[Callable[[], Any]] = None,
                 tell: Optional[Callable[[str], None]] = None):
        self.token, self.stop, self._tell = token, stop, tell

    def _ask(self, key: str, **values: Any) -> bool:
        return ask_from_worker(self.token, ui_text("deploy.tier.title"), ui_text(key, **values), stop=self.stop)

    def choose(self, offer: Any) -> str:
        from orchestrator.remote.gate import SETUP_REASONS
        reason = SETUP_REASONS.get(offer.reason, offer.reason)
        if offer.refused:
            self.tell([ui_text("deploy.tier.enterprise")])
        for choice in offer.choices:
            if choice == key_tiers.TIER_PASSWORD:
                break
            if self._ask(f"deploy.tier.ask.{choice}", reason=reason):
                return choice
        return key_tiers.TIER_PASSWORD

    def confirm(self, title: str, lines: Any) -> bool:
        key = "deploy.tier.ask.dropbear_off" if title == "dropbear" else "deploy.tier.ask.plan"
        return self._ask(key, plan="\n".join(str(line) for line in lines))

    def trust_key(self, check: Any) -> bool:
        return self._ask("deploy.tier.ask.host_key", key_type=check.key_type, fingerprint=check.fingerprint)

    def tell(self, lines: Any) -> None:
        if self._tell is not None:
            for line in lines:
                self._tell(str(line))


__all__ = ["GuiTierChooser"]
