//! llmcf-install against a fake board below a temporary prefix (the test build's --root): every root action, the
//! second run that changes nothing, rollback, uninstall, and the refusals (signature, signer place, account
//! conflicts, hostile folders). The board's programs (useradd, userdel, systemctl) are small fakes in the prefix.
#![cfg(feature = "test-root")]
#![forbid(unsafe_code)]

use std::fs;
use std::os::unix::fs::{symlink, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU32, Ordering};

use ed25519_dalek::{Signer, SigningKey};
use sha2::{Digest, Sha256, Sha512};

const BID: &str = "llm-0123abcd-20261101120000";
static COUNTER: AtomicU32 = AtomicU32::new(0);

fn b64(data: &[u8]) -> String {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        for i in 0..4 {
            out.push(if i <= chunk.len() { A[((n >> (18 - 6 * i)) & 63) as usize] as char } else { '=' });
        }
    }
    out
}

fn string(out: &mut Vec<u8>, s: &[u8]) {
    out.extend_from_slice(&(s.len() as u32).to_be_bytes());
    out.extend_from_slice(s);
}

fn public_blob(key: &SigningKey) -> Vec<u8> {
    let mut blob = Vec::new();
    string(&mut blob, b"ssh-ed25519");
    string(&mut blob, key.verifying_key().as_bytes());
    blob
}

fn sign(key: &SigningKey, data: &[u8]) -> String {
    let mut signed = b"SSHSIG".to_vec();
    string(&mut signed, b"llmcf-board");
    string(&mut signed, b"");
    string(&mut signed, b"sha512");
    string(&mut signed, &Sha512::digest(data));
    let mut sig = Vec::new();
    string(&mut sig, b"ssh-ed25519");
    string(&mut sig, &key.sign(&signed).to_bytes());
    let mut blob = b"SSHSIG".to_vec();
    blob.extend_from_slice(&1u32.to_be_bytes());
    string(&mut blob, &public_blob(key));
    string(&mut blob, b"llmcf-board");
    string(&mut blob, b"");
    string(&mut blob, b"sha512");
    string(&mut blob, &sig);
    format!("-----BEGIN SSH SIGNATURE-----\n{}\n-----END SSH SIGNATURE-----\n", b64(&blob))
}

fn sha(data: &[u8]) -> String {
    Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}

fn euid() -> u32 {
    let status = fs::read_to_string("/proc/self/status").unwrap();
    status.lines().find(|l| l.starts_with("Uid:")).unwrap().split_whitespace().nth(2).unwrap().parse().unwrap()
}

const FAKE_USERADD: &str = r#"#!/bin/sh
# fake useradd/adduser: appends the account to the prefix's password and group files
user=""; home=""; comment=""; shell=""
while [ $# -gt 0 ]; do
  case $1 in
    --home-dir) home=$2; shift ;;
    --comment) comment=$2; shift ;;
    --shell) shell=$2; shift ;;
    --*) ;;
    *) user=$1 ;;
  esac
  shift
done
echo "useradd $user" >> "$LLMCF_TEST_ROOT/calls.log"
grep -q "^$user:" "$LLMCF_TEST_ROOT/etc/passwd" && { echo "useradd: user exists" >&2; exit 9; }
echo "$user:x:990:990:$comment:$home:$shell" >> "$LLMCF_TEST_ROOT/etc/passwd"
echo "$user:x:990:" >> "$LLMCF_TEST_ROOT/etc/group"
"#;

const FAKE_USERDEL: &str = r#"#!/bin/sh
echo "userdel $1" >> "$LLMCF_TEST_ROOT/calls.log"
grep -v "^$1:" "$LLMCF_TEST_ROOT/etc/passwd" > "$LLMCF_TEST_ROOT/etc/passwd.new"
mv "$LLMCF_TEST_ROOT/etc/passwd.new" "$LLMCF_TEST_ROOT/etc/passwd"
grep -v "^$1:" "$LLMCF_TEST_ROOT/etc/group" > "$LLMCF_TEST_ROOT/etc/group.new"
mv "$LLMCF_TEST_ROOT/etc/group.new" "$LLMCF_TEST_ROOT/etc/group"
"#;

const FAKE_SYSTEMCTL: &str = r#"#!/bin/sh
echo "systemctl $*" >> "$LLMCF_TEST_ROOT/calls.log"
mark="$LLMCF_TEST_ROOT/fake-enabled"
mkdir -p "$mark"
for unit; do :; done
case $1 in
  is-enabled) if [ -e "$mark/$2" ]; then echo enabled; exit 0; fi; echo disabled; exit 1 ;;
  enable) : > "$mark/$unit" ;;
  disable) rm -f "$mark/$unit" ;;
esac
exit 0
"#;

struct Board {
    root: PathBuf,
    key: SigningKey,
    uid: u32,
}

fn write_mode(path: &Path, data: &[u8], mode: u32) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, data).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
}

fn chown_tree(path: &Path, uid: u32) {
    if euid() != 0 {
        return;
    }
    std::os::unix::fs::lchown(path, Some(uid), Some(uid)).unwrap();
    if path.is_dir() && !fs::symlink_metadata(path).unwrap().file_type().is_symlink() {
        for e in fs::read_dir(path).unwrap() {
            chown_tree(&e.unwrap().path(), uid);
        }
    }
}

