import math
import uuid
import numpy as np
from .crypto import bytes_hash, digest, sign, verify
from ..features.descriptor import MVSVG

EVENT_FIELDS = {"event_id", "timestamp", "node_id", "camera_id", "local_track_id", "global_identity_id",
                "visual_score", "plate_score", "temporal_score", "spatial_score", "decision", "decision_confidence",
                "observation_hash", "feature_hash", "plate_hash", "payload_hash", "descriptor_profile"}


def feature_hash(vector):
    return bytes_hash(np.asarray(vector, dtype="<f4").tobytes())


def make_packet(key, node_id, local_id, global_id, timestamp, observation_hash, payload, evidence, profile):
    event = {"event_id": str(uuid.uuid4()), "timestamp": timestamp, "node_id": node_id,
             "camera_id": node_id, "local_track_id": local_id, "global_identity_id": global_id,
             **{k: evidence.get(k) for k in ("visual_score", "plate_score", "temporal_score", "spatial_score")},
             "decision": evidence["decision"], "decision_confidence": evidence["confidence"],
             "observation_hash": observation_hash, "feature_hash": feature_hash(payload["vector"]),
             "plate_hash": digest(payload["plate"]), "payload_hash": digest(payload), "descriptor_profile": profile}
    return {"transaction": {"event": event, "signature": sign(key, event)}, "payload": payload}


def validate_transaction(tx, members):
    try:
        if set(tx) != {"event", "signature"}:
            return False
        event = tx["event"]
        if set(event) != EVENT_FIELDS or event["node_id"] not in members:
            return False
        uuid.UUID(event["event_id"])
        uuid.UUID(event["global_identity_id"].removeprefix("G-"))
        if not event["global_identity_id"].startswith("G-"):
            return False
        if event["camera_id"] != event["node_id"] or not event["local_track_id"].startswith(event["camera_id"] + "-T"):
            return False
        if len(event["local_track_id"]) > 96 or event["decision"] not in ("MATCH", "NO_MATCH", "UNCERTAIN"):
            return False
        if not isinstance(event["timestamp"], (float, int)) or not math.isfinite(event["timestamp"]) or event["timestamp"] <= 0:
            return False
        for name in ("visual_score", "plate_score", "temporal_score", "spatial_score", "decision_confidence"):
            value = event[name]
            if value is None and name != "decision_confidence":
                continue
            if not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
                return False
        for name in ("observation_hash", "feature_hash", "plate_hash", "payload_hash", "descriptor_profile"):
            value = event[name]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                return False
        return verify(members[event["node_id"]]["public_key"], event, tx["signature"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def validate_packet(packet, members, profile):
    try:
        if set(packet) != {"transaction", "payload"} or not validate_transaction(packet["transaction"], members):
            return False
        event, payload = packet["transaction"]["event"], packet["payload"]
        if set(payload) != {"vector", "plate", "quality", "position", "scale", "local"}:
            return False
        vector = np.asarray(payload["vector"], np.float32)
        if vector.shape != (MVSVG.dimension,) or not np.isfinite(vector).all() or not .999 <= np.linalg.norm(vector) <= 1.001:
            return False
        if event["descriptor_profile"] != profile or event["feature_hash"] != feature_hash(vector):
            return False
        if event["payload_hash"] != digest(payload) or event["plate_hash"] != digest(payload["plate"]):
            return False
        if not 0 <= payload["quality"] <= 1 or not 0 <= payload["scale"] <= 1:
            return False
        if len(payload["position"]) != 2 or any(not 0 <= v <= 1 for v in payload["position"]):
            return False
        plate = payload["plate"]
        if set(plate) != {"state", "text", "confidence"} or plate["state"] not in ("UNKNOWN", "SUPPORTED", "CONFLICTING"):
            return False
        if not 0 <= plate["confidence"] <= 1:
            return False
        if plate["state"] == "SUPPORTED":
            text = plate["text"]
            if not isinstance(text, str) or not 4 <= len(text) <= 16 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for c in text):
                return False
        elif plate["text"] is not None:
            return False
        local = payload["local"]
        if local:
            if set(local) != {"method", "points", "descriptors"} or local["method"] not in ("ORB", "SIFT"):
                return False
            points, desc = np.asarray(local["points"]), np.asarray(local["descriptors"])
            width = 32 if local["method"] == "ORB" else 128
            if points.shape != (len(points), 2) or not 6 <= len(points) <= 160 or desc.shape != (len(points), width):
                return False
            if not np.isfinite(points).all() or not np.isfinite(desc).all() or np.any(points < 0) or np.any(points > 1):
                return False
            if np.any(desc < 0) or np.any(desc > (255 if width == 32 else 1024)):
                return False
        return True
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False
