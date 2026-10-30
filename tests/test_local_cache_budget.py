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


# --- the budget (chain ctx-local-mem) --------------------------------------------------------------------------------

from orchestrator.runtime import llama_server as ls  # noqa: E402
from orchestrator.runtime.hardware import (MIB, OS_MARGIN, SYSTEM_RESERVE, CacheBudget, cache_number,  # noqa: E402
                                           kv_bytes_for, local_budget, over_cap)


def _budget(name, tier=None, saved=None, ctx=None):
    hw, backend = MACHINES[name]
    choice = backend_choice(hw, backend)
    model = CATALOG.model(tier) if tier else choose_model_tier(hw, CATALOG, choice)[0]
    return model, hw, choice, local_budget(hw, model.size, model.kv_bytes_per_token, ctx or CATALOG.server.ctx_min,
                                           saved=saved, choice=choice)


def test_kv_bytes_follow_the_ggml_block_sizes():
    model = CATALOG.model("small")
    assert kv_bytes_for(model, "f16") == model.kv_bytes_per_token == kv_bytes_for(model, None)
    assert kv_bytes_for(model, "q8_0") == 12288 * 34 // 64      # 32 values in 34 bytes instead of 64
    assert kv_bytes_for(model, "q4_0") == 12288 * 18 // 64      # 32 values in 18 bytes
    assert kv_bytes_for(1000, "q8_0") == 532                     # rounded up
    with pytest.raises(ValueError):
        kv_bytes_for(model, "q5_1")


def test_compute_context_with_a_quantised_cache_and_a_reserve():
    model = CATALOG.model("large")
    machine = HardwareInfo(os="linux", arch="x86_64", ram_total=16 * GIB, ram_available=7 * GIB)
    f16, _ = compute_context(model, machine, None, CATALOG.server)
    q8, why = compute_context(model, machine, None, CATALOG.server, kv_kind="q8_0")
    q4, _ = compute_context(model, machine, None, CATALOG.server, kv_kind="q4_0")
    assert f16 < q8 < q4 and "q8_0 KV cache" in why
    reserved, _ = compute_context(model, machine, None, CATALOG.server, reserve_bytes=512 * MIB)
    assert reserved < f16
    # The defaults give the result of 3.1.0, also through the new keywords.
    assert compute_context(model, machine, None, CATALOG.server, kv_kind="f16", reserve_bytes=0)[0] == f16
    # A GPU that holds the whole model: the prompt cache lives in RAM, so it does not shorten the GPU's context.
    gpu = compute_context(model, GPU16, backend_choice(GPU16, "cuda"), CATALOG.server, reserve_bytes=4 * GIB)
    assert gpu[0] == 32768


@pytest.mark.parametrize("name, ram, kv", [("cpu4", "lower", "q8_0"), ("gpu16", 2048, "f16"),
                                           ("apple64", 8192, "f16"), ("board2", 0, "q8_0")])