impl Board {
    fn new() -> Board {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let root = std::env::temp_dir().join(format!("llmcf-install-test-{}-{n}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        fs::set_permissions(&root, fs::Permissions::from_mode(0o755)).unwrap();
        // the board account: the test's own user, or (a test run as root) a plain account of its own
        let uid = if euid() == 0 { 4242 } else { euid() };
        let key = SigningKey::from_bytes(&[7u8; 32]);
        let b = Board { root, key, uid };
        write_mode(
            &b.p("etc/passwd"),
            format!("root:x:0:0:root:/root:/bin/sh\nboard:x:{uid}:{uid}:Board:/home/board:/bin/sh\n").as_bytes(),
            0o644,
        );
        write_mode(&b.p("etc/group"), format!("root:x:0:\nboard:x:{uid}:\n").as_bytes(), 0o644);
        let line = format!("llmcf namespaces=\"llmcf-board\" ssh-ed25519 {}\n", b64(&public_blob(&b.key)));
        write_mode(&b.p("etc/llmcf/allowed_signers"), line.as_bytes(), 0o644);
        fs::set_permissions(b.p("etc/llmcf"), fs::Permissions::from_mode(0o755)).unwrap();
        fs::set_permissions(b.p("etc"), fs::Permissions::from_mode(0o755)).unwrap();
        for (name, text) in [
            ("usr/sbin/useradd", FAKE_USERADD),
            ("usr/sbin/userdel", FAKE_USERDEL),
            ("usr/bin/systemctl", FAKE_SYSTEMCTL),
        ] {
            write_mode(&b.p(name), text.as_bytes(), 0o755);
        }
        write_mode(&b.p("usr/sbin/nologin"), b"#!/bin/sh\nexit 1\n", 0o755);
        b
    }

    fn p(&self, rel: &str) -> PathBuf {
        self.root.join(rel)
    }

    fn comp(&self) -> PathBuf {
        self.p(&format!("home/board/.local/share/llm-framework/bundle/{BID}/components/llm"))
    }

    fn kept(&self) -> PathBuf {
        self.p(&format!("home/board/.local/share/llm-framework/bundle/{BID}/.kept"))
    }

    /// The component as the unprivileged installer leaves it, and the signed manifest it kept.
    fn deploy(&self, root_actions: serde_json::Value) {
        let model = b"GGUF model bytes".to_vec();
        let conf = b"vm.swappiness = 10\n".to_vec();
        let runtime = b"runtime tarball".to_vec();
        write_mode(&self.comp().join("model.gguf"), &model, 0o644);
        write_mode(&self.comp().join("runtime.tar.gz"), &runtime, 0o644);
        write_mode(&self.comp().join("llmcf-limits.conf"), &conf, 0o644);
        write_mode(&self.comp().join("runtime/0123456789abcdef/llama-server.sh"), b"#!/bin/sh\n", 0o700);
        write_mode(&self.comp().join("start.sh"), b"#!/bin/sh\nexec true\n", 0o700);
        write_mode(&self.p("home/board/.config/llm-framework/llama-server.key"), b"key-of-the-runtime\n", 0o600);
        let file = |name: &str, data: &[u8], role: &str| {
            serde_json::json!({"name": name, "sha256": sha(data),
            "size": data.len(), "mode": "0644", "role": role, "source": "bundle"})
        };
        let manifest = serde_json::json!({
            "schema": "llmcf-bundle/1", "bundle_id": BID, "created": "2026-11-01T12:00:00Z",
            "framework_version": "3.3.0", "mode": "full",
            "board": {"device_id": "0123456789abcdef", "os": "linux", "arch": "aarch64", "init_system": "systemd"},
            "components": [{"id": "llm", "kind": "llm", "block": "llm-llama-server", "block_version": "1.0.0",
                            "block_sha256": "a".repeat(64), "block_trust": "framework", "keygen": true,
                            "variables": {"autostart": true, "user": "llmcf"},
                            "files": [file("runtime.tar.gz", &runtime, "runtime"), file("model.gguf", &model, "model"),
                                      file("llmcf-limits.conf", &conf, "config")],
                            "start": {"program": "runtime/0123456789abcdef/llama-server.sh", "args": ["serve"]}}],
            "root_actions": root_actions});
        let data = serde_json::to_vec_pretty(&manifest).unwrap();
        self.keep(&data, &sign(&self.key, &data));
    }

    /// The llm component as `deploy` leaves it, and a gateway component next to it (its program and start script),
    /// with the signed manifest of both.
    fn deploy_gateway(&self, root_actions: serde_json::Value) {
        self.deploy(serde_json::json!([]));
        let gw = self.p(&format!("home/board/.local/share/llm-framework/bundle/{BID}/components/gateway"));
        let program = b"gateway program".to_vec();
        write_mode(&gw.join("llmcf-gateway"), &program, 0o755);
        write_mode(&gw.join("start.sh"), b"#!/bin/sh\nexec true\n", 0o700);
        let mut manifest: serde_json::Value =
            serde_json::from_slice(&fs::read(self.kept().join("manifest.json")).unwrap()).unwrap();
        manifest["components"].as_array_mut().unwrap().push(serde_json::json!({
            "id": "gateway", "kind": "gateway", "block": "gateway-llmcf", "block_version": "1.0.0",
            "block_sha256": "b".repeat(64), "block_trust": "framework",
            "variables": {"autostart": true, "user": "llmcf"},
            "files": [{"name": "llmcf-gateway", "sha256": sha(&program), "size": program.len(), "mode": "0755",
                       "role": "program", "source": "bundle"}],
            "start": {"program": "llmcf-gateway", "args": ["serve"]}}));
        manifest["root_actions"] = root_actions;
        let data = serde_json::to_vec_pretty(&manifest).unwrap();
        self.keep(&data, &sign(&self.key, &data));
    }

    fn keep(&self, data: &[u8], signature: &str) {
        write_mode(&self.kept().join("manifest.json"), data, 0o644);
        write_mode(&self.kept().join("manifest.json.sig"), signature.as_bytes(), 0o644);
        chown_tree(&self.p("home/board"), self.uid);
    }

    fn run(&self, action: &str) -> (i32, String) {
        let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
            .args(["--root", self.root.to_str().unwrap(), action, BID])
            .env_clear()
            .env("SUDO_USER", "board")
            .output()
            .unwrap();
        (out.status.code().unwrap_or(-1), String::from_utf8_lossy(&out.stdout).into_owned())
    }

    fn passwd(&self) -> String {
        fs::read_to_string(self.p("etc/passwd")).unwrap()
    }

    fn unit(&self) -> PathBuf {
        self.p("etc/systemd/system/llmcf-llm.service")
    }
}

impl Drop for Board {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn autostart() -> serde_json::Value {
    serde_json::json!([
        {"action": "create_user", "component": "llm", "user": "llmcf"},
        {"action": "install_unit", "component": "llm", "unit": "llmcf-llm.service", "user": "llmcf"},
        {"action": "set_cgroup", "component": "llm", "unit": "llmcf-llm.service", "memory_max_mb": 1024,
         "memory_high_mb": 960, "cpu_weight": 200, "oom_score_adj": 500},
        {"action": "place_root_file", "component": "llm", "place": "sysctl", "file": "llmcf-limits.conf"}])
}

fn last(out: &str) -> &str {
    out.lines().last().unwrap_or("")
}

#[test]
fn plan_apply_again_status_rollback_and_uninstall() {
    let b = Board::new();
    b.deploy(autostart());
    let (code, out) = b.run("plan");
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-bundle: would create_user llm user=llmcf"), "{out}");
    assert!(last(&out).starts_with("llmcf-bundle: result=plan changed=0 pending=4"), "{out}");
    assert!(!b.passwd().contains("llmcf:"), "a plan changes nothing");

    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).starts_with("llmcf-bundle: result=ok changed="), "{out}");
    assert!(b.passwd().contains("llmcf:x:990:990:llmcf bundle account:/var/lib/llmcf/home/llmcf:/usr/sbin/nologin"));
    let unit = fs::read_to_string(b.unit()).unwrap();
    assert!(unit.contains("User=llmcf\n") && unit.contains("ProtectSystem=strict\n"));
    assert!(unit.contains(&format!("ExecStart=/bin/sh /var/lib/llmcf/bundle/{BID}/llm/start.sh\n")));
    let dropin = fs::read_to_string(b.p("etc/systemd/system/llmcf-llm.service.d/llmcf-cgroup.conf")).unwrap();
    assert!(dropin.contains("MemoryMax=1024M\n") && dropin.contains("CPUWeight=200\n"));
    // the model's unit goes first if the kernel must take one, and is slowed above its measured need
    assert!(dropin.contains("MemoryHigh=960M\n") && dropin.contains("OOMScoreAdjust=500\n"), "{dropin}");
    assert_eq!(fs::read(b.p("etc/sysctl.d/llmcf-limits.conf")).unwrap(), b"vm.swappiness = 10\n");
    let copy = b.p(&format!("var/lib/llmcf/bundle/{BID}/llm"));
    assert_eq!(fs::read(copy.join("model.gguf")).unwrap(), b"GGUF model bytes");
    assert!(copy.join("runtime/0123456789abcdef/llama-server.sh").is_file());
    assert_eq!(fs::metadata(copy.join("model.gguf")).unwrap().mode() & 0o777, 0o640);
    assert_eq!(
        fs::read(b.p("var/lib/llmcf/home/llmcf/.config/llm-framework/llama-server.key")).unwrap(),
        b"key-of-the-runtime\n"
    );
    let calls = fs::read_to_string(b.p("calls.log")).unwrap();
    assert!(
        calls.contains("systemctl daemon-reload\n") && calls.contains("systemctl enable --now llmcf-llm.service\n")
    );

    // a second run changes nothing and says so
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert_eq!(last(&out), "llmcf-bundle: result=ok changed=0 everything was in place already (nothing changed)");
    assert!(out.contains("llmcf-bundle: already install_unit llm user=llmcf unit=llmcf-llm.service"), "{out}");
    let (code, out) = b.run("status");
    assert_eq!((code, last(&out)), (0, "llmcf-bundle: result=installed changed=0 root=4"), "{out}");

    let (code, out) = b.run("plan-uninstall");
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-bundle: would-remove user-created llmcf"), "{out}");

    let (code, out) = b.run("rollback");
    assert_eq!(code, 0, "{out}");
    assert!(!b.unit().exists() && !b.passwd().contains("llmcf:") && !copy.exists(), "{out}");
    assert!(!b.p("etc/sysctl.d/llmcf-limits.conf").exists());
    assert!(fs::read_to_string(b.p("calls.log")).unwrap().contains("systemctl disable --now llmcf-llm.service\n"));
    let (_c, out) = b.run("rollback");
    assert_eq!(last(&out), "llmcf-bundle: result=ok changed=0 nothing to roll back");

    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    let (code, out) = b.run("uninstall");
    assert_eq!((code, last(&out)), (0, "llmcf-bundle: result=ok changed=7 the root part was uninstalled"), "{out}");
    assert!(!b.p(&format!("var/lib/llmcf/install/{BID}")).exists());
    assert!(!b.passwd().contains("llmcf:") && !b.unit().exists());
    let (code, out) = b.run("uninstall");
    assert_eq!((code, last(&out).starts_with("llmcf-bundle: result=absent")), (0, true), "{out}");
}

#[test]
fn a_unit_that_was_there_comes_back_on_rollback() {
    let b = Board::new();
    b.deploy(autostart());
    write_mode(&b.unit(), b"[Unit]\nDescription=earlier\n", 0o644);
    assert_eq!(b.run("apply").0, 0);
    assert!(fs::read_to_string(b.unit()).unwrap().contains("ProtectSystem=strict"));
    let (code, out) = b.run("rollback");
    assert_eq!(code, 0, "{out}");
    assert_eq!(fs::read(b.unit()).unwrap(), b"[Unit]\nDescription=earlier\n");
}

#[test]
fn a_changed_or_foreign_signature_is_refused_and_nothing_changes() {
    let b = Board::new();
    b.deploy(autostart());
    let data = fs::read(b.kept().join("manifest.json")).unwrap();
    let mut changed = data.clone();
    changed.extend_from_slice(b" ");
    b.keep(&changed, &fs::read_to_string(b.kept().join("manifest.json.sig")).unwrap());
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("does not match"), "{out}");
    b.keep(&data, &sign(&SigningKey::from_bytes(&[8u8; 32]), &data));
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("not signed with the framework's signing key"), "{out}");
    assert!(!b.passwd().contains("llmcf:") && !b.unit().exists());
}

