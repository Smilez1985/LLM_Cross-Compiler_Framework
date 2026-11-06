//! A minimal DNS-SD responder over mDNS (RFC 6762/6763) for one service instance of `_llmcf-pair._tcp.local`, only
//! while the pairing window is open. It answers questions for the service, its instance or its host name (PTR, SRV,
//! TXT, A/AAAA) and nothing else, only from a local peer (local.rs), and ends with the window. A question from a port
//! other than 5353 (the framework's one-shot browse, RFC 6762 6.7 "legacy unicast") gets its answer by unicast to the
//! asker with the question repeated and its ID; a question from port 5353 gets its answer to the mDNS group on the
//! link it came from. The TXT record holds only "v=1": no name, no account, no address beyond the A/AAAA record of
//! the link the question came over.

use crate::local::peer_allowed;
use socket2::{Domain, InterfaceIndexOrAddress, Protocol, Socket, Type};
use std::fs;
use std::io;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr, SocketAddrV4, SocketAddrV6, UdpSocket};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

pub const SERVICE: &str = "_llmcf-pair._tcp.local";
pub const MDNS_PORT: u16 = 5353;
pub const GROUP_V4: Ipv4Addr = Ipv4Addr::new(224, 0, 0, 251);
pub const GROUP_V6: Ipv6Addr = Ipv6Addr::new(0xff02, 0, 0, 0, 0, 0, 0, 0xfb);
const T_A: u16 = 1;
const T_PTR: u16 = 12;
const T_TXT: u16 = 16;
const T_AAAA: u16 = 28;
const T_SRV: u16 = 33;
const T_ANY: u16 = 255;
const TTL: u32 = 120;
const LEGACY_TTL: u32 = 10;

/// One question of a query: its name (lower case, without the trailing dot) and type.
#[derive(Debug, PartialEq, Eq)]
pub struct Question {
    pub name: String,
    pub qtype: u16,
    pub raw: Vec<u8>,
}

fn read_name(packet: &[u8], mut pos: usize) -> Option<(String, usize)> {
    let mut labels: Vec<String> = Vec::new();
    let mut end = None;
    let mut jumps = 0;
    loop {
        let len = *packet.get(pos)? as usize;
        if len & 0xc0 == 0xc0 {
            let target = ((len & 0x3f) << 8) | *packet.get(pos + 1)? as usize;
            if end.is_none() {
                end = Some(pos + 2);
            }
            jumps += 1;
            if jumps > 16 || target >= packet.len() {
                return None;
            }
            pos = target;
            continue;
        }
        if len & 0xc0 != 0 {
            return None;
        }
        if len == 0 {
            let name = labels.join(".");
            return Some((name, end.unwrap_or(pos + 1)));
        }
        let label = packet.get(pos + 1..pos + 1 + len)?;
        labels.push(String::from_utf8_lossy(label).to_ascii_lowercase());
        if labels.len() > 32 {
            return None;
        }
        pos += 1 + len;
    }
}

/// The ID and the questions of a query; None for a response, a truncated or malformed packet.
pub fn parse_query(packet: &[u8]) -> Option<(u16, Vec<Question>)> {
    if packet.len() < 12 {
        return None;
    }
    let id = u16::from_be_bytes([packet[0], packet[1]]);
    let flags = u16::from_be_bytes([packet[2], packet[3]]);
    if flags & 0x8000 != 0 || flags & 0x7800 != 0 {
        return None; // a response, or not a standard query
    }
    let qd = u16::from_be_bytes([packet[4], packet[5]]) as usize;
    if qd == 0 || qd > 16 {
        return None;
    }
    let mut pos = 12;
    let mut out = Vec::with_capacity(qd);
    for _ in 0..qd {
        let (name, next) = read_name(packet, pos)?;
        let qtype = u16::from_be_bytes([*packet.get(next)?, *packet.get(next + 1)?]);
        let _class = u16::from_be_bytes([*packet.get(next + 2)?, *packet.get(next + 3)?]);
        let mut raw = Vec::new();
        write_name(&mut raw, &name);
        raw.extend_from_slice(&packet[next..next + 4]);
        out.push(Question { name, qtype, raw });
        pos = next + 4;
    }
    Some((id, out))
}

