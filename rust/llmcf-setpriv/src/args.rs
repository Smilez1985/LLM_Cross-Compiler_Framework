//! The command line: exactly the setpriv (util-linux) options the framework uses, with setpriv's names and meaning,
//! and nothing else.
//!
//! Taken: `--reuid=UID`, `--regid=GID`, `--groups=GID,GID,...`, `--clear-groups`, `--inh-caps=LIST`,
//! `--ambient-caps=LIST`, `--bounding-set=LIST`, `--no-new-privs`, then `--` (or the first argument that is not an
//! option) and the program with its arguments. An option value may follow as `--name=VALUE` or as the next argument.
//! Like setpriv: `--regid` needs `--groups` or `--clear-groups`, `--groups` and `--clear-groups` exclude each
//! other, an option given twice is refused, and a program is required. Unlike setpriv, ids are numbers only: no
//! user or group name is ever looked up (no NSS, nothing of the image or the host is loaded). `--version` and
//! `--help` print and exit. Every other option is refused. No unsafe code here.
#![forbid(unsafe_code)]

use std::ffi::OsString;

use crate::caps::{self, Item};

/// What the command line asks for.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Options {
    pub reuid: Option<u32>,
    pub regid: Option<u32>,
    /// `--groups`: exactly these supplementary groups.
    pub groups: Option<Vec<u32>>,
    /// `--clear-groups`: no supplementary groups.
    pub clear_groups: bool,
    pub inh_caps: Option<Vec<Item>>,
    pub ambient_caps: Option<Vec<Item>>,
    pub bounding_set: Option<Vec<Item>>,
    pub no_new_privs: bool,
    /// The program and its arguments (never empty).
    pub command: Vec<OsString>,
}

/// The parsed command line.
#[derive(Debug, PartialEq, Eq)]
pub enum Parsed {
    Run(Options),
    Version,
    Help,
}

pub const USAGE: &str = "Usage: llmcf-setpriv [options] [--] <program> [<argument>...]

Runs a program with other ids and capabilities (a subset of util-linux setpriv, same option names and meaning).

Options:
  --reuid=UID          real, effective and saved user id (a number)
  --regid=GID          real, effective and saved group id (a number; needs --groups or --clear-groups)
  --groups=GID,...     exactly these supplementary groups (numbers)
  --clear-groups       no supplementary groups
  --inh-caps=LIST      the inheritable capabilities (+NAME, -NAME, +all, -all, comma separated, in order)
  --ambient-caps=LIST  the ambient capabilities (same list form)
  --bounding-set=LIST  the capability bounding set (same list form)
  --no-new-privs       set no_new_privs
  --version            print the version
  --help               print this text
";

const WITH_VALUE: [&str; 6] = [
    "--reuid",
    "--regid",
    "--groups",
    "--inh-caps",
    "--ambient-caps",
    "--bounding-set",
];
const FLAGS: [&str; 4] = ["--clear-groups", "--no-new-privs", "--version", "--help"];

/// A user or group id: a decimal number below 2^32 - 1 (that value means "unchanged" to the kernel).
pub fn parse_id(text: &str, what: &str) -> Result<u32, String> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!(
            "invalid {what} '{text}': only numbers are taken (no name is looked up)"
        ));
    }
    match text.parse::<u32>() {
        Ok(n) if n != u32::MAX => Ok(n),
        _ => Err(format!("invalid {what} '{text}': out of range")),
    }
}

/// The list of `--groups`: comma separated numbers, at least one.
pub fn parse_groups(text: &str) -> Result<Vec<u32>, String> {
    if text.is_empty() {
        return Err("--groups needs at least one group (use --clear-groups for none)".to_string());
    }
    text.split(',')
        .map(|g| parse_id(g, "supplementary group"))
        .collect()
}

