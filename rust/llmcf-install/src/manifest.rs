//! The bundle manifest (schema llmcf-bundle/1) exactly as orchestrator/runtime/bundle/manifest.py writes and checks
//! it: every key known (anything else is refused), every value checked by the same rules (the patterns are ported
//! one by one; tests/data/bundle_manifest_vectors.json holds the shared vectors both test suites check). Nothing the
//! manifest says is trusted before its signature passed (sig.rs), and even then only through these checks.

use serde_json::{Map, Value};

pub const SCHEMA: &str = "llmcf-bundle/1";
pub const MAX_MANIFEST_BYTES: usize = 1 << 20;
pub const MAX_COMPONENTS: usize = 16;
pub const MAX_FILES: usize = 64;
pub const MAX_ARGS: usize = 96;
pub const MAX_ROOT_ACTIONS: usize = 64;
pub const ROOT_ACTIONS: [&str; 11] = [
    "create_user",
    "install_unit",
    "set_cgroup",
    "place_root_file",
    "remove_user",
    "remove_unit",
    "remove_cgroup",
    "remove_root_file",
    "run_harness",
    "allow_unit_control",
    "install_package",
];
/// The component kinds that are no engine (manifest.NOT_ENGINES): every other kind is an engine whose unit the
/// gateway may start and stop (allow_unit_control), whatever the kind is called.
pub const NOT_ENGINES: [&str; 2] = ["gateway", "harness"];
/// At most this many units in one allow_unit_control (one per engine component, manifest.MAX_CONTROL_UNITS).
pub const MAX_CONTROL_UNITS: usize = 15;

/// A component kind (manifest.RE_KIND `^[a-z][a-z0-9-]{1,31}$`): the known ones (llm, stt, tts, vision, gateway,
/// harness) and any kind of its own.
pub fn is_kind(s: &str) -> bool {
    (2..=32).contains(&s.len())
        && s.as_bytes()[0].is_ascii_lowercase()
        && s.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}

/// Whether a component of `kind` is an engine (manifest.is_engine).
pub fn is_engine(kind: &str) -> bool {
    is_kind(kind) && !NOT_ENGINES.contains(&kind)
}
/// The two files of a harness component (manifest.HARNESS_ARCHIVE, manifest.HARNESS_WATCH).
pub const HARNESS_ARCHIVE: &str = "harness.tar";
pub const HARNESS_WATCH: &str = "harness_watch.sh";
pub const ROOT_PLACES: [&str; 3] = ["unit-dropin", "tmpfiles", "sysctl"];
/// At most this many packages in one install_package (manifest.MAX_PACKAGES).
pub const MAX_PACKAGES: usize = 16;

// --- the value rules (manifest.py RE_*; no regex crate: each pattern as a small function) ------------------------

fn all(s: &str, ok: impl Fn(u8) -> bool) -> bool {
    s.bytes().all(ok)
}
fn lower_digit(c: u8) -> bool {
    c.is_ascii_lowercase() || c.is_ascii_digit()
}
fn alnum(c: u8) -> bool {
    c.is_ascii_alphanumeric()
}

/// RE_ID `^[a-z0-9][a-z0-9._-]{0,47}$`
pub fn is_id(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 48 && lower_digit(b[0]) && all(s, |c| lower_digit(c) || b"._-".contains(&c))
}

/// RE_WORD `^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$`
pub fn is_word(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 64 && (alnum(b[0]) || b[0] == b'_') && all(s, |c| alnum(c) || b"._-".contains(&c))
}

/// RE_FILE `^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$` (never "." or "..": they start with '.')
pub fn is_file(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 128 && (alnum(b[0]) || b[0] == b'_') && all(s, |c| alnum(c) || b"._-".contains(&c))
}

/// RE_VAR `^[a-z][a-z0-9_]{0,31}$`
pub fn is_var(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 32 && b[0].is_ascii_lowercase() && all(s, |c| lower_digit(c) || c == b'_')
}

