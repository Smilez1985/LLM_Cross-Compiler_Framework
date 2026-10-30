#!/usr/bin/env python3
"""
The MQTT doorbell (DECISIONS round 40, TASK 5): every build end, published to an MQTT broker the person points to,
so the person's phone, a home automation or an external AI client wakes up and fetches the details over MCP. Until
the gateway's built-in broker (rumqttd, ROADMAP) exists the person brings a broker (Mosquitto, NanoMQ, ...); the
built-in broker takes over the same topics and payload.

Contract (DESIGN, the same later on the built-in broker):
- topics `<prefix>/v1/build/<request_id>/end` (one build) and `<prefix>/v1/events` (one feed for all), QoS 1, not
  retained; `<prefix>/v1/status` "online" (retained) while a framework process is connected, "offline" (retained,
  also as the last will).
- payload JSON with fixed fields only: {"v": 1, "event": build_ready|build_error|build_stopped, "reason": one of the
  five end reasons, "name": the speaking name, "request_id": "req_<8 hex>", "ts": UTC time}. No text from outside
  the framework, neither in a topic nor in the payload (the request id is checked against its pattern).
- the framework publishes only; it never subscribes to its own topics (subscribers are read-only by design: set the
  broker's ACLs so only the framework may publish under the prefix).

Settings (all person-only, llm/settings_change.PERSON_ONLY_SETTINGS: an AI may not redirect or silence it):
mqtt_broker_url (empty: no doorbell; mqtt://host:port or mqtts://host:port), mqtt_username, mqtt_topic_prefix
(default "llmcf", [a-z0-9_-]{1,32}), mqtt_ca_file (optional). The password only in the credential vault (secret name
"mqtt_password"), never in the config file or a log.

Locality (utils/locality, the one rule): in offline mode only a broker that counts as local; TLS (mqtts, the
certificate checked) for every broker that is not this computer (loopback); plain mqtt only on loopback, so a
password never leaves this computer without TLS. The connection goes only to the addresses the rule checked
(pinned_addresses, as every other connection of offline mode: utils/locality.connect_addresses): in offline mode the
local addresses of the broker's name (never a public one of a name with both), and the loopback addresses for the
name localhost, so no resolver decides where the doorbell and the password go; the certificate is still checked
against the broker's name.

The publisher runs on its own thread with a bounded queue and timeouts: a broker that is down never blocks or fails
a build; a failure is logged without the password and kept for Mojo's check line (doorbell_status.json in the per-user
folder). The MQTT client (aiomqtt over paho-mqtt) is imported only when a doorbell is sent.
"""

import asyncio
import atexit
import json
import os
import queue
import re
import socket
import ssl
import threading
import time
import weakref
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from orchestrator.Core.build_end import END_REASONS, EVENT_OF, NAME_OF_EVENT
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

logger = get_logger("Doorbell")

SETTINGS = ("mqtt_broker_url", "mqtt_username", "mqtt_topic_prefix", "mqtt_ca_file")
PASSWORD_SECRET = "mqtt_password"
DEFAULT_PREFIX = "llmcf"
PREFIX_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{8}$")
TEST_REQUEST_ID = "req_00000000"     # the request id of a test doorbell
PORTS = {"mqtt": 1883, "mqtts": 8883}
PAYLOAD_VERSION = 1
QOS = 1
QUEUE_SIZE = 100
CONNECT_TIMEOUT = 10.0
PUBLISH_TIMEOUT = 10.0
STATUS_FILE = "doorbell_status.json"
# A process that ends with doorbells still queued (every `llm-cli build start`: the publisher thread is a daemon) sends
# them first, at most this many seconds in all (a broker that is down then costs the process this long, never more)
EXIT_FLUSH = 15.0


class DoorbellRefused(ValueError):
    """A broker setting the doorbell may not use; `code` names why (bad_url, no_tls, offline, bad_prefix, ...)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Broker:
    """The person's broker: scheme mqtt or mqtts, host, port, user name, topic prefix, CA file (or "")."""
    scheme: str
    host: str
    port: int
    username: str = ""
    prefix: str = DEFAULT_PREFIX
    ca_file: str = ""
    # The checked addresses the connection goes to (pinned_addresses); empty: the host itself (an address, or a name
    # online, which the system resolves)
    addresses: Tuple[str, ...] = ()

    @property
    def tls(self) -> bool:
        return self.scheme == "mqtts"

    def label(self) -> str:
        """How the broker is named in texts for the person (never the password; the user name is not shown)."""
        return f"{self.scheme}://{self.host}:{self.port}"


