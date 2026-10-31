"""The GUI's questions for a board that cannot take the root-owned key line (orchestrator/gui/key_tier_ask.py): one
yes/no question per choice in the offer's order, "No" (password login) when the person says no to each, the plan of
the switch and the removal of Dropbear asked separately."""

import pytest

pytest.importorskip("PySide6")

from orchestrator.gui import key_tier_ask  # noqa: E402
from orchestrator.remote import key_tiers  # noqa: E402


def _offer(mode="private"):
    facts = key_tiers.TierFacts(server="dropbear", sudo="password", verifier="yes", family="debian",
                                managers=("apt-get",))
    return key_tiers.offer("no-sshd", facts, mode)


@pytest.mark.parametrize("answers, chosen", [((True,), "openssh"), ((False, True), "dropbear"),
                                             ((False, False), "password")])
def test_the_gui_asks_each_choice_in_order_and_falls_back_to_the_password(monkeypatch, answers, chosen):
    asked = []
    replies = iter(answers)
    monkeypatch.setattr(key_tier_ask, "ask_from_worker", lambda token, title, text, stop=None: asked.append(text)
                        or next(replies))
    chooser = key_tier_ask.GuiTierChooser(object())
    assert chooser.choose(_offer()) == chosen
    assert len(asked) == len(answers) and "OpenSSH" in asked[0]
    if len(asked) > 1:
        assert "one step weaker" in asked[1]


def test_the_gui_says_why_enterprise_mode_refuses_and_never_offers_the_weaker_tier(monkeypatch):
    asked, told = [], []
    monkeypatch.setattr(key_tier_ask, "ask_from_worker", lambda token, title, text, stop=None: asked.append(text)
                        or False)
    chooser = key_tier_ask.GuiTierChooser(object(), tell=told.append)
    assert chooser.choose(_offer("enterprise")) == "password"
    assert len(asked) == 1 and "Enterprise" in told[0]
    assert chooser.confirm("dropbear", ["Remove Dropbear now?"]) is False
