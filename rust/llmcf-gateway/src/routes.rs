//! The gateway's route table as data: which client path goes to which engine, through which protocol adapter, with
//! which limits (`--routes FILE`, schema llmcf-gateway-routes/1, made by the bundle generator from the chosen blocks'
//! `reach` sections). Without `--routes` the table is the one the gateway always served, made from its options
//! (`Table::from_args`).
//!
//! The file is read once at the start: at most MAX_TABLE_BYTES, strict JSON, every key known (anything else is
//! refused), every value checked by the rules orchestrator/runtime/bundle/reach.py checks with
//! (tests/data/gateway_routes_vectors.json holds the vectors both test suites check). A route's `protocol` picks its
//! adapter: the OpenAI adapters (openai-chat, -completions, -models, -embeddings, -audio-transcription,
//! -audio-speech) check their bodies themselves; the generic ones forward with the declared limits: http-json (one
//! strict JSON object), http-multipart (the bounded form parser, the declared fields, a file by its magic type),
//! http-sse (an event stream passed through with an idle timeout), http-binary (a body of a declared magic type).

use crate::args::Serve;
use serde_json::{Map, Value};
use std::time::Duration;

pub const SCHEMA: &str = "llmcf-gateway-routes/1";
pub const MAX_TABLE_BYTES: usize = 64 * 1024;
pub const MAX_ROUTES: usize = 64;
pub const MAX_ENGINES: usize = 16;
pub const MAX_FIELDS: usize = 16;
pub const MAX_TYPES: usize = 16;
pub const MAX_BODY_KB: u64 = 204_800;
pub const MAX_TIMEOUT_S: u64 = 3600;
/// The gateway's answer wait of old: the timeout of the routes made from its options.
pub const DEFAULT_TIMEOUT_S: u64 = 900;
const RESERVED: [&str; 2] = ["/health", "/metrics"];
const RESERVED_PREFIXES: [&str; 2] = ["/v1/llmcf", "/api/llmcf"];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Protocol {
    Chat,
    Completions,
    Models,
    Embeddings,
    Transcription,
    Speech,
    Json,
    Multipart,
    Sse,
    Binary,
}

impl Protocol {
    pub const ALL: [Protocol; 10] = [
        Protocol::Chat,
        Protocol::Completions,
        Protocol::Models,
        Protocol::Embeddings,
        Protocol::Transcription,
        Protocol::Speech,
        Protocol::Json,
        Protocol::Multipart,
        Protocol::Sse,
        Protocol::Binary,
    ];

    pub fn word(self) -> &'static str {
        match self {
            Protocol::Chat => "openai-chat",
            Protocol::Completions => "openai-completions",
            Protocol::Models => "openai-models",
            Protocol::Embeddings => "openai-embeddings",
            Protocol::Transcription => "openai-audio-transcription",
            Protocol::Speech => "openai-audio-speech",
            Protocol::Json => "http-json",
            Protocol::Multipart => "http-multipart",
            Protocol::Sse => "http-sse",
            Protocol::Binary => "http-binary",
        }
    }

    fn methods(self) -> &'static [&'static str] {
        match self {
            Protocol::Models => &["GET"],
            Protocol::Json | Protocol::Sse => &["GET", "POST"],
            _ => &["POST"],
        }
    }

    /// Whether the protocol's body is JSON (the JSON body wait; the others are uploads).
    pub fn json_body(self) -> bool {
        !matches!(self, Protocol::Transcription | Protocol::Multipart | Protocol::Binary)
    }

    fn may_stream(self) -> bool {
        !matches!(
            self,
            Protocol::Models | Protocol::Embeddings | Protocol::Transcription | Protocol::Binary | Protocol::Multipart
        )
    }
}

/// The magic types a binary body or a multipart file may be declared with, each recognized by its first bytes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Magic {
    Jpeg,
    Png,
    Gif,
    Webp,
    Bmp,
    Tiff,
    Wav,
    Flac,
    Ogg,
    Mp3,
    Mp4,
    Webm,
    Pdf,
}

