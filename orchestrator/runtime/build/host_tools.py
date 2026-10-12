#!/usr/bin/env python3
"""
The conversion tools in a target's toolchain image, built from the recipe (design 4.3, decisions D13 and D21).

A target's Dockerfile (targets/Rockchip/dockerfile, targets/_template/Dockerfile and what is generated from it)
compiles llama.cpp's conversion tools while the image is built: llama-quantize, llama-imatrix, llama-perplexity,
llama-completion and llama-bench. The image runs on the build host, so the tools are planned for the build host: the
recipe's plan for purpose host-tools from the build host's hardware probe, with its CPU features written out as
options (or all CPU variants when the probe could not tell them). The image build gets three arguments:

- LLAMA_CPP_TAG and LLAMA_CPP_COMMIT: the pin (configs/llama_runtime.yml, llama_cpp.source). The Dockerfile fetches
  the commit (a tag can move, a commit cannot) and stops unless `git rev-parse HEAD` gives it back;
- LLAMA_CMAKE_ARGS: the plan's CMake options as -DNAME=VALUE words. The Dockerfile checks every word and puts the
  options every build has after them (no download-capable option, LLAMA_SUBPROCESS=OFF,
  FETCHCONTENT_FULLY_DISCONNECTED=ON, the build number and commit), so those always win.

Without a usable probe the argument is left out and the Dockerfile keeps its default: the fixed x86-64 AVX2 baseline
these tools always had, written out. Options proposed by an AI or typed into the module wizard never reach this
(decision D20): only the recipe's plan does.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from orchestrator.runtime.build.recipe import X86_FEATURES, BuildPlan, PlanError, ToolchainInfo, plan_build
from orchestrator.runtime.catalog import RuntimeCatalog
from orchestrator.runtime.profile import HardwareProfile

# The image build arguments only the framework sets (a build's own build_args never replace them).
PINNED_BUILD_ARGS = ("LLAMA_CPP_TAG", "LLAMA_CPP_COMMIT", "LLAMA_CMAKE_ARGS")
# One word of LLAMA_CMAKE_ARGS, exactly what the Dockerfiles accept (they check it again before configuring).
CMAKE_WORD = re.compile(r"^-D[A-Z][A-Z0-9_]{0,63}=[A-Za-z0-9._:+/$;-]{0,256}$", re.ASCII)
_RE_TAG = re.compile(r"^b\d{1,7}$", re.ASCII)
_RE_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ARG_CMAKE = re.compile(r"^\s*ARG\s+LLAMA_CMAKE_ARGS\b", re.MULTILINE)
_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.IGNORECASE | re.MULTILINE)
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]{1,128})?(@sha256:[0-9a-f]{64})?$", re.ASCII)

# How the tools were tuned, as build_info.json records it (not-recipe: the Dockerfile takes no LLAMA_CMAKE_ARGS).
TUNINGS = ("explicit", "variants", "baseline", "not-recipe")
_X86_OPTIONS = frozenset(X86_FEATURES.values())


@dataclass(frozen=True)
class HostTools:
    """What the image build of a target gets for its llama.cpp conversion tools."""
    tag: str
    commit: str
    plan: Optional[BuildPlan]
    tuning: str                    # explicit: the probe's CPU features; variants: all CPU variants; baseline
    reason: str                    # one line for the build log
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def build_args(self) -> Dict[str, str]:
        args = {"LLAMA_CPP_TAG": self.tag, "LLAMA_CPP_COMMIT": self.commit}
        if self.plan is not None:
            args["LLAMA_CMAKE_ARGS"] = cmake_args(self.plan)
        return args

    def record(self) -> Dict[str, Any]:
        """What build_info.json keeps about the tools (tag, commit, how they were tuned, the plan's ID and words)."""
        out: Dict[str, Any] = {"tag": self.tag, "commit": self.commit, "tuning": self.tuning}
        if self.plan is not None:
            out["plan_id"] = self.plan.build_id
            out["cmake_args"] = cmake_args(self.plan).split()
        return out


def cmake_args(plan: BuildPlan) -> str:
    """The plan's CMake options as space-separated -DNAME=VALUE words. A value the Dockerfiles could not take as one
    word (a space, a quote, a glob character) is refused; the recipe makes none for conversion tools."""
    if plan.purpose != "host-tools" or len(plan.steps) != 1:
        raise PlanError("only a single-step host-tools plan becomes LLAMA_CMAKE_ARGS")
    words = []
    for name, value in plan.steps[0].options:
        word = f"-D{name}={value}"
        if not CMAKE_WORD.fullmatch(word):
            raise PlanError(f"option {name} cannot be passed to the image build as one word")
        words.append(word)
    return " ".join(words)


def dockerfile_takes_cmake_args(text: str) -> bool:
    """True when the Dockerfile declares ARG LLAMA_CMAKE_ARGS (its llama.cpp build follows the recipe's plan)."""
    return bool(_ARG_CMAKE.search(text or ""))


def dockerfile_base(text: str) -> Optional[str]:
    """The image of the Dockerfile's first stage (where the tools are compiled), when it is a plain reference."""
    m = _FROM.search(text or "")
    image = m.group(1).lower() if m else ""
    return image if _IMAGE.fullmatch(image) else None


def plan_host_tools(profile: HardwareProfile, *, catalog: RuntimeCatalog,
                    base_image: Optional[str] = None) -> BuildPlan:
    """The recipe's plan for the conversion tools of an image built on the machine `profile` describes. Raises
    PlanError (an OS or architecture the recipe has no plan for, a profile without its facts)."""
    toolchain = ToolchainInfo(host_os=profile.os or "", host_arch=profile.arch or "",
                              base_image=base_image or "target-image")
    return plan_build(profile, purpose="host-tools", placement="container", backend="cpu", catalog=catalog,
                      toolchain=toolchain)


def host_tools(profile: Optional[HardwareProfile], *, pin: Dict[str, str], catalog: Optional[RuntimeCatalog],
               base_image: Optional[str] = None, why_no_profile: str = "no hardware probe of this machine") -> HostTools:
    """The image build arguments for the conversion tools: the pin, plus the plan when a probe of the build host and
    the catalog are there and the recipe has a plan for it; otherwise the Dockerfile's baseline, with the reason."""
    tag, commit = str(pin.get("tag") or ""), str(pin.get("commit") or "")
    if not (_RE_TAG.fullmatch(tag) and _RE_COMMIT.fullmatch(commit)):
        raise PlanError("the llama.cpp pin is not a tag like b11429 with a full commit id")

    def baseline(why: str) -> HostTools:
        return HostTools(tag, commit, None, "baseline",
                         f"llama.cpp conversion tools: the Dockerfile's fixed x86-64 baseline ({why})")

    if profile is None:
        return baseline(why_no_profile)
    if catalog is None:
        return baseline("the runtime catalog could not be read")
    source = catalog.llama_cpp.source
    if source is None or (source.tag, source.commit) != (tag, commit):
        return baseline("the runtime catalog names another llama.cpp commit than the pin")
    try:
        plan = plan_host_tools(profile, catalog=catalog, base_image=base_image)
        cmake_args(plan)
    except PlanError as e:
        return baseline(f"no plan for this machine: {e}")
    options = plan.options()
    if options.get("GGML_CPU_ALL_VARIANTS") == "ON":
        tuning = "variants"
        how = "all CPU variants, the best one chosen at run time (the probe could not tell every CPU feature)"
    else:
        tuning = "explicit"
        if plan.target["arch"] == "x86_64":
            on = [k[len("GGML_"):] for k, v in sorted(options.items()) if v == "ON" and k in _X86_OPTIONS]
            how = "CPU features " + (", ".join(on) if on else "none beyond x86-64")
        else:
            how = f"GGML_CPU_ARM_ARCH={options.get('GGML_CPU_ARM_ARCH', 'armv8-a')}"
    return HostTools(tag, commit, plan, tuning,
                     f"llama.cpp conversion tools tuned to this machine: {how} (plan {plan.build_id[:16]})",
                     tuple(plan.notes))

