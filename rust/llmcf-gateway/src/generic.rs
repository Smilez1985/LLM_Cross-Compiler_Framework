//! The generic checked passthrough of the route table (routes.rs), as pure functions (no I/O): what a block declares
//! in its reach is all the gateway needs to take a new engine's requests safely.
//!
//! - http-multipart: the body is parsed with the speech route's bounded parser (audio::parse: at most MAX_PARTS parts,
//!   short heads, each field once); every part must be a declared field, every needed field must be there, a file is
//!   recognized by its first bytes against its declared magic types (never by its name or the client's word), a text
//!   field is UTF-8 of at most MAX_TEXT_FIELD bytes. The engine gets the gateway's own form: the same field names,
//!   each file under a fixed name (`<field>.<type>`) with its type's media type, never the client's file name.
//! - http-binary: the body is one of the declared magic types by its first bytes; it goes on with that type's media
//!   type.
//!
//! The gateway never logs a field's content, a file or a body.

use crate::audio;
use crate::routes::{self, Field, Magic};
use crate::rules::Refusal;
use bytes::Bytes;

/// The longest text field of a generic form.
pub const MAX_TEXT_FIELD: usize = 64 * 1024;

fn refuse(status: u16, code: &'static str, message: impl Into<String>) -> Refusal {
    Refusal { status, code, message: message.into() }
}

fn types_text(types: &[Magic]) -> String {
    types.iter().map(|t| t.word()).collect::<Vec<_>>().join(", ")
}

/// A checked form for its engine: its chunks (the files are slices of the client's body, no copy) and how many files
/// and bytes it carries (for the log line).
pub struct Form {
    pub chunks: Vec<Bytes>,
    pub files: usize,
}

/// The parts of an http-multipart body checked against the route's declared `fields` and rebuilt as the gateway's own
/// form with the client's boundary (by the parse, the boundary appears in none of the values).
pub fn multipart(body: &Bytes, boundary: &str, fields: &[Field]) -> Result<Form, Refusal> {
    let parts = audio::parse(body, boundary)?;
    let mut seen: Vec<&str> = Vec::new();
    let mut chunks = Vec::with_capacity(parts.len() * 2 + 1);
    let mut files = 0;
    for p in &parts {
        let Some(field) = fields.iter().find(|f| f.name == p.name) else {
            let shown: String = p.name.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '_').take(40).collect();
            return Err(refuse(400, "unknown_field", format!("the field {shown} is not taken")));
        };
        if seen.contains(&field.name.as_str()) {
            return Err(refuse(400, "invalid_multipart", "a field twice"));
        }
        seen.push(&field.name);
        if field.file {
            if p.data.is_empty() {
                return Err(refuse(400, "missing_file", format!("the file {} is empty", field.name)));
            }
            let kind = if field.types.is_empty() {
                None
            } else {
                Some(routes::sniff(&field.types, &p.data).ok_or_else(|| {
                    refuse(
                        415,
                        "unsupported_type",
                        format!("the file {} is none of {} by its first bytes", field.name, types_text(&field.types)),
                    )
                })?)
            };
            let (ext, mime) = kind.map(|k| (k.word(), k.mime())).unwrap_or(("bin", "application/octet-stream"));
            chunks.push(Bytes::from(format!(
                "--{boundary}\r\nContent-Disposition: form-data; name=\"{}\"; filename=\"{}.{ext}\"\r\n\
                 Content-Type: {mime}\r\n\r\n",
                field.name, field.name
            )));
            chunks.push(p.data.clone());
            chunks.push(Bytes::from_static(b"\r\n"));
            files += 1;
        } else {
            if p.filename.is_some() {
                return Err(refuse(400, "invalid_value", format!("{} is a text field, not a file", field.name)));
            }
            if p.data.len() > MAX_TEXT_FIELD || std::str::from_utf8(&p.data).is_err() {
                return Err(refuse(400, "invalid_value", format!("{} is a text of at most 64 KiB", field.name)));
            }
            chunks.push(Bytes::from(format!(
                "--{boundary}\r\nContent-Disposition: form-data; name=\"{}\"\r\n\r\n",
                field.name
            )));
            chunks.push(p.data.clone());
            chunks.push(Bytes::from_static(b"\r\n"));
        }
    }
    if let Some(missing) = fields.iter().find(|f| f.required && !seen.contains(&f.name.as_str())) {
        return Err(refuse(400, "missing_field", format!("the field {} is needed", missing.name)));
    }
    chunks.push(Bytes::from(format!("--{boundary}--\r\n")));
    Ok(Form { chunks, files })
}

