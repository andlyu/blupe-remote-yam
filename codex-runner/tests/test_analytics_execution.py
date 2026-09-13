"""Analytics start means hardware execution, never queue/lifecycle acceptance."""
import unittest
from unittest.mock import Mock
from remote_yam.controller import RunnerController
from remote_yam.providers import ScriptedAdapter
from remote_yam.session import MockSessionAPI


class AnalyticsExecutionTests(unittest.TestCase):
    def test_action_execution_survives_terminal_and_resets_on_new_run(self):
        runner = RunnerController(MockSessionAPI(auto_activate=False))
        runner.join(ScriptedAdapter([]), 'private task')
        def action(status):
            runner._handle_event({'schema_version': 1, 'session_id': runner._session_id,
                                  'type': 'action_result', 'payload': {'status': status}})
        original = runner.status()['analytics_run']['run_id']
        action('accepted')
        self.assertFalse(runner.status()['analytics_run']['execution_confirmed'])
        action('executed')
        runner.stop()
        self.assertTrue(runner.status()['analytics_run']['execution_confirmed'])
        runner.join(ScriptedAdapter([]), 'another private task')
        self.assertFalse(runner.status()['analytics_run']['execution_confirmed'])
        self.assertNotEqual(original, runner.status()['analytics_run']['run_id'])
        runner.stop()

    def test_trajectory_requires_valid_execution_progress(self):
        runner = RunnerController(Mock())
        runner._analytics_run = {'execution_confirmed': False}
        runner._trajectory = {'trajectory_id':'t', 'state':'accepted', 'first_step_id':0,
                              'progress_count':0, 'waypoint_count':2}
        with self.assertRaises(RuntimeError):
            runner._trajectory_progress({'trajectory_id':'t', 'step_id':0})
        self.assertFalse(runner._analytics_run['execution_confirmed'])
        runner._trajectory_progress({'trajectory_id':'t', 'step_id':0, 'executed_at':1.0})
        self.assertTrue(runner._analytics_run['execution_confirmed'])
