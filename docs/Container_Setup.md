Version 3.0.0 plus the [unreleased changes](../CHANGELOG.md#unreleased) · Last updated 2026-10-12

**English** · [Deutsch](Container_Setup_DE.md)

# The framework in a container

This guide is for running the framework in a container that you build yourself, for example on a server, a NAS or
a Proxmox host. **No framework image is published or pulled.** A ready-made image would have to be rebuilt and
maintained for every update, so you build the image on your own computer instead. The builder runs the same
installation inside the container as on a Debian computer (`install.sh`), from exactly the files the repository
ships. Nothing is pushed anywhere.

On a desktop PC the [normal installation](../README.md#installation) is simpler. A container is not needed for Ditto
to use the framework on another computer either ([Ditto and a framework on another
computer](Features.md#ditto-and-a-framework-on-another-computer)).

## Contents

1. [What you need](#1-what-you-need)
2. [Build the image](#2-build-the-image)
3. [Start it and set it up once](#3-start-it-and-set-it-up-once)
4. [AI clients: the MCP server](#4-ai-clients-the-mcp-server)
5. [Where your data is](#5-where-your-data-is)
6. [The GUI (optional)](#6-the-gui-optional)
7. [Updating](#7-updating)
8. [Limits](#8-limits)
9. [The files](#9-the-files)
10. [Where Docker keeps images](#10-where-docker-keeps-images)

## 1. What you need

- Linux with **Docker Engine 23 or newer** and BuildKit (`docker buildx`), and **git**.
- A clone of this repository (the image is built from the files `git ls-files` lists).
- **Internet access while building:** the Debian base image (pinned by digest), Debian packages and the pinned
  Python packages from PyPI. Running the container afterwards needs the internet only for what the framework itself
  downloads (models, AI providers).
- About 1.5 GB of disk space for the image, plus your models and builds in the data volume.

## 2. Build the image

```bash
make build                          # or: scripts/build_container_image.sh
```

What happens:

1. The builder checks Docker, BuildKit and git, and stops with a clear message when one is missing (exit code 2).
2. It copies exactly the files the repository ships (`git ls-files`) into a temporary folder. Untracked files in your
   repository folder, such as `configs/config.yml`, keys or `.env` files, never reach the build.
3. Docker builds [`Docker Setup/Dockerfile`](../Docker%20Setup/Dockerfile) from that folder:
   - base: Debian 13 slim, pinned by digest;
   - a user `lcf` with your user and group id (never 0, root), so files in a bind-mounted data folder belong to you;
     when your group id exists in Debian already (for example 100, `users`), that group is used;
   - `install.sh` runs as on a Debian computer, as if started with `sudo` by `lcf`: it installs Python with `apt`,
     copies the program to `/opt/llm-conversion-framework`, creates `.venv` there with the pinned
     `requirements.txt` and writes the `llm-framework` launcher. Only the Docker engine and the `docker` group are
     left out (`LCF_CONTAINER_BUILD=1`);
   - `llm-cli` is put on the PATH, and `llmcf`, its new name (unreleased);
   - the sources are a read-only mount during the build, so they never become an image layer, and no secret is in
     any layer;
   - at the end the build checks the installation: the command line must import, and every system library Qt loads
     must be there (for `--gui` also those of the desktop and the X11 platform plugin). Otherwise the build stops
     and names what is missing, instead of giving an image whose `llm-cli` fails at the first command. When `pip`
     cannot install the pinned packages, `install.sh` stops the build too.
4. The image gets the local tag `lcf-framework:local`, and the builder prints how to run it.

A build from the repository folder itself (`docker build -f "Docker Setup/Dockerfile" .`) is refused before
anything is copied, because that folder may hold untracked personal files.

| Option | Meaning |
|---|---|
| `--gui` | Also install the desktop libraries for the GUI; tag `lcf-framework:local-gui` (`make build-gui`) |
| `--tag NAME` | Another local tag. A name with a registry (`ghcr.io/...`, `host:5000/...`) is refused: the image stays on this computer |
| `--uid N`, `--gid N` | The container user's ids (default: yours; 0 becomes 1000) |

## 3. Start it and set it up once

```bash
make up                             # docker compose -f "Docker Setup/docker-compose.yml" up -d framework
```

The container `lcf-framework` keeps running (`sleep infinity`), so you and your AI clients can start framework
commands in it with `docker exec`. Compose never pulls the image (`pull_policy: never`), publishes no port and drops
all capabilities. Then, once:

1. **Choose how the credential vault is protected.** A container has no keyring, and the framework chooses nothing
   for you: until you choose, commands that need the framework stop with a message naming the choices.
   - **A key file given to the container as a Docker secret** (recommended on a server). Create a file only you can
     read, for example with `(umask 077 && head -c 32 /dev/urandom | base64 > ~/lcf-vault-key)` (the file is
     never readable by others, not even for a moment), and a compose file of your own next to it
     (`~/lcf-compose.override.yml`):

     ```yaml
     services:
       framework:
         secrets: [lcf_vault_key]
       gui:                             # only if you use make gui
         secrets: [lcf_vault_key]
     secrets:
       lcf_vault_key:
         file: /path/to/lcf-vault-key   # the absolute path of your key file
     ```

     The container user has your user id, so it can read the file. Every `make` call needs this file from now on:
     without it, `make up` replaces the container by one without the secret, and the framework stops until you pass
     it again. So set the variable once in your shell profile (for example `~/.profile`) and open a new terminal:
     `export LCF_COMPOSE_OVERRIDE="$HOME/lcf-compose.override.yml"`. Then run `make up` and
     `docker exec -it lcf-framework llm-cli ditto auth storage --use helper --helper "cat /run/secrets/lcf_vault_key"`.
   - **`--use insecure`**: a key file next to the vault in the data volume. Whoever can read the volume can read your
     credentials.
   - `passphrase` asks at every start of a framework process, so it does not work for MCP sessions started with
     `docker exec -i`. `LLM_SECRET_STORAGE=ephemeral` keeps the vault in memory for a single command.
2. **Tool permissions:** `docker exec -it lcf-framework llm-cli ditto setup`.
3. **Health check:** `docker exec -it lcf-framework llm-cli mojo` (or `make mojo`). It reports that Docker is
   not reachable: the container builds no models ([Limits](#8-limits)).

## 4. AI clients: the MCP server

The HTTP transport of `llm-cli mcp serve` listens on loopback only, and inside a container that is the container's
own loopback. AI clients therefore reach the containerized framework over **stdio**:

- **On the same computer:** the client starts `docker exec -i lcf-framework llm-cli mcp serve`. For Claude Code you
  run `claude mcp add lcf -- docker exec -i lcf-framework llm-cli mcp serve` yourself; the framework never starts
  the claude command.
- **From another computer, over SSH to the host:** the client's command is
  `ssh user@server docker exec -i lcf-framework llm-cli mcp serve`. The SSH user needs Docker access on the server,
  which is as much as root access there.
- **A named token instead of the shared caller `mcp`:** create it in the container with
  `docker exec -it lcf-framework llm-cli mcp token add laptop` and pass it as an environment variable, never on the
  command line: `docker exec -i -e LCF_MCP_TOKEN lcf-framework llm-cli mcp serve`, with `LCF_MCP_TOKEN` set in the
  environment the client gives that command. Ditto on another computer adds `-e LCF_MCP_TOKEN` to a `docker exec`
  command by itself.
- **HTTP instead** (advanced): `llm-cli mcp serve --transport http --container` inside the container binds the
  container's interfaces; publish the port to the host's loopback only (`-p 127.0.0.1:8765:8765`) and reach it from
  another computer through an SSH tunnel. Every client needs its own named token; the compose file publishes no port.

## 5. Where your data is

Everything personal lives in the Docker volume `lcf-data`, mounted at `/data`:

| Folder | What | Set by |
|---|---|---|
| `/data/home` | Personal state: tool permissions, audit log, credential vault, SSH key and known devices, MCP tokens, chat history, knowledge base | `LLM_FRAMEWORK_HOME` |
| `/data/config` | `config.yml` | `LLM_FRAMEWORK_CONFIG_DIR` |
| `/data/output`, `/data/cache`, `/data/models` | Builds, cache, downloaded models | `LLM_FRAMEWORK_OUTPUT_DIR`, `..._CACHE_DIR`, `..._MODELS_DIR` |
| `/data/datasets`, `/data/logs` | Calibration datasets, log files | `LLM_FRAMEWORK_DATASETS_DIR`, `..._LOGS_DIR` |
| `/data/huggingface`, `/data/xdg-cache` | Hugging Face downloads and other caches | `HF_HOME`, `XDG_CACHE_HOME` |

Back the volume up like any Docker volume.

The program folder `/opt/llm-conversion-framework` belongs to `lcf`, as it belongs to your user on bare metal.
Targets you create with the module wizard or `llm-cli module create` and community packs you install are written
there, so they live in the container itself, not in the volume, and are lost when the container is removed or
replaced. The make targets therefore never remove or replace a container that holds files of yours outside the
volume:

- `make down` stops the containers and removes none.
- `make up` and `make gui` start a container that holds files of yours as it is (a new image or changed settings
  then need a new container, see [Updating](#7-updating)); a container without such files is replaced as usual.
- `make clean` removes only containers that hold nothing of yours. For each other one it lists every file (full path,
  file name, size and what it is) and the command to remove the container yourself; it never removes the volume.
- `make kept-files` shows those files at any time ([`scripts/container_kept_files.sh`](../scripts/container_kept_files.sh)).

## 6. The GUI (optional)

The image is headless by default. The GUI is an explicit option:

```bash
make build-gui                      # the image with Qt's desktop libraries
make gui                            # allows your user on the X11 display, starts the container lcf-framework-gui
```

The GUI container gets access to your X11 display (`/tmp/.X11-unix` and `DISPLAY`), so it can see the screen and
keyboard input of that display. `make gui` allows only your own user (`xhost +SI:localuser:<you>`); the container
user has your user id when you built the image as yourself. Wayland desktops work through XWayland.

## 7. Updating

```bash
git pull
make build
make up
```

The data volume stays. `make up` replaces the container with the new image when it holds nothing of yours outside
the volume. Otherwise it keeps the old container running, lists those files and says what to do: copy them into the
volume first (a target of your own as a pack:
`docker exec lcf-framework llm-cli zoo export target NAME --author "<your name>" --out /data/packs`, see
[CLI_Usage.md](CLI_Usage.md#zoo-export)), remove the old container yourself (`docker rm -f lcf-framework`), run
`make up` again and install the packs again.

## 8. Limits

- **No conversions inside the container.** The image contains no Docker engine, and builds need Docker on the
  computer that converts. Build on a computer where the framework is installed directly. Giving the container the
  host's Docker socket is not offered: it would give the container root-equivalent rights on the host.
- **No keyring:** see step 1 of [Start it and set it up once](#3-start-it-and-set-it-up-once).
- **Targets and community packs** live in the container's program folder, not in the volume: moving to a new image
  needs an export first ([Updating](#7-updating)).
- **Spoken announcements** are off (`LLM_FRAMEWORK_NO_AUDIO=1`).
- The image is built and smoke-tested in CI (`.github/workflows/framework-container.yml`); it has not been tested
  on Proxmox or a NAS yet.

## 9. The files

| File | Purpose |
|---|---|
| [`scripts/build_container_image.sh`](../scripts/build_container_image.sh) | The builder: checks, stages the shipped files, builds with a local tag, prints how to run it |
| [`scripts/container_kept_files.sh`](../scripts/container_kept_files.sh) | Lists the files of yours a container holds outside the data volume; the make targets never remove or replace such a container |
| [`Docker Setup/Dockerfile`](../Docker%20Setup/Dockerfile) | The image: Debian 13 slim pinned by digest, `install.sh`, user `lcf`, `/data`, headless by default |
| [`Docker Setup/Dockerfile.dockerignore`](../Docker%20Setup/Dockerfile.dockerignore) | Keeps large and personal folders out of a build context made from the repository folder itself |
| [`Docker Setup/docker-compose.yml`](../Docker%20Setup/docker-compose.yml) | The services `framework` and `gui` (profile `gui`), the volume `lcf-data`, `pull_policy: never` |
| [`Makefile`](../Makefile) | `make build`, `build-gui`, `up`, `gui`, `down`, `kept-files`, `mojo` (and its old name `doctor`), `shell`, `clean`, `audit` (Trivy scans an archive of the image; the scanner never gets the Docker socket) |
| `Docker Setup/pyproject.toml` | An old copy of the project file (version 1.6.0) that nothing uses; the project file is [`pyproject.toml`](../pyproject.toml) at the top of the repository |

## 10. Where Docker keeps images

This holds for every computer that builds with Docker, not only for the container set-up (unreleased).

- `llm-cli mojo` shows where Docker keeps its images (Docker's data folder, `DockerRootDir`), how much space is
  free on that disk, and how much the framework's build images use.
- The framework never deletes an image by itself. The builder keeps the newest image of each target as
  `llm-framework/<target>:last-build`; older ones stay without a name. You delete the ones you no longer need:
  **Tools > Framework Build Images...** in the GUI or `llm-cli doctor --fix --tier 2` in a terminal. Nothing is ticked;
  you pick.
- Docker's build cache is not the framework's. `docker system df` shows its size; `docker builder prune` clears it
  for every project on this computer. That is your own decision.
- To keep images on another disk, move Docker's data folder. This is a step for an administrator: it affects every
  Docker user and project on the computer, and the framework never changes Docker's settings.
  - Linux with Docker Engine: stop Docker (`sudo systemctl stop docker docker.socket`), copy the folder with its
    permissions (`sudo rsync -aHAX /var/lib/docker/ /mnt/bigdisk/docker/`), set `"data-root": "/mnt/bigdisk/docker"`
    in `/etc/docker/daemon.json`, and start Docker again (`sudo systemctl start docker`). Check it with
    `docker info --format '{{.DockerRootDir}}'`. Remove the old folder only after you checked that everything works.
  - Docker Desktop (Windows, macOS): Settings > Resources > Advanced > Disk image location.
  - Rootless Docker keeps the folder in your home folder; Docker's documentation on `data-root` describes the move.
