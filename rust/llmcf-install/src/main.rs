//! llmcf-install: the root helper of the LLM Cross-Compiler Framework's board bundle.
//!
//! `sudo llmcf-install ACTION BUNDLE` (ACTION plan, apply, status, rollback, uninstall or plan-uninstall) carries out
//! ONLY the typed root actions of the bundle's signed manifest (create_user/remove_user, install_unit/remove_unit,
//! set_cgroup/remove_cgroup, place_root_file/remove_root_file, allow_unit_control: the gateway's account may start,
//! stop and restart exactly the bundle's engine units, through a polkit rule or, on a board without polkit's rules,
//! a sudoers drop-in with exactly those systemctl commands) for the board account that called it through sudo.
//! The manifest is the one the unprivileged installer kept in the account's bundle folder; its signature is checked
//! here against the root-owned signer files (/etc/llmcf/allowed_signers, else /etc/llmcf/signing_key.pem) and
//! nothing the manifest says is trusted before. No network, no shell, no path from the environment; a test build
//! (cargo feature test-root, never in a release) takes `--root PREFIX` first.
//!
//! `sudo llmcf-install harness-start|harness-read|harness-answer|harness-stop|harness-records BUNDLE COMPONENT RUN
//! [MODE|OFFSET|PART]` runs the person's harness of a harness component (manifest root action run_harness) through the
//! framework's harness watch, detached, and reads it back (harness.rs).
//!
//! `sudo llmcf-install install_package plan|apply BUNDLE|- NAME...` installs packages of the board's distribution a
//! signed list names (a bundle's install_package actions, or `-`: the clock keeper fake-hwclock) through apt and dpkg,
//! from a board preload the framework took in for that scope (checked against the distribution's signed lists and the
//! board's own archive keyring) or else from the board's own sources (package.rs).
//!
//! `sudo llmcf-install clock STAMP|keep` sets the board's time to the PC's (a checked UTC time stamp) and saves it
//! for the next start, or sets up keeping the last known time across a restart on a board without a working RTC
//! (clock.rs).
//!
//! Exit status: 0 done (also plan, status, absent), 2 not a call it takes, 3 refused (nothing changed), 5 a step
//! failed (the journal says what was done: roll back or run it again).
#![forbid(unsafe_code)]

mod clock;
mod harness;
mod install;
mod manifest;
mod package;
mod sig;
mod sys;

use std::process::ExitCode;

const VERSION: &str = env!("CARGO_PKG_VERSION");

fn refuse(code: i32, why: &str) -> ExitCode {
    println!("llmcf-bundle: result=refused changed=0 {why}");
    ExitCode::from(code as u8)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    #[cfg(feature = "test-root")]
    let mut args = args;
    if args.len() == 1 && (args[0] == "version" || args[0] == "--version") {
        println!("llmcf-bundle: helper llmcf-install {VERSION}");
        return ExitCode::SUCCESS;
    }
    let env = if args.first().map(String::as_str) == Some("--root") {
        #[cfg(feature = "test-root")]
        {
            if args.len() < 2 {
                return refuse(install::EXIT_USAGE, "--root takes a folder");
            }
            let root = std::path::PathBuf::from(args.remove(1));
            args.remove(0);
            sys::Env::test(root)
        }
        #[cfg(not(feature = "test-root"))]
        {
            return refuse(install::EXIT_USAGE, "this llmcf-install has no test mode (--root is only in a test build)");
        }
    } else {
        sys::Env::real()
    };
    let harness_action = args.first().map(|a| harness::ACTIONS.contains(&a.as_str())).unwrap_or(false);
    let clock_action = args.first().map(String::as_str) == Some(clock::ACTION);
    let package_action = args.first().map(String::as_str) == Some(package::ACTION);
    if package_action {
        if !package::is_call(&args[1..]) {
            return refuse(
                install::EXIT_USAGE,
                "usage: llmcf-install install_package plan|apply BUNDLE|- NAME[=VERSION]... (1 to 16 packages of the \
                 distribution by its naming rules)",
            );
        }
    } else if clock_action {
        if args.len() != 2 || !clock::is_arg(&args[1]) {
            return refuse(
                install::EXIT_USAGE,
                "usage: llmcf-install clock YYYY-MM-DDTHH:MM:SSZ (a UTC time from 2025 to 2099) or llmcf-install clock keep",
            );
        }
    } else if harness_action {
        let want = if ["harness-start", "harness-read", "harness-records"].contains(&args[0].as_str()) { 5 } else { 4 };
        let ok = args.len() == want
            && manifest::is_id(&args[2])
            && harness::is_run(&args[3])
            && match args[0].as_str() {
                "harness-start" => ["dry", "run", "run-yes"].contains(&args[4].as_str()),
                "harness-read" => {
                    !args[4].is_empty() && args[4].len() <= 15 && args[4].bytes().all(|c| c.is_ascii_digit())
                }
                "harness-records" => harness::is_records_value(&args[4]),
                _ => true,
            };
        if !ok {
            return refuse(
                install::EXIT_USAGE,
                "usage: llmcf-install harness-start BUNDLE COMPONENT RUN dry|run|run-yes, harness-read BUNDLE COMPONENT \
                 RUN OFFSET, harness-records BUNDLE COMPONENT RUN index|N.OFFSET.LENGTH, or harness-answer|harness-stop \
                 BUNDLE COMPONENT RUN",
            );
        }
    } else if args.len() != 2 || !install::ACTIONS.contains(&args[0].as_str()) {
        return refuse(
            install::EXIT_USAGE,
            "usage: llmcf-install plan|apply|status|rollback|uninstall|plan-uninstall BUNDLE",
        );
    }
    let (action, bid) = (args[0].as_str(), args[1].as_str());
    if !clock_action && !package_action && !manifest::is_id(bid) {
        return refuse(install::EXIT_USAGE, "not a bundle id");
    }
    println!("llmcf-bundle: helper llmcf-install {VERSION}");
    if !env.test && sys::effective_uid() != Some(0) {
        return refuse(install::EXIT_REFUSED, "llmcf-install runs only as root (through sudo from the board account)");
    }
    // The board account that called it through sudo: its bundle folder is where the kept manifest lies.
    let name = std::env::var("SUDO_USER").unwrap_or_default();
    let valid = !name.is_empty()
        && name.len() <= 32
        && name != "root"
        && name.bytes().next().map(|c| c.is_ascii_alphabetic() || c == b'_').unwrap_or(false)
        && name.bytes().all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c));
    if !valid {
        return refuse(install::EXIT_REFUSED, "run it with sudo as the framework's board account");
    }
    let account = match sys::account(&env, &name) {
        // (a test build run by root, as on some CI runners, has the test's own account uid 0)
        Some(a) if (a.uid != 0 || env.test) && sys::plain_abs(&a.home) => a,
        _ => return refuse(install::EXIT_REFUSED, "the board account has no plain home folder in the password file"),
    };
    if clock_action {
        return ExitCode::from(clock::run(&env, bid) as u8);
    }
    if package_action {
        return ExitCode::from(package::run(&env, &account, &args[1..]) as u8);
    }
    if harness_action {
        return ExitCode::from(harness::run(&env, action, bid, &account, &args[2..]) as u8);
    }
    ExitCode::from(install::run(&env, action, bid, account) as u8)
}
