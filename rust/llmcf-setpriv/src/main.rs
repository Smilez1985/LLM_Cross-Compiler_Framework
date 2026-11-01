//! llmcf-setpriv: the LLM Cross-Compiler Framework's static uid switch for the runtime layer of every build
//! container (orchestrator/runtime_layer/: entrypoint.sh hands the stage runner its ids and two rights, stage_user.sh
//! starts each stage module as the stage account without any right).
//!
//! It takes exactly the util-linux setpriv options those scripts use, with the same names and meaning (args.rs), and
//! refuses every other option. In this order: the supplementary groups (setgroups), the group ids (setresgid), the
//! user ids (setresuid; PR_SET_KEEPCAPS keeps the permitted set across it), no_new_privs, then the capability sets
//! (bounding set, inheritable set, ambient set). Before the program starts it checks that the ids and groups are
//! exactly the asked ones, that no_new_privs is set when asked, that the capability sets are the asked ones, and,
//! when the new ids are not root, that becoming root again fails. Any difference aborts before the program runs.
//! The program is then executed directly (execvp, as setpriv: the PATH lookup of the C library for a name without a
//! slash; never a shell).
//!
//! Exit status like setpriv: 1 for a refused command line, 127 when a switch step or a check fails or the program is
//! not found, 126 when the program cannot be executed.
#![deny(unsafe_code)]

mod args;
mod caps;
mod sys;

use std::ffi::OsString;
use std::os::unix::process::CommandExt;
use std::process::{exit, Command};

use args::{Options, Parsed};
use caps::{Item, Which};

const NAME: &str = "llmcf-setpriv";
const EXIT_USAGE: i32 = 1;
const EXIT_PRIVERR: i32 = 127;
const EXIT_CANNOT_EXEC: i32 = 126;

fn fail(code: i32, text: &str) -> ! {
    eprintln!("{NAME}: {text}");
    exit(code)
}

fn step<T>(what: &str, result: std::io::Result<T>) -> T {
    match result {
        Ok(v) => v,
        Err(e) => fail(EXIT_PRIVERR, &format!("{what} failed: {e}")),
    }
}

/// The ambient set as a mask (one PR_CAP_AMBIENT_IS_SET per capability up to `last`).
fn ambient_set(last: u32) -> u64 {
    (0..=last)
        .filter(|c| step("reading the ambient capabilities", sys::ambient_has(*c)))
        .fold(0, |m, c| m | caps::bit(c))
}

/// The bounding set as a mask.
fn bounding_set(last: u32) -> u64 {
    (0..=last)
        .filter(|c| step("reading the bounding set", sys::bounding_has(*c)))
        .fold(0, |m, c| m | caps::bit(c))
}

/// Whether the three ids are all other than root.
fn not_root(ids: (u32, u32, u32)) -> bool {
    ids.0 != 0 && ids.1 != 0 && ids.2 != 0
}

/// The switch itself, in setpriv's meaning; returns only when every step and every check passed.
fn switch(opts: &Options) {
    let last = caps::last_cap();
    if opts.reuid.is_some() {
        step(
            "keeping the capabilities across the uid switch (PR_SET_KEEPCAPS)",
            sys::set_keepcaps(),
        );
    }
    if let Some(groups) = &opts.groups {
        step("setgroups", sys::setgroups(groups));
    } else if opts.clear_groups {
        step("setgroups", sys::setgroups(&[]));
    }
    if let Some(gid) = opts.regid {
        step("setresgid", sys::setresgid(gid));
    }
    if let Some(uid) = opts.reuid {
        step("setresuid", sys::setresuid(uid));
    }
    if opts.no_new_privs {
        step("setting no_new_privs", sys::set_no_new_privs());
    }

    let mut expected_bounding = None;
    if let Some(items) = &opts.bounding_set {
        let now = bounding_set(last);
        let wanted = caps::apply(items, now, last);
        if wanted & !now != 0 {
            fail(
                EXIT_PRIVERR,
                "the bounding set cannot gain a capability it does not have",
            );
        }
        // Dropping needs CAP_SETPCAP in the effective set: raised from the permitted set for these calls only (the
        // switch away from uid 0 empties the effective set)
        let before = step("capget", sys::capget());
        let setpcap = caps::bit(caps::CAP_SETPCAP);
        if before.effective & setpcap == 0 && before.permitted & setpcap != 0 {
            step(
                "capset",
                sys::capset(sys::CapSets {
                    effective: before.effective | setpcap,
                    ..before
                }),
            );
        }
        for cap in (0..=last).filter(|c| now & caps::bit(*c) != 0 && wanted & caps::bit(*c) == 0) {
            step("dropping from the bounding set", sys::bounding_drop(cap));
        }
        let mut after = step("capget", sys::capget());
        after.effective = before.effective & after.permitted;
        step("capset", sys::capset(after));
        expected_bounding = Some(wanted);
    }

    let mut expected_inheritable = None;
    if let Some(items) = &opts.inh_caps {
        let now = step("capget", sys::capget());
        let wanted = caps::apply(items, now.inheritable, last);
        step(
            "setting the inheritable capabilities",
            sys::capset(sys::CapSets {
                inheritable: wanted,
                ..now
            }),
        );
        expected_inheritable = Some(wanted);
    }

    let mut expected_ambient = None;
    if let Some(items) = &opts.ambient_caps {
        let mut wanted = ambient_set(last);
        for item in items {
            match *item {
                Item::Drop(Which::All) => {
                    step("clearing the ambient capabilities", sys::ambient_clear());
                    wanted = 0;
                }
                Item::Add(Which::All) => {
                    for cap in 0..=last {
                        step("raising an ambient capability", sys::ambient_raise(cap));
                    }
                    wanted = caps::all(last);
                }
                Item::Add(Which::One(cap)) => {
                    step("raising an ambient capability", sys::ambient_raise(cap));
                    wanted |= caps::bit(cap);
                }
                Item::Drop(Which::One(cap)) => {
                    step("lowering an ambient capability", sys::ambient_lower(cap));
                    wanted &= !caps::bit(cap);
                }
            }
        }
        expected_ambient = Some(wanted);
    }

    let uids = step("getresuid", sys::getresuid());
    let gids = step("getresgid", sys::getresgid());
    if not_root(uids) {
        // Exactly what execve gives a program without file capabilities under these ids: the ambient set becomes
        // the permitted and effective set, everything else goes. Done here already, so the checks below see the
        // rights the program starts with.
        let now = step("capget", sys::capget());
        let ambient = ambient_set(last);
        step(
            "dropping the capabilities the program will not have",
            sys::capset(sys::CapSets {
                effective: 0,
                permitted: ambient,
                inheritable: now.inheritable,
            }),
        );
    }
    verify(
        opts,
        uids,
        gids,
        last,
        expected_bounding,
        expected_inheritable,
        expected_ambient,
    );
}

