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

- A universal adapter: the bundle passes the gateway its route table (`routes.json`, made by the generator from the
  chosen blocks' `reach` sections, signed with the manifest, one `--routes` argument). Each route reaches its
  engine through its protocol: the OpenAI adapters below (and embeddings and speech output), or a generic checked
  passthrough with the limits the block declares (`http-json`, `http-multipart`, `http-sse`, `http-binary`). A new
  engine needs its block, never a new gateway.
- Strict JSON: only the known fields of `/v1/chat/completions` and `/v1/completions`, text content (and, with the
  vision component, images); anything else is refused before it reaches the model.
- Speech: `POST /v1/audio/transcriptions` (OpenAI's multipart form: `file`, `model`, `language`, `prompt`,
  `response_format` json, text, verbose_json, srt or vtt, `temperature`) goes to the bundle's whisper-server (blocks
  `stt-whisper-cpp`, `stt-whisper-cpp-program`) as its own form at `/inference`. The audio's type is read from its
  bytes: wav, mp3, flac and ogg with Vorbis (what whisper-server decodes without ffmpeg); webm, Opus and mp4 are
  refused with the reason. Without a whisper component the route answers 501.
- Images: with the vision component (block `vision-mmproj`, a llama-server of its own), a chat whose user message has
  `image_url` parts goes to it unchanged. Only `data:` URLs (png, jpeg or gif, checked by their bytes): the board
  never fetches an image from an address for a client. Each image counts `image_tokens` against the vision
  component's context.
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
  health awaited. The plan always shows whether all together fits. In turn the engines' units are installed but not
  started at boot: the gateway starts the llm at its own start and each other engine when a request needs it.
- `/health` lists the routes with the engine and port of each; `/metrics` counts requests, refusals and body bytes
  per route.
- Logs: route, status, time and token counts, the audio's type and size, the number of images; never a prompt, an
  image, audio or a transcript.

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
- `idle_stop_minutes` (0): stops an engine after these minutes without a request, through its unit (needs the
  gateway's and the engines' units, autostart); the next request starts it again.
- `max_audio_mb` (25): the largest audio upload, apart from `max_body_kb`.
- `max_images` (4), `max_image_kb` (2048), `max_images_kb` (4096): the images of one chat request (0 images: none);
  `image_tokens` (768): the tokens one image is counted with (a fixed, conservative number: the projector's own count
  depends on the model and the picture).
- `upstream_port`, `ctx_size`: set by the bundle from the llm component (its port and context size); `stt_port` from
  the whisper component, `vision_port` and `vision_ctx_size` from the vision component (0: none).
- `autostart` (no): with yes, the root actions create_user, install_unit and set_cgroup with `user` (llmcf) and
  `memory_limit_mb` (64, plus room for the audio uploads and images in flight, or derived from the gateway's
  measurement on the board); llmcf-install carries them out.
