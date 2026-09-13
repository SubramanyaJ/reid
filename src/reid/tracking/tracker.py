"""Persistent component hypotheses and constant-velocity vehicle track promotion.

One-to-one assignment deliberately avoids proximity-based component merging.
During merged blobs, established hypotheses coast instead of collapsing IDs.
"""
from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import linear_sum_assignment


def center(box):
    return box[:2] + box[2:] / 2


def iou(a, b):
    low = np.maximum(a[:2], b[:2])
    high = np.minimum(a[:2] + a[2:], b[:2] + b[2:])
    overlap = np.maximum(0, high - low).prod()
    return float(overlap / max(1., a[2:].prod() + b[2:].prod() - overlap))


@dataclass
class Track:
    hypothesis_id: int
    bbox: np.ndarray
    last_time: float
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    hits: int = 1
    age: int = 1
    missed: int = 0
    state: str = "TENTATIVE"
    local_id: str | None = None
    global_id: str | None = None
    quality: float = 0.
    consistency: float = .5
    last_observation: float = -1e12
    evidence: dict = field(default_factory=dict)
    readings: list = field(default_factory=list)
    last_center: np.ndarray | None = None
    last_measured_time: float = 0.
    foreground_area: int = 0
    foreground_mask: np.ndarray | None = None
    preview_id: str | None = None


class Tracker:
    def __init__(self, camera_id, config, next_id=1):
        self.camera_id, self.cfg = camera_id, config
        self.tracks = []
        self.next_hypothesis, self.next_id = 1, next_id
        self.candidate_count = 0

    def valid_geometry(self, box, foreground_area, shape):
        height, width = shape[:2]
        x, y, w, h = box
        if min(w, h) <= 0:
            return False
        fraction = w * h / (width * height)
        # Hard resolution-relative floors apply at every vertical position.
        # Perspective only makes the area requirement slightly stricter near the bottom.
        relative_y = float(np.clip((y + h) / height, 0, 1))
        minimum = self.cfg["min_vehicle_fraction"] * (1 + .25 * relative_y)
        return (self.cfg["min_width_fraction"] <= w / width <= self.cfg.get("max_width_fraction", .80) and
                self.cfg["min_height_fraction"] <= h / height <= self.cfg.get("max_height_fraction", .85) and
                minimum <= fraction <= self.cfg["max_vehicle_fraction"] and
                foreground_area / (width * height) >= self.cfg["min_foreground_fraction"] and
                foreground_area / (w * h) >= self.cfg["min_fill_ratio"] and
                self.cfg["min_aspect_ratio"] <= w / h <= self.cfg["max_aspect_ratio"])

    def valid_vehicle(self, track, shape):
        return (self.valid_geometry(track.bbox, track.foreground_area, shape) and
                track.hits >= self.cfg["min_hits"] and track.consistency >= self.cfg["min_motion_consistency"])

    def update(self, components, timestamp, shape):
        # Invalid small fragments cannot create hypotheses or shrink established objects.
        components = [c for c in components if self.valid_geometry(c.bbox, c.area, shape)]
        self.candidate_count = len(components)
        tracks = [t for t in self.tracks if t.state != "DELETED"]
        for t in tracks:
            dt = max(0, min(timestamp - t.last_time, 2.))
            t.bbox[:2] += t.velocity * dt
            t.last_time = timestamp
            t.age += 1
        costs = np.full((len(tracks), len(components)), 1e6)
        merged = set()
        for j, component in enumerate(components):
            contained = []
            b = component.bbox
            for i, t in enumerate(tracks):
                c = center(t.bbox)
                if t.local_id and np.all(c >= b[:2]) and np.all(c <= b[:2] + b[2:]):
                    contained.append(i)
            if len(contained) >= 2:
                merged.add(j)
        for i, t in enumerate(tracks):
            for j, component in enumerate(components):
                if j in merged:
                    continue
                b = component.bbox
                ratio = np.maximum(t.bbox[2:], 1) / np.maximum(b[2:], 1)
                distance = np.linalg.norm(center(t.bbox) - center(b)) / max(20, np.linalg.norm(t.bbox[2:]))
                overlap = iou(t.bbox, b)
                if np.any(ratio < .45) or np.any(ratio > 2.2) or (overlap < .01 and distance > .85):
                    continue
                costs[i, j] = .45 * (1 - overlap) + .4 * distance + .15 * np.abs(np.log(ratio)).mean()
        matched_tracks, matched_components = set(), set()
        if costs.size:
            rows, cols = linear_sum_assignment(costs)
            for i, j in zip(rows, cols):
                if costs[i, j] >= 1e5:
                    continue
                t, component = tracks[i], components[j]
                c = center(component.bbox)
                if t.last_center is not None:
                    dt = max(.001, timestamp - t.last_measured_time)
                    measured = (c - t.last_center) / dt
                    error = np.linalg.norm(measured - t.velocity) * dt / max(20, np.linalg.norm(t.bbox[2:]))
                    t.consistency = .7 * t.consistency + .3 * float(np.exp(-error))
                    t.velocity = .65 * t.velocity + .35 * measured
                t.last_center, t.last_measured_time = c.copy(), timestamp
                t.bbox = component.bbox.copy()
                t.foreground_area, t.foreground_mask = component.area, component.mask
                t.hits += 1
                t.missed = 0
                if self.valid_vehicle(t, shape):
                    if t.local_id is None:
                        t.local_id = f"{self.camera_id}-T{self.next_id:05d}"
                        self.next_id += 1
                    t.state = "CONFIRMED"
                elif t.local_id:
                    t.state = "LOST"
                matched_tracks.add(i)
                matched_components.add(j)
        for i, t in enumerate(tracks):
            if i not in matched_tracks:
                t.missed += 1
                t.state = "DELETED" if t.missed > self.cfg["max_missed"] else "LOST" if t.local_id else "TENTATIVE"
        for j, component in enumerate(components):
            if j in matched_components or j in merged:
                continue
            t = Track(self.next_hypothesis, component.bbox.copy(), timestamp,
                      last_center=center(component.bbox), last_measured_time=timestamp,
                      foreground_area=component.area, foreground_mask=component.mask)
            self.next_hypothesis += 1
            tracks.append(t)
        self.tracks = [t for t in tracks if t.state != "DELETED"]
        return [t for t in self.tracks if t.local_id]
