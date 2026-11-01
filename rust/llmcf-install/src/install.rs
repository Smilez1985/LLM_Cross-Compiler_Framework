//! The typed root actions of a bundle (manifest.ROOT_ACTIONS), their plan, apply, status, rollback and uninstall,
//! with a journal under /var/lib/llmcf/install/<bundle id>/ that says exactly what llmcf-install made, so a
//! rollback undoes the last run and an uninstall removes only what the journal names. Every report line starts with
//! `llmcf-bundle: ` (orchestrator/runtime/bundle/installer.report reads them; the last one is `result=...`).

use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::manifest::{self, is_id, is_root_file, is_unit, is_user, Component, Manifest, RootAction};
use crate::sig;
use crate::sys::{self, Account, Env};

pub const EXIT_OK: i32 = 0;
pub const EXIT_USAGE: i32 = 2;
pub const EXIT_REFUSED: i32 = 3;
pub const EXIT_FAILED: i32 = 5;
pub const ACTIONS: [&str; 6] = ["plan", "apply", "status", "rollback", "uninstall", "plan-uninstall"];
const MAX_TREE_ENTRIES: usize = 20000;
const MAX_TREE_DEPTH: usize = 8;
const MAX_ROOT_FILE: u64 = 64 * 1024;
const DROPIN: &str = "llmcf-cgroup.conf";

pub fn say(state: &str, step: &str, what: &str) {
    println!("llmcf-bundle: {state} {step} {what}");
}

/// The end of a run: its result word, the changed count and the rest of the last line, and the exit status.
pub struct Done {
    pub result: &'static str,
    pub detail: String,
    pub code: i32,
}

impl Done {
    fn ok(result: &'static str, detail: impl Into<String>) -> Done {
        Done { result, detail: detail.into(), code: EXIT_OK }
    }
    pub fn refused(detail: impl Into<String>) -> Done {
        Done { result: "refused", detail: detail.into(), code: EXIT_REFUSED }
    }
    fn failed(detail: impl Into<String>) -> Done {
        Done { result: "failed", detail: detail.into(), code: EXIT_FAILED }
    }
}

// --- the journal --------------------------------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
struct Entry {
    run: u64,
    kind: String,
    args: Vec<String>,
}

impl Entry {
    fn line(&self) -> String {
        let mut parts = vec![self.run.to_string(), self.kind.clone()];
        parts.extend(self.args.iter().cloned());
        parts.join("\t")
    }
}

fn is_backup(s: &str) -> bool {
    s == "-" || (!s.is_empty() && s.len() <= 12 && s.bytes().all(|c| c.is_ascii_digit()))
}

/// A path the journal may name, relative to the board's root: only llmcf's fixed places and names.
fn is_rel(rel: &str) -> bool {
    let parts: Vec<&str> = rel.split('/').collect();
    match parts.as_slice() {
        ["etc", "systemd", "system", unit] => is_unit(unit),
        ["etc", "systemd", "system", dir, file] => {
            dir.strip_suffix(".d").map(is_unit).unwrap_or(false) && (is_root_file(file) || *file == DROPIN)
        }
        ["etc", "tmpfiles.d", file] | ["etc", "sysctl.d", file] => is_root_file(file),
        ["var", "lib", "llmcf", "home", user, ".config", "llm-framework", "llama-server.key"] => is_user(user),
        _ => false,
    }
}

fn parse_entry(line: &str) -> Option<Entry> {
    let f: Vec<&str> = line.split('\t').collect();
    if f.len() < 3 {
        return None;
    }
    let run: u64 = f[0].parse().ok()?;
    let args: Vec<String> = f[2..].iter().map(|s| s.to_string()).collect();
    let ok = match (f[1], &f[2..]) {
        ("user-created" | "user-used", [user]) => is_user(user),
        ("user-removed", [user, backup]) => is_user(user) && is_backup(backup),
        ("tree-made", [comp, backup]) => is_id(comp) && is_backup(backup),
        ("file-made" | "file-gone", [rel, backup]) => is_rel(rel) && is_backup(backup),
        ("unit-enabled" | "unit-disabled", [unit]) => is_unit(unit),
        _ => false,
    };
    ok.then(|| Entry { run, kind: f[1].into(), args })
}

fn read_journal(path: &Path) -> Result<Vec<Entry>, String> {
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return Ok(Vec::new()),
    };
    text.lines()
        .filter(|l| !l.is_empty())
        .map(|l| parse_entry(l).ok_or_else(|| "the journal holds a line llmcf-install never wrote".to_string()))
        .collect()
}

// --- the context of one run ---------------------------------------------------------------------------------------

pub struct Ctx<'a> {
    env: &'a Env,
    bid: String,
    account: Account,
    state: PathBuf,
    changed: u32,
    run: u64,
    reload: bool,
    journal: Vec<Entry>,
}

struct Lock(PathBuf);

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn mkdirs(env: &Env, dir: &Path, mode: u32) -> Result<(), String> {
    if dir.is_dir() {
        return Ok(());
    }
    if let Some(parent) = dir.parent() {
        mkdirs(env, parent, 0o755)?;
    }
    fs::create_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    fs::set_permissions(dir, fs::Permissions::from_mode(mode)).map_err(|e| e.to_string())?;
    let _ = env;
    Ok(())
}

fn chown(env: &Env, path: &Path, uid: u32, gid: u32) -> Result<(), String> {
    if env.can_chown() {
        std::os::unix::fs::lchown(path, Some(uid), Some(gid)).map_err(|e| format!("{}: {e}", path.display()))?;
    }
    Ok(())
}

/// Writes `data` to `path` in place of what it held (a new file next to it, then renamed), with `mode` and owner.
fn write_atomic(env: &Env, path: &Path, data: &[u8], mode: u32, owner: (u32, u32)) -> Result<(), String> {
    let dir = path.parent().ok_or("no folder")?;
    let name = path.file_name().ok_or("no name")?.to_string_lossy().into_owned();
    let tmp = dir.join(format!(".{name}.llmcf-tmp"));
    let _ = fs::remove_file(&tmp);
    let mut f = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(data).and_then(|_| f.sync_all()).map_err(|e| e.to_string())?;
    drop(f);
    chown(env, &tmp, owner.0, owner.1)?;
    fs::set_permissions(&tmp, fs::Permissions::from_mode(mode)).map_err(|e| e.to_string())?;
    fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))
}

fn same_text(path: &Path, want: &[u8]) -> bool {
    match fs::symlink_metadata(path) {
        Ok(m) if m.file_type().is_file() => fs::read(path).map(|d| d == want).unwrap_or(false),
        _ => false,
    }
}

