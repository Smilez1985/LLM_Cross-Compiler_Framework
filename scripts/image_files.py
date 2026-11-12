#!/usr/bin/env python3
"""
Reading container images without running them (docs/DESIGN.md, Installer, platforms and dependencies: the build's
preload and the taking from vendor and community images). Standard library only: the build's preload scripts carry
this file next to scripts/preload_lock.py and run it on a computer with a network that has neither the framework nor
a container engine; the build (Core/build_preload.reader) loads it for an online build.

- Registry: the registry's HTTP API (Docker Registry v2 / OCI distribution) with an anonymous bearer token when the
  registry asks for one. A manifest is checked against the digest it was asked by (or the index entry that named
  it), every blob against its digest; nothing is trusted that was not checked.
- save_image(): one base image for one platform as a `docker save` archive (manifest.json, the config, the layers as
  the registry serves them), which `docker load` and `podman load` read; it names only its reference.
- take_files(): the named files of an image pinned by digest. The layers are read from the top down, a whiteout
  (`.wh.NAME`) and an opaque folder (`.wh..wh..opq`) hide what is below them, and reading stops once every named file
  is decided. A named file must be a regular file (a hard link inside its layer counts); a symbolic link, a folder, a
  deleted or a missing file is refused, naming it. Each copy is checked against its pinned sha256 and stored under
  that sha256; the rest of the image is discarded (each layer is deleted once read).
- read_files() and read_saved(): small text files of an image whose content is not pinned (its os-release and dpkg
  status, for the build's distribution packages), read the same way from its layers, from the layer files or from a
  `docker save` archive; a link inside the image is followed once.
"""

import hashlib
import json
import os
import platform as _platform
import posixpath
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")
MANIFEST_TYPES = ("application/vnd.oci.image.manifest.v1+json",
                  "application/vnd.docker.distribution.manifest.v2+json")
ACCEPT = ", ".join(INDEX_TYPES + MANIFEST_TYPES)
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_MANIFEST = 4 * 1024 * 1024
TIMEOUT = 120
WHITEOUT = ".wh."
OPAQUE = ".wh..wh..opq"
# The Docker Hub: its name in a reference and the host of its registry API.
HUB_NAMES = ("docker.io", "index.docker.io", "registry-1.docker.io")
HUB_API = "registry-1.docker.io"


class ImageFilesError(Exception):
    pass


# --- references and platforms ----------------------------------------------------------------------------------------

def parse_reference(ref: str):
    """(registry, repository, tag, digest) of an image reference as Docker reads it: a first part with a dot, a colon
    or 'localhost' is the registry, else the Docker Hub (and a name without a slash is under library/)."""
    text = (ref or "").strip()
    if not text or any(c.isspace() for c in text) or "$" in text:
        raise ImageFilesError(f"not an image reference: {ref!r}")
    name, _, digest = text.partition("@")
    if digest and not DIGEST.match(digest):
        raise ImageFilesError(f"{ref}: the digest is not sha256:<64 hex characters>")
    first, slash, rest = name.partition("/")
    if slash and ("." in first or ":" in first or first == "localhost"):
        registry, path = first, rest
    else:
        registry, path = "docker.io", name
    tag = ""
    last = path.rsplit("/", 1)[-1]
    if ":" in last:
        path, tag = path.rsplit(":", 1)
    if registry in HUB_NAMES:
        registry = "docker.io"
        if "/" not in path:
            path = f"library/{path}"
    if not re.fullmatch(r"[a-z0-9]+(?:[._-]+[a-z0-9]+)*(?:/[a-z0-9]+(?:[._-]+[a-z0-9]+)*)*", path):
        raise ImageFilesError(f"{ref}: the repository name {path!r} is not valid")
    if tag and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}", tag):
        raise ImageFilesError(f"{ref}: the tag {tag!r} is not valid")
    return registry, path, tag, digest


def host_platform() -> str:
    """The platform of this computer as images name it (linux/amd64, linux/arm64)."""
    machine = _platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    return f"linux/{arch}"


