#!/usr/bin/env python3
"""
Makes the runtime layer's two static tools (busybox and util-linux setpriv) the way "build" (orchestrator/Core/
runtime_layer.py: the framework's builder image orchestrator/runtime_layer/toolbox/Dockerfile over the pinned source
tarballs, each checked by its sha256) and writes them with their SHA256SUMS into OUT.

The CI job "Runtime layer tools" (.github/workflows/ci.yml) runs it, runs the runtime layer's tests with the real
tools (LLMCF_TEST_TOOLBOX=OUT) and uploads OUT as a workflow artifact. Publishing a release with these files is the
maintainer's step; their URL and sha256 then go into the one pinned table of the way "fetch"
(orchestrator/runtime_layer/toolbox/prebuilt.json, kind "file"). This script publishes nothing.

Usage: python scripts/runtime_toolbox.py OUT
"""

import json
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def fetch(url: str) -> bytes:
    """A pinned https source tarball (runtime_layer checks its sha256 before it is used)."""
    if not url.startswith("https://"):
        raise ValueError("only pinned https addresses are fetched")
    with urllib.request.urlopen(url, timeout=300) as response:  # nosec B310 - pinned https URL, sha256-checked
        return response.read(200 * 1024 * 1024)


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    import docker

    from orchestrator.Core import runtime_layer
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    cache = Path(tempfile.mkdtemp(prefix="llmcf-toolbox-"))
    try:
        builder = runtime_layer.engine_builder(docker.from_env(), fetch, log=print)
        record = runtime_layer.ensure_toolbox(cache, "build", offline=False, fetch=fetch, build=builder, log=print)
        arch = runtime_layer.machine()
        lines, table = [], {}
        for tool in runtime_layer.TOOLS:
            name = f"{tool}-{arch}"
            shutil.copy2(runtime_layer.toolbox_dir(cache) / tool, out / name)
            digest = record["sha256"][tool]
            lines.append(f"{digest}  {name}\n")
            table[tool] = {arch: {"kind": "file", "name": name, "sha256": digest,
                                  "url": "(the release asset's https URL, entered when the maintainer publishes it)"}}
        (out / "SHA256SUMS").write_text("".join(lines), encoding="ascii")
        (out / "prebuilt-entries.json").write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
        print("".join(lines), end="")
        return 0
    finally:
        from orchestrator.utils.atomic import remove_tree
        remove_tree(cache, label="the toolbox's scratch folder")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
