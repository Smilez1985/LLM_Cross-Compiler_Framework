# CLI User Guide (V2.3 Enterprise)

The Command Line Interface (CLI) is the power tool for headless servers, CI/CD pipelines, and advanced users. It provides access to **all** framework features.

**Entry Point:** `python3 orchestrator/cli.py [COMMAND] [OPTIONS]`

---

## 1. Hardware Analysis (`scan`)
Executes the hardware probe script (Linux/Windows) and generates the configuration file.

```bash
# Standard Scan
python3 orchestrator/cli.py scan

# With specific output path
python3 orchestrator/cli.py scan --output my_server_profile.txt
```

Output: Creates target_hardware_config.txt in the current directory.

2. Build Management (build)
Starts and monitors conversion jobs.

Start a Build

```bash
python3 orchestrator/cli.py build \
  --model meta-llama/Llama-2-7b-chat-hf \
  --target rk3588 \
  --quant Q4_K_M \
  --format GGUF \
  --priority HIGH \
  --gpu
```
Options:

--model: HuggingFace ID or local path.

--target: Name of the target folder in targets/ (e.g., rk3588, nvidia_jetson).

--quant: Quantization method (Q4_K_M, Q8_0, F16).

--format: Target format (GGUF, ONNX, TFLITE, RKNN).

--priority: LOW, NORMAL, HIGH, URGENT.

--gpu: Enables GPU passthrough for the build container (speeds up quantization).

Check Status

```bash
# List all jobs
python3 orchestrator/cli.py build status --all

# Details for a specific job
python3 orchestrator/cli.py build status [REQUEST_ID]
```
3. Swarm Memory (swarm)
Share knowledge and configurations with the community.

```bash
# Upload a Knowledge Base (encrypted)
python3 orchestrator/cli.py swarm upload --file knowledge_export.json --user MyUsername
```
Security: The file is encrypted using SwarmCipher (XOR+Obfuscation) before upload.

Requirement: A GitHub token must be stored in the Secrets Manager.

4. Secrets Management (secrets)
Manage sensitive data (API keys, passwords) in the secure OS Keyring.

```bash
# Set a secret (Interactive password prompt)
python3 orchestrator/cli.py secrets set openai_api_key

# Pass value directly (Caution: shows in history!)
python3 orchestrator/cli.py secrets set github_token "ghp_..."

# Read a secret (decrypted)
python3 orchestrator/cli.py secrets get openai_api_key

# List available keys
python3 orchestrator/cli.py secrets list
```
5. Deployment (deploy)
Create "Golden Artifacts" and deploy to devices.

Create Package
Creates a ZIP archive containing the model, runtime, and startup scripts.

```bash
python3 orchestrator/cli.py deploy package \
  --artifact output/my_model_q4.gguf \
  --profile rk3588_box \
  --docker
```
--docker: Includes Docker images and docker-compose.yml for air-gapped deployment.

Remote Execution
Copies the package via SSH to the target and executes it.
```bash
python3 orchestrator/cli.py deploy run \
  output/deploy_pkg_2025.zip \
  --ip 192.168.1.50 \
  --user root \
  --password "secret"
```

6. Module Development (module)
Helper tools for developers.

```bash
# Create new module interactively (CLI Wizard)
python3 orchestrator/cli.py module create

# AI Generation from Probe File (Headless Wizard)
python3 orchestrator/cli.py module generate-ai target_hardware_config.txt
```
7. Configuration (config)
   
```bash
# Show current configuration
python3 orchestrator/cli.py config show
```
