//! The gateway as a program in front of a stub llama-server (a small HTTP server in this test on loopback that
//! records what reaches it): the routes, the strict checks, the savings, the limits, the logs and the TLS mode.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const KEY: &str = "stub-key-0123456789";

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
}

struct Stub {
    port: u16,
    seen: Arc<Mutex<Vec<(String, String, String)>>>,
    /// each completion request: when it reached the stub and when its answer's last byte went out
    times: Arc<Mutex<Vec<(Instant, Instant)>>>,
}

fn read_request(stream: &mut TcpStream) -> Option<(String, String, String, String)> {
    let mut reader = BufReader::new(stream.try_clone().ok()?);
    let mut line = String::new();
    reader.read_line(&mut line).ok()?;
    let mut parts = line.split_whitespace();
    let (method, path) = (parts.next()?.to_string(), parts.next()?.to_string());
    let (mut length, mut auth) = (0usize, String::new());
    loop {
        let mut h = String::new();
        reader.read_line(&mut h).ok()?;
        let h = h.trim_end();
        if h.is_empty() {
            break;
        }
        let lower = h.to_ascii_lowercase();
        if let Some(v) = lower.strip_prefix("content-length:") {
            length = v.trim().parse().unwrap_or(0);
        }
        if lower.starts_with("authorization:") {
            auth = h["authorization:".len()..].trim().to_string();
        }
    }
    let mut body = vec![0u8; length];
    reader.read_exact(&mut body).ok()?;
    Some((method, path, auth, String::from_utf8_lossy(&body).into_owned()))
}

fn stub() -> Stub {
    stub_with(Duration::ZERO, false)
}

/// A stub whose completions take `delay`; with `stream`, the answer is a server-sent event stream whose first event
/// goes out at once and whose end comes after the delay.
fn stub_with(delay: Duration, sse: bool) -> Stub {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let times = Arc::new(Mutex::new(Vec::new()));
    let (log, timelog) = (seen.clone(), times.clone());
    thread::spawn(move || {
        for conn in listener.incoming() {
            let Ok(mut conn) = conn else { continue };
            let (log, timelog) = (log.clone(), timelog.clone());
            thread::spawn(move || {
                let mut stream = conn.try_clone().unwrap();
                let Some((method, path, auth, body)) = read_request(&mut conn) else { return };
                log.lock().unwrap().push((path.clone(), auth, body));
                if method == "POST" {
                    let arrived = Instant::now();
                    if sse {
                        let first = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"O\"}}]}\n\n";
                        let _ = write!(
                            stream,
                            "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\
                             Connection: close\r\n\r\n{:x}\r\n{first}\r\n",
                            first.len()
                        );
                        let _ = stream.flush();
                        thread::sleep(delay);
                        let last = "data: [DONE]\n\n";
                        let _ = write!(stream, "{:x}\r\n{last}\r\n0\r\n\r\n", last.len());
                        timelog.lock().unwrap().push((arrived, Instant::now()));
                        return;
                    }
                    thread::sleep(delay);
                    timelog.lock().unwrap().push((arrived, Instant::now()));
                }
                let answer = match (method.as_str(), path.as_str()) {
                    ("GET", "/health") => r#"{"status":"ok"}"#.to_string(),
                    ("GET", "/props") => r#"{"default_generation_settings":{"n_ctx":2048},"total_slots":1}"#.into(),
                    ("GET", "/v1/models") => r#"{"object":"list","data":[{"id":"model.gguf"}]}"#.into(),
                    _ => r#"{"choices":[{"index":0,"message":{"role":"assistant","content":"OK"}}],"usage":{"prompt_tokens":9,"completion_tokens":2}}"#.into(),
                };
                let _ = write!(
                    stream,
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    answer.len(),
                    answer
                );
            });
        }
    });
    Stub { port, seen, times }
}

struct Gateway {
    child: Child,
    port: u16,
    home: PathBuf,
    log: Arc<Mutex<String>>,
}

impl Drop for Gateway {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.home);
    }
}

