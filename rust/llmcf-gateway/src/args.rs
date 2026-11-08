//! The gateway's command line: `serve` and `probe`, each option with its own bound. Every other option, a value
//! out of its bounds or an option twice is refused (exit 2); nothing is read from the environment but HOME.

use crate::engines::{self, Kind, Mode, Spec};
use std::collections::HashSet;

/// Where the gateway listens: loopback (127.0.0.1, plain HTTP) or the local network (every address, TLS only).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Bind {
    Loopback,
    Lan,
}

impl Bind {
    pub fn word(self) -> &'static str {
        match self {
            Bind::Loopback => "loopback",
            Bind::Lan => "lan",
        }
    }
}

/// The values of `serve` (the defaults are the bundle block's defaults).
#[derive(Clone, Debug)]
pub struct Serve {
    pub port: u16,
    pub bind: Bind,
    pub upstream_port: u16,
    /// The context size the board's llama-server was started with (the llm component's ctx_size).
    pub ctx_size: u32,
    /// The server's parallel slots: each gets ctx_size / slots tokens.
    pub slots: u32,
    pub max_body_kb: u32,
    pub max_tokens: u32,
    pub rate_per_minute: u32,
    pub max_inflight: u32,
    /// Requests that may wait for a place in flight at once (queue.rs); 0: none wait, a full board answers 429.
    pub queue_max: u32,
    /// How long one request may wait for its place before it is answered 503.
    pub queue_wait_seconds: u32,
    /// MiB of the board's MemAvailable below which no new request goes in (memory.rs; 0: not watched). The bundle
    /// passes half the board's measured reserve; without a measurement this conservative floor holds.
    pub memory_floor_mb: u32,
    /// The memory pressure ("some avg10", percent) above which no new request goes in (0: not watched).
    pub memory_psi_max: u32,
    /// The engines the gateway starts and stops, each its own unit with its measured memory (`--engine`; `--llm-unit
    /// U` is `--engine llm=U:0`). None: it controls nothing.
    pub engines: Vec<Spec>,
    /// together (each started on demand) or in-turn (those that do not fit together take turns; engines.rs)
    pub engines_mode: Mode,
    /// MiB the engines may use together in turn (0: one engine at a time)
    pub engines_budget_mb: u64,
    /// how long an engine stays loaded at least before it may give way to another one in turn
    pub engine_hold_seconds: u32,
    pub idle_stop_minutes: u32,
    /// The loopback port of the speech engine (whisper.cpp's whisper-server; None: the board has none the gateway
    /// speaks to, and the speech route answers 501). An stt `--engine` with a port gives it too.
    pub stt_port: Option<u16>,
    /// The loopback port of the vision engine (a second llama-server with --mmproj; None: no vision, image parts are
    /// refused). A vision `--engine` with a port gives it too.
    pub vision_port: Option<u16>,
    /// The context size the vision engine was started with (the vision component's ctx_size).
    pub vision_ctx_size: u32,
    /// The largest audio upload (the whole multipart body) in MB, apart from the JSON cap max_body_kb.
    pub max_audio_mb: u32,
    /// Images one chat request may carry (0: none).
    pub max_images: u32,
    /// The largest image (decoded bytes) in KB.
    pub max_image_kb: u32,
    /// All images of one request together (decoded bytes) in KB.
    pub max_images_kb: u32,
    /// The tokens one image is counted with against the vision engine's context (a conservative fixed number: the
    /// projector's own count depends on the model and the picture).
    pub image_tokens: u32,
}

