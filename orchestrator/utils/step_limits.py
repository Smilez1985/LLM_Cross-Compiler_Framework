#!/usr/bin/env python3
"""
Time limits of a build step (DECISIONS round 25 Q8): no hard time limit. A step takes as long as it takes; what stops
it is a step that has stopped working, not the clock.

- **Soft cap** (setting `build_timeout`, now a soft cap; its old meaning, a hard limit, is delegated here): when a step
  runs longer, a build the person started goes on, with one warning that says so and that Cancel stops it. A build an
  AI started stops there and reports it: the soft cap of an AI-started build is never extended (an AI may only lower
  it: llm/settings_change.TIGHTEN_ONLY).
- **Idle timeout** (setting `build_idle_timeout`): a step that prints nothing for that long is stopped, for everyone;
  that is a hang, not a long build.
- Loops: a model that repeats itself in the functional test and a stage that keeps restarting are caught where they
  happen (the targets' model_check.py and the stage runner build.sh); they need no clock.

One StepClock per running step: the caller tells it about every line of output (output()), waits at most next_wait()
seconds at a time, and asks check() after each wait. check() raises StepTimedOut (a TimeoutError) when the step must
stop and returns the soft-cap warning once for a person's step. The texts name the setting, never a path.
"""

import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

SOFT_CAP_SETTING = "build_timeout"
IDLE_SETTING = "build_idle_timeout"
DEFAULT_SOFT_CAP = 3600
DEFAULT_IDLE = 1800
POLL = 30.0          # the longest single wait of a caller between two checks
MIN_WAIT = 0.05


def seconds(value: float) -> str:
    """'5' for 5.0, '0.05' for 0.05: how the texts name a number of seconds."""
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


class StepTimedOut(TimeoutError):
    """A step stopped by its time limits; `kind` is "soft_cap" (an AI-started build at its soft cap) or "idle"."""

    def __init__(self, text: str, kind: str):
        super().__init__(text)
        self.kind = kind


@dataclass(frozen=True)
class StepLimits:
    """The limits of one step. `soft_cap`: seconds (None: none); `idle`: seconds without output (None: none);
    `ai_started`: an AI started the build, so the soft cap stops it."""
    soft_cap: Optional[float] = DEFAULT_SOFT_CAP
    idle: Optional[float] = DEFAULT_IDLE
    ai_started: bool = False

    @classmethod
    def from_settings(cls, get: Callable[[str, Any], Any], *, owner: str = "") -> "StepLimits":
        """From the settings (`get(key, default)`, e.g. ConfigManager.get); an owner (a tool caller) is an AI."""
        return cls(_positive(get(SOFT_CAP_SETTING, DEFAULT_SOFT_CAP), DEFAULT_SOFT_CAP),
                   _positive(get(IDLE_SETTING, DEFAULT_IDLE), DEFAULT_IDLE), bool(owner))

    @classmethod
    def hard(cls, limit: float) -> "StepLimits":
        """A plain time limit that always stops (the framework's own short steps, and callers that pass a number)."""
        return cls(soft_cap=float(limit), idle=None, ai_started=True)


def _positive(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number > 0 else float(default)


class StepClock:
    """The time limits of one running step (see the module text)."""

    def __init__(self, limits: StepLimits, stage: str = "build", clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self.stage = stage or "build"
        self.clock = clock
        self.started = self.last_output = clock()
        self.warned = False

    def output(self) -> None:
        """The step printed something: it is not idle."""
        self.last_output = self.clock()

    def elapsed(self) -> float:
        return self.clock() - self.started

    def next_wait(self, poll: float = POLL) -> float:
        """How long the caller may wait before it asks check() again."""
        now = self.clock()
        waits = [poll]
        if self.limits.idle:
            waits.append(self.limits.idle - (now - self.last_output))
        if self.limits.soft_cap and (self.limits.ai_started or not self.warned):
            waits.append(self.limits.soft_cap - (now - self.started))
        return max(MIN_WAIT, min(waits))

    def check(self) -> Optional[str]:
        """Raises StepTimedOut when the step must stop; returns the soft-cap warning once for a person's step."""
        now = self.clock()
        idle, cap = self.limits.idle, self.limits.soft_cap
        if idle and now - self.last_output >= idle:
            raise StepTimedOut(f"The {self.stage} stage printed nothing for {seconds(idle)} s and was stopped "
                               f"(setting {IDLE_SETTING}): it no longer worked.", "idle")
        if cap and now - self.started >= cap:
            if self.limits.ai_started:
                raise StepTimedOut(f"The {self.stage} stage ran longer than {seconds(cap)} s and was stopped (setting "
                                   f"{SOFT_CAP_SETTING}): an AI started this build, and the soft cap of an AI-started "
                                   "build is never extended. The person can start it again, or raise the soft cap.",
                                   "soft_cap")
            if not self.warned:
                self.warned = True
                idle_text = (f"; it is stopped only if it prints nothing for {seconds(idle)} s (setting "
                             f"{IDLE_SETTING})") if idle else ""
                return (f"The {self.stage} stage has run longer than {seconds(cap)} s (setting {SOFT_CAP_SETTING}, a "
                        f"soft cap). It goes on because you started it; Cancel stops it{idle_text}.")
        return None


__all__ = ["DEFAULT_IDLE", "DEFAULT_SOFT_CAP", "IDLE_SETTING", "POLL", "SOFT_CAP_SETTING", "StepClock", "StepLimits",
           "StepTimedOut", "seconds"]
