import hashlib
from copy import deepcopy
import json
from pathlib import Path
import sys
import cv2
import numpy as np
import pytest
import yaml
from reid.cameras.video import VideoFile
from reid.cli import main
from reid.config import DEFAULTS
from reid.metrics.video import replay_video
from reid.metrics.vision import synthetic_frames
from reid.network.peer import PeerNetwork
from reid.runtime import Runtime


@pytest.fixture
def mp4(tmp_path):
    path = tmp_path/'recorded input.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 20, (640, 360))
    assert writer.isOpened(), 'Test requires MP4 encoding support in OpenCV'
    try:
        for row in synthetic_frames(frames=30):
            writer.write(row['image'])
    finally:
        writer.release()
    return path


def test_mp4_cli_uses_real_pipeline_and_media_time(mp4, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('File replay must not start network services')
    monkeypatch.setattr(PeerNetwork, 'run', forbidden)
    out = tmp_path/'result'
    monkeypatch.setattr(sys, 'argv', ['reid', 'replay', '--video', str(mp4), '--out', str(out)])
    main()
    metrics = json.loads((out/'metrics_live.json').read_text())
    assert metrics['state'] == 'complete'
    assert metrics['counts']['frames'] == 110
    assert metrics['counts']['warmup_frames'] == 80
    assert metrics['counts']['evaluated_frames'] == 30
    assert metrics['counts']['observations'] > 0
    assert metrics['input']['fps'] == 20
    assert metrics['input']['decoded_duration_seconds'] == 5.5
    assert metrics['input']['sha256'] == hashlib.sha256(mp4.read_bytes()).hexdigest()
    assert metrics['accuracy']['pairwise_reid'] is None
    assert metrics['performance']['processing_mean_ms'] > 0
    archive = Path(metrics['annotation_directory'])
    assert metrics == json.loads((archive/'metrics_live.json').read_text())
    observations = [json.loads(line) for line in (archive/'observations.jsonl').read_text().splitlines()]
    assert all(r['video_time_seconds'] == r['frame_index']/20 for r in observations)
    cfg = yaml.safe_load((out/'replay_config.yaml').read_text())
    assert [member['id'] for member in cfg['members']] == ['VIDEO']
    assert cfg['camera']['source'] is None
    assert Path(cfg['node']['database']).parent == out


def test_fps_override_and_finite_eof(mp4):
    source = VideoFile(mp4, fps=10)
    try:
        frames = list(source.frames(320, 180))
        assert len(frames) == 110
        assert frames[-1][1] == 10.9
        assert frames[0][0].shape == (180, 320, 3)
    finally:
        source.close()
    assert not source.capture.isOpened()


@pytest.mark.parametrize('failure,state', [(KeyboardInterrupt, 'interrupted'), (ValueError, 'failed')])
def test_interrupt_or_failure_finalizes_metrics(mp4, tmp_path, monkeypatch, failure, state):
    process = Runtime.process_frame
    calls = []
    def fail_after_frame(self, *args, **kwargs):
        if calls:
            raise failure('test interruption')
        calls.append(True)
        process(self, *args, **kwargs)
    monkeypatch.setattr(Runtime, 'process_frame', fail_after_frame)
    out = tmp_path/'stopped'
    with pytest.raises(failure):
        replay_video(mp4, out)
    metrics = json.loads((out/'metrics_live.json').read_text())
    assert metrics['state'] == state and metrics['ended_at']
    assert metrics['counts']['frames'] == 1
    assert metrics['counts']['frame_errors'] == (state == 'failed')
    # Reopen the database after cleanup; no lingering capture/runtime process.
    import sqlite3
    connection = sqlite3.connect(out/'state.sqlite')
    connection.execute('BEGIN EXCLUSIVE')
    connection.close()


def test_bad_paths_parameters_and_existing_output(mp4, tmp_path):
    out = tmp_path/'existing'
    out.mkdir()
    sentinel = out/'keep.txt'
    sentinel.write_text('preserved')
    with pytest.raises(ValueError, match='already exists'):
        replay_video(mp4, out)
    assert sentinel.read_text() == 'preserved'
    for fps in (0, -1, float('nan')):
        with pytest.raises(ValueError, match='FPS'):
            VideoFile(mp4, fps)
    with pytest.raises(ValueError, match='warmup'):
        replay_video(mp4, tmp_path/'invalid', warmup_seconds=-1)
    with pytest.raises(ValueError, match='does not exist'):
        replay_video(tmp_path/'missing.mp4', tmp_path/'invalid')
    invalid = tmp_path/'invalid.mp4'
    invalid.write_bytes(b'not a video')
    with pytest.raises(ValueError, match='decode'):
        replay_video(invalid, tmp_path/'invalid')
    assert not (tmp_path/'invalid').exists()


def test_decoder_failure_is_not_reported_as_complete(mp4, tmp_path, monkeypatch):
    original = VideoFile.frames
    def truncated(self, width, height):
        yield next(original(self, width, height))
        raise ValueError('Video ended early')
    monkeypatch.setattr(VideoFile, 'frames', truncated)
    out = tmp_path/'truncated'
    with pytest.raises(ValueError, match='ended early'):
        replay_video(mp4, out)
    metrics = json.loads((out/'metrics_live.json').read_text())
    assert metrics['state'] == 'failed'
    assert metrics['input']['error'] == 'Video ended early'


def test_existing_config_is_isolated_and_short_clip_reports_warmup(mp4, tmp_path):
    original_key, original_db = tmp_path/'original.pem', tmp_path/'original.sqlite'
    original_key.write_text('private key must not be read or replaced')
    original_db.write_text('database must not be opened')
    cfg = deepcopy(DEFAULTS)
    cfg['node'].update(private_key=str(original_key), database=str(original_db))
    cfg['members'] = [{'id': 'C1', 'url': 'http://192.0.2.1:9000', 'public_key': 'unused-original'}]
    cfg['camera'].update(source=0, width=320, height=180)
    path = tmp_path/'original.yaml'
    path.write_text(yaml.safe_dump(cfg))
    before = path.read_bytes()
    report = replay_video(mp4, tmp_path/'isolated', config=path, warmup_seconds=20)
    assert report['counts']['warmup_frames'] == 110
    assert report['counts']['evaluated_frames'] == 0
    assert report['input']['processing_width'] == 320
    assert report['state'] == 'complete'
    assert path.read_bytes() == before
    assert original_key.read_text() == 'private key must not be read or replaced'
    assert original_db.read_text() == 'database must not be opened'
