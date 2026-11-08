//! The board's engines (the llm's llama-server, whisper for speech, vision) and when each one runs: the gateway is
//! the board orchestrator of the models.
//!
//! Each engine is its own systemd unit with the memory the board's measurement gave it (`--engine
//! NAME=UNIT:MIB[:PORT]`). A route names the engine it needs (`for_route`, one line per route). `together`: every
//! engine is started on demand and stays (the idle stop as before). `in-turn` (the person's choice when the measured
//! memory cannot hold them all): before a request goes to an engine that is not running, the running engines that do
//! not fit next to it (with `--engines-budget-mb` 0: all others) finish what they have in flight (never stopped with
//! requests in flight; an engine stays loaded at least `--engine-hold-seconds` after its start so two harnesses cannot
//! make it thrash), are stopped, the needed engine is started and its health awaited (bounded; then 503 with
//! Retry-After), and only then the request goes in.
//!
//! How this meets the fair queue (queue.rs): a request takes its place in flight first, in the queue's fair order,
//! and only then asks for its engine. A request whose engine runs goes in at once. A switch is made under one turn
//! (a FIFO lock): the switches are made in the order their requests got their places, so the queue's fairness holds
//! for the engines too, and an engine that is to be stopped takes no new request (those wait for their turn behind
//! the switch and switch back after it, never starving the one that waits).
//!
//! systemctl and the health checks are behind `Hooks`, so the decisions are tested with a fake board.

use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};
use tokio::sync::Notify;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Kind {
    Llm,
    Stt,
    Vision,
}

impl Kind {
    pub fn word(self) -> &'static str {
        match self {
            Kind::Llm => "llm",
            Kind::Stt => "stt",
            Kind::Vision => "vision",
        }
    }

    pub fn from_word(s: &str) -> Option<Kind> {
        match s {
            "llm" => Some(Kind::Llm),
            "stt" => Some(Kind::Stt),
            "vision" => Some(Kind::Vision),
            _ => None,
        }
    }
}

/// One route of the gateway to an engine: the client's method and path, the engine it needs and the engine's own path
/// the request goes to (whisper.cpp's whisper-server takes its uploads at /inference).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Route {
    pub method: &'static str,
    pub path: &'static str,
    pub engine: Kind,
    pub upstream: &'static str,
}

/// The routing table: every route to an engine the gateway serves (/health and /metrics are its own). A chat request
/// with image parts goes to the vision engine instead (`for_request`). A new route is one line here.
pub const ROUTES: [Route; 4] = [
    Route { method: "GET", path: "/v1/models", engine: Kind::Llm, upstream: "/v1/models" },
    Route { method: "POST", path: "/v1/chat/completions", engine: Kind::Llm, upstream: "/v1/chat/completions" },
    Route { method: "POST", path: "/v1/completions", engine: Kind::Llm, upstream: "/v1/completions" },
    Route { method: "POST", path: "/v1/audio/transcriptions", engine: Kind::Stt, upstream: "/inference" },
];

/// The route of a method and path (its place in ROUTES).
pub fn route_index(method: &str, path: &str) -> Option<usize> {
    ROUTES.iter().position(|r| r.method == method && r.path == path)
}

/// The engine a route needs (None: no engine; such a route is the gateway's own).
pub fn for_route(path: &str) -> Option<Kind> {
    ROUTES.iter().find(|r| r.path == path).map(|r| r.engine)
}

/// The engine one request goes to: a chat request with image parts to the vision engine (a llama-server of its own
/// with the projector), every other one to its route's engine.
pub fn for_request(path: &str, images: bool) -> Option<Kind> {
    match for_route(path) {
        Some(Kind::Llm) if images && path == "/v1/chat/completions" => Some(Kind::Vision),
        other => other,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Together,
    InTurn,
}

impl Mode {
    pub fn word(self) -> &'static str {
        match self {
            Mode::Together => "together",
            Mode::InTurn => "in-turn",
        }
    }
}

/// One engine as `--engine NAME=UNIT:MIB[:PORT]` gives it: its unit, its measured memory need, the loopback port of its
/// health check (none: the unit's own state; the llm's is the gateway's --upstream-port).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Spec {
    pub kind: Kind,
    pub unit: String,
    pub mib: u64,
    pub port: Option<u16>,
}

