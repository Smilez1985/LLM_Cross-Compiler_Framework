#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Model Manager (v2.3.0)
DIREKTIVE: Goldstandard, Modular & Data-Driven.

Zweck:
Verwaltet Downloads, Suche und Metadaten von KI-Modellen (Hugging Face).
Fungiert als 'Ethics Gate' durch Prüfung von Lizenzen.

Updates v2.3.0:
- Constructor hardened to accept ConfigManager directly (fixes framework.py init).
- License check improvements.
"""

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass

from orchestrator.utils.logging import get_logger

@dataclass
class ModelMetadata:
    name: str
    source: str
    format: str
    model_type: str = ""
    size_bytes: int = 0
    license: str = "unknown"
    
    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)

class ModelManager:
    def __init__(self, config_or_framework: Any, base_dir: Optional[Path] = None):
        self.logger = get_logger("ModelManager")
        
        # --- Logic for handling Framework vs Config object ---
        framework = None
        self.framework = None  # the FrameworkManager when there is one (its vault token, its per-user folder)
        if hasattr(config_or_framework, 'config') and not isinstance(config_or_framework, dict):
            # It is FrameworkManager: relative paths resolve against the installation, never the working
            # directory (the CLI and the MCP server are started inside arbitrary projects).
            framework = config_or_framework
            self.framework = framework
            self.config = framework.config
            self.root_path = Path(getattr(getattr(framework, 'info', None), 'installation_path', None) or Path("."))
        else:
            # It is ConfigManager directly (standalone use): base_dir, else the working directory
            self.config = config_or_framework
            self.root_path = Path(base_dir or getattr(self.config, 'root_path', None) or Path("."))

        # Paths
        # Use .get() for safety if config is ConfigManager class
        models_dir_name = self.config.get("models_dir", "models") if hasattr(self.config, "get") else getattr(self.config, "models_dir", "models")
        models_dir_name = models_dir_name or "models"
        
        if framework is not None and callable(getattr(framework, "resolve_path", None)):
            self.models_dir = framework.resolve_path(models_dir_name)
        else:
            self.models_dir = self.root_path / Path(str(models_dir_name)).expanduser()
        # Created by the downloads that use it, not at startup.

    def initialize(self) -> bool:
        self.logger.info(f"Model Manager initialized. Storage: {self.models_dir}")
        return True

    # --- HUGGING FACE INTEGRATION ---

    def search_huggingface_models(self, query: str, limit: int = 20,
                                  gated: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Models matching `query`, most downloaded first. gated: False only models that download without accepting
        conditions, True only gated ones, None all. Each result keeps the gating mode (`gating`: false, auto,
        manual) next to the plain `gated` flag. Raises on any failure: an error is never an empty list."""
        from huggingface_hub import HfApi
        from orchestrator.Core.hf_hub import gating
        models = HfApi().list_models(search=query, limit=limit, sort="downloads", gated=gated,
                                     expand=["gated", "downloads", "likes", "tags"])
        results = []
        for m in models:
            mode = gating(getattr(m, "gated", False))
            license_tag = "unknown"
            for tag in (m.tags or []):
                if tag.startswith("license:"):
                    license_tag = tag.split(":", 1)[1]
                    break
            results.append({"id": m.id, "downloads": m.downloads, "likes": m.likes, "gated": mode != "false",
                            "gating": mode, "license": license_tag})
        return results

    # --- v2.3.0: ETHICS GATE (License Check) ---
    def check_license(self, model_id: str) -> Dict[str, Any]:
        """The licence of a model: `restrictive` is "false" only for a licence on the permissive allowlist, "true"
        for a known restrictive one and "unknown" otherwise (`other`, custom, a list, none); `is_restrictive` is
        True unless it is "false", so the GUI asks before such a download. Raises when the check fails: a failed
        check never counts as "not restrictive"."""
        from huggingface_hub import model_info
        from orchestrator.Core.hf_hub import classify_license
        self.logger.info(f"Checking license for {model_id}...")
        info = model_info(model_id)
        card = info.card_data
        raw = card.get("license") if card is not None and hasattr(card, "get") else None
        if raw is None:
            raw = next((t.split(":", 1)[1] for t in (info.tags or []) if t.startswith("license:")), None)
        license_name, restrictive = classify_license(raw)
        result = {"license": license_name, "restrictive": restrictive, "is_restrictive": restrictive != "false"}
        if restrictive == "true":
            result["message"] = (f"Warning: model '{model_id}' has a restrictive license ({license_name}). Check "
                                 "your usage rights!")
        elif restrictive == "unknown":
            result["message"] = (f"The license of '{model_id}' ({license_name}) is not on the list of permissive "
                                 "licenses; read it before you use the model.")
        else:
            result["message"] = f"Permissive license: {license_name}"
        return result

    def list_repo_files(self, repo_id: str, token: Optional[str] = None) -> List[str]:
        """The file list of a repository. Raises on any failure."""
        from huggingface_hub import HfApi
        return HfApi(token=token).list_repo_files(repo_id=repo_id)

    def download_file(self, repo_id: str, filename: str, token: Optional[str] = None,
                      progress: Optional[Callable[[str, int, int], None]] = None,
                      stop: Optional[threading.Event] = None) -> str:
        """Downloads one file for the GUI's Hugging Face browser (the person's own action) through the framework's
        verified fetcher, the same as an AI's download_hf_file (DECISIONS round 11 A6, hf_hub.download_for_person):
        the commit is resolved first and exactly that commit's file is fetched, resumable, checked against the Hub's
        size and sha256, into <models_dir>/hf/<owner>--<name>/<commit[:12]>/, under the one disk rule with its own
        reservation in the per-user ledger while it runs. `token`: the one the person typed, else the one stored in
        the framework; never an implicit one. `progress(name, done, total)` is called between chunks; `stop` ends it
        between two chunks and keeps the part already downloaded. Raises on any failure (HubError, a DownloadError,
        disk_plan.InsufficientSpace)."""
        from orchestrator.Core.hf_hub import download_for_person
        token = token or self._stored_token()
        self.logger.info(f"Downloading {filename} from {repo_id}...")
        path = download_for_person(self.models_dir, repo_id, filename, api=self._hub_api(token), token=token,
                                   ledger=self._ledger(), stop=stop, progress=progress)
        self.logger.info(f"Download successful: {path.name}")
        return str(path)

    # --- the old huggingface_hub cache (A6: listed and usable, an optional one-time Take over) ---

    def old_downloads(self):
        """The files the browser downloaded before A6 into huggingface_hub's cache inside the models folder
        (hf_takeover.old_cache), each with its state (new, taken, conflict)."""
        from orchestrator.Core.hf_takeover import old_cache
        return old_cache(self.models_dir)

    def take_over(self, files, stop: Optional[threading.Event] = None,
                  progress: Optional[Callable[[int, int, str], None]] = None):
        """Copies `files` of the old cache into the new folder, each checked (hf_takeover.take_over), under the one
        disk rule with its own reservation while it runs; the old copies stay."""
        from orchestrator.Core.hf_takeover import take_over
        return take_over(self.models_dir, files, ledger=self._ledger(), stop=stop, progress=progress,
                         lookup=self._hub_lookup())

    def old_copies_plan(self):
        """The deletion plan of the old cache's models that were taken over completely (hf_takeover), or None."""
        from orchestrator.Core.hf_takeover import old_copies_plan
        return old_copies_plan(self.models_dir)

    def delete_old_copies(self, accepted, trash=None, audit=None):
        """Removes exactly the old copies the person accepted in the deletion dialog (hf_takeover.delete_old_copies)."""
        from orchestrator.Core.hf_takeover import delete_old_copies
        return delete_old_copies(self.models_dir, accepted, trash=trash, audit=audit)

    # --- what the downloads use ---

    def _stored_token(self) -> Optional[str]:
        """The Hugging Face token stored in the framework's vault (hf_token), or None."""
        from orchestrator.Core.hf_hub import HubClient
        return HubClient(self.framework).token() if self.framework is not None else None

    def _hub_api(self, token: Optional[str]):
        """The Hub's API with `token` or no token at all (False: never HF_TOKEN or the Hugging Face home folder's
        token file), pinned to huggingface.co (hf_hub.hub_api: never the host in HF_ENDPOINT). A stand-in
        framework's `hf_api_factory` replaces it (tests)."""
        factory = getattr(self.framework, "hf_api_factory", None)
        if callable(factory):
            return factory(token or False)
        from orchestrator.Core.hf_hub import hub_api
        return hub_api(token)

    def _hub_lookup(self):
        """For the Take over: the files the Hub lists for one commit of a repository ({name: HubFile}), with the
        stored token; used only for a cached file whose checksum the old cache did not keep."""
        def lookup(repo: str, commit: str):
            from orchestrator.Core.hf_hub import hub_file
            token = self._stored_token()
            info = self._hub_api(token).model_info(repo, revision=commit, files_metadata=True, token=token or False)
            return {f.name: f for f in (hub_file(s) for s in getattr(info, "siblings", None) or [])}
        return lookup

    def _ledger(self):
        """The disk reservation ledger every process of this user shares (builds and downloads), or None without a
        per-user folder (a ConfigManager on its own)."""
        if self.framework is None:
            return None
        from orchestrator.Core.build_plan import state_dir
        from orchestrator.Core.disk_plan import ReservationLedger
        folder = state_dir(self.framework)
        return ReservationLedger(folder) if folder is not None else None

    def _detect_model_format(self, model_path: Path) -> str:
        if not model_path.exists(): return "unknown"
        if model_path.is_dir():
            if (model_path / "config.json").exists(): return "huggingface"
            return "directory"
        
        s = model_path.suffix.lower()
        if s == ".gguf": return "gguf"
        if s == ".onnx": return "onnx"
        if s == ".tflite": return "tflite"
        if s in [".pt", ".pth", ".bin"]: return "pytorch"
        if s == ".safetensors": return "safetensors"
        return "unknown"
