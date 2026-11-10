//! The post-processing: one function per kind behind one signature (the model's outputs with their attributes and the
//! card's parameters in, detections in the model's input pixels out), pure code without I/O, so every kind is tested
//! on tensors built by hand without an NPU. The six detection kinds of step 1 follow Rockchip's model zoo (v2.5.0,
//! `examples/*/cpp/postprocess.cc`): yolo-anchor (YOLOv5, v7), yolo-anchorfree-obj (YOLOX), yolo-dfl (YOLOv6, v8,
//! 11, PP-YOLOE), yolo-nms-free (YOLOv10 with DFL branches, YOLO26 with decoded boxes; top-K without NMS),
//! device-nms (NMS compiled into the model) and ssd-prior (SSD and RetinaFace against prior boxes, SCRFD by distances
//! from anchor centres; landmarks where the model has them).
//!
//! Every score threshold is compared in the output's quantised domain first (tensor.rs), through the inverse sigmoid
//! where the card says the activation is applied on the board. NMS is per class, greedy, by score with ties broken by
//! the candidate's order (the order of the outputs, cells and anchors), so equal inputs give equal results.

use crate::card::{Activation, Card, Post};
use crate::tensor::{logit, sigmoid, Tensor, Thr};

/// One detection in the model's input pixels (x1, y1, x2, y2), with its class, score and landmarks.
#[derive(Clone, Debug, PartialEq)]
pub struct Det {
    pub class: usize,
    pub score: f32,
    pub b: [f32; 4],
    pub keypoints: Vec<(f32, f32)>,
}

/// The decisions of use for one request (the card's thresholds, the block's or the request's options within them).
#[derive(Clone, Debug, PartialEq)]
pub struct Use {
    pub score: f32,
    pub nms_iou: f32,
    pub max_results: usize,
    /// the classes asked for (None: all)
    pub classes: Option<Vec<bool>>,
}

impl Use {
    fn wants(&self, class: usize) -> bool {
        self.classes.as_ref().is_none_or(|c| c.get(class).copied().unwrap_or(false))
    }
}

type R<T> = Result<T, String>;

/// The outputs of one role, in output order.
fn by_role<'a, 'b>(outs: &'b [Tensor<'a>], roles: &[Option<String>], role: &str) -> Vec<&'b Tensor<'a>> {
    outs.iter().zip(roles).filter(|(_, r)| r.as_deref() == Some(role)).map(|(t, _)| t).collect()
}

/// The stride of a head of grid h x w, checked against the card's strides and the input size.
fn stride_of(t: &Tensor, card: &Card, strides: &[u32]) -> R<u32> {
    if t.shape.len() != 4 || t.shape[0] != 1 {
        return Err(format!("a head has the shape {:?}; [1, c, h, w] is needed", t.shape));
    }
    let (h, w) = (t.shape[2] as u32, t.shape[3] as u32);
    let s = card.input.height / h.max(1);
    if s * h != card.input.height || s * w != card.input.width || !strides.contains(&s) {
        return Err(format!("a head of {h} x {w} cells has none of the card's strides {strides:?}"));
    }
    Ok(s)
}

/// A score threshold for an output whose scores are `act` (model: as they are; sigmoid: logits).
fn score_thr(t: &Tensor, score: f32, act: Activation) -> Thr {
    t.threshold(if act == Activation::Sigmoid { logit(score) } else { score })
}

fn act(v: f32, a: Activation) -> f32 {
    if a == Activation::Sigmoid {
        sigmoid(v)
    } else {
        v
    }
}

/// Decodes the outputs by the card's kind; the detections in input pixels, after NMS (or top-K) and the cap.
pub fn decode(card: &Card, roles: &[Option<String>], outs: &[Tensor], u: &Use) -> R<Vec<Det>> {
    let a = card.activation;
    let dets = match &card.post {
        Post::YoloAnchor { strides, anchors } => {
            let mut c = Vec::new();
            for t in by_role(outs, roles, "boxes") {
                let s = stride_of(t, card, strides)?;
                let k = strides.iter().position(|x| *x == s).unwrap_or(0);
                yolo_head(t, s, Some(&anchors[k]), card.labels.len(), a, u, &mut c)?;
            }
            nms(c, u)
        }
        Post::YoloAnchorFree { strides } => {
            let mut c = Vec::new();
            for t in by_role(outs, roles, "boxes") {
                let s = stride_of(t, card, strides)?;
                yolo_head(t, s, None, card.labels.len(), a, u, &mut c)?;
            }
            nms(c, u)
        }
        Post::YoloDfl { strides, reg_max } => nms(dfl(card, roles, outs, strides, *reg_max, a, u)?, u),
        Post::YoloNmsFree { dfl: Some((strides, reg_max)), top_k } => {
            top(dfl(card, roles, outs, strides, *reg_max, a, u)?, *top_k as usize, u)
        }
        Post::YoloNmsFree { dfl: None, top_k } => top(decoded(card, roles, outs, a, u)?, *top_k as usize, u),
        Post::DeviceNms { yxyx, normalized } => top(device(card, roles, outs, *yxyx, *normalized, u)?, usize::MAX, u),
        Post::SsdPrior { .. } => nms(ssd(card, roles, outs, u)?, u),
    };
    Ok(dets)
}

