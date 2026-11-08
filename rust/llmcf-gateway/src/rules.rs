//! What the gateway checks and saves on every request, as pure functions (no I/O): strict JSON validation of the
//! OpenAI-compatible bodies, the context budget from the board's start parameters, and the token-saving rewrites.
//!
//! - Strict: a body is one JSON object with only the fields listed here, each of its type; messages have a known role
//!   and text content; anything else is refused before it reaches llama-server (400 with a fixed code).
//! - Budget: the prompt is counted with the host's own rough rule (about 3 characters a token, the knowledge base's
//!   CHARS_PER_TOKEN, plus a few tokens per message); a prompt that does not fit the board's context per slot is
//!   refused, and max_tokens is cut to what is left (never above the gateway's own cap).
//! - Savings: a system message whose text an earlier system message already carries is dropped; a tool result byte
//!   for byte the same as an earlier result of the same tool with the same arguments is sent as
//!   "same result as tool call #<id>" (Ditto's rule, tool_loop.ResultIndex, tested with the same vectors in
//!   tests/fixtures/context_rules/duplicate_result.json); cache_prompt is set so llama-server reuses the cached
//!   prefix, and with several slots a request goes to the slot its system prompt hashes to (slot reuse).
//! - Images: with a vision engine, a user message may carry image parts (images.rs: data URLs only, png, jpeg or gif
//!   by their bytes, capped in size and count); they go on unchanged, the request goes to the vision engine and is
//!   counted against its context with a fixed number of tokens per image.

use crate::images::{self, ImageRules, Seen};
use ring::digest::{digest, SHA256};
use serde_json::{Map, Value};
use std::collections::HashMap;

/// The host's rough token rule (orchestrator/rag/embeddings.py CHARS_PER_TOKEN).
pub const CHARS_PER_TOKEN: f64 = 3.0;
/// Tokens a chat template adds around each message (role markers), counted on top of its text.
pub const PER_MESSAGE_TOKENS: u64 = 4;
/// Fewer tokens than this left for the answer: the prompt does not fit.
pub const MIN_ANSWER_TOKENS: u64 = 16;
pub const MAX_MESSAGES: usize = 512;
pub const MAX_TOOLS: usize = 64;
/// tool_loop.DUPLICATE_TEXT
pub const DUPLICATE_TEXT: &str = "same result as tool call #";

#[derive(Clone, Copy, Debug)]
pub struct Limits {
    /// Tokens per slot (the board's ctx_size / slots).
    pub budget: u64,
    /// The gateway's cap on max_tokens.
    pub max_tokens: u64,
    pub slots: u32,
    /// the image parts taken (None: no vision engine, only text parts)
    pub images: Option<ImageRules>,
    /// tokens of the vision engine's context, for a request with images
    pub vision_budget: u64,
}

#[derive(Debug, PartialEq)]
pub struct Refusal {
    pub status: u16,
    pub code: &'static str,
    pub message: String,
}

fn refuse(code: &'static str, message: impl Into<String>) -> Refusal {
    Refusal { status: 400, code, message: message.into() }
}

#[derive(Debug, Default)]
pub struct Outcome {
    pub body: Value,
    pub prompt_tokens: u64,
    pub max_tokens: u64,
    pub system_dropped: u64,
    pub results_pointed: u64,
    pub clamped: bool,
    pub stream: bool,
    /// image parts of the request (it goes to the vision engine then)
    pub images: u32,
    pub image_bytes: u64,
}

/// Rough token count of a text (ceil(chars / CHARS_PER_TOKEN)).
pub fn tokens_of(text: &str) -> u64 {
    let chars = text.chars().count() as f64;
    (chars / CHARS_PER_TOKEN).ceil() as u64
}

const CHAT_FIELDS: &[&str] = &[
    "model",
    "messages",
    "max_tokens",
    "max_completion_tokens",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "stream",
    "stream_options",
    "stop",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "repeat_penalty",
    "response_format",
    "tools",
    "tool_choice",
    "n",
    "cache_prompt",
    "user",
];
const COMPLETION_FIELDS: &[&str] = &[
    "model",
    "prompt",
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "stream",
    "stop",
    "seed",
    "presence_penalty",
    "frequency_penalty",
    "repeat_penalty",
    "n",
    "cache_prompt",
    "user",
];
const NUMBERS: &[&str] =
    &["temperature", "top_p", "top_k", "min_p", "seed", "presence_penalty", "frequency_penalty", "repeat_penalty"];

