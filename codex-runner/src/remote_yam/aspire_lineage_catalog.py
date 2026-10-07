"""Read-only local evidence catalog; never starts a policy or robot session."""
import base64
from datetime import datetime, timezone
import io
import json
from pathlib import Path

from .aspire_lineage import sha256, version, record_lineage, retrieval_catalog
from .aspire_repair_trace import recorded_trace
from .aspire_recovery_flow import task_resolution


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return {}


class LineageCatalog:
    def __init__(self, config, *, inline_images=False):
        self.config = config
        self.inline_images = inline_images
        self.artifacts = {}
        self.skill_root = Path(config['skill_directory']).resolve()
        self.output_root = self.skill_root.parent.parent
        manifest = config.get('executable_skills', {}).get('manifest')
        self.manifest_path = Path(manifest).resolve() if manifest else None
        self.allowed_roots = [self.output_root]
        if self.manifest_path:
            self.allowed_roots.append(self.manifest_path.parent)

    def artifact(self, path):
        if not path:
            return None
        path = Path(path).resolve()
        if not any(path.is_relative_to(root) for root in self.allowed_roots):
            return None
        if path.suffix not in ('.py', '.json', '.jsonl', '.diff', '.png', '.jpg', '.jpeg', '.mp4', '.md') or not path.is_file():
            return None
        key = sha256(str(path))
        self.artifacts[key] = path
        return {'name': path.name, 'source': str(path), 'url': '/api/aspire-lineage/artifacts/' + key}

    def image(self, path):
        item = self.artifact(path)
        if item and self.inline_images:
            from PIL import Image
            with Image.open(path) as image:
                image.thumbnail((480, 320))
                stream = io.BytesIO()
                image.convert('RGB').save(stream, format='JPEG', quality=75)
                item['preview'] = 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode()
        return item

    def episode(self, packet):
        directory = Path(packet.get('policy_directory') or '.')
        if directory.name=='policy':directory=directory.parent
        receipt = load(packet.get('receipt'))
        review = load(packet.get('review'))
        episode = load(directory / 'episode.json')
        loop = load(packet.get('coding_loop') or directory / 'policy/coding-loop.json')
        result = receipt.get('result') or {}
        postpark = receipt.get('postpark_evaluation') or {}
        record = {'id': packet['task_id'], 'task': packet.get('task'),
            'resolution':task_resolution(directory,receipt),
            'episode_id': episode.get('episode_id'), 'evidence_role': packet.get('evidence_role'),
            'recorded_at':review.get('recorded_at') or (Path(packet['receipt']).stat().st_mtime if packet.get('receipt') and Path(packet['receipt']).is_file() else 0),
            'visual_review_success':review.get('success'),
            'review_basis':review.get('success_basis'),
            'authorship_note':review.get('coding_author') or (packet.get('author_provenance') or {}).get('coding_author'),
            'task_updates':loop.get('task_updates', []),
            'scope': review.get('validation_scope') or packet.get('review_scope'),
            'native_status': packet.get('native_status') or result.get('status'),
            'review_status': review.get('status') or packet.get('review_status'),
            'after_parking_success': packet.get('postpark_automatic_success', postpark.get('success')),
            'postpark_checks': review.get('postpark_checks') or packet.get('postpark_checks')
                or (postpark.get('placement_evidence') or {}).get('evidence', {}).get('checks'),
            'limits': review.get('remaining_limits', []), 'attempts': [], 'images': [], 'replays': [],
            'receipt': self.artifact(packet.get('receipt')), 'review': self.artifact(packet.get('review'))}
        for path in review.get('images', [])[:3]:
            image = self.image(path)
            if image:
                image['phase']='After-parking capture' if 'postpark' in path or 'parked' in Path(path).name else 'Recorded review; capture phase unrecorded'
                record['images'].append(image)
        for path in sorted((directory / 'review-through-parking/video').glob('*.mp4')):
            item = self.artifact(path)
            if item:record['replays'].append(item)
        attempts = loop.get('attempts') or packet.get('native_findings', [])
        for attempt in attempts:
            folder = Path(attempt.get('directory') or directory / ('policy/attempt-%02d' % attempt.get('attempt', 1)))
            reply = load(folder / 'coding-response.json')
            trace = load(folder / 'lineage.json') or attempt.get('lineage')
            provenance = attempt.get('executable_reuse')
            if not trace and provenance and reply.get('source'):
                retrieved = retrieval_catalog(provenance.get('retrieved_topic_skills', {}))
                trace = record_lineage(reply, retrieved, provenance)
            if not trace:
                trace = {'retrieved': [], 'used': [], 'new_skills': [], 'usage_recorded': False}
            snapshot = load(directory / ('policy/retrieval-%02d.json' % attempt.get('attempt', 1)))
            if not snapshot and provenance:
                snapshot = retrieval_catalog(provenance.get('retrieved_topic_skills', {}))
            for use in trace.get('used', []):
                for ref in use.get('source_refs', []):
                    ref['recorded_program']=self.artifact(folder/'generated_program.py')
                    original=self.artifact(ref.get('source'))
                    matches=bool(original and sha256(Path(original['source']).read_text())==ref.get('source_sha256'))
                    ref['original_artifact']=original if matches else None
                    ref['original_source_state']='hash_verified' if matches else 'changed_or_unavailable'
            # The full immutable snapshots stay on disk. Send one copy of source
            # snippets to the browser, rather than repeating nested evidence.
            if isinstance(snapshot, list):
                snapshot = [{**{k:e.get(k) for k in ('id','version','title','scope','validation',
                    'snippets','limits','statuses','why','trigger')},
                    'episodes':[p['task_id'] for p in e.get('evidence', []) if p.get('task_id')]}
                    for e in snapshot]
            if not trace.get('authorship'):
                author=record.get('authorship_note') or ''
                requests=(review.get('coding_provenance') or {}).get('task_coding_model_requests')
                trace['authorship']={'generated_by_codex':'Codex' in author,
                    'mode':'reuse' if provenance or attempt.get('reused_agent_program') else 'generation',
                    'basis':'recorded_author_review' if author else 'unrecorded',
                    'note':author,'task_coding_model_requests':requests}
            trace = {**trace, 'executable': {'core_revision': (trace.get('executable') or {}).get('core_revision')}}
            record['attempts'].append({'attempt': attempt.get('attempt'),
                'id':'aspire-'+str(attempt.get('attempt')), 'policy':'aspire',
                'plan': self.result(attempt.get('plan', {})),
                'execution': self.result(attempt.get('execution') or attempt.get('failure') or {}),
                'candidate_evidence':self.artifact(folder/'execute/candidate-plans.jsonl') or self.artifact(folder/'plan/candidate-plans.jsonl'),
                'source': self.artifact(folder / 'generated_program.py'),
                'source_sha256': sha256(reply['source']) if reply.get('source') else None,
                'lineage': trace, 'retrieval_snapshot': snapshot if isinstance(snapshot, list) else [],
                'code_revision_reason':attempt.get('code_revision_reason'),
                'summary': reply.get('summary'), 'code': reply.get('source', '')})
        for recovery in loop.get('recovery_attempts', []):
            policy=recovery.get('policy','astra')
            label='Local Codex repair' if policy=='codex_local' else 'Astra recovery'
            summary=recovery.get('diagnosis') or (label+'; saved ASPIRE source unchanged. Model completion and physical verification are separate.')
            record['attempts'].append(dict(attempt=len(record['attempts'])+1,
                id=recovery['id'],policy=policy,parent_attempt_id=recovery.get('parent_attempt_id'),
                plan={},execution=dict(status=recovery['status'],reason=recovery.get('reason') or
                    (recovery.get('outcome') or {}).get('summary') or (recovery.get('outcome') or {}).get('reason') or recovery.get('diagnosis'),
                    physical_success=None,completed_packets=recovery.get('completed_packets')),
                source=None,source_sha256=None,code='',retrieval_snapshot=[],
                summary=summary,diff=self.artifact(Path(recovery['repair_directory'])/'repair.diff') if recovery.get('repair_directory') else None,
                mode=recovery.get('mode'),status=recovery['status'],trace=recorded_trace(recovery,directory),
                lineage=dict(usage_recorded=False,used=[],retrieved=[],new_skills=[]),
                code_revision_reason=recovery.get('previous_result')))
            if recovery.get('mode') in ('offline_code_repair','offline_rerun_diagnosis') and recovery.get('coding_loop'):
                child=self.episode(dict(task_id=recovery['id'],task=record['task'],
                    policy_directory=recovery['repair_directory'],coding_loop=recovery['coding_loop']))
                for revision in child['attempts']:
                    revision.update(attempt=len(record['attempts'])+1,policy=policy,
                        id=recovery['id']+'-code-'+str(revision['attempt']),parent_attempt_id=recovery['id'])
                    if recovery.get('mode')=='offline_rerun_diagnosis':revision['mode']='offline_rerun_diagnosis'
                    record['attempts'].append(revision)
        recovery=load(directory/'recovery-state.json')
        record['recovery_run_id']=directory.name if self.config.get('automatic_recovery') is True and (record['resolution'] or {}).get('recovery_available') else None
        for retry_record in recovery.get('attempts',[]):
            retry=Path(retry_record['directory']);identity=retry_record['id']
            if retry.resolve()==directory.resolve() or not retry.resolve().is_relative_to(self.output_root):continue
            child=self.episode(dict(task_id=record['id']+'-'+identity,task=record['task'],policy_directory=str(retry),
                coding_loop=str(retry/'coding-loop.json'),receipt=str(retry/'task-completion.json'),review=str(retry/'postpark-review.json')))
            record['attempts'].append(dict(attempt=len(record['attempts'])+1,id=identity,policy='aspire_retry',
                parent_attempt_id=retry_record['parent_attempt_id'],plan=retry_record.get('preflight_result') or {},execution=dict(status=retry_record.get('native_status') or retry_record['status']),
                source=self.artifact(retry/'recovery-preflight/repair.py'),source_sha256=retry_record.get('source_sha256'),
                candidate_evidence=self.artifact(retry/'recovery-preflight/plan/candidate-plans.jsonl'),
                preflight_log=self.artifact(retry/'recovery-preflight/plan-process.log'),
                preflight_process=self.artifact(retry/'recovery-preflight/plan-process.json'),
                code='',retrieval_snapshot=[],lineage={},
                summary=recovery.get('detail'),resolution=recovery))
            for attempt in child['attempts']:
                parent=attempt.get('parent_attempt_id')
                attempt.update(attempt=len(record['attempts'])+1,id=identity+'-'+str(attempt.get('id')),
                    parent_attempt_id=identity+'-'+str(parent) if parent else identity)
                record['attempts'].append(attempt)
            for image in child['images']:record['images'].append(dict(image,phase=identity+' · '+image['phase']))
        return record

    def saved_program(self, path):
        """A persisted program is visible even before an episode review exists."""
        entry=load(path)
        source=path.with_suffix('.py')
        if not source.is_file() or not entry.get('source_sha256'):
            return None
        code=source.read_text()
        if sha256(code)!=entry['source_sha256']:
            return None
        trace=entry.get('lineage') or {'usage_recorded':False,'used':[],'retrieved':[],'new_skills':[]}
        if not trace.get('authorship'):
            trace['authorship']={'generated_by_codex':entry.get('code_generation_kind')=='codex_subscription_transport',
                'mode':'generation','basis':entry.get('code_generation_kind','unrecorded'),'model':entry.get('model')}
        trace['program']={'source':str(source),'source_sha256':entry['source_sha256'],'code':code}
        return dict(id='saved-program-'+entry['source_sha256'],task=entry.get('task','Saved program'),
            episode_id=None,evidence_role='saved_program',recorded_at=path.stat().st_mtime,
            scope='Saved program validation: '+str(entry.get('validation','unknown'))+'. No after-parking review is attached to this entry.',
            native_status=entry.get('validation'),review_status=None,after_parking_success=None,
            visual_review_success=None,postpark_checks=None,task_updates=[],limits=[],images=[],replays=[],
            receipt=self.artifact(path),review=None,
            attempts=[dict(attempt=1,plan={},execution={},source=self.artifact(source),
                source_sha256=entry['source_sha256'],lineage=trace,retrieval_snapshot=[],summary=entry.get('summary'),code=code)])

    @staticmethod
    def result(value):
        return {key: value.get(key) for key in ('status', 'success', 'planning_success',
            'physical_motion_calls', 'reason', 'failing_stage','candidate_search') if key in value}

    def build(self):
        from .aspire_codex_policy import AspireCodexPolicy
        from .aspire_code_runtime import run_generated
        import inspect
        learning = self.config.get('skill_learning', {})
        index = load(Path(learning.get('root') or self.skill_root) / '.claude/libero/skills/patterns.json')
        patterns, packets = [], {}
        for pattern in index.get('patterns', []):
            refs = []
            for evidence in pattern.get('evidence', []):
                packets[evidence['task_id']] = evidence
                refs.append(evidence['task_id'])
            patterns.append({**{key: pattern.get(key) for key in ('id', 'title', 'topic', 'trigger',
                'why', 'scope', 'limits', 'statuses', 'status')}, 'version': version(pattern),
                'snippets': pattern.get('snippets', []), 'episodes': refs})
        manifest = load(self.manifest_path) if self.manifest_path else {}
        # Discover recorded failures/revisions as well as successful promotions.
        receipts = sorted(self.output_root.glob('aspire-*/*/receipt.json'),
            key=lambda p: p.stat().st_mtime, reverse=True)[:24]
        for path in receipts:
            receipt = load(path)
            directory = path.parent
            if not receipt.get('task'):
                continue
            existing = next((p for p in packets.values() if p.get('receipt') == str(path)), None)
            if not existing:
                key = directory.name + '-' + sha256(str(directory))[:12]
                packets[key] = {'task_id': key, 'task': receipt['task'], 'receipt': str(path),
                    'review': str(directory/'review.json'), 'policy_directory': str(directory/'policy'),
                    'evidence_role': 'development', 'review_scope': 'Recorded run; physical review may be absent'}
        completed=sorted((self.skill_root.parent/'runs').glob('aspire-*/task-completion.json'),
            key=lambda p:p.stat().st_mtime,reverse=True)[:24]
        for path in completed:
            completion=load(path);directory=path.parent
            if not completion.get('task') or any(p.get('receipt')==str(path) for p in packets.values()):continue
            packets[directory.name]=dict(task_id=directory.name,task=completion['task'],
                receipt=str(path),review=str(directory/'postpark-review.json'),
                policy_directory=str(directory),coding_loop=str(directory/'coding-loop.json'),
                native_status=completion.get('native_status'),review_status=completion.get('status'),
                postpark_automatic_success=completion.get('success'),evidence_role='recorded_run',
                review_scope='Recorded task; an offline repair does not change the physical outcome.')
        episodes=[self.episode(packet) for packet in packets.values()]
        recorded_hashes={a.get('source_sha256') for e in episodes for a in e['attempts']}
        for path in self.skill_root.glob('*.json'):
            entry=self.saved_program(path)
            if entry and entry['attempts'][0]['source_sha256'] not in recorded_hashes:
                episodes.append(entry)
        episodes.sort(key=lambda e:e['recorded_at'],reverse=True)
        if manifest:
            path = (self.manifest_path.parent / manifest.get('source', '')).resolve()
            artifact = self.artifact(path)
            current = path.read_text() if artifact else ''
            manifest = {**manifest, 'source': str(path), 'source_artifact': artifact,
                'source_code': current, 'manifest_sha256': sha256(self.manifest_path.read_text()),
                'source_integrity': sha256(current) == manifest.get('source_sha256')}
        from .aspire_executable_skills import configured_executable_skills
        library=configured_executable_skills(self.config)
        saved_tasks=[]
        if library and library.repairs:
            for path in library.repairs.glob('*.json'):
                task=load(path).get('task')
                selected=library.select(task) if isinstance(task,str) else None
                if selected and selected['provenance'].get('source_binding')=='exact_task_program':
                    saved_tasks.append(dict(task=task,source_sha256=selected['provenance']['bound_source_sha256']))
        return {'schema_version': 1, 'robot_id': self.config['robot_id'],
            'execution_environment':self.config.get('execution_environment','web'),'saved_program_tasks':saved_tasks,
            'runtime_capabilities':dict(linked_astra_recovery=hasattr(AspireCodexPolicy,'_start_recovery'),
                offline_outcome_repair=hasattr(AspireCodexPolicy,'_repair_outcome'),
                stage_progress=hasattr(AspireCodexPolicy,'_stage_status'),
                public_repair_trace=hasattr(AspireCodexPolicy,'_forward_recovery_event'),
                candidate_plan_limit=inspect.signature(run_generated).parameters['max_candidate_plans'].default,
                candidate_search_s=inspect.signature(run_generated).parameters['candidate_search_s'].default),
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'semantics': 'Episode evidence is scoped to its recorded task. Planning is not physical success. '
                'Retrieved context is not proof of use. Exact snippet matches confirm text, not semantic dependence.',
            'executable': manifest, 'recipes': patterns, 'episodes': episodes}
