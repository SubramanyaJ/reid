"""Metric definitions shared by the offline research suite and its regression tests."""
import sys
import numpy as np
from scipy.optimize import linear_sum_assignment
from ..tracking.tracker import iou


def ratio(a, b):
    return float(a / b) if b else None


def distribution(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return {"n": 0, "mean": None, "median": None, "p95": None}
    return {"n": len(values), "mean": float(values.mean()),
            "median": float(np.median(values)), "p95": float(np.percentile(values, 95))}


def bootstrap_mean(values, seed=2026, samples=1000):
    """Query-resampling interval, not a claim of independent physical identities."""
    a = np.asarray(values, dtype=float)
    if not len(a):
        return None
    rng = np.random.default_rng(seed)
    means = np.array([rng.choice(a, len(a), replace=True).mean() for _ in range(samples)])
    return [float(x) for x in np.percentile(means, [2.5, 97.5])]


def ranking_metrics(ranking, relevant):
    """AP denominator includes ALL eligible positives, including candidates missed by LSH.

    A query without an eligible positive is open-set, excluded from mAP/CMC.
    An empty result for a query WITH positives has AP=0 and CMC=0.
    """
    relevant = set(relevant)
    if not relevant:
        return {"ap": None, "rank1": None, "rank5": None, "relevant_recall": None}
    hit = np.array([key in relevant for key in ranking], dtype=int)
    ap = float(np.sum(np.cumsum(hit) * hit / np.arange(1, len(hit) + 1)) / len(relevant))
    return {"ap": ap, "rank1": int(bool(hit[:1].sum())), "rank5": int(bool(hit[:5].sum())),
            "relevant_recall": float(hit.sum() / len(relevant))}


def match_boxes(truth, predictions, threshold=.5):
    """Maximum-cardinality matching above IoU threshold; IoU breaks cardinality ties."""
    if not len(truth) or not len(predictions):
        return []
    overlaps = np.array([[iou(np.asarray(a), np.asarray(b)) for b in predictions] for a in truth])
    valid = overlaps >= threshold
    # A unit of cardinality must dominate the total possible IoU gain.
    weights = valid * (min(overlaps.shape) + 1 + overlaps)
    rows, columns = linear_sum_assignment(-weights)
    return [(int(i), int(j), float(overlaps[i, j])) for i, j in zip(rows, columns) if valid[i, j]]


def detection_summary(tp, fp, fn, overlaps):
    return {"tp": tp, "fp": fp, "fn": fn, "precision": ratio(tp, tp + fp),
            "recall": ratio(tp, tp + fn), "f1": ratio(2 * tp, 2 * tp + fp + fn),
            "matched_box_iou": distribution(overlaps)}


def deep_size(value, seen=None):
    """Estimated reachable Python/NumPy bytes; not RSS or peak temporary memory."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(deep_size(k, seen) + deep_size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set)):
        size += sum(deep_size(v, seen) for v in value)
    return size
