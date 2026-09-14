import asyncio
import csv
import json
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from reid.metrics.live import LiveMetrics, evaluate_live
from reid.runtime import Runtime
from reid.network.api import create_app
from reid.provenance.crypto import save_private


def observation(metrics, prediction, decision='MATCH'):
    return metrics.observation({'global_id': prediction, 'evidence': {'decision': decision}})


def test_live_checkpoint_and_finalization(tmp_path):
    metrics = LiveMetrics(tmp_path, 'C1', 'test', {})
    assert json.loads((tmp_path/'metrics_live.json').read_text())['state'] == 'running'
    metrics.frame(10., True, 0, 0)
    metrics.frame(30., False, 2, 1)
    observation(metrics, 'A')
    observation(metrics, 'A', 'SKIP_QUALITY')
    metrics.flush('complete')
    value = json.loads((tmp_path/'metrics_live.json').read_text())
    assert value['performance']['processing_mean_ms'] == 20
    assert value['performance']['processing_fps'] == 50
    assert value['counts']['evaluated_frames'] == 1
    assert value['ended_at'] and value['accuracy']['mAP'] is None
    assert value == json.loads((metrics.archive/'metrics_live.json').read_text())
    assert len(list(csv.DictReader((metrics.archive/'labels.csv').open()))) == 1
    second = LiveMetrics(tmp_path, 'C1', 'test', {})
    assert second.run_id != metrics.run_id
    assert json.loads((metrics.archive/'metrics_live.json').read_text()) == value


def test_annotated_pairs_and_abstention(tmp_path):
    a, b = [LiveMetrics(tmp_path/n, n, 'test', {}) for n in ['C1', 'C2']]
    rows = [(observation(a, 'A'), 'car1'), (observation(a, 'B'), 'car2'),
            (observation(b, 'A'), 'car1'), (observation(b, 'A'), 'car2'),
            (observation(b, 'A', 'UNCERTAIN'), 'car3')]
    labels = tmp_path/'labels.csv'
    with labels.open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['observation_id', 'truth_id'])
        writer.writerows((r['observation_id'], truth) for r, truth in rows)
    result = evaluate_live([a.archive, b.archive], labels, tmp_path/'scored.json')['accuracy']
    assert result['resolution_coverage'] == .8
    assert result['annotation_coverage'] == 1
    assert {k: result['pairwise_reid'][k] for k in ['tp','fp','fn','tn']} == dict(tp=1,fp=2,fn=1,tn=2)
    assert {k: result['cross_camera_pairwise_reid'][k] for k in ['tp','fp','fn','tn']} == dict(tp=1,fp=1,fn=1,tn=1)
    with pytest.raises(ValueError, match='outside'):
        evaluate_live([a.archive, b.archive], labels, a.archive/'metrics_live.json')
    with pytest.raises(ValueError, match='Duplicate'):
        evaluate_live([a.archive, a.archive], labels, tmp_path/'bad.json')


def test_blank_labels_rejected(tmp_path):
    m = LiveMetrics(tmp_path/'run', 'C1', 'test', {})
    observation(m, 'A')
    with pytest.raises(ValueError, match='nonblank'):
        evaluate_live([m.archive], m.archive/'labels.csv', tmp_path/'bad.json')


def test_runtime_lifecycle_without_camera(config, consortium, tmp_path):
    keys, members = consortium
    key_path = tmp_path/'C1.pem'
    save_private(key_path, keys['C1'])
    config['members'] = list(members.values())
    config['node'].update(id='C1', private_key=str(key_path), database=str(tmp_path/'state.sqlite'))
    config['camera']['source'] = None
    runtime = Runtime(config)
    async def idle_network():
        await asyncio.Event().wait()
    runtime.network.run = idle_network
    requested = []
    runtime.request_shutdown = lambda: requested.append(True)
    with TestClient(create_app(runtime), client=('127.0.0.1', 50000)) as client:
        runtime.process_frame(np.zeros((180, 320, 3), np.uint8), 1., warm=True)
        assert client.get('/api/metrics').json()['counts']['frames'] == 1
        assert client.post('/api/shutdown').status_code == 200
        assert requested == [True]
    assert json.loads((tmp_path/'metrics_live.json').read_text())['state'] == 'complete'
    # A separate runtime is needed after the first lifespan closes its database.
    runtime = Runtime(config)
    with TestClient(create_app(runtime), client=('10.78.223.1', 50000)) as client:
        assert client.post('/api/shutdown').status_code == 403