/// RE_SHA `^[0-9a-f]{64}$`
pub fn is_sha(s: &str) -> bool {
    s.len() == 64 && all(s, |c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

/// RE_VERSION `^\d{1,4}\.\d{1,4}\.\d{1,4}$`
pub fn is_version(s: &str) -> bool {
    let parts: Vec<&str> = s.split('.').collect();
    parts.len() == 3 && parts.iter().all(|p| (1..=4).contains(&p.len()) && all(p, |c| c.is_ascii_digit()))
}

/// RE_LICENCE `^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$`
pub fn is_licence(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 64 && alnum(b[0]) && all(s, |c| alnum(c) || b".+-".contains(&c))
}

/// RE_ARG `^(?:@DIR@/)?[A-Za-z0-9._+:=,-][A-Za-z0-9._+/:=,-]{0,255}$` and no `..` part (StartSpec._args)
pub fn is_arg(s: &str) -> bool {
    let rest = s.strip_prefix("@DIR@/").unwrap_or(s);
    let b = rest.as_bytes();
    let first = |c: u8| alnum(c) || b"._+:=,-".contains(&c);
    !b.is_empty()
        && b.len() <= 256
        && first(b[0])
        && all(rest, |c| first(c) || c == b'/')
        && !s.split('/').any(|p| p == "..")
}

/// RE_URL `^https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~+,=:@%/-]*)?$`
pub fn is_url(s: &str) -> bool {
    let rest = match s.strip_prefix("https://").or_else(|| s.strip_prefix("http://")) {
        Some(r) => r,
        None => return false,
    };
    let (authority, path) = match rest.find('/') {
        Some(i) => (&rest[..i], Some(&rest[i..])),
        None => (rest, None),
    };
    let (host, port) = match authority.find(':') {
        Some(i) => (&authority[..i], Some(&authority[i + 1..])),
        None => (authority, None),
    };
    if host.is_empty() || !all(host, |c| alnum(c) || c == b'.' || c == b'-') {
        return false;
    }
    if let Some(p) = port {
        if !(1..=5).contains(&p.len()) || !all(p, |c| c.is_ascii_digit()) {
            return false;
        }
    }
    match path {
        Some(p) => all(p, |c| alnum(c) || b"._~+,=:@%/-".contains(&c)),
        None => true,
    }
}

/// The account rule (remote/address.valid_user: `^[a-z_][a-z0-9_.-]{0,31}$`, never root), lower-case as useradd
/// takes it (manifest.check_value "user").
pub fn is_user(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 32
        && (b[0].is_ascii_lowercase() || b[0] == b'_')
        && all(s, |c| lower_digit(c) || b"_.-".contains(&c))
        && s != "root"
}

/// A unit `^llmcf-[a-z0-9-]{1,40}\.service$`
pub fn is_unit(s: &str) -> bool {
    match s.strip_prefix("llmcf-").and_then(|r| r.strip_suffix(".service")) {
        Some(name) => (1..=40).contains(&name.len()) && all(name, |c| lower_digit(c) || c == b'-'),
        None => false,
    }
}

/// A root file's name `^llmcf-[a-z0-9._-]{1,60}\.conf$` (the fixed places take only llmcf's own .conf files).
pub fn is_root_file(s: &str) -> bool {
    match s.strip_prefix("llmcf-").and_then(|r| r.strip_suffix(".conf")) {
        Some(name) => (1..=60).contains(&name.len()) && all(name, |c| lower_digit(c) || b"._-".contains(&c)),
        None => false,
    }
}

/// A plain word of the board facts (os, init system) `^[a-z0-9_.-]{1,32}$`
pub fn is_fact(s: &str) -> bool {
    (1..=32).contains(&s.len()) && all(s, |c| lower_digit(c) || b"_.-".contains(&c))
}

/// RE_HARNESS_ID `^[a-z][a-z0-9-]{1,39}$`
pub fn is_harness_id(s: &str) -> bool {
    let b = s.as_bytes();
    (2..=40).contains(&b.len()) && b[0].is_ascii_lowercase() && all(s, |c| lower_digit(c) || c == b'-')
}

/// RE_HARNESS_PATH `^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,15}$`, at most 255 characters, no `.` or `..` part
pub fn is_harness_path(s: &str) -> bool {
    let parts: Vec<&str> = s.split('/').collect();
    !s.is_empty()
        && s.len() <= 255
        && parts.len() <= 16
        && parts.iter().all(|p| !p.is_empty() && *p != "." && *p != ".." && all(p, |c| alnum(c) || b"._-".contains(&c)))
}

/// RE_HARNESS_ARG `^--?[A-Za-z0-9][A-Za-z0-9_.=-]{0,63}$`
pub fn is_harness_arg(s: &str) -> bool {
    let rest = s.strip_prefix("--").or_else(|| s.strip_prefix('-')).unwrap_or("");
    let b = rest.as_bytes();
    !b.is_empty() && b.len() <= 64 && alnum(b[0]) && all(rest, |c| alnum(c) || b"_.=-".contains(&c))
}

/// RE_HARNESS_ENV `^[A-Z][A-Z0-9_]{0,63}=[A-Za-z0-9_.:-]{0,64}$`
pub fn is_harness_env(s: &str) -> bool {
    let Some((name, value)) = s.split_once('=') else { return false };
    let n = name.as_bytes();
    !n.is_empty()
        && n.len() <= 64
        && n[0].is_ascii_uppercase()
        && all(name, |c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == b'_')
        && value.len() <= 64
        && all(value, |c| alnum(c) || b"_.:-".contains(&c))
}

/// RE_PACKAGE `^[a-z0-9][a-z0-9+.-]{1,63}(=[0-9][A-Za-z0-9.+~:-]{0,63})?$`: a package of the distribution by
/// Debian's naming rules, optionally with the one version to install (`NAME=VERSION`). Never an option, a path, an
/// architecture qualifier or anything a shell would read.
pub fn is_package(s: &str) -> bool {
    let (name, version) = match s.split_once('=') {
        Some((n, v)) => (n, Some(v)),
        None => (s, None),
    };
    let n = name.as_bytes();
    let name_ok =
        (2..=64).contains(&n.len()) && lower_digit(n[0]) && all(name, |c| lower_digit(c) || b"+.-".contains(&c));
    let version_ok = match version {
        None => true,
        Some(v) => {
            let b = v.as_bytes();
            (1..=64).contains(&b.len()) && b[0].is_ascii_digit() && all(v, |c| alnum(c) || b".+~:-".contains(&c))
        }
    };
    name_ok && version_ok
}

/// The name of a package word (`NAME` or `NAME=VERSION`).
pub fn package_name(s: &str) -> &str {
    s.split_once('=').map(|(n, _)| n).unwrap_or(s)
}

/// RE_PIN `^(?:[0-9a-f]{40}|[0-9a-f]{64})$`
pub fn is_pin(s: &str) -> bool {
    (s.len() == 40 || s.len() == 64) && all(s, |c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

fn is_created(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 20
        && b.iter().enumerate().all(|(i, &c)| match i {
            4 | 7 => c == b'-',
            10 => c == b'T',
            13 | 16 => c == b':',
            19 => c == b'Z',
            _ => c.is_ascii_digit(),
        })
}

// --- the manifest ---------------------------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct FileEntry {
    pub name: String,
    pub sha256: String,
    pub size: u64,
    pub mode: String,
    pub role: String,
    pub source: String,
}

/// The person's harness of a harness component (manifest.HarnessPart).
#[derive(Debug, Clone, PartialEq)]
pub struct HarnessPart {
    pub id: String,
    pub pin: String,
    pub tree_sha256: String,
    pub installer: String,
    pub dry_run_args: Vec<String>,
    pub exit_codes: Vec<(u8, String)>,
    pub assume_yes: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Component {
    pub id: String,
    pub kind: String,
    pub files: Vec<FileEntry>,
    pub has_start: bool,
    pub keygen: bool,
    pub harness: Option<HarnessPart>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RootAction {
    pub action: String,
    pub component: String,
    pub user: Option<String>,
    pub unit: Option<String>,
    pub memory_max_mb: Option<u64>,
    /// systemd's MemoryHigh (the unit is slowed and reclaimed above it), never above memory_max_mb
    pub memory_high_mb: Option<u64>,
    pub cpu_weight: Option<u64>,
    /// systemd's OOMScoreAdjust: who the kernel's out-of-memory killer takes first (never -1000, never exempt)
    pub oom_score_adj: Option<i64>,
    pub place: Option<String>,
    pub file: Option<String>,
    /// allow_unit_control only: the bundle's engine units the gateway's account may start, stop and restart
    pub units: Vec<String>,
    /// install_unit only: the unit is installed but not enabled at boot; the gateway starts it on demand (engines in
    /// turn). Only `true` is written; absent means enabled and started as before.
    pub on_demand: bool,
    /// install_package only: the distribution's packages the component needs (NAME or NAME=VERSION)
    pub packages: Vec<String>,
}

impl RootAction {
    /// What a report line says of it: the component and its values (key=value, the manifest's own order).
    pub fn what(&self) -> String {
        let mut out = vec![self.component.clone()];
        let units = (!self.units.is_empty()).then(|| self.units.join(","));
        let packages = (!self.packages.is_empty()).then(|| self.packages.join(","));
        let pairs: [(&str, Option<String>); 11] = [
            ("user", self.user.clone()),
            ("unit", self.unit.clone()),
            ("units", units),
            ("memory_max_mb", self.memory_max_mb.map(|v| v.to_string())),
            ("memory_high_mb", self.memory_high_mb.map(|v| v.to_string())),
            ("cpu_weight", self.cpu_weight.map(|v| v.to_string())),
            ("oom_score_adj", self.oom_score_adj.map(|v| v.to_string())),
            ("place", self.place.clone()),
            ("file", self.file.clone()),
            ("on_demand", self.on_demand.then(|| "true".to_string())),
            ("packages", packages),
        ];
        for (k, v) in pairs {
            if let Some(v) = v {
                out.push(format!("{k}={v}"));
            }
        }
        out.join(" ")
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Manifest {
    pub bundle_id: String,
    pub mode: String,
    pub init_system: Option<String>,
    pub components: Vec<Component>,
    pub root_actions: Vec<RootAction>,
}

impl Manifest {
    pub fn component(&self, id: &str) -> Option<&Component> {
        self.components.iter().find(|c| c.id == id)
    }
}

type R<T> = Result<T, String>;

fn obj<'a>(v: &'a Value, what: &str) -> R<&'a Map<String, Value>> {
    v.as_object().ok_or_else(|| format!("{what} is not a mapping"))
}

fn keys(m: &Map<String, Value>, what: &str, required: &[&str], optional: &[&str]) -> R<()> {
    for k in m.keys() {
        if !required.contains(&k.as_str()) && !optional.contains(&k.as_str()) {
            return Err(format!("{what}: unknown key {k:?}"));
        }
    }
    for k in required {
        if !m.contains_key(*k) {
            return Err(format!("{what}: {k} is missing"));
        }
    }
    Ok(())
}

fn text<'a>(m: &'a Map<String, Value>, key: &str, what: &str) -> R<&'a str> {
    m.get(key).and_then(Value::as_str).ok_or_else(|| format!("{what}.{key} is not text"))
}

fn opt_text<'a>(m: &'a Map<String, Value>, key: &str, what: &str) -> R<Option<&'a str>> {
    match m.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s)),
        Some(_) => Err(format!("{what}.{key} is not text")),
    }
}

