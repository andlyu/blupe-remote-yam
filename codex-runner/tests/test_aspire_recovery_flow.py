"""No hardware: real local launch/controller lifecycle with fixture harnesses."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from local_playground import LocalPlayground
from playground import RequestError
from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.aspire_executable_skills import ExecutableSkillLibrary
from remote_yam.aspire_lineage_catalog import LineageCatalog
from remote_yam.aspire_recovery_flow import RecoveryManager,read,task_resolution
from remote_yam.session import MockSessionAPI
from test_aspire_launch_routing import saved_library,TASK


@pytest.fixture
def station(tmp_path):
    saved_library(tmp_path)
    library=ExecutableSkillLibrary(tmp_path/'manifest.json',repairs=tmp_path/'skills/.repaired-programs')
    selected=library.select(TASK)
    root=tmp_path/'runs'/('aspire-'+'a'*32);root.mkdir(parents=True)
    source=selected['response']['source'];digest=hashlib.sha256(source.encode()).hexdigest()
    (root/'task-completion.json').write_text(json.dumps(dict(task=TASK,status='UNVERIFIED',success=False,reason='centered_over_chip unknown')))
    (root/'coding-loop.json').write_text(json.dumps(dict(task=TASK,model='fixture',attempts=[],recovery_attempts=[
        dict(id='astra-repair-1',parent_attempt_id='aspire-1',mode='offline_code_repair',status='PLAN_VALIDATED',source_sha256=digest)])))
    config=dict(automatic_recovery=True,execution_environment='local',code_revision_limit=4,
        skill_directory=str(tmp_path/'skills'),run_directory=str(tmp_path/'runs'),robot_id='fixture')
    api=MockSessionAPI(auto_activate=True)
    app=LocalPlayground(public_origin='http://127.0.0.1:8792',session_api=None,camera_origin='http://127.0.0.1:8089',
        development=True,local_codex=True,api_factory=lambda:api,share_conversation=False,robot_id='fixture')
    app.aspire_station_config=config
    app.resolve_api_depth_launch=lambda task:dict(program=library.select(task),route=dict(requested_policy='aspire',actual_policy='aspire',message='fixture'))
    requests=[];feedbacks=[];options=dict(occluded=False,verification=True,cancel=False)
    observe=Mock(return_value=dict(source='hardware',mode='DISABLED',safety={'ok':True}))
    def factory(*args,**kwargs):
        def harness(**request):
            requests.append(request);folder=Path(request['directory']);folder.mkdir(parents=True,exist_ok=True)
            if options['cancel']:policy.cancelled=lambda:True
            if request['mode']=='observe':
                snapshot=folder/'scene';snapshot.mkdir();(snapshot/'snapshot.json').write_text(json.dumps(dict(fresh_capture=len(requests))))
                return dict(status='OBSERVED',snapshot=str(snapshot),context={},images={},physical_motion_calls=0)
            if request['mode']=='plan' and 'recovery-preflight' in str(folder):
                assert request['snapshot'].is_dir() and request['snapshot'].parent.parent==folder.parent
                assert json.loads((request['snapshot']/'snapshot.json').read_text())['fresh_capture']==len(requests)-1
                assert isinstance(json.loads(request['queries'].read_text()),dict)
            task=dict(steps=[dict(stage='correct',kind='move',arguments={})],geometry={})
            if options['occluded']:
                task.update(observation_only_repair=True,decision='Chip occluded',geometry=dict(target_measurement=None,target_identity='green chip'))
            (folder/'task_definition.json').write_text(json.dumps(task))
            if options.get('plan_failure') and request['mode']=='plan' and not options.get('plan_failed'):
                options['plan_failed']=True
                return dict(status='PLAN_FAILED',planning_success=False,reason='measured approach IK failed',physical_motion_calls=0)
            if options.get('live_failure') and request['mode']=='execute' and not options.get('live_failed'):
                options['live_failed']=True
                return dict(status='PLAN_FAILED',planning_success=False,reason='fresh leased approach IK failed',physical_motion_calls=0)
            if options.get('ambiguous') and request['mode']=='execute':
                policy._saved_motion_requests=1
                return dict(status='HARNESS_ERROR',planning_success=False,reason='writer exited after submission',physical_motion_calls=0)
            if options.get('stationary'):
                (folder/'full_sequence_plan.json').write_text(json.dumps(dict(stages=[dict(stage='correct',native_noop=True)])))
            if request['mode']=='evaluate':
                options['evaluations']=options.get('evaluations',0)+1
                image=folder/'top.png';image.write_bytes(b'fixture parked image')
                return dict(status='EVALUATION_ONLY',success=options['verification'],physical_motion_calls=0,
                    scene=dict(images={'top':str(image)},context=dict(measurement_number=options['evaluations'],
                        measured_observation=dict(source='hardware',mode='DISABLED'))),
                    placement_evidence=dict(evidence=dict(checks=dict(centered_over_chip=options['verification'],
                        bottom_at_chip=True if options['verification'] else None))))
            return dict(status='PLAN_ONLY' if request['mode']=='plan' else ('FAILED' if options.get('native_failure') else 'UNVERIFIED'),planning_success=True,
                success=False,physical_motion_calls=0 if request['mode']=='plan' else 13)
        options['factory_count']=options.get('factory_count',0)+1
        def generate(**request):
            feedbacks.append(request['feedback'])
            if options.get('give_up'):
                return dict(action='give_up',source='',summary='No measurable corrective target',lesson='Preserve uncertainty',queries=[])
            source=request['feedback']['previous_source']
            if not options.get('unchanged'):
                import re
                source=re.sub(r"return \{'success': False[^\n]*", "return {'success': False, 'measurement_revision': "+str(len(feedbacks))+"}",source)
            elif options.get('cosmetic'):source+='\n# Cosmetic comment is not a correction\n'
            return dict(action='rerun' if options.get('justified_rerun') else 'program',source=source,
                summary='Fresh measurable correction justifies a rerun' if options.get('justified_rerun') else 'Diagnosed latest measured failure '+str(len(feedbacks)),
                lesson='Use fresh checks',queries=selected['response']['queries'])
        policy=AspireCodexPolicy(harness=harness,calibration={},robot_id='fixture',task=TASK,
            directory=tmp_path/'runs'/('aspire-'+'b'*31+str(options['factory_count'])),instructions='',skill_directory=tmp_path/'skills',
            allow_hardware=False,selected_executable=library.select(TASK),execution_environment='local',generator=generate)
        recorder=Mock();recorder.finish_after_stop.return_value=dict(status='PARKING_OBSERVED',physical_commands=0)
        policy.configure_postpark_review('https://fixture.invalid',observe,recorder_factory=Mock(return_value=recorder))
        policy._review_recorder=recorder
        return policy
    app.api_depth_provider_factory=factory
    _,visitor=app.new_visitor()
    before={name:(root/name).read_bytes() for name in ('coding-loop.json','task-completion.json')}
    with patch('remote_yam.subscription_setup.verify_subscription',return_value=dict(ready=True,setup_prompt='',label='fixture')):
        yield SimpleNamespace(app=app,root=root,visitor=visitor,api=api,requests=requests,feedbacks=feedbacks,options=options,observe=observe,config=config,before=before)
    for item in app.visitors.values():item.close()


def wait(s):
    s.visitor.controller._worker.join(8)
    assert not s.visitor.controller.busy()
    s.app.recovery_manager.tick()
    return read(s.root/'recovery-state.json')


def test_runnable_repair_uses_one_ordinary_queue_home_fresh_execute_and_after_parking_verification(station):
    s=station;s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='verified' and state['retry_count']==1
    assert len(s.api.create_requests)==1
    assert [r['mode'] for r in s.requests]==['observe','plan','execute','evaluate']
    assert s.requests[0].get('bridge_socket') is None
    assert s.requests[2]['bridge_socket']
    assert all((s.root/name).read_bytes()==data for name,data in s.before.items())
    child=s.visitor.controller._provider
    assert child.repair_outcomes is True and child.execution_environment=='local'
    assert s.feedbacks==[]
    assert child.public_config()['task_progress']['resolution']['state']=='verified'
    s.app.recovery_manager.tick()
    assert len(s.api.create_requests)==1
    with pytest.raises(RequestError,match='already checked'):s.app.recovery_manager.start(s.visitor,s.root)
    episode=LineageCatalog(s.config).episode(dict(task_id='original',task=TASK,policy_directory=str(s.root),
        coding_loop=str(s.root/'coding-loop.json'),receipt=str(s.root/'task-completion.json')))
    assert episode['resolution']['state']=='verified'
    assert any(a.get('id')=='live-retry-1' and a['parent_attempt_id']=='astra-repair-1' for a in episode['attempts'])


def test_occluded_stationary_plan_blocks_before_queue_and_names_action(station):
    s=station;s.options['occluded']=True;s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='action_needed' and state['retry_count']==0
    assert 'rim is visible' in state['next_action'] and 'green chip' in state['next_action']
    assert s.api.create_requests==[]
    assert [r['mode'] for r in s.requests]==['observe','plan']
    assert all((s.root/name).read_bytes()==data for name,data in s.before.items())


def test_unflagged_stationary_native_plan_does_not_admit_a_physical_retry(station):
    s=station;s.options['stationary']=True
    s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='unresolved' and state['retry_count']==0
    assert state['title']=='No corrective move was planned'
    assert s.api.create_requests==[] and [r['mode'] for r in s.requests]==['observe','plan']


def test_retry_unverified_repairs_offline_but_physical_limit_is_terminal(station):
    s=station;s.options['verification']=False;s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='unresolved' and state['retry_count']==1
    assert 'automatic retry limit is reached' in state['next_action']
    for _ in range(3):s.app.recovery_manager.tick()
    assert len(s.api.create_requests)==1 and len(s.requests)==6 and len(s.feedbacks)==1


def test_cancel_between_preflight_and_admission_never_joins(station):
    s=station;s.options['cancel']=True;s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='cancelled' and s.api.create_requests==[]


def test_active_worker_stop_marker_changed_source_and_unsafe_station_fence_retry(station):
    s=station
    with patch.object(s.visitor.controller,'busy',return_value=True):
        with pytest.raises(RequestError,match='active'):s.app.recovery_manager.start(s.visitor,s.root)
    assert not (s.root/'recovery-state.json').exists()
    s.app.resolve_api_depth_launch=lambda task:dict(program=None)
    with pytest.raises(RequestError,match='exact repaired source'):s.app.recovery_manager.start(s.visitor,s.root)
    assert not (s.root/'recovery-state.json').exists()
    (s.root/'automatic-recovery-cancelled').touch()
    with pytest.raises(RequestError,match='already checked'):s.app.recovery_manager.start(s.visitor,s.root)
    assert s.api.create_requests==[]


def test_station_change_before_admission_blocks_and_no_reset_is_invented(station):
    s=station;s.observe.side_effect=[dict(source='hardware',mode='DISABLED',safety={'ok':True}),
        dict(source='hardware',mode='API_ACTIVE',safety={'ok':True})]
    s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='unresolved' and s.api.create_requests==[]
    assert 'station changed' in state['detail']


def test_parked_uninitialized_queue_ready_station_allows_read_only_preflight_before_home(station):
    s=station;s.observe.return_value=dict(source='hardware',mode='DISABLED',queue_ready=True,
        safety=dict(ok=False,estop_engaged=False,reason='hardware_not_initialized'))
    s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='verified' and state['retry_count']==1
    assert [r['mode'] for r in s.requests]==['observe','plan','execute','evaluate']
    assert len(s.api.create_requests)==1
    assert read(Path(state['retry_directory'])/'recovery-preflight/station-before.json')['queue_ready'] is True


def test_station_fault_never_auto_rechecks_and_explicit_recheck_preserves_limit_and_attempt(station):
    s=station;s.observe.return_value=dict(source='hardware',mode='DISABLED',queue_ready=True,
        safety=dict(ok=False,estop_engaged=True,reason='emergency_stop'))
    s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='action_needed' and 'emergency_stop' in state['detail']
    assert s.requests==[] and s.api.create_requests==[]
    s.app.recovery_manager.tick();assert s.requests==[]
    s.observe.return_value=dict(source='hardware',mode='DISABLED',safety=dict(ok=True))
    s.visitor.last_launch=-float('inf')
    s.app.recovery_manager.start(s.visitor,s.root,limit=3);state=wait(s)
    assert state['state']=='verified' and state['retry_limit']==1 and state['retry_count']==1
    assert len(state['attempts'])==2 and state['attempts'][0]['status']=='action_needed'
    assert state['attempts'][1]['parent_attempt_id']=='live-retry-1'
    assert state['attempts'][1]['id']=='live-retry-1-check-2'


def test_second_retry_follows_actual_first_retry_after_an_explicit_station_recheck(station):
    s=station;s.options['verification']=False
    s.observe.return_value=dict(source='hardware',mode='DISABLED',safety=dict(ok=False,reason='station_fault'))
    s.app.recovery_manager.start(s.visitor,s.root,limit=2);wait(s)
    s.observe.return_value=dict(source='hardware',mode='DISABLED',safety=dict(ok=True))
    s.visitor.last_launch=-float('inf');s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='retry_pending'
    s.visitor.last_launch=-float('inf');s.app.recovery_manager.tick();state=wait(s)
    assert state['retry_count']==2 and state['state']=='unresolved'
    assert state['attempts'][-1]['parent_attempt_id']=='live-retry-1-check-2-astra-repair-1'
    assert len(s.api.create_requests)==2


def test_restart_keeps_unknown_attempt_unresolved_without_replaying(station):
    s=station;(s.root/'recovery-state.json').write_text(json.dumps(dict(state='recovering',retry_count=1)))
    s.app.recovery_manager.tick()
    assert read(s.root/'recovery-state.json')['state']=='unresolved'
    assert s.api.create_requests==[]


def test_legacy_station_gate_failure_offers_explicit_recheck_without_replaying_on_restart(station):
    s=station;(s.root/'recovery-state.json').write_text(json.dumps(dict(state='unresolved',retry_count=0,retry_limit=2,
        detail='The station is not confirmed stopped and safe for fresh recovery observation.',attempts=[])))
    s.app.recovery_manager.tick();state=read(s.root/'recovery-state.json')
    assert state['state']=='action_needed' and state['preflight_recheck'] is True and state['retry_limit']==2
    assert s.api.create_requests==[] and s.requests==[]


def test_plan_validated_alone_cannot_promote_physical_task(station):
    answer=task_resolution(station.root)
    assert answer['state']=='unresolved' and answer['recovery_available'] is True
    assert 'fresh geometry' in answer['next_action']


@pytest.mark.parametrize('limit',[0,2,3])
def test_effective_configured_limit_is_enforced_and_retained(station,limit):
    s=station;s.options['verification']=False
    s.app.recovery_manager.start(s.visitor,s.root,limit=limit)
    if limit==0:
        assert s.api.create_requests==[]
        assert read(s.root/'recovery-state.json')['retry_limit']==0
        return
    for number in range(1,limit+1):
        state=wait(s)
        assert state['retry_limit']==limit and state['retry_count']==number
        if number<limit:
            assert state['state']=='retry_pending'
            s.visitor.last_launch=-float('inf');s.app.recovery_manager.tick()
    assert state['state']=='unresolved' and len(s.api.create_requests)==limit
    assert len(state['attempts'])==limit
    assert state['attempts'][-1]['parent_attempt_id']=='live-retry-'+str(limit-1)+'-astra-repair-1'
    assert [r['mode'] for r in s.requests]==['observe','plan','execute','evaluate','observe','plan']*limit
    for _ in range(2):s.app.recovery_manager.tick()
    assert len(s.api.create_requests)==limit


@pytest.mark.parametrize('invalid',[-1,4,1.5,'2',True,None])
def test_invalid_limit_rejected_before_any_recovery_work(station,invalid):
    s=station
    with pytest.raises(RequestError,match='whole number'):
        s.app.launch(s.visitor,dict(automatic_retry_limit=invalid))
    assert s.api.create_requests==[] and not (s.root/'recovery-state.json').exists()


def test_recorded_run_limit_cannot_change_just_by_editing_next_submission(station):
    s=station;(s.root/'recovery-settings.json').write_text(json.dumps(dict(retry_limit=0)))
    s.app.recovery_manager.start(s.visitor,s.root,limit=3)
    assert read(s.root/'recovery-state.json')['retry_limit']==0 and s.api.create_requests==[]


def test_successive_failures_repair_latest_source_checks_images_and_state_without_budget_reset(station):
    s=station;s.options['verification']=False
    loop=read(s.root/'coding-loop.json');loop.update(coding_requests=1,code_revision_limit=4)
    (s.root/'coding-loop.json').write_text(json.dumps(loop))
    original=(s.root/'coding-loop.json').read_bytes()
    s.app.recovery_manager.start(s.visitor,s.root,limit=3)
    for number in range(1,4):
        state=wait(s)
        assert state['coding_requests']==number+1 and state['code_revision_limit']==4
        actual=[r for r in s.requests if r['mode']=='execute'][-1]
        feedback=s.feedbacks[-1]
        assert feedback['previous_source']==actual['program'].read_text()
        assert feedback['original_source_sha256']==hashlib.sha256(feedback['previous_source'].encode()).hexdigest()
        assert feedback['failed_checks']==['centered_over_chip','bottom_at_chip']
        assert feedback['measured_state']['measurement_number']==number
        assert feedback['postpark_evaluation']['placement_evidence']['evidence']['checks']['bottom_at_chip'] is None
        assert feedback['review_images']==[str(actual['directory'].parent.parent/'postpark-evaluation/top.png')]
        assert Path(feedback['latest_live_task_definition_path']).parent==actual['directory']
        assert feedback['latest_live_task_definition']['steps'][0]['stage']=='correct'
        if number<3:
            assert state['state']=='retry_pending'
            s.visitor.last_launch=-float('inf');s.app.recovery_manager.tick()
    assert state['state']=='unresolved' and state['retry_count']==3 and len(s.feedbacks)==3
    sources=[r['program'].read_text() for r in s.requests if r['mode']=='execute']
    assert len(set(sources))==3 and s.feedbacks[1]['previous_source']==sources[1]
    assert state['attempts'][2]['parent_attempt_id']=='live-retry-2-astra-repair-1'
    assert (s.root/'coding-loop.json').read_bytes()==original
    episode=LineageCatalog(s.config).episode(dict(task_id='original',task=TASK,policy_directory=str(s.root),
        coding_loop=str(s.root/'coding-loop.json'),receipt=str(s.root/'task-completion.json')))
    repair=next(a for a in episode['attempts'] if a['id']=='live-retry-2-astra-repair-1')
    assert repair['parent_attempt_id']=='live-retry-2-aspire-1'


def test_unchanged_code_rerun_is_explicitly_justified_and_distinct_from_a_code_fix(station):
    s=station;s.options.update(verification=False,unchanged=True,justified_rerun=True)
    s.app.recovery_manager.start(s.visitor,s.root,limit=2);state=wait(s)
    assert state['state']=='retry_pending'
    child=s.visitor.controller._provider
    assert child._recovery_attempts[-1]['status']=='RERUN_VALIDATED'
    assert child._recovery_attempts[-1]['mode']=='offline_rerun_diagnosis'
    assert child._recovery_attempts[-1]['rerun_reason']=='Fresh measurable correction justifies a rerun'
    assert child._recovery_attempts[-1]['source_changed'] is False
    selected=s.app.resolve_api_depth_launch(TASK)['program']
    assert selected['provenance']['selection']=='reused_justified_rerun_program'
    assert selected['provenance']['rerun_reason']=='Fresh measurable correction justifies a rerun'
    s.options['verification']=True;s.visitor.last_launch=-float('inf');s.app.recovery_manager.tick();state=wait(s)
    assert state['state']=='verified' and len(s.feedbacks)==1 and len(s.api.create_requests)==2


@pytest.mark.parametrize('cosmetic',[False,True])
def test_identical_or_cosmetic_program_is_no_progress_and_never_a_new_fix(station,cosmetic):
    s=station;s.options.update(verification=False,unchanged=True,cosmetic=cosmetic)
    s.app.recovery_manager.start(s.visitor,s.root,limit=3);state=wait(s)
    assert state['state']=='unresolved' and state['title']=='No justified recovery candidate'
    record=s.visitor.controller._provider._recovery_attempts[-1]
    assert record['status']=='NO_PROGRESS' and record['effective_program_changed'] is False
    assert 'not a new fix' in record['reason']
    assert len(s.api.create_requests)==1 and len(s.feedbacks)==1
    s.app.recovery_manager.tick();assert len(s.api.create_requests)==1


def test_known_failed_physical_outcome_can_repair_when_parking_and_command_state_are_known(station):
    s=station;s.options.update(verification=False,native_failure=True)
    s.app.recovery_manager.start(s.visitor,s.root,limit=2);state=wait(s)
    assert state['state']=='retry_pending' and state['retry_count']==1
    assert s.feedbacks[0]['native_result']['status']=='FAILED'
    assert s.feedbacks[0]['native_result']['physical_motion_calls']==13


def test_zero_physical_retries_still_allows_corrected_first_motion_after_initial_code_failure(station):
    s=station
    completion=read(s.root/'task-completion.json')
    completion.update(pre_motion_failure=True,native_result=dict(status='PLAN_FAILED',physical_motion_calls=0))
    (s.root/'task-completion.json').write_text(json.dumps(completion))
    s.app.recovery_manager.start(s.visitor,s.root,limit=0);state=wait(s)
    assert state['state']=='verified' and state['retry_count']==0 and state['retry_limit']==0
    assert len(s.api.create_requests)==1 and s.feedbacks==[]


def test_exhausted_coding_budget_blocks_replay_even_with_physical_budget_remaining(station):
    s=station;s.options['verification']=False
    loop=read(s.root/'coding-loop.json');loop.update(coding_requests=1,code_revision_limit=1)
    (s.root/'coding-loop.json').write_text(json.dumps(loop))
    s.app.recovery_manager.start(s.visitor,s.root,limit=3);state=wait(s)
    assert state['state']=='unresolved' and state['title']=='Task code-revision limit reached'
    assert state['coding_requests']==1 and state['retry_count']==1 and s.feedbacks==[]
    s.app.recovery_manager.tick();assert len(s.api.create_requests)==1


def test_preflight_native_failure_gets_exact_source_diagnosis_and_full_plan_before_queue(station):
    s=station;s.options['plan_failure']=True
    s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='verified' and state['coding_requests']==1
    assert [r['mode'] for r in s.requests]==['observe','plan','observe','plan','execute','evaluate']
    assert s.feedbacks[0]['reason']=='measured approach IK failed'
    assert s.feedbacks[0]['previous_source']==(Path(state['retry_directory'])/'recovery-preflight/repair.py').read_text()
    assert len(s.api.create_requests)==1


def test_live_zero_command_plan_failure_revises_then_fresh_admission_without_physical_budget_charge(station):
    s=station;s.options['live_failure']=True
    s.app.recovery_manager.start(s.visitor,s.root);state=wait(s)
    assert state['state']=='retry_pending' and state['retry_count']==0 and state['coding_requests']==1
    assert s.feedbacks[0]['native_result']['reason']=='fresh leased approach IK failed'
    s.visitor.last_launch=-float('inf');s.app.recovery_manager.tick();state=wait(s)
    assert state['state']=='verified' and state['retry_count']==1 and len(s.api.create_requests)==2


@pytest.mark.parametrize('option',['give_up','ambiguous'])
def test_no_justified_correction_or_unknown_submitted_commands_never_replay(station,option):
    s=station;s.options.update(verification=False,**{option:True})
    s.app.recovery_manager.start(s.visitor,s.root,limit=3);state=wait(s)
    assert state['state']=='unresolved'
    assert len(s.api.create_requests)==1
    if option=='ambiguous':assert s.feedbacks==[] and state['retry_count']==1
    else:assert len(s.feedbacks)==1 and state['title']=='No justified recovery candidate'
    s.app.recovery_manager.tick();assert len(s.api.create_requests)==1


@pytest.mark.parametrize('environment',[None,'web'])
def test_recovery_manager_requires_explicit_local_environment(station,environment):
    s=station;s.config['execution_environment']=environment
    with pytest.raises(RequestError,match='not enabled'):s.app.recovery_manager.start(s.visitor,s.root)
    s.app.recovery_manager.tick();assert s.requests==[] and s.api.create_requests==[]
