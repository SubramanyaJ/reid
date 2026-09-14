# Classical Multi-Camera Vehicle Re-Identification

## Process a stored MP4 on one machine

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

Performance statistics are available immediately. Re-ID precision/recall/F1 still require independently labeled physical identities; predictions cannot grade themselves. Fill the archived `labels.csv` and use `live-evaluate` as described below. Single-video scores describe within-recording identity consistency, not cross-camera accuracy; box/mask IoU and ranked mAP require the separately labeled evaluation inputs described in the research-suite section.

## Run the existing three-node LAN setup

Use this section for the machines already configured in this project. **Do not run `reid init` again.** The later provisioning section is only for a new, separate installation.

| Node | Machine and project root | Config on that machine | Monitor |
|---|---|---|---|
| C1 | Local Windows: `E:\home\gitthings\reid` | `config/lan/C1.yaml` | http://10.78.223.22:9000 |
| C2 | `sub@10.78.223.1`: `/home/sub/reid` | `config/lan/C2.yaml` | http://10.78.223.1:9000 |
| C3 | `sub@10.78.223.84`: `/home/sub/reid` | `config/lan/C3.yaml` | http://10.78.223.84:9000 |

The configured C1 address is **10.78.223.22**. The older `10.78.233.22` address was a typo. Each machine uses port **9000**; ports 9001/9002 belong to the separate single-machine smoke setup.

### 1. Before starting

Turn on C2 and C3, join the same LAN, and ensure the three listed IP addresses are still assigned. Each machine needs its own installed Python environment, its own existing config/key, and the same application version. For the new live metrics and graceful shutdown, update **`src/` and `scripts/` on each machine** from this revision; preserve that machine's `config/lan/`, `data/`, and `.venv/`. With the existing editable install, Python source changes take effect at restart. Do not copy the Windows virtual environment to Linux.

Current camera choices are C1 `source: 1`, C2 `source: 0`, and C3 `source: null`. Thus the existing deployment has **three nodes and two enabled cameras**. To use a camera on C3, edit `/home/sub/reid/config/lan/C3.yaml` on C3 and set `camera.source` to its actual device index or stream URL before starting. `null` is valid for a peer without a camera.

On Windows, check connectivity (these commands do not start nodes):

```powershell
Set-Location E:\home\gitthings\reid
ssh -o BatchMode=yes -o ConnectTimeout=10 sub@10.78.223.1 'cd ~/reid && .venv/bin/python -m reid --help'
ssh -o BatchMode=yes -o ConnectTimeout=10 sub@10.78.223.84 'cd ~/reid && .venv/bin/python -m reid --help'
```

### 2. Start all three from local PowerShell

```powershell
Set-Location E:\home\gitthings\reid
.\scripts\lan_cluster.ps1 start
```

Wait for startup, then inspect all nodes:

```powershell
.\scripts\lan_cluster.ps1 status
```

Open http://10.78.223.22:9000/ and the other monitors in the table. Camera nodes need about four seconds of background warmup; keep the scene clear during this period. A camera-less C3 can be healthy while its frame panel remains empty. The helper starts detached processes, so closing PowerShell or SSH does not stop them.

If PowerShell blocks the script, use this per-invocation command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\lan_cluster.ps1 start
```

### 3. Stop all three and collect metrics

```powershell
.\scripts\lan_cluster.ps1 stop
```

The updated helper first requests graceful shutdown over each machine's loopback interface, allowing camera cleanup and final metric export. If this fails, it falls back to process termination; the last checkpoint survives but may say `running`. `state: complete` means normal shutdown, not that accuracy has been independently established. The controller attempts all three hosts even if one fails, then reports failures. A machine that is powered off cannot be contacted.

Every node writes these files beside its configured database:

```text
data/lan/C1/metrics_live.json                  latest run (C2/C3 on those nodes)
data/lan/C1/runs/<run-id>/metrics_live.json    preserved per-run report
data/lan/C1/runs/<run-id>/observations.jsonl   decisions with unique observation IDs
data/lan/C1/runs/<run-id>/labels.csv           observation_id,truth_id template
data/lan/C1/peer.log                         managed process log
```

Metrics are written at initialization, checkpointed during frame processing at approximately five-second intervals, and finalized at normal shutdown. Performance counts include frames, warmup, admitted candidate boxes, confirmed track-frames, errors, and decision categories. Processing mean/min/max time and throughput are measured; accuracy fields are `null` until independent labels exist. Remote nodes need the updated code to produce these files. No real camera run was performed merely to demonstrate this export.

To copy preserved remote runs after an experiment, choose a new collection directory each time:

```powershell
New-Item -ItemType Directory -Force experiments/collected/session-01/C2,experiments/collected/session-01/C3
scp -r sub@10.78.223.1:~/reid/data/lan/C2/runs experiments/collected/session-01/C2/
scp -r sub@10.78.223.84:~/reid/data/lan/C3/runs experiments/collected/session-01/C3/
```

### Run just one node, or troubleshoot

Use one of the following instead of the cluster start command:

```powershell
# Local C1, managed background process
.\.venv\Scripts\python.exe scripts/lan_node.py start --node C1
# Remote C2
ssh sub@10.78.223.1 'cd ~/reid && .venv/bin/python scripts/lan_node.py start --node C2'
# Remote C3
ssh sub@10.78.223.84 'cd ~/reid && .venv/bin/python scripts/lan_node.py start --node C3'
```

Replace `start` with `status` or `stop` to control that node. For a foreground debugging session, first stop its managed process, then run `python -m reid run --config config/lan/Cx.yaml` with that machine's environment and node ID; Ctrl+C ends the run and exports metrics.

| Symptom | Check |
|---|---|
| SSH timeout | Remote power, LAN address, route, and SSH service. The controller does not power on computers. |
| Status is null | Read `data/lan/Cx/peer.log` on that host; check its `.venv`, config path, and startup error. |
| Local monitor works, peers unavailable | TCP 9000 must be reachable between hosts; check firewalls and `members[].url`. `node.host: 0.0.0.0` binds interfaces; it is not a peer address. |
| Camera has no image | Check `camera.source`, device permission, and whether another program owns the camera; `null` intentionally disables it. |
| Port already occupied | Stop the existing managed/foreground copy before starting another. |
| Identity state differs | Wait for peer synchronization and inspect errors; preserve the existing keys and databases. |
| IP addresses changed | Membership URLs define the existing deployment. Use its original addresses, or deliberately provision a separate configuration/database set as described below; do not edit one member list alone. |

For local logs: `Get-Content data/lan/C1/peer.log -Tail 50`. For C2: `ssh sub@10.78.223.1 'tail -n 50 ~/reid/data/lan/C2/peer.log'`.

### Obtain actual live Re-ID accuracy

After stopping, fill the `truth_id` column in each run's `labels.csv` using independently observed physical identities. The same real object must have the same truth label across cameras. Do not copy predicted `global_id` values into truth labels. Keep synchronized source recordings or independent observer notes so labels can be checked: the application does **not** automatically record full video, and its thumbnail cache is bounded. `observations.jsonl` provides timestamps, local IDs, prediction evidence, and preview references for annotation.

Combine the desired runs' label rows into one CSV with a single header. Then score the selected archives; substitute actual run IDs and collected paths below:

```powershell
.\.venv\Scripts\python.exe -m reid live-evaluate `
  --run-dirs data/lan/C1/runs/<C1-run-id> experiments/collected/session-01/C2/runs/<C2-run-id> `
  --labels experiments/collected/session-01/labels.csv `
  --out experiments/results/live-session-01/metrics_live.json
```

