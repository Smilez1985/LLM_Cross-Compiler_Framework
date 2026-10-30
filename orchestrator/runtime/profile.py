#!/usr/bin/env python3
"""
Hardware profiles: the probe file (target_hardware_config.txt, schema 2), its parser and the per-user store.

The probe runs on the machine that will run the binaries and writes KEY=VALUE lines. configs/hardware_probe_schema.yml
lists every key with its source, normalisation and validation; this module loads that file and is the only reader of
probe files:

- parse_profile(), read_profile() and parse_profile_json() validate a schema 2 file strictly: a duplicate key, an
  empty value, a value outside the schema or a missing required key rejects the whole file. A schema 1 file (today's
  hardware_probe.sh / .ps1 output, no PROBE_SCHEMA line) is converted leniently: what maps is validated and kept, the
  rest is dropped without keeping its value (HOSTNAME, RAM part numbers, ...), and the profile says it should be
  probed again before a build.
- HardwareProfile holds only validated, normalised values. perf_key() is the hash benchmark results are keyed by; it
  ignores volatile and recorded-only keys.
- ProfileStore keeps profiles in <state dir>/hardware/: local.txt for this machine and devices/<id>.txt (plus a small
  .json with the user's label and the SSH address) for other machines. Volatile values are never stored.

Probe files come from devices over SSH or are carried in by hand, so they are untrusted. Nothing here runs a command,
and free-text values (CPU_MODEL, GPU names, BOARD_MODEL) are for display only.
"""

import codecs
import hashlib
import json
import os
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import yaml

from orchestrator.remote.trust import FINGERPRINT, device_id_for
from orchestrator.utils.atomic import write_private

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "configs" / "hardware_probe_schema.yml"
PROFILE_FILE_NAME = "target_hardware_config.txt"
BOOL_VALUES = ("ON", "OFF", "UNKNOWN")
PERF_KEY_VERSION = 1  # bump when the hashed structure changes: every stored benchmark result then becomes stale

_TYPES = {"bool", "enum", "int", "pattern", "version", "text", "list", "timestamp"}
_STABILITY = {"stable", "volatile"}
_PERF = {"key", "recorded", "none"}
_GPU_KEY = re.compile(r"^GPU(\d{1,3})_([A-Z][A-Z0-9_]*)$", re.ASCII)
_INT = re.compile(r"^(0|[1-9][0-9]{0,11})$", re.ASCII)
_SINCE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$", re.ASCII)
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_HEX4 = re.compile(r"^0x[0-9a-f]{4}$", re.ASCII)
_DOTTED = re.compile(r"(\d{1,5}(?:\.\d{1,6}){0,3})", re.ASCII)
_SCHEMA1_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$", re.ASCII)
# Unicode categories never allowed in a value: controls, format characters (zero-width, bidi overrides), separators.
_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


class ProfileError(ValueError):
    """A probe file or stored profile that cannot be used; the message names the line or key and the reason."""


class SchemaError(RuntimeError):
    """configs/hardware_probe_schema.yml itself is broken (a framework bug, never caused by a probe file)."""


def _show(text: str, limit: int = 40) -> str:
    """Untrusted text for an error message: quoted, escaped and shortened."""
    return repr(text if len(text) <= limit else text[:limit] + "...")


def _clean(text: str) -> Optional[str]:
    """The reason a value is unusable as one line of text, or None."""
    for ch in text:
        if unicodedata.category(ch) in _FORBIDDEN_CATEGORIES:
            return f"contains the control or format character U+{ord(ch):04X}"
    return None


# --- the schema --------------------------------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class KeySpec:
    """One key of the schema (for per-GPU keys `name` is the part after GPU<n>_)."""
    name: str
    group: str
    type: str
    required: bool
    stability: str
    perf: str
    indexed: bool = False
    writer: str = "probe"
    values: Tuple[str, ...] = ()
    pattern: Optional["re.Pattern[str]"] = None
    item_pattern: Optional["re.Pattern[str]"] = None
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    max_length: Optional[int] = None
    max_items: Optional[int] = None
    only_arch: Tuple[str, ...] = ()
    only_os: Tuple[str, ...] = ()
    only_gpu_vendor: Tuple[str, ...] = ()
    os_values: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    cpuinfo: Tuple[str, ...] = ()
    cpuinfo_any: Tuple[str, ...] = ()
    since: Optional[str] = None  # the probe version that added the key (a file of an older probe lacks it)


