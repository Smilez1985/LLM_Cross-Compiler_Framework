"""`llm-cli mcp serve --container`: the HTTP server of a framework that runs inside a container the person built
locally binds the container's interfaces, and only there; the token, Host and Origin checks stay the same. (Moved here
from the removed remote-mode tests: the container setup serves external MCP clients, not Ditto.)
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from orchestrator import mcp_server  # noqa: E402


# --- container mode for the HTTP server --------------------------------------------------------------------------

class TestContainerMode:

    def test_loopback_only_without_the_flag(self):
        assert mcp_server.http_bind("127.0.0.1", False) == "127.0.0.1"
        with pytest.raises(ValueError, match="--container"):
            mcp_server.http_bind("0.0.0.0", False)

    def test_the_flag_binds_the_container_interfaces_only_inside_a_container(self):
        assert mcp_server.http_bind("127.0.0.1", True, inside=True) == "0.0.0.0"
        with pytest.raises(ValueError, match="only for a framework running inside a container"):
            mcp_server.http_bind("127.0.0.1", True, inside=False)

    def test_the_container_server_keeps_the_token_and_origin_checks(self):
        # The app in front of the server is the same TokenAuth with loopback Host names (the published port).
        assert mcp_server.allowed_hosts(8765) == ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"]

    def test_serve_refuses_container_mode_outside_a_container(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "in_container", lambda: False)
        with pytest.raises(ValueError, match="inside a container"):
            mcp_server.serve(SimpleNamespace(tool_registry=None), transport="http", container=True)

    def test_the_cli_flag_reaches_the_server(self, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        from test_review_mcp import FakeRegistryContext
        served = []
        monkeypatch.setattr(mcp_server, "serve", lambda framework, **kwargs: served.append(kwargs))
        registry = SimpleNamespace(policy=lambda: SimpleNamespace(configured=True),
                                   policy_store=SimpleNamespace(problem=None))
        result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "serve", "--transport", "http", "--container"],
                                    obj=FakeRegistryContext.make(registry))
        assert result.exit_code == 0, result.output
        assert served and served[0]["container"] is True