The annotated report contains pairwise identity precision/recall/F1, cross-camera pairwise scores, annotation coverage, and resolution coverage. It scores resolved annotated observations and keeps abstentions visible through coverage; it is **not** rank-1, mAP, IDF1, or MOTA. Repeated frames are correlated, so these pairs should not be interpreted as independent statistical samples. Blank truth labels are excluded, an entirely blank template is rejected, and partial annotation coverage is reported. Run directories are preserved. For actual box/mask and ranked-retrieval accuracy, use the labeled frame/crop inputs of the offline research suite described below.



A runnable research MVP that associates moving vehicle observations across independent cameras using classical vision, a handcrafted descriptor, random-hyperplane LSH, font-template plate recognition, and soft contextual evidence. Identical Python peers replicate signed identity-event provenance through a majority-certificate ledger. A peer may process a camera or run solely as a consortium participant.

**Implementation status:** the MVP has been deployed on three LAN peers with two live cameras. The whole-object filtering and thumbnail update includes regression tests for isolated fingers, connected hand silhouettes, nearby-object separation, size/foreground gates, component-specific crops, preview retention, and API references. Live operation checks establish that the software runs and replicates signed events; they do not establish hand/vehicle recognition accuracy. See `LAN-RUNBOOK.md` for this deployment’s start/stop commands and camera indices.

This is **not production-grade vehicle Re-ID**, an accurate general-purpose plate reader, or a Byzantine fault tolerant blockchain. Consensus agrees on signed claims and their order; it does not prove that a physical vehicle was identified correctly.

## 1. Scope and architecture

The initial arrangement can be C1 with a camera, C2 with a camera, and C3 without a camera. The implementation accepts any fixed positive number of configured peers. Every node uses the same entry point, serves the same network operations, validates the same blocks, and hosts the same monitoring interface. There is no dedicated validator, registry server, or permanent leader.

```text
Capture thread (optional on every peer)
  camera index / HTTP MJPEG / RTSP
    -> processing limiter (at most 24 FPS)
    -> 3–5 s background warm-up after connection
    -> MOG2 or KNN -> shadow/foreground separation -> morphology
    -> connected components -> persistent motion hypotheses
    -> geometry/persistence validation -> confirmed local tracks
    -> optional GrabCut -> observation quality
    -> MVSV-G descriptor ---------------------> LSH -> exact cosine
    -> optional ORB/SIFT local evidence ----------------------|
    -> geometric plate proposals -> template OCR -> aggregation|
    -> soft camera/time context ------------------------------|
    -> final evidence fusion -> match/new UUID/abstain
    -> signed event + separate signed-hash-bound feature sidecar

Async peer service (always present)
  pending-event gossip <-> equal peers
  scheduled proposer -> signed votes -> majority certificate
  verified blocks -> local replicated ledger
  verified state sidecars -> local bounded identity galleries
  HTTP status, camera JPEG, and monitor interface
```

The implementation separates these responsibilities:

| Package under `src/reid/` | Responsibility |
| --- | --- |
| `cameras` | Source opening, reconnection, warm-up timing, rate limiting |
| `detection` | Background models, foreground/shadow masks, raw components |
| `tracking` | Temporal hypotheses, geometry validation, prediction and local IDs |
| `segmentation` | Mask extraction and conservative optional GrabCut |
| `features` | Observation quality, MVSV-G, candidate-specific ORB/SIFT |
| `plate` | Geometric localization, rectification, segmentation, templates, fuzzy aggregation |
| `lsh` | Seeded random-hyperplane candidate index |
| `reid` | Galleries, independent context scores, final fusion |
| `provenance` | Canonical serialization, Ed25519, SHA-256, block verification |
| `consensus` | Height-rotating proposal and persistent majority voting |
| `network` | HTTP endpoints, transaction gossip, verified catch-up |
| `storage` | Separate operational, identity, and committed-ledger SQLite tables |
| `visualization` | Local monitor HTML/CSS/JavaScript |
| `metrics` | Reproducible offline research suite, labeled frame/crop replay, LSH comparison, pairwise evaluation |

