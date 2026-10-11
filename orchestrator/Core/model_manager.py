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

import os
import sys
import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from datetime import datetime
import time

import requests
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
        if hasattr(config_or_framework, 'config') and not isinstance(config_or_framework, dict):
            # It is FrameworkManager: relative paths resolve against the installation, never the working
            # directory (the CLI and the MCP server are started inside arbitrary projects).
            framework = config_or_framework
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

    def search_huggingface_models(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            from huggingface_hub import HfApi
            api = HfApi()
            # Expand tags to find license info if possible
            models = api.list_models(
                search=query, 
                limit=limit, 
                sort="downloads", 
                direction=-1,
                expand=["gated", "downloads", "likes", "tags"] 
            )
            
            results = []
            for m in models:
                is_gated = getattr(m, "gated", False)
                # Normalize gated status
                if is_gated not in [False, None]: is_gated = True
                else: is_gated = False
                
                # Extract license from tags (heuristik)
                license_tag = "unknown"
                if m.tags:
                    for tag in m.tags:
                        if tag.startswith("license:"):
                            license_tag = tag.split(":")[1]
                            break
                    
                results.append({
                    "id": m.modelId,
                    "downloads": m.downloads,
                    "likes": m.likes,
                    "gated": is_gated,
                    "license": license_tag
                })
            return results

        except ImportError:
            self.logger.error("huggingface_hub not installed")
            return []
        except Exception as e:
            self.logger.error(f"HF Search failed: {e}")
            return []
            
    # --- v2.3.0: ETHICS GATE (License Check) ---
    def check_license(self, model_id: str) -> Dict[str, Any]:
        """
        Checks the license of a model and returns warnings if restrictive.
        Used by GUI before starting download/build.
        """
        self.logger.info(f"Checking license for {model_id}...")
        
        try:
            from huggingface_hub import model_info
            info = model_info(model_id)
            
            # Try to find license in cardData or tags
            license_name = "unknown"
            if info.cardData and "license" in info.cardData:
                license_name = info.cardData["license"]
            elif info.tags:
                for tag in info.tags:
                    if tag.startswith("license:"):
                        license_name = tag.split(":")[1]
                        break
            
            # Analysis of restrictive keywords
            restrictive_keywords = [
                "non-commercial", "cc-by-nc", "research-only", 
                "llama-2-community", "creativeml-openrail-m"
            ]
            is_restrictive = any(k in license_name.lower() for k in restrictive_keywords)
            
            result = {
                "license": license_name,
                "is_restrictive": is_restrictive,
                "message": ""
            }
            
            if is_restrictive:
                result["message"] = f"⚠️ Warning: Model '{model_id}' has a restrictive license ({license_name}). Check usage rights!"
                self.logger.warning(result["message"])
            else:
                result["message"] = f"✅ License seems permissible: {license_name}"
                
            return result

        except Exception as e:
            self.logger.warning(f"Could not verify license for {model_id}: {e}")
            return {"license": "unknown", "is_restrictive": False, "message": "License check failed."}

    def list_repo_files(self, repo_id: str, token: Optional[str] = None) -> List[str]:
        """Fetches file list from a repository."""
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=token)
            files = api.list_repo_files(repo_id=repo_id)
            return files
        except Exception as e:
            self.logger.error(f"Failed to list files for {repo_id}: {e}")
            return []

    def download_file(self, repo_id: str, filename: str, token: Optional[str] = None) -> Optional[str]:
        """Downloads a specific file (GGUF/Bin) from HF."""
        try:
            from huggingface_hub import hf_hub_download
            self.logger.info(f"Downloading {filename} from {repo_id}...")
            
            path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                cache_dir=str(self.models_dir),
                token=token
            )
            self.logger.info(f"Download successful: {path}")
            return path
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return None

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
