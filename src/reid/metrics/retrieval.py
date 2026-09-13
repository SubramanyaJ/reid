"""Paired exact/LSH experiments with honest candidate-miss accounting."""
from time import perf_counter_ns
import json
import numpy as np
from ..lsh.index import HyperplaneLSH
from .measures import bootstrap_mean, deep_size, distribution, ranking_metrics, ratio


INDEX_CONFIGS = [(8, 12, False), (8, 12, True), (8, 16, True), (16, 12, True)]


def eligibility(data, qid, cross_camera=False):
    eligible = np.ones(len(data["vectors"]), dtype=bool)
    if "sample_ids" in data:
        eligible &= data["sample_ids"] != data["query_sample_ids"][qid]
    if cross_camera:
        if "camera_ids" not in data:
            raise ValueError("--cross-camera requires gallery and query camera IDs")
        eligible &= data["camera_ids"] != data["query_camera_ids"][qid]
    return eligible


def search(vectors, query, eligible, index=None, k=10):
    if index is None:
        scores = vectors @ query
        keys = np.flatnonzero(eligible)
        scores = scores[keys]
    else:
        keys = np.array(sorted(key for key in index.query(query) if eligible[key]), dtype=int)
        scores = vectors[keys] @ query if len(keys) else np.empty(0, dtype=np.float32)
    # Both methods pay for bounded top-k selection, not a full O(N log N) ranking.
    count = min(k, len(keys))
    if count:
        selected = np.argpartition(-scores, count-1)[:count]
        selected = selected[np.lexsort((keys[selected], -scores[selected]))]
        top = keys[selected]
    else:
        top = np.empty(0, dtype=int)
    return keys, scores, top


def evaluate_retrieval(data, seeds=(17, 29, 43), repeats=5, cross_camera=False, configs=INDEX_CONFIGS):
    vectors, queries = data["vectors"], data["queries"]
    n, dimension = vectors.shape
    all_rows, summaries = [], []
    for seed in seeds:
        indexes, builds, memory, occupancies = {}, {}, {}, {}
        for tables, bits, probe in configs:
            name = f"lsh_L{tables}_b{bits}_r{int(probe)}"
            index = HyperplaneLSH(dimension, tables, bits, seed, probe)
            started = perf_counter_ns()
            for key, vector in enumerate(vectors):
                index.insert(key, vector)
            builds[name] = (perf_counter_ns()-started)/1e6
            indexes[name] = index
            memory[name] = deep_size([index.planes, index.keys, index.buckets])
            loads = [len(bucket) for table in index.buckets for bucket in table.values()]
            occupancies[name] = {"occupied_buckets": len(loads), "postings": sum(loads),
                                 "load": distribution(loads), "max_load": max(loads, default=0),
                                 "plane_bytes": int(index.planes.nbytes),
                                 "probes_per_query": tables*(1+bits if probe else 1)}
        methods = {"brute_force": None, **indexes}
        rows_by_method = {name: [] for name in methods}
        rng = np.random.default_rng(seed)
        # Untimed warmups for every method, followed by paired, shuffled interleaving.
        for qid in range(min(5, len(queries))):
            eligible = eligibility(data, qid, cross_camera)
            for index in methods.values():
                search(vectors, queries[qid], eligible, index)
        for qid in rng.permutation(len(queries)):
            qid = int(qid)
            query = queries[qid]
            eligible = eligibility(data, qid, cross_camera)
            eligible_keys = np.flatnonzero(eligible)
            relevant = set(eligible_keys[data["labels"][eligible_keys] == data["query_labels"][qid]]) if "labels" in data else set()
            samples = {name: [] for name in methods}
            outputs = {}
            for _ in range(repeats):
                for name in rng.permutation(list(methods)):
                    started = perf_counter_ns()
                    outputs[name] = search(vectors, query, eligible, methods[name])
                    samples[name].append((perf_counter_ns()-started)/1e6)
            # Accuracy computation and complete ranking are deliberately outside timers.
            ek, es, _ = outputs["brute_force"]
            exact_ranking = ek[np.lexsort((ek, -es))]
            exact_set = set(exact_ranking[:10])
            for name, (keys, scores, _) in outputs.items():
                ranking = keys[np.lexsort((keys, -scores))]
                metrics = ranking_metrics(ranking.tolist(), relevant)
                if "labels" not in data:
                    metrics = {key: None for key in metrics}
                row = {"dataset": data["name"], "gallery_size": n, "seed": seed, "query": qid,
                       "method": name, "eligible": len(eligible_keys), "candidates": len(keys),
                       "candidate_fraction": ratio(len(keys), len(eligible_keys)),
                       "exact_top1_recall": int(exact_ranking[0] in set(keys)) if len(exact_ranking) else None,
                       "exact_top10_recall": ratio(len(exact_set.intersection(keys)), len(exact_set)),
                       **metrics, "query_ms": float(np.median(samples[name])),
                       "timing_samples_ms": json.dumps(samples[name]), "relevant_count": len(relevant),
                       "top_score": float(scores.max()) if len(scores) else None,
                       "top1_correct": int(bool(len(ranking) and ranking[0] in relevant)) if "labels" in data else None}
                rows_by_method[name].append(row)
                all_rows.append(row)
        baseline = {r["query"]: r["query_ms"] for r in rows_by_method["brute_force"]}
        for name, rows in rows_by_method.items():
            def mean(key):
                values = [r[key] for r in rows if r[key] is not None]
                return float(np.mean(values)) if values else None
            speeds = [baseline[r["query"]] / max(r["query_ms"], 1e-12) for r in rows]
            aps = [r["ap"] for r in rows if r["ap"] is not None]
            # Fixed visual-only .82 threshold. Unknowns are reported separately from CMC.
            known = [r for r in rows if r["relevant_count"] > 0]
            unknown = [r for r in rows if r["relevant_count"] == 0] if "labels" in data else []
            accepted = lambda r: r["top_score"] is not None and r["top_score"] >= .82
            summary = {"dataset": data["name"], "gallery_size": n, "dimension": dimension,
                       "queries": len(queries), "seed": seed, "method": name,
                       "query_ms": distribution([r["query_ms"] for r in rows]),
                       "candidate_count": distribution([r["candidates"] for r in rows]),
                       "candidate_fraction": mean("candidate_fraction"),
                       "exact_top1_recall": mean("exact_top1_recall"), "exact_top10_recall": mean("exact_top10_recall"),
                       "relevant_candidate_recall": mean("relevant_recall"), "rank1": mean("rank1"), "rank5": mean("rank5"),
                       "mAP": mean("ap"), "mAP_query_bootstrap_ci95": bootstrap_mean(aps, seed),
                       "known_queries": len(known), "unknown_queries": len(unknown),
                       "visual_threshold": .82, "unknown_false_accept_rate": ratio(sum(accepted(r) for r in unknown), len(unknown)),
                       "known_correct_accept_rate": ratio(sum(accepted(r) and r["top1_correct"] for r in known), len(known)),
                       "known_wrong_accept_rate": ratio(sum(accepted(r) and not r["top1_correct"] for r in known), len(known)),
                       "paired_speedup_mean": float(np.mean(speeds)), "paired_speedup_ci95": bootstrap_mean(speeds, seed),
                       "median_latency_speedup": float(np.median(list(baseline.values())) / np.median([r["query_ms"] for r in rows])),
                       "index_build_ms": builds.get(name, 0.), "vector_bytes": int(vectors.nbytes),
                       "index_estimated_bytes": memory.get(name, 0), "index_buckets": occupancies.get(name)}
            summaries.append(summary)
    return summaries, all_rows


