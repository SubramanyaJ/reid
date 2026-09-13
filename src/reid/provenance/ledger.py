import math
from .crypto import digest, sign, verify
from .events import validate_transaction

CORE_FIELDS = ("block_index", "timestamp", "proposer_id", "transactions", "previous_block_hash")


def core(block):
    return {key: block[key] for key in CORE_FIELDS}


def genesis(members):
    # Membership and addresses are fixed for this chain, in sorted canonical order.
    fingerprint = digest([members[k] for k in sorted(members)])
    block = {"block_index": 0, "timestamp": 0., "proposer_id": "GENESIS", "transactions": [],
             "previous_block_hash": fingerprint}
    block["block_hash"] = digest(core(block))
    block["proposal_signature"] = ""
    block["consensus_metadata"] = {"votes": []}
    return block


def vote_body(height, block_hash, node_id):
    return {"domain": "reid-vote-v1", "block_index": height, "block_hash": block_hash, "node_id": node_id}


class Ledger:
    def __init__(self, db, members):
        self.db, self.members = db, members
        self.member_ids = sorted(members)
        self.quorum = len(members) // 2 + 1
        with db.lock:
            if db.tip() is None:
                db.commit(genesis(members))
            first = db.blocks(0, 1)[0]
            if first != genesis(members):
                raise ValueError("Database belongs to a different consortium")

    def proposer(self, height):
        return self.member_ids[(height - 1) % len(self.member_ids)]

    def validate(self, block, previous, committed=True, seen=None):
        try:
            if set(block) != set(CORE_FIELDS) | {"block_hash", "proposal_signature", "consensus_metadata"}:
                return False
            height = block["block_index"]
            if type(height) is not int or height != previous["block_index"] + 1:
                return False
            if block["previous_block_hash"] != previous["block_hash"] or block["proposer_id"] != self.proposer(height):
                return False
            if not isinstance(block["timestamp"], (int, float)) or not math.isfinite(block["timestamp"]) or block["timestamp"] < previous["timestamp"]:
                return False
            if digest(core(block)) != block["block_hash"]:
                return False
            if not verify(self.members[block["proposer_id"]]["public_key"],
                          {"domain": "reid-proposal-v1", "block_hash": block["block_hash"]}, block["proposal_signature"]):
                return False
            transactions = block["transactions"]
            if not isinstance(transactions, list) or not 1 <= len(transactions) <= 128:
                return False
            ids = set()
            for tx in transactions:
                if not validate_transaction(tx, self.members):
                    return False
                event_id = tx["event"]["event_id"]
                if event_id in ids or (event_id in seen if seen is not None else self.db.contains(event_id)):
                    return False
                ids.add(event_id)
            if set(block["consensus_metadata"]) != {"votes"}:
                return False
            votes = block["consensus_metadata"]["votes"]
            voters = set()
            for vote in votes:
                who = vote["node_id"]
                if who in voters or who not in self.members or set(vote) != {"node_id", "signature"}:
                    return False
                if not verify(self.members[who]["public_key"], vote_body(height, block["block_hash"], who), vote["signature"]):
                    return False
                voters.add(who)
            return len(voters) >= self.quorum if committed else True
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    def accept(self, block):
        with self.db.lock:
            previous = self.db.tip()
            if block["block_index"] <= previous["block_index"]:
                stored = self.db.blocks(block["block_index"], 1)
                if (not stored or stored[0]["block_hash"] != block["block_hash"] or
                        core(stored[0]) != core(block)):
                    raise ValueError("Conflicting committed history")
                return False
            if not self.validate(block, previous):
                raise ValueError("Invalid block or quorum certificate")
            self.db.commit(block)
            return True

    def verify_all(self):
        previous, seen, count, start = None, set(), 0, 0
        while True:
            blocks = self.db.blocks(start, 128)
            if not blocks:
                break
            for block in blocks:
                if previous is None:
                    if block != genesis(self.members):
                        return False, count
                elif not self.validate(block, previous, seen=seen):
                    return False, count
                seen.update(tx["event"]["event_id"] for tx in block["transactions"])
                previous, count = block, count + 1
            start = blocks[-1]["block_index"] + 1
        return count > 0, count
