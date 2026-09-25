"""Human-readable run provenance, captured from the loaded runner at startup."""
from pathlib import Path
import difflib
import subprocess


def build_history():
    try:
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(['git', '-C', str(root), 'log', '-30', '--format=%H%x09%s'], capture_output=True, text=True, timeout=2, check=True)
        return [dict(revision=line.split('\t', 1)[0], description=line.split('\t', 1)[1])
                for line in result.stdout.splitlines() if '\t' in line]
    except (OSError, subprocess.SubprocessError):
        return []


BUILD_HISTORY = build_history()


def snapshot(provider, prompt, duration):
    config = provider.public_config()
    return {'model': config.get('model'), 'provider': config.get('provider'),
            'reasoning': config.get('reasoning_effort'),
            'trajectory_speed': getattr(getattr(provider, '_geometry', None), 'speed_multiplier', None),
            'task_prompt': prompt, 'system_prompt': getattr(provider, '_system_prompt', None),
            'duration_s': duration, 'code_revision': BUILD_HISTORY[0]['revision'] if BUILD_HISTORY else None,
            'code_history': BUILD_HISTORY}


def changes(previous, current):
    if not current:
        return ['Change notes were not recorded for this older run.']
    if not previous:
        return ['First recorded configuration; no earlier configuration available to compare.']
    notes = []
    for key, label in [('model', 'Model'), ('provider', 'Provider'), ('reasoning', 'Reasoning effort'),
                       ('trajectory_speed', 'Trajectory speed'), ('duration_s', 'Run time limit')]:
        old, new = previous.get(key), current.get(key)
        if old != new:
            notes.append(f'{label}: {old if old is not None else "not recorded"} → {new if new is not None else "not recorded"}.')
    for key, label in [('task_prompt', 'Task prompt'), ('system_prompt', 'Model instructions')]:
        old, new = previous.get(key), current.get(key)
        if old == new:
            continue
        if old is None or new is None:
            notes.append(f'{label}: previous or new text was not recorded; exact comparison unavailable.')
            continue
        for tag, i, j, a, b in difflib.SequenceMatcher(None, old.splitlines(), new.splitlines()).get_opcodes():
            if tag != 'equal':
                before = '\n'.join(old.splitlines()[i:j])
                after = '\n'.join(new.splitlines()[a:b])
                notes.append(f'{label} changed from “{before}” to “{after}”.')
    old, new = previous.get('code_revision'), current.get('code_revision')
    if old != new:
        history = current.get('code_history') or []
        revisions = [item['revision'] for item in history]
        if old in revisions:
            notes.extend('Code: ' + item['description'] + '.' for item in reversed(history[:revisions.index(old)]))
        else:
            notes.append('Runner code version changed; the intervening change descriptions are unavailable.')
    return notes or ['No recorded prompt, model, speed, time-limit or runner-code changes.']
