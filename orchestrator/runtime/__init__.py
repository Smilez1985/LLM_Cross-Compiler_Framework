"""Managed local LLM runtime (llama-server from llama.cpp) for Ditto's offline mode, and hardware profiles."""

from orchestrator.runtime.catalog import RuntimeCatalog, load_catalog, version_tuple
from orchestrator.runtime.hardware import (BackendChoice, GpuInfo, HardwareInfo, choose_model_tier, compute_context,
                                           detect_hardware, model_fits, select_backend)
from orchestrator.runtime.llama_server import (IntegrityError, LocalEndpoint, LocalLLMRuntime, LocalRuntimeError,
                                               RuntimeStatus, managed_endpoint, read_endpoint)
from orchestrator.runtime.profile import (HardwareProfile, ProfileError, ProfileStore, parse_profile,
                                          read_profile)

__all__ = [
    "BackendChoice", "GpuInfo", "HardwareInfo", "HardwareProfile", "IntegrityError", "LocalEndpoint",
    "LocalLLMRuntime", "LocalRuntimeError", "ProfileError", "ProfileStore", "RuntimeCatalog", "RuntimeStatus",
    "choose_model_tier", "compute_context", "detect_hardware", "load_catalog", "managed_endpoint", "model_fits",
    "parse_profile", "read_endpoint", "read_profile", "select_backend", "version_tuple",
]
