Version 3.4.0 · Last updated 2026-11-06

**English** · [Deutsch](Editors_and_Agents_DE.md)

# Editors and agents: setup guide

This guide connects the framework to editors that speak the Agent Client Protocol (ACP): Android Studio, Zed and
JetBrains IDEs. There are three ways, and you can use any of them on its own:

| Part | Who is the AI agent | Who starts what |
|---|---|---|
| [A: the framework's MCP server in an editor's agent](#a-the-frameworks-mcp-server-in-an-editors-agent) | An agent of the editor (Claude Agent, Codex, Gemini CLI, ...) | The editor starts the agent; the agent starts `llm-cli mcp serve` |
| [B: Ditto as the editor's agent](#b-ditto-as-the-editors-agent) | Ditto, the framework's own assistant | The editor starts `llm-cli acp` |
| [C: agents in the framework's own window](#c-agents-in-the-frameworks-own-window) | An agent you add to the framework | The framework starts the agent and gives it its MCP server |

In every case the agent acts only through the framework's tools: every call goes through your tool permissions, the
approvals and the audit log, as for any AI app over MCP ([Features: MCP server](Features.md#mcp-server)).

Facts about the editors come from their own documentation (see [Sources](#sources), all read on 2026-11-06). A
statement marked **[Unconfirmed]** could not be checked against a primary source.

## Contents

- [Before you start: copy the entries, never type them](#before-you-start-copy-the-entries-never-type-them)
- [A: the framework's MCP server in an editor's agent](#a-the-frameworks-mcp-server-in-an-editors-agent)
- [B: Ditto as the editor's agent](#b-ditto-as-the-editors-agent)
- [C: agents in the framework's own window](#c-agents-in-the-frameworks-own-window)
- [Troubleshooting](#troubleshooting)
- [Sources](#sources)

## Before you start: copy the entries, never type them

An editor starts the framework as a program of its own. The entry it needs names absolute paths of this
installation: the Python interpreter of the framework's virtual environment, the arguments
`-P -m orchestrator.cli -q` followed by `mcp serve` (part A) or `acp` (part B), and the variable `PYTHONPATH` set to
the installation folder. `-P` keeps the folder the editor starts it in off Python's import path. The framework makes
these entries itself, from the same launch command the framework uses when it starts the MCP server for an agent of
its own (part C):

- **In the GUI:** **Security > AI App Connections (MCP)...** shows the MCP server entries (Claude Code, Claude
  Desktop, VS Code, Zed), each with a **Copy** button. **File > Settings...**, page **Ditto in editors**, shows the
  entries for Ditto (the command, the Zed entry, the JetBrains entry), each with a **Copy** button.
- **At the terminal**, with the framework's own Python (so the printed path is the right interpreter):

```bash
llm-cli mcp config
llm-cli acp config
llm-cli acp config --json
```

Copy the entry again after you move or reinstall the framework: the paths change with it. On Windows the command line
is quoted so that cmd and PowerShell both keep it; if a path contains `%`, `!`, `$`, a backtick or a double quote,
only the JSON entries are shown.

## A: the framework's MCP server in an editor's agent

In ACP the editor passes MCP servers to the agent when it opens a session (`session/new`, field `mcpServers`), and
the agent then connects to them as an MCP client. Every ACP agent must accept MCP servers over stdio: a name, the
command as an absolute path, the arguments and the environment ([ACP: session setup][acp-session]). The framework's
MCP server is such an stdio server. The agent starts it and uses the framework's tools within your permissions; you
sign in to the agent with your own account, the framework never sees it.

What the agent sees: the entry names local paths (the Python interpreter and the framework's installation folder), as
with any MCP client configuration. Tool results never carry host paths or device account names: folders reach the
model as labels.

### Zed

1. Copy the **Zed** entry (`context_servers`) from **Security > AI App Connections (MCP)...** or from
   `llm-cli mcp config`.
2. Add it to Zed's `settings.json` under `context_servers` (merge it with entries that are there already).

Zed's documentation says: "Zed can forward configured MCP servers over ACP; agents may also read native MCP config".
It says "may": whether a given external agent receives the server depends on Zed and the agent
([Zed: MCP][zed-mcp], [Zed: external agents][zed-agents]).

### JetBrains IDEs

1. Open **Settings | Tools | AI Assistant | Model Context Protocol (MCP)** and click **Add**
   ([JetBrains: MCP][jb-mcp]). The documented JSON format is `mcpServers` with `command` and `args` for a local
   server: paste the **Claude Desktop** entry (`mcpServers`) from **Security > AI App Connections (MCP)...** or
   `llm-cli mcp config`. If you use Claude Desktop already, **Write to Claude Desktop...** (or
   `llm-cli mcp config --write claude-desktop`) followed by the IDE's **Import from Claude** button does the same.
2. Let the IDE hand the servers you configured to its ACP agents ([JetBrains: ACP][jb-acp]): for agents installed
   from the registry, keep **Pass custom MCP servers** on in **Settings | Tools | AI Assistant | Agents**; for an
   agent of your own in `~/.jetbrains/acp.json`, `use_custom_mcp` under `default_mcp_settings` does it (on by
   default).

**[Unconfirmed]** The JetBrains page documents only `command` and `args` in the JSON, and a separate **Working
directory** field in the dialog. Whether the IDE keeps the entry's `env` (`PYTHONPATH`) was not confirmed. Without it
the server cannot start (Python reports that it finds no module named `orchestrator`); then check the entry's
variables in the dialog.

### Android Studio

Android Studio's own MCP settings (**File > Settings > Tools > AI > MCP Servers**, the file `mcp.json`) list as a
limitation: "Connection to MCP servers through `stdio` transport" ([Android Studio: add an MCP server][as-mcp]).
Android Studio therefore cannot hand the framework's stdio server to its agents.

What works instead is the agent's own MCP configuration: Claude Agent, for example, reads the configuration of
Claude Code, which the `claude mcp add ...` command from `llm-cli mcp config` (or the **Claude Code** entry in
**Security > AI App Connections (MCP)...**) writes. **[Unconfirmed]** Whether each agent that Android Studio runs
(Bring Your Own Agent) reads its own MCP configuration was not confirmed for Android Studio; Zed's documentation says
that agents may do so ([Zed: external agents][zed-agents]).

## B: Ditto as the editor's agent

The editor starts `llm-cli acp` and you chat with Ditto in the editor; Ditto works with the framework's own tools, the
same ones as in the chat panel. Ditto is **experimental**, in the editor too: the notice is the first message of every
editor session ([Features: Ditto in editors](Features.md#ditto-in-editors-acp)).

### Zed

Copy the **Zed** entry from **File > Settings...**, page **Ditto in editors**, or from `llm-cli acp config`, and add it
to Zed's `settings.json` under `agent_servers`. Zed takes an own agent as `"type": "custom"` with `command`, `args`
and `env`; the copied entry has exactly these fields ([Zed: external agents][zed-agents]).

### JetBrains IDEs

In the AI Chat tool window, click **More** and choose **Add Custom Agent**: the IDE creates `~/.jetbrains/acp.json`
and opens it ([JetBrains: ACP][jb-acp]). Paste the **JetBrains** entry (`agent_servers`, from the same tab or from
`llm-cli acp config --json`) into it. The agents' settings page is **Settings | Tools | AI Assistant | Agents**.

### Android Studio

Android Studio (Rabbit 2 and later) runs ACP agents ("Bring Your Own Agent"): you pick an agent in the agent window,
and "Additional agents can be found in the registry Settings > Tools > AI > Agents" ([Android Developers blog,
2026-09-24][as-byoa]). Ditto is not an agent of the ACP registry; you add it as a custom agent. Google's setup page for
its Antigravity agent says: "Android Studio uses the same `~/.jetbrains/acp.json` file as other JetBrains IDEs", and
"Custom agents are in preview" ([Antigravity: other editors (ACP)][ag-acp]). So:

1. Add Ditto's entry to `~/.jetbrains/acp.json` under `agent_servers`, as in the JetBrains steps above (the **Copy**
   button on the **Ditto in editors** tab, or `llm-cli acp config --json`).
2. Restart Android Studio and open the **Agent** tool window.
3. Pick the name you gave the entry in the agent picker at the top of the panel.

Android Studio's own pages (the blog posts and the release notes read for this guide) do not describe custom agents;
the statement above comes from Google's Antigravity documentation.

### Signing Ditto in

Ditto answers with the AI provider of your AI settings. While none is set up, the editor offers **Sign Ditto in**: it
runs the framework's own sign-in (`llm-cli acp --login`) in a terminal, which asks for the provider and then runs
`llm-cli ditto login PROVIDER` for it. You can also sign in beforehand with **Security > Sign-in Overview...** or at the terminal:

```bash
llm-cli ditto login PROVIDER
```

No key ever passes through the editor.

### What the editor can do, and what it cannot

- **Permission questions** offer only **Allow once** and **Reject**. "Do not ask again" (a standing approval) exists
  only in the framework's own approval window. A deletion, or a tool you set to "confirm in the framework", is never
  asked in the editor; a question the editor does not answer waits as a pending approval in the framework
  (**Ditto > Pending Approvals...**).
- **Folders**: the editor's project folders reach the model only as labels (`project`, `project-2`, ...), never as
  paths. The privacy filter applies to every cloud call as everywhere.
- **The editor's own MCP servers**: an editor may pass Ditto its MCP servers. They are recorded and shown in the
  **Ditto in editors** tab and by `llm-cli acp servers list`; none of them is used, because their tools would bypass
  the framework's permissions and audit log. Only you can enable an entry, in that tab or with
  `llm-cli acp servers enable SERVER`; this version connects to none either way.
- The editor's file and terminal services are never used; builds and deployments run only through the framework's
  tools. Provider and model can be chosen per session; slash commands `/status`, `/targets`, `/builds`, `/mojo`.

```bash
llm-cli acp servers list
llm-cli acp servers enable SERVER
llm-cli acp servers disable SERVER
```

## C: agents in the framework's own window

The framework is also an ACP client, like an editor: you add external agents and chat with them beside Ditto
([Features: ACP agents you pick](Features.md#acp-agents-you-pick)). Adding, signing in and starting an agent is your
action alone; an AI can never do it.

### Adding Claude Agent, Codex or Gemini CLI

- **From the ACP registry:** **Ditto > Agents...**, **Fetch the registry** (only on that click, over HTTPS), pick the
  agent and **Add the selected agent...**. Before you say yes you see its name, description, distribution, licence,
  the exact command, the download and its checksum; "No" is the default. At the terminal:

```bash
llm-cli agents search --refresh
llm-cli agents search claude
llm-cli agents add ID
llm-cli agents add ID --kind npx
```

- **Your own command:** in the same window, **Your own command** (the program, its arguments, whether it works
  without the internet, the variables of its keys), or:

```bash
llm-cli agents add --command PATH --name NAME --arg ARG --key VARIABLE
llm-cli agents add --command PATH --name NAME --local
```

- **The checksum rule:** npx and uvx agents run only with the exact version the registry names. A binary is downloaded
  only over HTTPS and only with the registry's sha256; without one it is not downloaded, and you can install it
  yourself and add it as your own command.

### Signing in

**Sign in...** in **Ditto > Agents...** (or `llm-cli agents login NAME`) uses the agent's own sign-in:

- its **browser login**: your provider's sign-in page with its own second factor (OAuth, 2FA); the framework never
  sees your password;
- its **terminal login**: the agent's login command in a console window you see (in the GUI) or in your terminal;
- **API keys**: **Store a key...** (or `llm-cli agents login NAME --key VARIABLE`) stores a key, asked hidden and
  twice, in the framework's credential vault. It goes only to that agent, as that variable, when it starts.

```bash
llm-cli agents login NAME
llm-cli agents login NAME --method METHOD
llm-cli agents login NAME --key VARIABLE
```

### Chatting, offline mode, what the agent sees

- **Chat with:** in the chat panel picks the agent; at the terminal `llm-cli agents run NAME`. The agent's
  permission questions come in the framework's own dialog: **Allow once** or **Reject**; an "always" of the agent is
  never chosen.
- **Offline mode** (**Ditto > AI Settings...**, **Offline mode**) refuses an agent that needs the internet. An own
  command added as working without the internet (`--local`) may start.
- **What the agent sees:** the framework's MCP tools, and nothing else of the framework. Its session gets exactly one
  MCP server, the framework's own over stdio; that entry names local paths (the Python interpreter, the installation
  folder, the agent's own working folder), as with any MCP client configuration. The framework offers it no file
  system and no terminal. The agent runs with a clean environment (the system basics and its own keys) in its own
  folder under the framework home.
- **Removing:** **Remove...** (or `llm-cli agents remove NAME`) lists the agent's folders and entry first ("No" is the
  default) and takes its keys from the vault.

```bash
llm-cli agents list
llm-cli agents run NAME
llm-cli agents remove NAME
```

## Troubleshooting

The messages below are the framework's own texts; `{name}` stands for the agent's name.

| Message | Where | What to do |
|---|---|---|
| "Ditto has no AI provider set up yet. Sign Ditto in first" | The editor, part B | Use the editor's **Sign Ditto in**, **Security > Sign-in Overview...**, or `llm-cli ditto login PROVIDER`. |
| "The installation path ... cannot be put on PYTHONPATH safely" | The entries, parts A and B | The installation folder is relative or contains the path separator (`:` or `;`); install the framework to another folder. |
| "The entry cannot be shown: {reason}" | **Ditto in editors** tab | The reason is the line above or another problem with the paths; the same entry from `llm-cli acp config` says the same. |
| "Offline mode is on, and {name} needs the internet" | Part C | Turn offline mode off, or use an agent you added as working without the internet. |
| "The ACP registry gives no checksum for the download of {name}" | Part C | The framework does not download it. Install the agent yourself and add it as your own command. |
| "Node.js (npx) is not installed on this computer." / "uv (uvx) is not installed on this computer." | Part C | Install Node.js or uv, or pick another distribution of the agent. |
| "The ACP registry was not fetched yet." | Part C | **Fetch the registry**, or `llm-cli agents search --refresh`. |
| "{name} starts for the first time and may download its package first" | Part C | Wait: the first start of a new npx or uvx agent may take up to 5 minutes. |
| "{name} did not start the Agent Client Protocol" | Part C | The program did not answer as an ACP agent: check its command and arguments; the end of its own output follows the message. |
| "The program of {name} is missing. Remove the agent and add it again." | Part C | Its installed program is gone: remove the agent and add it again. |
| "The agent offers no 'allow once' for this, only 'always' or reject." | Part C | The framework never chooses "always"; only Reject is possible. Give a standing approval in the framework's own window if you want one. |
| "This version connects to no editor MCP server either way." | Part B | Expected: enabling an editor's MCP server records your consent only. |
| Python: no module named `orchestrator` | Parts A and B | The editor dropped the entry's `PYTHONPATH`, or the installation moved: copy the entry again and keep its variables. |

## Sources

All accessed on 2026-11-06.

- [acp-session]: Agent Client Protocol, session setup: https://agentclientprotocol.com/protocol/v1/session-setup.md
- [zed-agents]: Zed, external agents: https://zed.dev/docs/ai/external-agents
- [zed-mcp]: Zed, Model Context Protocol: https://zed.dev/docs/ai/mcp
- [jb-acp]: JetBrains AI Assistant, Agent Client Protocol: https://www.jetbrains.com/help/ai-assistant/acp.html
- [jb-mcp]: JetBrains AI Assistant, Model Context Protocol: https://www.jetbrains.com/help/ai-assistant/mcp.html
- [as-mcp]: Android Studio, add an MCP server: https://developer.android.com/studio/gemini/add-mcp-server
- [as-byoa]: Android Developers blog, "Build your way: Use any AI agent of your choice in Android Studio" (2026-09-24):
  https://developer.android.com/blog/posts/build-your-way-use-any-ai-agent-of-your-choice-in-android-studio
- [ag-acp]: Google Antigravity, other editors (ACP): https://antigravity.google/docs/ide/extensions/acp
- The framework's research on ACP: [ACP research](recherche/ACP.md).

[acp-session]: https://agentclientprotocol.com/protocol/v1/session-setup.md
[zed-agents]: https://zed.dev/docs/ai/external-agents
[zed-mcp]: https://zed.dev/docs/ai/mcp
[jb-acp]: https://www.jetbrains.com/help/ai-assistant/acp.html
[jb-mcp]: https://www.jetbrains.com/help/ai-assistant/mcp.html
[as-mcp]: https://developer.android.com/studio/gemini/add-mcp-server
[as-byoa]: https://developer.android.com/blog/posts/build-your-way-use-any-ai-agent-of-your-choice-in-android-studio
[ag-acp]: https://antigravity.google/docs/ide/extensions/acp
