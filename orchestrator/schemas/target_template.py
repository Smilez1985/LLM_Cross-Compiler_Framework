#!/usr/bin/env python3
"""
The variables of the general target template (targets/_template): what a module declares about each output format
it builds, kept in its target.yml under `template_variables:` and rendered by the module generator
(orchestrator/Core/module_generator.py) into the config stage as data.

The template holds the pattern of every stage for any hardware; what depends on the board or the SoC is a variable:
the vendor's converter and its arguments, the input formats it takes, its output's extension, its quantization types,
the runtime libraries the board needs (with their versions, their path in the build image and whether the vendor's
licence allows passing them on), the paths on the board, the format's `sdk:` needs for the pre-flight check and the
vendor files that may not be passed on (only referenced; the person fetches them under their own licence acceptance).
GGUF with llama.cpp is the pattern's built-in instance (converter `llama.cpp`). Next to the formats, `source_images`
names a vendor's or a community's image pinned by digest and the files the build image takes from it (read without
running it, Core/build_preload.py); BuildPreload is target.yml's `build_preload:` block, the Python packages of the
build image for the build's preload of a computer without a network.

Every value is a plain word or address: no quote, no `$`, no backtick, no space in a word, no bracket in a text
(a bracketed text reads as a template placeholder). The module generator checks every value again with the shell
deny patterns of today's fields.
"""

import re
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orchestrator.schemas.targets import SdkNeeds

# The placeholders a converter argument may hold; the convert stage puts the build's values in their place.
PLACEHOLDERS = ("input", "output", "output_dir", "quant", "model_name", "work_dir")
BUILTIN_CONVERTER = "llama.cpp"   # the GGUF instance of the pattern: the template's own GGUF stages
FORMAT_ID_RE = r"^[a-z][a-z0-9_]{0,23}$"
# A converter: a program of the build image by its absolute path or by a plain name on its PATH, or a module or a
# script of the target folder.
COMMAND_RE = re.compile(r"^(?:/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+|modules/[a-z0-9_]{1,64}_module\.sh"
                        r"|scripts/[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\.py|[A-Za-z0-9][A-Za-z0-9._+-]{0,63})$")
# One argument word: letters, digits and . , : = + / @ % _ - and the {placeholders}; nothing the shell would read.
ARG_RE = re.compile(r"^[A-Za-z0-9_.,:=+/@%{}-]{1,256}$")
ABS_PATH_RE = r"^/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+$"
FILE_NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$"
VERSION_RE = r"^[A-Za-z0-9][A-Za-z0-9.+_~-]{0,31}$"
QUANT_RE = r"^[A-Za-z0-9_]{1,32}$"
HTTPS_RE = (r"^https://[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::\d{1,5})?"
            r"(?:/[A-Za-z0-9._~%/+=-]*)?$")
# A label or note: printable text without quotes, backslash, $, backtick, brackets or braces.
TEXT_RE = r"^[^\"'\\$`\[\]{}<>\x00-\x1f\x7f]*$"
InputFormat = Literal["hf", "onnx", "gguf"]


def _no_dots(value: str, what: str) -> str:
    if ".." in value.split("/"):
        raise ValueError(f"{what}: no '..' in a path")
    return value


