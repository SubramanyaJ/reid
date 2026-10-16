"""Owner-held identities, authenticated enrollment, and safe setup recovery."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import hmac
import json
from pathlib import Path
import socket
import threading
from types import SimpleNamespace
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from reid import setup
from reid.cli import main
from reid.config import load_config
from reid.provenance.crypto import canonical, public_text, sign


CODE = "shared-lab-enrollment-code-2026"


@pytest.fixture
def peers():
    return [{"id": node, "url": f"http://127.0.0.1:{9100 + index * 2}"}
            for index, node in enumerate(("C1", "C2", "C3"))]


@pytest.fixture
def identities(peers):
    keys = {peer["id"]: Ed25519PrivateKey.generate() for peer in peers}
    sessions = {node: setup.Enrollment(node, key, peers, CODE) for node, key in keys.items()}
    return keys, sessions


def reseal(packet, secret, key=None):
    """Authenticate crafted packets to exercise checks after the MAC boundary."""
    if key is not None:
        packet["signature"] = sign(key, packet["body"])
    signed = {"body": packet["body"], "signature": packet["signature"]}
    packet["mac"] = hmac.new(secret, canonical(signed), hashlib.sha256).hexdigest()
    return packet


def local_state(node, peers):
    return {"version": 1, "id": node, "peers": peers,
            "camera": {"source": None, "fps": 20.}, "database": "data/state.sqlite"}


def roster(sessions):
    return [sessions[node].packet["body"]["member"] for node in sorted(sessions)]


def test_three_peers_exchange_public_identities_over_real_http(tmp_path, monkeypatch):
    """Bind all ports before starting threads; no fixed-port or release/rebind race."""
    servers, reservations, proxies, peers = {}, [], {}, []
    original_server = setup.enrollment_server
    try:
        for node in ("C1", "C2", "C3"):
            for _ in range(100):
                runtime = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                runtime.bind(("127.0.0.1", 0))
                port = runtime.getsockname()[1]
                if port > 65534:
                    runtime.close()
                    continue
                proxy = SimpleNamespace()
                try:
                    # The real production server reserves the adjacent setup
                    # endpoint immediately; its identity is attached below.
                    server = original_server(proxy, "127.0.0.1", port + 1)
                except OSError:
                    runtime.close()
                    continue
                reservations.append(runtime)
                servers[node], proxies[node] = server, proxy
                peers.append({"id": node, "url": f"http://127.0.0.1:{port}"})
                break
            else:
                pytest.fail("Could not reserve three free runtime/setup port pairs")

        keys = {node: setup.identity(tmp_path / node, node) for node in servers}
        sessions = {node: setup.Enrollment(node, key, peers, CODE) for node, key in keys.items()}
        requests = []
        for node, session in sessions.items():
            def accept(packet, session=session):
                requests.append(deepcopy(packet))
                return session.accept(packet)
            proxies[node].accept = accept
            proxies[node].packet = session.packet

        monkeypatch.setattr(setup, "enrollment_server", lambda session, host, port: servers[session.local_id])
        barrier = threading.Barrier(3)
        ready_barrier = threading.Barrier(3)
        messages = {node: [] for node in sessions}
        ready_rosters, retried = {}, []

        def ready(node, members):
            ready_rosters[node] = members
            ready_barrier.wait(timeout=5)
            # A peer that is already ready must still serve retries while its
            # operator waits for all machines to confirm their fingerprints.
            remote = peers[(list(sessions).index(node) + 1) % len(peers)]
            request = Request(setup.exchange_url(remote["url"]) + "/setup/identity",
                              data=canonical(sessions[node].packet),
                              headers={"Content-Type": "application/json"}, method="POST")
            with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
                retried.append(sessions[node].accept(json.loads(response.read())))
            ready_barrier.wait(timeout=5)

        def enroll(node):
            barrier.wait(timeout=5)
            return setup.exchange(sessions[node], host="127.0.0.1", timeout=10,
                                  progress=messages[node].append,
                                  ready=lambda members: ready(node, members))

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {node: pool.submit(enroll, node) for node in sessions}
            results = {node: future.result(timeout=20) for node, future in futures.items()}

        expected = roster(sessions)
        assert requests  # These identities traversed actual HTTP handlers.
        assert all(result == expected for result in results.values())
        assert all(result == expected for result in ready_rosters.values())
        assert sorted(retried) == ["C1", "C2", "C3"]
        assert all(any("Listening" in message for message in output) for output in messages.values())
        assert len({member["public_key"] for member in expected}) == 3
        for packet in requests:
            assert set(packet) == {"body", "signature", "mac"}
            assert set(packet["body"]["member"]) == {"id", "url", "public_key"}

        for node, key in keys.items():
            out = tmp_path / node
            config = setup.write_config(out, local_state(node, peers), results[node], key)
            cfg = load_config(config)
            assert cfg["members"] == expected
            assert Path(cfg["node"]["private_key"]) == out / "keys" / f"{node}.pem"
            assert [path.name for path in (out / "keys").iterdir()] == [f"{node}.pem"]
            private_bytes = (out / "keys" / f"{node}.pem").read_bytes()
            assert private_bytes not in canonical(requests)
            assert private_bytes not in config.read_bytes()
            for remote in set(keys) - {node}:
                assert f"keys/{remote}.pem" not in config.read_text(encoding="utf-8")
    finally:
        for server in servers.values():
            server.server_close()
        for reservation in reservations:
            reservation.close()


def test_payload_tampering_is_rejected_before_any_identity_is_pinned(identities):
    _, sessions = identities
    packet = deepcopy(sessions["C2"].packet)
    packet["body"]["member"]["url"] = "http://attacker.invalid:9000"
    with pytest.raises(ValueError, match="code or peer list"):
        sessions["C1"].accept(packet)
    assert [member["id"] for member in sessions["C1"].snapshot()] == ["C1"]


def test_valid_mac_does_not_bypass_public_identity_signature(identities):
    keys, sessions = identities
    packet = deepcopy(sessions["C2"].packet)
    packet["signature"] = sign(keys["C3"], packet["body"])
    reseal(packet, sessions["C1"].secret)
    with pytest.raises(ValueError, match="identity signature"):
        sessions["C1"].accept(packet)


def test_different_enrollment_code_is_rejected(peers, identities):
    keys, sessions = identities
    other = setup.Enrollment("C2", keys["C2"], peers, "a-different-shared-code")
    with pytest.raises(ValueError, match="code or peer list"):
        sessions["C1"].accept(other.packet)


def test_different_topology_is_rejected_even_with_valid_mac(peers, identities):
    keys, sessions = identities
    changed = deepcopy(peers)
    changed[-1]["url"] = "http://127.0.0.1:9300"
    other = setup.Enrollment("C2", keys["C2"], changed, CODE)
    with pytest.raises(ValueError, match="code or peer list"):
        sessions["C1"].accept(other.packet)
    packet = reseal(deepcopy(other.packet), sessions["C1"].secret)
    with pytest.raises(ValueError, match="protocol or peer list"):
        sessions["C1"].accept(packet)


@pytest.mark.parametrize("mutation, message", [
    (lambda packet: packet.update(private_key="SECRET"), "Unexpected enrollment fields"),
    (lambda packet: packet["body"].update(private_key="SECRET"), "protocol or peer list"),
    (lambda packet: packet["body"]["member"].update(private_key="SECRET"), "Only a public member"),
    (lambda packet: packet["body"]["member"].update(id="UNKNOWN"), "Unexpected node ID"),
    (lambda packet: packet["body"]["member"].update(url="http://127.0.0.1:9998"), "Unexpected node ID"),
    (lambda packet: packet["body"]["member"].update(public_key="not base64!"), ".*"),
    (lambda packet: packet["body"]["member"].update(public_key="YQ=="), "Invalid Ed25519 public key"),
])
def test_enrollment_rejects_unknown_or_private_fields_and_invalid_keys(identities, mutation, message):
    keys, sessions = identities
    packet = deepcopy(sessions["C2"].packet)
    mutation(packet)
    reseal(packet, sessions["C1"].secret, keys["C2"])
    with pytest.raises(ValueError, match=message):
        sessions["C1"].accept(packet)
    assert set(sessions["C1"].members) == {"C1"}


def test_conflicting_member_key_is_never_replaced(peers, identities):
    _, sessions = identities
    receiver = sessions["C1"]
    receiver.accept(sessions["C2"].packet)
    original = deepcopy(receiver.members["C2"])
    assert receiver.accept(sessions["C2"].packet) == "C2"  # Retry is idempotent.
    replacement = setup.Enrollment("C2", Ed25519PrivateKey.generate(), peers, CODE)
    with pytest.raises(ValueError, match="Public key changed for C2"):
        receiver.accept(replacement.packet)
    assert receiver.members["C2"] == original
    with pytest.raises(ValueError, match="refusing to replace"):
        receiver.snapshot()


def test_two_nodes_cannot_enroll_the_same_public_key(peers, identities):
    keys, sessions = identities
    copied = setup.Enrollment("C2", keys["C1"], peers, CODE)
    with pytest.raises(ValueError, match="cannot share a public key"):
        sessions["C1"].accept(copied.packet)


def test_identity_retry_reuses_only_the_local_private_key(tmp_path):
    first = setup.identity(tmp_path, "C2")
    pem = (tmp_path / "keys" / "C2.pem").read_bytes()
    second = setup.identity(tmp_path, "C2")
    assert public_text(first.public_key()) == public_text(second.public_key())
    assert (tmp_path / "keys" / "C2.pem").read_bytes() == pem
    assert list((tmp_path / "keys").iterdir()) == [tmp_path / "keys" / "C2.pem"]


def test_config_requires_complete_membership_and_matching_local_key(tmp_path, peers, identities):
    keys, sessions = identities
    members = roster(sessions)
    state = local_state("C1", peers)
    with pytest.raises(ValueError, match="incomplete or changed membership"):
        setup.write_config(tmp_path, state, members[:-1], keys["C1"])
    assert not (tmp_path / "C1.yaml").exists()
    with pytest.raises(ValueError, match="does not match"):
        setup.write_config(tmp_path, state, members, keys["C2"])
    assert not (tmp_path / "C1.yaml").exists()


def test_existing_config_is_reused_but_changed_roster_is_refused(tmp_path, peers, identities):
    keys, sessions = identities
    state = local_state("C1", peers)
    members = roster(sessions)
    config = setup.write_config(tmp_path, state, members, keys["C1"])
    before = config.read_bytes()
    assert setup.write_config(tmp_path, state, list(reversed(members)), keys["C1"]) == config
    assert config.read_bytes() == before
    changed = deepcopy(members)
    changed[1]["public_key"] = public_text(Ed25519PrivateKey.generate().public_key())
    with pytest.raises(ValueError, match="Existing membership differs"):
        setup.write_config(tmp_path, state, changed, keys["C1"])
    assert config.read_bytes() == before


def test_orphaned_database_is_not_adopted(tmp_path, peers, identities):
    keys, sessions = identities
    state = local_state("C1", peers)
    database = tmp_path / state["database"]
    database.parent.mkdir()
    database.write_bytes(b"existing history")
    with pytest.raises(ValueError, match="Database already exists"):
        setup.write_config(tmp_path, state, roster(sessions), keys["C1"])
    assert not (tmp_path / "C1.yaml").exists()
    assert database.read_bytes() == b"existing history"


def test_invalid_config_is_not_published_or_left_as_a_temporary_file(tmp_path, peers, identities):
    keys, sessions = identities
    state = local_state("C1", peers)
    state["camera"]["fps"] = -1
    with pytest.raises(ValueError, match="camera.fps"):
        setup.write_config(tmp_path, state, roster(sessions), keys["C1"])
    assert not (tmp_path / "C1.yaml").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_wizard_retry_refuses_missing_saved_key(tmp_path, peers, monkeypatch):
    state = local_state("C2", peers)
    (tmp_path / "setup.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("must not prompt for a replacement identity"))
    with pytest.raises(ValueError, match="missing its private key"):
        setup.setup_state(tmp_path)
    assert not (tmp_path / "keys").exists()
    assert not list(tmp_path.glob("*.yaml"))


@pytest.mark.parametrize("host", ["192.168.1.101", "peer.example", "0.0.0.0", "::"])
def test_remote_init_is_rejected_without_writing_keys(tmp_path, capsys, host):
    out = tmp_path / "remote"
    with pytest.raises(SystemExit) as error:
        main(["init", "--nodes", "C1", "--hosts", host, "--out", str(out)])
    assert error.value.code == 2
    assert "loopback-only" in capsys.readouterr().err
    assert not out.exists()
