---
title: exec format error in container builds
lang: en
---
# exec format error in container builds

A build step that fails with `exec /bin/sh: exec format error` runs a binary for another CPU architecture: an
arm64 base image on an x86_64 host, or the other way round.

Two ways out:

- **Emulation.** Register QEMU user emulation once per host with
  `docker run --privileged --rm tonistiigi/binfmt --install arm64`, then build with
  `docker buildx build --platform linux/arm64 .`. Every instruction of the build runs emulated, which is often ten
  times slower than native.
- **Cross-compilation.** Keep an x86_64 build image and install an aarch64 cross toolchain inside it
  (`crossbuild-essential-arm64` on Debian). Only the compiler output targets ARM, so the build runs at native
  speed. This is what the framework's target modules do.

`docker image inspect --format '{{.Architecture}}' <image>` shows the architecture of an image; `uname -m` shows
the host's.
