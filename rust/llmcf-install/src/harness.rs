//! The person's harness on the board (manifest root action run_harness): the tree that came as the component's
//! harness.tar, unpacked root-owned by `apply` (a plain USTAR reader of its own: only files, folders and links that
//! stay inside, every name checked) with its tree checksum computed exactly as orchestrator/runtime/bundle/harness.py
//! does (`tree_sha256`, the vectors in tests/data/harness_tree_vectors.json), and the person's run of the harness's
//! own installer through the framework's harness_watch.sh, detached from the SSH session:
//!
//!   harness-start BUNDLE COMPONENT RUN MODE   MODE dry (its own dry run), run (its questions answered live) or
//!                                             run-yes (its own assume-yes switch, only when the manifest carries it)
//!   harness-read BUNDLE COMPONENT RUN OFFSET  the installer's output from OFFSET (waits a little for more), base64
//!   harness-answer BUNDLE COMPONENT RUN       one line of stdin goes to the installer (never shown, never kept)
//!   harness-stop BUNDLE COMPONENT RUN         ends the installer (the watch still records the end)
//!   harness-records BUNDLE COMPONENT RUN index         what the watch recorded, for the report on the host: the
//!                                                      list of record files with their size and sha256
//!   harness-records BUNDLE COMPONENT RUN N.OFFSET.LEN  one part of record N (at most PART_SIZE bytes), base64
//!
//! Nothing of the records is cut off: they are sent in parts of at most 4 MiB, and the host fetches part by part
//! (reading on from its last good offset after a dropped connection) and checks every file's sha256 at the end.
//!
//! The tree checksum is computed again before every start: a tree that changed on the board is never run. Every
//! record stays root-only on the board (the run folder is 0700); the board account reads them only through these
//! actions.

use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::os::unix::fs::{FileTypeExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use sha2::{Digest, Sha256};

use crate::install::{self, say, Done};
use crate::manifest::{self, Component, HarnessPart, Manifest};
use crate::sig;
use crate::sys::{self, Account, Env};

pub const ACTIONS: [&str; 5] = ["harness-start", "harness-read", "harness-answer", "harness-stop", "harness-records"];
pub const MAX_FILES: usize = 20000;
pub const MAX_BYTES: u64 = 512 * 1024 * 1024;
const MAX_DEPTH: usize = 64;
const READ_WAIT: Duration = Duration::from_secs(15);
const READ_CHUNK: u64 = 256 * 1024;
const MAX_ANSWER: usize = 4096;
/// The largest part of a record one harness-records call sends (harness_run.PART_SIZE on the host).
pub const PART_SIZE: u64 = 4 * 1024 * 1024;
/// The records the host's report reads, in this order: their number is the N of a part call.
pub const RECORDS: [&str; 14] = [
    "exit_code",
    "started",
    "ended",
    "watch",
    "terminal",
    "before/packages.txt",
    "after/packages.txt",
    "before/accounts.txt",
    "after/accounts.txt",
    "before/ports.txt",
    "after/ports.txt",
    "before/files.txt",
    "after/files.txt",
    "trace.txt",
];
/// O_NONBLOCK on the three targets llmcf-install is built for (x86_64, aarch64, armv7: the generic value).
const O_NONBLOCK: i32 = 0o4000;

// --- the tree checksum (harness.py tree_listing / tree_sha256) ------------------------------------------------------

/// (relative path, kind, digest) of every entry below `root`, sorted, a top-level `.git` folder left out: kind "x" an
/// executable file, "f" another file, "l" a link (its target as digest). A special file, an absolute link or too
/// many files or bytes is refused.
pub fn listing(root: &Path) -> Result<Vec<(String, String, String)>, String> {
    fn go(
        root: &Path,
        dir: &Path,
        rel: &str,
        depth: usize,
        size: &mut u64,
        out: &mut Vec<(String, String, String)>,
    ) -> Result<(), String> {
        if depth > MAX_DEPTH {
            return Err("the harness is nested too deep".into());
        }
        for entry in fs::read_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))? {
            let entry = entry.map_err(|e| e.to_string())?;
            let name = entry.file_name().into_string().map_err(|_| "a name in the harness is not UTF-8".to_string())?;
            let path = dir.join(&name);
            if depth == 0 && name == ".git" && fs::metadata(&path).map(|m| m.is_dir()).unwrap_or(false) {
                continue;
            }
            let child = if rel.is_empty() { name.clone() } else { format!("{rel}/{name}") };
            let meta = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
            let ft = meta.file_type();
            if ft.is_symlink() {
                let target = fs::read_link(&path).map_err(|e| e.to_string())?;
                let target = target
                    .into_os_string()
                    .into_string()
                    .map_err(|_| format!("{child}: a link target that is not UTF-8"))?;
                if target.starts_with('/') || !stays_inside(&child, &target) {
                    return Err(format!("{child}: a link that leaves the harness"));
                }
                out.push((child, "l".into(), target));
            } else if ft.is_dir() {
                go(root, &path, &child, depth + 1, size, out)?;
            } else if ft.is_file() {
                *size += meta.len();
                if *size > MAX_BYTES {
                    return Err("the harness is larger than 512 MB".into());
                }
                let kind = if meta.permissions().mode() & 0o111 != 0 { "x" } else { "f" };
                out.push((child, kind.into(), file_sha256(&path)?));
            } else {
                return Err(format!("{child}: not a file (a device or another special file)"));
            }
            if out.len() > MAX_FILES {
                return Err(format!("the harness has more than {MAX_FILES} files"));
            }
        }
        let _ = root;
        Ok(())
    }
    let mut out = Vec::new();
    let mut size = 0u64;
    go(root, root, "", 0, &mut size, &mut out)?;
    out.sort();
    Ok(out)
}

