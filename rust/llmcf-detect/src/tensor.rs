//! An output of the NPU as the post-processing reads it: one layout helper (every kind indexes through `at`, so a
//! kind works in NCHW and in NHWC alike) and the quantised domain (a threshold is turned once into the output's own
//! integer type with its scale and zero point, so the many candidates below it are dropped without becoming floating
//! point; only those above it are dequantised).

use crate::npu::{Attr, DType, Layout};

pub struct Tensor<'a> {
    /// the logical shape: [n, c, h, w] for four dimensions, else the dimensions as they are
    pub shape: Vec<usize>,
    layout: Layout,
    dtype: DType,
    zp: i32,
    scale: f32,
    data: &'a [u8],
}

/// A threshold in an output's own domain: an integer for a quantised output, a float for a float output.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Thr {
    Int(i32),
    Float(f32),
}

fn f16_to_f32(h: u16) -> f32 {
    let sign = if h & 0x8000 != 0 { -1.0 } else { 1.0 };
    let exp = ((h >> 10) & 0x1f) as i32;
    let frac = (h & 0x3ff) as f32;
    match exp {
        0 => sign * frac * 2f32.powi(-24),
        31 if frac == 0.0 => sign * f32::INFINITY,
        31 => f32::NAN,
        _ => sign * (1.0 + frac / 1024.0) * 2f32.powi(exp - 15),
    }
}

impl<'a> Tensor<'a> {
    /// The view of `data` with `attr`'s shape, type and quantisation; None when the bytes do not fit the shape.
    pub fn new(attr: &Attr, data: &'a [u8]) -> Option<Tensor<'a>> {
        if data.len() != attr.elems() * attr.dtype.size() {
            return None;
        }
        Some(Tensor {
            shape: attr.logical().iter().map(|d| *d as usize).collect(),
            layout: attr.layout,
            dtype: attr.dtype,
            zp: attr.zp,
            scale: attr.scale,
            data,
        })
    }

    /// The flat index of the logical position (c, h, w) of batch 0 of a four-dimensional tensor.
    pub fn at(&self, c: usize, h: usize, w: usize) -> usize {
        let (cs, hs, ws) = (self.shape[1], self.shape[2], self.shape[3]);
        match self.layout {
            Layout::Nhwc => (h * ws + w) * cs + c,
            _ => (c * hs + h) * ws + w,
        }
    }

    /// The flat index of (row, col) of a [1, rows, cols] tensor, or of (col, row) when `transposed` ([1, cols, rows]).
    pub fn at2(&self, row: usize, col: usize, transposed: bool) -> usize {
        if transposed {
            col * self.shape[2] + row
        } else {
            row * self.shape[2] + col
        }
    }

    /// The raw integer at flat index `i` (a quantised output), None for a float output.
    pub fn raw(&self, i: usize) -> Option<i32> {
        match self.dtype {
            DType::I8 => Some(self.data[i] as i8 as i32),
            DType::U8 => Some(self.data[i] as i32),
            DType::I16 => Some(i16::from_le_bytes([self.data[2 * i], self.data[2 * i + 1]]) as i32),
            DType::I32 => Some(i32::from_le_bytes(self.data[4 * i..4 * i + 4].try_into().ok()?)),
            DType::F32 | DType::F16 => None,
        }
    }

    /// The value at flat index `i` as a float (dequantised: (q - zp) * scale).
    pub fn get(&self, i: usize) -> f32 {
        match self.dtype {
            DType::F32 => f32::from_le_bytes(self.data[4 * i..4 * i + 4].try_into().unwrap_or([0; 4])),
            DType::F16 => f16_to_f32(u16::from_le_bytes([self.data[2 * i], self.data[2 * i + 1]])),
            _ => self.deq(self.raw(i).unwrap_or(0)),
        }
    }

    fn deq(&self, q: i32) -> f32 {
        (q - self.zp) as f32 * self.scale
    }

    fn range(&self) -> (i32, i32) {
        match self.dtype {
            DType::I8 => (-128, 127),
            DType::U8 => (0, 255),
            DType::I16 => (-32768, 32767),
            _ => (i32::MIN, i32::MAX),
        }
    }

    /// `value` in this output's own domain: the smallest raw integer whose dequantised value is at least `value`
    /// (so "raw >= it" is exactly "dequantised >= value"), or the float itself.
    pub fn threshold(&self, value: f32) -> Thr {
        if matches!(self.dtype, DType::F32 | DType::F16) || self.scale <= 0.0 {
            return Thr::Float(value);
        }
        let (lo, hi) = self.range();
        let guess = (value / self.scale + self.zp as f32).floor();
        let mut q = guess.clamp(lo as f32, hi as f32) as i64;
        while q > lo as i64 && self.deq((q - 1) as i32) >= value {
            q -= 1;
        }
        while q <= hi as i64 && self.deq(q as i32) < value {
            q += 1;
        }
        // above the type's range: nothing passes (hi + 1 cannot be reached by a raw value)
        Thr::Int(q.min(i32::MAX as i64) as i32)
    }

    /// Whether the value at flat index `i` is at least the threshold `t` (made by `threshold` for this output).
    pub fn passes(&self, i: usize, t: Thr) -> bool {
        match (t, self.raw(i)) {
            (Thr::Int(q), Some(r)) => r >= q,
            (Thr::Float(f), None) => self.get(i) >= f,
            (Thr::Float(f), Some(_)) => self.get(i) >= f,
            (Thr::Int(_), None) => false,
        }
    }

    /// Whether the raw value at `a` is above the one at `b` (the cheap comparison of the class search).
    pub fn greater(&self, a: usize, b: usize) -> bool {
        match (self.raw(a), self.raw(b)) {
            (Some(x), Some(y)) => x > y && self.scale > 0.0 || x < y && self.scale < 0.0,
            _ => self.get(a) > self.get(b),
        }
    }
}

pub fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x).exp())
}

