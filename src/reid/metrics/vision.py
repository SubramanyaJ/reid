"""Replay labeled frames through the actual foreground/tracking/cropping code."""
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter_ns
import cv2
import numpy as np
from ..config import DEFAULTS
from ..detection.foreground import Foreground
from ..tracking.tracker import Tracker
from ..segmentation.refine import crop_candidate
from ..features.quality import observation_quality
from ..features.descriptor import MVSVG
from .datasets import file_digest, render_crop
from .measures import detection_summary, distribution, match_boxes, ratio


def synthetic_frames(seed=2026, frames=100):
    background = np.full((360, 640, 3), (70, 75, 70), np.uint8)
    for number in range(80 + frames):
        image, mask, boxes = background.copy(), np.zeros((360, 640), np.uint8), []
        warmup = number < 80
        if not warmup:
            t = number - 80
            for identity, x, y in [(0, 20 + 2*t, 80), (1, 460 - 2*t, 220)]:
                crop, own = render_crop(identity, 1, seed)
                image[y:y+80, x:x+128][own > 0] = crop[own > 0]
                mask[y:y+80, x:x+128][own > 0] = 255
                bx, by, bw, bh = cv2.boundingRect(own)
                boxes.append({"identity": str(identity), "bbox": [x+bx, y+by, bw, bh]})
            # Unlabelled thin distractor is deliberately a negative (not a target object).
            dx = 15 + 3*t
            cv2.rectangle(image, (dx, 12), (dx+5, 60), (200, 130, 40), -1)
        yield {"camera": "synthetic-C1", "timestamp": number/20, "warmup": warmup,
               "image": image, "mask": mask, "objects": boxes}


def manifest_frames(path):
    path = Path(path).resolve()
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not document.get("frames"):
        raise ValueError("Frame manifest needs a nonempty frames list")
    previous, shapes, warmup_times, active = {}, {}, {}, set()
    for row in document["frames"]:
        camera, timestamp = str(row["camera"]), float(row["timestamp"])
        if not np.isfinite(timestamp) or timestamp <= previous.get(camera, -np.inf):
            raise ValueError("Frame timestamps must be finite and strictly increasing per camera")
        previous[camera] = timestamp
        image_path = (path.parent / row["image"]).resolve()
        image = cv2.imread(str(image_path))
        if image is None:
            raise ValueError(f"Cannot read frame: {image_path}")
        if camera in shapes and shapes[camera] != image.shape:
            raise ValueError("Frame resolution must be constant within each camera sequence")
        shapes[camera] = image.shape
        warmup = row.get("warmup", False)
        if type(warmup) is not bool:
            raise ValueError("warmup must be a JSON boolean")
        if warmup:
            if camera in active:
                raise ValueError("Warmup must precede evaluated frames; use a separate manifest for a new sequence")
            warmup_times.setdefault(camera, []).append(timestamp)
        else:
            times = warmup_times.get(camera, [])
            if not times or max(times)-min(times) < 3:
                raise ValueError("Each camera requires at least 3 seconds of explicit background warmup frames")
            active.add(camera)
        objects = row["objects"]
        if not isinstance(objects, list):
            raise ValueError("Each frame needs an objects list, empty for a labeled negative frame")
        identities = set()
        for obj in objects:
            box = np.asarray(obj["bbox"], dtype=float)
            identity = str(obj["identity"])
            if box.shape != (4,) or not np.isfinite(box).all() or np.any(box[:2] < 0) or np.any(box[2:] <= 0):
                raise ValueError("Ground-truth boxes need finite, positive xywh in image coordinates")
            if box[0]+box[2] > image.shape[1] or box[1]+box[3] > image.shape[0] or identity in identities:
                raise ValueError("Truth boxes must be within the frame and identities unique per frame")
            identities.add(identity)
        mask = None
        mask_path = (path.parent / row["mask"]).resolve() if row.get("mask") else None
        if mask_path:
            mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
            if mask is None or mask.shape != image.shape[:2]:
                raise ValueError("Unreadable or misaligned full-frame ground-truth mask")
        yield {"camera": camera, "timestamp": timestamp, "warmup": warmup, "image": image,
               "objects": objects, "mask": mask,
               "input": {"image": str(image_path), "sha256": file_digest(image_path),
                         "mask_sha256": file_digest(mask_path) if mask_path else None}}


