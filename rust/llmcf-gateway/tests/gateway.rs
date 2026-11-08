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

#[test]
fn while_memory_is_low_new_requests_wait_and_are_not_sent_on() {
    // a floor no board reaches: the guard holds every request back until its wait ends
    let s = stub();
    let g = gateway("qmem", s.port, &["--memory-floor-mb", "1048576", "--queue-wait-seconds", "1"]);
    let (status, body) = post(g.port, "/v1/chat/completions", CHAT);
    assert_eq!(status, 503, "{body}");
    assert!(body.contains("queue_timeout"), "{body}");
    assert!(s.times.lock().unwrap().is_empty(), "nothing reached the model");
    let (_, health) = http(g.port, "GET", "/health", "", "text/plain");
    let v: serde_json::Value = serde_json::from_str(&health).unwrap();
    assert_eq!(v["memory"]["low"], true, "{health}");
    assert_eq!(v["memory"]["floor_mib"], 1048576);
    assert_eq!(metric(g.port, "llmcf_gateway_memory_low"), 1.0);
    assert!(metric(g.port, "llmcf_gateway_admission_held_total") >= 1.0);
    let log = g.log.lock().unwrap().clone();
    assert!(log.contains("memory low (MemAvailable ") && log.contains("new requests wait"), "{log}");
    // the guard off: the same request goes through
    let g = gateway("qmem0", s.port, &["--memory-floor-mb", "0", "--memory-psi-max", "0"]);
    assert_eq!(post(g.port, "/v1/chat/completions", CHAT).0, 200);
}

#[test]
fn health_and_metrics_list_the_engines_and_a_running_llm_takes_requests() {
    // the llm stub answers already (as one started by its unit at boot would): the gateway takes it as running
    // without asking systemctl to start anything; the speech engine's port answers nothing, so it is stopped
    let s = stub();
    let stt_port = free_port();
    let g = gateway(
        "engines",
        s.port,
        &[
            "--engine",
            "llm=llmcf-llm.service:900",
            "--engine",
            &format!("stt=llmcf-stt.service:300:{stt_port}"),
            "--engines",
            "in-turn",
            "--engines-budget-mb",
            "1000",
        ],
    );
    let (status, _) = post(g.port, "/v1/chat/completions", CHAT);
    assert_eq!(status, 200);
    let (_, body) = http(g.port, "GET", "/health", "", "text/plain");
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["control"], "systemd");
    assert_eq!(v["engines"]["mode"], "in-turn");
    assert_eq!(v["engines"]["budget_mib"], 1000);
    assert_eq!(v["engines"]["list"][0]["name"], "llm");
    assert_eq!(v["engines"]["list"][0]["state"], "running", "{body}");
    assert_eq!(v["engines"]["list"][1]["name"], "stt");
    assert_eq!(v["engines"]["list"][1]["state"], "stopped");
    assert_eq!(v["engines"]["list"][1]["mib"], 300);
    assert_eq!(metric(g.port, "llmcf_gateway_engine_switches_total"), 0.0);
    assert_eq!(metric(g.port, "llmcf_gateway_engine_switch_wait_seconds_total"), 0.0);
    assert_eq!(metric(g.port, "llmcf_gateway_engine_running{engine=\"llm\"}"), 1.0);
    assert_eq!(metric(g.port, "llmcf_gateway_engine_running{engine=\"stt\"}"), 0.0);
    let log = g.log.lock().unwrap().clone();
    assert!(log.contains("engines llm=900MiB,stt=300MiB (in-turn), 1000 MiB together"), "{log}");
}

/// What a recording stub saw of one request: path, Content-Type, Authorization and the body's bytes.
type Seen = Arc<Mutex<Vec<(String, String, String, Vec<u8>)>>>;

