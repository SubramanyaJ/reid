"""Run the camera pipeline on a finite local video, without starting peers."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import logging
import math
from pathlib import Path
import time
import uuid
import cv2
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ..cameras.video import VideoFile
from ..config import DEFAULTS, load_config
from ..provenance.crypto import public_text, save_private
from ..runtime import Runtime

log = logging.getLogger(__name__)


def replay_video(path, out=None, config=None, fps=None, warmup_seconds=None):
    cfg = load_config(config) if config else deepcopy(DEFAULTS)
    if warmup_seconds is not None:
        if not math.isfinite(warmup_seconds) or warmup_seconds < 0:
            raise ValueError('--warmup-seconds must be finite and nonnegative')
        cfg['camera']['warmup_seconds'] = warmup_seconds
    video = VideoFile(path, fps)
    runtime = None
    state = 'failed'
    try:
        if out is None:
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
            out = Path('experiments/results') / f'video-{stamp}-{uuid.uuid4().hex[:8]}'
        out = Path(out).resolve()
        if out.exists():
            raise ValueError('Replay output already exists; choose a new --out directory')
        out.mkdir(parents=True)
        # Separate membership, keys and gallery prevent contamination of LAN state.
        key = Ed25519PrivateKey.generate()
        cfg['node'].update(id='VIDEO', private_key=str(out/'key.pem'), database=str(out/'state.sqlite'))
        cfg['members'] = [{'id': 'VIDEO', 'url': 'http://127.0.0.1:9000',
                           'public_key': public_text(key.public_key())}]
        cfg['camera']['source'] = None
        cfg['context']['transitions'] = {}
        save_private(cfg['node']['private_key'], key)
        (out/'replay_config.yaml').write_text(yaml.safe_dump(cfg, sort_keys=False), encoding='utf-8')
        runtime = Runtime(cfg)
        epoch = time.time()
        runtime.live_metrics.input_metadata.update(video.metadata,
            timestamp_epoch=epoch,
            timestamp_note='Observation timestamp = run epoch + video time, not original recording date',
            warmup_seconds=cfg['camera']['warmup_seconds'],
            processing_width=cfg['camera']['width'], processing_height=cfg['camera']['height'])
        runtime.live_metrics.flush()
        digest = hashlib.sha256()
        with video.path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b''):
                digest.update(chunk)
        runtime.live_metrics.input_metadata['sha256'] = digest.hexdigest()
        runtime.live_metrics.flush()
        last_progress = time.monotonic()
        for frame, media_time in video.frames(cfg['camera']['width'], cfg['camera']['height']):
            runtime.observation_context = {'frame_index': video.decoded_frames - 1,
                                           'video_time_seconds': media_time}
            runtime.live_metrics.input_metadata.update(decoded_frames=video.decoded_frames,
                                                       last_video_time_seconds=media_time)
            try:
                runtime.process_frame(frame, epoch + media_time,
                    warm=media_time < cfg['camera']['warmup_seconds'], reconnect=video.decoded_frames == 1)
            except Exception:
                runtime.live_metrics.error()
                raise
            if time.monotonic() - last_progress >= 5:
                log.info('Replay: %d frames, %.1f video seconds', video.decoded_frames, media_time)
                last_progress = time.monotonic()
        state = 'complete'
    except KeyboardInterrupt:
        state = 'interrupted'
        raise
    except Exception as error:
        if runtime is not None:
            runtime.live_metrics.input_metadata['error'] = str(error)
        if isinstance(error, cv2.error):
            raise ValueError(f'Video processing failed: {error}') from error
        raise
    finally:
        video.close()
        if runtime is not None:
            runtime.stop.set()
            runtime.live_metrics.input_metadata['decoded_frames'] = video.decoded_frames
            runtime.live_metrics.input_metadata['decoded_duration_seconds'] = video.decoded_frames / video.fps
            runtime.live_metrics.input_metadata['identity_count'] = len(runtime.gallery.identities)
            try:
                runtime.live_metrics.flush(state)
            finally:
                runtime.db.close()
    report = runtime.live_metrics.snapshot()
    if not report['counts']['evaluated_frames']:
        log.warning('All frames were background warmup; use a longer clip or reduce --warmup-seconds')
    print(f"Processed {report['counts']['frames']} frames. Metrics: {out/'metrics_live.json'}")
    return report
