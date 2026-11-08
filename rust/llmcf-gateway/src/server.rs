//! The HTTP front: OpenAI-compatible routes in front of llama-server on loopback, plus /health and /metrics.
//!
//! Loopback mode listens on 127.0.0.1 with plain HTTP; the local-network mode listens on every address with TLS 1.3
//! only (never plain HTTP off loopback), and a client that is not on the board itself must send the gateway's
//! bearer token. Every request is size-capped, rate-limited per client address and counted against a small number
//! of requests in flight; a request that finds them all taken waits in a fair queue (queue.rs) with a time limit and
//! a capped length. Each route names the engine it needs (engines.rs): with engines given, the request takes its
//! place in flight first and then its engine, started (and in turn the others stopped) by the scheduler. The log line
//! of a request holds its route, status, time and token counts, never a prompt, an image, audio or a transcript.
//!
//! The routes to the engines are data (routes.rs): the bundle's route table (`--routes FILE`, made from the chosen
//! blocks' reach) or, without it, the table the gateway always served. A route's protocol picks its adapter: the chat
//! and completion routes (a chat request with image parts to the engine of the images route, rules.rs and images.rs),
//! the speech route (audio.rs, whisper-server's own form), embeddings and speech output (rules.rs), and the generic
//! checked passthrough (generic.rs: http-json, http-multipart, http-sse, http-binary). Each route has its own body
//! cap (for chat with vision the images on top) and timeout; a Content-Length above the cap is refused before the
//! request waits. Every route goes through the same fair queue, memory guard and engine scheduler, and is counted on
//! its own in /metrics.

use crate::args::{Bind, Serve};
use crate::audio;
use crate::control;
use crate::engines::{self, Fut, Hooks, Scheduler, Spec, Ticket, Timing, LLM, STT, VISION};
use crate::generic;
use crate::images::ImageRules;
use crate::limits::RateLimit;
use crate::memory::{self, Guard};
use crate::peer;
use crate::queue::{self, Permit, Queue, Refused};
use crate::routes::{Protocol, Table};
use crate::rules::{self, Limits, Refusal};
use crate::tls;
use bytes::{Buf, BufMut, Bytes};
use http_body_util::{combinators::UnsyncBoxBody, BodyExt, Full, Limited};
use hyper::body::{Frame, Incoming, SizeHint};
use hyper::header::{HeaderValue, AUTHORIZATION, CONTENT_LENGTH, CONTENT_TYPE, HOST, RETRY_AFTER};
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};
use std::time::{Duration, Instant};
use tokio::io::Interest;
use tokio::net::{TcpListener, TcpStream};

pub type BoxError = Box<dyn std::error::Error + Send + Sync>;
pub type Body = UnsyncBoxBody<Bytes, BoxError>;

/// The runtime key of llama-server below the home (launcher.KEY_REL).
pub const KEY_REL: &str = ".config/llm-framework/llama-server.key";
const ANSWER_WAIT: Duration = Duration::from_secs(900);
/// How long a request's body may take once its in-flight permit is taken: a client that sends its headers and then
/// stalls frees the permit after this time (408), so a few slow clients cannot hold the model away from the others.
pub const BODY_WAIT: Duration = Duration::from_secs(30);
/// The same for an audio upload (up to `--max-audio-mb`, which takes longer over a slow link).
pub const AUDIO_BODY_WAIT: Duration = Duration::from_secs(120);

/// One route's counts (the route table's), without any content.
#[derive(Default)]
pub struct RouteMetrics {
    pub requests: AtomicU64,
    /// answered by the gateway itself with an error (its checks, caps, limits, the queue, an engine not ready)
    pub refused: AtomicU64,
    pub bytes_in: AtomicU64,
}

#[derive(Default)]
pub struct Metrics {
    /// requests the engines answered through a generic route (http-json, -multipart, -sse, -binary)
    pub generic: AtomicU64,
    pub requests: AtomicU64,
    pub ok: AtomicU64,
    pub refused: AtomicU64,
    pub too_large: AtomicU64,
    pub rate_limited: AtomicU64,
    pub unauthorized: AtomicU64,
    pub upstream_errors: AtomicU64,
    pub prompt_tokens: AtomicU64,
    pub system_dropped: AtomicU64,
    pub results_pointed: AtomicU64,
    pub clamped: AtomicU64,
    pub images: AtomicU64,
    /// one entry per route of the table, in its order
    pub routes: Vec<RouteMetrics>,
}

pub struct State {
    pub cfg: Serve,
    pub key: Option<String>,
    pub token: Option<String>,
    pub fingerprint: Option<String>,
    pub rate: Mutex<RateLimit>,
    pub queue: Arc<Queue>,
    pub memory: Arc<Guard>,
    pub metrics: Metrics,
    pub budget: AtomicU64,
    /// the engines the gateway starts and stops (None: it controls none)
    pub engines: Option<Arc<Scheduler>>,
    /// the board the scheduler works on instead of systemctl (tests only: a fake board; None: systemctl)
    pub board: Option<Arc<dyn Hooks>>,
    /// the route table (routes.rs)
    pub routes: Table,
    /// the engine on --upstream-port (the language model's llama-server: its /health and /props set the budget)
    pub primary: Option<String>,
}

impl State {
    fn limits(&self, path: &str) -> Limits {
        let c = &self.cfg;
        let route = self.routes.images_route(path).map(|i| &self.routes.routes[i]);
        let vision = route.and_then(|r| self.routes.engine(&r.engine)).filter(|e| e.port.is_some());
        let images = (vision.is_some() && c.max_images > 0).then_some(ImageRules {
            max_images: c.max_images,
            max_image_bytes: c.max_image_kb as u64 * 1024,
            max_total_bytes: c.max_images_kb as u64 * 1024,
            tokens: c.image_tokens as u64,
        });
        Limits {
            budget: self.budget.load(Relaxed),
            max_tokens: c.max_tokens as u64,
            slots: c.slots,
            images,
            vision_budget: vision.and_then(|e| e.ctx_size).unwrap_or(c.vision_ctx_size) as u64,
        }
    }

    /// The loopback port of an engine (None: the board has none the gateway speaks to).
    fn port_of(&self, id: &str) -> Option<u16> {
        self.routes.engine(id).and_then(|e| e.port)
    }

    fn is_primary(&self, id: &str) -> bool {
        self.primary.as_deref() == Some(id)
    }

    /// The body cap of a route: its own, and for a chat route with an images route as many images as a request may
    /// carry in base64 (4 characters for 3 bytes) with their data-URL heads.
    fn body_cap(&self, idx: usize) -> usize {
        let r = &self.routes.routes[idx];
        if r.protocol != Protocol::Chat || self.limits(&r.path).images.is_none() {
            return r.max_body;
        }
        let c = &self.cfg;
        r.max_body + (c.max_images_kb as usize * 1024).div_ceil(3) * 4 + c.max_images as usize * 64
    }
}

/// What a request's log line and its route's counts say (never its content).
#[derive(Default)]
pub struct Trace {
    pub note: String,
    pub route: Option<usize>,
    /// the engine answered (its status, whatever it is, is not the gateway's refusal)
    pub engine_answered: bool,
    pub bytes_in: u64,
}

/// A request body of ready chunks: a JSON text, or whisper's form around a slice of the client's audio (no copy).
pub struct Chunks {
    parts: VecDeque<Bytes>,
    left: u64,
}

impl Chunks {
    pub fn new(parts: Vec<Bytes>) -> Chunks {
        let left = parts.iter().map(|b| b.len() as u64).sum();
        Chunks { parts: parts.into(), left }
    }
}

impl hyper::body::Body for Chunks {
    type Data = Bytes;
    type Error = std::convert::Infallible;