/// A stub engine that records every request whole (binary bodies too) and answers `answer` with `ctype`.
fn recorder(answer: &'static str, ctype: &'static str) -> (u16, Seen) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen: Seen = Arc::new(Mutex::new(Vec::new()));
    let log = seen.clone();
    thread::spawn(move || {
        for conn in listener.incoming() {
            let Ok(mut conn) = conn else { continue };
            let log = log.clone();
            thread::spawn(move || {
                let mut reader = BufReader::new(conn.try_clone().unwrap());
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() {
                    return;
                }
                let path = line.split_whitespace().nth(1).unwrap_or("").to_string();
                let (mut length, mut ct, mut auth) = (0usize, String::new(), String::new());
                loop {
                    let mut h = String::new();
                    if reader.read_line(&mut h).is_err() {
                        return;
                    }
                    let h = h.trim_end();
                    if h.is_empty() {
                        break;
                    }
                    let (k, v) = h.split_once(':').unwrap_or((h, ""));
                    match k.to_ascii_lowercase().as_str() {
                        "content-length" => length = v.trim().parse().unwrap_or(0),
                        "content-type" => ct = v.trim().to_string(),
                        "authorization" => auth = v.trim().to_string(),
                        _ => {}
                    }
                }
                let mut body = vec![0u8; length];
                if reader.read_exact(&mut body).is_err() {
                    return;
                }
                let health = path == "/health";
                if !health {
                    log.lock().unwrap().push((path, ct, auth, body));
                }
                let answer = if health { r#"{"status":"ok"}"# } else { answer };
                let _ = write!(
                    conn,
                    "HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{answer}",
                    answer.len()
                );
            });
        }
    });
    (port, seen)
}

const BOUNDARY: &str = "test-boundary-31415";

