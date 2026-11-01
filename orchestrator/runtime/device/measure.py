#!/usr/bin/env python3
"""
The board measurement: what a board really has for its local model, measured on that board, never guessed.

It runs with the short benchmark on the board (verify_on_device with a model that may run: `llm-cli device runtime
verify --bench-model`, the Boards window's and the Deployment window's benchmark, the AI's run_device_benchmark and a
deploy with a benchmark model), over the board's gate (two operations defined here, both as the unprivileged account):

- measure-idle (reads only): MemTotal, MemAvailable, SwapTotal and SwapFree of /proc/meminfo, the zram devices' sizes,
  the online CPUs, the CPU's top and current clock and the size of the model file; once before and once after the
  load, so the swing of the idle memory is measured too;
- measure-load (a job on its own, like the benchmark): the build's own llama-server with the chosen model on loopback
  (the fixed options of the shared server policy, the key file, a cleared environment, an empty stdin) once per
  context step of CTX_STEPS, each time until it says it serves (at most LOAD_SECONDS), then its resident memory
  (VmRSS, VmHWM of /proc/<pid>/status), the free memory under load, the CPU clock (throttling) and the model's
  training context from its own log (only digits are kept, never the log's text); the server is stopped after
  each step.

derive() turns the record into the board's limits (BoardLimits), all from measured numbers:

- the per-board reserve: what must stay free beside the server, RESERVE_SHARE of the measured RAM (at least
  RESERVE_MIN_MIB) plus the measured swing of the idle free memory;
- the KV cache per token: the growth of the server's resident memory from the first to the last context step (in the
  KV type it was measured with; another type by its ggml block size, hardware.KV_TYPE_FACTORS), the model's own
  memory the rest (the weights, the compute buffers);
- the KV type: the person's choice, else the boards' rule (hardware.kv_kind_for_left: q8_0 when less than
  KV_LITTLE_RAM is left next to the model and the reserve);
- the prompt cache (--cache-ram): Ditto's rule of the eighth of the RAM (hardware.CACHE_RAM_RULE_MIB) but at most half
  of what is left next to the model and the KV cache of CTX_FLOOR tokens; below CACHE_RAM_MIN_MIB it is off;
- the context: what fits in the rest, in steps of 256, never above the model's training context or CTX_CAP; below
  CTX_MIN the model does not fit on this board (`fits` False, the reason says why).

The record (llm-framework.board-limits/1, MEASURE_VERSION) is kept per board next to its probe
(<state>/hardware/devices/<id>.limits.json, runtime/profile.ProfileStore), and the limits go to the board as a small
key=value file (LIMITS_REL, written by limits-write) that the start script reads: context, KV type, prompt cache,
the server's measured need and the reserve, the model's size. Without that file and without -c the start script
refuses to start the server. The person sees every number (person_lines); an AI gets numbers and words only
(ai_facts), never a path or an account.
"""

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from orchestrator.remote import gate
from orchestrator.remote.gate import define
from orchestrator.runtime.device.launcher import LIMITS_REL
from orchestrator.runtime.server_policy import (KV_CACHE_TYPES, LOOPBACK, POSIX_CLEARED_PATH, SERVER_FIXED,
                                                check_server_command, posix_cleared_env)

SCHEMA = "llm-framework.board-limits/1"
MEASURE_VERSION = 1
CTX_STEPS = (512, 1536)          # two context steps: the KV growth between them is measured
CTX_FLOOR = 2048                 # the context the prompt cache must leave room for
CTX_MIN = 512                    # less than this: the model does not fit on the board
CTX_CAP = 32768                  # never more than this, whatever fits
LOAD_SECONDS = 120               # how long one step waits for the server to serve
SETTLE_SECONDS = 2               # after it serves: the warm-up has touched the weights
MEASURE_PORT = 65431             # the measurement's own loopback port (not the server's)
RESERVE_MIN_MIB = 128
RESERVE_SHARE = 0.05
THROTTLE_SHARE = 0.85            # the clock under load below this share of its top: the board slows down
LIMITS_MAX = 2048                # bytes of the limits file on the board
READY_TEXT = "starting the main loop"   # llama-server's line once it serves (at the pin)
MIB = 1024 * 1024


