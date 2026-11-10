//! The engine around one model: started from its card and its bytes through the NPU (the model checked against the
//! card's SHA256 before the runtime parses it, the SoC against the card's platform, the versions, the input and every
//! output against the card, the core mask), then one inference per image: preparation (prep.rs), the NPU, the
//! post-processing (post.rs), and the result as one JSON object of the schema llmcf-vision-result/1 in the source's
//! pixels with the card's labels and class ids.

use crate::card::{self, Card, InputType};
use crate::image::Rgb;
use crate::npu::{self, Npu, Versions};
use crate::post::{self, Det, Use};
use crate::prep::{self, Mapping};
use crate::tensor::Tensor;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

pub const RESULT_SCHEMA: &str = "llmcf-vision-result/1";
/// The longest options text of a request.
pub const MAX_OPTIONS: usize = 64 * 1024;

/// What the block may set in place of the card's thresholds (decisions of use, never facts of the model).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Overrides {
    /// percent (1 to 100); None: the card's
    pub score_pct: Option<u32>,
    pub nms_pct: Option<u32>,
    pub max_results: Option<u32>,
    /// class ids; None: all
    pub classes: Option<Vec<u32>>,
}

pub struct Engine {
    pub card: Card,
    npu: Box<dyn Npu>,
    roles: Vec<Option<String>>,
    pub defaults: Use,
    pub versions: Versions,
    pub warnings: Vec<String>,
    input_quant: (i32, f32),
}

/// One frame's result before it is written: the detections in source pixels and the timings.
pub struct Outcome {
    pub dets: Vec<Det>,
    pub map: Mapping,
    pub prepare_ms: f64,
    pub npu_ms: f64,
    pub post_ms: f64,
}

pub fn sha256_hex(data: &[u8]) -> String {
    Sha256::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}

fn ms(since: Instant) -> f64 {
    (since.elapsed().as_secs_f64() * 1e5).round() / 100.0
}

impl Engine {
    /// The engine for `model` with its `card` on the NPU `npu`, on a board of the SoC `soc` (the device tree's).
    pub fn start(
        card_bytes: &[u8],
        model: &[u8],
        mut npu: Box<dyn Npu>,
        soc: Option<&str>,
        cores: &str,
        o: &Overrides,
    ) -> Result<Engine, String> {
        let card = card::parse(card_bytes).map_err(|e| format!("the model card is refused: {e}"))?;
        if sha256_hex(model) != card.rknn_sha256 {
            return Err("the model is not the one its card names (its SHA256 differs): it is not loaded".into());
        }
        let soc = soc.ok_or("the board's SoC is not named in its device tree (/proc/device-tree/compatible)")?;
        if !npu::soc_fits(&card.platform, soc) {
            return Err(format!("the model was converted for the {}, the board is a {soc}", card.platform));
        }
        let mask = npu::core_mask(cores, soc)?;
        npu.load(model).map_err(|e| format!("the NPU runtime could not load the model: {e}"))?;
        let versions = npu.versions().map_err(|e| format!("the NPU runtime names no versions: {e}"))?;
        let warnings = npu::check_versions(&versions, &card)?;
        let inputs = npu.inputs();
        if inputs.len() != 1 {
            return Err(format!("the model has {} inputs; the engine feeds one image", inputs.len()));
        }
        let want = vec![1, 3, card.input.height, card.input.width];
        if inputs[0].logical() != want {
            return Err(format!(
                "the model's input is {:?}, the card says {:?} (n, c, h, w)",
                inputs[0].logical(),
                want
            ));
        }
        let input_quant = (inputs[0].zp, inputs[0].scale);
        let roles = npu::match_outputs(&card, npu.outputs())?;
        if mask != 0 {
            npu.set_core_mask(mask).map_err(|e| format!("the NPU cores could not be set: {e}"))?;
        }
        let t = &card.thresholds;
        let pct = |p: Option<u32>, d: f32| p.map_or(d, |p| p.clamp(1, 100) as f32 / 100.0);
        let max_results = o.max_results.map_or(t.max_results, |m| m.clamp(1, t.max_results)) as usize;
        let classes = match &o.classes {
            None => None,
            Some(ids) => {
                let mut v = vec![false; card.labels.len()];
                for id in ids {
                    *v.get_mut(*id as usize).ok_or_else(|| {
                        format!("the class {id} is not among the card's {} classes", card.labels.len())
                    })? = true;
                }
                Some(v)
            }
        };
        let defaults =
            Use { score: pct(o.score_pct, t.score), nms_iou: pct(o.nms_pct, t.nms_iou), max_results, classes };
        Ok(Engine { card, npu, roles, defaults, versions, warnings, input_quant })
    }

