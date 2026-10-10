# 🚀 LLM Cross-Compiler Framework
### DITTO: Definitive Inference Target Translation On-Edge

**Professionelle Toolchain zur Cross-Kompilierung, Quantisierung und zum Deployment lokaler LLMs auf Rockchip NPU Targets (RK3588, RK3576, RK3566).**

Dieses Framework automatisiert den gesamten Lebenszyklus von Edge AI: vom Download der Modelle (HuggingFace) über die Konvertierung ins GGUF-Format und hardwarespezifische Quantisierung bis hin zum Air-Gap-Deployment auf Embedded-Geräten.

---

## 🚀 Hauptfunktionen

### 🛡️ Enterprise Security (v2.3)
Das Framework erzwingt strenge Sicherheitsvalidierungen in allen Modulen für den sicheren Einsatz in Unternehmensumgebungen:
* **SSRF-Schutz:** Der Crawler nutzt eine zentralisierte Validierungslogik, um Zugriffe auf Localhost, private IP-Bereiche und Nicht-HTTP-Schemata strikt zu blockieren.
* **Deployment-Härtung:** Ziel-IP-Adressen werden gegen strenge Muster validiert, bevor jegliche Socket-Verbindung oder SSH-Handshake versucht wird.
* **Audit-Ready:** Automatisierte CI-Skripte (`ci_image_audit.sh`) prüfen Docker-Container auf Effizienz und Layer-Sicherheit ohne Host-Abhängigkeiten.

### 🏗️ Kernarchitektur
* **Cross-Compilation:** Native Docker-Container garantieren reproduzierbare Builds für AArch64-Architekturen auf x86-Hosts.
* **Smarte Quantisierung:** Automatische Auswahl von Quantisierungsmethoden (z.B. `Q4_K_M`), balanciert für spezifische NPU-Speicherlimits.
* **Slim-RAG Strategie:** Deployt eine "leere" Vektor-DB-Struktur auf das Zielgerät. Das Gerät lernt lokal; es werden keine massiven Datenbanken transferiert.
* **Polite Crawler:** Eine respektvolle Ingest-Engine, die `robots.txt` beachtet, Rate-Limits einhält und PDFs/HTML für den RAG-Kontext parst.

---

## 📋 Voraussetzungen

Vor der Installation muss sichergestellt sein, dass das System folgende Anforderungen erfüllt:

### Windows Nutzer ⚠️
* **Docker Desktop** muss installiert sein und laufen.
* Das **WSL 2 Backend** muss in den Docker-Einstellungen aktiviert sein.
* Dies ist zwingend erforderlich, damit die Cross-Compilation-Container korrekt arbeiten.

### Linux Nutzer
* Eine Standard-Installation von **Docker** ist erforderlich (das Installationsskript kann dies meist automatisch einrichten).

---

## 📦 Installation

Wir haben den Installationsprozess in zwei "Single Source of Truth" Skripten zusammengefasst.

### Windows
1. **Als Admin ausführen:** Rechtsklick auf `install.bat` und "Als Administrator ausführen" wählen.
2. **Prozess:** Das Skript prüft auf Python 3.11 bis 3.14 (installiert 3.13 via Winget falls fehlend), erstellt ein isoliertes `.venv`, installiert alle Abhängigkeiten und legt Desktop-Verknüpfungen an.

### Linux / macOS
1. Terminal im Repository-Root öffnen.
2. Installer starten:
```bash
   sudo ./install.sh
```
3. **Prozess:** Installiert Systemabhängigkeiten, korrigiert Docker-Gruppenrechte für den User und deployt das Framework nach `/opt/llm-conversion-framework`.

---

## 🖥️ Bedienungsanleitung

### 1. Die Orchestrator GUI
Start über die Desktop-Verknüpfung (Windows) oder CLI.

* **Source Tab:** Suche und Download von Modellen direkt von HuggingFace. Validiert SHA256-Integrität.
* **Convert Tab:** Steuert die Konvertierungs-Pipeline.
    * *Input:* Rohes PyTorch/Safetensors Modell.
    * *Output:* NPU-optimiertes GGUF-Format.
    * *Opt-in:* Hardware-Flags für spezifische Boards aktivierbar.
