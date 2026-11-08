//! Speech to text through the gateway: OpenAI's `POST /v1/audio/transcriptions` (multipart/form-data) checked and
//! translated for whisper.cpp's whisper-server (the bundle's blocks stt-whisper-cpp and stt-whisper-cpp-program), as
//! pure functions (no I/O).
//!
//! - The body is read whole (it is capped by `--max-audio-mb` before it is read, apart from the JSON cap) and parsed
//!   here with a bounded parser: the body must start with its boundary, at most MAX_PARTS parts, each part's head at
//!   most MAX_PART_HEAD bytes with only Content-Disposition (form-data, a name, a filename) and Content-Type, each
//!   field once. The parts are slices of the one body (no copy of the audio).
//! - Fields: `file` (needed), `model` (taken and not passed on: the board's whisper-server has one model),
//!   `language` (auto or a language code), `prompt`, `response_format` (json, text, verbose_json, srt, vtt: the
//!   formats whisper-server writes itself) and `temperature` (0 to 1). Any other field is refused.
//! - The audio's type is read from its first bytes, never from its name or the client's word: wav, mp3, flac and ogg
//!   with Vorbis, what whisper-server decodes itself (miniaudio with stb_vorbis). The bundle starts whisper-server
//!   without `--convert` (that runs ffmpeg through a shell), so webm, Opus, mp4/m4a and the rest are refused with 415
//!   and the reason rather than passed on to fail there.
//! - The request to whisper-server is its own form at /inference with the same field names (whisper's `file`,
//!   `language`, `prompt`, `response_format`, `temperature`), the file under a fixed name (`audio.<type>`), never the
//!   client's file name. whisper-server answers JSON as OpenAI does (`{"text": ...}`); its text format comes as
//!   text/html and is passed on as text/plain.
//!
//! The gateway never logs the audio, the prompt or the transcript.

use crate::rules::Refusal;
use bytes::Bytes;

/// Parts of one upload (file and five fields fit with room).
pub const MAX_PARTS: usize = 16;
/// The head of one part (its Content-Disposition and Content-Type lines).
pub const MAX_PART_HEAD: usize = 1024;
/// whisper's prompt is cut to half its text context (224 tokens) anyway.
pub const MAX_PROMPT: usize = 2048;

fn refuse(status: u16, code: &'static str, message: impl Into<String>) -> Refusal {
    Refusal { status, code, message: message.into() }
}

fn bad(message: &str) -> Refusal {
    refuse(400, "invalid_multipart", message)
}

/// One part of a multipart/form-data body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Part {
    pub name: String,
    pub filename: Option<String>,
    pub content_type: Option<String>,
    pub data: Bytes,
}

/// The boundary of a `multipart/form-data; boundary=...` Content-Type (RFC 2046: 1 to 70 characters of its set).
pub fn boundary(content_type: &str) -> Option<String> {
    let mut it = content_type.split(';');
    if !it.next()?.trim().eq_ignore_ascii_case("multipart/form-data") {
        return None;
    }
    for param in it {
        let (k, v) = param.split_once('=')?;
        if !k.trim().eq_ignore_ascii_case("boundary") {
            continue;
        }
        let v = v.trim();
        let v = v.strip_prefix('"').and_then(|x| x.strip_suffix('"')).unwrap_or(v);
        let ok = (1..=70).contains(&v.len())
            && !v.ends_with(' ')
            && v.bytes().all(|c| c.is_ascii_alphanumeric() || b"'()+_,-./:=? ".contains(&c));
        return ok.then(|| v.to_string());
    }
    None
}

/// A quoted value of a Content-Disposition parameter (no escapes, no quote, no control character inside).
fn quoted(v: &str) -> Option<String> {
    let inner = v.trim().strip_prefix('"')?.strip_suffix('"')?;
    (inner.len() <= 256 && !inner.chars().any(|c| c == '"' || c == '\\' || c.is_control())).then(|| inner.to_string())
}

