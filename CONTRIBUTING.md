# Contributing to LLM Cross-Compiler Framework

First off, thank you for considering contributing to this project! We are building the **Gold Standard** for Edge AI deployment, and we need your help to support the infinite variety of hardware out there.

This guide explains how to contribute in two main ways:
1.  **🧠 The Community Brain:** Sharing knowledge (SDK docs, flags) so Ditto gets smarter.
2.  **🛠️ Hardware Targets:** Adding support for new chips (RISC-V, NPUs, GPUs).

---

## 🧠 1. Contributing Knowledge (RAG Snapshots)

This framework uses a local Vector Database (Qdrant) to understand hardware documentation. If you have successfully configured a tricky board (e.g. a new Banana Pi or an exotic RISC-V board), you can share that knowledge.

### How it works
1.  **Ingest:** Use the **"Deep Ingest"** feature in the Wizard to crawl the official documentation URL of your hardware.
2.  **Verify:** Build your model. If Ditto chose the correct flags, the knowledge is good.
3.  **Export:** The framework automatically creates a `knowledge.json` snapshot in your target's `knowledge/` folder when you generate a module.

### Submission Process
1.  Locate the snapshot: `targets/<your_target>/knowledge/initial_knowledge.json`.
2.  **Security Check:** Ensure no API keys or local paths (e.g. `/home/user/secret`) are inside. *Note: Our Telemetry/Export logic attempts to sanitize this, but double-checking is mandatory.*
3.  Copy this file to `community/knowledge/<target_name>_docs.json`.
4.  Open a Pull Request.

---

## 🛠️ 2. Contributing Hardware Targets

You want to add support for a new board? Great!
We follow a strict **"4-Module Architecture"** to ensure stability.

### The Architecture
Every target in `targets/` must have this structure:

```text

targets/my_board/
├── target.yml           # Metadata & Capability Definition
├── Dockerfile           # The Build Environment (Cross-Compiler & SDKs)
└── modules/
    ├── source_module.sh  # Clones llama.cpp (Standard)
    ├── config_module.sh  # Generates CMake Toolchain (The Logic)
    ├── convert_module.sh # Converts HF -> GGUF (Standard)
    └── target_module.sh  # Quantizes & Packages (Standard)

```
Step-by-Step Guide

1. **Probe**: Run scripts/hardware_probe.sh on your device. Save the output.

2. **Generate**: Use the Module Wizard (GUI or CLI) to generate the base structure.
   Use the AI Mode if you are unsure about compiler flags.

3. **Refine** ```config_module.sh```: This is where the magic happens.

*Read the ```target_hardware_config.txt```.

*Set ```CMAKE_C_FLAGS``` (e.g. ```-mcpu=cortex-a76```).

*Enable GGML flags (e.g. ```-DGGML_NEON=ON```).

*Directive: Do not hardcode values if they are in the probe config.

4. **Refine** ```Dockerfile```:

*Use multi-stage builds.

*Install vendor SDKs (e.g. ```rknn-toolkit```) here.

*Keep it minimal.

5. **Test**: Run a full build locally via ```llm-cli build start```.

**Submission Rules**
No Binaries: Do not commit ```.whl``` or ```.so``` files. Use ```pip install``` or ```wget``` in the Dockerfile.

Reproducibility: Pin versions! Use ```git checkout <commit-hash>``` instead of ```latest```.

## 💻 3. Code Contributions (Python/Core)
If you want to improve the Orchestrator or UI:

1. Environment:
```bash
# Install dependencies (Poetry handles hashes)
pip install poetry
poetry install
```

## 2. Standards:

Python 3.10+

Type Hints: Required for all new functions.

Logging: Use ```self.logger```, never ```print()```.

Security: No raw ```subprocess.run``` with user input. Use ```shlex.quote``` or list arguments.

## 3. Testing:

Run ```poetry run pytest``` before submitting.

New features need new tests in ```tests/```.

## 🛡️ Security Policy
Secrets: Never commit ```master.key``` or ```secrets.enc```.

Isolation: Do not modify the ```socket-proxy``` settings in ```docker-compose.yml``` to loosen security.

Reporting: If you find a vulnerability, please open a Private Advisory on GitHub instead of a public issue.

## 🤝 Community Governance
Be respectful. We are building this together.

Documentation First. If a feature isn't documented, it doesn't exist.

Gold Standard. We prefer "Stable & Secure" over "Fast & Broken".

Happy Coding!
