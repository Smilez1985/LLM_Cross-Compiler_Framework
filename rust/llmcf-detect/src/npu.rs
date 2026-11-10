//! The one door to the NPU: the trait `Npu` (load a model from its bytes, its inputs and outputs with their
//! dimensions, layout, type and quantisation, the core mask, a run with input buffers that hands back the output
//! buffers) and what the engine checks around it: the runtime's and the driver's versions against the card and the
//! development guide's minimum, the model's SoC against the board, and the card's outputs against the model's.
//! rknn.rs is the implementation with Rockchip's runtime; sim.rs the simulated NPU of the tests (feature sim-npu).

use crate::card::{Card, OutputSpec, Post};

/// The element type of a tensor as the runtime reports it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DType {
    F32,
    F16,
    I8,
    U8,
    I16,
    I32,
}

impl DType {
    pub fn size(self) -> usize {
        match self {
            DType::I8 | DType::U8 => 1,
            DType::F16 | DType::I16 => 2,
            DType::F32 | DType::I32 => 4,
        }
    }
    #[cfg(feature = "sim-npu")]
    pub fn word(self) -> &'static str {
        match self {
            DType::F32 => "float32",
            DType::F16 => "float16",
            DType::I8 => "int8",
            DType::U8 => "uint8",
            DType::I16 => "int16",
            DType::I32 => "int32",
        }
    }
    #[cfg(feature = "sim-npu")]
    pub fn from_word(w: &str) -> Option<DType> {
        [DType::F32, DType::F16, DType::I8, DType::U8, DType::I16, DType::I32].into_iter().find(|d| d.word() == w)
    }
}

/// How a four-dimensional tensor lies in memory; Flat for any other (row-major in the order of its dimensions).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Layout {
    Nchw,
    Nhwc,
    Flat,
}

/// One input or output of the loaded model.
#[derive(Clone, Debug, PartialEq)]
pub struct Attr {
    pub index: u32,
    pub name: String,
    /// the dimensions as the runtime reports them (NHWC order for an NHWC tensor)
    pub dims: Vec<u32>,
    pub layout: Layout,
    pub dtype: DType,
    pub zp: i32,
    pub scale: f32,
}

impl Attr {
    /// The logical shape: NCHW order for four dimensions, whatever the layout; else the dimensions as they are.
    pub fn logical(&self) -> Vec<u32> {
        match (self.layout, self.dims.as_slice()) {
            (Layout::Nhwc, [n, h, w, c]) => vec![*n, *c, *h, *w],
            _ => self.dims.clone(),
        }
    }
    pub fn elems(&self) -> usize {
        self.dims.iter().map(|d| *d as usize).product()
    }
}

/// The runtime's API version and the kernel driver's version, as the runtime reports them.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Versions {
    pub api: String,
    pub driver: String,
}

#[derive(Debug)]
pub struct NpuError(pub String);

