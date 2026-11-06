//! The board's pairing window: one TCP listener, one session at a time, only with peers on a local link, at most
//! `attempts` sessions that tried a code, and nothing after the deadline.

use crate::local::peer_allowed;
use crate::proto::{board_session, Failure};
use rand_core::{OsRng, RngCore};
use std::io::{self, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::thread;
use std::time::{Duration, Instant};

/// The longest pairing window (10 minutes) and the most codes a window takes (3).
pub const MAX_WINDOW: Duration = Duration::from_secs(600);
pub const MAX_ATTEMPTS: u32 = 3;
/// How long one session may take at most (a peer that stops answering does not hold the window).
pub const SESSION_TIMEOUT: Duration = Duration::from_secs(30);

/// One session's stream with an end for the whole session (the window's end or SESSION_TIMEOUT after the accept,
/// whichever comes first): every read and write gets only the time left, and none starts after the end. A per-read
/// timeout alone would let a peer that sends one byte at a time keep a session, and finish a pairing, long after the
/// window closed.
struct Bounded<'a> {
    stream: &'a TcpStream,
    end: Instant,
}

impl Bounded<'_> {
    fn left(&self) -> io::Result<Duration> {
        let left = self.end.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return Err(io::Error::new(io::ErrorKind::TimedOut, "the session's time ended"));
        }
        Ok(left)
    }
}

impl Read for Bounded<'_> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        let left = self.left()?;
        self.stream.set_read_timeout(Some(left))?;
        let mut s = self.stream;
        s.read(buf)
    }
}

impl Write for Bounded<'_> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let left = self.left()?;
        self.stream.set_write_timeout(Some(left))?;
        let mut s = self.stream;
        s.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        let mut s = self.stream;
        s.flush()
    }
}

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
        let (stream, peer) = match listener.accept() {
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
        let end = deadline.min(Instant::now() + SESSION_TIMEOUT);
        stream.set_nonblocking(false)?;
        let _ = stream.set_nodelay(true);
        match board_session(&mut Bounded { stream: &stream, end }, code, facts) {
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
