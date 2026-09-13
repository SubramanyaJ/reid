"""Offline data adapters. Synthetic fixtures are explicitly NOT real vehicle evidence."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
import cv2
import numpy as np
from ..features.descriptor import MVSVG
from .measures import distribution


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize(matrix):
    array = np.array(matrix, dtype=np.float32, order="C", copy=True)
    if array.ndim != 2 or not all(array.shape) or not np.isfinite(array).all():
        raise ValueError("Descriptors must be nonempty, finite 2-D arrays")
    with np.errstate(over="ignore"):
        norms = np.linalg.norm(array, axis=1, keepdims=True)
    if not np.isfinite(norms).all():
        raise ValueError("Descriptor norms overflow float32; rescale the supplied data")
    if np.any(norms < 1e-9):
        raise ValueError("Zero descriptors cannot be evaluated")
    return array / norms


def validate(data):
    data["vectors"], data["queries"] = normalize(data["vectors"]), normalize(data["queries"])
    n, d = data["vectors"].shape
    q = len(data["queries"])
    if data["queries"].shape[1] != d:
        raise ValueError("Gallery and query dimensions disagree")
    for a, b in [("labels", "query_labels"), ("camera_ids", "query_camera_ids"), ("sample_ids", "query_sample_ids")]:
        if (a in data) != (b in data):
            raise ValueError(f"Supply both {a} and {b}, or neither")
        if a in data:
            for key, count in [(a, n), (b, q)]:
                array = np.asarray(data[key])
                if array.shape != (count,) or array.dtype.kind not in "iuUS":
                    raise ValueError(f"{key} must be aligned 1-D integer or fixed-width string identifiers")
                data[key] = array.astype(str)
    for key in ("sample_ids", "query_sample_ids"):
        if key in data and len(set(data[key])) != len(data[key]):
            raise ValueError(f"{key} must be unique within its split")
    return data


def load_npz(path, limit):
    with np.load(path, allow_pickle=False) as supplied:
        data = {key: supplied[key] for key in ("vectors", "queries", "labels", "query_labels",
                "camera_ids", "query_camera_ids", "sample_ids", "query_sample_ids") if key in supplied}
    if "vectors" not in data or "queries" not in data:
        raise ValueError("NPZ needs vectors and queries")
    data = validate(data)  # Validate ALL rows before truncating queries.
    for key in ("queries", "query_labels", "query_camera_ids", "query_sample_ids"):
        if key in data:
            data[key] = data[key][:limit]
    data["name"] = "supplied_descriptors"
    data["origin"] = {"kind": "user_supplied", "path": str(Path(path).resolve()), "sha256": file_digest(path),
                      "descriptor_profile": "external; provide extraction details with the publication"}
    return data


def gaussian(size, queries, seed, dimension=602):
    rng = np.random.default_rng(seed)
    groups = max(5, size // 5)
    centers = normalize(rng.normal(size=(groups, dimension)))
    labels = np.arange(size) % groups
    targets = rng.integers(0, groups, queries)
    return validate({"name": "gaussian_clusters", "vectors": centers[labels] + rng.normal(0, .018, (size, dimension)),
                     "queries": centers[targets] + rng.normal(0, .018, (queries, dimension)),
                     "labels": labels, "query_labels": targets,
                     "origin": {"kind": "synthetic_vectors", "seed": seed, "noise_std": .018,
                                "identities": groups, "warning": "Isotropic index diagnostic, not image Re-ID accuracy"}})


def render_crop(identity, view, seed):
    """Analytic silhouettes and color/stripe patterns with held-out photometric perturbations."""
    rng = np.random.default_rng(seed + identity * 1009)
    image = np.full((80, 128, 3), 32, np.uint8)
    mask = np.zeros((80, 128), np.uint8)
    shift = (-4, 0, 4, 7)[view % 4]
    polygon = np.array([[6, 40], [27+shift, 33], [40+shift, 15], [86+shift, 15],
                        [105+shift, 34], [121, 41], [121, 63], [6, 63]], np.int32)
    color = rng.integers(45, 225, size=3).tolist()
    cv2.fillPoly(mask, [polygon], 255)
    cv2.fillPoly(image, [polygon], color)
    for x in (30, 98):
        cv2.circle(mask, (x, 62), 10, 255, -1)
        cv2.circle(image, (x, 62), 10, (25, 25, 25), -1)
        cv2.circle(image, (x, 62), 4, (170, 170, 170), -1)
    cv2.rectangle(image, (45+shift, 21), (83+shift, 33), (130, 145, 160), -1)
    for _ in range(7):
        x, y = int(rng.integers(12, 114)), int(rng.integers(40, 59))
        cv2.line(image, (x, y), (min(120, x+int(rng.integers(4, 17))), y), rng.integers(20, 240, 3).tolist(), 2)
    noise = np.random.default_rng(seed + identity * 1009 + view * 9173).normal(0, 2, image.shape)
    gain = (.93, 1., 1.07, .83)[view % 4]
    image = np.uint8(np.clip(image.astype(float) * gain + noise, 0, 255))
    if view % 4 == 3:
        image = cv2.GaussianBlur(image, (3, 3), .6)
    return image, mask


def rendered(identities, seed):
    extractor = MVSVG()
    gallery, queries, labels, targets, durations = [], [], [], [], []
    for identity in range(identities + max(2, identities // 4)):
        for view in (range(4) if identity < identities else [3]):
            crop, mask = render_crop(identity, view, seed)
            start = perf_counter()
            vector = extractor.extract(crop, mask)
            durations.append((perf_counter() - start) * 1000)
            if view < 3:
                gallery.append(vector)
                labels.append(identity)
            else:
                queries.append(vector)
                targets.append(identity)
    return validate({"name": "rendered_mvsvg", "vectors": gallery, "queries": queries,
                     "labels": labels, "query_labels": targets,
                     "origin": {"kind": "synthetic_images", "seed": seed, "identities": identities,
                                "unknown_identities": max(2, identities // 4), "gallery_views": [0, 1, 2],
                                "query_view": 3, "descriptor_profile": extractor.profile,
                                "extraction_ms": distribution(durations),
                                "warning": "Drawn fixtures; no evidence of real cross-camera generalization"}})


def load_crops(path, limit):
    path = Path(path).resolve()
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    extractor, fingerprints, durations = MVSVG(), [], []
    data = {"name": "supplied_crops"}
    split_hashes = []
    for split, vector_key, prefix in [("gallery", "vectors", ""), ("queries", "queries", "query_")]:
        rows = document[split]
        if not rows:
            raise ValueError("Crop manifest needs nonempty gallery and queries")
        vectors, labels, cameras, ids, hashes = [], [], [], [], set()
        for row in rows:
            image_path = (path.parent / row["image"]).resolve()
            crop = cv2.imread(str(image_path))
            if crop is None:
                raise ValueError(f"Cannot read crop: {image_path}")
            mask_path = (path.parent / row["mask"]).resolve() if row.get("mask") else None
            mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE) if mask_path else None
            if mask_path and (mask is None or mask.shape != crop.shape[:2]):
                raise ValueError(f"Unreadable/misaligned mask: {mask_path}")
            digest = file_digest(image_path)
            hashes.add(digest)
            fingerprints.append({"image": str(image_path), "sha256": digest,
                                 "mask_sha256": file_digest(mask_path) if mask_path else None})
            start = perf_counter()
            vectors.append(extractor.extract(crop, mask))
            durations.append((perf_counter()-start)*1000)
            labels.append(str(row["identity"]))
            cameras.append(str(row["camera"]))
            ids.append(str(row["sample_id"]))
        split_hashes.append(hashes)
        data.update({vector_key: vectors, prefix+"labels": labels, prefix+"camera_ids": cameras,
                     prefix+"sample_ids": ids})
    if split_hashes[0] & split_hashes[1]:
        raise ValueError("Identical image bytes occur in gallery and query splits; use held-out images")
    data = validate(data)
    for key in ("queries", "query_labels", "query_camera_ids", "query_sample_ids"):
        data[key] = data[key][:limit]
    data["origin"] = {"kind": "user_supplied_images", "manifest_sha256": file_digest(path),
                      "descriptor_profile": extractor.profile, "inputs": fingerprints,
                      "extraction_ms": distribution(durations)}
    return data
