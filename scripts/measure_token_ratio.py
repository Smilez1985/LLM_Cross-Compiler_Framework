#!/usr/bin/env python3
"""
Development only: the characters per token of a tokenizer on the kinds of text Ditto sends, for the calibrated
estimate of `orchestrator/llm/token_count.py` (the `estimates:` table of configs/context_windows.yml).

    python scripts/measure_token_ratio.py --llama-tokenize PATH --model VOCAB.gguf [--json out.json]

The corpus is fixed and comes from this repository, so a second run on another computer gives the same numbers: the
first 20,000 characters of docs/Features.md (English prose), docs/Features_DE.md (German prose),
orchestrator/llm/client.py (code), and configs/llm_providers.yml as compact JSON (what a tool result looks like). Each
kind is counted by llama.cpp's own `llama-tokenize` with the given GGUF file (a vocabulary-only file of llama.cpp's
`models/` folder is enough). It prints, per kind, the characters, the tokens and the characters per token; then the
ratio the estimate uses (the smallest, so the estimate never counts fewer tokens than the tokenizer on these kinds)
and the largest overestimate that ratio makes on the other kinds, in percent.

Nothing is sent anywhere. It never runs in CI.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CHARS = 20_000


def corpus(root: Path = ROOT) -> Dict[str, str]:
    """The fixed texts, by kind."""
    import yaml
    providers = yaml.safe_load((root / "configs" / "llm_providers.yml").read_text(encoding="utf-8"))
    compact = json.dumps(providers, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "prose_en": (root / "docs" / "Features.md").read_text(encoding="utf-8")[:SAMPLE_CHARS],
        "prose_de": (root / "docs" / "Features_DE.md").read_text(encoding="utf-8")[:SAMPLE_CHARS],
        "code": (root / "orchestrator" / "llm" / "client.py").read_text(encoding="utf-8")[:SAMPLE_CHARS],
        "json": compact[:SAMPLE_CHARS],
    }


def count_tokens(tool: str, model: str, text: str) -> int:
    """Tokens of `text` as llama-tokenize counts them (no BOS)."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.txt"
        path.write_bytes(text.encode("utf-8"))
        out = subprocess.run([tool, "-m", model, "-f", str(path), "--ids", "--log-disable", "--no-bos"],
                             capture_output=True, text=True, check=True, timeout=600).stdout
    ids = json.loads(out.strip().splitlines()[-1])
    return len(ids)


def measure(tool: str, model: str) -> Dict[str, object]:
    kinds: List[Dict[str, object]] = []
    for kind, text in corpus().items():
        tokens = count_tokens(tool, model, text)
        kinds.append({"kind": kind, "chars": len(text), "tokens": tokens, "chars_per_token": len(text) / tokens})
    ratio = min(k["chars_per_token"] for k in kinds)
    over = max((k["tokens"] * k["chars_per_token"] / ratio - k["tokens"]) / k["tokens"] for k in kinds)
    return {"model": Path(model).name, "kinds": kinds, "chars_per_token": round(ratio, 2),
            "max_overestimate_pct": round(100 * over, 1)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--llama-tokenize", required=True, help="llama.cpp's llama-tokenize program")
    parser.add_argument("--model", required=True, help="a GGUF file with the tokenizer (vocabulary-only is enough)")
    parser.add_argument("--json", help="also write the result to this file")
    args = parser.parse_args(argv)
    result = measure(args.llama_tokenize, args.model)
    for kind in result["kinds"]:
        print(f"{kind['kind']:9} {kind['chars']:6} chars {kind['tokens']:6} tokens "
              f"{kind['chars_per_token']:.2f} chars/token")
    print(f"estimate: {result['chars_per_token']} chars/token, at most {result['max_overestimate_pct']} % too many")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
