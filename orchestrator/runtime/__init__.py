"""Managed local LLM runtime (llama-server from llama.cpp) for Ditto's offline mode."""

from orchestrator.runtime.catalog import RuntimeCatalog, load_catalog, version_tuple
from orchestrator.runtime.hardware import (BackendChoice, GpuInfo, HardwareInfo, choose_model_tier, compute_context,
                                           detect_hardware, model_fits, select_backend)
from orchestrator.runtime.llama_server import (IntegrityError, LocalEndpoint, LocalLLMRuntime, LocalRuntimeError,
                                               RuntimeStatus, managed_endpoint, read_endpoint)

__all__ = [
    "BackendChoice", "GpuInfo", "HardwareInfo", "IntegrityError", "LocalEndpoint", "LocalLLMRuntime",
    "LocalRuntimeError", "RuntimeCatalog", "RuntimeStatus", "choose_model_tier", "compute_context", "detect_hardware",
    "load_catalog", "managed_endpoint", "model_fits", "read_endpoint", "select_backend", "version_tuple",
]