#[test]
fn a_signer_others_may_change_or_one_in_the_home_folder_is_never_trusted() {
    let b = Board::new();
    b.deploy(autostart());
    fs::set_permissions(b.p("etc/llmcf/allowed_signers"), fs::Permissions::from_mode(0o666)).unwrap();
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("not in a root-owned place"), "{out}");
    fs::set_permissions(b.p("etc/llmcf/allowed_signers"), fs::Permissions::from_mode(0o644)).unwrap();
    fs::set_permissions(b.p("etc/llmcf"), fs::Permissions::from_mode(0o777)).unwrap();
    assert_eq!(b.run("apply").0, 3);
    fs::set_permissions(b.p("etc/llmcf"), fs::Permissions::from_mode(0o755)).unwrap();
    // only a signer in the account's home (the home gate's place): no root-owned key, refused
    let line = fs::read(b.p("etc/llmcf/allowed_signers")).unwrap();
    fs::remove_dir_all(b.p("etc/llmcf")).unwrap();
    write_mode(&b.p("home/board/.config/llm-framework/signing/allowed_signers"), &line, 0o644);
    chown_tree(&b.p("home/board"), b.uid);
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("no root-owned signing key"), "{out}");
    assert!(!b.passwd().contains("llmcf:"));
}

#[test]
fn the_pem_twin_is_used_without_an_allowed_signers_file() {
    let b = Board::new();
    b.deploy(autostart());
    fs::remove_file(b.p("etc/llmcf/allowed_signers")).unwrap();
    let mut der = vec![0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00];
    der.extend_from_slice(b.key.verifying_key().as_bytes());
    let pem = format!("-----BEGIN PUBLIC KEY-----\n{}\n-----END PUBLIC KEY-----\n", b64(&der));
    write_mode(&b.p("etc/llmcf/signing_key.pem"), pem.as_bytes(), 0o644);
    let (code, out) = b.run("plan");
    assert_eq!(code, 0, "{out}");
}

