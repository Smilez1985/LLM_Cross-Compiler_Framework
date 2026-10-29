Version 3.0.0 plus the [unreleased changes](../CHANGELOG.md#unreleased) · Last updated 2026-10-16

**English** · [Deutsch](Mojo_DE.md)

# Mojo: check and repair the installation

Mojo is the framework's health check. It used to be called the Doctor; the name comes from the rag4ll project. Mojo
has two modes:

- **Mojo Oracle** only looks. It says what it found, where, why it matters and whether Mojo Miracle can repair it.
- **Mojo Miracle** repairs what can be repaired safely. Then it says what it repaired, what it did not repair, and
  why.

Mojo never asks a model what is wrong: every check is a fixed rule with a stable code. Mojo never deletes anything
unless you pick it from a list, and it never shows a secret.

Back to the [README](../README.md) · All commands: [CLI_Usage.md](CLI_Usage.md#mojo)

## Contents

1. [Run Mojo](#1-run-mojo)
2. [What Mojo Oracle reports](#2-what-mojo-oracle-reports)
3. [What Mojo Miracle repairs](#3-what-mojo-miracle-repairs)
4. [What Mojo Miracle does not repair, and why](#4-what-mojo-miracle-does-not-repair-and-why)
5. [Without a terminal](#5-without-a-terminal)
6. [For AI clients](#6-for-ai-clients)
7. [The old names keep working](#7-the-old-names-keep-working)
8. [Exit codes and JSON](#8-exit-codes-and-json)

## 1. Run Mojo

```bash
llmcf mojo              # Mojo Oracle (the default): look only
llmcf mojo --oracle     # the same
llmcf mojo --miracle    # repair what can be repaired, then report
llmcf mojo --json       # machine-readable; never asks and never deletes
llmcf mojo --oracle --board "Kitchen board"   # also check this one board now
```

`llmcf` is the framework's command line under its new name. `llm-cli` is the same program and keeps working, so
`llm-cli mojo` does exactly the same. The development setup and the framework container have both commands. With
the installers you call the command line as [Calling the CLI](CLI_Usage.md#calling-the-cli) shows. In the container:
`make mojo`, or `docker exec -it lcf-framework llmcf mojo`.

When Mojo Oracle finds a problem that Mojo Miracle can act on, it asks at a terminal:

```
Run Mojo Miracle now? [y/N]:
```

With `y` Mojo Miracle runs right away; you do not type the command again. No is the default: then nothing is
changed.

In the GUI (unreleased): **Tools > Mojo Health Check...** runs Mojo Oracle when the window opens and shows the same
findings with an overall line for the exit code; **Run Mojo Miracle...** asks the same question (No is the default),
opens the framework's build images in the deletion dialog and shows what was repaired, what was not and why. The
window and the command call the same functions.

## 2. What Mojo Oracle reports

Mojo Oracle prints one line per check. For every warning and error it also prints:

- **What** it found.
- **Where**: the full path or the object. Only you see full paths; an AI reads a label such as "the state folder".
- **Why it matters.**
- **The hint**: what helps.
- **Mojo Miracle**: whether it can repair it, or why not. For a repair it shows the plan: what changes, the full
  path, before and after, and whether it can be undone.

An information line (ℹ) shows its hint too. At the end one sentence counts every problem by what can be done about
it, for example: "Mojo Oracle found 3 problems (1 error, 2 warnings): Mojo Miracle can repair 1; 1 needs your list of
what to delete; 1 needs you." Nothing is skipped. Mojo Oracle changes nothing.

It checks Python, Docker and buildx (and names the container engine: under Podman, the fallback on Rocky Linux,
AlmaLinux and Oracle Linux, `docker.engine` lists what Podman cannot do and the way to Docker CE; see
[Features](Features.md#podman-instead-of-docker)), where Docker keeps its images, free disk space for the cache,
output and build folders, the targets, the state folder, the secrets store and the keyring, the tool permissions, the MCP tokens and
the audit log, the chat feed, the calibration data, the community packs, the knowledge snapshots against the free
space of their disk, the storage of the knowledge base, the local llama.cpp runtime (in Docker mode: whether Ditto's
image is there, built on this computer and verified) and the boards you trusted.

### The storage of the knowledge base

The knowledge base is stored in Qdrant Edge; one in the storage of earlier versions is converted on its first use.
Mojo reads the record of that conversion and opens nothing (`knowledge.storage`):

- a conversion that failed is a warning: the knowledge base still uses the old storage and keeps working. The line
  gives the reason and how to try again: **Tools > Knowledge Snapshots... > Try the conversion again** in the GUI, or
  `llm-cli knowledge storage convert` in a terminal;
- a knowledge base in Qdrant Edge whose engine cannot be loaded (a missing or broken package) is an error: it is not
  opened, and the old folder never stands in for it. Install the framework's requirements again;
- the old storage folder kept after a conversion is information, with its full path and size. Nothing deletes it by
  itself, Mojo Miracle neither: **Tools > Knowledge Snapshots... > Delete the old storage...** in the GUI or
  `llm-cli knowledge storage delete-old` list it and ask you first;
- on a computer without a build of Qdrant Edge (Windows on ARM) an information says that the knowledge base keeps the
  older storage.

### The boards you trusted

Mojo makes no network contact with a board unless you ask it to. A check only makes sense where there is a problem to
find, so Mojo reports what the framework recorded, from files only:

- a host key in the framework's `known_hosts` that changed (`device_host_key_changed`, a warning) or that is gone (a
  warning: trust the board again);
- the last contact with a board that failed: a deploy, a runtime deploy, a verify, a benchmark or a probe that could
  not reach the board or log in, with its reason code and the next step: `device_host_down` (information: nothing
  answered, the board may have been switched off), `device_port_closed`, `device_auth_failed` or
  `device_host_key_changed` (warnings), and when it happened;
- what the last deploy saw on the board (an old Qdrant sidecar).

Every contact of the framework with a board records what it found; a later contact that reaches the board and logs
in ends the record, and so does trusting the board again. Boards without a recorded problem get one line that says
so.

For a board a recorded fact points to, Mojo offers the check. At a terminal it asks:

```
Check Kitchen board now? (one connection to its SSH port, no login) [y/N]:
```

With `y` Mojo opens one connection to the board's SSH port and prints the result. No is the default: then nothing
is contacted, and Mojo prints the command for later. Without a terminal it prints only the command.

`llmcf mojo --oracle --board NAME` checks one board right away. NAME is the board's label, its device ID or its
address. Mojo never logs in, sends no password and names no device account. A wrong password or a framework key that
is gone (`device_auth_failed`) shows only at the next deploy or benchmark; when such a failure is recorded and the
board answers now, Mojo says that it cannot check the login. A checked board keeps its recorded problem until a
deploy, verify or benchmark reaches it again, because Mojo Oracle only looks and writes nothing.

In the GUI the Mojo window contacts no board by itself either: a board a recorded fact points to gets a **Check
<board> now** button, and **Check a board...** checks any trusted board you pick by its label and address (two
boards that read the same, such as a board set up again with a new host key, also show their device ID). The
Deployment tab's **Check this board** (group "Benchmark on a board") checks the board chosen there. Each is the same
single connection to the board's SSH port, without a login, and shows the board's finding with its next step.

## 3. What Mojo Miracle repairs

Mojo Miracle makes only harmless repairs. A harmless repair narrows access or switches to an intact build, and it
can be undone. Today there are two:

| Problem | What Mojo Miracle does |
|---|---|
| `state.permissions`: other users of this computer can read your state folder (tool permissions, audit log, chat) | The folder loses the rights of other users, for example mode 0755 becomes 0700. Your own rights stay. |
| `runtime.build.missing`: the active llama.cpp build of Ditto's server is missing | Ditto's server switches to the newest intact, verified build of the pinned llama.cpp version, if one is stored. Nothing is built or deleted. |

How a repair runs:

1. Every repair is a plan first (a dry run). Mojo Oracle shows it.
2. Right before the change Mojo checks again. If the problem is gone, nothing is changed ("already gone"). If the
   plan is not the one Mojo Oracle showed you, nothing is changed ("the problem changed").
3. The folder is opened without following a link, and its mode is changed only while it is still the mode of the
   plan.
4. The audit log records the repair before it happens. If it cannot, nothing is changed.
5. Afterwards Mojo checks again and says honestly whether the problem is gone ("Checked again: the problem is
   gone") or still there.

Only one Mojo Miracle runs at a time; a second one changes nothing and says so. If the check after the repairs finds
a problem that was not there before, Mojo Miracle lists it as new.

## 4. What Mojo Miracle does not repair, and why

Every problem that Mojo Miracle does not repair gets one of these reasons:

| Reason | What it means | Examples |
|---|---|---|
| deleting needs your list | Freeing space means deleting, and the framework deletes only what you tick and confirm in its own window. At a terminal Mojo Miracle shows you the framework's build images (name, id, size, what each is; nothing is ticked). You type the numbers of the ones to delete and confirm exactly those; No is the default. The GUI has the same list under **Tools > Framework Build Images...**. Docker images are never deleted unless you do it, and Docker's build cache is never touched. For knowledge snapshots the list is **Tools > Knowledge Snapshots...** or `llm-cli knowledge snapshots`. | `disk.cache`, `disk.output`, `disk.builds`, `knowledge.snapshots` |
| needs you | Only you can do this: your keys, the tool permissions, the audit log, your settings and files. An AI never changes them. | `secrets.store`, `secrets.storage`, `tools.permissions`, `tools.tokens`, `tools.audit`, `chat_feed.store`, `targets.none`, `devices.<device id>` (a board that refuses SSH or whose host key changed), `knowledge.storage` (a failed conversion of the knowledge base, or Qdrant Edge that cannot be loaded), a revoked community pack (you delete its folder yourself) |
| needs an administrator | It changes the system outside the framework. | `docker.daemon`, `docker.buildx`, `docker.engine`, `python.version`, `secrets.keyring`, `runtime.docker.unavailable` |
| not repairable automatically | Nothing in the framework can do it by itself. The hint says what helps. | `runtime.build.damaged`, `runtime.build.outdated`, `runtime.docker.image` (make Ditto's image again with `llm-cli ditto runtime ensure --mode docker --force`), `data.calibration`, a check that crashed |

A repair can also end like this, always with its reason:

- **Still there**: the change was made, but the check still finds the problem.
- **Already gone**: the problem was gone before Mojo Miracle changed anything.
- **The problem changed**: nothing was changed; `llmcf mojo` shows the new plan.
- **Failed**: for example the audit log could not record it, or the folder changed meanwhile.
- **Not deleted**: you chose nothing from the list.

## 5. Without a terminal

In a pipe, a script or an AI app's shell nobody can answer a question. Then:

- `llmcf mojo` reports and says how many problems `llmcf mojo --miracle` can act on.
- `llmcf mojo --miracle` makes only the harmless repairs. It prints each plan and records it in the audit log.
  Everything else is reported as not repaired, with the reason.
- Nothing is ever deleted.
- `--json` never asks and never deletes. With `--miracle` it makes only the harmless repairs too.

## 6. For AI clients

An AI uses Mojo through the MCP server (and Ditto through the same tools):

- **`run_mojo_oracle`** (read-only): every finding with `where` (a label, never a host path), `why` and `repair`.
  `repair.how` is one of `miracle`, `deletion_list`, `person`, `admin`, `not_automatic`; `repair.summary` says what
  Mojo Miracle would do or why not; `repair.next` says what the AI does next. It contacts no board. For a board a
  recorded fact points to, `check_board` names the request that checks it. `{"board": "Kitchen board"}` checks that
  one trusted board now: one connection to its SSH port, no login. An AI names a board by its label or device ID
  (`list_devices` shows both), never by an address. A board you trusted without a name of its own has its address as
  label; an AI names it by its device ID, and `check_board` does so too.
- **`run_mojo_miracle`** with `{"code": "state.permissions"}`: repairs one problem whose `repair.how` is `miracle`.
  Every call asks you first; the question shows the plan (the full path only in the framework's own window). There
  is no "do not ask again" and no delegation for it. It never deletes. Any other problem is refused before the
  question, with the reason. The result is `repaired`, `still_present`, `already_fixed`, `plan_changed`, `failed`
  or `refused`, with the reason.
- The prompt **`check_installation`** tells an AI how to explain the findings and to say "repaired" only when the
  result says so.

A delegation for harmless repairs comes later, together with the delegations.

## 7. The old names keep working

- `llm-cli doctor` keeps its options, its output and its exit codes. It prints a short note on stderr that names
  `llmcf mojo`. `doctor --fix` makes the harmless repairs (the same audited repairs as Mojo Miracle); a problem
  still present after it is shown with the reason. `doctor --fix --tier 2` also shows the list of the framework's
  build images, as before.
- The tool `run_doctor` stays callable but is no longer listed. A setting for `run_doctor` also applies to
  `run_mojo_oracle`, and the other way round; the stricter one wins.
- In Python, `orchestrator.Core.doctor` re-exports everything from `orchestrator.Core.mojo`.
- `make doctor` stays next to `make mojo`.

## 8. Exit codes and JSON

The exit code is 0 (no problem), 1 (warnings) or 2 (errors). After Mojo Miracle it is the result of the check
afterwards.

`llmcf mojo --json` writes the keys of `llm-cli doctor --json` (`exit_code`, `findings`, `fixed`, `still_present`)
plus `mode` and `summary`. Each finding also has `where`, `why`, `repair_id` and `cannot`, and each problem has
`miracle` (`can`, `summary` and the `plan` steps). With `--miracle` it adds `repaired`, `already_gone`,
`not_repaired` (each with its reason) and `new_problems`, and `findings` are those of the check afterwards. A
finding of a board a recorded fact points to has `check_board` (the board, its device ID and the command that checks
it); `--json` never contacts a board unless `--board` names one. Then
`fixed` and `still_present` mean what they mean for `llm-cli doctor --fix --json`: the repairs Mojo Miracle made, and
those it tried that are still there.
