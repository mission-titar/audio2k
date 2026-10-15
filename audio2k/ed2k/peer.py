"""Session TCP client↔client jouée avec une identité, côté téléchargeur."""
import asyncio
import logging
import time
import zlib

from . import proto as P

log = logging.getLogger("peer")


class Peer:
    def __init__(self, ident, reader, writer, filehash, size, on_block=None, on_event=None, server=(0, 0), seeder=None):
        self.ident, self.r, self.w = ident, reader, writer
        self.filehash, self.size = filehash, size
        self.on_block = on_block or (lambda peer, start, data: None)
        self.on_event = on_event or (lambda peer, ev, *a: None)
        self.server = server
        self.seeder = seeder               # None, ou objet qui expose nos parts vérifiées à repartager
        self.served = 0                    # octets envoyés à ce pair (repartage)
        self.remote = None                 # proto.Hello du pair
        self.state = "hello"               # hello, connected, queued, uploading, closed
        self.rank = None
        self.filename = None
        self.sent_req = 0                  # plages demandées (octets)
        self.received = 0                  # octets utiles reçus
        self.packets_in = 0
        self.t_accept = None
        self.pending = {}                  # start -> end des plages en vol
        self.parts = None                  # None = fichier complet chez le pair, sinon indices de parts complètes
        self._packed = {}                  # start -> [packed_len, bytearray]
        addr = writer.get_extra_info("peername") or ("?", 0)
        self.addr = f"{addr[0]}:{addr[1]}"
        self.tag = f"[{ident.nick}<-{self.addr}]"

    # --- envoi -----------------------------------------------------------
    def send(self, pkt):
        self.w.write(pkt)

    def send_hello(self):
        self.send(P.hello(self.ident, server_ip=self.server[0], server_port=self.server[1]))

    def ask_file(self):
        """Séquence eMule : nom du fichier (+ notre part status), fichier courant, demande de slot."""
        # part status + nombre de sources complètes (attendu car nous annonçons ExtendedRequests v2)
        self.send(P.frame(P.OP_REQUESTFILENAME, self.filehash + P.part_status(self.size) + P.u16(0)))
        if self.size > P.PARTSIZE:
            self.send(P.frame(P.OP_SETREQFILEID, self.filehash))
        self.send(P.frame(P.OP_STARTUPLOADREQ, self.filehash))
        self.state = "queued" if self.state != "uploading" else self.state

    def ask_hashset(self):
        self.send(P.frame(P.OP_HASHSETREQUEST, self.filehash))

    def request(self, ranges):
        """Demande des plages [a, b) par paquets de 3."""
        ranges = list(ranges)
        for i in range(0, len(ranges), 3):
            chunk = ranges[i:i + 3]
            self.send(P.request_parts(self.filehash, chunk))
            for a, b in chunk:
                self.pending[a] = b
                self.sent_req += b - a

    def inflight(self):
        return sum(b - a for a, b in self.pending.items())

    def available(self):
        """Plages d'octets que ce pair peut servir (parts complètes), ou None pour tout."""
        if self.parts is None:
            return None
        return [(p * P.PARTSIZE, min((p + 1) * P.PARTSIZE, self.size)) for p in sorted(self.parts)]

    def close(self):
        if self.state != "closed":
            self.state = "closed"
            self.w.close()

    # --- réception -------------------------------------------------------
    async def run(self, inbound):
        try:
            if not inbound:
                self.send_hello()
            while True:
                prot, op, pl = await P.read_packet(self.r)
                self.packets_in += 1
                self._dispatch(prot, op, pl, inbound)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, OSError) as e:
            log.info("%s fermé : %s", self.tag, e or type(e).__name__)
        finally:
            lost, state = dict(self.pending), self.state
            self.pending.clear()
            self.close()
            self.on_event(self, "closed", lost, state)

    def _dispatch(self, prot, op, pl, inbound):
        if prot == P.PROT_ED2K:
            if op == P.OP_HELLO:
                self.remote = P.parse_hello(pl, False)
                self.send(P.hello_answer(self.ident, server_ip=self.server[0], server_port=self.server[1]))
                self._connected()
            elif op == P.OP_HELLOANSWER:
                self.remote = P.parse_hello(pl, True)
                self._connected()
            elif op == P.OP_REQFILENAMEANSWER:
                r = P.Reader(pl); r.hash(); self.filename = r.string()
                self.on_event(self, "filename", self.filename)
            elif op == P.OP_FILESTATUS:
                r = P.Reader(pl); r.hash(); n = r.u16(); bits = bytes(r.take(r.left()))
                self.parts = None if n == 0 else {i for i in range(n) if bits[i // 8] >> (i % 8) & 1}
                self.on_event(self, "filestatus", self.parts)
            elif op == P.OP_FILEREQANSNOFIL:
                self.on_event(self, "nofile")
            elif op == P.OP_HASHSETANSWER:
                r = P.Reader(pl); r.hash(); n = r.u16()
                self.on_event(self, "hashset", [r.hash() for _ in range(n)])
            elif op == P.OP_ACCEPTUPLOADREQ:
                self.state, self.t_accept = "uploading", time.monotonic()
                self.on_event(self, "accept")
            elif op == P.OP_QUEUERANK:
                self.rank = P.Reader(pl).u32(); self.state = "queued"
                self.on_event(self, "rank", self.rank)
            elif op == P.OP_OUTOFPARTREQS:
                self.state = "queued"
                lost = dict(self.pending); self.pending.clear()
                self.on_event(self, "outofparts", lost)
            elif op in (P.OP_SENDINGPART,):
                self._block(*P.parse_sending_part(pl)[1:])
            elif op == P.OP_REQUESTFILENAME:
                self._seed_reqname(pl)
            elif op == P.OP_SETREQFILEID:
                self._seed_setreqid(pl)
            elif op == P.OP_STARTUPLOADREQ:
                self._seed_startupload(pl)
            elif op in (P.OP_REQUESTPARTS, P.OP_REQUESTPARTS_I64):
                self._seed_request(pl, op == P.OP_REQUESTPARTS_I64)
            elif op == P.OP_HASHSETREQUEST:
                self._seed_hashset(pl)
            elif op in (P.OP_CANCELTRANSFER, P.OP_END_OF_DOWNLOAD):
                pass                       # le pair a fini de nous télécharger
            elif op == P.OP_ASKSHAREDFILES:
                self.send(P.frame(P.OP_ASKSHAREDDENIEDANS))
            elif op == P.OP_MESSAGE:
                log.info("%s message : %s", self.tag, P.Reader(pl).string())
            else:
                log.debug("%s ed2k 0x%02x ignoré (%d o)", self.tag, op, len(pl))
        elif prot == P.PROT_EMULE:
            if op == P.OP_QUEUERANKING:
                self.rank = P.Reader(pl).u16(); self.state = "queued"
                self.on_event(self, "rank", self.rank)
            elif op == P.OP_COMPRESSEDPART:
                self._packed_block(*P.parse_compressed_part(pl)[1:])
            elif op == P.OP_SENDINGPART_I64:
                self._block(*P.parse_sending_part(pl, True)[1:])
            elif op == P.OP_COMPRESSEDPART_I64:
                self._packed_block(*P.parse_compressed_part(pl, True)[1:])
            elif op == P.OP_PUBLICIP_REQ:
                ip = self.w.get_extra_info("peername")[0]
                self.send(P.frame(P.OP_PUBLICIP_ANSWER, bytes(int(x) for x in ip.split(".")), P.PROT_EMULE))
            elif op == P.OP_EMULEINFO:
                self.send(P.frame(P.OP_EMULEINFOANSWER, P.u8(0x44) + P.u8(P.EDONKEYVERSION) + P.u32(0), P.PROT_EMULE))
            elif op in (P.OP_MULTIPACKET, P.OP_MULTIPACKET_EXT):
                self.send(P.frame(P.OP_FILEREQANSNOFIL, pl[:16]))
            else:
                log.debug("%s emule 0x%02x ignoré (%d o)", self.tag, op, len(pl))
        else:
            raise ValueError(f"protocole inconnu 0x{prot:02x}")

    # --- côté repartage (nous servons nos parts vérifiées) ---------------
    def _can_seed(self, payload16):
        return self.seeder is not None and payload16[:16] == self.filehash and self.seeder.parts()

    def _seed_reqname(self, pl):
        if not self._can_seed(pl):
            self.send(P.frame(P.OP_FILEREQANSNOFIL, pl[:16])); return
        self.send(P.reqfilename_answer(self.filehash, self.seeder.filename))
        self.send(P.file_status(self.filehash, self.size, self.seeder.parts()))

    def _seed_setreqid(self, pl):
        if self._can_seed(pl):
            self.send(P.file_status(self.filehash, self.size, self.seeder.parts()))
        else:
            self.send(P.frame(P.OP_FILEREQANSNOFIL, pl[:16]))

    def _seed_startupload(self, pl):
        if self._can_seed(pl):
            self.send(P.frame(P.OP_ACCEPTUPLOADREQ))     # slot immédiat : on repartage volontiers
            self.on_event(self, "seed_start")

    def _seed_hashset(self, pl):
        hs = self.seeder and self.seeder.hashset
        if hs and pl[:16] == self.filehash:
            self.send(P.hashset_answer(self.filehash, hs))

    def _seed_request(self, pl, i64):
        if self.seeder is None:
            return
        r = P.Reader(pl); r.hash()
        starts = [r.u64() if i64 else r.u32() for _ in range(3)]
        ends = [r.u64() if i64 else r.u32() for _ in range(3)]
        ranges = [(s, e) for s, e in zip(starts, ends) if e > s]
        if ranges:
            asyncio.create_task(self._serve(ranges))

    async def _serve(self, ranges):
        """Envoie les octets demandés qui tombent dans nos parts vérifiées, par tranches de 10 Ko."""
        try:
            for a, b in ranges:
                for start, chunk in self.seeder.read_chunks(a, b):
                    self.send(P.sending_part(self.filehash, start, chunk))
                    self.served += len(chunk)
                    await self.w.drain()
            self.on_event(self, "served")
        except (ConnectionError, OSError):
            self.close()

    def _connected(self):
        if self.state == "hello":
            self.state = "connected"
        self.on_event(self, "hello", self.remote)

    def _block(self, start, data):
        end = start + len(data)
        self.received += len(data)
        # retire la partie livrée des plages en vol (les blocs arrivent en tranches ≤ 10240 o)
        for a in [a for a, b in self.pending.items() if a <= start < b]:
            b = self.pending.pop(a)
            if end < b:
                self.pending[end] = b
        self.on_block(self, start, data)

    def _packed_block(self, start, packed_len, frag):
        buf = self._packed.setdefault(start, [packed_len, bytearray()])
        buf[1] += frag
        if len(buf[1]) >= packed_len:
            del self._packed[start]
            self._block(start, zlib.decompress(bytes(buf[1])))