`runtime.py` wires the subsystems together. `cli.py` contains actual project entry points. `tests/` covers the independent components and ledger lifecycle. `experiments/` contains an evaluation CSV example; generated results default to `experiments/results/`.

### Non-ML constraint

There are no learned detectors, embeddings, vocabularies, OCR engines, model downloads, or training steps. The code does not use YOLO, neural networks, PyTorch, TensorFlow, ONNX, Deep SORT, CLIP, Tesseract, or BoVW. MOG2/KNN are OpenCV classical adaptive background subtraction algorithms, not learned vehicle classifiers. ORB/SIFT are classical local features. Plate templates are rendered glyphs or manually supplied bitmap characters. Random Gaussian LSH hyperplanes are seeded projections, not trained parameters.

## 2. Installation

Use Python 3.10 or newer; Python 3.11/3.12 is a sensible starting environment. OpenCV, NumPy, SciPy, Matplotlib, cryptography, FastAPI, HTTPX, Uvicorn, PyYAML, and pytest are installed through `requirements.txt` and the editable package definition in `pyproject.toml`. A Python virtual environment is local to each laptop. There is no Node.js/frontend build step.

### Linux

Run from a clone/copy of this repository; change the first path if your checkout differs.

```bash
cd ~/reid
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m reid --help
```

On Debian/Ubuntu systems missing venv or OpenCV shared libraries, install the applicable system packages first:

```bash
sudo apt-get update
sudo apt-get install python3-venv libgl1 libglib2.0-0
```

### Windows PowerShell

The project root for this checkout is `E:\home\gitthings\reid`.

```powershell
Set-Location E:\home\gitthings\reid
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m reid --help
```

If local policy blocks activation, invoke the environment directly; activation is not required:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m reid --help
```

Do not run multiple Uvicorn workers against one node database. The CLI explicitly uses one worker. To stop a node, press Ctrl+C in its terminal.

## 3. Provisioning a new deployment (skip for the existing LAN)

### Three local peers for a first smoke check

The following command is identical on Linux and Windows after activating the environment:

```text
python -m reid init --nodes C1 C2 C3 --out config/local
```

This creates `config/local/C1.yaml`, `C2.yaml`, `C3.yaml`, and three private key files under `config/local/keys/`. Each configuration includes the complete public membership list. Local addresses are `127.0.0.1:9000`, `:9001`, and `:9002`. Each node gets its own SQLite database under `data/<node>/`. All cameras initially have `source: null`.

The command refuses to overwrite an existing configuration/key. Use a different output directory when constructing a separate consortium. Node names are configurable; the three names are an example, not a protocol limit. A single-node consortium is also supported and has quorum one.

Open three terminals, activate the environment in each, and run one command per terminal:

```text
python -m reid run --config config/local/C1.yaml
python -m reid run --config config/local/C2.yaml
python -m reid run --config config/local/C3.yaml
```

Open the local monitors:

- C1: <http://127.0.0.1:9000>
- C2: <http://127.0.0.1:9001>
- C3: <http://127.0.0.1:9002>

With no camera and no events, a healthy chain remains at genesis (height zero). Nodes do not generate empty blocks to animate the UI. Configure cameras to produce real observations, or run the independent offline demonstration below.

### New LAN deployment on different machines

Provision this example **once**, with the actual laptop IP addresses substituted, then distribute the resulting files:

```text
python -m reid init --nodes C1 C2 C3 --hosts 192.168.1.101 192.168.1.102 192.168.1.103 --port 9000 --out config/lan
```

When hostnames/IPs differ, each uses TCP port 9000. Repeated hosts receive successive ports. Copy the repository to every laptop. Place the generated `C1.yaml` and `keys/C1.pem` on laptop 1, `C2.yaml` and `keys/C2.pem` on laptop 2, and the corresponding C3 files on laptop 3, preserving the `config/lan/` layout. Each node configuration includes **all public** members, but each laptop needs only its own private key. Keep the provisioning key copies securely or remove them manually after securely transferring ownership; do not check private keys into Git.

On laptop 1, run:

```text
python -m reid run --config config/lan/C1.yaml
```

Use the corresponding C2/C3 config on the other laptops. Allow the configured TCP port through each host firewall for the trusted peer network. `node.host` is the listening bind address; each `members[].url` is the reachable peer address. `0.0.0.0` is a bind address, not a peer URL.

**Membership is fixed for one chain.** Every node must use exactly the same member IDs, public keys, and URL strings, because those values define genesis. Changing membership or addresses requires a new consortium/database in this MVP; there is no membership reconfiguration protocol. Do not delete only one peer's votes while keeping the rest of its identity/ledger in service: durable votes are part of consensus safety.

Peer HTTP endpoints and the monitoring UI assume a trusted LAN. Events, proposals, and votes are cryptographically authenticated, but HTTP transport, camera previews, feature packets, and status endpoints are not access-controlled or encrypted by this application. Status responses are advisory; signatures and certificates establish acceptance. Use an isolated lab network, VPN, or externally managed authenticated TLS proxy for the intended deployment. Never treat a member's signature as proof that its reported identity is physically correct.

## 4. Configuration and cameras

Edit the generated node YAML. `config/config.yaml` is an annotated template, not a ready-to-run provisioned identity; its membership is intentionally empty. Paths in YAML resolve relative to that YAML file, making a copied `config/local/` or `config/lan/` layout portable. Feature weights and descriptor profile must agree across peers. Changing a descriptor profile in an existing database is rejected to prevent incompatible galleries.

Example camera configuration:

```yaml
camera:
  source: 0
  width: 960
  height: 540
  fps: 20
  warmup_seconds: 4
  reconnect_seconds: 3
```

Source examples:

```yaml
# OpenCV local camera index; use an integer, not "0".
source: 0

# DroidCam-style HTTP/MJPEG; use the endpoint your phone app exposes.
source: "http://192.168.1.120:4747/video"

