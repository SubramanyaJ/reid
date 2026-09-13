"""Offline integration demonstration: two rendered cameras and a camera-less peer."""
import csv
from copy import deepcopy
import json
from pathlib import Path
import time
import cv2
import numpy as np
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ..config import DEFAULTS
from ..provenance.crypto import public_text, save_private
from ..runtime import Runtime
from ..tracking.tracker import iou
from .evaluate import evaluate


def scene(index, camera):
    frame = np.full((360, 640, 3), (57, 59, 60), np.uint8)
    for x in range(0, 640, 90):
        cv2.rectangle(frame, (x, 172), (x+45, 176), (175, 175, 175), -1)
    truths = []
    schedule = [("A", 65, 195, 80, (35, 70, 210)), ("B", 125, 255, 230, (180, 100, 30))] if camera == "C1" else [
               ("A", 240, 370, 230, (40, 78, 200)), ("B", 290, 420, 80, (175, 105, 35))]
    for identity, begin, end, y, color in schedule:
        if not begin <= index < end:
            continue
        progress = (index - begin) / (end - begin)
        x = round(-110 + 850 * (progress if camera == "C1" else 1-progress))
        w, h = 110, 52
        cv2.rectangle(frame, (x, y+9), (x+w, y+h-8), color, -1)
        cv2.fillConvexPoly(frame, np.array([[x+20,y+9],[x+37,y],[x+78,y],[x+92,y+9]]), color)
        cv2.rectangle(frame, (x+38,y+3), (x+76,y+17), (155,145,135), -1)
        cv2.line(frame, (x+54,y+3), (x+54,y+40), (40,40,40), 2)
        for wheel in (x+22, x+87):
            cv2.circle(frame, (wheel,y+44), 9, (12,12,12), -1)
            cv2.circle(frame, (wheel,y+44), 4, (150,150,150), -1)
        cv2.rectangle(frame, (x+61,y+27), (x+104,y+40), (235,235,235), -1)
        cv2.putText(frame, "AB1234" if identity == "A" else "CD5678", (x+62,y+37),
                    cv2.FONT_HERSHEY_PLAIN, .6, (5,5,5), 1)
        for dx in range(8, 100, 13):
            cv2.line(frame, (x+dx,y+22), (x+dx+5,y+23), tuple(min(255,c+20) for c in color), 1)
        if 0 <= x and x+w <= 640:
            truths.append((identity, np.array([x, y, w, h], float)))
    return frame, truths


def replicate(nodes):
    # Same transaction/vote/block validators used by HTTP peers; transport here is in-process.
    for origin in nodes:
        for tx in origin.db.pending(128):
            packet = origin.db.packet(tx["event"]["event_id"])
            if packet:
                for peer in nodes:
                    peer.receive_packet(packet)
    height = nodes[0].db.tip()["block_index"] + 1
    proposer = next(node for node in nodes if node.node_id == nodes[0].ledger.proposer(height))
    block = proposer.consensus.propose()
    if block:
        votes = [node.consensus.vote(block) for node in nodes]
        committed = proposer.consensus.finalize(block, votes)
        for node in nodes:
            node.ledger.accept(committed)
            node.apply_block(committed)


def demo(out, frames=430):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any((out / node / "key.pem").exists() for node in ("C1", "C2", "C3")):
        raise ValueError("Demo output already contains keys. Choose a new --out directory.")
    keys = {node: Ed25519PrivateKey.generate() for node in ("C1", "C2", "C3")}
    members = [{"id": node, "url": f"http://127.0.0.1:{9100+i}", "public_key": public_text(key.public_key())}
               for i, (node, key) in enumerate(keys.items())]
    nodes, writers, records = [], [], []
    try:
        for node, key in keys.items():
            cfg = deepcopy(DEFAULTS)
            cfg["members"] = members
            cfg["node"].update(id=node, private_key=str(out/node/"key.pem"), database=str(out/node/"state.sqlite"))
            cfg["camera"].update(width=640, height=360, fps=15, source=None)
            save_private(cfg["node"]["private_key"], key)
            nodes.append(Runtime(cfg))
        for name in ("C1", "C2"):
            writer = cv2.VideoWriter(str(out / f"{name}.avi"), cv2.VideoWriter_fourcc(*"MJPG"), 15, (640, 360))
            if not writer.isOpened():
                raise RuntimeError("OpenCV MJPG writer unavailable")
            writers.append(writer)
        start = time.time()
        for index in range(frames):
            for node, writer in zip(nodes[:2], writers):
                frame, truths = scene(index, node.node_id)
                node.process_frame(frame, start + index / 15, warm=index < 60, reconnect=index == 0)
                annotated = cv2.imdecode(np.frombuffer(node.jpeg, np.uint8), cv2.IMREAD_COLOR)
                writer.write(annotated)
                if index % 15 == 0:
                    for truth, box in truths:
                        candidates = [t for t in node.tracker.tracks if t.local_id and t.missed == 0]
                        track = max(candidates, key=lambda t: iou(t.bbox, box), default=None)
                        if track is not None and iou(track.bbox, box) < .3:
                            track = None
                        records.append({"frame": index, "truth_id": truth, "camera_id": node.node_id,
                                        "local_id": track.local_id if track else "",
                                        "global_id": track.global_id if track and track.global_id else ""})
            if index % 15 == 0:
                replicate(nodes)
        replicate(nodes)
        summaries = [{"node": node.node_id, "height": node.db.tip()["block_index"],
                      "tip": node.db.tip()["block_hash"], "integrity": node.ledger.verify_all()[0],
                      "identities": len(node.gallery.identities)} for node in nodes]
        (out / "ledger_summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
        if records:
            with (out / "observations.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(records[0]))
                writer.writeheader()
                writer.writerows(records)
            evaluate(out / "observations.csv", out / "evaluation.json")
        print(f"Offline demo written to {out}. Inspect both AVI files and ledger_summary.json.")
    finally:
        for writer in writers:
            writer.release()
        for node in nodes:
            node.db.close()
