---
title: Building llama.cpp with GPU backends
lang: en
---
# Building llama.cpp with GPU backends

llama.cpp selects its compute backends at configure time with CMake options. A plain
`cmake -B build && cmake --build build --config Release -j` builds the CPU backend only.

## NVIDIA (CUDA)

Configure with `-DGGML_CUDA=ON`; the CUDA toolkit (nvcc) must be installed in the build container. By default
kernels are compiled for several GPU generations, which takes long. Limit them with
`-DCMAKE_CUDA_ARCHITECTURES="86;89"` to the generations you deploy to. Older guides use the option names
`LLAMA_CUBLAS` or `LLAMA_CUDA`; they were renamed and are ignored by current releases.

## Other GPUs

- Vulkan: `-DGGML_VULKAN=ON`, needs the Vulkan SDK headers and `glslc`.
- AMD ROCm: `-DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1100` (the target names the GPU architecture).
- Apple Metal is enabled by default on macOS.

## Portable binaries

`GGML_NATIVE` is on by default and tunes the CPU code for the machine that compiles it. For binaries that run on
other machines, and for every cross build, configure with `-DGGML_NATIVE=OFF` and choose the instruction set
explicitly.
