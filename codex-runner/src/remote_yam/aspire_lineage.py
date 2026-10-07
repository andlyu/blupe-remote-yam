"""Content-addressed ASPIRE use records. Retrieval alone is never ancestry."""
import hashlib
import json


def sha256(value):
    return hashlib.sha256(value.encode()).hexdigest()


def version(entry):
    # Compact retrieval evidence and the full library use the same recipe version.
    # Full evidence remains in the persisted retrieval snapshot and library hash.
    content={k:v for k,v in entry.items() if k not in ('evidence','version','kind')}
    return sha256(json.dumps(content, sort_keys=True, separators=(',', ':')))


SOURCE_REF_SCHEMA = dict(type='object', additionalProperties=False, properties={
    'source': {'type': 'string'}, 'source_sha256': {'type': 'string'},
    'start_line': {'type': 'integer'}, 'end_line': {'type': 'integer'},
    'target_start_line': {'type': 'integer'}, 'target_end_line': {'type': 'integer'}},
    required=['source', 'source_sha256', 'start_line', 'end_line', 'target_start_line', 'target_end_line'])
LINEAGE_SCHEMA = dict(type='object', additionalProperties=False, properties={
    'used': dict(type='array', items=dict(type='object', additionalProperties=False, properties={
        'id': {'type': 'string'}, 'version': {'type': 'string'},
        'usage': {'type': 'string', 'enum': ['unchanged', 'adapted']},
        'changed': {'type': 'string'},
        'source_refs': dict(type='array', items=SOURCE_REF_SCHEMA)},
        required=['id', 'version', 'usage', 'changed', 'source_refs'])),
    'new_skills': dict(type='array', items=dict(type='object', additionalProperties=False, properties={
        'id': {'type': 'string'}, 'title': {'type': 'string'}, 'behavior': {'type': 'string'},
        'reason': {'type': 'string'}, 'start_line': {'type': 'integer'}, 'end_line': {'type': 'integer'}},
        required=['id', 'title', 'behavior', 'reason', 'start_line', 'end_line']))},
    required=['used', 'new_skills'])

LINEAGE_INSTRUCTIONS = '''Record lineage in the response. Use only the supplied retrieved IDs
and content-addressed versions. For each actually used recipe/program declare unchanged or
adapted, explain what changed, and link exact original source/hash/line ranges to the new
program's line ranges. A recipe you merely read is not a used skill. Declare any new behavior
implemented with its actual code lines in new_skills; do not claim physical success from
planning. An empty used/new_skills array is allowed. Do not invent source ancestry.'''


def retrieval_catalog(knowledge, programs=()):
    entries = []
    for item in knowledge.get('entries', [])+knowledge.get('failure_findings', []):
        if any(entry['id']==item['id'] for entry in entries):continue
        entries.append({**item, 'version': version(item), 'kind': 'topic_recipe'})
    for item in programs:
        source = item.get('source', '')
        digest = item.get('source_sha256') or sha256(source)
        entries.append({**item, 'id': 'program:' + digest, 'version': digest,
            'kind': 'saved_program', 'snippets': [{'source': item.get('source_path', ''),
                'source_sha256': digest, 'start_line': 1, 'end_line': len(source.splitlines()),
                'code': source, 'original_code': source}]})
    return entries


def _slice(source, start, end):
    lines = source.splitlines()
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
        raise ValueError('Invalid lineage target source range')
    return '\n'.join(lines[start-1:end])


def _retrieved_slice(entry, ref):
    """Resolve an original range without guessing a generalized recipe's mapping."""
    start, end = ref['start_line'], ref['end_line']
    if type(start) is not int or type(end) is not int or not 1 <= start <= end:
        raise ValueError('Invalid lineage original source range')
    candidates = [s for s in entry.get('snippets', []) if
        all(s.get(k) == ref[k] for k in ('source', 'source_sha256'))]
    for snippet in candidates:
        if (snippet.get('start_line'), snippet.get('end_line')) == (start, end):
            return snippet
    for snippet in candidates:
        first, last = snippet.get('start_line'), snippet.get('end_line')
        if (type(first) is not int or type(last) is not int or
                not first <= start <= end <= last):
            continue
        code, original = snippet.get('code'), snippet.get('original_code')
        span = last - first + 1
        # A full generalized recipe can still be cited as before. Subranges
        # require one recipe line per original line, in both saved texts.
        if (not isinstance(code, str) or not isinstance(original, str) or
                len(code.splitlines()) != span or len(original.splitlines()) != span):
            continue
        return {**snippet, 'start_line': start, 'end_line': end,
            'code': _slice(code, start-first+1, end-first+1),
            'original_code': _slice(original, start-first+1, end-first+1)}
    raise ValueError(f'Declared source linkage for {entry["id"]} lines {start}-{end} '
        'is not a retrieved snippet or an unambiguously mapped contained range')


