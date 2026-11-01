# The framework's builder image for the runtime layer's tools (orchestrator/Core/runtime_layer.py, way "build"):
# a static busybox (the stage runner's sh and small tools) and a static util-linux setpriv (the uid switch), made from
# the pinned source tarballs the host put into this context after checking their sha256 (checked here again). The
# result is /out/busybox and /out/setpriv, which the host copies out, checks (static programs) and caches. Nothing of
# a target ever runs here. The base is pinned by digest (the same as the framework's own image).
#
# Both tools are linked against musl, not glibc: a static glibc program still loads the NSS modules that the
# computer's /etc/nsswitch.conf names (setpriv looks up every --reuid/--regid/--groups value with getpwnam/getgrnam
# first), and those modules belong to the image's or the host's own glibc. On Ubuntu ("passwd: files systemd") the
# static setpriv dlopened libnss_systemd.so.2 of glibc 2.39 into its glibc 2.41 and died with SIGFPE. musl has no
# loadable NSS modules, so nothing of the image or the host is ever loaded; the check at the end stops the build if
# a tool still names nsswitch.conf.
FROM debian:13-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc make libc6-dev linux-libc-dev musl-tools xz-utils bzip2 \
        pkg-config binutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY util-linux-2.40.4.tar.xz busybox-1.37.0.tar.bz2 libcap-ng-0.8.5.tar.gz ./
RUN echo "5c1daf733b04e9859afdc3bd87cc481180ee0f88b5c0946b16fdec931975fb79  util-linux-2.40.4.tar.xz" | sha256sum -c - \
    && echo "3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4  busybox-1.37.0.tar.bz2" | sha256sum -c - \
    && echo "3ba5294d1cbdfa98afaacfbc00b6af9ed2b83e8a21817185dfd844cc8c7ac6ff  libcap-ng-0.8.5.tar.gz" | sha256sum -c -

# musl-gcc sees only musl's own headers: the kernel's (linux/, asm/, asm-generic/, mtd/, ...) come from
# linux-libc-dev through a folder of links, never glibc's headers
RUN multiarch="$(gcc -print-multiarch)" \
    && mkdir -p /opt/kernel-headers \
    && for dir in linux asm-generic mtd rdma misc drm sound video xen; do \
        if [ -d "/usr/include/$dir" ]; then ln -s "/usr/include/$dir" "/opt/kernel-headers/$dir"; fi; \
    done \
    && ln -s "/usr/include/$multiarch/asm" /opt/kernel-headers/asm
ENV CC="musl-gcc -isystem /opt/kernel-headers"

# libcap-ng (setpriv's capability library): only its static library, for musl, into /opt/capng
RUN tar -xzf libcap-ng-0.8.5.tar.gz \
    && cd libcap-ng-0.8.5 \
    && ./configure --prefix=/opt/capng --disable-shared --enable-static --without-python3 \
    && make -C src -j"$(nproc)" \
    && make -C src install

# util-linux: only setpriv, linked statically (libcap-ng's static library). libtool takes a plain -static for its own
# libraries only, so the link of setpriv gets -all-static; the readelf check stops the build if it is still dynamic.
RUN tar -xJf util-linux-2.40.4.tar.xz \
    && cd util-linux-2.40.4 \
    && ./configure --disable-all-programs --enable-setpriv --disable-shared --enable-static --without-python \
        --without-systemd --without-udev --without-ncursesw --without-tinfo --disable-nls \
        CPPFLAGS=-I/opt/capng/include LDFLAGS="-static -L/opt/capng/lib" \
    && make -j"$(nproc)" setpriv LDFLAGS="-all-static -L/opt/capng/lib" \
    && mkdir -p /out && cp setpriv /out/setpriv && strip /out/setpriv

# busybox: its default configuration, linked statically against musl, without the applets that need the network or
# a TTY setup
RUN tar -xjf busybox-1.37.0.tar.bz2 \
    && cd busybox-1.37.0 \
    && make defconfig >/dev/null \
    && sed -i -e 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/' -e 's/^CONFIG_TC=y/# CONFIG_TC is not set/' .config \
    && make -j"$(nproc)" CC="$CC" HOSTCC=gcc busybox \
    && cp busybox /out/busybox && strip /out/busybox

# Both static (no program interpreter), neither loads NSS modules, and both run
RUN for tool in /out/setpriv /out/busybox; do \
        if readelf -l "$tool" | grep -q 'program interpreter'; then echo "$tool is not static" >&2; exit 1; fi; \
        if grep -q nsswitch.conf "$tool"; then echo "$tool would load NSS modules (glibc)" >&2; exit 1; fi; \
    done \
    && /out/setpriv --version && /out/busybox true
