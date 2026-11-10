//! The model card (schema llmcf-model-card/1): what the engine knows of a model, written by the Rockchip target
//! module at conversion and checked here by the very rules of orchestrator/runtime/bundle/model_card.py
//! (tests/data/model_card_vectors.json holds the vectors both sides are tested with): at most 1 MiB, strict JSON
//! (json.rs: a key twice is refused), every key known, every value bounded, and the facts that belong together
//! checked together (the task and its post-processing kind, the parameters of that kind, the roles of its outputs,
//! the labels and the scores' activation). The kinds of the later steps are known by name and refused with that
//! reason, so a card for them is never misread. A card of another schema is refused, never guessed.

use crate::json;
use serde_json::{Map, Value};

pub const SCHEMA: &str = "llmcf-model-card/1";
pub const MAX_CARD_BYTES: usize = 1024 * 1024;
pub const MAX_LABELS: usize = 20_000;
pub const MAX_LABEL_BYTES: usize = 64;
pub const MAX_OUTPUTS: usize = 32;
pub const PLATFORMS: [&str; 8] = ["rk3562", "rk3566", "rk3568", "rk3576", "rk3588", "rv1103", "rv1106", "rv1126b"];
pub const TASKS: [&str; 10] =
    ["detect", "segment", "pose", "obb", "classify", "semantic", "depth", "embed", "text", "text-detect"];