class ProbeSchema:
    """configs/hardware_probe_schema.yml, checked for completeness and turned into validators."""

    def __init__(self, data: Mapping[str, Any]):
        if not isinstance(data, Mapping) or data.get("schema") != 2:
            raise SchemaError("hardware_probe_schema.yml: 'schema: 2' missing")
        try:
            f = data["file"]
            self.file_name = str(f["name"])
            self.max_bytes = int(f["max_bytes"])
            self.max_value_length = int(f["max_value_length"])
            self.key_pattern = re.compile(f["key_pattern"], re.ASCII)
            self.max_gpus = int(f["max_gpus"])
            if tuple(f["boolean_values"]) != BOOL_VALUES:
                raise SchemaError("file.boolean_values must be ON, OFF, UNKNOWN")
            self.patterns = {str(k): re.compile(v, re.ASCII) for k, v in data["patterns"].items()}
            t = data["tables"]
            self.tables: Mapping[str, Any] = t
            self.arch_aliases = {str(k).lower(): str(v) for k, v in t["arch_aliases"].items()}
            self.arch_32bit = {str(k): str(v) for k, v in t["arch_32bit_userland"].items()}
            self.x86_arches = tuple(t["x86_arches"])
            self.arm_arches = tuple(t["arm_arches"])
            self.hwcap_bits = {str(arch): {str(token): str(bit) for token, bit in bits.items()}
                               for arch, bits in (t.get("hwcap_bits") or {}).items()}
            self.os_families = {str(fam): tuple(ids) for fam, ids in t["os_families"].items()}
            self.os_family_fixed = {str(k): str(v) for k, v in t["os_family_fixed"].items()}
            self.x86_vendors = {str(k).strip(): str(v) for k, v in t["x86_vendors"].items()}
            self.arm_implementers = {str(k): str(v) for k, v in t["arm_implementers"].items()}
            self.arm_parts = {str(k): str(v) for k, v in t["arm_parts"].items()}
            self.pci_vendors = {str(k): str(v) for k, v in t["pci_vendors"].items()}
            self.soc_models_known = frozenset(str(v) for v in t["soc_models_known"])
            self.usb_rockchip_pids = {str(k): str(v) for k, v in t["usb_rockchip_pids"].items()}
            self.warning_codes = {str(k): str(v) for k, v in t["warning_codes"].items()}
            self.consumers = frozenset(str(c) for c in t["consumers"])
            self.keys: Dict[str, KeySpec] = {str(k): self._spec(str(k), v, False) for k, v in data["keys"].items()}
            self.gpu_keys: Dict[str, KeySpec] = {str(k): self._spec(str(k), v, True)
                                                 for k, v in data["gpu_keys"].items()}
        except (KeyError, TypeError, ValueError, AttributeError, re.error) as e:
            raise SchemaError(f"hardware_probe_schema.yml is incomplete: {type(e).__name__}: {e}") from e
        for name in self.keys:
            if not self.key_pattern.fullmatch(name) or _GPU_KEY.match(name):
                raise SchemaError(f"{name}: not a valid top-level key name")
        for name in ("PROBE_SCHEMA", "OS", "ARCH", "GPU_COUNT"):
            if name not in self.keys:
                raise SchemaError(f"{name} is missing")
        self.bool_keys = tuple(k for k, s in self.keys.items() if s.type == "bool")
        self.gpu_bool_keys = tuple(k for k, s in self.gpu_keys.items() if s.type == "bool")
        self.cpu_feature_keys = tuple(k for k, s in self.keys.items() if s.group in ("x86", "arm"))

    def _spec(self, name: str, raw: Mapping[str, Any], indexed: bool) -> KeySpec:
        where = f"GPU<n>_{name}" if indexed else name
        kind = raw["type"]
        if kind not in _TYPES:
            raise SchemaError(f"{where}: unknown type {kind!r}")
        for needed in ("group", "stability", "perf", "sources", "normalise", "consumers", "platforms"):
            if needed not in raw:
                raise SchemaError(f"{where}: field {needed!r} missing")
        if raw["stability"] not in _STABILITY or raw["perf"] not in _PERF:
            raise SchemaError(f"{where}: stability or perf has an unknown value")
        if raw["stability"] == "volatile" and (raw["perf"] != "none" or raw.get("required")):
            raise SchemaError(f"{where}: a volatile key cannot be required, enter perf_key or be recorded")
        if not isinstance(raw["sources"], Mapping) or not raw["sources"] or not str(raw["normalise"]).strip():
            raise SchemaError(f"{where}: sources and normalise must be filled in")
        unknown = set(raw["consumers"]) - set(self.consumers)
        if unknown:
            raise SchemaError(f"{where}: unknown consumers {sorted(unknown)}")
        if kind == "bool":
            required = True
        elif "required" in raw:
            required = bool(raw["required"])
        else:
            raise SchemaError(f"{where}: field 'required' missing")
        if raw.get("since") is not None:
            if not _SINCE.fullmatch(str(raw["since"])):
                raise SchemaError(f"{where}: since must be a version like 3.2.0")
            if kind != "bool" and required:
                raise SchemaError(f"{where}: a key added in a later probe version cannot be required")
        values = tuple(raw.get("values") or ())
        if kind == "enum" and (not values or not all(isinstance(v, str) for v in values)):
            raise SchemaError(f"{where}: enum values must be quoted strings")
        pattern = None
        if kind == "pattern":
            pattern = re.compile(raw["pattern"], re.ASCII)
        elif kind == "version":
            pattern = re.compile(raw["pattern"], re.ASCII) if "pattern" in raw else self.patterns["version"]
        item_pattern = re.compile(raw["item_pattern"], re.ASCII) if kind == "list" else None
        if kind == "int" and not (isinstance(raw.get("min"), int) and isinstance(raw.get("max"), int)):
            raise SchemaError(f"{where}: int keys need min and max")
        if kind == "text" and not isinstance(raw.get("max_length"), int):
            raise SchemaError(f"{where}: text keys need max_length")
        if kind == "list" and not isinstance(raw.get("max_items"), int):
            raise SchemaError(f"{where}: list keys need max_items")
        os_values = {str(v): tuple(oses) for v, oses in (raw.get("os_values") or {}).items()}
        if set(os_values) - set(values):
            raise SchemaError(f"{where}: os_values names values the key does not have")
        return KeySpec(
            name=name, group=str(raw["group"]), type=kind, required=required, stability=raw["stability"],
            perf=raw["perf"], indexed=indexed, writer=str(raw.get("writer", "probe")), values=values,
            pattern=pattern, item_pattern=item_pattern, minimum=raw.get("min"), maximum=raw.get("max"),
            max_length=raw.get("max_length"), max_items=raw.get("max_items"),
            only_arch=tuple(raw.get("only_arch") or ()), only_os=tuple(raw.get("only_os") or ()),
            only_gpu_vendor=tuple(raw.get("only_gpu_vendor") or ()), os_values=os_values,
            cpuinfo=tuple(raw.get("cpuinfo") or ()), cpuinfo_any=tuple(raw.get("cpuinfo_any") or ()),
            since=str(raw["since"]) if raw.get("since") is not None else None)

    # --- keys and values ---

    def lookup(self, key: str) -> Optional[Tuple[KeySpec, Optional[int]]]:
        """(spec, GPU index or None) for a key of the schema, None for an unknown key. A per-GPU key with a leading
        zero or an index of max_gpus or more raises ProfileError."""
        spec = self.keys.get(key)
        if spec is not None:
            return spec, None
        m = _GPU_KEY.match(key)
        if m and m.group(2) in self.gpu_keys:
            digits = m.group(1)
            if (len(digits) > 1 and digits[0] == "0") or int(digits) >= self.max_gpus:
                raise ProfileError(f"{key}: GPU index out of range (GPU0 to GPU{self.max_gpus - 1}, no leading zeros)")
            return self.gpu_keys[m.group(2)], int(digits)
        return None

    def check_value(self, spec: KeySpec, value: str) -> str:
        """The normalised value, or ValueError with the reason. Values are untrusted."""
        if len(value) > self.max_value_length:
            raise ValueError(f"longer than {self.max_value_length} characters")
        problem = _clean(value)
        if problem:
            raise ValueError(problem)
        kind = spec.type
        if kind == "bool":
            if value not in BOOL_VALUES:
                raise ValueError(f"{_show(value)} is not ON, OFF or UNKNOWN")
            return value
        if kind == "enum":
            if value not in spec.values:
                raise ValueError(f"{_show(value)} is not one of {', '.join(spec.values)}")
            return value
        if kind == "int":
            if not _INT.fullmatch(value):
                raise ValueError(f"{_show(value)} is not a decimal integer")
            number = int(value)
            if not spec.minimum <= number <= spec.maximum:
                raise ValueError(f"{number} is outside {spec.minimum}..{spec.maximum}")
            return value
        if kind in ("pattern", "version"):
            if not spec.pattern.fullmatch(value):
                raise ValueError(f"{_show(value)} does not match {spec.pattern.pattern}")
            return value
        if kind == "text":
            text = " ".join(value.split())
            if not text:
                raise ValueError("is empty")
            if len(text) > spec.max_length:
                raise ValueError(f"longer than {spec.max_length} characters")
            return text
        if kind == "list":
            items: List[str] = []
            for item in value.split(","):
                if not spec.item_pattern.fullmatch(item):
                    raise ValueError(f"list item {_show(item)} does not match {spec.item_pattern.pattern}")
                if item not in items:
                    items.append(item)
            if len(items) > spec.max_items:
                raise ValueError(f"more than {spec.max_items} items")
            return ",".join(items)
        if kind == "timestamp":
            if not _TIMESTAMP.fullmatch(value):
                raise ValueError(f"{_show(value)} is not a UTC time like 2026-10-11T08:15:00Z")
            try:
                datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                raise ValueError(f"{_show(value)} is not a valid date and time") from None
            return value
        raise SchemaError(f"{spec.name}: unhandled type {kind}")  # pragma: no cover - _spec rejects other types

    def canonical_order(self, keys: Iterable[str]) -> List[str]:
        """Keys in the order of the schema file; per-GPU keys follow GPU_COUNT, by index, then by schema order."""
        top = {k: i for i, k in enumerate(self.keys)}
        gpu = {k: i for i, k in enumerate(self.gpu_keys)}
        anchor = top["GPU_COUNT"]

        def rank(key: str) -> Tuple[int, int, int]:
            if key in top:
                return (top[key], 0, 0)
            m = _GPU_KEY.match(key)
            return (anchor, 1 + int(m.group(1)), gpu.get(m.group(2), 0)) if m else (len(top), 0, 0)

        return sorted(keys, key=rank)

    # --- normalisation helpers (reference implementation for the emitters and the schema 1 conversion) ---

    def normalise_arch(self, machine: str, userland_bits: Optional[int] = None) -> str:
        """uname -m (or an equivalent) -> ARCH; a 32-bit userland on a 64-bit kernel gives the 32-bit ARCH."""
        arch = self.arch_aliases.get(str(machine).strip().lower(), "other")
        if userland_bits == 32:
            arch = self.arch_32bit.get(arch, arch)
        return arch

    def os_family(self, os_name: str, os_id: Optional[str] = None, id_like: Sequence[str] = ()) -> str:
        """OS_FAMILY from OS, OS_ID and OS_ID_LIKE (tables.os_families, tables.os_family_fixed)."""
        if os_name in self.os_family_fixed:
            return self.os_family_fixed[os_name]
        for candidate in [os_id or ""] + list(id_like):
            for family, ids in self.os_families.items():
                if candidate and candidate in ids:
                    return family
        return "other"

    def cpu_vendor(self, vendor_id: Optional[str] = None, implementer: Optional[str] = None) -> str:
        """CPU_VENDOR from an x86 vendor_id or an ARM implementer (0x41)."""
        if vendor_id is not None:
            return self.x86_vendors.get(vendor_id.strip(), "other")
        if implementer is not None:
            return self.arm_implementers.get(_hex(implementer, 2) or "", "other")
        return "other"

    def arm_core_name(self, implementer: str, part: str) -> str:
        """The core name of tables.arm_parts, or "<implementer>:<part>" when the part is unknown."""
        key = f"{_hex(implementer, 2)}:{_hex(part, 3)}"
        return self.arm_parts.get(key, key)

    def features_from_flags(self, arch: str, flags: Iterable[str]) -> Dict[str, str]:
        """Every x86 and ARM SUPPORTS_* key from a /proc/cpuinfo flag list: ON/OFF for the features of `arch`, OFF
        for the features of other architectures."""
        tokens = {f.lower() for f in flags}
        result: Dict[str, str] = {}
        for key in self.cpu_feature_keys:
            spec = self.keys[key]
            if arch not in spec.only_arch:
                result[key] = "OFF"
            elif spec.cpuinfo_any:
                result[key] = "ON" if tokens & set(spec.cpuinfo_any) else "OFF"
            else:
                result[key] = "ON" if spec.cpuinfo and set(spec.cpuinfo) <= tokens else "OFF"
        return result


