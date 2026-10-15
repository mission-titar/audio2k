# Protocole eD2K : spécification binaire pour un téléchargeur minimal face à eMule 0.50a

Ce document décrit, octet par octet, ce qu'il faut émettre et accepter pour qu'un client Python
(asyncio, stdlib) se connecte à un serveur eD2K, obtienne un HighID, trouve une source LowID,
la fasse rappeler, entre dans sa file d'attente et télécharge des plages d'octets arbitraires.
Le pair à satisfaire est eMule 0.50a vanilla.

Références : `E:` = `third_party/emule-0.50a/eMule0.50a-Sources/srchybrid/`,
`A:` = `third_party/amule/src/`. Les numéros de ligne sont ceux de ces arbres.

## 0. Conventions

- Tous les entiers multi-octets sont **little-endian** (`uint16`, `uint32`, `uint64`), y compris
  les longueurs de chaînes et de paquets.
- **IP brute** : 4 octets dans l'ordre réseau (`a.b.c.d` donne `a b c d` sur le fil). Lue comme
  `uint32` LE, elle vaut `a | b<<8 | c<<16 | d<<24`. C'est le format « ED2K » des ID.
- **ID client ED2K** : `uint32` LE. `id < 0x01000000` (16777216) signifie **LowID**
  (`E:otherfunctions.h:392`). Un HighID est l'IP brute du client. Un LowID est un numéro
  attribué par le serveur.
- **ID hybride** : `ntohl(IP brute)`, c'est-à-dire `a<<24 | b<<16 | c<<8 | d`. Utilisé en interne
  par eMule et sur le fil uniquement dans l'échange de sources v3+ (section 4.18).
- **hash16** : 16 octets bruts (MD4). **Chaîne** (sauf mention contraire) : `uint16` longueur +
  octets, sans terminateur.
- `(proto, opcode)` : l'opcode seul est ambigu (ex. `0x01` = `OP_HELLO` en `0xE3` mais
  `OP_EMULEINFO` en `0xC5`). Toujours dispatcher sur le couple.

## 1. Framing

### 1.1 En-tête TCP (serveur et client)

`E:packets.cpp:31-37`, `E:packets.cpp:60-72`, `E:packets.cpp:182-189`, `E:EMSocket.cpp:345-397`.

| Offset | Taille | Type | Valeur |
|---|---|---|---|
| 0 | 1 | uint8 | protocole : `0xE3` (OP_EDONKEYPROT), `0xC5` (OP_EMULEPROT), `0xD4` (OP_PACKEDPROT) |
| 1 | 4 | uint32 | longueur = `1 + len(payload)` : **inclut l'octet d'opcode**, exclut les 5 premiers octets |
| 5 | 1 | uint8 | opcode |
| 6 | N | octets | payload (N = longueur - 1) |

Contrôles d'eMule à la réception (`E:EMSocket.cpp:373-392`) :

- protocole différent de `0xE3`, `0xC5`, `0xD4` : `ERR_WRONGHEADER`, déconnexion ;
- payload de plus de 2 000 000 octets : `ERR_TOOBIG`, déconnexion.

Plusieurs paquets peuvent se suivre dans un même segment TCP (eMule regroupe jusqu'à 15
`OP_GETSOURCES` dans une trame, `E:DownloadQueue.cpp:1553-1570`). Un lecteur doit donc
boucler : lire 6 octets, puis `longueur - 1` octets.

### 1.2 Paquets compressés `0xD4`

`E:packets.cpp:199-250`.

- L'émetteur compresse **le payload seul** avec `compress2(..., Z_BEST_COMPRESSION)` : flux zlib
  RFC 1950 complet (en-tête `78 DA`, Adler-32). L'opcode n'est pas compressé. L'en-tête devient
  `D4 | uint32(1 + len(payload_compressé)) | opcode`.
- L'émetteur ne compresse que si le résultat est plus petit (`E:packets.cpp:204`).
- Décompression Python : `zlib.decompress(payload)`.
- **Protocole après décompression** :
  - côté client : `0xC5` (`E:packets.cpp:245`). Le paquet est traité par
    `ProcessExtPacket` (`E:ListenSocket.cpp:2217-2225`, le `case` retombe dans `OP_EMULEPROT`).
    Entre clients, seuls des opcodes eMule étendus sont donc compressés
    (ex. `OP_ANSWERSOURCES2` au-delà de 354 octets, `E:KnownFile.cpp:1343-1344`).
    Taille décompressée maximale acceptée par eMule : 50 000 octets (`E:packets.h:44`).
  - côté serveur : `0xE3` (`E:ServerSocket.cpp:739-747`), maximum 250 000 octets.
