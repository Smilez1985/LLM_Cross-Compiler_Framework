//! The CPU path of the preparation: an RGB image to the model's input as its card says (size by letterbox, stretch
//! or centre crop with bilinear scaling, the pad value, colour order, layout, type and, where the model does not carry
//! it, the normalisation), and the way back: a box in the model's input pixels to the source image's pixels (the
//! letterbox's scale and padding undone). RGA does the same on the board's DMA buffers with step 2; this path stays
//! for the boards without it and is the one the tests take.

use crate::card::{Input, InputType, Layout, Resize};
use crate::image::Rgb;

/// How the model's input relates to the source image: input = source * scale + offset (per axis), and the source's
/// size to clamp into.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Mapping {
    pub sx: f32,
    pub sy: f32,
    pub ox: f32,
    pub oy: f32,
    pub width: u32,
    pub height: u32,
}

impl Mapping {
    /// A point of the model's input in the source's pixels, clamped into the image.
    pub fn back(&self, x: f32, y: f32) -> (f32, f32) {
        (
            ((x - self.ox) / self.sx).clamp(0.0, self.width as f32),
            ((y - self.oy) / self.sy).clamp(0.0, self.height as f32),
        )
    }
}

/// Bilinear scaling with half-pixel centres (OpenCV's INTER_LINEAR); an equal size is an exact copy.
fn scale(src: &Rgb, w: u32, h: u32) -> Vec<u8> {
    if (src.width, src.height) == (w, h) {
        return src.data.clone();
    }
    let (sw, sh) = (src.width as usize, src.height as usize);
    let (fx, fy) = (sw as f32 / w as f32, sh as f32 / h as f32);
    let mut out = vec![0u8; (w * h * 3) as usize];
    let axis = |d: usize, f: f32, n: usize| {
        let s = ((d as f32 + 0.5) * f - 0.5).max(0.0);
        let i0 = (s.floor() as usize).min(n - 1);
        let i1 = (i0 + 1).min(n - 1);
        (i0, i1, s - i0 as f32)
    };
    for y in 0..h as usize {
        let (y0, y1, wy) = axis(y, fy, sh);
        for x in 0..w as usize {
            let (x0, x1, wx) = axis(x, fx, sw);
            for c in 0..3 {
                let p = |yy: usize, xx: usize| src.data[(yy * sw + xx) * 3 + c] as f32;
                let top = p(y0, x0) * (1.0 - wx) + p(y0, x1) * wx;
                let bottom = p(y1, x0) * (1.0 - wx) + p(y1, x1) * wx;
                out[(y * w as usize + x) * 3 + c] = (top * (1.0 - wy) + bottom * wy).round().clamp(0.0, 255.0) as u8;
            }
        }
    }
    out
}

