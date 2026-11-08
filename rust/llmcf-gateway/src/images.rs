//! The image parts of a chat request for the vision engine (a llama-server of its own with --mmproj), as pure checks.
//!
//! An image arrives as OpenAI's `{"type": "image_url", "image_url": {"url": "data:image/png;base64,...", "detail":
//! ...}}` part and goes on unchanged. Only data URLs are taken: llama-server itself downloads an http(s) image URL (up
//! to 10 MB, from any host), and the board never fetches from the internet on a client's behalf, so such a URL is
//! refused here, as are file:// URLs and bare base64. The data URL names its type and the decoded bytes must start
//! with that type's magic: png, jpeg or gif, the formats llama-server's image loader (stb_image) reads; webp needs its
//! ffmpeg build and is refused. Each image is capped, all images of a request together are capped, and so is their
//! count. An image is counted with a fixed number of tokens (`--image-tokens`) against the vision engine's context:
//! the projector's own count depends on the model and the picture, so the gateway takes a conservative number.

use crate::rules::Refusal;

/// What the gateway takes from the images of one request.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ImageRules {
    pub max_images: u32,
    /// the largest image, decoded bytes
    pub max_image_bytes: u64,
    /// all images of a request together, decoded bytes
    pub max_total_bytes: u64,
    /// tokens one image is counted with
    pub tokens: u64,
}

/// The images of one request so far.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Seen {
    pub count: u32,
    pub bytes: u64,
}

fn refuse(status: u16, code: &'static str, message: impl Into<String>) -> Refusal {
    Refusal { status, code, message: message.into() }
}

/// The image types taken: the data URL's media type and the first bytes of such a file.
const TYPES: &[(&str, &[&[u8]])] = &[
    ("image/png", &[b"\x89PNG\r\n\x1a\n"]),
    ("image/jpeg", &[b"\xff\xd8\xff"]),
    ("image/jpg", &[b"\xff\xd8\xff"]),
    ("image/gif", &[b"GIF87a", b"GIF89a"]),
];