    /// The decisions of use of a request: its options (strict JSON: score, nms_iou, max_results, labels) within the
    /// card's bounds, on top of the engine's defaults.
    pub fn options(&self, text: Option<&[u8]>) -> Result<Use, String> {
        let mut u = self.defaults.clone();
        let Some(text) = text else { return Ok(u) };
        let v = crate::json::parse(text, MAX_OPTIONS).map_err(|e| format!("options is {e}"))?;
        let m = v.as_object().ok_or("options is a JSON object")?;
        for (k, x) in m {
            match k.as_str() {
                "score" | "nms_iou" => {
                    let f = x
                        .as_f64()
                        .filter(|f| *f > 0.0 && *f <= 1.0)
                        .ok_or_else(|| format!("options.{k} is a number above 0 and at most 1"))?;
                    if k == "score" {
                        u.score = f as f32;
                    } else {
                        u.nms_iou = f as f32;
                    }
                }
                "max_results" => {
                    let max = self.card.thresholds.max_results as u64;
                    let n = x
                        .as_u64()
                        .filter(|n| (1..=max).contains(n) && !x.is_f64())
                        .ok_or_else(|| format!("options.max_results is a whole number from 1 to {max}"))?;
                    u.max_results = n as usize;
                }
                "labels" => {
                    let names = x
                        .as_array()
                        .filter(|a| !a.is_empty() && a.len() <= card::MAX_LABELS)
                        .ok_or("options.labels is a list of the card's labels")?;
                    let mut want = vec![false; self.card.labels.len()];
                    for n in names {
                        let s = n.as_str().unwrap_or("");
                        let i = self
                            .card
                            .labels
                            .iter()
                            .position(|l| l == s)
                            .ok_or("options.labels names a label the model does not have")?;
                        want[i] = true;
                    }
                    if let Some(block) = &self.defaults.classes {
                        for (w, b) in want.iter_mut().zip(block) {
                            *w &= *b;
                        }
                    }
                    u.classes = Some(want);
                }
                _ => {
                    return Err(format!(
                        "options: unknown key {}",
                        k.chars().filter(|c| c.is_ascii_alphanumeric() || *c == '_').take(32).collect::<String>()
                    ))
                }
            }
        }
        Ok(u)
    }

    /// One inference of `img` with the decisions `u`.
    pub fn infer(&mut self, img: &Rgb, u: &Use) -> Result<Outcome, String> {
        let t0 = Instant::now();
        let (tensor, map) = prep::prepare(img, &self.card.input, self.input_quant);
        let prepare_ms = ms(t0);
        let t1 = Instant::now();
        let outs = self.npu.run(&[&tensor]).map_err(|e| format!("the NPU run failed: {e}"))?;
        let npu_ms = ms(t1);
        let t2 = Instant::now();
        let attrs = self.npu.outputs();
        if outs.len() != attrs.len() {
            return Err(format!("the NPU gave {} outputs, the model has {}", outs.len(), attrs.len()));
        }
        let mut views = Vec::with_capacity(outs.len());
        for (a, o) in attrs.iter().zip(&outs) {
            views.push(
                Tensor::new(a, o)
                    .ok_or_else(|| format!("output {} has {} bytes, not its shape's", a.index, o.len()))?,
            );
        }
        let mut dets = post::decode(&self.card, &self.roles, &views, u)?;
        for d in &mut dets {
            let (x1, y1) = map.back(d.b[0], d.b[1]);
            let (x2, y2) = map.back(d.b[2], d.b[3]);
            d.b = [x1, y1, x2, y2];
            for p in &mut d.keypoints {
                *p = map.back(p.0, p.1);
            }
        }
        Ok(Outcome { dets, map, prepare_ms, npu_ms, post_ms: ms(t2) })
    }