    fn poll_frame(mut self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<Option<Result<Frame<Bytes>, Self::Error>>> {
        match self.parts.pop_front() {
            Some(b) => {
                self.left -= b.len() as u64;
                Poll::Ready(Some(Ok(Frame::data(b))))
            }
            None => Poll::Ready(None),
        }
    }

    fn is_end_stream(&self) -> bool {
        self.parts.is_empty()
    }

    fn size_hint(&self) -> SizeHint {
        SizeHint::with_exact(self.left)
    }
}

fn full(data: impl Into<Bytes>) -> Body {
    Full::new(data.into()).map_err(|never| match never {}).boxed_unsync()
}

fn json_response(status: StatusCode, v: &Value) -> Response<Body> {
    let mut r = Response::new(full(v.to_string()));
    *r.status_mut() = status;
    r.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
    r
}

fn error(status: StatusCode, code: &str, message: &str) -> Response<Body> {
    json_response(status, &json!({"error": {"code": code, "message": message, "type": "invalid_request_error"}}))
}

fn retry(mut r: Response<Body>, seconds: u64) -> Response<Body> {
    if let Ok(v) = HeaderValue::from_str(&seconds.to_string()) {
        r.headers_mut().insert(RETRY_AFTER, v);
    }
    r
}

/// Why a request body was not read.
#[derive(Debug)]
pub enum BodyFail {
    /// larger than the gateway takes
    TooLarge,
    /// not all there within its wait
    Slow,
    /// the client's connection broke off
    Broken,
}

/// The request body, at most `cap` bytes, read within `wait` into one buffer of the announced size (a large upload is
/// held once, never as its frames and a copy).
pub async fn read_body<B>(body: B, cap: usize, wait: Duration) -> Result<Bytes, BodyFail>
where
    B: hyper::body::Body,
{
    let hint = body.size_hint();
    if hint.lower() > cap as u64 {
        return Err(BodyFail::TooLarge);
    }
    let read = async move {
        let mut body = std::pin::pin!(body);
        let mut buf: Vec<u8> = Vec::with_capacity(hint.exact().unwrap_or(0) as usize);
        while let Some(frame) = body.frame().await {
            let frame = frame.map_err(|_| BodyFail::Broken)?;
            if let Ok(data) = frame.into_data() {
                if buf.len() + data.remaining() > cap {
                    return Err(BodyFail::TooLarge);
                }
                buf.put(data);
            }
        }
        Ok(Bytes::from(buf))
    };
    tokio::time::timeout(wait, read).await.unwrap_or(Err(BodyFail::Slow))
}

fn equal(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

// --- llama-server on loopback --------------------------------------------------------------------------------

async fn upstream(port: u16, req: Request<Chunks>) -> Result<Response<Incoming>, String> {
    upstream_within(port, req, ANSWER_WAIT).await
}

/// A request to an engine on loopback whose answer's head must come within `wait`.
async fn upstream_within(port: u16, req: Request<Chunks>, wait: Duration) -> Result<Response<Incoming>, String> {
    let stream = TcpStream::connect(("127.0.0.1", port)).await.map_err(|_| "down".to_string())?;
    let (mut sender, conn) =
        hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|e| e.to_string())?;
    tokio::spawn(async move {
        let _ = conn.await;
    });
    tokio::time::timeout(wait, sender.send_request(req))
        .await
        .map_err(|_| "the engine did not answer in time".to_string())?
        .map_err(|e| e.to_string())
}

/// An answer body that ends with an error when its engine is silent for longer than `wait` between two pieces (the
/// route's timeout): a stalled engine frees its place in flight instead of holding it.
struct Idle {
    inner: Body,
    wait: Duration,
    sleep: Pin<Box<tokio::time::Sleep>>,
}

impl Idle {
    fn new(inner: Body, wait: Duration) -> Idle {
        Idle { inner, wait, sleep: Box::pin(tokio::time::sleep(wait)) }
    }
}

impl hyper::body::Body for Idle {
    type Data = Bytes;
    type Error = BoxError;

    fn poll_frame(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Result<Frame<Bytes>, BoxError>>> {
        let this = &mut *self;
        match Pin::new(&mut this.inner).poll_frame(cx) {
            Poll::Ready(Some(Ok(frame))) => {
                let next = tokio::time::Instant::now() + this.wait;
                this.sleep.as_mut().reset(next);
                Poll::Ready(Some(Ok(frame)))
            }
            Poll::Ready(other) => Poll::Ready(other),
            Poll::Pending => match std::future::Future::poll(this.sleep.as_mut(), cx) {
                Poll::Ready(()) => {
                    Poll::Ready(Some(Err("the engine was silent longer than the route's timeout".into())))
                }
                Poll::Pending => Poll::Pending,
            },
        }
    }

    fn is_end_stream(&self) -> bool {
        self.inner.is_end_stream()
    }

    fn size_hint(&self) -> SizeHint {
        self.inner.size_hint()
    }
}

/// A request to an engine on loopback, with the runtime's key when the engine's block makes one (llama-server for the
/// llm and vision), without one otherwise (whisper-server and the other engines never see it).
fn upstream_request(
    state: &State,
    engine: &str,
    method: Method,
    path: &str,
    body: Chunks,
    ctype: &str,
) -> Request<Chunks> {
    let port = state.port_of(engine).unwrap_or(0);
    let mut b = Request::builder()
        .method(method)
        .uri(path)
        .header(HOST, format!("127.0.0.1:{port}"))
        .header(CONTENT_TYPE, ctype);
    let keyed = state.routes.engine(engine).is_some_and(|e| e.key);
    if let (Some(key), true) = (&state.key, keyed) {
        b = b.header(AUTHORIZATION, format!("Bearer {key}"));
    }
    b.body(body).unwrap_or_else(|_| Request::new(Chunks::new(Vec::new())))
}

async fn upstream_json(state: &State, path: &str) -> Option<Value> {
    let req = upstream_request(state, LLM, Method::GET, path, Chunks::new(Vec::new()), "application/json");
    let resp = tokio::time::timeout(Duration::from_secs(3), upstream(state.cfg.upstream_port, req)).await.ok()?.ok()?;
    if !resp.status().is_success() {
        return None;
    }
    let body = Limited::new(resp.into_body(), 1 << 20).collect().await.ok()?.to_bytes();
    serde_json::from_slice(&body).ok()
}

/// Whether llama-server answers its /health; on the way, the budget from its /props (n_ctx per slot).
pub async fn upstream_up(state: &State) -> bool {
    if upstream_json(state, "/health").await.is_none() {
        return false;
    }
    let own = (state.cfg.ctx_size / state.cfg.slots) as u64;
    let theirs = upstream_json(state, "/props")
        .await
        .and_then(|p| p.pointer("/default_generation_settings/n_ctx").and_then(Value::as_u64))
        .filter(|n| *n > 0);
    state.budget.store(theirs.map(|n| n.min(own)).unwrap_or(own), Relaxed);
    true
}

async fn run_systemctl(verb: &'static str, unit: String) -> Result<bool, String> {
    tokio::task::spawn_blocking(move || control::systemctl(verb, &unit)).await.map_err(|e| e.to_string())?
}

/// The board as the scheduler sees it: systemctl for exactly the engines' units, and each engine's health (the llm:
/// llama-server's /health and /props, which also set the context budget; another engine with a port: any HTTP answer
/// there; without a port: its unit is active).
impl Hooks for State {
    fn control<'a>(&'a self, verb: &'static str, unit: &'a str) -> Fut<'a, Result<bool, String>> {
        if let Some(board) = &self.board {
            return board.control(verb, unit);
        }
        Box::pin(run_systemctl(verb, unit.to_string()))
    }

    fn up<'a>(&'a self, spec: &'a Spec) -> Fut<'a, bool> {
        Box::pin(async move {
            let primary = self.is_primary(&spec.id);
            if let Some(board) = &self.board {
                if !primary {
                    return board.up(spec).await;
                }
            }
            match (primary, spec.port.or(self.port_of(&spec.id))) {
                (true, _) => upstream_up(self).await,
                (false, Some(port)) => engines::http_up(port).await,
                (false, None) => matches!(run_systemctl("is-active", spec.unit.clone()).await, Ok(true)),
            }
        })
    }
}

/// The engine `id` ready for a request: an engine the gateway does not control runs by itself (the llm must answer
/// its /health; the others are asked by the request itself); one it controls gets the scheduler's ticket (the engine
/// started, in turn the others stopped first) and, for the llm, its answer to /health. Err with the person's words
/// (the caller answers 503 with Retry-After).
async fn engine_ready(state: &State, id: &str, note: &mut String) -> Result<Option<Ticket>, String> {
    let Some(sched) = state.engines.as_ref().filter(|s| s.index(id).is_some()) else {
        return if !state.is_primary(id) || upstream_up(state).await {
            Ok(None)
        } else {
            Err("the model server does not answer".into())
        };
    };
    let (ticket, waited) = sched.enter(state, id).await.inspect_err(|_| {
        note.push_str(&format!(" engine={id}:not-ready"));
    })?;
    if waited > Duration::ZERO {
        note.push_str(&format!(" engine={id}:{}ms", waited.as_millis()));
    }
    if state.is_primary(id) && !upstream_up(state).await {
        sched.mark_down(id);
        return Err("the model server does not answer".into());
    }
    Ok(Some(ticket))
}

