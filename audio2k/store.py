"""Fichier épars + carte des octets acquis, persistés dans un dossier d'état."""
import json
import os
from pathlib import Path

from .intervals import Intervals


class Store:
    def __init__(self, state_dir, filehash, size):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.bin = self.dir / f"{filehash.hex().upper()}.bin"
        self.meta = self.dir / f"{filehash.hex().upper()}.json"
        self.have = Intervals()
        if self.meta.exists():
            self.have = Intervals(json.load(open(self.meta))["have"])
        if not self.bin.exists():
            with open(self.bin, "wb") as f:
                f.truncate(size)
        self._f = open(self.bin, "r+b")

    def write(self, start, data):
        self._f.seek(start)
        self._f.write(data)
        self.have.add(start, start + len(data))

    def read(self, start, end):
        if not self.have.contains(start, end):
            raise KeyError((start, end))
        self._f.seek(start)
        return self._f.read(end - start)

    def missing(self, ranges):
        out = []
        for a, b in ranges:
            out.extend(self.have.missing(a, b))
        return out

    def save(self):
        self._f.flush()
        tmp = self.meta.with_suffix(".tmp")
        json.dump({"size": self.size, "have": self.have.to_json()}, open(tmp, "w"))
        os.replace(tmp, self.meta)


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        s = Store(d, b"\1" * 16, 1000)
        s.write(10, b"abc"); s.write(13, b"def"); s.save()
        s2 = Store(d, b"\1" * 16, 1000)
        assert s2.read(10, 16) == b"abcdef" and s2.missing([(0, 20)]) == [(0, 10), (16, 20)]
        assert os.path.getsize(s2.bin) == 1000
    print("store ok")
