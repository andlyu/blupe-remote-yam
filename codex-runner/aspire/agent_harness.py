"""Station launch utilities used by the shared Codex/ASPIRE policy."""
import json
import os
from pathlib import Path
import subprocess
import signal
import time


def coding_instructions(config):
    real=Path(config['aspire'])/'aspire/real'
    if config.get('coding_baseline')=='upstream' or config.get('original_upstream_skills'):
        import hashlib
        sources=[real/'AGENTS.md']
        sources.extend(real/f'.agents/skills/{name}/SKILL.md' for name in
            ('yam-geometry','yam-motion-planner','yam-grasp-pickup','yam-transport'))
        sources.extend(real/'cap/saved_scripts/skill_library'/name for name in
            ('grasp_geometry.py','pick_place.py'))
        material=[]
        for path in sources:
            material.append('\nORIGINAL UPSTREAM SOURCE '+str(path)+' SHA256 '+
                hashlib.sha256(path.read_bytes()).hexdigest()+'\n'+path.read_text())
        context=('Clean upstream baseline with per-run local skills.' if config.get('coding_baseline')=='upstream'
                 else 'Retain and reuse compatible accumulated local code skills supplied with this request. This is not a clean baseline reset.')
        return ('Pinned original NVIDIA ASPIRE callable skills and examples. '+context+'\n'
            +''.join(material)+'\nCURRENT STATION TOOL/SCHEMA/EXECUTION INTERFACE:\n'
            +Path(__file__).with_name('UPSTREAM-BASELINE.md').read_text())
    text=(real/'AGENTS.md').read_text()+'\n'
    for name in ('yam-geometry','yam-motion-planner','yam-grasp-pickup','yam-transport'):
        text+=(real/f'.agents/skills/{name}/SKILL.md').read_text()+'\n'
    text+='\nReusable measured geometry helper source:\n'+Path(__file__).with_name('scene_geometry.py').read_text()
    # Read actual upstream library interfaces; do not inject its station poses
    # as Robo-house geometry or claim automatic semantic retrieval exists.
    import ast
    library=[]
    for path in (real/'cap/saved_scripts/skill_library').glob('*.py'):
        tree=ast.parse(path.read_text())
        library.extend(dict(module='skill_library.'+path.stem,name=n.name,doc=ast.get_docstring(n))
            for n in tree.body if isinstance(n,ast.FunctionDef))
    return (text+'\nUpstream skill-library functions (station assumptions may differ):\n'+json.dumps(library)
        +'\nAUTHORITATIVE CURRENT ROBO-HOUSE CONTRACT (overrides older station examples):\n'
        +Path(__file__).with_name('STATION.md').read_text()+'\n'
        +Path(__file__).with_name('CODE-GENERATION.md').read_text())