# --- the operations on the board ------------------------------------------------------------------------------------

def _idle(model: str, rest: str = "") -> str:
    return "; ".join([
        "awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {sub(\":\", \"\", $1); print \"mem\", $1, $2}' "
        "/proc/meminfo 2>/dev/null",
        'for z in /sys/block/zram*; do [ -r "$z/disksize" ] && echo "zram $(cat "$z/disksize" 2>/dev/null)"; done',
        'echo "cpus $(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 0)"',
        'f=/sys/devices/system/cpu/cpu0/cpufreq; [ -r $f/cpuinfo_max_freq ] && echo "freqmax $(cat $f/cpuinfo_max_freq)"',
        '[ -r $f/scaling_cur_freq ] && echo "freq $(cat $f/scaling_cur_freq)"',
        f'[ -f {model} ] && echo "model $(wc -c < {model} | tr -d \' \')"',
        "true"])


define("measure-idle", ("path",), _idle, doc="the board's memory, swap, zram, CPUs, clock and the model's size "
                                              "(reads only)")


def _server_words(home: str, program: str, model: str, port: str, key: str) -> str:
    """The measurement's server line: the shared server policy's fixed options on loopback with the key file (held
    to check_server_command, as the start script's line is)."""
    argv = ["@P@", "-m", "@M@", "--host", LOOPBACK, "--port", "65000", "--api-key-file", "@K@", *SERVER_FIXED]
    check_server_command(argv)
    env = " ".join(f"{k}={v}" for k, v in posix_cleared_env("@H@").items())
    line = f"exec env -i {env} " + " ".join(argv)
    return (line.replace("@P@", program).replace("@M@", model).replace("@K@", key).replace("@H@", home)
            .replace("--port 65000", f"--port {port}"))


def _load_job(home: str, program: str, model: str, port: str, key: str, kv: str, rest: str = "") -> str:
    from orchestrator.runtime.device.board_ops import BENCH_PID
    server = _server_words(home, program, model, port, key)
    return "\n".join([
        f"case {program} in */bin/llama-server) ;; *) echo 'only llama-server is measured' >&2; exit 3 ;; esac",
        f"case {kv} in {'|'.join(KV_CACHE_TYPES)}) ;; *) echo 'not a KV-cache type' >&2; exit 3 ;; esac",
        "llmcf_p=",
        "trap '[ -n \"$llmcf_p\" ] && kill \"$llmcf_p\" 2>/dev/null; exit 143' INT TERM",
        f"echo $$ > {BENCH_PID}",
        "llmcf_d=$(pwd)",
        f'case {kv} in f16) llmcf_kv="" ;; *) llmcf_kv="-ctk {kv} -ctv {kv}" ;; esac',
        "{",
        f"for llmcf_c in {rest}; do",
        '  rm -f "$llmcf_d/srv.log"',
        f'  (PATH={POSIX_CLEARED_PATH}; export PATH; cd {home} && {server} -c "$llmcf_c" $llmcf_kv '
        '</dev/null >"$llmcf_d/srv.log" 2>&1) &',
        "  llmcf_p=$!",
        "  llmcf_i=0; llmcf_up=0",
        f"  while [ $llmcf_i -lt {LOAD_SECONDS} ]; do",
        '    kill -0 "$llmcf_p" 2>/dev/null || break',
        f'    if grep -q "{READY_TEXT}" "$llmcf_d/srv.log" 2>/dev/null; then llmcf_up=1; break; fi',
        "    sleep 1; llmcf_i=$((llmcf_i + 1))",
        "  done",
        "  llmcf_rss=0; llmcf_hwm=0; llmcf_av=0",
        "  if [ $llmcf_up = 1 ]; then",
        f"    sleep {SETTLE_SECONDS}",
        "    llmcf_rss=$(awk '/^VmRSS:/ {print $2}' /proc/$llmcf_p/status 2>/dev/null)",
        "    llmcf_hwm=$(awk '/^VmHWM:/ {print $2}' /proc/$llmcf_p/status 2>/dev/null)",
        "    llmcf_av=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)",
        "  fi",
        "  llmcf_f=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null)",
        "  llmcf_t=$(sed -n 's/.*n_ctx_train[^0-9]*\\([0-9][0-9]*\\).*/\\1/p' \"$llmcf_d/srv.log\" 2>/dev/null "
        "| head -n 1)",
        '  echo "step ctx=$llmcf_c up=$llmcf_up rss_kib=${llmcf_rss:-0} hwm_kib=${llmcf_hwm:-0} '
        'avail_kib=${llmcf_av:-0} freq_khz=${llmcf_f:-0} train=${llmcf_t:-0}"',
        '  kill "$llmcf_p" 2>/dev/null; wait "$llmcf_p" 2>/dev/null',
        "  llmcf_p=",
        "done",
        '} >out 2>err',
        'rm -f "$llmcf_d/srv.log"',
        "true"])


