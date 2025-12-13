#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Helper Utilities (v2.3.0)
DIREKTIVE: Goldstandard. Secure file operations.

Zweck:
Allgemeine Hilfsfunktionen für Dateioperationen, Sicherheit und System-Checks.
Vermeidet zirkuläre Abhängigkeiten durch strikte Trennung von Business-Logik.
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
import re
import ctypes
from pathlib import Path
from typing import Dict, List, Optional, Any, Union

def ensure_directory(path: Union[str, Path], mode: int = 0o755) -> Path:
    """Creates directory if not exists."""
    p = Path(path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
        try:
            p.chmod(mode)
        except Exception: pass
    return p

def safe_json_load(path: Path, default=None):
    """Loads JSON with error suppression."""
    if not path.exists(): return default
    try:
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default

def check_command_exists(cmd: str) -> bool:
    """Checks if a binary is in PATH."""
    return shutil.which(cmd) is not None

def execute_command(command: List[str], cwd=None, timeout=None, env=None):
    """Executes a subprocess securely."""
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
    Secure extraction preventing Zip Slip/Tar Slip vulnerabilities.
    """
    dest_dir = dest_dir.resolve()
    
    if str(archive_path).endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'r') as zf:
            for member in zf.infolist():
                # Resolve the target path
                member_path = (dest_dir / member.filename).resolve()
                # Security Check: Must actully be inside dest_dir
                if not str(member_path).startswith(str(dest_dir)):
                    raise Exception(f"Security: Zip Slip attempt detected: {member.filename}")
            
            # Bandit: Paths validated above
            zf.extractall(dest_dir) # nosec
                
    elif str(archive_path).endswith(('.tar.gz', '.tgz', '.tar')):
        with tarfile.open(archive_path, 'r:*') as tf:
            for member in tf.getmembers():
                member_path = (dest_dir / member.name).resolve()
                if not str(member_path).startswith(str(dest_dir)):
                     raise Exception(f"Security: Tar Slip attempt detected: {member.name}")
            
            # Bandit: Paths validated above
            def is_within_directory(directory, target):
                abs_directory = os.path.abspath(directory)
                abs_target = os.path.abspath(target)
                prefix = os.path.commonprefix([abs_directory, abs_target])
                return prefix == abs_directory

            def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
                for member in tar.getmembers():
                    member_path = os.path.join(path, member.name)
                    if not is_within_directory(path, member_path):
                        raise Exception("Attempted Path Traversal in Tar File")
                tar.extractall(path, members, numeric_owner=numeric_owner) 
                
            safe_extract(tf, dest_dir)

def calculate_file_checksum(path: Path, algorithm="sha256") -> str:
    """Calculates hash of a file."""
    if not path.exists(): return ""
    h = hashlib.new(algorithm)
    with open(path, 'rb') as f:
        while c := f.read(8192): h.update(c)
    return h.hexdigest()

def is_admin() -> bool:
    """Checks for administrative privileges (Windows/Linux)."""
    try:
        if platform.system() == "Windows":
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        else:
            return os.geteuid() == 0
    except:
        return False

def sanitize_filename(name: str) -> str:
    """Removes illegal characters for filenames."""
    name = str(name).strip().replace(" ", "_")
    return re.sub(r'(?u)[^-\w.]', '', name)
