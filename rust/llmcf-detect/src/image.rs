//! Images in: JPEG and PNG, recognized by their first bytes, their dimensions read from the header before anything is
//! decoded (more than MAX_PIXELS is refused, so a small file cannot unpack into gigabytes), decoded by pure Rust
//! decoders into 8-bit RGB. Grey, grey with alpha, RGBA, CMYK and 16-bit images become RGB; alpha is dropped.

/// 40 megapixels: above a 12-megapixel still, far below what would exhaust a small board's memory.
pub const MAX_PIXELS: u64 = 40_000_000;

/// An 8-bit RGB image.
#[derive(Clone, Debug, PartialEq)]
pub struct Rgb {
    pub width: u32,
    pub height: u32,
    pub data: Vec<u8>,
}

/// Why an image is not taken: the HTTP status and the code and words of the answer.
#[derive(Debug, PartialEq)]
pub struct Refused {
    pub status: u16,
    pub code: &'static str,
    pub message: String,
}

fn refuse(status: u16, code: &'static str, message: impl Into<String>) -> Refused {
    Refused { status, code, message: message.into() }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Jpeg,
    Png,
}

pub fn sniff(data: &[u8]) -> Option<Kind> {
    if data.starts_with(&[0xFF, 0xD8, 0xFF]) {
        Some(Kind::Jpeg)
    } else if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some(Kind::Png)
    } else {
        None
    }
}

fn check_size(w: u64, h: u64) -> Result<(), Refused> {
    if w == 0 || h == 0 {
        return Err(refuse(400, "invalid_image", "the image has no pixels"));
    }
    if w * h > MAX_PIXELS {
        return Err(refuse(
            422,
            "image_too_large",
            format!("the image's header announces {w} x {h} pixels; at most 40 megapixels are taken"),
        ));
    }
    Ok(())
}

/// The image's dimensions from its header alone.
pub fn dimensions(data: &[u8]) -> Result<(u32, u32), Refused> {
    match sniff(data) {
        Some(Kind::Png) => {
            if data.len() < 24 || &data[12..16] != b"IHDR" {
                return Err(refuse(400, "invalid_image", "the PNG has no header"));
            }
            let w = u32::from_be_bytes([data[16], data[17], data[18], data[19]]);
            let h = u32::from_be_bytes([data[20], data[21], data[22], data[23]]);
            Ok((w, h))
        }
        Some(Kind::Jpeg) => {
            let mut d = jpeg_decoder::Decoder::new(data);
            d.read_info().map_err(|_| refuse(400, "invalid_image", "the JPEG's header cannot be read"))?;
            let info = d.info().ok_or_else(|| refuse(400, "invalid_image", "the JPEG has no frame header"))?;
            Ok((info.width as u32, info.height as u32))
        }
        None => Err(refuse(415, "unsupported_type", "the image is no JPEG or PNG by its first bytes")),
    }
}

