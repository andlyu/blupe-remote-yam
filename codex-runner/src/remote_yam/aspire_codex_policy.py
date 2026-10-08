"""Prompt → Codex program → native ASPIRE test/repair → existing API policy.

Uses the existing CodexAdapter subscription/JSONL/resume transport. See pinned
ASPIRE real/AGENTS.md and OpenAI's build_iterative_repair_loops_with_codex.
No separate coding service, API key or robot session client is given to codegen.
"""
import ast
import base64
import difflib
import hashlib
import json
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import quote

from .aspire_api_policy import AspireApiPolicy, BridgeServer
from .codex_policy import CodexAdapter, DEFAULT_MODEL
from .aspire_skill_learning import PROMOTION_SCHEMA, COORDINATOR_INSTRUCTIONS
from .aspire_lineage import LINEAGE_SCHEMA, LINEAGE_INSTRUCTIONS, retrieval_catalog, record_lineage
from .aspire_review_recording import SessionReviewRecorder, completed_task_outcome
from .aspire_progress import HarnessProgress
from .aspire_repair_trace import AttemptTrace
from .aspire_recovery_flow import task_resolution
from .providers import PolicyComplete
from .aspire_skill_review import DeferredSkillReview, SubscriptionSkillCoordinator, promotion_knowledge

PROGRAM_SCHEMA = dict(type='object', additionalProperties=False, properties=dict(
    action=dict(type='string', enum=['program','rerun','give_up']),
    source=dict(type='string'), summary=dict(type='string'), lesson=dict(type='string'),
    lineage=LINEAGE_SCHEMA,
    queries=dict(type='array',items=dict(type='object',additionalProperties=False,
        properties=dict(name=dict(type='string'),query=dict(type='string')),required=['name','query']))),
    required=['action','source','summary','lesson','queries','lineage'])


def coding_feedback(value):
    """Keep exact evidence on disk; do not embed pixel rasters in model text."""
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            if key in ('scene','initial_scene','outcome_scene') and isinstance(item,dict):
                result[key]={name:item[name] for name in ('status','snapshot','images') if name in item}
                result[key]['context_in_current_scene']=True
            elif key=='mask' and isinstance(item,list):
                result[key]=dict(height=len(item),width=len(item[0]) if item else 0,
                    evidence='Exact raster is saved in the ASPIRE segmentation artifacts; camera images are attached')
            elif key in ('reason','traceback','feedback') and isinstance(item,str) and len(item)>4000:
                result[key]=(item[:3000]+'\n[Model excerpt: '+str(len(item))+
                    ' characters in the preserved full artifact; inspect structured native diagnostics.]\n'+item[-1000:])
            else:result[key]=coding_feedback(item)
        return result
    if isinstance(value,list):return [coding_feedback(item) for item in value]
    return value


def model_retrieval_catalog(entries):
    """Code and exact citations once; full recursive provenance stays on disk.

    Saved manifests can contain previous retrievals, lineage and validation
    history, each embedding the same whole programs again. Those aren't new
    code components and exceeded the CLI's 1 Mi-character turn-input limit.
    """
    result=[]
    keys=('id','version','kind','title','topic','why','scope','limits','task','summary',
          'lesson','validation','evidence','source_sha256','source_path','model')
    for entry in entries:
        item={key:entry[key] for key in keys if key in entry}
        item['snippets']=[]
        for snippet in entry.get('snippets',[]):
            snippet=dict(snippet)
            if snippet.get('original_code')==snippet.get('code'):
                snippet.pop('original_code',None)
            item['snippets'].append(snippet)
        item['validation_history']=[{key:record[key] for key in ('validation','evidence') if key in record}
            for record in entry.get('validation_history',[])]
        result.append(item)
    return result


def validate_program(reply):
    # Preserve old saved responses; missing lineage is explicitly unknown.
    required=set(PROGRAM_SCHEMA['required'])-{'lineage'}
    if not isinstance(reply,dict) or set(reply) not in (required, required|{'lineage'}):
        raise ValueError('Invalid coding response schema')
    if reply['action'] not in ('program','rerun','give_up') or any(not isinstance(reply[k],str) for k in ('source','summary','lesson')):
        raise ValueError('Invalid coding response fields')
    if reply['action']=='give_up': return
    tree=ast.parse(reply['source'])
    compile(tree,'generated_program.py','exec')
    functions={node.name for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
    if not {'build_task','evaluate'}<=functions:
        raise ValueError('Program must define build_task(tools) and evaluate(tools, task)')
    queries=reply['queries']
    if not isinstance(queries,list) or not queries:
        raise ValueError('Program must declare perception queries')
    names=set()
    for entry in queries:
        if not isinstance(entry,dict) or set(entry)!= {'name','query'} or not isinstance(entry['name'],str) or not entry['name'].isidentifier() or not isinstance(entry['query'],str) or not entry['query'].strip() or entry['name'] in names:
            raise ValueError('Invalid perception query registry')
        names.add(entry['name'])


def effective_program(source):
    """Ignore formatting, comments and docstrings when detecting no progress."""
    tree=ast.parse(source)
    for node in ast.walk(tree):
        body=getattr(node,'body',None)
        if isinstance(body,list) and body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):
            node.body=body[1:]
    return ast.dump(tree,include_attributes=False)