def record_lineage(reply, retrieved=(), executable=None):
    """Validate declarations against the supplied snapshot, retain proof strength."""
    source = reply.get('source', '')
    declarations = reply.get('lineage')
    result = {'schema_version': 1, 'program_sha256': sha256(source),
        'retrieved': [{'id': e['id'], 'version': e['version'], 'kind': e['kind']}
            for e in retrieved], 'used': [], 'new_skills': [],
        'usage_recorded': declarations is not None or executable is not None}
    if executable:
        # Generic cores have a two-line input binder. An exact-task repair is
        # already the complete validated program, including its bound inputs.
        exact_task=executable.get('source_binding')=='exact_task_program'
        core = source if exact_task else source.partition('\n\n')[2]
        exact = (sha256(core) == executable['core_source_sha256'] and
                 sha256(source) == executable['bound_source_sha256'])
        result['used'].append({'id': executable['skill'], 'version': str(executable['version']),
            'usage': 'unchanged', 'changed': ('Exact-task repaired source reused unchanged; fresh live planning required.'
                if exact_task else 'Fresh task inputs bound; executable body unchanged.'),
            'verification': 'exact_source_match' if exact else 'hash_mismatch',
            'source_refs': [{'source': executable['core_source'],
                'source_sha256': executable['core_source_sha256'], 'start_line': 1,
                'end_line': len(core.splitlines()), 'target_start_line': 1 if exact_task else 3,
                'target_end_line': len(source.splitlines()), 'exact_match':exact,
                'recipe_code':core, 'derived_code':core}], 'inputs': executable['inputs'],
            'development_evidence': executable['development_evidence'],
            'core_revision': executable.get('core_revision'),
            'validation_scope': executable['validation_scope']})
        result['executable'] = executable
        if exact_task and executable.get('repair_authorship'):
            result['authorship']=executable['repair_authorship']
        return result
    if declarations is None:
        return result
    if not isinstance(declarations, dict) or set(declarations) != {'used', 'new_skills'}:
        raise ValueError('Invalid lineage declaration')
    if not all(isinstance(declarations[k], list) for k in ('used','new_skills')):
        raise ValueError('Lineage declarations must be arrays')
    for use in declarations['used']:
        if not isinstance(use,dict) or set(use)!=set(LINEAGE_SCHEMA['properties']['used']['items']['required']):
            raise ValueError('Invalid used skill fields')
        entry = next((e for e in retrieved if e['id'] == use['id'] and e['version'] == use['version']), None)
        if entry is None:
            raise ValueError('Declared skill was not retrieved at that version')
        if use['usage'] not in ('unchanged', 'adapted') or not isinstance(use['changed'], str):
            raise ValueError('Invalid skill usage declaration')
        if use['usage'] == 'adapted' and not use['changed'].strip():
            raise ValueError('Adapted skill must explain what changed')
        refs = []
        if not isinstance(use['source_refs'],list):raise ValueError('Source references must be an array')
        for ref in use['source_refs']:
            if not isinstance(ref,dict) or set(ref)!=set(SOURCE_REF_SCHEMA['required']):
                raise ValueError('Invalid source reference fields')
            snippet = _retrieved_slice(entry, ref)
            target = _slice(source, ref['target_start_line'], ref['target_end_line'])
            matched = target == snippet['code'].strip('\n')
            if use['usage'] == 'unchanged' and not matched:
                raise ValueError('Unchanged skill must match its exact retrieved code')
            refs.append({**ref, 'snippet_sha256': sha256(snippet['code']),
                'target_sha256': sha256(target), 'exact_match': matched,
                'original_code': snippet.get('original_code'), 'recipe_code': snippet['code'],
                'derived_code': target})
        if not refs:
            raise ValueError('Used skill requires exact source linkage')
        result['used'].append({**use, 'source_refs': refs,
            'verification': 'exact_source_match' if all(r['exact_match'] for r in refs)
                else 'model_declared_adaptation'})
    for item in declarations['new_skills']:
        if not isinstance(item,dict) or set(item)!=set(LINEAGE_SCHEMA['properties']['new_skills']['items']['required']):
            raise ValueError('Invalid new skill fields')
        code = _slice(source, item['start_line'], item['end_line'])
        if any(not isinstance(item[k], str) or not item[k].strip()
                for k in ('id', 'title', 'behavior', 'reason')):
            raise ValueError('New skill requires identity, behavior and reason')
        result['new_skills'].append({**item, 'code': code, 'code_sha256': sha256(code),
            'creation': 'recorded_code', 'physical_success': None, 'planning_success': None})
    return result
