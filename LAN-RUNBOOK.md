# Running this three-machine LAN deployment

Configured on 2026-09-13. All three peers run the same application as background processes. C1 uses color camera index `1`, C2 uses integrated camera index `0`, and C3 remains peer-only (`source: null`). They remain running when the launching terminal or SSH connection closes. They are not installed as boot-time services.

| Node | Host | Project | Monitor |
|---|---|---|---|
| C1 | Windows, local | `E:\home\gitthings\reid` | http://10.78.223.22:9000 |
| C2 | `sub@10.78.223.1` | `/home/sub/reid` | http://10.78.223.1:9000 |
| C3 | `sub@10.78.223.84` | `/home/sub/reid` | http://10.78.223.84:9000 |

The verified local Wi-Fi IP is **10.78.223.22**, not 10.78.233.22. All three membership lists already used the verified address, so no keys, member URLs, or genesis definitions were changed.

## Control all three from local PowerShell

No virtual-environment activation is needed for these helpers:

```powershell
Set-Location E:\home\gitthings\reid
.\scripts\lan_cluster.ps1 status
.\scripts\lan_cluster.ps1 start
.\scripts\lan_cluster.ps1 stop
```

Run only the action you want. `start` is idempotent when the node is already serving its expected port. The scripts use the existing SSH keys for the two remote peers. `stop` targets only the recorded managed process and checks its OS creation token to avoid terminating an unrelated process after PID reuse. Linux uses SIGTERM; Windows terminates the detached process. SQLite uses transactional recovery. To restart after changing configuration, stop and then start.

The cluster helper is specific to these three host addresses. The Re-ID application and consensus membership remain configurable for arbitrary node counts.

## Control one node

Local C1:

```powershell
Set-Location E:\home\gitthings\reid
.\.venv\Scripts\python.exe scripts/lan_node.py status --node C1
.\.venv\Scripts\python.exe scripts/lan_node.py stop --node C1
.\.venv\Scripts\python.exe scripts/lan_node.py start --node C1
```

C2 from local PowerShell:

```powershell
ssh sub@10.78.223.1 'cd ~/reid && .venv/bin/python scripts/lan_node.py status --node C2'
ssh sub@10.78.223.1 'cd ~/reid && .venv/bin/python scripts/lan_node.py stop --node C2'
ssh sub@10.78.223.1 'cd ~/reid && .venv/bin/python scripts/lan_node.py start --node C2'
```

C3:

```powershell
ssh sub@10.78.223.84 'cd ~/reid && .venv/bin/python scripts/lan_node.py status --node C3'
ssh sub@10.78.223.84 'cd ~/reid && .venv/bin/python scripts/lan_node.py stop --node C3'
ssh sub@10.78.223.84 'cd ~/reid && .venv/bin/python scripts/lan_node.py start --node C3'
```

Do not additionally launch a foreground copy on port 9000 while the background node is running. For an interactive foreground run, stop that managed node, then run `.venv/bin/python -m reid run --config config/lan/C2.yaml` on Linux or `.\.venv\Scripts\python.exe -m reid run --config config/lan/C1.yaml` locally.

## Configuration and persistent data

Each host uses its own `config/lan/C1.yaml`, `C2.yaml`, or `C3.yaml` as appropriate. LAN configuration backups named `*.yaml.before-lan-*` preserve the previous files.

The original C1 smoke-test database belonged to a different consortium. It was **preserved in place**, and the LAN configs now use separate paths:

- C1: `E:\home\gitthings\reid\data\lan\C1\state.sqlite`
- C2: `/home/sub/reid/data/lan/C2/state.sqlite`
- C3: `/home/sub/reid/data/lan/C3/state.sqlite`

Each of these folders also contains `peer.log` and `process.json`. Keep the databases and their vote records when restarting; do not reinitialize keys or delete ledger state as a routine startup step.

Tail logs:

```powershell
Get-Content E:\home\gitthings\reid\data\lan\C1\peer.log -Tail 40 -Wait
ssh sub@10.78.223.1 'tail -n 40 -f ~/reid/data/lan/C2/peer.log'
ssh sub@10.78.223.84 'tail -n 40 -f ~/reid/data/lan/C3/peer.log'
```

## What healthy running peers show

- Every node lists both other peers as `ONLINE`.
- The quorum is 2 of 3.
- All peers use genesis hash `29e0e45258f554e36f86fd7c3e1b7ebed7993d85b9d2be800067e796e36be212`; their tip hashes advance with committed observations and converge after propagation.
- The proposer rotates between C1, C2, and C3. Peers can show `FOLLOWING`, `PROPOSING`, `COMMITTED`, or `IDLE`.
- C1 and C2 cameras show `ONLINE` after four seconds of background warm-up. C3 shows `DISABLED` because it is intentionally peer-only.

No synthetic vehicle claims were injected into the live ledger during bring-up. After enabling the cameras, both feeds produced previews and processed roughly 16–20 FPS in a spot check. Their camera-derived identity events committed over the LAN; all three peers agreed on height 18 and the same tip hash in a simultaneous sample. These are operational checks, not an evaluation of physical vehicle identification accuracy. There are no periodic empty blocks.

Verify the complete ledger:

```powershell
.\.venv\Scripts\python.exe -m reid verify-ledger --config config/lan/C1.yaml
ssh sub@10.78.223.1 'cd ~/reid && .venv/bin/python -m reid verify-ledger --config config/lan/C2.yaml'
ssh sub@10.78.223.84 'cd ~/reid && .venv/bin/python -m reid verify-ledger --config config/lan/C3.yaml'
```

## Camera sources and changes

C1's actual color-camera index is `1`; index `0` produced an essentially grayscale/dark infrared feed in the probe. C2's integrated color camera is index `0`. These sources are enabled in each node's own YAML on its host. Camera-setting backups are named `*.yaml.before-camera-*`.

To change a source, edit the relevant node's own YAML on its host, then restart that node with the helper. For DroidCam/RTSP use the actual stream URL. Keep C3 at `source: null` if it is a peer-only laptop. A camera index refers to the laptop running that node. Keep cameras stationary during background warm-up and point them at the intended observation area. See the main README for camera configuration and limitations.

The consensus protocol has no view-change mechanism: it stalls if the scheduled proposer is offline, even when the other two machines remain reachable. This is an existing MVP limitation, not a sign that the startup helper failed.
