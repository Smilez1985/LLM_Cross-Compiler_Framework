//! Capability names and the capability lists of setpriv's --inh-caps, --ambient-caps and --bounding-set: a comma
//! separated list of items, each `+NAME` or `-NAME`, NAME a capability name without the `cap_` prefix (any case) or
//! `all`; the items apply in order to the set the process has. No unsafe code here.
#![forbid(unsafe_code)]

/// The kernel's capability names by number (linux/capability.h), as setpriv (libcap-ng) spells them.
pub const NAMES: [&str; 41] = [
    "chown",
    "dac_override",
    "dac_read_search",
    "fowner",
    "fsetid",
    "kill",
    "setgid",
    "setuid",
    "setpcap",
    "linux_immutable",
    "net_bind_service",
    "net_broadcast",
    "net_admin",
    "net_raw",
    "ipc_lock",
    "ipc_owner",
    "sys_module",
    "sys_rawio",
    "sys_chroot",
    "sys_ptrace",
    "sys_pacct",
    "sys_admin",
    "sys_boot",
    "sys_nice",
    "sys_resource",
    "sys_time",
    "sys_tty_config",
    "mknod",
    "lease",
    "audit_write",
    "audit_control",
    "setfcap",
    "mac_override",
    "mac_admin",
    "syslog",
    "wake_alarm",
    "block_suspend",
    "audit_read",
    "perfmon",
    "bpf",
    "checkpoint_restore",
];

#[cfg(test)]
pub const CAP_SETGID: u32 = 6;
#[cfg(test)]
pub const CAP_SETUID: u32 = 7;
pub const CAP_SETPCAP: u32 = 8;

/// One item of a capability list: raise (`+`) or drop (`-`) one capability or all of them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Item {
    Add(Which),
    Drop(Which),
}

/// The capability an item names.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Which {
    All,
    One(u32),
}

/// The capability number of a name (any case, without `cap_`), None for a name the kernel does not have.
pub fn number(name: &str) -> Option<u32> {
    let name = name.to_ascii_lowercase();
    NAMES.iter().position(|n| *n == name).map(|i| i as u32)
}

/// A capability list as setpriv takes it, or the plain reason it is not one.
pub fn parse_list(text: &str) -> Result<Vec<Item>, String> {
    if text.is_empty() {
        return Err("an empty capability list".to_string());
    }
    let mut items = Vec::new();
    for part in text.split(',') {
        let (add, name) = match part.as_bytes().first() {
            Some(b'+') => (true, &part[1..]),
            Some(b'-') => (false, &part[1..]),
            _ => {
                return Err(format!(
                    "bad capability string '{part}': each item starts with + or -"
                ))
            }
        };
        let which = if name.eq_ignore_ascii_case("all") {
            Which::All
        } else {
            match number(name) {
                Some(n) => Which::One(n),
                None => return Err(format!("unknown capability '{name}'")),
            }
        };
        items.push(if add {
            Item::Add(which)
        } else {
            Item::Drop(which)
        });
    }
    Ok(items)
}

/// The bit of capability `n` in a 64-bit capability mask.
pub fn bit(n: u32) -> u64 {
    1u64 << n
}

/// Every capability 0..=last as a mask.
pub fn all(last: u32) -> u64 {
    if last >= 63 {
        u64::MAX
    } else {
        (1u64 << (last + 1)) - 1
    }
}

/// The set `items` make of `start` (every capability up to `last` for `all`).
pub fn apply(items: &[Item], start: u64, last: u32) -> u64 {
    items.iter().fold(start, |set, item| match *item {
        Item::Add(Which::All) => set | all(last),
        Item::Drop(Which::All) => 0,
        Item::Add(Which::One(n)) => set | bit(n),
        Item::Drop(Which::One(n)) => set & !bit(n),
    })
}

/// The kernel's highest capability number (/proc/sys/kernel/cap_last_cap), the table's last when it cannot be read.
pub fn last_cap() -> u32 {
    std::fs::read_to_string("/proc/sys/kernel/cap_last_cap")
        .ok()
        .and_then(|t| t.trim().parse::<u32>().ok())
        .filter(|n| *n < 64)
        .unwrap_or(NAMES.len() as u32 - 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_match_the_kernel_numbers() {
        assert_eq!(number("setgid"), Some(CAP_SETGID));
        assert_eq!(number("SETUID"), Some(CAP_SETUID));
        assert_eq!(number("setpcap"), Some(CAP_SETPCAP));
        assert_eq!(number("checkpoint_restore"), Some(40));
        assert_eq!(number("cap_setuid"), None);
        assert_eq!(number("nonsense"), None);
    }

    #[test]
    fn the_lists_the_layer_uses_parse() {
        assert_eq!(
            parse_list("-all,+setuid,+setgid").unwrap(),
            vec![
                Item::Drop(Which::All),
                Item::Add(Which::One(7)),
                Item::Add(Which::One(6))
            ]
        );
        assert_eq!(parse_list("-all").unwrap(), vec![Item::Drop(Which::All)]);
        assert_eq!(parse_list("+ALL").unwrap(), vec![Item::Add(Which::All)]);
    }

    #[test]
    fn bad_lists_are_refused() {
        for text in [
            "",
            "all",
            "setuid",
            "+",
            "-all,",
            "+setuid,,+setgid",
            "+cap_setuid",
            "*all",
            "+setuid -setgid",
        ] {
            assert!(parse_list(text).is_err(), "{text:?} was taken");
        }
    }

    #[test]
    fn items_apply_in_order() {
        let items = parse_list("-all,+setuid,+setgid").unwrap();
        assert_eq!(apply(&items, all(40), 40), bit(6) | bit(7));
        assert_eq!(apply(&parse_list("+setuid,-all").unwrap(), 0, 40), 0);
        assert_eq!(
            apply(&parse_list("+all,-setuid").unwrap(), 0, 40),
            all(40) & !bit(7)
        );
        assert_eq!(all(40).count_ones(), 41);
        assert_eq!(all(63), u64::MAX);
    }
}