/// `NAME=UNIT:MIB[:PORT]`, every part with its bound.
pub fn parse_spec(v: &str) -> Result<Spec, String> {
    let bad = || "--engine is NAME=UNIT:MIB[:PORT] (NAME llm, stt or vision; UNIT llmcf-<name>.service)".to_string();
    let (name, rest) = v.split_once('=').ok_or_else(bad)?;
    let kind = Kind::from_word(name).ok_or_else(bad)?;
    let mut parts = rest.split(':');
    let unit = parts.next().filter(|u| crate::args::is_unit(u)).ok_or_else(bad)?;
    let digits = |s: &str, max: u64| -> Option<u64> {
        (!s.is_empty() && s.len() <= 7 && s.bytes().all(|c| c.is_ascii_digit()))
            .then(|| s.parse::<u64>().ok())
            .flatten()
            .filter(|n| *n <= max)
    };
    let mib = parts.next().and_then(|m| digits(m, 1 << 20)).ok_or_else(bad)?;
    let port = match parts.next() {
        None => None,
        Some(p) => Some(digits(p, 65535).filter(|p| *p >= 1024).ok_or_else(bad)? as u16),
    };
    if parts.next().is_some() {
        return Err(bad());
    }
    Ok(Spec { kind, unit: unit.to_string(), mib, port })
}

pub type Fut<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// What the scheduler does on the board: systemctl (start, stop, is-active) for exactly an engine's unit, and the
/// engine's health check.
pub trait Hooks: Send + Sync {
    fn control<'a>(&'a self, verb: &'static str, unit: &'a str) -> Fut<'a, Result<bool, String>>;
    fn up<'a>(&'a self, spec: &'a Spec) -> Fut<'a, bool>;
}

/// The bounded times of the scheduler.
#[derive(Clone, Copy, Debug)]
pub struct Timing {
    /// an engine stays loaded at least this long after its start before another one may take its place
    pub hold: Duration,
    /// the longest wait for a started engine's health
    pub start_wait: Duration,
    /// the longest wait for the engines to be stopped to finish their requests in flight
    pub switch_wait: Duration,
    /// how often a health check is repeated while an engine starts
    pub poll: Duration,
}

impl Default for Timing {
    fn default() -> Timing {
        Timing {
            hold: Duration::from_secs(30),
            start_wait: Duration::from_secs(120),
            switch_wait: Duration::from_secs(120),
            poll: Duration::from_secs(1),
        }
    }
}

#[derive(Default)]
struct Slot {
    running: bool,
    /// to be stopped by a switch that waits for its turn: no new request goes in
    stopping: bool,
    starting: bool,
    inflight: u32,
    since: Option<Instant>,
    used: Option<Instant>,
    starts: u64,
    stops: u64,
}

#[derive(Default)]
pub struct Metrics {
    /// engines that had to be stopped so another one could run (in turn)
    pub switches: AtomicU64,
    /// the time requests waited for a switch, in microseconds
    pub switch_wait_micros: AtomicU64,
    pub start_failures: AtomicU64,
    /// switches given up because an engine to be stopped stayed busy
    pub busy: AtomicU64,
}

pub struct Scheduler {
    pub mode: Mode,
    /// MiB the engines may use together in turn (0: one engine at a time)
    pub budget_mib: u64,
    pub specs: Vec<Spec>,
    timing: Timing,
    slots: Mutex<Vec<Slot>>,
    changed: Notify,
    turn: tokio::sync::Mutex<()>,
    pub metrics: Metrics,
}

/// One request at an engine: the engine is not stopped while one is held (held to the answer's last byte).
pub struct Ticket {
    sched: Arc<Scheduler>,
    idx: usize,
}

impl Drop for Ticket {
    fn drop(&mut self) {
        {
            let mut slots = self.sched.lock();
            let s = &mut slots[self.idx];
            s.inflight = s.inflight.saturating_sub(1);
            s.used = Some(Instant::now());
        }
        self.sched.changed.notify_waiters();
    }
}

/// An engine's state for /health and /metrics.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EngineState {
    pub kind: Kind,
    pub unit: String,
    pub mib: u64,
    pub state: &'static str,
    pub inflight: u32,
    pub starts: u64,
    pub stops: u64,
}

impl Scheduler {
    pub fn new(mode: Mode, budget_mib: u64, specs: Vec<Spec>, timing: Timing) -> Arc<Scheduler> {
        let slots = specs.iter().map(|_| Slot::default()).collect();
        Arc::new(Scheduler {
            mode,
            budget_mib,
            specs,
            timing,
            slots: Mutex::new(slots),
            changed: Notify::new(),
            turn: tokio::sync::Mutex::new(()),
            metrics: Metrics::default(),
        })
    }