/// One YOLO head [1, A*(5+C), h, w] (A anchors; YOLOX: A = 1 and no anchors): objectness first in the quantised
/// domain, then the best class; score = objectness x class.
fn yolo_head(
    t: &Tensor,
    s: u32,
    anchors: Option<&[(f32, f32)]>,
    classes: usize,
    a: Activation,
    u: &Use,
    out: &mut Vec<Det>,
) -> R<()> {
    let per = 5 + classes;
    let na = anchors.map_or(1, <[_]>::len);
    if t.shape[1] != na * per {
        return Err(format!(
            "a head has {} channels; {} anchors x (5 + {classes} classes) are {}",
            t.shape[1],
            na,
            na * per
        ));
    }
    let thr = score_thr(t, u.score, a);
    let sf = s as f32;
    for gy in 0..t.shape[2] {
        for gx in 0..t.shape[3] {
            for k in 0..na {
                let base = k * per;
                if !t.passes(t.at(base + 4, gy, gx), thr) {
                    continue;
                }
                let mut best = 0;
                for c in 1..classes {
                    if t.greater(t.at(base + 5 + c, gy, gx), t.at(base + 5 + best, gy, gx)) {
                        best = c;
                    }
                }
                let score = act(t.get(t.at(base + 4, gy, gx)), a) * act(t.get(t.at(base + 5 + best, gy, gx)), a);
                if score < u.score || !u.wants(best) {
                    continue;
                }
                let v = |c: usize| t.get(t.at(base + c, gy, gx));
                let (cx, cy, w, h) = match anchors {
                    Some(an) => {
                        let (x, y, w, h) = (act(v(0), a), act(v(1), a), act(v(2), a), act(v(3), a));
                        (
                            (x * 2.0 - 0.5 + gx as f32) * sf,
                            (y * 2.0 - 0.5 + gy as f32) * sf,
                            (w * 2.0).powi(2) * an[k].0,
                            (h * 2.0).powi(2) * an[k].1,
                        )
                    }
                    None => ((v(0) + gx as f32) * sf, (v(1) + gy as f32) * sf, v(2).exp() * sf, v(3).exp() * sf),
                };
                out.push(Det {
                    class: best,
                    score,
                    b: [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0],
                    keypoints: Vec::new(),
                });
            }
        }
    }
    Ok(())
}

/// The DFL branches (YOLOv6, v8, 11, PP-YOLOE, v10): per stride a box branch [1, 4*reg_max, h, w], a score branch
/// [1, C, h, w] and an optional score sum [1, 1, h, w] (a cell whose sum is below the threshold has no class above
/// it). The box is the expected distance of each side (softmax over the bins; reg_max 1: the distance itself).
fn dfl(
    card: &Card,
    roles: &[Option<String>],
    outs: &[Tensor],
    strides: &[u32],
    reg: u32,
    a: Activation,
    u: &Use,
) -> R<Vec<Det>> {
    let boxes = by_role(outs, roles, "boxes");
    let scores = by_role(outs, roles, "scores");
    let sums = by_role(outs, roles, "score_sum");
    let classes = card.labels.len();
    let reg = reg as usize;
    let mut out = Vec::new();
    for bt in &boxes {
        let s = stride_of(bt, card, strides)?;
        let same = |t: &&&Tensor| t.shape.len() == 4 && t.shape[2] == bt.shape[2] && t.shape[3] == bt.shape[3];
        let st = scores.iter().find(same).ok_or_else(|| format!("no score branch for the stride {s}"))?;
        let sum = sums.iter().find(same);
        if bt.shape[1] != 4 * reg || st.shape[1] != classes {
            return Err(format!(
                "the branches of stride {s} have {} and {} channels; 4 x {reg} and {classes} are needed",
                bt.shape[1], st.shape[1]
            ));
        }
        let thr = score_thr(st, u.score, a);
        let sum_thr = sum.map(|t| score_thr(t, u.score, a));
        let sf = s as f32;
        let mut bins = vec![0f32; reg];
        for gy in 0..bt.shape[2] {
            for gx in 0..bt.shape[3] {
                if let (Some(t), Some(q)) = (sum, sum_thr) {
                    if !t.passes(t.at(0, gy, gx), q) {
                        continue;
                    }
                }
                let mut best = 0;
                for c in 1..classes {
                    if st.greater(st.at(c, gy, gx), st.at(best, gy, gx)) {
                        best = c;
                    }
                }
                if !st.passes(st.at(best, gy, gx), thr) {
                    continue;
                }
                let score = act(st.get(st.at(best, gy, gx)), a);
                if score < u.score || !u.wants(best) {
                    continue;
                }
                let mut d = [0f32; 4];
                for (side, dist) in d.iter_mut().enumerate() {
                    if reg == 1 {
                        *dist = bt.get(bt.at(side, gy, gx));
                        continue;
                    }
                    let mut max = f32::MIN;
                    for (i, b) in bins.iter_mut().enumerate() {
                        *b = bt.get(bt.at(side * reg + i, gy, gx));
                        max = max.max(*b);
                    }
                    let (mut sum_e, mut acc) = (0f32, 0f32);
                    for (i, b) in bins.iter().enumerate() {
                        let e = (b - max).exp();
                        sum_e += e;
                        acc += e * i as f32;
                    }
                    *dist = acc / sum_e;
                }
                let (cx, cy) = (gx as f32 + 0.5, gy as f32 + 0.5);
                out.push(Det {
                    class: best,
                    score,
                    b: [(cx - d[0]) * sf, (cy - d[1]) * sf, (cx + d[2]) * sf, (cy + d[3]) * sf],
                    keypoints: Vec::new(),
                });
            }
        }
    }
    Ok(out)
}

