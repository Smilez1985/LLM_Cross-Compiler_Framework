# Ditto's local model server in a container (orchestrator/runtime/container_server.py, runtime mode "docker").
#
# Built locally on the user's machine, never pulled as a finished image and never pushed anywhere: the framework
# shares this recipe, not an image (decision D21, DECISIONS round 7 D). BASE is a Docker Official Image of
# configs/llama_runtime.yml (build_bases), pinned by digest: the base the builder container compiled the programs on,
# or, for programs built natively on this machine, a base with the same C library that is at least as new. The image
# build has network for the base's own signed package archive (runtime libraries only). Nothing here downloads
# llama.cpp, a model or a binary, and no licence is accepted.
#
# Two stages. runtime-base: the base and the runtime libraries (the OpenMP and C++ runtimes; for a Vulkan build the
# Vulkan loader and Mesa's drivers; NVIDIA's driver comes from the NVIDIA Container Toolkit when the container starts).
# A new build is verified in a container of this stage before it is stored. ditto: the programs of one verified build,
# copied from the build store into the build context's bin/ folder.
#
# LIBC: glibc (Debian and Ubuntu bases) or musl (Alpine). BACKEND: cpu or vulkan.
ARG BASE
# hadolint ignore=DL3006
FROM ${BASE} AS runtime-base
ARG LIBC=glibc
ARG BACKEND=cpu
SHELL ["/bin/sh", "-eu", "-c"]
# Package versions follow the digest-pinned base's release.
# hadolint ignore=DL3008,DL3018
RUN if [ "$LIBC" = "glibc" ]; then \
        export DEBIAN_FRONTEND=noninteractive; \
        packages="libgomp1 libstdc++6"; \
        case "$BACKEND" in \
            cpu) ;; \
            vulkan) packages="$packages libvulkan1 mesa-vulkan-drivers" ;; \
            *) echo "unsupported BACKEND: $BACKEND" >&2; exit 2 ;; \
        esac; \
        apt-get update; \
        apt-get install -y --no-install-recommends $packages; \
        rm -rf /var/lib/apt/lists/*; \
    elif [ "$LIBC" = "musl" ]; then \
        packages="libgomp libstdc++"; \
        case "$BACKEND" in \
            cpu) ;; \
            vulkan) packages="$packages vulkan-loader mesa-vulkan-ati mesa-vulkan-intel" ;; \
            *) echo "unsupported BACKEND: $BACKEND" >&2; exit 2 ;; \
        esac; \
        apk add --no-cache $packages; \
    else \
        echo "unknown LIBC: $LIBC" >&2; exit 2; \
    fi
LABEL org.llm-framework.ditto-runtime="base"

FROM runtime-base AS ditto
# The programs and the shared libraries next to them (found through their $ORIGIN run path).
COPY bin/ /opt/ditto/bin/
WORKDIR /opt/ditto/bin
# The server runs as the host user (docker create --user), never as root, on a read-only root file system; its
# command line comes from orchestrator/runtime/server_policy.py (container_server_command).
LABEL org.llm-framework.ditto-runtime="image"