#[test]
fn an_account_llmcf_did_not_make_is_never_taken_over() {
    let b = Board::new();
    b.deploy(autostart());
    let mut passwd = b.passwd();
    passwd.push_str("llmcf:x:1001:1001:Someone:/home/llmcf:/bin/sh\n");
    fs::write(b.p("etc/passwd"), &passwd).unwrap();
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("did not make; it is never taken over"), "{out}");
    assert!(!b.unit().exists());
    // an account with uid 0
    fs::write(b.p("etc/passwd"), b.passwd().replace("llmcf:x:1001:1001", "llmcf:x:0:0")).unwrap();
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("uid 0"), "{out}");
}

#[test]
fn a_file_that_does_not_match_its_sha_stops_the_run_and_rollback_undoes_the_rest() {
    let b = Board::new();
    b.deploy(autostart());
    let model = b.comp().join("model.gguf");
    fs::write(&model, b"GGUF model BYTES").unwrap(); // same size, other bytes
    chown_tree(&b.p("home/board"), b.uid);
    let (code, out) = b.run("apply");
    assert_eq!(code, 5, "{out}");
    assert!(out.contains("llmcf-bundle: failed install_unit"), "{out}");
    assert!(out.contains("does not match its sha256"), "{out}");
    assert!(!b.unit().exists() && !b.p(&format!("var/lib/llmcf/bundle/{BID}/llm")).exists());
    assert!(b.passwd().contains("llmcf:"), "the account made before the failed step stays until the rollback");
    let (code, out) = b.run("rollback");
    assert_eq!(code, 0, "{out}");
    assert!(!b.passwd().contains("llmcf:"));
}

#[test]
fn hostile_folders_and_values_are_refused() {
    let b = Board::new();
    b.deploy(autostart());
    // the component folder is a link (to a root-only place): never followed
    let comp = b.comp();
    fs::rename(&comp, comp.with_file_name("elsewhere")).unwrap();
    symlink(b.p("etc"), &comp).unwrap();
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(!b.passwd().contains("llmcf:"));
    fs::remove_file(&comp).unwrap();
    fs::rename(comp.with_file_name("elsewhere"), &comp).unwrap();
    // a link inside the component folder
    symlink(b.p("etc/passwd"), comp.join("passwd")).unwrap();
    let (code, out) = b.run("apply");
    assert_ne!(code, 0, "{out}");
    assert!(!b.p(&format!("var/lib/llmcf/bundle/{BID}/llm/passwd")).exists());
    fs::remove_file(comp.join("passwd")).unwrap();
    // no bundle id, no sudo account, a root account
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
        .args(["--root", b.root.to_str().unwrap(), "apply", "../etc"])
        .env("SUDO_USER", "board")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    for user in ["", "root", "bo ard", "nobody-here"] {
        let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
            .args(["--root", b.root.to_str().unwrap(), "apply", BID])
            .env_clear()
            .env("SUDO_USER", user)
            .output()
            .unwrap();
        assert_eq!(out.status.code(), Some(3), "{user}");
    }
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
        .args(["--root", b.root.to_str().unwrap(), "run-shell", BID])
        .env("SUDO_USER", "board")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
}

#[test]
fn removals_take_only_what_llmcf_made() {
    let b = Board::new();
    b.deploy(autostart());
    assert_eq!(b.run("apply").0, 0);
    b.deploy(serde_json::json!([
        {"action": "remove_unit", "component": "llm", "unit": "llmcf-llm.service"},
        {"action": "remove_cgroup", "component": "llm", "unit": "llmcf-llm.service"},
        {"action": "remove_root_file", "component": "llm", "place": "sysctl", "file": "llmcf-limits.conf"},
        {"action": "remove_user", "component": "llm", "user": "llmcf"}]));
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(!b.unit().exists() && !b.passwd().contains("llmcf:"), "{out}");
    let (code, out) = b.run("rollback");
    assert_eq!(code, 0, "{out}");
    assert!(b.unit().exists() && b.passwd().contains("llmcf:"), "{out}");
    // an account llmcf did not make is never removed
    b.deploy(serde_json::json!([{"action": "remove_user", "component": "llm", "user": "board"}]));
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(b.passwd().contains("board:"));
}

