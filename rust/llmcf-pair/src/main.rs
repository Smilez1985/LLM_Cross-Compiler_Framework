//! llmcf-pair: pairing of a board with the LLM Cross-Compiler Framework by a short code.
//!
//! `llmcf-pair announce --out FILE [--identity FILE] [--host-keys DIR] [--window SECONDS] [--attempts N]
//! [--port PORT]` is started by the hardware probe on the board (under the probe's sudo) when pairing by code is on.
//! It shows an 8-digit code on the console, announces `_llmcf-pair._tcp` by mDNS on the local links for at most 10
//! minutes (then exits and answers nothing more), listens on a TCP port (any free one unless --port; the mDNS answer
//! names it) and talks only to peers on a local link (private addresses, 169.254/16, fe80::/10, ULA). A peer that
//! proves the code (SPAKE2, proto.rs) gets the board's SSH host keys and the probe's identity (--identity, a JSON
//! object) over the channel the code keyed, and sends the framework's pairing content (its public key line and
//! signer file), which is written to --out (created new, mode 0600) for the probe to install the same way as the
//! content of a probe kit. At most 3 wrong codes (--attempts lowers it), then it exits. No network beyond the local
//! links, no shell, nothing from the environment; a test build (cargo feature test-mode, never in a release) takes
//! `--test-loopback MDNS_PORT`: the loopback address counts as the link and mDNS answers on 127.0.0.1:MDNS_PORT.
//!
//! Exit status: 0 paired, 2 not a call it takes, 3 the window ended, 4 too many wrong codes, 1 another failure.
#![forbid(unsafe_code)]

use llmcf_pair::announce::{self, Outcome, MAX_ATTEMPTS, MAX_WINDOW};
use llmcf_pair::{hostkeys, mdns};
use rand_core::{OsRng, RngCore};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::net::{Ipv4Addr, Ipv6Addr, SocketAddr, TcpListener, UdpSocket};
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const VERSION: &str = env!("CARGO_PKG_VERSION");
const USAGE: &str = "usage: llmcf-pair announce --out FILE [--identity FILE] [--host-keys DIR] [--window SECONDS] \
                     [--attempts N] [--port PORT]";
const MAX_IDENTITY: u64 = 16 * 1024;

struct Options {
    out: PathBuf,
    identity: Option<PathBuf>,
    host_keys: PathBuf,
    window: Duration,
    attempts: u32,
    port: u16,
    test_mdns_port: Option<u16>,
}

fn say(text: &str) {
    eprintln!("llmcf-pair: {text}");
}

fn usage(why: &str) -> ExitCode {
    say(why);
    eprintln!("{USAGE}");
    ExitCode::from(2)
}

fn parse(args: &[String]) -> Result<Options, String> {
    if args.first().map(String::as_str) != Some("announce") {
        return Err("the one command is announce".into());
    }
    let mut o = Options {
        out: PathBuf::new(),
        identity: None,
        host_keys: PathBuf::from("/etc/ssh"),
        window: MAX_WINDOW,
        attempts: MAX_ATTEMPTS,
        port: 0,
        test_mdns_port: None,
    };
    let mut it = args[1..].iter();
    while let Some(flag) = it.next() {
        let mut value = || it.next().cloned().ok_or_else(|| format!("{flag} needs a value"));
        match flag.as_str() {
            "--out" => o.out = PathBuf::from(value()?),
            "--identity" => o.identity = Some(PathBuf::from(value()?)),
            "--host-keys" => o.host_keys = PathBuf::from(value()?),
            "--window" => {
                let s: u64 = value()?.parse().map_err(|_| "--window takes seconds".to_string())?;
                if s == 0 || s > MAX_WINDOW.as_secs() {
                    return Err(format!("--window takes 1 to {} seconds", MAX_WINDOW.as_secs()));
                }
                o.window = Duration::from_secs(s);
            }
            "--attempts" => {
                let n: u32 = value()?.parse().map_err(|_| "--attempts takes a number".to_string())?;
                if n == 0 || n > MAX_ATTEMPTS {
                    return Err(format!("--attempts takes 1 to {MAX_ATTEMPTS}"));
                }
                o.attempts = n;
            }
            "--port" => o.port = value()?.parse().map_err(|_| "--port takes a port number".to_string())?,
            "--test-loopback" => {
                let port: u16 = value()?.parse().map_err(|_| "--test-loopback takes a port number".to_string())?;
                if cfg!(feature = "test-mode") {
                    o.test_mdns_port = Some(port);
                } else {
                    return Err("no test mode in this build".into());
                }
            }
            other => return Err(format!("unknown option {other}")),
        }
    }
    if o.out.as_os_str().is_empty() {
        return Err("--out FILE is needed".into());
    }
    Ok(o)
}

