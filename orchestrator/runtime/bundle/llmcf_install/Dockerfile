# The framework's builder image for llmcf-install, the board bundle's root helper, for llmcf-gateway and for the vision
# engine llmcf-detect (orchestrator/runtime/bundle/root_helper.py, way "image": used when cargo is not on this
# computer). The context is the framework's rust/ workspace without target/; the result is /out/<crate> for the board's
# target (build arguments TARGET and CRATE), which the host copies out, checks and caches. llmcf-install and the
# gateway are static musl programs linked with the toolchain's own rust-lld (rust/.cargo/config.toml); llmcf-detect
# is an aarch64 glibc program linked by zig through cargo-zigbuild against glibc 2.31 (root_helper.GNU_PROGRAMS), with
# zig from its official tarball checked against its SHA256 and cargo-zigbuild at its exact version, the same pins as
# CI's Rust job. The base is the official Rust image pinned by digest; the toolchain file (rust/rust-toolchain.toml)
# names the same version and the targets, and Cargo.lock pins every crate (--locked).
FROM rust:1.97.0-slim-trixie@sha256:686a437ead83701e8f871e66e838c3ec55f46b5fc235b025756396ac823bdc51

ARG TARGET=aarch64-unknown-linux-musl
# The crate: llmcf-install, llmcf-gateway (its TLS, ring, compiles a little C: Debian's clang cross-compiles it) or
# llmcf-detect (zig links it against glibc 2.31)
ARG CRATE=llmcf-install
ARG ZIG_VERSION=0.13.0
ARG ZIG_SHA256_AMD64=d45312e61ebcc48032b77bc4cf7fd6915c11fa16e4aad116b66c9468211230ea
ARG ZIG_SHA256_ARM64=041ac42323837eb5624068acd8b00cd5777dac4cf91179e8dad7a7e90dd0c556
ARG CARGO_ZIGBUILD_VERSION=0.19.8
WORKDIR /src
COPY . .
RUN case "$CRATE" in llmcf-install) ;; llmcf-gateway) apt-get update \
            && apt-get install -y --no-install-recommends clang llvm && rm -rf /var/lib/apt/lists/* ;; \
        llmcf-detect) apt-get update && apt-get install -y --no-install-recommends curl xz-utils ca-certificates \
            && rm -rf /var/lib/apt/lists/* \
            && case "$(uname -m)" in x86_64) zarch=x86_64; zsha="$ZIG_SHA256_AMD64" ;; \
                aarch64) zarch=aarch64; zsha="$ZIG_SHA256_ARM64" ;; *) echo "no pinned zig for $(uname -m)" >&2; exit 1 ;; esac \
            && curl -fsSL -o /tmp/zig.tar.xz "https://ziglang.org/download/$ZIG_VERSION/zig-linux-$zarch-$ZIG_VERSION.tar.xz" \
            && echo "$zsha  /tmp/zig.tar.xz" | sha256sum -c - \
            && mkdir -p /opt/zig && tar -xJf /tmp/zig.tar.xz -C /opt/zig --strip-components=1 && rm /tmp/zig.tar.xz \
            && cargo install --locked cargo-zigbuild --version "=$CARGO_ZIGBUILD_VERSION" ;; \
        *) echo "not a crate this image builds: $CRATE" >&2; exit 1 ;; esac \
    && case "$CRATE:$TARGET" in \
        llmcf-detect:aarch64-unknown-linux-gnu) ;; \
        llmcf-detect:*) echo "llmcf-detect is built for aarch64-unknown-linux-gnu, not $TARGET" >&2; exit 1 ;; \
        *:x86_64-unknown-linux-musl|*:aarch64-unknown-linux-musl|*:armv7-unknown-linux-musleabihf) ;; \
        *) echo "not a target llmcf-install is built for: $TARGET" >&2; exit 1 ;; esac \
    && rustup target add "$TARGET" \
    && t="$(echo "$TARGET" | tr - _)" \
    && if [ "$CRATE" = llmcf-gateway ]; then export "CC_$t=clang" "CFLAGS_$t=--target=$TARGET" "AR_$t=llvm-ar"; fi \
    && if [ "$CRATE" = llmcf-detect ]; then PATH="/opt/zig:$PATH" cargo zigbuild --release --locked -p "$CRATE" \
            --target "$TARGET.2.31"; else cargo build --release --locked -p "$CRATE" --target "$TARGET"; fi \
    && mkdir -p /out \
    && cp "target/$TARGET/release/$CRATE" "/out/$CRATE" \
    && rm -rf target
