# LAN deployment and independent node startup

[Project overview](README.md) · [New deployment setup](docs/setup.md)

## Set up machines with local keys

For a new deployment, install Python 3.10 or newer and run the matching command on **every machine**. It downloads [SubramanyaJ/reid](https://github.com/SubramanyaJ/reid) without requiring Git:

```powershell
# Windows
py -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/SubramanyaJ/reid/master/install.py', timeout=30).read())"
```

```bash
# Linux
python3 -c 'import urllib.request; exec(urllib.request.urlopen("https://raw.githubusercontent.com/SubramanyaJ/reid/master/install.py", timeout=30).read())'
```

The installer creates `~/reid` by default (`reid` in your user home); append `--directory "path/to/reid"` to choose another location. Rerunning the command reuses an installer-created directory without updating the source or replacing its keys, configuration, or database. An existing unrelated directory is refused. After installation, use its directory for the project commands below. For an existing checkout, run `py scripts/setup_node.py` on Windows or `python3 scripts/setup_node.py` on Linux from that checkout's root instead.

The setup script creates or reuses a local `.venv`, installs and checks dependencies, and guides enrollment. Enter this machine's ID, the complete node list, identical reachable runtime URLs for all peers, a local camera index/stream or `none`, FPS, and the same enrollment code on every machine. The code must have at least 16 characters; a blank entry generates one to enter on the other machines. Run setup on all machines while they exchange identities. Every private key is generated and retained only on its own machine; only authenticated public identity information is exchanged.

Allow the runtime TCP port and runtime port + 1 between hosts on the trusted LAN: normally **9000 and 9001**. The second port is temporary for setup. Compare the displayed topology hashes and final membership fingerprints across machines. Keep every setup window open until **all machines show the same ready membership fingerprint**, then press Enter on each machine. Setup reports per-peer progress and waits up to 600 seconds by default; `--timeout 1200` extends this. If interrupted or timed out, rerun the script to reuse the saved identity and settings. A completed node can choose to reopen exchange to help an interrupted peer finish.

Successful setup offers foreground startup by default. On a later run, choose to start using the saved configuration, reopen exchange, or exit. No central provisioning machine or saved enrollment code is required for normal startup. The setup files are under `config/node/`, each local private key is `config/node/keys/<node>.pem`, and the runtime configuration is `config/node/<node>.yaml`. The local database is `data/enrolled/<node>/state.sqlite`. Preserve them on their owning machine. Do not copy `.venv` or private keys between hosts.

Before enrollment finishes, a rerun also offers to correct the network/camera settings without changing the local private key. After enrollment, preserve the fixed membership and use a separate setup directory for a new deployment.

### Start and stop independently

Rerun the setup script and select startup, or run the local configuration directly:

```powershell
# Windows, C1 example
.\.venv\Scripts\python.exe -m reid run --config config/node/C1.yaml
```

```bash
# Linux, C2 example
.venv/bin/python -m reid run --config config/node/C2.yaml
```

Use the ID assigned to that machine. Open its configured runtime URL in a browser for the monitor. Press Ctrl+C in its terminal to stop gracefully. Each peer keeps its own ledger, gallery, and run reports beside its database. For example, C1 writes `data/enrolled/C1/metrics_live.json` and preserved reports, observations, and label templates in `data/enrolled/C1/runs/<run-id>/`.

| Symptom | Check |
|---|---|
| Dependency installation fails | Python 3.10+, available internet/package index, and the installation error displayed by the script; retry after fixing it. |
| Setup keeps waiting for a peer | That peer is running setup, has the same node list, URLs, and code, and its runtime port + 1 is reachable. |
| Topology hashes differ | Use the same full node list and reachable URLs on every machine. |
| One peer finished but another timed out | Reopen exchange on the completed peer and retry on the interrupted peer. |
| Peer starts but camera is disconnected | Check the local camera source, permissions, or device ownership; `none` is valid. |
| Runtime peers are unavailable | Check the runtime port, LAN routes, and configured member URLs. |
| Port is occupied | Stop another runtime/setup instance using that port before retrying. |

For labeled evaluation, follow [Obtain actual live Re-ID accuracy](#obtain-actual-live-re-id-accuracy) below, substituting the new `data/enrolled/` paths. Membership is fixed for a ledger: changed addresses or keys require a new consortium/database. New enrollment does not rotate keys in the historical deployment below. See the [full setup guide](docs/setup.md) for enrollment files and configuration details.

## Run the existing three-node LAN setup

This section preserves the historical `config/lan/` deployment and its SSH-based controller. The new per-machine setup above uses separate configurations and databases. **Do not run `reid init` again or replace these existing keys.** `reid init` now supports loopback-only demonstrations; new multi-machine enrollment uses the setup script.

| Node | Machine and project root | Config on that machine | Monitor |
|---|---|---|---|
| C1 | Local Windows: `E:\home\gitthings\reid` | `config/lan/C1.yaml` | http://10.78.223.22:9000 |
| C2 | `sub@10.78.223.1`: `/home/sub/reid` | `config/lan/C2.yaml` | http://10.78.223.1:9000 |
| C3 | `sub@10.78.223.84`: `/home/sub/reid` | `config/lan/C3.yaml` | http://10.78.223.84:9000 |

The configured C1 address is **10.78.223.22**. The older `10.78.233.22` address was a typo. Each historical machine uses runtime port **9000**. The separate single-machine demo uses runtime ports 9000/9001/9002; new per-machine enrollment temporarily uses runtime port + 1 for setup.

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
| IP addresses changed | Membership URLs define the existing deployment. Use its original addresses, or deliberately provision a separate configuration/database set using the [setup guide](docs/setup.md); do not edit one member list alone. |

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

The annotated report contains pairwise identity precision/recall/F1, cross-camera pairwise scores, annotation coverage, and resolution coverage. It scores resolved annotated observations and keeps abstentions visible through coverage; it is **not** rank-1, mAP, IDF1, or MOTA. Repeated frames are correlated, so these pairs should not be interpreted as independent statistical samples. Blank truth labels are excluded, an entirely blank template is rejected, and partial annotation coverage is reported. Run directories are preserved. For actual box/mask and ranked-retrieval accuracy, use the labeled frame/crop inputs of the [offline research suite](experiments/RESEARCH.md).

The consensus protocol has no view-change mechanism: it stalls when the scheduled proposer is offline. Historical bring-up observations are archived in [docs/history/lan-bringup.md](docs/history/lan-bringup.md).