_HEX_INPUT = re.compile(r"^(?:0[xX])?([0-9a-fA-F]{1,8})$", re.ASCII)


def _hex(value: str, digits: int) -> Optional[str]:
    """'0x41' / '41' / '0X41' -> '0x41' with at least `digits` lower-case hex digits; None if not hex."""
    m = _HEX_INPUT.fullmatch(str(value).strip())
    return f"0x{int(m.group(1), 16):0{digits}x}" if m else None


@lru_cache(maxsize=4)
def _load_schema_cached(path: str, mtime_ns: int) -> ProbeSchema:
    with open(path, encoding="utf-8") as f:
        return ProbeSchema(yaml.safe_load(f))


def load_schema(path: Union[str, Path, None] = None) -> ProbeSchema:
    """The probe schema (configs/hardware_probe_schema.yml of the installation, not of the config folder: it is
    part of the framework, not a setting). Cached until the file changes."""
    p = Path(path or SCHEMA_PATH)
    try:
        mtime = p.stat().st_mtime_ns
    except OSError as e:
        raise SchemaError(f"cannot read {p}: {e}") from e
    return _load_schema_cached(str(p), mtime)


# --- the profile ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class HailoFacts:
    """HardwareProfile.hailo(): what the probe found of a Hailo accelerator (None: not determined or no device).

    family: hailo8 (HailoRT 4.x, branch hailo8) or hailo1x (HailoRT 5.x, branch master); arch: the Dataflow Compiler's
    --hw-arch spelling (hailo8, hailo8l, hailo10h, ...); devices, interface (pcie, usb, integrated); driver and its
    version (Linux), firmware_version, hailort_version (hailortcli = libhailort), pyhailort_version. version_mismatch:
    hailortcli refused to run because libhailort has another version. Driver, firmware and HailoRT of one machine must
    be the same release; comparing them is the preflight's and deploy's job (HAILO_PLAN 4.2), not the probe's."""
    present: bool
    family: Optional[str] = None
    arch: Optional[str] = None
    devices: Optional[int] = None
    interface: Optional[str] = None
    driver: Optional[str] = None
    driver_version: Optional[str] = None
    firmware_version: Optional[str] = None
    hailort_version: Optional[str] = None
    pyhailort_version: Optional[str] = None
    version_mismatch: bool = False

    @property
    def hailort_line(self) -> Optional[str]:
        """The HailoRT version line the board needs: "4" for family hailo8, "5" for hailo1x (None when unknown)."""
        return {"hailo8": "4", "hailo1x": "5"}.get(self.family or "")

    @property
    def model(self) -> Optional[str]:
        """NPU_MODEL's spelling of the architecture: hailo-8l, hailo-10h."""
        return "hailo-" + self.arch[len("hailo"):] if self.arch else None


