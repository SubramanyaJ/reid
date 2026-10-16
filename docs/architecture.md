# Architecture and implementation

[Project overview](../README.md) · Run commands from the repository root.

## Scope and architecture

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

`runtime.py` coordinates detection, tracking, observations, and peer state. Frame overlays live in `visualization/frames.py`. `cli.py` defines arguments and dispatches to the lazy-loaded actions in `commands.py`; `network/http.py` owns bounded JSON decoding for both HTTP services. `tests/` covers the independent components and ledger lifecycle. `experiments/` contains an evaluation CSV example; generated results default to `experiments/results/`.

### Non-ML constraint

There are no learned detectors, embeddings, vocabularies, OCR engines, model downloads, or training steps. The code does not use YOLO, neural networks, PyTorch, TensorFlow, ONNX, Deep SORT, CLIP, Tesseract, or BoVW. MOG2/KNN are OpenCV classical adaptive background subtraction algorithms, not learned vehicle classifiers. ORB/SIFT are classical local features. Plate templates are rendered glyphs or manually supplied bitmap characters. Random Gaussian LSH hyperplanes are seeded projections, not trained parameters.

## Classical vision and temporal tracking

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

## MVSV-G visual descriptor and retrieval

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

## Classical plate channel

Plate localization uses grayscale blackhat contrast, horizontal gradient energy, thresholding, morphological grouping, and rotated-rectangle area/aspect filters. Plausible regions undergo a four-corner perspective warp. CLAHE and adaptive thresholding are tried in both polarities. Character components are filtered geometrically and grouped by vertical alignment into one or multiple text lines, then sorted left-to-right within each line.

Characters are normalized to 24×40 and compared using normalized correlation to hand-rendered OpenCV font templates. Several classical font faces/stroke widths provide a small deterministic template bank. Optionally set `plate.template_directory` to a directory of manually supplied `A.png` through `Z.png` and `0.png` through `9.png`. Supplied glyphs may have either polarity. These are character templates, not a trained recognition model or labelled vehicle embedding database.

The selected character score and runner-up margin contribute to reading confidence. A track retains up to 24 readings. Normalized text strips separators and uppercases alphanumeric characters. Fuzzy comparison uses weighted Levenshtein distance with reduced substitution costs for common ambiguities (`0/O/Q`, `1/I/L`, `2/Z`, `5/S`, `8/B`, `6/G`). Canonical text is a confidence-weighted medoid with a support requirement.

Plate states are `UNKNOWN`, `SUPPORTED`, and `CONFLICTING`. Missing/low-confidence readings remain `UNKNOWN`; disagreement can produce `CONFLICTING`. Only supported states produce a plate comparison score. Missing evidence remains `None`, never a fabricated zero. Supported fuzzy plate matches can independently nominate identities that visual LSH failed to retrieve, but exact visual verification and the visual floor still apply.

This template recognizer is deliberately limited. Real embossed fonts, dirt, screws, borders, skew, joined characters, non-Latin scripts, unusual layouts, and tiny plates will often fail. Multi-line support means line segmentation exists; it does not imply reliable recognition of every regional plate design. Re-ID can continue using visual evidence alone.

## Context, fusion, and identities

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

## Provenance, storage, and consensus

### Local identities and peer enrollment

`scripts/setup_node.py` prepares a virtual environment on each machine and runs the interactive `reid setup` command. Each node generates its own Ed25519 private key locally; the setup protocol exchanges public identities only. There is no central key generator or permanent enrollment coordinator. A shared enrollment code authenticates enrollment messages, and each public identity is signed by its own private key. The code is used in memory and is not saved in the setup files.

All participants enter the same fixed set of node IDs and reachable runtime URLs. Setup displays a topology hash, exchanges authenticated identities over a temporary HTTP service at each runtime port plus one, and displays a membership fingerprint once complete. Only the normalized public membership records enter the generated runtime configuration. Temporary setup metadata does not change the ledger format. The completed public membership must agree on every machine, because IDs, public keys, and URL strings determine genesis.

The default local setup directory is `config/node/`: `setup.json` retains retry settings, `keys/<node>.pem` holds only the local private key, `public.json` exports public identity information, and `<node>.yaml` configures the local runtime. The database is separate at `data/enrolled/<node>/state.sqlite`. Interrupted setup can be retried using the same identity. An enrolled node can reopen exchange to help an interrupted peer or start independently without repeating enrollment. This flow creates a separate consortium from any historical `config/lan/` deployment; it does not rotate keys in an existing chain.

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

## Monitor

Every peer serves the same local monitor, using background `#181818`, accent `#ff6700`, and network color `#00F0F0`. Panels show NODE STATUS, CAMERA, DETECTION, TRACKING, RE-ID, VISUAL, PLATE, CONTEXT, LEDGER, CONSORTIUM, and CONSENSUS.

The camera image is the local source with bboxes, local IDs, abbreviated global IDs, and visual similarity where available. The page polls status once per second and JPEG previews at up to four per second; processing may run faster. It shows processing FPS, component count, tracks, candidate count, foreground/shadow fractions, peer states/heights, quorum, scheduled proposer, pending transactions, block tip, and recent decisions. The integrity indicator reflects startup verification and validated subsequent acceptance; it is not a continuous independent scan of disk corruption. Use `verify-ledger` for a complete rescan.

No-camera nodes show a disabled camera feed while maintaining normal gossip, votes, catch-up, and gallery state. The UI is read-only, uses native browser APIs, and does not expose an administrative control panel.


### Inspecting what was re-identified

The monitor now includes **RE-ID OBJECTS / RECENT DECISIONS** with the current local observation beside the exact gallery exemplar selected for comparison, camera labels, global/local IDs, decision, visual score, quality, and commit state. `UNCERTAIN`/rejected observations can be inspected without creating a ledger event. For `NO_MATCH`, the other image is a compared/rejected candidate, not a successful match. Clicking a preview opens a larger inspection dialog.

**IDENTITY GALLERY / ALL CAMERAS** shows the latest observation per identity, its source cameras, and up to three retained exemplars. The peer-only C3 monitor can show C1/C2 images. Tracking rows also have a thumbnail. Preview dimensions preserve the crop aspect ratio, mask background pixels to dark gray, and cap the longest edge at 240 pixels by default.

Thumbnails live in the additive local `thumbnails` SQLite table. At most `visualization.thumbnail_limit` observations (default 300) are retained per node, including uncertain local observations. Entries survive restart and the oldest are evicted automatically. Existing ledger blocks, signed-event schemas, feature vectors, descriptor profiles, and provenance hashes are unchanged. **No image bytes are added to the ledger or signed feature payload.**

`GET /api/objects` returns lightweight observation/identity metadata. `GET /api/thumbnails/{event_id}` returns a bounded JPEG or HTTP 404. Cross-camera image URLs point to the configured originating peer: the browser needs to reach that peer. Thumbnails are not replicated with the ledger. If the origin is offline, the image was evicted, or the event predates this change, the UI explicitly displays “Preview unavailable.” Older original crops were not retained and cannot be regenerated. When the exact matched exemplar is unavailable but a newer image of that identity exists, the comparison panel labels the fallback **Identity reference (latest)** so it is not mistaken for the exact scoring exemplar.

Preview images are visual inspection aids, not cryptographically verified reconstructions of the full-resolution observation hash. Like the live camera preview, they are accessible over the configured trusted LAN without application-level authentication. No continuous raw-video archive is introduced.

## Limitations and future work

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
