import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.aspire_skill_review import DeferredSkillReview, SubscriptionSkillCoordinator, promotion_knowledge


class StartupReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_launch_uses_one_verified_snapshot_without_calling_old_run_reviewer(self):
        snapshot = {'entries': [], 'library_sha256': 'verified-fixture'}
        library = SimpleNamespace(retrieve=Mock(return_value=snapshot),
            review_pending=Mock(side_effect=AssertionError('Historical review on launch')),
            require_reviewed=Mock(side_effect=AssertionError('Pending learning gate on launch')))
        policy = AspireCodexPolicy(harness=lambda **kw: None, calibration={}, robot_id='fixture',
            task='Fixture task', directory=self.root/'policy', instructions='',
            skill_directory=self.root/'programs', skill_learning=library,
            skill_review_timing='after_run')
        policy._prepare_program_before_session = Mock()
        policy.prepare_before_session(policy.task)
        policy._review_prior_skills(policy.task)  # The live handoff does not recheck history.
        self.assertEqual(policy._topic_knowledge(), snapshot)
        library.retrieve.assert_called_once()
        library.review_pending.assert_not_called()
        library.require_reviewed.assert_not_called()
        receipt = json.loads((policy.directory/'startup-review.json').read_text())
        self.assertEqual(receipt['historical_model_requests'], 0)
        self.assertEqual(receipt['library_sha256'], 'verified-fixture')
        self.assertEqual(policy.public_config()['skill_learning']['review_timing'], 'after_run')

    def test_slow_background_review_is_single_worker_and_does_not_hold_launch(self):
        entered, release = threading.Event(), threading.Event()
        def review(coordinator, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return [{'task': 'fixture'}]
        library = SimpleNamespace(root=self.root, suite='fixture', campaign=self.root/'campaign',
            review_pending=Mock(side_effect=review))
        coordinator = Mock()
        factory = Mock(return_value=coordinator)
        job = DeferredSkillReview.schedule(library, factory)
        try:
            self.assertTrue(entered.wait(1))
            same = DeferredSkillReview.schedule(library, factory)
            self.assertIs(same, job)
            self.assertFalse(job.done.is_set())
            factory.assert_called_once()
        finally:
            release.set()
            self.assertTrue(job.done.wait(2))
        self.assertEqual(job.status['status'], 'COMPLETE')
        self.assertEqual(library.review_pending.call_count, 2)
        library.review_pending.assert_called_with(coordinator, current_task=None)
        coordinator.close.assert_called_once()

    def test_background_review_error_is_recorded_separately_and_can_retry(self):
        library = SimpleNamespace(root=self.root, suite='fixture', campaign=self.root/'campaign',
            review_pending=Mock(side_effect=RuntimeError('Saved finding needs repair')))
        job = DeferredSkillReview.schedule(library, lambda: Mock())
        self.assertTrue(job.done.wait(2))
        self.assertEqual(job.status['status'], 'FAILED')
        receipt = json.loads((library.campaign/'background-review.json').read_text())
        self.assertEqual(receipt['physical_commands_sent'], 0)
        self.assertEqual(receipt['error_type'], 'RuntimeError')
        library.review_pending = Mock(return_value=[])
        retry = DeferredSkillReview.schedule(library, lambda: Mock())
        self.assertIsNot(retry, job)
        self.assertTrue(retry.done.wait(2))
        self.assertEqual(retry.status['status'], 'COMPLETE')

    def test_compact_review_context_keeps_failure_scopes_and_exact_snippets(self):
        snippet = {'code': 'center = points.mean(axis=0)', 'source': '/fixture.py',
            'source_sha256': 'source-hash', 'start_line': 1, 'end_line': 5}
        recipe = {'id': 'measured-center', 'version': 'version-hash', 'topic': 'localize',
            'trigger': 'Block placement', 'statuses': ['failure', 'provisional'],
            'limits': ['Contact height unverified'], 'snippets': [snippet],
            'evidence': [{'task_id': 'old', 'finding_status': 'failure',
                'postpark_checks': {'supported_height': False},
                'full_recursive_history': 'duplicate' * 10000}]}
        library = SimpleNamespace(retrieve=lambda task: {'library_sha256': 'verified',
            'entries': [recipe], 'failure_findings': [recipe]})
        compact = promotion_knowledge(library, 'Fixture task')
        self.assertEqual(len(compact['entries']), 1)
        item = compact['entries'][0]
        self.assertEqual(item['snippets'], [snippet])
        self.assertEqual(item['statuses'], ['failure', 'provisional'])
        self.assertEqual(item['evidence'][0]['postpark_checks'], {'supported_height': False})
        self.assertLess(len(json.dumps(compact)), 1200)
        self.assertIn('full_recursive_history', recipe['evidence'][0])

    def test_background_transport_uses_text_only_low_reasoning_and_preserves_numbered_source(self):
        library = SimpleNamespace(retrieve=lambda task: {'entries': []})
        provider = SimpleNamespace(_calls=0, _post_json=Mock(return_value={'findings': [], 'reason': 'No new lesson'}),
            _workspace=SimpleNamespace(cleanup=Mock()))
        coordinator = SubscriptionSkillCoordinator(library, 'fixture')
        with patch('remote_yam.aspire_skill_review.CodexAdapter', return_value=provider):
            coordinator(packet={'task': 'Fixture task'}, sources={'/fixture.py': 'x = 1\ny = 2'}, feedback=None)
        payload = provider._post_json.call_args.args[0]
        body = json.loads(payload['input'][0]['content'][0]['text'])
        self.assertEqual(body['numbered_source_programs']['/fixture.py'], '1 x = 1\n2 y = 2')
        self.assertEqual(payload['tools'], [])
        self.assertEqual(provider._expected_camera_count, 0)
        self.assertEqual(provider.reasoning_effort, 'low')
        coordinator.close()
        provider._workspace.cleanup.assert_called_once()

    def test_preparation_and_live_handoff_reuse_snapshot_and_still_plan_fresh(self):
        source = "def build_task(tools):\n return {'steps': []}\ndef evaluate(tools, task):\n return {'success': False}\n"
        response = dict(action='program', source=source, summary='Fixture', lesson='',
            queries=[dict(name='block', query='green block')])
        calls = []
        def harness(**request):
            calls.append(request)
            if request['mode']=='observe':
                return {'status': 'OBSERVED', 'context': {}, 'images': {}, 'snapshot': 'fixture'}
            if request['mode']=='plan':
                return {'status': 'PLAN_ONLY', 'planning_success': True}
            return {'status': 'SCENE_CAPTURE_FAILED', 'physical_motion_calls': 0}
        library = SimpleNamespace(retrieve=Mock(return_value={'entries': [], 'library_sha256': 'fixture'}),
            stage_policy=Mock(return_value={'status': 'AWAITING_PROMOTION_REVIEW'}))
        policy = AspireCodexPolicy(harness=harness, calibration={}, robot_id='fixture', task='Fixture task',
            directory=self.root/'policy', instructions='', skill_directory=self.root/'programs',
            generator=lambda **kw: response, skill_learning=library, skill_review_timing='after_run')
        policy.prepare_before_session(policy.task)
        with patch.object(policy, '_schedule_deferred_review'):
            self.assertEqual(policy._coding_loop()['status'], 'SCENE_CAPTURE_FAILED')
        library.retrieve.assert_called_once()
        self.assertEqual([c['mode'] for c in calls], ['observe', 'plan', 'execute'])
        self.assertNotIn('snapshot', calls[-1])
        self.assertIs(policy._topic_snapshot, policy._preparation._topic_snapshot)


if __name__ == '__main__':
    unittest.main()
