# aMule (source = contexte amule_src, voir compose.yaml), amuled + amulecmd uniquement.
FROM debian:trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git pkg-config ca-certificates netcat-openbsd \
      libboost-dev libcrypto++-dev libwxgtk3.2-dev libglib2.0-dev zlib1g-dev libreadline-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=amule_src . /src
# Debug : active __DEBUG__, requis pour les logs verbeux par catégorie (VerboseDebug).
RUN git config --global --add safe.directory /src \
 && cmake -S /src -B /build -DCMAKE_BUILD_TYPE=Debug \
      -DBUILD_MONOLITHIC=NO -DBUILD_DAEMON=YES -DBUILD_AMULECMD=YES -DBUILD_ED2K=NO \
      -DENABLE_UPNP=NO -DENABLE_NLS=NO -DENABLE_IP2COUNTRY=NO -DBUILD_TESTING=NO \
 && cmake --build /build -j"$(nproc)" && cmake --install /build && rm -rf /build
COPY amule.conf entrypoint.sh /etc/amule-lab/
ENV LANG=C.UTF-8 HOME=/data
ENTRYPOINT ["/bin/sh", "/etc/amule-lab/entrypoint.sh"]
