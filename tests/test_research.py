from argparse import Namespace
import json
import cv2
import numpy as np
import pytest
from reid.metrics.datasets import gaussian, normalize, validate, load_npz, load_crops, render_crop
from reid.metrics.measures import ranking_metrics, match_boxes
from reid.metrics.retrieval import eligibility, evaluate_retrieval
from reid.metrics.research import research
from reid.tracking.tracker import Tracker
from reid.lsh.index import HyperplaneLSH
from reid.metrics.vision import manifest_frames


def test_ap_penalizes_missing_candidates_and_distinguishes_unknowns():
    result = ranking_metrics([0, 2], {0, 1})
    assert result['ap'] == .5  # one positive missing, denominator remains two
    assert result['relevant_recall'] == .5
    assert ranking_metrics([], {0})['ap'] == 0
    assert ranking_metrics([0], set())['ap'] is None
    assert ranking_metrics([2, 0, 1], {0, 1})['ap'] == pytest.approx((.5+2/3)/2)


def test_box_matching_is_one_to_one_and_thresholded():
    truth = [[0, 0, 20, 20], [40, 0, 20, 20]]
    predictions = [[0, 0, 20, 20], [0, 0, 20, 20], [40, 0, 8, 8]]
    matched = match_boxes(truth, predictions)
    assert len(matched) == 1 and matched[0][0] == 0 and matched[0][2] == 1
    assert match_boxes([], predictions) == []


def test_exclusion_and_validation_prevent_trivial_self_matches():
    data = validate({'vectors': np.eye(3), 'queries': [[1, 0, 0]],
                     'sample_ids': ['a', 'b', 'c'], 'query_sample_ids': ['a'],
                     'camera_ids': ['C1', 'C1', 'C2'], 'query_camera_ids': ['C1']})
    np.testing.assert_array_equal(eligibility(data, 0), [False, True, True])
    np.testing.assert_array_equal(eligibility(data, 0, True), [False, False, True])
    with pytest.raises(ValueError, match='Zero'):
        normalize([[0, 0]])
    with pytest.raises(ValueError, match='both'):
        validate({'vectors': np.eye(3), 'queries': [[1, 0, 0]], 'labels': [1, 2, 3]})


def test_bad_rows_beyond_query_limit_are_still_rejected(tmp_path):
    path = tmp_path/'bad.npz'
    np.savez(path, vectors=np.eye(2), queries=[[1, 0], [np.nan, 1]])
    with pytest.raises(ValueError, match='finite'):
        load_npz(path, 1)


def test_finite_vectors_with_overflowing_norm_are_rejected():
    with pytest.raises(ValueError, match='overflow'):
        normalize([[1e30, 1e30]])


def test_lsh_removal_update_and_empty_bucket_cleanup():
    index = HyperplaneLSH(3, tables=2, bits=3, multiprobe=False)
    vector = np.array([1, 0, 0], np.float32)
    index.insert(7, vector)
    assert 7 in index.query(vector)
    index.insert(7, -vector)
    assert 7 not in index.query(vector) and 7 in index.query(-vector)
    index.remove(7)
    assert not index.keys and all(not table for table in index.buckets)


def test_tightened_maximum_width_height_and_area(config):
    tracker = Tracker('C1', config['tracking'])
    shape = (1000, 1000, 3)
    assert tracker.valid_geometry([100, 100, 150, 150], 18000, shape)
    assert not tracker.valid_geometry([0, 0, 810, 300], 200000, shape)
    # Relax aspect only to isolate the maximum-height condition.
    tracker.cfg['min_aspect_ratio'] = .1
    assert not tracker.valid_geometry([0, 0, 300, 860], 200000, shape)
    assert not tracker.valid_geometry([0, 0, 700, 700], 400000, shape)


def test_retrieval_baseline_known_rankings_and_unknowns():
    data = validate({'name': 'oracle', 'vectors': np.eye(3), 'queries': np.eye(3),
                     'labels': ['A', 'B', 'C'], 'query_labels': ['A', 'B', 'unknown']})
    summaries, rows = evaluate_retrieval(data, seeds=[17], repeats=1, configs=[(2, 3, True)])
    exact = summaries[0]
    assert exact['mAP'] == 1 and exact['rank1'] == 1 and exact['unknown_queries'] == 1
    assert exact['known_queries'] == 2 and exact['unknown_false_accept_rate'] == 1
    assert exact['exact_top1_recall'] == 1
    assert len(rows) == 6
    json.dumps(summaries, allow_nan=False)


