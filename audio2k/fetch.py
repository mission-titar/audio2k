"""Orchestration : une identité, écoute entrante, serveur/callback, alimentation du slot, repartage."""
import asyncio
import json
import logging
import time
from pathlib import Path

from .ed2k import proto as P
from .ed2k.peer import Peer
from .ed2k.server import Server
from .intervals import Intervals
from .plan import Plan

log = logging.getLogger("fetch")
REASK = 29 * 60          # FILEREASKTIME d'eMule : re-contacter une source au plus toutes les 29 min
MIN_ASK = 10 * 60 + 30   # MIN_REQUESTTIME + marge : redemander plus tôt fait monter le compteur de ban
NO_SOURCE_RETRY = 60     # serveur sans source : le réinterroger (lui, pas la source) ; eMule soutient 1 demande / 20 s
CALLBACK_WAIT = 60       # une source rappelée se connecte en ~15 s ; au-delà, l'ID annoncé par le serveur est périmé


class Seeder:
    """Expose au pair distant les parts ed2k complètes et vérifiées, pour les repartager."""

    def __init__(self, plan, filename):
        self.plan, self.filename = plan, filename

    @property
    def hashset(self):
        return self.plan.hashset

    def parts(self):
        return self.plan.verified

    def read_chunks(self, a, b, chunk=10240):
        store = self.plan.store
        for p in sorted(self.plan.verified):
            lo = max(a, p * P.PARTSIZE)
            hi = min(b, (p + 1) * P.PARTSIZE, store.size)
            off = lo
            while off < hi:
                n = min(chunk, hi - off)
                yield off, store.read(off, off + n)
                off += n


def load_identity(state_dir, base_port, nick):
    """Une seule identité, userhash stable (persisté), port suivant la ligne de commande."""
    path = Path(state_dir) / "identities.json"
    ids = json.load(open(path)) if path.exists() else []
    if ids:
        d = ids[0]
        if d["tcp_port"] != base_port:
            log.warning("[%s] port TCP %d -> %d (le userhash est conservé)", d["nick"], d["tcp_port"], base_port)
            d["tcp_port"], d["udp_port"] = base_port, base_port + 10
    else:
        d = {"userhash": P.emule_userhash().hex(), "nick": nick, "tcp_port": base_port, "udp_port": base_port + 10}
        ids = [d]
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    json.dump(ids, open(path, "w"), indent=1)
    return P.Identity(bytes.fromhex(d["userhash"]), d["nick"], d["tcp_port"], d["udp_port"])


