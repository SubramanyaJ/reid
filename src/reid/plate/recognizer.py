"""Classical geometric plate proposals and font-template OCR, without OCR engines."""
from pathlib import Path
import string
import cv2
import numpy as np


def normalize_glyph(binary):
    points = cv2.findNonZero(binary)
    if points is None:
        return np.zeros((40, 24), np.uint8)
    x, y, w, h = cv2.boundingRect(points)
    crop = binary[y:y+h, x:x+w]
    scale = min(20 / w, 36 / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    out = np.zeros((40, 24), np.uint8)
    out[(40-nh)//2:(40-nh)//2+nh, (24-nw)//2:(24-nw)//2+nw] = cv2.resize(crop, (nw, nh))
    return out


class PlateRecognizer:
    def __init__(self, template_directory=None):
        self.templates = []
        for character in string.ascii_uppercase + string.digits:
            for font in (cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_PLAIN):
                for thickness in (1, 2):
                    canvas = np.zeros((64, 64), np.uint8)
                    cv2.putText(canvas, character, (5, 48), font, 1.5, 255, thickness, cv2.LINE_AA)
                    self.templates.append((character, normalize_glyph(canvas)))
            if template_directory:
                path = Path(template_directory) / f"{character}.png"
                if path.exists():
                    glyph = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    if glyph is None:
                        raise ValueError(f"Unreadable template: {path}")
                    _, glyph = cv2.threshold(glyph, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                    if glyph.mean() > 127:
                        glyph = 255 - glyph
                    self.templates.append((character, normalize_glyph(glyph)))

    def proposals(self, image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        contrast = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5)))
        grad = np.abs(cv2.Sobel(contrast, cv2.CV_32F, 1, 0))
        grad = np.uint8(255 * grad / max(1., float(grad.max())))
        _, binary = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((5, 17), np.uint8))
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        proposals = []
        for contour in contours:
            rect = cv2.minAreaRect(contour)
            rw, rh = rect[1]
            if min(rw, rh) < 8:
                continue
            ratio = max(rw, rh) / min(rw, rh)
            fraction = rw * rh / gray.size
            if not 1.1 <= ratio <= 6.5 or not .003 <= fraction <= .35:
                continue
            expanded = (rect[0], (rw * 1.12, rh * 1.22), rect[2])
            pts = cv2.boxPoints(expanded)
            # Order cyclically, then orient the long side horizontally.
            sums, diffs = pts.sum(1), np.diff(pts, axis=1).ravel()
            ordered = np.float32([pts[np.argmin(sums)], pts[np.argmin(diffs)], pts[np.argmax(sums)], pts[np.argmax(diffs)]])
            if len(np.unique(ordered, axis=0)) != 4:
                continue
            width = np.linalg.norm(ordered[1] - ordered[0])
            height = np.linalg.norm(ordered[3] - ordered[0])
            if height > width:
                ordered = np.roll(ordered, -1, axis=0)
                width, height = height, width
            target_h = max(32, min(120, round(240 * height / max(width, 1))))
            matrix = cv2.getPerspectiveTransform(ordered, np.float32([[0, 0], [239, 0], [239, target_h-1], [0, target_h-1]]))
            proposals.append(cv2.warpPerspective(gray, matrix, (240, target_h)))
        return proposals[:8]

    def recognize(self, image):
        best = ("", 0.)
        for plate in self.proposals(image):
            enhanced = cv2.createCLAHE(clipLimit=2., tileGridSize=(4, 4)).apply(plate)
            for polarity in (cv2.THRESH_BINARY_INV, cv2.THRESH_BINARY):
                binary = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, polarity, 19, 7)
                # Component height and vertical alignment identify single/multiple text lines.
                counts, _, stats, _ = cv2.connectedComponentsWithStats(binary)
                glyphs = []
                for x, y, w, h, area in stats[1:counts]:
                    if h < max(9, .18 * plate.shape[0]) or h > .94 * plate.shape[0]:
                        continue
                    if not .07 <= w / h <= 1.05 or not .09 <= area / (w * h) <= .9:
                        continue
                    glyphs.append((int(x), int(y), int(w), int(h)))
                if not 4 <= len(glyphs) <= 16:
                    continue
                lines = []
                for box in sorted(glyphs, key=lambda b: b[1] + b[3] / 2):
                    cy = box[1] + box[3] / 2
                    line = next((line for line in lines if abs(np.mean([b[1]+b[3]/2 for b in line]) - cy) < .5 * box[3]), None)
                    if line is None:
                        lines.append([box])
                    else:
                        line.append(box)
                if len(lines) > 3:
                    continue
                text, scores = "", []
                for line in lines:
                    for x, y, w, h in sorted(line):
                        glyph = normalize_glyph(binary[y:y+h, x:x+w])
                        candidates = {}
                        for char, template in self.templates:
                            score = float(cv2.matchTemplate(glyph, template, cv2.TM_CCOEFF_NORMED)[0, 0])
                            candidates[char] = max(candidates.get(char, -1), score)
                        ordered = sorted(candidates.items(), key=lambda item: item[1], reverse=True)
                        char, score = ordered[0]
                        margin = max(0., score - ordered[1][1])
                        text += char
                        scores.append(max(0., score) * (.7 + .3 * min(1., margin / .15)))
                confidence = float(np.mean(scores)) if scores else 0.
                if confidence > best[1]:
                    best = text, confidence
        return best
