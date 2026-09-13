from copy import deepcopy
from pathlib import Path
import math
import re
import yaml

DEFAULTS = {
    "node": {"id": "C1", "host": "0.0.0.0", "port": 9000,
             "private_key": "keys/C1.pem", "database": "../data/C1/state.sqlite"},
    "members": [],
    "camera": {"source": None, "width": 960, "height": 540, "fps": 20,
               "warmup_seconds": 4, "reconnect_seconds": 3},
    "detection": {"method": "MOG2", "history": 500, "variance_threshold": 24,
                  "min_component_fraction": .0008, "min_core_radius_fraction": .015,
                  "max_hole_fraction": .001, "grabcut": False},
    "tracking": {"min_hits": 6, "max_missed": 14, "min_vehicle_fraction": .008,
                 "max_vehicle_fraction": .40, "min_width_fraction": .065, "min_height_fraction": .10,
                 "max_width_fraction": .80, "max_height_fraction": .85,
                 "min_foreground_fraction": .004, "min_fill_ratio": .30,
                 "min_aspect_ratio": .4, "max_aspect_ratio": 3.5, "min_motion_consistency": .4},
    "features": {"weights": {"color": 1., "texture": .65, "structure": .7, "statistics": .4},
                 "local_verifier": "ORB"},
    "lsh": {"tables": 8, "bits": 12, "seed": 17, "multiprobe": True, "brute_force": False},
    "reid": {"min_quality": .42, "observation_interval": 1.5, "gallery_size": 8,
             "match_threshold": .82, "uncertain_threshold": .68, "visual_floor": .60, "margin": .035},
    "plate": {"min_confidence": .58, "min_readings": 2, "template_directory": None},
    "context": {"transitions": {}},
    "consensus": {"interval_seconds": 2, "max_transactions": 64},
    "network": {"timeout_seconds": 3, "max_body_bytes": 2000000, "sync_batch": 32},
    "visualization": {"thumbnail_limit": 300, "thumbnail_size": 240},
}


def merge(base, extra):
    result = deepcopy(base)
    for key, value in extra.items():
        if key not in result:
            raise ValueError(f"Unknown configuration key: {key}")
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result[key], dict) and result[key] else value
    return result


def load_config(path):
    path = Path(path).resolve()
    cfg = merge(DEFAULTS, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    cfg["camera"]["fps"] = min(24., float(cfg["camera"]["fps"]))
    if not 3 <= cfg["camera"]["warmup_seconds"] <= 5:
        raise ValueError("camera.warmup_seconds must be between 3 and 5")
    for section, key in [("camera", "fps"), ("camera", "width"), ("camera", "height"),
                         ("camera", "reconnect_seconds"), ("tracking", "min_hits"),
                         ("tracking", "max_missed"), ("lsh", "tables"), ("reid", "gallery_size"),
                         ("reid", "observation_interval"), ("consensus", "interval_seconds"),
                         ("network", "timeout_seconds"), ("network", "sync_batch")]:
        value = cfg[section][key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{section}.{key} must be positive and finite")
    if not 1 <= cfg["lsh"]["bits"] <= 32:
        raise ValueError("LSH bits must be 1..32")
    if not 1 <= cfg["consensus"]["max_transactions"] <= 128:
        raise ValueError("Blocks support 1..128 transactions")
    if cfg["detection"]["method"] not in ("MOG2", "KNN"):
        raise ValueError("detection.method must be MOG2 or KNN")
    if cfg["features"]["local_verifier"] not in ("ORB", "SIFT", "NONE"):
        raise ValueError("local_verifier must be ORB, SIFT, or NONE")
    for section, names in {
        "detection": ["min_component_fraction", "min_core_radius_fraction", "max_hole_fraction"],
        "tracking": ["min_vehicle_fraction", "max_vehicle_fraction", "min_width_fraction", "min_height_fraction",
                     "max_width_fraction", "max_height_fraction", "min_foreground_fraction",
                     "min_fill_ratio", "min_motion_consistency"],
    }.items():
        for name in names:
            value = cfg[section][name]
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{section}.{name} must be a finite fraction in [0,1]")
    if not 0 < cfg["tracking"]["min_aspect_ratio"] <= cfg["tracking"]["max_aspect_ratio"] <= 10:
        raise ValueError("Invalid tracking aspect ratio range")
    if cfg["tracking"]["min_vehicle_fraction"] >= cfg["tracking"]["max_vehicle_fraction"]:
        raise ValueError("Minimum object area must be smaller than maximum object area")
    for dimension in ("width", "height"):
        if cfg["tracking"][f"min_{dimension}_fraction"] >= cfg["tracking"][f"max_{dimension}_fraction"]:
            raise ValueError(f"Minimum object {dimension} must be smaller than maximum object {dimension}")
    for key, low, high in [("thumbnail_limit", 1, 5000), ("thumbnail_size", 64, 512)]:
        value = cfg["visualization"][key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"visualization.{key} must be an integer in [{low},{high}]")
    weights = cfg["features"]["weights"]
    if any(not math.isfinite(v) or v < 0 for v in weights.values()) or sum(weights.values()) <= 0:
        raise ValueError("Feature weights must be finite, nonnegative, and not all zero")
    if not 0 <= cfg["reid"]["uncertain_threshold"] < cfg["reid"]["match_threshold"] <= 1:
        raise ValueError("Invalid fusion thresholds")
    members = cfg["members"]
    ids = [m["id"] for m in members]
    if any(not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", node) for node in ids):
        raise ValueError("Invalid member ID")
    if not members or len(set(ids)) != len(ids) or cfg["node"]["id"] not in ids:
        raise ValueError("Membership must be unique and include this node. Run the init command first.")
    if len({m["public_key"] for m in members}) != len(members):
        raise ValueError("Each member needs a distinct public key")
    if len({m["url"] for m in members}) != len(members):
        raise ValueError("Each peer needs a distinct network address")
    if type(cfg["node"]["port"]) is not int or not 1 <= cfg["node"]["port"] <= 65535:
        raise ValueError("node.port must be 1..65535")
    for member in members:
        if not member["url"].startswith(("http://", "https://")):
            raise ValueError("Peer URLs must use HTTP(S)")
    for section, key in [("node", "private_key"), ("node", "database"), ("plate", "template_directory")]:
        if cfg[section][key] is not None:
            cfg[section][key] = str((path.parent / cfg[section][key]).resolve())
    return cfg