/// A whole number (never a bool, never a float, never text: stricter than pydantic's lax mode, never looser).
fn uint(m: &Map<String, Value>, key: &str, what: &str, lo: u64, hi: u64) -> R<Option<u64>> {
    match m.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => match n.as_u64() {
            Some(v) if (lo..=hi).contains(&v) => Ok(Some(v)),
            _ => Err(format!("{what}.{key} must be a whole number from {lo} to {hi}")),
        },
        Some(_) => Err(format!("{what}.{key} must be a whole number from {lo} to {hi}")),
    }
}

fn int(m: &Map<String, Value>, key: &str, what: &str, lo: i64, hi: i64) -> R<Option<i64>> {
    match m.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Number(n)) => match n.as_i64() {
            Some(v) if (lo..=hi).contains(&v) => Ok(Some(v)),
            _ => Err(format!("{what}.{key} must be a whole number from {lo} to {hi}")),
        },
        Some(_) => Err(format!("{what}.{key} must be a whole number from {lo} to {hi}")),
    }
}

fn one_of<'a>(value: &'a str, choices: &[&str], what: &str) -> R<&'a str> {
    if choices.contains(&value) {
        Ok(value)
    } else {
        Err(format!("{what} must be one of {}", choices.join(", ")))
    }
}

fn file_entry(v: &Value, comp: &str) -> R<FileEntry> {
    let what = format!("component {comp} file");
    let m = obj(v, &what)?;
    keys(m, &what, &["name", "sha256", "size", "role"], &["mode", "source", "url", "format", "licence"])?;
    let name = text(m, "name", &what)?;
    if !is_file(name) {
        return Err(format!("{what}: the name {name:?} is not a plain file name"));
    }
    let sha = text(m, "sha256", &what)?;
    if !is_sha(sha) {
        return Err(format!("{what} {name}: sha256 must be 64 lower-case hex digits"));
    }
    let size = uint(m, "size", &what, 0, 1 << 46)?.ok_or_else(|| format!("{what} {name}: size is missing"))?;
    let mode = opt_text(m, "mode", &what)?.unwrap_or("0644");
    one_of(mode, &["0600", "0644", "0700", "0755"], &format!("{what} {name} mode"))?;
    let role = one_of(text(m, "role", &what)?, &["model", "mmproj", "program", "runtime", "config", "data"], &what)?;
    let source = opt_text(m, "source", &what)?.unwrap_or("bundle");
    one_of(source, &["bundle", "online"], &format!("{what} {name} source"))?;
    let url = opt_text(m, "url", &what)?;
    if source == "online" {
        if !url.map(is_url).unwrap_or(false) {
            return Err(format!("{what} {name}: an online item needs a plain https URL"));
        }
    } else if url.is_some() {
        return Err(format!("{what} {name}: only an online item has a URL"));
    }
    if let Some(f) = opt_text(m, "format", &what)? {
        if !is_word(f) {
            return Err(format!("{what} {name}: format must be a plain word"));
        }
    }
    if let Some(l) = opt_text(m, "licence", &what)? {
        if !is_licence(l) {
            return Err(format!("{what} {name}: licence must be an SPDX identifier or a short word"));
        }
    }
    Ok(FileEntry {
        name: name.into(),
        sha256: sha.into(),
        size,
        mode: mode.into(),
        role: role.into(),
        source: source.into(),
    })
}

