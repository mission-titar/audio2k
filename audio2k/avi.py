"""Analyse partielle d'un AVI 1.0 : en-tête, index idx1, plages d'octets audio."""
import struct
from dataclasses import dataclass, field

PARTSIZE = 9_728_000
EMBLOCKSIZE = 184_320
MAX_REQ = 3 * EMBLOCKSIZE          # taille max d'une plage OP_REQUESTPARTS (eMule)
SENDINGPART_OVERHEAD = 30           # 6 en-tête paquet + 16 hash + 4 start + 4 end


@dataclass
class Stream:
    fcc_type: bytes
    handler: bytes
    strf: bytes


@dataclass
class Layout:
    size: int
    movi_off: int              # offset du 'LIST' du movi
    movi_size: int
    idx1_off: int = 0          # offset du fourcc 'idx1'
    idx1_size: int = 0
    streams: list = field(default_factory=list)

    @property
    def idx1_data(self):
        return (self.idx1_off + 8, self.idx1_off + 8 + self.idx1_size)

    @property
    def header(self):
        return (0, self.movi_off + 12)

    def audio_stream(self):
        for i, s in enumerate(self.streams):
            if s.fcc_type == b"auds":
                return i
        raise ValueError("pas de flux audio")


def _chunks(buf, off, end):
    while off + 8 <= end:
        fcc, sz = buf[off:off + 4], struct.unpack_from("<I", buf, off + 4)[0]
        yield fcc, sz, off
        off += 8 + sz + (sz & 1)


def parse_header(head, size):
    """head = premiers octets du fichier (assez pour couvrir hdrl et le LIST movi)."""
    if head[:4] != b"RIFF" or head[8:12] != b"AVI ":
        raise ValueError("pas un AVI")
    lay = Layout(size=size, movi_off=-1, movi_size=0)
    for fcc, sz, off in _chunks(head, 12, len(head)):
        if fcc == b"LIST" and head[off + 8:off + 12] == b"movi":
            lay.movi_off, lay.movi_size = off, sz
            break
        if fcc == b"LIST" and head[off + 8:off + 12] == b"hdrl":
            for f2, s2, o2 in _chunks(head, off + 12, off + 8 + sz):
                if f2 == b"LIST" and head[o2 + 8:o2 + 12] == b"strl":
                    st = Stream(b"", b"", b"")
                    for f3, s3, o3 in _chunks(head, o2 + 12, o2 + 8 + s2):
                        if f3 == b"strh":
                            st.fcc_type, st.handler = head[o3 + 8:o3 + 12], head[o3 + 12:o3 + 16]
                        elif f3 == b"strf":
                            st.strf = head[o3 + 8:o3 + 8 + s3]
                    lay.streams.append(st)
    if lay.movi_off < 0:
        raise ValueError("LIST movi introuvable dans l'en-tête fourni : lire plus d'octets")
    return lay


def idx1_guess(lay):
    """Offset probable du chunk suivant movi (là où idx1 devrait se trouver)."""
    return lay.movi_off + 8 + lay.movi_size + (lay.movi_size & 1)


def parse_idx1(lay, data):
    """Entrées (ckid, flags, offset_absolu_du_chunk, size)."""
    out = []
    n = len(data) // 16
    base = lay.movi_off + 8       # offsets idx1 relatifs au fourcc 'movi' (convention usuelle)
    if n and struct.unpack_from("<I", data, 8)[0] >= lay.movi_off:
        base = 0                  # convention absolue
    for i in range(n):
        ckid, flags, off, sz = struct.unpack_from("<4sIII", data, i * 16)
        out.append((ckid, flags, base + off, sz))
    return out


def parse_idx1_with_base(data, base):
    """Entrées (ckid, flags, offset_absolu, size) avec une base connue (movi_off + 8, ou 0 si absolu)."""
    return [(ckid, flags, base + off, sz)
            for ckid, flags, off, sz in (struct.unpack_from("<4sIII", data, i * 16) for i in range(len(data) // 16))]


def audio_entries(lay, entries):
    tag = b"%02d" % lay.audio_stream()
    return [e for e in entries if e[0][:2] == tag and e[0][2:4] in (b"wb", b"tx")]


def audio_entries_by_ckid(entries):
    """Sans en-tête : les chunks audio sont ceux dont le ckid finit par 'wb' (flux le plus fréquent)."""
    streams = {}
    for e in entries:
        if e[0][2:4] == b"wb":
            streams.setdefault(e[0][:2], []).append(e)
    if not streams:
        raise ValueError("aucun chunk audio dans l'index")
    return max(streams.values(), key=len)


def audio_payload_ranges(entries):
    """Plages [a, b) des données audio (sans l'en-tête de 8 octets, reconstituable)."""
    return [(off + 8, off + 8 + sz) for _, _, off, sz in entries]


def coalesce(ranges, gap=SENDINGPART_OVERHEAD, max_len=MAX_REQ):
    """Fusionne les plages séparées de moins de `gap` octets ; découpe à max_len."""
    out = []
    for a, b in sorted(ranges):
        if out and a - out[-1][1] <= gap and max(b, out[-1][1]) - out[-1][0] <= max_len:
            out[-1] = (out[-1][0], max(out[-1][1], b))          # les plages peuvent se chevaucher
        else:
            while b - a > max_len:
                out.append((a, a + max_len))
                a += max_len
            out.append((a, b))
    return out


if __name__ == "__main__":
    import glob, os, sys
    path = sys.argv[1] if len(sys.argv) > 1 else glob.glob("*.avi")[0]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        lay = parse_header(f.read(65536), size)
        f.seek(idx1_guess(lay)); fcc, sz = struct.unpack("<4sI", f.read(8))
        assert fcc == b"idx1", fcc
        lay.idx1_off, lay.idx1_size = idx1_guess(lay), sz
        f.seek(lay.idx1_off + 8); entries = parse_idx1(lay, f.read(sz))
        au = audio_entries(lay, entries)
        # vérifie que les offsets pointent bien sur des chunks audio
        for ckid, _, off, s in au[:50]:
            f.seek(off); assert f.read(4) == ckid, (ckid, off)
    rngs = audio_payload_ranges(au)
    req = coalesce(rngs)
    wanted = sum(b - a for a, b in rngs)
    print(f"streams={[s.fcc_type for s in lay.streams]} movi@{lay.movi_off} idx1@{lay.idx1_off} ({lay.idx1_size} o)")
    print(f"audio: {len(au)} chunks, {wanted} o utiles ; {len(req)} plages à demander, "
          f"{sum(b - a for a, b in req)} o + {len(req) * SENDINGPART_OVERHEAD} o d'en-têtes ; "
          f"en-tête+idx1 = {lay.header[1] + lay.idx1_size} o")
    print("avi ok")
