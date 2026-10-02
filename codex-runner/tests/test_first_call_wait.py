import unittest
from remote_yam.first_call_wait import FirstCallWait

class API:
    def __init__(self):self.operations=[];self.fail=False
    def _capability(self,sid):return 'private'
    def _request(self,method,path,payload=None,**kw):
        op=payload['operation'];self.operations.append(op)
        if self.fail:raise TimeoutError('ambiguous request')
        return dict(status='completed',result=dict(state='planning' if op=='start' else 'returned',settled=op=='finish'))

class Tests(unittest.TestCase):
    def test_capture_boundary_first_call_only_and_return_barrier(self):
        api=API();wait=FirstCallWait(api,lambda:dict(session_id='s',episode_id='e',lease_id='l'),lambda:False)
        for call in (1,2):
            wait.interaction('observation',{'call':call})
            wait.interaction('model_request',{'call':call})
            wait.interaction('model_timing',{'call':call})
        self.assertEqual(api.operations,['start','finish']);self.assertTrue(wait.finished)
    def test_ambiguous_write_is_not_retried(self):
        api=API();api.fail=True
        wait=FirstCallWait(api,lambda:dict(session_id='s',episode_id='e',lease_id='l'),lambda:False)
        with self.assertRaises(TimeoutError):wait.interaction('model_request',{'call':1})
        self.assertEqual(api.operations,['start'])

    def test_provider_returns_before_using_response_and_only_first_call(self):
        from unittest.mock import patch
        from remote_yam.robocurve_policy import OpenAIAdapter
        from remote_yam.providers import PolicyComplete
        from test_robocurve_policy import Cameras, observation, response, finished
        api = API()
        provider = OpenAIAdapter('mock-key', 'gpt-6-astra', camera_source=Cameras())
        wait = FirstCallWait(api, lambda: dict(session_id='s', episode_id='e', lease_id='l'), lambda: False)
        provider.interaction_sink = lambda kind, message, **details: wait.interaction(kind, details)
        def model(payload):
            self.assertEqual(api.operations, ['start'])
            self.assertEqual(provider._camera_source.calls, 1)
            return response('move_to', {'targets': {'left_z': .195}, 'note': 'up'})
        with patch.object(provider, '_post_json', side_effect=model):
            points = provider.build_trajectory('pick', observation(), 0)
        self.assertEqual(api.operations, ['start', 'finish'])
        provider.trajectory_completed(finished(observation(), points), len(points))
        with patch.object(provider, '_post_json', return_value=response('done', {'summary': 'done', 'hindsight': 'none'}, 'call_2')):
            with self.assertRaises(PolicyComplete):
                provider.build_trajectory('pick', finished(observation(), points), len(points))
        self.assertEqual(api.operations, ['start', 'finish'])

    def test_model_error_still_returns_arms(self):
        from unittest.mock import patch
        from remote_yam.robocurve_policy import OpenAIAdapter
        from test_robocurve_policy import Cameras, observation
        api = API()
        provider = OpenAIAdapter('mock-key', 'gpt-6-astra', camera_source=Cameras())
        wait = FirstCallWait(api, lambda: dict(session_id='s', episode_id='e', lease_id='l'), lambda: False)
        provider.interaction_sink = lambda kind, message, **details: wait.interaction(kind, details)
        with patch.object(provider, '_post_json', side_effect=RuntimeError('model unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'model unavailable'):
                provider.build_trajectory('pick', observation(), 0)
        self.assertEqual(api.operations, ['start', 'finish'])

    def test_unsettled_return_blocks_response(self):
        from unittest.mock import patch
        api = API()
        wait = FirstCallWait(api, lambda: dict(session_id='s', episode_id='e', lease_id='l'), lambda: False)
        wait.interaction('model_request', {'call': 1})
        with patch.object(api, '_request', return_value=dict(status='completed', result=dict(state='returning', settled=False))):
            with self.assertRaisesRegex(RuntimeError, 'return not verified'):
                wait.interaction('model_timing', {'call': 1})
        self.assertFalse(wait.finished)

    def test_controller_wires_wait_only_for_enabled_robot(self):
        from unittest.mock import patch
        from remote_yam.controller import RunnerController
        from remote_yam.robocurve_policy import OpenAIAdapter
        from remote_yam.session import MockSessionAPI
        from test_robocurve_policy import Cameras
        for enabled in (False, True):
            api = MockSessionAPI()
            controller = RunnerController(api, share_conversation=False)
            provider = OpenAIAdapter('mock-key', 'gpt-6-astra', camera_source=Cameras())
            with patch.dict('os.environ', {'YAM_FIRST_CALL_WANDER_ROBOTS': 'yam-1' if enabled else ''}):
                state = controller.join(provider, 'pick')
            self.assertEqual(state['first_call_wander_enabled'], enabled)
            with patch.object(FirstCallWait, 'interaction') as interaction:
                provider._calls = 1
                provider._interaction('model_request', 'mock')
                self.assertEqual(interaction.call_count, int(enabled))
            controller.disconnect()
