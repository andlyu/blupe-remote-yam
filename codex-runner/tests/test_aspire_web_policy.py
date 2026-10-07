"""Approved web branches; HTTP tests use only simulated sessions and fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from remote_yam.aspire_web_policy import configure_web_policy, web_runtime_config
from remote_yam.providers import PolicyComplete
from remote_yam.aspire_worker import AspireWorkerServer, RemoteAspirePolicy

TOKEN = 'fixture-web-token-'*3
TASK = 'Pick up the green block and place it on the green towel.'
SOURCE = "def build_task(tools):\n return {'steps': []}\ndef evaluate(tools, task):\n return {'success': False}\n"


class WebPolicyTests(unittest.TestCase):
    def policy(self, result):
        policy = SimpleNamespace(_outcome=result, _saved_executable_only=True, _saved_motion_requests=0,
            _controller_failure=False, _failure=None, _recovery_attempts=[], _recovery=None,
            cancelled=lambda: False, build_trajectory=Mock(side_effect=PolicyComplete('Native finished')),
            _build_recovery=Mock(return_value=['fixture Astra packet']))
        def start(result):
            policy._recovery = policy.recovery_factory()
            policy._recovery_attempts.append(dict(parent='fixture-native-attempt', result=result))
        policy._start_recovery = Mock(side_effect=start)
        fallback = Mock(return_value=object())
        configure_web_policy(policy, fallback)
        return policy, fallback

    def test_web_settings_are_explicit_and_leave_local_config_intact(self):
        local = dict(code_revision_loop=True, automatic_recovery=True)
        web = web_runtime_config(local)
        self.assertEqual(web['execution_environment'], 'web')
        self.assertFalse(web['code_revision_loop'])
        self.assertFalse(web['automatic_recovery'])
        self.assertTrue(local['code_revision_loop'])

    def test_genuine_native_plan_failure_links_one_astra_attempt(self):
        result = dict(status='PLAN_FAILED', planning_success=False, physical_motion_calls=0)
        policy, fallback = self.policy(result)
        self.assertEqual(policy.build_trajectory(TASK, {}, 0), ['fixture Astra packet'])
        fallback.assert_called_once()
        self.assertIs(policy._recovery_attempts[0]['result'], result)
        self.assertIsNone(policy.recovery_factory)
        self.assertFalse(policy.repair_outcomes)
        self.assertFalse(policy.code_revision_loop)
        with self.assertRaises(PolicyComplete):
            policy.build_trajectory(TASK, {}, 0)
        self.assertEqual(fallback.call_count, 1)

    def test_nonplanning_and_postmotion_failures_do_not_fallback(self):
        for status in ('SCENE_CAPTURE_FAILED','HARNESS_ERROR','PROGRAM_ERROR','CANCELLED',
                       'UNVERIFIED','FAILED','SUCCESS','BLOCKED'):
            with self.subTest(status=status):
                policy, fallback = self.policy(dict(status=status, planning_success=False, physical_motion_calls=0))
                with self.assertRaises(PolicyComplete):
                    policy.build_trajectory(TASK, {}, 0)
                fallback.assert_not_called()
                policy._start_recovery.assert_not_called()

    def test_stop_controller_fault_and_dispatch_uncertainty_fence_plan_fallback(self):
        for field, value in (('_controller_failure',True),('_failure','controller fault'),
                             ('_saved_motion_requests',1),('cancelled',lambda: True)):
            with self.subTest(field=field):
                policy, fallback = self.policy(dict(status='PLAN_FAILED', planning_success=False, physical_motion_calls=0))
                setattr(policy, field, value)
                with self.assertRaises(PolicyComplete):
                    policy.build_trajectory(TASK, {}, 0)
                fallback.assert_not_called()
        for extra in (dict(physical_motion_calls=1),dict(physical_motion_calls=None),
                      dict(physical_motion_calls=False),dict(failure_kind='process'),dict(planning_success=None)):
            with self.subTest(extra=extra):
                policy, fallback = self.policy(dict(dict(status='PLAN_FAILED', planning_success=False,
                                                         physical_motion_calls=0), **extra))
                with self.assertRaises(PolicyComplete):
                    policy.build_trajectory(TASK, {}, 0)
                fallback.assert_not_called()

    def transport(self, prepare):
        policy = SimpleNamespace(cancelled=lambda: False, prepare_before_session=prepare,
            public_config=lambda: dict(provider='codex', model='gpt-6-astra'), close_transport=Mock())
        created=[]
        def factory(prompt):
            created.append(prompt)
            return policy
        server=AspireWorkerServer(('127.0.0.1',0),TOKEN,'fixture',factory)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        remote=RemoteAspirePolicy('http://127.0.0.1:'+str(server.server_port),TOKEN,
                                  robot_id='fixture',poll_s=.02,timeout_s=15)
        self.addCleanup(remote.close_transport)
        return policy,remote,server,created

    def test_slow_preparation_is_polled_without_http_timeout_or_replay(self):
        calls=[]
        def prepare(prompt):
            calls.append(prompt);time.sleep(5.2)
        policy,remote,server,created=self.transport(prepare)
        started=time.monotonic();remote.prepare_before_session(TASK)
        self.assertGreaterEqual(time.monotonic()-started,5.2)
        self.assertEqual(calls,[TASK]);self.assertEqual(created,[TASK])
        self.assertEqual(len(server.runs[remote._run_id].calls),1)

    def test_stop_cancels_slow_preparation_without_replay(self):
        started,stop=threading.Event(),threading.Event();finished=[];errors=[]
        def prepare(prompt):
            started.set()
            while not policy.cancelled():time.sleep(.01)
            finished.append('cancelled')
            raise RuntimeError('Stopped before queue admission')
        policy,remote,server,created=self.transport(prepare);remote.cancelled=stop.is_set
        def work():
            try:remote.prepare_before_session(TASK)
            except RuntimeError:errors.append('cancelled')
        thread=threading.Thread(target=work);thread.start();self.assertTrue(started.wait(2));stop.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,['cancelled']);self.assertEqual(created,[TASK])
        deadline=time.monotonic()+2
        while not finished and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(finished,['cancelled'])


try:
    from remote_yam.aspire_codex_policy import AspireCodexPolicy
    from remote_yam.aspire_executable_skills import ExecutableSkillLibrary
except ModuleNotFoundError:
    AspireCodexPolicy=None


@unittest.skipIf(AspireCodexPolicy is None, 'Optional native ASPIRE station package is not installed')
class NativeWebTests(unittest.TestCase):
    def test_real_http_chart_branches_keep_one_reservation_and_original_source(self):
        cases=[('PLAN_FAILED',False,0,True,None),('SCENE_CAPTURE_FAILED',False,0,False,None),
               ('HARNESS_ERROR',False,0,False,None),('UNVERIFIED',True,1,False,None),
               ('FAILED',True,1,False,None),('SUCCESS',True,1,False,None),
               ('PLAN_FAILED',False,0,False,'controller'),('PLAN_FAILED',False,0,False,'dispatch'),
               ('PLAN_FAILED',False,0,False,'stop')]
        for status,planning,motion,fallback_expected,fence in cases:
            with self.subTest(status=status,fence=fence):
                self.lifecycle(status,planning,motion,fallback_expected,fence)

    def lifecycle(self,status,planning,motion,fallback_expected,fence=None):
        from remote_yam.controller import RunnerController
        from remote_yam.session import MockSessionAPI
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);order=[]
            (root/'core.py').write_text(SOURCE)
            (root/'manifest.json').write_text(json.dumps(dict(skill='pick_place_block',version=1,source='core.py',
                source_sha256=hashlib.sha256(SOURCE.encode()).hexdigest(),development_evidence=['fixture only'],
                validation_scope='Offline fixture')))
            selected=ExecutableSkillLibrary(root/'manifest.json').select(TASK)
            self.assertIsNotNone(selected)
            bound_source=selected['response']['source']
            result=dict(status=status,planning_success=planning,success=status=='SUCCESS',physical_motion_calls=motion)
            if status=='HARNESS_ERROR':result['failure_kind']='process'
            class Session(MockSessionAPI):
                def create_session(self,*args,**kwargs):
                    order.append('queue_home');return super().create_session(*args,**kwargs)
            session=Session(auto_activate=True)
            def harness(**request):
                self.assertEqual(request['mode'],'execute');self.assertNotIn('snapshot',request)
                self.assertEqual(Path(request['program']).read_text(),bound_source)
                self.assertEqual(len(session.create_requests),1)
                order.extend(['fresh_capture','full_native_plan'])
                if fence=='controller':policy._controller_failure=True
                if fence=='dispatch':policy._saved_motion_requests=1
                if fence=='stop':policy.cancelled=lambda:True
                return result
            generator=Mock(side_effect=AssertionError('Web must never generate ASPIRE source'))
            policy=AspireCodexPolicy(harness=harness,calibration={},robot_id='fixture',task=TASK,
                directory=root/'run',instructions='',skill_directory=root/'skills',selected_executable=selected,
                generator=generator,allow_hardware=False)
            astra=Mock()
            astra.public_config.return_value=dict(model='fixture Astra')
            astra.build_trajectory.side_effect=PolicyComplete('Fixture Astra finished')
            astra._outcome=dict(status='done',summary='Fixture completion is not physical verification')
            factory=Mock(return_value=astra);configure_web_policy(policy,factory)
            server=AspireWorkerServer(('127.0.0.1',0),TOKEN,'fixture',lambda prompt:policy)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            remote=RemoteAspirePolicy('http://127.0.0.1:'+str(server.server_port),TOKEN,robot_id='fixture',poll_s=.01)
            try:
                controller=RunnerController(session,robot_id='fixture',hardware_control_enabled=False,
                                            submit_attempts=1,share_conversation=False)
                controller.join_and_run(remote,TASK);controller._worker.join(6)
                if controller._worker.is_alive():controller.stop()
                self.assertFalse(controller._worker.is_alive(),controller.status())
                self.assertEqual(order,['queue_home','fresh_capture','full_native_plan'])
                self.assertEqual(len(session.create_requests),1);self.assertEqual(session.trajectory_log,[])
                generator.assert_not_called();self.assertEqual(len(policy._attempts),1)
                original=policy._attempts[0]
                self.assertEqual(original['execution']['status'],status)
                self.assertEqual(original['lineage']['program_sha256'],hashlib.sha256(bound_source.encode()).hexdigest())
                self.assertEqual(factory.call_count,1 if fallback_expected else 0)
                self.assertFalse(policy.repair_outcomes)
                if fallback_expected:
                    linked=policy._recovery_attempts[0]
                    self.assertEqual(linked['parent_attempt_id'],'aspire-1')
                    self.assertEqual(linked['previous_source_sha256'],original['lineage']['program_sha256'])
                else:self.assertEqual(policy._recovery_attempts,[])
            finally:
                remote.close_transport();server.shutdown();server.server_close()

    def test_after_parking_verification_reports_and_finishes_without_code_repair(self):
        from test_aspire_postpark import PostparkTests
        for success in (False,True):
            with self.subTest(verified_success=success):
                fixture=PostparkTests();fixture.setUp()
                try:
                    policy,native=fixture.policy(native_success=True,evaluation_success=success)
                    original=Path(policy._attempts[-1]['directory'])/'execute/task_result.json'
                    before=original.read_bytes()
                    fallback=Mock();configure_web_policy(policy,fallback)
                    policy.generator=Mock(side_effect=AssertionError('Web may not repair ASPIRE code'))
                    server=AspireWorkerServer(('127.0.0.1',0),TOKEN,'fixture',lambda prompt:policy)
                    threading.Thread(target=server.serve_forever,daemon=True).start()
                    remote=RemoteAspirePolicy('http://127.0.0.1:'+str(server.server_port),TOKEN,robot_id='fixture',poll_s=.01)
                    try:
                        remote._created=True
                        remote._request('/runs/'+remote._run_id,dict(prompt=policy.task,robot_id='fixture'))
                        remote.review_after_session_stop()
                        outcome=remote.public_config()['task_outcome']
                        self.assertEqual(outcome['success'],success)
                        self.assertEqual(outcome['physical_commands_sent_by_review'],0)
                        self.assertEqual(original.read_bytes(),before)
                        policy.generator.assert_not_called();fallback.assert_not_called()
                        self.assertEqual(policy._recovery_attempts,[])
                        if not success:self.assertEqual(outcome['status'],'UNVERIFIED')
                    finally:remote.close_transport();server.shutdown();server.server_close()
                finally:fixture.doCleanups()


if __name__=='__main__':unittest.main()
