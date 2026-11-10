# Dockerfile of the generic ARM64 CPU target (GGUF): 64-bit ARM boards without a vendor SDK (target.yml; today the
# Raspberry Pi 4, BCM2711, and the Raspberry Pi 5, BCM2712)
# Build context: staged by orchestrator/utils/build_context.py (modules/, scripts/, docker/entrypoint.sh).
#
# One toolchain, no vendor SDK: /opt/venv holds llama.cpp's GGUF conversion (convert_hf_to_gguf.py, torch CPU), the
# llama.cpp source tree has its tools (build/bin: quantize, imatrix, completion, perplexity, bench), and the aarch64
# compilers cross-build llama-cli and llama-server for the board in the target stage. Debian Bookworm (Python 3.11),
# the same base as the Rockchip image, so the fetch and build steps of llama.cpp are the same (tests compare them).
#
# Two stages: the builder stage fetches and builds llama.cpp's tools and installs the venv (build-essential,
# python3-dev); the final image gets the venv and the llama.cpp source tree with its tools at the same paths, CMake,
# make, gcc and g++ with the aarch64 compilers for the package's cross build and the config stage's compiler test, git
# for `source_module.sh --clone-llama-cpp` by hand, gosu, the modules and the llmbuilder user. On an arm64 build
# computer the aarch64 compilers are the image's own gcc and g++ (Debian has no gcc-aarch64-linux-gnu for arm64; its
# gcc and g++ packages name them aarch64-linux-gnu-gcc and -g++).

FROM debian:bookworm-slim AS builder

# Parallel compile jobs; empty = all cores (the framework lowers it after an out-of-memory build).
ARG BUILD_JOBS=

