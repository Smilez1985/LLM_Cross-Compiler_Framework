//! Rockchip's NPU runtime (librknnrt.so) behind the trait `Npu`, loaded at start with dlopen from an absolute path,
//! never by a library name, so no search path, LD_LIBRARY_PATH or other package can put another library in its
//! place. The runtime is never linked and never shipped (its licence gives no right to pass it on): the board's own
//! copy (a vendor package at its fixed places) or the pinned download, which is checked against its SHA256 here at
//! every start as well.
//!
//! The FFI declarations are hand-written for exactly the functions and structures the engine uses (rknn_api.h of
//! rknn-toolkit2 2.5.0 as documented in Rockchip's development guide); no header of Rockchip's is copied or generated
//! into the repository. The structure sizes are fixed here and checked against what the runtime reports (a query
//! with the wrong size is refused by the runtime, and every answer is checked for sane values); the first run on a
//! real board checks them for real (ROADMAP: "Vision on the NPU on a real board: the first NPU run").
//!
//! The only unsafe code of the engine is in this file.
#![allow(unsafe_code)]

use crate::npu::{Attr, DType, Layout, Npu, NpuError, Versions};
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::path::Path;

/// The places of the board's own copy (Radxa's rknpu2 packages put it at both), in this order.
pub const BOARD_PLACES: [&str; 2] = ["/usr/lib/librknnrt.so", "/usr/lib/aarch64-linux-gnu/librknnrt.so"];
/// The pinned download: rknn-toolkit2's rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so at commit
/// 2c723177a94fac65cbc036d722907ddbc91980cf (tag v2.5.0), placed root-owned by the board installer.
pub const PINNED_PLACE: &str = "/opt/llmcf/vendor/rknn/2.5.0/librknnrt.so";
pub const PINNED_SHA256: &str = "4b6cc575bedf8952b13db4b1190fa3b3d2ac213f00be774ff6ccd19d651970c0";

const MAX_DIMS: usize = 16;
const MAX_NAME: usize = 256;
const QUERY_IN_OUT_NUM: c_int = 0;
const QUERY_INPUT_ATTR: c_int = 1;
const QUERY_OUTPUT_ATTR: c_int = 2;
const QUERY_SDK_VERSION: c_int = 5;

type Ctx = u64;

#[repr(C)]
struct InOutNum {
    n_input: u32,
    n_output: u32,
}

#[repr(C)]
struct TensorAttr {
    index: u32,
    n_dims: u32,
    dims: [u32; MAX_DIMS],
    name: [c_char; MAX_NAME],
    n_elems: u32,
    size: u32,
    fmt: c_int,
    kind: c_int,
    qnt_type: c_int,
    fl: i8,
    zp: i32,
    scale: f32,
    w_stride: u32,
    size_with_stride: u32,
    pass_through: u8,
    h_stride: u32,
}

#[repr(C)]
struct SdkVersion {
    api_version: [c_char; 256],
    drv_version: [c_char; 256],
}

#[repr(C)]
struct Input {
    index: u32,
    buf: *mut c_void,
    size: u32,
    pass_through: u8,
    kind: c_int,
    fmt: c_int,
}

#[repr(C)]
struct Output {
    want_float: u8,
    is_prealloc: u8,
    index: u32,
    buf: *mut c_void,
    size: u32,
}

/// The sizes the runtime's 2.x interface has on aarch64 (rknn_tensor_attr, rknn_sdk_version, rknn_input,
/// rknn_output), checked by the tests so a change of these declarations is seen.
#[cfg(test)]
pub const SIZES: [(&str, usize); 4] = [
    ("rknn_tensor_attr", std::mem::size_of::<TensorAttr>()),
    ("rknn_sdk_version", std::mem::size_of::<SdkVersion>()),
    ("rknn_input", std::mem::size_of::<Input>()),
    ("rknn_output", std::mem::size_of::<Output>()),
];

