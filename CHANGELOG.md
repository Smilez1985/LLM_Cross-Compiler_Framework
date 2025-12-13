# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.0] - 2025-12-10
**Hardware & Deployment Update.** Bridges the gap between build and run by introducing a zero-dependency deployment manager and full Intel hardware support.

### Added
- **Deployment Manager:** Integrated SSH/SCP client to deploy artifacts directly to edge devices. Features a "Zero-Dependency" mode (using system binaries or Paramiko) and a "Network Guard" ping-loop for connection stability.
- **Intel Ecosystem:** Full support for Intel IPEX-LLM and OpenVINO. `hardware_probe` now detects Intel Arc GPUs, iGPUs, and NPU accelerators (Meteor Lake).
- **Live Monitoring:** Real-time CPU and RAM usage visualization of the build container directly in the GUI (replacing external tools like `ctop`).
- **Visual Identity:** "Ditto" comes to life! Added dynamic avatar states in the Wizard (Thinking, Reading, Success, Error) to improve user experience.
- **Auto-Documentation:** The builder now automatically generates a `Model_Card.md` containing SHA256 hashes and usage instructions, bundled into the Golden Artifact ZIP.

### Changed
- **Hardware Probe:** Massively expanded `hardware_probe.sh` and `.ps1` to detect AVX512-VNNI and AMX flags essential for Intel optimization.
- **GUI:** Added "Deploy" button to Main Window (active after successful build) and a secure `DeploymentDialog` that keeps credentials in RAM only.
- **Builder:** Updated artifact handling to deterministically identify model files and create a standardized ZIP package ("Golden Artifact").

## [1.6.0] - 2025-12-06
**Deep Ingest Release.** Empowers the AI Agent "Ditto" to recursively crawl, filter, and learn from external documentation websites and PDFs.

### Added
- **Deep Crawler Engine:** Integrated `LangChain` based Recursive URL Loader to parse entire documentation sites (not just single pages).
- **PDF Intelligence:** Native PDF parsing support using `pypdf`, enabling Ditto to read data sheets and technical manuals.
- **Smart Filtering:** Implemented language detection (ignoring non-English content) and navigation/footer removal for cleaner knowledge.
- **Knowledge Snapshots:** The Module Generator now automatically extracts learned knowledge from Qdrant and bundles it as `knowledge.json` within the target module for sharing.
- **GUI Dialogs:** New `URLInputDialog` for batch-adding documentation links with configuration for crawl depth and page limits.
- **Legal Compliance:** Mandatory "Robots.txt" respect and user disclaimer checkbox before crawling.

### Changed
- **Wizard Workflow:** Integrated "Deep Ingest" step into the module creation wizard with live progress logging.
- **RAG Manager:** Expanded `ingest_url` to handle recursive crawling results and PDF streams.
- **Config Manager:** Added settings for `crawler_max_depth`, `crawler_max_pages`, and input history.

## [1.5.0] - 2025-12-01
**Expert Knowledge Release.** Transforms the AI Agent "Ditto" from a passive reader into an active expert system using local RAG technology.

### Added
- **Local RAG (Experimental):** Integration of Qdrant Vector Database to enable semantic search over hardware documentation instead of naive scraping.
- **Dynamic Sidecar Architecture:** Qdrant runs as an on-demand container. It is only downloaded and started if the user explicitly enables "Knowledge Base" to save resources.
- **Community Brain:** New workflow to share indexed knowledge snapshots via Git. Users can import community-curated knowledge packs (`community/knowledge/*.json`) for offline intelligence.
- **RAGManager:** New core component (`orchestrator/Core/rag_manager.py`) handling document ingestion, chunking, and vector retrieval.

### Changed
- **Ditto Agent:** Updated `_fetch_documentation` logic to prioritize vector search results over raw URL downloads for higher precision in module generation.
- **DockerManager:** Added logic to dynamically spawn/stop the `llm-qdrant` container based on user configuration.
- **GUI:** Added "Enable Local Knowledge Base" toggle in AI Settings dialog.

