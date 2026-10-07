import json
import threading
from unittest.mock import Mock

from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.aspire_repair_trace import AttemptTrace, recorded_trace
from remote_yam.aspire_lineage_catalog import LineageCatalog
from test_aspire_launch_routing import saved_library, TASK, SOURCE


def test_public_trace_upserts_incrementally_and_retains_only_display_fields(tmp_path):
    record=dict(id='astra-repair-1',parent_attempt_id='aspire-1')
    trace=AttemptTrace(record,tmp_path)
    trace.append('model_request','Generate/revise an ASPIRE program',attempt=1,feedback={'private':'source'})
    trace.append('model_progress','Checking support',progress_type='summary',item_id='r',encrypted_content='secret',raw='private')
    first=record['trace'][-1]['id']
    trace.append('model_progress','Checking support and chip visibility',progress_type='summary',item_id='r')
    assert record['trace'][-1]['id']==first
    assert len(record['trace'])==2
    trace.append('model_progress','hidden',progress_type='raw_reasoning')
    trace.append('tool_error','Native plan failed',arguments={'capability':'secret'})
    trace.append('model_request','Generate/revise an ASPIRE program',attempt=2)
    trace.append('model_progress','Revising target fit',progress_type='summary',item_id='r')
    restored=recorded_trace(record,tmp_path)
    assert len(restored)==5
    assert restored[1]['message']=='Checking support and chip visibility'
    assert restored[-1]['revision']==2
    assert restored[-1]['id']!=first
    assert all(e['attempt_id']=='astra-repair-1' and e['parent_attempt_id']=='aspire-1' for e in restored)
    assert not any(secret in json.dumps(restored) for secret in ('private','secret','hidden','capability'))


def test_active_child_summaries_forward_before_repair_finishes_and_preserve_original_lineage(tmp_path):
    library=saved_library(tmp_path);selected=library.select(TASK)
    policy=AspireCodexPolicy(harness=Mock(),calibration={},robot_id='fixture',task=TASK,
        directory=tmp_path/'run',instructions='',skill_directory=tmp_path/'skills',
        selected_executable=selected,allow_hardware=False)
    attempt=policy.directory/'attempt-01';attempt.mkdir()
    (attempt/'coding-response.json').write_text(json.dumps(selected['response']))
    native=dict(status='UNVERIFIED',success=False,physical_motion_calls=13,planning_success=True)
    policy._attempts=[dict(attempt=1,directory=str(attempt),execution=native)]
    original=dict(program_sha256='executed-source',used=[{'id':'original'}])
    policy._current_lineage=original.copy()
    sent,finish=threading.Event(),threading.Event();forwarded=[]
    policy.interaction_sink=lambda kind,message,**details:forwarded.append((kind,message,details))
    def generate(**request):
        child=policy._offline_repair
        child._emit('model_progress','Diagnosing partial chip visibility.',progress_type='summary',item_id='r',raw='private')
        child._emit('tool_result','Repair lineage',lineage={'program_sha256':'repair-source'})
        sent.set();assert finish.wait(5)
        return dict(selected['response'],source=SOURCE+'\n# recorded repair\n',summary='Preserve unknown centering',lesson='Fresh evidence required')
    policy.generator=generate
    policy.harness=lambda **r: dict(status='OBSERVED',context={},images={},snapshot='fixture') if r['mode']=='observe' else dict(status='PLAN_ONLY',planning_success=True,success=False,physical_motion_calls=0)
    completion=dict(status='UNVERIFIED',reason='centering unknown',failed_checks=['centered_over_chip'],images=[])
    worker=threading.Thread(target=policy._repair_outcome,args=(completion,None,native));worker.start()
    try:
        assert sent.wait(3)
        recovery=policy.public_config()['task_progress']['attempts'][-1]
        assert recovery['status']=='running'
        assert any(e['message']=='Diagnosing partial chip visibility.' for e in recovery['trace'])
        assert policy._current_lineage==original
        assert all(e['attempt_id']=='astra-repair-1' for e in recovery['trace'])
        assert all(details.get('attempt_id')=='astra-repair-1' for kind,_,details in forwarded if kind=='model_progress')
        assert 'private' not in json.dumps(recovery['trace'])
    finally:
        finish.set();worker.join(5)
    assert not worker.is_alive()
    saved=json.loads((policy.directory/'coding-loop.json').read_text())
    assert saved['attempts'][0]['execution']==native
    assert saved['recovery_attempts'][0]['status']=='PLAN_VALIDATED'
    episode=LineageCatalog(dict(skill_directory=str(policy.skill_directory),robot_id='fixture')).episode(
        dict(task_id='current',task=TASK,policy_directory=str(policy.directory),coding_loop=str(policy.directory/'coding-loop.json')))
    trace=episode['attempts'][1]['trace']
    assert any(e['message']=='Diagnosing partial chip visibility.' for e in trace)
    assert policy._current_lineage==original


def test_recorded_trace_recovers_only_this_repair_and_rejects_ambiguous_overlap(tmp_path):
    journal=tmp_path/'runner/interactions.jsonl';journal.parent.mkdir()
    record=dict(id='astra-repair-1',parent_attempt_id='aspire-1',started_at=10,ended_at=20,
        diagnosis='Chip remains unconfirmed',lesson='Do not loosen checks')
    events=[dict(id=1,timestamp=5,kind='model_progress',message='Previous development',details={'progress_type':'summary'}),
        dict(id=2,timestamp=11,kind='model_request',message='Generate/revise an ASPIRE program',details={'feedback':'private'}),
        dict(id=3,timestamp=12,kind='model_progress',message='Repair summary',details={'progress_type':'summary','raw':'private'}),
        dict(id=4,timestamp=13,kind='model_progress',message='Unrelated attempt',details={'progress_type':'summary','attempt_id':'other'}),
        dict(id=5,timestamp=14,kind='model_progress',message='hidden',details={'progress_type':'raw_reasoning'})]
    journal.write_text(''.join(json.dumps(e)+'\n' for e in events))
    trace=recorded_trace(record,tmp_path)
    assert any(e['message']=='Repair summary' for e in trace)
    assert trace[-1]['message']==record['diagnosis']
    assert not any(value in json.dumps(trace) for value in ('Previous development','Unrelated attempt','private','hidden'))
    events.append(dict(id=6,timestamp=15,kind='model_request',message='Unrelated skill promotion'))
    journal.write_text(''.join(json.dumps(e)+'\n' for e in events))
    assert [e['message'] for e in recorded_trace(record,tmp_path)]==[record['diagnosis']]


def test_trace_retains_distinct_unkeyed_summaries_and_tolerates_partial_journal(tmp_path):
    record=dict(id='astra-repair-1',parent_attempt_id='aspire-1')
    trace=AttemptTrace(record,tmp_path)
    trace.append('model_progress','Measuring uncertainty',progress_type='summary')
    trace.append('model_progress','Checking complete plan',progress_type='summary')
    with trace.path.open('a') as stream:
        stream.write('null\n[]\n{"attempt_id":"astra-repair-1"}\n{"partial"\n')
    assert [e['message'] for e in recorded_trace(record,tmp_path)]==[
        'Measuring uncertainty','Checking complete plan']