type InitFn = unsafe extern "C" fn(*mut Ctx, *mut c_void, u32, u32, *mut c_void) -> c_int;
type DestroyFn = unsafe extern "C" fn(Ctx) -> c_int;
type QueryFn = unsafe extern "C" fn(Ctx, c_int, *mut c_void, u32) -> c_int;
type InputsSetFn = unsafe extern "C" fn(Ctx, u32, *mut Input) -> c_int;
type RunFn = unsafe extern "C" fn(Ctx, *mut c_void) -> c_int;
type OutputsGetFn = unsafe extern "C" fn(Ctx, u32, *mut Output, *mut c_void) -> c_int;
type OutputsReleaseFn = unsafe extern "C" fn(Ctx, u32, *mut Output) -> c_int;
type CoreMaskFn = unsafe extern "C" fn(Ctx, c_int) -> c_int;

struct Api {
    init: InitFn,
    destroy: DestroyFn,
    query: QueryFn,
    inputs_set: InputsSetFn,
    run: RunFn,
    outputs_get: OutputsGetFn,
    outputs_release: OutputsReleaseFn,
    core_mask: CoreMaskFn,
}

pub struct Rknn {
    api: Api,
    ctx: Option<Ctx>,
    inputs: Vec<Attr>,
    outputs: Vec<Attr>,
}

// The context is used by one thread at a time (the engine holds it behind a lock); the runtime's handle is a number.
unsafe impl Send for Rknn {}

fn err(s: impl Into<String>) -> NpuError {
    NpuError(s.into())
}

fn code(what: &str, ret: c_int) -> Result<(), NpuError> {
    if ret == 0 {
        Ok(())
    } else {
        let name = match ret {
            -1 => "RKNN_ERR_FAIL",
            -2 => "RKNN_ERR_TIMEOUT",
            -3 => "RKNN_ERR_DEVICE_UNAVAILABLE",
            -4 => "RKNN_ERR_MALLOC_FAIL",
            -5 => "RKNN_ERR_PARAM_INVALID (a structure of a size this runtime does not take)",
            -6 => "RKNN_ERR_MODEL_INVALID",
            -7 => "RKNN_ERR_CTX_INVALID",
            -8 => "RKNN_ERR_INPUT_INVALID",
            -9 => "RKNN_ERR_OUTPUT_INVALID",
            -10 => "RKNN_ERR_DEVICE_UNMATCH",
            -12 => "RKNN_ERR_TARGET_PLATFORM_UNMATCH",
            _ => "an error",
        };
        Err(err(format!("{what}: {name} ({ret})")))
    }
}

fn cstr(raw: &[c_char]) -> String {
    let bytes: Vec<u8> = raw.iter().take_while(|c| **c != 0).map(|c| *c as u8).collect();
    String::from_utf8_lossy(&bytes).chars().filter(|c| !c.is_control()).collect()
}

/// Which runtime file `--rknn-runtime` names: `board` (the first of BOARD_PLACES that is there), `pinned` (the
/// pinned download, checked by its SHA256) or an absolute path (a file the person placed, taken as it is).
pub fn resolve(spec: &str) -> Result<(String, Option<&'static str>), String> {
    match spec {
        "board" => BOARD_PLACES
            .iter()
            .find(|p| Path::new(p).is_file())
            .map(|p| (p.to_string(), None))
            .ok_or_else(|| format!("the board has no NPU runtime of its own at {}", BOARD_PLACES.join(" or "))),
        "pinned" => Ok((PINNED_PLACE.to_string(), Some(PINNED_SHA256))),
        p if p.starts_with('/') && !p.split('/').any(|s| s == "..") => Ok((p.to_string(), None)),
        _ => Err("--rknn-runtime is board, pinned or an absolute path".into()),
    }
}