fn exists(path: &Path) -> bool {
    fs::symlink_metadata(path).is_ok()
}

fn systemctl(env: &Env) -> Option<PathBuf> {
    env.tool(&["/usr/bin/systemctl", "/bin/systemctl", "/usr/sbin/systemctl"])
}

fn nologin(env: &Env) -> &'static str {
    for c in ["/usr/sbin/nologin", "/sbin/nologin"] {
        if env.path(c).is_file() {
            return c;
        }
    }
    "/bin/false"
}

impl<'a> Ctx<'a> {
    pub fn new(env: &'a Env, bid: &str, account: Account) -> Ctx<'a> {
        let state = env.path(sys::STATE_BASE).join(bid);
        Ctx { env, bid: bid.into(), account, state, changed: 0, run: 1, reload: false, journal: Vec::new() }
    }

    fn journal_path(&self) -> PathBuf {
        self.state.join("journal")
    }

    fn load_journal(&mut self) -> Result<(), String> {
        let path = self.journal_path();
        if exists(&path) {
            sys::owned_safe(&path, self.env.root_uid())?;
        }
        self.journal = read_journal(&path)?;
        self.run = self.journal.iter().map(|e| e.run).max().unwrap_or(0) + 1;
        Ok(())
    }

    fn save_journal(&self) -> Result<(), String> {
        let text: String = self.journal.iter().map(|e| e.line() + "\n").collect();
        let uid = self.env.root_uid();
        write_atomic(self.env, &self.journal_path(), text.as_bytes(), 0o600, (uid, uid))
    }

    fn note(&mut self, kind: &str, args: &[&str]) -> Result<(), String> {
        self.journal.push(Entry {
            run: self.run,
            kind: kind.into(),
            args: args.iter().map(|s| s.to_string()).collect(),
        });
        self.save_journal()
    }

    fn lock(&self) -> Result<Lock, String> {
        mkdirs(self.env, &self.env.path(sys::STATE_BASE), 0o700)?;
        mkdirs(self.env, &self.state, 0o700)?;
        let path = self.state.join("lock");
        OpenOptions::new().write(true).create_new(true).mode(0o600).open(&path).map_err(|_| {
            format!(
                "another run of llmcf-install for this bundle is going on (or one stopped: remove {} on the \
                     board)",
                path.display()
            )
        })?;
        Ok(Lock(path))
    }

    fn next_backup(&self) -> Result<(String, PathBuf), String> {
        let dir = self.state.join("backup");
        mkdirs(self.env, &dir, 0o700)?;
        let mut n = 1u64;
        if let Ok(entries) = fs::read_dir(&dir) {
            for e in entries.flatten() {
                if let Ok(v) = e.file_name().to_string_lossy().parse::<u64>() {
                    n = n.max(v + 1);
                }
            }
        }
        Ok((n.to_string(), dir.join(n.to_string())))
    }

    fn backup_path(&self, id: &str) -> PathBuf {
        self.state.join("backup").join(id)
    }

    /// Writes a root file (a unit, a drop-in, a root place's file) and journals it; an earlier, different file at
    /// the place is kept as a backup for the rollback.
    fn put_file(&mut self, rel: &str, data: &[u8], mode: u32, owner: (u32, u32)) -> Result<bool, String> {
        let path = self.env.path(&format!("/{rel}"));
        if same_text(&path, data) {
            return Ok(false);
        }
        if let Some(dir) = path.parent() {
            mkdirs(self.env, dir, 0o755)?;
        }
        let backup = if exists(&path) {
            let (id, to) = self.next_backup()?;
            fs::rename(&path, &to).map_err(|e| e.to_string())?;
            id
        } else {
            "-".into()
        };
        if let Err(e) = write_atomic(self.env, &path, data, mode, owner) {
            if backup != "-" {
                let _ = fs::rename(self.backup_path(&backup), &path);
            }
            return Err(e);
        }
        self.note("file-made", &[rel, &backup])?;
        Ok(true)
    }

    fn drop_file(&mut self, rel: &str) -> Result<bool, String> {
        let path = self.env.path(&format!("/{rel}"));
        if !exists(&path) {
            return Ok(false);
        }
        let (id, to) = self.next_backup()?;
        fs::rename(&path, &to).map_err(|e| e.to_string())?;
        self.note("file-gone", &[rel, &id])?;
        Ok(true)
    }
}

// --- the manifest, from the board account's kept installer (or the root copy of its last apply) -------------------

/// The signed manifest of bundle `bid`: from the folder the unprivileged installer kept (<home>/.local/share/
/// llm-framework/bundle/<bid>/.kept, every folder on the way a plain folder of the account), else from the root copy
/// of the last apply; its signature checked against the root-owned signer files only.
pub fn load_manifest(env: &Env, bid: &str, account: &Account) -> Result<(Manifest, Vec<u8>, Vec<u8>), String> {
    let kept = sys::account_folder(env, &account.home, &[sys::BUNDLE_REL, bid, ".kept"], account.uid);
    let (data, signature) = match kept {
        Ok(folder) => (
            sys::read_checked(&folder.join("manifest.json"), account.uid, manifest::MAX_MANIFEST_BYTES as u64)?,
            sys::read_checked(&folder.join("manifest.json.sig"), account.uid, 16 * 1024)
                .map_err(|_| "the bundle's manifest carries no signature of the framework".to_string())?,
        ),
        Err(why) => {
            let state = env.path(sys::STATE_BASE).join(bid);
            let uid = env.root_uid();
            match (
                sys::read_checked(&state.join("manifest.json"), uid, manifest::MAX_MANIFEST_BYTES as u64),
                sys::read_checked(&state.join("manifest.json.sig"), uid, 16 * 1024),
            ) {
                (Ok(d), Ok(s)) => (d, s),
                _ => return Err(format!("the bundle is not on the board (deploy it first): {why}")),
            }
        }
    };
    let mut keys = Vec::new();
    if let Some(text) = sys::trusted_signer_text(env, sys::SIGNERS)? {
        keys = sig::allowed_keys(&text);
    }
    if keys.is_empty() {
        if let Some(text) = sys::trusted_signer_text(env, sys::SIGNERS_PEM)? {
            keys.extend(sig::pem_key(&text));
        }
    }
    if keys.is_empty() {
        return Err("this board holds no root-owned signing key of the framework (pair it again with the board's \
                    password); nothing was changed"
            .into());
    }
    let armored = String::from_utf8_lossy(&signature).into_owned();
    sig::verify(&data, &armored, &keys)
        .map_err(|e| format!("the bundle's manifest: {}; nothing was changed", e.text()))?;
    let m = manifest::parse(&data).map_err(|e| format!("the bundle's manifest: {e}; nothing was changed"))?;
    if m.bundle_id != bid {
        return Err("the manifest is the one of another bundle; nothing was changed".into());
    }
    Ok((m, data, signature))
}

// --- the component's root copy ------------------------------------------------------------------------------------

#[derive(Debug, PartialEq)]
struct Item {
    rel: String,
    dir: bool,
    size: u64,
    mtime: i64,
    exec: bool,
}

/// Every entry below `top` (plain files and folders only, all of owner `uid`), sorted.
fn walk(top: &Path, uid: u32) -> Result<Vec<Item>, String> {
    fn go(dir: &Path, rel: &str, uid: u32, depth: usize, out: &mut Vec<Item>) -> Result<(), String> {
        if depth > MAX_TREE_DEPTH {
            return Err("the component's folder is nested too deep".into());
        }
        let mut names: Vec<String> = fs::read_dir(dir)
            .map_err(|e| format!("{}: {e}", dir.display()))?
            .filter_map(|e| e.ok().map(|e| e.file_name().to_string_lossy().into_owned()))
            .collect();
        names.sort();
        for name in names {
            if !manifest::is_file(&name) && !name.starts_with('.') {
                return Err(format!("the component's folder holds a name llmcf never writes: {name:?}"));
            }
            if name.contains('/') || name == "." || name == ".." || name.ends_with(".part") {
                continue;
            }
            let path = dir.join(&name);
            let meta = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
            let child = if rel.is_empty() { name.clone() } else { format!("{rel}/{name}") };
            if meta.uid() != uid {
                return Err(format!("{child} in the component's folder has another owner"));
            }
            if meta.file_type().is_dir() {
                out.push(Item { rel: child.clone(), dir: true, size: 0, mtime: 0, exec: false });
                go(&path, &child, uid, depth + 1, out)?;
            } else if meta.file_type().is_file() {
                out.push(Item {
                    rel: child,
                    dir: false,
                    size: meta.len(),
                    mtime: meta.mtime(),
                    exec: meta.mode() & 0o100 != 0,
                });
            } else {
                return Err(format!("{child} in the component's folder is a link or a special file"));
            }
            if out.len() > MAX_TREE_ENTRIES {
                return Err("the component's folder holds too many files".into());
            }
        }
        Ok(())
    }
    let mut out = Vec::new();
    go(top, "", uid, 0, &mut out)?;
    Ok(out)
}

fn copy_file_checked(from: &Path, to: &Path, uid: u32, want_sha: Option<&str>) -> Result<(), String> {
    let before = fs::symlink_metadata(from).map_err(|e| e.to_string())?;
    let mut src = File::open(from).map_err(|e| format!("{}: {e}", from.display()))?;
    let after = src.metadata().map_err(|e| e.to_string())?;
    if after.dev() != before.dev() || after.ino() != before.ino() || after.uid() != uid || !after.is_file() {
        return Err(format!("{} changed while it was copied", from.display()));
    }
    let mut dst = OpenOptions::new().write(true).create_new(true).mode(0o600).open(to).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = src.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        hash.update(&buf[..n]);
        dst.write_all(&buf[..n]).map_err(|e| e.to_string())?;
    }
    dst.sync_all().map_err(|e| e.to_string())?;
    if let Some(want) = want_sha {
        if sig::hex(&hash.finalize()) != want {
            return Err(format!("{} does not match its sha256 in the manifest", from.display()));
        }
    }
    let modified = after.modified().map_err(|e| e.to_string())?;
    dst.set_modified(modified).map_err(|e| e.to_string())?;
    Ok(())
}