class Converter(BaseModel):
    """The program that converts a model into the format, and its arguments."""
    model_config = ConfigDict(extra="forbid")

    command: str = Field(max_length=300, description="'llama.cpp' (GGUF, built in), a program of the build image "
                                                     "(absolute path or a plain name on its PATH), "
                                                     "modules/NAME_module.sh or scripts/NAME.py of the target")
    args: List[str] = Field(default_factory=list, max_length=32,
                            description="Arguments, one word each; placeholders: " + ", ".join(
                                "{" + p + "}" for p in PLACEHOLDERS))

    @field_validator("command")
    @classmethod
    def _command(cls, v: str) -> str:
        v = v.strip()
        if v != BUILTIN_CONVERTER and not COMMAND_RE.fullmatch(v):
            raise ValueError("command: 'llama.cpp', an absolute path in the build image, a plain program name, "
                             "modules/NAME_module.sh or scripts/NAME.py")
        return _no_dots(v, "command")

    @field_validator("args")
    @classmethod
    def _args(cls, v: List[str]) -> List[str]:
        for word in v:
            if not ARG_RE.fullmatch(word):
                raise ValueError(f"args: '{word[:60]}' is not one plain word (letters, digits and . , : = + / @ % _ - "
                                 "and the placeholders)")
            for name in re.findall(r"\{([^{}]*)\}", word):
                if name not in PLACEHOLDERS:
                    raise ValueError(f"args: unknown placeholder {{{name}}} (known: {', '.join(PLACEHOLDERS)})")
            if re.sub(r"\{[a-z_]+\}", "", word).count("{") or re.sub(r"\{[a-z_]+\}", "", word).count("}"):
                raise ValueError(f"args: '{word[:60]}' has a brace outside a placeholder")
            _no_dots(word, "args")
        return v


class RuntimeLibrary(BaseModel):
    """A library the board needs to run the format's models."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=FILE_NAME_RE, description="File name, e.g. libvendorrt.so")
    version: str = Field(pattern=VERSION_RE, description="Its version, e.g. 2.1.0")
    image_path: str = Field(pattern=ABS_PATH_RE, max_length=300,
                            description="Where it is in the build image, e.g. /opt/vendor/lib/libvendorrt.so")
    redistributable: bool = Field(default=False, description="Whether the vendor's licence allows passing it on in a "
                                                             "package (then it is copied into the package)")
    source_url: Optional[str] = Field(default=None, pattern=HTTPS_RE, max_length=500,
                                      description="Where the person fetches it under their own licence acceptance "
                                                  "(required when it may not be passed on)")
    licence_url: Optional[str] = Field(default=None, pattern=HTTPS_RE, max_length=500)

    @model_validator(mode="after")
    def _source(self):
        _no_dots(self.image_path, "image_path")
        if not self.redistributable and not self.source_url:
            raise ValueError(f"runtime library {self.name}: may not be passed on, so source_url says where the person "
                             "fetches it")
        return self


class VendorFile(BaseModel):
    """A vendor file the format needs that may not be passed on: only referenced, never copied into a package or a
    pack; the person fetches it under their own licence acceptance."""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=FILE_NAME_RE)
    url: str = Field(pattern=HTTPS_RE, max_length=500)
    licence_url: str = Field(pattern=HTTPS_RE, max_length=500)
    purpose: str = Field(default="", max_length=200, pattern=TEXT_RE)


class BoardPaths(BaseModel):
    """Where the package goes on the board."""
    model_config = ConfigDict(extra="forbid")

    install_dir: str = Field(default="/opt/llmcf", pattern=ABS_PATH_RE, max_length=200)
    model_dir: str = Field(default="/opt/llmcf/models", pattern=ABS_PATH_RE, max_length=200)
    lib_dir: str = Field(default="/opt/llmcf/lib", pattern=ABS_PATH_RE, max_length=200)

    @model_validator(mode="after")
    def _dots(self):
        for value in (self.install_dir, self.model_dir, self.lib_dir):
            _no_dots(value, "board_paths")
        return self


class FormatVariables(BaseModel):
    """One output format of a module: the board- and SoC-dependent values the template's convert and target stages
    take."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=FORMAT_ID_RE, description="Format id as builds name it, e.g. gguf or hef")
    label: str = Field(min_length=1, max_length=80, pattern=TEXT_RE)
    converter: Converter
    input: List[InputFormat] = Field(min_length=1, max_length=3, description="Input formats: hf, onnx, gguf")
    output_extension: str = Field(pattern=r"^[a-z0-9]{1,12}$")
    quantizations: List[str] = Field(min_length=1, max_length=100)
    default_quantization: str
    runtime_libraries: List[RuntimeLibrary] = Field(default_factory=list, max_length=32)
    board_paths: BoardPaths = Field(default_factory=BoardPaths)
    sdk: Optional[SdkNeeds] = None
    vendor_files: List[VendorFile] = Field(default_factory=list, max_length=16)
    notes: str = Field(default="", max_length=300, pattern=TEXT_RE)

    @field_validator("quantizations")
    @classmethod
    def _quants(cls, v: List[str]) -> List[str]:
        bad = [q for q in v if not re.fullmatch(QUANT_RE, q)]
        if bad:
            raise ValueError(f"quantizations: plain type names (letters, digits, _): {', '.join(bad[:5])}")
        if len(set(v)) != len(v):
            raise ValueError("quantizations: each type once")
        return v

    @model_validator(mode="after")
    def _consistent(self):
        problems = []
        if self.default_quantization not in self.quantizations:
            problems.append(f"default_quantization {self.default_quantization!r} is not in quantizations")
        if len(set(self.input)) != len(self.input):
            problems.append("input: each format once")
        builtin = self.converter.command == BUILTIN_CONVERTER
        if builtin and self.id != "gguf":
            problems.append("the llama.cpp converter makes the format gguf only")
        if self.id == "gguf" and not builtin:
            problems.append("gguf is the template's llama.cpp format: its converter is llama.cpp")
        if builtin and self.converter.args:
            problems.append("the llama.cpp converter takes no arguments here (the GGUF stages call it)")
        if builtin and self.output_extension != "gguf":
            problems.append("the gguf format's extension is gguf")
        names = [lib.name for lib in self.runtime_libraries]
        if len(set(names)) != len(names):
            problems.append("runtime_libraries: each name once")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def capability(self) -> Dict[str, Any]:
        """The format's entry in target.yml's capabilities block (orchestrator/schemas/targets.py FormatCapability)."""
        cap: Dict[str, Any] = {"label": self.label, "input": list(self.input), "quantizations": list(self.quantizations),
                               "default_quantization": self.default_quantization}
        if self.converter.command == BUILTIN_CONVERTER:
            cap.update({"imatrix": True, "dataset": "optional"})
        if self.notes:
            cap["notes"] = self.notes
        if self.sdk is not None:
            cap["sdk"] = self.sdk.model_dump(exclude_none=True)
        return cap

    def helper_files(self) -> List[str]:
        """The files of the template a converter of the target folder needs (the Rockchip helpers start their
        converter scripts)."""
        command = self.converter.command
        if command.startswith(("modules/", "scripts/")):
            return [command] + list(HELPER_SCRIPTS.get(command, ()))
        return []


