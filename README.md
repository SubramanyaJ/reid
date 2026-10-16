# Classical Multi-Camera Vehicle Re-Identification

A Python application for identifying objects across stationary cameras using classical computer vision, handcrafted descriptors, and LSH retrieval. Equal peers exchange signed observations and maintain a permissioned provenance ledger. Local video replay runs the same vision pipeline without a LAN deployment.

There are no trained models or model downloads. Detection finds moving foreground objects; it does not classify vehicles. Recognition accuracy requires independently labeled data.

## Install

Use Python 3.10 or newer. From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m reid --help
```

On Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m reid --help
```

The `dev` extra adds pytest. `pip install -r requirements.txt` installs the same editable development environment. No frontend build is needed. See [setup](docs/setup.md) for system dependencies and camera configuration.

## Process a video

```powershell
.\.venv\Scripts\python.exe -m reid replay --video "E:\videos\recording.mp4" --ui
```

Open **http://127.0.0.1:9000/**, click **Start video**, then approve or reject the queued object pairs. Final `metrics_live.json` is available after the video and all reviews finish. Omit `--ui` for terminal-only processing. Each run uses a fresh output directory under `experiments/results/`; `--out` selects a new directory explicitly.

See [video replay and review](docs/video.md) for timing, output files, and the limits of comparison-level accuracy.

## Run camera peers

For the configured C1/C2/C3 machines, follow the [LAN runbook](LAN-RUNBOOK.md). It covers startup, shutdown, logs, and collecting labeled observations.

For a **new** local deployment:

```bash
python -m reid init --nodes C1 C2 C3 --out config/local
python -m reid run --config config/local/C1.yaml
```

Run C2 and C3 in separate terminals with their configs. Configure each camera source before starting. Full provisioning instructions are in [setup](docs/setup.md).

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
