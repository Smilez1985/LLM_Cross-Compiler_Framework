# The LLM Cross-Compiler Framework in a container, built on your own computer.
#
# No framework image is published or pulled: scripts/build_container_image.sh builds this image locally, from
# exactly the files the repository ships (git ls-files), with a local tag:
#   scripts/build_container_image.sh          # headless: the CLI and the MCP server (default)
#   scripts/build_container_image.sh --gui    # also the desktop libraries for the GUI (explicit option)
# Guide: docs/Container_Setup.md (German: docs/Container_Setup_DE.md).
#
# Inside, install.sh runs exactly as on a Debian computer (apt, /opt/llm-cross-compiler-framework, .venv with the pinned
# requirements.txt), for the unprivileged user lcf; only the Docker engine is left out (LCF_CONTAINER_BUILD=1).
# No secret enters a layer: the sources are a read-only bind mount of the staged context (tracked files only), and
# everything personal (settings, credential vault, audit log, MCP tokens, models, builds) lives in /data at run time.

# Debian 13 (trixie) slim, pinned by digest (as debian-13 in configs/llama_runtime.yml); Dependabot proposes updates.
FROM debian:13-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

# 1 installs the GUI (PySide6, the optional extra 'gui') and the libraries Qt needs on an X11 display; 0 (default)
# keeps the image headless: the CLI and the MCP server, without Qt.
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

# A minimal server install: no recommended packages. tini reaps the processes of docker exec sessions. The headless
# image has no Qt at all (the framework core is Qt-free; PySide6 is the optional extra 'gui'), so it needs neither
# libglib nor the desktop libraries. The GUI adds libglib (Qt's core library links against it) and what QtGui,
# QtWidgets and Qt's xcb platform plugin load (tests/test_container_setup.py checks the list against the Qt wheel's
# libraries).
# hadolint ignore=DL3008
RUN echo 'APT::Install-Recommends "false";' > /etc/apt/apt.conf.d/90-lcf-no-recommends \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && if [ "$WITH_GUI" = "1" ]; then \
        apt-get install -y --no-install-recommends fontconfig fonts-dejavu-core libdbus-1-3 libegl1 libgl1 \
            libglib2.0-0t64 libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
            libxcb-render-util0 libxcb-shape0 libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
            libxkbcommon-x11-0 libxkbcommon0; \
    fi \
    && rm -rf /var/lib/apt/lists/*

# Your primary group may exist in Debian already (100 "users" on openSUSE, 20 with Docker Desktop on a Mac): then the
# user lcf gets that group instead of a new one, so the build does not stop and your files keep your group.
RUN if [ "$LCF_UID" = "0" ] || [ "$LCF_GID" = "0" ]; then echo "LCF_UID/LCF_GID must not be 0 (root)." >&2; exit 1; fi \
    && if ! getent group "$LCF_GID" >/dev/null; then groupadd --gid "$LCF_GID" lcf; fi \
    && useradd --uid "$LCF_UID" --gid "$LCF_GID" --create-home --shell /bin/bash lcf

# The same installation as on bare metal, as if 'sudo ./install.sh' was started by the user lcf (headless: as
# 'sudo ./install.sh --headless', without the GUI's PySide6). The staged context carries the marker
# .lcf-container-context; a build from the repository folder itself (which may hold untracked files such as settings
# or keys) is refused before anything is copied.
RUN --mount=type=bind,source=.,target=/src \
    if [ ! -f /src/.lcf-container-context ]; then \
        echo "Build this image with scripts/build_container_image.sh (it stages only the files the repository ships)." >&2; \
        exit 1; \
    fi \
    && headless=1 && if [ "$WITH_GUI" = "1" ]; then headless=0; fi \
    && SUDO_USER=lcf LCF_CONTAINER_BUILD=1 LCF_HEADLESS="$headless" bash /src/install.sh \
    && rm -f /opt/llm-cross-compiler-framework/.lcf-container-context \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# llm-cli on the PATH (on bare metal the installers leave that to you), started like the MCP client configs do:
# -P keeps the working directory off sys.path, the installation comes from PYTHONPATH. llmcf is its new name.
RUN printf '%s\n' '#!/bin/sh' \
        'exec env PYTHONPATH=/opt/llm-cross-compiler-framework /opt/llm-cross-compiler-framework/.venv/bin/python -P -m orchestrator.cli "$@"' \
        > /usr/local/bin/llm-cli \
    && chmod 0755 /usr/local/bin/llm-cli \
    && ln -s llm-cli /usr/local/bin/llmcf \
    && install -d -o "$LCF_UID" -g "$LCF_GID" -m 0700 /data

# The image counts as built only when the installation works: the framework's command line imports; the headless
# image has no Qt; the GUI image has every system library Qt loads (QtCore, QtGui, QtWidgets and the xcb platform
# plugin). A missing one would otherwise only show at the first llm-cli or llm-framework command. Nothing is written
# here.
RUN PYTHONPATH=/opt/llm-cross-compiler-framework /opt/llm-cross-compiler-framework/.venv/bin/python -P -c "import orchestrator.cli" \
    && qt=$(echo /opt/llm-cross-compiler-framework/.venv/lib/python3*/site-packages/PySide6) \
    && set -- \
    && if [ "$WITH_GUI" = "1" ]; then \
        set -- "$qt/QtCore.abi3.so" "$qt/QtGui.abi3.so" "$qt/QtWidgets.abi3.so" "$qt/Qt/plugins/platforms/libqxcb.so"; \
    elif [ -e "$qt" ]; then \
        echo "The headless image must not install Qt (PySide6 is the optional extra 'gui'): $qt" >&2; \
        exit 1; \
    fi \
    && for lib in "$@"; do \
        [ -f "$lib" ] || { echo "Qt library missing in the installation: $lib" >&2; exit 1; }; \
        missing=$(ldd "$lib" | grep "not found" || true); \
        if [ -n "$missing" ]; then \
            printf '%s needs system libraries the image lacks (add their Debian packages to the apt step):\n%s\n' \
                "$lib" "$missing" >&2; \
            exit 1; \
        fi; \
    done

# Everything personal lives in the volume /data. Only targets you create and packs you install go to the program
# folder, as on bare metal: they stay in the container, and make never removes or replaces a container that holds
# them (scripts/container_kept_files.sh). The credential vault needs a protection you choose (setting
# secret_storage); there is no keyring in a container, and nothing is chosen for you.
ENV LLM_FRAMEWORK_HOME=/data/home \
    LLM_FRAMEWORK_CONFIG_DIR=/data/config \
    LLM_FRAMEWORK_OUTPUT_DIR=/data/output \
    LLM_FRAMEWORK_BUILD_DIR=/data/builds \
    LLM_FRAMEWORK_CACHE_DIR=/data/cache \
    LLM_FRAMEWORK_MODELS_DIR=/data/models \
    LLM_FRAMEWORK_DATASETS_DIR=/data/datasets \
    LLM_FRAMEWORK_LOGS_DIR=/data/logs \
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
