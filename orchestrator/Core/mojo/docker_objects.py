#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - The framework's Docker images (Mojo; the doctor's tier 2 of wave 2 and WP7)

The framework's build images are listed for the person (name, id, size, what each is; nothing ticked), and the
person's command line or the GUI's deletion dialog deletes exactly the images the person ticked and confirmed
(round 5 f, round 6 10.4), never over MCP and never under a delegation. It is the one deletion path for the
framework's Docker images. Docker's build cache is never cleared: it cannot be limited to the framework.
"""

import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


class DockerUnavailable(RuntimeError):
    """No Docker client, or the daemon did not answer: nothing can be listed or deleted."""


class ImagePlanChanged(RuntimeError):
    """The framework's images changed since the person saw the list: nothing was deleted."""

    def __init__(self):
        super().__init__("The framework's build images changed since the list was shown; nothing was deleted. "
                         "Look at the new list and confirm it.")


@dataclass
class ImageDeletion:
    """What happened to a confirmed list: the images deleted (by speaking name) with their listed size, and the ones
    Docker refused (speaking name and Docker's reason, e.g. a container still uses it)."""
    deleted: List[str] = field(default_factory=list)
    freed_bytes: int = 0
    failed: List[tuple] = field(default_factory=list)


_TARGET_WORD = re.compile(r"^[\w.+-]{1,64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
# A name the framework gives its images (llm-framework/<target>:<build id or last-build>, llm-framework/llama-builder:
# <base>-<hash>); an image another program named is never listed.
_FRAMEWORK_TAG = re.compile(r"^llm-framework/[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def _docker_client(framework):
    return framework.get_component("docker_client") if hasattr(framework, "get_component") else None


def _framework_labels() -> Tuple[str, str]:
    from orchestrator.Core.builder import IMAGE_LABEL
    from orchestrator.runtime.build.executor import BUILDER_LABEL
    return IMAGE_LABEL, BUILDER_LABEL


def _image_item(image):
    """The deletion item of one image, or None when it is not the framework's: it carries the framework's label and
    either no name or only names the framework gives. Name (speaking), full id, size, and what it is."""
    from orchestrator.tools.spec import DeletionItem
    target_label, builder_label = _framework_labels()
    labels = getattr(image, "labels", None) or {}
    image_id = str(getattr(image, "id", "") or "")
    tags = sorted(str(t) for t in (getattr(image, "tags", None) or []))
    if not _IMAGE_ID.match(image_id) or (target_label not in labels and builder_label not in labels):
        return None
    if any(not _FRAMEWORK_TAG.match(t) for t in tags):
        return None   # another program named it: never listed
    attrs = getattr(image, "attrs", None) or {}
    created = str(attrs.get("Created") or "")[:10]
    built = f" (built {created})" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", created) else ""
    try:
        size = max(0, int(attrs.get("Size") or 0))
    except (TypeError, ValueError):
        size = 0
    short = image_id[7:19]
    if target_label in labels:
        target = str(labels.get(target_label) or "")
        target = target if _TARGET_WORD.match(target) else "an unknown target"
        if not tags:
            name = f"Framework build image {short} ({target})"
            what = (f"unused build image of the framework for {target}, without a name{built}; left over from an "
                    "earlier build")
        else:
            name = f"Framework build image {', '.join(tags)}"
            what = (f"build image of the framework for {target}{built}; the next build for {target} reuses it and "
                    "is faster while it is kept")
    else:
        name = f"Runtime builder image {', '.join(tags) or short}"
        what = (f"image that compiles the llama.cpp runtime (Ditto's server, board runtimes){built}"
                + ("; the next runtime build reuses it" if tags else "; without a name, left over from an earlier "
                                                                     "runtime build"))
    return DeletionItem(kind="docker_image", speaking_name=name[:300], file_name=image_id, size_bytes=size,
                        what=what[:300])


def framework_images(framework) -> List[Any]:
    """The framework's own images (target build images and runtime builder images) as Docker lists them now.
    DockerUnavailable without a reachable Docker daemon. Reads only."""
    client = _docker_client(framework)
    if client is None:
        raise DockerUnavailable("Docker is not available, so no framework images can be listed.")
    found: Dict[str, Any] = {}
    try:
        for label in _framework_labels():
            for image in client.images.list(filters={"label": label}):
                found.setdefault(str(getattr(image, "id", "")), image)
    except Exception:  # noqa: BLE001 - the daemon's own words stay out (they can name a socket path)
        raise DockerUnavailable("The Docker daemon did not answer, so no framework images can be listed.") from None
    return list(found.values())


def framework_image_plan(framework):
    """The list of tier 2 and of Tools > Framework Build Images...: every build image of the framework (those left
    over from earlier builds without a name, the named ones such as llm-framework/<target>:last-build, and the
    runtime builder images) as a deletion plan for the person's multiple choice, each with its name, full id, size
    and what it is; images left over come first. Docker's build cache is never part of it, nor any image another
    program named. Returns None when there is nothing to list; DockerUnavailable without a reachable Docker daemon.
    Reads only."""
    from orchestrator.Core.deletion import plan_of
    listed = [(bool(getattr(image, "tags", None)), item) for image in framework_images(framework)
              for item in [_image_item(image)] if item is not None]
    return plan_of([item for _, item in sorted(listed, key=lambda pair: (pair[0], pair[1].file_name))],
                   ("permanent",))


def docker_storage(framework) -> Optional[Dict[str, Any]]:
    """Where Docker keeps its images (DockerRootDir) with the free space of that disk when it is visible from here, and
    how many images of the framework there are and their size (layers shared between images counted for each).
    None when Docker cannot be asked. Reads only; changes no daemon setting."""
    client = _docker_client(framework)
    if client is None:
        return None
    try:
        info = client.info() or {}
        root = str(info.get("DockerRootDir") or "")
    except Exception:  # noqa: BLE001
        return None
    try:
        images = [i for i in (_image_item(img) for img in framework_images(framework)) if i is not None]
    except DockerUnavailable:
        images = []
    free = None
    if root and os.path.isabs(root):
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            free = None   # Docker Desktop keeps it inside its own virtual machine
    return {"root": root, "free_bytes": free, "images": len(images), "image_bytes": sum(i.size_bytes for i in images)}


def delete_framework_images(framework, plan, actor: str) -> ImageDeletion:
    """Deletes exactly the images of `plan` (the ones the person ticked, or the whole list), which the person accepted
    in the framework's own window (actor "cli" or "gui"; never an AI, never under a delegation). The list is looked up
    again first: if one of the chosen images changed (another name, gone), nothing is deleted (ImagePlanChanged). The
    deletion is audited before anything is deleted (nothing is deleted when the audit cannot be written); each image
    is looked at again right before and removed only while it is still exactly as listed: an image without a name by
    its id, a named one name by name (Docker deletes it with its last name), without force and without its untagged
    parents, and never by a prune, which could catch images that changed after the list was shown."""
    from orchestrator.Core.deletion import AcceptedDeletion, same_items
    if actor not in ("cli", "gui"):
        raise PermissionError("Only the person deletes the framework's images, in the GUI or at a terminal.")
    current = same_items(framework_image_plan(framework), plan)
    if current is None or current.sha256() != plan.sha256():
        raise ImagePlanChanged()
    audit = getattr(getattr(framework, "tool_registry", None), "audit", None)
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so no image was deleted.")
    extra = AcceptedDeletion(plan, "permanent", actor).audit_extra()
    try:
        audit.record(actor, "doctor", "framework", "person", "started", approved=True,
                     arguments={"images": len(plan.items), "bytes": plan.total_bytes},
                     detail=f"deleting {len(plan.items)} framework build images the person chose", extra=extra)
    except Exception:  # noqa: BLE001
        raise RuntimeError("The audit log cannot record the deletion; no image was deleted.") from None
    from orchestrator.utils.helpers import redact_secrets
    client = _docker_client(framework)
    result = ImageDeletion()
    for item in plan.items:
        # A build of another framework process may name a listed image again (the same layers) after the list was
        # looked up: each image is looked at right before its removal and kept unless it is still exactly as listed.
        try:
            now = client.images.get(item.file_name)
        except Exception:  # noqa: BLE001 - gone meanwhile (or the daemon did not answer): nothing to delete
            result.failed.append((item.speaking_name, "kept: it could not be found again, so it was not deleted"))
            continue
        if _image_item(now) != item:
            result.failed.append((item.speaking_name, "kept: it got a name or changed after the list was shown"))
            continue
        names = sorted(getattr(now, "tags", None) or [])
        removed: List[str] = []
        try:
            for name in names or [item.file_name]:
                client.images.remove(image=name, noprune=True)
                removed.append(name)
        except Exception as e:  # noqa: BLE001 - e.g. a container still uses it: kept and reported
            reason = redact_secrets(str(getattr(e, "explanation", "") or e))[:300]
            if removed and names:   # Docker dropped these names before it kept the image at its last one
                reason += f"; its names {', '.join(removed)} were removed, the image itself stays"
            result.failed.append((item.speaking_name, reason))
            continue
        result.deleted.append(item.speaking_name)
        result.freed_bytes += item.size_bytes
    try:
        audit.record(actor, "doctor", "framework", "person", "error" if result.failed else "ok", approved=True,
                     arguments={"deleted": len(result.deleted), "kept": len(result.failed)},
                     detail=f"{len(result.deleted)} framework images deleted, {len(result.failed)} kept", extra=extra)
    except Exception:  # noqa: BLE001 - the deletion happened and is shown to the person; its start is recorded
        pass
    return result
