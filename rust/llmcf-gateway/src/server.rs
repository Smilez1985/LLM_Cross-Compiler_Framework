//! The HTTP front: OpenAI-compatible routes in front of llama-server on loopback, plus /health and /metrics.
//!
//! Loopback mode listens on 127.0.0.1 with plain HTTP; the local-network mode listens on every address with TLS 1.3
//! only (never plain HTTP off loopback), and a client that is not on the board itself must send the gateway's
//! bearer token. Every request is size-capped, rate-limited per client address and counted against a small number
//! of requests in flight; a request that finds them all taken waits in a fair queue (queue.rs) with a time limit and
//! a capped length. The log line of a request holds its route, status, time and token counts, never a prompt.

use crate::args::{Bind, Serve};
use crate::control;
use crate::limits::RateLimit;
use crate::memory::{self, Guard};
use crate::peer;
use crate::queue::{self, Permit, Queue, Refused};
use crate::rules::{self, Limits, Refusal};
use crate::tls;
use bytes::Bytes;
use http_body_util::{combinators::UnsyncBoxBody, BodyExt, Full, Limited};
use hyper::body::Incoming;
use hyper::header::{HeaderValue, AUTHORIZATION, CONTENT_TYPE, HOST, RETRY_AFTER};
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use serde_json::{json, Value};
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::io::Interest;
use tokio::net::{TcpListener, TcpStream};

pub type Body = UnsyncBoxBody<Bytes, hyper::Error>;

/// The runtime key of llama-server below the home (launcher.KEY_REL).
pub const KEY_REL: &str = ".config/llm-framework/llama-server.key";
const START_WAIT: Duration = Duration::from_secs(120);
const ANSWER_WAIT: Duration = Duration::from_secs(900);
/// How long a request's body may take once its in-flight permit is taken: a client that sends its headers and then
/// stalls frees the permit after this time (408), so a few slow clients cannot hold the model away from the others.
pub const BODY_WAIT: Duration = Duration::from_secs(30);

#[derive(Default)]
pub struct Metrics {
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
    pub llm_starts: AtomicU64,
    pub llm_stops: AtomicU64,
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
    pub last: Mutex<Instant>,
    pub budget: AtomicU64,
    pub stopped: Mutex<bool>,
}

