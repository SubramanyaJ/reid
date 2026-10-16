"""CLI actions with lazy imports so each command loads only its dependencies."""
from copy import deepcopy
import ipaddress
import os
from pathlib import Path
import re
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .config import DEFAULTS, load_config
from .provenance.crypto import public_text, save_private


def setup_node(args):
    from .setup import run_setup
    try:
        run_setup(args)
    except KeyboardInterrupt:
        print("\n[stopped] Setup interrupted. Saved identity/state are preserved; rerun the script to continue.")
        raise SystemExit(130) from None
    except EOFError:
        raise ValueError("Setup requires an interactive terminal. Rerun scripts/setup_node.py in a terminal.") from None


def initialize(args):
    names = args.nodes
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) for name in names):
        raise ValueError("Node IDs must be unique, 1..32 alphanumeric/underscore/hyphen characters")
    hosts = args.hosts or ["127.0.0.1"] * len(names)
    if len(hosts) != len(names):
        raise ValueError("Provide one --hosts value per node")
    for host in hosts:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if not loopback:
            raise ValueError("init is for loopback-only demos. Run python scripts/setup_node.py on EACH machine "
                             "so private keys are generated locally and never distributed.")
    if not 1024 <= args.port <= 65535 - len(names):
        raise ValueError("Choose an unprivileged TCP base port with room for all peers")
    out = Path(args.out).resolve()
    paths = [out / f"{name}.yaml" for name in names] + [out / "keys" / f"{name}.pem" for name in names]
    if any(path.exists() for path in paths):
        raise ValueError("Initialization would overwrite keys/configuration; use a new --out directory")
    keys = {name: Ed25519PrivateKey.generate() for name in names}
    ports, seen_hosts = [], {}
    for host in hosts:
        ports.append(args.port + seen_hosts.get(host, 0))
        seen_hosts[host] = seen_hosts.get(host, 0) + 1
    members = [{"id": name, "url": f"http://{'[' + host + ']' if ':' in host else host}:{port}", "public_key": public_text(keys[name].public_key())}
               for name, host, port in zip(names, hosts, ports)]
    out.mkdir(parents=True, exist_ok=True)
    for name, port in zip(names, ports):
        save_private(out / "keys" / f"{name}.pem", keys[name])
        cfg = deepcopy(DEFAULTS)
        cfg["members"] = members
        cfg["node"].update(id=name, port=port, private_key=f"keys/{name}.pem",
                           database=Path(os.path.relpath(Path.cwd()/"data"/name/"state.sqlite", out)).as_posix())
        (out / f"{name}.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    print(f"Created {len(names)} peer configurations in {out}. Keep each private key only on its owner node.")


def replay(args):
    if args.ui:
        from .network.replay import serve_replay
        serve_replay(args.video, args.out, args.config, args.fps, args.warmup_seconds, args.port)
    else:
        from .metrics.video import replay_video
        replay_video(args.video, args.out, args.config, args.fps, args.warmup_seconds)


def run_peer(args):
    import uvicorn
    from .runtime import Runtime
    from .network.api import create_app
    cfg = load_config(args.config)
    runtime = Runtime(cfg)
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), host=cfg["node"]["host"],
        port=cfg["node"]["port"], workers=1, proxy_headers=False))
    runtime.request_shutdown = lambda: setattr(server, 'should_exit', True)
    server.run()


def verify_ledger(args):
    from .storage.database import Database
    from .provenance.ledger import Ledger
    cfg = load_config(args.config)
    if not Path(cfg["node"]["database"]).is_file():
        raise ValueError("No ledger database exists for this configuration")
    db = Database(cfg["node"]["database"])
    try:
        valid, count = Ledger(db, {m["id"]: m for m in cfg["members"]}).verify_all()
        print(f"Ledger integrity: {'VALID' if valid else 'INVALID'}; blocks including genesis: {count}")
        if not valid:
            raise SystemExit(1)
    finally:
        db.close()


def run_demo(args):
    if args.frames < 1:
        raise ValueError("--frames must be positive")
    from .metrics.synthetic import demo
    demo(args.out, args.frames)


def run_benchmark(args):
    if args.queries < 1 or any(size < 5 for size in args.sizes):
        raise ValueError("Use positive query count and dataset sizes >=5")
    from .metrics.benchmark import benchmark
    benchmark(args.out, args.sizes, args.queries, dataset=args.dataset)


def run_evaluation(args):
    from .metrics.evaluate import evaluate
    evaluate(args.csv, args.out)


def evaluate_live_run(args):
    from .metrics.live import evaluate_live
    report = evaluate_live(args.run_dirs, args.labels, args.out)
    print(report['accuracy'])


def run_research(args):
    if args.queries < 1 or args.repeats < 1 or args.identities < 2 or any(size < 5 for size in args.sizes):
        raise ValueError("Use positive queries/repeats, identities >=2 and sizes >=5")
    if args.data_seed < 0 or args.data_seed > 2147483647 or any(seed < 0 for seed in args.seeds):
        raise ValueError("Seeds must be nonnegative; data seed must fit a signed 32-bit integer")
    if args.cross_camera and not (args.dataset or args.crops):
        raise ValueError("--cross-camera needs supplied data with camera identifiers")
    # CLI imports no NumPy before this branch. Pin BLAS before loading its runtime.
    for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "BLIS_NUM_THREADS"):
        os.environ[variable] = "1"
    from .metrics.research import research
    research(args)