/// The input tensor's bytes for `img` and the mapping back. `quant`: the input's zero point and scale where the model
/// takes int8 (from the runtime's input attributes).
pub fn prepare(img: &Rgb, input: &Input, quant: (i32, f32)) -> (Vec<u8>, Mapping) {
    let (iw, ih) = (input.width, input.height);
    let (w, h) = (img.width as f32, img.height as f32);
    // the RGB image at the model's size (HWC) and the mapping
    let (canvas, map) = match input.resize {
        Resize::Stretch => (
            scale(img, iw, ih),
            Mapping { sx: iw as f32 / w, sy: ih as f32 / h, ox: 0.0, oy: 0.0, width: img.width, height: img.height },
        ),
        Resize::Letterbox => {
            let s = (iw as f32 / w).min(ih as f32 / h);
            let (nw, nh) = (((w * s).round() as u32).clamp(1, iw), ((h * s).round() as u32).clamp(1, ih));
            let (px, py) = ((iw - nw) / 2, (ih - nh) / 2);
            let inner = scale(img, nw, nh);
            let mut canvas = vec![input.pad; (iw * ih * 3) as usize];
            for y in 0..nh as usize {
                let row = ((y + py as usize) * iw as usize + px as usize) * 3;
                canvas[row..row + nw as usize * 3]
                    .copy_from_slice(&inner[y * nw as usize * 3..(y + 1) * nw as usize * 3]);
            }
            let (sx, sy) = (nw as f32 / w, nh as f32 / h);
            (canvas, Mapping { sx, sy, ox: px as f32, oy: py as f32, width: img.width, height: img.height })
        }
        Resize::CentreCrop => {
            let s = (iw as f32 / w).max(ih as f32 / h);
            let (nw, nh) = (((w * s).round() as u32).max(iw), ((h * s).round() as u32).max(ih));
            let (cx, cy) = ((nw - iw) / 2, (nh - ih) / 2);
            let big = scale(img, nw, nh);
            let mut canvas = vec![0u8; (iw * ih * 3) as usize];
            for y in 0..ih as usize {
                let from = ((y + cy as usize) * nw as usize + cx as usize) * 3;
                canvas[y * iw as usize * 3..(y + 1) * iw as usize * 3]
                    .copy_from_slice(&big[from..from + iw as usize * 3]);
            }
            let (sx, sy) = (nw as f32 / w, nh as f32 / h);
            (canvas, Mapping { sx, sy, ox: -(cx as f32), oy: -(cy as f32), width: img.width, height: img.height })
        }
    };
    let n = (iw * ih) as usize;
    let order: [usize; 3] = if input.bgr { [2, 1, 0] } else { [0, 1, 2] };
    let value = |p: usize, c: usize| -> f32 {
        let v = canvas[p * 3 + order[c]] as f32;
        if input.normalised_in_model {
            v
        } else {
            (v - input.mean[c]) / input.std[c]
        }
    };
    let index = |p: usize, c: usize| match input.layout {
        Layout::Nhwc => p * 3 + c,
        Layout::Nchw => c * n + p,
    };
    let mut out = vec![0u8; n * 3 * if input.kind == InputType::Float32 { 4 } else { 1 }];
    for p in 0..n {
        for c in 0..3 {
            let i = index(p, c);
            let v = value(p, c);
            match input.kind {
                InputType::Uint8 => out[i] = v.round().clamp(0.0, 255.0) as u8,
                InputType::Int8 => {
                    let (zp, s) = quant;
                    let q = if s > 0.0 { (v / s).round() as i32 + zp } else { v.round() as i32 };
                    out[i] = q.clamp(-128, 127) as i8 as u8;
                }
                InputType::Float32 => out[4 * i..4 * i + 4].copy_from_slice(&v.to_le_bytes()),
            }
        }
    }
    (out, map)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn input(resize: Resize, layout: Layout, w: u32, h: u32) -> Input {
        Input {
            width: w,
            height: h,
            layout,
            bgr: false,
            resize,
            pad: 114,
            mean: [0.0; 3],
            std: [1.0; 3],
            normalised_in_model: true,
            kind: InputType::Uint8,
        }
    }

    fn solid(w: u32, h: u32, rgb: [u8; 3]) -> Rgb {
        Rgb { width: w, height: h, data: (0..w * h).flat_map(|_| rgb).collect() }
    }

    #[test]
    fn an_image_of_the_models_size_goes_in_unchanged() {
        let img = Rgb { width: 2, height: 2, data: (0..12).collect() };
        let (t, m) = prepare(&img, &input(Resize::Letterbox, Layout::Nhwc, 2, 2), (0, 1.0));
        assert_eq!(t, img.data);
        assert_eq!(m.back(1.0, 1.0), (1.0, 1.0));
        let (t, _) = prepare(&img, &input(Resize::Letterbox, Layout::Nchw, 2, 2), (0, 1.0));
        assert_eq!(t, vec![0, 3, 6, 9, 1, 4, 7, 10, 2, 5, 8, 11]);
        let mut bgr = input(Resize::Stretch, Layout::Nhwc, 2, 2);
        bgr.bgr = true;
        assert_eq!(&prepare(&img, &bgr, (0, 1.0)).0[..3], &[2, 1, 0]);
    }

    #[test]
    fn the_letterbox_pads_and_maps_back() {
        // 8 x 4 into 4 x 4: scaled to 4 x 2, one row of padding above and below
        let img = solid(8, 4, [10, 20, 30]);
        let (t, m) = prepare(&img, &input(Resize::Letterbox, Layout::Nhwc, 4, 4), (0, 1.0));
        assert_eq!(&t[..3], &[114, 114, 114]);
        assert_eq!(&t[4 * 3..4 * 3 + 3], &[10, 20, 30]);
        assert_eq!(&t[3 * 4 * 3..3 * 4 * 3 + 3], &[114, 114, 114]);
        assert_eq!(m.back(0.0, 1.0), (0.0, 0.0));
        assert_eq!(m.back(4.0, 3.0), (8.0, 4.0));
        assert_eq!(m.back(-5.0, 9.0), (0.0, 4.0), "clamped into the image");
    }

    #[test]
    fn centre_crop_stretch_normalisation_and_types() {
        let img = solid(8, 4, [100, 50, 0]);
        let (_, m) = prepare(&img, &input(Resize::CentreCrop, Layout::Nhwc, 4, 4), (0, 1.0));
        assert_eq!(m.back(0.0, 0.0), (2.0, 0.0));
        let (_, m) = prepare(&img, &input(Resize::Stretch, Layout::Nhwc, 4, 4), (0, 1.0));
        assert_eq!(m.back(4.0, 4.0), (8.0, 4.0));
        let mut f = input(Resize::Stretch, Layout::Nhwc, 1, 1);
        f.normalised_in_model = false;
        f.mean = [50.0, 50.0, 50.0];
        f.std = [50.0, 25.0, 1.0];
        f.kind = InputType::Float32;
        let (t, _) = prepare(&solid(1, 1, [100, 50, 0]), &f, (0, 1.0));
        let v: Vec<f32> = t.chunks_exact(4).map(|b| f32::from_le_bytes(b.try_into().unwrap())).collect();
        assert_eq!(v, vec![1.0, 0.0, -50.0]);
        f.kind = InputType::Int8;
        let (t, _) = prepare(&solid(1, 1, [100, 50, 0]), &f, (-10, 0.5));
        assert_eq!(t, vec![(2 - 10) as i8 as u8, (-10i8) as u8, (-110i8) as u8]);
    }
}
