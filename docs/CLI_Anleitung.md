# CLI Benutzerhandbuch (V2.3 Enterprise)

Die Kommandozeile (CLI) ist das mächtige Werkzeug für Headless-Server, CI/CD-Pipelines und Power-User. Sie bietet Zugriff auf **alle** Funktionen des Frameworks.

**Einstiegspunkt:** `python3 orchestrator/cli.py [BEFEHL] [OPTIONEN]`

---

## 1. Hardware-Analyse (`scan`)
Führt das Hardware-Probe-Skript aus (Linux/Windows) und generiert die Konfigurationsdatei.

```bash
# Standard Scan
python3 orchestrator/cli.py scan

# Mit spezifischem Ausgabepfad
python3 orchestrator/cli.py scan --output mein_server_profil.txt
```

Output: Erstellt target_hardware_config.txt im aktuellen Verzeichnis.

2. Build Management (build)
Startet und überwacht Konvertierungs-Jobs.

Start eines Builds
```bash
python3 orchestrator/cli.py build \
  --model meta-llama/Llama-2-7b-chat-hf \
  --target rk3588 \
  --quant Q4_K_M \
  --format GGUF \
  --priority HIGH \
  --gpu
```

Optionen:

--model: HuggingFace ID oder lokaler Pfad.

--target: Name des Zielordners in targets/ (z.B. rk3588, nvidia_jetson).

--quant: Quantisierungsmethode (Q4_K_M, Q8_0, F16).

--format: Zielformat (GGUF, ONNX, TFLITE, RKNN).

--priority: LOW, NORMAL, HIGH, URGENT.

--gpu: Aktiviert GPU-Passthrough für den Build-Container (für schnellere Quantisierung).

Status prüfen

```bash
# Alle Jobs auflisten
python3 orchestrator/cli.py build status --all

# Details zu einem spezifischen Job
python3 orchestrator/cli.py build status [REQUEST_ID]
```
3. Swarm Memory (swarm)
Teilen von Wissen und Konfigurationen mit der Community.

```bash
# Upload einer Knowledge-Base (verschlüsselt)
python3 orchestrator/cli.py swarm upload --file knowledge_export.json --user MeinName
```

Sicherheit: Die Datei wird vor dem Upload mit SwarmCipher (XOR+Obfuscation) verschlüsselt.

Voraussetzung: Ein GitHub-Token muss im Secrets Manager hinterlegt sein.

4. Secrets Management (secrets)

Verwaltung sensibler Daten (API-Keys, Passwörter) im sicheren OS-Keyring.

```bash
# Secret setzen (Interaktive Passworteingabe)
python3 orchestrator/cli.py secrets set openai_api_key

# Wert direkt übergeben (Vorsicht in der History!)
python3 orchestrator/cli.py secrets set github_token "ghp_..."

# Secret lesen (entschlüsselt)
python3 orchestrator/cli.py secrets get openai_api_key

# Verfügbare Keys auflisten
python3 orchestrator/cli.py secrets list
```

5. Deployment (deploy)
Erstellung von "Golden Artifacts" und Deployment auf Geräte.

Paket erstellen (Packaging)
Erstellt ein ZIP-Archiv mit Modell, Runtime und Start-Skripten.

```bash
python3 orchestrator/cli.py deploy package \
  --artifact output/my_model_q4.gguf \
  --profile rk3588_box \
  --docker
```
--docker: Inkludiert Docker-Images und docker-compose.yml für ein Air-Gap Deployment.

Remote Ausführung
Kopiert das Paket via SSH auf das Ziel und startet es.

```bash
python3 orchestrator/cli.py deploy run \
  output/deploy_pkg_2025.zip \
  --ip 192.168.1.50 \
  --user root \
  --password "geheim"
```

6. Modul-Entwicklung (module)
Hilfswerkzeuge für Entwickler.

```bash
# Neues Modul interaktiv erstellen (CLI Wizard)
python3 orchestrator/cli.py module create

# AI-Generierung aus Probe-Datei (Headless Wizard)
python3 orchestrator/cli.py module generate-ai target_hardware_config.txt
```
7. Konfiguration (config)

```bash
# Aktuelle Konfiguration anzeigen
python3 orchestrator/cli.py config show
```