/// The command line (without the program name) as setpriv would read it, or the plain reason it is refused.
pub fn parse<I: IntoIterator<Item = OsString>>(args: I) -> Result<Parsed, String> {
    let mut args = args.into_iter();
    let mut opts = Options::default();
    let mut seen: Vec<&'static str> = Vec::new();
    let mut version = false;
    let mut help = false;
    while let Some(arg) = args.next() {
        let text = match arg.to_str() {
            Some(t) => t.to_string(),
            None if arg.to_string_lossy().starts_with('-') => {
                return Err("an option that is not valid text".to_string())
            }
            None => {
                opts.command.push(arg);
                break;
            }
        };
        if text == "--" {
            break;
        }
        if !text.starts_with('-') || text == "-" {
            opts.command.push(arg);
            break;
        }
        let (name, inline) = match text.split_once('=') {
            Some((n, v)) => (n.to_string(), Some(v.to_string())),
            None => (text.clone(), None),
        };
        let known = WITH_VALUE.iter().chain(FLAGS.iter()).find(|k| **k == name);
        let Some(&key) = known else {
            return Err(format!("unrecognized option '{name}' (llmcf-setpriv takes only the options the framework uses)"));
        };
        if seen.contains(&key) {
            return Err(format!("duplicate {key} option"));
        }
        seen.push(key);
        if FLAGS.contains(&key) {
            if inline.is_some() {
                return Err(format!("option '{key}' takes no value"));
            }
            match key {
                "--clear-groups" => opts.clear_groups = true,
                "--no-new-privs" => opts.no_new_privs = true,
                "--version" => version = true,
                _ => help = true,
            }
            continue;
        }
        let value = match inline {
            Some(v) => v,
            None => match args.next() {
                Some(v) => v
                    .into_string()
                    .map_err(|_| format!("the value of {key} is not valid text"))?,
                None => return Err(format!("option '{key}' requires a value")),
            },
        };
        match key {
            "--reuid" => opts.reuid = Some(parse_id(&value, "user id")?),
            "--regid" => opts.regid = Some(parse_id(&value, "group id")?),
            "--groups" => opts.groups = Some(parse_groups(&value)?),
            "--inh-caps" => {
                opts.inh_caps =
                    Some(caps::parse_list(&value).map_err(|e| format!("--inh-caps: {e}"))?)
            }
            "--ambient-caps" => {
                opts.ambient_caps =
                    Some(caps::parse_list(&value).map_err(|e| format!("--ambient-caps: {e}"))?)
            }
            _ => {
                opts.bounding_set =
                    Some(caps::parse_list(&value).map_err(|e| format!("--bounding-set: {e}"))?)
            }
        }
    }
    if help {
        return Ok(Parsed::Help);
    }
    if version {
        return Ok(Parsed::Version);
    }
    opts.command.extend(args);
    if opts.groups.is_some() && opts.clear_groups {
        return Err("--groups and --clear-groups are mutually exclusive".to_string());
    }
    if opts.regid.is_some() && opts.groups.is_none() && !opts.clear_groups {
        return Err("--regid requires --groups or --clear-groups".to_string());
    }
    if opts.command.is_empty() {
        return Err("no program specified".to_string());
    }
    Ok(Parsed::Run(opts))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::caps::Which;

    fn run(args: &[&str]) -> Result<Options, String> {
        match parse(args.iter().map(OsString::from))? {
            Parsed::Run(o) => Ok(o),
            other => Err(format!("not a run: {other:?}")),
        }
    }

    fn os(items: &[&str]) -> Vec<OsString> {
        items.iter().map(OsString::from).collect()
    }

    #[test]
    fn the_runner_switch_of_the_entrypoint() {
        // orchestrator/runtime_layer/entrypoint.sh, with supplementary groups and without
        let o = run(&[
            "--reuid=1000",
            "--regid=1000",
            "--groups=44,109",
            "--inh-caps=-all,+setuid,+setgid",
            "--ambient-caps=-all,+setuid,+setgid",
            "--",
            "/llmcf/bin/busybox",
            "sh",
            "/llmcf/runner.sh",
        ])
        .unwrap();
        assert_eq!((o.reuid, o.regid), (Some(1000), Some(1000)));
        assert_eq!(o.groups, Some(vec![44, 109]));
        assert!(!o.clear_groups && !o.no_new_privs && o.bounding_set.is_none());
        let runner = vec![
            Item::Drop(Which::All),
            Item::Add(Which::One(7)),
            Item::Add(Which::One(6)),
        ];
        assert_eq!(o.inh_caps, Some(runner.clone()));
        assert_eq!(o.ambient_caps, Some(runner));
        assert_eq!(
            o.command,
            os(&["/llmcf/bin/busybox", "sh", "/llmcf/runner.sh"])
        );
        let o = run(&[
            "--reuid=1000",
            "--regid=1000",
            "--clear-groups",
            "--inh-caps=-all,+setuid,+setgid",
            "--ambient-caps=-all,+setuid,+setgid",
            "--",
            "/llmcf/bin/busybox",
            "sh",
        ])
        .unwrap();
        assert!(o.clear_groups && o.groups.is_none());
    }

    #[test]
    fn the_stage_switch_of_as_stage() {
        // orchestrator/runtime_layer/stage_user.sh as_stage
        let o = run(&[
            "--reuid=64010",
            "--regid=64010",
            "--groups=1000,44",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            "--no-new-privs",
            "--",
            "/bin/sh",
            "-c",
            "echo hi",
        ])
        .unwrap();
        assert_eq!(
            (o.reuid, o.regid, o.no_new_privs),
            (Some(64010), Some(64010), true)
        );
        assert_eq!(o.inh_caps, Some(vec![Item::Drop(Which::All)]));
        assert_eq!(o.command, os(&["/bin/sh", "-c", "echo hi"]));
    }

    #[test]
    fn values_as_the_next_argument_and_the_tests_bounding_set() {
        // tests/test_stage_user.py starts the runner like this (with the host's setpriv)
        let o = run(&[
            "--bounding-set=-all,+setuid,+setgid",
            "--reuid",
            "64020",
            "--regid",
            "64020",
            "--clear-groups",
            "--no-new-privs",
            "--",
            "true",
        ])
        .unwrap();
        assert_eq!((o.reuid, o.regid), (Some(64020), Some(64020)));
        assert!(o.bounding_set.is_some());
    }

    #[test]
    fn the_program_may_follow_without_a_double_dash() {
        let o = run(&["--reuid=5", "id", "--reuid=0"]).unwrap();
        assert_eq!(o.command, os(&["id", "--reuid=0"]));
        assert_eq!(
            run(&["--", "--weird-name"]).unwrap().command,
            os(&["--weird-name"])
        );
    }

    #[test]
    fn every_other_option_is_refused() {
        for flag in [
            "--keep-groups",
            "--init-groups",
            "--ruid=1",
            "--euid=1",
            "--rgid=1",
            "--egid=1",
            "--dump",
            "-d",
            "--securebits=+noroot",
            "--selinux-label=x",
            "--apparmor-profile=x",
            "--reset-env",
            "--pdeathsig=TERM",
            "--landlock-access=fs",
            "--reu=1",
            "--keep-caps",
            "-V",
            "--nnp",
        ] {
            let err = run(&[flag, "--", "true"]).unwrap_err();
            assert!(err.contains("unrecognized option"), "{flag}: {err}");
        }
    }

    #[test]
    fn setprivs_rules_hold() {
        assert!(run(&["--regid=5", "--", "true"])
            .unwrap_err()
            .contains("requires --groups or --clear-groups"));
        assert!(run(&["--groups=1", "--clear-groups", "--", "true"])
            .unwrap_err()
            .contains("mutually exclusive"));
        assert!(run(&["--reuid=1", "--reuid=2", "--", "true"])
            .unwrap_err()
            .contains("duplicate"));
        assert!(run(&["--reuid=1"]).unwrap_err().contains("no program"));
        assert!(run(&["--reuid=1", "--"])
            .unwrap_err()
            .contains("no program"));
        assert!(run(&["--reuid"]).unwrap_err().contains("requires a value"));
        assert!(run(&["--no-new-privs=1", "--", "true"])
            .unwrap_err()
            .contains("takes no value"));
    }

    #[test]
    fn ids_are_numbers_only() {
        for bad in [
            "root",
            "",
            "-1",
            "1.5",
            " 1",
            "4294967295",
            "99999999999",
            "+5",
        ] {
            assert!(
                run(&[&format!("--reuid={bad}"), "--", "true"]).is_err(),
                "uid {bad:?} was taken"
            );
            assert!(
                run(&["--regid=1", &format!("--groups={bad}"), "--", "true"]).is_err(),
                "group {bad:?}"
            );
        }
        assert!(run(&["--groups=1,,2", "--", "true"]).is_err());
        assert!(run(&["--groups=", "--", "true"]).is_err());
        assert_eq!(run(&["--reuid=0", "--", "true"]).unwrap().reuid, Some(0));
        assert_eq!(
            run(&["--reuid=4294967294", "--", "true"]).unwrap().reuid,
            Some(u32::MAX - 1)
        );
    }

    #[test]
    fn bad_capability_lists_are_refused() {
        assert!(run(&["--inh-caps=all", "--", "true"])
            .unwrap_err()
            .contains("--inh-caps"));
        assert!(run(&["--ambient-caps=+nothing", "--", "true"])
            .unwrap_err()
            .contains("--ambient-caps"));
        assert!(run(&["--bounding-set=", "--", "true"])
            .unwrap_err()
            .contains("--bounding-set"));
    }

    #[test]
    fn version_and_help() {
        assert_eq!(parse(os(&["--version"])).unwrap(), Parsed::Version);
        assert_eq!(parse(os(&["--help"])).unwrap(), Parsed::Help);
        assert_eq!(
            parse(os(&["--reuid=1", "--version"])).unwrap(),
            Parsed::Version
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_program_name_that_is_not_text_is_kept_as_it_is() {
        use std::os::unix::ffi::OsStringExt;
        let name = OsString::from_vec(vec![b'p', 0xff]);
        match parse(vec![OsString::from("--"), name.clone()]).unwrap() {
            Parsed::Run(o) => assert_eq!(o.command, vec![name]),
            other => panic!("{other:?}"),
        }
    }
}
