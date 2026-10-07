"""Generated task definitions use ASPIRE tools; native plans precede dispatch."""
import json
import hashlib
from pathlib import Path
import time
import traceback
import numpy as np


def plain(value):
    if isinstance(value,np.ndarray):return plain(value.tolist())
    if isinstance(value,np.generic):return plain(value.item())
    if isinstance(value,float) and not np.isfinite(value):return None
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [plain(v) for v in value]
    return value


def task_segments(task):
    """Plan every move, including jaw changes occurring between moves."""
    if not isinstance(task,dict) or not isinstance(task.get('steps'),list) or not task['steps']:
        raise ValueError('build_task must return a nonempty steps list')
    segments=[];jaws={};names=set()
    for step in task['steps']:
        name=step['stage']
        if not isinstance(name,str) or not name or name in names:
            raise ValueError('Task stages must have unique names')
        names.add(name)
        if step['kind'] in ('open','close'):
            side=step['side']
            if side not in ('left','right'):raise ValueError('Invalid API arm')
            jaws[side]=1. if step['kind']=='open' else 0.
        elif step['kind']=='move':
            arguments=dict(step['arguments'])
            if any(k in arguments for k in ('trajectory_cache_key','preview_only')):
                raise ValueError('Task specifies poses; runtime owns preview/cache execution')
            segments.append(dict(stage=name,arguments=arguments,gripper_state=dict(jaws)))
            jaws={}
        else:raise ValueError('Task step kind must be move/open/close')
    if not segments:raise ValueError('Task contains no native motion paths')
    return segments


class CandidateSearchExhausted(RuntimeError):
    pass


