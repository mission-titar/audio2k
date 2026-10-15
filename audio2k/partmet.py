"""Export de l'état vers un couple <n>.part / <n>.part.met importable dans eMule ou aMule."""
import shutil
import struct
import time

from .ed2k import proto as P

PARTFILE_VERSION = 0xE0
FT_FILENAME, FT_FILESIZE, FT_GAPSTART, FT_GAPEND = 0x01, 0x02, 0x09, 0x0A


def _tag_named(name: bytes, type_, value):
    if type_ == P.TAG_STRING:
        v = value.encode("utf-8")
        return P.u8(type_) + P.u16(len(name)) + name + P.u16(len(v)) + v
    return P.u8(type_) + P.u16(len(name)) + name + P.u32(value)


def build_met(name, size, filehash, hashset, gaps, mtime=None):
    """gaps : liste de [a, b) manquants (b exclusif, comme dans le .met)."""
    tags = [_tag_named(P.u8(FT_FILENAME), P.TAG_STRING, name), _tag_named(P.u8(FT_FILESIZE), P.TAG_UINT32, size)]
    for i, (a, b) in enumerate(gaps):
        tags.append(_tag_named(P.u8(FT_GAPSTART) + str(i).encode(), P.TAG_UINT32, a))
        tags.append(_tag_named(P.u8(FT_GAPEND) + str(i).encode(), P.TAG_UINT32, b))
    body = filehash + P.u16(len(hashset)) + b"".join(hashset)
    return P.u8(PARTFILE_VERSION) + P.u32(int(mtime or time.time())) + body + P.u32(len(tags)) + b"".join(tags)


def export(store, plan, name, out_dir, number=1):
    """Copie le fichier épars en <n>.part et écrit <n>.part.met ; renvoie les deux chemins."""
    if not plan.hashset:
        raise RuntimeError("hashset inconnu : impossible d'écrire un .part.met valide")
    gaps = store.have.missing(0, store.size)
    part = out_dir / f"{number:03d}.part"
    shutil.copyfile(store.bin, part)
    met = out_dir / f"{number:03d}.part.met"
    met.write_bytes(build_met(name, store.size, bytes.fromhex(store.bin.stem), plan.hashset, gaps, part.stat().st_mtime))
    return part, met


def parse_met(data):
    """Relit un .part.met (autotest) : (nom, taille, hash, hashset, gaps)."""
    r = P.Reader(data)
    assert r.u8() in (PARTFILE_VERSION, 0xE1, 0xE2)
    r.u32()
    fh = r.hash()
    hs = [r.hash() for _ in range(r.u16())]
    name = size = None
    starts, ends = {}, {}
    for _ in range(r.u32()):
        k, v = r.tag()
        if k == FT_FILENAME: name = v
        elif k == FT_FILESIZE: size = v
        elif isinstance(k, bytes) and k[0] == FT_GAPSTART: starts[int(k[1:])] = v
        elif isinstance(k, bytes) and k[0] == FT_GAPEND: ends[int(k[1:])] = v
    gaps = [(starts[i], ends[i]) for i in sorted(starts)]
    return name, size, fh, hs, gaps


if __name__ == "__main__":
    hs = [bytes([i]) * 16 for i in range(3)]
    met = build_met("a b.avi", 20_000_000, b"\7" * 16, hs, [(0, 10), (9_728_000, 12_000_000)])
    assert parse_met(met) == ("a b.avi", 20_000_000, b"\7" * 16, hs, [(0, 10), (9_728_000, 12_000_000)])
    print("partmet ok")