impl Default for Serve {
    fn default() -> Serve {
        Serve {
            port: 8088,
            bind: Bind::Loopback,
            upstream_port: 8080,
            ctx_size: 4096,
            slots: 1,
            max_body_kb: 256,
            max_tokens: 512,
            rate_per_minute: 60,
            max_inflight: 2,
            queue_max: 32,
            queue_wait_seconds: 120,
            memory_floor_mb: 64,
            memory_psi_max: 50,
            engines: Vec::new(),
            engines_mode: Mode::Together,
            engines_budget_mb: 0,
            engine_hold_seconds: 30,
            idle_stop_minutes: 0,
            stt_port: None,
            vision_port: None,
            vision_ctx_size: 4096,
            max_audio_mb: 25,
            max_images: 4,
            max_image_kb: 2048,
            max_images_kb: 4096,
            image_tokens: 768,
        }
    }
}

/// The values of `probe`: a check of the running gateway from the board itself (loopback only).
#[derive(Clone, Debug, Default)]
pub struct Probe {
    pub port: u16,
    pub tls: bool,
    pub complete: bool,
    pub bench: u32,
}

#[derive(Debug)]
pub enum Call {
    Version,
    Serve(Serve),
    Probe(Probe),
}

/// A unit name as llmcf-install writes it (manifest.RE_UNIT): llmcf-<name>.service.
pub fn is_unit(s: &str) -> bool {
    let Some(mid) = s.strip_prefix("llmcf-").and_then(|r| r.strip_suffix(".service")) else { return false };
    (1..=40).contains(&mid.len()) && mid.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
}

fn number(name: &str, value: &str, low: u64, high: u64) -> Result<u64, String> {
    if value.is_empty() || value.len() > 10 || !value.bytes().all(|c| c.is_ascii_digit()) {
        return Err(format!("{name} takes a whole number"));
    }
    let n: u64 = value.parse().map_err(|_| format!("{name} takes a whole number"))?;
    if n < low || n > high {
        return Err(format!("{name} must be from {low} to {high}"));
    }
    Ok(n)
}

fn pairs(rest: &[String]) -> Result<Vec<(&str, &str)>, String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    let mut i = 0;
    while i < rest.len() {
        let name = rest[i].as_str();
        if !name.starts_with("--") {
            return Err(format!("{name} is not an option"));
        }
        // --engine is given once per engine
        if name != "--engine" && !seen.insert(name) {
            return Err(format!("{name} is given twice"));
        }
        if matches!(name, "--tls" | "--complete") {
            out.push((name, ""));
            i += 1;
            continue;
        }
        let value = rest.get(i + 1).ok_or_else(|| format!("{name} takes a value"))?;
        out.push((name, value.as_str()));
        i += 2;
    }
    Ok(out)
}

fn port(name: &str, v: &str) -> Result<u16, String> {
    Ok(number(name, v, 1024, 65535)? as u16)
}

/// A port, or 0 for none.
fn port_or_none(name: &str, v: &str) -> Result<Option<u16>, String> {
    if v == "0" {
        return Ok(None);
    }
    port(name, v).map(Some)
}

impl Serve {
    /// The body cap of a chat request: the JSON cap, and with vision as many images as a request may carry in base64
    /// (4 characters for 3 bytes) with their data-URL heads.
    pub fn chat_body_cap(&self) -> usize {
        let json = self.max_body_kb as usize * 1024;
        if self.vision_port.is_none() || self.max_images == 0 {
            return json;
        }
        json + (self.max_images_kb as usize * 1024).div_ceil(3) * 4 + self.max_images as usize * 64
    }

    /// The body cap of an audio upload.
    pub fn audio_body_cap(&self) -> usize {
        self.max_audio_mb as usize * 1024 * 1024
    }
}

