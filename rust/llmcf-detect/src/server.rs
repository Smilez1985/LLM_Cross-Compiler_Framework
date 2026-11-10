//! The engine's HTTP side, with the gateway's stack (tokio current-thread, hyper 1, HTTP/1.1), on 127.0.0.1 only:
//! the gateway checks every request first (its route table from the block's reach) and is the engine's only client.
//!
//! - `GET /health`: 503 until the model is loaded and its checks passed (the gateway's wait after a start sees real
//!   readiness), then 200 with the model, its kind and the versions;
//! - `POST /detect`: the gateway's form (`image`, a JPEG or PNG, and the optional text field `options`, strict JSON);
//! - `POST /detect/image`: the image alone (image/jpeg or image/png), with the defaults;
//! - `GET /events`: the results of the engine's source as an event stream, one event per processed frame.
//!
//! The source (step 1: a folder of still images played at a fixed rate) runs only while at least one client follows
//! its events and stops a short time after the last one left. Only the latest frame counts: the inference takes the
//! newest frame whenever it is free, older frames are dropped, never queued, and each result says how many frames were
//! dropped since the last one. Every follower shares one inference; a slow follower skips results instead of making
//! the engine keep them (a channel of one result per follower). The log holds numbers and states only.

use crate::engine::Engine;
use crate::image::{self, Rgb};
use bytes::Bytes;
use http_body_util::{combinators::UnsyncBoxBody, BodyExt, Full, Limited};
use hyper::body::{Frame, Incoming};
use hyper::header::{HeaderValue, CACHE_CONTROL, CONTENT_TYPE};
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use serde_json::{json, Value};
use std::convert::Infallible;
use std::path::{Path, PathBuf};
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};
use std::time::{Duration, Instant};
use tokio::net::TcpListener;
use tokio::sync::{mpsc, watch};

pub type Body = UnsyncBoxBody<Bytes, Infallible>;

/// The largest body the engine reads (the gateway's caps are smaller).
pub const MAX_BODY: usize = 16 * 1024 * 1024;
const BODY_WAIT: Duration = Duration::from_secs(60);
const MAX_PARTS: usize = 8;
/// A comment line on an event stream while no result comes, so the gateway's idle timeout sees a living stream.
const KEEPALIVE: Duration = Duration::from_secs(10);
/// How long the source keeps running after its last follower left.
const LINGER: Duration = Duration::from_secs(5);
pub const MAX_SOURCE_FILES: usize = 1000;
const MAX_SOURCE_FILE: u64 = 16 * 1024 * 1024;

pub struct State {
    pub engine: Arc<Mutex<Option<Engine>>>,
    pub ready: AtomicBool,
    pub failed: Mutex<Option<String>>,
    pub uploads: AtomicU64,
    pub followers: AtomicUsize,
    pub results: watch::Sender<Option<Bytes>>,
    pub source: Option<Source>,
}

/// A folder of still images (JPEG or PNG by their first bytes) played in name order at `fps`, in a loop.
pub struct Source {
    pub files: Vec<PathBuf>,
    pub fps: u32,
}

impl Source {
    /// The images of `dir`: plain files only (no links), at most MAX_SOURCE_FILES, each at most 16 MiB.
    pub fn open(dir: &Path, fps: u32) -> Result<Source, String> {
        let mut files = Vec::new();
        let entries = std::fs::read_dir(dir).map_err(|_| "the source folder cannot be read".to_string())?;
        for e in entries.flatten() {
            let p = e.path();
            let Ok(meta) = std::fs::symlink_metadata(&p) else { continue };
            if !meta.is_file() || meta.len() > MAX_SOURCE_FILE || meta.len() < 8 {
                continue;
            }
            let mut head = [0u8; 8];
            let ok = std::fs::File::open(&p).and_then(|mut f| std::io::Read::read_exact(&mut f, &mut head)).is_ok();
            if ok && image::sniff(&head).is_some() {
                files.push(p);
            }
        }
        files.sort();
        files.truncate(MAX_SOURCE_FILES);
        if files.is_empty() {
            return Err("the source folder holds no JPEG or PNG image".into());
        }
        Ok(Source { files, fps })
    }
}