fn object(body: &[u8]) -> Result<Map<String, Value>, Refusal> {
    let value: Value = serde_json::from_slice(body).map_err(|_| refuse("invalid_json", "the body is no valid JSON"))?;
    match value {
        Value::Object(m) => Ok(m),
        _ => Err(refuse("invalid_json", "the body is no JSON object")),
    }
}

fn check_common(m: &Map<String, Value>, fields: &[&str]) -> Result<(), Refusal> {
    for key in m.keys() {
        if !fields.contains(&key.as_str()) {
            return Err(refuse("unknown_field", format!("the field {} is not taken", short(key))));
        }
    }
    for key in NUMBERS {
        if let Some(v) = m.get(*key) {
            if !v.is_number() {
                return Err(refuse("invalid_value", format!("{key} is a number")));
            }
        }
    }
    if let Some(v) = m.get("model") {
        if !v.as_str().map(|s| s.len() <= 128).unwrap_or(false) {
            return Err(refuse("invalid_value", "model is a short text"));
        }
    }
    if let Some(v) = m.get("user") {
        if !v.as_str().map(|s| s.len() <= 128).unwrap_or(false) {
            return Err(refuse("invalid_value", "user is a short text"));
        }
    }
    for key in ["stream", "cache_prompt"] {
        if let Some(v) = m.get(key) {
            if !v.is_boolean() {
                return Err(refuse("invalid_value", format!("{key} is true or false")));
            }
        }
    }
    if let Some(v) = m.get("n") {
        if v.as_u64() != Some(1) {
            return Err(refuse("invalid_value", "n must be 1 (one answer per request)"));
        }
    }
    if let Some(v) = m.get("stop") {
        let ok = match v {
            Value::String(s) => s.len() <= 256,
            Value::Array(a) => a.len() <= 8 && a.iter().all(|s| s.as_str().map(|s| s.len() <= 256).unwrap_or(false)),
            _ => false,
        };
        if !ok {
            return Err(refuse("invalid_value", "stop is a text or a list of at most 8 texts"));
        }
    }
    Ok(())
}

fn short(s: &str) -> String {
    let clean: String = s.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '_').take(40).collect();
    if clean.is_empty() {
        "(unnamed)".into()
    } else {
        clean
    }
}

/// The text of a message's content: a string, or a list of {"type":"text","text":...} parts; in a user message with
/// a vision engine also {"type":"image_url","image_url":{"url":"data:...","detail":...}} parts (counted in `seen`).
fn content_text(v: &Value, images: Option<&ImageRules>, seen: &mut Seen) -> Result<String, Refusal> {
    match v {
        Value::String(s) => Ok(s.clone()),
        Value::Null => Ok(String::new()),
        Value::Array(parts) => {
            let mut out = String::new();
            for p in parts {
                let Some(o) = p.as_object() else {
                    return Err(refuse("invalid_message", "a content part is an object"));
                };
                let kind = o.get("type").and_then(Value::as_str);
                if kind == Some("image_url") {
                    let Some(rules) = images else {
                        return Err(refuse(
                            "unsupported_content",
                            "the board runs no vision engine (block vision-mmproj): only text content parts are taken",
                        ));
                    };
                    image_part(o, rules, seen)?;
                    continue;
                }
                if kind != Some("text") {
                    return Err(refuse("unsupported_content", "a content part is text or, for vision, image_url"));
                }
                if o.keys().any(|k| k != "type" && k != "text") {
                    return Err(refuse("invalid_message", "a text part has only type and text"));
                }
                out.push_str(o.get("text").and_then(Value::as_str).ok_or_else(|| refuse("invalid_message", "text"))?);
            }
            Ok(out)
        }
        _ => Err(refuse("invalid_message", "content is a text or a list of text parts")),
    }
}

/// One image part: only its type and image_url, the URL a checked data URL (images.rs), detail auto, low or high.
fn image_part(o: &Map<String, Value>, rules: &ImageRules, seen: &mut Seen) -> Result<(), Refusal> {
    if o.keys().any(|k| k != "type" && k != "image_url") {
        return Err(refuse("invalid_message", "an image part has only type and image_url"));
    }
    let Some(iu) = o.get("image_url").and_then(Value::as_object) else {
        return Err(refuse("invalid_message", "image_url is an object with its url"));
    };
    if iu.keys().any(|k| k != "url" && k != "detail") {
        return Err(refuse("invalid_message", "image_url has only url and detail"));
    }
    if let Some(d) = iu.get("detail") {
        if !matches!(d.as_str(), Some("auto" | "low" | "high")) {
            return Err(refuse("invalid_value", "detail is auto, low or high"));
        }
    }
    let url = iu.get("url").and_then(Value::as_str).ok_or_else(|| refuse("invalid_message", "image_url has a url"))?;
    images::check_url(url, rules, seen)
}

