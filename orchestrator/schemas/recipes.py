#!/usr/bin/env python3
"""
Recipes, the "model templates" of the community zoo: which model (at a pinned commit) was converted for which
target, board, format and quantization, with which options, and what came out (artifact hashes, toolchain
image, device tests). A recipe carries no weights and no binaries; it is rebuilt, never downloaded as a model.
Recipes are written from a successful build's build_info.json (Recipe.from_build_info), never typed by hand.
"""

import re
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator import __version__
from orchestrator.schemas.build import BuildRequestIn
from orchestrator.schemas.targets import TargetCapabilities

_SHA256 = r"^[0-9a-f]{64}$"
_COMMIT = r"^[0-9a-f]{40}$"
_HF_REPO = r"^[A-Za-z0-9][\w.-]*/[\w.-]+$"
_VERSION = r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-z.-]{1,32})?$"
_LICENSE = r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$"  # SPDX id, "other" or "unknown"
_FILE_NAME = r"^[^/\\\x00-\x1f\x7f]{1,200}$"
# A local image id (sha256:...) or a registry reference pinned by digest (name@sha256:...).
_IMAGE = r"^(?:[a-z0-9][a-z0-9._/:-]{0,200}@)?sha256:[0-9a-f]{64}$"
_RECIPE_ID = r"^[a-z0-9][a-z0-9._-]{0,99}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RecipeModel(_Strict):
    repo: str = Field(pattern=_HF_REPO, max_length=200)
    # A commit, never a branch or tag: only a commit names exactly one set of weights.
    revision: str = Field(pattern=_COMMIT)
    license: str = Field(default="unknown", pattern=_LICENSE)

    @field_validator("repo")
    @classmethod
    def _no_traversal(cls, value: str) -> str:
        if ".." in value:
            raise ValueError("not a Hugging Face repo id")
        return value


class RecipeTarget(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9][\w.-]{0,63}$")
    pack_version: Optional[str] = Field(default=None, pattern=_VERSION)
    # PackManifest.content_sha256() of the target folder the build used.
    pack_sha256: str = Field(pattern=_SHA256)


class DatasetRef(_Strict):
    name: str = Field(pattern=_FILE_NAME)
    sha256: str = Field(pattern=_SHA256)


class RecipeOptions(_Strict):
    use_imatrix: bool = False
    max_context_length: Optional[int] = Field(default=None, ge=256, le=1_048_576)
    dataset: Optional[DatasetRef] = None


class ArtifactRef(_Strict):
    file: str = Field(pattern=_FILE_NAME)
    size: int = Field(gt=0)
    sha256: str = Field(pattern=_SHA256)


class DeviceTest(_Strict):
    board_model: str = Field(min_length=1, max_length=100)
    runtime: str = Field(min_length=1, max_length=100)
    tokens_per_s: Optional[float] = Field(default=None, ge=0)
    ram_mb: Optional[int] = Field(default=None, ge=0)
    smoke_passed: bool


class RecipeResult(_Strict):
    """One reproduction: independent results with the same artifact sha256 make a recipe "verified"."""
    artifact: ArtifactRef
    lcf_version: str = Field(pattern=_VERSION)
    toolchain_image: Optional[str] = Field(default=None, pattern=_IMAGE)
    device_test: Optional[DeviceTest] = None
    reporter: Optional[str] = Field(default=None, max_length=64)


class Recipe(_Strict):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=_RECIPE_ID)
    model: RecipeModel
    target: RecipeTarget
    board: Optional[str] = Field(default=None, max_length=64)
    format: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    quantization: Optional[str] = Field(default=None, max_length=32)
    options: RecipeOptions = Field(default_factory=RecipeOptions)
    results: List[RecipeResult] = Field(default_factory=list, max_length=100)
    author: str = Field(min_length=1, max_length=64)
    created: datetime

    @field_validator("created")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created needs a time zone (UTC)")
        return value.astimezone(timezone.utc)

    def check_target(self, capabilities: TargetCapabilities) -> List[str]:
        """Problems of this recipe on the named target (empty: it can be built there)."""
        return capabilities.check_request(self.format, self.board, self.quantization,
                                          dataset=self.options.dataset is not None, imatrix=self.options.use_imatrix)

    def to_build_request(self, dataset_path: Optional[str] = None, use_gpu: bool = False) -> BuildRequestIn:
        """The build that reproduces this recipe, through the existing build service."""
        if self.options.dataset is not None and not dataset_path:
            raise ValueError(f"This recipe needs the calibration dataset {self.options.dataset.name} "
                             f"(sha256 {self.options.dataset.sha256}).")
        return BuildRequestIn(model=self.model.repo, revision=self.model.revision, target=self.target.id,
                              format=self.format, board=self.board, quantization=self.quantization,
                              use_imatrix=self.options.use_imatrix, dataset_path=dataset_path, use_gpu=use_gpu,
                              max_context_length=self.options.max_context_length)

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True)

    @classmethod
    def from_build_info(cls, info: Dict[str, Any], *, author: str, license: str = "unknown",
                        recipe_id: Optional[str] = None, now: Optional[datetime] = None) -> "Recipe":
        """A recipe from a finished build's build_info.json. Raises ValueError when the build cannot be
        reproduced by others (a local model, no pinned commit, no target fingerprint, no artifact)."""
        from orchestrator.Core.community_pack import scrub

        repo, revision = info.get("model_source") or "", info.get("model_revision")
        if not (re.fullmatch(_HF_REPO, repo) and revision):
            raise ValueError("Only builds of a Hugging Face model at a known commit can become a recipe; "
                             "a local model cannot be rebuilt by others.")
        if not info.get("target_pack_sha256"):
            raise ValueError("The build did not record its target fingerprint (built with an older version).")
        artifact = next((a for a in info.get("artifacts") or [] if a.get("file") == info.get("primary")), None)
        if artifact is None:
            raise ValueError("The build lists no primary artifact.")
        board, fmt, quant = info.get("board"), str(info.get("format") or ""), info.get("quantization")
        dataset = info.get("dataset")
        pack_version = str(info.get("target_version") or "")
        if not recipe_id:
            parts = [repo.split("/")[-1], board or info.get("target"), fmt, quant]
            recipe_id = re.sub(r"[^a-z0-9._-]+", "-", "-".join(str(p) for p in parts if p).lower()).strip("-.")[:100]
        return cls(
            id=recipe_id,
            model=RecipeModel(repo=repo, revision=revision, license=license),
            target=RecipeTarget(id=info.get("target"), pack_sha256=info["target_pack_sha256"],
                                pack_version=pack_version if re.fullmatch(_VERSION, pack_version) else None),
            board=board, format=fmt.lower(), quantization=quant,
            options=RecipeOptions(
                use_imatrix=bool(info.get("imatrix")), max_context_length=info.get("max_context_length"),
                dataset=DatasetRef(name=scrub(PurePath(str(dataset["name"])).name), sha256=dataset["sha256"])
                if dataset else None),
            results=[RecipeResult(artifact=ArtifactRef(file=artifact["file"], size=artifact["bytes"],
                                                       sha256=artifact["sha256"]),
                                  lcf_version=info.get("lcf_version") or __version__,
                                  toolchain_image=info.get("toolchain_image"))],
            author=author,
            created=now or datetime.now(timezone.utc),
        )
