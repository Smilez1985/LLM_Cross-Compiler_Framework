//! System packages of the board's distribution (`sudo llmcf-install install_package plan|apply SCOPE NAME...`, and
//! the bundle's root action install_package within apply): only packages a signed list names, through apt and dpkg,
//! from the board's own package sources or from a board preload the framework took in for that scope.
//!
//! - SCOPE is a bundle id (the names must be ones an install_package action of its kept, signed manifest names) or
//!   `-` (only llmcf-install's own clock keeper, SELF_PACKAGES). NAME is `NAME` or `NAME=VERSION` by Debian's rules
//!   (manifest::is_package): never an option, a path or a shell word.
//! - The source: the board preload taken in for the scope (<home>/.local/share/llm-framework/packages/<bundle id or
//!   board>/, unpacked there by the framework as the account) when it is there, else the board's own sources. A
//!   preload is first copied into a root-owned place (COPY_BASE/<scope>) and checked there: each suite's Release.gpg
//!   with the board's gpgv (or sqv) against the board's own root-owned archive keyrings, that the Release is of the
//!   board's release and architecture, each Packages list against its Release by size and SHA-256, and every .deb
//!   against its list by size and SHA-256; one file that is not listed, does not match or is a link refuses the whole
//!   preload. apt then installs exactly those files with no package source at all (an empty source list).
//! - plan changes nothing on the system (it may refresh the root copy of a preload); apply installs non-interactively
//!   without the recommended packages and never removes one; both say per package what is there and what would come.
//! - No network of its own: apt reaches the board's own sources where it has them.
//!
//! Report lines start with `llmcf-bundle: `; the last one is `result=planned|ok|refused|failed changed=N source=...`.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use sha2::{Digest, Sha256};

use crate::install::{self, EXIT_FAILED, EXIT_OK, EXIT_REFUSED};
use crate::manifest::{is_id, is_package, package_name, MAX_PACKAGES};
use crate::sig;
use crate::sys::{self, Account, Env};

pub const ACTION: &str = "install_package";
/// What llmcf-install may install without a bundle: its own clock keeper (clock.rs `keep`).
pub const SELF_PACKAGES: [&str; 1] = ["fake-hwclock"];
/// The board account's folder of taken-in preloads, below its home (board_packages.PACKAGES_REL).
pub const PACKAGES_REL: &str = ".local/share/llm-framework/packages";
/// The folder name of the preload for scope `-` (packages outside a bundle).
pub const BOARD_SCOPE: &str = "board";
/// The root-owned place of the packages: preload/<scope> (a preload's root copy, checked there), gnupg (gpgv's empty
/// home), no-sources (an empty source folder for apt) and journal (llmcf-install's log of package runs).
pub const COPY_BASE: &str = "/var/lib/llmcf/packages";
const JOURNAL: &str = "journal";
const APT_GET: [&str; 2] = ["/usr/bin/apt-get", "/bin/apt-get"];
const APT_MARK: [&str; 2] = ["/usr/bin/apt-mark", "/bin/apt-mark"];
const DPKG: [&str; 2] = ["/usr/bin/dpkg", "/bin/dpkg"];
const DPKG_QUERY: [&str; 2] = ["/usr/bin/dpkg-query", "/bin/dpkg-query"];
const GPGV: [&str; 2] = ["/usr/bin/gpgv", "/bin/gpgv"];
const SQV: [&str; 2] = ["/usr/bin/sqv", "/bin/sqv"];
/// The package managers named when a board has no apt: llmcf-install does not install with them.
const OTHER_MANAGERS: [(&str, &str); 6] = [
    ("dnf", "/usr/bin/dnf"),
    ("yum", "/usr/bin/yum"),
    ("zypper", "/usr/bin/zypper"),
    ("pacman", "/usr/bin/pacman"),
    ("apk", "/sbin/apk"),
    ("opkg", "/bin/opkg"),
];
/// The distributions' own archive keyrings on the board (only these names, only root-owned): what a preload's
/// Release.gpg is checked against.
pub const KEYRINGS: [&str; 8] = [
    "/usr/share/keyrings/debian-archive-keyring.gpg",
    "/usr/share/keyrings/debian-archive-keyring.pgp",
    "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
    "/usr/share/keyrings/raspbian-archive-keyring.gpg",
    "/usr/share/keyrings/raspberrypi-archive-keyring.gpg",
    "/usr/share/keyrings/raspberrypi-archive-keyring.pgp",
    "/usr/share/keyrings/armbian.gpg",
    "/usr/share/keyrings/armbian-archive-keyring.gpg",
];
const OS_RELEASE: [&str; 2] = ["/etc/os-release", "/usr/lib/os-release"];
const MAX_PRELOAD_FILES: usize = 8192;
const MAX_PRELOAD_BYTES: u64 = 8 << 30;
const MAX_RELEASE_BYTES: u64 = 8 << 20;
const MAX_LIST_BYTES: u64 = 512 << 20;
const MAX_DEPTH: usize = 12;