- Le serveur n'envoie des paquets `0xD4` que si le login annonçait `SRVCAP_ZLIB`.

À ne pas confondre avec `OP_COMPRESSEDPART` (section 4.10), qui est un paquet `0xC5`
ordinaire dont les données de fichier sont un fragment de flux zlib.

### 1.3 En-tête UDP

`E:packets.cpp:39-44`, `E:packets.cpp:191-197`, `E:ClientUDPSocket.cpp:93-101`.

| Offset | Taille | Type | Valeur |
|---|---|---|---|
| 0 | 1 | uint8 | protocole : `0xC5` pour client-client (Kad : `0xE4`/`0xE5`) |
| 1 | 1 | uint8 | opcode |
| 2 | N | octets | payload (pas de champ longueur, la taille vient du datagramme) |

### 1.4 Obfuscation

Ignorée. Il suffit de ne jamais l'annoncer : pas de `SRVCAP_*CRYPT` au login, bits crypt à 0 dans
`CT_EMULE_MISCOPTIONS2`. eMule 0.50a a par défaut « supporté » = vrai, « demandé » = faux,
« exigé » = faux (`E:Preferences.cpp:2410-2412`), donc il parle en clair avec nous. Une source
qui **exige** l'obfuscation (bit `0x04` des options crypt, section 3.8) est injoignable : l'ignorer
(`E:BaseClient.cpp:1437`).

### 1.5 OP_MULTIPACKET et variantes

`E:ListenSocket.cpp:1074-1382`, émission `E:DownloadClient.cpp:316-401`.

Requête (téléchargeur vers uploader), protocole `0xC5` :

| Opcode | Nom | Préfixe |
|---|---|---|
| `0x92` | OP_MULTIPACKET | hash16 |
| `0xA4` | OP_MULTIPACKET_EXT | hash16, uint64 taille du fichier |
| `0xA9` | OP_MULTIPACKET_EXT2 | FileIdentifier (section 4.14) |

Puis une suite de sous-opcodes jusqu'à la fin du payload :

| Sous-opcode | Données qui suivent |
|---|---|
| `0x58` OP_REQUESTFILENAME | infos étendues (section 4.5) selon notre ExtReq |
| `0x4F` OP_SETREQFILEID | rien |
| `0x81` OP_REQUESTSOURCES | rien |
| `0x83` OP_REQUESTSOURCES2 | uint8 version, uint16 options |
| `0x9E` OP_AICHFILEHASHREQ | rien |

Tout autre sous-opcode lève une exception : **déconnexion** (`E:ListenSocket.cpp:1281-1286`).

Réponse `0xC5 0x93` OP_MULTIPACKETANSWER (préfixe hash16) ou `0xC5 0xB0`
OP_MULTIPACKETANSWER_EXT2 (préfixe FileIdentifier), suivie de :

| Sous-opcode | Données |
|---|---|
| `0x59` OP_REQFILENAMEANSWER | chaîne (uint16 + octets) |
| `0x50` OP_FILESTATUS | uint16 nb parts + bitfield (section 4.7) |
| `0x9D` OP_AICHFILEHASHANS | hash AICH 20 octets |

Fichier inconnu : `OP_FILEREQANSNOFIL` (`0xE3 0x48`, hash16). Les sources partent dans un
paquet séparé `OP_ANSWERSOURCES2`.

**Faut-il les supporter ?**

- En émission : non. Envoyer les paquets unitaires `0xE3` (section 4) et mettre à 0 le bit
  MultiPacket de MISCOPTIONS1 ainsi que les bits ExtMultiPacket et FileIdentifiers de
  MISCOPTIONS2. eMule utilise alors lui aussi le chemin unitaire (`E:DownloadClient.cpp:403-480`).