fn upload(parts: &[(&str, Option<&str>, &[u8])]) -> Vec<u8> {
    let mut out = Vec::new();
    for (name, filename, data) in parts {
        out.extend_from_slice(format!("--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"").as_bytes());
        if let Some(f) = filename {
            out.extend_from_slice(format!("; filename=\"{f}\"\r\nContent-Type: application/octet-stream").as_bytes());
        }
        out.extend_from_slice(b"\r\n\r\n");
        out.extend_from_slice(data);
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(format!("--{BOUNDARY}--\r\n").as_bytes());
    out
}

/// One request with a binary body; (status, headers and body as text).
fn http_bytes(port: u16, path: &str, content_type: &str, body: &[u8]) -> (u16, String) {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(20))).unwrap();
    write!(
        s,
        "POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    )
    .unwrap();
    let _ = s.write_all(body);
    let mut out = String::new();
    let _ = s.read_to_string(&mut out);
    (status_of(&out), out)
}

fn wav(len: usize) -> Vec<u8> {
    let mut v = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00".to_vec();
    v.resize(len, 0x11);
    v
}

#[test]
fn an_audio_upload_reaches_whisper_as_its_own_form() {
    let s = stub();
    let (stt, seen) = recorder("Hallo Welt\n", "text/html; charset=utf-8");
    let g = gateway("stt", s.port, &["--stt-port", &stt.to_string(), "--max-audio-mb", "1"]);
    let audio = wav(70_000);
    let body = upload(&[
        ("file", Some("../secret name.wav"), &audio),
        ("model", None, b"whisper-1"),
        ("language", None, b"de"),
        ("prompt", None, b"Ein Gruss"),
        ("response_format", None, b"text"),
        ("temperature", None, b"0"),
    ]);
    let ctype = format!("multipart/form-data; boundary={BOUNDARY}");
    let (status, answer) = http_bytes(g.port, "/v1/audio/transcriptions", &ctype, &body);
    assert_eq!(status, 200, "{answer}");
    assert!(answer.to_ascii_lowercase().contains("content-type: text/plain; charset=utf-8"), "{answer}");
    assert!(answer.contains("\r\nHallo Welt\n\r\n"), "{answer}");
    let got = seen.lock().unwrap().clone();
    assert_eq!(got.len(), 1);
    let (path, ct, auth, sent) = &got[0];
    assert_eq!(path, "/inference", "whisper-server's own path");
    assert_eq!(ct, &ctype);
    assert!(auth.is_empty(), "the model server's key never goes to whisper");
    let text = String::from_utf8_lossy(sent);
    for field in [
        "name=\"response_format\"\r\n\r\ntext\r\n",
        "name=\"language\"\r\n\r\nde\r\n",
        "name=\"prompt\"\r\n\r\nEin Gruss\r\n",
        "name=\"temperature\"\r\n\r\n0\r\n",
        "name=\"file\"; filename=\"audio.wav\"",
    ] {
        assert!(text.contains(field), "{field} in {text:.400}");
    }
    assert!(!text.contains("secret") && !text.contains("whisper-1"), "neither the client's file name nor its model");
    let at = sent.windows(audio.len()).position(|w| w == audio.as_slice());
    assert!(at.is_some(), "the audio arrives byte for byte");
    // the log has the route, the type and the size, never the prompt or the transcript
    let log = g.log.lock().unwrap().clone();
    assert!(log.contains("POST /v1/audio/transcriptions 200") && log.contains("audio=wav"), "{log}");
    assert!(!log.contains("Gruss") && !log.contains("Hallo"), "{log}");
    assert_eq!(metric(g.port, "llmcf_gateway_route_requests_total{route=\"/v1/audio/transcriptions\"}"), 1.0);
    assert_eq!(
        metric(g.port, "llmcf_gateway_route_bytes_in_total{route=\"/v1/audio/transcriptions\"}"),
        body.len() as f64
    );
}

#[test]
fn audio_the_gateway_does_not_take_never_reaches_whisper() {
    let s = stub();
    let (stt, seen) = recorder(r#"{"text":"x"}"#, "application/json");
    let g = gateway("stt-no", s.port, &["--stt-port", &stt.to_string(), "--max-audio-mb", "1"]);
    let ctype = format!("multipart/form-data; boundary={BOUNDARY}");
    // too large by its Content-Length: refused before it is read
    let (status, _) = http_bytes(g.port, "/v1/audio/transcriptions", &ctype, &vec![b'x'; (1 << 20) + 1]);
    assert_eq!(status, 413);
    let cases: Vec<(String, Vec<u8>, u16)> = vec![
        ("application/json".into(), b"{}".to_vec(), 415),
        (ctype.clone(), upload(&[("model", None, b"whisper-1")]), 400),
        (ctype.clone(), upload(&[("file", Some("a.webm"), b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81")]), 415),
        (ctype.clone(), upload(&[("file", Some("a.wav"), &wav(100)), ("x", None, b"1")]), 400),
        (ctype.clone(), b"not a form".to_vec(), 400),
    ];
    for (ct, body, want) in cases {
        let (status, answer) = http_bytes(g.port, "/v1/audio/transcriptions", &ct, &body);
        assert_eq!(status, want, "{answer}");
    }
    assert!(seen.lock().unwrap().is_empty());
    assert_eq!(metric(g.port, "llmcf_gateway_route_refused_total{route=\"/v1/audio/transcriptions\"}"), 6.0);
    // a gateway without a speech engine says so
    let g2 = gateway("stt-none", s.port, &[]);
    let (status, answer) = http_bytes(g2.port, "/v1/audio/transcriptions", &ctype, &upload(&[]));
    assert_eq!(status, 501, "{answer}");
    assert!(answer.contains("stt-whisper-cpp"), "{answer}");
}

fn png_url(size: usize) -> String {
    const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut data = b"\x89PNG\r\n\x1a\n".to_vec();
    data.resize(size, 3);
    let mut out = String::from("data:image/png;base64,");
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        for i in 0..4 {
            out.push(if i <= c.len() { A[(n >> (18 - 6 * i) & 63) as usize] as char } else { '=' });
        }
    }
    out
}

#[test]
fn a_chat_with_an_image_goes_to_the_vision_engine_unchanged() {
    let s = stub();
    let answer = r#"{"choices":[{"index":0,"message":{"role":"assistant","content":"a cat"}}]}"#;
    let (vision, seen) = recorder(answer, "application/json");
    let g = gateway("vision", s.port, &["--vision-port", &vision.to_string(), "--vision-ctx-size", "8192"]);
    let image = serde_json::json!({"type": "image_url", "image_url": {"url": png_url(300_000), "detail": "auto"}});
    let body = serde_json::json!({"messages": [{"role": "user", "content": [
        {"type": "text", "text": "What is it?"}, image.clone()]}]});
    let text = body.to_string();
    assert!(text.len() > 256 * 1024, "larger than the JSON cap alone");
    let (status, out) = post(g.port, "/v1/chat/completions", &text);
    assert_eq!(status, 200, "{out}");
    assert!(out.contains("a cat"));
    let got = seen.lock().unwrap().clone();
    assert_eq!(got.len(), 1);
    let (path, _, auth, sent) = &got[0];
    assert_eq!(path, "/v1/chat/completions");
    assert_eq!(auth, &format!("Bearer {KEY}"), "the vision engine is a llama-server with the runtime's key");
    let v: serde_json::Value = serde_json::from_slice(sent).unwrap();
    assert_eq!(v["messages"][0]["content"][1], image, "the image part arrives unchanged");
    assert!(v.get("id_slot").is_none());
    // a text chat still goes to the llm
    assert_eq!(post(g.port, "/v1/chat/completions", CHAT).0, 200);
    assert_eq!(seen.lock().unwrap().len(), 1);
    assert_eq!(s.seen.lock().unwrap().iter().filter(|r| r.0 == "/v1/chat/completions").count(), 1);
    assert_eq!(metric(g.port, "llmcf_gateway_images_total"), 1.0);
    // a remote image is never fetched: refused before any engine
    let remote = serde_json::json!({"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://example.invalid/cat.png"}}]}]});
    let (status, out) = post(g.port, "/v1/chat/completions", &remote.to_string());
    assert_eq!(status, 400);
    assert!(out.contains("remote_image"), "{out}");
    assert_eq!(seen.lock().unwrap().len(), 1);
    let (_, health) = http(g.port, "GET", "/health", "", "text/plain");
    let h: serde_json::Value = serde_json::from_str(&health).unwrap();
    assert_eq!(h["routes"][1]["with_images"], "vision");
    assert_eq!(h["routes"][1]["vision_port"], vision);
    // without a vision engine an image part is refused
    let g2 = gateway("novision", s.port, &[]);
    let small = serde_json::json!({"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": png_url(100)}}]}]});
    let (status, out) = post(g2.port, "/v1/chat/completions", &small.to_string());
    assert_eq!(status, 400);
    assert!(out.contains("unsupported_content"), "{out}");
}

