"""Ensemble d'intervalles [a, b) disjoints et triés, sérialisable en JSON."""
import bisect


class Intervals:
    def __init__(self, items=()):
        self._s = []
        for a, b in items:
            self.add(a, b)

    def __len__(self):
        return len(self._s)

    def __iter__(self):
        return iter(self._s)

    def total(self):
        return sum(b - a for a, b in self._s)

    def add(self, a, b):
        if b <= a:
            return
        i = bisect.bisect_left(self._s, (a, a))
        if i and self._s[i - 1][1] >= a:
            i -= 1
        j = i
        while j < len(self._s) and self._s[j][0] <= b:
            a, b = min(a, self._s[j][0]), max(b, self._s[j][1])
            j += 1
        self._s[i:j] = [(a, b)]

    def contains(self, a, b):
        i = bisect.bisect_right(self._s, (a, float("inf"))) - 1
        return i >= 0 and self._s[i][0] <= a and self._s[i][1] >= b

    def missing(self, a, b):
        """Sous-intervalles de [a, b) non couverts."""
        out, cur = [], a
        i = max(bisect.bisect_right(self._s, (a, float("inf"))) - 1, 0)
        while i < len(self._s) and self._s[i][0] < b:
            x, y = self._s[i]
            if x > cur:
                out.append((cur, x))
            cur = max(cur, y)
            i += 1
        if cur < b:
            out.append((cur, b))
        return out

    def to_json(self):
        return [list(t) for t in self._s]


if __name__ == "__main__":
    iv = Intervals([(10, 20), (30, 40)])
    iv.add(20, 30)
    assert list(iv) == [(10, 40)]
    iv.add(50, 60)
    assert iv.missing(0, 70) == [(0, 10), (40, 50), (60, 70)]
    assert iv.missing(12, 18) == [] and iv.contains(12, 18) and not iv.contains(35, 55)
    iv.add(5, 12)
    assert list(iv) == [(5, 40), (50, 60)]
    print("intervals ok")