pub const ROLES: [&str; 14] = [
    "boxes",
    "scores",
    "score_sum",
    "objectness",
    "mask_coeffs",
    "protos",
    "keypoints",
    "angle",
    "class_logits",
    "embedding",
    "depth",
    "text_logits",
    "prob_map",
    "auto",
];
/// Every post-processing kind with its task; the first six (step 1) are built, the others come with their steps.
pub const KINDS: [(&str, &str); 15] = [
    ("yolo-anchor", "detect"),
    ("yolo-anchorfree-obj", "detect"),
    ("yolo-dfl", "detect"),
    ("yolo-nms-free", "detect"),
    ("device-nms", "detect"),
    ("ssd-prior", "detect"),
    ("yolo-seg-protos", "segment"),
    ("yolo-pose", "pose"),
    ("yolo-obb", "obb"),
    ("classification", "classify"),
    ("semantic-seg", "semantic"),
    ("depth-map", "depth"),
    ("embedding", "embed"),
    ("ctc-text", "text"),
    ("db-text-det", "text-detect"),
];
pub const BUILT_KINDS: usize = 6;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Layout {
    Nhwc,
    Nchw,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Resize {
    Letterbox,
    Stretch,
    CentreCrop,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InputType {
    Uint8,
    Int8,
    Float32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Activation {
    Model,
    Sigmoid,
    Softmax,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Input {
    pub width: u32,
    pub height: u32,
    pub layout: Layout,
    pub bgr: bool,
    pub resize: Resize,
    pub pad: u8,
    pub mean: [f32; 3],
    pub std: [f32; 3],
    pub normalised_in_model: bool,
    pub kind: InputType,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Post {
    /// YOLOv5, v7: one head per stride, anchors per stride as (w, h) in input pixels.
    YoloAnchor { strides: Vec<u32>, anchors: Vec<Vec<(f32, f32)>> },
    /// YOLOX: one head per stride, no anchors.
    YoloAnchorFree { strides: Vec<u32> },
    /// YOLOv6, v8, 11, PP-YOLOE: per stride a box branch (DFL with reg_max bins), a score branch, an optional score
    /// sum.
    YoloDfl { strides: Vec<u32>, reg_max: u32 },
    /// YOLOv10 (DFL branches) and YOLO26 (decoded xyxy boxes): top-K, no NMS.
    YoloNmsFree { dfl: Option<(Vec<u32>, u32)>, top_k: u32 },
    /// NMS compiled into the model: boxes and scores per row.
    DeviceNms { yxyx: bool, normalized: bool },
    /// SSD, RetinaFace (centre and variance against prior boxes), SCRFD (distances from anchor centres).
    SsdPrior {
        distance: bool,
        steps: Vec<u32>,
        min_sizes: Vec<Vec<f32>>,
        variances: [f32; 2],
        clip: bool,
        background: bool,
        keypoints: u32,
    },
}

impl Post {
    pub fn word(&self) -> &'static str {
        match self {
            Post::YoloAnchor { .. } => "yolo-anchor",
            Post::YoloAnchorFree { .. } => "yolo-anchorfree-obj",
            Post::YoloDfl { .. } => "yolo-dfl",
            Post::YoloNmsFree { .. } => "yolo-nms-free",
            Post::DeviceNms { .. } => "device-nms",
            Post::SsdPrior { .. } => "ssd-prior",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct OutputSpec {
    pub index: u32,
    pub role: String,
    /// the logical shape (NCHW order for four dimensions, as the ONNX graph has it); None: taken from the model
    pub shape: Option<Vec<u32>>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Thresholds {
    pub score: f32,
    pub nms_iou: f32,
    pub max_results: u32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Card {
    pub name: String,
    pub licence: String,
    pub rknn_sha256: String,
    pub toolkit: (u32, u32, u32),
    pub platform: String,
    pub input: Input,
    pub task: String,
    pub post: Post,
    pub labels: Vec<String>,
    pub thresholds: Thresholds,
    pub activation: Activation,
    pub outputs: Vec<OutputSpec>,
}

type R<T> = Result<T, String>;

fn obj<'a>(v: &'a Value, what: &str) -> R<&'a Map<String, Value>> {
    v.as_object().ok_or_else(|| format!("{what} is not a mapping"))
}

fn keys(m: &Map<String, Value>, what: &str, required: &[&str], optional: &[&str]) -> R<()> {
    for k in m.keys() {
        if !required.contains(&k.as_str()) && !optional.contains(&k.as_str()) {
            return Err(format!("{what}: unknown key {k}"));
        }
    }
    for k in required {
        if !m.contains_key(*k) {
            return Err(format!("{what}: {k} is missing"));
        }
    }
    Ok(())
}

fn text<'a>(m: &'a Map<String, Value>, key: &str, what: &str, max: usize) -> R<&'a str> {
    let s = m.get(key).and_then(Value::as_str).ok_or_else(|| format!("{what}.{key} is not text"))?;
    if s.is_empty() || s.len() > max || s.chars().any(char::is_control) {
        return Err(format!("{what}.{key} is a plain text of 1 to {max} bytes"));
    }
    Ok(s)
}

fn word<'a>(m: &'a Map<String, Value>, key: &str, what: &str, choices: &[&str]) -> R<&'a str> {
    let s = m.get(key).and_then(Value::as_str).ok_or_else(|| format!("{what}.{key} is not text"))?;
    if choices.contains(&s) {
        Ok(s)
    } else {
        Err(format!("{what}.{key} must be one of {}", choices.join(", ")))
    }
}

fn uint(v: Option<&Value>, what: &str, lo: u64, hi: u64) -> R<u32> {
    match v.and_then(Value::as_u64) {
        Some(n) if (lo..=hi).contains(&n) && !v.is_some_and(|x| x.is_f64()) => Ok(n as u32),
        _ => Err(format!("{what} must be a whole number from {lo} to {hi}")),
    }
}

fn num(v: Option<&Value>, what: &str, lo: f64, hi: f64, open_low: bool) -> R<f32> {
    match v.and_then(Value::as_f64) {
        Some(x) if x.is_finite() && x <= hi && (x > lo || !open_low && x == lo) => Ok(x as f32),
        _ => Err(format!("{what} must be a number {}{lo} and at most {hi}", if open_low { "above " } else { "from " })),
    }
}

fn boolean(v: Option<&Value>, what: &str) -> R<bool> {
    v.and_then(Value::as_bool).ok_or_else(|| format!("{what} is true or false"))
}

fn is_hex(s: &str, n: usize) -> bool {
    s.len() == n && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn version(s: &str) -> Option<(u32, u32, u32)> {
    let mut it = s.split('.');
    let mut part = || it.next().filter(|p| (1..=4).contains(&p.len()) && p.bytes().all(|b| b.is_ascii_digit()));
    let v = (part()?.parse().ok()?, part()?.parse().ok()?, part()?.parse().ok()?);
    it.next().is_none().then_some(v)
}

fn licence_ok(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 64
        && b[0].is_ascii_alphanumeric()
        && b.iter().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'+' | b'-'))
}

fn file_ok(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty()
        && b.len() <= 128
        && (b[0].is_ascii_alphanumeric() || b[0] == b'_')
        && b.iter().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'_' | b'-'))
}

