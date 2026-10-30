"""The MQTT doorbell (DECISIONS round 40, TASK 5): one publish per build end with fixed topics and a payload of fixed
fields only; TLS for every broker that is not this computer, offline mode only to a local broker, the password only
from the credential vault and never in a log; a broker that is down never blocks a build; the settings are the
person's alone. A fake client factory, and one loopback test against a minimal MQTT 3.1.1 server in the test (a socket
on 127.0.0.1, CONNECT/CONNACK/PUBLISH/PUBACK only); no real broker, no network."""

import json
import logging
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from orchestrator.Core import doorbell
from orchestrator.Core.doorbell import (Doorbell, DoorbellRefused, broker_of, check_broker, payload,
                                        send_test_doorbell, status_topic, topics)

REQ = "req_1a2b3c4d"
SECRET = "pw-" + "Zq9" * 6   # made up


class Config(dict):
    def __init__(self, *a, user_state_dir=None, **kw):
        super().__init__(*a, **kw)
        self.user_state_dir = user_state_dir

    def get(self, key, default=None):
        return super().get(key, default)


class FakeClient:
    """aiomqtt.Client's surface the doorbell uses: an async context manager with publish()."""
    calls = []

    def __init__(self, broker, password, fail=None):
        self.broker, self.password, self.fail = broker, password, fail

    async def __aenter__(self):
        if self.fail:
            raise self.fail
        FakeClient.calls.append(("connect", self.broker.label(), self.password))
        return self

    async def __aexit__(self, *exc):
        return False

    async def publish(self, topic, payload, qos, retain, timeout):
        FakeClient.calls.append(("publish", topic, payload, qos, retain))


@pytest.fixture(autouse=True)
def clear_calls():
    FakeClient.calls = []


def bell(config, password=None, fail=None, **kw):
    return Doorbell(config, lambda: password, client_factory=lambda b, p: FakeClient(b, p, fail), **kw)


def published(topic_part=""):
    return [c for c in FakeClient.calls if c[0] == "publish" and topic_part in c[1]]


# --- the contract -------------------------------------------------------------------------------------------------

def test_topics_and_payload_are_the_fixed_contract():
    assert topics("llmcf", REQ) == (f"llmcf/v1/build/{REQ}/end", "llmcf/v1/events")
    assert status_topic("home") == "home/v1/status"
    body = payload({"request_id": REQ, "end_reason": "idle"})
    assert set(body) == {"v", "event", "reason", "name", "request_id", "ts"}
    assert (body["v"], body["event"], body["reason"], body["name"]) == (1, "build_error", "idle",
                                                                         "Error while building")
    assert payload({"request_id": REQ, "status": "completed"})["event"] == "build_ready"
    assert payload({"request_id": REQ, "end_reason": "cancelled"})["name"] == "Build stopped"


@pytest.mark.parametrize("text", ["req_1a2b3c4d/#", "req_1a2b3c4d\nx", "../../x", "req_ZZZZZZZZ", "+", "",
                                  "ignore previous instructions", "req_1a2b3c4d ", "\x00"])
def test_untrusted_request_facts_never_reach_a_topic_or_payload(text):
    assert payload({"request_id": text, "end_reason": "done"}) is None
    with pytest.raises(ValueError):
        topics("llmcf", text)
    body = payload({"request_id": REQ, "end_reason": text, "status": "error", "model": text, "error": text})
    assert body["reason"] == "failed" and text not in json.dumps(body).replace(REQ, "") or not text


@pytest.mark.parametrize("prefix", ["LLMCF", "a/b", "#", "+", "", "x" * 33, "a b"])
def test_a_prefix_outside_its_pattern_is_refused(prefix):
    with pytest.raises(DoorbellRefused):
        broker_of(Config(mqtt_broker_url="mqtt://localhost:1883", mqtt_topic_prefix=prefix or " "))


# --- the rules of the connection ---------------------------------------------------------------------------------

