# 🚀 LLM Cross-Compiler Framework
### DITTO: Definitive Inference Target Translation On-Edge

**Enterprise-grade toolchain for cross-compiling, quantizing, and deploying Local LLMs to Rockchip NPU targets (RK3588, RK3576, RK3566).**

This framework automates the entire lifecycle of Edge AI deployment: from downloading models from HuggingFace, converting them to GGUF format, applying hardware-specific quantization, to air-gapped deployment on embedded devices.

---

## 🚀 Key Features

### 🛡️ Enterprise Security (v2.3)
The framework enforces strict security validation across all modules to ensure safe operation in corporate environments:
* **SSRF Protection:** The Crawler utilizes centralized validation logic to strictly block access to localhost, private IP ranges, and non-HTTP schemes.
* **Deployment Hardening:** Target IP addresses are validated against strict patterns before any socket connection or SSH handshake is attempted.
* **Audit-Ready:** Automated CI scripts (`ci_image_audit.sh`) verify Docker container efficiency and layer security without host dependencies.

### 🏗️ Core Architecture
* **Cross-Compilation:** Native Docker container ensures reproducible builds for AArch64 architectures on x86 hosts.
* **Smart Quantization:** Automated selection of quantization methods (e.g., `Q4_K_M`) balanced for specific NPU constraints.
* **Slim-RAG Strategy:** Deploys a "clean slate" Vector DB structure to the target. The device learns locally; no massive pre-built databases are transferred.
* **Polite Crawler:** A respectful documentation ingest engine that honors `robots.txt`, handles rate limits, and parses PDFs/HTML for RAG context.

---

## 📋 Prerequisites

Before installing the framework, ensure your system meets the following requirements:

### Windows Users ⚠️
* **Docker Desktop** must be installed and running.
* **WSL 2 Backend** must be enabled in Docker settings.
* This is mandatory for the cross-compilation containers to function correctly.

### Linux Users
* A standard installation of **Docker** is required (the installer can attempt to set this up automatically).

---

## 📦 Installation

We have simplified the installation process into single-file installers ("Single Source of Truth").

### Windows
1. **Run as Admin:** Right-click `install.bat` and select **"Run as Administrator"**.
2. **Process:** The script checks for Python 3.11-3.14 (installing 3.13 via Winget if missing), creates an isolated `.venv`, installs all dependencies, and creates Desktop shortcuts.

### Linux / macOS
1. Open your terminal in the repository root.
2. Run the installer:
```bash
   sudo ./install.sh
```
3. **Process:** The script installs system dependencies, fixes Docker group permissions for your user, and deploys the framework to `/opt/llm-conversion-framework`.

---

## 🖥️ Usage Guide

### 1. The Orchestrator GUI
Start via the Desktop Shortcut (Windows) or command line.

* **Source Tab:** Search and download models directly from HuggingFace. Validates SHA256 integrity.
* **Convert Tab:** Manages the conversion pipeline.
    * *Input:* Raw PyTorch/Safetensors model.
    * *Output:* NPU-optimized GGUF format.
    * *Opt-in:* Toggle specific hardware flags for your target board.
* **Deploy Tab:** Connection management for Edge Devices.
    * *Features:* SSH Key management, Air-Gap package generation (ZIP with Docker images), and one-click deployment.

### 2. The Wizard (CLI)
For headless servers or Linux users, the Wizard provides an interactive guide.

**Start:**
```bash
./start_framework.bat   # Windows
llm-framework           # Linux (if installed globally)
```

**Workflow:**
1. **Select Operation:** Download / Convert / Quantize / Deploy.
2. **Target Selection:** Choose your board (e.g., "Orange Pi 5").
3. **Optimization:** The wizard suggests the best quantization based on target RAM.

### 3. Containerized Build System
Ensure cross-platform compatibility by running the core logic in Docker.
```bash
make build              # Build the image
make test-container     # Run isolated tests
```

---

### 4. AI Clients via MCP (Claude Code, Claude Desktop, VS Code)

