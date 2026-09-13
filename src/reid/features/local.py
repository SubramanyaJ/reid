import cv2
import numpy as np


class LocalVerifier:
    def __init__(self, method="ORB"):
        self.method = method
        self.detector = (cv2.SIFT_create(nfeatures=160) if method == "SIFT" else
                         cv2.ORB_create(nfeatures=160, edgeThreshold=10) if method == "ORB" else None)

    def extract(self, crop, mask):
        if self.detector is None:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        points, desc = self.detector.detectAndCompute(gray, mask)
        if desc is None or len(points) < 6:
            return None
        points, desc = points[:160], desc[:160]
        return {"method": self.method, "points": [[p.pt[0] / crop.shape[1], p.pt[1] / crop.shape[0]] for p in points],
                "descriptors": desc.tolist()}

    @staticmethod
    def compare(a, b):
        if not a or not b or a["method"] != b["method"]:
            return None
        binary = a["method"] == "ORB"
        dtype = np.uint8 if binary else np.float32
        aa, bb = np.array(a["descriptors"], dtype=dtype), np.array(b["descriptors"], dtype=dtype)
        if min(len(aa), len(bb)) < 6:
            return None
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING if binary else cv2.NORM_L2)
        pairs = matcher.knnMatch(aa, bb, k=2)
        good = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < .75 * pair[1].distance]
        if len(good) < 4:
            return None  # Texture-poor vehicles do not create negative local evidence.
        p = np.float32([a["points"][m.queryIdx] for m in good])
        q = np.float32([b["points"][m.trainIdx] for m in good])
        # Robust median translation consistency, deterministic (no learned model/RANSAC randomness).
        offsets = q - p
        inliers = np.linalg.norm(offsets - np.median(offsets, axis=0), axis=1) < .12
        return float(inliers.mean() * min(1., len(good) / 12))