fn uints(v: Option<&Value>, what: &str, min_len: usize, max_len: usize, lo: u64, hi: u64) -> R<Vec<u32>> {
    let a = v.and_then(Value::as_array).ok_or_else(|| format!("{what} is a list"))?;
    if a.len() < min_len || a.len() > max_len {
        return Err(format!("{what} is a list of {min_len} to {max_len} whole numbers"));
    }
    a.iter().map(|x| uint(Some(x), what, lo, hi)).collect()
}

fn three(v: Option<&Value>, what: &str, lo: f64, open_low: bool) -> R<[f32; 3]> {
    let a = v.and_then(Value::as_array).filter(|a| a.len() == 3).ok_or_else(|| format!("{what} is three numbers"))?;
    Ok([
        num(a.first(), what, lo, 1000.0, open_low)?,
        num(a.get(1), what, lo, 1000.0, open_low)?,
        num(a.get(2), what, lo, 1000.0, open_low)?,
    ])
}

fn strides(p: &Map<String, Value>, what: &str) -> R<Vec<u32>> {
    let s = uints(p.get("strides"), &format!("{what}.strides"), 1, 8, 1, 256)?;
    let mut seen = s.clone();
    seen.sort_unstable();
    seen.dedup();
    if seen.len() != s.len() {
        return Err(format!("{what}.strides names a stride twice"));
    }
    Ok(s)
}

