"""Bounded authorized live recovery through the ordinary local runner lifecycle."""
import hashlib
import json
from pathlib import Path
import re
import threading
import time


def read(path):
    try:
        value=json.loads(Path(path).read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def resolution(state,title,detail,next_action,**extra):
    return dict(state=state,title=title,detail=detail,next_action=next_action,**extra)


def retry_limit(value=1):
    if type(value) is not int or not 0<=value<=3:raise ValueError('Automatic retry limit must be a whole number from 0 to 3.')
    return value


def validated_recovery(attempt):
    return (attempt.get('mode') in ('offline_code_repair','offline_rerun_diagnosis')
        and attempt.get('status') in ('PLAN_VALIDATED','RERUN_VALIDATED'))


def stopped_for_observation(observation):
    safety=observation.get('safety') or {}
    # Parking disables and deinitializes this station. Read-only capture may
    # precede ordinary queue/Home initialization; task motion still uses the
    # controller's normal safety checks after Home.
    parked_uninitialized=(safety.get('reason')=='hardware_not_initialized'
        and safety.get('estop_engaged') is False and observation.get('queue_ready') is True)
    return (observation.get('source')=='hardware' and observation.get('mode')=='DISABLED'
        and safety.get('estop_engaged') is not True and (safety.get('ok') is True or parked_uninitialized))


def task_resolution(directory,completion=None,phase=None,repairs=()):
    directory=Path(directory)
    saved=read(directory/'recovery-state.json')
    if saved:return saved
    parent=read(directory/'recovery-parent.json').get('root')
    if parent:
        saved=read(Path(parent)/'recovery-state.json')
        if saved:return saved
    saved=read(directory/'task-resolution.json')
    if saved:return saved
    completion=completion or read(directory/'task-completion.json')
    if completion.get('success') is True:
        return resolution('verified','Task success verified','Fresh after-parking checks confirmed the task.','No action needed.')
    if phase in ('repairing_after_parking','reviewing_after_parking'):
        return resolution('recovering','Checking and repairing the task','Physical success has not been verified.','Diagnosis and validation are continuing.')
    if not completion:return None
    repairs=repairs or read(directory/'coding-loop.json').get('recovery_attempts',[])
    loop=read(directory/'coding-loop.json')
    if loop.get('code_revision_loop') and loop.get('coding_requests',0)>=loop.get('code_revision_limit',1) and not any(validated_recovery(a) for a in repairs):
        return resolution('unresolved','Task code-revision limit reached',completion.get('reason','No verified outcome was recorded.'),
            'No coding requests remain for this submission. Review its exact failure evidence before a new Run.')
    ready=any(validated_recovery(a) for a in repairs)
    limit=read(directory/'recovery-settings.json').get('retry_limit',1)
    return resolution('unresolved','Task remains unverified',completion.get('reason','Physical success was not confirmed.'),
        ('Automatic retries are disabled for this run. Review the checks before another Run.' if limit==0 else
         'A repaired program passed offline planning. Recovery must check fresh geometry before any retry.') if ready else
        'Review the failed or unknown checks before another run; no reset requirement has been established.',
        recovery_available=ready and limit>0,recovery_run_id=directory.name,retry_limit=limit)


def geometry_blocker(task,task_prompt=''):
    geometry=task.get('geometry') or {}
    target=geometry.get('target_measurement')
    if 'target_measurement' in geometry and (not isinstance(target,dict) or target.get('reliable') is not True):
        chip=geometry.get('target_identity') or 'target chip'
        block='black block' if 'black block' in task_prompt.casefold() else 'block'
        return resolution('action_needed','Expose the target chip before recovery',
            'Fresh geometry could not establish a reliable chip center; the chip is missing, occluded, or its visible rim is insufficient.',
            f'With the station stopped, ensure the {chip} is present and its rim is visible in the top camera. If the {block} covers it, move the block off the chip. Then click Run to check the scene again.')
    if task.get('observation_only_repair') is True:
        error=geometry.get('measurement_error')
        return resolution('action_needed' if error else 'unresolved','Recovery needs measurable geometry' if error else 'Task remains unverified',
            str(error or task.get('decision') or 'The repaired program selected observation only, not a corrective move.')[:1000],
            'Resolve the measurement issue shown above, then click Run to recheck. No robot retry was admitted.' if error else
            'No corrective move is justified by these measurements. Review the recorded uncertainty; a scene reset has not been established.')
    return None


class RecoveryManager:
    """Serialized with user launch/Stop; durable claims prevent duplicate retries."""
    def __init__(self,runner):
        self.runner=runner
        self.lock=threading.RLock()
        self.startup_checked=False

    def enabled(self):
        config=getattr(self.runner,'aspire_station_config',None) or {}
        return config.get('execution_environment')=='local' and config.get('automatic_recovery') is True

    def save(self,root,value):
        path=Path(root)/'recovery-state.json'
        value=dict(value,updated_at=time.time())
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)
        return value

    def cancel(self,visitor):
        policy=getattr(visitor.controller,'_provider',None)
        directory=getattr(policy,'directory',None)
        if not directory:return
        root=Path(getattr(policy,'_recovery_root',directory))
        saved=read(root/'recovery-state.json')
        if saved.get('state')=='verified' or read(root/'task-completion.json').get('success') is True:return
        (root/'automatic-recovery-cancelled').touch()
        if saved.get('state') in ('recovering','retry_pending'):
            self.save(root,dict(read(root/'recovery-state.json'),**resolution('cancelled','Recovery stopped',
                'Stop or operator intervention cancelled automatic recovery.','Run again only when you are ready.')))

    def tick(self):
        if not self.enabled():return
        with self.lock:
            visitors=list(self.runner.visitors.values())
            if not self.startup_checked:
                self.startup_checked=True
                config=self.runner.aspire_station_config
                runs=Path(config.get('run_directory',Path(config['skill_directory']).parent/'runs'))
                owned={getattr(getattr(v.controller,'_provider',None),'_recovery_root',None) for v in visitors}
                for path in runs.glob('aspire-*/recovery-state.json'):
                    saved=read(path)
                    legacy_station=saved.get('detail')=='The station is not confirmed stopped and safe for fresh recovery observation.'
                    legacy_snapshot=(saved.get('preflight_result') or {}).get('status')=='HARNESS_ERROR' and 'Agent planning requires a recorded snapshot' in (saved.get('preflight_result') or {}).get('reason','')
                    if (saved.get('state')=='unresolved' and saved.get('retry_count')==0
                            and (legacy_station or legacy_snapshot)
                            and not (path.parent/'automatic-recovery-cancelled').exists()):
                        # Preserve the failed preflight in its attempt history.
                        # Expose an explicit recheck, never replay it on restart.
                        self.save(path.parent,dict(saved,**resolution('action_needed','Recheck the stopped station',
                            'The previous read-only check stopped before geometry capture. No robot session was admitted.',
                            'Check the scene again. Ordinary queue/Home initialization remains required before task motion.',
                            preflight_recheck=True,recovery_available=True,recovery_run_id=path.parent.name)))
                    if saved.get('state') in ('recovering','retry_pending') and str(path.parent) not in owned:
                        self.save(path.parent,dict(saved,**resolution('unresolved','Recovery was interrupted',
                            'The launcher restarted before recovery recorded a final outcome.',
                            'Review the saved attempt before another Run. Unknown command state will not be replayed.')))
            for visitor in visitors:
                policy=getattr(visitor.controller,'_provider',None)
                root=getattr(policy,'_recovery_root',None)
                if root and not visitor.controller.busy():self.finish(visitor,policy,Path(root))
            if any(v.controller.busy() or v.controller.status()['status'] in {'preparing','queued','running'} for v in visitors):return
            for visitor in visitors:
                policy=getattr(visitor.controller,'_provider',None)
                root=getattr(policy,'_recovery_root',None)
                if root and read(Path(root)/'recovery-state.json').get('state')=='retry_pending':
                    if time.monotonic()-visitor.last_launch<5:continue
                    try:self.start(visitor,Path(root),continuation=True)
                    except Exception as exc:
                        self.save(root,dict(read(Path(root)/'recovery-state.json'),**resolution('unresolved','Recovery could not continue',str(exc)[:1000],'Review this blocker before another Run.')))
                    return
            for visitor in visitors:
                policy=getattr(visitor.controller,'_provider',None)
                if (not getattr(policy,'repair_outcomes',False) or getattr(policy,'_recovery_root',None)
                        or getattr(policy,'_controller_failure',False) or visitor.retired
                        or visitor.controller.status().get('error')):continue
                root=Path(policy.directory)
                if ((root/'recovery-state.json').exists() or (root/'automatic-recovery-cancelled').exists()
                        or not getattr(policy,'_task_completion',None) or policy._task_completion.get('success') is True):continue
                if any(validated_recovery(a) for a in policy._recovery_attempts):
                    try:self.start(visitor,root)
                    except Exception as exc:
                        if not (root/'recovery-state.json').exists():
                            self.save(root,resolution('unresolved','Recovery could not start',str(exc)[:1000],'Review the recorded failure before another run.'))
                    return

    def start(self,visitor,root,*,limit=None,continuation=False):
        from playground import HostedRunner, RequestError
        with self.lock:
            if not self.enabled():raise RequestError(409,'Automatic recovery is not enabled for this station.')
            if any(v.controller.busy() or v.controller.status()['status'] in {'preparing','queued','running'} for v in self.runner.visitors.values()):
                raise RequestError(409,'A task or review is active. Recovery will not duplicate it.')
            root=Path(root)
            previous=read(root/'recovery-state.json')
            continuing=continuation and previous.get('state')=='retry_pending' and (previous.get('initial_motion_pending')
                or previous.get('retry_count',0)<previous.get('retry_limit',0))
            recheck=(not continuation and previous.get('preflight_recheck') is True
                and previous.get('retry_count',0)==0 and previous.get('state')=='action_needed')
            if ((root/'recovery-state.json').exists() and not (continuing or recheck)) or (root/'automatic-recovery-cancelled').exists():
                raise RequestError(409,'This recovery was already checked or stopped. Follow its next action before a new Run.')
            limit=retry_limit(previous['retry_limit'] if continuing or recheck else read(root/'recovery-settings.json').get('retry_limit',1 if limit is None else limit))
            root_completion=read(root/'task-completion.json')
            initial_motion_pending=previous.get('initial_motion_pending',root_completion.get('pre_motion_failure') is True)
            if limit==0 and not initial_motion_pending:
                self.save(root,resolution('unresolved','Automatic retries disabled','This run has an automatic-retry limit of 0.',
                    'Review the outcome before another Run. Changing settings applies to the next submission.',retry_limit=0,retry_count=0))
                return dict(ok=True,status='stopped')
            candidate=Path(previous.get('candidate_directory',root)) if continuing else root
            completion=read(candidate/'task-completion.json');loop=read(candidate/'coding-loop.json')
            native=completion.get('native_result') or completion.get('result') or {}
            if (native.get('ambiguous_command_state') or native.get('failure_kind')
                    or native.get('status') in ('HARNESS_ERROR','SCENE_CAPTURE_FAILED','CANCELLED')):
                raise RequestError(409,'The previous command or harness state does not permit automatic replay.')
            repairs=[a for a in loop.get('recovery_attempts',[]) if validated_recovery(a)]
            if completion.get('success') is True or not repairs:raise RequestError(409,'No unresolved task with a validated repair is available.')
            repair=repairs[-1];task=completion['task']
            if task!=read(root/'task-completion.json').get('task'):raise RequestError(409,'Recovery candidate changed the original task.')
            selected=self.runner.resolve_api_depth_launch(task)['program']
            # A newly generated exact-task program may have no generic
            # template. Its immutable repair artifact is the candidate; never
            # substitute another library version or discard prompt conditions.
            candidate_response=None
            from .aspire_executable_skills import configured_executable_skills
            current_library=configured_executable_skills(self.runner.aspire_station_config)
            if not selected and current_library is None and not loop.get('executable_reuse') and repair.get('source'):
                source=Path(repair['source'])
                candidate_response=read(source.parent/'coding-response.json')
                if source.read_text()!=candidate_response.get('source'):candidate_response=None
            response=selected['response'] if selected else candidate_response
            if not response or hashlib.sha256(response['source'].encode()).hexdigest()!=repair['source_sha256']:
                raise RequestError(409,'The exact repaired source no longer matches this task and its current inputs.')
            # Claim before starting any work; a restart never resumes a physical
            # attempt whose command state is unknown.
            state=resolution('recovering','Checking the scene for recovery','Offline planning passed; physical task success remains unverified.',
                'Check fresh geometry and a complete native plan before queue/Home or task motion.',
                retry_count=previous.get('retry_count',0),retry_limit=limit,recovery_checks=previous.get('recovery_checks',0)+1,
                parent_attempt_id=repair['id'],source_sha256=repair['source_sha256'],task=task,attempts=previous.get('attempts',[]),
                coding_requests=previous.get('coding_requests',loop.get('coding_requests',0)),
                initial_motion_pending=initial_motion_pending,
                code_revision_limit=previous.get('code_revision_limit',loop.get('code_revision_limit',self.runner.aspire_station_config.get('code_revision_limit',4))))
            number=state['retry_count']+(0 if initial_motion_pending else 1)
            identity=('live-plan-recovery-'+str(state['recovery_checks'])) if initial_motion_pending else 'live-retry-'+str(number)
            if any(a['id']==identity or a['id'].startswith(identity+'-check-') for a in state['attempts']):identity+='-check-'+str(state['recovery_checks'])
            if continuing or recheck:self.save(root,state)
            else:
                with (root/'recovery-state.json').open('x') as stream:json.dump(state,stream)
            def attach(policy):
                if not selected:
                    from .aspire_codex_policy import validate_program
                    validate_program(response)
                    policy.validated_program=response;policy.initialize_before_observation=True
                    policy._validated_lineage=read(Path(repair['source']).parent/'lineage.json')
                if hashlib.sha256(policy.validated_program['source'].encode()).hexdigest()!=state['source_sha256']:
                    raise ValueError('Repaired source changed before recovery admission')
                policy._recovery_root=str(root);policy.repair_outcomes=True;policy.code_revision_loop=True
                policy.execution_environment='local';policy.recovery_factory=None;policy.automatic_retry_limit=limit
                policy.code_revision_limit=max(0,state['code_revision_limit']-state['coding_requests'])
                parent=(state['attempts'][-1]['id']+'-'+repair['id']) if continuing else (state['attempts'][-1]['id'] if state['attempts'] else repair['id'])
                (policy.directory/'recovery-parent.json').write_text(json.dumps(dict(root=str(root),parent_attempt_id=parent,id=identity))+'\n')
                state.update(retry_directory=str(policy.directory))
                state['attempts'].append(dict(id=identity,parent_attempt_id=parent,directory=str(policy.directory),source_sha256=state['source_sha256'],status='checking_scene'))
                self.save(root,state)
                original=policy.prepare_before_session
                def prepare(prompt):
                    try:
                        self.preflight(policy,root,state)
                        original(prompt)
                        state['attempts'][-1]['status']='running'
                        state['attempts'][-1]['source_sha256']=hashlib.sha256(policy.validated_program['source'].encode()).hexdigest()
                        self.save(root,dict(state,**resolution('recovering','Running the corrected program' if initial_motion_pending else f'Retrying the task · {number} of {limit}',
                            'Fresh corrective geometry and the full native preflight passed.',
                            'Join the ordinary queue/Home flow, recapture and replan before task motion; verify again after parking.'),retry_count=number))
                    except Exception as exc:
                        current=read(root/'recovery-state.json')
                        if current.get('state')=='recovering':
                            state['attempts'][-1]['status']='cancelled' if policy.cancelled() else 'unresolved'
                            self.save(root,dict(state,**resolution('cancelled' if policy.cancelled() else 'unresolved',
                                'Recovery stopped',str(exc)[:1000],'Review the recorded blocker before another Run.'),
                                coding_requests=state['coding_requests']+policy._coding_requests))
                        elif policy._coding_requests:
                            self.save(root,dict(current,coding_requests=state['coding_requests']+policy._coding_requests))
                        policy.close_transport()
                        raise
                policy.prepare_before_session=prepare
                return policy
            try:
                return HostedRunner.launch(self.runner,visitor,dict(provider='codex',api_key='',prompt=task,
                    model=loop.get('model') or 'gpt-6-astra',reasoning_effort='high',response_speed='standard',
                    run_duration_s=300,use_api_depth=True,share_conversation=True,automatic_retry_limit=limit),provider_transform=attach)
            except Exception as exc:
                self.save(root,dict(state,**resolution('unresolved','Recovery could not start',str(exc)[:1000],
                    'Review this failure before another Run.')))
                raise

    def preflight(self,policy,root,state):
        policy._check_cancelled()
        observation=policy._postpark_observe()
        folder=policy.directory/'recovery-preflight';folder.mkdir()
        (folder/'station-before.json').write_text(json.dumps(observation,indent=2)+'\n')
        if not stopped_for_observation(observation):
            reason=(observation.get('safety') or {}).get('reason') or 'stopped hardware state was not confirmed'
            blocker=resolution('action_needed','Station needs attention before recovery',
                f'Fresh station observation blocked recovery: {reason}.',
                'Resolve the station status shown above, then check the scene again. No robot session was admitted.',
                preflight_recheck=True,recovery_available=True,recovery_run_id=Path(root).name)
            state['attempts'][-1]['status']='action_needed'
            self.save(root,dict(state,**blocker,preflight_evidence=str(folder)))
            raise RuntimeError(blocker['detail'])
        if policy.vision_session:
            policy.vision_session.start(cancelled=policy.cancelled,emit=policy._emit);policy.vision_session.wait_ready()
        program=folder/'repair.py';program.write_text(policy.validated_program['source'])
        queries=folder/'queries.json';queries.write_text(json.dumps({q['name']:q['query'] for q in policy.validated_program['queries']}))
        policy._task_update('Checking fresh recovery geometry.','Read-only capture and complete native planning; no robot session yet.',
            'Retry only a justified corrective plan; report an explicit blocker otherwise.')
        scene=policy._run_harness(mode='observe',directory=folder/'scene',task=policy.task,cancelled=policy.cancelled)
        snapshot=Path(scene.get('snapshot') or folder/'missing-snapshot').resolve()
        if (scene.get('status')!='OBSERVED' or not (snapshot/'snapshot.json').is_file()
                or not snapshot.is_relative_to(folder.resolve()) or scene.get('physical_motion_calls',0)!=0):
            raise RuntimeError('Fresh recovery snapshot could not be captured; no robot session was admitted.')
        policy._check_cancelled()
        result=policy._run_harness(mode='plan',directory=folder/'plan',task=policy.task,
            snapshot=snapshot,program=program,queries=queries,cache_root=policy.directory,cancelled=policy.cancelled)
        task=read(folder/'plan/task_definition.json')
        blocker=geometry_blocker(task,policy.task)
        if (not blocker and result.get('planning_success') is not True
                and result.get('status') in ('PLAN_FAILED','PROGRAM_ERROR','BUILD_TASK_FAILED')
                and not result.get('failure_kind') and not policy.cancelled()):
            policy.revise_before_session(result,folder/'plan',scene)
            result=policy._preparation_result
            latest=Path(policy._preparation._attempts[-1]['directory'])/'plan'
            task=read(latest/'task_definition.json')
            blocker=geometry_blocker(task,policy.task)
            plan_folder=latest
        else:plan_folder=folder/'plan'
        stages=read(plan_folder/'full_sequence_plan.json').get('stages') or []
        if (not blocker and stages and all(s.get('native_noop') is True for s in stages)
                and all(s.get('kind')=='move' for s in task.get('steps',[]))):
            blocker=resolution('unresolved','No corrective move was planned',
                'The complete native plan retains the measured pose; it does not justify a physical retry.',
                'Review the recorded measurement uncertainty. No robot session was admitted and no reset requirement was established.')
        if not blocker and (result.get('planning_success') is not True or not task.get('steps')):
            harness_error=result.get('status') in ('HARNESS_ERROR','SCENE_CAPTURE_FAILED')
            reason=str(result.get('reason') or result.get('status'))
            blocker=resolution('action_needed' if harness_error else 'unresolved',
                'Recovery preflight could not run' if harness_error else 'No complete recovery plan passed',
                ('The recovery launcher rejected its arguments.' if result.get('failure_kind')=='launcher_arguments' else
                 'The recovery harness failed before returning a native result. Raw diagnostics are retained under this attempt.') if harness_error else reason[:1000],
                'Correct the reported runner error, then check the scene again. No scene reset requirement was established.' if harness_error else
                'Review the native planning failure. A failed candidate does not establish that a scene reset is required.',
                **(dict(preflight_recheck=True,recovery_available=True,recovery_run_id=Path(root).name) if harness_error else {}))
        if blocker:
            state['attempts'][-1].update(status=blocker['state'],preflight_result={k:result[k] for k in ('status','reason','planning_success','physical_motion_calls','failure_kind','process_returncode','process_diagnostics') if k in result})
            self.save(root,dict(state,**blocker,preflight_evidence=str(folder),
                preflight_result={k:result[k] for k in ('status','reason','planning_success','physical_motion_calls','failure_kind','process_returncode','process_diagnostics') if k in result}))
            policy._task_update(blocker['title']+'.',blocker['detail'],blocker['next_action'])
            raise RuntimeError(blocker['detail'])
        policy._check_cancelled()
        latest=policy._postpark_observe()
        (folder/'station-after.json').write_text(json.dumps(latest,indent=2)+'\n')
        if not stopped_for_observation(latest):
            raise RuntimeError('The station changed while recovery was checking the scene; no automatic retry was admitted.')

    def finish(self,visitor,policy,root):
        state=read(root/'recovery-state.json')
        if state.get('state')!='recovering':return
        completion=read(policy.directory/'task-completion.json')
        brief={k:completion[k] for k in ('status','success','reason','failed_checks','evidence') if k in completion}
        native=policy._outcome or {}
        state['coding_requests']=state.get('coding_requests',0)+policy._coding_requests
        repairs=[a for a in policy._recovery_attempts if validated_recovery(a)]
        # A zero-command live planning failure uses the coding budget, not a
        # physical retry. Parent-side submitted packets fence unknown motion.
        no_commands=(completion.get('pre_motion_failure') is True and native.get('physical_motion_calls',0)==0
            and not policy._saved_motion_requests and not policy._failure and not policy._controller_failure)
        if no_commands and not state.get('initial_motion_pending'):state['retry_count']=max(0,state['retry_count']-1)
        if native.get('physical_motion_calls',0)>0:state['initial_motion_pending']=False
        if completion.get('success') is True:
            final=resolution('verified','Task success verified after retry','Fresh after-parking checks confirmed the repaired attempt.',
                'No action needed. The original unverified attempt remains in history.')
        else:
            task=read(Path(policy._attempts[-1]['directory'])/'execute/task_definition.json') if policy._attempts else {}
            final=geometry_blocker(task,policy.task) or resolution('unresolved',f'Retry limit reached · {state["retry_count"]} of {state["retry_limit"]}',
                str(completion.get('reason') or visitor.controller.status().get('error') or native.get('reason') or native.get('status') or 'No fresh after-parking verification was recorded.')[:1000],
                'The automatic retry limit is reached. Review the failed or unknown checks before another Run; no reset requirement was established.')
            available=state.get('initial_motion_pending') or state['retry_count']<state['retry_limit']
            if (final['state']=='unresolved' and available and completion and repairs
                    and completion.get('parking',{}).get('status')=='PARKING_OBSERVED'
                    and native.get('status') not in ('CANCELLED','RECOVERY_BLOCKED','HARNESS_ERROR','SCENE_CAPTURE_FAILED')
                    and not native.get('failure_kind') and not native.get('ambiguous_command_state') and not policy._failure and not policy.review_cancelled()
                    and not visitor.controller.status().get('error') and not getattr(policy,'_controller_failure',False)):
                final=resolution('retry_pending',f'Preparing retry {state["retry_count"]+1} of {state["retry_limit"]}',
                    'The latest failed attempt was diagnosed and a candidate passed offline planning.',
                    'Check the latest candidate against fresh geometry and a complete plan before the next admission.',candidate_directory=str(policy.directory))
            elif final['state']=='unresolved' and available:
                final=resolution('unresolved','Task code-revision limit reached' if state['coding_requests']>=state['code_revision_limit'] else 'No justified recovery candidate',
                    str(completion.get('reason') or native.get('reason') or 'No fresh outcome was confirmed.'),
                    'No coding requests remain for this submission.' if state['coding_requests']>=state['code_revision_limit'] else
                    'Review the latest repair failure or uncertainty. No validated correction is available; no automatic replay was admitted.')
        if state.get('attempts'):
            state['attempts'][-1].update(status=final['state'],completion=brief,native_status=native.get('status'))
        self.save(root,dict(state,**final,completion=brief,native_status=native.get('status')))
