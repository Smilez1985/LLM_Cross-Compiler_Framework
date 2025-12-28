KORRIGIERT! 🎯

📋 SSOT_ARCHITECTURE.md (KORRIGIERT)
markdown# SSOT Architecture

## Overview

Das Framework verwendet ein **Zwei-File-System** für Wissensmanagement:

### 1. `project_sources.yml` - Production SSOT ⭐ PRIMARY
**Purpose:** Runtime configuration für Framework
**Used by:**
- Orchestrator (ENV variables)
- Crawler (Link discovery)
- Ditto (docs_workflow references)
- Build system

**Content:**
- Links zu Dokumentationen
- Commit hashes / Versions
- Integration Status
- Priority flags
- Kurze Notes (1-2 Sätze max)

**Rules:**
- ✅ Links only (kein detailliertes Wissen)
- ✅ Lightweight (schnell parsbar)
- ✅ Single source of truth für Production
- ❌ NO code examples
- ❌ NO detailed guides

### 2. `detailed_project.sources.yml` - Developer Reference
**Purpose:** Offline developer reference & Knowledge backup
**Used by:**
- Developers (ohne Internet)
- Onboarding (schneller Einstieg)
- Development (ohne RAG setup)
- Backup (falls RAG down)

**Content:**
- Alles aus `project_sources.yml` PLUS:
- Detaillierte Guides (Quantization, etc.)
- Code-Beispiele
- Best Practices
- Deployment Philosophien
- Performance Matrizen

**Rules:**
- ⚠️ NOT used by framework runtime
- ⚠️ Manual sync with project_sources.yml needed
- ✅ Can lag behind (reference only)
- ✅ Developer convenience

---

## File Structure Comparison
```
project_sources.yml (Production)          detailed_project.sources.yml (Reference)
├─ Links only                            ├─ Links PLUS detailed knowledge
├─ ~500 lines                            ├─ ~3000+ lines
├─ Fast parsing                          ├─ Comprehensive reference
└─ Framework reads this                  └─ Developers read this
```

---

## Update Strategy

### When updating framework:

**Step 1: Update `project_sources.yml` FIRST** ⭐
```bash
# Add new backend
vim project_sources.yml

# Example:
inference_backends:
  new_backend:
    repo_url: "https://github.com/..."
    commit: "v1.0.0"
    docs_workflow: "https://..."
    priority: "HIGH"
    integration_status: "PLANNED"
```

**Step 2: Commit Production SSOT**
```bash
git add project_sources.yml
git commit -m "Add new_backend to SSOT"
```

**Step 3: Update `detailed_project.sources.yml` (optional, later)**
```bash
# Sync basic structure
python scripts/sync_ssot.py

# Add detailed knowledge manually
vim detailed_project.sources.yml
# Add: quantization guides, code examples, etc.

git add detailed_project.sources.yml
git commit -m "Sync detailed SSOT + add backend knowledge"
```

### Priority:
- `project_sources.yml` = **CRITICAL** (framework breaks without)
- `detailed_project.sources.yml` = **NICE TO HAVE** (dev convenience)

---

## Use Cases

### Use `project_sources.yml` when:
- ✅ Running framework (production)
- ✅ Building targets
- ✅ Crawler operations
- ✅ Production deployments
- ✅ CI/CD pipelines
- ✅ Docker container builds

### Use `detailed_project.sources.yml` when:
- ✅ Offline development (no internet)
- ✅ RAG not set up yet
- ✅ Onboarding new developers
- ✅ Quick reference during coding
- ✅ Understanding architecture
- ✅ Learning quantization strategies
- ✅ Backup knowledge source

---

## File Precedence

**In case of conflict:**