def _get(config: Any, key: str, default: Any = "") -> Any:
    get = getattr(config, "get", None)
    try:
        value = get(key, default) if callable(get) else getattr(config, key, default)
    except Exception:  # noqa: BLE001 - an unreadable setting counts as unset
        return default
    return default if value is None else value


def broker_of(config: Any) -> Optional[Broker]:
    """The broker of the settings, None when mqtt_broker_url is empty (no doorbell). DoorbellRefused for a value that
    does not fit (the check of the locality and TLS rules is check_broker)."""
    from urllib.parse import urlsplit
    url = str(_get(config, "mqtt_broker_url", "") or "").strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise DoorbellRefused("The MQTT broker address is not a valid URL (mqtt://host:port or mqtts://host:port).",
                              "bad_url") from None
    scheme = (parts.scheme or "").lower()
    if scheme not in PORTS or not parts.hostname:
        raise DoorbellRefused("The MQTT broker address must be mqtt://host:port or mqtts://host:port.", "bad_url")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise DoorbellRefused("The MQTT broker address holds only the scheme, host and port: the user name goes into "
                              "its own setting and the password into the credential vault.", "bad_url")
    prefix = str(_get(config, "mqtt_topic_prefix", DEFAULT_PREFIX) or DEFAULT_PREFIX).strip()
    if not PREFIX_RE.fullmatch(prefix):
        raise DoorbellRefused("The MQTT topic prefix is 1 to 32 characters: a-z, 0-9, _ and -.", "bad_prefix")
    username = str(_get(config, "mqtt_username", "") or "").strip()
    if len(username) > 128 or any(ch in username for ch in "\r\n\x00"):
        raise DoorbellRefused("The MQTT user name is one line of at most 128 characters.", "bad_username")
    ca_file = str(_get(config, "mqtt_ca_file", "") or "").strip()
    return Broker(scheme, parts.hostname.lower(), int(port or PORTS[scheme]), username, prefix, ca_file)


def _is_loopback(broker: Broker, config: Any, resolver: Optional[Callable[[str], Any]] = None) -> bool:
    """Whether the broker is surely this computer: the name localhost, or a loopback address. Any other name needs
    TLS (its addresses may change), whatever it resolves to now; so does a name under .localhost: it counts as local
    for the locality rule, but the MQTT client asks the system resolver for it, which may answer with any address."""
    from orchestrator.utils.locality import LOCALHOST_NAMES, address_kind, normal_host, parse_address
    if parse_address(broker.host) is not None:
        return address_kind(broker.host) == "loopback"
    return normal_host(broker.host) in LOCALHOST_NAMES


def check_broker(broker: Broker, config: Any, *, password: bool = False,
                 resolver: Optional[Callable[[str], Any]] = None) -> None:
    """The rules of the doorbell's connection, DoorbellRefused with the plain text when one is broken:
    offline mode reaches only a local broker (utils/locality, the one rule); plain mqtt only on loopback, TLS for
    every other broker (so a password never leaves this computer without TLS)."""
    from orchestrator.utils.locality import check_url, offline_mode
    loopback = _is_loopback(broker, config, resolver)
    if not broker.tls and not loopback:
        raise DoorbellRefused(f"The MQTT broker {broker.label()} is not on this computer, so the doorbell needs TLS: "
                              "use mqtts:// (the certificate is checked).", "no_tls")
    if broker.ca_file and not Path(broker.ca_file).is_file():
        raise DoorbellRefused("The CA file of the MQTT broker (setting mqtt_ca_file) does not exist.", "no_ca_file")
    if offline_mode(config) and not loopback:
        verdict = check_url(f"{broker.scheme}://{broker.host}:{broker.port}", config, resolver=resolver)
        if not verdict.local:
            raise DoorbellRefused(verdict.refusal("the MQTT doorbell"), "offline")
    del password   # with TLS beyond loopback, a password is sent only over TLS (or never leaves this computer)


def pinned_addresses(broker: Broker, config: Any) -> Tuple[str, ...]:
    """The addresses a connection to `broker` may go to, in turn (utils/locality.connect_addresses, from the check's
    cache): the loopback addresses of the name localhost; in offline mode the checked local addresses of a name
    (never its public ones); () for an address (it is its own) and for a name online (the system resolves it).
    DoorbellRefused "offline" when the name no longer counts as local."""
    from orchestrator.utils.locality import LocalityRefused, connect_addresses, offline_mode, parse_address
    if parse_address(broker.host) is not None:
        return ()
    if _is_loopback(broker, config):
        return connect_addresses(broker.host, None, "the MQTT doorbell")
    if not offline_mode(config):
        return ()
    try:
        return connect_addresses(broker.host, config, "the MQTT doorbell")
    except LocalityRefused as e:
        raise DoorbellRefused(str(e), "offline") from None


