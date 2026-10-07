"""Provider construction must not hold the worker's HTTP/control path."""
import json
import threading
import time
import unittest

from remote_yam.aspire_worker import AspireWorkerServer, RemoteAspirePolicy
from remote_yam.controller import RunnerController
from test_aspire_worker import CountingAPI, PacketPolicy, TASK, TOKEN


class CreationTests(unittest.TestCase):
    def setUp(self):
        self.native = PacketPolicy()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.created = []

        def factory(prompt):
            self.created.append(prompt)
            self.entered.set()
            if not self.release.wait(12):
                raise RuntimeError('Fixture factory was not released')
            return self.native

        self.server = AspireWorkerServer(('127.0.0.1', 0), TOKEN, 'yam-1', factory)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.release.set)

    def policy(self, **kwargs):
        client = RemoteAspirePolicy(self.origin, TOKEN, robot_id='yam-1', poll_s=.01, **kwargs)
        self.addCleanup(client.close_transport)
        return client

    def wait_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(condition())

    def start_preparation(self, client):
        errors = []

        def prepare():
            try:
                client.prepare_before_session(TASK)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=prepare, daemon=True)
        thread.start()
        self.assertTrue(self.entered.wait(2))
        return thread, errors

    def test_creation_longer_than_http_deadline_uses_one_simulated_reservation(self):
        client = self.policy()
        api = CountingAPI()
        controller = RunnerController(api)
        controller.update_monitor_observation(api.get_robot_observation('yam-1'))
        self.addCleanup(controller.stop)
        # Each readiness subprocess permits five seconds. A valid constructor
        # may exceed the transport's per-request five-second deadline in total.
        timer = threading.Timer(5.3, self.release.set)
        self.addCleanup(timer.cancel)
        timer.start()
        started = time.monotonic()
        controller.join_and_run(client, TASK)
        self.assertTrue(self.entered.wait(2))
        self.assertEqual(api.admissions, 0)
        self.assertEqual(client._request('/health')['ready'], True)
        controller._worker.join(9)
        self.assertFalse(controller._worker.is_alive())
        self.assertGreaterEqual(time.monotonic() - started, 5.2)
        self.assertIsNone(controller.status()['error'], controller.status())
        self.assertEqual(self.created, [TASK])
        self.assertEqual(api.admissions, 1)
        self.assertEqual(self.native.prepared, [TASK])
        self.assertEqual(len(api.trajectory_log), 1)  # MockSessionAPI only.

    def test_duplicate_pending_creation_is_fast_and_factory_runs_once(self):
        client = self.policy()
        thread, errors = self.start_preparation(client)
        started = time.monotonic()
        state = client._request('/runs/' + client._run_id,
            dict(prompt=TASK, robot_id='yam-1'))
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(state['creation']['status'], 'running')
        self.assertEqual(self.created, [TASK])
        self.release.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.native.prepared, [TASK])

    def test_stop_during_creation_is_prompt_and_closes_late_provider(self):
        client = self.policy()
        cancelled = threading.Event()
        client.cancelled = cancelled.is_set
        thread, errors = self.start_preparation(client)
        cancelled.set()
        started = time.monotonic()
        client.close_transport()
        self.assertLess(time.monotonic() - started, 1)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.release.set()
        self.wait_until(lambda: self.native.closed)
        self.assertTrue(self.native.cancelled())
        self.assertEqual(self.native.prepared, [])
        self.assertEqual(self.native.observations, [])
        self.assertEqual(self.created, [TASK])

    def test_bounded_creation_timeout_closes_late_provider_without_replay(self):
        client = self.policy(timeout_s=.15)
        thread, errors = self.start_preparation(client)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIn('timed out; this request was not replayed', str(errors[0]))
        self.assertTrue(self.server.runs[client._run_id].closed)
        self.release.set()
        self.wait_until(lambda: self.native.closed)
        self.assertEqual(self.created, [TASK])
        self.assertEqual(self.native.prepared, [])

    def test_expired_pending_creation_keeps_identifier_until_late_cleanup(self):
        client = self.policy()
        client._created = True
        client._request('/runs/' + client._run_id, dict(prompt=TASK, robot_id='yam-1'))
        self.assertTrue(self.entered.wait(2))
        run = self.server.runs[client._run_id]
        run.last_seen = time.monotonic() - 100
        self.wait_until(lambda: run.closed)
        self.assertIs(self.server.runs[client._run_id], run)
        duplicate = client._request('/runs/' + client._run_id,
            dict(prompt=TASK, robot_id='yam-1'))
        self.assertEqual(duplicate['creation']['status'], 'running')
        self.assertEqual(self.created, [TASK])
        self.release.set()
        self.wait_until(lambda: self.native.closed)
        self.assertEqual(self.native.prepared, [])
        run.last_seen = time.monotonic() - 100
        self.wait_until(lambda: client._run_id not in self.server.runs)

    def test_lost_create_response_closes_without_duplicate_construction(self):
        client = self.policy()
        real = client._request
        creations = []

        def lost(path, body=None):
            result = real(path, body)
            if path == '/runs/' + client._run_id and body is not None:
                creations.append(path)
                raise RuntimeError('Lost create response')
            return result

        client._request = lost
        with self.assertRaisesRegex(RuntimeError, 'Lost create response'):
            client.prepare_before_session(TASK)
        self.assertTrue(self.entered.wait(2))
        self.assertTrue(self.server.runs[client._run_id].closed)
        self.release.set()
        self.wait_until(lambda: self.native.closed)
        self.assertEqual(len(creations), 1)
        self.assertEqual(self.created, [TASK])
        self.assertEqual(self.native.prepared, [])

    def test_pending_factory_does_not_block_another_run(self):
        blocked_factory = self.server.factory
        other = PacketPolicy()

        def factory(prompt):
            if prompt == 'Independent fixture':
                self.created.append(prompt)
                return other
            return blocked_factory(prompt)

        self.server.factory = factory
        client = self.policy()
        thread, errors = self.start_preparation(client)
        independent = self.policy()
        started = time.monotonic()
        independent.prepare_before_session('Independent fixture')
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(other.prepared, ['Independent fixture'])
        client.close_transport()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.release.set()
        self.wait_until(lambda: self.native.closed)
        self.assertEqual(self.created, [TASK, 'Independent fixture'])
        self.assertEqual(self.native.prepared, [])

    def test_creation_failure_is_reported_without_private_exception_body(self):
        def broken(prompt):
            self.created.append(prompt)
            raise OSError('private-secret-must-not-be-published')

        self.server.factory = broken
        client = self.policy()
        with self.assertRaisesRegex(RuntimeError, 'worker call failed') as caught:
            client.prepare_before_session(TASK)
        state = client._request('/runs/' + client._run_id)
        self.assertEqual(state['creation']['status'], 'failed')
        self.assertEqual(state['creation']['error_type'], 'OSError')
        self.assertNotIn('private-secret', json.dumps(state) + str(caught.exception))
        self.assertEqual(self.created, [TASK])
        self.assertEqual(self.native.prepared, [])


if __name__ == '__main__':
    unittest.main()
