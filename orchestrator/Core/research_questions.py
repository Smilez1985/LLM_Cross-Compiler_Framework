#!/usr/bin/env python3
"""
The search questions a hardware probe gives (DESIGN, Ditto, the row "Ditto's research for the archive starts from the
hardware probe", step 1; maintainer's decision of 2026-11-23, round 117).

Fixed templates per probe key turn what the probe found into questions for Ditto's web search: the SoC, the NPU, the
board with its distribution, the GPU. Only the keys of ALLOWED_KEYS are read, so a private value of the probe (the host
name, an address, a MAC, a serial number, a user name) can never be part of a question, and every value used must match
a strict pattern (letters, digits, space and . _ - +, at most VALUE_MAX characters). Ditto may add questions of its
own; each question still goes through the web search as it is (the person is asked with the words that leave this
computer, the privacy filter).
"""

import re
from typing import Callable, List, Mapping, Tuple

ALLOWED_KEYS = ("SOC_VENDOR", "SOC_MODEL", "BOARD_MODEL", "NPU_VENDOR", "NPU_MODEL", "OS_ID", "OS_VERSION_ID",
                "GPU0_VENDOR", "GPU0_NAME", "ARCH")
VALUE_MAX = 64
QUESTIONS_MAX = 8
_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}$")
_UNKNOWN = {"", "unknown", "none", "n/a", "na", "other", "generic", "off", "default string", "to be filled by o.e.m."}


def clean(values: Mapping[str, object]) -> dict:
    """The probe's values a question may use: only ALLOWED_KEYS, only values of the strict pattern, none that only
    says "unknown"."""
    out = {}
    for key in ALLOWED_KEYS:
        value = str(values.get(key) or "").strip(" ")
        # the raw value is checked (a line break or a tab is never folded into a space first), then repeated spaces go
        if value.lower() in _UNKNOWN or len(value) > VALUE_MAX or not _VALUE.fullmatch(value):
            continue
        out[key] = re.sub(r" {2,}", " ", value)
    return out


# What a GPU question asks, by the probe's GPU0_VENDOR (the name carries the vendor already); else Vulkan.
GPU_TOPIC = {"nvidia": "compute capability", "amd": "ROCm support", "intel": "SYCL support"}

# (the keys a template needs, the template): in this order, so the SoC and the NPU come first
TEMPLATES: Tuple[Tuple[Tuple[str, ...], Callable[[dict], str]], ...] = (
    (("SOC_MODEL",), lambda v: f"{v.get('SOC_VENDOR', '')} {v['SOC_MODEL']} datasheet"),
    (("SOC_MODEL",), lambda v: f"{v['SOC_MODEL']} llama.cpp performance"),
    (("NPU_MODEL",), lambda v: f"{v.get('NPU_VENDOR', '')} {v['NPU_MODEL']} NPU INT8 quantization toolkit"),
    (("SOC_MODEL", "NPU_VENDOR"), lambda v: f"{v['SOC_MODEL']} NPU LLM support"),
    (("BOARD_MODEL", "OS_ID"), lambda v: f"{v['BOARD_MODEL']} {v['OS_ID']} {v.get('OS_VERSION_ID', '')} kernel"),
    (("BOARD_MODEL",), lambda v: f"{v['BOARD_MODEL']} specifications"),
    (("GPU0_NAME",), lambda v: f"{v['GPU0_NAME']} {GPU_TOPIC.get(v.get('GPU0_VENDOR', '').lower(), 'Vulkan support')}"),
)


def research_questions(values: Mapping[str, object]) -> List[str]:
    """The search questions of a probe's values, in the order of TEMPLATES, without repeats, at most QUESTIONS_MAX;
    [] when the probe names nothing a template can use."""
    usable = clean(values)
    questions: List[str] = []
    for needs, template in TEMPLATES:
        if all(key in usable for key in needs):
            question = " ".join(template(usable).split())
            if question.lower() not in (q.lower() for q in questions):
                questions.append(question)
    return questions[:QUESTIONS_MAX]


__all__ = ["ALLOWED_KEYS", "QUESTIONS_MAX", "TEMPLATES", "VALUE_MAX", "clean", "research_questions"]
