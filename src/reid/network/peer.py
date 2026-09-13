import asyncio
import logging
import time
import httpx
from ..provenance.events import validate_packet

log = logging.getLogger(__name__)


class PeerNetwork:
    def __init__(self, runtime):
        self.runtime = runtime
        self.cfg, self.db, self.ledger, self.consensus = runtime.cfg, runtime.db, runtime.ledger, runtime.consensus
        self.members = self.ledger.members
        self.peers = {key: value for key, value in self.members.items() if key != runtime.node_id}
        self.statuses = {key: {"state": "UNKNOWN", "height": None} for key in self.peers}
        self.client = None

    async def request(self, node, path, payload=None):
        url = self.peers[node]["url"].rstrip("/") + path
        response = await (self.client.get(url) if payload is None else self.client.post(url, json=payload))
        response.raise_for_status()
        return response.json()

    async def synchronize(self, node):
        status = await self.request(node, "/peer/status")
        if status["genesis"] != self.db.blocks(0, 1)[0]["block_hash"] or status["node_id"] != node:
            raise ValueError("Peer membership/genesis mismatch")
        self.statuses[node] = {"state": "ONLINE", "height": status["height"], "last_contact": time.time()}
        tip = self.db.tip()
        if status["height"] == tip["block_index"] and status["tip_hash"] != tip["block_hash"]:
            raise ValueError("Peer reports conflicting chain tip")
        # Bounded work each tick; late nodes repeatedly fetch subsequent batches.
        if status["height"] > tip["block_index"]:
            blocks = await self.request(node, f"/peer/blocks?start={tip['block_index'] + 1}&limit={self.cfg['network']['sync_batch']}")
            for block in blocks:
                self.ledger.accept(block)
                self.runtime.apply_block(block)
        # Current-state exchange sends signed off-ledger packets, never a database image.
        cursor_key = "state_cursor:" + node
        cursor = self.db.get(cursor_key, "")
        response = await self.request(node, "/peer/state?after=" + cursor)
        for packet in response["packets"]:
            if not validate_packet(packet, self.members, self.runtime.descriptor.profile):
                raise ValueError("Invalid signed identity state packet")
            if self.db.contains(packet["transaction"]["event"]["event_id"]):
                self.runtime.receive_packet(packet)
        self.db.set(cursor_key, response["next_cursor"] if response["has_more"] else "")

    async def gossip(self, node):
        # Repeated pending gossip ensures reconnecting peers eventually receive events.
        for tx in self.db.pending(16):
            packet = self.db.packet(tx["event"]["event_id"])
            if packet:
                await self.request(node, "/peer/transaction", packet)

    async def contact(self, node):
        try:
            await self.synchronize(node)
            await self.gossip(node)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            self.statuses[node] = {"state": "UNREACHABLE", "height": None, "error": str(error)[:160]}
            log.debug("Peer %s: %s", node, error)

    async def collect_vote(self, node, block):
        try:
            vote = await self.request(node, "/peer/proposal", block)
            if vote.get("node_id") != node:
                return None
            return vote
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return None

    async def broadcast_commit(self, node, block):
        try:
            await self.request(node, "/peer/commit", block)
        except (httpx.HTTPError, ValueError):
            pass  # Periodic verified block sync repairs missed broadcasts.

    async def tick(self):
        await asyncio.gather(*(self.contact(node) for node in self.peers))
        block = self.consensus.propose()
        if block is None:
            return
        votes = [self.consensus.vote(block)]
        others = await asyncio.gather(*(self.collect_vote(node, block) for node in self.peers))
        # Verify each vote independently so a malformed vote cannot poison a valid quorum.
        for vote in others:
            if vote is None:
                continue
            probe = {**block, "consensus_metadata": {"votes": [vote]}}
            if self.ledger.validate(probe, self.db.tip(), committed=False):
                votes.append(vote)
        if len(votes) < self.ledger.quorum:
            self.consensus.state = f"WAITING_QUORUM {len(votes)}/{self.ledger.quorum}"
            return
        committed = self.consensus.finalize(block, votes)
        self.runtime.apply_block(committed)
        await asyncio.gather(*(self.broadcast_commit(node, committed) for node in self.peers))

    async def run(self):
        async with httpx.AsyncClient(timeout=self.cfg["network"]["timeout_seconds"], trust_env=False) as client:
            self.client = client
            while not self.runtime.stop.is_set():
                try:
                    await self.tick()
                except (ValueError, KeyError, httpx.HTTPError):
                    log.exception("Consensus/sync tick failed; retrying")
                    self.consensus.state = "RETRY"
                await asyncio.sleep(self.cfg["consensus"]["interval_seconds"])
