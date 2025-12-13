# Module Developer Guide (V2.3 Enterprise)

Welcome to the **LLM Cross-Compiler Framework**. This guide explains how to add support for new hardware platforms (Targets).

The system relies on a **Module Template System**. Each target (e.g., `targets/rockchip_rk3588`) contains four critical files that control the cross-compilation process. You do not need to write these manually—use the **Wizard**.

---

## 🧙 The Module Creation Wizard

The Wizard (`orchestrator/gui/wizards.py`) is your primary tool. It supports three operation modes, depending on hardware complexity.

Launch it via GUI: **Tools -> Create New Target Module**.

### Mode A: Manual Mode (The Expert)
*Use case: Completely unknown hardware or highly specific custom OS setups.*

1.  **Hardware:** You manually enter architecture (`aarch64`, `riscv64`) and SDK names.
2.  **Docker:** You define the base image (e.g., `ubuntu:22.04`) and package list (`apt-get install ...`).
3.  **Flags:** You type out GCC flags (`-mcpu=...`) and CMake variables by hand.
4.  **Result:** The Wizard creates the folder structure but only fills in your raw inputs.

### Mode B: AI-Assisted (Ditto + Hardware Probe)
*Use case: Standard SBCs (Raspberry Pi, Jetson, Orange Pi) and common CPUs.*

1.  **Probe:** Run `scripts/hardware_probe.sh` on the target device. Upload the resulting `target_hardware_config.txt` in the Wizard.
2.  **Analysis:** Ditto (the AI Agent) parses the file. It detects:
    * CPU Cores and Architecture.
    * Available RAM.
    * Accelerator Vendor IDs (GPU/NPU).
3.  **Generation:** Ditto generates the `Dockerfile` and `config_module.sh` based on its internal training data regarding this hardware.
    * *Example:* It sees "Cortex-A76" in the probe and automatically sets `-mcpu=cortex-a76`.

### Mode C: AI Expert (Ditto + RAG Knowledge Base)
*Use case: Proprietary NPUs, Bleeding-Edge Hardware, or closed-source SDKs (Rockchip RKLLM, HailoRT).*

1.  **Preparation (Ingest):** Use "Deep Ingest" (in Wizard or CLI) to load PDF manuals or documentation sites into the local Vector DB (Qdrant).
2.  **Probe & RAG:** Upload the probe file and check **"Enable Knowledge Base"**.
3.  **Synthesis:**
    * Ditto analyzes the probe.
    * It queries the local database (RAG) for specific compiler flags relevant to the detected SDK version.
    * It combines both into a high-precision build script that includes undocumented or brand-new flags the base LLM might not know.

---

## 📂 Module Structure

Every generated module in `targets/` consists of these four files:

| File | Function | Status V2.3 |
| :--- | :--- | :--- |
| `Dockerfile` | Defines the build environment (Compilers, SDKs). | **Auto-Generated** |
| `source_module.sh` | Downloads model and converts to FP16 GGUF. | **Static** (Template) |
| `config_module.sh` | **The Core.** Reads `target_hardware_config.txt` and exports `CMAKE_ARGS`. | **Auto-Generated** |
| `target_module.sh` | Orchestrates quantization and compilation. | **Static** (Template) |

### Important for Manual Edits
When editing `config_module.sh`: **Do not hardcode values!**
Use the helper logic to read values dynamically from the probe file:
```bash
# BAD:
export CPU_CORES=4

# GOOD (Goldstandard):
CPU_CORES=$(cat /build-cache/target_hardware_config.txt | grep "CPU_CORES" | cut -d= -f2)
```
This ensures your module remains flexible across different board variants (e.g., 4GB vs 8GB RAM).
