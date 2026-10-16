"""Argument definitions and dispatch for the reid command."""
import argparse
import logging
from . import commands as actions


def build_parser():
    parser = argparse.ArgumentParser(description="Classical vehicle Re-ID permissioned peer")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Interactive per-machine setup with local keys and public-only peer enrollment")
    setup.set_defaults(handler=actions.setup_node)
    setup.add_argument("--out", default="config/node", help="This machine's setup directory (default: config/node)")
    setup.add_argument("--timeout", type=float, default=600., help="Public-key exchange timeout in seconds (default: 600)")
    init = commands.add_parser("init", help="Provision loopback-only demo peers on one machine; use setup for a LAN")
    init.set_defaults(handler=actions.initialize)
    init.add_argument("--nodes", nargs="+", default=["C1", "C2", "C3"])
    init.add_argument("--hosts", nargs="+", help="Loopback hosts only; real machines must generate their own keys with setup")
    init.add_argument("--port", type=int, default=9000)
    init.add_argument("--out", default="config/local")
    run = commands.add_parser("run", help="Run an identical camera-capable peer and monitor")
    run.set_defaults(handler=actions.run_peer)
    run.add_argument("--config", default="config/local/C1.yaml")
    video = commands.add_parser("replay", help="Process a local MP4 on one isolated node and export metrics_live.json")
    video.set_defaults(handler=actions.replay)
    video.add_argument("--video", required=True, help="Path to a pre-recorded local video")
    video.add_argument("--out", help="New output directory; default creates a unique experiments/results/video-* directory")
    video.add_argument("--config", help="Optional existing config for vision settings; LAN keys/state are not reused")
    video.add_argument("--fps", type=float, help="Override file FPS for the video timeline (no wall-clock throttling)")
    video.add_argument("--warmup-seconds", type=float, help="Background warmup in video seconds; default 4")
    video.add_argument("--ui", action="store_true", help="Start the local video frontend with human match review")
    video.add_argument("--port", type=int, default=9000, help="Local frontend port for --ui; default 9000")
    verify = commands.add_parser("verify-ledger")
    verify.set_defaults(handler=actions.verify_ledger)
    verify.add_argument("--config", default="config/local/C1.yaml")
    demo = commands.add_parser("demo")
    demo.set_defaults(handler=actions.run_demo)
    demo.add_argument("--out", default="experiments/results/demo")
    demo.add_argument("--frames", type=int, default=430)
    bench = commands.add_parser("benchmark")
    bench.set_defaults(handler=actions.run_benchmark)
    bench.add_argument("--out", default="experiments/results/benchmark")
    bench.add_argument("--sizes", nargs="+", type=int, default=[100, 1000, 5000])
    bench.add_argument("--queries", type=int, default=100)
    bench.add_argument("--dataset", help="NPZ with vectors, queries, optional labels and query_labels")
    evaluate = commands.add_parser("evaluate")
    evaluate.set_defaults(handler=actions.run_evaluation)
    evaluate.add_argument("--csv", required=True)
    evaluate.add_argument("--out", default="experiments/results/evaluation.json")
    live = commands.add_parser("live-evaluate", help="Score independently labeled live observations after a run")
    live.set_defaults(handler=actions.evaluate_live_run)
    live.add_argument("--run-dirs", nargs="+", required=True, help="One or more data/lan/Cx/runs/UUID directories")
    live.add_argument("--labels", required=True, help="CSV with observation_id,truth_id")
    live.add_argument("--out", default="experiments/results/live/metrics_live.json")
    metrics = commands.add_parser("metrics", help="Offline visual research suite; writes metrics.json without starting nodes")
    metrics.set_defaults(handler=actions.run_research)
    metrics.add_argument("--out", default="experiments/results/research")
    inputs = metrics.add_mutually_exclusive_group()
    inputs.add_argument("--dataset", help="Held-out descriptor NPZ")
    inputs.add_argument("--crops", help="Labeled gallery/query image manifest JSON")
    metrics.add_argument("--frames-manifest", help="Labeled frame replay JSON; otherwise uses synthetic motion")
    metrics.add_argument("--cross-camera", action="store_true", help="Exclude all same-camera gallery entries")
    metrics.add_argument("--sizes", nargs="+", type=int, default=[100, 1000, 5000, 10000])
    metrics.add_argument("--queries", type=int, default=100)
    metrics.add_argument("--identities", type=int, default=64, help="Synthetic crop identities, with three gallery views each")
    metrics.add_argument("--seeds", nargs="+", type=int, default=[17, 29, 43], help="Independent hyperplane seeds")
    metrics.add_argument("--data-seed", type=int, default=2026)
    metrics.add_argument("--repeats", type=int, default=5)
    metrics.add_argument("--skip-vision", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        args.handler(args)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