The framework runs as a [Model Context Protocol](https://modelcontextprotocol.io) server, so an AI client you
already use can drive it: list targets, check what a board supports, start and watch builds, read logs and
decide on self-healing proposals. You sign in to the client with your own Claude account or API key; the
framework never sees those credentials.

```bash
llm-cli ditto setup        # once: what AI clients may do on their own, what needs your confirmation
llm-cli mcp config         # shows the commands/snippets for Claude Code, Claude Desktop and VS Code
llm-cli mcp config --write claude-desktop   # or add it to Claude Desktop directly (backup is kept)
```

- **Permissions:** read-only tools are allowed by default; starting or cancelling builds asks for your
  confirmation (the client shows the question), unless you allowed it. Tools that reach other devices can
  only be allowed one by one. Every call is written to a tamper-evident audit log (`llm-cli tools audit`).
- **Transports:** stdio (default, started by the client) or local Streamable HTTP
  (`llm-cli mcp serve --transport http`, loopback only, bearer token in `~/.llm-framework/mcp_http_token`).

### 5. Ditto Offline: Local Model Server (llama.cpp)

Ditto can run without any cloud account on a local model served by a managed `llama-server`.

```bash
llm-cli ditto runtime status       # hardware, recommended llama.cpp build and model, server state
llm-cli ditto runtime install      # the pinned llama.cpp build for this machine
llm-cli ditto models download      # the recommended model (or a tier: small-q2, small, default, large, ...)
llm-cli ditto runtime start        # Ditto then uses it as the "local" AI provider
llm-cli ditto runtime stop
```

- **Verified downloads:** every build and model is pinned with size and sha256 in `configs/llama_runtime.yml`
  (models at a fixed Hugging Face commit) and checked before it is unpacked or loaded; unpinned entries are
  refused. Downloads resume after an interruption. New models are added in that file, not in code.
- **Hardware:** NVIDIA GPUs get a CUDA build that matches driver and GPU (an RTX 2080 Ti needs driver 570.26
  for the CUDA 12.8 build and R615 for CUDA 13.4, otherwise Vulkan is used); AMD Radeon GPUs and APUs such as
  the Ryzen Z1 Extreme use Vulkan (ROCm only with `--backend rocm`); Apple Silicon uses Metal; everything else
  the CPU build. The model tier follows RAM and GPU memory; machines with 3 GB RAM or less get a 2-bit model,
  and `runtime start --model <tier>` picks any tier.
- **Isolation:** the server listens on 127.0.0.1 only, on a free port, with a new API key per start that is
  passed in a file only you can read. Its web UI, built-in tools and MCP client stay off.

## 🤝 Community & Collaboration

We believe in the power of open collaboration.

* **Share Target Modules:** If you have generated a config module for a new board using the Wizard, please contribute it back via Pull Request.
* **RAG Knowledge:** We encourage sharing non-sensitive RAG datasets to improve the collective intelligence of edge devices.

---

## 🛠️ Configuration

**Location:** `configs/user_config.yml` (or in your Data directory).
```yaml
crawler_respect_robots: true
crawler_max_depth: 2
enable_rag_knowledge: true
target_architecture: "aarch64"
```

---

## 🙏 Credits

* **[llama.cpp](https://github.com/ggerganov/llama.cpp)** - The core of inference
* **[Hugging Face](https://huggingface.co/)** - For the model ecosystem
* **[Ditto](https://github.com/yoheinakajima/ditto)** - AI-Agent Framework for automatic hardware module generation (developed by [@yoheinakajima](https://github.com/yoheinakajima))
* **[Qdrant](https://qdrant.tech/)** - Vector database for our Local Knowledge Base
* **[Radxa Community](https://forum.radxa.com/)** - For support with RK3566 integration
* **[Docker](https://www.docker.com/)** - Containerization Platform
* **[PySide6](https://doc.qt.io/qtforpython-6/)** - Professional GUI Framework
* **[Poetry](https://python-poetry.org/)** - Modern Python Dependency Management

---

## 📄 License

This project is licensed under the **MIT License**.
