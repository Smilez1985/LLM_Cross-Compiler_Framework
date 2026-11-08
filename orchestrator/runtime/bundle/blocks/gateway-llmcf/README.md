# Gateway (llmcf-gateway)

Block `gateway-llmcf` (kind gateway) of the board bundle: the framework's own Rust gateway (`rust/llmcf-gateway`),
an OpenAI-compatible front on the board in front of the bundle's llama-server. The framework builds the program for
the board's architecture (a static musl binary, kept in its cache and used offline afterwards) and signs it into the
bundle; you pick no file.

## Models

None of its own: the gateway talks to the bundle's llm component (block `llm-llama-server`, one GGUF model) on
127.0.0.1 with that component's key file. A bundle with the gateway needs the llm component. Memory: a few MB
resident, in its own small unit with autostart (its memory limit 64 MB while the gateway was not measured on the
board; from its measured peak plus the queue and a margin once it was); llama-server keeps its own unit and limit.
The gateway's unit has OOMScoreAdjust -500: the kernel takes a model's server first, never the gateway before it.
Licence: the gateway is the framework's (MIT); the model's licence is named in the llm component.

## What it does

- Strict JSON: only the known fields of `/v1/chat/completions` and `/v1/completions`, text content only; anything
  else is refused before it reaches the model.
- Saves tokens: `cache_prompt` on every request (llama-server reuses the cached prompt prefix), a repeated system
  prompt is sent once, a tool result byte for byte the same as an earlier one of the same tool and arguments is sent
  as "same result as tool call #<id>", and `max_tokens` is cut to what the board's context leaves.
- Limits: the largest request body, a rate limit per client, requests in flight; `/health` and `/metrics`.
- A queue instead of a refusal: a request that finds every place in flight taken waits, in a fair order per client
  (round robin over the clients: a client's own bearer key, else the board account the kernel names for its
  connection, else its address), at most `queue_wait_seconds` (then 503 with Retry-After) and at most `queue_max` of
  them (then 429 with Retry-After); a client that hangs up while it waits frees its place; a streamed answer holds
  its place until its end.
- A memory guard: while the board's free memory (MemAvailable) is below `memory_floor_mb` or its memory pressure
  (`/proc/pressure/memory`, "some avg10") is above `memory_psi_max`, new requests wait in the queue instead of going
  to the model, until memory recovers or their wait ends; the change is logged with the numbers only.
- The engines: with its own unit (`autostart`) and the engines' units, the gateway starts and stops the bundle's llm,
  speech and vision units (the root action allow_unit_control: a polkit rule for exactly its account and these units,
  or a sudoers file with exactly their `systemctl start|stop|restart` lines on a board without polkit's rules). The
  bundle passes each engine with its memory from the plan (`--engine llm=llmcf-llm.service:MB`) and the person's
  choice from the plan: all together (each started on demand), or in turn (`llm-cli bundle plan --engines in-turn`,
  the Board bundle window's checkbox) when the board's memory cannot hold them together: before a request goes to an
  engine that is not running, the others finish their requests, are stopped, and the engine is started and its
  health awaited. The plan always shows whether all together fits. Only the llm's requests pass the gateway so far.
- Logs: route, status, time and token counts, never prompt text.

## Variables

- `port` (8088): the gateway's port.
- `bind` (loopback): `loopback` listens on 127.0.0.1 only; `lan` listens on the board's network with TLS 1.3 only
  (never plain HTTP): the board makes its own certificate on first start, and the framework pins its SHA-256
  fingerprint at the first check over the board's SSH session; a client on the network sends the gateway's bearer
  token (kept on the board, 0600).
- `max_body_kb` (256), `max_tokens` (512), `rate_per_minute` (60), `max_inflight` (2): the limits.
- `queue_max` (32), `queue_wait_seconds` (120): the queue's length and the longest wait of one request.
- `memory_floor_mb` (64; set by the bundle to half the board's measured reserve), `memory_psi_max` (50): the memory
  guard (0 turns either check off).
- `upstream_port`, `ctx_size`: set by the bundle from the llm component (its port and context size).
- `autostart` (no): with yes, the root actions create_user, install_unit and set_cgroup with `user` (llmcf) and
  `memory_limit_mb` (64, or derived from the gateway's measurement on the board); llmcf-install carries them out.
