This file is in the root of the project, build from here.
Build the complete runnable MVP for the following project. **Create the actual files and code; do not merely describe them.** Leave execution/testing commands for the user to run manually, but provide the commands in the README.

# PROJECT

**Non-ML Multi-Camera Vehicle Re-Identification Using Classical Vision, Handcrafted Features, LSH, Classical License-Plate Recognition, and a Permissioned P2P Provenance Ledger**

The system consists of multiple identical Python nodes running on laptops. Each node may have a camera configured or may operate without one.

The initial physical setup is three laptops:

* Laptop 1: camera + peer
* Laptop 2: camera + peer
* Laptop 3: peer

However, **do not hard-code the system to three nodes**.

All nodes run the same software and act as equal peers. There is no dedicated validator, server, master, or permanent leader.

# NON-ML REQUIREMENT

Strictly no machine learning.

Do not use YOLO, CNNs, neural networks, deep embeddings, pretrained models, PyTorch, TensorFlow, ONNX, Deep SORT, CLIP, learned Re-ID, neural OCR, or learned detectors/features.

Use classical OpenCV/NumPy/SciPy methods and standard Python libraries.

# PIPELINE

Implement:

```text
Camera
→ ≤24 FPS
→ background warm-up
→ MOG2/KNN
→ mask cleanup
→ connected components
→ temporal object formation
→ vehicle candidate validation
→ optional GrabCut refinement
→ local tracking
→ observation quality
→ visual descriptor
→ LSH candidate retrieval
→ exact visual verification
→ plate recognition
→ fuzzy plate comparison
→ temporal/spatial context
→ late-fusion Re-ID
→ Global ID
→ provenance transaction
→ P2P consensus
→ replicated ledger
→ monitoring UI
```

Keep detection, tracking, visual Re-ID, plate Re-ID, context, and provenance as separate subsystems.

# CAMERA

Support:

* DroidCam HTTP/MJPEG
* RTSP
* OpenCV camera index

Camera source, resolution and processing FPS must be configurable.

Cap processing at **24 FPS**.

On startup, use a configurable 3–5 second warm-up period for background modeling. Do not use the first frame as the background.

Handle disconnect/reconnect gracefully.

Do not require manually configured ROIs.

# DETECTION

Use MOG2 as the primary background subtraction method, with KNN as an optional alternative.

Clean the foreground mask using conservative morphology and small-component removal.

Preserve shadow information where useful.

Connected components should initially produce raw foreground regions only.

Do not assume one component equals one vehicle.

# TEMPORAL OBJECT FORMATION

Associate foreground components across frames using deterministic:

* centroid motion;
* bbox overlap/distance;
* size consistency.

Use persistence to form object hypotheses.

Do not merge components merely because they are close.

Two nearby vehicles must remain separable when motion/geometry suggests they are separate.

Allow temporary missed frames. Do not require perfect continuous tracks.

# VEHICLE VALIDATION

Validate object hypotheses using:

* width;
* height;
* area;
* aspect ratio;
* combined geometry;
* persistence;
* motion consistency;
* scale/perspective plausibility.

Do not rely on one fixed global pixel size.

Do not require manual perspective calibration.

# GRABCUT

GrabCut is optional refinement, not the vehicle detector.

Use it only after a stable vehicle candidate exists, initialized from the candidate bbox/foreground mask.

If refinement is poor, retain the original candidate mask.

# TRACKING

Maintain independent local IDs:

```text
C1-T01
C1-T02
C2-T01
```

Use deterministic constant-velocity association/prediction.

Track states may be:

```text
TENTATIVE
CONFIRMED
LOST
DELETED
```

Allow temporary gaps.

Local track IDs are never global identities.

# OBSERVATION QUALITY

Before adding an observation to the Re-ID gallery, evaluate:

* size;
* sharpness;
* segmentation quality;
* visibility;
* stability;
* motion blur/occlusion where practical.

Poor observations should be skipped rather than poisoning the gallery.

# VISUAL DESCRIPTOR

Implement the **MVSV-G handcrafted vehicle descriptor**.

It should combine:

```text
Color:
  HSV histograms
  Lab histograms
  coarse spatial color statistics

Texture:
  multi-scale uniform LBP
  local variance (VAR)

Structure:
  compact HOG
  edge statistics
  coarse silhouette

Region statistics:
  mean/std/robust spread
  selected covariance statistics
```

Use coarse spatial regions; avoid excessive spatial precision.

Resize crops to a fixed analysis size while preserving aspect ratio.

Normalize feature blocks independently, apply configurable block weights, concatenate, then perform final L2 normalization.

Output a fixed-length float32 vector.

Do not include camera ID, timestamp, plate text, or spatial/temporal information in this descriptor.

# LOCAL VISUAL VERIFICATION

After LSH retrieves candidates, optionally use classical SIFT or ORB for candidate-specific local matching.

Do not use BoVW or learned vocabularies.

# IDENTITY GALLERY

Each Global ID maintains a bounded gallery of high-quality visual descriptors.

Do not store every frame.

Retain useful viewpoint/appearance diversity.

Example:

```text
G-007
 ├── visual descriptor A
 ├── visual descriptor B
 ├── visual descriptor C
 └── plate history
```

# LSH

Implement **random-hyperplane LSH for cosine similarity** from scratch.

For normalized x:

```text
h(x) = sign(r · x)
```

Use deterministic seeded random Gaussian hyperplanes.

Support:

* multiple tables;
* configurable bits/table;
* insertion;
* query;
* candidate deduplication;
* optional multi-probe.

LSH is candidate generation only.

Then perform exact cosine similarity against gallery descriptors.

Also provide a brute-force mode for comparison.

# PLATE CHANNEL

Plate recognition is an independent Re-ID signal.

Use classical methods only:

```text
plate localization
→ geometric filtering
→ perspective correction
→ threshold/contrast enhancement
→ line detection
→ character segmentation
→ template matching
→ confidence
→ temporal aggregation
```

Support both single-line and multi-line plates.

Do not use Tesseract/neural OCR.

Represent plate state as:

```text
UNKNOWN
SUPPORTED
CONFLICTING
```

A missing plate is not negative evidence.

Use fuzzy comparison based on edit distance, normalization and character ambiguity.

Aggregate multiple frame readings into a track-level/canonical plate hypothesis.

# RE-ID CONTEXT

Use temporal and spatial information as **soft evidence**, not rigid gating.

Cameras may have different viewpoints, scales and coordinate systems.

Do not directly compare raw pixel coordinates across cameras.

Do not require continuous visibility between cameras.

Use broad:

* elapsed-time plausibility;
* camera-transition plausibility;
* within-camera motion;
* relative scale/location where useful.

Context should not override strong contradictory appearance/plate evidence.

# RE-ID FUSION

Keep these independent:

```text
visual_score
plate_score
temporal_score
spatial_score
```

Fuse them only at the final decision stage.

Support:

```text
MATCH
NO_MATCH
UNCERTAIN
```

Missing plate evidence must be handled without treating it as zero.

Log the individual evidence components and final decision.

# GLOBAL IDENTITIES

Generate system-level Global IDs independently of feature hashes.

For example:

```text
G-001
G-002
G-003
```

Use an internal UUID or equivalent stable ID.

A global identity stores:

```text
global_id
visual_gallery
plate_history
camera_history
observation_history
last_seen
last_camera
last_position
```

A local track can transition:

```text
C1-T07 → G-014
```

and another node can later establish:

```text
C2-T03 → G-014
```

# PROVENANCE

Create signed identity-event transactions.

Do not put raw images, video, or full feature vectors on the ledger.

Events may contain:

```text
event_id
timestamp
node_id
camera_id
local_track_id
global_identity_id
visual_score
plate_score
temporal_score
spatial_score
decision
decision_confidence
observation_hash
feature_hash
plate_hash
```

Use SHA-256 for provenance hashes.

Do not confuse LSH hashes with provenance hashes.

# CONSORTIUM

Every node runs identical peer software.

Each peer:

* has a cryptographic identity;
* maintains its own ledger;
* receives transactions;
* validates transactions;
* participates in consensus;
* may temporarily propose a block;
* synchronizes with peers.

No permanent leader.