fn b64(c: u8) -> Option<u8> {
    match c {
        b'A'..=b'Z' => Some(c - b'A'),
        b'a'..=b'z' => Some(c - b'a' + 26),
        b'0'..=b'9' => Some(c - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

/// The decoded length of a padded base64 text, None when it is no such text (only the alphabet, a length that is a
/// multiple of 4, at most two '=' at its end).
pub fn base64_len(text: &[u8]) -> Option<u64> {
    if text.is_empty() || text.len() % 4 != 0 {
        return None;
    }
    let pad = text.iter().rev().take_while(|&&c| c == b'=').count();
    if pad > 2 || !text[..text.len() - pad].iter().all(|&c| b64(c).is_some()) {
        return None;
    }
    Some((text.len() / 4 * 3 - pad) as u64)
}

/// The first bytes of a base64 text (only as many as its first 16 characters hold).
pub fn base64_head(text: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(12);
    for quad in text.chunks(4).take(4) {
        let v: Vec<u8> = quad.iter().map_while(|&c| b64(c)).collect();
        if v.len() >= 2 {
            out.push((v[0] << 2) | (v[1] >> 4));
        }
        if v.len() >= 3 {
            out.push((v[1] << 4) | (v[2] >> 2));
        }
        if v.len() == 4 {
            out.push((v[2] << 6) | v[3]);
        }
        if v.len() < 4 {
            break;
        }
    }
    out
}

/// Checks one image part's URL against the rules and the images of the request so far (counted on).
pub fn check_url(url: &str, rules: &ImageRules, seen: &mut Seen) -> Result<(), Refusal> {
    let lower: String = url.chars().take(16).collect::<String>().to_ascii_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") {
        return Err(refuse(
            400,
            "remote_image",
            "the board fetches no image from the network; send the image itself as a data: URL",
        ));
    }
    let Some(rest) = url.strip_prefix("data:") else {
        return Err(refuse(400, "invalid_image", "an image is a data: URL (data:image/png;base64,...)"));
    };
    let Some((head, data)) = rest.split_once(',') else {
        return Err(refuse(400, "invalid_image", "an image is a data: URL (data:image/png;base64,...)"));
    };
    let Some(media) = head.strip_suffix(";base64") else {
        return Err(refuse(400, "invalid_image", "an image's data URL is base64 (data:image/png;base64,...)"));
    };
    let media = media.to_ascii_lowercase();
    let Some((_, magics)) = TYPES.iter().find(|(t, _)| *t == media) else {
        let why = if media == "image/webp" {
            "webp is not read by the vision engine's image loader (stb_image); send png, jpeg or gif"
        } else {
            "an image is png, jpeg or gif"
        };
        return Err(refuse(415, "unsupported_image", why));
    };
    seen.count += 1;
    if seen.count > rules.max_images {
        return Err(refuse(400, "too_many_images", format!("a request carries at most {} images", rules.max_images)));
    }
    let size = base64_len(data.as_bytes())
        .ok_or_else(|| refuse(400, "invalid_image", "an image's data is no valid base64"))?;
    if size > rules.max_image_bytes {
        return Err(refuse(413, "image_too_large", format!("an image is at most {} KB", rules.max_image_bytes / 1024)));
    }
    seen.bytes += size;
    if seen.bytes > rules.max_total_bytes {
        return Err(refuse(
            413,
            "image_too_large",
            format!("the images of a request are at most {} KB together", rules.max_total_bytes / 1024),
        ));
    }
    let first = base64_head(data.as_bytes());
    if !magics.iter().any(|m| first.starts_with(m)) {
        return Err(refuse(415, "unsupported_image", "the image's bytes are not the type its data URL names"));
    }
    Ok(())
}

#[cfg(test)]
pub mod tests {
    use super::*;

    pub const RULES: ImageRules =
        ImageRules { max_images: 2, max_image_bytes: 1024, max_total_bytes: 1536, tokens: 768 };

    pub fn enc(data: &[u8]) -> String {
        const A: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        let mut out = String::new();
        for c in data.chunks(3) {
            let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
            for i in 0..4 {
                if i <= c.len() {
                    out.push(A[(n >> (18 - 6 * i) & 63) as usize] as char);
                } else {
                    out.push('=');
                }
            }
        }
        out
    }

    pub fn png(size: usize) -> Vec<u8> {
        let mut v = b"\x89PNG\r\n\x1a\n".to_vec();
        v.resize(size, 7);
        v
    }

    pub fn url(media: &str, data: &[u8]) -> String {
        format!("data:{media};base64,{}", enc(data))
    }

    #[test]
    fn base64_lengths_and_heads() {
        for n in 0..10 {
            let data: Vec<u8> = (0..n as u8).collect();
            let e = enc(&data);
            if n > 0 {
                assert_eq!(base64_len(e.as_bytes()), Some(n as u64), "{e}");
                assert_eq!(base64_head(e.as_bytes()), data[..n.min(12)], "{e}");
            }
        }
        assert_eq!(base64_len(b""), None);
        assert_eq!(base64_len(b"abc"), None);
        assert_eq!(base64_len(b"ab=c"), None);
        assert_eq!(base64_len(b"a==="), None);
        assert_eq!(base64_len(b"ab c"), None);
    }

    #[test]
    fn a_data_url_of_a_known_type_is_taken() {
        let mut seen = Seen::default();
        check_url(&url("image/png", &png(100)), &RULES, &mut seen).unwrap();
        let jpeg = [b"\xff\xd8\xff\xe0".as_slice(), &[0u8; 40]].concat();
        check_url(&url("image/jpeg", &jpeg), &RULES, &mut seen).unwrap();
        assert_eq!(seen, Seen { count: 2, bytes: 144 });
        let mut seen = Seen::default();
        check_url(&url("image/gif", b"GIF89a-------"), &RULES, &mut seen).unwrap();
        check_url(&url("IMAGE/JPG", &jpeg), &RULES, &mut seen).unwrap();
    }

    #[test]
    fn remote_urls_and_other_shapes_are_refused() {
        let code = |u: &str| check_url(u, &RULES, &mut Seen::default()).unwrap_err().code;
        assert_eq!(code("http://example.invalid/cat.png"), "remote_image");
        assert_eq!(code("HTTPS://example.invalid/cat.png"), "remote_image");
        assert_eq!(code("file:///etc/passwd"), "invalid_image");
        assert_eq!(code(&enc(&png(10))), "invalid_image", "bare base64");
        assert_eq!(code("data:image/png,rawbytes"), "invalid_image");
        assert_eq!(code("data:image/png;base64"), "invalid_image");
        assert_eq!(code("data:image/png;base64,@@@@"), "invalid_image");
        assert_eq!(code(&url("image/webp", b"RIFF\0\0\0\0WEBPVP8 ")), "unsupported_image");
        assert_eq!(code(&url("image/svg+xml", b"<svg></svg>")), "unsupported_image");
        assert_eq!(code(&url("text/plain", b"hello")), "unsupported_image");
    }

    #[test]
    fn bad_magic_too_big_and_too_many_are_refused() {
        let one = |u: &str| check_url(u, &RULES, &mut Seen::default()).unwrap_err();
        let e = one(&url("image/png", b"GIF89a-------"));
        assert_eq!((e.status, e.code), (415, "unsupported_image"));
        let e = one(&url("image/jpeg", &png(64)));
        assert_eq!(e.code, "unsupported_image");
        let e = one(&url("image/png", &png(1025)));
        assert_eq!((e.status, e.code), (413, "image_too_large"));
        let mut seen = Seen::default();
        check_url(&url("image/png", &png(1000)), &RULES, &mut seen).unwrap();
        let e = check_url(&url("image/png", &png(600)), &RULES, &mut seen).unwrap_err();
        assert_eq!((e.status, e.code), (413, "image_too_large"), "the request's images together");
        let mut seen = Seen::default();
        for _ in 0..2 {
            check_url(&url("image/png", &png(10)), &RULES, &mut seen).unwrap();
        }
        let e = check_url(&url("image/png", &png(10)), &RULES, &mut seen).unwrap_err();
        assert_eq!(e.code, "too_many_images");
    }
}