fn full(status: StatusCode, ctype: &'static str, body: Bytes) -> Response<Body> {
    let mut r = Response::new(Full::new(body).boxed_unsync());
    *r.status_mut() = status;
    r.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static(ctype));
    r
}

fn json_response(status: StatusCode, v: &Value) -> Response<Body> {
    full(status, "application/json", Bytes::from(v.to_string()))
}

fn error(status: u16, code: &str, message: &str) -> Response<Body> {
    let s = StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST);
    json_response(s, &json!({"error": {"code": code, "message": message}}))
}

/// The engine's own form from the gateway (generic.rs there: declared fields only, each once): `image` and
/// `options`. Returns (image bytes, options bytes).
pub fn form(body: &Bytes, boundary: &str) -> Result<(Bytes, Option<Bytes>), (u16, &'static str, String)> {
    let bad = |m: &str| (400, "invalid_multipart", m.to_string());
    if boundary.is_empty() || boundary.len() > 200 {
        return Err(bad("the form has no boundary"));
    }
    let delim = format!("--{boundary}");
    let b = &body[..];
    if !b.starts_with(delim.as_bytes()) {
        return Err(bad("the form does not start with its boundary"));
    }
    let sep = format!("\r\n--{boundary}");
    let find = |hay: &[u8], needle: &[u8], from: usize| {
        hay[from..].windows(needle.len()).position(|w| w == needle).map(|p| p + from)
    };
    let mut pos = delim.len();
    let (mut image, mut options) = (None, None);
    let mut parts = 0;
    loop {
        if b[pos..].starts_with(b"--") {
            break;
        }
        if !b[pos..].starts_with(b"\r\n") {
            return Err(bad("a part does not start on its own line"));
        }
        parts += 1;
        if parts > MAX_PARTS {
            return Err(bad("too many parts"));
        }
        let head_start = pos + 2;
        let head_end = find(b, b"\r\n\r\n", head_start)
            .filter(|e| e - head_start <= 1024)
            .ok_or_else(|| bad("a part's head is missing or too long"))?;
        let head = std::str::from_utf8(&b[head_start..head_end]).map_err(|_| bad("a part's head is not text"))?;
        let name = head
            .split("\r\n")
            .find_map(|l| {
                let lower = l.to_ascii_lowercase();
                lower.starts_with("content-disposition:").then(|| {
                    l.split(';')
                        .find_map(|p| p.trim().strip_prefix("name=\"").and_then(|n| n.strip_suffix('"')))
                        .map(str::to_string)
                })?
            })
            .ok_or_else(|| bad("a part has no name"))?;
        let data_start = head_end + 4;
        let data_end = find(b, sep.as_bytes(), data_start).ok_or_else(|| bad("the form has no end"))?;
        let data = body.slice(data_start..data_end);
        let slot = match name.as_str() {
            "image" => &mut image,
            "options" => &mut options,
            _ => return Err((400, "unknown_field", "the form takes the fields image and options".into())),
        };
        if slot.is_some() {
            return Err(bad("a field twice"));
        }
        *slot = Some(data);
        pos = data_end + sep.len();
    }
    let image = image.ok_or((400, "missing_field", "the field image is needed".to_string()))?;
    Ok((image, options))
}

fn boundary_of(ctype: &str) -> Option<String> {
    let lower = ctype.to_ascii_lowercase();
    if !lower.trim_start().starts_with("multipart/form-data") {
        return None;
    }
    ctype.split(';').find_map(|p| {
        let p = p.trim();
        (p.len() > 9 && p[..9].eq_ignore_ascii_case("boundary=")).then(|| p[9..].trim_matches('"').to_string())
    })
}

