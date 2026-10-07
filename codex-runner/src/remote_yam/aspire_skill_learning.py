"""ASPIRE topic promotion, using its pinned, unmodified promotion recorder.

Execution produces findings; a coordinator reviews and promotes recipes. This
module never calls a model, opens a robot session, or executes a code snippet.
"""
from contextlib import contextmanager
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import textwrap


ASPIRE_COMMIT = 'f4c8939aab0af9b97690c561bd80e282940f7886'
RECORDER_SHA256 = 'edb74c8c306e5bf24b76648a126eb629e6d4fa9e875783b450819f0356ab04f6'
TOPICS = {
    'localize': 'Perception descriptions, disambiguation and measured geometry',
    'grasp': 'Native grasp frames, contact geometry and outcome verification',
    'transport': 'Waypoints, finite placement surfaces and withdrawal',
    'manipulation': 'Contact interaction techniques',
}


class FindingPacketChanged(ValueError):
    """The run supplied newer outcome evidence while its old packet was reviewed."""


def _object(properties):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties,
            'required': list(properties)}


PROMOTION_SCHEMA = _object({
    'reason': {'type': 'string'},
    'findings': {'type': 'array', 'items': _object({
        **{key: {'type': 'string'} for key in ('id', 'title', 'trigger', 'why', 'scope')},
        'topic': {'type': 'string', 'enum': list(TOPICS)},
        'status': {'type': 'string', 'enum': ['observed_recipe', 'provisional', 'failure']},
        'limits': {'type': 'array', 'items': {'type': 'string'}},
        'keywords': {'type': 'array', 'items': {'type': 'string'}},
        'snippets': {'type': 'array', 'items': _object({
            'source': {'type': 'string'}, 'start_line': {'type': 'integer'}, 'end_line': {'type': 'integer'},
            'generalize': {'type': 'array', 'items': _object({
                'original': {'type': 'string'}, 'replacement': {'type': 'string'}})},
        })},
    })},
})

COORDINATOR_INSTRUCTIONS = '''Act as ASPIRE's skill-promotion coordinator, following pinned
aspire/sim/.claude/libero/fix-loop/main-agent-prompt.md section 6 Update skills.
Read the supplied findings and real saved programs. Promote supported generalizable patterns,
not whole task summaries, into localize/grasp/transport/manipulation recipes. Each recipe needs
its trigger, a 5–20 line snippet selected from exact source (one-based inclusive line numbers),
explicit generalization of task-specific text/constants, why, scope and limits. Reuse existing
pattern ids and topics when extending the same knowledge; merge variants instead of duplicating.
Perception queries/backend/model/effective encoding provenance and code authorship are evidence.
Configured model names are not proof of authorship. Only development evidence may drive edits.
No fabricated seed validation or multi-run robustness. Observed_recipe requires retained
placement observed after parking, and only establishes that recipe's stated narrow scope.
Planning success or completed API packets alone do not establish pickup, placement or metric
contact. Preserve original failures and checks: unvalidated fixes stay provisional, failure
findings stay failure. Never promote unexplained height/dimension results as calibrated fixes.
Do not copy stale scene poses, paths, nominal object dimensions or an unrelated chip-label
prerequisite into towel knowledge. Snippets are documented recipes, not new callable tool names.
If no supported generalization is available, return no findings and a concise reason. Return
only the supplied schema. Do not execute code, use robot sessions or perform any experiment.'''


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


