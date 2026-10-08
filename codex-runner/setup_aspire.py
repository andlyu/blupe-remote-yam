"""Install/check BluPe's API-backed native ASPIRE runtime without moving a robot."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

RUNNER=Path(__file__).resolve().parent
sys.path.insert(0,str(RUNNER/'src'))
from remote_yam.aspire_station import PIN, load_station_config, station_config, recording_environment

UPSTREAM='https://github.com/NVlabs/ASPIRE.git'
ORIGIN='https://yam-session-api.n5hthc3gj4cqy.us-east-1.cs.amazonlightsail.com'


def clean_checkout(source):
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    def git(*args):return subprocess.check_output(['git','-C',str(source),*args],env=env,text=True).strip()
    if git('rev-parse','HEAD')!=PIN or git('status','--porcelain'):
        raise ValueError('Native ASPIRE must be a clean checkout of the pinned revision; existing sources are never reset.')


def check(config):
    clean_checkout(Path(config['aspire']))
    # This child imports the real native namespace and compiles both models.
    # It never instantiates an API session or requests camera data.
    env={k:v for k,v in os.environ.items() if not k.startswith(('OPENFORGE_','CAP_','YAM_','GIT_'))}
    real=Path(config['aspire'])/'aspire/real'
    env['PYTHONPATH']=os.pathsep.join((str(RUNNER/'src'),str(real),str(real/'cap/saved_scripts'),str(RUNNER/'aspire')))
    env['PYTHONDONTWRITEBYTECODE']='1'
    env=recording_environment(config['python'], env)
    code='''
import hashlib, json, pathlib, sys
import cv2, hydra, mink, mujoco, numpy, scipy, portal, imageio_ffmpeg
from cap.agent.agent_config import register_configs
from cap.env.real_bimanual_yam.skills import make_namespace
from cap.agent.tools.freespace_move import FreespaceMoveTool
from cap.agent.recorder import ScriptRecorder
import agent_harness, agent_snapshot, scene_geometry
from remote_yam.aspire_gripper_model import load_gripper_profile
from remote_yam.aspire_published_skills import PublishedSkillLibrary
from remote_yam.aspire_skill_learning import configured_skill_library
config=json.loads(sys.argv[1])
real=pathlib.Path(config['aspire'])/'aspire/real'
mujoco.MjModel.from_xml_path(str(real/'robot/models/station/station.xml'))
profile=load_gripper_profile(pathlib.Path(agent_harness.__file__).with_name('robohouse-linear4310.json'))
for path,digest in [(profile['model_xml'],profile['model_xml_sha256'])]+[(m['path'],m['sha256']) for m in profile['mesh_files'].values()]:
    assert hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()==digest, path
mujoco.MjModel.from_xml_path(profile['model_xml'])
assert 'build_task' in agent_harness.coding_instructions(config)
library=PublishedSkillLibrary()
assert library.programs('pick up a block')
assert library.retrieve('pick and place')['entries']
learning=configured_skill_library(config)
if learning is not None: learning._check_current()
assert pathlib.Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()
print('ASPIRE runtime ready: pinned native namespace, planner, gripper assets, shared catalog and persistent learning. No robot accessed.')
'''
    subprocess.run([config['python'],'-c',code,json.dumps(config)],env=env,cwd=RUNNER,check=True)
    subprocess.run(['ffmpeg','-version'],env=env,stdout=subprocess.DEVNULL,check=True)
    # Parse the unmodified upstream CLI to catch missing config/runtime imports.
    subprocess.run([config['python'],str(real/'run_script.py'),'--help'],env=env,cwd=real,stdout=subprocess.DEVNULL,check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    default_state=Path.home()/'.local/share/blupe/aspire'
    parser.add_argument('--state-dir',type=Path,default=default_state)
    parser.add_argument('--config',type=Path,default=Path.home()/'.config/blupe/aspire.json')
    parser.add_argument('--source',type=Path,help='Use an existing clean pinned NVIDIA ASPIRE checkout')
    parser.add_argument('--runtime-python',type=Path,help='Use an existing native Python environment instead of installing one')
    parser.add_argument('--python',default='3.11',help='Python version/path passed to uv for a new native environment')
    parser.add_argument('--origin',default=ORIGIN)
    parser.add_argument('--robot-id',default='robot-ba8413962083809c')
    parser.add_argument('--check',action='store_true',help='Only verify the saved config/runtime; no installation or robot access')
    args=parser.parse_args()
    if args.check:
        check(load_station_config(args.config));return 0
    state=args.state_dir.expanduser().absolute()
    source=(args.source.expanduser().absolute() if args.source else state/('source-'+PIN[:7]))
    config_file=args.config.expanduser().absolute()
    # Never silently replace an operator's station/perception/learning settings.
    existing=load_station_config(config_file) if config_file.exists() else None
    if existing and (existing['aspire']!=str(source) or
                     (args.runtime_python and existing['python']!=str(args.runtime_python.expanduser().absolute()))):
        parser.error('Existing config uses another runtime; choose a new --config path or edit it explicitly.')
    state.mkdir(parents=True,exist_ok=True)
    env={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env['GIT_LFS_SKIP_SMUDGE']='1'
    if not source.exists():
        subprocess.run(['git','init',str(source)],env=env,check=True)
        subprocess.run(['git','-C',str(source),'remote','add','origin',UPSTREAM],env=env,check=True)
        subprocess.run(['git','-C',str(source),'fetch','--depth=1','origin',PIN],env=env,check=True)
        subprocess.run(['git','-C',str(source),'checkout','--detach',PIN],env=env,check=True)
    clean_checkout(source)
    python=(args.runtime_python.expanduser().absolute() if args.runtime_python else state/'venv/bin/python')
    if not args.runtime_python:
        uv=shutil.which('uv')
        if not uv:parser.error('Install uv, then rerun setup; see codex-runner/docs/aspire/README.md.')
        if not python.exists():subprocess.run([uv,'venv','--python',args.python,str(state/'venv')],check=True)
        subprocess.run([uv,'pip','install','--python',str(python),'-r',str(RUNNER/'aspire/requirements.txt')],check=True)
    config=existing or station_config(dict(aspire=str(source),python=str(python),
        origin=args.origin,robot_id=args.robot_id,skill_directory=str(state/'skills/programs'),
        run_directory=str(state/'runs'),skill_learning=dict(root=str(state/'skills/topics'),
            suite='robohouse',enabled=True,review_timing='after_run')))
    check(config)
    if not existing:
        # Resolve the bundled harness dynamically so a different checkout can load this config.
        config.pop('harness_module',None)
        config_file.parent.mkdir(parents=True,exist_ok=True)
        with config_file.open('x') as handle:json.dump(config,handle,indent=2);handle.write('\n')
    print('Config: '+str(config_file))
    print('Launch: '+shlex.join(['./run-aspire.sh','--config',str(config_file)]))
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,subprocess.CalledProcessError,FileNotFoundError) as exc:
        print('ASPIRE setup failed: '+str(exc),file=sys.stderr);raise SystemExit(2)