    fn lock(&self) -> MutexGuard<'_, Vec<Slot>> {
        self.slots.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn index(&self, kind: Kind) -> Option<usize> {
        self.specs.iter().position(|s| s.kind == kind)
    }

    pub fn running(&self, kind: Kind) -> bool {
        self.index(kind).map(|i| self.lock()[i].running).unwrap_or(false)
    }

    pub fn states(&self) -> Vec<EngineState> {
        let slots = self.lock();
        self.specs
            .iter()
            .zip(slots.iter())
            .map(|(spec, s)| EngineState {
                kind: spec.kind,
                unit: spec.unit.clone(),
                mib: spec.mib,
                state: if s.starting {
                    "starting"
                } else if s.stopping && s.running {
                    "stopping"
                } else if s.running {
                    "running"
                } else {
                    "stopped"
                },
                inflight: s.inflight,
                starts: s.starts,
                stops: s.stops,
            })
            .collect()
    }

    /// The engines that must be stopped before `want` may run (none in `together`): in turn, all others with a budget
    /// of 0, else the least recently used ones until `want` fits next to the rest.
    fn victims(&self, slots: &[Slot], want: usize) -> Vec<usize> {
        if self.mode == Mode::Together {
            return Vec::new();
        }
        let mut others: Vec<usize> = (0..slots.len()).filter(|&i| i != want && slots[i].running).collect();
        if self.budget_mib == 0 {
            return others;
        }
        // the least recently used first
        others.sort_by_key(|&i| slots[i].used.or(slots[i].since));
        let mut used: u64 = others.iter().map(|&i| self.specs[i].mib).sum();
        let need = self.specs[want].mib;
        let mut out = Vec::new();
        for i in others {
            if used + need <= self.budget_mib {
                break;
            }
            used -= self.specs[i].mib;
            out.push(i);
        }
        out
    }

    fn try_enter(self: &Arc<Self>, idx: usize) -> Option<Ticket> {
        let mut slots = self.lock();
        let s = &mut slots[idx];
        if s.running && !s.stopping {
            s.inflight += 1;
            s.used = Some(Instant::now());
            return Some(Ticket { sched: self.clone(), idx });
        }
        None
    }

    /// A ticket for a request to engine `kind`, the engine running (started, and in turn the others stopped, first);
    /// Ok with the time the request waited for it, Err with the reason (the caller answers 503 with Retry-After).
    pub async fn enter(self: &Arc<Self>, hooks: &dyn Hooks, kind: Kind) -> Result<(Ticket, Duration), String> {
        let idx = self.index(kind).ok_or_else(|| format!("the board runs no {} engine", kind.word()))?;
        if let Some(t) = self.try_enter(idx) {
            return Ok((t, Duration::ZERO));
        }
        let started = Instant::now();
        let _turn = self.turn.lock().await;
        if let Some(t) = self.try_enter(idx) {
            return Ok((t, started.elapsed()));
        }
        let victims = {
            let mut slots = self.lock();
            let v = self.victims(&slots, idx);
            for &i in &v {
                slots[i].stopping = true;
            }
            v
        };
        let done = self.switch(hooks, idx, &victims, started).await;
        {
            let mut slots = self.lock();
            for &i in &victims {
                slots[i].stopping = false;
            }
            slots[idx].starting = false;
        }
        self.changed.notify_waiters();
        if let Err(why) = done {
            eprintln!("llmcf-gateway: the {} engine is not ready: {why}", kind.word());
            return Err(why);
        }
        let waited = started.elapsed();
        if !victims.is_empty() {
            self.metrics.switches.fetch_add(victims.len() as u64, Relaxed);
            self.metrics.switch_wait_micros.fetch_add(waited.as_micros() as u64, Relaxed);
            let names: Vec<&str> = victims.iter().map(|&i| self.specs[i].kind.word()).collect();
            eprintln!(
                "llmcf-gateway: engines in turn: {} stopped for {} after {}ms",
                names.join(","),
                kind.word(),
                waited.as_millis()
            );
        }
        let mut slots = self.lock();
        slots[idx].inflight += 1;
        slots[idx].used = Some(Instant::now());
        drop(slots);
        Ok((Ticket { sched: self.clone(), idx }, waited))
    }

