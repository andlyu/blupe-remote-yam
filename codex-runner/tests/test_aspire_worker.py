"""Real HTTP worker/controller integration against simulation only."""
import json
import threading
import time
import unittest
from urllib import request, error

from remote_yam.aspire_worker import AspireWorkerServer, RemoteAspirePolicy
from remote_yam.controller import RunnerController
from remote_yam.providers import PolicyComplete
from remote_yam.session import MockSessionAPI

TOKEN = 'fixture-service-token-'*3
TASK = 'Place the green block on the towel.'


class PacketPolicy:
    def __init__(self):
        self.prepared = []
        self.observations = []
        self.completions = []
        self.closed = False
        self.reviewed = False
        self.block = False
        self.started = threading.Event()

    def public_config(self):
        return dict(provider='codex', model='gpt-6-astra', phase='fixture',
            launch_route=dict(actual_policy='aspire', requested_policy='aspire', prompt=TASK),
            private_field=TOKEN)

    def prepare_before_session(self, prompt):
        self.prepared.append(prompt)
        self.interaction_sink('model_progress', 'Saved program selected')

    def validate_session_admission(self, api):
        self.simulation = isinstance(api, MockSessionAPI)

    def build_trajectory(self, prompt, observation, first_step_id):
        self.started.set()
        if self.block:
            while not self.cancelled():
                time.sleep(.01)
            raise RuntimeError('Fixture cancelled')
        if self.completions:
            raise PolicyComplete('Done')
        self.observations.append(observation)
        fresh = self.refresh_observation()
        assert fresh['episode_id'] == observation['episode_id']
        assert fresh['lease_id'] == observation['lease_id']
        assert fresh['step_id'] == first_step_id
        return [dict(step_id=first_step_id, left_joints_deg=observation['left_joints_deg'],
            right_joints_deg=observation['right_joints_deg'],
            left_gripper=observation['left_gripper'], right_gripper=observation['right_gripper'])]

    def trajectory_completed(self, observation, count):
        self.completions.append((observation, count))

    def trajectory_failed(self, *args, **kwargs):
        self.failure = (args, kwargs)

    def review_after_session_stop(self):
        self.reviewed = True

    def close_transport(self):
        self.closed = True


