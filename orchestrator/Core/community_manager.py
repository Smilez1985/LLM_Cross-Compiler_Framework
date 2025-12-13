#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Community Manager (v2.3.0)
DIREKTIVE: Goldstandard, Swarm Memory Integration.

Verwaltet Community-Module und den 'Swarm Memory' (Shared RAG).
Stellt sicher, dass User-Targets geschützt bleiben.

UPDATES v2.3.0:
- SwarmCipher: Full Implementation (Dynamic Key + Integrity Hash).
- Git Integration: Push/Pull auf 'Edge-LLM-Knowledge-Base'.
- Robust Path Handling & Config Safety.
"""

import shutil
import logging
import yaml
import zipfile
import json
import re
import os
import hashlib
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from orchestrator.utils.logging import get_logger

# Fallback Helper
def ensure_directory(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

# --- SWARM CIPHER (The "Bluff" Security v2) ---
class SwarmCipher:
    """
    Implements 'Security by Obscurity' for the Swarm Memory.
    Technique: 
    1. Dynamic Key Gen (Static Base + Content Length).
    2. XOR Obfuscation.
    3. Unicode Symbol Mapping (Wingdings-style).
    """
    BASE_KEY = "DITTO_SWARM_CORE_V1"
    OFFSET = 0x2200 

    @staticmethod
    def _generate_dynamic_key(length: int) -> bytes:
        """Erzeugt einen dynamischen Key basierend auf der Länge."""
        raw = f"{SwarmCipher.BASE_KEY}_{length}"
        return hashlib.sha256(raw.encode('utf-8')).digest()

    @staticmethod
    def encrypt(text: str) -> Dict[str, str]:
        """
        Text -> XOR (Dynamic) -> Symbols.
        Returns: { "payload": symbols, "hash": sha256_plaintext }
        """
        # 1. Integrity Hash
        data = text.encode('utf-8')
        sha_hash = hashlib.sha256(data).hexdigest()
        
        # 2. Dynamic Key
        key_bytes = SwarmCipher._generate_dynamic_key(len(data))
        
        # 3. XOR
        xored = bytearray()
        for i, b in enumerate(data):
            xored.append(b ^ key_bytes[i % len(key_bytes)])
        
        # 4. Map to Symbols
        payload = "".join([chr(b + SwarmCipher.OFFSET) for b in xored])
        
        return {"payload": payload, "hash": sha_hash}

    @staticmethod
    def decrypt(symbols: str, expected_hash: str = None) -> str:
        """Symbol String -> Bytes -> XOR (Dynamic) -> Text + Verify"""
        try:
            # 1. Map symbols back to bytes
            xored = bytearray()
            for char in symbols:
                val = ord(char) - SwarmCipher.OFFSET
                if val < 0: raise ValueError("Invalid Swarm Char") # Simple bounds check
                # Note: val > 255 check removed to allow wider unicode mapping if needed, 
                # but standard byte mapping implies 0-255 range for reconstruction.
                if val > 255: raise ValueError("Invalid Byte Value")
                xored.append(val)
            
            # 2. Re-Generate Dynamic Key (Length is implicit from data)
            key_bytes = SwarmCipher._generate_dynamic_key(len(xored))
            
            # 3. XOR Reverse
            decrypted = bytearray()
            for i, b in enumerate(xored):
                decrypted.append(b ^ key_bytes[i % len(key_bytes)])
                
            text = decrypted.decode('utf-8')
            
            # 4. Verify Integrity
            if expected_hash:
                curr_hash = hashlib.sha256(decrypted).hexdigest()
                if curr_hash != expected_hash:
                    raise ValueError("Integrity Check Failed! (Hash Mismatch)")
            
            return text
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")

@dataclass
class CommunityModule:
    id: str
    name: str
    description: str
    architecture: str
    author: str
    version: str
    path: Path
    is_installed: bool = False

class CommunityManager:
    SWARM_REPO_URL = "https://github.com/Smilez1985/Edge-LLM-Knowledge-Base.git"

    def __init__(self, framework_manager):
        self.logger = get_logger("CommunityManager")
        self.framework = framework_manager
        
        # Robust Path & Config Resolution
        if hasattr(framework_manager, 'info') and hasattr(framework_manager.info, 'installation_path'):
             self.app_root = Path(framework_manager.info.installation_path)
        else:
             self.app_root = Path(".").resolve()
             
        # Config Access
        self.config = getattr(framework_manager, 'config', None)

        self.community_dir = self.app_root / "community"
        self.knowledge_dir = self.community_dir / "knowledge"
        self.targets_dir = self.app_root / "targets"
        self.contribution_dir = self.app_root / "contributions"
        
        ensure_directory(self.community_dir)
        ensure_directory(self.knowledge_dir)
        ensure_directory(self.contribution_dir)

    # --- MODULE MANAGEMENT ---
    def scan_modules(self) -> List[CommunityModule]:
        modules = []
        if not self.community_dir.exists(): return []
        
        # Fallback if targets_dir doesn't exist yet
        if not self.targets_dir.exists():
            return []
            
        installed_targets = {p.name for p in self.targets_dir.iterdir() if p.is_dir()}
        
        for item in self.community_dir.iterdir():
            if item.is_dir() and (item / "target.yml").exists():
                try:
                    with open(item / "target.yml", 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f)
                        meta = data.get('metadata', {})
                    mod = CommunityModule(
                        id=item.name, name=meta.get('name', item.name),
                        description=meta.get('description', 'No description'),
                        architecture=meta.get('architecture_family', 'unknown'),
                        author=meta.get('maintainer', 'Community'),
                        version=meta.get('version', '1.0.0'),
                        path=item, is_installed=(item.name in installed_targets)
                    )
                    modules.append(mod)
                except Exception as e: self.logger.error(f"Error loading module {item.name}: {e}")
        return sorted(modules, key=lambda x: x.name)

    def install_module(self, module_id: str) -> bool:
        source = self.community_dir / module_id
        dest = self.targets_dir / module_id
        if dest.exists(): return False
        try: shutil.copytree(source, dest); return True
        except Exception as e:
            if dest.exists(): shutil.rmtree(dest)
            self.logger.error(f"Install failed: {e}")
            raise e

    # ============================================================================
    # KNOWLEDGE BASE SYNC (Swarm Decrypt)
    # ============================================================================
    def sync_knowledge_base(self) -> int:
        # Check Framework for RAG component
        if not self.framework or not hasattr(self.framework, 'get_component'):
            return 0
            
        rag_manager = self.framework.get_component("rag_manager")
        if not rag_manager: return 0
        
        imported_count = 0
        
        for json_file in self.knowledge_dir.glob("*.json"):
            try:
                if json_file.with_suffix(".imported").exists(): continue
                self.logger.info(f"Syncing {json_file.name}...")
                with open(json_file, 'r', encoding='utf-8') as f: data = json.load(f)
                
                # --- SWARM DECRYPTION ---
                if isinstance(data, dict) and data.get("swarm_encrypted", False):
                    try:
                        # Pass Hash for Validation
                        raw_payload = SwarmCipher.decrypt(data["payload"], data.get("hash"))
                        data = json.loads(raw_payload)
                    except Exception as e:
                        self.logger.error(f"Decryption/Integrity failed for {json_file.name}: {e}")
                        continue
                # ------------------------

                if isinstance(data, list):
                    for entry in data:
                        meta = entry.get("metadata", {})
                        meta["origin_pack"] = json_file.name
                        if rag_manager.ingest_document(entry.get("source", ""), entry.get("content", ""), meta):
                            imported_count += 1
                
                with open(json_file.with_suffix(".imported"), 'w') as f: f.write(datetime.now().isoformat())
            except Exception as e: self.logger.error(f"Sync failed {json_file.name}: {e}")
        return imported_count

    def export_knowledge_base(self, output_name_prefix: str = "knowledge_export") -> Optional[str]:
        if not self.framework or not hasattr(self.framework, 'get_component'):
            return None
            
        rag_manager = self.framework.get_component("rag_manager")
        if not rag_manager or not rag_manager._connect(): return None
        
        all_points = []
        next_offset = None
        try:
            while True:
                rec, next_offset = rag_manager.client.scroll("framework_knowledge", limit=100, offset=next_offset, with_payload=True, with_vectors=False)
                all_points.extend(rec)
                if next_offset is None: break
        except: return None

        export_data = []
        for point in all_points:
            p = point.payload or {}
            c = self._sanitize_content(p.get("content", ""))
            s = self._sanitize_content(p.get("source", ""))
            if c: export_data.append({"source": s, "content": c, "metadata": {k:v for k,v in p.items() if k not in ["content", "source"]}})

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = self.knowledge_dir / f"{output_name_prefix}_{ts}.json"
        with open(export_path, 'w', encoding='utf-8') as f: json.dump(export_data, f, indent=2)
        return str(export_path)

    # ============================================================================
    # SWARM UPLOAD (Full Implementation)
    # ============================================================================
    def upload_knowledge_to_swarm(self, export_file: str, github_token: str, username: str = "CommunityUser") -> bool:
        file_path = Path(export_file)
        if not file_path.exists(): return False

        # A. Encryption & Hashing
        try:
            with open(file_path, 'r', encoding='utf-8') as f: raw_json = f.read()
            
            result = SwarmCipher.encrypt(raw_json)
            
            swarm_packet = {
                "swarm_encrypted": True,
                "version": "v1.1", # Bumped version
                "timestamp": datetime.now().isoformat(),
                "contributor": username,
                "hash": result["hash"], # Public Hash for Integrity
                "payload": result["payload"]
            }
            enc_filename = f"swarm_{file_path.name}"
        except Exception as e:
            self.logger.error(f"Encryption failed: {e}")
            return False

        # B. Git Push
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / "swarm_repo"
            
            # Sanitize URL for logging vs usage
            auth_url = self.SWARM_REPO_URL.replace("https://", f"https://{username}:{github_token}@")
            
            try:
                subprocess.run(["git", "clone", "--depth", "1", auth_url, str(repo_dir)], check=True, capture_output=True)
                dest = repo_dir / "contributions" / enc_filename
                ensure_directory(dest.parent)
                
                with open(dest, 'w', encoding='utf-8') as f: json.dump(swarm_packet, f, indent=2)
                
                # Configure Git safely locally
                subprocess.run(["git", "config", "user.email", "ditto@framework.local"], cwd=repo_dir, check=True)
                subprocess.run(["git", "config", "user.name", username], cwd=repo_dir, check=True)
                
                subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
                subprocess.run(["git", "commit", "-m", f"feat: Contribution from {username}"], cwd=repo_dir, check=True)
                subprocess.run(["git", "push"], cwd=repo_dir, check=True)
                
                self.logger.info("✅ Upload successful!")
                return True
            except Exception as e:
                self.logger.error(f"Upload failed: {e}")
                return False

    def _sanitize_content(self, text: str) -> str:
        if not text: return ""
        text = re.sub(r'(sk-[a-zA-Z0-9]{20,})', 'sk-REDACTED', text)
        text = re.sub(r'(hf_[a-zA-Z0-9]{20,})', 'hf_REDACTED', text)
        return text
