//! What llmcf-install reads of and runs on the board: its fixed places (never configured by the environment), the
//! ownership checks, the password and group files, and the few programs it runs with a fixed argv, a cleared
//! environment and no shell. A test build (feature test-root) puts every fixed place below a prefix; release builds
//! have no such mode.

use std::fs::{self, File};
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// llmcf's root-owned places on the board (all fixed).
pub const SIGNERS: &str = "/etc/llmcf/allowed_signers";
pub const SIGNERS_PEM: &str = "/etc/llmcf/signing_key.pem";
pub const STATE_BASE: &str = "/var/lib/llmcf/install";
pub const HOME_BASE: &str = "/var/lib/llmcf/home";
pub const COPY_BASE: &str = "/var/lib/llmcf/bundle";
pub const PASSWD: &str = "/etc/passwd";
pub const GROUP: &str = "/etc/group";
/// The board account's bundle folder below its home (installer.BUNDLE_REL).
pub const BUNDLE_REL: &str = ".local/share/llm-framework/bundle";
/// The runtime's key file below a home (launcher.KEY_REL).
pub const KEY_REL: &str = ".config/llm-framework/llama-server.key";
/// The comment of every account llmcf-install makes (how it knows its own accounts again).
pub const USER_COMMENT: &str = "llmcf bundle account";
const CLEARED_PATH: &str = "/usr/sbin:/usr/bin:/sbin:/bin";

pub struct Env {
    root: PathBuf,
    pub test: bool,
}

impl Env {
    pub fn real() -> Env {
        Env { root: PathBuf::from("/"), test: false }
    }

    #[cfg(feature = "test-root")]
    pub fn test(root: PathBuf) -> Env {
        Env { root, test: true }
    }

    /// A fixed absolute place on the board (below the prefix in a test build).
    pub fn path(&self, abs: &str) -> PathBuf {
        let rel = abs.trim_start_matches('/');
        if self.test {
            self.root.join(rel)
        } else {
            Path::new("/").join(rel)
        }
    }

    /// The owner every root-owned place must have: root (the test build's own user instead).
    pub fn root_uid(&self) -> u32 {
        if self.test {
            effective_uid().unwrap_or(u32::MAX)
        } else {
            0
        }
    }

    /// Whether this process may hand files to other owners (root, or a test build run as root).
    pub fn can_chown(&self) -> bool {
        effective_uid() == Some(0)
    }

    /// A program of the board at one of its usual absolute places (the first that is a file).
    pub fn tool(&self, candidates: &[&str]) -> Option<PathBuf> {
        candidates.iter().map(|c| self.path(c)).find(|p| p.is_file())
    }

    /// Runs `program` with `args` exactly (no shell, cleared environment, no stdin); (success, its output).
    pub fn run(&self, program: &Path, args: &[&str]) -> (bool, String) {
        let mut cmd = Command::new(program);
        cmd.args(args).env_clear().env("PATH", CLEARED_PATH).env("LC_ALL", "C").stdin(Stdio::null());
        if self.test {
            cmd.env("LLMCF_TEST_ROOT", &self.root);
        }
        match cmd.output() {
            Ok(out) => {
                let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
                text.push_str(&String::from_utf8_lossy(&out.stderr));
                (out.status.success(), text)
            }
            Err(e) => (false, e.to_string()),
        }
    }
}

/// The effective user ID of this process (Linux: /proc/self/status), without any unsafe code.
pub fn effective_uid() -> Option<u32> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    let line = status.lines().find(|l| l.starts_with("Uid:"))?;
    line.split_whitespace().nth(2)?.parse().ok()
}

/// `path` is a real file or folder (no link) owned by `uid` that neither its group nor others may write.
pub fn owned_safe(path: &Path, uid: u32) -> Result<(), String> {
    let meta = fs::symlink_metadata(path).map_err(|_| format!("{} is missing", path.display()))?;
    if meta.file_type().is_symlink() {
        return Err(format!("{} is a link", path.display()));
    }
    if meta.uid() != uid {
        return Err(format!("{} is not owned by {}", path.display(), if uid == 0 { "root" } else { "its owner" }));
    }
    if meta.mode() & 0o022 != 0 {
        return Err(format!("{} can be written by others", path.display()));
    }
    Ok(())
}

