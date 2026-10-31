"""Mojo's doorbell line keeps the broker's address and the last send error, also where an AI reads it (decided for
transparency: the person can fix the broker from it), and never carries the user name or the password.

Fake MQTT clients only; the broker names are documentation names (example.org, *.test)."""

import json
from types import SimpleNamespace

from orchestrator.Core import doorbell
from orchestrator.Core.doorbell import Doorbell
from orchestrator.Core.mojo.checks.doorbell import check_doorbell
from orchestrator.Core.mojo.views import labeller

USER = "doorbell-user-x7"   # made up
SECRET = "pw-" + "Kq4" * 6   # made up
BROKER = "mqtts://broker.example.org:8883"


class Config(dict):
    def __init__(self, *a, user_state_dir=None, **kw):
        super().__init__(*a, **kw)
        self.user_state_dir = user_state_dir

    def get(self, key, default=None):
        return super().get(key, default)


class Refusing:
    """A client whose connection fails with an error that names the user name and the password."""

    def __init__(self, broker, password):
        self.password = password

    async def __aenter__(self):
        raise ConnectionRefusedError(f"not authorised: user {USER} password {self.password} at broker.example.org")

    async def __aexit__(self, *exc):
        return False


def _failed_send(tmp_path):
    config = Config(mqtt_broker_url=BROKER, mqtt_username=USER, user_state_dir=tmp_path)
    bell = Doorbell(config, lambda: SECRET, client_factory=lambda b, p: Refusing(b, p), state_dir=tmp_path)
    bell.on_build_end({"request_id": "req_0a1b2c3d", "end_reason": "done"})
    assert bell.flush(10)
    return config


def test_the_last_send_error_never_keeps_the_user_name_or_the_password(tmp_path):
    _failed_send(tmp_path)
    status = doorbell.read_status(tmp_path)
    assert status["ok"] is False
    text = json.dumps(status)
    assert SECRET not in text
    assert USER not in text, "the user name of the broker reached the stored send error"
    assert "not authorised" in status["problem"]


def test_the_mojo_line_names_the_broker_and_the_error_for_the_person_and_for_an_ai(tmp_path):
    config = _failed_send(tmp_path)
    framework = SimpleNamespace(config=config)
    (finding,) = check_doorbell(framework)
    assert finding.code == "doorbell.send"
    for text in (finding.message, labeller(framework)(finding.message)):
        assert BROKER in text, "the broker address is kept (decided: transparency)"
        assert "not authorised" in text, "the last send error is kept"
        assert USER not in text and SECRET not in text
    assert finding.where == BROKER


def test_the_mojo_line_of_a_set_broker_names_it_without_credentials(tmp_path):
    config = Config(mqtt_broker_url=BROKER, mqtt_username=USER, user_state_dir=tmp_path)
    (finding,) = check_doorbell(SimpleNamespace(config=config))
    assert BROKER in finding.message and USER not in finding.message