impl Magic {
    pub const ALL: [Magic; 13] = [
        Magic::Jpeg,
        Magic::Png,
        Magic::Gif,
        Magic::Webp,
        Magic::Bmp,
        Magic::Tiff,
        Magic::Wav,
        Magic::Flac,
        Magic::Ogg,
        Magic::Mp3,
        Magic::Mp4,
        Magic::Webm,
        Magic::Pdf,
    ];

    pub fn word(self) -> &'static str {
        match self {
            Magic::Jpeg => "jpeg",
            Magic::Png => "png",
            Magic::Gif => "gif",
            Magic::Webp => "webp",
            Magic::Bmp => "bmp",
            Magic::Tiff => "tiff",
            Magic::Wav => "wav",
            Magic::Flac => "flac",
            Magic::Ogg => "ogg",
            Magic::Mp3 => "mp3",
            Magic::Mp4 => "mp4",
            Magic::Webm => "webm",
            Magic::Pdf => "pdf",
        }
    }

    /// The media type a body of this type is sent to the engine with (never the client's word).
    pub fn mime(self) -> &'static str {
        match self {
            Magic::Jpeg => "image/jpeg",
            Magic::Png => "image/png",
            Magic::Gif => "image/gif",
            Magic::Webp => "image/webp",
            Magic::Bmp => "image/bmp",
            Magic::Tiff => "image/tiff",
            Magic::Wav => "audio/wav",
            Magic::Flac => "audio/flac",
            Magic::Ogg => "audio/ogg",
            Magic::Mp3 => "audio/mpeg",
            Magic::Mp4 => "video/mp4",
            Magic::Webm => "video/webm",
            Magic::Pdf => "application/pdf",
        }
    }

    /// Whether `d` starts as this type does.
    pub fn sniff(self, d: &[u8]) -> bool {
        match self {
            Magic::Jpeg => d.starts_with(&[0xFF, 0xD8, 0xFF]),
            Magic::Png => d.starts_with(b"\x89PNG\r\n\x1a\n"),
            Magic::Gif => d.starts_with(b"GIF87a") || d.starts_with(b"GIF89a"),
            Magic::Webp => d.len() >= 12 && &d[..4] == b"RIFF" && &d[8..12] == b"WEBP",
            Magic::Bmp => d.starts_with(b"BM") && d.len() >= 14,
            Magic::Tiff => d.starts_with(b"II*\0") || d.starts_with(b"MM\0*"),
            Magic::Wav => d.len() >= 12 && &d[..4] == b"RIFF" && &d[8..12] == b"WAVE",
            Magic::Flac => d.starts_with(b"fLaC"),
            Magic::Ogg => d.starts_with(b"OggS"),
            Magic::Mp3 => {
                d.starts_with(b"ID3")
                    || (d.len() >= 2
                        && d[0] == 0xFF
                        && d[1] & 0xE0 == 0xE0
                        && (d[1] >> 3) & 3 != 1
                        && (d[1] >> 1) & 3 != 0)
            }
            Magic::Mp4 => d.len() >= 8 && &d[4..8] == b"ftyp",
            Magic::Webm => d.starts_with(b"\x1a\x45\xdf\xa3"),
            Magic::Pdf => d.starts_with(b"%PDF-"),
        }
    }

    fn from_word(s: &str) -> Option<Magic> {
        Magic::ALL.into_iter().find(|m| m.word() == s)
    }
}

/// The first of `types` the bytes are (None: none of them).
pub fn sniff(types: &[Magic], d: &[u8]) -> Option<Magic> {
    types.iter().copied().find(|m| m.sniff(d))
}

/// One field of an http-multipart route.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Field {
    pub name: String,
    pub file: bool,
    pub types: Vec<Magic>,
    pub required: bool,
}

/// One route of the table.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Route {
    pub method: &'static str,
    pub path: String,
    pub protocol: Protocol,
    pub engine: String,
    pub upstream: String,
    /// the body cap in bytes
    pub max_body: usize,
    /// the wait for the engine's answer, and the longest gap inside it
    pub timeout: Duration,
    /// the answer may be an event stream
    pub stream: bool,
    /// the route's name within its engine (metrics and /health say `<engine>.<key>`)
    pub key: String,
    pub types: Vec<Magic>,
    pub fields: Vec<Field>,
    /// openai-chat only: the route takes the chat requests that carry image parts
    pub images: bool,
}

