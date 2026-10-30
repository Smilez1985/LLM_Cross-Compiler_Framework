"""A quantised V cache needs flash attention (chain followups-3).

llama.cpp refuses a q8_0 or q4_0 V cache when the build or the backend has no flash attention, and the server then
exits during startup. With local_kv_cache on auto the launcher starts once more with the f16 cache, records why
(server.json kv_fallback, RuntimeStatus.kv_fallback), tells the person (CLI, GUI, Mojo) and goes straight to f16 for
that server afterwards. A q8_0 or q4_0 the person set is never changed behind their back: the start fails with a plain
text that names local_kv_cache and f16. The server here is a stand-in process (tests/test_runtime.FAKE_SERVER) that
fails like llama.cpp when it gets a quantised V cache.
"""

import json
import os

import pytest

from orchestrator.runtime import llama_server as ls
from orchestrator.runtime.hardware import GIB, HardwareInfo

import test_runtime
from test_runtime import fake_runtime  # noqa: F401 - fixture

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="the stand-in llama-server is a shebang script")
BOARD2 = HardwareInfo(os="linux", arch="arm64", ram_total=2 * GIB, ram_available=int(1.4 * GIB))

# What the pinned llama.cpp prints when the V cache is quantised and flash attention is off or missing (both forms).
FA_ERRORS = ("llama_init_from_model: V cache quantization requires flash_attn",
             "common_init_from_params: failed to create context: quantized V cache was requested, but this requires "
             "Flash Attention")

NO_FA = '''
if mode == "no_fa" and opt("-ctv") not in (None, "f16"):
    print("%s", flush=True)
    sys.exit(1)
if mode == "exit":'''


class Settings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def no_fa_runtime(fake_runtime, monkeypatch):  # noqa: F811
    def make(error=FA_ERRORS[1], **settings):
        rt = fake_runtime("no_fa")
        script = test_runtime.FAKE_SERVER.replace('\nif mode == "exit":', NO_FA % error, 1)
        server = rt.root / "b1-cpu" / "llama-server"
        server.write_text(server.read_text().split("\n", 1)[0] + "\n" + script)
        rt._hardware = BOARD2  # the boards' rule: q8_0 on auto
        rt.settings = Settings(**settings)
        return rt
    return make


def _argv(rt, start):
    return json.loads((rt.root / "b1-cpu" / f"argv-{start}.json").read_text())


def _value(argv, option):
    return argv[argv.index(option) + 1] if option in argv else None


def _starts(rt):
    return int((rt.root / "b1-cpu" / "starts").read_text())


@pytest.mark.parametrize("text", FA_ERRORS)
def test_the_flash_attention_refusal_is_recognised(text):
    assert ls.needs_flash_attention("loading model\n" + text + "\nmain: exiting")
    assert not ls.needs_flash_attention("error: failed to load model 'tiny.gguf'")


@POSIX_ONLY
@pytest.mark.parametrize("error", FA_ERRORS)
def test_auto_starts_once_more_with_f16_and_says_so(no_fa_runtime, error):
    rt = no_fa_runtime(error)
    ep = rt.start()
    assert _value(_argv(rt, 1), "-ctv") == "q8_0"
    assert "-ctv" not in _argv(rt, 2) and "-fa" not in _argv(rt, 2) and _starts(rt) == 2
    state = json.loads(rt.state_file.read_text())
    assert state["kv_cache"] == "f16" and state["kv_fallback"] == "q8_0"
    status = rt.status()
    assert status.kv_cache == "f16" and status.kv_fallback == "q8_0"
    note = ls.kv_fallback_text("q8_0")
    assert "flash attention" in note and "f16" in note and "local_kv_cache" in note
    assert ep.n_ctx == int(_value(_argv(rt, 2), "-c"))
    # The f16 cache needs more memory per token: the context is planned again for it, never larger than before.
    assert int(_value(_argv(rt, 2), "-c")) <= int(_value(_argv(rt, 1), "-c"))

    rt.stop(timeout=5)
    rt.start()
    assert _starts(rt) == 3 and "-ctv" not in _argv(rt, 3), "the next start goes straight to f16"
    assert rt.status().kv_fallback == "q8_0"


@POSIX_ONLY
@pytest.mark.parametrize("kind", ["q8_0", "q4_0"])
def test_a_quantised_cache_the_person_set_fails_with_a_plain_text(no_fa_runtime, kind):
    rt = no_fa_runtime(local_kv_cache=kind)
    with pytest.raises(ls.LocalRuntimeError) as caught:
        rt.start()
    message = str(caught.value)
    assert "local_kv_cache" in message and kind in message and "f16" in message and "flash attention" in message
    assert _starts(rt) == 1, "the person's setting is never replaced"
    assert not rt.state_file.exists() or "kv_fallback" not in rt.state_file.read_text()


@POSIX_ONLY
def test_another_startup_failure_is_not_retried(fake_runtime):  # noqa: F811
    rt = fake_runtime("exit")
    rt._hardware = BOARD2
    with pytest.raises(ls.LocalRuntimeError, match="exited during startup"):
        rt.start()
    assert _starts(rt) == 1


def test_mojo_names_the_fallback(tmp_path):
    from orchestrator.Core.mojo.checks.runtime import _kv_fallback
    assert _kv_fallback(tmp_path) == []
    (tmp_path / "server.json").write_text(json.dumps({"kv_cache": "f16", "kv_fallback": "q4_0"}))
    [finding] = _kv_fallback(tmp_path)
    assert finding.code == "runtime.kv_cache.fallback" and finding.severity.value == "warn"
    assert "q4_0" in finding.message and "f16" in finding.message and "flash attention" in finding.message
    assert "local_kv_cache" in finding.fix_hint
    (tmp_path / "server.json").write_text("{broken")
    assert _kv_fallback(tmp_path) == []


def test_the_cli_and_the_gui_texts_name_it():
    from orchestrator.utils.l10n_prompt_cache import TEXTS
    for lang in ("en", "de"):
        assert "{kind}" in TEXTS[lang]["prompt_cache.kv_fallback"]