- En réception : oui, **minimalement**. Si la source télécharge elle-même ce fichier (part file
  de plus d'une part), notre `OP_REQUESTFILENAME` nous ajoute à ses sources
  (`E:ListenSocket.cpp:404-410`). Elle peut ensuite nous contacter et envoyer `OP_MULTIPACKET*`,
  `OP_REQUESTFILENAME`, `OP_SETREQFILEID` ou `OP_STARTUPLOADREQ`. Il faut extraire le hash et
  répondre `OP_FILEREQANSNOFIL` (hash16). Elle nous retire alors de ses sources
  (`E:ListenSocket.cpp:491-518`).

## 2. Tags

### 2.1 Lecture (format accepté par eMule)

`E:packets.cpp:458-574` (`CTag::CTag(CFileDataIO*, bool)`), équivalent `A:Tag.cpp:84-180`.

```
type = u8
if type & 0x80:                 # forme courte "new ed2k tags"
    type &= 0x7F ; name_id = u8
else:
    nlen = u16
    if nlen == 1: name_id = u8  # nom à 1 octet = identifiant numérique
    else:         name = bytes(nlen)
valeur selon type
```

| Type | Code | Valeur sur le fil |
|---|---|---|
| TAGTYPE_HASH | `0x01` | 16 octets |
| TAGTYPE_STRING | `0x02` | uint16 longueur + octets |
| TAGTYPE_UINT32 | `0x03` | uint32 |
| TAGTYPE_FLOAT32 | `0x04` | 4 octets IEEE-754 LE |
| TAGTYPE_BOOL | `0x05` | 1 octet (ignoré) |
| TAGTYPE_BOOLARRAY | `0x06` | uint16 n, puis `n/8 + 1` octets (division entière, ignorés) |
| TAGTYPE_BLOB | `0x07` | uint32 longueur + octets |
| TAGTYPE_UINT16 | `0x08` | uint16 (converti en entier) |
| TAGTYPE_UINT8 | `0x09` | uint8 (converti en entier) |
| TAGTYPE_BSOB | `0x0A` | **non géré** par le CTag eD2K : aucun octet consommé, désynchronisation |
| TAGTYPE_UINT64 | `0x0B` | uint64 |
| TAGTYPE_STR1..STR16 | `0x11`..`0x20` | `type - 0x10` octets bruts, sans longueur |

Un type inconnu ne consomme aucun octet (`E:packets.cpp:565-572`) : la suite du paquet est
décalée. Ne jamais émettre `0x0A` ni un type exotique.

Chaînes (`E:SafeFile.cpp:72-117`) : si elles commencent par le BOM `EF BB BF`, c'est de l'UTF-8.
Sinon, eMule tente l'UTF-8 quand le pair annonce Unicode, puis se rabat sur la page de code locale.
En Python : retirer un éventuel BOM puis `bytes.decode('utf-8', 'replace')`.

### 2.2 Écriture : ancien format (celui qu'eMule utilise pour le login et le Hello)

`E:packets.cpp:716-775` (`WriteTagToFile`).


`type(u8) | u16 1 | id(u8) | valeur` pour un tag à identifiant numérique. Entiers en
`TAGTYPE_UINT32` (4 octets), chaînes en `TAGTYPE_STRING` (u16 + octets). Le lecteur accepte aussi
la forme courte `type|0x80, id` (`E:packets.cpp:587-714`).

## 3. Points à vérifier (priorisés)

### 3.1 (a) Callback LowID

**OP_CALLBACKREQUEST** `E3 1C`, nous vers serveur (`E:BaseClient.cpp:1578-1588`) :

| Off | Taille | Type | Valeur |
|---|---|---|---|
| 0 | 4 | uint32 | ID LowID de la source, tel que reçu dans OP_FOUNDSOURCES |

eMule ne l'envoie que si la source est sur **notre** serveur (IP et port du serveur identiques,
`E:BaseClient.cpp:1512`, `E:sockets.cpp:558-564`), et que nous sommes HighID.

**OP_CALLBACKREQUESTED** `E3 35`, serveur vers la source LowID (`E:ServerSocket.cpp:546-605`) :

| Off | Taille | Type | Valeur |
|---|---|---|---|
| 0 | 4 | uint32 | notre IP brute |
| 4 | 2 | uint16 | notre port TCP (celui du login) |
| 6 | 1 | uint8 | options crypt (présent si taille ≥ 23) : 0x01 supporté, 0x02 demandé, 0x04 exigé |
| 7 | 16 | hash16 | notre userhash (présent si taille ≥ 23) |