/// Stops each engine after `idle_stop_minutes` without a request (only the gateway's own engines; never one with a
/// request in flight, nothing while requests wait in the queue).
pub async fn idle_stopper(state: Arc<State>) {
    let (Some(sched), minutes) = (state.engines.clone(), state.cfg.idle_stop_minutes) else { return };
    if minutes == 0 {
        return;
    }
    loop {
        tokio::time::sleep(Duration::from_secs(30)).await;
        if state.queue.queued() > 0 {
            continue;
        }
        for id in sched.stop_idle(&*state, Duration::from_secs(minutes as u64 * 60)).await {
            eprintln!("llmcf-gateway: the {id} engine was stopped after {minutes} idle minutes");
        }
    }
}

// --- the routes --------------------------------------------------------------------------------------------

fn engine_facts(state: &State) -> Value {
    let Some(sched) = state.engines.as_ref() else { return json!({"mode": "none", "list": []}) };
    let list: Vec<Value> = sched
        .states()
        .into_iter()
        .map(|e| json!({"name": e.id, "unit": e.unit, "mib": e.mib, "state": e.state, "inflight": e.inflight}))
        .collect();
    json!({"mode": sched.mode.word(), "budget_mib": sched.budget_mib, "list": list})
}

async fn health(state: &State) -> Response<Body> {
    let up = upstream_up(state).await;
    let primary = state.primary.as_deref().unwrap_or(LLM);
    let stopped = state.engines.as_ref().is_some_and(|s| s.index(primary).is_some() && !s.running(primary));
    let upstream = if up {
        "up"
    } else if stopped {
        "stopped"
    } else {
        "down"
    };
    json_response(
        StatusCode::OK,
        &json!({
            "status": if up || stopped { "ok" } else { "degraded" },
            "gateway": env!("CARGO_PKG_VERSION"),
            "upstream": upstream,
            "bind": state.cfg.bind.word(),
            "tls": state.cfg.bind == Bind::Lan,
            "ctx_budget": state.budget.load(Relaxed),
            "max_tokens": state.cfg.max_tokens,
            "control": if state.engines.is_some() { "systemd" } else { "none" },
            "engines": engine_facts(state),
            "routes": route_facts(state),
            "memory": memory_facts(&state.memory),
            "queue": {
                "running": state.queue.running(),
                "max_inflight": state.cfg.max_inflight,
                "queued": state.queue.queued(),
                "max": state.queue.cap(),
                "wait_seconds": state.queue.wait().as_secs(),
            },
        }),
    )
}

/// The routing table as the board has it: each route, its protocol, its engine and that engine's loopback port (null:
/// the board runs no such engine the gateway speaks to; the route then answers 501), and where a chat with images
/// goes.
fn route_facts(state: &State) -> Value {
    let list: Vec<Value> = state
        .routes
        .routes
        .iter()
        .map(|r| {
            let mut v = json!({"method": r.method, "path": r.path, "engine": r.engine, "to": r.upstream,
                               "port": state.port_of(&r.engine), "protocol": r.protocol.word(),
                               "name": format!("{}.{}", r.engine, r.key), "max_body_kb": r.max_body / 1024,
                               "timeout_s": r.timeout.as_secs(), "stream": r.stream});
            if r.images {
                v["match"] = json!("images");
            } else if r.protocol == Protocol::Chat {
                let images = state.limits(&r.path).images.is_some();
                let to = state.routes.images_route(&r.path).map(|i| state.routes.routes[i].engine.clone());
                v["with_images"] = if images { json!(to) } else { Value::Null };
                v["vision_port"] = json!(to.and_then(|e| state.port_of(&e)));
            }
            v
        })
        .collect();
    Value::Array(list)
}

fn memory_facts(g: &Guard) -> Value {
    let r = g.reading();
    json!({
        "available_mib": r.available_mib,
        "psi_some_avg10": r.psi_some_avg10,
        "floor_mib": g.floor_mib,
        "psi_max": g.psi_max,
        "low": g.watching() && !g.room(&r),
    })
}

fn metrics(state: &State) -> Response<Body> {
    let m = &state.metrics;
    let q = &state.queue.metrics;
    let (llm_starts, llm_stops) = state
        .engines
        .as_ref()
        .and_then(|s| s.states().into_iter().find(|e| state.is_primary(&e.id)))
        .map(|e| (e.starts, e.stops))
        .unwrap_or((0, 0));
    let em = state.engines.as_ref().map(|s| &s.metrics);
    let switches = em.map(|m| m.switches.load(Relaxed)).unwrap_or(0);
    let rows: [(&str, &str, u64); 21] = [
        ("llmcf_gateway_requests_total", "requests received", m.requests.load(Relaxed)),
        ("llmcf_gateway_answered_total", "requests the model server answered", m.ok.load(Relaxed)),
        ("llmcf_gateway_refused_total", "requests refused by the strict checks", m.refused.load(Relaxed)),
        ("llmcf_gateway_too_large_total", "bodies over the size cap", m.too_large.load(Relaxed)),
        ("llmcf_gateway_rate_limited_total", "requests over the rate limit", m.rate_limited.load(Relaxed)),
        ("llmcf_gateway_queued_total", "requests that waited for a place in flight", q.queued_total.load(Relaxed)),
        ("llmcf_gateway_queue_timeouts_total", "requests that waited their whole time (503)", q.timeouts.load(Relaxed)),
        ("llmcf_gateway_queue_full_total", "requests that found the queue full (429)", q.full.load(Relaxed)),
        ("llmcf_gateway_queue_gone_total", "waiting requests whose client left", q.gone.load(Relaxed)),
        ("llmcf_gateway_admission_held_total", "times the memory guard held requests back", q.held.load(Relaxed)),
        ("llmcf_gateway_unauthorized_total", "requests without the token", m.unauthorized.load(Relaxed)),
        ("llmcf_gateway_upstream_errors_total", "model server errors", m.upstream_errors.load(Relaxed)),
        ("llmcf_gateway_prompt_tokens_estimated_total", "prompt tokens counted", m.prompt_tokens.load(Relaxed)),
        (
            "llmcf_gateway_system_prompts_dropped_total",
            "repeated system prompts dropped",
            m.system_dropped.load(Relaxed),
        ),
        (
            "llmcf_gateway_tool_results_pointed_total",
            "repeated tool results pointed at",
            m.results_pointed.load(Relaxed),
        ),
        ("llmcf_gateway_max_tokens_clamped_total", "answers cut to the budget", m.clamped.load(Relaxed)),
        ("llmcf_gateway_images_total", "image parts sent to the vision engine", m.images.load(Relaxed)),
        ("llmcf_gateway_generic_total", "requests answered through a generic route", m.generic.load(Relaxed)),
        ("llmcf_gateway_llm_starts_total", "model server starts", llm_starts),
        ("llmcf_gateway_llm_stops_total", "model server stops", llm_stops),
        ("llmcf_gateway_engine_switches_total", "engines stopped so another one could run in turn", switches),
    ];
    let mut text = String::new();
    for (name, help, v) in rows {
        text.push_str(&format!("# HELP {name} {help}\n# TYPE {name} counter\n{name} {v}\n"));
    }
    for (name, help, v) in [
        ("llmcf_gateway_inflight", "requests in flight", state.queue.running() as u64),
        ("llmcf_gateway_queued", "requests waiting for a place in flight", state.queue.queued() as u64),
        ("llmcf_gateway_memory_low", "1 while the memory guard holds new requests back", state.memory.is_low() as u64),
        (
            "llmcf_gateway_memory_available_mib",
            "the board's MemAvailable",
            state.memory.reading().available_mib.unwrap_or(0),
        ),
    ] {
        text.push_str(&format!("# HELP {name} {help}\n# TYPE {name} gauge\n{name} {v}\n"));
    }
    for (name, help, pick) in [
        ("llmcf_gateway_route_requests_total", "requests per route", 0),
        ("llmcf_gateway_route_refused_total", "requests per route the gateway answered with an error", 1),
        ("llmcf_gateway_route_bytes_in_total", "request body bytes per route", 2),
    ] {
        text.push_str(&format!("# HELP {name} {help}\n# TYPE {name} counter\n"));
        for (r, rm) in state.routes.routes.iter().zip(m.routes.iter()) {
            let v = [&rm.requests, &rm.refused, &rm.bytes_in][pick].load(Relaxed);
            let matched = if r.images { ",match=\"images\"" } else { "" };
            text.push_str(&format!("{name}{{route=\"{}\"{matched}}} {v}\n", r.path));
        }
    }
    let wait = em.map(|m| m.switch_wait_micros.load(Relaxed)).unwrap_or(0) as f64 / 1e6;
    let name = "llmcf_gateway_engine_switch_wait_seconds_total";
    text.push_str(&format!("# HELP {name} time requests waited for an engine switch\n# TYPE {name} counter\n"));
    text.push_str(&format!("{name} {wait:.6}\n"));
    if let Some(sched) = state.engines.as_ref() {
        for (name, help) in [
            ("llmcf_gateway_engine_running", "1 while the engine runs (the current engine in turn)"),
            ("llmcf_gateway_engine_inflight", "requests at the engine"),
        ] {
            text.push_str(&format!("# HELP {name} {help}\n# TYPE {name} gauge\n"));
            for e in sched.states() {
                let v = if name.ends_with("running") { (e.state == "running") as u64 } else { e.inflight as u64 };
                text.push_str(&format!("{name}{{engine=\"{}\"}} {v}\n", e.id));
            }
        }
    }
    let name = "llmcf_gateway_queue_wait_seconds";
    text.push_str(&format!("# HELP {name} time a request waited for its place\n# TYPE {name} histogram\n"));
    for (i, b) in queue::BUCKETS.iter().enumerate() {
        text.push_str(&format!("{name}_bucket{{le=\"{b}\"}} {}\n", q.buckets[i].load(Relaxed)));
    }
    let count = q.wait_count.load(Relaxed);
    text.push_str(&format!("{name}_bucket{{le=\"+Inf\"}} {count}\n"));
    text.push_str(&format!("{name}_sum {:.6}\n{name}_count {count}\n", q.wait_micros.load(Relaxed) as f64 / 1e6));
    let mut r = Response::new(full(text));
    r.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static("text/plain; version=0.0.4"));
    r
}