/// The magic type of an http-binary body among the route's declared `types`.
pub fn binary(body: &[u8], types: &[Magic]) -> Result<Magic, Refusal> {
    if body.is_empty() {
        return Err(refuse(400, "missing_body", "the body is empty"));
    }
    routes::sniff(types, body).ok_or_else(|| {
        refuse(415, "unsupported_type", format!("the body is none of {} by its first bytes", types_text(types)))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::audio::tests::{form, B};

    const JPEG: &[u8] = b"\xFF\xD8\xFF\xE0\0\x10JFIF\0rest-of-image";

    fn fields() -> Vec<Field> {
        vec![
            Field { name: "image".into(), file: true, types: vec![Magic::Jpeg, Magic::Png], required: true },
            Field { name: "threshold".into(), file: false, types: Vec::new(), required: false },
        ]
    }

    fn joined(f: &Form) -> Vec<u8> {
        f.chunks.iter().flat_map(|c| c.to_vec()).collect()
    }

    #[test]
    fn a_declared_form_goes_on_as_the_gateways_own() {
        let body = Bytes::from(form(&[
            ("threshold", None, None, b"0.4"),
            ("image", Some("../../secret name.jpg"), Some("text/html"), JPEG),
        ]));
        let f = multipart(&body, B, &fields()).unwrap();
        assert_eq!(f.files, 1);
        let text = String::from_utf8_lossy(&joined(&f)).to_string();
        assert!(text.contains("name=\"image\"; filename=\"image.jpeg\"\r\nContent-Type: image/jpeg\r\n\r\n"));
        assert!(!text.contains("secret") && !text.contains("text/html"), "never the client's name or word");
        assert!(text.contains("name=\"threshold\"\r\n\r\n0.4\r\n") && text.ends_with(&format!("--{B}--\r\n")));
        // the gateway's form parses again with the same rules
        let again = audio::parse(&Bytes::from(joined(&f)), B).unwrap();
        assert_eq!(again.len(), 2);
        assert_eq!(again[1].data, Bytes::from_static(JPEG));
    }

    #[test]
    fn what_the_route_does_not_declare_is_refused() {
        let cases: [(Vec<u8>, &str); 6] = [
            (form(&[("image", Some("a.gif"), None, b"GIF89a....")]), "unsupported_type"),
            (form(&[("threshold", None, None, b"0.4")]), "missing_field"),
            (form(&[("image", Some("a.jpg"), None, JPEG), ("other", None, None, b"x")]), "unknown_field"),
            (form(&[("image", Some("a.jpg"), None, JPEG), ("image", Some("b.jpg"), None, JPEG)]), "invalid_multipart"),
            (form(&[("image", Some("a.jpg"), None, b"")]), "missing_file"),
            (form(&[("image", Some("a.jpg"), None, JPEG), ("threshold", Some("t.txt"), None, b"1")]), "invalid_value"),
        ];
        for (body, code) in cases {
            assert_eq!(multipart(&Bytes::from(body), B, &fields()).err().unwrap().code, code);
        }
        let long = vec![b'a'; MAX_TEXT_FIELD + 1];
        let body = Bytes::from(form(&[("image", Some("a.jpg"), None, JPEG), ("threshold", None, None, &long)]));
        assert_eq!(multipart(&body, B, &fields()).err().unwrap().code, "invalid_value");
        // a file field without types takes any bytes, under a fixed name
        let any = vec![Field { name: "blob".into(), file: true, types: Vec::new(), required: true }];
        let f = multipart(&Bytes::from(form(&[("blob", Some("x.exe"), None, b"MZ\x90\0")])), B, &any).unwrap();
        assert!(String::from_utf8_lossy(&joined(&f)).contains("filename=\"blob.bin\""));
    }

    #[test]
    fn a_binary_body_is_one_of_its_declared_types() {
        assert_eq!(binary(JPEG, &[Magic::Png, Magic::Jpeg]).unwrap(), Magic::Jpeg);
        assert_eq!(binary(b"%PDF-1.4", &[Magic::Jpeg]).err().unwrap().status, 415);
        assert_eq!(binary(b"", &[Magic::Jpeg]).err().unwrap().code, "missing_body");
    }
}
