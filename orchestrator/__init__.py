"""LLM Cross-Compiler Framework."""

# Single source of the framework version (pyproject.toml carries the same value; a test checks it).
__version__ = "3.3.0"

# Before any framework module can import huggingface_hub (or fastembed, which imports it): no implicit Hugging Face
# token and no telemetry or agent-harnesses request for the whole process (DECISIONS round 25 Q2, utils/hf_env.py).
from orchestrator.utils.hf_env import seal as _seal_huggingface_hub  # noqa: E402

_seal_huggingface_hub()