struct Places {
    source: PathBuf,
    dest: PathBuf,
}

impl<'a> Ctx<'a> {
    fn places(&self, comp: &str) -> Result<Places, String> {
        let source = sys::account_folder(
            self.env,
            &self.account.home,
            &[sys::BUNDLE_REL, &self.bid, "components", comp],
            self.account.uid,
        )
        .map_err(|e| {
            format!("the component {comp} is not installed for the account yet (run the deploy first): {e}")
        })?;
        Ok(Places { source, dest: self.env.path(sys::COPY_BASE).join(&self.bid).join(comp) })
    }

    fn copy_is_current(&self, comp: &Component) -> bool {
        let Ok(p) = self.places(&comp.id) else { return false };
        match (walk(&p.source, self.account.uid), walk(&p.dest, self.env.root_uid())) {
            (Ok(a), Ok(b)) => a == b && a.iter().any(|i| i.rel == "start.sh"),
            _ => false,
        }
    }

    /// The root copy of the component's folder (/var/lib/llmcf/bundle/<bid>/<component>): root-owned, readable by
    /// the unit's account group only, every file of the manifest checked against its sha256 while it is copied.
    fn make_copy(&mut self, comp: &Component, gid: u32) -> Result<bool, String> {
        if self.copy_is_current(comp) {
            return Ok(false);
        }
        let p = self.places(&comp.id)?;
        let items = walk(&p.source, self.account.uid)?;
        if !items.iter().any(|i| i.rel == "start.sh" && !i.dir) {
            return Err(format!("the component {} has no start script yet (run the deploy first)", comp.id));
        }
        let listed: Vec<&manifest::FileEntry> = comp.files.iter().collect();
        for f in &listed {
            if !items.iter().any(|i| i.rel == f.name && !i.dir && i.size == f.size) {
                return Err(format!(
                    "the file {} of component {} is not in place yet (run the deploy first)",
                    f.name, comp.id
                ));
            }
        }
        let parent = p.dest.parent().ok_or("no folder")?.to_path_buf();
        mkdirs(self.env, &parent, 0o755)?;
        let tmp = parent.join(format!(".{}.llmcf-tmp", comp.id));
        if exists(&tmp) {
            fs::remove_dir_all(&tmp).map_err(|e| e.to_string())?;
        }
        fs::create_dir(&tmp).map_err(|e| e.to_string())?;
        let root = self.env.root_uid();
        let result = (|| -> Result<(), String> {
            for item in &items {
                let to = tmp.join(&item.rel);
                if item.dir {
                    fs::create_dir(&to).map_err(|e| e.to_string())?;
                    continue;
                }
                let want = listed.iter().find(|f| f.name == item.rel).map(|f| f.sha256.as_str());
                copy_file_checked(&p.source.join(&item.rel), &to, self.account.uid, want)?;
                chown(self.env, &to, root, gid)?;
                fs::set_permissions(&to, fs::Permissions::from_mode(if item.exec { 0o750 } else { 0o640 }))
                    .map_err(|e| e.to_string())?;
            }
            for item in items.iter().filter(|i| i.dir) {
                let to = tmp.join(&item.rel);
                chown(self.env, &to, root, gid)?;
                fs::set_permissions(&to, fs::Permissions::from_mode(0o750)).map_err(|e| e.to_string())?;
            }
            chown(self.env, &tmp, root, gid)?;
            fs::set_permissions(&tmp, fs::Permissions::from_mode(0o750)).map_err(|e| e.to_string())
        })();
        if let Err(e) = result {
            let _ = fs::remove_dir_all(&tmp);
            return Err(e);
        }
        let backup = if exists(&p.dest) {
            let (id, to) = self.next_backup()?;
            fs::rename(&p.dest, &to).map_err(|e| e.to_string())?;
            id
        } else {
            "-".into()
        };
        fs::rename(&tmp, &p.dest).map_err(|e| e.to_string())?;
        self.note("tree-made", &[&comp.id, &backup])?;
        Ok(true)
    }