/// Whether a [1, a, b] output lies as [1, 4 or C, N] (transposed: the anchors last, as YOLO26's) or [1, N, 4 or C].
fn rows(t: &Tensor, width: usize) -> R<(usize, bool)> {
    match t.shape.as_slice() {
        [1, a, b] if *a == width && *b != width => Ok((*b, true)),
        [1, a, b] if *b == width => Ok((*a, false)),
        s => Err(format!("an output of the shape {s:?} has no dimension of {width}")),
    }
}

/// YOLO26: boxes [1, 4, N] decoded as x1, y1, x2, y2 in input pixels and scores [1, C, N] (either may lie the other
/// way round), the best class of each anchor against the threshold.
fn decoded(card: &Card, roles: &[Option<String>], outs: &[Tensor], a: Activation, u: &Use) -> R<Vec<Det>> {
    let classes = card.labels.len();
    let (Some(bt), Some(st)) =
        (by_role(outs, roles, "boxes").first().copied(), by_role(outs, roles, "scores").first().copied())
    else {
        return Err("yolo-nms-free with decoded boxes needs an output of boxes and one of scores".into());
    };
    let (n, bt_t) = rows(bt, 4)?;
    let (m, st_t) = rows(st, classes)?;
    if n != m {
        return Err(format!("{n} boxes and {m} rows of scores"));
    }
    let thr = score_thr(st, u.score, a);
    let mut out = Vec::new();
    for i in 0..n {
        let mut best = 0;
        for c in 1..classes {
            if st.greater(st.at2(i, c, st_t), st.at2(i, best, st_t)) {
                best = c;
            }
        }
        if !st.passes(st.at2(i, best, st_t), thr) {
            continue;
        }
        let score = act(st.get(st.at2(i, best, st_t)), a);
        if score < u.score || !u.wants(best) {
            continue;
        }
        let b = |k: usize| bt.get(bt.at2(i, k, bt_t));
        out.push(Det { class: best, score, b: [b(0), b(1), b(2), b(3)], keypoints: Vec::new() });
    }
    Ok(out)
}

/// NMS in the model: boxes [1, N, 4] and scores [1, N, C] as the model gives them, already suppressed.
fn device(
    card: &Card,
    roles: &[Option<String>],
    outs: &[Tensor],
    yxyx: bool,
    normalized: bool,
    u: &Use,
) -> R<Vec<Det>> {
    let (Some(bt), Some(st)) =
        (by_role(outs, roles, "boxes").first().copied(), by_role(outs, roles, "scores").first().copied())
    else {
        return Err("device-nms needs an output of boxes and one of scores".into());
    };
    let classes = card.labels.len();
    let (n, bt_t) = rows(bt, 4)?;
    let (m, st_t) = rows(st, classes)?;
    if n != m {
        return Err(format!("{n} boxes and {m} rows of scores"));
    }
    let thr = st.threshold(u.score);
    let (w, h) = if normalized { (card.input.width as f32, card.input.height as f32) } else { (1.0, 1.0) };
    let mut out = Vec::new();
    for i in 0..n {
        let mut best = 0;
        for c in 1..classes {
            if st.greater(st.at2(i, c, st_t), st.at2(i, best, st_t)) {
                best = c;
            }
        }
        if !st.passes(st.at2(i, best, st_t), thr) || !u.wants(best) {
            continue;
        }
        let v = |k: usize| bt.get(bt.at2(i, k, bt_t));
        let b = if yxyx { [v(1) * w, v(0) * h, v(3) * w, v(2) * h] } else { [v(0) * w, v(1) * h, v(2) * w, v(3) * h] };
        out.push(Det { class: best, score: st.get(st.at2(i, best, st_t)), b, keypoints: Vec::new() });
    }
    Ok(out)
}

/// The prior boxes in the order of the steps, cells and sizes: (cx, cy, w, h) in input pixels (RetinaFace's
/// generator: the cell's centre, the size's width and height; SCRFD's anchor centres sit on the cell's corner).
pub fn priors(card: &Card) -> Vec<[f32; 4]> {
    let Post::SsdPrior { distance, steps, min_sizes, .. } = &card.post else { return Vec::new() };
    let (iw, ih) = (card.input.width as f32, card.input.height as f32);
    let mut out = Vec::new();
    for (s, sizes) in steps.iter().zip(min_sizes) {
        let sf = *s as f32;
        let (fw, fh) = ((iw / sf).ceil() as usize, (ih / sf).ceil() as usize);
        for y in 0..fh {
            for x in 0..fw {
                for m in sizes {
                    let off = if *distance { 0.0 } else { 0.5 };
                    out.push([(x as f32 + off) * sf, (y as f32 + off) * sf, *m, *m]);
                }
            }
        }
    }
    out
}