# RTSP; use the actual camera stream path and credentials if required.
source: "rtsp://192.168.1.121:554/stream1"

# Camera-less peer, still fully participating in ledger and gallery synchronization.
source: null
```

Only place one `source` entry in the actual YAML. Local video-file paths are also accepted by OpenCV for manual experiments; at EOF the capture loop reconnects and repeats with fresh warm-up. The offline synthetic demo does not require a camera or network service.

The capture loop clamps processing to at most 24 FPS, including frame processing time. It attempts to request the configured capture resolution and resizes delivered frames to the configured analysis resolution. Network streams use OpenCV's FFmpeg backend with five-second open/read timeouts; backend/codec support is platform-dependent. A failed source enters `DISCONNECTED` and retries. Every fresh connection resets the background model and local hypotheses, preserves the monotonic local ID counter, and warms up for the configured 3–5 seconds. A blocked device driver may still delay shutdown beyond the requested timeout. Camera failures do not stop the separate peer service.

Use a stationary camera and let an initially unobstructed scene warm up. There is no manually configured ROI, homography, or perspective calibration. Camera motion and unstable exposure damage background subtraction. The FPS cap limits processing, not the remote camera's encoder rate. Reading is synchronous and some capture backends may buffer frames; low-latency capture is backend-dependent.

Selected settings:

| Setting | Default | Meaning |
| --- | --- | --- |
| `detection.method` | `MOG2` | `KNN` is the alternative |
| `detection.history` | 500 | Background model history |
| `detection.min_component_fraction` | 0.0008 | Resolution-relative component floor, with nine-pixel lower bound |
| `detection.grabcut` | false | Refine only confirmed candidates |
| `tracking.min_hits` | 6 | Matched observations required for promotion |
| `tracking.max_missed` | 14 | Temporary missing-frame allowance |
| `reid.min_quality` | 0.42 | Observation admission threshold |
| `reid.observation_interval` | 1.5 s | Maximum descriptor/OCR observation frequency per track |
| `reid.gallery_size` | 8 | Maximum descriptor exemplars per identity |
| `features.local_verifier` | `ORB` | `SIFT` or `NONE` also supported |
| `lsh.tables / bits / seed` | 8 / 12 / 17 | Reproducible hyperplane families |
| `lsh.multiprobe` | true | Probe all one-bit neighbors as well as exact bucket |
| `lsh.brute_force` | false | Bypass LSH for comparative experiments |
| `reid.match_threshold` | 0.82 | Final match-score threshold |
| `reid.uncertain_threshold` | 0.68 | Abstention band lower threshold |
| `reid.visual_floor` | 0.60 | Minimum visual evidence, even with plate/context support |
| `reid.margin` | 0.035 | Minimum separation between viable top candidates |
| `plate.min_confidence / min_readings` | 0.58 / 2 | Track plate evidence requirements |
| `consensus.interval_seconds` | 2 | Delay between peer/consensus passes |
| `consensus.max_transactions` | 64 | Maximum transactions in a proposed block; protocol cap 128 |
| `network.sync_batch` | 32 | Missing blocks fetched per peer per pass |

These are heuristic starting values. They are not calibrated performance claims. All nodes need compatible descriptor settings; camera and local quality settings may differ.

## 5. Classical vision and temporal tracking

MOG2 is the primary foreground model. Shadow pixels (OpenCV value 127) are preserved as a separate mask and reported in the monitor; only strong foreground pixels become components. A 3×3 elliptical opening/closing removes isolated noise conservatively. Small enclosed holes are filled. Connected regions must contain a substantial interior core measured by a distance transform; thin disconnected fragments are removed, while fingers/details attached to a substantial palm/object retain their silhouette. No large dilation joins nearby objects. Image-area-relative component filtering follows.

Connected components produce **raw foreground regions**. They are not immediately accepted as vehicles. The tracker first creates tentative hypotheses and solves one-to-one associations using the Hungarian algorithm. The cost combines predicted centroid distance, bbox IoU, and logarithmic width/height consistency. A constant-velocity estimate updates from measured centroids. Bounded temporary gaps coast with prediction instead of forcing new IDs.

Whole-object promotion uses six matched observations, hard normalized width/height floors, bbox area, actual foreground area, mask occupancy, aspect ratio, and residual-based motion consistency. The perspective envelope can only tighten the area floor near the bottom of the image; it can no longer relax the floor for small regions near the top. Small fragments are excluded from association so an established whole-object track cannot shrink into a finger-sized detection. This is geometry, not a semantic vehicle or hand classifier, and other sufficiently large moving objects can still pass.

The tracker intentionally avoids merging components solely because they are nearby. If a large component contains the predicted centers of multiple established objects, both hypotheses coast through the ambiguous region. The component does not collapse them into one ID. This also means fragmented vehicles may produce multiple hypotheses: the MVP favors conservative separation over aggressive fragment fusion. Long occlusion, crossing similar objects, and large mask changes can still cause fragmentation or identity switches.

Track states are `TENTATIVE`, `CONFIRMED`, `LOST`, and `DELETED`. Hypotheses have internal IDs; only validated tracks receive IDs such as `C1-T00001`. The next local counter persists in SQLite. A reconnect or restart starts fresh trajectories without reusing old local IDs. Full motion trajectories are operational in-memory state, not replicated global state.

Optional GrabCut starts with the confirmed candidate crop and foreground-derived seeds. The refined mask is retained only if area and overlap remain compatible with the original. OpenCV errors or poor refinement fall back to the original mask. GrabCut never creates detections.

Observation quality combines relative crop size, Laplacian sharpness, mask occupancy, clipping visibility, and motion stability. Overlapping tracks receive an occlusion penalty; very small, clipped, or sparsely segmented crops are strongly penalized. This is a heuristic blur/occlusion screen, not a visibility oracle. Poor observations are logged as `SKIP_QUALITY` and do not enter the gallery.


### Whole-object filtering for the hand demonstration

The following are the research revision's tighter settings. They are applied to the default template and local C1 configuration. Remote hosts have not been changed or started. The source camera indices remain C1=`1`, C2=`0`; C3 remains camera-less. Existing configs explicitly override defaults, so changing Python defaults alone does not retune a previously generated YAML. Apply these settings on each other host when you next deploy there.

```yaml
detection:
  min_component_fraction: 0.0008
  min_core_radius_fraction: 0.015
  max_hole_fraction: 0.001
  grabcut: true
