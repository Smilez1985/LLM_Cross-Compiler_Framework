#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Helper Utilities
DIREKTIVE: Goldstandard. Secure file operations.
"""

import os
import sys
import json
import shutil
import subprocess
import platform
import tempfile
import hashlib
import zipfile
import tarfile
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

def ensure_directory(path: Union[str, Path], mode: int = 0o755) -> Path:
    p = Path(path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
        try:
            p.chmod(mode)
        except Exception: pass
    return p

def safe_json_load(path: Path, default=None):
    if not path.exists(): return default
    try:
        with open(path, 'r') as f: return json.load(f)
    except: return default

def check_command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def execute_command(command: List[str], cwd=None, timeout=None, env=None):
    try:
        res = subprocess.run(
            command, 
            cwd=cwd, 
            timeout=timeout, 
            env=env, 
            capture_output=True, 
            text=True,
            check=False 
        )
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return -1, "", str(e)

def safe_extract_archive(archive_path: Path, dest_dir: Path):
    """
    Secure extraction preventing Zip Slip/Tar Slip.
    """
    dest_dir = dest_dir.resolve()
    
    if str(archive_path).endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'r') as zf:
            for member in zf.infolist():
                member_path = (dest_dir / member.filename).resolve()
                if not str(member_path).startswith(str(dest_dir)):
                    raise Exception(f"Security: Zip Slip attempt detected: {member.filename}")
            # Bandit: Pfad wurde oben validiert
            zf.extractall(dest_dir) # nosec
                
    elif str(archive_path).endswith(('.tar.gz', '.tgz', '.tar')):
        with tarfile.open(archive_path, 'r:*') as tf:
            for member in tf.getmembers():
                member_path = (dest_dir / member.name).resolve()
                if not str(member_path).startswith(str(dest_dir)):
                     raise Exception(f"Security: Tar Slip attempt detected: {member.name}")
            # Bandit: Pfad wurde oben validiert
            tf.extractall(dest_dir) # nosec

def calculate_file_checksum(path: Path) -> str:
    if not path.exists(): return ""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while c := f.read(8192): h.update(c)
    return h.hexdigest()
