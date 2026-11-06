//! The pairing protocol, version 1 (the framework's host side is orchestrator/remote/pair_pake.py; the two must stay
//! byte for byte the same):
//!
//! Every message is a frame: a 4-byte big-endian length, then that many bytes (at most MAX_FRAME).
//!
//! 1. board -> host  HELLO   = MAGIC (12 bytes) || SPAKE2 message of side A (33 bytes: b'A' || point)
//! 2. host  -> board PROOF   = SPAKE2 message of side B (33 bytes) || host confirmation (32 bytes)
//! 3. board -> host  WRONG   = b"WRONG" (the code did not match; the board counts the attempt and closes)
//!    or             WELCOME = board confirmation (32 bytes) || AEAD(board key, nonce 0, board facts JSON)
//! 4. host  -> board KIT     = AEAD(host key, nonce 0, the framework's pairing content JSON)
//! 5. board -> host  DONE    = AEAD(board key, nonce 1, {"received": true})
//!
//! SPAKE2 runs over Ed25519 with the identities ID_A (the board) and ID_B (the framework) and the 8-digit code as the
//! password; its 32-byte key K never leaves either side. T = SHA-256(MAGIC || HELLO's message || PROOF's message) is
//! the transcript. From K, HKDF-SHA-256 (salt SALT) derives four 32-byte keys with the infos "confirm host",
//! "confirm board", "key host to board" and "key board to host". The confirmations are HMAC-SHA-256 over
//! b"host" || T and b"board" || T, checked in constant time; the board answers only after the host proved the code,
//! and the host trusts nothing before the board proved it. The AEAD is ChaCha20-Poly1305 with T as associated data;
//! each direction has its own key and a counter nonce (4 zero bytes || 8-byte big-endian counter), so no nonce is
//! used twice under one key.

use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{ChaCha20Poly1305, Key, Nonce};
use hkdf::Hkdf;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use spake2::{Ed25519Group, Identity, Password, Spake2};
use std::io::{self, Read, Write};

pub const MAGIC: &[u8; 12] = b"LLMCF-PAIR/1";
pub const ID_A: &[u8] = b"llmcf-pair board";
pub const ID_B: &[u8] = b"llmcf-pair framework";
pub const SALT: &[u8] = b"llmcf-pair/1";
pub const WRONG: &[u8] = b"WRONG";
pub const MAX_FRAME: usize = 256 * 1024;
const MSG_LEN: usize = 33;
const MAC_LEN: usize = 32;

type HmacSha256 = Hmac<Sha256>;

/// Why a pairing session ended without success.
#[derive(Debug, PartialEq, Eq)]
pub enum Failure {
    /// The other side's confirmation did not match: a wrong code (or a side that never knew it).
    WrongCode,
    /// The other side broke the protocol (a frame of the wrong size or kind, a message that is not a point).
    Protocol(&'static str),
    /// The connection failed or timed out.
    Io(io::ErrorKind),
}

impl From<io::Error> for Failure {
    fn from(e: io::Error) -> Self {
        Failure::Io(e.kind())
    }
}

pub fn read_frame(r: &mut impl Read) -> Result<Vec<u8>, Failure> {
    let mut len = [0u8; 4];
    r.read_exact(&mut len)?;
    let n = u32::from_be_bytes(len) as usize;
    if n > MAX_FRAME {
        return Err(Failure::Protocol("frame too large"));
    }
    let mut buf = vec![0u8; n];
    r.read_exact(&mut buf)?;
    Ok(buf)
}

pub fn write_frame(w: &mut impl Write, data: &[u8]) -> Result<(), Failure> {
    if data.len() > MAX_FRAME {
        return Err(Failure::Protocol("frame too large"));
    }
    w.write_all(&(data.len() as u32).to_be_bytes())?;
    w.write_all(data)?;
    w.flush()?;
    Ok(())
}

/// The keys of one session, derived from the SPAKE2 key and bound to the transcript.
pub struct Keys {
    pub transcript: [u8; 32],
    confirm_host: [u8; 32],
    confirm_board: [u8; 32],
    host_to_board: [u8; 32],
    board_to_host: [u8; 32],
}

impl Keys {
    pub fn derive(k: &[u8], msg_a: &[u8], msg_b: &[u8]) -> Keys {
        let mut t = Sha256::new();
        t.update(MAGIC);
        t.update(msg_a);
        t.update(msg_b);
        let transcript: [u8; 32] = t.finalize().into();
        let hk = Hkdf::<Sha256>::new(Some(SALT), k);
        let take = |info: &[u8]| {
            let mut out = [0u8; 32];
            hk.expand(info, &mut out).expect("32 bytes are a valid HKDF length");
            out
        };
        Keys {
            transcript,
            confirm_host: take(b"confirm host"),
            confirm_board: take(b"confirm board"),
            host_to_board: take(b"key host to board"),
            board_to_host: take(b"key board to host"),
        }
    }

