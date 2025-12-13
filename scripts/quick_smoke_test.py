#!/usr/bin/env python3
"""
LLM Framework - Local Smoke Test (Quick Check)
DIREKTIVE: Schnelltest für Entwickler. Prüft Umgebung und Core-Funktionen.
"""

import sys
import os
import shutil
import subprocess
import platform
from pathlib import Path

def print_status(step, success, msg=""):
    icon = "✅" if success else "❌"
    print(f"{icon} [{step}] {msg}")
    if not success:
        print(f"   FATAL: Test '{step}' failed. Aborting.")
        sys.exit(1)

def run_smoke_test():
    print("🚀 Starting Local Smoke Test...")
    repo_root = Path(__file__).parent.parent.resolve()
    os.chdir(repo_root)
    
    # 1. Environment Check
    print("\n[1] Checking Environment:")
    
    # Python Version
    py_ver = sys.version_info
    print_status("Python Version", py_ver >= (3, 10), f"Found {py_ver.major}.{py_ver.minor}")
    
    # Docker
    docker_check = subprocess.run(["docker", "--version"], capture_output=True)
    print_status("Docker CLI", docker_check.returncode == 0, "Docker command available")
    
    # Git
    git_check = subprocess.run(["git", "--version"], capture_output=True)
    print_status("Git CLI", git_check.returncode == 0, "Git command available")

    # 2. Dependency Integrity
    print("\n[2] Checking Dependencies:")
    try:
        import PySide6
        import docker
        import yaml
        print_status("Imports", True, "Core libraries (PySide6, docker, yaml) loadable")
    except ImportError as e:
        print_status("Imports", False, f"Missing dependency: {e}")

    # 3. File Structure
    print("\n[3] Checking File Structure:")
    required_files = [
        "orchestrator/main.py",
        "scripts/setup_windows.py",
        "targets/Rockchip/dockerfile",
        "configs/project_sources.yml"
    ]
    for f in required_files:
        exists = (repo_root / f).exists()
        print_status(f"File: {f}", exists)

    # 4. Running Unit Tests
    print("\n[4] Running Unit Tests (pytest):")
    # Wir nutzen subprocess, um pytest isoliert zu starten
    test_cmd = [sys.executable, "-m", "pytest", "tests/"]
    
    # Prüfen ob tests ordner existiert, wenn nicht, warnen aber nicht failen (falls noch nicht erstellt)
    if (repo_root / "tests").exists():
        result = subprocess.run(test_cmd, capture_output=False) # Output direkt zeigen
        print_status("Unit Tests", result.returncode == 0, "All tests passed")
    else:
        print("⚠️  Ordner 'tests/' fehlt. Überspringe Unit-Tests (Bitte tests/test_core.py erstellen!)")

    print("\n🎉 SMOKE TEST PASSED! System looks ready.")

if __name__ == "__main__":
    run_smoke_test()