async fn read_body(req: Request<Incoming>) -> Result<Bytes, Response<Body>> {
    let collect = Limited::new(req.into_body(), MAX_BODY).collect();
    match tokio::time::timeout(BODY_WAIT, collect).await {
        Ok(Ok(c)) => Ok(c.to_bytes()),
        Ok(Err(_)) => Err(error(413, "body_too_large", "the body is larger than the engine takes")),
        Err(_) => Err(error(408, "body_timeout", "the body did not come in time")),
    }
}

/// One posted image through the engine (decoded, inferred and written in a blocking thread: the NPU and the CPU work
/// never block the server's own thread).
async fn detect(state: &Arc<State>, image: Bytes, options: Option<Bytes>) -> Response<Body> {
    let engine = state.engine.clone();
    let seq = state.uploads.fetch_add(1, Relaxed) + 1;
    let done = tokio::task::spawn_blocking(move || {
        let img = image::decode(&image).map_err(|e| (e.status, e.code, e.message))?;
        let mut guard = engine.lock().map_err(|_| (500, "engine_error", "the engine stopped".to_string()))?;
        let eng = guard.as_mut().ok_or((503, "not_ready", "the model is not loaded yet".to_string()))?;
        let u = eng.options(options.as_deref()).map_err(|e| (400, "invalid_options", e))?;
        let out = eng.infer(&img, &u).map_err(|e| (500, "npu_error", e))?;
        Ok::<Value, (u16, &'static str, String)>(eng.result(&out, "upload", seq, 0))
    })
    .await;
    match done {
        Ok(Ok(v)) => json_response(StatusCode::OK, &v),
        Ok(Err((status, code, msg))) => error(status, code, &msg),
        Err(_) => error(500, "engine_error", "the inference stopped"),
    }
}

/// An event stream body: one event per result the follower's channel holds, a comment while none comes.
struct Events {
    rx: mpsc::Receiver<Bytes>,
}

impl hyper::body::Body for Events {
    type Data = Bytes;
    type Error = Infallible;
    fn poll_frame(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Result<Frame<Bytes>, Infallible>>> {
        self.rx.poll_recv(cx).map(|o| o.map(|b| Ok(Frame::data(b))))
    }
}

/// Counts a follower while its stream is open.
struct Follower(Arc<State>);

impl Drop for Follower {
    fn drop(&mut self) {
        self.0.followers.fetch_sub(1, Relaxed);
    }
}

fn events(state: &Arc<State>) -> Response<Body> {
    if state.source.is_none() {
        return error(404, "no_source", "the engine has no source of its own (only posted images)");
    }
    let (tx, rx) = mpsc::channel::<Bytes>(1);
    let mut results = state.results.subscribe();
    results.mark_unchanged();
    state.followers.fetch_add(1, Relaxed);
    let follower = Follower(state.clone());
    tokio::spawn(async move {
        let _counted = follower;
        if tx.send(Bytes::from_static(b": llmcf-detect events\n\n")).await.is_err() {
            return;
        }
        loop {
            tokio::select! {
                changed = results.changed() => {
                    if changed.is_err() {
                        return;
                    }
                    let Some(r) = results.borrow_and_update().clone() else { continue };
                    let mut ev = Vec::with_capacity(r.len() + 16);
                    ev.extend_from_slice(b"data: ");
                    ev.extend_from_slice(&r);
                    ev.extend_from_slice(b"\n\n");
                    // a follower that has not taken the last result skips this one
                    if let Err(mpsc::error::TrySendError::Closed(_)) = tx.try_send(Bytes::from(ev)) {
                        return;
                    }
                }
                _ = tokio::time::sleep(KEEPALIVE) => {
                    if let Err(mpsc::error::TrySendError::Closed(_)) = tx.try_send(Bytes::from_static(b": keepalive\n\n")) {
                        return;
                    }
                }
            }
        }
    });
    let mut r = Response::new(Events { rx }.boxed_unsync());
    r.headers_mut().insert(CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
    r.headers_mut().insert(CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    r
}

/// The source's loop: while followed, the newest frame by the clock (frame k is due at start + k / fps) is inferred
/// whenever the engine is free; the frames that came in the meantime are counted as dropped.
pub async fn run_source(state: Arc<State>) {
    let Some(src) = state.source.as_ref() else { return };
    let period = Duration::from_secs_f64(1.0 / src.fps as f64);
    let files: Arc<Vec<PathBuf>> = Arc::new(src.files.clone());
    let mut seq: u64 = 0;
    loop {
        // wait for a follower
        while state.followers.load(Relaxed) == 0 || !state.ready.load(Relaxed) {
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        eprintln!("llmcf-detect: source started ({} images at {} frames a second)", files.len(), src.fps);
        let start = Instant::now();
        let mut last: Option<u64> = None;
        let mut alone_since: Option<Instant> = None;
        loop {
            if state.followers.load(Relaxed) == 0 {
                let since = *alone_since.get_or_insert_with(Instant::now);
                if since.elapsed() >= LINGER {
                    break;
                }
            } else {
                alone_since = None;
            }
            let due = (start.elapsed().as_secs_f64() * src.fps as f64).floor() as u64;
            if last == Some(due) {
                let next = start + period.mul_f64((due + 1) as f64);
                tokio::time::sleep_until(next.into()).await;
                continue;
            }
            let dropped = last.map_or(0, |l| due.saturating_sub(l + 1));
            last = Some(due);
            seq += 1;
            let file = files[(due % files.len() as u64) as usize].clone();
            let engine = state.engine.clone();
            let this = seq;
            let done = tokio::task::spawn_blocking(move || -> Result<Bytes, String> {
                let data = std::fs::read(&file).map_err(|_| "a source image cannot be read".to_string())?;
                let img: Rgb = image::decode(&data).map_err(|e| e.message)?;
                let mut guard = engine.lock().map_err(|_| "the engine stopped".to_string())?;
                let eng = guard.as_mut().ok_or("the model is not loaded")?;
                let u = eng.defaults.clone();
                let out = eng.infer(&img, &u)?;
                Ok(Bytes::from(eng.result(&out, "files", this, dropped).to_string()))
            })
            .await;
            match done {
                Ok(Ok(b)) => {
                    let _ = state.results.send(Some(b));
                }
                Ok(Err(e)) => {
                    eprintln!("llmcf-detect: a source frame failed: {}", e.chars().take(200).collect::<String>())
                }
                Err(_) => eprintln!("llmcf-detect: a source frame stopped"),
            }
        }
        eprintln!("llmcf-detect: source stopped (no follower)");
    }
}

async fn handle(state: Arc<State>, req: Request<Incoming>) -> Response<Body> {
    let started = Instant::now();
    let method = req.method().clone();
    let path = req.uri().path().to_string();
    let resp = route(&state, req).await;
    let shown = if ["/health", "/detect", "/detect/image", "/events"].contains(&path.as_str()) {
        path.as_str()
    } else {
        "other"
    };
    if shown != "/health" {
        eprintln!("llmcf-detect: {method} {shown} {} {} ms", resp.status().as_u16(), started.elapsed().as_millis());
    }
    resp
}

async fn route(state: &Arc<State>, req: Request<Incoming>) -> Response<Body> {
    match (req.method(), req.uri().path()) {
        (&Method::GET, "/health") => {
            if let Some(why) = state.failed.lock().ok().and_then(|f| f.clone()) {
                return json_response(StatusCode::SERVICE_UNAVAILABLE, &json!({"status": "failed", "reason": why}));
            }
            if !state.ready.load(Relaxed) {
                return json_response(StatusCode::SERVICE_UNAVAILABLE, &json!({"status": "loading"}));
            }
            let mut v = state.engine.lock().ok().and_then(|g| g.as_ref().map(Engine::health)).unwrap_or(json!({}));
            v["source"] = json!(if state.source.is_some() { "files" } else { "none" });
            v["followers"] = json!(state.followers.load(Relaxed));
            json_response(StatusCode::OK, &v)
        }
        (&Method::POST, "/detect") => {
            let ctype = req.headers().get(CONTENT_TYPE).and_then(|v| v.to_str().ok()).unwrap_or("").to_string();
            let Some(boundary) = boundary_of(&ctype) else {
                return error(415, "unsupported_type", "the body is no multipart form");
            };
            let body = match read_body(req).await {
                Ok(b) => b,
                Err(r) => return r,
            };
            match form(&body, &boundary) {
                Ok((img, options)) => detect(state, img, options).await,
                Err((s, c, m)) => error(s, c, &m),
            }
        }
        (&Method::POST, "/detect/image") => {
            let body = match read_body(req).await {
                Ok(b) => b,
                Err(r) => return r,
            };
            detect(state, body, None).await
        }
        (&Method::GET, "/events") => events(state),
        (_, "/health" | "/detect" | "/detect/image" | "/events") => error(405, "method_not_allowed", "not this method"),
        _ => error(404, "not_found", "no such path"),
    }
}

/// Serves on 127.0.0.1:`port` (bound before the model is loaded, so /health answers 503 meanwhile).
pub async fn serve(state: Arc<State>, listener: TcpListener) -> Result<(), String> {
    if state.source.is_some() {
        tokio::spawn(run_source(state.clone()));
    }
    loop {
        let Ok((stream, _)) = listener.accept().await else {
            tokio::time::sleep(Duration::from_millis(100)).await;
            continue;
        };
        let state = state.clone();
        tokio::spawn(async move {
            let svc = hyper::service::service_fn(move |req| {
                let state = state.clone();
                async move { Ok::<_, Infallible>(handle(state, req).await) }
            });
            let conn = hyper::server::conn::http1::Builder::new()
                .max_buf_size(64 * 1024)
                .header_read_timeout(Duration::from_secs(20))
                .timer(hyper_util::rt::TokioTimer::new())
                .to_owned();
            let _ = conn.serve_connection(TokioIo::new(stream), svc).await;
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const B: &str = "llmcf-boundary";

    fn part(name: &str, file: bool, data: &[u8]) -> Vec<u8> {
        let mut v = format!("--{B}\r\nContent-Disposition: form-data; name=\"{name}\"").into_bytes();
        if file {
            v.extend_from_slice(format!("; filename=\"{name}.jpeg\"\r\nContent-Type: image/jpeg").as_bytes());
        }
        v.extend_from_slice(b"\r\n\r\n");
        v.extend_from_slice(data);
        v.extend_from_slice(b"\r\n");
        v
    }

    fn body(parts: &[Vec<u8>]) -> Bytes {
        let mut v: Vec<u8> = parts.concat();
        v.extend_from_slice(format!("--{B}--\r\n").as_bytes());
        Bytes::from(v)
    }

    #[test]
    fn the_gateways_form_is_read_and_nothing_else() {
        let (img, opt) =
            form(&body(&[part("image", true, b"\xFF\xD8\xFFdata\r\n--not"), part("options", false, b"{}")]), B)
                .unwrap();
        assert_eq!(&img[..], b"\xFF\xD8\xFFdata\r\n--not");
        assert_eq!(opt.as_deref(), Some(&b"{}"[..]));
        let (img, opt) = form(&body(&[part("image", true, b"x")]), B).unwrap();
        assert_eq!((&img[..], opt), (&b"x"[..], None));
        assert_eq!(form(&body(&[part("options", false, b"{}")]), B).unwrap_err().1, "missing_field");
        assert_eq!(
            form(&body(&[part("image", true, b"x"), part("image", true, b"y")]), B).unwrap_err().1,
            "invalid_multipart"
        );
        assert_eq!(form(&body(&[part("other", false, b"x")]), B).unwrap_err().1, "unknown_field");
        assert_eq!(form(&Bytes::from_static(b"--llmcf-boundary\r\nX"), B).unwrap_err().1, "invalid_multipart");
        assert_eq!(boundary_of("multipart/form-data; boundary=\"abc\""), Some("abc".into()));
        assert_eq!(boundary_of("application/json"), None);
    }
}