    fn mac(key: &[u8; 32], label: &[u8], transcript: &[u8; 32]) -> HmacSha256 {
        let mut m = <HmacSha256 as Mac>::new_from_slice(key).expect("HMAC takes any key length");
        m.update(label);
        m.update(transcript);
        m
    }

    pub fn host_confirmation(&self) -> [u8; 32] {
        Self::mac(&self.confirm_host, b"host", &self.transcript).finalize().into_bytes().into()
    }

    pub fn board_confirmation(&self) -> [u8; 32] {
        Self::mac(&self.confirm_board, b"board", &self.transcript).finalize().into_bytes().into()
    }

    pub fn host_confirmation_ok(&self, tag: &[u8]) -> bool {
        Self::mac(&self.confirm_host, b"host", &self.transcript).verify_slice(tag).is_ok()
    }

    pub fn board_confirmation_ok(&self, tag: &[u8]) -> bool {
        Self::mac(&self.confirm_board, b"board", &self.transcript).verify_slice(tag).is_ok()
    }

    fn nonce(counter: u64) -> [u8; 12] {
        let mut n = [0u8; 12];
        n[4..].copy_from_slice(&counter.to_be_bytes());
        n
    }

    fn seal(&self, key: &[u8; 32], counter: u64, plain: &[u8]) -> Vec<u8> {
        let cipher = ChaCha20Poly1305::new(Key::from_slice(key));
        let nonce = Self::nonce(counter);
        cipher
            .encrypt(Nonce::from_slice(&nonce), Payload { msg: plain, aad: &self.transcript })
            .expect("ChaCha20-Poly1305 seals any message of a frame's size")
    }

    fn open(&self, key: &[u8; 32], counter: u64, sealed: &[u8]) -> Result<Vec<u8>, Failure> {
        let cipher = ChaCha20Poly1305::new(Key::from_slice(key));
        let nonce = Self::nonce(counter);
        cipher
            .decrypt(Nonce::from_slice(&nonce), Payload { msg: sealed, aad: &self.transcript })
            .map_err(|_| Failure::Protocol("a sealed frame does not open"))
    }

    pub fn seal_board(&self, counter: u64, plain: &[u8]) -> Vec<u8> {
        self.seal(&self.board_to_host, counter, plain)
    }

    pub fn open_board(&self, counter: u64, sealed: &[u8]) -> Result<Vec<u8>, Failure> {
        self.open(&self.board_to_host, counter, sealed)
    }

    pub fn seal_host(&self, counter: u64, plain: &[u8]) -> Vec<u8> {
        self.seal(&self.host_to_board, counter, plain)
    }