Temporary proposer selection may rotate deterministically among peers.

Do not hard-code the number of nodes.

Use configurable peer addresses and a configurable TCP/network port.

Example:

```text
192.168.1.101:9000
192.168.1.102:9000
192.168.1.103:9000
```

Same port is fine because the IPs differ.

# CONSENSUS

Implement a simple transparent quorum-based consensus suitable for an MVP.

The objective is:

* distributed agreement;
* block ordering;
* replicated state;
* no permanent central coordinator.

Do **not** claim Byzantine fault tolerance.

Do not introduce unnecessary consensus complexity.

# BLOCK / LEDGER

Blocks should contain at least:

```text
block_index
timestamp
proposer_id
transactions
previous_block_hash
block_hash
consensus_metadata
```

Every peer maintains its own copy.

Use deterministic canonical serialization before hashing.

# SYNCHRONIZATION

A peer that starts late or reconnects must be able to catch up.

Support equivalent operations to:

```text
status
request missing blocks
request current state
```

Verify received blocks before accepting them.

Do not blindly copy another peer's database.

# STORAGE

Use SQLite for local persistent state.

Keep separate concepts for:

```text
local operational state
identity/gallery state
committed ledger state
```

Do not make one machine's SQLite database authoritative for the consortium.

# FRONTEND

Every node gets the same monitor-style UI.

Use:

```text
background: #181818
accent:    #ff6700
network:   #00F0F0
```

Minimal text, technical labels, no marketing UI.

Show:

```text
NODE STATUS
CAMERA
DETECTION
TRACKING
RE-ID
VISUAL
PLATE
CONTEXT
LEDGER
CONSORTIUM
CONSENSUS
```

The camera panel shows the local camera.

Overlay:

* bounding boxes;
* local track ID;
* Global ID;
* relevant similarity information.

Also show:

* FPS;
* detection count;
* active tracks;
* candidate count;
* peer status;
* consensus state;
* latest block;
* ledger integrity.

A node without a camera continues functioning as a normal peer.

# EVALUATION

Provide lightweight tests for the major independent components:

* descriptor;
* LSH;
* tracker;
* plate matching;
* Re-ID fusion;
* blockchain/ledger;
* consensus.

Do not build an excessive test suite.

Also provide an offline synthetic demonstration and an LSH benchmark.

Benchmark:

```text
brute force
vs
LSH + exact verification
```

Measure at least:

* dataset size;
* candidates returned;
* candidate recall where labelled data exists;
* query latency.

Provide basic CSV output and simple plots.

# PROJECT STRUCTURE

Use a clean modular structure separating:

```text
cameras
detection
tracking
segmentation
features
plate
lsh
reid
provenance
consensus
network
storage
visualization
metrics
```

A reasonable structure is:

```text
project/
  README.md
  requirements.txt
  .gitignore
  config/
    config.yaml
  src/
    ...
  tests/
    ...
  experiments/
    ...
  data/
    ...
```

Adjust structure when technically justified.

# README

Write a serious technical README covering, only after everything is implemented:

* project purpose;
* architecture;
* non-ML constraint;
* installation;
* configuration;
* running a node;
* configuring peers;
* configuring cameras;
* offline demo;
* LSH benchmark;
* Re-ID evaluation;
* ledger verification;
* limitations;
* future work.

The README must be good enough to make a 6-page research paper out of. Be very thorough.

**The README must include exact commands for both Linux and Windows**, including virtual-environment creation, dependency installation, running a node, running the offline demo, running tests, and running the benchmark.

Use the correct platform-specific activation commands, e.g.:

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Adapt commands to the actual project entry points you create.

# LIMITATIONS

Document that this is not production-grade vehicle Re-ID.

Explicitly discuss:

* viewpoint;
* lighting;
* occlusion;
* blur;
* scale;
* background contamination;
* nearby vehicles;
* segmentation errors;
* OCR errors;
* visually similar vehicles;
* parked vehicles becoming background;
* LSH candidate misses.

# DELIVERABLE

Create the complete repository and all source files.

The code should be runnable after dependency installation.

Do not spend effort producing a long narrative response after creating the files. The important deliverable is the repository and README.
