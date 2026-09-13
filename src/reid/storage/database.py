"""One local SQLite file, distinct operational, gallery/sidecar, and ledger tables."""
import json
from pathlib import Path
import sqlite3
import threading
from ..provenance.crypto import canonical


class Database:
    def __init__(self, path):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS operational(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS packets(event_id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pending(event_id TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS blocks(height INTEGER PRIMARY KEY, hash TEXT UNIQUE NOT NULL, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS committed(event_id TEXT PRIMARY KEY, height INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS votes(height INTEGER PRIMARY KEY, hash TEXT NOT NULL, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS proposals(height INTEGER PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS identities(global_id TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)

    def get(self, key, default=None):
        with self.lock:
            row = self.conn.execute("SELECT value FROM operational WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO operational VALUES (?,?)", (key, canonical(value).decode()))

    def tip(self):
        with self.lock:
            row = self.conn.execute("SELECT value FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def blocks(self, start=0, limit=32):
        with self.lock:
            rows = self.conn.execute("SELECT value FROM blocks WHERE height>=? ORDER BY height LIMIT ?", (start, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def contains(self, event_id):
        with self.lock:
            return self.conn.execute("SELECT 1 FROM committed WHERE event_id=?", (event_id,)).fetchone() is not None

    def put_packet(self, packet):
        event_id = packet["transaction"]["event"]["event_id"]
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT value FROM packets WHERE event_id=?", (event_id,)).fetchone()
            value = canonical(packet).decode()
            if existing and existing[0] != value:
                raise ValueError("Conflicting packet for event ID")
            self.conn.execute("INSERT OR IGNORE INTO packets VALUES (?,?)", (event_id, value))
            if not self.contains(event_id):
                self.conn.execute("INSERT OR IGNORE INTO pending VALUES (?,?)", (event_id, canonical(packet["transaction"]).decode()))

    def packet(self, event_id):
        with self.lock:
            row = self.conn.execute("SELECT value FROM packets WHERE event_id=?", (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def packets(self, after="", limit=64, committed_only=True):
        with self.lock:
            sql = "SELECT p.event_id,p.value FROM packets p "
            if committed_only:
                sql += "JOIN committed c ON p.event_id=c.event_id "
            rows = self.conn.execute(sql + "WHERE p.event_id>? ORDER BY p.event_id LIMIT ?", (after, limit)).fetchall()
        return [(r[0], json.loads(r[1])) for r in rows]

    def pending(self, limit=64):
        with self.lock:
            rows = self.conn.execute("SELECT value FROM pending ORDER BY event_id LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def record_vote(self, height, block_hash, vote):
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT hash,value FROM votes WHERE height=?", (height,)).fetchone()
            if existing:
                if existing[0] != block_hash:
                    raise ValueError("Already voted for a different block at this height")
                return json.loads(existing[1])
            self.conn.execute("INSERT INTO votes VALUES (?,?,?)", (height, block_hash, canonical(vote).decode()))
        return vote

    def proposal(self, height, block=None):
        with self.lock, self.conn:
            if block:
                self.conn.execute("INSERT OR IGNORE INTO proposals VALUES (?,?)", (height, canonical(block).decode()))
            row = self.conn.execute("SELECT value FROM proposals WHERE height=?", (height,)).fetchone()
        return json.loads(row[0]) if row else None

    def commit(self, block):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO blocks VALUES (?,?,?)", (block["block_index"], block["block_hash"], canonical(block).decode()))
            for tx in block["transactions"]:
                event_id = tx["event"]["event_id"]
                self.conn.execute("INSERT INTO committed VALUES (?,?)", (event_id, block["block_index"]))
                self.conn.execute("DELETE FROM pending WHERE event_id=?", (event_id,))

    def save_identity(self, identity):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO identities VALUES (?,?)", (identity["global_id"], canonical(identity).decode()))

    def identities(self):
        with self.lock:
            rows = self.conn.execute("SELECT value FROM identities ORDER BY global_id").fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self):
        with self.lock:
            self.conn.close()
