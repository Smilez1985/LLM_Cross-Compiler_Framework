"""How the ACP client reads the sign-in methods an agent offers (Core/acp_client.py `AgentSession._method`;
docs/DESIGN.md, "ACP agent (Ditto in editors)", the row on the SDK 1.0.0).

ACP v1 knows two methods: `terminal` (sent with `type: "terminal"`) and `agent`, the default, sent without `type`. The
SDK 1.0.0 reads a method without `type` as its terminal class too (the class's `type` has a default), so the client
counts `type` only when the agent sent it. A method of a type ACP v1 does not know reads as an agent method.
"""

import pytest

pytest.importorskip("acp")

from acp.schema import InitializeResponse  # noqa: E402

from orchestrator.Core.acp_client import AgentSession  # noqa: E402


def methods(*raw):
    """What the client reads from an agent's answer to initialize that offers these methods."""
    answer = InitializeResponse.model_validate({"protocolVersion": 1, "authMethods": list(raw)})
    return [AgentSession._method(m) for m in answer.auth_methods]


def test_a_method_without_type_is_an_agent_method_never_a_terminal_login():
    (method,) = methods({"id": "browser", "name": "Sign in with the browser"})
    assert method.type == "agent" and method.args == () and method.env == ()


def test_a_terminal_method_keeps_its_arguments_and_environment():
    (method,) = methods({"id": "console", "name": "At the console", "type": "terminal", "args": ["--login"],
                         "env": {"FAKE_LOGIN": "1"}})
    assert method.type == "terminal" and method.args == ("--login",) and method.env == (("FAKE_LOGIN", "1"),)


def test_both_kinds_in_one_answer_keep_their_order():
    found = methods({"id": "browser", "name": "Browser"},
                    {"id": "console", "name": "Console", "type": "terminal"})
    assert [(m.id, m.type) for m in found] == [("browser", "agent"), ("console", "terminal")]


def test_a_method_of_a_type_acp_v1_does_not_know_is_an_agent_method():
    (method,) = methods({"id": "keys", "name": "Keys", "type": "env_var", "vars": [{"name": "TOKEN"}]})
    assert method.type == "agent" and method.args == ()