/// The image decoded into RGB, after the header check.
pub fn decode(data: &[u8]) -> Result<Rgb, Refused> {
    let (w, h) = dimensions(data)?;
    check_size(w as u64, h as u64)?;
    let bad = |what: &str| refuse(400, "invalid_image", format!("the {what} cannot be decoded"));
    match sniff(data) {
        Some(Kind::Jpeg) => {
            let mut d = jpeg_decoder::Decoder::new(data);
            d.set_max_decoding_buffer_size((MAX_PIXELS * 4) as usize);
            let px = d.decode().map_err(|_| bad("JPEG"))?;
            let info = d.info().ok_or_else(|| bad("JPEG"))?;
            if (info.width as u32, info.height as u32) != (w, h) {
                return Err(bad("JPEG"));
            }
            let n = (w * h) as usize;
            let data = match info.pixel_format {
                jpeg_decoder::PixelFormat::RGB24 => px,
                jpeg_decoder::PixelFormat::L8 => px.iter().flat_map(|v| [*v, *v, *v]).collect(),
                jpeg_decoder::PixelFormat::L16 => px.chunks_exact(2).flat_map(|v| [v[0], v[0], v[0]]).collect(),
                jpeg_decoder::PixelFormat::CMYK32 => px
                    .chunks_exact(4)
                    .flat_map(|p| {
                        // Adobe's inverted CMYK as the decoder hands it: each channel times K
                        let k = p[3] as u32;
                        [(p[0] as u32 * k / 255) as u8, (p[1] as u32 * k / 255) as u8, (p[2] as u32 * k / 255) as u8]
                    })
                    .collect(),
            };
            if data.len() != n * 3 {
                return Err(bad("JPEG"));
            }
            Ok(Rgb { width: w, height: h, data })
        }
        Some(Kind::Png) => {
            let mut d = png::Decoder::new_with_limits(data, png::Limits { bytes: (MAX_PIXELS * 8) as usize });
            d.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
            let mut r = d.read_info().map_err(|_| bad("PNG"))?;
            let mut buf = vec![0; r.output_buffer_size()];
            let frame = r.next_frame(&mut buf).map_err(|_| bad("PNG"))?;
            if (frame.width, frame.height) != (w, h) || frame.bit_depth != png::BitDepth::Eight {
                return Err(bad("PNG"));
            }
            let px = &buf[..frame.buffer_size()];
            let data: Vec<u8> = match frame.color_type {
                png::ColorType::Rgb => px.to_vec(),
                png::ColorType::Rgba => px.chunks_exact(4).flat_map(|p| [p[0], p[1], p[2]]).collect(),
                png::ColorType::Grayscale => px.iter().flat_map(|v| [*v, *v, *v]).collect(),
                png::ColorType::GrayscaleAlpha => px.chunks_exact(2).flat_map(|p| [p[0], p[0], p[0]]).collect(),
                png::ColorType::Indexed => return Err(bad("PNG")),
            };
            if data.len() != (w * h * 3) as usize {
                return Err(bad("PNG"));
            }
            Ok(Rgb { width: w, height: h, data })
        }
        None => Err(refuse(415, "unsupported_type", "the image is no JPEG or PNG by its first bytes")),
    }
}

#[cfg(test)]
pub mod tests {
    use super::*;

    /// A PNG of `w` x `h` RGB pixels, encoded with the png crate (uncompressed enough for tests).
    pub fn png_rgb(w: u32, h: u32, px: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        {
            let mut e = png::Encoder::new(&mut out, w, h);
            e.set_color(png::ColorType::Rgb);
            e.set_depth(png::BitDepth::Eight);
            let mut wr = e.write_header().unwrap();
            wr.write_image_data(px).unwrap();
        }
        out
    }

    #[test]
    fn a_png_is_decoded_into_rgb() {
        let px: Vec<u8> = (0..2 * 3 * 3).map(|i| i as u8).collect();
        let img = decode(&png_rgb(2, 3, &px)).unwrap();
        assert_eq!((img.width, img.height), (2, 3));
        assert_eq!(img.data, px);
    }

    #[test]
    fn a_header_that_announces_too_many_pixels_is_refused_before_decoding() {
        let mut big = png_rgb(1, 1, &[1, 2, 3]);
        big[16..20].copy_from_slice(&10_000u32.to_be_bytes());
        big[20..24].copy_from_slice(&10_000u32.to_be_bytes());
        let e = decode(&big).unwrap_err();
        assert_eq!((e.status, e.code), (422, "image_too_large"));
        assert_eq!(decode(b"GIF89a....").unwrap_err().status, 415);
        assert_eq!(decode(b"\xFF\xD8\xFF\xE0garbage").unwrap_err().status, 400);
        // a JPEG's frame header (SOF0) that announces 9000 x 9000
        let mut jpeg = vec![0xFF, 0xD8, 0xFF, 0xC0, 0x00, 0x11, 0x08];
        jpeg.extend_from_slice(&9000u16.to_be_bytes());
        jpeg.extend_from_slice(&9000u16.to_be_bytes());
        jpeg.extend_from_slice(&[3, 1, 0x11, 0, 2, 0x11, 1, 3, 0x11, 1]);
        if let Ok((w, h)) = dimensions(&jpeg) {
            assert_eq!((w, h), (9000, 9000));
            assert_eq!(decode(&jpeg).unwrap_err().status, 422);
        }
    }
}