fn post(p: &Map<String, Value>, kind: &str) -> R<Post> {
    let what = "postprocess";
    let allowed: &[&str] = match kind {
        "yolo-anchor" => &["kind", "strides", "anchors"],
        "yolo-anchorfree-obj" => &["kind", "strides"],
        "yolo-dfl" => &["kind", "strides", "reg_max"],
        "yolo-nms-free" => &["kind", "box_format", "strides", "reg_max", "top_k"],
        "device-nms" => &["kind", "box_format", "box_units"],
        _ => &["kind", "encoding", "steps", "min_sizes", "variances", "clip", "background", "keypoints"],
    };
    let required: &[&str] = match kind {
        "yolo-anchor" => &["kind", "strides", "anchors"],
        "yolo-anchorfree-obj" => &["kind", "strides"],
        "yolo-dfl" => &["kind", "strides", "reg_max"],
        "yolo-nms-free" => &["kind", "box_format", "top_k"],
        "device-nms" => &["kind", "box_format", "box_units"],
        _ => &["kind", "encoding", "steps", "min_sizes", "background"],
    };
    keys(p, what, required, allowed)?;
    Ok(match kind {
        "yolo-anchor" => {
            let strides = strides(p, what)?;
            let raw = p.get("anchors").and_then(Value::as_array).ok_or("postprocess.anchors is a list")?;
            if raw.len() != strides.len() {
                return Err("postprocess.anchors has one list of anchors per stride".into());
            }
            let mut anchors = Vec::new();
            for per in raw {
                let per = per
                    .as_array()
                    .filter(|a| (1..=9).contains(&a.len()))
                    .ok_or("postprocess.anchors: each stride has 1 to 9 anchors")?;
                let mut out = Vec::new();
                for pair in per {
                    let pair = pair
                        .as_array()
                        .filter(|a| a.len() == 2)
                        .ok_or("postprocess.anchors: an anchor is [width, height]")?;
                    out.push((
                        num(pair.first(), "postprocess.anchors", 0.0, 4096.0, true)?,
                        num(pair.get(1), "postprocess.anchors", 0.0, 4096.0, true)?,
                    ));
                }
                anchors.push(out);
            }
            Post::YoloAnchor { strides, anchors }
        }
        "yolo-anchorfree-obj" => Post::YoloAnchorFree { strides: strides(p, what)? },
        "yolo-dfl" => {
            Post::YoloDfl { strides: strides(p, what)?, reg_max: uint(p.get("reg_max"), "postprocess.reg_max", 1, 64)? }
        }
        "yolo-nms-free" => {
            let format = word(p, "box_format", what, &["dfl", "xyxy"])?;
            let top_k = uint(p.get("top_k"), "postprocess.top_k", 1, 1000)?;
            let dfl = if format == "dfl" {
                if !p.contains_key("strides") || !p.contains_key("reg_max") {
                    return Err("postprocess: box_format dfl needs strides and reg_max".into());
                }
                Some((strides(p, what)?, uint(p.get("reg_max"), "postprocess.reg_max", 1, 64)?))
            } else {
                if p.contains_key("strides") || p.contains_key("reg_max") {
                    return Err("postprocess: box_format xyxy takes no strides or reg_max".into());
                }
                None
            };
            Post::YoloNmsFree { dfl, top_k }
        }
        "device-nms" => Post::DeviceNms {
            yxyx: word(p, "box_format", what, &["xyxy", "yxyx"])? == "yxyx",
            normalized: word(p, "box_units", what, &["pixels", "normalized"])? == "normalized",
        },
        _ => {
            let distance = word(p, "encoding", what, &["center-variance", "distance"])? == "distance";
            let steps = uints(p.get("steps"), "postprocess.steps", 1, 8, 1, 256)?;
            let raw = p.get("min_sizes").and_then(Value::as_array).ok_or("postprocess.min_sizes is a list")?;
            if raw.len() != steps.len() {
                return Err("postprocess.min_sizes has one list of sizes per step".into());
            }
            let mut min_sizes = Vec::new();
            for per in raw {
                let per = per
                    .as_array()
                    .filter(|a| (1..=8).contains(&a.len()))
                    .ok_or("postprocess.min_sizes: each step has 1 to 8 sizes")?;
                min_sizes.push(
                    per.iter()
                        .map(|x| num(Some(x), "postprocess.min_sizes", 0.0, 4096.0, true))
                        .collect::<R<Vec<_>>>()?,
                );
            }
            let variances = match p.get("variances") {
                None if distance => [0.1, 0.2],
                None => return Err("postprocess: encoding center-variance needs variances".into()),
                Some(v) => {
                    let a = v.as_array().filter(|a| a.len() == 2).ok_or("postprocess.variances is two numbers")?;
                    [
                        num(a.first(), "postprocess.variances", 0.0, 10.0, true)?,
                        num(a.get(1), "postprocess.variances", 0.0, 10.0, true)?,
                    ]
                }
            };
            Post::SsdPrior {
                distance,
                steps,
                min_sizes,
                variances,
                clip: match p.get("clip") {
                    None => false,
                    v => boolean(v, "postprocess.clip")?,
                },
                background: boolean(p.get("background"), "postprocess.background")?,
                keypoints: match p.get("keypoints") {
                    None => 0,
                    v => uint(v, "postprocess.keypoints", 0, 32)?,
                },
            }
        }
    })
}

/// The roles each built kind takes for its outputs.
fn roles_of(post: &Post) -> &'static [&'static str] {
    match post {
        Post::YoloAnchor { .. } | Post::YoloAnchorFree { .. } => &["boxes", "auto"],
        Post::YoloDfl { .. } => &["boxes", "scores", "score_sum", "auto"],
        Post::YoloNmsFree { dfl: Some(_), .. } => &["boxes", "scores", "score_sum", "auto"],
        Post::YoloNmsFree { dfl: None, .. } => &["boxes", "scores", "auto"],
        Post::DeviceNms { .. } => &["boxes", "scores"],
        Post::SsdPrior { .. } => &["boxes", "scores", "keypoints"],
    }
}