def _load_refusal(args) -> str:
    if not str(args[1]).endswith("/bin/llama-server"):
        return "only llama-server is measured"
    if args[5] not in KV_CACHE_TYPES:
        return f"the KV-cache type is one of {', '.join(KV_CACHE_TYPES)}"
    if not 1 <= len(args) - 6 <= 4:
        return "one to four context steps"
    return ""


define("measure-load", ("home", "program", "path", "uint", "path", "word"), _load_job, rest="uint", rest_max=4,
       job=True, refuse=_load_refusal,
       doc="the build's llama-server with the model at each context step, on loopback with the key file: its "
           "resident memory, the free memory, the CPU clock and the model's training context (numbers only)")


def _limits_write(home: str, rest: str = "") -> str:
    folder = f"{home}/{LIMITS_REL.rsplit('/', 1)[0]}"
    target = f"{home}/{LIMITS_REL}"
    return (f"umask 077 && mkdir -p {folder} && chmod 700 {folder} && head -c {LIMITS_MAX} > {target}.tmp && "
            f"mv -f {target}.tmp {target}")


define("limits-write", ("home",), _limits_write, doc="the board's measured limits for the start script (stdin)")


# --- parsing --------------------------------------------------------------------------------------------------------

_MEM = re.compile(r"^mem (MemTotal|MemAvailable|SwapTotal|SwapFree) (\d{1,12})$", re.ASCII)
_ONE = re.compile(r"^(zram|cpus|freqmax|freq|model) (\d{1,15})$", re.ASCII)
_STEP = re.compile(r"^step ctx=(\d{1,7}) up=([01]) rss_kib=(\d{1,12}) hwm_kib=(\d{1,12}) avail_kib=(\d{1,12}) "
                   r"freq_khz=(\d{1,10}) train=(\d{1,9})$", re.ASCII)


@dataclass
class Idle:
    """The board at rest (MiB; the clock in kHz; 0 where the board shows nothing)."""
    total_mib: int = 0
    available_mib: int = 0
    swap_total_mib: int = 0
    swap_free_mib: int = 0
    zram_mib: int = 0
    cpus: int = 0
    freq_max_khz: int = 0
    freq_khz: int = 0
    model_bytes: int = 0


def parse_idle(text: str) -> Idle:
    """measure-idle's answer; lines that are not exactly its own are ignored."""
    idle = Idle()
    mem = {}
    for line in (text or "").splitlines():
        line = line.strip()
        m = _MEM.fullmatch(line)
        if m:
            mem[m.group(1)] = int(m.group(2)) // 1024   # kB -> MiB
            continue
        m = _ONE.fullmatch(line)
        if not m:
            continue
        value = int(m.group(2))
        if m.group(1) == "zram":
            idle.zram_mib += value // MIB
        elif m.group(1) == "cpus":
            idle.cpus = value
        elif m.group(1) == "freqmax":
            idle.freq_max_khz = value
        elif m.group(1) == "freq":
            idle.freq_khz = value
        else:
            idle.model_bytes = value
    idle.total_mib = mem.get("MemTotal", 0)
    idle.available_mib = mem.get("MemAvailable", 0)
    idle.swap_total_mib = mem.get("SwapTotal", 0)
    idle.swap_free_mib = mem.get("SwapFree", 0)
    return idle