fn head(text: &[u8]) -> Result<(String, Option<String>, Option<String>), Refusal> {
    let text = std::str::from_utf8(text).map_err(|_| bad("a part's head is no text"))?;
    let (mut name, mut filename, mut ctype, mut disposition) = (None, None, None, false);
    for line in text.split("\r\n") {
        let (k, v) = line.split_once(':').ok_or_else(|| bad("a part's head line is Name: value"))?;
        match k.trim().to_ascii_lowercase().as_str() {
            "content-disposition" if !disposition => {
                disposition = true;
                let mut params = v.split(';');
                if !params.next().unwrap_or("").trim().eq_ignore_ascii_case("form-data") {
                    return Err(bad("a part is form-data"));
                }
                for p in params {
                    let (pk, pv) = p.split_once('=').ok_or_else(|| bad("a form-data parameter is name=value"))?;
                    let value = quoted(pv).ok_or_else(|| bad("a form-data parameter is a short quoted text"))?;
                    let slot = match pk.trim().to_ascii_lowercase().as_str() {
                        "name" => &mut name,
                        "filename" => &mut filename,
                        _ => return Err(bad("a form-data part has a name and a filename only")),
                    };
                    if slot.replace(value).is_some() {
                        return Err(bad("a form-data parameter twice"));
                    }
                }
            }
            "content-type" if ctype.is_none() => {
                let v = v.trim();
                if v.is_empty() || v.len() > 128 || v.chars().any(|c| c.is_control()) {
                    return Err(bad("a part's Content-Type is a short text"));
                }
                ctype = Some(v.to_string());
            }
            _ => return Err(bad("a part's head has Content-Disposition and Content-Type only")),
        }
    }
    let name = name.ok_or_else(|| bad("a part has a name"))?;
    Ok((name, filename, ctype))
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    memchr::memmem::find(hay, needle)
}

/// The parts of a multipart/form-data body (see the module text for its bounds).
pub fn parse(body: &Bytes, boundary: &str) -> Result<Vec<Part>, Refusal> {
    let delim = format!("--{boundary}");
    let next = format!("\r\n--{boundary}");
    if !body.starts_with(delim.as_bytes()) {
        return Err(bad("the body starts with its boundary"));
    }
    let mut pos = delim.len();
    let mut parts = Vec::new();
    loop {
        let rest = &body[pos..];
        if rest.starts_with(b"--") {
            return Ok(parts);
        }
        if !rest.starts_with(b"\r\n") {
            return Err(bad("a boundary line ends with CRLF"));
        }
        pos += 2;
        if parts.len() == MAX_PARTS {
            return Err(bad("too many parts"));
        }
        let window = &body[pos..body.len().min(pos + MAX_PART_HEAD + 4)];
        let end = find(window, b"\r\n\r\n").ok_or_else(|| bad("a part's head is too long or has no end"))?;
        let (name, filename, content_type) = head(&window[..end])?;
        let start = pos + end + 4;
        let len = find(&body[start..], next.as_bytes()).ok_or_else(|| bad("the body has no closing boundary"))?;
        parts.push(Part { name, filename, content_type, data: body.slice(start..start + len) });
        pos = start + len + next.len();
    }
}

/// The audio types whisper-server decodes without ffmpeg.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Audio {
    Wav,
    Mp3,
    Flac,
    OggVorbis,
}

impl Audio {
    pub fn ext(self) -> &'static str {
        match self {
            Audio::Wav => "wav",
            Audio::Mp3 => "mp3",
            Audio::Flac => "flac",
            Audio::OggVorbis => "ogg",
        }
    }
}

const NO_FFMPEG: &str = "the board's speech engine (whisper-server without ffmpeg) reads wav, mp3, flac and ogg \
                         with Vorbis";