La source cherche un client connu par IP et port (`FindClientByIP(ip, port)`), sinon en crée un,
puis `TryToConnect()` : connexion TCP directe vers nous et envoi immédiat de `OP_HELLO`
(`E:BaseClient.cpp:1531-1546`, `E:BaseClient.cpp:1644-1662`). Sans bits crypt dans notre login,
l'option crypt vaut 0 et la connexion est en clair.

**OP_CALLBACK_FAIL** `E3 36` : payload non spécifié, eMule ne fait que le journaliser
(`E:ServerSocket.cpp:606-610`, `A:ServerSocket.cpp:549-552`). À traiter comme un échec du rappel.

Délai : eMule abandonne une connexion en attente, callback compris, après **45 s**
(`E:ClientList.cpp:981`). Il ne réessaie pas avant 20 min (`E:PartFile.cpp:2719`).

**Reconnaissance de la source qui nous rappelle.** Son `OP_HELLO` contient son userhash, son ID
(le LowID que nous avons demandé, car `GetID()` renvoie l'ID serveur, `E:emule.cpp:1301-1312`),
son port, puis l'IP et le port de son serveur (`E:BaseClient.cpp:990-1140`). Critère recommandé,
dans l'ordre :

1. userhash identique à celui fourni par `OP_FOUNDSOURCES_OBFU` quand le bit 0x80 était présent
   (`E:PartFile.cpp:2854-2858`) ;
2. sinon, ID du Hello égal au LowID demandé et serveur du Hello égal à notre serveur. C'est le
   critère d'eMule pour un LowID (`E:DownloadClient.cpp:137-158`).

L'IP:port ne suffit pas pour un LowID, car le port annoncé n'est pas joignable. Chaque identité
écoute sur son propre port, et le serveur rappelle sur le port du login de l'identité qui a fait
la demande.

**Côté eMule, comment il nous reconnaît** (`E:ClientList.cpp:214-270`, `AttachToAlreadyKnown`,
`Compare` en `E:DownloadClient.cpp:131-211`) :

- la correspondance par userhash est prioritaire. Pour un HighID, la correspondance IP + port
  TCP fait aussi foi ;
- si le client trouvé est déjà connecté depuis une autre IP ou un autre port, la nouvelle
  connexion est refusée (`E:ClientList.cpp:236-251`) ;
- si un couple IP:port connu (liste de suivi, 2 h) présente un **autre userhash**, c'est un
  **ban de 2 h sur l'IP** (`E:BaseClient.cpp:654-669`, `E:ClientList.cpp:404-417`,
  `CLIENTBANTIME` `E:opcodes.h:122`).

Conséquence pour les identités multiples : couples (userhash, port TCP, port UDP) distincts et
stables. Au plus 3 clients d'une même IP dans la file d'un uploader
(`E:UploadQueue.cpp:619-637`). Un ban vise l'IP entière.

Piège connexe : dans notre Hello, l'IP et le port du serveur doivent être ceux du serveur commun.
Sinon, une source LowID dont la file dépasse 50 personnes ne nous inscrit pas, silencieusement,
sauf si notre port Kad n'est pas nul (`E:UploadQueue.cpp:537-545`).

### 3.2 (b) Rang en file

**OP_QUEUERANKING** `C5 60` (`E:UploadClient.cpp:858-871`) : envoyé seulement si nous sommes
« client eMule », c'est-à-dire si notre Hello contenait `CT_EMULE_VERSION`
(`E:BaseClient.cpp:566-577`, `E:BaseClient.cpp:710-714`).

| Off | Taille | Type | Valeur |
|---|---|---|---|
| 0 | 2 | uint16 | rang (1 = premier) |
| 2 | 10 | octets | zéros |

eMule refuse à la réception toute taille différente de 12 (`E:DownloadClient.cpp:2210-2220`).

**OP_QUEUERANK** `E3 5C` (`E:DownloadClient.cpp:2200-2208`) : `uint32` rang. Hérité d'eDonkey,
eMule 0.50a le lit mais ne l'envoie jamais.

