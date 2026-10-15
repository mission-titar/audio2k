# audio2k : rapport de faisabilité

Objectif : récupérer la bande son française des demi-épisodes de *Keroro Mission Titar* partagés par
AnimuseeFMR (eMule v0.50a vanilla, low ID, 1 Ko/s, 2 slots, un fichier par 24 h) sans télécharger la
vidéo.

En une phrase : l'apport du PoC est l'extraction audio seule (objectif 1), la seule chose qu'aucun client
existant ne fait. Le second objectif, « s'approprier la bande passante pour aller plus vite », n'existe pas
comme levier : on ne peut pas tirer plus que le 1 Ko/s de la source, et il se réduit au repartage de fichier
partiel, c'est-à-dire au civisme eD2K ordinaire.

Sources de vérité : code eMule 0.50a (`docs/emule-0.50a-uploader.md`), format binaire du protocole
(`docs/ed2k-wire.md`, complété par la référence exécutable `audio2k/ed2k/proto.py`), banc d'essai local
(`lab/README.md`) avec un eMule 0.50a réel sous Wine et un aMule compilé depuis le fork actif amule-org
(master du 24 septembre 2026, le clone amule-project étant à l'arrêt depuis juin), tous deux limités à
1 Ko/s et partageant l'AVI exemple.

## Verdict

| Objectif | Résultat | Preuve |
|---|---|---|
| 1. Télécharger uniquement l'audio | **Possible, et c'est l'apport du PoC** | Testé contre eMule 0.50a : 17 996 plages de 188 à 1 558 octets servies à 985 à 995 o/s utiles sur un slot à 1 Ko/s |
| 2. « S'approprier la bande passante » pour accélérer | **Sans objet** | Le 1 Ko/s de la source est un plancher qu'aucune ruse ne dépasse. Accaparer les 2 slots ne l'augmente pas et prive les autres. Aucun levier d'accélération propre |
| 2 bis. Repartager ce qu'on a récupéré | **Possible, = civisme eD2K standard** | Repartage natif (`--seed`), transfert prouvé octet pour octet ; ou export `.part` + `.part.met` vers eMule/aMule. Exige des parts entières, donc incompatible avec le mode audio seul |

Nuance importante : l'accélération observée lors du test réel (9,86 Mo en quelques secondes) ne venait pas
de la source lente, mais des autres téléchargeurs qui avaient déjà les parts. C'est du multi-source
ordinaire, que fait tout client eD2K, pas un mécanisme propre au PoC.

**Test réel (24 septembre 2026, 21h27, fichier N°075A de `data.json`)** : high ID obtenu sur le port 4700,
callback vers AnimuseeFMR (eMule 0.50a, low ID) honoré en 13 s, file d'attente rang 5 puis 4. Les quatre autres
téléchargeurs (eMule 0.70b, eMule 0.50a, deux aMule 3.1a) ont ouvert un slot immédiatement et livré en 12 s
les 2 derniers Mo (index) puis tout l'audio des parts 1, 2, 3, 5, 6, 7 et 8 : 9,86 Mo, jusqu'à 560 Ko/s.
Il ne reste à obtenir de la source lente que 2,62 Mo (en-tête et audio des parts 0 et 4), soit moins d'une
heure de son grand slot ; 76 % des chunks audio sont déjà extractibles. La fin dépend de la file d'attente.

## Objectif 1 : audio seul

### Pourquoi c'est possible

- `OP_REQUESTPARTS` porte trois plages `[début, fin)` arbitraires. Côté uploader, eMule 0.50a ne vérifie
  qu'une borne haute (3 × 180 Ko par plage) et, pour un fichier partiel, que la plage est dans une part
  complète. Aucune taille minimale, aucun alignement (`UploadClient.cpp`, `CreateNextBlockPackage`).
- Chaque plage revient dans un `OP_SENDINGPART` de 30 octets d'en-tête. Le surcoût pour des chunks MP3 de
  384 ou 768 octets est de 4 à 8 %, et le planificateur fusionne deux plages séparées de moins de 30 octets
  (les chunks vidéo de 6 octets des images dupliquées) pour le réduire.
- En annonçant `DataCompVer = 0` dans le Hello, l'uploader n'essaie pas de compresser (eMule compresse les
  `.avi` par défaut, ce qui gaspillerait son CPU et ajouterait des en-têtes zlib par bloc).

### Ce qu'il faut rapatrier (AVI 1.0 de VirtualDubMod, fichier exemple de 96,4 Mo)

| Élément | Octets | Rôle |
|---|---|---|
| En-tête (64 Ko demandés, 10 Ko utiles) | 65 536 | formats des flux, position du `LIST movi` |
| Index `idx1` | 576 064 | position et taille de chaque chunk |
| Charge audio (17 996 chunks) | 11 524 992 | la piste MP3 128 kb/s |
| Fusions et en-têtes de paquets | ≈ 500 000 | surcoût protocole |
| **Total** | **≈ 12,3 Mo soit 12,7 % du fichier** | ≈ 3,5 h à 1 Ko/s |

Les en-têtes de chunks (8 octets, 144 Ko au total) ne sont pas téléchargés : ils se reconstruisent depuis
l'index. Le résultat est un flux MP3 brut lisible tel quel (`extract`).

### Ordre des demandes en présence d'autres téléchargeurs

Constat du test réel : les autres téléchargeurs (4 sources high ID) possèdent les parts 1, 2, 3, 5, 6, 7 et 8,
mais ni la part 0 (en-tête) ni la part 4. Attendre l'en-tête depuis la source lente aurait gaspillé leurs
slots. Le PoC procède donc ainsi :

1. il demande d'abord les **2 derniers Mo** du fichier à qui les a (la part finale contient `idx1`) en
   même temps que l'en-tête ;
2. dès que `idx1` est là, il retrouve la position du `LIST movi` **sans l'en-tête** : les derniers chunks du
   film sont dans ces 2 Mo, et leurs en-têtes (fourcc + taille) permettent de caler les offsets de l'index ;
3. les plages audio sont alors connues et chaque pair reçoit celles qu'il peut servir ;
4. une source **complète** (la lente) n'est sollicitée que pour ce qu'aucune source partielle connue ne
   possède : ici l'en-tête et l'audio des parts 0 et 4, soit ≈ 2,7 Mo au lieu de 12,3 Mo.

Validé au banc d'essai (aMule ne possédant que la part 9 + eMule complet) : phase audio en 15 s, part 9
servie par aMule, le reste par eMule, chacun à ≈ 1 Ko/s.

### Mesures au banc d'essai

| Uploader | Phase | Débit utile mesuré | Remarque |
|---|---|---|---|
| eMule 0.50a (Wine) | en-tête puis idx1 | 990 à 1 020 o/s | plages de 64 Ko et 552 Ko |
| eMule 0.50a (Wine) | audio, plages de 188 à 1 558 o | 985 à 995 o/s | 3 min sans erreur ni avertissement dans le log eMule |
| aMule master | audio | 990 à 1 003 o/s | identique |
| aMule master, fichier partiel (part 9 seule) | audio | 975 à 996 o/s | le client lit `OP_FILESTATUS`, ne demande que des plages de la part 9 (vérifié dans le log aMule) |

Le dernier cas compte pour le réel : les autres téléchargeurs du fichier (4 sources high ID vues sur le
serveur) servent leurs parts complètes à plein débit, et le PoC les exploite pour l'audio comme pour les
parts entières, sans passer par la source lente.

## Objectif 2 : sans objet, sinon du civisme eD2K

Le débit d'une source est un plancher qu'aucune manœuvre ne dépasse : accaparer ses deux slots ne l'augmente
pas et ne fait que priver les autres. Il n'existe donc pas de levier d'accélération. Ce qui reste d'utile est
le comportement d'un bon client : ne demander à la source lente que ce qu'elle seule possède, prendre le
reste des sources rapides, et repartager ce qu'on complète.

C'est ce que fait le PoC, avec **une seule identité** : il ne réclame à une source complète que les octets
qu'aucune source partielle connue n'a (logique `avoid`), et rapatrie le reste des autres téléchargeurs en
parallèle. Repartager exige des parts **entières** (eD2K ne partage que des parts complètes vérifiées par
MD4), donc `--want audio` ne repartage rien ; `--want all --seed` complète des parts entières et les ressert.

Repartage, deux manières, toutes deux prouvées :

- **Natif (`--seed`)** : le PoC répond aux autres clients (`OP_REQUESTFILENAME`, `OP_FILESTATUS`,
  `OP_ACCEPTUPLOADREQ`, `OP_SENDINGPART`, hashset) et s'annonce au serveur (`OP_OFFERFILES`). Il ne sert que
  des parts au MD4 vérifié. Test : deux instances séparées ont transféré tout le fichier, résultat octet pour
  octet identique à l'original.
- **Export (`export-part`)** : écrit un `.part` + `.part.met` qu'un eMule/aMule reprend et sert. Test : aMule
  charge le `.part.met` et sert la part.

Le détail du throttler d'eMule (pourquoi 1 Ko/s se scinde en un grand et un petit slot), de la règle des
3 clients par IP et des autres leviers écartés (crédits, slot collection, friend slot) est dans
`docs/emule-0.50a-uploader.md`. Le PoC n'exploite aucun d'eux : il ne tient qu'un slot et ne prolonge pas un
slot au-delà de ce qu'il a à télécharger.

## Ce qui fait bannir ou déconnecter (vérifié)

- **Changer de userhash** pour la même IP : ban « Aggressive behaviour » (reproduit au banc d'essai en
  effaçant le dossier d'état ; les identités sont désormais persistées dans `state/<x>/identities.json`).
- **Redemander le même fichier en moins de 10 min** hors slot : compteur, ban à la 4e fois. Le PoC persiste
  la date de la dernière demande par (identité, pair) et n'insiste pas.
- **Trois ports suivis pour notre IP** : toute demande ignorée en silence pendant 2 h (voir objectif 2).
- **Ne rien demander pendant 40 s** : le socket est fermé (normal, on est rappelé quand le slot s'ouvre).
- **Rester une heure sans redemander** : purge de la file. Le PoC redemande toutes les 29 min (comme eMule).
- Plage de plus de 552 960 octets, ou hors part complète chez une source partielle : « Client error »,
  retrait du slot.

## Prérequis réseau

- Le client Python doit être **high ID** : un port TCP redirigé du routeur vers la machine. Pendant le login,
  le serveur se connecte à ce port et envoie un Hello auquel le client répond (le PoC écoute avant de se
  connecter, y compris dans `probe`, qui sert donc de test « suis-je high ID ? »). Un aMule tourne
  sur l'hôte et occupe déjà 4662 : soit l'arrêter et prendre 4662, soit rediriger un autre port et le passer
  avec `--port`. Chaque identité écoute sur son propre port (`--port` puis +1, +2), tous à rediriger.
- La source low ID nous joint via un callback serveur : il faut être connecté au **même serveur** qu'elle
  (eMule Sunrise, dans `data.json`).
- L'identité principale se connecte au serveur ; les identités secondaires ouvrent une session serveur
  éphémère pour leur propre callback (repli sur la session principale si le serveur refuse).

## Mode d'emploi

```sh
# analyse locale d'un AVI (plages audio, volumes)
python -m audio2k plan fichier.avi

# sonder le serveur : login, ID, sources du fichier
python -m audio2k probe --server "$(jq -r .server data.json)" --link "$(jq -r .file data.json)" --state state/real --port 4662

# objectif 1 : récupérer la bande son au plus vite pour soi (le processus tourne des heures ; Ctrl-C sauvegarde)
python -m audio2k fetch --server "$(jq -r .server data.json)" --link "$(jq -r .file data.json)" \
    --state state/real --port 4662 --extract episode.mp3

# objectif 2 coopératif : parts entières + repartage natif (une identité, on ne se bat pas pour les slots)
python -m audio2k fetch --server "$(jq -r .server data.json)" --link "$(jq -r .file data.json)" \
    --state state/real --port 4662 --want all --seed

# repartage confié à un client tiers (alternative à --seed)
python -m audio2k export-part --link ... --state state/real --out-dir /chemin/Temp --number 900

# banc d'essai (docker compose dans lab/amule et lab/emule)
python -m audio2k fetch --link "ed2k://|file|x|96411648|57B7DDCBA926CD030A9AC7FD15B7253C|/" \
    --direct 127.0.0.1:24662 --state state/lab --port 4670
```

`--only-low` ne vise que la source lente en ignorant les sources high ID ; en coopératif on fait
l'inverse : prendre les parts communes des sources rapides et ne demander à la source lente que ce qu'elle
seule possède (comportement par défaut). `--seed` sert nos parts complètes vérifiées et nous annonce comme
source.

## Architecture du PoC (Python 3.12+, stdlib uniquement)

| Module | Rôle |
|---|---|
| `audio2k/md4.py` | MD4 pur Python (hashlib n'a plus MD4 avec OpenSSL 3), hash ed2k par parts |
| `audio2k/avi.py` | en-tête RIFF, `idx1`, plages audio, fusion des plages |
| `audio2k/plan.py` | phases en-tête → idx1 → audio (ou parts entières), hashset, vérification MD4, extraction MP3 |
| `audio2k/store.py`, `intervals.py` | fichier épars + carte des octets acquis, persistés |
| `audio2k/ed2k/proto.py` | framing, tags, Hello, login, sources, callback, `OP_REQUESTPARTS`, blocs |
| `audio2k/ed2k/peer.py` | session client↔client : côté téléchargeur, et côté semeur (`--seed`) |
| `audio2k/ed2k/server.py` | session serveur eD2K |
| `audio2k/fetch.py` | identité unique, écoute entrante, callback, alimentation du slot, redemandes, repartage |
| `audio2k/partmet.py` | export `.part` / `.part.met` |

Chaque module a un autotest : `python -m audio2k.plan`, `python audio2k/ed2k/proto.py`, etc.

## Limites connues

- Pas d'identification sécurisée (RSA) : crédits non comptabilisés par la source, sans autre effet.
- Pas de re-demande UDP : les redemandes passent par TCP et callback serveur (comme eMule le fait vers un
  low ID sans Kad).
- Le nom du fichier change à chaque rotation de 24 h : un état par hash ; le multi-fichiers se pilote en
  relançant la commande avec le lien du jour (`probe` le confirme en une seconde).
- Le `.part.met` exporté ne porte pas de hash AICH ; eMule/aMule le recalculent ou s'en passent.

## Conclusion

- **Objectif 1 (extraction audio seule) : prouvé, et c'est la seule contribution originale.** On récupère la
  piste MP3 pour environ 12 % des octets du fichier, en demandant à une source eMule 0.50a des milliers de
  petites plages qu'elle sert sans broncher. C'est ce qui permet de sauver la bande son quand la fenêtre de
  24 h ne suffit pas à tirer le fichier entier.
- **Objectif 2 (« s'approprier la bande passante ») : sans objet.** Le débit d'une source est un plancher
  qu'aucune manœuvre ne dépasse ; l'accaparement des deux slots ne l'augmente pas et ne fait que priver les
  autres. Ce qui reste d'utile n'est pas une accélération mais du repartage de fichier partiel, soit le
  comportement normal de tout bon client eD2K. Le PoC sait le faire (`--seed`, prouvé), mais cela exige des
  parts entières et sort donc du cadre « audio seul » qui est sa raison d'être.
- **La vitesse observée vient du swarm, pas d'une ruse.** Quand d'autres téléchargeurs détiennent déjà des
  parts, on les prend chez eux à plein débit : c'est du multi-source standard. La seule donnée réellement
  rare est celle que seule la source lente possède, et son débit reste le facteur limitant pour tout le monde.