/// One tool call of an assistant message: (id, name, arguments as canonical JSON text).
fn tool_calls(v: &Value) -> Result<Vec<(String, String, String)>, Refusal> {
    let Some(list) = v.as_array() else { return Err(refuse("invalid_message", "tool_calls is a list")) };
    let mut out = Vec::new();
    for c in list {
        let o = c.as_object().ok_or_else(|| refuse("invalid_message", "a tool call is an object"))?;
        if o.keys().any(|k| !matches!(k.as_str(), "id" | "type" | "function" | "index")) {
            return Err(refuse("invalid_message", "a tool call has id, type and function"));
        }
        let id =
            o.get("id").and_then(Value::as_str).ok_or_else(|| refuse("invalid_message", "a tool call has an id"))?;
        let f = o.get("function").and_then(Value::as_object).ok_or_else(|| refuse("invalid_message", "function"))?;
        let name = f.get("name").and_then(Value::as_str).unwrap_or("");
        let args = f.get("arguments").and_then(Value::as_str).unwrap_or("{}");
        // json_dumps of the parsed arguments (sorted keys, no spaces); text that is no JSON stays as it is
        let canon = serde_json::from_str::<Value>(args).map(|v| v.to_string()).unwrap_or_else(|_| args.to_string());
        out.push((id.to_string(), name.to_string(), canon));
    }
    Ok(out)
}

/// utils/untrusted.without_boundary_ids: the random 12-hex id of every `<untrusted_data id="...">` and
/// `</untrusted_data id="...">` boundary taken out.
pub fn without_boundary_ids(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    loop {
        let open = rest.find("<untrusted_data id=\"");
        let close = rest.find("</untrusted_data id=\"");
        let (at, len) = match (open, close) {
            (Some(a), Some(b)) if b < a => (b, "</untrusted_data id=\"".len()),
            (Some(a), _) => (a, "<untrusted_data id=\"".len()),
            (None, Some(b)) => (b, "</untrusted_data id=\"".len()),
            (None, None) => break,
        };
        let head = at + len;
        out.push_str(&rest[..head]);
        let tail = &rest[head..];
        let hex = tail.as_bytes().iter().take(12).all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(c));
        if tail.len() >= 13 && hex && tail.as_bytes()[12] == b'"' {
            rest = &tail[12..];
        } else {
            rest = tail;
        }
    }
    out.push_str(rest);
    out
}

/// tool_loop.result_key: the tool, its canonical arguments and the sha256 of the text without boundary ids.
pub fn result_key(name: &str, args: &str, text: &str) -> (String, String, Vec<u8>) {
    let d = digest(&SHA256, without_boundary_ids(text).as_bytes());
    (name.to_string(), args.to_string(), d.as_ref().to_vec())
}

/// tool_loop.ResultIndex: the successful results seen, by key, each with the call that first returned it.
#[derive(Default)]
pub struct ResultIndex {
    seen: HashMap<(String, String, Vec<u8>), String>,
}

impl ResultIndex {
    /// Indexes a result already in the request (an error result is never pointed at). The gateway sees every result
    /// of a request as new (OpenAI's format has no error mark); the shared vectors use it.
    #[cfg_attr(not(test), allow(dead_code))]
    pub fn present(&mut self, id: &str, name: &str, args: &str, text: &str, is_error: bool) {
        if !is_error {
            self.seen.entry(result_key(name, args, text)).or_insert_with(|| id.to_string());
        }
    }

    /// What goes to the model for a new result.
    pub fn sent(&mut self, id: &str, name: &str, args: &str, text: &str) -> String {
        let key = result_key(name, args, text);
        if let Some(orig) = self.seen.get(&key) {
            if orig != id {
                return format!("{DUPLICATE_TEXT}{orig}");
            }
        }
        self.seen.entry(key).or_insert_with(|| id.to_string());
        text.to_string()
    }
}

fn fnv1a(text: &str) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for b in text.bytes() {
        h ^= b as u64;
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    h
}

fn answer_tokens(m: &Map<String, Value>, keys: &[&str], prompt: u64, lim: &Limits) -> Result<(u64, bool), Refusal> {
    let mut asked = None;
    for key in keys {
        if let Some(v) = m.get(*key) {
            asked = Some(v.as_u64().filter(|n| *n >= 1).ok_or_else(|| refuse("invalid_value", "max_tokens"))?);
        }
    }
    let room = lim.budget.saturating_sub(prompt);
    if room < MIN_ANSWER_TOKENS {
        return Err(refuse(
            "context_exceeded",
            format!("the prompt takes about {prompt} tokens; the board's context holds {} per request", lim.budget),
        ));
    }
    let want = asked.unwrap_or(lim.max_tokens);
    let n = want.min(lim.max_tokens).min(room);
    Ok((n, n < want))
}