class CountingAPI(MockSessionAPI):
    def __init__(self):
        super().__init__()
        self.admissions = 0

    def create_session(self, *args, **kwargs):
        self.admissions += 1
        return super().create_session(*args, **kwargs)

    def get_session(self, session_id):
        result = super().get_session(session_id)
        result.update(latest_observation_step=0, lease_id=self._record(session_id)['lease_id'])
        return result


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.native = PacketPolicy()
        self.created = []
        def factory(prompt):
            self.created.append(prompt)
            return self.native
        self.server = AspireWorkerServer(('127.0.0.1',0), TOKEN, 'yam-1', factory)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def policy(self, **kwargs):
        value = RemoteAspirePolicy(self.origin,TOKEN,robot_id='yam-1',poll_s=.01,**kwargs)
        self.addCleanup(value.close_transport)
        return value

    def test_native_task_metadata_crosses_hosted_worker_snapshot(self):
        original = self.native.public_config
        progress = dict(task=TASK, updates=[dict(happened='Native plan passed.',
            changed='Complete candidate checked.', next_action='Execute admitted plan.')],
            lineage=dict(authorship=dict(generated_by_codex=True, mode='reuse')),
            attempts=[dict(id='aspire-1', status='running')])
        self.native.public_config = lambda: dict(original(), task_progress=progress)
        client = self.policy()
        client.prepare_before_session(TASK)
        projected = client.public_config()['task_progress']
        self.assertEqual(projected['task'], progress['task'])
        self.assertEqual(projected['updates'], progress['updates'])
        self.assertEqual(projected['lineage']['authorship'], progress['lineage']['authorship'])
        self.assertEqual(projected['attempts'][0]['id'], 'aspire-1')
        self.assertEqual(projected['attempts'][0]['status'], 'running')
        progress['outcome'] = dict(status='UNVERIFIED', success=False, reason='After parking did not confirm.')
        client._accept(client._request('/runs/'+client._run_id))
        self.assertEqual(client.public_config()['task_progress']['outcome']['success'], False)
        self.assertEqual(self.native.observations, [])

    def test_one_public_queue_lease_with_refresh_completion_and_review(self):
        client = self.policy()
        self.assertEqual(self.created, [])  # Health does not create a runtime.
        session = CountingAPI()
        controller = RunnerController(session)
        controller.update_monitor_observation(session.get_robot_observation('yam-1'))
        controller.join_and_run(client,TASK)
        controller._worker.join(4)
        self.assertFalse(controller._worker.is_alive(),controller.status())
        self.assertIsNone(controller.status()['error'],controller.status())
        self.assertEqual(session.admissions,1)
        self.assertEqual(self.created,[TASK])
        self.assertEqual(self.native.prepared,[TASK])
        self.assertTrue(self.native.simulation)
        self.assertEqual(len(session.trajectory_log),1)
        self.assertEqual(len(self.native.completions),1)
        self.assertTrue(self.native.reviewed)
        observation=self.native.observations[0]
        self.assertTrue(observation['lease_id'])
        self.assertTrue(observation['episode_id'])
        wire=json.dumps(observation)+json.dumps(client.public_config())
        self.assertNotIn('session_capability',wire)
        self.assertNotIn(TOKEN,wire)
        self.assertFalse(hasattr(client,'run_session'))

    def test_stop_during_inference_never_dispatches_late_packet(self):
        self.native.block=True
        client=self.policy()
        session=CountingAPI()
        controller=RunnerController(session)
        controller.update_monitor_observation(session.get_robot_observation('yam-1'))
        controller.join_and_run(client,TASK)
        self.assertTrue(self.native.started.wait(2))
        controller.stop()
        controller._worker.join(3)
        self.assertEqual(session.admissions,1)
        self.assertEqual(session.trajectory_log,[])
        self.assertTrue(self.native.cancelled())

    def test_credentials_robot_scope_and_method_allowlist(self):
        with self.assertRaises(RuntimeError):
            RemoteAspirePolicy(self.origin,'wrong-'*8,robot_id='yam-1')
        with self.assertRaises(ValueError):
            RemoteAspirePolicy(self.origin,TOKEN,robot_id='another-robot')
        client=self.policy();client.prepare_before_session(TASK)
        with self.assertRaises(RuntimeError):
            client._call('create_session','forbidden')
        self.assertEqual(self.created,[TASK])
        self.assertEqual(self.native.observations,[])
        with self.assertRaises(ValueError):
            RemoteAspirePolicy('http://public.example',TOKEN,robot_id='yam-1')

    def test_lost_worker_response_is_cancelled_without_replaying(self):
        client=self.policy();client.prepare_before_session(TASK)
        real=client._request
        calls=[]
        def lost(path,body=None):
            calls.append(path)
            if body and body.get('method')=='build_trajectory':
                real(path,body)
                raise RuntimeError('Lost response')
            return real(path,body)
        client._request=lost
        with self.assertRaises(RuntimeError):
            client.build_trajectory(TASK,{},0)
        self.assertEqual(sum('/calls/' in p for p in calls),1)
        self.assertTrue(self.native.cancelled())

    def test_worker_expires_disconnected_client_and_closes_vision(self):
        client=self.policy();client.prepare_before_session(TASK)
        run=self.server.runs[client._run_id]
        run.last_seen=time.monotonic()-100
        deadline=time.monotonic()+2
        while not self.native.closed and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertTrue(self.native.closed)
        self.assertTrue(run.cancelled.is_set())

    def test_progress_snapshot_never_rolls_back_after_a_slow_heartbeat(self):
        client=self.policy()
        client._accept(dict(revision=2,config={'phase':'complete'}))
        client._accept(dict(revision=1,config={'phase':'planning'}))
        self.assertEqual(client.public_config()['phase'],'complete')

    def test_same_call_id_is_idempotent_and_cannot_change_arguments(self):
        client=self.policy();client.prepare_before_session(TASK)
        path='/runs/'+client._run_id+'/calls/'+'a'*32
        body=dict(method='validate_session_admission',args=[False],kwargs={})
        first=client._request(path,body)
        second=client._request(path,body)
        self.assertEqual(len(self.server.runs[client._run_id].calls),2)
        with self.assertRaises(RuntimeError):
            client._request(path,dict(body,args=[True]))


if __name__=='__main__':
    unittest.main()
