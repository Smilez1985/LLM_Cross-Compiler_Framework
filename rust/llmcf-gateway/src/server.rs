//! The HTTP front: OpenAI-compatible routes in front of llama-server on loopback, plus /health and /metrics.
//!
//! Loopback mode listens on 127.0.0.1 with plain HTTP; the local-network mode listens on every address with TLS 1.3
//! only (never plain HTTP off loopback), and a client that is not on the board itself must send the gateway's
//! bearer token. Every request is size-capped, rate-limited per client address and counted against a small number
//! of requests in flight; the log line of a request holds its route, status, time and token counts, never a prompt.

use crate::args::{Bind, Serve};
use crate::control;
use crate::limits::RateLimit;
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
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Semaphore;

pub type Body = UnsyncBoxBody<Bytes, hyper::Error>;

/// The runtime key of llama-server below the home (launcher.KEY_REL).
pub const KEY_REL: &str = ".config/llm-framework/llama-server.key";
const START_WAIT: Duration = Duration::from_secs(120);
const ANSWER_WAIT: Duration = Duration::from_secs(900);

#[derive(Default)]
pub struct Metrics {
    pub requests: AtomicU64,
    pub ok: AtomicU64,
    pub refused: AtomicU64,
    pub too_large: AtomicU64,
    pub rate_limited: AtomicU64,
    pub busy: AtomicU64,
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
    pub inflight: Arc<Semaphore>,
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
        let busy = state.inflight.available_permits() < state.cfg.max_inflight as usize;
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
        }),
    )
}

fn metrics(state: &State) -> Response<Body> {
    let m = &state.metrics;
    let inflight =
        state.cfg.max_inflight as usize - state.inflight.available_permits().min(state.cfg.max_inflight as usize);
    let rows: [(&str, &str, u64); 14] = [
        ("llmcf_gateway_requests_total", "requests received", m.requests.load(Relaxed)),
        ("llmcf_gateway_answered_total", "requests the model server answered", m.ok.load(Relaxed)),
        ("llmcf_gateway_refused_total", "requests refused by the strict checks", m.refused.load(Relaxed)),
        ("llmcf_gateway_too_large_total", "bodies over the size cap", m.too_large.load(Relaxed)),
        ("llmcf_gateway_rate_limited_total", "requests over the rate limit", m.rate_limited.load(Relaxed)),
        ("llmcf_gateway_busy_total", "requests over the in-flight limit", m.busy.load(Relaxed)),
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
    text.push_str(&format!(
        "# HELP llmcf_gateway_inflight requests in flight\n# TYPE llmcf_gateway_inflight gauge\nllmcf_gateway_inflight {inflight}\n"
    ));
    let mut r = Response::new(full(text));
    r.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static("text/plain; version=0.0.4"));
    r
}

fn refusal(state: &State, r: Refusal) -> Response<Body> {
    state.metrics.refused.fetch_add(1, Relaxed);
    error(StatusCode::from_u16(r.status).unwrap_or(StatusCode::BAD_REQUEST), r.code, &r.message)
}

async fn forward(state: &State, path: &str, out: Value, permit: tokio::sync::OwnedSemaphorePermit) -> Response<Body> {
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

async fn handle(state: Arc<State>, peer: SocketAddr, req: Request<Incoming>) -> Response<Body> {
    let started = Instant::now();
    let method = req.method().clone();
    let path = req.uri().path().to_string();
    let mut note = String::new();
    let resp = route(&state, peer, req, &mut note).await;
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

async fn route(state: &Arc<State>, peer: SocketAddr, req: Request<Incoming>, note: &mut String) -> Response<Body> {
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
    let Ok(permit) = state.inflight.clone().try_acquire_owned() else {
        state.metrics.busy.fetch_add(1, Relaxed);
        return retry(error(StatusCode::TOO_MANY_REQUESTS, "busy", "the model is busy with other requests"), 2);
    };
    let cap = state.cfg.max_body_kb as usize * 1024;
    let body = match Limited::new(req.into_body(), cap).collect().await {
        Ok(b) => b.to_bytes(),
        Err(_) => {
            state.metrics.too_large.fetch_add(1, Relaxed);
            return error(StatusCode::PAYLOAD_TOO_LARGE, "too_large", "the body is larger than the gateway takes");
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
        " prompt~{} max={} dropped={} pointed={}{}",
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
    let state = Arc::new(State {
        rate: Mutex::new(RateLimit::new(cfg.rate_per_minute)),
        inflight: Arc::new(Semaphore::new(cfg.max_inflight as usize)),
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
    let acceptor = tls_cfg.map(tokio_rustls::TlsAcceptor::from);
    loop {
        let Ok((stream, peer)) = listener.accept().await else {
            tokio::time::sleep(Duration::from_millis(100)).await;
            continue;
        };
        let state = state.clone();
        let acceptor = acceptor.clone();
        tokio::spawn(async move {
            let svc = hyper::service::service_fn(move |req| {
                let state = state.clone();
                async move { Ok::<_, std::convert::Infallible>(handle(state, peer, req).await) }
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
