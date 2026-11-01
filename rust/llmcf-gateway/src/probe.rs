//! `llmcf-gateway probe`: the board's own check of its running gateway on loopback (what the host's verify and
//! bench run over the board session). It prints `llmcf-gateway: key=value ...` lines only (the presented
//! certificate's fingerprint in TLS mode, health facts, the tiny completion's result, the bench numbers), never a
//! prompt or an answer's text. Exit 0 when every asked check passed, 1 otherwise.

use crate::args::Probe;
use crate::tls;
use bytes::Bytes;
use http_body_util::{BodyExt, Full, Limited};
use hyper::header::{CONTENT_TYPE, HOST};
use hyper::{Method, Request};
use hyper_util::rt::TokioIo;
use serde_json::{json, Value};
use std::time::{Duration, Instant};
use tokio::net::TcpStream;

/// The fixed tiny request of the verify and the bench.
pub fn tiny_request() -> Value {
    json!({"messages": [{"role": "user", "content": "Reply with the single word OK."}],
           "max_tokens": 8, "temperature": 0})
}

async fn call(p: &Probe, method: Method, path: &str, body: Option<&Value>) -> Result<(u16, Value), String> {
    let stream = TcpStream::connect(("127.0.0.1", p.port)).await.map_err(|_| "the gateway does not answer")?;
    let mut req = Request::builder().method(method).uri(path).header(HOST, format!("127.0.0.1:{}", p.port));
    if body.is_some() {
        req = req.header(CONTENT_TYPE, "application/json");
    }
    let req =
        req.body(Full::new(body.map(|b| Bytes::from(b.to_string())).unwrap_or_default())).map_err(|e| e.to_string())?;
    let resp = if p.tls {
        let (cfg, rec) = tls::probe_config()?;
        let name = rustls::pki_types::ServerName::try_from("llmcf-gateway").map_err(|e| e.to_string())?;
        let tls = tokio_rustls::TlsConnector::from(cfg)
            .connect(name, stream)
            .await
            .map_err(|e| format!("no TLS handshake with the gateway: {e}"))?;
        if let Some(fp) = rec.seen.lock().ok().and_then(|s| s.clone()) {
            if path == "/health" {
                println!("llmcf-gateway: fingerprint={fp}");
            }
        }
        let (mut s, conn) =
            hyper::client::conn::http1::handshake(TokioIo::new(tls)).await.map_err(|e| e.to_string())?;
        tokio::spawn(async move {
            let _ = conn.await;
        });
        s.send_request(req).await.map_err(|e| e.to_string())?
    } else {
        let (mut s, conn) =
            hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|e| e.to_string())?;
        tokio::spawn(async move {
            let _ = conn.await;
        });
        s.send_request(req).await.map_err(|e| e.to_string())?
    };
    let status = resp.status().as_u16();
    let bytes = Limited::new(resp.into_body(), 1 << 20).collect().await.map_err(|e| e.to_string())?.to_bytes();
    Ok((status, serde_json::from_slice(&bytes).unwrap_or(Value::Null)))
}

fn word(v: &Value, key: &str) -> String {
    let s = match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(b)) => if *b { "yes" } else { "no" }.to_string(),
        Some(Value::Number(n)) => n.to_string(),
        _ => "unknown".into(),
    };
    s.chars().filter(|c| c.is_ascii_alphanumeric() || ".-_".contains(*c)).take(32).collect()
}

async fn completion(p: &Probe) -> Result<(u64, u64), String> {
    let t = Instant::now();
    let (status, v) = call(p, Method::POST, "/v1/chat/completions", Some(&tiny_request())).await?;
    let ms = t.elapsed().as_millis() as u64;
    if status != 200 {
        let code = v.pointer("/error/code").and_then(Value::as_str).unwrap_or("error");
        return Err(format!(
            "status {status} {}",
            code.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '_').take(32).collect::<String>()
        ));
    }
    let tokens = v.pointer("/usage/completion_tokens").and_then(Value::as_u64).unwrap_or(0);
    let has_answer = v.pointer("/choices/0/message/content").map(Value::is_string).unwrap_or(false);
    if !has_answer {
        return Err("the answer holds no message".into());
    }
    Ok((ms, tokens))
}

pub async fn run(p: Probe) -> i32 {
    let health = match tokio::time::timeout(Duration::from_secs(15), call(&p, Method::GET, "/health", None)).await {
        Ok(Ok((200, v))) => v,
        Ok(Ok((s, _))) => {
            println!("llmcf-gateway: health=failed status={s}");
            return 1;
        }
        Ok(Err(e)) => {
            println!(
                "llmcf-gateway: health=failed reason={}",
                e.replace(' ', "_").chars().take(80).collect::<String>()
            );
            return 1;
        }
        Err(_) => {
            println!("llmcf-gateway: health=failed reason=timeout");
            return 1;
        }
    };
    let up = word(&health, "upstream");
    println!(
        "llmcf-gateway: health={} upstream={up} bind={} tls={} budget={} control={} version={}",
        word(&health, "status"),
        word(&health, "bind"),
        word(&health, "tls"),
        word(&health, "ctx_budget"),
        word(&health, "control"),
        word(&health, "gateway")
    );
    let mut code = if word(&health, "status") == "ok" { 0 } else { 1 };
    if p.complete {
        match completion(&p).await {
            Ok((ms, tokens)) => println!("llmcf-gateway: complete=ok ms={ms} tokens={tokens}"),
            Err(e) => {
                println!(
                    "llmcf-gateway: complete=failed reason={}",
                    e.replace(' ', "_").chars().take(80).collect::<String>()
                );
                code = 1;
            }
        }
    }
    if p.bench > 0 {
        let (mut total_ms, mut total_tokens, mut runs) = (0u64, 0u64, 0u32);
        for _ in 0..p.bench {
            match completion(&p).await {
                Ok((ms, tokens)) => {
                    total_ms += ms;
                    total_tokens += tokens;
                    runs += 1;
                }
                Err(e) => {
                    println!(
                        "llmcf-gateway: bench=failed reason={}",
                        e.replace(' ', "_").chars().take(80).collect::<String>()
                    );
                    return 1;
                }
            }
        }
        let avg = total_ms / runs.max(1) as u64;
        let tps = if total_ms > 0 { total_tokens as f64 * 1000.0 / total_ms as f64 } else { 0.0 };
        println!("llmcf-gateway: bench=ok runs={runs} avg_ms={avg} tokens={total_tokens} tokens_per_s={tps:.2}");
    }
    code
}