class AspireCodexPolicy(AspireApiPolicy):
    provider_name='codex'

    def __init__(self, *, harness, calibration, robot_id, task=None, model=DEFAULT_MODEL,
                 directory, instructions, skill_directory, max_revisions=4,
                 allow_hardware=None, plan_only=False, generator=None,
                 initial_feedback=None, resume_conversation=None, validated_program=None,
                 coding_timeout_s=180, display_name='Codex + ASPIRE',
                 initialize_before_observation=False, vision_session=None, owns_vision_session=True,
                 skill_learning=None, skill_coordinator=None, executable_skills=None,
                 skill_review_timing='before_launch', selected_executable=None, recovery_factory=None,
                 repair_outcomes=False, repair_vision_factory=None, automatic_recovery=False,
                 code_revision_loop=False, code_revision_limit=None,execution_environment=None,
                 published_skills=None):
        if execution_environment not in (None,'local','web'):raise ValueError('Invalid ASPIRE execution environment')
        self.execution_environment=execution_environment
        if execution_environment is not None:
            code_revision_loop=execution_environment=='local'
            repair_outcomes=code_revision_loop and not plan_only
            automatic_recovery=automatic_recovery and code_revision_loop
            if code_revision_loop:recovery_factory=None
        self.harness=harness
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.instructions=instructions
        if vision_session and 'SELECTED SAM 3 PERCEPTION' not in self.instructions:
            self.instructions+='\nSELECTED SAM 3 PERCEPTION:\n'
            self.instructions+=('The selected segmenter is facebook/sam3 on RunPod. It returns native binary '
                'instance masks for concise positive object descriptions. Use short descriptions with the '
                'object name, color and shape; its text encoder has a 32-token context. Long descriptions '
                'are truncated and the effective text is saved with the masks. Polygon-format instructions, '
                'multi-patch tracing requests and geometric face selection belong to the previous contour '
                'interface and do not define SAM 3 output geometry. Inspect the returned instance against '
                "the actual RGB-D frame before deriving task geometry. Preserve the task's target identity "
                'and occlusion evidence when adapting reused programs.\n')
        self.published_skills=published_skills
        self.skill_directory=Path(skill_directory)
        self.skill_learning=skill_learning
        if skill_review_timing not in ('before_launch', 'after_run'):
            raise ValueError('Invalid ASPIRE skill review timing')
        self.skill_review_timing=skill_review_timing
        self._topic_snapshot=None
        self._deferred_review=None
        self._deferred_review_scheduled=False
        self.executable_skills=executable_skills
        self._executable_reuse=[]
        self._retrieved_lineage=[]
        self._validated_lineage=None
        self._task_updates=[]
        self._current_lineage=None
        self._live_stage=None
        self.recovery_factory=recovery_factory
        self._recovery=None
        self._recovery_attempts=[]
        self._attempt_traces={}
        self._saved_motion_requests=0
        self._controller_failure=False
        self.repair_outcomes=repair_outcomes
        self.automatic_recovery=automatic_recovery
        self.code_revision_loop=code_revision_loop
        self.code_revision_limit=max_revisions+1 if code_revision_limit is None else code_revision_limit
        if type(self.code_revision_limit) is not int or not 0<=self.code_revision_limit<=24:
            raise ValueError('Invalid task coding-request budget')
        self._coding_requests=0
        self.repair_vision_factory=repair_vision_factory
        self._offline_repair=None
        self.skill_coordinator=skill_coordinator or self._coordinate_skill_promotion
        self._custom_skill_coordinator=skill_coordinator
        self._promotion_provider=None
        self._promotion_history=[]
        if type(max_revisions) is not int or not 0<=max_revisions<=12:
            raise ValueError('Invalid coding revision budget')
        self.max_revisions=max_revisions;self.plan_only=plan_only
        if type(coding_timeout_s) not in (int, float) or not 0 < coding_timeout_s <= 1800:
            raise ValueError('Invalid coding timeout')
        self.coding_timeout_s=float(coding_timeout_s)
        self.display_name=display_name
        self.generator=generator
        self.initial_feedback=initial_feedback
        self.resume_conversation=str(uuid.UUID(resume_conversation)) if resume_conversation else None
        self.validated_program=validated_program
        if validated_program:validate_program(validated_program)
        self._saved_executable_only=selected_executable is not None
        if selected_executable:
            if plan_only or validated_program is not None:
                raise ValueError('Saved executable launch requires execution mode and one program source')
            self.validated_program=selected_executable['response']
            validate_program(self.validated_program)
            provenance=selected_executable['provenance']
            self._validated_lineage=record_lineage(self.validated_program,executable=provenance)
            if any(use['verification']!='exact_source_match' for use in self._validated_lineage['used']):
                raise ValueError('Saved executable differs from its selected source hash')
            self._executable_reuse.append(provenance)
            (self.directory/'executable-reuse.json').write_text(json.dumps(provenance,indent=2)+'\n')
            initialize_before_observation=True
        if initialize_before_observation and (plan_only or self.validated_program is None):
            raise ValueError('Initialized-scene planning requires a previously planned agent program and execution mode')
        self.initialize_before_observation=initialize_before_observation
        self.vision_session=vision_session
        self._owns_vision_session=owns_vision_session
        self._history=[];self._coding_provider=None;self._attempts=[]
        self._preparation=None;self._preparation_result=None;self._phase='idle'
        self._postpark_observe=None;self._review_origin=None
        self._review_recorder=None;self._review_factory=SessionReviewRecorder
        self._review_closed=threading.Event();self._review_finished=False
        self.review_cancelled=lambda:False
        self._task_completion=None;self._review_phase=None
        super().__init__(run_harness=self._coding_loop,calibration=calibration,
            robot_id=robot_id,task=task,recording_path=self.directory/'runner',allow_hardware=allow_hardware)
        self.model=model
        self.reasoning_effort='high';self.response_speed='standard'

    def public_config(self):
        status = getattr(self.vision_session, 'public_status', None)
        vision = status() if callable(status) else None
        return {**super().public_config(), 'provider':'codex','model':self.model,
            **({'launch_route':self.launch_route} if getattr(self,'launch_route',None) else {}),
            'reasoning_effort':self.reasoning_effort,'response_speed':self.response_speed,
            'display_name':self.display_name,'implementation':('saved_aspire_fresh_live_plan_execute'
                if self._saved_executable_only else 'codex_generate_harness_plan_repair_execute'),
            'authentication':'chatgpt','transport':'codex_exec','plan_only':self.plan_only,
            'automatic_retry_limit':getattr(self,'automatic_retry_limit',1 if self.automatic_recovery else 0),
            'code_revision_loop':self.code_revision_loop,'code_revision_limit':self.code_revision_limit,
            'coding_requests':self._coding_requests,'execution_environment':self.execution_environment,
            'phase':self._review_phase or (self._phase if not self._finished else 'complete'),
            'task_outcome':self._task_completion,
            'preparation_status':(self._preparation_result or {}).get('status'),
            'initialize_before_observation':self.initialize_before_observation,
            'coding_attempts':len(self._attempts),'skill_directory':str(self.skill_directory),
            'task_progress':{'task':self.task,'updates':list(self._task_updates),
                'resolution':task_resolution(self.directory,self._task_completion,self._review_phase,self._recovery_attempts),
                'lineage':self._current_lineage,'outcome':self._task_completion,
                'stage':self._stage_status(), 'attempts':self._public_attempts(),
                'recovery':self._recovery.public_config() if self._recovery else None,
                **({'vision':vision} if isinstance(vision,dict) else {})},
            'executable_skills':{'enabled':self.executable_skills is not None,
                'selection':'bind_compatible_inputs_before_code_generation'},
            'skill_learning':{'enabled':self.skill_learning is not None,
                'workflow':'upstream_topic_promotion',
                'coordinator':'codex_subscription_after_run' if self.skill_review_timing=='after_run' else 'codex_subscription_before_next_task',
                'review_timing':self.skill_review_timing,
                'startup_library_sha256':(self._topic_snapshot or {}).get('library_sha256'),
                'background_review':dict(self._deferred_review.status) if self._deferred_review else None},
            **({'segmentation_backend':'runpod_sam3','segmentation_model':'facebook/sam3',
                'segmentation_endpoint_id':self.vision_session.client.endpoint_id,
                'vision_ready':self.vision_session.ready_result is not None} if self.vision_session else {})}

    def prepare_before_session(self, prompt):
        if self.task is None:self.task=prompt
        if self.task!=prompt:raise ValueError('ASPIRE prompt differs from configured task')
        self._check_cancelled()
        if self._saved_executable_only:
            self._publish_stage(dict(stage='queue_home',title='Waiting for queue and Home',
                detail='Vision warmup overlaps queue admission and robot initialization.'))
            self._task_update('Program found.',
                'Saved executable SHA256 '+self._executable_reuse[0]['bound_source_sha256']+'. No code generation requested.',
                'Join the queue, initialize/Home, then capture and fully plan from the live scene.')
        if self.skill_learning and self.skill_review_timing=='before_launch':
            self._task_update('Reviewing your prompt…',
                'Working through your prompt and preparing the next steps.',
                'Use the current scene to prepare the next steps.')
        self._review_prior_skills(prompt)
        if self.vision_session:
            self.vision_session.start(cancelled=self.cancelled, emit=self._emit)
        try:
            self._prepare_program_before_session(prompt)
            # Saved programs need Home before their fresh scene capture. Let
            # queue/Home overlap cold GPU startup; the live loop still waits
            # for model readiness before invoking the execution harness.
            if self.vision_session and not self.initialize_before_observation:
                self.vision_session.wait_ready()
        except BaseException:
            if self.vision_session and self._owns_vision_session:self.vision_session.close()
            raise

    def configure_postpark_review(self, origin, observe, *, recorder_factory=SessionReviewRecorder):
        """Local UI lifecycle; the standalone trial launcher owns its own review."""
        self._review_origin=origin
        self._postpark_observe=observe
        self._review_factory=recorder_factory

    def _start_postpark_recording(self):
        if (self.plan_only or self.allow_hardware is not True or self._postpark_observe is None
                or self._review_recorder is not None or self._review_closed.is_set()):
            return
        urls={role:self._review_origin.rstrip('/')+'/v1/robots/'+quote(self.robot_id,safe='')+
            '/cameras/'+role+'.jpg' for role in ('top','left','right')}
        try:
            self._review_recorder=self._review_factory(self._review_origin,urls,
                self.directory/'review-through-parking')
            self._review_recorder.start()
        except Exception as exc:
            if self._review_recorder:self._review_recorder.close()
            self._review_recorder=None
            self._task_update('Parking recording could not start.',type(exc).__name__,
                'Placement after parking will remain unverified without recorded evidence.')

    def review_after_session_stop(self):
        """Read only; called by the controller worker after its normal stop."""
        if self._postpark_observe is None or self._review_finished:
            return
        self._review_finished=True
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(22.)  # Allow the stopped native writer to close.
        native=dict(self._outcome or {})
        if self._recovery_attempts:
            # Astra may have changed the scene. The saved program's evaluator
            # cannot attribute that new placement to the failed ASPIRE attempt.
            parking=self._review_recorder.finish_after_stop(self._postpark_observe,
                cancelled=self._review_closed.is_set) if self._review_recorder else {}
            if self._review_recorder:self._review_recorder.close()
            completion=dict(status='UNVERIFIED',success=False,checked_at=time.time(),
                reason='Astra recovery is a separate attempt; independent after-parking task verification is not recorded.',
                native_result=native,recovery_attempts=self._recovery_attempts,parking=parking)
            self._task_completion=completion
            (self.directory/'task-completion.json').write_text(json.dumps(dict(task=self.task,**completion),indent=2)+'\n')
            self._task_update('Recovery after-parking outcome: UNVERIFIED.',completion['reason'],
                'Review both attempts; Astra model completion does not prove retained placement.')
            self._save_attempt_history()
            return
        if not native.get('physical_motion_calls',0):
            parking=self._review_recorder.finish_after_stop(self._postpark_observe,
                cancelled=self._review_closed.is_set) if self._review_recorder else {}
            if self._review_recorder:self._review_recorder.close()
            if self.code_revision_loop and self._attempts:
                completion=dict(status='UNVERIFIED',success=False,reason=str(native.get('reason') or native.get('status')),
                    checked_at=time.time(),failed_checks=[],images=[],native_result=native,
                    parking=parking,physical_motion_calls=0,pre_motion_failure=True)
                self._task_completion=completion
                (self.directory/'task-completion.json').write_text(json.dumps(dict(task=self.task,**completion),indent=2)+'\n')
                if native.get('status')=='RECOVERY_BLOCKED':
                    from .aspire_recovery_flow import geometry_blocker,read
                    task=read(Path(self._attempts[-1]['directory'])/'execute/task_definition.json')
                    blocker=geometry_blocker(task,self.task)
                    if blocker:(self.directory/'task-resolution.json').write_text(json.dumps(blocker)+'\n')
                elif (self.repair_outcomes and native.get('status') not in ('CANCELLED','SCENE_CAPTURE_FAILED','HARNESS_ERROR')
                        and not native.get('failure_kind') and not self._controller_failure and not self._failure
                        and not self._saved_motion_requests and not self.review_cancelled() and not self._review_closed.is_set()):
                    self._repair_outcome(completion,None,native)
            return
        self._review_phase='reviewing_after_parking'
        self._task_update('Checking the outcome after normal parking.',
            'Task commands are finished. The observer uses cameras and state only.',
            'Evaluate a fresh parked scene with the exact executed program.')
        parking=dict(status='UNVERIFIED',physical_commands=0)
        evaluation=None
        scene_still_parked=False
        try:
            if self._review_recorder:
                parking=self._review_recorder.finish_after_stop(self._postpark_observe,
                    cancelled=self._review_closed.is_set)
            if parking.get('status')=='PARKING_OBSERVED' and not self._review_closed.is_set():
                for item in reversed(self._attempts):
                    attempt=Path(item['directory'])
                    definition=attempt/'execute/task_definition.json'
                    if definition.exists():
                        evaluation=self._run_harness(mode='evaluate',directory=self.directory/'postpark-evaluation',
                            task=self.task,program=attempt/'generated_program.py',queries=attempt/'queries.json',
                            task_definition=definition,cache_root=self.directory,
                            cancelled=self._review_closed.is_set)
                        observed=(evaluation or {}).get('scene',{}).get('context',{}).get('measured_observation',{})
                        final_observation=self._postpark_observe()
                        scene_still_parked=(observed.get('source')=='hardware' and observed.get('mode')=='DISABLED'
                            and final_observation.get('source')=='hardware' and final_observation.get('mode')=='DISABLED')
                        break
        except Exception as exc:
            parking['review_error_type']=type(exc).__name__
        finally:
            if self._review_recorder:self._review_recorder.close()
        if self._review_closed.is_set():
            parking['status']='UNVERIFIED'
        completion=completed_task_outcome(native,parking,evaluation)
        if evaluation and not scene_still_parked:
            completion.update(success=False,status='FAILED' if native.get('status')=='FAILED' else 'UNVERIFIED',
                reason='Station parking was not confirmed throughout this read-only evaluation')
        evidence=(evaluation or {}).get('placement_evidence',{}).get('evidence',{})
        failed=[name for name,passed in evidence.get('checks',{}).items() if passed is not True]
        if not completion['success']:
            if evaluation and not scene_still_parked:pass
            elif failed:completion['reason']='Post-parking evaluation did not confirm: '+', '.join(failed)
            elif parking.get('status')!='PARKING_OBSERVED':completion['reason']='Parking and fresh camera evidence were not established'
            elif (evaluation or {}).get('status')!='EVALUATION_ONLY':completion['reason']='Fresh post-parking evaluation did not complete'
        completion.update(checked_at=time.time(),evidence=str(self.directory/'task-completion.json'),
            failed_checks=failed,physical_commands_sent_by_review=0)
        images=[]
        scene=(evaluation or {}).get('scene',{})
        for role,path in scene.get('images',{}).items():
            source=str(Path(path).resolve())
            stamp=scene.get('context',{}).get('camera_metadata',{}).get(role,{}).get('metadata',{}).get('captured_at')
            images.append(dict(camera=role,source=source,captured_at=stamp,
                url='/api/aspire-lineage/artifacts/'+hashlib.sha256(source.encode()).hexdigest()))
        completion['images']=images
        receipt=dict(task=self.task,**completion,result=native,native_result=native,parking=parking,
            postpark_evaluation=evaluation)
        (self.directory/'task-completion.json').write_text(json.dumps(receipt,indent=2)+'\n')
        (self.directory/'postpark-review.json').write_text(json.dumps(dict(
            status=completion['status'],recorded_at=completion['checked_at'],
            validation_scope='Automatic fresh post-parking evaluation; sparse recording preserves capture times, not continuous proof.',
            images=[item['source'] for item in images],postpark_checks=evidence.get('checks',{})),indent=2)+'\n')
        self._task_completion=completion
        self._outcome=dict(native,**completion,postpark_evaluation=evaluation)
        if evaluation and evaluation.get('scene'):self._outcome['outcome_scene']=evaluation['scene']
        # The release-time result and original source remain untouched.
        if self._attempts and not self._review_closed.is_set():
            attempt=Path(self._attempts[-1]['directory'])
            reply=json.loads((attempt/'coding-response.json').read_text())
            self._persist_skill(reply,self._outcome)
            if completion['success']:self._physical_feedback_path().unlink(missing_ok=True)
            else:self._save_physical_failure(reply,self._outcome,attempt)
        self._review_phase='complete'
        self._task_update('After-parking outcome: '+completion['status']+'.',completion['reason'],
            'Review the saved post-parking images and task-completion receipt.',result=completion)
        loop=self.directory/'coding-loop.json'
        if loop.exists():
            saved=json.loads(loop.read_text())
            saved.update(task_updates=self._task_updates,task_completion=completion)
            loop.write_text(json.dumps(saved,indent=2)+'\n')
        if self.skill_learning:
            findings=self.skill_learning.stage_policy(self.directory,self.task,self._attempts)
            (self.directory/'skill-learning.json').write_text(json.dumps(findings,indent=2)+'\n')
            self._schedule_deferred_review()
        if (not completion['success'] and self.repair_outcomes and not self._review_closed.is_set()
                and not self._controller_failure and not self._failure and not self.review_cancelled()
                and native.get('status') not in ('CANCELLED','HARNESS_ERROR','SCENE_CAPTURE_FAILED')
                and not native.get('failure_kind') and not native.get('ambiguous_command_state')):
            self._repair_outcome(completion,evaluation,native)

    def _repair_outcome(self,completion,evaluation,native):
        """Bounded subscription repair after parking; this path cannot execute."""
        if self._offline_repair is not None or not self._attempts or any(
                a.get('mode')=='offline_code_repair' for a in self._recovery_attempts):return
        remaining=self.code_revision_limit-self._coding_requests
        if self.code_revision_loop and remaining<=0:
            self._task_update('Task code-revision limit reached.',completion['reason'],
                'No coding requests remain for this submission. Preserve the outcome; no unchanged-source replay.')
            self._save_attempt_history()
            return
        previous=Path(self._attempts[-1]['directory'])/'coding-response.json'
        reply=json.loads(previous.read_text())
        definition=Path(self._attempts[-1]['directory'])/'execute/task_definition.json'
        task_definition=json.loads(definition.read_text()) if definition.exists() else {}
        scene=(evaluation or {}).get('scene') or native.get('outcome_scene') or native.get('initial_scene') or native.get('scene') or {}
        feedback=dict(status='PREVIOUS_ATTEMPT',reason=completion['reason'],
            failed_checks=completion.get('failed_checks',[]),previous_source=reply['source'],
            original_source_sha256=hashlib.sha256(reply['source'].encode()).hexdigest(),
            native_result=coding_feedback(native),
            postpark_evaluation=coding_feedback({k:evaluation[k] for k in ('status','success','reason',
                'placement_evidence','scene') if k in evaluation}) if evaluation else None,
            original_evidence=str(self.directory),
            measured_state=coding_feedback(scene.get('context',{})),
            latest_live_task_definition=coding_feedback(task_definition),latest_live_task_definition_path=str(definition),
            review_images=[p for p in ([i['source'] for i in completion.get('images',[])] or list(scene.get('images',{}).values())) if Path(p).is_file()],
            physical_commands_sent=0,diagnosis_required=(
                'Distinguish actual placement failure from camera/mask/metric uncertainty using the recorded '
                'parked evidence and fresh scene. Unknown evidence remains unknown. Repair the evaluator '
                'when its assumptions caused missing confirmation; do not blindly change motion. Explain '
                'the diagnosis and changes in summary/lesson. Preserve verification checks unless actual '
                'evidence establishes a contract defect. Revise the actual Python using this latest run, '
                'not only an earlier failure. Test the entire revised native plan offline. If no justified '
                'revision is possible, explain the concrete evidence blocker; do not propose blind replay. '
                'A program fix must meaningfully change its behavior or effective inputs/strategy. Formatting, '
                'comments and docstrings are not fixes. If evidence specifically supports retaining the '
                'effective program and rerunning it, return action=rerun with an explicit evidence-based '
                'reason in summary/lesson. That is a justified retry, not a new repaired revision.'))
        record=dict(id='astra-repair-1',policy='astra',mode='offline_code_repair',
            parent_attempt_id='aspire-'+str(self._attempts[-1]['attempt']),status='running',
            started_at=time.time(),code_generation_requested=True,physical_motion_calls=0,
            previous_source_sha256=feedback['original_source_sha256'],previous_result={
                k:completion[k] for k in ('status','reason','failed_checks') if k in completion})
        self._recovery_attempts.append(record)
        self._attempt_traces[record['id']]=AttemptTrace(record,self.directory)
        self._review_phase='repairing_after_parking'
        self._publish_stage(dict(stage='offline_repair',title='Astra is diagnosing the unverified outcome',
            detail='Recorded parked evidence and exact failed source are supplied. Repair and planning only.'))
        self._task_update('Unverified outcome handed to Astra for repair.',completion['reason'],
            'Diagnose placement versus observation uncertainty; validate revised code offline without task motion.')
        self._save_attempt_history()
        repair=None
        try:
            repair=AspireCodexPolicy(harness=self.harness,calibration=self.report,robot_id=self.robot_id,
                task=self.task,model=self.model,directory=self.directory/'outcome-repair',
                instructions=self.instructions+'\nOUTCOME REPAIR CONTRACT: '+feedback['diagnosis_required'],
                skill_directory=self.skill_directory,max_revisions=self.max_revisions,
                allow_hardware=False,plan_only=True,generator=self.generator,initial_feedback=feedback,
                resume_conversation=getattr(self._coding_provider,'_thread_id',None) or self.resume_conversation,
                coding_timeout_s=self.coding_timeout_s,executable_skills=self.executable_skills,
                code_revision_loop=self.code_revision_loop,code_revision_limit=max(0,remaining),
                vision_session=self.repair_vision_factory() if self.repair_vision_factory else None)
            self._offline_repair=repair
            repair.cancelled=lambda:self._review_closed.is_set() or self.review_cancelled()
            repair.interaction_sink=lambda kind,message,**details:self._forward_recovery_event(record,kind,message,**details)
            repair._stream_coding_progress=True
            repair.reasoning_effort=self.reasoning_effort;repair.response_speed=self.response_speed
            result=repair._coding_loop()
            record.update(status='PLAN_VALIDATED' if result.get('planning_success') is True else 'FAILED',
                result={k:result[k] for k in ('status','reason','planning_success','physical_motion_calls') if k in result},
                repair_directory=str(repair.directory),coding_loop=str(repair.directory/'coding-loop.json'))
            if repair._attempts:
                final=Path(repair._attempts[-1]['directory'])
                revised=json.loads((final/'coding-response.json').read_text())
                record.update(source=str(final/'generated_program.py'),
                    source_sha256=hashlib.sha256(revised['source'].encode()).hexdigest(),
                    diagnosis=revised['summary'],lesson=revised['lesson'])
                diff=''.join(difflib.unified_diff(reply['source'].splitlines(True),
                    revised['source'].splitlines(True),fromfile='failed-program.py',tofile='revised-program.py'))
                (repair.directory/'repair.diff').write_text(diff)
                record['source_changed']=reply['source']!=revised['source']
                record['effective_program_changed']=effective_program(reply['source'])!=effective_program(revised['source']) if revised['action']!='give_up' else False
                if self.code_revision_loop and record['status']=='PLAN_VALIDATED':
                    if revised['action']=='rerun' and not record['effective_program_changed'] and revised['summary'].strip():
                        record.update(status='RERUN_VALIDATED',mode='offline_rerun_diagnosis',rerun_reason=revised['summary'])
                    elif not record['effective_program_changed']:
                        record.update(status='NO_PROGRESS',reason='No meaningful correction was produced. An unchanged effective program is not a new fix.')
                    elif revised['action']=='rerun':
                        record.update(status='NO_PROGRESS',reason='The rerun response changed the effective program; its correction must be diagnosed explicitly.')
                if record['status'] in ('PLAN_VALIDATED','RERUN_VALIDATED'):
                    root=self.skill_directory/'.repaired-programs';root.mkdir(parents=True,exist_ok=True)
                    receipt=dict(kind='justified_rerun' if record['status']=='RERUN_VALIDATED' else 'code_repair',
                        rerun_reason=record.get('rerun_reason'),task=self.task,status='PLAN_VALIDATED',response=dict(revised,action='program'),
                        source_sha256=record['source_sha256'],source=record['source'],
                        base_core_sha256=(self._executable_reuse[0] if self._executable_reuse else {}).get('base_core_sha256',
                            (self._executable_reuse[0] if self._executable_reuse else {}).get('core_source_sha256',getattr(self.executable_skills,'digest',None))),
                        base_inputs=(self._executable_reuse[0] if self._executable_reuse else {}).get('inputs'),
                        base_context_sha256=(self._executable_reuse[0] if self._executable_reuse else {}).get('base_context_sha256',getattr(self.executable_skills,'context_sha256',None)),
                        parent_run=str(self.directory),parent_attempt_id=record['parent_attempt_id'],
                        original_outcome=record['previous_result'],diagnosis=revised['summary'],
                        authorship=(repair._attempts[-1].get('lineage') or {}).get('authorship'),
                        model_conversation_id=getattr(repair._coding_provider,'_thread_id',None) or repair.resume_conversation,
                        physical_success=None)
                    path=root/(hashlib.sha256(self.task.strip().casefold().encode()).hexdigest()+'.json')
                    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(receipt,indent=2)+'\n');temporary.replace(path)
            self._task_update(('Astra justified rerun: ' if record['status']=='RERUN_VALIDATED' else 'Astra offline repair: ')+record['status']+'.',
                record.get('reason') or record.get('diagnosis') or str(result.get('reason') or result.get('status')),
                ('Fresh geometry will be checked within the run’s automatic-retry limit; original outcome stays UNVERIFIED.' if self.automatic_recovery else
                 'Revised code is saved for the next human Run with fresh live planning; original outcome stays UNVERIFIED.')
                if record['status'] in ('PLAN_VALIDATED','RERUN_VALIDATED') else 'No meaningful fix was validated. Review the recorded blocker; no automatic replay.')
        except Exception as exc:
            record.update(status='FAILED',reason=str(exc),
                repair_directory=str(self.directory/'outcome-repair'),
                coding_loop=str(self.directory/'outcome-repair/coding-loop.json'))
            self._task_update('Astra offline repair failed.',str(exc),
                'Original outcome and repair failure are saved; no task-motion commands were sent.')
        finally:
            if repair is not None:self._coding_requests+=repair._coding_requests
            record['ended_at']=time.time()
            (self.directory/'offline-repair.json').write_text(json.dumps(record,indent=2)+'\n')
            self._review_phase='complete';self._live_stage=None
            self._save_attempt_history()
            if repair is not None:repair.close_transport()

    def revise_before_session(self,result,evidence,scene):
        """Repair an actual read-only native failure using the shared coder."""
        self._check_cancelled()
        if not self.code_revision_loop:raise RuntimeError('ASPIRE revision is local only')
        source=self.validated_program['source']
        self.initial_feedback=dict(status='PREVIOUS_ATTEMPT',reason=result.get('reason') or result.get('status'),
            native_result=coding_feedback(result),previous_source=source,
            original_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            original_evidence=str(evidence),scene=coding_feedback(scene),physical_commands_sent=0,
            diagnosis_required='Diagnose this exact latest native failure, revise only as justified, and validate the complete native plan. Preserve all prompt conditions.')
        self.validated_program=None;self.initialize_before_observation=False;self._saved_executable_only=False
        self._prepare_program_before_session(self.task)
        changed=effective_program(source)!=effective_program(self.validated_program['source'])
        if not changed and self.validated_program['action']!='rerun':
            raise RuntimeError('No progress: the code diagnosis produced no meaningful correction or explicitly justified rerun.')
        if self.validated_program['action']=='rerun' and (changed or not self.validated_program['summary'].strip()):
            raise RuntimeError('The unchanged-code rerun must have an explicit justification and preserve the effective program.')
        self._task_update('Justified rerun validated.' if not changed else 'ASPIRE code correction validated.',
            self.validated_program['summary'],'Capture and fully plan again after ordinary queue/Home.')
        self.validated_program=dict(self.validated_program,action='program')
        self.initialize_before_observation=True

    def _prepare_program_before_session(self, prompt):
        """Generate and test a fresh prompt before consuming its robot lease."""
        if self.task is None:self.task=prompt
        if self.task!=prompt:raise ValueError('ASPIRE prompt differs from configured task')
        if self.initial_feedback is None:
            previous=self._physical_feedback_path()
            if previous.exists():self.initial_feedback=json.loads(previous.read_text())
            else:
                previous=self._coding_feedback_path()
                if previous.exists():
                    self.initial_feedback=json.loads(previous.read_text())
                    conversation=self.initial_feedback.get('model_conversation_id')
                    if conversation and self.resume_conversation is None:
                        self.resume_conversation=str(uuid.UUID(conversation))
                    self._task_update('Reviewing the previous coding failure.',
                        str(self.initial_feedback.get('reason','Saved harness feedback is supplied.')),
                        'Revise the recorded source and test it against the current scene.')
        if self.initialize_before_observation:
            # Idle parked arms may obscure a target. A previously generated,
            # planned program can enter the normal queue/Home flow first. The
            # execution harness still captures fresh leased RGB-D and plans
            # the complete task before its first task command; no recorded
            # geometry or path cache is used for live motion.
            validate_program(self.validated_program)
            handoff=dict(task=prompt,validation=('SAVED_EXECUTABLE_FRESH_PLAN_REQUIRED'
                    if self._saved_executable_only else 'PREVIOUS_FULL_PLAN_ONLY'),
                source_sha256=hashlib.sha256(self.validated_program['source'].encode()).hexdigest(),
                model_conversation_id=self.resume_conversation,
                queue_session_created=False,physical_motion_calls=0,
                live_replanning_required=True,scene_capture='after_normal_queue_initialization')
            if self._saved_executable_only:
                handoff['executable']=self._executable_reuse[0]
                handoff['code_generation_requested']=False
            (self.directory/'handoff.json').write_text(json.dumps(handoff,indent=2)+'\n')
            self._preparation_result=dict(status='AWAITING_INITIALIZED_SCENE',
                planning_success=None,physical_motion_calls=0,live_replanning_required=True)
            self.initial_feedback=dict(self.initial_feedback or {},preparation=handoff)
            self._phase='awaiting_lease'
            self._emit('tool_result','Reuse planned agent source; capture and fully plan after queue initialization',
                result=self._preparation_result)
            return
        self._phase='offline_generation'
        preparation=AspireCodexPolicy(harness=self.harness,calibration=self.report,
            robot_id=self.robot_id,task=prompt,model=self.model,
            directory=self.directory/'preparation',instructions=self.instructions,
            skill_directory=self.skill_directory,max_revisions=self.max_revisions,
            allow_hardware=False,plan_only=True,generator=self.generator,
            initial_feedback=self.initial_feedback,resume_conversation=self.resume_conversation,
            validated_program=self.validated_program,coding_timeout_s=self.coding_timeout_s,
            display_name=self.display_name, vision_session=self.vision_session, owns_vision_session=False,
            skill_learning=self.skill_learning,skill_coordinator=self.skill_coordinator,
            executable_skills=self.executable_skills,skill_review_timing=self.skill_review_timing)
        preparation.code_revision_loop=self.code_revision_loop
        preparation.code_revision_limit=max(0,self.code_revision_limit-self._coding_requests)
        preparation._topic_snapshot=self._topic_snapshot
        self._preparation=preparation
        preparation.reasoning_effort=self.reasoning_effort
        preparation.response_speed=self.response_speed
        preparation.cancelled=lambda:self.cancelled()
        preparation.interaction_sink=self._emit
        try:
            result=preparation._coding_loop()
            self._preparation_result=result
            if result.get('planning_success') is not True:
                self._outcome=result
                reason=str(result.get('reason',result.get('status')))
                last=result.get('last_harness_feedback',{})
                if last.get('reason'):reason+='; last harness failure: '+str(last['reason'])
                raise RuntimeError('ASPIRE preparation failed: '+reason)
            attempt=Path(preparation._attempts[-1]['directory'])
            reply=json.loads((attempt/'coding-response.json').read_text())
            validate_program(reply)
            self.validated_program=reply
            self._validated_lineage=json.loads((attempt/'lineage.json').read_text())
            self.resume_conversation=(getattr(preparation._coding_provider,'_thread_id',None)
                or preparation.resume_conversation)
            handoff=dict(task=prompt,validation='FULL_PLAN_ONLY',source=str(attempt/'generated_program.py'),
                source_sha256=hashlib.sha256(reply['source'].encode()).hexdigest(),
                model_conversation_id=self.resume_conversation,
                native_plan=str(attempt/'plan/full_sequence_plan.json'),
                queue_session_created=False,physical_motion_calls=0,
                live_replanning_required=True)
            (self.directory/'handoff.json').write_text(json.dumps(handoff,indent=2)+'\n')
            self.initial_feedback=dict(status='PREPARED',preparation=handoff)
            self._phase='prepared'
        finally:
            self._coding_requests+=preparation._coding_requests
            preparation.close_transport()

    def _physical_feedback_path(self):
        key=hashlib.sha256(self.task.strip().lower().encode()).hexdigest()
        return self.skill_directory/'physical-feedback'/(key+'.json')

    def _coding_feedback_path(self):
        key=hashlib.sha256(self.task.strip().lower().encode()).hexdigest()
        return self.skill_directory/'coding-feedback'/(key+'.json')

    def _save_coding_failure(self,reply,result):
        feedback=dict(status='PREVIOUS_ATTEMPT',reason=result.get('reason',result.get('status')),
            result=coding_feedback(result),previous_source=reply['source'],
            model_conversation_id=getattr(self._coding_provider,'_thread_id',None) or self.resume_conversation,
            evidence=str(self.directory.resolve()),physical_commands_sent=0,
            instruction='Revise this recorded coding/planning failure offline using current measurements. No task motion occurred.')
        path=self._coding_feedback_path();path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(feedback,indent=2)+'\n')

    def _save_physical_failure(self,reply,result,attempt):
        scene=result.get('outcome_scene',result.get('scene',{}))
        images=list(scene.get('images',{}).values())
        image_provenance='captured_scene'
        if not result.get('outcome_scene'):
            # An aborted sequence may never reach its outcome capture. Use
            # actual last recorded previews rather than presenting the
            # initial scene as the failed physical outcome.
            previews=[]
            for role in ('top','left','right'):
                frames=sorted((attempt/'execute/observations').glob('*_'+role+'_preview.jpg'))
                if frames:previews.append(str(frames[-1].resolve()))
            if previews:
                images=previews
                image_provenance='last_recorded_previews_before_shutdown'
        feedback=dict(status='PREVIOUS_PHYSICAL_OUTCOME_UNCONFIRMED',
            result=coding_feedback(result),previous_source=reply['source'],
            review_images=images,review_image_provenance=image_provenance,
            evidence=str((attempt/'execute').resolve()),
            instruction='Review actual outcome images; completed paths do not prove retention or placement. Revise offline before another human-authorized attempt. Reset readiness comes from the operator.')
        path=self._physical_feedback_path();path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(feedback,indent=2)+'\n')

    def build_trajectory(self,prompt,observation,first_step_id):
        if self.task is None:self.task=prompt
        if self._recovery:
            return self._build_recovery(prompt,observation,first_step_id)
        try:
            return super().build_trajectory(prompt,observation,first_step_id)
        except PolicyComplete:
            result=self._outcome or {}
            if (self.code_revision_loop or not self._saved_executable_only or not self.recovery_factory or self.cancelled()
                    or self._failure or result.get('success') is True
                    or result.get('status') in ('CANCELLED','PLAN_ONLY')):
                raise
            if result.get('physical_motion_calls',0)>0 or self._saved_motion_requests or self._recovery_attempts:
                # Finish parking and gather outcome evidence before diagnosing
                # a physical failure. No automatic physical replay.
                raise
            # The harness has exited, all packets have settled, and the existing
            # controller still owns the lease. Astra observes and decides anew;
            # no saved path or code is repaired or replayed here.
            self._start_recovery(result)
            return self._build_recovery(prompt,observation,first_step_id)

    def _execute(self,points):
        # A harness crash can lose its result after a submitted packet. Keep
        # this independent parent-side count to prevent an uncertain replay.
        self._saved_motion_requests+=1
        return super()._execute(points)

    def _public_attempts(self):
        def brief(result):
            return {k:result[k] for k in ('status','success','reason','planning_success',
                'physical_motion_calls','failing_stage','candidate_search') if k in result}
        return [dict(id='aspire-'+str(a['attempt']),policy='aspire',attempt=a['attempt'],
            source_sha256=(a.get('lineage') or {}).get('program_sha256'),
            result=brief(a.get('execution') or a.get('plan') or a.get('failure') or {}),
            status=(a.get('execution') or a.get('plan') or a.get('failure') or {}).get('status','running'))
            for a in self._attempts] + [dict(a,trace=list(a.get('trace',[]))) for a in self._recovery_attempts]

    def _save_attempt_history(self):
        path=self.directory/'coding-loop.json'
        saved=json.loads(path.read_text()) if path.exists() else dict(task=self.task,attempts=self._attempts)
        saved.update(recovery_attempts=self._recovery_attempts,task_updates=self._task_updates,
            coding_requests=self._coding_requests,code_revision_limit=self.code_revision_limit,
            code_revision_loop=self.code_revision_loop)
        path.write_text(json.dumps(saved,indent=2)+'\n')

    def _forward_recovery_event(self,record,kind,message,**details):
        self._attempt_traces[record['id']].append(kind,message,**details)
        # Child lineage belongs to the repair revision. Preserve the original
        # executed program's lineage in the main task-progress record.
        forwarded={k:v for k,v in details.items() if k!='lineage'}
        self._emit(kind,message,**dict(forwarded,attempt_id=record['id'],
            parent_attempt_id=record.get('parent_attempt_id')))

    def _start_recovery(self,result):
        parent='aspire-'+str(self._attempts[-1]['attempt']) if self._attempts else 'aspire-startup'
        record=dict(id='astra-1',policy='astra',parent_attempt_id=parent,status='starting',
            started_at=time.time(),model=self.model,code_generation_requested=False,
            previous_source_sha256=(self._current_lineage or {}).get('program_sha256'),
            previous_result={k:result[k] for k in ('status','reason','planning_success','physical_motion_calls','failing_stage') if k in result},
            completed_packets=0)
        self._recovery_attempts.append(record)
        self._attempt_traces[record['id']]=AttemptTrace(record,self.directory)
        self._task_update('Saved program attempt ended: '+str(result.get('status','FAILED'))+'.',
            str(result.get('reason') or 'The saved program did not confirm task success.'),
            'Hand off to Astra as a linked attempt; retain the original source and outcome.')
        self._publish_stage(dict(stage='astra_recovery',title='Handing off to Astra',
            detail='Saved-program failure is preserved. Astra will use fresh observations.'))
        self._save_attempt_history()
        try:
            self._recovery=self.recovery_factory()
            self._recovery.reasoning_effort=self.reasoning_effort
            self._recovery.response_speed=self.response_speed
            record['status']='running'
            self._task_update('Astra recovery started.',
                'Linked to '+parent+'. ASPIRE source is unchanged; no code generation requested.',
                'Astra observes the current scene and uses the existing planning and controller checks.')
        except Exception as exc:
            record.update(status='FAILED',reason=str(exc),ended_at=time.time())
            self._task_update('Astra handoff failed.',str(exc),'Failure and the original attempt are saved.')
            raise
        finally:
            self._save_attempt_history()

    def _build_recovery(self,prompt,observation,first_step_id):
        self._check_cancelled()
        recovery=self._recovery
        recovery.cancelled=self.cancelled
        recovery.refresh_observation=self.refresh_observation
        record=self._recovery_attempts[-1]
        recovery.interaction_sink=lambda kind,message,**details:self._forward_recovery_event(record,kind,message,**details)
        feedback=json.dumps(record['previous_result'])
        recovery_prompt=prompt+'\n\nPrevious saved ASPIRE attempt: '+feedback+(
            '\nThis is a separate recovery attempt. Reobserve the current scene, preserve target identity, '
            'and assess what remains. Do not assume the previous commands succeeded or replay their paths.')
        self._publish_stage(dict(stage='astra_recovery',title='Astra is preparing the next action',
            detail='Fresh observations and normal Astra planning; completed packets: '+str(record['completed_packets'])))
        try:
            return recovery.build_trajectory(recovery_prompt,observation,first_step_id)
        except PolicyComplete:
            outcome=getattr(recovery,'_outcome',None) or {}
            record.update(status='MODEL_REPORTED_DONE' if outcome.get('status')=='done' else 'FAILED',
                outcome=outcome,ended_at=time.time(),physical_success=None)
            self._task_update('Astra recovery ended: '+record['status']+'.',
                str(outcome.get('summary') or outcome.get('reason') or 'Outcome unrecorded'),
                'Model completion is separate from physical verification; the original ASPIRE outcome is retained.')
            self._save_attempt_history()
            raise
        except Exception as exc:
            record.update(status='FAILED',reason=str(exc),ended_at=time.time())
            self._task_update('Astra recovery failed.',str(exc),'Both attempts remain in the recorded history.')
            self._save_attempt_history()
            raise

    def trajectory_completed(self,observation,waypoint_count):
        if self._recovery:
            self._recovery.trajectory_completed(observation,waypoint_count)
            self._recovery_attempts[-1]['completed_packets']+=1
            self._save_attempt_history()
        else:
            super().trajectory_completed(observation,waypoint_count)

    def trajectory_failed(self,code,message,waypoint_count,*,feedback=None):
        # Controller faults fence the whole launch; never automatically replay
        # a timed-out saved-program packet in a new recovery attempt.
        super().trajectory_failed(code,message,waypoint_count,feedback=feedback)
        self._controller_failure=True
        if self._recovery_attempts:
            self._recovery_attempts[-1].update(status='FAILED',reason=self._failure,ended_at=time.time())
            self._save_attempt_history()

    def _run_program(self):
        if not self._saved_executable_only or not self.recovery_factory:
            return super()._run_program()
        try:
            with BridgeServer(self._rpc) as bridge:
                self._outcome=self.run_harness(bridge.path)
            if not isinstance(self._outcome,dict):
                raise ValueError('ASPIRE harness did not return a result object')
        except Exception as exc:
            self._outcome=dict(status='FAILED',success=False,reason=self._failure or str(exc),
                physical_motion_calls=self._saved_motion_requests,ambiguous_command_state=bool(self._saved_motion_requests))
            if self._attempts:
                self._attempts[-1]['failure']=self._outcome
            self._task_update('Saved program harness failed.',self._outcome['reason'],
                'Preserve this attempt; Astra recovery requires the existing lease and no controller fault.')
            self._save_attempt_history()
        finally:
            self._finished=True

    def _stage_status(self):
        if self._offline_repair and self._review_phase=='repairing_after_parking':
            stage=self._offline_repair._stage_status()
            if stage:return stage
        if not self._live_stage:return None
        return dict(self._live_stage,elapsed_s=max(0.,time.time()-self._live_stage['started_at']))

    def _publish_stage(self,stage):
        previous=self._live_stage or {}
        changed=previous.get('stage')!=stage['stage']
        stage['started_at']=stage.get('started_at') or (time.time() if changed else previous['started_at'])
        self._live_stage=dict(stage,active=True)
        if changed or stage.get('error'):
            self._task_update(stage['title']+'.',str(stage.get('error') or stage.get('detail') or ''),
                'Current stage and elapsed time are shown while this work continues.')

    def _run_harness(self,**request):
        try:
            with HarnessProgress(request['directory'],self._publish_stage):
                result=self.harness(**request)
            self._live_stage=None
            return result
        except Exception as exc:
            self._publish_stage(dict(stage='harness_error',title='Native harness failed',detail=str(exc),error=str(exc)))
            raise

    def _emit(self,kind,message,**details):
        # Public task updates are explicit activity/results, never private reasoning.
        if details.get('task_update'):
            self._task_updates.append(dict(timestamp=time.time(),**details['task_update']))
        if details.get('lineage'):
            self._current_lineage=details['lineage']
        if callable(self.interaction_sink):self.interaction_sink(kind,message,**details)

    def _task_update(self, happened, changed, next_action, **details):
        self._emit('tool_result',happened,task_update=dict(
            happened=happened,changed=changed,next_action=next_action),**details)


    def _skills(self):
        skills=[]
        terms=set(self.task.lower().split())
        for path in self.skill_directory.glob('*.json'):
            entry=json.loads(path.read_text())
            source=path.with_suffix('.py')
            if source.is_file():
                rank=len(terms & set((entry.get('task','')+' '+entry.get('lesson','')).lower().split()))
                skills.append((rank,path.stat().st_mtime,dict(entry,source=source.read_text(),
                    source_path=str(source.resolve()))))
        if self.published_skills:
            known={item[2].get('source_sha256') for item in skills}
            for entry in self.published_skills.programs(self.task):
                if entry['source_sha256'] not in known:
                    rank=len(terms & set((entry.get('task','')+' '+entry.get('lesson','')).lower().split()))
                    skills.append((rank,0.,entry))
        # Equally relevant older programs otherwise fill all retrieval slots
        # and exclude the just-revised, natively validated program.
        return [item for _,_,item in sorted(skills,key=lambda row:(row[0],row[1]),reverse=True)[:3]]

    def _generate(self,scene,feedback):
        if self._executable_reuse and self._attempts:
            prior=Path(self._attempts[-1]['directory'])/'coding-response.json'
            feedback=dict(feedback or {},previous_source=json.loads(prior.read_text())['source'],
                executable_repair_reason='Actual harness feedback established incompatibility; input binding alone is insufficient')
        self._task_update('Matching saved skills to this task.',
            'Retrieval provides context; actual code use is recorded separately.',
            'Check for a compatible executable before requesting code generation.')
        knowledge=self._topic_knowledge()
        programs=self._skills()
        self._retrieved_lineage=retrieval_catalog(knowledge,programs)
        (self.directory/f'retrieval-{len(self._attempts)+1:02d}.json').write_text(
            json.dumps(self._retrieved_lineage,indent=2)+'\n')
        if self.executable_skills and not self._attempts and (feedback or {}).get('status') not in (
                'PREVIOUS_ATTEMPT','CODE_ERROR','PLAN_FAILED','PROGRAM_ERROR','FAILED'):
            selected=self.executable_skills.select(self.task)
            if selected:
                provenance=dict(selected['provenance'],retrieved_topic_skills=knowledge,
                    current_scene=scene.get('snapshot'),code_revision_reason=None)
                self._executable_reuse.append(provenance)
                (self.directory/'executable-reuse.json').write_text(json.dumps(provenance,indent=2)+'\n')
                self._emit('tool_result','Reuse executable skill; bind task inputs without a coding request',
                    skill=provenance['skill'],inputs=provenance['inputs'])
                self._task_update('Reusing the saved executable.',
                    'Only task inputs are bound; core SHA256 '+provenance['core_source_sha256'][:12]+'.',
                    'Measure the current scene and test the complete native plan.')
                return selected['response']
        if self._coding_requests>=self.code_revision_limit:
            raise RuntimeError('Task code-revision limit reached; no coding requests remain')
        self._coding_requests+=1
        self._save_attempt_history()
        prompt=((self.instructions if not self._history else 'Continue using the current Robo-house program contract; revise from the actual harness feedback.')
            +'\nTASK: '+self.task+'\nSCENE: '+json.dumps(scene['context'])+
            '\n'+LINEAGE_INSTRUCTIONS+
            '\nRETRIEVED ASPIRE TOPIC SKILLS / PREVIOUS DEVELOPMENT HISTORY — exact IDs/versions and original validation scopes: '+json.dumps(model_retrieval_catalog(self._retrieved_lineage))+
            '\nRETRIEVAL SCOPE: '+json.dumps({k:v for k,v in knowledge.items() if k not in ('entries','failure_findings')})+
            '\nCURRENT RUN ACTIVITY / HARNESS FEEDBACK: '+json.dumps(coding_feedback(feedback)))
        (self.directory/f'coding-{len(self._attempts)+1:02d}-prompt.txt').write_text(prompt)
        self._emit('model_request','Generate/revise an ASPIRE program',task=self.task,
            attempt=len(self._attempts)+1,feedback=feedback)
        self._emit('tool_result','New skills needed; Astra will compose or revise from existing components',
            lineage={'retrieved':self._retrieved_lineage,'used':[],'new_skills':[],
                'usage_recorded':False,'stage':'needed'})
        self._task_update('Revising code from harness feedback.' if self._attempts else 'Generating task code with Codex.',
            ('Reported issue: '+str((feedback or {}).get('reason',(feedback or {}).get('status','unknown')))) if self._attempts else 'No compatible executable was selected; retrieved components are supplied to the coding policy.',
            'Validate the returned source and recorded skill use before planning.')
        if self.generator:
            return self.generator(prompt=prompt,scene=scene,feedback=feedback)
        if self._coding_provider is None:
            self._coding_provider=CodexAdapter(self.model,timeout_s=self.coding_timeout_s)
            self._coding_provider._decision_schema=PROGRAM_SCHEMA
            self._coding_provider._decision_response=lambda value:value
            self._coding_provider._thread_id=self.resume_conversation
            self._coding_provider._decision_instructions=(
                'You are the Codex coding policy for ASPIRE. Return the supplied JSON schema containing '
                'a complete Python program. Shell/web/other agent tools are disabled; the runner executes '
                'your code in the real ASPIRE harness and returns native planning errors for revision.')
        provider=self._coding_provider
        provider._stream_all_summaries=bool(getattr(self,'_stream_coding_progress',False))
        provider.reasoning_effort=self.reasoning_effort;provider.response_speed=self.response_speed
        provider.cancelled=self.cancelled;provider._interaction=self._emit
        provider._calls+=1
        parts=[dict(type='input_text',text=prompt)]
        for role in ('top','left','right'):
            parts.append(dict(type='input_image',detail='high',image_url='data:image/png;base64,'+
                base64.b64encode(Path(scene['images'][role]).read_bytes()).decode()))
        for path in (feedback or {}).get('review_images', []):
            parts.append(dict(type='input_image',detail='high',image_url='data:image/png;base64,'+
                base64.b64encode(Path(path).read_bytes()).decode()))
        # Three station views plus explicitly labelled recorded failure evidence.
        # This configures the existing transport attachment count, not cameras.
        provider._expected_camera_count=sum(part['type']=='input_image' for part in parts)
        self._history.append(dict(role='user',content=parts))
        started=time.monotonic()
        reply=provider._post_json(dict(instructions='Return a complete ASPIRE task program using the station contract in this turn.',tools=[],input=self._history))
        self._emit('model_response',reply.get('summary','Program response received'),
            summary=reply.get('summary',''),lesson=reply.get('lesson',''))
        self._history.append(dict(role='assistant',content=json.dumps(reply)))
        self._emit('model_timing','Codex program generation completed',elapsed_s=time.monotonic()-started)
        return reply

    def _review_prior_skills(self, task):
        if self.skill_learning:
            if self.skill_review_timing=='after_run':
                if self._topic_snapshot is None:
                    started=time.monotonic()
                    self._topic_snapshot=self.skill_learning.retrieve(task,
                        context='SAM3 perception instance mask' if self.vision_session else '')
                    receipt=dict(status='VERIFIED_LIBRARY_SNAPSHOT',task=task,
                        library_sha256=self._topic_snapshot.get('library_sha256'),
                        elapsed_s=time.monotonic()-started,historical_model_requests=0,
                        review_timing='after_run')
                    (self.directory/'startup-review.json').write_text(json.dumps(receipt,indent=2)+'\n')
                return
            self.skill_learning.review_pending(self.skill_coordinator,current_task=task)
            self.skill_learning.require_reviewed(task)

    def _topic_knowledge(self):
        knowledge={'entries':[]}
        if self.skill_learning:
            if self.skill_review_timing=='after_run':
                self._review_prior_skills(self.task)
                knowledge=self._topic_snapshot
            else:
                knowledge=self.skill_learning.retrieve(self.task,
                    context='SAM3 perception instance mask' if self.vision_session else '')
        if self.published_skills:
            from .aspire_published_skills import merge_topic_knowledge
            knowledge=merge_topic_knowledge(knowledge,self.published_skills.retrieve(self.task,
                context='SAM3 perception instance mask' if self.vision_session else ''))
        return knowledge

    def _schedule_deferred_review(self):
        if (not self.skill_learning or self.skill_review_timing!='after_run'
                or not self._owns_vision_session or self._deferred_review_scheduled):
            return
        self._deferred_review_scheduled=True
        library,model,timeout,speed=self.skill_learning,self.model,self.coding_timeout_s,self.response_speed
        self._deferred_review=DeferredSkillReview.schedule(library,
            lambda:self._custom_skill_coordinator or SubscriptionSkillCoordinator(
                library,model,timeout_s=timeout,response_speed=speed))

    def _coordinate_skill_promotion(self, *, packet, sources, feedback):
        """Use our subscription agent as upstream's coordinator before queueing.

        Reviews saved development evidence only; no robot/model tool is exposed.
        """
        if self._promotion_provider is None:
            self._promotion_provider=CodexAdapter(self.model,timeout_s=self.coding_timeout_s)
            self._promotion_provider._decision_schema=PROMOTION_SCHEMA
            self._promotion_provider._decision_response=lambda value:value
            self._promotion_provider._decision_instructions=COORDINATOR_INSTRUCTIONS
            self._promotion_provider._expected_camera_count=0
        provider=self._promotion_provider
        provider.cancelled=self.cancelled;provider._interaction=self._emit
        provider.reasoning_effort=self.reasoning_effort;provider.response_speed=self.response_speed
        provider._calls+=1
        self._emit('model_request','Reviewing your prompt…',
            task=packet['task'],promotion_task_id=packet['task_id'])
        self._promotion_history.append(dict(role='user',content=[dict(type='input_text',text=json.dumps(dict(
                findings=coding_feedback(packet),numbered_source_programs={path:'\n'.join(
                    str(number)+' '+line for number,line in enumerate(source.splitlines(),1))
                    for path,source in sources.items()},
                existing_topic_knowledge=promotion_knowledge(self.skill_learning,packet['task']),
                promotion_feedback=feedback)))]))
        reply=provider._post_json(dict(instructions=COORDINATOR_INSTRUCTIONS,tools=[],
            input=self._promotion_history))
        self._promotion_history.append(dict(role='assistant',content=json.dumps(reply)))
        for finding in reply['findings']:
            for snippet in finding['snippets']:
                snippet['generalize']={item['original']:item['replacement'] for item in snippet['generalize']}
        return reply

    def _persist_skill(self,reply,result):
        self.skill_directory.mkdir(parents=True,exist_ok=True)
        digest=hashlib.sha256(reply['source'].encode()).hexdigest()
        target=self.skill_directory/digest
        previous=json.loads(target.with_suffix('.json').read_text()) if target.with_suffix('.json').exists() else {}
        history=previous.get('validation_history',[])
        if previous and not history:
            history=[{k:previous.get(k) for k in ('validation','evidence','lineage')}]
        evidence=dict(validation='PHYSICAL_SUCCESS' if result.get('success') is True else 'FULL_PLAN_ONLY',
            evidence=str(self.directory.resolve()),lineage=self._attempts[-1].get('lineage'))
        if evidence not in history:history.append(evidence)
        self._task_update('Saved this program with its evidence.',
            'Program SHA256 '+digest[:12]+'. Validation: '+evidence['validation']+'.',
            'Fresh measurements and planning are required for a future reuse.')
        target.with_suffix('.py').write_text(reply['source'])
        target.with_suffix('.json').write_text(json.dumps(dict(task=self.task,lesson=reply['lesson'],
            summary=reply['summary'],source_sha256=digest,
            validation='PHYSICAL_SUCCESS' if result.get('success') is True else 'FULL_PLAN_ONLY',
            evidence=str(self.directory.resolve()),model=self.model,
            code_generation_kind='codex_subscription_transport' if self._coding_provider else 'saved_program_or_supplied_generator',
            executable_reuse=self._executable_reuse,
            lineage=self._attempts[-1].get('lineage'),
            validation_history=history,
            aspire_commit='f4c8939aab0af9b97690c561bd80e282940f7886',
            model_conversation_id=getattr(self._coding_provider,'_thread_id',None),
            model_weights_changed=False),indent=2)+'\n')
        if (self.plan_only and self.executable_skills and not self._executable_reuse
                and (self.initial_feedback or {}).get('status') not in ('PREVIOUS_ATTEMPT','PREVIOUS_PHYSICAL_OUTCOME_UNCONFIRMED')
                and self.executable_skills.select(self.task) is None):
            # Exact generated programs retain every prompt condition. This
            # registry also holds repairs, but does not require repair ancestry.
            root=self.skill_directory/'.repaired-programs';root.mkdir(exist_ok=True)
            receipt=dict(kind='generated_exact_task_program',task=self.task,status='PLAN_VALIDATED',response=reply,
                source_sha256=digest,source=str(Path(self._attempts[-1]['directory'])/'generated_program.py'),
                base_core_sha256=self.executable_skills.digest,base_context_sha256=self.executable_skills.context_sha256,
                base_inputs=None,parent_run=str(self.directory),parent_attempt_id='aspire-'+str(self._attempts[-1]['attempt']),
                original_outcome=dict(status='FULL_PLAN_ONLY',physical_success=None),diagnosis=reply['summary'],
                authorship=(self._attempts[-1].get('lineage') or {}).get('authorship'))
            path=root/(hashlib.sha256(self.task.strip().casefold().encode()).hexdigest()+'.json')
            temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(receipt,indent=2)+'\n');temporary.replace(path)

    def _coding_loop(self,bridge_socket=None):
        self._review_prior_skills(self.task)
        if self.vision_session:
            self.vision_session.start(cancelled=self.cancelled, emit=self._emit)
        self._phase='offline_generation' if self.plan_only else 'live_harness'
        if self.initial_feedback is None:
            previous=self._physical_feedback_path()
            if previous.exists():self.initial_feedback=json.loads(previous.read_text())
        try:
            scene=None
            if not self.validated_program or self.plan_only:
                self._task_update('Capturing the current RGB-D scene.',
                    'No task motion has been requested by this observation step.',
                    'Use this measured scene to prepare and test the program.')
                scene=self._run_harness(mode='observe',directory=self.directory/'scene',task=self.task,
                    bridge_socket=bridge_socket,cancelled=self.cancelled)
                if scene.get('status')!='OBSERVED':return scene
            feedback=self.initial_feedback or dict(status='GENERATE',physical_commands_sent=0)
            for index in range(1 if self._saved_executable_only and not self.code_revision_loop else self.max_revisions+1):
                self._check_cancelled()
                reuse=index==0 and self.validated_program is not None
                try:
                    reply=self.validated_program if reuse else self._generate(scene,feedback)
                except Exception as exc:
                    error=dict(status='CODING_REQUEST_FAILED',reason=str(exc),
                        physical_commands_sent=0,queue_session_created=False,
                        last_harness_feedback=coding_feedback(feedback))
                    (self.directory/'coding-request-error.json').write_text(json.dumps(error,indent=2)+'\n')
                    if self._attempts:
                        previous=json.loads((Path(self._attempts[-1]['directory'])/'coding-response.json').read_text())
                        self._save_coding_failure(previous,feedback)
                    self._task_update('Coding request failed.',str(exc),
                        'The planning failure and source are saved for the next preparation; no automatic submission.')
                    raise
                attempt=self.directory/f'attempt-{index+1:02d}';attempt.mkdir(exist_ok=False)
                (attempt/'coding-response.json').write_text(json.dumps(reply,indent=2)+'\n')
                self._attempts.append(dict(attempt=index+1,directory=str(attempt),
                    reused_agent_program=reuse,
                    executable_reuse=self._executable_reuse[-1] if self._executable_reuse and index==0 else None,
                    code_revision_reason=feedback if self._executable_reuse and index>0 else None,
                    model_conversation_id=getattr(self._coding_provider,'_thread_id',None) or self.resume_conversation))
                try:
                    validate_program(reply)
                    if reply['action']=='rerun' and not feedback.get('previous_source'):
                        raise ValueError('A rerun requires the actual prior source and an evidence-based diagnosis')
                    if reuse and self._validated_lineage:
                        lineage=json.loads(json.dumps(self._validated_lineage))
                    elif reuse and not self._retrieved_lineage:
                        lineage=record_lineage({k:v for k,v in reply.items() if k!='lineage'})
                        if reply.get('lineage'):lineage['unverified_declarations']=reply['lineage']
                    else:
                        lineage=record_lineage(reply,self._retrieved_lineage,
                            self._attempts[-1]['executable_reuse'])
                    previous_authorship=lineage.get('authorship') or {}
                    lineage['authorship']=dict(
                        generated_by_codex=(bool(self._coding_provider) if not reuse else previous_authorship.get('generated_by_codex',False)),
                        mode='reuse' if reuse or self._attempts[-1]['executable_reuse'] else 'generation',
                        model=self.model if self._coding_provider else previous_authorship.get('model'),
                        basis='codex_subscription_transport' if self._coding_provider and not reuse else previous_authorship.get('basis','unrecorded'))
                    core_author=str((lineage.get('executable') or {}).get('core_revision',{}).get('author','')) if (lineage.get('executable') or {}).get('core_revision') else ''
                    if 'Codex' in core_author:
                        lineage['authorship'].update(generated_by_codex=True,basis='recorded_core_revision_author',mode='reuse')
                    self._attempts[-1]['lineage']=lineage
                    (attempt/'lineage.json').write_text(json.dumps(lineage,indent=2)+'\n')
                except (SyntaxError,ValueError,TypeError) as exc:
                    feedback=dict(status='CODE_ERROR',reason=str(exc),physical_commands_sent=0,previous_source=reply.get('source'))
                    self._attempts[-1]['failure']=feedback
                    self._task_update('Code or lineage validation failed.',
                        str(exc), 'Request a corrected program; no motion plan was executed.')
                    continue
                if reply['action']=='give_up':
                    return dict(status='BLOCKED',success=False,reason=reply['summary'],physical_motion_calls=0)
                program=attempt/'generated_program.py';program.write_text(reply['source'])
                lineage['program']={'source':str(program.resolve()),'source_sha256':lineage['program_sha256'],
                    'code':reply['source']}
                for created in lineage['new_skills']:created['source']=str(program.resolve())
                (attempt/'lineage.json').write_text(json.dumps(lineage,indent=2)+'\n')
                self._emit('tool_result','New skills created' if lineage['new_skills'] else 'Program lineage recorded',
                    attempt=index+1,lineage=lineage)
                queries=attempt/'queries.json';queries.write_text(json.dumps({e['name']:e['query'] for e in reply['queries']}))
                if self.vision_session:
                    self._phase='vision_warmup'
                    self._task_update('Waiting for SAM 3 vision.',
                        'The perception worker must be ready before fresh planning and task motion.',
                        'Capture the current scene and test the complete native plan when vision is ready.')
                    self.vision_session.wait_ready()
                    self._phase='offline_generation' if self.plan_only else 'live_harness'
                if not reuse or self.plan_only:
                    self._task_update('Testing the complete native motion plan.',
                        'Program '+str(index+1)+' is saved at SHA256 '+lineage['program_sha256'][:12]+'.',
                        'Review the actual planning result before any task execution.')
                    result=self._run_harness(mode='plan',directory=attempt/'plan',task=self.task,
                        bridge_socket=None,snapshot=scene['snapshot'],program=program,queries=queries,
                        cache_root=self.directory,cancelled=self.cancelled)
                    self._attempts[-1]['plan']=result
                    for created in lineage['new_skills']:created['planning_success']=result.get('planning_success')
                    (attempt/'lineage.json').write_text(json.dumps(lineage,indent=2)+'\n')
                    self._emit('tool_result','ASPIRE full plan result',attempt=index+1,result=result)
                    self._task_update('Complete plan passed.' if result.get('planning_success') is True else 'Complete plan failed.',
                        str(result.get('reason') or ('All native planning checks passed.' if result.get('planning_success') is True else result.get('status','Cause unrecorded'))),
                        'Save the planned source; fresh live planning is still required.' if result.get('planning_success') is True else 'Revise from this failure within the existing revision budget.')
                    if result.get('planning_success') is not True:
                        feedback=dict(result,previous_source=reply['source'],
                            original_source_sha256=hashlib.sha256(reply['source'].encode()).hexdigest())
                        continue
                    self._persist_skill(reply,result)
                    if self.plan_only:return dict(result,status='PLAN_ONLY',success=False,physical_motion_calls=0)
                else:
                    self._emit('tool_result','Reuse saved source; live harness captures fresh state and fully plans before task motion',attempt=index+1)
                # Execute inside a new real harness pass: fresh scene/state and
                # a complete native plan before the first task command.
                self._task_update('Starting the fresh live harness.',
                    'The saved source is unchanged; observations and the full plan are rebuilt for this scene.',
                    'Execute only when the live plan passes, then check the observed outcome.')
                self._start_postpark_recording()
                result=self._run_harness(mode='execute',directory=attempt/'execute',task=self.task,
                    bridge_socket=bridge_socket,program=program,queries=queries,
                    cache_root=self.directory,cancelled=self.cancelled)
                if self._failure:
                    result=dict(result,status='FAILED',success=False,
                        harness_reason=result.get('reason'),reason=self._failure)
                self._attempts[-1]['execution']=result
                self._task_update('Live harness result: '+str(result.get('status','unknown'))+'.',
                    str(result.get('reason') or 'Task-motion calls recorded: '+str(result.get('physical_motion_calls','unknown'))+'.'),
                    'The local runner will check after normal parking.' if self._postpark_observe else
                    'After-parking review is separate; this harness result does not establish retained placement.')
                for created in lineage['new_skills']:
                    created['physical_success']=result.get('success')
                    created['physical_evidence_scope']='Live harness result; after-parking review is separate'
                (attempt/'lineage.json').write_text(json.dumps(lineage,indent=2)+'\n')
                if result.get('status')=='SCENE_CAPTURE_FAILED' and result.get('physical_motion_calls',0)==0:
                    # Camera capture already has bounded fresh-frame recovery.
                    # Rewriting task Python cannot fix an exhausted camera
                    # read and would waste the lease without changing it.
                    return result
                if result.get('planning_success') is not True and result.get('physical_motion_calls',0)==0:
                    if self.code_revision_loop:
                        self._save_coding_failure(reply,result)
                        self._task_update('Live ASPIRE code or planning failed before task motion.',
                            str(result.get('reason') or result.get('status')),
                            'Finish the ordinary stop, revise this exact source and latest evidence offline, then validate before fresh admission.')
                        return result
                    if self._saved_executable_only and not self.code_revision_loop:
                        self._save_coding_failure(reply,result)
                        self._task_update('Saved program failed in the live harness.',
                            str(result.get('reason',result.get('status','Native failure'))),
                            'Failure evidence is saved for local development; this launch does not generate or retry code.')
                        return result
                    if (self._controller_failure or self._failure or self.cancelled() or self._saved_motion_requests
                            or result.get('status') in ('HARNESS_ERROR','CANCELLED') or result.get('failure_kind')):
                        return result
                    feedback=dict(status='PREVIOUS_ATTEMPT',result=coding_feedback(result),previous_source=reply['source'],
                        original_source_sha256=hashlib.sha256(reply['source'].encode()).hexdigest(),
                        reason=result.get('reason'),failing_stage=result.get('failing_stage'),physical_commands_sent=0)
                    self._task_update('ASPIRE is revising the failed Python.',
                        str(result.get('reason') or result.get('status')),
                        'Use this actual pre-motion failure, validate the complete revised plan, then recapture and replan before motion.')
                    if result.get('initial_scene') or result.get('scene'):scene=result.get('initial_scene') or result['scene']
                    if scene is None:
                        scene=self._run_harness(mode='observe',directory=attempt/'refresh_scene',task=self.task,
                            bridge_socket=bridge_socket,cancelled=self.cancelled)
                        if scene.get('status')!='OBSERVED':return scene
                    continue
                # Offline code repair is allowed; no automatic physical replay.
                if result.get('success') is True and self._postpark_observe is None:
                    self._persist_skill(reply,result)
                    self._physical_feedback_path().unlink(missing_ok=True)
                    self._coding_feedback_path().unlink(missing_ok=True)
                elif result.get('physical_motion_calls',0)>0:
                    self._save_physical_failure(reply,result,attempt)
                return result
            self._task_update('Coding revision budget exhausted.',
                str(feedback.get('reason',feedback.get('status','Last failure unrecorded'))),
                'Review the saved failures before preparing another attempt.')
            self._save_coding_failure(reply,feedback)
            return dict(status='BLOCKED',success=False,physical_motion_calls=0,
                reason='Coding revision budget exhausted',last_harness_feedback=feedback)
        finally:
            if self.vision_session and self._owns_vision_session:self.vision_session.close()
            (self.directory/'coding-loop.json').write_text(json.dumps(dict(task=self.task,
                model=self.model,attempts=self._attempts,plan_only=self.plan_only,
                executable_reuse=self._executable_reuse,task_updates=self._task_updates,
                model_weights_changed=False,automatic_physical_retries=0,
                coding_requests=self._coding_requests,code_revision_limit=self.code_revision_limit,
                code_revision_loop=self.code_revision_loop),indent=2)+'\n')
            if self.skill_learning and self._owns_vision_session:
                # Physical runs stage again after parking evidence arrives.
                motions=any((item.get('execution') or {}).get('physical_motion_calls',0)
                    for item in self._attempts)
                ready=(self.skill_review_timing!='after_run' or self._postpark_observe is None or not motions)
                findings=self.skill_learning.stage_policy(self.directory,self.task,self._attempts,
                    review_ready=ready)
                (self.directory/'skill-learning.json').write_text(json.dumps(findings,indent=2)+'\n')
                if ready:
                    self._schedule_deferred_review()

    def close_transport(self):
        if self._recovery_attempts and self._recovery_attempts[-1]['status'] in ('starting','running'):
            self._recovery_attempts[-1].update(status='STOPPED',ended_at=time.time(),
                reason='Session ended before Astra recorded a terminal outcome')
            self._save_attempt_history()
        self._review_closed.set()
        if self._review_recorder:self._review_recorder.close()
        if self.vision_session and self._owns_vision_session:self.vision_session.close()
        if self._coding_provider:self._coding_provider._workspace.cleanup()
        if self._promotion_provider:self._promotion_provider._workspace.cleanup()
        if self._recovery:
            close=getattr(self._recovery,'close_transport',None)
            if callable(close):close()
            elif getattr(self._recovery,'_workspace',None):self._recovery._workspace.cleanup()


