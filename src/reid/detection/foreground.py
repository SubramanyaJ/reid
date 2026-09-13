from dataclasses import dataclass
import cv2
import numpy as np


@dataclass
class Component:
    bbox: np.ndarray  # x, y, width, height
    area: int
    centroid: np.ndarray
    mask: np.ndarray | None = None  # bbox-local pixels belonging only to this component


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

    def clean(self, mask):
        """Keep connected objects with a thick core, including their attached details.

        A thin disconnected finger has no qualifying core. Fingers attached to a
        palm keep their silhouette; no dilation joins neighboring objects.
        """
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        # Fill only small enclosed holes, never the open gap between objects.
        contours, hierarchy = cv2.findContours(mask.copy(), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        max_hole = mask.size * self.cfg["max_hole_fraction"]
        if hierarchy is not None:
            for index, contour in enumerate(contours):
                if hierarchy[0, index, 3] >= 0 and cv2.contourArea(contour) <= max_hole:
                    cv2.drawContours(mask, [contour], -1, 255, cv2.FILLED)
        count, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
        distance = cv2.distanceTransform(np.pad(mask, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
        radius = min(mask.shape) * self.cfg["min_core_radius_fraction"]
        components = []
        cleaned = np.zeros_like(mask)
        minimum = max(9, round(mask.size * self.cfg["min_component_fraction"]))
        for i in range(1, count):
            x, y, w, h, area = stats[i]
            if area < minimum:
                continue
            local = labels[y:y+h, x:x+w] == i
            if distance[y:y+h, x:x+w][local].max(initial=0) < radius:
                continue
            object_mask = np.uint8(local) * 255
            cleaned[y:y+h, x:x+w][local] = 255
            components.append(Component(np.array([x, y, w, h], dtype=float), int(area), centers[i], object_mask))
        return cleaned, components

    def apply(self, frame, warmup=False):
        raw = self.model.apply(frame, learningRate=.05 if warmup else -1)
        # Shadows are retained separately, never inflated into foreground objects.
        shadows = np.uint8(raw == 127) * 255
        cleaned, components = self.clean(np.uint8(raw == 255) * 255)
        return cleaned, shadows, [] if warmup else components
