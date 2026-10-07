"""Small public ASPIRE status snapshots for the existing text-sharing API.

This is activity and recorded evidence, not model reasoning. Source code,
filesystem references, image data, wire payloads and arbitrary tool details
are deliberately outside the projection.
"""
import json
import math
import re


PREFIX = 'ASPIRE_TASK_PROGRESS_V1\n'
MODEL_PREFIX = 'PUBLIC_MODEL_EVENT_V1\n'


def public_text(value):
    """Keep diagnostic meaning without publishing host paths or credentials."""
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[redacted]', str(value))
    text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*', 'Bearer [redacted]', text)
    return re.sub(r'''(?<![\w])(?:/(?:Users|home|opt|var|etc|private|tmp|root)(?:/[^\s"'<>]*)?|[A-Za-z]:[\\/][^\s"'<>]*)''',
                  '[private path]', text)


def progress_message(progress, text):
    if not isinstance(progress, dict):
        return None

    def fields(value, names):
        if not isinstance(value, dict):
            return {}
        out = {}
        for key in names.split():
            item = value.get(key)
            if isinstance(item, str):
                out[key] = public_text(text(item))[:500]
            elif item is None or isinstance(item, bool):
                if key in value:
                    out[key] = item
            elif isinstance(item, (int, float)) and math.isfinite(item):
                out[key] = item
        return out

    def rows(value, names, limit):
        return [fields(row, names) for row in value[-limit:] if isinstance(row, dict)] if isinstance(value, list) else []

    result = fields(progress, 'task')
    if isinstance(progress.get('task'), str):
        result['task'] = public_text(text(progress['task']))[:4000]
    result['updates'] = rows(progress.get('updates'), 'timestamp happened changed next_action', 12)
    for key, names in {
        'resolution': 'state title detail next_action retry_limit retry_count recovery_available',
        'outcome': 'status success reason',
        'stage': 'stage title detail started_at event_at elapsed_s active candidates rejected_candidates last_failure error',
        'vision': 'model state started_at ready_at ended_at',
    }.items():
        if isinstance(progress.get(key), dict):
            result[key] = fields(progress[key], names)
    lineage = progress.get('lineage')
    if isinstance(lineage, dict):
        result['lineage'] = fields(lineage, 'pending stage usage_recorded program_sha256')
        result['lineage']['authorship'] = fields(lineage.get('authorship'),
            'generated_by_codex mode model task_coding_model_requests')
        for key, names in {
            'used': 'id version usage changed verification validation_scope',
            'retrieved': 'id version kind',
            'new_skills': 'id title behavior reason creation code_sha256 start_line end_line planning_success physical_success',
        }.items():
            result['lineage'][key] = rows(lineage.get(key), names, 6)
    attempts = progress.get('attempts')
    if isinstance(attempts, list):
        result['attempts'] = []
        for attempt in attempts[-8:]:
            if not isinstance(attempt, dict):
                continue
            row = fields(attempt, 'id policy attempt mode status parent_attempt_id source_sha256 reason diagnosis started_at ended_at')
            row['result'] = fields(attempt.get('result'), 'status success reason planning_success physical_motion_calls failing_stage')
            row['outcome'] = fields(attempt.get('outcome'), 'status success summary reason')
            result['attempts'].append(row)
    # Remove oldest activity first; preserve terminal evidence and provenance.
    # Never truncate JSON into an invalid message or exceed the API contract.
    while True:
        content = PREFIX + json.dumps({'task_progress': result}, ensure_ascii=False, separators=(',', ':'))
        if len(content) <= 8000:
            return content
        if result['updates']:
            result['updates'].pop(0)
        elif result.get('attempts'):
            result['attempts'].pop(0)
        elif result.get('lineage', {}).get('retrieved'):
            result['lineage']['retrieved'].pop(0)
        elif result.get('lineage', {}).get('new_skills'):
            result['lineage']['new_skills'].pop(0)
        elif result.get('lineage', {}).get('used'):
            result['lineage']['used'].pop(0)
        else:
            return None