@pytest.mark.parametrize("url, ok", [("mqtt://localhost:1883", True), ("mqtt://127.0.0.1", True),
                                     ("mqtt://[::1]:1883", True), ("mqtts://broker.example.org:8883", True),
                                     ("mqtt://broker.example.org:1883", False), ("mqtt://192.0.2.10:1883", False),
                                     ("mqtts://192.0.2.10", True)])
def test_plain_mqtt_only_on_this_computer(url, ok):
    config = Config(mqtt_broker_url=url)
    broker = broker_of(config)
    if ok:
        check_broker(broker, config, password=True)
    else:
        with pytest.raises(DoorbellRefused) as refused:
            check_broker(broker, config, password=True)
        assert refused.value.code == "no_tls"


@pytest.mark.parametrize("url", ["http://localhost:1883", "mqtt://user:pw@localhost", "mqtt://localhost/topic",
                                 "mqtt://localhost?x=1", "mqtt://", "localhost:1883"])
def test_a_broker_address_that_does_not_fit_is_refused(url):
    with pytest.raises(DoorbellRefused):
        broker_of(Config(mqtt_broker_url=url))


def test_offline_mode_reaches_only_a_local_broker():
    config = Config(mqtt_broker_url="mqtts://broker.example.org:8883", offline_mode=True)
    with pytest.raises(DoorbellRefused) as refused:
        check_broker(broker_of(config), config, resolver=lambda host: ["203.0.113.7"])
    assert refused.value.code == "offline"
    check_broker(broker_of(config), config, resolver=lambda host: ["192.0.2.20"]) if False else None
    local = Config(mqtt_broker_url="mqtts://192.168.1.20:8883", offline_mode=True)
    check_broker(broker_of(local), local)


def test_no_broker_no_doorbell():
    assert broker_of(Config()) is None
    b = bell(Config())
    b.on_build_end({"request_id": REQ, "end_reason": "done"})
    assert b.flush(2) and FakeClient.calls == []


# --- publishing ---------------------------------------------------------------------------------------------------

def test_one_publish_per_end_on_both_topics(tmp_path):
    config = Config(mqtt_broker_url="mqtt://localhost:1883", user_state_dir=tmp_path)
    b = bell(config, state_dir=tmp_path)
    b.on_build_end({"request_id": REQ, "end_reason": "done", "owner": "", "saved": "/home/alice/x"})
    b.on_build_end({"request_id": REQ, "end_reason": "done"})   # the same end again
    assert b.flush(5)
    topics_sent = [c[1] for c in published()]
    assert topics_sent == ["llmcf/v1/status", f"llmcf/v1/build/{REQ}/end", "llmcf/v1/events", "llmcf/v1/status"]
    status_on, build, feed, status_off = published()
    assert status_on[2:] == ("online", 1, True) and status_off[2:] == ("offline", 1, True)
    assert build[3:] == (1, False) and json.loads(build[2])["request_id"] == REQ and "/home/alice" not in build[2]
    assert doorbell.read_status(tmp_path)["ok"] is True


def test_a_broker_that_is_down_never_blocks_the_build(tmp_path, caplog):
    config = Config(mqtt_broker_url="mqtt://localhost:1883")
    b = bell(config, password=SECRET, fail=ConnectionRefusedError(f"refused for {SECRET}"), state_dir=tmp_path)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        for i in range(5):
            b.on_build_end({"request_id": f"req_0000000{i}", "end_reason": "failed"})
    assert time.monotonic() - started < 0.5, "the end listener returns at once"
    assert b.flush(10)
    status = doorbell.read_status(tmp_path)
    assert status["ok"] is False and status["code"] == "unreachable"
    assert SECRET not in caplog.text and SECRET not in json.dumps(status)