/// One engine of the table: its id (the component id), its loopback port (None: the board has no such engine the
/// gateway speaks to; its routes answer 501), whether requests carry the runtime's key, its context size.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Engine {
    pub id: String,
    pub port: Option<u16>,
    pub key: bool,
    pub ctx_size: Option<u32>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Table {
    pub engines: Vec<Engine>,
    pub routes: Vec<Route>,
}

type R<T> = Result<T, String>;

fn is_segment(s: &str, lower: bool) -> bool {
    let b = s.as_bytes();
    (1..=64).contains(&b.len())
        && (if lower {
            b[0].is_ascii_lowercase() || b[0].is_ascii_digit()
        } else {
            b[0].is_ascii_alphanumeric() || b[0] == b'_'
        })
        && b.iter().all(|&c| {
            (if lower { c.is_ascii_lowercase() } else { c.is_ascii_alphabetic() })
                || c.is_ascii_digit()
                || b"._-".contains(&c)
        })
}

/// A client path (reach.RE_PATH): /v1/ or /api/ and 1 to 6 segments of lower-case letters, digits, '.', '_', '-'.
pub fn is_path(s: &str) -> bool {
    let Some(rest) = s.strip_prefix("/v1/").or_else(|| s.strip_prefix("/api/")) else { return false };
    let segs: Vec<&str> = rest.split('/').collect();
    (1..=6).contains(&segs.len()) && segs.iter().all(|p| is_segment(p, true))
}

/// An engine's own path (reach.RE_UPSTREAM): 1 to 8 segments of letters, digits, '.', '_', '-', each starting with a
/// letter, a digit or '_'.
pub fn is_upstream(s: &str) -> bool {
    let Some(rest) = s.strip_prefix('/') else { return false };
    let segs: Vec<&str> = rest.split('/').collect();
    (1..=8).contains(&segs.len()) && segs.iter().all(|p| is_segment(p, false))
}

/// A route key (reach.RE_KEY): 1 to 32 lower-case letters, digits and '-', starting with a letter.
pub fn is_key(s: &str) -> bool {
    (1..=32).contains(&s.len())
        && s.as_bytes()[0].is_ascii_lowercase()
        && s.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}

/// A multipart field name (reach.RE_FIELD).
pub fn is_field(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=64).contains(&b.len())
        && (b[0].is_ascii_alphabetic() || b[0] == b'_')
        && b.iter().all(|&c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
}

fn keys(m: &Map<String, Value>, what: &str, need: &[&str], may: &[&str]) -> R<()> {
    for k in m.keys() {
        if !need.contains(&k.as_str()) && !may.contains(&k.as_str()) {
            return Err(format!("{what}: unknown field {}", k.chars().take(40).collect::<String>()));
        }
    }
    for k in need {
        if !m.contains_key(*k) {
            return Err(format!("{what}: {k} missing"));
        }
    }
    Ok(())
}

fn whole(v: &Value, low: u64, high: u64, what: &str) -> R<u64> {
    v.as_u64()
        .filter(|n| (low..=high).contains(n) && !v.is_f64())
        .ok_or_else(|| format!("{what} is from {low} to {high}"))
}

fn text<'a>(m: &'a Map<String, Value>, k: &str, what: &str) -> R<&'a str> {
    m.get(k).and_then(Value::as_str).ok_or_else(|| format!("{what}: {k} is a text"))
}

fn flag(m: &Map<String, Value>, k: &str, what: &str) -> R<bool> {
    m.get(k).and_then(Value::as_bool).ok_or_else(|| format!("{what}: {k} is true or false"))
}

fn types(v: &Value, what: &str) -> R<Vec<Magic>> {
    let list = v.as_array().filter(|l| (1..=MAX_TYPES).contains(&l.len())).ok_or(format!("{what}: types is a list"))?;
    let mut out: Vec<Magic> = Vec::new();
    for t in list {
        let m = t.as_str().and_then(Magic::from_word).ok_or(format!("{what}: an unknown magic type"))?;
        if out.contains(&m) {
            return Err(format!("{what}: a magic type twice"));
        }
        out.push(m);
    }
    Ok(out)
}