Quand il est envoyé : après chaque `OP_STARTUPLOADREQ` qui nous laisse en file, et après
`OP_OUTOFPARTREQS` (`E:UploadQueue.cpp:576`, `E:UploadQueue.cpp:693`, `E:UploadClient.cpp:781`).
Aucun rang n'est envoyé si nous sommes refusés (file pleine, trop de clients de la même IP, ban)
ou si nous obtenons directement le slot (`OP_ACCEPTUPLOADREQ`, `E:UploadQueue.cpp:672-685`).

**Score nul, jamais de slot** (`E:UploadClient.cpp:192-240`) :

- sans tag `CT_NAME` (`m_pszUsername` nul), le score vaut 0 ;
- un pseudo contenant « EMULE-CLIENT » ou « POWERMULE » est traité comme « GPL evildoer »
  (`E:BaseClient.cpp:701-706`) ;
- en l'absence de `CT_EMULE_VERSION`, le score est divisé par deux.

### 3.3 (c) OP_OUTOFPARTREQS

`E3 57`, payload vide.

- **Uploader** (`E:UploadClient.cpp:767-782`, `E:UploadClient.cpp:722-725`) : fin de session
  (`SESSIONMAXTIME` 1 h ou score dépassé, `E:UploadQueue.cpp:803-852`). Il envoie
  `OP_OUTOFPARTREQS`, nous remet en file sans pénalité (`AddClientToQueue(this, true)`), puis
  envoie `OP_QUEUERANKING` sur la même socket.
- **Téléchargeur eMule** (`E:ListenSocket.cpp:711-721`) : si l'état est `DS_DOWNLOADING`, il passe
  en `DS_ONQUEUE`, ce qui vide les blocs en attente (`E:DownloadClient.cpp:754-781`). Il n'envoie
  rien : ni `OP_STARTUPLOADREQ`, ni annulation. La socket se ferme ensuite après 40 s
  d'inactivité. Il redemande normalement 29 min plus tard.
- À imiter : ne pas renvoyer `OP_REQUESTPARTS` ensuite. `AddReqBlock` ignore les demandes hors
  slot (`E:UploadClient.cpp:645-650`).

Pertes de slot sans remise en file, qu'il faut éviter :

- déconnexion pendant le slot (`E:BaseClient.cpp:1213-1217`) ;
- `OP_CANCELTRANSFER` `E3 56` vide (`E:ListenSocket.cpp:646-653`) ;
- bloc de plus de 552 960 octets, soit `3*EMBLOCKSIZE`, bloc non disponible dans son part file,
  ou fichier non partagé (`E:UploadClient.cpp:284-317`).

### 3.4 (d) UDP : réask

Protocole `0xC5`. Il est envoyé depuis le port UDP annoncé (`CT_EMULE_UDPPORTS`, 16 bits bas),
car l'uploader identifie l'émetteur par IP et port UDP (`E:UploadQueue.cpp:487-506`).

**OP_REASKFILEPING** `C5 90` (émission `E:DownloadClient.cpp:1545-1562`, lecture
`E:ClientUDPSocket.cpp:271-370`). Les champs dépendent de **nos** versions annoncées dans
MISCOPTIONS1 :

| Off | Taille | Type | Condition | Valeur |
|---|---|---|---|---|
| 0 | 16 | hash16 | toujours | hash du fichier |
| 16 | 2 | uint16 | UDPver > 3 et ExtReq ≥ 1 | nb parts ED2K (0 accepté : « rien ») |
| 18 | ⌈n/8⌉ | bitfield | idem | bit i de l'octet k = part 8k+i |
| … | 2 | uint16 | (UDPver > 3 et ExtReq ≥ 2) ou (UDPver = 3) | nb sources complètes |

Avec UDPver > 3, l'uploader lit via `ProcessExtendedInfo` (`E:UploadClient.cpp:397-449`).
Recommandation : UDPver = 4 et ExtReq = 2, ce qui donne hash + part status + compteur.

Réponses de l'uploader :

- **OP_REASKACK** `C5 91` : si UDPver > 3, `uint16 n` + bitfield (n = 0 : fichier complet), puis
  `uint16` rang (`E:ClientUDPSocket.cpp:327-341`) ;
- **OP_FILENOTFOUND** `C5 92` : vide, fichier non partagé ;
- **OP_QUEUEFULL** `C5 93` : vide, émetteur absent de la file et file presque pleine. Sinon,
  aucune réponse : il faut alors redemander en TCP.