@dataclass
class Step:
    """One context step under load: whether the server served, its resident memory (MiB), the free memory then, the
    CPU clock and the model's training context (0: unknown)."""
    ctx: int
    up: bool
    rss_mib: float = 0.0
    hwm_mib: float = 0.0
    available_mib: int = 0
    freq_khz: int = 0
    train_ctx: int = 0


def parse_load(text: str) -> List[Step]:
    steps = []
    for line in (text or "").splitlines():
        m = _STEP.fullmatch(line.strip())
        if m:
            ctx, up, rss, hwm, avail, freq, train = (int(g) for g in m.groups())
            steps.append(Step(ctx, bool(up) and rss > 0, round(rss / 1024, 1), round(hwm / 1024, 1), avail // 1024,
                              freq, train))
    return steps


# --- the record and the limits --------------------------------------------------------------------------------------

@dataclass
class Measurement:
    """What was measured on the board (numbers only), with the build and the model it was measured with."""
    measured_at: str
    build_id: str
    model_sha256: str
    model_bytes: int
    kv_measured: str
    idle_before: Idle
    idle_after: Idle
    steps: List[Step]
    heat: Optional[Dict[str, Any]] = None
    version: int = MEASURE_VERSION

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["schema"] = SCHEMA
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Measurement":
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ValueError("not a board measurement record")
        if data.get("version") != MEASURE_VERSION:
            raise ValueError(f"a board measurement of version {data.get('version')}, this framework reads "
                             f"{MEASURE_VERSION}: measure the board again")
        kv = data.get("kv_measured")
        if kv not in KV_CACHE_TYPES:
            raise ValueError("the measured KV-cache type is not one of the framework's")
        return cls(measured_at=str(data["measured_at"])[:32], build_id=str(data["build_id"])[:64],
                   model_sha256=str(data["model_sha256"])[:64], model_bytes=int(data["model_bytes"]),
                   kv_measured=kv, idle_before=Idle(**data["idle_before"]), idle_after=Idle(**data["idle_after"]),
                   steps=[Step(**s) for s in data.get("steps") or []], heat=data.get("heat"))


@dataclass
class BoardLimits:
    """The board's limits for its local model, derived from a Measurement (MiB unless named otherwise)."""
    fits: bool
    reason: str
    total_mib: int
    available_mib: int
    reserve_mib: int
    usable_mib: int
    base_mib: int = 0
    kv_bytes_per_token: int = 0          # of kv_cache
    f16_bytes_per_token: int = 0
    kv_cache: str = "f16"
    kv_why: str = "measured"
    ctx: int = 0
    train_ctx: int = 0
    cache_ram_mib: int = 0
    need_mib: int = 0
    model_bytes: int = 0
    model_sha256: str = ""
    throttled: bool = False
    swap_mib: int = 0
    zram_mib: int = 0
    cpus: int = 0
    measured_at: str = ""
    build_id: str = ""
    version: int = MEASURE_VERSION
    reasons: Dict[str, str] = field(default_factory=dict)

    def board_file(self) -> str:
        """The key=value file the start script reads (LIMITS_REL): numbers and the KV type only."""
        return "".join(f"{k}={v}\n" for k, v in (
            ("version", self.version), ("ctx", self.ctx if self.fits else 0), ("kv", self.kv_cache),
            ("cache_ram", self.cache_ram_mib), ("need_mib", self.need_mib), ("reserve_mib", self.reserve_mib),
            ("model_size", self.model_bytes)))

    def need_for(self, model_bytes: int, ctx: int, kv: Optional[str] = None) -> int:
        """MiB a server of a model of `model_bytes` needs at `ctx` tokens on this board: the measured model's own
        memory scaled by the model size (the weights dominate it), the measured KV cache per token (of `kv`, the
        board's type by default) and the prompt cache."""
        from orchestrator.runtime.hardware import KV_TYPE_FACTORS
        base = self.base_mib
        if self.model_bytes and model_bytes and model_bytes != self.model_bytes:
            base = int(self.base_mib - self.model_bytes / MIB + model_bytes / MIB)
        per_token = (self.f16_bytes_per_token * KV_TYPE_FACTORS.get(kv, 1.0)) if kv else self.kv_bytes_per_token
        return int(base + per_token * ctx / MIB + self.cache_ram_mib)

    def ai_facts(self) -> Dict[str, Any]:
        """Numbers and words only (no path, no account) for an AI."""
        return {"measured": True, "fits": self.fits, "context_tokens": self.ctx, "kv_cache": self.kv_cache,
                "prompt_cache_mib": self.cache_ram_mib, "server_needs_mib": self.need_mib,
                "reserve_mib": self.reserve_mib, "usable_mib": self.usable_mib, "ram_mib": self.total_mib,
                "free_at_rest_mib": self.available_mib, "kv_bytes_per_token": self.kv_bytes_per_token,
                "training_context": self.train_ctx, "throttled": self.throttled, "measured_at": self.measured_at,
                "version": self.version, "reason": self.reason}

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _kib_per_token(steps: Sequence[Step]) -> Optional[float]:
    up = [s for s in steps if s.up]
    if len(up) < 2:
        return None
    a, b = up[0], up[-1]
    if b.ctx <= a.ctx:
        return None
    return max(0.0, (max(b.rss_mib, b.hwm_mib) - max(a.rss_mib, a.hwm_mib)) * 1024 / (b.ctx - a.ctx))


