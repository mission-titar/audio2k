"""Format binaire eD2K/eMule : framing, tags, paquets usuels (cf. docs/ed2k-wire.md)."""
import os
import struct
import zlib
from dataclasses import dataclass

PROT_ED2K, PROT_EMULE, PROT_PACKED = 0xE3, 0xC5, 0xD4
PARTSIZE, EMBLOCKSIZE = 9_728_000, 184_320
EDONKEYVERSION = 0x3C

# serveur
OP_LOGINREQUEST, OP_REJECT, OP_OFFERFILES, OP_GETSOURCES, OP_CALLBACKREQUEST = 0x01, 0x05, 0x15, 0x19, 0x1C
OP_GETSOURCES_OBFU, OP_SERVERSTATUS, OP_CALLBACKREQUESTED, OP_CALLBACK_FAIL = 0x23, 0x34, 0x35, 0x36
OP_SERVERMESSAGE, OP_IDCHANGE, OP_SERVERIDENT, OP_FOUNDSOURCES, OP_FOUNDSOURCES_OBFU = 0x38, 0x40, 0x41, 0x42, 0x44
OP_SERVERLIST, OP_GETSERVERLIST = 0x32, 0x14
# client (0xE3)
OP_HELLO, OP_SENDINGPART, OP_REQUESTPARTS, OP_FILEREQANSNOFIL, OP_END_OF_DOWNLOAD = 0x01, 0x46, 0x47, 0x48, 0x49
OP_ASKSHAREDFILES, OP_HELLOANSWER, OP_CHANGE_CLIENT_ID, OP_MESSAGE, OP_SETREQFILEID = 0x4A, 0x4C, 0x4D, 0x4E, 0x4F
OP_FILESTATUS, OP_HASHSETREQUEST, OP_HASHSETANSWER, OP_STARTUPLOADREQ, OP_ACCEPTUPLOADREQ = 0x50, 0x51, 0x52, 0x54, 0x55
OP_CANCELTRANSFER, OP_OUTOFPARTREQS, OP_REQUESTFILENAME, OP_REQFILENAMEANSWER, OP_QUEUERANK = 0x56, 0x57, 0x58, 0x59, 0x5C
OP_ASKSHAREDDENIEDANS = 0x61
# client étendu (0xC5)
OP_EMULEINFO, OP_EMULEINFOANSWER, OP_COMPRESSEDPART, OP_QUEUERANKING = 0x01, 0x02, 0x40, 0x60
OP_REQUESTSOURCES, OP_ANSWERSOURCES, OP_PUBLICKEY, OP_SIGNATURE, OP_SECIDENTSTATE = 0x81, 0x82, 0x85, 0x86, 0x87
OP_MULTIPACKET, OP_MULTIPACKETANSWER, OP_PUBLICIP_REQ, OP_PUBLICIP_ANSWER = 0x92, 0x93, 0x97, 0x98
OP_AICHFILEHASHREQ, OP_COMPRESSEDPART_I64, OP_SENDINGPART_I64, OP_REQUESTPARTS_I64 = 0x9E, 0xA1, 0xA2, 0xA3
OP_MULTIPACKET_EXT = 0xA4
# UDP client (0xC5)
OP_REASKFILEPING, OP_REASKACK, OP_FILENOTFOUND, OP_QUEUEFULL = 0x90, 0x91, 0x92, 0x93

CT_NAME, CT_PORT, CT_VERSION, CT_SERVER_FLAGS = 0x01, 0x0F, 0x11, 0x20
CT_EMULE_UDPPORTS, CT_EMULE_MISCOPTIONS1, CT_EMULE_VERSION, CT_EMULE_MISCOPTIONS2 = 0xF9, 0xFA, 0xFB, 0xFE
SRVCAP_ZLIB, SRVCAP_NEWTAGS, SRVCAP_UNICODE, SRVCAP_LARGEFILES = 0x0001, 0x0008, 0x0010, 0x0100

TAG_HASH, TAG_STRING, TAG_UINT32, TAG_FLOAT, TAG_BOOL, TAG_BLOB = 1, 2, 3, 4, 5, 7
TAG_UINT16, TAG_UINT8, TAG_BSOB, TAG_UINT64 = 8, 9, 10, 11


