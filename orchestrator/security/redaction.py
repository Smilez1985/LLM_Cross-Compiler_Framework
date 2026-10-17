#!/usr/bin/env python3
"""
Secrets at runtime: SecretStr for credentials held in memory, and the logging redaction that keeps credentials
out of every log, the provider SDKs' and HTTP libraries' included.

setup_logging() (orchestrator/utils/logging.py) calls install_log_redaction() and configure_sdk_loggers(): the
HTTP and SDK loggers log DEBUG output (request options with prompts, headers, URLs) only with --debug-http, and
even then through the redaction.
"""

import hmac
import logging
import sys
import threading
from typing import Optional, Union

from orchestrator.security.secret_patterns import redact

# HTTP libraries and provider SDKs (with their child loggers, e.g. openai._base_client, httpcore2.http11).
SDK_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic", "openai", "urllib3", "botocore",
               "google.auth", "azure", "msal")


class SecretStr:
    """A credential held in memory. str(), repr() and format() give <redacted>, pickling is refused;
    get_secret_value() hands the value to the one place that needs it (building an SDK client)."""

    __slots__ = ("_value",)

    def __init__(self, value: Union[str, "SecretStr"]):
        self._value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)

    @classmethod
    def wrap(cls, value: Union[str, "SecretStr", None]) -> Optional["SecretStr"]:
        """SecretStr for a non-empty value, None otherwise."""
        if isinstance(value, SecretStr):
            return value if value else None
        return cls(value) if value else None

    def get_secret_value(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretStr):
            return NotImplemented
        # surrogatepass: values from os.environ may hold undecodable bytes as lone surrogates
        return hmac.compare_digest(self._value.encode("utf-8", "surrogatepass"),
                                   other._value.encode("utf-8", "surrogatepass"))

    def __hash__(self) -> int:
        return hash(self._value)

    def __copy__(self) -> "SecretStr":
        return self

    def __deepcopy__(self, memo) -> "SecretStr":
        return self

    def __reduce_ex__(self, protocol):
        raise TypeError("a SecretStr is not pickled: the credential would be written out in plain text")


def reveal(value: Union[str, SecretStr, None]) -> Optional[str]:
    """The plain value of a SecretStr (or of a str), for the moment it is used."""
    return value.get_secret_value() if isinstance(value, SecretStr) else value


# --- logging ----------------------------------------------------------------------------------------------------

_exception_formatter = logging.Formatter()


def _printable(value: object) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<unprintable {type(value).__name__}>"


def redact_record(record: logging.LogRecord) -> None:
    """Redacts a record in place: the message with its arguments (merged, so no handler formats the raw
    arguments again), the traceback text and the stack. Never raises: it runs inside logger.info() and the
    like, in the code that logs."""
    try:
        message = record.getMessage()
    except Exception:  # arguments that do not fit the format, or an object without str(): keep it readable
        try:
            message = f"{record.msg} {record.args!r}"
        except Exception:
            message = f"{_printable(record.msg)} {_printable(record.args)}"
    record.msg, record.args = redact(message), ()
    if record.exc_info and not record.exc_text:
        try:
            record.exc_text = _exception_formatter.formatException(record.exc_info)  # formatters use it as is
        except Exception:
            record.exc_text = f"<traceback not available: {_printable(record.exc_info)}>"
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)


class RedactingFilter(logging.Filter):
    """Redacts each record it sees; never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
        redact_record(record)
        return True


class _StderrHandler(logging.StreamHandler):
    """--debug-http output. Writes to the sys.stderr of the moment (the CLI test runner replaces it)."""

    def __init__(self):
        logging.Handler.__init__(self)
        self.addFilter(_FILTER)
        self.setFormatter(logging.Formatter("%(asctime)s - %(name)s - [%(levelname)s] - %(message)s",
                                            "%Y-%m-%d %H:%M:%S"))

    @property
    def stream(self):
        return sys.stderr


_FILTER = RedactingFilter()
_lock = threading.Lock()
_factory_installed = False
_debug_http = False
_debug_handler: Optional[logging.Handler] = None


def install_log_redaction() -> None:
    """Redacts every log record when it is created, whichever logger it comes from (child loggers such as
    openai._base_client included) and whichever handler gets it (handlers added later, such as the GUI's,
    included). The filter on the root and SDK loggers also covers records that were not built by the record
    factory. Safe to call more than once."""
    global _factory_installed
    with _lock:
        if not _factory_installed:
            make_record = logging.getLogRecordFactory()

            def redacting_factory(*args, **kwargs):
                record = make_record(*args, **kwargs)
                redact_record(record)
                return record

            logging.setLogRecordFactory(redacting_factory)
            _factory_installed = True
        for name in ("", *SDK_LOGGERS):
            logger = logging.getLogger(name)
            if _FILTER not in logger.filters:
                logger.addFilter(_FILTER)


def configure_sdk_loggers(level: int, debug_http: Optional[bool] = None) -> None:
    """Levels of the HTTP and SDK loggers. Without debug_http they log from INFO at the lowest, whatever the
    framework level; with it they log DEBUG, also to stderr. None keeps the last choice (the CLI sets it, and
    the framework's later setup_logging() keeps it)."""
    global _debug_http, _debug_handler
    with _lock:
        if debug_http is not None:
            _debug_http = debug_http
        if _debug_http and _debug_handler is None:
            _debug_handler = _StderrHandler()
        for name in SDK_LOGGERS:
            logger = logging.getLogger(name)
            logger.setLevel(logging.DEBUG if _debug_http else max(level, logging.INFO))
            if _debug_handler is not None:
                if _debug_http and _debug_handler not in logger.handlers:
                    logger.addHandler(_debug_handler)
                elif not _debug_http:
                    logger.removeHandler(_debug_handler)
        # The framework's set_global_level() gives every existing logger its own level; child loggers follow
        # their SDK logger again.
        prefixes = tuple(f"{name}." for name in SDK_LOGGERS)
        for name, logger in list(logging.Logger.manager.loggerDict.items()):
            if name.startswith(prefixes) and isinstance(logger, logging.Logger):
                logger.setLevel(logging.NOTSET)
