//! llmcf-gateway: the LLM Cross-Compiler Framework's token-saving middleware in front of the board's llama-server.
//!
//! `llmcf-gateway serve [OPTION VALUE ...]` runs the OpenAI-compatible front (rules.rs: strict JSON, the context
//! budget, the savings; server.rs: routes, limits, TLS in the local-network mode; routes.rs: the route table as data,
//! `--routes FILE` from the bundle's blocks, so a new engine needs no change here; generic.rs: the checked
//! passthrough of a route a block declares); `llmcf-gateway probe --port P
//! [--tls] [--complete] [--bench N]` checks a running gateway from the board itself (probe.rs). It reads no
//! environment but HOME (its state folder and the model server's key file live below it), runs no shell and talks
//! only to the board's engines on 127.0.0.1; with --engine (or --llm-unit) it starts and stops exactly those systemd
//! units (control.rs), together or in turn when they do not fit together (engines.rs).
//!
//! Exit status: 0 done, 1 a check failed or the server stopped with an error, 2 not a call it takes.
#![forbid(unsafe_code)]

mod args;
mod audio;
mod control;
mod engines;
mod generic;
mod images;
mod limits;
mod memory;
mod peer;
mod probe;
mod queue;
mod routes;
mod rules;
mod server;
mod tls;

use std::process::ExitCode;

fn main() -> ExitCode {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let call = match args::parse(&argv) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("llmcf-gateway: {e}");
            return ExitCode::from(2);
        }
    };
    let rt = match tokio::runtime::Builder::new_current_thread().enable_all().build() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("llmcf-gateway: no runtime: {e}");
            return ExitCode::from(1);
        }
    };
    match call {
        args::Call::Version => {
            println!("llmcf-gateway {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        args::Call::Probe(p) => ExitCode::from(rt.block_on(probe::run(p)) as u8),
        args::Call::Serve(cfg) => {
            let (state, tls) = match server::build_state(cfg) {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("llmcf-gateway: {e}");
                    return ExitCode::from(1);
                }
            };
            match rt.block_on(server::serve(state, tls)) {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => {
                    eprintln!("llmcf-gateway: {e}");
                    ExitCode::from(1)
                }
            }
        }
    }
}