def run_generated(source, tools, directory, *, execute=False, task_prompt='',
                  max_candidate_plans=64, candidate_search_s=30.):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    (directory/'generated_program.py').write_text(source)
    result=dict(task=task_prompt,success=False,status='NOT_RUN',planning_success=False,
        physical_motion_calls=0,events=[],source_sha256=hashlib.sha256(source.encode()).hexdigest())
    def save(name,value):
        (directory/name).write_text(json.dumps(plain(value),indent=2,allow_nan=False)+'\n')
    search_started = None
    search_elapsed = 0.
    candidates = []
    def candidate_plan(segments):
        nonlocal search_started
        if search_started is None:
            search_started = time.monotonic()
        elapsed = time.monotonic()-search_started
        if len(candidates) >= max_candidate_plans or elapsed >= candidate_search_s:
            raise CandidateSearchExhausted(
                f'Native candidate search exhausted after {len(candidates)} plans and {elapsed:.1f}s; '
                'individual rejected candidates are preserved, not proof of unreachability')
        started = time.time()
        plan = tools['read_only']['plan_freespace_sequence'](segments)
        record = dict(candidate=len(candidates)+1, started_at=started, finished_at=time.time(),
            success=plan.get('success'), status=plan.get('status'), reason=plan.get('reason'),
            failing_stage=plan.get('failing_stage'), segments=plain(segments), result=plain(plan))
        candidates.append(record)
        with (directory/'candidate-plans.jsonl').open('a') as stream:
            stream.write(json.dumps(record,allow_nan=False)+'\n')
        return plan
    try:
        program={}
        exec(compile(source,'generated_program.py','exec'),program)
        # Native @skill profiling records code preparation plus the later
        # planning/execution outcome, labeled by validation phase.
        from cap.agent.skill_registry import SkillRegistry
        registry=SkillRegistry.get()
        def generated_task_v1():
            nonlocal search_elapsed
            read_only = dict(tools['read_only'])
            if 'plan_freespace_sequence' in read_only:
                read_only['plan_freespace_sequence'] = candidate_plan
            try:
                return program['build_task'](read_only),dict(success=True,validation_phase='program_build')
            finally:
                search_elapsed = 0. if search_started is None else time.monotonic()-search_started
        task,_=registry.register(generated_task_v1)()
        save('task_definition.json',task)
        segments=task_segments(task)
        result['full_plan_started_at']=time.time()
        plan=tools['plan_freespace_sequence'](segments)
        result['full_plan_finished_at']=time.time()
        save('full_sequence_plan.json',plan)
        result.update(planning_success=plan['success'],full_plan_status=plan['status'])
        registry.profiles()['generated_task_v1'].record(dict(success=plan['success'],validation_phase='full_native_plan'))
        if not plan['success']:
            result.update(status='PLAN_FAILED',failing_stage=plan.get('failing_stage'),
                reason=plan.get('reason'),planner_feedback=plan)
            return result
        if not execute:
            result.update(status='PLAN_ONLY')
            return result
        if task.get('observation_only_repair') is True:
            result.update(status='RECOVERY_BLOCKED',reason=task.get('decision') or 'Fresh corrective geometry was not established',
                observation_only_repair=True)
            return result
        motion_stages={s['stage']:s for s in plan['stages']}
        for step in task['steps']:
            event=dict(stage=step['stage'],kind=step['kind'],status='started',started_at=time.time())
            result['events'].append(event)
            save('task_stages.json',result['events'])
            if step['kind']=='move' and motion_stages[step['stage']].get('native_noop') is True:
                event.update(status='Success',executed=False,native_noop=True,
                    reason=motion_stages[step['stage']]['reason'],trajectory_cache_key=None)
                save('task_stages.json',result['events'])
                continue
            result['physical_motion_calls']+=1
            if step['kind']=='move':
                moved=tools['freespace_move'](trajectory_cache_key=motion_stages[step['stage']]['trajectory_cache_key'])
                event.update(status=moved.status,executed=moved.executed,reason=moved.reason,
                    trajectory_cache_key=moved.trajectory_cache_key)
                if moved.status!='Success' or moved.executed is not True:
                    raise RuntimeError('Native execution failed at '+step['stage']+': '+moved.reason)
            else:
                jaw=tools['open_gripper' if step['kind']=='open' else 'close_gripper'](step['side'])
                event.update(status='returned',result=plain(jaw))
                if isinstance(jaw,dict) and jaw.get('success') is False:
                    raise RuntimeError('Gripper failed at '+step['stage']+': '+str(jaw))
            save('task_stages.json',result['events'])
        # A final evaluator receives a newly captured actual scene. A completed
        # packet alone never supplies physical success.
        try:
            final_tools=tools['fresh_read_only']()
            evidence=program['evaluate'](final_tools,task)
            if not isinstance(evidence,dict) or type(evidence.get('success')) is not bool:
                raise ValueError('evaluate must return visual evidence with a boolean success')
            result.update(success=evidence['success'],status='SUCCESS' if evidence['success'] else 'UNVERIFIED',
                placement_evidence=plain(evidence))
        except Exception as exc:
            result.update(status='UNVERIFIED',reason='Final outcome assessment: '+str(exc))
        registry.profiles()['generated_task_v1'].record(dict(success=result['success'],validation_phase='physical_execution',
            evidence=result.get('placement_evidence')))
        return result
    except CandidateSearchExhausted as exc:
        result.update(status='PLAN_FAILED',reason=str(exc),failing_stage='candidate_search',
            candidate_search_exhausted=True)
        return result
    except Exception as exc:
        result.update(status='FAILED' if result['physical_motion_calls'] else 'PROGRAM_ERROR',
            reason=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc())
        if result['events']:result['failing_stage']=result['events'][-1]['stage']
        return result
    finally:
        result['candidate_search'] = dict(plans=len(candidates),
            rejected=sum(c.get('success') is not True for c in candidates),
            limit=max_candidate_plans, budget_s=candidate_search_s,
            elapsed_s=search_elapsed,
            evidence=str(directory/'candidate-plans.jsonl'))
        save('task_result.json',result)