/// A chat completion request: checked, rewritten and counted (see the module text).
pub fn chat(body: &[u8], lim: &Limits) -> Result<Outcome, Refusal> {
    let mut m = object(body)?;
    check_common(&m, CHAT_FIELDS)?;
    if let Some(t) = m.get("tools") {
        let ok = t.as_array().map(|a| a.len() <= MAX_TOOLS && a.iter().all(Value::is_object)).unwrap_or(false);
        if !ok {
            return Err(refuse("invalid_value", format!("tools is a list of at most {MAX_TOOLS} objects")));
        }
    }
    let messages = match m.remove("messages") {
        Some(Value::Array(a)) if !a.is_empty() && a.len() <= MAX_MESSAGES => a,
        _ => return Err(refuse("invalid_value", format!("messages is a list of 1 to {MAX_MESSAGES} messages"))),
    };
    let mut out = Vec::with_capacity(messages.len());
    let mut systems: Vec<String> = Vec::new();
    let mut calls: HashMap<String, (String, String)> = HashMap::new();
    let mut index = ResultIndex::default();
    let mut o = Outcome::default();
    let mut tokens: u64 = 0;
    let mut first_system: Option<String> = None;
    let mut seen = Seen::default();
    for msg in messages {
        let Value::Object(mut mo) = msg else { return Err(refuse("invalid_message", "a message is an object")) };
        let role = mo.get("role").and_then(Value::as_str).unwrap_or("").to_string();
        let allowed: &[&str] = match role.as_str() {
            "system" | "user" => &["role", "content", "name"],
            "assistant" => &["role", "content", "name", "tool_calls", "reasoning_content"],
            "tool" => &["role", "content", "tool_call_id", "name"],
            _ => return Err(refuse("invalid_message", "a message's role is system, user, assistant or tool")),
        };
        if let Some(k) = mo.keys().find(|k| !allowed.contains(&k.as_str())) {
            return Err(refuse("invalid_message", format!("a {role} message has no field {}", short(k))));
        }
        if let Some(n) = mo.get("name") {
            if !n.as_str().map(|s| s.len() <= 64).unwrap_or(false) {
                return Err(refuse("invalid_message", "name is a short text"));
            }
        }
        // images only in a user's message
        let rules = if role == "user" { lim.images.as_ref() } else { None };
        let text = content_text(mo.get("content").unwrap_or(&Value::Null), rules, &mut seen)?;
        match role.as_str() {
            "system" => {
                if systems.contains(&text) {
                    o.system_dropped += 1;
                    continue;
                }
                if first_system.is_none() {
                    first_system = Some(text.clone());
                }
                systems.push(text.clone());
            }
            "assistant" => {
                if let Some(tc) = mo.get("tool_calls") {
                    for (id, name, args) in tool_calls(tc)? {
                        tokens += tokens_of(&name) + tokens_of(&args);
                        calls.insert(id, (name, args));
                    }
                }
                if let Some(r) = mo.get("reasoning_content") {
                    tokens += tokens_of(r.as_str().unwrap_or(""));
                }
            }
            "tool" => {
                let id = mo.get("tool_call_id").and_then(Value::as_str).unwrap_or("").to_string();
                if let Some((name, args)) = calls.get(&id) {
                    let sent = index.sent(&id, name, args, &text);
                    if sent != text {
                        o.results_pointed += 1;
                        tokens += PER_MESSAGE_TOKENS + tokens_of(&sent);
                        mo.insert("content".into(), Value::String(sent));
                        out.push(Value::Object(mo));
                        continue;
                    }
                }
            }
            _ => {}
        }
        tokens += PER_MESSAGE_TOKENS + tokens_of(&text);
        out.push(Value::Object(mo));
    }
    if let Some(t) = m.get("tools") {
        tokens += tokens_of(&t.to_string());
    }
    // a request with images goes to the vision engine: its own context, each image a fixed number of tokens
    let mut lim = *lim;
    if seen.count > 0 {
        tokens += seen.count as u64 * lim.images.map(|r| r.tokens).unwrap_or(0);
        lim.budget = lim.vision_budget;
        lim.slots = 1;
    }
    let lim = &lim;
    let (n, clamped) = answer_tokens(&m, &["max_tokens", "max_completion_tokens"], tokens, lim)?;
    m.remove("max_completion_tokens");
    m.insert("max_tokens".into(), Value::from(n));
    m.insert("messages".into(), Value::Array(out));
    m.insert("cache_prompt".into(), Value::Bool(true));
    if lim.slots > 1 {
        let anchor = first_system.unwrap_or_default();
        m.insert("id_slot".into(), Value::from(fnv1a(&anchor) % lim.slots as u64));
    }
    o.stream = m.get("stream").and_then(Value::as_bool).unwrap_or(false);
    o.prompt_tokens = tokens;
    o.max_tokens = n;
    o.clamped = clamped;
    o.images = seen.count;
    o.image_bytes = seen.bytes;
    o.body = Value::Object(m);
    Ok(o)
}

