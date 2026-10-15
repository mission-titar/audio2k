# Banc d'essai eD2K : uploaders aMule et eMule

Deux uploaders locaux pour tester le client Python. Chacun partage le même fichier AVI de la racine du dépôt, monté en lecture seule sous son nom d'origine dans `/share/` (pas de copie, pas de renommage).

| Service | Dossier | TCP client | UDP client | Autre |
| --- | --- | --- | --- | --- |
| aMule (amuled) | `lab/amule/` | 14662 | 14672 | EC 14712 (mot de passe `lab`) |
| eMule 0.50a (Wine) | `lab/emule/` | 24662 | 24672 | aucun |

Les ports 4662/4672 de l'hôte restent libres pour le client Python.

## Démarrer, arrêter

```sh
cd lab/amule && docker compose up -d --build     # arrêt : docker compose down
cd lab/emule && docker compose up -d --build     # arrêt : docker compose down
docker ps --filter name=lab-                      # état + healthcheck
```

L'état persistant va dans `lab/<service>/data/` (gitignoré). Pour repartir de zéro : `docker compose down`, puis supprimer `data/`.
Les conteneurs tournent en uid 1000 : tout ce qui est dans `data/` appartient à l'utilisateur, pas à root.

## aMule

- **Version** : fork `amule-org/amule`, master épinglé au commit `7c62af3561eacd2cf768e302ec72ef3c2681870d` (2026-09-24, merge PR #1591). C'est là que se fait le développement : le code lui-même pointe vers amule-org.
- **Clone local** : `third_party/amule` suit `amule-project/amule`, dont le dernier commit est `6a1105634d81242a581a0f36a7e00379b12a83f3` (2026-06-24, « chore(deps): bump actions/cache from 5 to 6 »). Il a 1299 commits de retard sur amule-org. On peut quand même le compiler avec `AMULE_SRC=../../third_party/amule docker compose build`, testé et fonctionnel.
- **`amuled --version`** affiche `aMuleD GIT compiled with wxBase(GTK3) v3.2.8 and Boost 1.83 (Debugging) (OS: Linux)`. La chaîne ne contient pas le commit, car le contexte git de BuildKit n'embarque pas de `.git` exploitable. Le commit fait foi dans `compose.yaml`.
- **Build** : CMake uniquement, autotools a disparu du master. Options : `BUILD_MONOLITHIC=NO BUILD_DAEMON=YES BUILD_AMULECMD=YES BUILD_ED2K=NO ENABLE_UPNP=NO ENABLE_NLS=NO`, en `CMAKE_BUILD_TYPE=Debug`, obligatoire pour les logs verbeux.
- **Config** : le modèle `amule/amule.conf` est copié dans `data/config/` au premier démarrage, puis n'est plus écrasé. Pour repartir du modèle, supprimer `data/config/amule.conf`. Valeurs : MaxUpload=1 Ko/s, Port 4662, UDP 4672, pas d'autoconnexion, Kad et ED2K désactivés, UPnP off, EC sur 4712.

Commandes utiles :

```sh
docker exec lab-amule amulecmd -h 127.0.0.1 -p 4712 -P lab -c "show shared"
amulecmd -h 127.0.0.1 -p 14712 -P lab -c "show ul"     # depuis l'hôte, si amulecmd y est installé
tail -f lab/amule/data/config/logfile                   # ou : docker logs -f lab-amule
```

**Logs verbeux** : ils sont actifs par défaut. La trace montre les paquets reçus, OP_HELLO, OP_HELLOANSWER, OP_ACCEPTUPLOADREQ et les sessions d'upload. Ils sont réglés dans `data/config/amule.conf`, à modifier conteneur arrêté car amuled réécrit le fichier en quittant :

```ini
[eMule]
VerboseDebug=1
VerboseDebugLogfile=1
[Debug]
Cat_ED2k Client=1
Cat_Local Client Protocol=1
Cat_Remote Client Protocol=1
```

Les autres catégories possibles sont listées dans `src/Logger.cpp`, avec des noms comme `Cat_ClientUDP`, `Cat_Credits` ou `Cat_Packet Parsing Errors`. Pour couper les logs verbeux, mettre `VerboseDebug=0`.

## eMule 0.50a

- **Binaire** : zip officiel `https://sourceforge.net/projects/emule/files/eMule/0.50a/eMule0.50a.zip/download`, téléchargé au build et vérifié par sha256 (`1dfb6900a0ce6b8b9b0cc9ece35d3a9b44ade8608d589e2a352f0468e6c9feb1`). `emule.exe` est un PE32 i386, avec FileVersion `0.50.0 Unicode`, soit 0.50a. Le log affiche `Starting eMule v0.50a`.
- **Image** : Debian trixie, wine 10.0 (wine32:i386), Xvfb. Le conteneur lance `xvfb-run -a wine emule.exe` avec `WINEPREFIX=data/wineprefix` (win32, environ 640 Mo, créé au premier démarrage).
- **Arborescence** : eMule est copié dans `data/emule/` au premier démarrage, config et logs compris. Il est donc lisible et modifiable depuis l'hôte :
  - `data/emule/config/preferences.ini`, `shareddir.dat`, `known.met` ;
  - `data/emule/logs/eMule.log` et `data/emule/logs/eMule_Verbose.log`, en UTF-8 (`LogFileFormat=1`).
- **Préconfiguration** (`emule/preferences.ini`) : AppVersion=0.50a (sinon assistant de premier démarrage), Language=1033 (sinon boîte « langue »), MaxUpload=1, MaxDownload=1, UploadCapacityNew=10, Port 4662, UDP 4672, Autoconnect=0, NetworkKademlia=0, UPnP off, SmartIdCheck=0, MinToTray=0, Splashscreen=0, ConfirmExit=0, UpdateNotifyTestClient=0, Verbose=1, LogUlDlEvents=1, SaveLogToDisk=1, SaveDebugToDisk=1.
- **`shareddir.dat`** : UTF-16LE avec BOM, une ligne `Z:\share\` (Z: = racine Unix sous Wine).

```sh
tail -f lab/emule/data/emule/logs/eMule_Verbose.log
docker exec lab-emule sh -c 'XAUTHORITY=$(ls /tmp/xvfb-run.*/Xauthority) DISPLAY=:99 xwininfo -root -tree' | grep '"'   # fenêtres ouvertes
```

**Logs d'upload** : le binaire est une build release. Les niveaux de debug protocole comme `DebugClientTCP` sont compilés hors du binaire. Seuls les événements d'upload apparaissent dans `eMule_Verbose.log` : ajout à la file, début et fin de session. Un Hello seul n'y laisse aucune trace.

## Hash ed2k de l'AVI

`lab/hash.py` calcule le hash en Python pur (MD4 maison, vérifié sur les vecteurs RFC 1320 à chaque exécution). Il dure environ 19 s pour 96 Mo.

```sh
python3 lab/hash.py *.avi
```

```
ed2k://|file|[TV] KERORO MISSION TITAR N°062A « Les demoiselles cambrioleuses » [Dimanche 21 septembre 2008 à 16H50 sur TELETOON].avi|96411648|57B7DDCBA926CD030A9AC7FD15B7253C|/
```

| Source | Hash |
| --- | --- |
| `hash.py` | `57B7DDCBA926CD030A9AC7FD15B7253C` |
| aMule, `amulecmd show shared` (amule-org et clone local) | `57B7DDCBA926CD030A9AC7FD15B7253C` |
| eMule, `known.met` (octets 9 à 24, 10 parts) | `57B7DDCBA926CD030A9AC7FD15B7253C` |

Taille : 96411648 octets. Le fichier compte 10 parts de 9728000 octets, la dernière étant partielle. Ce n'est pas un multiple exact, donc aucune part vide n'est ajoutée.

## Vérifications faites

- Les deux conteneurs démarrent à froid, avec `data/` vide, et passent `healthy`.
- aMule liste l'AVI haché dans `show shared`, et les ports 14662 et 14712 répondent depuis l'hôte.
- eMule hache l'AVI au premier démarrage : le log indique `Hashing file` puis `eMule Version 0.50a ready`. Seule la fenêtre principale « eMule v0.50a » est ouverte, sans dialogue bloquant. Le port 24662 répond depuis l'hôte.
- Un OP_HELLO (0x01) envoyé depuis l'hôte sur 14662 et sur 24662 reçoit un OP_HELLOANSWER (0x4C) des deux uploaders.
- `docker compose stop` arrête proprement les deux services et `known.met` est écrit.

## Écueils rencontrés

1. **aMule master : CMake seulement.** `configure.ac` et `autogen.sh` n'existent plus. Il faut aussi `libglib2.0-dev`, même pour amuled seul.
2. **aMule vide `shareddir.dat` au premier démarrage.** Sans `preferences.dat`, le constructeur de CPreferences appelle `Save()` avant de lire les dossiers partagés. Il réécrit alors les trois `shareddir*.dat` vides. L'entrypoint crée donc un `preferences.dat` (octet de version et userhash aléatoire) avant de lancer amuled.
3. **Logs verbeux aMule : build Debug requise.** Les `AddDebugLogLineN` ne sont compilés qu'avec `__DEBUG__`, c'est-à-dire `CMAKE_BUILD_TYPE=Debug`. En Release, `VerboseDebug=1` ne produit rien.
4. **xvfb-run bloqué en PID 1.** Xvfb démarrait, mais wine ne se lançait jamais, faute de réception du signal USR1. Le problème disparaît avec `init: true` (tini en PID 1).
5. **eMule n'écrit `known.met` qu'en quittant.** `docker stop` tuerait wine brutalement. L'entrypoint intercepte SIGTERM et envoie `wine taskkill /IM emule.exe`, un WM_CLOSE qui donne une fermeture propre grâce à ConfirmExit=0.
6. **Premier démarrage eMule.** Il faut `AppVersion` non vide, sinon l'assistant s'ouvre, et `Language=1033`, sinon une boîte de langue apparaît. Le dossier de config est déterminé par la présence de `config/preferences.ini` à côté de l'exe, et `HKCU\Software\eMule\UsePublicUserDirectories=2` force ce mode. Gecko et Mono sont désactivés via `WINEDLLOVERRIDES`.
7. **Docker Desktop.** Les uploaders voient le client de l'hôte avec l'IP `192.168.65.1`, la passerelle de la VM, et non 127.0.0.1. Le client est vu comme LAN : `FilterBadIPs=0` pour eMule, `FilterLanIPs=0` et `ParanoidFiltering=0` pour aMule.
8. **Noms non ASCII.** `LANG=C.UTF-8` est nécessaire dans les deux conteneurs. Avec ce réglage, le nom « … » avec `°`, `«`, `»` et `à` passe intact dans aMule comme dans eMule/Wine.
