"""Managed local LLM runtime (llama-server from llama.cpp) for Ditto's offline mode."""

from orchestrator.runtime.catalog import RuntimeCatalog, load_catalog
from orchestrator.runtime.hardware import (BackendChoice, HardwareInfo, choose_model_tier, compute_context,
                                           detect_hardware, select_backend)
from orchestrator.runtime.llama_server import (IntegrityError, LocalEndpoint, LocalLLMRuntime, LocalRuntimeError,
                                               RuntimeStatus)

__all__ = [
    "BackendChoice", "HardwareInfo", "IntegrityError", "LocalEndpoint", "LocalLLMRuntime", "LocalRuntimeError",
    "RuntimeCatalog", "RuntimeStatus", "choose_model_tier", "compute_context", "detect_hardware", "load_catalog",
    "select_backend",
]
