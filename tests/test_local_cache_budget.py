"""One memory budget for Ditto's local server (chain ctx-local-mem).

Four fixed machines (documentation names only): a 4 GiB computer without a GPU, a 16 GiB computer with an 8 GiB NVIDIA
GPU, a 64 GiB Apple computer with unified memory and a 2 GiB board-like computer. The characterization tests at the top
pin what the context rule (compute_context) and the prompt cache of the launcher (--cache-ram) gave before the budget
was introduced; they must keep passing.
"""

import json
import os

import pytest

from orchestrator.runtime.catalog import load_catalog
from orchestrator.runtime.hardware import (GIB, GpuInfo, HardwareInfo, backend_choice, choose_model_tier,
                                           compute_context)

from test_runtime import fake_runtime  # noqa: F401 - fixture

CATALOG = load_catalog()
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="the stand-in llama-server is a shebang script")

CPU4 = HardwareInfo(os="linux", arch="x86_64", ram_total=4 * GIB, ram_available=3 * GIB)
GPU16 = HardwareInfo(os="linux", arch="x86_64", ram_total=16 * GIB, ram_available=12 * GIB,
                     gpus=[GpuInfo(vendor="nvidia", name="NVIDIA Example 8G", discrete=True, vram_mib=8192,
                                   vram_free_mib=7800, compute_cap=8.6, driver_version="580.95.05")])
APPLE64 = HardwareInfo(os="macos", arch="arm64", ram_total=64 * GIB, ram_available=48 * GIB,
                       gpus=[GpuInfo(vendor="apple", name="Apple M-Example", unified=True, vram_mib=48 * 1024)])
BOARD2 = HardwareInfo(os="linux", arch="arm64", ram_total=2 * GIB, ram_available=int(1.4 * GIB))
MACHINES = {"cpu4": (CPU4, "cpu"), "gpu16": (GPU16, "cuda"), "apple64": (APPLE64, "metal"),
            "board2": (BOARD2, "cpu")}


# --- characterization: what the rules gave before the budget ----------------------------------------------------------

@pytest.mark.parametrize("name, tier, ctx, limit", [
    ("cpu4", "small", 32768, "cap"),
    ("gpu16", "large", 32768, "cap"),
    ("apple64", "large", 32768, "cap"),
    ("board2", "small-q2", 4096, "minimum"),
])
def test_context_of_the_four_machines(name, tier, ctx, limit):
    hw, backend = MACHINES[name]
    choice = backend_choice(hw, backend)
    model, _ = choose_model_tier(hw, CATALOG, choice)
    assert model.tier == tier
    got, why = compute_context(model, hw, choice, CATALOG.server)
    assert got == ctx and limit in why


def test_context_reasons_name_the_memory_they_used():
    model = CATALOG.model("small")
    assert compute_context(model, CPU4, backend_choice(CPU4, "cpu"), CATALOG.server)[1] == (
        "32768 tokens (cap; 3.0 GiB free RAM, 12 KiB KV cache per token)")
    assert compute_context(CATALOG.model("large"), GPU16, backend_choice(GPU16, "cuda"), CATALOG.server)[1] == (
        "32768 tokens (cap; 7.6 GiB free GPU memory, 32 KiB KV cache per token)")
    assert compute_context(CATALOG.model("default"), BOARD2, None, CATALOG.server)[1] == (
        "4096 tokens (minimum; only ~0 fit in 1.4 GiB free RAM next to the model)")


def _argv(rt, start: int = 1):
    return json.loads((rt.root / "b1-cpu" / f"argv-{start}.json").read_text())


def _value(argv, option):
    return argv[argv.index(option) + 1] if option in argv else None


@POSIX_ONLY
@pytest.mark.parametrize("name, cache_ram", [("cpu4", "512"), ("gpu16", "2048"), ("apple64", "8192")])
def test_cache_ram_of_the_launcher(fake_runtime, name, cache_ram):  # noqa: F811
    rt = fake_runtime()
    rt._hardware = MACHINES[name][0]
    rt.start()
    assert _value(_argv(rt), "--cache-ram") == cache_ram
