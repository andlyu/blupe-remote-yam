import copy
import unittest
from unittest.mock import Mock
from remote_yam.controller import RunnerController


class CompletedRecoveryTests(unittest.TestCase):
    def setup_recovery(self):
        record = {'session_id': 's', 'episode_id': 'e', 'lease_id': 'l', 'trajectory_id': 't'}
        trace = {
            'trajectory_results': [{**record, 'status': status, 'reported_at': 1.0} for status in ('accepted','completed')],
            'trajectory_progress': [{**record, 'step_id': step, 'executed_at': 1.0} for step in (3,4)],
        }
        api = Mock()
        api.get_episode_trace.return_value = trace
        runner = RunnerController(api)
        runner._session_id, runner._episode_id, runner._lease_id = 's', 'e', 'l'
        runner._trajectory = {'trajectory_id':'t','state':'dispatched','first_step_id':3,'last_step_id':4,'waypoint_count':2,'progress_count':0}
        return runner, trace

    def test_trace_recovery_does_not_skip_final_observation_validation(self):
        runner, _ = self.setup_recovery()
        runner._recover_finished_trajectory()
        self.assertEqual(runner._trajectory['state'], 'accepted')
        self.assertEqual(runner._trajectory['progress_count'], 2)
        self.assertFalse(runner._trajectory['final_observation_received'])

    def test_incomplete_progress_is_not_treated_as_completion(self):
        runner, trace = self.setup_recovery()
        trace['trajectory_progress'].pop()
        with self.assertRaisesRegex(RuntimeError, 'incomplete or unordered'):
            runner._recover_finished_trajectory()
        self.assertEqual(runner._trajectory['state'], 'dispatched')

    def test_wrong_lease_is_rejected(self):
        runner, trace = self.setup_recovery()
        trace['trajectory_results'][0]['lease_id'] = 'other'
        with self.assertRaisesRegex(RuntimeError, 'another episode or lease'):
            runner._recover_finished_trajectory()

    def test_failed_packet_is_not_recovered_as_success(self):
        runner, trace = self.setup_recovery()
        trace['trajectory_results'][-1]['status'] = 'rejected'
        with self.assertRaisesRegex(RuntimeError, 'failed while disconnected'):
            runner._recover_finished_trajectory()

    def test_disconnect_records_transport_type_without_secret_text(self):
        from contextlib import redirect_stdout
        from io import StringIO
        runner, _ = self.setup_recovery()
        cause = ConnectionResetError(54, 'Authorization: secret-test-key')
        wrapped = ConnectionError('connection failed with secret-test-key')
        wrapped.__cause__ = cause
        output = StringIO()
        with redirect_stdout(output):
            runner._record_event_disconnect(wrapped)
        event = runner.status()['run_summary']['events'][-1]
        self.assertEqual(event['cause_type'], 'ConnectionResetError')
        self.assertEqual(event['errno'], 54)
        self.assertNotIn('secret-test-key', str(event)+output.getvalue())
