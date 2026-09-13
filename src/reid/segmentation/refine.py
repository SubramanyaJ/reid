import cv2
import numpy as np


def crop_candidate(frame, foreground, bbox, refine=False):
    x, y, w, h = np.rint(bbox).astype(int)
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if x2 <= x1 or y2 <= y1:
        return None, None, 0.
    crop, mask = frame[y1:y2, x1:x2].copy(), foreground[y1:y2, x1:x2].copy()
    visibility = (x2 - x1) * (y2 - y1) / max(w * h, 1)
    if refine and crop.shape[0] >= 16 and crop.shape[1] >= 16 and np.count_nonzero(mask) > 20:
        seed = np.where(mask > 0, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)
        core = cv2.erode(mask, np.ones((3, 3), np.uint8)) > 0
        seed[core] = cv2.GC_FGD
        seed[[0, -1], :] = cv2.GC_BGD
        seed[:, [0, -1]] = cv2.GC_BGD
        try:
            cv2.grabCut(crop, seed, None, np.zeros((1, 65)), np.zeros((1, 65)), 2, cv2.GC_INIT_WITH_MASK)
            candidate = np.uint8((seed == cv2.GC_FGD) | (seed == cv2.GC_PR_FGD)) * 255
            ratio = np.count_nonzero(candidate) / max(np.count_nonzero(mask), 1)
            overlap = np.count_nonzero((candidate > 0) & (mask > 0)) / max(np.count_nonzero(mask), 1)
            if .55 <= ratio <= 1.6 and overlap >= .6:
                mask = candidate
        except cv2.error:
            pass
    return crop, mask, float(visibility)
