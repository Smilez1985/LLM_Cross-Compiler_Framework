//! The framework's board signature (SSHSIG, PROTOCOL.sshsig) checked here, without ssh-keygen or openssl: an
//! Ed25519 signature in the namespace `llmcf-board` over the sha512 of the signed bytes, by a key the board's
//! ROOT-OWNED signer files name (the allowed_signers line with principal `llmcf`, or its PEM twin). The signature's
//! own public key is only compared with those keys, never trusted by itself.

use ed25519_dalek::{Signature, VerifyingKey};
use sha2::{Digest, Sha256, Sha512};

pub const NAMESPACE: &str = "llmcf-board";
pub const PRINCIPAL: &str = "llmcf";
const MAGIC: &[u8] = b"SSHSIG";
const KEY_TYPE: &[u8] = b"ssh-ed25519";
/// The DER prefix of an Ed25519 SubjectPublicKeyInfo (RFC 8410): the 32 key bytes follow.
const SPKI_ED25519: [u8; 12] = [0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00];

/// Standard base64 (RFC 4648, with padding), refusing every other character.
pub fn b64decode(text: &str) -> Option<Vec<u8>> {
    fn val(c: u8) -> Option<u32> {
        match c {
            b'A'..=b'Z' => Some((c - b'A') as u32),
            b'a'..=b'z' => Some((c - b'a' + 26) as u32),
            b'0'..=b'9' => Some((c - b'0' + 52) as u32),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    }
    let bytes = text.as_bytes();
    if bytes.is_empty() || bytes.len() % 4 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    for (i, chunk) in bytes.chunks(4).enumerate() {
        let last = i == bytes.len() / 4 - 1;
        let pad = chunk.iter().rev().take_while(|&&c| c == b'=').count();
        if pad > 2 || (pad > 0 && !last) {
            return None;
        }
        let mut acc: u32 = 0;
        for &c in &chunk[..4 - pad] {
            acc = (acc << 6) | val(c)?;
        }
        acc <<= 6 * pad as u32;
        let three = [(acc >> 16) as u8, (acc >> 8) as u8, acc as u8];
        out.extend_from_slice(&three[..3 - pad]);
    }
    Some(out)
}

fn take_string<'a>(data: &mut &'a [u8]) -> Option<&'a [u8]> {
    if data.len() < 4 {
        return None;
    }
    let n = u32::from_be_bytes([data[0], data[1], data[2], data[3]]) as usize;
    if data.len() < 4 + n {
        return None;
    }
    let s = &data[4..4 + n];
    *data = &data[4 + n..];
    Some(s)
}

fn ssh_string(out: &mut Vec<u8>, s: &[u8]) {
    out.extend_from_slice(&(s.len() as u32).to_be_bytes());
    out.extend_from_slice(s);
}

/// The 32 key bytes of an `ssh-ed25519` public key blob.
fn ed25519_of_blob(blob: &[u8]) -> Option<[u8; 32]> {
    let mut rest = blob;
    let kind = take_string(&mut rest)?;
    let key = take_string(&mut rest)?;
    if kind != KEY_TYPE || key.len() != 32 || !rest.is_empty() {
        return None;
    }
    key.try_into().ok()
}

/// The keys of an allowed_signers file that may sign in the framework's namespace: lines of the principal `llmcf`
/// whose options are exactly a namespaces= list naming `llmcf-board` (no cert-authority, no validity window), of
/// type ssh-ed25519. Every other line is skipped.
pub fn allowed_keys(text: &str) -> Vec<[u8; 32]> {
    let mut keys = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let words: Vec<&str> = line.split_whitespace().collect();
        let Some(at) = words.iter().position(|w| *w == "ssh-ed25519") else { continue };
        if at < 2 || at + 1 >= words.len() {
            continue;
        }
        if !words[0].split(',').any(|p| p == PRINCIPAL) {
            continue;
        }
        let options = &words[1..at];
        let ok = options.len() == 1
            && options[0]
                .strip_prefix("namespaces=\"")
                .and_then(|r| r.strip_suffix('"'))
                .map(|list| list.split(',').any(|n| n == NAMESPACE))
                .unwrap_or(false);
        if !ok {
            continue;
        }
        if let Some(key) = b64decode(words[at + 1]).and_then(|b| ed25519_of_blob(&b)) {
            keys.push(key);
        }
    }
    keys
}

/// The key of a PEM SubjectPublicKeyInfo of an Ed25519 key (signing_key.pem), or None.
pub fn pem_key(text: &str) -> Option<[u8; 32]> {
    let lines: Vec<&str> = text.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    if lines.len() < 3
        || lines[0] != "-----BEGIN PUBLIC KEY-----"
        || lines[lines.len() - 1] != "-----END PUBLIC KEY-----"
    {
        return None;
    }
    let der = b64decode(&lines[1..lines.len() - 1].concat())?;
    if der.len() != 44 || der[..12] != SPKI_ED25519 {
        return None;
    }
    der[12..].try_into().ok()
}