#[test]
fn the_unprivileged_variant_has_no_root_actions_to_run() {
    let b = Board::new();
    b.deploy(autostart());
    let data = fs::read_to_string(b.kept().join("manifest.json"))
        .unwrap()
        .replace("\"mode\": \"full\"", "\"mode\": \"unprivileged\"");
    b.keep(data.as_bytes(), &sign(&b.key, data.as_bytes()));
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("unprivileged-only variant"), "{out}");
}

// --- the person's harness (root action run_harness and the harness actions) -----------------------------------------

const WATCH: &str = include_str!("../../../orchestrator/runtime/bundle/harness_watch.sh");
const VECTORS: &str = include_str!("../../../tests/data/harness_tree_vectors.json");

fn unb64(text: &str) -> Vec<u8> {
    let mut out = Vec::new();
    let (mut acc, mut bits) = (0u32, 0);
    for c in text.bytes() {
        let v = match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => continue,
        } as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
        }
    }
    out
}

impl Board {
    fn hcomp(&self) -> PathBuf {
        self.p(&format!("home/board/.local/share/llm-framework/bundle/{BID}/components/agent"))
    }

    /// A harness component as the unprivileged installer leaves it (the vectors' packed tree and the real watch).
    fn deploy_harness(&self, assume_yes: bool) -> String {
        let v: serde_json::Value = serde_json::from_str(VECTORS).unwrap();
        let tar = unb64(v["pack"]["tar_base64"].as_str().unwrap());
        let tree = v["pack"]["tree_sha256"].as_str().unwrap().to_string();
        write_mode(&self.hcomp().join("harness.tar"), &tar, 0o644);
        write_mode(&self.hcomp().join("harness_watch.sh"), WATCH.as_bytes(), 0o755);
        fs::create_dir_all(self.p("watched")).unwrap();
        fs::create_dir_all(self.p("bin")).unwrap();
        let _ = symlink("/bin/sh", self.p("bin/sh"));
        let file = |name: &str, data: &[u8], role: &str, mode: &str| {
            serde_json::json!({"name": name, "sha256": sha(data), "size": data.len(), "mode": mode, "role": role,
                               "source": "bundle"})
        };
        let mut harness = serde_json::json!({"id": "demo", "pin": tree, "tree_sha256": tree,
            "installer": "installer/install.sh", "dry_run_args": ["--dry-run"],
            "exit_codes": {"0": "done", "1": "findings"}});
        if assume_yes {
            harness["assume_yes"] = serde_json::json!("DEMO_YES=1");
        }
        let manifest = serde_json::json!({
            "schema": "llmcf-bundle/1", "bundle_id": BID, "created": "2026-11-01T12:00:00Z",
            "framework_version": "3.4.0", "mode": "full",
            "board": {"device_id": "0123456789abcdef", "os": "linux", "arch": "aarch64", "init_system": "systemd"},
            "components": [{"id": "agent", "kind": "harness", "block": "harness-agent", "block_version": "1.0.0",
                            "block_sha256": "a".repeat(64), "variables": {"harness": "demo"},
                            "files": [file("harness.tar", &tar, "data", "0644"),
                                      file("harness_watch.sh", WATCH.as_bytes(), "program", "0755")],
                            "harness": harness}],
            "root_actions": [{"action": "run_harness", "component": "agent"}]});
        let data = serde_json::to_vec_pretty(&manifest).unwrap();
        self.keep(&data, &sign(&self.key, &data));
        tree
    }

    fn harness(&self, args: &[&str]) -> (i32, String) {
        let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
            .args(["--root", self.root.to_str().unwrap(), args[0], BID])
            .args(&args[1..])
            .env_clear()
            .env("SUDO_USER", "board")
            .output()
            .unwrap();
        (out.status.code().unwrap_or(-1), String::from_utf8_lossy(&out.stdout).into_owned())
    }

    /// The installer's output of a run until it ended (harness-read from offset 0, again and again).
    fn read_all(&self, run: &str) -> (String, String) {
        let mut offset = 0u64;
        let mut text = Vec::new();
        for _ in 0..60 {
            let (code, out) = self.harness(&["harness-read", "agent", run, &offset.to_string()]);
            assert_eq!(code, 0, "{out}");
            let head = out.lines().find(|l| l.starts_with("llmcf-harness: status=")).unwrap().to_string();
            let data = out.lines().find_map(|l| l.strip_prefix("llmcf-harness: data ")).unwrap_or("");
            text.extend(unb64(data));
            offset = head.split("offset=").nth(1).unwrap().split(' ').next().unwrap().parse().unwrap();
            if head.contains("status=ended") {
                return (head, String::from_utf8_lossy(&text).into_owned());
            }
        }
        panic!("the run did not end");
    }
}