fn harness_part(v: &Value, what: &str) -> R<HarnessPart> {
    let what = format!("{what} harness");
    let m = obj(v, &what)?;
    keys(m, &what, &["id", "pin", "tree_sha256", "installer"], &["dry_run_args", "exit_codes", "assume_yes"])?;
    let id = text(m, "id", &what)?;
    if !is_harness_id(id) {
        return Err(format!("{what}: a harness id is 2 to 40 lower-case letters, digits and '-'"));
    }
    let pin = text(m, "pin", &what)?;
    if !is_pin(pin) {
        return Err(format!("{what}: a pin is a 40-digit commit or a sha256"));
    }
    let tree = text(m, "tree_sha256", &what)?;
    if !is_sha(tree) {
        return Err(format!("{what}: tree_sha256 must be 64 lower-case hex digits"));
    }
    let installer = text(m, "installer", &what)?;
    if !is_harness_path(installer) {
        return Err(format!("{what}: the installer is a plain path inside the harness"));
    }
    let dry_run_args: Vec<String> = match m.get("dry_run_args") {
        None => Vec::new(),
        Some(Value::Array(items)) if items.len() <= 8 => items
            .iter()
            .map(|a| a.as_str().filter(|a| is_harness_arg(a)).map(String::from))
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| format!("{what}: dry_run_args are options such as --dry-run"))?,
        Some(_) => return Err(format!("{what}: dry_run_args: up to 8 options such as --dry-run")),
    };
    let mut exit_codes = Vec::new();
    match m.get("exit_codes") {
        None => exit_codes.push((0u8, "done".to_string())),
        Some(Value::Object(codes)) if !codes.is_empty() => {
            for (k, v) in codes {
                let plain = !k.is_empty()
                    && k.len() <= 3
                    && all(k, |c| c.is_ascii_digit())
                    && (k == "0" || !k.starts_with('0'));
                let code = k.parse::<u16>().ok().filter(|c| plain && *c <= 255);
                let outcome = v.as_str().filter(|o| *o == "done" || *o == "findings");
                match (code, outcome) {
                    (Some(c), Some(o)) => exit_codes.push((c as u8, o.to_string())),
                    _ => return Err(format!("{what}: exit_codes: codes 0 to 255, each meaning done or findings")),
                }
            }
        }
        Some(_) => return Err(format!("{what}: exit_codes: codes 0 to 255, each meaning done or findings")),
    }
    let assume_yes = opt_text(m, "assume_yes", &what)?;
    if let Some(a) = assume_yes {
        if !is_harness_env(a) {
            return Err(format!("{what}: assume_yes is NAME=value"));
        }
    }
    Ok(HarnessPart {
        id: id.into(),
        pin: pin.into(),
        tree_sha256: tree.into(),
        installer: installer.into(),
        dry_run_args,
        exit_codes,
        assume_yes: assume_yes.map(Into::into),
    })
}