fn refusal(state: &State, r: Refusal) -> Response<Body> {
    state.metrics.refused.fetch_add(1, Relaxed);
    error(StatusCode::from_u16(r.status).unwrap_or(StatusCode::BAD_REQUEST), r.code, &r.message)
}

/// How an engine's answer goes back to the client.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Answer {
    /// as it is (llama-server's OpenAI-compatible answers, a stream too; a generic route's answer)
    Pass,
    /// whisper-server's: its text format as text/plain (it says text/html), an error as the gateway's own error
    Speech { text: bool },
}

fn engine_name(id: &str) -> String {
    match id {
        LLM => "the model server".into(),
        STT => "the speech engine".into(),
        VISION => "the vision engine".into(),
        other => format!("the {other} engine"),
    }
}

/// A checked request for an engine: its method, body, Content-Type and how its answer goes back.
struct Outgoing {
    method: Method,
    body: Chunks,
    ctype: String,
    answer: Answer,
}

fn is_event_stream(h: &hyper::HeaderMap) -> bool {
    h.get(CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.trim().to_ascii_lowercase().starts_with("text/event-stream"))
}

/// Sends a checked request to the engine of route `idx` (made ready first: engine_ready) at the route's own path and
/// passes the answer back within the route's timeout (its head, and every gap inside it), the permit and the engine's
/// ticket held to the answer's last byte. An event stream comes back only on a route that declares one (`stream`);
/// an http-sse route's answer must be one.
async fn forward(state: &State, idx: usize, out: Outgoing, permit: Permit, t: &mut Trace) -> Response<Body> {
    let r = &state.routes.routes[idx];
    let Outgoing { method, body, ctype, answer } = out;
    let ticket = match engine_ready(state, &r.engine, &mut t.note).await {
        Ok(ticket) => ticket,
        Err(e) => {
            state.metrics.upstream_errors.fetch_add(1, Relaxed);
            return retry(error(StatusCode::SERVICE_UNAVAILABLE, "upstream_down", &e), 15);
        }
    };
    let Some(port) = state.port_of(&r.engine) else {
        return error(StatusCode::NOT_IMPLEMENTED, "no_engine", "the board runs no such engine");
    };
    let req = upstream_request(state, &r.engine, method, &r.upstream, body, &ctype);
    let resp = match upstream_within(port, req, r.timeout).await {
        Ok(resp) => resp,
        Err(e) => {
            state.metrics.upstream_errors.fetch_add(1, Relaxed);
            let msg = if e == "down" { format!("{} does not answer", engine_name(&r.engine)) } else { e };
            return retry(error(StatusCode::BAD_GATEWAY, "upstream_error", &msg), 5);
        }
    };
    let streams = is_event_stream(resp.headers());
    if resp.status().is_success() && (streams && !r.stream || !streams && r.protocol == Protocol::Sse) {
        // the engine answers otherwise than its block declares: nothing of it is passed on
        state.metrics.upstream_errors.fetch_add(1, Relaxed);
        let msg = if streams {
            "the engine answered an event stream its route does not declare"
        } else {
            "the engine answered no event stream on its stream route"
        };
        return retry(error(StatusCode::BAD_GATEWAY, "upstream_error", msg), 5);
    }
    t.engine_answered = true;
    if resp.status().is_success() {
        state.metrics.ok.fetch_add(1, Relaxed);
        if !matches!(r.protocol, Protocol::Chat | Protocol::Completions | Protocol::Models | Protocol::Transcription) {
            state.metrics.generic.fetch_add(1, Relaxed);
        }
    } else {
        state.metrics.upstream_errors.fetch_add(1, Relaxed);
        if let Answer::Speech { .. } = answer {
            // whisper's own error text is not passed on (it may name the file); its status is
            t.engine_answered = false;
            return if resp.status() == StatusCode::BAD_REQUEST {
                error(StatusCode::BAD_REQUEST, "invalid_audio", "the speech engine could not read the audio")
            } else {
                let msg = format!("the speech engine answered {}", resp.status().as_u16());
                retry(error(StatusCode::BAD_GATEWAY, "upstream_error", &msg), 5)
            };
        }
    }
    let (parts, body) = resp.into_parts();
    // the permit and the engine's ticket are held until the answer's last byte went out (a stream too)
    let body: Body = body
        .map_frame(move |f| {
            let _held = (&permit, &ticket);
            f
        })
        .map_err(|e| Box::new(e) as BoxError)
        .boxed_unsync();
    let mut out = Response::new(Idle::new(body, r.timeout).boxed_unsync());
    *out.status_mut() = parts.status;
    for name in [CONTENT_TYPE, hyper::header::CACHE_CONTROL] {
        if let Some(v) = parts.headers.get(&name) {
            out.headers_mut().insert(name, v.clone());
        }
    }
    if answer == (Answer::Speech { text: true }) {
        out.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static("text/plain; charset=utf-8"));
    }
    out
}

/// One connection's two ends and a second handle on its socket, only to see the client hang up while its request
/// waits in the queue (the request's own handle belongs to hyper).
pub struct Conn {
    pub peer: SocketAddr,
    pub local: SocketAddr,
    pub watch: Option<TcpStream>,
}

/// Ends when the connection's client hung up (or never, without a second handle).
async fn hung_up(watch: Option<&TcpStream>) {
    let Some(s) = watch else { return std::future::pending().await };
    loop {
        match s.ready(Interest::READABLE).await {
            Ok(r) if r.is_read_closed() || r.is_error() => return,
            Ok(_) => {
                // only readable (the request's body not read yet): forget that and wait for the next event
                let _ = s.try_io(Interest::READABLE, || Err::<(), _>(std::io::ErrorKind::WouldBlock.into()));
            }
            Err(_) => return,
        }
    }
}

async fn handle(state: Arc<State>, conn: Arc<Conn>, req: Request<Incoming>) -> Response<Body> {
    let started = Instant::now();
    let method = req.method().clone();
    let path = req.uri().path().to_string();
    let mut t = Trace::default();
    let resp = route(&state, &conn, req, &mut t).await;
    if let Some(rm) = t.route.and_then(|i| state.metrics.routes.get(i)) {
        rm.requests.fetch_add(1, Relaxed);
        rm.bytes_in.fetch_add(t.bytes_in, Relaxed);
        if resp.status().as_u16() >= 400 && !t.engine_answered {
            rm.refused.fetch_add(1, Relaxed);
        }
    }
    eprintln!(
        "llmcf-gateway: {} {} {} {}ms{}",
        method,
        if path.len() <= 64 { path.as_str() } else { "(long path)" },
        resp.status().as_u16(),
        started.elapsed().as_millis(),
        t.note
    );
    resp
}

