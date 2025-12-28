#!/usr/bin/env python3
"""
Sync project_sources.yml changes to detailed_project.sources.yml

Keeps basic structure in sync while preserving detailed content.
"""

import yaml
from pathlib import Path

def sync_ssot():
    """Sync production SSOT to detailed SSOT (links/commits only)"""
    
    prod_file = Path('project_sources.yml')
    detailed_file = Path('detailed_project.sources.yml')
    
    if not prod_file.exists():
        print("❌ project_sources.yml not found!")
        return
    
    if not detailed_file.exists():
        print("⚠️  detailed_project.sources.yml not found, creating from production...")
        detailed_file.write_text(prod_file.read_text())
        print("✅ Created detailed_project.sources.yml")
        return
    
    # Read both files
    with open(prod_file, 'r') as f:
        prod = yaml.safe_load(f)
    
    with open(detailed_file, 'r') as f:
        detailed = yaml.safe_load(f)
    
    # Sync metadata
    if 'metadata' in prod:
        if 'metadata' not in detailed:
            detailed['metadata'] = {}
        detailed['metadata']['ssot_version'] = prod['metadata'].get('ssot_version')
        detailed['metadata']['last_synced'] = prod['metadata'].get('last_updated')
    
    # Sync commits/versions (recursive)
    def sync_commits(prod_dict, detailed_dict):
        for key, value in prod_dict.items():
            if isinstance(value, dict):
                if key not in detailed_dict:
                    detailed_dict[key] = {}
                sync_commits(value, detailed_dict[key])
            elif key in ['commit', 'version', 'pypi_package', 'docker_tag']:
                # Update version info from production
                detailed_dict[key] = value
    
    sync_commits(prod, detailed)
    
    # Write back
    with open(detailed_file, 'w') as f:
        yaml.dump(detailed, f, default_flow_style=False, sort_keys=False)
    
    print("✅ Synced project_sources.yml → detailed_project.sources.yml")
    print("   - Updated: commits, versions, metadata")
    print("   - Preserved: detailed knowledge, examples")
    print("")
    print("⚠️  Review changes before committing:")
    print("   git diff detailed_project.sources.yml")

if __name__ == '__main__':
    sync_ssot()