    /// The runtime's key file of the board account, copied for the unit's account (its own home, mode 0600), so
    /// clients keep using the same key; nothing when the account has none (the runtime makes one itself then).
    fn copy_key(&mut self, user: &Account) -> Result<bool, String> {
        let Ok(folder) =
            sys::account_folder(self.env, &self.account.home, &[".config/llm-framework"], self.account.uid)
        else {
            return Ok(false);
        };
        let Ok(data) = sys::read_checked(&folder.join("llama-server.key"), self.account.uid, 4096) else {
            return Ok(false);
        };
        let home = format!("{}/{}", sys::HOME_BASE, user.name);
        for dir in [format!("{home}/.config"), format!("{home}/.config/llm-framework")] {
            let path = self.env.path(&dir);
            if !exists(&path) {
                fs::create_dir(&path).map_err(|e| e.to_string())?;
                chown(self.env, &path, user.uid, user.gid)?;
                fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())?;
            }
        }
        let rel = format!("var/lib/llmcf/home/{}/{}", user.name, sys::KEY_REL);
        self.put_file(&rel, &data, 0o600, (user.uid, user.gid))
    }
}

// --- the texts llmcf-install writes -------------------------------------------------------------------------------

/// The unit of a component: a fixed template with systemd's hardening, the component's root copy's start.sh.
pub fn unit_text(bid: &str, comp: &str, user: &str) -> String {
    let dir = format!("{}/{bid}/{comp}", sys::COPY_BASE);
    format!(
        "# Written by llmcf-install for bundle {bid}, component {comp}. Do not edit: llmcf-install writes it again.\n\
         [Unit]\n\
         Description=llmcf bundle {bid}: {comp}\n\
         After=network-online.target\n\
         Wants=network-online.target\n\
         \n\
         [Service]\n\
         Type=simple\n\
         User={user}\n\
         WorkingDirectory={dir}\n\
         ExecStart=/bin/sh {dir}/start.sh\n\
         Restart=on-failure\n\
         RestartSec=5\n\
         UMask=0077\n\
         NoNewPrivileges=yes\n\
         ProtectSystem=strict\n\
         ProtectHome=yes\n\
         PrivateTmp=yes\n\
         ReadWritePaths={home}/{user}\n\
         ProtectKernelTunables=yes\n\
         ProtectKernelModules=yes\n\
         ProtectControlGroups=yes\n\
         RestrictSUIDSGID=yes\n\
         LockPersonality=yes\n\
         \n\
         [Install]\n\
         WantedBy=multi-user.target\n",
        home = sys::HOME_BASE
    )
}

/// The cgroup drop-in of a unit (systemd's MemoryMax and CPUWeight).
pub fn cgroup_text(memory_mb: u64, cpu_weight: Option<u64>) -> String {
    let mut text = format!("# Written by llmcf-install. Do not edit.\n[Service]\nMemoryMax={memory_mb}M\n");
    if let Some(w) = cpu_weight {
        text.push_str(&format!("CPUWeight={w}\n"));
    }
    text
}

fn dropin_rel(unit: &str, file: &str) -> String {
    format!("etc/systemd/system/{unit}.d/{file}")
}

fn root_file_rel(a: &RootAction) -> String {
    let file = a.file.as_deref().unwrap_or("");
    match a.place.as_deref() {
        Some("unit-dropin") => dropin_rel(a.unit.as_deref().unwrap_or(""), file),
        Some("tmpfiles") => format!("etc/tmpfiles.d/{file}"),
        _ => format!("etc/sysctl.d/{file}"),
    }
}

fn llmcf_account(a: &Account) -> bool {
    a.comment == sys::USER_COMMENT && a.home == format!("{}/{}", sys::HOME_BASE, a.name) && a.uid != 0
}

/// Another bundle's journal that names `user` (it uses or made it).
fn used_elsewhere(env: &Env, bid: &str, user: &str) -> Option<String> {
    let base = env.path(sys::STATE_BASE);
    for e in fs::read_dir(base).ok()?.flatten() {
        let other = e.file_name().to_string_lossy().into_owned();
        if other == bid || !is_id(&other) {
            continue;
        }
        if let Ok(entries) = read_journal(&e.path().join("journal")) {
            if entries.iter().any(|x| (x.kind == "user-created" || x.kind == "user-used") && x.args[0] == user) {
                return Some(other);
            }
        }
    }
    None
}

// --- one action: is it done, may it be done, do it ----------------------------------------------------------------

#[derive(PartialEq)]
enum State {
    Done,
    Pending,
    Conflict(String),
}

impl<'a> Ctx<'a> {
    fn is_enabled(&self, unit: &str) -> bool {
        match systemctl(self.env) {
            Some(prog) => {
                let (ok, out) = self.env.run(&prog, &["is-enabled", unit]);
                ok && out.trim() == "enabled"
            }
            None => false,
        }
    }