/// One checksum over the listing: "<kind> <digest> <path>\n" for each row, its sha256.
pub fn tree_sha256(rows: &[(String, String, String)]) -> String {
    let mut h = Sha256::new();
    for (path, kind, digest) in rows {
        h.update(format!("{kind} {digest} {path}\n").as_bytes());
    }
    sig::hex(&h.finalize())
}

fn file_sha256(path: &Path) -> Result<String, String> {
    let mut f = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut h = Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
    }
    Ok(sig::hex(&h.finalize()))
}

/// Whether the link `rel` (a path inside the tree) with the relative `target` stays inside the tree (by its words:
/// `..` never climbs above the tree's top).
pub fn stays_inside(rel: &str, target: &str) -> bool {
    if target.is_empty() || target.starts_with('/') || target.contains('\0') {
        return false;
    }
    let mut parts: Vec<&str> = rel.split('/').collect();
    parts.pop();
    for p in target.split('/') {
        match p {
            "" | "." => {}
            ".." => {
                if parts.pop().is_none() {
                    return false;
                }
            }
            other => parts.push(other),
        }
    }
    true
}

// --- the tar file (what harness.py pack writes) ---------------------------------------------------------------------

fn octal(field: &[u8]) -> Option<u64> {
    let text: Vec<u8> = field.iter().copied().take_while(|&c| c != 0).filter(|&c| c != b' ').collect();
    if text.is_empty() {
        return Some(0);
    }
    let mut v: u64 = 0;
    for c in text {
        if !(b'0'..=b'7').contains(&c) {
            return None;
        }
        v = v.checked_mul(8)?.checked_add((c - b'0') as u64)?;
    }
    Some(v)
}

fn cstr(field: &[u8]) -> &[u8] {
    let end = field.iter().position(|&c| c == 0).unwrap_or(field.len());
    &field[..end]
}

fn plain_rel(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 4096
        && !name.starts_with('/')
        && name.split('/').all(|p| !p.is_empty() && p != "." && p != "..")
        && name.bytes().all(|c| c >= 0x20 && c != 0x7f)
}

