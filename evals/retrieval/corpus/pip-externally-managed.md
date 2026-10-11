---
title: "pip: externally-managed-environment"
lang: en
---
# pip: externally-managed-environment

On Debian 12, Ubuntu 23.04 and later releases, `pip install` outside a virtual environment stops with
`error: externally-managed-environment`. The distribution marks its Python as managed by the package manager
(PEP 668), so pip does not overwrite files that apt installed.

Create a virtual environment and install there:

```
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

For a command line tool that should be available everywhere, use `pipx install <tool>`. The option
`--break-system-packages` disables the check; it can break system tools that depend on the distribution's
packages and should not be used on a machine you care about. Inside a build container a virtual environment
costs almost nothing and keeps the image reproducible.