fn home(name: &str) -> PathBuf {
    let home = std::env::temp_dir().join(format!("llmcf-gw-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&home);
    std::fs::create_dir_all(home.join(".config/llm-framework")).unwrap();
    std::fs::write(home.join(".config/llm-framework/llama-server.key"), format!("{KEY}\n")).unwrap();
    home
}

fn gateway(name: &str, upstream: u16, extra: &[&str]) -> Gateway {
    let port = free_port();
    let home = home(name);
    let mut args =
        vec!["serve".to_string(), "--port".into(), port.to_string(), "--upstream-port".into(), upstream.to_string()];
    args.extend(extra.iter().map(|s| s.to_string()));
    let mut child = Command::new(env!("CARGO_BIN_EXE_llmcf-gateway"))
        .args(&args)
        .env_clear()
        .env("HOME", &home)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let log = Arc::new(Mutex::new(String::new()));
    let sink = log.clone();
    let mut err = child.stderr.take().unwrap();
    thread::spawn(move || {
        let mut buf = [0u8; 4096];
        while let Ok(n) = err.read(&mut buf) {
            if n == 0 {
                break;
            }
            sink.lock().unwrap().push_str(&String::from_utf8_lossy(&buf[..n]));
        }
    });
    let until = Instant::now() + Duration::from_secs(20);
    while TcpStream::connect(("127.0.0.1", port)).is_err() {
        assert!(Instant::now() < until, "the gateway did not start: {}", log.lock().unwrap());
        thread::sleep(Duration::from_millis(50));
    }
    Gateway { child, port, home, log }
}

/// One plain HTTP/1.1 request; (status, body).
fn http(port: u16, method: &str, path: &str, body: &str, content_type: &str) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    write!(
        s,
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
    .unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    let status = out.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0);
    let body = out.split_once("\r\n\r\n").map(|(_, b)| b.to_string()).unwrap_or_default();
    (status, body)
}

/// One request whose whole answer (headers included) comes back as text.
fn raw(port: u16, path: &str, body: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    write!(
        s,
        "POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
    .unwrap();
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    out
}

fn status_of(answer: &str) -> u16 {
    answer.split_whitespace().nth(1).and_then(|c| c.parse().ok()).unwrap_or(0)
}

fn metric(port: u16, name: &str) -> f64 {
    let (_, text) = http(port, "GET", "/metrics", "", "text/plain");
    text.lines()
        .find_map(|l| l.strip_prefix(name).and_then(|r| r.strip_prefix(' ')))
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or_else(|| panic!("no {name} in {text}"))
}

const CHAT: &str = r#"{"messages":[{"role":"user","content":"x"}]}"#;

fn post(port: u16, path: &str, body: &str) -> (u16, String) {
    http(port, "POST", path, body, "application/json")
}

#[test]
fn health_reports_the_model_server_and_the_budget_from_its_props() {
    let s = stub();
    let g = gateway("health", s.port, &["--ctx-size", "4096"]);
    let (status, body) = http(g.port, "GET", "/health", "", "text/plain");
    assert_eq!(status, 200, "{body}");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["upstream"], "up");
    assert_eq!(v["ctx_budget"], 2048);
    assert_eq!(v["tls"], false);
    assert_eq!(v["control"], "none");
    assert_eq!(v["queue"]["queued"], 0);
    assert_eq!(v["queue"]["max"], 32);
    assert_eq!(v["queue"]["wait_seconds"], 120);
}

#[test]
fn a_busy_model_queues_requests_and_a_full_queue_answers_429() {
    let s = stub_with(Duration::from_millis(900), false);
    let g = gateway("queue", s.port, &["--max-inflight", "1", "--queue-max", "1"]);
    let port = g.port;
    let first = thread::spawn(move || post(port, "/v1/chat/completions", CHAT).0);
    thread::sleep(Duration::from_millis(250));
    let second = thread::spawn(move || post(port, "/v1/chat/completions", CHAT).0);
    thread::sleep(Duration::from_millis(250));
    assert_eq!(metric(port, "llmcf_gateway_queued"), 1.0);
    let third = raw(port, "/v1/chat/completions", CHAT);
    assert_eq!(status_of(&third), 429, "{third}");
    assert!(third.contains("queue_full") && third.to_ascii_lowercase().contains("retry-after: 5"), "{third}");
    assert_eq!(first.join().unwrap(), 200);
    assert_eq!(second.join().unwrap(), 200, "the queued request is answered once the first is done");
    assert_eq!(metric(port, "llmcf_gateway_queue_full_total"), 1.0);
    assert_eq!(metric(port, "llmcf_gateway_queued_total"), 1.0);
    assert_eq!(metric(port, "llmcf_gateway_queue_wait_seconds_count"), 2.0);
    assert!(metric(port, "llmcf_gateway_queue_wait_seconds_sum") > 0.3);
    let log = g.log.lock().unwrap().clone();
    assert!(log.contains(" queued=") && (log.contains("by=account") || log.contains("by=address")), "{log}");
}

#[test]
fn a_request_that_waits_too_long_gets_503_with_retry_after() {
    let s = stub_with(Duration::from_millis(2500), false);
    let g = gateway("qwait", s.port, &["--max-inflight", "1", "--queue-wait-seconds", "1"]);
    let port = g.port;
    let first = thread::spawn(move || post(port, "/v1/chat/completions", CHAT).0);
    thread::sleep(Duration::from_millis(250));
    let started = Instant::now();
    let second = raw(port, "/v1/chat/completions", CHAT);
    assert_eq!(status_of(&second), 503, "{second}");
    assert!(second.contains("queue_timeout") && second.to_ascii_lowercase().contains("retry-after: 15"), "{second}");
    assert!(started.elapsed() >= Duration::from_millis(900) && started.elapsed() < Duration::from_secs(2));
    assert_eq!(first.join().unwrap(), 200);
    assert_eq!(metric(port, "llmcf_gateway_queue_timeouts_total"), 1.0);
    assert_eq!(s.times.lock().unwrap().len(), 1, "the request that waited too long never reached the model");
}

#[test]
fn a_client_that_hangs_up_while_it_waits_frees_its_place() {
    let s = stub_with(Duration::from_millis(1500), false);
    let g = gateway("qgone", s.port, &["--max-inflight", "1", "--queue-max", "1"]);
    let port = g.port;
    let first = thread::spawn(move || post(port, "/v1/chat/completions", CHAT).0);
    thread::sleep(Duration::from_millis(250));
    {
        let mut c = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(
            c,
            "POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{CHAT}",
            CHAT.len()
        )
        .unwrap();
        thread::sleep(Duration::from_millis(250));
        assert_eq!(metric(port, "llmcf_gateway_queued"), 1.0);
    }
    thread::sleep(Duration::from_millis(250));
    assert_eq!(metric(port, "llmcf_gateway_queued"), 0.0, "the place of the client that left is free");
    assert_eq!(metric(port, "llmcf_gateway_queue_gone_total"), 1.0);
    // the freed place takes the next request (the queue holds one)
    let next = thread::spawn(move || post(port, "/v1/chat/completions", CHAT).0);
    assert_eq!(first.join().unwrap(), 200);
    assert_eq!(next.join().unwrap(), 200);
    assert_eq!(s.times.lock().unwrap().len(), 2, "the request whose client left never reached the model");
}

#[test]
fn a_streamed_answer_holds_its_place_until_its_end() {
    let s = stub_with(Duration::from_millis(800), true);
    let g = gateway("qstream", s.port, &["--max-inflight", "1"]);
    let port = g.port;
    let body = r#"{"messages":[{"role":"user","content":"x"}],"stream":true}"#;
    let first = thread::spawn(move || raw(port, "/v1/chat/completions", body));
    thread::sleep(Duration::from_millis(200));
    let second = thread::spawn(move || raw(port, "/v1/chat/completions", body));
    let (a, b) = (first.join().unwrap(), second.join().unwrap());
    assert!(a.contains("[DONE]") && b.contains("[DONE]"), "{a}\n{b}");
    let times = s.times.lock().unwrap().clone();
    assert_eq!(times.len(), 2);
    assert!(times[1].0 >= times[0].1, "the second request reached the model only after the first stream ended");
}

#[test]
fn a_chat_request_reaches_the_model_server_rewritten_and_with_its_key() {
    let s = stub();
    let g = gateway("chat", s.port, &["--max-tokens", "64"]);
    let body = r#"{"messages":[{"role":"system","content":"Be brief."},{"role":"user","content":"zebra-violet"},
                  {"role":"system","content":"Be brief."}],"max_tokens":500}"#;
    let (status, answer) = post(g.port, "/v1/chat/completions", body);
    assert_eq!(status, 200, "{answer}");
    assert!(answer.contains("\"OK\""));
    let seen = s.seen.lock().unwrap().clone();
    let (_, auth, sent) = seen.iter().find(|(p, _, _)| p == "/v1/chat/completions").expect("the request").clone();
    assert_eq!(auth, format!("Bearer {KEY}"));
    let v: serde_json::Value = serde_json::from_str(&sent).unwrap();
    assert_eq!(v["cache_prompt"], true);
    assert_eq!(v["max_tokens"], 64);
    assert_eq!(v["messages"].as_array().unwrap().len(), 2);
    thread::sleep(Duration::from_millis(200));
    let log = g.log.lock().unwrap().clone();
    assert!(log.contains("POST /v1/chat/completions 200"), "{log}");
    assert!(!log.contains("zebra-violet"), "the log holds prompt text: {log}");
    let (_, metrics) = http(g.port, "GET", "/metrics", "", "text/plain");
    assert!(metrics.contains("llmcf_gateway_system_prompts_dropped_total 1"), "{metrics}");
    assert!(metrics.contains("llmcf_gateway_max_tokens_clamped_total 1"), "{metrics}");
}

