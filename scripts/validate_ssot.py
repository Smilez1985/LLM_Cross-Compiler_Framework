#!/usr/bin/env python3
"""Validate project_sources.yml"""

import yaml
import sys
from pathlib import Path

def validate_ssot():
    """Validate production SSOT structure"""
    
    ssot_file = Path('project_sources.yml')
    
    if not ssot_file.exists():
        print("❌ project_sources.yml not found!")
        sys.exit(1)
    
    try:
        with open(ssot_file, 'r') as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"❌ YAML syntax error: {e}")
        sys.exit(1)
    
    print("✅ YAML syntax valid")
    
    # Check required top-level keys
    required_keys = ['metadata', 'build_tools', 'inference_backends', 'hardware_targets']
    missing = [k for k in required_keys if k not in data]
    
    if missing:
        print(f"❌ Missing required keys: {missing}")
        sys.exit(1)
    
    print("✅ All required keys present")
    
    # Check all entries have docs_workflow
    def check_docs(d, path=""):
        issues = []
        for k, v in d.items():
            current_path = f"{path}.{k}" if path else k
            if isinstance(v, dict):
                if 'repo_url' in v or 'url' in v:
                    if 'docs_workflow' not in v:
                        issues.append(f"Missing docs_workflow: {current_path}")
                issues.extend(check_docs(v, current_path))
        return issues
    
    issues = check_docs(data)
    
    if issues:
        print("⚠️  Warnings:")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print("✅ All entries have docs_workflow")
    
    print(f"\n✅ project_sources.yml is valid!")
    
    # Stats
    backend_count = len(data.get('inference_backends', {}))
    target_count = len(data.get('hardware_targets', {}))
    print(f"\nStats:")
    print(f"  - Inference backends: {backend_count}")
    print(f"  - Hardware targets: {target_count}")

if __name__ == '__main__':
    validate_ssot()
