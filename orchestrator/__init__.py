"""LLM Cross-Compiler Framework."""

# Single source of the framework version (pyproject.toml carries the same value; a test checks it).
__version__ = "3.4.0"

# The framework writes nothing into an installed copy while it runs (per-person folders, Core/config_manager.py
# developer_checkout): the installers may hand the installation to root, readable for the group llmcf
# (install.sh share_installation). A framework without this line still writes there and keeps the old ownership.
INSTALL_READ_ONLY = True

# Before any framework module can import huggingface_hub (or fastembed, which imports it): no implicit Hugging Face
# token and no telemetry or agent-harnesses request for the whole process (DESIGN round 25 Q2, utils/hf_env.py).
from orchestrator.utils.hf_env import seal as _seal_huggingface_hub  # noqa: E402

_seal_huggingface_hub()
