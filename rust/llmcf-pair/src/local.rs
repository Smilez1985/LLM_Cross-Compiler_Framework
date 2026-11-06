//! The local-link rule of the pairing: the same address rule as the framework's orchestrator/utils/locality.py
//! (rule a, without names and without the person's opt-outs): a peer is local when its address is private (RFC 1918:
//! 10/8, 172.16/12, 192.168/16; IPv6 ULA fc00::/7) or link-local (169.254/16, fe80::/10). Tailscale's ranges
//! (100.64/10 and its ULA fd7a:115c:a1e0::/48) are not local here: the pairing never goes through a VPN. IPv4 inside
//! IPv6 (::ffff:a.b.c.d) is judged as IPv4. The loopback address is the board itself, not the person's computer: it
//! counts only in the test build's loopback-as-link mode.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

/// What an address is for the pairing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Private,
    LinkLocal,
    Loopback,
    Vpn,
    Other,
}

fn v4_kind(ip: Ipv4Addr) -> Kind {
    let o = ip.octets();
    if o[0] == 100 && (o[1] & 0xc0) == 64 {
        return Kind::Vpn;
    }
    if ip.is_loopback() || ip.is_unspecified() {
        return Kind::Loopback;
    }
    if o[0] == 10 || (o[0] == 172 && (o[1] & 0xf0) == 16) || (o[0] == 192 && o[1] == 168) {
        return Kind::Private;
    }
    if o[0] == 169 && o[1] == 254 {
        return Kind::LinkLocal;
    }
    Kind::Other
}

fn v6_kind(ip: Ipv6Addr) -> Kind {
    if let Some(v4) = ip.to_ipv4_mapped() {
        return v4_kind(v4);
    }
    let s = ip.segments();
    if s[0] == 0xfd7a && s[1] == 0x115c && s[2] == 0xa1e0 {
        return Kind::Vpn;
    }
    if ip.is_loopback() || ip.is_unspecified() {
        return Kind::Loopback;
    }
    if (s[0] & 0xfe00) == 0xfc00 {
        return Kind::Private;
    }
    if (s[0] & 0xffc0) == 0xfe80 {
        return Kind::LinkLocal;
    }
    Kind::Other
}

/// The kind of `ip` (IPv4 inside IPv6 judged as IPv4).
pub fn kind(ip: IpAddr) -> Kind {
    match ip {
        IpAddr::V4(v4) => v4_kind(v4),
        IpAddr::V6(v6) => v6_kind(v6),
    }
}

/// True when a peer at `ip` may pair: private or link-local; loopback only with `loopback_is_link` (the test build).
pub fn peer_allowed(ip: IpAddr, loopback_is_link: bool) -> bool {
    match kind(ip) {
        Kind::Private | Kind::LinkLocal => true,
        Kind::Loopback => loopback_is_link,
        Kind::Vpn | Kind::Other => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    #[test]
    fn the_local_ranges_are_allowed() {
        for a in [
            "10.1.2.3",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.4.2",
            "169.254.10.20",
            "fe80::1",
            "fd12::5",
            "fc00::1",
            "::ffff:192.168.1.2",
            "::ffff:169.254.3.4",
        ] {
            assert!(peer_allowed(ip(a), false), "{a}");
        }
    }

    #[test]
    fn public_vpn_and_loopback_are_refused() {
        for a in [
            "192.0.2.10",
            "198.51.100.7",
            "203.0.113.9",
            "2001:db8::1",
            "172.32.0.1",
            "100.64.0.1",
            "100.127.255.254",
            "fd7a:115c:a1e0::1",
            "127.0.0.1",
            "::1",
            "0.0.0.0",
            "::",
            "224.0.0.251",
            "ff02::fb",
            "::ffff:203.0.113.1",
        ] {
            assert!(!peer_allowed(ip(a), false), "{a}");
        }
    }

    #[test]
    fn loopback_counts_only_in_the_test_mode() {
        assert!(peer_allowed(ip("127.0.0.1"), true));
        assert!(peer_allowed(ip("::1"), true));
        assert!(!peer_allowed(ip("203.0.113.1"), true));
    }
}
