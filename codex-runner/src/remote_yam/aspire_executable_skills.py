"""Text-only compatible executable selection, before perception or policy launch."""
import ast
import hashlib
import json
from pathlib import Path
import re


COLORS = ('red', 'green', 'blue', 'black', 'white', 'yellow', 'orange', 'purple')
COLOR = '(?:' + '|'.join(COLORS) + ')'


def pick_place_inputs(prompt, occupant_colors=('red', 'green', 'black')):
    """Recognize the complete supported request; other tasks use normal Astra."""
    sentence = str(prompt).strip().lower()
    # Full-match deliberately preserves all conditions. Unsupported qualifiers,
    # extra sentences, negations and plan-only requests do not select saved code.
    target = re.fullmatch(r'(?:pick\s+up|pick|move|place|put)\s+(?:the\s+|a\s+)?'
        r'(?P<source>' + COLOR + r')\s+(?:(?:rectangular|cuboid)\s+)?block\s+'
        r'(?:(?:and\s+)?(?:place|put|move)\s+it\s+)?'
        r'(?:onto|on|to)\s+(?:a\s+clear\s+patch\s+of\s+)?(?:the\s+|a\s+)?'
        r'(?P<color>' + COLOR + r')?\s*(?P<kind>(?:round\s+)?(?:poker\s+)?chip|towel)\s*\.?', sentence)
    if not target:
        return None
    kind = 'chip' if 'chip' in target['kind'] else 'fabric'
    color = target['color'] or ('green' if kind == 'fabric' else None)
    if color is None or (kind == 'fabric' and color != 'green'):
        return None
    source = target['source']
    queries = {'block': source+' rectangular block', 'source_table': 'white tabletop',
        'source_cloth': 'large teal fabric towel'}
    queries['chip' if kind == 'chip' else 'towel'] = (
        color+' round poker chip' if kind == 'chip' else 'large teal fabric towel')
    # Actual station inventory descriptions; missing instances are optional.
    for occupied in occupant_colors:
        if occupied not in COLORS:
            raise ValueError('Unsupported station occupant color')
        if occupied != source:
            queries['occupied_'+occupied] = occupied+' rectangular block'
    return {'source_description': queries['block'],
        'target_description': queries['chip' if kind == 'chip' else 'towel'],
        'target_kind': kind, 'source_support_strategy': 'measured_table_or_cloth',
        'occupancy_strategy': 'observed_block_footprints', 'queries': queries}


