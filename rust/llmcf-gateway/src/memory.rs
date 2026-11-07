//! The memory guard: no new request goes to the model while the board's free memory is low, so the kernel's
//! out-of-memory killer is never reached through the gateway. It reads MemAvailable from /proc/meminfo and, where
//! the kernel has it, the memory pressure ("some avg10" of /proc/pressure/memory, the share of the last ten seconds
//! in which some task waited for memory). Below the floor or above the pressure limit, new requests wait in the
//! queue (queue.rs), never refused for it, until memory recovers or their wait ends. A change is logged in one line
//! with the numbers only. A board whose kernel says neither is taken as fine.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// How long one reading is used before /proc is read again (the queue asks on every request).
const FRESH: Duration = Duration::from_millis(250);

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Reading {
    pub available_mib: Option<u64>,
    pub psi_some_avg10: Option<f64>,
}

/// MemAvailable of /proc/meminfo, in MiB.
pub fn parse_meminfo(text: &str) -> Option<u64> {
    let line = text.lines().find(|l| l.starts_with("MemAvailable:"))?;
    let kb: u64 = line.split_whitespace().nth(1)?.parse().ok()?;
    Some(kb / 1024)
}

/// "some avg10" of /proc/pressure/memory, in percent.
pub fn parse_psi(text: &str) -> Option<f64> {
    let line = text.lines().find(|l| l.starts_with("some "))?;
    let v = line.split_whitespace().find_map(|w| w.strip_prefix("avg10="))?;
    v.parse().ok().filter(|p: &f64| p.is_finite() && *p >= 0.0)
}

fn read_small(path: &str) -> Option<String> {
    use std::io::Read;
    let mut out = String::new();
    std::fs::File::open(path).ok()?.take(64 * 1024).read_to_string(&mut out).ok()?;
    Some(out)
}

/// The board's reading now.
pub fn read_proc() -> Reading {
    Reading {
        available_mib: read_small("/proc/meminfo").as_deref().and_then(parse_meminfo),
        psi_some_avg10: read_small("/proc/pressure/memory").as_deref().and_then(parse_psi),
    }
}

pub type Reader = Box<dyn Fn() -> Reading + Send + Sync>;

pub struct Guard {
    /// MiB of MemAvailable below which no new request goes in (0: not watched)
    pub floor_mib: u64,
    /// the pressure in percent above which no new request goes in (0: not watched)
    pub psi_max: u32,
    read: Reader,
    last: Mutex<Option<(Instant, Reading)>>,
    low: AtomicBool,
    pub lows: AtomicU64,
}

impl Guard {
    pub fn new(floor_mib: u64, psi_max: u32, read: Reader) -> Guard {
        Guard { floor_mib, psi_max, read, last: Mutex::new(None), low: AtomicBool::new(false), lows: AtomicU64::new(0) }
    }

    pub fn watching(&self) -> bool {
        self.floor_mib > 0 || self.psi_max > 0
    }

    /// The latest reading (read again when older than FRESH).
    pub fn reading(&self) -> Reading {
        let mut last = self.last.lock().unwrap_or_else(|e| e.into_inner());
        if let Some((at, r)) = *last {
            if at.elapsed() < FRESH {
                return r;
            }
        }
        let r = (self.read)();
        *last = Some((Instant::now(), r));
        r
    }

    /// Whether this reading leaves room for a new request.
    pub fn room(&self, r: &Reading) -> bool {
        let under = self.floor_mib > 0 && r.available_mib.is_some_and(|a| a < self.floor_mib);
        let pressed = self.psi_max > 0 && r.psi_some_avg10.is_some_and(|p| p > self.psi_max as f64);
        !(under || pressed)
    }

    pub fn is_low(&self) -> bool {
        self.low.load(Relaxed)
    }

    /// Whether a new request may go in now; a change is logged (numbers only).
    pub fn ok(&self) -> bool {
        if !self.watching() {
            return true;
        }
        let r = self.reading();
        let room = self.room(&r);
        let was_low = self.low.swap(!room, Relaxed);
        if was_low == room {
            eprintln!("llmcf-gateway: {}", self.line(&r, room));
            if !room {
                self.lows.fetch_add(1, Relaxed);
            }
        }
        room
    }

