#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - What a build of a Hugging Face model would fetch (MCP design 2.3, 6.5, 6.6 rule 12)

Looked up once, before the person is asked: the commit the default branch points to now (the build then fetches
exactly that commit, G30), the files the build stages with their sizes (the same filter the build engine uses), the
weights type (safetensors, pickle, gguf), the gating, the licence and whether the token stored in the framework can
read the repository. The token is read inside the framework only and passed to huggingface_hub explicitly; without
a stored token the calls pass token=False, so a token the person never gave the framework (HF_TOKEN, the Hugging Face
home folder's token file) is never sent (G37). Nothing here returns or logs the token.
"""

import fnmatch
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

# One licence allowlist and keyword list, one gating map and one file record for every Hugging Face path
# (builds here, get_hf_model_info and download_hf_file in hf_hub), so a model is classified the same everywhere.
from orchestrator.Core.hf_hub import HubFile, classify_license, gating
from orchestrator.utils.helpers import redact_secrets

# What the build engine leaves out when it stages a repository (one list for staging and for the size look-up).
STAGE_IGNORE = ("*.gguf", "*.onnx", "*.onnx_data", "*.h5", "*.msgpack", "*.ot", "*.tflite", "original/*", "onnx/*")
PICKLE_PATTERNS = ("*.bin", "*.pt", "*.pth")


class HubLookupError(ValueError):
    """A look-up the framework could not do (network, unknown repository, no access), with a reason code."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


TOKEN_NEXT = ("Tell the person: accept the model's conditions on huggingface.co with their account, then store a "
              "token with 'llm-cli secrets set hf_token' or in the GUI. The token never passes through an AI.")


def staged_files(files: List[str]) -> List[str]:
    """The files the build engine stages from a repository: no GGUF/ONNX/TF copies, and no pickle weights when
    safetensors exist (the same weights twice otherwise)."""
    ignore = list(STAGE_IGNORE)
    if any(f.endswith(".safetensors") for f in files):
        ignore += list(PICKLE_PATTERNS)
    return [f for f in files if not any(fnmatch.fnmatch(f, pattern) for pattern in ignore)]


def weights_type(files: List[str]) -> str:
    names = [f.lower() for f in files]
    if any(n.endswith(".safetensors") for n in names):
        return "safetensors"
    if any(n.endswith((".bin", ".pt", ".pth")) for n in names):
        return "pickle"
    if any(n.endswith(".gguf") for n in names):
        return "gguf"
    return "none"


def license_class(license_name: Any) -> Tuple[str, str]:
    """(licence as text, permissive|restrictive|unknown), from hf_hub.classify_license: permissive only on the
    allowlist; a list-valued, missing, `other` or custom licence is unknown."""
    shown, restrictive = classify_license(license_name)
    return shown, {"false": "permissive", "true": "restrictive"}.get(restrictive, "unknown")


@dataclass
class ModelFacts:
    """What the framework looked up for one repository (repository text is untrusted: licence names)."""
    repo: str
    commit: Optional[str]
    files: List[HubFile] = field(default_factory=list)
    staged: List[HubFile] = field(default_factory=list)
    weights: str = "none"
    gated: str = "false"          # false, auto, manual
    license: str = "unknown"
    license_class: str = "unknown"
    token_access: str = "not_needed"  # yes, no, no_token, not_needed
    parameters: Optional[int] = None
    base_model: Optional[str] = None
    # Whether the base model the card names is gated: False without a base model, None when it could not be looked up
    base_gated: Optional[bool] = False

    @property
    def download_bytes(self) -> int:
        return sum(f.size or 0 for f in self.staged)

    @property
    def sizes_known(self) -> bool:
        return all(f.size is not None for f in self.staged)


MAX_BASE_MODELS = 8  # base models looked up for gating; a card naming more is outside every standing approval


def look_up(repo: str, revision: Optional[str], token: Optional[str], api: Any = None) -> ModelFacts:
    """The facts of a build's Hugging Face model. `token`: the token stored in the framework, or None (then
    token=False goes to every call: never an implicit token). HubLookupError with a reason code when the repository
    cannot be read; a gated repository the stored token cannot read is refused here, before any question:
    needs_person without a stored token, permission_denied with one that has no access."""
    from huggingface_hub import HfApi
    from huggingface_hub.utils import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError
    api = api or HfApi()
    auth = token or False
    try:
        info = api.model_info(repo, revision=revision, files_metadata=True, token=auth)
    except GatedRepoError:
        info = None  # gated and this token (or none) cannot see the file list: decided below
    except RepositoryNotFoundError:
        raise HubLookupError(f"There is no Hugging Face model '{repo}' the framework can read (it does not exist, or "
                             "it is private and the stored token has no access).", code="invalid_value",
                             next="Check the repo id (owner/name), for example with search_hf_models.") from None
    except HfHubHTTPError as e:
        raise HubLookupError(f"Hugging Face could not be asked about '{repo}': {redact_secrets(str(e))[:300]}",
                             code="failed", next="Try again later; tell the person if it keeps failing.") from None
    except Exception as e:  # network down, DNS
        raise HubLookupError(f"Hugging Face could not be reached: {redact_secrets(str(e))[:300]}", code="failed",
                             next="Check the network, or tell the person; the framework may be offline.") from None
    if info is None:
        if not token:
            raise HubLookupError(f"'{repo}' is a gated model, and no Hugging Face token is stored in the framework.",
                                 code="needs_person", next=TOKEN_NEXT)
        raise HubLookupError(f"'{repo}' is a gated model, and the Hugging Face token stored in the framework has no "
                             "access to it.", code="permission_denied", next=TOKEN_NEXT)
    gated = gating(getattr(info, "gated", False))
    card = getattr(info, "card_data", None) or getattr(info, "cardData", None) or {}
    get = card.get if hasattr(card, "get") else (lambda k, d=None: getattr(card, k, d))
    license_name = get("license", None)
    if license_name is None:
        license_name = next((t.split(":", 1)[1] for t in (getattr(info, "tags", None) or [])
                             if isinstance(t, str) and t.startswith("license:")), None)
    shown, klass = license_class(license_name)
    base = get("base_model", None)
    bases = [b for b in (base if isinstance(base, (list, tuple)) else [base]) if b is not None]
    base = bases[0] if bases else None
    files = [HubFile(s.rfilename, getattr(s, "size", None),
                     getattr(getattr(s, "lfs", None), "sha256", None) if getattr(s, "lfs", None) else None)
             for s in (getattr(info, "siblings", None) or [])]
    names = [f.name for f in files]
    keep = set(staged_files(names))
    safetensors = getattr(info, "safetensors", None)
    parameters = getattr(safetensors, "total", None) if safetensors is not None else None
    if parameters is None and isinstance(safetensors, dict):
        parameters = safetensors.get("total")
    commit = getattr(info, "sha", None)
    facts = ModelFacts(repo=repo, commit=commit if isinstance(commit, str) and len(commit) == 40 else None,
                       files=files, staged=[f for f in files if f.name in keep], weights=weights_type(list(keep)),
                       gated=gated, license=str(shown)[:200], license_class=klass,
                       parameters=int(parameters) if isinstance(parameters, int) and parameters > 0 else None,
                       base_model=str(base)[:200] if isinstance(base, str) else None)
    # Every base model the card names (a merge names several): one gated or unknown one is enough to keep the call
    # outside every standing approval.
    others = [b for b in bases if b != repo]
    if any(not isinstance(b, str) for b in others) or len(others) > MAX_BASE_MODELS:
        facts.base_gated = None
    else:
        for name in others:
            try:
                gated_base = gating(getattr(api.model_info(str(name)[:200], token=auth), "gated", False)) != "false"
            except Exception:  # noqa: BLE001 - unknown: outside every standing approval
                facts.base_gated = None
                break
            if gated_base:
                facts.base_gated = True
                break
    if gated != "false":
        if not token:
            facts.token_access = "no_token"
        else:
            try:
                api.auth_check(repo, token=token)
                facts.token_access = "yes"
            except (GatedRepoError, RepositoryNotFoundError):
                facts.token_access = "no"
            except Exception:  # noqa: BLE001 - the check itself failed: not proven, so no access
                facts.token_access = "no"
        if facts.token_access == "no_token":
            raise HubLookupError(f"'{repo}' is a gated model ({gated} approval on huggingface.co), and no Hugging Face "
                                 "token is stored in the framework.", code="needs_person", next=TOKEN_NEXT)
        if facts.token_access == "no":
            raise HubLookupError(f"'{repo}' is a gated model ({gated} approval on huggingface.co), and the token "
                                 "stored in the framework has no access to it.", code="permission_denied",
                                 next=TOKEN_NEXT)
    return facts