tracking:
  min_hits: 6
  min_vehicle_fraction: 0.008
  max_vehicle_fraction: 0.40
  min_width_fraction: 0.065
  min_height_fraction: 0.10
  max_width_fraction: 0.80
  max_height_fraction: 0.85
  min_foreground_fraction: 0.004
  min_fill_ratio: 0.30
  min_aspect_ratio: 0.4
  max_aspect_ratio: 3.5
  min_motion_consistency: 0.4
visualization:
  thumbnail_limit: 300
  thumbnail_size: 240
```

At 960×540, an integer bbox must be at least **63 pixels wide and 54 pixels high**, with area at least **4,148–5,184 pixels** depending on vertical location, at least **2,074 foreground pixels**, and at least 30% bbox occupancy. Maximum width is **768 pixels**, maximum height **459 pixels**, and maximum area **207,360 pixels** (40% of the frame). All limits apply together. The interior-core radius remains about **8 pixels**. A whole hand with a connected palm can pass, while a thin isolated finger should fail. Larger floors reject more distant objects as well as fragments; tighter ceilings reject close objects as well as oversized blobs. The `min_vehicle_fraction` name is retained for YAML compatibility and acts as a hard whole-object area floor.

Candidate crops use only the associated connected component's mask, not every foreground pixel inside its bounding box. Optional GrabCut runs only after a stable candidate exists, with the existing fallback rules. The live overlay draws the selected component contour in cyan within the track bbox. DETECTION now counts geometry-qualified candidate regions; RAW REGIONS shows the earlier segmentation count. Lost tracks can coast briefly after an object disappears.

The hand demonstration changes the desired object scale, not the non-ML requirement. This is **not semantic hand detection**: a large arm, face, or other moving region can satisfy geometry too. A stationary hand may be absorbed into the background; physically touching objects may remain one component. A palm that is not foreground cannot be reconstructed reliably from disconnected fingers without additional scene assumptions. Keep the camera stationary and allow the four-second warm-up before testing.

## 6. MVSV-G visual descriptor and retrieval

MVSV-G is a **602-dimensional `float32` vector** defined by this repository, not a claim of a validated standard descriptor. The crop is letterboxed to 128×80 with its aspect ratio preserved. Statistics operate on the foreground mask; eroded interior masks reduce boundary contamination in texture and gradient extraction.

| Block | Dimension | Construction |
| --- | ---: | --- |
| Color | 180 | HSV 12/8/8 and Lab 8/8/8 histograms over whole crop and two horizontal bands; 2×2 coarse Lab mean/std |
| Texture | 54 | Radius 1/2/3 rotation-invariant uniform eight-neighbor LBP, ten bins each; eight-bin log local variance for each radius |
| Structure | 332 | Compact 4×8-cell unsigned nine-bin gradient histograms (288); edge orientation/density (10); 6×4 silhouette plus row/column occupancy (34) |
| Region statistics | 36 | Six-channel HSV/Lab mean, std, interquartile range; 15 off-diagonal covariance values; occupancy, crop aspect, gradient magnitude |

Histogram blocks use square-root frequency normalization. Each major block is independently L2-normalized, multiplied by its configurable weight, concatenated, and finally L2-normalized. No camera ID, timestamp, plate text, transition probability, or position is included. The profile hash covers the descriptor version and weights; it is separate from each observation's feature hash.

Random-hyperplane LSH is implemented from scratch:

```text
table t, bit b: h[t,b](x) = 1 if dot(r[t,b], x) >= 0, otherwise 0
r[t,b] ~ Gaussian, drawn from a seeded NumPy generator
```

The default index has eight tables with twelve bits each. Insertions map `(global_id, event_id)` to buckets; replacement and eviction remove stale bucket entries. Query results are deduplicated. Optional multi-probe checks every bucket with Hamming distance one from the original signature. There is no exhaustive fallback in LSH mode: candidate misses are exposed rather than hidden. `brute_force: true` enumerates all gallery entries.

LSH only generates candidates. For every retrieved identity, exact cosine similarity is computed against all its retained exemplars, and the strongest exemplar supplies candidate-specific local verification. ORB or SIFT ratio-test correspondences are screened for deterministic median-translation consistency. Sparse correspondences yield missing local evidence. Available local evidence contributes ten percent of the visual channel. This simple geometry check is not viewpoint-invariant and may reduce a true match under large camera changes.

An identity retains up to eight useful exemplars. Nearly identical descriptors (cosine ≥0.985) replace an existing exemplar only when quality improves. Overflow uses greedy quality and descriptor diversity selection. Plate, camera, and recent observation histories are also bounded in the identity summary. Event sidecars are retained separately for provenance-bound catch-up, so **bounded galleries do not imply bounded total database size**. No frame-by-frame video archive is stored. The monitor now retains a bounded cache of masked, downscaled JPEG observation thumbnails on their originating peer, separate from gallery descriptors and the ledger.

## 7. Classical plate channel

Plate localization uses grayscale blackhat contrast, horizontal gradient energy, thresholding, morphological grouping, and rotated-rectangle area/aspect filters. Plausible regions undergo a four-corner perspective warp. CLAHE and adaptive thresholding are tried in both polarities. Character components are filtered geometrically and grouped by vertical alignment into one or multiple text lines, then sorted left-to-right within each line.

Characters are normalized to 24×40 and compared using normalized correlation to hand-rendered OpenCV font templates. Several classical font faces/stroke widths provide a small deterministic template bank. Optionally set `plate.template_directory` to a directory of manually supplied `A.png` through `Z.png` and `0.png` through `9.png`. Supplied glyphs may have either polarity. These are character templates, not a trained recognition model or labelled vehicle embedding database.

The selected character score and runner-up margin contribute to reading confidence. A track retains up to 24 readings. Normalized text strips separators and uppercases alphanumeric characters. Fuzzy comparison uses weighted Levenshtein distance with reduced substitution costs for common ambiguities (`0/O/Q`, `1/I/L`, `2/Z`, `5/S`, `8/B`, `6/G`). Canonical text is a confidence-weighted medoid with a support requirement.

Plate states are `UNKNOWN`, `SUPPORTED`, and `CONFLICTING`. Missing/low-confidence readings remain `UNKNOWN`; disagreement can produce `CONFLICTING`. Only supported states produce a plate comparison score. Missing evidence remains `None`, never a fabricated zero. Supported fuzzy plate matches can independently nominate identities that visual LSH failed to retrieve, but exact visual verification and the visual floor still apply.

This template recognizer is deliberately limited. Real embossed fonts, dirt, screws, borders, skew, joined characters, non-Latin scripts, unusual layouts, and tiny plates will often fail. Multi-line support means line segmentation exists; it does not imply reliable recognition of every regional plate design. Re-ID can continue using visual evidence alone.

## 8. Context, fusion, and identities

The evidence record keeps `visual_score`, `plate_score`, `temporal_score`, and `spatial_score` separate until final fusion. Within one camera, normalized position distance, scale ratio, and elapsed time produce broad soft plausibility. Across cameras, raw coordinates are never compared. The default cross-camera evidence is intentionally neutral/broad. Optional transitions can provide broad timing windows and prior probabilities:

```yaml
context:
  transitions:
    C1->C2:
      min_seconds: 3
      max_seconds: 180
      probability: 0.7
    C2->C1:
      min_seconds: 3
      max_seconds: 240
      probability: 0.6
