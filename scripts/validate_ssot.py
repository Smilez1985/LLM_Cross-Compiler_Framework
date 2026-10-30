#!/usr/bin/env python3
"""Validate configs/project_sources.yml (the source catalogue): its YAML, its top-level keys, the docs_workflow link of
every entry with an address, and the model suggestions (sections models and tiny_models, read by the Builder, the CLI
and the MCP resource lcf://models/suggestions: orchestrator/Core/doc_sources.model_problems). Exit code 1 on an error.

    python scripts/validate_ssot.py [PATH]
"""

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT = REPO_ROOT / "configs" / "project_sources.yml"
REQUIRED_KEYS = ['metadata', 'build_tools', 'inference_backends', 'hardware_targets']


def check_docs(d, path=""):
    """Entries with an address but no docs_workflow link (warnings)."""
    issues = []
    for k, v in d.items():
        current_path = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            if 'repo_url' in v or 'url' in v:
                if 'docs_workflow' not in v:
                    issues.append(f"Missing docs_workflow: {current_path}")
            issues.extend(check_docs(v, current_path))
    return issues


def validate_ssot(path: Path = DEFAULT) -> int:
    """Checks the catalogue at `path`; returns the exit code (0 valid, 1 an error)."""
    sys.path.insert(0, str(REPO_ROOT))
    from orchestrator.Core.doc_sources import model_problems

    if not path.exists():
        print(f"ERROR: {path.name} not found")
        return 1
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"ERROR: YAML syntax error: {e}")
        return 1
    if not isinstance(data, dict):
        print("ERROR: the catalogue is not a mapping")
        return 1
    print("YAML syntax valid")

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        print(f"ERROR: missing required keys: {missing}")
        return 1
    print("All required keys present")

    issues = check_docs(data)
    if issues:
        print("Warnings:")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print("All entries have docs_workflow")

    problems = model_problems(data)
    if problems:
        print("ERROR: the model suggestions are not valid:")
        for problem in problems:
            print(f"   - {problem}")
        return 1
    count = sum(len(data.get(s) or {}) for s in ("tiny_models", "models"))
    print(f"Model suggestions valid ({count} entries)")

    print(f"\n{path.name} is valid")
    print("\nStats:")
    print(f"  - Inference backends: {len(data.get('inference_backends', {}))}")
    print(f"  - Hardware targets: {len(data.get('hardware_targets', {}))}")
    return 0


if __name__ == '__main__':
    sys.exit(validate_ssot(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT))