fn component(v: &Value) -> R<Component> {
    let m = obj(v, "a component")?;
    keys(
        m,
        "component",
        &["id", "kind", "block", "block_version", "block_sha256"],
        &["block_trust", "variables", "files", "start", "keygen", "harness"],
    )?;
    let id = text(m, "id", "component")?;
    if !is_id(id) {
        return Err("a component id is lower-case letters, digits, '.', '_' and '-'".into());
    }
    let what = format!("component {id}");
    let kind = text(m, "kind", &what)?;
    if !is_kind(kind) {
        return Err(format!("{what}: a kind is 2 to 32 lower-case letters, digits and '-', starting with a letter"));
    }
    if !is_id(text(m, "block", &what)?) {
        return Err(format!("{what}: the block is no block id"));
    }
    if !is_version(text(m, "block_version", &what)?) {
        return Err(format!("{what}: a block version is MAJOR.MINOR.PATCH"));
    }
    if !is_sha(text(m, "block_sha256", &what)?) {
        return Err(format!("{what}: block_sha256 must be 64 lower-case hex digits"));
    }
    if let Some(t) = opt_text(m, "block_trust", &what)? {
        one_of(t, &["framework", "indexed", "signed", "own"], &format!("{what} block_trust"))?;
    }
    if let Some(vars) = m.get("variables") {
        let vars = obj(vars, &format!("{what} variables"))?;
        for (k, val) in vars {
            if !is_var(k) {
                return Err(format!("{what}: variable name {k:?} is not a plain name"));
            }
            let ok = match val {
                Value::Bool(_) => true,
                Value::Number(n) => n.as_u64().map(|x| x < (1 << 31)).unwrap_or(false),
                Value::String(s) => s.is_empty() || is_word(s),
                _ => false,
            };
            if !ok {
                return Err(format!("{what}: variable {k}: only numbers, yes/no and plain words"));
            }
        }
    }
    let files: Vec<FileEntry> = match m.get("files") {
        None => Vec::new(),
        Some(Value::Array(items)) => items.iter().map(|f| file_entry(f, id)).collect::<R<_>>()?,
        Some(_) => return Err(format!("{what}: files is not a list")),
    };
    if files.len() > MAX_FILES {
        return Err(format!("{what}: at most {MAX_FILES} files"));
    }
    let mut names: Vec<&str> = files.iter().map(|f| f.name.as_str()).collect();
    names.sort_unstable();
    if names.windows(2).any(|w| w[0] == w[1]) {
        return Err(format!("{what}: a file name twice"));
    }
    let has_start = match m.get("start") {
        None | Some(Value::Null) => false,
        Some(s) => {
            let sm = obj(s, &format!("{what} start"))?;
            keys(sm, &format!("{what} start"), &["program"], &["args"])?;
            let program = text(sm, "program", &what)?;
            let parts: Vec<&str> = program.split('/').collect();
            if !(1..=4).contains(&parts.len()) || parts.iter().any(|p| !is_file(p)) {
                return Err(format!("{what}: program must be a plain relative path in the component's folder"));
            }
            match sm.get("args") {
                None => {}
                Some(Value::Array(args)) => {
                    if args.len() > MAX_ARGS {
                        return Err(format!("{what}: at most {MAX_ARGS} arguments"));
                    }
                    for a in args {
                        if !a.as_str().map(is_arg).unwrap_or(false) {
                            return Err(format!("{what}: an argument holds characters a program argument never takes"));
                        }
                    }
                }
                Some(_) => return Err(format!("{what}: args is not a list")),
            }
            let first = parts[0];
            let runtime = files.iter().any(|f| f.role == "runtime");
            if !files.iter().any(|f| f.name == first) && !(runtime && first == "runtime") {
                return Err(format!("{what}: its program is none of its files"));
            }
            true
        }
    };
    let keygen = match m.get("keygen") {
        None => false,
        Some(Value::Bool(b)) => *b,
        Some(_) => return Err(format!("{what}: keygen is yes or no")),
    };
    let harness = match m.get("harness") {
        None | Some(Value::Null) => None,
        Some(h) => Some(harness_part(h, &what)?),
    };
    if (kind == "harness") != harness.is_some() {
        return Err(format!("{what}: a harness component, and only one, names its harness"));
    }
    if harness.is_some() {
        let has =
            |name: &str, role: &str| files.iter().any(|f| f.name == name && f.role == role && f.source == "bundle");
        if !has(HARNESS_ARCHIVE, "data") || !has(HARNESS_WATCH, "program") {
            return Err(format!(
                "{what}: a harness comes as {HARNESS_ARCHIVE} (data) and {HARNESS_WATCH} (program) in the bundle"
            ));
        }
        if has_start {
            return Err(format!("{what}: a harness has no start of its own (its installer runs once)"));
        }
    }
    Ok(Component { id: id.into(), kind: kind.into(), files, has_start, keygen, harness })
}