    fn state_of(&self, m: &Manifest, a: &RootAction) -> State {
        let env = self.env;
        match a.action.as_str() {
            "create_user" => match sys::account(env, a.user.as_deref().unwrap_or("")) {
                None => State::Pending,
                Some(acc) if llmcf_account(&acc) => State::Done,
                Some(acc) if acc.uid == 0 => State::Conflict(format!("the account {} has uid 0", acc.name)),
                Some(acc) => State::Conflict(format!(
                    "an account {} exists that llmcf-install did not make; it is never taken over",
                    acc.name
                )),
            },
            "remove_user" => match sys::account(env, a.user.as_deref().unwrap_or("")) {
                None => State::Done,
                Some(acc) if !llmcf_account(&acc) => State::Conflict(format!(
                    "the account {} was not made by llmcf-install; it is never removed",
                    acc.name
                )),
                Some(acc) => match used_elsewhere(env, &self.bid, &acc.name) {
                    Some(other) => State::Conflict(format!("the account {} is used by bundle {other}", acc.name)),
                    None => State::Pending,
                },
            },
            "install_unit" => {
                let unit = a.unit.as_deref().unwrap_or("");
                let user = a.user.as_deref().unwrap_or("");
                let Some(comp) = m.component(&a.component) else { return State::Pending };
                let text = unit_text(&self.bid, &comp.id, user);
                let path = env.path(&format!("/etc/systemd/system/{unit}"));
                if same_text(&path, text.as_bytes()) && self.copy_is_current(comp) && self.is_enabled(unit) {
                    State::Done
                } else {
                    State::Pending
                }
            }
            "remove_unit" => {
                let unit = a.unit.as_deref().unwrap_or("");
                if exists(&env.path(&format!("/etc/systemd/system/{unit}"))) {
                    State::Pending
                } else {
                    State::Done
                }
            }
            "set_cgroup" => {
                let rel = dropin_rel(a.unit.as_deref().unwrap_or(""), DROPIN);
                let text = cgroup_text(a.memory_max_mb.unwrap_or(0), a.cpu_weight);
                if same_text(&env.path(&format!("/{rel}")), text.as_bytes()) {
                    State::Done
                } else {
                    State::Pending
                }
            }
            "remove_cgroup" => {
                let rel = dropin_rel(a.unit.as_deref().unwrap_or(""), DROPIN);
                if exists(&env.path(&format!("/{rel}"))) {
                    State::Pending
                } else {
                    State::Done
                }
            }
            "place_root_file" => {
                let path = env.path(&format!("/{}", root_file_rel(a)));
                let want = m
                    .component(&a.component)
                    .and_then(|c| c.files.iter().find(|f| Some(f.name.as_str()) == a.file.as_deref()))
                    .map(|f| f.sha256.clone())
                    .unwrap_or_default();
                match fs::read(&path) {
                    Ok(d) if sig::sha256_hex(&d) == want => State::Done,
                    _ => State::Pending,
                }
            }
            _ => {
                if exists(&self.env.path(&format!("/{}", root_file_rel(a)))) {
                    State::Pending
                } else {
                    State::Done
                }
            }
        }
    }

    /// What must hold before an apply changes anything: the tools it needs, no account conflict, the component's
    /// files in place for a unit or a root file.
    fn precheck(&self, m: &Manifest, states: &[State]) -> Result<(), String> {
        for (a, s) in m.root_actions.iter().zip(states) {
            if let State::Conflict(why) = s {
                return Err(format!("{} {}: {why}; nothing was changed", a.action, a.component));
            }
        }
        let pending =
            |name: &str| m.root_actions.iter().zip(states).any(|(a, s)| a.action == name && *s == State::Pending);
        let units = m.root_actions.iter().any(|a| a.unit.is_some());
        if units {
            if let Some(init) = &m.init_system {
                if init != "systemd" {
                    return Err(format!("the bundle's units need systemd; the board runs {init}; nothing was changed"));
                }
            }
            if systemctl(self.env).is_none() {
                return Err(
                    "the bundle's units need systemd, and the board has no systemctl; nothing was changed".into()
                );
            }
        }
        if pending("create_user")
            && self.env.tool(&["/usr/sbin/useradd", "/sbin/useradd", "/usr/bin/useradd"]).is_none()
            && self.env.tool(&["/usr/sbin/adduser", "/sbin/adduser", "/bin/adduser", "/usr/bin/adduser"]).is_none()
        {
            return Err(
                "the board has neither useradd nor adduser to make the bundle's account; nothing was changed".into()
            );
        }
        if pending("remove_user")
            && self.env.tool(&["/usr/sbin/userdel", "/sbin/userdel"]).is_none()
            && self.env.tool(&["/usr/sbin/deluser", "/sbin/deluser", "/bin/deluser"]).is_none()
        {
            return Err("the board has neither userdel nor deluser; nothing was changed".into());
        }
        for (a, s) in m.root_actions.iter().zip(states) {
            if *s != State::Pending {
                continue;
            }
            if a.action == "install_unit" {
                let comp = m.component(&a.component).ok_or("no component")?;
                if !self.copy_is_current(comp) {
                    let p = self.places(&comp.id)?;
                    let items = walk(&p.source, self.account.uid)?;
                    if !items.iter().any(|i| i.rel == "start.sh") {
                        return Err(format!(
                            "the component {} has no start script yet (run the deploy first); \
                                            nothing was changed",
                            comp.id
                        ));
                    }
                    for f in &comp.files {
                        if !items.iter().any(|i| i.rel == f.name && i.size == f.size) {
                            return Err(format!(
                                "the file {} of component {} is not in place yet (run the deploy \
                                                first); nothing was changed",
                                f.name, comp.id
                            ));
                        }
                    }
                }
                let user = a.user.as_deref().unwrap_or("");
                let made_here =
                    m.root_actions.iter().any(|b| b.action == "create_user" && b.user.as_deref() == Some(user));
                if !made_here && sys::account(self.env, user).is_none() {
                    return Err(format!(
                        "the unit's account {user} does not exist and the bundle does not make it; \
                                        nothing was changed"
                    ));
                }
            }
            if a.action == "place_root_file" {
                let p = self.places(&a.component)?;
                let name = a.file.as_deref().unwrap_or("");
                let meta = fs::symlink_metadata(p.source.join(name)).map_err(|_| {
                    format!("the file {name} of component {} is not in place yet; nothing was changed", a.component)
                })?;
                if !meta.file_type().is_file() || meta.len() > MAX_ROOT_FILE {
                    return Err(format!(
                        "the file {name} of component {} is no small plain file; nothing was changed",
                        a.component
                    ));
                }
            }
        }
        Ok(())
    }