    /// Under the turn: waits until the victims have nothing in flight and were loaded their minimum time, stops them,
    /// starts `want` and waits for its health.
    async fn switch(&self, hooks: &dyn Hooks, want: usize, victims: &[usize], started: Instant) -> Result<(), String> {
        let deadline = started + self.timing.switch_wait;
        loop {
            let notified = self.changed.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            let more = {
                let slots = self.lock();
                let now = Instant::now();
                let mut more: Option<Duration> = None;
                for &i in victims {
                    let s = &slots[i];
                    if s.inflight > 0 {
                        more = Some(self.timing.poll);
                    } else if let Some(since) = s.since {
                        let until = since + self.timing.hold;
                        if until > now {
                            more = Some(more.map_or(until - now, |m| m.min(until - now)));
                        }
                    }
                }
                more
            };
            let Some(more) = more else { break };
            let now = Instant::now();
            if now >= deadline {
                self.metrics.busy.fetch_add(1, Relaxed);
                return Err("the engines in turn stayed busy for the whole wait".into());
            }
            tokio::select! {
                _ = &mut notified => {}
                _ = tokio::time::sleep(more.min(deadline - now)) => {}
            }
        }
        for &i in victims {
            let unit = &self.specs[i].unit;
            match hooks.control("stop", unit).await {
                Ok(true) => {
                    let mut slots = self.lock();
                    slots[i].running = false;
                    slots[i].since = None;
                    slots[i].stops += 1;
                }
                Ok(false) => return Err(format!("{unit} could not be stopped")),
                Err(e) => return Err(e),
            }
        }
        self.start(hooks, want).await
    }

    async fn start(&self, hooks: &dyn Hooks, idx: usize) -> Result<(), String> {
        let spec = &self.specs[idx];
        self.lock()[idx].starting = true;
        if !hooks.up(spec).await {
            match hooks.control("start", &spec.unit).await {
                Ok(true) => self.lock()[idx].starts += 1,
                Ok(false) => {
                    self.metrics.start_failures.fetch_add(1, Relaxed);
                    return Err(format!("{} could not be started", spec.unit));
                }
                Err(e) => {
                    self.metrics.start_failures.fetch_add(1, Relaxed);
                    return Err(e);
                }
            }
            let until = Instant::now() + self.timing.start_wait;
            loop {
                if hooks.up(spec).await {
                    break;
                }
                if Instant::now() >= until {
                    self.metrics.start_failures.fetch_add(1, Relaxed);
                    // an engine that does not come up gives its memory back (the next request starts it again)
                    let _ = hooks.control("stop", &spec.unit).await;
                    return Err(format!("{} was started but does not answer yet", spec.unit));
                }
                tokio::time::sleep(self.timing.poll).await;
            }
        }
        let mut slots = self.lock();
        slots[idx].running = true;
        slots[idx].since = Some(Instant::now());
        Ok(())
    }

    /// The engine `kind` running as `enter` makes it, without a request (the gateway's start).
    pub async fn prepare(self: &Arc<Self>, hooks: &dyn Hooks, kind: Kind) -> Result<(), String> {
        self.enter(hooks, kind).await.map(|_| ())
    }

    /// An engine whose health check failed although it was taken as running: the next request starts it again.
    pub fn mark_down(&self, kind: Kind) {
        if let Some(i) = self.index(kind) {
            let mut slots = self.lock();
            if slots[i].inflight <= 1 {
                slots[i].running = false;
                slots[i].since = None;
            }
        }
    }

    /// At the gateway's start: which engines run (systemctl is-active); in turn, those that do not fit together are
    /// stopped (the first ones of the list kept: the llm comes first).
    pub async fn reconcile(&self, hooks: &dyn Hooks) {
        let _turn = self.turn.lock().await;
        let mut kept: Vec<usize> = Vec::new();
        for (i, spec) in self.specs.iter().enumerate() {
            if !matches!(hooks.control("is-active", &spec.unit).await, Ok(true)) {
                continue;
            }
            let fits = match self.mode {
                Mode::Together => true,
                Mode::InTurn if self.budget_mib == 0 => kept.is_empty(),
                Mode::InTurn => {
                    kept.is_empty()
                        || kept.iter().map(|&k| self.specs[k].mib).sum::<u64>() + spec.mib <= self.budget_mib
                }
            };
            if fits {
                kept.push(i);
                let mut slots = self.lock();
                slots[i].running = true;
                slots[i].since = Some(Instant::now());
            } else if let Ok(true) = hooks.control("stop", &spec.unit).await {
                self.lock()[i].stops += 1;
                eprintln!(
                    "llmcf-gateway: engines in turn: {} stopped at the start (it does not fit)",
                    spec.kind.word()
                );
            }
        }
    }