impl Rknn {
    /// Opens the runtime at `path` (absolute), checking its SHA256 first when one is pinned.
    pub fn open(path: &str, sha256: Option<&str>) -> Result<Rknn, String> {
        if !path.starts_with('/') {
            return Err("the NPU runtime is loaded only by its absolute path".into());
        }
        let meta = std::fs::symlink_metadata(path).map_err(|_| format!("the NPU runtime {path} is not there"))?;
        if !meta.is_file() && sha256.is_some() {
            return Err(format!("the pinned NPU runtime {path} is not a plain file"));
        }
        if let Some(want) = sha256 {
            let data = std::fs::read(path).map_err(|e| format!("the NPU runtime cannot be read: {e}"))?;
            if crate::engine::sha256_hex(&data) != want {
                return Err(format!("the NPU runtime {path} is not the pinned file (its SHA256 differs)"));
            }
        }
        let c = CString::new(path).map_err(|_| "the runtime's path holds a NUL byte".to_string())?;
        // SAFETY: dlopen with a NUL-terminated absolute path; the handle is kept for the process's life.
        let handle = unsafe { libc::dlopen(c.as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL) };
        if handle.is_null() {
            // SAFETY: dlerror returns a NUL-terminated message or null.
            let why = unsafe {
                let e = libc::dlerror();
                if e.is_null() {
                    String::new()
                } else {
                    CStr::from_ptr(e).to_string_lossy().chars().take(200).collect()
                }
            };
            return Err(format!("the NPU runtime could not be loaded: {why}"));
        }
        let sym = |name: &str| -> Result<*mut c_void, String> {
            let n = CString::new(name).map_err(|_| "symbol".to_string())?;
            // SAFETY: a symbol lookup in the handle opened above.
            let p = unsafe { libc::dlsym(handle, n.as_ptr()) };
            if p.is_null() {
                Err(format!("the NPU runtime has no {name}: it is not Rockchip's librknnrt.so 2.x"))
            } else {
                Ok(p)
            }
        };
        // SAFETY: each symbol is the runtime's function of exactly the declared signature (rknn_api.h 2.x).
        let api = unsafe {
            Api {
                init: std::mem::transmute::<*mut c_void, InitFn>(sym("rknn_init")?),
                destroy: std::mem::transmute::<*mut c_void, DestroyFn>(sym("rknn_destroy")?),
                query: std::mem::transmute::<*mut c_void, QueryFn>(sym("rknn_query")?),
                inputs_set: std::mem::transmute::<*mut c_void, InputsSetFn>(sym("rknn_inputs_set")?),
                run: std::mem::transmute::<*mut c_void, RunFn>(sym("rknn_run")?),
                outputs_get: std::mem::transmute::<*mut c_void, OutputsGetFn>(sym("rknn_outputs_get")?),
                outputs_release: std::mem::transmute::<*mut c_void, OutputsReleaseFn>(sym("rknn_outputs_release")?),
                core_mask: std::mem::transmute::<*mut c_void, CoreMaskFn>(sym("rknn_set_core_mask")?),
            }
        };
        Ok(Rknn { api, ctx: None, inputs: Vec::new(), outputs: Vec::new() })
    }

    fn ctx(&self) -> Result<Ctx, NpuError> {
        self.ctx.ok_or_else(|| err("no model is loaded"))
    }

    fn attr(&self, cmd: c_int, index: u32) -> Result<Attr, NpuError> {
        // SAFETY: a zeroed plain C structure is a valid value of it.
        let mut a: TensorAttr = unsafe { std::mem::zeroed() };
        a.index = index;
        // SAFETY: the runtime fills at most `size` bytes of the structure we own.
        let ret = unsafe {
            (self.api.query)(
                self.ctx()?,
                cmd,
                &mut a as *mut _ as *mut c_void,
                std::mem::size_of::<TensorAttr>() as u32,
            )
        };
        code("rknn_query(attributes)", ret)?;
        if a.index != index || a.n_dims == 0 || a.n_dims as usize > MAX_DIMS {
            return Err(err("the runtime's tensor attributes are not of the structure the engine knows"));
        }
        let dims = a.dims[..a.n_dims as usize].to_vec();
        let dtype = match a.kind {
            0 => DType::F32,
            1 => DType::F16,
            2 => DType::I8,
            3 => DType::U8,
            4 => DType::I16,
            6 => DType::I32,
            k => return Err(err(format!("a tensor of the runtime's type {k}, which the engine does not read"))),
        };
        let layout = match (a.fmt, dims.len()) {
            (0, 4) => Layout::Nchw,
            (1, 4) => Layout::Nhwc,
            (2, _) => return Err(err("a tensor in the NC1HWC2 layout, which the engine does not read")),
            _ => Layout::Flat,
        };
        let attr = Attr { index, name: cstr(&a.name), dims, layout, dtype, zp: a.zp, scale: a.scale };
        if attr.elems() as u64 != a.n_elems as u64 {
            return Err(err("the runtime's element count does not fit its dimensions"));
        }
        Ok(attr)
    }
}

