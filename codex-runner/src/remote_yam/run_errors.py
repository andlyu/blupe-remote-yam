"""Public run errors: useful categories without private exception payloads."""
from remote_yam.past_runs import public_robot_error, run_timed_out, public_runner_diagnostic


def public_run_error(state):
    specific = public_robot_error(state)
    if specific:
        return specific
    if run_timed_out(state):
        return 'Run reached time limit'
    diagnostic = public_runner_diagnostic(state)
    if diagnostic:
        return diagnostic
    error = state.get('error')
    safety = state.get('safety_error') or {}
    if error or safety:
        text = str(error or safety.get('code') or '').lower()
        if 'runtime limit' in text or 'session_timeout' in text or 'policy_runtime_timeout' in text:
            return 'Run reached time limit'
        if '401' in text or 'authentication' in text or 'invalid_api_key' in text:
            return 'Model provider rejected the API credentials.'
        if '429' in text or 'rate_limit' in text or 'quota' in text:
            return 'Model provider rate limit or quota exceeded.'
        if 'camera' in text:
            return 'Run could not obtain a camera frame.'
        if safety.get('category') == 'hardware_safety':
            return 'Robot safety check failed; motion was stopped.'
        if 'timeout' in text or 'timed out' in text:
            return 'Run request timed out.'
        if 'connection' in text or 'urlerror' in text or state.get('status') == 'disconnected':
            return 'Run lost its connection to the model provider or robot server.'
        return 'The run encountered a model or runner error.'
    if state.get('status') in {'preparing', 'running'}:
        if state.get('server_contact_issue'):
            return 'Connection issue with the model provider or robot server; retrying.'
        retry = ((state.get('provider') or {}).get('vision') or {}).get('retry') or {}
        if retry.get('state') == 'retrying':
            return 'Camera frame unavailable; retrying.'
    return None