#[derive(Debug, PartialEq, Eq)]
pub enum SigError {
    Damaged,
    OtherKey,
    OtherPurpose,
    Mismatch,
}

impl SigError {
    pub fn text(&self) -> &'static str {
        match self {
            SigError::Damaged => "its signature is damaged or not an SSHSIG signature",
            SigError::OtherKey => "it is not signed with the framework's signing key this board trusts",
            SigError::OtherPurpose => "its signature was made for another purpose (namespace or hash)",
            SigError::Mismatch => "its signature does not match: the file was changed",
        }
    }
}

/// Checks `armored` (an SSHSIG signature as ssh-keygen -Y sign writes it) over `data` against `trusted` keys only.
pub fn verify(data: &[u8], armored: &str, trusted: &[[u8; 32]]) -> Result<(), SigError> {
    let lines: Vec<&str> = armored.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    if lines.len() < 3
        || lines[0] != "-----BEGIN SSH SIGNATURE-----"
        || lines[lines.len() - 1] != "-----END SSH SIGNATURE-----"
    {
        return Err(SigError::Damaged);
    }
    let blob = b64decode(&lines[1..lines.len() - 1].concat()).ok_or(SigError::Damaged)?;
    if blob.len() < 10 || &blob[..6] != MAGIC || blob[6..10] != [0, 0, 0, 1] {
        return Err(SigError::Damaged);
    }
    let mut rest = &blob[10..];
    let public = take_string(&mut rest).ok_or(SigError::Damaged)?;
    let namespace = take_string(&mut rest).ok_or(SigError::Damaged)?;
    let reserved = take_string(&mut rest).ok_or(SigError::Damaged)?;
    let hash = take_string(&mut rest).ok_or(SigError::Damaged)?;
    let signature = take_string(&mut rest).ok_or(SigError::Damaged)?;
    if !rest.is_empty() {
        return Err(SigError::Damaged);
    }
    let key = ed25519_of_blob(public).ok_or(SigError::OtherKey)?;
    if !trusted.contains(&key) {
        return Err(SigError::OtherKey);
    }
    if namespace != NAMESPACE.as_bytes() || !reserved.is_empty() {
        return Err(SigError::OtherPurpose);
    }
    let digest: Vec<u8> = match hash {
        b"sha512" => Sha512::digest(data).to_vec(),
        b"sha256" => Sha256::digest(data).to_vec(),
        _ => return Err(SigError::OtherPurpose),
    };
    let mut sig_rest = signature;
    let kind = take_string(&mut sig_rest).ok_or(SigError::Damaged)?;
    let raw = take_string(&mut sig_rest).ok_or(SigError::Damaged)?;
    if kind != KEY_TYPE || raw.len() != 64 || !sig_rest.is_empty() {
        return Err(SigError::Damaged);
    }
    let mut signed = Vec::with_capacity(128);
    signed.extend_from_slice(MAGIC);
    ssh_string(&mut signed, namespace);
    ssh_string(&mut signed, reserved);
    ssh_string(&mut signed, hash);
    ssh_string(&mut signed, &digest);
    let verifying = VerifyingKey::from_bytes(&key).map_err(|_| SigError::OtherKey)?;
    let raw: [u8; 64] = raw.try_into().map_err(|_| SigError::Damaged)?;
    verifying.verify_strict(&signed, &Signature::from_bytes(&raw)).map_err(|_| SigError::Mismatch)
}

/// The lower-case hex sha256 of `data`.
pub fn sha256_hex(data: &[u8]) -> String {
    hex(&Sha256::digest(data))
}

pub fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(DIGITS[(b >> 4) as usize] as char);
        out.push(DIGITS[(b & 15) as usize] as char);
    }
    out
}

#[cfg(test)]
pub mod testkey {
    //! A fixed test key (never a real one) and an SSHSIG signer for the tests.
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    pub fn b64encode(data: &[u8]) -> String {
        const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut out = String::new();
        for chunk in data.chunks(3) {
            let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
            let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
            for i in 0..4 {
                if i <= chunk.len() {
                    out.push(A[((n >> (18 - 6 * i)) & 63) as usize] as char);
                } else {
                    out.push('=');
                }
            }
        }
        out
    }

    pub fn key(seed: u8) -> SigningKey {
        SigningKey::from_bytes(&[seed; 32])
    }