    /// Stops every engine without a request for `idle` (never one with requests in flight or in a switch).
    pub async fn stop_idle(&self, hooks: &dyn Hooks, idle: Duration) -> Vec<Kind> {
        let Ok(_turn) = self.turn.try_lock() else { return Vec::new() };
        let due: Vec<usize> = {
            let slots = self.lock();
            (0..slots.len())
                .filter(|&i| {
                    let s = &slots[i];
                    let last = s.used.or(s.since);
                    s.running && !s.stopping && s.inflight == 0 && last.map(|t| t.elapsed() >= idle).unwrap_or(true)
                })
                .collect()
        };
        let mut out = Vec::new();
        for i in due {
            if let Ok(true) = hooks.control("stop", &self.specs[i].unit).await {
                let mut slots = self.lock();
                if slots[i].inflight == 0 {
                    slots[i].running = false;
                    slots[i].since = None;
                    slots[i].stops += 1;
                    out.push(self.specs[i].kind);
                }
            }
        }
        out
    }
}

/// Whether something answers HTTP on the board's loopback `port` (any status below 500 of GET /health: whisper's
/// server answers with its own page or 404, which still says it is up).
pub async fn http_up(port: u16) -> bool {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let check = async {
        let mut s = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.ok()?;
        let req = format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
        s.write_all(req.as_bytes()).await.ok()?;
        let mut head = [0u8; 16];
        let mut got = 0;
        while got < 12 {
            let n = s.read(&mut head[got..]).await.ok()?;
            if n == 0 {
                break;
            }
            got += n;
        }
        let text = std::str::from_utf8(&head[..got]).ok()?;
        let code: u16 = text.strip_prefix("HTTP/1.")?.get(2..5)?.parse().ok()?;
        Some(code < 500)
    };
    matches!(tokio::time::timeout(Duration::from_secs(3), check).await, Ok(Some(true)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::atomic::AtomicBool;

    /// A fake board: systemctl flips the unit's state at once (or never, for a unit that fails), the health says what
    /// the unit's state says after `delay_checks` checks.
    #[derive(Default)]
    struct Fake {
        active: Mutex<HashMap<String, bool>>,
        calls: Mutex<Vec<String>>,
        broken: Mutex<Vec<String>>,
        never_up: AtomicBool,
    }

    impl Fake {
        fn with(active: &[&str]) -> Arc<Fake> {
            let f = Fake::default();
            for u in active {
                f.active.lock().unwrap().insert(u.to_string(), true);
            }
            Arc::new(f)
        }
        fn calls(&self) -> Vec<String> {
            self.calls.lock().unwrap().clone()
        }
        fn is(&self, unit: &str) -> bool {
            *self.active.lock().unwrap().get(unit).unwrap_or(&false)
        }
    }

    impl Hooks for Fake {
        fn control<'a>(&'a self, verb: &'static str, unit: &'a str) -> Fut<'a, Result<bool, String>> {
            Box::pin(async move {
                if verb != "is-active" {
                    self.calls.lock().unwrap().push(format!("{verb} {unit}"));
                }
                if self.broken.lock().unwrap().iter().any(|u| u == unit) {
                    return Ok(false);
                }
                let mut a = self.active.lock().unwrap();
                Ok(match verb {
                    "start" => {
                        a.insert(unit.to_string(), true);
                        true
                    }
                    "stop" => {
                        a.insert(unit.to_string(), false);
                        true
                    }
                    _ => *a.get(unit).unwrap_or(&false),
                })
            })
        }
        fn up<'a>(&'a self, spec: &'a Spec) -> Fut<'a, bool> {
            Box::pin(async move { !self.never_up.load(Relaxed) && self.is(&spec.unit) })
        }
    }

    fn spec(kind: Kind, mib: u64) -> Spec {
        Spec { kind, unit: format!("llmcf-{}.service", kind.word()), mib, port: None }
    }

    fn quick() -> Timing {
        Timing {
            hold: Duration::ZERO,
            start_wait: Duration::from_millis(200),
            switch_wait: Duration::from_millis(500),
            poll: Duration::from_millis(5),
        }
    }

    #[test]
    fn routes_name_their_engine_and_specs_are_checked() {
        assert_eq!(for_route("/v1/chat/completions"), Some(Kind::Llm));
        assert_eq!(for_route("/v1/models"), Some(Kind::Llm));
        assert_eq!(for_route("/v1/audio/transcriptions"), Some(Kind::Stt));
        assert_eq!(for_route("/health"), None);
        assert_eq!(for_route("/v1/embeddings"), None, "not served");
        // images: the chat route goes to vision, nothing else does
        assert_eq!(for_request("/v1/chat/completions", true), Some(Kind::Vision));
        assert_eq!(for_request("/v1/chat/completions", false), Some(Kind::Llm));
        assert_eq!(for_request("/v1/completions", true), Some(Kind::Llm));
        assert_eq!(for_request("/v1/audio/transcriptions", false), Some(Kind::Stt));
        assert_eq!(route_index("POST", "/v1/audio/transcriptions"), Some(3));
        assert_eq!(ROUTES[3].upstream, "/inference");
        assert_eq!(route_index("GET", "/v1/audio/transcriptions"), None);
        assert_eq!(route_index("POST", "/metrics"), None);
        assert_eq!(
            parse_spec("stt=llmcf-stt.service:300:8090").unwrap(),
            Spec { kind: Kind::Stt, unit: "llmcf-stt.service".into(), mib: 300, port: Some(8090) }
        );
        assert_eq!(parse_spec("llm=llmcf-llm.service:900").unwrap().port, None);
        for bad in [
            "tts=llmcf-tts.service:10",
            "llm=sshd.service:10",
            "llm=llmcf-llm.service",
            "llm=llmcf-llm.service:-1",
            "llm=llmcf-llm.service:10:80",
            "llm=llmcf-llm.service:10:8080:1",
            "llm=llmcf-llm.service:99999999",
            "llmcf-llm.service:10",
        ] {
            assert!(parse_spec(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn who_is_stopped_for_whom() {
        let s = Scheduler::new(
            Mode::InTurn,
            1000,
            vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200), spec(Kind::Vision, 400)],
            quick(),
        );
        let now = Instant::now();
        let slot =
            |running: bool, used: u64| Slot { running, used: Some(now + Duration::from_secs(used)), ..Slot::default() };
        // llm and stt run (900 MiB); vision (400) needs room: the least recently used goes first (stt at 1 before
        // llm at 2), then llm as well since 700 + 400 > 1000
        let slots = vec![slot(true, 2), slot(true, 1), slot(false, 0)];
        assert_eq!(s.victims(&slots, 2), vec![1, 0]);
        // stt (200) fits next to llm (700): nothing is stopped
        let slots = vec![slot(true, 2), slot(false, 0), slot(false, 0)];
        assert!(s.victims(&slots, 1).is_empty());
        // with a budget of 0 every other running engine is stopped
        let one = Scheduler::new(Mode::InTurn, 0, vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200)], quick());
        assert_eq!(one.victims(&[slot(true, 1), slot(false, 0)], 1), vec![0]);
        // together: never
        let all = Scheduler::new(Mode::Together, 0, vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200)], quick());
        assert!(all.victims(&[slot(true, 1), slot(false, 0)], 1).is_empty());
    }

    #[tokio::test(flavor = "current_thread")]
    async fn together_starts_on_demand_and_stops_nothing() {
        let fake = Fake::with(&["llmcf-llm.service"]);
        let s = Scheduler::new(Mode::Together, 0, vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200)], quick());
        s.reconcile(&*fake).await;
        assert!(s.running(Kind::Llm) && !s.running(Kind::Stt));
        let (a, _) = s.enter(&*fake, Kind::Llm).await.unwrap();
        let (b, waited) = s.enter(&*fake, Kind::Stt).await.unwrap();
        assert!(waited < Duration::from_millis(200));
        assert_eq!(fake.calls(), vec!["start llmcf-stt.service"]);
        assert!(s.running(Kind::Llm) && s.running(Kind::Stt));
        drop((a, b));
        assert_eq!(s.metrics.switches.load(Relaxed), 0);
        assert!(s.enter(&*fake, Kind::Vision).await.is_err(), "no such engine");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn in_turn_waits_for_the_running_engine_to_finish_then_switches() {
        let fake = Fake::with(&["llmcf-llm.service"]);
        let s = Scheduler::new(Mode::InTurn, 0, vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200)], quick());
        s.reconcile(&*fake).await;
        let (busy, _) = s.enter(&*fake, Kind::Llm).await.unwrap();
        let s2 = s.clone();
        let f2 = fake.clone();
        let stt = tokio::spawn(async move { s2.enter(&*f2, Kind::Stt).await });
        tokio::time::sleep(Duration::from_millis(30)).await;
        // the llm has a request in flight: it is never stopped under it, and takes no new request meanwhile
        assert!(fake.is("llmcf-llm.service") && fake.calls().is_empty());
        assert_eq!(s.states()[0].state, "stopping");
        assert!(s.try_enter(0).is_none(), "an engine that is to be stopped takes no new request");
        drop(busy);
        let (ticket, waited) = stt.await.unwrap().unwrap();
        assert!(waited >= Duration::from_millis(25), "{waited:?}");
        assert_eq!(fake.calls(), vec!["stop llmcf-llm.service", "start llmcf-stt.service"]);
        assert!(!s.running(Kind::Llm) && s.running(Kind::Stt));
        assert_eq!(s.metrics.switches.load(Relaxed), 1);
        assert!(s.metrics.switch_wait_micros.load(Relaxed) > 0);
        drop(ticket);
        // and back again for the next llm request
        let (_t, _) = s.enter(&*fake, Kind::Llm).await.unwrap();
        assert_eq!(fake.calls()[2..], ["stop llmcf-stt.service".to_string(), "start llmcf-llm.service".to_string()]);
        assert_eq!(s.metrics.switches.load(Relaxed), 2);
    }

    #[tokio::test(flavor = "current_thread")]
    async fn a_busy_engine_or_a_dead_start_ends_in_a_refusal() {
        let fake = Fake::with(&["llmcf-llm.service"]);
        let s = Scheduler::new(Mode::InTurn, 0, vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200)], quick());
        s.reconcile(&*fake).await;
        let (held, _) = s.enter(&*fake, Kind::Llm).await.unwrap();
        let why = s.enter(&*fake, Kind::Stt).await.err().unwrap();
        assert!(why.contains("stayed busy"), "{why}");
        assert!(fake.is("llmcf-llm.service"), "never stopped with a request in flight");
        assert_eq!(s.states()[0].state, "running", "a refused switch leaves the engine as it was");
        drop(held);
        fake.never_up.store(true, Relaxed);
        let why = s.enter(&*fake, Kind::Stt).await.err().unwrap();
        assert!(why.contains("does not answer yet"), "{why}");
        assert_eq!(s.metrics.start_failures.load(Relaxed), 1);
        assert!(!fake.is("llmcf-stt.service"), "an engine that does not come up is stopped again");
        fake.never_up.store(false, Relaxed);
        fake.broken.lock().unwrap().push("llmcf-stt.service".into());
        assert!(s.enter(&*fake, Kind::Stt).await.is_err());
    }

    #[tokio::test(flavor = "current_thread")]
    async fn an_engine_is_held_its_minimum_time_and_idle_ones_are_stopped() {
        let fake = Fake::with(&[]);
        let t = Timing { hold: Duration::from_millis(80), ..quick() };
        let s = Scheduler::new(Mode::InTurn, 0, vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200)], t);
        drop(s.enter(&*fake, Kind::Llm).await.unwrap());
        let started = Instant::now();
        drop(s.enter(&*fake, Kind::Stt).await.unwrap());
        assert!(started.elapsed() >= Duration::from_millis(60), "the llm stayed its minimum time");
        // reconcile at the start: two running engines do not fit in turn, the first one is kept
        let fake2 = Fake::with(&["llmcf-llm.service", "llmcf-stt.service"]);
        let s2 = Scheduler::new(Mode::InTurn, 0, vec![spec(Kind::Llm, 700), spec(Kind::Stt, 200)], quick());
        s2.reconcile(&*fake2).await;
        assert!(s2.running(Kind::Llm) && !s2.running(Kind::Stt) && !fake2.is("llmcf-stt.service"));
        // idle: stopped, never one with a request in flight
        let held = s2.enter(&*fake2, Kind::Llm).await.unwrap();
        assert!(s2.stop_idle(&*fake2, Duration::ZERO).await.is_empty());
        drop(held);
        assert_eq!(s2.stop_idle(&*fake2, Duration::ZERO).await, vec![Kind::Llm]);
        assert!(!fake2.is("llmcf-llm.service"));
    }

    /// Two fake upstreams on their own loopback ports stand for the llm and the speech engine: "starting" a unit
    /// opens its port, "stopping" it closes it, and the health is the real HTTP check (http_up). The scheduler alone
    /// here; server.rs tests the same switch through the gateway's real HTTP routes.
    struct Upstreams {
        ports: HashMap<String, u16>,
        servers: Mutex<HashMap<String, tokio::task::JoinHandle<()>>>,
        calls: Mutex<Vec<String>>,
    }

    async fn serve_health(listener: tokio::net::TcpListener) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        loop {
            let Ok((mut s, _)) = listener.accept().await else { return };
            tokio::spawn(async move {
                let mut buf = [0u8; 512];
                let _ = s.read(&mut buf).await;
                let _ = s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok").await;
            });
        }
    }

    impl Hooks for Upstreams {
        fn control<'a>(&'a self, verb: &'static str, unit: &'a str) -> Fut<'a, Result<bool, String>> {
            Box::pin(async move {
                let port = *self.ports.get(unit).ok_or("unknown unit")?;
                let mut servers = self.servers.lock().unwrap();
                match verb {
                    "start" => {
                        self.calls.lock().unwrap().push(format!("start {unit}"));
                        let std = std::net::TcpListener::bind(("127.0.0.1", port)).map_err(|e| e.to_string())?;
                        std.set_nonblocking(true).map_err(|e| e.to_string())?;
                        let listener = tokio::net::TcpListener::from_std(std).map_err(|e| e.to_string())?;
                        servers.insert(unit.to_string(), tokio::spawn(serve_health(listener)));
                        Ok(true)
                    }
                    "stop" => {
                        self.calls.lock().unwrap().push(format!("stop {unit}"));
                        if let Some(h) = servers.remove(unit) {
                            h.abort();
                        }
                        Ok(true)
                    }
                    _ => Ok(servers.contains_key(unit)),
                }
            })
        }
        fn up<'a>(&'a self, spec: &'a Spec) -> Fut<'a, bool> {
            Box::pin(async move { http_up(self.ports[&spec.unit]).await })
        }
    }

    fn free_port() -> u16 {
        std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
    }

    #[tokio::test(flavor = "current_thread")]
    async fn two_upstreams_take_turns_on_their_own_ports() {
        let (llm_port, stt_port) = (free_port(), free_port());
        let board = Arc::new(Upstreams {
            ports: HashMap::from([
                ("llmcf-llm.service".to_string(), llm_port),
                ("llmcf-stt.service".to_string(), stt_port),
            ]),
            servers: Mutex::new(HashMap::new()),
            calls: Mutex::new(Vec::new()),
        });
        let specs = vec![
            Spec { kind: Kind::Llm, unit: "llmcf-llm.service".into(), mib: 700, port: Some(llm_port) },
            Spec { kind: Kind::Stt, unit: "llmcf-stt.service".into(), mib: 300, port: Some(stt_port) },
        ];
        // 900 MiB for both: they do not fit together, so they take turns
        let s = Scheduler::new(Mode::InTurn, 900, specs, quick());
        let (llm, _) = s.enter(&*board, Kind::Llm).await.unwrap();
        assert!(http_up(llm_port).await && !http_up(stt_port).await);
        let (s2, b2) = (s.clone(), board.clone());
        let speech = tokio::spawn(async move { s2.enter(&*b2, Kind::Stt).await });
        tokio::time::sleep(Duration::from_millis(40)).await;
        assert!(http_up(llm_port).await, "the llm keeps running while its request is in flight");
        drop(llm);
        let (stt, waited) = speech.await.unwrap().unwrap();
        assert!(waited >= Duration::from_millis(30));
        assert!(http_up(stt_port).await && !http_up(llm_port).await, "only the speech engine runs now");
        drop(stt);
        let (_llm, _) = s.enter(&*board, Kind::Llm).await.unwrap();
        assert!(http_up(llm_port).await && !http_up(stt_port).await);
        assert_eq!(
            *board.calls.lock().unwrap(),
            [
                "start llmcf-llm.service",
                "stop llmcf-llm.service",
                "start llmcf-stt.service",
                "stop llmcf-stt.service",
                "start llmcf-llm.service"
            ]
        );
        assert_eq!(s.metrics.switches.load(Relaxed), 2);
        let states = s.states();
        assert_eq!((states[0].state, states[1].state), ("running", "stopped"));
        assert_eq!((states[0].starts, states[0].stops, states[1].starts, states[1].stops), (2, 1, 1, 1));
    }
}
