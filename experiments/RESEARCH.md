# Run the visual research suite

Run from the repository root. No camera, keys, databases, peer service, or network connection is needed. The suite uses the actual foreground, tracking, crop, MVSV-G, and LSH modules. It measures visual retrieval separately from foreground-to-descriptor replay; it does not claim to evaluate the full live identity-fusion system.

Windows PowerShell:

```powershell
Set-Location E:\home\gitthings\reid
.\.venv\Scripts\python.exe -m reid metrics --out experiments/results/my-run
```

Linux, when you later use another machine:

```bash
python -m reid metrics --out experiments/results/my-run
```

Use a fresh `--out` directory for every run; existing `metrics.json` is preserved. A quick check uses `--sizes 100 --queries 10 --identities 8 --seeds 17 --repeats 2`. The default evaluates Gaussian galleries of 100/1,000/5,000/10,000 descriptors, 100 queries each, plus 192 handcrafted descriptors from 64 drawn objects and 80 held-out queries (16 unknown objects). Hyperplane seeds are 17/29/43, with five timing repetitions per query. Input generation uses seed 2026. Four indexes vary table count, bit count, and one-bit multiprobing. The CLI pins supported BLAS thread environment variables before NumPy is imported and sets OpenCV to one thread. Use the fresh CLI process for publishable timings, not an already running notebook that may have loaded BLAS.

Outputs:

| File | Contents |
|---|---|
| `metrics.json` | Structured summaries, settings, source/input checksums, package/runtime details, metric definitions |
| `queries.csv` | Per-query results, all raw repeated query timings, candidates, recalls, AP, ranks, top cosine |
| `retrieval.png` | Latency, candidate fraction, and exact top-10 inclusion for every input distribution |
| `descriptor-ablation.png` | Exhaustive mAP after removing each MVSV-G block (when crops are evaluated) |
| `rendered-descriptors.npz` | Reproducible synthetic image descriptors and labels (default suite) |

`paper.org` contains the paper. Its tables identify the archived run under `experiments/results/paper-local-20260914`; a later run does not silently rewrite the paper. Figures are ordinary PNG files and the paper remains Org text.

## Evaluate your own labeled crops

Prepare **independently labeled** images of the physical objects. Split entire capture sequences/tracklets between gallery and queries; neighboring frames from one track should not become a purported cross-camera test. Reserve separate identities or capture sessions for tuning. The evaluator does not infer physical ground truth from generated global tags.

`crops.example.json` shows the shape of the manifest. Paths resolve relative to the manifest. Every image needs `identity`, `camera`, and `sample_id`; masks are optional crop-sized grayscale images with nonzero foreground. Without a mask, the entire image is treated as foreground. The adapter rejects identical image bytes across gallery and queries, zero/nonfinite descriptors, duplicate sample IDs within a split, and misaligned masks. Distinct file bytes alone cannot rule out near-duplicate-frame leakage, so split by capture session before writing the manifest.

```text
python -m reid metrics --crops data/crops.json --cross-camera --queries 1000 --out experiments/results/real-crops
```

`--cross-camera` excludes **all** same-camera gallery samples. Matching sample IDs are always excluded. Different datasets have different junk-image protocols: this is an explicit strict cross-camera protocol, not an automatic implementation of every official benchmark split. CMC/mAP are descriptor rankings, not rankings after grouping all exemplars of one identity. Ground-truth masks test descriptor quality conditional on known localization; use predicted masks in a separately named experiment to measure the effect of segmentation errors. ORB verification, observation-interval sampling, bounded live-gallery selection, and late fusion are outside these retrieval timings.

## Supply extracted vectors

```text
python -m reid metrics --dataset data/descriptors.npz --queries 1000 --skip-vision --out experiments/results/real-vectors
```

NPZ keys `vectors` `(N,D)` and `queries` `(Q,D)` are required. Optional pairs are `labels` / `query_labels`, `camera_ids` / `query_camera_ids`, and `sample_ids` / `query_sample_ids`. Identifiers must be one-dimensional integer or fixed-width string arrays of matching length, never pickle/object arrays. Supply both members of each pair. All rows are validated before query truncation. Vectors are copied and L2 normalized. The NPZ adapter cannot verify image/tracklet leakage or descriptor provenance: record how you extracted and split them. Block ablation is only applied to MVSV-G crops, never arbitrary 602-value external vectors.

## Replay labeled video frames

