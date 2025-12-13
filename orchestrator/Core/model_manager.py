#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Model Manager (v2.3.0)
DIREKTIVE: Goldstandard, Modular & Data-Driven.

Zweck:
Verwaltet Downloads, Suche und Metadaten von KI-Modellen (Hugging Face).
Stellt sicher, dass Offline-Modelle (Tiny Models) verfügbar sind.
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

# Fallback Helper, falls utils.helpers noch nicht existiert
def ensure_directory(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

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
    def __init__(self, config_or_framework: Any):
        self.logger = get_logger("ModelManager")
        
        # --- Logic for handling Framework vs Config object ---
        # framework.py passes 'self.config' (ConfigManager), but code might expect FrameworkManager
        if hasattr(config_or_framework, 'config') and not isinstance(config_or_framework, dict):
            # It is FrameworkManager
            self.config = config_or_framework.config
            self.root_path = getattr(config_or_framework.info, 'installation_path', Path("."))
        else:
            # It is ConfigManager directly
            self.config = config_or_framework
            self.root_path = getattr(self.config, 'root_path', Path("."))

        # Paths
        # Use .get() for safety if config is ConfigManager class
        models_dir_name = self.config.get("models_dir", "models") if hasattr(self.config, "get") else getattr(self.config, "models_dir", "models")
        
        self.models_dir = self.root_path / models_dir_name
        self.tiny_models_dir = self.models_dir / "tiny_models"
        
        ensure_directory(self.models_dir)
        ensure_directory(self.tiny_models_dir)
        
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

    # --- TINY MODELS / OFFLINE INTELLIGENCE ---

    def get_available_tiny_models(self) -> Dict[str, Dict[str, Any]]:
        """Returns the list of tiny models defined in SSOT."""
        # Use .get() method if available (ConfigManager), else dictionary access
        if hasattr(self.config, 'get'):
            sources = self.config.get("source_repositories", {})
        else:
            sources = getattr(self.config, "source_repositories", {})
            
        return sources.get("tiny_models", {})

    def is_tiny_model_installed(self, model_key: str) -> bool:
        """Checks if the specific tiny model is already downloaded."""
        tiny_defs = self.get_available_tiny_models()
        if model_key not in tiny_defs: return False
        
        repo_url = tiny_defs[model_key].get("url", "")
        repo_id = repo_url.replace("https://huggingface.co/", "")
        
        # Check if local directory exists in tiny_models_dir
        local_dir = self.tiny_models_dir / model_key
        # Simple check: Does folder exist and have content?
        if local_dir.exists() and (local_dir / "config.json").exists():
            return True
            
        return False

    def download_tiny_model(self, model_key: str) -> Optional[str]:
        """
        Downloads a full Tiny Model (Repo Snapshot) for offline usage.
        Used by AI Setup Wizard.
        """
        tiny_defs = self.get_available_tiny_models()
        if model_key not in tiny_defs:
            self.logger.error(f"Unknown tiny model key: {model_key}")
            return None

        repo_url = tiny_defs[model_key].get("url", "")
        repo_id = repo_url.replace("https://huggingface.co/", "")
        
        self.logger.info(f"Starting download for Offline Brain: {repo_id}...")
        
        try:
            from huggingface_hub import snapshot_download
            
            # Download into a specific subfolder
            local_dir = self.tiny_models_dir / model_key
            
            path = snapshot_download(
                repo_id=repo_id,
                local_dir=str(local_dir),
                local_dir_use_symlinks=False, # Real files for true offline portability
                resume_download=True
            )
            
            self.logger.info(f"Tiny Model installed to: {path}")
            return str(path)
            
        except Exception as e:
            self.logger.error(f"Tiny Model download failed: {e}")
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
