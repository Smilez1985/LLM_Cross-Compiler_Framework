#!/usr/bin/env python3
"""
Looks up a container image in its registry, anonymously, for the question before an AI-created target module is
written (MCP design 6.6 rule 11, DECISIONS round 3): the digest a tag points at now (the module is written pinned to
exactly that digest), the platforms in the image's manifest and whether one matches this computer.

Only the registry's public read API is used (Docker Registry HTTP API v2 / OCI distribution), with an anonymous pull
token where the registry hands one out. A registry that needs a login is not looked up: the person's Docker
credentials stay with Docker, and the person gives the image as image@sha256:... instead. Every address the lookup
reaches (the registry, its token service) must be public (validate_public_url), so an image reference cannot make the
framework reach into the local network. Nothing of the answer but the digest and the platforms is used.
"""

import hashlib
import json
import platform
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.llm.task_prompts import split_image
from orchestrator.utils.next_steps import WithSteps

MAX_MANIFEST_BYTES = 4 * 1024 * 1024
TIMEOUT = 15.0
MAX_REDIRECTS = 5
MANIFEST_TYPES = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json")
DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm/v7",
         "riscv64": "riscv64", "ppc64le": "ppc64le", "s390x": "s390x"}
PIN_NEXT = "Give the image with its digest (image@sha256:...), which needs no lookup."


class ImageLookupError(WithSteps, ValueError):
    """The image could not be looked up; `code` is the reason (missing_information, needs_person, invalid_value)."""

    def __init__(self, message: str, code: str = "missing_information", next: str = PIN_NEXT):
        super().__init__(message)
        self.code, self.next = code, next


@dataclass(frozen=True)
class ImageFacts:
    """What the question shows about a base image and what the module is written with (`pinned`)."""
    reference: str
    registry: str
    repository: str
    tag: str
    digest: str
    official: bool
    resolved_now: bool                       # a tag was resolved to its digest by this lookup
    platforms: Tuple[str, ...] = ()          # os/arch[/variant] in the manifest; empty: unknown
    host: str = ""                           # this computer, as os/arch
    note: str = ""                           # why the platforms are unknown, when they are
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def pinned(self) -> str:
        """The reference the module is written with: image[:tag]@sha256:..."""
        name = self.repository if self.registry == "docker.io" else f"{self.registry}/{self.repository}"
        return f"{name}{':' + self.tag if self.tag else ''}@{self.digest}"

    @property
    def platform_match(self) -> Optional[bool]:
        if not self.platforms or not self.host:
            return None
        return any(p == self.host or p.startswith(self.host + "/") for p in self.platforms)


def host_platform() -> str:
    machine = platform.machine().lower()
    return f"linux/{_ARCH.get(machine, machine)}"


def _registry_host(registry: str) -> str:
    return "registry-1.docker.io" if registry == "docker.io" else registry


def _check_public(url: str) -> None:
    from orchestrator.utils.validation import validate_public_url
    result = validate_public_url(url)
    if not result.valid:
        raise ImageLookupError(f"The registry address {urllib.parse.urlsplit(url).netloc} is not a public address, "
                               "so the framework does not look it up.", code="invalid_value")


def _read(response) -> bytes:
    data = b""
    for chunk in response.iter_content(65536):
        data += chunk
        if len(data) > MAX_MANIFEST_BYTES:
            raise ImageLookupError("The registry's answer is too large to be a manifest.", code="failed")
    return data


def _bearer(challenge: str) -> Dict[str, str]:
    if not challenge.lower().startswith("bearer "):
        return {}
    return {k.lower(): v for k, v in re.findall(r'(\w+)="([^"]*)"', challenge[7:])}