/// The checks after the switch: anything other than what was asked aborts before the program runs.
fn verify(
    opts: &Options,
    uids: (u32, u32, u32),
    gids: (u32, u32, u32),
    last: u32,
    bounding: Option<u64>,
    inheritable: Option<u64>,
    ambient: Option<u64>,
) {
    let wrong = |what: &str| {
        fail(
            EXIT_PRIVERR,
            &format!("the switch did not give the asked {what}; nothing was run"),
        )
    };
    if let Some(uid) = opts.reuid {
        if uids != (uid, uid, uid) {
            wrong("user ids");
        }
    }
    if let Some(gid) = opts.regid {
        if gids != (gid, gid, gid) {
            wrong("group ids");
        }
    }
    let asked_groups = match (&opts.groups, opts.clear_groups) {
        (Some(list), _) => Some(list.clone()),
        (None, true) => Some(Vec::new()),
        _ => None,
    };
    if let Some(mut asked) = asked_groups {
        let mut have = step("getgroups", sys::getgroups());
        asked.sort_unstable();
        asked.dedup();
        have.sort_unstable();
        have.dedup();
        if have != asked {
            wrong("supplementary groups");
        }
    }
    if opts.no_new_privs && !step("reading no_new_privs", sys::no_new_privs()) {
        wrong("no_new_privs");
    }
    if let Some(mask) = bounding {
        if bounding_set(last) != mask {
            wrong("bounding set");
        }
    }
    if let Some(mask) = inheritable {
        if step("capget", sys::capget()).inheritable != mask {
            wrong("inheritable capabilities");
        }
    }
    if let Some(mask) = ambient {
        if ambient_set(last) != mask {
            wrong("ambient capabilities");
        }
    }
    if not_root(uids) && sys::setresuid(0).is_ok() {
        fail(
            EXIT_PRIVERR,
            "becoming root again did not fail after the switch; nothing was run",
        );
    }
    if not_root(uids) && gids.0 != 0 && gids.1 != 0 && gids.2 != 0 && sys::setresgid(0).is_ok() {
        fail(
            EXIT_PRIVERR,
            "taking the root group again did not fail after the switch; nothing was run",
        );
    }
}

fn main() {
    let argv: Vec<OsString> = std::env::args_os().skip(1).collect();
    let opts = match args::parse(argv) {
        Ok(Parsed::Run(opts)) => opts,
        Ok(Parsed::Version) => {
            println!("{NAME} {}", env!("CARGO_PKG_VERSION"));
            return;
        }
        Ok(Parsed::Help) => {
            print!("{}", args::USAGE);
            return;
        }
        Err(text) => fail(EXIT_USAGE, &format!("{text}\nTry '{NAME} --help'.")),
    };
    switch(&opts);
    let error = Command::new(&opts.command[0])
        .args(&opts.command[1..])
        .exec();
    let code = if error.kind() == std::io::ErrorKind::NotFound {
        EXIT_PRIVERR
    } else {
        EXIT_CANNOT_EXEC
    };
    fail(
        code,
        &format!(
            "failed to execute {}: {error}",
            opts.command[0].to_string_lossy()
        ),
    );
}