Limite importante : eMule n'envoie pas de réask UDP à une source **LowID**, seulement via un
buddy Kad (`E:DownloadClient.cpp:1534-1589`). La source est derrière un pare-feu, donc un
ping direct n'arrivera généralement pas. Pour une source LowID, le réask se fait par
callback serveur puis TCP (section 3.7). Côté uploader, le ping UDP met à jour
`LastUpRequest` (`E:ClientUDPSocket.cpp:304-305`).

### 3.5 (e) Hashset

**OP_HASHSETREQUEST** `E3 51` : hash16, exactement 16 octets (`E:ListenSocket.cpp:665-675`).

**OP_HASHSETANSWER** `E3 52` (`E:FileIdentifier.cpp:257-265`) :

| Off | Taille | Type | Valeur |
|---|---|---|---|
| 0 | 16 | hash16 | hash du fichier |
| 16 | 2 | uint16 | n = `taille // PARTSIZE + 1` si taille ≥ PARTSIZE, sinon 0 |
| 18 | 16·n | hash16[] | MD4 de chaque part. Si la taille est multiple de PARTSIZE, le dernier vaut MD4("") = `31D6CFE0D16AE931B73C59D7E0C089C0` |

Vérification : `MD4(concat(parts)) == hash du fichier` (`E:FileIdentifier.cpp:151-181`). Pour un
fichier de moins de 9 728 000 octets, le hash du fichier est directement `MD4(données)`.

Dangers :

- taille ≠ 16, ou fichier absent de la liste partagée de l'uploader : exception, puis
  **déconnexion** (`E:UploadClient.cpp:826-835`, `E:ListenSocket.cpp:1043-1054`) ;
- réponse non sollicitée : exception « unwanted hashset » côté eMule (`E:DownloadClient.cpp:853`).

Variante **OP_HASHSETREQUEST2** `C5 B1` : `FileIdentifier` + uint8 options (0x01 MD4, 0x02 AICH).
Réponse `C5 B2` : `FileIdentifier`, uint8 options, puis le bloc MD4 ci-dessus sans répétition
(`E:UploadClient.cpp:796-823`, `E:FileIdentifier.cpp:267-305`). `FileIdentifier` =
`u8 desc` (bit0 MD4, bit1 taille, bit2 AICH ; bits 3-4 obligatoirement à 0), `[hash16]`,
`[u64 taille]`, `[20 octets AICH]` (`E:FileIdentifier.cpp:94-117`). Inutile si l'on n'annonce pas
FileIdentifiers : la version 1 suffit.

Python : `hashlib.new('md4')` échoue souvent sous OpenSSL 3. Il faut prévoir un MD4 en pur
Python.

### 3.6 (f) Identification sécurisée : peut-on l'ignorer ? Oui

Formats (`C5`, `E:BaseClient.cpp:2024-2249`) :