fn fields(v: &Value, what: &str) -> R<Vec<Field>> {
    let list =
        v.as_array().filter(|l| (1..=MAX_FIELDS).contains(&l.len())).ok_or(format!("{what}: fields is a list"))?;
    let mut out: Vec<Field> = Vec::new();
    for f in list {
        let m = f.as_object().ok_or(format!("{what}: a field is an object"))?;
        keys(m, what, &["name"], &["file", "types", "required"])?;
        let name = text(m, "name", what)?;
        if !is_field(name) {
            return Err(format!("{what}: a field name is letters, digits, '_', '.' and '-'"));
        }
        let file = if m.contains_key("file") { flag(m, "file", what)? } else { false };
        let required = if m.contains_key("required") { flag(m, "required", what)? } else { false };
        let t = match m.get("types") {
            None => Vec::new(),
            Some(_) if !file => return Err(format!("{what}: field {name}: only a file field has types")),
            Some(v) => types(v, what)?,
        };
        if out.iter().any(|o| o.name == name) {
            return Err(format!("{what}: a field name twice"));
        }
        out.push(Field { name: name.into(), file, types: t, required });
    }
    Ok(out)
}

fn route(v: &Value) -> R<Route> {
    let m = v.as_object().ok_or("a route is an object")?;
    keys(
        m,
        "route",
        &["path", "method", "protocol", "engine", "upstream_path", "max_body_kb", "timeout_s", "stream", "key"],
        &["types", "fields", "match"],
    )?;
    let path = text(m, "path", "route")?;
    if !is_path(path) {
        return Err("route path is /v1/... or /api/... (lower-case letters, digits, '.', '_', '-')".into());
    }
    if RESERVED.contains(&path) || RESERVED_PREFIXES.iter().any(|p| path == *p || path.starts_with(&format!("{p}/"))) {
        return Err(format!("route {path}: a path of the gateway's own"));
    }
    let what = format!("route {path}");
    let word = text(m, "protocol", &what)?;
    let protocol =
        Protocol::ALL.into_iter().find(|p| p.word() == word).ok_or(format!("{what}: an unknown protocol"))?;
    let method = text(m, "method", &what)?;
    let method = *protocol.methods().iter().find(|x| **x == method).ok_or(format!(
        "{what}: protocol {} takes {}",
        protocol.word(),
        protocol.methods().join(" or ")
    ))?;
    let engine = text(m, "engine", &what)?;
    if !crate::engines::is_engine_id(engine) {
        return Err(format!("{what}: engine is a component id"));
    }
    let upstream = text(m, "upstream_path", &what)?;
    if !is_upstream(upstream) {
        return Err(format!("{what}: upstream_path is a plain absolute path"));
    }
    let max_body = whole(&m["max_body_kb"], 1, MAX_BODY_KB, &format!("{what}: max_body_kb"))? as usize * 1024;
    let timeout = Duration::from_secs(whole(&m["timeout_s"], 1, MAX_TIMEOUT_S, &format!("{what}: timeout_s"))?);
    let stream = flag(m, "stream", &what)?;
    if protocol == Protocol::Sse && !stream {
        return Err(format!("{what}: an http-sse route streams"));
    }
    if stream && !protocol.may_stream() {
        return Err(format!("{what}: protocol {} answers no stream", protocol.word()));
    }
    let key = text(m, "key", &what)?;
    if !is_key(key) {
        return Err(format!("{what}: key is a short word"));
    }
    let t = match (m.get("types"), protocol) {
        (Some(v), Protocol::Binary) => types(v, &what)?,
        (None, Protocol::Binary) => return Err(format!("{what}: an http-binary route names its magic types")),
        (Some(_), _) => return Err(format!("{what}: only an http-binary route has types")),
        (None, _) => Vec::new(),
    };
    let f = match (m.get("fields"), protocol) {
        (Some(v), Protocol::Multipart) => fields(v, &what)?,
        (None, Protocol::Multipart) => return Err(format!("{what}: an http-multipart route names its fields")),
        (Some(_), _) => return Err(format!("{what}: only an http-multipart route has fields")),
        (None, _) => Vec::new(),
    };
    let images = match m.get("match") {
        None => false,
        Some(Value::String(s)) if s == "images" && protocol == Protocol::Chat => true,
        Some(_) => return Err(format!("{what}: match is images, on an openai-chat route only")),
    };
    Ok(Route {
        method,
        path: path.into(),
        protocol,
        engine: engine.into(),
        upstream: upstream.into(),
        max_body,
        timeout,
        stream,
        key: key.into(),
        types: t,
        fields: f,
        images,
    })
}

