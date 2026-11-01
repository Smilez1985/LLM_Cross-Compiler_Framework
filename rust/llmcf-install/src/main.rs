//! llmcf-install: the root helper of the LLM Cross-Compiler Framework's board bundle.
//!
//! `sudo llmcf-install ACTION BUNDLE` (ACTION plan, apply, status, rollback, uninstall or plan-uninstall) carries out
//! ONLY the typed root actions of the bundle's signed manifest (create_user/remove_user, install_unit/remove_unit,
//! set_cgroup/remove_cgroup, place_root_file/remove_root_file) for the board account that called it through sudo.
//! The manifest is the one the unprivileged installer kept in the account's bundle folder; its signature is checked
//! here against the root-owned signer files (/etc/llmcf/allowed_signers, else /etc/llmcf/signing_key.pem) and
//! nothing the manifest says is trusted before. No network, no shell, no path from the environment; a test build
//! (cargo feature test-root, never in a release) takes `--root PREFIX` first.
//!
//! Exit status: 0 done (also plan, status, absent), 2 not a call it takes, 3 refused (nothing changed), 5 a step
//! failed (the journal says what was done: roll back or run it again).
#![forbid(unsafe_code)]

mod install;
mod manifest;
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
    if args.len() != 2 || !install::ACTIONS.contains(&args[0].as_str()) {
        return refuse(
            install::EXIT_USAGE,
            "usage: llmcf-install plan|apply|status|rollback|uninstall|plan-uninstall BUNDLE",
        );
    }
    let (action, bid) = (args[0].as_str(), args[1].as_str());
    if !manifest::is_id(bid) {
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
        Some(a) if a.uid != 0 && sys::plain_abs(&a.home) => a,
        _ => return refuse(install::EXIT_REFUSED, "the board account has no plain home folder in the password file"),
    };
    ExitCode::from(install::run(&env, action, bid, account) as u8)
}