* **Deploy Tab:** Verbindungsmanagement zu Edge-Geräten.
    * *Features:* SSH-Key-Management, Generierung von Air-Gap-Paketen (ZIP inkl. Docker-Images) und One-Click-Deployment.

### 2. Der Wizard (CLI)
Für Headless-Server oder Linux-Nutzer bietet der Wizard eine interaktive Anleitung.

**Start:**
```bash
./start_framework.bat   # Windows
llm-framework           # Linux (falls global installiert)
```

**Workflow:**
1. **Operation wählen:** Download / Convert / Quantize / Deploy.
2. **Target wählen:** Wähle dein Board (z.B. "Orange Pi 5").
3. **Optimierung:** Der Wizard schlägt die beste Quantisierung basierend auf dem Ziel-RAM vor.

### 3. Containerisiertes Build-System
Die Kernlogik läuft in Docker, um Plattformunabhängigkeit zu sichern.
```bash
make build              # Image bauen
make test-container     # Isolierte Tests ausführen
```

---

### 4. KI-Clients über MCP (Claude Code, Claude Desktop, VS Code)

Das Framework läuft als [Model-Context-Protocol](https://modelcontextprotocol.io)-Server. Ein KI-Client, den du
ohnehin nutzt, kann es damit steuern: Targets auflisten, prüfen, was ein Board kann, Builds starten und
verfolgen, Logs lesen und über Self-Healing-Vorschläge entscheiden. Im Client meldest du dich mit deinem
eigenen Claude-Konto oder API-Key an; das Framework sieht diese Zugangsdaten nie.

```bash
llm-cli ditto setup        # einmalig: was KI-Clients selbst dürfen und was deine Bestätigung braucht
llm-cli mcp config         # zeigt die Befehle/Snippets für Claude Code, Claude Desktop und VS Code
llm-cli mcp config --write claude-desktop   # oder direkt in Claude Desktop eintragen (mit Backup)
```

- **Freigaben:** Lesende Tools sind standardmäßig erlaubt; Builds starten oder abbrechen fragt nach deiner
  Bestätigung (der Client zeigt die Frage), außer du hast es erlaubt. Tools, die andere Geräte erreichen,
  lassen sich nur einzeln erlauben. Jeder Aufruf landet in einem manipulationssicheren Audit-Log
  (`llm-cli tools audit`).
- **Transporte:** stdio (Standard, vom Client gestartet) oder lokales Streamable HTTP
  (`llm-cli mcp serve --transport http`, nur Loopback, Bearer-Token in `~/.llm-framework/mcp_http_token`).

## 🤝 Community & Zusammenarbeit

Wir glauben an die Kraft offener Zusammenarbeit.

* **Target Module teilen:** Wenn du mit dem Wizard ein Config-Modul für ein neues Board generiert hast, stelle es bitte per Pull Request bereit.
* **RAG Wissen:** Wir ermutigen zum Teilen von nicht-sensitiven RAG-Datensätzen, um die kollektive Intelligenz der Edge-Geräte zu verbessern.

---

## 🛠️ Konfiguration

**Ort:** `configs/user_config.yml` (oder im Datenverzeichnis).
```yaml
crawler_respect_robots: true
crawler_max_depth: 2
enable_rag_knowledge: true
target_architecture: "aarch64"
```

---

## 🙏 Danksagung

* **[llama.cpp](https://github.com/ggerganov/llama.cpp)** - Das Herzstück der Inferenz
* **[Hugging Face](https://huggingface.co/)** - Für das Modell-Ökosystem
* **[Ditto](https://github.com/yoheinakajima/ditto)** - AI-Agent Framework für automatische Hardware-Modul-Generierung (entwickelt von [@yoheinakajima](https://github.com/yoheinakajima))
* **[Qdrant](https://qdrant.tech/)** - Vektor-Datenbank für unsere Lokale Wissensdatenbank
* **[Radxa Community](https://forum.radxa.com/)** - Für den Support bei der RK3566 Integration
* **[Docker](https://www.docker.com/)** - Containerization Platform
* **[PySide6](https://doc.qt.io/qtforpython-6/)** - Professional GUI Framework
* **[Poetry](https://python-poetry.org/)** - Modern Python Dependency Management

---

## 📄 Lizenz

Dieses Projekt ist lizenziert unter der **MIT License**.