/// One output of a role, or several concatenated along their rows (SCRFD gives one per stride): (tensor, row) pairs.
fn concat<'b, 'a>(ts: &[&'b Tensor<'a>], width: usize) -> R<Vec<(&'b Tensor<'a>, usize, bool)>> {
    let mut out = Vec::new();
    for t in ts {
        let (n, tr) = rows(t, width)?;
        out.extend((0..n).map(|i| (*t, i, tr)));
    }
    Ok(out)
}

/// SSD, RetinaFace and SCRFD: boxes [1, N, 4], scores [1, N, C] (class 0 the background where the card says so) and
/// landmarks [1, N, 2K], each possibly in several outputs taken in their order, against the prior boxes.
fn ssd(card: &Card, roles: &[Option<String>], outs: &[Tensor], u: &Use) -> R<Vec<Det>> {
    let Post::SsdPrior { distance, variances, clip, background, keypoints, steps, .. } = &card.post else {
        return Ok(Vec::new());
    };
    let classes = card.labels.len() + usize::from(*background);
    let boxes = concat(&by_role(outs, roles, "boxes"), 4)?;
    let scores = concat(&by_role(outs, roles, "scores"), classes)?;
    let kps =
        if *keypoints > 0 { concat(&by_role(outs, roles, "keypoints"), 2 * *keypoints as usize)? } else { Vec::new() };
    let pri = priors(card);
    if boxes.len() != pri.len() || scores.len() != pri.len() || *keypoints > 0 && kps.len() != pri.len() {
        return Err(format!(
            "{} boxes, {} rows of scores and {} prior boxes; the card's steps and sizes must give one prior per box",
            boxes.len(),
            scores.len(),
            pri.len()
        ));
    }
    let (iw, ih) = (card.input.width as f32, card.input.height as f32);
    let first = usize::from(*background);
    let a = card.activation;
    let mut out = Vec::new();
    // anchors per cell of each step, to find a row's stride for the distance encoding
    let mut stride_of_row = Vec::with_capacity(pri.len());
    if let Post::SsdPrior { min_sizes, .. } = &card.post {
        for (s, sizes) in steps.iter().zip(min_sizes) {
            let n = ((iw / *s as f32).ceil() * (ih / *s as f32).ceil()) as usize * sizes.len();
            stride_of_row.extend(std::iter::repeat_n(*s as f32, n));
        }
    }
    for (i, p) in pri.iter().enumerate() {
        let (st, si, st_t) = scores[i];
        let raw: Vec<f32> = (0..classes).map(|c| st.get(st.at2(si, c, st_t))).collect();
        let probs: Vec<f32> = match a {
            Activation::Softmax => {
                let max = raw.iter().copied().fold(f32::MIN, f32::max);
                let e: Vec<f32> = raw.iter().map(|v| (v - max).exp()).collect();
                let sum: f32 = e.iter().sum();
                e.iter().map(|v| v / sum).collect()
            }
            Activation::Sigmoid => raw.iter().map(|v| sigmoid(*v)).collect(),
            Activation::Model => raw,
        };
        let mut best = first;
        for c in first + 1..classes {
            if probs[c] > probs[best] {
                best = c;
            }
        }
        let score = probs[best];
        let label = best - first;
        if score < u.score || !u.wants(label) {
            continue;
        }
        let (bt, bi, bt_t) = boxes[i];
        let l = |k: usize| bt.get(bt.at2(bi, k, bt_t));
        let mut b = if *distance {
            let s = stride_of_row[i];
            [p[0] - l(0) * s, p[1] - l(1) * s, p[0] + l(2) * s, p[1] + l(3) * s]
        } else {
            let cx = p[0] + l(0) * variances[0] * p[2];
            let cy = p[1] + l(1) * variances[0] * p[3];
            let w = p[2] * (l(2) * variances[1]).exp();
            let h = p[3] * (l(3) * variances[1]).exp();
            [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0]
        };
        let mut points = Vec::new();
        if *keypoints > 0 {
            let (kt, ki, kt_t) = kps[i];
            for k in 0..*keypoints as usize {
                let (x, y) = (kt.get(kt.at2(ki, 2 * k, kt_t)), kt.get(kt.at2(ki, 2 * k + 1, kt_t)));
                points.push(if *distance {
                    let s = stride_of_row[i];
                    (p[0] + x * s, p[1] + y * s)
                } else {
                    (p[0] + x * variances[0] * p[2], p[1] + y * variances[0] * p[3])
                });
            }
        }
        if *clip {
            b = [b[0].clamp(0.0, iw), b[1].clamp(0.0, ih), b[2].clamp(0.0, iw), b[3].clamp(0.0, ih)];
        }
        out.push(Det { class: label, score, b, keypoints: points });
    }
    Ok(out)
}

