"""Launch the pinned ASPIRE real script harness; execution requires a leased bridge."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PIN = 'f4c8939aab0af9b97690c561bd80e282940f7886'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aspire', type=Path, required=True)
    parser.add_argument('--python', type=Path, required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--robot-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--script', type=Path, default=Path(__file__).with_name('agent_entry.py'))
    parser.add_argument('--bridge-socket', type=Path)
    parser.add_argument('--no-recording', action='store_true')
    parser.add_argument('--segmentation', choices=('astra', 'bundlesdf', 'runpod_sam3'), default=os.environ.get('YAM_SEGMENTATION_BACKEND','astra'))
    parser.add_argument('--gripper-geometry', choices=('robohouse-linear4310', 'upstream-yam', 'published'), default='robohouse-linear4310')
    parser.add_argument('--native-planner', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--task', default='Inspect the current scene.')
    parser.add_argument('--agent-mode', choices=('observe', 'plan', 'execute', 'evaluate'), default='observe')
    parser.add_argument('--agent-snapshot', type=Path)
    parser.add_argument('--agent-program', type=Path)
    parser.add_argument('--agent-queries', type=Path)
    parser.add_argument('--agent-cache-root', type=Path)
    parser.add_argument('--agent-task-definition', type=Path)
    parser.add_argument('--upstream-baseline', '--original-upstream-skills', dest='upstream_baseline', action='store_true',
        help='Original upstream callable bindings; this harness does not reset the coding skill library')
    args = parser.parse_args()
    if args.agent_mode == 'execute' and not args.execute:
        parser.error('Agent execute requires --execute')
    if args.agent_mode in ('observe', 'plan', 'evaluate') and args.execute:
        parser.error('Observation/planning modes cannot enable physical commands')
    if args.agent_mode == 'plan' and (not args.agent_snapshot or args.bridge_socket):
        parser.error('Agent planning requires a recorded snapshot and no live bridge')
    if args.agent_mode in ('plan', 'execute', 'evaluate') and not args.agent_program:
        parser.error('Agent plan/execute requires the generated program')
    if args.agent_mode == 'evaluate' and (not args.agent_task_definition or args.bridge_socket):
        parser.error('Agent evaluation requires a saved task definition and no live bridge')
    if args.execute and (not args.bridge_socket or not args.native_planner):
        parser.error('--execute requires the runner-owned bridge socket and native planner')
    checkout = args.aspire.resolve()
    commit = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != PIN or subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'], text=True).strip():
        parser.error('ASPIRE must be a clean checkout of '+PIN)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    real = checkout/'aspire/real'
    runner = Path(__file__).resolve().parents[1]
    # Forward only credentials needed by the explicitly selected segmenter.
    env = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'CODEX_HOME',
                                          'SSL_CERT_FILE', 'SSL_CERT_DIR') if key in os.environ}
    sys.path.insert(0,str(runner/'src'))
    from remote_yam.aspire_station import recording_environment
    env = recording_environment(args.python.absolute(), env)
    from remote_yam.runpod_sam3 import segmentation_environment
    env.update(segmentation_environment(environ=dict(os.environ,YAM_SEGMENTATION_BACKEND=args.segmentation)))
    env['PYTHONPATH'] = os.pathsep.join([str(runner/'src'), str(real), str(real/'cap/saved_scripts'), str(Path(__file__).parent.resolve())])
    env['OPENFORGE_ALLOW_PHYSICAL_MOTION'] = '1' if args.execute else '0'
    env['OPENFORGE_REAL_YAM_RECORDING_SOURCE'] = 'env'
    env['OPENFORGE_REAL_YAM_CAMERAS'] = 'top,left,right'
    env['OPENFORGE_DEBUG_OBS_CAMERAS'] = 'top,left,right'
    env['OPENFORGE_BUNDLESDF_PREVIEW_CAMERAS'] = 'top,left,right'
    env['OPENFORGE_RUN_DIR'] = str(output)
    # Resolving a venv's Python symlink escapes its site-packages.
    command = [str(args.python.absolute()), str(real/'run_script.py'), 'robot=real_yam',
        'env.name=yam-real:blupe', 'robot.adapter._target_=remote_yam.aspire_adapter.BlupeAspireAdapter',
        '+robot.adapter.origin='+args.origin, '+robot.adapter.robot_id='+args.robot_id,
        '+robot.adapter.output_dir='+str(output/'observations'),
        'robot.dashboard=false', 'robot.await_exit=false', 'robot.go_home_on_exit=false',
        'recording.enabled='+str(not args.no_recording).lower(), 'recording.cameras=[top,left,right]',
        'debug_ui.enabled=true', 'debug_ui.launch=false', 'skill_reflection.enabled=false',
        'script_file='+str(args.script.resolve()), 'script_output_dir='+str(output),
        'skill_library_path='+str(real/'cap/saved_scripts/skill_library'),
        'task='+json.dumps(args.task)]
    if args.agent_mode:
        command = [arg.replace('robot.adapter._target_=remote_yam.aspire_adapter.BlupeAspireAdapter',
            'robot.adapter._target_=agent_snapshot.AgentSnapshotAdapter') for arg in command]
        env['ASPIRE_AGENT_MODE'] = args.agent_mode
        env['ASPIRE_AGENT_TASK'] = args.task
        if args.upstream_baseline:
            env['ASPIRE_AGENT_CONTRACT']=str(Path(__file__).with_name('UPSTREAM-BASELINE.md'))
            command+=['+robot.upstream_geometry_skills=true']
        if args.agent_program:
            env['ASPIRE_AGENT_PROGRAM'] = str(args.agent_program.resolve())
        if args.agent_task_definition:
            env['ASPIRE_AGENT_TASK_DEFINITION'] = str(args.agent_task_definition.resolve())
        if args.agent_snapshot:
            command.append('+robot.adapter.snapshot='+str(args.agent_snapshot.resolve()))
        if args.agent_cache_root:
            command.append('+robot.adapter.cache_root='+str(args.agent_cache_root.resolve()))
    command += ['+robot.segmentation_backend='+args.segmentation]
    if args.native_planner:
        command.append('+robot.native_planner=true')
    if args.segmentation in ('astra','runpod_sam3'):
        query_key='astra_queries_file' if args.segmentation=='astra' else 'segmentation_queries_file'
        command.append('+robot.'+query_key+'='+str((args.agent_queries or Path(__file__).with_name('sam3-objects.json' if args.segmentation=='runpod_sam3' else 'astra-objects.json')).resolve()))
    geometry_digest = None
    if args.gripper_geometry in ('upstream-yam', 'robohouse-linear4310'):
        from remote_yam.aspire_gripper_model import load_gripper_profile
        profile_file = Path(__file__).with_name('upstream-yam-gripper.json' if args.gripper_geometry == 'upstream-yam' else 'robohouse-linear4310.json')
        geometry_bytes = (json.dumps(load_gripper_profile(profile_file), indent=2)+'\n').encode()
        geometry_snapshot = output/'gripper-defaults.json'
        geometry_snapshot.write_bytes(geometry_bytes)
        geometry_digest = hashlib.sha256(geometry_bytes).hexdigest()
        command.append('+robot.gripper_geometry_file='+str(geometry_snapshot))
    if args.bridge_socket:
        command.append('+robot.adapter.bridge_socket='+str(args.bridge_socket.absolute()))
    # This is saved agent context, not an LLM invocation. The coding agent must
    # read the bundled station contract before generating code for the upstream harness.
    briefing = Path(__file__).with_name('UPSTREAM-BASELINE.md' if args.upstream_baseline else 'STATION.md')
    (output/'agent_station_instructions.md').write_text(briefing.read_text())
    (output/'launch.json').write_text(json.dumps(dict(aspire_commit=commit, command=command,
        mode='leased_hardware_api_bridge' if args.execute else 'simulation_api_bridge' if args.bridge_socket else 'live_observation_only',
        physical_commands_enabled=args.execute, camera_roles=['top', 'left', 'right'],
        segmentation_backend=args.segmentation,
        segmentation_model='gpt-6-astra' if args.segmentation == 'astra' else 'facebook/sam3' if args.segmentation=='runpod_sam3' else 'SAM3',
        gripper_geometry_selection=args.gripper_geometry, gripper_geometry_sha256=geometry_digest,
        native_planner=args.native_planner,
        task=args.task, agent_mode=args.agent_mode,
        camera_environment={key: env[key] for key in ('OPENFORGE_REAL_YAM_CAMERAS',
            'OPENFORGE_DEBUG_OBS_CAMERAS', 'OPENFORGE_BUNDLESDF_PREVIEW_CAMERAS')},
        agent_station_instructions=str(briefing)), indent=2)+'\n')
    with (output/'launcher.log').open('w') as log:
        result = subprocess.run(command, cwd=real, env=env, stdout=log, stderr=subprocess.STDOUT,
            timeout=None if args.execute else max(240,float(env['RUNPOD_SAM3_TIMEOUT_S'])+60) if args.segmentation=='runpod_sam3' else 240 if args.segmentation == 'astra' else 90)
    print(str(output))
    # Upstream may exit zero after catching a script exception. Inspect results.
    if not (output/'result.json').exists():
        print('Upstream harness did not produce result.json', file=sys.stderr)
        return result.returncode or 2
    print((output/'result.json').read_text())
    task_result = output/'task_result.json'
    if task_result.exists() and json.loads(task_result.read_text()).get('status') in ('BLOCKED', 'FAILED'):
        return 2
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