/// The audio's type by its first bytes.
pub fn sniff(d: &[u8]) -> Result<Audio, Refusal> {
    let refused = |what: &str| refuse(415, "unsupported_audio", format!("{what}: {NO_FFMPEG}"));
    if d.len() >= 12 && &d[..4] == b"RIFF" && &d[8..12] == b"WAVE" {
        return Ok(Audio::Wav);
    }
    if d.starts_with(b"fLaC") {
        return Ok(Audio::Flac);
    }
    if d.starts_with(b"ID3") {
        return Ok(Audio::Mp3);
    }
    // an MPEG audio frame: 11 sync bits, a version and a layer that are not "reserved"
    if d.len() >= 2 && d[0] == 0xFF && d[1] & 0xE0 == 0xE0 && (d[1] >> 3) & 3 != 1 && (d[1] >> 1) & 3 != 0 {
        return Ok(Audio::Mp3);
    }
    if d.starts_with(b"OggS") {
        // the first page's first packet names the codec: 27 bytes of page head, then its segment table
        let segs = *d.get(26).unwrap_or(&0) as usize;
        let packet = d.get(27 + segs..).unwrap_or(&[]);
        if packet.starts_with(b"\x01vorbis") {
            return Ok(Audio::OggVorbis);
        }
        if packet.starts_with(b"OpusHead") {
            return Err(refused("ogg with Opus"));
        }
        return Err(refused("ogg of an unknown codec"));
    }
    if d.starts_with(b"\x1a\x45\xdf\xa3") {
        return Err(refused("webm"));
    }
    if d.len() >= 8 && &d[4..8] == b"ftyp" {
        return Err(refused("mp4 or m4a"));
    }
    Err(refused("not an audio type the gateway knows"))
}

/// whisper-server's answer formats (the same words as OpenAI's).
pub const FORMATS: &[&str] = &["json", "text", "verbose_json", "srt", "vtt"];

/// A checked transcription request.
#[derive(Debug)]
pub struct Transcription {
    pub audio: Bytes,
    pub kind: Audio,
    pub language: Option<String>,
    pub prompt: Option<String>,
    pub format: &'static str,
    pub temperature: Option<String>,
}

fn text_of(p: &Part, max: usize) -> Result<String, Refusal> {
    let t = std::str::from_utf8(&p.data).map_err(|_| refuse(400, "invalid_value", format!("{} is text", p.name)))?;
    if t.len() > max {
        return Err(refuse(400, "invalid_value", format!("{} is at most {max} bytes", p.name)));
    }
    Ok(t.to_string())
}

/// The fields of an upload checked (see the module text).
pub fn transcription(parts: Vec<Part>) -> Result<Transcription, Refusal> {
    let mut seen: Vec<String> = Vec::new();
    let (mut file, mut language, mut prompt, mut format, mut temperature) = (None, None, None, "json", None);
    for p in parts {
        if seen.contains(&p.name) {
            return Err(refuse(400, "invalid_multipart", "a field twice"));
        }
        seen.push(p.name.clone());
        match p.name.as_str() {
            "file" => file = Some(p.data),
            "model" => {
                text_of(&p, 128)?;
            }
            "language" => {
                let l = text_of(&p, 8)?.trim().to_ascii_lowercase();
                let ok = l == "auto" || ((2..=3).contains(&l.len()) && l.bytes().all(|c| c.is_ascii_lowercase()));
                if !ok {
                    return Err(refuse(400, "invalid_value", "language is auto or a language code (en, de, ...)"));
                }
                language = Some(l);
            }
            "prompt" => prompt = Some(text_of(&p, MAX_PROMPT)?),
            "response_format" => {
                let f = text_of(&p, 16)?;
                format = FORMATS.iter().copied().find(|x| *x == f.trim()).ok_or_else(|| {
                    refuse(400, "invalid_value", "response_format is json, text, verbose_json, srt or vtt")
                })?;
            }
            "temperature" => {
                let t: f64 = text_of(&p, 16)?
                    .trim()
                    .parse()
                    .ok()
                    .filter(|t: &f64| (0.0..=1.0).contains(t))
                    .ok_or_else(|| refuse(400, "invalid_value", "temperature is a number from 0 to 1"))?;
                temperature = Some(format!("{t}"));
            }
            other => {
                let shown: String = other.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '_').take(40).collect();
                return Err(refuse(400, "unknown_field", format!("the field {shown} is not taken")));
            }
        }
    }
    let audio = file.ok_or_else(|| refuse(400, "missing_file", "the upload has no file field"))?;
    if audio.is_empty() {
        return Err(refuse(400, "missing_file", "the file is empty"));
    }
    let kind = sniff(&audio)?;
    Ok(Transcription { audio, kind, language, prompt, format, temperature })
}