| Paquet | Payload |
|---|---|
| OP_SECIDENTSTATE `87` | u8 état (0 rien, 1 signature voulue, 2 clé + signature), u32 défi. eMule ignore toute taille ≠ 5 |
| OP_PUBLICKEY `85` | u8 len, clé (len) |
| OP_SIGNATURE `86` | u8 len, signature, [u8 type d'IP en v2] |

- Mettre les bits SecIdent (16-19) de `CT_EMULE_MISCOPTIONS1` à **0**. eMule n'envoie alors pas
  `OP_SECIDENTSTATE` (`E:BaseClient.cpp:2251-2261`).
- Même si ce paquet arrive, ne pas répondre ne provoque ni déconnexion ni pénalité. Tant que nous
  n'avons jamais envoyé de clé publique, notre état reste `IS_NOTAVAILABLE`
  (`E:ClientCredits.cpp:360-375`). Le score est calculé normalement (ratio 1,
  `E:ClientCredits.cpp:108-117`).
- Seul coût : pas de crédits d'upload, ce qui ne change rien pour un client qui n'envoie rien.
- Ne **jamais** envoyer une clé que l'on ne sait pas signer. L'état deviendrait `IS_IDNEEDED`
  puis `IS_IDFAILED`, avec un ratio forcé et de possibles conflits de hash
  (`E:ClientCredits.cpp:396-404`).
- Ne pas mettre non plus le bit PeerCache (bit 3). Sinon, eMule peut envoyer `OP_PUBLICIP_REQ`
  `C5 97` (`E:BaseClient.cpp:1679-1680`). Si ce paquet arrive, répondre `C5 98` + u32 IP brute,
  ou l'ignorer sans conséquence.

### 3.7 (g) Séquence « nous HighID, source LowID »

```
Nous (HighID, port P)          Serveur S                    Source L (LowID, eMule 0.50a)
  │ TCP connect ───────────────►│
  │ E3 01 LOGINREQUEST ────────►│
  │◄──── TCP connect (test port)│   (le serveur ferme juste après, E:BaseClient.cpp:983-985)
  │◄──── E3 01 HELLO ───────────│
  │ E3 4C HELLOANSWER ─────────►│   répondre immédiatement, sinon LowID
  │◄──── E3 38 SERVERMESSAGE ───│
  │◄──── E3 40 IDCHANGE ────────│   id ≥ 0x01000000 : HighID (id = notre IP brute)
  │◄──── E3 34 SERVERSTATUS ────│
  │ E3 19 GETSOURCES ──────────►│
  │◄──── E3 42 FOUNDSOURCES ────│   (id_L < 0x01000000, port_L)
  │ E3 1C CALLBACKREQUEST(id_L)►│
  │                             │── E3 35 CALLBACKREQUESTED(notre IP, P) ──►│
  │◄─────────────── TCP connect de L vers IP:P (≤ 45 s) ───────────────────│
  │◄─────────────── E3 01 HELLO (userhash_L, id_L, …, serveur S) ──────────│
  │ E3 4C HELLOANSWER ────────────────────────────────────────────────────►│
  │ E3 58 REQUESTFILENAME(hash [+ u16 0 + u16 0 si ExtReq=2]) ───────────►│
  │ E3 4F SETREQFILEID(hash)   [seulement si le fichier a plus d'une part] ►│
  │◄──────────── E3 59 REQFILENAMEANSWER(hash, u16 len, nom) ─────────────│
  │◄──────────── E3 50 FILESTATUS(hash, u16 n, bitfield) ─────────────────│
  │ E3 54 STARTUPLOADREQ(hash) ──────────────────────────────────────────►│
  │◄──────────── C5 60 QUEUERANKING(rang)  ou  E3 55 ACCEPTUPLOADREQ ─────│
  │   … 40 s sans trafic : L ferme la socket, nous restons dans sa file …   │
  │ toutes les 29 min (au moins 10 min et au plus 60 min d'écart) :        │
  │   CALLBACKREQUEST, puis la même séquence REQUESTFILENAME … STARTUPLOADREQ│
  │                                                                          │
  │ Notre tour : L nous connaît HighID, il se connecte directement          │
  │ (E:UploadQueue.cpp:221-226, E:BaseClient.cpp:1531-1546)                  │
  │◄─────────────── TCP connect de L vers IP:P ────────────────────────────│
  │◄─────────────── E3 01 HELLO ───────────────────────────────────────────│
  │ E3 4C HELLOANSWER ────────────────────────────────────────────────────►│
  │◄─────────────── E3 55 ACCEPTUPLOADREQ (vide) ──────────────────────────│  (E:BaseClient.cpp:1731-1743)
  │ E3 47 REQUESTPARTS(hash, 3×start, 3×end exclusif) ───────────────────►│  aucun autre paquet requis avant
  │◄─────── E3 46 SENDINGPART ×k  ou  C5 40 COMPRESSEDPART ×k ─────────────│
  │ E3 47 REQUESTPARTS (liste pendante complète, renvoyée à chaque bloc fini)►│
  │   …                                                                      │
  │◄─────── E3 57 OUTOFPARTREQS, puis C5 60 QUEUERANKING ──────────────────│  retour en file
```

Règles tirées de cette séquence :

- **Premier paquet d'une connexion entrante** : `OP_HELLO` obligatoirement, sinon déconnexion
  (`E:ListenSocket.cpp:256-260`). Nous ne faisons que répondre `OP_HELLOANSWER`, sans octet de
  taille de hash, sans jamais envoyer notre propre `OP_HELLO` sur cette socket.
- **Fréquence des réasks** : `STARTUPLOADREQ` moins de 10 min après le précédent pour le même
  fichier donne `badrequests++`. À 4, c'est un ban de l'IP (`E:UploadClient.cpp:895-914`,
  `MIN_REQUESTTIME`, `BADCLIENTBAN`). Plus d'1 h sans `STARTUPLOADREQ` ni ping UDP entraîne une
  purge de la file (`E:UploadQueue.cpp:127`, `MAX_PURGEQUEUETIME`). eMule utilise 29 min
  (`FILEREASKTIME`).
- **Six requêtes pour des hash inconnus** donnent un ban « FileReq flood »
  (`E:BaseClient.cpp:2822-2841`).
- **Slot perdu si nous sommes injoignables** : si la connexion de L vers nous échoue au moment
  du slot, nous perdons le slot (`E:BaseClient.cpp:1213-1217`). Le port d'écoute doit rester
  ouvert tant que nous sommes en file.
- **Plages demandées** : 3 blocs au maximum par `REQUESTPARTS`, chacun d'au plus 184 320 octets
  pour imiter eMule, jamais plus de 552 960. Uniquement des parts marquées dans `FILESTATUS`.
  Les champs `start = end = 0` sont ignorés (`E:ListenSocket.cpp:624-643`). Renvoyer une plage
  déjà demandée est sans effet : dédoublonnage par (start, end) (`E:UploadClient.cpp:665-678`).
- **OP_COMPRESSEDPART** : tous les paquets d'un bloc portent le même `start`, égal au début du
  bloc demandé. Le champ `u32` qui suit est la taille compressée **totale** du bloc. Il faut
  concaténer les fragments dans un `zlib.decompressobj()` propre au bloc. La position de sortie
  vaut début du bloc plus le nombre d'octets déjà décompressés (`E:UploadClient.cpp:539-598`,
  `E:DownloadClient.cpp:1181-1218`). Pour recevoir uniquement du brut, mettre DataCompVer
  (bits 20-23 de MISCOPTIONS1) à 0 (`E:UploadClient.cpp:361-364`).