/// A folder below `top` for the entry `rel`: every part a real folder (made with 0755 where it is missing), never a
/// link.
fn parent_dirs(top: &Path, rel: &str) -> Result<PathBuf, String> {
    let mut at = top.to_path_buf();
    let parts: Vec<&str> = rel.split('/').collect();
    for p in &parts[..parts.len() - 1] {
        at.push(p);
        match fs::symlink_metadata(&at) {
            Ok(m) if m.file_type().is_dir() => {}
            Ok(_) => return Err(format!("{rel}: a folder of it is a file or a link in the tar")),
            Err(_) => {
                fs::create_dir(&at).map_err(|e| e.to_string())?;
                fs::set_permissions(&at, fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
            }
        }
    }
    Ok(top.join(rel))
}

/// Unpacks the plain USTAR file `tar` into the new folder `top`: files (0755 or 0644 by the executable bit),
/// folders and links that stay inside; any other entry, a name with `..`, a link out, an entry twice or too many
/// files or bytes stops it. (The tar's own sha256 was checked against the signed manifest before.)
pub fn unpack(tar: &Path, top: &Path) -> Result<(), String> {
    let mut f = File::open(tar).map_err(|e| format!("{}: {e}", tar.display()))?;
    fs::create_dir(top).map_err(|e| format!("{}: {e}", top.display()))?;
    fs::set_permissions(top, fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    let mut header = [0u8; 512];
    let (mut files, mut bytes) = (0usize, 0u64);
    loop {
        if f.read_exact(&mut header).is_err() {
            return Err("the harness tar ends early".into());
        }
        if header.iter().all(|&c| c == 0) {
            return Ok(());
        }
        if &header[257..263] != b"ustar\0" {
            return Err("the harness tar is no plain USTAR file".into());
        }
        let want = octal(&header[148..156]).ok_or("a tar header has a bad checksum")?;
        let sum: u64 =
            header.iter().enumerate().map(|(i, &c)| if (148..156).contains(&i) { 32 } else { c as u64 }).sum();
        if sum != want {
            return Err("a tar header has a bad checksum".into());
        }
        let name = cstr(&header[0..100]);
        let prefix = cstr(&header[345..500]);
        let mut full = Vec::new();
        if !prefix.is_empty() {
            full.extend_from_slice(prefix);
            full.push(b'/');
        }
        full.extend_from_slice(name);
        let rel = String::from_utf8(full).map_err(|_| "a name in the harness tar is not UTF-8".to_string())?;
        let rel = rel.trim_end_matches('/').to_string();
        if !plain_rel(&rel) {
            return Err(format!("{rel:?}: not a plain path inside the harness"));
        }
        let size = octal(&header[124..136]).ok_or("a tar header has a bad size")?;
        let mode = octal(&header[100..108]).ok_or("a tar header has a bad mode")?;
        files += 1;
        bytes += size;
        if files > MAX_FILES || bytes > MAX_BYTES {
            return Err("the harness tar holds more than the harness may".into());
        }
        let path = parent_dirs(top, &rel)?;
        match header[156] {
            b'0' | 0 => {
                let mut out = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&path)
                    .map_err(|_| format!("{rel}: twice in the harness tar, or not writable"))?;
                let mut left = size;
                let mut buf = vec![0u8; 1 << 16];
                while left > 0 {
                    let n = (left as usize).min(buf.len());
                    f.read_exact(&mut buf[..n]).map_err(|_| "the harness tar ends early".to_string())?;
                    out.write_all(&buf[..n]).map_err(|e| e.to_string())?;
                    left -= n as u64;
                }
                let pad = (512 - (size % 512)) % 512;
                if pad > 0 {
                    f.read_exact(&mut buf[..pad as usize]).map_err(|_| "the harness tar ends early".to_string())?;
                }
                drop(out);
                let m = if mode & 0o111 != 0 { 0o755 } else { 0o644 };
                fs::set_permissions(&path, fs::Permissions::from_mode(m)).map_err(|e| e.to_string())?;
            }
            b'5' => match fs::symlink_metadata(&path) {
                Ok(m) if m.file_type().is_dir() => {}
                Ok(_) => return Err(format!("{rel}: twice in the harness tar")),
                Err(_) => {
                    fs::create_dir(&path).map_err(|e| e.to_string())?;
                    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
                }
            },
            b'2' => {
                let target = String::from_utf8(cstr(&header[157..257]).to_vec())
                    .map_err(|_| format!("{rel}: a link target that is not UTF-8"))?;
                if size != 0 || !stays_inside(&rel, &target) {
                    return Err(format!("{rel}: a link that leaves the harness"));
                }
                std::os::unix::fs::symlink(&target, &path).map_err(|_| format!("{rel}: twice in the harness tar"))?;
            }
            _ => return Err(format!("{rel}: an entry of the harness tar that is no file, folder or link")),
        }
    }
}

/// Base64 (standard alphabet, with padding) of `data`.
pub fn b64(data: &[u8]) -> String {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        for i in 0..4 {
            out.push(if i <= chunk.len() { A[((n >> (18 - 6 * i)) & 63) as usize] as char } else { '=' });
        }
    }
    out
}

// --- the root copy of a harness component ----------------------------------------------------------------------------

/// Where llmcf-install keeps the harness of `comp`: /var/lib/llmcf/bundle/<bundle>/<component>/ with tree/,
/// harness_watch.sh, tree.sha256 and runs/.
pub fn place(env: &Env, bid: &str, comp: &str) -> PathBuf {
    env.path(sys::COPY_BASE).join(bid).join(comp)
}

fn watch_sha(comp: &Component) -> String {
    comp.files.iter().find(|f| f.name == manifest::HARNESS_WATCH).map(|f| f.sha256.clone()).unwrap_or_default()
}

/// Whether the root copy of the harness is there with the manifest's tree checksum and watch (its marker; the tree
/// itself is hashed again before every start).
pub fn is_current(env: &Env, bid: &str, comp: &Component) -> bool {
    let Some(part) = &comp.harness else { return false };
    let dir = place(env, bid, &comp.id);
    let marker = fs::read_to_string(dir.join("tree.sha256")).unwrap_or_default();
    dir.join("tree").is_dir()
        && marker.trim() == part.tree_sha256
        && file_sha256(&dir.join(manifest::HARNESS_WATCH)).map(|s| s == watch_sha(comp)).unwrap_or(false)
}

