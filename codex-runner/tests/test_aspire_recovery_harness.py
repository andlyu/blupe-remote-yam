import os
"""Station launcher protocol checks; no robot/network/model subprocess runs."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_aspire_recovery_flow import station, wait

STATION=Path(__file__).resolve().parents[1]/'aspire'


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,STATION/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def client():
    return module('recovery_station_harness','agent_harness.py').HarnessClient(
        dict(python=sys.executable,aspire='/private/tmp/aspire-yam-f4c8939',original_upstream_skills=True),
        'https://fixture.invalid','fixture')


def test_actual_recovery_capture_and_plan_commands_pass_real_launcher_argument_validation(station):
    s=station;s.app.recovery_manager.start(s.visitor,s.root);assert wait(s)['state']=='verified'
    commands=[]
    def process(command,stdout,**kwargs):
        commands.append(command)
        output=Path(command[command.index('--output')+1]);mode=command[command.index('--agent-mode')+1]
        output.mkdir(exist_ok=True,parents=True)
        artifact=output/('scene.json' if mode=='observe' else 'task_result.json')
        artifact.write_text(json.dumps(dict(status='OBSERVED' if mode=='observe' else 'PLAN_ONLY',planning_success=mode=='plan')))
        return SimpleNamespace(poll=lambda:0,returncode=0)
    with patch('remote_yam.runpod_sam3.segmentation_environment',return_value={'YAM_SEGMENTATION_BACKEND':'runpod_sam3'}),patch('subprocess.Popen',side_effect=process):
        for request in s.requests[:2]:client()(**request)
    launcher=module('recovery_station_launcher','launch_observation.py')
    class AcceptedArguments(Exception):pass
    for command in commands:
        assert '--execute' not in command and '--bridge-socket' not in command and '--no-recording' in command
        with patch.object(sys,'argv',command[1:]),patch('subprocess.check_output',side_effect=AcceptedArguments):
            with pytest.raises(AcceptedArguments):launcher.main()
    plan=commands[1]
    assert Path(plan[plan.index('--agent-snapshot')+1])==s.requests[1]['snapshot']
    assert isinstance(json.loads(Path(plan[plan.index('--agent-queries')+1]).read_text()),dict)
    assert json.loads((Path(plan[plan.index('--output')+1]).parent/'plan-process.json').read_text())['returncode']==0


@pytest.mark.parametrize('code,stderr,kind',[(2,'usage: launch_observation.py\nerror: missing snapshot','launcher_arguments'),
    (1,'RuntimeError: process failed','process_exit'),(0,'','missing_artifact')])
def test_missing_artifact_preserves_actual_launcher_exit_status_and_failure_kind(tmp_path,code,stderr,kind):
    def process(command,stdout,**kwargs):
        stdout.write(stderr);stdout.flush()
        return SimpleNamespace(poll=lambda:code,returncode=code)
    with patch('remote_yam.runpod_sam3.segmentation_environment',return_value={}),patch('subprocess.Popen',side_effect=process):
        result=client()(mode='plan',directory=tmp_path/'plan',task='fixture',snapshot=tmp_path/'fresh',program=tmp_path/'repair.py')
    assert result['status']=='HARNESS_ERROR' and result['failure_kind']==kind
    assert result['process_returncode']==code and result['physical_motion_calls']==0
    recorded=json.loads(Path(result['process_diagnostics']).read_text())
    assert recorded['returncode']==code and '--agent-snapshot' in recorded['command']


def test_native_solver_failure_remains_distinct_from_launcher_failure(tmp_path):
    def process(command,stdout,**kwargs):
        output=Path(command[command.index('--output')+1]);output.mkdir()
        (output/'task_result.json').write_text(json.dumps(dict(status='PLAN_FAILED',planning_success=False,
            reason='Native IK failed',physical_motion_calls=0)))
        return SimpleNamespace(poll=lambda:0,returncode=0)
    with patch('remote_yam.runpod_sam3.segmentation_environment',return_value={}),patch('subprocess.Popen',side_effect=process):
        result=client()(mode='plan',directory=tmp_path/'plan',task='fixture',snapshot=tmp_path/'fresh',program=tmp_path/'repair.py')
    assert result['status']=='PLAN_FAILED' and 'failure_kind' not in result and result['process_returncode']==0