fn root_action(v: &Value) -> R<RootAction> {
    let m = obj(v, "a root action")?;
    keys(
        m,
        "root action",
        &["action", "component"],
        &[
            "user",
            "unit",
            "memory_max_mb",
            "memory_high_mb",
            "cpu_weight",
            "oom_score_adj",
            "place",
            "file",
            "units",
            "on_demand",
            "packages",
        ],
    )?;
    let action = one_of(text(m, "action", "root action")?, &ROOT_ACTIONS, "root action")?.to_string();
    let component = text(m, "component", "root action")?;
    if !is_id(component) {
        return Err("root action: component must be a component id".into());
    }
    let what = format!("root action {action}");
    let user = opt_text(m, "user", &what)?;
    if let Some(u) = user {
        if !is_user(u) {
            return Err(format!("{what}: an account name (lower-case letters, digits, '_', '.', '-'; never root)"));
        }
    }
    let unit = opt_text(m, "unit", &what)?;
    if let Some(u) = unit {
        if !is_unit(u) {
            return Err(format!("{what}: a unit is named llmcf-<name>.service"));
        }
    }
    let memory_max_mb = uint(m, "memory_max_mb", &what, 16, 1 << 20)?;
    let memory_high_mb = uint(m, "memory_high_mb", &what, 16, 1 << 20)?;
    if let (Some(high), Some(max)) = (memory_high_mb, memory_max_mb) {
        if high > max {
            return Err(format!("{what}: memory_high_mb is above memory_max_mb"));
        }
    }
    let cpu_weight = uint(m, "cpu_weight", &what, 1, 10000)?;
    let oom_score_adj = int(m, "oom_score_adj", &what, -999, 1000)?;
    let place = opt_text(m, "place", &what)?;
    if let Some(p) = place {
        one_of(p, &ROOT_PLACES, &format!("{what} place"))?;
    }
    let file = opt_text(m, "file", &what)?;
    if let Some(f) = file {
        if !is_file(f) {
            return Err(format!("{what}: file is not a plain file name"));
        }
    }
    let units = match m.get("units") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(items)) => {
            let mut out: Vec<String> = Vec::new();
            for item in items {
                let u = item
                    .as_str()
                    .filter(|u| is_unit(u))
                    .ok_or_else(|| format!("{what}: a unit is named llmcf-<name>.service"))?;
                if out.iter().any(|x| x == u) {
                    return Err(format!("{what}: a unit twice"));
                }
                out.push(u.into());
            }
            if !(1..=MAX_CONTROL_UNITS).contains(&out.len()) {
                return Err(format!("{what}: 1 to {MAX_CONTROL_UNITS} units"));
            }
            out
        }
        Some(_) => return Err(format!("{what}: units is a list")),
    };
    if !units.is_empty() && action != "allow_unit_control" {
        return Err(format!("{what}: only allow_unit_control names units"));
    }
    let packages = match m.get("packages") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(items)) => {
            let mut out: Vec<String> = Vec::new();
            for item in items {
                let p = item.as_str().filter(|p| is_package(p)).ok_or_else(|| {
                    format!("{what}: a package is named by the distribution's rules (NAME or NAME=VERSION)")
                })?;
                if out.iter().any(|x| package_name(x) == package_name(p)) {
                    return Err(format!("{what}: a package twice"));
                }
                out.push(p.into());
            }
            if !(1..=MAX_PACKAGES).contains(&out.len()) {
                return Err(format!("{what}: 1 to {MAX_PACKAGES} packages"));
            }
            out
        }
        Some(_) => return Err(format!("{what}: packages is a list")),
    };
    if !packages.is_empty() && action != "install_package" {
        return Err(format!("{what}: only install_package names packages"));
    }
    let on_demand = match m.get("on_demand") {
        None | Some(Value::Null) => false,
        Some(Value::Bool(true)) => true,
        Some(_) => return Err(format!("{what}: on_demand is true or absent")),
    };
    if on_demand && action != "install_unit" {
        return Err(format!("{what}: only install_unit is on demand"));
    }
    let need: &[&str] = match action.as_str() {
        "allow_unit_control" => &["user", "units"],
        "create_user" | "remove_user" => &["user"],
        "install_unit" => &["unit", "user"],
        "remove_unit" | "remove_cgroup" => &["unit"],
        "set_cgroup" => &["unit", "memory_max_mb"],
        "run_harness" => &[],
        "install_package" => &["packages"],
        _ => &["place", "file"],
    };
    let have = |k: &str| match k {
        "user" => user.is_some(),
        "unit" => unit.is_some(),
        "memory_max_mb" => memory_max_mb.is_some(),
        "place" => place.is_some(),
        "units" => !units.is_empty(),
        "packages" => !packages.is_empty(),
        _ => file.is_some(),
    };
    let missing: Vec<&str> = need.iter().copied().filter(|k| !have(k)).collect();
    if !missing.is_empty() {
        return Err(format!("{action} needs {}", missing.join(", ")));
    }
    if action.ends_with("root_file") {
        if !is_root_file(file.unwrap_or("")) {
            return Err(format!("{what}: a root file is named llmcf-<name>.conf"));
        }
        if place == Some("unit-dropin") && unit.is_none() {
            return Err(format!("{what}: a unit drop-in names its unit"));
        }
    }
    Ok(RootAction {
        action,
        component: component.into(),
        user: user.map(Into::into),
        unit: unit.map(Into::into),
        memory_max_mb,
        memory_high_mb,
        cpu_weight,
        oom_score_adj,
        place: place.map(Into::into),
        file: file.map(Into::into),
        units,
        on_demand,
        packages,
    })
}