#[test]
fn a_harness_is_unpacked_checked_run_and_read_back_and_a_changed_tree_never_runs() {
    let b = Board::new();
    let tree = b.deploy_harness(false);
    let (code, out) = b.harness(&["harness-start", "agent", "dry-1", "dry"]);
    assert_eq!(code, 3, "the harness is not in place before the apply: {out}");
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-bundle: done run_harness agent"), "{out}");
    let copy = b.p(&format!("var/lib/llmcf/bundle/{BID}/agent"));
    assert_eq!(fs::read_to_string(copy.join("tree.sha256")).unwrap().trim(), tree);
    assert_eq!(fs::metadata(&copy).unwrap().mode() & 0o777, 0o700);
    assert!(copy.join("tree/installer/install.sh").is_file() && !copy.join("tree/.git").exists());
    let (code, out) = b.run("status");
    assert_eq!((code, last(&out)), (0, "llmcf-bundle: result=installed changed=0 root=1"), "{out}");

    // the dry run: its own option, the watch's records read back
    let (code, out) = b.harness(&["harness-start", "agent", "dry-1", "dry"]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-bundle: started harness agent run=dry-1 mode=dry"), "{out}");
    let (head, text) = b.read_all("dry-1");
    assert!(head.contains("status=ended code=0"), "{head}");
    assert!(text.contains("installed"), "{text}");
    // the records: an index of files with size and sha256, then each file in parts, nothing cut off
    let (code, out) = b.harness(&["harness-records", "agent", "dry-1", "index"]);
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-harness: records part=4194304"), "{out}");
    let index: Vec<(String, String, u64, String)> = out
        .lines()
        .filter_map(|l| l.strip_prefix("llmcf-harness: record "))
        .map(|l| {
            let w: Vec<&str> = l.split(' ').collect();
            (w[0].to_string(), w[1].to_string(), w[2].parse().unwrap(), w[3].to_string())
        })
        .collect();
    assert_eq!(index.len(), 14, "{out}");
    assert_eq!(index[0].1, "exit_code");
    for (n, name, size, digest) in &index {
        let mut data = Vec::new();
        while (data.len() as u64) < *size {
            let value = format!("{n}.{}.7", data.len());
            let (code, part) = b.harness(&["harness-records", "agent", "dry-1", &value]);
            assert_eq!(code, 0, "{part}");
            let head = part.lines().find(|l| l.starts_with("llmcf-harness: part ")).unwrap();
            let chunk = unb64(part.lines().find_map(|l| l.strip_prefix("llmcf-harness: data ")).unwrap());
            assert!(!chunk.is_empty() && chunk.len() <= 7, "{head}");
            assert!(head.contains(&format!("sha256={}", sha(&chunk))), "{head}");
            data.extend(chunk);
        }
        assert_eq!(&sha(&data), digest, "{name}");
        if name == "exit_code" {
            assert_eq!(data, b"0\n");
        }
    }
    let (code, out) = b.harness(&["harness-records", "agent", "dry-1", "0.99.1"]);
    assert_eq!(code, 3, "an offset beyond the end: {out}");
    let (code, out) = b.harness(&["harness-start", "agent", "dry-1", "dry"]);
    assert_eq!(code, 3, "a run name is used once: {out}");
    let (code, out) = b.harness(&["harness-start", "agent", "yes-1", "run-yes"]);
    assert_eq!(code, 3, "no assume-yes switch without the person's opt-in: {out}");
    let (code, out) = b.harness(&["harness-answer", "agent", "dry-1"]);
    assert_eq!(code, 3, "a dry run takes no answers: {out}");

    // a tree changed on the board is never run
    fs::write(copy.join("tree/src/app.py"), "print('changed')\n").unwrap();
    let (code, out) = b.harness(&["harness-start", "agent", "run-1", "run"]);
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("does not match its checksum"), "{out}");
    assert!(!copy.join("runs/run-1").exists());

    // uninstall removes the harness's root copy with its runs
    let (code, out) = b.run("uninstall");
    assert_eq!(code, 0, "{out}");
    assert!(!copy.exists(), "{out}");
}

#[test]
fn hostile_harness_arguments_are_refused_before_anything() {
    let b = Board::new();
    b.deploy_harness(true);
    for args in [
        vec!["harness-start", "agent", "../x", "run"],
        vec!["harness-start", "agent", "run-1", "sh"],
        vec!["harness-read", "agent", "run-1", "-1"],
        vec!["harness-read", "agent", "run-1"],
        vec!["harness-stop", "../agent", "run-1"],
        vec!["harness-records", "agent", "run-1", "more"],
        vec!["harness-records", "agent", "run-1"],
        vec!["harness-records", "agent", "run-1", "14.0.1"],
        vec!["harness-records", "agent", "run-1", "0.0.0"],
    ] {
        let (code, out) = b.harness(&args);
        assert_eq!(code, 2, "{args:?}: {out}");
    }
    let (code, out) = b.harness(&["harness-start", "llm", "run-1", "run"]);
    assert_eq!(code, 3, "{out}");
}

// --- the gateway's right to start and stop the bundle's engines (allow_unit_control) --------------------------------

fn with_control() -> serde_json::Value {
    serde_json::json!([
        {"action": "create_user", "component": "llm", "user": "llmcf"},
        {"action": "install_unit", "component": "llm", "unit": "llmcf-llm.service", "user": "llmcf"},
        {"action": "create_user", "component": "gateway", "user": "llmcf"},
        {"action": "install_unit", "component": "gateway", "unit": "llmcf-gateway.service", "user": "llmcf"},
        {"action": "allow_unit_control", "component": "gateway", "user": "llmcf", "units": ["llmcf-llm.service"]}])
}

const FAKE_VISUDO: &str = r#"#!/bin/sh
echo "visudo $*" >> "$LLMCF_TEST_ROOT/calls.log"
grep -q 'NOPASSWD' "$3" || exit 1
exit 0
"#;

#[test]
fn the_gateway_may_start_its_engines_through_polkit() {
    let b = Board::new();
    write_mode(&b.p("usr/bin/pkaction"), b"#!/bin/sh\necho 'pkaction version 0.120'\n", 0o755);
    b.deploy_gateway(with_control());
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    let rule_path = b.p(&format!("etc/polkit-1/rules.d/60-llmcf-{BID}.rules"));
    let rule = fs::read_to_string(&rule_path).unwrap();
    assert!(rule.contains("subject.user == \"llmcf\"") && rule.contains("(unit == \"llmcf-llm.service\")"), "{rule}");
    assert_eq!(fs::metadata(&rule_path).unwrap().mode() & 0o777, 0o644);
    assert!(!b.p("etc/sudoers.d").exists(), "polkit's way writes no sudoers file");
    assert!(out.contains("llmcf-bundle: done allow_unit_control gateway user=llmcf units=llmcf-llm.service"), "{out}");
    // the same run again changes nothing
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-bundle: already allow_unit_control gateway"), "{out}");
    assert!(last(&out).ends_with("everything was in place already (nothing changed)"), "{out}");
    // the uninstall takes it away with the rest
    let (code, out) = b.run("uninstall");
    assert_eq!(code, 0, "{out}");
    assert!(!rule_path.exists());
}

