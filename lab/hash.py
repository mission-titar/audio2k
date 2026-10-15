#!/usr/bin/env python3
"""Hash ed2k d'un fichier (MD4 pur Python, stdlib seule) et lien ed2k://."""
import os
import struct
import sys

PARTSIZE = 9728000
_M = 0xFFFFFFFF


def _rotl(x, n):
    return ((x << n) | (x >> (32 - n))) & _M


def md4(data: bytes) -> bytes:
    n = len(data)
    data += b"\x80" + b"\x00" * ((55 - n) % 64) + struct.pack("<Q", n * 8)
    a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for off in range(0, len(data), 64):
        x = struct.unpack_from("<16I", data, off)
        aa, bb, cc, dd = a, b, c, d
        for i in (0, 4, 8, 12):
            a = _rotl((a + ((b & c) | (~b & d)) + x[i]) & _M, 3)
            d = _rotl((d + ((a & b) | (~a & c)) + x[i + 1]) & _M, 7)
            c = _rotl((c + ((d & a) | (~d & b)) + x[i + 2]) & _M, 11)
            b = _rotl((b + ((c & d) | (~c & a)) + x[i + 3]) & _M, 19)
        for i in (0, 1, 2, 3):
            a = _rotl((a + ((b & c) | (b & d) | (c & d)) + x[i] + 0x5A827999) & _M, 3)
            d = _rotl((d + ((a & b) | (a & c) | (b & c)) + x[i + 4] + 0x5A827999) & _M, 5)
            c = _rotl((c + ((d & a) | (d & b) | (a & b)) + x[i + 8] + 0x5A827999) & _M, 9)
            b = _rotl((b + ((c & d) | (c & a) | (d & a)) + x[i + 12] + 0x5A827999) & _M, 13)
        for i in (0, 2, 1, 3):
            a = _rotl((a + (b ^ c ^ d) + x[i] + 0x6ED9EBA1) & _M, 3)
            d = _rotl((d + (a ^ b ^ c) + x[i + 8] + 0x6ED9EBA1) & _M, 9)
            c = _rotl((c + (d ^ a ^ b) + x[i + 4] + 0x6ED9EBA1) & _M, 11)
            b = _rotl((b + (c ^ d ^ a) + x[i + 12] + 0x6ED9EBA1) & _M, 15)
        a, b, c, d = (a + aa) & _M, (b + bb) & _M, (c + cc) & _M, (d + dd) & _M
    return struct.pack("<4I", a, b, c, d)


def ed2k_hash(path) -> bytes:
    parts = []
    with open(path, "rb") as f:
        while chunk := f.read(PARTSIZE):
            parts.append(md4(chunk))
    if os.path.getsize(path) % PARTSIZE == 0:
        parts.append(md4(b""))  # taille multiple exacte : part vide en plus
    return parts[0] if len(parts) == 1 else md4(b"".join(parts))


def ed2k_link(path) -> str:
    name = os.path.basename(path).replace("|", "%7C")
    return f"ed2k://|file|{name}|{os.path.getsize(path)}|{ed2k_hash(path).hex().upper()}|/"


# Vecteurs RFC 1320.
assert md4(b"").hex() == "31d6cfe0d16ae931b73c59d7e0c089c0"
assert md4(b"abc").hex() == "a448017aaf21d8525fc10ae87aa6729d"
assert md4(b"1234567890" * 8).hex() == "e33b4ddc9c38f2199c3e7b164fcc0536"

if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(ed2k_link(p))