/// The new root copy of the harness in the folder `tmp`: the account's harness.tar copied and checked against its
/// sha256 in the manifest, unpacked, its tree checksum checked, the watch copied and checked; the tar removed again.
pub fn make_copy(tmp: &Path, source: &Path, comp: &Component, uid: u32) -> Result<(), String> {
    let part = comp.harness.as_ref().ok_or("no harness")?;
    let archive = comp.files.iter().find(|f| f.name == manifest::HARNESS_ARCHIVE).ok_or("no harness.tar")?;
    install::copy_checked(
        &source.join(manifest::HARNESS_ARCHIVE),
        &tmp.join(manifest::HARNESS_ARCHIVE),
        uid,
        &archive.sha256,
    )?;
    unpack(&tmp.join(manifest::HARNESS_ARCHIVE), &tmp.join("tree"))?;
    fs::remove_file(tmp.join(manifest::HARNESS_ARCHIVE)).map_err(|e| e.to_string())?;
    let got = tree_sha256(&listing(&tmp.join("tree"))?);
    if got != part.tree_sha256 {
        return Err(format!(
            "the harness tree of component {} does not match its tree checksum in the manifest; nothing of it runs",
            comp.id
        ));
    }
    install::copy_checked(
        &source.join(manifest::HARNESS_WATCH),
        &tmp.join(manifest::HARNESS_WATCH),
        uid,
        &watch_sha(comp),
    )?;
    fs::set_permissions(tmp.join(manifest::HARNESS_WATCH), fs::Permissions::from_mode(0o700))
        .map_err(|e| e.to_string())?;
    let mut marker = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(tmp.join("tree.sha256"))
        .map_err(|e| e.to_string())?;
    marker.write_all(format!("{}\n", part.tree_sha256).as_bytes()).map_err(|e| e.to_string())?;
    fs::set_permissions(tmp, fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
}

// --- the harness actions ---------------------------------------------------------------------------------------------

/// A run's name: the host's (`dry-YYYYmmddHHMMSS` and the like), checked like a component id.
pub fn is_run(s: &str) -> bool {
    manifest::is_id(s) && !s.contains('.')
}

fn sh(env: &Env) -> Option<PathBuf> {
    env.tool(&["/bin/sh", "/usr/bin/sh"])
}

/// A run that has not ended yet (its watch's process still there), other than `except`.
fn running(runs: &Path, except: &str) -> Option<String> {
    for e in fs::read_dir(runs).ok()?.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if name == except || e.path().join("ended").exists() {
            continue;
        }
        let pid = fs::read_to_string(e.path().join("pid")).unwrap_or_default();
        let pid = pid.trim();
        if pid.is_empty() || !pid.bytes().all(|c| c.is_ascii_digit()) || Path::new("/proc").join(pid).exists() {
            return Some(name);
        }
    }
    None
}

fn status_of(out: &Path) -> String {
    if out.join("ended").exists() {
        let code = fs::read_to_string(out.join("exit_code")).unwrap_or_default();
        let code = code.trim();
        let code =
            if !code.is_empty() && code.len() <= 3 && code.bytes().all(|c| c.is_ascii_digit()) { code } else { "90" };
        format!("status=ended code={code}")
    } else {
        "status=running".into()
    }
}

struct Run<'a> {
    env: &'a Env,
    comp: Component,
    part: HarnessPart,
    dir: PathBuf,
    out: PathBuf,
}

fn open_run<'a>(env: &'a Env, m: &Manifest, bid: &str, comp: &str, run: &str) -> Result<Run<'a>, String> {
    let c = m.component(comp).ok_or_else(|| format!("the bundle has no component {comp}"))?;
    let part = c.harness.clone().ok_or_else(|| format!("the component {comp} is no harness"))?;
    if m.mode != "full" || !m.root_actions.iter().any(|a| a.action == "run_harness" && a.component == comp) {
        return Err(format!("the signed manifest lets no harness of component {comp} run"));
    }
    if !is_current(env, bid, c) {
        return Err(format!(
            "the harness of component {comp} is not in place on the board (run the bundle's root actions first: \
             llmcf-install apply)"
        ));
    }
    let dir = place(env, bid, comp);
    sys::owned_safe(&dir, env.root_uid())?;
    let out = dir.join("runs").join(run);
    Ok(Run { env, comp: c.clone(), part, dir, out })
}

pub fn run(env: &Env, action: &str, bid: &str, account: &Account, args: &[String]) -> i32 {
    let done = match install::load_manifest(env, bid, account) {
        Err(e) => Done::refused(e),
        Ok((m, _, _)) => match open_run(env, &m, bid, &args[0], &args[1]) {
            Err(e) => Done::refused(e),
            Ok(r) => match action {
                "harness-start" => start(&r, &args[1], &args[2]),
                "harness-read" => read(&r, args[2].parse().unwrap_or(0)),
                "harness-answer" => answer(&r),
                "harness-stop" => stop(&r),
                _ => records(&r, args.get(2).map(String::as_str).unwrap_or("")),
            },
        },
    };
    let detail = if done.detail.is_empty() { String::new() } else { format!(" {}", done.detail) };
    println!("llmcf-bundle: result={} changed=0{}", done.result, detail);
    done.code
}

