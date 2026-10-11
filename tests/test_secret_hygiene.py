"""Secret hygiene at runtime: redaction with the shared pattern table, the logging redaction on every logger
(credential headers, query parameters, key formats, tracebacks), --debug-http, SecretStr, and what adapters, the
LLM client and their errors show."""

import copy
import io
import json
import logging
import pickle
import re
import sys
import time

import pytest

from orchestrator.llm import AuthFailed, LLMClient
from orchestrator.security import redaction, secret_patterns
from orchestrator.security.redaction import SDK_LOGGERS, RedactingFilter, SecretStr
from orchestrator.utils.helpers import redact_secrets
from test_llm import (FakeConfig, FakeHTTP, FakeSecrets, anthropic_error, ask, claude_message, completion,
                      responses_body, text_block)
from test_secret_scan import REDACT_ONLY_SAMPLES, SAMPLES, body

TOKEN = "tok" + body(37)  # a credential in no known format: only the header and parameter rules can find it
KEYS = {"anthropic_api_key": SAMPLES["Anthropic key"], "openai_api_key": SAMPLES["OpenAI key"],
        "openrouter_api_key": SAMPLES["OpenRouter key"]}


@pytest.fixture
def redacted_logging():
    """The redaction as setup_logging() installs it; the SDK loggers are put back afterwards."""
    saved = {name: logging.getLogger(name).level for name in SDK_LOGGERS}
    redaction.install_log_redaction()
    yield
    redaction.configure_sdk_loggers(logging.INFO, debug_http=False)
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


# --- the shared table at runtime --------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", sorted(p.name for p in secret_patterns.PATTERNS if p.redact))
def test_each_rule_is_redacted_at_runtime(kind):
    sample = {**SAMPLES, **REDACT_ONLY_SAMPLES}[kind]
    rule = next(p for p in secret_patterns.PATTERNS if p.name == kind)
    match = rule.regex.search(sample)
    secret = match.group("secret") if "secret" in rule.regex.groupindex else match.group(0)
    redacted = redact_secrets(f"request failed:\n{sample}\nretrying")
    assert secret not in redacted and "REDACTED" in redacted and redacted.endswith("\nretrying")


def test_redaction_keeps_what_is_not_secret():
    redacted = redact_secrets("https://deploy:" + "pw" + body(10) + "@registry.internal.corp/v2 and client_secret: '"
                              + body(30) + "'")
    assert redacted == "https://deploy:REDACTED@registry.internal.corp/v2 and client_secret: 'REDACTED'"
    # Build logs and model settings that only look like credentials stay readable.
    for text in ("MAX_TOKENS=16000", "TOKENIZER_PATH=/models/qwen/tokenizer.json", "NUM_TOKENS_LIMIT=4096",
                 "eos_token = '<|im_end|>'", '"pad_token": "<|finetune_right_pad_id|>"',
                 "EOS_TOKEN=<|reserved_special_token_250|>", "Bearer authentication is required",
                 "max_tokens: 16000", 'given = headers.get(b"authorization", b"")'):
        assert redact_secrets(text) == text
    # Source code keeps its shape: only the quoted value goes, string prefix included.
    assert redact_secrets('headers={"Authorization": f"Bearer {key}"}') == 'headers={"Authorization": f"REDACTED"}'
    assert redact_secrets("{'x-api-key': u'" + TOKEN + "'}") == "{'x-api-key': u'REDACTED'}"


@pytest.mark.parametrize("text, secret", [
    ("ANTHROPIC_" + "API_KEY='" + TOKEN + "'", TOKEN),
    ("docker run -e HF_" + "TOKEN=" + TOKEN + " image", TOKEN),
    ("AWS_SECRET_" + "ACCESS_KEY: " + TOKEN, TOKEN),
    ('$env:OPENAI_' + 'API_KEY="' + TOKEN + '"', TOKEN),
    ("401 from server: invalid Bearer " + TOKEN + "." + body(20), TOKEN),
    ('{"access_' + 'token": "ya29.' + TOKEN + '", "expires_in": 3599}', TOKEN),
    ("code=" + TOKEN + "&grant_type=authorization_code", TOKEN),  # form-encoded token request bodies
    ("body:\nrefresh_" + "token=" + TOKEN + "&grant_type=refresh_token", TOKEN),
])
def test_credentials_in_unknown_formats_are_redacted_by_context(text, secret):
    assert secret not in redact_secrets(text) and "REDACTED" in redact_secrets(text)


