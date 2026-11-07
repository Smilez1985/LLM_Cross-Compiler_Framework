//! Who a request comes from, for the queue's fair order (queue::ClientKey): the client's own bearer key when it sent
//! one that is not the gateway's own token (a digest of it, the key itself is never kept), else for a client on the
//! board the account the kernel names for its socket (the uid column of the row in /proc/net/tcp or tcp6 whose
//! local end is the client's address and port and whose remote end is the gateway's), else the client's address.

use crate::queue::ClientKey;
use std::net::{IpAddr, SocketAddr};
use std::path::Path;

/// The most of a /proc/net table read (a small board has a few hundred sockets at most).
const MAX_TABLE: u64 = 4 << 20;

/// The key of a request from `peer` to the gateway's `local` end with this Authorization header.
pub fn client_key(auth: Option<&str>, own_token: Option<&str>, peer: SocketAddr, local: SocketAddr) -> ClientKey {
    if let Some(given) = auth.map(str::trim).filter(|a| !a.is_empty()) {
        let own = own_token.map(|t| format!("Bearer {t}"));
        if own.as_deref() != Some(given) {
            let d = ring::digest::digest(&ring::digest::SHA256, given.as_bytes());
            let mut key = [0u8; 16];
            key.copy_from_slice(&d.as_ref()[..16]);
            return ClientKey::Bearer(key);
        }
    }
    if peer.ip().is_loopback() {
        if let Some(uid) = socket_uid(Path::new("/proc/net"), peer, local) {
            return ClientKey::Uid(uid);
        }
    }
    ClientKey::Addr(peer.ip())
}

/// The kernel's text of an address in /proc/net/tcp{,6}: each 32-bit word of it as the host stores it, in hex.
fn proc_addr(addr: SocketAddr) -> String {
    let words: Vec<[u8; 4]> = match addr.ip() {
        IpAddr::V4(v4) => vec![v4.octets()],
        IpAddr::V6(v6) => v6.octets().chunks(4).map(|c| [c[0], c[1], c[2], c[3]]).collect(),
    };
    let mut out: String = words.iter().map(|w| format!("{:08X}", u32::from_ne_bytes(*w))).collect();
    out.push_str(&format!(":{:04X}", addr.port()));
    out
}

/// The uid owning the socket whose local end is `peer` and whose remote end is `local` (the client's socket).
pub fn socket_uid(dir: &Path, peer: SocketAddr, local: SocketAddr) -> Option<u32> {
    let file = if peer.is_ipv4() { "tcp" } else { "tcp6" };
    let text = read_capped(&dir.join(file))?;
    uid_in_table(&text, &proc_addr(peer), &proc_addr(local))
}

fn read_capped(path: &Path) -> Option<String> {
    use std::io::Read;
    let mut out = String::new();
    std::fs::File::open(path).ok()?.take(MAX_TABLE).read_to_string(&mut out).ok()?;
    Some(out)
}

/// The uid of the table's row with these two ends (the columns: sl local rem st queues tr retrnsmt uid ...).
pub fn uid_in_table(text: &str, local_end: &str, remote_end: &str) -> Option<u32> {
    text.lines().skip(1).find_map(|line| {
        let cols: Vec<&str> = line.split_whitespace().collect();
        if cols.len() > 7 && cols[1] == local_end && cols[2] == remote_end {
            cols[7].parse().ok()
        } else {
            None
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_kernel_names_the_account_of_a_client_on_the_board() {
        let peer: SocketAddr = "127.0.0.1:51000".parse().unwrap();
        let gw: SocketAddr = "127.0.0.1:8088".parse().unwrap();
        let (p, g) = (proc_addr(peer), proc_addr(gw));
        assert!(p.ends_with(":C738") && g.ends_with(":1F98"), "{p} {g}");
        let table = format!(
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n\
             \x20  0: {g} 00000000:0000 0A 00000000:00000000 00:00000000 00000000   990        0 11 1\n\
             \x20  1: {g} {p} 01 00000000:00000000 00:00000000 00000000   990        0 12 1\n\
             \x20  2: {p} {g} 01 00000000:00000000 00:00000000 00000000  1234        0 13 1\n"
        );
        assert_eq!(uid_in_table(&table, &p, &g), Some(1234));
        assert_eq!(uid_in_table(&table, &g, "00000000:0000"), Some(990));
        assert_eq!(uid_in_table(&table, &p, &proc_addr("127.0.0.1:9".parse().unwrap())), None);
    }

    #[test]
    fn the_live_table_finds_this_tests_own_socket() {
        // a real loopback connection: the client's socket belongs to this test's own account
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let local = listener.local_addr().unwrap();
        let client = std::net::TcpStream::connect(local).unwrap();
        let peer = client.local_addr().unwrap();
        if let Some(uid) = socket_uid(Path::new("/proc/net"), peer, local) {
            let me = std::fs::metadata("/proc/self").map(|m| std::os::unix::fs::MetadataExt::uid(&m)).unwrap();
            assert_eq!(uid, me);
        }
    }

    #[test]
    fn a_bearer_key_wins_except_the_gateways_own_token() {
        let peer: SocketAddr = "198.51.100.7:40000".parse().unwrap();
        let local: SocketAddr = "198.51.100.1:8088".parse().unwrap();
        let a = client_key(Some("Bearer harness-a"), Some("tok"), peer, local);
        let b = client_key(Some("Bearer harness-b"), Some("tok"), peer, local);
        assert!(matches!(a, ClientKey::Bearer(_)) && a != b);
        assert_eq!(client_key(Some("Bearer harness-a"), None, peer, local), a);
        assert_eq!(client_key(Some("Bearer tok"), Some("tok"), peer, local), ClientKey::Addr(peer.ip()));
        assert_eq!(client_key(None, None, peer, local), ClientKey::Addr(peer.ip()));
    }
}