def test_unlabeled_data_has_no_claimed_identity_accuracy():
    data = validate({'name': 'no_labels', 'vectors': np.eye(2), 'queries': np.eye(2)})
    summaries, _ = evaluate_retrieval(data, seeds=[1], repeats=1, configs=[])
    assert summaries[0]['mAP'] is None and summaries[0]['unknown_false_accept_rate'] is None


def test_crop_adapter_extracts_real_files_and_rejects_duplicate_images(tmp_path):
    for view in (0, 3):
        crop, mask = render_crop(0, view, 2026)
        cv2.imwrite(str(tmp_path/f'{view}.png'), crop)
        cv2.imwrite(str(tmp_path/f'{view}-mask.png'), mask)
    document = {'gallery': [{'image': '0.png', 'mask': '0-mask.png', 'identity': 'A', 'camera': 'C1', 'sample_id': 'one'}],
                'queries': [{'image': '3.png', 'mask': '3-mask.png', 'identity': 'A', 'camera': 'C2', 'sample_id': 'two'}]}
    path = tmp_path/'crops.json'
    path.write_text(json.dumps(document))
    data = load_crops(path, 10)
    assert data['vectors'].shape == (1, 602) and data['queries'].shape == (1, 602)
    assert data['origin']['inputs'][0]['mask_sha256']
    document['queries'][0]['image'] = '0.png'
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='Identical image bytes'):
        load_crops(path, 10)


def test_frame_adapter_requires_chronology_warmup_and_valid_truth(tmp_path):
    cv2.imwrite(str(tmp_path/'frame.png'), np.zeros((100, 200, 3), np.uint8))
    rows = [{'camera': 'C1', 'timestamp': t, 'image': 'frame.png', 'warmup': t < 4, 'objects': []} for t in (0, 3.95, 4)]
    path = tmp_path/'frames.json'
    path.write_text(json.dumps({'frames': rows}))
    assert len(list(manifest_frames(path))) == 3
    rows[-1]['timestamp'] = 3.5
    path.write_text(json.dumps({'frames': rows}))
    with pytest.raises(ValueError, match='strictly increasing'):
        list(manifest_frames(path))
    path.write_text(json.dumps({'frames': [{'camera': 'C1', 'timestamp': 4, 'image': 'frame.png', 'objects': []}]}))
    with pytest.raises(ValueError, match='warmup'):
        list(manifest_frames(path))


def test_real_command_contract_writes_json_csv_and_preserves_runs(tmp_path):
    path = tmp_path/'input.npz'
    data = gaussian(10, 3, 2026)
    np.savez(path, vectors=data['vectors'], queries=data['queries'], labels=data['labels'], query_labels=data['query_labels'])
    args = Namespace(out=str(tmp_path/'result'), dataset=str(path), crops=None, frames_manifest=None,
                     sizes=[10], queries=3, identities=2, seeds=[17], data_seed=2026,
                     repeats=1, cross_camera=False, skip_vision=True)
    result = research(args)
    on_disk = json.loads((tmp_path/'result'/'metrics.json').read_text())
    assert on_disk['schema_version'] == 'reid-research/1.0'
    assert len(result['retrieval']) == 5 and (tmp_path/'result'/'queries.csv').is_file()
    assert on_disk['datasets'][0]['sha256']
    with pytest.raises(ValueError, match='already exists'):
        research(args)
    # A legitimate exclusion policy can leave no eligible gallery rows.
    np.savez(path, vectors=np.eye(2), queries=np.eye(2), camera_ids=['C1', 'C1'], query_camera_ids=['C1', 'C1'])
    args.out = str(tmp_path/'excluded')
    args.cross_camera = True
    excluded = research(args)
    assert excluded['retrieval'][0]['candidate_fraction'] is None
    assert excluded['retrieval'][0]['exact_top1_recall'] is None