/// A plain completion request (`prompt` one text).
pub fn completion(body: &[u8], lim: &Limits) -> Result<Outcome, Refusal> {
    let mut m = object(body)?;
    check_common(&m, COMPLETION_FIELDS)?;
    let prompt = m.get("prompt").and_then(Value::as_str).ok_or_else(|| refuse("invalid_value", "prompt is a text"))?;
    let tokens = tokens_of(prompt);
    let (n, clamped) = answer_tokens(&m, &["max_tokens"], tokens, lim)?;
    m.insert("max_tokens".into(), Value::from(n));
    m.insert("cache_prompt".into(), Value::Bool(true));
    let stream = m.get("stream").and_then(Value::as_bool).unwrap_or(false);
    Ok(Outcome { body: Value::Object(m), prompt_tokens: tokens, max_tokens: n, clamped, stream, ..Outcome::default() })
}

const EMBEDDING_FIELDS: &[&str] = &["model", "input", "encoding_format", "dimensions", "user"];
/// Inputs of one embeddings request.
pub const MAX_EMBEDDING_INPUTS: usize = 2048;
const SPEECH_FIELDS: &[&str] = &["model", "input", "voice", "response_format", "speed", "instructions", "user"];
/// The longest text of one speech request (OpenAI's limit).
pub const MAX_SPEECH_INPUT: usize = 4096;
const SPEECH_FORMATS: &[&str] = &["mp3", "opus", "aac", "flac", "wav", "pcm"];

/// An embeddings request (`POST /v1/embeddings`, OpenAI's fields only): `input` one text or a list of 1 to
/// MAX_EMBEDDING_INPUTS texts, `encoding_format` float or base64, `dimensions` a whole number. The checked body.
pub fn embeddings(body: &[u8]) -> Result<Value, Refusal> {
    let m = object(body)?;
    check_common(&m, EMBEDDING_FIELDS)?;
    let ok = match m.get("input") {
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => {
            (1..=MAX_EMBEDDING_INPUTS).contains(&a.len()) && a.iter().all(|v| v.as_str().is_some_and(|s| !s.is_empty()))
        }
        _ => false,
    };
    if !ok {
        return Err(refuse("invalid_value", format!("input is a text or a list of 1 to {MAX_EMBEDDING_INPUTS} texts")));
    }
    if let Some(v) = m.get("encoding_format") {
        if !matches!(v.as_str(), Some("float" | "base64")) {
            return Err(refuse("invalid_value", "encoding_format is float or base64"));
        }
    }
    if let Some(v) = m.get("dimensions") {
        if !v.as_u64().is_some_and(|n| (1..=65_536).contains(&n)) {
            return Err(refuse("invalid_value", "dimensions is a whole number from 1 to 65536"));
        }
    }
    Ok(Value::Object(m))
}

/// A speech request (`POST /v1/audio/speech`, OpenAI's fields only): `input` a text of 1 to MAX_SPEECH_INPUT
/// characters, `voice` a plain word, `response_format` one of OpenAI's audio formats, `speed` 0.25 to 4. The checked
/// body; the engine answers the audio itself.
pub fn speech(body: &[u8]) -> Result<Value, Refusal> {
    let m = object(body)?;
    check_common(&m, SPEECH_FIELDS)?;
    let input = m.get("input").and_then(Value::as_str).unwrap_or("");
    if input.is_empty() || input.chars().count() > MAX_SPEECH_INPUT {
        return Err(refuse("invalid_value", format!("input is a text of 1 to {MAX_SPEECH_INPUT} characters")));
    }
    if let Some(v) = m.get("voice") {
        let word = v.as_str().is_some_and(|s| {
            (1..=64).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(&c))
        });
        if !word {
            return Err(refuse("invalid_value", "voice is a plain word"));
        }
    }
    if let Some(v) = m.get("response_format") {
        if !v.as_str().is_some_and(|f| SPEECH_FORMATS.contains(&f)) {
            return Err(refuse("invalid_value", "response_format is mp3, opus, aac, flac, wav or pcm"));
        }
    }
    if let Some(v) = m.get("speed") {
        if !v.as_f64().is_some_and(|x| (0.25..=4.0).contains(&x)) {
            return Err(refuse("invalid_value", "speed is a number from 0.25 to 4"));
        }
    }
    if let Some(v) = m.get("instructions") {
        if !v.as_str().is_some_and(|s| s.chars().count() <= MAX_SPEECH_INPUT) {
            return Err(refuse("invalid_value", "instructions is a short text"));
        }
    }
    Ok(Value::Object(m))
}