def u8(v): return struct.pack("<B", v)
def u16(v): return struct.pack("<H", v)
def u32(v): return struct.pack("<I", v)
def u64(v): return struct.pack("<Q", v)


class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def take(self, n):
        if self.p + n > len(self.d):
            raise ValueError("paquet tronqué")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack("<H", self.take(2))[0]
    def u32(self): return struct.unpack("<I", self.take(4))[0]
    def u64(self): return struct.unpack("<Q", self.take(8))[0]
    def hash(self): return bytes(self.take(16))
    def string(self, n=None): return bytes(self.take(self.u16() if n is None else n)).decode("utf-8", "replace")
    def left(self): return len(self.d) - self.p

    def tag(self):
        """(nom: int ou bytes, valeur). Formats classique et compact (bit 0x80)."""
        t = self.u8()
        if t & 0x80:
            t &= 0x7F
            name = self.u8()
        else:
            n = self.u16()
            name = self.u8() if n == 1 else bytes(self.take(n))
        if t == TAG_STRING: return name, self.string()
        if 0x11 <= t <= 0x20: return name, self.string(t - 0x10)
        if t == TAG_UINT32: return name, self.u32()
        if t == TAG_UINT64: return name, self.u64()
        if t == TAG_UINT16: return name, self.u16()
        if t in (TAG_UINT8, TAG_BOOL): return name, self.u8()
        if t == TAG_FLOAT: return name, struct.unpack("<f", self.take(4))[0]
        if t == TAG_HASH: return name, self.hash()
        if t == TAG_BLOB: return name, bytes(self.take(self.u32()))
        if t == TAG_BSOB: return name, bytes(self.take(self.u8()))
        raise ValueError(f"type de tag inconnu 0x{t:02x}")

    def tags(self):
        return dict(self.tag() for _ in range(self.u32()))


def tag(name, value):
    """Encodage classique : type, uint16 1, uint8 nom, valeur."""
    if isinstance(value, str):
        v = value.encode("utf-8")
        return u8(TAG_STRING) + u16(1) + u8(name) + u16(len(v)) + v
    return u8(TAG_UINT32) + u16(1) + u8(name) + u32(value)


def frame(opcode, payload=b"", prot=PROT_ED2K):
    return u8(prot) + u32(len(payload) + 1) + u8(opcode) + payload


async def read_packet(reader):
    """Lit un paquet ; renvoie (prot, opcode, payload), les 0xD4 sont décompressés en 0xC5."""
    hdr = await reader.readexactly(6)
    prot, size, opcode = hdr[0], struct.unpack("<I", hdr[1:5])[0], hdr[5]
    if size < 1 or size > 2_000_000:
        raise ValueError(f"taille de paquet aberrante {size} (prot 0x{prot:02x})")
    payload = await reader.readexactly(size - 1)
    if prot == PROT_PACKED:
        payload, prot = zlib.decompress(payload), PROT_EMULE
    return prot, opcode, payload


def emule_userhash():
    h = bytearray(os.urandom(16))
    h[5], h[14] = 14, 111          # marqueurs d'un userhash eMule
    return bytes(h)


@dataclass
class Identity:
    userhash: bytes
    nick: str
    tcp_port: int
    udp_port: int
    client_id: int = 0            # high ID = notre IP en uint32 (renseigné après login)


def misc_options1(udp_ver=4, data_comp=0, sec_ident=0, sx1=0, ext_req=2, comment=0,
                  peercache=0, no_view_shared=1, multipacket=0, preview=0, unicode=1, aich=0):
    return ((aich << 29) | (unicode << 28) | (udp_ver << 24) | (data_comp << 20) | (sec_ident << 16)
            | (sx1 << 12) | (ext_req << 8) | (comment << 4) | (peercache << 3)
            | (no_view_shared << 2) | (multipacket << 1) | preview)


def misc_options2(large_files=1, ext_multipacket=0, sx2=0, captcha=0, kad_version=0):
    return (captcha << 11) | (sx2 << 10) | (ext_multipacket << 5) | (large_files << 4) | kad_version


def emule_version(mjr=0, mnr=50, upd=0):
    """upd 0 = 'a' (eMule 0.50a annonce upd=0)."""
    return (mjr << 17) | (mnr << 10) | (upd << 7)


