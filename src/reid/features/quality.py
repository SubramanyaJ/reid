import cv2
import numpy as np


def observation_quality(crop, mask, visibility, consistency, occluded=False):
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    pixels = mask > 0
    if np.count_nonzero(pixels) < 30:
        return 0., {"reason": "empty segmentation"}
    sharpness = float(cv2.Laplacian(gray, cv2.CV_32F)[pixels].var())
    fill = float(pixels.mean())
    size = min(1., min(crop.shape[:2]) / 90.)
    sharp = min(1., sharpness / 180.)
    segmentation = float(np.clip(1 - abs(fill - .7) / .7, 0, 1))
    quality = .2 * size + .25 * sharp + .2 * segmentation + .15 * visibility + .2 * consistency
    if fill < .15 or visibility < .7 or min(crop.shape[:2]) < 20:
        quality *= .3
    if occluded:
        quality *= .45
    return float(quality), {"size": size, "sharpness": sharp, "segmentation": segmentation,
                            "visibility": visibility, "stability": consistency, "occluded": occluded}