1. **`project_sources.yml`** = Source of Truth (production)
2. **`detailed_project.sources.yml`** = Reference (may be outdated)
3. **RAG database** = Crawled knowledge (auto-updated from #1)

**If links/versions differ:**
- Trust `project_sources.yml`
- Update `detailed_project.sources.yml` to match
- RAG crawler uses `project_sources.yml`

---

## Workflow Examples

### Example 1: Adding New Hardware Target
```bash
# 1. Update production SSOT
vim project_sources.yml

# Add under hardware_targets:
hardware_targets:
  qualcomm:
    qnn_sdk:
      repo_url: "https://..."
      docs_workflow: "https://..."
      priority: "HIGH"
      integration_status: "PLANNED"

# 2. Commit
git add project_sources.yml
git commit -m "feat: Add Qualcomm QNN SDK to SSOT"

# 3. Framework can now use this
./orchestrator load_sources  # Reads project_sources.yml

# 4. Later: Add detailed knowledge
vim detailed_project.sources.yml
# Add: Quantization guides, code examples, etc.
git commit -m "docs: Add Qualcomm detailed knowledge"
```

### Example 2: Updating Backend Version
```bash
# 1. Update production SSOT
sed -i 's/commit: "v0.10.0"/commit: "v0.11.0"/' project_sources.yml

# 2. Commit
git add project_sources.yml
git commit -m "chore: Update TensorRT-LLM to v0.11.0"

# 3. Optional: Sync to detailed file
python scripts/sync_ssot.py
git add detailed_project.sources.yml
git commit -m "chore: Sync detailed SSOT versions"
```

### Example 3: Offline Development
```bash
# No internet? No problem!
# Use detailed file for all info

# Find quantization info
grep -A 50 "quantization:" detailed_project.sources.yml

# Find OpenVINO examples
grep -A 100 "openvino:" detailed_project.sources.yml

# No need for RAG or internet!
```

---

## Best Practices

### For Contributors:

**DO:**
- ✅ Always update `project_sources.yml` first
- ✅ Keep notes brief (1-2 sentences)
- ✅ Add ALL relevant docs_* links
- ✅ Pin commits/versions
- ✅ Test framework after SSOT changes

**DON'T:**
- ❌ Add detailed knowledge to `project_sources.yml`
- ❌ Add code examples to `project_sources.yml`
- ❌ Skip integration_status flag
- ❌ Use relative paths (always full URLs)

### For Maintainers:

**DO:**
- ✅ Validate `project_sources.yml` before release
- ✅ Run link checker regularly
- ✅ Keep detailed file synced (monthly)
- ✅ Document breaking changes in both files

**DON'T:**
- ❌ Let detailed file drift too far
- ❌ Duplicate knowledge between files and RAG
- ❌ Commit broken YAML

### For Users:

**DO:**
- ✅ Read `project_sources.yml` to understand framework
- ✅ Use `detailed_project.sources.yml` for learning
- ✅ Report broken links via issues

**DON'T:**
- ❌ Modify files manually (use scripts)
- ❌ Rely on detailed file for production
- ❌ Assume both files are always in sync

---

## Validation & Testing

### Pre-commit Checks
```bash
# 1. Validate YAML syntax
yamllint project_sources.yml
yamllint detailed_project.sources.yml

# 2. Check all links are reachable
python scripts/validate_ssot_links.py project_sources.yml

# 3. Verify commits/versions exist
python scripts/validate_commits.py project_sources.yml

# 4. Check drift between files
python scripts/check_ssot_drift.py
```

### CI/CD Pipeline
```yaml
# .github/workflows/validate-ssot.yml
name: Validate SSOT
on: [pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Validate YAML
        run: yamllint project_sources.yml
      
      - name: Check Links
        run: python scripts/validate_ssot_links.py
      
      - name: Verify Commits
        run: python scripts/validate_commits.py
```

---

## Troubleshooting

### "Framework can't find backend"

**Cause:** Not in `project_sources.yml`
**Fix:**
```bash
# Add to project_sources.yml
# NOT to detailed_project.sources.yml!
vim project_sources.yml
```

### "Detailed file is outdated"

**Cause:** Manual changes to `project_sources.yml` not synced
**Fix:**
```bash
python scripts/sync_ssot.py
git diff detailed_project.sources.yml  # Review
git commit -m "Sync detailed SSOT"
```

### "Links are broken"

**Cause:** Upstream docs moved
**Fix:**
```bash
# Update in project_sources.yml
vim project_sources.yml

# Crawler will re-fetch on next run
./crawler update --force
```

---

## Migration Guide

### From old `sources.yaml` to `project_sources.yml`
```bash
# If you have old sources.yaml, rename it
mv sources.yaml project_sources.yml

# Update references in code
grep -r "sources.yaml" . --exclude-dir=.git
# Replace with: project_sources.yml

# Commit
git add project_sources.yml
git commit -m "chore: Rename SSOT to project_sources.yml"
```

---

## Advanced Usage

### Custom Extraction from Detailed File
```python
# scripts/extract_backend_info.py
import yaml

with open('detailed_project.sources.yml') as f:
    data = yaml.safe_load(f)

# Extract all quantization info
quant = data.get('quantization', {})
print(yaml.dump(quant, default_flow_style=False))
```

### Diff Between Files
```bash
# See what's ONLY in detailed file
diff <(yq eval 'keys' project_sources.yml) \
     <(yq eval 'keys' detailed_project.sources.yml)
```

### Generate Quick Reference
```bash
# Extract all docs_workflow links
yq eval '.. | select(has("docs_workflow")) | .docs_workflow' \
   project_sources.yml > docs_links.txt
```

---

## Summary

| File | Purpose | Used By | Content | Size |
|------|---------|---------|---------|------|
| `project_sources.yml` | Production SSOT | Framework, Crawler, Orchestrator | Links + Metadata | ~500 lines |
| `detailed_project.sources.yml` | Developer Reference | Developers, Offline use | Links + Knowledge | ~3000+ lines |

**Golden Rule:**
> Framework reads `project_sources.yml`  
> Developers read `detailed_project.sources.yml`  
> When in doubt, trust `project_sources.yml`

---

**Last Updated:** 2025-12-28  
**Maintained By:** LLM Conversion Framework Team