class HardwareProfile:
    """A validated hardware profile. Only keys of the schema with validated, normalised values are kept; every bool
    key is present (ON, OFF or UNKNOWN), also for each GPU."""

    __slots__ = ("_values", "source_schema", "notes", "schema")

    def __init__(self, values: Mapping[str, str], *, schema: ProbeSchema, source_schema: int = 2,
                 notes: Sequence[str] = ()):
        self.schema = schema
        self.source_schema = source_schema
        self.notes: Tuple[str, ...] = tuple(notes)  # parser notes (key names and codes, never values)
        self._values = MappingProxyType({k: values[k] for k in schema.canonical_order(values)})

    def __repr__(self) -> str:
        return (f"HardwareProfile(os={self.os!r}, arch={self.arch!r}, keys={len(self._values)}, "
                f"source_schema={self.source_schema})")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, HardwareProfile) and dict(self._values) == dict(other._values)

    __hash__ = None  # type: ignore[assignment]

    # --- access ---

    @property
    def values(self) -> Mapping[str, str]:
        return self._values

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self._values.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self._values

    def flag(self, key: str) -> str:
        """ON, OFF or UNKNOWN for a bool key (UNKNOWN for a key the schema does not know as bool)."""
        value = self._values.get(key)
        return value if value in BOOL_VALUES else "UNKNOWN"

    def is_on(self, key: str) -> bool:
        return self._values.get(key) == "ON"

    def int_value(self, key: str) -> Optional[int]:
        value = self._values.get(key)
        return int(value) if value is not None and _INT.fullmatch(value) else None

    def items_of(self, key: str) -> List[str]:
        """The items of a list key (empty when absent)."""
        value = self._values.get(key)
        return value.split(",") if value else []

    @property
    def os(self) -> Optional[str]:
        return self._values.get("OS")

    @property
    def arch(self) -> Optional[str]:
        return self._values.get("ARCH")

    @property
    def libc(self) -> Optional[str]:
        return self._values.get("LIBC")

    @property
    def libc_version(self) -> Optional[str]:
        return self._values.get("LIBC_VERSION")

    @property
    def converted(self) -> bool:
        """Converted from a schema 1 file: facts may be missing or UNKNOWN."""
        return self._values.get("PROBE_CONVERTED_FROM") is not None

    @property
    def needs_reprobe(self) -> bool:
        return self.converted or "reprobe_before_build" in self.warnings

    @property
    def warnings(self) -> List[str]:
        return self.items_of("PROBE_WARNINGS")

    @property
    def soc_model_known(self) -> bool:
        """SOC_MODEL is one the framework takes decisions on (other values are display only)."""
        return self._values.get("SOC_MODEL") in self.schema.soc_models_known

    def hailo(self) -> "HailoFacts":
        """The Hailo accelerator facts of the probe (HAILO_PLAN.md section 2) in one typed value for its consumers
        (target matching, the board recommendation, the documentation links, the wizard, deploy). A profile without
        Hailo keys (no Hailo device, or a probe older than 3.1.0) gives present False and None everywhere else."""
        return HailoFacts(
            present=self.is_on("SUPPORTS_HAILO"), family=self.get("HAILO_FAMILY"), arch=self.get("HAILO_ARCH"),
            devices=self.int_value("HAILO_DEVICES"), interface=self.get("HAILO_INTERFACE"),
            driver=self.get("HAILO_DRIVER"), driver_version=self.get("HAILO_DRIVER_VERSION"),
            firmware_version=self.get("HAILO_FW_VERSION"), hailort_version=self.get("HAILORT_VERSION"),
            pyhailort_version=self.get("HAILO_PYHAILORT_VERSION"),
            version_mismatch="version_mismatch:HAILORT_VERSION" in self.warnings)

    def gpus(self) -> List[Dict[str, str]]:
        """Per GPU (GPU0, GPU1, ...) its keys without the GPU<n>_ prefix."""
        count = self.int_value("GPU_COUNT") or 0
        return [{suffix: self._values[f"GPU{n}_{suffix}"] for suffix in self.schema.gpu_keys
                 if f"GPU{n}_{suffix}" in self._values} for n in range(count)]

    # --- keys for benchmarks ---

    def perf_fields(self) -> Dict[str, Any]:
        """What perf_key() hashes: the keys marked `perf: key` and, per GPU, its `perf: key` fields. GPUs are sorted,
        so the order an emitter lists them in does not matter."""
        keys = {k: v for k, v in self._values.items() if k in self.schema.keys and self.schema.keys[k].perf == "key"}
        gpu_fields = [s for s, spec in self.schema.gpu_keys.items() if spec.perf == "key"]
        gpus = [{s: g[s] for s in gpu_fields if s in g} for g in self.gpus()]
        gpus.sort(key=lambda g: json.dumps(g, sort_keys=True))
        return {"v": PERF_KEY_VERSION, "keys": keys, "gpus": gpus}

    def perf_key(self) -> str:
        """sha256 of the canonical JSON of perf_fields(). It stays the same when only volatile, recorded-only or
        meta keys change (free RAM, driver versions, OS version, probe tool, time of the probe)."""
        canonical = json.dumps(self.perf_fields(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    def recorded(self) -> Dict[str, str]:
        """The `perf: recorded` values (stored with every benchmark run; a change marks results stale)."""
        out = {k: v for k, v in self._values.items()
               if k in self.schema.keys and self.schema.keys[k].perf == "recorded"}
        for n, gpu in enumerate(self.gpus()):
            for suffix, spec in self.schema.gpu_keys.items():
                if spec.perf == "recorded" and suffix in gpu:
                    out[f"GPU{n}_{suffix}"] = gpu[suffix]
        return out

    def stable(self) -> "HardwareProfile":
        """The profile without volatile keys (what the store keeps)."""
        kept = {}
        for key, value in self._values.items():
            found = self.schema.lookup(key)
            if found and found[0].stability != "volatile":
                kept[key] = value
        return HardwareProfile(kept, schema=self.schema, source_schema=self.source_schema, notes=self.notes)

    # --- views ---

    def as_dict(self) -> Dict[str, str]:
        """The JSON view: the same keys and string values as the file, PROBE_SCHEMA included."""
        return dict(self._values)

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, ensure_ascii=False)

    def to_text(self) -> str:
        """The profile as a schema 2 file (canonical key order, LF line ends)."""
        lines = ["# Hardware profile, schema 2 (configs/hardware_probe_schema.yml)"]
        lines += [f"{k}={v}" for k, v in self._values.items()]
        return "\n".join(lines) + "\n"


# --- parsing -------------------------------------------------------------------------------------------------------

_Pair = Tuple[int, str, bool, str]  # (line number, key, had "=", value)


def _decode(data: Union[str, bytes], schema: ProbeSchema) -> Tuple[str, bool]:
    """(text, strict UTF-8). Schema 2 must be UTF-8; schema 1 files from PowerShell may be UTF-16 or ANSI."""
    if isinstance(data, str):
        if len(data) > schema.max_bytes:
            raise ProfileError(f"larger than {schema.max_bytes} bytes")
        return (data[1:] if data.startswith("\ufeff") else data), True
    if len(data) > schema.max_bytes:
        raise ProfileError(f"larger than {schema.max_bytes} bytes")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16"), False
        except UnicodeDecodeError as e:
            raise ProfileError(f"not valid UTF-16: {e.reason}") from None
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), False


def _scan(text: str) -> Tuple[Optional[str], List[_Pair]]:
    """(the PROBE_SCHEMA value of the first line that is not a comment, the KEY=VALUE lines)."""
    pairs: List[_Pair] = []
    version: Optional[str] = None
    for number, line in enumerate(text.split("\n"), 1):
        if line.endswith("\r"):
            line = line[:-1]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not pairs and key == "PROBE_SCHEMA":
            version = value.strip(" \t")
        pairs.append((number, key, bool(sep), value.strip(" \t")))
    return version, pairs


def parse_profile(data: Union[str, bytes], *, schema: Optional[ProbeSchema] = None) -> HardwareProfile:
    """Parses a probe file: schema 2 strictly, schema 1 (no PROBE_SCHEMA line) through the conversion.
    Raises ProfileError with the line and the reason."""
    schema = schema or load_schema()
    text, utf8 = _decode(data, schema)
    version, pairs = _scan(text)
    if version is None:
        later = next((n for n, key, _, _ in pairs if key.strip() == "PROBE_SCHEMA"), None)
        if later is not None:
            raise ProfileError(f"line {later}: PROBE_SCHEMA must be the first line that is not a comment, written "
                               "exactly as PROBE_SCHEMA=2")
        return _from_schema1(pairs, schema)
    if version == "1":
        return _from_schema1(pairs[1:], schema)
    if version != "2":
        raise ProfileError(f"PROBE_SCHEMA={_show(version)}: this framework reads schema 1 and 2; "
                           "update the framework or use its own probe")
    if not utf8:
        raise ProfileError("a schema 2 file must be UTF-8")
    return _from_schema2(pairs, schema)


