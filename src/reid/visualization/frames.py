"""Frame overlays and JPEG encoding shared by live capture and video replay."""
import cv2
import numpy as np


def draw_track(frame, track):
    x, y, w, h = np.rint(track.bbox).astype(int)
    color = (0, 103, 255) if track.state == "CONFIRMED" else (240, 240, 0)
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    if track.state == "CONFIRMED" and track.foreground_mask is not None:
        contours, _ = cv2.findContours(track.foreground_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(frame, contours, -1, (240, 240, 0), 1, offset=(x, y))
    identity = track.global_id[:10] if track.global_id else "UNASSIGNED"
    visual = track.evidence.get("visual_score")
    score = f" V:{visual:.2f}" if visual is not None else ""
    cv2.putText(frame, f"{track.local_id} {identity}{score}", (max(0, x), max(16, y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1, cv2.LINE_AA)


def encode_frame(frame, warm=False):
    if warm:
        cv2.putText(frame, "BACKGROUND WARMUP", (20, 32), cv2.FONT_HERSHEY_SIMPLEX,
                    .7, (0, 103, 255), 2)
    ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return jpeg.tobytes() if ok else None
