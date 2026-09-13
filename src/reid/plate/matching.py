import re

AMBIGUITIES = [set("0OQ"), set("1IL"), set("2Z"), set("5S"), set("8B"), set("6G")]


def normalize(text):
    return re.sub(r"[^A-Z0-9]", "", text.upper())[:16]


def similarity(left, right):
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return None
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            substitution = 0 if ca == cb else .25 if any(ca in group and cb in group for group in AMBIGUITIES) else 1
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j-1] + substitution))
        previous = current
    return max(0., 1 - previous[-1] / max(len(a), len(b)))


def aggregate(readings, min_confidence=.58, min_readings=2):
    useful = [(normalize(text), float(conf)) for text, conf in readings[-24:]
              if conf >= min_confidence and len(normalize(text)) >= 4]
    unknown = {"state": "UNKNOWN", "text": None, "confidence": 0.}
    if len(useful) < min_readings:
        return unknown
    canonical = max(sorted({t for t, _ in useful}), key=lambda t: sum(c * similarity(t, u) for u, c in useful))
    supporters = [(t, c) for t, c in useful if similarity(canonical, t) >= .8]
    support = sum(c for _, c in supporters) / sum(c for _, c in useful)
    if support < .65:
        return {"state": "CONFLICTING", "text": None, "confidence": support}
    if len(supporters) < min_readings:
        return unknown
    return {"state": "SUPPORTED", "text": canonical,
            "confidence": support * sum(c for _, c in supporters) / len(supporters)}
