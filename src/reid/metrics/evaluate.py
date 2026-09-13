import csv
import json
from pathlib import Path


def evaluate(path, out):
    """Pairwise identity consistency of labelled, resolved observations; abstentions separate."""
    with Path(path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"truth_id", "global_id", "camera_id"}.issubset(rows[0]):
        raise ValueError("CSV columns required: truth_id, global_id, camera_id")
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    resolved = [r for r in rows if r["global_id"].strip()]
    cross = {key: 0 for key in counts}
    for i, a in enumerate(resolved):
        for b in resolved[i+1:]:
            same_truth, same_pred = a["truth_id"] == b["truth_id"], a["global_id"] == b["global_id"]
            key = "tp" if same_truth and same_pred else "fp" if same_pred else "fn" if same_truth else "tn"
            counts[key] += 1
            if a["camera_id"] != b["camera_id"]:
                cross[key] += 1

    def summary(c):
        precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else None
        recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else None
        f1 = (2 * precision * recall / (precision + recall) if precision + recall else 0.) if precision is not None and recall is not None else None
        return {**c, "precision": precision, "recall": recall, "f1": f1}
    result = {"observations": len(rows), "resolved": len(resolved), "coverage": len(resolved) / len(rows),
              "all_pairs": summary(counts), "cross_camera_pairs": summary(cross),
              "note": "Pairwise metrics on resolved rows; report coverage alongside accuracy. Not IDF1 or MOTA."}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
