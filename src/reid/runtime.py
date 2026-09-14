import asyncio
from collections import deque
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import threading
import time
import uuid
import cv2
import numpy as np
from .cameras.capture import Camera
from .consensus.quorum import Consensus
from .detection.foreground import Foreground
from .features.descriptor import MVSVG
from .features.local import LocalVerifier
from .features.quality import observation_quality
from .network.peer import PeerNetwork
from .metrics.live import LiveMetrics
from .plate.matching import aggregate
from .plate.recognizer import PlateRecognizer
from .provenance.crypto import bytes_hash, canonical, load_private, public_text
from .provenance.events import make_packet, validate_packet
from .provenance.ledger import Ledger
from .reid.gallery import Gallery
from .segmentation.refine import crop_candidate
from .storage.database import Database
from .tracking.tracker import Tracker, iou
from .visualization.thumbnails import Thumbnails

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, cfg, metrics_filename='metrics_live.json'):
        self.cfg, self.node_id = cfg, cfg["node"]["id"]
        self.key = load_private(cfg["node"]["private_key"])
        self.members = {m["id"]: m for m in cfg["members"]}
        if public_text(self.key.public_key()) != self.members[self.node_id]["public_key"]:
            raise ValueError("Private key does not match configured consortium membership")
        self.db = Database(cfg["node"]["database"])
        self.ledger = Ledger(self.db, self.members)
        self.integrity, _ = self.ledger.verify_all()
        if not self.integrity:
            raise ValueError("Local ledger failed integrity verification")
        self.descriptor = MVSVG(cfg["features"]["weights"])
        previous_profile = self.db.get("descriptor_profile")
        if previous_profile and previous_profile != self.descriptor.profile:
            raise ValueError("Descriptor settings changed; use a fresh database/gallery")
        self.db.set("descriptor_profile", self.descriptor.profile)
        self.gallery = Gallery(self.db, cfg)
        self.consensus = Consensus(self.node_id, self.key, self.ledger, cfg["consensus"]["max_transactions"])
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.camera = Camera(cfg["camera"])
        self.detector = Foreground(cfg["detection"])
        self.tracker = Tracker(self.node_id, cfg["tracking"], self.db.get("next_track_id", 1))
        self.plates = PlateRecognizer(cfg["plate"]["template_directory"])
        self.local = LocalVerifier(cfg["features"]["local_verifier"])
        self.network = PeerNetwork(self)
        self.events = deque(maxlen=100)
        self.thumbnails = Thumbnails(self.db, cfg["visualization"]["thumbnail_limit"], cfg["visualization"]["thumbnail_size"])
        self.jpeg = None
        self.metrics = {"fps": 0., "detections": 0, "raw_regions": 0, "tracks": [], "candidate_count": 0, "frames": 0,
                        "mask_fraction": 0., "shadow_fraction": 0., "error": None}
        self.log_path = Path(cfg["node"]["database"]).parent / "decisions.jsonl"
        self.live_metrics = LiveMetrics(self.log_path.parent, self.node_id, self.descriptor.profile,
            {key: cfg[key] for key in ('detection', 'tracking', 'features', 'lsh', 'reid')}, metrics_filename)
        self.request_shutdown = None
        self.observation_context = {}
        self.review_sink = None
        self.replay()

    def replay(self):
        cursor = ""
        while True:
            rows = self.db.packets(cursor, 128, committed_only=False)
            if not rows:
                break
            for _, packet in rows:
                tx = packet["transaction"]
                if self.db.contains(tx["event"]["event_id"]) or tx["event"]["node_id"] == self.node_id:
                    if validate_packet(packet, self.members, self.descriptor.profile) and self.committed_packet_matches(packet):
                        self.gallery.apply(packet)
            cursor = rows[-1][0]

    def committed_packet_matches(self, packet):
        event_id = packet["transaction"]["event"]["event_id"]
        with self.db.lock:
            row = self.db.conn.execute("SELECT b.value FROM committed c JOIN blocks b ON b.height=c.height WHERE c.event_id=?", (event_id,)).fetchone()
        if row is None:
            return True
        block = json.loads(row[0])
        return any(tx == packet["transaction"] for tx in block["transactions"])

    def receive_packet(self, packet):
        if not validate_packet(packet, self.members, self.descriptor.profile) or not self.committed_packet_matches(packet):
            raise ValueError("Invalid signed observation packet")
        self.db.put_packet(packet)
        event = packet["transaction"]["event"]
        if self.db.contains(event["event_id"]) or event["node_id"] == self.node_id:
            self.gallery.apply(packet)

    def apply_block(self, block):
        for tx in block["transactions"]:
            packet = self.db.packet(tx["event"]["event_id"])
            if (packet and packet["transaction"] == tx and self.committed_packet_matches(packet)
                    and validate_packet(packet, self.members, self.descriptor.profile)):
                self.gallery.apply(packet)

    def log_decision(self, track, timestamp, evidence, plate, quality):
        record = {"timestamp": timestamp, "local_id": track.local_id, "global_id": track.global_id,
                  "evidence": evidence, "plate": plate, "quality": quality, "preview_id": track.preview_id,
                  **self.observation_context}
        record = self.live_metrics.observation(record)
        if self.review_sink is not None:
            self.review_sink.record(record)
        with self.lock:
            self.events.appendleft(record)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(canonical(record).decode() + "\n")

    def process_frame(self, frame, timestamp, warm=False, reconnect=False):
        processing_start = time.perf_counter()
        if reconnect:
            self.detector.reset()
            self.tracker = Tracker(self.node_id, self.cfg["tracking"], self.db.get("next_track_id", 1))
        mask, shadows, components = self.detector.apply(frame, warm)
        tracks = [] if warm else self.tracker.update(components, timestamp, frame.shape)
        if self.tracker.next_id != self.db.get("next_track_id", 1):
            self.db.set("next_track_id", self.tracker.next_id)
        annotated = frame.copy()
        snapshot = []
        for track in tracks:
            if track.state == "CONFIRMED" and timestamp - track.last_observation >= self.cfg["reid"]["observation_interval"]:
                track.last_observation = timestamp
                crop, object_mask, visibility = crop_candidate(frame, mask, track.bbox, self.cfg["detection"]["grabcut"], track.foreground_mask)
                if crop is None:
                    continue
                occluded = any(other is not track and iou(track.bbox, other.bbox) > .25 for other in tracks)
                quality, details = observation_quality(crop, object_mask, visibility, track.consistency, occluded)
                track.quality = quality
                if quality >= self.cfg["reid"]["min_quality"]:
                    text, confidence = self.plates.recognize(crop)
                    if text:
                        track.readings = (track.readings + [(text, confidence)])[-24:]
                    plate = aggregate(track.readings, self.cfg["plate"]["min_confidence"], self.cfg["plate"]["min_readings"])
                    vector = self.descriptor.extract(crop, object_mask)
                    x, y, w, h = track.bbox
                    position = [float(np.clip((x+w/2) / frame.shape[1], 0, 1)), float(np.clip((y+h/2) / frame.shape[0], 0, 1))]
                    payload = {"vector": vector.tolist(), "plate": plate, "quality": quality, "position": position,
                               "scale": float(np.clip(w*h / (frame.shape[0]*frame.shape[1]), 0, 1)),
                               "local": self.local.extract(crop, object_mask)}
                    occupied = {other.global_id for other in tracks if other is not track and other.global_id}
                    gid, evidence = self.gallery.decide(payload, self.node_id, timestamp, occupied, track.global_id)
                    track.evidence = evidence
                    packet = None
                    if gid:
                        track.global_id = gid
                        _, encoded = cv2.imencode(".png", crop)
                        observation_hash = bytes_hash(encoded.tobytes() + object_mask.tobytes())
                        packet = make_packet(self.key, self.node_id, track.local_id, gid, timestamp, observation_hash,
                                             payload, evidence, self.descriptor.profile)
                    preview_id = packet["transaction"]["event"]["event_id"] if packet else str(uuid.uuid4())
                    if self.review_sink is not None:
                        self.review_sink.capture_preview(preview_id, crop, object_mask)
                    self.thumbnails.put(preview_id, crop, object_mask,
                        {"node_id": self.node_id, "local_id": track.local_id, "global_id": track.global_id,
                         "timestamp": timestamp, "quality": quality, "plate": plate, "evidence": evidence,
                         "submitted": packet is not None})
                    track.preview_id = preview_id
                    if packet:
                        self.receive_packet(packet)
                    self.log_decision(track, timestamp, evidence, plate, details)
                else:
                    self.log_decision(track, timestamp, {"decision": "SKIP_QUALITY"},
                                      {"state": "UNKNOWN", "text": None, "confidence": 0.}, details)
            x, y, w, h = np.rint(track.bbox).astype(int)
            color = (0, 103, 255) if track.state == "CONFIRMED" else (240, 240, 0)
            cv2.rectangle(annotated, (x, y), (x+w, y+h), color, 2)
            if track.state == "CONFIRMED" and track.foreground_mask is not None:
                contours, _ = cv2.findContours(track.foreground_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(annotated, contours, -1, (240, 240, 0), 1, offset=(x, y))
            gid_label = track.global_id[:10] if track.global_id else "UNASSIGNED"
            visual = track.evidence.get("visual_score")
            score_label = f" V:{visual:.2f}" if visual is not None else ""
            cv2.putText(annotated, f"{track.local_id} {gid_label}{score_label}", (max(0, x), max(16, y-6)),
                        cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1, cv2.LINE_AA)
            snapshot.append({"local_id": track.local_id, "global_id": track.global_id, "state": track.state,
                             "quality": track.quality, "evidence": track.evidence,
                             "preview_url": self.preview_url(self.node_id, track.preview_id)})
        if warm:
            cv2.putText(annotated, "BACKGROUND WARMUP", (20, 32), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 103, 255), 2)
        ok, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 78])
        with self.lock:
            if ok:
                self.jpeg = jpeg.tobytes()
            self.metrics.update(detections=self.tracker.candidate_count if not warm else 0, raw_regions=len(components), tracks=snapshot,
                candidate_count=max((t.evidence.get("candidate_count", 0) for t in tracks), default=0),
                frames=self.metrics["frames"] + 1, mask_fraction=float((mask > 0).mean()),
                shadow_fraction=float((shadows > 0).mean()), error=None)
        self.live_metrics.frame((time.perf_counter() - processing_start)*1000, warm,
            self.tracker.candidate_count if not warm else 0,
            sum(t.state == 'CONFIRMED' for t in tracks))

    def camera_loop(self):
        previous = time.monotonic()
        for frame, timestamp, warm, reconnect in self.camera.frames(self.stop):
            try:
                self.process_frame(frame, timestamp, warm, reconnect)
                now = time.monotonic()
                with self.lock:
                    self.metrics["fps"] = min(24., 1 / max(.001, now - previous))
                previous = now
            except Exception as error:
                log.exception("Frame processing failed")
                self.live_metrics.error()
                with self.lock:
                    self.metrics["error"] = str(error)

    def preview_url(self, node_id, event_id):
        if not event_id or node_id not in self.members:
            return None
        try:
            uuid.UUID(event_id)
        except (ValueError, TypeError):
            return None
        base = "" if node_id == self.node_id else self.members[node_id]["url"].rstrip("/")
        return f"{base}/api/thumbnails/{event_id}"

    def observation_cards(self):
        cards = self.thumbnails.recent(12)
        for card in cards:
            evidence = card["evidence"]
            reference = evidence.get("identity_preview") or {}
            card["preview_url"] = self.preview_url(self.node_id, card["preview_id"])
            card["matched_preview_url"] = self.preview_url(evidence.get("matched_node_id"), evidence.get("matched_event_id"))
            card["reference_preview_url"] = self.preview_url(reference.get("node_id"), reference.get("event_id"))
            card["committed"] = self.db.contains(card["preview_id"])
        return cards

    def identity_cards(self, limit=16):
        with self.gallery.lock:
            recent = sorted(self.gallery.identities.values(), key=lambda item: item["last_seen"], reverse=True)[:limit]
            return [{"global_id": item["global_id"], "last_camera": item["last_camera"], "last_seen": item["last_seen"],
                     "gallery_size": len(item["visual_gallery"]),
                     "cameras": sorted({record["camera"] for record in item["camera_history"]}),
                     "preview_url": self.preview_url((item.get("preview") or {}).get("node_id"), (item.get("preview") or {}).get("event_id")),
                     "exemplars": [{"event_id": entry["event_id"], "camera": entry.get("node_id"),
                                    "preview_url": self.preview_url(entry.get("node_id"), entry["event_id"])}
                                   for entry in item["visual_gallery"][-3:]]} for item in recent]

    def snapshot(self):
        tip = self.db.tip()
        with self.gallery.lock:
            identity_count = len(self.gallery.identities)
        with self.lock:
            return {"node_id": self.node_id, "camera": self.camera.state, **self.metrics,
                    "identity_count": identity_count, "events": list(self.events)[:12],
                    "peers": dict(self.network.statuses), "consensus": self.consensus.state,
                    "quorum": self.ledger.quorum, "members": len(self.members),
                    "next_proposer": self.ledger.proposer(tip["block_index"] + 1),
                    "height": tip["block_index"], "block_hash": tip["block_hash"],
                    "integrity": self.integrity, "pending": len(self.db.pending(100000)),
                    "descriptor": "MVSV-G / 602", "profile": self.descriptor.profile,
                    "retrieval": "BRUTE FORCE" if self.cfg["lsh"]["brute_force"] else "LSH + EXACT"}

    @asynccontextmanager
    async def lifespan(self, app):
        thread = None
        if self.cfg["camera"]["source"] is not None:
            thread = threading.Thread(target=self.camera_loop, name="camera", daemon=True)
            thread.start()
        network_task = asyncio.create_task(self.network.run())
        try:
            yield
        finally:
            self.stop.set()
            network_task.cancel()
            try:
                await network_task
            except asyncio.CancelledError:
                pass
            if thread:
                await asyncio.to_thread(thread.join, 7)
            if thread is None or not thread.is_alive():
                self.db.close()
            self.live_metrics.flush('complete' if thread is None or not thread.is_alive() else 'camera_shutdown_timeout')