impl std::fmt::Display for NpuError {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

/// The NPU as the engine uses it.
pub trait Npu: Send {
    /// Loads the model from its bytes (the runtime parses it).
    fn load(&mut self, model: &[u8]) -> Result<(), NpuError>;
    /// The runtime's and the driver's versions (after load: the runtime answers them for a loaded model).
    fn versions(&self) -> Result<Versions, NpuError>;
    fn inputs(&self) -> &[Attr];
    fn outputs(&self) -> &[Attr];
    /// The NPU cores this model runs on (a bit per core; 0: the runtime decides).
    fn set_core_mask(&mut self, mask: u32) -> Result<(), NpuError>;
    /// One run: each input's bytes in, each output's bytes out (in the output's own type and layout).
    fn run(&mut self, inputs: &[&[u8]]) -> Result<Vec<Vec<u8>>, NpuError>;
}

/// The leading MAJOR.MINOR.PATCH of a version text such as "2.3.0 (c949ad889d@2024-11-07T11:35:33)" or "v0.9.8".
pub fn leading_version(text: &str) -> Option<(u32, u32, u32)> {
    let t = text.trim().trim_start_matches(['v', 'V']);
    let end = t.find(|c: char| !(c.is_ascii_digit() || c == '.')).unwrap_or(t.len());
    let mut it = t[..end].split('.').map(|p| p.parse::<u32>().ok());
    Some((it.next()??, it.next()??, it.next().flatten().unwrap_or(0)))
}

/// The driver's minimum (the development guide's) and the version the 2.5.0 quick start recommends.
pub const DRIVER_MIN: (u32, u32, u32) = (0, 9, 2);
pub const DRIVER_RECOMMENDED: (u32, u32, u32) = (0, 9, 8);

/// The version checks: the runtime at least the toolkit that converted the model (and of its major version), the
/// driver at least 0.9.2 (refused below: an NPU job on an unsupported driver can hang the device for every engine of
/// the board). Ok with the warnings to show, or the reason it is refused.
pub fn check_versions(v: &Versions, card: &Card) -> Result<Vec<String>, String> {
    let api = leading_version(&v.api)
        .ok_or_else(|| format!("the NPU runtime names no version it can be checked by ({})", short(&v.api)))?;
    let driver = leading_version(&v.driver)
        .ok_or_else(|| format!("the NPU driver names no version it can be checked by ({})", short(&v.driver)))?;
    let t = card.toolkit;
    if api.0 != t.0 || api < t {
        return Err(format!(
            "the NPU runtime {}.{}.{} is older than the toolkit {}.{}.{} that converted the model (or of another major \
             version); a runtime of at least {}.{}.{} is needed",
            api.0, api.1, api.2, t.0, t.1, t.2, t.0, t.1, t.2
        ));
    }
    if driver < DRIVER_MIN {
        return Err(format!(
            "the NPU driver {}.{}.{} is older than 0.9.2, the runtime's minimum: the engine does not run models on it \
             (an NPU job on an unsupported driver can hang the NPU for every engine of the board)",
            driver.0, driver.1, driver.2
        ));
    }
    let mut warnings = Vec::new();
    if driver < DRIVER_RECOMMENDED {
        warnings.push(format!(
            "the NPU driver {}.{}.{} is older than 0.9.8, which Rockchip's runtime 2.5.0 recommends",
            driver.0, driver.1, driver.2
        ));
    }
    Ok(warnings)
}

fn short(s: &str) -> String {
    s.chars().filter(|c| !c.is_control()).take(60).collect()
}

/// The board's SoC from /proc/device-tree/compatible (NUL-separated "vendor,model" words): the first rockchip one.
pub fn soc_from_compatible(raw: &[u8]) -> Option<String> {
    raw.split(|b| *b == 0).filter_map(|w| std::str::from_utf8(w).ok()).find_map(|w| {
        w.strip_prefix("rockchip,")
            .filter(|m| (2..=12).contains(&m.len()) && m.bytes().all(|b| b.is_ascii_alphanumeric()))
            .map(str::to_ascii_lowercase)
    })
}

/// Whether a model converted for `platform` runs on the board's `soc`: RK3566 and RK3568 are interchangeable, every
/// other pair exact.
pub fn soc_fits(platform: &str, soc: &str) -> bool {
    let family = |s: &str| if s == "rk3566" || s == "rk3568" { "rk356x".to_string() } else { s.to_string() };
    family(platform) == family(soc)
}

/// NPU cores per SoC (the core mask is refused on a single-core SoC).
pub fn npu_cores(soc: &str) -> u32 {
    match soc {
        "rk3588" => 3,
        "rk3576" => 2,
        _ => 1,
    }
}

/// The core mask of `--npu-cores` ("auto", or a list of cores such as "0,1") for the board's SoC.
pub fn core_mask(spec: &str, soc: &str) -> Result<u32, String> {
    if spec == "auto" {
        return Ok(0);
    }
    let cores = npu_cores(soc);
    if cores == 1 {
        return Err(format!("the {soc} has one NPU core: --npu-cores takes only auto there"));
    }
    let mut mask = 0u32;
    for part in spec.split(',') {
        let c: u32 =
            part.parse().map_err(|_| format!("--npu-cores is auto or a list of cores such as 0,1 (not {spec})"))?;
        if c >= cores || mask & (1 << c) != 0 {
            return Err(format!("the {soc} has the NPU cores 0 to {}; --npu-cores names each once", cores - 1));
        }
        mask |= 1 << c;
    }
    Ok(mask)
}

/// The card's outputs against the model's: every output the card names is there, with the logical shape it says;
/// a mismatch is refused, naming the output and both shapes. Roles `auto` are found by their shape (the zoo's rule:
/// for DFL boxes the channel count is 4 times reg_max, scores have the class count, a score sum has one channel; for
/// decoded boxes one dimension is 4). Returns the role of every model output (None: not used).
pub fn match_outputs(card: &Card, outs: &[Attr]) -> Result<Vec<Option<String>>, String> {
    let mut roles: Vec<Option<String>> = vec![None; outs.len()];
    for spec in &card.outputs {
        let Some(pos) = outs.iter().position(|a| a.index == spec.index) else {
            return Err(format!("the card names output {}, the model has {} outputs", spec.index, outs.len()));
        };
        let attr = &outs[pos];
        let logical = attr.logical();
        if let Some(shape) = &spec.shape {
            if *shape != logical {
                return Err(format!(
                    "output {} ({}): the card says the shape {:?}, the model has {:?}",
                    spec.index,
                    short(&attr.name),
                    shape,
                    logical
                ));
            }
        }
        roles[pos] = Some(if spec.role == "auto" { auto_role(card, spec, &logical)? } else { spec.role.clone() });
    }
    Ok(roles)
}

fn auto_role(card: &Card, spec: &OutputSpec, shape: &[u32]) -> Result<String, String> {
    let classes = card.labels.len() as u32;
    let channels = if shape.len() == 4 { Some(shape[1]) } else { None };
    let found = match &card.post {
        Post::YoloAnchor { .. } | Post::YoloAnchorFree { .. } => Some("boxes"),
        Post::YoloDfl { reg_max, .. } | Post::YoloNmsFree { dfl: Some((_, reg_max)), .. } => match channels {
            Some(c) if c == 4 * reg_max => Some("boxes"),
            Some(c) if c == classes => Some("scores"),
            Some(1) => Some("score_sum"),
            _ => None,
        },
        Post::YoloNmsFree { dfl: None, .. } => match shape {
            [1, a, _] | [1, _, a] if *a == 4 => Some("boxes"),
            [1, a, _] | [1, _, a] if *a == classes => Some("scores"),
            _ => None,
        },
        _ => None,
    };
    found
        .map(str::to_string)
        .ok_or_else(|| format!("output {}: its role cannot be told by its shape {shape:?}", spec.index))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_are_read_from_their_leading_numbers() {
        assert_eq!(leading_version("2.3.0 (c949ad889d@2024-11-07T11:35:33)"), Some((2, 3, 0)));
        assert_eq!(leading_version("v0.9.8"), Some((0, 9, 8)));
        assert_eq!(leading_version("0.9"), Some((0, 9, 0)));
        assert_eq!(leading_version("unknown"), None);
    }

    #[test]
    fn the_soc_comes_from_the_device_tree_and_rk356x_models_are_interchangeable() {
        assert_eq!(soc_from_compatible(b"radxa,zero3\0rockchip,rk3566\0"), Some("rk3566".into()));
        assert_eq!(soc_from_compatible(b"raspberrypi,5-model-b\0brcm,bcm2712\0"), None);
        assert!(soc_fits("rk3566", "rk3568") && soc_fits("rk3588", "rk3588"));
        assert!(!soc_fits("rk3588", "rk3566") && !soc_fits("rk3576", "rk3588"));
    }

    #[test]
    fn the_core_mask_is_refused_on_a_single_core_soc() {
        assert_eq!(core_mask("auto", "rk3566"), Ok(0));
        assert!(core_mask("0", "rk3566").unwrap_err().contains("one NPU core"));
        assert_eq!(core_mask("0,2", "rk3588"), Ok(0b101));
        assert!(core_mask("0,3", "rk3588").is_err() && core_mask("1,1", "rk3576").is_err());
        assert!(core_mask("x", "rk3588").is_err());
    }
}
