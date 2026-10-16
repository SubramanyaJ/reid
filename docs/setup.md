# Installation and configuration

[Project overview](../README.md) · Run commands from the repository root.

## Installation

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

## Provisioning a new deployment (skip for the existing LAN)

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

With no camera and no events, a healthy chain remains at genesis (height zero). Nodes do not generate empty blocks to animate the UI. Configure cameras to produce real observations, or run the [independent offline demonstration](evaluation.md#offline-synthetic-demonstration).

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

## Configuration and cameras

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
