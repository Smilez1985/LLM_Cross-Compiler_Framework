# 🚀 LLM Cross-Compiler Framework
**DITTO: Definitive Inference Target Translation On-Edge**


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-20.10+-blue.svg)](https://docs.docker.com/get-docker/)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-green.svg)]()
[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)]()
[![GitHub Stars](https://img.shields.io/github/stars/Smilez1985/llm_conversion_framework?style=social)](https://github.com/Smilez1985/llm_conversion_framework)
[![GitHub Forks](https://img.shields.io/github/forks/Smilez1985/llm_conversion_framework?style=social)](https://github.com/Smilez1985/llm_conversion_framework)

> **Hinweis:** Für die englische Dokumentation siehe [README.md](README.md).

**Die autonome MLOps-Plattform für Edge-AI.**  
Ein selbstverwaltendes, selbstheilendes Framework, das Large Language Models (LLMs) für jede Hardware (Rockchip, NVIDIA, Intel, etc.) kompiliert, optimiert und deployt – ohne "Dependency-Hölle".

---

## 🌟 Was ist neu in v2.0.0 (The Brain Update)

Wir haben das Framework von einem "Werkzeug" in ein **Intelligentes System** verwandelt.

* 🧠 **Native Offline-Intelligenz:** Ditto läuft jetzt lokal (TinyLlama/Qwen) ohne Internet oder externe Docker-Container. Null Abhängigkeiten.
* 🚑 **Selbstheilende Architektur:** Builds schlagen nicht einfach fehl; sie diagnostizieren sich selbst. Das Framework erkennt Treiber-Konflikte oder fehlende Bibliotheken und schlägt exakte Reparatur-Befehle vor.
* 🛡️ **Guardian Layers (Schutzschichten):**
    * **Konsistenz-Gate:** Verhindert zum Scheitern verurteilte Builds, indem es SDK- und Treiber-Kompatibilität *vor* der Ausführung prüft.
    * **Wissens-Versicherung:** Automatische RAG-Snapshots ermöglichen Rollbacks, falls die KI falsche Informationen lernt.
    * **Ethik-Gate:** Warnt vor dem Download bei Modellen mit restriktiven Lizenzen.
* 🔮 **Selbstbewusstsein:** Ditto indiziert nun seinen eigenen Quellcode (`/app`), wodurch er tiefe architektonische Fragen zum Framework selbst beantworten kann.

[Vollständigen Changelog ansehen](CHANGELOG.md) | [Upgrade Guide](docs/upgrade_v2.0.md)

---

## ⚡ Hauptfunktionen

### 🏗️ Multi-Architektur Support
Kompilieren Sie Modelle für jede Zielarchitektur von einem einzigen x86-Host aus. Unterstützt **Rockchip NPU** (RKNN), **NVIDIA GPU** (TensorRT), **Intel XPU** (IPEX/OpenVINO) und mehr.

### 🤖 Autonomer KI-Agent (Ditto)
Ditto ist nicht mehr nur ein Wizard.
* **Deep Ingest:** Crawlt Dokumentations-Webseiten und PDFs, um neue SDKs zu erlernen.
* **Chat-Interface:** Stellen Sie Fragen wie *"Warum ist mein Build fehlgeschlagen?"* oder *"Wie optimiere ich für 8GB RAM?"*.
* **Gedächtnis:** Erinnert sich an Ihren Hardware-Kontext, hält den Chat aber durch "Rolling Context Compression" sauber.

### 🚀 Zero-Dependency Deployment
Schieben Sie Ihre optimierten Modelle mit einem Klick auf das Edge-Gerät.
* **Sicher:** Zugangsdaten existieren nur im RAM.
* **Robust:** "Network Guard" pausiert den Transfer bei Verbindungsabbruch.
* **Einfach:** Generiert ein eigenständiges `deploy.sh` auf dem Zielgerät.

### 🛡️ Security-First Architektur
* **Socket Proxy:** Isoliert Docker, um Root-Ausbrüche zu verhindern.
* **Trivy Scanning:** Prüft jedes Build-Image auf CVEs (Sicherheitslücken).
* **Sanitization:** Telemetrie (Opt-In) entfernt automatisch API-Keys und Benutzerpfade.

---

## 📂 Projektstruktur
```
.
├── Launch-LLM-Conversion-Framework.bat # One-Click Installer & Launcher
├── assets/                             # UI Ressourcen (Ditto Avatare)
├── orchestrator/
│   ├── gui/                            # PySide6 GUI (Chat, Wizard, Monitoring)
│   ├── Core/                           # Das Gehirn
│   │   ├── self_healing_manager.py     # Auto-Diagnose
│   │   ├── consistency_manager.py      # Pre-Flight Checks
│   │   ├── ditto_manager.py            # Native Inferenz
│   │   └── rag_manager.py              # Wissensbasis & Snapshots
├── targets/                            # Hardware Module (Rockchip, Intel, etc.)
├── community/
│   └── knowledge/                      # Geteilte RAG Snapshots
└── output/                             # Golden Artifacts
```

---

## 📟 Unterstützte Hardware

| Familie | Status | Chips | Features |
|---------|--------|-------|----------|
| **Rockchip** | ✅ Production | RK3588, RK3566, RK3576 | RKLLM, RKNN, W8A8 |
| **NVIDIA** | ✅ Production | Orin, Xavier, RTX 30/40 | TensorRT, CUDA 12 |
| **Intel** | ✅ Production | Arc A-Series, Core Ultra | IPEX-LLM, OpenVINO |
| **Raspberry Pi** | 🚧 Beta | Pi 5 + Hailo-8L | HailoRT, PCIe |
| **RISC-V** | 🌐 Community | VisionFive 2 | Vector Ext. (V) |

---

## 📥 Installation & Nutzung

### Windows (One-Click)

1. Laden Sie das Repository herunter.
2. Doppelklicken Sie auf **Launch-LLM-Conversion-Framework.bat**.
3. Installiert automatisch Python/Git falls fehlend, richtet die Umgebung ein und aktualisiert sich selbst.

### Linux (Headless / CI)
```bash
make setup  # Prüft Gruppen & Rechte
make up     # Startet Orchestrator
docker exec -it llm-orchestrator llm-cli
```

---

## 🛠️ Der Workflow

1. **Probe:** Führen Sie `./hardware_probe.sh` auf Ihrem Zielgerät aus.
2. **Import:** Laden Sie das Profil in der GUI.
3. **Konsultieren:** Fragen Sie Ditto: *"Ist dieses Modell mit meinen 8GB RAM kompatibel?"*
4. **Bauen:** Wählen Sie Modell & Format (GGUF/RKNN). Das Konsistenz-Gate sichert die Kompatibilität.
5. **Deployen:** Klicken Sie auf "Deploy to Target", um das Golden Artifact via SSH zu übertragen.

---

## 🤝 Community & Governance

- **Wissen teilen:** Exportieren Sie Ihre RAG-Snapshots nach `community/knowledge/`, um anderen zu helfen.
- **Telemetrie:** Opt-In anonyme Berichterstattung hilft uns, Bugs schneller zu beheben. (Wir tracken niemals Prompts oder private Keys).
- **Support:** Öffnen Sie eine [GitHub Discussion](https://github.com/Smilez1985/llm_conversion_framework/discussions).

---

## 📄 Lizenz

Lizenziert unter der **MIT License**. Siehe [LICENSE](LICENSE) für Details.

---

<div align="center">

[⭐ Star us on GitHub](https://github.com/Smilez1985/llm_conversion_framework) | [📖 Dokumentation](#) | [💬 Discord](#)

**Empowering developers to run AI everywhere.**

</div>
