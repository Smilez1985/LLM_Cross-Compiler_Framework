#!/usr/bin/env python3
"""
Does SearXNG run in a Python folder of its own on this computer? (llmcf's second way to have a search engine, without a
container; Core/searxng_native.py.) CI runs this on Linux, Windows and macOS (.github/workflows/searxng-native.yml); a person
can run it too:  python scripts/searxng_native_check.py [FOLDER]

It installs SearXNG at the pinned commit into FOLDER (a new temporary folder by default), copies llmcf's finished settings
file, starts SearXNG on 127.0.0.1, asks it for JSON, checks that a format the settings file does not allow is refused,
searches once, and stops it. Exit code 0 when all of that worked, 1 otherwise, with the reason and the log's last lines.
"""

import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.Core import searxng_native, searxng_setup  # noqa: E402

PORT = 8891
QUERY = "python programming language"


def fetch(path: str) -> "tuple[int, str]":
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{PORT}{path}", timeout=60) as answer:
            return answer.status, answer.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def main(argv: list) -> int:
    own = len(argv) < 2
    folder = Path(tempfile.mkdtemp(prefix="llmcf-searxng-")) if own else Path(argv[1])
    process = None
    try:
        print(f"python {sys.version.split()[0]} on {sys.platform}; folder {folder}", flush=True)
        searxng_native.install(folder / "native", progress=lambda text: print(text, flush=True))
        settings, copied = searxng_setup.prepare(folder)
        secret, _ = searxng_setup.ensure_secret(folder)
        print(f"settings file: {settings} (copied: {copied})", flush=True)
        process = searxng_native.start(folder / "native", settings, secret, PORT)
        print("SearXNG answers on /healthz", flush=True)
        status, text = fetch(f"/search?q={urllib.request.quote(QUERY)}&format=json")
        assert status == 200, f"format=json answered HTTP {status}"
        data = json.loads(text)
        assert isinstance(data.get("results"), list), "the JSON has no list of results"
        print(f"format=json: HTTP 200, {len(data['results'])} results, unresponsive engines: "
              f"{[e[0] for e in data.get('unresponsive_engines', [])][:6]}", flush=True)
        status, _ = fetch(f"/search?q={urllib.request.quote(QUERY)}&format=csv")
        assert status == 403, f"format=csv should be refused (403), answered HTTP {status}"
        print("format=csv: HTTP 403 (not allowed by the settings file, as intended)", flush=True)
        print("OK: SearXNG runs in its own Python folder here.", flush=True)
        return 0
    except (AssertionError, searxng_native.NativeError, searxng_setup.SetupError, ValueError, OSError) as e:
        print(f"FAILED: {type(e).__name__}: {e}", flush=True)
        log = folder / "native" / "server.log"
        if log.is_file():
            print("--- last lines of server.log:", flush=True)
            print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]), flush=True)
        return 1
    finally:
        if process is not None:
            searxng_native.stop(process)
        if own:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