/// whisper-server's form for /inference: its field names, the audio as a slice of the client's body (no copy), the
/// client's boundary (which, by the parse, appears in none of the values).
pub fn whisper_form(t: &Transcription, boundary: &str) -> Vec<Bytes> {
    let mut out = Vec::with_capacity(4);
    let mut text = String::new();
    let mut field = |name: &str, value: &str| {
        text.push_str(&format!("--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"));
    };
    field("response_format", t.format);
    if let Some(l) = &t.language {
        field("language", l);
    }
    if let Some(p) = &t.prompt {
        field("prompt", p);
    }
    if let Some(x) = &t.temperature {
        field("temperature", x);
    }
    text.push_str(&format!(
        "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.{}\"\r\n\
         Content-Type: application/octet-stream\r\n\r\n",
        t.kind.ext()
    ));
    out.push(Bytes::from(text));
    out.push(t.audio.clone());
    out.push(Bytes::from(format!("\r\n--{boundary}--\r\n")));
    out
}

#[cfg(test)]
pub mod tests {
    use super::*;

    pub const B: &str = "llmcf-test-boundary-7d1";

    pub fn wav(len: usize) -> Vec<u8> {
        let mut v = b"RIFF\x24\x00\x00\x00WAVEfmt ".to_vec();
        v.resize(len.max(16), 0x80);
        v
    }

    /// A form of (name, filename, content type, data) parts with boundary B.
    /// One part of a test form: name, filename, content type, data.
    pub type Field<'a> = (&'a str, Option<&'a str>, Option<&'a str>, &'a [u8]);

    pub fn form(parts: &[Field]) -> Vec<u8> {
        let mut out = Vec::new();
        for (name, filename, ctype, data) in parts {
            out.extend_from_slice(format!("--{B}\r\nContent-Disposition: form-data; name=\"{name}\"").as_bytes());
            if let Some(f) = filename {
                out.extend_from_slice(format!("; filename=\"{f}\"").as_bytes());
            }
            out.extend_from_slice(b"\r\n");
            if let Some(c) = ctype {
                out.extend_from_slice(format!("Content-Type: {c}\r\n").as_bytes());
            }
            out.extend_from_slice(b"\r\n");
            out.extend_from_slice(data);
            out.extend_from_slice(b"\r\n");
        }
        out.extend_from_slice(format!("--{B}--\r\n").as_bytes());
        out
    }

    fn parsed(body: Vec<u8>) -> Result<Vec<Part>, Refusal> {
        parse(&Bytes::from(body), B)
    }

    #[test]
    fn the_boundary_comes_from_the_content_type() {
        assert_eq!(boundary("multipart/form-data; boundary=abc").as_deref(), Some("abc"));
        assert_eq!(boundary("Multipart/Form-Data; charset=x; boundary=\"a b:c\"").as_deref(), Some("a b:c"));
        assert_eq!(boundary("multipart/form-data"), None);
        assert_eq!(boundary("multipart/mixed; boundary=abc"), None);
        assert_eq!(boundary("application/json"), None);
        assert_eq!(boundary(&format!("multipart/form-data; boundary={}", "x".repeat(71))), None);
        assert_eq!(boundary("multipart/form-data; boundary=a\r\nb"), None);
        assert_eq!(boundary("multipart/form-data; boundary="), None);
    }

