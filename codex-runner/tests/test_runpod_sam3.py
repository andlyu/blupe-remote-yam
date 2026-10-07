import base64
from contextlib import nullcontext
import copy
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from remote_yam.runpod_sam3 import (RunpodSam3Client, RunpodSam3Segmenter, RunpodVisionSession,
    segmentation_environment, configured_vision_session)
from remote_yam.runpod_sam3_worker import Sam3Worker, TransformersSam3Engine, encode_object
from remote_yam.aspire_codex_policy import AspireCodexPolicy, configured_aspire_policy
from remote_yam.aspire_adapter import BlupeAspireAdapter


class Response:
    def __init__(self, value):
        self.raw = json.dumps(value).encode()
    def __enter__(self):return self
    def __exit__(self, *args):pass
    def read(self, limit):return self.raw[:limit]


class Engine:
    def __init__(self):
        self.warmups = 0
        self.calls = []
    def warmup(self):self.warmups += 1
    def infer(self, rgb, queries, threshold):
        self.calls.append((rgb.copy(), dict(queries), threshold))
        mask = np.zeros(rgb.shape[:2], bool)
        mask[2:5, 3:7] = True
        return {name: (mask if text != 'absent' else np.zeros_like(mask),
                       .9 if text != 'absent' else 0., 1 if text != 'absent' else 0)
                for name, text in queries.items()}


class TransportTests(unittest.TestCase):
    def test_async_queue_polling_and_timing_without_resubmission(self):
        opener = Mock(side_effect=[Response(dict(id='job-1', status='IN_QUEUE')),
            Response(dict(id='job-1', status='IN_PROGRESS')),
            Response(dict(id='job-1', status='COMPLETED', output={'answer': 1}, delayTime=12000, executionTime=250))])
        client = RunpodSam3Client('endpoint', 'secret-fixture', opener=opener, poll_s=.001)
        self.assertEqual(client.call({'operation': 'warmup'}), {'answer': 1})
        self.assertEqual([c.args[0].get_method() for c in opener.call_args_list], ['POST', 'GET', 'GET'])
        self.assertEqual(json.loads(opener.call_args_list[0].args[0].data)['input'], {'operation': 'warmup'})
        self.assertEqual(client.last_timing['delay_ms'], 12000)
        self.assertEqual(client.last_timing['execution_ms'], 250)
        self.assertNotIn('secret-fixture', json.dumps(client.last_timing))
        spans=client.last_timing['spans']
        self.assertEqual([s['operation'] for s in spans if s['stage']=='http'],['run','status','status'])
        self.assertEqual(sum(s['stage']=='poll_wait' for s in spans),2)
        self.assertTrue(any(s['stage']=='cancel_check' for s in spans))
        self.assertTrue(all(s['duration_s']>=0 for s in spans))
        self.assertNotIn('job-1',json.dumps(spans))

    def test_cancellation_cancels_existing_job_without_replaying_submission(self):
        cancelled = threading.Event()
        def open_request(req, **kwargs):
            if req.full_url.endswith('/run'):
                cancelled.set()
                return Response(dict(id='job-1', status='IN_QUEUE'))
            self.assertTrue(req.full_url.endswith('/cancel/job-1'))
            return Response(dict(status='CANCELLED'))
        opener = Mock(side_effect=open_request)
        client = RunpodSam3Client('endpoint', 'key', opener=opener, cancelled=cancelled.is_set)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            client.call({'operation': 'warmup'})
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(client.last_timing['status'],'error')
        self.assertEqual([s['operation'] for s in client.last_timing['spans'] if s['stage']=='http'],
                         ['run','cancel'])

    def test_failed_job_and_timeout_are_terminal_and_cancelled(self):
        for state, timeout in [('FAILED', 1), ('IN_QUEUE', .002)]:
            opener = Mock(return_value=Response(dict(id='job-1', status=state)))
            client = RunpodSam3Client('endpoint', 'key', opener=opener, timeout_s=timeout, poll_s=.01)
            with self.subTest(state=state), self.assertRaises((RuntimeError, TimeoutError)):
                client.call({'operation': 'warmup'})
            self.assertEqual(sum(c.args[0].full_url.endswith('/run') for c in opener.call_args_list), 1)
            self.assertTrue(opener.call_args_list[-1].args[0].full_url.endswith('/cancel/job-1'))

    def test_bad_endpoint_does_not_send_a_key_to_another_host(self):
        opener = Mock()
        for endpoint in ('https://evil.invalid', '../endpoint', 'endpoint?key=value', ''):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                RunpodSam3Client(endpoint, 'key', opener=opener)
        opener.assert_not_called()


class WorkerSegmenterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine()
        self.factory = Mock(return_value=self.engine)
        self.worker = Sam3Worker(self.factory)
        self.rgb = np.arange(8*10*3, dtype=np.uint8).reshape(8, 10, 3)
        self.client = Mock()
        self.client.last_timing = {'elapsed_s': .5}
        self.client.call.side_effect = lambda payload: self.worker.handle({'input': payload})

    def segmenter(self, queries=None):
        return RunpodSam3Segmenter(queries or dict(block='green block', towel='green towel'),
            Path(self.temp.name)/'masks', client=self.client)

    def test_full_worker_protocol_preserves_grid_batches_queries_and_reuses_only_exact_pixels(self):
        segmenter = self.segmenter()
        first = segmenter.segment(self.rgb, 'green block')
        self.assertEqual(first['bbox_xywh'], [3, 2, 4, 3])
        first['mask'][:] = False
        second = segmenter.segment(self.rgb, 'green towel')
        self.assertFalse(first['call_timing']['cache_hit'])
        self.assertTrue(second['call_timing']['cache_hit'])
        self.assertTrue(second['mask'].any())
        self.assertEqual(self.client.call.call_count, 1)
        np.testing.assert_array_equal(self.engine.calls[0][0], self.rgb)
        self.assertEqual(self.engine.calls[0][1], dict(block='green block', towel='green towel'))
        changed = self.rgb.copy();changed[0, 0, 0] += 1
        segmenter.segment(changed, 'green block')
        self.assertEqual(self.client.call.call_count, 2)
        self.factory.assert_called_once()
        self.assertEqual(self.engine.warmups, 1)
        for receipt in Path(self.temp.name).glob('**/request.json'):
            info = json.loads(receipt.read_text())
            self.assertEqual(info['status'], 'VALID_MASKS')
            self.assertEqual(info['robot_commands_sent'], 0)
        self.assertEqual(len(list(Path(self.temp.name).glob('**/*-mask.png'))), 4)

    def test_worker_warmups_load_once_and_do_not_repeat_dummy_inference(self):
        for _ in range(3):
            result = self.worker.handle({'input': {'operation': 'warmup'}})
            self.assertTrue(result['ready'])
        self.factory.assert_called_once()
        self.assertEqual(self.engine.warmups, 1)

    def test_wrong_image_identity_mask_grid_score_and_bounds_are_rejected_and_recorded(self):
        for mutation in ('digest', 'size', 'score', 'box', 'mask_grid'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                def call(payload):
                    result = self.worker.handle({'input': payload})
                    if mutation == 'digest':result['png_sha256'] = 'wrong'
                    if mutation == 'size':result['width'] += 1
                    if mutation == 'score':result['objects']['block']['score'] = '0.9'
                    if mutation == 'box':result['objects']['block']['bbox_xywh'] = [0, 0, 1, 1]
                    if mutation == 'mask_grid':
                        result['objects']['block'] = encode_object(np.ones((1, 1), bool), .9, 1, (1, 1))
                    return result
                client = Mock();client.call.side_effect = call;client.last_timing = {}
                segmenter = RunpodSam3Segmenter({'block': 'green block'}, directory, client=client)
                with self.assertRaises(ValueError):segmenter.segment(self.rgb, 'green block')
                self.assertIsNone(segmenter._cache)
                info = json.loads(next(Path(directory).glob('*/request.json')).read_text())
                self.assertEqual(info['status'], 'REJECTED')

    def test_missing_objects_are_empty_masks_without_fabricated_regions(self):
        result = self.segmenter({'block': 'absent'}).segment(self.rgb, 'absent')
        self.assertEqual(result['status'], 'not_found')
        self.assertFalse(result['mask'].any())
        self.assertEqual(result['bbox_xywh'], [0, 0, 0, 0])

    def test_changed_query_invalidates_cache_and_cancelled_cache_is_not_returned(self):
        segmenter = self.segmenter()
        segmenter.segment(self.rgb, 'green block')
        segmenter.segment(self.rgb, 'red block')
        self.assertEqual(self.client.call.call_count, 2)
        segmenter.cancelled = lambda: True
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            segmenter.segment(self.rgb, 'green block')
        self.assertEqual(self.client.call.call_count, 2)

    def test_bad_worker_input_is_rejected_before_loading_gpu_weights(self):
        buffer = io.BytesIO();Image.fromarray(self.rgb).save(buffer, format='PNG')
        png = buffer.getvalue()
        valid = dict(operation='segment', queries={'block': 'green block'},
            image_png_base64=base64.b64encode(png).decode(), png_sha256=hashlib.sha256(png).hexdigest())
        for change in ({'png_sha256': 'wrong'}, {'queries': {}}, {'score_threshold': True}, {'operation': 'move_robot'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.worker.handle({'input': dict(valid, **change)})
        self.factory.assert_not_called()


class IntegrationTests(unittest.TestCase):
    def test_station_config_reads_key_file_and_forwards_only_selected_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            key_file=Path(directory)/'key';key_file.write_text('fixture-model-key\n')
            config={'segmentation_backend':'runpod_sam3','runpod_sam3':
                {'endpoint_id':'fixture-endpoint','api_key_file':str(key_file),'timeout_s':420}}
            source={'PATH':'fixture-path','UNRELATED_API_KEY':'do-not-forward','HF_TOKEN':'do-not-forward'}
            environment=segmentation_environment(config,environ=source)
            self.assertEqual(environment,{'YAM_SEGMENTATION_BACKEND':'runpod_sam3',
                'RUNPOD_SAM3_ENDPOINT_ID':'fixture-endpoint','RUNPOD_API_KEY':'fixture-model-key',
                'RUNPOD_SAM3_TIMEOUT_S':'420'})
            with patch.dict(os.environ,source,clear=True):
                keeper=configured_vision_session(config)
                self.assertEqual(keeper.client.endpoint_id,'fixture-endpoint')
                self.assertEqual(keeper.client.timeout_s,420)
                self.assertIsNone(keeper._thread)
            self.assertEqual(segmentation_environment(config,environ=dict(source,YAM_SEGMENTATION_BACKEND='astra')),
                {'YAM_SEGMENTATION_BACKEND':'astra'})
            self.assertEqual(segmentation_environment({},environ=source),{})

    def test_environment_override_replaces_astra_binding_and_batches_the_existing_query_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queries = root/'queries.json';queries.write_text(json.dumps({'block': 'green block', 'towel': 'green towel'}))
            config = {'robot.segmentation_backend': 'astra', 'robot.astra_queries_file': str(queries)}
            modules = {
                'cap.env.real_bimanual_yam.skills': SimpleNamespace(make_namespace=lambda *args, **kwargs: {}),
                'cap.agent.tool_handle': SimpleNamespace(make_tool_runner_namespace=lambda: {}),
                'cap.agent.robot_adapters.base': SimpleNamespace(cfg_select=lambda cfg, key, default: cfg.get(key, default))}
            engine = Engine();worker = Sam3Worker(lambda: engine)
            client = Mock();client.last_timing = {}
            client.call.side_effect = lambda payload: worker.handle({'input': payload})
            env = Mock(output_dir=root, cancelled=lambda: False)
            adapter = BlupeAspireAdapter(origin='https://example.invalid', robot_id='fixture', output_dir=root)
            adapter.create_environment = Mock(return_value=env)
            with patch.dict('sys.modules', modules), patch.dict(os.environ, {'YAM_SEGMENTATION_BACKEND': 'runpod_sam3'}), \
                    patch.object(RunpodSam3Client, 'from_env', return_value=client):
                _, namespace = adapter.create_runtime(cfg=config)
                result = namespace['segment_camera_rgb'](np.zeros((8, 10, 3), np.uint8), 'green block')
            self.assertEqual(env.segmentation_backend, 'runpod_sam3')
            self.assertEqual(result['segmentation_backend'], 'runpod_sam3')
            self.assertEqual(engine.calls[0][1], {'block': 'green block', 'towel': 'green towel'})
            self.assertEqual(client.call.call_count, 1)

    def test_configured_provider_creates_a_keeper_without_exposing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = root/'station.py'
            module.write_text('def HarnessClient(*args): return lambda **kwargs: {}\ndef coding_instructions(config): return "fixture"\n')
            keeper = Mock(ready_result=None,client=SimpleNamespace(endpoint_id='fixture-endpoint'))
            with patch.dict(os.environ, {'YAM_SEGMENTATION_BACKEND': 'runpod_sam3', 'RUNPOD_API_KEY': 'runpod-fixture-secret',
                    'RUNPOD_SAM3_ENDPOINT_ID':'fixture-endpoint'}), \
                    patch('remote_yam.runpod_sam3.RunpodVisionSession', return_value=keeper) as factory, \
                    patch('remote_yam.api_depth.ApiDepth.read', return_value='{}'):
                policy = configured_aspire_policy({'harness_module': str(module), 'skill_directory': str(root/'skills')},
                    origin='https://example.invalid', robot_id='fixture', model=None, directory=root)
            factory.assert_called_once()
            self.assertEqual(factory.call_args.args[0].endpoint_id,'fixture-endpoint')
            self.assertIs(policy.vision_session, keeper)
            self.assertNotIn('runpod-fixture-secret', json.dumps(policy.public_config()))
            policy.close_transport()
            keeper.close.assert_called_once()


class InferenceTests(unittest.TestCase):
    def test_long_task_description_is_encoded_to_actual_model_limit_with_provenance(self):
        words=' '.join('token'+str(i) for i in range(90))
        tensor=SimpleNamespace(tolist=lambda:list(range(32)))
        encoded=SimpleNamespace(input_ids=[tensor],to=Mock())
        encoded.to.return_value=encoded
        processor=Mock()
        processor.tokenizer.return_value=encoded
        processor.tokenizer.encode.return_value=list(range(92))
        processor.tokenizer.decode.return_value='effective bounded query'
        engine=object.__new__(TransformersSam3Engine)
        engine.processor=processor
        engine.model=SimpleNamespace(config=SimpleNamespace(text_config=SimpleNamespace(max_position_embeddings=32)))
        result,provenance=engine.encode_text(words)
        self.assertIs(result,encoded)
        processor.tokenizer.assert_called_once_with(words,return_tensors='pt',padding='max_length',
            truncation=True,max_length=32)
        processor.assert_not_called()
        self.assertEqual(provenance,dict(token_limit=32,original_tokens=92,truncated=True,
            effective_text='effective bounded query'))
        processor.tokenizer.encode.return_value=[1,2,3]
        _,provenance=engine.encode_text('short query')
        self.assertFalse(provenance['truncated'])

    def test_one_image_encoder_for_multiple_prompts_and_highest_instance_on_original_grid(self):
        class Tensor:
            def __init__(self, value):self.value = np.asarray(value)
            def detach(self):return self
            def float(self):return self
            def cpu(self):return self
            def numpy(self):return self.value
            def __getitem__(self, index):return Tensor(self.value[index])

        class Inputs(dict):
            def __init__(self, **kwargs):super().__init__(**kwargs);self.__dict__.update(kwargs)
            def to(self, device):return self

        rgb = np.zeros((8, 10, 3), np.uint8)
        expected = np.zeros((8, 10), bool);expected[2:5, 3:7] = True
        processor = Mock(side_effect=lambda **kwargs: Inputs(pixel_values='image')
            if 'images' in kwargs else Inputs(input_ids=kwargs['text']))
        processor.post_process_instance_segmentation.side_effect = [
            [dict(scores=Tensor([.3, .9]), masks=Tensor([np.ones_like(expected), expected]))],
            [dict(scores=Tensor([]), masks=Tensor(np.zeros((0, 8, 10), bool)))]]
        model = Mock();model.get_vision_features.return_value = 'shared-vision'
        engine = object.__new__(TransformersSam3Engine)
        engine.torch = SimpleNamespace(inference_mode=nullcontext,
            cuda=SimpleNamespace(synchronize=Mock()))
        engine._bf16 = False;engine.processor = processor;engine.model = model
        engine.encode_text = lambda text: (Inputs(input_ids=text), {'effective_text':text})

        output = engine.infer(rgb, {'block': 'green block', 'missing': 'absent'}, .2)
        model.get_vision_features.assert_called_once_with(pixel_values='image')
        self.assertEqual([call.kwargs['vision_embeds'] for call in model.call_args_list],
            ['shared-vision', 'shared-vision'])
        self.assertEqual([call.kwargs['input_ids'] for call in model.call_args_list], ['green block', 'absent'])
        for call in processor.post_process_instance_segmentation.call_args_list:
            self.assertEqual(call.kwargs['target_sizes'], [(8, 10)])
        np.testing.assert_array_equal(output['block'][0], expected)
        self.assertEqual(output['block'][1:], (.9, 2))
        self.assertEqual(output['missing'][0].shape, (8, 10))
        self.assertFalse(output['missing'][0].any())
        self.assertEqual(output['missing'][1:], (0., 0))
        engine.torch.cuda.synchronize.assert_called_once()


class SessionTests(unittest.TestCase):
    def test_public_startup_status_tracks_warmup_and_survives_policy_handoff(self):
        entered, release = threading.Event(), threading.Event()
        clock = [100.]
        client = Mock();client.last_timing = {'private': 'fixture-secret'}
        def warmup():
            entered.set()
            if not release.wait(2):raise RuntimeError('Fixture warmup not released')
            return {'ready': True, 'model': 'facebook/sam3'}
        client.warmup.side_effect = warmup
        session = RunpodVisionSession(client)
        self.assertEqual(session.public_status()['state'], 'idle')
        with patch('remote_yam.runpod_sam3.time.time', side_effect=lambda: clock[0]):
            session.start()
            try:
                self.assertTrue(entered.wait(1))
                starting = session.public_status()
                self.assertEqual(starting['state'], 'starting')
                self.assertEqual(starting['started_at'], 100.)
                self.assertIsNone(starting['ready_at'])
                with tempfile.TemporaryDirectory() as directory:
                    policy = AspireCodexPolicy(harness=Mock(), calibration={}, robot_id='fixture', task='Fixture',
                        directory=Path(directory)/'run', instructions='', skill_directory=Path(directory)/'skills',
                        vision_session=session)
                    self.assertEqual(policy.public_config()['task_progress']['vision'], starting)
                clock[0] = 145.8
                release.set();session.wait_ready()
                ready = session.public_status()
                self.assertEqual(ready['state'], 'ready')
                self.assertEqual(ready['ready_at'], 145.8)
                self.assertEqual(ready['started_at'], starting['started_at'])
                self.assertNotIn('fixture-secret', json.dumps(ready))
                clock[0] = 147.
                session.close()
                self.assertEqual(session.public_status()['state'], 'stopped')
                self.assertEqual(session.public_status()['ended_at'], 147.)
            finally:
                release.set();session.close();session._thread.join(2)

    def test_failed_startup_has_a_terminal_timestamp_without_exposing_raw_error(self):
        session = RunpodVisionSession(Mock(warmup=Mock(side_effect=RuntimeError('fixture-private-error'))))
        try:
            session.start()
            with self.assertRaisesRegex(RuntimeError, 'fixture-private-error'):session.wait_ready()
            status = session.public_status()
            self.assertEqual(status['state'], 'failed')
            self.assertGreaterEqual(status['ended_at'], status['started_at'])
            self.assertNotIn('fixture-private-error', json.dumps(status))
        finally:
            session.close();session._thread.join(2)

    def test_close_stops_keepalive_and_failure_is_not_ready(self):
        client = Mock();client.last_timing = {}
        client.warmup.return_value = {'ready': True, 'model': 'facebook/sam3'}
        session = RunpodVisionSession(client, interval_s=.01)
        session.start();session.wait_ready();session.close();session._thread.join(1)
        count = client.warmup.call_count
        time.sleep(.03)
        self.assertEqual(client.warmup.call_count, count)
        failed = RunpodVisionSession(Mock(warmup=Mock(side_effect=RuntimeError('load failed'))))
        failed.start()
        with self.assertRaisesRegex(RuntimeError, 'load failed'):failed.wait_ready()
        failed.close()

    def test_warmup_overlaps_generation_and_is_ready_before_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            order = []
            vision = Mock()
            vision.start.side_effect = lambda **kwargs: order.append('start')
            vision.wait_ready.side_effect = lambda: order.append('ready')
            vision.close.side_effect = lambda: order.append('close')
            reply = dict(action='program', source="def build_task(tools):\n return {}\ndef evaluate(tools, task):\n return {}\n",
                summary='fixture', lesson='', queries=[dict(name='block', query='green block')])
            def generate(**kwargs):order.append('generate');return reply
            def harness(**kwargs):
                order.append(kwargs['mode'])
                if kwargs['mode'] == 'observe':return dict(status='OBSERVED', context={}, images={}, snapshot='fixture')
                return dict(status='PLAN_ONLY', planning_success=True)
            policy = AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture', task='pick block',
                directory=Path(directory)/'run', instructions='', skill_directory=Path(directory)/'skills',
                generator=generate, vision_session=vision)
            policy.prepare_before_session(policy.task)
            self.assertLess(order.index('start'), order.index('generate'))
            self.assertLess(order.index('generate'), order.index('ready'))
            self.assertLess(order.index('ready'), order.index('plan'))
            self.assertNotIn('close', order)
            policy.close_transport()
            self.assertEqual(order[-1], 'close')

    def test_vision_failure_prevents_plan_and_closes_keeper(self):
        with tempfile.TemporaryDirectory() as directory:
            vision = Mock();vision.wait_ready.side_effect = RuntimeError('GPU unavailable')
            reply = dict(action='program', source="def build_task(tools):\n return {}\ndef evaluate(tools, task):\n return {}\n",
                summary='fixture', lesson='', queries=[dict(name='block', query='green block')])
            harness = Mock(return_value=dict(status='OBSERVED', context={}, images={}, snapshot='fixture'))
            policy = AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture', task='pick block',
                directory=Path(directory)/'run', instructions='', skill_directory=Path(directory)/'skills',
                generator=lambda **kwargs: reply, vision_session=vision)
            with self.assertRaisesRegex(RuntimeError, 'GPU unavailable'):
                policy.prepare_before_session(policy.task)
            self.assertEqual([c.kwargs['mode'] for c in harness.call_args_list], ['observe'])
            vision.close.assert_called_once()


if __name__ == '__main__':unittest.main()