### Security
- **Data Privacy:** Vector data remains strictly local (127.0.0.1). No external cloud vector store is used.
- **Sanitization:** Export tools for community knowledge automatically strip potential sensitive paths or API keys before creating snapshots.

## [1.4.0] - 2025-11-30
**Smart Calibration Release.** Introduces a context-aware quantization pipeline with Human-in-the-Loop verification to ensure enterprise-grade model quality on NPU targets.

### Added
- **Smart Calibration:** Automated workflow to detect if a model requires calibration data (e.g., INT8) and generate/select it.
- **DatasetManager:** New core component (`orchestrator/Core/dataset_manager.py`) for deterministic domain detection and dataset validation.
- **Human-in-the-Loop:** Added `DatasetReviewDialog` to allow users to verify and edit AI-generated calibration data before build execution.
- **Ditto AI Upgrade:** Expanded Ditto's capabilities to generate synthetic domain-specific training data (Code, Chat, Medical) based on SSOT knowledge.

### Changed
- **Build Pipeline:** Updated `builder.py` and `docker_manager.py` to inject calibration datasets (`dataset.json`) into the build container.
- **Rockchip Modules:** Updated `rkllm_module.sh` and `rknn_module.sh` (and their Python wrappers) to accept and utilize external calibration datasets for higher quantization accuracy.
- **GUI:** `MainWindow` now intelligently prompts for datasets when selecting sensitive quantization modes (INT8/W8A8).
- **Policy:** Removed heuristic guessing for model domains; introduced strict metadata checks or user prompts.

## [1.3.0] - 2025-11-29
**Production Ready Release.** Focuses on full internationalization, security hardening, and closing the loop between GUI and Build Engine.

### Added
- **I18n Support:** Full English/German localization for GUI and Wizard.
- **LocalizationManager:** Centralized service for dynamic language switching (`orchestrator/utils/localization.py`).
- **GUI Enhancements:**
    - New Dropdowns for **Task** (LLM, Voice, VLM) and **Quantization** (INT8, INT4, FP16).
    - Added **"Use GPU"** checkbox for NVIDIA Passthrough.
    - Integrated Language Selection Dialog on first start.
    - **SecretsManager:** AES-256 encryption for sensitive API keys.

### Changed
- **Refactoring:** Centralized text resources in `LocalizationManager`.
- **Dependencies:** Added `litellm`, `rich`, `psutil` and `cryptography` to core requirements.
- **Docker:** Removed static builder services in favor of dynamic spawning.

## [1.2.0] - 2025-11-29
**AI Integration Release.** Introduces the Ditto Agent for automated hardware discovery and target generation.

### Added
- **Ditto AI Agent:** Automated analysis of hardware probes and documentation fetching from SSOT.
- **Smart Dispatcher:** Implemented dynamic `build.sh` template handling Task and Hardware routing.
- **Templates:** Added `rkllm_module` and `rknn_module` templates for Rockchip NPU support.
- **Security:** Added `trivy-infra-scanner` for automated container vulnerability auditing.
- **NetworkGuard:** Robust connectivity checks and secure downloads.

### Changed
- **Hardware Probe:** Hardened Windows detection using Native API (C# Injection) for reliable CPU flags.
- **Pipeline:** Decoupled Python generation logic from Bash scripts.

## [1.1.0] - 2025-11-28
**Architecture Refactoring.** Decouples the core builder from specific target logic to enable universal cross-compilation.

### Changed
- **Architecture:** Removed hardcoded `TargetArch` enums; Builder is now target-agnostic.
- **Clean Code:** Extracted inline Python code from Bash scripts into standalone files.
- **Builder:** Switched to dynamic volume mounting for module execution.

### Fixed
- **Stability:** Fixed list/set type errors in Windows installer.
- **Cross-Compilation:** Added missing cross-compilers (`gcc-aarch64`) to Docker templates.

## [1.0.0] - 2025-11-20
**Initial Release.** Establishes the core framework structure and basic Rockchip support.

### Added
- Core Framework structure (Orchestrator, Builder, Docker Manager).
- Basic support for Rockchip RK3588/RK3566 cross-compilation.
- CLI (`llm-cli`) and GUI (`llm-gui`) entry points.
