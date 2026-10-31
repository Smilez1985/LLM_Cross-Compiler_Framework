#!/usr/bin/env python3
"""
The board's temperature (DECISIONS round 23 item 16; stage C wave 3c-2, chain boards).

Where a board shows its temperature, the framework reads it over the board session it already has (nothing is
installed, nothing written): during the checks of a runtime deploy and of `device runtime verify`, before and after the
short llama-bench run (runtime/device/remote.py verify_on_device). Two sources, both read-only:

- Linux's thermal zones, /sys/class/thermal/thermal_zone*/temp (millidegrees) and type, with the zone's own trip
  points (trip_point_*_temp and _type): the lowest "passive" trip is where the kernel starts to slow the chip down,
  else the lowest "hot" one;
- vcgencmd on a Raspberry Pi when it is there and allowed (measure_temp, and get_throttled: bit 0x8 means the
  firmware's soft temperature limit is active now).

A zone without such a trip point is judged by a conservative default per SoC family (from the board's
/proc/device-tree/compatible): DEFAULT_LIMITS, 80 °C for a Raspberry Pi (its firmware starts to slow down at about
80 °C) and any other board, 85 °C for Rockchip RK35xx (the passive trip point of their device trees), never above a
zone's critical trip point less CRITICAL_MARGIN. The hottest zone, measured against its limit, is the reading
(BoardHeat). A hot board gets a warning with what helps (cooling: a heatsink or a fan; fewer threads; a smaller
quantization; a pause between runs), the person in their language (person_lines), an AI the same facts in English
without any path or account (ai_text), and Mojo Oracle a line from the recorded reading (trusted.record_heat).

This is no probe key: the probe's schema (probe-keys) has none for the temperature, and a temperature is a moment, not
a property of the board.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# One read-only command: every readable zone with its type and trip points, vcgencmd's two answers when it is there,
# and the board's device-tree compatible string (the SoC family of the default limit). Values are parsed strictly.
THERMAL_COMMAND = (
    'for z in /sys/class/thermal/thermal_zone*; do [ -r "$z/temp" ] || continue; '
    't=$(cat "$z/temp" 2>/dev/null) || continue; n=${z##*/}; '
    'echo "zone $n $t $(cat "$z/type" 2>/dev/null)"; '
    'for p in "$z"/trip_point_*_temp; do [ -r "$p" ] || continue; i=${p##*trip_point_}; i=${i%_temp}; '
    'echo "trip $n $(cat "$p" 2>/dev/null) $(cat "$z/trip_point_${i}_type" 2>/dev/null)"; done; done; '
    'if command -v vcgencmd >/dev/null 2>&1; then '
    'echo "vc $(vcgencmd measure_temp 2>/dev/null) $(vcgencmd get_throttled 2>/dev/null)"; fi; '
    '[ -r /proc/device-tree/compatible ] && echo "compatible $(tr \'\\000\' \' \' < /proc/device-tree/compatible)"; '
    'true')
THERMAL_TIMEOUT = 20

FAMILIES = (("raspberry-pi", ("raspberrypi", "brcm,bcm2711", "brcm,bcm2712", "brcm,bcm2837", "brcm,bcm2835")),
            ("rockchip", ("rockchip,",)), ("allwinner", ("allwinner,",)), ("amlogic", ("amlogic,",)),
            ("nvidia-tegra", ("nvidia,tegra",)))
FAMILY_NAMES = {"raspberry-pi": "Raspberry Pi", "rockchip": "Rockchip", "allwinner": "Allwinner",
                "amlogic": "Amlogic", "nvidia-tegra": "NVIDIA Jetson"}   # "generic": no family name
DEFAULT_LIMITS = {"raspberry-pi": 80.0, "rockchip": 85.0, "generic": 80.0}
CRITICAL_MARGIN = 10.0        # a default limit stays this far below a zone's critical (shutdown) trip point
SOFT_LIMIT_BIT = 0x8          # vcgencmd get_throttled: the soft temperature limit is active now
PLAUSIBLE = (-40.0, 150.0)    # a reading outside is a sensor without a value (0x7fff..., -273 °C)

_ZONE = re.compile(r"^zone (thermal_zone\d{1,3}) (-?\d{1,7})(?: ([A-Za-z0-9_.-]{1,40}))?\s*$", re.ASCII)
_TRIP = re.compile(r"^trip (thermal_zone\d{1,3}) (-?\d{1,7}) (passive|active|hot|critical)\s*$", re.ASCII)
_KIND = re.compile(r"^[A-Za-z0-9_.() -]{1,40}$", re.ASCII)   # a zone's type as BoardHeat names it
_VC_TEMP = re.compile(r"temp=(-?\d{1,3}(?:\.\d{1,2})?)'C", re.ASCII)
_VC_THROTTLED = re.compile(r"throttled=0x([0-9a-fA-F]{1,8})", re.ASCII)


@dataclass(frozen=True)
class Zone:
    name: str                          # thermal_zone0
    kind: str                          # its type (cpu-thermal, soc-thermal, ...); "" when it has none
    celsius: float
    trips: Tuple[Tuple[str, float], ...] = ()   # (passive|active|hot|critical, °C)


@dataclass(frozen=True)
class BoardHeat:
    """What the reading says: the temperature of the zone closest to its limit, that limit and where it comes from
    ("trip": the zone's own trip point, "default": DEFAULT_LIMITS for `family`), and whether the firmware says it is
    slowing down for heat now. `hot` when the zone reached its limit or the firmware says so."""
    celsius: float
    limit: float
    zone: str
    limit_from: str
    family: str
    throttled: bool = False

    @property
    def hot(self) -> bool:
        return self.throttled or self.celsius >= self.limit

    def as_dict(self) -> Dict[str, Any]:
        """The reading as data (kept in the deployment's record, given to an AI and to the GUI's benchmark window):
        numbers, the zone's type and the SoC family, never a path or an account."""
        return {"celsius": round(self.celsius, 1), "limit_celsius": round(self.limit, 1), "zone": self.zone,
                "limit_from": self.limit_from, "family": self.family, "hot": self.hot, "throttled": self.throttled}


def from_dict(data: Any) -> Optional[BoardHeat]:
    """BoardHeat.as_dict() read back (None for anything else: a record written by another version stays quiet)."""
    if not isinstance(data, dict):
        return None
    try:
        celsius, limit = float(data["celsius"]), float(data["limit_celsius"])
        zone, source, family = str(data["zone"]), str(data["limit_from"]), str(data.get("family") or "generic")
    except (KeyError, TypeError, ValueError):
        return None
    if not (PLAUSIBLE[0] <= celsius <= PLAUSIBLE[1] and PLAUSIBLE[0] <= limit <= PLAUSIBLE[1]) \
            or source not in ("trip", "default") or not _KIND.fullmatch(zone) \
            or family not in (*FAMILY_NAMES, "generic"):
        return None
    return BoardHeat(celsius, limit, zone, source, family, bool(data.get("throttled")))


def _celsius(raw: str) -> Optional[float]:
    value = int(raw)
    celsius = value / 1000.0 if abs(value) >= 1000 else float(value)   # sysfs gives millidegrees; a few give °C
    return celsius if PLAUSIBLE[0] <= celsius <= PLAUSIBLE[1] else None


def family_of(compatible: str) -> str:
    text = (compatible or "").lower()
    for name, needles in FAMILIES:
        if any(needle in text for needle in needles):
            return name
    return "generic"


def parse(output: str) -> Tuple[List[Zone], Optional[float], Optional[int], str]:
    """(zones, vcgencmd's temperature, vcgencmd's throttled bits, the SoC family) of THERMAL_COMMAND's output; lines
    that are not exactly what the command prints are left out (the board's answer is never trusted as text)."""
    temps: Dict[str, Tuple[str, float]] = {}
    trips: Dict[str, List[Tuple[str, float]]] = {}
    vc_temp, throttled, family = None, None, "generic"
    for line in (output or "").splitlines():
        match = _ZONE.match(line)
        if match:
            celsius = _celsius(match.group(2))
            if celsius is not None:
                temps[match.group(1)] = (match.group(3) or "", celsius)
            continue
        match = _TRIP.match(line)
        if match:
            celsius = _celsius(match.group(2))
            if celsius is not None and celsius > 0:
                trips.setdefault(match.group(1), []).append((match.group(3), celsius))
            continue
        if line.startswith("vc "):
            t, b = _VC_TEMP.search(line), _VC_THROTTLED.search(line)
            vc_temp = float(t.group(1)) if t and PLAUSIBLE[0] <= float(t.group(1)) <= PLAUSIBLE[1] else None
            throttled = int(b.group(1), 16) if b else None
        elif line.startswith("compatible "):
            family = family_of(line[len("compatible "):])
    zones = [Zone(name, kind, celsius, tuple(trips.get(name, ()))) for name, (kind, celsius) in sorted(temps.items())]
    return zones, vc_temp, throttled, family


def zone_limit(zone: Zone, family: str) -> Tuple[float, str]:
    """(the temperature from which the zone's chip slows down, "trip" or "default")."""
    for kind in ("passive", "hot"):
        own = [t for k, t in zone.trips if k == kind]
        if own:
            return min(own), "trip"
    limit = DEFAULT_LIMITS.get(family, DEFAULT_LIMITS["generic"])
    critical = [t for k, t in zone.trips if k == "critical"]
    if critical:
        limit = min(limit, min(critical) - CRITICAL_MARGIN)
    return limit, "default"


def judge(zones: List[Zone], vc_temp: Optional[float], throttled: Optional[int], family: str) -> Optional[BoardHeat]:
    """The reading of a board (None when nothing could be read): the zone with the smallest margin to its limit;
    without a thermal zone vcgencmd's temperature against the family's default."""
    soft = bool(throttled is not None and throttled & SOFT_LIMIT_BIT)
    best: Optional[BoardHeat] = None
    for zone in zones:
        limit, source = zone_limit(zone, family)
        heat = BoardHeat(zone.celsius, limit, zone.kind or zone.name, source, family, soft)
        if best is None or (heat.limit - heat.celsius) < (best.limit - best.celsius):
            best = heat
    if best is None and vc_temp is not None:
        best = BoardHeat(vc_temp, DEFAULT_LIMITS.get(family, DEFAULT_LIMITS["generic"]), "soc (vcgencmd)", "default",
                         family, soft)
    return best


def _command() -> str:
    """THERMAL_COMMAND as llmcf's board operation `thermal` (runtime/device/board_ops.py defines it; a gated board runs
    only operations), or as the script itself where that module is not loaded."""
    from orchestrator.remote import gate
    try:
        return gate.script("thermal")
    except gate.GateRefused:
        return THERMAL_COMMAND


def read_heat(shell: Any) -> Optional[BoardHeat]:
    """The board's temperature over `shell` (runtime/device/remote.py RemoteShell): one read-only command that may run
    twice after a network drop; None when the board shows none or the command fails (the step goes on). A Cancel
    (RemoteError of kind "stopped") is passed on."""
    try:
        result = shell.run(_command(), again=True, timeout=THERMAL_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - the temperature is a bonus: the step's own command says what is wrong
        if getattr(e, "kind", "") == "stopped":
            raise
        return None
    if getattr(result, "status", 1) != 0:
        return None
    return judge(*parse(result.out))


def hotter(a: Optional[BoardHeat], b: Optional[BoardHeat]) -> Optional[BoardHeat]:
    """Of two readings the one closer to (or further past) its limit."""
    if a is None or b is None:
        return a or b
    if a.hot != b.hot:
        return a if a.hot else b
    return a if (a.limit - a.celsius) <= (b.limit - b.celsius) else b


def _number(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def person_lines(heat: Optional[BoardHeat], lang: Optional[str] = None) -> List[str]:
    """The person's lines for a reading, in their language: the temperature when it is fine; for a hot board the
    warning, what helps and the Mojo line."""
    from orchestrator.utils.localization import ui_text
    if heat is None:
        return []
    values = {"celsius": _number(heat.celsius), "limit": _number(heat.limit), "zone": heat.zone}
    if not heat.hot:
        return [ui_text("device.heat.fine", lang, **values)]
    if heat.limit_from == "trip":
        source = ui_text("device.heat.from_trip", lang)
    elif heat.family in FAMILY_NAMES:
        source = ui_text("device.heat.from_default", lang, family=FAMILY_NAMES[heat.family])
    else:
        source = ui_text("device.heat.from_generic", lang)
    lines = [ui_text("device.heat.hot", lang, limit_from=source, **values)]
    if heat.throttled:
        lines.append(ui_text("device.heat.throttled", lang))
    return lines + [ui_text("device.heat.advice", lang), ui_text("device.heat.mojo", lang)]


ADVICE = ("What helps: a heatsink or a fan; fewer threads (-t, for example half of the cores); a smaller quantization "
          "(for example Q4_K_M instead of Q8_0); a pause between runs so the board cools down.")


def ai_text(heat: Optional[BoardHeat]) -> str:
    """The same facts for an AI, in English: temperatures, the zone's type and the advice, never a path or an
    account ("" when the board is not hot)."""
    if heat is None or not heat.hot:
        return ""
    source = ("the zone's own trip point" if heat.limit_from == "trip" else
              f"a conservative default for {FAMILY_NAMES[heat.family]}" if heat.family in FAMILY_NAMES else
              "a conservative default, as the board names no limit of its own")
    text = (f"The board is hot: {_number(heat.celsius)} °C ({heat.zone}); it slows itself down from "
            f"{_number(heat.limit)} °C ({source}), so the benchmark measures less than the board can do.")
    if heat.throttled:
        text += " Its firmware says it is slowing down for heat now."
    return f"{text} Tell the person. {ADVICE}"


__all__ = ["ADVICE", "BoardHeat", "CRITICAL_MARGIN", "DEFAULT_LIMITS", "FAMILY_NAMES", "THERMAL_COMMAND", "Zone",
           "ai_text", "family_of", "from_dict", "hotter", "judge", "parse", "person_lines", "read_heat", "zone_limit"]