fn line(text: &str) {
    println!("llmcf-bundle: {text}");
}

/// Whether `s` is a scope: a bundle id or `-` (a bundle id `board` is never taken: it is the folder of `-`).
pub fn is_scope(s: &str) -> bool {
    s == "-" || (is_id(s) && s != BOARD_SCOPE)
}

/// Whether the words after `install_package` are a call it takes: plan|apply, a scope, 1 to MAX_PACKAGES packages
/// (each name once).
pub fn is_call(args: &[String]) -> bool {
    if args.len() < 3 || args.len() > 2 + MAX_PACKAGES {
        return false;
    }
    if !["plan", "apply"].contains(&args[0].as_str()) || !is_scope(&args[1]) {
        return false;
    }
    let mut names = BTreeSet::new();
    args[2..].iter().all(|p| is_package(p) && names.insert(package_name(p).to_string()))
}

/// The board's package manager: Ok(()) for apt and dpkg, else the refusal that names what the board has.
pub fn manager(env: &Env) -> Result<(), String> {
    if env.tool(&APT_GET).is_some() && env.tool(&DPKG).is_some() && env.tool(&DPKG_QUERY).is_some() {
        return Ok(());
    }
    let other = OTHER_MANAGERS.iter().find(|(_, p)| env.path(p).is_file()).map(|(n, _)| *n);
    Err(match other {
        Some(name) => format!(
            "this board's package manager is {name}; llmcf-install installs packages only with apt and dpkg (Debian, \
             Ubuntu, Raspberry Pi OS, Armbian, DietPi); nothing was changed"
        ),
        None => {
            "this board has no apt and dpkg; llmcf-install installs packages only with them; nothing was changed".into()
        }
    })
}

/// The installed version of package `name` (None: not installed), from dpkg's own status.
pub fn installed(env: &Env, name: &str) -> Option<String> {
    let q = env.tool(&DPKG_QUERY)?;
    let (ok, out) = env.run(&q, &["-W", "-f=${Status}\t${Version}\n", name]);
    if !ok {
        return None;
    }
    out.lines().find_map(|l| {
        let (status, version) = l.split_once('\t')?;
        (status.trim() == "install ok installed" && !version.trim().is_empty()).then(|| version.trim().to_string())
    })
}

/// Whether the word `NAME[=VERSION]` is in place: installed, in that version where it names one.
pub fn in_place(env: &Env, word: &str) -> bool {
    match (installed(env, package_name(word)), word.split_once('=')) {
        (Some(have), Some((_, want))) => have == want,
        (Some(_), None) => true,
        (None, _) => false,
    }
}

/// Debian's version order through the board's own dpkg: whether `a` is lower than `b`.
fn lower(env: &Env, a: &str, b: &str) -> bool {
    match env.tool(&DPKG) {
        Some(dpkg) => env.run(&dpkg, &["--compare-versions", a, "lt", b]).0,
        None => false,
    }
}

/// The board's own release (VERSION_CODENAME of os-release) and architecture (dpkg --print-architecture).
fn board_release(env: &Env) -> Result<(String, String), String> {
    let text = OS_RELEASE.iter().find_map(|p| fs::read_to_string(env.path(p)).ok()).unwrap_or_default();
    let codename = text
        .lines()
        .find_map(|l| l.strip_prefix("VERSION_CODENAME="))
        .map(|v| v.trim().trim_matches('"').to_string())
        .unwrap_or_default();
    let word = |s: &str| {
        !s.is_empty() && s.len() <= 32 && s.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
    };
    if !word(&codename) {
        return Err("the board's os-release names no release codename, so no preload can be checked against it".into());
    }
    let dpkg = env.tool(&DPKG).ok_or("the board has no dpkg")?;
    let (ok, out) = env.run(&dpkg, &["--print-architecture"]);
    let arch = out.trim().to_string();
    if !ok || !word(&arch) {
        return Err("dpkg did not name the board's architecture".into());
    }
    Ok((codename, arch))
}

// --- the preload: copied into a root-owned place and checked there ----------------------------------------------------

/// One .deb of a checked preload: its package, version, path in the root copy.
#[derive(Debug, Clone, PartialEq)]
pub struct Deb {
    pub package: String,
    pub version: String,
    pub path: PathBuf,
}

fn name_ok(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 255
        && !name.starts_with('.')
        && name.bytes().all(|c| c.is_ascii_alphanumeric() || b"._+~%-".contains(&c))
}

