---
title: Tool permissions for Ditto and MCP clients
lang: en
---
# Tool permissions for Ditto and MCP clients

Ditto and AI clients connected over MCP use the framework through tools. Every tool belongs to a tier:

- **read**: list targets, show capabilities, read build logs, search the knowledge base;
- **local write**: start or cancel builds and other changes on this computer;
- **external**: anything that reaches other devices, such as deploying to a board, or that deletes data.

For each tier you decide once whether its tools run on their own (allow), need your confirmation (ask) or never
run (deny). Run `llm-cli ditto setup` the first time and `llm-cli tools permissions` to change the decision later;
`--reset` returns to the recommended defaults. A confirmation shows the tool's title and every argument. Without
a terminal or dialog to ask in, "ask" counts as "deny".

Every tool call, allowed or refused, is written to an audit log. `llm-cli tools audit` shows the latest entries,
and `llm-cli tools audit --verify` checks that no entry was changed or removed.