def configured_aspire_policy(config, *, origin, robot_id, model, directory,
                             task=None, selected_executable=None, recovery_factory=None,execution_environment='web'):
    """LocalPlayground depth provider hook; queue/Stop remain RunnerController's."""
    from .api_depth import ApiDepth
    import importlib.util
    config=dict(config)
    from .runpod_sam3 import configured_vision_session
    from .aspire_skill_learning import configured_skill_library
    from .aspire_executable_skills import configured_executable_skills
    from .aspire_published_skills import PublishedSkillLibrary
    published=(PublishedSkillLibrary() if config.get('published_skills',True)
        and config.get('coding_baseline')!='upstream' else None)
    vision_session=configured_vision_session(config)
    spec=importlib.util.spec_from_file_location('aspire_station_launcher',config['harness_module'])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    # Preserve local UI runs after its temporary visitor is cleaned up. This
    # also keeps reviewed images inside the existing lineage artifact boundary.
    directory=Path(config.get('run_directory',Path(config['skill_directory']).parent/'runs'))/('aspire-'+uuid.uuid4().hex)
    report=json.loads(ApiDepth(origin,robot_id,camera='top').read('/calibration',256_000))
    policy=AspireCodexPolicy(harness=module.HarnessClient(config,origin,robot_id),calibration=report,
        robot_id=robot_id,task=task,model=model or DEFAULT_MODEL,directory=directory,
        instructions=module.coding_instructions(config),skill_directory=config['skill_directory'],
        max_revisions=config.get('max_revisions',4),coding_timeout_s=config.get('coding_timeout_s',180),
        display_name=config.get('display_name',config.get('run_name','Codex + ASPIRE')), vision_session=vision_session,
        skill_learning=configured_skill_library(config),executable_skills=configured_executable_skills(config),
        published_skills=published,
        selected_executable=selected_executable,recovery_factory=recovery_factory,
        repair_outcomes=True,repair_vision_factory=lambda:configured_vision_session(config),
        automatic_recovery=config.get('automatic_recovery') is True,
        execution_environment=execution_environment,
        code_revision_limit=config.get('code_revision_limit',config.get('max_revisions',4)),
        skill_review_timing=(config.get('skill_learning') or {}).get('review_timing','before_launch'))
    from .session import HttpSessionAPI
    observer=HttpSessionAPI(origin,robot_id=robot_id,timeout_s=2.)
    policy.configure_postpark_review(origin,lambda:observer.get_robot_observation(robot_id))
    return policy