def derive(m: Measurement, kv_chosen: Optional[str] = None) -> BoardLimits:
    """The board's limits from the measurement `m` (see the module text); `kv_chosen` is the person's KV type (None:
    automatic, the boards' rule with the measured numbers)."""
    from orchestrator.runtime.hardware import CACHE_RAM_MIN_MIB, CACHE_RAM_RULE_MIB, KV_TYPE_FACTORS, kv_kind_for_left
    total = m.idle_before.total_mib or m.idle_after.total_mib
    available = min(v for v in (m.idle_before.available_mib, m.idle_after.available_mib) if v) \
        if (m.idle_before.available_mib or m.idle_after.available_mib) else 0
    swing = abs(m.idle_before.available_mib - m.idle_after.available_mib) \
        if m.idle_before.available_mib and m.idle_after.available_mib else 0
    reserve = max(RESERVE_MIN_MIB, int(round(total * RESERVE_SHARE))) + swing
    usable = max(0, available - reserve)
    throttled = any(s.freq_khz and m.idle_before.freq_max_khz and s.freq_khz < THROTTLE_SHARE *
                    m.idle_before.freq_max_khz for s in m.steps if s.up) or bool((m.heat or {}).get("throttled"))
    train = max((s.train_ctx for s in m.steps), default=0)
    common = dict(total_mib=total, available_mib=available, reserve_mib=reserve, usable_mib=usable,
                  model_bytes=m.model_bytes or m.idle_before.model_bytes, model_sha256=m.model_sha256,
                  throttled=throttled, swap_mib=m.idle_before.swap_total_mib, zram_mib=m.idle_before.zram_mib,
                  cpus=m.idle_before.cpus, measured_at=m.measured_at, build_id=m.build_id, train_ctx=train)
    reasons: Dict[str, str] = {"reserve_mib": f"{reserve} MiB: {int(RESERVE_SHARE * 100)} % of the measured "
                                              f"{total} MiB RAM (at least {RESERVE_MIN_MIB} MiB) plus the measured "
                                              f"swing of the free memory at rest ({swing} MiB)"}
    if not total or not available:
        return BoardLimits(False, "the board did not show its memory", reasons=reasons, **common)
    per_kib = _kib_per_token(m.steps)
    if per_kib is None:
        started = [s.ctx for s in m.steps if s.up]
        why = ("the server did not start with this model on the board" if not started else
               "the server started at one context step only")
        return BoardLimits(False, why + f" ({usable} MiB usable): try a smaller model or quantization",
                           reasons=reasons, **common)
    first = next(s for s in m.steps if s.up)
    measured_factor = KV_TYPE_FACTORS[m.kv_measured]
    f16_per_token = per_kib * 1024 / measured_factor
    base = int(max(first.rss_mib, first.hwm_mib) - per_kib * first.ctx / 1024)
    left = usable - base
    if kv_chosen in KV_TYPE_FACTORS:
        kind, kv_why = kv_chosen, "the person's choice"
    else:
        kind = kv_kind_for_left(left * MIB)
        kv_why = f"automatic: {left} MiB left next to the model and the reserve"
    per_token = f16_per_token * KV_TYPE_FACTORS[kind]
    low, high = CACHE_RAM_RULE_MIB
    rule = max(low, min(high, total // 8))
    room = left - int(per_token * min(CTX_FLOOR, train or CTX_FLOOR) / MIB)
    cache = min(rule, max(0, room) // 2)
    if cache < CACHE_RAM_MIN_MIB:
        cache = 0
    fits_tokens = int(max(0, left - cache) * MIB // max(per_token, 1))
    cap = min(CTX_CAP, train) if train else CTX_CAP
    ctx = min(fits_tokens, cap) // 256 * 256
    reasons.update({
        "kv_cache": f"{kind} ({kv_why})",
        "cache_ram_mib": (f"{cache} MiB: an eighth of the RAM ({low} to {high} MiB), at most half of the {max(room, 0)} "
                          f"MiB left next to the model and {CTX_FLOOR} tokens" + ("; too little: off" if not cache
                                                                                   else "")),
        "ctx": (f"{ctx} tokens: {max(left - cache, 0)} MiB left, {int(per_token)} bytes per token measured"
                + (f", the model's training context {train}" if train and ctx == cap else "")
                + (f", the cap {CTX_CAP}" if ctx == CTX_CAP else ""))})
    need = int(base + per_token * ctx / MIB + cache)
    limits = BoardLimits(ctx >= CTX_MIN, "", base_mib=base, kv_bytes_per_token=int(per_token),
                         f16_bytes_per_token=int(f16_per_token), kv_cache=kind, kv_why=kv_why, ctx=ctx,
                         cache_ram_mib=cache, need_mib=need, reasons=reasons, **common)
    limits.reason = (f"{ctx} tokens fit next to the model ({base} MiB), {kind} KV cache, {cache} MiB prompt cache, "
                     f"{reserve} MiB kept free" if limits.fits else
                     f"the model ({base} MiB) leaves room for only {ctx} tokens of the {usable} MiB usable: try a "
                     "smaller model or quantization")
    return limits


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- on the board ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class MeasureRequest:
    """A measurement with the short benchmark: the person's KV-cache type (None: automatic; the load runs with f16
    then), the build and the model's sha256 it is recorded with."""
    kv_chosen: Optional[str] = None
    build_id: str = ""
    model_sha256: str = ""


def measure_on_board(shell: Any, *, home: str, root: str, folder: str, model: str, request: MeasureRequest,
                     heat: Any = None) -> Measurement:
    """The measurement over the board session `shell` (runtime/device/remote.py RemoteShell): idle, the load at
    CTX_STEPS with the build's llama-server in `folder`, idle again. RemoteError of the session is passed on."""
    from orchestrator.runtime.device.launcher import KEY_REL
    from orchestrator.runtime.device.remote import follow_job
    before = parse_idle(shell.run(gate.script("measure-idle", model), again=True).out)
    kv = request.kv_chosen or "f16"
    job = gate.job("measure-load", home, f"{folder}/bin/llama-server", model, str(MEASURE_PORT), f"{home}/{KEY_REL}",
                   kv, *(str(c) for c in CTX_STEPS))
    result = follow_job(shell, root, job, timeout=len(CTX_STEPS) * (LOAD_SECONDS + 30))
    after = parse_idle(shell.run(gate.script("measure-idle", model), again=True).out)
    return Measurement(measured_at=utc_stamp(), build_id=request.build_id, model_sha256=request.model_sha256,
                       model_bytes=before.model_bytes, kv_measured=kv, idle_before=before, idle_after=after,
                       steps=parse_load(result.out), heat=heat.as_dict() if hasattr(heat, "as_dict") else None)


def write_board_limits(shell: Any, home: str, limits: BoardLimits) -> bool:
    """The limits file for the start script on the board (limits-write); False when the board refused it."""
    data = limits.board_file().encode("ascii")
    res = shell.run(gate.script("limits-write", home), stdin=[data], again=True)
    return res.status == 0


# --- kept per board -------------------------------------------------------------------------------------------------

def save(state_dir: Path, device_id: str, measurement: Measurement) -> Path:
    from orchestrator.runtime.profile import ProfileStore
    return ProfileStore(state_dir).save_limits(device_id, measurement.as_dict())


def load(state_dir: Path, device_id: str) -> Optional[Measurement]:
    """The board's stored measurement, None when there is none or it is of another version (then the board is
    measured again; a damaged or older record is never read as limits)."""
    from orchestrator.runtime.profile import ProfileStore
    try:
        data = ProfileStore(state_dir).load_limits(device_id)
        return Measurement.from_dict(data) if data else None
    except (ValueError, KeyError, TypeError):
        return None


def board_limits(state_dir: Path, device_id: str, kv_chosen: Optional[str] = None) -> Optional[BoardLimits]:
    """The board's limits from its stored measurement (None: not measured)."""
    m = load(state_dir, device_id)
    return derive(m, kv_chosen) if m is not None else None


NOT_MEASURED = ("this board has not been measured yet: run the benchmark on the board once (it measures the board's "
                "memory with the model); no board limit is ever guessed")


def person_lines(limits: Optional[BoardLimits], lang: Optional[str] = None) -> List[str]:
    """The measured limits in plain words for the person (English or German)."""
    de = (lang or "").lower().startswith("de")
    if limits is None:
        return [("Dieses Board ist noch nicht vermessen: lass einmal den Benchmark auf dem Board laufen (er misst den "
                 "Speicher des Boards mit dem Modell); ein Board-Grenzwert wird nie geraten.") if de else
                NOT_MEASURED[0].upper() + NOT_MEASURED[1:] + "."]
    if de:
        lines = [f"Gemessen am {limits.measured_at}: {limits.total_mib} MiB RAM, {limits.available_mib} MiB frei in "
                 f"Ruhe, {limits.reserve_mib} MiB bleiben frei (Reserve), {limits.usable_mib} MiB nutzbar."]
        lines.append(f"Kontext {limits.ctx} Token, KV-Cache {limits.kv_cache}, Prompt-Cache {limits.cache_ram_mib} "
                     f"MiB, der Server braucht {limits.need_mib} MiB." if limits.fits else
                     f"Das Modell passt nicht: {limits.reason}")
        if limits.throttled:
            lines.append("Das Board hat unter Last den Takt gesenkt (Hitze oder Strom).")
        return lines
    lines = [f"Measured {limits.measured_at}: {limits.total_mib} MiB RAM, {limits.available_mib} MiB free at rest, "
             f"{limits.reserve_mib} MiB kept free (reserve), {limits.usable_mib} MiB usable."]
    lines.append(f"Context {limits.ctx} tokens, KV cache {limits.kv_cache}, prompt cache {limits.cache_ram_mib} MiB, "
                 f"the server needs {limits.need_mib} MiB." if limits.fits else
                 f"The model does not fit: {limits.reason}")
    if limits.throttled:
        lines.append("The board lowered its clock under load (heat or power).")
    return lines


def ai_facts(limits: Optional[BoardLimits]) -> Dict[str, Any]:
    return limits.ai_facts() if limits is not None else {"measured": False, "reason": NOT_MEASURED}


__all__ = ["BoardLimits", "CTX_STEPS", "Idle", "LIMITS_REL", "MEASURE_VERSION", "MeasureRequest", "Measurement",
           "NOT_MEASURED", "SCHEMA", "Step", "ai_facts", "board_limits", "derive", "load", "measure_on_board",
           "parse_idle", "parse_load", "person_lines", "save", "write_board_limits"]