fn engine(v: &Value) -> R<Engine> {
    let m = v.as_object().ok_or("an engine is an object")?;
    keys(m, "engine", &["id", "port", "key"], &["ctx_size"])?;
    let id = text(m, "id", "engine")?;
    if !crate::engines::is_engine_id(id) {
        return Err("an engine id is a component id (lower-case letters, digits and '-')".into());
    }
    let what = format!("engine {id}");
    let port = whole(&m["port"], 1024, 65535, &format!("{what}: port"))? as u16;
    let key = flag(m, "key", &what)?;
    let ctx_size = match m.get("ctx_size") {
        None => None,
        Some(v) => Some(whole(v, 256, 1 << 20, &format!("{what}: ctx_size"))? as u32),
    };
    Ok(Engine { id: id.into(), port: Some(port), key, ctx_size })
}

impl Table {
    /// The table from its file's bytes (see the module text).
    pub fn parse(data: &[u8]) -> R<Table> {
        if data.len() > MAX_TABLE_BYTES {
            return Err("the route table is larger than 64 KiB".into());
        }
        let v: Value = serde_json::from_slice(data).map_err(|_| "the route table is no JSON".to_string())?;
        let m = v.as_object().ok_or("the route table is an object")?;
        keys(m, "the route table", &["schema", "engines", "routes"], &[])?;
        if m["schema"].as_str() != Some(SCHEMA) {
            return Err(format!("the route table's schema must be {SCHEMA}"));
        }
        let engines = m["engines"]
            .as_array()
            .filter(|l| (1..=MAX_ENGINES).contains(&l.len()))
            .ok_or(format!("engines is a list of 1 to {MAX_ENGINES}"))?
            .iter()
            .map(engine)
            .collect::<R<Vec<Engine>>>()?;
        for (i, e) in engines.iter().enumerate() {
            if engines[..i].iter().any(|o| o.id == e.id) {
                return Err("an engine id twice".into());
            }
            if engines[..i].iter().any(|o| o.port == e.port) {
                return Err("an engine port twice".into());
            }
        }
        let routes = m["routes"]
            .as_array()
            .filter(|l| (1..=MAX_ROUTES).contains(&l.len()))
            .ok_or(format!("routes is a list of 1 to {MAX_ROUTES}"))?
            .iter()
            .map(route)
            .collect::<R<Vec<Route>>>()?;
        let table = Table { engines, routes };
        table.check()?;
        Ok(table)
    }

    /// The rules across routes: every engine known, no method, path and match twice, no key twice in one engine, an
    /// images route next to the plain openai-chat route on its path.
    fn check(&self) -> R<()> {
        for (i, r) in self.routes.iter().enumerate() {
            if self.engine(&r.engine).is_none() {
                return Err(format!("route {}: engine {} is none of the table's engines", r.path, r.engine));
            }
            let before = &self.routes[..i];
            if before.iter().any(|o| o.method == r.method && o.path == r.path && o.images == r.images) {
                return Err(format!("route {} {} is declared twice", r.method, r.path));
            }
            if before.iter().any(|o| o.engine == r.engine && o.key == r.key) {
                return Err(format!("{}: the route key {} twice", r.engine, r.key));
            }
        }
        for r in self.routes.iter().filter(|r| r.images) {
            let plain = self.routes.iter().find(|o| !o.images && o.method == r.method && o.path == r.path);
            if plain.is_none_or(|p| p.protocol != Protocol::Chat) {
                return Err(format!("route {} (images) needs the plain openai-chat route on the same path", r.path));
            }
        }
        Ok(())
    }