    /// The log line of a change.
    pub fn line(&self, r: &Reading, room: bool) -> String {
        let avail = r.available_mib.map(|a| format!("{a} MiB")).unwrap_or_else(|| "unknown".into());
        let psi = r.psi_some_avg10.map(|p| format!(", pressure some avg10 {p:.2}")).unwrap_or_default();
        if room {
            format!("memory recovered (MemAvailable {avail}{psi}): waiting requests go in")
        } else {
            format!(
                "memory low (MemAvailable {avail}, floor {} MiB{psi}, pressure limit {}): new requests wait",
                self.floor_mib, self.psi_max
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    const MEMINFO: &str = "MemTotal:        1966080 kB\nMemFree:          102400 kB\nMemAvailable:     524288 kB\n";
    const PSI: &str =
        "some avg10=12.50 avg60=3.00 avg300=1.00 total=123\nfull avg10=1.00 avg60=0.00 avg300=0.00 total=9\n";

    #[test]
    fn the_kernels_texts_are_read() {
        assert_eq!(parse_meminfo(MEMINFO), Some(512));
        assert_eq!(parse_meminfo("MemTotal: 1 kB\n"), None);
        assert_eq!(parse_psi(PSI), Some(12.5));
        assert_eq!(parse_psi("full avg10=1.00\n"), None);
        assert_eq!(parse_psi("some avg10=nan avg60=0\n"), None);
    }

    fn guard(floor: u64, psi: u32, now: Arc<Mutex<Reading>>) -> Guard {
        Guard::new(floor, psi, Box::new(move || *now.lock().unwrap()))
    }

    #[test]
    fn below_the_floor_or_under_pressure_no_new_request_goes_in() {
        let now = Arc::new(Mutex::new(Reading { available_mib: Some(512), psi_some_avg10: Some(1.0) }));
        let g = guard(128, 50, now.clone());
        assert!(g.ok());
        *now.lock().unwrap() = Reading { available_mib: Some(100), psi_some_avg10: Some(1.0) };
        std::thread::sleep(FRESH + Duration::from_millis(20));
        assert!(!g.ok() && g.is_low());
        *now.lock().unwrap() = Reading { available_mib: Some(900), psi_some_avg10: Some(80.0) };
        std::thread::sleep(FRESH + Duration::from_millis(20));
        assert!(!g.ok(), "under pressure");
        *now.lock().unwrap() = Reading { available_mib: Some(900), psi_some_avg10: None };
        std::thread::sleep(FRESH + Duration::from_millis(20));
        assert!(g.ok() && !g.is_low(), "recovered (a kernel without pressure numbers is fine)");
        assert_eq!(g.lows.load(Relaxed), 1, "one change to low, logged once");
    }

    #[test]
    fn an_unknown_reading_and_a_guard_off_let_requests_in() {
        let now = Arc::new(Mutex::new(Reading::default()));
        assert!(guard(128, 50, now.clone()).ok());
        *now.lock().unwrap() = Reading { available_mib: Some(1), psi_some_avg10: Some(99.0) };
        assert!(guard(0, 0, now).ok());
    }

    #[test]
    fn the_log_line_holds_numbers_only() {
        let g = guard(128, 50, Arc::new(Mutex::new(Reading::default())));
        let r = Reading { available_mib: Some(100), psi_some_avg10: Some(3.0) };
        assert_eq!(
            g.line(&r, false),
            "memory low (MemAvailable 100 MiB, floor 128 MiB, pressure some avg10 3.00, pressure limit 50): new requests wait"
        );
        assert!(g.line(&r, true).starts_with("memory recovered (MemAvailable 100 MiB"));
    }

    #[test]
    fn the_board_itself_can_be_read() {
        let r = read_proc();
        if std::path::Path::new("/proc/meminfo").exists() {
            assert!(r.available_mib.is_some());
        }
    }
}
