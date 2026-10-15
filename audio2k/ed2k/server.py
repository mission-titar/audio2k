"""Session TCP avec un serveur eD2K : login, sources, callback vers une source LOW ID."""
import asyncio
import logging

from . import proto as P

log = logging.getLogger("server")


class Server:
    def __init__(self, host, port, ident):
        self.host, self.port, self.ident = host, port, ident
        self.ip = 0                    # IP du serveur en uint32 (pour les Hello)
        self.client_id = None
        self.flags = 0
        self._id_event = asyncio.Event()
        self._sources = {}             # filehash -> Future
        self._callback_fail = asyncio.Queue()
        self.r = self.w = None
        self.task = None

    async def connect(self, timeout=30):
        self.r, self.w = await asyncio.open_connection(self.host, self.port)
        ip = self.w.get_extra_info("peername")[0]
        self.ip = int.from_bytes(bytes(int(x) for x in ip.split(".")), "little")
        self.w.write(P.login_request(self.ident))
        self.task = asyncio.create_task(self._loop())
        await asyncio.wait_for(self._id_event.wait(), timeout)
        if self.client_id is None:
            raise ConnectionError("login refusé")
        self.ident.client_id = self.client_id if not P.is_low_id(self.client_id) else 0
        self.w.write(P.frame(P.OP_OFFERFILES, P.u32(0)))
        return self.client_id

    async def sources(self, filehash, size, timeout=20):
        fut = asyncio.get_running_loop().create_future()
        self._sources[filehash] = fut
        self.w.write(P.get_sources(filehash, size))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return []
        finally:
            self._sources.pop(filehash, None)

    def callback(self, client_id):
        log.info("callback demandé vers l'ID %d", client_id)
        self.w.write(P.frame(P.OP_CALLBACKREQUEST, P.u32(client_id)))

    async def callback_failed(self, timeout):
        """Attend un OP_CALLBACK_FAIL pendant `timeout` s ; True s'il en arrive un."""
        try:
            await asyncio.wait_for(self._callback_fail.get(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def keepalive(self):
        self.w.write(P.frame(P.OP_OFFERFILES, P.u32(0)))

    def close(self):
        if self.w:
            self.w.close()

    async def _loop(self):
        try:
            while True:
                prot, op, pl = await P.read_packet(self.r)
                if op == P.OP_IDCHANGE:
                    r = P.Reader(pl)
                    self.client_id = r.u32()
                    self.flags = r.u32() if r.left() >= 4 else 0
                    log.info("ID reçu : %d (%s), flags 0x%x", self.client_id,
                             "LOW" if P.is_low_id(self.client_id) else "HIGH " + P.ip_str(self.client_id), self.flags)
                    self._id_event.set()
                elif op == P.OP_SERVERMESSAGE:
                    for line in P.Reader(pl).string().splitlines():
                        log.info("serveur : %s", line)
                elif op in (P.OP_FOUNDSOURCES, P.OP_FOUNDSOURCES_OBFU):
                    fh, srcs = P.parse_found_sources(pl, op == P.OP_FOUNDSOURCES_OBFU)
                    log.info("sources pour %s : %s", fh.hex().upper()[:8],
                             [(P.ip_str(c) if not P.is_low_id(c) else f"lowid {c}", p) for c, p in srcs])
                    fut = self._sources.get(fh)
                    if fut and not fut.done():
                        fut.set_result(srcs)
                elif op == P.OP_CALLBACK_FAIL:
                    log.warning("callback refusé par le serveur (source déconnectée ?)")
                    self._callback_fail.put_nowait(True)
                elif op == P.OP_SERVERSTATUS:
                    r = P.Reader(pl); log.info("serveur : %d utilisateurs, %d fichiers", r.u32(), r.u32())
                elif op == P.OP_REJECT:
                    log.error("serveur : OP_REJECT")
                    self._id_event.set()
                elif op in (P.OP_SERVERIDENT, P.OP_SERVERLIST):
                    pass
                else:
                    log.debug("serveur 0x%02x ignoré (%d o)", op, len(pl))
        except (asyncio.IncompleteReadError, ConnectionError, ValueError, OSError) as e:
            log.warning("connexion serveur fermée : %s", e or type(e).__name__)
            self._id_event.set()
            for fut in self._sources.values():
                if not fut.done():
                    fut.set_result([])
