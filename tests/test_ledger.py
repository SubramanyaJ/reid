from copy import deepcopy
import pytest
from reid.consensus.quorum import Consensus
from reid.features.descriptor import MVSVG
from reid.provenance.crypto import digest, sign
from reid.provenance.events import validate_packet, validate_transaction
from reid.provenance.ledger import Ledger, core
from reid.storage.database import Database
from conftest import packet_for


def test_packet_authentication_and_hash_binding(consortium):
    keys, members = consortium
    packet = packet_for(keys["C1"])
    assert validate_packet(packet, members, MVSVG().profile)
    changed = deepcopy(packet)
    changed["payload"]["quality"] = .1
    assert not validate_packet(changed, members, MVSVG().profile)
    changed = deepcopy(packet)
    changed["transaction"]["event"]["visual_score"] = 1.
    assert not validate_transaction(changed["transaction"], members)


def test_quorum_rotation_restart_and_late_catchup(consortium, tmp_path):
    keys, members = consortium
    dbs = [Database(str(tmp_path/f"{node}.sqlite")) for node in members]
    ledgers = [Ledger(db, members) for db in dbs]
    peers = [Consensus(node, keys[node], ledger) for node, ledger in zip(members, ledgers)]
    packet = packet_for(keys["C1"])
    for db in dbs:
        db.put_packet(packet)
    block = peers[0].propose()
    assert block["proposer_id"] == "C1"
    vote = peers[0].vote(block)
    assert peers[0].vote(block) == vote
    with pytest.raises(ValueError):
        peers[0].finalize(block, [vote])
    committed = peers[0].finalize(block, [vote, peers[1].vote(block)])
    ledgers[1].accept(committed)
    assert ledgers[1].proposer(2) == "C2"
    # Late peer accepts only validated blocks, and catches up without copying SQLite.
    ledgers[2].accept(committed)
    assert len({db.tip()["block_hash"] for db in dbs}) == 1
    assert all(ledger.verify_all() == (True, 2) for ledger in ledgers)
    tampered = deepcopy(committed)
    tampered["transactions"][0]["event"]["decision_confidence"] = .123
    assert not ledgers[2].validate(tampered, dbs[2].blocks(0, 1)[0], seen=set())
    next_packet = packet_for(keys["C2"], node="C2")
    for db in dbs:
        db.put_packet(next_packet)
    second = peers[1].propose()
    committed_second = peers[1].finalize(second, [peer.vote(second) for peer in peers[:2]])
    for ledger in (ledgers[0], ledgers[2]):
        ledger.accept(committed_second)
    assert all(ledger.verify_all() == (True, 3) for ledger in ledgers)
    for db in dbs:
        db.close()


def test_durable_vote_prevents_equivocation_after_restart(consortium, tmp_path):
    keys, members = consortium
    path = str(tmp_path / "node.sqlite")
    db = Database(path)
    ledger = Ledger(db, members)
    proposer = Consensus("C1", keys["C1"], ledger)
    db.put_packet(packet_for(keys["C1"]))
    block = proposer.propose()
    proposer.vote(block)
    db.close()
    db = Database(path)
    restarted = Consensus("C1", keys["C1"], Ledger(db, members))
    assert restarted.propose() == block
    conflicting = deepcopy(block)
    conflicting["timestamp"] += 1
    conflicting["block_hash"] = digest(core(conflicting))
    conflicting["proposal_signature"] = sign(keys["C1"], {"domain": "reid-proposal-v1", "block_hash": conflicting["block_hash"]})
    with pytest.raises(ValueError, match="Already voted"):
        restarted.vote(conflicting)
    db.close()


def test_duplicate_voter_is_not_quorum(consortium):
    keys, members = consortium
    db = Database(":memory:")
    ledger = Ledger(db, members)
    peer = Consensus("C1", keys["C1"], ledger)
    db.put_packet(packet_for(keys["C1"]))
    block = peer.propose()
    vote = peer.vote(block)
    with pytest.raises(ValueError):
        peer.finalize(block, [vote, vote])
    db.close()