    fn make_user(&mut self, user: &str) -> Result<(), String> {
        let env = self.env;
        let home = format!("{}/{user}", sys::HOME_BASE);
        mkdirs(env, &env.path(sys::HOME_BASE), 0o755)?;
        let shell = nologin(env);
        let (ok, out) = if let Some(useradd) = env.tool(&["/usr/sbin/useradd", "/sbin/useradd", "/usr/bin/useradd"]) {
            env.run(
                &useradd,
                &[
                    "--system",
                    "--user-group",
                    "--no-create-home",
                    "--home-dir",
                    &home,
                    "--shell",
                    shell,
                    "--comment",
                    sys::USER_COMMENT,
                    user,
                ],
            )
        } else {
            let adduser = env
                .tool(&["/usr/sbin/adduser", "/sbin/adduser", "/bin/adduser", "/usr/bin/adduser"])
                .ok_or("no useradd or adduser")?;
            if let Some(addgroup) = env.tool(&["/usr/sbin/addgroup", "/sbin/addgroup", "/bin/addgroup"]) {
                if !sys::group_exists(env, user) {
                    let _ = env.run(&addgroup, &["-S", user]);
                }
            }
            env.run(&adduser, &["-S", "-D", "-H", "-h", &home, "-s", shell, "-g", sys::USER_COMMENT, "-G", user, user])
        };
        let made = sys::account(env, user);
        match made {
            Some(acc) if ok && llmcf_account(&acc) => {
                self.note("user-created", &[user])?;
                let path = env.path(&home);
                if !exists(&path) {
                    fs::create_dir(&path).map_err(|e| e.to_string())?;
                }
                chown(env, &path, acc.uid, acc.gid)?;
                fs::set_permissions(&path, fs::Permissions::from_mode(0o750)).map_err(|e| e.to_string())?;
                Ok(())
            }
            _ => Err(format!(
                "the account {user} could not be made: {}",
                out.trim().chars().take(200).collect::<String>()
            )),
        }
    }

    fn delete_user(&mut self, user: &str) -> Result<String, String> {
        let env = self.env;
        let (ok, out) = if let Some(userdel) = env.tool(&["/usr/sbin/userdel", "/sbin/userdel"]) {
            env.run(&userdel, &[user])
        } else {
            let deluser = env.tool(&["/usr/sbin/deluser", "/sbin/deluser", "/bin/deluser"]).ok_or("no userdel")?;
            env.run(&deluser, &[user])
        };
        if !ok || sys::account(env, user).is_some() {
            return Err(format!(
                "the account {user} could not be removed: {}",
                out.trim().chars().take(200).collect::<String>()
            ));
        }
        if sys::group_exists(env, user) {
            if let Some(g) = env.tool(&["/usr/sbin/groupdel", "/sbin/groupdel", "/usr/sbin/delgroup", "/bin/delgroup"])
            {
                let _ = env.run(&g, &[user]);
            }
        }
        let home = env.path(&format!("{}/{user}", sys::HOME_BASE));
        if exists(&home) {
            let (id, to) = self.next_backup()?;
            fs::rename(&home, &to).map_err(|e| e.to_string())?;
            return Ok(id);
        }
        Ok("-".into())
    }

    fn systemctl_run(&mut self, args: &[&str]) -> Result<(), String> {
        let prog = systemctl(self.env).ok_or("the board has no systemctl")?;
        let (ok, out) = self.env.run(&prog, args);
        if ok {
            Ok(())
        } else {
            Err(format!("systemctl {} failed: {}", args.join(" "), out.trim().chars().take(200).collect::<String>()))
        }
    }

    fn reload_now(&mut self) -> Result<(), String> {
        if self.reload {
            self.reload = false;
            self.systemctl_run(&["daemon-reload"])?;
        }
        Ok(())
    }

    fn perform(&mut self, m: &Manifest, a: &RootAction) -> Result<(), String> {
        match a.action.as_str() {
            "create_user" => self.make_user(a.user.as_deref().unwrap_or("")),
            "remove_user" => {
                let user = a.user.as_deref().unwrap_or("");
                let backup = self.delete_user(user)?;
                self.note("user-removed", &[user, &backup])
            }
            "install_unit" => {
                let comp = m.component(&a.component).ok_or("no component")?.clone();
                let user = a.user.as_deref().unwrap_or("");
                let acc = sys::account(self.env, user).ok_or_else(|| format!("the account {user} does not exist"))?;
                self.make_copy(&comp, acc.gid)?;
                if comp.keygen {
                    self.copy_key(&acc)?;
                }
                let unit = a.unit.as_deref().unwrap_or("");
                let root = self.env.root_uid();
                if self.put_file(
                    &format!("etc/systemd/system/{unit}"),
                    unit_text(&self.bid, &comp.id, user).as_bytes(),
                    0o644,
                    (root, root),
                )? {
                    self.reload = true;
                }
                Ok(())
            }
            "remove_unit" => {
                let unit = a.unit.as_deref().unwrap_or("");
                if self.is_enabled(unit) {
                    self.systemctl_run(&["disable", "--now", unit])?;
                    self.note("unit-disabled", &[unit])?;
                }
                if self.drop_file(&format!("etc/systemd/system/{unit}"))? {
                    self.reload = true;
                }
                Ok(())
            }
            "set_cgroup" => {
                let rel = dropin_rel(a.unit.as_deref().unwrap_or(""), DROPIN);
                let root = self.env.root_uid();
                let text = cgroup_text(a.memory_max_mb.unwrap_or(0), a.cpu_weight);
                if self.put_file(&rel, text.as_bytes(), 0o644, (root, root))? {
                    self.reload = true;
                }
                Ok(())
            }
            "remove_cgroup" => {
                if self.drop_file(&dropin_rel(a.unit.as_deref().unwrap_or(""), DROPIN))? {
                    self.reload = true;
                }
                Ok(())
            }
            "place_root_file" => {
                let comp = m.component(&a.component).ok_or("no component")?;
                let name = a.file.as_deref().unwrap_or("");
                let entry = comp.files.iter().find(|f| f.name == name).ok_or("no file")?;
                let p = self.places(&comp.id)?;
                let data = sys::read_checked(&p.source.join(name), self.account.uid, MAX_ROOT_FILE)?;
                if sig::sha256_hex(&data) != entry.sha256 {
                    return Err(format!("the file {name} does not match its sha256 in the manifest"));
                }
                let root = self.env.root_uid();
                if self.put_file(&root_file_rel(a), &data, 0o644, (root, root))?
                    && a.place.as_deref() == Some("unit-dropin")
                {
                    self.reload = true;
                }
                Ok(())
            }
            _ => {
                if self.drop_file(&root_file_rel(a))? && a.place.as_deref() == Some("unit-dropin") {
                    self.reload = true;
                }
                Ok(())
            }
        }
    }

    // --- undo of one journal entry ---------------------------------------------------------------------------