ENV DEBIAN_FRONTEND=noninteractive
ENV LLAMA_CPP_PATH=/usr/src/llama.cpp
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# Sources in your own network for an offline build (orchestrator/Core/build_image.py): the person's apt mirror (it
# serves the distribution's paths, such as /debian and /debian-security) or apt cache proxy. The framework passes them
# only in offline mode, after the locality rule judged them; empty (the default) keeps the distribution's own sources.
# No credentials: a build argument shows in the image's history.
ARG APT_MIRROR
ARG APT_PROXY
RUN set -eu; \
    if [ -n "${APT_MIRROR:-}" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
            if [ -f "${f}" ]; then sed -Ei "s#https?://[^/[:space:]]+#${APT_MIRROR%/}#g" "${f}"; fi; \
        done; \
    fi; \
    if [ -n "${APT_PROXY:-}" ]; then \
        printf 'Acquire::http::Proxy "%s";\n' "${APT_PROXY}" > /etc/apt/apt.conf.d/01llmcf-proxy; \
    fi

# The person's pip index in the own network for an offline build (pip reads both from the environment; PIP_TRUSTED_HOST
# only for an http index). Not set: PyPI and the Dockerfile's own addresses.
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
        python3 python3-venv python3-dev pkg-config libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# --- llama.cpp conversion tools (convert, quantize, imatrix; completion, perplexity, bench): the source ---
# Built from the one llama.cpp pin (configs/llama_runtime.yml, llama_cpp.source) by commit, never by a tag alone: the
# framework passes LLAMA_CPP_TAG and LLAMA_CPP_COMMIT, and the build stops unless `git rev-parse HEAD` gives back the
# commit. LLAMA_CMAKE_ARGS comes from the build recipe (orchestrator/runtime/build/recipe.py, purpose host-tools): the
# CPU features of the build host's hardware probe, because the tools run on the machine that builds this image. The
# defaults are for builds outside the framework (CI): the pin, and the fixed x86-64 baseline (AVX2, no AVX-512) these
# tools always had, written out.
# LLAMA_CPP_SOURCE=context (an offline build without a mirror in the own network): the verified local copy of the
# pinned commit that the framework staged into sources/llama.cpp of the build context, checked here by its commit too;
# git (the default) fetches the commit from LLAMA_CPP_REPO.
ARG LLAMA_CPP_SOURCE=git
COPY sources/ /tmp/llmcf-sources/
ARG LLAMA_CPP_REPO=https://github.com/ggml-org/llama.cpp.git
ARG LLAMA_CPP_TAG=b11429
ARG LLAMA_CPP_COMMIT=d81235049384534c167caea52b85a694f6103d14
RUN set -eu; \
    printf '%s\n' "${LLAMA_CPP_COMMIT}" | grep -Eqx '[0-9a-f]{40}' \
        || { echo "LLAMA_CPP_COMMIT must be a full commit id (40 hex characters)" >&2; exit 1; }; \
    printf '%s\n' "${LLAMA_CPP_TAG}" | grep -Eqx 'b[0-9]{1,7}' \
        || { echo "LLAMA_CPP_TAG must be a build tag such as b11429" >&2; exit 1; }; \
    if [ "${LLAMA_CPP_SOURCE:-git}" = context ]; then \
        [ -d /tmp/llmcf-sources/llama.cpp/.git ] \
            || { echo "LLAMA_CPP_SOURCE=context, but the build context holds no llama.cpp copy" >&2; exit 1; }; \
        cp -a /tmp/llmcf-sources/llama.cpp "${LLAMA_CPP_PATH}"; \
        [ -z "$(git -C "${LLAMA_CPP_PATH}" status --porcelain=v1 --untracked-files=normal --ignored=traditional)" ] \
            || { echo "llama.cpp: the local copy has changed or added files" >&2; exit 1; }; \
    else \
        git init -q "${LLAMA_CPP_PATH}"; \
        git -C "${LLAMA_CPP_PATH}" remote add origin "${LLAMA_CPP_REPO}"; \
        if ! git -C "${LLAMA_CPP_PATH}" fetch -q --depth 1 origin "${LLAMA_CPP_COMMIT}"; then \
            echo "fetching the commit failed; fetching tag ${LLAMA_CPP_TAG}, which must be that commit" >&2; \
            git -C "${LLAMA_CPP_PATH}" fetch -q --depth 1 origin "refs/tags/${LLAMA_CPP_TAG}"; \
        fi; \
        git -C "${LLAMA_CPP_PATH}" -c advice.detachedHead=false checkout -q FETCH_HEAD; \
    fi; \
    head="$(git -C "${LLAMA_CPP_PATH}" rev-parse HEAD)"; \
    [ "${head}" = "${LLAMA_CPP_COMMIT}" ] \
        || { echo "llama.cpp: got commit ${head}, the pin is ${LLAMA_CPP_COMMIT}" >&2; exit 1; }; \
    rm -rf "${LLAMA_CPP_PATH}/.git"; \
    printf '%s\n' "${LLAMA_CPP_TAG}" > "${LLAMA_CPP_PATH}/.llama_cpp_tag"; \
    printf '%s\n' "${LLAMA_CPP_COMMIT}" > "${LLAMA_CPP_PATH}/.llama_cpp_commit"

# --- GGUF venv (default python3 on PATH) ---
# With a pip index of the own network the requirements' own --extra-index-url (the PyTorch index) is left out: the
# index serves every wheel.
ENV VIRTUAL_ENV=/opt/venv
RUN if [ -n "${PIP_INDEX_URL:-}" ]; then sed -i '/^[[:space:]]*--extra-index-url/d' "${LLAMA_CPP_PATH}"/requirements/*.txt; fi \
    && python3 -m venv "${VIRTUAL_ENV}" \
    && "${VIRTUAL_ENV}/bin/pip" install --upgrade pip setuptools wheel \
    && "${VIRTUAL_ENV}/bin/pip" install -r "${LLAMA_CPP_PATH}/requirements/requirements-convert_hf_to_gguf.txt" \
    && "${VIRTUAL_ENV}/bin/pip" install "${LLAMA_CPP_PATH}/gguf-py"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# --- llama.cpp conversion tools: the build (after the venv, so a new LLAMA_CMAKE_ARGS rebuilds only this layer) ---
# Every build: no option that downloads at configure or build time, no subprocess support in llama.cpp, no FetchContent
# download (FETCHCONTENT_FULLY_DISCONNECTED), the build number and commit for --version. These come after
# LLAMA_CMAKE_ARGS on the command line, so they always win. Options that download whenever they are set are refused,
# and so is a configure log that reports a download. llama-bench --version must report the pinned build and commit.
ARG LLAMA_CMAKE_ARGS="-DBUILD_SHARED_LIBS=OFF -DGGML_CCACHE=OFF -DGGML_OPENMP=ON -DGGML_NATIVE=OFF -DGGML_SSE42=ON -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_BMI2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF -DGGML_AVX512_VBMI=OFF -DGGML_AVX512_VNNI=OFF -DGGML_AVX512_BF16=OFF -DGGML_AVX_VNNI=OFF -DGGML_AMX_TILE=OFF -DGGML_AMX_INT8=OFF -DGGML_AMX_BF16=OFF"
# hadolint ignore=SC2086
RUN set -euf; \
    native=no; \
    for word in ${LLAMA_CMAKE_ARGS}; do \
        printf '%s\n' "${word}" | grep -Eqx -- '-D[A-Z][A-Z0-9_]{0,63}=[A-Za-z0-9._:+/$;-]{0,256}' \
            || { echo "LLAMA_CMAKE_ARGS: refusing '${word}' (only -DNAME=VALUE words)" >&2; exit 1; }; \
        case "${word}" in \
            -DLLAMA_BUILD_BORINGSSL=*|-DLLAMA_BUILD_LIBRESSL=*|-DGGML_CUDA_CCCL_VERSION=*|-DGGML_ZENDNN=*|-DGGML_VIRTGPU=*|-DLLAMA_BUILD_WEBUI=*|-DLLAMA_USE_PREBUILT_WEBUI=*) \
                echo "LLAMA_CMAKE_ARGS: ${word%%=*} downloads at configure time and is never set" >&2; exit 1 ;; \
            -DGGML_NATIVE=*) native=yes ;; \
        esac; \
    done; \
    [ "${native}" = yes ] || { echo "LLAMA_CMAKE_ARGS must set GGML_NATIVE (always explicit)" >&2; exit 1; }; \
    build_number="${LLAMA_CPP_TAG#b}"; \
    short_commit="$(printf '%.8s' "${LLAMA_CPP_COMMIT}")"; \
    log="${LLAMA_CPP_PATH}/configure.log"; \
    cmake -S "${LLAMA_CPP_PATH}" -B "${LLAMA_CPP_PATH}/build" ${LLAMA_CMAKE_ARGS} \
        -DCMAKE_BUILD_TYPE=Release -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_APP=OFF \
        -DLLAMA_BUILD_SERVER=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF -DLLAMA_LLGUIDANCE=OFF \
        -DGGML_CPU_KLEIDIAI=OFF -DGGML_OPENMP_FETCH=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_SUBPROCESS=OFF \
        -DFETCHCONTENT_FULLY_DISCONNECTED=ON \
        -DLLAMA_BUILD_NUMBER="${build_number}" -DLLAMA_BUILD_COMMIT="${short_commit}" \
        > "${log}" 2>&1 || { cat "${log}" >&2; exit 1; }; \
    cat "${log}"; \
    if grep -Ei '\b(fetching|downloading|downloading from|hf download|hf fetch|running npm|populating|fetchcontent|git clone|cloning into)\b|\bfile\(download' "${log}" >&2; then \
        echo "llama.cpp configure reported a download (lines above); the build stops" >&2; exit 1; \
    fi; \
    rm -f "${log}"; \
    cmake --build "${LLAMA_CPP_PATH}/build" -j"${BUILD_JOBS:-$(nproc)}" \
        --target llama-quantize llama-imatrix llama-completion llama-perplexity llama-bench; \
    version="$("${LLAMA_CPP_PATH}/build/bin/llama-bench" --version 2>&1)"; \
    case "${version}" in \
        *"(build ${build_number}, commit ${short_commit})"*) printf '%s\n' "${version}" ;; \
        *) echo "llama-bench --version: '${version}', expected build ${build_number}, commit ${short_commit}" >&2; exit 1 ;; \
    esac; \
    rm -rf "${LLAMA_CPP_PATH}/build/CMakeFiles"

# Of the build tree the final image needs the tools (build/bin; statically linked, BUILD_SHARED_LIBS=OFF): the static
# libraries and CMake files stay in this stage. The source tree goes along (the package's cross build, the converter).
RUN find "${LLAMA_CPP_PATH}/build" -mindepth 1 -maxdepth 1 ! -name bin -exec rm -rf {} +

# ============================================================================
# The image: what the build stages run
# ============================================================================
FROM debian:bookworm-slim

LABEL maintainer="LLM Cross-Compiler Framework"
LABEL description="GGUF model conversion and the llama.cpp cross build for 64-bit ARM boards (Raspberry Pi 4 and 5)"
LABEL target.architecture="aarch64"

ARG USER_ID=1000
ARG GROUP_ID=1000

ENV DEBIAN_FRONTEND=noninteractive
ENV BUILD_CACHE_DIR=/build-cache
ENV LLAMA_CPP_PATH=/usr/src/llama.cpp
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# cmake, make, gcc, g++, libc6-dev: the package's cross build and the config stage's compiler test (gcc and g++ also
# for a native x86_64 plan, and on arm64 as the aarch64 compilers); gcc-/g++-aarch64-linux-gnu on amd64; libgomp1:
# the OpenMP runtime of the llama.cpp tools; git: the llama.cpp source
# by hand (source_module.sh --clone-llama-cpp); gosu: privilege drop that keeps PATH.
# Sources in your own network for an offline build (orchestrator/Core/build_image.py): the person's apt mirror (it
# serves the distribution's paths, such as /debian and /debian-security) or apt cache proxy. The framework passes them
# only in offline mode, after the locality rule judged them; empty (the default) keeps the distribution's own sources.
# No credentials: a build argument shows in the image's history.
ARG APT_MIRROR
ARG APT_PROXY
RUN set -eu; \
    if [ -n "${APT_MIRROR:-}" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
            if [ -f "${f}" ]; then sed -Ei "s#https?://[^/[:space:]]+#${APT_MIRROR%/}#g" "${f}"; fi; \
        done; \
    fi; \
    if [ -n "${APT_PROXY:-}" ]; then \
        printf 'Acquire::http::Proxy "%s";\n' "${APT_PROXY}" > /etc/apt/apt.conf.d/01llmcf-proxy; \
    fi

# hadolint ignore=DL3008,SC2086
RUN set -eu; \
    cross=""; \
    if [ "$(dpkg --print-architecture)" = amd64 ]; then \
        cross="gcc-aarch64-linux-gnu g++-aarch64-linux-gnu"; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        cmake make gcc g++ libc6-dev pkg-config git ca-certificates gosu \
        python3 libgomp1 ${cross}; \
    rm -rf /var/lib/apt/lists/*

# The venv and the llama.cpp tools of the builder stage, at the paths they were made for
COPY --from=builder /usr/src/llama.cpp /usr/src/llama.cpp
COPY --from=builder /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# --- Framework modules and user ---
WORKDIR /app
COPY modules/ ./modules/
COPY scripts/ ./scripts/
RUN find modules scripts -type f \( -name '*.sh' -o -name '*.py' \) -exec chmod +x {} +

# useradd -l: no lastlog/faillog entry, whose sparse file would grow with a high USER_ID
RUN groupadd -g "${GROUP_ID}" llmbuilder \
    && useradd -l -u "${USER_ID}" -g llmbuilder -d /app -s /bin/bash llmbuilder \
    && mkdir -p "${BUILD_CACHE_DIR}" \
    && chown -R llmbuilder:llmbuilder /app "${BUILD_CACHE_DIR}"

# The stage account of the build sandbox (modules/stage_user.sh STAGE_UID/STAGE_GID): the stage modules run under it,
# with other ids than the stage runner and no capabilities, so they cannot read the runner's memory. No home, no login
# shell. setpriv (util-linux) is the switch; the build fails here when the base image lacks it.
RUN groupadd -g 64010 llmstage \
    && useradd -l -M -u 64010 -g llmstage -d /nonexistent -s /usr/sbin/nologin llmstage \
    && command -v setpriv

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR ${BUILD_CACHE_DIR}
USER llmbuilder
ENTRYPOINT ["/entrypoint.sh"]
CMD ["interactive"]