class Fetcher:
    def __init__(self, plan: Plan, filehash, size, identity, server=None, direct=(),
                 pipeline=64 * 1024, only_low=False, seed=False, name=""):
        self.plan, self.filehash, self.size = plan, filehash, size
        self.identity = identity
        self.server_addr, self.direct = server, list(direct)
        self.pipeline, self.only_low = pipeline, only_low
        self.seeder = Seeder(plan, name) if seed else None
        self.server = None
        self.peers = set()
        self.known_parts = {}               # "ip:port" du pair -> parts complètes (None = tout)
        self.claimed = Intervals()          # plages demandées à un pair, pas encore reçues
        self.next_ask = {}                  # clé source (voir source_key) -> prochain contact autorisé (monotonic)
        self.asked = {}                     # clé source -> dernière demande de slot (monotonic)
        p = plan.store.dir / "last_ask.json"
        self.last_ask = json.load(open(p)) if p.exists() else {}   # "nick:userhash" -> dernier OP_STARTUPLOADREQ
        self.stats_t0 = time.monotonic()
        self.stop = asyncio.Event()

    # --- événements pair -------------------------------------------------
    def on_block(self, peer, start, data):
        self.plan.store.write(start, data)
        if peer.inflight() < self.pipeline // 2:
            self.feed(peer)

    def on_event(self, peer, ev, *args):
        if ev == "hello":
            h = args[0]
            log.info("%s hello : %s « %s » port %d", peer.tag, h.version_str(), peer.remote.tags.get(P.CT_NAME, "?"), h.port)
            if self.server and peer.addr.split(":")[0] == self.server.host:
                return                          # test de port du serveur : on a déjà répondu au Hello
            self.maybe_ask(peer)
        elif ev == "filename":
            log.info("%s fichier : %s", peer.tag, args[0])
        elif ev == "filestatus":
            log.info("%s parts disponibles : %s", peer.tag, "toutes" if args[0] is None else sorted(args[0]))
            self.known_parts[peer.addr] = args[0]
            if self.plan.hashset is None and self.size > P.PARTSIZE:
                peer.ask_hashset()
        elif ev == "hashset":
            if self.plan.set_hashset(args[0]):
                log.info("%s hashset reçu et vérifié : %d hashes de parts", peer.tag, len(args[0]))
            else:
                log.warning("%s hashset reçu invalide (ne redonne pas le hash du fichier)", peer.tag)
        elif ev == "nofile":
            log.warning("%s ne partage pas ce fichier (rotation ?)", peer.tag)
        elif ev == "rank":
            log.info("%s en file d'attente, rang %d", peer.tag, args[0])
        elif ev == "accept":
            log.info("%s SLOT ACCORDÉ", peer.tag)
            self.feed(peer)
        elif ev == "outofparts":
            log.info("%s fin de session d'upload (OP_OUTOFPARTREQS) après %d o", peer.tag, peer.received)
            self.release()
        elif ev == "seed_start":
            log.info("%s nous demande le fichier : slot de repartage accordé", peer.tag)
        elif ev == "served":
            log.info("%s repartage : %d o envoyés", peer.tag, peer.served)
        elif ev == "closed":
            self.release()
            self.peers.discard(peer)
            extra = f", {peer.served} o repartagés" if peer.served else ""
            log.info("%s déconnecté : %d o reçus%s, %d paquets", peer.tag, peer.received, extra, peer.packets_in)
            if args[1] == "uploading" and peer.remote:      # coupure en plein slot : la source nous a oubliés
                k = self.source_key(peer)
                self.next_ask[k] = t = self.ask_allowed_at(k)
                log.info("%s slot perdu sur coupure : recontact dans %.0f s (règle des 10 min)", peer.tag, t - time.monotonic())

    def maybe_ask(self, peer):
        """Un OP_STARTUPLOADREQ par userhash de pair au plus toutes les MIN_ASK s, persisté entre deux lancements."""
        key = self.ask_key(peer)
        t = self.last_ask.get(key, 0)
        if time.time() - t >= MIN_ASK:
            peer.ask_file()
            t = self.last_ask[key] = time.time()
            json.dump(self.last_ask, open(self.plan.store.dir / "last_ask.json", "w"))
        else:
            log.info("%s demande récente (%.0f s) : on attend l'éventuel slot sans redemander", peer.tag, time.time() - t)
        self.asked[self.source_key(peer)] = time.monotonic() - (time.time() - t)

    def ask_key(self, peer):
        return f"{peer.ident.nick}:{peer.remote.userhash.hex()}"

    def source_key(self, peer):
        """Clé connue avant connexion : port annoncé si low ID (l'ID change à chaque reconnexion), sinon (ip, port)."""
        # ponytail: deux sources low ID sur le même port partagent leurs délais (prudent, jamais dangereux)
        return peer.remote.port if P.is_low_id(peer.remote.client_id) else (peer.addr.split(":")[0], peer.remote.port)

    def ask_allowed_at(self, key):
        """Instant (monotonic) où la règle des 10 min autorise une nouvelle demande à cette source."""
        return self.asked.get(key, 0) + MIN_ASK

    async def callback_answered(self, cid, timeout):
        """Vrai si la source rappelée s'est connectée dans le délai ; faux sur OP_CALLBACK_FAIL ou silence."""
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            if await self.server.callback_failed(1):
                return False
            if self.has_cid(cid):
                return True
        return False

    def release(self):
        """Recalcule les plages en vol depuis les pairs actifs (les perdues redeviennent demandables)."""
        self.claimed = Intervals()
        for p in self.peers:
            for a, b in p.pending.items():
                self.claimed.add(a, b)

    def feed(self, peer):
        if peer.state != "uploading":
            return
        budget = self.pipeline - peer.inflight()
        if budget <= 0:
            return
        avoid = None
        if peer.parts is None:                  # source complète (la lente) : lui laisser ce que les autres n'ont pas
            avoid = [(p * P.PARTSIZE, min((p + 1) * P.PARTSIZE, self.size))
                     for addr, parts in self.known_parts.items() if addr != peer.addr and parts for p in parts]
        ranges = self.plan.next_ranges(self.claimed, budget, available=peer.available(), avoid=avoid)
        if not ranges:
            if not peer.inflight():
                log.info("%s plus rien à demander à ce pair (phase %s, parts %s)", peer.tag, self.plan.phase(),
                         "toutes" if peer.parts is None else sorted(peer.parts))
                if self.plan.store.missing(self.plan.wanted()) == []:
                    self.stop.set()
            return
        for a, b in ranges:
            self.claimed.add(a, b)
        peer.request(ranges)

    # --- connexions ------------------------------------------------------
    def _mk_peer(self, reader, writer):
        srv = (self.server.ip, self.server.port) if self.server else (0, 0)
        peer = Peer(self.identity, reader, writer, self.filehash, self.size, self.on_block, self.on_event, srv, self.seeder)
        self.peers.add(peer)
        return peer

    async def listen(self):
        async def handle(reader, writer):
            peer = self._mk_peer(reader, writer)
            log.info("%s connexion entrante", peer.tag)
            await peer.run(inbound=True)
        srv = await asyncio.start_server(handle, "0.0.0.0", self.identity.tcp_port)
        log.info("[%s] écoute TCP %d", self.identity.nick, self.identity.tcp_port)
        return srv

    async def dial(self, host, port):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
        except (OSError, asyncio.TimeoutError) as e:
            log.warning("connexion %s:%d impossible : %s", host, port, e)
            return
        peer = self._mk_peer(reader, writer)
        asyncio.create_task(peer.run(inbound=False))

    def has_link_to(self, host, port):
        return any(p.addr == f"{host}:{port}" and p.state != "closed" for p in self.peers)

    def has_cid(self, cid):
        """Sommes-nous déjà connectés à cette source low ID (elle nous a rappelés) ?"""
        return any(p.remote and p.remote.client_id == cid and p.state != "closed" for p in self.peers)

    def can_help(self, host, port):
        """Une source HIGH ID connue a-t-elle des parts où il nous manque quelque chose ?"""
        parts = self.known_parts.get(f"{host}:{port}")
        if parts is None:
            return True
        avail = [(p * P.PARTSIZE, min((p + 1) * P.PARTSIZE, self.size)) for p in parts]
        return bool(self.plan.next_ranges(self.claimed, 1, available=avail))

    async def reask_loop(self):
        """Contacte la source low ID par callback serveur (elle nous rappelle), et les sources high ID en direct.
        Au plus une prise de contact par source toutes les REASK s ; jamais si déjà connecté.
        Exceptions : slot perdu sur coupure ou source revenue, dès que la règle des 10 min le permet."""
        srcs, t_srcs = None, 0
        delay = max(self.last_ask.values(), default=0) + MIN_ASK - time.time()
        if delay > 0:
            log.info("redémarrage récent : premier contact dans %.0f s (règle des 10 min)", delay)
            await asyncio.sleep(delay)
        while not self.stop.is_set():
            now = time.monotonic()
            if self.direct:
                srcs = [(0, h, p) for h, p in self.direct]
            elif srcs is None or now - t_srcs > (REASK if srcs else NO_SOURCE_RETRY):
                gone = srcs == []
                srcs = [(c, P.ip_str(c) if not P.is_low_id(c) else None, p) for c, p in await self.find_sources()]
                t_srcs = now
                if gone and srcs:                   # retour de source : seule la règle des 10 min s'impose
                    self.next_ask = {k: min(v, self.ask_allowed_at(k)) for k, v in self.next_ask.items()}
            for cid, host, port in srcs:
                key = port if host is None else (host, port)
                if now < max(self.next_ask.get(key, 0), self.ask_allowed_at(key)):
                    continue                                # contact sans demande possible = contact perdu
                if host is None:                            # source low ID : callback serveur
                    if self.has_cid(cid):
                        continue
                    self.server.callback(cid)
                    self.next_ask[key] = now + REASK
                    if not await self.callback_answered(cid, CALLBACK_WAIT):
                        log.warning("callback vers l'ID %d sans suite en %d s (ID périmé ?) : on réinterroge le serveur", cid, CALLBACK_WAIT)
                        self.next_ask[key] = time.monotonic() + NO_SOURCE_RETRY
                        srcs = None
                        break
                else:                                       # source high ID : connexion directe
                    if self.has_link_to(host, port) or not self.can_help(host, port):
                        continue
                    await self.dial(host, port)
                    self.next_ask[key] = now + REASK
                await asyncio.sleep(3)                       # espace les connexions (pas de rafale)
            await asyncio.sleep(5)

    def offer(self):
        """S'annoncer au serveur comme source du fichier, pour que d'autres nous trouvent et nous téléchargent."""
        if not (self.seeder and self.server and self.seeder.parts()):
            return
        cid = self.server.client_id if not P.is_low_id(self.server.client_id) else 0
        self.server.w.write(P.offer_files([(self.filehash, cid, self.identity.tcp_port, self.seeder.filename, self.size)]))
        log.info("annoncé au serveur comme source (%d parts vérifiées)", len(self.seeder.parts()))

    async def find_sources(self):
        srcs = await self.server.sources(self.filehash, self.size)
        if not srcs:
            log.warning("aucune source annoncée par le serveur pour ce fichier : nouvel essai dans %d s", NO_SOURCE_RETRY)
        if self.only_low:
            srcs = [s for s in srcs if P.is_low_id(s[0])]
        return srcs

    async def status_loop(self):
        while not self.stop.is_set():
            await asyncio.sleep(15)
            self.plan.store.save()
            ok, bad = self.plan.verify_parts()
            for p in ok:
                log.info("part ed2k %d complète et MD4 vérifié : repartageable", p)
            for p in bad:
                log.error("part ed2k %d complète mais MD4 FAUX : données corrompues", p)
            if ok:
                self.offer()                 # ré-annonce avec les nouvelles parts
            have = self.plan.store.have.total()
            want = sum(b - a for a, b in self.plan.wanted())
            missing = sum(b - a for a, b in self.plan.store.missing(self.plan.wanted()))
            el = time.monotonic() - self.stats_t0
            peers = "; ".join(f"{p.state}{'#' + str(p.rank) if p.rank is not None else ''}"
                              f" {p.received}o" + (f" {p.received / (time.monotonic() - p.t_accept):.0f}o/s" if p.t_accept else "")
                              + (f" +{p.served}o repartagés" if p.served else "")
                              for p in self.peers) or "aucun pair"
            log.info("phase %s | acquis %d o | reste %d/%d o | %.0f s | %s", self.plan.phase(), have, missing, want, el, peers)

    async def run(self):
        server = await self.listen()
        if not self.direct:
            self.server = Server(*self.server_addr, self.identity)
            cid = await self.server.connect()
            if P.is_low_id(cid):
                log.error("LOW ID reçu (%d) : le port %d n'est pas joignable, aucune source low ID ne pourra nous joindre",
                          cid, self.identity.tcp_port)
            self.offer()
        tasks = [asyncio.create_task(self.reask_loop()), asyncio.create_task(self.status_loop())]
        try:
            await self.stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            for p in list(self.peers):
                p.close()
            server.close()
            if self.server:
                self.server.close()
            self.plan.store.save()
