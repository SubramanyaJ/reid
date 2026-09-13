"""Bounded-rate capture, reconnect, and fresh warm-up after each connection."""
import logging
import time
import cv2

log = logging.getLogger(__name__)


class Camera:
    def __init__(self, config):
        self.config = config
        self.state = "DISABLED" if config["source"] is None else "CONNECTING"

    def frames(self, stop):
        cfg = self.config
        source = cfg["source"]
        if source is None:
            return
        while not stop.is_set():
            self.state = "CONNECTING"
            cap = cv2.VideoCapture()
            try:
                if isinstance(source, str) and source.startswith(("http://", "https://", "rtsp://")):
                    cap.open(source, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
                else:
                    cap.open(source)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                if not cap.isOpened():
                    self.state = "DISCONNECTED"
                    stop.wait(cfg["reconnect_seconds"])
                    continue
                start = time.monotonic()
                first = True
                while not stop.is_set():
                    tick = time.monotonic()
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        self.state = "DISCONNECTED"
                        break
                    frame = cv2.resize(frame, (cfg["width"], cfg["height"]))
                    warm = tick - start < cfg["warmup_seconds"]
                    self.state = "WARMUP" if warm else "ONLINE"
                    yield frame, time.time(), warm, first
                    first = False
                    stop.wait(max(0., 1 / min(24., cfg["fps"]) - (time.monotonic() - tick)))
            except cv2.error:
                log.exception("Camera backend error")
                self.state = "DISCONNECTED"
            finally:
                cap.release()
            stop.wait(cfg["reconnect_seconds"])
        self.state = "STOPPED"
