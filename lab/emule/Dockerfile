# eMule 0.50a (binaire officiel, 32 bits) sous Wine + Xvfb.
FROM debian:trixie
RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
      wine wine32:i386 xvfb xauth x11-utils curl ca-certificates unzip netcat-openbsd procps \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/emule.zip 'https://sourceforge.net/projects/emule/files/eMule/0.50a/eMule0.50a.zip/download' \
 && echo '1dfb6900a0ce6b8b9b0cc9ece35d3a9b44ade8608d589e2a352f0468e6c9feb1  /tmp/emule.zip' | sha256sum -c \
 && unzip -q /tmp/emule.zip -d /opt && rm /tmp/emule.zip && test -f /opt/eMule0.50a/emule.exe
COPY preferences.ini entrypoint.sh /etc/emule-lab/
ENV LANG=C.UTF-8 HOME=/data WINEPREFIX=/data/wineprefix WINEARCH=win32 \
    WINEDEBUG=-all WINEDLLOVERRIDES="mscoree=d;mshtml=d;winemenubuilder.exe=d"
ENTRYPOINT ["/bin/sh", "/etc/emule-lab/entrypoint.sh"]
