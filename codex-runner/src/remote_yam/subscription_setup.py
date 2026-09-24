"""Local setup guidance and live subscription checks, without robot transport."""
import time


def subscription_status(provider):
    if provider == 'codex':
        from .codex_policy import codex_status
        status = codex_status()
        label = 'Astra'
        prompt = 'Set up Astra for this playground using README.md.'
    elif provider == 'claude':
        from .claude_policy import claude_status
        status = claude_status()
        label = 'Opus (Claude)'
        prompt = 'Follow docs/claude/README.md to use my Claude subscription to control the remote YAMs.'
    else:
        raise ValueError('Unknown subscription provider')
    if status['ready']:
        status = {**status, 'message': f'{label} login found locally. Run verifies a live model request before joining the queue.'}
    return {**status, 'provider': provider, 'label': label, 'setup_prompt': prompt,
            'verified': False,
            'availability_note': 'Run and Check again send a small verification request using your subscription.'}


def probe_subscription(provider, model=''):
    """Exercise the same CLI, model and credentials as inference, in a fresh workspace.

    This adapter is never attached to a controller or Session API. There are no
    camera frames, user tasks or robot state in the text-only request, and any
    returned decision is discarded rather than executed.
    """
    if provider == 'claude':
        from .claude_policy import ClaudeAdapter, DEFAULT_MODEL
        adapter = ClaudeAdapter(model or DEFAULT_MODEL, timeout_s=45)
    elif provider == 'codex':
        from .codex_policy import CodexAdapter, DEFAULT_MODEL
        adapter = CodexAdapter(model or DEFAULT_MODEL, timeout_s=45)
    else:
        raise ValueError('Unknown subscription provider')
    try:
        adapter._expected_camera_count = 0
        result = adapter._post_json({'input': [{'role': 'user', 'content': [{
            'type': 'input_text', 'text': 'Subscription connection check only, with no robot connected. '
            'No images are provided. Do not use tools. Return action done, all targets null, '
            'summary Connection verified, and empty strings for other text fields.'}]}], 'tools': []})
        output = result.get('output') or []
        if len(output) != 1 or output[0].get('name') != 'done':
            raise RuntimeError('Subscription check did not return the required completion.')
        return adapter.model
    finally:
        adapter._workspace.cleanup()


def subscription_failure(provider, error, *, setup=None):
    """Map sanitized adapter failures without exposing raw CLI or account data."""
    setup = setup or subscription_status(provider)
    text = str(error).lower()
    if any(term in text for term in ('needs sign-in', 'needs chatgpt sign-in', 'oauth', 'authentication', 'token_expired')):
        state = 'login_required'
        instruction = 'Run `claude auth login`.' if provider == 'claude' else 'Sign in again with Codex using your ChatGPT account.'
        message = f"{setup['label']} subscription sign-in expired or was rejected. {instruction}"
    elif any(term in text for term in ('usage limit', 'rate limit', 'quota')):
        state, message = 'usage_limit', f"{setup['label']} subscription usage limit reached. Wait for it to reset, then check again."
    else:
        state, message = 'verification_failed', f"Could not verify {setup['label']} with a live model request. Check the connection, model access and local setup, then try again."
    return {**setup, 'ready': False, 'verified': False, 'state': state, 'message': message}


def verify_subscription(provider, model=''):
    setup = subscription_status(provider)
    if not setup['ready']:
        return setup
    try:
        verified_model = probe_subscription(provider, model)
    except (RuntimeError, OSError) as exc:
        return subscription_failure(provider, exc, setup=setup)
    return {**setup, 'ready': True, 'verified': True, 'state': 'verified',
            'model': verified_model, 'verified_at': time.time(),
            'message': f"{setup['label']} subscription verified with a live model response. Your run can now join the queue."}