@pytest.mark.parametrize("text", [
    "sk-" * 30000,                                    # a run of key prefixes without a digit
    "id_token_hint " + "a." * 30000,                  # many scheme-like starts
    "id_token_hint " + "a://" * 15000,                # many URLs without a query
    "id_token_hint=" * 8000,
    "TOKEN_" * 20000 + "x",
    "Authorization: '" * 8000,
])
def test_redaction_stays_fast_on_adversarial_text(text):
    # Redaction runs on every log record and on text from servers and build logs: never quadratic.
    start = time.perf_counter()
    redact_secrets(text)
    assert time.perf_counter() - start < 1.0


# --- logging ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("header", secret_patterns.SENSITIVE_HEADERS)
@pytest.mark.parametrize("shape", ["{h}: {v}", "{{'{h}': '{v}', 'accept': 'json'}}", '{{"{h}": "{v}"}}',
                                   "[(b'{h}', b'{v}'), (b'host', b'api')]",
                                   '"{{\\"{h}\\": \\"{v}\\", \\"accept\\": \\"json\\"}}"'])  # JSON in JSON
def test_credential_headers_are_redacted(redacted_logging, caplog, header, shape):
    caplog.set_level(logging.DEBUG, logger="httpx2")
    logging.getLogger("httpx2").debug("request headers %s", shape.format(h=header.title(), v="Bearer " + TOKEN))
    assert TOKEN not in caplog.text and "REDACTED" in caplog.text


@pytest.mark.parametrize("parameter", [p for p in secret_patterns.SENSITIVE_QUERY_PARAMETERS
                                       if p != "id_token_hint"])  # that one blanks the whole query (below)
def test_credential_query_parameters_are_redacted(redacted_logging, caplog, parameter):
    caplog.set_level(logging.INFO, logger="httpx")
    logging.getLogger("httpx").info('HTTP Request: GET https://auth.example.invalid/cb?lang=en&%s=%s&next=1 '
                                    '"HTTP/1.1 200 OK"', parameter, TOKEN)
    assert TOKEN not in caplog.text
    assert f"&{parameter}=REDACTED" in caplog.text and "?lang=en" in caplog.text and "&next=1" in caplog.text


def test_fragments_and_reauthorization_links_are_redacted(redacted_logging, caplog):
    caplog.set_level(logging.INFO, logger="orchestrator.auth")
    log = logging.getLogger("orchestrator.auth")
    log.info("callback https://app.example.invalid/done#access_token=%s&expires_in=3600", TOKEN)
    log.info("reauthorize at https://auth.example.invalid/oauth/authorize?client_id=app&id_token_hint=%s&x=1",
             SAMPLES["JSON web token"])
    assert TOKEN not in caplog.text and SAMPLES["JSON web token"] not in caplog.text
    assert "#access_token=REDACTED&expires_in=3600" in caplog.text
    assert "https://auth.example.invalid/oauth/authorize?REDACTED" in caplog.text and "client_id" not in caplog.text


@pytest.mark.parametrize("name", ["openai._base_client", "anthropic._base_client", "httpcore2.http11",
                                  "urllib3.connectionpool", "botocore.endpoint", "google.auth.transport",
                                  "azure.core.pipeline.policies.http_logging_policy", "msal.application",
                                  "some.other.library", "framework"])
def test_keys_are_redacted_whichever_logger_logs_them(redacted_logging, caplog, name):
    caplog.set_level(logging.DEBUG, logger=name)
    key = SAMPLES["Anthropic key"]
    logging.getLogger(name).debug("Request options: %s", {"headers": {"X-Api-Key": key}, "json_data": {"k": key}})
    assert key not in caplog.text and "Request options" in caplog.text
    assert all(key not in str(r.msg) and not r.args for r in caplog.records)


