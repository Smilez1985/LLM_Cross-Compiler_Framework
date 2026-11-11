//! The board's clock (`sudo llmcf-install clock STAMP|keep`): the PC's time for a board without a battery-backed
//! clock, and keeping the last known time across a restart. No shell, no network, no value from the environment.
//!
//! - `clock STAMP` (a UTC time stamp `YYYY-MM-DDTHH:MM:SSZ` from MIN_YEAR to MAX_YEAR, checked here again): sets the
//!   system time with `date -u -s`, writes it into a working RTC with `hwclock -w -u`, and saves it for the next
//!   start (`fake-hwclock save force`, or systemd-timesyncd's clock file touched).
//! - `clock keep`: nothing when the board has a working RTC; else fake-hwclock when it is installed (its unit
//!   enabled, the time saved), else systemd-timesyncd (enabled and started: it saves its clock file while it runs
//!   and moves the clock forward to it at start); else it says that neither is there. It never installs a package
//!   itself: `install_package plan|apply - fake-hwclock` (package.rs) does, after the person's yes.
//!
//! Report lines start with `llmcf-bundle: `; the last one is `result=ok|unavailable|refused|failed changed=N ...`.

use std::fs::{self, OpenOptions};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use crate::install::{EXIT_FAILED, EXIT_OK, EXIT_REFUSED};
use crate::sys::Env;

pub const ACTION: &str = "clock";
/// The years a time stamp may name: no PC that pairs a board shows an earlier one, and a later one is a mistake.
pub const MIN_YEAR: u64 = 2025;
pub const MAX_YEAR: u64 = 2099;
/// An RTC that holds a time before this (2025-01-01T00:00:00Z) was reset (no battery): no working RTC.
pub const RTC_FLOOR: u64 = 1_735_689_600;
const RTC_EPOCH: &str = "/sys/class/rtc/rtc0/since_epoch";
const RTC_DIR: &str = "/sys/class/rtc/rtc0";
const DATE: [&str; 3] = ["/usr/bin/date", "/bin/date", "/usr/sbin/date"];
const HWCLOCK: [&str; 4] = ["/usr/sbin/hwclock", "/sbin/hwclock", "/usr/bin/hwclock", "/bin/hwclock"];
const FAKE_HWCLOCK: [&str; 2] = ["/usr/sbin/fake-hwclock", "/sbin/fake-hwclock"];
const TIMESYNCD: [&str; 2] = ["/usr/lib/systemd/systemd-timesyncd", "/lib/systemd/systemd-timesyncd"];
const SYSTEMCTL: [&str; 3] = ["/usr/bin/systemctl", "/bin/systemctl", "/usr/sbin/systemctl"];
const TIMESYNC_DIR: &str = "/var/lib/systemd/timesync";
const TIMESYNC_CLOCK: &str = "/var/lib/systemd/timesync/clock";
const SYSTEMD_RUN: &str = "/run/systemd/system";

fn done(result: &str, changed: u32, detail: &str, code: i32) -> i32 {
    println!("llmcf-bundle: result={result} changed={changed} {detail}");
    code
}

/// Whether `s` is the one argument `clock` takes: `keep` or a UTC time stamp in range.
pub fn is_arg(s: &str) -> bool {
    s == "keep" || stamp_seconds(s).is_some()
}

