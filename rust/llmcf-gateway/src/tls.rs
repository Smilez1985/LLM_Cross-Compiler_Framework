//! The local-network mode's TLS: the board's own self-signed certificate (made here on first start, ECDSA P-256,
//! kept in the gateway's state folder, the key 0600), its SHA-256 fingerprint (what the host pins when it pairs), the
//! bearer token remote clients send, and the client side the board's own probe uses (it accepts the presented
//! certificate only to report its fingerprint; the handshake signature is still checked).

use ring::digest::{digest, SHA256};
use ring::rand::{SecureRandom, SystemRandom};
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{ring as provider, verify_tls12_signature, verify_tls13_signature, CryptoProvider};
use rustls::pki_types::pem::PemObject;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

pub const CERT: &str = "cert.pem";
pub const KEY: &str = "key.pem";
pub const TOKEN: &str = "token";

pub fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn fingerprint(der: &[u8]) -> String {
    hex(digest(&SHA256, der).as_ref())
}

fn write_private(path: &Path, data: &[u8], mode: u32) -> Result<(), String> {
    let tmp = path.with_extension("tmp");
    let _ = fs::remove_file(&tmp);
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(&tmp)
        .map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
    f.write_all(data).and_then(|_| f.sync_all()).map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| e.to_string())
}

/// The gateway's state folder ($HOME/.local/state/llm-framework/gateway), made 0700.
pub fn state_dir(home: &Path) -> Result<PathBuf, String> {
    let dir = home.join(".local/state/llm-framework/gateway");
    fs::create_dir_all(&dir).map_err(|e| format!("cannot make the gateway's state folder: {e}"))?;
    fs::set_permissions(&dir, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
    Ok(dir)
}

pub struct Identity {
    pub certs: Vec<CertificateDer<'static>>,
    pub key: PrivateKeyDer<'static>,
    pub fingerprint: String,
}

/// The certificate in `dir`, made on first use; a key file another account can read is refused.
pub fn identity(dir: &Path) -> Result<Identity, String> {
    let (cert, key) = (dir.join(CERT), dir.join(KEY));
    if !cert.is_file() || !key.is_file() {
        let made = rcgen::generate_simple_self_signed(vec!["llmcf-gateway".to_string()])
            .map_err(|e| format!("the certificate could not be made: {e}"))?;
        write_private(&key, made.signing_key.serialize_pem().as_bytes(), 0o600)?;
        write_private(&cert, made.cert.pem().as_bytes(), 0o644)?;
    }
    let mode = fs::metadata(&key).map_err(|e| e.to_string())?.permissions().mode();
    if mode & 0o077 != 0 {
        return Err("the gateway's TLS key is readable by others; it is not used".into());
    }
    let certs: Vec<CertificateDer<'static>> = CertificateDer::pem_file_iter(&cert)
        .map_err(|e| format!("the certificate cannot be read: {e}"))?
        .collect::<Result<_, _>>()
        .map_err(|e| format!("the certificate cannot be read: {e}"))?;
    let first = certs.first().ok_or("the certificate file holds no certificate")?;
    let fp = fingerprint(first.as_ref());
    let key = PrivateKeyDer::from_pem_file(&key).map_err(|e| format!("the TLS key cannot be read: {e}"))?;
    Ok(Identity { certs, key, fingerprint: fp })
}

/// The bearer token remote clients send in the local-network mode (32 random bytes, hex, 0600), made on first use.
pub fn token(dir: &Path) -> Result<String, String> {
    let path = dir.join(TOKEN);
    if let Ok(text) = fs::read_to_string(&path) {
        let t = text.trim().to_string();
        if t.len() == 64 && t.bytes().all(|c| c.is_ascii_hexdigit()) {
            return Ok(t);
        }
    }
    let mut raw = [0u8; 32];
    SystemRandom::new().fill(&mut raw).map_err(|_| "no random numbers for the token")?;
    let t = hex(&raw);
    write_private(&path, format!("{t}\n").as_bytes(), 0o600)?;
    Ok(t)
}

fn crypto() -> Arc<CryptoProvider> {
    Arc::new(provider::default_provider())
}

/// TLS 1.3 only, no client certificates.
pub fn server_config(id: Identity) -> Result<Arc<rustls::ServerConfig>, String> {
    let mut cfg = rustls::ServerConfig::builder_with_provider(crypto())
        .with_protocol_versions(&[&rustls::version::TLS13])
        .map_err(|e| e.to_string())?
        .with_no_client_auth()
        .with_single_cert(id.certs, id.key)
        .map_err(|e| format!("the certificate does not fit its key: {e}"))?;
    cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
    Ok(Arc::new(cfg))
}

/// The probe's verifier: records the presented certificate's fingerprint; the signature of the handshake is
/// checked as usual, so the peer holds that certificate's key.
#[derive(Debug)]
pub struct Recorder {
    pub seen: Mutex<Option<String>>,
    provider: Arc<CryptoProvider>,
}

impl ServerCertVerifier for Recorder {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        if let Ok(mut seen) = self.seen.lock() {
            *seen = Some(fingerprint(end_entity.as_ref()));
        }
        Ok(ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls12_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider.signature_verification_algorithms.supported_schemes()
    }
}

pub fn probe_config() -> Result<(Arc<rustls::ClientConfig>, Arc<Recorder>), String> {
    let rec = Arc::new(Recorder { seen: Mutex::new(None), provider: crypto() });
    let mut cfg = rustls::ClientConfig::builder_with_provider(crypto())
        .with_protocol_versions(&[&rustls::version::TLS13])
        .map_err(|e| e.to_string())?
        .dangerous()
        .with_custom_certificate_verifier(rec.clone())
        .with_no_client_auth();
    cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
    Ok((Arc::new(cfg), rec))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_identity_is_made_once_and_kept_private() {
        let dir = std::env::temp_dir().join(format!("llmcf-gw-tls-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let a = identity(&dir).unwrap();
        let b = identity(&dir).unwrap();
        assert_eq!(a.fingerprint, b.fingerprint);
        assert_eq!(a.fingerprint.len(), 64);
        assert_eq!(fs::metadata(dir.join(KEY)).unwrap().permissions().mode() & 0o777, 0o600);
        let t = token(&dir).unwrap();
        assert_eq!(t, token(&dir).unwrap());
        fs::set_permissions(dir.join(KEY), fs::Permissions::from_mode(0o644)).unwrap();
        assert!(identity(&dir).is_err());
        fs::remove_dir_all(&dir).unwrap();
    }
}