fn start(r: &Run, run: &str, mode: &str) -> Done {
    let runs = r.dir.join("runs");
    if !runs.is_dir() {
        if let Err(e) =
            fs::create_dir(&runs).and_then(|_| fs::set_permissions(&runs, fs::Permissions::from_mode(0o700)))
        {
            return Done::refused(format!("the harness's run folder cannot be made: {e}"));
        }
    }
    if fs::symlink_metadata(&r.out).is_ok() {
        return Done::refused(format!("a run {run} of this harness is there already"));
    }
    if let Some(other) = running(&runs, run) {
        return Done::refused(format!("the harness's run {other} is going on; attach to it or stop it first"));
    }
    let yes = match mode {
        "dry" if r.part.dry_run_args.is_empty() => {
            return Done::refused("the harness has no dry run of its own (its spec names no dry-run option)")
        }
        "run-yes" => match &r.part.assume_yes {
            Some(y) => Some(y.clone()),
            None => {
                return Done::refused("the bundle carries no assume-yes switch of this harness (the person's opt-in)")
            }
        },
        _ => None,
    };
    // the tree is hashed again now: what runs is exactly what the person took in
    let tree = r.dir.join("tree");
    match listing(&tree) {
        Ok(rows) if tree_sha256(&rows) == r.part.tree_sha256 => {}
        Ok(_) => {
            return Done::refused(
                "the harness tree on the board does not match its checksum in the signed manifest; nothing was started",
            )
        }
        Err(e) => {
            return Done::refused(format!("the harness tree on the board cannot be checked: {e}; nothing was started"))
        }
    }
    if file_sha256(&r.dir.join(manifest::HARNESS_WATCH)).ok() != Some(watch_sha(&r.comp)) {
        return Done::refused(
            "the harness watch on the board does not match its sha256 in the signed manifest; nothing was started",
        );
    }
    let Some(shell) = sh(r.env) else { return Done::refused("the board has no /bin/sh") };
    let watch = r.dir.join(manifest::HARNESS_WATCH);
    let out = r.out.to_string_lossy().into_owned();
    let mut argv: Vec<String> = vec![
        watch.to_string_lossy().into_owned(),
        "start".into(),
        if mode == "dry" { "dry".into() } else { "run".into() },
        tree.to_string_lossy().into_owned(),
        r.part.installer.clone(),
        out,
    ];
    if let Some(y) = yes {
        argv.push(y);
    }
    if mode == "dry" {
        argv.push("--".into());
        argv.extend(r.part.dry_run_args.iter().cloned());
    }
    let refs: Vec<&str> = argv.iter().map(String::as_str).collect();
    let (ok, text) = r.env.run(&shell, &refs);
    if !ok || text.trim() != "started" {
        return Done::failed(format!(
            "the harness watch did not start: {}",
            text.trim().chars().filter(|c| !c.is_control()).take(200).collect::<String>()
        ));
    }
    say("started", "harness", &format!("{} run={run} mode={mode}", r.comp.id));
    Done::ok_text("started", "")
}