class TopicSkillLibrary:
    def __init__(self, root, aspire, suite='robohouse'):
        self.root = Path(root).resolve()
        source = Path(aspire) / 'aspire/sim/scripts/libero/record_skill_promotion.py'
        if digest(source) != RECORDER_SHA256:
            raise ValueError('ASPIRE promotion recorder differs from the pinned revision')
        spec = importlib.util.spec_from_file_location('aspire_promotion_recorder', source)
        self.recorder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recorder)
        self.suite = self.recorder.validate_name(suite, 'suite')
        self.skills = self.root / self.recorder.SKILLS_REL
        self.campaign = self.recorder.campaign_dir(self.root, self.suite)

    @contextmanager
    def _locked(self):
        # Promotions serialize across CLI/policy processes, not just threads.
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.promotion.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _initialize(self):
        self.skills.mkdir(parents=True, exist_ok=True)
        for topic, description in TOPICS.items():
            path = self.skills / (topic + '.md')
            if not path.exists():
                path.write_text(f'# Robo-house {topic}\n\n{description}.\n\n'
                    'Documented recipes and extracted code; not new callable tool bindings.\n'
                    'Recapture geometry and fully plan each new scene. Individual evidence scopes '
                    'and unresolved checks govern reuse; no multi-run robustness is implied.\n')
        index = self.skills / 'patterns.json'
        if not index.exists():
            self.recorder.write_json_atomic(index, {'schema_version': 1, 'patterns': []})

    @staticmethod
    def task_id(directory):
        directory = Path(directory).resolve()
        name = re.sub(r'[^A-Za-z0-9_.-]', '-', directory.parent.name)[:64]
        return name + '-' + hashlib.sha256(str(directory).encode()).hexdigest()[:12]

    @staticmethod
    def _program_bytes(program):
        # Preserve the original path as provenance and reject changed live
        # sources. Once a visitor directory is cleaned up, use only a retained
        # copy with the same recorded hash; never silently skip the finding.
        source = Path(program['path'])
        if not source.is_file() and program.get('retained_source'):
            source = Path(program['retained_source'])
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != program['sha256']:
            raise ValueError('Recorded program changed before coordinator review')
        return data

    def _stage(self, packet):
        self._initialize()
        task = packet['task_id']
        # Completed snapshots/patches are immutable. A later review must use a
        # separate amendment, rather than rewriting a promoted finding.
        if self.recorder.completed_record(self.root, self.suite, task):
            return self._verify(task)
        directory = self.campaign / task
        packet = json.loads(json.dumps(packet))
        for program in packet['programs']:
            data = self._program_bytes(program)
            retained = directory / 'programs' / (program['sha256'] + '.py')
            retained.parent.mkdir(parents=True, exist_ok=True)
            if retained.exists():
                if digest(retained) != program['sha256']:
                    raise ValueError('Retained program source hash mismatch')
            else:
                with retained.open('xb') as handle:
                    handle.write(data)
            program['retained_source'] = str(retained.resolve())
        self.recorder.write_json_atomic(directory / 'findings.json', packet)
        (directory / 'findings.md').write_text(
            '# Development findings awaiting coordinator review\n\n'
            'Native failures, visual placement and metric checks remain separate. '
            'No supported recipe is inferred automatically from packet completion.\n\n'
            '```json\n' + json.dumps(packet, indent=2) + '\n```\n')
        return {'task_id': task, 'status': 'AWAITING_PROMOTION_REVIEW',
                'findings': str(directory / 'findings.json')}

    def stage_policy(self, directory, task, attempts, *, review_ready=True):
        directory = Path(directory).resolve()
        programs = []
        for attempt in attempts:
            path = Path(attempt['directory']).resolve()
            source = path / 'generated_program.py'
            if not source.exists():
                continue
            programs.append({'path': str(source), 'sha256': digest(source),
                'queries': load(path / 'queries.json'),
                'coding_response': str(path / 'coding-response.json'),
                'model_conversation_id': attempt.get('model_conversation_id'),
                'reused_agent_program': attempt.get('reused_agent_program', False),
                'native_plan_artifacts': str(path / 'plan'),
                'native_execution_artifacts': str(path / 'execute')})
        native_findings = []
        for attempt in attempts:
            native_findings.append({'attempt': attempt.get('attempt'), **{phase: {
                key: result.get(key) for key in ('status', 'success', 'planning_success',
                    'physical_motion_calls', 'failing_stage', 'reason', 'native_sequence_diagnostics')
                if key in result} for phase, result in attempt.items()
                if phase in ('plan', 'execution') and isinstance(result, dict)}})
        packet = {'task_id': self.task_id(directory), 'task': task,
            'evidence_role': 'development', 'policy_directory': str(directory),
            'programs': programs, 'coding_loop': str(directory / 'coding-loop.json'),
            'native_findings': native_findings,
            'review_ready': review_ready,
            'retained_placement_observed': False,
            'review_scope': 'Harness result only; parking and final review not yet supplied',
            'aspire_commit': ASPIRE_COMMIT}
        completion=directory/'task-completion.json'
        if completion.exists():
            final=load(completion)
            packet.update(receipt=str(completion),review=str(directory/'postpark-review.json'),
                retained_placement_observed=final.get('success') is True,
                postpark_automatic_success=final.get('success'),
                review_status=final.get('status'),
                review_scope='Automatic fresh post-parking evidence; original native result retained separately',
                postpark_checks=(final.get('postpark_evaluation') or {}).get('placement_evidence',{}).get('evidence',{}).get('checks'))
        with self._locked():
            return self._stage(packet)

    def stage_run(self, receipt, review=None, *, evidence_role='development', provenance=None, amendment=None):
        if evidence_role not in ('development', 'held_out'):
            raise ValueError('Unknown evidence role')
        receipt = Path(receipt).resolve()
        run = receipt.parent
        data = load(receipt)
        policy = run / 'policy'
        loop_path = policy / 'coding-loop.json'
        if not loop_path.exists():
            loop_path = policy / 'preparation/coding-loop.json'
        loop = load(loop_path) if loop_path.exists() else {'attempts': []}
        self.stage_policy(policy, data['task'], loop['attempts'])
        with self._locked():
            task = self.task_id(policy)
            original_task = task
            if amendment:
                task += '-' + self.recorder.validate_name(amendment, 'amendment')
            if self.recorder.completed_record(self.root, self.suite, task):
                return self._verify(task)
            packet = load(self.campaign / original_task / 'findings.json')
            packet['task_id'] = task
            if amendment:
                packet['amends_task_id'] = original_task
                packet['amendment'] = amendment
            manual = load(review) if review else {}
            parking = (data.get('review_through_parking') or {}).get('status')
            post = data.get('postpark_evaluation') or {}
            completion = data.get('task_completion') or {}
            packet.update(evidence_role=evidence_role, receipt=str(receipt),
                receipt_sha256=digest(receipt), review=str(Path(review).resolve()) if review else None,
                review_sha256=digest(review) if review else None,
                native_status=(data.get('result') or {}).get('status'),
                native_success=(data.get('result') or {}).get('success'),
                parking_status=parking,
                postpark_checks=post.get('placement_evidence', {}).get('evidence', {}).get('checks', {}),
                postpark_automatic_success=post.get('placement_evidence', {}).get('success'),
                retained_placement_observed=parking == 'PARKING_OBSERVED' and
                    (manual.get('success') is True or completion.get('success') is True),
                review_scope=manual.get('validation_scope', manual.get('success_basis',
                    completion.get('reason', 'No final physical review'))),
                review_status=manual.get('status'),
                manual_review={k: manual[k] for k in ('success_basis', 'validation_scope',
                    'metric_diagnosis', 'original_failed_checks', 'height_diagnosis') if k in manual},
                author_provenance={k: manual[k] for k in ('coding_author', 'coding_provenance',
                    'preparation_and_author_provenance') if k in manual},
                configured_model=data.get('model'),
                model_conversation_id=data.get('model_conversation_id'),
                provenance=provenance or {},
                previous_development_history=data.get('reference_run'),
                current_run_artifacts={name: str(run / name) for name in
                    ('api-command-results.json', 'episode-trace.json', 'policy-events.json',
                     'postpark-evaluation-result.json', 'review-through-parking',
                     'video-verification.json') if (run / name).exists()})
            perception = []
            for path in sorted(run.glob('**/runpod-sam3/*/request.json')):
                request = load(path)
                response_path = path.with_name('response.json')
                response = load(response_path) if response_path.exists() else {}
                perception.append({'request': str(path), 'request_sha256': digest(path),
                    'response': str(response_path),
                    'backend': 'runpod_sam3', 'model': request.get('model'),
                    'queries': request.get('queries'), 'query_encoding': response.get('query_encoding'),
                    'image_sha256': request.get('image_sha256'),
                    'worker_id': request.get('timing', {}).get('worker_id')})
            # The chip ancestor used Astra contours, not SAM3. Retain that
            # backend's saved proposal provenance without importing pixel masks.
            for outcome in (data.get('result') or {}, post):
                observations = outcome.get('placement_evidence', {}).get('evidence', {}).get('perception', [])
                for observation in observations:
                    proposal = observation.get('proposal', {})
                    if proposal.get('segmentation_backend') == 'astra_contour_proposals':
                        perception.append({'backend': 'astra_contour_proposals',
                            'model': proposal.get('model'), 'query_name': observation.get('name'),
                            'image_sha256': proposal.get('image_sha256'),
                            'proposal_explanation': proposal.get('explanation'),
                            'queries': [p['queries'] for p in packet['programs']],
                            'query_encoding': None})
            packet['perception'] = perception
            packet['worker_revision'] = manual.get('worker_revision', (provenance or {}).get('worker_revision'))
            return self._stage(packet)

    def review_pending(self, coordinator, *, current_task=None):
        """Agent handoff before later-task generation, without human CLI steps.

        The coordinator sees findings and exact saved code, selects snippets or
        explains a no-op, then the original recorder commits and verifies it.
        Generation of the next task never races a library promotion.
        """
        with self._locked():
            packets = [load(path) for path in sorted(self.campaign.glob('*/findings.json'))]
        records = []
        for packet in packets:
            task = packet['task_id']
            if packet['task'] == current_task:
                continue
            if not packet.get('review_ready', True):
                continue
            if self.recorder.completed_record(self.root, self.suite, task):
                records.append(self.verify(task))
                continue
            if packet['evidence_role'] == 'held_out':
                records.append(self.promote(task, [], reason='Held-out outcomes retained separately; not used for skill edits.'))
                continue
            if not packet['programs']:
                try:
                    records.append(self.promote(task, [],
                        reason='No generated source exists in this attempt; no code recipe can be extracted.',
                        expected_packet=packet))
                except FindingPacketChanged:
                    pass
                continue
            sources = {}
            for program in packet['programs']:
                sources[program['path']] = self._program_bytes(program).decode('utf-8')
            feedback = None
            for revision in range(2):
                proposal = coordinator(packet=packet, sources=sources, feedback=feedback)
                try:
                    record = self.promote(task, proposal['findings'], reason=proposal['reason'],
                        expected_packet=packet)
                    records.append(record)
                    break
                except FindingPacketChanged:
                    # Leave the newer packet pending. A later worker reviews
                    # its actual outcome; old model output cannot promote it.
                    break
                except (ValueError, KeyError, TypeError) as exc:
                    feedback = str(exc)
            else:
                raise RuntimeError('ASPIRE skill coordinator could not complete promotion: ' + str(feedback))
        return records

    def require_reviewed(self, current_task):
        """Upstream's promotion gate applies before dispatching a later task.

        Same-task repair/preview-to-execution continues with its saved feedback.
        This gate does not change motion, feedback, cancellation or Home checks.
        """
        with self._locked():
            self._check_current()
            for path in sorted(self.campaign.glob('*/findings.json')):
                packet = load(path)
                if packet['task'] == current_task:
                    continue
                if not self.recorder.completed_record(self.root, self.suite, packet['task_id']):
                    raise ValueError('ASPIRE skill promotion review required before a later task: ' + str(path))
                self._verify(packet['task_id'])

    def _extract(self, finding, packet):
        if finding['topic'] not in TOPICS:
            raise ValueError('Unknown skill topic')
        self.recorder.validate_name(finding['id'], 'pattern id')
        for name in ('title', 'trigger', 'why', 'scope'):
            if not isinstance(finding.get(name), str) or not finding[name].strip():
                raise ValueError('Pattern requires ' + name)
        if finding.get('status') not in ('observed_recipe', 'provisional', 'failure'):
            raise ValueError('Pattern requires an explicit evidence status')
        if finding['status'] == 'observed_recipe' and not packet['retained_placement_observed']:
            raise ValueError('Failed/unreviewed run cannot become an observed successful recipe')
        if not isinstance(finding.get('limits'), list) or not finding['limits']:
            raise ValueError('Pattern requires explicit validation limits')
        snippets = []
        for selection in finding['snippets']:
            source = Path(selection['source']).resolve()
            known = next((p for p in packet['programs'] if p['path'] == str(source)), None)
            if known is None:
                raise ValueError('Snippet source is not the exact recorded program')
            try:
                data = self._program_bytes(known)
            except ValueError:
                raise ValueError('Snippet source is not the exact recorded program') from None
            start, end = selection['start_line'], selection['end_line']
            lines = data.decode('utf-8').splitlines()
            if not (type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)
                    and 5 <= end - start + 1 <= 20):
                raise ValueError('Extract a real 5–20 line working snippet')
            original = textwrap.dedent('\n'.join(lines[start-1:end]))
            code = original
            for old, new in selection.get('generalize', {}).items():
                if old not in code or not old or not isinstance(new, str):
                    raise ValueError('Generalization must replace actual extracted text')
                code = code.replace(old, new)
            snippets.append({'code': code, 'original_code': original, 'source': str(source),
                'source_sha256': known['sha256'], 'start_line': start, 'end_line': end,
                'generalization': selection.get('generalize', {})})
        if not snippets:
            raise ValueError('A recipe must contain working source code')
        return {**finding, 'snippets': snippets}

    def promote(self, task, findings, *, reason=None, expected_packet=None):
        with self._locked():
            completed = self.recorder.completed_record(self.root, self.suite, task)
            if completed:
                return self._verify(task)
            self._check_current()
            packet = load(self.campaign / task / 'findings.json')
            if expected_packet is not None and packet != expected_packet:
                raise FindingPacketChanged('Finding packet changed during coordinator review')
            if findings and packet['evidence_role'] != 'development':
                raise ValueError('Held-out evidence must stay separate from skill edits')
            extracted = [self._extract(finding, packet) for finding in findings]
            if not extracted and not reason:
                raise ValueError('No-op promotion requires a concise reason')
            index = load(self.skills / 'patterns.json')
            # Validate/merge before taking the upstream snapshot, so rejected
            # proposals do not leave an unfinished promotion or partial edits.
            for finding in extracted:
                item = next((p for p in index['patterns'] if p['id'] == finding['id']), None)
                if item is None:
                    item = {**finding, 'snippets': [], 'evidence': []}
                    index['patterns'].append(item)
                elif item['topic'] != finding['topic']:
                    raise ValueError('An existing pattern cannot change topic')
                for key in ('trigger', 'why', 'scope'):
                    if finding[key] not in item[key]:
                        item[key] += '\n' + finding[key]
                item['limits'] = sorted(set(item['limits'] + finding['limits']))
                item['keywords'] = sorted(set(item.get('keywords', []) + finding.get('keywords', [])))
                # Failure/provisional evidence never upgrades a supported recipe
                # or hides its caveat; each contribution keeps its own status.
                for snippet in finding['snippets']:
                    if not any(self._code_key(s['code']) == self._code_key(snippet['code']) for s in item['snippets']):
                        item['snippets'].append(snippet)
                unique = []
                for snippet in item['snippets']:
                    if not any(self._code_key(s['code']) == self._code_key(snippet['code']) for s in unique):
                        unique.append(snippet)
                item['snippets'] = unique
                if not any(e['task_id'] == task for e in item['evidence']):
                    item['evidence'].append({**packet, 'finding_status': finding['status'],
                        'finding_scope': finding['scope']})
                item['statuses'] = sorted({e['finding_status'] for e in item['evidence']})
            self.recorder.begin_promotion(self.root, suite=self.suite, task=task)
            if extracted:
                self.recorder.write_json_atomic(self.skills / 'patterns.json', index)
                self._render(index)
            record = self.recorder.finish_promotion(self.root, suite=self.suite, task=task,
                reason=reason or ('No new generalizable variation; existing recipes already cover these findings.'
                    if extracted else None))
            directory = self.recorder.completed_record(self.root, self.suite, task)[0]
            after = directory / 'after'
            for relative in record['skill_sha256_after']:
                destination = after / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.root / relative, destination)
            self.recorder.write_json_atomic(directory / 'audit.json', {
                'recorder_sha256': RECORDER_SHA256,
                'patch_sha256': digest(self.root / record['patch_path']),
                'finding_packet_sha256': digest(self.campaign / task / 'findings.json')})
            return self._verify(task)

    @staticmethod
    def _code_key(code):
        try:
            return ast.dump(ast.parse(code), include_attributes=False)
        except SyntaxError:
            # Context fragments remain documented code; ignore comments and
            # whitespace when deciding whether they add a distinct variant.
            return '\n'.join(line.strip() for line in code.splitlines()
                if line.strip() and not line.lstrip().startswith('#'))

    def _render(self, index):
        for topic, description in TOPICS.items():
            text = f'# Robo-house {topic}\n\n{description}.\n\n'
            text += 'Documented recipes, not callable tools. Reuse requires fresh scene geometry '
            text += 'and full native planning. Status applies to each cited observation, not universal robustness.\n'
            for item in index['patterns']:
                if item['topic'] != topic:
                    continue
                text += f"\n## {item['title']}\n\nPattern: `{item['id']}`\n\n"
                text += '**Trigger:** ' + item['trigger'] + '\n\n'
                for snippet in item['snippets']:
                    text += '```python\n' + snippet['code'] + '\n```\n\n'
                    text += f"Extracted from [{Path(snippet['source']).name}]({snippet['source']}), "
                    text += f"lines {snippet['start_line']}–{snippet['end_line']}; SHA256 `{snippet['source_sha256']}`.\n\n"
                    if snippet['generalization']:
                        text += 'Explicit generalization: ' + json.dumps(snippet['generalization']) + '\n\n'
                text += '**Why:** ' + item['why'] + '\n\n**Scope:** ' + item['scope'] + '\n\n'
                text += '**Limits:**\n\n' + ''.join('- ' + value + '\n' for value in item['limits'])
                text += '\n**Development evidence:**\n\n'
                for evidence in item['evidence']:
                    text += f"- {evidence['task']} — {evidence['finding_status']}; {evidence['finding_scope']}. "
                    text += f"[Findings]({self.campaign / evidence['task_id'] / 'findings.json'}). "
                    text += 'Native status: ' + str(evidence.get('native_status')) + '; original post-parking checks: '
                    text += json.dumps(evidence.get('postpark_checks', {}), sort_keys=True) + '.\n'
                text += '\nEffective perception descriptions and backend/model/worker provenance:\n\n'
                text += '```json\n' + json.dumps([self._compact_evidence(e)
                    for e in item['evidence']], indent=2) + '\n```\n'
            (self.skills / (topic + '.md')).write_text(text)

    def _check_current(self):
        if self.recorder.pending_promotions(self.root, self.suite):
            raise ValueError('An ASPIRE skill promotion is unfinished; complete its review before dispatch')
        ledger = self.recorder.read_ledger(self.root, self.suite)
        if ledger:
            latest = max(ledger, key=lambda item: item['sequence'])
            self._verify(latest['task'])
            current = self.recorder.skill_hashes(self.root)
            if current != latest['skill_sha256_after']:
                raise ValueError('Current topic library differs from its last recorded promotion')

    @staticmethod
    def _compact_evidence(evidence):
        # All batch requests/encodings remain in findings.json. Deduplicate
        # identical effective query contracts before including model context.
        perception = []
        for request in evidence.get('perception', []):
            contract = {k: request.get(k) for k in ('backend', 'model', 'queries', 'query_encoding')}
            if contract not in perception:
                perception.append(contract)
        return {**{k: evidence.get(k) for k in ('task_id', 'task', 'receipt', 'review',
            'native_status', 'postpark_automatic_success', 'postpark_checks', 'review_scope',
            'finding_status', 'finding_scope', 'author_provenance', 'worker_revision')},
            'programs': [{key: p.get(key) for key in ('path', 'retained_source', 'sha256', 'queries', 'model_conversation_id')}
                for p in evidence['programs']], 'perception_contracts': perception,
            'code_revision_provenance': evidence.get('provenance', {}),
            'full_provenance': str(Path(evidence['policy_directory']).parent),
            'evidence_role': evidence['evidence_role']}

    def _verify(self, task):
        record = self.recorder.verify_promotion(self.root, suite=self.suite, task=task)
        directory = self.recorder.completed_record(self.root, self.suite, task)[0]
        audit = load(directory / 'audit.json')
        if audit['recorder_sha256'] != RECORDER_SHA256 or digest(self.root / record['patch_path']) != audit['patch_sha256']:
            raise ValueError('Promotion patch integrity failed')
        for label, folder in (('before', 'before'), ('after', 'after')):
            hashes = record['skill_sha256_' + label]
            for relative, expected in hashes.items():
                if digest(directory / folder / relative) != expected:
                    raise ValueError('Promotion snapshot integrity failed')
            if self.recorder.library_sha256(hashes) != record['library_' + label + '_sha256']:
                raise ValueError('Promotion library digest failed')
        expected_patch = self.recorder.build_patch(directory / 'after', directory,
            record['skill_sha256_before'], record['skill_sha256_after'])
        if expected_patch != (self.root / record['patch_path']).read_text():
            raise ValueError('Promotion patch differs from the preserved snapshots')
        if digest(self.campaign / task / 'findings.json') != audit['finding_packet_sha256']:
            raise ValueError('Promoted finding packet was modified')
        if digest(self.campaign / task / 'findings.md') != record['findings_sha256']:
            raise ValueError('Promoted findings were modified')
        if not any(entry == record for entry in self.recorder.read_ledger(self.root, self.suite)):
            raise ValueError('Promotion ledger record differs')
        return record

    def verify(self, task):
        with self._locked():
            return self._verify(task)

    def retrieve(self, task, limit=6, *, context=''):
        if not (self.skills / 'patterns.json').exists():
            return {'entries': [], 'aspire_commit': ASPIRE_COMMIT}
        with self._locked():
            self._check_current()
            index = load(self.skills / 'patterns.json')
            verified=set()
            for item in index['patterns']:
                for evidence in item['evidence']:
                    task_id=evidence['task_id']
                    if task_id not in verified:
                        self._verify(task_id)
                        verified.add(task_id)
            terms = set(re.findall(r'[a-z0-9]+', (task+' '+context).lower()))
            ranked = []
            for item in index['patterns']:
                words = set(re.findall(r'[a-z0-9]+', ' '.join([item['topic'], item['title'],
                    item['trigger'], ' '.join(item.get('keywords', []))]).lower()))
                rank = len(terms & words)
                if rank:
                    ranked.append((rank, item))
            ordered = sorted(ranked, key=lambda row: (-row[0], row[1]['id']))
            # Perception context must not consume every slot and hide grasp or
            # transport knowledge. First cover each relevant topic, then fill
            # remaining slots by relevance. Failure caveats are also retained.
            selected, seen_topics = [], set()
            for row in ordered:
                if row[1]['topic'] not in seen_topics and len(selected) < limit:
                    selected.append(row)
                    seen_topics.add(row[1]['topic'])
            for row in ordered:
                if row not in selected and len(selected) < limit:
                    selected.append(row)
            return {'aspire_commit': ASPIRE_COMMIT,
                'retrieval_context': context,
                'library_sha256': self.recorder.library_sha256(self.recorder.skill_hashes(self.root)),
                'semantics': 'Documented recipes with source snippets; not callable bindings. '
                    'Previous development evidence is separate from this run. Provisional/failure findings '
                    'are caveats, not validated repairs. Fresh geometry and native full planning are required.',
                'entries': [{**item, 'evidence': [self._compact_evidence(e)
                    for e in item['evidence']]} for _, item in selected],
                'failure_findings': [{**item, 'evidence': [self._compact_evidence(e)
                    for e in item['evidence']]} for _, item in ranked
                    if any(status != 'observed_recipe' for status in item['statuses'])]}


def configured_skill_library(config):
    learning = config.get('skill_learning')
    if not learning or not learning.get('enabled', False) or config.get('coding_baseline') == 'upstream':
        return None
    return TopicSkillLibrary(learning['root'], config['aspire'], learning.get('suite', 'robohouse'))