/// Every plain file below `top` (no link, no special file, every entry of owner `uid`), as (relative path, size).
fn files_below(top: &Path, uid: u32) -> Result<Vec<(String, u64)>, String> {
    fn go(
        dir: &Path,
        rel: &str,
        uid: u32,
        depth: usize,
        out: &mut Vec<(String, u64)>,
        bytes: &mut u64,
    ) -> Result<(), String> {
        if depth > MAX_DEPTH {
            return Err("the preload is nested too deep".into());
        }
        let mut names: Vec<String> = Vec::new();
        for e in fs::read_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))? {
            let name = e.map_err(|e| e.to_string())?.file_name().to_string_lossy().into_owned();
            if !name_ok(&name) {
                return Err(format!("the preload holds a name it never has: {name:?}"));
            }
            names.push(name);
        }
        names.sort();
        for name in names {
            let path = dir.join(&name);
            let child = if rel.is_empty() { name.clone() } else { format!("{rel}/{name}") };
            let meta = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
            if meta.uid() != uid {
                return Err(format!("{child} in the preload has another owner"));
            }
            if meta.file_type().is_dir() {
                go(&path, &child, uid, depth + 1, out, bytes)?;
            } else if meta.file_type().is_file() {
                *bytes += meta.len();
                out.push((child, meta.len()));
            } else {
                return Err(format!("{child} in the preload is a link or a special file"));
            }
            if out.len() > MAX_PRELOAD_FILES || *bytes > MAX_PRELOAD_BYTES {
                return Err("the preload holds more files or bytes than a board preload may".into());
            }
        }
        Ok(())
    }
    let mut out = Vec::new();
    let mut bytes = 0;
    go(top, "", uid, 0, &mut out, &mut bytes)?;
    Ok(out)
}

/// The account's taken-in preload folder of `scope` (None: there is none).
fn preload_folder(env: &Env, account: &Account, scope: &str) -> Result<Option<PathBuf>, String> {
    let name = if scope == "-" { BOARD_SCOPE } else { scope };
    let base = env.path(&account.home).join(PACKAGES_REL).join(name);
    if fs::symlink_metadata(&base).is_err() {
        return Ok(None);
    }
    sys::account_folder(env, &account.home, &[PACKAGES_REL, name, "packages"], account.uid)
        .map(Some)
        .map_err(|e| format!("the board preload taken in for {name} is not a plain folder of the account: {e}"))
}

