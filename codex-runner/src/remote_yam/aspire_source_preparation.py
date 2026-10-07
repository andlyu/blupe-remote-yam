"""Prepare ASPIRE source before admission; measure and plan in the live harness.

This adapter uses the native policy's retrieval, generation, revision history,
lineage and execution loop. It does not capture, plan, enqueue or dispatch.
Native ASPIRE remains an optional dependency installed on the station.
"""
import ast
import hashlib
import json
import time


def validate_source_interface(source):
    tree = ast.parse(source)
    compile(tree, 'generated_program.py', 'exec')
    definitions = {node.name: node for node in tree.body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name, count in (('build_task', 1), ('evaluate', 2)):
        node = definitions.get(name)
        if not isinstance(node, ast.FunctionDef):
            raise ValueError(name + ' must be a synchronous function')
        arguments = node.args
        positional = len(arguments.posonlyargs) + len(arguments.args)
        required = positional - len(arguments.defaults)
        if (required > count or (positional < count and arguments.vararg is None)
                or any(default is None for default in arguments.kw_defaults)):
            raise ValueError(name + ' has an incompatible harness signature')


def configure_source_preparation(policy):
    from .aspire_codex_policy import PROGRAM_SCHEMA, validate_program
    from .aspire_lineage import record_lineage
    SourcePreparation(policy, validate_program, record_lineage, PROGRAM_SCHEMA)
    return policy


class SourcePreparation:
    def __init__(self, policy, validate_program, record_lineage, schema):
        self.policy = policy
        self.validate_program, self.record_lineage, self.schema = validate_program, record_lineage, schema
        self.native_prepare = policy._prepare_program_before_session
        policy._prepare_program_before_session = self.prepare

    def request_text(self, *, prompt, scene, feedback):
        # Use the same subscription transport/conversation as native revisions.
        from .codex_policy import CodexAdapter
        policy = self.policy
        if policy._coding_provider is None:
            policy._coding_provider = CodexAdapter(policy.model, timeout_s=policy.coding_timeout_s)
            policy._coding_provider._decision_schema = self.schema
            policy._coding_provider._decision_response = lambda value: value
            policy._coding_provider._thread_id = policy.resume_conversation
        provider = policy._coding_provider
        provider._decision_instructions = (
            'You are the Codex coding policy for ASPIRE. Return a complete Python program '
            'using the supplied JSON schema. Shell, web and agent tools are disabled; '
            'the native harness returns measured scene and native planning feedback for revisions.')
        instructions = (
            'Return a complete ASPIRE Python program using the supplied schema. '
            'No current scene has been captured. Write runtime perception and geometry; '
            'do not invent measured positions or claim planning/physical success. '
            'Shell, web and agent tools are disabled. The native harness captures and '
            'fully plans from fresh measurements after queue initialization.')
        provider.reasoning_effort, provider.response_speed = policy.reasoning_effort, policy.response_speed
        provider.cancelled, provider._interaction = policy.cancelled, policy._emit
        provider._expected_camera_count = 0
        provider._calls += 1
        policy._history.append(dict(role='user', content=[dict(type='input_text', text=prompt)]))
        started = time.monotonic()
        reply = provider._post_json(dict(instructions=instructions,
                                        tools=[], input=policy._history))
        policy._history.append(dict(role='assistant', content=json.dumps(reply)))
        policy._emit('model_response', reply.get('summary', 'Program response received'))
        policy._emit('model_timing', 'Codex source preparation completed', elapsed_s=time.monotonic()-started)
        return reply

    def prepare(self, prompt):
        policy = self.policy
        if policy.task != prompt:
            raise ValueError('ASPIRE prompt differs from configured task')
        if policy.validated_program is not None:
            return self.native_prepare(prompt)
        root = policy.directory / 'source-preparation'
        root.mkdir(exist_ok=True)
        feedback = policy.initial_feedback
        if feedback is None:
            for path in (policy._physical_feedback_path(), policy._coding_feedback_path()):
                if path.is_file():
                    feedback = json.loads(path.read_text())
                    break
        feedback = feedback or dict(status='GENERATE_SOURCE', physical_commands_sent=0)
        scene = dict(status='AWAITING_INITIALIZED_SCENE', snapshot=None, images={}, context=dict(
            current_scene_available=False, capture='after_queue_and_home',
            instruction='Generate runtime perception and geometry. No current measured scene or motion plan exists.'))
        original_generator, original_instructions = policy.generator, policy.instructions
        policy.generator = original_generator or self.request_text
        policy.instructions += (
            '\nSOURCE PREPARATION BEFORE QUEUE: no camera capture or motion planning has occurred. '
            'Prepare Python using runtime tools for fresh perception and geometry. '
            'The station will capture and plan all paths after Home before task motion. '
            'Never hardcode an invented current object pose or use a recorded plan for execution.\n')
        policy._phase = 'source_preparation'
        try:
            for index in range(policy.max_revisions + 1):
                policy._check_cancelled()
                attempt = root / ('attempt-%02d' % (index+1))
                attempt.mkdir()
                reply = policy._generate(scene, feedback)
                (attempt / 'coding-response.json').write_text(json.dumps(reply, indent=2)+'\n')
                for name in ('coding-01-prompt.txt', 'retrieval-01.json'):
                    path = policy.directory / name
                    if path.is_file():
                        (attempt / name).write_bytes(path.read_bytes())
                if reply.get('action') == 'give_up':
                    raise RuntimeError('ASPIRE source preparation blocked: '+reply.get('summary', 'No program'))
                try:
                    self.validate_program(reply)
                    validate_source_interface(reply['source'])
                    lineage = self.record_lineage(reply, policy._retrieved_lineage)
                except (SyntaxError, ValueError, TypeError) as exc:
                    feedback = dict(status='CODE_ERROR', reason=str(exc),
                                    previous_source=reply.get('source'), physical_commands_sent=0)
                    (attempt / 'validation.json').write_text(json.dumps(feedback, indent=2)+'\n')
                    policy._task_update('Source validation failed.', str(exc),
                                        'Revise Python before joining the queue; no scene or plan was captured.')
                    continue
                policy._check_cancelled()
                source = attempt / 'generated_program.py'
                source.write_text(reply['source'])
                lineage['authorship'] = dict(generated_by_codex=bool(policy._coding_provider),
                    mode='generation', model=policy.model if policy._coding_provider else None,
                    basis='codex_subscription_transport' if policy._coding_provider else 'unrecorded')
                lineage['program'] = dict(source=str(source.resolve()), source_sha256=lineage['program_sha256'],
                                          code=reply['source'])
                (attempt / 'lineage.json').write_text(json.dumps(lineage, indent=2)+'\n')
                policy.validated_program, policy._validated_lineage = reply, lineage
                policy.initialize_before_observation = True
                policy.resume_conversation = getattr(policy._coding_provider, '_thread_id', None) or policy.resume_conversation
                original_emit = policy._emit
                def emit(kind, message, **details):
                    if message.startswith('Reuse planned agent source;'):
                        message = 'Source statically validated; capture and fully plan after queue initialization'
                    original_emit(kind, message, **details)
                policy._emit = emit
                try:
                    self.native_prepare(prompt)
                finally:
                    policy._emit = original_emit
                handoff_path = policy.directory / 'handoff.json'
                handoff = json.loads(handoff_path.read_text())
                handoff.update(validation='SOURCE_VALIDATED_FRESH_PLAN_REQUIRED', source=str(source.resolve()),
                               code_generation_requested=True, planning_success=None)
                handoff_path.write_text(json.dumps(handoff, indent=2)+'\n')
                policy.initial_feedback = dict(status='SOURCE_PREPARED', preparation=handoff)
                policy._task_update('ASPIRE Python prepared.',
                    'Source SHA256 '+hashlib.sha256(reply['source'].encode()).hexdigest()+'. Static/interface validation passed.',
                    'Join the existing queue and Home, then capture fresh RGB-D and fully plan before task motion.')
                return
            raise RuntimeError('ASPIRE source validation revision budget exhausted')
        except BaseException as exc:
            (root / 'failure.json').write_text(json.dumps(dict(status='SOURCE_PREPARATION_FAILED',
                reason=str(exc), queue_session_created=False, physical_motion_calls=0), indent=2)+'\n')
            raise
        finally:
            policy.generator, policy.instructions = original_generator, original_instructions
