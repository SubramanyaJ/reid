# Evaluation and paper reproduction

[Project overview](../README.md) · Run commands from the repository root.

## Offline synthetic demonstration

The demo renders moving vehicle-like objects in two synthetic fixed cameras and uses three real runtime/ledger instances, the third with no camera. It exercises warm-up, detection, tracking, features, quality, fusion, signed events, votes, replication, and ledger verification. Consensus transport is **in-process** in this demo; it does not test HTTP networking or physical cameras. It generates ground-truth boxes to attach approximate frame observations to known vehicle identities.

Linux:

```bash
cd ~/reid
source .venv/bin/activate
python -m reid demo --out experiments/results/demo
python -m reid evaluate --csv experiments/results/demo/observations.csv --out experiments/results/demo/reid_metrics.json
```

Windows PowerShell:

```powershell
Set-Location E:\home\gitthings\reid
.\.venv\Scripts\Activate.ps1
python -m reid demo --out experiments/results/demo
python -m reid evaluate --csv experiments/results/demo/observations.csv --out experiments/results/demo/reid_metrics.json
```

Inspect `C1.avi` and `C2.avi`, `observations.csv`, `evaluation.json`, `ledger_summary.json`, each node's `decisions.jsonl`, and its local SQLite database. AVI output uses MJPG through OpenCV; codec support must be present. The default 430 frames at 15 simulated FPS cover the two-camera schedule. Processing runs offline as fast as possible. This is a reproducible scene generator, but UUIDs, keys, and wall-clock anchors differ per run. An existing demo key directory is never overwritten; use `--out experiments/results/demo2` to repeat.

The demo reports outcomes even if detections or cross-camera matches fail. It does not force global IDs to ground-truth labels, inject successful plate readings, or substitute measured performance with canned values. Success on rendered shapes would not establish accuracy on real vehicles.

## Tests and manual network checks

Linux:

```bash
cd ~/reid
source .venv/bin/activate
python -m pytest -q
```

Windows PowerShell:

```powershell
Set-Location E:\home\gitthings\reid
.\.venv\Scripts\Activate.ps1
python -m pytest -q
```

Tests cover descriptor dimensions/normalization/repeatability/discrimination; quality rejection; background warm-up; track persistence, gaps, expiry and merged components; seeded LSH insertion/removal; fuzzy and conflicting plates; missing-evidence fusion and contradiction protection; cross-camera coordinate independence; bounded persistent galleries; signed packet tampering; insufficient quorum; duplicate voters; proposer rotation; late block acceptance; ledger corruption; and durable vote/proposal replay. They do not establish real camera accuracy or complete OCR robustness. Tests are deliberately small, independent, and CPU-only.

Manual HTTP checks with running local peers:

```bash
# Linux
curl http://127.0.0.1:9000/peer/status
curl http://127.0.0.1:9001/peer/status
curl http://127.0.0.1:9002/peer/status
```

```powershell
# Windows PowerShell
Invoke-RestMethod http://127.0.0.1:9000/peer/status
Invoke-RestMethod http://127.0.0.1:9001/peer/status
Invoke-RestMethod http://127.0.0.1:9002/peer/status
```

After camera-generated events commit, confirm matching tip hashes. Stop C3, continue observations, then restart C3 with its original key/config/database. Observe missing-block catch-up and gallery recovery. Expect commitment to stall at C3's proposer turn while it is offline. Test an invalid signature or modified transaction with a disposable test consortium; rejection should leave the committed tip unchanged. The Python ledger tests already construct several invalid inputs without depending on a network service.

Full local ledger verification, Linux:

```bash
source .venv/bin/activate
python -m reid verify-ledger --config config/local/C1.yaml
python -m reid verify-ledger --config config/local/C2.yaml
python -m reid verify-ledger --config config/local/C3.yaml
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m reid verify-ledger --config config/local/C1.yaml
python -m reid verify-ledger --config config/local/C2.yaml
python -m reid verify-ledger --config config/local/C3.yaml
```

The verifier checks genesis, all hashes and signatures, proposer order, chain extension, event uniqueness, and quorum certificates. An invalid chain exits nonzero. Run against the corresponding LAN config if using `config/lan/`.

## LSH benchmark and Re-ID evaluation

### Retrieval benchmark

Linux:

```bash
source .venv/bin/activate
python -m reid benchmark --sizes 100 1000 5000 --queries 100 --out experiments/results/benchmark
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m reid benchmark --sizes 100 1000 5000 --queries 100 --out experiments/results/benchmark
```

The benchmark compares brute-force matrix-vector cosine search against LSH candidate generation **plus exact candidate cosine verification**. Default input is synthetic labelled descriptor clusters, generated with seed 2026. It is a retrieval experiment, not vehicle-recognition accuracy. `benchmark.csv` records dataset size, query index, method, number of candidates, candidate recall, exact nearest-neighbor inclusion, top-1 label correctness, query latency in milliseconds, and index-build time. `benchmark.png` plots median query latency, mean candidates, and labelled candidate recall.

Labelled candidate recall is `relevant descriptors returned / all relevant descriptors` for the query's label. It differs from whether at least one correct candidate was retrieved. Exact-top1 recall indicates whether the brute-force nearest descriptor was present in the LSH candidate set. Unlabelled data leave label metrics blank. Query timing excludes index construction and dataset loading, includes candidate retrieval and reranking, and is measured on the local machine. NumPy/BLAS thread settings, CPU cache, query order, dimension, and gallery distribution affect timings. Brute force can be faster for small datasets; no speedup is assumed.

For real extracted descriptors, provide an NPZ with `vectors` shaped `(N,D)` and held-out `queries` shaped `(Q,D)`. Optionally include matching-length `labels` and `query_labels`; both must be supplied together. Use integer or fixed-width string labels, not pickled object arrays. Zero/nonfinite vectors are rejected, vectors are normalized, and NPZ loading disables pickle.

```text
python -m reid benchmark --dataset data/labelled_descriptors.npz --queries 200 --out experiments/results/real_benchmark
```

### End-to-end Re-ID evaluation

Use CSV columns `truth_id,global_id,camera_id`; additional columns are ignored. Blank `global_id` means unresolved/abstained. `experiments/labelled_observations.example.csv` shows the format. `evaluate` reports resolved coverage, pairwise precision/recall/F1, and cross-camera-only versions, using independent physical labels as truth. Undefined ratios are JSON `null`. Pairwise metrics are computed only among resolved rows, so coverage must accompany the accuracy numbers. This tool does not implement MOTA, IDF1, or a full tracking benchmark, and its simple pair enumeration is quadratic in row count.

```text
python -m reid evaluate --csv data/labelled_observations.csv --out experiments/results/reid_evaluation.json
```

For a credible study: collect consented multi-camera sequences; label physical identities independently of this system; separate tuning and held-out captures; report camera geometry, time synchronization, resolution, weather, frame rate, and traffic density; evaluate quality filtering, LSH vs brute force, ORB/SIFT/none, plate enabled/missing, and context transitions; report abstention coverage, false associations, identity fragmentation, candidate misses, and processing latency. Also measure block latency and recovery behavior separately from vision accuracy. A hash-chain result is not a substitute for these measurements.

## Paper and reproducible visual metrics

[`main.tex`](../main.tex) is the current manuscript; [`paper.org`](../paper.org) is an earlier working draft. The [paper revision notes](history/paper_revision_notes.md) explain the parking-lot experiment, its source records, and the reconciliation of recorded judgments with previously exported metrics. Preserve the source experiment records when reproducing those results.

The manuscript's historical review experiment contains accepted matches only. New replay sessions also queue uncertain and rejected proposals when a preserved candidate pair exists. Those runs have a different evaluation population; do not combine their scores with the historical accepted-match-only experiment without accounting for that difference.

Run the offline visual suite with a fresh output directory:

```bash
python -m reid metrics --out experiments/results/my-run
```

Outputs include `metrics.json`, per-query `queries.csv`, retrieval plots, and descriptor ablations. The [research suite guide](../experiments/RESEARCH.md) explains timing conventions, ground truth, crop/frame manifests, and NPZ input. Default synthetic inputs exercise the pipeline; they do not establish real multi-camera accuracy.

## Build the paper

Keep `paper_figures/` beside `main.tex`. Compile from the project root with a LaTeX installation containing IEEEtran and the packages declared by the manuscript:

```text
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

`make` runs the same two compilation passes. No BibTeX step is needed because references are included in the source. `scripts/clean.ps1` removes intermediate files while retaining the manuscript, figures, and compiled PDFs.