impl Npu for Rknn {
    fn load(&mut self, model: &[u8]) -> Result<(), NpuError> {
        let mut ctx: Ctx = 0;
        let mut copy = model.to_vec();
        // SAFETY: the runtime reads `size` bytes of the model buffer and writes the context.
        let ret = unsafe {
            (self.api.init)(&mut ctx, copy.as_mut_ptr() as *mut c_void, copy.len() as u32, 0, std::ptr::null_mut())
        };
        code("rknn_init", ret)?;
        self.ctx = Some(ctx);
        let mut n = InOutNum { n_input: 0, n_output: 0 };
        // SAFETY: as in attr().
        let ret = unsafe {
            (self.api.query)(
                ctx,
                QUERY_IN_OUT_NUM,
                &mut n as *mut _ as *mut c_void,
                std::mem::size_of::<InOutNum>() as u32,
            )
        };
        code("rknn_query(in_out_num)", ret)?;
        if n.n_input == 0 || n.n_input > 8 || n.n_output == 0 || n.n_output > 32 {
            return Err(err("the model's inputs and outputs are not what the engine reads"));
        }
        self.inputs = (0..n.n_input).map(|i| self.attr(QUERY_INPUT_ATTR, i)).collect::<Result<_, _>>()?;
        self.outputs = (0..n.n_output).map(|i| self.attr(QUERY_OUTPUT_ATTR, i)).collect::<Result<_, _>>()?;
        Ok(())
    }

    fn versions(&self) -> Result<Versions, NpuError> {
        // SAFETY: a zeroed C structure; the runtime fills at most its size.
        let mut v: SdkVersion = unsafe { std::mem::zeroed() };
        let ret = unsafe {
            (self.api.query)(
                self.ctx()?,
                QUERY_SDK_VERSION,
                &mut v as *mut _ as *mut c_void,
                std::mem::size_of::<SdkVersion>() as u32,
            )
        };
        code("rknn_query(sdk_version)", ret)?;
        Ok(Versions { api: cstr(&v.api_version), driver: cstr(&v.drv_version) })
    }

    fn inputs(&self) -> &[Attr] {
        &self.inputs
    }

    fn outputs(&self) -> &[Attr] {
        &self.outputs
    }

    fn set_core_mask(&mut self, mask: u32) -> Result<(), NpuError> {
        // SAFETY: a call with the loaded context and a number.
        code("rknn_set_core_mask", unsafe { (self.api.core_mask)(self.ctx()?, mask as c_int) })
    }