/// The root-owned signer file `abs`, checked with its folder and the folder above it: root-owned, no link, not
/// writable by group or others. A home-folder signer is never looked at.
pub fn trusted_signer_text(env: &Env, abs: &str) -> Result<Option<String>, String> {
    let file = env.path(abs);
    if fs::symlink_metadata(&file).is_err() {
        return Ok(None);
    }
    let uid = env.root_uid();
    let folder = file.parent().ok_or("no folder")?.to_path_buf();
    let above = folder.parent().ok_or("no folder")?.to_path_buf();
    for p in [&file, &folder, &above] {
        owned_safe(p, uid).map_err(|e| format!("the framework's signing key is not in a root-owned place: {e}"))?;
    }
    let text = read_checked(&file, uid, 64 * 1024)?;
    Ok(Some(String::from_utf8_lossy(&text).into_owned()))
}

/// The bytes of the regular file `path` (no link, owner `uid`, at most `max` bytes); the open file is the one that
/// was checked (device and inode compared after the open).
pub fn read_checked(path: &Path, uid: u32, max: u64) -> Result<Vec<u8>, String> {
    let before = fs::symlink_metadata(path).map_err(|_| format!("{} is missing", path.display()))?;
    if !before.file_type().is_file() {
        return Err(format!("{} is not a plain file", path.display()));
    }
    if before.uid() != uid {
        return Err(format!("{} has another owner", path.display()));
    }
    if before.len() > max {
        return Err(format!("{} is larger than it may be", path.display()));
    }
    let mut file = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let after = file.metadata().map_err(|e| e.to_string())?;
    if after.dev() != before.dev() || after.ino() != before.ino() {
        return Err(format!("{} changed while it was read", path.display()));
    }
    let mut data = Vec::new();
    file.by_ref().take(max + 1).read_to_end(&mut data).map_err(|e| e.to_string())?;
    if data.len() as u64 > max {
        return Err(format!("{} is larger than it may be", path.display()));
    }
    Ok(data)
}

#[derive(Debug, Clone, PartialEq)]
pub struct Account {
    pub name: String,
    pub uid: u32,
    pub gid: u32,
    pub comment: String,
    pub home: String,
    pub shell: String,
}

/// The accounts of the password file (a line that is not seven plain fields is skipped).
pub fn accounts(env: &Env) -> Vec<Account> {
    let text = fs::read_to_string(env.path(PASSWD)).unwrap_or_default();
    text.lines()
        .filter_map(|line| {
            let f: Vec<&str> = line.split(':').collect();
            if f.len() != 7 {
                return None;
            }
            Some(Account {
                name: f[0].into(),
                uid: f[2].parse().ok()?,
                gid: f[3].parse().ok()?,
                comment: f[4].into(),
                home: f[5].into(),
                shell: f[6].into(),
            })
        })
        .collect()
}

pub fn account(env: &Env, name: &str) -> Option<Account> {
    accounts(env).into_iter().find(|a| a.name == name)
}

/// A group's name by its ID (the group file), for a report line.
pub fn group_exists(env: &Env, name: &str) -> bool {
    fs::read_to_string(env.path(GROUP)).unwrap_or_default().lines().any(|l| l.split(':').next() == Some(name))
}

/// A plain absolute path (a home folder from the password file): no `.`/`..` part, nothing that needs quoting.
pub fn plain_abs(path: &str) -> bool {
    path.starts_with('/')
        && path.len() <= 512
        && path.bytes().all(|c| c.is_ascii_alphanumeric() || b"._/-".contains(&c))
        && !path.contains("//")
        && path.split('/').skip(1).all(|p| !p.is_empty() && p != "." && p != "..")
}

/// The folder `parts` below `home`, each part a real folder (no link) owned by the account `uid`; the home itself
/// a real folder owned by the account or by root.
pub fn account_folder(env: &Env, home: &str, parts: &[&str], uid: u32) -> Result<PathBuf, String> {
    let mut at = env.path(home);
    let meta = fs::symlink_metadata(&at).map_err(|_| "the account's home folder is missing".to_string())?;
    if !meta.file_type().is_dir() || (meta.uid() != uid && meta.uid() != env.root_uid()) {
        return Err("the account's home folder is not a plain folder of the account".into());
    }
    for part in parts {
        for piece in part.split('/') {
            at.push(piece);
            let meta = fs::symlink_metadata(&at).map_err(|_| format!("{} is missing", at.display()))?;
            if !meta.file_type().is_dir() || meta.uid() != uid {
                return Err(format!(
                    "{} is not a plain folder of the account (a link, or another owner)",
                    at.display()
                ));
            }
        }
    }
    Ok(at)
}
