---
title: Cross-compiling llama.cpp for aarch64 in a container
lang: en
---
# Cross-compiling llama.cpp for aarch64 in a container

The script below runs inside an x86_64 Debian build container and produces portable aarch64 binaries of
llama.cpp, packaged with checksums. It is the pattern the target modules follow.

```bash
#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="${SRC_DIR:-/build/llama.cpp}"
OUT_DIR="${OUT_DIR:-/build/output}"
BUILD_DIR="${SRC_DIR}/build-aarch64"
ARM_ARCH="${ARM_ARCH:-armv8.2-a+dotprod}"
JOBS="$(nproc)"

echo "[build] Installing the cross toolchain"
apt-get update
apt-get install -y --no-install-recommends \
    crossbuild-essential-arm64 cmake ninja-build git ca-certificates file

echo "[build] Writing the CMake toolchain file"
cat > /build/aarch64.cmake <<'EOF'
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)
set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)
set(CMAKE_FIND_ROOT_PATH /usr/aarch64-linux-gnu)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
EOF

echo "[build] Configuring"
cmake -S "${SRC_DIR}" -B "${BUILD_DIR}" -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE=/build/aarch64.cmake \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_NATIVE=OFF \
    -DGGML_CPU_ARM_ARCH="${ARM_ARCH}" \
    -DLLAMA_CURL=OFF \
    -DBUILD_SHARED_LIBS=OFF

echo "[build] Compiling with ${JOBS} jobs"
cmake --build "${BUILD_DIR}" --config Release -j "${JOBS}" \
    --target llama-cli llama-server llama-quantize llama-bench

echo "[build] Checking the architecture of the results"
for tool in llama-cli llama-server llama-quantize llama-bench; do
    file "${BUILD_DIR}/bin/${tool}" | grep -q "ARM aarch64" || {
        echo "[build] ${tool} is not an aarch64 binary" >&2
        exit 1
    }
done

echo "[build] Stripping and packaging"
mkdir -p "${OUT_DIR}/bin"
for tool in llama-cli llama-server llama-quantize llama-bench; do
    aarch64-linux-gnu-strip -o "${OUT_DIR}/bin/${tool}" "${BUILD_DIR}/bin/${tool}"
done
git -C "${SRC_DIR}" rev-parse HEAD > "${OUT_DIR}/SOURCE_COMMIT"
(cd "${OUT_DIR}" && sha256sum bin/* SOURCE_COMMIT > SHA256SUMS)
tar -C "${OUT_DIR}" -czf "${OUT_DIR}/llama.cpp-aarch64.tar.gz" bin SOURCE_COMMIT SHA256SUMS
echo "[build] Done: ${OUT_DIR}/llama.cpp-aarch64.tar.gz"
```

`armv8.2-a+dotprod` suits the Cortex-A55 and Cortex-A76 cores of current Rockchip SoCs; use `armv8-a` for older
boards such as the Raspberry Pi 3.