# The format helpers of the template and the converter scripts they start (copied only when a format names them).
HELPER_SCRIPTS = {"modules/rknn_module.sh": ("scripts/rknn_converter.py",),
                  "modules/rkllm_module.sh": ("scripts/export_rkllm.py",)}


IMAGE_PIN_RE = (r"^(?:[a-z0-9.-]+(?::\d{1,5})?/)?[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*"
                r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?@sha256:[0-9a-f]{64}$")
PLATFORM_RE = r"^linux/(?:amd64|arm64)$"
SHA256_HEX_RE = r"^[0-9a-f]{64}$"


class ImageFile(BaseModel):
    """One file taken from a source image: where it is in that image, its sha256, where it goes in llmcf's image."""
    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=ABS_PATH_RE, max_length=300, description="Its path in the source image")
    sha256: str = Field(pattern=SHA256_HEX_RE)
    dest: str = Field(pattern=ABS_PATH_RE, max_length=300, description="Its path in llmcf's build image")
    executable: bool = False
    redistributable: bool = Field(default=False, description="Whether its licence allows passing it on; a file that "
                                                             "may not stays with the person")

    @model_validator(mode="after")
    def _dots(self):
        _no_dots(self.path, "path")
        _no_dots(self.dest, "dest")
        return self


class SourceImage(BaseModel):
    """A vendor's or a community's image that already has files the build image needs: pinned by digest, read
    without running it, only the named files taken (scripts/image_files.py, Core/build_preload.py)."""
    model_config = ConfigDict(extra="forbid")

    image: str = Field(pattern=IMAGE_PIN_RE, max_length=400, description="name@sha256:DIGEST (manifest or index)")
    platform: Optional[str] = Field(default=None, pattern=PLATFORM_RE,
                                    description="linux/amd64 or linux/arm64; default: the build computer's")
    licence_url: Optional[str] = Field(default=None, pattern=HTTPS_RE, max_length=500)
    purpose: str = Field(default="", max_length=200, pattern=TEXT_RE)
    files: List[ImageFile] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _unique(self):
        if len({f.path for f in self.files}) != len(self.files):
            raise ValueError(f"{self.image}: each file path once")
        return self


