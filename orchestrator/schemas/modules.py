#!/usr/bin/env python3
"""
The parts of a target module definition that add vendor software (MCP design 2.6, DECISIONS round 3 (d) and round 4
item 4): an apt repository with its signing key, and a pinned download. Shared by Ditto's ModuleDefinition and the
MCP tool create_target_module; the module generator checks every value again (orchestrator/Core/module_generator.py).
No field is named like a credential: a key here is a public signing key, given by its address and fingerprint.
"""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class PackageSourceIn(BaseModel):
    """A vendor apt repository: the framework's Dockerfile template fetches its signing key during the image build,
    checks the key's fingerprint and adds the source with signed-by."""
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=9, max_length=300, description="https address of the apt repository, e.g. "
                                                                "https://repo.example.com/debian")
    suite: str = Field(min_length=1, max_length=64, description="Suite (distribution), e.g. bookworm or jammy")
    components: List[str] = Field(min_length=1, max_length=10, description="Components, e.g. [\"main\"]")
    signed_by_url: str = Field(min_length=9, max_length=300, description="https address of the repository's public "
                                                                         "signing key")
    signed_by_fingerprint: str = Field(min_length=40, max_length=60, description="The signing key's full fingerprint "
                                                                                 "(40 hex characters)")


class PinnedDownloadIn(BaseModel):
    """A file the image build downloads into /opt/vendor (for example a vendor SDK tarball), checked by size and
    sha256 before it gets its name. Never a prebuilt llama.cpp (targets build it from source)."""
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=9, max_length=500, description="https address of the file")
    name: str = Field(min_length=1, max_length=100, description="File name under /opt/vendor, e.g. sdk-1.2.tar.gz")
    sha256: str = Field(min_length=64, max_length=64, description="The file's sha256 (64 hex characters)")
    size_bytes: int = Field(gt=0, description="The file's exact size in bytes")
