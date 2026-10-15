# Comportement de l'uploader eMule v0.50a (client Windows vanilla)

Étude du code source de `third_party/emule-0.50a/eMule0.50a-Sources/srchybrid/`.
Objectif : comprendre, pour un client téléchargeur interopérable, comment une
source eMule 0.50a limitée à 1 Ko/s, en LOW ID, avec 2 slots d'upload, répartit
sa bande passante et gère sa file d'attente. Chaque affirmation cite
`fichier:ligne` du code réel.

Toutes les lignes citées se réfèrent aux fichiers du dossier `srchybrid/`.
Constantes dans `opcodes.h` sauf indication.

---

## 0. Constantes et valeurs par défaut utiles

| Constante | Valeur | Lieu |
|---|---|---|
| `MIN_UP_CLIENTS_ALLOWED` | 2 | opcodes.h:111 |
| `MAX_UP_CLIENTS_ALLOWED` | 100 | opcodes.h:110 |
| `UPLOAD_CLIENT_DATARATE` | 3072 o/s | opcodes.h:109 |
| `UPLOAD_CHECK_CLIENT_DR` | 2048 | opcodes.h:108 |
| `EMBLOCKSIZE` | 184320 | opcodes.h:134 |
| `PARTSIZE` | 9728000 | opcodes.h:100 |
| `SESSIONMAXTRANS` | PARTSIZE + 20 Ko ≈ 9,28 Mo | opcodes.h:97 |
| `SESSIONMAXTIME` | 1 h | opcodes.h:98 |
| `MAX_PURGEQUEUETIME` | 1 h | opcodes.h:117 |
| `MIN_REQUESTTIME` | 10 min | opcodes.h:116 |
| `BADCLIENTBAN` | 4 | opcodes.h:115 |
| `CLIENTBANTIME` | 2 h | opcodes.h:122 |
| `CONNECTION_TIMEOUT` | 40 s | opcodes.h:62 |
| `MAXPRIORITYCOLL_SIZE` | 50 Ko | opcodes.h:128 |
| `MAXCONPER5SEC` | 20 | opcodes.h:106 |
| `PACKET_HEADER_SIZE` | 6 | EMSocket.h:29 |

Préférences par défaut (Preferences.cpp) : `TransferFullChunks = true`
(Preferences.cpp:2235), `UseCreditSystem = true` (Preferences.cpp:2286),
`QueueSize = 5000` (Preferences.cpp:2261-2263), `DontCompressAvi = false`
(Preferences.cpp:2058), `MaxConnectionsPerFiveSeconds = 20` hors Win9x
(Preferences.cpp:2088, GetDefaultMaxConperFive Preferences.cpp:2520+).

Dans le scénario étudié, l'utilisateur a fixé sa limite d'upload à 1 Ko/s, donc
`GetMaxUpload()` renvoie 1. `LastCommonRouteFinder::SetPrefs` reçoit
`pMaxUpload = 1*1024 = 1024` et `pMinUpload = 1*1024 = 1024`, d'où
`m_upload = maxUpload = 1024` (LastCommonRouteFinder.cpp:224-231, 263-264). C'est
cette valeur que le throttler lit via `GetUpload()`
(LastCommonRouteFinder.cpp:283-292).

---

## 1. Ouverture et fermeture des slots

### Combien de slots

Le nombre de slots ouverts est piloté par `CUploadQueue::ForceNewClient`
(UploadQueue.cpp:419) appelé chaque tour de `Process` (UploadQueue.cpp:327), et
par `AcceptNewClient` (UploadQueue.cpp:389).

`AcceptNewClient(curUploadSlots)` :

- renvoie `true` tant que `curUploadSlots < MIN_UP_CLIENTS_ALLOWED` (= 2)
  (UploadQueue.cpp:392-393) : les 2 premiers slots sont toujours autorisés ;
- renvoie `false` si `curUploadSlots >= MAX_UP_CLIENTS_ALLOWED` (100) ou si, à
  partir de 4 slots, un des plafonds de débit est atteint (UploadQueue.cpp:401-414).

`ForceNewClient` calcule le nombre de slots visé pour une limite fixe
(UploadQueue.cpp:457-473) :

```cpp
if (MaxSpeed > 12)      nMaxSlots = (MaxSpeed*1024) / upPerClient;
else if (MaxSpeed > 7)  nMaxSlots = MIN_UP_CLIENTS_ALLOWED + 2;   // 4
else if (MaxSpeed > 3)  nMaxSlots = MIN_UP_CLIENTS_ALLOWED + 1;   // 3
else                    nMaxSlots = MIN_UP_CLIENTS_ALLOWED;       // 2
```

`MaxSpeed = GetMaxUpload()` (UploadQueue.cpp:435-439). Ici `MaxSpeed = 1`, donc
la branche `else` s'applique : `nMaxSlots = 2`. Comme
`curUploadSlots < nMaxSlots` déclenche l'ouverture (UploadQueue.cpp:469), on
ouvre jusqu'à 2 slots et pas plus.

### Pourquoi exactement 2 slots à 1 Ko/s

`MIN_UP_CLIENTS_ALLOWED = 2` est le plancher (opcodes.h:111) et, pour
`MaxSpeed <= 3`, `nMaxSlots = 2` est aussi le plafond
(UploadQueue.cpp:465-466). Les deux se rejoignent : 2 slots, ni plus ni moins.
Le throttler peut réclamer un slot supplémentaire via
`m_iHighestNumberOfFullyActivatedSlotsSinceLastCall` (UploadQueue.cpp:475-481),
mais ce chemin n'ajoute un slot que si le throttler a jugé pouvoir alimenter plus
de slots que la file n'en contient ; à 1 Ko/s, `GetSlotLimit` renvoie 2
(voir §2), donc aucun slot en plus.