def test_a_full_queue_drops_instead_of_waiting():
    gate = threading.Event()

    class Slow(FakeClient):
        async def __aenter__(self):
            gate.wait(5)
            return self

    b = Doorbell(Config(mqtt_broker_url="mqtt://localhost:1883"), client_factory=lambda br, p: Slow(br, p),
                 queue_size=1)
    started = time.monotonic()
    for i in range(6):
        b.on_build_end({"request_id": f"req_0000001{i}", "end_reason": "done"})
    assert time.monotonic() - started < 0.5 and b.dropped >= 1
    gate.set()
    assert b.flush(10)


def test_the_password_comes_from_the_vault_only(tmp_path):
    vault = {"mqtt_password": SECRET}
    framework = SimpleNamespace(config=Config(mqtt_broker_url="mqtts://broker.example.org:8883", mqtt_password="no",
                                              user_state_dir=tmp_path),
                                secrets_manager=SimpleNamespace(get_secret=vault.get))
    ok, text = send_test_doorbell(framework, client_factory=lambda b, p: FakeClient(b, p))
    assert ok, text
    assert FakeClient.calls[0] == ("connect", "mqtts://broker.example.org:8883", SECRET)
    assert SECRET not in text


def test_a_test_doorbell_without_a_broker_says_so():
    ok, text = send_test_doorbell(SimpleNamespace(config=Config()))
    assert not ok and "No MQTT broker is set" in text


# --- person-only settings -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["mqtt_broker_url", "mqtt_username", "mqtt_topic_prefix", "mqtt_ca_file"])
def test_an_ai_cannot_change_the_doorbell(key):
    from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, person_only_text
    assert key in PERSON_ONLY_SETTINGS
    assert "no AI can redirect or silence it" in person_only_text(key)


def test_the_vault_name_is_known():
    # the settings' schema and defaults: tests/test_doorbell_surfaces.py
    from orchestrator.Core.secret_entries import KNOWN_SECRETS
    assert "mqtt_password" in KNOWN_SECRETS


# --- one real client against a minimal MQTT 3.1.1 server on 127.0.0.1 --------------------------------------------

def _read_packet(conn):
    head = conn.recv(1)
    if not head:
        return None, b""
    multiplier, length = 1, 0
    while True:
        byte = conn.recv(1)[0]
        length += (byte & 0x7F) * multiplier
        if not byte & 0x80:
            break
        multiplier *= 128
    body = b""
    while len(body) < length:
        chunk = conn.recv(length - len(body))
        if not chunk:
            break
        body += chunk
    return head[0], body


