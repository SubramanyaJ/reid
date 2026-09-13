"""Fixed-membership, majority certificates with a height-rotating proposer.

Persisted one-vote-per-height and proposal replay. No view change and no BFT claim.
"""
import time
from ..provenance.crypto import digest, sign
from ..provenance.ledger import core, vote_body


class Consensus:
    def __init__(self, node_id, key, ledger, max_transactions=64):
        self.node_id, self.key, self.ledger = node_id, key, ledger
        self.db = ledger.db
        self.max_transactions = max_transactions
        self.state = "IDLE"

    def propose(self):
        with self.db.lock:
            previous = self.db.tip()
            height = previous["block_index"] + 1
            if self.ledger.proposer(height) != self.node_id:
                self.state = "FOLLOWING"
                return None
            existing = self.db.proposal(height)
            if existing:
                self.state = "PROPOSING"
                return existing
            transactions = self.db.pending(self.max_transactions)
            if not transactions:
                self.state = "IDLE"
                return None
            block = {"block_index": height, "timestamp": max(time.time(), previous["timestamp"]),
                     "proposer_id": self.node_id, "transactions": transactions,
                     "previous_block_hash": previous["block_hash"]}
            block["block_hash"] = digest(core(block))
            block["proposal_signature"] = sign(self.key, {"domain": "reid-proposal-v1", "block_hash": block["block_hash"]})
            block["consensus_metadata"] = {"votes": []}
            self.state = "PROPOSING"
            return self.db.proposal(height, block)

    def vote(self, block):
        with self.db.lock:
            if not self.ledger.validate(block, self.db.tip(), committed=False):
                raise ValueError("Invalid proposal")
            body = vote_body(block["block_index"], block["block_hash"], self.node_id)
            vote = {"node_id": self.node_id, "signature": sign(self.key, body)}
            return self.db.record_vote(block["block_index"], block["block_hash"], vote)

    def finalize(self, block, votes):
        result = {**block, "consensus_metadata": {"votes": sorted(votes, key=lambda v: v["node_id"])}}
        self.ledger.accept(result)
        self.state = "COMMITTED"
        return result