    fn run(&mut self, inputs: &[&[u8]]) -> Result<Vec<Vec<u8>>, NpuError> {
        let ctx = self.ctx()?;
        if inputs.len() != self.inputs.len() {
            return Err(err("the number of inputs differs from the model's"));
        }
        let mut copies: Vec<Vec<u8>> = inputs.iter().map(|i| i.to_vec()).collect();
        let mut ins: Vec<Input> = Vec::with_capacity(copies.len());
        for (a, buf) in self.inputs.iter().zip(copies.iter_mut()) {
            let float = buf.len() == a.elems() * 4;
            ins.push(Input {
                index: a.index,
                buf: buf.as_mut_ptr() as *mut c_void,
                size: buf.len() as u32,
                pass_through: 0,
                kind: if float {
                    0
                } else if a.dtype == DType::I8 {
                    2
                } else {
                    3
                },
                fmt: if a.layout == Layout::Nchw { 0 } else { 1 },
            });
        }
        // SAFETY: the input descriptors point into `copies`, alive until the run ends.
        code("rknn_inputs_set", unsafe { (self.api.inputs_set)(ctx, ins.len() as u32, ins.as_mut_ptr()) })?;
        code("rknn_run", unsafe { (self.api.run)(ctx, std::ptr::null_mut()) })?;
        let mut outs: Vec<Output> = (0..self.outputs.len())
            .map(|i| Output { want_float: 0, is_prealloc: 0, index: i as u32, buf: std::ptr::null_mut(), size: 0 })
            .collect();
        // SAFETY: the runtime fills the descriptors with buffers it owns until rknn_outputs_release.
        code("rknn_outputs_get", unsafe {
            (self.api.outputs_get)(ctx, outs.len() as u32, outs.as_mut_ptr(), std::ptr::null_mut())
        })?;
        let mut result = Vec::with_capacity(outs.len());
        for (k, a) in self.outputs.iter().enumerate() {
            let (buf, size) = (outs[k].buf, outs[k].size);
            let want = a.elems() * a.dtype.size();
            if buf.is_null() || size as usize != want {
                // SAFETY: release what the runtime handed out before refusing.
                unsafe { (self.api.outputs_release)(ctx, outs.len() as u32, outs.as_mut_ptr()) };
                return Err(err(format!("output {} came with {size} bytes, its shape has {want}", a.index)));
            }
            // SAFETY: the runtime's buffer holds `size` bytes until it is released below.
            result.push(unsafe { std::slice::from_raw_parts(buf as *const u8, size as usize) }.to_vec());
        }
        // SAFETY: the descriptors the runtime filled above.
        code("rknn_outputs_release", unsafe { (self.api.outputs_release)(ctx, outs.len() as u32, outs.as_mut_ptr()) })?;
        Ok(result)
    }
}

impl Drop for Rknn {
    fn drop(&mut self) {
        if let Some(ctx) = self.ctx.take() {
            // SAFETY: the context made by rknn_init, destroyed once.
            unsafe { (self.api.destroy)(ctx) };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_structures_have_the_sizes_of_the_runtimes_interface() {
        // rknn_tensor_attr: 4 + 4 + 16*4 + 256 + 4 + 4 + 3*4 + 1 (+3) + 4 + 4 + 4 + 4 + 1 (+3) + 4 = 376 bytes
        assert_eq!(SIZES[0], ("rknn_tensor_attr", 376));
        assert_eq!(SIZES[1], ("rknn_sdk_version", 512));
        if cfg!(target_pointer_width = "64") {
            assert_eq!(SIZES[2], ("rknn_input", 32));
            assert_eq!(SIZES[3], ("rknn_output", 24));
        }
    }

    #[test]
    fn the_runtime_is_named_by_an_absolute_path_only() {
        assert_eq!(resolve("pinned").unwrap(), (PINNED_PLACE.to_string(), Some(PINNED_SHA256)));
        assert!(resolve("librknnrt.so").is_err() && resolve("../x.so").is_err() && resolve("/a/../b.so").is_err());
        assert_eq!(resolve("/opt/x/librknnrt.so").unwrap().1, None);
        assert!(Rknn::open("librknnrt.so", None).is_err());
        assert!(Rknn::open("/nonexistent/librknnrt.so", None).err().unwrap().contains("not there"));
        let tmp = std::env::temp_dir().join(format!("llmcf-detect-not-the-runtime-{}", std::process::id()));
        std::fs::write(&tmp, b"not a library").unwrap();
        let p = tmp.to_str().unwrap().to_string();
        assert!(Rknn::open(&p, Some(PINNED_SHA256)).err().unwrap().contains("SHA256"));
        assert!(Rknn::open(&p, None).err().unwrap().contains("could not be loaded"));
        let _ = std::fs::remove_file(tmp);
    }
}
