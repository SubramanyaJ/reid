import cv2
import numpy as np
from reid.features.descriptor import MVSVG
from reid.features.quality import observation_quality
from reid.detection.foreground import Component, Foreground
from reid.tracking.tracker import Tracker


def test_descriptor_fixed_masked_normalized_and_discriminating():
    extractor = MVSVG()
    a = np.full((60, 130, 3), (30, 60, 210), np.uint8)
    cv2.rectangle(a, (40, 10), (80, 35), (120, 110, 100), -1)
    b = a.copy()
    b[:, :, [0, 2]] = b[:, :, [2, 0]]
    mask = np.full(a.shape[:2], 255, np.uint8)
    first = extractor.extract(a, mask)
    assert first.shape == (602,) and first.dtype == np.float32
    assert np.isfinite(first).all() and abs(np.linalg.norm(first) - 1) < 1e-5
    np.testing.assert_array_equal(first, extractor.extract(a, mask))
    assert np.dot(first, extractor.extract(b, mask)) < .99
    resized = extractor.extract(cv2.resize(a, (65, 30)))
    assert resized.shape == first.shape


def test_quality_rejects_tiny_empty_observations():
    crop = np.zeros((10, 20, 3), np.uint8)
    score, _ = observation_quality(crop, np.zeros((10, 20), np.uint8), 1., 1.)
    assert score == 0


def component(x, y=40):
    return Component(np.array([x, y, 40, 24], float), 960, np.array([x+20, y+12], float))


def test_tracker_persistence_separation_gap_and_expiry(config):
    cfg = {**config["tracking"], "min_hits": 2, "max_missed": 3}
    tracker = Tracker("C1", cfg)
    for frame in range(4):
        tracks = tracker.update([component(10+3*frame), component(110-3*frame)], frame*.1, (120, 200, 3))
    ids = [t.local_id for t in tracks]
    assert len(ids) == 2 and len(set(ids)) == 2
    tracker.update([], .4, (120, 200, 3))
    assert all(t.state == "LOST" for t in tracker.tracks)
    tracks = tracker.update([component(25), component(95)], .5, (120, 200, 3))
    assert [t.local_id for t in tracks] == ids
    for n in range(4):
        tracker.update([], .6+n*.1, (120, 200, 3))
    assert not tracker.tracks


def test_merged_blob_does_not_collapse_confirmed_ids(config):
    tracker = Tracker("C1", {**config["tracking"], "min_hits": 2})
    for index in range(3):
        tracker.update([component(10), component(65)], index*.1, (120, 200, 3))
    ids = [t.local_id for t in tracker.tracks]
    merged = Component(np.array([10, 40, 95, 24], float), 2280, np.array([57.5, 52]))
    tracks = tracker.update([merged], .3, (120, 200, 3))
    assert [t.local_id for t in tracks] == ids
    assert all(t.state == "LOST" for t in tracks)


def test_background_warmup_suppresses_components(config):
    detector = Foreground(config["detection"])
    frame = np.zeros((100, 160, 3), np.uint8)
    for _ in range(15):
        _, _, components = detector.apply(frame, warmup=True)
        assert components == []