class TemplateVariables(BaseModel):
    """The `template_variables:` block of a generated target.yml: the formats the module was made with and the files
    its build image takes from source images."""
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    formats: List[FormatVariables] = Field(min_length=1, max_length=8)
    source_images: List[SourceImage] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _unique(self):
        ids = [f.id for f in self.formats]
        if len(set(ids)) != len(ids):
            raise ValueError("formats: each id once")
        dests = [f.dest for image in self.source_images for f in image.files]
        if len(set(dests)) != len(dests):
            raise ValueError("source_images: each dest once")
        return self


# One requirement line of pip: a name with optional extras and version clauses, or `name @ https://...`; nothing a
# shell or pip's option parser would read (no leading -, no quote, no ; marker).
REQUIREMENT_RE = (r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?"
                  r"(?:\s*(?:===|==|~=|!=|>=|<=|<|>)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:===|==|~=|!=|>=|<=|<|>)\s*"
                  r"[A-Za-z0-9.*+!_-]+)*|\s@\shttps://[A-Za-z0-9._~%/+=:#-]+)?$")
LLAMA_FILE_RE = r"^requirements(?:/[A-Za-z0-9_.-]+)*\.txt$|^requirements\.txt$"


class PipGroup(BaseModel):
    """Requirement lines the build image's pip installs from one index (llama.cpp's own requirement files by their
    path at the pinned commit)."""
    model_config = ConfigDict(extra="forbid")

    requirements: List[str] = Field(default_factory=list, max_length=200)
    index_url: Optional[str] = Field(default=None, pattern=HTTPS_RE, max_length=500)
    extra_index_urls: List[str] = Field(default_factory=list, max_length=4)
    llama_cpp_requirements: List[str] = Field(default_factory=list, max_length=8)
    platforms: List[str] = Field(default_factory=list, max_length=2,
                                 description="Only for these build computers (linux/amd64, linux/arm64); empty: all")

    @model_validator(mode="after")
    def _lines(self):
        if any(not re.fullmatch(PLATFORM_RE, p) for p in self.platforms):
            raise ValueError("platforms: linux/amd64 or linux/arm64")
        bad = [r for r in self.requirements if len(r) > 300 or not re.fullmatch(REQUIREMENT_RE, r)]
        bad += [u for u in self.extra_index_urls if not re.fullmatch(HTTPS_RE, u)]
        bad += [f for f in self.llama_cpp_requirements if not re.fullmatch(LLAMA_FILE_RE, f) or ".." in f]
        if bad:
            raise ValueError(f"not a plain requirement, https index or llama.cpp requirement file: {bad[0][:80]}")
        if not self.requirements and not self.llama_cpp_requirements:
            raise ValueError("a pip group names requirements or llama.cpp requirement files")
        return self


