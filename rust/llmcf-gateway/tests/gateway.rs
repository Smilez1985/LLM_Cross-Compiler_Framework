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
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let log = seen.clone();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let log = log.clone();
            thread::spawn(move || {
                let Some((method, path, auth, body)) = read_request(&mut stream) else { return };
                log.lock().unwrap().push((path.clone(), auth, body));
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
    Stub { port, seen }
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
