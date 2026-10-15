"""MD4 pur Python (RFC 1320), avec hashlib si OpenSSL l'expose encore."""
import hashlib
import struct


def _f(x, y, z): return (x & y) | (~x & z)
def _g(x, y, z): return (x & y) | (x & z) | (y & z)
def _h(x, y, z): return x ^ y ^ z
def _rol(x, n): return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


class _PyMD4:
    digest_size = 16
    block_size = 64

    def __init__(self, data=b""):
        self._state = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
        self._buf = b""
        self._len = 0
        if data:
            self.update(data)

    def update(self, data):
        self._len += len(data)
        self._buf += data
        n = len(self._buf) // 64 * 64
        for i in range(0, n, 64):
            self._block(self._buf[i:i + 64])
        self._buf = self._buf[n:]

    def _block(self, blk):
        x = struct.unpack("<16I", blk)
        a, b, c, d = self._state
        for i, s in zip(range(16), [3, 7, 11, 19] * 4):
            a, b, c, d = d, (_rol((a + _f(b, c, d) + x[i]) & 0xFFFFFFFF, s)), b, c
        for i, s in zip([0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15], [3, 5, 9, 13] * 4):
            a, b, c, d = d, (_rol((a + _g(b, c, d) + x[i] + 0x5A827999) & 0xFFFFFFFF, s)), b, c
        for i, s in zip([0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15], [3, 9, 11, 15] * 4):
            a, b, c, d = d, (_rol((a + _h(b, c, d) + x[i] + 0x6ED9EBA1) & 0xFFFFFFFF, s)), b, c
        self._state = [(v + w) & 0xFFFFFFFF for v, w in zip(self._state, (a, b, c, d))]

    def digest(self):
        clone = _PyMD4()
        clone._state, clone._buf, clone._len = list(self._state), self._buf, self._len
        pad = b"\x80" + b"\x00" * ((55 - clone._len) % 64) + struct.pack("<Q", clone._len * 8)
        clone.update(pad)
        return struct.pack("<4I", *clone._state)

    def hexdigest(self):
        return self.digest().hex()


def md4(data=b""):
    try:
        return hashlib.new("md4", data)
    except ValueError:
        return _PyMD4(data)


PARTSIZE = 9_728_000


def ed2k_hash(fileobj, size):
    """Hash ed2k (hex) + liste des hashes de parts, lus depuis fileobj."""
    parts = []
    remaining = size
    while remaining > 0:
        h = md4()
        todo = min(PARTSIZE, remaining)
        while todo:
            chunk = fileobj.read(min(todo, 1 << 20))
            h.update(chunk)
            todo -= len(chunk)
        parts.append(h.digest())
        remaining -= min(PARTSIZE, remaining)
    if size % PARTSIZE == 0:
        parts.append(md4().digest())
    root = parts[0] if len(parts) == 1 else md4(b"".join(parts)).digest()
    return root.hex().upper(), parts


if __name__ == "__main__":
    assert _PyMD4(b"").hexdigest() == "31d6cfe0d16ae931b73c59d7e0c089c0"
    assert _PyMD4(b"abc").hexdigest() == "a448017aaf21d8525fc10ae87aa6729d"
    assert _PyMD4(b"12345678901234567890123456789012345678901234567890123456789012345678901234567890").hexdigest() == "e33b4ddc9c38f2199c3e7b164fcc0536"
    print("md4 ok")