class RegistryClient:
    """A minimal anonymous Registry v2 client. `session`: a requests-like object (tests pass a fake)."""

    def __init__(self, session: Any = None, timeout: float = TIMEOUT):
        if session is None:
            import requests
            from orchestrator.Core.crawler_manager import PublicOnlyAdapter
            session = requests.Session()
            session.trust_env = False  # never a proxy or .netrc credential from the environment
            adapter = PublicOnlyAdapter()  # connects only to the public address that was checked (DNS rebinding)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        self.session, self.timeout = session, timeout
        self._tokens: Dict[str, str] = {}

    def _fetch(self, url: str, headers: Dict[str, str], params: Optional[Dict[str, str]] = None):
        """GET with every redirect followed by hand: each hop must be https and public like the first address (a
        registry must not point the lookup into the local network), and the Authorization header is sent only to
        the host it was issued for."""
        host = urllib.parse.urlsplit(url).netloc.lower()
        for _ in range(MAX_REDIRECTS + 1):
            _check_public(url)
            same_host = urllib.parse.urlsplit(url).netloc.lower() == host
            sent = headers if same_host else {k: v for k, v in headers.items() if k.lower() != "authorization"}
            response = self.session.get(url, headers=sent, params=params, timeout=self.timeout, stream=True,
                                        allow_redirects=False)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("Location", "")
            close = getattr(response, "close", None)
            if callable(close):
                close()
            target = urllib.parse.urljoin(url, location)
            params = None  # the query belongs to the first address; a redirect names its own
            if not location or not target.startswith("https://"):
                raise ImageLookupError("The registry redirected the lookup to an address that is not https.",
                                       code="failed", next=PIN_NEXT)
            url = target
        raise ImageLookupError("The registry redirected the lookup too often.", code="failed", next=PIN_NEXT)

    def _get(self, url: str, headers: Dict[str, str], repo: str):
        auth = self._tokens.get(repo)
        sent = dict(headers, **({"Authorization": f"Bearer {auth}"} if auth else {}))
        response = self._fetch(url, sent)
        if response.status_code == 401 and not auth:
            challenge = _bearer(response.headers.get("WWW-Authenticate", ""))
            realm = challenge.get("realm", "")
            if not realm.startswith("https://"):
                raise self._login_needed()
            query = {"service": challenge.get("service", ""), "scope": f"repository:{repo}:pull"}
            token_resp = self._fetch(realm, {}, params={k: v for k, v in query.items() if v})
            if token_resp.status_code != 200:
                raise self._login_needed()
            try:
                body = token_resp.json()
            except ValueError:
                raise self._login_needed() from None
            token = body.get("token") or body.get("access_token") if isinstance(body, dict) else None
            if not token:
                raise self._login_needed()
            self._tokens[repo] = token
            return self._get(url, headers, repo)
        if response.status_code in (401, 403):
            raise self._login_needed()
        if response.status_code == 404:
            raise ImageLookupError("The registry does not know this image or tag.", code="invalid_value",
                                   next="Check the image name and tag on the registry's page.")
        if response.status_code != 200:
            raise ImageLookupError(f"The registry answered with HTTP {response.status_code}.", code="failed",
                                   next="Try again later, or " + PIN_NEXT[0].lower() + PIN_NEXT[1:])
        return response

    @staticmethod
    def _login_needed() -> ImageLookupError:
        return ImageLookupError("The registry needs a login to read this image; the framework never uses your registry "
                                "credentials.", code="needs_person",
                                next="Log in with 'docker login' in a terminal on this computer and give the image as "
                                     "image@sha256:... (for example from 'docker buildx imagetools inspect').")

    def manifest(self, registry: str, repo: str, ref: str) -> Tuple[str, Dict[str, Any], str]:
        """(digest, manifest, media type) of a tag or digest."""
        url = f"https://{_registry_host(registry)}/v2/{repo}/manifests/{ref}"
        response = self._get(url, {"Accept": MANIFEST_TYPES}, repo)
        data = _read(response)
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        header = response.headers.get("Docker-Content-Digest", "")
        if DIGEST_RE.fullmatch(ref) and digest != ref:
            raise ImageLookupError("The registry sent a manifest that does not match the digest asked for.",
                                   code="failed")
        if header and header != digest:
            raise ImageLookupError("The registry's digest header does not match the manifest it sent.", code="failed")
        try:
            manifest = json.loads(data)
        except ValueError:
            raise ImageLookupError("The registry's answer is not a manifest.", code="failed") from None
        media = str(manifest.get("mediaType") or response.headers.get("Content-Type", "")).split(";")[0].strip()
        return digest, manifest, media

    def config_platform(self, registry: str, repo: str, digest: str) -> Optional[str]:
        if not DIGEST_RE.fullmatch(str(digest or "")):
            return None
        url = f"https://{_registry_host(registry)}/v2/{repo}/blobs/{digest}"
        data = _read(self._get(url, {}, repo))
        if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
            return None
        try:
            config = json.loads(data)
        except ValueError:
            return None
        os_name, arch = config.get("os"), config.get("architecture")
        variant = config.get("variant")
        return f"{os_name}/{arch}" + (f"/{variant}" if variant else "") if os_name and arch else None