/// The Content-Length a request announces (None: none, or not a number).
fn announced(req: &Request<Incoming>) -> Option<u64> {
    req.headers().get(CONTENT_LENGTH).and_then(|v| v.to_str().ok()).and_then(|v| v.trim().parse().ok())
}

async fn route(state: &Arc<State>, conn: &Conn, req: Request<Incoming>, t: &mut Trace) -> Response<Body> {
    let peer = conn.peer;
    state.metrics.requests.fetch_add(1, Relaxed);
    let local = peer.ip().is_loopback();
    if let (Some(token), false) = (&state.token, local) {
        let given = req.headers().get(AUTHORIZATION).and_then(|v| v.to_str().ok()).unwrap_or("");
        if !equal(given.as_bytes(), format!("Bearer {token}").as_bytes()) {
            state.metrics.unauthorized.fetch_add(1, Relaxed);
            return error(StatusCode::UNAUTHORIZED, "unauthorized", "send the gateway's bearer token");
        }
    }
    let path = req.uri().path().to_string();
    match (req.method(), path.as_str()) {
        (&Method::GET, "/health") => return health(state).await,
        (&Method::GET, "/metrics") => return metrics(state),
        _ => {}
    }
    let Some(idx) = state.routes.find(req.method().as_str(), &path) else {
        return error(StatusCode::NOT_FOUND, "not_found", "no such route");
    };
    t.route = Some(idx);
    let r = &state.routes.routes[idx];
    if state.port_of(&r.engine).is_none() {
        let msg = if r.protocol == Protocol::Transcription {
            "the board runs no speech engine the gateway speaks to (a block whose reach serves the speech route, such \
             as stt-whisper-cpp or stt-whisper-cpp-program)"
                .to_string()
        } else {
            format!("the board runs no {} engine", r.engine)
        };
        return error(StatusCode::NOT_IMPLEMENTED, "no_engine", &msg);
    }
    let ip: IpAddr = peer.ip();
    let wait = state.rate.lock().map(|mut r| r.take(ip, Instant::now())).unwrap_or(Err(60));
    if let Err(seconds) = wait {
        state.metrics.rate_limited.fetch_add(1, Relaxed);
        return retry(error(StatusCode::TOO_MANY_REQUESTS, "rate_limited", "too many requests"), seconds);
    }
    if r.protocol == Protocol::Models {
        let ticket = match engine_ready(state, &r.engine, &mut t.note).await {
            Ok(t) => t,
            Err(e) => return retry(error(StatusCode::SERVICE_UNAVAILABLE, "upstream_down", &e), 15),
        };
        let port = state.port_of(&r.engine).unwrap_or(0);
        let get =
            upstream_request(state, &r.engine, Method::GET, &r.upstream, Chunks::new(Vec::new()), "application/json");
        return match upstream_within(port, get, r.timeout).await {
            Ok(resp) => {
                t.engine_answered = true;
                let status = resp.status();
                let body = resp
                    .into_body()
                    .map_frame(move |f| {
                        let _held = &ticket;
                        f
                    })
                    .map_err(|e| Box::new(e) as BoxError);
                let mut out = Response::new(body.boxed_unsync());
                *out.status_mut() = status;
                out.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
                out
            }
            Err(_) => error(StatusCode::BAD_GATEWAY, "upstream_error", "the model server does not answer"),
        };
    }
    let get = req.method() == Method::GET;
    let ctype = req.headers().get(CONTENT_TYPE).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
    let boundary = match r.protocol {
        Protocol::Transcription | Protocol::Multipart => {
            let Some(b) = audio::boundary(&ctype) else {
                state.metrics.refused.fetch_add(1, Relaxed);
                return error(
                    StatusCode::UNSUPPORTED_MEDIA_TYPE,
                    "invalid_multipart",
                    "the body is multipart/form-data with its boundary",
                );
            };
            b
        }
        // a binary body is taken by its first bytes, never by the client's word
        Protocol::Binary => String::new(),
        _ if get => String::new(),
        _ => {
            if !ctype.to_ascii_lowercase().starts_with("application/json") {
                state.metrics.refused.fetch_add(1, Relaxed);
                return error(StatusCode::UNSUPPORTED_MEDIA_TYPE, "invalid_json", "the body is application/json");
            }
            String::new()
        }
    };
    let cap = if get { 0 } else { state.body_cap(idx) };
    let body_wait = if r.protocol.json_body() { BODY_WAIT } else { AUDIO_BODY_WAIT };
    if announced(&req).is_some_and(|n| n > cap as u64) {
        // refused before it waits or is read
        state.metrics.too_large.fetch_add(1, Relaxed);
        let msg = if get { "a GET takes no body" } else { "the body is larger than the gateway takes" };
        return error(StatusCode::PAYLOAD_TOO_LARGE, "too_large", msg);
    }
    let auth = req.headers().get(AUTHORIZATION).and_then(|v| v.to_str().ok());
    let key = peer::client_key(auth, state.token.as_deref(), peer, conn.local);
    let kind = key.kind();
    let permit = match state.queue.acquire(key, hung_up(conn.watch.as_ref())).await {
        Ok((permit, waited)) => {
            if waited > Duration::ZERO {
                t.note = format!(" queued={}ms by={kind}", waited.as_millis());
            }
            permit
        }
        Err(Refused::Full) => {
            t.note = " queue=full".into();
            return retry(
                error(StatusCode::TOO_MANY_REQUESTS, "queue_full", "the model is busy and its queue is full"),
                5,
            );
        }
        Err(Refused::Timeout) => {
            t.note = " queue=timeout".into();
            return retry(
                error(StatusCode::SERVICE_UNAVAILABLE, "queue_timeout", "the model was busy for the whole wait"),
                15,
            );
        }
        Err(Refused::Gone) => {
            t.note = " queue=gone".into();
            return error(StatusCode::REQUEST_TIMEOUT, "client_gone", "the client left while it waited");
        }
    };
    let before = std::mem::take(&mut t.note);
    let body = match read_body(req.into_body(), cap, body_wait).await {
        Ok(b) => b,
        Err(BodyFail::TooLarge) => {
            state.metrics.too_large.fetch_add(1, Relaxed);
            return error(StatusCode::PAYLOAD_TOO_LARGE, "too_large", "the body is larger than the gateway takes");
        }
        Err(BodyFail::Slow) => {
            state.metrics.refused.fetch_add(1, Relaxed);
            return error(StatusCode::REQUEST_TIMEOUT, "slow_body", "the body did not arrive in time");
        }
        Err(BodyFail::Broken) => {
            state.metrics.refused.fetch_add(1, Relaxed);
            return error(StatusCode::BAD_REQUEST, "broken_body", "the body broke off");
        }
    };
    t.bytes_in = body.len() as u64;
    match r.protocol {
        Protocol::Transcription => transcribe(state, idx, body, &boundary, permit, before, t).await,
        Protocol::Chat | Protocol::Completions => text_route(state, idx, body, permit, before, t).await,
        _ => generic_route(state, idx, body, &boundary, permit, before, t).await,
    }
}