```

Out-of-window observations receive smoothly reduced timing support, not rejection. Clock skew is not corrected by the application; synchronize laptop clocks for sensible event histories and context. Vehicles need not remain continuously visible between cameras.

Available channels are fused by a normalized weighted sum. Visual, temporal, and spatial weights are 0.78, 0.07, and 0.05; a supported plate channel receives weight `0.35 × plate_confidence`. Missing plate evidence is omitted from both numerator and denominator. A strong supported plate contradiction (similarity <0.45 at confidence ≥0.65), or visual similarity below the visual floor, forces `NO_MATCH` regardless of favorable context.

Results are `MATCH`, `NO_MATCH`, or `UNCERTAIN`. A narrow score margin between two viable identities becomes `UNCERTAIN`. New tracks only allocate a new UUID when the decision is `NO_MATCH`. `UNCERTAIN` tracks remain unassigned and retry on a later eligible observation. An already assigned local track is compared to its bound identity; inconsistent later observations are skipped instead of repeatedly switching IDs. Identities assigned to other currently active local tracks are excluded from new-track matching. There is no distributed simultaneous-camera exclusivity rule.

Global IDs are `G-<UUID>` and independent of hashes. The monitor abbreviates them for space; signed events and stored state retain full UUIDs. Local and global identifiers are different namespaces. A second camera can retrieve a replicated gallery and associate its new local track with the first camera's global UUID.

Gallery state contains `global_id`, `visual_gallery`, `plate_history`, `camera_history`, `observation_history`, `last_seen`, `last_camera`, `last_position`, and `last_scale`. Local pending observations are available immediately to their originating peer; remote gallery updates require committed events and valid sidecars. Consequently, two cameras observing an unseen vehicle simultaneously can create duplicate global identities before synchronization. There is no automatic identity-merging or retroactive repair protocol. The ledger records these decisions honestly; it does not resolve semantic conflicts.

`data/<node>/decisions.jsonl` logs quality decisions and all independent evidence channels, including abstentions. Match confidence is the fused score; new-identity confidence is the complement of the strongest rejected score, or 0.5 when no candidate exists. These are heuristic scores, not calibrated probabilities. An abstention is local operational evidence and does not create an identity ledger event.

## 9. Provenance, storage, and consensus

### Signed events and off-ledger state

Each accepted identity observation produces an Ed25519-signed transaction. Event fields include event UUID, timestamp, node/camera/local/global IDs, the four evidence scores, decision, confidence, crop/mask observation hash, feature hash, plate-state hash, sidecar payload hash, and descriptor profile hash.

The ledger contains **no images, video, plate strings, or complete feature vectors**. Feature/plate/local-keypoint data travels as a separate payload whose canonical SHA-256 digest is covered by the signed event. Receivers verify membership, signatures, field schema, descriptor dimension/norm/profile, payload hash, feature bytes, plate hash, and basic bounds before admitting it. Committed sidecars must match the actual committed transaction, not merely reuse an event ID. A valid signature establishes the source of a claim, not the quality of its vision.

Serialization is UTF-8 JSON with sorted keys, compact separators, ASCII escaping, and nonfinite numbers rejected. Feature hashes use explicit little-endian float32 bytes. SHA-256 provenance hashes and LSH bit signatures have entirely different roles. The observation hash covers encoded crop bytes and its segmentation mask; full-resolution raw observation bytes are discarded after hashing in live operation, while a masked, downscaled JPEG thumbnail is retained for visual inspection. This lossy preview is a monitor aid, not a replacement for the original hash-bound crop/mask bytes. Feature sidecars and unsalted plate hashes can still disclose sensitive information; excluding raw images from blocks is not anonymization.

### SQLite layout

Each node independently opens its own SQLite database in WAL mode with full synchronous durability. Tables separate:

- Operational state: settings/profile markers, local ID counter, sync cursors, persistent vote/proposal records, pending events.
- Identity state: bounded summaries plus signed feature/plate sidecars and idempotent application markers.
- Committed state: ordered blocks and a committed-event index.

Consensus operations use a local reentrant lock and database transactions. Votes and proposed blocks survive a restart. Gallery updates and their applied-event markers commit together. Event and sidecar archives currently grow without pruning; production retention and compaction are future work. A SQLite file is never copied from another peer and never acts as the consortium authority.

### Majority protocol

Let `N` be the configured member count and `q = floor(N/2) + 1`. Members are sorted by node ID. At height `h ≥ 1`, proposer `members[(h−1) mod N]` is scheduled. It selects pending signed events, creates one durable proposal extending its current tip, and signs the block hash. Peers verify the proposal and persist one signed vote per height. Repeated requests for the same hash return the existing vote; a different hash at that height is rejected even after restart.

The proposer collects distinct valid votes. With at least `q`, it commits and broadcasts the block with its quorum certificate. Blocks include index, timestamp, proposer ID, transactions, previous hash, block hash, proposal signature, and consensus metadata containing the votes. The block hash covers its immutable core; the certificate is independently verified, allowing equivalent valid vote subsets without changing the block identity. Duplicate voters do not count. Duplicate committed event IDs are rejected.

In the honest crash/recovery model, majority intersection and durable one-vote-per-height prevent two different blocks from obtaining honest majorities at the same height. This assumes stable membership, uncompromised keys, and retained durable votes. **This protocol is not Byzantine fault tolerant.** A faulty member may lie about observations; compromised voters can violate the assumptions. There is no proof-of-work, proof-of-stake, election service, or permanent coordinator.

**Liveness is deliberately restricted.** There is no view change or proposer replacement. A missing scheduled proposer stalls the next height even if a majority of other peers is reachable. Lack of a majority also stalls commitment. A returning proposer reuses its persisted proposal and retries; returning followers catch up. This transparent choice avoids presenting an incomplete leader-election scheme as a correct consensus protocol. A three-peer partition does not imply the two connected peers can keep producing blocks indefinitely: they will stop when the absent peer's proposer turn arrives.

### Catch-up and validation

HTTP operations are:

| Operation | Endpoint | Behavior |
| --- | --- | --- |
| Status | `GET /peer/status` | Node, tip, height, genesis fingerprint |
| Missing blocks | `GET /peer/blocks?start=H&limit=N` | Ordered bounded block batch |
| Current identity material | `GET /peer/state?after=EVENT_UUID` | Cursor-paginated committed signed sidecars |
| Event gossip | `POST /peer/transaction` | Validate and retain signed event/payload |
| Vote request | `POST /peer/proposal` | Verify proposal, persist and return signed vote |
| Commit | `POST /peer/commit` | Verify extension and certificate before storing |

The peer loop checks all configured peers, catches up a bounded batch, exchanges signed state packets, and gossips pending transactions again for retry. State scans cycle so packets missed during earlier block catch-up are revisited. Chain extension, deterministic proposer, transaction signatures, hashes, unique event IDs, and majority certificates are checked before acceptance. Conflicting history is rejected rather than overwritten. A sidecar unavailable on all online peers leaves a valid provenance block with an incomplete Re-ID gallery; block consensus itself does not guarantee off-ledger data availability.

## 10. Monitor

Every peer serves the same local monitor, using background `#181818`, accent `#ff6700`, and network color `#00F0F0`. Panels show NODE STATUS, CAMERA, DETECTION, TRACKING, RE-ID, VISUAL, PLATE, CONTEXT, LEDGER, CONSORTIUM, and CONSENSUS.