def _platform_matches(entry_platform: dict, wanted: str) -> bool:
    parts = wanted.split("/")
    if not isinstance(entry_platform, dict) or len(parts) < 2:
        return False
    if entry_platform.get("os") != parts[0] or entry_platform.get("architecture") != parts[1]:
        return False
    variant = entry_platform.get("variant")
    return len(parts) < 3 or variant == parts[2] or (variant is None and parts[2] == "v8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# --- the registry ----------------------------------------------------------------------------------------------------

class Registry:
    """One repository of a registry through its HTTP API. `opener`: a urllib opener (tests pass their own)."""

    def __init__(self, registry: str, repository: str, opener=None):
        self.host = HUB_API if registry == "docker.io" else registry
        self.repository = repository
        self.opener = opener or urllib.request.build_opener()
        self.token = ""
        local = self.host.split(":")[0] in ("localhost", "127.0.0.1")
        self.scheme = "http" if local else "https"

    def _url(self, kind: str, reference: str) -> str:
        return f"{self.scheme}://{self.host}/v2/{self.repository}/{kind}/{reference}"

    def _open(self, url: str, accept: str = ""):
        for attempt in (0, 1):
            request = urllib.request.Request(url)
            if accept:
                request.add_header("Accept", accept)
            if self.token:
                # never passed on to a redirect (a blob store's own address takes no registry token)
                request.add_unredirected_header("Authorization", f"Bearer {self.token}")
            try:
                return self.opener.open(request, timeout=TIMEOUT)
            except urllib.error.HTTPError as error:
                if error.code == 401 and attempt == 0:
                    self._login(error.headers.get("WWW-Authenticate", ""))
                    continue
                raise ImageFilesError(f"{url}: the registry answered {error.code} {error.reason}") from None
            except (urllib.error.URLError, OSError) as error:
                raise ImageFilesError(f"{url}: {getattr(error, 'reason', error)}") from None
        raise ImageFilesError(f"{url}: the registry refused the token it gave")

    def _login(self, challenge: str) -> None:
        if not challenge.lower().startswith("bearer "):
            raise ImageFilesError(f"{self.host} asks for a login this reader does not do (only an anonymous token)")
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = fields.pop("realm", "")
        if not realm.startswith("https://"):
            raise ImageFilesError(f"{self.host}: the token address is not https")
        fields.setdefault("scope", f"repository:{self.repository}:pull")
        with self.opener.open(realm + "?" + urllib.parse.urlencode(fields), timeout=TIMEOUT) as answer:
            data = json.loads(answer.read(MAX_MANIFEST).decode("utf-8"))
        self.token = str(data.get("token") or data.get("access_token") or "")
        if not self.token:
            raise ImageFilesError(f"{self.host} gave no token")

    def manifest(self, reference: str):
        """(bytes, media type, digest) of a manifest or index; checked when `reference` is a digest."""
        with self._open(self._url("manifests", reference), ACCEPT) as answer:
            data = answer.read(MAX_MANIFEST + 1)
            media = (answer.headers.get("Content-Type") or "").split(";")[0].strip()
        if len(data) > MAX_MANIFEST:
            raise ImageFilesError(f"{self.repository}:{reference}: the manifest is too large")
        digest = _sha256(data)
        if DIGEST.match(reference) and digest != reference:
            raise ImageFilesError(f"{self.repository}: the manifest has the digest {digest}, not the pinned "
                                  f"{reference}")
        try:
            media = media or json.loads(data).get("mediaType", "")
        except ValueError:
            raise ImageFilesError(f"{self.repository}:{reference}: the manifest is not JSON") from None
        return data, media, digest

    def blob(self, digest: str, dest: Path) -> Path:
        """Writes the blob `digest` to `dest`, checked against its digest (a wrong one is deleted)."""
        if not DIGEST.match(digest or ""):
            raise ImageFilesError(f"{self.repository}: a blob without a sha256 digest")
        check = hashlib.sha256()
        part = Path(str(dest) + ".part")
        with self._open(self._url("blobs", digest)) as answer, open(part, "wb") as sink:
            for block in iter(lambda: answer.read(1 << 20), b""):
                check.update(block)
                sink.write(block)
        if "sha256:" + check.hexdigest() != digest:
            part.unlink()
            raise ImageFilesError(f"{self.repository}: the blob {digest[:19]}... does not have its digest")
        os.replace(part, dest)
        return dest


def resolve(ref: str, platform: str = "", opener=None):
    """(registry, the image manifest as a dict, its digest) of `ref` for `platform` (default: this computer's): an
    index is followed to the entry of the platform, each document checked against the digest that named it."""
    registry_name, repository, tag, digest = parse_reference(ref)
    registry = Registry(registry_name, repository, opener)
    data, media, found = registry.manifest(digest or tag or "latest")
    document = json.loads(data)
    wanted = platform or host_platform()
    if media in INDEX_TYPES or "manifests" in document:
        entries = [m for m in document.get("manifests") or [] if isinstance(m, dict)
                   and _platform_matches(m.get("platform") or {}, wanted)]
        if not entries:
            raise ImageFilesError(f"{ref} has no image for {wanted}")
        data, media, found = registry.manifest(str(entries[0].get("digest") or ""))
        document = json.loads(data)
        if not DIGEST.match(str(entries[0].get("digest") or "")):
            raise ImageFilesError(f"{ref}: the index names its image without a digest")
    if "layers" not in document or "config" not in document:
        raise ImageFilesError(f"{ref}: not an image manifest ({media or 'no type'})")
    return registry, document, found


# --- a base image as a docker save archive ---------------------------------------------------------------------------

def load_name(ref: str) -> str:
    """The name a load gives the image of `ref` (the reference with its tag, :latest when it names none); a
    reference pinned by digest has no such name."""
    _registry, _path, tag, digest = parse_reference(ref)
    if digest:
        raise ImageFilesError(f"{ref} is pinned by digest; a loaded image cannot carry that name, so the preload "
                              "takes base images named by a tag only")
    return ref if tag else f"{ref}:latest"


def save_image(ref: str, out: Path, platform: str = "", opener=None) -> dict:
    """Writes the image `ref` for `platform` as a `docker save` archive `out` that names only `ref`. Returns
    {ref, name, digest, config, platform}."""
    name = load_name(ref)
    registry, manifest, digest = resolve(ref, platform, opener)
    config = str((manifest.get("config") or {}).get("digest") or "")
    layers = [str(layer.get("digest") or "") for layer in manifest.get("layers") or [] if isinstance(layer, dict)]
    with tempfile.TemporaryDirectory(prefix="llmcf-image-") as work:
        work = Path(work)
        registry.blob(config, work / "config.json")
        diff_ids = (json.loads((work / "config.json").read_text(encoding="utf-8")).get("rootfs") or {}).get("diff_ids")
        if not isinstance(diff_ids, list) or len(diff_ids) != len(layers):
            raise ImageFilesError(f"{ref}: the config does not list one layer for each layer of the manifest")
        entries = []
        for number, layer in enumerate(layers):
            registry.blob(layer, work / f"layer-{number}")
            entries.append((work / f"layer-{number}", f"{layer.split(':', 1)[1]}/layer.tar"))
        config_name = f"{config.split(':', 1)[1]}.json"
        listing = [{"Config": config_name, "RepoTags": [name], "Layers": [n for _, n in entries]}]
        (work / "manifest.json").write_text(json.dumps(listing), encoding="utf-8")
        part = Path(str(out) + ".part")
        with tarfile.open(part, "w") as archive:
            archive.add(work / "manifest.json", "manifest.json")
            archive.add(work / "config.json", config_name)
            for path, member in entries:
                archive.add(path, member)
        os.replace(part, out)
    return {"ref": ref, "name": name, "digest": digest, "config": config, "platform": platform or host_platform()}


# --- the named files of an image -------------------------------------------------------------------------------------

def normal_path(path: str) -> str:
    """A path in an image without its leading / or ./ ('' is the root); '..' is refused."""
    text = str(path or "").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    parts = [p for p in text.strip("/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ImageFilesError(f"{path}: no '..' in a path of an image")
    return "/".join(parts)


def _ancestors(path: str):
    """The folders above `path`, from the root's children down ('a', 'a/b' for 'a/b/c')."""
    parts = path.split("/")
    return ["/".join(parts[:n]) for n in range(1, len(parts))]


def _decide(path: str, names: dict):
    """What one layer says about `path`: ('file', member), ('refuse', why), ('deleted', why) or None (look lower)."""
    member = names.get(path)
    if member is not None:
        if member.isfile() or member.islnk():
            return "file", member
        if member.issym():
            return "refuse", f"is a link to {member.linkname} in the image; name the file it points to"
        if member.isdir():
            return "refuse", "is a folder in the image, not a file"
        return "refuse", "is not a regular file in the image"
    for above in _ancestors(path) + [path]:
        parent, _, base = above.rpartition("/")
        if (f"{parent}/" if parent else "") + WHITEOUT + base in names:
            return "deleted", f"is deleted in a layer of the image ({above})"
    for above in _ancestors(path):
        entry = names.get(above)
        if entry is not None and not entry.isdir():
            return "deleted", f"lies under {above}, which a layer of the image replaces with a non-folder"
        if f"{above}/{OPAQUE}" in names:
            return "deleted", f"lies under {above}, which a layer of the image empties (an opaque folder)"
    if f"{OPAQUE}" in names:
        return "deleted", "a layer of the image empties its root folder"
    return None


def take_files(ref: str, files: dict, out: Path, platform: str = "", opener=None, layer_source=None) -> dict:
    """Copies the files `files` ({path in the image: sha256}) of the image `ref` (pinned by digest) for `platform`
    into the folder `out`, each named by its sha256. Returns {path: {"sha256", "mode", "size"}}. ImageFilesError
    names every file that cannot be taken (missing, deleted, a link or folder, another sha256). `layer_source`
    (tests): a callable giving (manifest digest, [layer files from the bottom up]) instead of the registry."""
    if not parse_reference(ref)[3]:
        raise ImageFilesError(f"{ref}: a source image is pinned by its digest (name@sha256:...)")
    wanted = {normal_path(p): str(s).lower() for p, s in files.items()}
    for path, sha in wanted.items():
        if not path or not SHA256.match(sha):
            raise ImageFilesError(f"{path or '/'}: a file path and its sha256 (64 hex characters)")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    found, problems = {}, {}
    with tempfile.TemporaryDirectory(prefix="llmcf-image-files-") as work:
        work = Path(work)
        if layer_source is not None:
            layers = list(reversed(layer_source()))
            fetch = None
        else:
            registry, manifest, _digest = resolve(ref, platform, opener)
            layers = [str(layer.get("digest") or "") for layer in reversed(manifest.get("layers") or [])]
            fetch = registry
        for number, layer in enumerate(layers):
            open_ = [p for p in wanted if p not in found and p not in problems]
            if not open_:
                break
            if fetch is not None:
                path = fetch.blob(layer, work / f"layer-{number}")
            else:
                path = Path(layer)
            try:
                with tarfile.open(path, "r:*") as tar:
                    names = {}
                    for member in tar.getmembers():
                        names[normal_path(member.name)] = member
                    for wanted_path in open_:
                        verdict = _decide(wanted_path, names)
                        if verdict is None:
                            continue
                        kind, detail = verdict
                        if kind != "file":
                            problems[wanted_path] = detail
                            continue
                        found[wanted_path] = _copy(tar, detail, wanted[wanted_path], out, wanted_path, problems)
            except (tarfile.TarError, OSError, EOFError) as error:
                raise ImageFilesError(f"{ref}: a layer is not a readable tar archive ({error})") from None
            finally:
                if fetch is not None:
                    path.unlink(missing_ok=True)
    found = {p: v for p, v in found.items() if v is not None}
    for path in wanted:
        if path not in found and path not in problems:
            problems[path] = "is not in the image"
    if problems:
        raise ImageFilesError(f"{ref}: " + "; ".join(f"/{p} {why}" for p, why in sorted(problems.items())))
    return found


def _copy(tar, member, sha: str, out: Path, path: str, problems: dict):
    handle = tar.extractfile(member)
    if handle is None:
        problems[path] = "cannot be read from its layer"
        return None
    check = hashlib.sha256()
    part = out / f".{sha}.part"
    size = 0
    with handle, open(part, "wb") as sink:
        for block in iter(lambda: handle.read(1 << 20), b""):
            check.update(block)
            sink.write(block)
            size += len(block)
    if check.hexdigest() != sha:
        part.unlink()
        problems[path] = f"has the sha256 {check.hexdigest()}, not the pinned {sha}"
        return None
    os.replace(part, out / sha)
    return {"sha256": sha, "mode": member.mode & 0o777, "size": size}


# --- small files of an image whose content is not pinned (os-release, the dpkg status) --------------------------------

MAX_READ = 64 * 1024 * 1024


def _read_once(layers, paths) -> dict:
    """{path: ("file", bytes) | ("link", target path) | None} from the top layer that decides each path."""
    todo, out = list(paths), {}
    for layer in reversed(layers):
        if not todo:
            break
        if hasattr(layer, "seek"):
            layer.seek(0)
        try:
            tar = tarfile.open(fileobj=layer, mode="r:*") if hasattr(layer, "read") else tarfile.open(layer, "r:*")
        except (tarfile.TarError, OSError, EOFError) as error:
            raise ImageFilesError(f"a layer is not a readable tar archive ({error})") from None
        with tar:
            names = {normal_path(m.name): m for m in tar.getmembers()}
            for path in list(todo):
                member = names.get(path)
                if member is not None and member.issym():
                    link = member.linkname
                    joined = link if link.startswith("/") else "/".join(path.split("/")[:-1] + [link])
                    out[path] = ("link", normal_path(posixpath.normpath("/" + joined)))
                    todo.remove(path)
                    continue
                verdict = _decide(path, names)
                if verdict is None:
                    continue
                kind, detail = verdict
                handle = tar.extractfile(detail) if kind == "file" and detail.size <= MAX_READ else None
                out[path] = ("file", handle.read()) if handle is not None else None
                todo.remove(path)
    return out


def read_files(layers, paths) -> dict:
    """{path: bytes or None} of `paths` in the image made of `layers` (seekable binary file objects or paths of layer
    tars, from the bottom up): the top layer that decides a path wins, a whiteout or an opaque folder hides it (None),
    a symbolic link is followed once, to its target in the same image. Nothing is checked against a pin: the caller
    reads only facts of the image (what it is, what it has installed)."""
    layers = list(layers)
    wanted = [normal_path(p) for p in paths]
    first = _read_once(layers, wanted)
    links = {p: v[1] for p, v in first.items() if v and v[0] == "link"}
    second = _read_once(layers, sorted(set(links.values()))) if links else {}
    out = {}
    for path in wanted:
        entry = second.get(links[path]) if path in links else first.get(path)
        out[path] = entry[1] if entry and entry[0] == "file" else None
    return out


def read_saved(archive, paths) -> dict:
    """read_files() over a `docker save` archive (its manifest.json names the layers, as Docker, Podman and
    save_image() write them)."""
    try:
        with tarfile.open(archive, "r") as tar:
            handle = tar.extractfile("manifest.json")
            listing = json.loads(handle.read().decode("utf-8")) if handle is not None else None
            if not isinstance(listing, list) or len(listing) != 1 or not isinstance(listing[0], dict):
                raise ImageFilesError(f"{archive}: not one image in a docker save archive")
            layers = []
            for name in listing[0].get("Layers") or []:
                member = tar.extractfile(str(name))
                if member is None:
                    raise ImageFilesError(f"{archive}: the layer {name} is missing")
                layers.append(member)
            return read_files(layers, paths)
    except (tarfile.TarError, OSError, EOFError, KeyError, ValueError) as error:
        raise ImageFilesError(f"{archive}: not a readable docker save archive ({error})") from None


def copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)


__all__ = ["ImageFilesError", "Registry", "copy_file", "host_platform", "load_name", "normal_path",
           "parse_reference", "read_files", "read_saved", "resolve", "save_image", "take_files"]
