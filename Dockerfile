FROM python:3.12-slim-bookworm

ARG RAR2FS_VERSION=1.29.7
ARG UNRAR_VERSION=6.0.3
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PACKMOUNT_CONFIG_DIR=/config \
    PACKMOUNT_MOUNT_ROOT=/mnt/packmount \
    PACKMOUNT_ALLOWED_LOCAL_ROOTS=/data,/mnt,/media

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg \
    build-essential autoconf automake autoconf-archive autopoint gettext libtool pkg-config libfuse-dev fuse \
    cifs-utils nfs-common nfs-kernel-server rpcbind samba samba-common-bin smbclient \
    procps iproute2 kmod util-linux tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp/build
RUN set -eux; \
    wget -nv --tries=3 --timeout=30 -O unrar.tar.gz "https://www.rarlab.com/rar/unrarsrc-${UNRAR_VERSION}.tar.gz"; \
    tar xzf unrar.tar.gz; \
    cd unrar; \
    make -f makefile clean || true; \
    make -f makefile; \
    install -m 0755 unrar /usr/local/bin/unrar; \
    make -f makefile clean || true; \
    make -f makefile lib CXXFLAGS="-O2 -fPIC"; \
    test -f libunrar.so; \
    test -f libunrar.a; \
    install -m 0755 libunrar.so /usr/local/lib/libunrar.so; \
    ldconfig; \
    cd /tmp/build; \
    wget -nv --tries=3 --timeout=30 -O rar2fs.tar.gz "https://github.com/hasse69/rar2fs/archive/refs/tags/v${RAR2FS_VERSION}.tar.gz"; \
    mkdir -p rar2fs-src; \
    tar xzf rar2fs.tar.gz -C rar2fs-src --strip-components=1; \
    cd rar2fs-src; \
    autoreconf -fiv; \
    ./configure --with-unrar=/tmp/build/unrar --with-unrar-lib=/usr/local/lib; \
    make -j"$(nproc)"; \
    make install; \
    ldconfig; \
    rar2fs --version; \
    cd /; \
    rm -rf /tmp/build; \
    grep -q '^user_allow_other' /etc/fuse.conf || echo 'user_allow_other' >> /etc/fuse.conf

WORKDIR /app
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend /app
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && mkdir -p /config /data /mnt/packmount /run/packmount/sources /run/packmount/credentials /var/log/samba /etc/exports.d

EXPOSE 8080 139 445 111/tcp 111/udp 2049/tcp 2049/udp 20048/tcp 20048/udp
VOLUME ["/config", "/data", "/mnt/packmount"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
