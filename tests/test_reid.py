import numpy as np
from reid.lsh.index import HyperplaneLSH
from reid.plate.matching import aggregate, similarity
from reid.reid.fusion import fuse
from reid.reid.context import context_scores
from reid.reid.gallery import Gallery
from reid.storage.database import Database
from conftest import packet_for


def test_lsh_seed_dedup_replacement_and_removal():
    vector = np.random.default_rng(7).normal(size=64).astype(np.float32)
    vector /= np.linalg.norm(vector)
    a, b = HyperplaneLSH(64), HyperplaneLSH(64)
    assert a.hashes(vector) == b.hashes(vector)
    a.insert("one", vector)
    a.insert("one", vector)
    assert a.query(vector) == {"one"}
    a.remove("one")
    assert a.query(vector) == set()


def test_plate_missing_ambiguity_and_conflict():
    assert similarity("", "AB1234") is None
    assert similarity("ab-1234", "AB1234") == 1
    assert similarity("AB0I23", "ABO123") > .9
    assert aggregate([("AB1234", .9)])["state"] == "UNKNOWN"
    assert aggregate([("AB1234", .9), ("AB1234", .8)])["text"] == "AB1234"
    assert aggregate([("AAAAAA", .95), ("ZZZZZZ", .95)])["state"] == "CONFLICTING"


def test_fusion_missing_plate_and_contradictions(config):
    cfg = config["reid"]
    assert fuse(.96, None, .6, .5, cfg)["decision"] == "MATCH"
    assert fuse(.96, .1, 1., 1., cfg, plate_confidence=.95)["decision"] == "NO_MATCH"
    assert fuse(.2, 1., 1., 1., cfg, plate_confidence=1.)["decision"] == "NO_MATCH"
    assert fuse(.75, None, .5, .5, cfg)["decision"] == "UNCERTAIN"


def test_cross_camera_context_never_compares_coordinates(config):
    identity = {"last_seen": 10, "last_camera": "C1", "last_position": [0., 0.], "last_scale": .1}
    first = context_scores(identity, "C2", 20, [0., 0.], .01, config["context"])
    second = context_scores(identity, "C2", 20, [1., 1.], .9, config["context"])
    assert first == second


def test_gallery_bounded_persistent_and_exclusion(config, consortium, tmp_path):
    keys, _ = consortium
    config["reid"]["gallery_size"] = 2
    config["lsh"]["brute_force"] = True
    db = Database(str(tmp_path / "gallery.sqlite"))
    gallery = Gallery(db, config)
    first = packet_for(keys["C1"])
    gid = first["transaction"]["event"]["global_identity_id"]
    gallery.apply(first)
    for seed in range(5):
        gallery.apply(packet_for(keys["C1"], gid=gid, vector=np.random.default_rng(seed).random(602)))
    assert len(gallery.identities[gid]["visual_gallery"]) <= 2
    selected = gallery.identities[gid]["visual_gallery"][0]
    payload = {**first["payload"], "vector": selected["vector"]}
    match, evidence = gallery.decide(payload, "C2", first["transaction"]["event"]["timestamp"]+10)
    assert match == gid and evidence["decision"] == "MATCH"
    other, _ = gallery.decide(payload, "C2", 100, excluded={gid})
    assert other != gid
    restored = Gallery(db, config)
    assert gid in restored.identities
    db.close()