fn write_name(out: &mut Vec<u8>, name: &str) {
    for label in name.split('.').filter(|l| !l.is_empty()) {
        let bytes = label.as_bytes();
        out.push(bytes.len().min(63) as u8);
        out.extend_from_slice(&bytes[..bytes.len().min(63)]);
    }
    out.push(0);
}

fn record(out: &mut Vec<u8>, name: &str, rtype: u16, class: u16, ttl: u32, rdata: &[u8]) {
    write_name(out, name);
    out.extend_from_slice(&rtype.to_be_bytes());
    out.extend_from_slice(&class.to_be_bytes());
    out.extend_from_slice(&ttl.to_be_bytes());
    out.extend_from_slice(&(rdata.len() as u16).to_be_bytes());
    out.extend_from_slice(rdata);
}

/// The one service instance this board announces.
#[derive(Debug, Clone)]
pub struct Responder {
    pub instance: String,
    pub host: String,
    pub port: u16,
    pub loopback_is_link: bool,
}

impl Responder {
    pub fn instance_name(&self) -> String {
        format!("{}.{SERVICE}", self.instance)
    }

    pub fn host_name(&self) -> String {
        format!("{}.local", self.host)
    }

    fn concerns(&self, q: &Question) -> bool {
        match q.qtype {
            T_PTR | T_ANY if q.name == SERVICE => true,
            T_SRV | T_TXT | T_ANY if q.name == self.instance_name().to_ascii_lowercase() => true,
            T_A | T_AAAA | T_ANY if q.name == self.host_name().to_ascii_lowercase() => true,
            _ => false,
        }
    }

    /// The answer to `packet` from `src` (with `local`, this board's address on the link toward `src`), and where it
    /// goes; None when the packet is no question for this instance or `src` is not on a local link.
    pub fn answer(&self, packet: &[u8], src: SocketAddr, local: Option<IpAddr>) -> Option<(Vec<u8>, SocketAddr)> {
        if !peer_allowed(src.ip(), self.loopback_is_link) {
            return None;
        }
        let (id, questions) = parse_query(packet)?;
        let asked: Vec<&Question> = questions.iter().filter(|q| self.concerns(q)).collect();
        if asked.is_empty() {
            return None;
        }
        let legacy = src.port() != MDNS_PORT;
        let (ttl, flush) = if legacy { (LEGACY_TTL, 0) } else { (TTL, 0x8000) };
        let mut body = Vec::new();
        let mut count: u16 = 0;
        let instance = self.instance_name();
        let host = self.host_name();
        let mut rdata = Vec::new();
        write_name(&mut rdata, &instance);
        record(&mut body, SERVICE, T_PTR, 1, ttl, &rdata);
        count += 1;
        let mut srv = vec![0, 0, 0, 0];
        srv.extend_from_slice(&self.port.to_be_bytes());
        write_name(&mut srv, &host);
        record(&mut body, &instance, T_SRV, 1 | flush, ttl, &srv);
        count += 1;
        record(&mut body, &instance, T_TXT, 1 | flush, ttl, b"\x03v=1");
        count += 1;
        match local {
            Some(IpAddr::V4(a)) => {
                record(&mut body, &host, T_A, 1 | flush, ttl, &a.octets());
                count += 1;
            }
            Some(IpAddr::V6(a)) => {
                record(&mut body, &host, T_AAAA, 1 | flush, ttl, &a.octets());
                count += 1;
            }
            None => {}
        }
        let mut out = Vec::with_capacity(12 + body.len() + 64);
        out.extend_from_slice(&(if legacy { id } else { 0 }).to_be_bytes());
        out.extend_from_slice(&0x8400u16.to_be_bytes()); // a response, authoritative
        let qd: u16 = if legacy { asked.len() as u16 } else { 0 };
        out.extend_from_slice(&qd.to_be_bytes());
        out.extend_from_slice(&count.to_be_bytes());
        out.extend_from_slice(&0u16.to_be_bytes());
        out.extend_from_slice(&0u16.to_be_bytes());
        if legacy {
            for q in &asked {
                out.extend_from_slice(&q.raw);
            }
        }
        out.extend_from_slice(&body);
        let to = if legacy {
            src
        } else {
            match src {
                SocketAddr::V4(_) => SocketAddr::V4(SocketAddrV4::new(GROUP_V4, MDNS_PORT)),
                SocketAddr::V6(s) => SocketAddr::V6(SocketAddrV6::new(GROUP_V6, MDNS_PORT, 0, s.scope_id())),
            }
        };
        Some((out, to))
    }
}