def descriptor_ablations(data, cross_camera=False):
    """Re-normalized block removal, using exhaustive retrieval to isolate representation."""
    if data["vectors"].shape[1] != 602 or "labels" not in data:
        return []
    blocks = {"color": (0, 180), "texture": (180, 234), "structure": (234, 566), "statistics": (566, 602)}
    results = []
    for dropped in [None, *blocks]:
        vectors, queries = data["vectors"].copy(), data["queries"].copy()
        if dropped:
            lo, hi = blocks[dropped]
            vectors[:, lo:hi] = queries[:, lo:hi] = 0
            vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
            queries /= np.maximum(np.linalg.norm(queries, axis=1, keepdims=True), 1e-12)
        metrics = []
        positive, negative = [], []
        for qid, query in enumerate(queries):
            keys = np.flatnonzero(eligibility(data, qid, cross_camera))
            scores = vectors[keys] @ query
            ranking = keys[np.lexsort((keys, -scores))]
            relevant = set(keys[data["labels"][keys] == data["query_labels"][qid]])
            metrics.append(ranking_metrics(ranking, relevant))
            matching = data["labels"][keys] == data["query_labels"][qid]
            if matching.any():
                positive.append(float(scores[matching].max()))
            if (~matching).any():
                negative.append(float(scores[~matching].max()))
        results.append({"variant": "full" if dropped is None else "without_"+dropped,
                        "mAP": float(np.mean([m["ap"] for m in metrics if m["ap"] is not None])) if any(m["ap"] is not None for m in metrics) else None,
                        "rank1": float(np.mean([m["rank1"] for m in metrics if m["rank1"] is not None])) if any(m["rank1"] is not None for m in metrics) else None,
                        "best_positive_cosine": distribution(positive), "hardest_negative_cosine": distribution(negative)})
    return results
