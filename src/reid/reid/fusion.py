def fuse(visual_score, plate_score, temporal_score, spatial_score, config,
         plate_confidence=0., local_score=None):
    # Candidate-specific keypoint evidence only refines the visual channel.
    visual = visual_score if local_score is None else .9 * visual_score + .1 * local_score
    evidence = {"visual_score": float(visual), "plate_score": plate_score,
                "temporal_score": temporal_score, "spatial_score": spatial_score,
                "local_score": local_score}
    channels = [(visual, .78), (temporal_score, .07), (spatial_score, .05)]
    if plate_score is not None:
        channels.append((plate_score, .35 * plate_confidence))
    available = [(value, weight) for value, weight in channels if value is not None]
    score = sum(value * weight for value, weight in available) / sum(weight for _, weight in available)
    contradiction = plate_score is not None and plate_confidence >= .65 and plate_score < .45
    if visual < config["visual_floor"] or contradiction:
        decision = "NO_MATCH"
    elif score >= config["match_threshold"]:
        decision = "MATCH"
    elif score >= config["uncertain_threshold"]:
        decision = "UNCERTAIN"
    else:
        decision = "NO_MATCH"
    return {**evidence, "decision": decision, "confidence": float(score), "plate_conflict": contradiction}