/// This board's address on the link toward `peer` (the source address the system picks for it), or None.
pub fn local_address_toward(peer: SocketAddr) -> Option<IpAddr> {
    let bind: SocketAddr = match peer {
        SocketAddr::V4(_) => (Ipv4Addr::UNSPECIFIED, 0).into(),
        SocketAddr::V6(_) => (Ipv6Addr::UNSPECIFIED, 0).into(),
    };
    let s = UdpSocket::bind(bind).ok()?;
    s.connect(peer).ok()?;
    let ip = s.local_addr().ok()?.ip();
    if ip.is_unspecified() {
        None
    } else {
        Some(ip)
    }
}

/// The interface indexes of the board's network interfaces except loopback (/sys/class/net/*/ifindex).
pub fn interface_indexes() -> Vec<u32> {
    let mut out = Vec::new();
    if let Ok(entries) = fs::read_dir("/sys/class/net") {
        for e in entries.flatten() {
            if e.file_name() == "lo" {
                continue;
            }
            if let Ok(text) = fs::read_to_string(e.path().join("ifindex")) {
                if let Ok(i) = text.trim().parse::<u32>() {
                    out.push(i);
                }
            }
        }
    }
    out.sort_unstable();
    out
}

/// The mDNS sockets of the board: port 5353 on every interface (IPv4 and IPv6, next to a running Avahi through
/// address reuse), each joined to the mDNS group of every interface. A family the board has not is left out.
pub fn system_sockets() -> io::Result<Vec<UdpSocket>> {
    let mut out = Vec::new();
    let indexes = interface_indexes();
    let v4 = (|| -> io::Result<UdpSocket> {
        let s = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::UDP))?;
        s.set_reuse_address(true)?;
        s.set_reuse_port(true)?;
        s.bind(&SocketAddr::from((Ipv4Addr::UNSPECIFIED, MDNS_PORT)).into())?;
        s.set_multicast_ttl_v4(255)?;
        for i in &indexes {
            let _ = s.join_multicast_v4_n(&GROUP_V4, &InterfaceIndexOrAddress::Index(*i));
        }
        Ok(s.into())
    })();
    let v6 = (|| -> io::Result<UdpSocket> {
        let s = Socket::new(Domain::IPV6, Type::DGRAM, Some(Protocol::UDP))?;
        s.set_only_v6(true)?;
        s.set_reuse_address(true)?;
        s.set_reuse_port(true)?;
        s.bind(&SocketAddr::from((Ipv6Addr::UNSPECIFIED, MDNS_PORT)).into())?;
        s.set_multicast_hops_v6(255)?;
        for i in &indexes {
            let _ = s.join_multicast_v6(&GROUP_V6, *i);
        }
        Ok(s.into())
    })();
    let mut first_error = None;
    for s in [v4, v6] {
        match s {
            Ok(s) => out.push(s),
            Err(e) => first_error = first_error.or(Some(e)),
        }
    }
    if out.is_empty() {
        return Err(first_error.unwrap_or_else(|| io::Error::other("no mDNS socket")));
    }
    Ok(out)
}

