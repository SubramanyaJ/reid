"""MVSV-G: masked, coarse, multi-view statistical vehicle descriptor (handcrafted)."""
import hashlib
import json
import cv2
import numpy as np


def unit(values):
    a = np.asarray(values, dtype=np.float32).ravel()
    return a / max(float(np.linalg.norm(a)), 1e-12)


def histogram(values, bins, limits):
    counts = np.histogram(values, bins=bins, range=limits)[0].astype(np.float32)
    return np.sqrt(counts / max(float(counts.sum()), 1.))


def letterbox(crop, mask, width=128, height=80):
    scale = min(width / crop.shape[1], height / crop.shape[0])
    w, h = max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))
    image = np.zeros((height, width, 3), np.uint8)
    valid = np.zeros((height, width), np.uint8)
    x, y = (width - w) // 2, (height - h) // 2
    image[y:y+h, x:x+w] = cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA)
    valid[y:y+h, x:x+w] = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    return image, valid > 0


def uniform_lbp(gray, radius):
    yy, xx = np.indices(gray.shape, dtype=np.float32)
    neighbors = []
    for k in range(8):
        angle = 2 * np.pi * k / 8
        map_x = (xx + radius * np.cos(angle)).astype(np.float32)
        map_y = (yy - radius * np.sin(angle)).astype(np.float32)
        neighbors.append(cv2.remap(gray, map_x, map_y,
                                  cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT))
    neighbors = np.stack(neighbors)
    bits = neighbors >= gray
    changes = np.count_nonzero(bits != np.roll(bits, 1, axis=0), axis=0)
    labels = np.where(changes <= 2, bits.sum(axis=0), 9)
    return labels, neighbors.var(axis=0)


class MVSVG:
    dimension = 602

    def __init__(self, weights=None):
        self.weights = weights or {"color": 1., "texture": .65, "structure": .7, "statistics": .4}
        self.profile = hashlib.sha256(json.dumps({"version": "MVSV-G/1", "weights": self.weights},
                                                sort_keys=True).encode()).hexdigest()

    def extract(self, crop, mask=None):
        if crop is None or crop.size == 0:
            raise ValueError("Empty crop")
        if mask is None:
            mask = np.full(crop.shape[:2], 255, np.uint8)
        image, valid = letterbox(crop, mask)
        if valid.sum() < 10:
            raise ValueError("Insufficient foreground")
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
        color = []
        # Whole object and two broad horizontal bands: 3 * (28 + 24) = 156.
        for ya, yb in [(0, 80), (0, 40), (40, 80)]:
            m = valid[ya:yb]
            for channel, bins, high in [(0, 12, 180), (1, 8, 256), (2, 8, 256)]:
                color.extend(histogram(hsv[ya:yb, :, channel][m], bins, (0, high)))
            for channel in range(3):
                color.extend(histogram(lab[ya:yb, :, channel][m], 8, (0, 256)))
        # 2x2 coarse Lab mean/std: 24.
        for ya in (0, 40):
            for xa in (0, 64):
                values = lab[ya:ya+40, xa:xa+64][valid[ya:ya+40, xa:xa+64]].astype(float) / 255
                color.extend(np.r_[values.mean(0), values.std(0)] if len(values) else np.zeros(6))
        texture = []
        interior = cv2.erode(valid.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        if interior.sum() < 10:
            interior = valid
        # Three radii, 10-bin uniform LBP and 8-bin log local variance: 54.
        for radius in (1, 2, 3):
            labels, variance = uniform_lbp(gray, radius)
            texture.extend(histogram(labels[interior], 10, (0, 10)))
            texture.extend(histogram(np.log1p(variance[interior]), 8, (0, 10)))
        gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        magnitude, angle = cv2.cartToPolar(gx, gy, angleInDegrees=True)
        orientation = np.minimum(8, ((angle % 180) / 20).astype(int))
        structure = []
        # Compact 4x8 cells, unsigned nine-bin gradient histograms: 288.
        for ya in range(0, 80, 20):
            for xa in range(0, 128, 16):
                region = np.s_[ya:ya+20, xa:xa+16]
                m = interior[region]
                hist = np.bincount(orientation[region][m], weights=magnitude[region][m], minlength=9)
                structure.extend(unit(hist))
        edges = cv2.Canny(gray.astype(np.uint8), 60, 140) > 0
        structure.extend(histogram(orientation[interior & edges], 9, (0, 9)))
        structure.append(float(edges[interior].mean()))
        # Coarse silhouette 6x4, row occupancy 4, column occupancy 6: 34.
        silhouette = cv2.resize(valid.astype(np.float32), (6, 4), interpolation=cv2.INTER_AREA)
        structure.extend(silhouette.ravel())
        structure.extend(silhouette.mean(1))
        structure.extend(silhouette.mean(0))
        values = np.concatenate((hsv[valid].astype(float) / [180, 255, 255], lab[valid] / 255.), axis=1)
        statistics = list(np.r_[values.mean(0), values.std(0), np.percentile(values, 75, axis=0) - np.percentile(values, 25, axis=0)])
        cov = np.cov(values, rowvar=False)
        statistics.extend(cov[np.triu_indices(6, 1)])
        statistics.extend([float(valid.mean()), min(crop.shape[1] / crop.shape[0], 6.) / 6., float(magnitude[interior].mean() / 1443)])
        blocks = {"color": color, "texture": texture, "structure": structure, "statistics": statistics}
        vector = unit(np.concatenate([unit(block) * self.weights[name] for name, block in blocks.items()]))
        if vector.shape != (self.dimension,) or not np.isfinite(vector).all():
            raise ValueError(f"Descriptor shape/values invalid: {vector.shape}")
        return vector.astype(np.float32)