    /// The table the gateway always served, from its options: the llm's routes, the speech route to the stt engine
    /// (its port --stt-port; without one it answers 501) and, with --vision-port, the chats with images to vision.
    pub fn from_args(cfg: &Serve) -> Table {
        use crate::engines::{LLM, STT, VISION};
        let json = cfg.max_body_kb as usize * 1024;
        let wait = Duration::from_secs(DEFAULT_TIMEOUT_S);
        let r = |method, path: &str, protocol, engine: &str, upstream: &str, max_body, stream, key: &str| Route {
            method,
            path: path.into(),
            protocol,
            engine: engine.into(),
            upstream: upstream.into(),
            max_body,
            timeout: wait,
            stream,
            key: key.into(),
            types: Vec::new(),
            fields: Vec::new(),
            images: false,
        };
        let chat = "/v1/chat/completions";
        let mut routes = vec![
            r("GET", "/v1/models", Protocol::Models, LLM, "/v1/models", json, false, "models"),
            r("POST", chat, Protocol::Chat, LLM, chat, json, true, "chat"),
            r("POST", "/v1/completions", Protocol::Completions, LLM, "/v1/completions", json, true, "completions"),
            r(
                "POST",
                "/v1/audio/transcriptions",
                Protocol::Transcription,
                STT,
                "/inference",
                cfg.audio_body_cap(),
                false,
                "transcriptions",
            ),
        ];
        let mut engines = vec![
            Engine { id: LLM.into(), port: Some(cfg.upstream_port), key: true, ctx_size: Some(cfg.ctx_size) },
            Engine { id: STT.into(), port: cfg.stt_port, key: false, ctx_size: None },
        ];
        if cfg.vision_port.is_some() {
            engines.push(Engine {
                id: VISION.into(),
                port: cfg.vision_port,
                key: true,
                ctx_size: Some(cfg.vision_ctx_size),
            });
            routes.push(Route {
                images: true,
                ..r("POST", chat, Protocol::Chat, VISION, chat, json, true, "chat-images")
            });
        }
        Table { engines, routes }
    }

    pub fn engine(&self, id: &str) -> Option<&Engine> {
        self.engines.iter().find(|e| e.id == id)
    }

    /// The plain route of a method and path (its place in the table).
    pub fn find(&self, method: &str, path: &str) -> Option<usize> {
        self.routes.iter().position(|r| !r.images && r.method == method && r.path == path)
    }