### 3.8 (h) Keepalive serveur

- eMule 0.50a **n'envoie pas de keepalive par défaut** : `ServerKeepAliveTimeout` vaut 0
  (`E:Preferences.cpp:2063`).
- Si l'option est activée, eMule envoie `OP_OFFERFILES` `E3 15` avec `uint32 0` (4 octets) quand
  la connexion TCP est restée inutilisée pendant ce délai (`E:sockets.cpp:605-624`).
- Sans fichier partagé, eMule n'envoie même pas `OP_OFFERFILES` au login
  (`E:SharedFileList.cpp:863-870`).
- Le seul trafic régulier est `OP_GETSOURCES`, au plus 15 fichiers par trame, une trame toutes
  les 300 s et 15 min par fichier (`E:DownloadQueue.cpp:1485`, `E:DownloadQueue.cpp:1574`,
  `SERVERREASKTIME`).
- Les sources eMule ne disent rien du délai d'inactivité appliqué par le serveur : il n'est pas
  vérifiable ici et dépend du serveur. Recommandation : `OP_OFFERFILES(0)` après 10 à 15 min
  sans trafic. Ce paquet est inoffensif et c'est le keepalive qu'eMule utilise.
- Côté client : délai de connexion au serveur de 25 s (`CONSERVTIMEOUT`, `E:opcodes.h:113`,
  `E:sockets.cpp:433-464`). eMule ne coupe pas la socket serveur pour inactivité.

## 4. Constantes utiles

`E:opcodes.h:61-134`

| Nom | Valeur |
|---|---|
| PARTSIZE | 9 728 000 |
| EMBLOCKSIZE | 184 320 |
| CONNECTION_TIMEOUT (inactivité socket client) | 40 s, ×4 pendant un téléchargement côté eMule |
| FILEREASKTIME | 29 min |
| MIN_REQUESTTIME | 10 min |
| MAX_PURGEQUEUETIME | 1 h |
| SESSIONMAXTIME | 1 h |
| DOWNLOADTIMEOUT (sans bloc complet) | 100 s |
| EDONKEYVERSION (`CT_VERSION`) | `0x3C` |
| `CT_EMULE_VERSION` de 0.50a | `0x0000C800` : compat << 24, maj << 17, min << 10, upd << 7 |
| Ports par défaut | eMule 0.50a : aléatoires (`E:Preferences.cpp:2039-2046`). aMule : 4662 TCP et 4672 UDP (`A:Preferences.cpp:72-73`) |