#[test]
fn without_polkit_a_sudoers_file_with_exact_commands() {
    let b = Board::new();
    write_mode(&b.p("usr/bin/sudo"), b"#!/bin/sh\nexit 1\n", 0o755);
    write_mode(&b.p("usr/sbin/visudo"), FAKE_VISUDO.as_bytes(), 0o755);
    fs::create_dir_all(b.p("etc/sudoers.d")).unwrap();
    // an old polkit (0.105) reads no JavaScript rules: the sudoers way
    write_mode(&b.p("usr/bin/pkaction"), b"#!/bin/sh\necho 'pkaction version 0.105'\n", 0o755);
    b.deploy_gateway(with_control());
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    let path = b.p(&format!("etc/sudoers.d/llmcf-{BID}"));
    let text = fs::read_to_string(&path).unwrap();
    let lines: Vec<&str> = text.lines().filter(|l| !l.starts_with('#')).collect();
    assert_eq!(
        lines,
        [
            "llmcf ALL=(root) NOPASSWD: /usr/bin/systemctl start llmcf-llm.service",
            "llmcf ALL=(root) NOPASSWD: /usr/bin/systemctl stop llmcf-llm.service",
            "llmcf ALL=(root) NOPASSWD: /usr/bin/systemctl restart llmcf-llm.service",
        ]
    );
    assert_eq!(fs::metadata(&path).unwrap().mode() & 0o777, 0o440);
    let dropin = b.p("etc/systemd/system/llmcf-gateway.service.d/llmcf-control.conf");
    assert!(fs::read_to_string(&dropin).unwrap().contains("NoNewPrivileges=no\n"));
    assert!(fs::read_to_string(b.p("calls.log")).unwrap().contains("visudo -c -f "));
    assert!(!b.p("etc/polkit-1").exists());
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).ends_with("everything was in place already (nothing changed)"), "{out}");
    // the board gets a polkit with rules: the next run moves to its way and takes the sudoers files away
    write_mode(&b.p("usr/bin/pkaction"), b"#!/bin/sh\necho 'pkaction version 124'\n", 0o755);
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(!path.exists() && !dropin.exists());
    assert!(b.p(&format!("etc/polkit-1/rules.d/60-llmcf-{BID}.rules")).exists());
    let (code, out) = b.run("uninstall");
    assert_eq!(code, 0, "{out}");
    assert!(!path.exists() && !dropin.exists() && !b.p(&format!("etc/polkit-1/rules.d/60-llmcf-{BID}.rules")).exists());
}

#[test]
fn a_board_with_neither_way_is_refused_and_nothing_changes() {
    let b = Board::new();
    b.deploy_gateway(with_control());
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("neither polkit"), "{out}");
    assert!(!b.passwd().contains("llmcf:"));
}

#[test]
fn a_control_of_a_unit_not_of_the_bundle_is_refused() {
    let b = Board::new();
    write_mode(&b.p("usr/bin/pkaction"), b"#!/bin/sh\necho 'pkaction version 122'\n", 0o755);
    let mut actions = with_control();
    actions[4]["units"] = serde_json::json!(["llmcf-gateway.service"]);
    b.deploy_gateway(actions);
    let (code, out) = b.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("is no engine unit of this bundle"), "{out}");
    assert!(!b.p("etc/polkit-1").exists());
}

// --- engines in turn: their units installed, not enabled at boot (on_demand) --------------------------------------

#[test]
fn an_engine_unit_on_demand_is_installed_but_not_enabled_at_boot() {
    let b = Board::new();
    write_mode(&b.p("usr/bin/pkaction"), b"#!/bin/sh\necho 'pkaction version 0.120'\n", 0o755);
    // first together: the engine's unit is enabled and started as before
    b.deploy_gateway(with_control());
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(b.p("fake-enabled/llmcf-llm.service").exists());
    // then in turn: the same unit on demand is disabled at boot, never stopped by llmcf-install
    let _ = fs::remove_file(b.p("calls.log"));
    let mut actions = with_control();
    actions[1]["on_demand"] = serde_json::json!(true);
    b.deploy_gateway(actions.clone());
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(out.contains("llmcf-bundle: done disable llmcf-llm.service"), "{out}");
    let calls = fs::read_to_string(b.p("calls.log")).unwrap();
    assert!(calls.contains("systemctl disable llmcf-llm.service\n"), "{calls}");
    assert!(!calls.contains("--now llmcf-llm.service"), "{calls}");
    assert!(!b.p("fake-enabled/llmcf-llm.service").exists());
    assert!(b.p("fake-enabled/llmcf-gateway.service").exists(), "the gateway itself starts at boot");
    assert!(b.unit().exists());
    // in place now: the next run changes nothing
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).ends_with("everything was in place already (nothing changed)"), "{out}");
    // the uninstall stops a unit the gateway may have started and takes it away
    let (code, out) = b.run("uninstall");
    assert_eq!(code, 0, "{out}");
    assert!(fs::read_to_string(b.p("calls.log")).unwrap().contains("systemctl stop llmcf-llm.service\n"));
    assert!(!b.unit().exists());
}

#[test]
fn a_fresh_unit_on_demand_is_never_enabled_and_only_an_engine_of_the_control_may_be_one() {
    let b = Board::new();
    write_mode(&b.p("usr/bin/pkaction"), b"#!/bin/sh\necho 'pkaction version 0.120'\n", 0o755);
    let mut actions = with_control();
    actions[1]["on_demand"] = serde_json::json!(true);
    b.deploy_gateway(actions.clone());
    let (code, out) = b.run("apply");
    assert_eq!(code, 0, "{out}");
    let calls = fs::read_to_string(b.p("calls.log")).unwrap();
    assert!(!calls.contains("enable --now llmcf-llm.service"), "{calls}");
    assert!(calls.contains("enable --now llmcf-gateway.service"), "{calls}");
    assert!(b.unit().exists() && !b.p("fake-enabled/llmcf-llm.service").exists());
    // the gateway's own unit is no engine: it is never on demand
    let b2 = Board::new();
    let mut bad = with_control();
    bad[3]["on_demand"] = serde_json::json!(true);
    b2.deploy_gateway(bad);
    let (code, out) = b2.run("apply");
    assert_eq!(code, 3, "{out}");
    assert!(out.contains("is on demand but no engine unit"), "{out}");
}