def test_tracebacks_and_stacks_are_redacted(redacted_logging, caplog):
    caplog.set_level(logging.INFO, logger="orchestrator.test")
    log = logging.getLogger("orchestrator.test")
    key = SAMPLES["OpenRouter key"]
    try:
        raise RuntimeError(f"401 Unauthorized for key {key}")
    except RuntimeError:
        log.exception("call failed")
    log.warning("seen here: %s", key, stack_info=True)
    assert key not in caplog.text and "call failed" in caplog.text and "RuntimeError: 401" in caplog.text
    assert all(key not in (r.exc_text or "") + (r.stack_info or "") for r in caplog.records)


def test_a_record_with_broken_arguments_is_logged_redacted(redacted_logging, caplog):
    caplog.set_level(logging.INFO, logger="orchestrator.test")
    logging.getLogger("orchestrator.test").info("two values %s %s", SAMPLES["xAI key"])
    assert SAMPLES["xAI key"] not in caplog.text and "two values" in caplog.text


class Unprintable:
    def __str__(self):
        raise ValueError("no str")

    __repr__ = __str__


@pytest.mark.parametrize("msg, args", [(Unprintable(), ()), ("value %s", (Unprintable(),)),
                                       ("%(a)s", {"a": Unprintable()}), (b"bytes " + SAMPLES["Groq key"].encode(), ())])
def test_logging_never_raises_into_the_caller(redacted_logging, caplog, msg, args):
    # A message that cannot be turned into text must not make logger.info() raise in the code that logs.
    caplog.set_level(logging.INFO, logger="orchestrator.test")
    logging.getLogger("orchestrator.test").info(msg, *([args] if isinstance(args, dict) else args))
    assert caplog.records and SAMPLES["Groq key"] not in caplog.text


def test_extra_fields_are_redacted_in_the_json_log_format(redacted_logging):
    from orchestrator.utils.logging import LogFormat, StructuredFormatter

    key = SAMPLES["Anthropic key"]
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(StructuredFormatter(LogFormat.JSON))
    log = logging.getLogger("orchestrator.test.json_extras")
    log.addHandler(handler)
    log.setLevel(logging.INFO)  # other tests' setup_logging(level=...) leaves levels on existing loggers
    try:
        log.warning("request sent", extra={"api_key": key, "headers": {"Authorization": "Bearer " + TOKEN},
                                           "attempt": 2})
        logging.LoggerAdapter(log, {"session": "Bearer " + TOKEN}).warning("adapter")
    finally:
        log.removeHandler(handler)
    out = buffer.getvalue()
    entries = [json.loads(line) for line in out.splitlines()]
    assert len(entries) == 2 and key not in out and TOKEN not in out
    assert entries[0]["extra_fields"]["attempt"] == 2 and entries[1]["message"] == "adapter"
    assert entries[0]["extra_fields"]["api_key"] == "sk-REDACTED"


def test_the_filter_redacts_records_built_elsewhere(redacted_logging):
    record = logging.makeLogRecord({"msg": "key %s", "args": (SAMPLES["Groq key"],), "levelno": logging.INFO})
    assert RedactingFilter().filter(record) is True
    assert SAMPLES["Groq key"] not in record.getMessage()
    for name in ("", *SDK_LOGGERS):
        assert any(isinstance(f, RedactingFilter) for f in logging.getLogger(name).filters), name


def test_the_json_log_format_is_redacted(redacted_logging):
    from orchestrator.utils.logging import LogFormat, StructuredFormatter

    key = SAMPLES["Hugging Face token"]
    try:
        raise ValueError(f"bad token {key}")
    except ValueError:
        record = logging.getLogger("orchestrator.test").makeRecord(
            "orchestrator.test", logging.ERROR, __file__, 1, "failed with %s", (key,), sys.exc_info())
    entry = json.loads(StructuredFormatter(LogFormat.JSON).format(record))
    assert key not in json.dumps(entry) and "bad token" in entry["exception_message"]


