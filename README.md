# 🚀 LLM Cross-Compiler Framework
**DITTO: Definitive Inference Target Translation On-Edge**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-20.10+-blue.svg)](https://docs.docker.com/get-docker/)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-green.svg)]()
[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)]()
[![GitHub Stars](https://img.shields.io/github/stars/Smilez1985/llm_conversion_framework?style=social)](https://github.com/Smilez1985/llm_conversion_framework)
[![GitHub Forks](https://img.shields.io/github/forks/Smilez1985/llm_conversion_framework?style=social)](https://github.com/Smilez1985/llm_conversion_framework)

> **Note:** For documentation in German, see [README_DE.md](README_DE.md).

**The Autonomous MLOps Platform for Edge AI.**  
A self-managing, self-healing framework that compiles, optimizes, and deploys Large Language Models to any hardware (Rockchip, NVIDIA, Intel, etc.) without dependency hell.

---

## 🌟 What's New in v2.0.0 (The Brain Update)

We transformed the framework from a "Tool" into an **Intelligent System**.

* 🧠 **Native Offline Intelligence:** Ditto runs locally (TinyLlama/Qwen) without Internet or external Docker containers. Zero dependencies.
* 🚑 **Self-Healing Architecture:** Builds don't just fail; they diagnose themselves. The framework detects driver mismatches or missing libraries and proposes exact fix commands.
* 🛡️ **Guardian Layers:**
    * **Consistency Gate:** Prevents doomed builds by checking SDK vs. Driver compatibility *before* execution.
    * **Knowledge Insurance:** Automatic RAG snapshots allow rollbacks if the AI learns incorrect information.
    * **Ethics Gate:** Warns about restrictive model licenses before download.
* 🔮 **Self-Awareness:** Ditto now indexes its own source code (`/app`), allowing it to answer deep architectural questions about the framework itself.

[View Full Changelog](CHANGELOG.md) | [Upgrade Guide](docs/upgrade_v2.0.md)

---

## ⚡ Key Features

### 🏗️ Multi-Architecture Support
Compile models for any target architecture from a single x86 host. Supports **Rockchip NPU** (RKNN), **NVIDIA GPU** (TensorRT), **Intel XPU** (IPEX/OpenVINO), and more.

### 🤖 Autonomous AI Agent (Ditto)
Ditto isn't just a wizard anymore.
* **Deep Ingest:** Crawls documentation websites and PDFs to learn new SDKs.
* **Chat Interface:** Ask questions like *"Why did my build fail?"* or *"How do I optimize for 8GB RAM?"*.
* **Memory:** Remembers your hardware context but keeps the chat clean via "Rolling Context Compression".

### 🚀 Zero-Dependency Deployment
Push your optimized models to the edge with a single click.
* **Secure:** Credentials exist only in RAM.
* **Robust:** "Network Guard" pauses transfer on connection loss.
* **Simple:** Generates a standalone `deploy.sh` on the target.

### 🛡️ Security-First Architecture
* **Socket Proxy:** Isolates Docker to prevent root escapes.
* **Trivy Scanning:** Audits every build image for CVEs.
* **Sanitization:** Telemetry (Opt-In) automatically strips API keys and user paths.

---

## 📂 Project Structure
```
.
├── Launch-LLM-Conversion-Framework.bat # One-Click Installer & Launcher
├── assets/                             # UI Resources (Ditto Avatars)
├── orchestrator/
│   ├── gui/                            # PySide6 GUI (Chat, Wizard, Monitoring)
│   ├── Core/                           # The Brain
│   │   ├── self_healing_manager.py     # Auto-Diagnosis
│   │   ├── consistency_manager.py      # Pre-Flight Checks
│   │   ├── ditto_manager.py            # Native Inference
│   │   └── rag_manager.py              # Knowledge Base & Snapshots
├── targets/                            # Hardware Modules (Rockchip, Intel, etc.)
├── community/
│   └── knowledge/                      # Shared RAG Snapshots
└── output/                             # Golden Artifacts
```

---

## 📟 Supported Hardware

| Family | Status | Chips | Features |
|--------|--------|-------|----------|
| **Rockchip** | ✅ Production | RK3588, RK3566, RK3576 | RKLLM, RKNN, W8A8 |
| **NVIDIA** | ✅ Production | Orin, Xavier, RTX 30/40 | TensorRT, CUDA 12 |
| **Intel** | ✅ Production | Arc A-Series, Core Ultra | IPEX-LLM, OpenVINO |
| **Raspberry Pi** | 🚧 Beta | Pi 5 + Hailo-8L | HailoRT, PCIe |
| **RISC-V** | 🌐 Community | VisionFive 2 | Vector Ext. (V) |

---

## 📥 Installation & Usage

### Windows (One-Click)

1. Download the repository.
2. Double-click **Launch-LLM-Conversion-Framework.bat**.
3. It automatically installs Python/Git if missing, sets up the environment, and updates itself.

### Linux (Headless / CI)
```bash
make setup  # Checks groups & permissions
make up     # Starts Orchestrator
docker exec -it llm-orchestrator llm-cli
```

---

## 🛠️ The Workflow

1. **Probe:** Run `./hardware_probe.sh` on your target device.
2. **Import:** Load the profile in the GUI.
3. **Consult:** Ask Ditto: *"Is this model compatible with my 8GB RAM?"*
4. **Build:** Select Model & Format (GGUF/RKNN). The Consistency Gate ensures compatibility.
5. **Deploy:** Click "Deploy to Target" to push the Golden Artifact via SSH.

---

## 🤝 Community & Governance

- **Knowledge Sharing:** Export your RAG snapshots to `community/knowledge/` to help others.
- **Telemetry:** Opt-In anonymous reporting helps us fix bugs faster. (We never track prompts or private keys).
- **Support:** Open a [GitHub Discussion](https://github.com/Smilez1985/llm_conversion_framework/discussions).

---

## 📄 License

Licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

<div align="center">

[⭐ Star us on GitHub](https://github.com/Smilez1985/llm_conversion_framework) | [📖 Docs](#) | [💬 Discord](#)

**Empowering developers to run AI everywhere.**

</div>