/// A card from its bytes, checked (see the module text); the reason when it is not one.
pub fn parse(bytes: &[u8]) -> R<Card> {
    let v = json::parse(bytes, MAX_CARD_BYTES).map_err(|e| format!("the model card is {e}"))?;
    let top = obj(&v, "the model card")?;
    keys(
        top,
        "the model card",
        &[
            "schema",
            "model",
            "rknn",
            "input",
            "task",
            "postprocess",
            "labels",
            "thresholds",
            "score_activation",
            "outputs",
        ],
        &[],
    )?;
    if top.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return Err(format!("schema must be {SCHEMA}"));
    }
    let model = obj(&top["model"], "model")?;
    keys(model, "model", &["name", "licence"], &["source", "commit"])?;
    let name = text(model, "name", "model", 128)?.to_string();
    if model.contains_key("source") {
        text(model, "source", "model", 200)?;
    }
    if model.contains_key("commit") && !is_hex(model["commit"].as_str().unwrap_or(""), 40) {
        return Err("model.commit is a commit (40 lower-case hex digits)".into());
    }
    let licence = model["licence"]
        .as_str()
        .filter(|s| licence_ok(s))
        .ok_or("model.licence is an SPDX identifier or a short word")?;

    let rknn = obj(&top["rknn"], "rknn")?;
    keys(rknn, "rknn", &["file", "sha256", "toolkit", "platform", "quantisation"], &[])?;
    let file = rknn["file"].as_str().unwrap_or("");
    if !file_ok(file) || !file.ends_with(".rknn") {
        return Err("rknn.file is a plain file name ending in .rknn".into());
    }
    let sha = rknn["sha256"].as_str().unwrap_or("");
    if !is_hex(sha, 64) {
        return Err("rknn.sha256 must be 64 lower-case hex digits".into());
    }
    let toolkit = rknn["toolkit"].as_str().and_then(version).ok_or("rknn.toolkit is a version MAJOR.MINOR.PATCH")?;
    let platform = word(rknn, "platform", "rknn", &PLATFORMS)?.to_string();
    word(rknn, "quantisation", "rknn", &["w8a8", "fp16"])?;

    let i = obj(&top["input"], "input")?;
    keys(
        i,
        "input",
        &["width", "height", "layout", "colour", "resize", "mean", "std", "normalised_in_model", "type"],
        &["pad"],
    )?;
    let input = Input {
        width: uint(i.get("width"), "input.width", 1, 4096)?,
        height: uint(i.get("height"), "input.height", 1, 4096)?,
        layout: if word(i, "layout", "input", &["nhwc", "nchw"])? == "nhwc" { Layout::Nhwc } else { Layout::Nchw },
        bgr: word(i, "colour", "input", &["rgb", "bgr"])? == "bgr",
        resize: match word(i, "resize", "input", &["letterbox", "stretch", "centre-crop"])? {
            "letterbox" => Resize::Letterbox,
            "stretch" => Resize::Stretch,
            _ => Resize::CentreCrop,
        },
        pad: match i.get("pad") {
            None => 114,
            v => uint(v, "input.pad", 0, 255)? as u8,
        },
        mean: three(i.get("mean"), "input.mean", -1000.0, false)?,
        std: three(i.get("std"), "input.std", 0.0, true)?,
        normalised_in_model: boolean(i.get("normalised_in_model"), "input.normalised_in_model")?,
        kind: match word(i, "type", "input", &["uint8", "int8", "float32"])? {
            "uint8" => InputType::Uint8,
            "int8" => InputType::Int8,
            _ => InputType::Float32,
        },
    };
    if input.normalised_in_model && input.kind != InputType::Uint8 {
        return Err("input: a model with its normalisation inside takes uint8 (input.type)".into());
    }

    let task = word(top, "task", "the model card", &TASKS)?.to_string();
    let p = obj(&top["postprocess"], "postprocess")?;
    let kind = p.get("kind").and_then(Value::as_str).ok_or("postprocess.kind is not text")?;
    let Some(pos) = KINDS.iter().position(|(k, _)| *k == kind) else {
        return Err(format!(
            "postprocess.kind must be one of {}",
            KINDS.iter().map(|(k, _)| *k).collect::<Vec<_>>().join(", ")
        ));
    };
    if KINDS[pos].1 != task {
        return Err(format!("postprocess.kind {kind} belongs to the task {}, not {task}", KINDS[pos].1));
    }
    if pos >= BUILT_KINDS {
        return Err(format!(
            "postprocess.kind {kind} comes with a later step of the vision engine; it is not built yet"
        ));
    }
    let post = post(p, kind)?;

    let labels_raw = top["labels"].as_array().ok_or("labels is a list")?;
    if labels_raw.is_empty() || labels_raw.len() > MAX_LABELS {
        return Err(format!("labels is a list of 1 to {MAX_LABELS} names"));
    }
    let mut labels = Vec::with_capacity(labels_raw.len());
    for l in labels_raw {
        let s = l.as_str().unwrap_or("");
        if s.is_empty() || s.len() > MAX_LABEL_BYTES || s.chars().any(char::is_control) {
            return Err(format!("labels: each name is a plain text of 1 to {MAX_LABEL_BYTES} bytes"));
        }
        labels.push(s.to_string());
    }

    let t = obj(&top["thresholds"], "thresholds")?;
    keys(t, "thresholds", &["score", "nms_iou", "max_results"], &[])?;
    let thresholds = Thresholds {
        score: num(t.get("score"), "thresholds.score", 0.0, 1.0, true)?,
        nms_iou: num(t.get("nms_iou"), "thresholds.nms_iou", 0.0, 1.0, true)?,
        max_results: uint(t.get("max_results"), "thresholds.max_results", 1, 1000)?,
    };
    let activation = match word(top, "score_activation", "the model card", &["model", "sigmoid", "softmax"])? {
        "model" => Activation::Model,
        "sigmoid" => Activation::Sigmoid,
        _ => Activation::Softmax,
    };
    match (&post, activation) {
        (Post::DeviceNms { .. }, a) if a != Activation::Model => {
            return Err("score_activation: a model with NMS inside gives its scores as they are (model)".into())
        }
        (Post::SsdPrior { .. }, _) | (_, Activation::Model | Activation::Sigmoid) => {}
        _ => return Err("score_activation: softmax is for ssd-prior; YOLO scores are model or sigmoid".into()),
    }

    let raw_outputs = top["outputs"].as_array().ok_or("outputs is a list")?;
    if raw_outputs.is_empty() || raw_outputs.len() > MAX_OUTPUTS {
        return Err(format!("outputs is a list of 1 to {MAX_OUTPUTS} outputs"));
    }
    let allowed_roles = roles_of(&post);
    let mut outputs: Vec<OutputSpec> = Vec::new();
    for o in raw_outputs {
        let m = obj(o, "an output")?;
        keys(m, "an output", &["index", "role"], &["shape", "dims"])?;
        let index = uint(m.get("index"), "outputs.index", 0, MAX_OUTPUTS as u64 - 1)?;
        if outputs.iter().any(|x| x.index == index) {
            return Err(format!("outputs: the index {index} twice"));
        }
        let role = word(m, "role", "outputs", &ROLES)?.to_string();
        if !allowed_roles.contains(&role.as_str()) {
            return Err(format!(
                "outputs: the role {role} is not one of {} ({})",
                post.word(),
                allowed_roles.join(", ")
            ));
        }
        let shape = match m.get("shape") {
            None => None,
            v => Some(uints(v, "outputs.shape", 1, 6, 1, 1_000_000)?),
        };
        if let Some(d) = m.get("dims") {
            let names = d.as_array().ok_or("outputs.dims is a list of dimension names")?;
            let ok = |x: &Value| {
                x.as_str().is_some_and(|s| (1..=8).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_lowercase()))
            };
            if shape.as_ref().is_none_or(|s| s.len() != names.len()) || !names.iter().all(ok) {
                return Err("outputs.dims names each dimension of its shape (lower-case words)".into());
            }
        }
        outputs.push(OutputSpec { index, role, shape });
    }
    let has = |r: &str| outputs.iter().any(|o| o.role == r);
    let need: &[&str] = match &post {
        Post::DeviceNms { .. } | Post::SsdPrior { .. } => &["boxes", "scores"],
        _ => &[],
    };
    for r in need {
        if !has(r) {
            return Err(format!("outputs: {} needs an output of the role {r}", post.word()));
        }
    }
    if let Post::SsdPrior { keypoints, .. } = &post {
        if (*keypoints > 0) != has("keypoints") {
            return Err("outputs: an output of the role keypoints exactly when postprocess.keypoints is above 0".into());
        }
    }
    Ok(Card {
        name,
        licence: licence.to_string(),
        rknn_sha256: sha.to_string(),
        toolkit,
        platform,
        input,
        task,
        post,
        labels,
        thresholds,
        activation,
        outputs,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const VECTORS: &str = include_str!("../../../tests/data/model_card_vectors.json");

    fn patched(base: &Value, patches: &Value) -> Value {
        let mut doc = base.clone();
        for p in patches.as_array().unwrap() {
            let path = p["path"].as_array().unwrap();
            let mut at = &mut doc;
            for step in &path[..path.len() - 1] {
                at = match step {
                    Value::String(s) => &mut at[s.as_str()],
                    Value::Number(n) => &mut at[n.as_u64().unwrap() as usize],
                    _ => unreachable!(),
                };
            }
            let last = &path[path.len() - 1];
            match (last, p.get("value")) {
                (Value::String(s), Some(v)) => {
                    at.as_object_mut().unwrap().insert(s.clone(), v.clone());
                }
                (Value::String(s), None) => {
                    at.as_object_mut().unwrap().remove(s);
                }
                (Value::Number(n), Some(v)) => at[n.as_u64().unwrap() as usize] = v.clone(),
                (Value::Number(n), None) => {
                    at.as_array_mut().unwrap().remove(n.as_u64().unwrap() as usize);
                }
                _ => unreachable!(),
            }
        }
        doc
    }

    #[test]
    fn the_shared_model_card_vectors_hold() {
        let v: Value = serde_json::from_str(VECTORS).unwrap();
        for (name, base) in v["bases"].as_object().unwrap() {
            let card = parse(serde_json::to_string(base).unwrap().as_bytes());
            assert!(card.is_ok(), "base {name}: {card:?}");
        }
        for case in v["accept"].as_array().unwrap() {
            let doc = patched(&v["bases"][case["base"].as_str().unwrap()], &case["patches"]);
            let got = parse(serde_json::to_string(&doc).unwrap().as_bytes());
            assert!(got.is_ok(), "{}: {got:?}", case["why"]);
        }
        for case in v["reject"].as_array().unwrap() {
            let doc = patched(&v["bases"][case["base"].as_str().unwrap()], &case["patches"]);
            let got = parse(serde_json::to_string(&doc).unwrap().as_bytes());
            assert!(got.is_err(), "{} was taken", case["why"]);
        }
        for raw in v["raw_reject"].as_array().unwrap() {
            assert!(parse(raw["text"].as_str().unwrap().as_bytes()).is_err(), "{}", raw["why"]);
        }
    }

    #[test]
    fn a_card_is_capped() {
        assert!(parse(&vec![b' '; MAX_CARD_BYTES + 1]).unwrap_err().contains("1024 KiB"));
    }
}
