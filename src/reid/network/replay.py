"""Local browser-controlled replay and persistent human match review."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from ..cameras.video import VideoFile
from ..metrics.review import ReviewStore
from ..metrics.video import replay_video


class ReplaySession:
    def __init__(self, video, out=None, config=None, fps=None, warmup_seconds=None):
        probe = VideoFile(video, fps)
        self.video, self.metadata = str(probe.path), dict(probe.metadata)
        probe.close()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        self.out = Path(out or f'experiments/results/review-{stamp}-{uuid.uuid4().hex[:8]}').resolve()
        if self.out.exists():
            raise ValueError('Replay output already exists; choose a new --out directory')
        self.config, self.fps, self.warmup = config, fps, warmup_seconds
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.runtime = self.store = self.thread = None
        self.state, self.error = 'ready', None
        self.eof = False

    def start(self):
        with self.lock:
            if self.state != 'ready':
                return  # Repeated start requests cannot duplicate the worker.
            self.state = 'running'
            self.thread = threading.Thread(target=self._run, name='video-replay', daemon=True)
            self.thread.start()

    def _run(self):
        try:
            replay_video(self.video, self.out, self.config, self.fps, self.warmup, session=self)
        except Exception as error:
            with self.lock:
                self.state, self.error = 'failed', str(error)

    def attach(self, runtime):
        with self.lock:
            self.store = ReviewStore(runtime.db)
            runtime.review_sink = self.store
            self.runtime = runtime
            runtime.live_metrics.accuracy_override = self.store.accuracy()

    def finished(self, state):
        with self.lock:
            self.eof = state == 'complete'
            self.state = 'awaiting_review' if self.eof else state
            self.runtime.live_metrics.accuracy_override = self.store.accuracy()
            self.runtime.live_metrics.flush(self.state)
            self._finalize_if_ready()

    def _finalize_if_ready(self):
        if self.eof and self.store.summary()['pending'] == 0:
            metrics = self.runtime.live_metrics
            self.store.export(metrics.archive/'match_reviews.csv')
            self.store.export(self.out/'match_reviews.csv')
            with metrics.lock:
                metrics.accuracy_override = self.store.accuracy(final=True)
                metrics.filename = 'metrics_live.json'
                metrics.state = 'complete'
                # Preserve the replay-end timing; reviewing must not reduce measured FPS.
                metrics.flush()
            self.state = 'complete'

    def answer(self, observation_id, same_object):
        with self.lock:
            if self.store is None:
                raise ValueError('Start the video first')
            self.store.answer(observation_id, same_object)
            if self.state == 'complete':
                return  # Only identical retries can reach here; keep the final export stable.
            self.runtime.live_metrics.accuracy_override = self.store.accuracy()
            self.runtime.live_metrics.flush()
            self._finalize_if_ready()

    def snapshot(self):
        with self.lock:
            runtime = self.runtime
            live = runtime.live_metrics.snapshot() if runtime else None
            return {'state': self.state, 'error': self.error, 'video': self.video, 'output': str(self.out),
                    'reported_frames': self.metadata['reported_frame_count'],
                    'frames': live['counts']['frames'] if live else 0,
                    'video_time_seconds': live['input'].get('last_video_time_seconds', 0) if live else 0,
                    'performance': live['performance'] if live else None,
                    'review': self.store.summary() if self.store else {'total': 0, 'reviewed': 0, 'pending': 0},
                    'next': self.store.next_pending() if self.store else None,
                    'metrics_ready': self.state == 'complete'}

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(10)
        if self.runtime and (self.thread is None or not self.thread.is_alive()):
            self.runtime.db.close()


def create_replay_app(session):
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await asyncio.to_thread(session.close)

    app = FastAPI(title='Re-ID video review', lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware('http')
    async def local_review_only(request, call_next):
        # Browser writes require same-origin JSON; no cross-origin form submissions.
        if request.method == 'POST':
            origin = request.headers.get('origin')
            expected = f'{request.url.scheme}://{request.headers.get("host", "")}'
            if origin and origin != expected:
                return Response('Cross-origin review request rejected', status_code=403)
            if request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return Response('Use application/json', status_code=415)
        return await call_next(request)

    @app.get('/', response_class=HTMLResponse)
    def monitor():
        return (Path(__file__).parents[1]/'visualization'/'replay.html').read_text(encoding='utf-8')

    @app.get('/api/replay')
    def status():
        return session.snapshot()

    @app.post('/api/replay/start')
    def start():
        session.start()
        return {'state': session.state}

    @app.post('/api/review')
    async def review(request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 2048:
                raise HTTPException(413, 'Review payload too large')
        try:
            value = json.loads(data)
            if not isinstance(value, dict) or set(value) != {'observation_id', 'same_object'}:
                raise ValueError('Provide observation_id and same_object')
            if not isinstance(value['observation_id'], str) or len(value['observation_id']) > 100:
                raise ValueError('Invalid observation ID')
            session.answer(value['observation_id'], value['same_object'])
        except KeyError:
            raise HTTPException(404, 'Unknown comparison')
        except (ValueError, TypeError) as error:
            raise HTTPException(400, str(error))
        return {'saved': True}

    @app.get('/api/review/images/{event_id}')
    def review_image(event_id: str):
        data = session.store.image(event_id) if session.store else None
        if data is None:
            raise HTTPException(404, 'Review image unavailable')
        return Response(data, media_type='image/jpeg')

    @app.get('/api/frame')
    def frame():
        runtime = session.runtime
        if runtime is None:
            return Response(status_code=204)
        with runtime.lock:
            data = runtime.jpeg
        return Response(data, media_type='image/jpeg') if data else Response(status_code=204)

    @app.get('/api/replay/metrics')
    def download_metrics():
        if session.state != 'complete':
            raise HTTPException(409, 'Finish the video and every review before exporting final metrics')
        return FileResponse(session.out/'metrics_live.json', filename='metrics_live.json', media_type='application/json')

    return app


def serve_replay(video, out=None, config=None, fps=None, warmup_seconds=None, port=9000):
    import uvicorn
    if not 1024 <= port <= 65535:
        raise ValueError('--port must be between 1024 and 65535')
    session = ReplaySession(video, out, config, fps, warmup_seconds)
    print(f'Open http://127.0.0.1:{port}/ and click Start video. Output: {session.out}', flush=True)
    uvicorn.run(create_replay_app(session), host='127.0.0.1', port=port, proxy_headers=False)