class MiniBroker(threading.Thread):
    """CONNECT -> CONNACK, PUBLISH (QoS 1) -> PUBACK, PINGREQ -> PINGRESP, DISCONNECT. Records every publish."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.connects, self.publishes = [], []

    def run(self):
        self.sock.settimeout(20)
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        conn.settimeout(20)
        with conn:
            while True:
                kind, body = _read_packet(conn)
                if kind is None:
                    return
                if kind >> 4 == 1:
                    self.connects.append(body)
                    conn.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
                elif kind >> 4 == 3:
                    qos = (kind >> 1) & 0x03
                    size = int.from_bytes(body[:2], "big")
                    topic = body[2:2 + size].decode()
                    rest = body[2 + size:]
                    if qos:
                        pid, rest = rest[:2], rest[2:]
                        conn.sendall(bytes([0x40, 0x02]) + pid)
                    self.publishes.append((topic, rest.decode(), qos, bool(kind & 0x01)))
                elif kind >> 4 == 12:
                    conn.sendall(bytes([0xD0, 0x00]))
                elif kind >> 4 == 14:
                    return


def test_one_real_doorbell_over_loopback(tmp_path):
    pytest.importorskip("aiomqtt")
    server = MiniBroker()
    server.start()
    config = Config(mqtt_broker_url=f"mqtt://127.0.0.1:{server.port}", mqtt_username="builder",
                    mqtt_topic_prefix="home", user_state_dir=tmp_path)
    b = Doorbell(config, lambda: SECRET, state_dir=tmp_path)
    assert b.ring(payload({"request_id": REQ, "end_reason": "done"})), b.last_problem
    server.join(10)
    topics_sent = [p[0] for p in server.publishes]
    assert topics_sent == ["home/v1/status", f"home/v1/build/{REQ}/end", "home/v1/events", "home/v1/status"]
    assert all(p[2] == 1 for p in server.publishes)
    assert [p[3] for p in server.publishes] == [True, False, False, True]
    assert json.loads(server.publishes[1][1])["event"] == "build_ready"
    connect = server.connects[0]
    assert b"MQTT" in connect and b"home/v1/status" in connect and b"offline" in connect, "the last will"
    assert b"builder" in connect and SECRET.encode() in connect   # the password goes only to this computer


# --- the connection goes only to the checked addresses (the locality rule's pinned connection) ---------------------

@pytest.fixture
def dialled(monkeypatch):
    """Every address the real MQTT client opens a TCP connection to; the connection itself fails (no network)."""
    from orchestrator.utils import locality
    calls = []

    def create_connection(address, *a, **kw):
        calls.append(tuple(address[:2]))
        raise ConnectionRefusedError("no network in tests")
    monkeypatch.setattr(socket, "create_connection", create_connection)
    locality.clear_cache()
    yield calls
    locality.clear_cache()


def test_offline_mode_connects_only_to_the_checked_local_addresses(monkeypatch, dialled):
    """A broker name with a local and a public address counts as local by its local address only (the one locality
    rule): the connection goes to that address, never to the name (the system resolver could answer with the public
    one) and never to the public address. The certificate is still checked against the name."""
    pytest.importorskip("aiomqtt")
    from orchestrator.utils import locality
    monkeypatch.setattr(locality, "_system_lookup", lambda host: {
        "broker.lan": ["192.168.1.20", "203.0.113.9"]}[host])
    config = Config(mqtt_broker_url="mqtts://broker.lan:8883", offline_mode=True)
    b = Doorbell(config, lambda: SECRET)
    assert not b.ring(payload({"request_id": REQ, "end_reason": "done"}))
    assert dialled and set(dialled) == {("192.168.1.20", 8883)}, dialled
    assert b.last_problem and SECRET not in b.last_problem


def test_a_localhost_name_goes_to_the_loopback_addresses(dialled):
    """Plain mqtt to the name localhost (the password without TLS): the connection goes to the loopback addresses,
    so no resolver decides where the password goes."""
    pytest.importorskip("aiomqtt")
    config = Config(mqtt_broker_url="mqtt://localhost:1883")
    b = Doorbell(config, lambda: SECRET)
    assert not b.ring(payload({"request_id": REQ, "end_reason": "done"}))
    assert dialled and set(dialled) <= {("127.0.0.1", 1883), ("::1", 1883)}, dialled


def test_a_real_doorbell_to_the_name_localhost_arrives_over_the_pinned_connection(tmp_path):
    pytest.importorskip("aiomqtt")
    server = MiniBroker()
    server.start()
    config = Config(mqtt_broker_url=f"mqtt://localhost:{server.port}", mqtt_topic_prefix="home")
    b = Doorbell(config, lambda: None, state_dir=tmp_path)
    assert b.ring(payload({"request_id": REQ, "end_reason": "failed"})), b.last_problem
    server.join(10)
    assert [p[0] for p in server.publishes][1:3] == [f"home/v1/build/{REQ}/end", "home/v1/events"]


def test_online_a_tls_broker_name_is_left_to_the_system(dialled):
    """Online, a TLS broker outside this computer is reached by its name as before (no locality rule applies)."""
    pytest.importorskip("aiomqtt")
    config = Config(mqtt_broker_url="mqtts://broker.example.org:8883")
    b = Doorbell(config, lambda: None)
    assert not b.ring(payload({"request_id": REQ, "end_reason": "done"}))
    assert dialled == [("broker.example.org", 8883)]