### Quand un slot s'ouvre

`Process` appelle `AddUpNextClient` dès que `ForceNewClient()` est vrai
(UploadQueue.cpp:327-330). `ForceNewClient` refuse d'ouvrir si la file d'attente
est vide (UploadQueue.cpp:420-421) ou si moins d'une seconde s'est écoulée depuis
la dernière ouverture et que le débit est faible (UploadQueue.cpp:423-424).
`AddUpNextClient` choisit le meilleur client via `FindBestClientInQueue`
(UploadQueue.cpp:192) puis l'insère avec `InsertInUploadingList`
(UploadQueue.cpp:239).

### Quand un slot se ferme

Un slot se ferme par `RemoveFromUploadQueue` (UploadQueue.cpp:715), appelé
lorsque :

- la session dépasse une limite (`CheckForTimeOver`, voir §3), via
  `SendBlockData` (UploadClient.cpp:722-725) ;
- le client envoie `OP_CANCELTRANSFER` (ListenSocket.cpp:646-652) ou
  `OP_END_OF_DOWNLOAD` (ListenSocket.cpp:654-663) ;
- le socket disparaît ou se déconnecte (`Process` UploadQueue.cpp:340-345,
  `Disconnected` BaseClient.cpp:1213-1217) ;
- une erreur de lecture de bloc survient (`CreateNextBlockPackage`
  UploadClient.cpp:376-394).

`RemoveFromUploadQueue` retire aussi le socket du throttler
(UploadQueue.cpp:730-731) et renumérote les slots restants
(UploadQueue.cpp:756-758).

---

## 2. Répartition de la bande passante entre slots (throttler)

Tout se joue dans `UploadBandwidthThrottler::RunInternal`
(UploadBandwidthThrottler.cpp:406), thread dédié qui boucle en permanence. Les
sockets en slot sont dans `m_StandardOrder_list`, dans l'ordre d'insertion.

### Budget d'octets par tour

`allowedDataRate = theApp.lastCommonRouteFinder->GetUpload()` = 1024
(UploadBandwidthThrottler.cpp:431). Le budget `bytesToSpend` s'accumule au
prorata du temps écoulé : `realBytesToSpend += allowedDataRate*timeSinceLastLoop`
puis `bytesToSpend = realBytesToSpend/1000` (UploadBandwidthThrottler.cpp:597-599).
Comme `allowedDataRate < 6*1024`, on a `minFragSize = 536` et
`doubleSendSize = 536` (UploadBandwidthThrottler.cpp:556-559).

### Les trois boucles d'envoi

1. **Paquets de contrôle** d'abord (UploadBandwidthThrottler.cpp:632-647) :
   file `m_ControlQueueFirst_list` / `m_ControlQueue_list`. Ce sont les entêtes,
   accusés de rang, etc.

2. **Boucle « trickle »** (UploadBandwidthThrottler.cpp:650-672) : pour chaque
   socket dont `GetLastCalledSend()` remonte à plus d'1 seconde, on envoie le
   strict minimum `GetNeededBytes()` (UploadBandwidthThrottler.cpp:654-659). Cela
   empêche un socket affamé de se faire déconnecter par timeout.

3. **Boucle « equal for all »** (UploadBandwidthThrottler.cpp:674-709) :
   `maxSlot = m_StandardOrder_list.GetSize()`, MAIS
   ```cpp
   if (maxSlot > 0 && allowedDataRate/maxSlot < UPLOAD_CLIENT_DATARATE)
       maxSlot = allowedDataRate/UPLOAD_CLIENT_DATARATE;
   ```
   (UploadBandwidthThrottler.cpp:675-678). Avec `allowedDataRate = 1024` et
   `UPLOAD_CLIENT_DATARATE = 3072`, `1024/3072 = 0` en entier : **`maxSlot`
   devient 0**. La boucle `for(maxCounter = 0; maxCounter < min(0,2) ...)` ne
   s'exécute jamais. La répartition « équitable » est donc totalement
   court-circuitée à 1 Ko/s.

4. **Boucle « first to last »** (UploadBandwidthThrottler.cpp:712-729) : parcourt
   `m_StandardOrder_list` de l'indice 0 vers la fin, tant que
   `bytesToSpend > 0 && spentBytes < bytesToSpend`, et donne à chaque socket
   `SendFileAndControlData(bytesToSpend - spentBytes, doubleSendSize)`.

### Qui reçoit le gros

