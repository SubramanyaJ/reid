from copy import deepcopy
import uuid
import cv2
import numpy as np
from fastapi.testclient import TestClient
from reid.detection.foreground import Foreground, Component
from reid.tracking.tracker import Tracker
from reid.segmentation.refine import crop_candidate
from reid.storage.database import Database
from reid.visualization.thumbnails import Thumbnails
from reid.network.api import create_app
from reid.runtime import Runtime
from reid.provenance.crypto import save_private, canonical
from conftest import packet_for


def test_core_cleanup_preserves_whole_hand_and_separate_neighbors(config):
    mask = np.zeros((540, 960), np.uint8)
    cv2.rectangle(mask, (100, 210), (200, 300), 255, -1)  # palm
    for x in (110, 135, 160, 185):
        cv2.rectangle(mask, (x, 140), (x+10, 230), 255, -1)  # attached fingers
    cv2.rectangle(mask, (230, 210), (310, 300), 255, -1)  # separate substantial object
    cv2.rectangle(mask, (500, 140), (510, 220), 255, -1)  # isolated finger-sized fragment
    cleaned, components = Foreground(config['detection']).clean(mask)
    assert len(components) == 2
    assert cleaned[160, 115] == 255  # connected thin details are retained
    assert cleaned[170, 505] == 0
    assert cleaned[250, 215] == 0  # nearby objects were not joined
    assert all(c.mask.shape == (int(c.bbox[3]), int(c.bbox[2])) for c in components)


def test_geometry_rejects_finger_at_all_positions_and_scales(config):
    tracker = Tracker('C1', config['tracking'])
    for scale in (.5, 1., 2.):
        shape = (int(540*scale), int(960*scale), 3)
        for y in (10, 330):
            whole_hand = np.array([100, y, 90, 160], float) * scale
            finger = np.array([100, y, 24, 80], float) * scale
            horizontal_finger = np.array([100, y, 85, 24], float) * scale
            assert tracker.valid_geometry(whole_hand, 10000*scale**2, shape)
            assert not tracker.valid_geometry(finger, 1900*scale**2, shape)
            assert not tracker.valid_geometry(horizontal_finger, 2000*scale**2, shape)
            assert not tracker.valid_geometry(whole_hand, 200*scale**2, shape)  # empty oversized box


def test_small_fragment_does_not_shrink_established_hand(config):
    tracker = Tracker('C1', config['tracking'])
    hand = Component(np.array([100, 140, 90, 160], float), 11000, np.array([145, 220]))
    for i in range(8):
        tracker.update([hand], i*.05, (540, 960, 3))
    assert tracker.tracks[0].local_id
    fragment = Component(np.array([120, 150, 24, 80], float), 1900, np.array([132, 190]))
    tracker.update([fragment], .4, (540, 960, 3))
    assert tracker.tracks[0].missed == 1
    np.testing.assert_array_equal(tracker.tracks[0].bbox[2:], [90, 160])
    assert tracker.candidate_count == 0


def test_crop_uses_only_its_own_component_mask():
    frame = np.full((100, 100, 3), 200, np.uint8)
    foreground = np.full((100, 100), 255, np.uint8)
    own = np.zeros((60, 60), np.uint8)
    own[5:55, 5:20] = 255
    crop, mask, visibility = crop_candidate(frame, foreground, [10, 10, 60, 60], candidate_mask=own)
    assert mask[30, 40] == 0 and mask[30, 10] == 255
    assert visibility == 1 and crop.shape == (60, 60, 3)


def test_thumbnails_bounded_persistent_and_not_on_ledger(tmp_path):
    path = str(tmp_path/'preview.sqlite')
    db = Database(path)
    previews = Thumbnails(db, limit=2, size=96)
    crop = np.full((100, 200, 3), (30, 100, 210), np.uint8)
    mask = np.zeros((100, 200), np.uint8)
    mask[20:80, 30:150] = 255
    ids = [str(uuid.uuid4()) for _ in range(3)]
    for index, event_id in enumerate(ids):
        previews.put(event_id, crop, mask, {'timestamp': index+1})
    assert previews.get(ids[0]) is None
    assert len(previews.recent()) == 2
    image = cv2.imdecode(np.frombuffer(previews.get(ids[-1]), np.uint8), cv2.IMREAD_COLOR)
    assert max(image.shape[:2]) <= 96
    assert np.abs(image[0, 0].astype(int) - 32).max() <= 20  # JPEG chroma/ringing tolerance; original background was [30,100,210]
    assert not db.blocks() and not db.pending()
    db.close()
    db = Database(path)
    assert Thumbnails(db).get(ids[-1])
    assert Thumbnails(db).get('../../state.sqlite') is None
    db.close()


def test_object_api_local_and_remote_references_preserve_signed_packets(config, consortium, tmp_path):
    keys, members = consortium
    config['members'] = list(members.values())
    config['node'].update(id='C1', private_key=str(tmp_path/'C1.pem'), database=str(tmp_path/'state.sqlite'))
    save_private(config['node']['private_key'], keys['C1'])
    runtime = Runtime(config)
    packet = packet_for(keys['C2'], node='C2')
    before = canonical(packet)
    runtime.db.put_packet(packet)
    runtime.gallery.apply(packet)  # Isolated gallery fixture, not a live consensus injection.
    local_id = str(uuid.uuid4())
    runtime.thumbnails.put(local_id, np.zeros((80, 100, 3), np.uint8), np.full((80, 100), 255, np.uint8),
                           {'timestamp': 10., 'node_id': 'C1', 'evidence': {'decision': 'UNCERTAIN'}})
    # No lifespan context: this test does not start cameras or background networking.
    client = TestClient(create_app(runtime))
    objects = client.get('/api/objects').json()
    assert objects['observations'][0]['preview_url'] == f'/api/thumbnails/{local_id}'
    remote_id = packet['transaction']['event']['event_id']
    assert objects['identities'][0]['preview_url'] == f'http://127.0.0.1:9001/api/thumbnails/{remote_id}'
    assert client.get(f'/api/thumbnails/{local_id}').status_code == 200
    assert client.get('/api/thumbnails/not-an-event').status_code == 404
    assert b'RE-ID OBJECTS' in client.get('/').content
    assert canonical(packet) == before
    assert len(runtime.db.blocks()) == 1  # only genesis; previews cannot create ledger transactions
    client.close()
    runtime.db.close()
