"""Persistent human labels for frozen observation/candidate comparisons."""
import csv
import json
from .live import utc
from ..visualization.thumbnails import Thumbnails


class ReviewStore:
    def __init__(self, db):
        self.db = db
        with db.lock, db.conn:
            db.conn.execute('CREATE TABLE IF NOT EXISTS review_images(event_id TEXT PRIMARY KEY, jpeg BLOB NOT NULL)')
            db.conn.execute('''CREATE TABLE IF NOT EXISTS match_reviews(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT UNIQUE NOT NULL,
                record TEXT NOT NULL, predicted_match INTEGER NOT NULL,
                same_object INTEGER, reviewed_at TEXT)''')

    def capture_preview(self, event_id, crop, mask):
        jpeg = Thumbnails.encode(crop, mask, size=480)
        with self.db.lock, self.db.conn:
            self.db.conn.execute('INSERT OR IGNORE INTO review_images VALUES (?,?)', (event_id, jpeg))

    def image(self, event_id):
        with self.db.lock:
            row = self.db.conn.execute('SELECT jpeg FROM review_images WHERE event_id=?', (event_id,)).fetchone()
            return bytes(row[0]) if row else None

    def record(self, record):
        evidence = record['evidence']
        if evidence.get('decision') != 'MATCH':
            return
        if not evidence.get('matched_event_id') or not evidence.get('matched_global_id'):
            return
        if self.image(record['preview_id']) is None or self.image(evidence['matched_event_id']) is None:
            raise ValueError('Cannot queue a comparison without both preserved object images')
        with self.db.lock, self.db.conn:
            self.db.conn.execute('''INSERT OR IGNORE INTO match_reviews
                (observation_id,record,predicted_match) VALUES (?,?,?)''',
                (record['observation_id'], json.dumps(record), int(evidence['decision'] == 'MATCH')))

    def next_pending(self):
        with self.db.lock:
            row = self.db.conn.execute('SELECT sequence,record FROM match_reviews WHERE same_object IS NULL ORDER BY sequence LIMIT 1').fetchone()
        if not row:
            return None
        record = json.loads(row[1])
        return {**record, 'sequence': row[0],
                'observed_image': f"/api/review/images/{record['preview_id']}",
                'candidate_image': f"/api/review/images/{record['evidence']['matched_event_id']}"}

    def answer(self, observation_id, same_object):
        if type(same_object) is not bool:
            raise ValueError('same_object must be a boolean')
        with self.db.lock, self.db.conn:
            row = self.db.conn.execute('SELECT same_object FROM match_reviews WHERE observation_id=?', (observation_id,)).fetchone()
            if row is None:
                raise KeyError(observation_id)
            if row[0] is not None:
                if row[0] != int(same_object):
                    raise ValueError('This comparison has already been reviewed differently')
                return
            self.db.conn.execute('UPDATE match_reviews SET same_object=?,reviewed_at=? WHERE observation_id=?',
                                 (int(same_object), utc(), observation_id))

    def summary(self):
        with self.db.lock:
            grouped = self.db.conn.execute('SELECT predicted_match,same_object,COUNT(*) FROM match_reviews GROUP BY predicted_match,same_object').fetchall()
            abstentions = self.db.conn.execute("SELECT COUNT(*) FROM match_reviews WHERE json_extract(record,'$.evidence.decision')='UNCERTAIN'").fetchone()[0]
        counts = dict(tp=0, fp=0, fn=0, tn=0)
        total = reviewed = 0
        for predicted, actual, count in grouped:
            total += count
            if actual is None:
                continue
            reviewed += count
            key = 'tp' if predicted and actual else 'fp' if predicted else 'fn' if actual else 'tn'
            counts[key] += count
        tp, fp, fn, tn = (counts[k] for k in ('tp', 'fp', 'fn', 'tn'))
        divide = lambda a, b: a/b if b else None
        return {'total': total, 'reviewed': reviewed, 'pending': total-reviewed, 'abstentions': abstentions,
                'coverage': divide(reviewed, total), **counts,
                'accuracy': divide(tp+tn, reviewed), 'precision': divide(tp, tp+fp),
                'recall': divide(tp, tp+fn), 'f1': divide(2*tp, 2*tp+fp+fn)}

    def accuracy(self, final=False):
        summary = self.summary()
        return {'status': 'human_reviewed' if final and summary['reviewed'] else 'no_comparisons' if final else 'pending_review',
                'label_source': 'human approval/rejection of preserved observation/candidate image pairs',
                'match_decisions': summary,
                'scope': 'Only MATCH decisions are reviewed. Human same-object is the reference. Scores describe accepted matches only; recall/F1 do not measure missed matches or overall retrieval performance.',
                'unavailable': {'detection_and_mask_accuracy': 'Requires annotated boxes/masks',
                                'rank1_and_mAP': 'Requires labeled query/gallery identities and complete rankings',
                                'IDF1_and_MOTA': 'Requires frame-level identity ground truth; not implemented'},
                'undefined_ratios': 'null means no examples in the denominator; never replaced with fabricated zero accuracy'}

    def export(self, path):
        with self.db.lock:
            rows = self.db.conn.execute('SELECT observation_id,record,predicted_match,same_object,reviewed_at FROM match_reviews ORDER BY sequence').fetchall()
        with path.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['observation_id','frame_index','video_time_seconds','predicted_decision',
                             'predicted_match','same_object','reviewed_at','observed_event_id','candidate_event_id'])
            for key, value, predicted, actual, timestamp in rows:
                record = json.loads(value)
                writer.writerow([key, record.get('frame_index'), record.get('video_time_seconds'),
                    record['evidence']['decision'], predicted, actual, timestamp, record['preview_id'],
                    record['evidence']['matched_event_id']])
