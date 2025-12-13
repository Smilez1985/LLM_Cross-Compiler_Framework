# Leitfaden für Modulentwickler (V2.3 Enterprise)

Willkommen beim **LLM Cross-Compiler Framework**. Dieser Leitfaden erklärt, wie Sie Unterstützung für neue Hardware-Plattformen (Targets) hinzufügen.

Das System basiert auf einem **Modul-Template-System**. Jedes Target (z.B. `targets/rockchip_rk3588`) enthält vier Dateien, die die Cross-Kompilierung steuern. Sie müssen diese Dateien nicht manuell schreiben – nutzen Sie den **Wizard**.

---

## 🧙 Der Module Creation Wizard

Der Wizard (`orchestrator/gui/wizards.py`) ist das primäre Werkzeug zur Erstellung neuer Targets. Er unterstützt drei Arbeitsmodi, je nach Komplexität der Hardware.

Starten Sie ihn über die GUI: **Tools -> Create New Target Module**.

### Modus A: Manueller Modus (Der Experte)
*Einsatzgebiet: Völlig unbekannte Hardware oder sehr spezifische Custom-OS-Setups.*

1.  **Hardware:** Sie geben Architektur (`aarch64`, `riscv64`) und SDK-Namen manuell ein.
2.  **Docker:** Sie definieren das Basis-Image (z.B. `ubuntu:22.04`) und die Paketliste (`apt-get install ...`) selbst.
3.  **Flags:** Sie tippen die GCC-Flags (`-mcpu=...`) und CMake-Variablen von Hand ein.
4.  **Ergebnis:** Der Wizard erstellt die Ordnerstruktur, füllt aber nur Ihre Eingaben ein.

### Modus B: AI-Assisted (Ditto + Hardware Probe)
*Einsatzgebiet: Bekannte SBCs (Raspberry Pi, Jetson, Orange Pi) und Standard-CPUs.*

1.  **Probe:** Führen Sie `scripts/hardware_probe.sh` auf dem Zielgerät aus. Laden Sie die resultierende `target_hardware_config.txt` im Wizard hoch.
2.  **Analyse:** Ditto (der AI Agent) liest die Datei. Er erkennt:
    * CPU-Kerne und Architektur.
    * Verfügbare RAM-Menge.
    * Vorhandene Beschleuniger (GPU/NPU Vendor IDs).
3.  **Generierung:** Ditto generiert das `Dockerfile` und `config_module.sh` basierend auf seinem Trainingswissen über diese Hardware.
    * *Beispiel:* Er sieht "Cortex-A76" in der Probe und setzt automatisch `-mcpu=cortex-a76`.

### Modus C: AI Expert (Ditto + RAG Knowledge Base)
*Einsatzgebiet: Proprietäre NPUs, Bleeding-Edge Hardware oder spezielle SDKs (Rockchip RKLLM, HailoRT).*

1.  **Vorbereitung (Ingest):** Nutzen Sie vorher den "Deep Ingest" (im Wizard oder via CLI), um PDF-Handbücher oder Dokumentations-Webseiten des Herstellers in die lokale Vektor-Datenbank (Qdrant) zu laden.
2.  **Probe & RAG:** Laden Sie die Probe-Datei hoch und aktivieren Sie **"Enable Knowledge Base"**.
3.  **Synthese:**
    * Ditto analysiert die Probe.
    * Er sucht in der lokalen Datenbank (RAG) nach spezifischen Compiler-Flags für diese exakte SDK-Version.
    * Er kombiniert beides zu einem hochpräzisen Build-Skript, das auch undokumentierte oder sehr neue Flags berücksichtigt, die das Basis-LLM noch nicht kennt.

---

## 📂 Die Modul-Struktur

Jedes generierte Modul in `targets/` besteht aus diesen vier Dateien:

| Datei | Funktion | Status V2.3 |
| :--- | :--- | :--- |
| `Dockerfile` | Definiert die Build-Umgebung (Compiler, SDKs). | **Auto-Generiert** |
| `source_module.sh` | Lädt das Modell und konvertiert es zu FP16 GGUF. | **Statisch** (Template) |
| `config_module.sh` | **Das Herzstück.** Liest `target_hardware_config.txt` und exportiert `CMAKE_ARGS`. | **Auto-Generiert** |
| `target_module.sh` | Führt Quantisierung und Kompilierung aus. | **Statisch** (Template) |

### Wichtig für Manuelle Anpassungen
Wenn Sie das `config_module.sh` bearbeiten: **Hardcoden Sie keine Werte!**
Nutzen Sie die Helper-Funktion, um Werte dynamisch aus der Probe-Datei zu lesen:
```bash
# SCHLECHT:
export CPU_CORES=4

# GUT (Goldstandard):
CPU_CORES=$(cat /build-cache/target_hardware_config.txt | grep "CPU_CORES" | cut -d= -f2)
```
Nur so bleibt Ihr Modul flexibel für verschiedene Varianten eines Boards (z.B. 4GB vs 8GB RAM).