impl State {
    fn limits(&self) -> Limits {
        Limits { budget: self.budget.load(Relaxed), max_tokens: self.cfg.max_tokens as u64, slots: self.cfg.slots }
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
}

/// The request body, at most `cap` bytes, read within `wait`.
pub async fn read_body<B>(body: B, cap: usize, wait: Duration) -> Result<Bytes, BodyFail>
where
    B: hyper::body::Body,
    B::Error: Into<Box<dyn std::error::Error + Send + Sync>>,
{
    match tokio::time::timeout(wait, Limited::new(body, cap).collect()).await {
        Err(_) => Err(BodyFail::Slow),
        Ok(Err(_)) => Err(BodyFail::TooLarge),
        Ok(Ok(collected)) => Ok(collected.to_bytes()),
    }
}

fn equal(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

// --- llama-server on loopback --------------------------------------------------------------------------------

async fn upstream(state: &State, req: Request<Full<Bytes>>) -> Result<Response<Incoming>, String> {
    let stream = TcpStream::connect(("127.0.0.1", state.cfg.upstream_port)).await.map_err(|_| "down".to_string())?;
    let (mut sender, conn) =
        hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|e| e.to_string())?;
    tokio::spawn(async move {
        let _ = conn.await;
    });
    tokio::time::timeout(ANSWER_WAIT, sender.send_request(req))
        .await
        .map_err(|_| "the model server did not answer in time".to_string())?
        .map_err(|e| e.to_string())
}

fn upstream_request(state: &State, method: Method, path: &str, body: Bytes) -> Request<Full<Bytes>> {
    let mut b = Request::builder()
        .method(method)
        .uri(path)
        .header(HOST, format!("127.0.0.1:{}", state.cfg.upstream_port))
        .header(CONTENT_TYPE, "application/json");
    if let Some(key) = &state.key {
        b = b.header(AUTHORIZATION, format!("Bearer {key}"));
    }
    b.body(Full::new(body)).unwrap_or_else(|_| Request::new(Full::new(Bytes::new())))
}

async fn upstream_json(state: &State, path: &str) -> Option<Value> {
    let req = upstream_request(state, Method::GET, path, Bytes::new());
    let resp = tokio::time::timeout(Duration::from_secs(3), upstream(state, req)).await.ok()?.ok()?;
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

/// The model server up, started through its unit when the gateway controls one; Err with the person's words.
async fn ensure_up(state: &State) -> Result<(), String> {
    if upstream_up(state).await {
        return Ok(());
    }
    let Some(unit) = state.cfg.llm_unit.clone() else { return Err("the model server does not answer".into()) };
    match run_systemctl("start", unit).await {
        Ok(true) => {
            state.metrics.llm_starts.fetch_add(1, Relaxed);
            if let Ok(mut s) = state.stopped.lock() {
                *s = false;
            }
        }
        Ok(false) => return Err("the model server's unit could not be started".into()),
        Err(e) => return Err(e),
    }
    let until = Instant::now() + START_WAIT;
    while Instant::now() < until {
        if upstream_up(state).await {
            return Ok(());
        }
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    Err("the model server was started but does not answer yet".into())
}

/// Stops the model server after `idle_stop_minutes` without a request (only when the gateway controls its unit).
pub async fn idle_stopper(state: Arc<State>) {
    let (Some(unit), minutes) = (state.cfg.llm_unit.clone(), state.cfg.idle_stop_minutes) else { return };
    if minutes == 0 {
        return;
    }
    loop {
        tokio::time::sleep(Duration::from_secs(30)).await;
        let idle = state.last.lock().map(|t| t.elapsed()).unwrap_or_default();
        let stopped = state.stopped.lock().map(|s| *s).unwrap_or(false);
        let busy = state.queue.running() > 0 || state.queue.queued() > 0;
        if stopped || busy || idle < Duration::from_secs(minutes as u64 * 60) {
            continue;
        }
        if let Ok(true) = run_systemctl("stop", unit.clone()).await {
            state.metrics.llm_stops.fetch_add(1, Relaxed);
            if let Ok(mut s) = state.stopped.lock() {
                *s = true;
            }
            eprintln!("llmcf-gateway: the model server was stopped after {minutes} idle minutes");
        }
    }
}

// --- the routes --------------------------------------------------------------------------------------------

async fn health(state: &State) -> Response<Body> {
    let up = upstream_up(state).await;
    let stopped = state.stopped.lock().map(|s| *s).unwrap_or(false);
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
            "control": if state.cfg.llm_unit.is_some() { "systemd" } else { "none" },
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
    let rows: [(&str, &str, u64); 18] = [
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
        ("llmcf_gateway_llm_starts_total", "model server starts", m.llm_starts.load(Relaxed)),
        ("llmcf_gateway_llm_stops_total", "model server stops", m.llm_stops.load(Relaxed)),
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

async fn forward(state: &State, path: &str, out: Value, permit: Permit) -> Response<Body> {
    if let Err(e) = ensure_up(state).await {
        state.metrics.upstream_errors.fetch_add(1, Relaxed);
        return retry(error(StatusCode::SERVICE_UNAVAILABLE, "upstream_down", &e), 10);
    }
    let req = upstream_request(state, Method::POST, path, Bytes::from(out.to_string()));
    match upstream(state, req).await {
        Ok(resp) => {
            if resp.status().is_success() {
                state.metrics.ok.fetch_add(1, Relaxed);
            } else {
                state.metrics.upstream_errors.fetch_add(1, Relaxed);
            }
            let (parts, body) = resp.into_parts();
            // the permit is held until the answer's last byte went out (a streamed answer too)
            let body = body
                .map_frame(move |f| {
                    let _held = &permit;
                    f
                })
                .boxed_unsync();
            let mut r = Response::new(body);
            *r.status_mut() = parts.status;
            for name in [CONTENT_TYPE, hyper::header::CACHE_CONTROL] {
                if let Some(v) = parts.headers.get(&name) {
                    r.headers_mut().insert(name, v.clone());
                }
            }
            r
        }
        Err(e) => {
            state.metrics.upstream_errors.fetch_add(1, Relaxed);
            let msg = if e == "down" { "the model server does not answer".to_string() } else { e };
            retry(error(StatusCode::BAD_GATEWAY, "upstream_error", &msg), 5)
        }
    }
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
    let mut note = String::new();
    let resp = route(&state, &conn, req, &mut note).await;
    eprintln!(
        "llmcf-gateway: {} {} {} {}ms{}",
        method,
        if path.len() <= 64 { path.as_str() } else { "(long path)" },
        resp.status().as_u16(),
        started.elapsed().as_millis(),
        note
    );
    resp
}

async fn route(state: &Arc<State>, conn: &Conn, req: Request<Incoming>, note: &mut String) -> Response<Body> {
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
        (&Method::GET, "/v1/models") | (&Method::POST, "/v1/chat/completions") | (&Method::POST, "/v1/completions") => {
        }
        _ => return error(StatusCode::NOT_FOUND, "not_found", "no such route"),
    }
    if let Ok(mut t) = state.last.lock() {
        *t = Instant::now();
    }
    let ip: IpAddr = peer.ip();
    let wait = state.rate.lock().map(|mut r| r.take(ip, Instant::now())).unwrap_or(Err(60));
    if let Err(seconds) = wait {
        state.metrics.rate_limited.fetch_add(1, Relaxed);
        return retry(error(StatusCode::TOO_MANY_REQUESTS, "rate_limited", "too many requests"), seconds);
    }
    if req.method() == Method::GET {
        if let Err(e) = ensure_up(state).await {
            return retry(error(StatusCode::SERVICE_UNAVAILABLE, "upstream_down", &e), 10);
        }
        return match upstream(state, upstream_request(state, Method::GET, "/v1/models", Bytes::new())).await {
            Ok(resp) => {
                let status = resp.status();
                let mut r = Response::new(resp.into_body().boxed_unsync());
                *r.status_mut() = status;
                r.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
                r
            }
            Err(_) => error(StatusCode::BAD_GATEWAY, "upstream_error", "the model server does not answer"),
        };
    }
    let json_type = req
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .map(|v| v.to_ascii_lowercase().starts_with("application/json"))
        .unwrap_or(false);
    if !json_type {
        state.metrics.refused.fetch_add(1, Relaxed);
        return error(StatusCode::UNSUPPORTED_MEDIA_TYPE, "invalid_json", "the body is application/json");
    }
    let auth = req.headers().get(AUTHORIZATION).and_then(|v| v.to_str().ok());
    let key = peer::client_key(auth, state.token.as_deref(), peer, conn.local);
    let kind = key.kind();
    let permit = match state.queue.acquire(key, hung_up(conn.watch.as_ref())).await {
        Ok((permit, waited)) => {
            if waited > Duration::ZERO {
                *note = format!(" queued={}ms by={kind}", waited.as_millis());
            }
            permit
        }
        Err(Refused::Full) => {
            *note = " queue=full".into();
            return retry(
                error(StatusCode::TOO_MANY_REQUESTS, "queue_full", "the model is busy and its queue is full"),
                5,
            );
        }
        Err(Refused::Timeout) => {
            *note = " queue=timeout".into();
            return retry(
                error(StatusCode::SERVICE_UNAVAILABLE, "queue_timeout", "the model was busy for the whole wait"),
                15,
            );
        }
        Err(Refused::Gone) => {
            *note = " queue=gone".into();
            return error(StatusCode::REQUEST_TIMEOUT, "client_gone", "the client left while it waited");
        }
    };
    let before = std::mem::take(note);
    let cap = state.cfg.max_body_kb as usize * 1024;
    let body = match read_body(req.into_body(), cap, BODY_WAIT).await {
        Ok(b) => b,
        Err(BodyFail::TooLarge) => {
            state.metrics.too_large.fetch_add(1, Relaxed);
            return error(StatusCode::PAYLOAD_TOO_LARGE, "too_large", "the body is larger than the gateway takes");
        }
        Err(BodyFail::Slow) => {
            state.metrics.refused.fetch_add(1, Relaxed);
            return error(StatusCode::REQUEST_TIMEOUT, "slow_body", "the body did not arrive in time");
        }
    };
    if state.budget.load(Relaxed) == 0 {
        state.budget.store((state.cfg.ctx_size / state.cfg.slots) as u64, Relaxed);
    }
    let lim = state.limits();
    let checked =
        if path == "/v1/chat/completions" { rules::chat(&body, &lim) } else { rules::completion(&body, &lim) };
    let o = match checked {
        Ok(o) => o,
        Err(r) => {
            *note = format!(" refused={}", r.code);
            return refusal(state, r);
        }
    };
    let m = &state.metrics;
    m.prompt_tokens.fetch_add(o.prompt_tokens, Relaxed);
    m.system_dropped.fetch_add(o.system_dropped, Relaxed);
    m.results_pointed.fetch_add(o.results_pointed, Relaxed);
    if o.clamped {
        m.clamped.fetch_add(1, Relaxed);
    }
    *note = format!(
        "{before} prompt~{} max={} dropped={} pointed={}{}",
        o.prompt_tokens,
        o.max_tokens,
        o.system_dropped,
        o.results_pointed,
        if o.stream { " stream" } else { "" }
    );
    forward(state, &path, o.body, permit).await
}

// --- listening ---------------------------------------------------------------------------------------------

fn home() -> Result<PathBuf, String> {
    let home = std::env::var_os("HOME").map(PathBuf::from).ok_or("HOME is not set")?;
    if !home.is_absolute() {
        return Err("HOME is not an absolute path".into());
    }
    Ok(home)
}

pub fn build_state(cfg: Serve) -> Result<(Arc<State>, Option<Arc<rustls::ServerConfig>>), String> {
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
        metrics: Metrics::default(),
        last: Mutex::new(Instant::now()),
        budget: AtomicU64::new((cfg.ctx_size / cfg.slots) as u64),
        stopped: Mutex::new(false),
        key,
        token,
        fingerprint,
        cfg,
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
    if let Some(unit) = state.cfg.llm_unit.clone() {
        if !upstream_up(&state).await {
            match run_systemctl("start", unit).await {
                Ok(true) => {
                    state.metrics.llm_starts.fetch_add(1, Relaxed);
                }
                _ => eprintln!("llmcf-gateway: the model server's unit could not be started (it is tried again)"),
            }
        }
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
    use std::pin::Pin;
    use std::task::{Context, Poll};
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
}
