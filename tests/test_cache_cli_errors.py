"""`llmcf ditto cache`, `ditto cache set` and `ditto models list` print a plain error, never a traceback, when the
memory budget or the model choice cannot be worked out (chain followups-3): a missing model catalogue entry, a probe
that fails, a damaged file. The text is redacted and the exit code is 1."""

from click.testing import CliRunner

from orchestrator import cli as cli_module
from orchestrator.runtime.llama_server import LocalRuntimeError

SECRET = "hf_" + "A" * 34


class Broken:
    """A runtime whose budget and model choice fail the way a probe or a catalogue can."""

    def __init__(self, error):
        self.error = error
        self.settings = None

    def _fail(self, *args, **kwargs):
        raise self.error

    cache_budget = cache_settings = choose_tier = status = _fail


def _run(monkeypatch, error, *args):
    monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: Broken(error))
    return CliRunner().invoke(cli_module.cli, ["-q", "ditto", *args])


def _plain(out):
    assert out.exit_code == 1, out.output
    assert "Traceback" not in out.output and out.exception is None or isinstance(out.exception, SystemExit)


def test_cache_show_says_it_plainly(monkeypatch):
    out = _run(monkeypatch, LocalRuntimeError("The model 'tiny.gguf' is not in the catalogue."), "cache")
    _plain(out)
    assert "memory budget" in " ".join(out.output.split()) and "tiny.gguf" in out.output


def test_cache_set_says_it_plainly(monkeypatch):
    out = _run(monkeypatch, OSError(f"cannot read the probe (token {SECRET})"), "cache", "set", "ram", "auto")
    _plain(out)
    assert "memory budget" in out.output and SECRET not in out.output


def test_models_list_says_it_plainly(monkeypatch):
    out = _run(monkeypatch, ValueError("free RAM could not be measured"), "models", "list")
    _plain(out)
    assert "free RAM could not be measured" in " ".join(out.output.split())