def evaluate_vision(path=None, seed=2026, previous_gates=False):
    cfg = deepcopy(DEFAULTS)
    if previous_gates:
        cfg["tracking"].update(min_vehicle_fraction=.006, max_vehicle_fraction=.65,
                               min_width_fraction=.055, min_height_fraction=.09,
                               max_width_fraction=1., max_height_fraction=1.,
                               min_foreground_fraction=.003, min_fill_ratio=.25)
    source = manifest_frames(path) if path else synthetic_frames(seed)
    cameras, extractor = {}, MVSVG()
    counts = {"components": [0, 0, 0, []], "confirmed_tracks": [0, 0, 0, []]}
    durations = {stage: [] for stage in ["foreground", "tracking", "crop_quality", "descriptor", "total"]}
    frames, warmups, mask_frames, admitted, attempted, truth_count = 0, 0, 0, 0, 0, 0
    intersection = union = predicted_pixels = truth_pixels = 0
    last_assignment, segments, last_visible, frame_numbers = {}, {}, {}, {}
    id_switches, fragments = 0, 0
    fingerprints, resolutions, warmup_spans = [], {}, {}
    for record in source:
        camera, frame = record["camera"], record["image"]
        resolutions[camera] = [int(frame.shape[1]), int(frame.shape[0])]
        if camera not in cameras:
            cameras[camera] = Foreground(cfg["detection"]), Tracker(camera, cfg["tracking"])
        foreground, tracker = cameras[camera]
        if record.get("input"):
            fingerprints.append(record["input"])
        if record["warmup"]:
            foreground.apply(frame, warmup=True)
            warmups += 1
            warmup_spans.setdefault(camera, []).append(record["timestamp"])
            continue
        started = perf_counter_ns()
        mask, _, components = foreground.apply(frame)
        after_fg = perf_counter_ns()
        tracks = tracker.update(components, record["timestamp"], frame.shape)
        tracks = [track for track in tracks if track.missed == 0 and track.state == "CONFIRMED"]
        after_tracking = perf_counter_ns()
        crop_ms = descriptor_ms = 0.
        # Replay profiles every current confirmed crop; the live observation interval is bypassed.
        for track in tracks:
            attempted += 1
            step = perf_counter_ns()
            crop, own, visibility = crop_candidate(frame, mask, track.bbox, candidate_mask=track.foreground_mask)
            quality, _ = observation_quality(crop, own, visibility, track.consistency)
            crop_ms += (perf_counter_ns()-step)/1e6
            if quality >= cfg["reid"]["min_quality"]:
                step = perf_counter_ns()
                extractor.extract(crop, own)
                descriptor_ms += (perf_counter_ns()-step)/1e6
                admitted += 1
        finished = perf_counter_ns()
        durations["foreground"].append((after_fg-started)/1e6)
        durations["tracking"].append((after_tracking-after_fg)/1e6)
        durations["crop_quality"].append(crop_ms)
        durations["descriptor"].append(descriptor_ms)
        durations["total"].append((finished-started)/1e6)
        frames += 1
        frame_numbers[camera] = frame_numbers.get(camera, 0) + 1
        truth = [obj["bbox"] for obj in record["objects"]]
        truth_count += len(truth)
        valid = [c for c in components if tracker.valid_geometry(c.bbox, c.area, frame.shape)]
        for stage, boxes in [("components", [c.bbox for c in valid]), ("confirmed_tracks", [t.bbox for t in tracks])]:
            matches = match_boxes(truth, boxes)
            counts[stage][0] += len(matches)
            counts[stage][1] += len(boxes)-len(matches)
            counts[stage][2] += len(truth)-len(matches)
            counts[stage][3].extend(score for _, _, score in matches)
            if stage == "confirmed_tracks":
                for truth_index, track_index, _ in matches:
                    identity = (camera, str(record["objects"][truth_index]["identity"]))
                    assigned = tracks[track_index].local_id
                    if identity in last_assignment and last_assignment[identity] != assigned:
                        id_switches += 1
                    if identity in last_visible and last_visible[identity] < frame_numbers[camera]-1:
                        fragments += 1
                    segments.setdefault(identity, set()).add(assigned)
                    last_assignment[identity], last_visible[identity] = assigned, frame_numbers[camera]
        if record["mask"] is not None:
            gt, prediction = record["mask"] > 0, mask > 0
            intersection += int(np.count_nonzero(gt & prediction))
            union += int(np.count_nonzero(gt | prediction))
            truth_pixels += int(gt.sum())
            predicted_pixels += int(prediction.sum())
            mask_frames += 1
    if not frames:
        raise ValueError("No evaluation frames remain after warmup")
    return {"dataset": "supplied_frames" if path else "synthetic_motion",
            "kind": "user_supplied_frames" if path else "synthetic_images",
            "variant": "previous_geometry" if previous_gates else "tightened_geometry",
            "config": {key: cfg[key] for key in ("detection", "tracking")},
            "evaluated_frames": frames, "warmup_frames": warmups, "truth_boxes": truth_count,
            "resolutions": resolutions, "warmup_timestamp_span_seconds": {key: max(times)-min(times) for key, times in warmup_spans.items()},
            "iou_threshold": .5, **{name: detection_summary(*values) for name, values in counts.items()},
            "mask_annotated_frames": mask_frames, "foreground_mask_iou_micro": ratio(intersection, union) if mask_frames else None,
            "foreground_mask_dice_micro": ratio(2*intersection, truth_pixels+predicted_pixels) if mask_frames else None,
            "identity_switches": id_switches, "matched_segment_interruptions": fragments,
            "extra_track_ids_per_truth": sum(max(0, len(ids)-1) for ids in segments.values()),
            "quality_attempts": attempted, "quality_admitted": admitted, "quality_admission_rate": ratio(admitted, attempted),
            "stage_ms_per_frame": {stage: distribution(values) for stage, values in durations.items()},
            "processing_fps": 1000/np.mean(durations["total"]),
            "timing_scope": "foreground through descriptor; excludes disk decode, annotation matching, retrieval and frame pacing",
            "manifest_sha256": file_digest(path) if path else None, "inputs": fingerprints}
