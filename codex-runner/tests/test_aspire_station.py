"""Shared-only configuration, launch and gripper asset checks; no hardware calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from remote_yam.aspire_gripper_model import load_gripper_profile
from remote_yam.aspire_station import STATION, load_station_config


def module(name,filename):
    spec=importlib.util.spec_from_file_location(name,filename)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_minimal_config_uses_shared_runtime_and_persistent_learning(tmp_path):
    config=tmp_path/'station.json'
    config.write_text(json.dumps(dict(aspire='native',python='env/bin/python',origin='https://fixture.invalid',robot_id='fixture')))
    result=load_station_config(config)
    assert result['aspire']==str(tmp_path/'native')
    assert result['python']==str(tmp_path/'env/bin/python')
    assert Path(result['harness_module'])==STATION/'agent_harness.py'
    assert result['segmentation_backend']=='astra'
    assert result['published_skills'] and result['executable_skills']['enabled']
    assert '/outputs/' not in result['skill_directory']
    assert '/outputs/' not in result['skill_learning']['root']
    assert result['skill_learning']['review_timing']=='after_run'


def test_installed_gripper_profile_is_relative_and_byte_verified():
    filename=STATION/'robohouse-linear4310.json'
    raw=json.loads(filename.read_text())
    assert not Path(raw['model_xml']).is_absolute()
    profile=load_gripper_profile(filename)
    for name,digest in [(profile['model_xml'],profile['model_xml_sha256'])]+[(m['path'],m['sha256']) for m in profile['mesh_files'].values()]:
        assert Path(name).is_relative_to(STATION)
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest
    assert (STATION/'assets/linear_4310/LICENSE').is_file()


def test_ui_launcher_passes_station_identity_without_launching_a_task(tmp_path):
    native=tmp_path/'native/aspire/real/run_script.py';native.parent.mkdir(parents=True);native.touch()
    python=tmp_path/'env/bin/python';python.parent.mkdir(parents=True);python.touch()
    config=tmp_path/'station.json';config.write_text(json.dumps(dict(aspire=str(tmp_path/'native'),python=str(python),origin='https://fixture.invalid',robot_id='fixture')))
    launcher=module('portable_ui_launcher',STATION.parent/'launch_aspire.py')
    with patch.dict(os.environ,{},clear=False), patch.object(sys,'argv',['launch_aspire.py','--config',str(config),'--read-only','--port','8792']),patch.object(os,'execv') as launch:
        launcher.main()
        assert os.environ['YAM_RUNNER_PYTHON']==str(python)
        assert os.environ['ASPIRE_RUNTIME_PYTHON']==str(python)
    command=launch.call_args.args[1]
    assert command[0]==str(STATION.parents[1]/'run-codex.sh')
    assert command[command.index('--robot-id')+1]=='fixture'
    assert command[command.index('--session-api')+1]=='https://fixture.invalid'
    assert command[-3:]==['--read-only','--port','8792']


def test_shared_launcher_resolves_assets_before_saving_profile(tmp_path):
    launcher=module('portable_native_launcher',STATION/'launch_observation.py')
    output=tmp_path/'run';native=tmp_path/'native';native.mkdir()
    def run(command,**kwargs):
        (output/'result.json').write_text('{}')
        return type('Result',(),{'returncode':0})()
    argv=['launch_observation.py','--aspire',str(native),'--python',sys.executable,'--origin','https://fixture.invalid','--robot-id','fixture','--output',str(output),'--no-recording']
    with patch.object(sys,'argv',argv),patch('subprocess.check_output',side_effect=[launcher.PIN,'']),patch('subprocess.run',side_effect=run):
        assert launcher.main()==0
    profile=json.loads((output/'gripper-defaults.json').read_text())
    assert Path(profile['model_xml']).is_file()
    receipt=json.loads((output/'launch.json').read_text())
    assert not receipt['physical_commands_enabled']
    assert receipt['camera_roles']==['top','left','right']
    assert 'integrations/aspire' not in json.dumps(receipt)


def test_execution_still_requires_controller_bridge(tmp_path):
    launcher=module('portable_native_launcher_guard',STATION/'launch_observation.py')
    with patch.object(sys,'argv',['launch_observation.py','--aspire',str(tmp_path),'--python',sys.executable,'--origin','https://fixture.invalid','--robot-id','fixture','--output',str(tmp_path/'run'),'--agent-mode','execute','--execute']):
        with pytest.raises(SystemExit):launcher.main()
    assert not (tmp_path/'run').exists()