# --- SDK loggers and --debug-http ----------------------------------------------------------------------------

def test_sdk_debug_output_needs_debug_http(redacted_logging):
    logging.getLogger("openai._base_client").setLevel(logging.DEBUG)  # as set_global_level() leaves it
    redaction.configure_sdk_loggers(logging.DEBUG, debug_http=False)
    for name in (*SDK_LOGGERS, "openai._base_client"):
        assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
    assert logging.getLogger("httpx").isEnabledFor(logging.INFO)
    redaction.configure_sdk_loggers(logging.ERROR, debug_http=False)
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)  # never louder than the framework


def test_debug_http_logs_sdk_debug_output_to_stderr_redacted(redacted_logging, capsys):
    redaction.configure_sdk_loggers(logging.WARNING, debug_http=True)
    logging.getLogger("httpcore2.http11").debug("send_request_headers.started request=%s",
                                                [(b"authorization", b"Bearer " + TOKEN.encode())])
    err = capsys.readouterr().err
    assert "send_request_headers.started" in err and TOKEN not in err and "REDACTED" in err
    redaction.configure_sdk_loggers(logging.INFO)  # the framework's later setup_logging() keeps the choice
    assert logging.getLogger("openai").isEnabledFor(logging.DEBUG)
    redaction.configure_sdk_loggers(logging.INFO, debug_http=False)
    logging.getLogger("httpx2").info("after")
    assert not logging.getLogger("openai").isEnabledFor(logging.DEBUG) and "after" not in capsys.readouterr().err


def test_setup_logging_installs_the_redaction_and_caps_the_sdk_loggers(redacted_logging):
    from orchestrator.utils.logging import setup_logging

    framework_logger = logging.getLogger("orchestrator.test")
    try:
        setup_logging(level="DEBUG", debug_http=False)
        assert framework_logger.isEnabledFor(logging.DEBUG)
        assert not logging.getLogger("anthropic").isEnabledFor(logging.DEBUG)
        assert any(isinstance(f, RedactingFilter) for f in logging.getLogger().filters)
    finally:
        setup_logging(level="INFO")


def test_the_cli_flag_turns_on_sdk_debug_logging(redacted_logging):
    from click.testing import CliRunner

    from orchestrator import cli

    assert CliRunner().invoke(cli.cli, ["-q", "--debug-http", "mcp", "config"]).exit_code == 0
    assert logging.getLogger("openai").isEnabledFor(logging.DEBUG)
    assert CliRunner().invoke(cli.cli, ["-q", "mcp", "config"]).exit_code == 0
    assert not logging.getLogger("openai").isEnabledFor(logging.DEBUG)


# --- SecretStr --------------------------------------------------------------------------------------------------

def test_secret_str_never_shows_its_value():
    key = SAMPLES["Anthropic key"]
    secret = SecretStr(key)
    for shown in (str(secret), repr(secret), f"{secret}", "%s" % secret, "{!r}".format(secret), repr([secret]),
                  repr({"key": secret})):
        assert key not in shown and "<redacted>" in shown
    assert secret.get_secret_value() == key and redaction.reveal(secret) == key
    assert redaction.reveal("plain") == "plain" and redaction.reveal(None) is None
    assert SecretStr(secret) == secret and secret != SecretStr(key + "x") and secret != key
    assert SecretStr.wrap(None) is None and SecretStr.wrap("") is None and SecretStr.wrap(secret) is secret
    assert copy.deepcopy(secret) is secret and len({secret, SecretStr(key)}) == 1
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    with pytest.raises(TypeError):
        json.dumps({"key": secret})
    with pytest.raises(AttributeError):
        secret.value = key


def test_no_hint_or_doc_puts_a_secret_on_the_command_line():
    # Secrets never go into argv: the shell history and the process list would show them.
    from conftest import REPO_ROOT

    argv_secret = re.compile(r"secrets set \w+ +(?:[\"<$]|--password)")
    files = [*REPO_ROOT.glob("*.md"), *(REPO_ROOT / "docs").rglob("*.md"), *(REPO_ROOT / "orchestrator").rglob("*.py")]
    offenders = [f"{f.relative_to(REPO_ROOT)}:{n}" for f in files
                 for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if argv_secret.search(line)]
    assert offenders == []


