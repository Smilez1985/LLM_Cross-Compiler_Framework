# Builder image for self-built llama.cpp (orchestrator/runtime/build/executor.py, container and cross builds).
#
# Built locally on the user's machine, never pulled as a finished image: the framework shares this recipe, not a
# compiled image (decision D21). BASE is a build base of configs/llama_runtime.yml, pinned by digest and chosen so
# that its C library is not newer than the machine that runs the binaries. The image build has network: compilers,
# CMake and Ninja come from the base's own signed package archive. The llama.cpp configure and build steps run later
# in containers of this image with --network none, the verified source mounted read-only. Nothing here downloads
# llama.cpp or a binary, no licence is accepted, and the executor checks the GCC version against the catalog before
# every build (it is part of the build ID).
#
# LIBC: glibc (Debian and Ubuntu bases) or musl (Alpine). CROSS_TRIPLE: empty for the base's own architecture, or
# aarch64-linux-gnu (glibc) / aarch64-alpine-linux-musl (musl, clang with a sysroot in /opt/sysroot; unverified) for
# an ARM board.
ARG BASE
# hadolint ignore=DL3006
FROM ${BASE}
ARG LIBC=glibc
ARG CROSS_TRIPLE=""
SHELL ["/bin/sh", "-eu", "-c"]
# Package versions follow the digest-pinned base's release; the executor compares the compiler with the catalog.
# hadolint ignore=DL3008,DL3018
RUN if [ "$LIBC" = "glibc" ]; then \
        export DEBIAN_FRONTEND=noninteractive; \
        packages="gcc g++ make cmake ninja-build"; \
        case "$CROSS_TRIPLE" in \
            "") ;; \
            aarch64-linux-gnu) packages="$packages gcc-aarch64-linux-gnu g++-aarch64-linux-gnu libc6-dev-arm64-cross libgomp1-arm64-cross" ;; \
            *) echo "unsupported CROSS_TRIPLE for glibc: $CROSS_TRIPLE" >&2; exit 2 ;; \
        esac; \
        apt-get update; \
        apt-get install -y --no-install-recommends $packages; \
        rm -rf /var/lib/apt/lists/*; \
    elif [ "$LIBC" = "musl" ]; then \
        apk add --no-cache gcc g++ make cmake samurai musl-dev linux-headers; \
        case "$CROSS_TRIPLE" in \
            "") ;; \
            aarch64-alpine-linux-musl) \
                apk add --no-cache alpine-keys clang lld; \
                apk add --no-cache --no-scripts --initdb --root /opt/sysroot --arch aarch64 \
                    --keys-dir /usr/share/apk/keys/aarch64 --repositories-file /etc/apk/repositories \
                    musl-dev libstdc++-dev gcc g++ linux-headers ;; \
            *) echo "unsupported CROSS_TRIPLE for musl: $CROSS_TRIPLE" >&2; exit 2 ;; \
        esac; \
    else \
        echo "unknown LIBC: $LIBC" >&2; exit 2; \
    fi
# The build runs as the host user (docker run --user), with HOME and TMPDIR in the mounted work folder.
WORKDIR /work
LABEL org.llm-framework.builder="llama.cpp"
