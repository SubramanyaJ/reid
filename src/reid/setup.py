"""Interactive, owner-generated node identities and symmetric public-key enrollment."""
import base64
from copy import deepcopy
import getpass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .config import DEFAULTS, load_config
from .provenance.crypto import canonical, digest, load_private, public_text, save_private, sign, verify

PROTOCOL = "reid-enrollment-v1"
MAX_BODY = 16384
ID_PATTERN = r"[A-Za-z0-9_-]{1,32}"


def node_id(value):
    if not isinstance(value, str) or not re.fullmatch(ID_PATTERN, value):
        raise ValueError("Use 1..32 letters, digits, underscores or hyphens for a node ID")
    return value


def peer_url(value):
    """Canonical direct HTTP endpoint; the adjacent port is used for enrollment."""
    value = value.strip()
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    if (parts.scheme != "http" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/") or parts.query or parts.fragment):
        raise ValueError("Enter an HTTP host/IP and port, without credentials, path or query")
    host = parts.hostname.lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError("Invalid host name") from None
    else:
        if address.is_unspecified or address.is_multicast:
            raise ValueError("Use a reachable address, not a wildcard or multicast address")
        host = address.compressed
    port = parts.port if parts.port is not None else 9000
    if not 1024 <= port <= 65534:
        raise ValueError("Choose a runtime TCP port from 1024 to 65534; port + 1 is used for setup")
    return f"http://{'[' + host + ']' if ':' in host else host}:{port}"


def exchange_url(url):
    parts = urlsplit(url)
    host = parts.hostname
    return f"http://{'[' + host + ']' if ':' in host else host}:{parts.port + 1}"


def topology(peers):
    if not isinstance(peers, list) or not peers:
        raise ValueError("At least one peer is required")
    normalized = []
    for peer in peers:
        if not isinstance(peer, dict) or set(peer) != {"id", "url"}:
            raise ValueError("Each peer must contain only id and url")
        normalized.append({"id": node_id(peer["id"]), "url": peer_url(peer["url"])})
    if len({p["id"] for p in normalized}) != len(normalized):
        raise ValueError("Peer IDs must be unique")
    addresses = [url for p in normalized for url in (p["url"], exchange_url(p["url"]))]
    if len(set(addresses)) != len(addresses):
        raise ValueError("Runtime and setup endpoints must be distinct; leave one port between local peers")
    return sorted(normalized, key=lambda p: p["id"])


def parse_camera(value):
    value = value.strip()
    if value.lower() in ("", "none", "null", "off"):
        return None
    if value.isdecimal():
        return int(value)
    if value.startswith(("http://", "https://", "rtsp://")):
        return value
    raise ValueError("Enter a nonnegative camera index, HTTP/RTSP stream URL, or none")


def atomic_write(path, data, validate=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if validate is not None:
            validate(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def identity(out, local_id):
    """Create only this machine's key, exclusively; retries preserve it."""
    path = Path(out) / "keys" / f"{node_id(local_id)}.pem"
    if path.exists():
        key = load_private(path)
        print(f"[identity] Reusing this machine's private key: {path}", flush=True)
    else:
        key = Ed25519PrivateKey.generate()
        save_private(path, key)
        print(f"[identity] Generated this machine's private key: {path}", flush=True)
    print(f"[identity] Public-key fingerprint: {hashlib.sha256(base64.b64decode(public_text(key.public_key()))).hexdigest()}")
    return key


class Enrollment:
    """Authenticated public-only messages, pinned per expected node and topology."""
    def __init__(self, local_id, key, peers, code):
        self.peers = topology(peers)
        self.expected = {p["id"]: p["url"] for p in self.peers}
        if local_id not in self.expected:
            raise ValueError("Local node must appear in the peer list")
        if not isinstance(code, str) or len(code) < 16:
            raise ValueError("The shared enrollment code must contain at least 16 characters")
        self.local_id = local_id
        self.topology_hash = digest(self.peers)
        self.secret = hashlib.scrypt(code.encode(), salt=self.topology_hash.encode(), n=16384, r=8, p=1, dklen=32)
        member = {"id": local_id, "url": self.expected[local_id], "public_key": public_text(key.public_key())}
        body = {"protocol": PROTOCOL, "topology_hash": self.topology_hash, "member": member}
        signed = {"body": body, "signature": sign(key, body)}
        self.packet = {**signed, "mac": hmac.new(self.secret, canonical(signed), hashlib.sha256).hexdigest()}
        self.members = {local_id: member}
        self.lock = threading.RLock()
        self.conflict = None

    def accept(self, packet):
        try:
            if not isinstance(packet, dict) or set(packet) != {"body", "signature", "mac"}:
                raise ValueError("Unexpected enrollment fields")
            signed = {"body": packet["body"], "signature": packet["signature"]}
            expected_mac = hmac.new(self.secret, canonical(signed), hashlib.sha256).hexdigest()
            if not isinstance(packet["mac"], str) or not hmac.compare_digest(expected_mac, packet["mac"]):
                raise ValueError("Enrollment code or peer list differs")
            body = packet["body"]
            if (not isinstance(body, dict) or set(body) != {"protocol", "topology_hash", "member"}
                    or body["protocol"] != PROTOCOL or body["topology_hash"] != self.topology_hash):
                raise ValueError("Enrollment protocol or peer list differs")
            member = body["member"]
            if not isinstance(member, dict) or set(member) != {"id", "url", "public_key"}:
                raise ValueError("Only a public member identity may be exchanged")
            name = member["id"]
            if self.expected.get(name) != member["url"]:
                raise ValueError("Unexpected node ID or peer URL")
            raw = base64.b64decode(member["public_key"], validate=True)
            if len(raw) != 32 or base64.b64encode(raw).decode() != member["public_key"]:
                raise ValueError("Invalid Ed25519 public key")
            if not verify(member["public_key"], body, packet["signature"]):
                raise ValueError("Invalid public identity signature")
            with self.lock:
                existing = self.members.get(name)
                if existing is not None and existing != member:
                    self.conflict = f"Public key changed for {name}; refusing to replace its identity"
                    raise ValueError(self.conflict)
                if any(m["public_key"] == member["public_key"] and m["id"] != name for m in self.members.values()):
                    raise ValueError("Two nodes cannot share a public key")
                self.members[name] = dict(member)
            return name
        except (KeyError, TypeError, UnicodeError) as error:
            raise ValueError("Malformed public identity") from error

    def snapshot(self):
        with self.lock:
            if self.conflict:
                raise ValueError(self.conflict)
            return [dict(self.members[k]) for k in sorted(self.members)]


def enrollment_server(enrollment, host, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *args):
            pass  # Progress is shown by the wizard; do not log message contents.

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (self.path != "/setup/identity" or not 0 < length <= MAX_BODY
                        or self.headers.get_content_type() != "application/json"
                        or self.headers.get("Transfer-Encoding")):
                    raise ValueError("Invalid request path, content type or size")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("Incomplete enrollment request")
                enrollment.accept(json.loads(data))
                reply = canonical(enrollment.packet)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            except (ValueError, OSError):
                self.close_connection = True
                try:
                    self.send_error(400, "Enrollment rejected: check code, peer list and identity")
                except OSError:
                    pass

    family = socket.AF_INET6 if ":" in host else socket.AF_INET

    class Server(ThreadingHTTPServer):
        address_family = family
        # server_close waits for in-flight public replies before keys leave memory.
        daemon_threads = False

    return Server((host, port), Handler)


def exchange(enrollment, host="0.0.0.0", timeout=600., progress=print, ready=None):
    """Each peer hosts and contacts the others; there is no enrollment authority."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Enrollment timeout must be positive and finite")
    own = enrollment.expected[enrollment.local_id]
    port = urlsplit(own).port + 1
    if ":" in urlsplit(own).hostname and host == "0.0.0.0":
        host = "::"
    server = enrollment_server(enrollment, host, port)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))
    last_status = 0.
    failures = {}
    progress(f"[exchange] Listening on {host}:{port}; waiting up to {timeout:g}s for public identities.")
    progress("[exchange] Run this wizard on every machine with the same peer list and enrollment code.")
    try:
        while len(enrollment.snapshot()) < len(enrollment.peers):
            known = {m["id"] for m in enrollment.snapshot()}
            for peer in enrollment.peers:
                if peer["id"] in known:
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    missing = sorted(set(enrollment.expected) - {m["id"] for m in enrollment.snapshot()})
                    raise ValueError("Enrollment timed out waiting for " + ", ".join(missing)
                                     + ". Check addresses, matching code/list, and TCP setup ports; rerun to reuse your key.")
                request = Request(exchange_url(peer["url"]) + "/setup/identity", data=canonical(enrollment.packet),
                                  headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with opener.open(request, timeout=min(2., remaining)) as response:
                        data = response.read(MAX_BODY + 1)
                        if len(data) > MAX_BODY:
                            raise ValueError("Oversized enrollment response")
                        received = enrollment.accept(json.loads(data))
                        if received != peer["id"]:
                            raise ValueError("Address returned the wrong node identity")
                    failures.pop(peer["id"], None)
                except HTTPError as error:
                    failures[peer["id"]] = f"HTTP {error.code}; check the shared code and identical peer list"
                except (URLError, TimeoutError, OSError) as error:
                    failures[peer["id"]] = f"unreachable ({type(error).__name__}); start its wizard and check the setup port"
                except ValueError as error:
                    failures[peer["id"]] = str(error)
            current = enrollment.snapshot()
            now = time.monotonic()
            if now - last_status >= 5 or len(current) == len(enrollment.peers):
                progress(f"[exchange] Public identities ready: {len(current)}/{len(enrollment.peers)} "
                         + ", ".join(m["id"] for m in current))
                for name, message in sorted(failures.items()):
                    progress(f"[waiting] {name}: {message}")
                last_status = now
            if len(current) < len(enrollment.peers):
                time.sleep(min(.5, max(0., deadline - time.monotonic())))
        if ready is not None:
            # Receiving a request does not prove our reply reached its sender.
            # Stay available for retries until operators see every node ready.
            ready(enrollment.snapshot())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    # In-flight requests can detect a changed key while the server is draining.
    return enrollment.snapshot()


def write_config(out, state, members, key):
    """Commit a local config only after the complete, verified roster is known."""
    out = Path(out).resolve()
    expected = topology(state["peers"])
    members = sorted(members, key=lambda m: m["id"])
    if any(set(member) != {"id", "url", "public_key"} for member in members):
        raise ValueError("Configuration membership must contain only public identities")
    if [{"id": m["id"], "url": m["url"]} for m in members] != expected:
        raise ValueError("Cannot write configuration with incomplete or changed membership")
    own = next(m for m in members if m["id"] == state["id"])
    if own["public_key"] != public_text(key.public_key()):
        raise ValueError("Local public membership does not match this machine's private key")
    cfg_path = out / f"{state['id']}.yaml"
    if cfg_path.exists():
        cfg = load_config(cfg_path)
        if sorted(cfg["members"], key=lambda m: m["id"]) != members:
            raise ValueError("Existing membership differs; create a separate deployment directory instead of rotating keys")
        return cfg_path
    cfg = deepcopy(DEFAULTS)
    cfg["members"] = members
    cfg["node"].update(id=state["id"], port=urlsplit(own["url"]).port,
                       host="::" if ":" in urlsplit(own["url"]).hostname else "0.0.0.0",
                       private_key=f"keys/{state['id']}.pem", database=state["database"])
    cfg["camera"].update(state["camera"])
    # Do not adopt an orphaned database from a different enrollment.
    if (out / state["database"]).resolve().exists():
        raise ValueError("Database already exists without this setup's configuration; choose a fresh setup directory")
    atomic_write(cfg_path, yaml.safe_dump(cfg, sort_keys=False), validate=load_config)
    return cfg_path


def ask(label, default=None, convert=str):
    while True:
        suffix = f" [{default}]" if default is not None else ""
        answer = input(label + suffix + ": ").strip()
        if not answer and default is not None:
            answer = str(default)
        try:
            return convert(answer)
        except (ValueError, TypeError) as error:
            print(f"[input] {error}")


def camera_fps(value):
    value = float(value)
    if not math.isfinite(value) or not 0 < value <= 24:
        raise ValueError("Processing FPS must be greater than 0 and at most 24")
    return value


def choice(value, allowed):
    value = value.lower()
    if value not in allowed:
        raise ValueError("Choose " + ", ".join(allowed))
    return value


def ask_peers(local_id):
    while True:
        names = ask("All node IDs, separated by spaces (same list on every machine)", "C1 C2 C3").split()
        try:
            for name in names:
                node_id(name)
            if not names or len(set(names)) != len(names) or local_id not in names:
                raise ValueError("Use unique IDs and include this machine's ID")
            peers = [{"id": name, "url": ask(f"Reachable runtime address for {name} (host:port)", convert=peer_url)}
                     for name in sorted(names)]
            return topology(peers)
        except ValueError as error:
            print(f"[input] {error}; enter the peer list again.")


def setup_state(out):
    state_path = out / "setup.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or set(state) != {"version", "id", "peers", "camera", "database"}
                or state["version"] != 1):
            raise ValueError("Invalid saved setup state; preserve its key and inspect setup.json")
        node_id(state["id"])
        state["peers"] = topology(state["peers"])
        if state["id"] not in {p["id"] for p in state["peers"]}:
            raise ValueError("Saved peer list does not contain this node")
        if not (out / "keys" / f"{state['id']}.pem").is_file():
            raise ValueError("Saved identity is missing its private key; restore it or use a fresh deployment directory")
        print(f"[setup] Resuming {state['id']} from {state_path}")
        return state
    local_id = ask("This machine's node ID", "C1", node_id)
    peers = ask_peers(local_id)
    source = ask("Local camera index, stream URL, or none", "none", parse_camera)
    fps = ask("Processing FPS", "20", camera_fps)
    # The key is created before state is saved; a missing saved key is never silently replaced.
    identity(out, local_id)
    project = Path(__file__).resolve().parents[2]
    data_root = project / "data" / "enrolled"
    if out != project / "config" / "node":
        data_root = data_root / digest(str(out))[:12]
    database = Path(os.path.relpath(data_root / local_id / "state.sqlite", out)).as_posix()
    state = {"version": 1, "id": local_id, "peers": peers,
             "camera": {"source": source, "fps": fps}, "database": database}
    atomic_write(state_path, json.dumps(state, indent=2) + "\n")
    return state


def run_setup(args):
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("Enrollment timeout must be positive and finite")
    print("\nRe-ID machine setup | each machine owns its private key", flush=True)
    out = Path(args.out).resolve()
    resumed = (out / "setup.json").exists()
    state = setup_state(out)
    cfg_path = out / f"{state['id']}.yaml"
    if resumed and not cfg_path.exists():
        selection = ask("Unfinished setup: 1 = retry exchange, 2 = correct network/camera settings, 3 = exit", "1",
                        lambda value: choice(value, ("1", "2", "3")))
        if selection == "3":
            return
        if selection == "2":
            if (out / state["database"]).resolve().exists():
                raise ValueError("A database already exists; do not change enrollment settings for an existing ledger")
            state["peers"] = ask_peers(state["id"])
            source = ask("Local camera index, stream URL, or none", "none", parse_camera)
            fps = ask("Processing FPS", "20", camera_fps)
            state["camera"] = {"source": source, "fps": fps}
            atomic_write(out / "setup.json", json.dumps(state, indent=2) + "\n")
            print("[setup] Corrected settings saved; this machine's private key is unchanged.")
    key = identity(out, state["id"])
    print(f"[setup] Peer-list fingerprint: {digest(state['peers'])}")
    for peer in state["peers"]:
        print(f"  {peer['id']}: runtime {peer['url']} | setup {exchange_url(peer['url'])}")
    if cfg_path.exists():
        cfg = load_config(cfg_path)
        expected = [{"id": m["id"], "url": m["url"]} for m in sorted(cfg["members"], key=lambda m: m["id"])]
        own = next((m for m in cfg["members"] if m["id"] == state["id"]), None)
        if (cfg["node"]["id"] != state["id"] or expected != state["peers"] or own is None
                or own["public_key"] != public_text(key.public_key())
                or Path(cfg["node"]["private_key"]) != out / "keys" / f"{state['id']}.pem"):
            raise ValueError("Saved configuration differs from this local identity/setup; refusing to run")
        selection = ask("Existing node: 1 = start, 2 = reopen public-key exchange, 3 = exit", "1",
                        lambda value: choice(value, ("1", "2", "3")))
        if selection == "3":
            return
        do_exchange = selection == "2"
    else:
        do_exchange = True
    if do_exchange:
        print("[exchange] Enter the same enrollment code on all machines (minimum 16 characters).")
        print("[exchange] Leave blank on ONE machine to generate a code, then enter it on the others.")
        while True:
            code = getpass.getpass("Shared enrollment code (hidden): ")
            if not code:
                code = secrets.token_urlsafe(24)
                print(f"[exchange] Code for this enrollment: {code}")
            if len(code) >= 16:
                break
            print("[input] Use at least 16 characters.")
        enrollment = Enrollment(state["id"], key, state["peers"], code)
        del code
        if cfg_path.exists():
            # Pin already enrolled peers before reopening the listener.
            enrollment.members = {m["id"]: dict(m) for m in cfg["members"]}
        atomic_write(out / "public.json", json.dumps(enrollment.packet["body"] | {"signature": enrollment.packet["signature"]}, indent=2) + "\n")
        # Public membership is pinned above, but a returning node still serves peers until asked to stop.
        if cfg_path.exists():
            port = urlsplit(enrollment.expected[state["id"]]).port + 1
            bind = "::" if ":" in urlsplit(enrollment.expected[state["id"]]).hostname else "0.0.0.0"
            server = enrollment_server(enrollment, bind, port)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
            thread.start()
            try:
                print(f"[exchange] Serving this public identity on TCP {port}. Keep open until the other machines finish.")
                input("Press Enter after the other machines finish enrollment: ")
                enrollment.snapshot()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            members = enrollment.snapshot()
        else:
            def ready(members):
                print(f"[ready] All public identities received. Membership fingerprint: {digest(members)}", flush=True)
                print("[ready] Keep this window open until EVERY machine shows this same ready fingerprint.")
                input("Press Enter once all machines are ready (public exchange remains available until then): ")

            members = exchange(enrollment, timeout=args.timeout, progress=lambda text: print(text, flush=True), ready=ready)
        cfg_path = write_config(out, state, members, key)
        print(f"[ready] Saved local configuration: {cfg_path}")
        print(f"[ready] Membership fingerprint (must match on every machine): {digest(members)}")
        print("[ready] Only public identities were exchanged. The private key stays on this machine.")
        if ask("Start this node now? y/n", "y", lambda value: choice(value, ("y", "n"))) == "n":
            print("[ready] Start later by rerunning scripts/setup_node.py and selecting 1 = start.")
            return
    local_url = urlsplit(next(p["url"] for p in state["peers"] if p["id"] == state["id"]))
    monitor_host = "[::1]" if ":" in local_url.hostname else "127.0.0.1"
    print(f"[starting] Node {state['id']} | monitor http://{monitor_host}:{local_url.port}/")
    print("[starting] The monitor shows camera warmup, peers and consensus. Ctrl+C stops the node.", flush=True)
    # Run in this process so Ctrl+C follows the runtime's normal graceful shutdown.
    from .commands import run_peer
    from argparse import Namespace
    run_peer(Namespace(config=str(cfg_path)))
