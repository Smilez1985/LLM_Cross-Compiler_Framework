"""Managed local LLM runtime (llama-server from llama.cpp) for Ditto's offline mode, and hardware profiles."""

from orchestrator.utils.lazy_imports import lazy_attributes

# Lazy loading: `from orchestrator.runtime import LocalLLMRuntime` imports its module on first use, so importing one
# submodule (e.g. orchestrator.runtime.profile) does not load the llama-server code and the others.
_SOURCES = {
    "orchestrator.runtime.catalog": ("RuntimeCatalog", "load_catalog", "version_tuple"),
    "orchestrator.runtime.hardware": ("BackendChoice", "GpuInfo", "HardwareInfo", "choose_model_tier",
                                      "compute_context", "detect_hardware", "model_fits", "select_backend"),
    "orchestrator.runtime.llama_server": ("IntegrityError", "LocalEndpoint", "LocalLLMRuntime", "LocalRuntimeError",
                                          "RuntimeStatus", "managed_endpoint", "read_endpoint"),
    "orchestrator.runtime.profile": ("HardwareProfile", "ProfileError", "ProfileStore", "parse_profile",
                                     "read_profile"),
}
__getattr__ = lazy_attributes(__name__, {name: module for module, names in _SOURCES.items() for name in names})

__all__ = [
    "BackendChoice", "GpuInfo", "HardwareInfo", "HardwareProfile", "IntegrityError", "LocalEndpoint",
    "LocalLLMRuntime", "LocalRuntimeError", "ProfileError", "ProfileStore", "RuntimeCatalog", "RuntimeStatus",
    "choose_model_tier", "compute_context", "detect_hardware", "load_catalog", "managed_endpoint", "model_fits",
    "parse_profile", "read_endpoint", "read_profile", "select_backend", "version_tuple",
]