class HarnessClient:
    # The API bridge already rejects every command after Stop or a packet
    # failure. Leave the script alive briefly to save its failure and release
    # the native recorder; killing the process group first corrupts its MP4s.
    CANCEL_GRACE_SECONDS = 15.

    def __init__(self,config,origin,robot_id):
        self.config,self.origin,self.robot_id=dict(config),origin,robot_id

    def __call__(self, *, mode,directory,task,bridge_socket=None,snapshot=None,
                 program=None,queries=None,cache_root=None,task_definition=None,cancelled=lambda:False):
        from remote_yam.runpod_sam3 import segmentation_environment
        segmentation_env=segmentation_environment(self.config)
        directory=Path(directory)
        command=[self.config['python'],str(Path(__file__).with_name('launch_observation.py')),
            '--aspire',self.config['aspire'],'--python',self.config['python'],
            '--origin',self.origin,'--robot-id',self.robot_id,'--output',str(directory),
            '--script',str(Path(__file__).with_name('agent_entry.py')),'--native-planner',
            '--task',task,'--agent-mode',mode]
        command+=['--segmentation',segmentation_env.get('YAM_SEGMENTATION_BACKEND','astra')]
        command+=['--gripper-geometry',self.config.get('gripper_geometry','robohouse-linear4310')]
        if self.config.get('coding_baseline')=='upstream' or self.config.get('original_upstream_skills'):
            command+=['--original-upstream-skills']
        if snapshot:command+=['--agent-snapshot',str(snapshot)]
        if program:command+=['--agent-program',str(program)]
        if queries:command+=['--agent-queries',str(queries)]
        if cache_root:command+=['--agent-cache-root',str(cache_root)]
        if task_definition:command+=['--agent-task-definition',str(task_definition)]
        if bridge_socket and mode in ('observe','execute'):
            command+=['--bridge-socket',bridge_socket]
        if mode=='execute':command+=['--execute']
        else:command+=['--no-recording']
        directory.parent.mkdir(parents=True,exist_ok=True)
        log_path=directory.parent/(directory.name+'-process.log')
        with log_path.open('w') as log:
            env={key:os.environ[key] for key in ('PATH','HOME','CODEX_HOME','TMPDIR','LANG','SSL_CERT_FILE','SSL_CERT_DIR') if key in os.environ}
            env.update(segmentation_env)
            process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            cancellation_at=None
            forced_shutdown=False
            try:
                while process.poll() is None:
                    if cancellation_at is None and cancelled():
                        cancellation_at=time.monotonic()
                    if cancellation_at is not None and time.monotonic()-cancellation_at>=self.CANCEL_GRACE_SECONDS:
                        forced_shutdown=True
                        break
                    time.sleep(.1)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(5)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
        diagnostics=directory.parent/(directory.name+'-process.json')
        diagnostics.write_text(json.dumps(dict(command=command,returncode=process.returncode,
            log=str(log_path),mode=mode),indent=2)+'\n')
        if directory.exists():
            (directory/'harness_shutdown.json').write_text(json.dumps(dict(
                cancellation_observed=cancellation_at is not None,
                forced_shutdown=forced_shutdown,process_returncode=process.returncode,
                cleanup_grace_seconds=self.CANCEL_GRACE_SECONDS),indent=2)+'\n')
        path=directory/('scene.json' if mode=='observe' else 'task_result.json')
        if not path.is_file():
            stages_path=directory/'task_stages.json'
            plan_path=directory/'full_sequence_plan.json'
            stages=json.loads(stages_path.read_text()) if stages_path.exists() else []
            plan=json.loads(plan_path.read_text()) if plan_path.exists() else {}
            result=dict(status='CANCELLED' if cancellation_at is not None else 'HARNESS_ERROR',success=False,
                planning_success=plan.get('success',False),physical_motion_calls=len(stages),events=stages,
                reason='Missing '+path.name+'; '+log_path.read_text()[-6000:])
            result['failure_kind']=('launcher_arguments' if process.returncode==2 and 'usage:' in log_path.read_text()
                else 'process_exit' if process.returncode else 'missing_artifact')
            if stages:result['failing_stage']=stages[-1]['stage']
            # Native stdout only prints a shortened reward summary. The
            # unmodified harness retains the complete exception separately.
            events_path=directory/'debug_events.jsonl'
            if events_path.exists():
                for line in events_path.read_text().splitlines():
                    event=json.loads(line)
                    if event.get('type')=='exec_end' and event.get('error'):
                        result['reason']=event['error']
                        result['native_error_evidence']=str(events_path)
            native_path=directory/'result.json'
            native=json.loads(native_path.read_text()) if native_path.exists() else {}
            if native.get('details',{}).get('status')=='CAPTURING_SCENE' and not stages:
                result.update(status='SCENE_CAPTURE_FAILED',failing_stage='initial_scene_capture')
        else:
            result=json.loads(path.read_text())
        result.update(process_returncode=process.returncode,process_diagnostics=str(diagnostics))
        if mode!='observe' and (directory/'scene.json').exists():
            result['initial_scene']=json.loads((directory/'scene.json').read_text())
            result['scene']=result['initial_scene']
            if (directory/'outcome_scene.json').exists():
                result['outcome_scene']=json.loads((directory/'outcome_scene.json').read_text())
                result['scene']=result['outcome_scene']
        return result