fn read(r: &Run, offset: u64) -> Done {
    if !r.out.is_dir() {
        return Done::refused("no such run of the harness");
    }
    let log = r.out.join("installer.log");
    let started = Instant::now();
    loop {
        let size = fs::metadata(&log).map(|m| m.len()).unwrap_or(0);
        let ended = r.out.join("ended").exists();
        if size > offset || ended || started.elapsed() >= READ_WAIT {
            let from = offset.min(size);
            let take = (size - from).min(READ_CHUNK);
            let mut data = vec![0u8; take as usize];
            if take > 0 {
                let got = File::open(&log).and_then(|mut f| {
                    std::io::Seek::seek(&mut f, std::io::SeekFrom::Start(from))?;
                    f.read_exact(&mut data)
                });
                if got.is_err() {
                    return Done::failed("the installer's output could not be read");
                }
            }
            // "ended" only once everything was read
            let status = if ended && from + take >= size { status_of(&r.out) } else { "status=running".into() };
            println!("llmcf-harness: {status} offset={} size={take}", from + take);
            println!("llmcf-harness: data {}", b64(&data));
            return Done::ok_text("ok", "");
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

fn answer(r: &Run) -> Done {
    let mut line = Vec::new();
    let mut byte = [0u8; 1];
    let mut stdin = std::io::stdin().lock();
    while line.len() <= MAX_ANSWER {
        match stdin.read(&mut byte) {
            Ok(1) if byte[0] == b'\n' => break,
            Ok(1) => line.push(byte[0]),
            _ => break,
        }
    }
    if line.len() > MAX_ANSWER || line.iter().any(|&c| (c < 0x20 && c != b'\t') || c == 0x7f) {
        return Done::refused("an answer is one line of plain text");
    }
    let input = r.out.join("input");
    match fs::symlink_metadata(&input) {
        Ok(m) if m.file_type().is_fifo() => {}
        _ => return Done::refused("this run takes no answers (it runs with the assume-yes switch, or is a dry run)"),
    }
    if r.out.join("ended").exists() {
        return Done::refused("the installer has ended");
    }
    line.push(b'\n');
    let sent =
        OpenOptions::new().write(true).custom_flags(O_NONBLOCK).open(&input).and_then(|mut f| f.write_all(&line));
    if sent.is_err() {
        return Done::failed("the answer could not be passed to the installer");
    }
    say("answered", "harness", &r.comp.id);
    Done::ok_text("ok", "")
}

fn stop(r: &Run) -> Done {
    if r.out.join("ended").exists() {
        return Done::ok_text("ok", "the installer has ended already");
    }
    let Some(shell) = sh(r.env) else { return Done::refused("the board has no /bin/sh") };
    let watch = r.dir.join(manifest::HARNESS_WATCH).to_string_lossy().into_owned();
    let out = r.out.to_string_lossy().into_owned();
    let (ok, text) = r.env.run(&shell, &[&watch, "stop", &out]);
    if !ok {
        return Done::failed(format!(
            "the installer could not be stopped: {}",
            text.trim().chars().filter(|c| !c.is_control()).take(200).collect::<String>()
        ));
    }
    say("stopped", "harness", &r.comp.id);
    Done::ok_text("ok", "")
}

/// The lines of a record, each as text (a byte that is not UTF-8 replaced, control characters but the tab taken out:
/// nothing a record holds steers the host's terminal); never cut.
fn clean_lines(path: &Path) -> Vec<String> {
    match File::open(path) {
        Ok(f) => BufReader::new(f).split(b'\n').map_while(Result::ok).map(|l| clean(&l)).collect(),
        Err(_) => Vec::new(),
    }
}

fn clean(line: &[u8]) -> String {
    String::from_utf8_lossy(line).chars().filter(|c| !c.is_control() || *c == '\t').collect()
}

/// The trace lines the host's report reads: programs started, connections, files written, renamed or made setuid.
fn wanted_trace(line: &str) -> bool {
    if line.contains("execve(") {
        return line.trim_end().ends_with("= 0");
    }
    if line.contains("connect(") {
        return true;
    }
    if line.contains("= -1") {
        return false;
    }
    if line.contains("open(") || line.contains("openat(") || line.contains("creat(") {
        return ["O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"].iter().any(|f| line.contains(f));
    }
    line.contains("rename") || line.contains("chmod(") || line.contains("fchmodat(")
}

fn write_lines(path: &Path, lines: impl Iterator<Item = String>) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut out = BufWriter::new(OpenOptions::new().write(true).create_new(true).mode(0o600).open(path)?);
    for l in lines {
        out.write_all(l.as_bytes())?;
        out.write_all(b"\n")?;
    }
    out.flush()
}

/// The records as the host reads them, made once after the run ended in `<run>/export` (root-only like the run):
/// the small records whole, of each snapshot only the lines that differ between before and after (all the report
/// compares), of the trace only the lines the report reads. Nothing is cut; the folder never changes afterwards, so
/// a part read later continues the same bytes.
fn export(out: &Path) -> Result<PathBuf, String> {
    let dir = out.join("export");
    if dir.join(".done").is_file() {
        return Ok(dir);
    }
    let tmp = out.join("export.new");
    if fs::symlink_metadata(&tmp).is_ok() {
        fs::remove_dir_all(&tmp).map_err(|e| format!("an unfinished export cannot be cleared: {e}"))?;
    }
    fs::create_dir(&tmp).map_err(|e| e.to_string())?;
    for name in &RECORDS[..5] {
        write_lines(&tmp.join(name), clean_lines(&out.join(name)).into_iter()).map_err(|e| e.to_string())?;
    }
    for name in ["packages.txt", "accounts.txt", "ports.txt", "files.txt"] {
        let before = clean_lines(&out.join("before").join(name));
        let after = clean_lines(&out.join("after").join(name));
        let b: HashSet<&String> = before.iter().collect();
        let a: HashSet<&String> = after.iter().collect();
        write_lines(&tmp.join("before").join(name), before.iter().filter(|l| !a.contains(l)).cloned())
            .map_err(|e| e.to_string())?;
        write_lines(&tmp.join("after").join(name), after.iter().filter(|l| !b.contains(l)).cloned())
            .map_err(|e| e.to_string())?;
    }
    // the trace can be large: read line by line, never whole
    let trace: Box<dyn Iterator<Item = String>> = match File::open(out.join("trace.txt")) {
        Ok(f) => Box::new(
            BufReader::new(f).split(b'\n').map_while(Result::ok).map(|l| clean(&l)).filter(|l| wanted_trace(l)),
        ),
        Err(_) => Box::new(std::iter::empty()),
    };
    write_lines(&tmp.join("trace.txt"), trace).map_err(|e| e.to_string())?;
    fs::write(tmp.join(".done"), b"").map_err(|e| e.to_string())?;
    if fs::symlink_metadata(&dir).is_ok() {
        fs::remove_dir_all(&dir).map_err(|e| e.to_string())?;
    }
    fs::rename(&tmp, &dir).map_err(|e| e.to_string())?;
    Ok(dir)
}

/// A part call's value N.OFFSET.LEN: (record number, offset, length), each in range.
pub fn part_value(value: &str) -> Option<(usize, u64, u64)> {
    let mut it = value.split('.');
    let (n, off, len) = (it.next()?, it.next()?, it.next()?);
    if it.next().is_some() {
        return None;
    }
    let digits = |s: &str, max: usize| !s.is_empty() && s.len() <= max && s.bytes().all(|c| c.is_ascii_digit());
    if !digits(n, 2) || !digits(off, 15) || !digits(len, 8) {
        return None;
    }
    let (n, off, len): (usize, u64, u64) = (n.parse().ok()?, off.parse().ok()?, len.parse().ok()?);
    (n < RECORDS.len() && len >= 1).then_some((n, off, len))
}

/// Whether `value` is one harness-records takes: "index" or a part N.OFFSET.LEN.
pub fn is_records_value(value: &str) -> bool {
    value == "index" || part_value(value).is_some()
}

fn records(r: &Run, value: &str) -> Done {
    if !r.out.is_dir() {
        return Done::refused("no such run of the harness");
    }
    if !r.out.join("ended").exists() {
        return Done::refused("the installer is still running; its records are read once it ended");
    }
    let dir = match export(&r.out) {
        Ok(d) => d,
        Err(e) => return Done::failed(format!("the records could not be prepared: {e}")),
    };
    if value == "index" {
        println!("llmcf-harness: records part={PART_SIZE}");
        for (n, name) in RECORDS.iter().enumerate() {
            let path = dir.join(name);
            let size = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
            match file_sha256(&path) {
                Ok(sha) => println!("llmcf-harness: record {n} {name} {size} {sha}"),
                Err(_) => return Done::failed("a record could not be read"),
            }
        }
        return Done::ok_text("ok", "");
    }
    let Some((n, offset, len)) = part_value(value) else {
        return Done::refused("not a part of a record (N.OFFSET.LENGTH)");
    };
    let path = dir.join(RECORDS[n]);
    let size = fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
    if offset > size {
        return Done::refused("the offset lies beyond the record's end");
    }
    let take = len.min(PART_SIZE).min(size - offset);
    let mut data = vec![0u8; take as usize];
    let got = File::open(&path).and_then(|mut f| {
        f.seek(SeekFrom::Start(offset))?;
        f.read_exact(&mut data)
    });
    if got.is_err() {
        return Done::failed("the record could not be read");
    }
    println!("llmcf-harness: part {n} offset={offset} size={take} sha256={}", sig::hex(&Sha256::digest(&data)));
    println!("llmcf-harness: data {}", b64(&data));
    Done::ok_text("ok", "")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    const VECTORS: &str = include_str!("../../../tests/data/harness_tree_vectors.json");

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("llmcf-harness-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn unb64(text: &str) -> Vec<u8> {
        let mut out = Vec::new();
        let mut acc = 0u32;
        let mut bits = 0;
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

    fn build(root: &Path, entries: &Value) {
        for e in entries.as_array().unwrap() {
            let path = root.join(e["path"].as_str().unwrap());
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            match e["kind"].as_str().unwrap() {
                "l" => std::os::unix::fs::symlink(e["target"].as_str().unwrap(), &path).unwrap(),
                kind => {
                    fs::write(&path, e["data"].as_str().unwrap()).unwrap();
                    let mode = if kind == "x" { 0o755 } else { 0o644 };
                    fs::set_permissions(&path, fs::Permissions::from_mode(mode)).unwrap();
                }
            }
        }
    }

    #[test]
    fn the_shared_tree_vectors_hold() {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        for (i, case) in v["trees"].as_array().unwrap().iter().enumerate() {
            let root = temp(&format!("tree{i}"));
            build(&root, &case["entries"]);
            let rows = listing(&root).unwrap();
            let text: String = rows.iter().map(|(p, k, d)| format!("{k} {d} {p}\n")).collect();
            assert_eq!(text, case["listing"].as_str().unwrap());
            assert_eq!(tree_sha256(&rows), case["tree_sha256"].as_str().unwrap());
            let _ = fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn the_packed_tree_of_the_host_unpacks_to_its_checksum() {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        let case = &v["pack"];
        let dir = temp("pack");
        let tar = dir.join("harness.tar");
        fs::write(&tar, unb64(case["tar_base64"].as_str().unwrap())).unwrap();
        unpack(&tar, &dir.join("tree")).unwrap();
        assert_eq!(tree_sha256(&listing(&dir.join("tree")).unwrap()), case["tree_sha256"].as_str().unwrap());
        let _ = fs::remove_dir_all(&dir);
    }

    fn header(name: &str, kind: u8, size: usize, link: &str) -> Vec<u8> {
        let mut h = vec![0u8; 512];
        h[..name.len()].copy_from_slice(name.as_bytes());
        h[100..107].copy_from_slice(b"0000644");
        h[124..135].copy_from_slice(format!("{size:011o}").as_bytes());
        h[156] = kind;
        h[157..157 + link.len()].copy_from_slice(link.as_bytes());
        h[257..263].copy_from_slice(b"ustar\0");
        h[263..265].copy_from_slice(b"00");
        h[148..156].copy_from_slice(b"        ");
        let sum: u32 = h.iter().map(|&c| c as u32).sum();
        h[148..155].copy_from_slice(format!("{sum:06o}\0").as_bytes());
        h
    }

    fn tar_of(entries: &[(&str, u8, &[u8], &str)]) -> Vec<u8> {
        let mut out = Vec::new();
        for (name, kind, data, link) in entries {
            out.extend(header(name, *kind, data.len(), link));
            out.extend_from_slice(data);
            out.resize(out.len().div_ceil(512) * 512, 0);
        }
        out.extend(vec![0u8; 1024]);
        out
    }

    #[test]
    fn a_hostile_tar_is_refused() {
        let cases: Vec<Vec<u8>> = vec![
            tar_of(&[("../evil", b'0', b"x", "")]),
            tar_of(&[("/etc/passwd", b'0', b"x", "")]),
            tar_of(&[("a/../../x", b'0', b"x", "")]),
            tar_of(&[("out", b'2', b"", "../../etc")]),
            tar_of(&[("abs", b'2', b"", "/etc/passwd")]),
            tar_of(&[("l", b'2', b"", "."), ("l/x", b'0', b"x", "")]),
            tar_of(&[("dev", b'3', b"", "")]),
            tar_of(&[("twice", b'0', b"x", ""), ("twice", b'0', b"y", "")]),
        ];
        for (i, data) in cases.iter().enumerate() {
            let dir = temp(&format!("bad{i}"));
            fs::write(dir.join("t.tar"), data).unwrap();
            assert!(unpack(&dir.join("t.tar"), &dir.join("tree")).is_err(), "case {i}");
            let _ = fs::remove_dir_all(&dir);
        }
        let dir = temp("good");
        fs::write(dir.join("t.tar"), tar_of(&[("a/b.txt", b'0', b"hi", ""), ("a/l", b'2', b"", "b.txt")])).unwrap();
        unpack(&dir.join("t.tar"), &dir.join("tree")).unwrap();
        assert_eq!(fs::read(dir.join("tree/a/b.txt")).unwrap(), b"hi");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn base64_and_the_link_rule() {
        assert_eq!(b64(b""), "");
        assert_eq!(b64(b"f"), "Zg==");
        assert_eq!(b64(b"foobar"), "Zm9vYmFy");
        assert!(stays_inside("a/l", "b") && stays_inside("a/l", "../c") && !stays_inside("a/l", "../../c"));
        assert!(!stays_inside("l", "/etc") && !stays_inside("l", ""));
        assert!(wanted_trace("12 execve(\"/bin/sh\", [\"sh\"], 0x0) = 0"));
        assert!(!wanted_trace("12 openat(AT_FDCWD, \"/etc/x\", O_RDONLY) = 3"));
        assert!(wanted_trace("12 openat(AT_FDCWD, \"/etc/x\", O_WRONLY|O_CREAT, 0644) = 3"));
    }

    #[test]
    fn a_part_call_names_a_record_an_offset_and_a_length() {
        assert_eq!(part_value("0.0.4194304"), Some((0, 0, 4194304)));
        assert_eq!(part_value("13.123456789012345.1"), Some((13, 123456789012345, 1)));
        for bad in ["", "index", "14.0.1", "1.0.0", "1.0", "1.0.1.", "-1.0.1", "1.x.1", "1.0.123456789", "001.0.1"] {
            assert_eq!(part_value(bad), None, "{bad}");
        }
        assert!(is_records_value("index") && is_records_value("2.10.20") && !is_records_value("more"));
    }

    #[test]
    fn the_export_keeps_every_line_whole_and_never_changes_afterwards() {
        let out = temp("export");
        fs::create_dir_all(out.join("before")).unwrap();
        fs::create_dir_all(out.join("after")).unwrap();
        fs::write(out.join("exit_code"), "0\n").unwrap();
        let long = "x".repeat(5000);
        let many: String = (0..30000).map(|i| format!("/opt/agent/f{i}|1|2|644\n")).collect();
        fs::write(out.join("before/files.txt"), "/etc/same|1|1|644\n").unwrap();
        fs::write(out.join("after/files.txt"), format!("/etc/same|1|1|644\n{many}/opt/{long}|1|1|644\n")).unwrap();
        fs::write(out.join("trace.txt"), "1 openat(AT_FDCWD, \"/etc/x\", O_RDONLY) = 3\n1 connect(3, {}) = 0\n")
            .unwrap();
        let dir = export(&out).unwrap();
        let after = fs::read_to_string(dir.join("after/files.txt")).unwrap();
        assert_eq!(after.lines().count(), 30001);
        assert!(after.contains(&long) && !after.contains("/etc/same"));
        assert_eq!(fs::read_to_string(dir.join("before/files.txt")).unwrap(), "");
        assert_eq!(fs::read_to_string(dir.join("trace.txt")).unwrap(), "1 connect(3, {}) = 0\n");
        assert_eq!(fs::read_to_string(dir.join("exit_code")).unwrap(), "0\n");
        assert_eq!(fs::read_to_string(dir.join("watch")).unwrap(), "");
        fs::write(out.join("after/files.txt"), "changed\n").unwrap();
        assert_eq!(fs::read_to_string(export(&out).unwrap().join("after/files.txt")).unwrap(), after);
        let _ = fs::remove_dir_all(&out);
    }
}
