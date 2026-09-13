"""Single-command, camera-free research experiment runner."""
import csv
from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib.metadata
import json
import io
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter
import cv2
import numpy as np
from .datasets import file_digest, gaussian, load_crops, load_npz, rendered
from .retrieval import descriptor_ablations, evaluate_retrieval
from .vision import evaluate_vision


def environment():
    package_root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=package_root, capture_output=True,
                                text=True, timeout=5, check=False).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        commit = None
    runtime = io.StringIO()
    with redirect_stdout(runtime):
        np.show_config()
    return {"python": sys.version, "platform": platform.platform(), "processor": platform.processor(),
            "logical_cpus": os.cpu_count(), "git_commit": commit,
            "packages": {name: importlib.metadata.version(name) for name in ["numpy", "scipy", "opencv-python", "matplotlib"]},
            "thread_environment": {key: os.environ.get(key) for key in ["OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS"]},
            "opencv_threads": cv2.getNumThreads(), "numpy_runtime": runtime.getvalue(),
            "source_sha256": {str(path.relative_to(package_root)).replace("\\", "/"): file_digest(path)
                              for path in sorted(package_root.rglob("*.py"))}}


def charts(out, summaries, ablations):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    names = list(dict.fromkeys(row["dataset"] for row in summaries))
    fig, axes = plt.subplots(len(names), 3, figsize=(13, 3.6*len(names)), squeeze=False)
    for axis, dataset in zip(axes, names):
        selected = [row for row in summaries if row["dataset"] == dataset]
        sizes = sorted(set(row["gallery_size"] for row in selected))
        methods = list(dict.fromkeys(row["method"] for row in selected))
        for method in methods:
            groups = [[row for row in selected if row["method"] == method and row["gallery_size"] == size] for size in sizes]
            def optional_mean(group, key):
                values = [row[key] for row in group if row[key] is not None]
                return np.mean(values) if values else np.nan
            axis[0].plot(sizes, [np.mean([row["query_ms"]["median"] for row in group]) for group in groups], "o-", label=method)
            axis[1].plot(sizes, [optional_mean(group, "candidate_fraction") for group in groups], "o-", label=method)
            axis[2].plot(sizes, [optional_mean(group, "exact_top10_recall") for group in groups], "o-", label=method)
        for ax, title in zip(axis, ["Median query time (ms)", "Candidate fraction", "Exact top-10 inclusion"]):
            ax.set(title=f"{dataset}\n{title}", xlabel="Gallery descriptors")
            ax.grid(alpha=.2)
        axis[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out/"retrieval.png", dpi=170)
    plt.close(fig)
    if ablations:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar([row["variant"].replace("without_", "minus ") for row in ablations], [row["mAP"] or 0 for row in ablations])
        ax.set(ylabel="mAP", ylim=(0, 1.05), title="Descriptor ablation: exhaustive search on labeled crops")
        fig.tight_layout()
        fig.savefig(out/"descriptor-ablation.png", dpi=170)
        plt.close(fig)


def research(args):
    out = Path(args.out).resolve()
    # Preserve completed experiments: use a fresh output directory for each run.
    if (out/"metrics.json").exists():
        raise ValueError("metrics.json already exists; choose a new --out directory to preserve this run")
    out.mkdir(parents=True, exist_ok=True)
    cv2.setNumThreads(1)
    cv2.setRNGSeed(args.data_seed)
    started = perf_counter()
    config = {key: value for key, value in vars(args).items() if key != "command"}
    supplied = load_npz(args.dataset, args.queries) if args.dataset else load_crops(args.crops, args.queries) if args.crops else None
    datasets = [supplied] if supplied else [gaussian(size, args.queries, args.data_seed) for size in args.sizes]
    crops = supplied if args.crops else None
    if supplied is None:
        print("Extracting descriptors from held-out synthetic crop views...", flush=True)
        crops = rendered(args.identities, args.data_seed)
        datasets.append(crops)
        np.savez_compressed(out/"rendered-descriptors.npz", **{k: v for k, v in crops.items() if isinstance(v, np.ndarray)})
    summaries, rows, origins = [], [], []
    for data in datasets:
        print(f"Retrieval: {data['name']}, N={len(data['vectors'])}, Q={len(data['queries'])}", flush=True)
        result, raw = evaluate_retrieval(data, args.seeds, args.repeats, args.cross_camera)
        summaries.extend(result)
        rows.extend(raw)
        origins.append({"dataset": data["name"], "gallery_size": len(data["vectors"]), **data["origin"]})
    ablations = descriptor_ablations(crops, args.cross_camera) if crops else []
    vision = []
    if not args.skip_vision:
        for previous in (True, False):
            print(f"Labeled frame replay: {'previous' if previous else 'tightened'} box gates", flush=True)
            vision.append(evaluate_vision(args.frames_manifest, args.data_seed, previous))
    result = {"schema_version": "reid-research/1.0", "created_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "offline visual descriptor retrieval and labeled foreground/tracker replay; no cameras or peers started",
              "config": config, "environment": environment(), "datasets": origins,
              "retrieval": summaries, "descriptor_ablations": ablations, "vision": vision,
              "protocol": {"ranking_unit": "gallery descriptor, not fused identity",
                           "similarity": "cosine on normalized float32 vectors",
                           "timed_query": "candidate generation, eligibility filtering, exact scoring, bounded top-10 selection",
                           "accuracy_ranking": "complete candidate ranking outside the timed region; ties by gallery row index",
                           "latency_summary": "distribution over per-query median of repeated timings, separate for each index seed",
                           "confidence_intervals": "1000 query bootstrap resamples; conditional on fixed data and index seed",
                           "open_set": "no eligible positive after exclusions; omitted from mAP/CMC, reported at fixed cosine >= .82",
                           "exclusions": "matching sample IDs always excluded; all same-camera entries excluded with --cross-camera",
                           "memory": "reachable Python/NumPy index size estimate plus separately reported float32 gallery bytes; not RSS",
                           "limitations": ["synthetic fixtures do not establish real Re-ID accuracy", "no learned baselines implemented",
                                           "no MOTA/IDF1/HOTA claim", "no energy, network, or multi-machine measurement"]},
              "elapsed_seconds_before_charts": perf_counter()-started}
    with (out/"queries.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    charts(out, summaries, ablations)
    payload = json.dumps(result, indent=2, allow_nan=False)
    temporary = out/"metrics.json.tmp"
    temporary.write_text(payload+"\n", encoding="utf-8")
    temporary.replace(out/"metrics.json")
    print(f"Wrote {out/'metrics.json'}", flush=True)
    return result