Comme la boucle « equal » est neutralisée, c'est la boucle « first to last » qui
distribue tout. Elle sert le **socket d'indice 0 en premier** ; celui-ci absorbe
le budget disponible (quelques centaines d'octets par tour) jusqu'à ce que
`spentBytes >= bytesToSpend`. Le socket d'indice 1 n'est atteint que si le socket
0 n'a rien épuisé, c'est-à-dire s'il ne reste du budget qu'après le socket 0.

Résultat : **le socket à l'indice 0 est le « grand slot »** et reçoit la quasi
totalité du 1 Ko/s. Le socket d'indice 1, « petit slot », ne reçoit
essentiellement que les miettes de la boucle trickle (§2 boucle 2), soit
`GetNeededBytes()` une fois par seconde environ, ce qui correspond aux
0,1 à 0,2 Ko/s observés.

### Ce qui détermine l'ordre

`InsertInUploadingList` appelle
`AddToStandardList(uploadinglist.GetCount(), socket)` (UploadQueue.cpp:182), qui
insère à l'indice = nombre actuel de sockets, donc **en fin de liste**
(UploadBandwidthThrottler.cpp:133-142). Le premier client à obtenir un slot est
donc à l'indice 0 (grand slot), le second à l'indice 1 (petit slot). L'ordre suit
l'ordre chronologique d'attribution des slots. `ReSortUploadSlots`
(UploadQueue.cpp:1100) reconstruit la liste mais réinsère dans le même ordre que
`uploadinglist` (UploadQueue.cpp:1128-1140), sans re-trier par score : l'ordre
est donc préservé.

### Si le socket de tête n'a rien de bufferisé

Dans la boucle « first to last », si le socket 0 n'a rien à envoyer
(`SendFileAndControlData` renvoie 0 octet parce que ses files sont vides), la
boucle passe au socket suivant qui récupère alors le budget
(UploadBandwidthThrottler.cpp:712-721). Autrement dit **le slot en position 1
récupère le débit quand le slot 0 est inactif** (pas de `OP_REQUESTPARTS` en
attente, rien de bufferisé).

De plus, si le socket 0 se déconnecte, `RemoveFromStandardListNoLock` le retire
et **décale le socket 1 vers l'indice 0** (UploadBandwidthThrottler.cpp:179-197),
qui devient alors le grand slot.

---

## 3. Fin de session d'upload

### CheckForTimeOver

`CUploadQueue::CheckForTimeOver` (UploadQueue.cpp:803) décide de l'éjection :

- **File d'attente vide ou friend slot** → renvoie `false` immédiatement
  (UploadQueue.cpp:806-807). Une source dont la file est vide **ne libère jamais**
  ses uploads en cours : c'est explicitement pour économiser de la bande passante
  et éviter le va-et-vient upload/queue/upload (commentaire UploadQueue.cpp:804-805).
- **TransferFullChunks = false** (non défaut) : éjection si
  `GetUpStartTimeDelay() > SESSIONMAXTIME` (1 h, UploadQueue.cpp:823-827) ou si un
  autre client a un meilleur score (UploadQueue.cpp:830-841).
- **TransferFullChunks = true** (défaut) : éjection uniquement si
  `GetQueueSessionPayloadUp() > SESSIONMAXTRANS` (≈ 9,28 Mo,
  UploadQueue.cpp:844-849). **`SESSIONMAXTIME` n'est PAS vérifié dans cette
  branche** : en configuration par défaut une session n'est pas limitée dans le
  temps, seulement en volume (≈ 9,28 Mo), et seulement si la file n'est pas vide.

### Devenir du client éjecté

`SendBlockData` appelle, quand `CheckForTimeOver` est vrai :
`RemoveFromUploadQueue(this, ...)` puis `SendOutOfPartReqsAndAddToWaitingQueue`
(UploadClient.cpp:722-725).

`SendOutOfPartReqsAndAddToWaitingQueue` (UploadClient.cpp:767) :

- envoie `OP_OUTOFPARTREQS` (UploadClient.cpp:777-779) pour dire au téléchargeur
  de repasser en file ;
- appelle `AddClientToQueue(this, true)` avec `bIgnoreTimelimit = true`
  (UploadClient.cpp:781) : le client est **réinséré dans la file d'attente**, sans
  incrément du compteur de re-demandes (pas de risque de ban sur ce chemin).

Le temps d'attente est **remis à zéro** lors de cette réinsertion : le chemin
`AddClientToQueue` → `GetQueueList()->AddClient(client, true)`
(UploadQueue.cpp:691) avec `resetclient = true` appelle `SetWaitStartTime()` et
`SetAskedCount(1)` (QueueListCtrl.cpp:620-622). `SetWaitStartTime`
(UploadClient.cpp:976-981) écrit `credits->SetSecWaitStartTime(GetIP())`. Un
client éjecté repart donc en bas de la file (score fondé sur le temps d'attente
recomptant à zéro).

### Délais d'inactivité

Il n'existe pas d'éjection d'inactivité côté `CUploadQueue` : un client qui garde
son slot mais n'envoie plus de `OP_REQUESTPARTS` n'est pas retiré par
`CheckForTimeOver`. En revanche le **socket** se déconnecte par timeout :
`CEMSocket` a `m_uTimeOut = CONNECTION_TIMEOUT = 40 s` (EMSocket.cpp:89), et
`CClientReqSocket::CheckTimeOut` (ListenSocket.cpp:151) déconnecte tout socket
inactif au-delà de ce délai, vérifié dans `CListenSocket::Process` toutes les
5 s (ListenSocket.cpp:2645-2664, appelé depuis UploadQueue.cpp:1009).
`ResetTimeOutTimer` est rappelé à chaque envoi ou réception
(ListenSocket.cpp:2184, 2319, 2333, 2341, 2347). Tant que la source a des données
bufferisées pour ce socket, la boucle trickle (§2) envoie de quoi éviter le
timeout (`GetNeededBytes` EMSocket.cpp:842-888). Mais si le téléchargeur ne
demande rien et qu'il ne reste rien à envoyer, le socket meurt vers 40 s.
**Conclusion pratique : pour tenir un slot, il faut garder en permanence des
`OP_REQUESTPARTS` en attente.**

---

## 4. File d'attente

### AddClientToQueue

`CUploadQueue::AddClientToQueue` (UploadQueue.cpp:524).

- **Limite anti-callback LOW ID** (UploadQueue.cpp:537-545) : si NOUS sommes
  firewalled et connectés, un client inconnu (pas Kad, pas ami, pas sur notre
  serveur, pas en download) est refusé si la file dépasse 50 clients.
- `AddAskedCount()` et `SetLastUpRequest()` à chaque appel (UploadQueue.cpp:546-547).
- `AddRequestCount(fileid)` sauf si `bIgnoreTimelimit` (UploadQueue.cpp:548-549) :
  c'est le compteur de re-demandes trop rapprochées (voir bans plus bas).
- **Client banni** → sortie immédiate (UploadQueue.cpp:550-551).
- **Doublons / même userhash / même ip:port** via `Compare`
  (UploadQueue.cpp:580-618, `Compare` DownloadClient.cpp:131). Règle
  (UploadQueue.cpp:586-617) :
  - si le client déjà en file a un hash sécurisé valide (`IS_IDENTIFIED`), le
    nouveau est ignoré ;
  - sinon si le nouveau est `IS_IDENTIFIED`, l'ancien est retiré ;
  - sinon les deux sont retirés (on ne sait pas lequel est le pirate).
- **Limite par IP** : au plus 3 clients de même IP dans la file (`cSameIP >= 3`
  UploadQueue.cpp:625-631) ou `GetClientsFromIP(IP) >= 3` via la liste des clients
  suivis (UploadQueue.cpp:632-637, ClientList.cpp:419-425).
- **Collection** : un fichier `.emulecollection` de moins de `MAXPRIORITYCOLL_SIZE`
  (50 Ko) court-circuite la file et obtient un slot immédiat
  (UploadQueue.cpp:646-653, voir §9).
- **Plafond soft/hard de la file** (UploadQueue.cpp:660-671) :
  `softQueueLimit = QueueSize` (défaut 5000),
  `hardQueueLimit = QueueSize + max(QueueSize/4, 200)`. Au-delà du soft, seuls les
  clients au-dessus du crédit/priorité moyen (ou amis avec friend slot) entrent ;
  au-delà du hard, personne.
- File vide + `ForceNewClient(true)` → slot direct (UploadQueue.cpp:682-685) ;
  sinon `waitinglist.AddTail(client)`, état `US_ONUPLOADQUEUE`, envoi du rang
  (UploadQueue.cpp:687-694).

### GetScore

`CUpDownClient::GetScore(sysvalue, isdownloading, onlybasevalue)`
(UploadClient.cpp:192) :

- 0 si `IS_IDBADGUY` (UploadClient.cpp:206-207) ou banni/GPLEvildoer
  (UploadClient.cpp:212-213) ;
- `0x0FFFFFFF` (quasi infini) si ami avec friend slot et pas LOW ID
  (UploadClient.cpp:209-210) ;
- 0 si `sysvalue` et LOW ID non connecté (UploadClient.cpp:215-217) : un LOW ID
  non connecté ne peut donc pas être élu ;
- base = temps d'attente en secondes `(GetTickCount - GetWaitStartTime)/1000`
  (UploadClient.cpp:226) ;
- multipliée par `credits->GetScoreRatio(GetIP())` si crédits activés
  (UploadClient.cpp:236-240) ;
- multipliée par `filepriority/10` (UploadClient.cpp:241-242), la priorité du
  fichier valant 18 (très haute), 9 (haute), 7 (normale), 6 (basse), 2 (très
  basse) (GetFilePrioAsNumber UploadClient.cpp:156-186) ;
- ×0,5 pour les très vieux eMule (`m_byEmuleVersion <= 0x19`)
  (UploadClient.cpp:244-245).

`FindBestClientInQueue` (UploadQueue.cpp:111) trie par ce score et classe les
LOW ID non connectés à part : le meilleur LOW ID non connecté reçoit le flag
`m_bAddNextConnect` pour obtenir un slot dès qu'il se reconnecte
(UploadQueue.cpp:149-169).

### Purge et re-demandes

- `FindBestClientInQueue` purge tout client vu il y a plus de
  `MAX_PURGEQUEUETIME` (1 h) ou dont on ne partage plus le fichier
  (UploadQueue.cpp:127-133) ; son temps d'attente est effacé
  (`ClearWaitStartTime` UploadQueue.cpp:130). **Il faut donc redemander au moins
  une fois par heure pour rester en file.**
- Compteur « too many requests » : `AddRequestCount` (UploadClient.cpp:895)
  augmente `badrequests` si le même fichier est redemandé en moins de
  `MIN_REQUESTTIME` (10 min) et que le client n'est pas en download
  (UploadClient.cpp:900-906) ; à `badrequests == BADCLIENTBAN` (4) →
  `Ban()` (UploadClient.cpp:903-905). Un intervalle correct fait décrémenter le
  compteur (UploadClient.cpp:907-910).
- Rang envoyé via `OP_QUEUERANKING` (`SendRankingInfo` UploadClient.cpp:858-871),
  rang calculé par `GetWaitingPosition` (UploadQueue.cpp:860).

---

## 5. Requêtes de blocs

### OP_REQUESTPARTS / OP_REQUESTPARTS_I64

Traitées dans `ProcessPacket` (ListenSocket.cpp:597 pour 32 bits, 1930 pour
64 bits). Chaque paquet porte le hash (16 o) puis **3 couples début/fin**
(4 ou 8 octets chacun). Pour chaque couple avec `fin > début`, un
`Requested_Block_Struct` est créé et passé à `AddReqBlock`
(ListenSocket.cpp:624-634 et 1957-1967). Un couple `0/0` ou `fin <= début` est
ignoré silencieusement (ListenSocket.cpp:635-642).

**Aucune vérification que le hash demandé correspond au fichier déjà négocié**
(`requpfileid`) au niveau de `OP_REQUESTPARTS` : le hash du bloc est simplement
recopié (ListenSocket.cpp:631). La vérification a lieu plus tard dans
`CreateNextBlockPackage` (le fichier doit être partagé), voir plus bas.

### AddReqBlock

`CUpDownClient::AddReqBlock` (UploadClient.cpp:643) :

- rejette (et supprime) le bloc si l'état n'est pas `US_UPLOADING`
  (UploadClient.cpp:645-650) ;
- en cas de collection slot, rejette tout bloc dont le fichier n'est pas la
  collection (< 50 Ko) (UploadClient.cpp:652-663) ;
- **filtre les doublons** contre `m_DoneBlocks_list` et `m_BlockRequests_queue`
  (UploadClient.cpp:665-678) : une plage déjà envoyée ou déjà en attente est
  ignorée ;
- sinon ajoute en queue `m_BlockRequests_queue.AddTail(reqblock)`
  (UploadClient.cpp:680). **Aucune limite sur la longueur de cette file.**

### CreateNextBlockPackage

`CUpDownClient::CreateNextBlockPackage(bBigBuffer)` (UploadClient.cpp:264) lit sur
disque et met en tampon socket :

- bufferise en avance jusqu'à `nBufferLimit = 800 Ko` (gros tampon) ou **50 Ko**
  (tampon normal) (UploadClient.cpp:266-271, 280-281) ;
- **taille maximale d'une plage** : si `i64uTogo > EMBLOCKSIZE*3` (= 552 960 o)
  → exception `IDS_ERR_LARGEREQBLOCK` (UploadClient.cpp:316-317), qui provoque
  `RemoveFromUploadQueue` et déconnexion (UploadClient.cpp:376-394). **Une plage
  ne doit pas dépasser 552 960 octets.**
- **pas de taille minimale** : une plage de quelques centaines d'octets est
  acceptée sans problème ;
- **fichier partiel** : si le fichier est un part file, la plage doit être
  complète localement, sinon exception `IDS_ERR_INCOMPLETEBLOCK`
  (UploadClient.cpp:312-313) → déconnexion. Un fichier complet
  (`!IsPartFile()`) n'a pas ce contrôle (UploadClient.cpp:320-332) ;
- `SetUploadFileID(srcfile)` (UploadClient.cpp:349) : le fichier réellement
  servi est celui désigné par le hash du bloc, à condition d'être partagé
  (`GetFileByID`, sinon exception `IDS_ERR_REQ_FNF` UploadClient.cpp:284-286).

### Découpage en paquets

`CreateStandartPackets` (UploadClient.cpp:451) :

- taille de paquet ≈ 10240 octets : `nPacketSize = togo/(togo/10240)` si
  `togo > 10240`, sinon `togo` entier (UploadClient.cpp:454-457) ;
- chaque paquet `OP_SENDINGPART` a un entête applicatif de **24 octets**
  (hash 16 + début 4 + fin 4, UploadClient.cpp:520-525) ou **32 octets** pour la
  variante 64 bits `OP_SENDINGPART_I64` (UploadClient.cpp:512-517). S'y ajoute
  l'entête de paquet eD2K de **6 octets** (`PACKET_HEADER_SIZE` EMSocket.h:29).
  **Surcoût total sur le fil : 30 octets (ou 38 en 64 bits) par paquet
  `OP_SENDINGPART`.**

`CreatePackedPackets` (UploadClient.cpp:539) : compresse avec zlib niveau 9 ;
si le résultat n'est pas plus petit, retombe sur `CreateStandartPackets`
(UploadClient.cpp:543-546). Compression décidée dans `CreateNextBlockPackage`
selon l'extension : refusée pour `.zip .cbz .rar .cbr .ace .ogm`
(UploadClient.cpp:357), et pour `.avi` si `DontCompressAvi` (défaut false)
(UploadClient.cpp:358-359) ; requiert `m_byDataCompVer == 1`
(UploadClient.cpp:361).

### Demander des plages minuscules en masse

Oui, c'est possible et non sanctionné directement : `OP_REQUESTPARTS` accepte
toute plage `fin > début` (ListenSocket.cpp:626), `AddReqBlock` n'impose pas de
taille minimale ni de limite de file, et chaque plage < 10240 octets produit un
seul `OP_SENDINGPART` avec 30 octets d'entête sur le fil. Demander « quelques
centaines d'octets, des milliers de fois » gonfle massivement le surcoût
protocolaire mais reste accepté par le vanilla 0.50a. Seule contrainte : les
plages doivent être distinctes (les doublons exacts sont filtrés,
UploadClient.cpp:665-678) et complètes si c'est un part file
(UploadClient.cpp:312-313).

### Erreurs provoquant déconnexion / ban

- plage > 552 960 o, plage incomplète d'un part file, fichier non trouvé →
  exception dans `CreateNextBlockPackage` → `RemoveFromUploadQueue` + déconnexion
  (UploadClient.cpp:376-394) ;
- entête protocolaire invalide → `ERR_WRONGHEADER` (EMSocket.cpp:378-384) ;
- paquet annoncé > 2 Mo (`GlobalReadBuffer`) → `ERR_TOOBIG`
  (EMSocket.cpp:386-392) → déconnexion.

---

## 6. Machine d'état d'upload

Séquence normale côté uploader (ListenSocket.cpp `ProcessPacket`) :

1. `OP_HELLO` / `OP_HELLOANSWER` : handshake, création/attachement du client
   (ListenSocket.cpp:265-357).
2. `OP_SETREQFILEID` (0x4F) : le téléchargeur annonce le hash voulu ; l'uploader
   répond `OP_FILESTATUS` avec l'état des parts, ou `OP_FILEREQANSNOFIL` si le
   fichier n'est pas partagé (ListenSocket.cpp:429-489). Fixe le temps d'attente
   si pas encore fait (ListenSocket.cpp:437-438).
3. Optionnel `OP_REQUESTFILENAME` (0x58) : renvoie le nom et l'état étendu
   (ListenSocket.cpp:358-427), `ProcessExtendedInfo` lit le nombre de parts et le
   part status (UploadClient.cpp:397-449).
4. `OP_STARTUPLOADREQ` (0x54) : si le fichier est partagé, `AddClientToQueue`
   (ListenSocket.cpp:553-575). Sinon `CheckFailedFileIdReqs`
   (ListenSocket.cpp:572-573).
5. `OP_ACCEPTUPLOADREQ` (0x55) : envoyé par l'uploader quand le slot est accordé
   (`AddUpNextClient` UploadQueue.cpp:231-233), état `US_UPLOADING`.
6. `OP_REQUESTPARTS` → `OP_SENDINGPART` (§5).

### Vérification du part status

`ProcessExtendedInfo` (UploadClient.cpp:397) lit le nombre de parts eD2K et
l'état, vérifie seulement la cohérence du nombre de parts (UploadClient.cpp:415-420)
et stocke le part status pour l'affichage et le comptage de sources complètes.
Le code qui aurait pu **refuser** un client déclarant posséder le fichier complet
est présent mais **commenté** (UploadClient.cpp:430-432). **Un client qui déclare
avoir le fichier complet n'est donc pas refusé** : le part status ne bloque pas
l'attribution du slot.

### Retrait d'un client uploadant

Voir §3 : `Disconnected` (BaseClient.cpp:1196) sur fermeture/timeout socket,
`OP_CANCELTRANSFER`, `OP_END_OF_DOWNLOAD`, session dépassée, ou socket absent dans
`Process` (UploadQueue.cpp:340-345).

---

## 7. Spécificités d'un uploader en LOW ID

La source étant LOW ID, elle ne peut pas être contactée directement ; c'est
**elle qui se connecte au téléchargeur** pour donner le slot.

### Attribution du slot

`AddUpNextClient` : si le client élu n'a pas de socket connecté, état
`US_CONNECTING` et `TryToConnect(true)` (UploadQueue.cpp:221-226). Comme la source
est LOW ID, `TryToConnect` (BaseClient.cpp:1369) ne peut pas ouvrir de connexion
TCP sortante :

- si le téléchargeur est HIGH ID, la source fait une connexion TCP sortante
  normale vers lui (BaseClient.cpp:1531-1547) ;
- si le téléchargeur est LOW ID lui aussi, `CanDoCallback` échoue
  (BaseClient.cpp:1485) → échec ; `FindBestClientInQueue` a d'ailleurs déjà classé
  ce cas à part (UploadQueue.cpp:142-169).

### Callback par le serveur

Pour joindre un téléchargeur LOW ID (l'inverse du scénario, mais symétrique), ou
pour qu'un LOW ID joigne notre source, on passe par le serveur :

- côté demandeur, `TryToConnect` envoie `OP_CALLBACKREQUEST` au serveur local si
  le pair est sur le même serveur (BaseClient.cpp:1578-1587) ;
- côté récepteur, le serveur transmet `OP_CALLBACKREQUESTED`
  (ServerSocket.cpp:546) qui déclenche `TryToConnect` vers le demandeur
  (ServerSocket.cpp:602). Le serveur peut aussi renvoyer `OP_CALLBACK_FAIL`
  (ServerSocket.cpp:606).

Puisque la source est LOW ID, pour recevoir un slot **le téléchargeur doit être
HIGH ID** (ou joignable par direct UDP callback si supporté,
BaseClient.cpp:1550-1566) : la source se connecte alors à lui directement, sans
passer par le serveur.

### Re-demande UDP vers un LOW ID

`UDPReaskForDownload` (DownloadClient.cpp:1521) n'envoie `OP_REASKFILEPING` que si
la source est **HIGH ID** (`!HasLowID()`, DownloadClient.cpp:1534-1563). Vers une
source LOW ID, le reask UDP direct est impossible : il faut un buddy Kad
(`OP_REASKCALLBACKUDP` DownloadClient.cpp:1565-1588). **Donc `OP_REASKFILEPING`
vers un LOW ID sans buddy Kad ne marche pas** : le téléchargeur doit soit attendre
que la source le rappelle, soit refaire une connexion TCP (via callback serveur).

Côté source, `OP_REASKFILEPING` reçu en UDP est traité dans
ClientUDPSocket.cpp:271 : si l'expéditeur est déjà en file (localisé par IP:port
UDP, ClientUDPSocket.cpp:280), la source répond `OP_REASKACK` avec le rang
(ClientUDPSocket.cpp:335-341) ; sinon, si la file dépasse `QueueSize - 50`, elle
renvoie `OP_QUEUEFULL` (ClientUDPSocket.cpp:355-363), sinon ignore pour forcer une
reconnexion TCP.

### Téléchargeur injoignable au moment du slot

Si `TryToConnect` échoue (téléchargeur HIGH ID injoignable), `AddUpNextClient`
renvoie `false` (UploadQueue.cpp:224-225) et le slot n'est pas créé. Le flag
`m_bAddNextConnect` permet à un LOW ID d'obtenir un slot dès sa prochaine
connexion (UploadQueue.cpp:149-169, 561-574).

---

## 8. Identification du client

### Tags du Hello examinés

`ProcessHelloTypePacket` (BaseClient.cpp:373) lit hash, ID, port, puis les tags :
`CT_NAME`, `CT_VERSION`, `CT_PORT`, `CT_MOD_VERSION`, `CT_EMULE_UDPPORTS`,
`CT_EMULE_MISCOPTIONS1/2` (capacités : compression, sec ident, requêtes étendues,
large files, crypt layer…), `CT_EMULE_VERSION` (BaseClient.cpp:401-593).

### Rejet d'un logiciel inconnu ?

Non. Un client dont les tags eMule sont absents est simplement traité comme
non-eMule (`bIsMule = false`, BaseClient.cpp:710-717) ; il n'est pas rejeté.
L'identification sécurisée n'est **pas** requise pour obtenir un slot : elle sert
seulement à protéger les crédits (voir §9).

### Détection GPL / leechers

Marquage `m_bGPLEvildoer` si le nom d'utilisateur contient `EMULE-CLIENT` ou
`POWERMULE` (BaseClient.cpp:701-706), ou si la mod version commence par `LH`,
`LIO`, `PLUS PLUS` (`CheckForGPLEvilDoer` BaseClient.cpp:2670-2683). Un
GPLEvildoer a un `GetScore` de 0 (UploadClient.cpp:212). C'est la seule
« détection de leecher » du vanilla 0.50a ; elle ne bannit pas, elle annule le
score.

### Appels à Ban() et déclencheurs

- **Changement de userhash** pour une IP:port suivie : `Ban()`
  (BaseClient.cpp:657-661 et 663-669) ; délai de 2 h avant qu'un nouveau hash soit
  utilisable.
- **Collision userhash** avec un client connecté `IS_IDENTIFIED` :
  `tocheck->Ban()` (ClientList.cpp:244-248).
- **Flood de re-demandes** du même fichier (< 10 min, 4 fois) : `Ban()`
  (UploadClient.cpp:903-905).
- **FileReq flood** : 6 requêtes de fichiers introuvables →
  `TrackBadRequest`, et à la 2ᵉ mauvaise passe `Ban("FileReq flood")`
  (BaseClient.cpp:2822-2839).
- **QR flood** côté download (DownloadClient.cpp:2236).
- **Spammer / captcha / données corrompues** : `SetSpammer` (BaseClient.cpp:2852),
  `Ban("Identified as sender of corrupt data")` (CorruptionBlackBox.cpp:377-381).

Le ban est une entrée IP dans `m_bannedList` pour `CLIENTBANTIME` (2 h)
(`AddBannedClient` ClientList.cpp:357-358, `IsBannedClient`
ClientList.cpp:361-369). `Ban()` ferme le socket en douceur
(UploadClient.cpp:955-956).

### Limites de connexions par IP

- Sur `OnAccept`, `TooManySockets(true)` arrête l'écoute si trop de sockets
  (ListenSocket.cpp:2527-2530). `TooManySockets` (ListenSocket.cpp:2723) teste
  `GetOpenSockets() > MaxConnections`, ou `m_OpenSocketsInterval > MaxConperFive *
  modificateur` (défaut 20/5 s), ou trop de demi-connexions.
- Filtrage IP et liste de bannis vérifiés à l'`Accept`
  (ListenSocket.cpp:2493-2507 conditionnel, 2621-2636 non conditionnel).
- Suivi par IP : `GetClientsFromIP` (ClientList.cpp:419-425) alimente la limite de
  3 clients par IP en file (§4). `AddConnectingClient` /
  `m_liConnectingClients` gèrent les connexions sortantes en cours avec timeout
  45 s (ClientList.cpp:962-990).

---

## 9. Leviers pour capter la bande passante

Marqués **[hostile]** quand ils privent d'autres téléchargeurs.

1. **Tenir les deux slots avec deux identités depuis la même IP [hostile].**
   La limite est de 3 clients de même IP en file (§4, UploadQueue.cpp:625-637) ;
   deux identités (userhash + port différents) tiennent donc les 2 slots. `Compare`
   (DownloadClient.cpp:131) distingue les clients par userhash valide, ou à défaut
   par IP:port. Il faut donc des **userhash distincts et des ports distincts** pour
   ne pas être fusionné ou banni (BaseClient.cpp:657-669, ClientList.cpp:244-248).
   Résultat : les deux slots sont à vous, la source ne sert que vous. C'est le
   levier le plus efficace ici (§10).

2. **Rester en position 0 du throttler.** Le premier client servi occupe l'indice
   0 (grand slot, §2). Pour l'obtenir, être le premier à recevoir un slot ; en cas
   de déconnexion du slot 0, le slot 1 y est promu (UploadBandwidthThrottler.cpp:179-197).

3. **Garder des `OP_REQUESTPARTS` en attente en continu.** Un slot dont la file de
   blocs est vide ne consomme rien et cède son budget au socket suivant (§2). Pour
   maximiser le débit du grand slot, ne jamais laisser `m_BlockRequests_queue` se
   vider (mais rester sous 552 960 o par plage, §5).

4. **Slot collection.** Partager côté source un fichier `.emulecollection` de
   moins de 50 Ko permet un slot immédiat qui **ne périme jamais**
   (UploadQueue.cpp:646-653, `CheckForTimeOver` renvoie false tant qu'on demande
   la collection, UploadQueue.cpp:809-814). Limité au contenu de la collection
   (< 50 Ko, `AddReqBlock` rejette les autres fichiers, UploadClient.cpp:652-663).
   Utile pour un pied dans la porte instantané, pas pour un gros volume.

5. **Crédits.** `GetScoreRatio` monte jusqu'à ×10 le score selon le volume qu'on a
   déjà **uploadé vers la source** (ClientCredits.cpp:108-144). Sans historique, le
   ratio est 1,0. Pour en profiter durablement, il faut l'**identification
   sécurisée** (`IS_IDENTIFIED`), sinon `IS_IDBADGUY` annule crédits et score
   (ClientCredits.cpp:111-114, UploadClient.cpp:206-207). N'accélère que la montée
   en file, pas le débit une fois en slot.

6. **Priorité de fichier.** Le score est multiplié par la priorité du fichier
   demandé (18 pour très haute, UploadClient.cpp:166-183). C'est la source qui fixe
   cette priorité, pas le téléchargeur : levier non contrôlable côté client.

7. **Friend slot / PowerShare.** `GetScore` renvoie quasi l'infini pour un ami avec
   friend slot (UploadClient.cpp:209-210), et `CheckForTimeOver` l'exempte
   (UploadQueue.cpp:806). Mais le statut ami se règle **localement côté source** :
   inaccessible à un téléchargeur tiers. Pas de PowerShare exploitable à distance
   dans le vanilla 0.50a.

8. **Suppression par même userhash (« hostile ») [hostile].** Envoyer un OP_HELLO
   avec le userhash d'un client concurrent non identifié fait retirer les deux de
   la file (UploadQueue.cpp:605-617). Contre un client `IS_IDENTIFIED` cela vous
   fait bannir vous-même (ClientList.cpp:244-248), donc à réserver aux concurrents
   non identifiés. Technique agressive envers les autres téléchargeurs.

9. **Bizarrerie du hash de bloc.** `OP_REQUESTPARTS` ne vérifie pas que le hash
   correspond au fichier négocié (ListenSocket.cpp:624-634) ; on peut demander des
   plages de n'importe quel fichier partagé une fois le slot obtenu, tant qu'il est
   dans `sharedfiles`.

---

## 10. Exemple chiffré (1 Ko/s, 2 slots)

Lecture du throttler à `allowedDataRate = 1024`, `m_StandardOrder_list` = 2
sockets :

- `maxSlot = 1024/3072 = 0` (UploadBandwidthThrottler.cpp:675-678) : la boucle
  équitable est neutralisée.
- La boucle « first to last » sert le socket 0 en premier ; il absorbe la quasi
  totalité du budget par tour.
- Le socket 1 ne reçoit que le trickle (`GetNeededBytes` une fois par seconde,
  UploadBandwidthThrottler.cpp:650-668).

**Régime permanent estimé :**

| Slot | Débit |
|---|---|
| Slot 0 (grand) | ≈ 0,8 à 0,9 Ko/s |
| Slot 1 (petit) | ≈ 0,1 à 0,2 Ko/s |
| Total source | ≈ 1,0 Ko/s |

Ces chiffres collent aux observations (« grand slot » vs « petit slot »).

**Téléchargeur tenant les deux slots avec deux identités :** il capte les deux
lignes ci-dessus, soit **≈ 1,0 Ko/s au total**, c'est-à-dire 100 % de l'upload de
la source. La répartition grand/petit persiste entre ses deux identités, mais
comme les deux sont à lui, le débit cumulé est le plein 1 Ko/s, au détriment de
tout autre téléchargeur (au plus une place réservée à un ami/collection échappe à
ce calcul).

---

## Conclusions pour le PoC

### Règles à respecter côté téléchargeur (sinon ban ou déconnexion)

- **Être HIGH ID** (ou joignable) : une source LOW ID se connecte à nous ; un
  LOW ID↔LOW ID ne reçoit jamais de slot (BaseClient.cpp:1485,
  UploadQueue.cpp:142-169).
- **Redemander au moins une fois par heure** pour ne pas être purgé de la file
  (`MAX_PURGEQUEUETIME` UploadQueue.cpp:127-133).
- **Ne pas redemander le même fichier plus vite que toutes les 10 min** hors slot
  (sinon ban à 4 fois, UploadClient.cpp:900-905).
- **Ne pas demander de fichiers introuvables en rafale** (ban « FileReq flood » à
  6, BaseClient.cpp:2828-2839).
- **Plages ≤ 552 960 octets**, distinctes, et complètes si part file
  (UploadClient.cpp:312-317).
- **Userhash stable et unique par identité** ; ne pas réutiliser le userhash d'un
  autre (ban changement de hash / collision, BaseClient.cpp:657-669,
  ClientList.cpp:244-248).
- **Garder des `OP_REQUESTPARTS` en attente** en permanence pour ne pas céder le
  budget ni laisser le socket expirer à 40 s (UploadBandwidthThrottler.cpp:712-721,
  EMSocket.cpp:89).
- **Au plus 3 identités par IP** en file (UploadQueue.cpp:625-637).

### Leviers classés par efficacité

1. **Deux identités (userhash + port distincts, même IP) tenant les 2 slots**
   [hostile] : capte ≈ 100 % du 1 Ko/s. Levier décisif.
2. **Occuper la position 0 du throttler et l'alimenter sans cesse** : le grand
   slot vaut ≈ 0,8-0,9 Ko/s.
3. **Slot collection** (`.emulecollection` < 50 Ko partagé par la source) :
   entrée immédiate et non expirante, mais volume ≤ 50 Ko.
4. **Crédits + identification sécurisée** : accélère seulement l'accès à la file,
   pas le débit en slot ; utile sur file chargée.
5. **Suppression par userhash d'un concurrent non identifié** [hostile] : agressif,
   risqué si la cible est identifiée.
6. **Friend slot / priorité fichier** : réglés côté source, non exploitables par un
   téléchargeur tiers.

En régime par défaut (`TransferFullChunks = true`), une session ne se termine
qu'à ≈ 9,28 Mo transférés et **seulement si la file n'est pas vide**
(UploadQueue.cpp:806-807, 844-849) : si la file de la source est vide, les slots
tenus ne sont jamais libérés, ce qui rend le levier 1 encore plus efficace.
