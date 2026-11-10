//! The engine's command line: `serve`, `check-card` and `version`, each option with its own bound. Every other
//! option, a value out of its bounds or an option twice is refused (exit 2); nothing is read from the environment.

use std::collections::HashSet;
use std::path::PathBuf;

#[derive(Clone, Debug, PartialEq)]
pub struct Serve {
    /// the loopback port (the engine never listens elsewhere)
    pub port: u16,
    pub model: PathBuf,
    pub card: PathBuf,
    /// `board`, `pinned` or an absolute path (rknn.rs resolve)
    pub runtime: String,
    /// `auto` or a list of NPU cores such as 0.1
    pub npu_cores: String,
    pub score_pct: Option<u32>,
    pub nms_pct: Option<u32>,
    pub max_results: Option<u32>,
    pub classes: Option<Vec<u32>>,
    /// the folder of still images played as the engine's source (a plain name: a folder next to the model, the
    /// engine's own media folder, never a path), and its rate
    pub source_files: Option<String>,
    pub source_fps: u32,
    /// the simulated NPU's folder (a test build only)
    pub sim_npu: Option<PathBuf>,
}

#[derive(Debug, PartialEq)]
pub enum Call {
    Version,
    CheckCard(PathBuf),
    Serve(Box<Serve>),
}

fn number(v: &str, name: &str, lo: u32, hi: u32) -> Result<u32, String> {
    match v.parse::<u32>() {
        Ok(n) if (lo..=hi).contains(&n) && v.bytes().all(|b| b.is_ascii_digit()) => Ok(n),
        _ => Err(format!("{name} is a whole number from {lo} to {hi}")),
    }
}

fn path(v: &str, name: &str) -> Result<PathBuf, String> {
    if v.is_empty() || v.len() > 4096 || v.contains('\0') {
        return Err(format!("{name} is a path"));
    }
    Ok(PathBuf::from(v))
}

pub fn parse(argv: &[String]) -> Result<Call, String> {
    match argv.first().map(String::as_str) {
        Some("version" | "--version") if argv.len() == 1 => return Ok(Call::Version),
        Some("check-card") if argv.len() == 2 => return Ok(Call::CheckCard(path(&argv[1], "the card")?)),
        Some("serve") => {}
        _ => {
            return Err("usage: llmcf-detect serve --port P --model FILE --card FILE --rknn-runtime board|pinned|PATH \
                         [OPTION VALUE ...] | check-card FILE | version"
                .into())
        }
    }
    let mut seen = HashSet::new();
    let mut s = Serve {
        port: 0,
        model: PathBuf::new(),
        card: PathBuf::new(),
        runtime: String::new(),
        npu_cores: "auto".into(),
        score_pct: None,
        nms_pct: None,
        max_results: None,
        classes: None,
        source_files: None,
        source_fps: 2,
        sim_npu: None,
    };
    let mut it = argv[1..].iter();
    while let Some(opt) = it.next() {
        let v = it.next().ok_or_else(|| format!("{opt} needs a value"))?;
        if !seen.insert(opt.clone()) {
            return Err(format!("{opt} is given twice"));
        }
        match opt.as_str() {
            "--port" => s.port = number(v, "--port", 1024, 65535)? as u16,
            "--model" => s.model = path(v, "--model")?,
            "--card" => s.card = path(v, "--card")?,
            "--rknn-runtime" => s.runtime = v.clone(),
            "--npu-cores" => {
                if v != "auto"
                    && !(v.len() <= 16 && v.split('.').all(|c| c.len() == 1 && c.as_bytes()[0].is_ascii_digit()))
                {
                    return Err("--npu-cores is auto or a list of cores such as 0.1".into());
                }
                s.npu_cores = v.clone();
            }
            "--score-pct" => s.score_pct = Some(number(v, "--score-pct", 1, 100)?),
            "--nms-pct" => s.nms_pct = Some(number(v, "--nms-pct", 1, 100)?),
            "--max-results" => s.max_results = Some(number(v, "--max-results", 1, 1000)?),
            "--classes" => {
                if v != "all" {
                    let ids = v
                        .split('.')
                        .map(|p| number(p, "--classes", 0, 19_999))
                        .collect::<Result<Vec<_>, _>>()
                        .map_err(|_| "--classes is all or class ids joined by '.' (such as 0.2.15)".to_string())?;
                    s.classes = Some(ids);
                }
            }
            "--source-files" => {
                let plain = !v.is_empty()
                    && v.len() <= 64
                    && v != "."
                    && v != ".."
                    && v.bytes().all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'));
                if !plain {
                    return Err("--source-files names a folder next to the model (a plain name, never a path)".into());
                }
                s.source_files = Some(v.clone());
            }
            "--source-fps" => s.source_fps = number(v, "--source-fps", 1, 60)?,
            "--sim-npu" => {
                #[cfg(feature = "sim-npu")]
                {
                    s.sim_npu = Some(path(v, "--sim-npu")?);
                }
                #[cfg(not(feature = "sim-npu"))]
                {
                    return Err("this llmcf-detect has no simulated NPU (--sim-npu is only in a test build)".into());
                }
            }
            other => return Err(format!("unknown option {}", other.chars().take(40).collect::<String>())),
        }
    }
    if s.port == 0 || s.model.as_os_str().is_empty() || s.card.as_os_str().is_empty() {
        return Err("serve needs --port, --model and --card".into());
    }
    if s.runtime.is_empty() == s.sim_npu.is_none() {
        return Err("serve needs --rknn-runtime (or, in a test build, --sim-npu instead)".into());
    }
    Ok(Call::Serve(Box::new(s)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn a(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    const BASE: [&str; 9] =
        ["serve", "--port", "8093", "--model", "m.rknn", "--card", "m.card.json", "--rknn-runtime", "board"];

    #[test]
    fn a_serve_call_takes_its_options_once_and_within_bounds() {
        let Call::Serve(s) = parse(&a(&BASE)).unwrap() else { panic!() };
        assert_eq!((s.port, s.runtime.as_str(), s.npu_cores.as_str(), s.source_fps), (8093, "board", "auto", 2));
        let mut more = a(&BASE);
        more.extend(a(&["--classes", "0.2.15", "--score-pct", "30", "--npu-cores", "0.1"]));
        let Call::Serve(s) = parse(&more).unwrap() else { panic!() };
        assert_eq!((s.classes, s.score_pct), (Some(vec![0, 2, 15]), Some(30)));
        for bad in [
            &["--port", "80"][..],
            &["--score-pct", "0"],
            &["--classes", "1,2"],
            &["--npu-cores", "0,1"],
            &["--port", "8094"],
            &["--bind", "0.0.0.0"],
            &["--source-fps"],
            &["--source-files", "../media"],
            &["--source-files", "/media"],
        ] {
            let mut v = a(&BASE);
            v.extend(a(bad));
            assert!(parse(&v).is_err(), "{bad:?}");
        }
        assert!(parse(&a(&BASE[..7])).is_err(), "no runtime");
        assert_eq!(parse(&a(&["version"])).unwrap(), Call::Version);
        assert_eq!(parse(&a(&["check-card", "x.json"])).unwrap(), Call::CheckCard("x.json".into()));
    }

    #[test]
    fn the_simulated_npu_is_only_in_a_test_build() {
        let mut v = a(&BASE[..7]);
        v.extend(a(&["--sim-npu", "/tmp/sim"]));
        let got = parse(&v);
        if cfg!(feature = "sim-npu") {
            assert!(got.is_ok());
        } else {
            assert!(got.unwrap_err().contains("no simulated NPU"));
        }
    }
}