/// allow_unit_control in its bundle (manifest.py BundleManifest._whole, the same rule): it belongs to the bundle's
/// gateway component, names the account the gateway's own unit runs as, and each of its units is the unit an
/// install_unit of this bundle writes for one of its engine components (any kind but gateway and harness) under the
/// bundle's one naming rule, `llmcf-<component id>.service`. Nothing else: no other unit, no pattern, no wildcard.
fn control_rule(a: &RootAction, comp: &Component, components: &[Component], actions: &[RootAction]) -> R<()> {
    let what = "root action allow_unit_control";
    if comp.kind != "gateway" {
        return Err(format!("{what}: component {} is not the bundle's gateway", comp.id));
    }
    let own = actions.iter().find(|b| b.action == "install_unit" && b.component == comp.id);
    if own.and_then(|b| b.user.as_deref()) != a.user.as_deref() || own.is_none() {
        return Err(format!("{what}: the account is not the one the gateway's own unit runs as"));
    }
    for unit in &a.units {
        let engine = actions
            .iter()
            .filter(|b| b.action == "install_unit" && b.unit.as_deref() == Some(unit))
            .find_map(|b| components.iter().find(|c| c.id == b.component && is_engine(&c.kind)));
        match engine {
            Some(c) if *unit == format!("llmcf-{}.service", c.id) => {}
            _ => return Err(format!("{what}: {unit} is no engine unit of this bundle")),
        }
    }
    Ok(())
}