class ExecutableSkillLibrary:
    def __init__(self, manifest, *, repairs=None):
        self.path = Path(manifest).resolve()
        self.repairs = Path(repairs) if repairs else None
        self.manifest = json.loads(self.path.read_text())
        self.context_sha256=hashlib.sha256(json.dumps(self.manifest,sort_keys=True).encode()).hexdigest()
        if self.manifest.get('skill') != 'pick_place_block' or self.manifest.get('version') != 1:
            raise ValueError('Unsupported executable skill manifest')
        self.source_path = (self.path.parent/self.manifest['source']).resolve()
        self.source = self.source_path.read_text()
        self.digest = hashlib.sha256(self.source.encode()).hexdigest()
        if self.digest != self.manifest['source_sha256']:
            raise ValueError('Executable skill source differs from its recorded hash')
        revision = self.manifest.get('latest_core_revision')
        if revision and revision.get('new_core_sha256') != self.digest:
            raise ValueError('Executable core revision differs from its source hash')
        tree = ast.parse(self.source)
        functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        if not {'build_task', 'evaluate'} <= functions:
            raise ValueError('Executable skill lacks the ASPIRE program contract')

    def select(self, prompt):
        inputs = pick_place_inputs(prompt, self.manifest.get('occupant_colors', ('red', 'green', 'black')))
        if inputs is not None:
            for name, description in self.manifest.get('additional_occupants', {}).items():
                if not isinstance(name, str) or not name.isidentifier() or not isinstance(description, str) or not description.strip():
                    raise ValueError('Invalid additional station occupant description')
                inputs['queries']['occupied_context_'+name] = description
            if self.manifest.get('additional_occupants'):
                inputs['occupancy_strategy'] = 'observed_block_and_context_object_footprints'
            local = self.manifest.get('local_context_objects', {})
            if local:
                if not isinstance(local, dict) or any(not isinstance(k, str) or not k.isidentifier()
                        or not isinstance(v, dict) for k, v in local.items()):
                    raise ValueError('Invalid local context object inputs')
                inputs['local_context_objects'] = json.loads(json.dumps(local, allow_nan=False))
                inputs['occupancy_strategy'] = 'SAM3_block_masks_and_labelled_local_context_depth'
        # Repairs are immutable, exact-task versions. They do not overwrite the
        # shared core or silently generalize a task-specific motion/evaluator.
        if self.repairs:
            key=hashlib.sha256(str(prompt).strip().casefold().encode()).hexdigest()
            path=self.repairs/(key+'.json')
            try:
                repair=json.loads(path.read_text())
                response=repair['response'];source_path=Path(repair['source'])
                body=source_path.read_text()
                if (repair.get('status')=='PLAN_VALIDATED' and repair.get('base_core_sha256')==self.digest
                        and repair.get('base_inputs')==inputs
                        and (inputs is not None or repair.get('base_context_sha256')==self.context_sha256)
                        and repair.get('task','').strip().casefold()==str(prompt).strip().casefold()
                        and hashlib.sha256(body.encode()).hexdigest()==repair['source_sha256']
                        and body==response['source']):
                    # This is the complete exact-task program that passed its
                    # native plan. Preserve that hash, including its bound inputs.
                    bound=body
                    return dict(response=dict(response,source=bound),provenance=dict(
                        skill='saved_exact_task_program' if repair.get('kind') in ('generated_exact_task_program','justified_rerun') else 'pick_place_block_repair',version=repair['source_sha256'],
                        selection={'generated_exact_task_program':'reused_exact_task_program','justified_rerun':'reused_justified_rerun_program'}.get(repair.get('kind'),'reused_explicit_linked_repair'),inputs=inputs,core_source=str(source_path),
                        rerun_reason=repair.get('rerun_reason'),
                        source_binding='exact_task_program',repair_authorship=repair.get('authorship'),
                        core_source_sha256=repair['source_sha256'],base_core_sha256=self.digest,base_context_sha256=self.context_sha256,
                        bound_source_sha256=hashlib.sha256(bound.encode()).hexdigest(),
                        manifest=str(path),development_evidence=[repair['parent_run']],
                        validation_scope='Offline native plan passed; physical outcome remains unverified.',
                        recovery_parent=dict(run=repair['parent_run'],attempt=repair['parent_attempt_id'],
                            outcome=repair['original_outcome'],diagnosis=repair['diagnosis']),
                        code_generation_requested=False))
            except (OSError,ValueError,KeyError,TypeError):
                pass
        if inputs is None:return None
        source = 'TASK_INPUTS = '+repr(inputs)+'\n\n'+self.source
        return {'response': {'action': 'program', 'source': source,
            'summary': 'Reuse pick_place_block; bind source, destination and support/occupancy inputs.',
            'lesson': 'Fresh RGB-D, dimensions and complete native paths are mandatory. '
                'Contact-height and intrinsic-dimension metrology remain unresolved.',
            'queries': [{'name': k, 'query': v} for k, v in inputs['queries'].items()]},
            'provenance': {'skill': self.manifest['skill'], 'version': 1,
                'selection': ('reused_repaired_executable_inputs_only' if self.manifest.get('latest_core_revision')
                    else 'reused_executable_inputs_only'), 'inputs': inputs,
                'core_source': str(self.source_path), 'core_source_sha256': self.digest,
                'bound_source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                'manifest': str(self.path), 'development_evidence': self.manifest['development_evidence'],
                'core_revision': self.manifest.get('latest_core_revision'),
                'core_revision_evidence': self.manifest.get('revision_evidence', []),
                'validation_scope': self.manifest['validation_scope'], 'code_generation_requested': False}}


def configured_executable_skills(config):
    if config.get('coding_baseline') == 'upstream':
        return None
    value = config.get('executable_skills') or {}
    from .aspire_published_skills import DEFAULT_ROOT
    return ExecutableSkillLibrary(value.get('manifest') or DEFAULT_ROOT/'executable/pick_place.json',
        repairs=Path(config['skill_directory'])/'.repaired-programs' if config.get('skill_directory') else None) if value.get('enabled') else None


def select_aspire_launch(config, prompt, *, execution_environment='web'):
    """Select by the complete prompt before capture, planning or code generation."""
    library = configured_executable_skills(config)
    selected = library.select(prompt) if library else None
    if execution_environment not in ('local','web'):raise ValueError('Invalid ASPIRE execution environment')
    coding_loop=execution_environment=='local'
    route = dict(requested_policy='aspire', actual_policy='aspire' if selected or coding_loop else 'astra',
        prompt=prompt, execution_environment=execution_environment,code_generation_requested=not selected and coding_loop)
    if selected:
        route.update(message='Program found. Using saved ASPIRE code; fresh capture and full planning follow Home.',
            skill=selected['provenance']['skill'],
            source_sha256=selected['provenance']['bound_source_sha256'])
    elif coding_loop:
        route.update(message='No saved ASPIRE program matches. ASPIRE will generate Python and validate its complete native plan before queue admission.')
    else:
        route.update(message='No saved ASPIRE program matches this task. Running Astra instead. '
            'To add ASPIRE support, clone the repository and generate a program locally using your own Astra subscription.',
            development_url='https://github.com/andlyu/blupe-remote-yam')
    return dict(route=route, program=selected)