/// The chat and completion routes: the strict checks and savings (rules.rs), a chat with image parts to the engine of
/// the images route on its path.
async fn text_route(
    state: &State,
    idx: usize,
    body: Bytes,
    permit: Permit,
    before: String,
    t: &mut Trace,
) -> Response<Body> {
    let r = &state.routes.routes[idx];
    if state.budget.load(Relaxed) == 0 {
        state.budget.store((state.cfg.ctx_size / state.cfg.slots) as u64, Relaxed);
    }
    let lim = state.limits(&r.path);
    let checked = if r.protocol == Protocol::Chat { rules::chat(&body, &lim) } else { rules::completion(&body, &lim) };
    // the client's body is not needed any more (with images it is large): only the checked one stays
    drop(body);
    let o = match checked {
        Ok(o) => o,
        Err(e) => {
            t.note = format!(" refused={}", e.code);
            return refusal(state, e);
        }
    };
    if o.stream && !r.stream {
        t.note = " refused=no_stream".into();
        return refusal(
            state,
            rules::Refusal { status: 400, code: "invalid_value", message: "this route answers no stream".into() },
        );
    }
    let m = &state.metrics;
    m.prompt_tokens.fetch_add(o.prompt_tokens, Relaxed);
    m.system_dropped.fetch_add(o.system_dropped, Relaxed);
    m.results_pointed.fetch_add(o.results_pointed, Relaxed);
    m.images.fetch_add(o.images as u64, Relaxed);
    if o.clamped {
        m.clamped.fetch_add(1, Relaxed);
    }
    t.note = format!(
        "{before} prompt~{} max={} dropped={} pointed={}{}{}",
        o.prompt_tokens,
        o.max_tokens,
        o.system_dropped,
        o.results_pointed,
        if o.images > 0 { format!(" images={}", o.images) } else { String::new() },
        if o.stream { " stream" } else { "" }
    );
    // a chat with images goes to the images route's engine (counted on that route)
    let target = if o.images > 0 { state.routes.images_route(&r.path).unwrap_or(idx) } else { idx };
    if target != idx {
        t.route = Some(target);
    }
    let out = Chunks::new(vec![Bytes::from(o.body.to_string())]);
    drop(o);
    let out = Outgoing { method: Method::POST, body: out, ctype: "application/json".into(), answer: Answer::Pass };
    forward(state, target, out, permit, t).await
}

/// The other adapters (embeddings, speech output) and the generic checked passthrough (generic.rs): the body checked
/// by the route's protocol and declared limits, then sent to the engine at the route's own path.
async fn generic_route(
    state: &State,
    idx: usize,
    body: Bytes,
    boundary: &str,
    permit: Permit,
    before: String,
    t: &mut Trace,
) -> Response<Body> {
    let r = &state.routes.routes[idx];
    let json = |b: Bytes| Chunks::new(vec![b]);
    let checked: Result<(Chunks, String, String), rules::Refusal> = match r.protocol {
        Protocol::Embeddings => rules::embeddings(&body)
            .map(|v| (json(Bytes::from(v.to_string())), "application/json".into(), String::new())),
        Protocol::Speech => {
            rules::speech(&body).map(|v| (json(Bytes::from(v.to_string())), "application/json".into(), String::new()))
        }
        Protocol::Json | Protocol::Sse if r.method == "GET" => {
            Ok((Chunks::new(Vec::new()), "application/json".into(), String::new()))
        }
        Protocol::Json | Protocol::Sse => {
            rules::json_object(&body).map(|_| (json(body.clone()), "application/json".into(), String::new()))
        }
        Protocol::Multipart => generic::multipart(&body, boundary, &r.fields).map(|f| {
            let files = f.files;
            (Chunks::new(f.chunks), format!("multipart/form-data; boundary={boundary}"), format!(" files={files}"))
        }),
        Protocol::Binary => generic::binary(&body, &r.types)
            .map(|m| (json(body.clone()), m.mime().to_string(), format!(" type={}", m.word()))),
        _ => Err(rules::Refusal { status: 404, code: "not_found", message: "no such route".into() }),
    };
    drop(body);
    let (out, ctype, what) = match checked {
        Ok(v) => v,
        Err(e) => {
            t.note = format!("{before} refused={}", e.code);
            return refusal(state, e);
        }
    };
    t.note = format!("{before} {}{what}", r.protocol.word());
    let method = if r.method == "GET" { Method::GET } else { Method::POST };
    forward(state, idx, Outgoing { method, body: out, ctype, answer: Answer::Pass }, permit, t).await
}

/// The speech route: the upload checked (audio.rs) and sent to whisper-server's form at the route's own path.
async fn transcribe(
    state: &State,
    idx: usize,
    body: Bytes,
    boundary: &str,
    permit: Permit,
    before: String,
    t: &mut Trace,
) -> Response<Body> {
    let checked = audio::parse(&body, boundary).and_then(audio::transcription);
    let tr = match checked {
        Ok(tr) => tr,
        Err(r) => {
            t.note = format!("{before} refused={}", r.code);
            return refusal(state, r);
        }
    };
    t.note = format!("{before} audio={} bytes={} format={}", tr.kind.ext(), tr.audio.len(), tr.format);
    let form = Chunks::new(audio::whisper_form(&tr, boundary));
    let ctype = format!("multipart/form-data; boundary={boundary}");
    let answer = Answer::Speech { text: tr.format == "text" };
    drop(tr);
    drop(body);
    forward(state, idx, Outgoing { method: Method::POST, body: form, ctype, answer }, permit, t).await
}

// --- listening ---------------------------------------------------------------------------------------------

fn home() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME").map(PathBuf::from).ok_or("HOME is not set")?;
    if !home.is_absolute() {
        return Err("HOME is not an absolute path".into());
    }
    Ok(home)
}

/// The route table (the bundle's `--routes` file, or the one made from the options), the engine on --upstream-port
/// and the engines the scheduler controls, each with its port from the table where `--engine` gives none. Refused: a
/// table engine on the gateway's own port, an `--engine` whose port is not its table engine's.
fn route_table(cfg: &Serve) -> Result<(Table, Option<String>, Vec<Spec>), String> {
    let table = match &cfg.routes {
        None => Table::from_args(cfg),
        Some(path) => {
            let meta = std::fs::metadata(path).map_err(|_| "the route table (--routes) cannot be read".to_string())?;
            if !meta.is_file() || meta.len() > crate::routes::MAX_TABLE_BYTES as u64 {
                return Err("the route table (--routes) is no file of at most 64 KiB".into());
            }
            let data = std::fs::read(path).map_err(|_| "the route table (--routes) cannot be read".to_string())?;
            Table::parse(&data).map_err(|e| format!("the route table (--routes): {e}"))?
        }
    };
    if table.engines.iter().any(|e| e.port == Some(cfg.port)) {
        return Err("an engine of the route table listens on the gateway's own port".into());
    }
    let primary = table.engines.iter().find(|e| e.port == Some(cfg.upstream_port)).map(|e| e.id.clone());
    let mut specs = cfg.engines.clone();
    for spec in &mut specs {
        let Some(e) = table.engine(&spec.id) else { continue };
        match (spec.port, e.port) {
            (Some(a), Some(b)) if a != b && primary.as_deref() != Some(spec.id.as_str()) => {
                return Err(format!("the {} engine's port is not the one of the route table", spec.id))
            }
            (None, Some(p)) if primary.as_deref() != Some(spec.id.as_str()) => spec.port = Some(p),
            _ => {}
        }
    }
    Ok((table, primary, specs))
}

pub fn build_state(cfg: Serve) -> Result<(Arc<State>, Option<Arc<rustls::ServerConfig>>), String> {
    build_state_on(cfg, None)
}

/// build_state with the board the scheduler works on (None: systemctl; tests give a fake board).
pub fn build_state_on(
    cfg: Serve,
    board: Option<Arc<dyn Hooks>>,
) -> Result<(Arc<State>, Option<Arc<rustls::ServerConfig>>), String> {
    let home = home()?;
    let key = std::fs::read_to_string(home.join(KEY_REL))
        .ok()
        .map(|k| k.trim().to_string())
        .filter(|k| !k.is_empty() && k.bytes().all(|c| c.is_ascii_graphic()));
    if key.is_none() {
        eprintln!("llmcf-gateway: no key file of the model server in this account's home; requests go without one");
    }
    let (token, fingerprint, tls) = if cfg.bind == Bind::Lan {
        let dir = tls::state_dir(&home)?;
        let id = tls::identity(&dir)?;
        let fp = id.fingerprint.clone();
        (Some(tls::token(&dir)?), Some(fp), Some(tls::server_config(id)?))
    } else {
        (None, None, None)
    };
    let (table, primary, engines) = route_table(&cfg)?;
    let guard = Arc::new(Guard::new(cfg.memory_floor_mb as u64, cfg.memory_psi_max, Box::new(memory::read_proc)));
    let admit = guard.clone();
    let queue = Queue::new(
        cfg.max_inflight as usize,
        cfg.queue_max as usize,
        Duration::from_secs(cfg.queue_wait_seconds as u64),
        Box::new(move || admit.ok()),
    );
    let state = Arc::new(State {
        rate: Mutex::new(RateLimit::new(cfg.rate_per_minute)),
        queue,
        memory: guard,
        metrics: Metrics {
            routes: table.routes.iter().map(|_| RouteMetrics::default()).collect(),
            ..Metrics::default()
        },
        budget: AtomicU64::new((cfg.ctx_size / cfg.slots) as u64),
        engines: (!engines.is_empty()).then(|| {
            let timing = Timing {
                hold: Duration::from_secs(cfg.engine_hold_seconds as u64),
                switch_wait: Duration::from_secs(cfg.queue_wait_seconds as u64),
                ..Timing::default()
            };
            Scheduler::new(cfg.engines_mode, cfg.engines_budget_mb, engines, timing)
        }),
        routes: table,
        primary,
        key,
        token,
        fingerprint,
        cfg,
        board,
    });
    Ok((state, tls))
}