/// The route table of a bundle (`--routes`): an engine of a kind of its own, reached only through what its block
/// declares (a form in, JSON out, a binary body, a JSON route, an event stream, embeddings), next to the llm.
#[test]
fn a_route_table_reaches_a_new_engine_through_the_generic_adapters() {
    let s = stub();
    let (detect, seen) = recorder(r#"{"boxes":[{"label":"cat","score":0.9}]}"#, "application/json");
    let events = stub_with(Duration::from_millis(200), true);
    let stall = stub_with(Duration::from_secs(4), true);
    let table = format!(
        r#"{{"schema":"llmcf-gateway-routes/1",
  "engines":[{{"id":"llm","port":{llm},"key":true,"ctx_size":4096}},{{"id":"detect","port":{detect},"key":false}},
             {{"id":"events","port":{events},"key":false}},{{"id":"stall","port":{stall},"key":false}}],
  "routes":[
   {{"path":"/v1/chat/completions","method":"POST","protocol":"openai-chat","engine":"llm","upstream_path":"/v1/chat/completions","max_body_kb":256,"timeout_s":900,"stream":true,"key":"chat"}},
   {{"path":"/api/detect","method":"POST","protocol":"http-multipart","engine":"detect","upstream_path":"/detect","max_body_kb":64,"timeout_s":30,"stream":false,"key":"detect",
     "fields":[{{"name":"image","file":true,"types":["jpeg","png"],"required":true}},{{"name":"threshold"}}]}},
   {{"path":"/api/detect/raw","method":"POST","protocol":"http-binary","engine":"detect","upstream_path":"/raw","max_body_kb":64,"timeout_s":30,"stream":false,"key":"raw","types":["jpeg"]}},
   {{"path":"/api/detect/config","method":"POST","protocol":"http-json","engine":"detect","upstream_path":"/config","max_body_kb":4,"timeout_s":30,"stream":false,"key":"config"}},
   {{"path":"/v1/embeddings","method":"POST","protocol":"openai-embeddings","engine":"detect","upstream_path":"/embed","max_body_kb":16,"timeout_s":30,"stream":false,"key":"embeddings"}},
   {{"path":"/api/events","method":"POST","protocol":"http-sse","engine":"events","upstream_path":"/v1/chat/completions","max_body_kb":4,"timeout_s":10,"stream":true,"key":"events"}},
   {{"path":"/api/events/plain","method":"POST","protocol":"http-json","engine":"events","upstream_path":"/v1/chat/completions","max_body_kb":4,"timeout_s":10,"stream":false,"key":"plain"}},
   {{"path":"/api/stall","method":"POST","protocol":"http-sse","engine":"stall","upstream_path":"/v1/chat/completions","max_body_kb":4,"timeout_s":1,"stream":true,"key":"stall"}}]}}"#,
        llm = s.port,
        events = events.port,
        stall = stall.port
    );
    let dir = home("routes-file");
    let file = dir.join("routes.json");
    std::fs::write(&file, table).unwrap();
    let g = gateway("routes", s.port, &["--routes", file.to_str().unwrap()]);
    // the llm keeps its route and its key
    assert_eq!(post(g.port, "/v1/chat/completions", CHAT).0, 200);
    assert_eq!(s.seen.lock().unwrap().last().unwrap().1, format!("Bearer {KEY}"));
    // a form in: the declared fields only, the file by its bytes, under a fixed name; JSON out as the engine wrote it
    let jpeg = b"\xFF\xD8\xFF\xE0\0\x10JFIF\0image-bytes";
    let form = upload(&[("image", Some("../holiday photo.jpg"), jpeg), ("threshold", None, b"0.5")]);
    let ctype = format!("multipart/form-data; boundary={BOUNDARY}");
    let (status, answer) = http_bytes(g.port, "/api/detect", &ctype, &form);
    assert_eq!(status, 200, "{answer}");
    assert!(answer.contains(r#"{"boxes":[{"label":"cat""#) && answer.contains("application/json"));
    {
        let seen = seen.lock().unwrap();
        let (path, ct, auth, body) = seen.last().unwrap();
        let text = String::from_utf8_lossy(body);
        assert_eq!((path.as_str(), auth.as_str()), ("/detect", ""), "the runtime's key never goes to another engine");
        assert!(ct.starts_with("multipart/form-data; boundary="));
        assert!(text.contains("filename=\"image.jpeg\"") && text.contains("Content-Type: image/jpeg"));
        assert!(!text.contains("holiday"), "never the client's file name");
    }
    // what the route does not declare never reaches the engine
    let before = seen.lock().unwrap().len();
    let gif = upload(&[("image", Some("a.gif"), b"GIF89a-not-allowed")]);
    assert_eq!(http_bytes(g.port, "/api/detect", &ctype, &gif).0, 415);
    let extra = upload(&[("image", Some("a.jpg"), jpeg), ("secret", None, b"x")]);
    assert_eq!(http_bytes(g.port, "/api/detect", &ctype, &extra).0, 400);
    assert_eq!(http_bytes(g.port, "/api/detect/raw", "application/octet-stream", b"%PDF-1.7").0, 415);
    assert_eq!(post(g.port, "/api/detect/config", "[1,2]").0, 400);
    assert_eq!(post(g.port, "/api/detect/config", &format!("{{\"x\":\"{}\"}}", "a".repeat(5000))).0, 413);
    assert_eq!(post(g.port, "/v1/embeddings", r#"{"input":"x","extra":1}"#).0, 400);
    assert_eq!(http(g.port, "GET", "/api/detect", "", "application/json").0, 404, "a method it does not declare");
    assert_eq!(seen.lock().unwrap().len(), before);
    // a binary body by its magic type, with that type's media type; a JSON route; embeddings
    let (status, _) = http_bytes(g.port, "/api/detect/raw", "text/plain", jpeg);
    assert_eq!(status, 200);
    assert_eq!(seen.lock().unwrap().last().unwrap().1, "image/jpeg");
    assert_eq!(post(g.port, "/api/detect/config", r#"{"labels":["cat"]}"#).0, 200);
    assert_eq!(seen.lock().unwrap().last().unwrap().3, br#"{"labels":["cat"]}"#.to_vec());
    assert_eq!(post(g.port, "/v1/embeddings", r#"{"input":["a","b"]}"#).0, 200);
    assert_eq!(seen.lock().unwrap().last().unwrap().0, "/embed");
    // an event stream passes through on its stream route, is refused on a route that declares none
    let stream = raw(g.port, "/api/events", r#"{"go":true}"#);
    assert_eq!(status_of(&stream), 200);
    assert!(stream.contains("text/event-stream") && stream.contains("data: [DONE]"), "{stream}");
    let plain = raw(g.port, "/api/events/plain", r#"{"go":true}"#);
    assert_eq!(status_of(&plain), 502, "{plain}");
    // an engine silent for longer than its route's timeout: the stream ends there, its place is free again
    let cut = raw(g.port, "/api/stall", r#"{"go":true}"#);
    assert!(cut.contains("\"O\"") && !cut.contains("[DONE]"), "{cut}");
    // counted per route, never the content
    assert_eq!(metric(g.port, "llmcf_gateway_route_requests_total{route=\"/api/detect\"}"), 3.0);
    assert_eq!(metric(g.port, "llmcf_gateway_route_refused_total{route=\"/api/detect\"}"), 2.0);
    assert_eq!(metric(g.port, "llmcf_gateway_route_bytes_in_total{route=\"/api/detect\"}"), {
        (form.len() + gif.len() + extra.len()) as f64
    });
    assert!(metric(g.port, "llmcf_gateway_generic_total") >= 5.0);
    let (_, health) = http(g.port, "GET", "/health", "", "text/plain");
    let v: serde_json::Value = serde_json::from_str(&health).unwrap();
    let detect_route = &v["routes"][1];
    assert_eq!(detect_route["protocol"], "http-multipart");
    assert_eq!(
        (detect_route["name"].as_str(), detect_route["port"].as_u64()),
        (Some("detect.detect"), Some(detect as u64))
    );
    let log = g.log.lock().unwrap().clone();
    assert!(!log.contains("holiday") && !log.contains("image-bytes") && !log.contains("labels"), "{log}");
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_route_table_that_breaks_a_rule_stops_the_start() {
    let dir = home("routes-bad");
    let file = dir.join("routes.json");
    std::fs::write(&file, r#"{"schema":"llmcf-gateway-routes/1","engines":[],"routes":[]}"#).unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-gateway"))
        .args(["serve", "--port", &free_port().to_string(), "--routes", file.to_str().unwrap()])
        .env_clear()
        .env("HOME", &dir)
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("the route table (--routes)"));
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-gateway"))
        .args(["serve", "--routes", "/nowhere/routes.json", "--stt-port", "8090"])
        .env_clear()
        .env("HOME", &dir)
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2), "the speech options are the route table's");
    let _ = std::fs::remove_dir_all(&dir);
}