def _pin(paho_client: Any, broker: Broker) -> None:
    """The paho client opens its TCP connection only to broker.addresses, tried in turn (the TLS layer still checks
    the certificate against the broker's name); every one failed: the lookup is dropped (locality.forget)."""
    from orchestrator.utils.locality import forget
    if not callable(getattr(paho_client, "_create_socket_connection", None)):
        raise RuntimeError("the MQTT client cannot be held to the checked addresses")

    def connect() -> socket.socket:
        failed: Optional[OSError] = None
        source = (getattr(paho_client, "_bind_address", ""), getattr(paho_client, "_bind_port", 0))
        for address in broker.addresses:
            try:
                return socket.create_connection((address, broker.port),
                                                timeout=getattr(paho_client, "_connect_timeout", CONNECT_TIMEOUT),
                                                source_address=source)
            except OSError as e:   # the next checked address
                failed = e
        forget(broker.host)
        raise failed or OSError("no checked address")
    paho_client._create_socket_connection = connect


def topics(prefix: str, request_id: str) -> Tuple[str, str]:
    """(the build's topic, the feed topic); ValueError for a prefix or request id outside their patterns."""
    if not PREFIX_RE.fullmatch(prefix or "") or not REQUEST_ID_RE.fullmatch(request_id or ""):
        raise ValueError("topic parts outside their patterns")
    return f"{prefix}/v1/build/{request_id}/end", f"{prefix}/v1/events"


def status_topic(prefix: str) -> str:
    if not PREFIX_RE.fullmatch(prefix or ""):
        raise ValueError("topic prefix outside its pattern")
    return f"{prefix}/v1/status"