/// A generic JSON route's body (http-json, http-sse): one JSON object, nothing else; the engine reads its fields.
pub fn json_object(body: &[u8]) -> Result<(), Refusal> {
    object(body).map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn embeddings_and_speech_take_their_fields_only() {
        assert!(embeddings(br#"{"model":"m","input":"hi"}"#).is_ok());
        assert!(embeddings(br#"{"input":["a","b"],"encoding_format":"base64","dimensions":64}"#).is_ok());
        for (body, code) in [
            (&br#"{"input":""}"#[..], "invalid_value"),
            (br#"{"input":[]}"#, "invalid_value"),
            (br#"{"input":[1,2]}"#, "invalid_value"),
            (br#"{"input":"x","encoding_format":"int8"}"#, "invalid_value"),
            (br#"{"input":"x","dimensions":0}"#, "invalid_value"),
            (br#"{"input":"x","extra":1}"#, "unknown_field"),
            (b"[1]", "invalid_json"),
        ] {
            assert_eq!(embeddings(body).unwrap_err().code, code, "{}", String::from_utf8_lossy(body));
        }
        assert!(speech(
            br#"{"model":"m","input":"Hallo","voice":"de_DE-thorsten","response_format":"wav","speed":1.5}"#
        )
        .is_ok());
        for body in [
            &br#"{"voice":"x"}"#[..],
            br#"{"input":"x","voice":"a b"}"#,
            br#"{"input":"x","response_format":"exe"}"#,
            br#"{"input":"x","speed":9}"#,
            br#"{"input":"x","stream":"yes"}"#,
        ] {
            assert!(speech(body).is_err(), "{}", String::from_utf8_lossy(body));
        }
        assert!(speech(format!(r#"{{"input":"{}"}}"#, "a".repeat(MAX_SPEECH_INPUT + 1)).as_bytes()).is_err());
        assert!(json_object(br#"{"any":[1,{"x":null}]}"#).is_ok());
        assert_eq!(json_object(b"[]").unwrap_err().code, "invalid_json");
        assert_eq!(json_object(b"{\"a\":1} trailing").unwrap_err().code, "invalid_json");
    }

    const LIM: Limits = Limits { budget: 4096, max_tokens: 512, slots: 1, images: None, vision_budget: 0 };

    fn run(v: Value) -> Result<Outcome, Refusal> {
        chat(v.to_string().as_bytes(), &LIM)
    }

    #[test]
    fn a_plain_request_goes_through_with_cache_prompt_and_max_tokens() {
        let o = run(json!({"messages": [{"role": "user", "content": "hi"}]})).unwrap();
        assert_eq!(o.body["cache_prompt"], json!(true));
        assert_eq!(o.body["max_tokens"], json!(512));
        assert!(o.body.get("id_slot").is_none());
        assert_eq!(o.prompt_tokens, PER_MESSAGE_TOKENS + 1);
    }

    #[test]
    fn strict_validation_refuses_what_it_does_not_know() {
        let cases = [
            (json!([1, 2]), "invalid_json"),
            (json!({"messages": []}), "invalid_value"),
            (json!({"messages": [{"role": "user", "content": "x"}], "tools_extra": 1}), "unknown_field"),
            (json!({"messages": [{"role": "root", "content": "x"}]}), "invalid_message"),
            (json!({"messages": [{"role": "user", "content": "x", "extra": 1}]}), "invalid_message"),
            (json!({"messages": [{"role": "user", "content": [{"type": "image_url"}]}]}), "unsupported_content"),
            (json!({"messages": [{"role": "user", "content": [{"type": "video"}]}]}), "unsupported_content"),
            (json!({"messages": [{"role": "user", "content": "x"}], "n": 2}), "invalid_value"),
            (json!({"messages": [{"role": "user", "content": "x"}], "temperature": "hot"}), "invalid_value"),
            (json!({"messages": [{"role": "user", "content": "x"}], "id_slot": 3}), "unknown_field"),
        ];
        for (body, code) in cases {
            assert_eq!(run(body.clone()).unwrap_err().code, code, "{body}");
        }
        assert_eq!(chat(b"{not json", &LIM).unwrap_err().code, "invalid_json");
    }

    #[test]
    fn a_repeated_system_prompt_is_sent_once() {
        let o = run(json!({"messages": [
            {"role": "system", "content": "You help."},
            {"role": "user", "content": "a"},
            {"role": "system", "content": "You help."},
            {"role": "system", "content": "Other."}]}))
        .unwrap();
        assert_eq!(o.system_dropped, 1);
        assert_eq!(o.body["messages"].as_array().unwrap().len(), 3);
    }

    #[test]
    fn the_budget_cuts_max_tokens_and_refuses_a_prompt_that_does_not_fit() {
        let lim = Limits { budget: 100, ..LIM };
        let o = chat(
            json!({"messages": [{"role": "user", "content": "x".repeat(150)}], "max_tokens": 400})
                .to_string()
                .as_bytes(),
            &lim,
        )
        .unwrap();
        assert_eq!(o.max_tokens, 100 - 54);
        assert!(o.clamped);
        let e = chat(json!({"messages": [{"role": "user", "content": "x".repeat(400)}]}).to_string().as_bytes(), &lim)
            .unwrap_err();
        assert_eq!(e.code, "context_exceeded");
        let o = chat(
            json!({"messages": [{"role": "user", "content": "x"}], "max_completion_tokens": 20}).to_string().as_bytes(),
            &lim,
        )
        .unwrap();
        assert_eq!(o.body["max_tokens"], json!(20));
        assert!(o.body.get("max_completion_tokens").is_none());
    }

    #[test]
    fn several_slots_pick_the_slot_of_the_system_prompt() {
        let lim = Limits { budget: 2048, max_tokens: 64, slots: 4, ..LIM };
        let body = json!({"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}]});
        let a = chat(body.to_string().as_bytes(), &lim).unwrap();
        let b = chat(body.to_string().as_bytes(), &lim).unwrap();
        assert_eq!(a.body["id_slot"], b.body["id_slot"]);
        assert!(a.body["id_slot"].as_u64().unwrap() < 4);
    }

    #[test]
    fn image_parts_need_the_vision_engine_and_are_counted_against_its_context() {
        use crate::images::tests::{png, url, RULES};
        let img = |u: String| json!({"type": "image_url", "image_url": {"url": u, "detail": "low"}});
        let body = |parts: Vec<Value>| {
            json!({"messages": [{"role": "user", "content": parts}], "max_tokens": 4000}).to_string().into_bytes()
        };
        let one = body(vec![json!({"type": "text", "text": "what is it?"}), img(url("image/png", &png(64)))]);
        // without a vision engine an image part is refused
        assert_eq!(chat(&one, &LIM).unwrap_err().code, "unsupported_content");
        let lim = Limits { images: Some(RULES), vision_budget: 2048, slots: 4, ..LIM };
        let o = chat(&one, &lim).unwrap();
        assert_eq!((o.images, o.image_bytes), (1, 64));
        assert_eq!(o.prompt_tokens, PER_MESSAGE_TOKENS + 4 + 768, "text plus the fixed tokens of the image");
        assert_eq!(o.max_tokens, 512.min(2048 - o.prompt_tokens), "the vision engine's budget");
        assert!(o.body.get("id_slot").is_none(), "the llm's slots are not the vision engine's");
        // the image part goes on unchanged
        let sent: Value = serde_json::from_slice(&one).unwrap();
        assert_eq!(o.body["messages"][0]["content"], sent["messages"][0]["content"]);
        // a text-only request with the same limits stays with the llm's budget and slots
        let t = chat(br#"{"messages":[{"role":"user","content":"hi"}]}"#, &lim).unwrap();
        assert_eq!(t.images, 0);
        assert!(t.body.get("id_slot").is_some());
        // two images do not fit a 1500 context counted at 768 each
        let two = body(vec![img(url("image/png", &png(8))), img(url("image/png", &png(8)))]);
        let small = Limits { images: Some(ImageRules { max_images: 3, ..RULES }), vision_budget: 1500, ..LIM };
        assert_eq!(chat(&two, &small).unwrap_err().code, "context_exceeded");
        // shapes and places refused
        for (parts, code) in [
            (vec![img("http://example.invalid/a.png".into())], "remote_image"),
            (vec![json!({"type": "image_url", "image_url": "data:image/png;base64,AAAA"})], "invalid_message"),
            (
                vec![json!({"type": "image_url", "image_url": {"url": url("image/png", &png(8)), "x": 1}})],
                "invalid_message",
            ),
            (
                vec![json!({"type": "image_url", "image_url": {"url": url("image/png", &png(8)), "detail": "max"}})],
                "invalid_value",
            ),
            (
                vec![json!({"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}})],
                "unsupported_content",
            ),
            (
                vec![img(url("image/png", &png(8))), img(url("image/png", &png(8))), img(url("image/png", &png(8)))],
                "too_many_images",
            ),
        ] {
            assert_eq!(chat(&body(parts.clone()), &lim).unwrap_err().code, code, "{parts:?}");
        }
        let sys = json!({"messages": [{"role": "system", "content": [img(url("image/png", &png(8)))]},
                                      {"role": "user", "content": "x"}]});
        assert_eq!(chat(sys.to_string().as_bytes(), &lim).unwrap_err().code, "unsupported_content");
    }

    #[test]
    fn a_repeated_tool_result_points_at_the_first() {
        let call = |id: &str| {
            json!({"id": id, "type": "function",
                                     "function": {"name": "status", "arguments": "{\"b\":1,\"a\":2}"}})
        };
        let o = run(json!({"messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": null, "tool_calls": [call("c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\":true}"},
            {"role": "assistant", "content": null, "tool_calls": [{"id": "c2", "type": "function",
                "function": {"name": "status", "arguments": "{\"a\": 2, \"b\": 1}"}}]},
            {"role": "tool", "tool_call_id": "c2", "content": "{\"ok\":true}"}]}))
        .unwrap();
        assert_eq!(o.results_pointed, 1);
        assert_eq!(o.body["messages"][4]["content"], json!("same result as tool call #c1"));
        assert_eq!(o.body["messages"][2]["content"], json!("{\"ok\":true}"));
    }

    /// The shared vectors of Ditto's duplicate-result rule (tests/fixtures/context_rules/duplicate_result.json).
    #[test]
    fn the_shared_duplicate_result_vectors_decide_as_the_host_does() {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../../tests/fixtures/context_rules/duplicate_result.json");
        let text = std::fs::read_to_string(path).expect("the shared vectors");
        let vectors: Value = serde_json::from_str(&text).unwrap();
        let arr = vectors.as_array().unwrap();
        assert!(arr.len() >= 10);
        for v in arr {
            let mut idx = ResultIndex::default();
            for p in v["present"].as_array().unwrap() {
                idx.present(
                    p["id"].as_str().unwrap(),
                    p["name"].as_str().unwrap(),
                    &p["arguments"].to_string(),
                    p["result"].as_str().unwrap(),
                    p.get("is_error").and_then(Value::as_bool).unwrap_or(false),
                );
            }
            let sent: Vec<String> = v["new"]
                .as_array()
                .unwrap()
                .iter()
                .map(|n| {
                    idx.sent(
                        n["id"].as_str().unwrap(),
                        n["name"].as_str().unwrap(),
                        &n["arguments"].to_string(),
                        n["result"].as_str().unwrap(),
                    )
                })
                .collect();
            let want: Vec<String> =
                v["sent"].as_array().unwrap().iter().map(|s| s.as_str().unwrap().to_string()).collect();
            assert_eq!(sent, want, "{}", v["name"]);
        }
    }

    #[test]
    fn boundary_ids_are_taken_out() {
        let a = "<untrusted_data id=\"0123456789ab\" source=\"x\">\nl\n</untrusted_data id=\"0123456789ab\">";
        assert_eq!(without_boundary_ids(a), "<untrusted_data id=\"\" source=\"x\">\nl\n</untrusted_data id=\"\">");
        assert_eq!(without_boundary_ids("<untrusted_data id=\"xyz\">"), "<untrusted_data id=\"xyz\">");
    }

    #[test]
    fn a_completion_needs_a_text_prompt() {
        let o = completion(br#"{"prompt": "Hello"}"#, &LIM).unwrap();
        assert_eq!(o.body["cache_prompt"], json!(true));
        assert_eq!(completion(br#"{"prompt": ["a"]}"#, &LIM).unwrap_err().code, "invalid_value");
        assert_eq!(completion(br#"{"prompt": "a", "suffix": "b"}"#, &LIM).unwrap_err().code, "unknown_field");
    }
}
