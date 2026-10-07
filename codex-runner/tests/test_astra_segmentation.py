import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from remote_yam.astra_segmentation import AstraContourSegmenter, rasterize_response, immutable_scene_contour_request


def rectangle(x0, y0, x1, y1):
    return [{'x': x0, 'y': y0}, {'x': x1, 'y': y0}, {'x': x1, 'y': y1}, {'x': x0, 'y': y1}]


def proposal():
    return dict(image_width=100, image_height=80, coordinate_system='pixel_xy_top_left',
        block=dict(status='found', confidence=.9, explanation='fixture only',
            polygons=[dict(outer=rectangle(10, 10, 25, 25), holes=[])]),
        towel=dict(status='found', confidence=.9, explanation='fixture only',
            polygons=[dict(outer=rectangle(35, 5, 80, 70), holes=[rectangle(50, 30, 60, 40)])]))


class AstraContourTests(unittest.TestCase):
    def test_immutable_capture_replay_survives_new_coding_root_and_matches_each_query(self):
        image = np.zeros((80, 100, 3), np.uint8)
        queries = dict(block='green cuboid', towel='green towel')
        frames = dict(top=dict(metadata=dict(captured_at=123, depth_sha256='exact-depth')))
        with tempfile.TemporaryDirectory() as output:
            root = Path(output)
            first = root/'old/plan'
            (first/'scene').mkdir(parents=True)
            (first/'scene/snapshot.json').write_text(json.dumps(dict(frames=frames)))
            original = AstraContourSegmenter(queries, first/'observations/astra',
                request=Mock(return_value=(proposal(), dict(model='gpt-6-astra'))))
            expected = original.segment(image, queries['block'])['mask']
            never = Mock(side_effect=AssertionError('Exact capture/query must not re-contour'))
            replay = immutable_scene_contour_request([root/'old'], frames, queries, fallback=never)
            second = AstraContourSegmenter(queries, root/'new/observations/astra', request=replay)
            np.testing.assert_array_equal(second.segment(image, queries['block'])['mask'], expected)
            never.assert_not_called()
            info = json.loads(next((root/'new').glob('**/request.json')).read_text())
            self.assertEqual(info['transport'], 'validated_exact_capture_query_replay')
            self.assertIn('block', info['cached_query_provenance'])

            changed = dict(queries, towel='new towel query')
            def fresh(png, schema, prompt, timeout):
                self.assertNotIn('block', schema['properties'])
                response = proposal()
                del response['block']
                return response, dict(model='gpt-6-astra')
            fallback = Mock(side_effect=fresh)
            mixed = AstraContourSegmenter(changed, root/'mixed/observations/astra',
                request=immutable_scene_contour_request([root/'old'], frames, changed, fallback=fallback))
            np.testing.assert_array_equal(mixed.segment(image, changed['block'])['mask'], expected)
            fallback.assert_called_once()
            self.assertTrue(next((root/'mixed').glob('**/model-response.json')).is_file())

    def test_changed_pixels_or_capture_metadata_do_not_reuse_recorded_proposals(self):
        image = np.zeros((80, 100, 3), np.uint8)
        queries = dict(block='green cuboid', towel='green towel')
        frames = dict(top=dict(metadata=dict(captured_at=123)))
        with tempfile.TemporaryDirectory() as output:
            root = Path(output);first = root/'old/plan'
            (first/'scene').mkdir(parents=True)
            (first/'scene/snapshot.json').write_text(json.dumps(dict(frames=frames)))
            original = AstraContourSegmenter(queries, first/'observations/astra',
                request=Mock(return_value=(proposal(), dict(model='gpt-6-astra'))))
            original.segment(image, queries['block'])
            for label, rgb, metadata in [('pixels', image+1, frames),
                    ('capture', image, dict(top=dict(metadata=dict(captured_at=124))))]:
                fallback = Mock(return_value=(proposal(), dict(model='gpt-6-astra')))
                replay = immutable_scene_contour_request([root/'old'], metadata, queries, fallback=fallback)
                AstraContourSegmenter(queries, root/label/'astra', request=replay).segment(rgb, queries['block'])
                fallback.assert_called_once()

    def test_visible_towel_hole_and_original_pixel_grid_are_preserved(self):
        masks = rasterize_response(proposal(), 100, 80, ('block', 'towel'))
        self.assertTrue(masks['block']['mask'][15, 15])
        self.assertFalse(masks['block']['mask'][15, 30])
        self.assertTrue(masks['towel']['mask'][20, 45])
        self.assertFalse(masks['towel']['mask'][35, 55])
        self.assertFalse(np.any(masks['block']['mask'] & masks['towel']['mask']))
        self.assertEqual(masks['block']['mask'].shape, (80, 100))

    def test_wrong_dimensions_coordinates_and_malformed_scores_are_rejected(self):
        cases = []
        for key, value in [('image_width', 640), ('coordinate_system', 'normalized'), ('image_height', True)]:
            response = proposal()
            response[key] = value
            cases.append(response)
        for key, value in [('status', 'unknown'), ('confidence', True), ('confidence', float('nan'))]:
            response = proposal()
            response['block'][key] = value
            cases.append(response)
        for response in cases:
            with self.subTest(response=response), self.assertRaises(ValueError):
                rasterize_response(response, 100, 80, ('block', 'towel'))

    def test_out_of_bounds_self_intersection_and_invalid_holes_are_rejected(self):
        for ring in [rectangle(-1, 1, 15, 15), rectangle(1, 1, 101, 20),
                     [{'x':10,'y':10},{'x':25,'y':25},{'x':10,'y':25},{'x':25,'y':10}],
                     rectangle(10, 10, 25, 25)+[{'x':10,'y':10}]]:
            response = proposal()
            response['block']['polygons'][0]['outer'] = ring
            with self.subTest(ring=ring), self.assertRaises(ValueError):
                rasterize_response(response, 100, 80, ('block', 'towel'))
        for hole in [rectangle(1, 1, 15, 15), rectangle(30, 30, 60, 40), rectangle(35, 20, 50, 30)]:
            response = proposal()
            response['towel']['polygons'][0]['holes'] = [hole]
            with self.subTest(hole=hole), self.assertRaises(ValueError):
                rasterize_response(response, 100, 80, ('block', 'towel'))

    def test_cross_target_overlap_is_evidence_while_invalid_polygon_topology_is_rejected(self):
        response = proposal()
        response['towel'] = copy.deepcopy(response['block'])
        masks = rasterize_response(response, 100, 80, ('block', 'towel'))
        np.testing.assert_array_equal(masks['block']['mask'], masks['towel']['mask'])
        response = proposal()
        response['block']['polygons'].append(copy.deepcopy(response['block']['polygons'][0]))
        with self.assertRaisesRegex(ValueError, 'components overlap'):
            rasterize_response(response, 100, 80, ('block', 'towel'))
        response = proposal()
        response['towel']['polygons'][0]['holes'].append(rectangle(55, 35, 65, 45))
        with self.assertRaisesRegex(ValueError, 'Holes overlap'):
            rasterize_response(response, 100, 80, ('block', 'towel'))

    def test_registered_queries_use_one_exact_image_request_and_do_not_mutate_the_cache(self):
        image = np.zeros((80, 100, 3), np.uint8)
        request = Mock(return_value=(proposal(), dict(model='gpt-6-astra', transport='fixture')))
        with tempfile.TemporaryDirectory() as output:
            segmenter = AstraContourSegmenter(dict(block='green cuboid', towel='green towel'), output, request=request)
            first = segmenter.segment(image, 'green cuboid')
            first['mask'][:] = False
            second = segmenter.segment(image, 'green towel')
            self.assertTrue(segmenter.segment(image, 'green cuboid')['mask'].any())
            request.assert_called_once()
            np.testing.assert_array_equal(np.asarray(Image.open(io.BytesIO(request.call_args.args[0]))), image)
            self.assertTrue(second['proposals_not_ground_truth'])
            self.assertEqual(second['model'], 'gpt-6-astra')
            self.assertEqual(len(list(Path(output).glob('*/overlay.png'))), 1)
            image[0, 0, 0] = 1
            segmenter.segment(image, 'green towel')
            self.assertEqual(request.call_count, 2)

    def test_invalid_response_is_saved_and_never_silently_retried_or_falls_back(self):
        response = proposal()
        response['block']['confidence'] = 'malformed'
        request = Mock(return_value=(response, dict(model='gpt-6-astra')))
        with tempfile.TemporaryDirectory() as output:
            segmenter = AstraContourSegmenter(dict(block='green cuboid', towel='green towel'), output, request=request)
            with self.assertRaises(ValueError):
                segmenter.segment(np.zeros((80, 100, 3), np.uint8), 'green cuboid')
            request.assert_called_once()
            self.assertIsNone(segmenter._cache)
            self.assertEqual(len(list(Path(output).glob('*/response.json'))), 1)
            receipt = json.loads(next(Path(output).glob('*/request.json')).read_text())
            self.assertEqual(receipt['status'], 'REJECTED')
            self.assertEqual(receipt['robot_commands_sent'], 0)

    def test_low_confidence_ambiguous_small_full_and_absent_masks_are_not_motion_gates(self):
        response = proposal()
        response['block'].update(status='ambiguous', confidence=.1,
            polygons=[dict(outer=rectangle(10, 10, 11, 11), holes=[])])
        response['towel']['polygons'] = [dict(outer=rectangle(0, 0, 99, 79), holes=[])]
        masks = rasterize_response(response, 100, 80, ('block', 'towel'))
        self.assertEqual(masks['block']['status'], 'ambiguous')
        self.assertEqual(masks['block']['score'], .1)
        self.assertLess(masks['block']['mask'].sum(), 80)
        self.assertEqual(masks['towel']['mask'].sum(), 8000)
        response['block'].update(status='not_found', polygons=[])
        masks = rasterize_response(response, 100, 80, ('block', 'towel'))
        self.assertFalse(masks['block']['mask'].any())
        self.assertEqual(masks['block']['bbox_xywh'], [0, 0, 0, 0])

    def test_unregistered_query_can_be_added_to_the_model_request(self):
        def request(image, schema, prompt, timeout):
            response = proposal()
            key = next(k for k in schema['properties'] if k.startswith('query_'))
            response[key] = copy.deepcopy(response['block'])
            return response, {'model': 'gpt-6-astra'}
        with tempfile.TemporaryDirectory() as output:
            segmenter = AstraContourSegmenter(dict(block='green cuboid', towel='green towel'), output, request=request)
            mask = segmenter.segment(np.zeros((80, 100, 3), np.uint8), 'different target')
            self.assertTrue(mask['mask'].any())


if __name__ == '__main__':
    unittest.main()