    #[test]
    fn parts_are_slices_of_the_body_between_its_boundaries() {
        let audio = wav(100);
        let body = form(&[
            ("file", Some("talk.wav"), Some("audio/wav"), &audio),
            ("model", None, None, b"whisper-1"),
            // a value that looks like a boundary without its CRLF and dashes stays one value
            ("prompt", None, None, format!("about {B} and --{B}x").as_bytes()),
        ]);
        let parts = parsed(body).unwrap();
        assert_eq!(parts.len(), 3);
        assert_eq!(parts[0].name, "file");
        assert_eq!(parts[0].filename.as_deref(), Some("talk.wav"));
        assert_eq!(parts[0].content_type.as_deref(), Some("audio/wav"));
        assert_eq!(&parts[0].data[..], &audio[..]);
        assert_eq!(&parts[1].data[..], b"whisper-1");
        assert_eq!(parts[2].data, Bytes::from(format!("about {B} and --{B}x")));
        // empty form
        assert!(parsed(format!("--{B}--\r\n").into_bytes()).unwrap().is_empty());
    }

    #[test]
    fn a_broken_form_is_refused() {
        let ok = form(&[("model", None, None, b"m")]);
        let cases: Vec<Vec<u8>> = vec![
            ok[2..].to_vec(),                                                                // no leading boundary
            ok[..ok.len() - 12].to_vec(),                                                    // no closing boundary
            format!("--{B}\r\nContent-Disposition: form-data; name=\"a\"\r\n").into_bytes(), // no head end
            format!("--{B}\r\nX-Other: 1\r\n\r\nv\r\n--{B}--").into_bytes(),
            format!("--{B}\r\nContent-Disposition: attachment; name=\"a\"\r\n\r\nv\r\n--{B}--").into_bytes(),
            format!("--{B}\r\nContent-Disposition: form-data; name=a\r\n\r\nv\r\n--{B}--").into_bytes(),
            format!("--{B}\r\nContent-Disposition: form-data; filename=\"f\"\r\n\r\nv\r\n--{B}--").into_bytes(),
            format!("--{B}\r\nContent-Disposition: form-data; name=\"a\"; name=\"b\"\r\n\r\nv\r\n--{B}--").into_bytes(),
            format!("--{B}\r\nContent-Disposition: form-data; name=\"a\"; size=\"1\"\r\n\r\nv\r\n--{B}--").into_bytes(),
            format!("--{B}x\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nv\r\n--{B}--").into_bytes(),
            format!("--{B}\r\nContent-Disposition: form-data; name=\"{}\"\r\n\r\nv\r\n--{B}--", "n".repeat(1100))
                .into_bytes(),
        ];
        for body in cases {
            let e = parsed(body.clone()).unwrap_err();
            assert_eq!(e.code, "invalid_multipart", "{}", String::from_utf8_lossy(&body));
        }
        let many: Vec<Field> = (0..=MAX_PARTS).map(|_| ("model", None, None, b"m".as_slice())).collect();
        assert_eq!(parsed(form(&many)).unwrap_err().message, "too many parts");
    }

    #[test]
    fn audio_types_by_their_first_bytes() {
        assert_eq!(sniff(&wav(64)).unwrap(), Audio::Wav);
        assert_eq!(sniff(b"fLaC\0\0\0\x22").unwrap(), Audio::Flac);
        assert_eq!(sniff(b"ID3\x04\0\0\0\0").unwrap(), Audio::Mp3);
        assert_eq!(sniff(b"\xff\xfb\x90\x64").unwrap(), Audio::Mp3);
        let mut ogg = b"OggS\0\x02".to_vec();
        ogg.resize(26, 0);
        ogg.push(1); // one segment
        ogg.push(30);
        let mut vorbis = ogg.clone();
        vorbis.extend_from_slice(b"\x01vorbis\0\0");
        assert_eq!(sniff(&vorbis).unwrap(), Audio::OggVorbis);
        let mut opus = ogg;
        opus.extend_from_slice(b"OpusHead\x01");
        for (data, word) in [
            (opus, "Opus"),
            (b"\x1a\x45\xdf\xa3\x01\0\0\0".to_vec(), "webm"),
            (b"\0\0\0\x20ftypM4A ".to_vec(), "mp4"),
            (b"hello world".to_vec(), "knows"),
            (b"\xff\xe1".to_vec(), "knows"), // a reserved MPEG layer
        ] {
            let e = sniff(&data).unwrap_err();
            assert_eq!((e.status, e.code), (415, "unsupported_audio"));
            assert!(e.message.contains(word), "{}", e.message);
        }
    }

