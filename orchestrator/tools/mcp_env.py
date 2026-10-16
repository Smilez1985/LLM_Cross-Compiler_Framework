"""
The environment variable that carries a stdio client's MCP token.

It has its own module so `llm-cli` can take the token out of the environment before any command runs without
importing the token store (lazy loading); orchestrator.tools.mcp_tokens re-exports it.
"""

ENV_VAR = "LCF_MCP_TOKEN"      # the token of a stdio client, read once by `llm-cli mcp serve`
