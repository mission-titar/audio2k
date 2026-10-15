"""Ce qu'on veut du fichier distant : en-tête + fin de fichier (idx1) puis audio, ou parts entières."""
import json
import struct

from . import avi
from .intervals import Intervals
from .md4 import md4
from .store import Store

HEAD = 65536
TAIL = 2 << 20              # la fin du fichier contient idx1 (≈ 16 o par chunk) : 2 Mo couvrent ~40 min
PARTSIZE = avi.PARTSIZE


class Plan:
    """`wanted()` renvoie les plages utiles à l'instant t, selon ce que le Store contient déjà."""

    def __init__(self, store: Store, mode="audio", gap=avi.SENDINGPART_OVERHEAD):
        self.store, self.mode, self.gap = store, mode, gap
        self.layout = None          # en-tête analysé (part 0)
        self.entries = None         # entrées idx1 en offsets absolus
        self.audio = None           # plages audio (payload) une fois l'index résolu
        self._idx1 = None           # (offset du fourcc idx1, taille) trouvé dans la fin de fichier
        self._hs = store.dir / f"{store.bin.stem}.hashset.json"
        self.hashset = [bytes.fromhex(h) for h in json.load(open(self._hs))] if self._hs.exists() else None
        self.verified = set()       # indices de parts complètes dont le MD4 est vérifié

    # --- hashset -------------------------------------------------------------
    def set_hashset(self, hashes):
        """Accepte le hashset s'il redonne bien le hash du fichier ; le persiste."""
        fh = bytes.fromhex(self.store.bin.stem)
        root = hashes[0] if len(hashes) == 1 else md4(b"".join(hashes)).digest()
        if root != fh:
            return False
        self.hashset = hashes
        json.dump([h.hex() for h in hashes], open(self._hs, "w"))
        return True

    def verify_parts(self):
        """Vérifie le MD4 des parts complètes pas encore vérifiées ; renvoie (ok, corrompues)."""
        ok, bad = [], []
        if not self.hashset:
            return ok, bad
        n = -(-self.store.size // PARTSIZE)
        for p in range(n):
            a, b = p * PARTSIZE, min((p + 1) * PARTSIZE, self.store.size)
            if p in self.verified or not self.store.have.contains(a, b):
                continue
            if md4(self.store.read(a, b)).digest() == self.hashset[p]:
                self.verified.add(p); ok.append(p)
            else:
                bad.append(p)
        return ok, bad

    # --- analyse AVI ---------------------------------------------------------
    def _tail_range(self):
        return (max(0, self.store.size - TAIL), self.store.size)

    def _try_parse(self):
        s = self.store
        if self.layout is None and s.have.contains(0, min(HEAD, s.size)):
            self.layout = avi.parse_header(s.read(0, min(HEAD, s.size)), s.size)
        if self.audio is not None:
            return
        if self._idx1 is None:
            self._idx1 = self._find_idx1()
        if self._idx1 is None:
            return
        off, sz = self._idx1
        if not s.have.contains(off + 8, off + 8 + sz):
            return
        raw = s.read(off + 8, off + 8 + sz)
        base = self.layout.movi_off + 8 if self.layout else self._infer_base(raw)
        if base is None:
            return
        self.entries = avi.parse_idx1_with_base(raw, base)
        self.audio = avi.audio_payload_ranges(avi.audio_entries_by_ckid(self.entries))

    def _find_idx1(self):
        """Cherche le chunk idx1 : via l'en-tête si on l'a, sinon dans la fin de fichier acquise."""
        s = self.store
        if self.layout:
            off = avi.idx1_guess(self.layout)
            while s.have.contains(off, off + 8):
                fcc, sz = struct.unpack("<4sI", s.read(off, off + 8))
                if fcc == b"idx1":
                    return off, sz
                off += 8 + sz + (sz & 1)                 # chunk intermédiaire (JUNK...)
            return None
        a, b = self._tail_range()
        for x, y in self.store.have:
            x, y = max(x, a), min(y, b)
            if y - x < 16:
                continue
            data = s.read(x, y)
            pos = data.find(b"idx1")
            while pos >= 0:
                sz = struct.unpack_from("<I", data, pos + 4)[0] if pos + 8 <= len(data) else 0
                if sz % 16 == 0 and sz >= 16 and x + pos + 8 + sz <= s.size and data[pos + 8 + 2:pos + 8 + 4] in (b"wb", b"dc", b"db", b"tx"):
                    return x + pos, sz
                pos = data.find(b"idx1", pos + 1)
        return None

    def _infer_base(self, raw):
        """Sans en-tête : retrouve la base des offsets idx1 grâce aux derniers chunks, présents dans la fin de fichier."""
        s = self.store
        n = len(raw) // 16
        if n == 0:
            return None
        cands = None
        for i in range(n - 1, max(n - 40, -1), -1):
            ckid, _, off, sz = struct.unpack_from("<4sIII", raw, i * 16)
            found = set()
            for x, y in s.have:                             # cherche l'en-tête de chunk ckid+size dans nos octets
                data = s.read(x, y) if y - x <= TAIL + HEAD else None
                if data is None:
                    continue
                pat = ckid + struct.pack("<I", sz)
                p = data.find(pat)
                while p >= 0:
                    found.add(x + p - off)
                    p = data.find(pat, p + 1)
            if not found:
                continue
            cands = found if cands is None else cands & found
            if len(cands) == 1:
                return cands.pop()
            if not cands:
                return None
        return None

    # --- plages voulues ------------------------------------------------------
    def wanted(self):
        s = self.store
        if self.mode == "all":
            return [(0, s.size)]
        self._try_parse()
        head = (0, min(HEAD, s.size))
        if self.audio is None:
            return [head, self._tail_range()]
        return [head] + self.audio

    def phase(self):
        if self.mode == "all": return "parts"
        self._try_parse()
        return "audio" if self.audio is not None else "en-tête/idx1"

    def next_ranges(self, claimed, budget, max_len=avi.MAX_REQ, available=None, avoid=None):
        """Plages manquantes non demandées (servables par le pair, hors `avoid` si possible), jusqu'à `budget` o."""
        missing = self.store.missing(self.wanted())
        if claimed:
            missing = [r for a, b in missing for r in claimed.missing(a, b)]
        if available is not None:
            avail = Intervals(available)
            missing = [(max(a, x), min(b, y)) for a, b in missing for x, y in avail if a < y and x < b]
        if avoid:
            av = Intervals(avoid)
            kept = [r for a, b in missing for r in av.missing(a, b)]
            missing = kept or missing              # si rien d'exclusif, on sert quand même
        if self.mode == "all":
            missing = self._order_parts(missing)
        out, tot = [], 0
        for a, b in avi.coalesce(missing, self.gap, max_len):
            if tot >= budget:
                break
            b = min(b, a + max(budget - tot, 1))
            out.append((a, b))
            tot += b - a
        return out

    def _order_parts(self, missing):
        """Objectif 2 : d'abord les parts ed2k les plus avancées, séquentiellement."""
        by_part = {}
        for a, b in missing:
            by_part.setdefault(a // PARTSIZE, []).append((a, b))
        order = sorted(by_part, key=lambda p: sum(b - a for a, b in by_part[p]))
        return [r for p in order for r in by_part[p]]

    def extract_mp3(self, out_path):
        """Concatène les chunks audio acquis ; renvoie (chunks écrits, chunks manquants)."""
        self._try_parse()
        if self.audio is None:
            raise RuntimeError("index idx1 pas encore acquis")
        ok = miss = 0
        with open(out_path, "wb") as f:
            for a, b in self.audio:
                if self.store.have.contains(a, b):
                    f.write(self.store.read(a, b)); ok += 1
                else:
                    miss += 1
        return ok, miss


if __name__ == "__main__":
    import glob, os, tempfile
    path = glob.glob("*.avi")[0]
    size = os.path.getsize(path)
    src = open(path, "rb")

    def fill(st, ranges):
        for a, b in ranges:
            src.seek(a); st.write(a, src.read(b - a))

    with tempfile.TemporaryDirectory() as d:
        # 1) sans en-tête : la fin de fichier suffit à résoudre l'index et la base des offsets
        st = Store(d, b"\2" * 16, size)
        plan = Plan(st)
        w = plan.wanted()
        assert w == [(0, HEAD), (size - TAIL, size)], w
        fill(st, [(size - TAIL, size)])
        assert plan.phase() == "audio" and plan.audio is not None and len(plan.audio) == 17996
        assert plan.entries[0][2] == 10240 + 12, plan.entries[0]          # premier chunk juste après 'LIST movi'
        # la part 9 seule : on ne doit demander que dedans
        p9 = [(9 * PARTSIZE, size)]
        r = plan.next_ranges(Intervals(), 10 ** 9, available=p9)
        assert r and all(a >= 9 * PARTSIZE for a, b in r)
        # `avoid` : un pair complet ne reçoit pas ce que d'autres peuvent servir
        r2 = plan.next_ranges(Intervals(), 10 ** 9, avoid=p9)
        assert r2 and all(b <= 9 * PARTSIZE for a, b in r2)
        # 2) tout récupérer, extraire
        fill(st, plan.next_ranges(Intervals(), 10 ** 9))
        assert plan.next_ranges(Intervals(), 1) == []
        ok, miss = plan.extract_mp3(os.path.join(d, "out.mp3"))
        assert miss == 0 and ok == 17996
        # 3) avec en-tête d'abord : même résultat
        st2 = Store(d, b"\3" * 16, size)
        plan2 = Plan(st2)
        fill(st2, [(0, HEAD)])
        fill(st2, plan2.next_ranges(Intervals(), 10 ** 9))
        assert plan2.phase() == "audio" and plan2.layout and plan2.audio == plan.audio
        print(f"plan ok : {ok} chunks, {st.have.total()} o acquis sur {size} ({100 * st.have.total() / size:.1f} %)")
