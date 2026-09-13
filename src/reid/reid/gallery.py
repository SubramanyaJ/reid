import threading
import uuid
import numpy as np
from ..features.descriptor import MVSVG
from ..features.local import LocalVerifier
from ..lsh.index import HyperplaneLSH
from ..plate.matching import aggregate, similarity
from .context import context_scores
from .fusion import fuse


class Gallery:
    def __init__(self, db, cfg):
        self.db, self.cfg = db, cfg
        self.lock = threading.RLock()
        self.identities = {item["global_id"]: item for item in db.identities()}
        self.index = HyperplaneLSH(MVSVG.dimension, **cfg["lsh"])
        self.entries = {}
        for identity in self.identities.values():
            self._index_identity(identity)

    def _index_identity(self, identity):
        gid = identity["global_id"]
        for key in [key for key in self.entries if key[0] == gid]:
            self.index.remove(key)
            del self.entries[key]
        for entry in identity["visual_gallery"]:
            key = (gid, entry["event_id"])
            self.entries[key] = np.asarray(entry["vector"], np.float32)
            self.index.insert(key, self.entries[key])

    def apply(self, packet):
        event, payload = packet["transaction"]["event"], packet["payload"]
        applied = "applied:" + event["event_id"]
        with self.lock:
            if self.db.get(applied, False):
                return
            gid = event["global_identity_id"]
            identity = self.identities.get(gid, {"global_id": gid, "visual_gallery": [], "plate_history": [],
                "camera_history": [], "observation_history": [], "last_seen": 0., "last_camera": None,
                "last_position": [0., 0.], "last_scale": 0.})
            entry = {"event_id": event["event_id"], "vector": payload["vector"], "quality": payload["quality"],
                     "local": payload["local"], "timestamp": event["timestamp"], "node_id": event["node_id"]}
            gallery = identity["visual_gallery"]
            vector = np.asarray(entry["vector"])
            similarities = [float(np.dot(vector, old["vector"])) for old in gallery]
            if not gallery or max(similarities) < .985:
                gallery.append(entry)
            else:
                index = int(np.argmax(similarities))
                if entry["quality"] > gallery[index]["quality"]:
                    gallery[index] = entry
            # Greedy diversity and quality retention with deterministic tie breaks.
            if len(gallery) > self.cfg["reid"]["gallery_size"]:
                pool = sorted(gallery, key=lambda x: (-x["quality"], x["event_id"]))
                selected = [pool.pop(0)]
                while pool and len(selected) < self.cfg["reid"]["gallery_size"]:
                    best = max(pool, key=lambda e: .55 * e["quality"] + .45 *
                               (1 - max(float(np.dot(e["vector"], old["vector"])) for old in selected)))
                    selected.append(best)
                    pool.remove(best)
                identity["visual_gallery"] = selected
            plate = payload["plate"]
            if plate["state"] == "SUPPORTED":
                identity["plate_history"].append([plate["text"], plate["confidence"]])
                identity["plate_history"] = identity["plate_history"][-24:]
            identity["camera_history"].append({"camera": event["camera_id"], "timestamp": event["timestamp"]})
            identity["camera_history"] = sorted(identity["camera_history"], key=lambda x: (x["timestamp"], x["camera"]))[-64:]
            identity["observation_history"] = (identity["observation_history"] + [event["event_id"]])[-64:]
            if event["timestamp"] >= identity["last_seen"]:
                identity.update(last_seen=event["timestamp"], last_camera=event["camera_id"],
                                last_position=payload["position"], last_scale=payload["scale"],
                                preview={"event_id": event["event_id"], "node_id": event["node_id"]})
            self.identities[gid] = identity
            # Commit summary and applied marker together, surviving interrupted replay.
            from ..provenance.crypto import canonical
            with self.db.lock, self.db.conn:
                self.db.conn.execute("INSERT OR REPLACE INTO identities VALUES (?,?)", (gid, canonical(identity).decode()))
                self.db.conn.execute("INSERT OR REPLACE INTO operational VALUES (?,?)", (applied, "true"))
            self._index_identity(identity)

    def decide(self, payload, camera_id, timestamp, excluded=None, bound_id=None):
        with self.lock:
            vector = np.asarray(payload["vector"], np.float32)
            keys = set(self.entries) if self.cfg["lsh"]["brute_force"] else self.index.query(vector)
            candidates = {key[0] for key in keys}
            plate = payload["plate"]
            # Independent plate candidate generation can recover a visual LSH miss.
            if plate["state"] == "SUPPORTED":
                for gid, identity in self.identities.items():
                    if any(similarity(plate["text"], text) >= .75 for text, _ in identity["plate_history"]):
                        candidates.add(gid)
            if bound_id:
                candidates = {bound_id} if bound_id in self.identities else set()
            else:
                candidates.difference_update(excluded or set())
            ranked = []
            for gid in sorted(candidates):
                identity = self.identities[gid]
                if not identity["visual_gallery"]:
                    continue
                entry = max(identity["visual_gallery"], key=lambda entry: np.dot(vector, entry["vector"]))
                visual = float(np.clip(np.dot(vector, entry["vector"]), 0, 1))
                canonical_plate = aggregate(identity["plate_history"], min_readings=1)
                plate_score, plate_confidence = None, 0.
                if plate["state"] == canonical_plate["state"] == "SUPPORTED":
                    plate_score = similarity(plate["text"], canonical_plate["text"])
                    plate_confidence = min(plate["confidence"], canonical_plate["confidence"])
                temporal, spatial = context_scores(identity, camera_id, timestamp, payload["position"],
                                                   payload["scale"], self.cfg["context"])
                local = LocalVerifier.compare(payload["local"], entry["local"])
                evidence = fuse(visual, plate_score, temporal, spatial, self.cfg["reid"], plate_confidence, local)
                origin = entry.get("node_id")
                if origin is None:
                    old_packet = self.db.packet(entry["event_id"])
                    origin = old_packet["transaction"]["event"]["node_id"] if old_packet else None
                evidence.update(matched_global_id=gid, matched_event_id=entry["event_id"], matched_node_id=origin,
                                identity_preview=identity.get("preview"))
                ranked.append((gid, evidence))
            # Reject contradictions before selecting the best viable candidate.
            ranked.sort(key=lambda pair: (pair[1]["decision"] != "NO_MATCH", pair[1]["confidence"]), reverse=True)
            if ranked:
                gid, result = ranked[0]
                result["candidate_count"] = len(candidates)
                if len(ranked) > 1 and ranked[1][1]["decision"] != "NO_MATCH" and result["confidence"] - ranked[1][1]["confidence"] < self.cfg["reid"]["margin"]:
                    result["decision"] = "UNCERTAIN"
                if result["decision"] == "MATCH":
                    return gid, result
                if result["decision"] == "UNCERTAIN" or bound_id:
                    return None, result
                result["confidence"] = 1 - result["confidence"]
            else:
                result = {"decision": "NO_MATCH", "confidence": .5, "visual_score": None,
                          "plate_score": None, "temporal_score": None, "spatial_score": None, "candidate_count": 0}
                if bound_id:
                    return None, {**result, "decision": "UNCERTAIN"}
            return "G-" + str(uuid.uuid4()), result
