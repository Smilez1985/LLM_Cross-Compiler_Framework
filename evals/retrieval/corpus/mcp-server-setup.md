---
title: Connecting AI clients over MCP
lang: en
---
# Connecting AI clients over MCP

The framework includes a Model Context Protocol server, so AI coding assistants and desktop AI apps can list
targets, start builds and read logs through the same tools Ditto uses, within the same tool permissions.

- `llm-cli mcp serve` runs the server on stdio. The client starts this command itself; stdout carries the
  protocol and logs go to stderr.
- `llm-cli mcp serve --transport http` serves Streamable HTTP on 127.0.0.1, port 8765 by default, protected by a
  bearer token. Only loopback addresses are accepted.
- `llm-cli mcp config` prints ready-to-copy configuration snippets for supported clients, and
  `--write <client>` adds the server to a client's configuration file (other entries are kept, a backup is
  written).

Sign in to the client with your own account or key; the framework never sees it. Set up the tool permissions
with `llm-cli ditto setup` before connecting a client, otherwise only read-only tools run without asking.