def hello_body(ident, server_ip=0, server_port=0, data_comp=0):
    tags = [tag(CT_NAME, ident.nick), tag(CT_VERSION, EDONKEYVERSION),
            # port Kad annoncé = port UDP : un uploader low ID à file chargée n'ignore pas les clients « avec Kad »
            tag(CT_EMULE_UDPPORTS, (ident.udp_port << 16) | ident.udp_port),
            tag(CT_EMULE_MISCOPTIONS1, misc_options1(data_comp=data_comp)),
            tag(CT_EMULE_MISCOPTIONS2, misc_options2()), tag(CT_EMULE_VERSION, emule_version())]
    return (ident.userhash + u32(ident.client_id) + u16(ident.tcp_port) + u32(len(tags)) + b"".join(tags)
            + u32(server_ip) + u16(server_port))


def hello(ident, **kw):
    return frame(OP_HELLO, u8(16) + hello_body(ident, **kw))


def hello_answer(ident, **kw):
    return frame(OP_HELLOANSWER, hello_body(ident, **kw))


@dataclass
class Hello:
    userhash: bytes
    client_id: int
    port: int
    tags: dict
    server_ip: int
    server_port: int

    @property
    def udp_port(self): return self.tags.get(CT_EMULE_UDPPORTS, 0) & 0xFFFF

    @property
    def misc1(self): return self.tags.get(CT_EMULE_MISCOPTIONS1, 0)

    @property
    def is_emule(self): return CT_EMULE_MISCOPTIONS1 in self.tags or self.userhash[5] == 14 and self.userhash[14] == 111

    def version_str(self):
        v = self.tags.get(CT_EMULE_VERSION)
        if v is None:
            return "?"
        return f"eMule {(v >> 17) & 0x7F}.{(v >> 10) & 0x7F}{chr(97 + ((v >> 7) & 7))}"


def parse_hello(payload, is_answer):
    r = Reader(payload)
    if not is_answer and r.u8() != 16:
        raise ValueError("taille de hash != 16")
    h = Hello(r.hash(), r.u32(), r.u16(), r.tags(), 0, 0)
    if r.left() >= 6:
        h.server_ip, h.server_port = r.u32(), r.u16()
    return h


def login_request(ident, flags=SRVCAP_ZLIB | SRVCAP_NEWTAGS | SRVCAP_LARGEFILES | SRVCAP_UNICODE):
    tags = [tag(CT_NAME, ident.nick), tag(CT_VERSION, EDONKEYVERSION),
            tag(CT_SERVER_FLAGS, flags), tag(CT_EMULE_VERSION, emule_version())]
    return frame(OP_LOGINREQUEST, ident.userhash + u32(0) + u16(ident.tcp_port) + u32(len(tags)) + b"".join(tags))


FT_FILENAME, FT_FILESIZE = 0x01, 0x02


def offer_files(files):
    """OP_OFFERFILES : s'annoncer au serveur comme source. files = [(hash, client_id, port, name, size)]."""
    body = u32(len(files))
    for fh, cid, port, name, size in files:
        tags = [tag(FT_FILENAME, name), tag(FT_FILESIZE, size)]
        body += fh + u32(cid) + u16(port) + u32(len(tags)) + b"".join(tags)
    return frame(OP_OFFERFILES, body)


def reqfilename_answer(filehash, name):
    return frame(OP_REQFILENAMEANSWER, filehash + u16(len(name.encode())) + name.encode())


def file_status(filehash, size, have):
    """Réponse OP_FILESTATUS : parts complètes que nous servons (0 signifierait 'tout')."""
    return frame(OP_FILESTATUS, filehash + part_status(size, have))


def sending_part(filehash, start, data):
    end = start + len(data)
    if end >= 1 << 32:
        return frame(OP_SENDINGPART_I64, filehash + u64(start) + u64(end) + data, PROT_EMULE)
    return frame(OP_SENDINGPART, filehash + u32(start) + u32(end) + data)


def hashset_answer(filehash, hashes):
    return frame(OP_HASHSETANSWER, filehash + u16(len(hashes)) + b"".join(hashes))


def get_sources(filehash, size):
    if size < 1 << 32:
        return frame(OP_GETSOURCES, filehash + u32(size))
    return frame(OP_GETSOURCES, filehash + u32(0) + u64(size))


