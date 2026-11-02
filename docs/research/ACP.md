Version 3.3.0 · Last updated 2026-11-02

# ACP research

[Deutsche Fassung](ACP_DE.md)

Research date: 2026-11-02. All sources were accessed on that date. They are listed at the end and cited as [n].
Facts come from the cited source. Text marked **[Inference]** is our own conclusion. Text marked **[Unconfirmed]**
could not be checked against a primary source.

## Contents

1. [Which ACP?](#1-which-acp)
2. [Agent Client Protocol in depth](#2-agent-client-protocol-in-depth)
3. [What ACP support means for llmcf](#3-what-acp-support-means-for-llmcf)
4. [Open points](#4-open-points)
5. [Sources](#5-sources)

---

## 1. Which ACP?

The request asks for "ACP (Agent Context Protocol)". Several protocols use the letters ACP. None of the
widely used ones is called "Agent Context Protocol".

| Name | Owner | What it does | Status on 2026-11-02 |
|---|---|---|---|
| **Agent Client Protocol** | Zed Industries, co-developed with JetBrains | Connects code editors (clients) to coding agents. JSON-RPC 2.0, mostly over stdio [1][2] | Active. Stable version 1, version 2 in draft [4][21] |
| Agent Communication Protocol | IBM Research / BeeAI | Agent-to-agent communication over REST (OpenAPI 0.2.0) [56][58] | Merged into **A2A** in August 2025. Repository archived 2025-08-27; SDK frozen at 1.0.3 since 2025-08-21 [59][60][61][63] |
| Agent Connect Protocol | AGNTCY (Cisco-donated, Linux Foundation project since 2025-07-29 [78]) | REST interface to call and configure remote agents [69] | Repository archived 2026-04-11 [69] |
| Agentic Commerce Protocol | OpenAI and Stripe | Checkout and orders between buyers, their agents and shops [68] | Beta. Unrelated to developer tools |
| "Agent Context Protocol" | Small academic or personal projects | Multi-agent coordination, or a documentation convention [70][71][72][73] | Fewer than 130 GitHub stars each; no editor or client implements them |

**This document focuses on the Agent Client Protocol.** Reasons:

- llmcf is a developer tool with an MCP server. The Agent Client Protocol is the only ACP that sits next to MCP in
  the developer-tool stack. It reuses MCP's JSON types and hands MCP servers to agents [2][5].
- It has real adoption: 49 agents are listed, and the clients page lists 21 editors and IDEs plus about 130 further
  clients (CLI, desktop, web, notebook, mobile, messaging) [30][31]. Zed, JetBrains, Google (Gemini CLI), GitHub
  Copilot and Cursor are involved [30][47].
- IBM's Agent Communication Protocol is no longer developed. Its team moved to A2A, and BeeAI now uses A2A
  [60][61][62]. A2A 1.0 is stable (v1.0.1 on 2026-05-28) [64][65]. **[Inference]** Since the merger, "ACP" in
  developer-tool contexts mostly means the Agent Client Protocol; no source states this directly ([67] only describes
  IBM's ACP joining A2A). IBM ACP itself described MCP as the agent-to-tool layer and ACP as the
  agent-to-agent layer [57]. If agent-to-agent work is ever wanted, the target is A2A, not IBM ACP. The companion document [MCP vs ACP](MCP_vs_ACP.md) covers A2A.
- **[Inference]** "Context" probably drifted in from "Model *Context* Protocol". The user should confirm this
  reading before design work starts.

Note: in this document, "ACP" from here on means the Agent Client Protocol.

---

## 2. Agent Client Protocol in depth

### 2.1 Purpose

- ACP standardizes communication between code editors or IDEs ("clients") and coding agents. Its model is the
  Language Server Protocol (LSP): any ACP agent should work in any ACP client [2].
- "ACP assumes that the user is primarily in their editor." The **agent** runs the LLM loop and its tools. The
  **client** provides the user interface, permission prompts, files and terminals [2].
- ACP reuses MCP's JSON representations where it can. It adds its own types for coding work, such as diffs. Text
  for the user is Markdown [2].
- Zed announced ACP on 2025-08-27. Gemini CLI was the first reference implementation, built with Google [47].
  The licence is Apache-2.0 [46][47].

### 2.2 Roles

| Role | Who | Implements |
|---|---|---|
| Client | The editor (Zed, a JetBrains IDE, Neovim, ...) | UI, permission prompts, optional file system and terminal services |
| Agent | The coding agent, usually a CLI program | The LLM loop, sessions, tool calls, and the JSON-RPC methods the client calls |

The client usually starts the agent as a subprocess [18]. Both sides send requests to each other.

### 2.3 Transport

- Message format: JSON-RPC 2.0 [3].
- **stdio is the only transport the spec defines** [18]. Messages are separated by a newline and must not contain
  embedded newlines. The agent may log UTF-8 text to stderr. It must not write anything to stdout that is not a
  valid ACP message.
- Custom transports are allowed. They must keep JSON-RPC and the ACP lifecycle, and should document their security,
  including peer authentication [16][18].
- Remote agents: "Full support for remote agents is a work in progress" [2]. The RFD "Streamable HTTP & WebSocket
  Transport" became **Active on 2026-07-02** (latest revision 2026-10-30) [24]. Its design: one `/acp` endpoint;
  GET with `Accept: text/event-stream` opens an SSE stream; GET with `Upgrade: websocket` opens a WebSocket; POST
  sends messages; DELETE ends the connection. Streamable HTTP must use HTTP/2. Headers `Acp-Connection-Id` and
  `Acp-Session-Id`. Clients that support remote ACP must support both Streamable HTTP and WebSocket [24].
- A Transports Working Group was formed on 2026-04-22 [28].
- **[Inference]** The remote transport is not part of the stable spec yet. Building on it today means building on a
  moving target.

### 2.4 Lifecycle

1. **Initialize.** The client sends `initialize` with the latest `protocolVersion` it supports and its
   capabilities. `protocolVersion` is one integer, the major version. The current stable version is **1**. The agent
   answers with the same version if it supports it, otherwise with its own latest version. If the client cannot use
   that version, it closes the connection [4].
2. **Authenticate** (only if the agent lists auth methods) [15].
3. **Create or restore a session.** `session/new` takes `cwd` (absolute path), `mcpServers` and optional
   `additionalDirectories`. It returns a `sessionId` [5]. `session/load` replays history; `session/resume` restores
   context without replay [5].
4. **Prompt turns.** The client sends `session/prompt`. The agent streams `session/update` notifications (text,
   thoughts, tool calls, plans). It may ask for permission or call client services. The turn ends when the agent
   answers the prompt request with a stop reason: `end_turn`, `max_tokens`, `max_turn_requests`, `refusal` or
   `cancelled` [7].
5. **Cancel** at any time with the `session/cancel` notification [7].
6. **Close** with `session/close` (idempotent) [5].

A request sent before `initialize` gets error -32600 [19].

### 2.5 Methods (version 1)

Client to agent (the agent implements these) [3][5][6]:

| Method | Kind | Required? |
|---|---|---|
| `initialize` | request | yes |
| `authenticate` | request | yes, when the agent lists auth methods |
| `session/new` | request | yes |
| `session/prompt` | request | yes |
| `session/cancel` | notification | yes |
| `session/load` | request | optional (`agentCapabilities.loadSession`) |
| `session/resume` | request | optional (stabilized 2026-04-22) |
| `session/close` | request | optional (stabilized 2026-04-23) |
| `session/list` | request | optional (stabilized 2026-03-09) |
| `session/delete` | request | optional (stabilized 2026-06-05) |
| `session/set_mode` | request | optional, being phased out in favour of config options |
| `session/set_config_option` | request | optional |
| `logout` | request | optional (stabilized 2026-05-21) |

Agent to client (the client implements these) [3][12][13][14]:

| Method | Kind | Needs client capability |
|---|---|---|
| `session/update` | notification | none (baseline) |
| `session/request_permission` | request | none (baseline) |
| `fs/read_text_file` | request | `fs.readTextFile` |
| `fs/write_text_file` | request | `fs.writeTextFile` |
| `terminal/create`, `terminal/output`, `terminal/wait_for_exit`, `terminal/kill`, `terminal/release` | request | `terminal` |
| `elicitation/create` | request | `elicitation.form` or `elicitation.url` |
| `elicitation/complete` | notification | URL mode |

Kinds of `session/update` [7]: `user_message_chunk`, `agent_message_chunk`, `agent_thought_chunk`, `tool_call`,
`tool_call_update`, `plan`, `available_commands_update`, `current_mode_update`, `config_option_update`,
`session_info_update`, `usage_update`, `notice`, `compaction_update`, `compaction_summary_chunk` (the last three
need client opt-in).

Error codes [19]: the JSON-RPC standard codes, plus -32800 request cancelled, -32000 authentication required,
-32002 resource not found.

Custom methods start with `_`. Custom data goes into `_meta`. The keys `traceparent`, `tracestate` and `baggage`
are reserved for W3C trace context [17].

### 2.6 Capabilities

Negotiated once in `initialize`. Anything not advertised counts as unsupported [4].

- **Client:** `fs.readTextFile`, `fs.writeTextFile`, `terminal`, `auth.terminal`, `elicitation` (`form` and/or `url`;
  an empty object advertises nothing), `session.configOptions.boolean`, `session.compaction`, `session.notices` [4].
- **Agent:** `loadSession`; `promptCapabilities` (`image`, `audio`, `embeddedContext`; text and resource links are
  always supported); `mcpCapabilities` (`http`, `sse`); `auth.logout`; `sessionCapabilities` with `delete` and
  `additionalDirectories` [4], and `resume` and `close` [5].
- Both sides should send `clientInfo` / `agentInfo` (`name`, `title`, `version`). These are self-reported labels and
  should not drive security decisions [4][16].

### 2.7 Content and tool-call reporting

- ACP's `ContentBlock` "is compatible with, and a superset of" the MCP 2025-06-18 `ContentBlock`. MCP tool output
  can be passed through unchanged [8].
- Block types: `text` (Markdown, required), `image` and `audio` (base64, need capabilities), `resource` (embedded,
  needs `embeddedContext`), `resource_link` (required) [8].
- A **tool call** is reported with `tool_call` (required: `toolCallId`, `title`). Later changes come as
  `tool_call_update` with only the changed fields [9].
  - `kind`: `read`, `edit`, `delete`, `move`, `search`, `execute`, `think`, `fetch`, `switch_mode`, `other`.
  - `status`: `pending`, `in_progress`, `completed`, `failed`.
  - `content`: a content block, a **diff** (`path`, `oldText`, `newText`) or an embedded **terminal**.
  - `locations`: `{path, line?}` with 1-based lines. Display only; they grant no access.
  - `name` (stabilized 2026-09-17) is informational and grants nothing [9][28].
- **Plan:** a list of entries with priority and status. Each update replaces the whole plan [10].
- **Slash commands:** the agent announces them with `available_commands_update`. A command is sent as normal prompt
  text, `/name args` [11].
- **Config options** replace session modes. Categories: `mode`, `model`, `model_config`, `thought_level`, custom.
  Types: `select`, and `boolean` if the client supports it [6].

### 2.8 Permissions

- `session/request_permission` sends `sessionId`, the `toolCall` and a list of options. Each option has a kind:
  `allow_once`, `allow_always`, `reject_once`, `reject_always`. The answer is `selected` with an `optionId`, or
  `cancelled` [9].
- **The agent stores remembered decisions** ("allow always"). The option kind is only a hint for the UI [9].
- On `session/cancel`, the client answers open permission requests with `cancelled` [7].
- Permission requests are advisory. The client must make its own checks too: "A permission request from the Agent
  does not replace the Client's own checks" [12][16].

### 2.9 File system, terminals and elicitation

- `fs/read_text_file` reads a file, **including unsaved changes in the editor**. `fs/write_text_file` writes one.
  Paths are absolute. The client checks each path against the session's root set (`cwd` plus
  `additionalDirectories`) [5][12].
- `terminal/create` starts a command in the client and returns a `terminalId` at once. Output, exit status, kill and
  release are separate calls [13].
- **Elicitation** (stabilized 2026-07-22): `form` mode (a flat schema) or `url` mode (for sensitive flows such as
  OAuth). Form mode must not be used for secrets. An agent must not fall back to form mode when URL mode is missing.
  It is based on the MCP 2026-07-28 release-candidate elicitation spec [14].

### 2.10 How ACP works with MCP

- The client passes **`mcpServers`** in `session/new` (and `load` / `resume`). **The agent then connects to those
  MCP servers as an MCP client** [5].
- Entry kinds: **stdio, which every agent must support** (`name`, `command` as absolute path, `args`, `env`);
  `http` (needs `mcpCapabilities.http`); `sse` (needs `mcpCapabilities.sse`; MCP has deprecated SSE) [4][5].
- **MCP-over-ACP** (draft RFD): the client declares an MCP server with `"type": "acp"`. MCP messages then travel
  over the existing ACP connection through one method, `mcp/message`. No extra process or listener is needed. It
  targets the stateless MCP 2026-07-28 release [25].
- **Proxy chains** (RFD, implemented in the Rust SDK): `proxy/initialize` and `proxy/successor`. A proxy can inject
  an MCP server into an agent through MCP-over-ACP [26].

### 2.11 Authentication and security

- The agent lists `authMethods`. Type `agent` (default): the agent signs in by itself after the client calls
  `authenticate`. Type `terminal`: the client runs the agent program interactively for sign-in (needs
  `auth.terminal`) [15]. A gated request returns -32000 [19].
- Security model [16]:
  - "The Agent runs with whatever permissions its process is given." ACP is **not a sandbox**.
  - Every string, path, URI and tool result from the agent, MCP results included, is untrusted data.
  - Clients should neutralize terminal control sequences, show bidi and zero-width characters, and never open links
    or run scripts without user action.
  - Agent-supplied remote URIs: only `https` or `data`, no credentials, no loopback or private targets.
  - Agents should ask before writing files that control execution or approval (hooks, settings, MCP configuration),
    and before sending data off the machine after reading untrusted content.
  - No credentials in `_meta`. Session and tool-call ids are not credentials.
  - Multi-user agents scope sessions, permissions and elicitations to the session's owner.
- Security reports go to the Zed security address listed on the governance page [29].

### 2.12 SDKs and versions

| Language | Package | Version | Source |
|---|---|---|---|
| Python | `agent-client-protocol` (import `acp`) | stable 0.12.1 (2026-08-16); pre-release 1.0.0rc3 (2026-10-12); Python 3.10 to 3.14 | [42] |
| TypeScript | `@agentclientprotocol/sdk` | 2.2.0 (2026-10-30); 1.0.0 published 2026-06-24, announced 2026-06-25 | [43][22] |
| Rust | `agent-client-protocol` | 3.6.0 (2026-10-28); 1.0.0 published 2026-06-24, announced 2026-06-25 | [45][22] |
| Rust schema | `agent-client-protocol-schema` | 1.15.0 (2026-11-01) | [46] |
| Kotlin | `com.agentclientprotocol:acp` | 0.34.0 on Maven Central (metadata updated 2026-10-21); the docs page still shows `0.1.0-SNAPSHOT`; JVM only | [35][79] |
| Java | `com.agentclientprotocol:acp-core` (plus `acp-json-jackson2` / `acp-json-jackson3`) | 0.80.0 (2026-10-08) | [34][80][81] |

- Latest schema release: v1.29.0 (2026-11-01), next to v2.0.0-alpha.12 [39].
- The Python SDK is generated from the upstream schema. It provides an `Agent` base class, `run_agent()`, a client
  helper that spawns agent processes, and helpers for session accumulation, tool-call tracking and permission
  brokering [37][40]. The docs have a testing page [36] (not read in detail).
- **ACP v2** has been a **draft since 2026-07-20**. It removes `session/load`, `session/set_mode`, `fs/*` and all
  `terminal/*` methods (a separate RFD explains the removal: the surface was not widely adopted), renames `authenticate` to
  `auth/login`, and replaces `tool_call` by the first `tool_call_update`. The announcement says pieces "can, and
  will, change" and "Adding v2 support should not mean dropping v1" [20][21][27].

### 2.13 Adopters

- **Editors and clients** [31]: Zed, JetBrains IDEs, Neovim (several plugins), Emacs, VS Code (through extensions),
  Visual Studio, Sublime Text, Qt Creator, Pulsar, Obsidian, Unity, a Chrome client, and notebook tools such as
  marimo, Jupyter and DuckDB.
- **Agents** [30]: 49 listed, among them Gemini CLI, Goose, GitHub Copilot (public preview), Cursor, Junie
  (JetBrains), Cline, OpenCode, OpenHands, Kiro CLI, Qwen Code, Kimi CLI, Mistral Vibe, Augment Code, Docker cagent.
  Claude Agent [41][44], Codex CLI and Pi connect through adapters.
- **ACP Registry** [32][33]: a curated list that JetBrains IDEs (2025.3.2 or later, with JetBrains AI
  253.30387.147) can install from [52]. It only lists agents that support authentication. It went live on
  2026-01-28 (Zed blog [49]; JetBrains blog January 2026 [52]; heise 2026-01-30 [53]). The ACP updates page dates
  "ACP Registry is Released" (RFD moved to Completed) to 2026-03-09 [28]. The registry showed 43 agents [33]; its
  JSON lists the distribution kinds `npx` (23 agents), `binary` (20) and `uvx` (2) [82].
- The main repository had about 4.6k stars [38].
- Zed 1.0 shipped on 2026-04-29; the announcement names ACP as the way Zed runs Claude Agent, Codex, OpenCode and
  Cursor [55].
- Microsoft did not plan to add ACP to VS Code natively "right away" (October 2025) [51]. Current VS Code support
  is through extensions [31].

### 2.14 Governance, maturity and open issues

- **Governance:** Zed and JetBrains govern ACP jointly under an interim model. The stated goal is an independent
  foundation later. Lead maintainers come from Zed and JetBrains [29][28]. Changes go through RFDs with the stages
  Draft, Active, Preview, Completed; only Completed carries a stability promise [23]. ACP is not listed as an
  Agentic AI Foundation project; MCP and A2A are [66][77].
- **Maturity:** v1 is stable, and many features were stabilized in 2026 (session list, resume, close, delete,
  elicitation, tool-call names) [28]. The Rust and TypeScript SDKs reached 1.0 (announced 2026-06-25) [22]. The Python SDK is
  still below 1.0, with migration guides for 0.7, 0.8 and 0.11 [37][42].
- **Open issues:**
  - No stable remote transport [2][24].
  - v2 is a draft and drops the v1 file system and terminal methods [20].
  - MCP-over-ACP and proxy chains are drafts [25][26].
  - Session modes are being replaced by config options [6].
  - Protocol pace: several schema releases per week in late October 2026 [39].

---

## 3. What ACP support means for llmcf

### 3.1 Starting point in the repository

- `orchestrator/mcp_server.py` serves the framework's tool registry over MCP (spec 2026-07-28 [74][75], `mcp` 2.3.0 [76]). stdio is
  the default. Streamable HTTP binds to loopback only and needs a named token per client.
- Every call goes through `ToolRegistry.call()`: input schema, the person's stored permissions, confirmation,
  audit log, output filter. An "ask" permission becomes an MCP elicitation, or a pending approval in the framework.
- Ditto is the built-in assistant (`orchestrator/Core/ditto_manager.py`, `DittoManager.chat`). It is optional and
  marked experimental. Its hands are the same registry tools.
- Today Ditto has no MCP client: no module in `orchestrator/` uses the `mcp` package except the server, the CLI and
  the GUI.
- Design rules that matter here (docs/DESIGN.md):
  - An AI never sees host paths, device account names or secrets; folders get labels, devices speaking names.
  - Standing approvals ("do not ask again") are created only in the framework's own window.
  - The framework never starts a client program itself.
  - There is no "ask Ditto" tool; an MCP client takes Ditto's job itself.

**Key point:** ACP is not a second dialect of MCP. In ACP the editor is the client and the agent answers it. A
tools-only MCP server cannot "also speak ACP" on the same stream: an ACP agent must write only ACP messages to
stdout [18], and it is expected to run an LLM prompt turn [2]. **[Inference]** "llmcf supports ACP" therefore means
one of the options below.

### 3.2 Already working without code: llmcf as an MCP server for ACP agents

Every ACP agent must accept stdio MCP servers from the editor [5]. A person who uses Claude Agent, Gemini CLI or
another ACP agent in Zed or a JetBrains IDE can already add llmcf's MCP server (`llm-cli mcp serve`) as an stdio entry.
The agent then uses llmcf's tools with all existing checks. **[Inference]** This needs only documentation: a
config example per editor, and a note that the command must be an absolute path.

### 3.3 Option A: llmcf as an ACP agent (Ditto in the editor)

What it is: a new entry point, for example `llm-cli acp`, that speaks ACP over stdio. The editor starts it. The
person chats with Ditto inside Zed, JetBrains, Neovim or Emacs. Ditto works with the framework's own tools.

Mapping (**[Inference]**, from the spec and the code):

| ACP | llmcf |
|---|---|
| `session/prompt` | `DittoManager.chat(question, history)` |
| `agent_message_chunk` / `agent_thought_chunk` | streamed text from the LLM layer |
| `tool_call` / `tool_call_update` | a registry call with its audit and job id; build progress as `in_progress`, log excerpts as content |
| `session/request_permission` | the "ask" permission of the tool policy |
| `elicitation/create` (form) | today's confirmation questions; never for secrets |
| `plan` | the build stages (source, config, convert, target) |
| `available_commands_update` | for example `/build`, `/status`, `/deploy`, `/mojo` |
| `session/set_config_option` | Ditto's provider and model choice |
| `session/cancel` | the existing cancel token of the turn and its jobs |
| `authMethods` type `terminal` | the existing sign-in (`llm-cli ditto login`) |
| `session/new.mcpServers` | needs a new MCP client in Ditto (see risks) |

Effort **[Inference]**: moderate. One new adapter module in the adapter layer, next to `cli.py` and `mcp_server.py`.
The Python SDK brings JSON-RPC and typed models. Most work is mapping Ditto's turn events to `session/update`,
plus tests. A minimal MCP client for stdio servers is extra work.

Risks:
- **Mandatory stdio MCP support.** The spec says every agent must support stdio `mcpServers` [5]. Ditto has no MCP
  client today. **[Inference]** Editor-supplied servers bring foreign tools that do not go through
  `ToolRegistry.call()`. They should be listed but offered only after the person enables them in llmcf, with their
  results marked untrusted. This matches the planned board bridge rule in DESIGN.md.
- **Duplicate value.** The design says an MCP client should take Ditto's job. In an editor that already runs Claude
  Agent or Gemini CLI, the person can use llmcf through MCP (3.2). **[Inference]** Option A is mainly useful for
  people who want Ditto's local model, or who have no other agent.
- **Moving protocol.** v2 is a draft and the Python SDK is below 1.0 [21][42].
- **Experimental notice.** Ditto's one-time "use at your own risk" notice must also reach editor users.

Security (**[Inference]**, building on [16] and DESIGN.md):
- **Approvals:** the ACP adapter calls the same `ToolRegistry.call()` path. Offer only `allow_once` and
  `reject_once` in permission requests. Standing approvals stay in the framework's own window, as today. Person-only
  decisions (deletions, licences, first trust of a host key, the AI's own permissions) keep their current rules. A
  question that cannot be asked in the editor becomes a pending approval, as with MCP.
- **Path scrubbing:** `cwd` and `additionalDirectories` from the editor are absolute host paths. They must not reach
  the model as they are. Use the existing labels (`security/paths.scrub_for_ai`, folder labels). Tool-call
  `locations` are display-only, but the same filter should apply to anything that also goes into the model's context.
- **No device accounts to the AI:** the existing rule (device account names and paths never reach the AI) applies
  unchanged. Board names are speaking names.
- **Editor file system and terminal:** do not use `fs/*` or `terminal/*` for builds or deploys. These would bypass
  the registry. If used at all, only for reading files inside the root set, after the person allows it. v2 removes
  these methods anyway [20].
- **Secrets:** never through form elicitation [14]; sign-in through the `terminal` auth method or the framework.
- **Labels are labels:** `clientInfo` is a label for people, as with the MCP ClientTracker today [4][16].
- **stdout discipline:** the ACP process must log only to stderr [18].

### 3.4 Option B: llmcf as an ACP client (external agents in the llmcf GUI)

What it is: the llmcf GUI chat panel hosts external ACP agents (Claude Agent, Gemini CLI, Codex through adapters)
next to Ditto or instead of it. llmcf would show their output and answer `request_permission`, `fs/*` and
`terminal/*`. It would pass its own MCP server in `mcpServers`.

Effort **[Inference]**: high. llmcf must implement the client side, a file system and terminal service under its
own security rules, and agent discovery (registry or config).

Risks:
- It contradicts the design rule that the framework never starts a client program itself. A maintainer decision
  would be needed first.
- llmcf would run third-party agent binaries with the person's rights. ACP is not a sandbox [16].
- The agents' sign-in and subscriptions would run through llmcf's window.

### 3.5 Option C: an ACP-to-MCP bridge

Two readings:
1. **A tools-only ACP agent** that maps prompts to MCP tool calls without an LLM. **[Inference]** Not useful: ACP
   expects an agent with a prompt turn [2], and editors already pass MCP servers to real agents (3.2).
2. **MCP-over-ACP** [25]: the editor or a proxy carries llmcf's MCP traffic inside the ACP connection
   (`mcp/message`). It targets MCP 2026-07-28, the version llmcf's server implements. **[Inference]** This could
   later remove the second process. It is a draft RFD today, and in this model the bridge lives in the editor or
   proxy, not in llmcf. llmcf would only need to accept MCP messages from a non-stdio channel.

Effort **[Inference]**: none now; watch the RFD. Risk: building on a draft.

### 3.6 Recommendation

**[Inference]**

1. **Now:** document 3.2. llmcf already works with every ACP agent through its MCP server. Add tested config
   snippets for Zed and a JetBrains IDE. No protocol code.
2. **Next, if the maintainer wants Ditto in editors:** Option A over stdio only, on ACP v1. Pin the Python SDK
   (`agent-client-protocol` 0.12.1, or 1.0.0 once final). Keep the package only in the new adapter, like `mcp` and
   uvicorn today. Route every action through `ToolRegistry.call()`. Offer no "always" options. Scrub paths. Accept
   `mcpServers`, but connect only to servers the person enabled.
3. **Later or never:** Option B only after a maintainer decision on starting client programs. Option C only when
   MCP-over-ACP leaves draft.
4. **Not IBM ACP.** For agent-to-agent work, look at A2A instead (see [MCP vs ACP](MCP_vs_ACP.md)).
5. Before any design: confirm with the user that "ACP" means the Agent Client Protocol.

---

## 4. Open points

- Whether the user meant the Agent Client Protocol (most likely) or agent-to-agent communication (then A2A).
- Whether `mcp` 2.3.0 content types convert directly into ACP SDK types. The spec says ACP content is a superset of
  MCP 2025-06-18 [8]; not tested.
- The Kotlin and Java SDK versions come from Maven Central and the Java SDK README; their release notes were not
  read.
- JetBrains' own October 2025 announcement was not read; the Zed blog (2025-10-06) [48] and a secondary source [50]
  confirm that JetBrains co-develops ACP.

---

## 5. Sources

All accessed 2026-11-02.

1. https://agentclientprotocol.com/
2. https://agentclientprotocol.com/overview/introduction
3. https://agentclientprotocol.com/protocol/overview
4. https://agentclientprotocol.com/protocol/v1/initialization.md
5. https://agentclientprotocol.com/protocol/v1/session-setup.md
6. https://agentclientprotocol.com/protocol/v1/session-config-options.md
7. https://agentclientprotocol.com/protocol/v1/prompt-turn.md
8. https://agentclientprotocol.com/protocol/v1/content.md
9. https://agentclientprotocol.com/protocol/v1/tool-calls.md
10. https://agentclientprotocol.com/protocol/v1/agent-plan.md
11. https://agentclientprotocol.com/protocol/v1/slash-commands.md
12. https://agentclientprotocol.com/protocol/v1/file-system.md
13. https://agentclientprotocol.com/protocol/v1/terminals.md
14. https://agentclientprotocol.com/protocol/v1/elicitation.md
15. https://agentclientprotocol.com/protocol/v1/authentication.md
16. https://agentclientprotocol.com/protocol/v1/security.md
17. https://agentclientprotocol.com/protocol/v1/extensibility.md
18. https://agentclientprotocol.com/protocol/v1/transports.md
19. https://agentclientprotocol.com/protocol/v1/error.md
20. https://agentclientprotocol.com/protocol/v2/migration.md
21. https://agentclientprotocol.com/announcements/acp-v2-draft.md
22. https://agentclientprotocol.com/announcements/sdk-1-0-releases.md
23. https://agentclientprotocol.com/rfds/about.md
24. https://agentclientprotocol.com/rfds/streamable-http-websocket-transport.md
25. https://agentclientprotocol.com/rfds/mcp-over-acp.md
26. https://agentclientprotocol.com/rfds/proxy-chains.md
27. https://agentclientprotocol.com/rfds/v2/client-filesystem-terminal-capabilities.md
28. https://agentclientprotocol.com/updates.md
29. https://agentclientprotocol.com/community/governance.md
30. https://agentclientprotocol.com/get-started/agents.md
31. https://agentclientprotocol.com/get-started/clients.md
32. https://agentclientprotocol.com/get-started/registry.md
33. https://agentclientprotocol.com/registry
34. https://agentclientprotocol.com/llms.txt
35. https://agentclientprotocol.com/libraries/kotlin.md
36. https://agentclientprotocol.com/libraries/testing.md
37. https://agentclientprotocol.github.io/python-sdk/quickstart/
38. https://github.com/agentclientprotocol/agent-client-protocol
39. https://github.com/agentclientprotocol/agent-client-protocol/releases
40. https://github.com/agentclientprotocol/python-sdk
41. https://github.com/agentclientprotocol/claude-agent-acp
42. https://pypi.org/pypi/agent-client-protocol/json
43. https://registry.npmjs.org/@agentclientprotocol/sdk/latest
44. https://registry.npmjs.org/@agentclientprotocol/claude-agent-acp/latest
45. https://crates.io/api/v1/crates/agent-client-protocol
46. https://crates.io/api/v1/crates/agent-client-protocol-schema
47. https://zed.dev/blog/bring-your-own-agent-to-zed
48. https://zed.dev/blog/jetbrains-on-acp
49. https://zed.dev/blog/acp-registry
50. https://tessl.io/blog/jetbrains-joins-agent-client-protocol-to-open-ai-coding-agents-to-any-ide/
51. https://www.theregister.com/2025/10/07/jetbrains_acp_vs_code/
52. https://blog.jetbrains.com/ai/2026/01/acp-agent-registry/
53. https://www.heise.de/en/news/Integrate-AI-Agents-into-the-Editor-JetBrains-and-Zed-Release-ACP-Registry-11160709.html
54. https://www.morphllm.com/agent-client-protocol
55. https://zed.dev/blog/zed-1-0
56. https://agentcommunicationprotocol.dev/introduction/welcome.md
57. https://agentcommunicationprotocol.dev/about/mcp-and-a2a.md
58. https://raw.githubusercontent.com/i-am-bee/acp/main/docs/spec/openapi.yaml
59. https://github.com/i-am-bee/acp
60. https://github.com/orgs/i-am-bee/discussions/5
61. https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/
62. https://framework.beeai.dev/integrations/a2a
63. https://pypi.org/pypi/acp-sdk/json
64. https://a2a-protocol.org/latest/specification/
65. https://github.com/a2aproject/A2A/releases
66. https://aaif.io/blog/a2a-joins-aaif
67. https://zuplo.com/blog/agent-protocol-stack-mcp-a2a-acp-2026
68. https://github.com/agentic-commerce-protocol/agentic-commerce-protocol
69. https://github.com/agntcy/acp-spec
70. https://github.com/agent-context-protocol/agent-context-protocol
71. https://huggingface.co/papers/2505.14569
72. https://github.com/prmichaelsen/agent-context-protocol
73. https://github.com/agx-dev/acp
74. https://modelcontextprotocol.io/specification/latest
75. https://modelcontextprotocol.io/specification/2026-07-28/changelog
76. https://pypi.org/pypi/mcp/json
77. https://aaif.io/projects/
78. https://www.linuxfoundation.org/press/linux-foundation-welcomes-the-agntcy-project-to-standardize-open-multi-agent-system-infrastructure-and-break-down-ai-agent-silos
79. https://repo1.maven.org/maven2/com/agentclientprotocol/acp/maven-metadata.xml
80. https://repo1.maven.org/maven2/com/agentclientprotocol/acp-core/maven-metadata.xml
81. https://github.com/agentclientprotocol/java-sdk
82. https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json