#[test]
fn what_the_strict_checks_refuse_never_reaches_the_model_server() {
    let s = stub();
    let g = gateway("strict", s.port, &["--max-body-kb", "4"]);
    let (status, body) = post(g.port, "/v1/chat/completions", "{not json");
    assert_eq!(status, 400, "{body}");
    assert!(body.contains("invalid_json"));
    let (status, _) = post(g.port, "/v1/chat/completions", r#"{"messages":[{"role":"user","content":"x"}],"x":1}"#);
    assert_eq!(status, 400);
    let big = format!(r#"{{"messages":[{{"role":"user","content":"{}"}}]}}"#, "a".repeat(5000));
    let (status, _) = post(g.port, "/v1/chat/completions", &big);
    assert_eq!(status, 413);
    let (status, _) = http(g.port, "POST", "/v1/chat/completions", "{}", "text/plain");
    assert_eq!(status, 415);
    let (status, _) = http(g.port, "GET", "/slots", "", "text/plain");
    assert_eq!(status, 404);
    assert!(s.seen.lock().unwrap().iter().all(|(p, _, _)| p != "/v1/chat/completions"));
}

#[test]
fn the_rate_limit_answers_429_with_retry_after() {
    let s = stub();
    let g = gateway("rate", s.port, &["--rate-per-minute", "2"]);
    let body = r#"{"messages":[{"role":"user","content":"x"}]}"#;
    assert_eq!(post(g.port, "/v1/chat/completions", body).0, 200);
    assert_eq!(post(g.port, "/v1/chat/completions", body).0, 200);
    let (status, answer) = post(g.port, "/v1/chat/completions", body);
    assert_eq!(status, 429, "{answer}");
    assert!(answer.contains("rate_limited"));
}

#[test]
fn a_model_server_that_is_down_gives_503() {
    let down = free_port();
    let g = gateway("down", down, &[]);
    let (status, body) = post(g.port, "/v1/chat/completions", r#"{"messages":[{"role":"user","content":"x"}]}"#);
    assert_eq!(status, 503, "{body}");
    assert!(body.contains("upstream_down"));
    let (_, health) = http(g.port, "GET", "/health", "", "text/plain");
    assert!(health.contains("\"upstream\":\"down\""), "{health}");
}

fn probe(port: u16, extra: &[&str]) -> (i32, String) {
    let mut args = vec!["probe".to_string(), "--port".into(), port.to_string()];
    args.extend(extra.iter().map(|s| s.to_string()));
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-gateway")).args(&args).env_clear().output().unwrap();
    (out.status.code().unwrap_or(-1), String::from_utf8_lossy(&out.stdout).into_owned())
}

#[test]
fn the_probe_checks_health_one_completion_and_a_short_bench() {
    let s = stub();
    let g = gateway("probe", s.port, &[]);
    let (code, out) = probe(g.port, &["--complete", "--bench", "2"]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-gateway: health=ok upstream=up bind=loopback tls=no"), "{out}");
    assert!(out.contains("complete=ok"), "{out}");
    assert!(out.contains("bench=ok runs=2"), "{out}");
    assert!(!out.contains("OK\""), "the probe prints no answer text: {out}");
}

#[test]
fn the_lan_mode_speaks_only_tls_and_reports_its_pinned_fingerprint() {
    let s = stub();
    let g = gateway("lan", s.port, &["--bind", "lan"]);
    let dir = g.home.join(".local/state/llm-framework/gateway");
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(std::fs::metadata(dir.join("key.pem")).unwrap().permissions().mode() & 0o777, 0o600);
    assert_eq!(std::fs::metadata(dir.join("token")).unwrap().permissions().mode() & 0o777, 0o600);
    // plain HTTP to the TLS port gets no HTTP answer
    let (status, _) = http(g.port, "GET", "/health", "", "text/plain");
    assert_eq!(status, 0);
    let (code, out) = probe(g.port, &["--tls", "--complete"]);
    assert_eq!(code, 0, "{out}");
    let fp =
        out.lines().find_map(|l| l.strip_prefix("llmcf-gateway: fingerprint=")).expect("a fingerprint").to_string();
    assert_eq!(fp.len(), 64);
    assert!(out.contains("tls=yes") && out.contains("bind=lan"), "{out}");
    assert!(g.log.lock().unwrap().contains(&fp), "the start line names the certificate's fingerprint");
    // the probe without TLS gets nothing from the TLS port
    assert_ne!(probe(g.port, &[]).0, 0);
}

#[test]
fn serve_refuses_options_it_does_not_take() {
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-gateway"))
        .args(["serve", "--host", "0.0.0.0"])
        .env_clear()
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    let v = Command::new(env!("CARGO_BIN_EXE_llmcf-gateway")).arg("--version").output().unwrap();
    assert!(String::from_utf8_lossy(&v.stdout).starts_with("llmcf-gateway "));
}