The camera image is the local source with bboxes, local IDs, abbreviated global IDs, and visual similarity where available. The page polls status once per second and JPEG previews at up to four per second; processing may run faster. It shows processing FPS, component count, tracks, candidate count, foreground/shadow fractions, peer states/heights, quorum, scheduled proposer, pending transactions, block tip, and recent decisions. The integrity indicator reflects startup verification and validated subsequent acceptance; it is not a continuous independent scan of disk corruption. Use `verify-ledger` for a complete rescan.

No-camera nodes show a disabled camera feed while maintaining normal gossip, votes, catch-up, and gallery state. The UI is read-only, uses native browser APIs, and does not expose an administrative control panel.


### Inspecting what was re-identified

The monitor now includes **RE-ID OBJECTS / RECENT DECISIONS** with the current local observation beside the exact gallery exemplar selected for comparison, camera labels, global/local IDs, decision, visual score, quality, and commit state. `UNCERTAIN`/rejected observations can be inspected without creating a ledger event. For `NO_MATCH`, the other image is a compared/rejected candidate, not a successful match. Clicking a preview opens a larger inspection dialog.

**IDENTITY GALLERY / ALL CAMERAS** shows the latest observation per identity, its source cameras, and up to three retained exemplars. The peer-only C3 monitor can show C1/C2 images. Tracking rows also have a thumbnail. Preview dimensions preserve the crop aspect ratio, mask background pixels to dark gray, and cap the longest edge at 240 pixels by default.

Thumbnails live in the additive local `thumbnails` SQLite table. At most `visualization.thumbnail_limit` observations (default 300) are retained per node, including uncertain local observations. Entries survive restart and the oldest are evicted automatically. Existing ledger blocks, signed-event schemas, feature vectors, descriptor profiles, and provenance hashes are unchanged. **No image bytes are added to the ledger or signed feature payload.**

`GET /api/objects` returns lightweight observation/identity metadata. `GET /api/thumbnails/{event_id}` returns a bounded JPEG or HTTP 404. Cross-camera image URLs point to the configured originating peer: the browser needs to reach that peer. Thumbnails are not replicated with the ledger. If the origin is offline, the image was evicted, or the event predates this change, the UI explicitly displays “Preview unavailable.” Older original crops were not retained and cannot be regenerated. When the exact matched exemplar is unavailable but a newer image of that identity exists, the comparison panel labels the fallback **Identity reference (latest)** so it is not mistaken for the exact scoring exemplar.