def payload(facts: Mapping[str, Any], now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The doorbell's payload of a build end's facts, fixed fields only; None when the facts do not name a valid
    request id or end reason (nothing is sent then)."""
    rid = facts.get("request_id")
    reason = facts.get("end_reason")
    if reason not in END_REASONS:
        reason = {"completed": "done", "error": "failed", "cancelled": "cancelled"}.get(str(facts.get("status") or ""))
    if not isinstance(rid, str) or not REQUEST_ID_RE.fullmatch(rid) or reason not in END_REASONS:
        return None
    event = EVENT_OF[reason]
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"v": PAYLOAD_VERSION, "event": event, "reason": reason, "name": NAME_OF_EVENT[event], "request_id": rid,
            "ts": when}


# --- the MQTT client (aiomqtt over paho-mqtt, imported only when a doorbell is sent) --------------------------------

def tls_context(broker: Broker) -> ssl.SSLContext:
    """The certificate is always checked: the system's CAs, or the person's CA file."""
    context = ssl.create_default_context(cafile=broker.ca_file or None)
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def aiomqtt_client(broker: Broker, password: Optional[str]):
    """The real client: aiomqtt.Client with the last will "offline" (retained) on the status topic."""
    import aiomqtt
    will = aiomqtt.Will(status_topic(broker.prefix), payload="offline", qos=QOS, retain=True)
    client = aiomqtt.Client(hostname=broker.host, port=broker.port, username=broker.username or None,
                            password=password or None, tls_context=tls_context(broker) if broker.tls else None,
                            will=will, timeout=CONNECT_TIMEOUT, identifier=f"llmcf-{os.getpid()}",
                            protocol=aiomqtt.ProtocolVersion.V311)
    if broker.addresses:
        _pin(getattr(client, "_client", None), broker)
    return client


ClientFactory = Callable[[Broker, Optional[str]], Any]


async def _send(client_factory: ClientFactory, broker: Broker, password: Optional[str],
                messages: Tuple[Tuple[str, str, bool], ...]) -> None:
    async with client_factory(broker, password) as client:
        await client.publish(status_topic(broker.prefix), payload="online", qos=QOS, retain=True,
                             timeout=PUBLISH_TIMEOUT)
        for topic, body, retain in messages:
            await client.publish(topic, payload=body, qos=QOS, retain=retain, timeout=PUBLISH_TIMEOUT)
        await client.publish(status_topic(broker.prefix), payload="offline", qos=QOS, retain=True,
                             timeout=PUBLISH_TIMEOUT)


def send_now(broker: Broker, password: Optional[str], body: Dict[str, Any],
             client_factory: Optional[ClientFactory] = None) -> None:
    """Connects, publishes one doorbell (the build's topic and the feed) and disconnects; raises on any failure. Runs
    its own event loop (a selector loop: paho needs one, also on Windows)."""
    build_topic, feed_topic = topics(broker.prefix, body["request_id"])
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    loop = asyncio.SelectorEventLoop()
    try:
        loop.run_until_complete(asyncio.wait_for(
            _send(client_factory or aiomqtt_client, broker, password,
                  ((build_topic, text, False), (feed_topic, text, False))),
            CONNECT_TIMEOUT + 4 * PUBLISH_TIMEOUT))
    finally:
        loop.close()


def password_of(framework: Any) -> Optional[str]:
    """The broker's password from the credential vault (secret mqtt_password), None when none is stored."""
    secrets = getattr(framework, "secrets_manager", None) or getattr(framework, "secrets", None)
    get = getattr(secrets, "get_secret", None)
    if not callable(get):
        return None
    try:
        return get(PASSWORD_SECRET) or None
    except Exception:  # noqa: BLE001 - a locked vault: no password
        return None


def _error_text(error: BaseException, password: Optional[str]) -> str:
    text = redact_secrets(f"{error.__class__.__name__}: {error}")
    if password:
        text = text.replace(password, "***")
    return text[:300]


class Doorbell:
    """Publishes every build end of this process to the person's broker (see the module text). on_build_end never
    blocks: it puts the event into a bounded queue that one daemon thread sends."""

    def __init__(self, config: Any, password: Callable[[], Optional[str]] = lambda: None, *,
                 client_factory: Optional[ClientFactory] = None, state_dir: Optional[Path] = None,
                 queue_size: int = QUEUE_SIZE):
        self.config = config
        self._password = password
        self._factory = client_factory
        self._state_dir = Path(state_dir) if state_dir else None
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=max(1, queue_size))
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._sent_for: set = set()
        self.sent = 0
        self.dropped = 0
        self.last_problem: Optional[str] = None
        _LIVE.add(self)

    def on_build_end(self, facts: Dict[str, Any]) -> None:
        """The orchestrator's end listener: one doorbell per request id; nothing when no broker is set."""
        try:
            if not str(_get(self.config, "mqtt_broker_url", "") or "").strip():
                return
            body = payload(facts)
            if body is None:
                return
            with self._lock:
                if body["request_id"] in self._sent_for:
                    return
                self._sent_for.add(body["request_id"])
            self._queue.put_nowait(body)
            self._start()
        except queue.Full:
            self.dropped += 1
            logger.warning("A doorbell was dropped: the queue of the MQTT doorbell is full (is the broker down?).")
        except Exception as e:  # noqa: BLE001 - a doorbell never changes a build
            logger.warning(f"No doorbell for a build's end: {e.__class__.__name__}")

    def _start(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="mqtt-doorbell", daemon=True)
                self._thread.start()

    def _work(self) -> None:
        while True:
            try:
                body = self._queue.get(timeout=1.0)
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._thread = None
                        return
                continue
            self.ring(body)

    def ring(self, body: Dict[str, Any]) -> bool:
        """Sends one doorbell now (also the test doorbell); True when the broker took it. Never raises."""
        password = None
        try:
            broker = broker_of(self.config)
            if broker is None:
                return False
            password = self._password()
            check_broker(broker, self.config, password=bool(password))
            broker = replace(broker, addresses=pinned_addresses(broker, self.config))
            send_now(broker, password, body, self._factory)
        except DoorbellRefused as e:
            self._record(False, str(e), e.code)
            logger.warning(f"The MQTT doorbell was not sent: {e}")
            return False
        except Exception as e:  # noqa: BLE001 - a broker that is down never fails anything
            text = _error_text(e, password)
            self._record(False, f"The MQTT broker could not be reached or refused the doorbell ({text}).",
                         "unreachable")
            logger.warning(f"The MQTT doorbell was not sent: {text}")
            return False
        self.sent += 1
        self._record(True, "", "")
        return True

    def flush(self, timeout: float = 30.0) -> bool:
        """Waits until every queued doorbell is sent (or failed), at most `timeout` seconds."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                thread = self._thread
            if self._queue.empty() and (thread is None or not thread.is_alive()):
                return True
            time.sleep(0.02)
        return False

    def _record(self, ok: bool, problem: str, code: str) -> None:
        """Keeps the last result for Mojo's check line (no password, no user name)."""
        self.last_problem = None if ok else problem
        if self._state_dir is None:
            return
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            path = self._state_dir / STATUS_FILE
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"ok": ok, "code": code, "problem": problem,
                                       "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass


_LIVE: "weakref.WeakSet[Doorbell]" = weakref.WeakSet()


@atexit.register
def _flush_at_exit() -> None:
    """The doorbells this process still has queued are sent before it ends (at most EXIT_FLUSH seconds in all)."""
    deadline = time.monotonic() + EXIT_FLUSH
    for bell in list(_LIVE):
        try:
            bell.flush(max(0.0, deadline - time.monotonic()))
        except Exception:  # noqa: BLE001 - the process ends anyway
            pass


def read_status(state_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    """The last doorbell result of any process (doorbell_status.json), None when none was sent yet."""
    if not state_dir:
        return None
    try:
        data = json.loads((Path(state_dir) / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def send_test_doorbell(framework: Any, client_factory: Optional[ClientFactory] = None) -> Tuple[bool, str]:
    """Sends a test doorbell now (CLI `config doorbell --test`, the GUI's Send a test doorbell): (ok, plain text).
    The test's payload is a build_ready of the request id req_00000000."""
    config = getattr(framework, "config", framework)
    try:
        broker = broker_of(config)
    except DoorbellRefused as e:
        return False, str(e)
    if broker is None:
        return False, "No MQTT broker is set (setting mqtt_broker_url), so there is no doorbell."
    bell = Doorbell(config, lambda: password_of(framework), client_factory=client_factory,
                    state_dir=getattr(config, "user_state_dir", None))
    body = payload({"request_id": TEST_REQUEST_ID, "end_reason": "done"})
    if bell.ring(body):
        return True, f"A test doorbell was sent to {broker.label()} (topics {broker.prefix}/v1/events and " \
                     f"{broker.prefix}/v1/build/{TEST_REQUEST_ID}/end)."
    return False, bell.last_problem or "The test doorbell was not sent."


def apply_settings(config: Any, changes: Mapping[str, str]) -> Optional[Broker]:
    """The person's change of the doorbell's settings (CLI `config doorbell`, the GUI's settings): checked by the same
    rules as a send (broker_of, check_broker) and saved to config.yml; on a refusal or a failed save every setting
    stays as it was. Returns the broker (None: no doorbell). DoorbellRefused / OSError."""
    unknown = set(changes) - set(SETTINGS)
    if unknown:
        raise DoorbellRefused(f"Not a doorbell setting: {sorted(unknown)}", "unknown_setting")
    before = {key: _get(config, key, "") for key in changes}

    def restore() -> None:
        for key, value in before.items():
            config.set(key, value)

    try:
        for key, value in changes.items():
            if config.set(key, str(value or "").strip()) is False:   # the settings schema refused it
                if key == "mqtt_topic_prefix":
                    raise DoorbellRefused("The MQTT topic prefix is 1 to 32 characters: a-z, 0-9, _ and -.",
                                          "bad_prefix")
                raise DoorbellRefused(f"The setting {key} does not fit its rule.", "bad_setting")
        broker = broker_of(config)
        if broker is not None:
            check_broker(broker, config)
    except DoorbellRefused:
        restore()
        raise
    save = getattr(config, "save_user_config", None)
    if callable(save) and not save():
        restore()
        raise OSError("The settings could not be saved.")
    return broker


def attach_doorbell(framework: Any) -> Optional[Doorbell]:
    """The process's doorbell on the orchestrator's end listener (every process that runs a build); it sends nothing
    until the person sets a broker."""
    orchestrator = getattr(framework, "orchestrator", None)
    add = getattr(orchestrator, "add_end_listener", None)
    if not callable(add):
        return None
    config = getattr(framework, "config", None)
    bell = Doorbell(config, lambda: password_of(framework), state_dir=getattr(config, "user_state_dir", None))
    add(bell.on_build_end)
    return bell


__all__ = ["SETTINGS", "PASSWORD_SECRET", "DEFAULT_PREFIX", "Broker", "Doorbell", "DoorbellRefused", "broker_of",
           "check_broker", "pinned_addresses", "apply_settings", "topics", "status_topic", "payload", "send_now",
           "send_test_doorbell", "attach_doorbell", "read_status", "password_of", "TEST_REQUEST_ID"]
