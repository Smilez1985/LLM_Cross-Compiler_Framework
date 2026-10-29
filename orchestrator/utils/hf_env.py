#!/usr/bin/env python3
"""
huggingface_hub's own switches for the whole process (DECISIONS round 25 Q2): tokens leave this computer only on
Hugging Face's own encrypted way and only when Hugging Face needs one, and nothing is sent that does not have to be.

- HF_HUB_DISABLE_IMPLICIT_TOKEN=1: no library that uses huggingface_hub (fastembed in rag/embeddings.py and
  rag/retrieval.py, tokenizers, anything else) sends a token it finds by itself (HF_TOKEN, the Hugging Face home
  folder's token file, an OAuth refresh). The framework's own calls name their token in every request
  (Core/hf_hub.py), so this changes nothing for them.
- HF_HUB_DISABLE_TELEMETRY=1: huggingface_hub sends no telemetry and adds nothing about the installed packages to its
  user agent. In huggingface_hub 1.33 this is also the switch of its agent-harnesses registry request: the user agent
  (utils/_headers.py _http_user_agent) calls detect_agent() only while HF_HUB_DISABLE_TELEMETRY is off, and
  detect_agent (utils/_detect_agent.py) fetches `{ENDPOINT}/api/agent-harnesses` once a day, to the host in
  HF_ENDPOINT. With the switch on, no request goes there.

huggingface_hub reads both once, when its module `huggingface_hub.constants` is first imported (constants.py:
`HF_HUB_DISABLE_TELEMETRY = _is_true(os.environ.get(...))`, `HF_HUB_DISABLE_IMPLICIT_TOKEN: bool = ...`), and later
reads the module's values at each request. seal() therefore runs when the package `orchestrator` is imported
(orchestrator/__init__.py), before any framework module can import huggingface_hub or fastembed, and it also sets the
module's values when huggingface_hub was imported before the framework (a script that imported a library first).
The person's own value of either variable is not kept: the framework always runs with both switches on.

Only the standard library is imported here.
"""

import os
import sys

SWITCHES = ("HF_HUB_DISABLE_IMPLICIT_TOKEN", "HF_HUB_DISABLE_TELEMETRY")


def seal() -> None:
    """Both switches on for this process and the programs it starts; huggingface_hub's values too when it is loaded
    already."""
    for name in SWITCHES:
        os.environ[name] = "1"
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None:
        for name in SWITCHES:
            setattr(constants, name, True)


def sealed() -> bool:
    """Both switches are on in the environment and, when huggingface_hub is loaded, in its constants."""
    constants = sys.modules.get("huggingface_hub.constants")
    return all(os.environ.get(name) == "1" for name in SWITCHES) and (
        constants is None or all(getattr(constants, name, False) is True for name in SWITCHES))


__all__ = ["SWITCHES", "seal", "sealed"]
