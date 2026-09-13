import argparse
from copy import deepcopy
import logging
import os
from pathlib import Path
import re
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from .config import DEFAULTS, load_config
from .provenance.crypto import public_text, save_private


def initialize(args):
    names = args.nodes
    if len(set(names)) != len(names) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) for name in names):
        raise ValueError("Node IDs must be unique, 1..32 alphanumeric/underscore/hyphen characters")
    hosts = args.hosts or ["127.0.0.1"] * len(names)
    if len(hosts) != len(names):
        raise ValueError("Provide one --hosts value per node")
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
    members = [{"id": name, "url": f"http://{host}:{port}", "public_key": public_text(keys[name].public_key())}
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


def main():
    parser = argparse.ArgumentParser(description="Classical vehicle Re-ID permissioned peer")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Provision a new fixed-membership consortium")
    init.add_argument("--nodes", nargs="+", default=["C1", "C2", "C3"])
    init.add_argument("--hosts", nargs="+")
    init.add_argument("--port", type=int, default=9000)
    init.add_argument("--out", default="config/local")
    run = commands.add_parser("run", help="Run an identical camera-capable peer and monitor")
    run.add_argument("--config", default="config/local/C1.yaml")
    verify = commands.add_parser("verify-ledger")
    verify.add_argument("--config", default="config/local/C1.yaml")
    demo = commands.add_parser("demo")
    demo.add_argument("--out", default="experiments/results/demo")
    demo.add_argument("--frames", type=int, default=430)
    bench = commands.add_parser("benchmark")
    bench.add_argument("--out", default="experiments/results/benchmark")
    bench.add_argument("--sizes", nargs="+", type=int, default=[100, 1000, 5000])
    bench.add_argument("--queries", type=int, default=100)
    bench.add_argument("--dataset", help="NPZ with vectors, queries, optional labels and query_labels")
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--csv", required=True)
    evaluate.add_argument("--out", default="experiments/results/evaluation.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "init":
            initialize(args)
        elif args.command == "run":
            import uvicorn
            from .runtime import Runtime
            from .network.api import create_app
            cfg = load_config(args.config)
            runtime = Runtime(cfg)
            uvicorn.run(create_app(runtime), host=cfg["node"]["host"], port=cfg["node"]["port"], workers=1)
        elif args.command == "verify-ledger":
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
        elif args.command == "demo":
            if args.frames < 1:
                raise ValueError("--frames must be positive")
            from .metrics.synthetic import demo
            demo(args.out, args.frames)
        elif args.command == "benchmark":
            if args.queries < 1 or any(size < 5 for size in args.sizes):
                raise ValueError("Use positive query count and dataset sizes >=5")
            from .metrics.benchmark import benchmark
            benchmark(args.out, args.sizes, args.queries, dataset=args.dataset)
        elif args.command == "evaluate":
            from .metrics.evaluate import evaluate
            evaluate(args.csv, args.out)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
