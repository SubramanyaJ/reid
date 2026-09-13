"""Bounded local preview cache, separate from signed packets and ledger contents."""
import json
import uuid
import cv2
from ..provenance.crypto import canonical


class Thumbnails:
    def __init__(self, db, limit=300, size=240):
        self.db, self.limit, self.size = db, limit, size
        with db.lock, db.conn:
            db.conn.execute('CREATE TABLE IF NOT EXISTS thumbnails(event_id TEXT PRIMARY KEY, timestamp REAL NOT NULL, metadata TEXT NOT NULL, jpeg BLOB NOT NULL)')
            db.conn.execute('CREATE INDEX IF NOT EXISTS thumbnails_time ON thumbnails(timestamp DESC)')

    def put(self, event_id, crop, mask, metadata):
        uuid.UUID(event_id)
        # Suppress background pixels and keep the object's connected silhouette.
        preview = crop.copy()
        preview[mask == 0] = (32, 32, 32)
        scale = min(1., self.size / max(preview.shape[:2]))
        preview = cv2.resize(preview, (max(1, round(preview.shape[1] * scale)), max(1, round(preview.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not ok:
            raise ValueError('Thumbnail encoding failed')
        with self.db.lock, self.db.conn:
            self.db.conn.execute('INSERT OR REPLACE INTO thumbnails VALUES (?,?,?,?)',
                                 (event_id, metadata['timestamp'], canonical(metadata).decode(), encoded.tobytes()))
            self.db.conn.execute('DELETE FROM thumbnails WHERE event_id IN (SELECT event_id FROM thumbnails ORDER BY timestamp DESC,event_id DESC LIMIT -1 OFFSET ?)', (self.limit,))

    def get(self, event_id):
        try:
            uuid.UUID(event_id)
        except (ValueError, TypeError, AttributeError):
            return None
        with self.db.lock:
            row = self.db.conn.execute('SELECT jpeg FROM thumbnails WHERE event_id=?', (event_id,)).fetchone()
        return bytes(row[0]) if row else None

    def recent(self, limit=12):
        with self.db.lock:
            rows = self.db.conn.execute('SELECT event_id,metadata FROM thumbnails ORDER BY timestamp DESC,event_id DESC LIMIT ?', (limit,)).fetchall()
        return [{**json.loads(row[1]), 'preview_id': row[0]} for row in rows]
