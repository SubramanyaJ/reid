# Installation and configuration

[Project overview](../README.md) · After installation, run project commands from the installation directory.

## Interactive setup on each machine

Use Python 3.10 or newer. Run the matching command on every machine to download [SubramanyaJ/reid](https://github.com/SubramanyaJ/reid) and open interactive setup. Git is not required:

```powershell
# Windows PowerShell
py -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/SubramanyaJ/reid/master/install.py', timeout=30).read())"
```

```bash
# Linux
python3 -c 'import urllib.request; exec(urllib.request.urlopen("https://raw.githubusercontent.com/SubramanyaJ/reid/master/install.py", timeout=30).read())'
```

The installer uses Python's standard library to download the repository archive into `~/reid` (`reid` in your user home), then runs `scripts/setup_node.py` there. The setup launcher creates or reuses `.venv`, installs the application and its runtime dependencies, verifies imports, and starts `python -m reid setup` using that environment. Both scripts display progress and errors, including dependency-installation output. First installation needs internet access. Use the same application revision on all peers.

Rerunning the one-line command reuses a directory created by this installer and retains the local private key, configuration, database, and source files. It does not download an application update automatically. An existing directory not created by the installer is refused; run its existing setup script or choose a different installation directory. Add `--directory` after the bootstrap command to choose a location, for example:

```powershell
py -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/SubramanyaJ/reid/master/install.py', timeout=30).read())" --directory "$env:USERPROFILE\reid-node" --timeout 1200
```

The bootstrap accepts `--help` and forwards setup options such as `--timeout 1200` and `--skip-install`. These arguments work on Linux too. After installation, change into the displayed installation directory before running project commands directly. The default is `~/reid` on both platforms.

### Use an existing checkout instead

If you already copied or cloned the repository, run the local launcher from its root:

```powershell
# Windows PowerShell
py scripts/setup_node.py
```

```bash
# Linux
python3 scripts/setup_node.py
```

To clone explicitly, use `git clone https://github.com/SubramanyaJ/reid.git`, enter the resulting `reid` directory, and run the local launcher above. `python scripts/setup_node.py` also works when `python` selects a supported interpreter. A failed dependency installation can be fixed and retried with the same command.

An environment that has already been installed can be checked and reused without pip or network access:

```text
python scripts/setup_node.py --skip-install
```

The wrapper refuses `--skip-install` when the environment is missing, incomplete, or belongs to a different checkout. Do not copy `.venv` from another machine or operating system. There is no Node.js/frontend build step.

On Debian/Ubuntu systems missing venv or OpenCV shared libraries, install the applicable system packages first:

```bash
sudo apt-get update
sudo apt-get install python3-venv libgl1 libglib2.0-0
```

### What to enter

Run setup on all machines during enrollment. Every machine performs the same role; there is no main provisioning computer. For a three-machine example:

| Prompt | C1 machine | C2 machine | C3 machine |
|---|---|---|---|
| Local node ID | `C1` | `C2` | `C3` |
| Full node list | `C1 C2 C3` | `C1 C2 C3` | `C1 C2 C3` |
| C1 runtime URL | `http://192.168.1.101:9000` | same | same |
| C2 runtime URL | `http://192.168.1.102:9000` | same | same |
| C3 runtime URL | `http://192.168.1.103:9000` | same | same |
| Camera | local device index or stream | local device index or stream | `none` if camera-less |
| Enrollment code | one shared code | the same code | the same code |

Substitute the actual IP addresses or resolvable hostnames. The node list defaults to `C1 C2 C3`, and the runtime port normally is 9000. Each URL must reach the correct machine from every other machine. `0.0.0.0` is a listening bind address, not a reachable peer URL. Do not use `localhost` for another machine.

Choose a local integer camera index such as `0`, a stream URL such as `rtsp://192.168.1.120:554/stream1`, or `none`. Setup also asks for FPS. A camera-less node still participates fully in synchronization and consensus. See [Configuration and cameras](#configuration-and-cameras) for the vision settings and source examples.

The shared enrollment code must have at least 16 characters. Leaving it blank generates a code to enter on the other machines. Enter the same code at all peers; keep it within the deployment group. It is used in memory to authenticate enrollment messages and is not saved. Each node independently generates and retains its own Ed25519 private key. The exchange sends signed **public identity information only**. There is no step that sends or copies private keys between machines.

### Network exchange and progress

Allow the runtime TCP port and the temporary setup TCP port between these machines on the trusted LAN. Setup uses **runtime port + 1** on each machine: with runtime 9000, allow **9000 and 9001**. Ensure both are free before starting. Do not assign another node's runtime to a setup port when running several nodes on one host.

Setup displays the topology hash, local identity information, and periodic status for each peer while it waits. Compare the topology hash across machines; differences mean that the entered node lists or URLs differ. Authenticated public identities are collected from every expected peer before the local runtime configuration is completed. Keep every setup window open until **all machines display the same ready membership fingerprint**, then press Enter on each machine. The listener remains available while this prompt is open so peers can retry interrupted replies.

The default exchange timeout is 600 seconds. Extend it when machines take longer to start:

```text
python scripts/setup_node.py --timeout 1200
```

Timeout and Ctrl+C preserve the local identity and saved setup settings. Rerun the same command to retry. If one node completed while another was interrupted, rerun setup on the completed node and choose to reopen exchange so the interrupted node can collect its public identity. The completed node reuses its existing key and configuration. Rerunning an enrolled setup offers startup, reopening exchange, or exit.

An unfinished setup offers retry, correction of network/camera settings, or exit. Use correction to fix a mistyped peer address or camera choice while keeping the same local private key. Once enrollment has produced a configuration, its membership is fixed; changing the consortium requires a separate setup directory, for example `--out config/new-deployment`. A custom setup directory gets a separate database directory under `data/enrolled/`.

The launcher offers to start the peer in the foreground after successful enrollment, with **yes** as the default. Open the displayed monitor address. Camera nodes need the configured background warmup; a camera-less node can be healthy with no frame. Press Ctrl+C in that terminal for graceful shutdown and final metric export.

### Files and later independent startup

The default outputs on each machine are:

| File | Purpose |
|---|---|
| `config/node/setup.json` | This machine's saved enrollment settings for retries |
| `config/node/keys/<node>.pem` | This machine's private key; keep it only on its owner |
| `config/node/public.json` | Public identity export for inspection |
| `config/node/<node>.yaml` | Local runtime configuration, including every public member |
| `data/enrolled/<node>/state.sqlite` | Local runtime database, created when the peer starts |

Keep generated setup files and private keys out of Git. Preserve the local key, configuration, and database across restarts. Setup reuses them instead of replacing the node's identity. Do not copy another node's private key or database onto this machine.

Once enrolled, each machine can restart independently with the script's startup option, or directly with its own configuration:

```powershell
# Windows, C1 example
.\.venv\Scripts\python.exe -m reid run --config config/node/C1.yaml
```

```bash
# Linux, C2 example
.venv/bin/python -m reid run --config config/node/C2.yaml
```

The enrollment code and temporary exchange service are not needed for normal restarts. Every peer keeps its own public membership list; no key-distribution machine has to stay online. Runtime consensus still depends on the scheduled proposer and a majority being available. Do not run multiple peer processes or Uvicorn workers against one node database.

**Membership is fixed for one chain.** IDs, public keys, and URL strings define genesis and must agree exactly on every machine. A changed membership or address requires a new consortium/database in this MVP. New enrollment uses `config/node/` and `data/enrolled/` separately from the historical `config/lan/` deployment; it does not rotate that deployment's keys in place. Preserve the old keys and ledger if its history must be retained. Do not delete only one peer's votes while keeping the rest of its identity/ledger in service: durable votes are part of consensus safety.

Peer HTTP endpoints and the monitoring UI assume a trusted LAN. Events, proposals, and votes are cryptographically authenticated, but HTTP transport, camera previews, feature packets, and status endpoints are not access-controlled or encrypted by this application. Status responses are advisory; signatures and certificates establish acceptance. Use an isolated lab network, VPN, or externally managed authenticated TLS proxy for the intended deployment. Never treat a member's signature as proof that its reported identity is physically correct.

## Manual installation and local smoke check

If an environment is already managed manually, installation remains available:

```powershell
# Windows; activation is optional
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m reid setup
```

```bash
# Linux
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m reid setup
```

The `dev` extra adds pytest; `pip install -r requirements.txt` installs the same editable development environment. Runtime dependencies are OpenCV, NumPy, SciPy, Matplotlib, cryptography, FastAPI, HTTPX, Uvicorn, and PyYAML.

For three peers **on one machine**, the local demo command remains:

```text
python -m reid init --nodes C1 C2 C3 --out config/local
```

This creates `config/local/C1.yaml`, `C2.yaml`, `C3.yaml`, and three private key files under `config/local/keys/`. Each configuration includes the complete public membership list. Local addresses are `127.0.0.1:9000`, `:9001`, and `:9002`. Each node gets its own SQLite database under `data/<node>/`. All cameras initially have `source: null`.

This local-only helper creates all demo keys in one place and rejects remote `--hosts` addresses. For different machines, use the independent setup flow above. It refuses to overwrite an existing configuration/key. Node names are configurable; a single-node consortium is also supported and has quorum one.

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

## Configuration and cameras

Edit the generated local node YAML for additional camera or vision settings. `config/config.yaml` is an annotated template, not a ready-to-run provisioned identity; its membership is intentionally empty. Paths in YAML resolve relative to that YAML file. Feature weights and descriptor profile must agree across peers. Changing a descriptor profile in an existing database is rejected to prevent incompatible galleries.

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