    fn undo(&mut self, e: &Entry) -> Result<(), String> {
        let env = self.env;
        let what = e.args.join(" ");
        match e.kind.as_str() {
            "user-used" => return Ok(()),
            "user-created" => {
                let user = &e.args[0];
                if let Some(other) = used_elsewhere(env, &self.bid, user) {
                    say("kept", "user", &format!("{user} (bundle {other} uses it)"));
                    return Ok(());
                }
                match sys::account(env, user) {
                    Some(acc) if llmcf_account(&acc) => {
                        self.delete_user(user)?;
                        let _ = fs::remove_dir_all(env.path(&format!("{}/{user}", sys::HOME_BASE)));
                    }
                    Some(_) => {
                        say("kept", "user", &format!("{user} (it is no account llmcf-install made any more)"));
                        return Ok(());
                    }
                    None => {}
                }
            }
            "user-removed" => {
                let user = e.args[0].clone();
                if sys::account(env, &user).is_none() {
                    self.make_user(&user)?;
                    self.journal.pop(); // make_user noted it; the undo takes no new journal line
                    self.save_journal()?;
                }
                if e.args[1] != "-" {
                    let home = env.path(&format!("{}/{user}", sys::HOME_BASE));
                    let _ = fs::remove_dir_all(&home);
                    fs::rename(self.backup_path(&e.args[1]), &home).map_err(|x| x.to_string())?;
                }
                say("restored", "user", &user);
                self.changed += 1;
                return Ok(());
            }
            "tree-made" => {
                let dest = env.path(sys::COPY_BASE).join(&self.bid).join(&e.args[0]);
                if exists(&dest) {
                    fs::remove_dir_all(&dest).map_err(|x| x.to_string())?;
                }
                if e.args[1] != "-" {
                    fs::rename(self.backup_path(&e.args[1]), &dest).map_err(|x| x.to_string())?;
                }
            }
            "file-made" => {
                let path = env.path(&format!("/{}", e.args[0]));
                if exists(&path) {
                    fs::remove_file(&path).map_err(|x| x.to_string())?;
                }
                if e.args[1] != "-" {
                    fs::rename(self.backup_path(&e.args[1]), &path).map_err(|x| x.to_string())?;
                }
                if let Some(dir) = path.parent() {
                    if dir.extension().map(|x| x == "d").unwrap_or(false) {
                        let _ = fs::remove_dir(dir);
                    }
                }
                if e.args[0].starts_with("etc/systemd/") {
                    self.reload = true;
                }
            }
            "file-gone" => {
                let path = env.path(&format!("/{}", e.args[0]));
                if let Some(dir) = path.parent() {
                    mkdirs(env, dir, 0o755)?;
                }
                fs::rename(self.backup_path(&e.args[1]), &path).map_err(|x| x.to_string())?;
                if e.args[0].starts_with("etc/systemd/") {
                    self.reload = true;
                }
                say("restored", "file", &e.args[0]);
                self.changed += 1;
                return Ok(());
            }
            "unit-enabled" => {
                self.reload_now()?;
                self.systemctl_run(&["disable", "--now", &e.args[0]])?;
            }
            "unit-disabled" => {
                self.reload_now()?;
                self.systemctl_run(&["enable", "--now", &e.args[0]])?;
                say("restored", "unit", &what);
                self.changed += 1;
                return Ok(());
            }
            _ => return Err("the journal holds a line llmcf-install never wrote".into()),
        }
        say("removed", &e.kind, &what);
        self.changed += 1;
        Ok(())
    }

    fn undo_all(&mut self, entries: Vec<Entry>) -> Result<(), String> {
        for e in entries.iter().rev() {
            self.undo(e)?;
            if let Some(i) = self.journal.iter().rposition(|x| x == e) {
                self.journal.remove(i);
            }
            self.save_journal()?;
        }
        self.reload_now()
    }
}

// --- the actions --------------------------------------------------------------------------------------------------

fn finish(ctx: &Ctx, done: Done) -> i32 {
    let detail = if done.detail.is_empty() { String::new() } else { format!(" {}", done.detail) };
    println!("llmcf-bundle: result={} changed={}{}", done.result, ctx.changed, detail);
    done.code
}

pub fn run(env: &Env, action: &str, bid: &str, account: Account) -> i32 {
    let mut ctx = Ctx::new(env, bid, account);
    let done = match action {
        "rollback" | "uninstall" | "plan-uninstall" => undo_action(&mut ctx, action),
        _ => manifest_action(&mut ctx, action),
    };
    finish(&ctx, done)
}

fn undo_action(ctx: &mut Ctx, action: &str) -> Done {
    if !exists(&ctx.state) {
        return Done::ok("absent", "llmcf-install made nothing of this bundle on the board");
    }
    if let Err(e) = sys::owned_safe(&ctx.state, ctx.env.root_uid()) {
        return Done::refused(format!("the bundle's root state is not safe: {e}"));
    }
    if let Err(e) = ctx.load_journal() {
        return Done::refused(e);
    }
    if action == "plan-uninstall" {
        for e in ctx.journal.iter().rev() {
            if e.kind != "user-used" {
                say("would-remove", &e.kind, &e.args.join(" "));
            }
        }
        return Done::ok("plan", "");
    }
    let _lock = match ctx.lock() {
        Ok(l) => l,
        Err(e) => return Done::refused(e),
    };
    let entries: Vec<Entry> = if action == "rollback" {
        let last = ctx.journal.iter().map(|e| e.run).max().unwrap_or(0);
        ctx.journal.iter().filter(|e| e.run == last).cloned().collect()
    } else {
        ctx.journal.clone()
    };
    if action == "rollback" && entries.is_empty() {
        return Done::ok("ok", "nothing to roll back");
    }
    if let Err(e) = ctx.undo_all(entries) {
        return Done::failed(format!("{e}; the journal keeps what is left, run it again"));
    }
    if action == "rollback" {
        return Done::ok("ok", "the last run of the root actions was rolled back");
    }
    drop(_lock);
    let copies = ctx.env.path(sys::COPY_BASE).join(&ctx.bid);
    let _ = fs::remove_dir(&copies);
    if let Err(e) = fs::remove_dir_all(&ctx.state) {
        return Done::failed(format!("the bundle's root state could not be removed: {e}"));
    }
    Done::ok("ok", "the root part was uninstalled")
}