/// The probe's identity: a JSON object of at most 16 KiB, or an empty object.
fn identity(path: &Option<PathBuf>) -> serde_json::Value {
    let Some(path) = path else { return serde_json::json!({}) };
    match fs::metadata(path) {
        Ok(m) if m.len() <= MAX_IDENTITY => {}
        _ => return serde_json::json!({}),
    }
    match fs::read(path).ok().and_then(|b| serde_json::from_slice::<serde_json::Value>(&b).ok()) {
        Some(v) if v.is_object() => v,
        _ => serde_json::json!({}),
    }
}

fn facts(o: &Options) -> Vec<u8> {
    let keys: Vec<serde_json::Value> = hostkeys::read_dir(&o.host_keys)
        .into_iter()
        .map(|k| serde_json::json!({"type": k.key_type, "key": k.line, "fingerprint": k.fingerprint}))
        .collect();
    serde_json::to_vec(&serde_json::json!({"version": 1, "identity": identity(&o.identity), "host_keys": keys}))
        .expect("plain JSON values serialize")
}

fn listener(o: &Options) -> std::io::Result<TcpListener> {
    if o.test_mdns_port.is_some() {
        return TcpListener::bind((Ipv4Addr::LOCALHOST, o.port));
    }
    // both families on one socket where the board has IPv6; the peer check, not the bind, decides who may pair
    TcpListener::bind((Ipv6Addr::UNSPECIFIED, o.port)).or_else(|_| TcpListener::bind((Ipv4Addr::UNSPECIFIED, o.port)))
}

fn write_content(out: &PathBuf, content: &serde_json::Value) -> std::io::Result<()> {
    let mut f = OpenOptions::new().write(true).create_new(true).mode(0o600).open(out)?;
    f.write_all(&serde_json::to_vec(content).expect("a JSON value serializes"))?;
    f.sync_all()
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() == 1 && (args[0] == "--version" || args[0] == "version") {
        println!("llmcf-pair {VERSION}");
        return ExitCode::SUCCESS;
    }
    if args.len() == 1 && (args[0] == "--help" || args[0] == "-h") {
        println!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    let o = match parse(&args) {
        Ok(o) => o,
        Err(why) => return usage(&why),
    };
    if o.out.exists() {
        say("the --out file exists already; nothing was started");
        return ExitCode::from(1);
    }
    let loopback_is_link = o.test_mdns_port.is_some();
    let listener = match listener(&o) {
        Ok(l) => l,
        Err(e) => {
            say(&format!("no TCP port to listen on ({})", e.kind()));
            return ExitCode::from(1);
        }
    };
    let port = listener.local_addr().map(|a| a.port()).unwrap_or(0);
    let sockets = match o.test_mdns_port {
        Some(p) => UdpSocket::bind((Ipv4Addr::LOCALHOST, p)).map(|s| vec![s]),
        None => mdns::system_sockets(),
    };
    let sockets = match sockets {
        Ok(s) => s,
        Err(e) => {
            say(&format!("no mDNS socket ({}); the framework can still find the board by its address", e.kind()));
            Vec::new()
        }
    };
    if let (Some(_), Some(s)) = (o.test_mdns_port, sockets.first()) {
        let mdns_port = s.local_addr().map(|a: SocketAddr| a.port()).unwrap_or(0);
        say(&format!("test mode: tcp={port} mdns={mdns_port}"));
    }
    let tag = format!("{:04x}", OsRng.next_u32() & 0xffff);
    let responder =
        mdns::Responder { instance: format!("llmcf-{tag}"), host: format!("llmcf-pair-{tag}"), port, loopback_is_link };
    let code = announce::new_code();
    let deadline = Instant::now() + o.window;
    let stop = Arc::new(AtomicBool::new(false));
    let threads = mdns::serve(sockets, responder, deadline, stop.clone());
    let minutes = o.window.as_secs().div_ceil(60);
    say(&format!(
        "pairing code {} - type it into the framework on your computer (never into a chat). Valid for {minutes} \
         minute(s), {} tries; only computers on a local link (cable or WLAN) can pair.",
        announce::shown(&code),
        o.attempts
    ));
    let outcome = announce::serve(&listener, &code, &facts(&o), deadline, o.attempts, loopback_is_link, say);
    stop.store(true, Ordering::SeqCst);
    drop(listener);
    for t in threads {
        let _ = t.join();
    }
    match outcome {
        Ok(Outcome::Paired(content)) => match write_content(&o.out, &content) {
            Ok(()) => {
                say("paired: the framework's content was received");
                println!("llmcf-pair: result=paired");
                ExitCode::SUCCESS
            }
            Err(e) => {
                say(&format!("the content could not be written ({})", e.kind()));
                ExitCode::from(1)
            }
        },
        Ok(Outcome::WindowEnded) => {
            say("the pairing window ended; the board no longer answers");
            println!("llmcf-pair: result=window-ended");
            ExitCode::from(3)
        }
        Ok(Outcome::TooManyAttempts) => {
            say("too many wrong codes; the board no longer answers");
            println!("llmcf-pair: result=wrong-codes");
            ExitCode::from(4)
        }
        Err(e) => {
            say(&format!("the pairing stopped ({})", e.kind()));
            ExitCode::from(1)
        }
    }
}
