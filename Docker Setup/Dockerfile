# The LLM Cross-Compiler Framework in a container, built on your own computer.
#
# No framework image is published or pulled: scripts/build_container_image.sh builds this image locally, from
# exactly the files the repository ships (git ls-files), with a local tag:
#   scripts/build_container_image.sh          # headless: the CLI and the MCP server (default)
#   scripts/build_container_image.sh --gui    # also the desktop libraries for the GUI (explicit option)
# Guide: docs/Container_Setup.md (German: docs/Container_Setup_DE.md).
#
# Inside, install.sh runs exactly as on a Debian computer (apt, /opt/llm-conversion-framework, .venv with the pinned
# requirements.txt), for the unprivileged user lcf; only the Docker engine is left out (LCF_CONTAINER_BUILD=1).
# No secret enters a layer: the sources are a read-only bind mount of the staged context (tracked files only), and
# everything personal (settings, credential vault, audit log, MCP tokens, models, builds) lives in /data at run time.

# Debian 13 (trixie) slim, pinned by digest (as debian-13 in configs/llama_runtime.yml); Dependabot proposes updates.
FROM debian:13-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

# 1 installs the libraries Qt needs for the GUI on an X11 display; 0 (default) keeps the image headless.
ARG WITH_GUI=0
# The container user's ids; the builder passes yours (never 0), so files in a bind-mounted data folder are yours.
ARG LCF_UID=1000
ARG LCF_GID=1000

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# A minimal server install: no recommended packages. tini reaps the processes of docker exec sessions.
# hadolint ignore=DL3008
RUN echo 'APT::Install-Recommends "false";' > /etc/apt/apt.conf.d/90-lcf-no-recommends \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && if [ "$WITH_GUI" = "1" ]; then \
        apt-get install -y --no-install-recommends fontconfig fonts-dejavu-core libdbus-1-3 libegl1 libgl1 \
            libglib2.0-0t64 libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
            libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxkbcommon-x11-0 libxkbcommon0; \
    fi \
    && rm -rf /var/lib/apt/lists/*

RUN if [ "$LCF_UID" = "0" ] || [ "$LCF_GID" = "0" ]; then echo "LCF_UID/LCF_GID must not be 0 (root)." >&2; exit 1; fi \
    && groupadd --gid "$LCF_GID" lcf \
    && useradd --uid "$LCF_UID" --gid lcf --create-home --shell /bin/bash lcf

# The same installation as on bare metal, as if 'sudo ./install.sh' was started by the user lcf. The staged context
# carries the marker .lcf-container-context; a build from the repository folder itself (which may hold untracked
# files such as settings or keys) is refused before anything is copied.
RUN --mount=type=bind,source=.,target=/src \
    if [ ! -f /src/.lcf-container-context ]; then \
        echo "Build this image with scripts/build_container_image.sh (it stages only the files the repository ships)." >&2; \
        exit 1; \
    fi \
    && SUDO_USER=lcf LCF_CONTAINER_BUILD=1 bash /src/install.sh \
    && rm -f /opt/llm-conversion-framework/.lcf-container-context \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# llm-cli on the PATH (on bare metal the installers leave that to you), started like the MCP client configs do:
# -P keeps the working directory off sys.path, the installation comes from PYTHONPATH.
RUN printf '%s\n' '#!/bin/sh' \
        'exec env PYTHONPATH=/opt/llm-conversion-framework /opt/llm-conversion-framework/.venv/bin/python -P -m orchestrator.cli "$@"' \
        > /usr/local/bin/llm-cli \
    && chmod 0755 /usr/local/bin/llm-cli \
    && install -d -o lcf -g lcf -m 0700 /data

# Everything personal lives in the volume /data. The credential vault needs a protection you choose
# (setting secret_storage); there is no keyring in a container, and nothing is chosen for you.
ENV LLM_FRAMEWORK_HOME=/data/home \
    LLM_FRAMEWORK_CONFIG_DIR=/data/config \
    LLM_FRAMEWORK_OUTPUT_DIR=/data/output \
    LLM_FRAMEWORK_CACHE_DIR=/data/cache \
    LLM_FRAMEWORK_MODELS_DIR=/data/models \
    LLM_FRAMEWORK_NO_AUDIO=1 \
    HF_HOME=/data/huggingface \
    XDG_CACHE_HOME=/data/xdg-cache

VOLUME ["/data"]
# Numeric, so a runtime that checks "not root" (Kubernetes runAsNonRoot, Podman) can verify it.
USER ${LCF_UID}:${LCF_GID}
WORKDIR /data

ENTRYPOINT ["/usr/bin/tini", "--"]
# Headless by default. The compose file keeps the container running (sleep infinity) for docker exec sessions:
#   docker exec -i lcf-framework llm-cli mcp serve      (stdio MCP server for an AI client)
# The GUI starts only on request: llm-framework (image built with --gui, X11 display passed in).
CMD ["llm-cli", "--help"]