/// The manifest from its signed bytes (JSON as dump_manifest writes it), every value checked.
pub fn parse(data: &[u8]) -> R<Manifest> {
    if data.len() > MAX_MANIFEST_BYTES {
        return Err("the manifest is larger than 1 MiB".into());
    }
    let v: Value = serde_json::from_slice(data).map_err(|_| "the manifest is not JSON".to_string())?;
    let m = obj(&v, "the manifest")?;
    keys(
        m,
        "the manifest",
        &["schema", "bundle_id", "created", "framework_version", "board", "components"],
        &["mode", "root_actions"],
    )?;
    if text(m, "schema", "manifest")? != SCHEMA {
        return Err(format!("the manifest's schema is not {SCHEMA}"));
    }
    let bundle_id = text(m, "bundle_id", "manifest")?;
    if !is_id(bundle_id) {
        return Err("bundle_id is lower-case letters, digits, '.', '_' and '-'".into());
    }
    if !is_created(text(m, "created", "manifest")?) {
        return Err("created is a UTC time YYYY-MM-DDTHH:MM:SSZ".into());
    }
    let fv = text(m, "framework_version", "manifest")?;
    if !(1..=32).contains(&fv.len()) || !all(fv, |c| alnum(c) || b".+-".contains(&c)) {
        return Err("framework_version is not a version".into());
    }
    let board = obj(m.get("board").unwrap_or(&Value::Null), "board")?;
    keys(board, "board", &["device_id", "arch"], &["os", "init_system"])?;
    let device = text(board, "device_id", "board")?;
    if !(8..=64).contains(&device.len()) || !all(device, |c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return Err("board.device_id is 8 to 64 hex digits".into());
    }
    let arch = text(board, "arch", "board")?;
    if !(1..=32).contains(&arch.len()) || !all(arch, |c| alnum(c) || b"_.-".contains(&c)) {
        return Err("board.arch is not a plain word".into());
    }
    if let Some(os) = opt_text(board, "os", "board")? {
        if !is_fact(os) {
            return Err("board.os is not a plain word".into());
        }
    }
    let init_system = opt_text(board, "init_system", "board")?;
    if let Some(i) = init_system {
        if !is_fact(i) {
            return Err("board.init_system is not a plain word".into());
        }
    }
    let mode = opt_text(m, "mode", "manifest")?.unwrap_or("full");
    one_of(mode, &["full", "unprivileged"], "mode")?;
    let components: Vec<Component> = match m.get("components") {
        Some(Value::Array(items)) => items.iter().map(component).collect::<R<_>>()?,
        _ => return Err("components is not a list".into()),
    };
    if !(1..=MAX_COMPONENTS).contains(&components.len()) {
        return Err(format!("a bundle has 1 to {MAX_COMPONENTS} components"));
    }
    let mut ids: Vec<&str> = components.iter().map(|c| c.id.as_str()).collect();
    ids.sort_unstable();
    if ids.windows(2).any(|w| w[0] == w[1]) {
        return Err("a component id twice".into());
    }
    let root_actions: Vec<RootAction> = match m.get("root_actions") {
        None => Vec::new(),
        Some(Value::Array(items)) => items.iter().map(root_action).collect::<R<_>>()?,
        Some(_) => return Err("root_actions is not a list".into()),
    };
    if root_actions.len() > MAX_ROOT_ACTIONS {
        return Err(format!("at most {MAX_ROOT_ACTIONS} root actions"));
    }
    let units: Vec<&str> =
        root_actions.iter().filter(|a| a.action == "install_unit").filter_map(|a| a.unit.as_deref()).collect();
    if root_actions.iter().filter(|a| a.action == "allow_unit_control").count() > 1 {
        return Err("root action allow_unit_control: at most one in a bundle".into());
    }
    for a in &root_actions {
        let Some(comp) = components.iter().find(|c| c.id == a.component) else {
            return Err(format!("root action {} names no component of the bundle", a.action));
        };
        match a.action.as_str() {
            "install_unit" if !comp.has_start => {
                return Err(format!("root action install_unit: component {} has no start", comp.id))
            }
            "set_cgroup" if !units.contains(&a.unit.as_deref().unwrap_or("")) => {
                return Err(format!(
                    "root action set_cgroup: unit {} is installed by no install_unit of the bundle",
                    a.unit.as_deref().unwrap_or("")
                ))
            }
            "place_root_file" => {
                let name = a.file.as_deref().unwrap_or("");
                if !comp.files.iter().any(|f| f.name == name) {
                    return Err(format!("root action place_root_file: {name} is no file of component {}", comp.id));
                }
            }
            "run_harness" if comp.harness.is_none() => {
                return Err(format!("root action run_harness: component {} names no harness", comp.id))
            }
            "allow_unit_control" => control_rule(a, comp, &components, &root_actions)?,
            _ => {}
        }
        if a.on_demand {
            let control = root_actions.iter().find(|b| b.action == "allow_unit_control");
            let unit = a.unit.as_deref().unwrap_or("");
            if !is_engine(&comp.kind) || !control.is_some_and(|c| c.units.iter().any(|u| u == unit)) {
                return Err(format!(
                    "root action install_unit: {unit} is on demand but no engine unit the bundle's gateway may start"
                ));
            }
        }
    }
    Ok(Manifest {
        bundle_id: bundle_id.into(),
        mode: mode.into(),
        init_system: init_system.map(Into::into),
        components,
        root_actions,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const VECTORS: &str = include_str!("../../../tests/data/bundle_manifest_vectors.json");

    fn rule(name: &str) -> fn(&str) -> bool {
        match name {
            "id" => is_id,
            "word" => is_word,
            "file" => is_file,
            "var" => is_var,
            "sha" => is_sha,
            "version" => is_version,
            "licence" => is_licence,
            "arg" => is_arg,
            "url" => is_url,
            "user" => is_user,
            "unit" => is_unit,
            "root_file" => is_root_file,
            "fact" => is_fact,
            "package" => is_package,
            other => panic!("no rule {other}"),
        }
    }

    #[test]
    fn the_shared_value_vectors_hold() {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        let values = v["values"].as_object().unwrap();
        assert!(values.len() >= 12);
        for (name, cases) in values {
            let check = rule(name);
            for good in cases["accept"].as_array().unwrap() {
                assert!(check(good.as_str().unwrap()), "{name} should take {good}");
            }
            for bad in cases["reject"].as_array().unwrap() {
                assert!(!check(bad.as_str().unwrap()), "{name} should refuse {bad}");
            }
        }
    }

    #[test]
    fn the_shared_manifest_vectors_hold() {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        let base = &v["manifests"]["base"];
        let text = serde_json::to_vec(base).unwrap();
        let m = parse(&text).unwrap();
        assert_eq!(m.root_actions.len(), 3);
        for case in v["manifests"]["reject"].as_array().unwrap() {
            let mut doc = base.clone();
            apply_case(&mut doc, case);
            let bytes = serde_json::to_vec(&doc).unwrap();
            assert!(parse(&bytes).is_err(), "should refuse: {}", case["why"]);
        }
        for case in v["manifests"]["accept"].as_array().unwrap() {
            let mut doc = base.clone();
            apply_case(&mut doc, case);
            let bytes = serde_json::to_vec(&doc).unwrap();
            assert!(parse(&bytes).is_ok(), "should take: {} ({:?})", case["why"], parse(&bytes).err());
        }
    }

    pub fn apply_case(doc: &mut Value, case: &Value) {
        for patch in case["patches"].as_array().unwrap() {
            apply_patch(doc, patch);
        }
    }

    /// A patch: {"path": ["components", 0, "id"], "value": ...} (no "value": the key is removed).
    pub fn apply_patch(doc: &mut Value, patch: &Value) {
        let path = patch["path"].as_array().unwrap();
        let mut at = doc;
        for (i, step) in path.iter().enumerate() {
            let last = i == path.len() - 1;
            if last {
                match (step, patch.get("value")) {
                    (Value::String(k), Some(v)) => {
                        at.as_object_mut().unwrap().insert(k.clone(), v.clone());
                    }
                    (Value::String(k), None) => {
                        at.as_object_mut().unwrap().remove(k);
                    }
                    (Value::Number(n), Some(v)) => at[n.as_u64().unwrap() as usize] = v.clone(),
                    _ => panic!("bad patch"),
                }
                return;
            }
            at = match step {
                Value::String(k) => at.get_mut(k.as_str()).unwrap(),
                Value::Number(n) => at.get_mut(n.as_u64().unwrap() as usize).unwrap(),
                _ => panic!("bad patch"),
            };
        }
    }

    #[test]
    fn a_huge_or_unknown_manifest_is_refused() {
        assert!(parse(&vec![b' '; MAX_MANIFEST_BYTES + 1]).is_err());
        assert!(parse(b"[]").is_err());
        assert!(parse(b"{\"schema\": \"llmcf-bundle/2\"}").is_err());
    }
}
