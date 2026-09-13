from copy import deepcopy
import time
import uuid
import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from reid.config import DEFAULTS
from reid.features.descriptor import MVSVG
from reid.provenance.crypto import public_text
from reid.provenance.events import make_packet


@pytest.fixture
def config():
    return deepcopy(DEFAULTS)


@pytest.fixture
def consortium():
    keys = {name: Ed25519PrivateKey.generate() for name in ("C1", "C2", "C3")}
    members = {name: {"id": name, "url": f"http://127.0.0.1:{9000+i}", "public_key": public_text(key.public_key())}
               for i, (name, key) in enumerate(keys.items())}
    return keys, members


def packet_for(key, node="C1", gid=None, vector=None, timestamp=None):
    if vector is None:
        vector = np.ones(MVSVG.dimension, np.float32)
    vector = vector / np.linalg.norm(vector)
    payload = {"vector": vector.tolist(), "plate": {"state": "UNKNOWN", "text": None, "confidence": 0.},
               "quality": .9, "position": [.5, .5], "scale": .1, "local": None}
    return make_packet(key, node, node + "-T00001", gid or "G-" + str(uuid.uuid4()), timestamp or time.time(),
                       "a" * 64, payload, {"decision": "NO_MATCH", "confidence": .5}, MVSVG().profile)
