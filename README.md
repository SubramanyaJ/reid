# Classical Multi-Camera Vehicle Re-Identification

A Python application for identifying objects across stationary cameras using classical computer vision, handcrafted descriptors, and LSH retrieval. Equal peers exchange signed observations and maintain a permissioned provenance ledger. Local video replay runs the same vision pipeline without a LAN deployment.

There are no trained models or model downloads. Detection finds moving foreground objects; it does not classify vehicles. Recognition accuracy requires independently labeled data.

## Set up each machine

Install Python 3.10 or newer, then run the matching command on **each machine**. Git is not required:

```powershell
# Windows
py -c "import urllib.request; exec(urllib.request.urlopen('https://raw.githubusercontent.com/SubramanyaJ/reid/master/install.py', timeout=30).read())"
```

On Linux:

```bash
python3 -c 'import urllib.request; exec(urllib.request.urlopen("https://raw.githubusercontent.com/SubramanyaJ/reid/master/install.py", timeout=30).read())'
```

The installer downloads [SubramanyaJ/reid](https://github.com/SubramanyaJ/reid) into `~/reid` (`reid` in your user home), creates a local `.venv`, installs the runtime dependencies, and opens interactive setup. It reports each stage and asks for the local node ID, full peer list and reachable addresses, camera source, and FPS. Use the same peer list and enrollment code on every machine. Allow the runtime port (normally 9000) and temporary setup port (normally 9001) between the machines.

Rerun the same command for later starts. It reuses an installer-created directory and preserves its keys, configuration, and database; it does not update the application automatically. Add `--directory "path/to/reid"` to choose another location. An existing unrelated directory is refused. After installation, run other commands below from that installation directory.

If you already have a checkout, run `py scripts/setup_node.py` on Windows or `python3 scripts/setup_node.py` on Linux from its root instead.

Every machine generates and keeps **its own private key**. Equal peers exchange authenticated public identities; no machine generates or distributes another machine's private key. The enrollment code stays in memory. Once enrolled, each machine can start independently using its saved configuration. Rerunning the script reuses its identity and offers startup, resuming public-key exchange, or exit.

See [setup](docs/setup.md) for the step-by-step procedure, output files, retry behavior, and manual installation. Do not copy `.venv` between machines. No frontend build is needed.

## Process a video

```powershell
.\.venv\Scripts\python.exe -m reid replay --video "E:\videos\recording.mp4" --ui
```

Open **http://127.0.0.1:9000/**, click **Start video**, then approve or reject the queued object pairs. Final `metrics_live.json` is available after the video and all reviews finish. Omit `--ui` for terminal-only processing. Each run uses a fresh output directory under `experiments/results/`; `--out` selects a new directory explicitly.

See [video replay and review](docs/video.md) for timing, output files, and the limits of comparison-level accuracy.

## Run camera peers

For a new multi-machine deployment, run the setup script above on every machine. When setup offers to launch, accept to run that node in the foreground. Later, rerun the script or use its generated configuration directly:

```powershell
.\.venv\Scripts\python.exe -m reid run --config config/node/C1.yaml
```

Use the local node ID in place of C1; Linux uses `.venv/bin/python`. See the [LAN runbook](LAN-RUNBOOK.md) for independent startup, shutdown, and the separately retained historical LAN deployment.

For a **single-machine smoke check** only:

```bash
python -m reid init --nodes C1 C2 C3 --out config/local
python -m reid run --config config/local/C1.yaml
```

Run C2 and C3 in separate terminals with their configs. `reid init` creates all demo identities locally and accepts loopback hosts only; use the interactive setup for different machines. Configure each camera source before starting.

## Project map

| Location | Purpose |
|---|---|
| `src/reid/cli.py`, `commands.py` | Arguments and command actions |
| `src/reid/runtime.py` | Shared frame processing and peer coordination |
| `src/reid/cameras`, `detection`, `tracking`, `segmentation` | Frames, foreground, tracks, and crops |
| `src/reid/features`, `plate`, `lsh`, `reid` | Descriptors, optional plate evidence, retrieval, identity decisions |
| `src/reid/network`, `consensus`, `provenance`, `storage` | APIs, replication, signed ledger, persistence |
| `src/reid/metrics`, `visualization` | Evaluation, human review, and monitor interfaces |
| `tests/` | Component and integration regression tests |
| `config/`, `scripts/` | Configuration and deployment helpers |
| `experiments/` | Example inputs, research instructions, and local results |
| `main.tex`, `paper_figures/` | Manuscript and figure assets |

The [architecture reference](docs/architecture.md) describes algorithms, boundaries, and limitations. [Evaluation and paper reproduction](docs/evaluation.md) covers benchmarks, labels, network checks, and manuscript builds. The [research suite guide](experiments/RESEARCH.md) documents crop/frame manifests and metrics. Earlier project instructions and delivery notes are retained under `docs/history/`.

## Develop and clean

Install the development extra in this machine's environment to add pytest:

```bash
python -m pip install -e '.[dev]'
```

```bash
python -B -m pytest -q -p no:cacheprovider
```

The suite covers vision, gallery persistence, consensus, video replay, browser review, and report export. This command avoids writing Python and pytest caches. Tests use temporary files and synthetic inputs; they do not establish accuracy on real footage.

On PowerShell, preview or run cleanup from the repository root:

```powershell
.\scripts\clean.ps1 -WhatIf
.\scripts\clean.ps1
```

Cleanup removes project Python caches, pytest caches, config backup copies, and LaTeX intermediates. It preserves the virtual environment, editable-install metadata, keys, databases, experiment results, manuscripts, and PDFs. Generated caches and build intermediates are ignored by Git.
