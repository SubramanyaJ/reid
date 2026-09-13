from collections import defaultdict
import numpy as np


class HyperplaneLSH:
    def __init__(self, dimension, tables=8, bits=12, seed=17, multiprobe=True, **_):
        if dimension < 1 or tables < 1 or not 1 <= bits <= 32:
            raise ValueError("Invalid LSH dimensions")
        self.bits, self.multiprobe = bits, multiprobe
        self.planes = np.random.default_rng(seed).normal(size=(tables, bits, dimension)).astype(np.float32)
        self.buckets = [defaultdict(set) for _ in range(tables)]
        self.keys = {}

    def hashes(self, vector):
        v = np.asarray(vector, dtype=np.float32)
        if v.shape != (self.planes.shape[2],) or not np.isfinite(v).all():
            raise ValueError("Invalid vector")
        signs = (self.planes @ v) >= 0
        return [sum(int(bit) << j for j, bit in enumerate(row)) for row in signs]

    def insert(self, key, vector):
        self.remove(key)
        hashes = self.hashes(vector)
        self.keys[key] = hashes
        for bucket, h in zip(self.buckets, hashes):
            bucket[h].add(key)

    def remove(self, key):
        for bucket, h in zip(self.buckets, self.keys.pop(key, [])):
            bucket[h].discard(key)
            if not bucket[h]:
                del bucket[h]

    def query(self, vector):
        found = set()
        for bucket, h in zip(self.buckets, self.hashes(vector)):
            found.update(bucket.get(h, ()))
            if self.multiprobe:
                for bit in range(self.bits):
                    found.update(bucket.get(h ^ (1 << bit), ()))
        return found