class BuildPreload(BaseModel):
    """target.yml `build_preload:`: the Python packages the build image installs, for the build's preload of a
    computer without a network (Core/build_preload.py): the image's Python and glibc and the pip groups."""
    model_config = ConfigDict(extra="forbid")

    python: str = Field(default="3.11", pattern=r"^3\.(?:1[0-4]|[89])$")
    glibc: str = Field(default="2.36", pattern=r"^2\.\d{1,3}$")
    pip: List[PipGroup] = Field(default_factory=list, max_length=16)
    gpu_python: Optional[str] = Field(default=None, pattern=r"^3\.(?:1[0-4]|[89])$",
                                      description="The Python of the GPU image (Dockerfile.gpu); default: python")
    gpu_glibc: Optional[str] = Field(default=None, pattern=r"^2\.\d{1,3}$")
    gpu_pip: List[PipGroup] = Field(default_factory=list, max_length=16,
                                    description="The pip groups of the GPU image; empty: pip")
    tools: List[str] = Field(default_factory=lambda: ["pip", "setuptools", "wheel", "poetry-core"], max_length=16,
                             description="Fetched one by one without dependencies: what the image's pip upgrades to "
                                         "and builds local packages with")

    @model_validator(mode="after")
    def _tools(self):
        bad = [r for r in self.tools if not re.fullmatch(REQUIREMENT_RE, r)]
        if bad:
            raise ValueError(f"tools: not a plain requirement: {bad[0][:80]}")
        return self

    def section(self, gpu: bool = False) -> Optional[Dict[str, Any]]:
        """What the preload fetches for the image (gpu: Dockerfile.gpu), as the recipe holds it; None without groups."""
        groups = (self.gpu_pip or self.pip) if gpu else self.pip
        if not groups:
            return None
        return {"python": (self.gpu_python or self.python) if gpu else self.python,
                "glibc": (self.gpu_glibc or self.glibc) if gpu else self.glibc,
                "groups": [g.model_dump(exclude_none=True) for g in groups], "tools": list(self.tools)}


def gguf_format(quantizations: List[str]) -> Dict[str, Any]:
    """The GGUF instance of the pattern (llama.cpp), as a module without other formats records it."""
    return {"id": "gguf", "label": "GGUF (llama.cpp)", "converter": {"command": BUILTIN_CONVERTER},
            "input": ["hf"], "output_extension": "gguf", "quantizations": list(quantizations),
            "default_quantization": quantizations[0]}


# Ready values for the formats the template already has helpers for: a format entry may be just one of these names.
PRESETS: Dict[str, Dict[str, Any]] = {
    "rknn": {"id": "rknn", "label": "RKNN (Rockchip NPU, ONNX vision/audio models)",
             "converter": {"command": "modules/rknn_module.sh"}, "input": ["onnx"], "output_extension": "rknn",
             "quantizations": ["fp16", "i8"], "default_quantization": "fp16",
             "sdk": {"toolkit": "RKNN-Toolkit2", "version": "2.3.2", "runtime_key": "RKNN_RUNTIME_VERSION",
                     "runtime_name": "librknnrt"}},
    "rkllm": {"id": "rkllm", "label": "RKLLM (Rockchip NPU, LLMs)",
              "converter": {"command": "modules/rkllm_module.sh"}, "input": ["hf"], "output_extension": "rkllm",
              "quantizations": ["w8a8", "w8a8_g128", "w8a8_g256", "w8a8_g512", "w4a16", "w4a16_g32", "w4a16_g64",
                                "w4a16_g128"], "default_quantization": "w8a8",
              "sdk": {"toolkit": "RKLLM-Toolkit", "version": "1.3.1", "runtime_key": "RKLLM_RUNTIME_VERSION",
                      "runtime_name": "librkllmrt"}},
}


def resolve_formats(entries: Optional[List[Union[str, Dict[str, Any], FormatVariables]]]) -> List[Dict[str, Any]]:
    """The declared formats as plain values: an entry is a format's values or the name of a preset (gguf takes the
    template's GGUF values with its default types)."""
    out: List[Dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, FormatVariables):
            out.append(entry.model_dump(exclude_none=True))
        elif isinstance(entry, str):
            name = entry.strip().lower()
            if name == "gguf":
                out.append(gguf_format(["Q4_K_M", "Q4_0", "Q5_K_M", "Q6_K", "Q8_0", "F16"]))
            elif name in PRESETS:
                out.append(dict(PRESETS[name]))
            else:
                raise ValueError(f"formats: '{entry[:40]}' is no preset (gguf, {', '.join(PRESETS)}); give the "
                                 "format's values instead")
        elif isinstance(entry, dict):
            out.append(dict(entry))
        else:
            raise ValueError("formats: each entry is a format's values or a preset name")
    return out
