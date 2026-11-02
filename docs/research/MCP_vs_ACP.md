Version 3.3.0 · Last updated 2026-11-02

# MCP vs ACP

[Deutsche Fassung](MCP_vs_ACP_DE.md) · Details on ACP: [ACP research](ACP.md)

Research date: 2026-11-02. All sources were accessed on that date. They are listed at the end and cited as [n].
Text marked **[Inference]** is our own conclusion. Text marked **[Unconfirmed]** could not be checked against a
primary source.

Three protocols are compared:

- **MCP**, the Model Context Protocol, revision 2026-07-28 (current) [1][2].
- **ACP**, the Agent Client Protocol from Zed and JetBrains, version 1, with version 2 in draft [12][27].
- **A2A**, the Agent2Agent protocol, version 1.0.x [32][33]. IBM's *Agent Communication Protocol*, also called ACP,
  **merged into A2A** in August 2025 and is archived [38][39][40]. It is not compared separately. Before the merger,
  IBM ACP itself described MCP as agent-to-tool and ACP as agent-to-agent [41]. Only IBM's ACP folded into A2A; the
  Agent Client Protocol is not affected by that merger [40][42].

## Contents

1. [Side-by-side comparison](#1-side-by-side-comparison)
2. [How they fit together](#2-how-they-fit-together)
3. [Overlap and gaps](#3-overlap-and-gaps)
4. [What it means for llmcf](#4-what-it-means-for-llmcf)
5. [Sources](#5-sources)

---

## 1. Side-by-side comparison

| Aspect | MCP (2026-07-28) | ACP, Agent Client Protocol (v1) | A2A (1.0.x; absorbed IBM ACP) |
|---|---|---|---|
| **Purpose** | Give an AI application tools, resources and prompts [1] | Let an editor drive a coding agent: prompts, diffs, terminals, approvals [12] | Let independent, possibly opaque agents delegate work to each other [32] |
| **Parties** | Host and its client (in the AI app) ↔ server (tools and data) [1] | Client (editor or IDE) ↔ agent [12] | Client agent ↔ remote agent (A2A server) [32] |
| **Direction** | Client → server requests; server → client responses and notifications only. Servers send no requests in this revision [3] | Both ways: the editor calls the agent (`session/prompt`); the agent calls the editor (`request_permission`, `fs/*`, `terminal/*`) [13] | Client agent → remote agent requests; remote agent → client through streams and push webhooks [32] |
| **Transport** | stdio and Streamable HTTP; custom transports allowed; HTTP+SSE deprecated [2][3] | stdio only is defined; remote Streamable HTTP and WebSocket in an Active RFD [17][28] | HTTP(S) with JSON-RPC, gRPC or HTTP+JSON/REST bindings; SSE for streaming; webhooks [32] |
| **Message format** | JSON-RPC 2.0 [1] | JSON-RPC 2.0; reuses MCP JSON types; content is a superset of MCP 2025-06-18 content [12][19] | Abstract data model (Task, Message, Part, Artifact), mapped to each binding; media type `application/a2a+json` [32][33] |
| **Session and state** | **Stateless** since 2026-07-28: no `initialize` handshake, no session id; state through server-minted handles in tool arguments [2] | **Stateful**: `session/new`, `load`, `resume`, `list`, `close`, `delete`; the agent holds history and context [15] | Task-centred: task ids plus `contextId`; the remote agent holds task state [32] |
| **Streaming** | Request-scoped SSE responses; `subscriptions/listen` for list changes; progress notifications [2][3] | `session/update` notifications: message chunks, thoughts, tool calls, plan, usage [13] | `SendStreamingMessage` and task subscription over SSE; push notifications [32] |
| **Long-running work** | Tasks extension `io.modelcontextprotocol/tasks` (`tasks/get`, `tasks/update`, `tasks/cancel`) [5] | A prompt turn ends with a stop reason; `session/cancel` [13] | Native task lifecycle with `INPUT_REQUIRED` and `AUTH_REQUIRED` [32] |
| **Human approval** | The host must get user consent before calling a tool, but MCP cannot enforce it; tool annotations are hints; elicitation through multi-round-trip requests (`input_required`, then retry) [1][2] | First-class: `session/request_permission` with `allow_once`, `allow_always`, `reject_once`, `reject_always`; the agent stores "always" decisions; elicitation (form, URL) stable since 2026-07-22 [16][20][22] | `INPUT_REQUIRED` and `AUTH_REQUIRED` task states; no dedicated approval dialog primitive **[Inference]** [32] |
| **File system and terminal** | None in the core. Roots are deprecated; paths go in tool parameters [2] | Optional client services `fs/read_text_file` (includes unsaved editor changes), `fs/write_text_file`, `terminal/*`; removed in the v2 draft [27][43][44] | None; agents exchange messages and artifacts only [32] |
| **Auth** | Optional. HTTP: OAuth 2.1 resource server with RFC 8707, RFC 9728, RFC 9207, client ID metadata documents; no token passthrough. stdio: credentials from the environment [4] | `authMethods` in `initialize` [14]; `authenticate` (agent signs in itself) or `terminal` (interactive sign-in); `logout` [21] | Security schemes in the Agent Card: API key, HTTP auth, OAuth 2.0 (authorization code with PKCE, client credentials, device code), OpenID Connect, mTLS [32] |
| **Discovery** | `server/discover` (versions, capabilities, identity); client configuration [2] | ACP Registry for agents; editor configuration [26] | Agent Card at `/.well-known/agent-card.json`, optionally signed; extended card after auth [32] |
| **SDKs** | Python `mcp` 2.3.0 (2026-10-02); standalone FastMCP 4.1.5 (2026-11-01); other languages exist [9][10][11] | Python `agent-client-protocol` 0.12.1 (1.0.0rc3 pre-release); TypeScript and Rust 1.0 (announced 2026-06-25); Kotlin `acp` 0.34.0, Java `acp-core` 0.80.0 [30][31][49][50] | Python `a2a-sdk` 1.3.0 (2026-10-27); five production languages: Python, JavaScript, Java, Go, .NET [35][37] |
| **Governance** | Agentic AI Foundation (AAIF) under the Linux Foundation since 2025-12-09 [7][8] | Zed and JetBrains, interim model, foundation planned; not an AAIF project [23][51] | Linux Foundation project since 2025-06-23; AAIF hosted project since 2026-08-17 [34][36] |
| **Maturity** | Five revisions since 2024-11-05; formal deprecation policy; 2026-07-28 was a breaking redesign [2][6][45] | v1 stable with many features stabilized in 2026; v2 draft since 2026-07-20 [22][27] | v1.0.0 on 2026-03-12, v1.0.1 on 2026-05-28 [33] |
| **Adoption** | Wide; llmcf is used from Claude Code, Claude Desktop and VS Code (repo docs). No count researched | 49 agents; 21 editors and IDEs plus about 130 further clients listed; about 4.6k GitHub stars [24][25][46]. Android Studio Rabbit 2 (2026-10-28) lets the person pick any "ACP-compliant agent" [53] | More than 150 organizations (2026-04-09) [35] |

---

## 2. How they fit together

The three protocols sit at different joints. They do not replace each other.

```
   person
     |
  +--------------------+
  | Editor / IDE       |   Zed, JetBrains, Neovim ...
  +--------------------+
     |  ACP  (JSON-RPC over stdio; prompts, diffs, approvals)
  +--------------------+         A2A  (HTTP; tasks)        +---------------+
  | Coding agent       | <-------------------------------> | Remote agent  |
  | (LLM loop)         |                                   +---------------+
  +--------------------+
     |  MCP  (JSON-RPC over stdio or HTTP; tools, resources)
  +--------------------+
  | MCP server         |   for example llmcf: targets, builds, logs, deploy
  +--------------------+
```

- **ACP over MCP.** The editor passes MCP server entries in `session/new.mcpServers`. The agent then connects to
  those servers as an MCP client. stdio entries are mandatory for every agent [15]. So an MCP server reaches every
  ACP agent whose editor lists it.
- **A2A beside MCP.** The A2A spec calls MCP complementary: a client agent asks a remote agent to do a task, and the
  remote agent may use MCP for its tools [32]. AAIF places MCP at the agent-to-tool layer and A2A at the
  agent-to-agent layer [36].
- **ACP and A2A** barely touch. ACP connects a person in an editor to an agent. A2A connects agents over a network.
  **[Inference]** One coding agent can speak ACP to the editor, A2A to other agents and MCP to tools.

---

## 3. Overlap and gaps

Overlap:

- **JSON-RPC 2.0** in MCP, ACP and one A2A binding [1][13][32].
- **Content types.** ACP content is a superset of MCP 2025-06-18 content, so MCP tool output can be passed through
  unchanged [19].
- **Elicitation.** ACP elicitation is based on the MCP 2026-07-28 release candidate, with form and URL modes [20].
  Both have a URL mode for sensitive flows (MCP since 2025-11-25) [20][45]. ACP says form mode must not be used for secrets [20].
- **Pause for input.** MCP `input_required`, the MCP tasks `input_required` state and A2A `INPUT_REQUIRED` follow a
  similar pattern [2][5][32]. **[Inference]**
- **Trace context.** MCP and ACP both reserve `traceparent`, `tracestate` and `baggage` in `_meta` [2][48].
- **MCP-over-ACP** (draft RFD) would carry MCP messages inside the ACP connection through `mcp/message` [29].

Differences that matter in practice:

- **State.** MCP dropped protocol sessions in 2026-07-28 [2]. ACP is built around sessions held by the agent [15].
- **Who asks the person.** MCP leaves consent to the host and cannot enforce it [1]. ACP gives the agent a
  permission request that the editor shows [16]. Both treat this as advisory; the server or client must still make
  its own checks [1][18][43].
- **Requests from the other side.** MCP servers send no requests in 2026-07-28 [3]. ACP agents call into the editor
  for permissions, files and terminals [13].
- **Small mismatches.** MCP 2026-07-28 removed `notifications/elicitation/complete` [2]. ACP keeps
  `elicitation/complete` for URL mode [20]. MCP moved "resource not found" from -32002 to -32602 [2]; ACP v1 still
  uses -32002 [47]. **[Inference]** A bridge has to translate these.

Gaps:

- **Remote ACP.** No stable remote transport yet [12][28].
- **Discovery.** MCP has `server/discover` per server; ACP has a curated registry; A2A has Agent Cards. They do not
  share a format [2][26][32].
- **Auth for local stdio.** MCP says stdio should not use OAuth and takes credentials from the environment [4]. ACP
  leaves sign-in to the agent [21]. Neither protocol authenticates the local peer process. **[Inference]**
- **Not a sandbox.** ACP states it plainly: the agent runs with its process's rights [18]. MCP likewise cannot
  enforce consent [1]. Safety stays in the implementations.
- **Governance.** MCP and A2A sit in a neutral foundation (AAIF). ACP does not yet [23][36][51].
- **IBM ACP** is archived, with the Python SDK `acp-sdk` frozen at 1.0.3 since 2025-08-21; the migration guide link
  in its README returns 404 [38][39][52].
  **[Inference]** Do not start new work on it.

---

## 4. What it means for llmcf

**[Inference]** for this whole section, based on the sources and the repository.

- **MCP stays the tool interface.** llmcf's MCP server (`orchestrator/mcp_server.py`, spec 2026-07-28, `mcp` 2.3.0)
  is the right layer for tools. Every call keeps going through `ToolRegistry.call()` with policy, confirmation,
  audit and output filter.
- **ACP support starts for free.** ACP agents in Zed, JetBrains and other editors can already use llmcf, because
  every ACP agent must accept stdio MCP servers [15]. The first step is documentation: a tested editor
  configuration that starts `llm-cli mcp serve`.
- **An ACP agent endpoint is a separate process.** If Ditto should run inside editors, it needs its own entry point
  (for example `llm-cli acp`). One stdio stream carries either ACP or MCP, never both [17]. That endpoint must:
  - map Ditto's turns to `session/update`;
  - route every action through the registry;
  - offer only once-options in permission requests, so standing approvals stay in llmcf's own window;
  - scrub the editor's absolute paths (`cwd`, roots) before they reach the model, as with every AI reader today;
  - never pass device account names or secrets to the AI;
  - accept editor-supplied MCP servers but connect only to those the person enabled, with results marked untrusted.
- **A2A only if agent-to-agent work is wanted.** It would fit next to MCP, for example llmcf's tools offered as A2A
  skills in an Agent Card. Nothing in today's design asks for it. It needs network exposure, which llmcf avoids
  today (loopback only).
- **Hosting other agents in the llmcf GUI** (llmcf as an ACP client) conflicts with the design rule that the framework
  never starts a client program itself. It needs a maintainer decision first.
- **Pin and wait.** ACP v2 is a draft, the Python SDK is below 1.0, and MCP-over-ACP is a draft [27][29][30]. Build
  on ACP v1 over stdio only, and keep the ACP package inside the new adapter.

---

## 5. Sources

All accessed 2026-11-02.

1. https://modelcontextprotocol.io/specification/latest
2. https://modelcontextprotocol.io/specification/2026-07-28/changelog
3. https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
4. https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
5. https://modelcontextprotocol.io/extensions/tasks/overview
6. https://blog.modelcontextprotocol.io/posts/2026-07-28/
7. https://blog.modelcontextprotocol.io/posts/2025-12-09-mcp-joins-agentic-ai-foundation/
8. https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation
9. https://pypi.org/pypi/mcp/json
10. https://github.com/modelcontextprotocol/python-sdk/releases
11. https://pypi.org/pypi/fastmcp/json
12. https://agentclientprotocol.com/overview/introduction
13. https://agentclientprotocol.com/protocol/overview
14. https://agentclientprotocol.com/protocol/v1/initialization.md
15. https://agentclientprotocol.com/protocol/v1/session-setup.md
16. https://agentclientprotocol.com/protocol/v1/tool-calls.md
17. https://agentclientprotocol.com/protocol/v1/transports.md
18. https://agentclientprotocol.com/protocol/v1/security.md
19. https://agentclientprotocol.com/protocol/v1/content.md
20. https://agentclientprotocol.com/protocol/v1/elicitation.md
21. https://agentclientprotocol.com/protocol/v1/authentication.md
22. https://agentclientprotocol.com/updates.md
23. https://agentclientprotocol.com/community/governance.md
24. https://agentclientprotocol.com/get-started/clients.md
25. https://agentclientprotocol.com/get-started/agents.md
26. https://agentclientprotocol.com/get-started/registry.md
27. https://agentclientprotocol.com/announcements/acp-v2-draft.md
28. https://agentclientprotocol.com/rfds/streamable-http-websocket-transport.md
29. https://agentclientprotocol.com/rfds/mcp-over-acp.md
30. https://pypi.org/pypi/agent-client-protocol/json
31. https://agentclientprotocol.com/announcements/sdk-1-0-releases.md
32. https://a2a-protocol.org/latest/specification/
33. https://github.com/a2aproject/A2A/releases
34. https://linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents
35. https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year
36. https://aaif.io/blog/a2a-joins-aaif
37. https://pypi.org/pypi/a2a-sdk/json
38. https://github.com/i-am-bee/acp
39. https://github.com/orgs/i-am-bee/discussions/5
40. https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/
41. https://agentcommunicationprotocol.dev/about/mcp-and-a2a.md
42. https://zuplo.com/blog/agent-protocol-stack-mcp-a2a-acp-2026
43. https://agentclientprotocol.com/protocol/v1/file-system.md
44. https://agentclientprotocol.com/protocol/v1/terminals.md
45. https://modelcontextprotocol.io/specification/2025-11-25/changelog
46. https://github.com/agentclientprotocol/agent-client-protocol
47. https://agentclientprotocol.com/protocol/v1/error.md
48. https://agentclientprotocol.com/protocol/v1/extensibility.md
49. https://repo1.maven.org/maven2/com/agentclientprotocol/acp/maven-metadata.xml
50. https://repo1.maven.org/maven2/com/agentclientprotocol/acp-core/maven-metadata.xml
51. https://aaif.io/projects/
52. https://pypi.org/pypi/acp-sdk/json
53. https://developer.android.com/blog/posts/ai-agents-android-studio-integration