_JSON_STRING = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)
_MAX_JSON_DEPTH = 4  # the JSON view is one flat object; anything nested deeper is no probe file


def _nested_too_deeply(text: Union[str, bytes]) -> bool:
    """True when brackets outside JSON strings nest deeper than _MAX_JSON_DEPTH. Checked before json.loads, so a
    hostile file never builds deep structures and every Python version gives the same answer (3.14's decoder no
    longer raises RecursionError for 60 000 levels)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    depth = 0
    for bracket in re.findall(r"[\[\]{}]", _JSON_STRING.sub("", text)):
        depth += 1 if bracket in "[{" else -1
        if depth > _MAX_JSON_DEPTH:
            return True
    return False


def parse_profile_json(text: Union[str, bytes], *, schema: Optional[ProbeSchema] = None) -> HardwareProfile:
    """Parses the JSON view (hardware_probe.py --json): one object of string values, schema 2 rules."""
    schema = schema or load_schema()
    if len(text) > schema.max_bytes:
        raise ProfileError(f"larger than {schema.max_bytes} bytes")
    if _nested_too_deeply(text):
        raise ProfileError("not valid JSON: nested too deeply")

    def unique(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
        seen: Dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                raise ProfileError(f"{_show(key)} appears twice")
            seen[key] = value
        return seen

    try:
        data = json.loads(text, object_pairs_hook=unique)
    except RecursionError:  # deep nesting far below the size limit; callers expect ProfileError for bad files
        raise ProfileError("not valid JSON: nested too deeply") from None
    except ValueError as e:
        if isinstance(e, ProfileError):
            raise
        raise ProfileError(f"not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ProfileError("the JSON view must be one object")
    if data.get("PROBE_SCHEMA") != "2":
        raise ProfileError('the JSON view needs "PROBE_SCHEMA": "2"')
    pairs: List[_Pair] = []
    for number, (key, value) in enumerate(data.items(), 1):
        if not isinstance(value, str):
            raise ProfileError(f"{_show(key)}: values are strings")
        pairs.append((number, key, True, value))
    return _from_schema2(pairs, schema, where="entry")


def read_profile(path: Union[str, Path], *, schema: Optional[ProbeSchema] = None) -> HardwareProfile:
    """Reads and parses a probe file (KEY=VALUE, or the JSON view if it starts with "{")."""
    schema = schema or load_schema()
    p = Path(path)
    try:
        with open(p, "rb") as f:
            data = f.read(schema.max_bytes + 1)
    except OSError as e:
        raise ProfileError(f"cannot read {p.name}: {e.strerror or e}") from None
    try:
        if data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{"):
            return parse_profile_json(data, schema=schema)
        return parse_profile(data, schema=schema)
    except ProfileError as e:
        raise ProfileError(f"{p.name}: {e}") from None


def _from_schema2(pairs: Sequence[_Pair], schema: ProbeSchema, where: str = "line") -> HardwareProfile:
    values: Dict[str, str] = {}
    first_seen: Dict[str, int] = {}
    unknown: List[str] = []
    for number, key, has_sep, value in pairs:
        at = f"{where} {number}"
        if not has_sep:
            raise ProfileError(f"{at}: not KEY=VALUE")
        if not schema.key_pattern.fullmatch(key):
            raise ProfileError(f"{at}: {_show(key)} is not a valid key")
        if key in first_seen:
            raise ProfileError(f"{at}: {key} appears twice (first in {where} {first_seen[key]})")
        first_seen[key] = number
        if not value:
            raise ProfileError(f"{at}: {key} is empty (a value that could not be determined is left out)")
        if len(value) > schema.max_value_length:
            raise ProfileError(f"{at}: {key} is longer than {schema.max_value_length} characters")
        try:
            found = schema.lookup(key)
        except ProfileError as e:
            raise ProfileError(f"{at}: {e}") from None
        if found is None:
            unknown.append(key)
            continue
        try:
            values[key] = schema.check_value(found[0], value)
        except ValueError as e:
            raise ProfileError(f"{at}: {key}: {e}") from None
    added = _fill_later_booleans(values, schema)
    problems = _violations(values, schema, require=values.get("PROBE_CONVERTED_FROM") is None)
    if problems:
        shown = "; ".join(f"{key}: {reason}" for key, reason, _kind in problems[:8])
        more = f" (and {len(problems) - 8} more)" if len(problems) > 8 else ""
        raise ProfileError(shown + more)
    notes = [f"unknown_key:{k}" for k in unknown] + [f"probe_again:{k}" for k in added]
    source = 1 if values.get("PROBE_CONVERTED_FROM") else 2
    return HardwareProfile(values, schema=schema, source_schema=source, notes=notes)


def _version_tuple(text: Optional[str]) -> Tuple[int, ...]:
    return tuple(int(part) for part in text.split(".")) if text and _SINCE.fullmatch(text) else ()


def _fill_later_booleans(values: Dict[str, str], schema: ProbeSchema) -> List[str]:
    """A file of an older probe lacks the ON/OFF keys a later version added (`since` in the schema): each one is
    filled in, OFF outside its ARCH or OS (nothing to learn there), else UNKNOWN (never guessed). Returns the keys
    filled with UNKNOWN: probing the machine again tells them. A file of that version or newer must have them."""
    written = _version_tuple(values.get("PROBE_TOOL_VERSION"))
    os_name, arch = values.get("OS"), values.get("ARCH")
    unknown: List[str] = []
    for key, spec in schema.keys.items():
        if spec.type != "bool" or spec.since is None or key in values:
            continue
        if written and written >= _version_tuple(spec.since):
            continue  # reported as missing by _violations
        outside = (spec.only_arch and arch not in spec.only_arch) or (spec.only_os and os_name not in spec.only_os)
        values[key] = "OFF" if outside else "UNKNOWN"
        if not outside:
            unknown.append(key)
    return unknown


def _violations(values: Mapping[str, str], schema: ProbeSchema, *, require: bool) -> List[Tuple[str, str, str]]:
    """Rules across keys as (key, reason, kind), kind one of missing, gpu_index, outside, os_value: required keys,
    booleans, GPU indexes, only_arch / only_os / only_gpu_vendor, os_values. `require` False (a converted schema 1
    file) skips the required non-bool keys."""
    problems: List[Tuple[str, str, str]] = []
    for key, spec in schema.keys.items():
        if key not in values and (spec.type == "bool" or (require and spec.required)):
            reason = "missing (booleans are always written)" if spec.type == "bool" else "missing"
            problems.append((key, reason, "missing"))
    count_text = values.get("GPU_COUNT")
    count = int(count_text) if count_text is not None else None
    for key in values:
        found = schema.lookup(key)
        if found and found[1] is not None and (count is None or found[1] >= count):
            problems.append((key, f"GPU index not below GPU_COUNT ({count_text or 'missing'})", "gpu_index"))
    for n in range(count or 0):
        for suffix, spec in schema.gpu_keys.items():
            if spec.required and f"GPU{n}_{suffix}" not in values:
                problems.append((f"GPU{n}_{suffix}", "missing (written for every GPU)", "missing"))
    os_name, arch = values.get("OS"), values.get("ARCH")
    for key, value in values.items():
        found = schema.lookup(key)
        if not found:
            continue
        spec, index = found
        outside = None
        if spec.only_arch and arch and arch not in spec.only_arch:
            outside = f"ARCH {arch}"
        elif spec.only_os and os_name and os_name not in spec.only_os:
            outside = f"OS {os_name}"
        elif spec.only_gpu_vendor and index is not None:
            vendor = values.get(f"GPU{index}_VENDOR")
            if vendor and vendor not in spec.only_gpu_vendor:
                outside = f"GPU{index}_VENDOR {vendor}"
        if outside and (spec.type != "bool" or value == "ON"):
            reason = f"{'cannot be ON' if spec.type == 'bool' else 'does not exist'} with {outside}"
            problems.append((key, reason, "outside"))
        allowed = spec.os_values.get(value)
        if allowed and os_name and os_name not in allowed:
            problems.append((key, f"{value} does not exist with OS {os_name}", "os_value"))
    return problems


# --- schema 1 ------------------------------------------------------------------------------------------------------

_SCHEMA1_MARKERS = {"PLATFORM", "ARCH", "CPU_ARCH_TYPE", "CPU_MODEL"}
_NOT_A_VALUE = {"", "none", "unknown", "missing", "n/a", "discrete_cpu", "none detected"}
_SCHEMA1_BOOLS = {"ON": "ON", "TRUE": "ON", "YES": "ON", "1": "ON", "OFF": "OFF", "FALSE": "OFF", "NO": "OFF",
                  "0": "OFF"}
# Schema 1 keys whose meaning carries over unchanged.
_SCHEMA1_SAME = ("KERNEL", "OS_PRETTY_NAME", "CPU_MODEL", "CPU_CORES_LOGICAL", "CPU_CORES_PHYSICAL", "RAM_SPEED_MTS",
                 "SUPPORTS_CUDA", "SUPPORTS_INTEL_XPU", "SUPPORTS_RKNN", "SUPPORTS_HAILO", "SUPPORTS_METIS",
                 "SUPPORTS_MX3", "SUPPORTS_INTEL_NPU")


class _Converter:
    """Maps one schema 1 file to schema 2 keys. Values are validated one by one; what fails is dropped."""

    def __init__(self, raw: Dict[str, str], schema: ProbeSchema, notes: List[str]):
        self.raw, self.schema, self.notes = raw, schema, notes
        self.out: Dict[str, str] = {}
        self.used: set = set()

    def take(self, key: str) -> Optional[str]:
        """A schema 1 value (marked as used), or None when absent or a placeholder such as "Unknown"."""
        if key not in self.raw:
            return None
        self.used.add(key)
        value = self.raw[key]
        return None if value.strip().lower() in _NOT_A_VALUE or value.lower().startswith("unknown") else value

    def put(self, key: str, value: Optional[str]) -> bool:
        if value is None:
            return False
        found = self.schema.lookup(key)
        if found is None:  # pragma: no cover - the converter only writes keys of the schema
            raise SchemaError(f"converter writes unknown key {key}")
        spec = found[0]
        if spec.type == "bool":
            value = _SCHEMA1_BOOLS.get(value.strip().upper(), value)
        try:
            self.out[key] = self.schema.check_value(spec, value)
            return True
        except ValueError:
            self.notes.append(f"invalid:{key}")
            return False

    def run(self, ps1: bool) -> Dict[str, str]:
        s, take, put = self.schema, self.take, self.put
        put("PROBE_SCHEMA", "2")
        put("PROBE_CONVERTED_FROM", "1")
        put("PROBE_TOOL", "ps1" if ps1 else "sh")
        platform_name = (take("PLATFORM") or "").strip().lower()
        if platform_name in s.keys["OS"].values:
            put("OS", platform_name)
        arch_raw = take("ARCH") or take("CPU_ARCH_TYPE")
        if arch_raw is not None:
            arch = s.normalise_arch(arch_raw)
            if arch != "other":
                put("ARCH", arch)
        for key in _SCHEMA1_SAME:
            put(key, take(key))
        os_version = take("OS_VERSION")
        if os_version is not None:
            m = re.match(r"^(\d{1,3}\.\d{1,3}\.\d{1,6})", os_version.strip(), re.ASCII)
            if m:
                put("OS_VERSION_ID", m.group(1))
                put("KERNEL", m.group(1))
        if self.out.get("OS") == "windows":
            for key, value in (("OS_ID", "windows"), ("OS_FAMILY", "windows"), ("LIBC", "ucrt"),
                               ("INIT_SYSTEM", "scm")):
                put(key, value)
        vendor_id = take("CPU_VENDOR_ID")
        if vendor_id is not None:
            put("CPU_VENDOR", s.cpu_vendor(vendor_id=vendor_id))
        self._cpu_features(ps1)
        self._memory(ps1)
        self._soc_and_npu()
        self._gpu()
        for old, new in (("DOCKER_VERSION", "TOOL_DOCKER"), ("PYTHON_VERSION", "TOOL_PYTHON")):
            value = take(old)
            m = _DOTTED.search(value or "")
            if m:
                put(new, m.group(1))
        return self.out

    def _cpu_features(self, ps1: bool) -> None:
        s, arch = self.schema, self.out.get("ARCH")
        flags_raw = self.take("CPU_FLAGS_ALL")
        for key in s.cpu_feature_keys + ("SUPPORTS_FP16",):
            self.used.add(key)  # never taken over as written (see below)
        tokens = [t for t in (flags_raw or "").lower().split() if s.keys["CPU_FLAGS_ALL"].item_pattern.fullmatch(t)]
        if tokens and not ps1:
            self.put("CPU_FLAGS_ALL", ",".join(dict.fromkeys(tokens)))
            self.put("CPU_FLAGS_SOURCE", "proc-cpuinfo")
        if ps1:
            # The .ps1 (v2.5) asks IsProcessorFeaturePresent with wrong constants: none of its answers is usable.
            for key in s.cpu_feature_keys:
                self.out[key] = "UNKNOWN"
            self.notes.append("cpu_features_unknown")
        elif tokens and arch:
            self.out.update(s.features_from_flags(arch, tokens))
        # Otherwise the bools stay unset and become UNKNOWN (or OFF outside their ARCH): without the flag list the
        # .sh wrote OFF for everything.

    def _memory(self, ps1: bool) -> None:
        ram = self.take("RAM_TOTAL_MB")
        if ps1:
            if ram is not None:
                self.notes.append("dropped:RAM_TOTAL_MB")  # the sum of the SMBIOS slots, not usable memory
        else:
            self.put("RAM_TOTAL_MB", ram)
        self.put("SWAP_TOTAL_MB", self.take("RAM_SWAP_MB"))
        ram_type = self.take("RAM_TYPE")
        if ram_type is not None and ram_type.upper() in self.schema.keys["RAM_TYPE"].values:
            self.put("RAM_TYPE", ram_type.upper())

    def _soc_and_npu(self) -> None:
        take, put = self.take, self.put
        for key in ("SOC_VENDOR", "SOC_MODEL"):
            value = take(key)
            if value is not None and self.schema.keys[key].pattern.fullmatch(value.strip().lower()):
                put(key, value.strip().lower())
        vendor = take("NPU_VENDOR")
        vendor_word = re.match(r"[a-z]+", (vendor or "").lower())
        npu_vendor = vendor_word.group(0) if vendor_word else None
        model = take("NPU_MODEL")
        ident = re.sub(r"[^a-z0-9._-]+", "-", (model or "").lower()).strip("-") or None
        device_id = (take("NPU_DEVICE_ID") or "").strip()
        vendor_id = (take("NPU_VENDOR_ID") or "").strip().lower()
        take("NPU_MODE")
        take("NPU_STATUS")
        # The .sh writes NPU_MODEL=RKxxxx and NPU_DEVICE_ID=SoC_RKxxxx only when /dev/rknpu exists, from
        # /proc/device-tree/compatible: that is this machine's SoC. A board attached over USB is not.
        soc = re.fullmatch(r"SoC_(RK\d{4}[A-Z]?)", device_id, re.ASCII)
        soc_model = soc.group(1).lower() if soc else None
        if not soc_model and npu_vendor == "rockchip" and re.fullmatch(r"rk\d{4}[a-z]?", ident or "", re.ASCII):
            soc_model = ident
        if soc_model:
            put("SOC_VENDOR", self.out.get("SOC_VENDOR") or "rockchip")
            put("SOC_MODEL", self.out.get("SOC_MODEL") or soc_model)
            put("NPU_DEVICE", "/dev/rknpu")
        if npu_vendor in self.schema.keys["NPU_VENDOR"].values and (npu_vendor != "rockchip" or soc_model):
            put("NPU_VENDOR", npu_vendor)
            put("NPU_MODEL", ident)
        pid = device_id.lower()
        if _HEX4.fullmatch(pid) and (vendor_id == "0x2207" or (npu_vendor == "rockchip" and not vendor_id)):
            put("USB_ROCKCHIP_PID", pid)
            put("USB_ROCKCHIP_MODE", self.schema.usb_rockchip_pids.get(pid, "other"))
        node = take("DEVICE_NODE")
        if node is not None and "NPU_DEVICE" not in self.out:
            put("NPU_DEVICE", node)

    def _gpu(self) -> None:
        take, s = self.take, self.schema
        name_vendor = (take("GPU_VENDOR") or "").strip().lower()
        vendor_id = (take("GPU_VENDOR_ID") or "").strip().lower()
        device_id = (take("GPU_DEVICE_ID") or "").strip().lower()
        vendors = s.gpu_keys["VENDOR"].values
        vendor = name_vendor if name_vendor in vendors else s.pci_vendors.get(vendor_id)
        model, vram, driver = take("GPU_MODEL"), take("GPU_VRAM_MB"), take("GPU_DRIVER_VERSION")
        if vendor is None:
            return
        self.out["GPU_COUNT"] = "1"
        self.put("GPU0_VENDOR", vendor)
        if _HEX4.fullmatch(vendor_id) and _HEX4.fullmatch(device_id):
            self.put("GPU0_PCI_ID", f"{vendor_id}:{device_id}")
        self.put("GPU0_NAME", model)
        self.put("GPU0_VRAM_MB", vram)
        self.put("GPU0_DRIVER_VERSION", driver)
        self.notes.append("gpu_list_incomplete")  # schema 1 described at most one GPU and no AMD GPU at all


def _from_schema1(pairs: Sequence[_Pair], schema: ProbeSchema) -> HardwareProfile:
    raw: Dict[str, str] = {}
    ambiguous: set = set()
    notes: List[str] = ["converted_from_schema_1"]
    for _number, key, has_sep, value in pairs:
        key, value = key.strip(), value.replace("\t", " ")
        if not has_sep or not _SCHEMA1_KEY.fullmatch(key) or len(value) > schema.max_value_length or _clean(value):
            continue
        if key in raw and raw[key] != value:
            ambiguous.add(key)
        raw.setdefault(key, value)
    if not _SCHEMA1_MARKERS & raw.keys():
        raise ProfileError("not a hardware probe file (no PROBE_SCHEMA line and none of the keys of today's probe "
                           "scripts)")
    for key in sorted(ambiguous):
        del raw[key]
        notes.append(f"ambiguous:{key}")
    ps1 = raw.get("PLATFORM", "").strip().lower() == "windows" or "CPU_ARCH_TYPE" in raw
    conv = _Converter(raw, schema, notes)
    out = conv.run(ps1)

    dropped = sorted(set(raw) - conv.used)
    slots = [k for k in dropped if k.startswith("RAM_SLOT_")]
    notes += [f"dropped:{k}" for k in dropped if k not in slots] + (["dropped:RAM_SLOT_*"] if slots else [])

    # Booleans are always present: outside their ARCH/OS they are OFF, otherwise UNKNOWN when the file did not say.
    os_name, arch = out.get("OS"), out.get("ARCH")
    for key in schema.bool_keys:
        if key in out:
            continue
        spec = schema.keys[key]
        off = (spec.only_arch and arch and arch not in spec.only_arch) or (
            spec.only_os and os_name and os_name not in spec.only_os)
        out[key] = "OFF" if off else "UNKNOWN"
    for n in range(int(out.get("GPU_COUNT", "0"))):
        for suffix in schema.gpu_bool_keys:
            out.setdefault(f"GPU{n}_{suffix}", "UNKNOWN")
    # What still breaks a rule across keys (a USB board's SUPPORTS_RKNN=ON on Windows, ...) is fixed: a bool outside
    # its ARCH/OS becomes OFF, another bool UNKNOWN, any other key is dropped.
    for _round in range(3):
        problems = [p for p in _violations(out, schema, require=False) if p[0] in out]
        if not problems:
            break
        for key, _reason, kind in problems:
            if key not in out:
                continue
            notes.append(f"inconsistent:{key}")
            if schema.lookup(key)[0].type == "bool":
                out[key] = "OFF" if kind == "outside" else "UNKNOWN"
            else:
                del out[key]
    else:  # pragma: no cover - every fix removes the key it is about
        raise ProfileError("the schema 1 file could not be converted consistently")
    warnings = ["converted_from_schema_1", "reprobe_before_build"]
    if "cpu_features_unknown" in notes:
        warnings.append("cpu_features_unknown")
    out["PROBE_WARNINGS"] = ",".join(warnings)
    return HardwareProfile(out, schema=schema, source_schema=1, notes=notes)


# --- the store -----------------------------------------------------------------------------------------------------

DEVICE_ID = re.compile(r"^[0-9a-f]{16}$", re.ASCII)
_FINGERPRINT = FINGERPRINT   # the one fingerprint pattern (orchestrator/remote/trust.py)
_ADDRESS = re.compile(r"^(?:\[[0-9A-Fa-f:.]{2,45}\](?::\d{1,5})?|[0-9A-Fa-f:]{2,45}|"
                      r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?)$", re.ASCII)
_META_MAX_BYTES = 16384


def default_state_dir() -> Path:
    """The per-user framework folder (LLM_FRAMEWORK_HOME, default ~/.llm-framework), as ConfigManager uses it."""
    return Path(os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework")


# Device ID: the first 16 hex characters of the sha256 of the device's SSH host key fingerprint ("SHA256:..." as
# orchestrator.utils.ssh_trust.fingerprint prints it). Stable across IP changes, no host name. The one implementation
# is orchestrator/remote/trust.py device_id_for (the trust store derives it too).
device_id_for_host_key = device_id_for


def new_device_id() -> str:
    """A random device ID for a profile imported without SSH."""
    return secrets.token_hex(8)


def _check_device_id(device_id: str) -> str:
    if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
        raise ValueError("a device ID is 16 lower-case hex characters")
    return device_id


def _check_label(label: str) -> str:
    text = " ".join(str(label).split())
    if not text or len(text) > 64 or _clean(text):
        raise ValueError("a label is 1 to 64 printable characters")
    return text


def _check_address(address: str) -> str:
    if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
        raise ValueError("an address is a host name, an IPv4 or IPv6 address, optionally with :port")
    return address


class ProfileStore:
    """Hardware profiles of this user: <state dir>/hardware/local.txt (this machine) and
    <state dir>/hardware/devices/<device id>.txt plus .json (label and SSH address) for other machines, and
    <device id>.deploy.json with the device's deploy settings (orchestrator/runtime/device/settings.py).

    Files are written atomically with mode 0600 in folders with mode 0700. Volatile keys are never stored, and stored
    files are parsed with the same validation as probe files when they are read back."""

    def __init__(self, state_dir: Union[str, Path, None] = None, *, schema: Optional[ProbeSchema] = None):
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self.root = self.state_dir / "hardware"
        self.devices_dir = self.root / "devices"
        self._schema = schema

    @property
    def schema(self) -> ProbeSchema:
        return self._schema or load_schema()

    def _prepare(self, folder: Path) -> None:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            for p in (self.root, folder):
                try:
                    p.chmod(0o700)
                except OSError:
                    pass

    def local_path(self) -> Path:
        return self.root / "local.txt"

    def device_path(self, device_id: str) -> Path:
        return self.devices_dir / f"{_check_device_id(device_id)}.txt"

    def _meta_path(self, device_id: str) -> Path:
        return self.devices_dir / f"{_check_device_id(device_id)}.json"

    def _write(self, path: Path, profile: HardwareProfile) -> Path:
        if not isinstance(profile, HardwareProfile):
            raise TypeError("expected a HardwareProfile")
        stable = profile.stable()
        text = stable.to_text()
        # The store only ever holds files its own parser accepts, and exactly the profile it was given: a value with
        # a line break (a HardwareProfile built in code, not parsed) could otherwise add a key of its own.
        if parse_profile(text, schema=self.schema) != stable:
            raise ProfileError("the profile does not survive a round trip through the probe file format; not stored")
        self._prepare(path.parent)
        write_private(path, text)
        return path

    def _read(self, path: Path) -> Optional[HardwareProfile]:
        if not path.is_file():
            return None
        return read_profile(path, schema=self.schema)

    # --- this machine ---

    def save_local(self, profile: HardwareProfile) -> Path:
        return self._write(self.local_path(), profile)

    def load_local(self) -> Optional[HardwareProfile]:
        """This machine's stored profile, None if there is none; ProfileError if the file is damaged."""
        return self._read(self.local_path())

    # --- other machines ---

    def save_device(self, device_id: str, profile: HardwareProfile, *, label: Optional[str] = None,
                    address: Optional[str] = None) -> Path:
        """Stores a device's profile; label and address update the .json (None keeps the stored value)."""
        target = self.device_path(device_id)
        meta = self.device_info(device_id)
        if label is not None:
            meta["label"] = _check_label(label)
        if address is not None:
            meta["address"] = _check_address(address)
        path = self._write(target, profile)
        meta["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_private(self._meta_path(device_id), json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        return path

    def set_address(self, device_id: str, address: str) -> bool:
        """The stored board's SSH address (host[:port]) after the person changed it (Q5, deploy devices edit); its probe
        and label stay. False when no probe of this device is stored."""
        if not self.device_path(device_id).is_file():
            return False
        meta = self.device_info(device_id)
        meta["address"] = _check_address(address)
        meta["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_private(self._meta_path(device_id), json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
        return True

    def load_device(self, device_id: str) -> Optional[HardwareProfile]:
        return self._read(self.device_path(device_id))

    def device_info(self, device_id: str) -> Dict[str, str]:
        """The validated fields of devices/<id>.json (label, address, updated_at); invalid fields are left out."""
        path = self._meta_path(device_id)
        try:
            with open(path, "rb") as f:
                raw = f.read(_META_MAX_BYTES + 1)
            data = json.loads(raw) if len(raw) <= _META_MAX_BYTES else None
        except (OSError, ValueError, RecursionError):
            return {}
        if not isinstance(data, dict):
            return {}
        info: Dict[str, str] = {}
        for key, check in (("label", _check_label), ("address", _check_address)):
            try:
                if isinstance(data.get(key), str):
                    info[key] = check(data[key])
            except ValueError:
                pass
        stamp = data.get("updated_at")
        if isinstance(stamp, str) and _TIMESTAMP.fullmatch(stamp):
            info["updated_at"] = stamp
        return info

    def list_devices(self) -> List[str]:
        """IDs of the stored devices, sorted."""
        if not self.devices_dir.is_dir():
            return []
        return sorted(p.stem for p in self.devices_dir.glob("*.txt") if DEVICE_ID.fullmatch(p.stem))

    # --- deploy settings of a device (decision D7; checked by orchestrator/runtime/device/settings.py) ---

    def _deploy_path(self, device_id: str) -> Path:
        return self.devices_dir / f"{_check_device_id(device_id)}.deploy.json"

    def deploy_settings_path(self, device_id: str) -> Path:
        """Where the device's deploy settings are stored (the person sees it before damaged settings are set again)."""
        return self._deploy_path(device_id)

    def load_deploy_settings(self, device_id: str) -> Dict[str, Any]:
        """The device's deploy settings as stored ({} when there are none). ValueError when the file is damaged:
        settings decide what a deployment does, so a damaged file is never read as the defaults."""
        path = self._deploy_path(device_id)
        try:
            with open(path, "rb") as f:
                raw = f.read(_META_MAX_BYTES + 1)
        except FileNotFoundError:
            return {}
        except OSError as e:
            raise ValueError(f"the deploy settings of device {device_id} cannot be read: {e.strerror or e}") from None
        try:
            data = json.loads(raw) if len(raw) <= _META_MAX_BYTES else None
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict):
            raise ValueError(f"the deploy settings of device {device_id} are damaged ({path}); set them again")
        return data

    def save_deploy_settings(self, device_id: str, settings: Mapping[str, Any]) -> Path:
        """Stores the device's deploy settings (a private file next to its profile)."""
        path = self._deploy_path(device_id)
        text = json.dumps(dict(settings), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        if len(text.encode("ascii")) > _META_MAX_BYTES:
            raise ValueError("the deploy settings are too large")
        self._prepare(path.parent)
        write_private(path, text)
        return path

    def device_files(self, device_id: str) -> Dict[str, Path]:
        """Where the stored board's files are, whether they exist or not: its probe ("probe"), the probe's label and
        address ("probe_info") and its deploy settings ("deploy_settings"). The person's change of a board's host key
        moves them to the board's new device ID (runtime/device/board_move.py, P6)."""
        return {"probe": self.device_path(device_id), "probe_info": self._meta_path(device_id),
                "deploy_settings": self._deploy_path(device_id)}

    def remove_device(self, device_id: str) -> bool:
        removed = False
        for path in (self.device_path(device_id), self._meta_path(device_id), self._deploy_path(device_id)):
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
        return removed
