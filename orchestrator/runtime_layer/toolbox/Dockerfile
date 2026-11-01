# The framework's builder image for the runtime layer's tools (orchestrator/Core/runtime_layer.py, way "build"):
# a static busybox (the stage runner's sh and small tools) and a static util-linux setpriv (the uid switch), made from
# the pinned source tarballs the host put into this context after checking their sha256 (checked here again). The
# result is /out/busybox and /out/setpriv, which the host copies out, checks (static programs) and caches. Nothing of
# a target ever runs here. The base is pinned by digest (the same as the framework's own image).
FROM debian:13-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc make libc6-dev xz-utils bzip2 pkg-config libcap-ng-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY util-linux-2.40.4.tar.xz busybox-1.37.0.tar.bz2 ./
RUN echo "5c1daf733b04e9859afdc3bd87cc481180ee0f88b5c0946b16fdec931975fb79  util-linux-2.40.4.tar.xz" | sha256sum -c - \
    && echo "3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4  busybox-1.37.0.tar.bz2" | sha256sum -c -

# util-linux: only setpriv, linked statically (libcap-ng's static library). libtool takes a plain -static for its own
# libraries only, so the link of setpriv gets -all-static; the readelf check stops the build if it is still dynamic.
RUN tar -xJf util-linux-2.40.4.tar.xz \
    && cd util-linux-2.40.4 \
    && ./configure --disable-all-programs --enable-setpriv --disable-shared --enable-static --without-python \
        --without-systemd --without-udev --without-ncursesw --without-tinfo --disable-nls LDFLAGS=-static \
    && make -j"$(nproc)" setpriv LDFLAGS=-all-static \
    && mkdir -p /out && cp setpriv /out/setpriv && strip /out/setpriv \
    && if readelf -l /out/setpriv | grep -q 'program interpreter'; then echo 'setpriv is not static' >&2; exit 1; fi

# busybox: its default configuration, linked statically, without the applets that need the network or a TTY setup
RUN tar -xjf busybox-1.37.0.tar.bz2 \
    && cd busybox-1.37.0 \
    && make defconfig >/dev/null \
    && sed -i -e 's/^# CONFIG_STATIC is not set/CONFIG_STATIC=y/' -e 's/^CONFIG_TC=y/# CONFIG_TC is not set/' .config \
    && make -j"$(nproc)" busybox \
    && cp busybox /out/busybox && strip /out/busybox

RUN /out/setpriv --version && /out/busybox true
