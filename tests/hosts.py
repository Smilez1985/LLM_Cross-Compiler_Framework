"""Host names in the tests: whether a text names a host, and which host an address or image goes to.

A substring test ("ghcr.io" in text) also matches a longer name that only contains it (ghcr.io.example, myghcr.io);
these compare the whole name instead.
"""

import re
from urllib.parse import urlsplit


def names_host(text: str, host: str) -> bool:
    """Whether `text` names `host` (or the address `host`) as a whole: not as a part of a longer host name. A sentence's
    full stop, a slash, a port or a quote may follow it."""
    return re.search(rf"(?<![\w.-]){re.escape(host)}(?![\w-]|\.[\w-])", text) is not None


def url_host(url: str) -> str:
    """The host name of an address, in lower case ("" when it has none)."""
    return (urlsplit(url).hostname or "").lower()


def image_registry(image: str) -> str:
    """The registry part of an image reference ("" for an image without one: a Docker Hub image), by Docker's rule:
    the first path part is a registry only when it holds a dot or a colon or is localhost."""
    first, sep, _ = image.partition("/")
    return first if sep and ("." in first or ":" in first or first == "localhost") else ""
