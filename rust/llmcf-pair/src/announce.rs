//! The board's pairing window: one TCP listener, one session at a time, only with peers on a local link, at most
//! `attempts` sessions that tried a code, and nothing after the deadline.

use crate::local::peer_allowed;
use crate::proto::{board_session, Failure};
use rand_core::{OsRng, RngCore};
use std::io;
use std::net::TcpListener;
use std::thread;
use std::time::{Duration, Instant};

/// The longest pairing window (10 minutes) and the most codes a window takes (3).
pub const MAX_WINDOW: Duration = Duration::from_secs(600);
pub const MAX_ATTEMPTS: u32 = 3;
/// How long one session may take at most (a peer that stops answering does not hold the window).
pub const SESSION_TIMEOUT: Duration = Duration::from_secs(30);

/// How a window ended.
#[derive(Debug, PartialEq, Eq)]
pub enum Outcome {
    /// A peer proved the code; the pairing content it sent is the value.
    Paired(serde_json::Value),
    /// The window's time ran out.
    WindowEnded,
    /// `attempts` sessions tried a wrong code.
    TooManyAttempts,
}

/// A fresh 8-digit code from the operating system's random source (uniform: values at or above the largest multiple
/// of 10^8 below 2^32 are drawn again).
pub fn new_code() -> String {
    const LIMIT: u32 = u32::MAX - (u32::MAX % 100_000_000);
    loop {
        let v = OsRng.next_u32();
        if v < LIMIT {
            return format!("{:08}", v % 100_000_000);
        }
    }
}

/// The code as the console shows it: two groups of four digits.
pub fn shown(code: &str) -> String {
    if code.len() == 8 {
        format!("{} {}", &code[..4], &code[4..])
    } else {
        code.to_string()
    }
}

/// Serves the pairing on `listener` until a peer proves `code` (the content it sent comes back), the deadline passes
/// or `attempts` sessions tried a wrong code. A peer outside the local links (local.rs) is closed at once and costs no
/// attempt; nor does a peer that leaves before it tried a code. `report` gets one plain line per event.
pub fn serve(
    listener: &TcpListener,
    code: &str,
    facts: &[u8],
    deadline: Instant,
    attempts: u32,
    loopback_is_link: bool,
    mut report: impl FnMut(&str),
) -> io::Result<Outcome> {
    listener.set_nonblocking(true)?;
    let attempts = attempts.clamp(1, MAX_ATTEMPTS);
    let mut used = 0;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return Ok(Outcome::WindowEnded);
        }
        let (mut stream, peer) = match listener.accept() {
            Ok(got) => got,
            Err(e) if e.kind() == io::ErrorKind::WouldBlock || e.kind() == io::ErrorKind::Interrupted => {
                thread::sleep(Duration::from_millis(50));
                continue;
            }
            Err(e) => return Err(e),
        };
        if !peer_allowed(peer.ip(), loopback_is_link) {
            report("refused a peer that is not on a local link");
            drop(stream);
            continue;
        }
        let left =
            deadline.saturating_duration_since(Instant::now()).min(SESSION_TIMEOUT).max(Duration::from_millis(10));
        stream.set_nonblocking(false)?;
        stream.set_read_timeout(Some(left))?;
        stream.set_write_timeout(Some(left))?;
        let _ = stream.set_nodelay(true);
        match board_session(&mut stream, code, facts) {
            Ok(content) => return Ok(Outcome::Paired(content)),
            Err(Failure::Io(_)) => report("a peer left before it tried a code"),
            Err(failure) => {
                used += 1;
                report(match failure {
                    Failure::WrongCode => "a wrong code was typed",
                    _ => "a peer broke the protocol",
                });
                if used >= attempts {
                    return Ok(Outcome::TooManyAttempts);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_are_eight_digits() {
        for _ in 0..200 {
            let c = new_code();
            assert_eq!(c.len(), 8);
            assert!(c.bytes().all(|b| b.is_ascii_digit()));
        }
        assert_eq!(shown("01234567"), "0123 4567");
    }
}