def _platforms(client: RegistryClient, registry: str, repo: str, manifest: Dict[str, Any], media: str) -> List[str]:
    if media in INDEX_TYPES or "manifests" in manifest:
        out = []
        for entry in manifest.get("manifests") or []:
            p = entry.get("platform") or {}
            if not p.get("os") or p.get("os") == "unknown":  # attestation manifests
                continue
            out.append(f"{p['os']}/{p.get('architecture', '?')}" + (f"/{p['variant']}" if p.get("variant") else ""))
        return list(dict.fromkeys(out))
    found = client.config_platform(registry, repo, (manifest.get("config") or {}).get("digest", ""))
    return [found] if found else []


def inspect_image(reference: str, *, offline: bool = False, client: Optional[RegistryClient] = None) -> ImageFacts:
    """The facts of an image reference for the question. A tag is resolved to its digest (refused in offline mode);
    a reference with a digest keeps it, and its platforms are looked up when the framework may go online."""
    info = split_image(reference)
    registry, repository, tag, digest = info["registry"], info["repository"], info["tag"], info["digest"]
    if digest and not DIGEST_RE.fullmatch(digest):
        raise ImageLookupError("The digest is not sha256:<64 hex characters>.", code="invalid_value")
    repo = f"library/{repository}" if registry == "docker.io" and "/" not in repository else repository
    official = bool(info["official"])
    host = host_platform()
    if offline:
        if not digest:
            raise ImageLookupError(f"The framework is in offline mode, so the tag '{tag or 'latest'}' of {repository} "
                                   "cannot be resolved to the digest the module must be pinned to.")
        return ImageFacts(reference, registry, repository, tag, digest, official, False, (), host,
                          note="offline mode: the image's platforms were not looked up")
    client = client or RegistryClient()
    try:
        found, manifest, media = client.manifest(registry, repo, digest or tag or "latest")
    except ImageLookupError:
        if not digest:
            raise
        return ImageFacts(reference, registry, repository, tag, digest, official, False, (), host,
                          note="the registry could not be read: the image's platforms are unknown")
    except Exception as e:  # network errors: a tag cannot be pinned without the registry
        if not digest:
            raise ImageLookupError(f"The registry {registry} could not be reached ({e.__class__.__name__}), so the "
                                   "tag cannot be resolved to its digest.") from None
        return ImageFacts(reference, registry, repository, tag, digest, official, False, (), host,
                          note="the registry could not be reached: the image's platforms are unknown")
    try:
        platforms = _platforms(client, registry, repo, manifest, media)
        note = "" if platforms else "the manifest names no platform"
    except Exception:  # noqa: BLE001 - the platforms are information, never a reason to refuse
        platforms, note = [], "the image's platforms could not be read"
    return ImageFacts(reference, registry, repository, tag, digest or found, official, not digest, tuple(platforms),
                      host, note=note)