```text
python -m reid metrics --crops data/crops.json --cross-camera --frames-manifest data/frames.json --out experiments/results/real-study
```

`frames.example.json` is a format sketch, not supplied footage. Replace the example paths and expand it to include a complete sequence. Use a stationary camera. Supply at least three seconds of explicit `warmup: true` background frames per camera (four seconds at the capture frame rate is recommended), followed by evaluated frames. Warmup cannot resume midway through a camera sequence. Timestamps must strictly increase per camera; resolution must remain constant. Decode recorded video into chronological images; do not randomly sample frames for background-subtraction evaluation. There is independent foreground/tracker state per camera, and this replay opens no live camera.

Every frame requires `objects: [...]` with independently assigned physical `identity` and pixel `bbox: [x,y,width,height]`. Boxes must be within the image. A labeled negative frame has `objects: []`. Optional `mask` is a full-frame binary union of target objects, not a rectangle rasterization masquerading as pixel-level truth. Unannotated masks produce JSON `null` for mask accuracy, not zero. Only actual current confirmed observations are scored; coasting lost tracks are not counted as visible detections. The replay measures old and tightened gates using a fresh foreground model each time. The supplied sequence is not altered.

The default motion fixture has 80 warmup frames and 100 evaluated frames at 640×360/20 FPS, two drawn moving objects and a thin distractor. Its masks and identity labels are known analytically. It does not exercise real camera motion, occlusion, front/rear transitions, or weather.

## Read the numbers correctly

- `retrieval[]` contains one summary per dataset/gallery size/index seed/method. `rank1`, `rank5`, and `mAP` average only queries with eligible positives. Missing LSH candidates still count against AP's denominator. Queries with no eligible positives are listed as unknown for this gallery/protocol; the open-set fields need those labels to be meaningful.
- `exact_top1_recall` and `exact_top10_recall` measure inclusion of exhaustive cosine neighbors, irrespective of physical identity. `relevant_candidate_recall` measures inclusion of **all** eligible positive descriptors. These are different quantities.
- `query_ms` summarizes per-query medians across repeated timings. Its `p95` is the 95th percentile of those medians, not the p95 of all raw calls. Full ranking for AP and ground-truth work occur outside the timer. Both methods pay for candidate selection, filtering, cosine, and partial top-10 selection. LSH also pays its projection and Python set costs; brute force uses a vectorized scan.
- `median_latency_speedup = median(brute query medians) / median(LSH query medians)`. Above one means faster. `paired_speedup_mean` and its interval use per-query ratios, a different estimand. Intervals use 1,000 query-bootstrap samples and do not model different physical populations, hardware runs, or correlated frames. Independent index seeds are reported separately.
- `index_estimated_bytes` is recursively counted Python/NumPy object memory for the index with integer keys. `vector_bytes` is additional shared gallery storage. This is not process RSS, peak working memory, or the entire live identity store. Index build time excludes vector extraction/normalization and random-plane allocation. Data loading and figure generation are not query time.
- The fixed visual-only acceptance threshold is 0.82. Unknown false accepts, known correct accepts, and known wrong accepts are separate. These are diagnostics of a cosine-only rule, not calibrated probabilities or full live fusion decisions. Use a separate validation split to select a threshold and preserve unknown test identities.
- `vision[]` uses one-to-one IoU≥0.5 matching. It reports component and confirmed-track TP/FP/FN, precision/recall/F1, mean matched IoU, micro mask IoU/Dice, identity switches, interruptions, quality admission, and stage timing. A switch is a changed matched local ID for the same physical ID, including after a gap. An interruption is a previously matched identity becoming matched again after one or more evaluated frames without a match; disappearance/reappearance also counts. These are explicitly defined diagnostics, not official MOTA, IDF1, or HOTA.
- `stage_ms_per_frame.descriptor` is the sum for all admitted crops in each frame, including zero for frames without admitted tracks. `processing_fps` is inverse mean foreground-through-descriptor time, excluding disk decode, annotation work, retrieval, display, and pacing. It is not measured camera FPS.
- No independent real multi-camera data were available for the archived local run. Its generated-object and Gaussian results cannot establish field accuracy or superiority over learned methods.

## Regression suite

```text
python -m pytest -q
```

Research-specific regressions are in `tests/test_research.py`. They cover candidate-miss AP, unknown queries, leakage/exclusions, one-to-one box matching, maximum box limits, LSH insert/replace/remove, malformed data, and output preservation. They never start nodes or cameras.