    #[test]
    fn the_fields_are_checked_and_whisper_gets_its_own_form() {
        let audio = wav(64);
        let body = form(&[
            ("file", Some("../../etc/x.wav"), Some("audio/x-wav"), &audio),
            ("model", None, None, b"whisper-1"),
            ("language", None, None, b"DE"),
            ("prompt", None, None, "Grüße".as_bytes()),
            ("response_format", None, None, b"verbose_json"),
            ("temperature", None, None, b"0.20"),
        ]);
        let t = transcription(parsed(body).unwrap()).unwrap();
        assert_eq!((t.kind, t.format), (Audio::Wav, "verbose_json"));
        assert_eq!((t.language.as_deref(), t.temperature.as_deref()), (Some("de"), Some("0.2")));
        let out: Vec<u8> = whisper_form(&t, B).concat();
        let back = parse(&Bytes::from(out), B).unwrap();
        let names: Vec<&str> = back.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, ["response_format", "language", "prompt", "temperature", "file"]);
        assert_eq!(back[4].filename.as_deref(), Some("audio.wav"), "never the client's file name");
        assert_eq!(&back[4].data[..], &audio[..]);
        assert_eq!(&back[2].data[..], "Grüße".as_bytes());
        // defaults: json, nothing else
        let t = transcription(parsed(form(&[("file", Some("a"), None, &audio)])).unwrap()).unwrap();
        assert_eq!((t.format, t.language.as_deref(), t.prompt.as_deref()), ("json", None, None));
        let only: Vec<u8> = whisper_form(&t, B).concat();
        assert_eq!(parse(&Bytes::from(only), B).unwrap().len(), 2);
    }

    #[test]
    fn missing_or_wrong_fields_are_refused() {
        let audio = wav(64);
        let code = |parts: &[Field]| transcription(parsed(form(parts)).unwrap()).unwrap_err().code;
        assert_eq!(code(&[("model", None, None, b"whisper-1")]), "missing_file");
        assert_eq!(code(&[("file", Some("a"), None, b"")]), "missing_file");
        assert_eq!(code(&[("file", Some("a.wav"), Some("audio/wav"), b"not audio at all")]), "unsupported_audio");
        assert_eq!(code(&[("file", None, None, &audio), ("file", None, None, &audio)]), "invalid_multipart");
        assert_eq!(
            code(&[("file", None, None, &audio), ("timestamp_granularities[]", None, None, b"word")]),
            "unknown_field"
        );
        assert_eq!(code(&[("file", None, None, &audio), ("response_format", None, None, b"xml")]), "invalid_value");
        assert_eq!(code(&[("file", None, None, &audio), ("temperature", None, None, b"1.5")]), "invalid_value");
        assert_eq!(code(&[("file", None, None, &audio), ("temperature", None, None, b"NaN")]), "invalid_value");
        assert_eq!(code(&[("file", None, None, &audio), ("language", None, None, b"en-US")]), "invalid_value");
        assert_eq!(code(&[("file", None, None, &audio), ("prompt", None, None, &[0xff, 0xfe])]), "invalid_value");
        let long = vec![b'a'; MAX_PROMPT + 1];
        assert_eq!(code(&[("file", None, None, &audio), ("prompt", None, None, &long)]), "invalid_value");
    }
}
