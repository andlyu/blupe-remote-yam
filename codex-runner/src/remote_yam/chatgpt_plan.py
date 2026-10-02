"""SIWC Responses transport for an already-authorized account.

Registration, consent, refresh and credential storage belong to the calling app.
Protocol: https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
and preview-limitations (retrieved 2026-10-02). This is not a local Codex login.
"""
import copy
import json
import re
from urllib import error, request

ENDPOINT = 'https://api.openai.com/v1/responses'


class ChatGPTPlanError(RuntimeError):
    def __init__(self, status, body=None, request_id=None):
        self.status, self.body, self.request_id = status, body, request_id
        detail = body.get('error', {}) if isinstance(body, dict) else {}
        if isinstance(body,dict) and body.get('type') == 'error':
            detail = body
        elif isinstance(body,dict) and isinstance(body.get('response'),dict):
            detail = body['response'].get('error',{})
        self.code = detail.get('code') if isinstance(detail, dict) else None
        messages = {
            'subscription_sharing_usage_limit_exceeded': 'ChatGPT plan usage limit reached. Check ChatGPT settings → Usage.',
            'subscription_sharing_user_not_eligible': 'This ChatGPT account cannot use its plan in this app.',
            'subscription_sharing_unsupported_capability': 'This request is not supported by ChatGPT plan usage.',
        }
        message = messages.get(self.code, 'ChatGPT plan request failed. Check your connection and plan permissions.')
        code = self.code if isinstance(self.code, str) and re.fullmatch(r'[a-z0-9_]{1,100}', self.code) else 'request_failed'
        super().__init__(f'{message} (HTTP {status}, {code})')


def plan_payload(payload):
    """Keep robot tool/history identities while adapting the preview wire contract."""
    body = copy.deepcopy(payload)
    for key in ('background', 'conversation', 'max_output_tokens', 'max_tool_calls',
                'metadata', 'moderation', 'multi_agent', 'prompt', 'prompt_cache_retention',
                'safety_identifier', 'temperature', 'top_logprobs', 'top_p', 'truncation',
                'user', 'previous_response_id', 'service_tier'):
        body.pop(key, None)
    body.update(store=False, stream=True)
    if not isinstance(body.get('input'), list):
        body['input'] = [{'role': 'user', 'content': body.get('input', '')}]
    for item in body['input']:
        if isinstance(item, dict) and item.get('role') == 'system':
            item['role'] = 'developer'
    tools = body.get('tools', [])
    if tools:
        if any(t.get('type') not in {'function', 'custom'} for t in tools):
            raise ValueError('ChatGPT robot policies support only function/custom tools')
        body['tools'] = [{'type': 'namespace', 'name': 'robot',
                         'description': 'Validated robot control tools.', 'tools': tools}]
    return body


def stream_response(response, cancelled):
    """A partial tool call must never reach the robot; require the terminal event."""
    data, total = [], 0
    for line in response:
        if cancelled():
            raise RuntimeError('Model request cancelled')
        total += len(line)
        if total > 16 * 1024 * 1024 or len(line) > 4 * 1024 * 1024:
            raise RuntimeError('ChatGPT response exceeded its size limit')
        line = line.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            data.append(line[5:].lstrip())
        if line or not data:
            continue
        raw = '\n'.join(data)
        data = []
        if raw == '[DONE]':
            break
        event = json.loads(raw)
        if event.get('type') in {'error', 'response.failed', 'response.incomplete'}:
            raise ChatGPTPlanError(getattr(response,'status',200), event, response.headers.get('x-request-id'))
        if event.get('type') == 'response.completed':
            result = event.get('response')
            if not isinstance(result, dict) or result.get('status') != 'completed':
                raise RuntimeError('ChatGPT response did not complete')
            return result
    raise RuntimeError('ChatGPT stream ended before response.completed; no action was accepted')


def use_chatgpt_plan(provider, access_token, *, on_rejected=None, urlopen=None):
    """Bind an app-owned renewable grant before the provider joins the queue."""
    opener = urlopen or getattr(provider, '_urlopen', request.urlopen)
    provider._api_key = ''
    def post(payload):
        if provider.cancelled():
            raise RuntimeError('Model request cancelled')
        token = access_token()
        req = request.Request(ENDPOINT, data=json.dumps(plan_payload(payload)).encode(),
                              headers={'Authorization': 'Bearer ' + token,
                                       'Content-Type': 'application/json', 'Accept': 'text/event-stream'}, method='POST')
        try:
            with opener(req, timeout=45) as response:
                return stream_response(response, provider.cancelled)
        except error.HTTPError as exc:
            raw = exc.read(65536)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = {'detail': raw.decode('utf-8', errors='replace')}
            problem = ChatGPTPlanError(exc.code, body, exc.headers.get('x-request-id'))
            if on_rejected and problem.code == 'subscription_sharing_invalid_user':
                on_rejected()
            raise problem from None
        except ChatGPTPlanError as exc:
            if on_rejected and exc.code == 'subscription_sharing_invalid_user':
                on_rejected()
            raise
    provider._post_json = post
    original_config = provider.public_config
    def config():
        return {**original_config(), 'funding': 'chatgpt_plan', 'api_key_configured': False}
    provider.public_config = config
    return provider
