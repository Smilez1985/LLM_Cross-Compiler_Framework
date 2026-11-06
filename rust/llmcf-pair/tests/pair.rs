//! The board's pairing window end to end over real TCP on the loopback address (the loopback-as-link mode of the
//! tests): the right code pairs, a wrong code fails, three wrong codes end the window, the window ends on time, and a
//! peer that is not on a local link is refused without costing an attempt.

use llmcf_pair::announce::{serve, Outcome};
use llmcf_pair::proto::{host_session, Failure};
use std::net::{TcpListener, TcpStream};
use std::thread;
use std::time::{Duration, Instant};

const FACTS: &[u8] = br#"{"version":1,"identity":{"probe":"test"},"host_keys":[]}"#;
const CONTENT: &[u8] = br#"{"public_key":"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB example","signers":"x"}"#;

fn window(
    code: &'static str,
    secs: u64,
    attempts: u32,
    loopback_is_link: bool,
) -> (u16, thread::JoinHandle<(Outcome, Vec<String>)>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = thread::spawn(move || {
        let mut lines = Vec::new();
        let deadline = Instant::now() + Duration::from_secs(secs);
        let outcome =
            serve(&listener, code, FACTS, deadline, attempts, loopback_is_link, |l| lines.push(l.to_string())).unwrap();
        (outcome, lines)
    });
    (port, handle)
}

fn try_code(port: u16, code: &str) -> Result<serde_json::Value, Failure> {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    host_session(&mut s, code, CONTENT)
}

#[test]
fn the_right_code_pairs() {
    let (port, board) = window("31415926", 30, 3, true);
    let facts = try_code(port, "31415926").unwrap();
    assert_eq!(facts["identity"]["probe"], "test");
    let (outcome, _) = board.join().unwrap();
    match outcome {
        Outcome::Paired(content) => assert_eq!(content["signers"], "x"),
        other => panic!("{other:?}"),
    }
}

#[test]
fn a_wrong_code_fails_and_the_right_one_still_pairs() {
    let (port, board) = window("27182818", 30, 3, true);
    assert_eq!(try_code(port, "27182819").unwrap_err(), Failure::WrongCode);
    assert!(try_code(port, "27182818").is_ok());
    let (outcome, lines) = board.join().unwrap();
    assert!(matches!(outcome, Outcome::Paired(_)));
    assert!(lines.iter().any(|l| l.contains("wrong code")));
}

#[test]
fn three_wrong_codes_end_the_window() {
    let (port, board) = window("11223344", 30, 3, true);
    for wrong in ["00000000", "11111111", "22222222"] {
        assert_eq!(try_code(port, wrong).unwrap_err(), Failure::WrongCode);
    }
    let (outcome, _) = board.join().unwrap();
    assert_eq!(outcome, Outcome::TooManyAttempts);
    // the board no longer answers: the listener is gone with the window
    let late = TcpStream::connect(("127.0.0.1", port));
    if let Ok(mut s) = late {
        s.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        assert!(host_session(&mut s, "11223344", CONTENT).is_err());
    }
}

#[test]
fn the_window_ends_on_time() {
    let started = Instant::now();
    let (_port, board) = window("12121212", 1, 3, true);
    let (outcome, _) = board.join().unwrap();
    assert_eq!(outcome, Outcome::WindowEnded);
    let took = started.elapsed();
    assert!(took >= Duration::from_secs(1) && took < Duration::from_secs(5), "{took:?}");
}

#[test]
fn a_peer_off_the_local_links_is_refused_without_an_attempt() {
    // without the loopback-as-link mode the loopback peer stands for an address that is not on a local link
    let (port, board) = window("56565656", 2, 1, false);
    let refused = try_code(port, "56565656");
    assert!(matches!(refused, Err(Failure::Io(_))), "{refused:?}");
    let (outcome, lines) = board.join().unwrap();
    assert_eq!(outcome, Outcome::WindowEnded, "the refusal cost no attempt");
    assert!(lines.iter().any(|l| l.contains("not on a local link")));
}

#[test]
fn a_peer_that_leaves_costs_no_attempt() {
    let (port, board) = window("98989898", 30, 1, true);
    drop(TcpStream::connect(("127.0.0.1", port)).unwrap());
    thread::sleep(Duration::from_millis(200));
    assert!(try_code(port, "98989898").is_ok());
    let (outcome, _) = board.join().unwrap();
    assert!(matches!(outcome, Outcome::Paired(_)));
}

#[cfg(not(feature = "test-mode"))]
#[test]
fn a_release_build_refuses_the_test_mode() {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_llmcf-pair"))
        .args(["announce", "--out", "/nonexistent/x", "--test-loopback", "0"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("no test mode"));
}

#[cfg(feature = "test-mode")]
#[test]
fn the_program_pairs_in_the_test_mode_and_writes_the_content() {
    use std::io::{BufRead, BufReader};
    use std::process::{Command, Stdio};
    let dir = std::env::temp_dir().join(format!("llmcf-pair-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let out = dir.join("content.json");
    let _ = std::fs::remove_file(&out);
    let mut child = Command::new(env!("CARGO_BIN_EXE_llmcf-pair"))
        .args(["announce", "--out"])
        .arg(&out)
        .args(["--host-keys", "/nonexistent", "--window", "30", "--test-loopback", "0"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut err = BufReader::new(child.stderr.take().unwrap());
    let (mut port, mut code) = (0u16, String::new());
    let mut line = String::new();
    while port == 0 || code.is_empty() {
        line.clear();
        assert!(err.read_line(&mut line).unwrap() > 0, "the program ended early");
        if let Some(rest) = line.split("tcp=").nth(1) {
            port = rest.split_whitespace().next().unwrap().parse().unwrap();
        }
        if let Some(rest) = line.split("pairing code ").nth(1) {
            code = rest.chars().take(9).filter(|c| c.is_ascii_digit()).collect();
        }
    }
    assert_eq!(code.len(), 8);
    try_code(port, &code).unwrap();
    let status = child.wait().unwrap();
    assert_eq!(status.code(), Some(0));
    let written: serde_json::Value = serde_json::from_slice(&std::fs::read(&out).unwrap()).unwrap();
    assert_eq!(written["signers"], "x");
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(std::fs::metadata(&out).unwrap().permissions().mode() & 0o777, 0o600);
    std::fs::remove_dir_all(&dir).unwrap();
}

/// A peer that sends its bytes slowly: every byte keeps the board's per-read timeout alive, so without a deadline for
/// the whole session it could finish the pairing long after the window closed.
struct Trickle(TcpStream);

impl std::io::Read for Trickle {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        self.0.read(buf)
    }
}

impl std::io::Write for Trickle {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if buf.is_empty() {
            return Ok(0);
        }
        thread::sleep(Duration::from_millis(40));
        self.0.write(&buf[..1])
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.0.flush()
    }
}

#[test]
fn a_slow_peer_cannot_pair_after_the_window_ended() {
    let started = Instant::now();
    let (port, board) = window("16180339", 1, 3, true);
    let s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    let host = thread::spawn(move || host_session(&mut Trickle(s), "16180339", CONTENT));
    let (outcome, _) = board.join().unwrap();
    assert_eq!(outcome, Outcome::WindowEnded, "the board paired after its window ended");
    assert!(started.elapsed() < Duration::from_secs(3), "the session outlived the window: {:?}", started.elapsed());
    assert!(host.join().unwrap().is_err());
}
