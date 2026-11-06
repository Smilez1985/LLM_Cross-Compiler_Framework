//! The board's SSH host keys as the pairing sends them: each public key line of DIR/ssh_host_*_key.pub (type and
//! base64 blob only, never its comment, which may name the machine) with its OpenSSH fingerprint
//! "SHA256:" + base64(sha256(blob)) without padding, as `ssh-keygen -lf` shows it.

use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;

const B64: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const KEY_TYPES: &[&str] =
    &["ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa"];

pub fn b64_encode(data: &[u8], pad: bool) -> String {
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let n = chunk.len();
        let b = [chunk[0], if n > 1 { chunk[1] } else { 0 }, if n > 2 { chunk[2] } else { 0 }];
        let v = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for i in 0..4 {
            if i <= n {
                out.push(B64[((v >> (18 - 6 * i)) & 63) as usize] as char);
            } else if pad {
                out.push('=');
            }
        }
    }
    out
}

pub fn b64_decode(text: &str) -> Option<Vec<u8>> {
    let t = text.trim_end_matches('=');
    if t.len() % 4 == 1 {
        return None;
    }
    let mut out = Vec::with_capacity(t.len() * 3 / 4);
    let mut acc: u32 = 0;
    let mut bits = 0;
    for c in t.bytes() {
        let v = B64.iter().position(|&x| x == c)? as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Some(out)
}

/// One host key: its type, the public key line without comment ("TYPE BLOB") and its fingerprint.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostKey {
    pub key_type: String,
    pub line: String,
    pub fingerprint: String,
}

/// The host key of one public key line, or None for a line that is not a known key type with a base64 blob that
/// names the same type.
pub fn parse_line(text: &str) -> Option<HostKey> {
    let mut words = text.split_whitespace();
    let key_type = words.next()?;
    let blob = words.next()?;
    if !KEY_TYPES.contains(&key_type) || blob.len() > 4096 {
        return None;
    }
    let raw = b64_decode(blob)?;
    // the blob starts with the key type as an SSH string
    let n = u32::from_be_bytes(raw.get(..4)?.try_into().ok()?) as usize;
    if raw.get(4..4 + n)? != key_type.as_bytes() {
        return None;
    }
    let digest = Sha256::digest(&raw);
    Some(HostKey {
        key_type: key_type.to_string(),
        line: format!("{key_type} {blob}"),
        fingerprint: format!("SHA256:{}", b64_encode(&digest, false)),
    })
}

/// Every host key in `dir` (ssh_host_*_key.pub), sorted by file name; unreadable or odd files are left out.
pub fn read_dir(dir: &Path) -> Vec<HostKey> {
    let mut names: Vec<String> = match fs::read_dir(dir) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .filter_map(|e| e.file_name().into_string().ok())
            .filter(|n| n.starts_with("ssh_host_") && n.ends_with("_key.pub"))
            .collect(),
        Err(_) => return Vec::new(),
    };
    names.sort();
    names
        .iter()
        .filter_map(|n| fs::read_to_string(dir.join(n)).ok())
        .filter_map(|text| text.lines().next().and_then(parse_line))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn base64_round_trips() {
        for data in [&b""[..], b"f", b"fo", b"foo", b"foob", b"fooba", b"foobar"] {
            assert_eq!(b64_decode(&b64_encode(data, true)).unwrap(), data);
            assert_eq!(b64_decode(&b64_encode(data, false)).unwrap(), data);
        }
        assert_eq!(b64_encode(b"foobar", true), "Zm9vYmFy");
        assert_eq!(b64_encode(b"fo", true), "Zm8=");
        assert_eq!(b64_encode(b"fo", false), "Zm8");
        assert!(b64_decode("Z").is_none());
        assert!(b64_decode("Zm9v!").is_none());
    }

    #[test]
    fn a_key_line_gives_the_openssh_fingerprint() {
        // an Ed25519 key blob: string "ssh-ed25519", then a 32-byte key of 0x01 bytes
        let mut blob = Vec::new();
        blob.extend_from_slice(&11u32.to_be_bytes());
        blob.extend_from_slice(b"ssh-ed25519");
        blob.extend_from_slice(&32u32.to_be_bytes());
        blob.extend_from_slice(&[1u8; 32]);
        let line = format!("ssh-ed25519 {} root@board.example.com", b64_encode(&blob, true));
        let key = parse_line(&line).unwrap();
        assert_eq!(key.key_type, "ssh-ed25519");
        assert!(!key.line.contains("example.com"));
        let want = format!("SHA256:{}", b64_encode(&Sha256::digest(&blob), false));
        assert_eq!(key.fingerprint, want);
        assert!(!key.fingerprint.ends_with('='));
    }

    #[test]
    fn odd_lines_are_left_out() {
        assert!(parse_line("").is_none());
        assert!(parse_line("ssh-dss AAAA").is_none());
        assert!(parse_line("ssh-ed25519 !!!").is_none());
        // a blob that names another type than its line
        let mut blob = Vec::new();
        blob.extend_from_slice(&7u32.to_be_bytes());
        blob.extend_from_slice(b"ssh-rsa");
        assert!(parse_line(&format!("ssh-ed25519 {}", b64_encode(&blob, true))).is_none());
    }
}
