"""Per-run telemetry and independently annotated identity evaluation.

No labels are inferred from predictions. JSON checkpoints are atomic; an abrupt
termination leaves the last checkpoint marked running rather than complete.
"""
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


class LiveMetrics:
    def __init__(self, directory, node_id, profile, settings, filename='metrics_live.json'):
        self.directory = Path(directory)
        self.run_id = str(uuid.uuid4())
        self.archive = self.directory / 'runs' / self.run_id
        self.archive.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.started, self.clock = utc(), time.monotonic()
        self.node_id, self.profile, self.settings = node_id, profile, settings
        self.input_metadata = {}
        self.filename, self.accuracy_override = filename, None
        self.counts = Counter({key: 0 for key in ('frames', 'warmup_frames', 'evaluated_frames',
            'candidate_boxes', 'confirmed_track_frames', 'frame_errors', 'observations')})
        self.decisions = Counter()
        self.total_ms, self.min_ms, self.max_ms = 0., None, None
        self.last_flush, self.ended, self.state = 0., None, 'running'
        self.sequence = 0
        with (self.archive / 'labels.csv').open('w', newline='', encoding='utf-8') as stream:
            csv.writer(stream).writerow(['observation_id', 'truth_id'])
        self.flush()

    def observation(self, record):
        with self.lock:
            self.sequence += 1
            row = {**record, 'observation_id': f'{self.run_id}:{self.sequence}',
                   'run_id': self.run_id, 'camera_id': self.node_id}
            decision = row['evidence']['decision']
            self.counts['observations'] += 1
            self.decisions[decision] += 1
            # A stale track binding is not a resolved decision on this observation.
            row['resolved_global_id'] = row.get('global_id') if decision in ('MATCH', 'NO_MATCH') else None
            with (self.archive / 'observations.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, allow_nan=False) + '\n')
            if decision != 'SKIP_QUALITY':
                with (self.archive / 'labels.csv').open('a', newline='', encoding='utf-8') as stream:
                    csv.writer(stream).writerow([row['observation_id'], ''])
            return row

    def frame(self, milliseconds, warmup, detections, confirmed):
        with self.lock:
            self.counts['frames'] += 1
            self.counts['warmup_frames' if warmup else 'evaluated_frames'] += 1
            self.counts['candidate_boxes'] += detections
            self.counts['confirmed_track_frames'] += confirmed
            self.total_ms += milliseconds
            self.min_ms = milliseconds if self.min_ms is None else min(self.min_ms, milliseconds)
            self.max_ms = milliseconds if self.max_ms is None else max(self.max_ms, milliseconds)
            if time.monotonic() - self.last_flush >= 5:
                self.flush()

    def error(self):
        with self.lock:
            self.counts['frame_errors'] += 1
            self.flush()

    def snapshot(self):
        with self.lock:
            elapsed = max(0., (self.finished_clock if self.ended else time.monotonic()) - self.clock)
            frames = self.counts['frames']
            return {
                'schema_version': 1, 'run_id': self.run_id, 'node_id': self.node_id,
                'started_at': self.started, 'updated_at': utc(), 'ended_at': self.ended,
                'state': self.state, 'descriptor_profile': self.profile, 'settings': self.settings,
                'input': dict(self.input_metadata),
                'elapsed_seconds': elapsed, 'counts': dict(self.counts),
                'decisions': dict(self.decisions),
                'performance': {
                    'processing_mean_ms': self.total_ms / frames if frames else None,
                    'processing_min_ms': self.min_ms, 'processing_max_ms': self.max_ms,
                    'processing_fps': frames * 1000 / self.total_ms if self.total_ms else None,
                    'wall_fps': frames / elapsed if elapsed else None,
                    'scope': 'Successful process_frame calls including warmup, JPEG and per-frame work; capture/network wait excluded from processing time. Wall FPS includes idle time.'},
                'accuracy': deepcopy(self.accuracy_override) if self.accuracy_override is not None else {
                    'status': 'ground_truth_required',
                    'pairwise_reid': None, 'cross_camera_pairwise_reid': None,
                    'detection_precision': None, 'detection_recall': None, 'detection_f1': None,
                    'bbox_iou': None, 'mask_iou': None, 'mask_dice': None,
                    'rank1': None, 'mAP': None, 'IDF1': None, 'MOTA': None,
                    'reason': 'Predicted IDs are not ground truth. Use live-evaluate for annotated identity pairs; use the offline labeled crops/frames suite for ranking and geometry. IDF1/MOTA are not implemented.'},
                'annotation_directory': str(self.archive.resolve()),
                'checkpoint_interval_seconds': 5,
            }

    def flush(self, state=None):
        with self.lock:
            if state:
                self.state, self.ended, self.finished_clock = state, utc(), time.monotonic()
            value = self.snapshot()
            atomic_json(self.archive / self.filename, value)
            atomic_json(self.directory / self.filename, value)
            self.last_flush = time.monotonic()


def pair_counts(rows):
    """Count unordered resolved pairs in linear time using contingency counts."""
    choose = lambda n: n * (n - 1) // 2
    joint = Counter((r['truth_id'], r['resolved_global_id']) for r in rows)
    truth = Counter(r['truth_id'] for r in rows)
    predicted = Counter(r['resolved_global_id'] for r in rows)
    tp = sum(choose(n) for n in joint.values())
    fp = sum(choose(n) for n in predicted.values()) - tp
    fn = sum(choose(n) for n in truth.values()) - tp
    return {'tp': tp, 'fp': fp, 'fn': fn, 'tn': choose(len(rows)) - tp - fp - fn}


def pair_summary(counts):
    tp, fp, fn = (counts[k] for k in ('tp', 'fp', 'fn'))
    return {**counts, 'precision': tp/(tp+fp) if tp+fp else None,
            'recall': tp/(tp+fn) if tp+fn else None,
            'f1': 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}


def evaluate_live(run_dirs, labels_path, out):
    """Join human labels to immutable observation IDs; never score blank labels."""
    runs, observations = [], {}
    for directory in run_dirs:
        directory = Path(directory)
        runs.append(json.loads((directory / 'metrics_live.json').read_text(encoding='utf-8')))
        path = directory / 'observations.jsonl'
        for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
            row = json.loads(line)
            if row['evidence']['decision'] == 'SKIP_QUALITY':
                continue
            if row['observation_id'] in observations:
                raise ValueError('Duplicate run or observation ID')
            observations[row['observation_id']] = row
    labels_path = Path(labels_path)
    with labels_path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not {'observation_id', 'truth_id'}.issubset(reader.fieldnames or []):
            raise ValueError('Labels need observation_id,truth_id columns')
        labels = list(reader)
    seen, annotated = set(), []
    for row in labels:
        key = row['observation_id'].strip()
        if key in seen or key not in observations:
            raise ValueError('Duplicate or unknown observation ID in labels')
        seen.add(key)
        if row['truth_id'].strip():
            annotated.append({**observations[key], 'truth_id': row['truth_id'].strip()})
    if not annotated:
        raise ValueError('Provide independently annotated, nonblank truth_id values')
    resolved = [r for r in annotated if r.get('resolved_global_id')]
    all_pairs = pair_counts(resolved)
    cross = dict(all_pairs)
    for camera in {r['camera_id'] for r in resolved}:
        within = pair_counts([r for r in resolved if r['camera_id'] == camera])
        for key in cross:
            cross[key] -= within[key]
    report = {
        'schema_version': 1, 'created_at': utc(), 'source_runs': runs,
        'accuracy': {'status': 'annotated_identity_pairs', 'eligible_observations': len(observations),
                     'annotated_observations': len(annotated), 'resolved_observations': len(resolved),
                     'annotation_coverage': len(annotated)/len(observations),
                     'resolution_coverage': len(resolved)/len(annotated),
                     'pairwise_reid': pair_summary(all_pairs),
                     'cross_camera_pairwise_reid': pair_summary(cross),
                     'scope': 'Resolved annotated pairs only; not rank-1, mAP, IDF1 or MOTA. Partial labeling can bias results; report both coverage values.'},
        'labels_sha256': hashlib.sha256(labels_path.read_bytes()).hexdigest(),
    }
    out = Path(out).resolve()
    if out == labels_path.resolve() or any(out.is_relative_to(Path(d).resolve()) for d in run_dirs):
        raise ValueError('Write the annotated report outside the original run directories')
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(out, report)
    return report
