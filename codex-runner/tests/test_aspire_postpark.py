"""Offline outcome review, including the actual local web worker lifecycle."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playground import EphemeralController
from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.aspire_lineage_catalog import LineageCatalog
from remote_yam.providers import ScriptedAdapter, PolicyComplete
from remote_yam.session import MockSessionAPI

SOURCE = "def build_task(tools):\n return {'steps': []}\ndef evaluate(tools, task):\n return {'success': False}\n"


class PostparkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def policy(self, *, native_success=False, evaluation_success=False, parking='PARKING_OBSERVED'):
        self.requests = []
        native = dict(status='SUCCESS' if native_success else 'UNVERIFIED', success=native_success,
                      planning_success=True, physical_motion_calls=13)
        def harness(**request):
            self.requests.append(request)
            if request['mode'] == 'observe':
                return dict(status='OBSERVED', context={}, images={}, snapshot='fixture')
            if request['mode'] == 'plan':
                return dict(status='PLAN_ONLY', planning_success=True, success=False)
            if request['mode'] == 'execute':
                path=Path(request['directory']);path.mkdir(parents=True)
                (path/'task_definition.json').write_text('{"measured_live_geometry":true}')
                (path/'task_result.json').write_text(json.dumps(native))
                return native
            self.assertEqual(request['mode'], 'evaluate')
            self.assertNotIn('bridge_socket', request)
            self.assertNotIn('snapshot', request)
            self.assertFalse(request['cancelled']())
            self.assertEqual(Path(request['program']).read_text(), SOURCE)
            self.assertTrue(json.loads(Path(request['task_definition']).read_text())['measured_live_geometry'])
            return dict(status='EVALUATION_ONLY', success=evaluation_success, physical_motion_calls=0,
                        placement_evidence={'evidence': {'checks': {'centered_over_chip': evaluation_success}}},
                        scene={'images': {'top': 'fresh-parked-fixture.png'},'context':{
                            'measured_observation':{'source':'hardware','mode':'DISABLED'}}})
        reply=dict(action='program',source=SOURCE,summary='fixture',lesson='fixture',
                   queries=[dict(name='block',query='green rectangular block')])
        policy=AspireCodexPolicy(harness=harness,calibration={},robot_id='fixture',task='Place green on red chip',
            directory=self.root/'run',instructions='fixture',skill_directory=self.root/'skills',
            allow_hardware=True,generator=lambda **kwargs:reply)
        recorder=Mock()
        recorder.finish_after_stop.return_value=dict(status=parking,physical_commands=0)
        self.recorder=recorder
        policy.configure_postpark_review('https://fixture.invalid',
            Mock(return_value={'source':'hardware','mode':'DISABLED'}),recorder_factory=Mock(return_value=recorder))
        policy._outcome=policy._coding_loop('fixture-motion-bridge')
        policy._finished=True
        return policy,native

    def test_provisional_success_is_not_promoted_and_negative_parked_view_survives_stop(self):
        policy,native=self.policy(native_success=True)
        self.assertEqual(json.loads(next(policy.skill_directory.glob('*.json')).read_text())['validation'],
                         'FULL_PLAN_ONLY')
        # Normal Stop cancels task commands; it must not cancel read-only review.
        policy.cancelled=lambda:True
        policy.review_after_session_stop()
        result=policy.public_config()['task_outcome']
        self.assertFalse(result['success'])
        self.assertEqual(result['failed_checks'],['centered_over_chip'])
        self.assertEqual(result['physical_commands_sent_by_review'],0)
        self.assertEqual(json.loads((policy.directory/'attempt-01/execute/task_result.json').read_text()),native)
        manifest=json.loads(next(policy.skill_directory.glob('*.json')).read_text())
        self.assertEqual(manifest['validation'],'FULL_PLAN_ONLY')
        self.assertEqual(policy.public_config()['task_progress']['updates'][-1]['happened'],
                         'After-parking outcome: UNVERIFIED.')
        self.assertEqual([r['mode'] for r in self.requests],['observe','plan','execute','evaluate'])
        policy.review_after_session_stop()
        self.assertEqual(len(self.requests),4)

    def test_positive_fresh_parked_evidence_promotes_exact_source(self):
        policy,_=self.policy(evaluation_success=True)
        policy.review_after_session_stop()
        self.assertTrue(policy.public_config()['task_outcome']['success'])
        self.assertEqual(policy.public_config()['task_outcome']['native_status'],'UNVERIFIED')
        manifest=json.loads(next(policy.skill_directory.glob('*.json')).read_text())
        self.assertEqual(manifest['validation'],'PHYSICAL_SUCCESS')
        self.assertFalse(policy._physical_feedback_path().exists())
        self.recorder.start.assert_called_once()

    def test_unknown_parking_does_not_evaluate_or_claim_success(self):
        policy,_=self.policy(native_success=True,parking='UNVERIFIED',evaluation_success=True)
        policy.review_after_session_stop()
        self.assertFalse(policy.public_config()['task_outcome']['success'])
        self.assertEqual([r['mode'] for r in self.requests],['observe','plan','execute'])
        self.assertIn('not established',policy.public_config()['task_outcome']['reason'])

    def test_transport_teardown_prevents_a_later_scene_from_verifying_old_task(self):
        policy,_=self.policy(evaluation_success=True)
        policy.close_transport()
        policy.review_after_session_stop()
        self.assertFalse(policy.public_config()['task_outcome']['success'])
        self.assertEqual([r['mode'] for r in self.requests],['observe','plan','execute'])

    def test_native_motion_failure_cannot_be_overridden_by_positive_image(self):
        policy,_=self.policy(evaluation_success=True)
        policy._outcome.update(status='FAILED',reason='packet aborted')
        policy.review_after_session_stop()
        self.assertFalse(policy.public_config()['task_outcome']['success'])
        self.assertEqual(policy.public_config()['task_outcome']['status'],'FAILED')

    def test_another_active_run_cannot_verify_the_stopped_task(self):
        policy,_=self.policy(evaluation_success=True)
        policy._postpark_observe=Mock(return_value={'source':'hardware','mode':'API_ACTIVE'})
        policy.review_after_session_stop()
        self.assertFalse(policy.public_config()['task_outcome']['success'])
        self.assertIn('not confirmed throughout',policy.public_config()['task_outcome']['reason'])

    def test_saved_postpark_receipt_and_images_are_available_in_read_only_catalog(self):
        policy,_=self.policy(evaluation_success=True)
        image=policy.directory/'parked-top.png';image.write_bytes(b'fixture image')
        original=policy.harness
        def harness(**request):
            result=original(**request)
            result['scene']={'images':{'top':str(image)},'context':{'camera_metadata':{
                'top':{'metadata':{'captured_at':123.5}}},
                'measured_observation':{'source':'hardware','mode':'DISABLED'}}}
            return result
        policy.harness=harness
        policy.review_after_session_stop()
        loop=json.loads((policy.directory/'coding-loop.json').read_text())
        self.assertEqual(loop['task_completion']['images'][0]['captured_at'],123.5)
        catalog=LineageCatalog({'skill_directory':str(policy.skill_directory),'robot_id':'fixture'})
        item=catalog.episode(dict(task_id='fixture',task=policy.task,policy_directory=str(policy.directory),
            coding_loop=str(policy.directory/'coding-loop.json'),receipt=str(policy.directory/'task-completion.json'),
            review=str(policy.directory/'postpark-review.json')))
        self.assertTrue(item['after_parking_success'])
        self.assertEqual(item['native_status'],'UNVERIFIED')
        self.assertEqual(item['images'][0]['url'],policy.public_config()['task_outcome']['images'][0]['url'])
        self.assertEqual(item['task_updates'][-1]['happened'],'After-parking outcome: SUCCESS.')

    def test_web_worker_reviews_after_stop_before_transport_teardown_without_extra_packets(self):
        api=MockSessionAPI()
        order=[]
        original=api.stop_session
        def stop(*args,**kwargs):
            result=original(*args,**kwargs);order.append('stop');return result
        api.stop_session=stop
        class Reviewed(ScriptedAdapter):
            def infer(self,*args):
                raise PolicyComplete('fixture complete')
            def review_after_session_stop(self):
                order.append('review')
            def close_transport(self):
                order.append('close')
        provider=Reviewed([])
        controller=EphemeralController(api,hardware_control_enabled=False,share_conversation=False)
        controller.join_and_run(provider,'offline lifecycle fixture')
        controller._worker.join(5)
        self.assertFalse(controller._worker.is_alive())
        self.assertEqual(order,['stop','review','close'])
        self.assertEqual(api.trajectory_log,[])
        self.assertEqual(len(api.create_requests),1)  # Mock only; no physical session.
        self.assertIsNone(controller.status()['error'])


if __name__=='__main__':
    unittest.main()