fn manifest_action(ctx: &mut Ctx, action: &str) -> Done {
    let (m, data, signature) = match load_manifest(ctx.env, &ctx.bid, &ctx.account) {
        Ok(x) => x,
        Err(e) => return Done::refused(e),
    };
    if exists(&ctx.state) {
        if let Err(e) = sys::owned_safe(&ctx.state, ctx.env.root_uid()) {
            return Done::refused(format!("the bundle's root state is not safe: {e}"));
        }
        if let Err(e) = ctx.load_journal() {
            return Done::refused(e);
        }
    }
    if m.mode == "unprivileged" && action != "status" {
        return Done::refused("the bundle is the unprivileged-only variant: it has no root actions to run");
    }
    let states: Vec<State> = m.root_actions.iter().map(|a| ctx.state_of(&m, a)).collect();
    match action {
        "status" => {
            if m.root_actions.is_empty() || m.mode == "unprivileged" {
                return Done::ok("installed", "root=0");
            }
            let mut pending = 0;
            for (a, s) in m.root_actions.iter().zip(&states) {
                match s {
                    State::Done => say("already", &a.action, &a.what()),
                    _ => {
                        pending += 1;
                        say("pending", &a.action, &a.what());
                    }
                }
            }
            if pending == 0 {
                Done::ok("installed", format!("root={}", m.root_actions.len()))
            } else if !exists(&ctx.state) && pending == m.root_actions.len() {
                Done::ok("absent", "llmcf-install made nothing of this bundle on the board")
            } else {
                Done::ok("partial", format!("pending={pending}"))
            }
        }
        "plan" => {
            if let Err(e) = ctx.precheck(&m, &states) {
                return Done::refused(e);
            }
            let mut pending = 0;
            for (a, s) in m.root_actions.iter().zip(&states) {
                if *s == State::Done {
                    say("already", &a.action, &a.what());
                } else {
                    pending += 1;
                    say("would", &a.action, &a.what());
                }
            }
            Done::ok("plan", format!("pending={pending} root={}", m.root_actions.len()))
        }
        _ => apply(ctx, &m, &states, &data, &signature),
    }
}

fn apply(ctx: &mut Ctx, m: &Manifest, states: &[State], data: &[u8], signature: &[u8]) -> Done {
    if let Err(e) = ctx.precheck(m, states) {
        return Done::refused(e);
    }
    let lock = match ctx.lock() {
        Ok(l) => l,
        Err(e) => return Done::refused(e),
    };
    let root = ctx.env.root_uid();
    // the root copy of the signed manifest: status, rollback and uninstall work after the account's copy is gone
    if let Err(e) = write_atomic(ctx.env, &ctx.state.join("manifest.json"), data, 0o600, (root, root))
        .and_then(|_| write_atomic(ctx.env, &ctx.state.join("manifest.json.sig"), signature, 0o600, (root, root)))
    {
        return Done::refused(format!("the bundle's root state could not be written: {e}; nothing was changed"));
    }
    let mut touched: HashSet<String> = HashSet::new();
    for (a, s) in m.root_actions.iter().zip(states) {
        if *s == State::Done {
            say("already", &a.action, &a.what());
            if a.action == "create_user" {
                let user = a.user.clone().unwrap_or_default();
                if !ctx.journal.iter().any(|e| (e.kind == "user-created" || e.kind == "user-used") && e.args[0] == user)
                {
                    if let Err(e) = ctx.note("user-used", &[&user]) {
                        return Done::failed(e);
                    }
                }
            }
            continue;
        }
        let before = ctx.journal.len();
        if let Err(e) = ctx.perform(m, a) {
            say("failed", &a.action, &a.what());
            drop(lock);
            return Done::failed(format!("{e}; what was done is in the journal: roll back or run it again"));
        }
        if let Some(unit) = &a.unit {
            touched.insert(unit.clone());
        }
        if ctx.journal.len() > before {
            ctx.changed += 1;
        }
        say("done", &a.action, &a.what());
    }
    if let Err(e) = ctx.reload_now() {
        return Done::failed(format!("{e}; roll back or run it again"));
    }
    for a in m.root_actions.iter().filter(|a| a.action == "install_unit") {
        let unit = a.unit.as_deref().unwrap_or("");
        if !ctx.is_enabled(unit) {
            if let Err(e) = ctx.systemctl_run(&["enable", "--now", unit]) {
                say("failed", "enable", unit);
                return Done::failed(format!("{e}; roll back or run it again"));
            }
            if let Err(e) = ctx.note("unit-enabled", &[unit]) {
                return Done::failed(e);
            }
            ctx.changed += 1;
            say("done", "enable", unit);
        } else if touched.contains(unit) {
            let _ = ctx.systemctl_run(&["try-restart", unit]);
        }
    }
    drop(lock);
    if ctx.changed == 0 {
        Done::ok("ok", "everything was in place already (nothing changed)")
    } else {
        Done::ok("ok", "")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_journal_takes_only_its_own_lines() {
        assert!(parse_entry("1\tuser-created\tllmcf").is_some());
        assert!(parse_entry("2\tfile-made\tetc/systemd/system/llmcf-llm.service\t-").is_some());
        assert!(parse_entry("2\tfile-made\tetc/systemd/system/llmcf-llm.service.d/llmcf-cgroup.conf\t3").is_some());
        assert!(
            parse_entry("2\tfile-made\tvar/lib/llmcf/home/llmcf/.config/llm-framework/llama-server.key\t-").is_some()
        );
        for bad in [
            "1\tuser-created\troot",
            "1\tfile-made\tetc/passwd\t-",
            "1\tfile-made\tetc/systemd/system/sshd.service\t-",
            "1\tfile-made\tetc/systemd/system/llmcf-a.service\t../x",
            "1\ttree-made\t../x\t-",
            "x\tuser-used\tllmcf",
            "1\trun-shell\tid",
            "1\tfile-made\tetc/sysctl.d/../../passwd\t-",
        ] {
            assert!(parse_entry(bad).is_none(), "{bad}");
        }
    }

    #[test]
    fn the_unit_is_hardened_and_starts_the_root_copy() {
        let text = unit_text("b1", "llm", "llmcf");
        for line in [
            "User=llmcf",
            "NoNewPrivileges=yes",
            "ProtectSystem=strict",
            "PrivateTmp=yes",
            "ProtectHome=yes",
            "ExecStart=/bin/sh /var/lib/llmcf/bundle/b1/llm/start.sh",
            "ReadWritePaths=/var/lib/llmcf/home/llmcf",
        ] {
            assert!(text.lines().any(|l| l == line), "{line}");
        }
        assert_eq!(
            cgroup_text(512, Some(100)),
            "# Written by llmcf-install. Do not edit.\n[Service]\nMemoryMax=512M\nCPUWeight=100\n"
        );
    }
}
