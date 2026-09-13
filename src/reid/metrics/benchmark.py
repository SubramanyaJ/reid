import csv
from pathlib import Path
from time import perf_counter
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from ..lsh.index import HyperplaneLSH


def benchmark(out, sizes=(100, 1000, 5000), queries=100, dimension=602, dataset=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(2026)
    supplied = np.load(dataset, allow_pickle=False) if dataset else None
    rows = []
    if supplied is not None:
        sizes = [len(supplied["vectors"])]
        dimension = supplied["vectors"].shape[1]
    for size in sizes:
        if supplied is None:
            groups = max(5, size // 5)
            centers = rng.normal(size=(groups, dimension)).astype(np.float32)
            centers /= np.linalg.norm(centers, axis=1, keepdims=True)
            labels = np.arange(size) % groups
            vectors = centers[labels] + rng.normal(0, .018, (size, dimension))
            target = rng.integers(0, groups, size=queries)
            query_vectors = centers[target] + rng.normal(0, .018, (queries, dimension))
            query_labels = target
        else:
            vectors = supplied["vectors"]
            query_vectors = supplied["queries"][:queries]
            labels = supplied["labels"] if "labels" in supplied else None
            query_labels = supplied["query_labels"][:queries] if "query_labels" in supplied else None
        vectors, query_vectors = np.asarray(vectors, np.float32), np.asarray(query_vectors, np.float32)
        if vectors.ndim != 2 or query_vectors.ndim != 2 or vectors.shape[1] != query_vectors.shape[1]:
            raise ValueError("Dataset needs 2-D vectors and queries with matching dimensions")
        if not len(vectors) or not len(query_vectors) or not np.isfinite(vectors).all() or not np.isfinite(query_vectors).all():
            raise ValueError("Dataset must be nonempty and finite")
        if np.any(np.linalg.norm(vectors, axis=1) < 1e-9) or np.any(np.linalg.norm(query_vectors, axis=1) < 1e-9):
            raise ValueError("Zero descriptors cannot be benchmarked")
        if (labels is None) != (query_labels is None):
            raise ValueError("Supply both labels and query_labels, or neither")
        if labels is not None and (len(labels) != len(vectors) or len(query_labels) != len(query_vectors)):
            raise ValueError("Labels must align with descriptor rows")
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        query_vectors /= np.linalg.norm(query_vectors, axis=1, keepdims=True)
        index = HyperplaneLSH(dimension)
        start = perf_counter()
        for key, vector in enumerate(vectors):
            index.insert(key, vector)
        build_ms = (perf_counter() - start) * 1000
        for qid, query in enumerate(query_vectors):
            start = perf_counter()
            exact_scores = vectors @ query
            exact_best = int(np.argmax(exact_scores))
            brute_ms = (perf_counter() - start) * 1000
            start = perf_counter()
            candidates = sorted(index.query(query))
            best = candidates[int(np.argmax(vectors[candidates] @ query))] if candidates else None
            lsh_ms = (perf_counter() - start) * 1000
            relevant = set(np.flatnonzero(labels == query_labels[qid]).tolist()) if labels is not None else None
            recall = len(relevant.intersection(candidates)) / len(relevant) if relevant else None
            for mode, count, latency, prediction in [("brute_force", size, brute_ms, exact_best),
                                                     ("lsh_exact", len(candidates), lsh_ms, best)]:
                rows.append({"dataset_size": size, "query": qid, "method": mode, "candidates": count,
                             "candidate_recall": (1. if mode == "brute_force" else recall) if relevant else "",
                             "exact_top1_recall": int(mode == "brute_force" or exact_best in candidates),
                             "top1_correct": int(prediction is not None and labels[prediction] == query_labels[qid]) if labels is not None else "",
                             "query_ms": latency, "build_ms": build_ms if mode == "lsh_exact" else 0})
    with (out / "benchmark.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    fig, axes = plt.subplots(1, 3, figsize=(13, 4))
    for mode in ("brute_force", "lsh_exact"):
        subsets = [[r for r in rows if r["method"] == mode and r["dataset_size"] == size] for size in sizes]
        axes[0].plot(sizes, [np.median([r["query_ms"] for r in subset]) for subset in subsets], "o-", label=mode)
        axes[1].plot(sizes, [np.mean([r["candidates"] for r in subset]) for subset in subsets], "o-", label=mode)
        recalls = [[r["candidate_recall"] for r in subset if r["candidate_recall"] != ""] for subset in subsets]
        axes[2].plot(sizes, [np.mean(values) if values else np.nan for values in recalls], "o-", label=mode)
    for ax, title in zip(axes, ("Median query latency (ms)", "Mean candidate count", "Labelled candidate recall")):
        ax.set(xlabel="Dataset size", title=title)
        ax.grid(alpha=.2)
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(out / "benchmark.png", dpi=160)
    plt.close(fig)
    print(f"Wrote {out / 'benchmark.csv'} and benchmark.png (synthetic={dataset is None})")
