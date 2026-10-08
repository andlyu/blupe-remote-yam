"""Run the same Codex + ASPIRE policy in offline planning or one queued trial."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

RUNNER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNNER/'src'))
from agent_harness import HarnessClient, coding_instructions
from remote_yam.api_depth import ApiDepth
from remote_yam.aspire_codex_policy import AspireCodexPolicy
from remote_yam.runpod_sam3 import configured_vision_session
from remote_yam.controller import RunnerController
from remote_yam.session import HttpSessionAPI
from remote_yam.aspire_review_recording import SessionReviewRecorder, completed_task_outcome
from remote_yam.aspire_skill_learning import configured_skill_library
from remote_yam.aspire_executable_skills import configured_executable_skills
from remote_yam.aspire_published_skills import PublishedSkillLibrary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path.home()/'.config/blupe/aspire.json')
    parser.add_argument('--prompt', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='gpt-6-astra')
    parser.add_argument('--run-name', help='Human-readable queue label and runner display name')
    parser.add_argument('--initialize-before-observation', action='store_true',
        help='Use a passing prior agent program, then capture and fully plan after normal queue/Home initialization')
    parser.add_argument('--feedback', type=Path, help='Prior run receipt for evidence-driven revision/resume')
    parser.add_argument('--reference-run', type=Path,
        help='Receipt from a different task used as actual code/outcome evidence; never reused as a live plan')
    parser.add_argument('--program-response', type=Path,
        help='Reuse an exact saved coding-agent JSON response in motion-disabled fresh planning')
    parser.add_argument('--upstream-baseline', action='store_true',
        help='Fresh original-ASPIRE context, per-run empty local code skills, and compatible upstream callable bindings')
    parser.add_argument('--original-upstream-skills', action='store_true',
        help='Use original ASPIRE callable skills while retaining accumulated local program retrieval')
    parser.add_argument('--fresh-coding-session', action='store_true', help='Keep prior code/feedback but start fresh model context')
    parser.add_argument('--recorded-scene', type=Path, help='Saved scene.json for motion-disabled code revision')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan-only', action='store_true')
    mode.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if args.recorded_scene and not args.plan_only:
        parser.error('--recorded-scene is only valid with --plan-only')
    if args.upstream_baseline and args.reference_run:
        parser.error('A clean upstream baseline excludes historical reference runs')
    if args.program_response and (not args.plan_only or args.feedback):
        parser.error('--program-response requires --plan-only and excludes --feedback; execute using the passing plan receipt')
    from remote_yam.aspire_station import load_station_config
    config = load_station_config(args.config)
    from remote_yam.aspire_station import recording_environment
    os.environ.update(recording_environment(config['python']))
    run_name=args.run_name or config.get('run_name','Codex + ASPIRE')
    prior_feedback = None;resume = None;validated_program = None
    if args.program_response:
        import ast
        validated_program=json.loads(args.program_response.read_text())
        if validated_program.get('action')!='program' or not isinstance(validated_program.get('source'),str):
            parser.error('Saved response must contain an actual coding-agent program')
        ast.parse(validated_program['source'])
        prior_feedback=dict(status='SAVED_AGENT_PROGRAM',previous_source=validated_program['source'],
            evidence=str(args.program_response.resolve()),
            instruction='Reuse this actual saved program with fresh observations and full native planning; revise only on an actual incompatibility/failure.')
    if args.feedback:
        prior=json.loads(args.feedback.read_text())
        if args.upstream_baseline and prior.get('coding_baseline')!='upstream':
            parser.error('Upstream baseline accepts feedback only from its own new baseline runs')
        if prior.get('task') != args.prompt:
            parser.error('Feedback task must match this prompt')
        loop_path=args.feedback.parent/'policy/coding-loop.json'
        if not loop_path.exists():
            # A prequeue planning failure has no live policy attempts. Its
            # actual generated programs belong to the preparation loop.
            loop_path=args.feedback.parent/'policy/preparation/coding-loop.json'
        loop=json.loads(loop_path.read_text())
        attempts=loop['attempts']
        prior_feedback=dict(status='PREVIOUS_ATTEMPT',result=prior['result'],
            executable_reuse=prior.get('executable_reuse', []))
        review_path=args.feedback.parent/'review.json'
        if review_path.exists():
            prior_feedback['recorded_review']=json.loads(review_path.read_text())
            review_images=[]
            for image in prior_feedback['recorded_review'].get('images',[]):
                path=Path(image).resolve()
                if not path.is_relative_to(args.feedback.parent.resolve()):
                    parser.error('Review images must belong to the previous run directory')
                review_images.append(str(path))
            prior_feedback['review_images']=review_images
        if attempts:
            resume=attempts[-1].get('model_conversation_id')
            prior_source=Path(attempts[-1]['directory'])/'generated_program.py'
            if prior_source.exists():prior_feedback['previous_source']=prior_source.read_text()
            if args.execute and prior.get('mode')=='plan_only' and prior['result'].get('planning_success') is True:
                validated_program=json.loads((prior_source.parent/'coding-response.json').read_text())
    if args.fresh_coding_session:resume=None
    if args.reference_run:
        reference=json.loads(args.reference_run.read_text())
        reference_root=args.reference_run.parent.resolve()
        evidence=dict(task=reference['task'],receipt=str(args.reference_run.resolve()),
            result=reference.get('result',reference.get('harness_result',{})),
            instruction='Different-task reference evidence only. Generate the current task from its fresh scene; no reference geometry/path is a live plan.')
        reference_review=reference_root/'review.json'
        reference_images=[]
        if reference_review.exists():
            evidence['recorded_review']=json.loads(reference_review.read_text())
            for image in evidence['recorded_review'].get('images',[]):
                path=Path(image).resolve()
                if not path.is_relative_to(reference_root):
                    parser.error('Reference review images must belong to the reference run directory')
                reference_images.append(str(path))
        reference_loop=reference_root/'policy/coding-loop.json'
        if reference_loop.exists():
            reference_attempts=json.loads(reference_loop.read_text())['attempts']
            if reference_attempts:
                source=Path(reference_attempts[-1]['directory'])/'generated_program.py'
                if source.exists():evidence['previous_source']=source.read_text()
        prior_feedback=dict(prior_feedback or {},reference_run=evidence,
            review_images=list((prior_feedback or {}).get('review_images',[]))+reference_images)
    if args.initialize_before_observation and (not args.execute or validated_program is None):
        parser.error('--initialize-before-observation requires --execute and a passing --feedback plan receipt')
    output = args.output.resolve();output.mkdir(parents=True, exist_ok=False)
    if args.upstream_baseline:
        config=dict(config,coding_baseline='upstream',skill_directory=str(output/'upstream-skills'))
    if args.original_upstream_skills:
        config=dict(config,original_upstream_skills=True)
    report = json.loads(ApiDepth(config['origin'], config['robot_id'], camera='top').read('/calibration', 256_000))
    harness=HarnessClient(config, config['origin'], config['robot_id'])
    if args.recorded_scene:
        saved_scene=json.loads(args.recorded_scene.read_text())
        if saved_scene.get('status')!='OBSERVED':parser.error('Recorded scene must be an observed ASPIRE scene')
        saved_scene['context']=dict(saved_scene['context'],scene_capture='recorded_replay',
            recorded_snapshot=saved_scene['snapshot'],
            planning_evidence='This recorded scene only; a different or reset scene remains unverified.')
        live_harness=harness
        def harness(**kwargs):
            return saved_scene if kwargs['mode']=='observe' else live_harness(**kwargs)
    policy = AspireCodexPolicy(harness=harness,
        calibration=report, robot_id=config['robot_id'], task=args.prompt,
        model=args.model, directory=output/'policy', instructions=coding_instructions(config),
        skill_directory=config['skill_directory'], max_revisions=config.get('max_revisions', 4),
        coding_timeout_s=config.get('coding_timeout_s',180),
        execution_environment='local',
        code_revision_limit=config.get('code_revision_limit',config.get('max_revisions',4)),
        plan_only=args.plan_only, allow_hardware=args.execute,
        initial_feedback=prior_feedback,resume_conversation=resume,validated_program=validated_program,
        display_name=run_name,initialize_before_observation=args.initialize_before_observation,
        vision_session=configured_vision_session(config),skill_learning=configured_skill_library(config),
        executable_skills=configured_executable_skills(config),
        published_skills=(PublishedSkillLibrary()
            if config.get('published_skills',True) and config.get('coding_baseline')!='upstream' else None),
        skill_review_timing=(config.get('skill_learning') or {}).get('review_timing','after_run'))
    if validated_program and prior_feedback and prior_feedback.get('executable_reuse'):
        source_hash=hashlib.sha256(validated_program['source'].encode()).hexdigest()
        policy._executable_reuse=[dict(item, reuse_from_feedback=str(args.feedback.resolve()))
            for item in prior_feedback['executable_reuse'] if item['bound_source_sha256']==source_hash]
        # Refresh only provenance for the exact unchanged bound source. Core
        # repair history must not disappear when a passing plan is reused.
        if policy.executable_skills is not None:
            selected = policy.executable_skills.select(args.prompt)
            if selected and selected['provenance']['bound_source_sha256'] == source_hash:
                for item in policy._executable_reuse:
                    item.update(selected['provenance'])
    events = []
    def event(kind, message, **detail):
        events.append(dict(kind=kind, message=message, time=time.time(), **detail))
        (output/'policy-events.json').write_text(json.dumps(events, indent=2)+'\n')
        compact = {k:detail[k] for k in ('attempt','elapsed_s') if k in detail}
        if isinstance(detail.get('result'),dict):
            compact.update({k:detail['result'].get(k) for k in ('status','failing_stage','reason')})
        print(json.dumps(dict(kind=kind, message=message, **compact)), flush=True)
    policy.interaction_sink = event
    packets = [];controller = None
    review_recorder = native_review_recorder = None
    review_recording_result = None
    postpark_evaluation = None
    class RecordedAPI(HttpSessionAPI):
        def create_session(self, prompt, run_duration_s=300):
            # v1 has no caller-name field. Its supported queue prompt carries
            # the label; the coding policy still receives the original task.
            return super().create_session(run_name+' — '+prompt, run_duration_s=run_duration_s)

        def submit_trajectory(self, *parameters, **keywords):
            nonlocal review_recorder, native_review_recorder
            if review_recorder is None:
                # Use ASPIRE's own writer, with timestamped JPEG evidence kept
                # separately. This observer outlives the task subprocess and
                # follows the controller's normal parking without commands.
                import io
                import numpy as np
                from PIL import Image
                sys.path.insert(0, str(Path(config['aspire'])/'aspire/real'))
                from cap.agent.recorder import ScriptRecorder
                native_review_recorder = ScriptRecorder.from_env(
                    ['top', 'left', 'right'], output/'review-through-parking/video')
                native_review_recorder.start()
                def push(frame):
                    native_review_recorder.push_frame(frame.name,
                        np.asarray(Image.open(io.BytesIO(frame.jpeg)).convert('RGB')))
                urls = {role:config['origin'].rstrip('/')+'/v1/robots/'+quote(config['robot_id'], safe='')+
                        '/cameras/'+role+'.jpg' for role in ('top', 'left', 'right')}
                review_recorder = SessionReviewRecorder(config['origin'], urls,
                    output/'review-through-parking/frames', sink=push)
                review_recorder.start()
            review_recorder.mark('packet_submission', trajectory_id=parameters[3])
            # Capability is retained inside HttpSessionAPI, never serialized.
            packet = dict(parameters=list(parameters), keywords=keywords, sent_at=time.time())
            packets.append(packet)
            try:
                packet['result'] = super().submit_trajectory(*parameters, **keywords)
                return packet['result']
            except Exception as exc:
                packet['error'] = type(exc).__name__+': '+str(exc)
                raise
            finally:
                packet['returned_at'] = time.time()
                (output/'api-command-results.json').write_text(json.dumps(packets, indent=2)+'\n')
    try:
        if args.plan_only:
            outcome = policy._coding_loop()
        else:
            api = RecordedAPI(config['origin'], robot_id=config['robot_id'], supports_trajectories=True)
            controller = RunnerController(api, robot_id=config['robot_id'], submit_attempts=1,
                hardware_control_enabled=True, share_conversation=False)
            controller.join_and_run(policy, args.prompt, run_duration_s=300)
            progress_at = 0
            while controller._worker.is_alive():
                controller._worker.join(.2)
                if time.monotonic() >= progress_at:
                    status = controller.status()
                    print(json.dumps(dict(status=status.get('status'), packets_sent=len(packets),
                        error=status.get('error'))), flush=True)
                    progress_at = time.monotonic()+15
            outcome = policy._outcome
    except Exception as exc:
        outcome=dict(status='FAILED' if packets else 'BLOCKED',success=False,
            reason=type(exc).__name__+': '+str(exc),physical_motion_calls=len(packets))
    finally:
        if controller:
            controller.stop('single_prompt_trial_finished')
            if policy._thread:policy._thread.join(HarnessClient.CANCEL_GRACE_SECONDS+7)
            if policy._outcome is not None:outcome=policy._outcome
        if review_recorder:
            read_api = HttpSessionAPI(config['origin'], robot_id=config['robot_id'], timeout_s=2.)
            try:
                review_recording_result = review_recorder.finish_after_stop(
                    lambda:read_api.get_robot_observation(config['robot_id']))
            finally:
                review_recorder.close()
                native_review_recorder.stop()
            # Once parking is actually observed, assess a fresh unobstructed
            # scene with the exact generated source and its live task geometry.
            # This separate harness has no lease or motion bridge.
            if review_recording_result.get('status') == 'PARKING_OBSERVED':
                for attempt in reversed(policy._attempts):
                    run = Path(attempt['directory'])
                    definition = run/'execute/task_definition.json'
                    if definition.exists():
                        postpark_evaluation = harness(mode='evaluate',
                            directory=output/'postpark-evaluation', task=args.prompt,
                            program=run/'generated_program.py', queries=run/'queries.json',
                            task_definition=definition, cache_root=output/'policy')
                        (output/'postpark-evaluation-result.json').write_text(
                            json.dumps(postpark_evaluation, indent=2)+'\n')
                        break
        policy.close_transport()
    runner_status=controller.status() if controller else None
    if runner_status and runner_status.get('error'):
        outcome=dict(outcome or {},status='FAILED',success=False,
            reason=runner_status['error'],harness_reason=(outcome or {}).get('reason'),
            physical_packets_submitted=len(packets))
    completion = completed_task_outcome(outcome or {}, review_recording_result, postpark_evaluation) if args.execute else None
    if args.execute and packets and policy._attempts:
        attempt=Path(policy._attempts[-1]['directory'])
        reply=json.loads((attempt/'coding-response.json').read_text())
        reviewed=dict(outcome or {}, **completion)
        if postpark_evaluation:
            reviewed['postpark_evaluation']=postpark_evaluation
            reviewed['outcome_scene']=postpark_evaluation.get('scene', {})
        # A release-time success is provisional until the complete parking
        # transition is assessed. Do not retain a false PHYSICAL_SUCCESS skill.
        policy._persist_skill(reply, reviewed)
        if completion['success']:
            policy._physical_feedback_path().unlink(missing_ok=True)
        else:
            policy._save_physical_failure(reply, reviewed, attempt)
    receipt = dict(task=args.prompt, display_name=run_name,
        coding_baseline=config.get('coding_baseline','local_code_skills'),
        original_upstream_skills=bool(config.get('original_upstream_skills') or args.upstream_baseline),
        skill_directory=str(policy.skill_directory.resolve()),
        reference_run=str(args.reference_run.resolve()) if args.reference_run else None,
        program_response=str(args.program_response.resolve()) if args.program_response else None,
        program_response_sha256=hashlib.sha256(args.program_response.read_bytes()).hexdigest() if args.program_response else None,
        executable_reuse=policy._executable_reuse,
        queue_prompt=run_name+' — '+args.prompt,
        initialize_before_observation=args.initialize_before_observation,
        mode='plan_only' if args.plan_only else 'queued_trial',
        model=args.model, aspire_commit='f4c8939aab0af9b97690c561bd80e282940f7886',
        model_conversation_id=getattr(policy._coding_provider,'_thread_id',None) or policy.resume_conversation,
        physical_packets_submitted=len(packets), automatic_motion_retries=0,
        model_weights_changed=False, result=outcome,
        preparation_result=policy._preparation_result,
        review_through_parking=review_recording_result,
        postpark_evaluation=postpark_evaluation,
        task_completion=completion,
        recorded_scene=str(args.recorded_scene.resolve()) if args.recorded_scene else None,
        runner_status=runner_status)
    (output/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    if controller and receipt['runner_status'].get('session_id'):
        for name, read in (('episode', api.get_episode), ('episode-trace', api.get_episode_trace)):
            try:(output/(name+'.json')).write_text(json.dumps(read(receipt['runner_status']['session_id']), indent=2)+'\n')
            except Exception as exc:(output/(name+'-error.txt')).write_text(str(exc))
    if policy.skill_learning:
        learning=policy.skill_learning.stage_run(output/'receipt.json')
        (output/'skill-learning.json').write_text(json.dumps(learning,indent=2)+'\n')
    print(json.dumps(dict(output=str(output),result={k:outcome.get(k) for k in
        ('status','success','planning_success','physical_motion_calls','failing_stage','reason')}
        if outcome else None, task_completion=completion, physical_packets_submitted=len(packets))))
    return 0 if outcome and (outcome.get('planning_success') if args.plan_only else completion.get('success')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