fn iou(a: &[f32; 4], b: &[f32; 4]) -> f32 {
    let w = (a[2].min(b[2]) - a[0].max(b[0])).max(0.0);
    let h = (a[3].min(b[3]) - a[1].max(b[1])).max(0.0);
    let inter = w * h;
    let union =
        (a[2] - a[0]).max(0.0) * (a[3] - a[1]).max(0.0) + (b[2] - b[0]).max(0.0) * (b[3] - b[1]).max(0.0) - inter;
    if union <= 0.0 {
        0.0
    } else {
        inter / union
    }
}

/// By score, highest first; equal scores keep the candidates' order (a stable sort).
fn ranked(mut c: Vec<Det>) -> Vec<Det> {
    c.sort_by(|a, b| b.score.total_cmp(&a.score));
    c
}

/// Greedy NMS per class: a candidate whose IoU with a kept one of its class is above the threshold is dropped.
fn nms(c: Vec<Det>, u: &Use) -> Vec<Det> {
    let mut kept: Vec<Det> = Vec::new();
    for d in ranked(c) {
        if kept.len() >= u.max_results {
            break;
        }
        if kept.iter().all(|k| k.class != d.class || iou(&k.b, &d.b) <= u.nms_iou) {
            kept.push(d);
        }
    }
    kept
}

/// The best `k` without NMS, within the cap.
fn top(c: Vec<Det>, k: usize, u: &Use) -> Vec<Det> {
    let mut r = ranked(c);
    r.truncate(k.min(u.max_results));
    r
}

#[cfg(test)]
pub mod tests {
    use super::*;
    use crate::card;
    use crate::npu::{Attr, DType, Layout};

    pub const LABELS: [&str; 3] = ["person", "car", "dog"];

