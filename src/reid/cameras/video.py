"""Finite file decoding with a media clock, independent of processing speed."""
import math
from pathlib import Path
import cv2


class VideoFile:
    def __init__(self, path, fps=None):
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise ValueError(f'Video file does not exist: {self.path}')
        self.capture = cv2.VideoCapture(str(self.path))
        self.decoded_frames = 0
        try:
            if not self.capture.isOpened():
                raise ValueError(f'Cannot decode video: {self.path}')
            reported_fps = float(self.capture.get(cv2.CAP_PROP_FPS))
            self.fps = reported_fps if fps is None else float(fps)
            if not math.isfinite(self.fps) or self.fps <= 0:
                raise ValueError('Video FPS is unavailable or invalid; pass a positive --fps')
            count = float(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
            self.expected_frames = round(count) if math.isfinite(count) and count > 0 else None
            self.metadata = {
                'kind': 'video_file', 'path': str(self.path), 'fps': self.fps,
                'reported_fps': reported_fps if math.isfinite(reported_fps) else None,
                'reported_frame_count': self.expected_frames,
                'timestamp_basis': 'zero-based frame index / FPS; constant-frame-rate timeline',
                'pacing': 'all decoded frames, as fast as processing allows; no reconnect or looping',
            }
        except BaseException:
            self.close()
            raise

    def frames(self, width, height):
        while True:
            ok, frame = self.capture.read()
            if not ok or frame is None:
                if self.decoded_frames == 0:
                    raise ValueError('Video contains no decodable frames')
                if self.expected_frames is not None and self.decoded_frames < self.expected_frames:
                    raise ValueError(f'Video ended early: decoded {self.decoded_frames} of '
                                     f'{self.expected_frames} reported frames')
                return
            index = self.decoded_frames
            self.decoded_frames += 1
            yield cv2.resize(frame, (width, height)), index / self.fps

    def close(self):
        self.capture.release()
