#!/bin/sh
# Config dans /data/config (bind mount) ; on ne l'écrase pas si elle existe déjà.
set -e
mkdir -p /data/config /data/Temp /data/Incoming
[ -f /data/config/amule.conf ] || cp /etc/amule-lab/amule.conf /data/config/amule.conf
# Sans preferences.dat, amuled appelle Save() avant de lire shareddir.dat et le vide : on crée le userhash nous-mêmes.
[ -f /data/config/preferences.dat ] || { printf '\024'; head -c16 /dev/urandom; } > /data/config/preferences.dat
[ -s /data/config/shareddir.dat ] || echo /share > /data/config/shareddir.dat
exec amuled --config-dir=/data/config --log-stdout "$@"