/// Answers on `sockets` until `deadline` or until `stop` is set; then the sockets close and the board is silent.
pub fn serve(
    sockets: Vec<UdpSocket>,
    responder: Responder,
    deadline: Instant,
    stop: Arc<AtomicBool>,
) -> Vec<thread::JoinHandle<()>> {
    sockets
        .into_iter()
        .map(|socket| {
            let responder = responder.clone();
            let stop = stop.clone();
            thread::spawn(move || {
                let _ = socket.set_read_timeout(Some(Duration::from_millis(200)));
                let mut buf = [0u8; 1500];
                while !stop.load(Ordering::SeqCst) && Instant::now() < deadline {
                    let (n, src) = match socket.recv_from(&mut buf) {
                        Ok(got) => got,
                        Err(_) => continue,
                    };
                    let local = if responder.loopback_is_link && src.ip().is_loopback() {
                        Some(src.ip())
                    } else {
                        local_address_toward(src)
                    };
                    if let Some((reply, to)) = responder.answer(&buf[..n], src, local) {
                        let _ = socket.send_to(&reply, to);
                    }
                }
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn query(id: u16, name: &str, qtype: u16) -> Vec<u8> {
        let mut p = Vec::new();
        p.extend_from_slice(&id.to_be_bytes());
        p.extend_from_slice(&[0, 0, 0, 1, 0, 0, 0, 0, 0, 0]);
        write_name(&mut p, name);
        p.extend_from_slice(&qtype.to_be_bytes());
        p.extend_from_slice(&1u16.to_be_bytes());
        p
    }

    fn responder() -> Responder {
        Responder {
            instance: "llmcf-ab12".into(),
            host: "llmcf-pair-ab12".into(),
            port: 40123,
            loopback_is_link: false,
        }
    }

    #[test]
    fn a_browse_from_a_local_link_gets_ptr_srv_txt_and_address() {
        let src: SocketAddr = "169.254.7.8:50000".parse().unwrap();
        let local: IpAddr = "169.254.1.2".parse().unwrap();
        let (reply, to) = responder().answer(&query(0x1234, SERVICE, T_PTR), src, Some(local)).unwrap();
        assert_eq!(to, src, "a legacy question gets its answer by unicast");
        assert_eq!(&reply[..2], &0x1234u16.to_be_bytes());
        assert_eq!(u16::from_be_bytes([reply[4], reply[5]]), 1, "the question is repeated");
        assert_eq!(u16::from_be_bytes([reply[6], reply[7]]), 4);
        let text = String::from_utf8_lossy(&reply);
        assert!(text.contains("llmcf-ab12"));
        assert!(reply.windows(2).any(|w| w == 40123u16.to_be_bytes()));
        assert!(reply.windows(4).any(|w| w == [169, 254, 1, 2]));
    }

    #[test]
    fn a_question_from_port_5353_goes_to_the_group() {
        let src: SocketAddr = "[fe80::2%3]:5353".parse().unwrap();
        let (reply, to) = responder().answer(&query(7, SERVICE, T_PTR), src, None).unwrap();
        assert_eq!(&reply[..2], &[0, 0]);
        match to {
            SocketAddr::V6(s) => {
                assert_eq!(*s.ip(), GROUP_V6);
                assert_eq!(s.scope_id(), 3);
            }
            _ => panic!("not IPv6"),
        }
    }

    #[test]
    fn other_questions_and_peers_get_no_answer() {
        let local: SocketAddr = "192.168.1.5:50000".parse().unwrap();
        assert!(responder().answer(&query(1, "_http._tcp.local", T_PTR), local, None).is_none());
        assert!(responder().answer(&query(1, SERVICE, T_A), local, None).is_none());
        for public in ["203.0.113.5:50000", "[2001:db8::5]:50000", "127.0.0.1:50000", "100.64.1.1:5353"] {
            let src: SocketAddr = public.parse().unwrap();
            assert!(responder().answer(&query(1, SERVICE, T_PTR), src, None).is_none(), "{public}");
        }
        let mut response = query(1, SERVICE, T_PTR);
        response[2] = 0x84;
        assert!(responder().answer(&response, local, None).is_none(), "a response is never answered");
    }

    #[test]
    fn malformed_packets_are_ignored() {
        let local: SocketAddr = "192.168.1.5:50000".parse().unwrap();
        assert!(responder().answer(&[0u8; 5], local, None).is_none());
        let mut looped = vec![0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0];
        looped.extend_from_slice(&[0xc0, 12, 0, 12, 0, 1]); // a name that points at itself
        assert!(responder().answer(&looped, local, None).is_none());
        let mut cut = query(1, SERVICE, T_PTR);
        cut.truncate(cut.len() - 3);
        assert!(responder().answer(&cut, local, None).is_none());
    }

    #[test]
    fn names_are_matched_without_case_and_through_pointers() {
        let mut p = vec![0, 9, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0];
        write_name(&mut p, "_LLMCF-PAIR._TCP.LOCAL");
        p.extend_from_slice(&T_PTR.to_be_bytes());
        p.extend_from_slice(&1u16.to_be_bytes());
        let (_, qs) = parse_query(&p).unwrap();
        assert_eq!(qs[0].name, SERVICE);
        let src: SocketAddr = "10.0.0.9:40000".parse().unwrap();
        assert!(responder().answer(&p, src, None).is_some());
    }
}