/// The connection and a second handle on the same socket (a duplicate descriptor), to see its client hang up.
fn with_watch(stream: TcpStream) -> Option<(TcpStream, Option<TcpStream>)> {
    let std_stream = stream.into_std().ok()?;
    let watch = std_stream.try_clone().ok().and_then(|d| TcpStream::from_std(d).ok());
    Some((TcpStream::from_std(std_stream).ok()?, watch))
}

pub async fn serve(state: Arc<State>, tls_cfg: Option<Arc<rustls::ServerConfig>>) -> Result<(), String> {
    let addr: SocketAddr = match state.cfg.bind {
        Bind::Loopback => SocketAddr::from(([127, 0, 0, 1], state.cfg.port)),
        Bind::Lan => SocketAddr::from(([0, 0, 0, 0], state.cfg.port)),
    };
    if state.cfg.bind == Bind::Lan && tls_cfg.is_none() {
        return Err("the local-network mode runs only with TLS".into());
    }
    let listener = TcpListener::bind(addr).await.map_err(|e| format!("cannot listen on port {}: {e}", addr.port()))?;
    eprintln!(
        "llmcf-gateway {}: listening on {} port {} ({}){}",
        env!("CARGO_PKG_VERSION"),
        state.cfg.bind.word(),
        addr.port(),
        if tls_cfg.is_some() { "TLS 1.3" } else { "loopback HTTP" },
        state.fingerprint.as_ref().map(|f| format!(", certificate sha256 {f}")).unwrap_or_default()
    );
    if let Some(sched) = state.engines.clone() {
        eprintln!(
            "llmcf-gateway: engines {} ({}){}",
            sched.specs.iter().map(|e| format!("{}={}MiB", e.id, e.mib)).collect::<Vec<_>>().join(","),
            sched.mode.word(),
            if sched.budget_mib > 0 { format!(", {} MiB together", sched.budget_mib) } else { String::new() }
        );
        let st = state.clone();
        tokio::spawn(async move {
            // which engines run already (in turn, those that do not fit together are stopped), then the llm
            sched.reconcile(&*st).await;
            let primary = st.primary.clone().unwrap_or_else(|| LLM.to_string());
            if sched.index(&primary).is_some() && !sched.running(&primary) {
                if let Err(e) = sched.prepare(&*st, &primary).await {
                    eprintln!("llmcf-gateway: the model server could not be started ({e}); it is tried again");
                }
            }
        });
        tokio::spawn(idle_stopper(state.clone()));
    }
    let ticking = state.clone();
    tokio::spawn(async move {
        // the waiting requests are let in once the admission check says yes again (nothing else wakes them then)
        loop {
            tokio::time::sleep(Duration::from_secs(1)).await;
            ticking.queue.tick();
        }
    });
    let acceptor = tls_cfg.map(tokio_rustls::TlsAcceptor::from);
    loop {
        let Ok((stream, peer)) = listener.accept().await else {
            tokio::time::sleep(Duration::from_millis(100)).await;
            continue;
        };
        let state = state.clone();
        let acceptor = acceptor.clone();
        let local = stream.local_addr().unwrap_or(addr);
        let Some((stream, watch)) = with_watch(stream) else { continue };
        let conn_info = Arc::new(Conn { peer, local, watch });
        tokio::spawn(async move {
            let svc = hyper::service::service_fn(move |req| {
                let state = state.clone();
                let conn_info = conn_info.clone();
                async move { Ok::<_, std::convert::Infallible>(handle(state, conn_info, req).await) }
            });
            let conn = hyper::server::conn::http1::Builder::new()
                .max_buf_size(64 * 1024)
                .header_read_timeout(Duration::from_secs(20))
                .timer(hyper_util::rt::TokioTimer::new())
                .to_owned();
            match acceptor {
                Some(a) => {
                    if let Ok(Ok(tls)) = tokio::time::timeout(Duration::from_secs(10), a.accept(stream)).await {
                        let _ = conn.serve_connection(TokioIo::new(tls), svc).await;
                    }
                }
                None => {
                    let _ = conn.serve_connection(TokioIo::new(stream), svc).await;
                }
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engines::Mode;
    use std::collections::HashMap;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::sync::Semaphore;

    /// A request body that never sends a byte (a client that stalls after its headers).
    struct Stalled;

    impl hyper::body::Body for Stalled {
        type Data = Bytes;
        type Error = std::convert::Infallible;
        fn poll_frame(
            self: Pin<&mut Self>,
            _cx: &mut Context<'_>,
        ) -> Poll<Option<Result<hyper::body::Frame<Bytes>, Self::Error>>> {
            Poll::Pending
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn a_stalled_body_ends_after_its_wait_and_frees_the_permit() {
        // regression: the body was read without a time limit after the in-flight permit was taken, so a client that
        // sent its headers and then nothing held one of the few permits for as long as it liked
        assert_eq!(BODY_WAIT, Duration::from_secs(30));
        let permits = Arc::new(Semaphore::new(1));
        let permit = permits.clone().try_acquire_owned().unwrap();
        let started = Instant::now();
        let got = read_body(Stalled, 1024, Duration::from_millis(50)).await;
        assert!(matches!(got, Err(BodyFail::Slow)), "a stalled body is refused as slow");
        assert!(started.elapsed() < Duration::from_secs(5));
        drop(permit);
        assert_eq!(permits.available_permits(), 1);
    }

    #[tokio::test(flavor = "current_thread")]
    async fn a_body_in_time_is_read_and_a_large_one_refused() {
        let ok = read_body(Full::new(Bytes::from_static(b"{\"a\":1}")), 1024, BODY_WAIT).await;
        assert_eq!(ok.ok(), Some(Bytes::from_static(b"{\"a\":1}")));
        let big = read_body(Full::new(Bytes::from(vec![b'x'; 2048])), 1024, BODY_WAIT).await;
        assert!(matches!(big, Err(BodyFail::TooLarge)));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn a_large_announced_body_is_refused_before_it_is_read() {
        let big = read_body(Chunks::new(vec![Bytes::from(vec![0u8; 4096])]), 1024, BODY_WAIT).await;
        assert!(matches!(big, Err(BodyFail::TooLarge)));
        let two = read_body(Chunks::new(vec![Bytes::from_static(b"ab"), Bytes::from_static(b"cd")]), 4, BODY_WAIT);
        assert_eq!(two.await.ok(), Some(Bytes::from_static(b"abcd")));
    }

    /// One request's head and body as a fake engine reads it.
    async fn read_request(s: &mut TcpStream) -> Option<(String, String, Vec<u8>)> {
        let mut buf = Vec::new();
        let mut chunk = [0u8; 8192];
        let head_end = loop {
            let n = s.read(&mut chunk).await.ok()?;
            if n == 0 {
                return None;
            }
            buf.extend_from_slice(&chunk[..n]);
            if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break i + 4;
            }
        };
        let head = String::from_utf8_lossy(&buf[..head_end]).to_string();
        let length: usize = head
            .lines()
            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().to_string()))
            .and_then(|v| v.parse().ok())
            .unwrap_or(0);
        while buf.len() < head_end + length {
            let n = s.read(&mut chunk).await.ok()?;
            if n == 0 {
                break;
            }
            buf.extend_from_slice(&chunk[..n]);
        }
        let mut words = head.split_whitespace();
        let (method, path) = (words.next()?.to_string(), words.next()?.to_string());
        Some((method, path, buf[head_end..].to_vec()))
    }

    /// A fake engine: llama-server's /health, /props and chat, or whisper-server's /health and /inference; what reached
    /// it is recorded.
    async fn fake_engine(listener: TcpListener, whisper: bool, seen: Arc<Mutex<Vec<String>>>) {
        loop {
            let Ok((mut s, _)) = listener.accept().await else { return };
            let seen = seen.clone();
            tokio::spawn(async move {
                let Some((method, path, body)) = read_request(&mut s).await else { return };
                let answer = match (method.as_str(), path.as_str(), whisper) {
                    ("GET", "/health", _) => r#"{"status":"ok"}"#.to_string(),
                    ("GET", "/props", false) => r#"{"default_generation_settings":{"n_ctx":2048}}"#.to_string(),
                    ("POST", "/v1/chat/completions", false) => {
                        seen.lock().unwrap().push("llm chat".into());
                        r#"{"choices":[{"index":0,"message":{"role":"assistant","content":"OK"}}]}"#.to_string()
                    }
                    ("POST", "/inference", true) => {
                        let text = String::from_utf8_lossy(&body);
                        let file = text.contains("name=\"file\"; filename=\"audio.wav\"");
                        seen.lock().unwrap().push(format!("whisper inference file={file}"));
                        r#"{"text":" hello"}"#.to_string()
                    }
                    _ => {
                        let _ = s.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n").await;
                        return;
                    }
                };
                let head = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    answer.len()
                );
                let _ = s.write_all(format!("{head}{answer}").as_bytes()).await;
            });
        }
    }

    /// A fake board: starting a unit opens its fake engine on its port, stopping it closes the port.
    struct Board {
        ports: HashMap<String, (u16, bool)>,
        servers: Mutex<HashMap<String, tokio::task::JoinHandle<()>>>,
        calls: Mutex<Vec<String>>,
        seen: Arc<Mutex<Vec<String>>>,
    }

    impl Hooks for Board {
        fn control<'a>(&'a self, verb: &'static str, unit: &'a str) -> Fut<'a, Result<bool, String>> {
            Box::pin(async move {
                let (port, whisper) = *self.ports.get(unit).ok_or("unknown unit")?;
                let mut servers = self.servers.lock().unwrap();
                match verb {
                    "start" => {
                        self.calls.lock().unwrap().push(format!("start {unit}"));
                        let std = std::net::TcpListener::bind(("127.0.0.1", port)).map_err(|e| e.to_string())?;
                        std.set_nonblocking(true).map_err(|e| e.to_string())?;
                        let listener = TcpListener::from_std(std).map_err(|e| e.to_string())?;
                        servers
                            .insert(unit.to_string(), tokio::spawn(fake_engine(listener, whisper, self.seen.clone())));
                        Ok(true)
                    }
                    "stop" => {
                        self.calls.lock().unwrap().push(format!("stop {unit}"));
                        if let Some(h) = servers.remove(unit) {
                            h.abort();
                        }
                        Ok(true)
                    }
                    _ => Ok(servers.contains_key(unit)),
                }
            })
        }
        fn up<'a>(&'a self, spec: &'a Spec) -> Fut<'a, bool> {
            Box::pin(async move { engines::http_up(self.ports[&spec.unit].0).await })
        }
    }

    fn free_port() -> u16 {
        std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
    }

    async fn send(port: u16, method: &str, path: &str, ctype: &str, body: &[u8]) -> (u16, String) {
        let mut s = TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        let head = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\n\
             Connection: close\r\n\r\n",
            body.len()
        );
        s.write_all(head.as_bytes()).await.unwrap();
        s.write_all(body).await.unwrap();
        let mut out = Vec::new();
        let _ = tokio::time::timeout(Duration::from_secs(20), s.read_to_end(&mut out)).await;
        let text = String::from_utf8_lossy(&out).to_string();
        let status = text.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0);
        (status, text.split_once("\r\n\r\n").map(|(_, b)| b.to_string()).unwrap_or_default())
    }

    /// The in-turn switch llm <-> speech through the gateway's real HTTP routes: a chat request, an audio upload and a
    /// chat request again, each engine started (and the other one stopped) by the scheduler on a fake board whose
    /// engines are real HTTP servers on their own loopback ports.
    #[tokio::test(flavor = "current_thread")]
    async fn in_turn_the_routes_switch_the_llm_and_the_speech_engine() {
        let (port, llm_port, stt_port) = (free_port(), free_port(), free_port());
        let seen = Arc::new(Mutex::new(Vec::new()));
        let board = Arc::new(Board {
            ports: HashMap::from([
                ("llmcf-llm.service".to_string(), (llm_port, false)),
                ("llmcf-stt.service".to_string(), (stt_port, true)),
            ]),
            servers: Mutex::new(HashMap::new()),
            calls: Mutex::new(Vec::new()),
            seen: seen.clone(),
        });
        let cfg = Serve {
            port,
            upstream_port: llm_port,
            memory_floor_mb: 0,
            memory_psi_max: 0,
            engines: vec![
                Spec { id: LLM.into(), unit: "llmcf-llm.service".into(), mib: 700, port: None },
                Spec { id: STT.into(), unit: "llmcf-stt.service".into(), mib: 300, port: Some(stt_port) },
            ],
            engines_mode: Mode::InTurn,
            engine_hold_seconds: 0,
            stt_port: Some(stt_port),
            ..Serve::default()
        };
        let (state, _) = build_state_on(cfg, Some(board.clone() as Arc<dyn Hooks>)).unwrap();
        let server = tokio::spawn(serve(state.clone(), None));
        let until = Instant::now() + Duration::from_secs(10);
        while !engines::http_up(llm_port).await {
            assert!(Instant::now() < until, "the gateway started the llm at its start");
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        let chat = br#"{"messages":[{"role":"user","content":"x"}]}"#;
        let (status, body) = send(port, "POST", "/v1/chat/completions", "application/json", chat).await;
        assert_eq!(status, 200, "{body}");
        let audio = crate::audio::tests::wav(2000);
        let form = crate::audio::tests::form(&[
            ("file", Some("talk.wav"), Some("audio/wav"), &audio),
            ("model", None, None, b"whisper-1"),
        ]);
        let ctype = format!("multipart/form-data; boundary={}", crate::audio::tests::B);
        let (status, body) = send(port, "POST", "/v1/audio/transcriptions", &ctype, &form).await;
        assert_eq!(status, 200, "{body}");
        assert!(body.contains(r#"{"text":" hello"}"#), "{body}");
        assert!(engines::http_up(stt_port).await && !engines::http_up(llm_port).await, "only speech runs now");
        let (status, _) = send(port, "POST", "/v1/chat/completions", "application/json", chat).await;
        assert_eq!(status, 200);
        assert_eq!(
            *board.calls.lock().unwrap(),
            [
                "start llmcf-llm.service",
                "stop llmcf-llm.service",
                "start llmcf-stt.service",
                "stop llmcf-stt.service",
                "start llmcf-llm.service"
            ]
        );
        assert_eq!(*seen.lock().unwrap(), ["llm chat", "whisper inference file=true", "llm chat"]);
        let sched = state.engines.as_ref().unwrap();
        assert_eq!(sched.metrics.switches.load(Relaxed), 2);
        let m = &state.metrics.routes;
        let speech = state.routes.find("POST", "/v1/audio/transcriptions").unwrap();
        let chat_i = state.routes.find("POST", "/v1/chat/completions").unwrap();
        assert_eq!((m[speech].requests.load(Relaxed), m[speech].refused.load(Relaxed)), (1, 0));
        assert_eq!(m[speech].bytes_in.load(Relaxed), form.len() as u64);
        assert_eq!(m[chat_i].requests.load(Relaxed), 2);
        // a wrong upload is refused by the gateway, counted per route, and never wakes an engine
        let calls = board.calls.lock().unwrap().len();
        let bad = crate::audio::tests::form(&[("file", Some("x.webm"), None, b"\x1a\x45\xdf\xa3\x01\0\0\0")]);
        let (status, body) = send(port, "POST", "/v1/audio/transcriptions", &ctype, &bad).await;
        assert_eq!(status, 415, "{body}");
        assert!(body.contains("unsupported_audio"));
        assert_eq!(board.calls.lock().unwrap().len(), calls);
        assert_eq!(m[speech].refused.load(Relaxed), 1);
        let (_, health) = send(port, "GET", "/health", "text/plain", b"").await;
        let v: Value = serde_json::from_str(&health).unwrap();
        assert_eq!(v["routes"][3]["path"], "/v1/audio/transcriptions");
        assert_eq!(v["routes"][3]["engine"], "stt");
        assert_eq!(v["routes"][3]["port"], stt_port);
        assert_eq!(v["routes"][1]["with_images"], Value::Null, "no vision engine");
        server.abort();
    }
}
