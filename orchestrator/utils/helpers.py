#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Helper Utilities (v2.3.0)
DIREKTIVE: Goldstandard. Secure file operations.

Zweck:
Allgemeine Hilfsfunktionen für Dateioperationen, Sicherheit und System-Checks.
Vermeidet zirkuläre Abhängigkeiten durch strikte Trennung von Business-Logik.
"""

import os
import json
import shutil
import subprocess
import platform
import hashlib
import zipfile
import tarfile
import re
import ctypes
from pathlib import Path
from typing import List, Union

from orchestrator.security import secret_patterns

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
    dest_dir = Path(dest_dir).resolve()

    def inside(path: Path) -> bool:
        # Path containment, not string prefixes ("/x/out2" starts with "/x/out").
        return path.resolve().is_relative_to(dest_dir)

    if str(archive_path).endswith('.zip'):
        with zipfile.ZipFile(archive_path, 'r') as zf:
            for member in zf.infolist():
                if not inside(dest_dir / member.filename):
                    raise Exception(f"Security: Zip Slip attempt detected: {member.filename}")
            # Every member path was checked above.
            zf.extractall(dest_dir)  # nosec B202

    elif str(archive_path).endswith(('.tar.gz', '.tgz', '.tar')):
        with tarfile.open(archive_path, 'r:*') as tf:
            members = tf.getmembers()
            for member in members:
                target = dest_dir / member.name
                if not inside(target):
                    raise Exception(f"Security: Tar Slip attempt detected: {member.name}")
                if member.issym() and not inside(target.parent / member.linkname):
                    raise Exception(f"Security: Tar Slip via symlink detected: {member.name} -> {member.linkname}")
                if member.islnk() and not inside(dest_dir / member.linkname):
                    raise Exception(f"Security: Tar Slip via hardlink detected: {member.name} -> {member.linkname}")
                if member.isdev():
                    raise Exception(f"Security: Device file in archive: {member.name}")

            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest_dir, members=members, filter="data")
            else:
                # Without extraction filters, chained links could still escape; only plain files and dirs are allowed.
                links = [m.name for m in members if m.issym() or m.islnk()]
                if links:
                    raise Exception(f"Security: links in archive need Python >= 3.10.12 / 3.11.4: {links[:3]}")
                tf.extractall(dest_dir, members=members)  # nosec B202

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

# The one table of secret formats, shared with scripts/secret_scan.py.
SECRET_PATTERNS = secret_patterns.PATTERNS

def redact_secrets(text: str) -> str:
    """Replaces known API key/token formats, credential headers and query parameters before text leaves the
    machine or reaches a log."""
    if not text:
        return ""
    return secret_patterns.redact(text)

def sanitize_filename(name: str) -> str:
    """Removes illegal characters for filenames."""
    name = str(name).strip().replace(" ", "_")
    return re.sub(r'(?u)[^-\w.]', '', name)