    /// The route that takes the chats with images on `path` (its place in the table).
    pub fn images_route(&self, path: &str) -> Option<usize> {
        self.routes.iter().position(|r| r.images && r.path == path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const VECTORS: &str = include_str!("../../../tests/data/gateway_routes_vectors.json");

    /// A patch: {"path": ["routes", 0, "key"], "value": ...} (no "value": the key is removed).
    fn apply(doc: &mut Value, patch: &Value) {
        let path = patch["path"].as_array().unwrap();
        let mut at = doc;
        for (i, step) in path.iter().enumerate() {
            let last = i == path.len() - 1;
            match (step, last, patch.get("value")) {
                (Value::String(k), true, Some(v)) => {
                    at.as_object_mut().unwrap().insert(k.clone(), v.clone());
                }
                (Value::String(k), true, None) => {
                    at.as_object_mut().unwrap().remove(k);
                }
                (Value::Number(n), true, Some(v)) => at[n.as_u64().unwrap() as usize] = v.clone(),
                (Value::String(k), false, _) => at = at.get_mut(k.as_str()).unwrap(),
                (Value::Number(n), false, _) => at = at.get_mut(n.as_u64().unwrap() as usize).unwrap(),
                _ => panic!("bad patch"),
            }
        }
    }

    fn patched(case: &Value) -> Vec<u8> {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        let mut doc = v["tables"]["base"].clone();
        for p in case["patches"].as_array().unwrap() {
            apply(&mut doc, p);
        }
        serde_json::to_vec(&doc).unwrap()
    }

    #[test]
    fn the_shared_route_table_vectors_hold() {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        let accept = v["tables"]["accept"].as_array().unwrap();
        let reject = v["tables"]["reject"].as_array().unwrap();
        assert!(accept.len() >= 8 && reject.len() >= 40);
        for case in accept {
            let got = Table::parse(&patched(case));
            assert!(got.is_ok(), "should take: {} ({:?})", case["why"], got.err());
        }
        for case in reject {
            assert!(Table::parse(&patched(case)).is_err(), "should refuse: {}", case["why"]);
        }
    }

    #[test]
    fn the_base_table_reads_as_its_routes_and_engines() {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        let t = Table::parse(&serde_json::to_vec(&v["tables"]["base"]).unwrap()).unwrap();
        assert_eq!(t.find("POST", "/v1/chat/completions"), Some(1));
        assert_eq!(t.images_route("/v1/chat/completions"), Some(2));
        assert_eq!(t.routes[2].engine, "vision");
        assert_eq!(t.find("GET", "/v1/chat/completions"), None);
        let detect = &t.routes[t.find("POST", "/api/detect").unwrap()];
        assert_eq!(
            (detect.protocol, detect.max_body, detect.timeout),
            (Protocol::Multipart, 8192 * 1024, Duration::from_secs(60))
        );
        assert_eq!(detect.fields[0].types, vec![Magic::Jpeg, Magic::Png]);
        assert_eq!(t.engine("stt").unwrap().port, Some(8090));
        assert!(t.engine("llm").unwrap().key && !t.engine("detect").unwrap().key);
    }

    #[test]
    fn the_table_is_capped_and_strict() {
        assert!(Table::parse(&vec![b' '; MAX_TABLE_BYTES + 1]).is_err());
        assert!(Table::parse(b"{\"schema\":").is_err());
        assert!(Table::parse(b"[]").is_err());
        // a float, a negative number and a huge number are no whole numbers of the range
        for bad in ["1.5", "-1", "18446744073709551616"] {
            let v: Value = serde_json::from_str(VECTORS).unwrap();
            let text = serde_json::to_string(&v["tables"]["base"]).unwrap().replacen(
                "\"timeout_s\":900",
                &format!("\"timeout_s\":{bad}"),
                1,
            );
            assert!(Table::parse(text.as_bytes()).is_err(), "{bad}");
        }
    }

    #[test]
    fn magic_types_by_their_first_bytes() {
        assert_eq!(sniff(&[Magic::Png, Magic::Jpeg], &[0xFF, 0xD8, 0xFF, 0xE0]), Some(Magic::Jpeg));
        assert_eq!(sniff(&[Magic::Png], b"\x89PNG\r\n\x1a\n...."), Some(Magic::Png));
        assert_eq!(sniff(&[Magic::Png], b"GIF89a"), None);
        assert!(Magic::Webp.sniff(b"RIFF\0\0\0\0WEBPVP8 "));
        assert!(!Magic::Wav.sniff(b"RIFF\0\0\0\0WEBPVP8 "));
        assert!(Magic::Pdf.sniff(b"%PDF-1.7"));
        assert!(Magic::Mp4.sniff(b"\0\0\0\x18ftypmp42"));
        assert_eq!(Magic::Mp3.mime(), "audio/mpeg");
        for m in Magic::ALL {
            assert_eq!(Magic::from_word(m.word()), Some(m));
        }
    }

    #[test]
    fn the_table_from_the_options_is_the_one_the_gateway_always_served() {
        let cfg = Serve::default();
        let t = Table::from_args(&cfg);
        let rows: Vec<(&str, &str, &str, &str)> =
            t.routes.iter().map(|r| (r.method, r.path.as_str(), r.engine.as_str(), r.upstream.as_str())).collect();
        assert_eq!(
            rows,
            [
                ("GET", "/v1/models", "llm", "/v1/models"),
                ("POST", "/v1/chat/completions", "llm", "/v1/chat/completions"),
                ("POST", "/v1/completions", "llm", "/v1/completions"),
                ("POST", "/v1/audio/transcriptions", "stt", "/inference"),
            ]
        );
        assert_eq!(t.engine("stt").unwrap().port, None, "no speech engine: the route answers 501");
        assert_eq!(t.routes[3].max_body, 25 << 20);
        let with = Table::from_args(&Serve { vision_port: Some(8081), ..Serve::default() });
        assert_eq!(with.images_route("/v1/chat/completions"), Some(4));
        assert!(with.check().is_ok());
    }
}