    pub fn card_json(post: &str, outputs: &str, activation: &str, w: u32, h: u32, labels: &[&str]) -> String {
        format!(
            r#"{{"schema":"llmcf-model-card/1","model":{{"name":"test","licence":"mit"}},
            "rknn":{{"file":"m.rknn","sha256":"{}","toolkit":"2.5.0","platform":"rk3566","quantisation":"w8a8"}},
            "input":{{"width":{w},"height":{h},"layout":"nhwc","colour":"rgb","resize":"letterbox","mean":[0,0,0],
            "std":[255,255,255],"normalised_in_model":true,"type":"uint8"}},"task":"detect","postprocess":{post},
            "labels":{},"thresholds":{{"score":0.5,"nms_iou":0.45,"max_results":10}},"score_activation":"{activation}",
            "outputs":{outputs}}}"#,
            "0".repeat(64),
            serde_json::to_string(labels).unwrap()
        )
    }

    pub fn card_of(post: &str, outputs: &str, activation: &str, w: u32, h: u32) -> Card {
        card::parse(card_json(post, outputs, activation, w, h, &LABELS).as_bytes()).unwrap()
    }

    /// A quantised output built by hand: values put in by their logical position, stored in `layout`, int8 with the
    /// given scale and zero point (every value not set holds the zero point's floor: -128).
    pub struct Built {
        pub attr: Attr,
        pub data: Vec<u8>,
    }

    impl Built {
        pub fn q4(index: u32, c: usize, h: usize, w: usize, layout: Layout, zp: i32, scale: f32) -> Built {
            let dims = if layout == Layout::Nhwc {
                vec![1, h as u32, w as u32, c as u32]
            } else {
                vec![1, c as u32, h as u32, w as u32]
            };
            Built {
                attr: Attr { index, name: format!("o{index}"), dims, layout, dtype: DType::I8, zp, scale },
                data: vec![0x80; c * h * w],
            }
        }
        pub fn q3(index: u32, a: usize, b: usize, zp: i32, scale: f32) -> Built {
            Built {
                attr: Attr {
                    index,
                    name: format!("o{index}"),
                    dims: vec![1, a as u32, b as u32],
                    layout: Layout::Flat,
                    dtype: DType::I8,
                    zp,
                    scale,
                },
                data: vec![0x80; a * b],
            }
        }
        fn q(&self, v: f32) -> u8 {
            ((v / self.attr.scale).round() as i32 + self.attr.zp).clamp(-128, 127) as i8 as u8
        }
        pub fn set(&mut self, c: usize, h: usize, w: usize, v: f32) {
            let t = Tensor::new(&self.attr, &self.data).unwrap();
            let i = t.at(c, h, w);
            self.data[i] = self.q(v);
        }
        pub fn set2(&mut self, row: usize, col: usize, v: f32) {
            let i = row * self.attr.dims[2] as usize + col;
            self.data[i] = self.q(v);
        }
    }

    fn run(card: &Card, outs: &[Built], u: &Use) -> Vec<Det> {
        let attrs: Vec<Attr> = outs.iter().map(|b| b.attr.clone()).collect();
        let roles = crate::npu::match_outputs(card, &attrs).unwrap();
        let ts: Vec<Tensor> = outs.iter().map(|b| Tensor::new(&b.attr, &b.data).unwrap()).collect();
        decode(card, &roles, &ts, u).unwrap()
    }

    fn uses() -> Use {
        Use { score: 0.5, nms_iou: 0.45, max_results: 10, classes: None }
    }

    fn close(d: &Det, class: usize, b: [f32; 4], tol: f32) {
        assert_eq!(d.class, class, "{d:?}");
        for k in 0..4 {
            assert!((d.b[k] - b[k]).abs() <= tol, "{d:?} against {b:?}");
        }
    }

    /// YOLOv8-style DFL branches at stride 8 and 16 on a 32 x 32 input, in both layouts: one object per stride, a
    /// candidate exactly on the threshold (kept), one just below (dropped), and two overlapping boxes of one class
    /// (the lower one suppressed) next to an overlapping box of another class (kept).
    fn dfl_case(layout: Layout) -> Vec<Det> {
        let post = r#"{"kind":"yolo-dfl","strides":[8,16],"reg_max":4}"#;
        let outs = r#"[{"index":0,"role":"boxes","shape":[1,16,4,4]},{"index":1,"role":"scores"},
            {"index":2,"role":"auto"},{"index":3,"role":"auto"},{"index":4,"role":"score_sum"}]"#;
        let card = card_of(post, outs, "model", 32, 32);
        let s = 1.0 / 128.0;
        let mut b8 = Built::q4(0, 16, 4, 4, layout, -128, 0.1);
        let mut s8 = Built::q4(1, 3, 4, 4, layout, -128, s);
        let mut b16 = Built::q4(2, 16, 2, 2, layout, -128, 0.1);
        let mut s16 = Built::q4(3, 3, 2, 2, layout, -128, s);
        let mut sum16 = Built::q4(4, 1, 2, 2, layout, -128, s);
        // a one-hot bin per side gives that bin's index as the distance (exp(12.7) against exp(0): the rest vanish)
        let sides = |b: &mut Built, gy: usize, gx: usize, d: [usize; 4]| {
            for (side, bin) in d.iter().enumerate() {
                b.set(side * 4 + bin, gy, gx, 12.7);
            }
        };
        sides(&mut b8, 1, 1, [1, 1, 1, 1]); // centre (12, 12): box 4..20
        s8.set(0, 1, 1, 0.9);
        sides(&mut b8, 1, 2, [1, 1, 1, 1]); // centre (20, 12): box 12..28 x 4..20, IoU 1/3 with the first
        s8.set(0, 1, 2, 0.8); // kept: IoU 0.33 is not above 0.45
        sides(&mut b8, 2, 1, [1, 2, 1, 1]); // centre (12, 20): overlaps the first a lot
        s8.set(0, 2, 1, 0.7); // suppressed (same class)
        sides(&mut b8, 2, 2, [1, 2, 1, 1]);
        s8.set(1, 2, 2, 0.6); // another class: kept despite the overlap
        s8.set(2, 3, 3, 63.0 / 128.0); // just below 0.5: dropped
        sides(&mut b16, 0, 1, [2, 1, 1, 2]); // centre (24, 8): x -8..40 y -8..40
        s16.set(2, 0, 1, 0.5); // exactly on the threshold (64/128): kept
        sum16.set(0, 0, 1, 0.5);
        s16.set(1, 1, 0, 0.95); // no score sum above the threshold: skipped by the sum
        run(&card, &[b8, s8, b16, s16, sum16], &uses())
    }

    #[test]
    fn yolo_dfl_in_both_layouts_gives_the_same_golden_result() {
        let nchw = dfl_case(Layout::Nchw);
        let nhwc = dfl_case(Layout::Nhwc);
        assert_eq!(nchw, nhwc);
        assert_eq!(nchw.len(), 4, "{nchw:?}");
        close(&nchw[0], 0, [4.0, 4.0, 20.0, 20.0], 0.01);
        assert!((nchw[0].score - 0.9).abs() < 0.01);
        close(&nchw[1], 0, [12.0, 4.0, 28.0, 20.0], 0.01);
        close(&nchw[2], 1, [12.0, 4.0, 28.0, 28.0], 0.01);
        close(&nchw[3], 2, [-8.0, -8.0, 40.0, 40.0], 0.01);
        assert_eq!(nchw[3].score, 0.5);
    }

    #[test]
    fn nms_ties_keep_the_candidates_order_and_the_cap_holds() {
        let a = Det { class: 0, score: 0.8, b: [0.0, 0.0, 10.0, 10.0], keypoints: vec![] };
        let b = Det { class: 0, score: 0.8, b: [1.0, 0.0, 11.0, 10.0], keypoints: vec![] };
        let c = Det { class: 0, score: 0.7, b: [50.0, 50.0, 60.0, 60.0], keypoints: vec![] };
        let kept = nms(vec![a.clone(), b.clone(), c.clone()], &uses());
        assert_eq!(kept, vec![a.clone(), c.clone()]);
        assert_eq!(nms(vec![b.clone(), a.clone()], &uses()), vec![b]);
        let one = Use { max_results: 1, ..uses() };
        assert_eq!(nms(vec![c.clone(), a.clone()], &one), vec![a]);
        assert!(nms(Vec::new(), &uses()).is_empty(), "an empty frame");
    }

    #[test]
    fn yolo_anchor_heads_decode_as_the_zoo_does() {
        let post = r#"{"kind":"yolo-anchor","strides":[16],"anchors":[[[10,13],[16,30]]]}"#;
        let outs = r#"[{"index":0,"role":"boxes","shape":[1,16,2,2]}]"#;
        let card = card_of(post, outs, "model", 32, 32);
        for layout in [Layout::Nchw, Layout::Nhwc] {
            let mut h = Built::q4(0, 16, 2, 2, layout, -128, 1.0 / 255.0);
            // anchor 1 of cell (1, 0): x = (0.5*2 - 0.5 + 0) * 16 = 8, y = (0.5*2 - 0.5 + 1) * 16 = 24,
            // w = (0.5*2)^2 * 16 = 16, h = 30
            let base = 8;
            for (c, v) in [(0, 0.5), (1, 0.5), (2, 0.5), (3, 0.5), (4, 0.9), (5, 0.1), (6, 0.8), (7, 0.2)] {
                h.set(base + c, 1, 0, v);
            }
            h.set(4, 0, 1, 0.4); // objectness below the threshold: never looked at
            h.set(6, 0, 1, 1.0);
            let d = run(&card, &[h], &uses());
            assert_eq!(d.len(), 1, "{d:?}");
            close(&d[0], 1, [0.0, 9.0, 16.0, 39.0], 0.5);
            assert!((d[0].score - 0.9 * 0.8).abs() < 0.01);
        }
    }

    #[test]
    fn yolox_heads_have_no_anchors_and_exponential_sizes() {
        let post = r#"{"kind":"yolo-anchorfree-obj","strides":[8]}"#;
        let outs = r#"[{"index":0,"role":"boxes"}]"#;
        let card = card_of(post, outs, "sigmoid", 16, 16);
        let mut h = Built::q4(0, 8, 2, 2, Layout::Nchw, 0, 0.05);
        // cell (0, 1): x = (0.5 + 1) * 8 = 12, y = (0.5 + 0) * 8 = 4, w = h = exp(0) * 8 = 8; logits 3 and 2
        for (c, v) in [(0, 0.5), (1, 0.5), (2, 0.0), (3, 0.0), (4, 3.0), (5, -2.0), (6, -1.0), (7, 2.0)] {
            h.set(c, 0, 1, v);
        }
        let d = run(&card, &[h], &uses());
        assert_eq!(d.len(), 1);
        close(&d[0], 2, [8.0, 0.0, 16.0, 8.0], 0.05);
        assert!((d[0].score - sigmoid(3.0) * sigmoid(2.0)).abs() < 1e-3);
    }

    #[test]
    fn yolo26_decoded_boxes_take_the_top_k_without_nms() {
        let post = r#"{"kind":"yolo-nms-free","box_format":"xyxy","top_k":2}"#;
        let outs = r#"[{"index":0,"role":"auto","shape":[1,4,5]},{"index":1,"role":"auto","shape":[1,3,5]}]"#;
        let card = card_of(post, outs, "model", 64, 64);
        let mut b = Built::q3(0, 4, 5, 0, 0.5);
        let mut s = Built::q3(1, 3, 5, -128, 1.0 / 255.0);
        for (n, score) in [(0, 0.9), (1, 0.85), (2, 0.6), (3, 0.3)] {
            for k in 0..4 {
                b.set2(k, n, (10 * n + k) as f32);
            }
            s.set2(1, n, score);
        }
        let d = run(&card, &[b, s], &uses());
        assert_eq!(d.len(), 2, "top-K, no NMS although the boxes overlap");
        close(&d[0], 1, [0.0, 1.0, 2.0, 3.0], 0.01);
        close(&d[1], 1, [10.0, 11.0, 12.0, 13.0], 0.01);
    }

    #[test]
    fn yolov10_dfl_branches_take_the_top_k() {
        let post = r#"{"kind":"yolo-nms-free","box_format":"dfl","strides":[16],"reg_max":1,"top_k":5}"#;
        let outs = r#"[{"index":0,"role":"boxes"},{"index":1,"role":"scores"}]"#;
        let card = card_of(post, outs, "model", 32, 32);
        let mut b = Built::q4(0, 4, 2, 2, Layout::Nhwc, 0, 0.1);
        let mut s = Built::q4(1, 3, 2, 2, Layout::Nhwc, -128, 1.0 / 255.0);
        for side in 0..4 {
            b.set(side, 1, 1, 0.5);
            b.set(side, 1, 0, 0.5);
        }
        s.set(0, 1, 1, 0.7);
        s.set(0, 1, 0, 0.6);
        let d = run(&card, &[b, s], &uses());
        assert_eq!(d.len(), 2);
        close(&d[0], 0, [16.0, 16.0, 32.0, 32.0], 0.01);
        close(&d[1], 0, [0.0, 16.0, 16.0, 32.0], 0.01);
    }

    #[test]
    fn nms_in_the_model_gives_its_rows_as_they_are() {
        let post = r#"{"kind":"device-nms","box_format":"yxyx","box_units":"normalized"}"#;
        let outs = r#"[{"index":0,"role":"boxes","shape":[1,3,4]},{"index":1,"role":"scores","shape":[1,3,3]}]"#;
        let card = card_of(post, outs, "model", 100, 50);
        let mut b = Built::q3(0, 3, 4, -128, 1.0 / 255.0);
        let mut s = Built::q3(1, 3, 3, -128, 1.0 / 255.0);
        for (k, v) in [0.2, 0.1, 0.6, 0.5].iter().enumerate() {
            b.set2(0, k, *v);
        }
        s.set2(0, 2, 0.75);
        s.set2(1, 0, 0.2);
        let d = run(&card, &[b, s], &uses());
        assert_eq!(d.len(), 1);
        close(&d[0], 2, [10.0, 10.0, 50.0, 30.0], 0.3);
    }

    #[test]
    fn retinaface_priors_variances_and_landmarks() {
        let post = r#"{"kind":"ssd-prior","encoding":"center-variance","steps":[8],"min_sizes":[[8]],
            "variances":[0.1,0.2],"background":true,"keypoints":1}"#;
        let outs = r#"[{"index":0,"role":"boxes","shape":[1,4,4]},{"index":1,"role":"scores","shape":[1,4,2]},
            {"index":2,"role":"keypoints","shape":[1,4,2]}]"#;
        let text = card_json(post, outs, "model", 16, 16, &["face"]);
        let card = card::parse(text.as_bytes()).unwrap();
        assert_eq!(
            priors(&card),
            vec![[4.0, 4.0, 8.0, 8.0], [12.0, 4.0, 8.0, 8.0], [4.0, 12.0, 8.0, 8.0], [12.0, 12.0, 8.0, 8.0]]
        );
        let mut b = Built::q3(0, 4, 4, 0, 0.05);
        let mut s = Built::q3(1, 4, 2, -128, 1.0 / 255.0);
        let mut k = Built::q3(2, 4, 2, 0, 0.05);
        // prior 3 (12, 12, 8, 8): cx = 12 + 1.0*0.1*8 = 12.8, w = 8*exp(0) = 8; landmark 12 + 2*0.1*8 = 13.6
        for (c, v) in [(0, 1.0), (1, 0.0), (2, 0.0), (3, 0.0)] {
            b.set2(3, c, v);
        }
        s.set2(3, 0, 0.1);
        s.set2(3, 1, 0.9);
        s.set2(1, 1, 0.3);
        k.set2(3, 0, 2.0);
        k.set2(3, 1, -1.0);
        let d = run(&card, &[b, s, k], &uses());
        assert_eq!(d.len(), 1, "{d:?}");
        close(&d[0], 0, [8.8, 8.0, 16.8, 16.0], 0.01);
        assert!((d[0].keypoints[0].0 - 13.6).abs() < 0.01 && (d[0].keypoints[0].1 - 11.2).abs() < 0.01);
    }

    #[test]
    fn scrfd_distances_from_anchor_centres_with_softmax_off() {
        let post = r#"{"kind":"ssd-prior","encoding":"distance","steps":[8],"min_sizes":[[1,2]],"background":false}"#;
        let outs = r#"[{"index":0,"role":"boxes"},{"index":1,"role":"scores"}]"#;
        let text = card_json(post, outs, "sigmoid", 8, 8, &["face"]);
        let card = card::parse(text.as_bytes()).unwrap();
        let mut b = Built::q3(0, 2, 4, 0, 0.05);
        let mut s = Built::q3(1, 2, 1, 0, 0.05);
        for c in 0..4 {
            b.set2(1, c, 0.5);
        }
        s.set2(1, 0, 2.0);
        s.set2(0, 0, -2.0);
        let d = run(&card, &[b, s], &uses());
        assert_eq!(d.len(), 1);
        close(&d[0], 0, [-4.0, -4.0, 4.0, 4.0], 0.01);
    }

    #[test]
    fn the_class_subset_and_a_wrong_branch_are_handled() {
        let post = r#"{"kind":"yolo-dfl","strides":[16],"reg_max":1}"#;
        let outs = r#"[{"index":0,"role":"boxes"},{"index":1,"role":"scores"}]"#;
        let card = card_of(post, outs, "model", 32, 32);
        let mut b = Built::q4(0, 4, 2, 2, Layout::Nchw, 0, 0.1);
        let mut s = Built::q4(1, 3, 2, 2, Layout::Nchw, -128, 1.0 / 255.0);
        b.set(0, 0, 0, 0.5);
        s.set(1, 0, 0, 0.9);
        let only_dogs = Use { classes: Some(vec![false, false, true]), ..uses() };
        assert!(run(&card, &[b, s], &only_dogs).is_empty());
        let wrong = Built::q4(1, 2, 2, 2, Layout::Nchw, -128, 1.0);
        let b = Built::q4(0, 4, 2, 2, Layout::Nchw, 0, 0.1);
        let attrs = vec![b.attr.clone(), wrong.attr.clone()];
        let roles = crate::npu::match_outputs(&card, &attrs).unwrap();
        let ts = vec![Tensor::new(&b.attr, &b.data).unwrap(), Tensor::new(&wrong.attr, &wrong.data).unwrap()];
        assert!(decode(&card, &roles, &ts, &uses()).unwrap_err().contains("channels"));
    }
}