def test_the_budget_of_the_four_machines(name, ram, kv):
    model, hw, choice, budget = _budget(name)
    rule = max(256, min(8192, hw.ram_total // (8 * MIB)))
    assert budget.kv_cache == kv and budget.auto["local_kv_cache"] == kv, "the boards' rule (kv_cache_default)"
    if ram == "lower":
        assert 0 < budget.ram_mib < rule, "4 GiB without a GPU: less prompt cache than 3.1.0's 512 MiB"
    else:
        assert budget.ram_mib == ram
    assert budget.disk_mib == 0 and budget.soft_caps["local_cache_ram_mib"] == budget.ram_mib
    assert budget.hard_limits["local_cache_ram_mib"] >= budget.soft_caps["local_cache_ram_mib"]


@pytest.mark.parametrize("ram_gib", [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 64, 128])
@pytest.mark.parametrize("free_share", [0.3, 0.6, 0.9])
@pytest.mark.parametrize("tier", ["small-q2", "small", "default", "large"])
def test_the_automatic_prompt_cache_is_never_larger_than_before_and_never_makes_the_machine_swap(ram_gib, free_share,
                                                                                                 tier):
    hw = HardwareInfo(os="linux", arch="x86_64", ram_total=ram_gib * GIB,
                      ram_available=int(ram_gib * GIB * free_share))
    model = CATALOG.model(tier)
    budget = local_budget(hw, model.size, model.kv_bytes_per_token, CATALOG.server.ctx_min)
    assert 0 <= budget.ram_mib <= max(256, min(8192, hw.ram_total // (8 * MIB)))
    ctx, _ = compute_context(model, hw, None, CATALOG.server, model_bytes=model.size, kv_kind=budget.kv_cache,
                             reserve_bytes=budget.ram_mib * MIB)
    used = int(model.size * 1.05) + kv_bytes_for(model, budget.kv_cache) * ctx + budget.ram_mib * MIB + OS_MARGIN
    if budget.ram_mib:
        # Counted once: the weights, the KV cache of the context, the prompt cache and the margin fit in free RAM.
        assert used <= hw.ram_available


def test_no_value_reaches_no_limit():
    for value in ("-1", "-5", "1.5", "lots", "99999999"):
        with pytest.raises(ValueError):
            cache_number("local_cache_ram_mib", value)
    assert cache_number("local_cache_ram_mib", "auto") is None
    assert cache_number("local_cache_keep_minutes", "0") == float("inf")
    with pytest.raises(ValueError):
        cache_number("local_kv_cache", "q5_1")
    assert ls.server_extra_args(4096, gpu=False, cache_ram_mib=-1)[5] == "0", "never -1 on the command line"


def test_saved_values_soft_cap_and_hard_limit():
    _, _, _, auto = _budget("gpu16")
    soft, hard = auto.soft_caps["local_cache_ram_mib"], auto.hard_limits["local_cache_ram_mib"]
    assert over_cap(auto, "local_cache_ram_mib", "auto") is None
    assert over_cap(auto, "local_cache_ram_mib", str(soft)) is None
    assert over_cap(auto, "local_cache_ram_mib", str(soft + 1)) == "soft"
    assert over_cap(auto, "local_cache_ram_mib", str(hard + 1)) == "hard"
    assert auto.over_cap("local_cache_keep_minutes", "0") == "soft", "never unload is above every cap"
    assert over_cap(auto, "local_cache_keep_minutes", "5000") == "hard"
    _, _, _, chosen = _budget("gpu16", saved={"local_cache_ram_mib": str(soft + 100), "local_kv_cache": "q4_0",
                                               "local_cache_keep_minutes": "0"})
    assert chosen.ram_mib == soft + 100 and chosen.kv_cache == "q4_0" and chosen.keep_minutes == 0
    assert set(chosen.over_soft_cap) == {"local_cache_ram_mib", "local_cache_keep_minutes"}
    _, _, _, clamped = _budget("gpu16", saved={"local_cache_ram_mib": str(hard * 4)})
    assert clamped.ram_mib == hard and "hard limit" in clamped.reasons["local_cache_ram_mib"]
    assert isinstance(clamped, CacheBudget) and set(clamped.settings_view()) == {
        "local_cache_ram_mib", "local_cache_keep_minutes", "local_kv_cache"}
    assert clamped.hard_limits["local_cache_ram_mib"] <= (GPU16.ram_total - SYSTEM_RESERVE) // MIB


def test_one_cache_rule_only():
    """Ratchet: the prompt cache's eighth of the RAM is computed in one place (hardware.local_budget)."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "orchestrator"
    found = [p for p in root.rglob("*.py") if "ram_total // (8 * MIB)" in p.read_text(encoding="utf-8")]
    assert [p.name for p in found] == ["hardware.py"]
    assert (root / "runtime" / "hardware.py").read_text(encoding="utf-8").count("ram_total // (8 * MIB)") == 1
    launcher = (root / "runtime" / "llama_server.py").read_text(encoding="utf-8")
    assert launcher.count('"--cache-ram"') == 1, "both launchers take their options from server_extra_args"
    assert "512 * MIB" not in (root / "runtime" / "hardware.py").read_text(encoding="utf-8")


# --- the launcher ---------------------------------------------------------------------------------------------------

class Settings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@POSIX_ONLY
def test_a_board_like_machine_gets_less_prompt_cache_and_a_quantised_kv_cache(fake_runtime):  # noqa: F811
    rt = fake_runtime()
    rt._hardware = BOARD2
    ep = rt.start()
    argv = _argv(rt)
    # 3.1.0 gave 256 MiB here although only ~400 MiB are left next to the system's margin; and no KV-cache type.
    assert int(_value(argv, "--cache-ram")) < 256
    assert _value(argv, "-ctk") == "q8_0" and _value(argv, "-ctv") == "q8_0" and _value(argv, "-fa") == "auto"
    assert _value(argv, "-np") == "1"
    state = json.loads(rt.state_file.read_text())
    assert state["kv_cache"] == "q8_0" and state["cache_ram"] == int(_value(argv, "--cache-ram"))
    assert state["cache_settings"] == {"local_cache_ram_mib": "auto", "local_cache_keep_minutes": "auto",
                                       "local_kv_cache": "auto"}
    assert ep.n_ctx == int(_value(argv, "-c")) and state["n_ctx_slot"] == ep.n_ctx
    status = rt.status()
    assert status.n_ctx_slot == ep.n_ctx and status.kv_cache == "q8_0" and status.build_info == "b1-fake31m"
    assert ls.read_endpoint(rt.root).n_ctx == ep.n_ctx


@POSIX_ONLY
def test_the_settings_reach_the_server_and_a_change_restarts_it_once(fake_runtime):  # noqa: F811
    rt = fake_runtime()
    rt.settings = Settings(local_cache_ram_mib="300", local_kv_cache="q4_0", local_cache_keep_minutes="5")
    first = rt.start()
    argv = _argv(rt)
    assert _value(argv, "--cache-ram") == "300" and _value(argv, "-ctk") == "q4_0"
    assert _value(argv, "--sleep-idle-seconds") == "300" and _value(argv, "-np") == "1"
    assert rt.start().pid == first.pid, "unchanged settings never restart the server"
    rt.settings.values["local_kv_cache"] = "f16"
    second = rt.start()
    assert second.pid != first.pid and "-ctk" not in _argv(rt, 2) and "-fa" not in _argv(rt, 2)
    assert rt.start().pid == second.pid, "restarted once"
    rt.settings.values["local_cache_keep_minutes"] = "0"
    third = rt.start()
    assert third.pid != second.pid and "--sleep-idle-seconds" not in _argv(rt, 3), "0 = never unload"


@POSIX_ONLY
def test_a_server_without_props_still_starts(fake_runtime):  # noqa: F811
    rt = fake_runtime("noprops")
    ep = rt.start()
    assert ep.n_ctx is None and "n_ctx_slot" not in json.loads(rt.state_file.read_text())


def test_props_are_read_on_loopback_with_the_key_only():
    seen = []

    class Reply:
        status_code = 200

        @staticmethod
        def json():
            return {"default_generation_settings": {"n_ctx": 8192}, "total_slots": 1, "build_info": "b11429-abc",
                    "is_sleeping": True}

    class Session:
        def get(self, url, timeout, headers):
            seen.append((url, headers))
            return Reply()

        def post(self, *a, **k):
            pytest.fail("nothing is ever posted to /props")

    ep = ls.LocalEndpoint(base_url="http://127.0.0.1:4100/v1", api_key="k3y", model="m", port=4100, pid=1)
    props = ls.read_props(ep, Session())
    assert props == ls.Props(n_ctx=8192, total_slots=1, build_info="b11429-abc", is_sleeping=True)
    assert seen == [("http://127.0.0.1:4100/props", {"Authorization": "Bearer k3y"})]
    other = ep.model_copy(update={"base_url": "http://192.0.2.10:4100/v1"})
    assert ls.read_props(other, Session()) is None and len(seen) == 1


def test_both_launchers_build_the_same_options():
    extra = ls.server_extra_args(8192, gpu=True, cache_ram_mib=1024, kv_cache="q8_0", sleep_idle_seconds=600)
    assert extra == ["-c", "8192", "-np", "1", "--cache-ram", "1024", "-ctk", "q8_0", "-ctv", "q8_0", "-fa", "auto",
                     "-ngl", "auto", "--sleep-idle-seconds", "600"]
    from orchestrator.runtime import server_policy
    cmd = server_policy.container_server_command("/opt/llama/llama-server", "/models/x.gguf", key_file="/run/k",
                                                 alias="x", extra=extra)
    native = ls.build_server_command(ls.Path("/opt/llama-server"), ls.Path("/m/x.gguf"), 4100, ls.Path("k"), 8192,
                                     "x", gpu=True, cache_ram_mib=1024, sleep_idle_seconds=600, kv_cache="q8_0")
    assert cmd[-len(extra):] == native[-len(extra):] == extra
