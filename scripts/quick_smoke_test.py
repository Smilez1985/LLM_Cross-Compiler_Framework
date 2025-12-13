#!/usr/bin/env python3
"""
LLM Framework - Local Smoke Test (Quick Check v2.3.0)
DIREKTIVE: Schnelltest für Entwickler. Prüft Umgebung und Core-Funktionen.

Usage:
    python scripts/quick_smoke_test.py
"""

import sys
import os
import shutil
import subprocess
import platform
from pathlib import Path

def print_status(step, success, msg=""):
    icon = "✅" if success else "❌"
    color_start = "\033[92m" if success else "\033[91m"
    color_end = "\033[0m"
    print(f"{icon} [{step}] {color_start}{msg}{color_end}")
    if not success:
        print(f"   FATAL: Test '{step}' failed. Aborting.")
        sys.exit(1)

def run_smoke_test():
    print("🚀 Starting Local Smoke Test (v2.3.0)...")
    
    # Repo Root sicher ermitteln (angenommen scripts/ ist 1 Level unter Root)
    script_path = Path(__file__).resolve()
    repo_root = script_path.parent.parent
    os.chdir(repo_root)
    
    print(f"📂 Root: {repo_root}")
    
    # 1. Environment Check
    print("\n[1] Checking Environment:")
    
    # Python Version
    py_ver = sys.version_info
    print_status("Python Version", py_ver >= (3, 10), f"Found {py_ver.major}.{py_ver.minor}.{py_ver.micro}")
    
    # Docker
    try:
        docker_check = subprocess.run(["docker", "--version"], capture_output=True, text=True, check=False)
        print_status("Docker CLI", docker_check.returncode == 0, f"Found: {docker_check.stdout.strip()}")
    except FileNotFoundError:
        print_status("Docker CLI", False, "Docker command not found in PATH")
    
    # Git
    try:
        git_check = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
        print_status("Git CLI", git_check.returncode == 0, f"Found: {git_check.stdout.strip()}")
    except FileNotFoundError:
        print_status("Git CLI", False, "Git command not found in PATH")

    # 2. Dependency Integrity
    print("\n[2] Checking Dependencies:")
    try:
        import PySide6
        import docker
        import yaml
        import requests
        import paramiko
        print_status("Imports", True, "Core libraries (PySide6, docker, yaml, requests, paramiko) loadable")
    except ImportError as e:
        print_status("Imports", False, f"Missing dependency: {e} (Run 'pip install -r requirements.txt')")

    # 3. File Structure
    print("\n[3] Checking File Structure:")
    # Kritische Dateien für v2.3.0
    required_files = [
        "orchestrator/main.py",
        "orchestrator/core/framework.py",
        "orchestrator/gui/main_window.py",
        "orchestrator/utils/logging.py",
        "scripts/hardware_probe.py", # Oder .ps1/.sh, aber wir suchen generisch
        "targets/profiles"
    ]
    
    # Platform specific checks
    if platform.system() == "Windows":
        required_files.append("scripts/setup_windows.py")
    
    for f_rel in required_files:
        f_path = repo_root / f_rel
        # Check if file exists OR if directory exists (for targets/profiles)
        exists = f_path.exists()
        
        # Sonderfall hardware_probe: Prüfe auf .sh ODER .ps1
        if "hardware_probe" in f_rel:
            exists = (repo_root / "scripts/hardware_probe.sh").exists() or \
                     (repo_root / "scripts/hardware_probe.ps1").exists()
        
        print_status(f"File: {f_rel}", exists, "Found" if exists else "Missing")

    # 4. Running Unit Tests
    print("\n[4] Running Unit Tests (pytest):")
    # Wir nutzen subprocess, um pytest isoliert zu starten
    test_cmd = [sys.executable, "-m", "pytest", "tests/"]
    
    # Prüfen ob tests ordner existiert
    if (repo_root / "tests").exists():
        print("   Running pytest... (this might take a moment)")
        result = subprocess.run(test_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print_status("Unit Tests", True, "All tests passed")
        else:
            print("   ⚠️  Tests failed. Output:")
            print(result.stdout)
            print(result.stderr)
            # Smoke Test ist "soft", failt hier nicht hart, damit man debuggen kann
            print_status("Unit Tests", False, "Some tests failed (Non-Fatal for Smoke Test)")
    else:
        print("⚠️  Ordner 'tests/' fehlt. Überspringe Unit-Tests.")

    print("\n🎉 SMOKE TEST PASSED! System looks ready for v2.3.0 launch.")

if __name__ == "__main__":
    run_smoke_test()
