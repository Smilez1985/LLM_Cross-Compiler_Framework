//! The simulated NPU of the tests (cargo feature sim-npu, never in a release build): a recorded model in a folder.
//!
//! - `model.json`: the runtime's versions (`api`, `driver`), the inputs and outputs with their attributes
//!   (`index`, `name`, `dims` as the runtime reports them, `layout` nchw, nhwc or flat, `type`, `zp`, `scale`), and
//!   optionally a failure to play (`fail`: "load" or "run") and a time each run takes (`delay_ms`, at most 10 s);
//! - the outputs per input: `<sha256 of the input tensor>/out<i>.bin`, else `default/out<i>.bin`, raw little-endian
//!   in the output's type and layout;
//! - `compatible`: the board's device tree compatible list as the simulated board has it.
//!
//! A run checks the input's size against its attributes, as the runtime does.

use crate::engine::sha256_hex;
use crate::npu::{Attr, DType, Layout, Npu, NpuError, Versions};
use serde_json::Value;
use std::path::{Path, PathBuf};

pub struct SimNpu {
    dir: PathBuf,
    spec: Value,
    inputs: Vec<Attr>,
    outputs: Vec<Attr>,
    loaded: bool,
}

fn err(s: impl Into<String>) -> NpuError {
    NpuError(s.into())
}

fn attrs(v: &Value) -> Result<Vec<Attr>, NpuError> {
    let mut out = Vec::new();
    for a in v.as_array().ok_or_else(|| err("model.json: inputs and outputs are lists"))? {
        let dims: Vec<u32> =
            a["dims"].as_array().ok_or_else(|| err("dims"))?.iter().map(|d| d.as_u64().unwrap_or(0) as u32).collect();
        out.push(Attr {
            index: a["index"].as_u64().ok_or_else(|| err("index"))? as u32,
            name: a["name"].as_str().unwrap_or("").to_string(),
            layout: match a["layout"].as_str() {
                Some("nhwc") => Layout::Nhwc,
                Some("nchw") => Layout::Nchw,
                _ => Layout::Flat,
            },
            dtype: DType::from_word(a["type"].as_str().unwrap_or("")).ok_or_else(|| err("type"))?,
            zp: a["zp"].as_i64().unwrap_or(0) as i32,
            scale: a["scale"].as_f64().unwrap_or(1.0) as f32,
            dims,
        });
    }
    Ok(out)
}

impl SimNpu {
    pub fn open(dir: &Path) -> Result<SimNpu, NpuError> {
        let text = std::fs::read(dir.join("model.json"))
            .map_err(|e| err(format!("the simulated NPU has no model.json ({e})")))?;
        let spec: Value = serde_json::from_slice(&text).map_err(|e| err(format!("model.json: {e}")))?;
        let inputs = attrs(&spec["inputs"])?;
        let outputs = attrs(&spec["outputs"])?;
        Ok(SimNpu { dir: dir.to_path_buf(), spec, inputs, outputs, loaded: false })
    }

    fn fails(&self, at: &str) -> bool {
        self.spec["fail"].as_str() == Some(at)
    }
}

impl Npu for SimNpu {
    fn load(&mut self, model: &[u8]) -> Result<(), NpuError> {
        if self.fails("load") || model.is_empty() {
            return Err(err("RKNN_ERR_MODEL_INVALID (simulated)"));
        }
        self.loaded = true;
        Ok(())
    }

    fn versions(&self) -> Result<Versions, NpuError> {
        Ok(Versions {
            api: self.spec["api"].as_str().unwrap_or("2.5.0 (simulated)").to_string(),
            driver: self.spec["driver"].as_str().unwrap_or("0.9.8").to_string(),
        })
    }

    fn inputs(&self) -> &[Attr] {
        &self.inputs
    }

    fn outputs(&self) -> &[Attr] {
        &self.outputs
    }

    fn set_core_mask(&mut self, _mask: u32) -> Result<(), NpuError> {
        Ok(())
    }

    fn run(&mut self, inputs: &[&[u8]]) -> Result<Vec<Vec<u8>>, NpuError> {
        if !self.loaded || self.fails("run") {
            return Err(err("RKNN_ERR_FAIL (simulated)"));
        }
        if inputs.len() != self.inputs.len() {
            return Err(err("the number of inputs differs from the model's"));
        }
        for (a, i) in self.inputs.iter().zip(inputs) {
            let float = a.dtype == DType::F32;
            let want = a.elems() * if float { 4 } else { 1 };
            if i.len() != want {
                return Err(err(format!("input {} has {} bytes, the model takes {want}", a.index, i.len())));
            }
        }
        if let Some(ms) = self.spec["delay_ms"].as_u64() {
            std::thread::sleep(std::time::Duration::from_millis(ms.min(10_000)));
        }
        let key = sha256_hex(inputs.first().copied().unwrap_or(&[]));
        let folder = if self.dir.join(&key).is_dir() { self.dir.join(&key) } else { self.dir.join("default") };
        let mut out = Vec::new();
        for i in 0..self.outputs.len() {
            let data = std::fs::read(folder.join(format!("out{i}.bin")))
                .map_err(|e| err(format!("no recorded output {i} ({e})")))?;
            out.push(data);
        }
        Ok(out)
    }
}
