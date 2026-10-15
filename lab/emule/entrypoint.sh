#!/bin/sh
# eMule vit dans /data/emule (bind mount) : config/ et logs/ lisibles depuis l'hôte.
set -e
if [ ! -f /data/emule/emule.exe ]; then
  mkdir -p /data/emule && cp -r /opt/eMule0.50a/. /data/emule/
  mkdir -p /data/emule/Incoming /data/emule/Temp
  sed 's/$/\r/' /etc/emule-lab/preferences.ini > /data/emule/config/preferences.ini
  # shareddir.dat : UTF-16LE avec BOM, une ligne par dossier (chemin Wine).
  printf '\377\376' > /data/emule/config/shareddir.dat
  printf 'Z:\\share\\\r\n' | iconv -f UTF-8 -t UTF-16LE >> /data/emule/config/shareddir.dat
fi
if [ ! -d "$WINEPREFIX" ]; then
  wineboot -i
  wine reg add 'HKCU\Software\eMule' /v UsePublicUserDirectories /t REG_DWORD /d 2 /f
  wineserver -w
fi
cd /data/emule
# Arrêt propre sur SIGTERM (docker stop) : eMule n'écrit known.met qu'en quittant.
xvfb-run -a -s '-screen 0 1280x1024x24' wine emule.exe &
trap 'wine taskkill /IM emule.exe' TERM INT
wait $! || wait $!