/// The inverse of the sigmoid (a score threshold turned into the logit an output holds when its activation is applied
/// on the board).
pub fn logit(p: f32) -> f32 {
    let p = p.clamp(1e-7, 1.0 - 1e-7);
    (p / (1.0 - p)).ln()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn attr(dims: Vec<u32>, layout: Layout, dtype: DType, zp: i32, scale: f32) -> Attr {
        Attr { index: 0, name: "t".into(), dims, layout, dtype, zp, scale }
    }

    #[test]
    fn the_layout_helper_reads_nchw_and_nhwc_alike() {
        // logical [1, 2, 2, 3]: value = 100*c + 10*h + w
        let mut nchw = Vec::new();
        for c in 0..2 {
            for h in 0..2 {
                for w in 0..3 {
                    nchw.push((100 * c + 10 * h + w) as u8);
                }
            }
        }
        let mut nhwc = Vec::new();
        for h in 0..2 {
            for w in 0..3 {
                for c in 0..2 {
                    nhwc.push((100 * c + 10 * h + w) as u8);
                }
            }
        }
        let a = attr(vec![1, 2, 2, 3], Layout::Nchw, DType::U8, 0, 1.0);
        let b = attr(vec![1, 2, 3, 2], Layout::Nhwc, DType::U8, 0, 1.0);
        let (ta, tb) = (Tensor::new(&a, &nchw).unwrap(), Tensor::new(&b, &nhwc).unwrap());
        assert_eq!(ta.shape, tb.shape);
        for c in 0..2 {
            for h in 0..2 {
                for w in 0..3 {
                    assert_eq!(ta.get(ta.at(c, h, w)), tb.get(tb.at(c, h, w)));
                }
            }
        }
        assert!(Tensor::new(&a, &nchw[1..]).is_none(), "bytes that do not fit the shape");
    }

    #[test]
    fn a_threshold_in_the_quantised_domain_is_exact_on_its_edge() {
        let a = attr(vec![1, 256], Layout::Flat, DType::I8, -128, 1.0 / 255.0);
        let data: Vec<u8> = (0..256i32).map(|q| (q - 128) as i8 as u8).collect();
        let t = Tensor::new(&a, &data).unwrap();
        for value in [0.0f32, 0.25, 0.5, 64.0 / 255.0, 1.0, 1.5] {
            let thr = t.threshold(value);
            for i in 0..256 {
                assert_eq!(t.passes(i, thr), t.get(i) >= value, "value {value}, index {i}");
            }
        }
        let f = attr(vec![1, 2], Layout::Flat, DType::F32, 0, 1.0);
        let bytes: Vec<u8> = [0.3f32, 0.7].iter().flat_map(|v| v.to_le_bytes()).collect();
        let t = Tensor::new(&f, &bytes).unwrap();
        assert_eq!(t.threshold(0.5), Thr::Float(0.5));
        assert!(!t.passes(0, Thr::Float(0.5)) && t.passes(1, Thr::Float(0.5)));
    }

    #[test]
    fn half_floats_and_the_logit() {
        assert_eq!(f16_to_f32(0x3c00), 1.0);
        assert_eq!(f16_to_f32(0xc000), -2.0);
        assert!((sigmoid(logit(0.3)) - 0.3).abs() < 1e-6);
    }
}
