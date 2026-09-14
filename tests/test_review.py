import csv
import json
import time
import uuid
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from reid.metrics.review import ReviewStore
from reid.metrics.vision import synthetic_frames
from reid.network.replay import ReplaySession, create_replay_app
from reid.storage.database import Database
from reid.visualization.thumbnails import Thumbnails


def comparison(store, decision, number):
    observed, reference = str(uuid.uuid4()), str(uuid.uuid4())
    crop = np.full((40, 80, 3), 150, np.uint8)
    mask = np.full((40, 80), 255, np.uint8)
    store.capture_preview(observed, crop, mask)
    store.capture_preview(reference, crop, mask)
    record = {'observation_id': f'run:{number}', 'preview_id': observed,
              'frame_index': number, 'video_time_seconds': number/20,
              'evidence': {'decision': decision, 'matched_event_id': reference, 'matched_global_id': 'G-test'}}
    store.record(record)
    return record


def test_review_confusion_matrix_and_persistence(tmp_path):
    db = Database(str(tmp_path/'state.sqlite'))
    store = ReviewStore(db)
    examples = [('MATCH', True), ('MATCH', False), ('UNCERTAIN', True), ('NO_MATCH', False)]
    rows = [comparison(store, decision, i) for i, (decision, _) in enumerate(examples)]
    for row, (_, label) in zip(rows, examples):
        store.answer(row['observation_id'], label)
    store.answer(rows[0]['observation_id'], True)  # Idempotent repeated click.
    store.record(rows[0])  # No duplicate queue item.
    metrics = store.summary()
    assert {k: metrics[k] for k in ('tp','fp','fn','tn')} == dict(tp=1,fp=1,fn=1,tn=1)
    assert all(metrics[k] == .5 for k in ('accuracy','precision','recall','f1'))
    assert metrics['coverage'] == 1 and metrics['pending'] == 0
    with pytest.raises(ValueError, match='differently'):
        store.answer(rows[0]['observation_id'], False)
    with pytest.raises(ValueError, match='boolean'):
        store.answer(rows[0]['observation_id'], 'true')
    with pytest.raises(KeyError):
        store.answer('missing', True)
    store.export(tmp_path/'labels.csv')
    with (tmp_path/'labels.csv').open() as stream:
        assert len(list(csv.DictReader(stream))) == 4
    db.close()
    db = Database(str(tmp_path/'state.sqlite'))
    assert ReviewStore(db).summary() == metrics
    db.close()


def test_review_images_survive_normal_preview_eviction(tmp_path):
    db = Database(str(tmp_path/'state.sqlite'))
    store = ReviewStore(db)
    row = comparison(store, 'MATCH', 1)
    thumbnails = Thumbnails(db, limit=1)
    crop = np.full((40,80,3), 100, np.uint8)
    mask = np.full((40,80), 255, np.uint8)
    for timestamp in range(3):
        thumbnails.put(str(uuid.uuid4()), crop, mask, {'timestamp': timestamp})
    assert store.image(row['preview_id']) and store.image(row['evidence']['matched_event_id'])
    assert store.next_pending()['observation_id'] == row['observation_id']
    assert store.accuracy()['status'] == 'pending_review'
    db.close()


@pytest.fixture
def review_video(tmp_path):
    path = tmp_path/'review.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 20, (640,360))
    assert writer.isOpened()
    try:
        for row in synthetic_frames(frames=80):
            writer.write(row['image'])
    finally:
        writer.release()
    return path


def test_browser_start_review_and_final_export(review_video, tmp_path):
    out = tmp_path/'reviewed'
    session = ReplaySession(review_video, out)
    with TestClient(create_replay_app(session)) as client:
        assert 'Start video' in client.get('/').text
        assert client.get('/api/replay').json()['state'] == 'ready'
        assert not (out/'metrics_live.json').exists()
        assert client.get('/api/replay/metrics').status_code == 409
        assert client.post('/api/replay/start', json={}, headers={'Origin':'http://evil.example'}).status_code == 403
        assert client.post('/api/replay/start', content='{}').status_code == 415
        assert client.post('/api/replay/start', json={}).status_code == 200
        thread = session.thread
        client.post('/api/replay/start', json={})
        assert session.thread is thread
        thread.join(30)
        assert not thread.is_alive()
        status = client.get('/api/replay').json()
        assert status['state'] == 'awaiting_review', status
        assert status['review']['pending'] > 0
        assert (out/'metrics_pending.json').exists() and not (out/'metrics_live.json').exists()
        elapsed = session.runtime.live_metrics.snapshot()['elapsed_seconds']
        saved = None
        while status['next']:
            row = status['next']
            assert client.get(row['observed_image']).headers['content-type'] == 'image/jpeg'
            assert client.get(row['candidate_image']).status_code == 200
            # Test-only labels; never annotate the user's actual footage automatically.
            answer = {'observation_id': row['observation_id'], 'same_object': row['sequence'] % 2 == 0}
            assert client.post('/api/review', json=answer).status_code == 200
            saved = answer
            status = client.get('/api/replay').json()
        assert status['state'] == 'complete'
        result = client.get('/api/replay/metrics')
        assert result.status_code == 200
        metrics = result.json()
        assert metrics['state'] == 'complete'
        assert metrics['accuracy']['status'] == 'human_reviewed'
        assert metrics['accuracy']['match_decisions']['accuracy'] is not None
        assert metrics['accuracy']['match_decisions']['coverage'] == 1
        assert metrics['elapsed_seconds'] == elapsed
        assert (out/'match_reviews.csv').exists()
        assert (session.runtime.live_metrics.archive/'metrics_live.json').exists()
        assert client.post('/api/review', json=saved).status_code == 200
        assert client.post('/api/review', json={'observation_id':'missing','same_object':True}).status_code == 404
        assert client.post('/api/review', json={'observation_id':saved['observation_id'],'same_object':'true'}).status_code == 400


def test_labels_before_eof_cannot_finalize(review_video, tmp_path):
    session = ReplaySession(review_video, tmp_path/'deferred')
    # Attach a real runtime but keep the file worker unstarted for this lifecycle test.
    from copy import deepcopy
    from reid.config import DEFAULTS
    from reid.provenance.crypto import save_private, public_text
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from reid.runtime import Runtime
    cfg = deepcopy(DEFAULTS)
    key = Ed25519PrivateKey.generate()
    session.out.mkdir()
    save_private(session.out/'key.pem', key)
    cfg['node'].update(id='C1',private_key=str(session.out/'key.pem'),database=str(session.out/'state.sqlite'))
    cfg['members'] = [{'id':'C1','url':'http://127.0.0.1:9000','public_key':public_text(key.public_key())}]
    runtime = Runtime(cfg, metrics_filename='metrics_pending.json')
    session.attach(runtime)
    session.state = 'running'
    row = comparison(session.store, 'MATCH', 1)
    session.answer(row['observation_id'], True)
    assert session.state == 'running'
    assert not (session.out/'metrics_live.json').exists()
    session.finished('interrupted')
    assert session.state == 'interrupted'
    assert not (session.out/'metrics_live.json').exists()
    session.close()
