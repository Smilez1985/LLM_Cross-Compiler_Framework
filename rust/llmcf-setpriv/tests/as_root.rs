//! The built llmcf-setpriv on the real kernel, as root (the CI job "Runtime layer tools" runs it as root with
//! `CARGO_TARGET_<host>_RUNNER="sudo -E"`). Every test returns early with a note when it does not run as root or
//! not on Linux: the switch needs both.
#![forbid(unsafe_code)]

use std::process::{Command, Output};

const BIN: &str = env!("CARGO_BIN_EXE_llmcf-setpriv");
const NOBODY: &str = "65534";
const OTHER: &str = "65533";

/// Whether this test process is root on Linux (read from /proc, no unsafe call).
fn root() -> bool {
    let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
    let euid = status
        .lines()
        .find_map(|l| l.strip_prefix("Uid:"))
        .and_then(|v| v.split_whitespace().nth(1));
    if euid == Some("0") {
        return true;
    }
    eprintln!("skipped: llmcf-setpriv's switch is tested as root on Linux only");
    false
}

fn setpriv(args: &[&str]) -> Output {
    Command::new(BIN)
        .args(args)
        .output()
        .expect("llmcf-setpriv runs")
}

fn text(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

/// The `Cap*:` lines of /proc/self/status of the started program, by name.
fn caps_of(stdout: &str, name: &str) -> String {
    stdout
        .lines()
        .find_map(|l| l.strip_prefix(&format!("{name}:")))
        .unwrap_or("?")
        .trim()
        .to_string()
}

const REPORT: &str =
    "id -u; id -g; id -G; grep -E '^(Cap(Eff|Prm|Amb|Inh|Bnd)|NoNewPrivs):' /proc/self/status";

#[test]
fn the_stage_switch_drops_every_right() {
    if !root() {
        return;
    }
    let out = setpriv(&[
        "--reuid",
        NOBODY,
        "--regid",
        NOBODY,
        "--clear-groups",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--no-new-privs",
        "--",
        "/bin/sh",
        "-c",
        REPORT,
    ]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = text(&out);
    let lines: Vec<&str> = stdout.lines().collect();
    assert_eq!(&lines[..3], &[NOBODY, NOBODY, NOBODY], "{stdout}");
    for set in ["CapEff", "CapPrm", "CapAmb", "CapInh"] {
        assert_eq!(caps_of(&stdout, set), "0000000000000000", "{set}: {stdout}");
    }
    assert_eq!(caps_of(&stdout, "NoNewPrivs"), "1");
}

#[test]
fn supplementary_groups_are_exactly_the_asked_ones() {
    if !root() {
        return;
    }
    let out = setpriv(&[
        &format!("--reuid={NOBODY}"),
        &format!("--regid={NOBODY}"),
        "--groups=100,65533",
        "--",
        "/bin/sh",
        "-c",
        "id -G",
    ]);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let mut groups: Vec<String> = text(&out).split_whitespace().map(str::to_string).collect();
    groups.sort();
    assert_eq!(groups, vec!["100", "65533", "65534"]);
}

#[test]
fn becoming_root_again_fails() {
    if !root() {
        return;
    }
    // the switched program is llmcf-setpriv itself, asked to switch back to root
    let out = setpriv(&[
        "--reuid",
        NOBODY,
        "--regid",
        NOBODY,
        "--clear-groups",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--no-new-privs",
        "--",
        BIN,
        "--reuid=0",
        "--regid=0",
        "--clear-groups",
        "--",
        "/bin/true",
    ]);
    assert_eq!(
        out.status.code(),
        Some(127),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let err = String::from_utf8_lossy(&out.stderr);
    assert!(
        err.contains("setgroups failed") || err.contains("setresuid failed"),
        "{err}"
    );
    // also without --no-new-privs and without any capability option
    let out = setpriv(&[
        "--reuid",
        NOBODY,
        "--regid",
        NOBODY,
        "--clear-groups",
        "--",
        BIN,
        "--reuid=0",
        "--",
        "/bin/true",
    ]);
    assert_eq!(
        out.status.code(),
        Some(127),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
}

#[test]
fn the_runner_keeps_exactly_setuid_and_setgid_and_switches_on() {
    if !root() {
        return;
    }
    // like the runtime layer: the container's root with only the two rights, the runner with them as ambient
    // capabilities, then the stage account without any right
    let runner = [
        "--bounding-set=-all,+setuid,+setgid",
        "--reuid",
        NOBODY,
        "--regid",
        NOBODY,
        "--clear-groups",
        "--inh-caps=-all,+setuid,+setgid",
        "--ambient-caps=-all,+setuid,+setgid",
        "--",
    ];
    let mut args: Vec<&str> = runner.to_vec();
    args.extend(["/bin/sh", "-c", REPORT]);
    let out = setpriv(&args);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = text(&out);
    for set in ["CapEff", "CapPrm", "CapAmb", "CapInh", "CapBnd"] {
        assert_eq!(caps_of(&stdout, set), "00000000000000c0", "{set}: {stdout}");
    }
    let mut args: Vec<&str> = runner.to_vec();
    args.extend([
        BIN,
        "--reuid",
        OTHER,
        "--regid",
        OTHER,
        "--groups",
        NOBODY,
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--no-new-privs",
        "--",
        "/bin/sh",
        "-c",
        REPORT,
    ]);
    let out = setpriv(&args);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = text(&out);
    let lines: Vec<&str> = stdout.lines().collect();
    assert_eq!(&lines[..2], &[OTHER, OTHER], "{stdout}");
    for set in ["CapEff", "CapPrm", "CapAmb", "CapInh"] {
        assert_eq!(caps_of(&stdout, set), "0000000000000000", "{set}: {stdout}");
    }
}

#[test]
fn the_program_is_found_on_the_path_and_a_missing_one_is_127() {
    if !root() {
        return;
    }
    let out = setpriv(&["--reuid", NOBODY, "--", "id", "-u"]);
    assert_eq!(
        text(&out).trim(),
        NOBODY,
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let out = setpriv(&["--reuid", NOBODY, "--", "/nonexistent/program"]);
    assert_eq!(out.status.code(), Some(127));
}

#[test]
fn other_options_are_refused_before_anything_changes() {
    // no root needed: the command line is refused first
    let out = setpriv(&["--init-groups", "--reuid=1", "--", "/bin/true"]);
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stderr).contains("unrecognized option"));
    let out = setpriv(&["--version"]);
    assert!(text(&out).starts_with("llmcf-setpriv "));
}
