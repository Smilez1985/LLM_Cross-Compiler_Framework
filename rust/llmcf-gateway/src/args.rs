//! The gateway's command line: `serve` and `probe`, each option with its own bound. Every other option, a value
//! out of its bounds or an option twice is refused (exit 2); nothing is read from the environment but HOME.

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
    /// The llm component's systemd unit the gateway starts and stops (None: it controls nothing).
    pub llm_unit: Option<String>,
    pub idle_stop_minutes: u32,
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
            llm_unit: None,
            idle_stop_minutes: 0,
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
        if !seen.insert(name) {
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
                    "--idle-stop-minutes" => s.idle_stop_minutes = number(name, v, 0, 10_080)? as u32,
                    "--llm-unit" => {
                        if !is_unit(v) {
                            return Err("--llm-unit is a unit llmcf-install wrote (llmcf-<name>.service)".into());
                        }
                        s.llm_unit = Some(v.to_string());
                    }
                    _ => return Err(format!("{name} is not an option of serve")),
                }
            }
            if s.port == s.upstream_port {
                return Err("--port and --upstream-port must differ".into());
            }
            if s.idle_stop_minutes > 0 && s.llm_unit.is_none() {
                return Err("--idle-stop-minutes needs --llm-unit (the gateway stops only its own model server)".into());
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
            &["bogus"],
        ] {
            assert!(parse(&a(bad)).is_err(), "{bad:?}");
        }
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