    /// The result of one frame as JSON (llmcf-vision-result/1).
    pub fn result(&self, o: &Outcome, source: &str, seq: u64, dropped: u64) -> Value {
        let r2 = |v: f32| ((v as f64) * 100.0).round() / 100.0;
        let r4 = |v: f32| ((v as f64) * 10000.0).round() / 10000.0;
        let dets: Vec<Value> = o
            .dets
            .iter()
            .map(|d| {
                let mut m = Map::new();
                m.insert("label".into(), json!(self.card.labels[d.class]));
                m.insert("class_id".into(), json!(d.class));
                m.insert("score".into(), json!(r4(d.score)));
                m.insert("box".into(), json!([r2(d.b[0]), r2(d.b[1]), r2(d.b[2]), r2(d.b[3])]));
                if !d.keypoints.is_empty() {
                    m.insert(
                        "keypoints".into(),
                        json!(d.keypoints.iter().map(|p| [r2(p.0), r2(p.1)]).collect::<Vec<_>>()),
                    );
                }
                Value::Object(m)
            })
            .collect();
        let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0);
        json!({
            "schema": RESULT_SCHEMA,
            "model": self.card.name,
            "task": self.card.task,
            "kind": self.card.post.word(),
            "frame": {"source": source, "width": o.map.width, "height": o.map.height, "seq": seq, "time_ms": now,
                      "dropped": dropped},
            "timings_ms": {"prepare": o.prepare_ms, "npu": o.npu_ms, "post": o.post_ms},
            "detections": dets,
        })
    }

    /// What /health says once the model is loaded (numbers and states, nothing of a frame).
    pub fn health(&self) -> Value {
        json!({
            "status": "ok",
            "model": self.card.name,
            "task": self.card.task,
            "kind": self.card.post.word(),
            "input": [self.card.input.width, self.card.input.height],
            "int8_input": self.card.input.kind == InputType::Int8,
            "classes": self.card.labels.len(),
            "runtime": self.versions.api.chars().filter(|c| !c.is_control()).take(80).collect::<String>(),
            "driver": self.versions.driver.chars().filter(|c| !c.is_control()).take(40).collect::<String>(),
            "warnings": self.warnings,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::npu::{Attr, DType, Layout, NpuError};
    use crate::post::tests::{card_json, Built, LABELS};

    /// An NPU that hands back fixed outputs (the engine's checks around the NPU, without the simulated one's files).
    struct Fixed {
        driver: &'static str,
        input: Attr,
        attrs: Vec<Attr>,
        data: Vec<Vec<u8>>,
        fail_load: bool,
    }

    impl Npu for Fixed {
        fn load(&mut self, _model: &[u8]) -> Result<(), NpuError> {
            if self.fail_load {
                Err(NpuError("RKNN_ERR_MODEL_INVALID".into()))
            } else {
                Ok(())
            }
        }
        fn versions(&self) -> Result<Versions, NpuError> {
            Ok(Versions { api: "2.5.0 (test)".into(), driver: self.driver.into() })
        }
        fn inputs(&self) -> &[Attr] {
            std::slice::from_ref(&self.input)
        }
        fn outputs(&self) -> &[Attr] {
            &self.attrs
        }
        fn set_core_mask(&mut self, _mask: u32) -> Result<(), NpuError> {
            Ok(())
        }
        fn run(&mut self, inputs: &[&[u8]]) -> Result<Vec<Vec<u8>>, NpuError> {
            assert_eq!(inputs[0].len(), self.input.elems());
            Ok(self.data.clone())
        }
    }

    const POST: &str = r#"{"kind":"yolo-dfl","strides":[16],"reg_max":1}"#;
    const OUTS: &str = r#"[{"index":0,"role":"boxes","shape":[1,4,2,2]},{"index":1,"role":"scores"}]"#;

    fn fixed(driver: &'static str, channels: usize) -> Box<Fixed> {
        let input = Attr {
            index: 0,
            name: "images".into(),
            dims: vec![1, 32, 32, 3],
            layout: Layout::Nhwc,
            dtype: DType::U8,
            zp: 0,
            scale: 1.0,
        };
        let mut b = Built::q4(0, 4, 2, 2, Layout::Nchw, 0, 0.1);
        let mut s = Built::q4(1, channels, 2, 2, Layout::Nchw, -128, 1.0 / 128.0);
        for side in 0..4 {
            b.set(side, 1, 1, 0.5);
        }
        s.set(2, 1, 1, 0.75);
        Box::new(Fixed { driver, input, attrs: vec![b.attr, s.attr], data: vec![b.data, s.data], fail_load: false })
    }

    fn card_for(model: &[u8]) -> Vec<u8> {
        card_json(POST, OUTS, "model", 32, 32, &LABELS).replace(&"0".repeat(64), &sha256_hex(model)).into_bytes()
    }

    #[test]
    fn a_model_runs_through_its_card_into_the_sources_pixels() {
        let model = b"RKNN model bytes";
        let mut e =
            Engine::start(&card_for(model), model, fixed("0.9.8", 3), Some("rk3568"), "auto", &Overrides::default())
                .unwrap();
        assert!(e.warnings.is_empty());
        // a 64 x 32 image: letterboxed to 32 x 16 with 8 rows of padding above; the box (16, 16, 32, 32) of the
        // input is (32, 16, 64, 48) in the image's pixels, clamped into its 32 rows
        let img = Rgb { width: 64, height: 32, data: vec![50; 64 * 32 * 3] };
        let u = e.defaults.clone();
        let out = e.infer(&img, &u).unwrap();
        let v = e.result(&out, "upload", 1, 0);
        assert_eq!(v["schema"], RESULT_SCHEMA);
        assert_eq!(v["detections"][0]["label"], "dog");
        assert_eq!(v["detections"][0]["box"], json!([32.0, 16.0, 64.0, 32.0]));
        assert_eq!(v["frame"]["width"], 64);
        // options within the card's bounds
        assert!(e.options(Some(br#"{"labels":["person"]}"#)).is_ok());
        let only_people = e.options(Some(br#"{"labels":["person"],"score":0.3}"#)).unwrap();
        assert!(e.infer(&img, &only_people).unwrap().dets.is_empty());
        for bad in [
            &br#"{"score":0}"#[..],
            br#"{"max_results":11}"#,
            br#"{"labels":["cat"]}"#,
            br#"{"x":1}"#,
            b"[]",
            br#"{"score":1,"score":1}"#,
        ] {
            assert!(e.options(Some(bad)).is_err(), "{}", String::from_utf8_lossy(bad));
        }
    }

    #[test]
    fn the_checks_around_the_npu_refuse_with_the_reason() {
        let model = b"RKNN model bytes";
        let card = card_for(model);
        let o = Overrides::default();
        let start = |npu, soc, cores, model: &[u8]| Engine::start(&card, model, npu, soc, cores, &o).err().unwrap();
        assert!(start(fixed("0.9.8", 3), Some("rk3566"), "auto", b"another model").contains("SHA256"));
        assert!(start(fixed("0.8.8", 3), Some("rk3566"), "auto", model).contains("older than 0.9.2"));
        assert!(start(fixed("0.9.8", 3), Some("rk3588"), "auto", model).contains("converted for the rk3566"));
        assert!(start(fixed("0.9.8", 3), None, "auto", model).contains("device tree"));
        assert!(start(fixed("0.9.8", 3), Some("rk3566"), "0,1", model).contains("one NPU core"));
        let mut broken = fixed("0.9.8", 3);
        broken.fail_load = true;
        assert!(start(broken, Some("rk3566"), "auto", model).contains("could not load"));
        let mut wrong = fixed("0.9.8", 3);
        wrong.attrs[0] = Built::q4(0, 4, 4, 4, Layout::Nchw, 0, 0.1).attr;
        let why = start(wrong, Some("rk3566"), "auto", model);
        assert!(why.contains("[1, 4, 2, 2]") && why.contains("[1, 4, 4, 4]"), "{why}");
        let e = Engine::start(&card, model, fixed("0.9.5", 3), Some("rk3566"), "auto", &o).unwrap();
        assert!(e.warnings[0].contains("0.9.8"));
        let classes = Overrides { classes: Some(vec![7]), ..Overrides::default() };
        assert!(Engine::start(&card, model, fixed("0.9.8", 3), Some("rk3566"), "auto", &classes)
            .err()
            .unwrap()
            .contains("class 7"));
        let older = card_json(POST, OUTS, "model", 32, 32, &LABELS)
            .replace("2.5.0", "2.6.0")
            .replace(&"0".repeat(64), &sha256_hex(model));
        assert!(Engine::start(older.as_bytes(), model, fixed("0.9.8", 3), Some("rk3566"), "auto", &o)
            .err()
            .unwrap()
            .contains("older than the toolkit"));
    }
}