/// Copies the preload's `packages` folder into the root-owned COPY_BASE/<scope> (made anew), each file checked on
/// the way (copy_plain: the file that was looked at is the one copied).
fn root_copy(env: &Env, account: &Account, from: &Path, scope: &str) -> Result<PathBuf, String> {
    let files = files_below(from, account.uid)?;
    let base = env.path(COPY_BASE);
    install::mkdirs(env, &base, 0o755)?;
    sys::owned_safe(&base, env.root_uid())?;
    let name = if scope == "-" { BOARD_SCOPE } else { scope };
    let copies = base.join("preload");
    install::mkdirs(env, &copies, 0o755)?;
    let dest = copies.join(name);
    if fs::symlink_metadata(&dest).is_ok() {
        fs::remove_dir_all(&dest).map_err(|e| format!("{}: {e}", dest.display()))?;
    }
    fs::create_dir(&dest).map_err(|e| format!("{}: {e}", dest.display()))?;
    fs::set_permissions(&dest, fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    for (rel, _) in &files {
        let to = dest.join(rel);
        if let Some(dir) = to.parent() {
            install::mkdirs(env, dir, 0o755)?;
        }
        install::copy_plain(&from.join(rel), &to, account.uid)?;
        fs::set_permissions(&to, fs::Permissions::from_mode(0o644)).map_err(|e| e.to_string())?;
    }
    Ok(dest)
}

/// The SHA-256 and size of a file, read in blocks.
fn sha_size(path: &Path) -> Result<(String, u64), String> {
    let mut f = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buf = vec![0u8; 1 << 20];
    let mut size = 0u64;
    loop {
        let n = f.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        size += n as u64;
        hash.update(&buf[..n]);
    }
    Ok((sig::hex(&hash.finalize()), size))
}

/// What a Release file says: its codename and suite, architectures, and the SHA-256 and size of each list.
#[derive(Debug, Default)]
pub struct Release {
    pub codename: String,
    pub suite: String,
    pub architectures: Vec<String>,
    pub sha256: BTreeMap<String, (String, u64)>,
}

/// A Release file (RFC 822 style, the SHA256 field's lines ` HASH SIZE PATH`).
pub fn parse_release(text: &str) -> Release {
    let mut r = Release::default();
    let mut in_sha = false;
    for l in text.lines() {
        if l.starts_with(' ') || l.starts_with('\t') {
            if in_sha {
                let w: Vec<&str> = l.split_whitespace().collect();
                if let [hash, size, path] = w.as_slice() {
                    if let Ok(size) = size.parse::<u64>() {
                        if hash.len() == 64 && hash.bytes().all(|c| c.is_ascii_hexdigit()) {
                            r.sha256.insert(path.to_string(), (hash.to_ascii_lowercase(), size));
                        }
                    }
                }
            }
            continue;
        }
        in_sha = false;
        let Some((key, value)) = l.split_once(':') else { continue };
        let value = value.trim();
        match key {
            "Codename" => r.codename = value.to_string(),
            "Suite" => r.suite = value.to_string(),
            "Architectures" => r.architectures = value.split_whitespace().map(String::from).collect(),
            "SHA256" => in_sha = true,
            _ => {}
        }
    }
    r
}

/// Whether a Release belongs to the board's release `codename` (the release itself, or one of its suites such as
/// `bookworm-updates` and `bookworm-security`).
pub fn of_release(r: &Release, codename: &str) -> bool {
    let fits = |s: &str| s == codename || s.strip_prefix(codename).is_some_and(|rest| rest.starts_with('-'));
    fits(&r.codename) || fits(&r.suite)
}

/// The stanzas of a Packages list whose Filename is in `wanted` (Filename -> (package, version, SHA-256, size)),
/// read line by line while its own SHA-256 and size are computed.
#[allow(clippy::type_complexity)]
fn read_list(
    path: &Path,
    wanted: &BTreeSet<String>,
) -> Result<(String, u64, BTreeMap<String, (String, String, String, u64)>), String> {
    let f = File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut reader = BufReader::new(f);
    let mut hash = Sha256::new();
    let mut size = 0u64;
    let mut found = BTreeMap::new();
    let mut fields: BTreeMap<String, String> = BTreeMap::new();
    let mut buf = Vec::new();
    let take = |fields: &mut BTreeMap<String, String>, found: &mut BTreeMap<String, (String, String, String, u64)>| {
        if let Some(file) = fields.get("Filename") {
            if wanted.contains(file) {
                let size = fields.get("Size").and_then(|s| s.parse::<u64>().ok()).unwrap_or(u64::MAX);
                found.insert(
                    file.clone(),
                    (
                        fields.get("Package").cloned().unwrap_or_default(),
                        fields.get("Version").cloned().unwrap_or_default(),
                        fields.get("SHA256").cloned().unwrap_or_default().to_ascii_lowercase(),
                        size,
                    ),
                );
            }
        }
        fields.clear();
    };
    loop {
        buf.clear();
        let n = reader.read_until(b'\n', &mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        size += n as u64;
        if size > MAX_LIST_BYTES {
            return Err(format!("{} is larger than a package list may be", path.display()));
        }
        hash.update(&buf);
        let text = String::from_utf8_lossy(&buf);
        let l = text.trim_end_matches(['\n', '\r']);
        if l.is_empty() {
            take(&mut fields, &mut found);
            continue;
        }
        if l.starts_with(' ') || l.starts_with('\t') {
            continue;
        }
        if let Some((k, v)) = l.split_once(':') {
            if ["Package", "Version", "Filename", "Size", "SHA256"].contains(&k) {
                fields.insert(k.to_string(), v.trim().to_string());
            }
        }
    }
    take(&mut fields, &mut found);
    Ok((sig::hex(&hash.finalize()), size, found))
}

/// The board's archive keyrings llmcf-install trusts: the distributions' own files, root-owned and not writable by
/// others, in a root-owned folder.
fn keyrings(env: &Env) -> Vec<PathBuf> {
    let uid = env.root_uid();
    KEYRINGS
        .iter()
        .map(|k| env.path(k))
        .filter(|p| {
            fs::symlink_metadata(p).map(|m| m.file_type().is_file()).unwrap_or(false)
                && sys::owned_safe(p, uid).is_ok()
                && p.parent().map(|d| sys::owned_safe(d, uid).is_ok()).unwrap_or(false)
        })
        .collect()
}

/// Release.gpg over Release, checked with the board's gpgv (else sqv) against `rings`.
fn signature_ok(env: &Env, home: &Path, rings: &[PathBuf], release: &Path, signature: &Path) -> Result<(), String> {
    let sig_s = signature.to_string_lossy().into_owned();
    let rel_s = release.to_string_lossy().into_owned();
    let ring_s: Vec<String> = rings.iter().map(|r| r.to_string_lossy().into_owned()).collect();
    if let Some(gpgv) = env.tool(&GPGV) {
        install::mkdirs(env, home, 0o700)?;
        let home_s = home.to_string_lossy().into_owned();
        let mut args: Vec<&str> = vec!["--homedir", &home_s, "--ignore-time-conflict", "--status-fd", "1"];
        for r in &ring_s {
            args.push("--keyring");
            args.push(r);
        }
        args.push(&sig_s);
        args.push(&rel_s);
        let (ok, out) = env.run(&gpgv, &args);
        if ok && out.lines().any(|l| l.starts_with("[GNUPG:] VALIDSIG ")) {
            return Ok(());
        }
        return Err("its signature does not verify with the board's archive keys (gpgv)".into());
    }
    if let Some(sqv) = env.tool(&SQV) {
        let mut args: Vec<&str> = Vec::new();
        for r in &ring_s {
            args.push("--keyring");
            args.push(r);
        }
        args.push(&sig_s);
        args.push(&rel_s);
        let (ok, _) = env.run(&sqv, &args);
        if ok {
            return Ok(());
        }
        return Err("its signature does not verify with the board's archive keys (sqv)".into());
    }
    Err("the board has neither gpgv nor sqv to check the distribution's signature".into())
}

/// Checks the root copy `top` of a preload (see the module docstring) and returns its .deb files.
pub fn check_preload(env: &Env, top: &Path) -> Result<Vec<Deb>, String> {
    let (codename, arch) = board_release(env)?;
    let uid = env.root_uid();
    let files = files_below(top, uid)?;
    let rings = keyrings(env);
    if rings.is_empty() {
        return Err("the board has none of its distribution's archive keyrings in /usr/share/keyrings".into());
    }
    // group the files by suite folder (a number); the rest: sources.txt and the archive keys the PC checked
    let mut suites: BTreeMap<String, Vec<(String, u64)>> = BTreeMap::new();
    for (rel, size) in &files {
        let (head, rest) = rel.split_once('/').unwrap_or((rel.as_str(), ""));
        let numbered = !head.is_empty() && head.len() <= 3 && head.bytes().all(|c| c.is_ascii_digit());
        if numbered && !rest.is_empty() {
            suites.entry(head.to_string()).or_default().push((rest.to_string(), *size));
        } else if rel == "sources.txt"
            || (head == "keys" && !rest.contains('/') && (rest.ends_with(".asc") || rest.ends_with(".gpg")))
        {
            continue;
        } else {
            return Err(format!("the preload holds a file a board preload never has: {rel}"));
        }
    }
    if suites.is_empty() {
        return Err("the preload holds no packages".into());
    }
    let home = env.path(COPY_BASE).join("gnupg");
    let mut debs = Vec::new();
    for (n, entries) in &suites {
        let dir = top.join(n);
        let release_path = dir.join("Release");
        let what = |e: String| format!("suite {n} of the preload: {e}");
        if !entries.iter().any(|(r, _)| r == "Release") || !entries.iter().any(|(r, _)| r == "Release.gpg") {
            return Err(what("Release and Release.gpg belong in it".into()));
        }
        if fs::metadata(&release_path).map(|m| m.len()).unwrap_or(u64::MAX) > MAX_RELEASE_BYTES {
            return Err(what("its Release is larger than one may be".into()));
        }
        signature_ok(env, &home, &rings, &release_path, &dir.join("Release.gpg")).map_err(what)?;
        let text = fs::read_to_string(&release_path).map_err(|e| what(e.to_string()))?;
        let release = parse_release(&text);
        if !of_release(&release, &codename) {
            return Err(what(format!(
                "its Release is of {} {}, not of the board's release {codename}",
                release.codename, release.suite
            )));
        }
        if !release.architectures.contains(&arch) {
            return Err(what(format!("its Release has no packages for the board's architecture {arch}")));
        }
        let mut lists = Vec::new();
        let mut wanted = BTreeSet::new();
        for (rel, _) in entries {
            if rel == "Release" || rel == "Release.gpg" {
                continue;
            }
            let parts: Vec<&str> = rel.split('/').collect();
            if parts.len() == 3 && parts[1] == format!("binary-{arch}") && parts[2] == "Packages" {
                lists.push(rel.clone());
            } else if parts.first() == Some(&"pool") && rel.ends_with(".deb") && parts.len() >= 3 {
                wanted.insert(rel.clone());
            } else {
                return Err(what(format!("it holds a file a board preload never has: {rel}")));
            }
        }
        if lists.is_empty() {
            return Err(what("it holds no package list for the board's architecture".into()));
        }
        let mut listed: BTreeMap<String, (String, String, String, u64)> = BTreeMap::new();
        for rel in &lists {
            let (sha, size, found) = read_list(&dir.join(rel), &wanted).map_err(what)?;
            match release.sha256.get(rel) {
                Some((want, want_size)) if *want == sha && *want_size == size => {}
                Some(_) => return Err(what(format!("{rel} does not match the SHA-256 its signed Release names"))),
                None => return Err(what(format!("{rel} is not named by its signed Release"))),
            }
            for (k, v) in found {
                listed.entry(k).or_insert(v);
            }
        }
        for rel in &wanted {
            let Some((package, version, sha, size)) = listed.get(rel) else {
                return Err(what(format!("{rel} is not in its signed package lists")));
            };
            let (got, got_size) = sha_size(&dir.join(rel)).map_err(what)?;
            if got != *sha || got_size != *size {
                return Err(what(format!("{rel} does not match the size and SHA-256 its package list names")));
            }
            if !is_package(package) || !is_package(&format!("{package}={version}")) {
                return Err(what(format!("{rel} names a package or version llmcf-install does not take")));
            }
            debs.push(Deb { package: package.clone(), version: version.clone(), path: dir.join(rel) });
        }
    }
    Ok(debs)
}

// --- planning and installing --------------------------------------------------------------------------------------

/// What one plan or install found and did.
#[derive(Debug, Default)]
pub struct Outcome {
    /// "online" or "preload"
    pub source: String,
    /// NAME=VERSION of the packages already in place
    pub already: Vec<String>,
    /// NAME=VERSION of what would come (the packages and their dependencies)
    pub would: Vec<String>,
    /// NAME=VERSION of what was installed
    pub installed: Vec<String>,
}

/// The `Inst NAME [OLD] (VERSION ...)` lines of apt-get -s as NAME=VERSION.
pub fn simulated(text: &str) -> Vec<String> {
    text.lines()
        .filter_map(|l| {
            let rest = l.strip_prefix("Inst ")?;
            let name = rest.split_whitespace().next()?;
            let version = rest.split_once('(')?.1.split_whitespace().next()?;
            Some(format!("{name}={version}"))
        })
        .collect()
}

fn apt_env() -> [(&'static str, &'static str); 2] {
    [("DEBIAN_FRONTEND", "noninteractive"), ("APT_LISTCHANGES_FRONTEND", "none")]
}

const KEEP_CONF: [&str; 4] = ["-o", "Dpkg::Options::=--force-confdef", "-o", "Dpkg::Options::=--force-confold"];

fn tail(out: &str) -> String {
    let lines: Vec<&str> = out.lines().filter(|l| !l.trim().is_empty()).collect();
    let last: Vec<&str> = lines.iter().rev().take(2).rev().copied().collect();
    last.join(" / ").chars().take(240).collect()
}

/// Plans or installs `words` (already checked against the signed list) for `scope`: from the scope's taken-in
/// preload when there is one, else from the board's own sources.
pub fn install(env: &Env, account: &Account, scope: &str, words: &[String], apply: bool) -> Result<Outcome, String> {
    manager(env)?;
    let mut out = Outcome::default();
    let mut pending: Vec<String> = Vec::new();
    for w in words {
        let name = package_name(w);
        match installed(env, name) {
            Some(v) if in_place(env, w) => {
                line(&format!("package {name} installed={v}"));
                out.already.push(format!("{name}={v}"));
            }
            Some(v) => {
                line(&format!("package {name} installed={v} wanted={}", w.split_once('=').map(|x| x.1).unwrap_or("")));
                pending.push(w.clone());
            }
            None => {
                line(&format!("package {name} installed=no"));
                pending.push(w.clone());
            }
        }
    }
    let apt = env.tool(&APT_GET).ok_or("the board has no apt-get")?;
    let refused = |e: String| format!("{e}; nothing was changed");
    let preload = preload_folder(env, account, scope).map_err(refused)?;
    out.source = if preload.is_some() { "preload".into() } else { "online".into() };
    if pending.is_empty() {
        return Ok(out);
    }
    match preload {
        Some(folder) => {
            let top = root_copy(env, account, &folder, scope).map_err(refused)?;
            let debs = check_preload(env, &top).map_err(refused)?;
            line(&format!("preload checked debs={} source=preload", debs.len()));
            // the files to give apt: every package of the preload that is not installed in that version or newer
            let mut files: Vec<&Deb> = Vec::new();
            for d in &debs {
                match installed(env, &d.package) {
                    Some(v) if v == d.version || !lower(env, &v, &d.version) => {}
                    _ => files.push(d),
                }
            }
            for w in &pending {
                let name = package_name(w);
                let want = w.split_once('=').map(|x| x.1);
                if !debs.iter().any(|d| d.package == name && want.is_none_or(|v| v == d.version)) {
                    return Err(format!("the board preload does not hold {w}; nothing was changed"));
                }
            }
            for d in &files {
                out.would.push(format!("{}={}", d.package, d.version));
                line(&format!("would-install {} {}", d.package, d.version));
            }
            if !apply || files.is_empty() {
                return Ok(out);
            }
            let empty = env.path(COPY_BASE).join("no-sources");
            install::mkdirs(env, &empty, 0o755)?;
            let parts = format!("Dir::Etc::SourceParts={}", empty.display());
            let paths: Vec<String> = files.iter().map(|d| d.path.to_string_lossy().into_owned()).collect();
            let mut args: Vec<&str> = vec![
                "-o",
                "Dir::Etc::SourceList=/dev/null",
                "-o",
                &parts,
                "-o",
                "Dir::Cache::pkgcache=",
                "-o",
                "Dir::Cache::srcpkgcache=",
                "-o",
                "APT::Sandbox::User=root",
            ];
            args.extend(KEEP_CONF);
            // no --no-download: apt then refuses local files ("Pathname to install is not absolute"); with no source
            // at all there is nothing it could download
            args.extend(["install", "-y", "--no-install-recommends"]);
            args.extend(paths.iter().map(String::as_str));
            let (ok, text) = env.run_env(&apt, &args, &apt_env());
            if !ok {
                return Err(format!("apt did not install from the board preload: {}", tail(&text)));
            }
            let asked: BTreeSet<&str> = pending.iter().map(|w| package_name(w)).collect();
            let deps: Vec<&str> = files.iter().map(|d| d.package.as_str()).filter(|p| !asked.contains(p)).collect();
            if !deps.is_empty() {
                if let Some(mark) = env.tool(&APT_MARK) {
                    let mut margs = vec!["auto"];
                    margs.extend(deps.iter().copied());
                    let _ = env.run_env(&mark, &margs, &apt_env());
                }
            }
            for d in &files {
                if installed(env, &d.package).as_deref() == Some(d.version.as_str()) {
                    out.installed.push(format!("{}={}", d.package, d.version));
                    line(&format!("installed {} {}", d.package, d.version));
                }
            }
        }
        None => {
            let mut sim: Vec<&str> = vec!["-s", "install", "--no-install-recommends"];
            sim.extend(pending.iter().map(String::as_str));
            let (ok, text) = env.run_env(&apt, &sim, &apt_env());
            if ok {
                for w in simulated(&text) {
                    line(&format!("would-install {}", w.replacen('=', " ", 1)));
                    out.would.push(w);
                }
            } else if !apply {
                line(&format!(
                    "would-install {} (the board's package lists do not know it yet; the install reads them anew \
                     first: {})",
                    pending.join(","),
                    tail(&text)
                ));
                out.would.extend(pending.iter().cloned());
            }
            if !apply {
                return Ok(out);
            }
            let (ok, text) = env.run_env(&apt, &["update"], &apt_env());
            if !ok {
                return Err(format!(
                    "the board's package sources did not answer (no network? a board preload brings the packages): {}",
                    tail(&text)
                ));
            }
            let mut args: Vec<&str> = KEEP_CONF.to_vec();
            args.extend(["install", "-y", "--no-install-recommends"]);
            args.extend(pending.iter().map(String::as_str));
            let before: BTreeMap<String, Option<String>> =
                out.would.iter().map(|w| (package_name(w).to_string(), installed(env, package_name(w)))).collect();
            let (ok, text) = env.run_env(&apt, &args, &apt_env());
            if !ok {
                return Err(format!("apt did not install the packages: {}", tail(&text)));
            }
            let mut names: BTreeSet<String> = before.keys().cloned().collect();
            names.extend(pending.iter().map(|w| package_name(w).to_string()));
            for name in names {
                let now = installed(env, &name);
                if now.is_some() && before.get(&name).cloned().flatten() != now {
                    let v = now.unwrap_or_default();
                    line(&format!("installed {name} {v}"));
                    out.installed.push(format!("{name}={v}"));
                }
            }
        }
    }
    for w in &pending {
        if !in_place(env, w) {
            return Err(format!("{w} is not installed after the run"));
        }
    }
    Ok(out)
}

/// Appends one line to llmcf-install's own log of package runs (COPY_BASE/journal, root-owned 0600).
pub fn log(env: &Env, scope: &str, source: &str, result: &str, words: &[String]) {
    let base = env.path(COPY_BASE);
    if install::mkdirs(env, &base, 0o755).is_err() || sys::owned_safe(&base, env.root_uid()).is_err() {
        return;
    }
    let path = base.join(JOURNAL);
    if fs::symlink_metadata(&path).map(|m| !m.file_type().is_file()).unwrap_or(false) {
        return;
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let text = format!("{now}\t{scope}\t{source}\t{result}\t{}\n", words.join(","));
    if let Ok(mut f) = OpenOptions::new().append(true).create(true).mode(0o600).open(&path) {
        let _ = f.write_all(text.as_bytes());
    }
}

/// The words `scope` may install: the packages of its bundle's signed install_package actions, or SELF_PACKAGES.
fn allowed(env: &Env, account: &Account, scope: &str) -> Result<Vec<String>, String> {
    if scope == "-" {
        return Ok(SELF_PACKAGES.iter().map(|s| s.to_string()).collect());
    }
    let (m, _, _) = install::load_manifest(env, scope, account)?;
    Ok(m.root_actions.iter().filter(|a| a.action == ACTION).flat_map(|a| a.packages.iter().cloned()).collect())
}

fn done(result: &str, changed: usize, detail: &str, code: i32) -> i32 {
    println!("llmcf-bundle: result={result} changed={changed} {detail}");
    code
}

/// `install_package plan|apply SCOPE WORD...` as root for the board account (main.rs checked the call and the caller).
pub fn run(env: &Env, account: &Account, args: &[String]) -> i32 {
    let (action, scope, words) = (args[0].as_str(), args[1].as_str(), &args[2..]);
    if let Err(e) = manager(env) {
        return done("refused", 0, &e, EXIT_REFUSED);
    }
    let allowed = match allowed(env, account, scope) {
        Ok(a) => a,
        Err(e) => return done("refused", 0, &e, EXIT_REFUSED),
    };
    if let Some(w) = words.iter().find(|w| !allowed.contains(w)) {
        let whose =
            if scope == "-" { "llmcf-install's own clock keeper".to_string() } else { format!("bundle {scope}") };
        return done(
            "refused",
            0,
            &format!("{w} is not a package the signed list of {whose} names; nothing was changed"),
            EXIT_REFUSED,
        );
    }
    match install(env, account, scope, words, action == "apply") {
        Ok(out) if action == "plan" => done(
            "planned",
            0,
            &format!(
                "source={} pending={} would={} already={}",
                out.source,
                words.len() - out.already.len(),
                list(&out.would),
                list(&out.already)
            ),
            EXIT_OK,
        ),
        Ok(out) => {
            log(env, scope, &out.source, "ok", &out.installed);
            done(
                "ok",
                out.installed.len(),
                &format!("source={} installed={} already={}", out.source, list(&out.installed), list(&out.already)),
                EXIT_OK,
            )
        }
        Err(e) => {
            if action == "apply" {
                log(env, scope, "-", "failed", words);
            }
            let refused = e.ends_with("nothing was changed");
            done(if refused { "refused" } else { "failed" }, 0, &e, if refused { EXIT_REFUSED } else { EXIT_FAILED })
        }
    }
}

fn list(items: &[String]) -> String {
    if items.is_empty() {
        "-".into()
    } else {
        items.join(",")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_calls_it_takes() {
        let call = |w: &[&str]| is_call(&w.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert!(call(&["plan", "-", "fake-hwclock"]));
        assert!(call(&["apply", "llm-0123abcd-20261101120000", "libc6=2.36-9+deb12u4", "g++"]));
        for bad in [
            &["plan", "-"][..],
            &["install", "-", "fake-hwclock"],
            &["plan", "--", "fake-hwclock"],
            &["plan", "-", "-y"],
            &["plan", "-", "./x.deb"],
            &["plan", "-", "a;b"],
            &["plan", "-", "fake-hwclock", "fake-hwclock=0.13"],
            &["plan", "-", "x:arm64"],
        ] {
            assert!(!call(bad), "{bad:?}");
        }
    }

    #[test]
    fn a_release_and_its_suites() {
        let r = parse_release(
            "Origin: Debian\nSuite: oldstable-updates\nCodename: bookworm-updates\nArchitectures: amd64 arm64\n\
             MD5Sum:\n 0123 12 main/binary-arm64/Packages\nSHA256:\n \
             c7c883a61f348283050d3a754e7c8119117c4019bf65da804c78fdae315866f1 49434864 main/binary-arm64/Packages\n",
        );
        assert!(of_release(&r, "bookworm") && !of_release(&r, "trixie") && !of_release(&r, "book"));
        assert_eq!(r.architectures, ["amd64", "arm64"]);
        assert_eq!(r.sha256.len(), 1);
        assert_eq!(r.sha256["main/binary-arm64/Packages"].1, 49434864);
    }

    #[test]
    fn apt_simulation_lines() {
        let text = "NOTE: This is only a simulation!\nInst fake-hwclock (0.13 Debian:12.5/stable [all])\n\
                    Inst libc6 [2.36-9] (2.36-9+deb12u4 Debian:12.5/stable [arm64])\nConf fake-hwclock (0.13)\n";
        assert_eq!(simulated(text), ["fake-hwclock=0.13", "libc6=2.36-9+deb12u4"]);
    }
}