def test_secret_str_compares_values_that_are_not_valid_utf8():
    # os.environ keeps undecodable bytes as lone surrogates (POSIX); comparing must not raise.
    odd = "key-\udcff" + body(20)
    assert SecretStr(odd) == SecretStr(odd) and SecretStr(odd) != SecretStr(odd + "x")


# --- adapters, the client and their errors ----------------------------------------------------------------------

def _client(tmp_path, http, provider, secrets=None):
    settings = {"ai_model": "vendor/model"} if provider == "openrouter" else {}  # no default model there
    return LLMClient(FakeConfig(ai_provider=provider, **settings), FakeSecrets(KEYS if secrets is None else secrets),
                     state_dir=tmp_path / "state", http_client=http.client())


ANSWER = {"type": "message", "id": "m", "role": "assistant", "status": "completed",
          "content": [{"type": "output_text", "text": "ok", "annotations": []}]}


@pytest.mark.parametrize("provider, answer, header", [
    ("anthropic", claude_message([text_block("ok")]), "x-api-key"),
    ("openai", responses_body([ANSWER]), "authorization"),
    ("openrouter", completion("ok"), "authorization"),
])
def test_adapters_and_the_client_never_show_the_key(tmp_path, provider, answer, header):
    http = FakeHTTP().add(answer)
    client = _client(tmp_path, http, provider)
    key = KEYS[f"{provider}_api_key"]
    assert client.chat(ask()).text == "ok"
    assert key in http.last.headers[header]  # the SDK still gets the key
    adapter = client.adapter(client.resolve())
    assert isinstance(adapter._key, SecretStr)
    for shown in (repr(adapter), str(adapter), repr(vars(adapter)), repr(client), repr(vars(client)),
                  json.dumps(client.describe())):
        assert key not in shown
    assert client._privacy_key().hex() not in repr(vars(client))


def test_the_credential_store_does_not_show_its_secrets(tmp_path, memory_keyring):
    from orchestrator.Core.secrets_manager import SecretsManager

    store = SecretsManager(tmp_path / "configs")
    key = SAMPLES["OpenAI key"]
    assert store.initialize() and store.set_secret("openai_api_key", key)
    assert store.get_secret("openai_api_key") == key
    assert key not in repr(store) and key not in str(store)


def test_the_local_server_key_is_held_as_a_secret_str(tmp_path):
    http = FakeHTTP().add(completion("ok"))
    client = _client(tmp_path, http, "local", secrets={})
    key = "runtime-" + body(32)
    client.set_runtime_endpoint("local", "http://127.0.0.1:8099/v1", key, "qwen3.5-2b")
    assert client.chat(ask()).text == "ok"
    assert http.last.headers["authorization"] == f"Bearer {key}"
    assert key not in repr(vars(client)) and isinstance(client._runtime["local"][1], SecretStr)


@pytest.mark.parametrize("provider, status, error", [
    ("openrouter", 401, {"error": {"message": "Invalid key " + SAMPLES["OpenRouter key"], "code": 401}}),
    ("anthropic", *anthropic_error(401, "authentication_error", "invalid x-api-key " + SAMPLES["Anthropic key"])[:2]),
])
def test_adapter_errors_never_carry_the_key(tmp_path, caplog, redacted_logging, provider, status, error):
    key = KEYS[f"{provider}_api_key"]
    http = FakeHTTP().add(error, status=status)
    with pytest.raises(AuthFailed) as raised:
        _client(tmp_path, http, provider).chat(ask())
    err = raised.value
    assert key not in str(err) and key not in repr(err) and err.__cause__ is None and err.__suppress_context__
    caplog.set_level(logging.INFO, logger="orchestrator.test")
    logging.getLogger("orchestrator.test").error("chat failed", exc_info=err)
    assert key not in caplog.text and "AuthFailed" in caplog.text
