from dataclasses import dataclass
import cv2
import numpy as np


@dataclass
class Component:
    bbox: np.ndarray  # x, y, width, height
    area: int
    centroid: np.ndarray


class Foreground:
    def __init__(self, config):
        self.cfg = config
        self.reset()

    def reset(self):
        if self.cfg["method"] == "KNN":
            self.model = cv2.createBackgroundSubtractorKNN(history=self.cfg["history"], detectShadows=True)
        else:
            self.model = cv2.createBackgroundSubtractorMOG2(history=self.cfg["history"],
                            varThreshold=self.cfg["variance_threshold"], detectShadows=True)

    def apply(self, frame, warmup=False):
        raw = self.model.apply(frame, learningRate=.05 if warmup else -1)
        # Shadows are retained separately, never inflated into foreground objects.
        shadows = np.uint8(raw == 127) * 255
        mask = np.uint8(raw == 255) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        count, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
        components = []
        cleaned = np.zeros_like(mask)
        minimum = max(9, round(mask.size * self.cfg["min_component_fraction"]))
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            if area < minimum:
                continue
            cleaned[labels == i] = 255
            components.append(Component(np.array([x, y, w, h], dtype=float), int(area), centers[i]))
        return cleaned, shadows, [] if warmup else components
