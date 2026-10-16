# Video replay and human review

[Project overview](../README.md) · Run commands from the repository root.

## Process a stored MP4 on one machine

### Video frontend with human-reviewed accuracy

Add `--ui` to open a local video review service:

```powershell
Set-Location E:\home\gitthings\reid
.\.venv\Scripts\python.exe -m reid replay --video "E:\videos\recording.mp4" --ui --out experiments/results/video-review-01
```

Open **http://127.0.0.1:9000/** and click **Start video**. If that port is occupied, add `--port 9001` and open http://127.0.0.1:9001/. The frontend listens locally; no LAN peers or cameras are required. The video does not begin until you press Start.

1. Watch the annotated video preview while the recording is processed.
2. For each queued pair, compare **Current observation** with **Earlier candidate**. Click an image to enlarge it.
3. Choose **Approve · same object** or **Reject · different objects** based on physical identity. The model's prediction is hidden on the review card to reduce labeling bias.
4. After the video reaches EOF, finish any remaining reviews. The frontend then displays the measured scores and enables **Download metrics_live.json**. The file is also saved automatically in the output directory and its `runs/<run-id>/` archive.

The queue includes accepted matches and uncertain/rejected proposals with an actual compared exemplar. New identities with no candidate have no pair to review. Including non-accepted proposals provides negative predictions as well as positive predictions. Each frozen pair uses the exact images from that observation; review images are kept in the run database independently of the normal bounded preview cache. Labels do not change the pipeline's predictions or gallery. Every answer is saved immediately in SQLite; final labels are also exported to `match_reviews.csv` beside both final metric files.

While processing or review is incomplete, only **`metrics_pending.json`** is written. **`metrics_live.json` is published only after EOF and every queued pair has a label.** Keep the terminal/server running until review finishes; closing/reopening the browser is fine, but closing the server early interrupts the session. The current UI does not resume an interrupted server session; its saved database still retains the images and labels. Review time does not reduce the reported processing throughput.

The final report puts actual human-scored results under **`accuracy.match_decisions`**:

| Field | Definition |
|---|---|
| `accuracy` | `(TP + TN) / reviewed comparisons` |
| `precision` | `TP / (TP + FP)` among accepted model matches |
| `recall` | `TP / (TP + FN)` among human-positive candidate pairs |
| `f1` | `2 TP / (2 TP + FP + FN)` |
| `tp`, `fp`, `fn`, `tn` | Counts comparing the frozen model decision with your answer |
| `coverage`, `reviewed`, `total`, `pending` | Review completeness and sample counts |

Here model `MATCH` is positive; `UNCERTAIN` and compared `NO_MATCH` are **not accepted**, counted as negative for this stated operating policy. Your same-object/different-object answer supplies the reference label. Recall covers only candidate pairs actually presented, not objects missed by detection or candidates missed by the index. These are comparison-level scores, not exhaustive identity-clustering, cross-camera, box/mask, rank-1/mAP, IDF1, or MOTA accuracy. Repeated observations may be correlated. If a denominator has no examples, its ratio remains undefined (`null`); if the video generates no comparisons, the report explicitly says `no_comparisons`.

### Terminal-only replay

Use `replay` to process a local recording through the same foreground, tracking, crop, descriptor, and identity-decision code used by camera mode. No camera, SSH connection, running LAN peers, or prior `init` command is required. This mode runs in the terminal and exits at the end of the file; it does not start the web monitor.

```powershell
Set-Location E:\home\gitthings\reid
.\.venv\Scripts\python.exe -m reid replay --video "E:\videos\recording.mp4" --out experiments/results/video-01
```

On Linux, from the project root:

```bash
.venv/bin/python -m reid replay --video "/home/sub/videos/recording.mp4" --out experiments/results/video-01
```

Replace the example video path with your file. Quote paths containing spaces. Choose a **new output directory for each run**; existing directories are rejected to protect results. Omitting `--out` creates a unique `experiments/results/video-<timestamp>-<id>` directory automatically.

The output directory contains:

| File | Contents |
|---|---|
| `metrics_live.json` | Final counts, decisions, processing times/throughput, input details, and accuracy availability |
| `runs/<run-id>/metrics_live.json` | Preserved report compatible with camera-mode exports |
| `runs/<run-id>/observations.jsonl` | Decisions, source frame indexes, video times, predicted IDs, and preview references |
| `runs/<run-id>/labels.csv` | Blank identity-label template for independent annotation |
| `replay_config.yaml`, `state.sqlite`, `key.pem` | Isolated local settings, gallery/preview state, and run key |

The input metadata includes the source file's SHA-256, FPS, decoded frame count, decoded duration, processing dimensions, and number of identities in the resulting local gallery. A normal EOF finalizes `state: complete`. Ctrl+C finalizes `state: interrupted`; a processing failure finalizes `state: failed` once the runtime has initialized. An unreadable input fails promptly instead of reconnecting forever. A hard process kill retains only the most recent checkpoint.

### Replay timing and optional settings

Every decoded frame is processed as fast as the machine allows. Tracking, observation intervals, and background warmup use **video time**, computed as frame index divided by file FPS, rather than processing wall time. Processing FPS describes machine throughput and is distinct from the recording FPS. The timeline assumes constant frame rate; convert variable-frame-rate recordings to constant frame rate for timing-sensitive experiments. If FPS metadata is absent or incorrect, supply `--fps 30` (or the correct rate). This changes the assumed timeline; it does not throttle processing or drop frames.

The default warmup is the first four **video seconds**, using the same 960×540 processing size and visual defaults as the application. Prefer a stationary-camera recording with a clear background during warmup. A clip shorter than warmup produces a report with zero evaluated frames and a warning. Override warmup when appropriate:

```powershell
.\.venv\Scripts\python.exe -m reid replay --video "E:\videos\recording.mp4" --warmup-seconds 3
```

To use the current C1 visual settings (including its optional GrabCut setting):

```powershell
.\.venv\Scripts\python.exe -m reid replay --video "E:\videos\recording.mp4" --config config/lan/C1.yaml
```

The supplied config contributes vision, retrieval, quality, and preview settings. Replay replaces its membership, camera source, database path, and key with a fresh isolated `VIDEO` node; it also clears cross-camera transitions. Existing LAN databases and identities remain untouched. `camera.fps` does not limit file replay: the file FPS or `--fps` supplies its media clock. Observation timestamps are the run epoch plus video time, not the original recording date.

Performance statistics are available immediately. Re-ID precision/recall/F1 still require independently labeled physical identities; predictions cannot grade themselves. Fill the archived `labels.csv` and use [`live-evaluate`](../LAN-RUNBOOK.md#obtain-actual-live-re-id-accuracy). Single-video scores describe within-recording identity consistency, not cross-camera accuracy; box/mask IoU and ranked mAP require the separately labeled evaluation inputs described in the [research suite guide](../experiments/RESEARCH.md).
