//! llmcf-detect: the LLM Cross-Compiler Framework's vision engine on the board.
//!
//! `llmcf-detect serve --port P --model FILE --card FILE --rknn-runtime board|pinned|PATH [OPTION VALUE ...]` runs an
//! RKNN vision model on Rockchip's NPU through its model card (card.rs, schema llmcf-model-card/1): an image or the
//! frames of its source are prepared as the card says (prep.rs), run on the NPU (npu.rs: the trait; rknn.rs:
//! Rockchip's runtime loaded with dlopen by its absolute path), decoded by the card's post-processing kind (post.rs)
//! and answered as JSON (llmcf-vision-result/1) or as an event stream (server.rs). It listens on 127.0.0.1 only and
//! is reached through llmcf-gateway, which checks every request by the block's route table. `check-card FILE` checks
//! a model card by the engine's own rules; `version` names the program.
//!
//! A test build (cargo feature sim-npu, refused by a release build) takes `--sim-npu FOLDER` in place of the runtime:
//! the simulated NPU of the tests (sim.rs). Nothing is read from the environment, no shell is run, nothing is
//! downloaded, and the log holds numbers and states only, never an image or a result's content.
//!
//! Exit status: 0 done, 1 the engine could not start or stopped, 2 not a call it takes.
#![deny(unsafe_code)]

#[cfg(all(feature = "sim-npu", not(debug_assertions)))]
compile_error!("the simulated NPU (feature sim-npu) is for test builds only; a release build never carries it");

mod args;
mod card;
mod engine;
mod image;
mod json;
mod npu;
mod post;
mod prep;
mod rknn;
mod server;
#[cfg(feature = "sim-npu")]
mod sim;
mod tensor;

use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering::Relaxed};
use std::sync::{Arc, Mutex};

/// The largest model file the engine reads.
const MAX_MODEL: u64 = 2 << 30;
const COMPATIBLE: &str = "/proc/device-tree/compatible";

/// The NPU and the board's device tree compatible list.
type Board = (Box<dyn npu::Npu>, Option<Vec<u8>>);

fn npu_of(s: &args::Serve) -> Result<Board, String> {
    #[cfg(feature = "sim-npu")]
    if let Some(dir) = &s.sim_npu {
        let sim = sim::SimNpu::open(dir).map_err(|e| e.0)?;
        return Ok((Box::new(sim), std::fs::read(dir.join("compatible")).ok()));
    }
    let (path, sha) = rknn::resolve(&s.runtime)?;
    let rt = rknn::Rknn::open(&path, sha)?;
    Ok((Box::new(rt), std::fs::read(COMPATIBLE).ok()))
}

fn read_capped(path: &std::path::Path, cap: u64, what: &str) -> Result<Vec<u8>, String> {
    let meta = std::fs::metadata(path).map_err(|_| format!("the {what} is not there"))?;
    if !meta.is_file() || meta.len() > cap {
        return Err(format!("the {what} is no file of at most {} MiB", cap >> 20));
    }
    std::fs::read(path).map_err(|_| format!("the {what} cannot be read"))
}

fn load(s: &args::Serve) -> Result<engine::Engine, String> {
    let card = read_capped(&s.card, card::MAX_CARD_BYTES as u64, "model card")?;
    let model = read_capped(&s.model, MAX_MODEL, "model")?;
    let (npu, compatible) = npu_of(s)?;
    let soc = compatible.as_deref().and_then(npu::soc_from_compatible);
    let o = engine::Overrides {
        score_pct: s.score_pct,
        nms_pct: s.nms_pct,
        max_results: s.max_results,
        classes: s.classes.clone(),
    };
    engine::Engine::start(&card, &model, npu, soc.as_deref(), &s.npu_cores, &o)
}

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let call = match args::parse(&argv) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("llmcf-detect: {e}");
            return ExitCode::from(2);
        }
    };
    let s = match call {
        args::Call::Version => {
            println!("llmcf-detect {}", env!("CARGO_PKG_VERSION"));
            return ExitCode::SUCCESS;
        }
        args::Call::CheckCard(path) => {
            return match read_capped(&path, card::MAX_CARD_BYTES as u64, "model card").and_then(|b| card::parse(&b)) {
                Ok(c) => {
                    println!(
                        "llmcf-detect: the model card is valid ({}, {}, {} labels)",
                        c.task,
                        c.post.word(),
                        c.labels.len()
                    );
                    ExitCode::SUCCESS
                }
                Err(e) => {
                    println!("llmcf-detect: the model card is refused: {e}");
                    ExitCode::from(1)
                }
            };
        }
        args::Call::Serve(s) => s,
    };
    let source = match &s.source_files {
        None => None,
        Some(dir) => match server::Source::open(dir, s.source_fps) {
            Ok(src) => Some(src),
            Err(e) => {
                eprintln!("llmcf-detect: {e}");
                return ExitCode::from(1);
            }
        },
    };
    let rt = match tokio::runtime::Builder::new_current_thread().enable_all().max_blocking_threads(4).build() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("llmcf-detect: no runtime: {e}");
            return ExitCode::from(1);
        }
    };
    let (tx, _) = tokio::sync::watch::channel(None);
    let state = Arc::new(server::State {
        engine: Arc::new(Mutex::new(None)),
        ready: AtomicBool::new(false),
        failed: Mutex::new(None),
        uploads: AtomicU64::new(0),
        followers: AtomicUsize::new(0),
        results: tx,
        source,
    });
    let port = s.port;
    let result = rt.block_on(async move {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", port))
            .await
            .map_err(|e| format!("cannot listen on port {port}: {e}"))?;
        eprintln!("llmcf-detect {}: listening on loopback port {port}; loading the model", env!("CARGO_PKG_VERSION"));
        let st = state.clone();
        let loading = tokio::task::spawn_blocking(move || load(&s));
        let serving = tokio::spawn(server::serve(state.clone(), listener));
        match loading.await {
            Ok(Ok(eng)) => {
                eprintln!(
                    "llmcf-detect: model ready ({}, {} classes, runtime {}, driver {})",
                    eng.card.post.word(),
                    eng.card.labels.len(),
                    eng.versions.api.chars().take(40).collect::<String>(),
                    eng.versions.driver.chars().take(20).collect::<String>()
                );
                for w in &eng.warnings {
                    eprintln!("llmcf-detect: warning: {w}");
                }
                if let Ok(mut g) = st.engine.lock() {
                    *g = Some(eng);
                }
                st.ready.store(true, Relaxed);
            }
            Ok(Err(e)) => {
                if let Ok(mut f) = st.failed.lock() {
                    *f = Some(e.clone());
                }
                return Err(e);
            }
            Err(_) => return Err("the model's loading stopped".into()),
        }
        serving.await.map_err(|_| "the server stopped".to_string())?
    });
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("llmcf-detect: {e}");
            ExitCode::from(1)
        }
    }
}