Preview images are visual inspection aids, not cryptographically verified reconstructions of the full-resolution observation hash. Like the live camera preview, they are accessible over the configured trusted LAN without application-level authentication. No continuous raw-video archive is introduced.

## 11. Offline synthetic demonstration

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

## 12. Tests and manual network checks

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

## 13. LSH benchmark and Re-ID evaluation

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

## 14. Limitations and future work

The following are material constraints of the actual implementation:

- **Viewpoint:** coarse color/texture tolerate small appearance changes, but front/rear/side transitions can have different silhouettes and visible paint/glass. ORB/SIFT translation consistency is especially limited across viewpoint changes.
- **Lighting:** automatic exposure, white balance, glare, headlights, shadows, and night capture distort color histograms and foreground masks. There is no photometric calibration.
- **Occlusion:** short gaps coast; long occlusions expire. Bounding-box overlap is only a crude visibility estimate. Merged blobs preserve separate established hypotheses temporarily but do not solve general occlusion.
- **Blur:** Laplacian sharpness helps reject poor observations but confounds blur with low texture and does not recover detail. Fast vehicles may leave too few useful frames.
- **Scale:** letterboxing preserves crop aspect, but low-resolution distant vehicles lose plate and texture detail. Broad scale heuristics do not fit all road perspectives.
- **Background contamination:** foreground and GrabCut errors can include road, neighboring vehicles, or cast shadows. Interior masking reduces but does not eliminate contamination.
- **Nearby vehicles and segmentation errors:** components can fragment one vehicle or merge several. The conservative association model can still duplicate tracks, swap IDs, or reject unusual shapes. There is no semantic vehicle classifier.
- **OCR errors:** fonts, alignment, non-Latin characters, multiple lines, reflective surfaces, and segmentation failures can corrupt strings. Template confidence is uncalibrated. Fuzzy ambiguity rules can also make different plates look similar.
- **Visually similar vehicles:** same model/color vehicles may be indistinguishable with these descriptors. Weak or missing plates increase false matches; abstention and local exclusivity reduce but cannot eliminate them.
- **Parked vehicles:** adaptive background subtraction gradually absorbs stationary vehicles. The system targets moving objects, not exhaustive parked-car inventory.
- **LSH candidate misses:** finite tables/bits can miss the correct identity. Multi-probe and independent plate retrieval improve opportunity, but brute force is the necessary comparison baseline.
- **Distributed identity races:** observations accepted before remote gallery arrival can create duplicate global UUIDs. Bound local IDs are not automatically reconsidered; there is no global reconciliation/merge event.
- **Consensus availability:** fixed membership, majority availability, a reachable scheduled proposer, and durable votes are required. There is no view change, BFT, fork-choice repair, dynamic membership, or decentralized truth validation.
- **Storage and transport:** event/sidecar archives grow; state scans are simple cyclic pagination; HTTP status/features/previews need external access control in a shared network. One peer process has one camera thread. Backend read buffering and CPU-heavy template matching can reduce achieved FPS.

Future classical work can improve motion-aware fragment handling, multi-hypothesis association, automatic scene-scale envelopes, hand-designed illumination normalization, richer manually authored regional glyph sets, robust local geometric verification, adaptive descriptor diversity selection, and calibrated evidence thresholds using held-out evaluation. Distributed work can add authenticated transport, off-ledger availability acknowledgments, retention/checkpoints, explicit identity merge/retraction events, and a properly specified crash-tolerant view-change protocol with corresponding safety/liveness tests. Any such extension should preserve the separation between physical identification evidence and provenance agreement.


## 15. Paper and reproducible visual metrics

The final visual-method manuscript is [`main.tex`](main.tex), using the five-section structure of the full supplied reference PDF. It contains 15 recent research papers and 11 supporting references, 20 numbered equation groups, a pipeline algorithm, six tables, and three figures. It focuses on foreground support, bounding, masked descriptors, hashing, and retrieval. [`paper.org`](paper.org) retains the earlier working draft and an appended explanation of test provenance, synthetic evaluation, and published precedents.

Run the extended suite without starting any camera or node:

```powershell
Set-Location E:\home\gitthings\reid
.\.venv\Scripts\python.exe -m reid metrics --out experiments/results/my-run
```

The same `python -m reid metrics` command works on Linux after activating its environment. Use a new output directory each time. Outputs include `metrics.json`, per-query `queries.csv`, retrieval plots, and descriptor ablations. No new dependencies are required. [`experiments/RESEARCH.md`](experiments/RESEARCH.md) explains all metrics, timing/ground-truth conventions, real crop and frame manifests, and NPZ input. Run `python -m pytest -q` for the regression suite.

The archived paper run is `experiments/results/paper-local-20260914`. It uses generated data and cannot establish real multi-camera Re-ID accuracy. It reports both the larger-vector LSH speed advantage and the small image-gallery case where exhaustive search is faster. Unknown-object threshold failures and unfavorable descriptor ablations remain in the paper. The local defaults and C1 YAML now have stricter minimum and maximum box limits; no remote hosts were modified or started.

## Build the final paper

`main.tex` is the final manuscript, organized using the supplied full reference PDF. Its existing author block is preserved. `paper_figures/` contains its two figure assets; keep that directory beside the TEX file. Compile from the project root with a LaTeX installation containing IEEEtran, the standard AMS/algorithm packages, microtype, xurl, and balance:

```text
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

No BibTeX step is needed because the 26 references are included in `main.tex`. The compiled preview was checked as a nine-page, two-column paper. `paper.org` retains the working draft and now ends with a detailed test-provenance appendix, reproduction instructions, and short quotations from published evaluation precedents. The archived tables describe generated inputs, not measurements from live LAN footage.