    pub fn public_blob(key: &SigningKey) -> Vec<u8> {
        let mut blob = Vec::new();
        ssh_string(&mut blob, KEY_TYPE);
        ssh_string(&mut blob, key.verifying_key().as_bytes());
        blob
    }

    pub fn allowed_line(key: &SigningKey) -> String {
        format!("{PRINCIPAL} namespaces=\"{NAMESPACE}\" ssh-ed25519 {}\n", b64encode(&public_blob(key)))
    }

    pub fn pem(key: &SigningKey) -> String {
        let mut der = SPKI_ED25519.to_vec();
        der.extend_from_slice(key.verifying_key().as_bytes());
        format!("-----BEGIN PUBLIC KEY-----\n{}\n-----END PUBLIC KEY-----\n", b64encode(&der))
    }

    pub fn sign_ns(key: &SigningKey, data: &[u8], namespace: &str) -> String {
        let digest = Sha512::digest(data);
        let mut signed = MAGIC.to_vec();
        ssh_string(&mut signed, namespace.as_bytes());
        ssh_string(&mut signed, b"");
        ssh_string(&mut signed, b"sha512");
        ssh_string(&mut signed, &digest);
        let raw = key.sign(&signed).to_bytes();
        let mut sig = Vec::new();
        ssh_string(&mut sig, KEY_TYPE);
        ssh_string(&mut sig, &raw);
        let mut blob = MAGIC.to_vec();
        blob.extend_from_slice(&1u32.to_be_bytes());
        ssh_string(&mut blob, &public_blob(key));
        ssh_string(&mut blob, namespace.as_bytes());
        ssh_string(&mut blob, b"");
        ssh_string(&mut blob, b"sha512");
        ssh_string(&mut blob, &sig);
        let text = b64encode(&blob);
        let lines: Vec<String> = text.as_bytes().chunks(70).map(|c| String::from_utf8_lossy(c).into_owned()).collect();
        format!("-----BEGIN SSH SIGNATURE-----\n{}\n-----END SSH SIGNATURE-----\n", lines.join("\n"))
    }

    pub fn sign(key: &SigningKey, data: &[u8]) -> String {
        sign_ns(key, data, NAMESPACE)
    }
}

#[cfg(test)]
mod tests {
    use super::testkey::*;
    use super::*;

    #[test]
    fn base64_round_trip_and_refusals() {
        for data in [&b""[..], b"a", b"ab", b"abc", b"abcd", &[0u8, 255, 7, 9, 200][..]] {
            if data.is_empty() {
                continue;
            }
            assert_eq!(b64decode(&b64encode(data)).unwrap(), data);
        }
        assert!(b64decode("QUJD!").is_none());
        assert!(b64decode("QU=D").is_none());
        assert!(b64decode("QQ==QUJD").is_none());
        assert!(b64decode("").is_none());
    }

    #[test]
    fn a_signature_of_the_trusted_key_passes() {
        let k = key(7);
        let trusted = allowed_keys(&allowed_line(&k));
        assert_eq!(trusted.len(), 1);
        assert_eq!(verify(b"manifest", &sign(&k, b"manifest"), &trusted), Ok(()));
        assert_eq!(pem_key(&pem(&k)), Some(*k.verifying_key().as_bytes()));
    }

    #[test]
    fn changed_data_another_key_or_namespace_is_refused() {
        let k = key(7);
        let trusted = allowed_keys(&allowed_line(&k));
        assert_eq!(verify(b"manifest!", &sign(&k, b"manifest"), &trusted), Err(SigError::Mismatch));
        assert_eq!(verify(b"manifest", &sign(&key(8), b"manifest"), &trusted), Err(SigError::OtherKey));
        assert_eq!(verify(b"manifest", &sign_ns(&k, b"manifest", "file"), &trusted), Err(SigError::OtherPurpose));
        assert_eq!(verify(b"manifest", "garbage", &trusted), Err(SigError::Damaged));
        assert_eq!(verify(b"manifest", &sign(&k, b"manifest"), &[]), Err(SigError::OtherKey));
    }

    #[test]
    fn only_the_frameworks_signer_lines_count() {
        let k = key(7);
        let blob = b64encode(&public_blob(&k));
        for line in [
            format!("other namespaces=\"{NAMESPACE}\" ssh-ed25519 {blob}"),
            format!("{PRINCIPAL} ssh-ed25519 {blob}"),
            format!("{PRINCIPAL} namespaces=\"file\" ssh-ed25519 {blob}"),
            format!("{PRINCIPAL} cert-authority namespaces=\"{NAMESPACE}\" ssh-ed25519 {blob}"),
            format!("{PRINCIPAL} namespaces=\"{NAMESPACE}\" ssh-rsa {blob}"),
        ] {
            assert!(allowed_keys(&line).is_empty(), "{line}");
        }
    }
}