// --- the board's clock (clock.rs) ------------------------------------------------------------------------------------

const FAKE_LOGGED: &str = r#"#!/bin/sh
echo "$(basename "$0") $*" >> "$LLMCF_TEST_ROOT/calls.log"
exit 0
"#;

impl Board {
    fn clock(&self, arg: &str) -> (i32, String) {
        let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
            .args(["--root", self.root.to_str().unwrap(), "clock", arg])
            .env_clear()
            .env("SUDO_USER", "board")
            .output()
            .unwrap();
        (out.status.code().unwrap_or(-1), String::from_utf8_lossy(&out.stdout).into_owned())
    }

    fn calls(&self) -> String {
        fs::read_to_string(self.p("calls.log")).unwrap_or_default()
    }

    fn rtc(&self, since_epoch: &str) {
        write_mode(&self.p("sys/class/rtc/rtc0/since_epoch"), format!("{since_epoch}\n").as_bytes(), 0o444);
    }
}

#[test]
fn the_clock_is_set_from_a_checked_stamp_written_into_the_rtc_and_saved() {
    let b = Board::new();
    for name in ["usr/bin/date", "usr/sbin/hwclock", "usr/sbin/fake-hwclock"] {
        write_mode(&b.p(name), FAKE_LOGGED.as_bytes(), 0o755);
    }
    b.rtc("0");
    let (code, out) = b.clock("2026-11-10T12:30:05Z");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).starts_with("llmcf-bundle: result=ok changed=1 set=2026-11-10T12:30:05Z"), "{out}");
    assert!(last(&out).contains("rtc_written=yes saved=fake-hwclock"), "{out}");
    let calls = b.calls();
    assert!(calls.contains("date -u -s 2026-11-10 12:30:05\n"), "{calls}");
    assert!(calls.contains("hwclock -w -u\n") && calls.contains("fake-hwclock save force\n"), "{calls}");
}

#[test]
fn without_fake_hwclock_the_timesyncd_clock_file_keeps_the_time() {
    let b = Board::new();
    write_mode(&b.p("usr/bin/date"), FAKE_LOGGED.as_bytes(), 0o755);
    fs::create_dir_all(b.p("var/lib/systemd/timesync")).unwrap();
    let (code, out) = b.clock("2026-11-10T12:30:05Z");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).contains("rtc_written=no saved=timesyncd"), "{out}");
    assert!(b.p("var/lib/systemd/timesync/clock").is_file());
    // a link in the clock file's place is never followed
    let b = Board::new();
    write_mode(&b.p("usr/bin/date"), FAKE_LOGGED.as_bytes(), 0o755);
    fs::create_dir_all(b.p("var/lib/systemd/timesync")).unwrap();
    write_mode(&b.p("elsewhere"), b"x", 0o644);
    symlink(b.p("elsewhere"), b.p("var/lib/systemd/timesync/clock")).unwrap();
    let (_code, out) = b.clock("2026-11-10T12:30:05Z");
    assert!(last(&out).contains("saved=none"), "{out}");
}

#[test]
fn a_stamp_out_of_range_or_any_other_argument_is_refused_before_anything_runs() {
    let b = Board::new();
    write_mode(&b.p("usr/bin/date"), FAKE_LOGGED.as_bytes(), 0o755);
    for bad in ["2024-06-01T00:00:00Z", "2100-01-01T00:00:00Z", "2026-02-30T00:00:00Z", "now", "@1794313805"] {
        let (code, out) = b.clock(bad);
        assert_eq!(code, 2, "{bad}: {out}");
        assert!(last(&out).contains("result=refused changed=0"), "{out}");
    }
    assert_eq!(b.calls(), "");
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
        .args(["--root", b.root.to_str().unwrap(), "clock", "2026-11-10T12:30:05Z", "extra"])
        .env_clear()
        .env("SUDO_USER", "board")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    // root itself (no board account through sudo) is refused
    let out = Command::new(env!("CARGO_BIN_EXE_llmcf-install"))
        .args(["--root", b.root.to_str().unwrap(), "clock", "keep"])
        .env_clear()
        .env("SUDO_USER", "root")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(3));
}

#[test]
fn keeping_the_time_takes_fake_hwclock_then_timesyncd_and_nothing_with_a_working_rtc() {
    // a working RTC: nothing to set up
    let b = Board::new();
    b.rtc("1794313805");
    write_mode(&b.p("usr/sbin/fake-hwclock"), FAKE_LOGGED.as_bytes(), 0o755);
    let (code, out) = b.clock("keep");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).contains("changed=0 keep=rtc"), "{out}");
    assert_eq!(b.calls(), "");
    // an RTC that was reset (no battery) and fake-hwclock: its unit enabled, the time saved
    let b = Board::new();
    b.rtc("946684800");
    fs::create_dir_all(b.p("run/systemd/system")).unwrap();
    write_mode(&b.p("usr/sbin/fake-hwclock"), FAKE_LOGGED.as_bytes(), 0o755);
    let (code, out) = b.clock("keep");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).contains("changed=1 keep=fake-hwclock"), "{out}");
    assert!(b.calls().contains("systemctl enable fake-hwclock.service\nfake-hwclock save\n"), "{}", b.calls());
    // no RTC, systemd-timesyncd: enabled and started
    let b = Board::new();
    fs::create_dir_all(b.p("run/systemd/system")).unwrap();
    write_mode(&b.p("usr/lib/systemd/systemd-timesyncd"), b"#!/bin/sh\n", 0o755);
    let (code, out) = b.clock("keep");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).contains("changed=1 keep=timesyncd"), "{out}");
    assert!(b.calls().contains("systemctl enable --now systemd-timesyncd.service"), "{}", b.calls());
    // neither: said plainly, nothing installed
    let b = Board::new();
    let (code, out) = b.clock("keep");
    assert_eq!(code, 0, "{out}");
    assert!(last(&out).starts_with("llmcf-bundle: result=unavailable changed=0 keep=none"), "{out}");
}
