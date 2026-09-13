import math
import numpy as np


def context_scores(identity, camera_id, timestamp, position, scale, config):
    elapsed = max(0., timestamp - identity["last_seen"])
    previous = identity["last_camera"]
    if previous == camera_id:
        temporal = .55 + .4 * math.exp(-elapsed / 60)
        distance = float(np.linalg.norm(np.asarray(position) - identity["last_position"]))
        # Same camera only: normalized geometry with uncertainty widening over time.
        spatial = math.exp(-distance / (.2 + min(elapsed / 15, 2)))
        old_scale = max(identity.get("last_scale", scale), 1e-5)
        spatial = .8 * spatial + .2 * math.exp(-abs(math.log(max(scale, 1e-5) / old_scale)))
    else:
        transition = config.get("transitions", {}).get(f"{previous}->{camera_id}", {})
        low, high = transition.get("min_seconds", 0), transition.get("max_seconds", 300)
        if high <= low:
            high = low + 1
        outside = max(low - elapsed, elapsed - high, 0)
        temporal = .5 + .4 * math.exp(-outside / max(30, high - low))
        spatial = float(np.clip(transition.get("probability", .5), 0, 1))
    return float(temporal), float(spatial)