    pub fn open_host(&self, counter: u64, sealed: &[u8]) -> Result<Vec<u8>, Failure> {
        self.open(&self.host_to_board, counter, sealed)
    }
}

/// A JSON object of at most MAX_FRAME bytes, else a protocol failure.
pub fn json_object(bytes: &[u8]) -> Result<serde_json::Value, Failure> {
    match serde_json::from_slice::<serde_json::Value>(bytes) {
        Ok(v) if v.is_object() => Ok(v),
        _ => Err(Failure::Protocol("not a JSON object")),
    }
}

/// The board's side of one session over `stream`: proves nothing before the host proved the code, then sends
/// `facts` (a JSON object: the probe's identity and the board's SSH host keys) and returns the host's pairing content
/// (a JSON object) after it acknowledged it.
pub fn board_session<S: Read + Write>(stream: &mut S, code: &str, facts: &[u8]) -> Result<serde_json::Value, Failure> {
    let (state, msg_a) =
        Spake2::<Ed25519Group>::start_a(&Password::new(code.as_bytes()), &Identity::new(ID_A), &Identity::new(ID_B));
    let mut hello = Vec::with_capacity(MAGIC.len() + MSG_LEN);
    hello.extend_from_slice(MAGIC);
    hello.extend_from_slice(&msg_a);
    write_frame(stream, &hello)?;
    let proof = read_frame(stream)?;
    if proof.len() != MSG_LEN + MAC_LEN {
        return Err(Failure::Protocol("the host's proof has the wrong size"));
    }
    let (msg_b, tag) = proof.split_at(MSG_LEN);
    let k = state.finish(msg_b).map_err(|_| Failure::Protocol("the host's SPAKE2 message is not valid"))?;
    let keys = Keys::derive(&k, &msg_a, msg_b);
    if !keys.host_confirmation_ok(tag) {
        let _ = write_frame(stream, WRONG);
        return Err(Failure::WrongCode);
    }
    let mut welcome = keys.board_confirmation().to_vec();
    welcome.extend_from_slice(&keys.seal_board(0, facts));
    write_frame(stream, &welcome)?;
    let kit = keys.open_host(0, &read_frame(stream)?)?;
    let content = json_object(&kit)?;
    write_frame(stream, &keys.seal_board(1, br#"{"received":true}"#))?;
    Ok(content)
}

/// The framework's side of one session (the tests' stand-in for orchestrator/remote/pair_pake.py): returns the
/// board's facts after it proved the code, having sent `content`.
pub fn host_session<S: Read + Write>(stream: &mut S, code: &str, content: &[u8]) -> Result<serde_json::Value, Failure> {
    let hello = read_frame(stream)?;
    if hello.len() != MAGIC.len() + MSG_LEN || &hello[..MAGIC.len()] != MAGIC {
        return Err(Failure::Protocol("not a pairing board"));
    }
    let msg_a = &hello[MAGIC.len()..];
    let (state, msg_b) =
        Spake2::<Ed25519Group>::start_b(&Password::new(code.as_bytes()), &Identity::new(ID_A), &Identity::new(ID_B));
    let k = state.finish(msg_a).map_err(|_| Failure::Protocol("the board's SPAKE2 message is not valid"))?;
    let keys = Keys::derive(&k, msg_a, &msg_b);
    let mut proof = msg_b.clone();
    proof.extend_from_slice(&keys.host_confirmation());
    write_frame(stream, &proof)?;
    let welcome = read_frame(stream)?;
    if welcome == WRONG {
        return Err(Failure::WrongCode);
    }
    if welcome.len() < MAC_LEN || !keys.board_confirmation_ok(&welcome[..MAC_LEN]) {
        return Err(Failure::WrongCode);
    }
    let facts = json_object(&keys.open_board(0, &welcome[MAC_LEN..])?)?;
    write_frame(stream, &keys.seal_host(0, content))?;
    let done = json_object(&keys.open_board(1, &read_frame(stream)?)?)?;
    if done.get("received") != Some(&serde_json::Value::Bool(true)) {
        return Err(Failure::Protocol("the board did not take the content"));
    }
    Ok(facts)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixStream;
    use std::thread;

    fn run(
        board_code: &'static str,
        host_code: &'static str,
    ) -> (Result<serde_json::Value, Failure>, Result<serde_json::Value, Failure>) {
        let (mut a, mut b) = UnixStream::pair().unwrap();
        let board = thread::spawn(move || board_session(&mut a, board_code, br#"{"host_keys":[]}"#));
        let host = host_session(&mut b, host_code, br#"{"public_key":"ssh-ed25519 AAAA"}"#);
        drop(b);
        (board.join().unwrap(), host)
    }

    #[test]
    fn the_right_code_pairs_both_ways() {
        let (board, host) = run("12345678", "12345678");
        assert_eq!(board.unwrap()["public_key"], "ssh-ed25519 AAAA");
        assert_eq!(host.unwrap()["host_keys"], serde_json::json!([]));
    }

    #[test]
    fn a_wrong_code_fails_on_both_sides() {
        let (board, host) = run("12345678", "12345679");
        assert_eq!(board.unwrap_err(), Failure::WrongCode);
        assert_eq!(host.unwrap_err(), Failure::WrongCode);
    }

    #[test]
    fn the_keys_differ_per_direction_and_bind_the_transcript() {
        let a = Keys::derive(&[7u8; 32], &[1u8; 33], &[2u8; 33]);
        let b = Keys::derive(&[7u8; 32], &[1u8; 33], &[3u8; 33]);
        assert_ne!(a.host_confirmation(), a.board_confirmation());
        assert_ne!(a.host_confirmation(), b.host_confirmation());
        let sealed = a.seal_host(0, b"x");
        assert!(a.open_board(0, &sealed).is_err());
        assert!(b.open_host(0, &sealed).is_err());
        assert_eq!(a.open_host(0, &sealed).unwrap(), b"x");
        assert!(a.open_host(1, &sealed).is_err());
    }

    #[test]
    fn an_oversized_frame_is_refused() {
        let mut data: &[u8] = &[0x7f, 0xff, 0xff, 0xff];
        assert_eq!(read_frame(&mut data).unwrap_err(), Failure::Protocol("frame too large"));
    }
}