pub fn parse(args: &[String]) -> Result<Call, String> {
    let Some(first) = args.first() else { return Err("usage: llmcf-gateway serve|probe [OPTION VALUE ...]".into()) };
    match first.as_str() {
        "--version" | "version" if args.len() == 1 => Ok(Call::Version),
        "serve" => {
            let mut s = Serve::default();
            for (name, v) in pairs(&args[1..])? {
                match name {
                    "--port" => s.port = port(name, v)?,
                    "--bind" => {
                        s.bind = match v {
                            "loopback" => Bind::Loopback,
                            "lan" => Bind::Lan,
                            _ => return Err("--bind is loopback or lan".into()),
                        }
                    }
                    "--upstream-port" => s.upstream_port = port(name, v)?,
                    "--ctx-size" => s.ctx_size = number(name, v, 256, 1 << 20)? as u32,
                    "--slots" => s.slots = number(name, v, 1, 64)? as u32,
                    "--max-body-kb" => s.max_body_kb = number(name, v, 4, 16384)? as u32,
                    "--max-tokens" => s.max_tokens = number(name, v, 16, 1 << 20)? as u32,
                    "--rate-per-minute" => s.rate_per_minute = number(name, v, 1, 100_000)? as u32,
                    "--max-inflight" => s.max_inflight = number(name, v, 1, 64)? as u32,
                    "--queue-max" => s.queue_max = number(name, v, 0, 1024)? as u32,
                    "--queue-wait-seconds" => s.queue_wait_seconds = number(name, v, 1, 3600)? as u32,
                    "--memory-floor-mb" => s.memory_floor_mb = number(name, v, 0, 1 << 20)? as u32,
                    "--memory-psi-max" => s.memory_psi_max = number(name, v, 0, 100)? as u32,
                    "--idle-stop-minutes" => s.idle_stop_minutes = number(name, v, 0, 10_080)? as u32,
                    "--llm-unit" => {
                        if !is_unit(v) {
                            return Err("--llm-unit is a unit llmcf-install wrote (llmcf-<name>.service)".into());
                        }
                        s.engines.push(Spec { kind: Kind::Llm, unit: v.to_string(), mib: 0, port: None });
                    }
                    "--engine" => s.engines.push(engines::parse_spec(v)?),
                    "--engines" => {
                        s.engines_mode = match v {
                            "together" => Mode::Together,
                            "in-turn" => Mode::InTurn,
                            _ => return Err("--engines is together or in-turn".into()),
                        }
                    }
                    "--engines-budget-mb" => s.engines_budget_mb = number(name, v, 0, 1 << 20)?,
                    "--engine-hold-seconds" => s.engine_hold_seconds = number(name, v, 0, 3600)? as u32,
                    "--stt-port" => s.stt_port = port_or_none(name, v)?,
                    "--vision-port" => s.vision_port = port_or_none(name, v)?,
                    "--vision-ctx-size" => s.vision_ctx_size = number(name, v, 256, 1 << 20)? as u32,
                    "--max-audio-mb" => s.max_audio_mb = number(name, v, 1, 200)? as u32,
                    "--max-images" => s.max_images = number(name, v, 0, 16)? as u32,
                    "--max-image-kb" => s.max_image_kb = number(name, v, 16, 16384)? as u32,
                    "--max-images-kb" => s.max_images_kb = number(name, v, 16, 65536)? as u32,
                    "--image-tokens" => s.image_tokens = number(name, v, 16, 16384)? as u32,
                    _ => return Err(format!("{name} is not an option of serve")),
                }
            }
            if s.port == s.upstream_port {
                return Err("--port and --upstream-port must differ".into());
            }
            if s.idle_stop_minutes > 0 && s.engines.is_empty() {
                return Err(
                    "--idle-stop-minutes needs an engine (--engine or --llm-unit: the gateway stops only its own \
                            engines)"
                        .into(),
                );
            }
            for (i, e) in s.engines.iter().enumerate() {
                if s.engines[..i].iter().any(|o| o.kind == e.kind) {
                    return Err(format!(
                        "the {} engine is given twice (--llm-unit is --engine llm=UNIT)",
                        e.kind.word()
                    ));
                }
                if s.engines[..i].iter().any(|o| o.unit == e.unit) {
                    return Err(format!("{} is given for two engines", e.unit));
                }
                match (e.kind, e.port) {
                    (Kind::Llm, Some(p)) if p != s.upstream_port => {
                        return Err("the llm engine's port is --upstream-port".into())
                    }
                    (Kind::Stt | Kind::Vision, Some(p)) if p == s.port || p == s.upstream_port => {
                        return Err(format!("the {} engine's port is another one's", e.kind.word()))
                    }
                    _ => {}
                }
                // the speech or vision engine's port: from --engine, or the same as --stt-port / --vision-port
                let given = match e.kind {
                    Kind::Stt => &mut s.stt_port,
                    Kind::Vision => &mut s.vision_port,
                    Kind::Llm => continue,
                };
                match (*given, e.port) {
                    (None, p) => *given = p,
                    (Some(a), Some(b)) if a != b => {
                        return Err(format!("the {} engine's port is given twice, differently", e.kind.word()))
                    }
                    _ => {}
                }
            }
            if s.max_image_kb > s.max_images_kb {
                return Err("--max-image-kb is above --max-images-kb".into());
            }
            let mut ports = vec![s.port, s.upstream_port];
            for p in [s.stt_port, s.vision_port].into_iter().flatten() {
                if ports.contains(&p) {
                    return Err("--stt-port and --vision-port are ports of their own".into());
                }
                ports.push(p);
            }
            Ok(Call::Serve(s))
        }
        "probe" => {
            let mut p = Probe { port: 8088, ..Probe::default() };
            for (name, v) in pairs(&args[1..])? {
                match name {
                    "--port" => p.port = port(name, v)?,
                    "--tls" => p.tls = true,
                    "--complete" => p.complete = true,
                    "--bench" => p.bench = number(name, v, 1, 20)? as u32,
                    _ => return Err(format!("{name} is not an option of probe")),
                }
            }
            Ok(Call::Probe(p))
        }
        other => Err(format!("{other} is not a command (serve, probe, --version)")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(words: &[&str]) -> Vec<String> {
        words.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn serve_takes_its_options_with_bounds() {
        let Call::Serve(s) = parse(&a(&["serve", "--port", "9000", "--bind", "lan", "--ctx-size", "8192"])).unwrap()
        else {
            panic!()
        };
        assert_eq!((s.port, s.bind, s.ctx_size, s.upstream_port), (9000, Bind::Lan, 8192, 8080));
        assert_eq!((s.queue_max, s.queue_wait_seconds), (32, 120));
        let Call::Serve(s) = parse(&a(&["serve", "--queue-max", "0", "--queue-wait-seconds", "5"])).unwrap() else {
            panic!()
        };
        assert_eq!((s.queue_max, s.queue_wait_seconds), (0, 5));
        for bad in [
            &["serve", "--port", "80"][..],
            &["serve", "--bind", "0.0.0.0"],
            &["serve", "--port", "9000", "--port", "9001"],
            &["serve", "--host", "x"],
            &["serve", "--llm-unit", "sshd.service"],
            &["serve", "--llm-unit", "llmcf-../x.service"],
            &["serve", "--port", "8080"],
            &["serve", "--idle-stop-minutes", "5"],
            &["serve", "--ctx-size", "-1"],
            &["serve", "--queue-max", "5000"],
            &["serve", "--queue-wait-seconds", "0"],
            &["serve", "--memory-psi-max", "101"],
            &["serve", "--engine", "llm=llmcf-llm.service:9", "--llm-unit", "llmcf-x.service"],
            &["serve", "--engine", "llm=llmcf-llm.service:9", "--engine", "stt=llmcf-llm.service:9"],
            &["serve", "--engine", "llm=llmcf-llm.service:9:9000"],
            &["serve", "--engine", "stt=llmcf-stt.service:9:8080"],
            &["serve", "--engines", "sometimes"],
            &["serve", "--engines-budget-mb", "-5"],
            &["serve", "--engine-hold-seconds", "3601"],
            &["serve", "--stt-port", "8080"],
            &["serve", "--stt-port", "8090", "--vision-port", "8090"],
            &["serve", "--stt-port", "80"],
            &["serve", "--stt-port", "8090", "--engine", "stt=llmcf-stt.service:9:8091"],
            &["serve", "--max-audio-mb", "0"],
            &["serve", "--max-audio-mb", "201"],
            &["serve", "--max-images", "17"],
            &["serve", "--max-image-kb", "8192", "--max-images-kb", "4096"],
            &["serve", "--image-tokens", "8"],
            &["bogus"],
        ] {
            assert!(parse(&a(bad)).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn engines_take_their_units_memory_and_mode() {
        let Call::Serve(s) = parse(&a(&[
            "serve",
            "--engine",
            "llm=llmcf-llm.service:900",
            "--engine",
            "stt=llmcf-stt.service:300:8090",
            "--engines",
            "in-turn",
            "--engines-budget-mb",
            "1000",
            "--idle-stop-minutes",
            "10",
        ]))
        .unwrap() else {
            panic!()
        };
        assert_eq!(s.engines.len(), 2);
        assert_eq!((s.engines[1].kind, s.engines[1].mib, s.engines[1].port), (Kind::Stt, 300, Some(8090)));
        assert_eq!((s.engines_mode, s.engines_budget_mb, s.engine_hold_seconds), (Mode::InTurn, 1000, 30));
        // --llm-unit stays: the llm engine without its memory
        let Call::Serve(s) = parse(&a(&["serve", "--llm-unit", "llmcf-llm.service"])).unwrap() else { panic!() };
        assert_eq!(s.engines, vec![Spec { kind: Kind::Llm, unit: "llmcf-llm.service".into(), mib: 0, port: None }]);
        assert_eq!(s.engines_mode, Mode::Together);
    }

    #[test]
    fn speech_and_vision_take_their_ports_and_caps() {
        let Call::Serve(s) = parse(&a(&["serve"])).unwrap() else { panic!() };
        assert_eq!((s.stt_port, s.vision_port, s.max_audio_mb, s.max_images, s.image_tokens), (None, None, 25, 4, 768));
        assert_eq!(s.chat_body_cap(), 256 * 1024, "without vision the JSON cap alone");
        assert_eq!(s.audio_body_cap(), 25 << 20);
        let Call::Serve(s) = parse(&a(&[
            "serve",
            "--stt-port",
            "0",
            "--vision-port",
            "8081",
            "--engine",
            "stt=llmcf-stt.service:300:8090",
            "--max-images-kb",
            "3072",
        ]))
        .unwrap() else {
            panic!()
        };
        assert_eq!((s.stt_port, s.vision_port), (Some(8090), Some(8081)), "the stt engine's port is the route's");
        assert_eq!(s.chat_body_cap(), 256 * 1024 + 4096 * 1024 + 4 * 64);
        let Call::Serve(s) =
            parse(&a(&["serve", "--stt-port", "8090", "--engine", "stt=llmcf-stt.service:300:8090"])).unwrap()
        else {
            panic!()
        };
        assert_eq!(s.stt_port, Some(8090));
    }

    #[test]
    fn units_are_only_llmcf_units() {
        assert!(is_unit("llmcf-llm.service"));
        assert!(is_unit("llmcf-llm-2.service"));
        assert!(!is_unit("llmcf-.service"));
        assert!(!is_unit("llmcf-LLM.service"));
        assert!(!is_unit("other.service"));
    }

    #[test]
    fn probe_takes_flags() {
        let Call::Probe(p) = parse(&a(&["probe", "--port", "9000", "--tls", "--bench", "3"])).unwrap() else {
            panic!()
        };
        assert!(p.tls && !p.complete && p.bench == 3 && p.port == 9000);
        assert!(parse(&a(&["probe", "--bench", "100"])).is_err());
    }
}