fn leap(y: u64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

fn days_in(y: u64, m: u64) -> u64 {
    match m {
        2 if leap(y) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Seconds since 1970 of a UTC time stamp `YYYY-MM-DDTHH:MM:SSZ` (MIN_YEAR to MAX_YEAR, a real date), else None.
pub fn stamp_seconds(s: &str) -> Option<u64> {
    let b = s.as_bytes();
    let shape = b.len() == 20
        && b.iter().enumerate().all(|(i, &c)| match i {
            4 | 7 => c == b'-',
            10 => c == b'T',
            13 | 16 => c == b':',
            19 => c == b'Z',
            _ => c.is_ascii_digit(),
        });
    if !shape {
        return None;
    }
    let n = |a: usize, z: usize| s[a..z].parse::<u64>().ok();
    let (y, mo, d, h, mi, se) = (n(0, 4)?, n(5, 7)?, n(8, 10)?, n(11, 13)?, n(14, 16)?, n(17, 19)?);
    if !(MIN_YEAR..=MAX_YEAR).contains(&y) || !(1..=12).contains(&mo) || d == 0 || d > days_in(y, mo) {
        return None;
    }
    if h > 23 || mi > 59 || se > 59 {
        return None;
    }
    let mut days = 0u64;
    for year in 1970..y {
        days += if leap(year) { 366 } else { 365 };
    }
    for month in 1..mo {
        days += days_in(y, month);
    }
    days += d - 1;
    Some(days * 86400 + h * 3600 + mi * 60 + se)
}

/// (whether the board has an RTC at all, the time it holds when it reads)
fn rtc(env: &Env) -> (bool, Option<u64>) {
    let present = env.path(RTC_DIR).exists();
    let epoch = fs::read_to_string(env.path(RTC_EPOCH)).ok().and_then(|t| t.trim().parse::<u64>().ok());
    (present, epoch)
}

/// Whether the board has a working RTC: one that holds a time from RTC_FLOOR on.
pub fn working_rtc(env: &Env) -> bool {
    matches!(rtc(env), (true, Some(e)) if e >= RTC_FLOOR)
}

/// Saves the time just set for the next start: fake-hwclock (force: the time may go back), else the clock file of
/// systemd-timesyncd (its time is the file's modification time). The word of what saved it.
fn save(env: &Env) -> &'static str {
    if let Some(fake) = env.tool(&FAKE_HWCLOCK) {
        let (ok, _) = env.run(&fake, &["save", "force"]);
        if ok {
            return "fake-hwclock";
        }
    }
    if env.path(TIMESYNC_DIR).is_dir() {
        let path = env.path(TIMESYNC_CLOCK);
        let meta = fs::symlink_metadata(&path);
        // never through a link: only a plain file there, or a new one
        if meta.as_ref().map(|m| m.file_type().is_file()).unwrap_or(true) {
            let file = OpenOptions::new().write(true).create(true).truncate(false).open(&path);
            if let Ok(f) = file {
                if f.set_modified(SystemTime::now()).is_ok() {
                    return "timesyncd";
                }
            }
        }
    }
    "none"
}

fn set(env: &Env, stamp: &str) -> i32 {
    let Some(seconds) = stamp_seconds(stamp) else {
        return done("refused", 0, "not a UTC time stamp from 2025 to 2099", EXIT_REFUSED);
    };
    let Some(date) = env.tool(&DATE) else {
        return done("failed", 0, "the board has no date program", EXIT_FAILED);
    };
    let before = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or(Duration::ZERO).as_secs();
    // `date -u -s "YYYY-MM-DD HH:MM:SS"`: the form GNU date and BusyBox both take
    let text = format!("{} {}", &stamp[..10], &stamp[11..19]);
    let (ok, out) = env.run(&date, &["-u", "-s", &text]);
    if !ok {
        let why: String = out.lines().next().unwrap_or("").chars().take(160).collect();
        return done("failed", 0, &format!("date did not set the time: {why}"), EXIT_FAILED);
    }
    println!("llmcf-bundle: done clock set={stamp} was={before} to={seconds}");
    let rtc_written = if env.path(RTC_DIR).exists() {
        match env.tool(&HWCLOCK) {
            Some(hw) => env.run(&hw, &["-w", "-u"]).0,
            None => false,
        }
    } else {
        false
    };
    let saved = save(env);
    done("ok", 1, &format!("set={stamp} rtc_written={} saved={saved}", if rtc_written { "yes" } else { "no" }), EXIT_OK)
}

fn keep(env: &Env) -> i32 {
    if working_rtc(env) {
        return done("ok", 0, "keep=rtc the board has a working RTC; nothing to set up", EXIT_OK);
    }
    let systemd = env.path(SYSTEMD_RUN).is_dir();
    let systemctl = env.tool(&SYSTEMCTL);
    if let Some(fake) = env.tool(&FAKE_HWCLOCK) {
        if let (true, Some(sc)) = (systemd, systemctl.as_ref()) {
            let (ok, _) = env.run(sc, &["enable", "fake-hwclock.service"]);
            if !ok {
                return done("failed", 0, "keep=fake-hwclock systemctl could not enable fake-hwclock", EXIT_FAILED);
            }
        }
        let (ok, _) = env.run(&fake, &["save"]);
        let note = if ok { "" } else { " (its save did not run now; it saves at the next shutdown)" };
        return done("ok", 1, &format!("keep=fake-hwclock{note}"), EXIT_OK);
    }
    if let (Some(_), true, Some(sc)) = (env.tool(&TIMESYNCD), systemd, systemctl.as_ref()) {
        let (ok, _) = env.run(sc, &["enable", "--now", "systemd-timesyncd.service"]);
        if !ok {
            return done("failed", 0, "keep=timesyncd systemctl could not enable systemd-timesyncd", EXIT_FAILED);
        }
        save(env);
        return done("ok", 1, "keep=timesyncd", EXIT_OK);
    }
    done(
        "unavailable",
        0,
        "keep=none neither fake-hwclock nor systemd-timesyncd is on this board (llmcf-install install_package \
         brings fake-hwclock after the person's yes, from the board's own sources or a board preload)",
        EXIT_OK,
    )
}

/// `clock ARG` as root for the board account that called it through sudo (main.rs checked the caller).
pub fn run(env: &Env, arg: &str) -> i32 {
    if arg == "keep" {
        keep(env)
    } else {
        set(env, arg)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn time_stamps_are_read_exactly() {
        assert_eq!(stamp_seconds("2025-01-01T00:00:00Z"), Some(RTC_FLOOR));
        assert_eq!(stamp_seconds("2026-11-10T12:30:05Z"), Some(1_794_313_805));
        assert_eq!(stamp_seconds("2028-02-29T00:00:00Z"), Some(1_835_395_200));
        for bad in [
            "2024-12-31T23:59:59Z",
            "2100-01-01T00:00:00Z",
            "2026-02-29T00:00:00Z",
            "2026-13-01T00:00:00Z",
            "2026-11-10T24:00:00Z",
            "2026-11-10 12:00:00",
            "2026-11-10T12:00:00Z;x",
            "-026-11-10T12:00:00Z",
            "keep2",
            "",
        ] {
            assert!(stamp_seconds(bad).is_none(), "{bad}");
        }
        assert!(is_arg("keep") && is_arg("2026-11-10T12:00:00Z") && !is_arg("Keep"));
    }
}