def parse_found_sources(payload, obfu=False):
    r = Reader(payload)
    fh, n = r.hash(), r.u8()
    out = []
    for _ in range(n):
        cid, port = r.u32(), r.u16()
        if obfu:
            crypt = r.u8()
            if crypt & 0x80:
                r.hash()
        out.append((cid, port))
    return fh, out


def is_low_id(cid): return cid < 16_777_216


def ip_str(cid): return ".".join(str(b) for b in u32(cid))


def part_status(size, have=None):
    """uint16 nb parts + bitfield ; `have` = ensemble d'indices de parts complètes."""
    n = size // PARTSIZE + 1
    bits = bytearray((n + 7) // 8)
    for p in (have or ()):
        bits[p // 8] |= 1 << (p % 8)
    return u16(n) + bytes(bits)


def request_parts(filehash, ranges):
    """ranges : jusqu'à 3 plages [a, b) (end exclusif) ; les vides sont (0, 0)."""
    rs = list(ranges) + [(0, 0)] * (3 - len(ranges))
    if any(b >= 1 << 32 for _, b in rs):
        return frame(OP_REQUESTPARTS_I64, filehash + b"".join(u64(a) for a, _ in rs) + b"".join(u64(b) for _, b in rs), PROT_EMULE)
    return frame(OP_REQUESTPARTS, filehash + b"".join(u32(a) for a, _ in rs) + b"".join(u32(b) for _, b in rs))


def parse_sending_part(payload, i64=False):
    r = Reader(payload)
    fh = r.hash()
    a, b = (r.u64(), r.u64()) if i64 else (r.u32(), r.u32())
    data = bytes(r.take(r.left()))
    if b - a != len(data):
        raise ValueError(f"bloc incohérent {a}-{b} ({len(data)} o)")
    return fh, a, data


def parse_compressed_part(payload, i64=False):
    """(hash, start, taille_compressee_totale, fragment) : à réassembler jusqu'à la taille."""
    r = Reader(payload)
    fh = r.hash()
    a = r.u64() if i64 else r.u32()
    packed_len = r.u32()
    return fh, a, packed_len, bytes(r.take(r.left()))


def ed2k_link(name, size, filehash):
    from urllib.parse import quote
    return f"ed2k://|file|{quote(name, safe='')}|{size}|{filehash.hex().upper()}|/"


def parse_ed2k_link(link):
    from urllib.parse import unquote
    parts = link.split("|")
    if parts[1] == "file":
        return unquote(parts[2]), int(parts[3]), bytes.fromhex(parts[4])
    if parts[1] == "server":
        return parts[2], int(parts[3])
    raise ValueError(link)


if __name__ == "__main__":
    ident = Identity(emule_userhash(), "audio2k", 4662, 4672)
    pkt = hello(ident, server_ip=1, server_port=2)
    assert pkt[0] == PROT_ED2K and pkt[5] == OP_HELLO
    h = parse_hello(pkt[6:], False)
    assert h.userhash == ident.userhash and h.port == 4662 and h.udp_port == 4672 and h.is_emule
    assert h.version_str() == "eMule 0.50a" and h.server_ip == 1
    ha = parse_hello(hello_answer(ident)[6:], True)
    assert ha.userhash == ident.userhash
    # tag compact
    r = Reader(bytes([TAG_UINT32 | 0x80, 0x11]) + u32(7) + bytes([0x12]) + u16(1) + bytes([CT_NAME]) + b"ab")
    assert r.tag() == (0x11, 7) and r.tag() == (CT_NAME, "ab")
    rp = request_parts(b"\0" * 16, [(10, 20), (30, 40)])
    assert rp[6:] == b"\0" * 16 + u32(10) + u32(30) + u32(0) + u32(20) + u32(40) + u32(0)
    assert parse_sending_part(b"\1" * 16 + u32(5) + u32(8) + b"xyz") == (b"\1" * 16, 5, b"xyz")
    assert part_status(80_973_824) == u16(9) + b"\0\0"
    n, s, fh = parse_ed2k_link("ed2k://|file|a%20b.avi|123|51F29414B59FA5213AFD6C64D6869048|/")
    assert (n, s) == ("a b.avi", 123) and ed2k_link(n, s, fh).endswith("|123|51F29414B59FA5213AFD6C64D6869048|/")
    print("proto ok")
