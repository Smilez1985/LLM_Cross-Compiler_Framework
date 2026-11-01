# The framework's builder image for llmcf-install, the board bundle's root helper, and for llmcf-gateway (orchestrator/runtime/bundle/
# root_helper.py, way "image": used when cargo is not on this computer). The context is the framework's rust/
# workspace without target/; the result is /out/llmcf-install for the board's target (build argument TARGET), a static
# musl program linked with the toolchain's own rust-lld (rust/.cargo/config.toml), which the host copies out, checks
# (a static program of the board's machine) and caches. The base is the official Rust image pinned by digest; the
# toolchain file (rust/rust-toolchain.toml) names the same version and the three musl targets, and Cargo.lock pins
# every crate (--locked).
FROM rust:1.97.0-slim-trixie@sha256:686a437ead83701e8f871e66e838c3ec55f46b5fc235b025756396ac823bdc51

ARG TARGET=aarch64-unknown-linux-musl
# The crate: llmcf-install, or llmcf-gateway (its TLS, ring, compiles a little C: Debian's clang cross-compiles it)
ARG CRATE=llmcf-install
WORKDIR /src
COPY . .
RUN case "$CRATE" in llmcf-install) ;; llmcf-gateway) apt-get update \
            && apt-get install -y --no-install-recommends clang llvm && rm -rf /var/lib/apt/lists/* ;; \
        *) echo "not a crate this image builds: $CRATE" >&2; exit 1 ;; esac \
    && case "$TARGET" in x86_64-unknown-linux-musl|aarch64-unknown-linux-musl|armv7-unknown-linux-musleabihf) ;; \
        *) echo "not a target llmcf-install is built for: $TARGET" >&2; exit 1 ;; esac \
    && rustup target add "$TARGET" \
    && t="$(echo "$TARGET" | tr - _)" \
    && if [ "$CRATE" = llmcf-gateway ]; then export "CC_$t=clang" "CFLAGS_$t=--target=$TARGET" "AR_$t=llvm-ar"; fi \
    && cargo build --release --locked -p "$CRATE" --target "$TARGET" \
    && mkdir -p /out \
    && cp "target/$TARGET/release/$CRATE" "/out/$CRATE" \
    && rm -rf target
